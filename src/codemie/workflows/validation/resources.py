# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

import uuid
import yaml
from codemie.core.workflow_models import WorkflowConfig, WorkflowTool
from codemie.rest_api.security.user import User
from codemie.rest_api.models.assistant import Assistant
from codemie.service.mcp.toolkit_service import MCPToolkitService
from codemie.service.tools.tool_service import ToolsService
from codemie.rest_api.models.index import IndexInfo
from codemie.workflows.constants import WorkflowErrorType
from codemie.workflows.validation.models import (
    WorkflowValidationErrorDetail,
    ToolMeta,
    ToolkitType,
)
from codemie.workflows.validation.line_lookup import YamlLineFinder, NullYamlLineFinder, extract_line_numbers
from codemie.configs.logger import logger


_MSG_RESOURCE_NOT_FOUND = "Resource not found"


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
        unavailable_tools_from_asst_integrations: list[tuple[str, str, str]],
        unavailable_datasources: list[tuple[str, str, str]],
        workflow_config_dict: dict = None,
        line_number_map: dict[str, int] = None,
        toolkits_metadata: list[dict] = None,
        invalid_integration_tools: list[tuple[str, str, str]] = None,
    ):
        self.unavailable_assistants = unavailable_assistants
        self.unavailable_tools = unavailable_tools
        self.unavailable_tools_from_asst_integrations = unavailable_tools_from_asst_integrations
        self.invalid_integration_tools = invalid_integration_tools or []
        self.unavailable_datasources = unavailable_datasources
        self.workflow_config_dict = workflow_config_dict or {}
        self.line_number_map = line_number_map or {}
        self.toolkits_metadata = toolkits_metadata or []

        # Initialize YamlLineFinder for line number lookups
        if workflow_config_dict and line_number_map:
            self.line_finder = YamlLineFinder(workflow_config_dict, line_number_map)
        else:
            self.line_finder = NullYamlLineFinder()

        # Format messages for string representation (for backward compatibility)
        self.messages = [
            WorkflowConfigResourcesValidationError._format_map_message(
                "Assistants do not exist", unavailable_assistants
            ),
            WorkflowConfigResourcesValidationError._format_map_message("Tools do not exist", unavailable_tools),
            WorkflowConfigResourcesValidationError._format_list_message(
                "Tools (referenced in assistant definitions) do not exist",
                [tool for tool, _, _ in unavailable_tools_from_asst_integrations]
                if unavailable_tools_from_asst_integrations
                and isinstance(unavailable_tools_from_asst_integrations[0], tuple)
                else unavailable_tools_from_asst_integrations,
            ),
            WorkflowConfigResourcesValidationError._format_map_message(
                "Data sources (referenced in assistant definitions) do not exist", unavailable_datasources
            ),
        ]
        message = [f"{title}:\n{details}" for title, details in self.messages if details]
        super().__init__("\n".join(message))

    def _build_tool_error_detail(self, _ref: str, tool_id: str, state_id: str) -> WorkflowValidationErrorDetail:
        if tool_id == "":  # Error is in the tool definition itself — missing tool field
            config_line = self.line_finder.find_line_for_tool_field(tool_ref=_ref, field_path="tool")
            return WorkflowValidationErrorDetail(
                id=str(uuid.uuid4()),
                message="Tool is required",
                details="Tool '' does not exist",
                state_id=state_id,
                path="tool",
                config_line=config_line,
            )
        # Error is in the state — referenced tool does not exist
        config_line = self.line_finder.find_line_for_state_field(state_id=state_id, field_path="tool_id")
        return WorkflowValidationErrorDetail(
            id=str(uuid.uuid4()),
            message=_MSG_RESOURCE_NOT_FOUND,
            details=f"Tool '{tool_id}' does not exist",
            state_id=state_id,
            path="tool_id",
            config_line=config_line,
        )

    def to_dict(self) -> dict:
        """Convert resource validation errors to structured dictionary format with line numbers."""
        errors = []

        if self.unavailable_assistants:
            for _ref, assistant_id, state_id in self.unavailable_assistants:
                # Find line number for assistant_id field in state
                config_line = self.line_finder.find_line_for_state_field(
                    state_id=state_id,
                    field_path="assistant_id",
                )

                # Build error using Pydantic model for type safety
                error_detail = WorkflowValidationErrorDetail(
                    id=str(uuid.uuid4()),
                    message=_MSG_RESOURCE_NOT_FOUND,
                    details=f"Assistant '{assistant_id}' does not exist",
                    state_id=state_id,
                    path="assistant_id",
                    config_line=config_line,
                )

                errors.append(error_detail.model_dump(exclude_none=True))

        if self.unavailable_tools:
            for _ref, tool_id, state_id in self.unavailable_tools:
                error_detail = self._build_tool_error_detail(_ref, tool_id, state_id)
                errors.append(error_detail.model_dump(exclude_none=True))

        if self.unavailable_tools_from_asst_integrations:
            for tool_name, assistant_ref, _ in self.unavailable_tools_from_asst_integrations:
                # Find line number for tools field in assistant definition
                config_line = self.line_finder.find_line_for_assistant_field(
                    assistant_ref=assistant_ref,
                    field_path="tools",
                )

                # Note: No state_id - this is an assistant-level error, not state-level
                error_detail = WorkflowValidationErrorDetail(
                    id=str(uuid.uuid4()),
                    message=_MSG_RESOURCE_NOT_FOUND,
                    details=f"Tool '{tool_name}' (referenced in assistant '{assistant_ref}') does not exist",
                    path="tools",
                    config_line=config_line,
                    meta=None,
                )

                errors.append(error_detail.model_dump(exclude_none=True))

        if self.invalid_integration_tools:
            for tool_name, assistant_ref, _ in self.invalid_integration_tools:
                config_line = self.line_finder.find_line_for_assistant_field(
                    assistant_ref=assistant_ref,
                    field_path="tools",
                )

                meta = self._find_tool_meta(tool_name)

                # Note: No state_id - this is an assistant-level error, not state-level
                error_detail = WorkflowValidationErrorDetail(
                    id=str(uuid.uuid4()),
                    message="Invalid integration settings",
                    details=f"Integration alias in tool '{tool_name}' does not exist",
                    path="tools",
                    config_line=config_line,
                    meta=meta,
                )

                errors.append(error_detail.model_dump(exclude_none=True))

        if self.unavailable_datasources:
            for datasource_id, assistant_ref, state_id in self.unavailable_datasources:
                # Find line number for datasource_ids field in assistant definition
                config_line = self.line_finder.find_line_for_assistant_field(
                    assistant_ref=assistant_ref,
                    field_path="datasource_ids",
                )

                # Build error using Pydantic model for type safety
                error_detail = WorkflowValidationErrorDetail(
                    id=str(uuid.uuid4()),
                    message=_MSG_RESOURCE_NOT_FOUND,
                    details=f"Datasource '{datasource_id}' (used by assistant '{assistant_ref}') does not exist",
                    state_id=state_id,
                    path="datasource_ids",
                    config_line=config_line,
                )

                errors.append(error_detail.model_dump(exclude_none=True))

        return {
            "error_type": WorkflowErrorType.RESOURCE_VALIDATION.value,
            "message": "Configuration references unavailable resources",
            "errors": errors,
        }

    def _find_tool_meta(self, tool_name: str) -> ToolMeta | None:
        """Find toolkit metadata for a tool name."""
        if not self.toolkits_metadata:
            return None

        for toolkit_meta in self.toolkits_metadata:
            for tool_meta in toolkit_meta.get("tools", []):
                if tool_meta.get("name") != tool_name:
                    continue

                toolkit_name = toolkit_meta.get("toolkit", "")
                is_external = toolkit_meta.get("is_external", False)
                toolkit_type = ToolkitType.EXTERNAL_TOOLS if is_external else ToolkitType.TOOLS

                return ToolMeta(toolkit_type=toolkit_type.value, toolkit_name=toolkit_name, tool_name=tool_name)
        return None


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


def _is_integration_alias_valid(user: User, project_name: str, integration_alias: str) -> bool:
    """Check if integration_alias references valid settings configuration."""
    try:
        ToolsService.find_setting_for_tool(user=user, project_name=project_name, integration_alias=integration_alias)
        return True
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


def _build_tool_error_tuples(
    workflow_config: WorkflowConfig, tool_name: str, assistant_refs: list[str]
) -> list[tuple[str, str, str]]:
    return [
        (tool_name, assistant_ref, state_id)
        for assistant_ref in assistant_refs
        for state_id in _find_states_referencing_assistant(workflow_config, assistant_ref)
    ]


def _build_tool_info_map(assistants: list) -> dict[tuple[str, str | None], list[str]]:
    tool_info_to_assistants: dict[tuple[str, str | None], list[str]] = {}
    for assistant in assistants:
        for tool in assistant.tools or []:
            tool_info_to_assistants.setdefault((tool.name, tool.integration_alias), []).append(assistant.id)
    return tool_info_to_assistants


def _validate_tools_from_assistants_availability(
    workflow_config: WorkflowConfig, user
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """
    Validate tools from assistants.

    Returns:
        Tuple of (unavailable_tools, invalid_integration_tools)
        - unavailable_tools: Tools that don't exist
        - invalid_integration_tools: Tools that exist but have invalid integration_alias
    """
    tool_info_to_assistants = _build_tool_info_map(workflow_config.assistants or [])
    unavailable_tools = []
    invalid_integration_tools = []

    for (tool_name, integration_alias), assistant_refs in tool_info_to_assistants.items():
        if not _is_tool_available(workflow_config, tool_name, user):
            unavailable_tools.extend(_build_tool_error_tuples(workflow_config, tool_name, assistant_refs))
            continue

        if integration_alias and not _is_integration_alias_valid(user, workflow_config.project, integration_alias):
            invalid_integration_tools.extend(_build_tool_error_tuples(workflow_config, tool_name, assistant_refs))

    return unavailable_tools, invalid_integration_tools


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
    unavailable_tools_from_asst_integrations, invalid_integration_tools = _validate_tools_from_assistants_availability(
        workflow_config, user
    )
    unavailable_datasources = _validate_datasources_availability(workflow_config)

    unavailable_resources = (
        unavailable_assistants,
        unavailable_tools,
        unavailable_tools_from_asst_integrations,
        unavailable_datasources,
    )

    if any(unavailable_resources) or invalid_integration_tools:
        # Get toolkits metadata for enriching error messages
        toolkits_metadata = []
        try:
            from codemie.service.tools.tools_info_service import ToolsInfoService

            toolkits_metadata = ToolsInfoService.get_tools_info(user=user)
        except Exception as e:
            logger.warning(f"Failed to get toolkits metadata: {e}")

        # Extract line numbers from YAML config for better error reporting
        workflow_config_dict = None
        line_number_map = None

        # Skip line number extraction if yaml_config is missing or not a string
        if not workflow_config.yaml_config or not isinstance(workflow_config.yaml_config, str):
            raise WorkflowConfigResourcesValidationError(
                *unavailable_resources,
                workflow_config_dict,
                line_number_map,
                toolkits_metadata,
                invalid_integration_tools,
            )

        try:
            line_number_map = extract_line_numbers(workflow_config.yaml_config)
            workflow_config_dict = yaml.safe_load(workflow_config.yaml_config)
        except Exception as e:
            # If extraction fails, continue without line numbers
            logger.warning(
                f"Failed to extract line numbers for resource validation errors: {e}. "
                f"Validation will continue without line number information."
            )

        raise WorkflowConfigResourcesValidationError(
            *unavailable_resources, workflow_config_dict, line_number_map, toolkits_metadata, invalid_integration_tools
        )
