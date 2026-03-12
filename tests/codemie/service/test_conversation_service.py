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

from codemie.core.models import AssistantChatRequest, UpdateConversationRequest, UpdateAiMessageRequest, TokensUsage
from codemie.rest_api.models.assistant import Assistant
from codemie.service.conversation_service import ConversationService
from codemie.service.llm_service.llm_service import LLMService
from codemie.rest_api.models.conversation import Conversation, ConversationMetrics, GeneratedMessage


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
        name="New Name", llm_model=LLMService.BASE_NAME_GPT_41, pinned=True, folder="test", active_assistant_id="123"
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
    assert conversation.pinned
    assert conversation.folder == "test"
    assert conversation.assistant_ids == ["123", "234"]


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
def test_index_service_run_visible_to_admin_user(
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
    args, kwargs = mock_update_chat_history.call_args
    assert 'history_index' in kwargs, "history_index not found in kwargs"
    assert kwargs['history_index'] == 2, f"Expected history_index to be 2, got {kwargs['history_index']}"

    # Verify other mocks were called
    mock_conv_update.assert_called_once()
