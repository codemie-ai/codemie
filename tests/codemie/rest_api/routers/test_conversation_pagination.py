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

from datetime import datetime, timezone
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient

import codemie.rest_api.routers.conversation as conversation_router
from codemie.rest_api.main import app
from codemie.rest_api.models.conversation import (
    Conversation,
    ConversationHistoryPaginationData,
    ConversationListItem,
    GeneratedMessage,
)
from codemie.rest_api.models.index import SortOrder
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
    t_first = datetime(2025, 1, 10, tzinfo=timezone.utc)
    t_last = datetime(2025, 1, 15, tzinfo=timezone.utc)
    mock_get.return_value = [
        ConversationListItem(
            id="conv-1",
            name="Conv 1",
            date=t_last,
            very_first_msg_at=t_first,
            very_last_msg_at=t_last,
        )
    ]

    response = client.get("/v1/conversations")

    assert response.status_code == 200
    data = response.json()
    assert data[0]["very_first_msg_at"] is not None
    assert data[0]["very_last_msg_at"] is not None
    mock_get.assert_called_once()


@patch(
    "codemie.rest_api.routers.conversation.ConversationService.get_user_conversations_paginated", new_callable=MagicMock
)
def test_get_conversations_with_pagination(mock_paginated, client):
    """
    Test GET /v1/conversations with pagination parameters.
    Should call ConversationService.get_user_conversations_paginated.
    """
    t_first = datetime(2025, 1, 12, tzinfo=timezone.utc)
    t_last = datetime(2025, 1, 14, tzinfo=timezone.utc)
    mock_paginated.return_value = [
        ConversationListItem(
            id="conv-2",
            name="Conv 2",
            date=t_last,
            very_first_msg_at=t_first,
            very_last_msg_at=t_last,
        )
    ]

    response = client.get("/v1/conversations?page=0&per_page=10")

    assert response.status_code == 200
    data = response.json()
    assert data[0]["very_first_msg_at"] is not None
    assert data[0]["very_last_msg_at"] is not None
    mock_paginated.assert_called_once()


@patch("codemie.rest_api.routers.conversation.Assistant.get_by_ids", new_callable=MagicMock, return_value=[])
@patch("codemie.rest_api.routers.conversation.Ability.can", new_callable=MagicMock, return_value=True)
@patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", new_callable=MagicMock)
def test_export_json_without_pagination(mock_find, _mock_can, _mock_assistants, client):
    """
    Test GET /v1/conversations/{id}/export without pagination (JSON).
    Should call Conversation.find_by_id.
    Timestamps appear in the export when messages have dates; absent when they don't.
    """
    t_first = datetime(2025, 3, 1, 10, 0, tzinfo=timezone.utc)
    t_last = datetime(2025, 3, 1, 11, 0, tzinfo=timezone.utc)
    mock_find.return_value = Conversation(
        id="conv-123",
        conversation_id="conv-123",
        user_id="user-123",
        history=[
            GeneratedMessage(role="User", message="Hello", date=t_first),
            GeneratedMessage(role="Assistant", message="Hi", date=t_last),
        ],
    )

    response = client.get("/v1/conversations/conv-123/export?export_format=json")

    assert response.status_code == 200
    data = response.json()
    assert data["very_first_msg_at"] is not None
    assert data["very_last_msg_at"] is not None
    mock_find.assert_called_once_with("conv-123")


@patch("codemie.rest_api.routers.conversation.Assistant.get_by_ids", new_callable=MagicMock, return_value=[])
@patch("codemie.rest_api.routers.conversation.Ability.can", new_callable=MagicMock, return_value=True)
@patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", new_callable=MagicMock)
def test_export_json_without_pagination_no_dates(mock_find, _mock_can, _mock_assistants, client):
    """
    Timestamps are absent from the export JSON when no messages have a date value.
    """
    mock_find.return_value = Conversation(
        id="conv-123",
        conversation_id="conv-123",
        user_id="user-123",
        history=[GeneratedMessage(role="User", message="Hello")],
    )

    response = client.get("/v1/conversations/conv-123/export?export_format=json")

    assert response.status_code == 200
    data = response.json()
    assert "very_first_msg_at" not in data
    assert "very_last_msg_at" not in data
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
    Timestamps from the service (full-conversation bounds) appear in the export.
    """
    t_first = datetime(2025, 3, 1, 9, 0, tzinfo=timezone.utc)
    t_last = datetime(2025, 3, 1, 11, 0, tzinfo=timezone.utc)
    conv = Conversation(
        id="conv-123",
        conversation_id="conv-123",
        user_id="user-123",
        history=[GeneratedMessage(role="User", message="Hello", date=t_first)],
    )
    mock_slice.return_value = (conv, 1, t_first, t_last)

    response = client.get("/v1/conversations/conv-123/export?export_format=json&page=0&per_page=50")

    assert response.status_code == 200
    data = response.json()
    assert data["very_first_msg_at"] is not None
    assert data["very_last_msg_at"] is not None
    mock_slice.assert_called_once()


@patch(
    "codemie.rest_api.routers.conversation.ConversationService.get_conversation_history_slice",
    new_callable=MagicMock,
    return_value=(None, 0, None, None),
)
def test_export_json_not_found(mock_slice, client):
    """
    Test GET /v1/conversations/{id}/export with pagination when not found.
    Should return 404.
    """
    response = client.get("/v1/conversations/non-existent/export?export_format=json&page=0&per_page=10")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Helpers for GET /v1/conversations/{conversation_id} tests
# ---------------------------------------------------------------------------


def _make_test_conversation(n_messages: int = 3) -> Conversation:
    """Build a test Conversation with n_messages history items."""
    t_base = datetime(2025, 6, 1, tzinfo=timezone.utc)
    history = [
        GeneratedMessage(
            role="User" if i % 2 == 0 else "Assistant",
            message=f"msg-{i}",
            date=t_base.replace(hour=i),
        )
        for i in range(n_messages)
    ]
    return Conversation(
        id="conv-abc",
        conversation_id="conv-abc",
        conversation_name="Test Conversation",
        user_id="user-123",
        history=history,
        is_workflow_conversation=False,
        assistant_ids=["asst-1"],
    )


def _make_pagination(page=0, per_page=2, total=5, pages=3, has_next=True, has_previous=False):
    return ConversationHistoryPaginationData(
        page=page,
        per_page=per_page,
        total=total,
        pages=pages,
        has_next=has_next,
        has_previous=has_previous,
    )


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


@patch(
    "codemie.rest_api.routers.conversation.ConversationService.get_conversation_history_slice", new_callable=MagicMock
)
@patch("codemie.rest_api.routers.conversation.Assistant.get_by_ids", new_callable=MagicMock, return_value=[])
@patch("codemie.rest_api.routers.conversation.Ability.can", new_callable=MagicMock, return_value=True)
@patch("codemie.rest_api.routers.conversation.Conversation.find_by_id", new_callable=MagicMock)
def test_get_conversation_no_params_pagination_absent(mock_find, _mock_can, _mock_assistants, mock_slice, client):
    """
    When no pagination/sort params are provided, the response must not include
    a 'pagination' key and must return the full history unchanged.
    """
    mock_find.return_value = _make_test_conversation(n_messages=3)

    response = client.get("/v1/conversations/conv-abc")

    assert response.status_code == 200
    data = response.json()
    assert "pagination" not in data
    assert len(data["history"]) == 3
    mock_slice.assert_not_called()


# ---------------------------------------------------------------------------
# Pagination params present
# ---------------------------------------------------------------------------


@patch(
    "codemie.rest_api.routers.conversation.ConversationService.get_conversation_history_slice", new_callable=MagicMock
)
@patch("codemie.rest_api.routers.conversation.Assistant.get_by_ids", new_callable=MagicMock, return_value=[])
@patch("codemie.rest_api.routers.conversation.Ability.can", new_callable=MagicMock, return_value=True)
def test_get_conversation_with_page_and_per_page(_mock_can, _mock_assistants, mock_slice, client):
    """
    When page and per_page are provided, get_conversation_history_slice is called and
    the response includes a correct pagination block.
    """
    conv = _make_test_conversation(n_messages=5)
    sliced_conv = _make_test_conversation(n_messages=2)
    sliced_conv.history = conv.history[:2]
    sliced_conv.id = conv.id
    sliced_conv.conversation_id = conv.conversation_id
    mock_slice.return_value = (sliced_conv, 5, None, None)

    response = client.get("/v1/conversations/conv-abc?page=0&per_page=2")

    assert response.status_code == 200
    data = response.json()
    assert "pagination" in data
    assert data["pagination"]["page"] == 0
    assert data["pagination"]["per_page"] == 2
    assert data["pagination"]["total"] == 5
    assert data["pagination"]["pages"] == 3
    assert data["pagination"]["has_next"] is True
    assert data["pagination"]["has_previous"] is False
    assert len(data["history"]) == 2
    mock_slice.assert_called_once_with(
        conversation_id="conv-abc",
        page=0,
        per_page=2,
        sort_order=None,
    )


@patch(
    "codemie.rest_api.routers.conversation.ConversationService.get_conversation_history_slice", new_callable=MagicMock
)
@patch("codemie.rest_api.routers.conversation.Assistant.get_by_ids", new_callable=MagicMock, return_value=[])
@patch("codemie.rest_api.routers.conversation.Ability.can", new_callable=MagicMock, return_value=True)
def test_get_conversation_with_only_sort_order(_mock_can, _mock_assistants, mock_slice, client):
    """
    When only sort_order is provided (no page/per_page), it still triggers pagination
    using default page/per_page values, and the response includes a pagination block.
    """
    conv = _make_test_conversation(n_messages=3)
    mock_slice.return_value = (conv, 3, None, None)

    response = client.get("/v1/conversations/conv-abc?sort_order=asc")

    assert response.status_code == 200
    data = response.json()
    assert "pagination" in data
    mock_slice.assert_called_once()
    _, call_kwargs = mock_slice.call_args
    assert call_kwargs["sort_order"] == SortOrder.ASC


@patch(
    "codemie.rest_api.routers.conversation.ConversationService.get_conversation_history_slice", new_callable=MagicMock
)
@patch("codemie.rest_api.routers.conversation.Assistant.get_by_ids", new_callable=MagicMock, return_value=[])
@patch("codemie.rest_api.routers.conversation.Ability.can", new_callable=MagicMock, return_value=True)
def test_get_conversation_sort_order_desc(_mock_can, _mock_assistants, mock_slice, client):
    """
    DESC sort_order is forwarded to get_conversation_history_slice correctly.
    """
    conv = _make_test_conversation(n_messages=3)
    sliced_conv = _make_test_conversation(n_messages=2)
    sliced_conv.history = conv.history[:2]
    sliced_conv.id = conv.id
    sliced_conv.conversation_id = conv.conversation_id
    mock_slice.return_value = (sliced_conv, 3, None, None)

    response = client.get("/v1/conversations/conv-abc?sort_order=desc&page=0&per_page=2")

    assert response.status_code == 200
    mock_slice.assert_called_once()
    _, call_kwargs = mock_slice.call_args
    assert call_kwargs["sort_order"] == SortOrder.DESC


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


@patch(
    "codemie.rest_api.routers.conversation.ConversationService.get_conversation_history_slice", new_callable=MagicMock
)
def test_get_conversation_not_found(mock_slice, client):
    """404 is returned when conversation does not exist, even with pagination params."""
    mock_slice.return_value = (None, 0, None, None)

    response = client.get("/v1/conversations/no-such-id?page=0&per_page=10")

    assert response.status_code == 404


def test_get_conversation_invalid_sort_order(client):
    """422 is returned when sort_order has an invalid value."""
    response = client.get("/v1/conversations/conv-abc?sort_order=invalid_value")

    assert response.status_code == 422


def test_get_conversation_negative_page(client):
    """422 is returned when page is negative (ge=0 constraint)."""
    response = client.get("/v1/conversations/conv-abc?page=-1")

    assert response.status_code == 422


def test_get_conversation_zero_per_page(client):
    """422 is returned when per_page is 0 (ge=1 constraint)."""
    response = client.get("/v1/conversations/conv-abc?per_page=0")

    assert response.status_code == 422
