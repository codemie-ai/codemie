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

from copy import deepcopy
from typing import List, Dict, Optional

from codemie_tools.base.models import ToolSet
from codemie_tools.base import toolkit_provider
from codemie_tools.data_management.file_system.toolkit import FileSystemToolkit
from codemie_tools.data_management.workspace.toolkit import AgentWorkspaceToolkit
from codemie_tools.git.toolkit import GitToolkit
from codemie_tools.research.toolkit import ResearchToolkit

from codemie.agents.tools.kb.kb_toolkit import KBToolkit
from codemie.agents.tools.platform import PlatformToolkit
from codemie.configs import logger
from codemie.rest_api.security.user import User
from codemie.service.provider import ProviderToolkitsFactory


class ToolsInfoService:
    @staticmethod
    def get_tools_info(show_for_ui: bool = False, user: Optional[User] = None) -> List[Dict[str, str]]:
        """
        Get tools info to be displayed on the UI

        Args:
            show_for_ui: UI-specific formatting
            user: Current user (used for admin tools)
        """

        # Derive show_admin_tools from user
        show_admin_tools = user.is_admin if user else False

        # Create a copy of cached toolkits
        toolkits = deepcopy(list(toolkit_provider.get_available_toolkits_info()))
        logger.info(f"Get available toolkits. Autodiscovered toolkits: {len(toolkits)}")
        logger.debug(f"Get available toolkits. Autodiscovered toolkits: {toolkits}")

        # Import plugin UI info using enterprise dependency pattern
        from codemie.enterprise.plugin import get_plugin_toolkit_ui_info

        # Build standard toolkits list
        standard_toolkits = [
            GitToolkit.get_tools_ui_info(),
            ResearchToolkit.get_tools_ui_info(),
            FileSystemToolkit.get_tools_ui_info(show_admin_tools),
            AgentWorkspaceToolkit.get_tools_ui_info(),
        ]

        # Add plugin toolkit if available (enterprise dependency pattern)
        # get_plugin_toolkit_ui_info() returns wrapped ToolKit instance with full metadata
        if plugin_toolkit_ui := get_plugin_toolkit_ui_info():
            # Convert ToolKit instance to dict for consistency with other toolkits
            standard_toolkits.append(plugin_toolkit_ui.model_dump())

        toolkits.extend(standard_toolkits)
        logger.info(f"Get available toolkits. Append standard toolkits: {len(toolkits)}")

        ToolsInfoService._merge_code_toolkit(toolkits, show_for_ui)

        if not show_for_ui:
            toolkits.append(KBToolkit.get_tools_ui_info())
            logger.info(f"Get available toolkits. Append KB toolkit: {len(toolkits)}")

        toolkits.append(PlatformToolkit.get_tools_ui_info(show_admin_tools))
        logger.info(f"Get available toolkits. Append Platform toolkit: {len(toolkits)}")

        toolkits.extend(ToolsInfoService._provider_toolkits_info())
        logger.info(f"Get available toolkits. Append provider toolkits: {len(toolkits)}")

        return toolkits

    @staticmethod
    def _merge_code_toolkit(toolkits: List[Dict], show_for_ui: bool) -> None:
        """Merge backend code tools into autodiscovered CodeToolkit (if exists), or add it."""
        from codemie.agents.tools.code.code_toolkit import CodeToolkit as BackendCodeToolkit

        code_toolkit = next(
            (t for t in toolkits if t.get("toolkit") == ToolSet.CODEBASE_TOOLS.value),
            None,
        )
        backend_tools = (
            BackendCodeToolkit.get_tools_ui_info() if show_for_ui else BackendCodeToolkit.get_tools_api_info()
        )

        if code_toolkit:
            code_toolkit.setdefault("tools", []).extend(backend_tools["tools"])
        else:
            toolkits.append(backend_tools)

    @staticmethod
    def _provider_toolkits_info() -> List[Dict[str, str]]:
        """Get provider toolkits info"""
        provider_toolkits = ProviderToolkitsFactory.get_toolkits()

        return [provider_toolkit.get_tools_ui_info() for provider_toolkit in provider_toolkits]
