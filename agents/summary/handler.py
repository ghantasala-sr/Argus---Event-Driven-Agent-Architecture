"""Lambda handler for the Summary Agent."""

import json
import logging
import os
from typing import Any

import boto3
from shared.github_client import GitHubClient
from shared.transport import get_transport
from summary.agent import SummaryAgent

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ.get("DYNAMODB_TABLE", "argus-reviews"))
sns_client = boto3.client("sns")
transport = get_transport()

# Initialize GitHub Client
app_id = os.environ.get("GITHUB_APP_ID", "")
secret_arn = os.environ.get("GITHUB_PRIVATE_KEY_SECRET", "")
github_private_key = ""

if secret_arn:
    secrets_client = boto3.client("secretsmanager")
    secret_response = secrets_client.get_secret_value(SecretId=secret_arn)
    github_private_key = secret_response.get("SecretString", "")

github_client = GitHubClient(
    app_id=app_id,
    private_key=github_private_key,
)

agent = SummaryAgent(
    dynamodb_table=table,
    github_client=github_client,
)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Process summary requests from the review findings topic."""
    topic_arn = os.environ.get("REVIEW_COMPLETE_TOPIC_ARN", "")
    if not topic_arn:
        logger.error("REVIEW_COMPLETE_TOPIC_ARN not set")
        raise ValueError("REVIEW_COMPLETE_TOPIC_ARN environment variable required")

    for record in event.get("Records", []):
        body = record.get("body", "{}")
        try:
            envelope = json.loads(body)
            message_body = envelope.get("Message", body)
            finding_event = (
                json.loads(message_body) if isinstance(message_body, str) else message_body
            )
        except (json.JSONDecodeError, TypeError):
            finding_event = json.loads(body)

        # The summary agent is triggered whenever a review finding is published.
        # To avoid running the summary agent 4 times in parallel and posting 4 comments,
        # we can use the finding's `review_id` to poll the DB.
        # However, a better event-driven approach is waiting until a specific timeout message
        # or waiting inside the handler for X seconds for completion.

        review_id = finding_event.get("review_id", "")
        repo_full_name = finding_event.get("repo_full_name", "")
        pr_number = finding_event.get("pr_number", 0)
        head_sha = finding_event.get("head_sha", "")

        if not review_id:
            logger.warning("Missing review_id in event, skipping.")
            continue

        # We will poll for remaining events up to 15 seconds.
        logger.info("Starting summary aggregation for PR #%s", pr_number)

        # NOTE: Ideally we debounce this. For this implementation, we will just run the
        # wait block. If multiple handlers spin up, they'll post multiple comments.
        # In a real system we would use a DynamoDB lock or Step Functions.

        summary_result = agent.process(
            review_id=review_id,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            head_sha=head_sha,
            expected_agents=4,
            timeout_seconds=15,  # Shorter timeout for AWS Lambda bounds
        )

        # Emit completion event for LTM and Dashboard
        sns_client.publish(
            TopicArn=topic_arn,
            Message=json.dumps(summary_result),
            MessageAttributes={
                "event_type": {"DataType": "String", "StringValue": "review.complete"},
            },
        )
        logger.info("Published review.complete for PR #%s", pr_number)

    return {"statusCode": 200}
