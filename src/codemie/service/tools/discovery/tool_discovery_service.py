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

"""Main tool discovery service.

This module orchestrates tool discovery, configuration extraction, and schema formatting.
"""

import inspect
from typing import Any, Dict, Optional, Tuple, Type

from pydantic import BaseModel

from codemie.configs import logger
from codemie.core.models import ToolConfig
from codemie.enterprise.plugin import get_plugin_tools_for_assistant, is_plugin_enabled
from codemie.rest_api.models.settings import Settings
from codemie.service.tools.plugin_utils import cleanup_plugin_tool_name
from codemie_tools.base.base_toolkit import BaseToolkit
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.models import CredentialTypes, ToolMetadata, ToolSet

from .config_extractor import ToolConfigExtractor
from .metadata_finder import ToolMetadataFinder
from .schema_extractor import ToolSchemaExtractor


class ToolInfo(BaseModel):
    """Information about a discovered tool."""

    toolkit_class: Type
    config_class: Any
    config_schema: Dict
    tool_metadata: ToolMetadata
    config_param_name: str
    args_schema: Dict


class FormattedToolSchema(BaseModel):
    """Formatted tool schema for API responses."""

    tool_name: str
    creds_schema: Dict[str, Dict[str, Any]]
    args_schema: Dict[str, Dict[str, Any]]


class ToolDiscoveryService:
    """Main service for discovering tools and extracting their schemas."""

    @classmethod
    def find_tool_by_name(cls, tool_name: str) -> Optional[ToolInfo]:
        """Find tool information by searching for ToolMetadata with matching name.

        Args:
            tool_name: Name of the tool to find

        Returns:
            ToolInfo object if found, None otherwise
        """
        # Check provider tools first
        provider_result = ToolMetadataFinder.find_provider_tool(tool_name)
        if provider_result:
            toolkit_class, tool_metadata = provider_result
            return ToolInfo(
                toolkit_class=toolkit_class,
                config_class=None,
                config_schema={},
                tool_metadata=tool_metadata,
                config_param_name="",
                args_schema={},
            )

        # Find tool metadata
        tool_metadata = ToolMetadataFinder.find_tool_metadata(tool_name)
        if not tool_metadata:
            return None

        # Find toolkit class
        toolkit_class = ToolMetadataFinder.find_toolkit_for_metadata(tool_metadata)
        if not toolkit_class:
            return None

        # Extract configuration
        config_class, config_schema, config_param_name = ToolConfigExtractor.extract_config_for_tool(
            toolkit_class, tool_name
        )

        # Extract args schema
        args_schema = ToolSchemaExtractor.extract_args_schema(toolkit_class, tool_name)

        return ToolInfo(
            toolkit_class=toolkit_class,
            config_class=config_class,
            config_schema=config_schema,
            tool_metadata=tool_metadata,
            config_param_name=config_param_name,
            args_schema=args_schema,
        )

    @classmethod
    def get_formatted_tool_schema(
        cls, tool_name: str, user=None, setting_id: Optional[str] = None
    ) -> Optional[FormattedToolSchema]:
        """Get formatted tool schema with both credentials and args schemas.

        Args:
            tool_name: Name of the tool
            user: Optional user for runtime tool discovery (plugin tools)
            setting_id: Optional setting ID for runtime tool discovery

        Returns:
            FormattedToolSchema object if tool found, None otherwise
        """
        # Try static tool discovery first (metadata-based tools)
        tool_info = cls.find_tool_by_name(tool_name)
        if tool_info:
            return FormattedToolSchema(
                tool_name=tool_name,
                creds_schema=cls._format_schema(tool_info.config_schema),
                args_schema=cls._format_schema(tool_info.args_schema),
            )

        # Try runtime tool discovery (plugin tools, MCP tools) if user is provided
        if user:
            runtime_tool = cls._find_plugin_tool_by_setting(tool_name, user, setting_id)
            if runtime_tool:
                return cls._extract_schema_from_runtime_tool(tool_name, runtime_tool)

        # Tool not found
        return None

    @classmethod
    def _format_schema(cls, schema: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Format schema by converting type objects to strings."""
        formatted = {}
        for field_name, field_info in schema.items():
            field_type = field_info.get('type')
            type_str = getattr(field_type, "__name__", str(field_type))
            formatted[field_name] = {'type': type_str, 'required': field_info.get('required', True)}
        return formatted

    @classmethod
    def _find_plugin_tool_by_setting(cls, tool_name: str, user, setting_id: Optional[str]):
        """Find plugin tool by name using setting ID.

        Args:
            tool_name: Name of the plugin tool
            user: User instance
            setting_id: Optional plugin setting ID

        Returns:
            BaseTool instance if found, None otherwise
        """
        if not is_plugin_enabled():
            logger.debug("Plugin system not enabled")
            return None

        try:
            # Resolve project_name and tool_config from setting or user defaults
            project_name, tool_config = cls._resolve_plugin_context(user, setting_id)
            if project_name is None and setting_id:
                return None  # Setting validation failed

            # Get all plugin tools for the resolved context
            tools = get_plugin_tools_for_assistant(
                user_id=user.id, project_name=project_name, assistant_id=None, tool_config=tool_config
            )

            if not tools:
                logger.debug(f"No plugin tools found for user {user.id}")
                return None

            # Find the tool by name (with fallback to cleaned name)
            return cls._find_tool_in_list(tool_name, tools)

        except Exception as e:
            logger.error(f"Error finding plugin tool {tool_name}: {e}", exc_info=True)
            return None

    @classmethod
    def _resolve_plugin_context(cls, user, setting_id: Optional[str]) -> Tuple[Optional[str], Optional[ToolConfig]]:
        """Resolve project_name and tool_config from setting or user defaults.

        Args:
            user: User instance
            setting_id: Optional plugin setting ID

        Returns:
            Tuple of (project_name, tool_config). Returns (None, None) if setting validation fails.
        """
        if not setting_id:
            # Use default from user applications
            project_name = user.applications[0] if user.applications else None
            return project_name, None

        # Validate and extract setting information
        setting = Settings.get_by_id(setting_id)
        if not cls._validate_plugin_setting(setting, user, setting_id):
            return None, None

        project_name = setting.project_name
        tool_config = ToolConfig(name=ToolSet.PLUGIN.value, integration_id=setting_id)
        return project_name, tool_config

    @classmethod
    def _validate_plugin_setting(cls, setting: Optional[Settings], user, setting_id: str) -> bool:
        """Validate plugin setting exists, belongs to user, and is correct type.

        Args:
            setting: Setting instance or None
            user: User instance
            setting_id: Setting ID being validated

        Returns:
            True if valid, False otherwise
        """
        if not setting:
            logger.warning(f"Setting not found: {setting_id}")
            return False

        if setting.user_id != user.id:
            logger.warning(f"Setting {setting_id} does not belong to user {user.id}")
            return False

        if setting.credential_type != CredentialTypes.PLUGIN:
            logger.warning(f"Setting {setting_id} is not a plugin setting")
            return False

        return True

    @classmethod
    def _find_tool_in_list(cls, tool_name: str, tools: list) -> Optional[CodeMieTool]:
        """Find tool in list by name, with fallback to cleaned name.

        Args:
            tool_name: Tool name to search for
            tools: List of tool instances

        Returns:
            Tool instance if found, None otherwise
        """
        from codemie.service.tools.tool_service import ToolsService

        # Try finding with original name
        tool = cls._try_find_tool(tool_name, tools, ToolsService)
        if tool:
            logger.debug(f"Found plugin tool: {tool_name}")
            return tool

        # Try with cleaned name as fallback
        cleaned_tool_name = cleanup_plugin_tool_name(tool_name)
        if cleaned_tool_name != tool_name:
            tool = cls._try_find_tool(cleaned_tool_name, tools, ToolsService)
            if tool:
                logger.debug(f"Found plugin tool with cleaned name: {cleaned_tool_name}")
                return tool

        logger.debug(f"Plugin tool not found: {tool_name}")
        return None

    @classmethod
    def _try_find_tool(cls, tool_name: str, tools: list, tools_service) -> Optional[CodeMieTool]:
        """Attempt to find tool by name, returning None if not found.

        Args:
            tool_name: Tool name to search for
            tools: List of tool instances
            tools_service: ToolsService class for finding tools

        Returns:
            Tool instance if found, None otherwise
        """
        try:
            return tools_service.find_tool(tool_name, tools)
        except ValueError:
            return None

    @classmethod
    def _extract_schema_from_runtime_tool(cls, tool_name: str, tool: CodeMieTool) -> Optional[FormattedToolSchema]:
        """Extract schema from runtime tool instance using args_schema property.

        Args:
            tool_name: Name of the tool
            tool: BaseTool instance with args_schema property

        Returns:
            FormattedToolSchema if successful, None otherwise
        """
        try:
            # Plugin tools don't have config schema (credentials managed via settings)
            config_schema = {}

            # Extract args_schema from the tool using existing ToolConfigExtractor
            args_schema = {}
            if hasattr(tool, 'args_schema') and tool.args_schema:
                # Use existing config extractor to process the Pydantic model
                args_schema = ToolConfigExtractor.get_config_schema(tool.args_schema)

            return FormattedToolSchema(
                tool_name=tool_name,
                creds_schema=cls._format_schema(config_schema),
                args_schema=cls._format_schema(args_schema),
            )

        except Exception as e:
            logger.error(f"Error extracting schema from runtime tool {tool_name}: {e}", exc_info=True)
            return None

    @classmethod
    def create_toolkit_instance(cls, tool_info: ToolInfo, config_values: Dict[str, Any]) -> Optional[BaseToolkit]:
        """Create toolkit instance with configuration.

        Args:
            tool_info: Tool information object
            config_values: Configuration values to instantiate the toolkit

        Returns:
            Instantiated toolkit or None if creation fails
        """
        try:
            if not tool_info.config_class or not tool_info.config_param_name:
                return tool_info.toolkit_class()

            config = tool_info.config_class(**config_values)
            return tool_info.toolkit_class(**{tool_info.config_param_name: config})
        except Exception as e:
            logger.error(f"Error creating toolkit instance: {e}", exc_info=True)
            return None

    @classmethod
    def get_toolkit_method_for_tool(
        cls, tool_name: str, toolkit_class: Optional[Type] = None
    ) -> Optional[Tuple[callable, Dict[str, Any]]]:
        """Find the toolkit method that creates the tool with the given name by inspecting the toolkit class.

        Args:
            tool_name: Name of the tool
            toolkit_class: Optional toolkit class to search in

        Returns:
            Tuple of (method, parameters dict) if found, None otherwise
        """
        metadata_result = ToolMetadataFinder.find_tool_metadata(tool_name, return_var=True)
        if not metadata_result:
            return None

        metadata_var, tool_metadata = metadata_result

        if not toolkit_class:
            found_toolkit_class = ToolMetadataFinder.find_toolkit_for_metadata(tool_metadata)
            if not found_toolkit_class:
                return None
            toolkit_class = found_toolkit_class

        method = cls._find_method_with_metadata(metadata_var, toolkit_class)
        if not method:
            return None

        params = cls._extract_method_parameters(method)
        return method, params

    @classmethod
    def _find_method_with_metadata(cls, metadata_var: str, toolkit_class) -> Optional[callable]:
        """Find the method containing the given metadata variable."""
        for method_name, method in inspect.getmembers(toolkit_class, inspect.isfunction):
            if method_name.startswith('_'):
                continue

            try:
                source = inspect.getsource(method)
                if metadata_var in source:
                    return method
            except Exception:
                continue
        return None

    @classmethod
    def _extract_method_parameters(cls, method: callable) -> Dict[str, Any]:
        """Extract parameters from method signature excluding cls/self."""
        params = {}
        sig = inspect.signature(method)

        for param_name, param in sig.parameters.items():
            if param_name in ('cls', 'self'):
                continue
            params[param_name] = None if param.default is param.empty else param.default

        return params
