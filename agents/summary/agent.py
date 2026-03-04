"""Summary Agent — aggregates findings and posts the final PR review comment.

Waits for all parallel review agents to finish, deduplicates overlapping
findings, ranks them by severity, formats a Markdown comment, and posts
it to GitHub while updating the commit status.
"""

import logging
import os
import time
from typing import Any

from boto3.dynamodb.conditions import Key
from jinja2 import Environment, FileSystemLoader
from shared.github_client import GitHubClient

logger = logging.getLogger(__name__)


class SummaryAgent:
    """Aggregates findings and posts GitHub reviews.

    Args:
        dynamodb_table: DynamoDB table resource.
        github_client: Authenticated GitHubClient.
        template_dir: Path to the Jinja2 templates directory.
    """

    def __init__(
        self,
        dynamodb_table: Any,
        github_client: GitHubClient,
        template_dir: str | None = None,
    ) -> None:
        self.table = dynamodb_table
        self.github = github_client
        self.template_dir = template_dir or os.path.join(os.path.dirname(__file__), "templates")

        self.jinja_env = Environment(
            loader=FileSystemLoader(self.template_dir),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )

        # Register template helper macro
        self.jinja_env.globals["finding_partial"] = self._render_finding_partial

    def _render_finding_partial(self, finding: dict[str, Any]) -> str:
        """Helper to render the appropriate partial template based on severity."""
        severity = finding.get("severity", "info").lower()
        try:
            template = self.jinja_env.get_template(f"finding_{severity}.md.j2")
            return template.render(finding=finding)
        except Exception as e:
            logger.error("Failed to render finding partial for %s: %s", severity, str(e))
            return f"- **{finding.get('category')}**: {finding.get('message')}"

    def process(
        self,
        review_id: str,
        repo_full_name: str,
        pr_number: int,
        head_sha: str,
        expected_agents: int = 4,
        timeout_seconds: int = 120,
    ) -> dict[str, Any]:
        """Aggregate findings and post the final review.

        Args:
            review_id: Correlation ID for the review.
            repo_full_name: GitHub repository name.
            pr_number: GitHub PR number.
            head_sha: Commit SHA for status checks.
            expected_agents: Number of agents to wait for.
            timeout_seconds: Max time to wait for agents to finish.

        Returns:
            Dict containing the execution stats and final verdict.
        """
        start_time = time.monotonic()
        findings = self._wait_and_fetch_findings(review_id, expected_agents, timeout_seconds)

        deduped = self._deduplicate(findings)
        ranked = self._rank_findings(deduped)

        # Determine verdict
        verdict = "approve"
        if ranked["critical"]:
            verdict = "request_changes"
        elif ranked["warning"] or ranked["info"]:
            verdict = "comment"

        # Format markdown
        comment_body = self._format_markdown(ranked, verdict, start_time)

        # Post to GitHub
        logger.info("Posting review to %s PR #%d", repo_full_name, pr_number)
        self.github.post_review(repo_full_name, pr_number, comment_body)

        # Set commit status
        state = "success" if verdict != "request_changes" else "failure"
        description = "Review complete: no critical issues"
        if verdict == "request_changes":
            description = f"Found {len(ranked['critical'])} critical issues"

        self.github.set_commit_status(
            repo_full_name=repo_full_name,
            sha=head_sha,
            state=state,
            context="argus/review",
            description=description,
        )

        return {
            "review_id": review_id,
            "verdict": verdict,
            "total_findings": len(deduped),
            "latency_ms": int((time.monotonic() - start_time) * 1000),
        }

    def _wait_and_fetch_findings(
        self, review_id: str, expected_agents: int, timeout: int
    ) -> list[dict[str, Any]]:
        """Poll DynamoDB for findings until all agents finish or timeout."""
        start = time.monotonic()
        seen_agents: set[str] = set()
        findings: list[dict[str, Any]] = []

        while time.monotonic() - start < timeout:
            response = self.table.query(KeyConditionExpression=Key("pk").eq(f"REV#{review_id}"))
            items = response.get("Items", [])

            findings = [item for item in items if item["sk"].startswith("FINDING#")]

            # Check which agents have reported findings.
            # (Note: An agent might report 0 findings, so we also need AgentCore
            # or Lambda to write a completion marker if we wanted perfect sync.
            # For this phase, we aggregate whatever is available at timeout or
            # heuristically return if we see > 0 findings from all expected agents.)
            seen_agents = {f.get("agent") for f in findings if f.get("agent")}

            # If we don't have perfect completion markers, we rely primarily on the
            # timeout or a heuristic. We'll sleep and poll.
            if len(seen_agents) >= expected_agents:
                break

            time.sleep(2)

        logger.info(
            "Finished polling. Found %d findings from %d agents after %d seconds.",
            len(findings),
            len(seen_agents),
            int(time.monotonic() - start),
        )
        return findings

    def _deduplicate(self, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove overlapping findings across different agents."""
        unique = []
        seen_keys = set()

        for f in findings:
            # We use file+line+category as the unique heuristic
            key = f"{f.get('file', '')}:{f.get('line', 0)}"

            # If it's a critical finding, keep it and possibly overwrite a warning on the same line
            if key not in seen_keys:
                seen_keys.add(key)
                unique.append(f)
            else:
                # Basic overlap resolution: keep the higher severity
                for existing in unique:
                    if f"{existing.get('file', '')}:{existing.get('line', 0)}" == key:
                        is_exist_not_crit = existing.get("severity") != "critical"
                        if is_exist_not_crit and f.get("severity") == "critical":
                            unique.remove(existing)
                            unique.append(f)
                        break

        return unique

    def _rank_findings(self, findings: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        """Group and rank findings by severity."""
        ranked: dict[str, list[dict[str, Any]]] = {
            "critical": [],
            "warning": [],
            "info": [],
        }

        for f in findings:
            sev = f.get("severity", "info").lower()
            if sev in ranked:
                ranked[sev].append(f)
            else:
                ranked["info"].append(f)

        return ranked

    def _format_markdown(
        self, ranked: dict[str, list[dict[str, Any]]], verdict: str, start_time: float
    ) -> str:
        """Render the final review markdown comment using Jinja2."""
        template = self.jinja_env.get_template("review_comment.md.j2")
        total_files = len(
            set(f.get("file") for fList in ranked.values() for f in fList if f.get("file"))
        )

        return template.render(
            verdict=verdict,
            critical_findings=ranked["critical"],
            warning_findings=ranked["warning"],
            info_findings=ranked["info"],
            stats={
                "total_files": max(total_files, 1),  # mock stats for summary
                "total_additions": 0,
            },
            execution_time_ms=int((time.monotonic() - start_time) * 1000),
        )
