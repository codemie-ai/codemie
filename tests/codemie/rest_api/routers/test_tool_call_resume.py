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

"""Resume API: authorisation, the error order, the consume, the hook and the route (rules 4, 5, 7).

Every request goes through the real router (``POST /v1/assistants/{id}/model/tool-call/resume``) and the real
``ToolCallResumeMixin.resume_tool_call``. The pending row is an in-memory store behind
``ConversationCheckpointService``; its ``consume`` locks, validates and takes the row as the SQL does (Postgres
tests cover the statement). The resume branch of ``_handle_stream`` (``begin_resume``) is stubbed: its own tests
live next to the handler.
"""

import threading
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from codemie.agents.tool_confirmation.models import DecisionItem, StoredPendingToolCall, ToolCallPendingEvent
from codemie.core.ability import Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.handlers.assistant_handlers import (
    A2AAssistantHandler,
    StandardAssistantHandler,
)
from codemie.rest_api.handlers.tool_call_resume import (
    NO_PENDING_TOOL_CALL_MESSAGE,
    ConsumedPendingToolCall,
    ResumeStart,
    ToolCallResumeMixin,
)
from codemie.rest_api.main import app
from codemie.rest_api.models.assistant import ToolCallResumeRequest
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.conversation_checkpoint_service import ConversationCheckpointService
from codemie.service.generation_manager import GenerationManager

CONV_ID = "conv_1"
ASSISTANT_ID = "asst_1"
RESUME_URL = f"/v1/assistants/{ASSISTANT_ID}/model/tool-call/resume"
_HANDLERS = "codemie.rest_api.handlers.assistant_handlers"
_RESUME = "codemie.rest_api.handlers.tool_call_resume"
_EXECUTION = "codemie.rest_api.routers.assistant.execution"

client = TestClient(app)


# --- the pause store ---------------------------------------------------------------------------------------


class PauseStore:
    """The conversation's pending row, with the row lock the consume takes."""

    def __init__(self):
        self.row: Optional[StoredPendingToolCall] = None
        self.consume_calls = 0
        self.cancels = 0
        self.inside_lock = None  # runs while a consume holds the row (the race window)
        self._lock = threading.Lock()

    def consume(self, conversation_id, validate):
        self.consume_calls += 1
        with self._lock:
            if self.row is None:
                return None
            if self.inside_lock is not None:
                self.inside_lock()
            validate(self.row.model_copy(deep=True))
            row, self.row = self.row, None
            return row

    def cancel_pause(self, conversation_id):
        self.cancels += 1
        found = self.row is not None
        self.row = None
        return found


# --- fixtures ------------------------------------------------------------------------------------------------


@pytest.fixture
def user():
    return User(id="user_1", username="tester", name="Test User")


@pytest.fixture(autouse=True)
def _authenticated(user):
    app.dependency_overrides[authenticate] = lambda: user
    yield
    app.dependency_overrides = {}


@pytest.fixture(autouse=True)
def _no_registrations():
    GenerationManager()._generators.pop(CONV_ID, None)
    yield
    GenerationManager()._generators.pop(CONV_ID, None)


def _assistant(hide: bool = False):
    a = MagicMock()
    a.id = ASSISTANT_ID
    a.name = "O"
    a.type = "default"
    a.project = "proj"
    a.hedging_config = None
    a.hide_tool_outputs = hide
    a.version = None
    return a


@pytest.fixture
def assistant():
    return _assistant()


class Denied:
    """Which Ability checks fail: (action, kind) pairs; kind is 'assistant' or 'conversation'."""

    def __init__(self):
        self.pairs: set[tuple[Action, str]] = set()

    def can(self, action, subject) -> bool:
        kind = "conversation" if isinstance(subject, Conversation) else "assistant"
        return (action, kind) not in self.pairs


class Turn:
    """What the resumed turn did: registrations, placeholder saves, builds, runs and the pinned version."""

    def __init__(self):
        self.registered: list = []
        self.saves = 0
        self.builds = 0
        self.runs: list[list[tuple[str, str]]] = []
        self.pinned = None  # the version begin_resume sets on the handler; None keeps the master
        self.handlers: list = []

    def begin_resume(self, handler, request, generator, pending, execution_start, user_message_received_at=None):
        self.builds += 1
        self.handlers.append(handler)
        if self.pinned is not None:
            handler.assistant = self.pinned
        return ResumeStart(
            decisions=pending.decisions,
            assistant=handler.assistant,
        )

    def build_resumed_agent(self, handler, request, generator, start):
        agent = MagicMock()
        agent.resume_from_interrupt.side_effect = lambda decisions, **_stream_options: self.runs.append(
            [(d.item.thought_id, d.action) for d in decisions]
        )
        return agent


def _fake_serve_data(self, stream, generator_queue, chat_request, *args, **kwargs):
    stream()
    yield '{"generated_chunk": "done", "thought": {"id": "t", "message": "tool output"}, "last": true}\n'


@pytest.fixture
def env(assistant):
    """Wires the conversation, the store, the access checks and the turn; returns a namespace to drive."""
    state = MagicMock()
    state.denied = Denied()
    state.store = PauseStore()
    state.turn = Turn()
    state.conversation = _conversation()
    manager = GenerationManager()
    real_register = manager.register

    def register(conversation_id, generator, *args, **kwargs):
        state.turn.registered.append(conversation_id)
        return real_register(conversation_id, generator, *args, **kwargs)

    def save(*_args, **_kwargs):
        state.turn.saves += 1

    with (
        patch(f"{_EXECUTION}.get_assistant_by_id_or_raise", return_value=assistant),
        patch(f"{_RESUME}.Conversation.find_by_conversation_id", side_effect=lambda *_: state.conversation),
        patch("codemie.core.ability.Ability.can", side_effect=state.denied.can),
        patch.object(ConversationCheckpointService, "consume", side_effect=state.store.consume),
        patch.object(ConversationCheckpointService, "cancel_pause", side_effect=state.store.cancel_pause),
        patch.object(GenerationManager, "register", side_effect=register),
        patch.object(StandardAssistantHandler, "save_chat_history", side_effect=save),
        patch(f"{_HANDLERS}.begin_resume", side_effect=state.turn.begin_resume),
        patch(f"{_HANDLERS}.build_resumed_agent", side_effect=state.turn.build_resumed_agent),
        patch(f"{_HANDLERS}.extract_custom_headers", return_value={}),
        patch.object(StandardAssistantHandler, "_serve_data", _fake_serve_data),
    ):
        yield state


# --- rows ----------------------------------------------------------------------------------------------------


def _item(index: int, ids: list[str], auto_allowed: bool = False) -> DecisionItem:
    return DecisionItem(
        thought_id=f"thought_{index}",
        tool_call_id=ids[index],
        location_key="",
        checkpoint_ns="agent:1|post_model_hook:1",
        event=ToolCallPendingEvent(
            pending_tool_call_id=ids[index],
            tool_name="write_file",
            tool_args={"path": f"f{index}"},
            all_tool_call_ids=ids,
        ),
        auto_allowed=auto_allowed,
    )


def _record(items: int = 1, root: str = ASSISTANT_ID, auto: tuple[int, ...] = ()) -> StoredPendingToolCall:
    ids = [f"call_{index}" for index in range(items)]
    return StoredPendingToolCall(
        items=[_item(index, ids, auto_allowed=index in auto) for index in range(items)],
        root_assistant_id=root,
        history_index=0,
        original_user_message="please write",
        thoughts=[],
        payload={"carried_ids": []},
    )


def _conversation() -> Conversation:
    return Conversation(
        id=CONV_ID,
        conversation_id=CONV_ID,
        user_id="user_1",
        assistant_ids=[ASSISTANT_ID],
        initial_assistant_id=ASSISTANT_ID,
        history=[
            GeneratedMessage(role="User", message="please write", history_index=0),
            GeneratedMessage(role="Assistant", message="", history_index=0, assistant_id=ASSISTANT_ID),
        ],
    )


def _pause(env, record: Optional[StoredPendingToolCall] = None) -> PauseStore:
    env.store.row = record if record is not None else _record()
    return env.store


def _post(action=None, thought_id=None, decisions=None, **extra):
    body: dict = {"conversation_id": CONV_ID, **extra}
    if decisions is not None:
        body["decisions"] = [{"thought_id": tid, "action": act} for tid, act in decisions]
    if action is not None:
        body["action"] = action
    if thought_id is not None:
        body["thought_id"] = thought_id
    return client.post(RESUME_URL, json=body)


def _message(response) -> str:
    return response.json()["error"]["message"]


def _nothing_happened(env) -> bool:
    """Nothing registered, no placeholder, nothing built, nothing ran."""
    turn = env.turn
    return turn.registered == [] and turn.saves == 0 and turn.builds == 0 and turn.runs == []


# --- authorisation first --------------------------------------------------------------------------------------


_MALFORMED_BODIES = {
    "both-shapes": {"action": "allow", "decisions": [{"thought_id": "thought_0", "action": "allow"}]},
    "neither": {},
    "thought-id-next-to-decisions": {
        "thought_id": "thought_0",
        "decisions": [{"thought_id": "thought_0", "action": "allow"}],
    },
}


def _not_a_member(env):
    env.conversation.assistant_ids = ["other"]
    env.conversation.initial_assistant_id = "other"


@pytest.mark.parametrize("body", list(_MALFORMED_BODIES.values()), ids=list(_MALFORMED_BODIES))
@pytest.mark.parametrize(
    ("denied", "code"),
    [
        ((Action.READ, "assistant"), 401),
        ((Action.WRITE, "conversation"), 403),
        ("not-a-member", 403),
    ],
    ids=["no-read-on-assistant", "no-write-on-conversation", "not-a-member"],
)
def test_an_unauthorised_resume_is_refused_before_shape_checks_summary_and_row(env, denied, code, body):
    store = _pause(env)
    if denied == "not-a-member":
        _not_a_member(env)
    else:
        env.denied.pairs.add(denied)

    with patch(f"{_RESUME}.request_summary_manager.create_request_summary") as create:
        response = client.post(RESUME_URL, json={"conversation_id": CONV_ID, "stream": False, **body})

    assert response.status_code == code
    create.assert_not_called()
    assert store.consume_calls == 0 and store.row is not None and _nothing_happened(env)


# --- 422 before the consume -----------------------------------------------------------------------------------


def test_only_the_standard_handler_resumes_and_the_a2a_handler_gets_422(env, assistant, user):
    assert ToolCallResumeMixin.supports_tool_call_resume is False
    assert A2AAssistantHandler.supports_tool_call_resume is False
    assert StandardAssistantHandler.supports_tool_call_resume is True
    store = _pause(env)
    handler = A2AAssistantHandler.__new__(A2AAssistantHandler)
    handler.user, handler.assistant, handler.request_uuid = user, assistant, "req-1"

    with pytest.raises(ExtendedHTTPException) as error:
        handler.resume_tool_call(ToolCallResumeRequest(conversation_id=CONV_ID, action="allow"), MagicMock())

    assert error.value.code == 422
    assert store.consume_calls == 0 and store.row is not None and _nothing_happened(env)


@pytest.mark.parametrize(
    "body",
    [{"action": "allow", "stream": False}, *_MALFORMED_BODIES.values()],
    ids=["stream-false", *_MALFORMED_BODIES],
)
def test_an_authorised_bad_request_gets_422_without_a_summary_and_before_the_consume(env, body):
    store = _pause(env)

    with patch(f"{_RESUME}.request_summary_manager.create_request_summary") as create:
        response = client.post(RESUME_URL, json={"conversation_id": CONV_ID, **body})

    assert response.status_code == 422
    assert _message(response)
    create.assert_not_called()
    assert store.consume_calls == 0 and store.row is not None and _nothing_happened(env)


@pytest.mark.parametrize(
    ("record", "body", "code"),
    [
        (None, {"action": "allow"}, 404),
        ("other-root", {"action": "allow"}, 409),
        ("two-shown", {"action": "allow"}, 409),
    ],
    ids=["no-row", "other-root", "id-set"],
)
def test_a_refused_resume_after_authorisation_creates_no_request_summary(env, record, body, code):
    """Rule 4 (AR-7): the summary is created only after the consume, so a refused resume leaves nothing in memory."""
    if record == "other-root":
        _pause(env, _record(root="other_root"))
    elif record == "two-shown":
        _pause(env, _record(items=2))

    with patch(f"{_RESUME}.request_summary_manager.create_request_summary") as create:
        response = _post(**body)

    assert response.status_code == code
    create.assert_not_called()


def test_an_accepted_resume_creates_the_request_summary_once(env):
    _pause(env)

    with patch(f"{_RESUME}.request_summary_manager.create_request_summary") as create:
        response = _post("allow")

    assert response.status_code == 200
    create.assert_called_once()
    assert create.call_args.kwargs["project_name"] == "proj" and create.call_args.kwargs["request_id"]


# --- the consume: 404, other root 409, id-set 409 ----------------------------------------------


def test_no_row_gives_404_and_nothing_happens(env):
    response = _post("allow")

    assert response.status_code == 404
    assert _message(response) == NO_PENDING_TOOL_CALL_MESSAGE
    assert env.store.cancels == 0 and _nothing_happened(env)


def _stale_waiting_step(env) -> None:
    """The last answer still shows a waiting step whose pause is gone (e.g. a crash after an earlier consume)."""
    from codemie.chains.base import Thought
    from codemie.core import tool_step_states

    step = tool_step_states.waiting({"id": "thought_0", "author_name": "Write File", "author_type": "Tool"})
    env.conversation.history[-1].thoughts = [Thought.model_validate(step)]


def test_a_404_cancels_the_stale_waiting_steps_of_the_last_answer(env):
    """Rule 7.2 on the resume path: after a reload the user sees no Allow/Deny that only answers 404."""
    from codemie.core.tool_step_states import CANCELLED_MESSAGE

    _stale_waiting_step(env)
    with patch.object(Conversation, "update") as update:
        response = _post("allow", thought_id="thought_0")

    assert response.status_code == 404 and _message(response) == NO_PENDING_TOOL_CALL_MESSAGE
    (step,) = env.conversation.history[-1].thoughts
    assert step.interrupted is False and step.aborted is True and step.message == CANCELLED_MESSAGE
    assert update.call_count == 1 and _nothing_happened(env)


def test_a_404_still_answers_404_when_cancelling_the_stale_steps_fails(env):
    _stale_waiting_step(env)
    with patch.object(Conversation, "update", side_effect=RuntimeError("db down")):
        response = _post("allow", thought_id="thought_0")

    assert response.status_code == 404 and _message(response) == NO_PENDING_TOOL_CALL_MESSAGE


def test_another_root_assistant_gives_409_and_leaves_the_row(env):
    store = _pause(env, _record(root="other_root"))

    response = _post(decisions=[("thought_9", "allow")])  # also a bad id set: the root is checked first

    assert response.status_code == 409
    assert "another assistant" in _message(response)
    assert store.row is not None and store.row.root_assistant_id == "other_root" and _nothing_happened(env)


@pytest.mark.parametrize(
    ("items", "request_body"),
    [
        (2, {"decisions": [("thought_0", "allow")]}),
        (1, {"decisions": [("thought_0", "allow"), ("thought_1", "deny")]}),
        (2, {"decisions": [("thought_0", "allow"), ("thought_0", "deny")]}),
        (1, {"decisions": [("thought_x", "allow")]}),
        (2, {"action": "allow"}),
        (1, {"action": "allow", "thought_id": "thought_x"}),
        (2, {"decisions": [("thought_0", "allow"), ("thought_1", "allow")], "auto": (1,)}),
    ],
    ids=[
        "missing",
        "extra",
        "duplicate",
        "unknown",
        "action-with-two-shown",
        "action-with-another-id",
        "auto-allowed-extra",
    ],
)
def test_an_id_set_mismatch_gives_409_and_leaves_the_row(env, items, request_body):
    auto = request_body.pop("auto", ())
    store = _pause(env, _record(items=items, auto=auto))
    before = store.row.model_dump()

    response = _post(**request_body)

    assert response.status_code == 409
    assert store.row is not None and store.row.model_dump() == before and _nothing_happened(env)


# --- the hook, after the consume ----------------------------------------------------------------------------


@pytest.mark.parametrize("thought_id", [None, "thought_0"])
def test_a_single_shown_call_accepts_action_with_and_without_its_thought_id(env, thought_id):
    _pause(env)

    response = _post("deny", thought_id=thought_id)

    assert response.status_code == 200
    assert env.turn.runs == [[("thought_0", "deny")]]


def test_the_hook_is_called_once_after_the_consume_with_the_consumed_row(env, assistant):
    store = _pause(env, _record(items=3, auto=(1,)))
    expected_row = store.row.model_copy(deep=True)
    calls: list = []
    real_hook = StandardAssistantHandler.handle_tool_call_resume

    def hook(self, request, conversation, pending, raw_request):
        calls.append((request, conversation, pending, raw_request, store.row))
        return real_hook(self, request, conversation, pending, raw_request)

    with patch.object(StandardAssistantHandler, "handle_tool_call_resume", hook):
        response = _post(decisions=[("thought_2", "deny"), ("thought_0", "allow")])

    assert response.status_code == 200
    assert len(calls) == 1
    request, conversation, pending, raw_request, row_at_hook = calls[0]
    assert isinstance(request, ToolCallResumeRequest) and request.conversation_id == CONV_ID
    assert conversation.id == CONV_ID and raw_request is not None
    assert row_at_hook is None, "the row is consumed before the hook"
    assert isinstance(pending, ConsumedPendingToolCall) and pending.record == expected_row
    assert [(d.item.thought_id, d.action) for d in pending.decisions] == [
        ("thought_0", "allow"),
        ("thought_1", "allow"),
        ("thought_2", "deny"),
    ]
    assert env.turn.registered == [CONV_ID] and env.turn.builds == 1


def test_nothing_resolves_the_pinned_version_between_the_consume_and_the_hook(env, assistant):
    """Rule 5.1 (AR-1): the hook gets the master; only the resume branch resolves the pinned version."""
    seen: list = []
    real_hook = StandardAssistantHandler.handle_tool_call_resume

    def hook(self, request, conversation, pending, raw_request):
        seen.append(self.assistant)
        return real_hook(self, request, conversation, pending, raw_request)

    with (
        patch.object(StandardAssistantHandler, "handle_tool_call_resume", hook),
        patch("codemie.rest_api.routers.assistant._ask._prepare_assistant_for_execution") as prepare_assistant,
    ):
        _pause(env)
        assert _post("allow").status_code == 200

    assert seen == [assistant]
    prepare_assistant.assert_not_called()


def test_the_default_hook_calls_the_standard_stream_even_on_a_hedged_handler(assistant, user):
    from codemie.rest_api.handlers.hedged_handler import HedgedAssistantHandler

    handler = HedgedAssistantHandler(assistant, user, "req-1")
    pending = ConsumedPendingToolCall(_record(), [])
    request = ToolCallResumeRequest(conversation_id=CONV_ID, action="allow")
    raw_request = MagicMock()

    with (
        patch.object(StandardAssistantHandler, "_handle_stream", return_value="standard") as standard,
        patch.object(HedgedAssistantHandler, "_handle_stream", side_effect=AssertionError("hedged")),
    ):
        response = handler.handle_tool_call_resume(request, _conversation(), pending, raw_request)

    assert response == "standard"
    standard.assert_called_once()
    args, kwargs = standard.call_args
    assert args[0] is handler and args[1] is request and args[2] is raw_request
    assert kwargs["resume"] is pending


def test_of_two_concurrent_resumes_one_streams_and_the_other_gets_404(env):
    store = _pause(env)
    second: dict = {}

    def race():
        # The second resume arrives while the first holds the row: it waits on the lock, then finds nothing.
        thread = threading.Thread(target=lambda: second.setdefault("response", _post("allow")))
        thread.start()
        thread.join(timeout=0.2)
        second["thread"] = thread

    store.inside_lock = race
    first = _post("allow")
    store.inside_lock = None
    second["thread"].join(timeout=5)

    assert sorted([first.status_code, second["response"].status_code]) == [200, 404]
    assert env.turn.runs == [[("thought_0", "allow")]], "each tool runs once"
    assert env.turn.registered == [CONV_ID] and env.turn.builds == 1


# --- tool-output hiding on the route (AC21) ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("master_hides", "pinned_hides", "hidden"),
    [(False, False, False), (True, False, True), (False, True, True)],
    ids=["neither", "only-master", "only-pinned"],
)
def test_the_route_strips_when_the_master_or_the_pinned_version_hides(
    env, assistant, master_hides, pinned_hides, hidden
):
    _pause(env)
    assistant.hide_tool_outputs = master_hides
    env.turn.pinned = _assistant(hide=pinned_hides)

    response = _post("allow")

    assert response.status_code == 200
    assert ("tool output" not in response.text) is hidden


# --- a new message cancels a waiting pause (AC12) --------------------------------------------------------------


def test_a_new_message_cancels_the_waiting_pause_and_a_later_resume_gets_404(env, assistant, user):
    from codemie.core.models import AssistantChatRequest

    store = _pause(env)
    handler = StandardAssistantHandler(assistant, user, "req-1")

    with (
        patch(f"{_HANDLERS}.Conversation.find_by_id", return_value=env.conversation),
        patch.object(ConversationCheckpointService, "get_pending", side_effect=lambda _id: store.row),
    ):
        handler._guard_conversation_not_finished(AssistantChatRequest(text="new", conversation_id=CONV_ID))

    assert store.cancels == 1 and store.row is None
    response = _post("allow")
    assert response.status_code == 404 and _nothing_happened(env)
