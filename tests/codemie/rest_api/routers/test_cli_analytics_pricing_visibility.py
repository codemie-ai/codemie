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

"""Pricing warnings are visible to everyone with access to the endpoint (EPMCDME-17152).

Real FastAPI app, router, `_admin_gate`, the response models, `_metadata` and `_respond`; only the storage handler is
faked, and it hands every request the same data object, as the dashboard cache does. The tests verify model names
and flags, cache integrity, and the existing endpoint access gates.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import codemie.rest_api.routers.cli_analytics as router_module
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE

BASE = "/v1/analytics/cli-analytics"
END = datetime(2026, 9, 23, tzinfo=timezone.utc)
NAME = "mai-code-1.1-flash"


def _user(**flags) -> User:
    # model_construct skips the validator that makes every user an admin under ENV=local.
    base = {"id": "u", "email": "u@example.com", "is_admin": False, "is_maintainer": False, "is_auditor": False}
    return User.model_construct(**{**base, "admin_project_names": [], **flags})


ROLES = {
    "maintainer": _user(is_maintainer=True, is_admin=True),  # the User validator makes a maintainer an admin
    "maintainer+auditor": _user(is_maintainer=True, is_admin=True, is_auditor=True),
    "maintainer+project-admin": _user(is_maintainer=True, is_admin=True, admin_project_names=["demo"]),
    "admin": _user(is_admin=True),
    "admin+auditor": _user(is_admin=True, is_auditor=True),
    "project-admin": _user(admin_project_names=["demo"]),
    "project-admin+auditor": _user(admin_project_names=["demo"], is_auditor=True),
    "auditor": _user(is_auditor=True),  # EPMCDME-17657: an auditor reads all CLI analytics, read-only
}
NO_DASHBOARD = {"ordinary": _user()}
# The sessions routes are open to every viewer, the auditor included.
SESSION_VIEWERS = {**NO_DASHBOARD, "auditor": ROLES["auditor"]}

_PRICED = {"cost_incomplete": False, "unpriced_models": [], "cost_unknown": False}
_PARTIAL = {"cost_incomplete": True, "unpriced_models": [NAME], "cost_unknown": False}
_UNKNOWN = {"cost_incomplete": True, "unpriced_models": [NAME], "cost_unknown": True}
_BUCKET = {"cost_usd": 2.0, "session_count": 1, "cost_incomplete": True, "unpriced_models": [NAME]}
_BUCKETS = {
    "funding": {"api_based": _BUCKET, "subscription": {}, "unknown": {}},
    "personal_billed": _BUCKET,
}


def _user_row(name: str, cost: float, pricing: dict) -> dict:
    return {
        "developer_name": name,
        "user_email": name,
        "session_count": 1,
        "input_tokens": 1,
        "output_tokens": 1,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "cost_usd": cost,
        **pricing,
        **_BUCKETS,
    }


# Priced, partial (the priced part kept), entirely unknown, and a true $0.
_MIXED = [("ann", 5.0, _PRICED), ("bob", 2.0, _PARTIAL), ("cid", 0.0, _UNKNOWN), ("dee", 0.0, _PRICED)]
USERS = {
    "rows": [_user_row(*m) for m in _MIXED],
    "chart": [{"developer_name": n, "cost_usd": c, **p} for n, c, p in _MIXED],
    "total_count": 4,
    **_BUCKETS,
}
_REPO = {
    "repository": "r",
    "project_name": "p",
    "session_count": 1,
    "turns": 1,
    "cost_usd": 2.0,
    "files_changed": 0,
    "lines_added": None,
    "lines_removed": None,
    "net_lines": None,
    "tool_calls": 0,
    "tool_calls_success": 0,
    "tool_success_rate": 0.0,
    **_PARTIAL,
    **_BUCKETS,
}
_BRANCH = {k: v for k, v in _REPO.items() if k not in ("repository", "project_name", *_BUCKETS)}
REPOSITORIES = {
    "rows": [{**_REPO, "branches": [{**_BRANCH, "branch": "main", **_UNKNOWN}, {**_BRANCH, "branch": "x", **_PRICED}]}],
    "pagination": {"page": 0, "per_page": 20, "total_count": 1, "has_more": False},
    **_BUCKETS,
}
PROJECTS = {
    "rows": [{**_REPO, "repository_count": 1, "repositories": REPOSITORIES["rows"]}],
    "pagination": REPOSITORIES["pagination"],
    **_BUCKETS,
}
COST = {
    "kpis": {"total_sessions": 1, "total_cost_usd": 2.0, "total_tokens": 2, "avg_cost_per_session": 2.0},
    "cost_by_user": [{"developer_name": "bob", "cost_usd": 2.0, **_PARTIAL, **_BUCKETS}],
    "cost_by_model": [{"model_name": NAME, "cost_usd": 0.0, **_UNKNOWN, **_BUCKETS}],
    **_BUCKETS,
}
_UNKNOWN_SESSION = {"cost_incomplete": True, "unpriced_models": [NAME]}
_PARTIAL_SESSION = {"cost_incomplete": True, "unpriced_models": [NAME]}
_COMPLETE_SESSION = {"cost_incomplete": False, "unpriced_models": []}
_SESSION = {
    "trace_id": "t1",
    "developer_name": "bob",
    "start_time": "2026-09-22T10:00:00",
    "duration_ms": 1,
    "model_name": NAME,
    "input_tokens": 1,
    "output_tokens": 1,
    "cost_usd": 0.0,
    "cost_available": False,
    **_UNKNOWN_SESSION,
}
# Mixed siblings of one page: fully unpriced, partial (priced part kept) and complete, each with its own pricing.
_SIBLINGS = [
    {**_SESSION, "trace_id": "t2", "cost_usd": 3.0, "cost_available": True, **_PARTIAL_SESSION},
    {**_SESSION, "trace_id": "t3", "cost_usd": 4.0, "cost_available": True, **_COMPLETE_SESSION},
]
SESSIONS = {"sessions": [_SESSION, *_SIBLINGS], "page": 0, "per_page": 20, "total": 3}
SESSION_DETAIL = {**_SESSION, "harness_name": "Claude Code"}


def _names(value) -> list:
    """Every `unpriced_models` list in a response body, at any depth."""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            found += [item] if key == "unpriced_models" else _names(item)
    elif isinstance(value, list):
        for item in value:
            found += _names(item)
    return found


def _flags(value) -> list:
    """Every pricing flag of a response body, in document order."""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("cost_incomplete", "cost_unknown", "cost_available"):
                found.append((key, item))
            else:
                found += _flags(item)
    elif isinstance(value, list):
        for item in value:
            found += _flags(item)
    return found


class _Env:
    """The fake storage: one shared data object per route (as a dashboard cache holds it) and the current viewer."""

    def __init__(self) -> None:
        self.user = ROLES["maintainer"]
        self.data = {
            "users": USERS,
            "cost": COST,
            "repositories": REPOSITORIES,
            "projects": PROJECTS,
            "sessions": SESSIONS,
            "detail": SESSION_DETAIL,
        }
        self.pricer = MagicMock(unpriced=[NAME], catalog_source="yaml")
        self.handler = MagicMock()
        self.handler.get_users = AsyncMock(side_effect=lambda *a, **k: (self.data["users"], self.pricer))
        self.handler.get_cost = AsyncMock(side_effect=lambda *a, **k: (self.data["cost"], self.pricer))
        self.handler.get_repositories = AsyncMock(side_effect=lambda *a, **k: (self.data["repositories"], self.pricer))
        self.handler.get_projects = AsyncMock(side_effect=lambda *a, **k: (self.data["projects"], self.pricer))
        self.handler.get_sessions = AsyncMock(side_effect=lambda *a, **k: (self.data["sessions"], self.pricer, None))
        self.handler.get_session_detail = AsyncMock(
            side_effect=lambda *a, **k: (copy.deepcopy(self.data["detail"]), [NAME], "yaml")
        )


@pytest.fixture
def env():
    env = _Env()
    app = FastAPI()
    app.include_router(router_module.router)

    def authenticate_as(request: Request) -> User:
        request.state.user = env.user
        return env.user

    async def fake_resolve(self, user, *args, **kwargs):
        return router_module.LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=END - timedelta(days=1), end_dt=END)

    async def cached(name, f, fetch, *key):
        return await fetch()  # hands back the very same objects, as a cache hit does

    @app.exception_handler(ExtendedHTTPException)
    async def _extended(request, exc: ExtendedHTTPException):
        return JSONResponse(status_code=exc.code, content={"message": exc.message})

    storage = MagicMock()
    storage.reader.authorize_session = AsyncMock(return_value=True)
    app.dependency_overrides[authenticate] = authenticate_as
    with (
        patch.object(router_module, "_ensure_enabled", return_value=None),
        patch.object(router_module, "_handler", return_value=env.handler),
        patch.object(router_module, "_attach_user_ids", AsyncMock()),
        patch.object(router_module, "cached_dashboard", cached),
        patch.object(router_module, "get_cli_analytics_storage", return_value=storage),
        patch.object(router_module, "config", SimpleNamespace(is_local=False)),  # ETag is set off-local
        patch.object(router_module.FilterParams, "resolve", fake_resolve),
        patch.object(router_module.FilterParams, "resolve_sessions", fake_resolve),
    ):
        env.client = TestClient(app, raise_server_exceptions=False)
        yield env


DASHBOARD = [
    "users?include_chart=true&page=0&per_page=2",
    "user-charts",
    "cost",
    "repositories?include_branches=true",
    "projects",
]
ANY_VIEWER = [*DASHBOARD, "sessions", "sessions/t1"]


def _get(env, role_user, path):
    env.user = role_user
    return env.client.get(f"{BASE}/{path}")


@pytest.mark.parametrize("path", ANY_VIEWER)
@pytest.mark.parametrize("role", ROLES)
def test_every_authorized_viewer_receives_model_names_in_pricing_warnings(env, role, path):
    response = _get(env, ROLES[role], path)

    assert response.status_code == 200
    names = _names(response.json())
    assert [NAME] in names


@pytest.mark.parametrize("path", ANY_VIEWER)
def test_the_flags_are_the_same_for_every_viewer(env, path):
    bodies = {role: _get(env, user, path).json() for role, user in ROLES.items()}

    reference = _flags(bodies["maintainer"])
    assert reference
    assert all(_flags(body) == reference for body in bodies.values())
    assert bodies["admin"]["metadata"]["cost_incomplete"] is True


def test_model_names_and_pricing_flags_stay_on_their_own_rows(env):
    admin = _get(env, ROLES["admin"], "cost").json()["data"]
    assert [r["model_name"] for r in admin["cost_by_model"]] == [NAME]
    assert admin["cost_by_model"][0]["cost_unknown"] is True  # the mark stays on the model's own row
    sessions = _get(env, ROLES["admin"], "sessions").json()["data"]["sessions"]
    assert [s["model_name"] for s in sessions] == [NAME] * 3


def test_row_pricing_for_mixed_rows_and_the_chart(env):
    users = _get(env, ROLES["admin"], "users?include_chart=true&page=0&per_page=2").json()["data"]

    expected = [("ann", False, False), ("bob", True, False), ("cid", True, True), ("dee", False, False)]
    for rows in (users["rows"], users["chart"]):
        assert [(r["developer_name"], r["cost_incomplete"], r["cost_unknown"]) for r in rows] == expected
    assert [r["cost_usd"] for r in users["rows"]] == [5.0, 2.0, 0.0, 0.0]  # priced portion and true $0 preserved
    repo = _get(env, ROLES["admin"], "repositories?include_branches=true").json()["data"]["rows"][0]
    assert [(b["branch"], b["cost_incomplete"], b["cost_unknown"]) for b in repo["branches"]] == [
        ("main", True, True),
        ("x", False, False),
    ]


def test_responses_keep_pricing_names_in_cached_data(env):
    before = copy.deepcopy(env.data)
    paths = [*DASHBOARD, "sessions", "sessions/t1"]

    first = {p: _get(env, ROLES["maintainer"], p) for p in paths}
    admin = {p: _get(env, ROLES["admin"], p) for p in paths}
    again = {p: _get(env, ROLES["maintainer"], p) for p in paths}

    assert _names(env.data) == _names(before) and any(_names(before))
    for p in paths:
        assert _names(again[p].json()) == _names(first[p].json())
        assert _names(admin[p].json()) == _names(first[p].json())
        assert all("ETag" in response[p].headers for response in (first, admin, again))


@pytest.mark.parametrize("path", DASHBOARD)
@pytest.mark.parametrize("role", NO_DASHBOARD)
def test_an_ordinary_viewer_reaches_the_dashboard_routes(env, role, path):
    # EPMCDME-17657: the gate is gone, every role reads the data its scope allows.
    assert _get(env, NO_DASHBOARD[role], path).status_code == 200


@pytest.mark.parametrize("path", ["sessions", "sessions/t1"])
@pytest.mark.parametrize("role", SESSION_VIEWERS)
def test_an_ordinary_viewer_keeps_session_access_and_receives_model_names(env, role, path):
    response = _get(env, SESSION_VIEWERS[role], path)

    assert response.status_code == 200
    assert [NAME] in _names(response.json())
    assert response.json()["metadata"]["cost_incomplete"] is True


def test_missing_pricing_metadata_returns_empty_names(env):
    env.pricer = MagicMock(unpriced=[], catalog_source=None)
    env.data["sessions"] = {"sessions": [], "page": 0, "per_page": 20, "total": 0}

    response = _get(env, ROLES["admin"], "sessions")

    assert response.status_code == 200
    assert response.json()["metadata"]["unpriced_models"] == []
    assert response.json()["metadata"]["cost_incomplete"] is False


def test_session_rows_keep_their_own_pricing_state_for_every_viewer(env):
    for role, user in ROLES.items():
        rows = _get(env, user, "sessions").json()["data"]["sessions"]
        flags = [
            (r["trace_id"], r["cost_incomplete"], r["cost_available"], r["model_name"], r["cost_usd"]) for r in rows
        ]
        assert flags == [
            ("t1", True, False, NAME, 0.0),
            ("t2", True, True, NAME, 3.0),
            ("t3", False, True, NAME, 4.0),
        ], role
        shown = [r["unpriced_models"] for r in rows]
        assert shown == [[NAME], [NAME], []], role
        detail = _get(env, user, "sessions/t1").json()["data"]
        assert (detail["cost_incomplete"], detail["cost_available"]) == (True, False), role
        assert detail["unpriced_models"] == [NAME], role
