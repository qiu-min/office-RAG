"""Ollama Vision LLM implementation for local multimodal inference.

This module provides a local Vision LLM provider for Ollama models such as
``qwen2.5vl:3b``.  It uses Ollama's native ``/api/chat`` endpoint rather than
the OpenAI SDK or OpenAI-compatible protocol.
"""

from __future__ import annotations

import base64
import io
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.libs.llm.base_llm import ChatResponse, Message
from src.libs.llm.base_vision_llm import BaseVisionLLM, ImageInput


class OllamaVisionLLMError(RuntimeError):
    """Raised when an Ollama Vision API call fails."""


class OllamaVisionLLM(BaseVisionLLM):
    """Vision LLM provider backed by Ollama's native chat API.

    The provider accepts file paths, raw image bytes, and base64 image data.
    Ollama expects the image data in the ``messages[].images`` field as raw
    base64 without a ``data:*;base64,`` prefix.
    """

    DEFAULT_BASE_URL = "http://localhost:11434"
    DEFAULT_TIMEOUT = 120.0
    DEFAULT_MAX_IMAGE_SIZE = 2048

    def __init__(
        self,
        settings: Any,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        max_image_size: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the local Ollama Vision provider.

        Args:
            settings: Application settings containing ``vision_llm`` and
                ``llm`` configuration sections.
            base_url: Explicit Ollama URL. It takes precedence over the
                ``vision_llm.base_url`` setting, environment variables, and
                the default URL.
            timeout: HTTP timeout in seconds.
            max_image_size: Maximum image width/height used for optional
                local image resizing.
            **kwargs: Additional provider-specific overrides.
        """
        vision_settings = getattr(settings, "vision_llm", None)

        self.model = (
            getattr(vision_settings, "model", None)
            or getattr(getattr(settings, "llm", None), "model", None)
        )
        if not self.model:
            raise ValueError(
                "Ollama Vision model is not configured. Set vision_llm.model."
            )

        self.default_temperature = getattr(
            getattr(settings, "llm", None), "temperature", 0.0
        )
        self.default_max_tokens = getattr(
            getattr(settings, "llm", None), "max_tokens", 4096
        )

        configured_base_url = getattr(vision_settings, "base_url", None)
        self.base_url = (
            base_url
            or configured_base_url
            or os.environ.get("OLLAMA_BASE_URL")
            or self.DEFAULT_BASE_URL
        )

        configured_max_size = getattr(vision_settings, "max_image_size", None)
        self.max_image_size = (
            max_image_size
            or configured_max_size
            or self.DEFAULT_MAX_IMAGE_SIZE
        )
        self.timeout = timeout or self.DEFAULT_TIMEOUT
        self._extra_config = kwargs

    def chat_with_image(
        self,
        text: str,
        image: ImageInput,
        messages: Optional[list[Message]] = None,
        trace: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """Generate a response from a text prompt and an image."""
        self.validate_text(text)
        self.validate_image(image)

        processed_image = self.preprocess_image(
            image,
            max_size=(self.max_image_size, self.max_image_size),
        )
        image_base64 = self._get_image_base64(processed_image)

        temperature = kwargs.get("temperature", self.default_temperature)
        max_tokens = kwargs.get("max_tokens", self.default_max_tokens)
        model = kwargs.get("model", self.model)

        api_messages: List[Dict[str, Any]] = []
        if messages:
            api_messages.extend(self._convert_messages(messages))

        # The current prompt is always the final user message, so the current
        # image is attached to the last user message sent to Ollama.
        api_messages.append({
            "role": "user",
            "content": text,
            "images": [image_base64],
        })

        try:
            response_data = self._call_api(
                messages=api_messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=kwargs.get("timeout", self.timeout),
            )

            message = response_data.get("message")
            if not isinstance(message, dict):
                raise OllamaVisionLLMError(
                    "[Ollama Vision] Unexpected response format: missing 'message'"
                )

            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                raise OllamaVisionLLMError(
                    "[Ollama Vision] Unexpected response format: "
                    "missing 'message.content'"
                )

            usage = self._build_usage(response_data)
            return ChatResponse(
                content=content,
                model=response_data.get("model", model),
                usage=usage,
                raw_response=response_data,
            )
        except OllamaVisionLLMError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise OllamaVisionLLMError(
                f"[Ollama Vision] Invalid response format: {exc}"
            ) from exc
        except Exception as exc:
            raise OllamaVisionLLMError(
                f"[Ollama Vision] API call failed: {type(exc).__name__}: {exc}"
            ) from exc

    def preprocess_image(
        self,
        image: ImageInput,
        max_size: Optional[tuple[int, int]] = None,
    ) -> ImageInput:
        """Resize an oversized local image when Pillow is available.

        Ollama itself accepts the image bytes, but applying the configured
        limit keeps large PDF screenshots from producing unnecessarily large
        requests. Base64 input is preserved as-is because its source format
        is not reliably available without decoding and re-encoding it.
        """
        if not max_size or image.base64:
            return image

        try:
            from PIL import Image
        except ImportError:
            return image

        try:
            if image.data is not None:
                image_bytes = image.data
            elif image.path is not None:
                image_bytes = Path(image.path).read_bytes()
            else:
                return image

            img = Image.open(io.BytesIO(image_bytes))
            width, height = img.size
            max_width, max_height = max_size
            if width <= max_width and height <= max_height:
                return image

            ratio = min(max_width / width, max_height / height)
            new_size = (int(width * ratio), int(height * ratio))
            resized = img.resize(new_size, Image.Resampling.LANCZOS)

            output_format = img.format or "PNG"
            buffer = io.BytesIO()
            resized.save(buffer, format=output_format)
            return ImageInput(data=buffer.getvalue(), mime_type=image.mime_type)
        except Exception as exc:
            raise OllamaVisionLLMError(
                f"[Ollama Vision] Failed to preprocess image: {exc}"
            ) from exc

    def _get_image_base64(self, image: ImageInput) -> str:
        """Convert an ImageInput to raw base64 for Ollama."""
        try:
            if image.base64 is not None:
                encoded = image.base64
            elif image.data is not None:
                encoded = base64.b64encode(image.data).decode("utf-8")
            elif image.path is not None:
                encoded = base64.b64encode(
                    Path(image.path).read_bytes()
                ).decode("utf-8")
            else:
                raise ValueError("ImageInput has no valid data source")

            # Be tolerant of callers passing a data URL while still honoring
            # Ollama's requirement that images contain raw base64 only.
            if encoded.startswith("data:") and "," in encoded:
                encoded = encoded.split(",", 1)[1]
            return encoded
        except OllamaVisionLLMError:
            raise
        except Exception as exc:
            raise OllamaVisionLLMError(
                f"[Ollama Vision] Failed to encode image: {exc}"
            ) from exc

    def _convert_messages(self, messages: list[Message]) -> List[Dict[str, str]]:
        """Convert project Message objects to Ollama text messages."""
        valid_roles = {"system", "user", "assistant"}
        converted: List[Dict[str, str]] = []
        for index, message in enumerate(messages):
            if not isinstance(message, Message):
                raise ValueError(
                    f"Message at index {index} is not a Message instance"
                )
            if message.role not in valid_roles:
                raise ValueError(
                    f"Message at index {index} has invalid role '{message.role}'"
                )
            if not message.content or not message.content.strip():
                raise ValueError(
                    f"Message at index {index} has empty content"
                )
            converted.append({
                "role": message.role,
                "content": message.content,
            })
        return converted

    def _build_usage(self, response_data: Dict[str, Any]) -> Optional[Dict[str, int]]:
        """Map Ollama evaluation counters to the common usage structure."""
        prompt_count = response_data.get("prompt_eval_count")
        completion_count = response_data.get("eval_count")
        if prompt_count is None and completion_count is None:
            return None

        prompt_tokens = int(prompt_count or 0)
        completion_tokens = int(completion_count or 0)
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }

    def _call_api(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        temperature: float,
        max_tokens: int,
        timeout: float,
    ) -> Dict[str, Any]:
        """Call Ollama's native non-streaming chat endpoint."""
        import httpx

        url = f"{self.base_url.rstrip('/')}/api/chat"
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )

                if response.status_code != 200:
                    error_detail = self._parse_error_response(response)
                    raise OllamaVisionLLMError(
                        f"[Ollama Vision] API error (HTTP {response.status_code}): "
                        f"{error_detail}"
                    )

                try:
                    data = response.json()
                except (ValueError, TypeError) as exc:
                    raise OllamaVisionLLMError(
                        "[Ollama Vision] Invalid JSON response from Ollama"
                    ) from exc

                if not isinstance(data, dict):
                    raise OllamaVisionLLMError(
                        "[Ollama Vision] Invalid JSON response: expected an object"
                    )
                return data
        except OllamaVisionLLMError:
            raise
        except httpx.TimeoutException as exc:
            raise OllamaVisionLLMError(
                f"[Ollama Vision] Request timed out after {timeout} seconds. "
                "Consider increasing timeout for local vision inference."
            ) from exc
        except httpx.ConnectError as exc:
            raise OllamaVisionLLMError(
                "[Ollama Vision] Connection failed. Ensure Ollama is running "
                "locally (try 'ollama serve')."
            ) from exc
        except httpx.RequestError as exc:
            raise OllamaVisionLLMError(
                f"[Ollama Vision] Request failed: {type(exc).__name__}"
            ) from exc

    def _parse_error_response(self, response: Any) -> str:
        """Extract a concise error message from an Ollama response."""
        try:
            error_data = response.json()
            if isinstance(error_data, dict) and "error" in error_data:
                return str(error_data["error"])
            return response.text[:200] if response.text else "Unknown error"
        except Exception:
            return response.text[:200] if getattr(response, "text", None) else "Unknown error"

