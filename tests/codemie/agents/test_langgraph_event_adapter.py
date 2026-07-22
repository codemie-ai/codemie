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

from codemie.agents.langgraph_event_adapter import LangGraphEventAdapter


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
