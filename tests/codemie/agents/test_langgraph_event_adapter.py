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
        # The agent's post_model_hook output starts the deferred steps.
        adapter.parse_update_type({"post_model_hook": None})

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
        adapter.parse_supervisor_update_type({"post_model_hook": None}, author="supervisor")

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
        # The agent's post_model_hook output starts the deferred steps.
        adapter.parse_update_type({"post_model_hook": None})

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
        # The agent's post_model_hook output starts the deferred steps.
        adapter.parse_update_type({"post_model_hook": None})

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
        # The agent's post_model_hook output starts the deferred steps.
        adapter.parse_update_type({"post_model_hook": None})

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

        agent._callback_bridge.on_thought_failed.assert_called_once_with("msg-8", author=None)
        agent._on_llm_end.assert_called_once_with(response=message, run_id="msg-8", author=None)
        agent._safe_check_for_truncation.assert_called_once_with(message, author=None)
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


class TestTerminalMessageTruncationDetection:
    """EPMCDME-14145: terminal (non-tool-call) messages must also run the truncation detector."""

    @pytest.fixture
    def mock_agent(self):
        agent = MagicMock()
        agent.is_valid_ai_message.return_value = False
        agent.is_finish_reason_stop.return_value = False
        agent.is_finish_reason_truncated.return_value = False
        agent._detect_terminal_truncation = MagicMock()
        agent._on_llm_end = MagicMock()
        return agent

    def test_parse_message_type_detects_openai_truncated_terminal_message(self, mock_agent):
        mock_agent.is_finish_reason_truncated.return_value = True
        adapter = LangGraphEventAdapter(mock_agent)
        message = AIMessage(content="", response_metadata={"finish_reason": "length"})

        adapter.parse_message_type((message, {"langgraph_node": "agent"}), [])

        mock_agent._detect_terminal_truncation.assert_called_once_with(message)
        mock_agent._on_llm_end.assert_called_once_with(response=message, run_id=message.id)

    def test_parse_message_type_detects_claude_truncated_terminal_message(self, mock_agent):
        mock_agent.is_finish_reason_truncated.return_value = True
        adapter = LangGraphEventAdapter(mock_agent)
        message = AIMessage(content="", response_metadata={"stop_reason": "max_tokens"})

        adapter.parse_message_type((message, {"langgraph_node": "agent"}), [])

        mock_agent._detect_terminal_truncation.assert_called_once_with(message)

    def test_parse_message_type_detects_bedrock_truncated_terminal_message(self, mock_agent):
        mock_agent.is_finish_reason_truncated.return_value = True
        adapter = LangGraphEventAdapter(mock_agent)
        message = AIMessage(content="", response_metadata={"stopReason": "max_tokens"})

        adapter.parse_message_type((message, {"langgraph_node": "agent"}), [])

        mock_agent._detect_terminal_truncation.assert_called_once_with(message)

    def test_parse_message_type_normal_completion_still_invokes_detector(self, mock_agent):
        mock_agent.is_finish_reason_stop.return_value = True
        adapter = LangGraphEventAdapter(mock_agent)
        message = AIMessage(content="complete answer", response_metadata={"finish_reason": "stop"})

        adapter.parse_message_type((message, {"langgraph_node": "agent"}), [])

        mock_agent._detect_terminal_truncation.assert_called_once_with(message)
        mock_agent._on_llm_end.assert_called_once_with(response=message, run_id=message.id)

    def test_parse_message_type_skips_when_neither_condition_true(self, mock_agent):
        adapter = LangGraphEventAdapter(mock_agent)
        message = AIMessage(content="", response_metadata={})

        adapter.parse_message_type((message, {"langgraph_node": "agent"}), [])

        mock_agent._detect_terminal_truncation.assert_not_called()
        mock_agent._on_llm_end.assert_not_called()

    def test_parse_message_type_ignores_non_agent_node_terminal_message(self, mock_agent):
        """A structured-output stop from a later node must not reset the agent's truncation flag."""
        mock_agent.is_finish_reason_stop.return_value = True
        adapter = LangGraphEventAdapter(mock_agent)
        message = AIMessage(content="{}", response_metadata={"finish_reason": "stop"})

        adapter.parse_message_type((message, {"langgraph_node": "generate_structured_response"}), [])

        mock_agent._detect_terminal_truncation.assert_not_called()
        mock_agent._on_llm_end.assert_not_called()

    def test_parse_supervisor_message_type_detects_openai_truncated_terminal_message(self, mock_agent):
        mock_agent.is_finish_reason_truncated.return_value = True
        adapter = LangGraphEventAdapter(mock_agent)
        message = AIMessage(content="", response_metadata={"finish_reason": "length"})

        adapter.parse_supervisor_message_type((message, {"langgraph_node": "agent"}), [])

        mock_agent._detect_terminal_truncation.assert_called_once_with(message)
        mock_agent._on_llm_end.assert_called_once_with(response=message, run_id=message.id, author=None)

    def test_parse_supervisor_message_type_detects_claude_truncated_terminal_message(self, mock_agent):
        mock_agent.is_finish_reason_truncated.return_value = True
        adapter = LangGraphEventAdapter(mock_agent)
        message = AIMessage(content="", response_metadata={"stop_reason": "max_tokens"})

        adapter.parse_supervisor_message_type((message, {"langgraph_node": "agent"}), [])

        mock_agent._detect_terminal_truncation.assert_called_once_with(message)

    def test_parse_supervisor_message_type_normal_completion_still_invokes_detector(self, mock_agent):
        mock_agent.is_finish_reason_stop.return_value = True
        adapter = LangGraphEventAdapter(mock_agent)
        message = AIMessage(content="complete answer", response_metadata={"finish_reason": "stop"})

        adapter.parse_supervisor_message_type((message, {"langgraph_node": "agent"}), [])

        mock_agent._detect_terminal_truncation.assert_called_once_with(message)
        mock_agent._on_llm_end.assert_called_once_with(response=message, run_id=message.id, author=None)

    def test_parse_supervisor_message_type_ignores_structured_response_terminal_message(self, mock_agent):
        """The supervisor's structured-output stop must not reset a truncated answer's flag."""
        mock_agent.is_finish_reason_stop.return_value = True
        adapter = LangGraphEventAdapter(mock_agent)
        message = AIMessage(content="{}", response_metadata={"finish_reason": "stop"})

        adapter.parse_supervisor_message_type((message, {"langgraph_node": "generate_structured_response"}), [])

        mock_agent._detect_terminal_truncation.assert_not_called()
        mock_agent._on_llm_end.assert_called_once_with(response=message, run_id=message.id, author=None)


# --- EPMCDME-16463: call identity in the stream and container placement ----------------------------

from langchain_core.language_models import BaseChatModel  # noqa: E402
from langchain_core.messages import ToolMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from langchain_core.tools import BaseTool  # noqa: E402
from unittest.mock import Mock  # noqa: E402

from codemie.agents.langgraph_agent import LangGraphAgent  # noqa: E402
from codemie.agents.tool_confirmation.call_identity import assign_call_identities  # noqa: E402
from codemie.core.models import ToolCallPolicy  # noqa: E402
from codemie.core.thread import ThreadedGenerator  # noqa: E402
from tests.codemie.agents.tool_confirmation.support import FakeCheckpointService, patched_storage  # noqa: E402

_TOOL_RUNS: dict[str, int] = {}


class _CountedTool(BaseTool):
    safe: bool = False

    def is_safe(self, args: dict) -> bool:
        return self.safe

    def _run(self, q: str = "") -> str:
        _TOOL_RUNS[self.name] = _TOOL_RUNS.get(self.name, 0) + 1
        return f"{self.name} EXECUTED {_TOOL_RUNS[self.name]}"


def _counted_tools() -> list[BaseTool]:
    return [
        _CountedTool(name="read_tool", description="read", safe=True),
        _CountedTool(name="write_tool", description="write"),
    ]


class _RoundsModel(BaseChatModel):
    """Answers round ``n`` with ``rounds[n]`` tool calls; past the last round it answers with text."""

    role: str
    rounds: list[list[tuple[str, str]]]
    step: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted-rounds"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        index = self.step
        self.step += 1
        if index < len(self.rounds):
            calls = [
                {"name": name, "args": {"task": "do it"} if name.startswith("transfer_to_") else {"q": "1"}, "id": id_}
                for name, id_ in self.rounds[index]
            ]
            message = AIMessage(content="", tool_calls=calls)
        else:
            message = AIMessage(content=f"{self.role} done")
        return ChatResult(generations=[ChatGeneration(message=message)])


def _identity_request() -> Mock:
    request = Mock()
    request.conversation_id = "conv-identity"
    request.text = "hi"
    request.history_index = 0
    request.save_history = True
    request.history = []
    request.system_prompt = None
    request.file_names = None
    request.metadata = {}
    request.tools_config = None
    # What a pause stores from the request.
    request.version = None
    request.sub_assistants_versions = None
    request.skill_ids = None
    request.llm_model = None
    request.tool_call_policy = None
    return request


def _identity_user() -> Mock:
    user = Mock()
    user.id = "user-1"
    user.name = "Test User"
    user.username = "tester"
    return user


def _identity_assistant() -> Mock:
    assistant = Mock()
    assistant.id = "asst-root"
    assistant.project = "test"
    assistant.version = None
    assistant.tools_tokens_size_limit = None
    assistant.is_global = False
    return assistant


def _identity_agent(name: str, model: BaseChatModel, generator, **kwargs) -> LangGraphAgent:
    with patch.object(LangGraphAgent, "_initialize_llm", return_value=model):
        return LangGraphAgent(
            agent_name=name,
            description=f"agent {name}",
            tools=_counted_tools(),
            request=_identity_request(),
            system_prompt="You are an agent.",
            request_uuid="req-1",
            user=_identity_user(),
            llm_model="gpt-4.1",
            assistant=_identity_assistant(),
            thread_generator=generator,
            # A streamed run with history on: its gates pause.
            can_resume_pause=True,
            **kwargs,
        )


def _streamed_thoughts(generator: ThreadedGenerator) -> list[dict]:
    thoughts = []
    while not generator.queue.empty():
        item = generator.queue.get_nowait()
        if isinstance(item, str) and (thought := json.loads(item).get("thought")):
            thoughts.append(thought)
    return thoughts


def _step_id(identity: str) -> str:
    return str(LangGraphAgent._tool_call_id_to_uuid(identity))


@pytest.fixture
def _reset_tool_runs():
    _TOOL_RUNS.clear()


def _rewritten(raw: str) -> str:
    """The identity the gate gives ``raw`` when an earlier call of the agent already answered ``raw``."""
    ai_message = AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": raw, "type": "tool_call"}])
    return assign_call_identities([ToolMessage(content="", tool_call_id=raw)], ai_message, "")[0].wire_id


def test_two_delegation_rounds_with_one_raw_id_get_distinct_step_ids(_reset_tool_runs):
    """An endpoint numbering every call ``call_0``: round 2's handoff is a new step, its sub-agent sits under it."""
    generator = ThreadedGenerator(conversation_id="conv-identity")
    subs = [
        _identity_agent(name, _RoundsModel(role=name, rounds=[]), generator, is_subagent=True) for name in ("s1", "s2")
    ]
    model = _RoundsModel(role="orchestrator", rounds=[[("transfer_to_s1", "call_0")], [("transfer_to_s2", "call_0")]])
    orchestrator = _identity_agent(
        "Orchestrator",
        model,
        generator,
        subagents=[sub.agent_executor for sub in subs],
        subagent_descriptions={"s1": "first", "s2": "second"},
        tool_call_policy=ToolCallPolicy.AUTO_APPROVE,
    )

    orchestrator._agent_streaming([])

    first, second = _step_id("call_0"), _step_id(_rewritten("call_0"))
    assert first != second
    thoughts = _streamed_thoughts(generator)
    assert {first, second} <= {t["id"] for t in thoughts}
    assert _starts(thoughts, first) == _starts(thoughts, second) == 1
    s2_steps = [t for t in thoughts if "s2 done" in str(t.get("message")) and t["id"] != second]
    assert s2_steps and {t.get("parent_id") for t in s2_steps} == {second}


def _starts(thoughts: list[dict], step_id: str) -> int:
    """How many times the step was started (a step is started once and then only updated)."""
    first_seen = [t for t in thoughts if t["id"] == step_id]
    return 1 if first_seen and first_seen[0].get("in_progress") else 0


def _started_once_and_finished(thoughts: list[dict], step_id: str) -> dict:
    """The step's thoughts: started (in progress) before anything else, finished last, with its result."""
    own = [t for t in thoughts if t["id"] == step_id]
    assert own, f"step {step_id} is streamed"
    assert own[0].get("in_progress"), f"step {step_id} starts before its result"
    assert not own[-1].get("in_progress"), f"step {step_id} gets its result"
    return own[-1]


def _single_agent(rounds, **kwargs) -> LangGraphAgent:
    generator = ThreadedGenerator(conversation_id="conv-identity")
    return _identity_agent("Root", _RoundsModel(role="root", rounds=rounds), generator, **kwargs)


# AC27 shape: a call id already answered earlier in the agent's state (alone, and as a supervisor's own call).
@pytest.mark.parametrize("supervisor", [False, True], ids=["single", "supervisor"])
def test_a_call_id_answered_earlier_gets_its_own_step_and_result(_reset_tool_runs, supervisor):
    rounds = [[("read_tool", "call_0")], [("read_tool", "call_0")]]
    if supervisor:
        generator = ThreadedGenerator(conversation_id="conv-identity")
        agent = _orchestrator_over({"s1": []}, rounds, generator)
    else:
        agent = _single_agent(rounds, tool_call_policy=ToolCallPolicy.AUTO_APPROVE)

    agent._agent_streaming([])

    thoughts = _streamed_thoughts(agent.thread_generator)
    first = _started_once_and_finished(thoughts, _step_id("call_0"))
    second = _started_once_and_finished(thoughts, _step_id(_rewritten("call_0")))
    assert "EXECUTED 1" in str(first["message"])
    assert "EXECUTED 2" in str(second["message"])
    assert _TOOL_RUNS == {"read_tool": 2}


# AC27 shape: two calls with one id in one AI message.
def test_two_calls_with_one_id_in_one_message_get_two_steps(_reset_tool_runs):
    agent = _single_agent(
        [[("read_tool", "dup"), ("write_tool", "dup")]],
        tool_call_policy=ToolCallPolicy.AUTO_APPROVE,
    )

    agent._agent_streaming([])

    message = AIMessage(
        content="",
        tool_calls=[{"name": n, "args": {}, "id": "dup", "type": "tool_call"} for n in ("read_tool", "write_tool")],
    )
    read_id, write_id = (identity.wire_id for identity in assign_call_identities([], message, ""))
    thoughts = _streamed_thoughts(agent.thread_generator)
    assert "read_tool EXECUTED" in str(_started_once_and_finished(thoughts, _step_id(read_id))["message"])
    assert "write_tool EXECUTED" in str(_started_once_and_finished(thoughts, _step_id(write_id))["message"])
    assert _TOOL_RUNS == {"read_tool": 1, "write_tool": 1}


def _orchestrator_over(sub_rounds: dict[str, list], rounds, generator, **kwargs) -> LangGraphAgent:
    subs = [
        _identity_agent(name, _RoundsModel(role=name, rounds=sub_round), generator, is_subagent=True)
        for name, sub_round in sub_rounds.items()
    ]
    return _identity_agent(
        "Orchestrator",
        _RoundsModel(role="orchestrator", rounds=rounds),
        generator,
        subagents=[sub.agent_executor for sub in subs],
        subagent_descriptions={name: name for name in sub_rounds},
        tool_call_policy=ToolCallPolicy.AUTO_APPROVE,
        **kwargs,
    )


# AC27 shape: two siblings whose calls share an id.
def test_sibling_calls_sharing_an_id_get_distinct_steps_under_their_containers(_reset_tool_runs):
    generator = ThreadedGenerator(conversation_id="conv-identity")
    orchestrator = _orchestrator_over(
        {"s1": [[("write_tool", "call_0")]], "s2": [[("read_tool", "call_0")]]},
        [[("transfer_to_s1", "o_s1"), ("transfer_to_s2", "o_s2")]],
        generator,
    )

    orchestrator._agent_streaming([])

    thoughts = _streamed_thoughts(generator)
    steps = {}
    for tool_name in ("write_tool", "read_tool"):
        ids = {t["id"] for t in thoughts if f"{tool_name} EXECUTED" in str(t.get("message"))}
        assert len(ids) == 1, f"one step carries the {tool_name} result"
        steps[tool_name] = _started_once_and_finished(thoughts, ids.pop())
    assert steps["write_tool"]["id"] != steps["read_tool"]["id"]
    assert steps["write_tool"]["parent_id"] == _step_id("o_s1")
    assert steps["read_tool"]["parent_id"] == _step_id("o_s2")
    assert _TOOL_RUNS == {"write_tool": 1, "read_tool": 1}


# Rule 1 (one graph shape), AC11: a tree that does not confirm has the hook at every level, its call runs once and
# the root's step keeps the model's raw id.
@pytest.mark.parametrize("supervisor", [False, True], ids=["single", "supervisor"])
def test_a_non_confirming_agent_has_the_hook_runs_its_call_once_and_keeps_the_raw_id(_reset_tool_runs, supervisor):
    rounds = [[("write_tool", "call_0")]]
    if supervisor:
        agent = _orchestrator_over({"s1": []}, rounds, ThreadedGenerator(conversation_id="conv-identity"))
    else:
        agent = _single_agent(rounds, tool_call_policy=ToolCallPolicy.AUTO_APPROVE)

    with patch.object(LangGraphAgent, "ask_for_tool_confirmation") as ask_for_tool_confirmation:
        agent._agent_streaming([])

    assert agent.tree_requires_confirmation is False
    ask_for_tool_confirmation.assert_not_called()
    # A supervisor's agent node is a subgraph: the hook is one of its inner nodes.
    assert any(node.endswith("post_model_hook") for node in agent.agent_executor.get_graph(xray=1).nodes)
    step = _started_once_and_finished(_streamed_thoughts(agent.thread_generator), _step_id("call_0"))
    assert "write_tool EXECUTED 1" in str(step["message"])
    assert _TOOL_RUNS == {"write_tool": 1}


# --- pausing passes ---------------------------------------------------------------------------------


def test_pausing_pass_starts_the_rewritten_call_on_the_waiting_step_id(_reset_tool_runs):
    """A paused call whose id was answered earlier: its step, its waiting step and its item share one identity."""
    service = FakeCheckpointService()
    agent = _single_agent(
        [[("read_tool", "call_0")], [("write_tool", "call_0")]],
        tool_call_policy=ToolCallPolicy.APPROVE_FOR_ME,
        require_tool_confirmation=True,
    )

    with patched_storage(service):
        agent._agent_streaming([])

    step_id = _step_id(_rewritten("call_0"))
    thoughts = _streamed_thoughts(agent.thread_generator)
    own = [t for t in thoughts if t["id"] == step_id]
    assert own and own[0].get("in_progress") and not own[0].get("interrupted"), "the step starts first"
    assert own[-1].get("interrupted"), "then waits on the same id"
    assert [item.thought_id for item in service.record("conv-identity").items] == [step_id]
    assert _TOOL_RUNS == {"read_tool": 1}


def _paused_sub_agent(name: str, tool_name: str):
    """A sub-agent graph whose gate pauses its own calls (Task 10 builds these through ``LangGraphAgent``)."""
    from langgraph.prebuilt import create_react_agent

    from codemie.agents.tool_confirmation.confirmation_gate import build_confirmation_hook

    tools = _counted_tools()
    gate = build_confirmation_hook(
        ToolCallPolicy.ASK_FOR_APPROVAL,
        tools,
        is_subagent=True,
        pauses_own_calls=True,
        resumable=True,
    )
    model = _RoundsModel(role=name, rounds=[[(tool_name, "call_0")]])
    return create_react_agent(model, tools, name=name, post_model_hook=gate)


def test_nested_pausing_pass_starts_the_step_from_the_gate_identity_under_its_container(_reset_tool_runs):
    """Nested chunk order with ``subgraphs=True``: S's step starts, under O -> S, before the run stops."""
    from codemie.agents.tool_confirmation.call_identity import CallIdentity

    generator = ThreadedGenerator(conversation_id="conv-identity")
    orchestrator = _identity_agent(
        "Orchestrator",
        _RoundsModel(role="orchestrator", rounds=[[("transfer_to_s1", "o_s1")]]),
        generator,
        subagents=[_paused_sub_agent("s1", "write_tool")],
        subagent_descriptions={"s1": "s1"},
        tool_call_policy=ToolCallPolicy.AUTO_APPROVE,
        # Its sub-assistant pauses its own calls: the build raises the flag from that child.
        tree_requires_confirmation=True,
    )
    chunks = []
    stream = orchestrator.agent_executor.stream

    def recording_stream(*args, **kwargs):
        for chunk in stream(*args, **kwargs):
            chunks.append(chunk)
            yield chunk

    with (
        patched_storage(FakeCheckpointService()),
        patch.object(orchestrator.agent_executor, "stream", side_effect=recording_stream),
    ):
        orchestrator._agent_streaming([])

    interrupts = [c[2]["__interrupt__"] for c in chunks if c[1] == "updates" and "__interrupt__" in c[2]]
    assert interrupts and chunks[-1][1] == "updates" and "__interrupt__" in chunks[-1][2]
    call = interrupts[0][0].value["calls"][0]
    step_id = _step_id(CallIdentity(wire_id=call["tool_call_id"], location_key=call["location_key"]).step_key)
    assert call["location_key"], "the nested gate has a location"
    thoughts = _streamed_thoughts(generator)
    own = [t for t in thoughts if t["id"] == step_id]
    assert own and own[0].get("in_progress") and own[0]["parent_id"] == _step_id("o_s1")
    assert interrupts[0][0].value["calls"][0]["event"]["location"]["parent_thought_id"] == _step_id("o_s1")
    assert _TOOL_RUNS == {}
