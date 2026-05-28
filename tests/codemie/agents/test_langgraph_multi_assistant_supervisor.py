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

"""Supervisor-focused tests for LangGraph multi-assistant behavior."""

from collections import deque
from uuid import uuid4

import pytest
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from unittest.mock import ANY, MagicMock, patch

from codemie.agents.callbacks.agent_invoke_callback import AgentInvokeCallback
from codemie.agents.callbacks.agent_streaming_callback import AgentStreamingCallback
from codemie.agents.langgraph_agent import (
    LangGraphAgent,
    _SupervisorChunkContext,
    _SupervisorHandoffTracker,
)
from codemie.agents.supervisor.constants import METADATA_KEY_HANDOFF_BACK, METADATA_KEY_HANDOFF_DESTINATION
from codemie.chains.base import ThoughtOutputFormat
from codemie.core.models import AssistantChatRequest
from codemie.core.thread import ThreadedGenerator


class TestSupervisorHandoffTracker:
    def test_promote_pending_matches_by_task(self):
        tracker = _SupervisorHandoffTracker()
        first_run_id = uuid4()
        second_run_id = uuid4()

        tracker.queue_pending("analyst", (first_run_id, None, "task one", "Analyst #1"))
        tracker.queue_pending("analyst", (second_run_id, None, "task two", "Analyst #2"))

        promoted = tracker.promote_pending("analyst", "task two")

        assert promoted == (second_run_id, None, "task two", "Analyst #2")
        assert tracker.pending["analyst"] == deque([(first_run_id, None, "task one", "Analyst #1")])

    def test_complete_removes_active_handoff_and_binding(self):
        tracker = _SupervisorHandoffTracker()
        run_id = uuid4()
        handoff = (run_id, None, "task one", "Analyst")

        binding = tracker.activate("analyst", "analyst:instance-1", handoff)

        assert binding == (run_id, None)

        completed = tracker.complete("analyst:instance-1")

        assert completed == (run_id, None)
        assert tracker.run_bindings == {}
        assert tracker.active == {}


class TestSupervisorChunkContext:
    def test_from_chunk_defaults_to_supervisor_without_namespace(self):
        context = _SupervisorChunkContext.from_chunk(((), "updates", {"supervisor": {"messages": []}}))

        assert context.raw_author == "supervisor"
        assert context.author_key == "supervisor"
        assert context.author is None
        assert context.delegated_task is None

    def test_from_chunk_extracts_flat_namespace_and_task(self):
        context = _SupervisorChunkContext.from_chunk(
            (("analyst:node-1",), "messages", (HumanMessage(content="analyze data"), {}))
        )

        assert context.raw_author == "analyst"
        assert context.author_key == "analyst:node-1"
        assert context.author == "analyst:node-1"
        assert context.delegated_task == "analyze data"

    def test_from_chunk_prefers_leaf_namespace_for_nested_subgraphs(self):
        context = _SupervisorChunkContext.from_chunk(
            (("planner:outer-1", "analyst:inner-1"), "messages", (HumanMessage(content="analyze data"), {}))
        )

        assert context.raw_author == "analyst"
        assert context.author_key == "analyst:inner-1"
        assert context.author == "analyst:inner-1"
        assert context.delegated_task == "analyze data"


class TestLangGraphMultiAssistantSupervisor:
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
    def mock_regular_tool(self):
        tool = MagicMock(spec=BaseTool)
        tool.name = "regular_tool"
        tool.description = "A regular tool"
        tool.metadata = {}
        return tool

    @pytest.fixture
    def mock_agent_tool(self):
        tool = MagicMock(spec=BaseTool)
        tool.name = "agent_tool"
        tool.description = "An agent tool"
        tool.metadata = {}

        mock_agent = MagicMock()
        mock_agent_executor = MagicMock()
        mock_agent.agent_executor = mock_agent_executor
        tool._agent = mock_agent

        return tool

    @pytest.fixture
    def agent_config_with_subagents(self, mock_user, mock_request, mock_regular_tool, mock_agent_tool):
        assistant = MagicMock()
        assistant.project = "test"
        mock_subagent = mock_agent_tool._agent.agent_executor
        return {
            "agent_name": "SupervisorAgent",
            "description": "Supervisor agent with subagents",
            "tools": [mock_regular_tool],
            "subagents": [mock_subagent],
            "request": mock_request,
            "system_prompt": "You are a supervisor assistant managing subagents.",
            "request_uuid": "test_uuid",
            "user": mock_user,
            "llm_model": "gpt-4",
            "assistant": assistant,
        }

    @pytest.fixture
    def supervisor_agent(self, agent_config_with_subagents):
        with patch("codemie.agents.langgraph_agent.create_supervisor") as mock_create_supervisor:
            mock_supervisor = MagicMock()
            mock_supervisor.compile.return_value = MagicMock()
            mock_create_supervisor.return_value = mock_supervisor

            with patch("codemie.agents.langgraph_agent.get_llm_by_credentials"):
                agent = LangGraphAgent(**agent_config_with_subagents)
                yield agent

    def test_supervisor_agent_initialization(self, supervisor_agent, mock_regular_tool, mock_agent_tool):
        assert supervisor_agent.agent_name == "SupervisorAgent"
        assert len(supervisor_agent.tools) == 1
        assert len(supervisor_agent.subagents) == 1
        assert supervisor_agent.tools[0] == mock_regular_tool

    def test_supervisor_agent_uses_create_supervisor(self, mock_user, mock_request, mock_regular_tool):
        assistant = MagicMock()
        assistant.project = "test"
        config = {
            "agent_name": "SupervisorAgent",
            "description": "Supervisor agent with subagents",
            "tools": [mock_regular_tool],
            "subagents": [MagicMock()],
            "request": mock_request,
            "system_prompt": "You are a supervisor assistant managing subagents.",
            "request_uuid": "test_uuid",
            "user": mock_user,
            "llm_model": "gpt-4",
            "assistant": assistant,
        }

        with patch("codemie.agents.langgraph_agent.create_supervisor") as mock_create_supervisor:
            with patch("codemie.agents.langgraph_agent.create_smart_react_agent") as mock_create_smart_react:
                mock_supervisor = MagicMock()
                mock_supervisor.compile.return_value = MagicMock()
                mock_create_supervisor.return_value = mock_supervisor

                with patch("codemie.agents.langgraph_agent.get_llm_by_credentials"):
                    LangGraphAgent(**config)

                mock_create_supervisor.assert_called_once()
                mock_create_smart_react.assert_not_called()
                call_kwargs = mock_create_supervisor.call_args.kwargs
                assert call_kwargs["add_handoff_back_messages"] is False
                assert call_kwargs["output_mode"] == "last_message"
                assert callable(call_kwargs["pre_model_hook"])

    def test_regular_agent_uses_create_react_agent(self, mock_user, mock_request, mock_regular_tool):
        assistant = MagicMock()
        assistant.project = "test"
        config = {
            "agent_name": "RegularAgent",
            "description": "Regular agent without subagents",
            "tools": [mock_regular_tool],
            "request": mock_request,
            "system_prompt": "You are a regular assistant.",
            "request_uuid": "test_uuid",
            "user": mock_user,
            "llm_model": "gpt-4",
            "assistant": assistant,
        }

        with patch("codemie.agents.langgraph_agent.create_supervisor") as mock_create_supervisor:
            with patch("codemie.agents.langgraph_agent.create_smart_react_agent") as mock_create_smart_react:
                mock_create_smart_react.return_value = MagicMock()

                with patch("codemie.agents.langgraph_agent.get_llm_by_credentials"):
                    LangGraphAgent(**config)

                mock_create_smart_react.assert_called_once()
                mock_create_supervisor.assert_not_called()

    def test_check_is_handoff_tool(self):
        assert LangGraphAgent._check_is_handoff_tool("transfer_to_analyst") is True
        assert LangGraphAgent._check_is_handoff_tool("transfer_to_researcher") is True
        assert LangGraphAgent._check_is_handoff_tool("regular_tool") is False
        assert LangGraphAgent._check_is_handoff_tool("transfer") is False

    def test_extract_agent_name_from_tool(self):
        assert LangGraphAgent._extract_agent_name_from_tool("transfer_to_analyst") == "analyst"
        assert LangGraphAgent._extract_agent_name_from_tool("transfer_to_researcher") == "researcher"
        assert LangGraphAgent._extract_agent_name_from_tool("transfer_to_data_scientist") == "data_scientist"

    def test_supervisor_callbacks_initialization(self, supervisor_agent):
        assert hasattr(supervisor_agent, "supervisor_callbacks")
        assert isinstance(supervisor_agent.supervisor_callbacks, list)
        assert len(supervisor_agent.supervisor_callbacks) == 1
        assert isinstance(supervisor_agent.supervisor_callbacks[0], AgentInvokeCallback)

    def test_supervisor_callbacks_with_thread_generator(self, agent_config_with_subagents):
        agent_config_with_subagents["thread_generator"] = ThreadedGenerator()
        agent_config_with_subagents["stream_steps"] = True

        with patch("codemie.agents.langgraph_agent.create_supervisor") as mock_create_supervisor:
            mock_supervisor = MagicMock()
            mock_supervisor.compile.return_value = MagicMock()
            mock_create_supervisor.return_value = mock_supervisor

            with patch("codemie.agents.langgraph_agent.get_llm_by_credentials"):
                agent = LangGraphAgent(**agent_config_with_subagents)

        assert len(agent.supervisor_callbacks) == 1
        assert isinstance(agent.supervisor_callbacks[0], AgentStreamingCallback)

    def test_get_thoughts_from_callback_includes_supervisor_thoughts(self, supervisor_agent):
        supervisor_thought = {"id": "handoff-1", "input_text": "delegate task", "message": "done"}
        tool_thought = {"id": "tool-1", "input_text": "{}", "message": "tool output"}

        supervisor_agent.supervisor_callbacks = [MagicMock(thoughts=[supervisor_thought])]
        supervisor_agent.callbacks = [MagicMock(thoughts=[tool_thought])]

        thoughts = supervisor_agent.get_thoughts_from_callback()

        assert thoughts == [supervisor_thought, tool_thought]

    def test_set_thread_context_updates_invoke_callback_parent_id(self, supervisor_agent):
        supervisor_callback = AgentInvokeCallback()
        tool_callback = AgentInvokeCallback()
        supervisor_agent.supervisor_callbacks = [supervisor_callback]
        supervisor_agent.callbacks = [tool_callback]

        supervisor_agent.set_thread_context(context={}, parent_thought_id="handoff-1", author="analyst")

        assert supervisor_callback.parent_id == "handoff-1"
        assert tool_callback.parent_id == "handoff-1"

    def test_stream_graph_with_subagents(self, supervisor_agent):
        mock_stream = [
            ("supervisor", "messages", (AIMessage(content="chunk1"), None)),
            ("supervisor", "updates", {"supervisor": {"messages": [AIMessage(content="chunk2")]}}),
        ]

        supervisor_agent.agent_executor = MagicMock()
        supervisor_agent.agent_executor.stream.return_value = iter(mock_stream)
        supervisor_agent.process_chunk = MagicMock()
        supervisor_agent._on_chain_end = MagicMock()
        supervisor_agent._get_last_ai_message_content = MagicMock(return_value="final_result")

        result = supervisor_agent._stream_graph({"input": "test"})

        supervisor_agent.agent_executor.stream.assert_called_once()
        call_args = supervisor_agent.agent_executor.stream.call_args
        assert call_args[1]["subgraphs"] is True
        assert result == "final_result"

    def test_stream_graph_without_subagents(self, mock_user, mock_request, mock_regular_tool):
        assistant = MagicMock()
        assistant.project = "test"
        config = {
            "agent_name": "RegularAgent",
            "description": "Regular agent",
            "tools": [mock_regular_tool],
            "request": mock_request,
            "system_prompt": "You are a regular assistant.",
            "request_uuid": "test_uuid",
            "user": mock_user,
            "llm_model": "gpt-4",
            "assistant": assistant,
        }

        with patch("codemie.agents.langgraph_agent.create_smart_react_agent") as mock_create_smart_react:
            mock_create_smart_react.return_value = MagicMock()
            with patch("codemie.agents.langgraph_agent.get_llm_by_credentials"):
                agent = LangGraphAgent(**config)

        mock_stream = [
            ("messages", (AIMessage(content="chunk1"), None)),
            ("updates", {"agent": {"messages": [AIMessage(content="chunk2")]}}),
        ]

        agent.agent_executor = MagicMock()
        agent.agent_executor.stream.return_value = iter(mock_stream)
        agent.process_chunk = MagicMock()
        agent._on_chain_end = MagicMock()
        agent._get_last_ai_message_content = MagicMock(return_value="final_result")

        agent._stream_graph({"input": "test"})

        call_args = agent.agent_executor.stream.call_args
        assert call_args[1]["subgraphs"] is False

    def test_process_chunk_supervisor(self, supervisor_agent):
        chunk = ("supervisor", "messages", "test_value")
        chunks_collector = []

        supervisor_agent._process_chunk_for_supervisor = MagicMock()
        supervisor_agent._process_chunk_for_agent = MagicMock()

        supervisor_agent.process_chunk(chunk, chunks_collector)

        supervisor_agent._process_chunk_for_supervisor.assert_called_once_with(chunk, chunks_collector)
        supervisor_agent._process_chunk_for_agent.assert_not_called()

    def test_parse_supervisor_update_type_handoff(self, supervisor_agent):
        ai_message = AIMessage(content="Handing off to analyst")
        ai_message.tool_calls = [{"name": "transfer_to_analyst", "args": {"task": "analyze data"}}]

        value = {"supervisor": {"messages": [ai_message]}}
        agent_name = "analyst"

        supervisor_agent._on_supervisor_handoff = MagicMock()
        supervisor_agent.set_thread_context = MagicMock()

        supervisor_agent._LangGraphAgent__parse_supervisor_update_type(value, author=agent_name)

        supervisor_agent._on_supervisor_handoff.assert_not_called()
        supervisor_agent.set_thread_context.assert_not_called()
        assert agent_name in supervisor_agent._pending_handoffs
        _, stored_author, stored_task, stored_display_name = supervisor_agent._pending_handoffs[agent_name][0]
        assert stored_author == agent_name
        assert stored_task == "analyze data"
        assert stored_display_name == "Analyst"

    def test_process_chunk_supervisor_passes_handoff_task_to_ui(self, supervisor_agent):
        run_id = uuid4()
        supervisor_agent._pending_handoffs = {"analyst": deque([(run_id, None, "analyze data", "Analyst")])}
        supervisor_agent._on_supervisor_handoff = MagicMock()
        supervisor_agent.set_thread_context = MagicMock()

        chunk = (("analyst:node",), "messages", (AIMessage(content="starting"), {}))

        supervisor_agent._process_chunk_for_supervisor(chunk, [])

        supervisor_agent._on_supervisor_handoff.assert_called_once_with(
            "transfer_to_analyst", run_id, "analyze data", author=None, display_name="Analyst"
        )
        supervisor_agent.set_thread_context.assert_called_once_with(
            context={}, parent_thought_id=str(run_id), author="analyst:node"
        )

    def test_process_chunk_supervisor_uses_leaf_namespace_for_nested_subgraphs(self, supervisor_agent):
        run_id = uuid4()
        supervisor_agent._pending_handoffs = {"analyst": deque([(run_id, None, "analyze data", "Analyst")])}
        supervisor_agent._on_supervisor_handoff = MagicMock()
        supervisor_agent.set_thread_context = MagicMock()

        chunk = (("planner:outer-1", "analyst:inner-1"), "messages", (HumanMessage(content="analyze data"), {}))

        supervisor_agent._process_chunk_for_supervisor(chunk, [])

        supervisor_agent._on_supervisor_handoff.assert_called_once_with(
            "transfer_to_analyst", run_id, "analyze data", author=None, display_name="Analyst"
        )
        supervisor_agent.set_thread_context.assert_called_once_with(
            context={}, parent_thought_id=str(run_id), author="analyst:inner-1"
        )
        assert supervisor_agent._handoff_run_ids["analyst:inner-1"] == (run_id, None)

    def test_process_chunk_supervisor_suffixes_parallel_same_subassistant_names(self, supervisor_agent):
        first_run_id = uuid4()
        second_run_id = uuid4()
        supervisor_agent._pending_handoffs = {
            "analyst": deque(
                [
                    (first_run_id, None, "analyze batch one", "Analyst #1"),
                    (second_run_id, None, "analyze batch two", "Analyst #2"),
                ]
            )
        }
        supervisor_agent._on_supervisor_handoff = MagicMock()
        supervisor_agent.set_thread_context = MagicMock()

        supervisor_agent._process_chunk_for_supervisor(
            (("analyst:instance-1",), "messages", (AIMessage(content="starting one"), {})), []
        )
        supervisor_agent._process_chunk_for_supervisor(
            (("analyst:instance-2",), "messages", (AIMessage(content="starting two"), {})), []
        )

        supervisor_agent._on_supervisor_handoff.assert_not_called()

        supervisor_agent._process_chunk_for_supervisor(
            (("analyst:instance-1",), "messages", (HumanMessage(content="analyze batch one"), {})), []
        )
        supervisor_agent._process_chunk_for_supervisor(
            (("analyst:instance-2",), "messages", (HumanMessage(content="analyze batch two"), {})), []
        )

        assert supervisor_agent._on_supervisor_handoff.call_args_list[0].kwargs["display_name"] == "Analyst #1"
        assert supervisor_agent._on_supervisor_handoff.call_args_list[1].kwargs["display_name"] == "Analyst #2"
        assert supervisor_agent._handoff_run_ids["analyst:instance-1"] == (first_run_id, None)
        assert supervisor_agent._handoff_run_ids["analyst:instance-2"] == (second_run_id, None)

    def test_process_chunk_supervisor_matches_same_subassistant_by_task_when_order_flips(self, supervisor_agent):
        first_run_id = uuid4()
        second_run_id = uuid4()
        supervisor_agent._pending_handoffs = {
            "analyst": deque(
                [
                    (first_run_id, None, "analyze batch one", "Analyst #1"),
                    (second_run_id, None, "analyze batch two", "Analyst #2"),
                ]
            )
        }
        supervisor_agent._on_supervisor_handoff = MagicMock()
        supervisor_agent.set_thread_context = MagicMock()

        supervisor_agent._process_chunk_for_supervisor(
            (("analyst:instance-2",), "messages", (HumanMessage(content="analyze batch two"), {})), []
        )
        supervisor_agent._process_chunk_for_supervisor(
            (("analyst:instance-1",), "messages", (HumanMessage(content="analyze batch one"), {})), []
        )

        assert supervisor_agent._on_supervisor_handoff.call_args_list[0].kwargs["display_name"] == "Analyst #2"
        assert supervisor_agent._on_supervisor_handoff.call_args_list[1].kwargs["display_name"] == "Analyst #1"
        assert supervisor_agent._handoff_run_ids["analyst:instance-2"] == (second_run_id, None)
        assert supervisor_agent._handoff_run_ids["analyst:instance-1"] == (first_run_id, None)

    def test_process_chunk_supervisor_rebinds_reused_instance_to_matching_task_parent(
        self, agent_config_with_subagents
    ):
        agent_config_with_subagents["thread_generator"] = ThreadedGenerator()
        agent_config_with_subagents["stream_steps"] = True

        with patch("codemie.agents.langgraph_agent.create_supervisor") as mock_create_supervisor:
            mock_supervisor = MagicMock()
            mock_supervisor.compile.return_value = MagicMock()
            mock_create_supervisor.return_value = mock_supervisor

            with patch("codemie.agents.langgraph_agent.get_llm_by_credentials"):
                agent = LangGraphAgent(**agent_config_with_subagents)

        first_run_id = uuid4()
        second_run_id = uuid4()
        agent._pending_handoffs = {
            "analyst": deque(
                [
                    (first_run_id, None, "task one", "Analyst #1"),
                    (second_run_id, None, "task two", "Analyst #2"),
                ]
            )
        }

        agent._process_chunk_for_supervisor(
            (("analyst:instance-1",), "messages", (HumanMessage(content="task one"), {})), []
        )
        agent._process_chunk_for_supervisor(
            (("analyst:instance-2",), "messages", (HumanMessage(content="task two"), {})), []
        )
        agent._process_chunk_for_supervisor(
            (("analyst:instance-2",), "messages", (HumanMessage(content="task one"), {})), []
        )

        tool_call_message = AIMessage(content="")
        tool_call_message.tool_calls = [
            {
                "name": "lookup_repo",
                "args": {"query": "task one"},
                "id": "tool-call-1",
                "type": "tool_call",
            }
        ]
        agent._process_chunk_for_supervisor(
            (("analyst:instance-2",), "updates", {"agent": {"messages": [tool_call_message]}}), []
        )
        agent._process_chunk_for_supervisor(
            (
                ("analyst:instance-2",),
                "updates",
                {
                    "tools": {
                        "messages": [ToolMessage(content="repo result", tool_call_id="tool-call-1", name="lookup_repo")]
                    }
                },
            ),
            [],
        )

        thoughts = agent.thread_generator.thoughts
        task_one_thought = next(thought for thought in thoughts if thought["id"] == str(first_run_id))
        task_two_thought = next(thought for thought in thoughts if thought["id"] == str(second_run_id))

        assert len(task_one_thought["children"]) == 1
        assert task_one_thought["children"][0]["input_text"] == "{'query': 'task one'}"
        assert task_one_thought["children"][0]["parent_id"] == str(first_run_id)
        assert task_two_thought["children"] == []

    def test_agent_invoke_callback_keeps_parallel_subassistant_parents_separate(self):
        callback = AgentInvokeCallback()

        callback.set_context({}, "handoff-1", author="analyst:instance-1")
        callback.on_tool_start({"name": "lookup_repo"}, "task one", author="analyst:instance-1")

        callback.set_context({}, "handoff-2", author="analyst:instance-2")
        callback.on_tool_start({"name": "lookup_repo"}, "task two", author="analyst:instance-2")

        callback.on_tool_end("done one", author="analyst:instance-1")
        callback.on_tool_end("done two", author="analyst:instance-2")

        thought_by_input = {thought["input_text"]: thought for thought in callback.thoughts}

        assert thought_by_input["task one"]["parent_id"] == "handoff-1"
        assert thought_by_input["task two"]["parent_id"] == "handoff-2"

    def test_process_chunk_supervisor_buffers_early_parallel_same_name_activity_until_task_is_known(
        self, agent_config_with_subagents
    ):
        agent_config_with_subagents["thread_generator"] = ThreadedGenerator()
        agent_config_with_subagents["stream_steps"] = True

        with patch("codemie.agents.langgraph_agent.create_supervisor") as mock_create_supervisor:
            mock_supervisor = MagicMock()
            mock_supervisor.compile.return_value = MagicMock()
            mock_create_supervisor.return_value = mock_supervisor

            with patch("codemie.agents.langgraph_agent.get_llm_by_credentials"):
                agent = LangGraphAgent(**agent_config_with_subagents)

        first_run_id = uuid4()
        second_run_id = uuid4()
        agent._pending_handoffs = {
            "analyst": deque(
                [
                    (first_run_id, None, "task one", "Analyst #1"),
                    (second_run_id, None, "task two", "Analyst #2"),
                ]
            )
        }

        agent._process_chunk_for_supervisor(
            (("analyst:instance-2",), "messages", (AIMessage(content="working on task two"), {})),
            [],
        )

        assert agent.thread_generator.thoughts == []

        agent._process_chunk_for_supervisor(
            (("analyst:instance-2",), "messages", (HumanMessage(content="task two"), {})),
            [],
        )

        thoughts = agent.thread_generator.thoughts
        task_two_handoff = next(thought for thought in thoughts if thought["id"] == str(second_run_id))
        assert task_two_handoff["author_name"] == "Analyst #2"
        assert len(task_two_handoff["children"]) == 1
        assert task_two_handoff["children"][0]["message"] == "working on task two"

        remaining_pending = agent._pending_handoffs["analyst"]
        assert remaining_pending == deque([(first_run_id, None, "task one", "Analyst #1")])

    def test_handle_supervisor_handoff_back_reports_only_final_subagent_answer(self, supervisor_agent):
        from langgraph_supervisor.handoff import create_handoff_back_messages

        handoff_back_ai, handoff_back_tool = create_handoff_back_messages("analyst", "supervisor")
        messages = [
            HumanMessage(content="Original user request"),
            ToolMessage(
                content="Successfully transferred to analyst",
                name="transfer_to_analyst",
                tool_call_id="call-123",
                response_metadata={METADATA_KEY_HANDOFF_DESTINATION: "analyst"},
            ),
            AIMessage(content="Final analyst answer"),
            handoff_back_ai,
            handoff_back_tool,
        ]

        supervisor_agent._on_subassistant_back = MagicMock()
        run_id = uuid4()
        supervisor_agent._handoff_run_ids = {"analyst": (run_id, None)}

        supervisor_agent._LangGraphAgent__handle_supervisor_handoff_back(
            messages,
            run_id=supervisor_agent._handoff_run_ids["analyst"],
            author="analyst",
        )

        supervisor_agent._on_subassistant_back.assert_called_once_with("Final analyst answer", run_id, None)
        assert "analyst" not in supervisor_agent._handoff_run_ids

    def test_parse_supervisor_update_type_regular_tool(self, supervisor_agent):
        ai_message = AIMessage(content="Using regular tool")
        ai_message.tool_calls = [{"name": "search_tool", "args": {"query": "test"}}]

        value = {"supervisor": {"messages": [ai_message]}}

        supervisor_agent.is_finish_reason_tool_calls = MagicMock(return_value=True)
        supervisor_agent._get_tool_call_args = MagicMock(return_value=("search_tool", "{'query': 'test'}"))
        supervisor_agent._on_tool_start = MagicMock()
        supervisor_agent._on_supervisor_handoff = MagicMock()

        supervisor_agent._LangGraphAgent__parse_supervisor_update_type(value)

        supervisor_agent._on_tool_start.assert_called_once()
        call_args = supervisor_agent._on_tool_start.call_args
        assert call_args.args == ("search_tool", "{'query': 'test'}")
        assert call_args.kwargs.get("run_id") is not None
        supervisor_agent._on_supervisor_handoff.assert_not_called()

    def test_process_chunk_supervisor_uses_leaf_namespace_for_nested_subagent_tools(self, supervisor_agent):
        run_id = uuid4()
        ai_message = AIMessage(content="")
        ai_message.tool_calls = [
            {"name": "search_tool", "args": {"query": "test"}, "id": "call-123", "type": "tool_call"}
        ]

        supervisor_agent._handoff_run_ids = {"analyst:inner-1": (run_id, None)}
        supervisor_agent._on_tool_start = MagicMock()
        supervisor_agent._safe_check_for_truncation = MagicMock()

        supervisor_agent._process_chunk_for_supervisor(
            (("planner:outer-1", "analyst:inner-1"), "updates", {"agent": {"messages": [ai_message]}}),
            [],
        )

        supervisor_agent._on_tool_start.assert_called_once()
        call_args = supervisor_agent._on_tool_start.call_args
        assert call_args.args == ("search_tool", "{'query': 'test'}")
        assert call_args.kwargs["author"] == "analyst:inner-1"
        assert call_args.kwargs.get("run_id") is not None

    def test_parse_supervisor_update_type_tool_message(self, supervisor_agent):
        tool_message = ToolMessage(name="search_tool", content="Search results", tool_call_id="call-123")

        value = {"supervisor": {"messages": [tool_message]}}

        supervisor_agent._parse_tool_message = MagicMock()

        supervisor_agent._LangGraphAgent__parse_supervisor_update_type(value)

        supervisor_agent._parse_tool_message.assert_called_once_with(tool_message, author=None)

    def test_on_supervisor_handoff_callback(self, supervisor_agent):
        mock_callback = MagicMock(spec=BaseCallbackHandler)
        supervisor_agent.supervisor_callbacks = [mock_callback]

        destination = "agent_analyst"
        run_id = uuid4()
        supervisor_agent._on_supervisor_handoff(destination, run_id, display_name="Analyst #1")

        mock_callback.on_tool_start.assert_called_once()
        call_args = mock_callback.on_tool_start.call_args

        assert call_args[0][0]["name"] == "Analyst #1"
        assert call_args[1]["metadata"]["output_format"] == ThoughtOutputFormat.MARKDOWN.value

    def test_on_subassistant_back_callback(self, supervisor_agent):
        mock_callback = MagicMock(spec=BaseCallbackHandler)
        supervisor_agent.supervisor_callbacks = [mock_callback]

        output = "Subassistant completed the task"
        supervisor_agent._on_subassistant_back(output)

        mock_callback.on_tool_end.assert_called_once()
        assert mock_callback.on_tool_end.call_args[0][0] == output

    def test_supervisor_callback_error_handling(self, supervisor_agent):
        mock_callback = MagicMock(spec=BaseCallbackHandler)
        mock_callback.on_tool_start.side_effect = Exception("Callback error")
        supervisor_agent.supervisor_callbacks = [mock_callback]

        run_id = uuid4()
        with patch("codemie.agents.langgraph_agent.logger") as mock_logger:
            supervisor_agent._on_supervisor_handoff("agent_test", run_id)

            mock_logger.error.assert_called_once()

    def test_get_last_ai_message_content_supervisor(self, supervisor_agent):
        ai_message = AIMessage(content="Supervisor response")
        chunk = ("updates", {"supervisor": {"messages": [ai_message]}})

        content = supervisor_agent._get_last_ai_message_content(chunk)
        assert content == "Supervisor response"

    def test_get_last_ai_message_content_regular_agent(self, mock_user, mock_request, mock_regular_tool):
        assistant = MagicMock()
        assistant.project = "test"
        config = {
            "agent_name": "RegularAgent",
            "description": "Regular agent",
            "tools": [mock_regular_tool],
            "request": mock_request,
            "system_prompt": "You are a regular assistant.",
            "request_uuid": "test_uuid",
            "user": mock_user,
            "llm_model": "gpt-4",
            "assistant": assistant,
        }

        with patch("codemie.agents.langgraph_agent.create_smart_react_agent") as mock_create_smart_react:
            mock_create_smart_react.return_value = MagicMock()
            with patch("codemie.agents.langgraph_agent.get_llm_by_credentials"):
                agent = LangGraphAgent(**config)

        ai_message = AIMessage(content="Regular agent response")
        chunk = ("updates", {"agent": {"messages": [ai_message]}})

        content = agent._get_last_ai_message_content(chunk)
        assert content == "Regular agent response"

    def test_parse_supervisor_message_type_valid_ai_message(self, supervisor_agent):
        ai_message = AIMessage(content="Processing request")
        ai_message.response_metadata = {}

        value = (ai_message, {})
        chunks_collector = []

        supervisor_agent.is_valid_ai_message = MagicMock(return_value=True)
        supervisor_agent._process_agent_streaming = MagicMock()

        supervisor_agent._LangGraphAgent__parse_supervisor_message_type(value, chunks_collector)

        supervisor_agent._process_agent_streaming.assert_called_once_with(
            "Processing request", chunks_collector, ai_message.id, author=None
        )

    def test_parse_supervisor_message_type_handoff_back_ignored(self, supervisor_agent):
        ai_message = AIMessage(content="Handoff back")
        ai_message.response_metadata = {METADATA_KEY_HANDOFF_BACK: True}

        value = (ai_message, {})
        chunks_collector = []

        supervisor_agent.is_valid_ai_message = MagicMock(return_value=True)
        supervisor_agent._process_agent_streaming = MagicMock()

        supervisor_agent._LangGraphAgent__parse_supervisor_message_type(value, chunks_collector)

        supervisor_agent._process_agent_streaming.assert_not_called()

    def test_set_subagent_execution(self, supervisor_agent):
        mock_callback = MagicMock(spec=AgentStreamingCallback)
        mock_thought = MagicMock()
        mock_callback.thoughts_storage.create_thought.return_value = mock_thought

        supervisor_agent.callbacks = [mock_callback]

        supervisor_agent.set_subagent_execution()

        mock_callback.thoughts_storage.create_thought.assert_called_once_with(
            run_id=ANY, tool_name=AgentStreamingCallback.GENERIC_TOOL_NAME
        )
        assert mock_thought.author_type == "Agent"

    def test_supervisor_state_initialization(self, supervisor_agent):
        assert hasattr(supervisor_agent, "_supervisor_state")
        assert supervisor_agent._supervisor_state is None

    def test_is_finish_reason_tool_calls_with_tool_calls(self, supervisor_agent):
        message = AIMessage(content="Using tool")
        message.tool_calls = [{"name": "test_tool", "args": {}}]

        result = supervisor_agent.is_finish_reason_tool_calls(message)
        assert result is True

    def test_is_finish_reason_tool_calls_without_tool_calls(self, supervisor_agent):
        message = AIMessage(content="No tools")

        result = supervisor_agent.is_finish_reason_tool_calls(message)
        assert result is False

    def test_is_finish_reason_tool_calls_empty_tool_calls(self, supervisor_agent):
        message = AIMessage(content="No tools")
        message.tool_calls = []

        result = supervisor_agent.is_finish_reason_tool_calls(message)
        assert result is False
