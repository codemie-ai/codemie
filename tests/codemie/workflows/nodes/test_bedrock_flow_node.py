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

"""Tests for BedrockFlowNode workflow node."""

import pytest
from unittest.mock import Mock, patch
from langchain_core.messages import HumanMessage

from codemie.core.workflow_models.workflow_models import (
    CustomWorkflowNode,
    WorkflowState,
)
from codemie.rest_api.models.settings import Settings
from codemie.service.workflow_execution import WorkflowExecutionService
from codemie.workflows.callbacks.base_callback import BaseCallback
from codemie.workflows.models import AgentMessages
from codemie.workflows.nodes.bedrock_flow_node import BedrockFlowNode


@pytest.fixture
def mock_workflow_execution_service():
    """Create a mock WorkflowExecutionService."""
    service = Mock(spec=WorkflowExecutionService)
    service.user = Mock()
    service.user.id = "test-user-id"
    service.workflow_config = Mock()
    service.workflow_config.project = "test-project"
    service.workflow_execution_id = "test-execution-id"
    return service


@pytest.fixture
def mock_thought_queue():
    """Create a mock ThoughtQueue."""
    return Mock()


@pytest.fixture
def mock_callbacks():
    """Create a list of mock callbacks."""
    return [Mock(spec=BaseCallback)]


@pytest.fixture
def mock_workflow_state():
    """Create a mock WorkflowState."""
    state = Mock(spec=WorkflowState)
    state.task = "Test task"
    return state


@pytest.fixture
def custom_node_config():
    """Create a valid custom node configuration."""
    return {
        "flow_id": "test-flow-id",
        "flow_alias_id": "test-flow-alias-id",
        "setting_id": "test-setting-id",
        "input_node_name": "test-input-node",
        "input_node_output_field": "test-output-field",
        "input_node_output_type": "String",
    }


@pytest.fixture
def custom_node(custom_node_config):
    """Create a CustomWorkflowNode with valid configuration."""
    node = Mock(spec=CustomWorkflowNode)
    node.config = custom_node_config
    return node


class TestBedrockFlowNodeInitialization:
    """Test BedrockFlowNode initialization."""

    def test_init_with_valid_config(
        self, mock_callbacks, mock_workflow_execution_service, mock_thought_queue, mock_workflow_state, custom_node
    ):
        """Test successful initialization with valid configuration."""
        node = BedrockFlowNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            node_name="test-bedrock-node",
            custom_node=custom_node,
        )

        assert node.flow_id == "test-flow-id"
        assert node.flow_alias_id == "test-flow-alias-id"
        assert node.setting_id == "test-setting-id"
        assert node.input_node_name == "test-input-node"
        assert node.input_node_output_field == "test-output-field"
        assert node.input_node_output_type == "String"

    def test_init_without_custom_node_raises_error(
        self, mock_callbacks, mock_workflow_execution_service, mock_thought_queue, mock_workflow_state
    ):
        """Test that initialization without custom_node raises ValueError."""
        with pytest.raises(ValueError, match="Custom node configuration is required for BedrockFlowNode"):
            BedrockFlowNode(
                callbacks=mock_callbacks,
                workflow_execution_service=mock_workflow_execution_service,
                thought_queue=mock_thought_queue,
                workflow_state=mock_workflow_state,
                node_name="test-bedrock-node",
            )

    def test_init_with_integration_alias(
        self, mock_callbacks, mock_workflow_execution_service, mock_thought_queue, mock_workflow_state
    ):
        """Test initialization with integration_alias instead of setting_id."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.config = {
            "flow_id": "test-flow-id",
            "flow_alias_id": "test-flow-alias-id",
            "integration_alias": "test-integration-alias",
            "input_node_name": "test-input-node",
            "input_node_output_field": "test-output-field",
            "input_node_output_type": "String",
        }

        mock_settings = Mock(spec=Settings)
        mock_settings.id = "resolved-setting-id"

        with patch.object(Settings, 'get_by_alias', return_value=mock_settings):
            node = BedrockFlowNode(
                callbacks=mock_callbacks,
                workflow_execution_service=mock_workflow_execution_service,
                thought_queue=mock_thought_queue,
                workflow_state=mock_workflow_state,
                node_name="test-bedrock-node",
                custom_node=custom_node,
            )

            assert node.setting_id == "resolved-setting-id"

    def test_init_with_integration_alias_not_found(
        self, mock_callbacks, mock_workflow_execution_service, mock_thought_queue, mock_workflow_state
    ):
        """Test initialization with integration_alias when settings not found."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.config = {
            "flow_id": "test-flow-id",
            "flow_alias_id": "test-flow-alias-id",
            "integration_alias": "non-existent-alias",
            "input_node_name": "test-input-node",
            "input_node_output_field": "test-output-field",
            "input_node_output_type": "String",
        }

        with patch.object(Settings, 'get_by_alias', return_value=None):
            with pytest.raises(ValueError, match="Settings with alias 'non-existent-alias' not found"):
                BedrockFlowNode(
                    callbacks=mock_callbacks,
                    workflow_execution_service=mock_workflow_execution_service,
                    thought_queue=mock_thought_queue,
                    workflow_state=mock_workflow_state,
                    node_name="test-bedrock-node",
                    custom_node=custom_node,
                )

    def test_init_without_setting_id_or_integration_alias(
        self, mock_callbacks, mock_workflow_execution_service, mock_thought_queue, mock_workflow_state
    ):
        """Test initialization without setting_id or integration_alias raises ValueError."""
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.config = {
            "flow_id": "test-flow-id",
            "flow_alias_id": "test-flow-alias-id",
            "input_node_name": "test-input-node",
            "input_node_output_field": "test-output-field",
            "input_node_output_type": "String",
        }

        with pytest.raises(
            ValueError, match="Either 'setting_id' or 'integration_alias' must be provided in the node configuration"
        ):
            BedrockFlowNode(
                callbacks=mock_callbacks,
                workflow_execution_service=mock_workflow_execution_service,
                thought_queue=mock_thought_queue,
                workflow_state=mock_workflow_state,
                node_name="test-bedrock-node",
                custom_node=custom_node,
            )


class TestBedrockFlowNodeExecution:
    """Test BedrockFlowNode execution."""

    @patch('codemie.workflows.nodes.bedrock_flow_node.BedrockFlowService')
    def test_execute_with_messages(
        self,
        mock_bedrock_service,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        custom_node,
    ):
        """Test execute method with messages in input_data."""
        node = BedrockFlowNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            node_name="test-bedrock-node",
            custom_node=custom_node,
        )

        input_data = {"messages": [HumanMessage(content="Test message content")]}
        execution_context = {}

        mock_bedrock_service.invoke_flow.return_value = {"output": "Test output"}

        result = node.execute(input_data, execution_context)

        mock_bedrock_service.invoke_flow.assert_called_once_with(
            flow_id="test-flow-id",
            flow_alias_id="test-flow-alias-id",
            user=mock_workflow_execution_service.user,
            setting_id="test-setting-id",
            inputs=[
                {
                    "content": {"document": "Test message content"},
                    "nodeName": "test-input-node",
                    "nodeOutputName": "test-output-field",
                }
            ],
        )
        assert result == {"output": "Test output"}

    @patch('codemie.workflows.nodes.bedrock_flow_node.BedrockFlowService')
    def test_execute_without_messages(
        self,
        mock_bedrock_service,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        custom_node,
    ):
        """Test execute method without messages in input_data."""
        node = BedrockFlowNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            node_name="test-bedrock-node",
            custom_node=custom_node,
        )

        input_data = {}
        execution_context = {}

        mock_bedrock_service.invoke_flow.return_value = {"output": "Empty input output"}

        node.execute(input_data, execution_context)

        mock_bedrock_service.invoke_flow.assert_called_once()
        call_args = mock_bedrock_service.invoke_flow.call_args[1]
        assert call_args["inputs"][0]["content"]["document"] == ""

    @patch('codemie.workflows.nodes.bedrock_flow_node.BedrockFlowService')
    def test_execute_with_empty_messages_list(
        self,
        mock_bedrock_service,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        custom_node,
    ):
        """Test execute method with empty messages list."""
        node = BedrockFlowNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            node_name="test-bedrock-node",
            custom_node=custom_node,
        )

        input_data = {"messages": []}
        execution_context = {}

        mock_bedrock_service.invoke_flow.return_value = {"output": "Empty list output"}

        node.execute(input_data, execution_context)

        call_args = mock_bedrock_service.invoke_flow.call_args[1]
        assert call_args["inputs"][0]["content"]["document"] == ""


class TestBedrockFlowNodeMethods:
    """Test BedrockFlowNode helper methods."""

    def test_get_task(
        self, mock_callbacks, mock_workflow_execution_service, mock_thought_queue, mock_workflow_state, custom_node
    ):
        """Test get_task method returns correct task description."""
        node = BedrockFlowNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            node_name="test-bedrock-node",
            custom_node=custom_node,
        )

        state_schema = Mock(spec=AgentMessages)
        task = node.get_task(state_schema)

        assert task == "Triggering aws bedrock flow"

    def test_post_process_output_with_output_key(
        self, mock_callbacks, mock_workflow_execution_service, mock_thought_queue, mock_workflow_state, custom_node
    ):
        """Test post_process_output extracts output correctly."""
        node = BedrockFlowNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            node_name="test-bedrock-node",
            custom_node=custom_node,
        )

        state_schema = Mock(spec=AgentMessages)
        task = "Test task"
        output = {"output": "Expected output value"}

        result = node.post_process_output(state_schema, task, output)

        assert result == "Expected output value"

    def test_post_process_output_without_output_key(
        self, mock_callbacks, mock_workflow_execution_service, mock_thought_queue, mock_workflow_state, custom_node
    ):
        """Test post_process_output returns empty string when output key missing."""
        node = BedrockFlowNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            node_name="test-bedrock-node",
            custom_node=custom_node,
        )

        state_schema = Mock(spec=AgentMessages)
        task = "Test task"
        output = {"other_key": "some value"}

        result = node.post_process_output(state_schema, task, output)

        assert result == ""


class TestBedrockFlowNodeConfigRetrieval:
    """Test BedrockFlowNode configuration retrieval from workflow."""

    @patch('codemie.workflows.nodes.bedrock_flow_node.WorkflowService')
    def test_get_custom_node_config_with_workflow_id(
        self,
        mock_workflow_service_class,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test _get_custom_node_config retrieves config from referenced workflow."""
        # Setup mock workflow service
        mock_workflow_service = Mock()
        mock_workflow_service_class.return_value = mock_workflow_service

        # Create mock workflow with bedrock flow node
        mock_workflow_obj = Mock()
        mock_bedrock_node = Mock()
        mock_bedrock_node.custom_node_id = "bedrock_flow_node"
        mock_bedrock_node.config = {
            "flow_id": "workflow-flow-id",
            "flow_alias_id": "workflow-flow-alias-id",
            "setting_id": "workflow-setting-id",
            "input_node_name": "workflow-input-node",
            "input_node_output_field": "workflow-output-field",
            "input_node_output_type": "String",
        }
        mock_workflow_obj.custom_nodes = [mock_bedrock_node]
        mock_workflow_service.get_workflow.return_value = mock_workflow_obj

        # Create custom node that references a workflow
        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.config = {"workflow_id": "referenced-workflow-id"}

        node = BedrockFlowNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
            node_name="test-bedrock-node",
            custom_node=custom_node,
        )

        assert node.flow_id == "workflow-flow-id"
        assert node.flow_alias_id == "workflow-flow-alias-id"
        assert node.setting_id == "workflow-setting-id"

    @patch('codemie.workflows.nodes.bedrock_flow_node.WorkflowService')
    def test_get_custom_node_config_workflow_not_found(
        self,
        mock_workflow_service_class,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test _get_custom_node_config raises error when workflow not found."""
        mock_workflow_service = Mock()
        mock_workflow_service_class.return_value = mock_workflow_service
        mock_workflow_service.get_workflow.return_value = None

        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.config = {"workflow_id": "non-existent-workflow-id"}

        with pytest.raises(ValueError, match="WorkflowConfig with id non-existent-workflow-id not found"):
            BedrockFlowNode(
                callbacks=mock_callbacks,
                workflow_execution_service=mock_workflow_execution_service,
                thought_queue=mock_thought_queue,
                workflow_state=mock_workflow_state,
                node_name="test-bedrock-node",
                custom_node=custom_node,
            )

    @patch('codemie.workflows.nodes.bedrock_flow_node.WorkflowService')
    def test_get_custom_node_config_no_bedrock_node_in_workflow(
        self,
        mock_workflow_service_class,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test _get_custom_node_config raises error when workflow has no BedrockFlowNode."""
        mock_workflow_service = Mock()
        mock_workflow_service_class.return_value = mock_workflow_service

        mock_workflow_obj = Mock()
        mock_workflow_obj.custom_nodes = []
        mock_workflow_service.get_workflow.return_value = mock_workflow_obj

        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.config = {"workflow_id": "workflow-without-bedrock-node"}

        with pytest.raises(ValueError, match="The referenced workflow does not contain a BedrockFlowNode"):
            BedrockFlowNode(
                callbacks=mock_callbacks,
                workflow_execution_service=mock_workflow_execution_service,
                thought_queue=mock_thought_queue,
                workflow_state=mock_workflow_state,
                node_name="test-bedrock-node",
                custom_node=custom_node,
            )

    @patch('codemie.workflows.nodes.bedrock_flow_node.WorkflowService')
    def test_get_custom_node_config_wrong_custom_node_id(
        self,
        mock_workflow_service_class,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test _get_custom_node_config raises error when workflow has wrong custom_node_id."""
        mock_workflow_service = Mock()
        mock_workflow_service_class.return_value = mock_workflow_service

        mock_workflow_obj = Mock()
        mock_other_node = Mock()
        mock_other_node.custom_node_id = "some_other_node"
        mock_workflow_obj.custom_nodes = [mock_other_node]
        mock_workflow_service.get_workflow.return_value = mock_workflow_obj

        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.config = {"workflow_id": "workflow-with-wrong-node"}

        with pytest.raises(ValueError, match="The referenced workflow does not contain a BedrockFlowNode"):
            BedrockFlowNode(
                callbacks=mock_callbacks,
                workflow_execution_service=mock_workflow_execution_service,
                thought_queue=mock_thought_queue,
                workflow_state=mock_workflow_state,
                node_name="test-bedrock-node",
                custom_node=custom_node,
            )


class TestBedrockFlowNodeNormalizeInput:
    """Tests for _normalize_input static method."""

    def test_normalize_string(self):
        assert BedrockFlowNode._normalize_input("hello", "String") == "hello"

    def test_normalize_number_int(self):
        assert BedrockFlowNode._normalize_input("42", "Number") == 42

    def test_normalize_number_float(self):
        assert BedrockFlowNode._normalize_input("3.14", "Number") == 3.14

    def test_normalize_number_invalid_raises(self):
        with pytest.raises(ValueError, match="Error normalizing input to type 'Number'"):
            BedrockFlowNode._normalize_input("not-a-number", "Number")

    def test_normalize_boolean_true(self):
        assert BedrockFlowNode._normalize_input("true", "Boolean") is True
        assert BedrockFlowNode._normalize_input("Yes", "Boolean") is True
        assert BedrockFlowNode._normalize_input("1", "Boolean") is True

    def test_normalize_boolean_false(self):
        assert BedrockFlowNode._normalize_input("false", "Boolean") is False
        assert BedrockFlowNode._normalize_input("No", "Boolean") is False
        assert BedrockFlowNode._normalize_input("0", "Boolean") is False

    def test_normalize_boolean_invalid_raises(self):
        with pytest.raises(ValueError, match="Error normalizing input to type 'Boolean'"):
            BedrockFlowNode._normalize_input("maybe", "Boolean")

    def test_normalize_array_valid(self):
        assert BedrockFlowNode._normalize_input('["a", 1]', "Array") == ["a", 1]

    def test_normalize_array_invalid_raises(self):
        with pytest.raises(ValueError, match="Error normalizing input to type 'Array'"):
            BedrockFlowNode._normalize_input('{"a":1}', "Array")

    def test_normalize_object_valid(self):
        assert BedrockFlowNode._normalize_input('{"key": "value"}', "Object") == {"key": "value"}
        assert BedrockFlowNode._normalize_input('{"key_1": "value_1",\n     "key_2": "value_2"\n}', "Object") == {
            "key_1": "value_1",
            "key_2": "value_2",
        }

    def test_normalize_object_invalid_raises(self):
        with pytest.raises(ValueError, match="Error normalizing input to type 'Object'"):
            BedrockFlowNode._normalize_input('["not","dict"]', "Object")
        with pytest.raises(ValueError, match="Error normalizing input to type 'Object'"):
            BedrockFlowNode._normalize_input('', "Object")

    def test_normalize_none_expected_type_returns_raw(self):
        assert BedrockFlowNode._normalize_input("raw value", None) == "raw value"

    def test_normalize_unknown_type_fallback(self):
        # Falls through returning original string
        assert BedrockFlowNode._normalize_input("something", "UNSUPPORTED_TYPE") == "something"

    def test_normalize_empty_string_string_type(self):
        assert BedrockFlowNode._normalize_input("", "String") == ""

    def test_normalize_number_with_whitespace(self):
        assert BedrockFlowNode._normalize_input(" 7 ", "Number") == 7

    def test_normalize_boolean_case_insensitive(self):
        assert BedrockFlowNode._normalize_input("TrUe", "Boolean") is True
        assert BedrockFlowNode._normalize_input("FaLsE", "Boolean") is False
