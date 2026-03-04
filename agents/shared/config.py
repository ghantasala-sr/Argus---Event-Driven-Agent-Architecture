"""Argus configuration — loads and validates environment variables.

All agent config comes from environment variables injected by CDK.
This module provides typed access with validation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentConfig:
    """Configuration for an Argus agent, loaded from environment variables."""

    stage: str
    transport_type: str
    input_queue_url: str
    pr_parsed_topic_arn: str
    dynamodb_table: str
    github_app_id: str
    github_private_key_secret: str

    @classmethod
    def from_env(cls) -> AgentConfig:
        """Load configuration from environment variables.

        Returns:
            AgentConfig with all required values populated.

        Raises:
            ValueError: If any required environment variable is missing.
        """
        required_vars = {
            "stage": "STAGE",
            "transport_type": "TRANSPORT_TYPE",
            "input_queue_url": "INPUT_QUEUE_URL",
            "pr_parsed_topic_arn": "PR_PARSED_TOPIC_ARN",
            "dynamodb_table": "DYNAMODB_TABLE",
            "github_app_id": "GITHUB_APP_ID",
            "github_private_key_secret": "GITHUB_PRIVATE_KEY_SECRET",
        }

        values: dict[str, str] = {}
        missing: list[str] = []

        for field_name, env_var in required_vars.items():
            value = os.environ.get(env_var, "")
            if not value:
                missing.append(env_var)
            values[field_name] = value

        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        return cls(**values)


@dataclass(frozen=True)
class WebhookConfig:
    """Configuration for the webhook handler Lambda."""

    stage: str
    pr_webhook_topic_arn: str
    github_webhook_secret: str

    @classmethod
    def from_env(cls) -> WebhookConfig:
        """Load webhook configuration from environment variables."""
        required_vars = {
            "stage": "STAGE",
            "pr_webhook_topic_arn": "PR_WEBHOOK_TOPIC_ARN",
            "github_webhook_secret": "GITHUB_WEBHOOK_SECRET",
        }

        values: dict[str, str] = {}
        missing: list[str] = []

        for field_name, env_var in required_vars.items():
            value = os.environ.get(env_var, "")
            if not value:
                missing.append(env_var)
            values[field_name] = value

        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        return cls(**values)
