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

import contextvars

import pytest
from unittest.mock import MagicMock, patch

from fastapi import BackgroundTasks

from datetime import datetime

from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import AssistantChatRequest, UpdateConversationRequest, UpdateAiMessageRequest, TokensUsage
from codemie.rest_api.models.assistant import Assistant
from codemie.service.chat_naming_service import ChatNamingService
from codemie.service.conversation_service import ConversationService
from codemie.service.llm_service.llm_service import LLMService
from codemie.rest_api.models.conversation import (
    Conversation,
    ConversationListItem,
    ConversationMetrics,
    GeneratedMessage,
    UpsertHistoryRequest,
)


@pytest.fixture
def mock_admin_user():
    user = MagicMock()
    user.is_admin = True
    user.name = "name"
    user.id = "id"

    return user


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.is_admin = False
    user.id = "12"
    user.name = "123"
    user.current_project = None

    return user


@pytest.fixture
def mock_assistant():
    return Assistant(
        id="id",
        name="test_assistant",
        description="Test Assistant",
        project="test",
        toolkits=[],
        system_prompt="",
        llm_model_type="test_model",
        slug="test",
    )


@pytest.fixture
def mock_conversation():
    return Conversation(
        id="456",
        conversation_id="456",
        name="Test Conversation",
        assistant_ids=["234", "123"],
        history=[
            GeneratedMessage(
                history_index=0,
                message="Hello",
                role="User",
            ),
            GeneratedMessage(
                history_index=0,
                message="Hello back",
                role="Assistant",
            ),
        ],
    )


@pytest.fixture
def mock_conversation_metrics():
    return ConversationMetrics(
        id="456",
        conversation_id="456",
    )


@pytest.fixture
def mock_request():
    return AssistantChatRequest(
        text="Hello",
        history=[],
        file_names=["aW1hZ2UvcG5nX3Rlc3RfdGVzdC1pbWFnZS5wbmc="],
        system_prompt="",
        llm_model=LLMService.BASE_NAME_GPT_41,
    )


@pytest.fixture
def mock_update_request():
    return UpdateConversationRequest(
        name="New Name",
        llm_model=LLMService.BASE_NAME_GPT_41,
        enable_image_generation=True,
        image_generation_model="gpt-image-1",
        pinned=True,
        folder="test",
        active_assistant_id="123",
    )


@patch("codemie.service.conversation_service.ConversationFolder.touch_folder")
@patch("codemie.service.conversation_service.Conversation.update")
def test_conversation_service_update(
    mock_update,
    mock_touch_folder,
    mock_update_request,
    mock_conversation,
):
    mock_update.return_value = True

    conversation = ConversationService.update_conversation(
        mock_conversation,
        request=mock_update_request,
    )

    mock_update.assert_called()
    mock_touch_folder.assert_called_once_with("test", mock_conversation.user_id)
    assert conversation.conversation_name == "New Name"
    assert conversation.llm_model == LLMService.BASE_NAME_GPT_41
    assert conversation.enable_image_generation is True
    assert conversation.image_generation_model == "gpt-image-1"
    assert conversation.pinned
    assert conversation.folder == "test"
    assert conversation.assistant_ids == ["123", "234"]


@patch("codemie.service.conversation_service.Conversation.update")
def test_conversation_service_update_allows_clearing_image_generation_model(mock_update, mock_conversation):
    mock_update.return_value = True
    mock_conversation.enable_image_generation = True
    mock_conversation.image_generation_model = "old-image-model"

    request = UpdateConversationRequest(enable_image_generation=False, image_generation_model=None)

    conversation = ConversationService.update_conversation(
        mock_conversation,
        request=request,
    )

    assert conversation.enable_image_generation is False
    assert conversation.image_generation_model is None


@patch("codemie.service.conversation_service.Conversation.update")
def test_conversation_service_update_ai_message(
    mock_update,
    mock_update_request,
    mock_conversation,
):
    mock_update.return_value = True

    conversation = ConversationService.update_conversation_ai_message(
        mock_conversation,
        0,
        request=UpdateAiMessageRequest(message="New Message", message_index=0),
    )

    mock_update.assert_called()
    assert conversation.history[3].message == "New Message"


def test_update_conversation_ai_message_raises_409_when_finished():
    conversation = Conversation(
        id="conv-1",
        conversation_id="conv-1",
        user_id="u1",
        history=[],
        finished_at=datetime(2026, 8, 11, 12, 0, 0),
    )
    request = UpdateAiMessageRequest(message_index=0, message="edited")

    with pytest.raises(ExtendedHTTPException) as exc_info:
        ConversationService.update_conversation_ai_message(conversation, history_index=0, request=request)
    assert exc_info.value.code == 409


@patch("codemie.service.conversation_service.AgentWorkspaceService.sync_uploaded_files")
@patch("codemie.rest_api.models.conversation.Conversation.find_by_id")
def test_upsert_chat_history_raises_409_when_finished(
    mock_conv_find, mock_sync_uploaded_files, mock_user, mock_assistant
):
    finished_conversation = Conversation(
        id="conv-1",
        conversation_id="conv-1",
        user_id=mock_user.id,
        history=[],
        finished_at=datetime(2026, 8, 11, 12, 0, 0),
    )
    mock_conv_find.return_value = finished_conversation
    request = AssistantChatRequest(conversation_id="conv-1", text="hello", history=[], file_names=[])

    with pytest.raises(ExtendedHTTPException) as exc_info:
        ConversationService.upsert_chat_history(
            assistant_response="hi",
            time_elapsed=1.0,
            tokens_usage=TokensUsage(input_tokens=1, output_tokens=1, money_spent=0.0),
            request=request,
            assistant=mock_assistant,
            user=mock_user,
            thoughts=[],
        )
    assert exc_info.value.code == 409


@patch("codemie.rest_api.models.conversation.Conversation.find_by_id")
def test_upsert_conversation_with_history_raises_409_when_finished(mock_conv_find, mock_user):
    finished_conversation = Conversation(
        id="conv-1",
        conversation_id="conv-1",
        user_id=mock_user.id,
        history=[],
        finished_at=datetime(2026, 8, 11, 12, 0, 0),
    )
    mock_conv_find.return_value = finished_conversation
    request = UpsertHistoryRequest(assistant_id="asst-1", history=[GeneratedMessage(message="hi", role="User")])

    with pytest.raises(ExtendedHTTPException) as exc_info:
        ConversationService.upsert_conversation_with_history("conv-1", request, mock_user)
    assert exc_info.value.code == 409


def _finish_session_mock(*, rowcount=1, existing=True):
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.execute.return_value.rowcount = rowcount
    session.get.return_value = MagicMock() if existing else None
    return session


@patch("codemie.rest_api.models.conversation.Conversation.find_by_id")
def test_finish_conversation_marks_finished_and_returns_result(mock_conv_find):
    conversation = Conversation(
        id="conv-1",
        conversation_id="conv-1",
        user_id="u1",
        history=[],
    )
    mock_conv_find.return_value = conversation
    session = _finish_session_mock(rowcount=1)
    with patch("codemie.service.conversation_service.get_session", return_value=session):
        result = ConversationService.finish_conversation("conv-1")
    assert result.conversation_id == "conv-1"
    assert result.finished_at is not None
    assert not hasattr(result, "already_finished")
    assert not hasattr(result, "error")


@patch("codemie.rest_api.models.conversation.Conversation.find_by_id")
def test_finish_conversation_raises_409_when_already_done(mock_conv_find):
    finished_at = datetime(2026, 8, 11, 12, 0, 0)
    conversation = Conversation(
        id="conv-1",
        conversation_id="conv-1",
        user_id="user-1",
        finished_at=finished_at,
    )
    mock_conv_find.return_value = conversation
    with pytest.raises(ExtendedHTTPException) as exc_info:
        ConversationService.finish_conversation("conv-1")
    assert exc_info.value.code == 409


@patch("codemie.rest_api.models.conversation.Conversation.find_by_id")
def test_finish_conversation_raises_404_when_not_found(mock_conv_find):
    mock_conv_find.return_value = None
    with pytest.raises(ExtendedHTTPException) as exc_info:
        ConversationService.finish_conversation("missing-id")
    assert exc_info.value.code == 404


def test_finish_conversations_bulk_mixed_results():
    """Bulk finish: new → finished_at set; already-finished → already_finished=True, error=None; missing → error=not_found."""
    already_finished_at = datetime(2026, 8, 11, 12, 0, 0)

    # Simulate DB rows: conv-1 active, conv-2 already finished, conv-3 not in DB
    mock_rows = [
        MagicMock(id="conv-1", finished_at=None),
        MagicMock(id="conv-2", finished_at=already_finished_at),
    ]

    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=mock_session)
    mock_session.__exit__ = MagicMock(return_value=False)
    mock_session.exec.return_value.all.return_value = mock_rows
    mock_session.execute.return_value.scalars.return_value.all.return_value = ["conv-1"]

    with patch("codemie.service.conversation_service.get_session", return_value=mock_session):
        results = ConversationService.finish_conversations_bulk(["conv-1", "conv-2", "conv-3"])

    by_id = {r.conversation_id: r for r in results}
    assert by_id["conv-1"].finished_at is not None and by_id["conv-1"].error is None
    assert by_id["conv-2"].already_finished is True and by_id["conv-2"].error is None
    assert by_id["conv-3"].error == "not_found"


@patch("codemie.rest_api.models.conversation.Conversation.find_by_id")
def test_finish_conversation_does_not_merge_full_row(mock_conv_find):
    conversation = Conversation(
        id="conv-1",
        conversation_id="conv-1",
        user_id="u1",
        history=[],
    )
    mock_conv_find.return_value = conversation
    session = _finish_session_mock(rowcount=1)
    with (
        patch("codemie.service.conversation_service.get_session", return_value=session),
        patch.object(Conversation, "update") as mock_update,
    ):
        result = ConversationService.finish_conversation("conv-1")
    mock_update.assert_not_called()
    session.execute.assert_called_once()
    assert result.conversation_id == "conv-1"
    assert result.finished_at is not None
    assert not hasattr(result, "already_finished")
    assert not hasattr(result, "error")


@patch("codemie.rest_api.models.conversation.Conversation.find_by_id")
def test_finish_conversation_raises_404_when_deleted_before_persist(mock_conv_find):
    conversation = Conversation(
        id="conv-1",
        conversation_id="conv-1",
        user_id="u1",
        history=[],
    )
    mock_conv_find.return_value = conversation
    session = _finish_session_mock(rowcount=0, existing=False)
    with (
        patch("codemie.service.conversation_service.get_session", return_value=session),
        patch.object(Conversation, "update", return_value=None),
    ):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            ConversationService.finish_conversation("conv-1")
    assert exc_info.value.code == 404


def test_finish_conversations_bulk_unmatched_update_is_already_finished():
    already_finished_at = datetime(2026, 8, 11, 13, 0, 0)
    select_rows = [MagicMock(id="conv-1", finished_at=None)]
    followup_rows = [MagicMock(id="conv-1", finished_at=already_finished_at)]

    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    select_result = MagicMock()
    select_result.all.return_value = select_rows
    followup_result = MagicMock()
    followup_result.all.return_value = followup_rows
    session.exec.side_effect = [select_result, followup_result]
    session.execute.return_value.scalars.return_value.all.return_value = []

    with patch("codemie.service.conversation_service.get_session", return_value=session):
        results = ConversationService.finish_conversations_bulk(["conv-1"])

    assert results[0].already_finished is True
    assert results[0].finished_at == already_finished_at
    assert results[0].error is None


def test_finish_conversations_bulk_unmatched_update_deleted_is_not_found():
    select_rows = [MagicMock(id="conv-1", finished_at=None)]

    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    select_result = MagicMock()
    select_result.all.return_value = select_rows
    followup_result = MagicMock()
    followup_result.all.return_value = []
    session.exec.side_effect = [select_result, followup_result]
    session.execute.return_value.scalars.return_value.all.return_value = []

    with patch("codemie.service.conversation_service.get_session", return_value=session):
        results = ConversationService.finish_conversations_bulk(["conv-1"])

    assert results[0].error == "not_found"
    assert results[0].finished_at is None


def test_remove_conversation_history_index_raises_409_when_finished(mock_conversation):
    mock_conversation.finished_at = datetime(2026, 8, 11, 12, 0, 0)
    with pytest.raises(ExtendedHTTPException) as exc_info:
        ConversationService.remove_conversation_history_index(mock_conversation, 0)
    assert exc_info.value.code == 409


def test_clear_conversation_history_raises_409_when_finished(mock_conversation):
    mock_conversation.finished_at = datetime(2026, 8, 11, 12, 0, 0)
    with pytest.raises(ExtendedHTTPException) as exc_info:
        ConversationService.clear_conversation_history(mock_conversation)
    assert exc_info.value.code == 409


@patch("codemie.service.conversation_service.ConversationFolder.search_by_name_and_user", return_value=[])
@patch("codemie.service.conversation_service.Conversation.search_by_name_and_user")
def test_search_conversations_includes_finished_state(mock_search_chats, _mock_search_folders):
    finished_at = datetime(2026, 8, 11, 12, 0, 0)
    mock_search_chats.return_value = [
        ConversationListItem(
            id="chat-1",
            name="done chat",
            date=finished_at,
            update_date=finished_at,
            finished_at=finished_at,
        )
    ]
    result = ConversationService.search_conversations("user-1", "done")
    assert len(result.items) == 1
    assert result.items[0].finished_at == finished_at


@patch("codemie.rest_api.models.conversation.ConversationMetrics.calculate_metrics")
@patch("codemie.rest_api.models.conversation_folder.ConversationFolder.get_by_folder")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.get_by_conversation_id")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.save")
@patch("codemie.rest_api.models.conversation.Conversation.save")
def test_conversation_service_create(
    mock_conv_save,
    mock_metrics_save,
    mock_metrics_get,
    mock_folder_get,
    mock_calculate_metrics,
    mock_update_request,
    mock_conversation,
    mock_user,
):
    # Mock metrics get to raise KeyError (new conversation)
    mock_metrics_get.side_effect = KeyError("Metrics not found")
    # Mock folder get to return None (no existing folder)
    mock_folder_get.return_value = None

    ConversationService.create_conversation(mock_user, "123")

    mock_metrics_save.assert_called()
    mock_conv_save.assert_called()


@patch("codemie.rest_api.models.conversation.ConversationMetrics.calculate_metrics")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.save")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.update")
@patch("codemie.rest_api.models.conversation.Conversation.update")
@patch("codemie.rest_api.models.conversation.Conversation.find_by_id")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.get_by_conversation_id")
@patch("codemie.service.conversation_service.AgentWorkspaceService.sync_uploaded_files")
def test_index_service_run_visible_to_admin_user(
    mock_sync_uploaded_files,
    mock_metrics_get,
    mock_conv_find,
    mock_conv_update,
    mock_metrics_update,
    mock_metrics_save,
    mock_calculate_metrics,
    mock_request,
    mock_assistant,
    mock_admin_user,
    mock_conversation,
    mock_conversation_metrics,
):
    mock_conv_update.return_value = True
    mock_metrics_update.return_value = True
    mock_conv_find.return_value = mock_conversation
    mock_metrics_get.return_value = mock_conversation_metrics

    ConversationService.upsert_chat_history(
        assistant_response="",
        user=mock_admin_user,
        thoughts=[],
        time_elapsed=0,
        tokens_usage=TokensUsage(output_tokens=0, input_tokens=0, money_spent=0.0),
        assistant=mock_assistant,
        request=mock_request,
    )

    mock_conv_update.assert_called()
    mock_metrics_save.assert_called()
    mock_sync_uploaded_files.assert_called_once_with(
        conversation_id=mock_request.conversation_id,
        file_urls=mock_request.file_names,
        user=mock_admin_user,
    )


@patch(
    "codemie.service.monitoring.conversation_monitoring_service.ConversationMonitoringService.send_conversation_metric"
)
@patch("codemie.rest_api.models.conversation.ConversationMetrics.calculate_metrics")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.update")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.save")
@patch("codemie.rest_api.models.conversation.Conversation.update")
@patch("codemie.rest_api.models.conversation.Conversation.find_by_id")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.get_by_conversation_id")
@patch("codemie.rest_api.models.conversation.Conversation.update_chat_history")
def test_upsert_chat_history_with_missing_history_index(
    mock_update_chat_history,
    mock_metrics_get,
    mock_conv_find,
    mock_conv_update,
    mock_metrics_save,
    mock_metrics_update,
    mock_calculate_metrics,
    _mock_metrics,
):
    mock_assistant = MagicMock()
    mock_assistant.id = "assistant-id"
    mock_assistant.project = "test-project"
    mock_assistant.llm_model_type = "test-model"

    mock_admin_user = MagicMock()
    mock_admin_user.id = "user-id"
    mock_admin_user.name = "user-name"
    # Create a conversation with existing messages at history_index 0 and 1
    conversation = Conversation(
        id="test-id",
        conversation_id="test-id",
        history=[
            GeneratedMessage(history_index=0, message="Hello", role="User"),
            GeneratedMessage(history_index=0, message="Hi there", role="Assistant"),
            GeneratedMessage(history_index=1, message="How are you?", role="User"),
            GeneratedMessage(history_index=1, message="I'm fine, thanks!", role="Assistant"),
        ],
    )
    conversation_metrics = ConversationMetrics(conversation_id="test-id")

    # Set up the mock returns
    mock_conv_find.return_value = conversation
    mock_metrics_get.return_value = conversation_metrics
    mock_conv_update.return_value = True
    mock_metrics_update.return_value = True

    # Create a request without history_index
    request = AssistantChatRequest(
        conversation_id="test-id",
        text="What's the weather like?",
        history=[],
    )

    # Call the method
    ConversationService.upsert_chat_history(
        assistant_response="It's sunny today!",
        user=mock_admin_user,
        thoughts=[],
        time_elapsed=0,
        tokens_usage=TokensUsage(output_tokens=0, input_tokens=0, money_spent=0.0),
        assistant=mock_assistant,
        request=request,
        status=None,
    )

    # Verify that update_chat_history was called with history_index=2
    mock_update_chat_history.assert_called_once()
    turn = mock_update_chat_history.call_args.args[0]
    assert turn.history_index == 2, f"Expected history_index to be 2, got {turn.history_index}"

    # Verify other mocks were called
    mock_conv_update.assert_called_once()


@patch("codemie.rest_api.models.assistant.Assistant.get_by_ids")
def test_build_new_conversation_with_assistant(
    mock_get_by_ids,
    mock_user,
):
    from codemie.rest_api.models.assistant import AssistantType

    mock_tool_1 = MagicMock()
    mock_tool_1.name = "git"
    mock_tool_1.label = "Git"
    mock_tool_2 = MagicMock()
    mock_tool_2.name = "jira"
    mock_tool_2.label = "Jira"

    mock_toolkit = MagicMock()
    mock_toolkit.tools = [mock_tool_1, mock_tool_2]

    mock_assistant = MagicMock()
    mock_assistant.id = "asst-1"
    mock_assistant.name = "My Assistant"
    mock_assistant.type = AssistantType.CODEMIE
    mock_assistant.icon_url = "http://icon"
    mock_assistant.context = ["some context"]
    mock_assistant.conversation_starters = ["Hello", "How can you help?"]
    mock_assistant.toolkits = [mock_toolkit]
    mock_assistant.enable_image_generation = True
    mock_assistant.image_generation_model = "gpt-image-1"

    mock_get_by_ids.return_value = [mock_assistant]

    result = ConversationService.build_new_conversation(
        user=mock_user, initial_assistant_id="asst-1", folder="my-folder"
    )

    assert result.id == "new"
    assert result.folder == "my-folder"
    assert result.assistant_ids == ["asst-1"]
    assert result.initial_assistant_id == "asst-1"
    assert result.is_workflow_conversation is False
    assert len(result.assistant_data) == 1

    detail = result.assistant_data[0]
    assert detail.assistant_id == "asst-1"
    assert detail.assistant_name == "My Assistant"
    assert detail.assistant_icon == "http://icon"
    assert detail.assistant_type == AssistantType.CODEMIE
    assert detail.context == ["some context"]
    assert detail.conversation_starters == ["Hello", "How can you help?"]
    assert len(detail.tools) == 2
    assert detail.tools[0].name == "git"
    assert detail.tools[0].label == "Git"
    assert detail.tools[1].name == "jira"
    assert detail.tools[1].label == "Jira"
    assert result.enable_image_generation is True
    assert result.image_generation_model == "gpt-image-1"


@patch(
    "codemie.service.monitoring.conversation_monitoring_service.ConversationMonitoringService.send_conversation_metric"
)
@patch("codemie.rest_api.models.conversation.ConversationMetrics.calculate_metrics")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.update")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.save")
@patch("codemie.rest_api.models.conversation.Conversation.update")
@patch("codemie.rest_api.models.conversation.Conversation.find_by_id")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.get_by_conversation_id")
@patch("codemie.service.conversation_service.AgentWorkspaceService.sync_uploaded_files")
def test_upsert_chat_history_reuses_history_index_and_replaces_existing_turn_on_repeat_save(
    mock_sync_uploaded_files,
    mock_metrics_get,
    mock_conv_find,
    mock_conv_update,
    mock_metrics_save,
    mock_metrics_update,
    mock_calculate_metrics,
    _mock_metrics,
):
    mock_assistant = MagicMock()
    mock_assistant.id = "assistant-id"
    mock_assistant.project = "test-project"
    mock_assistant.llm_model_type = "test-model"

    mock_user = MagicMock()
    mock_user.id = "user-id"
    mock_user.name = "user-name"

    conversation = Conversation(
        id="test-id",
        conversation_id="test-id",
        history=[],
    )
    conversation_metrics = ConversationMetrics(conversation_id="test-id")

    mock_conv_find.return_value = conversation
    mock_metrics_get.return_value = conversation_metrics
    mock_conv_update.return_value = True
    mock_metrics_update.return_value = True

    request = AssistantChatRequest(
        conversation_id="test-id",
        text="Build the deck",
        history=[],
        file_names=[],
    )

    ConversationService.upsert_chat_history(
        assistant_response="Agent has been interrupted by client",
        user=mock_user,
        thoughts=[],
        time_elapsed=0,
        tokens_usage=TokensUsage(output_tokens=0, input_tokens=0, money_spent=0.0),
        assistant=mock_assistant,
        request=request,
        status=None,
    )

    assert request.history_index == 0
    assert len(conversation.history) == 2
    assert conversation.history[0].history_index == 0
    assert conversation.history[1].history_index == 0
    assert conversation.history[1].message == "Agent has been interrupted by client"

    ConversationService.upsert_chat_history(
        assistant_response="Presentation build completed successfully",
        user=mock_user,
        thoughts=[],
        time_elapsed=0,
        tokens_usage=TokensUsage(output_tokens=0, input_tokens=0, money_spent=0.0),
        assistant=mock_assistant,
        request=request,
        status=None,
    )

    assert request.history_index == 0
    assert len(conversation.history) == 2
    assert [message.history_index for message in conversation.history] == [0, 0]
    assert conversation.history[0].message == "Build the deck"
    assert conversation.history[1].message == "Presentation build completed successfully"
    assert mock_conv_update.call_count == 2
    assert mock_sync_uploaded_files.call_count == 2


@patch(
    "codemie.service.monitoring.conversation_monitoring_service.ConversationMonitoringService.send_conversation_metric"
)
@patch("codemie.rest_api.models.conversation.ConversationMetrics.calculate_metrics")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.update")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.save")
@patch("codemie.rest_api.models.conversation.Conversation.update")
@patch("codemie.rest_api.models.conversation.Conversation.find_by_id")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.get_by_conversation_id")
@patch("codemie.service.conversation_service.AgentWorkspaceService.sync_uploaded_files")
def test_upsert_chat_history_appends_variants_for_explicit_history_index(
    mock_sync_uploaded_files,
    mock_metrics_get,
    mock_conv_find,
    mock_conv_update,
    mock_metrics_save,
    mock_metrics_update,
    mock_calculate_metrics,
    _mock_metrics,
):
    mock_assistant = MagicMock()
    mock_assistant.id = "assistant-id"
    mock_assistant.project = "test-project"
    mock_assistant.llm_model_type = "test-model"

    mock_user = MagicMock()
    mock_user.id = "user-id"
    mock_user.name = "user-name"

    conversation = Conversation(
        id="test-id",
        conversation_id="test-id",
        history=[
            GeneratedMessage(history_index=0, message="Build the deck", role="User"),
            GeneratedMessage(history_index=0, message="Agent has been interrupted by client", role="Assistant"),
        ],
    )
    conversation_metrics = ConversationMetrics(conversation_id="test-id")

    mock_conv_find.return_value = conversation
    mock_metrics_get.return_value = conversation_metrics
    mock_conv_update.return_value = True
    mock_metrics_update.return_value = True

    first_request = AssistantChatRequest(
        conversation_id="test-id",
        text="Build the deck",
        history=[],
        file_names=[],
        history_index=0,
    )

    ConversationService.upsert_chat_history(
        assistant_response="Presentation build completed successfully",
        user=mock_user,
        thoughts=[],
        time_elapsed=0,
        tokens_usage=TokensUsage(output_tokens=0, input_tokens=0, money_spent=0.0),
        assistant=mock_assistant,
        request=first_request,
        status=None,
    )

    second_request = AssistantChatRequest(
        conversation_id="test-id",
        text="Build the deck",
        history=[],
        file_names=[],
        history_index=0,
    )

    ConversationService.upsert_chat_history(
        assistant_response="Presentation build completed with alternate layout",
        user=mock_user,
        thoughts=[],
        time_elapsed=0,
        tokens_usage=TokensUsage(output_tokens=0, input_tokens=0, money_spent=0.0),
        assistant=mock_assistant,
        request=second_request,
        status=None,
    )

    assert [message.history_index for message in conversation.history] == [0, 0, 0, 0, 0, 0]
    assert [message.message for message in conversation.history] == [
        "Build the deck",
        "Agent has been interrupted by client",
        "Build the deck",
        "Presentation build completed successfully",
        "Build the deck",
        "Presentation build completed with alternate layout",
    ]
    assert mock_conv_update.call_count == 2
    assert mock_sync_uploaded_files.call_count == 2


@patch("codemie.service.conversation_service.Conversation.update")
@patch("codemie.core.workflow_models.workflow_execution.WorkflowExecution.delete_by_conversation_ids")
@patch("codemie.service.conversation_service.Session")
@patch("codemie.core.workflow_models.workflow_execution.WorkflowExecution.get_engine")
def test_clear_conversation_history_cascades_to_workflow_executions(
    mock_get_engine,
    mock_session_cls,
    mock_delete_by_conv_ids,
    mock_update,
    mock_conversation,
):
    mock_update.return_value = True
    mock_session_instance = MagicMock()
    mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session_instance)
    mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

    result = ConversationService.clear_conversation_history(mock_conversation)

    mock_delete_by_conv_ids.assert_called_once_with(mock_session_instance, ["456"])
    mock_session_instance.commit.assert_called_once()
    assert result.history == []


@patch("codemie.core.workflow_models.workflow_config.WorkflowConfig.get_by_id")
def test_build_new_conversation_with_workflow(
    mock_get_by_id,
    mock_user,
):
    mock_workflow = MagicMock()
    mock_workflow.id = "wf-1"
    mock_workflow.name = "My Workflow"
    mock_workflow.icon_url = "http://wf-icon"
    mock_get_by_id.return_value = mock_workflow

    result = ConversationService.build_new_conversation(
        user=mock_user, initial_assistant_id="wf-1", is_workflow=True, folder="my-folder"
    )

    assert result.id == "new"
    assert result.folder == "my-folder"
    assert result.assistant_ids == ["wf-1"]
    assert result.initial_assistant_id == "wf-1"
    assert result.is_workflow_conversation is True
    assert len(result.assistant_data) == 1

    detail = result.assistant_data[0]
    assert detail.assistant_id == "wf-1"
    assert detail.assistant_name == "My Workflow"
    assert detail.assistant_icon == "http://wf-icon"
    assert detail.assistant_type is None
    assert detail.context is None
    assert detail.tools is None


@pytest.mark.parametrize(
    "content_raw,text,expected_raw",
    [
        ("", "Hello", "Hello"),
        (None, "Hello", "Hello"),
        ("<b>Hi</b>", "Hi", "<b>Hi</b>"),
        ("", "<script>alert(1)</script>", "&lt;script&gt;alert(1)&lt;/script&gt;"),
    ],
)
@patch(
    "codemie.service.monitoring.conversation_monitoring_service.ConversationMonitoringService.send_conversation_metric"
)
@patch("codemie.rest_api.models.conversation.ConversationMetrics.calculate_metrics")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.update")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.save")
@patch("codemie.rest_api.models.conversation.Conversation.update")
@patch("codemie.rest_api.models.conversation.Conversation.find_by_id")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.get_by_conversation_id")
@patch("codemie.rest_api.models.conversation.Conversation.update_chat_history")
def test_upsert_chat_history_content_raw_fallback(
    mock_update_chat_history,
    mock_metrics_get,
    mock_conv_find,
    mock_conv_update,
    mock_metrics_save,
    mock_metrics_update,
    mock_calculate_metrics,
    _mock_send_metric,
    content_raw,
    text,
    expected_raw,
):
    """When content_raw is empty or None, user_query_raw falls back to html.escape(text)."""
    mock_assistant = MagicMock()
    mock_assistant.id = "assistant-id"
    mock_assistant.project = "test-project"
    mock_assistant.llm_model_type = "test-model"

    mock_user = MagicMock()
    mock_user.id = "user-id"
    mock_user.name = "user-name"

    conversation = Conversation(id="conv-1", conversation_id="conv-1", history=[])
    mock_conv_find.return_value = conversation
    mock_metrics_get.return_value = ConversationMetrics(conversation_id="conv-1")
    mock_conv_update.return_value = True
    mock_metrics_update.return_value = True

    request = AssistantChatRequest(
        conversation_id="conv-1",
        text=text,
        content_raw=content_raw,
        history=[],
    )

    ConversationService.upsert_chat_history(
        assistant_response="response",
        user=mock_user,
        thoughts=[],
        time_elapsed=0,
        tokens_usage=TokensUsage(output_tokens=0, input_tokens=0, money_spent=0.0),
        assistant=mock_assistant,
        request=request,
    )

    mock_update_chat_history.assert_called_once()
    turn = mock_update_chat_history.call_args.args[0]
    assert turn.user_query_raw == expected_raw


@patch(
    "codemie.service.monitoring.conversation_monitoring_service.ConversationMonitoringService.send_conversation_metric"
)
@patch("codemie.rest_api.models.conversation.ConversationMetrics.calculate_metrics")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.save")
@patch("codemie.rest_api.models.conversation.Conversation.save")
@patch("codemie.rest_api.models.conversation.Conversation.find_by_id")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.get_by_conversation_id")
@patch("codemie.service.conversation_service.AgentWorkspaceService.sync_uploaded_files")
def test_upsert_chat_history_schedules_naming_task_for_new_conversation(
    mock_sync_uploaded_files,
    mock_metrics_get,
    mock_conv_find,
    mock_conv_save,
    mock_metrics_save,
    mock_calculate_metrics,
    _mock_send_metric,
    mock_request,
    mock_assistant,
    mock_admin_user,
    mock_conversation_metrics,
):
    mock_conv_find.return_value = None  # No existing conversation -> new-conversation branch
    mock_metrics_get.side_effect = KeyError("not found")
    mock_conv_save.return_value = True

    background_tasks = MagicMock(spec=BackgroundTasks)

    ConversationService.upsert_chat_history(
        assistant_response="Hi there!",
        user=mock_admin_user,
        thoughts=[],
        time_elapsed=0,
        tokens_usage=TokensUsage(output_tokens=0, input_tokens=0, money_spent=0.0),
        assistant=mock_assistant,
        request=mock_request,
        background_tasks=background_tasks,
    )

    background_tasks.add_task.assert_called_once()
    call_args, call_kwargs = background_tasks.add_task.call_args
    # Scheduled via a captured contextvars.Context.run so the naming task inherits
    # the LLM-credential context resolved at schedule time (see conversation_service.py).
    assert isinstance(call_args[0].__self__, contextvars.Context)
    assert call_args[0].__name__ == "run"
    assert call_args[1] == ChatNamingService.rename_conversation
    assert call_kwargs == {
        "conversation_id": mock_request.conversation_id,
        "first_message": mock_request.text,
        "assistant_response": "Hi there!",
        "request_id": None,
    }


@patch(
    "codemie.service.monitoring.conversation_monitoring_service.ConversationMonitoringService.send_conversation_metric"
)
@patch("codemie.rest_api.models.conversation.ConversationMetrics.calculate_metrics")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.update")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.save")
@patch("codemie.rest_api.models.conversation.Conversation.update")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.get_by_conversation_id")
@patch("codemie.service.conversation_service.AgentWorkspaceService.sync_uploaded_files")
def test_upsert_chat_history_schedules_naming_when_only_optimistic_client_name_set(
    mock_sync_uploaded_files,
    mock_metrics_get,
    mock_conv_update,
    mock_metrics_save,
    mock_metrics_update,
    mock_calculate_metrics,
    _mock_send_metric,
    mock_request,
    mock_assistant,
    mock_admin_user,
    mock_conversation_metrics,
):
    # codemie-ui optimistically PUTs a client-truncated name (matching the same
    # 50-char + "..." fallback shape) right after the first message is sent, before
    # the streaming response completes. That must not block LLM naming eligibility —
    # only a real user-chosen name should.
    pre_named_conversation = Conversation(
        id="789",
        conversation_id="789",
        conversation_name="Hello",  # == ConversationService._truncate_name(mock_request.text)
        assistant_ids=["123"],
        history=[],
    )
    mock_metrics_get.return_value = mock_conversation_metrics
    mock_conv_update.return_value = True
    mock_metrics_save.return_value = True
    mock_metrics_update.return_value = True

    background_tasks = MagicMock(spec=BackgroundTasks)

    with patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=pre_named_conversation):
        ConversationService.upsert_chat_history(
            assistant_response="Hi there!",
            user=mock_admin_user,
            thoughts=[],
            time_elapsed=0,
            tokens_usage=TokensUsage(output_tokens=0, input_tokens=0, money_spent=0.0),
            assistant=mock_assistant,
            request=mock_request,
            background_tasks=background_tasks,
        )

    background_tasks.add_task.assert_called_once()


@patch(
    "codemie.service.monitoring.conversation_monitoring_service.ConversationMonitoringService.send_conversation_metric"
)
@patch("codemie.rest_api.models.conversation.ConversationMetrics.calculate_metrics")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.update")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.save")
@patch("codemie.rest_api.models.conversation.Conversation.update")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.get_by_conversation_id")
@patch("codemie.service.conversation_service.AgentWorkspaceService.sync_uploaded_files")
def test_upsert_chat_history_does_not_schedule_naming_for_existing_named_conversation(
    mock_sync_uploaded_files,
    mock_metrics_get,
    mock_conv_update,
    mock_metrics_save,
    mock_metrics_update,
    mock_calculate_metrics,
    _mock_send_metric,
    mock_request,
    mock_assistant,
    mock_admin_user,
    mock_conversation,
    mock_conversation_metrics,
):
    mock_conversation.conversation_name = "Already named"
    mock_metrics_get.return_value = mock_conversation_metrics
    mock_conv_update.return_value = True
    mock_metrics_save.return_value = True
    mock_metrics_update.return_value = True

    background_tasks = MagicMock(spec=BackgroundTasks)

    with patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=mock_conversation):
        ConversationService.upsert_chat_history(
            assistant_response="Hi there!",
            user=mock_admin_user,
            thoughts=[],
            time_elapsed=0,
            tokens_usage=TokensUsage(output_tokens=0, input_tokens=0, money_spent=0.0),
            assistant=mock_assistant,
            request=mock_request,
            background_tasks=background_tasks,
        )

    background_tasks.add_task.assert_not_called()


@patch(
    "codemie.service.monitoring.conversation_monitoring_service.ConversationMonitoringService.send_conversation_metric"
)
@patch("codemie.rest_api.models.conversation.ConversationMetrics.calculate_metrics")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.save")
@patch("codemie.rest_api.models.conversation.Conversation.save")
@patch("codemie.rest_api.models.conversation.Conversation.find_by_id")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.get_by_conversation_id")
@patch("codemie.service.conversation_service.AgentWorkspaceService.sync_uploaded_files")
def test_upsert_chat_history_without_background_tasks_still_sets_legacy_name(
    mock_sync_uploaded_files,
    mock_metrics_get,
    mock_conv_find,
    mock_conv_save,
    mock_metrics_save,
    mock_calculate_metrics,
    _mock_send_metric,
    mock_request,
    mock_assistant,
    mock_admin_user,
):
    """Legacy non-regression: omitting background_tasks (as all pre-existing callers do) must not raise,
    and the synchronous legacy name assignment must be unaffected."""
    mock_conv_find.return_value = None
    mock_metrics_get.side_effect = KeyError("not found")
    mock_conv_save.return_value = True

    ConversationService.upsert_chat_history(
        assistant_response="Hi there!",
        user=mock_admin_user,
        thoughts=[],
        time_elapsed=0,
        tokens_usage=TokensUsage(output_tokens=0, input_tokens=0, money_spent=0.0),
        assistant=mock_assistant,
        request=mock_request,
    )

    mock_conv_save.assert_called_once()
