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
Service for working with tool metadata and configuration resolution.

This module provides utilities for:
- Extracting tool/toolkit metadata from toolkit_provider
- Resolving tool configuration from various sources
- Centralizing logic shared between validation and initialization
"""

from collections import defaultdict
from functools import lru_cache
from typing import Optional, Type

from pydantic import BaseModel
from codemie_tools.base import toolkit_provider
from codemie_tools.base.models import ToolSet

from codemie.configs.logger import logger
from codemie.core.models import ToolConfig
from codemie.rest_api.models.settings import SettingsBase
from codemie.service.settings.settings import SettingsService


INTERNAL_TOOLKITS = (ToolSet.PLUGIN.value, ToolSet.GIT.value)

WORKFLOW_UNSUPPORTED_INTEGRATION_TYPES: frozenset[str] = frozenset({ToolSet.PLUGIN.value})
"""
Integration types that can never be matched when filtering workflows.

Workflow tool records (WorkflowTool.tool / WorkflowAssistantTool.name) store only a flat
tool-name string — unlike Assistant's ToolKitDetails, there is no toolkit field to match
"Plugin" by name. Plugin tool names are also dynamic/per-instance (fetched live from the
enterprise plugin service), so unlike Git's fixed GIT_TOOL_NAMES, no static tool-name set
can ever be built for it either. Callers filtering workflows by integration_type must
reject these values explicitly instead of silently matching nothing.
"""

GIT_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "create_branch",
        "create_pull_request",
        "create_file",
        "delete_file",
        "list_branches_in_repo",
        "update_file",
        "update_file_diff",
        "set_active_branch",
        "get_pr_changes",
        "create_pr_change_comment",
    }
)


def get_enum_value(enum_or_value) -> Optional[str]:
    """
    Extract string value from an enum or return the value as-is if it's already a string.

    This utility handles the common pattern of converting ToolSet enums and other
    enum types to their string values for comparison and storage.

    Args:
        enum_or_value: Enum instance, string, or None

    Returns:
        String value of the enum, the original string, or None

    Examples:
        >>> from codemie_tools.base.models import ToolSet
        >>> get_enum_value(ToolSet.GIT)
        'Git'
        >>> get_enum_value("Git")
        'Git'
        >>> get_enum_value(None)
        None
    """
    if enum_or_value is None:
        return None

    # If it's an enum, get its value
    if hasattr(enum_or_value, 'value'):
        return enum_or_value.value

    # Otherwise, convert to string
    return str(enum_or_value)


class ToolMetadataService:
    """
    Service for working with tool metadata and configuration resolution.

    Combines metadata extraction and config resolution into a single service
    for better organization and reduced file count.
    """

    @staticmethod
    def _get_tool_and_toolkit_definitions(tool_name: str, toolkit_name: str) -> tuple[object | None, object | None]:
        """
        Get tool and toolkit definitions from toolkit_provider.

        Args:
            tool_name: Name of the tool
            toolkit_name: Name of the toolkit containing the tool

        Returns:
            Tuple of (tool_definition, toolkit_definition) or (None, None) if not found
        """
        toolkit_name_str = get_enum_value(toolkit_name)

        toolkit_class = toolkit_provider.get_toolkit(toolkit_name_str)
        if not toolkit_class:
            logger.debug(f"Toolkit not found in provider: {toolkit_name_str}")
            return None, None

        toolkit_definition = toolkit_class.get_definition()

        tool_definition = toolkit_provider.get_tool(tool_name)
        if not tool_definition:
            logger.debug(f"Tool not found in provider: {tool_name}")
            return None, None

        return tool_definition, toolkit_definition

    @staticmethod
    def requires_credentials(tool_name: str, toolkit_name: str) -> bool:
        """
        Check if a tool requires credentials based on toolkit_provider metadata.

        Args:
            tool_name: Name of the tool
            toolkit_name: Name of the toolkit containing the tool

        Returns:
            True if credentials are required, False otherwise
        """
        tool_definition, toolkit_definition = ToolMetadataService._get_tool_and_toolkit_definitions(
            tool_name, toolkit_name
        )

        if tool_definition is None or toolkit_definition is None:
            return False

        tool_requires = getattr(tool_definition, 'settings_config', False)
        toolkit_requires = getattr(toolkit_definition, 'settings_config', False)

        return tool_requires or toolkit_requires

    @staticmethod
    def get_config_class(tool_name: str, toolkit_name: str) -> Optional[Type]:
        """
        Get config_class for a tool.

        Args:
            tool_name: Name of the tool
            toolkit_name: Name of the toolkit containing the tool

        Returns:
            Config class type if found, None otherwise
        """
        tool_definition, toolkit_definition = ToolMetadataService._get_tool_and_toolkit_definitions(
            tool_name, toolkit_name
        )

        if tool_definition is None or toolkit_definition is None:
            return None

        # Try tool-level config_class first
        config_class = getattr(tool_definition, 'config_class', None)

        # Fallback to toolkit-level config_class
        if not config_class:
            config_class = getattr(toolkit_definition, 'config_class', None)

        return config_class

    @staticmethod
    def get_credential_type(config_class: Type) -> Optional[str]:
        """
        Extract credential_type from config_class.

        Args:
            config_class: Configuration class with credential_type field

        Returns:
            Credential type string or None if not found
        """
        if not config_class:
            return None

        try:
            if hasattr(config_class, 'model_fields') and 'credential_type' in config_class.model_fields:
                field = config_class.model_fields['credential_type']
                if hasattr(field, 'default'):
                    return get_enum_value(field.default)

            instance = config_class()
            if hasattr(instance, 'credential_type'):
                return get_enum_value(instance.credential_type)

        except Exception as e:
            logger.debug(f"Could not extract credential_type from {config_class.__name__}: {e}")

        return None

    @staticmethod
    def is_internal_toolkit(toolkit_name: str) -> bool:
        """
        Check if toolkit is internal (Plugin, Git).
        """
        return get_enum_value(toolkit_name) in INTERNAL_TOOLKITS

    @staticmethod
    @lru_cache(maxsize=1)
    def _credential_type_to_tool_names() -> dict[str, frozenset[str]]:
        """
        Reverse registry: credential_type -> tool names that carry it.

        Built from toolkit_provider's discoverable toolkits (excludes Plugin/Git — see
        is_internal_toolkit — since those never expose credential_type through config_class;
        callers must match them by toolkit name instead). Cached because toolkit_provider's
        registry is static for the process lifetime.
        """
        mapping: dict[str, set[str]] = defaultdict(set)
        for toolkit_class in toolkit_provider.get_available_toolkits():
            try:
                definition = toolkit_class.get_definition()
            except Exception as e:
                logger.warning(
                    f"Could not get definition for toolkit {toolkit_class.__name__}: {e}. "
                    f"Its tools will be excluded from integration_type/credential_type filtering "
                    f"until process restart (result is cached)."
                )
                continue

            if definition is None or ToolMetadataService.is_internal_toolkit(definition.toolkit):
                continue

            for tool in definition.tools:
                config_class = getattr(tool, "config_class", None) or getattr(definition, "config_class", None)
                credential_type = ToolMetadataService.get_credential_type(config_class)
                if credential_type:
                    mapping[credential_type].add(tool.name)

        return {credential_type: frozenset(names) for credential_type, names in mapping.items()}

    @staticmethod
    def get_supported_credential_types() -> frozenset[str]:
        """
        Credential types that can be used as an integration_type filter value: those that resolve
        to at least one tool, plus the internal toolkits (Git, Plugin) which are matched separately.
        """
        return frozenset(ToolMetadataService._credential_type_to_tool_names()) | frozenset(INTERNAL_TOOLKITS)

    @staticmethod
    def get_tool_names_for_credential_types(credential_types: list[str]) -> set[str]:
        """
        Resolve credential_type values to the set of tool names that carry them.
        Internal toolkits (Plugin, Git) are not in the discoverable registry; Git tool
        names are added explicitly via GIT_TOOL_NAMES when "Git" is requested.
        """
        mapping = ToolMetadataService._credential_type_to_tool_names()
        names: set[str] = set()
        for credential_type in credential_types:
            names |= mapping.get(credential_type, frozenset())
        if ToolSet.GIT.value in credential_types:
            names |= GIT_TOOL_NAMES
        return names

    @staticmethod
    def resolve_config(
        tool_name: str,
        toolkit_name: str,
        user_id: str,
        project_name: str,
        assistant_id: Optional[str] = None,
        tool_config: Optional[ToolConfig] = None,
        tool_settings: Optional[SettingsBase] = None,
    ) -> Optional[BaseModel]:
        """
        Resolve tool configuration with priority:
        1. Inline credentials (tool_config.tool_creds or tool_settings.credential_values)
        2. Referenced integration (tool_config.integration_id)
        3. Stored credentials (SettingsService lookup)

        Args:
            tool_name: Name of the tool
            toolkit_name: Name of the toolkit containing the tool
            user_id: User ID for retrieving stored configurations
            project_name: Project name for configuration lookup
            assistant_id: Optional assistant ID for configuration lookup
            tool_config: Optional tool configuration from request
            tool_settings: Optional inline tool settings with credentials

        Returns:
            Config object if found, None otherwise
        """
        # 1. Check if credentials required
        if not ToolMetadataService.requires_credentials(tool_name, toolkit_name):
            logger.debug(f"Tool {toolkit_name}.{tool_name} does not require credentials")
            return None  # No config needed

        # 2. Get config_class
        config_class = ToolMetadataService.get_config_class(tool_name, toolkit_name)
        if not config_class:
            logger.debug(f"No config_class found for {toolkit_name}.{tool_name}")
            return None

        # 3. Convert tool_settings to tool_config if needed
        if tool_settings and not tool_config:
            tool_config = ToolMetadataService._convert_settings_to_config(tool_settings)

        # 4. Delegate to SettingsService (handles inline credentials + stored credentials)
        try:
            config = SettingsService.get_config(
                config_class=config_class,
                user_id=user_id,
                project_name=project_name,
                assistant_id=assistant_id,
                tool_config=tool_config,
            )
            return config
        except Exception as e:
            logger.debug(f"Could not resolve config for {toolkit_name}.{tool_name}: {e}")
            return None

    @staticmethod
    def _convert_settings_to_config(tool_settings: SettingsBase) -> Optional[ToolConfig]:
        """
        Convert SettingsBase to ToolConfig format.

        This handles the conversion from tool_settings to ToolConfig with priority order:
        1. tool_settings.id -> tool_config.integration_id (reference to stored integration)
        2. tool_settings.credential_values -> tool_config.tool_creds (inline credentials)

        Note: The settings record ID (tool_settings.id) IS the integration ID. Each Settings
        record represents one integration configuration, and its primary key is used to
        reference that integration throughout the system.

        Args:
            tool_settings: Tool settings with id or credential_values

        Returns:
            ToolConfig if credentials found, None otherwise
        """
        if not tool_settings:
            return None

        # Priority 1: Check if has id (reference to stored integration)
        settings_id = getattr(tool_settings, 'id', None)
        if settings_id is not None:
            return ToolConfig(
                name=getattr(tool_settings, 'name', ''),
                integration_id=settings_id,
            )

        # Priority 2: Check if has credential_values (inline credentials)
        if not hasattr(tool_settings, 'credential_values'):
            return None

        credential_values = getattr(tool_settings, 'credential_values', None)
        if not credential_values:
            return None

        # Create ToolConfig with tool_creds
        return ToolConfig(
            name=getattr(tool_settings, 'name', ''),
            tool_creds=credential_values,
        )
