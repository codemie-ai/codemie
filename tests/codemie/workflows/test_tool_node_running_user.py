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

"""Which user the ``ScriptToolRegistry`` of a workflow script step holds: the running user, never the creator.

Real ``ToolNode`` -> ``ToolsService.find_tool_from_config`` -> ``AgentWorkspaceToolkit`` -> ``ScriptToolRegistry``;
the persistence and the sandbox are the fakes of ``test_tool_node_script_step``. The user the registry holds is read
off the ``ScriptRunContext`` the script tool hands to the workspace service when it runs.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.workflow_models import WorkflowConfig, WorkflowNextState, WorkflowState, WorkflowTool
from codemie.rest_api.models.settings import SettingsBase, SettingType
from codemie.rest_api.security.user import User
from codemie.service.script_tool_calls.context import ProjectScope, ScriptRunContext, ScriptScopeKind
from codemie.service.settings.base_settings import SearchFields
from codemie.workflows.constants import CONTEXT_STORE_VARIABLE, MESSAGES_VARIABLE
from codemie.workflows.nodes.tool_node import ToolNode
from codemie_tools.base.models import CredentialTypes
from tests.codemie.workflows.test_tool_node_script_step import (  # noqa: F401  (fixtures are imported by name)
    EXECUTION_ID,
    PROJECT,
    _FakeBatchJobRunner,
    _FakeWorkspaceService,
    _write_file,
    catalog,
    workspace,
)

CREATOR = SimpleNamespace(user_id="creator-user", username="creator", name="Creator")
RUNNING_USER = User(id="running-user", username="runner")
TRIGGER_USER = User(id="trigger-user", username="webhook-trigger")
ALIAS = "pinned-integration"


def _node(user: User, *, is_global: bool, integration_alias: str | None = None) -> ToolNode:
    tool = WorkflowTool(
        id="script_step",
        tool="execute_workspace_script",
        tool_args={"script_path": "scripts/run.py"},
        integration_alias=integration_alias,
    )
    config = MagicMock(spec=WorkflowConfig)
    config.project = PROJECT
    config.tools = [tool]
    config.is_global = is_global
    config.created_by = CREATOR
    state = WorkflowState(
        id="script_step",
        task="Run the script",
        next=WorkflowNextState(state_id="next", output_key="out", store_in_context=True),
        tool_id="script_step",
    )
    return ToolNode(
        callbacks=[MagicMock()],
        workflow_execution_service=MagicMock(),
        thought_queue=MagicMock(),
        workflow_state=state,
        workflow_config=config,
        user=user,
        execution_id=EXECUTION_ID,
    )


def _registry_context(workspace_service: _FakeWorkspaceService, node: ToolNode) -> ScriptRunContext:
    """Run the step and return the context the script tool's registry produced."""
    _FakeBatchJobRunner.behaviours["scripts/run.py"] = _write_file("out/data.txt", b"x")
    with patch.object(node, "_is_execution_aborted", return_value=False):
        node({CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []})
    (run_context,) = workspace_service.run_contexts
    assert run_context is not None
    return run_context


@pytest.fixture
def workspace_service(request: pytest.FixtureRequest) -> _FakeWorkspaceService:
    """The in-memory workspace of ``test_tool_node_script_step`` (its ``workspace`` fixture, imported above)."""
    service: _FakeWorkspaceService = request.getfixturevalue("workspace")
    return service


@pytest.fixture
def pinned_setting_lookup() -> Iterator[MagicMock]:
    """The pinned alias resolves to a PROJECT-type setting, owned by the creator's project."""
    setting = SettingsBase(
        id="setting-1",
        project_name=PROJECT,
        alias=ALIAS,
        credential_type=CredentialTypes.JIRA,
        setting_type=SettingType.PROJECT,
    )
    with patch("codemie.service.settings.settings.SettingsService.retrieve_setting", return_value=setting) as lookup:
        yield lookup


def test_global_workflow_created_by_a_and_run_by_b_gives_the_registry_user_b(
    workspace_service: _FakeWorkspaceService,
) -> None:
    node = _node(RUNNING_USER, is_global=True)

    context = _registry_context(workspace_service, node)

    assert node._owner_user_id is None
    assert context.user.id == RUNNING_USER.id != CREATOR.user_id
    assert context.scope_kind is ScriptScopeKind.WORKFLOW


def test_pinned_integration_alias_resolves_the_setting_for_the_creator_but_the_registry_holds_the_running_user(
    workspace_service: _FakeWorkspaceService, pinned_setting_lookup: MagicMock
) -> None:
    node = _node(RUNNING_USER, is_global=True, integration_alias=ALIAS)

    context = _registry_context(workspace_service, node)

    assert node._owner_user_id == CREATOR.user_id
    # The creator is only the owner of the pinned setting lookup ...
    search_fields = pinned_setting_lookup.call_args.args[0]
    assert search_fields[SearchFields.USER_ID] == CREATOR.user_id
    assert search_fields[SearchFields.ALIAS] == ALIAS
    # ... the script tool, and what its scripts may call, run as the running user.
    assert context.user.id == RUNNING_USER.id
    assert context.scope_kind is ScriptScopeKind.WORKFLOW


def test_inner_tool_calls_of_a_pinned_step_resolve_as_the_running_user_in_the_workflow_project(
    workspace_service: _FakeWorkspaceService, pinned_setting_lookup: MagicMock
) -> None:
    """Reference for the follow-up: the documented reach of a script's tool calls from a tool step.

    ``ProjectScope`` resolves a catalog tool by name with no integration alias, so the pinned (creator-owned) PROJECT
    setting of the step itself is not threaded into it: the lookup is the automatic one for the running user and the
    workflow project, which is what can reach a PROJECT-type setting of that project.
    """
    context = _registry_context(workspace_service, _node(RUNNING_USER, is_global=True, integration_alias=ALIAS))
    assert isinstance(context.scope, ProjectScope)
    assistants = MagicMock(name="VirtualAssistantService")
    assistants.create_from_tool_invocation.return_value = MagicMock(id="virtual-1")
    tools = MagicMock(name="ToolsService")
    with (
        patch("codemie.service.assistant.VirtualAssistantService", assistants),
        patch("codemie.service.tools.ToolsService", tools),
        patch("codemie.service.tools.ToolkitService", MagicMock(name="ToolkitService")),
    ):
        assert context.scope.resolve("catalog_tool") is tools.find_tool_by_invoke_request.return_value

    # No alias, no owner id: the lookup user is the running user, the project the workflow's.
    assistants.create_from_tool_invocation.assert_called_once_with("catalog_tool", RUNNING_USER, PROJECT)
    _, _, _, user, project = tools.find_tool_by_invoke_request.call_args.args
    assert (user.id, project) == (RUNNING_USER.id, PROJECT)


def test_a_webhook_or_cron_run_gives_the_registry_the_trigger_user(workspace_service: _FakeWorkspaceService) -> None:
    # A webhook or cron run reaches the node with the trigger's user as the node's user; the creator is not involved.
    node = _node(TRIGGER_USER, is_global=False)

    context = _registry_context(workspace_service, node)

    assert context.user.id == TRIGGER_USER.id
    assert context.user.id != CREATOR.user_id
