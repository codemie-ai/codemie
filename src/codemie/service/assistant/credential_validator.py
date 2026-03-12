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
Service for validating tool credentials.

This service checks if required credentials exist for toolkit tools,
using shared utilities for metadata extraction and config resolution.
"""

from typing import Optional

from pydantic import BaseModel

from codemie.configs.logger import logger
from codemie.rest_api.models.settings import SettingsBase
from codemie.rest_api.security.user import User
from codemie.service.settings.settings import SettingsService
from codemie.service.tools.tool_metadata_service import ToolMetadataService, get_enum_value
from codemie_tools.base.models import ToolSet


class ValidationResult(BaseModel):
    """Result of credential validation for a tool."""

    is_valid: bool
    credential_type: Optional[str] = None


class CredentialValidator:
    """Validates that required credentials exist for toolkit tools."""

    @staticmethod
    def validate_tool_credentials(
        toolkit_name: str,
        tool_name: str,
        user: User,
        project_name: str,
        tool_settings: Optional[SettingsBase] = None,
        assistant_id: Optional[str] = None,
    ) -> ValidationResult:
        """
        Check if credentials exist for a tool.

        This method:
        1. Checks if tool requires credentials (via ToolMetadataHelper)
        2. Checks inline credentials (tool_settings.credential_values)
        3. Checks stored credentials (via ToolConfigResolver + SettingsService)

        Args:
            toolkit_name: Name of the toolkit
            tool_name: Name of the tool
            user: User requesting validation
            project_name: Project name for scoped credentials
            tool_settings: Optional inline tool settings with credentials
            assistant_id: Optional assistant ID for user-assistant mappings

        Returns:
            ValidationResult with is_valid and credential_type
        """
        # Special handling for internal toolkits (Plugin, Git)
        if ToolMetadataService.is_internal_toolkit(toolkit_name):
            logger.debug(f"Validating internal toolkit: {toolkit_name}. Tool_settings={tool_settings}")
            return CredentialValidator._check_internal_toolkit_credentials(
                toolkit_name, tool_name, user, project_name, tool_settings, assistant_id
            )

        # 1. Check if credentials required (use ToolMetadataService)
        if not ToolMetadataService.requires_credentials(tool_name, toolkit_name):
            logger.debug(f"Tool {toolkit_name}.{tool_name} does not require credentials")
            return ValidationResult(is_valid=True)

        # 2. Check inline credentials first
        if tool_settings and hasattr(tool_settings, 'credential_values') and tool_settings.credential_values:
            logger.debug(f"Tool {toolkit_name}.{tool_name} has inline credentials")
            return ValidationResult(is_valid=True)  # Credentials provided inline

        # 3. Get config_class (use ToolMetadataService)
        config_class = ToolMetadataService.get_config_class(tool_name, toolkit_name)
        if not config_class:
            logger.warning(f"No config_class found for {toolkit_name}.{tool_name} - cannot validate credentials")
            return ValidationResult(is_valid=True)  # Can't validate without config_class, assume valid

        # 4. Try to resolve config (will check stored credentials)
        config = ToolMetadataService.resolve_config(
            tool_name=tool_name,
            toolkit_name=toolkit_name,
            user_id=user.id,
            project_name=project_name,
            assistant_id=assistant_id,
            tool_settings=tool_settings,
        )

        # 5. Extract credential_type (use ToolMetadataService)
        credential_type = ToolMetadataService.get_credential_type(config_class)

        # 6. Return validation result
        is_valid = config is not None
        if is_valid:
            logger.debug(f"Found credentials for {toolkit_name}.{tool_name}")
        else:
            logger.debug(f"Missing credentials for {toolkit_name}.{tool_name}")

        return ValidationResult(is_valid=is_valid, credential_type=credential_type)

    @staticmethod
    def _check_internal_toolkit_credentials(
        toolkit_name: str,
        tool_name: str,
        user: User,
        project_name: str,
        tool_settings: Optional[SettingsBase] = None,
        assistant_id: Optional[str] = None,
    ) -> ValidationResult:
        """
        Check credentials for internal toolkits (Plugin, Git).

        These toolkits use special methods in SettingsService:
        - Git: Uses get_git_creds() which uses get_config(Credentials)
        - Plugin: Uses get_plugin_creds() which uses get_credentials()

        Args:
            toolkit_name: Name of the internal toolkit ("Plugin" or "Git")
            tool_name: Name of the tool (for logging only)
            user: User requesting validation
            project_name: Project name for scoped credentials
            tool_settings: Optional tool settings with alias or credential_values
            assistant_id: Optional assistant ID for user-assistant mappings

        Returns:
            ValidationResult with is_valid and credential_type
        """

        toolkit_name_str = get_enum_value(toolkit_name)

        # Convert tool_settings to tool_config if provided
        tool_config = None
        if tool_settings:
            logger.debug(
                f"Tool {toolkit_name}.{tool_name} settings payload: "
                f"id={getattr(tool_settings, 'id', None)}, "
                f"alias={getattr(tool_settings, 'alias', None)}, "
                f"has_credential_values={bool(getattr(tool_settings, 'credential_values', None))}"
            )
            tool_config = ToolMetadataService._convert_settings_to_config(tool_settings)
            if tool_config:
                logger.debug(
                    f"Converted tool_settings to tool_config: "
                    f"integration_id={tool_config.integration_id}, "
                    f"has_tool_creds={bool(tool_config.tool_creds)}"
                )

        try:
            if toolkit_name_str == ToolSet.GIT.value:
                creds = SettingsService.get_git_creds(
                    user_id=user.id,
                    project_name=project_name,
                    repo_link=None,  # None will try to find any git credentials
                    tool_config=tool_config,
                    assistant_id=assistant_id,
                )
                is_valid = bool(creds and creds.url and creds.token)
                logger.debug(f"Git credentials validation result: is_valid={is_valid}, assistant_id={assistant_id}")
                return ValidationResult(is_valid=is_valid, credential_type="Git")

            elif toolkit_name_str == ToolSet.PLUGIN.value:
                creds = SettingsService.get_plugin_creds(
                    user_id=user.id,
                    project_name=project_name,
                    tool_config=tool_config,
                    assistant_id=assistant_id,
                )
                is_valid = bool(creds and creds.plugin_key)
                logger.debug(f"Plugin credentials validation result: is_valid={is_valid}, assistant_id={assistant_id}")
                return ValidationResult(is_valid=is_valid, credential_type="Plugin")

            else:
                logger.warning(f"Unknown internal toolkit: {toolkit_name_str}")
                return ValidationResult(is_valid=True)

        except Exception as e:
            logger.error(f"Error checking credentials for {toolkit_name_str}: {e}", exc_info=True)
            # Return valid on error (defensive programming) - assume credentials are okay if we can't check
            return ValidationResult(is_valid=True, credential_type=toolkit_name_str)
