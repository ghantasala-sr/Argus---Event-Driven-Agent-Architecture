"""GitHub API client for Argus.

Handles GitHub App authentication and provides methods to fetch
PR diffs, file lists, and metadata. Also validates webhook signatures.

Uses PyGithub for API interactions and JWT/installation tokens for auth.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any, Optional

import jwt
import requests
from github import Github, GithubIntegration
from github.PullRequest import PullRequest

logger = logging.getLogger(__name__)


class GitHubClient:
    """GitHub API wrapper using GitHub App authentication.

    Authenticates as a GitHub App installation to access repo-level
    resources (diffs, files, PR metadata, posting comments).
    """

    def __init__(
        self,
        app_id: str,
        private_key: str,
        installation_id: int = 0,
    ) -> None:
        """Initialize GitHub client with App credentials.

        Args:
            app_id: GitHub App ID.
            private_key: PEM-encoded private key for the GitHub App.
            installation_id: GitHub App installation ID (can be set later).
        """
        self.app_id = app_id
        self.private_key = private_key
        self.installation_id = installation_id
        self._github: Optional[Github] = None
        self._token_expires_at: float = 0

    def _get_installation_token(self) -> str:
        """Generate an installation access token using the App's private key.

        Returns:
            Installation access token string.
        """
        integration = GithubIntegration(
            integration_id=int(self.app_id),
            private_key=self.private_key,
        )
        token = integration.get_access_token(self.installation_id)
        self._token_expires_at = time.time() + 3500  # ~58 min (tokens last 1hr)
        return token.token

    def _get_github(self) -> Github:
        """Get an authenticated Github instance, refreshing token if needed.

        Returns:
            Authenticated Github client.
        """
        if self._github is None or time.time() >= self._token_expires_at:
            token = self._get_installation_token()
            self._github = Github(login_or_token=token)
        return self._github

    def get_pr(self, repo_full_name: str, pr_number: int) -> PullRequest:
        """Fetch a PullRequest object.

        Args:
            repo_full_name: Repository full name (e.g., "owner/repo").
            pr_number: Pull request number.

        Returns:
            PyGithub PullRequest object.
        """
        gh = self._get_github()
        repo = gh.get_repo(repo_full_name)
        return repo.get_pull(pr_number)

    def get_pr_diff(self, repo_full_name: str, pr_number: int) -> str:
        """Fetch the raw unified diff for a pull request.

        Uses the GitHub API media type for diffs to get the raw patch.

        Args:
            repo_full_name: Repository full name.
            pr_number: Pull request number.

        Returns:
            Raw unified diff string.
        """
        gh = self._get_github()
        repo = gh.get_repo(repo_full_name)
        pr = repo.get_pull(pr_number)

        # Use requests to fetch diff with proper Accept header
        headers = {
            "Authorization": f"token {gh._Github__requester._Requester__authorizationHeader.split(' ')[1]}",
            "Accept": "application/vnd.github.v3.diff",
        }
        response = requests.get(pr.url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.text

    def get_pr_files(self, repo_full_name: str, pr_number: int) -> list[dict[str, Any]]:
        """Fetch the list of files changed in a pull request.

        Args:
            repo_full_name: Repository full name.
            pr_number: Pull request number.

        Returns:
            List of file dicts with filename, status, additions, deletions, patch.
        """
        pr = self.get_pr(repo_full_name, pr_number)
        files = []
        for f in pr.get_files():
            files.append(
                {
                    "filename": f.filename,
                    "status": f.status,
                    "additions": f.additions,
                    "deletions": f.deletions,
                    "patch": getattr(f, "patch", "") or "",
                    "previous_filename": getattr(f, "previous_filename", None),
                }
            )
        return files

    def get_pr_metadata(self, repo_full_name: str, pr_number: int) -> dict[str, Any]:
        """Fetch PR metadata (title, body, labels, etc).

        Args:
            repo_full_name: Repository full name.
            pr_number: Pull request number.

        Returns:
            Dict with PR metadata fields.
        """
        pr = self.get_pr(repo_full_name, pr_number)
        return {
            "title": pr.title,
            "body": pr.body or "",
            "url": pr.html_url,
            "diff_url": pr.diff_url,
            "head_sha": pr.head.sha,
            "base_ref": pr.base.ref,
            "head_ref": pr.head.ref,
            "user": pr.user.login,
            "labels": [label.name for label in pr.labels],
            "created_at": pr.created_at.isoformat(),
            "updated_at": pr.updated_at.isoformat() if pr.updated_at else None,
        }

    @staticmethod
    def verify_webhook_signature(
        payload_body: bytes,
        signature_header: str,
        secret: str,
    ) -> bool:
        """Verify GitHub webhook signature (X-Hub-Signature-256).

        Args:
            payload_body: Raw request body bytes.
            signature_header: Value of X-Hub-Signature-256 header.
            secret: Webhook secret configured in GitHub App settings.

        Returns:
            True if signature is valid, False otherwise.
        """
        if not signature_header:
            return False

        expected_signature = (
            "sha256="
            + hmac.new(
                secret.encode("utf-8"),
                payload_body,
                hashlib.sha256,
            ).hexdigest()
        )

        return hmac.compare_digest(expected_signature, signature_header)
