"""Parser Agent — Lambda handler (thin wrapper).

This is the Lambda entry point triggered by SQS. It deserializes
the webhook event, calls ParserAgent.process(), and publishes the
result to SNS: pr.parsed.

ALL business logic lives in agent.py — this file is a thin wrapper only.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3
from shared.github_client import GitHubClient
from shared.models import PRWebhookEvent
from shared.transport import get_transport

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def _get_github_client() -> GitHubClient:
    """Create a GitHubClient using credentials from environment/secrets.

    Returns:
        Authenticated GitHubClient instance.
    """
    app_id = os.environ.get("GITHUB_APP_ID", "")

    # In production, fetch private key from Secrets Manager
    secret_arn = os.environ.get("GITHUB_PRIVATE_KEY_SECRET", "")
    if secret_arn:
        secrets_client = boto3.client("secretsmanager")
        response = secrets_client.get_secret_value(SecretId=secret_arn)
        private_key = response["SecretString"]
    else:
        # Local development fallback
        private_key = os.environ.get("GITHUB_PRIVATE_KEY", "")

    return GitHubClient(app_id=app_id, private_key=private_key)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for Parser Agent.

    Triggered by SQS messages from the parse-queue. Each message contains
    a PRWebhookEvent published by the webhook handler.

    Args:
        event: Lambda event containing SQS Records.
        context: Lambda context (unused).

    Returns:
        Response dict with statusCode.
    """
    from parser.agent import ParserAgent

    transport = get_transport()
    github_client = _get_github_client()
    dynamodb = boto3.resource("dynamodb")
    agent = ParserAgent(github_client=github_client, dynamodb_resource=dynamodb)

    pr_parsed_topic = os.environ.get("PR_PARSED_TOPIC_ARN", "")

    for record in event.get("Records", []):
        try:
            body = json.loads(record["body"])
            webhook_event = PRWebhookEvent(**body)

            logger.info(
                "Parser processing PR #%d from %s",
                webhook_event.pr_number,
                webhook_event.repo_full_name,
            )

            # Process: fetch diff → parse → chunk → write metadata
            parsed_event = agent.process(webhook_event)

            # Publish to SNS: pr.parsed for fan-out to review agents
            transport.publish(pr_parsed_topic, parsed_event.model_dump(mode="json"))

            logger.info(
                "Published ParsedPREvent for PR #%d (review_id=%s)",
                webhook_event.pr_number,
                parsed_event.review_id,
            )

        except Exception as e:
            logger.error(
                "Failed to process record: %s",
                str(e),
                exc_info=True,
            )
            # Re-raise so SQS retries (up to maxReceiveCount, then DLQ)
            raise

    return {"statusCode": 200}
