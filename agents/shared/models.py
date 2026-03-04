"""Pydantic event schemas for all Argus inter-agent communication.

ALL events flow through these models. Never pass raw dicts between agents.
Phase 1 defines: PRWebhookEvent, ParsedPREvent, and supporting models.
"""


from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# --- Enums ---


class Severity(str, Enum):
    """Finding severity levels, ordered from most to least critical."""

    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class FileStatus(str, Enum):
    """Git file status in a PR diff."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


# --- Base Models ---


class AgentMeta(BaseModel):
    """Metadata about the agent that produced an event.

    Tracks model usage, cost, and performance for observability.
    """

    agent: str
    model: str = "none"  # "none" for deterministic agents like Parser
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    tools_called: list[str] = Field(default_factory=list)


class BaseEvent(BaseModel):
    """Base event that all Argus events extend.

    Every event carries correlation IDs for tracing and agent metadata
    for cost/performance tracking.
    """

    event_type: str
    review_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trace_id: str = ""
    agent_meta: AgentMeta = Field(default_factory=lambda: AgentMeta(agent="unknown"))


# --- PR Webhook Event (GitHub → API Gateway → SNS) ---


class PRWebhookEvent(BaseModel):
    """Raw GitHub pull_request webhook event, published to SNS: pr.webhook.

    This is a simplified representation of the GitHub webhook payload,
    containing only the fields Argus needs.
    """

    action: str  # "opened", "synchronize", "reopened"
    repo_full_name: str  # "owner/repo"
    repo_clone_url: str  # "https://github.com/owner/repo.git"
    pr_number: int
    pr_title: str
    pr_url: str  # HTML URL of the PR
    pr_diff_url: str  # URL to fetch the raw diff
    head_sha: str  # Commit SHA of the PR head
    base_ref: str  # Target branch (e.g., "main")
    head_ref: str  # Source branch (e.g., "feature/foo")
    sender: str  # GitHub username who opened/updated the PR
    installation_id: int = 0  # GitHub App installation ID


# --- Parsed PR Event (Parser → SNS: pr.parsed → Fan-out to review agents) ---


class FileChange(BaseModel):
    """A single file changed in the PR.

    Contains the parsed diff content and metadata for review agents.
    """

    path: str  # File path relative to repo root
    language: str  # Detected language (e.g., "python", "typescript")
    status: FileStatus  # added, modified, deleted, renamed
    additions: int = 0  # Number of lines added
    deletions: int = 0  # Number of lines deleted
    patch: str = ""  # Raw unified diff for this file
    source_path: Optional[str] = None  # Original path if renamed


class DiffChunk(BaseModel):
    """A group of related file changes for agent processing.

    Large PRs are split into chunks (~500 lines each) so agents
    can process them within token limits.
    """

    chunk_index: int  # 0-based index within the PR
    total_chunks: int  # Total number of chunks for this PR
    files: list[FileChange]
    total_lines: int = 0  # Total diff lines in this chunk


class PRStats(BaseModel):
    """Summary statistics for a parsed PR."""

    total_files: int = 0
    total_additions: int = 0
    total_deletions: int = 0
    languages: list[str] = Field(default_factory=list)
    has_generated_files: bool = False
    generated_files_filtered: int = 0


class ParsedPREvent(BaseEvent):
    """Structured PR data published to SNS: pr.parsed.

    This is the primary input for all review agents. Contains the full
    parsed diff, file metadata, and chunked content for processing.
    """

    event_type: str = "pr.parsed"
    repo_full_name: str
    pr_number: int
    pr_title: str
    pr_url: str
    head_sha: str
    base_ref: str
    head_ref: str
    sender: str
    installation_id: int = 0
    files: list[FileChange] = Field(default_factory=list)
    chunks: list[DiffChunk] = Field(default_factory=list)
    stats: PRStats = Field(default_factory=PRStats)


# --- Phase 2: Review Agent Findings ---


class Finding(BaseModel):
    """A single finding from a review agent.

    Represents one security, performance, or style issue found in the PR.
    Used by all review agents — the category field indicates the source.
    """

    severity: Severity
    category: str  # e.g., "sql_injection", "hardcoded_secret", "n_plus_one"
    file: str  # File path where the issue was found
    line: int = 0  # Line number (0 if not applicable)
    message: str  # Human-readable description of the issue
    suggestion: str = ""  # Suggested fix or remediation
    agent: str = ""  # Agent that produced this finding


class SecurityReviewEvent(BaseEvent):
    """Security Agent findings published to SNS: review.findings.

    Contains all security issues found in the PR diff, classified by severity.
    """

    event_type: str = "review.security"
    repo_full_name: str
    pr_number: int
    pr_url: str
    head_sha: str
    findings: list[Finding] = Field(default_factory=list)
    files_analyzed: int = 0
    chunks_analyzed: int = 0

