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
Unit tests for SupervisorWorkflowExecutor in src/codemie/agents/langgraph/supervisor_workflow.py.
"""

import unittest
from unittest.mock import patch, MagicMock

from codemie.core.workflow_models import WorkflowConfig
from codemie.rest_api.security.user import User
from codemie.workflows.constants import SUPERVISOR_NODE
from codemie.workflows.nodes.result_finalizer_node import ResultFinalizerNode
from codemie.workflows.nodes.summarize_conversation_node import SummarizeConversationNode
from codemie.workflows.supervisor_workflow import SupervisorWorkflowExecutor


class TestSupervisorWorkflowExecutor(unittest.TestCase):
    @patch(
        "codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionService.find_workflow_execution"
    )
    @patch('codemie.service.workflow_execution.WorkflowExecutionService._refresh_workflow_execution')
    def setUp(self, _mock_refresh, mock_find_workflow_execution):
        mock_find_workflow_execution.return_value = MagicMock()
        self.workflow_config = WorkflowConfig(
            id="test_workflow", name="Test Workflow", description="A test workflow configuration.", assistants=[]
        )  # Simplified config for testing
        self.user_input = "Sample user input"
        self.user = User(id="test_user", username="testuser")
        self.executor = SupervisorWorkflowExecutor(
            workflow_config=self.workflow_config, user_input=self.user_input, user=self.user
        )

    @patch('codemie.service.workflow_execution.WorkflowExecutionService._refresh_workflow_execution')
    def test_init_state_graph(self, _mock_refresh):
        state_graph = self.executor.init_state_graph()
        self.assertIsNotNone(state_graph)

    @patch('codemie.workflows.workflow.initialize_assistant')
    def test_member_assistant_build_keeps_workflow_attribution(self, mock_initialize):
        """EPMCDME-16625: the supervisor's model is built after its members and must keep the workflow's billing."""
        from codemie.core.dependecies import get_current_project, set_litellm_context
        from codemie.rest_api.models.settings import LiteLLMContext

        def build_member(**_kwargs):
            set_litellm_context(LiteLLMContext(credentials=None, current_project="member-project"))
            return MagicMock()

        mock_initialize.side_effect = build_member
        set_litellm_context(LiteLLMContext(credentials=None, current_project="workflow-project"))

        self.executor.initialize_assistant(assistant=MagicMock())

        self.assertEqual(get_current_project(), "workflow-project")

    @patch('codemie.service.workflow_execution.WorkflowExecutionService._refresh_workflow_execution')
    def test_build_workflow(self, _mock_refresh):
        state_graph = self.executor.init_state_graph()
        self.executor.build_workflow(state_graph)
        self.assertIn(SUPERVISOR_NODE, state_graph.nodes)

    @patch('codemie.service.workflow_execution.WorkflowExecutionService._refresh_workflow_execution')
    def test_build_workflow_passes_user_to_fallback_nodes(self, _mock_refresh):
        self.workflow_config.enable_summarization_node = True
        with (
            patch('codemie.workflows.supervisor_workflow.ResultFinalizerNode') as finalizer_cls,
            patch('codemie.workflows.supervisor_workflow.SummarizeConversationNode') as summarize_cls,
        ):
            self.executor.build_workflow(self.executor.init_state_graph())

        self.assertIs(finalizer_cls.call_args.kwargs["user"], self.user)
        self.assertIs(summarize_cls.call_args.kwargs["user"], self.user)

    @patch('codemie.service.workflow_execution.WorkflowExecutionService._refresh_workflow_execution')
    def test_build_workflow_passes_user_to_summarize_node_without_finalizer(self, _mock_refresh):
        self.workflow_config.enable_summarization_node = False
        with (
            patch('codemie.workflows.supervisor_workflow.ResultFinalizerNode') as finalizer_cls,
            patch('codemie.workflows.supervisor_workflow.SummarizeConversationNode') as summarize_cls,
        ):
            self.executor.build_workflow(self.executor.init_state_graph())

        finalizer_cls.assert_not_called()
        self.assertIs(summarize_cls.call_args.kwargs["user"], self.user)

    @patch('codemie.service.workflow_execution.WorkflowExecutionService._refresh_workflow_execution')
    def test_fallback_nodes_resolve_user_override_default_for_executing_user(self, _mock_refresh):
        self.workflow_config.enable_summarization_node = True
        self.workflow_config.project = "proj-1"
        built = {}

        def capture(name, node_cls):
            def factory(*args, **kwargs):
                built[name] = node_cls(*args, **kwargs)
                return built[name]

            return factory

        with (
            patch(
                'codemie.workflows.supervisor_workflow.ResultFinalizerNode',
                side_effect=capture("finalizer", ResultFinalizerNode),
            ),
            patch(
                'codemie.workflows.supervisor_workflow.SummarizeConversationNode',
                side_effect=capture("summarize", SummarizeConversationNode),
            ),
            patch(
                'codemie.workflows.nodes.base_node.ModelAvailabilityService.resolve_user_override_model',
                return_value="override-default",
            ) as get_default,
        ):
            self.executor.build_workflow(self.executor.init_state_graph())

            self.assertEqual(built["finalizer"].resolve_user_override_model("requested"), "override-default")
            self.assertEqual(built["summarize"].resolve_user_override_model("requested"), "override-default")

        for call in get_default.call_args_list:
            self.assertEqual(call.kwargs["user_id"], "test_user")
            self.assertEqual(call.kwargs["project_name"], "proj-1")
            self.assertFalse(call.kwargs["is_global"])
