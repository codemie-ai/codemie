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

"""
Tests for conversation pagination endpoints.
"""

from datetime import datetime
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient

import codemie.rest_api.routers.conversation as conversation_router
from codemie.rest_api.main import app
from codemie.rest_api.models.conversation import Conversation, ConversationListItem, GeneratedMessage
from codemie.rest_api.security.user import User


@pytest.fixture
def user():
    return User(id="user-123", username="testuser", name="Test User")


@pytest.fixture
def mock_litellm_startup():
    """Mock LiteLLM startup functions to prevent budget initialization errors."""
    with patch("codemie.rest_api.main.is_litellm_enabled", return_value=False):
        with patch("codemie.rest_api.main._initialize_database_and_defaults", return_value=None):
            with patch("codemie.rest_api.main.close_llm_proxy_client", new_callable=AsyncMock):
                yield


@pytest.fixture
def client(mock_litellm_startup):
    """Create TestClient with proper mocking."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def override_dependency(user):
    app.dependency_overrides[conversation_router.authenticate] = lambda: user
    yield
    app.dependency_overrides = {}


@patch("codemie.rest_api.routers.conversation.Conversation.get_user_conversations", new_callable=MagicMock)
def test_get_conversations_without_pagination(mock_get, client):
    """
    Test GET /v1/conversations without pagination parameters.
    Should call Conversation.get_user_conversations.
    """
    mock_get.return_value = [ConversationListItem(id="conv-1", name="Conv 1", date=datetime(2025, 1, 15))]

    response = client.get("/v1/conversations")

    assert response.status_code == 200
    mock_get.assert_called_once()


@patch(
    "codemie.rest_api.routers.conversation.ConversationService.get_user_conversations_paginated", new_callable=MagicMock
)
def test_get_conversations_with_pagination(mock_paginated, client):
    """
    Test GET /v1/conversations with pagination parameters.
    Should call ConversationService.get_user_conversations_paginated.
    """
    mock_paginated.return_value = [ConversationListItem(id="conv-2", name="Conv 2", date=datetime(2025, 1, 14))]

    response = client.get("/v1/conversations?page=0&per_page=10")

    assert response.status_code == 200
    mock_paginated.assert_called_once()


@patch("codemie.rest_api.routers.conversation.Assistant.get_by_ids", new_callable=MagicMock, return_value=[])
@patch("codemie.rest_api.routers.conversation.Ability.can", new_callable=MagicMock, return_value=True)
@patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", new_callable=MagicMock)
def test_export_json_without_pagination(mock_find, _mock_can, _mock_assistants, client):
    """
    Test GET /v1/conversations/{id}/export without pagination (JSON).
    Should call Conversation.find_by_id.
    """
    mock_find.return_value = Conversation(
        id="conv-123",
        conversation_id="conv-123",
        user_id="user-123",
        history=[GeneratedMessage(role="User", message="Hello")],
    )

    response = client.get("/v1/conversations/conv-123/export?export_format=json")

    assert response.status_code == 200
    mock_find.assert_called_once_with("conv-123")


@patch("codemie.rest_api.routers.conversation.Assistant.get_by_ids", new_callable=MagicMock, return_value=[])
@patch("codemie.rest_api.routers.conversation.Ability.can", new_callable=MagicMock, return_value=True)
@patch(
    "codemie.rest_api.routers.conversation.ConversationService.get_conversation_history_slice", new_callable=MagicMock
)
def test_export_json_with_pagination(mock_slice, _mock_can, _mock_assistants, client):
    """
    Test GET /v1/conversations/{id}/export with pagination (JSON).
    Should call ConversationService.get_conversation_history_slice.
    """
    mock_slice.return_value = Conversation(
        id="conv-123",
        conversation_id="conv-123",
        user_id="user-123",
        history=[GeneratedMessage(role="User", message="Hello")],
    )

    response = client.get("/v1/conversations/conv-123/export?export_format=json&page=0&per_page=50")

    assert response.status_code == 200
    mock_slice.assert_called_once()


@patch(
    "codemie.rest_api.routers.conversation.ConversationService.get_conversation_history_slice",
    new_callable=MagicMock,
    return_value=None,
)
def test_export_json_not_found(mock_slice, client):
    """
    Test GET /v1/conversations/{id}/export with pagination when not found.
    Should return 404.
    """
    response = client.get("/v1/conversations/non-existent/export?export_format=json&page=0&per_page=10")

    assert response.status_code == 404
