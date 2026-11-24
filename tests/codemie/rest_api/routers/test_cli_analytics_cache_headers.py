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

"""CLI analytics reads are revalidated, never served from the browser cache for minutes (EPMCDME-17191)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, ConfigDict

import codemie.rest_api.routers.cli_analytics as router_module
from codemie.rest_api.routers.cli_analytics import LocalAnalyticsFrameworksResponse
from codemie.rest_api.security.user import User
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE

_FRAMEWORKS = {"data": ["a"]}


def _respond_with(is_local: bool, payload: dict):
    with patch.object(router_module, "config", SimpleNamespace(is_local=is_local)):
        return router_module._respond(payload, LocalAnalyticsFrameworksResponse)


def test_non_local_answer_is_revalidated_on_every_use():
    response = _respond_with(False, _FRAMEWORKS)

    assert response.headers["Cache-Control"] == "private, no-cache"
    assert "max-age" not in response.headers["Cache-Control"]
    assert re.fullmatch(r"[0-9a-f]{64}", response.headers["ETag"])


def test_local_answer_is_not_stored_and_has_no_etag():
    response = _respond_with(True, _FRAMEWORKS)

    assert response.headers["Cache-Control"] == "no-store"
    assert "ETag" not in response.headers


def test_changed_data_gives_a_fresh_200_with_a_new_etag_never_a_304():
    first = _respond_with(False, _FRAMEWORKS)
    # `_respond` takes no request, so a conditional 304 is impossible: the old ETag cannot match changed data.
    second = _respond_with(False, {"data": ["b"]})

    assert second.status_code == 200
    assert json.loads(second.body) == {"data": ["b"]}
    assert second.headers["ETag"] != first.headers["ETag"]


# ── Route level: every `_respond`-based GET answers with the same headers ───────────────────────────────────────────

END = datetime(2026, 9, 23, tzinfo=timezone.utc)
_DAYS = timedelta(days=1)


class _Anything(BaseModel):
    """Stands in for each route's response model so a minimal payload is enough; the headers are what is under test."""

    model_config = ConfigDict(extra="allow")


def _params() -> router_module.FilterParams:
    return router_module.FilterParams(
        time_period="last_7_days",
        start_date=None,
        end_date=None,
        users=None,
        projects=None,
        repositories=None,
        subscription_type=None,
        project_origin=None,
    )


def _handler() -> MagicMock:
    pricer = MagicMock(unpriced=[], catalog_source=None)
    users = {"rows": [], "avg_session_duration_ms": None, "total_count": 0}
    handler = MagicMock()
    handler.get_overview = AsyncMock(return_value=({}, pricer, None))
    handler.get_cost = AsyncMock(return_value=({}, pricer))
    handler.get_users = AsyncMock(return_value=(users, pricer))
    handler.get_repositories = AsyncMock(return_value=({}, pricer))
    handler.get_projects = AsyncMock(return_value=({}, pricer))
    handler.get_branches = AsyncMock(return_value=({}, pricer))
    handler.get_tools = AsyncMock(return_value={})
    handler.get_activity = AsyncMock(return_value={})
    handler.authorize_session = AsyncMock(return_value=True)
    handler.get_efficiency = AsyncMock(return_value=({}, pricer))
    handler.get_sessions = AsyncMock(return_value=({}, pricer, None))
    handler.get_session_detail = AsyncMock(return_value=({"start_time": "2026-09-22"}, [], None))
    return handler


async def _run_through_cache(name, f, fetch, *key):
    return await fetch()


_ROUTES = [
    ("get_overview", {}),
    ("get_cost", {"include_class_lists": True, "include_buckets": True}),
    (
        "get_users",
        {
            "page": 0,
            "per_page": 50,
            "include_chart": False,
            "include_daily_activity": False,
            "include_avg_duration": False,
        },
    ),
    ("get_user_charts", {}),
    (
        "get_repositories",
        {"page": None, "per_page": None, "include_branches": False, "search": None, "unattributed_last": False},
    ),
    ("get_projects", {"page": 0, "per_page": 50, "search": None, "include_repositories": True}),
    ("get_branches", {"repository": "r", "page": 0, "per_page": 10, "is_project_unattributed": False}),
    ("get_tools", {}),
    ("get_activity", {"tz": None}),
    ("get_efficiency", {}),
    ("get_frameworks", {}),
    (
        "get_sessions",
        {
            "page": None,
            "per_page": None,
            "sort_by": "start_time",
            "search": None,
            "framework": None,
            "is_unattributed": False,
            "branch": None,
            "is_branch_unattributed": False,
            "is_project_unattributed": False,
            "personal_only": False,
        },
    ),
    ("get_session_detail", {"trace_id": "t1"}),
]


async def _call_route(route: str, is_local: bool, kwargs: dict):
    real_respond = router_module._respond

    async def fake_resolve(self, user, *args, **kwargs):
        return router_module.LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=END - _DAYS, end_dt=END)

    storage = MagicMock()
    storage.reader.authorize_session = AsyncMock(return_value=True)
    route_kwargs = dict(kwargs)
    if route not in ("get_frameworks", "get_session_detail"):
        route_kwargs["filters"] = _params()
    with (
        patch.object(router_module, "_ensure_enabled", return_value=None),
        patch.object(router_module, "_handler", return_value=_handler()),
        patch.object(router_module, "_attach_user_ids", AsyncMock()),
        patch.object(router_module, "cached_dashboard", _run_through_cache),
        patch.object(router_module, "viewer_access", return_value=(True, frozenset())),
        patch.object(router_module, "get_cli_analytics_storage", return_value=storage),
        patch.object(router_module.FilterParams, "resolve", fake_resolve),
        patch.object(router_module.FilterParams, "resolve_sessions", fake_resolve),
        patch.object(router_module, "_respond", lambda payload, _model, exclude=None: real_respond(payload, _Anything)),
        patch.object(router_module, "config", SimpleNamespace(is_local=is_local)),
    ):
        return await getattr(router_module, route)(user=User(id="u1", email="a@example.com"), **route_kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(("route", "kwargs"), _ROUTES)
async def test_no_get_route_lets_the_browser_reuse_an_answer_without_asking(route, kwargs):
    response = await _call_route(route, False, kwargs)

    cache_control = response.headers["Cache-Control"]
    assert "no-cache" in cache_control
    assert not re.search(r"max-age=([1-9]\d*)", cache_control)


@pytest.mark.asyncio
@pytest.mark.parametrize(("route", "kwargs"), _ROUTES)
async def test_local_mode_keeps_every_get_route_unstored(route, kwargs):
    response = await _call_route(route, True, kwargs)

    assert response.headers["Cache-Control"] == "no-store"
    assert "ETag" not in response.headers


# The one GET that does not go through `_respond`: it returns a plain dict and sets no cache header of its own
# (its real response headers are asserted over HTTP in test_cli_analytics_cache_http.py).
_BYPASSES_RESPOND = {"classification_policy"}


def test_every_get_route_of_the_router_is_either_covered_above_or_a_known_bypass():
    """A new GET route fails here until it is added to `_ROUTES` or consciously listed as a bypass of `_respond`."""
    served = {
        route.endpoint.__name__ for route in router_module.router.routes if "GET" in getattr(route, "methods", set())
    }

    assert served == {name for name, _ in _ROUTES} | _BYPASSES_RESPOND
