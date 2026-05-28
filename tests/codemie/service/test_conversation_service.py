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
    args, kwargs = mock_update_chat_history.call_args
    assert 'history_index' in kwargs, "history_index not found in kwargs"
    assert kwargs['history_index'] == 2, f"Expected history_index to be 2, got {kwargs['history_index']}"

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
    assert detail.conversation_starters == []
