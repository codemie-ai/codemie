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

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from codemie.agents.langgraph_event_adapter import LangGraphCallbackBridge, LangGraphEventAdapter


class TestLangGraphEventAdapter:
    @pytest.fixture
    def mock_agent(self):
        agent = MagicMock()
        agent.is_finish_reason_tool_calls.return_value = True
        agent._safe_check_for_truncation = MagicMock()
        agent._on_llm_end = MagicMock()
        agent._on_tool_start = MagicMock()
        agent._tool_call_id_to_uuid = MagicMock(return_value=uuid4())
        return agent

    def test_parse_update_type_serializes_list_args_as_json(self, mock_agent):
        """Test that tool args containing lists are serialized as JSON, not Python repr."""
        adapter = LangGraphEventAdapter(mock_agent)

        ai_message = AIMessage(content="")
        ai_message.tool_calls = [
            {
                "name": "excel_tool",
                "args": {"sheet_names": ["Sheet1", "Sheet2"], "query": "analyze data"},
                "id": "call-123",
            }
        ]

        value = {"agent": {"messages": [ai_message]}}
        adapter.parse_update_type(value)

        mock_agent._on_tool_start.assert_called_once()
        call_args = mock_agent._on_tool_start.call_args

        # Verify args are JSON (double quotes), not Python repr (single quotes)
        tool_args_str = call_args.args[1]
        parsed = json.loads(tool_args_str)  # Should not raise JSONDecodeError
        assert parsed == {"sheet_names": ["Sheet1", "Sheet2"], "query": "analyze data"}

    def test_handle_supervisor_tool_calls_serializes_list_args_as_json(self, mock_agent):
        """Test that supervisor tool args containing lists are serialized as JSON."""
        adapter = LangGraphEventAdapter(mock_agent)
        mock_agent._check_is_handoff_tool = MagicMock(return_value=False)

        ai_message = AIMessage(content="")
        ai_message.tool_calls = [
            {
                "name": "email_tool",
                "args": {
                    "recipient_emails": ["user1@example.com", "user2@example.com"],
                    "subject": "Test",
                    "body": "Message",
                },
                "id": "call-456",
            }
        ]

        adapter.handle_supervisor_tool_calls(ai_message, author="supervisor")

        mock_agent._on_tool_start.assert_called_once()
        call_args = mock_agent._on_tool_start.call_args

        # Verify args are JSON (double quotes), not Python repr (single quotes)
        tool_args_str = call_args.args[1]
        parsed = json.loads(tool_args_str)  # Should not raise JSONDecodeError
        assert parsed["recipient_emails"] == ["user1@example.com", "user2@example.com"]

    def test_parse_update_type_handles_complex_nested_payloads(self, mock_agent):
        """Test that complex payloads with nested quotes serialize correctly."""
        adapter = LangGraphEventAdapter(mock_agent)

        ai_message = AIMessage(content="")
        ai_message.tool_calls = [
            {
                "name": "cypher_query",
                "args": {
                    "query": 'MATCH (n) WHERE n.name = "test" RETURN n',
                    "repository_ids": ["6a565360-fd33-4bd6-9510-aa333670bf72"],
                },
                "id": "call-789",
            }
        ]

        value = {"agent": {"messages": [ai_message]}}
        adapter.parse_update_type(value)

        mock_agent._on_tool_start.assert_called_once()
        call_args = mock_agent._on_tool_start.call_args

        # Verify args parse as valid JSON without mangling
        tool_args_str = call_args.args[1]
        parsed = json.loads(tool_args_str)  # Should not raise JSONDecodeError
        assert parsed["query"] == 'MATCH (n) WHERE n.name = "test" RETURN n'
        assert parsed["repository_ids"] == ["6a565360-fd33-4bd6-9510-aa333670bf72"]

    def test_parse_update_type_preserves_non_ascii_tool_args(self, mock_agent):
        """AC #5: non-ASCII tool args render as readable UTF-8, not \\uXXXX escapes."""
        adapter = LangGraphEventAdapter(mock_agent)

        ai_message = AIMessage(content="")
        ai_message.tool_calls = [
            {
                "name": "search_tool",
                "args": {"query": "Bakı şəhəri", "note": "Привет 😀"},
                "id": "call-non-ascii",
            }
        ]

        value = {"agent": {"messages": [ai_message]}}
        adapter.parse_update_type(value)

        mock_agent._on_tool_start.assert_called_once()
        tool_args_str = mock_agent._on_tool_start.call_args.args[1]

        # Readable characters are present verbatim, and no \uXXXX escapes leaked.
        assert "Bakı şəhəri" in tool_args_str
        assert "Привет 😀" in tool_args_str
        assert "\\u" not in tool_args_str
        # Still valid JSON that round-trips to the original values.
        assert json.loads(tool_args_str) == {"query": "Bakı şəhəri", "note": "Привет 😀"}

    def test_parse_update_type_ascii_args_unaffected(self, mock_agent):
        """AC #6: ASCII-only tool args are unchanged by the encoding fix."""
        adapter = LangGraphEventAdapter(mock_agent)

        ai_message = AIMessage(content="")
        ai_message.tool_calls = [
            {
                "name": "search_tool",
                "args": {"query": "hello world", "count": 3},
                "id": "call-ascii",
            }
        ]

        value = {"agent": {"messages": [ai_message]}}
        adapter.parse_update_type(value)

        mock_agent._on_tool_start.assert_called_once()
        tool_args_str = mock_agent._on_tool_start.call_args.args[1]

        assert "\\u" not in tool_args_str
        assert json.loads(tool_args_str) == {"query": "hello world", "count": 3}


class TestLangGraphCallbackBridgeSupervisorHandoff:
    def test_on_supervisor_handoff_forwards_real_tool_name(self):
        callback = MagicMock()
        agent = MagicMock()
        agent.supervisor_callbacks = [callback]
        bridge = LangGraphCallbackBridge(agent, get_logger=MagicMock())
        run_id = uuid4()

        bridge.on_supervisor_handoff("transfer_to_x", run_id, "task", display_name="X #2")

        serialized = callback.on_tool_start.call_args.args[0]
        assert serialized == {"name": "X #2", "tool_name": "transfer_to_x"}

    def test_on_supervisor_handoff_without_display_name_uses_destination(self):
        callback = MagicMock()
        agent = MagicMock()
        agent.supervisor_callbacks = [callback]
        bridge = LangGraphCallbackBridge(agent, get_logger=MagicMock())

        bridge.on_supervisor_handoff("transfer_to_x", uuid4(), "task")

        serialized = callback.on_tool_start.call_args.args[0]
        assert serialized == {"name": "transfer_to_x", "tool_name": "transfer_to_x"}


class TestLangGraphCallbackBridgeToolAttribution:
    """The stream gives the callbacks only a tool name; the bridge adds the attribution the agent stamped on the tool."""

    @staticmethod
    def _bridge(tools):
        agent = MagicMock()
        agent.tools = tools
        agent.callbacks = [MagicMock()]
        return LangGraphCallbackBridge(agent, get_logger=MagicMock()), agent.callbacks[0]

    @staticmethod
    def _tool(name, metadata):
        tool = MagicMock()
        tool.name = name
        tool.metadata = metadata
        return tool

    def test_the_callbacks_get_the_attribution_of_the_tool(self):
        metadata = {
            "agent_name": "asst",
            "llm_model": "gpt",
            "user_name": "u",
            "project": "p",
            "assistant_id": "a-1",
            "workflow_id": "wf-1",
            "tool_type": "plugin",
            "request_id": "r",
            "user_id": "u-1",
        }
        bridge, callback = self._bridge([self._tool("search", metadata)])

        bridge.on_tool_start("search", "{}", run_id=uuid4())

        assert callback.on_tool_start.call_args.kwargs["metadata"] == {
            "agent_name": "asst",
            "llm_model": "gpt",
            "user_name": "u",
            "project": "p",
            "assistant_id": "a-1",
            "workflow_id": "wf-1",
            "tool_type": "plugin",
        }

    def test_the_output_format_is_not_passed_so_the_thought_is_rendered_as_before(self):
        bridge, callback = self._bridge([self._tool("search", {"output_format": "markdown", "agent_name": "asst"})])

        bridge.on_tool_start("search", "{}", run_id=uuid4())

        assert "output_format" not in callback.on_tool_start.call_args.kwargs["metadata"]

    def test_a_tool_the_agent_does_not_own_gets_empty_metadata(self):
        bridge, callback = self._bridge([self._tool("search", {"agent_name": "asst"})])

        bridge.on_tool_start("somebody_elses_tool", "{}", run_id=uuid4())

        assert callback.on_tool_start.call_args.kwargs["metadata"] == {}

    def test_a_tool_without_metadata_gets_empty_metadata(self):
        bridge, callback = self._bridge([self._tool("search", None)])

        bridge.on_tool_start("search", "{}", run_id=uuid4())

        assert callback.on_tool_start.call_args.kwargs["metadata"] == {}


class TestTruncatedTextResponses:
    """Text-only responses cut at max_tokens are finalized and logged (EPMCDME-15787)."""

    @pytest.fixture
    def agent(self):
        agent = MagicMock()
        agent.llm_model = "claude-opus-5-5"
        agent.is_finish_reason_tool_calls.side_effect = lambda m: bool(getattr(m, "tool_calls", None))
        agent.is_finish_reason_stop.return_value = False
        agent._on_llm_end = MagicMock()
        agent._safe_check_for_truncation = MagicMock()
        return agent

    @pytest.fixture
    def mock_logger(self):
        with patch("codemie.agents.langgraph_event_adapter.logger") as mock_logger:
            yield mock_logger

    @pytest.mark.parametrize(
        ("metadata", "indicator"),
        [
            ({"finish_reason": "length"}, "finish_reason=length"),
            ({"stopReason": "max_tokens"}, "stop_reason=max_tokens"),
        ],
    )
    def test_agent_text_truncation_finalizes_and_warns(self, agent, mock_logger, metadata, indicator):
        message = AIMessage(content="1\n2\n3", id="msg-1", response_metadata=metadata)
        message.usage_metadata = {"input_tokens": 10, "output_tokens": 4096, "total_tokens": 4106}

        LangGraphEventAdapter(agent).parse_update_type({"agent": {"messages": [message]}})

        agent._on_llm_end.assert_called_once_with(response=message, run_id="msg-1", author=None)
        agent._safe_check_for_truncation.assert_not_called()
        mock_logger.warning.assert_called_once_with(
            f"truncated_response model=claude-opus-5-5 indicator={indicator} output_tokens=4096 action=kept"
        )

    def test_missing_usage_metadata_logs_unknown(self, agent, mock_logger):
        message = AIMessage(content="partial", id="msg-2", response_metadata={"finish_reason": "length"})

        LangGraphEventAdapter(agent).parse_update_type({"agent": {"messages": [message]}})

        mock_logger.warning.assert_called_once_with(
            "truncated_response model=claude-opus-5-5 indicator=finish_reason=length output_tokens=unknown action=kept"
        )

    def test_normal_stop_not_treated_as_truncated(self, agent, mock_logger):
        agent.is_finish_reason_stop.return_value = True
        message = AIMessage(content="done", id="msg-3", response_metadata={"finish_reason": "stop"})

        LangGraphEventAdapter(agent).parse_update_type({"agent": {"messages": [message]}})

        agent._on_llm_end.assert_called_once_with(response=message, run_id="msg-3")
        mock_logger.warning.assert_not_called()

    def test_tool_call_truncation_still_uses_existing_check(self, agent, mock_logger):
        message = AIMessage(content="", id="msg-4", response_metadata={"finish_reason": "length"})
        message.tool_calls = [{"name": "search", "args": {"q": "x"}, "id": "call-1"}]
        agent._safe_check_for_truncation.side_effect = RuntimeError("token limit")

        with pytest.raises(RuntimeError, match="token limit"):
            LangGraphEventAdapter(agent).parse_update_type({"agent": {"messages": [message]}})

        mock_logger.warning.assert_not_called()

    def test_supervisor_text_truncation_finalizes_and_warns(self, agent, mock_logger):
        agent._supervisor_coordinator.binding_for.return_value = None
        message = AIMessage(content="final", id="msg-5", response_metadata={"finish_reason": "length"})

        LangGraphEventAdapter(agent).parse_supervisor_update_type({"supervisor": {"messages": [message]}})

        agent._on_llm_end.assert_called_once_with(response=message, run_id="msg-5", author=None)
        mock_logger.warning.assert_called_once_with(
            "truncated_response model=claude-opus-5-5 indicator=finish_reason=length output_tokens=unknown action=kept"
        )

    def test_supervisor_subassistant_truncation_finalizes_and_still_reports_result(self, agent, mock_logger):
        binding = (uuid4(), "supervisor")
        agent._supervisor_coordinator.binding_for.return_value = binding
        message = AIMessage(content="partial", id="msg-6", response_metadata={"stop_reason": "max_tokens"})

        LangGraphEventAdapter(agent).parse_supervisor_update_type(
            {"analyst": {"messages": [message]}}, author="analyst"
        )

        agent._on_llm_end.assert_called_once_with(response=message, run_id="msg-6", author="analyst")
        agent._LangGraphAgent__handle_supervisor_subassistant_result.assert_called_once_with(
            [message], run_id=binding, author="analyst"
        )
        mock_logger.warning.assert_called_once_with(
            "truncated_response model=claude-opus-5-5 indicator=stop_reason=max_tokens output_tokens=unknown action=kept"
        )

    def test_truncated_invalid_tool_call_finalizes_then_raises(self, agent, mock_logger):
        message = AIMessage(
            content="",
            id="msg-8",
            response_metadata={"finish_reason": "length"},
            invalid_tool_calls=[{"name": "search", "args": '{"q": "unterminated', "id": "call-9", "error": None}],
        )
        agent._safe_check_for_truncation.side_effect = RuntimeError("token limit")

        with pytest.raises(RuntimeError, match="token limit"):
            LangGraphEventAdapter(agent).parse_update_type({"agent": {"messages": [message]}})

        agent._on_llm_end.assert_called_once_with(response=message, run_id="msg-8", author=None)
        agent._safe_check_for_truncation.assert_called_once_with(message)
        mock_logger.warning.assert_not_called()

    def test_supervisor_handoff_back_not_treated_as_truncated(self, agent, mock_logger):
        from codemie.agents.supervisor.constants import METADATA_KEY_HANDOFF_BACK

        message = AIMessage(content="back", id="msg-7", response_metadata={METADATA_KEY_HANDOFF_BACK: True})

        LangGraphEventAdapter(agent).parse_supervisor_update_type({"analyst": {"messages": [message]}})

        agent._on_llm_end.assert_not_called()
        mock_logger.warning.assert_not_called()
        agent._LangGraphAgent__handle_supervisor_handoff_back.assert_called_once()


class TestGetTruncationIndicator:
    @pytest.mark.parametrize(
        ("metadata", "expected"),
        [
            ({"finish_reason": "length"}, "finish_reason=length"),
            ({"finish_reason": "max_tokens"}, "finish_reason=max_tokens"),
            ({"stop_reason": "max_tokens"}, "stop_reason=max_tokens"),
            ({"stopReason": "max_tokens"}, "stop_reason=max_tokens"),
            ({"finish_reason": "stop"}, None),
            ({}, None),
            (None, None),
        ],
    )
    def test_indicator(self, metadata, expected):
        from codemie.agents.langgraph_event_adapter import get_truncation_indicator

        assert get_truncation_indicator(metadata) == expected
