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
Test Area: Agent Node Context Handling

Tests for AgentNode context handling, task preparation, dynamic value resolution,
and agent-specific execution logic.

This module tests the following critical functionality:
- Agent task preparation with dynamic values
- Current task key and file attachment handling
- Context store usage in agent execution
- MCP server args preprocessing
- Memory summarization trigger
- Agent node name with iteration info
- Task result processing (success/failure)
- Agent cleanup after execution
- Execution context generation
"""

import pytest
from unittest.mock import MagicMock, Mock, patch
from langgraph.types import Command

from codemie.rest_api.models.assistant import Assistant
from codemie.workflows.nodes.agent_node import AgentNode
from codemie.agents.assistant_agent import TaskResult
from codemie.core.exceptions import TaskException
from codemie.workflows.constants import (
    CONTEXT_STORE_VARIABLE,
    MESSAGES_VARIABLE,
    SUMMARIZE_MEMORY_NODE,
    CURRENT_TASK_KEY,
    ITERATION_NODE_NUMBER_KEY,
    TOTAL_ITERATIONS_KEY,
)
from codemie.core.workflow_models import WorkflowNextState, WorkflowState, WorkflowConfig, WorkflowAssistant
from langchain_core.messages import HumanMessage


@pytest.fixture
def mock_workflow_execution_service():
    """Create mock WorkflowExecutionService."""
    service = Mock()
    service.start_state = Mock(return_value="state_123")
    service.finish_state = Mock()
    return service


@pytest.fixture
def mock_thought_queue():
    """Create mock ThoughtQueue."""
    return Mock()


@pytest.fixture
def mock_callbacks():
    """Create mock callbacks."""
    callback = Mock()
    callback.on_node_start = Mock()
    callback.on_node_end = Mock()
    return [callback]


@pytest.fixture
def mock_assistant():
    """Create mock AIToolsAgent."""
    assistant = MagicMock(spec=Assistant)
    assistant.id = "test-assistant-id"
    assistant.project = "test-project"
    assistant.invoke_task = Mock(return_value=TaskResult(success=True, result="Agent response"))
    return assistant


@pytest.fixture
def mock_workflow_config():
    """Create mock WorkflowConfig."""
    config = Mock(spec=WorkflowConfig)
    config.project = "test_project"
    config.assistants = [
        Mock(
            spec=WorkflowAssistant,
            id="assistant_1",
            assistant_id=None,  # Virtual assistant (no persistent ID)
        )
    ]
    config.history_window = 10
    config.enable_summarization = True
    return config


@pytest.fixture
def mock_user():
    """Create mock User."""
    user = Mock()
    user.username = "test_user"
    return user


def test_tc_anc_001_agent_task_preparation_with_dynamic_values(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks, mock_assistant, mock_workflow_config, mock_user
):
    """
    TC_ANC_001: Agent Task Preparation with Dynamic Values

    Verify dynamic value resolution in agent task templates.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {"user_name": "Alice", "count": "5"},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="agent_node",
        task="Process data for {{user_name}} with {{count}} items",
        next=WorkflowNextState(state_id="next"),
        resolve_dynamic_values_in_prompt=True,
        assistant_id="assistant_1",
    )

    node = AgentNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        assistant=mock_assistant,
        user=mock_user,
        execution_id="exec_123",
    )

    # Act
    task = node.get_task(state_schema)

    # Assert
    # Task should have dynamic values resolved
    assert "Alice" in task
    assert "5" in task
    assert "{{user_name}}" not in task  # Placeholders replaced
    assert "{{count}}" not in task


def test_tc_anc_002_agent_task_with_current_task_key(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks, mock_assistant, mock_workflow_config, mock_user
):
    """
    TC_ANC_002: Agent Task with Current Task Key

    Test iteration task appended to main task.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
        CURRENT_TASK_KEY: "Process item #3",
    }

    workflow_state = WorkflowState(
        id="agent_node",
        task="Main workflow task",
        next=WorkflowNextState(state_id="next"),
        assistant_id="assistant_1",
    )

    node = AgentNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        assistant=mock_assistant,
        user=mock_user,
        execution_id="exec_123",
        current_task_key=CURRENT_TASK_KEY,
    )

    # Act
    task = node.get_task(state_schema)

    # Assert
    # Task should include both main task and current task
    assert "Main workflow task" in task
    assert "Current task: Process item #3" in task


@patch("codemie.workflows.nodes.agent_node.FileObject")
def test_tc_anc_003_agent_task_with_file_attachment(
    mock_file_object_class,
    mock_workflow_execution_service,
    mock_thought_queue,
    mock_callbacks,
    mock_assistant,
    mock_workflow_config,
    mock_user,
):
    """
    TC_ANC_003: Agent Task with File Attachment

    Test task with FileObject.from_encoded_url().
    """
    # Arrange
    mock_decoded_file = Mock()
    mock_decoded_file.name = "document.pdf"
    mock_file_object_class.from_encoded_url = Mock(return_value=mock_decoded_file)

    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="agent_node",
        task="Analyze the attached document",
        next=WorkflowNextState(state_id="next"),
        assistant_id="assistant_1",
    )

    node = AgentNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        assistant=mock_assistant,
        user=mock_user,
        execution_id="exec_123",
        file_names=["encoded_file_url_123"],
    )

    # Act
    task = node.get_task(state_schema)

    # Assert
    # File name should be appended to task
    assert "Analyze the attached document" in task
    assert "File attached: document.pdf" in task

    # Verify FileObject.from_encoded_url was called
    mock_file_object_class.from_encoded_url.assert_called_once_with("encoded_file_url_123")


def test_tc_anc_004_agent_context_store_usage(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks, mock_assistant, mock_workflow_config, mock_user
):
    """
    TC_ANC_004: Agent Context Store Usage

    Verify context store passed to agent for tool execution.
    """
    # Arrange
    context_store_data = {"api_key": "test_key", "base_url": "https://api.example.com"}
    state_schema = {
        CONTEXT_STORE_VARIABLE: context_store_data,
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="agent_node",
        task="Execute API call",
        next=WorkflowNextState(state_id="next"),
        assistant_id="assistant_1",
    )

    node = AgentNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        assistant=mock_assistant,
        user=mock_user,
        execution_id="exec_123",
    )

    # Act
    execution_context = node.generate_execution_context(state_schema)

    # Assert
    # Assistant should be present in context
    assert "assistant" in execution_context
    assert execution_context["assistant"] == mock_assistant


@patch("codemie.workflows.nodes.agent_node.process_string")
def test_tc_anc_005_agent_initialization_with_mcp_preprocessor(
    mock_process_string,
    mock_workflow_execution_service,
    mock_thought_queue,
    mock_callbacks,
    mock_workflow_config,
    mock_user,
):
    """
    TC_ANC_005: Agent Initialization with MCP Preprocessor

    Test MCP server args preprocessing with dynamic values.
    """
    # Arrange
    mock_process_string.return_value = "processed_value"

    state_schema = {
        CONTEXT_STORE_VARIABLE: {"key": "value"},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="agent_node",
        task="Test task",
        next=WorkflowNextState(state_id="next"),
        assistant_id="assistant_1",
    )

    node = AgentNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        user=mock_user,
        execution_id="exec_123",
        assistant=None,  # Force initialization
    )

    # Mock init_assistant to capture preprocessor
    mock_init_result = Mock()
    preprocessor_captured = None

    def capture_preprocessor(mcp_server_args_preprocessor=None):
        nonlocal preprocessor_captured
        preprocessor_captured = mcp_server_args_preprocessor
        return mock_init_result

    node.init_assistant = capture_preprocessor

    # Act
    execution_context = node.generate_execution_context(state_schema)

    # Assert
    assert execution_context["assistant"] == mock_init_result
    assert preprocessor_captured is not None

    # Test preprocessor
    result = preprocessor_captured("test_arg", {})
    assert result == "processed_value"


@patch("codemie.workflows.nodes.agent_node.should_summarize_memory")
@patch("codemie.service.guardrail.guardrail_service.GuardrailService.get_effective_guardrails")
def test_tc_anc_006_agent_memory_summarization_trigger(
    mock_get_guardrails,
    mock_should_summarize,
    mock_workflow_execution_service,
    mock_thought_queue,
    mock_callbacks,
    mock_assistant,
    mock_workflow_config,
    mock_user,
):
    """
    TC_ANC_006: Agent Memory Summarization Trigger

    Test before_execution() redirect to SUMMARIZE_MEMORY_NODE.
    """
    # Arrange
    mock_should_summarize.return_value = (10, True)  # Message count, should summarize
    mock_get_guardrails.return_value = []  # No guardrails configured

    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [HumanMessage(content=f"msg_{i}") for i in range(15)],
    }

    workflow_state = WorkflowState(
        id="agent_node",
        task="Task",
        next=WorkflowNextState(state_id="next"),
        assistant_id="assistant_1",
    )

    node = AgentNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        assistant=mock_assistant,
        user=mock_user,
        execution_id="exec_123",
        summarize_history=True,
    )

    context = node.generate_execution_context(state_schema)

    # Act
    result = node.before_execution(state_schema, context)

    # Assert
    # Should return Command to redirect to SUMMARIZE_MEMORY_NODE
    assert isinstance(result, Command)
    assert result.goto == SUMMARIZE_MEMORY_NODE


def test_tc_anc_007_agent_node_name_with_iteration(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks, mock_assistant, mock_workflow_config, mock_user
):
    """
    TC_ANC_007: Agent Node Name with Iteration

    Verify NodeName X of Y format in iterations.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
        ITERATION_NODE_NUMBER_KEY: 3,
        TOTAL_ITERATIONS_KEY: 10,
        CURRENT_TASK_KEY: "item_3",
    }

    workflow_state = WorkflowState(
        id="agent_node",
        task="Process item",
        next=WorkflowNextState(state_id="next"),
        assistant_id="assistant_1",
    )

    node = AgentNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        assistant=mock_assistant,
        user=mock_user,
        execution_id="exec_123",
        node_name="ProcessItem",
        current_task_key=CURRENT_TASK_KEY,
    )

    # Act
    node_name = node.get_node_name(state_schema)

    # Assert
    # Should include iteration info
    assert "ProcessItem" in node_name
    assert "3 of 10" in node_name


def test_tc_anc_008_agent_task_result_processing_success(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks, mock_assistant, mock_workflow_config, mock_user
):
    """
    TC_ANC_008: Agent Task Result Processing (Success)

    Test TaskResult success handling.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="agent_node",
        task="Test task",
        next=WorkflowNextState(state_id="next"),
        assistant_id="assistant_1",
    )

    node = AgentNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        assistant=mock_assistant,
        user=mock_user,
        execution_id="exec_123",
    )

    task_result = TaskResult(success=True, result="Successfully completed task")

    # Act
    output = node.post_process_output(state_schema, "Test task", task_result)

    # Assert
    assert output == "Successfully completed task"


def test_tc_anc_008_agent_task_result_processing_failure(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks, mock_assistant, mock_workflow_config, mock_user
):
    """
    TC_ANC_008: Agent Task Result Processing (Failure)

    Test TaskResult failure handling with exception.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="agent_node",
        task="Test task",
        next=WorkflowNextState(state_id="next"),
        assistant_id="assistant_1",
    )

    node = AgentNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        assistant=mock_assistant,
        user=mock_user,
        execution_id="exec_123",
    )

    original_exception = ValueError("API error")
    task_result = TaskResult(success=False, result="Task failed", original_exc=original_exception)

    # Act & Assert
    with pytest.raises(TaskException, match="Graph node execution failed"):
        node.post_process_output(state_schema, "Test task", task_result)


@patch("codemie.workflows.nodes.agent_node.VirtualAssistantService")
@patch("codemie.workflows.nodes.agent_node.find_assistant_by_id")
def test_tc_anc_009_agent_cleanup_after_execution(
    mock_find_assistant,
    mock_virtual_assistant_service,
    mock_workflow_execution_service,
    mock_thought_queue,
    mock_callbacks,
    mock_assistant,
    mock_workflow_config,
    mock_user,
):
    """
    TC_ANC_009: Agent Cleanup After Execution

    Test virtual assistant deletion.
    """
    # Arrange
    mock_assistant_config = Mock()
    mock_assistant_config.assistant_id = None  # Virtual assistant (no persistent ID)
    mock_find_assistant.return_value = mock_assistant_config

    state_schema = {
        CONTEXT_STORE_VARIABLE: {},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="agent_node",
        task="Test task",
        next=WorkflowNextState(state_id="next"),
        assistant_id="assistant_1",
    )

    node = AgentNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        assistant=mock_assistant,
        user=mock_user,
        execution_id="exec_123",
    )

    # Act
    node.after_execution(state_schema, TaskResult(success=True, result="Done"))

    # Assert
    # Virtual assistant should be deleted
    mock_virtual_assistant_service.delete_by_execution_id.assert_called_once_with("exec_123")


def test_tc_anc_010_agent_context_generation(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks, mock_assistant, mock_workflow_config, mock_user
):
    """
    TC_ANC_010: Agent Context Generation

    Verify execution context has all required components.
    """
    # Arrange
    state_schema = {
        CONTEXT_STORE_VARIABLE: {"key1": "value1"},
        MESSAGES_VARIABLE: [],
    }

    workflow_state = WorkflowState(
        id="agent_node",
        task="Test task",
        next=WorkflowNextState(state_id="next"),
        assistant_id="assistant_1",
    )

    node = AgentNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
        workflow_config=mock_workflow_config,
        assistant=mock_assistant,
        user=mock_user,
        execution_id="exec_123",
        file_names=["doc.pdf"],
        extra_param="extra_value",
    )

    # Act
    context = node.generate_execution_context(state_schema)

    # Assert
    # Context should include assistant
    assert "assistant" in context
    assert context["assistant"] == mock_assistant

    # Context should include kwargs
    assert "extra_param" in context
    assert context["extra_param"] == "extra_value"
