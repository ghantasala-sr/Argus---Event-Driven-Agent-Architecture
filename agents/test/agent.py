"""Test Agent — scans PR diffs for missing tests and edge cases."""

import logging
import time
from typing import Optional

import boto3

from shared.bedrock_client import BedrockClient, BedrockResponse
from shared.models import DiffChunk, Finding, ParsedPREvent, SecurityReviewEvent, Severity

logger = logging.getLogger(__name__)

TEST_SYSTEM_PROMPT = """You are a senior QA automation engineer reviewing code.
Analyze the provided code diff for missing test coverage and test quality.

Focus on:
1. Missing unit tests for new logic
2. Unhandled edge cases or boundary conditions
3. Poor mocking practices (e.g., hitting real APIs in tests)
4. Lack of assertions or testing the wrong thing
5. Flaky test patterns (e.g., reliance on hardcoded sleep/time)

For each issue found, respond in this exact JSON format:
{
  "findings": [
    {
      "severity": "CRITICAL|WARNING|INFO",
      "category": "missing_test|edge_case|mocking|flaky|other",
      "file": "path/to/file.py",
      "line": 42,
      "message": "Brief description of the testing issue",
      "suggestion": "How to improve test coverage"
    }
  ]
}

If no testing issues are found, respond with: {"findings": []}

IMPORTANT RULES:
- Focus heavily on TESTING quality and coverage.
- If they added a feature but no tests for it, that is a WARNING missing_test.
- Be precise with file paths.
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

class TestAgent:
    def __init__(self, bedrock_client: BedrockClient, dynamodb_table: Optional[str] = None, region: Optional[str] = None):
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

        test_event = SecurityReviewEvent(
            event_type="review.test",
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
                "agent": "test",
                "model": self.bedrock.default_model_id,
                "tokens_in": total_tokens_in,
                "tokens_out": total_tokens_out,
                "latency_ms": latency_ms,
            },
        )

        if self.table:
             self._write_findings(test_event)

        return test_event

    def _analyze_chunk(self, chunk: DiffChunk) -> tuple[list[Finding], BedrockResponse]:
        prompt = _build_chunk_prompt(chunk)
        response = self.bedrock.invoke(prompt=prompt, system_prompt=TEST_SYSTEM_PROMPT, temperature=0.1, max_tokens=2048)
        findings = self._parse_llm_response(response.text, chunk)
        return findings, response

    def _parse_llm_response(self, response_text: str, chunk: DiffChunk) -> list[Finding]:
        import json
        findings = []
        try:
            json_str = response_text.strip()
            if "```json" in json_str: json_str = json_str.split("```json")[1].split("```")[0].strip()
            elif "```" in json_str: json_str = json_str.split("```")[1].split("```")[0].strip()
            data = json.loads(json_str)
            for raw in data.get("findings", []):
                try: sev = Severity(raw.get("severity", "INFO").lower())
                except ValueError: sev = Severity.INFO
                findings.append(Finding(
                    severity=sev, category=raw.get("category", "test"),
                    file=raw.get("file", "unknown"), line=raw.get("line", 0),
                    message=raw.get("message", ""), suggestion=raw.get("suggestion", ""), agent="test"
                ))
        except Exception:
            pass
        return findings

    def _write_findings(self, event: SecurityReviewEvent) -> None:
        if not self.table: return
        try:
            for i, finding in enumerate(event.findings):
                self.table.put_item(Item={
                    "pk": f"REV#{event.review_id}", "sk": f"FINDING#test#{i}",
                    "severity": finding.severity.value, "category": finding.category,
                    "file": finding.file, "line": finding.line, "message": finding.message,
                    "suggestion": finding.suggestion, "agent": "test",
                    "pr_number": event.pr_number, "repo": event.repo_full_name,
                })
        except Exception as e:
            logger.error("Failed to write findings to DynamoDB: %s", str(e))
