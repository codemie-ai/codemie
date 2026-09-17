# EPMCDME-14138: Thread execution_id into ToolsService.find_tool_from_config Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bare (non-assistant-mediated) workflow tool states must share one `AgentWorkspace` per workflow execution instead of each invocation minting a fresh random-UUID workspace, so a later bare tool state (e.g. `execute_workspace_script`) can read a file an earlier bare tool state (e.g. `write_workspace_file`) wrote in the same execution.

**Architecture:** `ToolNode._execute_regular_tool` already carries `self.execution_id` (set from `kwargs["execution_id"]` at construction) and already forwards it correctly on the sibling MCP path (`_execute_mcp_tool`, `conversation_id=self.execution_id`). The regular-toolkit path does not: it calls `ToolsService.find_tool_from_config` without `execution_id`, so the classmethod builds `AssistantChatRequest(tools_config=config)` with no `conversation_id`, and the model's `default_factory=lambda: str(uuid.uuid4())` mints a new random id every call. `ToolkitSettingService.get_agent_workspace_toolkit` reads `request.conversation_id` and returns `[]` when it's falsy — today this silently drops the `AgentWorkspace` toolkit from the bare path entirely; once threaded, the toolkit also becomes active in production for bare tool states for the first time. The fix threads `execution_id` through two call sites, mirroring the existing MCP pattern exactly, with no change to `AssistantChatRequest`'s own model definition.

**Tech Stack:** Python 3.11+, FastAPI backend, Pydantic v2 models, pytest + unittest.mock, Poetry-managed dependencies. No UI involved.

**Spec:** No `spec.md` exists for this task (`sdlc-light` flow — research-first, straight to plan, no brainstorming ceremony). Grounding documents: `docs/superpowers/tasks/2026-08-21-bare-tool-state-conversation-id/technical-analysis.md` (codebase research) and the ticket text below (informal requirements input; `complexity-assessment.json` in this directory is informal context only, not a gate, per the user's decision to use `sdlc-light`).

**Ticket (EPMCDME-14138):** Bare, non-assistant-mediated tool states create a new empty `AgentWorkspace` for each invocation because `ToolsService.find_tool_from_config` builds `AssistantChatRequest` without `conversation_id`. Steps to reproduce: run `write_workspace_file` as one bare tool state, then `execute_workspace_script` as a later bare tool state in the same execution — the second state fails with `Script file 'X' was not provided to the execution workspace`. Acceptance criteria: `execution_id` is passed into `ToolsService.find_tool_from_config`; the constructed `AssistantChatRequest` uses `conversation_id=execution_id`; bare tool states in one execution share the same workspace; a regression test covers `write_workspace_file` followed by `execute_workspace_script`.

## Global Constraints

- `AssistantChatRequest.conversation_id` (`src/codemie/core/models.py:567`) is typed `str` with `default_factory=lambda: str(uuid.uuid4())` — it is NOT `Optional[str]`. Do not pass `conversation_id=None` explicitly and do not change the model's own definition or default.
- `execution_id` must be an optional keyword argument (`execution_id: str | None = None`) on `find_tool_from_config` so any caller that omits it keeps the existing random-UUID default behavior unchanged (backward compatible; only one caller exists today — `ToolNode._execute_regular_tool` — confirmed by grep across the whole codebase).
- Follow the existing MCP reference pattern exactly: `_execute_mcp_tool` already passes `conversation_id=self.execution_id` at `src/codemie/workflows/nodes/tool_node.py:138`.
- Test naming: new tests in `tests/codemie/workflows/test_tool_node_context.py` continue the file's `test_tc_tnc_NNN_<description>` numbering — the highest existing number is `018`, so the next test is `test_tc_tnc_019_...`. New tests elsewhere use plain descriptive names (no `tc_` prefix), matching the convention already used in `tests/codemie/service/tools/test_tool_service_owner_user_id.py` and `tests/codemie/service/tools/test_toolkit_settings_service.py`.
- Mock only the collaborator boundary being tested (per `.ai-run/guides/testing/testing-patterns.md`'s Seam Tests rule) — do not mock the entire `ToolsService` class when the test's purpose is to exercise `find_tool_from_config`'s own body.
- Git side effects (commits) only happen because the user is running the `sdlc-light` SDLC flow, which commits per task/group as part of its Stage 4 contract — not because commits are being taken as an unrelated liberty.

---

### Task 1: Thread `execution_id` through `ToolsService.find_tool_from_config`

**Files:**
- Modify: `src/codemie/service/tools/tool_service.py:84-112` (`find_tool_from_config` classmethod)
- Test: `tests/codemie/service/tools/test_find_tool_from_config_execution_id.py` (new file)

**Interfaces:**
- Consumes: `AssistantChatRequest` from `codemie.core.models` (already imported in `tool_service.py`); `ToolkitService.get_core_tools` (already imported locally inside the method via `from codemie.service.tools import ToolkitService`); `ToolsService.get_toolkit_from_workflow_tool_config` (existing classmethod on the same class, used as the mockable seam for this test).
- Produces: `find_tool_from_config(cls, tool_config, toolkits, assistant, user, project_name, owner_user_id=None, execution_id=None) -> object` — the new `execution_id` kwarg is consumed by Task 2's `ToolNode._execute_regular_tool` call site.

Test-first: yes — write `test_find_tool_from_config_threads_execution_id_as_conversation_id` and `test_find_tool_from_config_defaults_conversation_id_when_execution_id_omitted` against the *current* (unfixed) method first; both must fail before the fix (the first because `AssistantChatRequest.conversation_id` will be a random UUID instead of `"exec_123"`; the second is a sanity check that should already pass and must keep passing after the fix).

- [ ] **Step 1: Write the failing test file**

```python
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

"""Unit tests for ToolsService.find_tool_from_config execution_id -> conversation_id threading."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.models import AssistantChatRequest
from codemie.rest_api.security.user import User
from codemie.service.tools.tool_service import ToolsService


@pytest.fixture
def mock_user() -> User:
    user = MagicMock(spec=User)
    user.id = "executor-user-id"
    return user


@pytest.fixture
def mock_assistant():
    assistant = MagicMock()
    assistant.id = "assistant-1"
    assistant.project = "test-project"
    assistant.toolkits = []
    return assistant


@pytest.fixture
def mock_tool_config():
    tool_config = MagicMock()
    tool_config.tool = "write_workspace_file"
    return tool_config


def _toolkit_details(toolkit_name="AgentWorkspace"):
    toolkit = MagicMock()
    toolkit.toolkit = toolkit_name
    toolkit.get_tool_configs.return_value = {"some": "config"}
    return toolkit


def _capturing_toolkit_method(captured_requests):
    def fake_toolkit_method(assistant, user, llm_model, request_uuid, request):
        captured_requests.append(request)
        tool = MagicMock()
        tool.name = "write_workspace_file"
        return [tool]

    return fake_toolkit_method


@patch("codemie.service.tools.ToolkitService.get_core_tools")
@patch("codemie.service.tools.tool_service.ToolsService.get_toolkit_from_workflow_tool_config")
def test_find_tool_from_config_threads_execution_id_as_conversation_id(
    mock_get_toolkit_config: MagicMock,
    mock_get_core_tools: MagicMock,
    mock_assistant,
    mock_user,
    mock_tool_config,
) -> None:
    """execution_id must reach AssistantChatRequest.conversation_id when core tools are empty."""
    mock_get_toolkit_config.return_value = _toolkit_details()
    mock_get_core_tools.return_value = []  # force the toolkit-fallback branch (the bug site)

    captured_requests = []
    toolkits = {"AgentWorkspace": _capturing_toolkit_method(captured_requests)}

    ToolsService.find_tool_from_config(
        mock_tool_config,
        toolkits,
        mock_assistant,
        mock_user,
        "test-project",
        execution_id="exec_123",
    )

    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert isinstance(request, AssistantChatRequest)
    assert request.conversation_id == "exec_123"


@patch("codemie.service.tools.ToolkitService.get_core_tools")
@patch("codemie.service.tools.tool_service.ToolsService.get_toolkit_from_workflow_tool_config")
def test_find_tool_from_config_defaults_conversation_id_when_execution_id_omitted(
    mock_get_toolkit_config: MagicMock,
    mock_get_core_tools: MagicMock,
    mock_assistant,
    mock_user,
    mock_tool_config,
) -> None:
    """Backward compatibility: omitting execution_id keeps AssistantChatRequest's random-uuid default."""
    mock_get_toolkit_config.return_value = _toolkit_details()
    mock_get_core_tools.return_value = []

    captured_requests = []
    toolkits = {"AgentWorkspace": _capturing_toolkit_method(captured_requests)}

    ToolsService.find_tool_from_config(
        mock_tool_config,
        toolkits,
        mock_assistant,
        mock_user,
        "test-project",
    )

    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert isinstance(request, AssistantChatRequest)
    assert request.conversation_id  # non-empty random uuid4
    assert request.conversation_id != "exec_123"
```

- [ ] **Step 2: Run tests to verify they fail correctly**

Run: `poetry run pytest tests/codemie/service/tools/test_find_tool_from_config_execution_id.py -v`
Expected: `test_find_tool_from_config_threads_execution_id_as_conversation_id` FAILS with an assertion error on `request.conversation_id == "exec_123"` (it will instead be a random UUID). `test_find_tool_from_config_defaults_conversation_id_when_execution_id_omitted` PASSES already (no behavior change needed for that branch) — confirm it passes now so Step 4 doesn't accidentally break it.

- [ ] **Step 3: Implement the fix**

Modify `src/codemie/service/tools/tool_service.py:84-112`, replacing the current body:

```python
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
```

Only the signature (new `execution_id` kwarg) and the `AssistantChatRequest` construction inside the `if toolkit_method:` block changed. Everything else in the method is unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/tools/test_find_tool_from_config_execution_id.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/tools/tool_service.py tests/codemie/service/tools/test_find_tool_from_config_execution_id.py
git commit -m "EPMCDME-14138: Thread execution_id into ToolsService.find_tool_from_config"
```

---

### Task 2: Forward `execution_id` from `ToolNode._execute_regular_tool`

**Files:**
- Modify: `src/codemie/workflows/nodes/tool_node.py:214-221` (`_execute_regular_tool`)
- Test: `tests/codemie/workflows/test_tool_node_context.py` (append `test_tc_tnc_019_...`)

**Interfaces:**
- Consumes: `ToolsService.find_tool_from_config(..., execution_id=...)` from Task 1; `self.execution_id` (already set on `ToolNode` at `tool_node.py:77`).
- Produces: nothing new for later tasks — this closes the call-site half of the fix. Task 4's regression test exercises both Task 1 and Task 2 together.

Test-first: yes — write `test_tc_tnc_019_regular_tool_forwards_execution_id_to_find_tool_from_config` against the *current* (unfixed) `_execute_regular_tool` first; it must fail (missing `execution_id` key in `call_args.kwargs`) before the fix.

- [ ] **Step 1: Write the failing test**

Append to `tests/codemie/workflows/test_tool_node_context.py`, immediately after `test_tc_tnc_018_...` (the file's current last test) — mirror the existing `test_tc_tnc_016_marketplace_workflow_passes_publisher_owner_user_id` pattern exactly, swapping the assertion to `execution_id`:

```python
@patch("codemie.workflows.nodes.tool_node.VirtualAssistantService")
@patch("codemie.workflows.nodes.tool_node.ToolkitService")
@patch("codemie.workflows.nodes.tool_node.ToolsService")
def test_tc_tnc_019_regular_tool_forwards_execution_id_to_find_tool_from_config(
    mock_tools_service: MagicMock,
    mock_toolkit_service: MagicMock,
    mock_virtual_assistant_service: MagicMock,
    mock_workflow_execution_service: MagicMock,
    mock_thought_queue: MagicMock,
    mock_callbacks: list,
    mock_user: MagicMock,
    mock_workflow_config: MagicMock,
) -> None:
    """
    TC_TNC_019: _execute_regular_tool passes execution_id through to
    ToolsService.find_tool_from_config so sequential bare tool states in the
    same workflow execution share one AgentWorkspace (EPMCDME-14138).
    """
    # Arrange
    mock_assistant = Mock()
    mock_assistant.id = "assistant-123"
    mock_virtual_assistant_service.create_from_tool_config.return_value = mock_assistant

    mock_tool = Mock()
    mock_tool.args_schema = {}
    mock_tool.execute.return_value = "result"
    mock_tools_service.find_tool_from_config.return_value = mock_tool
    mock_toolkit_service.get_toolkit_methods.return_value = []

    state_schema = {CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []}

    workflow_state = WorkflowState(
        id="tool_node",
        task="Execute tool",
        next=WorkflowNextState(state_id="next"),
        tool_id="tool_1",
    )

    node = ToolNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        user=mock_user,
        execution_id="exec_123",
    )

    with patch("codemie.workflows.nodes.tool_node.process_values", return_value={}):
        # Act
        node._execute_regular_tool(state_schema)

    # Assert — execution_id forwarded as the workspace-sharing correlation id
    _, find_kwargs = mock_tools_service.find_tool_from_config.call_args
    assert find_kwargs["execution_id"] == "exec_123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/workflows/test_tool_node_context.py::test_tc_tnc_019_regular_tool_forwards_execution_id_to_find_tool_from_config -v`
Expected: FAIL with `KeyError: 'execution_id'` on `find_kwargs["execution_id"]`.

- [ ] **Step 3: Implement the fix**

Modify `src/codemie/workflows/nodes/tool_node.py:214-221`:

```python
        tool = ToolsService.find_tool_from_config(
            self._tool_config,
            toolkits,
            assistant,
            self.user,
            self.workflow_config.project,
            owner_user_id=owner_user_id,
            execution_id=self.execution_id,
        )
```

Only the new `execution_id=self.execution_id` kwarg is added; nothing else in `_execute_regular_tool` changes.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/workflows/test_tool_node_context.py::test_tc_tnc_019_regular_tool_forwards_execution_id_to_find_tool_from_config -v`
Expected: PASS.

Also re-run the full file to confirm no regression in `test_tc_tnc_016`/`017`/`018` (they assert `owner_user_id`, unaffected by this change, but the same call site is touched):

Run: `poetry run pytest tests/codemie/workflows/test_tool_node_context.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/workflows/nodes/tool_node.py tests/codemie/workflows/test_tool_node_context.py
git commit -m "EPMCDME-14138: Forward execution_id from ToolNode to find_tool_from_config"
```

---

### Task 3: Cover `ToolkitSettingService.get_agent_workspace_toolkit`

**Files:**
- Test: `tests/codemie/service/tools/test_toolkit_settings_service.py` (append a new `TestGetAgentWorkspaceToolkit` class)

**Interfaces:**
- Consumes: `ToolkitSettingService.get_agent_workspace_toolkit(cls, assistant, project_name, user, llm_model, request_uuid, request=None)` (`src/codemie/service/tools/toolkit_settings_service.py:233-254`, unchanged by this task); `AgentWorkspaceToolkit.get_toolkit` (imported at `toolkit_settings_service.py:21`, mocked as the seam); `ToolkitSettingService._build_workspace_image_generator` (existing staticmethod, mocked to isolate the `conversation_id` behavior under test from image-generation config).
- Produces: nothing consumed by later tasks — this is pure coverage for a previously-untested method (technical-analysis.md Coverage Gap #2). No production code changes in this task.

Test-first: no production code change in this task — this is coverage-only (no RED/GREEN cycle against a bug; both new tests exercise existing, already-correct behavior). Still write the tests and run them to confirm they pass against current code.

- [ ] **Step 1: Write the tests**

Append to `tests/codemie/service/tools/test_toolkit_settings_service.py` (reuses the file's existing `mock_user`/`mock_assistant` fixtures defined near the top of the file):

```python
class TestGetAgentWorkspaceToolkit:
    """Tests for ToolkitSettingService.get_agent_workspace_toolkit conversation_id handling."""

    def test_returns_empty_list_when_request_is_none(self, mock_assistant, mock_user):
        tools = ToolkitSettingService.get_agent_workspace_toolkit(
            assistant=mock_assistant,
            project_name="test-project",
            user=mock_user,
            llm_model=None,
            request_uuid="test-uuid",
            request=None,
        )

        assert tools == []

    def test_returns_empty_list_when_conversation_id_falsy(self, mock_assistant, mock_user):
        mock_request = MagicMock()
        mock_request.conversation_id = None

        tools = ToolkitSettingService.get_agent_workspace_toolkit(
            assistant=mock_assistant,
            project_name="test-project",
            user=mock_user,
            llm_model=None,
            request_uuid="test-uuid",
            request=mock_request,
        )

        assert tools == []

    @patch("codemie.service.tools.toolkit_settings_service.AgentWorkspaceToolkit")
    @patch.object(ToolkitSettingService, "_build_workspace_image_generator", return_value=None)
    def test_delegates_to_agent_workspace_toolkit_with_request_conversation_id(
        self,
        mock_build_image_generator: MagicMock,
        mock_agent_workspace_toolkit: MagicMock,
        mock_assistant,
        mock_user,
    ):
        mock_request = MagicMock()
        mock_request.conversation_id = "exec_123"

        mock_toolkit_instance = MagicMock()
        mock_toolkit_instance.get_tools.return_value = [MagicMock()]
        mock_agent_workspace_toolkit.get_toolkit.return_value = mock_toolkit_instance

        tools = ToolkitSettingService.get_agent_workspace_toolkit(
            assistant=mock_assistant,
            project_name="test-project",
            user=mock_user,
            llm_model=None,
            request_uuid="test-uuid",
            request=mock_request,
        )

        assert tools == mock_toolkit_instance.get_tools.return_value
        _, kwargs = mock_agent_workspace_toolkit.get_toolkit.call_args
        assert kwargs["conversation_id"] == "exec_123"
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/tools/test_toolkit_settings_service.py::TestGetAgentWorkspaceToolkit -v`
Expected: all 3 PASS against current code (this task adds coverage, not a fix).

- [ ] **Step 3: Commit**

```bash
git add tests/codemie/service/tools/test_toolkit_settings_service.py
git commit -m "EPMCDME-14138: Add coverage for ToolkitSettingService.get_agent_workspace_toolkit"
```

---

### Task 4: Acceptance-criteria regression test — sequential bare tool states share one workspace

**Files:**
- Test: `tests/codemie/service/tools/test_find_tool_from_config_execution_id.py` (append to the file created in Task 1)

**Interfaces:**
- Consumes: `ToolsService.find_tool_from_config(..., execution_id=...)` (Task 1, now fixed); `ToolkitSettingService.get_agent_workspace_toolkit` (Task 3's subject, unchanged); `AgentWorkspaceToolkit.get_toolkit` (mocked seam, same import path as Task 3: `codemie.service.tools.toolkit_settings_service.AgentWorkspaceToolkit`); the real toolkit-factory lambda shape registered in `ToolkitService.get_toolkit_methods` (`src/codemie/service/tools/toolkit_service.py:196-205`), reproduced inline in the test so the test exercises the exact positional-argument contract production code relies on.
- Produces: nothing consumed by later tasks — this is the final acceptance-criteria proof for EPMCDME-14138.

Test-first: yes, in the sense required by the ticket's acceptance criteria — this test must fail if run against the pre-Task-1/Task-2 code (each `find_tool_from_config` call would produce a different random `conversation_id`), and must pass now that Tasks 1–2 are done. Since Tasks 1–2 are already implemented at this point in the plan, this step is written and run once, confirming GREEN; there is no separate RED run for this task (running it before Task 1 would require reverting that task's fix, which is unnecessary given Tasks 1–2's own RED runs already proved the underlying bug).

- [ ] **Step 1: Write the regression test**

Append to `tests/codemie/service/tools/test_find_tool_from_config_execution_id.py`:

```python
from codemie.service.tools.toolkit_settings_service import ToolkitSettingService


def _agent_workspace_toolkit_lambda():
    """Reproduces the exact lambda registered for AGENT_WORKSPACE_TOOLKIT in
    ToolkitService.get_toolkit_methods (toolkit_service.py:196-205), so this test
    exercises the real positional-argument contract between find_tool_from_config
    and the toolkit factory."""
    return lambda assistant, user, llm_model, request_uuid, request: (
        ToolkitSettingService.get_agent_workspace_toolkit(
            assistant,
            assistant.project,
            user,
            llm_model,
            request_uuid,
            request,
        )
    )


@patch("codemie.service.tools.toolkit_settings_service.AgentWorkspaceToolkit")
@patch("codemie.service.tools.ToolkitService.get_core_tools")
@patch("codemie.service.tools.tool_service.ToolsService.get_toolkit_from_workflow_tool_config")
def test_find_tool_from_config_sequential_bare_tool_states_share_conversation_id(
    mock_get_toolkit_config: MagicMock,
    mock_get_core_tools: MagicMock,
    mock_agent_workspace_toolkit: MagicMock,
    mock_assistant,
    mock_user,
) -> None:
    """
    Regression for EPMCDME-14138 acceptance criteria: write_workspace_file then
    execute_workspace_script as sequential bare tool states in the same workflow
    execution must resolve AgentWorkspace via the SAME conversation_id, not two
    different random UUIDs.
    """
    mock_get_core_tools.return_value = []  # force the toolkit-fallback branch for both calls

    def make_toolkit_instance(tool_name):
        instance = MagicMock()
        tool = MagicMock()
        tool.name = tool_name
        instance.get_tools.return_value = [tool]
        return instance

    mock_agent_workspace_toolkit.get_toolkit.side_effect = [
        make_toolkit_instance("write_workspace_file"),
        make_toolkit_instance("execute_workspace_script"),
    ]

    toolkits = {"AgentWorkspace": _agent_workspace_toolkit_lambda()}

    write_tool_config = MagicMock()
    write_tool_config.tool = "write_workspace_file"
    script_tool_config = MagicMock()
    script_tool_config.tool = "execute_workspace_script"

    mock_get_toolkit_config.side_effect = [
        _toolkit_details("AgentWorkspace"),
        _toolkit_details("AgentWorkspace"),
    ]

    # First bare tool state in the execution: write_workspace_file
    ToolsService.find_tool_from_config(
        write_tool_config,
        toolkits,
        mock_assistant,
        mock_user,
        "test-project",
        execution_id="exec_123",
    )
    # Second bare tool state in the SAME execution: execute_workspace_script
    ToolsService.find_tool_from_config(
        script_tool_config,
        toolkits,
        mock_assistant,
        mock_user,
        "test-project",
        execution_id="exec_123",
    )

    assert mock_agent_workspace_toolkit.get_toolkit.call_count == 2
    first_kwargs = mock_agent_workspace_toolkit.get_toolkit.call_args_list[0].kwargs
    second_kwargs = mock_agent_workspace_toolkit.get_toolkit.call_args_list[1].kwargs
    assert first_kwargs["conversation_id"] == "exec_123"
    assert second_kwargs["conversation_id"] == "exec_123"
    assert first_kwargs["conversation_id"] == second_kwargs["conversation_id"]
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `poetry run pytest tests/codemie/service/tools/test_find_tool_from_config_execution_id.py -v`
Expected: all 3 tests in the file PASS (the 2 from Task 1 plus this one).

- [ ] **Step 3: Run the full affected test surface**

Run: `poetry run pytest tests/codemie/service/tools/test_find_tool_from_config_execution_id.py tests/codemie/service/tools/test_toolkit_settings_service.py tests/codemie/workflows/test_tool_node_context.py tests/codemie/service/tools/test_tool_service_owner_user_id.py -v`
Expected: all PASS. This confirms Tasks 1–4 together with the pre-existing `owner_user_id` coverage have no regressions.

- [ ] **Step 4: Commit**

```bash
git add tests/codemie/service/tools/test_find_tool_from_config_execution_id.py
git commit -m "EPMCDME-14138: Add regression test for cross-tool-state workspace sharing"
```

---

## Post-implementation note

`ToolkitSettingService.get_agent_workspace_toolkit`'s falsy-`conversation_id` guard (`return []`) will start resolving truthy for every bare tool state after Task 2 lands, per technical-analysis.md's Risk Indicators section — the `AgentWorkspace` toolkit becomes active in the bare tool-state path in production for the first time. This is the ticket's intended expected result, not a side effect to mitigate; no feature flag or opt-out is in scope for this fix.
