"""Transport abstraction layer for inter-agent communication.

CRITICAL DESIGN DECISION: Agents NEVER import SQS or Kafka directly.
All message passing goes through AgentTransport. The backend is selected
via TRANSPORT_TYPE environment variable.

Usage:
    from shared.transport import get_transport
    transport = get_transport()
    events = transport.consume()
    transport.publish(topic, event)
    transport.ack(receipt)
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any

import boto3

logger = logging.getLogger(__name__)


class AgentTransport(ABC):
    """Abstract interface for agent-to-agent messaging.

    Implementations must support consume (pull), publish (push), and
    acknowledge (mark processed) operations.
    """

    @abstractmethod
    def consume(self, max_messages: int = 1, wait_seconds: int = 5) -> list[dict[str, Any]]:
        """Pull messages from the agent's input queue.

        Args:
            max_messages: Maximum number of messages to receive (1-10).
            wait_seconds: Long-poll wait time in seconds (0-20).

        Returns:
            List of message dicts, each containing 'body' and 'receipt_handle'.
        """
        ...

    @abstractmethod
    def publish(self, topic: str, event: dict[str, Any]) -> str:
        """Publish an event to a topic for downstream agents.

        Args:
            topic: The SNS topic ARN or Kafka topic name.
            event: The event payload (will be JSON-serialized).

        Returns:
            Message ID from the publish operation.
        """
        ...

    @abstractmethod
    def ack(self, receipt_handle: str) -> None:
        """Acknowledge a message as successfully processed.

        Args:
            receipt_handle: The receipt handle from the consumed message.
        """
        ...


class SQSTransport(AgentTransport):
    """AWS SQS/SNS implementation of AgentTransport.

    Uses SQS for message consumption and SNS for publishing.
    Suitable for free-tier deployments.
    """

    def __init__(self, queue_url: str, region: str = "us-east-1") -> None:
        """Initialize SQS transport.

        Args:
            queue_url: URL of the SQS queue to consume from.
            region: AWS region for the SQS/SNS clients.
        """
        self.queue_url = queue_url
        self.sqs_client = boto3.client("sqs", region_name=region)
        self.sns_client = boto3.client("sns", region_name=region)

    def consume(self, max_messages: int = 1, wait_seconds: int = 5) -> list[dict[str, Any]]:
        """Pull messages from SQS queue with long-polling.

        Returns:
            List of dicts with 'body' (parsed JSON) and 'receipt_handle'.
        """
        response = self.sqs_client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=min(max_messages, 10),
            WaitTimeSeconds=wait_seconds,
            AttributeNames=["All"],
        )

        messages = []
        for msg in response.get("Messages", []):
            try:
                body = json.loads(msg["Body"])
            except json.JSONDecodeError:
                logger.error("Failed to parse SQS message body: %s", msg["Body"][:200])
                body = {"raw": msg["Body"]}

            messages.append(
                {
                    "body": body,
                    "receipt_handle": msg["ReceiptHandle"],
                    "message_id": msg["MessageId"],
                }
            )

        return messages

    def publish(self, topic: str, event: dict[str, Any]) -> str:
        """Publish an event to an SNS topic.

        Args:
            topic: SNS topic ARN.
            event: Event payload to publish as JSON.

        Returns:
            SNS MessageId.
        """
        response = self.sns_client.publish(
            TopicArn=topic,
            Message=json.dumps(event, default=str),
            MessageAttributes={
                "event_type": {
                    "DataType": "String",
                    "StringValue": event.get("event_type", "unknown"),
                }
            },
        )
        message_id = response["MessageId"]
        logger.info("Published to %s: %s", topic, message_id)
        return message_id

    def ack(self, receipt_handle: str) -> None:
        """Delete a message from SQS (acknowledging successful processing).

        Args:
            receipt_handle: SQS receipt handle from the consumed message.
        """
        self.sqs_client.delete_message(
            QueueUrl=self.queue_url,
            ReceiptHandle=receipt_handle,
        )
        logger.debug("Acknowledged message: %s", receipt_handle[:20])


def get_transport() -> AgentTransport:
    """Factory function to create the appropriate transport backend.

    Reads TRANSPORT_TYPE env var to select implementation.
    Currently supports: 'sqs' (default).
    Kafka support will be added in Phase 5.

    Returns:
        An AgentTransport implementation.

    Raises:
        ValueError: If TRANSPORT_TYPE is not supported.
        ValueError: If INPUT_QUEUE_URL is missing for SQS transport.
    """
    transport_type = os.environ.get("TRANSPORT_TYPE", "sqs").lower()

    if transport_type == "sqs":
        queue_url = os.environ.get("INPUT_QUEUE_URL", "")
        if not queue_url:
            raise ValueError("INPUT_QUEUE_URL environment variable is required for SQS transport")
        region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        return SQSTransport(queue_url=queue_url, region=region)
    else:
        raise ValueError(
            f"Unsupported transport type: '{transport_type}'. "
            f"Supported: 'sqs'. Kafka support coming in Phase 5."
        )
