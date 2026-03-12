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
Service for validating assistant integrations.

This service orchestrates validation of toolkit credentials for assistants,
with lazy loading and no recursion (orchestrators cannot have orchestrators as sub-assistants).
"""

from typing import List, Optional

from codemie.configs.logger import logger
from codemie.rest_api.models.assistant import (
    Assistant,
    AssistantRequest,
    IntegrationValidationResult,
    MissingIntegration,
    MissingIntegrationsByCredentialType,
    SettingsConfigLevel,
    ToolKitDetails,
    ToolDetails,
)
from codemie.rest_api.security.user import User
from codemie.service.assistant.credential_validator import CredentialValidator


class AssistantIntegrationValidator:
    """Orchestrates validation of assistant toolkit credentials."""

    @staticmethod
    def validate_integrations(
        assistant_request: AssistantRequest,
        user: User,
        project_name: str,
    ) -> IntegrationValidationResult:
        """
        Validate all integrations for an assistant.

        This method:
        1. Validates all toolkits in the main assistant
        2. Validates sub-assistants (lazy loading, no recursion)
        3. Groups results by credential_type
        4. Builds user-friendly message

        Args:
            assistant_request: Assistant configuration to validate
            user: User requesting validation
            project_name: Project name for scoped credentials

        Returns:
            Validation result with missing integrations grouped by credential_type
        """
        logger.info(f"Validating integrations for assistant: {assistant_request.name}")

        main_missing = AssistantIntegrationValidator._validate_toolkits(
            assistant_request.toolkits,
            user,
            project_name,
            assistant_id=None,  # Main assistant has no ID yet during creation
        )

        sub_missing = AssistantIntegrationValidator._validate_sub_assistants(
            assistant_request.assistant_ids or [],
            user,
            project_name,
        )

        main_grouped = AssistantIntegrationValidator._group_by_credential_type(main_missing)
        sub_grouped = AssistantIntegrationValidator._group_by_credential_type_with_context(sub_missing)

        return AssistantIntegrationValidator._build_validation_result(main_grouped, sub_grouped)

    @staticmethod
    def _validate_sub_assistants(
        assistant_ids: List[str],
        user: User,
        project_name: str,
    ) -> List[tuple]:
        """
        Validate sub-assistants and return missing tools with context.

        Args:
            assistant_ids: List of sub-assistant IDs to validate
            user: User requesting validation
            project_name: Project name for scoped credentials

        Returns:
            List of tuples (MissingIntegration, assistant_id, assistant_name, icon_url)
        """
        sub_missing = []

        for assistant_id in assistant_ids:
            try:
                sub_assistants = Assistant.get_by_ids(user, [assistant_id], parent_assistant=None)

                if not sub_assistants:
                    logger.warning(f"Sub-assistant not found: {assistant_id}")
                    continue

                sub_assistant = sub_assistants[0]

                sub_assistant_missing = AssistantIntegrationValidator._validate_toolkits(
                    sub_assistant.toolkits or [],
                    user,
                    project_name,
                    assistant_id=sub_assistant.id,  # Pass sub-assistant ID for user mappings
                )

                for tool in sub_assistant_missing:
                    sub_missing.append(
                        (
                            tool,
                            sub_assistant.id,
                            sub_assistant.name,
                            sub_assistant.icon_url,
                        )
                    )

            except Exception as e:
                logger.warning(f"Error validating sub-assistant {assistant_id}: {e}")
                continue

        return sub_missing

    @staticmethod
    def _build_validation_result(
        main_grouped: List[MissingIntegrationsByCredentialType],
        sub_grouped: List[MissingIntegrationsByCredentialType],
    ) -> IntegrationValidationResult:
        """
        Build validation result with message and logging.

        Args:
            main_grouped: Grouped missing integrations for main assistant
            sub_grouped: Grouped missing integrations for sub-assistants

        Returns:
            Complete validation result
        """
        total_count = sum(len(g.missing_tools) for g in main_grouped)
        total_count += sum(len(g.missing_tools) for g in sub_grouped)

        message = (
            f"Assistant has {total_count} tool(s) which require integrations that are not configured."
            if total_count > 0
            else None
        )

        if total_count > 0:
            logger.warning(f"Validation found missing integrations: {message}")
        else:
            logger.info("All required integrations are configured")

        return IntegrationValidationResult(
            has_missing_integrations=(total_count > 0),
            missing_by_credential_type=main_grouped,
            sub_assistants_missing=sub_grouped,
            message=message,
        )

    @classmethod
    def _validate_toolkits(
        cls,
        toolkits: List[ToolKitDetails],
        user: User,
        project_name: str,
        assistant_id: Optional[str] = None,
    ) -> List[MissingIntegration]:
        """
        Validate credentials for all toolkits.

        Args:
            toolkits: List of toolkits to validate
            user: User requesting validation
            project_name: Project name for scoped credentials
            assistant_id: Optional assistant ID for user-assistant mappings

        Returns:
            List of missing tools (not grouped)
        """
        missing_tools = []

        for toolkit in toolkits:
            # Skip external toolkits (provider tools)
            if getattr(toolkit, 'is_external', False):
                logger.debug(f"Skipping external toolkit: {toolkit.toolkit}")
                continue

            toolkit_missing = cls._validate_toolkit_tools(toolkit, user, project_name, assistant_id)
            missing_tools.extend(toolkit_missing)

        return missing_tools

    @classmethod
    def _validate_toolkit_tools(
        cls,
        toolkit: ToolKitDetails,
        user: User,
        project_name: str,
        assistant_id: Optional[str],
    ) -> List[MissingIntegration]:
        """
        Validate all tools in a single toolkit.

        Args:
            toolkit: Toolkit to validate
            user: User requesting validation
            project_name: Project name for scoped credentials
            assistant_id: Optional assistant ID for user-assistant mappings

        Returns:
            List of missing tools for this toolkit
        """
        missing_tools = []
        toolkit_settings = getattr(toolkit, 'settings', None)

        logger.debug(
            f"Toolkit {toolkit.toolkit} has settings: {bool(toolkit_settings)}, "
            f"alias={getattr(toolkit_settings, 'alias', None) if toolkit_settings else None}"
        )

        for tool in toolkit.tools:
            tool_settings = getattr(tool, 'settings', None)
            effective_settings = tool_settings if tool_settings else toolkit_settings

            cls._log_tool_validation(toolkit.toolkit, tool.name, tool_settings, toolkit_settings)

            validation_result = CredentialValidator.validate_tool_credentials(
                toolkit_name=toolkit.toolkit,
                tool_name=tool.name,
                user=user,
                project_name=project_name,
                tool_settings=effective_settings,
                assistant_id=assistant_id,
            )

            if not validation_result.is_valid:
                missing_tool = cls._create_missing_integration(toolkit, tool, validation_result)
                missing_tools.append(missing_tool)

        return missing_tools

    @classmethod
    def _log_tool_validation(
        cls,
        toolkit_name: str,
        tool_name: str,
        tool_settings: Optional[object],
        toolkit_settings: Optional[object],
    ) -> None:
        """Log tool validation details with settings source information."""
        if tool_settings:
            settings_source = 'tool'
        elif toolkit_settings:
            settings_source = 'toolkit'
        else:
            settings_source = 'none'

        logger.debug(
            f"Validating credentials for {toolkit_name}.{tool_name}: "
            f"tool_settings={bool(tool_settings)}, "
            f"toolkit_settings={bool(toolkit_settings)}, "
            f"using={settings_source}"
        )

    @staticmethod
    def _create_missing_integration(
        toolkit: ToolKitDetails,
        tool: ToolDetails,
        validation_result: object,
    ) -> MissingIntegration:
        """
        Create a MissingIntegration object for a tool that failed validation.

        Args:
            toolkit: Toolkit containing the tool
            tool: Tool that failed validation
            validation_result: Result from credential validation

        Returns:
            MissingIntegration object with appropriate settings level
        """
        # Use tool.settings_config directly - no need for service lookup
        settings_level = SettingsConfigLevel.TOOL if tool.settings_config else SettingsConfigLevel.TOOLKIT

        return MissingIntegration(
            toolkit=toolkit.toolkit,
            tool=tool.name,
            label=getattr(tool, 'label', tool.name),
            credential_type=validation_result.credential_type,
            settings_config_level=settings_level,
        )

    @staticmethod
    def _group_by_credential_type(
        missing_tools: List[MissingIntegration],
    ) -> List[MissingIntegrationsByCredentialType]:
        """
        Group missing tools by credential type (single pass with deduplication).

        Args:
            missing_tools: List of missing tools from main assistant

        Returns:
            List of missing integrations grouped by credential type
        """
        if not missing_tools:
            return []

        grouped_by_cred_type: dict[str, dict[tuple[str, str], MissingIntegration]] = {}

        for tool in missing_tools:
            cred_type = tool.credential_type or "Unknown"

            if cred_type not in grouped_by_cred_type:
                grouped_by_cred_type[cred_type] = {}

            # Deduplicate by (toolkit, tool) key
            tool_key = (tool.toolkit, tool.tool)
            if tool_key not in grouped_by_cred_type[cred_type]:
                grouped_by_cred_type[cred_type][tool_key] = tool

        result = [
            MissingIntegrationsByCredentialType(
                credential_type=cred_type,
                missing_tools=list(tools_dict.values()),
            )
            for cred_type, tools_dict in grouped_by_cred_type.items()
        ]

        # Sort by credential type for consistency
        result.sort(key=lambda x: x.credential_type)

        return result

    @staticmethod
    def _group_by_credential_type_with_context(
        missing_tools_with_context: List[tuple],
    ) -> List[MissingIntegrationsByCredentialType]:
        """
        Group sub-assistant missing tools with assistant context.

        Args:
            missing_tools_with_context: List of tuples (MissingIntegration, assistant_id,
                assistant_name, icon_url)

        Returns:
            List of missing integrations grouped by credential type with assistant context
        """
        if not missing_tools_with_context:
            return []

        # Group by (credential_type, assistant_id) to preserve sub-assistant context
        grouped: dict[tuple[str, str], MissingIntegrationsByCredentialType] = {}

        for tool, assistant_id, assistant_name, icon_url in missing_tools_with_context:
            cred_type = tool.credential_type or "Unknown"
            key = (cred_type, assistant_id)

            if key not in grouped:
                grouped[key] = MissingIntegrationsByCredentialType(
                    credential_type=cred_type,
                    missing_tools=[],
                    assistant_id=assistant_id,
                    assistant_name=assistant_name,
                    icon_url=icon_url,
                )

            # Deduplicate by (toolkit, tool) within this group
            tool_key = (tool.toolkit, tool.tool)
            existing_tools = {(t.toolkit, t.tool): t for t in grouped[key].missing_tools}
            if tool_key not in existing_tools:
                grouped[key].missing_tools.append(tool)

        # Sort by (credential_type, assistant_name) for consistency
        result = sorted(grouped.values(), key=lambda x: (x.credential_type, x.assistant_name or ''))
        return result
