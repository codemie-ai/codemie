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

import operator
from functools import reduce
from typing import Dict, List, Optional

from codemie_tools.base.models import ToolSet

from codemie.configs import logger
from codemie.core.models import AssistantChatRequest
from codemie.core.workflow_models import WorkflowAssistant, WorkflowTool
from codemie.rest_api.models.assistant import Assistant, ToolDetails, ToolKitDetails
from codemie.rest_api.models.settings import SettingsBase
from codemie.rest_api.security.user import User
from codemie.service.tools.tools_info_service import ToolsInfoService

SETTING_NOT_FOUND_ERROR = "Setting with alias {alias} not found"
TOOLKIT_NOT_FOUND_ERROR = "Toolset *{toolkit}* not found.\nAvailable:\n- {available_toolkits}"
TOOL_NOT_FOUND_ERROR = "Tool *{tool_name}* not found.\nAvailable:\n- {available_tools}"


class ToolsService:
    @classmethod
    def get_toolkits_from_assistant_tool_config(cls, config: WorkflowAssistant, user: User, project_name: str):
        return [cls.get_toolkit(tool.name, user, project_name, tool.integration_alias) for tool in config.tools]

    @classmethod
    def get_toolkit_from_workflow_tool_config(cls, tool_config: WorkflowTool, user: User, project_name: str):
        return cls.get_toolkit(tool_config.tool, user, project_name, tool_config.integration_alias)

    @classmethod
    def get_toolkit(cls, tool_name: str, user: User, project_name: str, integration_alias: Optional[str] = None):
        toolkit = cls.find_toolkit_for_tool(user=user, tool_name=tool_name)
        tool_attrs = {"name": tool_name}
        logger.debug(f"get_toolkit. ToolName: {tool_name}. Toolkit: {toolkit}")
        if integration_alias:
            tool_attrs["settings"] = cls.find_setting_for_tool(
                user=user, project_name=project_name, integration_alias=integration_alias
            )
        toolkit_attrs = {**toolkit}
        toolkit_attrs.update({"tools": [ToolDetails(**tool_attrs)]})
        return ToolKitDetails(**toolkit_attrs)

    @classmethod
    def find_tool_from_config(
        cls, tool_config: WorkflowTool, toolkits: Dict, assistant: Assistant, user: User, project_name: str
    ):
        toolkit = ToolsService.get_toolkit_from_workflow_tool_config(tool_config, user, project_name)
        config = toolkit.get_tool_configs()
        from codemie.service.tools import ToolkitService

        tools = ToolkitService.get_core_tools(
            assistant_toolkits=assistant.toolkits,
            user_id=user.id,
            project_name=assistant.project,
            assistant_id=assistant.id,
            tools_config=toolkit.get_tool_configs(),
        )
        if not tools:
            toolkit_name = toolkit.toolkit
            toolkit_method = toolkits.get(toolkit_name)
            if toolkit_method:
                tools = toolkits[toolkit_name](assistant, user, '', '', AssistantChatRequest(tools_config=config))

        return cls.find_tool(tool_config.tool, tools)

    @classmethod
    def find_tool_by_invoke_request(
        cls, tool_name: str, toolkits: Dict, assistant: Assistant, user: User, project: str
    ):
        toolkit = ToolsService.get_toolkit(tool_name, user, project)
        from codemie.service.tools import ToolkitService

        tools = ToolkitService.get_core_tools(
            assistant_toolkits=assistant.toolkits,
            user_id=user.id,
            project_name=assistant.project,
            assistant_id=assistant.id,
            tools_config=toolkit.get_tool_configs(),
        )
        if not tools:
            tools = toolkits[toolkit.toolkit](assistant, user, '', False, '')
        return cls.find_tool(tool_name, tools)

    @classmethod
    def find_tool(cls, tool_name: str, tools: List, tool_name_suffix: Optional[str] = None):
        if tool_name_suffix:
            tool_name = f"{tool_name}_{tool_name_suffix}"

        if tool_name.startswith("_"):
            tool = next((tool for tool in tools if tool_name in tool.name), None)
        else:
            tool = next((tool for tool in tools if tool.name == tool_name), None)

        if not tool:
            # To handle invalid plugin tool
            raise ValueError(
                TOOL_NOT_FOUND_ERROR.format(
                    tool_name=tool_name,
                    available_tools=[tool.name for tool in tools],
                )
            )
        return tool

    @staticmethod
    def find_toolkit_for_tool(user: User, tool_name: str):
        available_toolkits = ToolsService._get_available_toolkits(user)
        toolkit_found = None

        if tool_name and tool_name.startswith("_"):
            toolkit_found = next(
                (toolkit for toolkit in available_toolkits if toolkit["toolkit"] == ToolSet.PLUGIN), None
            )
            if toolkit_found:
                return toolkit_found

        for toolkit in available_toolkits:
            tool_found = next((item for item in toolkit["tools"] if item["name"] == tool_name), None)

            if tool_found:
                toolkit_found = toolkit

        if not toolkit_found:
            available_tools = [[tool["name"] for tool in toolkit["tools"]] for toolkit in available_toolkits]

            raise ValueError(
                TOOL_NOT_FOUND_ERROR.format(
                    tool_name=tool_name,
                    available_tools=reduce(operator.concat, available_tools),
                )
            )

        return toolkit_found

    @staticmethod
    def find_setting_for_tool(user: User, project_name: str, integration_alias: str) -> SettingsBase:
        """Given integration alias, lookup for for setting"""
        # To prevent circular import error
        from codemie.service.settings.settings import SettingsService
        from codemie.service.settings.base_settings import SearchFields

        setting = SettingsService.retrieve_setting(
            {
                SearchFields.USER_ID: user.id,
                SearchFields.PROJECT_NAME: project_name,
                SearchFields.ALIAS: integration_alias,
            }
        )

        if not setting:
            raise ValueError(SETTING_NOT_FOUND_ERROR.format(alias=integration_alias))

        return setting

    @staticmethod
    def _get_available_toolkits(user: User) -> List:
        return ToolsInfoService.get_tools_info(user=user)
