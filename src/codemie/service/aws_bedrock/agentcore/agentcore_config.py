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

import json
from typing import Any, Optional

from pydantic import BaseModel, field_validator, model_validator

from codemie.configs import logger


class AgentcoreReasoningConfig(BaseModel):
    """Paths used to extract thought content from each response body or SSE chunk."""

    thoughts_path: Optional[str] = None  # dot-notation path to the thoughts array in the response body
    text_path: str
    name_path: Optional[str] = None
    args_path: Optional[str] = None
    active_path: Optional[str] = None  # streaming only — boolean field per chunk indicating thought in-progress


class AgentcoreOutputConfig(BaseModel):
    """Paths used to extract the answer text (and optionally thoughts) from a response."""

    text_path: str
    reasoning: Optional[AgentcoreReasoningConfig] = None


class AgentcoreResponseConfig(BaseModel):
    """Inbound config: tells the runtime service what to extract from the AgentCore response."""

    streaming: bool = False
    body: Optional[AgentcoreOutputConfig] = None  # required when streaming=False
    chunk: Optional[AgentcoreOutputConfig] = None  # required when streaming=True

    @model_validator(mode="after")
    def _validate_body_or_chunk(self) -> "AgentcoreResponseConfig":
        if not self.streaming and self.body is None:
            raise ValueError("body is required when streaming is False")
        if self.streaming and self.chunk is None:
            raise ValueError("chunk is required when streaming is True")
        return self

    @classmethod
    def parse_json(cls, raw: Optional[str]) -> Optional["AgentcoreResponseConfig"]:
        """Return AgentcoreResponseConfig for structured-format JSON, or None for legacy/empty input."""
        if not raw:
            return None
        try:
            data = json.loads(raw)
            if "response" not in data:
                return None
            return cls.model_validate(data["response"])
        except Exception as e:
            logger.debug("Failed to parse AgentCore response configuration: %s", e)
            return None


class AgentcoreHistoryConfig(BaseModel):
    """Outbound config: controls how prior conversation turns are serialised into the request payload."""

    history_path: str  # dot-notation path where the turns array is placed, e.g. "messages"
    role_path: str = "role"  # field name for the role within each turn object
    message_path: str = "content"  # field name for the text within each turn object
    user_role: str = "user"  # role label emitted for ChatRole.USER turns
    assistant_role: str = "assistant"  # role label emitted for all non-user turns


class AgentcoreRequestConfig(BaseModel):
    """Outbound config: controls how the user query and history are placed in the request payload."""

    message_path: str = "message"  # dot-notation path for the current user query
    history: Optional[AgentcoreHistoryConfig] = None  # omit to never send history
    extra_payload: Optional[dict[str, Any]] = None  # static fields merged into every request

    @field_validator("extra_payload", mode="before")
    @classmethod
    def _validate_extra_payload(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
            except json.JSONDecodeError as exc:
                raise ValueError(f"extra_payload must be valid JSON: {exc}") from exc

            if not isinstance(parsed, dict):
                raise ValueError("extra_payload must be a JSON object, not an array or scalar")
            return parsed
        if not isinstance(v, dict):
            raise ValueError("extra_payload must be a JSON object")
        return v

    @classmethod
    def from_json(cls, raw: Optional[str]) -> "AgentcoreRequestConfig":
        """Return AgentcoreRequestConfig parsed from JSON, falling back to defaults when absent."""
        if not raw:
            return cls()
        try:
            data = json.loads(raw)
            return cls.model_validate(data.get("request", {}))
        except Exception as e:
            logger.debug("Failed to parse AgentCore request configuration: %s", e)
            return cls()
