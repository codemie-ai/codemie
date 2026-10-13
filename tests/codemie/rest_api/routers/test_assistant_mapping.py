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
    ASSISTANT_SCOPE,
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
        patch("codemie.rest_api.routers.assistant_mapping.get_assistant_by_id_or_raise") as mock_get_assistant,
        patch("codemie.rest_api.routers.assistant_mapping.search_settings_by_id") as mock_search_settings,
        patch("codemie.rest_api.routers.assistant_mapping.user_can_access_setting", return_value=True),
        patch(
            "codemie.service.assistant.assistant_user_mapping_service.assistant_user_mapping_service.create_or_update_mapping"
        ) as mock_create_update,
    ):
        # Set up the mocks
        mock_get_assistant.return_value = MagicMock()
        mock_search_settings.return_value = MagicMock()

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
            assistant_id=assistant_id,
            user_id=user.id,
            tools_config=sample_mapping_request.tools_config,
            workflow_id=ASSISTANT_SCOPE,
            apply_to_assistant=False,
        )


@pytest.mark.asyncio
async def test_create_or_update_mapping_failure(assistant_id, sample_mapping_request):
    # Arrange
    with (
        patch("codemie.rest_api.routers.assistant_mapping.get_assistant_by_id_or_raise") as mock_get_assistant,
        patch("codemie.rest_api.routers.assistant_mapping.search_settings_by_id") as mock_search_settings,
        patch("codemie.rest_api.routers.assistant_mapping.user_can_access_setting", return_value=True),
        patch(
            "codemie.service.assistant.assistant_user_mapping_service.assistant_user_mapping_service.create_or_update_mapping"
        ) as mock_create_update,
    ):
        # Set up the mocks
        mock_get_assistant.return_value = MagicMock()
        mock_search_settings.return_value = MagicMock()
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


def test_validate_mapping_access_project_shared_rejects_cross_project(user):
    """Project-shared (marketplace=False): a PROJECT integration of another project is rejected."""
    setting = MagicMock()
    with patch.object(assistant_mapping, "search_settings_by_id", return_value=setting):
        with patch.object(assistant_mapping, "user_can_access_setting", return_value=False) as gate:
            with pytest.raises(ExtendedHTTPException) as exc:
                assistant_mapping._validate_mapping_access(
                    [{"name": "MCP:srv", "integration_id": "int-cross"}], user, "proj-a", marketplace=False
                )
    assert exc.value.code == status.HTTP_403_FORBIDDEN
    gate.assert_called_once_with(setting, user, "proj-a", marketplace=False)


def test_validate_mapping_access_marketplace_allows_cross_project(user):
    """Marketplace (marketplace=True): the relaxed flag is forwarded to the access gate."""
    setting = MagicMock()
    with patch.object(assistant_mapping, "search_settings_by_id", return_value=setting):
        with patch.object(assistant_mapping, "user_can_access_setting", return_value=True) as gate:
            assistant_mapping._validate_mapping_access(
                [{"name": "MCP:srv", "integration_id": "int-cross"}], user, "proj-a", marketplace=True
            )
    gate.assert_called_once_with(setting, user, "proj-a", marketplace=True)


def test_validate_mapping_access_empty_integration_id_is_skipped(user):
    """An empty integration_id means DEFAULT (base config) and is accepted without a gate check."""
    with patch.object(assistant_mapping, "user_can_access_setting") as gate:
        assistant_mapping._validate_mapping_access(
            [{"name": "MCP:srv", "integration_id": ""}], user, "proj-a", marketplace=True
        )
    gate.assert_not_called()


@pytest.mark.asyncio
async def test_create_or_update_mapping_forwards_marketplace_flag(assistant_id, sample_mapping_request, user):
    """The router derives the marketplace flag from the assistant's is_global."""
    marketplace_assistant = MagicMock()
    marketplace_assistant.project = "proj-a"
    marketplace_assistant.is_global = True

    with (
        patch("codemie.rest_api.routers.assistant_mapping.get_assistant_by_id_or_raise") as mock_get_assistant,
        patch("codemie.rest_api.routers.assistant_mapping._validate_mapping_access") as mock_validate,
        patch(
            "codemie.service.assistant.assistant_user_mapping_service.assistant_user_mapping_service.create_or_update_mapping"
        ),
    ):
        mock_get_assistant.return_value = marketplace_assistant

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/users/mapping",
                json=sample_mapping_request.dict(),
                headers={"Authorization": "Bearer testtoken"},
            )

    assert response.status_code == status.HTTP_200_OK
    _, kwargs = mock_validate.call_args
    args = mock_validate.call_args.args
    assert kwargs.get("marketplace") is True or args[-1] is True


@pytest.mark.asyncio
async def test_get_assistant_mapping_found(assistant_id, user, sample_mapping_db):
    # Arrange
    with (
        patch("codemie.rest_api.routers.assistant_mapping.get_assistant_by_id_or_raise") as mock_get_assistant,
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
        patch("codemie.rest_api.routers.assistant_mapping.get_assistant_by_id_or_raise") as mock_get_assistant,
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
        patch("codemie.rest_api.routers.assistant_mapping.get_assistant_by_id_or_raise") as mock_get_assistant,
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
        patch("codemie.rest_api.routers.assistant_mapping.get_assistant_by_id_or_raise") as mock_get_assistant,
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


@pytest.mark.asyncio
async def test_create_or_update_mapping_forwards_the_workflow_scope(assistant_id, user):
    # Arrange
    with (
        patch("codemie.rest_api.routers.assistant_mapping.get_assistant_by_id_or_raise") as mock_get_assistant,
        patch("codemie.rest_api.routers.assistant_mapping.search_settings_by_id") as mock_search_settings,
        patch("codemie.rest_api.routers.assistant_mapping.user_can_access_setting", return_value=True),
        patch(
            "codemie.service.assistant.assistant_user_mapping_service.assistant_user_mapping_service.create_or_update_mapping"
        ) as mock_create_update,
    ):
        mock_get_assistant.return_value = MagicMock()
        mock_search_settings.return_value = MagicMock()
        tools_config = [{"name": "Git", "integration_id": "git-integration-id"}]

        # Act
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                f"/v1/assistants/{assistant_id}/users/mapping",
                json={"tools_config": tools_config, "workflow_id": "workflow-1", "apply_to_assistant": False},
                headers={"Authorization": "Bearer testtoken"},
            )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        mock_create_update.assert_called_once_with(
            assistant_id=assistant_id,
            user_id=user.id,
            tools_config=tools_config,
            workflow_id="workflow-1",
            apply_to_assistant=False,
        )


@pytest.mark.asyncio
async def test_create_or_update_mapping_without_workflow_stays_assistant_scoped(assistant_id, user):
    # Arrange: existing clients that never send a workflow must keep today's behaviour.
    with (
        patch("codemie.rest_api.routers.assistant_mapping.get_assistant_by_id_or_raise") as mock_get_assistant,
        patch("codemie.rest_api.routers.assistant_mapping.search_settings_by_id") as mock_search_settings,
        patch("codemie.rest_api.routers.assistant_mapping.user_can_access_setting", return_value=True),
        patch(
            "codemie.service.assistant.assistant_user_mapping_service.assistant_user_mapping_service.create_or_update_mapping"
        ) as mock_create_update,
    ):
        mock_get_assistant.return_value = MagicMock()
        mock_search_settings.return_value = MagicMock()

        # Act
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            await ac.post(
                f"/v1/assistants/{assistant_id}/users/mapping",
                json={"tools_config": [{"name": "Git", "integration_id": "git-integration-id"}]},
                headers={"Authorization": "Bearer testtoken"},
            )

        # Assert
        call_kwargs = mock_create_update.call_args.kwargs
        assert call_kwargs["workflow_id"] == ASSISTANT_SCOPE
        assert call_kwargs["apply_to_assistant"] is False


@pytest.mark.asyncio
async def test_get_mapping_with_workflow_returns_the_effective_config(assistant_id, user):
    # Arrange
    with (
        patch("codemie.rest_api.routers.assistant_mapping.get_assistant_by_id_or_raise"),
        patch(
            "codemie.service.assistant.assistant_user_mapping_service.assistant_user_mapping_service.get_effective_tools_config"
        ) as mock_effective,
    ):
        mock_effective.return_value = ([ToolConfig(name="Git", integration_id="workflow-git")], True)

        # Act
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(
                f"/v1/assistants/{assistant_id}/users/mapping?workflow_id=workflow-1",
                headers={"Authorization": "Bearer testtoken"},
            )

        # Assert
        body = response.json()
        assert response.status_code == status.HTTP_200_OK
        assert body["tools_config"] == [{"name": "Git", "integration_id": "workflow-git"}]
        assert body["workflow_id"] == "workflow-1"
        assert body["has_assistant_scope_selection"] is True
        mock_effective.assert_called_once_with(assistant_id=assistant_id, user_id=user.id, workflow_id="workflow-1")


@pytest.mark.asyncio
async def test_get_mapping_reports_what_auto_lookup_would_resolve(assistant_id, user):
    # The panel pre-selects these values, so the same resolution chain that runs at execution time
    # must answer the question "what applies to this slot right now".
    with (
        patch("codemie.rest_api.routers.assistant_mapping.get_assistant_by_id_or_raise") as mock_get_assistant,
        patch(
            "codemie.service.assistant.assistant_user_mapping_service.assistant_user_mapping_service.get_mapping"
        ) as mock_get_mapping,
        patch("codemie.rest_api.routers.assistant_mapping.SettingsService.retrieve_setting") as mock_retrieve,
        patch("codemie.rest_api.routers.assistant_mapping.user_can_access_setting", return_value=True),
    ):
        assistant = MagicMock()
        assistant.project = "project-1"
        assistant.is_global = False
        mock_get_assistant.return_value = assistant
        mock_get_mapping.return_value = None
        resolved = MagicMock()
        resolved.id = "auto-jira"
        mock_retrieve.return_value = resolved

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(
                f"/v1/assistants/{assistant_id}/users/mapping?credential_types=Jira",
                headers={"Authorization": "Bearer testtoken"},
            )

        body = response.json()
        assert response.status_code == status.HTTP_200_OK
        assert body["auto_resolved"] == [{"credential_type": "Jira", "integration_id": "auto-jira"}]


@pytest.mark.asyncio
async def test_get_mapping_hides_auto_resolved_the_user_cannot_access(assistant_id, user):
    with (
        patch("codemie.rest_api.routers.assistant_mapping.get_assistant_by_id_or_raise") as mock_get_assistant,
        patch(
            "codemie.service.assistant.assistant_user_mapping_service.assistant_user_mapping_service.get_mapping"
        ) as mock_get_mapping,
        patch("codemie.rest_api.routers.assistant_mapping.SettingsService.retrieve_setting") as mock_retrieve,
        patch("codemie.rest_api.routers.assistant_mapping.user_can_access_setting", return_value=False),
    ):
        assistant = MagicMock()
        assistant.project = "project-1"
        assistant.is_global = False
        mock_get_assistant.return_value = assistant
        mock_get_mapping.return_value = None
        mock_retrieve.return_value = MagicMock(id="foreign-integration")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(
                f"/v1/assistants/{assistant_id}/users/mapping?credential_types=Jira",
                headers={"Authorization": "Bearer testtoken"},
            )

        # Never surface an integration the user has no access to — that was CR-001 in EPMCDME-13337.
        assert response.json()["auto_resolved"] == []


@pytest.mark.asyncio
async def test_get_mapping_without_credential_types_reports_nothing(assistant_id, user):
    with (
        patch("codemie.rest_api.routers.assistant_mapping.get_assistant_by_id_or_raise"),
        patch(
            "codemie.service.assistant.assistant_user_mapping_service.assistant_user_mapping_service.get_mapping"
        ) as mock_get_mapping,
    ):
        mock_get_mapping.return_value = None

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(
                f"/v1/assistants/{assistant_id}/users/mapping",
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.json()["auto_resolved"] == []
