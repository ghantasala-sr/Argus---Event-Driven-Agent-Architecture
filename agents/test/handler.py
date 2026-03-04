"""Lambda handler for the Test Agent."""

import json
import logging
import os
from typing import Any

import boto3

from shared.bedrock_client import BedrockClient
from shared.models import ParsedPREvent, SecurityReviewEvent
from test.agent import TestAgent

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

sns_client = boto3.client("sns")
bedrock = BedrockClient(model_id=os.environ.get("MODEL_ID", "amazon.nova-micro-v1:0"))
agent = TestAgent(
    bedrock_client=bedrock,
    dynamodb_table=os.environ.get("DYNAMODB_TABLE"),
)

def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    topic_arn = os.environ.get("REVIEW_FINDINGS_TOPIC_ARN", "")
    if not topic_arn:
        logger.error("REVIEW_FINDINGS_TOPIC_ARN not set")
        raise ValueError("REVIEW_FINDINGS_TOPIC_ARN environment variable required")

    for record in event.get("Records", []):
        body = record.get("body", "{}")
        try:
            envelope = json.loads(body)
            message_body = envelope.get("Message", body)
            parsed_data = json.loads(message_body) if isinstance(message_body, str) else message_body
        except (json.JSONDecodeError, TypeError):
            parsed_data = json.loads(body)

        parsed_event = ParsedPREvent.model_validate(parsed_data)
        logger.info("Processing test logic review for PR #%d in %s", parsed_event.pr_number, parsed_event.repo_full_name)

        test_event: SecurityReviewEvent = agent.process(parsed_event)

        sns_client.publish(
            TopicArn=topic_arn,
            Message=test_event.model_dump_json(),
            MessageAttributes={
                "event_type": {"DataType": "String", "StringValue": "review.test"},
                "agent": {"DataType": "String", "StringValue": "test"},
            },
        )
        logger.info("Published %d test findings for PR #%d", len(test_event.findings), parsed_event.pr_number)

    return {"statusCode": 200}
