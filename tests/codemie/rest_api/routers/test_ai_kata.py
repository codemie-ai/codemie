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

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient

from codemie.rest_api.main import app
from codemie.rest_api.models.ai_kata import (
    AIKataListResponse,
    AIKataPaginatedResponse,
    AIKataResponse,
    KataLevel,
    KataLink,
    KataRole,
    KataStatus,
    KataTag,
)
from codemie.rest_api.models.base import PaginationData
from codemie.rest_api.models.user_kata_progress import KataProgressStatus, UserKataProgressResponse
from codemie.rest_api.security.user import User
from codemie.service.permission.permission_exceptions import PermissionAccessDenied


@pytest.fixture
def regular_user():
    """Regular non-admin user fixture."""
    return User(id="user123", username="testuser", name="Test User")


@pytest.fixture
def admin_user():
    """Admin user fixture."""
    return User(id="admin123", username="adminuser", name="Admin User", roles=["admin"])


@pytest.fixture
def sample_user_progress():
    """Sample user progress fixture."""
    return UserKataProgressResponse(
        id="progress123",
        user_id="user123",
        kata_id="kata123",
        status=KataProgressStatus.NOT_STARTED,
        started_at=None,
        completed_at=None,
    )


@pytest.fixture
def sample_kata_response(sample_user_progress):
    """Sample kata response fixture."""
    return AIKataResponse(
        id="kata123",
        title="Test Kata",
        description="Test kata description",
        steps="Step 1\nStep 2",
        level=KataLevel.BEGINNER,
        creator_id="admin123",
        creator_name="Admin User",
        creator_username="adminuser",
        duration_minutes=30,
        tags=["python", "testing"],
        roles=["developer"],
        links=[KataLink(title="Documentation", url="https://example.com", type="docs")],
        references=["Reference 1"],
        status=KataStatus.PUBLISHED,
        date=datetime(2024, 1, 1, 12, 0, 0),
        update_date=datetime(2024, 1, 2, 12, 0, 0),
        image_url="https://example.com/image.png",
        user_progress=sample_user_progress,
        enrollment_count=10,
    )


@pytest.fixture
def sample_kata_list_response(sample_user_progress):
    """Sample kata list response fixture."""
    return AIKataListResponse(
        id="kata123",
        title="Test Kata",
        description="Test kata description",
        level=KataLevel.BEGINNER,
        creator_name="Admin User",
        creator_username="adminuser",
        duration_minutes=30,
        tags=["python", "testing"],
        roles=["developer"],
        status=KataStatus.PUBLISHED,
        date=datetime(2024, 1, 1, 12, 0, 0),
        image_url="https://example.com/image.png",
        user_progress=sample_user_progress,
        enrollment_count=10,
    )


@pytest.fixture
def sample_paginated_response(sample_kata_list_response):
    """Sample paginated response fixture."""
    return AIKataPaginatedResponse(
        data=[sample_kata_list_response],
        pagination=PaginationData(page=1, per_page=20, total=1, pages=1),
    )


@pytest.fixture
def sample_kata_request_data():
    """Sample kata request data fixture."""
    return {
        "title": "New Test Kata",
        "description": "A new kata for testing",
        "steps": "Step 1: Do something\nStep 2: Do something else",
        "level": "beginner",
        "duration_minutes": 20,
        "tags": ["python", "fastapi"],
        "roles": ["developer", "architect"],
        "links": [{"title": "Docs", "url": "https://example.com", "type": "documentation"}],
        "references": ["Reference 1", "Reference 2"],
        "image_url": "https://example.com/kata.png",
    }


@pytest.fixture
def sample_kata_tags():
    """Sample kata tags fixture."""
    return [
        KataTag(id="python", name="Python", description="Python programming"),
        KataTag(id="testing", name="Testing", description="Software testing"),
    ]


@pytest.fixture
def sample_kata_roles():
    """Sample kata roles fixture."""
    return [
        KataRole(id="developer", name="Developer", description="Software developer"),
        KataRole(id="architect", name="Architect", description="Software architect"),
    ]


# GET /katas/tags Tests


@pytest.mark.asyncio
async def test_get_kata_tags_success(admin_user, sample_kata_tags):
    """Test successful retrieval of kata tags."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: admin_user

    with patch("codemie.rest_api.routers.ai_kata.load_kata_tags", return_value=sample_kata_tags):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/katas/tags", headers={"Authorization": "Bearer testtoken"})

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 2
        assert data[0]["id"] == "python"
        assert data[0]["name"] == "Python"
        assert data[1]["id"] == "testing"

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_get_kata_tags_empty_list(admin_user):
    """Test retrieval of kata tags when no tags are available."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: admin_user

    with patch("codemie.rest_api.routers.ai_kata.load_kata_tags", return_value=[]):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/katas/tags", headers={"Authorization": "Bearer testtoken"})

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 0

    app.dependency_overrides = {}


# GET /katas/roles Tests


@pytest.mark.asyncio
async def test_get_kata_roles_success(admin_user, sample_kata_roles):
    """Test successful retrieval of kata roles."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: admin_user

    with patch("codemie.rest_api.routers.ai_kata.load_kata_roles", return_value=sample_kata_roles):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/katas/roles", headers={"Authorization": "Bearer testtoken"})

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 2
        assert data[0]["id"] == "developer"
        assert data[0]["name"] == "Developer"
        assert data[1]["id"] == "architect"

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_get_kata_roles_empty_list(admin_user):
    """Test retrieval of kata roles when no roles are available."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: admin_user

    with patch("codemie.rest_api.routers.ai_kata.load_kata_roles", return_value=[]):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/katas/roles", headers={"Authorization": "Bearer testtoken"})

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 0

    app.dependency_overrides = {}


# GET /katas Tests (List with filters)


@pytest.mark.asyncio
async def test_list_katas_success_regular_user(regular_user, sample_paginated_response):
    """Test successful kata list retrieval for regular user."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: regular_user

    with patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_service:
        mock_service.list_katas.return_value = sample_paginated_response
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/katas?page=1&per_page=20", headers={"Authorization": "Bearer testtoken"})

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["pagination"]["total"] == 1
        assert data["pagination"]["page"] == 1
        assert len(data["data"]) == 1
        assert data["data"][0]["title"] == "Test Kata"

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_list_katas_with_filters_regular_user(regular_user, sample_paginated_response):
    """Test kata list with filters for regular user."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: regular_user

    filters = json.dumps({"search": "python", "level": "beginner", "tags": ["python"]})

    with patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_service:
        mock_service.list_katas.return_value = sample_paginated_response
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(
                f"/v1/katas?page=1&per_page=20&filters={filters}", headers={"Authorization": "Bearer testtoken"}
            )

        assert response.status_code == status.HTTP_200_OK

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_list_katas_invalid_json_filters(regular_user):
    """Test kata list with invalid JSON filters."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: regular_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/v1/katas?page=1&per_page=20&filters=invalid_json", headers={"Authorization": "Bearer testtoken"}
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert "Invalid filters" in data["error"]["message"]

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_list_katas_invalid_filter_schema(regular_user):
    """Test kata list with invalid filter schema."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: regular_user

    filters = json.dumps({"search": "a" * 201})  # Exceeds max_length

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            f"/v1/katas?page=1&per_page=20&filters={filters}", headers={"Authorization": "Bearer testtoken"}
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert "Invalid filters" in data["error"]["message"]

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_list_katas_admin_can_filter_by_any_status(admin_user, sample_paginated_response):
    """Test that admin user can filter by any status."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: admin_user

    filters = json.dumps({"status": "draft"})

    with patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_service:
        mock_service.list_katas.return_value = sample_paginated_response
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(
                f"/v1/katas?page=1&per_page=20&filters={filters}", headers={"Authorization": "Bearer testtoken"}
            )

        assert response.status_code == status.HTTP_200_OK

    app.dependency_overrides = {}


# GET /katas/{kata_id} Tests


@pytest.mark.asyncio
async def test_get_kata_success(regular_user, sample_kata_response):
    """Test successful kata retrieval."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: regular_user

    with patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_service:
        mock_service.get_kata.return_value = sample_kata_response
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/katas/kata123", headers={"Authorization": "Bearer testtoken"})

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == "kata123"
        assert data["title"] == "Test Kata"
        assert data["description"] == "Test kata description"

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_get_kata_not_found(regular_user):
    """Test kata retrieval when kata does not exist."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: regular_user

    with patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_service:
        mock_service.get_kata.return_value = None
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/katas/nonexistent", headers={"Authorization": "Bearer testtoken"})

        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()
        assert "Kata not found" in data["error"]["message"]

    app.dependency_overrides = {}


# POST /katas Tests (Create)


@pytest.mark.asyncio
async def test_create_kata_success_admin(admin_user, sample_kata_request_data):
    """Test successful kata creation by admin."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: admin_user

    with patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_service:
        mock_service.create_kata.return_value = "kata456"
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/katas", json=sample_kata_request_data, headers={"Authorization": "Bearer testtoken"}
            )

        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["id"] == "kata456"

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_create_kata_permission_denied(regular_user, sample_kata_request_data):
    """Test kata creation fails for non-admin user."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: regular_user

    with patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_service:
        mock_service.create_kata.side_effect = PermissionAccessDenied("Only admins can create katas")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/katas", json=sample_kata_request_data, headers={"Authorization": "Bearer testtoken"}
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        data = response.json()
        assert "Not authorized" in data["error"]["message"]

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_create_kata_validation_error(admin_user):
    """Test kata creation with invalid data."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: admin_user

    invalid_data = {
        "title": "",  # Empty title should fail
        "description": "Valid description",
        "steps": "Step 1",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post("/v1/katas", json=invalid_data, headers={"Authorization": "Bearer testtoken"})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    app.dependency_overrides = {}


# PUT /katas/{kata_id} Tests (Update)


@pytest.mark.asyncio
async def test_update_kata_success_admin(admin_user, sample_kata_request_data):
    """Test successful kata update by admin."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: admin_user

    with patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_service:
        mock_service.update_kata.return_value = True
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.put(
                "/v1/katas/kata123", json=sample_kata_request_data, headers={"Authorization": "Bearer testtoken"}
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "updated successfully" in data["message"]

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_update_kata_permission_denied(regular_user, sample_kata_request_data):
    """Test kata update fails for non-admin user."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: regular_user

    with patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_service:
        mock_service.update_kata.side_effect = PermissionAccessDenied("Only admins can update katas")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.put(
                "/v1/katas/kata123", json=sample_kata_request_data, headers={"Authorization": "Bearer testtoken"}
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        data = response.json()
        assert "Not authorized" in data["error"]["message"]

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_update_kata_failed(admin_user, sample_kata_request_data):
    """Test kata update failure."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: admin_user

    with patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_service:
        mock_service.update_kata.return_value = False
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.put(
                "/v1/katas/kata123", json=sample_kata_request_data, headers={"Authorization": "Bearer testtoken"}
            )

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        data = response.json()
        assert "Update failed" in data["error"]["message"]

    app.dependency_overrides = {}


# POST /katas/{kata_id}/publish Tests


@pytest.mark.asyncio
async def test_publish_kata_success_admin(admin_user):
    """Test successful kata publish by admin."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: admin_user

    with patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_service:
        mock_service.publish_kata.return_value = True
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post("/v1/katas/kata123/publish", headers={"Authorization": "Bearer testtoken"})

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "published successfully" in data["message"]

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_publish_kata_permission_denied(regular_user):
    """Test kata publish fails for non-admin user."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: regular_user

    with patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_service:
        mock_service.publish_kata.side_effect = PermissionAccessDenied("Only admins can publish katas")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post("/v1/katas/kata123/publish", headers={"Authorization": "Bearer testtoken"})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        data = response.json()
        assert "Not authorized" in data["error"]["message"]

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_publish_kata_failed(admin_user):
    """Test kata publish failure."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: admin_user

    with patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_service:
        mock_service.publish_kata.return_value = False
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post("/v1/katas/kata123/publish", headers={"Authorization": "Bearer testtoken"})

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        data = response.json()
        assert "Publish failed" in data["error"]["message"]

    app.dependency_overrides = {}


# POST /katas/{kata_id}/archive Tests


@pytest.mark.asyncio
async def test_archive_kata_success_admin(admin_user):
    """Test successful kata archive by admin."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: admin_user

    with patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_service:
        mock_service.archive_kata.return_value = True
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post("/v1/katas/kata123/archive", headers={"Authorization": "Bearer testtoken"})

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "archived successfully" in data["message"]

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_archive_kata_permission_denied(regular_user):
    """Test kata archive fails for non-admin user."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: regular_user

    with patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_service:
        mock_service.archive_kata.side_effect = PermissionAccessDenied("Only admins can archive katas")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post("/v1/katas/kata123/archive", headers={"Authorization": "Bearer testtoken"})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        data = response.json()
        assert "Not authorized" in data["error"]["message"]

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_archive_kata_failed(admin_user):
    """Test kata archive failure."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: admin_user

    with patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_service:
        mock_service.archive_kata.return_value = False
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post("/v1/katas/kata123/archive", headers={"Authorization": "Bearer testtoken"})

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        data = response.json()
        assert "Archive failed" in data["error"]["message"]

    app.dependency_overrides = {}


# DELETE /katas/{kata_id} Tests


@pytest.mark.asyncio
async def test_delete_kata_success_admin(admin_user):
    """Test successful kata delete by admin."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: admin_user

    with patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_service:
        mock_service.delete_kata.return_value = True
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.delete("/v1/katas/kata123", headers={"Authorization": "Bearer testtoken"})

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "deleted successfully" in data["message"]

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_delete_kata_permission_denied(regular_user):
    """Test kata delete fails for non-admin user."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: regular_user

    with patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_service:
        mock_service.delete_kata.side_effect = PermissionAccessDenied("Only admins can delete katas")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.delete("/v1/katas/kata123", headers={"Authorization": "Bearer testtoken"})

        assert response.status_code == status.HTTP_403_FORBIDDEN
        data = response.json()
        assert "Not authorized" in data["error"]["message"]

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_delete_kata_failed(admin_user):
    """Test kata delete failure."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: admin_user

    with patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_service:
        mock_service.delete_kata.return_value = False
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.delete("/v1/katas/kata123", headers={"Authorization": "Bearer testtoken"})

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        data = response.json()
        assert "Delete failed" in data["error"]["message"]

    app.dependency_overrides = {}


# POST /katas/{kata_id}/reactions Tests


@pytest.mark.asyncio
async def test_react_to_kata_like_success(regular_user, sample_kata_response):
    """Test successful like reaction to kata."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: regular_user

    mock_reaction_response = {"reaction": "like", "likes": 5, "dislikes": 2}

    with (
        patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_kata_service,
        patch("codemie.rest_api.routers.ai_kata.kata_user_interaction_service") as mock_interaction_service,
    ):
        mock_kata_service.get_kata.return_value = sample_kata_response
        mock_interaction_service.manage_reaction.return_value = mock_reaction_response
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/katas/kata123/reactions",
                json={"reaction": "like"},
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["reaction"] == "like"
        assert data["likes"] == 5

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_react_to_kata_dislike_success(regular_user, sample_kata_response):
    """Test successful dislike reaction to kata."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: regular_user

    mock_reaction_response = {"reaction": "dislike", "likes": 5, "dislikes": 3}

    with (
        patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_kata_service,
        patch("codemie.rest_api.routers.ai_kata.kata_user_interaction_service") as mock_interaction_service,
    ):
        mock_kata_service.get_kata.return_value = sample_kata_response
        mock_interaction_service.manage_reaction.return_value = mock_reaction_response
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/katas/kata123/reactions",
                json={"reaction": "dislike"},
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["reaction"] == "dislike"
        assert data["dislikes"] == 3

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_react_to_kata_invalid_reaction(regular_user):
    """Test invalid reaction type."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: regular_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/katas/kata123/reactions",
            json={"reaction": "invalid"},
            headers={"Authorization": "Bearer testtoken"},
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_react_to_kata_not_found(regular_user):
    """Test reaction to non-existent kata."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: regular_user

    with patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_service:
        mock_service.get_kata.return_value = None
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/katas/nonexistent/reactions",
                json={"reaction": "like"},
                headers={"Authorization": "Bearer testtoken"},
            )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()
        assert "Kata not found" in data["error"]["message"]

    app.dependency_overrides = {}


# DELETE /katas/{kata_id}/reactions Tests


@pytest.mark.asyncio
async def test_remove_kata_reactions_success(regular_user, sample_kata_response):
    """Test successful removal of kata reactions."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: regular_user

    mock_reaction_response = {"reaction": None, "likes": 4, "dislikes": 2}

    with (
        patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_kata_service,
        patch("codemie.rest_api.routers.ai_kata.kata_user_interaction_service") as mock_interaction_service,
    ):
        mock_kata_service.get_kata.return_value = sample_kata_response
        mock_interaction_service.remove_reactions.return_value = mock_reaction_response
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.delete("/v1/katas/kata123/reactions", headers={"Authorization": "Bearer testtoken"})

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["reaction"] is None
        assert data["likes"] == 4

    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_remove_kata_reactions_not_found(regular_user):
    """Test removing reactions from non-existent kata."""
    from codemie.rest_api.routers import ai_kata

    app.dependency_overrides[ai_kata.authenticate] = lambda: regular_user

    with patch("codemie.rest_api.routers.ai_kata.kata_service") as mock_service:
        mock_service.get_kata.return_value = None
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.delete("/v1/katas/nonexistent/reactions", headers={"Authorization": "Bearer testtoken"})

        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()
        assert "Kata not found" in data["error"]["message"]

    app.dependency_overrides = {}
