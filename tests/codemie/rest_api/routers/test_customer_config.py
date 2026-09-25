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

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers.customer_config import router
from codemie.rest_api.security.authentication import authenticate

_BANNER_SETTING_URL = "/v1/config/declarations/banner"
_DECLARATIONS_URL = "/v1/config/declarations"
_COMPONENT_ID = "component_id"
_BANNER = "banner"
_SETTINGS = "settings"


def _app(*, elevated: bool) -> FastAPI:
    """Router app whose caller is authenticated, with or without elevated permissions.

    Only ``authenticate`` is overridden: the real ``require_customer_config_write`` runs,
    so the 403 cases exercise the guard rather than a stub of it.
    """
    app = FastAPI()
    app.include_router(router)

    async def _authenticate(request: Request):
        request.state.user = SimpleNamespace(id="user-1", is_admin_or_maintainer=elevated)

    app.dependency_overrides[authenticate] = _authenticate

    @app.exception_handler(ExtendedHTTPException)
    async def _handle(_request: Request, exc: ExtendedHTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.code, content={"message": exc.message})

    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_public_config_needs_no_authentication():
    app = FastAPI()
    app.include_router(router)

    with patch(
        "codemie.rest_api.routers.customer_config.resolve_components",
        AsyncMock(return_value=[]),
    ):
        async with _client(app) as client:
            response = await client.get("/v1/config")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_declarations_are_listed_for_an_authorised_caller():
    declarations = [
        {
            _COMPONENT_ID: _BANNER,
            "label": "Banner",
            "description": None,
            "overridden": False,
            "value": {"enabled": False, "message": "", "linkLabel": "", "linkRoute": ""},
            "fields": [],
        }
    ]

    with patch(
        "codemie.rest_api.routers.customer_config.list_settings",
        AsyncMock(return_value=declarations),
    ):
        async with _client(_app(elevated=True)) as client:
            response = await client.get(_DECLARATIONS_URL)

    assert response.status_code == 200
    assert response.json()[0][_COMPONENT_ID] == _BANNER


@pytest.mark.asyncio
async def test_declarations_are_denied_without_elevated_permissions():
    async with _client(_app(elevated=False)) as client:
        response = await client.get(_DECLARATIONS_URL)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_saving_a_setting_returns_the_stored_settings():
    stored = {"enabled": True, "message": "Maintenance", "linkLabel": "", "linkRoute": ""}

    with patch(
        "codemie.rest_api.routers.customer_config.save_setting",
        AsyncMock(return_value=stored),
    ) as save:
        async with _client(_app(elevated=True)) as client:
            response = await client.put(_BANNER_SETTING_URL, json={_SETTINGS: stored})

    assert response.status_code == 200
    assert response.json() == {_COMPONENT_ID: _BANNER, "settings": stored}
    assert save.await_args.args[0] == _BANNER


@pytest.mark.asyncio
async def test_saving_a_setting_is_denied_without_elevated_permissions():
    async with _client(_app(elevated=False)) as client:
        response = await client.put(_BANNER_SETTING_URL, json={_SETTINGS: {}})

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_resetting_a_setting_returns_no_content():
    with patch(
        "codemie.rest_api.routers.customer_config.reset_setting",
        AsyncMock(return_value=None),
    ) as reset:
        async with _client(_app(elevated=True)) as client:
            response = await client.delete(_BANNER_SETTING_URL)

    assert response.status_code == 204
    assert reset.await_args.args[0] == _BANNER


@pytest.mark.asyncio
async def test_resetting_a_setting_is_denied_without_elevated_permissions():
    async with _client(_app(elevated=False)) as client:
        response = await client.delete(_BANNER_SETTING_URL)

    assert response.status_code == 403
