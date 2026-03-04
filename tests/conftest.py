"""Shared pytest fixtures for Argus tests.

Sets up mock AWS environment and common test utilities.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Generator

import boto3
import pytest
from moto import mock_aws

# Ensure agents/ is on the Python path
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def aws_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required AWS-related environment variables for all tests."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("STAGE", "test")
    monkeypatch.setenv("TRANSPORT_TYPE", "sqs")
    monkeypatch.setenv("DYNAMODB_TABLE", "argus-test-reviews")
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_SECRET", "test-secret-arn")


@pytest.fixture
def mock_aws_services() -> Generator[dict[str, Any], None, None]:
    """Start moto mock AWS services and create test resources.

    Yields:
        Dict with 'sqs', 'sns', 'dynamodb' clients and resource ARNs/URLs.
    """
    with mock_aws():
        region = "us-east-1"

        # Create SNS topics
        sns = boto3.client("sns", region_name=region)
        webhook_topic = sns.create_topic(Name="argus-test-pr-webhook")
        parsed_topic = sns.create_topic(Name="argus-test-pr-parsed")

        # Create SQS queues
        sqs = boto3.client("sqs", region_name=region)
        dlq = sqs.create_queue(QueueName="argus-test-parse-dlq")
        dlq_arn = sqs.get_queue_attributes(
            QueueUrl=dlq["QueueUrl"],
            AttributeNames=["QueueArn"],
        )["Attributes"]["QueueArn"]

        queue = sqs.create_queue(
            QueueName="argus-test-parse-queue",
            Attributes={
                "RedrivePolicy": json.dumps(
                    {"deadLetterTargetArn": dlq_arn, "maxReceiveCount": "3"}
                ),
            },
        )

        # Subscribe SQS to SNS
        sns.subscribe(
            TopicArn=webhook_topic["TopicArn"],
            Protocol="sqs",
            Endpoint=sqs.get_queue_attributes(
                QueueUrl=queue["QueueUrl"],
                AttributeNames=["QueueArn"],
            )["Attributes"]["QueueArn"],
        )

        # Create DynamoDB table
        dynamodb = boto3.client("dynamodb", region_name=region)
        dynamodb.create_table(
            TableName="argus-test-reviews",
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        yield {
            "sns": sns,
            "sqs": sqs,
            "dynamodb": dynamodb,
            "dynamodb_resource": boto3.resource("dynamodb", region_name=region),
            "webhook_topic_arn": webhook_topic["TopicArn"],
            "parsed_topic_arn": parsed_topic["TopicArn"],
            "queue_url": queue["QueueUrl"],
            "dlq_url": dlq["QueueUrl"],
        }


@pytest.fixture
def webhook_payload() -> dict[str, Any]:
    """Load the test webhook payload from fixtures."""
    with open(FIXTURES_DIR / "pr_webhook_event.json") as f:
        return json.load(f)


@pytest.fixture
def github_files_payload() -> list[dict[str, Any]]:
    """Load the test GitHub files response from fixtures."""
    with open(FIXTURES_DIR / "github_files.json") as f:
        return json.load(f)
