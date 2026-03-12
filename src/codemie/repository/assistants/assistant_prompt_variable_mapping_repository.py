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
Repository for assistant-to-prompt-variables mappings.
"""

from abc import ABC, abstractmethod
from datetime import datetime, UTC
from typing import Optional, List, Any
from uuid import uuid4

from sqlmodel import Session, select

from codemie.rest_api.models.usage.assistant_prompt_variable_mapping import (
    AssistantPromptVariableMappingSQL,
    PromptVariableConfig,
)


class AssistantPromptVariableMappingRepository(ABC):
    """
    Abstract base class for assistant prompt variable mapping repository.
    Defines the interface for assistant-to-prompt-variables mapping data operations.
    """

    @abstractmethod
    def create_or_update_mapping(
        self, assistant_id: str, user_id: str, variables_config: List[PromptVariableConfig]
    ) -> Any:
        """
        Create or update a mapping between an assistant's prompt variables and user values.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user
            variables_config: List of prompt variable configurations

        Returns:
            The created or updated mapping record
        """
        pass

    @abstractmethod
    def get_mapping(self, assistant_id: str, user_id: str) -> Optional[Any]:
        """
        Get mapping for a specific assistant and user.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user

        Returns:
            Mapping record if found, None otherwise
        """
        pass

    @abstractmethod
    def get_mappings_by_assistant(self, assistant_id: str) -> List[Any]:
        """
        Get all mappings for a specific assistant.

        Args:
            assistant_id: ID of the assistant

        Returns:
            List of mapping records for the assistant
        """
        pass

    @abstractmethod
    def get_mappings_by_user(self, user_id: str) -> List[Any]:
        """
        Get all mappings for a specific user.

        Args:
            user_id: ID of the user

        Returns:
            List of mapping records for the user
        """
        pass


class SQLAssistantPromptVariableMappingRepository(AssistantPromptVariableMappingRepository):
    """
    SQL implementation of the assistant prompt variable mapping repository.
    Uses SQLModel to interact with the database.
    """

    def create_or_update_mapping(
        self, assistant_id: str, user_id: str, variables_config: List[PromptVariableConfig]
    ) -> AssistantPromptVariableMappingSQL:
        """
        Create or update a mapping between an assistant's prompt variables and user values.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user
            variables_config: List of prompt variable configurations

        Returns:
            The created or updated mapping record
        """
        mapping = self.get_mapping(assistant_id, user_id)

        if mapping:
            # Update existing record
            with Session(AssistantPromptVariableMappingSQL.get_engine()) as session:
                mapping.variables_config = variables_config
                mapping.updated_at = datetime.now(UTC)
                session.add(mapping)
                session.commit()
                session.refresh(mapping)
                return mapping
        else:
            # Create new record with explicit ID
            with Session(AssistantPromptVariableMappingSQL.get_engine()) as session:
                # Create a new record
                mapping = AssistantPromptVariableMappingSQL(
                    id=str(uuid4()), assistant_id=assistant_id, user_id=user_id, variables_config=variables_config
                )
                session.add(mapping)
                session.commit()
                session.refresh(mapping)
                return mapping

    def get_mapping(self, assistant_id: str, user_id: str) -> Optional[AssistantPromptVariableMappingSQL]:
        """
        Get mapping for a specific assistant and user.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user

        Returns:
            Mapping record if found, None otherwise
        """
        with Session(AssistantPromptVariableMappingSQL.get_engine()) as session:
            query = select(AssistantPromptVariableMappingSQL).where(
                AssistantPromptVariableMappingSQL.assistant_id == assistant_id,
                AssistantPromptVariableMappingSQL.user_id == user_id,
            )
            result = session.exec(query).first()
            return result

    def get_mappings_by_assistant(self, assistant_id: str) -> List[AssistantPromptVariableMappingSQL]:
        """
        Get all mappings for a specific assistant.

        Args:
            assistant_id: ID of the assistant

        Returns:
            List of mapping records for the assistant
        """
        with Session(AssistantPromptVariableMappingSQL.get_engine()) as session:
            query = select(AssistantPromptVariableMappingSQL).where(
                AssistantPromptVariableMappingSQL.assistant_id == assistant_id
            )
            return session.exec(query).all()

    def get_mappings_by_user(self, user_id: str) -> List[AssistantPromptVariableMappingSQL]:
        """
        Get all mappings for a specific user.

        Args:
            user_id: ID of the user

        Returns:
            List of mapping records for the user
        """
        with Session(AssistantPromptVariableMappingSQL.get_engine()) as session:
            query = select(AssistantPromptVariableMappingSQL).where(
                AssistantPromptVariableMappingSQL.user_id == user_id
            )
            return session.exec(query).all()


# Default implementation
AssistantPromptVariableMappingRepositoryImpl = SQLAssistantPromptVariableMappingRepository
