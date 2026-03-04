"""Unit tests for Parser Agent."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from agents.parser.agent import (
    MAX_CHUNK_LINES,
    ParserAgent,
    _chunk_files,
    _detect_language,
    _is_generated_file,
    _map_git_status,
)
from agents.shared.models import FileChange, FileStatus, PRWebhookEvent


class TestDetectLanguage:
    """Tests for language detection from file extensions."""

    def test_python(self) -> None:
        assert _detect_language("src/main.py") == "python"

    def test_typescript(self) -> None:
        assert _detect_language("src/app.ts") == "typescript"

    def test_tsx(self) -> None:
        assert _detect_language("components/App.tsx") == "tsx"

    def test_javascript(self) -> None:
        assert _detect_language("index.js") == "javascript"

    def test_go(self) -> None:
        assert _detect_language("cmd/server/main.go") == "go"

    def test_rust(self) -> None:
        assert _detect_language("src/lib.rs") == "rust"

    def test_java(self) -> None:
        assert _detect_language("src/Main.java") == "java"

    def test_dockerfile(self) -> None:
        assert _detect_language("Dockerfile") == "dockerfile"
        assert _detect_language("api.dockerfile") == "dockerfile"

    def test_unknown_extension(self) -> None:
        assert _detect_language("file.xyz") == "unknown"

    def test_yaml(self) -> None:
        assert _detect_language("config.yml") == "yaml"
        assert _detect_language("settings.yaml") == "yaml"

    def test_sql(self) -> None:
        assert _detect_language("migrations/001.sql") == "sql"

    def test_case_insensitive(self) -> None:
        assert _detect_language("Main.PY") == "python"
        assert _detect_language("App.TSX") == "tsx"


class TestIsGeneratedFile:
    """Tests for generated file detection."""

    def test_package_lock(self) -> None:
        assert _is_generated_file("package-lock.json") is True

    def test_yarn_lock(self) -> None:
        assert _is_generated_file("yarn.lock") is True

    def test_poetry_lock(self) -> None:
        assert _is_generated_file("poetry.lock") is True

    def test_node_modules(self) -> None:
        assert _is_generated_file("node_modules/lodash/index.js") is True

    def test_dist_directory(self) -> None:
        assert _is_generated_file("dist/bundle.js") is True

    def test_minified_js(self) -> None:
        assert _is_generated_file("jquery.min.js") is True

    def test_proto_generated(self) -> None:
        assert _is_generated_file("api_pb2.py") is True
        assert _is_generated_file("api.pb.go") is True

    def test_normal_file(self) -> None:
        assert _is_generated_file("src/main.py") is False
        assert _is_generated_file("README.md") is False

    def test_pyc_files(self) -> None:
        assert _is_generated_file("module.pyc") is True


class TestMapGitStatus:
    """Tests for GitHub status → FileStatus mapping."""

    def test_added(self) -> None:
        assert _map_git_status("added") == FileStatus.ADDED

    def test_modified(self) -> None:
        assert _map_git_status("modified") == FileStatus.MODIFIED

    def test_removed(self) -> None:
        assert _map_git_status("removed") == FileStatus.DELETED

    def test_renamed(self) -> None:
        assert _map_git_status("renamed") == FileStatus.RENAMED

    def test_unknown_defaults_to_modified(self) -> None:
        assert _map_git_status("copied") == FileStatus.MODIFIED


class TestChunkFiles:
    """Tests for diff chunking logic."""

    def test_empty_files(self) -> None:
        assert _chunk_files([]) == []

    def test_single_small_file(self) -> None:
        files = [
            FileChange(
                path="a.py", language="python", status=FileStatus.ADDED,
                additions=10, deletions=0,
            ),
        ]
        chunks = _chunk_files(files, max_lines=500)
        assert len(chunks) == 1
        assert chunks[0].chunk_index == 0
        assert chunks[0].total_chunks == 1
        assert len(chunks[0].files) == 1

    def test_multiple_small_files_single_chunk(self) -> None:
        files = [
            FileChange(
                path=f"file{i}.py", language="python", status=FileStatus.ADDED,
                additions=10, deletions=0,
            )
            for i in range(5)
        ]
        chunks = _chunk_files(files, max_lines=500)
        assert len(chunks) == 1
        assert len(chunks[0].files) == 5
        assert chunks[0].total_lines == 50

    def test_large_files_split_into_chunks(self) -> None:
        files = [
            FileChange(
                path=f"file{i}.py", language="python", status=FileStatus.ADDED,
                additions=200, deletions=0,
            )
            for i in range(5)
        ]
        chunks = _chunk_files(files, max_lines=500)
        assert len(chunks) >= 2
        # Verify total_chunks is set correctly on all
        for chunk in chunks:
            assert chunk.total_chunks == len(chunks)

    def test_single_huge_file(self) -> None:
        files = [
            FileChange(
                path="huge.py", language="python", status=FileStatus.ADDED,
                additions=1000, deletions=0,
            ),
        ]
        # A single file that exceeds max_lines stays in its own chunk
        chunks = _chunk_files(files, max_lines=500)
        assert len(chunks) == 1
        assert len(chunks[0].files) == 1


class TestParserAgent:
    """Tests for the ParserAgent.process() method."""

    def _make_webhook_event(self) -> PRWebhookEvent:
        return PRWebhookEvent(
            action="opened",
            repo_full_name="test-org/test-repo",
            repo_clone_url="https://github.com/test-org/test-repo.git",
            pr_number=42,
            pr_title="feat: add auth",
            pr_url="https://github.com/test-org/test-repo/pull/42",
            pr_diff_url="https://github.com/test-org/test-repo/pull/42.diff",
            head_sha="abc123",
            base_ref="main",
            head_ref="feature/auth",
            sender="testuser",
            installation_id=12345,
        )

    @mock_aws
    def test_process_parses_files(self, github_files_payload: list) -> None:
        """Test that process() correctly parses files from GitHub API."""
        # Setup mock GitHub client
        mock_github = MagicMock()
        mock_github.get_pr_files.return_value = github_files_payload

        # Setup DynamoDB
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName="argus-test-reviews",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        agent = ParserAgent(github_client=mock_github, dynamodb_resource=dynamodb)
        event = self._make_webhook_event()
        result = agent.process(event)

        # Should have filtered out package-lock.json
        assert result.stats.generated_files_filtered == 1
        assert result.stats.has_generated_files is True

        # Remaining files: login.py, middleware.py, test_auth.py, helpers.ts, README.md, old_module.py
        assert result.stats.total_files == 6

        # Check languages detected
        assert "python" in result.stats.languages
        assert "typescript" in result.stats.languages

        # Verify chunks were created
        assert len(result.chunks) >= 1

        # Verify metadata
        assert result.event_type == "pr.parsed"
        assert result.pr_number == 42
        assert result.repo_full_name == "test-org/test-repo"
        assert result.agent_meta.agent == "parser"
        assert result.agent_meta.model == "none"

    def test_process_no_dynamodb(self, github_files_payload: list) -> None:
        """Test that process() works without DynamoDB (graceful degradation)."""
        mock_github = MagicMock()
        mock_github.get_pr_files.return_value = github_files_payload

        agent = ParserAgent(github_client=mock_github, dynamodb_resource=None)
        event = self._make_webhook_event()
        result = agent.process(event)

        # Should still work, just skip DynamoDB write
        assert result.stats.total_files == 6
        assert result.review_id  # Should have a UUID

    def test_process_empty_pr(self) -> None:
        """Test that process() handles PRs with no files."""
        mock_github = MagicMock()
        mock_github.get_pr_files.return_value = []

        agent = ParserAgent(github_client=mock_github, dynamodb_resource=None)
        event = self._make_webhook_event()
        result = agent.process(event)

        assert result.stats.total_files == 0
        assert result.files == []
        assert result.chunks == []

    def test_process_all_generated_files(self) -> None:
        """Test PR where all files are generated/auto-managed."""
        mock_github = MagicMock()
        mock_github.get_pr_files.return_value = [
            {"filename": "package-lock.json", "status": "modified", "additions": 500,
             "deletions": 300, "patch": ""},
            {"filename": "yarn.lock", "status": "modified", "additions": 200,
             "deletions": 100, "patch": ""},
        ]

        agent = ParserAgent(github_client=mock_github, dynamodb_resource=None)
        event = self._make_webhook_event()
        result = agent.process(event)

        assert result.stats.total_files == 0
        assert result.stats.generated_files_filtered == 2
        assert result.files == []
