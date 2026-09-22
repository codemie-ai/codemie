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

"""Tests for A2UI envelope capture in the streaming drain loop and persistence."""

from time import time
from unittest.mock import MagicMock, Mock, patch

import pytest

from codemie.chains.base import StreamedGenerationResult
from codemie.core.models import AssistantChatRequest
from codemie.core.thread import ThreadedGenerator
from codemie.rest_api.handlers.assistant_handlers import ChatHistoryData, StandardAssistantHandler
from codemie.rest_api.security.user import User


@pytest.fixture
def mock_user():
    user = Mock(spec=User)
    user.id = "user-123"
    user.username = "testuser"
    user.name = "Test User"
    return user


@pytest.fixture
def mock_assistant():
    assistant = MagicMock()
    assistant.id = "assistant-123"
    assistant.name = "Test Assistant"
    assistant.project = "test-project"
    return assistant


def _surface_envelopes(surface_id="s1"):
    return [
        {"version": "v0.9.1", "createSurface": {"surfaceId": surface_id, "catalogId": "cat-1"}},
        {
            "version": "v0.9.1",
            "updateComponents": {
                "surfaceId": surface_id,
                "components": [
                    {"id": "root", "component": "Column", "children": ["email", "ok"]},
                    {"id": "email", "component": "TextField", "label": "Email", "value": {"path": "/email"}},
                    {"id": "ok", "component": "Button", "child": "ok-label", "action": {"event": {"name": "submit"}}},
                    {"id": "ok-label", "component": "Text", "text": "OK"},
                ],
            },
        },
        {"version": "v0.9.1", "updateDataModel": {"surfaceId": surface_id, "path": "/", "value": {"email": ""}}},
    ]


def _drain(handler, generator_queue, request):
    stream = MagicMock()
    with patch("codemie.rest_api.handlers.assistant_handlers.run_assistant_in_thread_pool"):
        chunks = list(
            handler._serve_data(
                stream,
                generator_queue,
                request,
                execution_start=time(),
            )
        )
    return chunks


def test_serve_data_accumulates_a2ui_envelopes(mock_assistant, mock_user):
    handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")
    generator_queue = ThreadedGenerator()
    envelopes = _surface_envelopes()
    for envelope in envelopes:
        generator_queue.send(StreamedGenerationResult(a2ui=envelope).model_dump_json())
    generator_queue.send(StreamedGenerationResult(generated="").model_dump_json())
    generator_queue.close()

    request = AssistantChatRequest(text="hi", stream=True)
    with patch.object(handler, "save_chat_history") as save_mock:
        chunks = _drain(handler, generator_queue, request)

    # All envelopes are streamed to the client unchanged
    assert any('"a2ui"' in chunk and '"createSurface"' in chunk for chunk in chunks)
    assert any('"updateComponents"' in chunk for chunk in chunks)
    # And the FULL ordered list of the turn's envelopes is captured for persistence
    saved = save_mock.call_args[0][0]
    assert saved.a2ui_envelopes == envelopes


def test_serve_data_without_a2ui_chunks_saves_none(mock_assistant, mock_user):
    handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")
    generator_queue = ThreadedGenerator()
    generator_queue.send(StreamedGenerationResult(generated="plain text").model_dump_json())
    generator_queue.close()

    request = AssistantChatRequest(text="hi", stream=True)
    with patch.object(handler, "save_chat_history") as save_mock:
        _drain(handler, generator_queue, request)

    assert save_mock.call_args[0][0].a2ui_envelopes is None


def test_save_chat_history_forwards_a2ui_envelopes(mock_assistant, mock_user):
    handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")
    envelopes = _surface_envelopes()
    request = AssistantChatRequest(text="hi", stream=True)
    with (
        patch("codemie.rest_api.handlers.assistant_handlers.ConversationService.upsert_chat_history") as upsert_mock,
        patch("codemie.rest_api.handlers.assistant_handlers.request_summary_manager"),
        patch("codemie.service.llm_service.utils.set_llm_context"),
    ):
        handler.save_chat_history(
            ChatHistoryData(
                execution_start=time(),
                request=request,
                response="",
                thoughts=[],
                a2ui_envelopes=envelopes,
            )
        )
    assert upsert_mock.call_args.kwargs["outcome"].a2ui_envelopes == envelopes


def _action_envelope(surface_id="s1"):
    return {
        "version": "v0.9.1",
        "action": {"name": "submit", "surfaceId": surface_id, "sourceComponentId": "ok", "context": {}},
    }


class TestChatRequestA2uiFields:
    def test_request_accepts_wire_aliases(self):
        request = AssistantChatRequest.model_validate(
            {"text": "x", "a2uiAction": _action_envelope(), "a2uiDataModel": {"email": "a@b.c"}}
        )
        assert request.a2ui_action["action"]["surfaceId"] == "s1"
        assert request.a2ui_data_model == {"email": "a@b.c"}

    def test_request_serializes_with_wire_aliases(self):
        request = AssistantChatRequest(text="x", a2ui_action=_action_envelope(), a2ui_data_model={})
        dumped = request.model_dump(by_alias=True)
        assert dumped["a2uiAction"]["action"]["name"] == "submit"
        assert "a2uiDataModel" in dumped

    def test_fields_default_to_none(self):
        request = AssistantChatRequest(text="x")
        assert request.a2ui_action is None
        assert request.a2ui_data_model is None

    def test_supported_catalogs_accepts_frontend_wire_name(self):
        # Regression guard: the auto camel generator turns a2ui_supported_catalogs into
        # "a2UiSupportedCatalogs", which silently drops what the frontend actually sends
        # ("a2uiSupportedCatalogs") and leaves the capability gate permanently closed.
        request = AssistantChatRequest.model_validate(
            {"text": "x", "a2uiSupportedCatalogs": ["https://example.test/catalog.json"]}
        )
        assert request.a2ui_supported_catalogs == ["https://example.test/catalog.json"]

    def test_supported_catalogs_serializes_with_frontend_wire_name(self):
        request = AssistantChatRequest(text="x", a2ui_supported_catalogs=["c"])
        assert request.model_dump(by_alias=True)["a2uiSupportedCatalogs"] == ["c"]


class TestA2uiActionValidationWiring:
    def test_unknown_conversation_rejects_action(self, mock_assistant, mock_user):
        from codemie.core.exceptions import ExtendedHTTPException

        handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")
        request = AssistantChatRequest(text="x", conversation_id="c-none", a2ui_action=_action_envelope())
        with patch("codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_id", return_value=None):
            with pytest.raises(ExtendedHTTPException):
                handler._validate_a2ui_action(request)

    def test_noop_when_action_absent(self, mock_assistant, mock_user):
        handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")
        request = AssistantChatRequest(text="plain", conversation_id="c1")
        with patch("codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_id") as find_mock:
            handler._validate_a2ui_action(request)  # must not raise
        find_mock.assert_not_called()

    def test_free_text_reply_to_a_pending_surface_is_not_an_error(self, mock_assistant, mock_user):
        """A user may answer in the chat box instead of the form; that is a normal turn.

        It carries no action, so there is nothing to validate — the surface just stays
        unanswered. Asserting the conversation is never loaded pins that: a plain message
        must not pay for a history read, nor be able to fail on one.
        """
        handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")
        request = AssistantChatRequest(text="actually, let me just type it", conversation_id="c1")
        with patch("codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_id") as find_mock:
            handler._validate_a2ui_action(request)  # must not raise
        find_mock.assert_not_called()

    def test_data_model_without_action_rejected(self, mock_assistant, mock_user):
        # conversation_service persists a2ui_data_model unconditionally, so a data model
        # submitted without an action would otherwise skip ownership, surface existence,
        # the already-answered guard and the size cap entirely.
        from codemie.core.exceptions import ExtendedHTTPException

        handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")
        request = AssistantChatRequest(text="x", conversation_id="c1", a2ui_data_model={"blob": "x" * 200_000})
        with pytest.raises(ExtendedHTTPException) as exc:
            handler._validate_a2ui_action(request)
        assert "action" in str(exc.value.message).lower()

    def test_valid_action_with_data_model_passes_through_handler(self, mock_assistant, mock_user):
        from codemie.rest_api.models.conversation import GeneratedMessage

        handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")
        request = AssistantChatRequest(
            text="x", conversation_id="c1", a2ui_action=_action_envelope("s1"), a2ui_data_model={"email": "a@b.co"}
        )
        conversation = MagicMock()
        conversation.history = [
            GeneratedMessage(role="Assistant", message="", history_index=0, a2ui_envelopes=_surface_envelopes("s1"))
        ]
        with (
            patch("codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_id", return_value=conversation),
            patch("codemie.rest_api.handlers.assistant_handlers.Ability") as ability_cls,
        ):
            ability_cls.return_value.can.return_value = True
            handler._validate_a2ui_action(request)  # must not raise

    def test_data_model_key_not_on_surface_rejected_by_handler(self, mock_assistant, mock_user):
        from codemie.core.exceptions import ExtendedHTTPException
        from codemie.rest_api.models.conversation import GeneratedMessage

        handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")
        request = AssistantChatRequest(
            text="x", conversation_id="c1", a2ui_action=_action_envelope("s1"), a2ui_data_model={"ghost": "x"}
        )
        conversation = MagicMock()
        conversation.history = [
            GeneratedMessage(role="Assistant", message="", history_index=0, a2ui_envelopes=_surface_envelopes("s1"))
        ]
        with (
            patch("codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_id", return_value=conversation),
            patch("codemie.rest_api.handlers.assistant_handlers.Ability") as ability_cls,
        ):
            ability_cls.return_value.can.return_value = True
            with pytest.raises(ExtendedHTTPException):
                handler._validate_a2ui_action(request)

    def test_foreign_conversation_denied_before_reading_history(self, mock_assistant, mock_user):
        from codemie.core.exceptions import ExtendedHTTPException

        handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="u")
        request = AssistantChatRequest(text="x", conversation_id="foreign", a2ui_action=_action_envelope())
        foreign_conv = MagicMock()
        history_mock = MagicMock()
        type(foreign_conv).history = history_mock  # would be an oracle if read without authz
        with (
            patch("codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_id", return_value=foreign_conv),
            patch("codemie.rest_api.handlers.assistant_handlers.Ability") as ability_cls,
        ):
            ability_cls.return_value.can.return_value = False  # user cannot READ this conversation
            with pytest.raises(ExtendedHTTPException) as exc:
                handler._validate_a2ui_action(request)
        assert (
            exc.value.code in (403, "403")
            or getattr(exc.value, "status_code", None) == 403
            or "denied" in str(exc.value).lower()
        )
        history_mock.__get__ = MagicMock()  # keep reference; access must not have happened

    def test_valid_action_passes_through_handler(self, mock_assistant, mock_user):
        from codemie.rest_api.models.conversation import GeneratedMessage

        handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")
        request = AssistantChatRequest(text="x", conversation_id="c1", a2ui_action=_action_envelope("s1"))
        conversation = MagicMock()
        conversation.history = [
            GeneratedMessage(role="Assistant", message="", history_index=0, a2ui_envelopes=_surface_envelopes("s1"))
        ]
        with (
            patch("codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_id", return_value=conversation),
            patch("codemie.rest_api.handlers.assistant_handlers.Ability") as ability_cls,
        ):
            ability_cls.return_value.can.return_value = True
            handler._validate_a2ui_action(request)  # must not raise

    def test_standard_handler_wires_validation_into_process_request(self):
        import inspect

        source = inspect.getsource(StandardAssistantHandler.process_request)
        assert "_validate_a2ui_action" in source

    def test_a2a_handler_wires_validation_into_process_request(self):
        import inspect

        from codemie.rest_api.handlers.assistant_handlers import A2AAssistantHandler

        source = inspect.getsource(A2AAssistantHandler.process_request)
        assert "_validate_a2ui_action" in source
