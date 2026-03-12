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
Service for managing assistant-to-prompt-variables mappings.
"""

from typing import Dict, List, Optional

from codemie.configs import logger
from codemie.repository.assistants.assistant_prompt_variable_mapping_repository import (
    AssistantPromptVariableMappingRepositoryImpl,
    AssistantPromptVariableMappingRepository,
)
from codemie.rest_api.models.usage.assistant_prompt_variable_mapping import (
    AssistantPromptVariableMappingSQL,
    PromptVariableConfig,
)
from codemie.service.encryption.encryption_factory import EncryptionFactory


class AssistantPromptVariableMappingService:
    """Service for managing assistant-to-prompt-variables mappings."""

    MASKED_VALUE: str = "*" * 10

    def __init__(self, repository: Optional[AssistantPromptVariableMappingRepository] = None):
        """Initialize the service with a repository."""
        self.repository = repository if repository else AssistantPromptVariableMappingRepositoryImpl()
        self.encryption_service = EncryptionFactory().get_current_encryption_service()

    def create_or_update_mapping(
        self,
        assistant_id: str,
        user_id: str,
        variables_config: List[PromptVariableConfig],
    ) -> AssistantPromptVariableMappingSQL:
        """
        Create or update a mapping between an assistant's prompt variables and user values.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user
            variables_config: List of PromptVariableConfig with variable_key, variable_value, and is_sensitive

        Returns:
            The created or updated mapping record
        """
        logger.debug(f"Creating or updating prompt variable mapping for assistant {assistant_id} and user {user_id}")

        # Encrypt sensitive variables
        variable_configs = self._encrypt_sensitive_variables(variables_config)

        return self.repository.create_or_update_mapping(assistant_id, user_id, variable_configs)

    def get_mapping(self, assistant_id: str, user_id: str) -> Optional[AssistantPromptVariableMappingSQL]:
        """
        Get mapping for a specific assistant and user.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user

        Returns:
            Mapping record if found, None otherwise
        """
        logger.debug(f"Getting prompt variable mapping for assistant {assistant_id} and user {user_id}")
        return self.repository.get_mapping(assistant_id, user_id)

    def get_mapping_with_masked_values(
        self, assistant_id: str, user_id: str, assistant=None
    ) -> Optional[AssistantPromptVariableMappingSQL]:
        """
        Get mapping for a specific assistant and user with sensitive values masked.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user
            assistant: Optional assistant object to get is_sensitive flags from definition

        Returns:
            Mapping record with masked sensitive values if found, None otherwise
        """
        logger.debug(
            f"Getting prompt variable mapping with masked values for assistant {assistant_id} and user {user_id}"
        )
        mapping = self.repository.get_mapping(assistant_id, user_id)

        if mapping and mapping.variables_config:
            # Create a map of is_sensitive flags from assistant definition
            sensitive_map = {}
            if assistant and assistant.prompt_variables:
                sensitive_map = {var.key: var.is_sensitive for var in assistant.prompt_variables}
                logger.debug(f"Sensitive variable map from assistant definition: {sensitive_map}")

            # Update is_sensitive flag from assistant definition and mask sensitive values
            for config in mapping.variables_config:
                # Override is_sensitive from assistant definition
                config.is_sensitive = sensitive_map.get(config.variable_key, False)

            # Mask sensitive variables for UI display
            mapping.variables_config = self._mask_sensitive_variables(mapping.variables_config)

        return mapping

    def get_mappings_by_assistant(self, assistant_id: str) -> List[AssistantPromptVariableMappingSQL]:
        """
        Get all mappings for a specific assistant.

        Args:
            assistant_id: ID of the assistant

        Returns:
            List of mapping records for the assistant
        """
        logger.debug(f"Getting all prompt variable mappings for assistant {assistant_id}")
        return self.repository.get_mappings_by_assistant(assistant_id)

    def get_mappings_by_user(self, user_id: str) -> List[AssistantPromptVariableMappingSQL]:
        """
        Get all mappings for a specific user.

        Args:
            user_id: ID of the user

        Returns:
            List of mapping records for the user
        """
        logger.debug(f"Getting all prompt variable mappings for user {user_id}")
        return self.repository.get_mappings_by_user(user_id)

    def get_user_variable_values(self, assistant_id: str, user_id: str) -> Dict[str, str]:
        """
        Get all variable values for a user and assistant as a dictionary with decrypted sensitive values.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user

        Returns:
            Dictionary of variable_key: variable_value (with sensitive values decrypted)
        """
        logger.debug(f"Getting user variable values for assistant {assistant_id} and user {user_id}")
        mapping = self.get_mapping(assistant_id, user_id)

        if not mapping:
            logger.debug(f"No variable mapping found for assistant {assistant_id} and user {user_id}")
            return {}

        if not mapping.variables_config:
            logger.debug(
                f"Found mapping record but variables_config is empty for assistant {assistant_id} and user {user_id}"
            )
            return {}

        # Decrypt sensitive variables
        mapping.variables_config = self._decrypt_sensitive_variables(mapping.variables_config)

        result = {config.variable_key: config.variable_value for config in mapping.variables_config}
        logger.debug(f"Found {len(result)} user variable values")
        return result

    def _encrypt_sensitive_variables(self, variables_config: List[PromptVariableConfig]) -> List[PromptVariableConfig]:
        """
        Encrypt values for variables marked as sensitive.

        Args:
            variables_config: List of variable configurations to encrypt

        Returns:
            List of variable configurations with sensitive values encrypted
        """
        for config in variables_config:
            if config.is_sensitive and config.variable_value:
                logger.debug(f"Encrypting sensitive variable: {config.variable_key}")
                config.variable_value = self.encryption_service.encrypt(config.variable_value)

        return variables_config

    def _decrypt_sensitive_variables(self, variables_config: List[PromptVariableConfig]) -> List[PromptVariableConfig]:
        """
        Decrypt values for variables marked as sensitive.

        Args:
            variables_config: List of variable configurations to decrypt

        Returns:
            List of variable configurations with sensitive values decrypted
        """
        for config in variables_config:
            if config.is_sensitive and config.variable_value:
                logger.debug(f"Decrypting sensitive variable: {config.variable_key}")
                config.variable_value = self.encryption_service.decrypt(config.variable_value)

        return variables_config

    def _mask_sensitive_variables(self, variables_config: List[PromptVariableConfig]) -> List[PromptVariableConfig]:
        """
        Mask values for variables marked as sensitive for UI display.

        Args:
            variables_config: List of variable configurations to mask

        Returns:
            List of variable configurations with sensitive values masked
        """
        for config in variables_config:
            if config.is_sensitive and config.variable_value:
                logger.debug(f"Masking sensitive variable: {config.variable_key}")
                config.variable_value = self.MASKED_VALUE

        return variables_config


# Create a singleton instance of the service
assistant_prompt_variable_mapping_service = AssistantPromptVariableMappingService()
