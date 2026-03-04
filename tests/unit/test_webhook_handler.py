"""Unit tests for webhook handler."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws


def _make_api_gateway_event(
    payload: dict[str, Any],
    secret: str = "",
    event_type: str = "pull_request",
) -> dict[str, Any]:
    """Build a mock API Gateway proxy event.

    Args:
        payload: The webhook body payload.
        secret: Webhook secret for signature generation.
        event_type: GitHub event type header value.

    Returns:
        API Gateway event dict.
    """
    body = json.dumps(payload)
    headers: dict[str, str] = {
        "x-github-event": event_type,
        "content-type": "application/json",
    }

    if secret:
        sig = "sha256=" + hmac.new(
            secret.encode(), body.encode(), hashlib.sha256
        ).hexdigest()
        headers["x-hub-signature-256"] = sig

    return {
        "headers": headers,
        "body": body,
        "isBase64Encoded": False,
    }


class TestWebhookHandler:
    """Tests for the webhook Lambda handler."""

    @mock_aws
    def test_valid_pr_opened(
        self,
        monkeypatch: pytest.MonkeyPatch,
        webhook_payload: dict[str, Any],
    ) -> None:
        """Test processing a valid pull_request opened event."""
        # Create SNS topic
        sns = boto3.client("sns", region_name="us-east-1")
        topic = sns.create_topic(Name="test-pr-webhook")

        monkeypatch.setenv("PR_WEBHOOK_TOPIC_ARN", topic["TopicArn"])
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")

        from agents.webhook.handler import handler

        event = _make_api_gateway_event(webhook_payload)
        response = handler(event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["review_started"] is True
        assert body["pr_number"] == 42

    @mock_aws
    def test_ignore_non_pr_event(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that non-pull_request events are ignored."""
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")

        from agents.webhook.handler import handler

        event = _make_api_gateway_event(
            {"action": "created"},
            event_type="issues",
        )
        response = handler(event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert "Ignored event" in body["message"]

    @mock_aws
    def test_ignore_irrelevant_action(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that irrelevant PR actions (e.g., 'closed') are ignored."""
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")

        from agents.webhook.handler import handler

        payload = {
            "action": "closed",
            "pull_request": {"number": 1},
            "repository": {"full_name": "o/r"},
        }
        event = _make_api_gateway_event(payload)
        response = handler(event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert "Ignored action" in body["message"]

    @mock_aws
    def test_invalid_signature_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        webhook_payload: dict[str, Any],
    ) -> None:
        """Test that invalid webhook signatures are rejected with 401."""
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "real-secret")

        from agents.webhook.handler import handler

        event = _make_api_gateway_event(
            webhook_payload,
            secret="wrong-secret",
        )
        response = handler(event, None)
        assert response["statusCode"] == 401

    @mock_aws
    def test_valid_signature_accepted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        webhook_payload: dict[str, Any],
    ) -> None:
        """Test that valid webhook signatures pass validation."""
        secret = "my-secret"
        sns = boto3.client("sns", region_name="us-east-1")
        topic = sns.create_topic(Name="test-topic")

        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", secret)
        monkeypatch.setenv("PR_WEBHOOK_TOPIC_ARN", topic["TopicArn"])

        from agents.webhook.handler import handler

        event = _make_api_gateway_event(webhook_payload, secret=secret)
        response = handler(event, None)
        assert response["statusCode"] == 200

    def test_invalid_json_returns_400(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that invalid JSON body returns 400."""
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")

        from agents.webhook.handler import handler

        event = {
            "headers": {"x-github-event": "pull_request"},
            "body": "not json {{{",
            "isBase64Encoded": False,
        }
        response = handler(event, None)
        assert response["statusCode"] == 400

    @mock_aws
    def test_missing_topic_arn(
        self,
        monkeypatch: pytest.MonkeyPatch,
        webhook_payload: dict[str, Any],
    ) -> None:
        """Test that missing topic ARN returns 500."""
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")
        monkeypatch.setenv("PR_WEBHOOK_TOPIC_ARN", "")

        from agents.webhook.handler import handler

        event = _make_api_gateway_event(webhook_payload)
        response = handler(event, None)
        assert response["statusCode"] == 500
