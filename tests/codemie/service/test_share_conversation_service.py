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
from unittest.mock import patch, MagicMock
from datetime import datetime

from codemie.rest_api.security.user import User
from codemie.rest_api.models.conversation import Conversation
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.share.shared_conversation import SharedConversation
from codemie.service.share_conversation_service import ShareConversationService


class TestShareConversationService:
    """
    Tests for the ShareConversationService class.
    """

    @pytest.fixture
    def mock_user(self):
        """Create a mock user for testing."""
        return User(
            id="test_user_id",
            username="test_user",
            name="Test User",
            full_name="Test User",
            email="test@example.com",
            is_admin=False,
            project_names=[],
            admin_project_names=[],
            knowledge_bases=[],
            auth_token=None,
        )

    @pytest.fixture
    def mock_conversation(self):
        """Create a mock conversation for testing."""
        mock_conv = MagicMock(spec=Conversation)
        mock_conv.id = "test_conversation_id"
        mock_conv.user_id = "test_user_id"
        mock_conv.assistant_ids = ["test_assistant_id"]
        return mock_conv

    @pytest.fixture
    def mock_assistant(self):
        """Create a mock assistant for testing."""
        mock_assistant = MagicMock(spec=Assistant)
        mock_assistant.id = "test_assistant_id"
        mock_assistant.name = "Test Assistant"
        mock_assistant.icon_url = "test_icon_url"
        mock_assistant.context = ["Test context"]  # Context should be a list, not a string
        mock_assistant.conversation_starters = ["Test starter"]

        mock_toolkit = MagicMock()
        mock_tool = MagicMock()
        mock_tool.name = "test_tool"
        mock_tool.label = "Test Tool"
        mock_toolkit.tools = [mock_tool]
        mock_assistant.toolkits = [mock_toolkit]

        return mock_assistant

    @pytest.fixture
    def mock_shared_conversation(self):
        """Create a mock shared conversation for testing."""
        return MagicMock(
            spec=SharedConversation,
            id="share_test_conversation_id",
            share_id="share_test_conversation_id",
            conversation_id="test_conversation_id",
            shared_by_user_id="test_user_id",
            shared_by_user_name="Test User",
            created_at=datetime(2023, 1, 1, 12, 0, 0),
            access_count=0,
            share_token="test_token",
        )

    def test_create_share_new(self, mock_conversation, mock_user):
        """Test creating a new share for a conversation."""
        # Arrange
        with (
            patch(
                "codemie.rest_api.models.share.shared_conversation.SharedConversation.get_by_fields",
                return_value=None,
            ),
            patch(
                "codemie.rest_api.models.share.shared_conversation.SharedConversation.generate_share_token",
                return_value="test_token",
            ),
            patch("codemie.rest_api.models.share.shared_conversation.SharedConversation.save"),
            patch(
                "codemie.service.monitoring.conversation_monitoring_service.ConversationMonitoringService.send_share_conversation_metric"
            ),
        ):
            # Act
            result = ShareConversationService.share_conversation(conversation=mock_conversation, user=mock_user)

            # Assert
            assert result["share_id"] == f"share_{mock_conversation.id}"
            assert result["token"] == "test_token"
            assert "created_at" in result
            assert result["access_count"] == 0

    def test_create_share_existing(self, mock_conversation, mock_user, mock_shared_conversation):
        """Test creating a share when one already exists."""
        # Arrange
        with (
            patch(
                "codemie.rest_api.models.share.shared_conversation.SharedConversation.get_by_fields",
                return_value=mock_shared_conversation,
            ),
            patch(
                "codemie.service.monitoring.conversation_monitoring_service.ConversationMonitoringService.send_share_conversation_metric"
            ),
        ):
            # Act
            result = ShareConversationService.share_conversation(conversation=mock_conversation, user=mock_user)

            # Assert
            assert result["share_id"] == mock_shared_conversation.share_id
            assert result["token"] == mock_shared_conversation.share_token
            assert result["created_at"] == mock_shared_conversation.created_at.isoformat()
            assert result["access_count"] == mock_shared_conversation.access_count

    def test_get_shared_conversation_success(
        self, mock_user, mock_conversation, mock_shared_conversation, mock_assistant
    ):
        """Test successfully retrieving a shared conversation."""
        # Arrange
        mock_shared_conversation.conversation_id = mock_conversation.id

        with (
            patch(
                "codemie.rest_api.models.share.shared_conversation.SharedConversation.get_by_fields",
                return_value=mock_shared_conversation,
            ),
            patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=mock_conversation),
            patch("codemie.rest_api.models.assistant.Assistant.get_by_ids", return_value=[mock_assistant]),
            patch.object(mock_shared_conversation, "increment_access_count"),
            patch(
                "codemie.service.monitoring.conversation_monitoring_service.ConversationMonitoringService.send_share_conversation_metric"
            ),
        ):
            # Act
            result = ShareConversationService.get_shared_conversation(token="test_token", user=mock_user)

            # Assert
            assert result["conversation"] == mock_conversation
            assert result["shared_by"] == mock_shared_conversation.shared_by_user_name
            assert result["created_at"] == mock_shared_conversation.created_at
            assert result["access_count"] == mock_shared_conversation.access_count
            assert hasattr(mock_conversation, "assistant_data")
            mock_shared_conversation.increment_access_count.assert_called_once()

    def test_get_shared_conversation_token_not_found(self, mock_user):
        """Test retrieving a shared conversation with invalid token."""
        # Arrange
        with (
            patch(
                "codemie.rest_api.models.share.shared_conversation.SharedConversation.get_by_fields", return_value=None
            ),
            patch(
                "codemie.service.monitoring.conversation_monitoring_service.ConversationMonitoringService.send_share_conversation_metric"
            ),
        ):
            # Act
            result = ShareConversationService.get_shared_conversation(token="invalid_token", user=mock_user)

            # Assert
            assert result is None

    def test_get_shared_conversation_conversation_not_found(self, mock_user, mock_shared_conversation):
        """Test retrieving a shared conversation when the conversation doesn't exist."""
        # Arrange
        with (
            patch(
                "codemie.rest_api.models.share.shared_conversation.SharedConversation.get_by_fields",
                return_value=mock_shared_conversation,
            ),
            patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=None),
            patch(
                "codemie.service.monitoring.conversation_monitoring_service.ConversationMonitoringService.send_share_conversation_metric"
            ),
        ):
            # Act
            result = ShareConversationService.get_shared_conversation(token="test_token", user=mock_user)

            # Assert
            assert result is None
