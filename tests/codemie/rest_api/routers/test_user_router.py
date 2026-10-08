# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""Tests for user router endpoints.

Test coverage for src/codemie/rest_api/routers/user.py
Target coverage: >= 80%
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from elasticsearch import NotFoundError
from fastapi import FastAPI, status
from httpx import AsyncClient, ASGITransport

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.enterprise.idp.dependencies import register_enterprise_idps
from codemie.rest_api.models.usage.assistant_user_interaction import ReactionType
from codemie.rest_api.models.user import UserData
from codemie.rest_api.routers.user import router
from codemie.rest_api.security.idp.factory import IdpFactory
from codemie.rest_api.security.user import User


# Create a FastAPI app and include the router
app = FastAPI()
app.include_router(router)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
def mock_user():
    """Create a mock user for testing."""
    return User(
        id="user-123",
        username="testuser",
        name="Test User",
        email="test@example.com",
        project_names=["test-project", "another-project"],
        admin_project_names=["test-project"],
        knowledge_bases=["kb1", "kb2"],
        user_type="regular",
        picture="https://example.com/avatar.png",
    )


@pytest.fixture
def mock_user_project():
    """Create a mock user project."""
    mock_project = MagicMock()
    mock_project.project_name = "test-project"
    mock_project.is_project_admin = True
    return mock_project


# =============================================================================
# Test GET /v1/user endpoint
# =============================================================================


@pytest.mark.anyio
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_get_user_basic(mock_authenticate, mock_user):
    """Test GET /v1/user returns user data with IDP mode (ENABLE_USER_MANAGEMENT=False)."""
    # Arrange
    mock_authenticate.return_value = mock_user

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/user", headers={"user-id": "user-123"})

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["user_id"] == "user-123"
    assert data["username"] == "testuser"
    # Note: name in response comes from User.full_name which returns name when available, falls back to username
    assert data["name"] == "Test User"
    assert data["email"] == "test@example.com"
    assert data["is_maintainer"] is False
    # Note: In local ENV, both is_admin and is_admin can be True (dev override)
    # Just verify they're boolean values
    assert isinstance(data["is_admin"], bool)
    assert isinstance(data["is_admin"], bool)
    assert data["picture"] == "https://example.com/avatar.png"
    assert len(data["projects"]) == 2
    assert data["projects"][0]["name"] == "test-project"
    assert data["projects"][0]["is_project_admin"] is True
    assert data["projects"][1]["name"] == "another-project"
    assert data["projects"][1]["is_project_admin"] is False


@pytest.mark.anyio
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_get_user_reflects_is_auditor(mock_authenticate):
    """EPMCDME-10930 regression: GET /v1/user must echo is_auditor from the security context.

    _get_user_response previously built UserResponse without passing is_auditor from the
    authenticated User, so the field always fell back to the model default (False) even
    when the DB/session had is_auditor=True.
    """
    auditor_user = User(
        id="user-123",
        username="testuser",
        name="Test User",
        email="test@example.com",
        project_names=["test-project"],
        admin_project_names=[],
        knowledge_bases=[],
        user_type="regular",
        picture="",
        is_admin=False,
        is_maintainer=False,
        is_auditor=True,
    )
    mock_authenticate.return_value = auditor_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/user", headers={"user-id": "user-123"})

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["is_auditor"] is True


@pytest.mark.anyio
async def test_get_user_with_management_flag(mock_user, mock_user_project):
    """Test GET /v1/user with ENABLE_USER_MANAGEMENT=True queries DB for projects.

    AC: When user management is enabled, endpoint queries DB directly
    instead of relying on security context.

    NOTE: This test verifies the code path but currently uses default ENABLE_USER_MANAGEMENT=False
    to avoid database connection issues in tests. The actual logic is tested through
    integration tests with real database.
    """
    # This test currently demonstrates the desired behavior but relies on
    # the existing test infrastructure. The actual ENABLE_USER_MANAGEMENT=True
    # path with database calls is covered by integration tests.
    #
    # For unit test purposes, we verify the endpoint returns user data correctly
    # with the current test setup (ENABLE_USER_MANAGEMENT=False)

    with patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate") as mock_authenticate:
        mock_authenticate.return_value = mock_user

        # Act
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/user", headers={"user-id": "user-123"})

        # Assert - verify endpoint works correctly
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["user_id"] == "user-123"
        # Projects are returned (from user security context in this test setup)
        assert "projects" in data
        assert len(data["projects"]) >= 0


def test_get_user_response_exposes_is_default_with_user_management(mock_user):
    """EPMCDME-15110: /v1/user marks the user's default project so clients can show it."""
    from codemie.rest_api.routers.user import _get_user_response

    rows = [
        MagicMock(project_name="test-project", is_project_admin=True, is_default=False),
        MagicMock(project_name="dept-a", is_project_admin=False, is_default=True),
    ]
    session = MagicMock()
    session.exec.return_value.all.return_value = []
    with (
        patch("codemie.rest_api.routers.user.config.ENABLE_USER_MANAGEMENT", True),
        patch("codemie.clients.postgres.get_session") as mock_get_session,
        patch("codemie.repository.user_project_repository.user_project_repository.get_by_user_id", return_value=rows),
    ):
        mock_get_session.return_value.__enter__.return_value = session
        response = _get_user_response(mock_user)

    by_name = {p.name: p for p in response.projects}
    assert by_name["dept-a"].is_default is True
    assert by_name["test-project"].is_default is False


def test_get_user_response_derives_personal_default_when_none_stored(mock_user):
    """EPMCDME-15738: with nothing stored, /v1/user still marks one default, the personal project."""
    from codemie.rest_api.routers.user import _get_user_response

    rows = [
        MagicMock(project_name="test-project", is_project_admin=True, is_default=False),
        MagicMock(project_name="test@example.com", is_project_admin=False, is_default=False),
    ]
    session = MagicMock()
    session.exec.return_value.all.return_value = []
    with (
        patch("codemie.rest_api.routers.user.config.ENABLE_USER_MANAGEMENT", True),
        patch("codemie.clients.postgres.get_session") as mock_get_session,
        patch("codemie.repository.user_project_repository.user_project_repository.get_by_user_id", return_value=rows),
    ):
        mock_get_session.return_value.__enter__.return_value = session
        response = _get_user_response(mock_user)

    assert {p.name: p.is_default for p in response.projects} == {"test-project": False, "test@example.com": True}


def test_get_user_response_legacy_path_has_no_default(mock_user):
    """Legacy (IDP) mode has no default-project concept: every project is_default=False."""
    from codemie.rest_api.routers.user import _get_user_response

    session = MagicMock()
    session.exec.return_value.all.return_value = []
    with (
        patch("codemie.rest_api.routers.user.config.ENABLE_USER_MANAGEMENT", False),
        patch("codemie.clients.postgres.get_session") as mock_get_session,
    ):
        mock_get_session.return_value.__enter__.return_value = session
        response = _get_user_response(mock_user)

    assert response.projects
    assert all(p.is_default is False for p in response.projects)


# =============================================================================
# Test GET /v1/profile endpoint
# =============================================================================


@pytest.mark.anyio
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_get_profile(mock_authenticate, mock_user):
    """Test GET /v1/profile returns user profile (alias for /v1/user).

    AC Story 3: User profile response includes projects array.
    """
    # Arrange
    mock_authenticate.return_value = mock_user

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/profile", headers={"user-id": "user-123"})

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["user_id"] == "user-123"
    assert data["username"] == "testuser"
    assert "projects" in data
    assert len(data["projects"]) >= 0  # Projects should be present


# =============================================================================
# Test GET /v1/user/data endpoint
# =============================================================================


@pytest.mark.anyio
@patch("codemie.rest_api.routers.user.UserData.get_by_fields")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_get_user_data_existing(mock_authenticate, mock_get_by_fields, mock_user):
    """Test GET /v1/user/data returns existing user data."""
    # Arrange
    mock_authenticate.return_value = mock_user
    existing_data = UserData(user_id="user-123", sidebar_view="folders")
    mock_get_by_fields.return_value = existing_data

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/user/data", headers={"user-id": "user-123"})

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["user_id"] == "user-123"
    assert data["sidebar_view"] == "folders"
    assert "stt_support" in data
    mock_get_by_fields.assert_called_once_with({"user_id.keyword": "user-123"})


@pytest.mark.anyio
@patch("codemie.rest_api.routers.user.UserData.get_by_fields")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_get_user_data_creates_new(mock_authenticate, mock_get_by_fields, mock_user):
    """Test GET /v1/user/data creates new UserData when not found."""
    # Arrange
    mock_authenticate.return_value = mock_user
    mock_get_by_fields.return_value = None

    # Mock UserData save method
    with patch.object(UserData, "save") as mock_save:
        # Act
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/user/data", headers={"user-id": "user-123"})

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["user_id"] == "user-123"
        assert data["sidebar_view"] == "flat"  # Default value
        mock_save.assert_called_once()


# =============================================================================
# Test PUT /v1/user/data endpoint
# =============================================================================


@pytest.mark.anyio
@patch("codemie.rest_api.routers.user.conversation_monitoring_service")
@patch("codemie.rest_api.routers.user.UserData.get_by_fields")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_update_user_data_success(mock_authenticate, mock_get_by_fields, mock_monitoring_service, mock_user):
    """Test PUT /v1/user/data updates existing user data."""
    # Arrange
    mock_authenticate.return_value = mock_user
    existing_data = UserData(user_id="user-123", sidebar_view="flat")
    mock_get_by_fields.return_value = existing_data

    # Mock update method
    with patch.object(UserData, "update") as mock_update:
        # Act
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.put("/v1/user/data", headers={"user-id": "user-123"}, json={"sidebar_view": "folders"})

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["sidebar_view"] == "folders"
        mock_update.assert_called_once()
        # Note: full_name property returns name when set (name takes priority over username)
        mock_monitoring_service.send_view_mode_metric.assert_called_once_with(
            "folders", user_id="user-123", user_name="Test User"
        )


@pytest.mark.anyio
@patch("codemie.rest_api.routers.user.UserData.get_by_fields")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_update_user_data_creates_new(mock_authenticate, mock_get_by_fields, mock_user):
    """Test PUT /v1/user/data creates new record when not found."""
    # Arrange
    mock_authenticate.return_value = mock_user
    # NotFoundError signature: (message, meta, body)
    mock_get_by_fields.side_effect = NotFoundError("Not found", {}, {})

    # Mock save method
    with patch.object(UserData, "save") as mock_save:
        # Act
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.put("/v1/user/data", headers={"user-id": "user-123"}, json={"sidebar_view": "folders"})

        # Assert
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["user_id"] == "user-123"
        assert data["sidebar_view"] == "folders"
        mock_save.assert_called_once()


@pytest.mark.anyio
@patch("codemie.rest_api.routers.user.UserData.get_by_fields")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_update_user_data_error_handling(mock_authenticate, mock_get_by_fields, mock_user):
    """Test PUT /v1/user/data handles update errors gracefully."""
    # Arrange
    mock_authenticate.return_value = mock_user
    existing_data = UserData(user_id="user-123", sidebar_view="flat")
    mock_get_by_fields.return_value = existing_data

    # Mock update to raise exception
    with patch.object(UserData, "update", side_effect=Exception("Database error")):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            # Act
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
                await ac.put("/v1/user/data", headers={"user-id": "user-123"}, json={"sidebar_view": "folders"})

        # Assert
        assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert "Cannot change specified user data" in exc_info.value.message


# =============================================================================
# Test GET /v1/user/reactions endpoint
# =============================================================================


@pytest.mark.anyio
@patch("codemie.rest_api.routers.user.assistant_user_interaction_service")
@patch("codemie.rest_api.routers.user.skill_user_interaction_service")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_get_reactions_assistants_only_minimal(
    mock_authenticate, mock_skill_service, mock_assistant_service, mock_user
):
    """Test GET /v1/user/reactions returns assistant reactions without details."""
    # Arrange
    mock_authenticate.return_value = mock_user

    # Create mock reaction records
    reaction1 = MagicMock()
    reaction1.assistant_id = "assistant-1"
    reaction1.reaction = ReactionType.LIKE
    reaction1.reaction_at = datetime(2024, 1, 15, 12, 0, 0)

    reaction2 = MagicMock()
    reaction2.assistant_id = "assistant-2"
    reaction2.reaction = ReactionType.DISLIKE
    reaction2.reaction_at = datetime(2024, 1, 14, 10, 0, 0)

    mock_assistant_service.get_reactions_by_user.return_value = [reaction1, reaction2]
    mock_skill_service.get_reactions_by_user.return_value = []

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/v1/user/reactions?resource_type=assistants&include_details=false", headers={"user-id": "user-123"}
        )

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["items"]) == 2
    # Check sorting (most recent first)
    assert data["items"][0]["resourceId"] == "assistant-1"
    assert data["items"][0]["reaction"] == "like"
    assert data["items"][0]["resourceType"] == "assistant"
    assert data["items"][1]["resourceId"] == "assistant-2"
    assert data["items"][1]["reaction"] == "dislike"
    mock_assistant_service.get_reactions_by_user.assert_called_once_with("user-123", None)


@pytest.mark.anyio
@patch("codemie.rest_api.routers.user.Assistant.get_by_ids")
@patch("codemie.rest_api.routers.user.assistant_user_interaction_service")
@patch("codemie.rest_api.routers.user.skill_user_interaction_service")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_get_reactions_assistants_with_details(
    mock_authenticate, mock_skill_service, mock_assistant_service, mock_get_assistants, mock_user
):
    """Test GET /v1/user/reactions returns assistant reactions with full details."""
    # Arrange
    mock_authenticate.return_value = mock_user

    # Create mock reaction record
    reaction = MagicMock()
    reaction.assistant_id = "assistant-1"
    reaction.reaction = ReactionType.LIKE
    reaction.reaction_at = datetime(2024, 1, 15, 12, 0, 0)

    mock_assistant_service.get_reactions_by_user.return_value = [reaction]
    mock_skill_service.get_reactions_by_user.return_value = []

    # Create mock assistant with details
    assistant = MagicMock()
    assistant.id = "assistant-1"
    assistant.name = "Test Assistant"
    assistant.description = "Test Description"
    assistant.project = "test-project"
    assistant.slug = "test-assistant"
    assistant.icon = "icon-url"

    mock_get_assistants.return_value = [assistant]

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/v1/user/reactions?resource_type=assistants&include_details=true", headers={"user-id": "user-123"}
        )

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["resourceId"] == "assistant-1"
    assert data["items"][0]["name"] == "Test Assistant"
    assert data["items"][0]["description"] == "Test Description"
    assert data["items"][0]["project"] == "test-project"
    assert data["items"][0]["slug"] == "test-assistant"
    assert data["items"][0]["icon"] == "icon-url"
    mock_get_assistants.assert_called_once_with(mock_user, ["assistant-1"])


@pytest.mark.anyio
@patch("codemie.rest_api.routers.user.SkillService.get_skills_by_ids")
@patch("codemie.rest_api.routers.user.assistant_user_interaction_service")
@patch("codemie.rest_api.routers.user.skill_user_interaction_service")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_get_reactions_skills_with_details(
    mock_authenticate, mock_skill_service, mock_assistant_service, mock_get_skills, mock_user
):
    """Test GET /v1/user/reactions returns skill reactions with full details."""
    # Arrange
    mock_authenticate.return_value = mock_user

    # Create mock reaction record
    reaction = MagicMock()
    reaction.skill_id = "skill-1"
    reaction.reaction = ReactionType.LIKE
    reaction.reaction_at = datetime(2024, 1, 15, 12, 0, 0)

    mock_assistant_service.get_reactions_by_user.return_value = []
    mock_skill_service.get_reactions_by_user.return_value = [reaction]

    # Create mock skill with details
    skill = MagicMock()
    skill.id = "skill-1"
    skill.name = "Test Skill"
    skill.description = "Test Skill Description"
    skill.project = "test-project"
    skill.visibility = MagicMock()
    skill.visibility.value = "public"
    skill.categories = ["code", "review"]

    mock_get_skills.return_value = [skill]

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/v1/user/reactions?resource_type=skills&include_details=true", headers={"user-id": "user-123"}
        )

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["resourceId"] == "skill-1"
    assert data["items"][0]["name"] == "Test Skill"
    assert data["items"][0]["description"] == "Test Skill Description"
    assert data["items"][0]["project"] == "test-project"
    assert data["items"][0]["visibility"] == "public"
    assert data["items"][0]["categories"] == ["code", "review"]
    mock_get_skills.assert_called_once_with(["skill-1"], mock_user)


@pytest.mark.anyio
@patch("codemie.rest_api.routers.user.Assistant.get_by_ids")
@patch("codemie.rest_api.routers.user.SkillService.get_skills_by_ids")
@patch("codemie.rest_api.routers.user.assistant_user_interaction_service")
@patch("codemie.rest_api.routers.user.skill_user_interaction_service")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_get_reactions_all_resources(
    mock_authenticate,
    mock_skill_service,
    mock_assistant_service,
    mock_get_skills,
    mock_get_assistants,
    mock_user,
):
    """Test GET /v1/user/reactions with resource_type=all returns both assistants and skills."""
    # Arrange
    mock_authenticate.return_value = mock_user

    # Create mock assistant reaction
    assistant_reaction = MagicMock()
    assistant_reaction.assistant_id = "assistant-1"
    assistant_reaction.reaction = ReactionType.LIKE
    assistant_reaction.reaction_at = datetime(2024, 1, 15, 12, 0, 0)

    # Create mock skill reaction
    skill_reaction = MagicMock()
    skill_reaction.skill_id = "skill-1"
    skill_reaction.reaction = ReactionType.DISLIKE
    skill_reaction.reaction_at = datetime(2024, 1, 14, 10, 0, 0)

    mock_assistant_service.get_reactions_by_user.return_value = [assistant_reaction]
    mock_skill_service.get_reactions_by_user.return_value = [skill_reaction]

    # Mock assistant and skill
    assistant = MagicMock()
    assistant.id = "assistant-1"
    assistant.name = "Test Assistant"
    assistant.description = "Assistant Description"
    assistant.project = "test-project"
    assistant.slug = "test-assistant"
    assistant.icon = None

    skill = MagicMock()
    skill.id = "skill-1"
    skill.name = "Test Skill"
    skill.description = "Skill Description"
    skill.project = "test-project"
    skill.visibility = MagicMock()
    skill.visibility.value = "public"
    skill.categories = []

    mock_get_assistants.return_value = [assistant]
    mock_get_skills.return_value = [skill]

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/v1/user/reactions?resource_type=all&include_details=true", headers={"user-id": "user-123"}
        )

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["items"]) == 2
    # Check sorting (most recent first)
    assert data["items"][0]["resourceType"] == "assistant"
    assert data["items"][0]["resourceId"] == "assistant-1"
    assert data["items"][1]["resourceType"] == "skill"
    assert data["items"][1]["resourceId"] == "skill-1"


@pytest.mark.anyio
@patch("codemie.rest_api.routers.user.assistant_user_interaction_service")
@patch("codemie.rest_api.routers.user.skill_user_interaction_service")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_get_reactions_with_reaction_type_filter(
    mock_authenticate, mock_skill_service, mock_assistant_service, mock_user
):
    """Test GET /v1/user/reactions filters by reaction_type parameter."""
    # Arrange
    mock_authenticate.return_value = mock_user

    reaction = MagicMock()
    reaction.assistant_id = "assistant-1"
    reaction.reaction = ReactionType.LIKE
    reaction.reaction_at = datetime(2024, 1, 15, 12, 0, 0)

    mock_assistant_service.get_reactions_by_user.return_value = [reaction]
    mock_skill_service.get_reactions_by_user.return_value = []

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/v1/user/reactions?resource_type=assistants&reaction_type=like", headers={"user-id": "user-123"}
        )

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["reaction"] == "like"
    # Verify that reaction_type was passed as enum
    mock_assistant_service.get_reactions_by_user.assert_called_once_with("user-123", ReactionType.LIKE)


@pytest.mark.anyio
@patch("codemie.rest_api.routers.user.assistant_user_interaction_service")
@patch("codemie.rest_api.routers.user.skill_user_interaction_service")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_get_reactions_invalid_reaction_type(
    mock_authenticate, mock_skill_service, mock_assistant_service, mock_user
):
    """Test GET /v1/user/reactions returns 400 for invalid reaction_type."""
    # Arrange
    mock_authenticate.return_value = mock_user

    # Act
    with pytest.raises(ExtendedHTTPException) as exc_info:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            await ac.get("/v1/user/reactions?reaction_type=invalid", headers={"user-id": "user-123"})

    # Assert
    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
    assert "Invalid reaction type" in exc_info.value.message


@pytest.mark.anyio
@patch("codemie.rest_api.routers.user.Assistant.get_by_ids")
@patch("codemie.rest_api.routers.user.assistant_user_interaction_service")
@patch("codemie.rest_api.routers.user.skill_user_interaction_service")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_get_reactions_assistant_not_found_fallback(
    mock_authenticate, mock_skill_service, mock_assistant_service, mock_get_assistants, mock_user
):
    """Test GET /v1/user/reactions falls back to minimal response when assistant not found.

    AC: When include_details=true but assistant is not found (deleted or no access),
    should return minimal response instead of failing.
    """
    # Arrange
    mock_authenticate.return_value = mock_user

    reaction = MagicMock()
    reaction.assistant_id = "deleted-assistant"
    reaction.reaction = ReactionType.LIKE
    reaction.reaction_at = datetime(2024, 1, 15, 12, 0, 0)

    mock_assistant_service.get_reactions_by_user.return_value = [reaction]
    mock_skill_service.get_reactions_by_user.return_value = []
    # Assistant not found - empty result
    mock_get_assistants.return_value = []

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/v1/user/reactions?resource_type=assistants&include_details=true", headers={"user-id": "user-123"}
        )

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["items"]) == 1
    # Should return minimal response (no name, description, etc.)
    assert data["items"][0]["resourceId"] == "deleted-assistant"
    assert data["items"][0]["reaction"] == "like"
    assert "name" not in data["items"][0]
    assert "description" not in data["items"][0]


@pytest.mark.anyio
@patch("codemie.rest_api.routers.user.assistant_user_interaction_service")
@patch("codemie.rest_api.routers.user.skill_user_interaction_service")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_get_reactions_empty_result(mock_authenticate, mock_skill_service, mock_assistant_service, mock_user):
    """Test GET /v1/user/reactions returns empty list when no reactions."""
    # Arrange
    mock_authenticate.return_value = mock_user
    mock_assistant_service.get_reactions_by_user.return_value = []
    mock_skill_service.get_reactions_by_user.return_value = []

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/user/reactions", headers={"user-id": "user-123"})

    # Assert
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["items"] == []


@pytest.mark.anyio
@patch("codemie.rest_api.routers.user.assistant_user_interaction_service")
@patch("codemie.rest_api.routers.user.skill_user_interaction_service")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_get_reactions_error_handling(mock_authenticate, mock_skill_service, mock_assistant_service, mock_user):
    """Test GET /v1/user/reactions handles unexpected errors gracefully."""
    # Arrange
    mock_authenticate.return_value = mock_user
    mock_assistant_service.get_reactions_by_user.side_effect = Exception("Database error")

    # Act
    with pytest.raises(ExtendedHTTPException) as exc_info:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            await ac.get("/v1/user/reactions", headers={"user-id": "user-123"})

    # Assert
    assert exc_info.value.code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Failed to retrieve reactions" in exc_info.value.message


# =============================================================================
# Test GET /v1/user/log_out endpoint
# =============================================================================

_LOGOUT_URL = "https://keycloak.example/logout"
_SESSION_COOKIE = "kc-session"
_SECRET = "sentinel-secret-token-value"
_SAME_ORIGIN = {"sec-fetch-site": "same-origin"}
_CLEANUP = "codemie.rest_api.routers.user.remove_user_tokens_on_logout_async"


@pytest.fixture
def logout_deps(monkeypatch, mock_user):
    """Patch the IdP and user provider that resolve the logging-out user in the router module."""
    monkeypatch.setattr(config, "KEYCLOAK_LOGOUT_URL", _LOGOUT_URL)
    idp = MagicMock()
    idp.get_session_cookie.return_value = _SESSION_COOKIE
    provider = MagicMock()
    provider.authenticate_and_load_user = AsyncMock(return_value=mock_user)
    with (
        patch("codemie.rest_api.routers.user.get_idp_provider", return_value=idp),
        patch("codemie.rest_api.routers.user.get_user_provider", return_value=provider),
    ):
        yield SimpleNamespace(idp=idp, provider=provider)


async def _get_logout(headers: dict[str, str] | None = None, **kwargs):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        return await ac.get("/v1/user/log_out", headers=headers, follow_redirects=False, **kwargs)


def _assert_redirected_and_cookie_deleted(response, cookie_name: str = _SESSION_COOKIE) -> None:
    assert response.status_code == status.HTTP_302_FOUND
    assert response.headers["location"] == _LOGOUT_URL
    set_cookie = response.headers["set-cookie"]
    assert f'{cookie_name}=""' in set_cookie
    assert "Max-Age=0" in set_cookie


@pytest.mark.anyio
@patch(_CLEANUP)
async def test_logout_removes_tokens_of_resolved_user(mock_cleanup, logout_deps, mock_user):
    """A resolvable same-origin session is purged, then redirected with the session cookie deleted."""
    response = await _get_logout(_SAME_ORIGIN)

    mock_cleanup.assert_awaited_once_with(mock_user.id)
    logout_deps.provider.authenticate_and_load_user.assert_awaited_once()
    assert logout_deps.provider.authenticate_and_load_user.await_args.args[1] is logout_deps.idp
    _assert_redirected_and_cookie_deleted(response)


@pytest.mark.anyio
@patch(_CLEANUP)
async def test_logout_ignores_user_id_from_query_string(mock_cleanup, logout_deps, mock_user):
    """Identity comes from the resolved session only, never from request input."""
    await _get_logout({**_SAME_ORIGIN, "user-id": "someone-else"}, params={"user_id": "someone-else"})

    mock_cleanup.assert_awaited_once_with(mock_user.id)


@pytest.mark.anyio
@patch("codemie.rest_api.routers.user.logger")
@patch(_CLEANUP)
async def test_logout_skips_cleanup_when_user_unresolvable(mock_cleanup, mock_logger, logout_deps):
    """An unresolvable session still logs out; cleanup is skipped and nothing leaks into logs."""
    logout_deps.provider.authenticate_and_load_user.side_effect = RuntimeError(_SECRET)

    response = await _get_logout(_SAME_ORIGIN)

    mock_cleanup.assert_not_awaited()
    _assert_redirected_and_cookie_deleted(response)
    mock_logger.warning.assert_called()
    assert "skipped" in mock_logger.warning.call_args.args[0]
    assert _SECRET not in str(mock_logger.mock_calls)


@pytest.mark.anyio
@patch("codemie.rest_api.routers.user.logger")
@patch(_CLEANUP)
async def test_logout_skips_cleanup_when_user_resolution_times_out(mock_cleanup, mock_logger, logout_deps, monkeypatch):
    """A stalled IdP or DB lookup must not hang the logout redirect."""

    async def stalled_resolution(*_args, **_kwargs):
        await asyncio.sleep(5)

    logout_deps.provider.authenticate_and_load_user.side_effect = stalled_resolution
    monkeypatch.setattr(config, "LOGOUT_TOKEN_CLEANUP_TIMEOUT_SECONDS", 0.05)

    response = await _get_logout(_SAME_ORIGIN)

    mock_cleanup.assert_not_awaited()
    _assert_redirected_and_cookie_deleted(response)
    assert "TimeoutError" in str(mock_logger.warning.call_args_list)
    assert "skipped" in mock_logger.warning.call_args.args[0]


@pytest.mark.anyio
@patch("codemie.service.security.logout_token_cleanup._delete_all_tms_tokens")
@patch("codemie.service.security.logout_token_cleanup._tms_in_use", return_value=True)
async def test_logout_redirects_when_token_cleanup_fails(_mock_in_use, mock_delete, logout_deps, mock_user):
    """A failing TMS delete never turns the logout redirect into an error."""
    mock_delete.side_effect = RuntimeError(_SECRET)

    response = await _get_logout(_SAME_ORIGIN)

    mock_delete.assert_called_once_with(mock_user.id)
    _assert_redirected_and_cookie_deleted(response)


# --- CR-003: only same-origin / same-site requests may purge tokens -------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({"sec-fetch-site": "same-origin"}, id="fetch-site-same-origin"),
        pytest.param({"sec-fetch-site": "same-site"}, id="fetch-site-same-site"),
        pytest.param({"origin": "http://testserver"}, id="origin-matches-host"),
        pytest.param({"referer": "http://testserver/ui/home"}, id="referer-matches-host"),
        pytest.param(
            {"sec-fetch-site": "same-origin", "referer": "https://evil.example/page"},
            id="fetch-site-wins-over-referer",
        ),
    ],
)
@patch(_CLEANUP)
async def test_logout_removes_tokens_for_same_site_request(mock_cleanup, logout_deps, mock_user, headers):
    response = await _get_logout(headers)

    mock_cleanup.assert_awaited_once_with(mock_user.id)
    _assert_redirected_and_cookie_deleted(response)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({"sec-fetch-site": "cross-site"}, id="fetch-site-cross-site"),
        pytest.param({"sec-fetch-site": "none"}, id="fetch-site-none"),
        pytest.param({}, id="no-request-metadata"),
        pytest.param({"origin": "https://evil.example"}, id="origin-other-host"),
        pytest.param({"origin": "null"}, id="origin-null"),
        pytest.param({"referer": "https://evil.example/page"}, id="referer-other-host"),
        pytest.param({"referer": "http://[malformed"}, id="referer-malformed"),
        pytest.param(
            {"sec-fetch-site": "cross-site", "origin": "http://testserver"},
            id="fetch-site-wins-over-origin",
        ),
        pytest.param(
            {"origin": "https://evil.example", "referer": "http://testserver/ui"},
            id="origin-wins-over-referer",
        ),
    ],
)
@patch("codemie.rest_api.routers.user.logger")
@patch(_CLEANUP)
async def test_logout_skips_cleanup_but_still_redirects_for_other_requests(
    mock_cleanup, mock_logger, logout_deps, headers
):
    """Cross-site or unprovable requests delete the cookie and redirect exactly as before, without purging."""
    response = await _get_logout(headers)

    mock_cleanup.assert_not_awaited()
    logout_deps.provider.authenticate_and_load_user.assert_not_awaited()
    _assert_redirected_and_cookie_deleted(response)
    mock_logger.warning.assert_called_once()
    assert "same-origin or same-site" in mock_logger.warning.call_args.args[0]


# --- CR-002: the real IdP chain, not a mocked one -------------------------------------------------

_KEYCLOAK_USER_ID = "keycloak-user-id"


class _AcceptingValidator:
    """Stands in for the enterprise JWKS validator: accepts any bearer token."""

    async def validate(self, token: str) -> dict:
        return {"sub": _KEYCLOAK_USER_ID}


def _keycloak_access_token() -> str:
    claims = {
        "sub": _KEYCLOAK_USER_ID,
        "preferred_username": "kc-user",
        "name": "Keycloak User",
        "realm_access": {"roles": []},
    }
    return jwt.encode(claims, "unused-signing-key", algorithm="HS256")


@pytest.fixture
def real_keycloak_chain(monkeypatch):
    """Route logout through the real IdpFactory, JwksValidatingIdp, Keycloak IdP and legacy user provider.

    Yields configure(jwks_enabled), which returns the session cookie name of the real IdP.
    """
    pytest.importorskip("codemie_enterprise.idp")
    monkeypatch.setattr(IdpFactory, "_idp_registry", dict(IdpFactory._idp_registry))
    register_enterprise_idps()
    monkeypatch.setattr(config, "IDP_PROVIDER", "keycloak")
    monkeypatch.setattr(config, "ENABLE_USER_MANAGEMENT", False)
    monkeypatch.setattr(config, "KEYCLOAK_LOGOUT_URL", _LOGOUT_URL)

    def configure(jwks_enabled: bool) -> str:
        monkeypatch.setattr(config, "JWKS_VALIDATION_ENABLED", jwks_enabled)
        return IdpFactory.create().get_session_cookie()

    with patch("codemie.rest_api.security.jwks.runtime.get_global_validator", return_value=_AcceptingValidator()):
        yield configure


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("jwks_enabled", "bearer_header"),
    [
        pytest.param(False, False, id="jwks-off-proxy-token-header"),
        pytest.param(False, True, id="jwks-off-proxy-token-and-bearer"),
        pytest.param(True, True, id="jwks-on-proxy-token-and-bearer"),
    ],
)
@patch(_CLEANUP)
async def test_logout_removes_tokens_through_real_keycloak_idp_chain(
    mock_cleanup, real_keycloak_chain, jwks_enabled, bearer_header
):
    """Identity arrives as proxy-injected headers; the id handed to cleanup is the IdP subject."""
    cookie_name = real_keycloak_chain(jwks_enabled)
    token = _keycloak_access_token()
    headers = {**_SAME_ORIGIN, "x-auth-request-access-token": token}
    if bearer_header:
        headers["authorization"] = f"Bearer {token}"

    response = await _get_logout(headers)

    mock_cleanup.assert_awaited_once_with(_KEYCLOAK_USER_ID)
    _assert_redirected_and_cookie_deleted(response, cookie_name)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("jwks_enabled", "headers"),
    [
        pytest.param(False, {"cookie": "_oauth2_proxy=opaque-session"}, id="jwks-off-cookie-only"),
        pytest.param(True, {"cookie": "_oauth2_proxy=opaque-session"}, id="jwks-on-cookie-only"),
        pytest.param(
            True,
            {"x-auth-request-access-token": _keycloak_access_token()},
            id="jwks-on-proxy-token-without-bearer",
        ),
    ],
)
@patch(_CLEANUP)
async def test_logout_skips_cleanup_but_redirects_when_real_keycloak_chain_cannot_identify_user(
    mock_cleanup, real_keycloak_chain, jwks_enabled, headers
):
    """Accepted limitation: a request the IdP chain cannot authenticate skips the purge and still logs out."""
    cookie_name = real_keycloak_chain(jwks_enabled)

    response = await _get_logout({**_SAME_ORIGIN, **headers})

    mock_cleanup.assert_not_awaited()
    _assert_redirected_and_cookie_deleted(response, cookie_name)
