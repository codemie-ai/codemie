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

"""Tests for assistant category router endpoints."""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import app
from codemie.rest_api.models.category import Category
from codemie.rest_api.security.user import User


@pytest.fixture
def user():
    """Create a non-admin user for testing."""
    return User(id="user-123", username="testuser", name="Test User", is_admin=False)


@pytest.fixture
def admin_user():
    """Create an admin user for testing."""
    return User(id="admin-123", username="adminuser", name="Admin User", is_admin=True)


@pytest.fixture
def sample_categories():
    """Sample category data for testing."""
    return [
        Category(
            id="engineering",
            name="Engineering",
            description="Software engineering",
            date=datetime(2024, 1, 1, tzinfo=UTC),
            update_date=datetime(2024, 1, 1, tzinfo=UTC),
        ),
        Category(
            id="data-analytics",
            name="Data Analytics",
            description="Data analysis",
            date=datetime(2024, 1, 2, tzinfo=UTC),
            update_date=datetime(2024, 1, 2, tzinfo=UTC),
        ),
        Category(
            id="business-analysis",
            name="Business Analysis",
            description="Business analysis",
            date=datetime(2024, 1, 3, tzinfo=UTC),
            update_date=datetime(2024, 1, 3, tzinfo=UTC),
        ),
    ]


class TestGetAssistantCategories:
    """Tests for GET /assistants/categories (legacy, non-paginated endpoint)."""

    @pytest.mark.asyncio
    async def test_get_categories_success(self, sample_categories):
        """Test successfully retrieving all categories."""
        with patch("codemie.rest_api.routers.category.category_service.get_categories") as mock_get:
            mock_get.return_value = sample_categories

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
                response = await ac.get("/v1/assistants/categories")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert len(data) == 3
            assert data[0]["id"] == "engineering"
            assert data[0]["name"] == "Engineering"
            assert data[0]["description"] == "Software engineering"

    @pytest.mark.asyncio
    async def test_get_categories_empty(self):
        """Test retrieving categories when database is empty."""
        with patch("codemie.rest_api.routers.category.category_service.get_categories") as mock_get:
            mock_get.return_value = []

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
                response = await ac.get("/v1/assistants/categories")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data == []

    @pytest.mark.asyncio
    async def test_get_categories_error_handling(self):
        """Test error handling when loading categories fails."""
        with patch("codemie.rest_api.routers.category.category_service.get_categories") as mock_get:
            mock_get.side_effect = FileNotFoundError("Categories config not found")

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
                response = await ac.get("/v1/assistants/categories")

            assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            data = response.json()
            assert "Error loading categories" in data["error"]["message"]


class TestListAssistantCategories:
    """Tests for GET /assistants/categories/list (paginated with counts)."""

    @pytest.fixture(autouse=True)
    def override_auth(self, admin_user):
        """Override authentication to use admin user."""
        from codemie.rest_api.routers import category as category_router

        app.dependency_overrides[category_router.authenticate] = lambda: admin_user
        app.dependency_overrides[category_router.admin_access_only] = lambda: None
        yield
        app.dependency_overrides = {}

    @pytest.mark.asyncio
    async def test_list_categories_default_params(self):
        """Test listing categories with default parameters."""
        mock_result = {
            "categories": [
                {
                    "id": "engineering",
                    "name": "Engineering",
                    "description": "Software engineering",
                    "date": "2024-01-01T00:00:00Z",
                    "update_date": "2024-01-01T00:00:00Z",
                    "marketplace_assistants_count": 5,
                    "project_assistants_count": 3,
                }
            ],
            "page": 0,
            "per_page": 10,
            "total": 1,
            "pages": 1,
        }

        with patch("codemie.rest_api.routers.category.CategoryRepository.query") as mock_query:
            mock_query.return_value = mock_result

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
                response = await ac.get("/v1/assistants/categories/list")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["page"] == 0
            assert data["per_page"] == 10
            assert data["total"] == 1
            assert data["pages"] == 1
            assert len(data["categories"]) == 1

    @pytest.mark.asyncio
    async def test_list_categories_with_pagination(self):
        """Test listing categories with custom pagination."""
        mock_result = {
            "categories": [],
            "page": 2,
            "per_page": 5,
            "total": 20,
            "pages": 4,
        }

        with patch("codemie.rest_api.routers.category.CategoryRepository.query") as mock_query:
            mock_query.return_value = mock_result

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
                response = await ac.get("/v1/assistants/categories/list?page=2&per_page=5")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["page"] == 2
            assert data["per_page"] == 5
            mock_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_categories_invalid_page(self):
        """Test listing categories with invalid page number."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
            response = await ac.get("/v1/assistants/categories/list?page=-1")

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    @pytest.mark.asyncio
    async def test_list_categories_invalid_per_page(self):
        """Test listing categories with invalid per_page value."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
            response = await ac.get("/v1/assistants/categories/list?per_page=0")

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    @pytest.mark.asyncio
    async def test_list_categories_per_page_exceeds_limit(self):
        """Test listing categories with per_page exceeding maximum."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
            response = await ac.get("/v1/assistants/categories/list?per_page=101")

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


class TestGetAssistantCategory:
    """Tests for GET /assistants/categories/{id}."""

    @pytest.fixture(autouse=True)
    def override_auth(self, admin_user):
        """Override authentication to use admin user."""
        from codemie.rest_api.routers import category as category_router

        app.dependency_overrides[category_router.authenticate] = lambda: admin_user
        app.dependency_overrides[category_router.admin_access_only] = lambda: None
        yield
        app.dependency_overrides = {}

    @pytest.mark.asyncio
    async def test_get_category_success(self, sample_categories):
        """Test successfully retrieving a category by ID."""
        category = sample_categories[0]
        stats = {"marketplace_assistants_count": 5, "project_assistants_count": 3}

        with (
            patch("codemie.rest_api.routers.category.Category.find_by_id") as mock_find,
            patch("codemie.rest_api.routers.category.category_service.get_category_stats") as mock_stats,
        ):
            mock_find.return_value = category
            mock_stats.return_value = stats

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
                response = await ac.get("/v1/assistants/categories/engineering")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["id"] == "engineering"
            assert data["name"] == "Engineering"
            assert data["marketplaceAssistantCount"] == 5
            assert data["projectAssistantCount"] == 3

    @pytest.mark.asyncio
    async def test_get_category_not_found(self):
        """Test retrieving non-existent category."""
        with patch("codemie.rest_api.routers.category.Category.find_by_id") as mock_find:
            mock_find.return_value = None

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
                response = await ac.get("/v1/assistants/categories/nonexistent")

            assert response.status_code == status.HTTP_404_NOT_FOUND
            data = response.json()
            assert "Category not found" in data["error"]["message"]


class TestCreateAssistantCategory:
    """Tests for POST /assistants/categories."""

    @pytest.fixture(autouse=True)
    def override_auth(self, admin_user):
        """Override authentication to use admin user."""
        from codemie.rest_api.routers import category as category_router

        app.dependency_overrides[category_router.authenticate] = lambda: admin_user
        app.dependency_overrides[category_router.admin_access_only] = lambda: None
        yield
        app.dependency_overrides = {}

    @pytest.mark.asyncio
    async def test_create_category_success(self):
        """Test successfully creating a new category."""
        new_category = Category(
            id="testing",
            name="Testing",
            description="Testing and QA",
            date=datetime.now(UTC),
            update_date=datetime.now(UTC),
        )
        stats = {"marketplace_assistants_count": 0, "project_assistants_count": 0}

        with (
            patch("codemie.rest_api.routers.category.category_service.create_category") as mock_create,
            patch("codemie.rest_api.routers.category.category_service.get_category_stats") as mock_stats,
        ):
            mock_create.return_value = new_category
            mock_stats.return_value = stats

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
                response = await ac.post(
                    "/v1/assistants/categories",
                    json={"name": "Testing", "description": "Testing and QA"},
                )

            assert response.status_code == status.HTTP_201_CREATED
            data = response.json()
            assert data["id"] == "testing"
            assert data["name"] == "Testing"
            assert data["marketplaceAssistantCount"] == 0

    @pytest.mark.asyncio
    async def test_create_category_without_description(self):
        """Test creating category without description."""
        new_category = Category(
            id="testing",
            name="Testing",
            description=None,
            date=datetime.now(UTC),
            update_date=datetime.now(UTC),
        )
        stats = {"marketplace_assistants_count": 0, "project_assistants_count": 0}

        with (
            patch("codemie.rest_api.routers.category.category_service.create_category") as mock_create,
            patch("codemie.rest_api.routers.category.category_service.get_category_stats") as mock_stats,
        ):
            mock_create.return_value = new_category
            mock_stats.return_value = stats

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
                response = await ac.post(
                    "/v1/assistants/categories",
                    json={"name": "Testing"},
                )

            assert response.status_code == status.HTTP_201_CREATED
            data = response.json()
            assert data["name"] == "Testing"

    @pytest.mark.asyncio
    async def test_create_category_duplicate_name(self):
        """Test creating category with duplicate name."""
        with patch("codemie.rest_api.routers.category.category_service.create_category") as mock_create:
            mock_create.side_effect = ExtendedHTTPException(
                code=status.HTTP_409_CONFLICT,
                message="Category already exists",
                details="A category with name 'Testing' already exists",
            )

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
                response = await ac.post(
                    "/v1/assistants/categories",
                    json={"name": "Testing", "description": "Testing and QA"},
                )

            assert response.status_code == status.HTTP_409_CONFLICT
            data = response.json()
            assert "already exists" in data["error"]["message"]

    @pytest.mark.asyncio
    async def test_create_category_invalid_request(self):
        """Test creating category with invalid request data."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
            response = await ac.post(
                "/v1/assistants/categories",
                json={},  # Missing required 'name' field
            )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    @pytest.mark.asyncio
    async def test_create_category_duplicate_id_error(self):
        """Test creating category when generated ID already exists."""
        with patch("codemie.rest_api.routers.category.category_service.create_category") as mock_create:
            mock_create.side_effect = ValueError("Generated category ID already exists")

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
                response = await ac.post(
                    "/v1/assistants/categories",
                    json={"name": "Testing", "description": "Testing and QA"},
                )

            assert response.status_code == status.HTTP_400_BAD_REQUEST
            data = response.json()
            assert "Cannot create category" in data["error"]["message"]


class TestUpdateAssistantCategory:
    """Tests for PUT /assistants/categories/{id}."""

    @pytest.fixture(autouse=True)
    def override_auth(self, admin_user):
        """Override authentication to use admin user."""
        from codemie.rest_api.routers import category as category_router

        app.dependency_overrides[category_router.authenticate] = lambda: admin_user
        app.dependency_overrides[category_router.admin_access_only] = lambda: None
        yield
        app.dependency_overrides = {}

    @pytest.mark.asyncio
    async def test_update_category_success(self):
        """Test successfully updating a category."""
        updated_category = Category(
            id="engineering",
            name="Software Engineering",
            description="Updated description",
            date=datetime(2024, 1, 1, tzinfo=UTC),
            update_date=datetime.now(UTC),
        )
        stats = {"marketplace_assistants_count": 5, "project_assistants_count": 3}

        with (
            patch("codemie.rest_api.routers.category.category_service.get_category_stats") as mock_stats,
            patch("codemie.rest_api.routers.category.category_service.update_category") as mock_update,
        ):
            mock_stats.return_value = stats
            mock_update.return_value = updated_category

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
                response = await ac.put(
                    "/v1/assistants/categories/engineering",
                    json={"name": "Software Engineering", "description": "Updated description"},
                )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["id"] == "engineering"
            assert data["name"] == "Software Engineering"
            assert data["description"] == "Updated description"
            assert data["marketplaceAssistantCount"] == 5

    @pytest.mark.asyncio
    async def test_update_category_not_found(self):
        """Test updating non-existent category."""
        with (
            patch("codemie.rest_api.routers.category.category_service.get_category_stats") as mock_stats,
            patch("codemie.rest_api.routers.category.category_service.update_category") as mock_update,
        ):
            mock_stats.return_value = {"marketplace_assistants_count": 0, "project_assistants_count": 0}
            mock_update.side_effect = ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message="Category not found",
                details="Category with ID 'nonexistent' not found",
            )

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
                response = await ac.put(
                    "/v1/assistants/categories/nonexistent",
                    json={"name": "New Name", "description": "New Description"},
                )

            assert response.status_code == status.HTTP_404_NOT_FOUND
            data = response.json()
            assert "Category not found" in data["error"]["message"]

    @pytest.mark.asyncio
    async def test_update_category_duplicate_name(self):
        """Test updating category with name that already exists."""
        with (
            patch("codemie.rest_api.routers.category.category_service.get_category_stats") as mock_stats,
            patch("codemie.rest_api.routers.category.category_service.update_category") as mock_update,
        ):
            mock_stats.return_value = {"marketplace_assistants_count": 0, "project_assistants_count": 0}
            mock_update.side_effect = ExtendedHTTPException(
                code=status.HTTP_409_CONFLICT,
                message="Category name already exists",
                details="A category with name 'Engineering' already exists",
            )

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
                response = await ac.put(
                    "/v1/assistants/categories/testing",
                    json={"name": "Engineering", "description": "Some description"},
                )

            assert response.status_code == status.HTTP_409_CONFLICT
            data = response.json()
            assert "already exists" in data["error"]["message"]

    @pytest.mark.asyncio
    async def test_update_category_invalid_request(self):
        """Test updating category with invalid request data."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
            response = await ac.put(
                "/v1/assistants/categories/engineering",
                json={},  # Missing required fields
            )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


class TestDeleteAssistantCategory:
    """Tests for DELETE /assistants/categories/{id}."""

    @pytest.fixture(autouse=True)
    def override_auth(self, admin_user):
        """Override authentication to use admin user."""
        from codemie.rest_api.routers import category as category_router

        app.dependency_overrides[category_router.authenticate] = lambda: admin_user
        app.dependency_overrides[category_router.admin_access_only] = lambda: None
        yield
        app.dependency_overrides = {}

    @pytest.mark.asyncio
    async def test_delete_category_success(self):
        """Test successfully deleting a category."""
        with patch("codemie.rest_api.routers.category.category_service.delete_category") as mock_delete:
            mock_delete.return_value = None

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
                response = await ac.delete("/v1/assistants/categories/engineering")

            assert response.status_code == status.HTTP_204_NO_CONTENT
            mock_delete.assert_called_once_with("engineering")

    @pytest.mark.asyncio
    async def test_delete_category_not_found(self):
        """Test deleting non-existent category."""
        with patch("codemie.rest_api.routers.category.category_service.delete_category") as mock_delete:
            mock_delete.side_effect = ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message="Category not found",
                details="Category with ID 'nonexistent' not found",
            )

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
                response = await ac.delete("/v1/assistants/categories/nonexistent")

            assert response.status_code == status.HTTP_404_NOT_FOUND
            data = response.json()
            assert "Category not found" in data["error"]["message"]

    @pytest.mark.asyncio
    async def test_delete_category_with_assigned_assistants(self):
        """Test deleting category that has assigned assistants."""
        with patch("codemie.rest_api.routers.category.category_service.delete_category") as mock_delete:
            mock_delete.side_effect = ExtendedHTTPException(
                code=status.HTTP_409_CONFLICT,
                message="Cannot delete category",
                details="Cannot delete category with 5 assigned assistants",
            )

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
                response = await ac.delete("/v1/assistants/categories/engineering")

            assert response.status_code == status.HTTP_409_CONFLICT
            data = response.json()
            assert "Cannot delete category" in data["error"]["message"]
            assert "5 assigned assistants" in data["error"]["details"]


class TestCategoryAuthorizationRequirements:
    """Tests for authorization requirements on category endpoints."""

    @pytest.fixture(autouse=True)
    def override_auth_with_non_admin(self, user):
        """Override authentication to use non-admin user and make admin check raise 403."""
        from codemie.rest_api.routers import category as category_router

        def mock_admin_check():
            """Mock admin check that raises 403 for non-admin users."""
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message="Admin access required",
                details="This endpoint requires administrator privileges",
            )

        # Mock authenticate to return non-admin user
        app.dependency_overrides[category_router.authenticate] = lambda: user
        # Mock admin_access_only to raise 403
        app.dependency_overrides[category_router.admin_access_only] = mock_admin_check
        yield
        app.dependency_overrides = {}

    @pytest.mark.asyncio
    async def test_list_categories_requires_admin(self):
        """Test that listing categories requires admin access."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
            response = await ac.get("/v1/assistants/categories/list")

        # Should be forbidden for non-admin users
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_get_category_requires_admin(self):
        """Test that getting category by ID requires admin access."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
            response = await ac.get("/v1/assistants/categories/engineering")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_create_category_requires_admin(self):
        """Test that creating category requires admin access."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
            response = await ac.post(
                "/v1/assistants/categories",
                json={"name": "Testing", "description": "Test"},
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_update_category_requires_admin(self):
        """Test that updating category requires admin access."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
            response = await ac.put(
                "/v1/assistants/categories/engineering",
                json={"name": "New Name", "description": "New Desc"},
            )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_delete_category_requires_admin(self):
        """Test that deleting category requires admin access."""
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://localhost:8080") as ac:
            response = await ac.delete("/v1/assistants/categories/engineering")

        assert response.status_code == status.HTTP_403_FORBIDDEN
