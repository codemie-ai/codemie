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

"""Plugin tools delegate for handling plugin tool retrieval and filtering.

This module contains the logic for retrieving plugin tools from the enterprise
plugin system and filtering them based on assistant configuration.
"""

from typing import Any, List, Optional

from codemie_tools.base.models import ToolKit, ToolSet
from langchain_core.tools import BaseTool

from codemie.configs.logger import logger
from codemie.core.models import AssistantChatRequest, ToolConfig
from codemie.enterprise.plugin import (
    get_plugin_tools_for_assistant,
    is_plugin_enabled,
)
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.tools.plugin_utils import cleanup_plugin_tool_name


class PluginToolsDelegate:
    """Handles plugin tool retrieval and filtering logic."""

    @staticmethod
    def has_all_plugin_tools_enabled(assistant_toolkits: List[ToolKit]) -> bool:
        """Check if assistant has the 'Plugin' tool enabled in PLUGIN toolkit.

        When the 'Plugin' tool is present, it indicates all plugin tools should be enabled.

        Args:
            assistant_toolkits: List of toolkits assigned to the assistant

        Returns:
            True if PLUGIN toolkit contains a tool named 'Plugin', False otherwise
        """
        from codemie.enterprise.plugin.dependencies import PLUGIN_TOOL

        for toolkit in assistant_toolkits:
            if toolkit.toolkit == ToolSet.PLUGIN.value or toolkit.toolkit == ToolSet.PLUGIN:
                for tool in toolkit.tools:
                    if tool.name == PLUGIN_TOOL.name:
                        return True
        return False

    @classmethod
    def _filter_plugin_tools_by_assistant_tools(
        cls, plugin_tools: List[Any], assistant_toolkits: List[ToolKit]
    ) -> List[Any]:
        """Filter plugin tools by comparing cleaned names with assistant's configured plugin tools.

        Args:
            plugin_tools: List of all available plugin tools
            assistant_toolkits: List of toolkits configured for the assistant

        Returns:
            List of filtered plugin tools matching assistant's configuration
        """
        # Find all plugin toolkits (not just the first one)
        plugin_toolkits = [toolkit for toolkit in assistant_toolkits if toolkit.toolkit == ToolSet.PLUGIN]

        if not plugin_toolkits:
            return []

        tool_names = {tool.name for toolkit in plugin_toolkits for tool in toolkit.tools if tool.name}

        return [tool for tool in plugin_tools if tool.name and cleanup_plugin_tool_name(tool.name) in tool_names]

    @classmethod
    def _find_tool_config_by_name(cls, tools_config: Optional[List[ToolConfig]], name: str) -> Optional[ToolConfig]:
        """Find a specific tool configuration by name from a list of tool configurations.

        Args:
            tools_config: List of tool configurations (may be None)
            name: Name of the tool to find

        Returns:
            The found tool configuration or None if not found
        """
        if tools_config:
            return next((tc for tc in tools_config if tc.name == name), None)
        return None

    @classmethod
    def get_plugin_tools(
        cls,
        assistant: Assistant,
        user: User,
        request: Optional[AssistantChatRequest],
    ) -> list[BaseTool]:
        """Retrieve plugin tools from enterprise system with filtering.

        This method checks if the enterprise plugin system is available and enabled.
        If yes, it retrieves plugin tools and applies filtering based on assistant configuration.

        Args:
            assistant: The assistant to get plugin tools for
            user: The user making the request
            request: The assistant chat request

        Returns:
            List of plugin tools (LangChain BaseTool instances)

        Raises:
            RuntimeError: If enterprise plugin system is not available or enabled
        """
        if not is_plugin_enabled():
            raise RuntimeError("Enterprise plugin system is not available or enabled.")

        try:
            logger.info(f"Using enterprise plugin tools for assistant {assistant.id}")

            # Find tool config for PLUGIN toolkit
            tool_config = cls._find_tool_config_by_name(request.tools_config if request else None, ToolSet.PLUGIN.value)

            # Use enterprise integration to get plugin tools
            all_plugin_tools = get_plugin_tools_for_assistant(
                user_id=user.id,
                project_name=assistant.project,
                assistant_id=assistant.id,
                tool_config=tool_config,
            )

            # Check if assistant has 'Plugin' tool enabled (all plugins enabled)
            if cls.has_all_plugin_tools_enabled(assistant.toolkits):
                logger.info(f"Retrieved {len(all_plugin_tools)} plugin tools from enterprise (all plugins enabled)")
                return all_plugin_tools

            # Filter plugin tools by assistant's configured plugin tools
            filtered_tools = cls._filter_plugin_tools_by_assistant_tools(all_plugin_tools, assistant.toolkits)
            logger.info(f"Retrieved {len(filtered_tools)} filtered plugin tools from enterprise")
            return filtered_tools

        except Exception as e:
            logger.error(f"Error getting plugin tools from enterprise: {e}", exc_info=True)
            raise RuntimeError(f"Failed to get plugin tools from enterprise: {e}") from e
