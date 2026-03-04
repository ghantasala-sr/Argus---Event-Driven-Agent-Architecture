"""Unit tests for GitHubClient."""

from __future__ import annotations

import hashlib
import hmac
from unittest.mock import MagicMock, patch

import pytest

from agents.shared.github_client import GitHubClient


class TestWebhookSignatureVerification:
    """Tests for webhook signature validation."""

    def test_valid_signature(self) -> None:
        """Valid HMAC-SHA256 signature should return True."""
        secret = "test-secret"
        payload = b'{"action": "opened"}'
        expected_sig = "sha256=" + hmac.new(
            secret.encode(), payload, hashlib.sha256
        ).hexdigest()

        assert GitHubClient.verify_webhook_signature(payload, expected_sig, secret) is True

    def test_invalid_signature(self) -> None:
        """Invalid signature should return False."""
        assert (
            GitHubClient.verify_webhook_signature(
                b"payload", "sha256=invalid", "secret"
            )
            is False
        )

    def test_empty_signature(self) -> None:
        """Empty signature header should return False."""
        assert GitHubClient.verify_webhook_signature(b"payload", "", "secret") is False

    def test_tampered_payload(self) -> None:
        """Signature for different payload should fail."""
        secret = "test-secret"
        original = b'{"action": "opened"}'
        tampered = b'{"action": "closed"}'
        sig = "sha256=" + hmac.new(
            secret.encode(), original, hashlib.sha256
        ).hexdigest()

        assert GitHubClient.verify_webhook_signature(tampered, sig, secret) is False


class TestGitHubClientInit:
    """Tests for GitHubClient initialization."""

    def test_init_sets_fields(self) -> None:
        client = GitHubClient(
            app_id="123",
            private_key="fake-key",
            installation_id=456,
        )
        assert client.app_id == "123"
        assert client.private_key == "fake-key"
        assert client.installation_id == 456

    def test_init_default_installation_id(self) -> None:
        client = GitHubClient(app_id="123", private_key="fake-key")
        assert client.installation_id == 0


class TestGetPRFiles:
    """Tests for get_pr_files with mocked PyGithub."""

    @patch.object(GitHubClient, "_get_github")
    def test_get_pr_files_returns_file_list(self, mock_get_github: MagicMock) -> None:
        """Test that get_pr_files returns properly structured file dicts."""
        # Mock file objects
        mock_file1 = MagicMock()
        mock_file1.filename = "src/main.py"
        mock_file1.status = "modified"
        mock_file1.additions = 10
        mock_file1.deletions = 3
        mock_file1.patch = "@@ -1,3 +1,10 @@\n+new code"
        mock_file1.previous_filename = None

        mock_file2 = MagicMock()
        mock_file2.filename = "README.md"
        mock_file2.status = "added"
        mock_file2.additions = 5
        mock_file2.deletions = 0
        mock_file2.patch = "@@ -0,0 +1,5 @@\n+# README"
        mock_file2.previous_filename = None

        mock_pr = MagicMock()
        mock_pr.get_files.return_value = [mock_file1, mock_file2]

        mock_repo = MagicMock()
        mock_repo.get_pull.return_value = mock_pr

        mock_github = MagicMock()
        mock_github.get_repo.return_value = mock_repo
        mock_get_github.return_value = mock_github

        client = GitHubClient(app_id="123", private_key="key")
        files = client.get_pr_files("owner/repo", 1)

        assert len(files) == 2
        assert files[0]["filename"] == "src/main.py"
        assert files[0]["status"] == "modified"
        assert files[0]["additions"] == 10
        assert files[1]["filename"] == "README.md"
        assert files[1]["status"] == "added"
