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
Repository for assistant-to-tools mappings.
"""

from abc import ABC, abstractmethod
from datetime import datetime, UTC
from typing import Optional, List, Any
from uuid import uuid4

from sqlmodel import Session, select

from codemie.rest_api.models.usage.assistant_user_mapping import AssistantUserMappingSQL, ToolConfig


class AssistantUserMappingRepository(ABC):
    """
    Abstract base class for assistant mapping repository.
    Defines the interface for assistant-to-tools mapping data operations.
    """

    @abstractmethod
    def create_or_update_mapping(self, assistant_id: str, user_id: str, tools_config: List[ToolConfig]) -> Any:
        """
        Create or update a mapping between an assistant and tools/settings.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user
            tools_config: List of tool configurations

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


class SQLAssistantUserMappingRepository(AssistantUserMappingRepository):
    """
    SQL implementation of the assistant mapping repository.
    Uses SQLModel to interact with the database.
    """

    def create_or_update_mapping(
        self, assistant_id: str, user_id: str, tools_config: List[ToolConfig]
    ) -> AssistantUserMappingSQL:
        """
        Create or update a mapping between an assistant and tools/settings.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user
            tools_config: List of tool configurations

        Returns:
            The created or updated mapping record
        """
        mapping = self.get_mapping(assistant_id, user_id)

        if mapping:
            # Update existing record
            with Session(AssistantUserMappingSQL.get_engine()) as session:
                mapping.tools_config = tools_config
                mapping.updated_at = datetime.now(UTC)
                session.add(mapping)
                session.commit()
                session.refresh(mapping)
                return mapping
        else:
            # Create new record with explicit ID
            with Session(AssistantUserMappingSQL.get_engine()) as session:
                # Create a new record
                mapping = AssistantUserMappingSQL(
                    id=str(uuid4()), assistant_id=assistant_id, user_id=user_id, tools_config=tools_config
                )
                session.add(mapping)
                session.commit()
                session.refresh(mapping)
                return mapping

    def get_mapping(self, assistant_id: str, user_id: str) -> Optional[AssistantUserMappingSQL]:
        """
        Get mapping for a specific assistant and user.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user

        Returns:
            Mapping record if found, None otherwise
        """
        with Session(AssistantUserMappingSQL.get_engine()) as session:
            query = select(AssistantUserMappingSQL).where(
                AssistantUserMappingSQL.assistant_id == assistant_id, AssistantUserMappingSQL.user_id == user_id
            )
            return session.exec(query).first()

    def get_mappings_by_assistant(self, assistant_id: str) -> List[AssistantUserMappingSQL]:
        """
        Get all mappings for a specific assistant.

        Args:
            assistant_id: ID of the assistant

        Returns:
            List of mapping records for the assistant
        """
        with Session(AssistantUserMappingSQL.get_engine()) as session:
            query = select(AssistantUserMappingSQL).where(AssistantUserMappingSQL.assistant_id == assistant_id)
            return session.exec(query).all()

    def get_mappings_by_user(self, user_id: str) -> List[AssistantUserMappingSQL]:
        """
        Get all mappings for a specific user.

        Args:
            user_id: ID of the user

        Returns:
            List of mapping records for the user
        """
        with Session(AssistantUserMappingSQL.get_engine()) as session:
            query = select(AssistantUserMappingSQL).where(AssistantUserMappingSQL.user_id == user_id)
            return session.exec(query).all()


# Default implementation
AssistantUserMappingRepositoryImpl = SQLAssistantUserMappingRepository
