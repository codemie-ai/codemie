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

import pytest
from unittest.mock import MagicMock, patch

from codemie.workflows.exceptions import (
    FeatureDisabledError,
    WorkflowNestingDepthExceededError,
    SubWorkflowExecutionError,
)
from codemie.core.workflow_models import WorkflowExecutionStatusEnum
from codemie.workflows.constants import CONTEXT_STORE_VARIABLE, USER_INPUT
from codemie.workflows.nodes.sub_workflow_node import SubWorkflowNode


# ── module-level fixture: mock pool for all tests ────────────────────────────


@pytest.fixture(autouse=True)
def _patch_workflow_pool():
    """Prevent real pool compilation in all tests; pool behavior tested separately."""
    with patch('codemie.workflows.nodes.sub_workflow_node.workflow_pool'):
        yield


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def workflow_state():
    state = MagicMock()
    state.workflow_id = "child-wf-id"
    return state


@pytest.fixture
def workflow_execution_service():
    svc = MagicMock()
    svc.user = MagicMock()
    svc.user.as_user_model.return_value = MagicMock()
    return svc


@pytest.fixture
def node(workflow_state, workflow_execution_service):
    return SubWorkflowNode(
        callbacks=[],
        workflow_execution_service=workflow_execution_service,
        thought_queue=MagicMock(),
        workflow_state=workflow_state,
        node_name="sub_wf",
        execution_id="parent-exec-id",
        workflow_config=MagicMock(),
    )


@pytest.fixture
def state_schema():
    return {CONTEXT_STORE_VARIABLE: {"key": "value"}, "messages": []}


# ── T1: flag disabled ─────────────────────────────────────────────────────────


def test_execute_raises_when_flag_disabled(node, state_schema):
    with patch("codemie.workflows.nodes.sub_workflow_node.customer_config") as mock_cfg:
        mock_cfg.is_feature_enabled.return_value = False
        with pytest.raises(FeatureDisabledError):
            node.execute(state_schema, {})


# ── T2: nesting depth exceeded ────────────────────────────────────────────────


def test_execute_raises_when_depth_exceeded(node, state_schema):
    child_config = MagicMock()
    child_config.max_nesting_level = 1

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.customer_config") as mock_cfg,
        patch('codemie.workflows.nodes.sub_workflow_node.WorkflowService') as mock_svc,
    ):
        mock_cfg.is_feature_enabled.return_value = True
        mock_cfg.SUBWORKFLOW_MAX_NESTING_DEPTH = 1
        mock_svc_inst = mock_svc.return_value
        mock_svc_inst.get_workflow.return_value = child_config
        mock_svc.get_nesting_depth.return_value = 1  # at the limit
        # New: initial parent fetch must return active_sub_execution_id=None to take create path
        mock_svc.find_workflow_execution_by_id.return_value.active_sub_execution_id = None

        with pytest.raises(WorkflowNestingDepthExceededError):
            node.execute(state_schema, {})


# ── T4: non-START previous node → passes previous node output ──────────────────


def test_execute_passes_previous_node_output_when_no_mapping(node, state_schema):
    """When PREVIOUS_EXECUTION_STATE_ID is set (a real node preceded this one),
    child input is that state's output — not the full context_store JSON."""
    from codemie.workflows.constants import PREVIOUS_EXECUTION_STATE_ID

    state_schema[PREVIOUS_EXECUTION_STATE_ID] = "prev-state-uuid"
    state_schema[CONTEXT_STORE_VARIABLE] = {"irrelevant": "context_store_is_not_used_here"}

    child_exec = MagicMock(execution_id="child-id")
    child_config = MagicMock(max_nesting_level=None)
    finished_child = MagicMock(overall_status=WorkflowExecutionStatusEnum.SUCCEEDED)
    parent_exec = MagicMock()
    parent_exec_initial = MagicMock()
    parent_exec_initial.active_sub_execution_id = None

    captured_input = {}

    def capture_create(*args, **kwargs):
        captured_input['user_input'] = args[2] if len(args) > 2 else kwargs.get('user_input')
        return child_exec

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.customer_config") as mock_cfg,
        patch('codemie.workflows.nodes.sub_workflow_node.WorkflowService') as mock_svc,
        patch('codemie.workflows.workflow.WorkflowExecutor') as mock_executor,
    ):
        mock_cfg.is_feature_enabled.return_value = True
        mock_cfg.SUBWORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc.return_value.get_workflow.return_value = child_config
        mock_svc.get_nesting_depth.return_value = 0
        mock_svc.create_workflow_execution.side_effect = capture_create
        mock_svc.find_execution_state_output.return_value = "output from previous node"
        mock_svc.find_last_execution_state_output.return_value = ""
        mock_svc.find_workflow_execution_by_id.side_effect = [parent_exec_initial, finished_child, parent_exec]
        mock_executor.create_executor.return_value = MagicMock()

        node.execute(state_schema, {})

    assert captured_input['user_input'] == "output from previous node"
    mock_svc.find_execution_state_output.assert_called_once_with("prev-state-uuid")


# ── T4b: first node after START → passes USER_INPUT ──────────────────────────


def test_execute_passes_user_input_when_first_after_start(node, state_schema):
    """When PREVIOUS_EXECUTION_STATE_ID is absent (sub-workflow is the first node
    after START), child input is USER_INPUT — even when context_store is non-empty,
    proving the condition is PREVIOUS_EXECUTION_STATE_ID=None, not empty context_store."""
    # Do NOT set PREVIOUS_EXECUTION_STATE_ID — its absence signals first-after-START.
    state_schema[CONTEXT_STORE_VARIABLE] = {"some": "accumulated context"}
    state_schema[USER_INPUT] = "original user message"

    child_exec = MagicMock(execution_id="child-id")
    child_config = MagicMock(max_nesting_level=None)
    finished_child = MagicMock(overall_status=WorkflowExecutionStatusEnum.SUCCEEDED)
    parent_exec = MagicMock()
    parent_exec_initial = MagicMock()
    parent_exec_initial.active_sub_execution_id = None

    captured_input = {}

    def capture_create(*args, **kwargs):
        captured_input['user_input'] = args[2] if len(args) > 2 else kwargs.get('user_input')
        return child_exec

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.customer_config") as mock_cfg,
        patch('codemie.workflows.nodes.sub_workflow_node.WorkflowService') as mock_svc,
        patch('codemie.workflows.workflow.WorkflowExecutor') as mock_executor,
    ):
        mock_cfg.is_feature_enabled.return_value = True
        mock_cfg.SUBWORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc.return_value.get_workflow.return_value = child_config
        mock_svc.get_nesting_depth.return_value = 0
        mock_svc.create_workflow_execution.side_effect = capture_create
        mock_svc.find_last_execution_state_output.return_value = ""
        mock_svc.find_workflow_execution_by_id.side_effect = [parent_exec_initial, finished_child, parent_exec]
        mock_executor.create_executor.return_value = MagicMock()

        node.execute(state_schema, {})

    assert captured_input['user_input'] == "original user message"
    mock_svc.find_execution_state_output.assert_not_called()


# ── T5: happy path ────────────────────────────────────────────────────────────


def test_execute_happy_path_clears_active_sub_execution_id(node, state_schema):
    child_exec = MagicMock(execution_id="child-id")
    child_config = MagicMock(max_nesting_level=None)
    finished_child = MagicMock(overall_status=WorkflowExecutionStatusEnum.SUCCEEDED)
    parent_exec_initial = MagicMock()
    parent_exec_initial.active_sub_execution_id = None
    parent_exec_final = MagicMock()

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.customer_config") as mock_cfg,
        patch('codemie.workflows.nodes.sub_workflow_node.WorkflowService') as mock_svc,
        patch('codemie.workflows.workflow.WorkflowExecutor') as mock_executor,
    ):
        mock_cfg.is_feature_enabled.return_value = True
        mock_cfg.SUBWORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc_inst = mock_svc.return_value
        mock_svc_inst.get_workflow.return_value = child_config
        mock_svc.get_nesting_depth.return_value = 0
        mock_svc.create_workflow_execution.return_value = child_exec
        mock_svc.find_last_execution_state_output.return_value = "child state output"
        mock_svc.find_workflow_execution_by_id.side_effect = [parent_exec_initial, finished_child, parent_exec_final]
        child_executor = MagicMock()
        mock_executor.create_executor.return_value = child_executor

        result = node.execute(state_schema, {})

    child_executor.stream.assert_called_once()
    assert parent_exec_final.active_sub_execution_id is None
    parent_exec_final.save.assert_called_once()
    assert result == "child state output"


# ── T5b: execute() returns last child state output, not finished_child.output ─


def test_execute_returns_last_child_state_output(node, state_schema):
    """execute() must query find_last_execution_state_output for the child
    execution, not rely on finished_child.output which is always None on the
    success path (WorkflowExecutionService.finish() never writes it)."""
    child_exec = MagicMock(execution_id="child-id")
    child_config = MagicMock(max_nesting_level=None)
    # Simulate production reality: .output is None on the success path.
    finished_child = MagicMock(
        overall_status=WorkflowExecutionStatusEnum.SUCCEEDED,
        output=None,
    )
    parent_exec_initial = MagicMock()
    parent_exec_initial.active_sub_execution_id = None
    parent_exec_final = MagicMock()

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.customer_config") as mock_cfg,
        patch('codemie.workflows.nodes.sub_workflow_node.WorkflowService') as mock_svc,
        patch('codemie.workflows.workflow.WorkflowExecutor') as mock_executor,
    ):
        mock_cfg.is_feature_enabled.return_value = True
        mock_cfg.SUBWORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc_inst = mock_svc.return_value
        mock_svc_inst.get_workflow.return_value = child_config
        mock_svc.get_nesting_depth.return_value = 0
        mock_svc.create_workflow_execution.return_value = child_exec
        mock_svc.find_last_execution_state_output.return_value = "actual child output from state"
        mock_svc.find_workflow_execution_by_id.side_effect = [parent_exec_initial, finished_child, parent_exec_final]
        mock_executor.create_executor.return_value = MagicMock()

        result = node.execute(state_schema, {})

    assert result == "actual child output from state"
    mock_svc.find_last_execution_state_output.assert_called_once_with("child-id")


# ── T6: child fails → SubWorkflowExecutionError ───────────────────────────────


def test_execute_raises_when_child_fails(node, state_schema):
    child_exec = MagicMock(execution_id="child-id")
    child_config = MagicMock(max_nesting_level=None)
    failed_child = MagicMock(
        overall_status=WorkflowExecutionStatusEnum.FAILED,
        output="error message",
    )
    parent_exec_initial = MagicMock()
    parent_exec_initial.active_sub_execution_id = None
    parent_exec_final = MagicMock()

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.customer_config") as mock_cfg,
        patch('codemie.workflows.nodes.sub_workflow_node.WorkflowService') as mock_svc,
        patch('codemie.workflows.workflow.WorkflowExecutor') as mock_executor,
    ):
        mock_cfg.is_feature_enabled.return_value = True
        mock_cfg.SUBWORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc_inst = mock_svc.return_value
        mock_svc_inst.get_workflow.return_value = child_config
        mock_svc.get_nesting_depth.return_value = 0
        mock_svc.create_workflow_execution.return_value = child_exec
        mock_svc.find_workflow_execution_by_id.side_effect = [parent_exec_initial, failed_child, parent_exec_final]
        mock_executor.create_executor.return_value = MagicMock()

        with pytest.raises(SubWorkflowExecutionError):
            node.execute(state_schema, {})

    # active_sub_execution_id must still be cleared on failure
    assert parent_exec_final.active_sub_execution_id is None
    parent_exec_final.save.assert_called_once()


# ── T7: active_sub_execution_id set on parent before stream ──────────────────


def test_execute_sets_active_sub_execution_id_before_stream(node, state_schema):
    """create_workflow_execution must be called with parent_execution_id so the service
    sets active_sub_execution_id atomically before pool.acquire() + stream().
    The node itself no longer writes it directly (CR-003 fix removes the duplicate write)."""
    child_exec = MagicMock(execution_id="child-id")
    child_config = MagicMock(max_nesting_level=None)
    finished_child = MagicMock(
        overall_status=WorkflowExecutionStatusEnum.SUCCEEDED,
        output="done",
    )
    parent_exec_initial = MagicMock()
    parent_exec_initial.active_sub_execution_id = None
    parent_exec_final = MagicMock()

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.customer_config") as mock_cfg,
        patch('codemie.workflows.nodes.sub_workflow_node.WorkflowService') as mock_svc,
        patch('codemie.workflows.workflow.WorkflowExecutor') as mock_executor,
    ):
        mock_cfg.is_feature_enabled.return_value = True
        mock_cfg.SUBWORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc_inst = mock_svc.return_value
        mock_svc_inst.get_workflow.return_value = child_config
        mock_svc.get_nesting_depth.return_value = 0
        mock_svc.create_workflow_execution.return_value = child_exec
        mock_svc.find_last_execution_state_output.return_value = ""
        mock_svc.find_workflow_execution_by_id.side_effect = [
            parent_exec_initial,  # initial parent check
            finished_child,  # post-stream child status
            parent_exec_final,  # terminal parent clear
        ]
        mock_executor.create_executor.return_value = MagicMock()

        node.execute(state_schema, {})

    _, kwargs = mock_svc.create_workflow_execution.call_args
    assert kwargs.get("parent_execution_id") == "parent-exec-id"


@pytest.mark.parametrize("parent_attributes", [{"team": "core"}, None])
def test_execute_child_inherits_parent_run_attributes(node, state_schema, parent_attributes):
    parent_exec = MagicMock(active_sub_execution_id=None, attributes=parent_attributes)
    finished_child = MagicMock(overall_status=WorkflowExecutionStatusEnum.SUCCEEDED, output="done")

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.customer_config") as mock_cfg,
        patch('codemie.workflows.nodes.sub_workflow_node.WorkflowService') as mock_svc,
        patch('codemie.workflows.workflow.WorkflowExecutor') as mock_executor,
    ):
        mock_cfg.is_feature_enabled.return_value = True
        mock_cfg.SUBWORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc.return_value.get_workflow.return_value = MagicMock(max_nesting_level=None)
        mock_svc.get_nesting_depth.return_value = 0
        mock_svc.create_workflow_execution.return_value = MagicMock(execution_id="child-id")
        mock_svc.find_last_execution_state_output.return_value = ""
        mock_svc.find_workflow_execution_by_id.side_effect = [parent_exec, finished_child, MagicMock()]
        mock_executor.create_executor.return_value = MagicMock()

        node.execute(state_schema, {})

    assert mock_svc.create_workflow_execution.call_args.kwargs["attributes"] == parent_attributes


# ── T8: INTERRUPTED child → InterruptedException raised, id not cleared ──────


def test_execute_raises_interrupted_when_child_interrupted(node, state_schema):
    from codemie.core.exceptions import InterruptedException

    child_exec = MagicMock(execution_id="child-id")
    child_config = MagicMock(max_nesting_level=None)
    interrupted_child = MagicMock(
        overall_status=WorkflowExecutionStatusEnum.INTERRUPTED,
        output=None,
    )
    parent_exec_initial = MagicMock()
    parent_exec_initial.active_sub_execution_id = None

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.customer_config") as mock_cfg,
        patch('codemie.workflows.nodes.sub_workflow_node.WorkflowService') as mock_svc,
        patch('codemie.workflows.workflow.WorkflowExecutor') as mock_executor,
    ):
        mock_cfg.is_feature_enabled.return_value = True
        mock_cfg.SUBWORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc_inst = mock_svc.return_value
        mock_svc_inst.get_workflow.return_value = child_config
        mock_svc.get_nesting_depth.return_value = 0
        mock_svc.create_workflow_execution.return_value = child_exec
        # Only 2 calls: initial parent + child status (no final clear on INTERRUPTED)
        mock_svc.find_workflow_execution_by_id.side_effect = [
            parent_exec_initial,
            interrupted_child,
        ]
        mock_executor.create_executor.return_value = MagicMock()

        with pytest.raises(InterruptedException):
            node.execute(state_schema, {})


def test_execute_does_not_clear_active_id_on_child_interrupt(node, state_schema):
    from codemie.core.exceptions import InterruptedException

    child_exec = MagicMock(execution_id="child-id")
    child_config = MagicMock(max_nesting_level=None)
    interrupted_child = MagicMock(
        overall_status=WorkflowExecutionStatusEnum.INTERRUPTED,
    )
    parent_exec_initial = MagicMock()
    parent_exec_initial.active_sub_execution_id = None

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.customer_config") as mock_cfg,
        patch('codemie.workflows.nodes.sub_workflow_node.WorkflowService') as mock_svc,
        patch('codemie.workflows.workflow.WorkflowExecutor') as mock_executor,
    ):
        mock_cfg.is_feature_enabled.return_value = True
        mock_cfg.SUBWORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc_inst = mock_svc.return_value
        mock_svc_inst.get_workflow.return_value = child_config
        mock_svc.get_nesting_depth.return_value = 0
        mock_svc.create_workflow_execution.return_value = child_exec
        mock_svc.find_workflow_execution_by_id.side_effect = [
            parent_exec_initial,
            interrupted_child,
        ]
        mock_executor.create_executor.return_value = MagicMock()

        with pytest.raises(InterruptedException):
            node.execute(state_schema, {})

    # No 3rd call: find_workflow_execution_by_id was called exactly twice
    assert mock_svc.find_workflow_execution_by_id.call_count == 2


# ── T9: resume path — parent.active_sub_execution_id set → resume child ──────


def test_execute_resume_path_resumes_child_with_forwarded_input(node, state_schema):
    child_execution_id = "existing-child-id"
    child_exec_record = MagicMock(execution_id=child_execution_id, workflow_id="child-wf-id")
    parent_exec = MagicMock()
    parent_exec.active_sub_execution_id = child_execution_id
    child_config = MagicMock(max_nesting_level=None)
    finished_child = MagicMock(overall_status=WorkflowExecutionStatusEnum.SUCCEEDED)
    parent_exec_final = MagicMock()

    state_schema[CONTEXT_STORE_VARIABLE] = {"key": "val"}

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.customer_config") as mock_cfg,
        patch('codemie.workflows.nodes.sub_workflow_node.WorkflowService') as mock_svc,
        patch('codemie.workflows.workflow.WorkflowExecutor') as mock_executor,
    ):
        mock_cfg.is_feature_enabled.return_value = True
        mock_cfg.SUBWORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc_inst = mock_svc.return_value
        mock_svc_inst.get_workflow.return_value = child_config
        mock_svc.find_last_execution_state_output.return_value = "resumed output"
        mock_svc.find_workflow_execution_by_id.side_effect = [
            parent_exec,  # call 1: initial parent → resume path
            child_exec_record,  # call 2: fetch child by active_sub_execution_id
            finished_child,  # call 3: post-stream child status
            parent_exec_final,  # call 4: terminal parent clear
        ]
        child_executor = MagicMock()
        mock_executor.create_executor.return_value = child_executor

        result = node.execute(state_schema, {})

    create_kwargs = mock_executor.create_executor.call_args[1]
    assert create_kwargs.get("resume_execution") is True
    assert create_kwargs.get("execution_id") == child_execution_id
    mock_svc.create_workflow_execution.assert_not_called()
    assert result == "resumed output"


def test_execute_resume_path_clears_active_id_on_child_success(node, state_schema):
    child_execution_id = "existing-child-id"
    child_exec_record = MagicMock(execution_id=child_execution_id, workflow_id="child-wf-id")
    parent_exec = MagicMock()
    parent_exec.active_sub_execution_id = child_execution_id
    child_config = MagicMock(max_nesting_level=None)
    finished_child = MagicMock(
        overall_status=WorkflowExecutionStatusEnum.SUCCEEDED,
        output="done",
    )
    parent_exec_final = MagicMock()

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.customer_config") as mock_cfg,
        patch('codemie.workflows.nodes.sub_workflow_node.WorkflowService') as mock_svc,
        patch('codemie.workflows.workflow.WorkflowExecutor') as mock_executor,
    ):
        mock_cfg.is_feature_enabled.return_value = True
        mock_cfg.SUBWORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc_inst = mock_svc.return_value
        mock_svc_inst.get_workflow.return_value = child_config
        mock_svc.find_workflow_execution_by_id.side_effect = [
            parent_exec,
            child_exec_record,
            finished_child,
            parent_exec_final,
        ]
        mock_executor.create_executor.return_value = MagicMock()

        node.execute(state_schema, {})

    assert parent_exec_final.active_sub_execution_id is None
    parent_exec_final.save.assert_called_once()


# ── WorkflowExecutor compile/inject split ──────────────────────────────────


class TestWorkflowExecutorCompileInjectSplit:
    def test_create_executor_accepts_compiled_graph_param(self):
        """create_executor must accept compiled_graph kwarg without error."""
        from codemie.workflows.workflow import WorkflowExecutor

        mock_config = MagicMock()
        mock_config.mode = None  # avoid AUTONOMOUS branch
        mock_user = MagicMock()
        mock_graph = MagicMock()
        mock_graph._node_slots = {}

        with patch("codemie.workflows.workflow.WorkflowExecutor.__init__", return_value=None):
            try:
                WorkflowExecutor.create_executor(mock_config, "input", mock_user, compiled_graph=mock_graph)
            except TypeError as e:
                pytest.fail(f"create_executor does not accept compiled_graph: {e}")


# ── SubWorkflowNode pool integration ─────────────────────────────────────────


class TestSubWorkflowNodePoolIntegration:
    """Pool acquire/release wired into create-path only."""

    @pytest.fixture
    def pool_node(self, workflow_state, workflow_execution_service):
        return SubWorkflowNode(
            callbacks=[],
            workflow_execution_service=workflow_execution_service,
            thought_queue=MagicMock(),
            workflow_state=workflow_state,
            node_name="sub_wf",
            execution_id="parent-exec-id",
            workflow_config=MagicMock(),
        )

    def test_create_path_acquires_and_releases_pool_graph(self, pool_node, state_schema):
        """acquire() called on create-path; release() called in finally."""
        mock_graph = MagicMock()
        mock_graph._node_slots = {}

        with (
            patch("codemie.workflows.nodes.sub_workflow_node.customer_config") as mock_config,
            patch("codemie.workflows.nodes.sub_workflow_node.WorkflowService") as mock_ws,
            patch("codemie.workflows.nodes.sub_workflow_node.workflow_pool") as mock_pool,
            patch("codemie.workflows.workflow.WorkflowExecutor") as mock_executor_cls,
        ):
            mock_config.is_feature_enabled.return_value = True
            mock_config.SUBWORKFLOW_MAX_NESTING_DEPTH = 3

            parent_exec = MagicMock()
            parent_exec.active_sub_execution_id = None
            child_config = MagicMock()
            child_config.max_nesting_level = None
            finished_child = MagicMock(
                overall_status=WorkflowExecutionStatusEnum.SUCCEEDED,
                output="result",
            )
            parent_exec_final = MagicMock()

            mock_ws_inst = mock_ws.return_value
            mock_ws_inst.get_workflow.return_value = child_config
            mock_ws.get_nesting_depth.return_value = 0
            child_exec = MagicMock(execution_id="child-exec-1")
            mock_ws.create_workflow_execution.return_value = child_exec
            mock_ws.find_workflow_execution_by_id.side_effect = [
                parent_exec,
                finished_child,
                parent_exec_final,
            ]
            mock_pool.acquire.return_value = mock_graph
            mock_executor_cls.create_executor.return_value = MagicMock()

            pool_node.execute(state_schema, {})

        mock_pool.acquire.assert_called_once_with("child-wf-id", child_config)
        mock_pool.release.assert_called_once_with("child-wf-id", child_config, mock_graph)

    def test_create_path_releases_pool_graph_on_failure(self, pool_node, state_schema):
        """release() called in finally even when stream() raises."""
        mock_graph = MagicMock()
        mock_graph._node_slots = {}

        with (
            patch("codemie.workflows.nodes.sub_workflow_node.customer_config") as mock_config,
            patch("codemie.workflows.nodes.sub_workflow_node.WorkflowService") as mock_ws,
            patch("codemie.workflows.nodes.sub_workflow_node.workflow_pool") as mock_pool,
            patch("codemie.workflows.workflow.WorkflowExecutor") as mock_executor_cls,
        ):
            mock_config.is_feature_enabled.return_value = True
            mock_config.SUBWORKFLOW_MAX_NESTING_DEPTH = 3

            parent_exec = MagicMock()
            parent_exec.active_sub_execution_id = None
            child_config = MagicMock()
            child_config.max_nesting_level = None
            mock_ws_inst = mock_ws.return_value
            mock_ws_inst.get_workflow.return_value = child_config
            mock_ws.get_nesting_depth.return_value = 0
            child_exec = MagicMock(execution_id="child-exec-fail")
            mock_ws.create_workflow_execution.return_value = child_exec
            mock_ws.find_workflow_execution_by_id.return_value = parent_exec

            mock_pool.acquire.return_value = mock_graph
            mock_executor = MagicMock()
            mock_executor.stream.side_effect = RuntimeError("stream failed")
            mock_executor_cls.create_executor.return_value = mock_executor

            with pytest.raises(RuntimeError):
                pool_node.execute(state_schema, {})

        mock_pool.release.assert_called_once_with("child-wf-id", child_config, mock_graph)

    def test_resume_path_does_not_call_pool(self, pool_node, state_schema):
        """Resume path skips pool entirely."""
        child_execution_id = "child-exec-resume"
        child_exec_record = MagicMock(execution_id=child_execution_id, workflow_id="child-wf-id")
        parent_exec = MagicMock()
        parent_exec.active_sub_execution_id = child_execution_id
        child_config = MagicMock(max_nesting_level=None)
        finished_child = MagicMock(
            overall_status=WorkflowExecutionStatusEnum.SUCCEEDED,
            output="ok",
        )
        parent_exec_final = MagicMock()

        with (
            patch("codemie.workflows.nodes.sub_workflow_node.customer_config") as mock_config,
            patch("codemie.workflows.nodes.sub_workflow_node.WorkflowService") as mock_ws,
            patch("codemie.workflows.nodes.sub_workflow_node.workflow_pool") as mock_pool,
            patch("codemie.workflows.workflow.WorkflowExecutor") as mock_executor_cls,
        ):
            mock_config.is_feature_enabled.return_value = True
            mock_ws_inst = mock_ws.return_value
            mock_ws_inst.get_workflow.return_value = child_config
            mock_ws.find_workflow_execution_by_id.side_effect = [
                parent_exec,
                child_exec_record,
                finished_child,
                parent_exec_final,
            ]
            mock_executor_cls.create_executor.return_value = MagicMock()

            pool_node.execute(state_schema, {})

        mock_pool.acquire.assert_not_called()
        mock_pool.release.assert_not_called()


# ── T10: CR-002 null guard — finished_child None → SubWorkflowExecutionError ──


def test_execute_raises_when_finished_child_is_none(node, state_schema):
    """After stream(), if find_workflow_execution_by_id returns None for the child
    (transient DB error, ES refresh lag), SubWorkflowExecutionError must be raised
    rather than propagating AttributeError (CR-002 fix)."""
    child_exec = MagicMock(execution_id="child-id")
    child_config = MagicMock(max_nesting_level=None)
    parent_exec_initial = MagicMock()
    parent_exec_initial.active_sub_execution_id = None

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.customer_config") as mock_cfg,
        patch('codemie.workflows.nodes.sub_workflow_node.WorkflowService') as mock_svc,
        patch('codemie.workflows.workflow.WorkflowExecutor') as mock_executor,
    ):
        mock_cfg.is_feature_enabled.return_value = True
        mock_cfg.SUBWORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc.return_value.get_workflow.return_value = child_config
        mock_svc.get_nesting_depth.return_value = 0
        mock_svc.create_workflow_execution.return_value = child_exec
        mock_svc.find_workflow_execution_by_id.side_effect = [
            parent_exec_initial,  # initial parent check
            None,  # post-stream child lookup → simulates DB miss
        ]
        mock_executor.create_executor.return_value = MagicMock()

        with pytest.raises(SubWorkflowExecutionError):
            node.execute(state_schema, {})


# ── T11: CR-001 null guard — parent_exec None at terminal clear → no crash ───


def test_execute_tolerates_none_parent_exec_at_terminal_clear(node, state_schema):
    """At the terminal-clear step, if find_workflow_execution_by_id returns None
    (DB unavailable, row deleted), the clear must be silently skipped; execute()
    must still return the child output (CR-001 fix)."""
    child_exec = MagicMock(execution_id="child-id")
    child_config = MagicMock(max_nesting_level=None)
    finished_child = MagicMock(overall_status=WorkflowExecutionStatusEnum.SUCCEEDED)
    parent_exec_initial = MagicMock()
    parent_exec_initial.active_sub_execution_id = None

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.customer_config") as mock_cfg,
        patch('codemie.workflows.nodes.sub_workflow_node.WorkflowService') as mock_svc,
        patch('codemie.workflows.workflow.WorkflowExecutor') as mock_executor,
    ):
        mock_cfg.is_feature_enabled.return_value = True
        mock_cfg.SUBWORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc.return_value.get_workflow.return_value = child_config
        mock_svc.get_nesting_depth.return_value = 0
        mock_svc.create_workflow_execution.return_value = child_exec
        mock_svc.find_last_execution_state_output.return_value = "child output"
        mock_svc.find_workflow_execution_by_id.side_effect = [
            parent_exec_initial,  # initial parent check
            finished_child,  # post-stream child status
            None,  # terminal parent clear → DB miss
        ]
        mock_executor.create_executor.return_value = MagicMock()

        result = node.execute(state_schema, {})

    assert result == "child output"
