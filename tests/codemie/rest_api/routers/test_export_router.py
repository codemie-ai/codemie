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
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers import export as export_router
from codemie.rest_api.security.authentication import User, authenticate
from codemie.service.iac_export.document import ExportDocument, ExportError, to_canonical_bytes


class _Meta(BaseModel):
    kind: str = "fake"
    name: str = "n"


class _Spec(BaseModel):
    value: int = 1


class _FakeExporter:
    def export(self, id: str, user) -> ExportDocument:
        if id == "missing":
            raise ExportError("not found", status_code=404)
        if id == "invalid":
            _Spec(value="secret-value-xyz")  # pydantic ValidationError
        if id == "yaml":
            import yaml

            yaml.safe_load("a: [unclosed")
        if id == "attr":
            raise AttributeError("'NoneType' object has no attribute 'value' secret-value-xyz")
        return ExportDocument(meta=_Meta(), spec=_Spec())


def _build_app(authenticated: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(export_router.router)

    @app.exception_handler(ExtendedHTTPException)
    async def _handler(request, exc: ExtendedHTTPException):
        return JSONResponse(status_code=exc.code, content={"error": {"message": exc.message}})

    if authenticated:
        app.dependency_overrides[authenticate] = lambda: User(id="u1", username="tester")
    return app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _fake_registry(monkeypatch):
    monkeypatch.setattr(export_router, "EXPORTERS", {"fake": _FakeExporter()})


async def _get(app: FastAPI, url: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        return await ac.get(url)


@pytest.mark.anyio
async def test_success_returns_canonical_bytes():
    response = await _get(_build_app(), "/v1/export/fake/abc")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.content == to_canonical_bytes(ExportDocument(meta=_Meta(), spec=_Spec()))


@pytest.mark.anyio
async def test_unknown_kind_names_the_kind():
    response = await _get(_build_app(), "/v1/export/bogus/abc")

    assert response.status_code == 404
    assert "bogus" in response.text


@pytest.mark.anyio
async def test_export_error_maps_status_and_returns_no_document():
    response = await _get(_build_app(), "/v1/export/fake/missing")

    assert response.status_code == 404
    assert "not found" in response.text
    assert "meta" not in response.text
    assert "spec" not in response.text


# CR-001
@pytest.mark.anyio
@pytest.mark.parametrize("entity_id", ["invalid", "yaml"])
async def test_spec_build_errors_map_to_422_without_echoing_values(entity_id):
    response = await _get(_build_app(), f"/v1/export/fake/{entity_id}")

    assert response.status_code == 422
    assert "fake" in response.text and entity_id in response.text
    assert "secret-value-xyz" not in response.text


@pytest.mark.anyio
async def test_unexpected_errors_are_not_reported_as_stored_data_problems():
    with pytest.raises(AttributeError):
        await _get(_build_app(), "/v1/export/fake/attr")


@pytest.mark.anyio
async def test_unauthenticated_is_rejected():
    response = await _get(_build_app(authenticated=False), "/v1/export/fake/abc")

    assert response.status_code in (401, 403)


def test_register_exporter_adds_kind(monkeypatch):
    monkeypatch.setattr(export_router, "EXPORTERS", {})
    exporter = _FakeExporter()

    export_router.register_exporter("fake", exporter)

    assert export_router.EXPORTERS["fake"] is exporter


def test_production_registry_has_every_real_kind():
    import importlib

    module = importlib.reload(export_router)
    assert set(module.EXPORTERS) == {"assistant", "category", "datasource", "integration", "skill", "workflow"}


def test_router_is_mounted_on_the_application():
    from codemie.rest_api.main import app

    assert any(getattr(r, "path", "") == "/v1/export/{kind}/{id}" for r in app.routes)


@pytest.mark.anyio
async def test_env_ref_collision_in_document_is_a_422(monkeypatch):
    from codemie.service.iac_export.credential_ref import env_ref

    class _Colliding:
        def export(self, id, user):
            env_ref("a_b", "c")
            env_ref("a", "b_c")
            return ExportDocument(meta=_Meta(), spec=_Spec())

    monkeypatch.setattr(export_router, "EXPORTERS", {"fake": _Colliding()})
    response = await _get(_build_app(), "/v1/export/fake/x")
    assert response.status_code == 422
