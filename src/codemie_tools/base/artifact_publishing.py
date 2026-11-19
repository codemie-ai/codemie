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

"""Types shared by artifact producers and the core publisher (no `codemie.*` imports allowed here)."""

from __future__ import annotations

import io
import re
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

# Zip-container formats. An LLM cannot emit a valid deflate stream token-by-token, so base64 that
# claims to be one of these is hallucinated far more often than it is real (EPMCDME-14735).
ZIP_CONTAINER_EXTENSIONS = frozenset({".docx", ".odp", ".ods", ".odt", ".pptx", ".xlsb", ".xlsm", ".xlsx", ".zip"})


@dataclass(frozen=True)
class PublishContext:
    user_id: str
    conversation_id: str | None
    turn_id: str

    @classmethod
    def for_turn(cls, user_id: str, conversation_id: str | None, request_uuid: str | None) -> PublishContext:
        """The one place a turn id is chosen: the request uuid, else a fresh hex id (workflows pass none).

        Without a request uuid each call gets its own `artifacts/<turn>/` folder, so one run's files scatter.
        """
        return cls(user_id, conversation_id, request_uuid or uuid.uuid4().hex)


@dataclass
class PublishResult:
    link: str | None = None
    name: str = ""
    size: int = 0
    mime: str | None = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.link is not None


class ArtifactPublisher(Protocol):
    @classmethod
    def __get_pydantic_core_schema__(cls, source: Any, handler: GetCoreSchemaHandler) -> core_schema.CoreSchema:
        # Lets pydantic tools declare this type on their fields; the publisher is a service, not data.
        return core_schema.any_schema()

    def publish(  # noqa: PLR0913 - keyword-only payload options of the publisher protocol
        self,
        ctx: PublishContext,
        name: str,
        *,
        content: bytes | str | None = None,
        path: str | None = None,
        mime: str | None = None,
        base64_content: bool = False,
    ) -> PublishResult: ...


_UNSAFE_NAME_CHARS = re.compile(r"[`\x00-\x1f\x7f]")


def strip_unsafe_name_chars(name: str) -> str:
    """Drop backticks and control characters: names are shown inside markdown code spans and link labels."""
    return _UNSAFE_NAME_CHARS.sub("", name)


def zip_container_error(name: str, data: bytes) -> str | None:
    """The reason `data` is refused for a zip-container `name`, else None.

    Structure only: `is_zipfile` reads the end-of-central-directory record and decompresses nothing,
    so it is instant even on a hostile archive. It catches base64 the model invented, which carries a
    plausible `PK` signature but no valid central directory.
    """
    extension = PurePosixPath(name).suffix.lower()
    if extension in ZIP_CONTAINER_EXTENSIONS and not zipfile.is_zipfile(io.BytesIO(data)):
        return f"its content is not a valid {extension} file"
    return None
