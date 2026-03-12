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

"""Models for custom node schema definitions.

This module defines models for custom node configuration schema retrieval,
used by the CustomNodeInfoService to document node parameters.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CustomNodeConfigField(BaseModel):
    """Schema field definition for a custom node configuration parameter.

    Attributes:
        type: Type string (limited to: 'str', 'text', 'int', 'float', 'bool', 'list', 'Any')
        required: Whether this parameter is required
        description: Human-readable description of the parameter
        values: Optional list of allowed values (for list type with enum values)
    """

    type: Literal["str", "text", "int", "float", "bool", "list"] = Field(..., description="Type of the parameter")
    required: bool = Field(..., description="Whether the parameter is required")
    description: str = Field(..., description="Description of the parameter")
    values: list[str] | None = Field(None, description="Allowed values for list type (optional)")


class CustomNodeSchemaResponse(BaseModel):
    """Response model for custom node configuration schema.

    Example:
        {
            "custom_node_type": "state_processor",
            "config_schema": {
                "state_schema": {
                    "type": "AgentMessages",
                    "required": true,
                    "description": "Parameter 'state_schema' of type AgentMessages"
                },
                "execution_context": {
                    "type": "dict",
                    "required": true,
                    "description": "Parameter 'execution_context' of type dict"
                }
            }
        }
    """

    custom_node_type: str = Field(..., description="The custom node type identifier")
    config_schema: dict[str, CustomNodeConfigField] = Field(
        ..., description="Configuration schema mapping parameter names to their definitions"
    )
