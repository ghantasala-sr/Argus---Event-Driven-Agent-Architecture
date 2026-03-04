"""Bedrock Converse API client for Argus agents.

Provides a unified interface to Amazon Bedrock models with:
- Model routing (agent → model ID mapping)
- Token tracking for cost monitoring
- Retry logic with exponential backoff
"""

import json
import logging
import os
import time
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Default model routing: agent name → Bedrock model ID
# Security/Performance need deep reasoning → Nova Pro
# Style needs fast classification → Nova Micro
# Parser/Test/Summary → Nova Lite (balanced)
DEFAULT_MODEL_MAP: dict[str, str] = {
    "security": "amazon.nova-pro-v1:0",
    "performance": "amazon.nova-pro-v1:0",
    "style": "amazon.nova-micro-v1:0",
    "test_coverage": "amazon.nova-lite-v1:0",
    "summary": "amazon.nova-lite-v1:0",
}

MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 1.0


class BedrockResponse:
    """Response from a Bedrock model invocation.

    Attributes:
        text: The model's text response.
        tokens_in: Number of input tokens consumed.
        tokens_out: Number of output tokens generated.
        latency_ms: Round-trip latency in milliseconds.
        model_id: Model ID that was invoked.
    """

    def __init__(
        self,
        text: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: int = 0,
        model_id: str = "",
    ) -> None:
        self.text = text
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.latency_ms = latency_ms
        self.model_id = model_id


class BedrockClient:
    """Client for Amazon Bedrock Converse API.

    Wraps the Bedrock Runtime client with model routing, token tracking,
    and retry logic. All agents should use this client instead of calling
    boto3 directly.

    Args:
        region: AWS region for Bedrock (default: from env or us-east-1).
        model_id: Override model ID (default: from MODEL_ID env var or routing map).
    """

    def __init__(
        self,
        region: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> None:
        self.region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        self.default_model_id = model_id or os.environ.get("MODEL_ID", "")
        self.client = boto3.client("bedrock-runtime", region_name=self.region)

    def invoke(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model_id: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> BedrockResponse:
        """Invoke a Bedrock model using the Converse API.

        Args:
            prompt: User message to send to the model.
            system_prompt: Optional system instructions.
            model_id: Override model ID for this call.
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature (lower = more deterministic).

        Returns:
            BedrockResponse with text, token counts, and latency.

        Raises:
            ClientError: If Bedrock API call fails after retries.
        """
        resolved_model = model_id or self.default_model_id
        if not resolved_model:
            raise ValueError(
                "No model_id specified. Set MODEL_ID env var or pass model_id."
            )

        messages = [
            {
                "role": "user",
                "content": [{"text": prompt}],
            }
        ]

        kwargs: dict[str, Any] = {
            "modelId": resolved_model,
            "messages": messages,
            "inferenceConfig": {
                "maxTokens": max_tokens,
                "temperature": temperature,
            },
        }

        if system_prompt:
            kwargs["system"] = [{"text": system_prompt}]

        # Retry with exponential backoff for throttling
        last_error: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            try:
                start = time.monotonic()
                response = self.client.converse(**kwargs)
                latency_ms = int((time.monotonic() - start) * 1000)

                # Extract response text
                output = response.get("output", {})
                message = output.get("message", {})
                content = message.get("content", [])
                text = content[0]["text"] if content else ""

                # Extract token usage
                usage = response.get("usage", {})
                tokens_in = usage.get("inputTokens", 0)
                tokens_out = usage.get("outputTokens", 0)

                logger.info(
                    "Bedrock %s: %d in / %d out tokens, %dms",
                    resolved_model,
                    tokens_in,
                    tokens_out,
                    latency_ms,
                )

                return BedrockResponse(
                    text=text,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    latency_ms=latency_ms,
                    model_id=resolved_model,
                )

            except ClientError as e:
                last_error = e
                error_code = e.response["Error"]["Code"]

                if error_code in ("ThrottlingException", "ServiceUnavailableException"):
                    wait = BASE_BACKOFF_SECONDS * (2**attempt)
                    logger.warning(
                        "Bedrock throttled (attempt %d/%d), retrying in %.1fs",
                        attempt + 1,
                        MAX_RETRIES,
                        wait,
                    )
                    time.sleep(wait)
                else:
                    raise

        # All retries exhausted
        raise last_error  # type: ignore[misc]

    @staticmethod
    def get_model_for_agent(agent_name: str) -> str:
        """Get the recommended model ID for a given agent.

        Args:
            agent_name: Agent name (e.g., 'security', 'style').

        Returns:
            Bedrock model ID string.
        """
        return DEFAULT_MODEL_MAP.get(agent_name, "amazon.nova-lite-v1:0")
