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

"""Kill switch for the marketplace-management feature (EXECUTION-PLAN 0.2, F-16).

`MarketplaceManagementConfig.enabled` existed but was never read, so the
feature could not be turned off in a running deployment. These tests pin the
switch to observable behaviour: with the flag off the router is not mounted at
all (404 + absent from the OpenAPI schema); with it on the router is mounted.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

OVERVIEW_PATH = "/v1/marketplace-management/entities/assistants/overview"
# FastAPI advertises the route template, not the concrete path.
OVERVIEW_ROUTE_TEMPLATE = "/v1/marketplace-management/entities/{kind}/overview"


def _build_app(monkeypatch, enabled: bool) -> FastAPI:
    """Build a bare app through the production router-registration path."""
    from codemie.configs import config as app_config
    from codemie.rest_api import main

    monkeypatch.setattr(app_config, "MARKETPLACE_MANAGEMENT_ENABLED", enabled, raising=False)

    app = FastAPI()
    main.register_marketplace_management_router(app)
    return app


def test_router_absent_when_disabled(monkeypatch):
    """Flag off -> endpoint 404s and no marketplace path is advertised."""
    app = _build_app(monkeypatch, enabled=False)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(OVERVIEW_PATH)

    assert response.status_code == 404
    assert [p for p in app.openapi()["paths"] if "marketplace-management" in p] == []


def test_router_present_when_enabled(monkeypatch):
    """Mirror test: flag on -> the router really is mounted.

    Without this the disabled-case assertion would also pass against an
    endpoint that is simply broken and 404s unconditionally.
    """
    app = _build_app(monkeypatch, enabled=True)

    marketplace_paths = [p for p in app.openapi()["paths"] if "marketplace-management" in p]
    assert marketplace_paths, "marketplace-management routes should be mounted when the flag is on"
    assert OVERVIEW_ROUTE_TEMPLATE in app.openapi()["paths"]

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(OVERVIEW_PATH)

    # Unauthenticated, so the exact status depends on the auth stack — the
    # point is only that the route exists and is being served.
    assert response.status_code != 404
