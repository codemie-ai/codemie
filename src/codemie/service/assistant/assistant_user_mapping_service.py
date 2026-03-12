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
Service for managing assistant-to-tools mappings.
"""

from typing import Dict, List, Optional

from codemie.configs import logger
from codemie.repository.assistants.assistant_user_mapping_repository import (
    AssistantUserMappingRepositoryImpl,
    AssistantUserMappingRepository,
)
from codemie.rest_api.models.usage.assistant_user_mapping import AssistantUserMappingSQL, ToolConfig


class AssistantUserMappingService:
    """Service for managing assistant-to-tools mappings."""

    def __init__(self, repository: Optional[AssistantUserMappingRepository] = None):
        """Initialize the service with a repository."""
        self.repository = repository if repository else AssistantUserMappingRepositoryImpl()

    def create_or_update_mapping(
        self, assistant_id: str, user_id: str, tools_config: List[Dict[str, str]]
    ) -> AssistantUserMappingSQL:
        """
        Create or update a mapping between an assistant and tools/settings.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user
            tools_config: List of tool configurations with name and integration_id

        Returns:
            The created or updated mapping record
        """
        logger.debug(f"Creating or updating mapping for assistant {assistant_id} and user {user_id}")

        # Convert dictionaries to ToolConfig instances
        tool_configs = [ToolConfig(**config) for config in tools_config]

        return self.repository.create_or_update_mapping(assistant_id, user_id, tool_configs)

    def get_mapping(self, assistant_id: str, user_id: str) -> Optional[AssistantUserMappingSQL]:
        """
        Get mapping for a specific assistant and user.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user

        Returns:
            Mapping record if found, None otherwise
        """
        logger.debug(f"Getting mapping for assistant {assistant_id} and user {user_id}")
        return self.repository.get_mapping(assistant_id, user_id)

    def get_mappings_by_assistant(self, assistant_id: str) -> List[AssistantUserMappingSQL]:
        """
        Get all mappings for a specific assistant.

        Args:
            assistant_id: ID of the assistant

        Returns:
            List of mapping records for the assistant
        """
        logger.debug(f"Getting all mappings for assistant {assistant_id}")
        return self.repository.get_mappings_by_assistant(assistant_id)

    def get_mappings_by_user(self, user_id: str) -> List[AssistantUserMappingSQL]:
        """
        Get all mappings for a specific user.

        Args:
            user_id: ID of the user

        Returns:
            List of mapping records for the user
        """
        logger.debug(f"Getting all mappings for user {user_id}")
        return self.repository.get_mappings_by_user(user_id)


# Create a singleton instance of the service
assistant_user_mapping_service = AssistantUserMappingService()
