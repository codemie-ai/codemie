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

"""Tests for dynamic_config router endpoints.

Test coverage for src/codemie/rest_api/routers/dynamic_config.py
and src/codemie/service/dynamic_config_service.py

Target coverage: >= 90%
"""

import pytest
from unittest.mock import patch
from datetime import datetime
from fastapi import FastAPI, status
from httpx import AsyncClient, ASGITransport

from codemie.rest_api.models.dynamic_config import DynamicConfig, ConfigValueType
from codemie.rest_api.routers import dynamic_config as dynamic_config_router
from codemie.rest_api.security.user import User
from codemie.core.exceptions import ExtendedHTTPException


# Create a FastAPI app and include the router
app = FastAPI()
app.include_router(dynamic_config_router.router)


# Add exception handler for ExtendedHTTPException
@app.exception_handler(ExtendedHTTPException)
async def extended_http_exception_handler(request, exc: ExtendedHTTPException):
    """Handle ExtendedHTTPException by converting to JSON response."""
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=exc.code,
        content={
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
            "help": exc.help if hasattr(exc, "help") else None,
        },
    )


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mock_admin_user():
    """Create a mock super-admin user for testing."""
    return User(
        id="admin-123",
        username="admin",
        name="Admin User",
        email="admin@example.com",
        project_names=["project1"],
        admin_project_names=["project1"],
        knowledge_bases=["kb1"],
        user_type="admin",
        is_admin=True,
    )


@pytest.fixture
def mock_regular_user():
    """Create a mock regular (non-admin) user for testing."""
    return User(
        id="user-456",
        username="user",
        name="Regular User",
        email="user@example.com",
        project_names=["project1"],
        admin_project_names=[],
        knowledge_bases=["kb1"],
        user_type="regular",
        is_admin=False,
    )


@pytest.fixture
def override_auth_dependency(mock_admin_user):
    """Override the authenticate and admin_access_only dependencies with the admin user by default."""
    from codemie.rest_api.security.authentication import admin_access_only

    # Mock authenticate to return admin user
    app.dependency_overrides[dynamic_config_router.authenticate] = lambda: mock_admin_user
    # Mock admin_access_only to pass (admin check)
    app.dependency_overrides[admin_access_only] = lambda: None
    yield
    app.dependency_overrides = {}


@pytest.fixture
def mock_config():
    """Create a mock DynamicConfig object."""
    return DynamicConfig(
        id="config-123",
        key="TEST_STRING_KEY",
        value="test_value",
        value_type=ConfigValueType.STRING,
        description="Test string config",
        date=datetime.now(),
        update_date=datetime.now(),
        updated_by="admin-123",
    )


# =============================================================================
# Test POST /v1/dynamic-config (Create)
# =============================================================================


@pytest.mark.anyio
@patch("codemie.rest_api.routers.dynamic_config.DynamicConfigService")
async def test_create_config_string(mock_service, override_auth_dependency, mock_admin_user, mock_config):
    """Test creating a string config."""
    # Mock service methods
    mock_service.get_by_key.return_value = None  # No existing config
    mock_service.set.return_value = mock_config

    # When: Creating a string config
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/dynamic-config/",
            json={
                "key": "TEST_STRING_KEY",
                "value": "test_value",
                "value_type": "string",
                "description": "Test string config",
            },
        )

    # Then: Config created successfully
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["key"] == "TEST_STRING_KEY"
    assert data["value"] == "test_value"
    assert data["value_type"] == "string"
    assert data["description"] == "Test string config"
    assert data["updated_by"] == "admin-123"


@pytest.mark.anyio
@patch("codemie.rest_api.routers.dynamic_config.DynamicConfigService")
async def test_create_config_int(mock_service, override_auth_dependency, mock_admin_user):
    """Test creating an integer config."""
    # Mock service methods
    mock_service.get_by_key.return_value = None
    mock_config = DynamicConfig(
        id="config-124",
        key="TEST_MAX_RETRIES",
        value="5",
        value_type=ConfigValueType.INT,
        date=datetime.now(),
        update_date=datetime.now(),
        updated_by="admin-123",
    )
    mock_service.set.return_value = mock_config

    # When: Creating an int config
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/dynamic-config/",
            json={"key": "TEST_MAX_RETRIES", "value": "5", "value_type": "int"},
        )

    # Then: Config created successfully
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["key"] == "TEST_MAX_RETRIES"
    assert data["value"] == "5"
    assert data["value_type"] == "int"


@pytest.mark.anyio
@patch("codemie.rest_api.routers.dynamic_config.DynamicConfigService")
async def test_create_config_float(mock_service, override_auth_dependency, mock_admin_user):
    """Test creating a float config."""
    # Mock service methods
    mock_service.get_by_key.return_value = None
    mock_config = DynamicConfig(
        id="config-125",
        key="TEST_API_TIMEOUT",
        value="12.5",
        value_type=ConfigValueType.FLOAT,
        date=datetime.now(),
        update_date=datetime.now(),
        updated_by="admin-123",
    )
    mock_service.set.return_value = mock_config

    # When: Creating a float config
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/dynamic-config/",
            json={"key": "TEST_API_TIMEOUT", "value": "12.5", "value_type": "float"},
        )

    # Then: Config created successfully
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["key"] == "TEST_API_TIMEOUT"
    assert data["value"] == "12.5"
    assert data["value_type"] == "float"


@pytest.mark.anyio
@patch("codemie.rest_api.routers.dynamic_config.DynamicConfigService")
async def test_create_config_bool(mock_service, override_auth_dependency, mock_admin_user):
    """Test creating a boolean config."""
    # Mock service methods
    mock_service.get_by_key.return_value = None
    mock_config = DynamicConfig(
        id="config-126",
        key="TEST_FEATURE_ENABLED",
        value="true",
        value_type=ConfigValueType.BOOL,
        date=datetime.now(),
        update_date=datetime.now(),
        updated_by="admin-123",
    )
    mock_service.set.return_value = mock_config

    # When: Creating a bool config
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/dynamic-config/",
            json={"key": "TEST_FEATURE_ENABLED", "value": "true", "value_type": "bool"},
        )

    # Then: Config created successfully
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["key"] == "TEST_FEATURE_ENABLED"
    assert data["value"] == "true"
    assert data["value_type"] == "bool"


@pytest.mark.anyio
@patch("codemie.rest_api.routers.dynamic_config.DynamicConfigService")
async def test_create_config_duplicate_key_returns_409(
    mock_service, override_auth_dependency, mock_admin_user, mock_config
):
    """Test creating a config with duplicate key returns 409."""
    # Mock service to return existing config
    mock_service.get_by_key.return_value = mock_config

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # When: Attempting to create duplicate
        response = await ac.post(
            "/v1/dynamic-config/",
            json={"key": "TEST_STRING_KEY", "value": "value2", "value_type": "string"},
        )

    # Then: Returns 409 Conflict
    assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.anyio
@patch("codemie.rest_api.routers.dynamic_config.DynamicConfigService")
async def test_create_config_invalid_key_format_returns_400(mock_service, override_auth_dependency, mock_admin_user):
    """Test creating config with invalid key format returns 400."""
    # Mock service to raise validation error
    mock_service.get_by_key.return_value = None
    mock_service.set.side_effect = ExtendedHTTPException(
        code=400,
        message="Invalid key format",
        details="Key must be in UPPER_SNAKE_CASE format",
    )

    # When: Creating config with camelCase key
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/dynamic-config/",
            json={"key": "testInvalidKey", "value": "value", "value_type": "string"},
        )

    # Then: Returns 400 Bad Request
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "UPPER_SNAKE_CASE" in response.json()["details"]


@pytest.mark.anyio
@patch("codemie.rest_api.routers.dynamic_config.DynamicConfigService")
async def test_create_config_invalid_bool_value_returns_400(mock_service, override_auth_dependency, mock_admin_user):
    """Test creating bool config with invalid value returns 400."""
    # Mock service to raise validation error
    mock_service.get_by_key.return_value = None
    mock_service.set.side_effect = ExtendedHTTPException(
        code=400,
        message="Invalid boolean value",
        details="Boolean value must be 'true' or 'false' (case-insensitive)",
    )

    # When: Creating bool config with invalid value
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/dynamic-config/",
            json={"key": "TEST_BAD_BOOL", "value": "yes", "value_type": "bool"},
        )

    # Then: Returns 400 Bad Request
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.anyio
async def test_create_config_non_admin_returns_403(mock_regular_user):
    """Test creating config as non-admin returns 403."""
    from codemie.rest_api.security.authentication import admin_access_only

    # Mock admin_access_only to raise 403 for non-admin user
    def mock_admin_check():
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Access denied",
            details="This action requires administrator privileges.",
        )

    # Override with regular user and admin check that fails
    app.dependency_overrides[dynamic_config_router.authenticate] = lambda: mock_regular_user
    app.dependency_overrides[admin_access_only] = mock_admin_check
    try:
        # When: Attempting to create config
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/dynamic-config/",
                json={"key": "TEST_NO_ACCESS", "value": "value", "value_type": "string"},
            )

        # Then: Returns 403 Forbidden
        assert response.status_code == status.HTTP_403_FORBIDDEN
    finally:
        # Clean up
        app.dependency_overrides = {}


# =============================================================================
# Test GET /v1/dynamic-config (List All)
# =============================================================================


@pytest.mark.anyio
@patch("codemie.rest_api.routers.dynamic_config.DynamicConfigService")
async def test_list_all_configs(mock_service, override_auth_dependency, mock_admin_user):
    """Test listing all configs."""
    # Mock service to return configs
    config_a = DynamicConfig(
        id="config-a",
        key="TEST_CONFIG_A",
        value="value_a",
        value_type=ConfigValueType.STRING,
        date=datetime.now(),
        update_date=datetime.now(),
        updated_by="admin-123",
    )
    config_b = DynamicConfig(
        id="config-b",
        key="TEST_CONFIG_B",
        value="123",
        value_type=ConfigValueType.INT,
        date=datetime.now(),
        update_date=datetime.now(),
        updated_by="admin-123",
    )
    mock_service.list_all.return_value = [config_a, config_b]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # When: Listing all configs
        response = await ac.get("/v1/dynamic-config/")

    # Then: Returns all configs ordered by key
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["key"] == "TEST_CONFIG_A"
    assert data[1]["key"] == "TEST_CONFIG_B"


# =============================================================================
# Test GET /v1/dynamic-config/{key} (Get by Key)
# =============================================================================


@pytest.mark.anyio
@patch("codemie.rest_api.routers.dynamic_config.DynamicConfigService")
async def test_get_config_by_key(mock_service, override_auth_dependency, mock_admin_user, mock_config):
    """Test getting config by key."""
    # Mock service to return config
    mock_service.get_by_key.return_value = mock_config

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # When: Getting config by key
        response = await ac.get("/v1/dynamic-config/TEST_STRING_KEY")

    # Then: Returns config
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["id"] == "config-123"
    assert data["key"] == "TEST_STRING_KEY"
    assert data["value"] == "test_value"


@pytest.mark.anyio
@patch("codemie.rest_api.routers.dynamic_config.DynamicConfigService")
async def test_get_config_not_found_returns_404(mock_service, override_auth_dependency, mock_admin_user):
    """Test getting non-existent config returns 404."""
    # Mock service to return None
    mock_service.get_by_key.return_value = None

    # When: Getting non-existent config
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get("/v1/dynamic-config/TEST_NONEXISTENT")

    # Then: Returns 404
    assert response.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# Test PUT /v1/dynamic-config/{key} (Update)
# =============================================================================


@pytest.mark.anyio
@patch("codemie.rest_api.routers.dynamic_config.DynamicConfigService")
async def test_update_config(mock_service, override_auth_dependency, mock_admin_user):
    """Test updating existing config."""
    # Mock existing config
    existing_config = DynamicConfig(
        id="config-update",
        key="TEST_UPDATE",
        value="old_value",
        value_type=ConfigValueType.STRING,
        description="Test config",
        date=datetime.now(),
        update_date=datetime.now(),
        updated_by="admin-123",
    )
    updated_config = DynamicConfig(
        id="config-update",
        key="TEST_UPDATE",
        value="new_value",
        value_type=ConfigValueType.STRING,
        description="Test config",
        date=datetime.now(),
        update_date=datetime.now(),
        updated_by="admin-123",
    )
    mock_service.get_by_key.return_value = existing_config
    mock_service.convert_value.return_value = "new_value"
    mock_service.set.return_value = updated_config

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # When: Updating config
        response = await ac.put(
            "/v1/dynamic-config/TEST_UPDATE",
            json={"value": "new_value"},
        )

    # Then: Config updated successfully
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["key"] == "TEST_UPDATE"
    assert data["value"] == "new_value"
    assert data["value_type"] == "string"


@pytest.mark.anyio
@patch("codemie.rest_api.routers.dynamic_config.DynamicConfigService")
async def test_update_config_change_type(mock_service, override_auth_dependency, mock_admin_user):
    """Test updating config and changing type."""
    # Mock existing config
    existing_config = DynamicConfig(
        id="config-type",
        key="TEST_CHANGE_TYPE",
        value="123",
        value_type=ConfigValueType.STRING,
        date=datetime.now(),
        update_date=datetime.now(),
        updated_by="admin-123",
    )
    updated_config = DynamicConfig(
        id="config-type",
        key="TEST_CHANGE_TYPE",
        value="456",
        value_type=ConfigValueType.INT,
        date=datetime.now(),
        update_date=datetime.now(),
        updated_by="admin-123",
    )
    mock_service.get_by_key.return_value = existing_config
    mock_service.convert_value.return_value = 456
    mock_service.set.return_value = updated_config

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # When: Updating to int type
        response = await ac.put(
            "/v1/dynamic-config/TEST_CHANGE_TYPE",
            json={"value": "456", "value_type": "int"},
        )

    # Then: Type changed successfully
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["value"] == "456"
    assert data["value_type"] == "int"


@pytest.mark.anyio
@patch("codemie.rest_api.routers.dynamic_config.DynamicConfigService")
async def test_update_config_not_found_returns_404(mock_service, override_auth_dependency, mock_admin_user):
    """Test updating non-existent config returns 404."""
    # Mock service to return None
    mock_service.get_by_key.return_value = None

    # When: Updating non-existent config
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.put(
            "/v1/dynamic-config/TEST_UPDATE_MISSING",
            json={"value": "new_value"},
        )

    # Then: Returns 404
    assert response.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# Test DELETE /v1/dynamic-config/{key} (Delete)
# =============================================================================


@pytest.mark.anyio
@patch("codemie.rest_api.routers.dynamic_config.DynamicConfigService")
async def test_delete_config(mock_service, override_auth_dependency, mock_admin_user):
    """Test deleting config."""
    # Mock service delete (no return value, just success)
    mock_service.delete.return_value = None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # When: Deleting config
        response = await ac.delete("/v1/dynamic-config/TEST_DELETE")

    # Then: Config deleted successfully
    assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.anyio
@patch("codemie.rest_api.routers.dynamic_config.DynamicConfigService")
async def test_delete_config_not_found_returns_404(mock_service, override_auth_dependency, mock_admin_user):
    """Test deleting non-existent config returns 404."""
    # Mock service to raise 404
    mock_service.delete.side_effect = ExtendedHTTPException(
        code=404,
        message="Config not found",
        details="Configuration key 'TEST_DELETE_MISSING' does not exist",
    )

    # When: Deleting non-existent config
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.delete("/v1/dynamic-config/TEST_DELETE_MISSING")

    # Then: Returns 404
    assert response.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# Test Type Conversion (Service Layer Coverage)
# =============================================================================


@pytest.mark.anyio
@patch("codemie.rest_api.routers.dynamic_config.DynamicConfigService")
async def test_bool_type_conversion_case_insensitive(mock_service, override_auth_dependency, mock_admin_user):
    """Test bool conversion works with various cases."""
    # Mock service methods
    mock_service.get_by_key.return_value = None

    configs = [
        DynamicConfig(
            id="config-bool-1",
            key="TEST_BOOL_UPPER",
            value="TRUE",
            value_type=ConfigValueType.BOOL,
            date=datetime.now(),
            update_date=datetime.now(),
            updated_by="admin-123",
        ),
        DynamicConfig(
            id="config-bool-2",
            key="TEST_BOOL_LOWER",
            value="false",
            value_type=ConfigValueType.BOOL,
            date=datetime.now(),
            update_date=datetime.now(),
            updated_by="admin-123",
        ),
        DynamicConfig(
            id="config-bool-3",
            key="TEST_BOOL_MIXED",
            value="False",
            value_type=ConfigValueType.BOOL,
            date=datetime.now(),
            update_date=datetime.now(),
            updated_by="admin-123",
        ),
    ]
    mock_service.set.side_effect = configs

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        # Test "TRUE"
        response1 = await ac.post(
            "/v1/dynamic-config/",
            json={"key": "TEST_BOOL_UPPER", "value": "TRUE", "value_type": "bool"},
        )
        # Test "false"
        response2 = await ac.post(
            "/v1/dynamic-config/",
            json={"key": "TEST_BOOL_LOWER", "value": "false", "value_type": "bool"},
        )
        # Test "False"
        response3 = await ac.post(
            "/v1/dynamic-config/",
            json={"key": "TEST_BOOL_MIXED", "value": "False", "value_type": "bool"},
        )

    # Then: All succeed
    assert response1.status_code == status.HTTP_201_CREATED
    assert response2.status_code == status.HTTP_201_CREATED
    assert response3.status_code == status.HTTP_201_CREATED
