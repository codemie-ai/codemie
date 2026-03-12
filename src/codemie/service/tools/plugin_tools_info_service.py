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

from __future__ import annotations

from typing import Optional

from codemie.configs import logger
from codemie.core.models import ToolConfig
from codemie.enterprise.plugin import PluginToolkitUI, get_plugin_tools_for_assistant
from codemie.rest_api.security.user import User
from codemie.service.tools.plugin_utils import cleanup_plugin_tool_name
from codemie_tools.base.models import ToolSet


class PluginToolsInfoServiceError(Exception):
    """Exception raised when PluginToolsInfoService operations fail."""

    def __init__(self, message: str, details: Optional[str] = None):
        self.message = message
        self.details = details
        super().__init__(message)


class PluginToolsInfoService:
    """Service for retrieving actual plugin tools information from Plugin Integration."""

    @staticmethod
    def get_plugin_toolkit_info(
        plugin_setting_id: Optional[str], user: User, project_name: Optional[str] = None
    ) -> PluginToolkitUI:
        """
        Get actual list of current plugin tools data for specific setting, user and project.

        Args:
            plugin_setting_id: Optional integration ID for the plugin setting
            user: User instance
            project_name: Optional project name (uses first application if not provided)

        Returns:
            PluginToolkitUI: Toolkit UI with available plugin tools

        Raises:
            PluginToolsInfoServiceError: If credentials not found or tools cannot be retrieved
        """
        try:
            tool_config = None
            if plugin_setting_id:
                tool_config = ToolConfig(name=ToolSet.PLUGIN.value, integration_id=plugin_setting_id)

            resolved_project = project_name or (user.project_names[0] if user.project_names else None)

            tools = get_plugin_tools_for_assistant(
                user_id=user.id, project_name=resolved_project, tool_config=tool_config
            )

            if not tools:
                logger.info(
                    f"No plugin tools found for user {user.id}. Plugin integration may not have tools configured."
                )
                raise PluginToolsInfoServiceError("No plugin tools found", "")

            tools_info = []
            for tool in tools:
                if not tool.name:
                    continue

                cleaned_name = cleanup_plugin_tool_name(tool.name)

                tools_info.append(
                    {
                        "name": cleaned_name,
                        "description": tool.description or "",
                        "label": cleaned_name.replace("_", " ").title(),
                    }
                )

            logger.info(f"Retrieved {len(tools_info)} plugin tools for user {user.id}")

            if not tools_info:
                raise PluginToolsInfoServiceError(
                    "No tools found",
                    "Please check that plugins are running, tools are available and the plugin key is correct.",
                )

            return PluginToolkitUI(tools=tools_info)

        except PluginToolsInfoServiceError:
            raise
        except Exception as e:
            logger.error(f"Error retrieving plugin toolkit info for user {user.id}: {e}.", exc_info=True)
            raise PluginToolsInfoServiceError(f"Error retrieving plugin toolkit: {str(e)}")
