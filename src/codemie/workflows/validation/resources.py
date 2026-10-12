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
from typing import Optional

import yaml
from codemie.core.workflow_models import WorkflowConfig, WorkflowTool
from codemie.service.workflow_service import WorkflowService
from codemie.rest_api.security.user import User
from codemie.rest_api.models.assistant import Assistant
from codemie.service.assistant.assistant_integration_validator import AssistantIntegrationValidator
from codemie.service.assistant.credential_validator import CredentialValidator
from codemie.service.mcp.toolkit_service import MCPToolkitService
from codemie.service.tools.tool_service import ToolsService
from codemie.rest_api.models.index import IndexInfo
from codemie.workflows.constants import WorkflowErrorType
from codemie.workflows.validation.models import (
    WorkflowValidationErrorDetail,
    ToolMeta,
    ToolkitType,
)
from codemie.workflows.validation.tool_access import (
    ToolAccessProblem,
    validate_tool_access,
    yaml_field_path,
)
from codemie.workflows.validation.line_lookup import YamlLineFinder, NullYamlLineFinder, extract_line_numbers
from codemie.configs.customer_config import customer_config
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
        missing_integration_tools: list[tuple[str, str, str, str]] = None,
        unavailable_sub_workflows: list[tuple[str, str, str]] = None,
        invalid_tool_access: list[ToolAccessProblem] = None,
    ):
        self.unavailable_assistants = unavailable_assistants
        self.unavailable_tools = unavailable_tools
        self.unavailable_tools_from_asst_integrations = unavailable_tools_from_asst_integrations
        self.invalid_integration_tools = invalid_integration_tools or []
        self.missing_integration_tools = missing_integration_tools or []
        self.unavailable_datasources = unavailable_datasources
        self.unavailable_sub_workflows = unavailable_sub_workflows or []
        self.invalid_tool_access = invalid_tool_access or []
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
            WorkflowConfigResourcesValidationError._format_list_message(
                "Tools (referenced in assistant definitions) require missing integrations",
                [tool for tool, *_ in (missing_integration_tools or [])],
            ),
            WorkflowConfigResourcesValidationError._format_list_message(
                "Tool access is invalid",
                [problem.message for problem in self.invalid_tool_access],
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
        errors: list[dict] = []
        errors.extend(self._build_unavailable_assistant_errors())
        errors.extend(self._build_unavailable_tool_errors())
        errors.extend(self._build_unavailable_assistant_tool_errors())
        errors.extend(self._build_invalid_integration_errors())
        errors.extend(self._build_missing_integration_errors())
        errors.extend(self._build_unavailable_datasource_errors())
        errors.extend(self._build_invalid_tool_access_errors())

        return {
            "error_type": WorkflowErrorType.RESOURCE_VALIDATION.value,
            "message": "Configuration references unavailable resources",
            "errors": errors,
        }

    def _build_unavailable_assistant_errors(self) -> list[dict]:
        errors = []
        for _ref, assistant_id, state_id in self.unavailable_assistants or []:
            config_line = self.line_finder.find_line_for_state_field(
                state_id=state_id,
                field_path="assistant_id",
            )
            error_detail = WorkflowValidationErrorDetail(
                id=str(uuid.uuid4()),
                message=_MSG_RESOURCE_NOT_FOUND,
                details=f"Assistant '{assistant_id}' does not exist",
                state_id=state_id,
                path="assistant_id",
                config_line=config_line,
            )
            errors.append(error_detail.model_dump(exclude_none=True))
        return errors

    def _build_unavailable_tool_errors(self) -> list[dict]:
        errors = []
        for _ref, tool_id, state_id in self.unavailable_tools or []:
            error_detail = self._build_tool_error_detail(_ref, tool_id, state_id)
            errors.append(error_detail.model_dump(exclude_none=True))
        return errors

    def _build_unavailable_assistant_tool_errors(self) -> list[dict]:
        errors = []
        for tool_name, assistant_ref, _ in self.unavailable_tools_from_asst_integrations or []:
            # Assistant-level error: no state_id.
            config_line = self.line_finder.find_line_for_assistant_field(
                assistant_ref=assistant_ref,
                field_path="tools",
            )
            error_detail = WorkflowValidationErrorDetail(
                id=str(uuid.uuid4()),
                message=_MSG_RESOURCE_NOT_FOUND,
                details=f"Tool '{tool_name}' (referenced in assistant '{assistant_ref}') does not exist",
                path="tools",
                config_line=config_line,
                meta=None,
            )
            errors.append(error_detail.model_dump(exclude_none=True))
        return errors

    def _build_invalid_integration_errors(self) -> list[dict]:
        errors = []
        for tool_name, assistant_ref, _ in self.invalid_integration_tools or []:
            config_line = self.line_finder.find_line_for_assistant_field(
                assistant_ref=assistant_ref,
                field_path="tools",
            )
            meta = self._find_tool_meta(tool_name)
            # Assistant-level error: no state_id.
            error_detail = WorkflowValidationErrorDetail(
                id=str(uuid.uuid4()),
                message="Invalid integration settings",
                details=f"Integration alias in tool '{tool_name}' does not exist",
                path="tools",
                config_line=config_line,
                meta=meta,
            )
            errors.append(error_detail.model_dump(exclude_none=True))
        return errors

    def _build_missing_integration_errors(self) -> list[dict]:
        if not self.missing_integration_tools:
            return []

        errors = []
        for (assistant_ref, credential_type), tool_names in self._group_missing_integrations().items():
            config_line = self.line_finder.find_line_for_assistant_field(
                assistant_ref=assistant_ref,
                field_path="tools",
            )
            meta = self._find_tool_meta(tool_names[0])
            tools_list = ", ".join(f"'{name}'" for name in tool_names)
            # Assistant-level error: no state_id.
            error_detail = WorkflowValidationErrorDetail(
                id=str(uuid.uuid4()),
                message="Missing required integration",
                details=(
                    f"Assistant '{assistant_ref}' has tool(s) requiring the "
                    f"'{credential_type}' integration which is not configured: {tools_list}"
                ),
                path="tools",
                config_line=config_line,
                meta=meta,
            )
            errors.append(error_detail.model_dump(exclude_none=True))
        return errors

    def _group_missing_integrations(self) -> dict[tuple[str, str], list[str]]:
        """Group missing-integration tools by (assistant_ref, credential_type).

        Tools sharing the same integration collapse into a single issue, while tools
        requiring different integrations are reported separately.
        """
        grouped: dict[tuple[str, str], list[str]] = {}
        for tool_name, assistant_ref, _state_id, credential_type in self.missing_integration_tools:
            key = (assistant_ref, credential_type or "Unknown")
            tool_names = grouped.setdefault(key, [])
            if tool_name not in tool_names:
                tool_names.append(tool_name)
        return grouped

    def _build_invalid_tool_access_errors(self) -> list[dict]:
        errors = []
        for problem in self.invalid_tool_access:
            error_detail = WorkflowValidationErrorDetail(
                id=str(uuid.uuid4()),
                message="Invalid tool access",
                details=problem.message,
                state_id=problem.state_id,
                path=problem.path,
                config_line=self.line_finder.find_line_for_tool_field(
                    tool_ref=problem.tool_id, field_path=yaml_field_path(problem.path)
                ),
            )
            errors.append(error_detail.model_dump(exclude_none=True))
        return errors

    def _build_unavailable_datasource_errors(self) -> list[dict]:
        errors = []
        for datasource_id, assistant_ref, state_id in self.unavailable_datasources or []:
            config_line = self.line_finder.find_line_for_assistant_field(
                assistant_ref=assistant_ref,
                field_path="datasource_ids",
            )
            error_detail = WorkflowValidationErrorDetail(
                id=str(uuid.uuid4()),
                message=_MSG_RESOURCE_NOT_FOUND,
                details=f"Datasource '{datasource_id}' (used by assistant '{assistant_ref}') does not exist",
                state_id=state_id,
                path="datasource_ids",
                config_line=config_line,
            )
            errors.append(error_detail.model_dump(exclude_none=True))
        return errors

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


def _tool_requires_missing_integration(
    user: User, project_name: str, tool_name: str, integration_alias: str | None
) -> tuple[bool, str | None]:
    """
    Check whether a tool requires an integration that is not configured.

    Delegates to `CredentialValidator.validate_tool_credentials`, which mirrors how the
    tool's credentials are resolved at runtime: an explicit `integration_alias` is
    resolved to its settings, otherwise stored project/user credentials are looked up.

    A missing `integration_alias` is NOT an error on its own - it is the "Automatic
    Credentials Lookup" mode, in which the tool resolves an integration automatically.
    The integration is reported as missing only when the credentials cannot be resolved
    at all, i.e. exactly the situation in which the tool would fail at runtime.

    Returns a tuple `(is_missing, credential_type)`. The credential_type is normalised:
    if `CredentialValidator` returns `None`, the toolkit name is used as a fallback so the
    user-facing message names a real integration. Cases reported elsewhere (non-existent
    tool, invalid integration alias) return `(False, None)`.
    """
    try:
        toolkit = ToolsService.find_toolkit_for_tool(user, tool_name)
    except ValueError:
        # Tool does not exist - reported separately by availability validation
        return False, None

    # find_toolkit_for_tool always returns a toolkit dict or raises ValueError.
    toolkit_name = toolkit.get("toolkit", "")

    tool_settings = None
    if integration_alias:
        try:
            tool_settings = ToolsService.find_setting_for_tool(
                user=user, project_name=project_name, integration_alias=integration_alias
            )
        except ValueError:
            # Invalid alias - reported separately by _is_integration_alias_valid
            return False, None

    try:
        result = CredentialValidator.validate_tool_credentials(
            toolkit_name=toolkit_name,
            tool_name=tool_name,
            user=user,
            project_name=project_name,
            tool_settings=tool_settings,
            assistant_id=None,
        )
        # Fall back to the toolkit name when the validator does not surface a
        # specific credential type; "Unknown" is reserved for the case where we
        # have nothing meaningful to display.
        credential_type = result.credential_type or toolkit_name or "Unknown"
        return (not result.is_valid), credential_type
    except Exception as e:
        logger.warning(f"Failed to validate integration for tool '{tool_name}': {e}")
        return False, None


def _validate_referenced_assistants_integrations(
    workflow_config: WorkflowConfig, user: User
) -> list[tuple[str, str, str, str]]:
    """
    Validate integrations for referenced (existing) assistants used in the workflow.

    For every assistant referenced by `assistant_id`, the referenced assistant is
    loaded and its toolkits - as well as the toolkits of its sub-assistants
    (orchestrator pattern) - are checked for integrations that are not configured.

    Returns:
        List of tuples (tool_name, assistant_ref, state_id, credential_type) for tools
        whose required integration is missing.
    """
    missing_integration_tools: list[tuple[str, str, str, str]] = []
    for assistant in workflow_config.assistants or []:
        missing_integration_tools.extend(_collect_missing_for_referenced_assistant(workflow_config, user, assistant))
    return missing_integration_tools


def _collect_missing_for_referenced_assistant(
    workflow_config: WorkflowConfig, user: User, assistant
) -> list[tuple[str, str, str, str]]:
    """Resolve missing integrations for a single referenced assistant in the workflow."""
    if not assistant.assistant_id:
        return []

    db_assistant = _load_referenced_assistant(user, assistant.assistant_id)
    if db_assistant is None:
        # Non-existent assistant - reported separately by availability validation.
        return []

    missing_integrations = _safely_collect_missing_integrations(db_assistant, user, workflow_config.project)
    # A slot the author left to the consumer resolves per user at execution time: the person running
    # the workflow picks their own integration or gets one through automatic lookup. The author not
    # owning such an integration is therefore not a configuration error and must not block the save.
    missing_integrations = [
        missing for missing in missing_integrations if not _is_slot_left_to_the_user(db_assistant, missing.tool, user)
    ]
    if not missing_integrations:
        return []

    states = _find_states_referencing_assistant(workflow_config, assistant.id)
    return [
        # Fall back to the toolkit name so the user-facing message never shows "Unknown"
        # when MissingIntegration carries a toolkit but no resolved credential_type.
        (missing.tool, assistant.id, state_id, missing.credential_type or missing.toolkit or "Unknown")
        for missing in missing_integrations
        for state_id in states
    ]


def collect_consumer_slot_integration_warnings(workflow_config: WorkflowConfig, user: User) -> list[dict]:
    """Report slots whose integration depends on whoever runs the workflow.

    These never block saving: the slot is offered in "Your Integration Settings" and resolved per
    user, so the author having no such integration is legitimate. The author is still told about it,
    because for users without one the tool will report a missing integration at runtime.
    """
    warnings: list[dict] = []

    for assistant in workflow_config.assistants or []:
        if not getattr(assistant, "assistant_id", None):
            continue

        db_assistant = _load_referenced_assistant(user, assistant.assistant_id)
        if db_assistant is None:
            continue

        for missing in _safely_collect_missing_integrations(db_assistant, user, workflow_config.project) or []:
            if not _is_slot_left_to_the_user(db_assistant, missing.tool, user):
                continue

            warnings.append(
                {
                    "kind": "consumer_slot",
                    "assistant_ref": assistant.id,
                    "tool_name": missing.tool,
                    "credential_type": missing.credential_type or missing.toolkit or "Unknown",
                }
            )

    return warnings


def _is_slot_left_to_the_user(db_assistant, tool_name: str, user: User = None) -> bool:
    """Whether the assistant leaves this tool's integration to the consuming user.

    True whenever the author did not pin an integration for the slot. Such a slot is offered in
    "Your Integration Settings", so the person running the workflow can pick their own integration —
    and with automatic credentials lookup enabled one is resolved for them without any picking. In
    both cases the workflow author's own integrations say nothing about whether the slot will work,
    so a missing one is worth a warning, never a blocked save.

    Sub-assistants are searched too, because missing integrations are collected for them as well.
    A missing integration only carries the tool name, so when a parent and a sub-assistant expose a
    tool of the same name the first match decides — the parent's own slot.
    """
    own = _slot_is_unpinned(db_assistant, tool_name)
    if own is not None:
        return own

    for sub_assistant_id in getattr(db_assistant, "assistant_ids", None) or []:
        sub_assistant = _load_referenced_assistant(user, sub_assistant_id)
        if sub_assistant is None:
            continue
        sub = _slot_is_unpinned(sub_assistant, tool_name)
        if sub is not None:
            return sub

    return False


def _slot_is_unpinned(db_assistant, tool_name: str) -> Optional[bool]:
    """Whether this assistant's own slot for ``tool_name`` is unpinned; None when it has no such slot."""
    for toolkit in getattr(db_assistant, "toolkits", None) or []:
        toolkit_pinned = bool(getattr(toolkit, "settings", None))

        for tool in getattr(toolkit, "tools", None) or []:
            if getattr(tool, "name", None) != tool_name:
                continue

            return not (toolkit_pinned or bool(getattr(tool, "settings", None)))

    return None


def _load_referenced_assistant(user: User, assistant_id: str):
    """Load a persisted assistant by id, returning None on failure or absence."""
    try:
        db_assistants = Assistant.get_by_ids(user, [assistant_id])
    except Exception as e:
        logger.warning(f"Failed to load assistant '{assistant_id}' for integration validation: {e}")
        return None
    return db_assistants[0] if db_assistants else None


def _safely_collect_missing_integrations(db_assistant, user: User, project_name: str):
    """Collect missing integrations for a persisted assistant, swallowing transient errors."""
    try:
        return AssistantIntegrationValidator.collect_missing_integrations(db_assistant, user, project_name)
    except Exception as e:
        logger.warning(f"Failed to validate integrations for assistant '{db_assistant.id}': {e}")
        return []


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
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]], list[tuple[str, str, str, str]]]:
    """
    Validate tools from assistants.

    Returns:
        Tuple of (unavailable_tools, invalid_integration_tools, missing_integration_tools)
        - unavailable_tools: Tools that don't exist
        - invalid_integration_tools: Tools that exist but have an invalid integration_alias
        - missing_integration_tools: Tools that require an integration that is not configured
          (each entry carries the credential_type as a 4th element for grouping)
    """
    tool_info_to_assistants = _build_tool_info_map(workflow_config.assistants or [])
    # The model does not enforce mutual exclusivity between `assistant_id` and inline
    # `tools`. Referenced assistants are validated separately via
    # `_validate_referenced_assistants_integrations`, which loads their toolkits from
    # the DB; running the missing-integration check on the same tool here too would
    # surface a duplicate issue. Restrict the missing-integration check to inline
    # (assistant_id-less) assistants. Existence and alias-validity checks still apply
    # to all references for backward compatibility.
    inline_assistant_refs = {
        assistant.id for assistant in (workflow_config.assistants or []) if not assistant.assistant_id
    }

    unavailable_tools = []
    invalid_integration_tools = []
    missing_integration_tools = []

    for (tool_name, integration_alias), assistant_refs in tool_info_to_assistants.items():
        if not _is_tool_available(workflow_config, tool_name, user):
            unavailable_tools.extend(_build_tool_error_tuples(workflow_config, tool_name, assistant_refs))
            continue

        if integration_alias and not _is_integration_alias_valid(user, workflow_config.project, integration_alias):
            invalid_integration_tools.extend(_build_tool_error_tuples(workflow_config, tool_name, assistant_refs))
            continue

        inline_refs = [ref for ref in assistant_refs if ref in inline_assistant_refs]
        if not inline_refs:
            continue

        is_missing, credential_type = _tool_requires_missing_integration(
            user, workflow_config.project, tool_name, integration_alias
        )
        if is_missing:
            missing_integration_tools.extend(
                (*error_tuple, credential_type)
                for error_tuple in _build_tool_error_tuples(workflow_config, tool_name, inline_refs)
            )

    return unavailable_tools, invalid_integration_tools, missing_integration_tools


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


def _validate_sub_workflow_availability(workflow_config: WorkflowConfig, user: User) -> list[tuple[str, str, str]]:
    """Validate sub-workflow references in states; return tuples of (state_id, workflow_id, reason)."""
    if not customer_config.is_feature_enabled("subWorkflow"):
        return []
    unavailable = []
    for state in workflow_config.states or []:
        workflow_id = getattr(state, "workflow_id", None)
        if not workflow_id:
            continue
        if workflow_id == str(workflow_config.id):
            unavailable.append((state.id, workflow_id, "self-reference not allowed"))
            continue
        try:
            sub_wf = WorkflowService().get_workflow(workflow_id, user)
            if sub_wf is None:
                unavailable.append((state.id, workflow_id, "not found"))
        except Exception:
            unavailable.append((state.id, workflow_id, "not found or access denied"))
    return unavailable


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
    # Lines are reported against the text the client sent, not the normalized YAML rewritten from the objects.
    line_source_yaml = getattr(workflow_config, "_source_yaml_config", None) or workflow_config.yaml_config
    unavailable_assistants = _validate_assistants_availability(workflow_config, user)
    unavailable_tools = _validate_tools_avaiability(workflow_config, user)
    (
        unavailable_tools_from_asst_integrations,
        invalid_integration_tools,
        missing_integration_tools,
    ) = _validate_tools_from_assistants_availability(workflow_config, user)
    missing_integration_tools.extend(_validate_referenced_assistants_integrations(workflow_config, user))
    unavailable_datasources = _validate_datasources_availability(workflow_config)
    unavailable_sub_workflows = _validate_sub_workflow_availability(workflow_config, user)
    invalid_tool_access = validate_tool_access(workflow_config, user)

    unavailable_resources = (
        unavailable_assistants,
        unavailable_tools,
        unavailable_tools_from_asst_integrations,
        unavailable_datasources,
    )

    if (
        any(unavailable_resources)
        or invalid_integration_tools
        or missing_integration_tools
        or unavailable_sub_workflows
        or invalid_tool_access
    ):
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
        if not line_source_yaml or not isinstance(line_source_yaml, str):
            raise WorkflowConfigResourcesValidationError(
                *unavailable_resources,
                workflow_config_dict,
                line_number_map,
                toolkits_metadata,
                invalid_integration_tools,
                missing_integration_tools,
                unavailable_sub_workflows,
                invalid_tool_access,
            )

        try:
            line_number_map = extract_line_numbers(line_source_yaml)
            workflow_config_dict = yaml.safe_load(line_source_yaml)
        except Exception as e:
            # If extraction fails, continue without line numbers
            logger.warning(
                f"Failed to extract line numbers for resource validation errors: {e}. "
                f"Validation will continue without line number information."
            )

        raise WorkflowConfigResourcesValidationError(
            *unavailable_resources,
            workflow_config_dict,
            line_number_map,
            toolkits_metadata,
            invalid_integration_tools,
            missing_integration_tools,
            unavailable_sub_workflows,
            invalid_tool_access,
        )
