"""Webhook handler — validates GitHub webhooks and publishes to SNS.

This Lambda sits behind API Gateway. It validates the GitHub webhook
signature, filters for relevant events (pull_request opened/synchronize),
and publishes a PRWebhookEvent to SNS: pr.webhook.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any

import boto3
from shared.models import PRWebhookEvent

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Actions we care about — ignore "closed", "labeled", etc.
RELEVANT_ACTIONS = {"opened", "synchronize", "reopened"}

sns_client = boto3.client("sns")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """API Gateway Lambda handler for GitHub webhooks.

    Validates signature, filters events, publishes to SNS.

    Args:
        event: API Gateway proxy event.
        context: Lambda context.

    Returns:
        API Gateway response dict.
    """
    try:
        # 1. Extract headers and body
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        body = event.get("body", "")
        is_base64 = event.get("isBase64Encoded", False)

        if is_base64:
            import base64

            body_bytes = base64.b64decode(body)
            body_str = body_bytes.decode("utf-8")
        else:
            body_bytes = body.encode("utf-8") if isinstance(body, str) else body
            body_str = body if isinstance(body, str) else body.decode("utf-8")

        # 2. Verify webhook signature
        webhook_secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
        signature = headers.get("x-hub-signature-256", "")

        if webhook_secret and signature:
            expected = (
                "sha256="
                + hmac.new(
                    webhook_secret.encode("utf-8"),
                    body_bytes,
                    hashlib.sha256,
                ).hexdigest()
            )
            if not hmac.compare_digest(expected, signature):
                logger.warning("Invalid webhook signature")
                return _response(401, {"error": "Invalid signature"})

        # 3. Parse the payload
        payload = json.loads(body_str)

        # 4. Check event type
        github_event = headers.get("x-github-event", "")
        if github_event != "pull_request":
            logger.info("Ignoring event type: %s", github_event)
            return _response(200, {"message": f"Ignored event: {github_event}"})

        # 5. Check action
        action = payload.get("action", "")
        if action not in RELEVANT_ACTIONS:
            logger.info("Ignoring PR action: %s", action)
            return _response(200, {"message": f"Ignored action: {action}"})

        # 6. Build webhook event
        pr = payload.get("pull_request", {})
        repo = payload.get("repository", {})
        installation = payload.get("installation", {})

        webhook_event = PRWebhookEvent(
            action=action,
            repo_full_name=repo.get("full_name", ""),
            repo_clone_url=repo.get("clone_url", ""),
            pr_number=pr.get("number", 0),
            pr_title=pr.get("title", ""),
            pr_url=pr.get("html_url", ""),
            pr_diff_url=pr.get("diff_url", ""),
            head_sha=pr.get("head", {}).get("sha", ""),
            base_ref=pr.get("base", {}).get("ref", ""),
            head_ref=pr.get("head", {}).get("ref", ""),
            sender=payload.get("sender", {}).get("login", ""),
            installation_id=installation.get("id", 0),
        )

        # 7. Publish to SNS: pr.webhook
        topic_arn = os.environ.get("PR_WEBHOOK_TOPIC_ARN", "")
        if not topic_arn:
            logger.error("PR_WEBHOOK_TOPIC_ARN not set")
            return _response(500, {"error": "Topic ARN not configured"})

        message_id = sns_client.publish(
            TopicArn=topic_arn,
            Message=webhook_event.model_dump_json(),
            MessageAttributes={
                "event_type": {
                    "DataType": "String",
                    "StringValue": "pr.webhook",
                },
                "repo": {
                    "DataType": "String",
                    "StringValue": webhook_event.repo_full_name,
                },
            },
        )["MessageId"]

        logger.info(
            "Published PR #%d from %s (message_id=%s)",
            webhook_event.pr_number,
            webhook_event.repo_full_name,
            message_id,
        )

        return _response(
            200,
            {
                "message": "Webhook processed",
                "review_started": True,
                "pr_number": webhook_event.pr_number,
                "message_id": message_id,
            },
        )

    except json.JSONDecodeError as e:
        logger.error("Invalid JSON payload: %s", e)
        return _response(400, {"error": "Invalid JSON"})
    except Exception as e:
        logger.error("Webhook handler error: %s", e, exc_info=True)
        return _response(500, {"error": "Internal server error"})


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    """Build an API Gateway response.

    Args:
        status_code: HTTP status code.
        body: Response body dict (will be JSON-serialized).

    Returns:
        API Gateway proxy response format.
    """
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "X-Argus-Version": "1.0",
        },
        "body": json.dumps(body),
    }
