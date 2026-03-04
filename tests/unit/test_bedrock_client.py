"""Unit tests for the Bedrock client."""

from unittest.mock import MagicMock, patch

import pytest

from shared.bedrock_client import BedrockClient, BedrockResponse


class TestBedrockResponse:
    """Tests for BedrockResponse."""

    def test_default_values(self):
        r = BedrockResponse(text="hello")
        assert r.text == "hello"
        assert r.tokens_in == 0
        assert r.tokens_out == 0
        assert r.latency_ms == 0
        assert r.model_id == ""

    def test_full_values(self):
        r = BedrockResponse(
            text="result", tokens_in=100, tokens_out=50, latency_ms=200, model_id="nova-pro"
        )
        assert r.text == "result"
        assert r.tokens_in == 100
        assert r.tokens_out == 50
        assert r.latency_ms == 200
        assert r.model_id == "nova-pro"


class TestBedrockClientInit:
    """Tests for BedrockClient initialization."""

    @patch("shared.bedrock_client.boto3")
    def test_init_defaults(self, mock_boto):
        client = BedrockClient()
        assert client.region == "us-east-1"
        assert client.default_model_id == ""

    @patch("shared.bedrock_client.boto3")
    def test_init_with_params(self, mock_boto):
        client = BedrockClient(region="us-west-2", model_id="amazon.nova-pro-v1:0")
        assert client.region == "us-west-2"
        assert client.default_model_id == "amazon.nova-pro-v1:0"


class TestBedrockClientInvoke:
    """Tests for BedrockClient.invoke()."""

    @patch("shared.bedrock_client.boto3")
    def test_invoke_success(self, mock_boto):
        mock_client = MagicMock()
        mock_boto.client.return_value = mock_client

        mock_client.converse.return_value = {
            "output": {
                "message": {
                    "content": [{"text": '{"findings": []}'}],
                }
            },
            "usage": {
                "inputTokens": 150,
                "outputTokens": 25,
            },
        }

        client = BedrockClient(model_id="amazon.nova-pro-v1:0")
        response = client.invoke("Review this code")

        assert response.text == '{"findings": []}'
        assert response.tokens_in == 150
        assert response.tokens_out == 25
        assert response.latency_ms >= 0
        assert response.model_id == "amazon.nova-pro-v1:0"

    @patch("shared.bedrock_client.boto3")
    def test_invoke_with_system_prompt(self, mock_boto):
        mock_client = MagicMock()
        mock_boto.client.return_value = mock_client

        mock_client.converse.return_value = {
            "output": {"message": {"content": [{"text": "ok"}]}},
            "usage": {"inputTokens": 50, "outputTokens": 10},
        }

        client = BedrockClient(model_id="amazon.nova-pro-v1:0")
        client.invoke("test", system_prompt="You are a security expert")

        call_kwargs = mock_client.converse.call_args[1]
        assert "system" in call_kwargs
        assert call_kwargs["system"] == [{"text": "You are a security expert"}]

    @patch("shared.bedrock_client.boto3")
    def test_invoke_no_model_raises(self, mock_boto):
        client = BedrockClient()
        with pytest.raises(ValueError, match="No model_id specified"):
            client.invoke("test")

    @patch("shared.bedrock_client.boto3")
    def test_invoke_empty_response(self, mock_boto):
        mock_client = MagicMock()
        mock_boto.client.return_value = mock_client

        mock_client.converse.return_value = {
            "output": {"message": {"content": []}},
            "usage": {"inputTokens": 10, "outputTokens": 0},
        }

        client = BedrockClient(model_id="amazon.nova-micro-v1:0")
        response = client.invoke("test")
        assert response.text == ""


class TestModelRouting:
    """Tests for model routing logic."""

    def test_security_uses_nova_pro(self):
        model = BedrockClient.get_model_for_agent("security")
        assert "nova-pro" in model

    def test_style_uses_nova_micro(self):
        model = BedrockClient.get_model_for_agent("style")
        assert "nova-micro" in model

    def test_unknown_defaults_to_lite(self):
        model = BedrockClient.get_model_for_agent("unknown_agent")
        assert "nova-lite" in model

    def test_performance_uses_nova_pro(self):
        model = BedrockClient.get_model_for_agent("performance")
        assert "nova-pro" in model
