"""Parser Agent — parses GitHub PR diffs into structured events.

The Parser Agent is the first agent in the Argus pipeline. It receives
raw webhook events, fetches the PR diff from GitHub, parses it into
structured FileChange objects, detects languages, filters generated files,
and chunks large diffs for downstream review agents.

NO LLM is used in this agent — all logic is deterministic.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any

from shared.github_client import GitHubClient
from shared.models import (
    AgentMeta,
    DiffChunk,
    FileChange,
    FileStatus,
    ParsedPREvent,
    PRStats,
    PRWebhookEvent,
)

logger = logging.getLogger(__name__)

# Maximum diff lines per chunk for review agents
MAX_CHUNK_LINES = 500

# File extensions → language mapping
LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "jsx",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".r": "r",
    ".sql": "sql",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".xml": "xml",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".md": "markdown",
    ".tf": "terraform",
    ".dockerfile": "dockerfile",
}

# Patterns that indicate generated/auto-managed files (skip reviewing these)
GENERATED_FILE_PATTERNS: list[str] = [
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Pipfile.lock",
    "Gemfile.lock",
    "go.sum",
    "Cargo.lock",
    "composer.lock",
    ".min.js",
    ".min.css",
    ".bundle.js",
    ".chunk.js",
    "dist/",
    "build/",
    "vendor/",
    "node_modules/",
    "__pycache__/",
    ".pyc",
    ".map",
    ".d.ts",
    "generated/",
    "auto-generated",
    ".pb.go",
    "_pb2.py",
    ".swagger.",
]


def _detect_language(filepath: str) -> str:
    """Detect programming language from file extension.

    Args:
        filepath: Path to the file (e.g., "src/main.py").

    Returns:
        Language identifier string (e.g., "python"), or "unknown".
    """
    lower = filepath.lower()

    # Special cases
    if lower.endswith("dockerfile") or lower.startswith("dockerfile"):
        return "dockerfile"
    if lower.endswith("makefile") or lower == "makefile":
        return "makefile"

    _, ext = os.path.splitext(lower)
    return LANGUAGE_MAP.get(ext, "unknown")


def _is_generated_file(filepath: str) -> bool:
    """Check if a file is auto-generated and should be skipped.

    Args:
        filepath: Path to the file.

    Returns:
        True if the file matches a generated file pattern.
    """
    lower = filepath.lower()
    return any(pattern in lower for pattern in GENERATED_FILE_PATTERNS)


def _map_git_status(status: str) -> FileStatus:
    """Map GitHub API file status to our FileStatus enum.

    Args:
        status: GitHub status string ("added", "modified", "removed", "renamed").

    Returns:
        FileStatus enum value.
    """
    status_map = {
        "added": FileStatus.ADDED,
        "modified": FileStatus.MODIFIED,
        "removed": FileStatus.DELETED,
        "renamed": FileStatus.RENAMED,
    }
    return status_map.get(status, FileStatus.MODIFIED)


def _chunk_files(files: list[FileChange], max_lines: int = MAX_CHUNK_LINES) -> list[DiffChunk]:
    """Split file changes into manageable chunks for review agents.

    Groups files together until the chunk reaches max_lines, then starts
    a new chunk. Each chunk contains complete files (never splits a file).

    Args:
        files: List of FileChange objects to chunk.
        max_lines: Maximum diff lines per chunk.

    Returns:
        List of DiffChunk objects.
    """
    if not files:
        return []

    chunks: list[DiffChunk] = []
    current_files: list[dict] = []
    current_lines = 0

    for file in files:
        file_lines = file.additions + file.deletions
        file_dict = file.model_dump() if hasattr(file, "model_dump") else dict(file)

        # If adding this file would exceed the limit and we have files already,
        # finalize the current chunk
        if current_lines + file_lines > max_lines and current_files:
            chunks.append(
                DiffChunk(
                    chunk_index=len(chunks),
                    total_chunks=0,  # Will be set after all chunks created
                    files=current_files,
                    total_lines=current_lines,
                )
            )
            current_files = []
            current_lines = 0

        current_files.append(file_dict)
        current_lines += file_lines

    # Don't forget the last chunk
    if current_files:
        chunks.append(
            DiffChunk(
                chunk_index=len(chunks),
                total_chunks=0,
                files=current_files,
                total_lines=current_lines,
            )
        )

    # Set total_chunks on all chunks
    total = len(chunks)
    for chunk in chunks:
        chunk.total_chunks = total

    return chunks


class ParserAgent:
    """Parses GitHub PR diffs into structured events for review agents.

    This agent is purely deterministic — no LLM calls. It:
    1. Fetches the PR diff and file list from GitHub
    2. Parses diffs into structured FileChange objects
    3. Detects languages per file
    4. Filters out generated/auto-managed files
    5. Chunks large diffs for downstream processing
    6. Writes review metadata to DynamoDB
    """

    def __init__(self, github_client: GitHubClient, dynamodb_resource: Any = None) -> None:
        """Initialize Parser Agent.

        Args:
            github_client: Authenticated GitHubClient instance.
            dynamodb_resource: boto3 DynamoDB resource (for writing review metadata).
        """
        self.github = github_client
        self.dynamodb = dynamodb_resource

    def process(self, webhook_event: PRWebhookEvent) -> ParsedPREvent:
        """Parse a PR webhook event into a structured ParsedPREvent.

        Args:
            webhook_event: Raw webhook event from GitHub.

        Returns:
            ParsedPREvent with parsed diff, file changes, and chunks.
        """
        start_time = time.monotonic()
        review_id = str(uuid.uuid4())

        logger.info(
            "Processing PR #%d from %s (review_id=%s)",
            webhook_event.pr_number,
            webhook_event.repo_full_name,
            review_id,
        )

        # Set installation ID for GitHub auth
        self.github.installation_id = webhook_event.installation_id

        # 1. Fetch file list from GitHub API
        raw_files = self.github.get_pr_files(
            webhook_event.repo_full_name,
            webhook_event.pr_number,
        )

        # 2. Parse into FileChange objects
        all_files: list[FileChange] = []
        generated_count = 0

        for f in raw_files:
            filepath = f["filename"]

            # Filter generated files
            if _is_generated_file(filepath):
                generated_count += 1
                logger.debug("Skipping generated file: %s", filepath)
                continue

            file_change = FileChange(
                path=filepath,
                language=_detect_language(filepath),
                status=_map_git_status(f["status"]),
                additions=f.get("additions", 0),
                deletions=f.get("deletions", 0),
                patch=f.get("patch", ""),
                source_path=f.get("previous_filename"),
            )
            all_files.append(file_change)

        # 3. Compute stats
        languages = list(set(f.language for f in all_files if f.language != "unknown"))
        stats = PRStats(
            total_files=len(all_files),
            total_additions=sum(f.additions for f in all_files),
            total_deletions=sum(f.deletions for f in all_files),
            languages=sorted(languages),
            has_generated_files=generated_count > 0,
            generated_files_filtered=generated_count,
        )

        # 4. Chunk files for review agents
        chunks = _chunk_files(all_files)

        # 5. Calculate agent metadata
        latency_ms = int((time.monotonic() - start_time) * 1000)
        agent_meta = AgentMeta(
            agent="parser",
            model="none",  # Parser is deterministic — no LLM
            latency_ms=latency_ms,
            tools_called=["github_api"],
        )

        # 6. Build the parsed event
        parsed_event = ParsedPREvent(
            review_id=review_id,
            agent_meta=agent_meta,
            repo_full_name=webhook_event.repo_full_name,
            pr_number=webhook_event.pr_number,
            pr_title=webhook_event.pr_title,
            pr_url=webhook_event.pr_url,
            head_sha=webhook_event.head_sha,
            base_ref=webhook_event.base_ref,
            head_ref=webhook_event.head_ref,
            sender=webhook_event.sender,
            installation_id=webhook_event.installation_id,
            files=all_files,
            chunks=chunks,
            stats=stats,
        )

        # 7. Write review metadata to DynamoDB
        self._write_review_metadata(parsed_event)

        logger.info(
            "Parsed PR #%d: %d files, %d chunks, %d generated filtered, %dms",
            webhook_event.pr_number,
            stats.total_files,
            len(chunks),
            generated_count,
            latency_ms,
        )

        return parsed_event

    def _write_review_metadata(self, event: ParsedPREvent) -> None:
        """Write review metadata to DynamoDB.

        Args:
            event: The parsed PR event to record.
        """
        if not self.dynamodb:
            logger.warning("No DynamoDB resource — skipping metadata write")
            return

        table_name = os.environ.get("DYNAMODB_TABLE", "argus-reviews")
        try:
            table = self.dynamodb.Table(table_name)
            table.put_item(
                Item={
                    "pk": f"REV#{event.review_id}",
                    "sk": "META",
                    "status": "parsing_complete",
                    "pr_url": event.pr_url,
                    "pr_number": event.pr_number,
                    "repo": event.repo_full_name,
                    "sender": event.sender,
                    "head_sha": event.head_sha,
                    "files_count": event.stats.total_files,
                    "chunks_count": len(event.chunks),
                    "languages": event.stats.languages,
                    "created_at": event.timestamp.isoformat(),
                    "latency_ms": event.agent_meta.latency_ms,
                }
            )
            logger.info("Wrote review metadata: REV#%s", event.review_id)
        except Exception as e:
            logger.error("Failed to write review metadata: %s", e)
            # Don't fail the review if DynamoDB write fails — it's metadata only
