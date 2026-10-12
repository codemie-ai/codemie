# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

"""Read-only Exchange tools. ``execute`` raises typed ExchangeError directly (no wrapping)."""

from __future__ import annotations

import json
from typing import Any, Callable, Type

from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.utils.image_processor import ImageProcessor
from codemie_tools.ms_exchange.client_base import MAX_PAGE
from codemie_tools.ms_exchange.enrichment import enrich
from codemie_tools.ms_exchange.errors import OcrModelUnavailableError
from codemie_tools.ms_exchange.client_factory import get_client
from codemie_tools.ms_exchange.models import ExchangeToolConfig
from codemie_tools.ms_exchange.tools_vars import (
    EXCHANGE_ENRICH_BODY_TOOL,
    EXCHANGE_READ_TOOL,
    EXCHANGE_RETRIEVE_BY_ID_TOOL,
    EXCHANGE_RETRIEVE_FOLDERS_TOOL,
)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class _ExchangeReadOnlyTool(CodeMieTool):
    config: ExchangeToolConfig

    def is_safe(self, args: dict) -> bool:
        return True


class RetrieveFoldersInput(BaseModel):
    pass


class ReadInput(BaseModel):
    folder: str = Field(default="Inbox", description="Folder name or well-known name (default Inbox)")
    start: int = Field(default=0, ge=0, description="Zero-based offset of the first message")
    count: int = Field(default=10, ge=1, le=MAX_PAGE, description=f"Number of messages (max {MAX_PAGE})")


class RetrieveByIdInput(BaseModel):
    message_id: str = Field(description="Id of the message to retrieve")


class RetrieveFoldersTool(_ExchangeReadOnlyTool):
    name: str = EXCHANGE_RETRIEVE_FOLDERS_TOOL.name
    description: str = EXCHANGE_RETRIEVE_FOLDERS_TOOL.description
    args_schema: Type[BaseModel] = RetrieveFoldersInput

    def execute(self) -> str:
        return _dump(get_client(self.config).list_folders())


class ReadTool(_ExchangeReadOnlyTool):
    name: str = EXCHANGE_READ_TOOL.name
    description: str = EXCHANGE_READ_TOOL.description
    args_schema: Type[BaseModel] = ReadInput

    def execute(self, folder: str = "Inbox", start: int = 0, count: int = 10) -> str:
        return _dump(get_client(self.config).list_messages(folder, start, count))


class RetrieveByIdTool(_ExchangeReadOnlyTool):
    name: str = EXCHANGE_RETRIEVE_BY_ID_TOOL.name
    description: str = EXCHANGE_RETRIEVE_BY_ID_TOOL.description
    args_schema: Type[BaseModel] = RetrieveByIdInput

    def execute(self, message_id: str) -> str:
        return _dump(get_client(self.config).get_message(message_id))


class EnrichBodyInput(BaseModel):
    message_id: str = Field(description="Id of the message to enrich")
    ocr_model: str | None = Field(
        default=None,
        description="Optional multimodal model name used to OCR images; defaults to the assistant's chat model",
    )


class EnrichBodyTool(_ExchangeReadOnlyTool):
    """Body plus extracted attachment text.

    ``chat_model`` is injected by the assistant runtime. ``ocr_model_factory`` (name -> chat model) must be
    injected by the host application, because building a model lives in ``codemie`` and this package must not
    import from it. Without a factory, ``ocr_model`` is ignored in favour of ``chat_model``.
    """

    name: str = EXCHANGE_ENRICH_BODY_TOOL.name
    description: str = EXCHANGE_ENRICH_BODY_TOOL.description
    args_schema: Type[BaseModel] = EnrichBodyInput
    chat_model: Any | None = None
    ocr_model_factory: Callable[[str], Any] | None = None

    def _build_model(self, name: str) -> Any | None:
        """None on any failure; the caller raises outside the handler so no cause or context reaches the logs."""
        try:
            return self.ocr_model_factory(name)  # type: ignore[misc]
        except Exception:  # noqa: BLE001 - provider text must not surface
            return None

    def _resolve_ocr(self, ocr_model: str | None) -> Callable[[bytes], str] | None:
        model = self.chat_model
        if ocr_model and self.ocr_model_factory is not None:
            model = self._build_model(ocr_model)
            if model is None:
                raise OcrModelUnavailableError
        if model is None:
            return None
        return ImageProcessor(chat_model=model).extract_text_from_image_bytes

    def execute(self, message_id: str, ocr_model: str | None = None) -> str:
        client = get_client(self.config)
        body = client.get_text_body(message_id)
        attachments = []
        for att in client.list_attachments(message_id):
            if att.get("is_file"):
                content = lambda att_id=att["id"]: client.get_attachment_bytes(message_id, att_id)  # noqa: E731
            else:
                content = None
            attachments.append((att["name"], att.get("content_type", ""), content, att.get("size")))
        return enrich(body, attachments, self._resolve_ocr(ocr_model))
