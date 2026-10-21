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

"""End-to-end acceptance for assistant-level hide_tool_outputs: multi-tool runs, regression, round trip."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse

from codemie.rest_api.security.authentication import authenticate
from codemie.chains.base import Thought
from codemie.core.models import AssistantChatRequest
from codemie.rest_api.main import app
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage
from codemie.rest_api.routers.assistant._ask import _ask_assistant
from codemie.rest_api.security.user import User

R = "codemie.rest_api.routers.conversation"
VIS = "codemie.service.assistant.tool_output_visibility.Assistant.get_by_ids_no_permission_check"
ASSISTANT_ID = "assistant-123"


def _line(**kwargs) -> str:
    return json.dumps(kwargs) + "\n"


def _run_chunks() -> list[str]:
    """A multi-toolkit run: tool thoughts, a2ui, agent/execution errors, text, and a final chunk with tool_errors."""
    return [
        _line(thought={"id": "t1", "author_name": "Jira", "message": "jira secret"}),
        _line(thought={"id": "t2", "author_name": "Git", "message": "git secret"}),
        _line(thought={"id": "t3", "author_name": "Confluence", "message": "wiki secret"}),
        _line(a2ui={"component": "card"}),
        _line(generated_chunk="Hello "),
        _line(thought={"id": "t4"}, agent_error={"message": "agent failed"}),
        _line(execution_error={"message": "exec failed"}),
        _line(generated_chunk="world"),
        _line(
            last=True,
            generated="Hello world",
            thought={"id": "t5", "message": "final tool detail"},
            debug={"prompt": "secret"},
            tool_errors=[{"tool": "Jira", "status": 401}],
        ),
    ]


def _assistant(hide: bool | None, toolkits: int = 2) -> MagicMock:
    assistant = MagicMock(spec=Assistant)
    assistant.id = ASSISTANT_ID
    assistant.project = "test-project"
    assistant.toolkits = [MagicMock() for _ in range(toolkits)]
    assistant.hide_tool_outputs = hide
    return assistant


def _ask(assistant, user, chunks, **kwargs):
    handler = MagicMock()
    handler.process_request.return_value = StreamingResponse(iter(chunks))
    raw_request = MagicMock(state=MagicMock(uuid="req-1"))
    with (
        patch("codemie.rest_api.routers.assistant._ask.assistant_user_interaction_service.record_usage"),
        patch("codemie.rest_api.routers.assistant._ask.request_summary_manager.create_request_summary"),
        patch("codemie.rest_api.routers.utils.Ability") as ability,
        patch("codemie.rest_api.routers.assistant._ask.GuardrailService.apply_guardrails_for_entity") as guard,
        patch("codemie.rest_api.routers.assistant._ask.get_request_handler", return_value=handler),
    ):
        ability.return_value.can.return_value = True
        guard.return_value = ("hi", None)
        resp = _ask_assistant(assistant, raw_request, AssistantChatRequest(text="hi"), user, MagicMock(), **kwargs)

    async def _collect():
        return [json.loads(c) async for c in resp.body_iterator]

    return asyncio.run(_collect())


@pytest.fixture
def user():
    return MagicMock(spec=User)


def test_multi_tool_assistant_on_shows_only_final_response(user):
    out = _ask(_assistant(True, toolkits=3), user, _run_chunks())

    # pure-thought chunks are dropped; the agent_error chunk survives with its thought removed
    assert out == [
        {"a2ui": {"component": "card"}},
        {"generated_chunk": "Hello "},
        {"thought": None, "agent_error": {"message": "agent failed"}},
        {"execution_error": {"message": "exec failed"}},
        {"generated_chunk": "world"},
        {"last": True, "generated": "Hello world", "thought": None, "debug": None, "tool_errors": None},
    ]
    assert "secret" not in json.dumps(out)


@pytest.mark.parametrize("flag", [None, False])
def test_existing_assistant_without_flag_behaves_as_before(user, flag):
    # None mirrors an assistant whose column defaulted / was never set
    chunks = _run_chunks()
    out = _ask(_assistant(flag), user, chunks)

    assert out == [json.loads(c) for c in chunks]


def test_creator_also_hidden():
    creator = MagicMock(spec=User)
    creator.id = "creator-1"
    creator.username = "creator"
    assistant = _assistant(True)
    assistant.created_by = SimpleNamespace(id="creator-1", username="creator")

    out = _ask(assistant, creator, _run_chunks())

    assert all(c.get("thought") is None for c in out)
    assert out[-1]["tool_errors"] is None and out[-1]["debug"] is None
    assert out[-1]["generated"] == "Hello world"


def test_conversation_level_setting_cannot_reenable(user):
    # client explicitly asks for tool errors; the assistant-level flag still wins
    out = _ask(_assistant(True), user, _run_chunks(), include_tool_errors=True)

    assert all(c.get("thought") is None for c in out)
    assert out[-1]["tool_errors"] is None
    assert out[-1]["debug"] is None
    assert out[-1]["generated"] == "Hello world"


def test_on_then_off_round_trip_persists_full_thoughts(user):
    client = TestClient(app)
    app.dependency_overrides[authenticate] = lambda: user
    try:
        # 1) ask with ON: live egress is stripped while the source of truth (what gets persisted) is untouched
        source = _run_chunks()
        live = _ask(_assistant(True), user, source)
        assert all(c.get("thought") is None for c in live)
        stored_thoughts = [
            Thought(**json.loads(c)["thought"])
            for c in source
            if json.loads(c).get("thought", {}) and "message" in json.loads(c)["thought"]
        ]
        assert len(stored_thoughts) == 4  # full thoughts remain in the stored stream

        conversation = Conversation(
            id="c1",
            conversation_id="c1",
            name="Conv",
            initial_assistant_id=ASSISTANT_ID,
            assistant_ids=[ASSISTANT_ID],
            history=[
                GeneratedMessage(
                    message="Hello world", role="Assistant", assistant_id=ASSISTANT_ID, thoughts=stored_thoughts
                )
            ],
        )

        def _read(flagged: bool):
            lookup = lambda ids: [SimpleNamespace(id=i, hide_tool_outputs=flagged) for i in ids]  # noqa: E731
            with (
                patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=conversation),
                patch("codemie.core.ability.Ability.can", return_value=True),
                patch("codemie.rest_api.models.assistant.Assistant.get_by_ids", return_value=[]),
                patch(VIS, side_effect=lookup),
            ):
                return client.get("/v1/conversations/c1", headers={"Authorization": "Bearer t"}).json()

        # 2) flag ON: read path strips; 3) flip OFF: same stored history now returns full thoughts
        assert _read(True)["history"][0]["thoughts"] == []
        restored = _read(False)["history"][0]["thoughts"]
        assert [t["message"] for t in restored] == ["jira secret", "git secret", "wiki secret", "final tool detail"]
    finally:
        app.dependency_overrides = {}
