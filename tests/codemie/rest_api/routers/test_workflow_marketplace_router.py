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

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient

from codemie.core.models import UserEntity
from codemie.core.workflow_models.workflow_config import WorkflowConfig
from codemie.core.workflow_models.workflow_models import WorkflowAssistant
from codemie.rest_api.main import app
from codemie.rest_api.security.user import User

_WORKFLOW_ID = "wf-123"
_VALIDATE_URL = f"/v1/workflows/{_WORKFLOW_ID}/marketplace/publish/validate"
_PUBLISH_URL = f"/v1/workflows/{_WORKFLOW_ID}/marketplace/publish"
_UNPUBLISH_URL = f"/v1/workflows/{_WORKFLOW_ID}/marketplace/unpublish"

# Only DB-level calls are mocked — the service/ability layers run for real.
_GET_BY_ID = "codemie.core.workflow_models.workflow_config.WorkflowConfig.get_by_id"
_ASSISTANT_GET_BY_IDS = "codemie.rest_api.models.assistant.Assistant.get_by_ids"
_CATEGORY_REPO_PATCH = "codemie.service.workflow_config.workflow_marketplace_service._category_repository.get_by_ids"
_CONFIG_REPO_PATCH = "codemie.service.workflow_config.workflow_marketplace_service._workflow_config_repository"


@pytest.fixture
def user() -> User:
    # Patch config so that ENV != "local" during User construction — otherwise
    # resolve_is_admin() unconditionally sets is_admin=True for all users,
    # which makes every Ability check pass and breaks permission-failure tests.
    with patch("codemie.rest_api.security.user.config") as mock_cfg:
        mock_cfg.ENV = "prod"
        mock_cfg.ENABLE_USER_MANAGEMENT = True
        return User(id="u-1", username="testuser", name="Test User", auth_token=None)


@pytest.fixture(autouse=True)
def override_auth(user: User) -> Generator[None, None, None]:
    from codemie.rest_api.routers import workflow_marketplace as workflow_marketplace_router

    app.dependency_overrides[workflow_marketplace_router.authenticate] = lambda: user
    yield
    app.dependency_overrides = {}


def _make_workflow(
    *,
    owned: bool = True,
    shared: bool = False,
    assistants: list | None = None,
    tools: list | None = None,
) -> MagicMock:
    """Return a WorkflowConfig mock with permission methods configured.

    Sets bedrock=None so that Ability._is_remote_entity does not block WRITE.
    """
    wf = MagicMock(spec=WorkflowConfig)
    wf.id = _WORKFLOW_ID
    wf.bedrock = None  # _is_remote_entity checks this; None → not a remote entity
    wf.is_owned_by.return_value = owned
    wf.is_managed_by.return_value = False
    wf.is_shared_with.return_value = shared
    wf.assistants = assistants if assistants is not None else []
    wf.tools = tools if tools is not None else []
    return wf


def _make_virtual_step(*, mcp_server: MagicMock | None = None, tools: list | None = None) -> MagicMock:
    step = MagicMock(spec=WorkflowAssistant)
    step.assistant_id = None
    step.skill_ids = []
    step.mcp_servers = [mcp_server] if mcp_server else []
    step.tools = tools or []
    return step


def _make_external_step(assistant_id: str) -> MagicMock:
    step = MagicMock(spec=WorkflowAssistant)
    step.assistant_id = assistant_id
    return step


def _make_mcp_server_with_env_credentials(name: str = "my-mcp") -> MagicMock:
    server = MagicMock()
    server.name = name
    server.settings = MagicMock()
    server.settings.credential_values = ["val"]
    server.mcp_connect_auth_token = None
    server.config = None
    server.integration_alias = None
    return server


def _make_mcp_server_with_integration_alias(name: str = "my-mcp", alias: str = "my-alias") -> MagicMock:
    server = MagicMock()
    server.name = name
    server.settings = None
    server.mcp_connect_auth_token = None
    server.config = None
    server.integration_alias = alias
    return server


# ---------------------------------------------------------------------------
# validate — 200: clean workflow (no external refs, no inline credentials)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_clean_workflow_returns_200() -> None:
    workflow = _make_workflow(owned=True)

    with patch(_GET_BY_ID, return_value=workflow):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(_VALIDATE_URL, headers={"Authorization": "Bearer token"})

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["workflow_id"] == _WORKFLOW_ID
    assert body["inline_credentials"] == []
    assert "ready to be published" in body["message"]


# ---------------------------------------------------------------------------
# validate — 200: virtual step with MCP inline credentials → requires confirmation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_with_mcp_inline_credentials_returns_200_with_requires_confirmation() -> None:
    mcp_server = _make_mcp_server_with_env_credentials("my-mcp")
    step = _make_virtual_step(mcp_server=mcp_server)
    workflow = _make_workflow(owned=True, assistants=[step])

    with patch(_GET_BY_ID, return_value=workflow):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(_VALIDATE_URL, headers={"Authorization": "Bearer token"})

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["workflow_id"] == _WORKFLOW_ID
    assert len(body["inline_credentials"]) == 1
    assert body["inline_credentials"][0]["mcp_server"] == "my-mcp"
    assert body["inline_credentials"][0]["credential_type"] == "mcp_environment_vars"


# ---------------------------------------------------------------------------
# validate — 200: virtual step with MCP integration alias → requires confirmation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_with_mcp_integration_alias_returns_200_with_requires_confirmation() -> None:
    mcp_server = _make_mcp_server_with_integration_alias("my-mcp", "my-alias")
    step = _make_virtual_step(mcp_server=mcp_server)
    workflow = _make_workflow(owned=True, assistants=[step])

    with patch(_GET_BY_ID, return_value=workflow):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(_VALIDATE_URL, headers={"Authorization": "Bearer token"})

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["workflow_id"] == _WORKFLOW_ID
    assert len(body["inline_credentials"]) == 1
    assert body["inline_credentials"][0]["mcp_server"] == "my-mcp"
    assert body["inline_credentials"][0]["credential_type"] == "mcp_integration_alias"
    assert body["inline_credentials"][0]["integration_alias"] == "my-alias"


# ---------------------------------------------------------------------------
# validate — 404: workflow not found in DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_returns_404_when_workflow_not_found() -> None:
    with patch(_GET_BY_ID, side_effect=KeyError("not found")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(_VALIDATE_URL, headers={"Authorization": "Bearer token"})

    assert response.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# validate — 404: user has no READ permission (existence is hidden by design)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_returns_404_when_user_has_no_read_permission() -> None:
    workflow = _make_workflow(owned=False, shared=False)  # not readable

    with patch(_GET_BY_ID, return_value=workflow):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(_VALIDATE_URL, headers={"Authorization": "Bearer token"})

    assert response.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# validate — 403: user can read but not write (e.g. shared/global workflow)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_returns_403_when_user_has_no_write_permission() -> None:
    # shared=True → READ granted via is_shared_with; owned=False → WRITE denied
    workflow = _make_workflow(owned=False, shared=True)

    with patch(_GET_BY_ID, return_value=workflow):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(_VALIDATE_URL, headers={"Authorization": "Bearer token"})

    assert response.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# validate — 400: external assistant reference blocks publication
# ValidationException bubbles to global handler → 400
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_returns_400_when_workflow_references_external_assistant() -> None:
    external_step = _make_external_step("asst-1")
    workflow = _make_workflow(owned=True, assistants=[external_step])

    fake_assistant = MagicMock()
    fake_assistant.id = "asst-1"
    fake_assistant.skill_ids = []
    fake_assistant.assistant_ids = []

    with (
        patch(_GET_BY_ID, return_value=workflow),
        patch(_ASSISTANT_GET_BY_IDS, return_value=[fake_assistant]),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(_VALIDATE_URL, headers={"Authorization": "Bearer token"})

    assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# validate — 400: referenced entity not found in DB (NotFoundException → ValidationException)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_returns_400_when_referenced_assistant_missing_from_db() -> None:
    external_step = _make_external_step("asst-missing")
    workflow = _make_workflow(owned=True, assistants=[external_step])

    with (
        patch(_GET_BY_ID, return_value=workflow),
        patch(_ASSISTANT_GET_BY_IDS, return_value=[]),  # DB returns nothing → NotFoundException → ValidationException
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(_VALIDATE_URL, headers={"Authorization": "Bearer token"})

    assert response.status_code == status.HTTP_400_BAD_REQUEST


# ===========================================================================
# publish endpoint
# ===========================================================================


def _make_owned_real_workflow() -> WorkflowConfig:
    """Return a real WorkflowConfig instance owned by user u-1."""
    return WorkflowConfig(
        id=_WORKFLOW_ID,
        name="test-workflow",
        description="test description",
        created_by=UserEntity(user_id="u-1", username="testuser"),
    )


def _make_db_category(cat_id: str) -> MagicMock:
    cat = MagicMock()
    cat.id = cat_id
    return cat


# ---------------------------------------------------------------------------
# publish — 200: owned workflow, valid categories
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_returns_200_and_marks_workflow_global() -> None:
    workflow = _make_owned_real_workflow()

    with (
        patch(_GET_BY_ID, return_value=workflow),
        patch(_ASSISTANT_GET_BY_IDS, return_value=[]),
        patch(_CATEGORY_REPO_PATCH, return_value=[_make_db_category("cat-1")]),
        patch(_CONFIG_REPO_PATCH),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(
                _PUBLISH_URL,
                json={"categories": ["cat-1"]},
                headers={"Authorization": "Bearer token"},
            )

    assert response.status_code == status.HTTP_200_OK
    assert workflow.is_global is True
    assert workflow.categories == ["cat-1"]


# ---------------------------------------------------------------------------
# publish — 404: workflow not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_returns_404_when_workflow_not_found() -> None:
    with patch(_GET_BY_ID, side_effect=KeyError("not found")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(
                _PUBLISH_URL,
                json={"categories": ["cat-1"]},
                headers={"Authorization": "Bearer token"},
            )

    assert response.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# publish — 404: user has no READ permission
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_returns_404_when_user_has_no_read_permission() -> None:
    workflow = _make_workflow(owned=False, shared=False)

    with patch(_GET_BY_ID, return_value=workflow):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(
                _PUBLISH_URL,
                json={"categories": ["cat-1"]},
                headers={"Authorization": "Bearer token"},
            )

    assert response.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# publish — 403: user can read but not write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_returns_403_when_user_has_no_write_permission() -> None:
    workflow = _make_workflow(owned=False, shared=True)

    with patch(_GET_BY_ID, return_value=workflow):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(
                _PUBLISH_URL,
                json={"categories": ["cat-1"]},
                headers={"Authorization": "Bearer token"},
            )

    assert response.status_code == status.HTTP_403_FORBIDDEN


# ---------------------------------------------------------------------------
# publish — 400: external assistant blocks publication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_returns_400_when_workflow_references_external_assistant() -> None:
    external_step = _make_external_step("asst-1")
    workflow = _make_workflow(owned=True, assistants=[external_step])

    fake_assistant = MagicMock()
    fake_assistant.id = "asst-1"
    fake_assistant.skill_ids = []
    fake_assistant.assistant_ids = []

    with (
        patch(_GET_BY_ID, return_value=workflow),
        patch(_ASSISTANT_GET_BY_IDS, return_value=[fake_assistant]),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(
                _PUBLISH_URL,
                json={"categories": ["cat-1"]},
                headers={"Authorization": "Bearer token"},
            )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# publish — 400: invalid category IDs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_returns_400_when_category_ids_are_invalid() -> None:
    workflow = _make_workflow(owned=True)

    with (
        patch(_GET_BY_ID, return_value=workflow),
        patch(_ASSISTANT_GET_BY_IDS, return_value=[]),
        patch(_CATEGORY_REPO_PATCH, return_value=[]),  # none of the requested categories exist
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(
                _PUBLISH_URL,
                json={"categories": ["nonexistent"]},
                headers={"Authorization": "Bearer token"},
            )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# publish — 422: invalid request body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_returns_422_when_categories_is_empty_list() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        response = await ac.post(
            _PUBLISH_URL,
            json={"categories": []},
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_publish_returns_422_when_categories_contains_blank_string() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        response = await ac.post(
            _PUBLISH_URL,
            json={"categories": [""]},
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.asyncio
async def test_publish_returns_422_when_categories_contains_duplicates() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        response = await ac.post(
            _PUBLISH_URL,
            json={"categories": ["cat-1", "cat-1"]},
            headers={"Authorization": "Bearer token"},
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# ===========================================================================
# unpublish endpoint
# ===========================================================================


# ---------------------------------------------------------------------------
# unpublish — 200: owned workflow → sets is_global=False, categories unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unpublish_returns_200_and_clears_is_global() -> None:
    workflow = _make_owned_real_workflow()
    workflow.is_global = True
    workflow.categories = ["cat-1"]

    with (
        patch(_GET_BY_ID, return_value=workflow),
        patch(_CONFIG_REPO_PATCH),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(_UNPUBLISH_URL, headers={"Authorization": "Bearer token"})

    assert response.status_code == status.HTTP_200_OK
    assert workflow.is_global is False
    assert workflow.categories == ["cat-1"]  # categories are NOT cleared on unpublish


# ---------------------------------------------------------------------------
# unpublish — 404: workflow not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unpublish_returns_404_when_workflow_not_found() -> None:
    with patch(_GET_BY_ID, side_effect=KeyError("not found")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(_UNPUBLISH_URL, headers={"Authorization": "Bearer token"})

    assert response.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# unpublish — 404: user cannot read workflow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unpublish_returns_404_when_user_has_no_read_permission() -> None:
    workflow = _make_workflow(owned=False, shared=False)

    with patch(_GET_BY_ID, return_value=workflow):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(_UNPUBLISH_URL, headers={"Authorization": "Bearer token"})

    assert response.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------------
# unpublish — 403: user can read but cannot write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unpublish_returns_403_when_user_has_no_write_permission() -> None:
    workflow = _make_workflow(owned=False, shared=True)

    with patch(_GET_BY_ID, return_value=workflow):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
            response = await ac.post(_UNPUBLISH_URL, headers={"Authorization": "Bearer token"})

    assert response.status_code == status.HTTP_403_FORBIDDEN
