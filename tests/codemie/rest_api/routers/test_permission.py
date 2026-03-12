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
from unittest.mock import patch, ANY
from datetime import datetime
from fastapi import FastAPI
from fastapi import status
from fastapi.testclient import TestClient

from codemie.core.models import CreatedByUser
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.permission import Permission
from codemie.service.permission import (
    PermissionCreationService,
    PermissionDeletionService,
    PermissionAccessDenied,
    PermissionResourceNotFound,
    PermissionPrincipalNotFound,
)
from codemie.rest_api.security.user import User
from codemie.rest_api.routers.permission import router

app = FastAPI()
app.include_router(router)
client = TestClient(app)


@pytest.fixture
def mock_user():
    return User(id="user123", email="test@example.com")


@pytest.fixture
def mock_headers():
    return {"Authorization": "Bearer testtoken"}


@pytest.fixture
def sample_permission_request():
    return {
        "resource_id": "a45126c8-c952-4e40-8e36-d9604458a5f8",
        "resource_type": "datasource",
        "principal_id": "user456",
        "principal_type": "user",
        "permission_level": "read",
    }


@pytest.fixture
def sample_permission():
    return Permission(
        id="perm123",
        resource_id="a45126c8-c952-4e40-8e36-d9604458a5f8",
        resource_type="datasource",
        principal_id="user456",
        principal_type="user",
        permission_level="read",
        date=datetime.now(),
        created_by=CreatedByUser(
            id="test-user",
            username="test-user",
            name="TestUser",
        ),
    )


PERMISSIONS_PATH = "/v1/permissions"
NOT_FOUND_MSG = "not_found"


@patch("codemie.rest_api.security.authentication.authenticate")
@patch.object(PermissionCreationService, "run")
def test_create_permission_success(
    mock_create, mock_auth, mock_user, sample_permission_request, sample_permission, mock_headers
):
    """Test successful permission creation"""
    mock_auth.return_value = mock_user
    mock_create.return_value = (sample_permission, status.HTTP_201_CREATED)

    response = client.post(PERMISSIONS_PATH, headers=mock_headers, json=sample_permission_request)
    json = response.json()

    assert response.status_code == status.HTTP_201_CREATED

    assert json["id"] == "perm123"
    assert json["resource_type"] == "datasource"
    assert json["resource_id"] == "a45126c8-c952-4e40-8e36-d9604458a5f8"
    assert json["principal_type"] == "user"
    assert json["principal_id"] == "user456"
    assert json["permission_level"] == "read"

    created_by = json["created_by"]
    assert created_by["id"] == "test-user"
    assert created_by["username"] == "test-user"
    assert created_by["name"] == "TestUser"

    mock_create.assert_called_once()


@patch("codemie.rest_api.security.authentication.authenticate")
@patch.object(PermissionCreationService, "run")
def test_create_permission_already_exists(
    mock_create, mock_auth, mock_user, sample_permission_request, sample_permission, mock_headers
):
    """Test permission creation when it already exists"""
    mock_auth.return_value = mock_user
    mock_create.return_value = (sample_permission, status.HTTP_200_OK)

    response = client.post(PERMISSIONS_PATH, headers=mock_headers, json=sample_permission_request)
    json = response.json()

    assert response.status_code == status.HTTP_200_OK

    assert json["id"] == "perm123"
    assert json["resource_id"] == "a45126c8-c952-4e40-8e36-d9604458a5f8"
    assert json["resource_type"] == "datasource"
    assert json["principal_id"] == "user456"
    assert json["principal_type"] == "user"
    assert json["permission_level"] == "read"

    mock_create.assert_called_once()


@patch("codemie.rest_api.security.authentication.authenticate")
@patch.object(PermissionCreationService, "run")
def test_create_permission_resource_not_found(
    mock_create, mock_auth, mock_user, sample_permission_request, mock_headers
):
    """Test permission creation with resource not found error"""
    mock_auth.return_value = mock_user
    mock_create.side_effect = PermissionResourceNotFound()

    with pytest.raises(ExtendedHTTPException) as exc_info:
        client.post("/v1/permissions", headers=mock_headers, json=sample_permission_request)

    assert exc_info.value.code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.message == "Datasource not found"
    assert (
        exc_info.value.details
        == "The Datasource with ID [a45126c8-c952-4e40-8e36-d9604458a5f8] could not be found in the system."
    )
    assert exc_info.value.help == "Please ensure the specified ID is correct"


@patch("codemie.rest_api.security.authentication.authenticate")
@patch.object(PermissionCreationService, "run")
def test_create_permission_principal_not_found(
    mock_create, mock_auth, mock_user, sample_permission_request, mock_headers
):
    """Test permission creation with principal not found error"""
    mock_auth.return_value = mock_user
    mock_create.side_effect = PermissionPrincipalNotFound()

    with pytest.raises(ExtendedHTTPException) as exc_info:
        client.post(PERMISSIONS_PATH, headers=mock_headers, json=sample_permission_request)

    assert exc_info.value.code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.message == "Principal of type User not found"
    assert exc_info.value.details == "The Principal of type User with ID [user456] could not be found in the system."
    assert exc_info.value.help == "Please ensure the specified ID is correct"


@patch("codemie.rest_api.security.authentication.authenticate")
@patch.object(PermissionCreationService, "run")
def test_create_permission_access_denied(mock_create, mock_auth, mock_user, sample_permission_request, mock_headers):
    """Test permission creation with access denied error"""
    mock_auth.return_value = mock_user
    mock_create.side_effect = PermissionAccessDenied()

    with pytest.raises(ExtendedHTTPException) as exc_info:
        client.post(PERMISSIONS_PATH, headers=mock_headers, json=sample_permission_request)

    assert exc_info.value.code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.message == "Access denied"


@patch("codemie.rest_api.security.authentication.authenticate")
@patch.object(PermissionCreationService, "run")
@patch("codemie.configs.logger")
def test_create_permission_unprocessable_entity(
    mock_logger, mock_create, mock_auth, mock_user, sample_permission_request, mock_headers
):
    """Test permission creation with general exception"""
    mock_auth.return_value = mock_user
    mock_create.side_effect = ValueError("Invalid data")

    with pytest.raises(ExtendedHTTPException) as exc_info:
        client.post(PERMISSIONS_PATH, headers=mock_headers, json=sample_permission_request)

    assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert exc_info.value.message == "Failed to create a permission"


@patch("codemie.rest_api.security.authentication.authenticate")
@patch.object(PermissionDeletionService, "run")
def test_delete_permission_success(mock_delete, mock_auth, mock_user, mock_headers):
    """Test successful permission deletion"""
    permission_id = "perm123"
    mock_auth.return_value = mock_user

    response = client.delete(f"{PERMISSIONS_PATH}/{permission_id}", headers=mock_headers)

    assert response.status_code == status.HTTP_200_OK
    mock_delete.assert_called_once_with(permission_id=permission_id, user=ANY)


@patch("codemie.rest_api.security.authentication.authenticate")
@patch.object(PermissionDeletionService, "run")
def test_delete_permission_not_found(mock_delete, mock_auth, mock_user, mock_headers):
    """Test permission deletion with not found error"""
    permission_id = "perm123"
    mock_auth.return_value = mock_user
    mock_delete.side_effect = PermissionResourceNotFound()

    with pytest.raises(ExtendedHTTPException) as exc_info:
        client.delete(f"{PERMISSIONS_PATH}/{permission_id}", headers=mock_headers)

    assert exc_info.value.code == status.HTTP_404_NOT_FOUND
    assert exc_info.value.message == "Permission not found"


@patch("codemie.rest_api.security.authentication.authenticate")
@patch.object(PermissionDeletionService, "run")
def test_delete_permission_access_denied(mock_delete, mock_auth, mock_user, mock_headers):
    """Test permission deletion with access denied error"""
    permission_id = "perm123"
    mock_auth.return_value = mock_user
    mock_delete.side_effect = PermissionAccessDenied()

    with pytest.raises(ExtendedHTTPException) as exc_info:
        client.delete(f"{PERMISSIONS_PATH}/{permission_id}", headers=mock_headers)

    assert exc_info.value.code == status.HTTP_401_UNAUTHORIZED
    assert exc_info.value.message == "Access denied"


@patch("codemie.rest_api.security.authentication.authenticate")
@patch.object(PermissionDeletionService, "run")
@patch("codemie.configs.logger")
def test_delete_permission_unprocessable_entity(mock_logger, mock_delete, mock_auth, mock_user, mock_headers):
    """Test permission deletion with general exception"""
    permission_id = "perm123"
    mock_auth.return_value = mock_user
    mock_delete.side_effect = ValueError("Database error")

    with pytest.raises(ExtendedHTTPException) as exc_info:
        client.delete(f"{PERMISSIONS_PATH}/{permission_id}", headers=mock_headers)

    assert exc_info.value.code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert exc_info.value.message == "Failed to delete a permission"
