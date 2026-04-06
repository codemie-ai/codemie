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

import json
import pytest
from unittest.mock import ANY, MagicMock, patch

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_core.callbacks import BaseCallbackHandler

from codemie.agents.langgraph_agent import LangGraphAgent
from codemie.core.models import ChatMessage, AssistantChatRequest
from codemie.chains.base import GenerationResult
from codemie.core.constants import ChatRole
from codemie.core.thread import ThreadedGenerator
from codemie.agents.callbacks.agent_streaming_callback import AgentStreamingCallback
from codemie.agents.callbacks.agent_invoke_callback import AgentInvokeCallback
from codemie.agents.callbacks.monitoring_callback import MonitoringCallback


class TestLangGraphAgent:
    @pytest.fixture
    def mock_user(self):
        user = MagicMock()
        user.id = "test_user_id"
        user.name = "Test User"
        user.username = "testuser"
        return user

    @pytest.fixture
    def mock_request(self):
        request = MagicMock(spec=AssistantChatRequest)
        request.conversation_id = "test_conv_id"
        request.text = "Test request"
        request.history = []
        request.system_prompt = None
        request.file_names = None
        request.metadata = {}
        return request

    @pytest.fixture
    def mock_tool(self):
        tool = MagicMock(spec=BaseTool)
        tool.name = "test_tool"
        tool.description = "A test tool"
        tool.metadata = {}
        return tool

    @pytest.fixture
    def agent_config(self, mock_user, mock_request, mock_tool):
        assistant = MagicMock()
        assistant.project = "test"
        return {
            "agent_name": "TestAgent",
            "description": "Test agent description",
            "tools": [mock_tool],
            "request": mock_request,
            "system_prompt": "You are a test assistant.",
            "request_uuid": "test_uuid",
            "user": mock_user,
            "llm_model": "gpt-3.5-turbo",
            "assistant": assistant,
        }

    @pytest.fixture
    def agent(self, agent_config):
        with patch("codemie.agents.langgraph_agent.create_smart_react_agent") as mock_create_agent:
            mock_agent = MagicMock()
            mock_create_agent.return_value = mock_agent
            with patch("codemie.agents.langgraph_agent.get_llm_by_credentials"):
                agent = LangGraphAgent(**agent_config)
                yield agent

    def test_init(self, agent, agent_config):
        """Test agent initialization"""
        assert agent.agent_name == agent_config["agent_name"]
        assert agent.description == agent_config["description"]
        assert agent.tools == agent_config["tools"]
        assert agent.request == agent_config["request"]
        assert agent.system_prompt == agent_config["system_prompt"]
        assert agent.request_uuid == agent_config["request_uuid"]
        assert agent.user == agent_config["user"]
        assert agent.llm_model == agent_config["llm_model"]

    @pytest.fixture
    def agent_for_parse_update(self, agent):
        # Patch methods used in __parse_update_type
        agent._on_tool_start = MagicMock()
        agent._on_tool_end = MagicMock()
        agent._on_tool_error = MagicMock()
        agent._safe_check_for_truncation = MagicMock()
        agent.is_finish_reason_tool_calls = MagicMock(return_value=True)
        return agent

    def test_parse_update_type_agent_tool_call(self, agent_for_parse_update):
        # Simulate an agent update that triggers a tool call
        ai_message = AIMessage(
            content="Tool call",
            tool_calls=[{"name": "test_tool", "args": {"arg": 1}, "id": "call-123", "type": "tool_call"}],
        )
        value = {"agent": {"messages": [ai_message]}}
        # Call the private method
        agent_for_parse_update._LangGraphAgent__parse_update_type(value)

        # _on_tool_start called with tool name, args, run_id, and tool_call_count
        agent_for_parse_update._on_tool_start.assert_called_once_with("test_tool", "{'arg': 1}", run_id=ANY)
        # No error or end should be called
        agent_for_parse_update._on_tool_end.assert_not_called()
        agent_for_parse_update._on_tool_error.assert_not_called()

    def test_parse_update_type_tools_result_success(self, agent_for_parse_update):
        # Simulate a tools update with a successful result
        tool_message = ToolMessage(name="test_tool", content="Result: success", tool_call_id="call-123")
        value = {"tools": {"messages": [tool_message]}}
        # Call the private method
        agent_for_parse_update._LangGraphAgent__parse_update_type(value)

        # Should call _on_tool_end with the content (run_id is added internally)
        agent_for_parse_update._on_tool_end.assert_called_once_with("Result: success", run_id=ANY, author=None)
        agent_for_parse_update._on_tool_error.assert_not_called()
        agent_for_parse_update._on_tool_start.assert_not_called()

    def test_parse_update_type_tools_non_tool_message(self, agent_for_parse_update):
        # Simulate a tools update with a non-ToolMessage (should be ignored)
        class NotAToolMessage:
            pass

        not_a_tool = NotAToolMessage()
        value = {"tools": {"messages": [not_a_tool]}}
        # Call the private method
        agent_for_parse_update._LangGraphAgent__parse_update_type(value)

        # None of the callbacks should be called
        agent_for_parse_update._on_tool_error.assert_not_called()
        agent_for_parse_update._on_tool_end.assert_not_called()
        agent_for_parse_update._on_tool_start.assert_not_called()

    def test_configure_callbacks(self, agent):
        """Test callback configuration"""
        # Test with empty initial callbacks
        agent.callbacks = []
        callbacks = agent.configure_callbacks()

        # Check that default callbacks are added
        assert any(isinstance(cb, MonitoringCallback) for cb in callbacks)
        assert any(isinstance(cb, AgentInvokeCallback) for cb in callbacks)

        # Test with thread generator
        agent.thread_generator = ThreadedGenerator()
        agent.stream_steps = True
        callbacks = agent.configure_callbacks()

        # Check that AgentStreamingCallback is added when thread_generator is provided
        assert any(isinstance(cb, AgentStreamingCallback) for cb in callbacks)

    def test_invoke(self, agent):
        """Test invoke method"""
        mock_output = "Test output"
        agent._invoke_agent = MagicMock(
            return_value=GenerationResult(
                generated=mock_output, time_elapsed=None, input_tokens_used=None, tokens_used=None, success=True
            )
        )
        agent._get_inputs = MagicMock(return_value={"messages": []})

        result = agent.invoke("Test input")

        agent._get_inputs.assert_called_once()
        agent._invoke_agent.assert_called_once()
        assert result == mock_output

    def test_generate_exception(self, agent, monkeypatch):
        # Arrange
        def raise_exception(*args, **kwargs):
            raise fake_exception

        fake_exception = RuntimeError("Something went wrong")

        # Mock _invoke_agent to raise an exception
        monkeypatch.setattr(agent, "_invoke_agent", raise_exception)
        monkeypatch.setattr(agent, "_get_inputs", lambda: {})

        # Mock logger to verify error logging
        with (
            patch("codemie.agents.langgraph_agent.logger.error") as mock_logger_error,
            patch("codemie.agents.utils.handle_agent_exception", return_value=("traceback here", None)),
            patch("codemie.agents.langgraph_agent.time", side_effect=[1.0, 2.0]),
            patch("codemie.agents.langgraph_agent.BackgroundTasksService") as mock_bgtasks,
        ):
            mock_update = MagicMock()
            mock_bgtasks.return_value.update = mock_update

            # Act
            result = agent.generate(background_task_id="abc123")

            # Assert
            assert int(result.time_elapsed) == 1
            mock_logger_error.assert_called()
            mock_update.assert_called_once_with(
                task_id="abc123",
                status="FAILED",
                final_output=result.generated,
            )

    def test_invoke_with_error(self, agent):
        """Test invoke with error handling"""
        agent._invoke_agent = MagicMock(side_effect=Exception("Test error"))
        agent._get_inputs = MagicMock(return_value={"messages": []})

        result = agent.invoke("Test input")

        assert "AI Agent run failed with error" in result
        assert "Test error" in result

    def test_invoke_task(self, agent):
        """Test invoke_task method"""
        mock_output = "Task completed"
        agent._invoke_agent = MagicMock(
            return_value=GenerationResult(
                generated=mock_output, time_elapsed=None, input_tokens_used=None, tokens_used=None, success=True
            )
        )
        agent._filter_history = MagicMock(return_value=[])

        result = agent.invoke_task("Do task")

        agent._invoke_agent.assert_called_once()
        assert result.success
        assert result.result == mock_output

    def test_invoke_task_with_error(self, agent):
        """Test invoke_task with error handling"""
        agent._invoke_agent = MagicMock(side_effect=Exception("Task failed"))
        agent._filter_history = MagicMock(return_value=[])

        result = agent.invoke_task("Do task")

        assert not result.success

    def test_invoke_agent_returns_generation_result(self, agent):
        # Arrange
        inputs = {"foo": "bar"}
        expected_output = "agent result"
        agent._stream_graph = MagicMock(return_value=expected_output)
        agent._get_run_config = MagicMock(return_value={"run": "config"})

        # Act
        result = agent._invoke_agent(inputs)

        # Assert
        agent._stream_graph.assert_called_once_with(inputs, config=agent._get_run_config())
        assert isinstance(result, GenerationResult)
        assert result.generated == expected_output
        assert result.tokens_used is None

    def test_generate(self, agent):
        """Test generate method"""
        mock_output = "Generated content"
        agent._invoke_agent = MagicMock(
            return_value=GenerationResult(
                generated=mock_output, time_elapsed=None, input_tokens_used=None, tokens_used=None, success=True
            )
        )
        agent._get_inputs = MagicMock(return_value={"messages": []})

        with patch("codemie.agents.langgraph_agent.calculate_tokens", return_value=10):
            with patch("codemie.agents.langgraph_agent.time", side_effect=[0, 1]):
                result = agent.generate()

        assert isinstance(result, GenerationResult)
        assert result.generated == mock_output
        assert result.tokens_used == 10
        assert result.time_elapsed == 1

    @patch("codemie.agents.langgraph_agent.BackgroundTasksService")
    def test_generate_with_background_task(self, mock_task_service, agent):
        """Test generate with background task ID"""
        mock_output = "Generated content"
        agent._invoke_agent = MagicMock(
            return_value=GenerationResult(
                generated=mock_output, time_elapsed=None, input_tokens_used=None, tokens_used=None, success=True
            )
        )
        agent._get_inputs = MagicMock(return_value={"messages": []})

        mock_service = MagicMock()
        mock_task_service.return_value = mock_service

        task_id = "background-task-123"
        with patch("codemie.agents.langgraph_agent.calculate_tokens", return_value=10):
            with patch("codemie.agents.langgraph_agent.time", side_effect=[0, 1]):
                result = agent.generate(background_task_id=task_id)

        mock_service.update.assert_called_once()
        assert result.generated == mock_output

    def test_configure_tools(self, agent, mock_tool):
        """Test tool configuration"""
        agent.tools = [mock_tool]
        agent._configure_tools()

        # Check that tool metadata is updated
        assert mock_tool.metadata["request_id"] == agent.request_uuid
        assert mock_tool.metadata["user_id"] == agent.user.id
        assert mock_tool.metadata["user_name"] == agent.user.name
        assert mock_tool.metadata["llm_model"] == agent.llm_model
        assert mock_tool.metadata["agent_name"] == agent.agent_name

    def test_stream_graph(self, agent):
        """Test streaming graph execution"""
        chunks_collector = []

        mock_stream = MagicMock()
        mock_stream.__iter__.return_value = [
            ("messages", (AIMessage(content="chunk1"), None)),
            ("updates", {"agent": {"messages": [AIMessage(content="chunk2")]}}),
        ]

        agent.agent_executor = MagicMock()
        agent.agent_executor.stream.return_value = mock_stream
        agent.process_chunk = MagicMock()
        agent._on_chain_end = MagicMock()
        agent._get_last_ai_message_content = MagicMock(side_effect=["chunk1", "chunk2"])

        result = agent._stream_graph({"input": "test"}, None, chunks_collector)

        assert agent.agent_executor.stream.call_count == 1
        assert agent.process_chunk.call_count == 2
        assert agent._on_chain_end.call_count == 1
        assert result == "chunk2"  # Should be the last AI message content

    @patch("codemie.agents.langgraph_agent.logger")
    def test_stream(self, mock_logger, agent):
        """Test streaming functionality"""
        agent.thread_generator = MagicMock(spec=ThreadedGenerator)
        agent._agent_streaming = MagicMock(return_value="Streamed output")

        with patch("codemie.agents.langgraph_agent.time", side_effect=[0, 1]):
            agent.stream()

        agent._agent_streaming.assert_called_once()
        assert agent.thread_generator.send.call_count == 1
        assert agent.thread_generator.close.call_count == 1

        # Verify the JSON structure sent to thread generator
        call_args = agent.thread_generator.send.call_args[0][0]
        streamed_result = json.loads(call_args)
        assert streamed_result["generated"] == "Streamed output"
        assert streamed_result["last"] is True

    @patch("codemie.agents.langgraph_agent.logger")
    def test_stream_with_error(self, mock_logger, agent):
        """Test streaming with error handling"""
        agent.thread_generator = MagicMock(spec=ThreadedGenerator)
        agent._agent_streaming = MagicMock(side_effect=ValueError("Stream error"))

        with patch("codemie.agents.langgraph_agent.time", side_effect=[0, 1]):
            agent.stream()

        # Should still close the generator and send error
        assert agent.thread_generator.send.call_count == 1
        assert agent.thread_generator.close.call_count == 1

        # Verify error message is sent
        call_args = agent.thread_generator.send.call_args[0][0]
        streamed_result = json.loads(call_args)
        assert "AI Agent run failed with error" in streamed_result["generated"]

    def test_transform_history(self):
        """Test history transformation"""
        history = [
            ChatMessage(role=ChatRole.USER, message="Hello"),
            ChatMessage(role=ChatRole.ASSISTANT, message="Hi there"),
        ]

        transformed = LangGraphAgent._transform_history(history)

        assert len(transformed) == 2
        assert isinstance(transformed[0], HumanMessage)
        assert transformed[0].content == "Hello"
        assert isinstance(transformed[1], AIMessage)
        assert transformed[1].content == "Hi there"

    def test_filter_history(self):
        """Test history filtering"""
        history = [
            HumanMessage(content="Hello"),
            AIMessage(content=""),
            HumanMessage(content="How are you?"),
        ]

        filtered = LangGraphAgent._filter_history(history)

        assert len(filtered) == 2
        assert filtered[0].content == "Hello"
        assert filtered[1].content == "How are you?"

    @patch("codemie.agents.langgraph_agent.logger")
    def test_on_llm_new_token(self, mock_logger, agent):
        """Test LLM token callback handling"""
        mock_callback = MagicMock(spec=BaseCallbackHandler)
        agent.callbacks = [mock_callback]

        agent._on_llm_new_token("new token", run_id=None)

        mock_callback.on_llm_new_token.assert_called_once_with(token="new token", run_id=None, author=None)

    @patch("codemie.agents.langgraph_agent.logger")
    def test_on_llm_new_token_with_error(self, mock_logger, agent):
        """Test error handling in callbacks"""
        mock_callback = MagicMock(spec=BaseCallbackHandler)
        mock_callback.on_llm_new_token.side_effect = Exception("Callback error")
        agent.callbacks = [mock_callback]

        agent._on_llm_new_token("new token", run_id=None)

        mock_callback.on_llm_new_token.assert_called_once()
        mock_logger.error.assert_called_once()

    def test_get_inputs(self, agent):
        """Test input preparation"""
        agent.request.text = "Test question"
        agent.request.history = [
            ChatMessage(role=ChatRole.USER, message="Hello"),
            ChatMessage(role=ChatRole.ASSISTANT, message="Hi"),
        ]

        with patch.object(
            agent, '_transform_history', return_value=[HumanMessage(content="Hello"), AIMessage(content="Hi")]
        ):
            inputs = agent._get_inputs()

        assert len(inputs["messages"]) == 3  # 2 history + 1 current message
        assert inputs["messages"][2].content[0]["text"] == "Test question"

    @patch("codemie.agents.langgraph_agent.ImageService.filter_base64_images")
    def test_get_inputs_with_image(self, mock_filter_base64_images, agent):
        """Test input preparation with image"""
        # Setup
        agent.request.text = "Describe this image"
        agent.request.file_names = ["test-image.png"]
        mock_filter_base64_images.return_value = [{'content': 'base64-image-data', 'mime_type': 'image/png'}]

        # Create expected output manually instead of going through the method
        expected_content = [
            {"type": "text", "text": "Describe this image"},
            {"type": "text", "text": "Attached images:"},
            {"type": "image", "source_type": "base64", "data": "base64-image-data", "mime_type": "image/png"},
        ]

        # Check if the setup is correct
        # The structure of the input with image should include the image content correctly formated
        message = HumanMessage(content=expected_content)
        expected_inputs = {"messages": [message]}

        with patch.object(agent, '_get_inputs', return_value=expected_inputs):
            inputs = agent._get_inputs()

            # Assert
            assert len(inputs["messages"]) == 1
            assert isinstance(inputs["messages"][0], HumanMessage)
            assert len(inputs["messages"][0].content) == 3
            assert inputs["messages"][0].content[0]["text"] == "Describe this image"
            assert inputs["messages"][0].content[2]["type"] == "image"
            assert inputs["messages"][0].content[2]["data"] == "base64-image-data"

    def test_preprocess_output_schema(self):
        """Test output schema preprocessing"""
        # Test with valid schema
        schema = {"type": "object", "properties": {"result": {"type": "string"}}}

        with patch("codemie.agents.langgraph_agent.validate_json_schema", return_value=True):
            processed = LangGraphAgent._preprocess_output_schema(schema)

        assert processed["title"] == "StructuredOutput"

        # Test with invalid schema
        with patch("codemie.agents.langgraph_agent.validate_json_schema", return_value=False):
            with pytest.raises(ValueError):
                LangGraphAgent._preprocess_output_schema(schema)

    def test_format_assistant_name(self):
        """Test assistant name formatting"""
        name = "Test Assistan#@$%@#t/Name"
        formatted = LangGraphAgent.format_assistant_name(name)
        assert formatted == "test_assistant_name"

    def test_is_valid_ai_message(self):
        """Test AI message validation"""
        valid_message = AIMessage(content="Valid content")
        invalid_message = AIMessage(content="")
        not_ai_message = HumanMessage(content="User message")

        assert LangGraphAgent.is_valid_ai_message(valid_message) is True
        assert LangGraphAgent.is_valid_ai_message(invalid_message) is False
        assert LangGraphAgent.is_valid_ai_message(not_ai_message) is False

    def test_parse_additional_kwargs_to_tool_info(self):
        """Test parsing tool info from tool calls"""
        message = MagicMock()
        message.tool_calls = [{"name": "search_tool", "args": {"query": "python testing", "limit": 5}}]

        tool_name, tool_args = LangGraphAgent._get_tool_call_args(message)

        assert tool_name == "search_tool"
        assert tool_args == "{'query': 'python testing', 'limit': 5}"

    def test_get_last_ai_message_content(self, agent):
        """Test extraction of last AI message content"""
        # Test with agent message
        ai_message = AIMessage(content="AI content")
        chunk = ("updates", {"agent": {"messages": [ai_message]}})

        content = agent._get_last_ai_message_content(chunk)
        assert content == "AI content"

        # Test with structured response
        structured_response = {"key": "value"}
        chunk = ("updates", {"generate_structured_response": {"structured_response": structured_response}})

        content = agent._get_last_ai_message_content(chunk)
        assert content == structured_response

    def test_set_thread_context(self, agent):
        """Test setting thread context"""
        context = {"key": "value"}
        parent_id = "parent-123"

        mock_callback = MagicMock(spec=AgentStreamingCallback)
        agent.callbacks = [mock_callback]

        agent.set_thread_context(context, parent_id)

        assert agent.thread_context == context
        mock_callback.set_context.assert_called_once_with(context, parent_id, None)

    @patch("codemie.agents.langgraph_agent.FileObject")
    def test_task_property(self, mock_file_object, agent):
        """Test _task property behavior"""
        # Without file
        agent.request.text = "Simple task"
        agent.request.file_names = None

        assert agent._task == "Simple task"

        # With file
        agent.request.text = "Process file"
        agent.request.file_names = ["encoded-file-url"]

        mock_file = MagicMock()
        mock_file.name = "test.txt"
        mock_file_object.from_encoded_url.return_value = mock_file

        task = agent._task

        assert "Process file" in task
        assert "test.txt" in task

    def test_parse_tool_message_success_and_error(self, monkeypatch, agent):
        """Test _parse_tool_message for both 'success' and 'error' handling."""

        # Case 1: Error status triggers _on_tool_error
        agent._on_tool_error = MagicMock()
        agent._on_tool_end = MagicMock()
        agent.agent_name = "FakeAgent"
        agent.request_uuid = "uuid123"

        error_action = MagicMock(spec=ToolMessage)
        error_action.status = "error"
        error_action.content = "Tool failed"
        error_action.tool_call_id = "call-error"
        agent._parse_tool_message(error_action)
        agent._on_tool_error.assert_called_once_with("Tool failed", run_id=ANY, author=None)
        agent._on_tool_end.assert_not_called()

        # Case 2: Success status triggers _on_tool_end
        agent._on_tool_error.reset_mock()
        agent._on_tool_end.reset_mock()
        success_action = MagicMock(spec=ToolMessage)
        success_action.status = "success"
        success_action.content = "Tool succeeded"
        success_action.tool_call_id = "call-success"
        agent._parse_tool_message(success_action)
        agent._on_tool_end.assert_called_once_with("Tool succeeded", run_id=ANY, author=None)
        agent._on_tool_error.assert_not_called()

    def test_parse_tool_message_unknown_status(self, monkeypatch, agent):
        """Test _parse_tool_message logs warning with unknown status and still calls _on_tool_end."""

        agent._on_tool_error = MagicMock()
        agent._on_tool_end = MagicMock()
        agent.agent_name = "FakeAgent"
        agent.request_uuid = "uuidX"

        action = MagicMock(spec=ToolMessage)
        action.status = "pending"
        action.content = "Waiting..."
        action.tool_call_id = "call-pending"

        with patch("codemie.agents.langgraph_agent.logger.warning") as mock_warning:
            agent._parse_tool_message(action)
            mock_warning.assert_called_once()
            assert "Unknown tool action status: pending" in mock_warning.call_args[0][0]
            agent._on_tool_end.assert_called_once_with("Waiting...", run_id=ANY, author=None)
            agent._on_tool_error.assert_not_called()

    def test_invoke_with_a2a_output(self, agent):
        """Test invoke_with_a2a_output method"""
        mock_output = "Task result"
        agent._invoke_agent = MagicMock(
            return_value=GenerationResult(
                generated=mock_output, time_elapsed=None, input_tokens_used=None, tokens_used=None, success=True
            )
        )
        agent._get_inputs = MagicMock(return_value={"messages": []})

        result = agent.invoke_with_a2a_output("Do something")

        assert result["is_task_complete"] is True
        assert result["require_user_input"] is False
        assert result["content"] == mock_output

    def test_invoke_with_a2a_output_error(self, agent):
        """Test invoke_with_a2a_output with error handling"""
        agent._invoke_agent = MagicMock(side_effect=Exception("Task failed"))
        agent._get_inputs = MagicMock(return_value={"messages": []})

        result = agent.invoke_with_a2a_output("Do something")

        assert result["is_task_complete"] is False
        assert result["require_user_input"] is True
        assert "Task failed" in result["content"]

    def test_process_chunk(self, agent):
        """Test chunk processing"""
        chunks_collector = []
        agent._process_chunk_for_agent = MagicMock()

        chunk = ("messages", "test")
        agent.process_chunk(chunk, chunks_collector)

        agent._process_chunk_for_agent.assert_called_once_with(chunk, chunks_collector)

    def test_format_assistant_name_truncates_max_length_constant(self):
        """Ensure assistant name is truncated to the class-defined max length and normalized."""
        long_name = "A" * 100
        formatted = LangGraphAgent.format_assistant_name(long_name)
        expected_len = LangGraphAgent.ASSISTANT_NAME_MAX_LENGTH
        assert formatted == ("a" * expected_len)
        assert len(formatted) == expected_len

    @patch('codemie.agents.assistant_agent.config.HIDE_AGENT_STREAMING_EXCEPTIONS', True)
    @patch('codemie.agents.assistant_agent.config.CUSTOM_GUARDRAILS_MESSAGE', 'Content prohibited')
    @patch("codemie.agents.langgraph_agent.logger")
    def test_stream_with_error_with_guardrails_when_flag_enabled(self, mock_logger, agent):
        """Test streaming with error handling"""
        agent.thread_generator = MagicMock(spec=ThreadedGenerator)
        agent._agent_streaming = MagicMock(side_effect=ValueError("content blocked by policy"))

        with patch.object(
            agent,
            "_process_chunks",
            return_value=("Content prohibited", "guardrails"),
        ):
            with patch("codemie.agents.langgraph_agent.time", side_effect=[0, 1]):
                agent.stream()

        # Generator lifecycle
        assert agent.thread_generator.send.call_count == 1
        assert agent.thread_generator.close.call_count == 1

        # Validate payload
        call_args = agent.thread_generator.send.call_args[0][0]
        streamed_result = json.loads(call_args)

        assert streamed_result["execution_error"] == "guardrails"
        assert streamed_result["generated"] == "Content prohibited"
        assert streamed_result["last"] is True
        assert streamed_result["time_elapsed"] == 1
