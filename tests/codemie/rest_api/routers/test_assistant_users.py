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

from unittest.mock import patch, ANY, MagicMock

import pytest
from fastapi import status
from httpx import AsyncClient, ASGITransport

from codemie.core.models import CreatedByUser
from codemie.rest_api.main import app
from codemie.rest_api.models.prebuilt_assistants import PrebuiltAssistant
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_repository import AssistantScope


@pytest.fixture
def user():
    return User(id="123", username="testuser", name="Test User")


@pytest.fixture(autouse=True)
def override_dependency(user):
    from codemie.rest_api.security.authentication import authenticate

    app.dependency_overrides[authenticate] = lambda: user
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


@pytest.mark.asyncio
async def test_get_assistant_users_templates_scope_skips_repository():
    """Templates scope must not hit AssistantRepository.get_users."""
    with (
        patch.object(PrebuiltAssistant, "prebuilt_assistants", return_value=[]) as mock_prebuilt,
        patch("codemie.service.assistant.assistant_repository.AssistantRepository.get_users") as mock_repo,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(
                "/v1/assistants/users?scope=templates",
                headers={"Authorization": "Bearer testtoken"},
            )

        mock_prebuilt.assert_called_once()
        mock_repo.assert_not_called()
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_get_assistant_users_templates_returns_unique_authors():
    """Templates scope returns one entry per unique author, deduplicating by name."""
    author_a = CreatedByUser(id="u1", username="alice", name="Alice Smith")
    author_b = CreatedByUser(id="u2", username="bob", name="Bob Jones")

    template1 = MagicMock()
    template1.created_by = author_a
    template2 = MagicMock()
    template2.created_by = author_a  # duplicate — same author_a
    template3 = MagicMock()
    template3.created_by = author_b

    with patch.object(PrebuiltAssistant, "prebuilt_assistants", return_value=[template1, template2, template3]):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(
                "/v1/assistants/users?scope=templates",
                headers={"Authorization": "Bearer testtoken"},
            )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data) == 2
    assert {u["name"] for u in data} == {"Alice Smith", "Bob Jones"}


@pytest.mark.asyncio
async def test_get_assistant_users_templates_no_authors():
    """Templates with no created_by set return an empty list."""
    template = MagicMock()
    template.created_by = None

    with patch.object(PrebuiltAssistant, "prebuilt_assistants", return_value=[template]):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(
                "/v1/assistants/users?scope=templates",
                headers={"Authorization": "Bearer testtoken"},
            )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []
