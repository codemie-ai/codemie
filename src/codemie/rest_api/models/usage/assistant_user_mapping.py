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

"""
Models for mapping assistant to tools and settings.
"""

from datetime import datetime, UTC
from typing import List, Dict, Optional
from uuid import uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Column, Index, Field as SQLField


from codemie.rest_api.models.base import BaseModelWithSQLSupport, CommonBaseModel, PydanticListType


class ToolConfig(CommonBaseModel):
    """Represents a single tool configuration"""

    name: str
    integration_id: str


class AssistantUserMappingBase(CommonBaseModel):
    """Base model for tracking assistant mappings to tools and settings"""

    id: str = SQLField(default_factory=lambda: str(uuid4()), primary_key=True)
    assistant_id: str = SQLField(index=True)
    user_id: str = SQLField(index=True)
    tools_config: List[ToolConfig] = SQLField(default_factory=list, sa_column=Column(PydanticListType(ToolConfig)))
    created_at: datetime = SQLField(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = SQLField(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint('assistant_id', 'user_id', name='uix_assistant_user_mapping'),
        Index('ix_assistant_user_mapping_assistant_id', 'assistant_id'),
        Index('ix_assistant_user_mapping_user_id', 'user_id'),
    )

    @classmethod
    def create_with_tools_config(
        cls, assistant_id: str, user_id: str, tools_config_list: List[Dict[str, str]]
    ) -> "AssistantUserMappingBase":
        """
        Create a new AssistantUserMappingBase instance with the given tools_config.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user
            tools_config_list: List of tool configurations

        Returns:
            New AssistantUserMappingBase instance
        """
        # Convert dictionaries to ToolConfig instances
        tool_configs = [ToolConfig(**config) for config in tools_config_list]

        instance = cls(assistant_id=assistant_id, user_id=user_id, tools_config=tool_configs)
        return instance


class AssistantUserMappingSQL(BaseModelWithSQLSupport, AssistantUserMappingBase, table=True):
    """SQLModel version of AssistantUserMapping for PostgreSQL storage"""

    __tablename__ = "assistant_user_mapping"


# Use the SQL implementation
AssistantUserMapping = AssistantUserMappingSQL


class AssistantMappingRequest(CommonBaseModel):
    """Request model for creating/updating assistant mappings"""

    tools_config: List[Dict[str, str]]


class AssistantMappingResponse(CommonBaseModel):
    """Response model for assistant mappings API"""

    id: str
    assistant_id: str
    user_id: str
    tools_config: List[Dict[str, str]]
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_db_model(cls, db_model: AssistantUserMappingBase) -> "AssistantMappingResponse":
        """
        Convert database model to API response model.

        Args:
            db_model: Database model instance

        Returns:
            API response model instance
        """
        # Convert ToolConfig instances to dictionaries
        tools_config_list = [
            {"name": config.name, "integration_id": config.integration_id} for config in db_model.tools_config
        ]

        return cls(
            id=db_model.id,
            assistant_id=db_model.assistant_id,
            user_id=db_model.user_id,
            tools_config=tools_config_list,
            created_at=db_model.created_at,
            updated_at=db_model.updated_at,
        )
