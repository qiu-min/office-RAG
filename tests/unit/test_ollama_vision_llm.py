"""Unit tests for the Ollama Vision LLM provider."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from src.libs.llm.base_llm import ChatResponse, Message
from src.libs.llm.base_vision_llm import ImageInput
from src.libs.llm.llm_factory import LLMFactory
from src.libs.llm.ollama_vision_llm import (
    OllamaVisionLLM,
    OllamaVisionLLMError,
)


# A valid 1x1 PNG keeps tests independent of a real image file or model.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class MockSettings:
    class LLMSettings:
        provider = "deepseek"
        model = "deepseek-v4-flash"
        temperature = 0.2
        max_tokens = 512

    class VisionSettings:
        enabled = True
        provider = "ollama"
        model = "qwen2.5vl:3b"
        max_image_size = 2048
        base_url = None

    def __init__(self, vision_llm=None):
        self.llm = self.LLMSettings()
        self.vision_llm = vision_llm or self.VisionSettings()


def make_response(
    content: str = "An image description.",
    model: str = "qwen2.5vl:3b",
    status_code: int = 200,
    prompt_eval_count: int = 12,
    eval_count: int = 8,
) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {
        "model": model,
        "message": {"role": "assistant", "content": content},
        "prompt_eval_count": prompt_eval_count,
        "eval_count": eval_count,
    }
    response.text = ""
    return response


class TestOllamaVisionInit:
    def test_reads_model_and_vision_configuration(self):
        vision = SimpleNamespace(
            enabled=True,
            provider="ollama",
            model="qwen2.5vl:3b",
            max_image_size=1024,
            base_url="http://vision-host:11434",
        )

        llm = OllamaVisionLLM(MockSettings(vision_llm=vision), timeout=45.0)

        assert llm.model == "qwen2.5vl:3b"
        assert llm.base_url == "http://vision-host:11434"
        assert llm.max_image_size == 1024
        assert llm.default_temperature == 0.2
        assert llm.default_max_tokens == 512
        assert llm.timeout == 45.0

    def test_default_base_url(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

        llm = OllamaVisionLLM(MockSettings())

        assert llm.base_url == "http://localhost:11434"

    def test_base_url_from_environment(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://remote:11434")

        llm = OllamaVisionLLM(MockSettings())

        assert llm.base_url == "http://remote:11434"


class TestOllamaVisionImageEncoding:
    @pytest.fixture
    def llm(self):
        return OllamaVisionLLM(MockSettings())

    def test_data_to_base64(self, llm):
        result = llm._get_image_base64(ImageInput(data=PNG_BYTES))

        assert base64.b64decode(result) == PNG_BYTES

    def test_base64_is_reused_without_data_url_prefix(self, llm):
        encoded = base64.b64encode(PNG_BYTES).decode("utf-8")

        result = llm._get_image_base64(ImageInput(base64=encoded))

        assert result == encoded
        assert not result.startswith("data:")

    def test_data_url_prefix_is_removed(self, llm):
        encoded = base64.b64encode(PNG_BYTES).decode("utf-8")

        result = llm._get_image_base64(
            ImageInput(base64=f"data:image/png;base64,{encoded}")
        )

        assert result == encoded

    def test_path_to_base64(self, llm, tmp_path):
        image_path = tmp_path / "test.png"
        image_path.write_bytes(PNG_BYTES)

        result = llm._get_image_base64(ImageInput(path=str(image_path)))

        assert base64.b64decode(result) == PNG_BYTES


class TestOllamaVisionChat:
    @pytest.fixture
    def llm(self):
        return OllamaVisionLLM(MockSettings())

    def test_payload_and_response(self, llm):
        mock_response = make_response()

        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.return_value = mock_response

            result = llm.chat_with_image(
                text="Describe this image.",
                image=ImageInput(data=PNG_BYTES),
            )

            payload = mock_client.return_value.__enter__.return_value.post.call_args.kwargs[
                "json"
            ]

        assert isinstance(result, ChatResponse)
        assert result.content == "An image description."
        assert result.model == "qwen2.5vl:3b"
        assert result.usage == {
            "prompt_tokens": 12,
            "completion_tokens": 8,
            "total_tokens": 20,
        }
        assert result.raw_response == mock_response.json.return_value
        assert payload["model"] == "qwen2.5vl:3b"
        assert payload["stream"] is False
        assert payload["messages"][-1]["role"] == "user"
        assert payload["messages"][-1]["content"] == "Describe this image."
        image_value = payload["messages"][-1]["images"][0]
        assert image_value == base64.b64encode(PNG_BYTES).decode("utf-8")
        assert not image_value.startswith("data:")
        assert payload["options"] == {"temperature": 0.2, "num_predict": 512}

    def test_history_is_converted_before_current_image_message(self, llm):
        mock_response = make_response()

        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.return_value = mock_response

            llm.chat_with_image(
                text="What does this show?",
                image=ImageInput(data=PNG_BYTES),
                messages=[
                    Message(role="system", content="You are concise."),
                    Message(role="user", content="Previous question."),
                    Message(role="assistant", content="Previous answer."),
                ],
            )

            payload = mock_client.return_value.__enter__.return_value.post.call_args.kwargs[
                "json"
            ]

        assert payload["messages"][:3] == [
            {"role": "system", "content": "You are concise."},
            {"role": "user", "content": "Previous question."},
            {"role": "assistant", "content": "Previous answer."},
        ]
        assert payload["messages"][-1]["images"]

    def test_missing_message_content_raises(self, llm):
        mock_response = make_response()
        mock_response.json.return_value = {"model": "qwen2.5vl:3b", "message": {}}

        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.return_value = mock_response

            with pytest.raises(OllamaVisionLLMError, match="message.content"):
                llm.chat_with_image("Describe it", ImageInput(data=PNG_BYTES))

    def test_http_error_raises(self, llm):
        response = make_response(status_code=404)
        response.json.return_value = {"error": "model not found"}
        response.text = "model not found"

        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.return_value = response

            with pytest.raises(OllamaVisionLLMError, match="HTTP 404"):
                llm.chat_with_image("Describe it", ImageInput(data=PNG_BYTES))

    def test_invalid_json_response_raises(self, llm):
        response = MagicMock()
        response.status_code = 200
        response.json.side_effect = ValueError("not json")
        response.text = "not json"

        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.return_value = response

            with pytest.raises(OllamaVisionLLMError, match="Invalid JSON"):
                llm.chat_with_image("Describe it", ImageInput(data=PNG_BYTES))

    def test_connection_error_raises(self, llm):
        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.side_effect = (
                httpx.ConnectError("connection refused")
            )

            with pytest.raises(OllamaVisionLLMError, match="Connection failed"):
                llm.chat_with_image("Describe it", ImageInput(data=PNG_BYTES))

    def test_timeout_error_raises(self, llm):
        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.side_effect = (
                httpx.TimeoutException("timeout")
            )

            with pytest.raises(OllamaVisionLLMError, match="timed out"):
                llm.chat_with_image("Describe it", ImageInput(data=PNG_BYTES))

    def test_posts_to_ollama_chat_endpoint(self, llm):
        mock_response = make_response()

        with patch("httpx.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.return_value = mock_response

            llm.chat_with_image("Describe it", ImageInput(data=PNG_BYTES))

            call_args = mock_client.return_value.__enter__.return_value.post.call_args

        assert call_args.args[0] == "http://localhost:11434/api/chat"


class TestOllamaVisionFactory:
    def test_factory_registers_and_creates_provider(self):
        provider_names = LLMFactory.list_vision_providers()
        assert "ollama" in provider_names

        provider = LLMFactory.create_vision_llm(MockSettings())

        assert isinstance(provider, OllamaVisionLLM)
        assert provider.model == "qwen2.5vl:3b"
