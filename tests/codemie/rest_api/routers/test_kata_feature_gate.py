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

"""Every kata route is refused with 404 while features:katas is disabled."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from codemie.rest_api.main import app
from codemie.rest_api.routers import ai_kata, user_kata_progress
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

GATE = "codemie.configs.customer_config.CustomerConfig.is_feature_enabled"
MESSAGE = "Katas feature is not available"


def _kata_routes() -> list[tuple[str, str]]:
    return [
        (method, route.path)
        for router in (ai_kata.router, user_kata_progress.router)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    ]


@pytest.fixture
def client():
    app.dependency_overrides[authenticate] = lambda: User(id="u1", username="u", name="u")
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.pop(authenticate, None)


def _call(client, method: str, path: str):
    url = _fill(path)
    return client.request(method, url, json={})


def _fill(path: str) -> str:
    import re

    return re.sub(r"\{[^}]+\}", "x", path)


def test_routes_discovered():
    assert len(_kata_routes()) > 10


@pytest.mark.parametrize("method,path", _kata_routes())
def test_disabled_returns_404_and_skips_services(client, method, path):
    with (
        patch(GATE, return_value=False),
        patch.object(ai_kata, "kata_service", MagicMock()) as kata_svc,
        patch.object(user_kata_progress, "progress_service", MagicMock()) as progress_svc,
        patch.object(ai_kata, "kata_user_interaction_service", MagicMock()) as interaction_svc,
    ):
        response = _call(client, method, path)

    assert response.status_code == 404
    assert MESSAGE in response.text
    assert not kata_svc.method_calls
    assert not progress_svc.method_calls
    assert not interaction_svc.method_calls


@pytest.mark.parametrize("method,path", _kata_routes())
def test_enabled_does_not_return_gate_404(client, method, path):
    with patch(GATE, return_value=True):
        response = _call(client, method, path)

    assert MESSAGE not in response.text


def test_unauthenticated_stays_401_when_disabled():
    app.dependency_overrides.pop(authenticate, None)
    with patch(GATE, return_value=False):
        response = TestClient(app, raise_server_exceptions=False).get("/v1/katas/tags")

    assert response.status_code == 401


def test_preconfigured_katas_still_imported_with_default_config():
    from codemie.rest_api import main

    with (
        patch.object(main, "manage_preconfigured_assistants"),
        patch.object(main, "manage_preconfigured_skills"),
        patch.object(main, "create_preconfigured_workflows"),
        patch.object(main, "import_preconfigured_katas") as import_katas,
    ):
        main._initialize_preconfigured_content()

    import_katas.assert_called_once()
