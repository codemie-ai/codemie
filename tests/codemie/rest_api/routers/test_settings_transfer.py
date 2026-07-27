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

"""Tests for POST /v1/settings/transfer (move/copy project integrations)."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, status
from httpx import ASGITransport, AsyncClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings_transfer import TransferMode, TransferSettingsResponse
from codemie.rest_api.routers.settings import router
from codemie.rest_api.security.authentication import admin_or_maintainer_access_only
from codemie.rest_api.security.user import User

app = FastAPI()
app.include_router(router)


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
def admin_user():
    return User(id="admin-1", username="admin@example.com", roles=["admin"], project_names=[], admin_project_names=[])


# NOTE: a non-elevated User cannot be built here. User.resolve_is_admin forces is_admin=True
# whenever ENV == local (src/codemie/rest_api/security/user.py:60-61), and pytest.ini sets
# ENV=local. The guard is therefore covered by two tests: one overriding the dependency to
# prove it is wired to the route, and one calling the guard directly to prove it denies.


def _ok_response():
    return TransferSettingsResponse(
        message="Transferred 1 integration(s) from 'source' to 'target'.",
        source_project_name="source",
        target_project_name="target",
        mode=TransferMode.MOVE,
        transferred_count=1,
        transferred=[{"id": "row-1", "alias": "jira-prod", "credential_type": "Jira"}],
        skipped_count=0,
        skipped=[],
    )


@pytest.mark.anyio
@patch("codemie.rest_api.routers.settings.SettingsTransferService.transfer")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_transfer_move_success(mock_authenticate, mock_transfer, admin_user):
    # Arrange
    mock_authenticate.return_value = admin_user
    mock_transfer.return_value = _ok_response()

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post(
            "/v1/settings/transfer",
            headers={"user-id": "admin-1"},
            json={"source_project_name": "source", "target_project_name": "target", "mode": "move"},
        )

    # Assert
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["transferred_count"] == 1
    assert body["mode"] == "move"
    mock_transfer.assert_called_once_with(
        source_project_name="source", target_project_name="target", mode=TransferMode.MOVE
    )


@pytest.mark.anyio
async def test_admin_guard_denies_non_elevated_user():
    """The guard itself rejects a user without global elevated permissions."""
    # Arrange
    request = MagicMock()
    request.state.user.id = "user-1"
    request.state.user.is_admin_or_maintainer = False

    # Act / Assert
    with pytest.raises(ExtendedHTTPException) as excinfo:
        await admin_or_maintainer_access_only(request)

    assert excinfo.value.code == status.HTTP_403_FORBIDDEN


@pytest.mark.anyio
@patch("codemie.rest_api.routers.settings.SettingsTransferService.transfer")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_transfer_is_guarded_by_admin_dependency(mock_authenticate, mock_transfer, admin_user):
    """A denial from the admin guard blocks the route before the service is reached."""
    # Arrange
    mock_authenticate.return_value = admin_user

    def _deny():
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Access denied",
            details="This action requires administrator or maintainer privileges.",
        )

    app.dependency_overrides[admin_or_maintainer_access_only] = _deny

    # Act / Assert
    try:
        transport = ASGITransport(app=app)
        with pytest.raises(ExtendedHTTPException) as excinfo:
            async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
                await ac.post(
                    "/v1/settings/transfer",
                    headers={"user-id": "admin-1"},
                    json={"source_project_name": "source", "target_project_name": "target", "mode": "move"},
                )

        assert excinfo.value.code == status.HTTP_403_FORBIDDEN
        mock_transfer.assert_not_called()
    finally:
        app.dependency_overrides = {}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {"source_project_name": "source", "target_project_name": "target"},
        {"source_project_name": "source", "target_project_name": "target", "mode": "teleport"},
        {"source_project_name": "", "target_project_name": "target", "mode": "move"},
    ],
    ids=["missing-mode", "unsupported-mode", "blank-source"],
)
@patch("codemie.rest_api.routers.settings.SettingsTransferService.transfer")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_transfer_rejects_invalid_payload(mock_authenticate, mock_transfer, admin_user, payload):
    # Arrange
    mock_authenticate.return_value = admin_user

    # Act
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.post("/v1/settings/transfer", headers={"user-id": "admin-1"}, json=payload)

    # Assert
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    mock_transfer.assert_not_called()


@pytest.mark.anyio
@patch("codemie.rest_api.routers.settings.SettingsTransferService.transfer")
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_transfer_propagates_service_errors(mock_authenticate, mock_transfer, admin_user):
    # Arrange
    mock_authenticate.return_value = admin_user
    mock_transfer.side_effect = ExtendedHTTPException(
        code=status.HTTP_409_CONFLICT, message="Alias conflict in target project", details="jira-prod"
    )

    # Act / Assert
    transport = ASGITransport(app=app)
    with pytest.raises(ExtendedHTTPException) as excinfo:
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            await ac.post(
                "/v1/settings/transfer",
                headers={"user-id": "admin-1"},
                json={"source_project_name": "source", "target_project_name": "target", "mode": "move"},
            )

    assert excinfo.value.code == status.HTTP_409_CONFLICT
