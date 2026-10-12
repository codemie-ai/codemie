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

"""Save-time validation of `tool_access` on workflow tool steps that take it (today the workspace script tool).

Split out of `resources.py`: the entry checks, the structured error rows and the owner-integration warning
all belong to this one feature."""

from typing import NamedTuple

from codemie.core.workflow_models import WorkflowConfig
from codemie.configs.logger import logger
from codemie.rest_api.security.user import User
from codemie.service.tools.tool_metadata_service import ToolMetadataService
from codemie.service.tools.tool_service import ToolsService
from codemie_tools.base.tool_registry import can_be_listed_for, takes_tool_access


def _step_ids_of_tool(workflow_config: WorkflowConfig, tool_ref: str) -> list[str]:
    """States that run the tool `tool_ref`; the tool id itself when no state names it."""
    states = [state.id for state in workflow_config.states or [] if getattr(state, "tool_id", None) == tool_ref]
    return states or [tool_ref]


class ToolAccessProblem(NamedTuple):
    """One refused `tool_access` entry. `path` is `tool_access.<i>.name` for the tool, or
    `tool_access.<i>.integration_alias` for its alias, so a client can tell the row without parsing."""

    tool_id: str
    message: str
    state_id: str
    path: str = "tool_access"


def yaml_field_path(path: str) -> str:
    """`tool_access.0.integration_alias` -> `tool_access[0].integration_alias`."""
    result = ""
    for part in path.split("."):
        if part.isdigit():
            result += f"[{part}]"
        elif result:
            result += f".{part}"
        else:
            result = part
    return result


def _tool_access_problem(
    user: User, project_name: str, owner_user_id: str | None, host_tool: str, tool_name: str, alias: str | None
) -> tuple[str, str | None] | None:
    """Return why a step with the tool `host_tool` may not list `tool_name` with `alias`, or None when it may.

    The second item is the path tail of the entry part at fault: `integration_alias` for the alias, None for the
    tool itself."""
    try:
        toolkit = ToolsService.find_toolkit_for_tool(user, tool_name)
    except ValueError:
        return f"Tool '{tool_name}' does not exist", None
    # The host tool names the filter a listed tool must pass, in the same registry the runtime assembly uses; a tool the
    # registry does not know never passes, so it is refused here instead of passing the save and being left out later.
    if not can_be_listed_for(host_tool, tool_name):
        return f"Tool '{tool_name}' is not allowed in tool_access of '{host_tool}'", None
    if not alias:
        return None
    toolkit_name = toolkit.get("toolkit", "")
    if not ToolMetadataService.requires_credentials(tool_name, toolkit_name):
        return f"Tool '{tool_name}' takes no credentials, so integration alias '{alias}' is not allowed", (
            "integration_alias"
        )
    try:
        setting = ToolsService.find_setting_for_tool(
            user=user, project_name=project_name, integration_alias=alias, owner_user_id=owner_user_id
        )
    except ValueError:
        return (
            f"Integration alias '{alias}' for tool '{tool_name}' does not exist for the workflow creator",
            "integration_alias",
        )
    expected = ToolMetadataService.get_credential_type(ToolMetadataService.get_config_class(tool_name, toolkit_name))
    if expected is None:
        logger.warning(f"Cannot determine the credential type of tool '{tool_name}'; alias '{alias}' type not checked")
    elif _setting_credential_type(setting) != expected:
        return (
            f"Integration alias '{alias}' does not fit the credential type '{expected}' of tool '{tool_name}'",
            "integration_alias",
        )
    return None


def _setting_credential_type(setting) -> str | None:
    credential_type = getattr(setting, "credential_type", None)
    return getattr(credential_type, "value", credential_type)


def _tool_access_entry_problems(
    tool, access_list: list, user: User, project_name: str, owner_user_id: str | None
) -> list[tuple[str, str]]:
    """Return `(message, path)` for every refused entry of one step's `tool_access`."""
    if not takes_tool_access(tool.tool):
        return [(f"tool_access is not allowed on a step with the tool '{tool.tool}'", "tool_access")]
    problems: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, access in enumerate(access_list):
        entry_path = f"tool_access.{index}"
        if access.name in seen:
            problems.append((f"Tool '{access.name}' is listed more than once in tool_access", f"{entry_path}.name"))
            continue
        seen.add(access.name)
        problem = _tool_access_problem(
            user, project_name, owner_user_id, tool.tool, access.name, access.integration_alias
        )
        if problem:
            message, tail = problem
            problems.append((message, f"{entry_path}.{tail}" if tail else f"{entry_path}.name"))
    return problems


def validate_tool_access(workflow_config: WorkflowConfig, user: User) -> list[ToolAccessProblem]:
    """Validate `tool_access`. Aliases are the creator's."""
    created_by = getattr(workflow_config, "created_by", None)
    owner_user_id = getattr(created_by, "user_id", None) if created_by else None
    errors: list[ToolAccessProblem] = []

    for tool in workflow_config.tools or []:
        access_list = getattr(tool, "tool_access", None)
        if not isinstance(access_list, list) or not access_list:
            continue
        step_ids = _step_ids_of_tool(workflow_config, tool.id)
        for message, path in _tool_access_entry_problems(
            tool, access_list, user, workflow_config.project, owner_user_id
        ):
            errors.extend(
                ToolAccessProblem(tool.id, f"Step '{step_id}': {message}", step_id, path) for step_id in step_ids
            )
    return errors


def collect_tool_access_owner_integration_warnings(
    workflow_config: WorkflowConfig, treat_as_global: bool
) -> list[dict]:
    """Tell the publisher which script-step tools will run under the author's integrations.

    ``treat_as_global`` is the proposed state, not the stored flag: publishing flips ``is_global``
    only after validation, so the stored value would hide the very transition this warns about.
    Advisory only; never blocks.
    """
    if not treat_as_global:
        return []

    warnings: list[dict] = []
    for tool in workflow_config.tools or []:
        if not takes_tool_access(getattr(tool, "tool", None) or ""):
            continue
        access_list = getattr(tool, "tool_access", None)
        if not isinstance(access_list, list):
            continue
        step_ids = _step_ids_of_tool(workflow_config, tool.id)
        for access in access_list:
            alias = access.integration_alias
            if not alias:
                continue
            warnings.extend(
                {
                    "kind": "tool_access_owner_integration",
                    "step_id": step_id,
                    "tool_name": access.name,
                    "integration_alias": alias,
                    "message": (
                        f"Step '{step_id}': tool '{access.name}' runs under the workflow author's "
                        f"integration ({alias}) for everyone who runs this workflow."
                    ),
                }
                for step_id in step_ids
            )
    return warnings
