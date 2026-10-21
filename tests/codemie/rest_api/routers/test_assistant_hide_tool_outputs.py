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

"""Router egress filtering for the assistant-level hide_tool_outputs flag."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.responses import StreamingResponse

from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant, AssistantConfiguration
from codemie.rest_api.models.assistant import ToolCallResumeRequest
from codemie.rest_api.routers.assistant._ask import _ask_assistant, _ask_virtual_assistant
from codemie.rest_api.routers.assistant.execution import (
    _ask_assistant_resume,
    ask_assistant_by_id,
    ask_assistant_by_slug,
)
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_version_service import AssistantVersionService


def _line(**kwargs) -> str:
    return json.dumps(kwargs) + "\n"


def _stream() -> StreamingResponse:
    return StreamingResponse(iter([_line(thought={"id": "1"}), _line(generated_chunk="a")]))


def _chunks(resp: StreamingResponse) -> list:
    async def _collect():
        return [json.loads(c) async for c in resp.body_iterator]

    return asyncio.run(_collect())


def _assistant(hide: bool) -> MagicMock:
    assistant = MagicMock(spec=Assistant)
    assistant.id = "assistant-123"
    assistant.project = "test-project"
    assistant.hide_tool_outputs = hide
    return assistant


@pytest.fixture
def user():
    return MagicMock(spec=User)


@pytest.fixture
def raw_request():
    return MagicMock(state=MagicMock(uuid="req-1"))


def _ask(assistant, raw_request, user, handler):
    with (
        patch("codemie.rest_api.routers.assistant._ask.assistant_user_interaction_service.record_usage"),
        patch("codemie.rest_api.routers.assistant._ask.request_summary_manager.create_request_summary"),
        patch("codemie.rest_api.routers.utils.Ability") as ability,
        patch("codemie.rest_api.routers.assistant._ask.GuardrailService.apply_guardrails_for_entity") as guard,
        patch("codemie.rest_api.routers.assistant._ask.get_request_handler", return_value=handler),
    ):
        ability.return_value.can.return_value = True
        guard.return_value = ("hi", None)
        return _ask_assistant(assistant, raw_request, AssistantChatRequest(text="hi"), user, MagicMock())


def test_ask_strips_stream_when_flag_on(raw_request, user):
    handler = MagicMock()
    handler.process_request.return_value = _stream()

    resp = _ask(_assistant(True), raw_request, user, handler)

    assert [c["generated_chunk"] for c in _chunks(resp)] == ["a"]


def test_ask_strips_thought_only_chunk_when_flag_on(raw_request, user):
    handler = MagicMock()
    handler.process_request.return_value = _stream()

    resp = _ask(_assistant(True), raw_request, user, handler)

    chunks = _chunks(resp)
    assert chunks == [{"generated_chunk": "a"}]


def test_ask_unchanged_when_flag_off(raw_request, user):
    handler = MagicMock()
    handler.process_request.return_value = _stream()

    resp = _ask(_assistant(False), raw_request, user, handler)

    chunks = _chunks(resp)
    assert chunks == [{"thought": {"id": "1"}}, {"generated_chunk": "a"}]


def test_resume_strips_when_flag_on(raw_request, user):
    handler = MagicMock()
    handler.handle_tool_call_resume.return_value = _stream()
    request = ToolCallResumeRequest(conversation_id="c1", action="allow")

    with patch("codemie.rest_api.handlers.assistant_handlers.get_request_handler", return_value=handler):
        resp = _ask_assistant_resume(_assistant(True), raw_request, request, user, MagicMock())

    assert _chunks(resp) == [{"generated_chunk": "a"}]


def test_resume_unchanged_when_flag_off(raw_request, user):
    handler = MagicMock()
    handler.handle_tool_call_resume.return_value = _stream()
    request = ToolCallResumeRequest(conversation_id="c1", action="allow")

    with patch("codemie.rest_api.handlers.assistant_handlers.get_request_handler", return_value=handler):
        resp = _ask_assistant_resume(_assistant(False), raw_request, request, user, MagicMock())

    assert len(_chunks(resp)) == 2


@pytest.mark.parametrize("handler_cls", ["A2AAssistantHandler", "HedgedAssistantHandler"])
def test_a2a_and_hedged_handlers_are_covered(handler_cls, raw_request, user):
    """Any handler's process_request output is filtered at the router; no handler-level change is needed."""
    handler = MagicMock(spec=[])
    handler.__class__.__name__ = handler_cls
    handler.process_request = MagicMock(return_value=_stream())

    resp = _ask(_assistant(True), raw_request, user, handler)

    assert _chunks(resp) == [{"generated_chunk": "a"}]


def test_version_pinned_flag_is_used(raw_request, user):
    """Called directly (no master override), _ask_assistant uses the given assistant's flag."""
    on_handler = MagicMock()
    on_handler.process_request.return_value = _stream()
    off_handler = MagicMock()
    off_handler.process_request.return_value = _stream()

    stripped = _ask(_assistant(True), raw_request, user, on_handler)
    untouched = _ask(_assistant(False), raw_request, user, off_handler)

    assert len(_chunks(stripped)) == 1
    assert len(_chunks(untouched)) == 2


def test_virtual_assistant_is_filtered(raw_request, user):
    """Virtual/transient assistants have no DB record but must still honor hide_tool_outputs."""
    handler = MagicMock()
    stream = _stream()
    handler.process_request.return_value = stream
    virtual = _assistant(True)
    virtual.id = None

    with (
        patch("codemie.rest_api.routers.assistant._ask.request_summary_manager.create_request_summary"),
        patch("codemie.rest_api.routers.utils.Ability") as ability,
        patch("codemie.rest_api.routers.assistant._ask.get_request_handler", return_value=handler),
    ):
        ability.return_value.can.return_value = True
        resp = _ask_virtual_assistant(virtual, raw_request, AssistantChatRequest(text="hi"), user, MagicMock())

    assert resp is stream
    assert len(_chunks(resp)) == 1


def test_virtual_assistant_not_hidden_passes_through(raw_request, user):
    handler = MagicMock()
    stream = _stream()
    handler.process_request.return_value = stream
    virtual = _assistant(False)
    virtual.id = None

    with (
        patch("codemie.rest_api.routers.assistant._ask.request_summary_manager.create_request_summary"),
        patch("codemie.rest_api.routers.utils.Ability") as ability,
        patch("codemie.rest_api.routers.assistant._ask.get_request_handler", return_value=handler),
    ):
        ability.return_value.can.return_value = True
        resp = _ask_virtual_assistant(virtual, raw_request, AssistantChatRequest(text="hi"), user, MagicMock())

    assert resp is stream
    assert len(_chunks(resp)) == 2


def _real_assistant(hide: bool) -> Assistant:
    return Assistant(
        id="assistant-123",
        name="A",
        description="d",
        system_prompt="p",
        project="test-project",
        hide_tool_outputs=hide,
        version_count=2,
    )


def _version(hide: bool) -> AssistantConfiguration:
    return AssistantConfiguration(
        assistant_id="assistant-123", version_number=1, description="d", system_prompt="p", hide_tool_outputs=hide
    )


def _ask_pinned(route, master: Assistant, pinned: AssistantConfiguration, raw_request, user):
    handler = MagicMock()
    handler.process_request.return_value = _stream()
    request = AssistantChatRequest(text="hi", version=1)
    with (
        patch("codemie.rest_api.routers.assistant._ask.assistant_user_interaction_service.record_usage"),
        patch("codemie.rest_api.routers.assistant._ask.request_summary_manager.create_request_summary"),
        patch("codemie.rest_api.routers.utils.Ability") as ability,
        patch("codemie.rest_api.routers.assistant._ask.GuardrailService.apply_guardrails_for_entity") as guard,
        patch("codemie.rest_api.routers.assistant._ask.get_request_handler", return_value=handler),
        patch("codemie.rest_api.routers.assistant._ask._validate_remote_entities_and_raise"),
        patch("codemie.rest_api.routers.assistant.execution.get_assistant_by_id_or_raise", return_value=master),
        patch("codemie.rest_api.routers.assistant.execution._get_assistant_by_slug_or_raise", return_value=master),
        patch.object(AssistantVersionService, "get_version", return_value=pinned) as get_version,
    ):
        ability.return_value.can.return_value = True
        guard.return_value = ("hi", None)
        if route == "id":
            raw_request.state.wait_for_disconnect = AsyncMock()

            async def _call():
                return await ask_assistant_by_id(raw_request, master.id, MagicMock(), request, user, True)

            resp = asyncio.run(_call())
        else:
            resp = ask_assistant_by_slug(raw_request, "slug", MagicMock(), request, user, True)
    get_version.assert_called_once_with(master.id, 1)
    return resp


@pytest.mark.parametrize("route", ["id", "slug"])
@pytest.mark.parametrize(("master_hide", "pinned_hide"), [(True, False), (False, True)])
def test_version_pin_cannot_bypass_hiding(route, master_hide, pinned_hide, raw_request, user):
    """Fail closed: hidden when either the master assistant or the pinned version hides tool outputs."""
    resp = _ask_pinned(route, _real_assistant(master_hide), _version(pinned_hide), raw_request, user)

    assert _chunks(resp) == [{"generated_chunk": "a"}]


@pytest.mark.parametrize("route", ["id", "slug"])
def test_version_pin_unhidden_when_both_off(route, raw_request, user):
    resp = _ask_pinned(route, _real_assistant(False), _version(False), raw_request, user)

    assert len(_chunks(resp)) == 2
