"""Lambda handler for the LTM (Long-Term Memory) Writer."""

import json
import logging
import os
from typing import Any

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ.get("DYNAMODB_TABLE", "argus-reviews"))


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Process review.complete events to extract learning patterns."""
    for record in event.get("Records", []):
        body = record.get("body", "{}")
        try:
            envelope = json.loads(body)
            message_body = envelope.get("Message", body)
            review_complete = (
                json.loads(message_body) if isinstance(message_body, str) else message_body
            )
        except (json.JSONDecodeError, TypeError):
            review_complete = json.loads(body)

        review_id = review_complete.get("review_id", "")
        verdict = review_complete.get("verdict", "")
        latency = review_complete.get("latency_ms", 0)

        if not review_id:
            logger.warning("Missing review_id in event, skipping LTM extraction.")
            continue

        logger.info(
            "Extracting LTM patterns for review %s (Verdict: %s, Latency: %d ms)",
            review_id,
            verdict,
            latency,
        )

        # In Phase 4, we simply write a placeholder learning record to DynamoDB.
        # In Phase 5, this will scan the findings and learn team preferences.
        table.put_item(
            Item={
                "pk": "TEAM#global",
                "sk": f"LEARNING#{review_id}",
                "review_id": review_id,
                "verdict": verdict,
                "patterns_extracted": False,
            }
        )
        logger.info("Saved placeholder LTM learning record to DynamoDB.")

    return {"statusCode": 200}
