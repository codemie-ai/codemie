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

from typing import Literal

from pydantic import BaseModel, Field

from codemie.core.models import BaseResponse


class DisabledMcpServer(BaseModel):
    """An MCP link that was disabled on restore because its catalogue entry is unavailable."""

    name: str
    mcp_config_id: str
    reason: Literal["missing", "inactive", "not_public"]
    address: str | None = None


class DisabledMcpResponse(BaseResponse):
    """Response of a restore that disabled some MCP links."""

    disabled_mcp_servers: list[DisabledMcpServer] = Field(default_factory=list)


def message_with_disabled(message: str, disabled: list[DisabledMcpServer]) -> str:
    """`message` plus the names of the disabled MCP servers, for clients that ignore the list field."""
    if not disabled:
        return message
    names = ", ".join(dict.fromkeys(item.name for item in disabled))
    return f"{message} Disabled MCP servers that are unavailable: {names}."
