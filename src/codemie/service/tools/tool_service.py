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
from collections.abc import Iterable
from typing import Dict, List, Optional

from codemie_tools.base.models import ToolSet
from codemie_tools.base.tool_registry import can_be_listed_for

from codemie.configs import logger
from codemie.core.models import AssistantChatRequest
from codemie.core.workflow_models import WorkflowAssistant, WorkflowAssistantTool, WorkflowTool
from codemie.rest_api.models.assistant import Assistant, ToolDetails, ToolKitDetails
from codemie.rest_api.models.settings import SettingsBase
from codemie.rest_api.security.user import User
from codemie.service.tools.tools_info_service import ToolsInfoService

SETTING_NOT_FOUND_ERROR = "Setting with alias {alias} not found"
TOOLKIT_NOT_FOUND_ERROR = "Toolset *{toolkit}* not found.\nAvailable:\n- {available_toolkits}"
TOOL_NOT_FOUND_ERROR = "Tool *{tool_name}* not found.\nAvailable:\n- {available_tools}"


class ToolsService:
    @classmethod
    def get_toolkits_from_assistant_tool_config(
        cls,
        config: WorkflowAssistant,
        user: User,
        project_name: str,
        owner_user_id: str | None = None,
    ) -> list[ToolKitDetails]:
        return cls.get_toolkits_from_tools(config.tools, user, project_name, owner_user_id=owner_user_id)

    @classmethod
    def get_toolkits_from_tools(
        cls,
        tools: Iterable[WorkflowAssistantTool],
        user: User,
        project_name: str,
        owner_user_id: str | None = None,
    ) -> list[ToolKitDetails]:
        """One toolkit per listed tool; an alias pins the integration (looked up for ``owner_user_id`` when given)."""
        return [
            cls.get_toolkit(tool.name, user, project_name, tool.integration_alias, owner_user_id=owner_user_id)
            for tool in tools
        ]

    @classmethod
    def get_toolkits_for_tool_access(
        cls,
        host_tool: str,
        tool_access: Iterable[WorkflowAssistantTool] | None,
        user: User,
        project_name: str,
        owner_user_id: str | None = None,
    ) -> list[ToolKitDetails]:
        """Toolkits of the tools a step lists in ``tool_access``, built like the tools of a workflow assistant.

        The tool of the step (``host_tool``) names the filter a listed tool must pass (``tool_access_filter``); a tool
        that does not pass is never built. A name listed twice is dropped too. Save validation refuses both, so one
        that got here (import, direct write) is ambiguous or not allowed and must not widen the step's tools.
        """
        entries = list(tool_access or [])
        names = [entry.name for entry in entries]
        accepted = []
        for entry in entries:
            if names.count(entry.name) > 1 or not can_be_listed_for(host_tool, entry.name):
                logger.warning(f"Tool '{entry.name}' is not allowed in tool_access of '{host_tool}'; it is left out.")
                continue
            accepted.append(entry)
        return cls.get_toolkits_from_tools(accepted, user, project_name, owner_user_id=owner_user_id)

    @classmethod
    def get_toolkit_from_workflow_tool_config(
        cls,
        tool_config: WorkflowTool,
        user: User,
        project_name: str,
        owner_user_id: str | None = None,
    ) -> ToolKitDetails:
        return cls.get_toolkit(
            tool_config.tool, user, project_name, tool_config.integration_alias, owner_user_id=owner_user_id
        )

    @classmethod
    def get_toolkit(
        cls,
        tool_name: str,
        user: User,
        project_name: str,
        integration_alias: Optional[str] = None,
        owner_user_id: str | None = None,
    ) -> ToolKitDetails:
        toolkit = cls.find_toolkit_for_tool(user=user, tool_name=tool_name)
        tool_attrs = {"name": tool_name}
        logger.debug(f"get_toolkit. ToolName: {tool_name}. Toolkit: {toolkit}")
        if integration_alias:
            tool_attrs["settings"] = cls.find_setting_for_tool(
                user=user,
                project_name=project_name,
                integration_alias=integration_alias,
                owner_user_id=owner_user_id,
            )
        toolkit_attrs = {**toolkit}
        toolkit_attrs.update({"tools": [ToolDetails(**tool_attrs)]})
        return ToolKitDetails(**toolkit_attrs)

    @classmethod
    def find_tool_from_config(
        cls,
        tool_config: WorkflowTool,
        toolkits: Dict,
        assistant: Assistant,
        user: User,
        project_name: str,
        owner_user_id: str | None = None,
        execution_id: str | None = None,
    ) -> object:
        toolkit = ToolsService.get_toolkit_from_workflow_tool_config(
            tool_config, user, project_name, owner_user_id=owner_user_id
        )
        config = toolkit.get_tool_configs()
        from codemie.service.tools import ToolkitService

        tools = ToolkitService.get_core_tools(
            assistant_toolkits=assistant.toolkits,
            user_id=owner_user_id or user.id,
            project_name=assistant.project,
            assistant_id=assistant.id,
            tools_config=toolkit.get_tool_configs(),
            request_uuid=execution_id,
        )
        if not tools:
            toolkit_name = toolkit.toolkit
            toolkit_method = toolkits.get(toolkit_name)
            if toolkit_method:
                request_kwargs = {"tools_config": config}
                if execution_id:
                    request_kwargs["conversation_id"] = execution_id
                tools = toolkits[toolkit_name](assistant, user, '', '', AssistantChatRequest(**request_kwargs))

        return cls.find_tool(tool_config.tool, tools)

    @classmethod
    def find_tool_with_access(
        cls,
        tool_config: WorkflowTool,
        assistant: Assistant,
        user: User,
        execution_id: str,
        request_headers: dict[str, str] | None = None,
    ):
        """Build the tool of a step that takes ``tool_access`` together with the tools it lists.

        ``assistant`` carries the toolkit of the step's tool and one toolkit per listed tool. They are assembled the way
        the tools of an assistant are (``ToolkitService.get_tools``), and the tool sees exactly the listed tools: the
        assembled list is cut to the names of those toolkits before it is handed to the tool, so nothing the assembly
        adds on its own is reachable.
        """
        from codemie.service.script_tool_calls.binding import bind_script_registries
        from codemie.service.tools import ToolkitService

        request = AssistantChatRequest(
            conversation_id=execution_id,
            text="",
            workflow_execution_id=execution_id,
            tools_config=[config for toolkit in assistant.toolkits for config in toolkit.get_tool_configs()],
        )
        tools = ToolkitService.get_tools(
            assistant=assistant,
            request=request,
            user=user,
            llm_model=assistant.llm_model_type or "",
            request_uuid=execution_id,
            exclude_extra_context_tools=True,
            request_headers=request_headers,
        )
        names = {tool.name for toolkit in assistant.toolkits for tool in toolkit.tools}
        step_tools = [tool for tool in tools if tool.name in names]
        # get_tools bound the registries to its whole list; bind again to the cut one.
        bind_script_registries(step_tools, assistant)
        return cls.find_tool(tool_config.tool, step_tools)

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
    def find_setting_for_tool(
        user: User,
        project_name: str,
        integration_alias: str,
        owner_user_id: str | None = None,
    ) -> SettingsBase:
        """Given integration alias, lookup for setting. Uses owner_user_id when provided (marketplace workflows)."""
        # To prevent circular import error
        from codemie.service.settings.settings import SettingsService
        from codemie.service.settings.base_settings import SearchFields

        lookup_user_id = owner_user_id or user.id

        setting = SettingsService.retrieve_setting(
            {
                SearchFields.USER_ID: lookup_user_id,
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
