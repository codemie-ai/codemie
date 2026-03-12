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
Provides functionality for validating the availability of resources defined in the Workflow Config

Resources to be vaidated:
1) Assistants defined in the `assistants` section
2) Tools defined in the `tools` section
3) Tools defined in the `assistants` section (`assistants/tools`)
4) Data sources defined for virtual assistants in the `assistants` secion (`assistants/datasource_ids)

Functons:
    validate_workflow_config_resources_availability(workflow_config: WorkflowConfig, user: User): Validates
    the availability of resources and raises the `WorkflowConfigResourcesValidationError` if not all
    resources are available

Exceptions:
    WorkflowConfigResourcesValidationError
"""

from codemie.core.workflow_models import WorkflowConfig, WorkflowTool
from codemie.rest_api.security.user import User
from codemie.rest_api.models.assistant import Assistant
from codemie.service.mcp.toolkit_service import MCPToolkitService
from codemie.service.tools.tool_service import ToolsService
from codemie.rest_api.models.index import IndexInfo
from codemie.workflows.constants import WorkflowErrorResourceType, WorkflowErrorType, WorkflowValidationError


class WorkflowConfigResourcesValidationError(Exception):
    @staticmethod
    def _format_list_message(title: str, items: list[str]) -> tuple[str, str]:
        return title, "\n".join(items) if items else ""

    @staticmethod
    def _format_map_message(title: str, items: list[tuple[str, str, str]]) -> tuple[str, str]:
        return title, "\n".join(f"{ref} -> {id} (in state: {state})" for ref, id, state in items) if items else ""

    def __init__(
        self,
        unavailable_assistants: list[tuple[str, str, str]],
        unavailable_tools: list[tuple[str, str, str]],
        unavailable_tools_from_assistants: list[str],
        unavailable_datasources: list[tuple[str, str, str]],
    ):
        self.unavailable_assistants = unavailable_assistants
        self.unavailable_tools = unavailable_tools
        self.unavailable_tools_from_assistants = unavailable_tools_from_assistants
        self.unavailable_datasources = unavailable_datasources

        self.messages = [
            WorkflowConfigResourcesValidationError._format_map_message(
                "Assistants do not exist", unavailable_assistants
            ),
            WorkflowConfigResourcesValidationError._format_map_message("Tools do not exist", unavailable_tools),
            WorkflowConfigResourcesValidationError._format_list_message(
                "Tools (referenced in assistant definitions) do not exist", unavailable_tools_from_assistants
            ),
            WorkflowConfigResourcesValidationError._format_map_message(
                "Data sources (referenced in assistant definitions) do not exist", unavailable_datasources
            ),
        ]
        message = [f"{title}:\n{details}" for title, details in self.messages if details]
        super().__init__("\n".join(message))

    def to_dict(self) -> dict:
        """Convert resource validation errors to structured dictionary format."""
        errors = []

        if self.unavailable_assistants:
            for ref, assistant_id, state_id in self.unavailable_assistants:
                errors.append(
                    WorkflowValidationError(
                        resource_type=WorkflowErrorResourceType.ASSISTANT.value,
                        resource_id=assistant_id,
                        reference_state=state_id,
                        message=f"Assistant '{assistant_id}' (referenced as '{ref}') does not exist",
                    ).model_dump(exclude_none=True)
                )

        if self.unavailable_tools:
            for ref, tool_id, state_id in self.unavailable_tools:
                errors.append(
                    WorkflowValidationError(
                        resource_type=WorkflowErrorResourceType.TOOL.value,
                        resource_id=tool_id,
                        reference_state=state_id,
                        message=f"Tool '{tool_id}' (referenced as '{ref}') does not exist",
                    ).model_dump(exclude_none=True)
                )

        if self.unavailable_tools_from_assistants:
            for tool in self.unavailable_tools_from_assistants:
                errors.append(
                    WorkflowValidationError(
                        resource_type=WorkflowErrorResourceType.TOOL_FROM_ASSISTANT.value,
                        resource_id=tool,
                        reference_state=None,
                        message=f"Tool '{tool}' (referenced in assistant definition) does not exist",
                    ).model_dump(exclude_none=True)
                )

        if self.unavailable_datasources:
            for datasource_id, assistant_ref, state_id in self.unavailable_datasources:
                errors.append(
                    WorkflowValidationError(
                        resource_type=WorkflowErrorResourceType.DATASOURCE.value,
                        resource_id=datasource_id,
                        reference_state=state_id,
                        message=f"Datasource '{datasource_id}' (used by assistant '{assistant_ref}') does not exist",
                    ).model_dump(exclude_none=True)
                )

        return {"error_type": WorkflowErrorType.RESOURCE_VALIDATION.value, "errors": errors}


def _extract_assistants(workflow_config: WorkflowConfig) -> list[tuple[str, str]]:
    assistants = [(assistant.id, assistant.assistant_id) for assistant in workflow_config.assistants or []]
    return assistants


def _find_states_referencing_assistant(workflow_config: WorkflowConfig, assistant_ref: str) -> list[str]:
    """Find all states that reference a given assistant by its reference ID."""
    states = []
    for state in workflow_config.states or []:
        if hasattr(state, 'assistant_id') and state.assistant_id == assistant_ref:
            states.append(state.id)
    return states


def _find_states_referencing_tool(workflow_config: WorkflowConfig, tool_ref: str) -> list[str]:
    """Find all states that reference a given tool by its reference ID."""
    states = []
    for state in workflow_config.states or []:
        if hasattr(state, 'tool_id') and state.tool_id == tool_ref:
            states.append(state.id)
    return states


def _extract_tools_from_assistants(workflow_config: WorkflowConfig) -> list[str]:
    assistants = workflow_config.assistants or []
    tools = [tool.name for assistant in assistants for tool in (assistant.tools or [])]
    return list(set(tools))


def _extract_datasources(workflow_config: WorkflowConfig) -> dict[str, list[str]]:
    """Extract datasources and map them to assistant refs that use them."""
    assistants = workflow_config.assistants or []
    datasource_to_assistants: dict[str, list[str]] = {}
    for assistant in assistants:
        for datasource_id in assistant.datasource_ids or []:
            if datasource_id not in datasource_to_assistants:
                datasource_to_assistants[datasource_id] = []
            datasource_to_assistants[datasource_id].append(assistant.id)
    return datasource_to_assistants


def _is_assistant_available(assistant_id: str, user: User) -> bool:
    assistants = Assistant.get_by_ids(user, [assistant_id])
    return len(assistants) > 0


def _is_tool_available(workflow_config: WorkflowConfig, tool: str | WorkflowTool, user: User) -> bool:
    try:
        if isinstance(tool, WorkflowTool) and tool.mcp_server:
            if tool.mcp_server.resolve_dynamic_values_in_arguments:
                # we cannot validate dynamic MCP servers
                return True
            mcp_tools = MCPToolkitService.get_mcp_server_tools(
                mcp_servers=[tool.mcp_server],
                user_id=user.id,
                project_name=workflow_config.project,
                conversation_id=workflow_config.id,  # we should reuse the tools from the same workflow_config
            )
            tool = next((mcp_tool for mcp_tool in mcp_tools if mcp_tool.name == tool.tool), None)

            return tool is not None
        else:
            toolkit = ToolsService.find_toolkit_for_tool(user, tool.tool if isinstance(tool, WorkflowTool) else tool)
            return toolkit is not None
    except ValueError:
        return False


def _is_datasource_available(datasource_id) -> bool:
    try:
        datasource = IndexInfo.get_by_id(id_=datasource_id)
        return datasource is not None
    except KeyError:
        return False


def _validate_assistants_availability(workflow_config: WorkflowConfig, user: User) -> list[tuple[str, str, str]]:
    assistants = _extract_assistants(workflow_config)
    unavailable_assistants = []

    for ref, assistant_id in assistants:
        if assistant_id is not None and not _is_assistant_available(assistant_id, user):
            states = _find_states_referencing_assistant(workflow_config, ref)
            for state_id in states:
                unavailable_assistants.append((ref, assistant_id, state_id))

    return unavailable_assistants


def _validate_tools_from_assistants_availability(workflow_config: WorkflowConfig, user) -> list[str]:
    tools = _extract_tools_from_assistants(workflow_config)
    unavailable_tools = [tool for tool in tools if not _is_tool_available(workflow_config, tool, user)]
    return unavailable_tools


def _validate_tools_avaiability(workflow_config: WorkflowConfig, user: User) -> list[tuple[str, str, str]]:
    tools = workflow_config.tools
    unavailable_tools = []

    for tool in tools:
        if not _is_tool_available(workflow_config, tool, user):
            states = _find_states_referencing_tool(workflow_config, tool.id)
            for state_id in states:
                unavailable_tools.append((tool.id, tool.tool, state_id))

    return unavailable_tools


def _validate_datasources_availability(workflow_config: WorkflowConfig) -> list[tuple[str, str, str]]:
    """Validate datasources and return tuples of (datasource_id, assistant_ref, state_id)."""
    datasource_to_assistants = _extract_datasources(workflow_config)
    unavailable_datasources = []

    for datasource_id, assistant_refs in datasource_to_assistants.items():
        if _is_datasource_available(datasource_id):
            continue

        for assistant_ref in assistant_refs:
            states = _find_states_referencing_assistant(workflow_config, assistant_ref)
            for state_id in states:
                unavailable_datasources.append((datasource_id, assistant_ref, state_id))

    return unavailable_datasources


def validate_workflow_config_resources_availability(workflow_config: WorkflowConfig, user: User):
    """
    Validates the availability of resources required by the workflow configuration.

    This function checks whether all assistants, tools, tools used by assistants,
    and data sources referenced in the workflow configuration are available to the
    specified user. If any required resources are unavailable, a
    `WorkflowConfigResourcesValidationError` is raised.

    Args:
        workflow_config (WorkflowConfig): The workflow configuration to validate.
        user (User): The user executing the workflow, used to check resource permissions
                     and availability.

    Raises:
        WorkflowConfigResourcesValidationError: If one or more required resources
                                                (assistants, tools, tools used by
                                                assistants, or data sources) are
                                                unavailable.
    """
    unavailable_assistants = _validate_assistants_availability(workflow_config, user)
    unavailable_tools = _validate_tools_avaiability(workflow_config, user)
    unavailable_tools_from_assistants = _validate_tools_from_assistants_availability(workflow_config, user)
    unavailable_datasources = _validate_datasources_availability(workflow_config)

    unavailable_resources = (
        unavailable_assistants,
        unavailable_tools,
        unavailable_tools_from_assistants,
        unavailable_datasources,
    )

    if any(unavailable_resources):
        raise WorkflowConfigResourcesValidationError(*unavailable_resources)
