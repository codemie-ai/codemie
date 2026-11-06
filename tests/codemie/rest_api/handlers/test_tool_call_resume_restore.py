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

"""The resume branch of ``_handle_stream`` (rule 5.4, 5.5, AC10, AC22), the stored path and the stored payload.

The resumed turn is driven through ``StandardAssistantHandler._handle_stream(..., resume=pending)`` with a consumed
row. The history save runs for real over an in-memory conversation; only its database write, the metrics and the
build are replaced. ``AssistantService.build_agent`` is a builder that records what it was given and returns a
small agent tree with the attributes the path check reads.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from codemie.agents.tool_confirmation.models import (
    DecisionItem,
    PendingCallLocation,
    StoredPendingToolCall,
    ToolCallPendingEvent,
    applied_decisions,
)
from codemie.agents.tool_confirmation.resume_payload import build_resume_payload
from codemie.chains.base import Thought
from codemie.core import tool_step_states
from codemie.core.constants import ChatRole
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import AssistantChatRequest, ToolCallPolicy
from codemie.rest_api.handlers.assistant_handlers import AssistantRequestHandler, StandardAssistantHandler
from codemie.rest_api.handlers.tool_call_resume import ConsumedPendingToolCall
from codemie.rest_api.models.assistant import ToolCallResumeRequest
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage
from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie.service.assistant.assistant_engine_builder import (
    BuildChannel,
)
from codemie.service.assistant_service import AssistantService
from codemie.service.conversation_service import ConversationService
from codemie.service.generation_manager import GenerationManager

CONV_ID = "conv_1"
ROOT_ID = "asst_o"
S_ID = "asst_s"
CONTAINER_ID = "step_o_to_s"
ALLOWED_ID = "thought_allow"
DENIED_ID = "thought_deny"
AUTO_ID = "thought_auto"
CARRIED_ID = "step_carried"
WAITING_MESSAGE = "Waiting for your confirmation"
_HANDLERS = "codemie.rest_api.handlers.assistant_handlers"
_ASK = "codemie.rest_api.routers.assistant._ask"


# --- the rebuilt agent tree ----------------------------------------------------------------------------------


def _tool(name: str):
    return SimpleNamespace(name=name)


def _agent(name, tools=(), children=(), policy=ToolCallPolicy.ASK_FOR_APPROVAL, assistant=None):
    """An agent with only what the payload and the path check read."""
    return SimpleNamespace(
        agent_name=name,
        format_assistant_name=lambda value: value.replace(" ", "_"),
        tools=[_tool(t) for t in tools],
        subagents=[],
        tool_call_policy=policy,
        assistant=assistant or SimpleNamespace(hide_tool_outputs=False),
        _pending_tool_confirmation=False,
    )


def _s_agent(tools=("write_file",)):
    return _agent("S", tools=tools)


def _item(
    nested: bool = True,
    tool_name: str = "write_file",
    thought_id: str = "thought_0",
    auto_allowed: bool = False,
) -> DecisionItem:
    location = PendingCallLocation(parent_thought_id=CONTAINER_ID) if nested else None
    call_id = f"call_{thought_id}"
    return DecisionItem(
        thought_id=thought_id,
        tool_call_id=call_id,
        location_key=S_ID if nested else "",
        checkpoint_ns="s:1|post_model_hook:1" if nested else "agent:1|post_model_hook:1",
        auto_allowed=auto_allowed,
        event=ToolCallPendingEvent(
            pending_tool_call_id=call_id,
            tool_name=tool_name,
            tool_args={"path": "f0"},
            history_index=0,
            original_user_message="please write",
            all_tool_call_ids=[call_id],
            location=location,
        ),
    )


def _payload(**overrides) -> dict:
    payload = {
        "carried_ids": [],
        "root_version": None,
        "sub_assistants_versions": None,
        "skill_ids": None,
        "llm_model": None,
        "request_tool_call_policy": None,
        "file_names": [],
    }
    payload.update(overrides)
    return payload


# --- the paused turn: its row and its history entry ----------------------------------------------------------


def _waiting_step(thought_id: str) -> dict:
    return tool_step_states.waiting(
        {
            "id": thought_id,
            "parent_id": CONTAINER_ID,
            "author_name": "Write File",
            "input_text": '{"path": "f0"}',
            "message": WAITING_MESSAGE,
            "in_progress": False,
            "children": [],
        }
    )


def _snapshot(*extra: dict) -> list[dict]:
    """The generator's thoughts at the pause: a delegation container holding the waiting steps."""
    container = {
        "id": CONTAINER_ID,
        "author_name": "S",
        "author_type": "Agent",
        "input_text": "write it",
        "message": "delegating to S",
        "in_progress": True,
        "children": [_waiting_step(ALLOWED_ID), _waiting_step(DENIED_ID), *extra],
    }
    return [container]


def _row(thoughts=None, items=None, history_index=0, **payload_overrides) -> StoredPendingToolCall:
    return StoredPendingToolCall(
        items=items or [_item(thought_id=ALLOWED_ID), _item(thought_id=DENIED_ID)],
        root_assistant_id=ROOT_ID,
        history_index=history_index,
        original_user_message="please write",
        thoughts=thoughts if thoughts is not None else _snapshot(),
        payload=_payload(**payload_overrides),
    )


def _consumed(row: StoredPendingToolCall, requested=None) -> ConsumedPendingToolCall:
    requested = requested if requested is not None else {ALLOWED_ID: "allow", DENIED_ID: "deny"}
    return ConsumedPendingToolCall(record=row, decisions=applied_decisions(row.items, requested))


def _paused_conversation(row: StoredPendingToolCall) -> Conversation:
    """The conversation as the pause saved it: one entry at the row's history index, its steps waiting."""
    conversation = Conversation(
        id=CONV_ID, conversation_id=CONV_ID, user_id="user_1", assistant_ids=[ROOT_ID], initial_assistant_id=ROOT_ID
    )
    conversation.history = [
        GeneratedMessage(role=ChatRole.USER, message="please write", history_index=row.history_index),
        GeneratedMessage(
            role=ChatRole.ASSISTANT,
            message="",
            history_index=row.history_index,
            assistant_id=ROOT_ID,
            thoughts=[Thought(**step) for step in deepcopy(row.thoughts)],
            in_progress=False,
            status="success",
        ),
    ]
    return conversation


def _flat(thoughts) -> list:
    flat = []
    for thought in thoughts or []:
        flat.append(thought)
        children = thought.get("children") if isinstance(thought, dict) else thought.children
        flat.extend(_flat(children))
    return flat


def _by_id(thoughts) -> dict:
    return {(t["id"] if isinstance(t, dict) else t.id): t for t in _flat(thoughts)}


def _entry(conversation: Conversation, history_index: int) -> list[GeneratedMessage]:
    return [m for m in conversation.history if m.history_index == history_index and m.role == ChatRole.ASSISTANT]


# --- the turn harness ----------------------------------------------------------------------------------------


class Builds:
    """``AssistantService.build_agent``: records each build; the agent's run closes the generator it was built on."""

    def __init__(self, tree):
        self.tree = tree
        self.calls: list[dict] = []
        self.resumes: list[dict] = []
        self.streams = 0

    def build_agent(self, **kwargs):
        self.calls.append(kwargs)
        agent = self.tree
        generator = kwargs["thread_generator"]

        def resume_from_interrupt(applied, **_stream_options):
            self.resumes.append(list(applied))
            generator.close()

        def stream(**_stream_options):
            self.streams += 1
            generator.close()

        agent.resume_from_interrupt = resume_from_interrupt
        agent.stream = stream
        return agent


def _assistant(llm_model_type: str):
    assistant = MagicMock()
    assistant.id = ROOT_ID
    assistant.name = "O"
    assistant.project = "demo"
    assistant.is_global = False
    assistant.hide_tool_outputs = False
    assistant.skill_ids = []
    assistant.llm_model_type = llm_model_type
    assistant.version = None
    return assistant


@pytest.fixture
def master():
    return _assistant("gpt-4.1")


@pytest.fixture
def pinned():
    return _assistant("gpt-5")


class Turn(SimpleNamespace):
    """What one driven turn left: the handler, the builds, the saves and the metrics."""


@contextmanager
def _turn(master, conversation, tree, versioned=None):
    builds = Builds(tree)
    saves: list[dict] = []
    real_upsert = ConversationService.upsert_chat_history.__func__

    def upsert(cls, params=None, **kwargs):
        saves.append({"assistant": params.assistant, "in_progress": params.in_progress, "status": params.status})
        return real_upsert(cls, params, **kwargs)

    completion = MagicMock()
    llm_context = MagicMock()
    with (
        patch.object(ConversationService, "_find_or_create_conversation", return_value=(conversation, False, False)),
        patch("codemie.service.conversation_service._guard_finished"),
        patch.object(ConversationService, "upsert_chat_history", classmethod(upsert)),
        patch.object(ConversationService, "_handle_turn_completion", completion),
        patch.object(Conversation, "update"),
        patch.object(AgentWorkspaceService, "sync_uploaded_files"),
        patch("codemie.service.llm_service.utils.set_llm_context", llm_context),
        patch.object(AssistantRequestHandler, "_is_conversation_replay_v2_enabled", return_value=True),
        patch.object(AssistantService, "build_agent", side_effect=builds.build_agent),
        patch(f"{_HANDLERS}.extract_custom_headers", return_value={}),
        patch(f"{_HANDLERS}.run_assistant_in_thread_pool", side_effect=lambda fn, *args: fn(*args)),
        patch(
            f"{_ASK}.AssistantVersionService.apply_version_to_assistant",
            side_effect=versioned or (lambda assistant, version: assistant),
        ),
    ):
        handler = StandardAssistantHandler(master, User(id="user_1", username="tester", name="Tester"), "uuid_1")
        raw_request = MagicMock()
        raw_request.state.uuid = "uuid_1"
        yield Turn(
            handler=handler,
            raw_request=raw_request,
            builds=builds,
            saves=saves,
            completion=completion,
            llm_context=llm_context,
        )


def _resume(turn: Turn, pending: ConsumedPendingToolCall):
    request = ToolCallResumeRequest(conversation_id=CONV_ID, action="allow")
    return turn.handler._handle_stream(request, turn.raw_request, 0.0, datetime.now(timezone.utc), resume=pending)


def _drain(response) -> str:
    async def collect():
        return [chunk if isinstance(chunk, str) else chunk.decode() async for chunk in response.body_iterator]

    return "".join(asyncio.run(collect()))


def _tree():
    return _agent("O", children=[_s_agent()])


# --- the resume branch of _handle_stream ---------------------------------------------------------------------


def test_before_the_response_returns_the_entry_holds_one_in_progress_variant_with_the_seed(master, pinned):
    row = _row(root_version=3)
    conversation = _paused_conversation(row)
    with _turn(master, conversation, _tree(), versioned=lambda a, v: pinned) as turn:
        _resume(turn, _consumed(row))
        generator = GenerationManager().get_generator(CONV_ID)
        assert generator is turn.builds.calls[0]["thread_generator"], "the resumed turn is registered"
        GenerationManager().unregister(CONV_ID, generator)

    entry = _entry(conversation, 0)
    assert len(entry) == 1, "the placeholder replaces the paused variant"
    assert len([m for m in conversation.history if m.history_index == 0 and m.role == ChatRole.USER]) == 1
    assert entry[0].in_progress is True
    steps = _by_id(entry[0].thoughts)
    assert steps[ALLOWED_ID].interrupted is False
    assert steps[ALLOWED_ID].in_progress is True
    assert steps[ALLOWED_ID].message == ""
    assert steps[DENIED_ID].aborted is True
    assert steps[DENIED_ID].interrupted is False
    assert steps[DENIED_ID].message == tool_step_states.DENIED_MESSAGE
    container = steps[CONTAINER_ID]
    assert (container.message, container.in_progress, container.input_text) == ("delegating to S", True, "write it")
    assert not any(step.interrupted for step in steps.values())


def test_the_seed_is_set_into_the_generator_and_only_allowed_and_carried_steps_are_replaced(master, pinned):
    carried = {**_waiting_step(CARRIED_ID), "interrupted": False, "message": "still running"}
    row = _row(
        thoughts=_snapshot(carried),
        items=[_item(thought_id=ALLOWED_ID), _item(thought_id=DENIED_ID), _item(thought_id=AUTO_ID, auto_allowed=True)],
        carried_ids=[CARRIED_ID],
    )
    with _turn(master, _paused_conversation(row), _tree()) as turn:
        _resume(turn, _consumed(row))
        generator = turn.builds.calls[0]["thread_generator"]
        GenerationManager().unregister(CONV_ID, generator)

    steps = _by_id(generator.thoughts)
    assert tool_step_states.allowed(_waiting_step(ALLOWED_ID)) == {**steps[ALLOWED_ID], "children": []}
    assert steps[DENIED_ID]["message"] == tool_step_states.DENIED_MESSAGE
    assert steps[CARRIED_ID]["in_progress"] is True
    assert steps[CARRIED_ID]["message"] == "still running"
    assert generator.seeded_ids == {ALLOWED_ID, AUTO_ID, CARRIED_ID}
    assert row.thoughts == _snapshot(carried), "the row's snapshot is not modified"


def test_the_build_runs_on_resume_from_the_pinned_version_with_the_restored_request(master, pinned):
    row = _row(root_version=3)
    with _turn(master, _paused_conversation(row), _tree(), versioned=lambda a, v: pinned) as turn:
        _resume(turn, _consumed(row))
        GenerationManager().unregister(CONV_ID, turn.builds.calls[0]["thread_generator"])

    built = turn.builds.calls[0]
    assert built["assistant"] is pinned
    assert turn.handler.assistant is pinned
    assert built["channel"] == BuildChannel.RESUME
    assert built["build_context"].can_resume_pause is True
    assert built["request_uuid"] == "uuid_1"
    restored = built["request"]
    assert isinstance(restored, AssistantChatRequest)
    assert (restored.text, restored.history_index, restored.version) == ("please write", 0, 3)


def test_the_run_resumes_with_every_decision(master):
    row = _row()
    pending = _consumed(row)
    with _turn(master, _paused_conversation(row), _tree()) as turn:
        _drain(_resume(turn, pending))

    assert turn.builds.resumes == [pending.decisions]
    assert turn.builds.streams == 0
    assert GenerationManager().get_generator(CONV_ID) is None


def test_a_resume_resolves_the_pinned_version_exactly_once_and_the_placeholder_and_build_see_it(master, pinned):
    """Rule 5.1 (AR-1): no resolution after the consume; the resume branch resolves once, before the first save."""
    from codemie.rest_api.handlers.tool_call_resume import ToolCallResumeMixin
    from codemie.rest_api.routers.assistant import _ask
    from codemie.service.conversation_checkpoint_service import ConversationCheckpointService

    row = _row(root_version=3)
    conversation = _paused_conversation(row)

    def consume(conversation_id, validate):
        validate(row)
        return row

    request = ToolCallResumeRequest(
        conversation_id=CONV_ID,
        decisions=[{"thought_id": ALLOWED_ID, "action": "allow"}, {"thought_id": DENIED_ID, "action": "deny"}],
    )
    with (
        _turn(master, conversation, _tree(), versioned=lambda a, v: pinned) as turn,
        patch.object(ToolCallResumeMixin, "_authorize_tool_call_resume", return_value=conversation),
        patch.object(ConversationCheckpointService, "consume", side_effect=consume),
        patch("codemie.rest_api.handlers.tool_call_resume.request_summary_manager.create_request_summary"),
        patch(
            f"{_ASK}._prepare_assistant_for_execution", wraps=_ask._prepare_assistant_for_execution
        ) as prepare_assistant,
    ):
        _, version = turn.handler.resume_tool_call(request, turn.raw_request)
        GenerationManager().unregister(CONV_ID, turn.builds.calls[0]["thread_generator"])

    prepare_assistant.assert_called_once()
    assert version is pinned
    assert turn.saves[0]["in_progress"] is True and turn.saves[0]["assistant"] is pinned
    assert turn.builds.calls[0]["assistant"] is pinned


def test_every_save_and_the_metric_record_the_pinned_version_and_its_model(master, pinned):
    row = _row(root_version=3)
    conversation = _paused_conversation(row)
    with _turn(master, conversation, _tree(), versioned=lambda a, v: pinned) as turn:
        _drain(_resume(turn, _consumed(row)))

    assert [save["in_progress"] for save in turn.saves] == [True, False]
    assert all(save["assistant"] is pinned for save in turn.saves)
    assert all(call.args[0] is pinned for call in turn.llm_context.call_args_list)
    assert turn.completion.call_args.kwargs["llm_model"] == "gpt-5"
    assert len(_entry(conversation, 0)) == 1


def _assert_turn_error(turn: Turn, conversation: Conversation) -> None:
    entry = _entry(conversation, 0)
    assert len(entry) == 1
    assert entry[0].in_progress is False
    assert entry[0].status == "error"
    assert not any(step.interrupted for step in _by_id(entry[0].thoughts).values())
    assert turn.saves[-1]["status"].value == "error"
    assert GenerationManager().get_generator(CONV_ID) is None


def test_a_deleted_pinned_version_ends_as_an_ordinary_turn_error(master):
    def gone(assistant, version):
        raise ExtendedHTTPException(code=404, message="Version not found")

    row = _row(root_version=7)
    conversation = _paused_conversation(row)
    with _turn(master, conversation, _tree(), versioned=gone) as turn:
        with pytest.raises(ExtendedHTTPException):
            _resume(turn, _consumed(row))

    assert turn.builds.calls == []
    _assert_turn_error(turn, conversation)


def test_a_stored_policy_the_enum_no_longer_knows_ends_as_an_ordinary_turn_error(master):
    row = _row(request_tool_call_policy="no_longer_a_policy")
    conversation = _paused_conversation(row)
    with _turn(master, conversation, _tree()) as turn:
        with pytest.raises(ValueError):
            _resume(turn, _consumed(row))

    assert turn.builds.calls == []
    _assert_turn_error(turn, conversation)
    assert _by_id(_entry(conversation, 0)[0].thoughts)[DENIED_ID].message == tool_step_states.DENIED_MESSAGE


def _altered_entry(row: StoredPendingToolCall) -> Conversation:
    conversation = _paused_conversation(row)
    conversation.history[1].thoughts = [Thought(id="rewritten", author_name="other", message="rewritten")]
    return conversation


def _no_entry(row: StoredPendingToolCall) -> Conversation:
    conversation = _paused_conversation(row)
    conversation.history = []
    return conversation


@pytest.mark.parametrize("conversation_of", [_altered_entry, _no_entry], ids=["altered", "not_saved"])
def test_the_resume_seeds_from_the_snapshot_not_from_the_paused_entry(master, conversation_of):
    row = _row()
    conversation = conversation_of(row)
    with _turn(master, conversation, _tree()) as turn:
        _drain(_resume(turn, _consumed(row)))

    assert [save["status"].value for save in turn.saves] == ["success", "success"]
    entry = _entry(conversation, 0)
    assert len(entry) == 1
    steps = _by_id(entry[0].thoughts)
    assert set(steps) == {CONTAINER_ID, ALLOWED_ID, DENIED_ID}
    assert steps[DENIED_ID].message == tool_step_states.DENIED_MESSAGE
    assert len(turn.builds.resumes) == 1


# --- the request restored from the row (rule 5.4) ------------------------------------------------------------


def _restored(master, row, versioned=None):
    with _turn(master, _paused_conversation(row), _tree(), versioned=versioned) as turn:
        _resume(turn, _consumed(row))
        GenerationManager().unregister(CONV_ID, turn.builds.calls[0]["thread_generator"])
    return turn.builds.calls[0]


@pytest.mark.parametrize(
    ("stored", "history_index", "message", "expected"),
    [
        (
            {
                "root_version": 3,
                "sub_assistants_versions": {S_ID: 2},
                "skill_ids": None,
                "llm_model": "gpt-5.1",
                "request_tool_call_policy": "approve_for_me",
            },
            0,
            "please write",
            {
                "version": 3,
                "sub_assistants_versions": {S_ID: 2},
                "skill_ids": None,
                "llm_model": "gpt-5.1",
                "tool_call_policy": ToolCallPolicy.APPROVE_FOR_ME,
            },
        ),
        (
            {},
            4,
            "the row's message",
            {"version": None, "sub_assistants_versions": None, "skill_ids": None, "tool_call_policy": None},
        ),
    ],
    ids=["stored_values", "none_stays_none"],
)
def test_resume_restores_the_stored_request_exactly_as_received(master, stored, history_index, message, expected):
    row = _row(history_index=history_index, **stored)
    row.original_user_message = message

    built = _restored(master, row)
    restored = built["request"]

    assert {name: getattr(restored, name) for name in expected} == expected
    # build_agent resolves the model from the request; the assistant keeps its own model.
    assert built["assistant"].llm_model_type == "gpt-4.1"
    assert (restored.text, restored.history_index) == (message, history_index)
    for name in ("llm_model", "tool_call_policy"):
        assert (name in restored.model_fields_set) == (name in expected and expected[name] is not None)


@pytest.mark.parametrize(
    ("file_names", "expected"), [(["conv_1/report.pdf"], ["conv_1/report.pdf"]), (None, [])], ids=["stored", "absent"]
)
def test_the_resume_restores_the_turns_file_names(master, file_names, expected):
    row = _row(file_names=file_names)
    if file_names is None:
        row.payload.pop("file_names")

    assert _restored(master, row)["request"].file_names == expected


def test_resume_merges_the_stored_skill_ids_as_the_ask_path_does(master):
    with patch(f"{_ASK}.Assistant") as assistant_cls:
        copy = _assistant("gpt-4.1")
        copy.skill_ids = ["existing"]
        assistant_cls.return_value = copy
        built = _restored(master, _row(skill_ids=["sk_1"]))

    assert built["request"].skill_ids == ["sk_1"]
    assert built["assistant"].skill_ids == ["existing", "sk_1"]


# --- the payload written at the pause ------------------------------------------------------------------------


def test_payload_is_exactly_the_restore_fields_and_the_interrupt_and_carried_ids():
    received = SimpleNamespace(
        version=None,
        sub_assistants_versions={S_ID: 2},
        skill_ids=None,
        llm_model=None,
        tool_call_policy=None,
        text="please write",
        file_names=[],
    )
    s_assistant = SimpleNamespace(hide_tool_outputs=False, _runtime_looked_up_toolkits={"git": ["create_branch"]})
    s = _s_agent()
    s.assistant = s_assistant
    s.tool_call_policy = ToolCallPolicy.APPROVE_FOR_ME
    root = _agent(
        "O",
        children=[s],
        policy=ToolCallPolicy.AUTO_APPROVE,
        assistant=SimpleNamespace(hide_tool_outputs=True),
    )
    root.received_request = received
    root.request = SimpleNamespace(**{**vars(received), "tool_call_policy": ToolCallPolicy.ASK_FOR_APPROVAL})

    payload = build_resume_payload(root)

    assert json.loads(json.dumps(payload)) == {
        "root_version": None,
        "sub_assistants_versions": {S_ID: 2},
        "skill_ids": None,
        "llm_model": None,
        "request_tool_call_policy": None,
        "file_names": [],
        "carried_ids": [],
    }
