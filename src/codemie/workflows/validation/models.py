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

"""
Data models for workflow validation errors.

Contains Pydantic models and dataclasses used across validation modules.
"""

from collections import namedtuple
from enum import Enum
from typing import Optional, Union
from pydantic import BaseModel, Field


CrossRefError = namedtuple("CrossRefError", ["referrer", "key", "ref", "entity"])


class ToolkitType(str, Enum):
    """Enum for toolkit types."""

    TOOLS = "tools"
    EXTERNAL_TOOLS = "external-tools"


class ToolMeta(BaseModel):
    """Metadata for tool-related errors."""

    toolkit_type: str = Field(
        description=f"Type of toolkit: '{ToolkitType.TOOLS.value}' or '{ToolkitType.EXTERNAL_TOOLS.value}'"
    )
    toolkit_name: str = Field(description="Name of the toolkit")
    tool_name: str = Field(description="Name of the tool")


class MCPMeta(BaseModel):
    """Metadata for MCP server-related errors."""

    mcp_name: str = Field(description="Name of the MCP server")


class WorkflowValidationErrorDetail(BaseModel):
    """Pydantic model for structured validation error details."""

    id: str = Field(description="Unique UUID for this error")
    message: str = Field(description="Short, actionable error message")
    path: str = Field(description="Field path (leaf field name)")
    details: Optional[str] = Field(None, description="Optional detailed explanation")
    state_id: Optional[str] = Field(None, description="State ID for node-specific errors")
    config_line: Optional[int] = Field(None, description="1-indexed YAML line number")
    meta: Optional[Union[ToolMeta, MCPMeta]] = Field(None, description="Additional metadata (tool or MCP info)")

    class Config:
        populate_by_name = True
