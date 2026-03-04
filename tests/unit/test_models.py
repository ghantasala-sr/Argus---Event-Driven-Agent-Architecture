"""Unit tests for Pydantic event models."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agents.shared.models import (
    AgentMeta,
    BaseEvent,
    DiffChunk,
    FileChange,
    FileStatus,
    ParsedPREvent,
    PRStats,
    PRWebhookEvent,
    Severity,
)


class TestPRWebhookEvent:
    """Tests for PRWebhookEvent model."""

    def test_valid_webhook_event(self) -> None:
        event = PRWebhookEvent(
            action="opened",
            repo_full_name="owner/repo",
            repo_clone_url="https://github.com/owner/repo.git",
            pr_number=1,
            pr_title="Test PR",
            pr_url="https://github.com/owner/repo/pull/1",
            pr_diff_url="https://github.com/owner/repo/pull/1.diff",
            head_sha="abc123",
            base_ref="main",
            head_ref="feature",
            sender="testuser",
        )
        assert event.action == "opened"
        assert event.pr_number == 1
        assert event.installation_id == 0  # Default

    def test_webhook_event_serialization(self) -> None:
        event = PRWebhookEvent(
            action="synchronize",
            repo_full_name="owner/repo",
            repo_clone_url="https://github.com/owner/repo.git",
            pr_number=42,
            pr_title="Update auth",
            pr_url="https://github.com/owner/repo/pull/42",
            pr_diff_url="https://github.com/owner/repo/pull/42.diff",
            head_sha="def456",
            base_ref="main",
            head_ref="fix/auth",
            sender="devuser",
            installation_id=12345,
        )
        data = event.model_dump()
        restored = PRWebhookEvent(**data)
        assert restored.action == "synchronize"
        assert restored.installation_id == 12345


class TestFileChange:
    """Tests for FileChange model."""

    def test_file_change_defaults(self) -> None:
        fc = FileChange(path="src/main.py", language="python", status=FileStatus.ADDED)
        assert fc.additions == 0
        assert fc.deletions == 0
        assert fc.patch == ""
        assert fc.source_path is None

    def test_renamed_file(self) -> None:
        fc = FileChange(
            path="src/new_name.py",
            language="python",
            status=FileStatus.RENAMED,
            source_path="src/old_name.py",
        )
        assert fc.source_path == "src/old_name.py"

    def test_file_status_enum(self) -> None:
        assert FileStatus.ADDED.value == "added"
        assert FileStatus.MODIFIED.value == "modified"
        assert FileStatus.DELETED.value == "deleted"
        assert FileStatus.RENAMED.value == "renamed"


class TestDiffChunk:
    """Tests for DiffChunk model."""

    def test_chunk_creation(self) -> None:
        files = [
            FileChange(path="a.py", language="python", status=FileStatus.ADDED, additions=10),
            FileChange(path="b.py", language="python", status=FileStatus.MODIFIED, additions=5),
        ]
        chunk = DiffChunk(chunk_index=0, total_chunks=1, files=files, total_lines=15)
        assert chunk.chunk_index == 0
        assert len(chunk.files) == 2
        assert chunk.total_lines == 15


class TestAgentMeta:
    """Tests for AgentMeta model."""

    def test_default_agent_meta(self) -> None:
        meta = AgentMeta(agent="parser")
        assert meta.model == "none"
        assert meta.tokens_in == 0
        assert meta.tokens_out == 0
        assert meta.latency_ms == 0
        assert meta.tools_called == []

    def test_full_agent_meta(self) -> None:
        meta = AgentMeta(
            agent="security",
            model="nova-pro",
            tokens_in=1000,
            tokens_out=500,
            latency_ms=2500,
            tools_called=["semgrep", "bandit"],
        )
        assert meta.agent == "security"
        assert meta.model == "nova-pro"
        assert len(meta.tools_called) == 2


class TestBaseEvent:
    """Tests for BaseEvent model."""

    def test_base_event_defaults(self) -> None:
        event = BaseEvent(event_type="test", review_id="rev-123")
        assert event.event_type == "test"
        assert event.review_id == "rev-123"
        assert event.trace_id == ""
        assert event.agent_meta.agent == "unknown"
        assert isinstance(event.timestamp, datetime)

    def test_base_event_json_roundtrip(self) -> None:
        event = BaseEvent(
            event_type="review.security",
            review_id="rev-456",
            trace_id="trace-789",
            agent_meta=AgentMeta(agent="security", model="nova-pro"),
        )
        json_str = event.model_dump_json()
        restored = BaseEvent.model_validate_json(json_str)
        assert restored.event_type == "review.security"
        assert restored.agent_meta.agent == "security"


class TestParsedPREvent:
    """Tests for ParsedPREvent model."""

    def test_parsed_event_defaults(self) -> None:
        event = ParsedPREvent(
            review_id="rev-123",
            repo_full_name="owner/repo",
            pr_number=1,
            pr_title="Test",
            pr_url="https://github.com/owner/repo/pull/1",
            head_sha="abc",
            base_ref="main",
            head_ref="feature",
            sender="user",
        )
        assert event.event_type == "pr.parsed"
        assert event.files == []
        assert event.chunks == []
        assert event.stats.total_files == 0

    def test_parsed_event_full(self) -> None:
        files = [
            FileChange(path="a.py", language="python", status=FileStatus.ADDED, additions=10)
        ]
        chunks = [DiffChunk(chunk_index=0, total_chunks=1, files=files, total_lines=10)]
        stats = PRStats(
            total_files=1,
            total_additions=10,
            languages=["python"],
        )

        event = ParsedPREvent(
            review_id="rev-123",
            repo_full_name="owner/repo",
            pr_number=1,
            pr_title="Test",
            pr_url="https://github.com/owner/repo/pull/1",
            head_sha="abc",
            base_ref="main",
            head_ref="feature",
            sender="user",
            files=files,
            chunks=chunks,
            stats=stats,
        )
        assert len(event.files) == 1
        assert len(event.chunks) == 1
        assert event.stats.total_additions == 10
        assert "python" in event.stats.languages


class TestSeverity:
    """Tests for Severity enum."""

    def test_severity_values(self) -> None:
        assert Severity.CRITICAL.value == "critical"
        assert Severity.WARNING.value == "warning"
        assert Severity.INFO.value == "info"
