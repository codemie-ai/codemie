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

"""Service for managing assistant version operations"""

from typing import Optional
from datetime import datetime, UTC

from codemie.configs.logger import logger
from codemie.rest_api.models.assistant import (
    Assistant,
    AssistantConfiguration,
    AssistantRequest,
    AssistantVersionHistoryResponse,
)
from codemie.rest_api.security.user import User
from codemie.core.models import CreatedByUser
from codemie.core.exceptions import ExtendedHTTPException
from fastapi import status


class AssistantVersionService:
    """Service for managing assistant version operations"""

    @classmethod
    def create_initial_version(
        cls, assistant: Assistant, request: AssistantRequest, user: User
    ) -> AssistantConfiguration:
        """
        Create the initial version (version 1) for a new assistant.

        Args:
            assistant: The newly created assistant master record
            request: The assistant creation request
            user: The user creating the assistant

        Returns:
            The created version configuration
        """
        logger.info(f"Creating initial version for assistant {assistant.id}")

        config = AssistantConfiguration(
            assistant_id=assistant.id,
            version_number=1,
            description=request.description or "",
            system_prompt=request.system_prompt or "",
            llm_model_type=request.llm_model_type,
            temperature=request.temperature,
            top_p=request.top_p,
            context=request.context,
            toolkits=request.toolkits,
            mcp_servers=request.mcp_servers,
            assistant_ids=request.assistant_ids,
            conversation_starters=request.conversation_starters,
            bedrock=request.bedrock,
            agent_card=request.agent_card,
            custom_metadata=request.custom_metadata,
            created_by=CreatedByUser(id=user.id, username=user.username, name=user.name),
            change_notes="Initial version",
        )

        config.save()
        logger.info(f"Created version 1 for assistant {assistant.id}")
        return config

    @classmethod
    def create_new_version(
        cls, assistant: Assistant, request: AssistantRequest, user: User, change_notes: Optional[str] = None
    ) -> AssistantConfiguration:
        """
        Create a new version when assistant is updated.

        Args:
            assistant: The assistant master record
            request: The update request
            user: The user making the update
            change_notes: Optional notes about the changes

        Returns:
            The new version configuration
        """
        latest_version = AssistantConfiguration.get_latest_version_number(assistant.id)
        new_version_number = latest_version + 1

        logger.debug(f"Creating version {new_version_number} for assistant {assistant.id}")

        config = AssistantConfiguration(
            assistant_id=assistant.id,
            version_number=new_version_number,
            description=request.description or "",
            system_prompt=request.system_prompt or "",
            llm_model_type=request.llm_model_type,
            temperature=request.temperature,
            top_p=request.top_p,
            context=request.context,
            toolkits=request.toolkits,
            mcp_servers=request.mcp_servers,
            assistant_ids=request.assistant_ids,
            conversation_starters=request.conversation_starters,
            bedrock=request.bedrock,
            agent_card=request.agent_card,
            custom_metadata=request.custom_metadata,
            created_by=CreatedByUser(id=user.id, username=user.username, name=user.name),
            change_notes=change_notes or "Configuration updated",
        )

        config.save()

        # Update master record version count
        assistant.version_count = new_version_number
        assistant.updated_date = datetime.now(UTC)
        assistant.update()

        logger.debug(f"Created version {new_version_number} for assistant {assistant.id}")
        return config

    @classmethod
    def get_version(cls, assistant_id: str, version_number: int) -> AssistantConfiguration:
        """
        Get a specific version of an assistant.

        Args:
            assistant_id: The assistant ID
            version_number: The version number to retrieve

        Returns:
            The version configuration

        Raises:
            ExtendedHTTPException: If version not found
        """
        config = AssistantConfiguration.get_by_assistant_and_version(assistant_id, version_number)

        if not config:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message="Version not found",
                details=f"Version {version_number} not found for assistant {assistant_id}",
                help="Please verify the version number and try again",
            )

        return config

    @classmethod
    def get_current_version(cls, assistant_id: str) -> AssistantConfiguration:
        """
        Get the current (latest) version of an assistant.

        Args:
            assistant_id: The assistant ID

        Returns:
            The current version configuration

        Raises:
            ExtendedHTTPException: If no versions found
        """
        config = AssistantConfiguration.get_current_version(assistant_id)

        if not config:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message="No versions found",
                details=f"No version configurations found for assistant {assistant_id}",
                help="This may indicate a data consistency issue. Please contact support.",
            )

        return config

    @classmethod
    def get_version_history(
        cls, assistant: Assistant, page: int = 0, per_page: int = 20
    ) -> AssistantVersionHistoryResponse:
        """
        Get version history for an assistant.

        Args:
            assistant: The assistant master record
            page: Page number for pagination
            per_page: Number of versions per page

        Returns:
            Version history response with pagination
        """
        versions = AssistantConfiguration.get_version_history(assistant.id, page, per_page)

        return AssistantVersionHistoryResponse(
            versions=versions,
            total_versions=assistant.version_count,
            assistant_name=assistant.name,
            assistant_id=assistant.id,
        )

    @classmethod
    def rollback_to_version(
        cls, assistant: Assistant, target_version_number: int, user: User, change_notes: Optional[str] = None
    ) -> AssistantConfiguration:
        """
        Rollback assistant to a previous version by creating a new version
        with the target version's configuration.

        Args:
            assistant: The assistant master record
            target_version_number: The version to rollback to
            user: The user performing the rollback
            change_notes: Optional notes about the rollback

        Returns:
            The new version created from the rollback

        Raises:
            ExtendedHTTPException: If validation fails
        """
        # Validate rollback
        if target_version_number == assistant.version_count:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Cannot rollback to current version",
                details=f"Version {target_version_number} is already the current version",
                help="Please select a different version to rollback to",
            )

        if target_version_number < 1 or target_version_number > assistant.version_count:
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Invalid version number",
                details=f"Version {target_version_number} does not exist. Valid range: 1 to {assistant.version_count}",
                help="Please verify the version number",
            )

        # Get target version
        target_config = cls.get_version(assistant.id, target_version_number)

        # Create new version with target configuration
        new_version_number = assistant.version_count + 1

        logger.info(
            f"Rolling back assistant {assistant.id} to version {target_version_number}. "
            f"Creating new version {new_version_number}"
        )

        rollback_notes = change_notes or f"Rolled back to version {target_version_number}"

        new_config = AssistantConfiguration(
            assistant_id=assistant.id,
            version_number=new_version_number,
            # Copy all configuration from target version
            description=target_config.description,
            system_prompt=target_config.system_prompt,
            llm_model_type=target_config.llm_model_type,
            temperature=target_config.temperature,
            top_p=target_config.top_p,
            context=target_config.context,
            toolkits=target_config.toolkits,
            mcp_servers=target_config.mcp_servers,
            assistant_ids=target_config.assistant_ids,
            conversation_starters=target_config.conversation_starters,
            bedrock=target_config.bedrock,
            agent_card=target_config.agent_card,
            custom_metadata=target_config.custom_metadata,
            # New metadata
            created_by=CreatedByUser(id=user.id, username=user.username, name=user.name),
            change_notes=rollback_notes,
        )

        new_config.save()

        # Update master record with rollback configuration
        assistant.version_count = new_version_number
        assistant.updated_date = datetime.now(UTC)
        cls.update_assistant_config_fields(assistant, target_config)
        assistant.update()

        logger.info(f"Rollback complete. Created version {new_version_number} for assistant {assistant.id}")

        return new_config

    @classmethod
    def update_assistant_config_fields(cls, assistant: Assistant, config: AssistantConfiguration) -> None:
        """
        Update the assistant's configuration fields from a version configuration.

        This method is used to persist configuration changes to the assistant master record,
        typically during rollback or update operations.

        Args:
            assistant: The assistant master record to update (modified in place)
            config: The version configuration to apply
        """
        assistant.description = config.description
        assistant.system_prompt = config.system_prompt
        assistant.llm_model_type = config.llm_model_type
        assistant.temperature = config.temperature
        assistant.top_p = config.top_p
        assistant.context = config.context
        assistant.toolkits = config.toolkits
        assistant.mcp_servers = config.mcp_servers
        assistant.assistant_ids = config.assistant_ids
        assistant.conversation_starters = config.conversation_starters
        assistant.bedrock = config.bedrock
        assistant.bedrock_agentcore_runtime = config.bedrock_agentcore_runtime
        assistant.agent_card = config.agent_card
        assistant.custom_metadata = config.custom_metadata

        logger.debug(f"Updated assistant {assistant.id} configuration fields from version {config.version_number}")

    @classmethod
    def apply_version_to_assistant(cls, assistant: Assistant, version_number: int) -> Assistant:
        """
        Apply a specific version configuration to an assistant instance for execution.

        This method creates a new assistant instance with the specified version's configuration,
        allowing the assistant to be used with version-specific settings while maintaining
        the original assistant identity for permissions and tracking.

        This is used for chatting with specific versions without modifying the database.

        Args:
            assistant: The assistant master record
            version_number: The version number to apply

        Returns:
            A new assistant instance with version configuration applied

        Raises:
            ExtendedHTTPException: If version not found
        """
        logger.debug(f"Applying version {version_number} to assistant {assistant.id}")

        # Get the version configuration
        version_config = cls.get_version(assistant.id, version_number)

        # Create a new assistant instance with the base data
        asst = Assistant(**assistant.model_dump())

        # Apply version-specific configuration
        cls.update_assistant_config_fields(asst, version_config)
        asst.version = version_config.version_number

        logger.debug(f"Successfully applied version {version_number} configuration to assistant {asst.id}")

        return asst
