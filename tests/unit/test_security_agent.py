"""Unit tests for the Security Agent."""

import json
from unittest.mock import MagicMock, patch

import pytest

from shared.bedrock_client import BedrockClient, BedrockResponse
from shared.models import (
    DiffChunk,
    FileChange,
    Finding,
    ParsedPREvent,
    PRStats,
    SecurityReviewEvent,
    Severity,
)
from security.agent import SecurityAgent, SECRET_PATTERNS


# ────────────────────────────────────────────────────
# Test fixtures
# ────────────────────────────────────────────────────


def _make_parsed_event(files=None, chunks=None) -> ParsedPREvent:
    """Create a ParsedPREvent for testing."""
    return ParsedPREvent(
        review_id="test-review-123",
        trace_id="trace-abc",
        repo_full_name="test-org/test-repo",
        pr_number=42,
        pr_title="Test PR",
        pr_url="https://github.com/test-org/test-repo/pull/42",
        head_sha="abc123",
        base_ref="main",
        head_ref="feature/test",
        sender="testuser",
        files=files or [],
        chunks=chunks or [],
        stats=PRStats(),
    )


def _make_file_change(filepath: str, diff: str, language: str = "python") -> FileChange:
    """Create a FileChange for testing."""
    return FileChange(
        path=filepath,
        status="modified",
        additions=diff.count("\n+"),
        deletions=diff.count("\n-"),
        language=language,
        patch=diff,
    )


def _make_chunk(files: list, chunk_index: int = 0, total_chunks: int = 1) -> DiffChunk:
    """Create a DiffChunk for testing."""
    file_dicts = []
    for f in files:
        if isinstance(f, FileChange):
            file_dicts.append(f.model_dump())
        else:
            file_dicts.append(f)
    return DiffChunk(
        chunk_index=chunk_index,
        total_chunks=total_chunks,
        files=file_dicts,
        total_lines=sum(f.get("additions", 0) + f.get("deletions", 0) for f in file_dicts),
    )


# ────────────────────────────────────────────────────
# Regex Secret Scanning Tests
# ────────────────────────────────────────────────────


class TestSecretScanning:
    """Tests for regex-based secret detection."""

    def _make_agent(self):
        mock_bedrock = MagicMock(spec=BedrockClient)
        mock_bedrock.default_model_id = "amazon.nova-pro-v1:0"
        return SecurityAgent(bedrock_client=mock_bedrock)

    def test_detects_aws_access_key(self):
        agent = self._make_agent()
        file = _make_file_change(
            "config.py",
            "+AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'\n"
        )
        event = _make_parsed_event(files=[file])
        findings = agent._scan_secrets(event)

        assert len(findings) >= 1
        assert findings[0].severity == Severity.CRITICAL
        assert findings[0].category == "hardcoded_secret"
        assert "AWS" in findings[0].message

    def test_detects_password(self):
        agent = self._make_agent()
        file = _make_file_change(
            "settings.py",
            "+password = 'super_secret_password_123'\n"
        )
        event = _make_parsed_event(files=[file])
        findings = agent._scan_secrets(event)

        assert len(findings) >= 1
        assert findings[0].category == "hardcoded_secret"

    def test_detects_private_key(self):
        agent = self._make_agent()
        file = _make_file_change(
            "key.pem",
            "+-----BEGIN RSA PRIVATE KEY-----\n+MIIEpAIBAAKCAQEA\n"
        )
        event = _make_parsed_event(files=[file])
        findings = agent._scan_secrets(event)

        assert len(findings) >= 1
        assert findings[0].severity == Severity.CRITICAL

    def test_detects_github_token(self):
        agent = self._make_agent()
        file = _make_file_change(
            "deploy.sh",
            "+GITHUB_TOKEN=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij\n"
        )
        event = _make_parsed_event(files=[file])
        findings = agent._scan_secrets(event)

        assert len(findings) >= 1
        assert findings[0].severity == Severity.CRITICAL

    def test_detects_db_connection_string(self):
        agent = self._make_agent()
        file = _make_file_change(
            "db.py",
            "+DATABASE_URL = 'postgresql://admin:password@db.example.com:5432/mydb'\n"
        )
        event = _make_parsed_event(files=[file])
        findings = agent._scan_secrets(event)

        assert len(findings) >= 1
        assert findings[0].severity == Severity.CRITICAL

    def test_ignores_removed_lines(self):
        """Only added lines (starting with +) should be scanned."""
        agent = self._make_agent()
        file = _make_file_change(
            "config.py",
            "-AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'\n"
        )
        event = _make_parsed_event(files=[file])
        findings = agent._scan_secrets(event)

        assert len(findings) == 0

    def test_clean_code_no_findings(self):
        agent = self._make_agent()
        file = _make_file_change(
            "main.py",
            "+def hello():\n+    return 'world'\n"
        )
        event = _make_parsed_event(files=[file])
        findings = agent._scan_secrets(event)

        assert len(findings) == 0


# ────────────────────────────────────────────────────
# LLM Analysis Tests
# ────────────────────────────────────────────────────


class TestLLMAnalysis:
    """Tests for Bedrock Nova Pro security analysis."""

    def _make_agent_with_mock_llm(self, llm_response_text: str):
        mock_bedrock = MagicMock(spec=BedrockClient)
        mock_bedrock.default_model_id = "amazon.nova-pro-v1:0"
        mock_bedrock.invoke.return_value = BedrockResponse(
            text=llm_response_text,
            tokens_in=200,
            tokens_out=100,
            latency_ms=500,
            model_id="amazon.nova-pro-v1:0",
        )
        return SecurityAgent(bedrock_client=mock_bedrock)

    def test_llm_finds_sql_injection(self):
        llm_response = json.dumps({
            "findings": [
                {
                    "severity": "CRITICAL",
                    "category": "sql_injection",
                    "file": "api/users.py",
                    "line": 15,
                    "message": "SQL query built with string concatenation from user input",
                    "suggestion": "Use parameterized queries instead",
                }
            ]
        })

        agent = self._make_agent_with_mock_llm(llm_response)
        file = _make_file_change(
            "api/users.py",
            '+query = f"SELECT * FROM users WHERE id = {user_id}"\n'
        )
        chunk = _make_chunk([file])
        event = _make_parsed_event(files=[file], chunks=[chunk])

        result = agent.process(event)

        # Should have at least the LLM finding
        sql_findings = [f for f in result.findings if f.category == "sql_injection"]
        assert len(sql_findings) >= 1
        assert sql_findings[0].severity == Severity.CRITICAL
        assert sql_findings[0].file == "api/users.py"

    def test_llm_no_findings(self):
        llm_response = json.dumps({"findings": []})

        agent = self._make_agent_with_mock_llm(llm_response)
        file = _make_file_change(
            "utils.py",
            "+def add(a: int, b: int) -> int:\n+    return a + b\n"
        )
        chunk = _make_chunk([file])
        event = _make_parsed_event(files=[file], chunks=[chunk])

        result = agent.process(event)

        # Only regex findings (should be none for clean code)
        assert len(result.findings) == 0

    def test_llm_response_with_markdown_wrapping(self):
        llm_response = '```json\n{"findings": [{"severity": "WARNING", "category": "data_exposure", "file": "log.py", "line": 5, "message": "Logging sensitive data", "suggestion": "Mask PII"}]}\n```'

        agent = self._make_agent_with_mock_llm(llm_response)
        file = _make_file_change("log.py", "+logger.info(f'User password: {password}')\n")
        chunk = _make_chunk([file])
        event = _make_parsed_event(files=[file], chunks=[chunk])

        result = agent.process(event)

        data_exposure = [f for f in result.findings if f.category == "data_exposure"]
        assert len(data_exposure) >= 1

    def test_llm_malformed_response_handled(self):
        """LLM returns invalid JSON — should not crash."""
        agent = self._make_agent_with_mock_llm("This is not JSON at all")
        file = _make_file_change("test.py", "+pass\n")
        chunk = _make_chunk([file])
        event = _make_parsed_event(files=[file], chunks=[chunk])

        # Should not raise
        result = agent.process(event)
        assert isinstance(result, SecurityReviewEvent)

    def test_llm_error_handled_gracefully(self):
        """LLM call fails — agent should continue with regex findings only."""
        mock_bedrock = MagicMock(spec=BedrockClient)
        mock_bedrock.default_model_id = "amazon.nova-pro-v1:0"
        mock_bedrock.invoke.side_effect = Exception("Bedrock timeout")

        agent = SecurityAgent(bedrock_client=mock_bedrock)

        file = _make_file_change(
            "secrets.py",
            "+AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'\n"
        )
        chunk = _make_chunk([file])
        event = _make_parsed_event(files=[file], chunks=[chunk])

        result = agent.process(event)

        # Should still have regex findings even though LLM failed
        assert len(result.findings) >= 1


# ────────────────────────────────────────────────────
# Full Process Tests
# ────────────────────────────────────────────────────


class TestSecurityAgentProcess:
    """Tests for the full SecurityAgent.process() flow."""

    def test_process_returns_security_event(self):
        mock_bedrock = MagicMock(spec=BedrockClient)
        mock_bedrock.default_model_id = "amazon.nova-pro-v1:0"
        mock_bedrock.invoke.return_value = BedrockResponse(
            text='{"findings": []}', tokens_in=50, tokens_out=10
        )

        agent = SecurityAgent(bedrock_client=mock_bedrock)
        file = _make_file_change("main.py", "+print('hello')\n")
        chunk = _make_chunk([file])
        event = _make_parsed_event(files=[file], chunks=[chunk])

        result = agent.process(event)

        assert isinstance(result, SecurityReviewEvent)
        assert result.event_type == "review.security"
        assert result.repo_full_name == "test-org/test-repo"
        assert result.pr_number == 42
        assert result.files_analyzed == 1
        assert result.chunks_analyzed == 1

    def test_process_empty_pr(self):
        mock_bedrock = MagicMock(spec=BedrockClient)
        mock_bedrock.default_model_id = "amazon.nova-pro-v1:0"

        agent = SecurityAgent(bedrock_client=mock_bedrock)
        event = _make_parsed_event()

        result = agent.process(event)

        assert isinstance(result, SecurityReviewEvent)
        assert len(result.findings) == 0
        assert result.files_analyzed == 0

    def test_deduplication(self):
        """Same finding from regex and LLM should be deduplicated."""
        llm_response = json.dumps({
            "findings": [
                {
                    "severity": "CRITICAL",
                    "category": "hardcoded_secret",
                    "file": "config.py",
                    "line": 1,
                    "message": "AWS key found",
                    "suggestion": "Use env vars",
                }
            ]
        })

        mock_bedrock = MagicMock(spec=BedrockClient)
        mock_bedrock.default_model_id = "amazon.nova-pro-v1:0"
        mock_bedrock.invoke.return_value = BedrockResponse(
            text=llm_response, tokens_in=100, tokens_out=50
        )

        agent = SecurityAgent(bedrock_client=mock_bedrock)

        # Same secret will be found by both regex and LLM
        file = _make_file_change(
            "config.py",
            "+AWS_KEY = 'AKIAIOSFODNN7EXAMPLE'\n"
        )
        chunk = _make_chunk([file])
        event = _make_parsed_event(files=[file], chunks=[chunk])

        result = agent.process(event)

        # Deduplication should combine regex + LLM finding for same file:line:category
        config_findings = [f for f in result.findings if f.file == "config.py" and f.line == 1]
        assert len(config_findings) == 1


class TestFindingModel:
    """Tests for the Finding Pydantic model."""

    def test_finding_creation(self):
        f = Finding(
            severity=Severity.CRITICAL,
            category="sql_injection",
            file="api/users.py",
            line=15,
            message="SQL injection vulnerability",
            suggestion="Use parameterized queries",
            agent="security",
        )
        assert f.severity == Severity.CRITICAL
        assert f.line == 15

    def test_finding_defaults(self):
        f = Finding(
            severity=Severity.INFO,
            category="other",
            file="test.py",
            message="Minor issue",
        )
        assert f.line == 0
        assert f.suggestion == ""
        assert f.agent == ""

    def test_finding_serialization(self):
        f = Finding(
            severity=Severity.WARNING,
            category="xss",
            file="template.html",
            line=10,
            message="Unescaped output",
        )
        data = f.model_dump()
        assert data["severity"] == "warning"
        assert data["category"] == "xss"
