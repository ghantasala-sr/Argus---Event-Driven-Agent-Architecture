"""Lambda handler for the Security Agent.

Triggered by SQS messages from the security-queue. Deserializes the
ParsedPREvent, runs the SecurityAgent, and publishes findings to SNS.
"""

import json
import logging
import os
from typing import Any

import boto3
from security.agent import SecurityAgent
from shared.bedrock_client import BedrockClient
from shared.models import ParsedPREvent, SecurityReviewEvent

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Initialize clients outside handler for Lambda reuse
sns_client = boto3.client("sns")
bedrock = BedrockClient(
    model_id=os.environ.get("MODEL_ID", "amazon.nova-pro-v1:0"),
)
agent = SecurityAgent(
    bedrock_client=bedrock,
    dynamodb_table=os.environ.get("DYNAMODB_TABLE"),
)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for security review.

    Processes SQS records containing ParsedPREvent messages, runs the
    security agent, and publishes SecurityReviewEvent to SNS.

    Args:
        event: SQS event with Records array.
        context: Lambda context (unused).

    Returns:
        Dict with statusCode 200.
    """
    topic_arn = os.environ.get("REVIEW_FINDINGS_TOPIC_ARN", "")
    if not topic_arn:
        logger.error("REVIEW_FINDINGS_TOPIC_ARN not set")
        raise ValueError("REVIEW_FINDINGS_TOPIC_ARN environment variable required")

    for record in event.get("Records", []):
        body = record.get("body", "{}")

        # SQS messages from SNS have the actual message nested in "Message"
        try:
            envelope = json.loads(body)
            message_body = envelope.get("Message", body)
            if isinstance(message_body, str):
                parsed_data = json.loads(message_body)
            else:
                parsed_data = message_body
        except (json.JSONDecodeError, TypeError):
            parsed_data = json.loads(body)

        parsed_event = ParsedPREvent.model_validate(parsed_data)

        logger.info(
            "Processing security review for PR #%d in %s",
            parsed_event.pr_number,
            parsed_event.repo_full_name,
        )

        # Run security analysis
        security_event: SecurityReviewEvent = agent.process(parsed_event)

        # Publish findings to SNS
        sns_client.publish(
            TopicArn=topic_arn,
            Message=security_event.model_dump_json(),
            MessageAttributes={
                "event_type": {
                    "DataType": "String",
                    "StringValue": "review.security",
                },
                "agent": {
                    "DataType": "String",
                    "StringValue": "security",
                },
            },
        )

        logger.info(
            "Published %d security findings for PR #%d",
            len(security_event.findings),
            parsed_event.pr_number,
        )

    return {"statusCode": 200}
