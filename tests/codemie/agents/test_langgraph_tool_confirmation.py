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

from unittest.mock import MagicMock, patch

from opentelemetry import context as otel_context
from langgraph.checkpoint.memory import InMemorySaver
from codemie.agents.tool_confirmation.conversation_checkpoint_saver import ConversationCheckpointSaver


def _make_agent(require_tool_confirmation=False, conversation_id="conv_1"):
    """Build a minimal LangGraphAgent for run_config testing."""
    from codemie.agents.langgraph_agent import LangGraphAgent
    from codemie.core.models import AssistantChatRequest

    request = MagicMock(spec=AssistantChatRequest)
    request.conversation_id = conversation_id
    request.text = "hello"
    request.history = []
    request.file_names = []
    request.history_index = None
    request.save_history = True
    request.tools_config = None

    with patch.object(LangGraphAgent, "init_agent", return_value=MagicMock()):
        with patch.object(LangGraphAgent, "_setup_supervisor_coordinator", return_value=None, create=True):
            with patch("codemie.agents.langgraph_agent.get_otel_context_for_thread", return_value=None):
                agent = LangGraphAgent.__new__(LangGraphAgent)
                agent.agent_name = "test_agent"
                agent.description = "test"
                agent.tools = []
                agent.subagents = []
                agent.subagent_descriptions = {}
                agent.request = request
                agent.recursion_limit = 25
                agent.system_prompt = "You are helpful."
                agent.thread_generator = MagicMock()
                agent.user = MagicMock()
                agent.user.id = "user_1"
                agent.user.username = "test@test.com"
                agent.llm_model = "gpt-4"
                agent.temperature = None
                agent.top_p = None
                agent.request_uuid = "req_1"
                agent.conversation_id = conversation_id
                agent.handle_tool_error = True
                agent.throw_truncated_error = False
                agent.callbacks = []
                agent.supervisor_callbacks = []
                agent.output_schema = None
                agent.assistant = MagicMock()
                agent.assistant.version = None
                agent.override_global_checkpointer = True
                agent.trace_context = None
                agent._current_llm_run_id = None
                agent._sub_assistant_name_mapping = {}
                agent.history_compaction_pre_model_hook = None
                agent._otel_context = None
                agent.smart_tool_selection_enabled = False
                agent.tool_selection_limit = 5
                agent.verbose = False
                agent.is_react = True
                agent.stream_steps = True
                agent.tool_error_callback = MagicMock()
                agent.agent_executor = MagicMock()
                agent.require_tool_confirmation = require_tool_confirmation
                agent.tree_requires_confirmation = require_tool_confirmation
                # A streamed run with history on, as the build sets it: its pause can be resumed.
                agent.can_resume_pause = True
                return agent


def test_get_run_config_uses_in_memory_saver_when_no_confirmation():
    agent = _make_agent(require_tool_confirmation=False)
    with patch("codemie.agents.langgraph_agent.get_run_config", return_value={"recursion_limit": 25}):
        config = agent._get_run_config()
    assert isinstance(config.get("__pregel_checkpointer"), InMemorySaver)
    assert config.get("thread_id") == "thread"


def test_get_run_config_uses_conversation_saver_when_confirmation_enabled():
    agent = _make_agent(require_tool_confirmation=True, conversation_id="conv_123")
    with patch("codemie.agents.langgraph_agent.get_run_config", return_value={"recursion_limit": 25}):
        config = agent._get_run_config()
    assert isinstance(config.get("__pregel_checkpointer"), ConversationCheckpointSaver)
    assert config.get("thread_id") == "conv_123"


def test_get_run_config_uses_conversation_saver_when_only_a_child_confirms():
    """The flag the build raises from a confirming child, not the agent's own policy, selects the saver (rule 1)."""
    agent = _make_agent(require_tool_confirmation=False, conversation_id="conv_123")
    agent.tree_requires_confirmation = True
    with patch("codemie.agents.langgraph_agent.get_run_config", return_value={"recursion_limit": 25}):
        config = agent._get_run_config()
    assert isinstance(config.get("__pregel_checkpointer"), ConversationCheckpointSaver)


def test_get_run_config_conversation_saver_thread_id_matches_conversation_id():
    agent = _make_agent(require_tool_confirmation=True, conversation_id="conv_xyz")
    with patch("codemie.agents.langgraph_agent.get_run_config", return_value={"recursion_limit": 25}):
        config = agent._get_run_config()
    assert config["thread_id"] == "conv_xyz"


def test_get_run_config_uses_in_memory_saver_for_a_run_that_cannot_be_resumed():
    agent = _make_agent(require_tool_confirmation=True, conversation_id="conv_123")
    agent.can_resume_pause = False
    with patch("codemie.agents.langgraph_agent.get_run_config", return_value={"recursion_limit": 25}):
        config = agent._get_run_config()
    assert isinstance(config.get("__pregel_checkpointer"), InMemorySaver)
    assert config.get("thread_id") == "thread"


def _item(tool_call_id: str, action: str):
    from codemie.agents.tool_confirmation.models import AppliedDecision, DecisionItem, ToolCallPendingEvent

    item = DecisionItem(
        thought_id=f"thought-{tool_call_id}",
        tool_call_id=tool_call_id,
        location_key="",
        checkpoint_ns="agent:1|post_model_hook:1",
        event=ToolCallPendingEvent(pending_tool_call_id=tool_call_id, tool_name="write_tool", tool_args={}),
        auto_allowed=False,
    )
    return AppliedDecision(item=item, action=action)


def _resume_with_pending(agent, pending_ids, decisions):
    gate = MagicMock()
    with (
        patch("codemie.agents.langgraph_agent.get_run_config", return_value={"recursion_limit": 25}),
        patch("codemie.agents.tool_confirmation.tool_call_confirmation_mixin.set_llm_context"),
        patch.object(type(agent), "_pending_gate_interrupts", return_value=[(i, gate) for i in pending_ids]),
        patch.object(type(agent), "_stream_graph", return_value="done") as stream_graph,
        patch.object(type(agent), "_send_error_to_thread") as send_error,
    ):
        agent._pending_tool_confirmation = False
        agent.tool_pending_events = {}
        agent.thread_context = None
        agent._otel_context = otel_context.get_current()
        agent.resume_from_interrupt(decisions)
    return stream_graph, send_error


def test_resume_acks_every_pending_interrupt_of_the_graph_and_carries_every_decision():
    from langgraph.types import Command

    from codemie.agents.tool_confirmation.confirmation_gate import DECISIONS_CONFIG_KEY

    agent = _make_agent(require_tool_confirmation=True, conversation_id="conv_123")
    decisions = [_item("call_a", "allow"), _item("call_b", "deny")]

    stream_graph, send_error = _resume_with_pending(agent, ["i1", "i2"], decisions)

    send_error.assert_not_called()
    resume, run_config = stream_graph.call_args.args[:2]
    assert isinstance(resume, Command)
    assert resume.resume == {"i1": "ack", "i2": "ack"}
    assert run_config["configurable"][DECISIONS_CONFIG_KEY] == {
        "/call_a": {"action": "allow", "ns": "agent:1|post_model_hook:1"},
        "/call_b": {"action": "deny", "ns": "agent:1|post_model_hook:1"},
    }
    assert isinstance(run_config["__pregel_checkpointer"], ConversationCheckpointSaver)
    assert set(agent.tool_pending_events) == set()  # cleared after the stream


def test_stream_graph_sets_no_static_breakpoint():
    agent = _make_agent(require_tool_confirmation=True)
    agent._last_subassistant_answer = ""
    agent._event_adapter = MagicMock()
    agent.agent_executor.stream.return_value = iter(())
    with (
        patch.object(type(agent), "_finalize_stream_result"),
        patch.object(type(agent), "ask_for_tool_confirmation", return_value=False) as ask,
    ):
        agent._stream_graph({"messages": []}, {"thread_id": "conv_1"})

    assert "interrupt_before" not in agent.agent_executor.stream.call_args.kwargs
    ask.assert_called_once_with({"thread_id": "conv_1"})
