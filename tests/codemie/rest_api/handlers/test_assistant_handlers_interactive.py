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

"""Tests for interactive request capture in the streaming drain loop and persistence."""

from time import time
from unittest.mock import MagicMock, Mock, patch

import pytest

from codemie.chains.base import StreamedGenerationResult
from codemie.core.interactive import InteractiveRequest, InteractiveResponse
from codemie.core.models import AssistantChatRequest
from codemie.core.thread import ThreadedGenerator
from codemie.rest_api.handlers.assistant_handlers import StandardAssistantHandler
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


def test_serve_data_captures_interactive_request(mock_assistant, mock_user):
    handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")
    generator_queue = ThreadedGenerator()
    interactive_request = InteractiveRequest(request_id="r1", surface=[{"type": "button", "id": "ok", "label": "OK"}])
    generator_queue.send(StreamedGenerationResult(interactive_request=interactive_request).model_dump_json())
    generator_queue.send(StreamedGenerationResult(generated="done").model_dump_json())
    generator_queue.close()

    request = AssistantChatRequest(text="hi", stream=True)
    with patch.object(handler, "save_chat_history") as save_mock:
        chunks = _drain(handler, generator_queue, request)

    assert any('"interactive_request"' in chunk and '"r1"' in chunk for chunk in chunks)
    saved = save_mock.call_args[0][0]
    assert saved.interactive_request is not None
    assert saved.interactive_request.request_id == "r1"


def test_serve_data_without_interactive_chunk_saves_none(mock_assistant, mock_user):
    handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")
    generator_queue = ThreadedGenerator()
    generator_queue.send(StreamedGenerationResult(generated="plain text").model_dump_json())
    generator_queue.close()

    request = AssistantChatRequest(text="hi", stream=True)
    with patch.object(handler, "save_chat_history") as save_mock:
        _drain(handler, generator_queue, request)

    assert save_mock.call_args[0][0].interactive_request is None


def test_build_chat_history_messages_persists_interactive_fields():
    from codemie.rest_api.models.conversation import ChatTurnData, Conversation

    interactive_request = InteractiveRequest(request_id="r1", surface=[{"type": "button", "id": "ok", "label": "OK"}])
    interactive_response = InteractiveResponse(request_id="r0", kind="action", payload={"action": "ok"})
    user_message, assistant_message = Conversation._build_chat_history_messages(
        ChatTurnData(
            user_query="✓ OK",
            user_query_raw="✓ OK",
            assistant_id="a1",
            assistant_response="",
            thoughts=[],
            history_index=0,
            time_elapsed=1.0,
            input_tokens=1,
            output_tokens=1,
            file_names=[],
            money_spent=0.0,
            interactive_request=interactive_request,
            interactive_response=interactive_response,
        )
    )
    assert user_message.interactive_response.request_id == "r0"
    assert assistant_message.interactive_request.request_id == "r1"


def test_build_chat_history_messages_defaults_interactive_fields_to_none():
    from codemie.rest_api.models.conversation import ChatTurnData, Conversation

    user_message, assistant_message = Conversation._build_chat_history_messages(
        ChatTurnData(
            user_query="hi",
            user_query_raw="hi",
            assistant_id="a1",
            assistant_response="hello",
            thoughts=[],
            history_index=0,
            time_elapsed=1.0,
            input_tokens=1,
            output_tokens=1,
            file_names=[],
            money_spent=0.0,
        )
    )
    assert user_message.interactive_response is None
    assert assistant_message.interactive_request is None


class TestValidationCoverageAcrossHandlers:
    def test_base_validate_rejects_unknown_request_id(self, mock_assistant, mock_user):
        from codemie.core.exceptions import ExtendedHTTPException
        from codemie.core.interactive import InteractiveResponse

        handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")
        request = AssistantChatRequest(
            text="x",
            conversation_id="c-none",
            interactive_response=InteractiveResponse(request_id="ghost", kind="action", payload={"action": "ok"}),
        )
        with patch("codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_id", return_value=None):
            with pytest.raises(ExtendedHTTPException):
                handler._validate_interactive_response(request)

    def test_a2a_handler_wires_validation_into_process_request(self):
        import inspect

        from codemie.rest_api.handlers.assistant_handlers import A2AAssistantHandler

        source = inspect.getsource(A2AAssistantHandler.process_request)
        assert "_validate_interactive_response" in source

    def test_validate_is_noop_when_response_absent(self, mock_assistant, mock_user):
        """A plain (non-interactive) request must pass validation untouched — no DB read."""
        handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="test-uuid")
        request = AssistantChatRequest(text="plain message", conversation_id="c1")
        with patch("codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_id") as find_mock:
            handler._validate_interactive_response(request)  # must not raise
        find_mock.assert_not_called()

    def test_a2a_handler_accepts_plain_request(self, mock_user):
        """A2AAssistantHandler must not break on a request without interactive_response."""
        from codemie.rest_api.handlers.assistant_handlers import A2AAssistantHandler

        a2a_assistant = MagicMock()
        with patch("codemie.rest_api.handlers.assistant_handlers.RemoteAgentConnections"):
            handler = A2AAssistantHandler(assistant=a2a_assistant, user=mock_user, request_uuid="test-uuid")
        request = AssistantChatRequest(text="plain a2a", conversation_id="c1")
        # The shared validation helper (invoked first in process_request) must no-op.
        with patch("codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_id") as find_mock:
            handler._validate_interactive_response(request)
        find_mock.assert_not_called()


class TestInteractiveResponseOwnership:
    def test_foreign_conversation_denied_before_reading_history(self, mock_assistant, mock_user):
        from codemie.core.exceptions import ExtendedHTTPException
        from codemie.core.interactive import InteractiveResponse

        handler = StandardAssistantHandler(assistant=mock_assistant, user=mock_user, request_uuid="u")
        request = AssistantChatRequest(
            text="x",
            conversation_id="foreign",
            interactive_response=InteractiveResponse(request_id="r1", kind="action", payload={"action": "ok"}),
        )
        foreign_conv = MagicMock()
        foreign_conv.history = [MagicMock()]  # would be an oracle if read without authz
        with (
            patch("codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_id", return_value=foreign_conv),
            patch("codemie.rest_api.handlers.assistant_handlers.Ability") as ability_cls,
        ):
            ability_cls.return_value.can.return_value = False  # user cannot READ this conversation
            with pytest.raises(ExtendedHTTPException) as exc:
                handler._validate_interactive_response(request)
        assert (
            exc.value.code in (403, "403")
            or getattr(exc.value, "status_code", None) == 403
            or "denied" in str(exc.value).lower()
        )
