"""Security Agent — scans PR diffs for security vulnerabilities.

Performs two levels of analysis:
1. Regex-based: Fast pattern matching for hardcoded secrets, credentials, and tokens.
2. LLM-based: Nova Pro contextual analysis for SQL injection, XSS, auth flaws,
   and business logic vulnerabilities that regex can't catch.
"""

import logging
import re
import time
from typing import Any, Optional

import boto3

from shared.bedrock_client import BedrockClient, BedrockResponse
from shared.models import (
    DiffChunk,
    Finding,
    ParsedPREvent,
    SecurityReviewEvent,
    Severity,
)

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────
# Regex patterns for hardcoded secrets
# ────────────────────────────────────────────────────

SECRET_PATTERNS: list[dict[str, Any]] = [
    {
        "name": "AWS Access Key",
        "pattern": re.compile(r"(?:AKIA|ABIA|ACCA|ASIA)[0-9A-Z]{16}"),
        "severity": Severity.CRITICAL,
        "category": "hardcoded_secret",
    },
    {
        "name": "AWS Secret Key",
        "pattern": re.compile(
            r"(?:aws_secret_access_key|aws_secret_key|secret_key)\s*[=:]\s*['\"]?"
            r"([A-Za-z0-9/+=]{40})"
        ),
        "severity": Severity.CRITICAL,
        "category": "hardcoded_secret",
    },
    {
        "name": "Generic API Key",
        "pattern": re.compile(
            r"(?:api[_-]?key|apikey|api[_-]?secret)\s*[=:]\s*['\"]([a-zA-Z0-9_\-]{20,})['\"]",
            re.IGNORECASE,
        ),
        "severity": Severity.CRITICAL,
        "category": "hardcoded_secret",
    },
    {
        "name": "Generic Password",
        "pattern": re.compile(
            r"(?:password|passwd|pwd|secret)\s*[=:]\s*['\"]([^'\"]{8,})['\"]",
            re.IGNORECASE,
        ),
        "severity": Severity.WARNING,
        "category": "hardcoded_secret",
    },
    {
        "name": "Private Key",
        "pattern": re.compile(r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----"),
        "severity": Severity.CRITICAL,
        "category": "hardcoded_secret",
    },
    {
        "name": "GitHub Token",
        "pattern": re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}"),
        "severity": Severity.CRITICAL,
        "category": "hardcoded_secret",
    },
    {
        "name": "JWT Token",
        "pattern": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+"),
        "severity": Severity.WARNING,
        "category": "hardcoded_secret",
    },
    {
        "name": "Database Connection String",
        "pattern": re.compile(
            r"(?:mysql|postgres|postgresql|mongodb|redis)://[^\s'\"]{10,}",
            re.IGNORECASE,
        ),
        "severity": Severity.CRITICAL,
        "category": "hardcoded_secret",
    },
]

# ────────────────────────────────────────────────────
# LLM Security Review Prompt
# ────────────────────────────────────────────────────

SECURITY_SYSTEM_PROMPT = """You are a senior security engineer performing a code review.
Analyze the provided code diff for security vulnerabilities.

Focus on:
1. SQL Injection — unsanitized user input in queries
2. XSS (Cross-Site Scripting) — unescaped output in templates/HTML
3. Command Injection — user input passed to shell commands
4. Path Traversal — unsanitized file path construction
5. Authentication/Authorization — missing or weak auth checks
6. Insecure Data Handling — sensitive data in logs, plaintext storage
7. SSRF — user-controlled URLs in server-side requests
8. Insecure Deserialization — pickle, yaml.load without SafeLoader

For each issue found, respond in this exact JSON format:
{
  "findings": [
    {
      "severity": "CRITICAL|WARNING|INFO",
      "category": "sql_injection|xss|command_injection|path_traversal|auth_flaw|data_exposure|ssrf|deserialization|other",
      "file": "path/to/file.py",
      "line": 42,
      "message": "Brief description of the vulnerability",
      "suggestion": "How to fix this issue"
    }
  ]
}

If no security issues are found, respond with: {"findings": []}

IMPORTANT RULES:
- Only report REAL vulnerabilities, not style issues or best practices
- Be precise with file paths and line numbers
- CRITICAL = exploitable vulnerability, WARNING = potential risk, INFO = hardening suggestion
- Keep messages and suggestions concise (1-2 sentences each)"""


def _build_chunk_prompt(chunk: DiffChunk) -> str:
    """Build the LLM prompt for a diff chunk.

    Args:
        chunk: DiffChunk containing file changes to analyze.

    Returns:
        Formatted prompt string with file diffs.
    """
    parts = [f"Review this code diff (chunk {chunk.chunk_index + 1}/{chunk.total_chunks}):\n"]

    for file in chunk.files:
        # Handle both dict and FileChange objects
        if isinstance(file, dict):
            filepath = file.get("path", "unknown")
            patch = file.get("patch", "")
            language = file.get("language", "unknown")
        else:
            filepath = file.path
            patch = file.patch
            language = file.language

        parts.append(f"### File: {filepath} (language: {language})")
        parts.append(f"```diff\n{patch}\n```\n")

    return "\n".join(parts)


class SecurityAgent:
    """Security review agent that scans diffs for vulnerabilities.

    Combines regex-based secret scanning with LLM-powered contextual
    analysis using Amazon Bedrock Nova Pro.

    Args:
        bedrock_client: BedrockClient instance for LLM calls.
        dynamodb_table: Optional DynamoDB table name for writing findings.
        region: AWS region for DynamoDB.
    """

    def __init__(
        self,
        bedrock_client: BedrockClient,
        dynamodb_table: Optional[str] = None,
        region: Optional[str] = None,
    ) -> None:
        self.bedrock = bedrock_client
        self.dynamodb_table = dynamodb_table
        self.region = region or "us-east-1"

        if dynamodb_table:
            self.dynamodb = boto3.resource("dynamodb", region_name=self.region)
            self.table = self.dynamodb.Table(dynamodb_table)
        else:
            self.table = None

    def process(self, parsed_event: ParsedPREvent) -> SecurityReviewEvent:
        """Run full security analysis on a parsed PR event.

        1. Regex scan all files for hardcoded secrets.
        2. LLM analysis of each diff chunk for deeper vulnerabilities.
        3. Write findings to DynamoDB.
        4. Return SecurityReviewEvent.

        Args:
            parsed_event: ParsedPREvent from the Parser Agent.

        Returns:
            SecurityReviewEvent with all findings.
        """
        start = time.monotonic()
        all_findings: list[Finding] = []
        total_tokens_in = 0
        total_tokens_out = 0

        # Step 1: Regex secret scanning
        logger.info(
            "Scanning %d files for secrets in PR #%d",
            len(parsed_event.files),
            parsed_event.pr_number,
        )
        regex_findings = self._scan_secrets(parsed_event)
        all_findings.extend(regex_findings)
        logger.info("Found %d secrets via regex", len(regex_findings))

        # Step 2: LLM analysis per chunk
        logger.info(
            "Running LLM analysis on %d chunks", len(parsed_event.chunks)
        )
        for chunk in parsed_event.chunks:
            try:
                llm_findings, response = self._analyze_chunk(chunk)
                all_findings.extend(llm_findings)
                total_tokens_in += response.tokens_in
                total_tokens_out += response.tokens_out
            except Exception as e:
                logger.error(
                    "LLM analysis failed for chunk %d: %s",
                    chunk.chunk_index,
                    str(e),
                )

        # Deduplicate findings (same file + line + category)
        all_findings = self._deduplicate(all_findings)

        latency_ms = int((time.monotonic() - start) * 1000)

        # Build the event
        security_event = SecurityReviewEvent(
            review_id=parsed_event.review_id,
            trace_id=parsed_event.trace_id,
            repo_full_name=parsed_event.repo_full_name,
            pr_number=parsed_event.pr_number,
            pr_url=parsed_event.pr_url,
            head_sha=parsed_event.head_sha,
            findings=all_findings,
            files_analyzed=len(parsed_event.files),
            chunks_analyzed=len(parsed_event.chunks),
            agent_meta={
                "agent": "security",
                "model": self.bedrock.default_model_id,
                "tokens_in": total_tokens_in,
                "tokens_out": total_tokens_out,
                "latency_ms": latency_ms,
            },
        )

        # Step 3: Write findings to DynamoDB
        if self.table:
            self._write_findings(security_event)

        logger.info(
            "Security review complete: %d findings (%d critical, %d warning, %d info) in %dms",
            len(all_findings),
            sum(1 for f in all_findings if f.severity == Severity.CRITICAL),
            sum(1 for f in all_findings if f.severity == Severity.WARNING),
            sum(1 for f in all_findings if f.severity == Severity.INFO),
            latency_ms,
        )

        return security_event

    def _scan_secrets(self, parsed_event: ParsedPREvent) -> list[Finding]:
        """Scan all file diffs for hardcoded secrets using regex patterns.

        Args:
            parsed_event: The parsed PR event with file changes.

        Returns:
            List of Finding objects for detected secrets.
        """
        findings: list[Finding] = []

        for file in parsed_event.files:
            # Handle both dict and FileChange objects
            if isinstance(file, dict):
                filepath = file.get("path", "")
                patch = file.get("patch", "")
            else:
                filepath = file.path
                patch = file.patch

            if not patch:
                continue

            # Only scan added lines (lines starting with +)
            for line_num, line in enumerate(patch.split("\n"), start=1):
                if not line.startswith("+"):
                    continue

                for pattern_info in SECRET_PATTERNS:
                    if pattern_info["pattern"].search(line):
                        findings.append(
                            Finding(
                                severity=pattern_info["severity"],
                                category=pattern_info["category"],
                                file=filepath,
                                line=line_num,
                                message=f"{pattern_info['name']} detected in code",
                                suggestion=(
                                    "Move this secret to environment variables or "
                                    "AWS Secrets Manager. Never commit secrets to source control."
                                ),
                                agent="security",
                            )
                        )
                        break  # One finding per line

        return findings

    def _analyze_chunk(
        self, chunk: DiffChunk
    ) -> tuple[list[Finding], BedrockResponse]:
        """Analyze a diff chunk using Nova Pro for security vulnerabilities.

        Args:
            chunk: DiffChunk to analyze.

        Returns:
            Tuple of (findings list, BedrockResponse for token tracking).
        """
        prompt = _build_chunk_prompt(chunk)
        response = self.bedrock.invoke(
            prompt=prompt,
            system_prompt=SECURITY_SYSTEM_PROMPT,
            temperature=0.1,
            max_tokens=2048,
        )

        findings = self._parse_llm_response(response.text, chunk)
        return findings, response

    def _parse_llm_response(
        self, response_text: str, chunk: DiffChunk
    ) -> list[Finding]:
        """Parse the LLM JSON response into Finding objects.

        Args:
            response_text: Raw JSON string from the LLM.
            chunk: The chunk that was analyzed (for context).

        Returns:
            List of Finding objects parsed from the response.
        """
        import json as json_module

        findings: list[Finding] = []

        try:
            # Try to extract JSON from the response (LLM may add markdown wrapping)
            json_str = response_text.strip()
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0].strip()
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0].strip()

            data = json_module.loads(json_str)
            raw_findings = data.get("findings", [])

            for raw in raw_findings:
                severity_str = raw.get("severity", "INFO").lower()
                try:
                    severity = Severity(severity_str)
                except ValueError:
                    severity = Severity.INFO

                findings.append(
                    Finding(
                        severity=severity,
                        category=raw.get("category", "other"),
                        file=raw.get("file", "unknown"),
                        line=raw.get("line", 0),
                        message=raw.get("message", ""),
                        suggestion=raw.get("suggestion", ""),
                        agent="security",
                    )
                )

        except (json_module.JSONDecodeError, KeyError, IndexError) as e:
            logger.warning("Failed to parse LLM response: %s", str(e))

        return findings

    def _deduplicate(self, findings: list[Finding]) -> list[Finding]:
        """Remove duplicate findings (same file + line + category).

        Args:
            findings: List of all findings.

        Returns:
            Deduplicated list of findings.
        """
        seen: set[str] = set()
        unique: list[Finding] = []

        for f in findings:
            key = f"{f.file}:{f.line}:{f.category}"
            if key not in seen:
                seen.add(key)
                unique.append(f)

        return unique

    def _write_findings(self, event: SecurityReviewEvent) -> None:
        """Write security findings to DynamoDB.

        Args:
            event: SecurityReviewEvent to persist.
        """
        if not self.table:
            return

        try:
            for i, finding in enumerate(event.findings):
                self.table.put_item(
                    Item={
                        "pk": f"REV#{event.review_id}",
                        "sk": f"FINDING#security#{i}",
                        "severity": finding.severity.value,
                        "category": finding.category,
                        "file": finding.file,
                        "line": finding.line,
                        "message": finding.message,
                        "suggestion": finding.suggestion,
                        "agent": "security",
                        "pr_number": event.pr_number,
                        "repo": event.repo_full_name,
                    }
                )

            logger.info(
                "Wrote %d findings to DynamoDB for review %s",
                len(event.findings),
                event.review_id,
            )
        except Exception as e:
            logger.error("Failed to write findings to DynamoDB: %s", str(e))
