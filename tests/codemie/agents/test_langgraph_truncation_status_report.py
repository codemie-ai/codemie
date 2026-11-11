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

"""LangGraphAgent wiring of the truncation status report (EPMCDME-16835 AC2/AC3)."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from codemie.agents.supervisor.pre_model_hooks import (
    _build_truncation_abort_pre_model_hook,
    _compose_pre_model_hooks,
)
from codemie.configs import config as global_config
from codemie.core.exceptions import TokenLimitExceededException
from tests.codemie.agents.test_langgraph_truncation import create_mock_agent

STORY_KEY = "EPMCDME-16793"
REPORT = f"Created {STORY_KEY}. The sub-tasks were not created."


def _create_call() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call_create",
                "name": "generic_jira_tool",
                "args": {"method": "POST", "relative_url": "/rest/api/2/issue", "params": {}},
            }
        ],
    )


def _create_result() -> ToolMessage:
    return ToolMessage(
        content=f'HTTP: POST /rest/api/2/issue -> 201 Created {{"key": "{STORY_KEY}"}}',
        tool_call_id="call_create",
        name="generic_jira_tool",
    )


def _truncated() -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call_subtask",
                "name": "generic_jira_tool",
                "args": {"method": "POST", "relative_url": "/rest/api/2/issue", "params": "{"},
            }
        ],
        response_metadata={"finish_reason": "length"},
    )


def _turn() -> list:
    return [HumanMessage(content="Create the Story and 3 sub-tasks"), _create_call(), _create_result()]


def _exc_with_turn() -> TokenLimitExceededException:
    truncated = _truncated()
    return TokenLimitExceededException(
        "Token limit exceeded",
        model="m",
        truncation_reason="finish_reason=length",
        turn_messages=[*_turn(), truncated],
        truncated_message=truncated,
    )


def _agent_with_report_model(content=REPORT):
    agent = create_mock_agent(llm_model="claude-router-standard")
    agent.thread_generator = Mock()
    agent.thread_context = None
    agent.thread_generator.is_closed.return_value = False
    llm = MagicMock()
    llm.bind.return_value = llm
    llm.invoke.return_value = AIMessage(content=content)
    agent._initialize_llm = Mock(return_value=llm)
    return agent, llm


class TestTruncationCheckAttachesTurnMessages:
    def test_explicit_turn_messages_are_attached(self):
        agent = create_mock_agent()
        truncated = _truncated()
        turn = [*_turn(), truncated]

        with pytest.raises(TokenLimitExceededException) as info:
            agent._check_for_truncated_response(truncated, turn_messages=turn)

        assert info.value.turn_messages == turn
        assert info.value.truncated_message is truncated

    def test_run_messages_are_attached_when_none_are_passed(self):
        agent = create_mock_agent()
        agent._run_messages = _turn()
        truncated = _truncated()

        with pytest.raises(TokenLimitExceededException) as info:
            agent._safe_check_for_truncation(truncated)

        assert info.value.turn_messages == agent._run_messages
        assert info.value.truncated_message is truncated

    def test_agent_without_run_log_attaches_nothing(self):
        agent = create_mock_agent()

        with pytest.raises(TokenLimitExceededException) as info:
            agent._check_for_truncated_response(_truncated())

        assert not info.value.turn_messages

    def test_pre_model_hook_attaches_graph_state_messages(self):
        agent = create_mock_agent()
        state_messages = [*_turn(), _truncated(), ToolMessage(content="err", tool_call_id="call_subtask")]
        hook = _build_truncation_abort_pre_model_hook(agent)

        with pytest.raises(TokenLimitExceededException) as info:
            hook({"messages": state_messages})

        assert info.value.turn_messages == state_messages


class TestRunMessageLog:
    def test_reset_seeds_input_messages(self):
        agent = create_mock_agent()
        inputs = {"messages": [HumanMessage(content="old"), HumanMessage(content="now")]}

        agent._reset_run_messages(inputs)

        assert agent._run_messages == inputs["messages"]
        assert agent._run_messages is not inputs["messages"]

    def test_records_messages_from_updates_chunks_once(self):
        agent = create_mock_agent()
        agent._reset_run_messages({"messages": [HumanMessage(content="go", id="h1")]})
        call = _create_call()
        call.id = "a1"

        agent._record_run_messages(("updates", {"agent": {"messages": [call]}}))
        agent._record_run_messages(("updates", {"agent": {"messages": [call]}}))
        agent._record_run_messages(("updates", {"tools": {"messages": [_create_result()]}}))
        agent._record_run_messages(("messages", (call, {})))

        assert [type(m).__name__ for m in agent._run_messages] == ["HumanMessage", "AIMessage", "ToolMessage"]

    def test_records_namespaced_subgraph_updates(self):
        agent = create_mock_agent()
        agent._reset_run_messages({"messages": []})

        agent._record_run_messages((("sub:1",), "updates", {"agent": {"messages": [_create_result()]}}))

        assert len(agent._run_messages) == 1

    def test_consume_records_messages_before_processing_raises(self):
        agent = create_mock_agent()
        agent._reset_run_messages({"messages": []})
        agent.process_chunk = Mock(side_effect=RuntimeError("adapter stop"))
        truncated = _truncated()

        with pytest.raises(RuntimeError):
            agent._consume_stream_chunk(("updates", {"agent": {"messages": [truncated]}}), [], "", False)

        assert agent._run_messages == [truncated]

    def test_human_messages_from_updates_are_not_logged(self):
        """Re-emitted history or a sub-agent's task message must not move the start of the user's turn."""
        agent = create_mock_agent()
        request = HumanMessage(content="Create the Story")
        agent._reset_run_messages({"messages": [request]})
        call = _create_call()

        agent._record_run_messages(("updates", {"agent": {"messages": [call, _create_result()]}}))
        agent._record_run_messages(
            (("sub:1",), "updates", {"analyst": {"messages": [HumanMessage(content="sub-agent task")]}})
        )
        agent._record_run_messages(("updates", {"supervisor": {"messages": [request, call]}}))

        assert agent._run_messages == [request, call, agent._run_messages[2]]
        assert all(not isinstance(m, HumanMessage) for m in agent._run_messages[1:])

    def test_message_given_an_id_later_is_not_duplicated(self):
        agent = create_mock_agent()
        call = _create_call()
        agent._reset_run_messages({"messages": []})
        agent._record_run_messages(("updates", {"agent": {"messages": [call]}}))
        call.id = "assigned-later"

        agent._record_run_messages(("updates", {"agent": {"messages": [call]}}))

        assert agent._run_messages == [call]

    def test_list_shaped_node_updates_are_logged(self):
        agent = create_mock_agent()
        agent._reset_run_messages({"messages": []})

        agent._record_run_messages(("updates", {"tools": [{"messages": [_create_result()]}, {"messages": []}]}))

        assert len(agent._run_messages) == 1

    def test_resume_on_a_new_instance_seeds_the_log_from_the_checkpoint(self):
        """A confirmation resume runs on a fresh agent: the paused turn comes from the checkpointed state."""
        agent = create_mock_agent()
        paused_turn = [*_turn(), _create_call()]
        agent._resolve_state = Mock(return_value=SimpleNamespace(values={"messages": paused_turn}))
        config = {"configurable": {"thread_id": "conv-1"}}

        agent._reset_run_messages(Command(resume={}), config)

        agent._resolve_state.assert_called_once_with(config, subgraphs=True)
        assert agent._run_messages == paused_turn

    def test_resume_cut_off_after_a_confirmed_create_reports_the_created_key(self):
        agent, _ = _agent_with_report_model()
        agent.extended_error = Mock(return_value="LIMIT")
        agent.tools = [_JiraLikeTool()]
        agent._resolve_state = Mock(return_value=SimpleNamespace(values={"messages": [_turn()[0], _create_call()]}))
        agent._reset_run_messages(None, {"configurable": {"thread_id": "conv-1"}})
        agent._record_run_messages(("updates", {"tools": {"messages": [_create_result()]}}))
        truncated = _truncated()
        agent._record_run_messages(("updates", {"agent": {"messages": [truncated]}}))

        with pytest.raises(TokenLimitExceededException) as info:
            agent._safe_check_for_truncation(truncated)
        message = agent._get_user_error_message(info.value)

        assert message == f"LIMIT\n\n{REPORT}"
        prompt = agent._initialize_llm.return_value.invoke.call_args.args[0][1].content
        assert f"(created/updated: {STORY_KEY})" in prompt
        assert "Create the Story and 3 sub-tasks" in prompt

    def test_resume_seeds_the_log_with_the_paused_sub_agent_calls(self):
        """A call confirmed inside a sub-agent sits in the sub-agent's checkpoint, not the top-level one."""
        agent = create_mock_agent()
        request = _turn()[0]
        sub_call = _create_call()
        paused_sub = SimpleNamespace(
            values={"messages": [HumanMessage(content="sub-agent task", id="task"), sub_call]}, tasks=()
        )
        top_state = SimpleNamespace(
            values={"messages": [request]}, tasks=(SimpleNamespace(result=None, state=paused_sub),)
        )
        agent._resolve_state = Mock(return_value=top_state)

        agent._reset_run_messages(None, {"configurable": {"thread_id": "conv-1"}})

        assert agent._run_messages == [request, sub_call]

    def test_resume_seed_keeps_the_calls_of_a_parallel_sibling_that_finished_before_the_pause(self):
        """The finished sibling's parent keeps only its last reply; its create call is in its own sub-graph state."""
        agent = create_mock_agent()
        request = _turn()[0]
        sibling_call, sibling_result = _create_call(), _create_result()
        finished_sibling = SimpleNamespace(values={"messages": [sibling_call, sibling_result]}, tasks=())
        paused = SimpleNamespace(values={"messages": [_truncated()]}, tasks=())
        top_state = SimpleNamespace(
            values={"messages": [request]},
            tasks=(
                SimpleNamespace(result={"messages": []}, state=finished_sibling),
                SimpleNamespace(result=None, state=paused),
            ),
        )
        agent._resolve_state = Mock(return_value=top_state)

        agent._reset_run_messages(None, {"configurable": {"thread_id": "conv-1"}})

        assert agent._run_messages[:3] == [request, sibling_call, sibling_result]

    def test_resume_seed_takes_a_finished_task_result_without_a_sub_graph_state(self):
        agent = create_mock_agent()
        request, call = _turn()[0], _create_call()
        top_state = SimpleNamespace(
            values={"messages": [request]}, tasks=(SimpleNamespace(result={"messages": [call]}, state=None),)
        )
        agent._resolve_state = Mock(return_value=top_state)

        agent._reset_run_messages(None, {"configurable": {"thread_id": "conv-1"}})

        assert agent._run_messages == [request, call]

    def test_resume_seed_reads_the_checkpoint_with_its_sub_graph_states(self):
        """Without subgraphs=True a task's state is a config, not a snapshot, and paused sub-agent calls are lost."""
        agent = create_mock_agent()
        sub_call = _create_call()
        sub_state = SimpleNamespace(values={"messages": [sub_call]}, tasks=())
        agent.conversation_id = "conv-1"
        agent.agent_executor = Mock()
        agent.agent_executor.get_state.return_value = SimpleNamespace(
            values={"messages": [_turn()[0]]}, tasks=(SimpleNamespace(result=None, state=sub_state),)
        )

        agent._reset_run_messages(None, {"configurable": {"thread_id": "conv-1"}})

        assert agent.agent_executor.get_state.call_args.kwargs == {"subgraphs": True}
        assert agent.agent_executor.get_state.call_args.args[0]["configurable"]["thread_id"] == "conv-1"
        assert agent._run_messages[-1] is sub_call

    def test_unreadable_sub_graph_states_still_seed_the_top_level_messages(self):
        agent = create_mock_agent()
        paused_turn = [*_turn(), _create_call()]
        agent._resolve_state = Mock(
            side_effect=[RuntimeError("sub-graph checkpoint"), SimpleNamespace(values={"messages": paused_turn})]
        )
        config = {"configurable": {"thread_id": "conv-1"}}

        agent._reset_run_messages(None, config)

        assert agent._resolve_state.call_args_list[-1].args == (config,)
        assert agent._run_messages == paused_turn

    def test_resume_seed_does_not_repeat_a_message_in_both_states(self):
        agent = create_mock_agent()
        request, call = _turn()[0], _create_call().model_copy(update={"id": "call-msg"})
        sub = SimpleNamespace(values={"messages": [call.model_copy()]}, tasks=())
        agent._resolve_state = Mock(
            return_value=SimpleNamespace(
                values={"messages": [request, call]}, tasks=(SimpleNamespace(result=None, state=sub),)
            )
        )

        agent._reset_run_messages(None, {"configurable": {"thread_id": "conv-1"}})

        assert agent._run_messages == [request, call]

    def test_unreadable_checkpoint_on_resume_starts_an_empty_log(self):
        agent = create_mock_agent()
        agent._resolve_state = Mock(side_effect=RuntimeError("no checkpointer"))

        agent._reset_run_messages(None, {"configurable": {"thread_id": "conv-1"}})

        assert agent._run_messages == []

    def test_resume_without_config_starts_an_empty_log(self):
        agent = create_mock_agent()
        agent._resolve_state = Mock()

        agent._reset_run_messages(object())

        agent._resolve_state.assert_not_called()
        assert agent._run_messages == []

    def test_explicit_empty_turn_messages_are_kept(self):
        agent = create_mock_agent()
        agent._run_messages = _turn()

        with pytest.raises(TokenLimitExceededException) as info:
            agent._check_for_truncated_response(_truncated(), turn_messages=[])

        assert info.value.turn_messages == []

    def test_composed_hooks_pass_original_messages_to_the_truncation_hook(self):
        agent = create_mock_agent()
        state_messages = [*_turn(), _truncated()]

        def image_hook(state):
            return {"llm_input_messages": [*state["messages"], HumanMessage(content="image from tool")]}

        hook = _compose_pre_model_hooks(image_hook, _build_truncation_abort_pre_model_hook(agent))

        with pytest.raises(TokenLimitExceededException) as info:
            hook({"messages": state_messages})

        assert info.value.turn_messages == state_messages

    def test_malformed_chunk_is_ignored(self):
        agent = create_mock_agent()
        agent._reset_run_messages({"messages": []})

        agent._record_run_messages(("updates", "not-a-dict"))
        agent._record_run_messages(("updates",))

        assert agent._run_messages == []


class TestReportDelivery:
    def test_streamed_final_chunk_contains_limit_message_and_report(self):
        agent, _ = _agent_with_report_model()
        agent.extended_error = Mock(return_value=global_config.AGENT_MSG_TOKEN_LIMIT)

        agent._send_error_to_thread(_exc_with_turn(), execution_start=0.0, chunks_collector=[])

        sent = json.loads(agent.thread_generator.send.call_args[0][0])
        assert sent["generated"] == f"{global_config.AGENT_MSG_TOKEN_LIMIT}\n\n{REPORT}"
        assert sent["agent_error"]["message"] == global_config.AGENT_MSG_TOKEN_LIMIT
        assert sent["agent_error"]["error_code"] == "agent_token_limit"

    def test_streamed_report_follows_partial_text(self):
        agent, _ = _agent_with_report_model()
        agent.extended_error = Mock(return_value="LIMIT")

        agent._send_error_to_thread(_exc_with_turn(), execution_start=0.0, chunks_collector=["Working on it"])

        sent = json.loads(agent.thread_generator.send.call_args[0][0])
        assert sent["generated"] == f"Working on it\n\nLIMIT\n\n{REPORT}"

    def test_report_is_added_when_exceptions_are_hidden(self, monkeypatch):
        agent, _ = _agent_with_report_model()
        monkeypatch.setattr(global_config, "HIDE_AGENT_STREAMING_EXCEPTIONS", True)

        agent._send_error_to_thread(_exc_with_turn(), execution_start=0.0, chunks_collector=[])

        sent = json.loads(agent.thread_generator.send.call_args[0][0])
        assert sent["generated"].endswith(REPORT)

    def test_non_streaming_message_contains_report(self):
        agent, _ = _agent_with_report_model()
        agent.extended_error = Mock(return_value="LIMIT")

        assert agent._get_user_error_message(_exc_with_turn()) == f"LIMIT\n\n{REPORT}"

    def test_disabled_flag_uses_code_built_report_without_model_call(self, monkeypatch):
        agent, _ = _agent_with_report_model()
        agent.extended_error = Mock(return_value="LIMIT")
        monkeypatch.setattr(global_config, "AGENT_TRUNCATION_STATUS_REPORT_ENABLED", False)

        message = agent._get_user_error_message(_exc_with_turn())

        agent._initialize_llm.assert_not_called()
        assert STORY_KEY in message
        assert "not executed" in message

    def test_limit_error_without_turn_messages_is_unchanged(self):
        agent, _ = _agent_with_report_model()
        agent.extended_error = Mock(return_value="LIMIT")

        message = agent._get_user_error_message(TokenLimitExceededException("limit"))

        assert message == "LIMIT"
        agent._initialize_llm.assert_not_called()

    def test_other_errors_get_no_report(self):
        agent, _ = _agent_with_report_model()
        agent.extended_error = Mock(return_value="boom")

        assert agent._get_user_error_message(RuntimeError("boom")) == "boom"
        agent._initialize_llm.assert_not_called()

    def test_assistant_hiding_tool_outputs_gets_created_keys_only(self):
        agent, _ = _agent_with_report_model()
        agent.extended_error = Mock(return_value="LIMIT")
        agent.assistant = SimpleNamespace(hide_tool_outputs=True, project="p", version=1)

        message = agent._get_user_error_message(_exc_with_turn())

        agent._initialize_llm.assert_not_called()
        assert STORY_KEY in message
        assert "generic_jira_tool" not in message
        assert "/rest/api" not in message

    def test_disconnected_listener_gets_no_model_call(self):
        agent, _ = _agent_with_report_model()
        agent.extended_error = Mock(return_value="LIMIT")
        agent.thread_generator.is_closed.return_value = True

        message = agent._get_user_error_message(_exc_with_turn())

        agent._initialize_llm.assert_not_called()
        assert STORY_KEY in message

    def test_report_asks_own_and_sub_agent_tools_what_they_wrote(self):
        """Sub-assistants are compiled graphs in `subagents`; their tools come from the built agents, at any depth."""
        agent, _ = _agent_with_report_model()
        own, sub, nested = (SimpleNamespace(name=n) for n in ("own_tool", "sub_tool", "nested_tool"))
        grandchild = create_mock_agent()
        grandchild.tools, grandchild.subagent_agents = [nested], []
        child = create_mock_agent()
        child.tools, child.subagent_agents = [sub], [grandchild]
        agent.tools = [own]
        agent.subagents = [object()]
        agent.subagent_agents = [child, SimpleNamespace()]

        assert agent._report_tools_by_name() == {"own_tool": own, "sub_tool": sub, "nested_tool": nested}

    def test_pinned_version_of_a_hiding_master_gets_created_keys_only(self, monkeypatch):
        """The report hides by the same fail-closed rule as the response edge: master or pinned version."""
        agent, _ = _agent_with_report_model()
        agent.extended_error = Mock(return_value="LIMIT")
        agent.assistant = SimpleNamespace(id="asst-1", hide_tool_outputs=False, project="p", version=1)
        lookups = []
        monkeypatch.setattr(
            "codemie.service.assistant.tool_output_visibility.is_hidden_by_id",
            lambda assistant_id: lookups.append(assistant_id) or True,
        )

        message = agent._get_user_error_message(_exc_with_turn())

        assert lookups == ["asst-1"]
        agent._initialize_llm.assert_not_called()
        assert "generic_jira_tool" not in message
        assert "/rest/api" not in message

    def test_hidden_sub_assistant_under_a_visible_orchestrator_gets_created_keys_only(self, monkeypatch):
        """The report draws on every sub-assistant's calls, so one that hides tool outputs hides the report."""
        monkeypatch.setattr("codemie.service.assistant.tool_output_visibility.is_hidden_by_id", lambda _id: False)
        agent, _ = _agent_with_report_model()
        agent.extended_error = Mock(return_value="LIMIT")
        agent.assistant = SimpleNamespace(id="orchestrator", hide_tool_outputs=False, project="p", version=1)
        visible, hidden = create_mock_agent(), create_mock_agent()
        visible.assistant = SimpleNamespace(id="visible", hide_tool_outputs=False)
        hidden.assistant = SimpleNamespace(id="hidden", hide_tool_outputs=True)
        visible.subagent_agents, hidden.subagent_agents = [], []
        child = create_mock_agent()
        child.assistant, child.subagent_agents = None, [hidden]
        agent.subagent_agents = [visible, child]

        message = agent._get_user_error_message(_exc_with_turn())

        agent._initialize_llm.assert_not_called()
        assert STORY_KEY in message
        assert "generic_jira_tool" not in message
        assert "/rest/api" not in message

    def test_report_inputs_that_fail_fall_back_to_keys_only(self):
        agent, _ = _agent_with_report_model()
        agent.extended_error = Mock(return_value="LIMIT")
        agent._report_tools_by_name = Mock(side_effect=RuntimeError("tool tree"))

        message = agent._get_user_error_message(_exc_with_turn())

        agent._initialize_llm.assert_not_called()
        assert message.startswith("LIMIT\n\n")
        assert "generic_jira_tool" not in message


class _JiraLikeTool:
    """Answers written_refs the way the Jira tool does for the Story create of these tests."""

    name = "generic_jira_tool"

    def written_refs(self, args, output):
        return (STORY_KEY,) if STORY_KEY in output else ()


class TestReportMergesRaisingCheckWithRunLog:
    """EPMCDME-16835 re-review AR-2: the turn is the delivering agent's run log plus what the raising check attached."""

    def test_sub_agent_cut_off_still_reports_a_key_created_in_an_earlier_delegation(self):
        agent, llm = _agent_with_report_model()
        agent.extended_error = Mock(return_value="LIMIT")
        agent.tools = [_JiraLikeTool()]
        request = HumanMessage(content="Create the Story and 3 sub-tasks", id="h1")
        # The orchestrator's run log: delegation 1 created the Story inside the sub-agent
        agent._reset_run_messages({"messages": [request]})
        agent._record_run_messages(("updates", {"agent": {"messages": [_create_call(), _create_result()]}}))
        # The sub-agent's own view at delegation 2: earlier delegations appear only as their last reply
        truncated = _truncated()
        sub_agent_view = [request, AIMessage(content="The Story is created.", id="sub-reply-1"), truncated]

        with pytest.raises(TokenLimitExceededException) as info:
            agent._safe_check_for_truncation(truncated, turn_messages=sub_agent_view)
        agent._get_user_error_message(info.value)

        prompt = llm.invoke.call_args.args[0][1].content
        assert f"(created/updated: {STORY_KEY})" in prompt
        assert "Create the Story and 3 sub-tasks" in prompt
        assert "INTERRUPTED TOOL CALL:\n- Interrupted: generic_jira_tool POST /rest/api/2/issue" in prompt

    def test_a_sub_agent_task_message_does_not_move_the_start_of_the_turn(self):
        agent, llm = _agent_with_report_model()
        agent.extended_error = Mock(return_value="LIMIT")
        agent.tools = [_JiraLikeTool()]
        agent._reset_run_messages({"messages": [HumanMessage(content="Create the Story and 3 sub-tasks", id="h1")]})
        agent._record_run_messages(("updates", {"agent": {"messages": [_create_call(), _create_result()]}}))
        truncated = _truncated()
        sub_agent_view = [HumanMessage(content="sub-agent task", id="task-1"), truncated]

        with pytest.raises(TokenLimitExceededException) as info:
            agent._safe_check_for_truncation(truncated, turn_messages=sub_agent_view)
        agent._get_user_error_message(info.value)

        prompt = llm.invoke.call_args.args[0][1].content
        assert "Create the Story and 3 sub-tasks" in prompt
        assert "sub-agent task" not in prompt
        assert f"(created/updated: {STORY_KEY})" in prompt

    def test_attached_history_before_the_request_is_not_merged(self):
        agent, llm = _agent_with_report_model()
        agent.extended_error = Mock(return_value="LIMIT")
        request = HumanMessage(content="Create the Story and 3 sub-tasks", id="h2")
        agent._reset_run_messages({"messages": [request]})
        truncated = _truncated()
        old_turn = [HumanMessage(content="old question", id="h1"), AIMessage(content="old answer", id="a1")]

        with pytest.raises(TokenLimitExceededException) as info:
            agent._safe_check_for_truncation(truncated, turn_messages=[*old_turn, request, truncated])
        agent._get_user_error_message(info.value)

        prompt = llm.invoke.call_args.args[0][1].content
        assert "old answer" not in prompt
        assert "INTERRUPTED TOOL CALL:\n- Interrupted: generic_jira_tool POST /rest/api/2/issue" in prompt
