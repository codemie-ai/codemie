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

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import extended_http_exception_handler
from codemie.rest_api.models.deployment_version import DeploymentVersion
from codemie.rest_api.routers import common as common_router
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

app = FastAPI()
app.include_router(common_router.router)
app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mock_user():
    return User(
        id="user-1",
        username="tester",
        name="Test User",
        email="tester@example.com",
        project_names=["project1"],
        admin_project_names=[],
        knowledge_bases=[],
        user_type="regular",
        is_admin=False,
    )


@pytest.fixture
def override_auth(mock_user):
    app.dependency_overrides[authenticate] = lambda: mock_user
    yield
    app.dependency_overrides.pop(authenticate, None)


@pytest.mark.anyio
async def test_app_info_does_not_query_deployment_version_or_expose_deployed_at():
    """GET /v1/info must not hit deployment service and must not include deployedAt."""
    with patch(
        "codemie.service.deployment.deployment_version_service.DeploymentVersionService.list_deployments"
    ) as mock_list:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/v1/info")

    assert resp.status_code == 200
    data = resp.json()
    assert "deployedAt" not in data
    assert "deployed_at" not in data
    mock_list.assert_not_called()


@pytest.mark.anyio
async def test_list_deployment_versions_empty(override_auth):
    with patch(
        "codemie.service.deployment.deployment_version_service.DeploymentVersionService.list_deployments",
        return_value=[],
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/v1/deployment-versions")

    assert resp.status_code == 200
    assert resp.json() == {"deployments": []}


@pytest.mark.anyio
async def test_list_deployment_versions_returns_camel_case_items(override_auth):
    record = DeploymentVersion(
        id="1",
        version="2.40.0",
        deployed_at=datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc),
    )
    with patch(
        "codemie.service.deployment.deployment_version_service.DeploymentVersionService.list_deployments",
        return_value=[record],
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/v1/deployment-versions")

    assert resp.status_code == 200
    data = resp.json()
    assert data["deployments"][0]["version"] == "2.40.0"
    assert "deployedAt" in data["deployments"][0]
    assert "deployed_at" not in data["deployments"][0]


@pytest.mark.anyio
@pytest.mark.parametrize("flag", [True, False])
async def test_app_info_reports_the_configured_idp_managed_flag(flag):
    """GET /v1/info exposes idpManagedEnabled (camelCase) with the configured value, not just the default."""
    with patch("codemie.rest_api.routers.common.config") as mock_config:
        mock_config.APP_VERSION = "test-version"
        mock_config.FILE_DATASOURCE_MAX_UPLOAD_COUNT = 5
        mock_config.IDP_MANAGED_ENABLED = flag
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/v1/info")

    assert resp.status_code == 200
    assert resp.json()["idpManagedEnabled"] is flag


@pytest.mark.anyio
async def test_list_deployment_versions_requires_auth():
    """Without credentials, authenticate raises ExtendedHTTPException → 401."""
    app.dependency_overrides.pop(authenticate, None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/deployment-versions")
    assert resp.status_code == 401
    assert resp.json()["error"]["message"] == "Authentication failed"
