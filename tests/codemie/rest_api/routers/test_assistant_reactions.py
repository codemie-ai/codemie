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
from fastapi import status
from httpx import AsyncClient, ASGITransport
from codemie.rest_api.main import app
from codemie.rest_api.security.user import User
from codemie.rest_api.models.assistant import Assistant


@pytest.fixture
def user():
    return User(id="123", username="testuser", name="Test User")


@pytest.fixture
def assistant():
    return Assistant(
        id="456", name="Test Assistant", description="Test Description", system_prompt="Test Prompt", toolkits=[]
    )


@pytest.fixture(autouse=True)
def override_dependency(user):
    from codemie.rest_api.routers import assistant as assistant_router

    app.dependency_overrides[assistant_router.authenticate] = lambda: user
    yield
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_react_to_assistant_invalid_reaction():
    """Test adding an invalid reaction type to an assistant."""
    assistant_id = "456"
    reaction_data = {"reaction": "invalid_reaction"}  # Invalid reaction type

    with patch("codemie.rest_api.routers.assistant.Assistant.find_by_id", return_value=MagicMock()):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/reactions",
                json=reaction_data,
                headers={"Authorization": "Bearer testtoken"},
            )

        # Should return validation error because of the invalid reaction type
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_react_to_nonexistent_assistant():
    """Test adding a reaction to a non-existent assistant."""
    assistant_id = "nonexistent"
    reaction_data = {"reaction": "like"}

    with patch("codemie.rest_api.routers.assistant.Assistant.find_by_id", return_value=None):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/reactions",
                json=reaction_data,
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Assistant not found" in response.json()["error"]["message"]


@pytest.mark.asyncio
async def test_remove_reactions_nonexistent_assistant():
    """Test removing reactions from a non-existent assistant."""
    assistant_id = "nonexistent"

    with patch("codemie.rest_api.routers.assistant.Assistant.find_by_id", return_value=None):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.delete(
                f"/v1/assistants/{assistant_id}/reactions", headers={"Authorization": "Bearer testtoken"}
            )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "Assistant not found" in response.json()["error"]["message"]
