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

"""A browser revisiting CLI analytics sees newly ingested costs, over the real HTTP stack (EPMCDME-17191).

Real FastAPI app, router, auth dependencies and `_respond`; only the storage handler is faked, with a cost that
the test changes between requests (the "$0, then a priced session is ingested" flow of the ticket). A browser
honours `Cache-Control` and `ETag`, so what these tests pin is what it is told: revalidate on every use, and a
conditional request after a data change never gets a 304.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict

import codemie.rest_api.routers.cli_analytics as router_module
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE

BASE = "/v1/analytics/cli-analytics"
_REAL_METADATA = router_module._metadata  # the `client` fixture replaces it with a frozen one
END = datetime(2026, 9, 23, tzinfo=timezone.utc)
# The five tabs of the page: each is a separate GET the browser caches under its own URL.
TABS = ["overview", "cost", "users", "repositories", "sessions"]


class _Anything(BaseModel):
    """Stands in for each route's response model, so a minimal payload is enough; headers and body are under test."""

    model_config = ConfigDict(extra="allow")


class Backend:
    """What the storage currently holds: the one number every tab shows."""

    def __init__(self) -> None:
        self.cost = 0.0

    def handler(self) -> MagicMock:
        pricer = MagicMock(unpriced=[], catalog_source=None)

        def data() -> dict:
            return {"total_cost": self.cost}

        handler = MagicMock()
        handler.get_overview = AsyncMock(side_effect=lambda *a, **k: (data(), pricer, None))
        handler.get_cost = AsyncMock(side_effect=lambda *a, **k: (data(), pricer))
        handler.get_users = AsyncMock(
            side_effect=lambda *a, **k: (
                {"rows": [], "avg_session_duration_ms": None, "total_count": 0, **data()},
                pricer,
            )
        )
        handler.get_repositories = AsyncMock(side_effect=lambda *a, **k: (data(), pricer))
        handler.get_sessions = AsyncMock(side_effect=lambda *a, **k: (data(), pricer, None))
        return handler


@pytest.fixture
def backend():
    return Backend()


@pytest.fixture
def client(backend):
    real_respond = router_module._respond
    user = User(id="u1", email="admin@example.com", admin_project_names=["demo"])

    def authenticate_as(request: Request) -> User:
        request.state.user = user
        return user

    async def fake_resolve(self, user, *args, **kwargs):
        return router_module.LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=END - timedelta(days=1), end_dt=END)

    async def run_through_cache(name, f, fetch, *key):
        return await fetch()

    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[authenticate] = authenticate_as
    with (
        patch.object(router_module, "_ensure_enabled", return_value=None),
        patch.object(router_module, "_handler", side_effect=lambda: backend.handler()),
        patch.object(router_module, "_attach_user_ids", AsyncMock()),
        patch.object(router_module, "cached_dashboard", run_through_cache),
        patch.object(router_module, "viewer_access", return_value=(True, frozenset())),
        patch.object(router_module.FilterParams, "resolve", fake_resolve),
        patch.object(router_module.FilterParams, "resolve_sessions", fake_resolve),
        # The metadata carries a timestamp and a run time that change on every call: freeze them so that an ETag
        # can only differ when the data does.
        patch.object(router_module, "_metadata", lambda *a, **k: {"timestamp": "t0", "execution_time_ms": 1.0}),
        patch.object(router_module, "_respond", lambda payload, _model: real_respond(payload, _Anything)),
        patch.object(router_module, "config", SimpleNamespace(is_local=False)),
    ):
        yield TestClient(app)


def _cost_of(response) -> float:
    return response.json()["data"]["total_cost"]


def _assert_revalidated(response) -> None:
    cache_control = response.headers["Cache-Control"]
    assert "no-cache" in cache_control
    assert not re.search(r"max-age=([1-9]\d*)", cache_control)


@pytest.mark.parametrize("tab", TABS)
def test_every_tab_tells_the_browser_to_revalidate(client, tab):
    response = client.get(f"{BASE}/{tab}")

    assert response.status_code == 200
    _assert_revalidated(response)
    assert re.fullmatch(r"[0-9a-f]{64}", response.headers["ETag"])


@pytest.mark.parametrize("tab", TABS)
def test_a_zero_answer_is_followed_by_the_new_cost_on_revisit_and_reload(client, backend, tab):
    """The ticket's flow: the tab is opened at $0, a priced session is ingested, the user revisits and reloads."""
    first = client.get(f"{BASE}/{tab}")
    assert _cost_of(first) == 0.0

    backend.cost = 0.0972433
    revisit = client.get(f"{BASE}/{tab}")
    reload = client.get(f"{BASE}/{tab}", headers={"Cache-Control": "max-age=0"})

    assert _cost_of(revisit) == 0.0972433
    assert _cost_of(reload) == 0.0972433
    _assert_revalidated(revisit)
    assert revisit.headers["ETag"] != first.headers["ETag"]


def test_switching_between_all_tabs_shows_the_same_new_cost_on_each(client, backend):
    for tab in TABS:
        assert _cost_of(client.get(f"{BASE}/{tab}")) == 0.0

    backend.cost = 1.25
    seen = {tab: _cost_of(client.get(f"{BASE}/{tab}")) for tab in TABS}

    assert seen == dict.fromkeys(TABS, 1.25)


def test_with_the_metadata_frozen_unchanged_data_gives_the_same_etag(client, backend):
    """The `client` fixture freezes the metadata (timestamp, run time) on purpose, so that an ETag can only differ
    when the data does. In production the metadata changes on every request: see the next test."""
    backend.cost = 3.5
    first = client.get(f"{BASE}/overview")
    second = client.get(f"{BASE}/overview")

    assert first.json() == second.json()
    assert first.headers["ETag"] == second.headers["ETag"]
    _assert_revalidated(second)


def test_in_production_the_etag_differs_on_every_request_even_for_unchanged_data(client, backend):
    """Real `_metadata` (only its clock is driven): the timestamp is part of the body, so the ETag hashed from the
    body never repeats. Nothing reads `If-None-Match`, which is why every request is answered in full. If a later
    change adds 304 support, this test marks the point where the ETag has to stop covering the metadata."""
    backend.cost = 3.5
    ticks = iter(["2026-09-23T10:00:00Z", "2026-09-23T10:00:01Z"])
    with (
        patch.object(router_module, "_metadata", _REAL_METADATA),
        patch.object(router_module, "now_iso", lambda: next(ticks)),
    ):
        first = client.get(f"{BASE}/overview")
        second = client.get(f"{BASE}/overview")

    assert first.json()["data"] == second.json()["data"]
    assert first.json()["metadata"]["timestamp"] != second.json()["metadata"]["timestamp"]
    assert first.headers["ETag"] != second.headers["ETag"]
    _assert_revalidated(second)


def test_classification_policy_sets_no_cache_lifetime_in_its_real_response(client):
    """The route bypasses `_respond` and returns a plain dict: FastAPI adds no `Cache-Control`, so no `max-age`."""
    with patch.object(router_module, "corporate_organizations", return_value=[]):
        response = client.get(f"{BASE}/classification-policy")

    assert response.status_code == 200
    assert "max-age" not in response.headers.get("Cache-Control", "")
    assert "ETag" not in response.headers


def test_a_conditional_request_with_the_old_etag_after_a_data_change_gets_the_new_body_never_a_304(client, backend):
    old = client.get(f"{BASE}/overview")
    backend.cost = 0.309641

    conditional = client.get(f"{BASE}/overview", headers={"If-None-Match": old.headers["ETag"]})

    assert conditional.status_code == 200
    assert _cost_of(conditional) == 0.309641
    assert conditional.headers["ETag"] != old.headers["ETag"]


def test_a_conditional_request_on_unchanged_data_is_answered_in_full_not_with_a_304(client, backend):
    """No `If-None-Match` handling exists, so a 304 cannot be wrong: the answer is always the current body."""
    backend.cost = 2.0
    first = client.get(f"{BASE}/overview")

    conditional = client.get(f"{BASE}/overview", headers={"If-None-Match": first.headers["ETag"]})

    assert conditional.status_code == 200
    assert conditional.json() == first.json()
