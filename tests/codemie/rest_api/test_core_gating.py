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

"""Standalone core gating: require_core guards on routers and endpoints, and OpenAPI filtering."""

import functools

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from codemie.configs import config as app_config
from codemie.configs import llm_provider_availability as avail
from codemie.rest_api import core_gating, main
from codemie.rest_api.dependencies.core import require_core

CORE_PREFIXES = ("/v1/assistants", "/v1/workflows", "/v1/conversations", "/v1/skills", "/v1/ide/inference")
STAY_PATHS = ("/v1/config", "/v1/files/diagram/mermaid", "/v1/ide/config", "/v1/feedback")


@pytest.fixture(autouse=True)
def _reset():
    avail.core_availability.cache_clear()
    yield
    avail.core_availability.cache_clear()


def _set(monkeypatch, standalone: bool, core: bool):
    monkeypatch.setattr(app_config, "STANDALONE", standalone, raising=False)
    calls = []

    def fake(cfg):
        calls.append(cfg)
        return core, "azure_openai", [] if core else ["AZURE_OPENAI_API_KEY"]

    monkeypatch.setattr(avail, "core_availability", fake)
    monkeypatch.setattr(core_gating, "core_availability", fake)
    return calls


def _api_routes():
    return [r for r in main.app.routes if hasattr(r, "dependant")]


def _schema_paths():
    return {r.path for r in core_gating.schema_routes(main.app.routes) if hasattr(r, "dependant")}


def _wrapped_guard():
    @functools.wraps(require_core)
    def guard():
        return require_core()

    return guard


def _nested_guard(_: None = Depends(require_core)) -> None:
    return None


def _probe_app():
    """Minimal app: a router-level gate, endpoint-level gates (direct, wrapped, nested) and an open route."""
    gated_router = APIRouter(prefix="/gated", dependencies=[Depends(require_core)])

    @gated_router.get("/item")
    def gated_item():
        return {"ok": True}

    mixed = APIRouter(prefix="/mixed")

    @mixed.get("/open")
    def open_item():
        return {"ok": True}

    @mixed.get("/direct", dependencies=[Depends(require_core)])
    def direct_item():
        return {"ok": True}

    @mixed.get("/wrapped", dependencies=[Depends(_wrapped_guard())])
    def wrapped_item():
        return {"ok": True}

    @mixed.get("/nested", dependencies=[Depends(_nested_guard)])
    def nested_item():
        return {"ok": True}

    app = FastAPI()
    app.include_router(gated_router)
    app.include_router(mixed)
    return app


GATED_PROBES = ("/gated/item", "/mixed/direct", "/mixed/wrapped", "/mixed/nested")


def test_every_core_route_is_guarded():
    leaked = [
        r.path
        for r in _api_routes()
        if r.path.startswith(CORE_PREFIXES) and not core_gating._requires_core(r.dependant)
    ]
    assert leaked == []
    gated = {r.path for r in _api_routes() if core_gating._requires_core(r.dependant)}
    assert {
        "/v1/ide/inference/{assistant_id}",
        "/v1/ide/inference/virtual",
        "/v1/user/reactions",
        "/v1/conversations/{conversation_id}/feedback",
        "/v1/admin/users/{user_id}/conversations/{conversation_id}",
        "/v1/admin/users/{user_id}/conversations/{conversation_id}/feedback",
    } <= gated


def test_stay_routes_not_guarded():
    by_path = {r.path: r for r in _api_routes()}
    for path in STAY_PATHS:
        assert not core_gating._requires_core(by_path[path].dependant), path


def test_core_off_returns_404_for_router_and_endpoint_gates(monkeypatch):
    _set(monkeypatch, standalone=True, core=False)
    client = TestClient(_probe_app())
    for path in GATED_PROBES:
        assert client.get(path).status_code == 404, path
    assert client.get("/mixed/open").status_code == 200


def test_core_on_allows_all_routes(monkeypatch):
    _set(monkeypatch, standalone=True, core=True)
    client = TestClient(_probe_app())
    for path in (*GATED_PROBES, "/mixed/open"):
        assert client.get(path).status_code == 200, path


def test_standalone_off_allows_all_without_calling_check(monkeypatch):
    calls = _set(monkeypatch, standalone=False, core=False)
    client = TestClient(_probe_app())
    for path in (*GATED_PROBES, "/mixed/open"):
        assert client.get(path).status_code == 200, path
    assert core_gating.schema_routes(main.app.routes) == list(main.app.routes)
    assert calls == []


def test_core_off_hides_guarded_routes_from_schema(monkeypatch):
    _set(monkeypatch, standalone=True, core=False)
    paths = _schema_paths()
    assert not any(p.startswith(CORE_PREFIXES) for p in paths)
    assert "/v1/conversations/{conversation_id}/feedback" not in paths
    assert set(STAY_PATHS) <= paths

    probe = _probe_app()
    probe_paths = {r.path for r in core_gating.schema_routes(probe.routes) if hasattr(r, "dependant")}
    assert probe_paths == {"/mixed/open"}


def test_core_on_keeps_all_routes_in_schema(monkeypatch):
    _set(monkeypatch, standalone=True, core=True)
    assert core_gating.schema_routes(main.app.routes) == list(main.app.routes)


def test_check_exception_means_core_off(monkeypatch):
    monkeypatch.setattr(app_config, "STANDALONE", True, raising=False)

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(avail, "_default_provider", boom)
    monkeypatch.setattr("codemie.enterprise.litellm.is_litellm_enabled", lambda: False)
    client = TestClient(_probe_app())
    assert client.get("/gated/item").status_code == 404
    assert not any(p.startswith(CORE_PREFIXES) for p in _schema_paths())
