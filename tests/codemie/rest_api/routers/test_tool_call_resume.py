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

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_assistant():
    a = MagicMock()
    a.id = "asst_1"
    a.tool_permissions = None
    return a


@pytest.fixture
def mock_user():
    u = MagicMock()
    u.id = "user_1"
    return u


@pytest.fixture
def mock_raw_request():
    req = MagicMock()
    req.state.on_disconnect = MagicMock()
    return req


@pytest.fixture
def pending_event():
    from codemie.agents.tool_confirmation.models import ToolCallPendingEvent

    return ToolCallPendingEvent(
        pending_tool_call_id="call_abc",
        tool_name="some_tool",
        tool_args={"key": "value"},
        history_index=3,
        original_user_message="please run the tool",
    )


def test_resume_returns_404_when_no_checkpoint(mock_assistant):
    """When no pending checkpoint exists, the endpoint returns 404."""
    from codemie.service.conversation_checkpoint_service import ConversationCheckpointService

    with patch(
        "codemie.rest_api.routers.assistant.execution.get_assistant_by_id_or_raise", return_value=mock_assistant
    ):
        with patch.object(ConversationCheckpointService, "get_checkpoint", return_value=None):
            from codemie.rest_api.routers.assistant.execution import _resume_tool_call

            with pytest.raises(Exception) as exc_info:
                _resume_tool_call(
                    conversation_id="conv_1",
                )
            exc = exc_info.value
            # ExtendedHTTPException stores status code in .code and message in .message
            assert (
                "404" in str(exc_info.value)
                or "not found" in str(exc_info.value).lower()
                or getattr(exc, "code", None) == 404
                or "not found" in str(getattr(exc, "message", "")).lower()
            )


def test_allow_action_calls_resume_from_interrupt_on_agent(mock_assistant, mock_user, mock_raw_request, pending_event):
    """allow action delegates to agent.resume_from_interrupt() via handle_tool_call_resume."""
    from codemie.rest_api.handlers.assistant_handlers import StandardAssistantHandler
    from codemie.rest_api.models.assistant import ToolCallResumeRequest

    mock_agent = MagicMock()
    request = ToolCallResumeRequest(conversation_id="conv_1", action="allow")

    handler = StandardAssistantHandler(mock_assistant, mock_user, "req_uuid_1")

    captured = {}

    def fake_serve_data(stream, generator_queue, chat_request, execution_start, agent=None, **kwargs):
        captured["stream"] = stream
        return iter(())

    with patch(
        "codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_conversation_id",
        return_value=MagicMock(assistant_ids=["asst_1"], initial_assistant_id="asst_1"),
    ):
        with patch("codemie.rest_api.handlers.assistant_handlers.Ability.can", return_value=True):
            with patch(
                "codemie.rest_api.handlers.assistant_handlers.AssistantService.build_agent",
                return_value=mock_agent,
            ):
                with patch(
                    "codemie.service.conversation_checkpoint_service.ConversationCheckpointService.get_pending_tool_call",
                    return_value=pending_event,
                ):
                    with patch(
                        "codemie.service.conversation_checkpoint_service.ConversationCheckpointService.clear",
                    ):
                        with patch.object(StandardAssistantHandler, "_serve_data", side_effect=fake_serve_data):
                            handler.handle_tool_call_resume(request, mock_raw_request)

    # _serve_data is a generator function: the resume callable it was given only
    # runs once the returned stream is driven, mirroring real request handling.
    captured["stream"]()

    mock_agent.resume_from_interrupt.assert_called_once()


def test_deny_action_calls_reject_tool_call_on_agent(mock_assistant, mock_user, mock_raw_request, pending_event):
    """deny action delegates to agent.reject_tool_call(pending) via handle_tool_call_resume."""
    from codemie.rest_api.handlers.assistant_handlers import StandardAssistantHandler
    from codemie.rest_api.models.assistant import ToolCallResumeRequest

    mock_agent = MagicMock()
    request = ToolCallResumeRequest(conversation_id="conv_1", action="deny")

    handler = StandardAssistantHandler(mock_assistant, mock_user, "req_uuid_2")

    captured = {}

    def fake_serve_data(stream, generator_queue, chat_request, execution_start, agent=None, **kwargs):
        captured["stream"] = stream
        return iter(())

    with patch(
        "codemie.rest_api.handlers.assistant_handlers.Conversation.find_by_conversation_id",
        return_value=MagicMock(assistant_ids=["asst_1"], initial_assistant_id="asst_1"),
    ):
        with patch("codemie.rest_api.handlers.assistant_handlers.Ability.can", return_value=True):
            with patch(
                "codemie.rest_api.handlers.assistant_handlers.AssistantService.build_agent",
                return_value=mock_agent,
            ):
                with patch(
                    "codemie.service.conversation_checkpoint_service.ConversationCheckpointService.get_pending_tool_call",
                    return_value=pending_event,
                ):
                    with patch(
                        "codemie.service.conversation_checkpoint_service.ConversationCheckpointService.clear",
                    ):
                        with patch.object(StandardAssistantHandler, "_serve_data", side_effect=fake_serve_data):
                            handler.handle_tool_call_resume(request, mock_raw_request)

    captured["stream"]()

    mock_agent.reject_tool_call.assert_called_once_with(pending_event)
