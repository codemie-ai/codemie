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

from unittest.mock import MagicMock, patch

import pytest
import codemie.rest_api.routers.share as share_router
from codemie.rest_api.main import app
from codemie.rest_api.models.conversation import Conversation
from codemie.rest_api.security.user import User
from fastapi import status
from fastapi.testclient import TestClient

# Create test user
test_user = User(
    id="test_user_id",
    username="test_user",
    name="Test User",
    project_names=[],
    admin_project_names=[],
    knowledge_bases=[],
    auth_token=None,
)


# Override the authentication dependency
@pytest.fixture
def mock_authenticate():
    return test_user


@pytest.fixture(autouse=True)
def override_dependency(mock_authenticate):
    app.dependency_overrides[share_router.authenticate] = lambda: mock_authenticate
    yield
    app.dependency_overrides = {}


# Create test client with the overridden dependency
client = TestClient(app)


class TestShareRouter:
    @pytest.fixture
    def mock_conversation(self):
        """Create a mock conversation"""
        mock_conv = MagicMock(spec=Conversation)
        mock_conv.id = "test_conversation_id"
        mock_conv.user_id = test_user.id
        return mock_conv

    @pytest.fixture
    def mock_ability(self):
        """Mock the Ability class to authorize actions"""
        with patch("codemie.rest_api.routers.share.Ability") as mock_ability_class:
            ability_instance = MagicMock()
            ability_instance.can.return_value = True
            mock_ability_class.return_value = ability_instance
            yield mock_ability_class

    @pytest.fixture
    def mock_share_service(self):
        """Mock the ShareConversationService"""
        with patch("codemie.rest_api.routers.share.ShareConversationService") as mock_service:
            yield mock_service

    def test_create_shared_conversation_success(self, mock_conversation, mock_ability, mock_share_service):
        """Test successful creation of a shared conversation"""
        # Arrange
        share_response = {
            "share_id": "share_test_id",
            "token": "test_token_123",
            "created_at": "2023-01-01T12:00:00",
            "access_count": 0,
        }
        mock_share_service.share_conversation.return_value = share_response

        with patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=mock_conversation):
            # Act
            response = client.post("/v1/share/conversations", json={"chat_id": "test_conversation_id"})

            # Assert
            assert response.status_code == status.HTTP_201_CREATED
            assert response.json() == share_response
            mock_share_service.share_conversation.assert_called_once_with(mock_conversation, test_user)

    def test_create_shared_conversation_not_found(self, mock_ability, mock_share_service):
        """Test creating shared conversation when conversation doesn't exist"""
        # Arrange
        with patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=None):
            # Act
            response = client.post("/v1/share/conversations", json={"chat_id": "non_existent_id"})

            # Assert
            assert response.status_code == status.HTTP_404_NOT_FOUND
            response_json = response.json()
            assert (
                "not found" in response_json.get("message", "").lower()
                or "not found" in response_json.get("detail", "").lower()
                or "not found" in str(response_json).lower()
            )
            mock_share_service.share_conversation.assert_not_called()

    def test_create_shared_conversation_no_permission(self, mock_conversation, mock_share_service):
        """Test creating a shared conversation without proper permissions"""
        # Arrange
        with (
            patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=mock_conversation),
            patch("codemie.rest_api.routers.share.Ability") as mock_ability_class,
        ):
            # Setup ability mock to deny permission
            ability_instance = MagicMock()
            ability_instance.can.return_value = False
            mock_ability_class.return_value = ability_instance

            # Act
            response = client.post("/v1/share/conversations", json={"chat_id": "test_conversation_id"})

            # Assert
            assert response.status_code == status.HTTP_401_UNAUTHORIZED
            response_json = response.json()
            assert (
                "access denied" in response_json.get("message", "").lower()
                or "access denied" in response_json.get("detail", "").lower()
                or "access denied" in str(response_json).lower()
            )
            mock_share_service.share_conversation.assert_not_called()

    def test_create_shared_conversation_service_error(self, mock_conversation, mock_ability, mock_share_service):
        """Test service error during share creation"""
        # Arrange
        error_message = "Service error: Invalid conversation ID format"
        mock_share_service.share_conversation.side_effect = ValueError(error_message)

        with patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=mock_conversation):
            # Act
            response = client.post("/v1/share/conversations", json={"chat_id": "test_conversation_id"})

            # Assert
            assert response.status_code == status.HTTP_400_BAD_REQUEST
            assert response.json()["detail"] == error_message

    def test_access_shared_conversation_success(self, mock_share_service):
        """Test successfully accessing a shared conversation"""
        # Arrange - provide a serializable dictionary instead of mock objects
        conversation_dict = {
            "id": "test_conversation_id",
            "title": "Test Conversation",
            "messages": [],
            "assistant_data": [],
        }

        shared_data = {
            "conversation": conversation_dict,
            "shared_by": "Test User",
            "created_at": "2023-01-01T12:00:00",
            "access_count": 1,
        }

        mock_share_service.get_shared_conversation.return_value = shared_data

        # Act
        response = client.get("/v1/share/conversations/test_token_123")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == shared_data
        mock_share_service.get_shared_conversation.assert_called_once_with("test_token_123", test_user)

    def test_access_shared_conversation_not_found(self, mock_share_service):
        """Test accessing a non-existent shared conversation"""
        # Arrange
        mock_share_service.get_shared_conversation.return_value = None

        # Act
        response = client.get("/v1/share/conversations/invalid_token")

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND
        response_json = response.json()
        assert (
            "not found" in response_json.get("message", "").lower()
            or "not found" in response_json.get("detail", "").lower()
            or "not found" in str(response_json).lower()
        )

    def test_existing_share_returned(self, mock_conversation, mock_ability, mock_share_service):
        """Test that existing shares are returned without creating a new one"""
        # Arrange
        existing_share = {
            "share_id": "share_test_conversation_id",
            "token": "existing_token",
            "created_at": "2023-01-01T12:00:00",
            "access_count": 5,  # Non-zero access count indicates it's an existing share
        }
        mock_share_service.share_conversation.return_value = existing_share

        with patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=mock_conversation):
            # Act
            response = client.post("/v1/share/conversations", json={"chat_id": "test_conversation_id"})

            # Assert
            assert response.status_code == status.HTTP_201_CREATED
            response_json = response.json()
            assert response_json["token"] == "existing_token"
            assert response_json["access_count"] == 5
