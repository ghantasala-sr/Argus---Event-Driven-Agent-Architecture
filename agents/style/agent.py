"""Style Agent — scans PR diffs for clean code issues."""

import logging
import time

import boto3
from shared.bedrock_client import BedrockClient, BedrockResponse
from shared.models import DiffChunk, Finding, ParsedPREvent, SecurityReviewEvent, Severity

logger = logging.getLogger(__name__)

STYLE_SYSTEM_PROMPT = """You are a senior software engineer reviewing code style.
Analyze the provided code diff for style, readability, and clean code issues.

Focus on:
1. Naming conventions (variables, functions, classes)
2. Missing or poor docstrings and comments
3. Dead code or unused variables
4. overly complex or unreadable logic
5. Consistency with standard conventions (like PEP8 for Python)

For each issue found, respond in this exact JSON format:
{
  "findings": [
    {
      "severity": "CRITICAL|WARNING|INFO",
      "category": "style|naming|docstring|readability|complexity|other",
      "file": "path/to/file.py",
      "line": 42,
      "message": "Brief description of the issue",
      "suggestion": "How to fix it"
    }
  ]
}

If no style issues are found, respond with: {"findings": []}

IMPORTANT RULES:
- Focus on STYLE, not security or performance.
- INFO severity is appropriate for minor style things. WARNING for unreadable code.
- Stay concise.
"""


def _build_chunk_prompt(chunk: DiffChunk) -> str:
    parts = [f"Review this code diff (chunk {chunk.chunk_index + 1}/{chunk.total_chunks}):\n"]
    for file in chunk.files:
        filepath = file.get("path", "unknown") if isinstance(file, dict) else file.path
        patch = file.get("patch", "") if isinstance(file, dict) else file.patch
        language = file.get("language", "unknown") if isinstance(file, dict) else file.language
        parts.append(f"### File: {filepath} (language: {language})")
        parts.append(f"```diff\n{patch}\n```\n")
    return "\n".join(parts)


class StyleAgent:
    def __init__(
        self,
        bedrock_client: BedrockClient,
        dynamodb_table: str | None = None,
        region: str | None = None,
    ):
        self.bedrock = bedrock_client
        self.dynamodb_table = dynamodb_table
        self.region = region or "us-east-1"
        if dynamodb_table:
            self.dynamodb = boto3.resource("dynamodb", region_name=self.region)
            self.table = self.dynamodb.Table(dynamodb_table)
        else:
            self.table = None

    def process(self, parsed_event: ParsedPREvent) -> SecurityReviewEvent:
        start = time.monotonic()
        all_findings = []
        total_tokens_in = 0
        total_tokens_out = 0

        for chunk in parsed_event.chunks:
            try:
                llm_findings, response = self._analyze_chunk(chunk)
                all_findings.extend(llm_findings)
                total_tokens_in += response.tokens_in
                total_tokens_out += response.tokens_out
            except Exception as e:
                logger.error("LLM analysis failed for chunk %d: %s", chunk.chunk_index, str(e))

        latency_ms = int((time.monotonic() - start) * 1000)

        style_event = SecurityReviewEvent(
            event_type="review.style",
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
                "agent": "style",
                "model": self.bedrock.default_model_id,
                "tokens_in": total_tokens_in,
                "tokens_out": total_tokens_out,
                "latency_ms": latency_ms,
            },
        )

        if self.table:
            self._write_findings(style_event)

        return style_event

    def _analyze_chunk(self, chunk: DiffChunk) -> tuple[list[Finding], BedrockResponse]:
        prompt = _build_chunk_prompt(chunk)
        response = self.bedrock.invoke(
            prompt=prompt, system_prompt=STYLE_SYSTEM_PROMPT, temperature=0.1, max_tokens=2048
        )
        findings = self._parse_llm_response(response.text, chunk)
        return findings, response

    def _parse_llm_response(self, response_text: str, chunk: DiffChunk) -> list[Finding]:
        import json

        findings = []
        try:
            json_str = response_text.strip()
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0].strip()
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0].strip()
            data = json.loads(json_str)
            for raw in data.get("findings", []):
                try:
                    sev = Severity(raw.get("severity", "INFO").lower())
                except ValueError:
                    sev = Severity.INFO
                findings.append(
                    Finding(
                        severity=sev,
                        category=raw.get("category", "style"),
                        file=raw.get("file", "unknown"),
                        line=raw.get("line", 0),
                        message=raw.get("message", ""),
                        suggestion=raw.get("suggestion", ""),
                        agent="style",
                    )
                )
        except Exception:
            pass
        return findings

    def _write_findings(self, event: SecurityReviewEvent) -> None:
        if not self.table:
            return
        try:
            for i, finding in enumerate(event.findings):
                self.table.put_item(
                    Item={
                        "pk": f"REV#{event.review_id}",
                        "sk": f"FINDING#style#{i}",
                        "severity": finding.severity.value,
                        "category": finding.category,
                        "file": finding.file,
                        "line": finding.line,
                        "message": finding.message,
                        "suggestion": finding.suggestion,
                        "agent": "style",
                        "pr_number": event.pr_number,
                        "repo": event.repo_full_name,
                    }
                )
        except Exception as e:
            logger.error("Failed to write findings to DynamoDB: %s", str(e))
