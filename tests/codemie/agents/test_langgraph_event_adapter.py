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
from unittest.mock import MagicMock
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
