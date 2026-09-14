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

# ruff: noqa: E402

import sys
from unittest.mock import MagicMock, patch

# Stub optional native packages that may not be available/compiled in all environments
# (e.g. switchyard requires Rust/C++ compilers on Windows).
# This must run before any codemie imports so the stubs are in place at import time.
for _missing_pkg in ("switchyard", "switchyard.libsy"):
    if _missing_pkg not in sys.modules:
        sys.modules[_missing_pkg] = MagicMock()

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import app
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mock_user():
    # Patch config so is_admin is not forced to True by the dev/local environment check
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
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
def mock_unauthorized_user():
    # Patch config so is_admin is not forced to True by the dev/local environment check
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        return User(
            id="user-2",
            username="unauthorized",
            name="Unauthorized User",
            email="unauthorized@example.com",
            project_names=["unrelated-project"],
            admin_project_names=[],
            knowledge_bases=[],
            user_type="regular",
            is_admin=False,
        )


@pytest.fixture
def private_assistant():
    return Assistant(
        id="private-id-123",
        name="Private Assistant",
        description="Private Description",
        system_prompt="Private system prompt",
        project="project1",
        shared=True,
        is_global=False,
    )


@pytest.fixture
def global_assistant():
    return Assistant(
        id="global-id-123",
        name="Global Assistant",
        description="Global Description",
        system_prompt="Global system prompt",
        project="global-project",
        shared=True,
        is_global=True,
    )


@pytest.mark.anyio
@patch("codemie.rest_api.routers.a2a.Assistant.find_by_id")
@patch("codemie.rest_api.routers.a2a.authenticate")
async def test_get_agent_card_anonymous_raises_401_on_private_assistant(
    mock_authenticate, mock_find_by_id, private_assistant
):
    """Anonymous request to private/project assistant must return 401 Unauthorized."""
    mock_find_by_id.return_value = private_assistant
    mock_authenticate.side_effect = ExtendedHTTPException(
        code=status.HTTP_401_UNAUTHORIZED,
        message="Authentication failed",
        details="No valid credentials provided.",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        resp = await ac.get("/v1/a2a/assistants/private-id-123/.well-known/agent.json")

    assert resp.status_code == 401
    data = resp.json()
    assert data["error"]["message"] == "Authentication failed"
    mock_authenticate.assert_called_once()
    mock_find_by_id.assert_called_once_with("private-id-123")


@pytest.mark.anyio
@patch("codemie.rest_api.routers.a2a.Assistant.find_by_id")
@patch("codemie.rest_api.routers.a2a.authenticate")
async def test_get_agent_card_authorized_succeeds_on_private_assistant(
    mock_authenticate, mock_find_by_id, private_assistant, mock_user
):
    """Authenticated request with READ access on a private assistant must succeed (200 OK)."""
    mock_find_by_id.return_value = private_assistant
    mock_authenticate.return_value = mock_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        resp = await ac.get("/v1/a2a/assistants/private-id-123/.well-known/agent.json")

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Private Assistant"
    assert data["description"] == "Private Description"
    mock_authenticate.assert_called_once()


@pytest.mark.anyio
@patch("codemie.rest_api.routers.a2a.Assistant.find_by_id")
@patch("codemie.rest_api.routers.a2a.authenticate")
async def test_get_agent_card_unauthorized_raises_403_on_private_assistant(
    mock_authenticate, mock_find_by_id, private_assistant, mock_unauthorized_user
):
    """Authenticated request without READ access on a private assistant must return 403 Forbidden."""
    mock_find_by_id.return_value = private_assistant
    mock_authenticate.return_value = mock_unauthorized_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        resp = await ac.get("/v1/a2a/assistants/private-id-123/.well-known/agent.json")

    assert resp.status_code == 403
    data = resp.json()
    assert "Access Denied" in data["error"]["message"] or "Access Denied" in data["error"].get("details", "")
    mock_authenticate.assert_called_once()


@pytest.mark.anyio
@patch("codemie.rest_api.routers.a2a.Assistant.find_by_id")
@patch("codemie.rest_api.routers.a2a.authenticate")
async def test_get_agent_card_anonymous_succeeds_on_global_assistant(
    mock_authenticate, mock_find_by_id, global_assistant
):
    """Anonymous request on intentionally public (global/marketplace) assistant must succeed (200 OK) without calling authenticate."""
    mock_find_by_id.return_value = global_assistant

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        resp = await ac.get("/v1/a2a/assistants/global-id-123/.well-known/agent.json")

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Global Assistant"
    assert data["description"] == "Global Description"
    mock_authenticate.assert_not_called()


@pytest.mark.anyio
@patch("codemie.rest_api.routers.a2a.Assistant.find_by_id")
async def test_get_agent_card_missing_raises_404(mock_find_by_id):
    """Requesting non-existent assistant card must return 404 Not Found."""
    mock_find_by_id.return_value = None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        resp = await ac.get("/v1/a2a/assistants/missing-id-123/.well-known/agent.json")

    assert resp.status_code == 404
    data = resp.json()
    assert "wasn't found" in data["error"]["message"]
