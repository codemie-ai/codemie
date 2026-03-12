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

"""Tests for StateProcessorNode workflow node."""

import contextlib
import pytest
from unittest.mock import Mock, patch
from langchain_core.messages import HumanMessage, AIMessage

from codemie.core.workflow_models import (
    WorkflowState,
    CustomWorkflowNode,
    WorkflowExecutionStatusEnum,
    WorkflowExecutionStateWithThougths,
)
from codemie.service.workflow_execution import WorkflowExecutionService
from codemie.workflows.callbacks.base_callback import BaseCallback
from codemie.workflows.models import AgentMessages
from codemie.workflows.nodes.state_processor_node import StateProcessorNode


@pytest.fixture
def mock_workflow_execution_service():
    """Create a mock WorkflowExecutionService."""
    service = Mock(spec=WorkflowExecutionService)
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
    state.task = "Summarize the analysis results"
    return state


@pytest.fixture
def custom_node():
    """Create a CustomWorkflowNode with configuration."""
    node = Mock(spec=CustomWorkflowNode)
    node.id = "test-state-processor"
    node.model = "gpt-4o-mini"
    node.config = {
        "state_id": "branch_comparator",
        "output_template": "# Summary\n{% for item in items %}\n## {{ item.file_path }}\n{{ item.conclusion }}\n{% endfor %}",
    }
    return node


@pytest.fixture
def sample_states():
    """Create sample workflow execution states."""
    state1 = Mock(spec=WorkflowExecutionStateWithThougths)
    state1.task = "Analyze file1.py"
    state1.output = '{"file_path": "file1.py", "conclusion": "No issues found"}'
    state1.status = WorkflowExecutionStatusEnum.SUCCEEDED

    state2 = Mock(spec=WorkflowExecutionStateWithThougths)
    state2.task = "Analyze file2.py"
    state2.output = '{"file_path": "file2.py", "conclusion": "Minor optimization needed"}'
    state2.status = WorkflowExecutionStatusEnum.SUCCEEDED

    return [state1, state2]


class TestStateProcessorNodeInitialization:
    """Test StateProcessorNode initialization."""

    def test_init(self, mock_callbacks, mock_workflow_execution_service, mock_thought_queue, mock_workflow_state):
        """Test successful initialization."""
        node = StateProcessorNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
        )

        assert node.workflow_state == mock_workflow_state
        assert node.request_id == "test-execution-id"


class TestStateProcessorNodeExecution:
    """Test StateProcessorNode execution."""

    @patch('codemie.workflows.nodes.state_processor_node.TemplateRenderer')
    @patch('codemie.workflows.nodes.state_processor_node.extract_text_from_llm_output')
    @patch('codemie.workflows.nodes.state_processor_node.extract_json_content')
    @patch('codemie.workflows.nodes.state_processor_node.get_llm_by_credentials')
    @patch('codemie.workflows.nodes.state_processor_node.WorkflowExecutionStatesIndexService')
    def test_execute_basic(
        self,
        mock_states_service,
        mock_get_llm,
        mock_extract_json,
        mock_extract_text,
        mock_template_renderer,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        custom_node,
        sample_states,
    ):
        """Test execute method with basic configuration."""
        node = StateProcessorNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
        )

        # Setup mocks
        mock_states_service.run.return_value = {"data": sample_states}
        mock_llm = Mock()
        mock_llm.invoke.return_value = Mock(content="LLM response")
        mock_get_llm.return_value = mock_llm
        mock_extract_text.return_value = "Extracted text"
        mock_extract_json.side_effect = [
            {"file_path": "file1.py", "conclusion": "No issues found"},
            {"file_path": "file2.py", "conclusion": "Minor optimization needed"},
        ]
        mock_template_renderer.render_template_batch.return_value = "Rendered output"

        state_schema = Mock(spec=AgentMessages)
        execution_context = {"custom_node": custom_node}

        result = node.execute(state_schema, execution_context)

        assert result == "Rendered output"
        mock_states_service.run.assert_called_once()
        assert mock_llm.invoke.call_count == 2
        mock_template_renderer.render_template_batch.assert_called_once()

    @patch('codemie.workflows.nodes.state_processor_node.WorkflowExecutionStatesIndexService')
    def test_execute_with_custom_workflow_execution_id(
        self,
        mock_states_service,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        sample_states,
    ):
        """Test execute method with custom workflow_execution_id in config."""
        from tenacity import retry, stop_after_attempt, retry_if_exception_type, wait_none

        node = StateProcessorNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
        )

        # Replace retry decorator with no-wait version for faster testing
        original_fetch = node._fetch_states.__wrapped__
        node._fetch_states = retry(
            stop=stop_after_attempt(5), wait=wait_none(), retry=retry_if_exception_type(ValueError), reraise=True
        )(original_fetch.__get__(node, StateProcessorNode))

        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.model = "gpt-4o-mini"
        custom_node.config = {
            "workflow_execution_id": "custom-execution-id",
            "output_template": "Template",
        }

        mock_states_service.run.return_value = {"data": []}

        state_schema = Mock(spec=AgentMessages)
        execution_context = {"custom_node": custom_node}

        with patch('codemie.workflows.nodes.state_processor_node.get_llm_by_credentials'):
            with patch('codemie.workflows.nodes.state_processor_node.TemplateRenderer'):
                with contextlib.suppress(ValueError):
                    # Expected to raise ValueError because no completed states
                    node.execute(state_schema, execution_context)

        # Verify custom execution ID was used
        call_args = mock_states_service.run.call_args
        assert call_args[1]["execution_id"] == "custom-execution-id"

    @patch('codemie.workflows.nodes.state_processor_node.TemplateRenderer')
    @patch('codemie.workflows.nodes.state_processor_node.extract_text_from_llm_output')
    @patch('codemie.workflows.nodes.state_processor_node.extract_json_content')
    @patch('codemie.workflows.nodes.state_processor_node.get_llm_by_credentials')
    @patch('codemie.workflows.nodes.state_processor_node.WorkflowExecutionStatesIndexService')
    def test_execute_with_custom_status_filter(
        self,
        mock_states_service,
        mock_get_llm,
        mock_extract_json,
        mock_extract_text,
        mock_template_renderer,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        sample_states,
    ):
        """Test execute method with custom states_status_filter."""
        node = StateProcessorNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
        )

        custom_node = Mock(spec=CustomWorkflowNode)
        custom_node.id = "test-node"
        custom_node.model = "gpt-4o-mini"
        custom_node.config = {
            "output_template": "Template",
            "states_status_filter": [WorkflowExecutionStatusEnum.SUCCEEDED.value],
        }

        mock_states_service.run.return_value = {"data": sample_states}
        mock_llm = Mock()
        mock_llm.invoke.return_value = Mock(content="LLM response")
        mock_get_llm.return_value = mock_llm
        mock_extract_text.return_value = "Extracted text"
        mock_extract_json.side_effect = [
            {"file_path": "file1.py", "conclusion": "No issues found"},
            {"file_path": "file2.py", "conclusion": "Minor optimization needed"},
        ]
        mock_template_renderer.render_template_batch.return_value = "Rendered output"

        state_schema = Mock(spec=AgentMessages)
        execution_context = {"custom_node": custom_node}

        node.execute(state_schema, execution_context)

        call_args = mock_states_service.run.call_args
        assert call_args[1]["states_status_filter"] == [WorkflowExecutionStatusEnum.SUCCEEDED.value]

    @patch('codemie.workflows.nodes.state_processor_node.TemplateRenderer')
    @patch('codemie.workflows.nodes.state_processor_node.extract_text_from_llm_output')
    @patch('codemie.workflows.nodes.state_processor_node.extract_json_content')
    @patch('codemie.workflows.nodes.state_processor_node.get_llm_by_credentials')
    @patch('codemie.workflows.nodes.state_processor_node.WorkflowExecutionStatesIndexService')
    def test_execute_skips_states_without_output(
        self,
        mock_states_service,
        mock_get_llm,
        mock_extract_json,
        mock_extract_text,
        mock_template_renderer,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        custom_node,
    ):
        """Test execute skips states that have no output."""
        node = StateProcessorNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
        )

        state_with_output = Mock(spec=WorkflowExecutionStateWithThougths)
        state_with_output.task = "Task 1"
        state_with_output.output = '{"result": "success"}'
        state_with_output.status = WorkflowExecutionStatusEnum.SUCCEEDED

        state_without_output = Mock(spec=WorkflowExecutionStateWithThougths)
        state_without_output.task = "Task 2"
        state_without_output.output = None
        state_without_output.status = WorkflowExecutionStatusEnum.SUCCEEDED

        mock_states_service.run.return_value = {"data": [state_with_output, state_without_output]}
        mock_llm = Mock()
        mock_llm.invoke.return_value = Mock(content="LLM response")
        mock_get_llm.return_value = mock_llm
        mock_extract_text.return_value = "Extracted text"
        mock_extract_json.return_value = {"result": "success"}
        mock_template_renderer.render_template_batch.return_value = "Rendered output"

        state_schema = Mock(spec=AgentMessages)
        execution_context = {"custom_node": custom_node}

        node.execute(state_schema, execution_context)

        # LLM should only be invoked once for state with output
        assert mock_llm.invoke.call_count == 1

    @patch('codemie.workflows.nodes.state_processor_node.TemplateRenderer')
    @patch('codemie.workflows.nodes.state_processor_node.extract_text_from_llm_output')
    @patch('codemie.workflows.nodes.state_processor_node.extract_json_content')
    @patch('codemie.workflows.nodes.state_processor_node.get_llm_by_credentials')
    @patch('codemie.workflows.nodes.state_processor_node.WorkflowExecutionStatesIndexService')
    @patch('codemie.workflows.nodes.state_processor_node.logger')
    def test_execute_skips_states_with_invalid_json(
        self,
        mock_logger,
        mock_states_service,
        mock_get_llm,
        mock_extract_json,
        mock_extract_text,
        mock_template_renderer,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        custom_node,
        sample_states,
    ):
        """Test execute skips states where JSON extraction returns None."""
        node = StateProcessorNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
        )

        mock_states_service.run.return_value = {"data": sample_states}
        mock_llm = Mock()
        mock_llm.invoke.return_value = Mock(content="LLM response")
        mock_get_llm.return_value = mock_llm
        mock_extract_text.return_value = "Extracted text"
        # First call returns valid JSON, second returns None
        mock_extract_json.side_effect = [{"result": "success"}, None]
        mock_template_renderer.render_template_batch.return_value = "Rendered output"

        state_schema = Mock(spec=AgentMessages)
        execution_context = {"custom_node": custom_node}

        node.execute(state_schema, execution_context)

        # Template renderer should only receive one valid result
        call_args = mock_template_renderer.render_template_batch.call_args
        assert len(call_args[1]["json_str_list"]) == 1

    @patch('codemie.workflows.nodes.state_processor_node.TemplateRenderer')
    @patch('codemie.workflows.nodes.state_processor_node.get_llm_by_credentials')
    @patch('codemie.workflows.nodes.state_processor_node.WorkflowExecutionStatesIndexService')
    def test_execute_constructs_llm_messages_correctly(
        self,
        mock_states_service,
        mock_get_llm,
        mock_template_renderer,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        custom_node,
        sample_states,
    ):
        """Test execute constructs proper LLM messages with task and output."""
        node = StateProcessorNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
        )

        mock_states_service.run.return_value = {"data": sample_states[:1]}
        mock_llm = Mock()
        mock_llm.invoke.return_value = Mock(content='{"result": "success"}')
        mock_get_llm.return_value = mock_llm
        mock_template_renderer.render_template_batch.return_value = "Rendered output"

        with patch('codemie.workflows.nodes.state_processor_node.extract_text_from_llm_output') as mock_extract_text:
            with patch('codemie.workflows.nodes.state_processor_node.extract_json_content') as mock_extract_json:
                mock_extract_text.return_value = '{"result": "success"}'
                mock_extract_json.return_value = {"result": "success"}

                state_schema = Mock(spec=AgentMessages)
                execution_context = {"custom_node": custom_node}

                node.execute(state_schema, execution_context)

                # Verify LLM was invoked with correct message structure
                call_args = mock_llm.invoke.call_args[0][0]
                assert len(call_args) == 2
                assert isinstance(call_args[0], AIMessage)
                assert isinstance(call_args[1], HumanMessage)
                assert "TASK:" in str(call_args[0].content)
                assert "OUTPUT:" in str(call_args[0].content)


class TestStateProcessorNodeHelperMethods:
    """Test StateProcessorNode helper methods."""

    def test_get_task(self, mock_callbacks, mock_workflow_execution_service, mock_thought_queue, mock_workflow_state):
        """Test get_task returns workflow state task."""
        mock_workflow_state.task = "Custom task from workflow state"

        node = StateProcessorNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
        )

        state_schema = Mock(spec=AgentMessages)
        task = node.get_task(state_schema)

        assert task == "Custom task from workflow state"

    def test_post_process_output(
        self, mock_callbacks, mock_workflow_execution_service, mock_thought_queue, mock_workflow_state
    ):
        """Test post_process_output returns output unchanged."""
        node = StateProcessorNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
        )

        output = "Test output content"
        result = node.post_process_output(AgentMessages, "task", output)

        assert result == output


class TestStateProcessorNodeFetchStates:
    """Test StateProcessorNode _fetch_states method."""

    @patch('codemie.workflows.nodes.state_processor_node.WorkflowExecutionStatesIndexService')
    def test_fetch_states_success(
        self,
        mock_states_service,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        sample_states,
    ):
        """Test _fetch_states with successful states."""
        node = StateProcessorNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
        )

        mock_states_service.run.return_value = {"data": sample_states}

        result = node._fetch_states(
            execution_id="test-execution-id",
            state_name="test-state",
            states_status_filter=[WorkflowExecutionStatusEnum.SUCCEEDED.value],
        )

        assert len(result) == 2
        assert result == sample_states
        mock_states_service.run.assert_called_once_with(
            execution_id="test-execution-id",
            per_page=10000,
            include_thoughts=False,
            state_name_prefix="test-state",
            states_status_filter=[WorkflowExecutionStatusEnum.SUCCEEDED.value],
        )

    @patch('codemie.workflows.nodes.state_processor_node.WorkflowExecutionStatesIndexService')
    def test_fetch_states_no_states_raises_error(
        self,
        mock_states_service,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test _fetch_states raises ValueError when no states found."""
        from tenacity import retry, stop_after_attempt, retry_if_exception_type, wait_none

        node = StateProcessorNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
        )

        # Replace retry decorator with no-wait version for faster testing
        original_fetch = node._fetch_states.__wrapped__
        node._fetch_states = retry(
            stop=stop_after_attempt(5), wait=wait_none(), retry=retry_if_exception_type(ValueError), reraise=True
        )(original_fetch.__get__(node, StateProcessorNode))

        mock_states_service.run.return_value = {"data": []}

        with pytest.raises(ValueError, match="No Completed States, retrying..."):
            node._fetch_states(
                execution_id="test-execution-id",
                state_name="test-state",
                states_status_filter=[WorkflowExecutionStatusEnum.SUCCEEDED.value],
            )

    @patch('codemie.workflows.nodes.state_processor_node.WorkflowExecutionStatesIndexService')
    def test_fetch_states_with_not_started_status_raises_error(
        self,
        mock_states_service,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test _fetch_states raises ValueError when states have NOT_STARTED status."""
        from tenacity import retry, stop_after_attempt, retry_if_exception_type, wait_none

        node = StateProcessorNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
        )

        # Replace retry decorator with no-wait version for faster testing
        original_fetch = node._fetch_states.__wrapped__
        node._fetch_states = retry(
            stop=stop_after_attempt(5), wait=wait_none(), retry=retry_if_exception_type(ValueError), reraise=True
        )(original_fetch.__get__(node, StateProcessorNode))

        incomplete_state = Mock(spec=WorkflowExecutionStateWithThougths)
        incomplete_state.status = WorkflowExecutionStatusEnum.NOT_STARTED
        incomplete_state.output = "Some output"

        mock_states_service.run.return_value = {"data": [incomplete_state]}

        with pytest.raises(ValueError, match="No Completed States, retrying..."):
            node._fetch_states(
                execution_id="test-execution-id",
                state_name="test-state",
                states_status_filter=[WorkflowExecutionStatusEnum.NOT_STARTED.value],
            )

    @patch('codemie.workflows.nodes.state_processor_node.WorkflowExecutionStatesIndexService')
    def test_fetch_states_with_in_progress_status_raises_error(
        self,
        mock_states_service,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test _fetch_states raises ValueError when states have IN_PROGRESS status."""
        from tenacity import retry, stop_after_attempt, retry_if_exception_type, wait_none

        node = StateProcessorNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
        )

        # Replace retry decorator with no-wait version for faster testing
        original_fetch = node._fetch_states.__wrapped__
        node._fetch_states = retry(
            stop=stop_after_attempt(5), wait=wait_none(), retry=retry_if_exception_type(ValueError), reraise=True
        )(original_fetch.__get__(node, StateProcessorNode))

        incomplete_state = Mock(spec=WorkflowExecutionStateWithThougths)
        incomplete_state.status = WorkflowExecutionStatusEnum.IN_PROGRESS
        incomplete_state.output = "Some output"

        mock_states_service.run.return_value = {"data": [incomplete_state]}

        with pytest.raises(ValueError, match="No Completed States, retrying..."):
            node._fetch_states(
                execution_id="test-execution-id",
                state_name="test-state",
                states_status_filter=[WorkflowExecutionStatusEnum.IN_PROGRESS.value],
            )

    @patch('codemie.workflows.nodes.state_processor_node.WorkflowExecutionStatesIndexService')
    @patch('codemie.workflows.nodes.state_processor_node.logger')
    def test_fetch_states_logs_state_count(
        self,
        mock_logger,
        mock_states_service,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
        sample_states,
    ):
        """Test _fetch_states logs the count of retrieved states."""
        node = StateProcessorNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
        )

        mock_states_service.run.return_value = {"data": sample_states}

        node._fetch_states(
            execution_id="test-execution-id",
            state_name="test-state",
            states_status_filter=[WorkflowExecutionStatusEnum.SUCCEEDED.value],
        )

        mock_logger.debug.assert_called()
        log_message = str(mock_logger.debug.call_args)
        assert "States count retrieved: 2" in log_message

    @patch('codemie.workflows.nodes.state_processor_node.WorkflowExecutionStatesIndexService')
    def test_fetch_states_retry_mechanism(
        self,
        mock_states_service,
        mock_callbacks,
        mock_workflow_execution_service,
        mock_thought_queue,
        mock_workflow_state,
    ):
        """Test _fetch_states retry mechanism with tenacity."""
        from tenacity import retry, stop_after_attempt, retry_if_exception_type, wait_none

        node = StateProcessorNode(
            callbacks=mock_callbacks,
            workflow_execution_service=mock_workflow_execution_service,
            thought_queue=mock_thought_queue,
            workflow_state=mock_workflow_state,
        )

        # Replace the retry decorator with one that has no wait time
        original_fetch = node._fetch_states.__wrapped__  # Get the unwrapped function

        # Apply a new retry decorator with no wait time
        node._fetch_states = retry(
            stop=stop_after_attempt(5),
            wait=wait_none(),  # No wait between retries
            retry=retry_if_exception_type(ValueError),
            reraise=True,
        )(original_fetch.__get__(node, StateProcessorNode))

        # First 2 calls return empty, third call returns states
        mock_states_service.run.side_effect = [
            {"data": []},
            {"data": []},
            {"data": [Mock(status=WorkflowExecutionStatusEnum.SUCCEEDED, output="output")]},
        ]

        # The method should retry and eventually succeed
        result = node._fetch_states(
            execution_id="test-execution-id",
            state_name="test-state",
            states_status_filter=[WorkflowExecutionStatusEnum.SUCCEEDED.value],
        )

        assert len(result) == 1
        assert mock_states_service.run.call_count == 3
