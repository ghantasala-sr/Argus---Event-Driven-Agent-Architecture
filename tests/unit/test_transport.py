"""Unit tests for AgentTransport abstraction (SQS implementation)."""

from __future__ import annotations

import json
import os
from typing import Any

import boto3
import pytest
from moto import mock_aws

from agents.shared.transport import SQSTransport, get_transport


class TestSQSTransport:
    """Tests for SQSTransport implementation."""

    @mock_aws
    def test_publish_and_consume(self) -> None:
        """Test basic publish → consume → ack flow."""
        region = "us-east-1"
        sqs = boto3.client("sqs", region_name=region)
        sns = boto3.client("sns", region_name=region)

        # Create resources
        queue = sqs.create_queue(QueueName="test-queue")
        queue_url = queue["QueueUrl"]
        queue_arn = sqs.get_queue_attributes(
            QueueUrl=queue_url, AttributeNames=["QueueArn"]
        )["Attributes"]["QueueArn"]

        topic = sns.create_topic(Name="test-topic")
        topic_arn = topic["TopicArn"]

        # Subscribe queue to topic
        sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=queue_arn)

        # Test transport
        transport = SQSTransport(queue_url=queue_url, region=region)

        # Publish
        event = {"event_type": "test.event", "data": "hello"}
        msg_id = transport.publish(topic_arn, event)
        assert msg_id

        # Consume
        messages = transport.consume(max_messages=1, wait_seconds=0)
        assert len(messages) >= 1

        # Ack
        transport.ack(messages[0]["receipt_handle"])

        # Verify message was deleted
        remaining = sqs.receive_message(
            QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=0
        )
        assert len(remaining.get("Messages", [])) == 0

    @mock_aws
    def test_consume_empty_queue(self) -> None:
        """Test consuming from an empty queue returns empty list."""
        sqs = boto3.client("sqs", region_name="us-east-1")
        queue = sqs.create_queue(QueueName="empty-queue")

        transport = SQSTransport(queue_url=queue["QueueUrl"], region="us-east-1")
        messages = transport.consume(max_messages=1, wait_seconds=0)
        assert messages == []

    @mock_aws
    def test_publish_message_attributes(self) -> None:
        """Test that publish includes event_type as message attribute."""
        region = "us-east-1"
        sns = boto3.client("sns", region_name=region)
        topic = sns.create_topic(Name="attr-topic")

        sqs = boto3.client("sqs", region_name=region)
        queue = sqs.create_queue(QueueName="attr-queue")

        transport = SQSTransport(queue_url=queue["QueueUrl"], region=region)
        event = {"event_type": "pr.parsed", "review_id": "rev-123"}
        msg_id = transport.publish(topic["TopicArn"], event)
        assert isinstance(msg_id, str)
        assert len(msg_id) > 0


class TestGetTransport:
    """Tests for the transport factory function."""

    @mock_aws
    def test_get_sqs_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test factory returns SQSTransport when TRANSPORT_TYPE=sqs."""
        sqs = boto3.client("sqs", region_name="us-east-1")
        queue = sqs.create_queue(QueueName="factory-queue")

        monkeypatch.setenv("TRANSPORT_TYPE", "sqs")
        monkeypatch.setenv("INPUT_QUEUE_URL", queue["QueueUrl"])
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")

        transport = get_transport()
        assert isinstance(transport, SQSTransport)

    def test_get_transport_missing_queue_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test factory raises ValueError when INPUT_QUEUE_URL is missing."""
        monkeypatch.setenv("TRANSPORT_TYPE", "sqs")
        monkeypatch.delenv("INPUT_QUEUE_URL", raising=False)

        with pytest.raises(ValueError, match="INPUT_QUEUE_URL"):
            get_transport()

    def test_get_transport_unsupported_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test factory raises ValueError for unknown transport types."""
        monkeypatch.setenv("TRANSPORT_TYPE", "rabbitmq")

        with pytest.raises(ValueError, match="Unsupported transport type"):
            get_transport()
