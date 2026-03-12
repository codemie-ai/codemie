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

from unittest.mock import patch, ANY

import pytest
from fastapi import status
from httpx import AsyncClient, ASGITransport

from codemie.rest_api.main import app
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_repository import AssistantScope


@pytest.fixture
def user():
    return User(id="123", username="testuser", name="Test User")


@pytest.fixture(autouse=True)
def override_dependency(user):
    from codemie.rest_api.routers import assistant as assistant_router

    app.dependency_overrides[assistant_router.authenticate] = lambda: user
    yield
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_get_assistant_users_success():
    """Test successful retrieval of assistant users."""
    mock_users = [
        {"id": "user1", "username": "user1", "name": "User One"},
        {"id": "user2", "username": "user2", "name": "User Two"},
    ]

    with patch(
        "codemie.service.assistant.assistant_repository.AssistantRepository.get_users", return_value=mock_users
    ) as mock_get_users:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/assistants/users", headers={"Authorization": "Bearer testtoken"})

        mock_get_users.assert_called_once()
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == mock_users


@pytest.mark.asyncio
async def test_get_assistant_users_with_scope():
    """Test retrieval of assistant users with specific scope."""
    mock_users = [{"id": "user1", "username": "user1", "name": "User One"}]

    with patch(
        "codemie.service.assistant.assistant_repository.AssistantRepository.get_users", return_value=mock_users
    ) as mock_get_users:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(
                "/v1/assistants/users?scope=visible_to_user",  # Changed from GLOBAL to valid enum value
                headers={"Authorization": "Bearer testtoken"},
            )

        mock_get_users.assert_called_once_with(user=ANY, scope=AssistantScope.VISIBLE_TO_USER)
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == mock_users


@pytest.mark.asyncio
async def test_get_assistant_users_empty_result():
    """Test retrieval of assistant users with empty result."""
    with patch(
        "codemie.service.assistant.assistant_repository.AssistantRepository.get_users", return_value=[]
    ) as mock_get_users:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/assistants/users", headers={"Authorization": "Bearer testtoken"})

        mock_get_users.assert_called_once()
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == []
