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

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from codemie.rest_api.main import app
from codemie.service.generation_manager import GenerationManager
from codemie.core.thread import CancellationReason, ThreadedGenerator
from codemie.rest_api.routers.conversation import CONVERSATION_ABORTED_SUCCESS_MSG
from codemie.rest_api.models.base import ConversationStatus
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.conversation_service import ConversationService, UpsertChatHistoryParams
from codemie.core.models import AssistantChatRequest, TokensUsage

client = TestClient(app)


@pytest.fixture
def authenticated_user():
    mock_user = User(id="test_user", name="Test User")
    app.dependency_overrides[authenticate] = lambda: mock_user
    yield mock_user
    app.dependency_overrides.clear()


def test_generation_manager_registration():
    mgr = GenerationManager()
    gen = ThreadedGenerator(conversation_id="test_conv")
    mgr.register("test_conv", gen)
    assert mgr.is_active("test_conv") is True

    mgr.unregister("test_conv", gen)
    assert mgr.is_active("test_conv") is False


def test_generation_manager_abort():
    mgr = GenerationManager()
    gen = ThreadedGenerator(conversation_id="test_conv")
    mgr.register("test_conv", gen)
    assert gen.is_closed() is False

    aborted = mgr.abort("test_conv")
    assert aborted is True
    assert gen.is_closed() is True
    assert gen.cancellation_reason == CancellationReason.ABORTED_BY_USER
    assert mgr.is_active("test_conv") is False


@patch("codemie.rest_api.routers.conversation.Conversation.find_by_id")
@patch("codemie.rest_api.routers.conversation.Ability")
def test_abort_endpoint_success(mock_ability, mock_find_by_id, authenticated_user):
    # Mock conversation ownership and write access
    conv = MagicMock(spec=Conversation)
    last_msg = MagicMock(spec=GeneratedMessage)
    last_msg.in_progress = True
    conv.history = [last_msg]
    mock_find_by_id.return_value = conv

    ability_instance = MagicMock()
    ability_instance.can.return_value = True
    mock_ability.return_value = ability_instance

    # Register a mock generator
    mgr = GenerationManager()
    gen = ThreadedGenerator(conversation_id="test_conv_123")
    mgr.register("test_conv_123", gen)

    # Call the endpoint
    response = client.post("/v1/conversations/test_conv_123/abort")
    assert response.status_code == 200
    assert response.json()["message"] == CONVERSATION_ABORTED_SUCCESS_MSG
    assert gen.is_closed() is True
    assert last_msg.in_progress is False
    assert last_msg.status == ConversationStatus.INTERRUPTED.value
    assert conv.update.called is True


@patch("codemie.service.conversation_service.ConversationMonitoringService.send_conversation_metric")
@patch("codemie.service.conversation_service.ConversationService._upsert_conversation_metrics")
@patch("codemie.service.conversation_service.ConversationService._schedule_naming_background_task")
@patch("codemie.service.conversation_service.ConversationService._find_or_create_conversation")
@patch("codemie.service.conversation_service.AgentWorkspaceService")
def test_upsert_chat_history_in_progress_skips_metrics_and_naming(
    mock_workspace,
    mock_find_or_create,
    mock_schedule_naming,
    mock_upsert_metrics,
    mock_send_metric,
):
    conv = MagicMock(spec=Conversation)
    conv.conversation_id = "test_conv"
    conv.finished_at = None
    conv.history = []
    mock_find_or_create.return_value = (conv, True, True)

    req = AssistantChatRequest(text="Hello", conversation_id="test_conv")
    assistant = MagicMock()
    assistant.id = "ast_1"
    assistant.project = "default"
    assistant.llm_model_type = "claude"
    user = User(id="user_1")

    ConversationService.upsert_chat_history(
        UpsertChatHistoryParams(
            assistant_response="",
            time_elapsed=0.0,
            tokens_usage=TokensUsage(input_tokens=0, output_tokens=0, money_spent=0),
            request=req,
            assistant=assistant,
            user=user,
            thoughts=[],
            in_progress=True,
        )
    )

    # Metrics and naming MUST NOT be emitted when in_progress=True
    assert mock_send_metric.called is False
    assert mock_upsert_metrics.called is False
    assert mock_schedule_naming.called is False
    assert conv.save.called is True


@patch("codemie.service.conversation_service.ConversationMonitoringService.send_conversation_metric")
@patch("codemie.service.conversation_service.ConversationService._upsert_conversation_metrics")
@patch("codemie.service.conversation_service.ConversationService._schedule_naming_background_task")
@patch("codemie.service.conversation_service.ConversationService._find_or_create_conversation")
@patch("codemie.service.conversation_service.AgentWorkspaceService")
def test_upsert_chat_history_finished_triggers_metrics_and_naming(
    mock_workspace,
    mock_find_or_create,
    mock_schedule_naming,
    mock_upsert_metrics,
    mock_send_metric,
):
    conv = MagicMock(spec=Conversation)
    conv.conversation_id = "test_conv"
    conv.finished_at = None
    conv.history = []
    mock_find_or_create.return_value = (conv, False, True)

    req = AssistantChatRequest(text="Hello", conversation_id="test_conv")
    assistant = MagicMock()
    assistant.id = "ast_1"
    assistant.project = "default"
    assistant.llm_model_type = "claude"
    user = User(id="user_1")

    ConversationService.upsert_chat_history(
        UpsertChatHistoryParams(
            assistant_response="Hi there!",
            time_elapsed=1.5,
            tokens_usage=TokensUsage(input_tokens=10, output_tokens=20, money_spent=0.01),
            request=req,
            assistant=assistant,
            user=user,
            thoughts=[],
            in_progress=False,
        )
    )

    # Metrics and naming MUST be emitted when in_progress=False
    assert mock_send_metric.called is True
    assert mock_upsert_metrics.called is True
    assert mock_schedule_naming.called is True
    assert conv.update.called is True


def test_drain_and_save_chat_history_aborted_by_user():
    from codemie.rest_api.handlers.assistant_handlers import StandardAssistantHandler

    assistant = MagicMock()
    user = User(id="user_1")
    handler = StandardAssistantHandler(assistant=assistant, user=user, request_uuid="uuid-123")
    handler.save_chat_history = MagicMock()

    gen = ThreadedGenerator(conversation_id="conv_abort")
    gen.close(reason=CancellationReason.ABORTED_BY_USER)

    req = AssistantChatRequest(text="Hello", conversation_id="conv_abort")

    handler._drain_and_save_chat_history(
        generator_queue=gen,
        request=req,
        execution_start=0.0,
        last_response=None,
        a2ui_envelopes=None,
    )

    assert handler.save_chat_history.called is True
    data_saved = handler.save_chat_history.call_args[0][0]
    assert data_saved.status == ConversationStatus.INTERRUPTED
    assert data_saved.in_progress is False


def test_drain_and_save_chat_history_exception_saves_error_status():
    from codemie.rest_api.handlers.assistant_handlers import StandardAssistantHandler

    assistant = MagicMock()
    user = User(id="user_1")
    handler = StandardAssistantHandler(assistant=assistant, user=user, request_uuid="uuid-123")
    handler.save_chat_history = MagicMock()

    gen = ThreadedGenerator(conversation_id="conv_err")
    gen.queue.put(RuntimeError("LLM failed"))

    req = AssistantChatRequest(text="Hello", conversation_id="conv_err")

    handler._drain_and_save_chat_history(
        generator_queue=gen,
        request=req,
        execution_start=0.0,
        last_response=None,
        a2ui_envelopes=None,
    )

    assert handler.save_chat_history.called is True
    data_saved = handler.save_chat_history.call_args[0][0]
    assert data_saved.status == ConversationStatus.ERROR
    assert data_saved.in_progress is False


@patch("codemie.service.conversation_service.ConversationMonitoringService.send_conversation_metric")
@patch("codemie.service.conversation_service.ConversationService._upsert_conversation_metrics")
@patch("codemie.service.conversation_service.ConversationService._find_or_create_conversation")
@patch("codemie.service.conversation_service.AgentWorkspaceService")
def test_upsert_chat_history_preserves_interrupted_status(
    mock_workspace,
    mock_find_or_create,
    mock_upsert_metrics,
    mock_send_metric,
):
    conv = MagicMock(spec=Conversation)
    conv.conversation_id = "test_conv_abort"
    conv.finished_at = None
    last_msg = MagicMock()
    last_msg.status = ConversationStatus.INTERRUPTED.value
    last_msg.history_index = 0
    conv.history = [last_msg]
    mock_find_or_create.return_value = (conv, False, False)

    req = AssistantChatRequest(text="Hello", conversation_id="test_conv_abort", history_index=0)
    assistant = MagicMock()
    assistant.id = "ast_1"
    assistant.project = "default"
    assistant.llm_model_type = "claude"
    user = User(id="user_1")

    ConversationService.upsert_chat_history(
        UpsertChatHistoryParams(
            assistant_response="Late finished chunk",
            time_elapsed=2.0,
            tokens_usage=TokensUsage(input_tokens=10, output_tokens=20, money_spent=0.01),
            request=req,
            assistant=assistant,
            user=user,
            thoughts=[],
            status=ConversationStatus.SUCCESS,
            in_progress=False,
        )
    )

    assert conv.update_chat_history.called is True
    turn_data = conv.update_chat_history.call_args[0][0]
    assert turn_data.status == ConversationStatus.INTERRUPTED


@patch("codemie.rest_api.handlers.assistant_handlers.AssistantService.build_agent")
def test_handle_stream_build_agent_failure_persists_error_and_unregisters(mock_build_agent):
    from codemie.rest_api.handlers.assistant_handlers import StandardAssistantHandler

    mock_build_agent.side_effect = RuntimeError("Agent build failed")

    assistant = MagicMock()
    user = User(id="user_1")
    handler = StandardAssistantHandler(assistant=assistant, user=user, request_uuid="uuid-stream-fail")
    handler.save_chat_history = MagicMock()

    raw_req = MagicMock()
    raw_req.state = MagicMock()
    raw_req.headers = {}

    req = AssistantChatRequest(text="Hello", conversation_id="conv_build_fail")

    gm = GenerationManager()
    with pytest.raises(RuntimeError, match="Agent build failed"):
        handler._handle_stream(request=req, raw_request=raw_req, execution_start=0.0)

    # Verify GenerationManager is cleaned up
    assert gm.is_active("conv_build_fail") is False

    # Verify save_chat_history called twice: initial in_progress=True, then failure with status=ERROR and in_progress=False
    assert handler.save_chat_history.call_count == 2
    data_saved = handler.save_chat_history.call_args_list[1][0][0]
    assert data_saved.status == ConversationStatus.ERROR
    assert data_saved.in_progress is False


def test_build_chat_history_messages_response_time_null_when_in_progress():
    from codemie.rest_api.models.conversation import Conversation
    from codemie.service.conversation_service import ChatTurnData
    from codemie.rest_api.models.base import ConversationStatus

    turn = ChatTurnData(
        user_message_received_at=None,
        user_query_raw="Raw User Prompt",
        user_query="User Prompt",
        assistant_response="Draft response...",
        input_tokens=10,
        output_tokens=5,
        money_spent=0.0005,
        time_elapsed=12.34,
        history_index=1,
        thoughts=[],
        assistant_id="ast_123",
        file_names=[],
        status=ConversationStatus.SUCCESS,
        in_progress=True,
    )

    user_msg, assistant_msg = Conversation._build_chat_history_messages(turn)
    assert assistant_msg.response_time is None
    assert assistant_msg.in_progress is True


def test_build_chat_history_messages_response_time_preserved_when_finished():
    from codemie.rest_api.models.conversation import Conversation
    from codemie.service.conversation_service import ChatTurnData
    from codemie.rest_api.models.base import ConversationStatus

    turn = ChatTurnData(
        user_message_received_at=None,
        user_query_raw="Raw User Prompt",
        user_query="User Prompt",
        assistant_response="Completed response",
        input_tokens=10,
        output_tokens=15,
        money_spent=0.001,
        time_elapsed=26.13,
        history_index=1,
        thoughts=[],
        assistant_id="ast_123",
        file_names=[],
        status=ConversationStatus.SUCCESS,
        in_progress=False,
    )

    user_msg, assistant_msg = Conversation._build_chat_history_messages(turn)
    assert assistant_msg.response_time == 26.13
    assert assistant_msg.in_progress is False


@patch("codemie.rest_api.routers.conversation.Conversation.find_by_id")
def test_conversation_stream_not_found(mock_find_by_id, authenticated_user):
    mock_find_by_id.return_value = None
    response = client.get("/v1/conversations/non_existent_id/stream")
    assert response.status_code == 404


@patch("codemie.rest_api.routers.conversation.Conversation.find_by_id")
@patch("codemie.rest_api.routers.conversation.Ability")
def test_conversation_stream_access_denied(mock_ability, mock_find_by_id, authenticated_user):
    mock_find_by_id.return_value = MagicMock(spec=Conversation)
    ability_instance = MagicMock()
    ability_instance.can.return_value = False
    mock_ability.return_value = ability_instance

    response = client.get("/v1/conversations/conv_123/stream")
    assert response.status_code == 401


@patch("codemie.rest_api.routers.conversation.Conversation.find_by_id")
@patch("codemie.rest_api.routers.conversation.Ability")
def test_conversation_stream_no_active_generator(mock_ability, mock_find_by_id, authenticated_user):
    mock_find_by_id.return_value = MagicMock(spec=Conversation)
    ability_instance = MagicMock()
    ability_instance.can.return_value = True
    mock_ability.return_value = ability_instance

    response = client.get("/v1/conversations/conv_empty/stream")
    assert response.status_code == 200
    assert response.text == ""


@patch("codemie.rest_api.routers.conversation.Conversation.find_by_id")
@patch("codemie.rest_api.routers.conversation.Ability")
def test_conversation_stream_replays_and_completes(mock_ability, mock_find_by_id, authenticated_user):
    mock_find_by_id.return_value = MagicMock(spec=Conversation)
    ability_instance = MagicMock()
    ability_instance.can.return_value = True
    mock_ability.return_value = ability_instance

    mgr = GenerationManager()
    gen = ThreadedGenerator(conversation_id="conv_stream_test")
    mgr.register("conv_stream_test", gen)

    chunk1 = '{"generated_chunk": "Hello "}'
    chunk2 = '{"generated_chunk": "World", "last": true}'
    gen.send(chunk1)
    gen.send(chunk2)
    gen.close()

    response = client.get("/v1/conversations/conv_stream_test/stream")
    assert response.status_code == 200
    assert f"{chunk1}\n{chunk2}\n" == response.text

    mgr.unregister("conv_stream_test", gen)


def test_generation_manager_get_generator():
    mgr = GenerationManager()
    assert mgr.get_generator("non_existent_mgr") is None

    gen = ThreadedGenerator(conversation_id="mgr_test_conv")
    mgr.register("mgr_test_conv", gen)
    assert mgr.get_generator("mgr_test_conv") is gen

    gen.close()
    assert mgr.get_generator("mgr_test_conv") is gen

    mgr.unregister("mgr_test_conv", gen)
    assert mgr.get_generator("mgr_test_conv") is None
