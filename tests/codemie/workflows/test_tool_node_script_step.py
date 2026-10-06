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

"""A workflow tool step that runs ``execute_workspace_script`` against the run workspace.

The seam under test is the real ``ToolNode`` -> ``ToolsService.find_tool_from_config`` -> workspace toolkit ->
``ExecuteWorkspaceScriptTool`` -> ``WorkspaceScriptRunner`` chain. Only the persistence (an in-memory workspace
service shared by the steps of one run) and the sandbox job (``BatchJobRunner``) are replaced.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools import ToolException

from codemie.core.workflow_models import (
    WorkflowConfig,
    WorkflowExecutionStatusEnum,
    WorkflowNextState,
    WorkflowState,
    WorkflowTool,
)
from codemie.rest_api.models.agent_workspace import (
    CreateAgentWorkspaceRequest,
    ExecuteWorkspaceScriptResponse,
    WorkspaceFileItemResponse,
)
from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie.service.script_tool_calls.context import ScriptRunContext, ScriptScopeKind
from codemie.workflows.constants import CONTEXT_STORE_VARIABLE, MESSAGES_VARIABLE
from codemie.workflows.nodes.tool_node import ToolNode
from codemie_tools.base.file_object import FileObject
from codemie_tools.data_management.code_executor.batch_job_runner import JobResult
from codemie_tools.data_management.code_executor.models import ExecutionMode, SandboxMode
from codemie_tools.data_management.workspace.workspace_script_runner import WorkspaceScriptRunner

EXECUTION_ID = "exec-15559"
PROJECT = "wf-project"
OUTPUT_KEY = "script_result"
_RUNNER_MODULE = "codemie_tools.data_management.workspace.workspace_script_runner"
_TOOLKIT_MODULE = "codemie_tools.data_management.workspace.toolkit"

#: A script's behaviour in the fake sandbox: the workspace files in, the job result out.
ScriptBehaviour = Callable[[Mapping[str, bytes]], JobResult]


def _write_file(path: str, content: bytes, stdout: str = "") -> ScriptBehaviour:
    return lambda files: JobResult(stdout=stdout, stderr="", exit_code=0, changed_files={path: content})


class _FakeBatchJobRunner:
    """Stands in for the sandbox job: runs the registered behaviour of the script named by the wrapper code."""

    behaviours: dict[str, ScriptBehaviour] = {}

    def __init__(self, config: object) -> None:
        pass

    def run(self, wrapper_code: str, *, input_files: Mapping[str, bytes], **_: object) -> JobResult:
        return self.behaviours[wrapper_code](input_files)


class _FakeWorkspaceService(AgentWorkspaceService):
    """In-memory ``AgentWorkspaceService`` shared by the steps of one run; a workspace is keyed by conversation id.

    ``execute_workspace_script`` drives the real ``WorkspaceScriptRunner`` over the stored files, then stores the files
    the job changed, as the real service does.
    """

    def __init__(self) -> None:  # noqa: D107 - no repositories: the parent's init would open them
        self.files_by_conversation: dict[str, dict[str, bytes]] = {}
        self.created_for: list[str] = []
        self.run_contexts: list[ScriptRunContext | None] = []

    def create_workspace(self, request: CreateAgentWorkspaceRequest, user: User) -> SimpleNamespace:
        self.created_for.append(request.conversation_id)
        self.files_by_conversation.setdefault(request.conversation_id, {})
        return SimpleNamespace(id=f"workspace:{request.conversation_id}")

    def get_workspace(self, workspace_id: str, user: User) -> SimpleNamespace:
        return SimpleNamespace(id=workspace_id, conversation_id=workspace_id.removeprefix("workspace:"))

    def execute_workspace_script(
        self,
        workspace_id: str,
        script_path: str,
        user: User,
        export_files: list[str] | None = None,
        run_context: ScriptRunContext | None = None,
        tool_calling: object | None = None,
    ) -> ExecuteWorkspaceScriptResponse:
        self.run_contexts.append(run_context)
        conversation_id = self.get_workspace(workspace_id, user).conversation_id
        files = self.files_by_conversation[conversation_id]
        repository = MagicMock(name="file_repository")
        repository.read_file.side_effect = lambda file_name, owner, mime_type: SimpleNamespace(
            bytes_content=lambda: files[file_name]
        )
        runner = WorkspaceScriptRunner(
            file_repository=repository,
            user_id=user.id,
            input_files=[
                FileObject(name=path, mime_type="text/plain", owner=user.id, path=path, content=content)
                for path, content in files.items()
            ],
            execution_mode=ExecutionMode.SANDBOX,
            conversation_id=conversation_id,
        )
        runner.config = runner.config.model_copy(update={"sandbox_mode": SandboxMode.JOBS, "namespace": "ns-test"})
        with (
            patch(f"{_RUNNER_MODULE}.BatchJobRunner", _FakeBatchJobRunner),
            patch(f"{_RUNNER_MODULE}.build_guarded_workspace_script", side_effect=lambda path, **_: path),
            patch.object(WorkspaceScriptRunner, "_get_user_workdir", return_value="/home/codemie/u/conv"),
            patch.object(WorkspaceScriptRunner, "_validate_code_security_policy", return_value=None),
        ):
            output = runner.execute_script(script_path=script_path, export_files=export_files)
        synced: list[WorkspaceFileItemResponse] = []
        for changed in runner.last_execution_files:
            files[changed.name] = changed.bytes_content() or b""
            synced.append(
                WorkspaceFileItemResponse(
                    path=changed.name,
                    mime_type="text/plain",
                    checksum="x",
                    size=len(files[changed.name]),
                    version=1,
                    update_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                )
            )
        return ExecuteWorkspaceScriptResponse(
            message="Workspace script executed successfully", output=output, workspace_files=synced
        )


@pytest.fixture
def workspace() -> Iterator[_FakeWorkspaceService]:
    service = _FakeWorkspaceService()
    _FakeBatchJobRunner.behaviours = {}
    # The files a run was started with, registered under the execution id before the first step.
    service.files_by_conversation[EXECUTION_ID] = {
        "scripts/run.py": b"# script body",
        "scripts/read.py": b"# script body",
        "scripts/boom.py": b"# script body",
    }
    with patch(f"{_TOOLKIT_MODULE}.AgentWorkspaceService", return_value=service):
        yield service


@pytest.fixture(autouse=True)
def catalog() -> Iterator[None]:
    """The toolkit lookup of the step's tool is a catalog query; the rest of the chain is real."""
    toolkit_entry = {"toolkit": "AgentWorkspace", "label": "Agent Workspace", "tools": []}
    with (
        patch("codemie.service.tools.tool_service.ToolsService.find_toolkit_for_tool", return_value=toolkit_entry),
        patch("codemie.service.tools.ToolkitService.get_core_tools", return_value=[]),
        patch("codemie.service.tools.toolkit_settings_service.ToolkitSettingService._build_workspace_image_generator"),
    ):
        yield


def _node(script_path: str, execution_service: MagicMock) -> ToolNode:
    tool = WorkflowTool(id="script_step", tool="execute_workspace_script", tool_args={"script_path": script_path})
    config = MagicMock(spec=WorkflowConfig)
    config.project = PROJECT
    config.tools = [tool]
    config.is_global = False
    config.created_by = None
    state = WorkflowState(
        id="script_step",
        task="Run the script",
        next=WorkflowNextState(state_id="next", output_key=OUTPUT_KEY, store_in_context=True),
        tool_id="script_step",
    )
    return ToolNode(
        callbacks=[MagicMock()],
        workflow_execution_service=execution_service,
        thought_queue=MagicMock(),
        workflow_state=state,
        workflow_config=config,
        user=User(id="running-user", username="runner"),
        execution_id=EXECUTION_ID,
    )


def _run(node: ToolNode) -> dict[str, object]:
    with patch.object(node, "_is_execution_aborted", return_value=False):
        return node({CONTEXT_STORE_VARIABLE: {}, MESSAGES_VARIABLE: []})


@pytest.fixture
def execution_service() -> MagicMock:
    return MagicMock(name="workflow_execution_service")


def test_step_runs_in_the_workspace_keyed_by_the_execution_id_in_workflow_scope(
    workspace: _FakeWorkspaceService, execution_service: MagicMock
) -> None:
    _FakeBatchJobRunner.behaviours["scripts/run.py"] = _write_file("out/data.txt", b"payload", stdout="done")

    _run(_node("scripts/run.py", execution_service))

    assert set(workspace.created_for) == {EXECUTION_ID}
    (run_context,) = workspace.run_contexts
    assert run_context is not None and run_context.scope_kind is ScriptScopeKind.WORKFLOW
    assert run_context.user.id == "running-user"


def test_step_output_stored_under_output_key_is_the_whole_script_tool_json(
    workspace: _FakeWorkspaceService, execution_service: MagicMock
) -> None:
    _FakeBatchJobRunner.behaviours["scripts/run.py"] = _write_file("out/data.txt", b"payload", stdout="done")

    result = _run(_node("scripts/run.py", execution_service))

    stored = json.loads(str(result[OUTPUT_KEY]))
    assert set(stored) == {"message", "output", "workspace_files"}
    assert stored["message"] == "Workspace script executed successfully"
    assert "done" in stored["output"]
    assert [item["path"] for item in stored["workspace_files"]] == ["out/data.txt"]
    assert result[OUTPUT_KEY] == result[CONTEXT_STORE_VARIABLE][OUTPUT_KEY]  # type: ignore[index]


def test_a_file_the_script_changed_is_in_the_workspace_for_a_later_step(
    workspace: _FakeWorkspaceService, execution_service: MagicMock
) -> None:
    _FakeBatchJobRunner.behaviours["scripts/run.py"] = _write_file("out/data.txt", b"payload")
    _FakeBatchJobRunner.behaviours["scripts/read.py"] = lambda files: JobResult(
        stdout=f"read: {files['out/data.txt'].decode()}", stderr="", exit_code=0
    )

    _run(_node("scripts/run.py", execution_service))
    result = _run(_node("scripts/read.py", execution_service))

    assert "read: payload" in json.loads(str(result[OUTPUT_KEY]))["output"]
    assert workspace.files_by_conversation[EXECUTION_ID]["out/data.txt"] == b"payload"


def test_missing_script_path_fails_the_step_with_a_clear_message(
    workspace: _FakeWorkspaceService, execution_service: MagicMock
) -> None:
    with pytest.raises(ToolException, match="Script file 'scripts/absent.py' was not provided"):
        _run(_node("scripts/absent.py", execution_service))

    finish = execution_service.finish_state.call_args.kwargs
    assert finish["status"] == WorkflowExecutionStatusEnum.FAILED
    assert "scripts/absent.py" in finish["output"]


def test_failing_script_fails_the_step_with_its_output(
    workspace: _FakeWorkspaceService, execution_service: MagicMock
) -> None:
    _FakeBatchJobRunner.behaviours["scripts/boom.py"] = lambda files: JobResult(
        stdout="partial progress", stderr="RuntimeError: boom", exit_code=1, changed_files={"out/half.txt": b"x"}
    )

    with pytest.raises(ToolException, match="Code execution failed"):
        _run(_node("scripts/boom.py", execution_service))

    finish = execution_service.finish_state.call_args.kwargs
    assert finish["status"] == WorkflowExecutionStatusEnum.FAILED
    assert "partial progress" in finish["output"] and "RuntimeError: boom" in finish["output"]
    assert "out/half.txt" not in workspace.files_by_conversation[EXECUTION_ID]
