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
Tests for the assistant mapping router.
"""

import pytest
from fastapi import status
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, MagicMock

from codemie.rest_api.main import app
from codemie.rest_api.models.usage.assistant_user_mapping import (
    AssistantMappingRequest,
    AssistantMappingResponse,
    AssistantUserMappingSQL,
    ToolConfig,
)
from codemie.core.models import BaseResponse
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User
from codemie.rest_api.routers import assistant_mapping
from datetime import datetime, UTC


@pytest.fixture
def user():
    return User(id="test-user-id", username="testuser", name="Test User")


@pytest.fixture
def assistant_id():
    return "test-assistant-id"


@pytest.fixture
def tools_config_list():
    return [
        {"name": "Git", "integration_id": "git-integration-id"},
        {"name": "JIRA", "integration_id": "jira-integration-id"},
    ]


@pytest.fixture
def sample_mapping_request():
    return AssistantMappingRequest(
        tools_config=[
            {"name": "Git", "integration_id": "git-integration-id"},
            {"name": "JIRA", "integration_id": "jira-integration-id"},
        ]
    )


@pytest.fixture
def sample_mapping_db():
    return AssistantUserMappingSQL(
        id="test-id",
        assistant_id="test-assistant-id",
        user_id="test-user-id",
        tools_config=[
            ToolConfig(name="Git", integration_id="git-integration-id"),
            ToolConfig(name="JIRA", integration_id="jira-integration-id"),
        ],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def sample_mapping_response(sample_mapping_db):
    return AssistantMappingResponse.from_db_model(sample_mapping_db)


@pytest.fixture(autouse=True)
def override_dependency(user):
    app.dependency_overrides[assistant_mapping.authenticate] = lambda: user
    yield
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_create_or_update_mapping_success(assistant_id, sample_mapping_request, user):
    # Arrange
    with (
        patch("codemie.rest_api.routers.assistant_mapping._get_assistant_by_id_or_raise") as mock_get_assistant,
        patch(
            "codemie.service.assistant.assistant_user_mapping_service.assistant_user_mapping_service.create_or_update_mapping"
        ) as mock_create_update,
    ):
        # Set up the mocks
        mock_get_assistant.return_value = MagicMock()

        # Act
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/users/mapping",
                json=sample_mapping_request.dict(),
                headers={"Authorization": "Bearer testtoken"},
            )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == BaseResponse(message="Mappings created or updated successfully").dict()

        # Verify assistant existence was checked
        mock_get_assistant.assert_called_once_with(assistant_id)

        # Verify mapping was created with correct parameters
        mock_create_update.assert_called_once_with(
            assistant_id=assistant_id, user_id=user.id, tools_config=sample_mapping_request.tools_config
        )


@pytest.mark.asyncio
async def test_create_or_update_mapping_failure(assistant_id, sample_mapping_request):
    # Arrange
    with (
        patch("codemie.rest_api.routers.assistant_mapping._get_assistant_by_id_or_raise") as mock_get_assistant,
        patch(
            "codemie.service.assistant.assistant_user_mapping_service.assistant_user_mapping_service.create_or_update_mapping"
        ) as mock_create_update,
    ):
        # Set up the mocks
        mock_get_assistant.return_value = MagicMock()
        mock_create_update.side_effect = Exception("Database error")

        # Act
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/users/mapping",
                json=sample_mapping_request.dict(),
                headers={"Authorization": "Bearer testtoken"},
            )

        # Assert
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        response_data = response.json()
        assert response_data["error"]["message"] == "Failed to create or update mappings"
        assert "An error occurred while trying to store mappings: Database error" in response_data["error"]["details"]


@pytest.mark.asyncio
async def test_get_assistant_mapping_found(assistant_id, user, sample_mapping_db):
    # Arrange
    with (
        patch("codemie.rest_api.routers.assistant_mapping._get_assistant_by_id_or_raise") as mock_get_assistant,
        patch(
            "codemie.service.assistant.assistant_user_mapping_service.assistant_user_mapping_service.get_mapping"
        ) as mock_get_mapping,
    ):
        # Set up the mocks
        mock_get_assistant.return_value = MagicMock()
        mock_get_mapping.return_value = sample_mapping_db

        # Act
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(
                f"/v1/assistants/{assistant_id}/users/mapping", headers={"Authorization": "Bearer testtoken"}
            )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        response_data = response.json()

        # Check essential fields
        assert response_data["id"] == sample_mapping_db.id
        assert response_data["assistant_id"] == sample_mapping_db.assistant_id
        assert response_data["user_id"] == sample_mapping_db.user_id

        # Check tools config was properly converted
        assert len(response_data["tools_config"]) == 2
        assert response_data["tools_config"][0]["name"] == "Git"
        assert response_data["tools_config"][0]["integration_id"] == "git-integration-id"
        assert response_data["tools_config"][1]["name"] == "JIRA"
        assert response_data["tools_config"][1]["integration_id"] == "jira-integration-id"

        # Verify the mocked methods were called correctly
        mock_get_assistant.assert_called_once_with(assistant_id)
        mock_get_mapping.assert_called_once_with(assistant_id=assistant_id, user_id=user.id)


@pytest.mark.asyncio
async def test_get_assistant_mapping_not_found(assistant_id, user):
    # Arrange
    with (
        patch("codemie.rest_api.routers.assistant_mapping._get_assistant_by_id_or_raise") as mock_get_assistant,
        patch(
            "codemie.service.assistant.assistant_user_mapping_service.assistant_user_mapping_service.get_mapping"
        ) as mock_get_mapping,
    ):
        # Set up the mocks
        mock_get_assistant.return_value = MagicMock()
        mock_get_mapping.return_value = None

        # Act
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(
                f"/v1/assistants/{assistant_id}/users/mapping", headers={"Authorization": "Bearer testtoken"}
            )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        response_data = response.json()

        # Check that empty response is returned with defaults
        assert response_data["id"] == ""
        assert response_data["assistant_id"] == assistant_id
        assert response_data["user_id"] == user.id
        assert response_data["tools_config"] == []

        # Verify the mocked methods were called correctly
        mock_get_assistant.assert_called_once_with(assistant_id)
        mock_get_mapping.assert_called_once_with(assistant_id=assistant_id, user_id=user.id)


@pytest.mark.asyncio
async def test_get_assistant_mapping_error(assistant_id, user):
    # Arrange
    with (
        patch("codemie.rest_api.routers.assistant_mapping._get_assistant_by_id_or_raise") as mock_get_assistant,
        patch(
            "codemie.service.assistant.assistant_user_mapping_service.assistant_user_mapping_service.get_mapping"
        ) as mock_get_mapping,
    ):
        # Set up the mocks
        mock_get_assistant.return_value = MagicMock()
        mock_get_mapping.side_effect = Exception("Database error")

        # Act
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(
                f"/v1/assistants/{assistant_id}/users/mapping", headers={"Authorization": "Bearer testtoken"}
            )

        # Assert
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        response_data = response.json()
        assert response_data["error"]["message"] == "Failed to get mappings"
        assert (
            "An error occurred while trying to retrieve mappings: Database error" in response_data["error"]["details"]
        )


@pytest.mark.asyncio
async def test_get_assistant_mapping_extended_http_exception(assistant_id, user):
    # Arrange
    with (
        patch("codemie.rest_api.routers.assistant_mapping._get_assistant_by_id_or_raise") as mock_get_assistant,
        patch(
            "codemie.service.assistant.assistant_user_mapping_service.assistant_user_mapping_service.get_mapping"
        ) as mock_get_mapping,
    ):
        # Set up the mocks
        mock_get_assistant.return_value = MagicMock()
        http_exception = ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Access denied",
            details="You do not have permission to access this resource",
            help="Please contact an administrator",
        )
        mock_get_mapping.side_effect = http_exception

        # Act
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(
                f"/v1/assistants/{assistant_id}/users/mapping", headers={"Authorization": "Bearer testtoken"}
            )

        # Assert
        assert response.status_code == status.HTTP_403_FORBIDDEN
        response_data = response.json()
        assert response_data["error"]["message"] == "Access denied"
        assert response_data["error"]["details"] == "You do not have permission to access this resource"
