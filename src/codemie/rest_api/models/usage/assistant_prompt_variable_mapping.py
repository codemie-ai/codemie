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
Models for mapping assistant prompt variables to users.
"""

from datetime import datetime, UTC
from typing import List, Dict, Optional
from uuid import uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Column, Index, Field as SQLField


from codemie.rest_api.models.base import BaseModelWithSQLSupport, CommonBaseModel, PydanticListType


class PromptVariableConfig(CommonBaseModel):
    """Represents a single prompt variable configuration"""

    variable_key: str
    variable_value: str
    is_sensitive: Optional[bool] = False


class AssistantPromptVariableMappingBase(CommonBaseModel):
    """Base model for tracking assistant prompt variable mappings for users"""

    id: str = SQLField(default_factory=lambda: str(uuid4()), primary_key=True)
    assistant_id: str = SQLField(index=True)
    user_id: str = SQLField(index=True)
    variables_config: List[PromptVariableConfig] = SQLField(
        default_factory=list, sa_column=Column(PydanticListType(PromptVariableConfig))
    )
    created_at: datetime = SQLField(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = SQLField(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint('assistant_id', 'user_id', name='uix_assistant_prompt_variable_mapping'),
        Index('ix_assistant_prompt_variable_mapping_assistant_id', 'assistant_id'),
        Index('ix_assistant_prompt_variable_mapping_user_id', 'user_id'),
    )

    @classmethod
    def create_with_variables_config(
        cls, assistant_id: str, user_id: str, variables_config_list: List[Dict[str, str]]
    ) -> "AssistantPromptVariableMappingBase":
        """
        Create a new AssistantPromptVariableMappingBase instance with the given variables_config.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user
            variables_config_list: List of prompt variable configurations

        Returns:
            New AssistantPromptVariableMappingBase instance
        """
        # Convert dictionaries to PromptVariableConfig instances
        variable_configs = [PromptVariableConfig(**config) for config in variables_config_list]

        instance = cls(assistant_id=assistant_id, user_id=user_id, variables_config=variable_configs)
        return instance


class AssistantPromptVariableMappingSQL(BaseModelWithSQLSupport, AssistantPromptVariableMappingBase, table=True):
    """SQLModel version of AssistantPromptVariableMapping for PostgreSQL storage"""

    __tablename__ = "assistant_prompt_variable_mapping"


# Use the SQL implementation
AssistantPromptVariableMapping = AssistantPromptVariableMappingSQL


class AssistantPromptVariableMappingRequest(CommonBaseModel):
    """Request model for creating/updating assistant prompt variable mappings"""

    variables_config: List[PromptVariableConfig]


class AssistantPromptVariableMappingResponse(CommonBaseModel):
    """Response model for assistant prompt variable mappings API"""

    id: str
    assistant_id: str
    user_id: str
    variables_config: List[PromptVariableConfig]
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_db_model(cls, db_model: AssistantPromptVariableMappingBase) -> "AssistantPromptVariableMappingResponse":
        """
        Convert database model to API response model.

        Args:
            db_model: Database model instance

        Returns:
            API response model instance
        """
        return cls(
            id=db_model.id,
            assistant_id=db_model.assistant_id,
            user_id=db_model.user_id,
            variables_config=db_model.variables_config,
            created_at=db_model.created_at,
            updated_at=db_model.updated_at,
        )
