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
Stage 1.5 tests: the workflow execution thread binds the executing user into the request-scoped
ContextVar so per-user, membership-scoped credential resolution works in background workflow runs.

_execute_workflow_stream runs in a background thread that does not inherit contextvars, so it must
call set_current_user(self.user). Guarded by `if self.user`, a missing user leaves the ContextVar
unset (fail closed) rather than leaking another user's identity.
"""

import pytest
from unittest.mock import Mock, patch

from codemie.core.workflow_models import WorkflowConfig
from codemie.rest_api.security.user_context import get_current_user, set_current_user
from codemie.service.spend.origin_context import current_origin, spend_origin
from codemie.service.spend.traffic_class import TrafficOrigin
from codemie.workflows.workflow import WorkflowExecutor

EXECUTION_ID = "exec_ctx_123"


@pytest.fixture(autouse=True)
def _reset_current_user():
    """Isolate ContextVar state between tests."""
    set_current_user(None)
    yield
    set_current_user(None)


@pytest.fixture
def mock_user():
    user = Mock()
    user.id = "user_123"
    user.username = "test_user"
    return user


@pytest.fixture
def mock_thought_queue():
    queue = Mock()
    queue.set_context = Mock()
    return queue


@pytest.fixture
def basic_workflow_config():
    config = Mock(spec=WorkflowConfig)
    config.id = "wf_ctx_001"
    config.name = "Ctx Workflow"
    config.project = "test_project"
    config.states = []
    config.assistants = []
    config.tools = []
    config.enable_summarization_node = False
    config.is_global = False
    return config


def _make_executor(config, user, queue):
    with patch('codemie.workflows.workflow.WorkflowExecutionService'):
        return WorkflowExecutor(
            workflow_config=config,
            user_input="test",
            user=user,
            thought_queue=queue,
            execution_id=EXECUTION_ID,
        )


class TestWorkflowCurrentUserContext:
    """set_current_user in the workflow execution thread: set unconditionally, reset in finally."""

    @patch('codemie.workflows.workflow.get_observability_provider')
    @patch.object(WorkflowExecutor, '_auto_delete_execution')
    @patch.object(WorkflowExecutor, '_run_workflow_execution')
    @patch.object(WorkflowExecutor, '_build_graph_config')
    @patch.object(WorkflowExecutor, '_start_thought_consumer_if_enabled')
    def test_current_user_set_during_execution(
        self,
        mock_consumer,
        mock_config,
        mock_run,
        mock_auto_delete,
        mock_obs,
        basic_workflow_config,
        mock_user,
        mock_thought_queue,
    ):
        """The executing user is resolvable via get_current_user() inside the workflow thread."""
        executor = _make_executor(basic_workflow_config, mock_user, mock_thought_queue)

        captured = {}
        mock_run.side_effect = lambda *a, **kw: captured.update(user=get_current_user())

        executor._execute_workflow_stream()

        assert captured.get("user") is mock_user

    @patch('codemie.workflows.workflow.get_observability_provider')
    @patch.object(WorkflowExecutor, '_auto_delete_execution')
    @patch.object(WorkflowExecutor, '_run_workflow_execution')
    @patch.object(WorkflowExecutor, '_build_graph_config')
    @patch.object(WorkflowExecutor, '_start_thought_consumer_if_enabled')
    def test_current_user_not_set_when_user_missing(
        self,
        mock_consumer,
        mock_config,
        mock_run,
        mock_auto_delete,
        mock_obs,
        basic_workflow_config,
        mock_thought_queue,
    ):
        """Fail closed: no executing user leaves the ContextVar unset (no leaked identity)."""
        executor = _make_executor(basic_workflow_config, None, mock_thought_queue)

        captured = {}
        mock_run.side_effect = lambda *a, **kw: captured.update(user=get_current_user())

        executor._execute_workflow_stream()

        assert captured.get("user") is None

    @patch('codemie.workflows.workflow.get_observability_provider')
    @patch.object(WorkflowExecutor, '_auto_delete_execution')
    @patch.object(WorkflowExecutor, '_run_workflow_execution')
    @patch.object(WorkflowExecutor, '_build_graph_config')
    @patch.object(WorkflowExecutor, '_start_thought_consumer_if_enabled')
    def test_current_user_reset_after_execution(
        self,
        mock_consumer,
        mock_config,
        mock_run,
        mock_auto_delete,
        mock_obs,
        basic_workflow_config,
        mock_user,
        mock_thought_queue,
    ):
        """CR-002: the ContextVar is cleared after execution so a reused pool thread has no stale user."""
        executor = _make_executor(basic_workflow_config, mock_user, mock_thought_queue)

        executor._execute_workflow_stream()

        assert get_current_user() is None

    @patch('codemie.workflows.workflow.get_observability_provider')
    @patch.object(WorkflowExecutor, '_auto_delete_execution')
    @patch.object(WorkflowExecutor, '_run_workflow_execution')
    @patch.object(WorkflowExecutor, '_build_graph_config')
    @patch.object(WorkflowExecutor, '_start_thought_consumer_if_enabled')
    def test_stale_user_from_prior_run_is_overwritten(
        self,
        mock_consumer,
        mock_config,
        mock_run,
        mock_auto_delete,
        mock_obs,
        basic_workflow_config,
        mock_user,
        mock_thought_queue,
    ):
        """CR-002: a stale user left on the thread by a prior run is overwritten, not inherited."""
        stale = Mock()
        stale.id = "stale_999"
        set_current_user(stale)

        executor = _make_executor(basic_workflow_config, mock_user, mock_thought_queue)

        captured = {}
        mock_run.side_effect = lambda *a, **kw: captured.update(user=get_current_user())

        executor._execute_workflow_stream()

        # During the run the new user wins over the stale one; afterwards the var is cleared.
        assert captured.get("user") is mock_user
        assert get_current_user() is None


class TestWorkflowSpendOrigin:
    """The workflow run publishes its spend origin on the same thread that builds its node models."""

    @patch('codemie.workflows.workflow.get_observability_provider')
    @patch.object(WorkflowExecutor, '_auto_delete_execution')
    @patch.object(WorkflowExecutor, '_run_workflow_execution')
    @patch.object(WorkflowExecutor, '_build_graph_config')
    @patch.object(WorkflowExecutor, '_start_thought_consumer_if_enabled')
    def test_origin_is_workflow_during_execution_and_gone_after(
        self,
        mock_consumer,
        mock_config,
        mock_run,
        mock_auto_delete,
        mock_obs,
        basic_workflow_config,
        mock_user,
        mock_thought_queue,
    ):
        """Node models are built inside the graph run, so the origin must be live there."""
        executor = _make_executor(basic_workflow_config, mock_user, mock_thought_queue)

        captured = {}
        mock_run.side_effect = lambda *a, **kw: captured.update(origin=current_origin())

        executor._execute_workflow_stream()

        assert captured.get("origin") is TrafficOrigin.WORKFLOW
        assert current_origin() is None

    @patch('codemie.workflows.workflow.get_observability_provider')
    @patch.object(WorkflowExecutor, '_auto_delete_execution')
    @patch.object(WorkflowExecutor, '_build_graph_config')
    @patch.object(WorkflowExecutor, '_start_thought_consumer_if_enabled')
    def test_sub_workflow_hands_the_parent_origin_back(
        self,
        mock_consumer,
        mock_config,
        mock_auto_delete,
        mock_obs,
        basic_workflow_config,
        mock_user,
        mock_thought_queue,
    ):
        """A sub-workflow re-enters this body on the parent's thread; returning must not clear the origin.

        This is where the origin parts company with `set_current_workflow_id`, which the child
        overwrites and clears to None on the way out.
        """
        parent = _make_executor(basic_workflow_config, mock_user, mock_thought_queue)
        child = _make_executor(basic_workflow_config, mock_user, mock_thought_queue)

        captured = {}
        child._run_workflow_execution = lambda *a, **kw: captured.update(inside_child=current_origin())

        def run_child(*_args, **_kwargs):
            child._execute_workflow_stream()
            captured.update(parent_after_child=current_origin())

        parent._run_workflow_execution = run_child

        parent._execute_workflow_stream()

        assert captured.get("inside_child") is TrafficOrigin.WORKFLOW
        assert captured.get("parent_after_child") is TrafficOrigin.WORKFLOW

    @patch('codemie.workflows.workflow.get_observability_provider')
    @patch.object(WorkflowExecutor, '_auto_delete_execution')
    @patch.object(WorkflowExecutor, '_run_workflow_execution')
    @patch.object(WorkflowExecutor, '_build_graph_config')
    @patch.object(WorkflowExecutor, '_start_thought_consumer_if_enabled')
    def test_enclosing_origin_is_restored_not_cleared(
        self,
        mock_consumer,
        mock_config,
        mock_run,
        mock_auto_delete,
        mock_obs,
        basic_workflow_config,
        mock_user,
        mock_thought_queue,
    ):
        """A workflow reached from work that already published an origin gives that origin back."""
        executor = _make_executor(basic_workflow_config, mock_user, mock_thought_queue)

        captured = {}
        mock_run.side_effect = lambda *a, **kw: captured.update(origin=current_origin())

        with spend_origin(TrafficOrigin.CONVERSATION):
            executor._execute_workflow_stream()

            assert captured.get("origin") is TrafficOrigin.WORKFLOW
            assert current_origin() is TrafficOrigin.CONVERSATION
