# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import io
from typing import Any, Protocol

from langchain_core.messages import AIMessage, HumanMessage
from openai import AzureOpenAI
from pydantic import BaseModel


class LiteLLMImageConfig(BaseModel):
    api_base: str
    api_key: str
    api_version: str
    model_id: str
    timeout: float = 60.0


def resolve_image_url(url: str) -> tuple[str | None, str | None]:
    """Return (url, b64_data). Splits inline data-URLs into raw b64."""
    if url.startswith("data:image"):
        return None, url.split(",", 1)[1]
    return url, None


class ImageGenerator(Protocol):
    @property
    def model_id(self) -> str | None:
        """Return the backing image model identifier when available."""
        ...

    def generate(
        self,
        prompt: str,
        size: str | None = None,
        output_format: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> tuple[str | None, str | None]:
        """Generate an image from a prompt. Returns (url, b64_data)."""
        ...

    def edit(
        self,
        prompt: str,
        image: bytes,
        mask: bytes | None = None,
        size: str | None = None,
        output_format: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> tuple[str | None, str | None]:
        """Edit or inpaint an image. Returns (url, b64_data)."""
        ...


def is_gemini_image_model(model_id: str | None) -> bool:
    normalized_model_id = (model_id or "").strip().lower()
    return "gemini" in normalized_model_id and "image" in normalized_model_id


class LiteLLMImageGenerator:
    """Calls an AzureOpenAI-compatible image endpoint for generations and edits."""

    def __init__(self, config: LiteLLMImageConfig) -> None:
        self._model_id = config.model_id
        self._client = AzureOpenAI(
            azure_endpoint=config.api_base,
            api_key=config.api_key,
            api_version=config.api_version,
            timeout=config.timeout,
        )

    @property
    def model_id(self) -> str:
        return self._model_id

    def generate(
        self,
        prompt: str,
        size: str | None = None,
        output_format: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> tuple[str | None, str | None]:
        request: dict[str, Any] = {"model": self._model_id, "prompt": prompt, "n": 1}
        if size:
            request["size"] = size
        if output_format:
            request["output_format"] = output_format
        if extra_body:
            request["extra_body"] = extra_body

        item = self._client.images.generate(**request).data[0]
        if item.url:
            return resolve_image_url(item.url)
        return None, item.b64_json

    def edit(
        self,
        prompt: str,
        image: bytes,
        mask: bytes | None = None,
        size: str | None = None,
        output_format: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> tuple[str | None, str | None]:
        image_file = ("image.png", io.BytesIO(image), "image/png")
        request: dict[str, Any] = {
            "model": self._model_id,
            "image": image_file,
            "prompt": prompt,
        }
        if mask is not None:
            request["mask"] = ("mask.png", io.BytesIO(mask), "image/png")
        if size:
            request["size"] = size
        if output_format:
            request["output_format"] = output_format
        if extra_body:
            request["extra_body"] = extra_body

        item = self._client.images.edit(**request).data[0]
        if item.url:
            return resolve_image_url(item.url)
        return None, item.b64_json


class ChatModelImageGenerator:
    """Calls ChatVertexAI (or any LangChain wrapper) via invoke() based on model."""

    def __init__(self, model: Any, model_id: str | None = None) -> None:
        self._model = model
        self._model_id = model_id or getattr(model, "model_name", None)

    @property
    def model_id(self) -> str | None:
        return self._model_id

    def generate(
        self,
        prompt: str,
        size: str | None = None,
        output_format: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> tuple[str | None, str | None]:
        del size, output_format, extra_body
        response: AIMessage = self._model.invoke([HumanMessage(content=prompt)])
        if isinstance(response.content, list):
            for part in response.content:
                if isinstance(part, dict):
                    if url := part.get("image_url", {}).get("url"):
                        return resolve_image_url(url)
                    if b64 := part.get("data"):
                        return None, b64
        return None, None

    def edit(
        self,
        prompt: str,
        image: bytes,
        mask: bytes | None = None,
        size: str | None = None,
        output_format: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> tuple[str | None, str | None]:
        del prompt, image, mask, size, output_format, extra_body
        raise NotImplementedError("This image generator does not support inpainting or image edits.")
