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

"""HTTP-level authorization matrix for the cost center API (EPMCDME-17380).

Requests go through FastAPI's TestClient, so the real route guards (`admin_access_only`,
`admin_or_maintainer_or_auditor_access`) and the real `CostCenterService` role checks run.
Only identity (`authenticate`) and the data layer (DB sessions, repositories, feature gate,
budget summary) are stubbed.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.application_repository import application_repository
from codemie.repository.cost_center_repository import cost_center_repository
from codemie.rest_api.main import extended_http_exception_handler
from codemie.rest_api.routers import cost_centers as cost_centers_module
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service import cost_center_service as cost_center_service_module
from codemie.service.budget.cost_center_budget_summary import CostCenterBudgetSummary

COST_CENTER_ID = uuid4()
BASE = "/v1/admin/cost-centers"

# Every role flag is set explicitly; a plain user has none of them.
ROLES = {
    "auditor": {"is_admin": False, "is_maintainer": False, "is_auditor": True},
    "maintainer": {"is_admin": False, "is_maintainer": True, "is_auditor": False},
    "admin": {"is_admin": True, "is_maintainer": False, "is_auditor": False},
    "user": {"is_admin": False, "is_maintainer": False, "is_auditor": False},
}

# (name, method, url, json body, success status)
READS = [
    ("list", "GET", BASE, None, 200),
    ("detail", "GET", f"{BASE}/{COST_CENTER_ID}", None, 200),
    ("budget-rollup", "GET", f"{BASE}/{COST_CENTER_ID}/budget-rollup", None, 200),
]
WRITES = [
    ("create", "POST", BASE, {"name": "eng-123", "description": "d"}, 201),
    ("update", "PATCH", f"{BASE}/{COST_CENTER_ID}", {"description": "new"}, 200),
    ("delete", "DELETE", f"{BASE}/{COST_CENTER_ID}", None, 204),
]


def _cost_center():
    return SimpleNamespace(
        id=COST_CENTER_ID,
        name="eng-123",
        description="d",
        created_by="admin-1",
        date=datetime(2026, 3, 19, tzinfo=UTC),
        deleted_at=None,
        update_date=None,
    )


def _make_user(role: str) -> User:
    # ENV=local (pytest.ini) and ENABLE_USER_MANAGEMENT=False would force/derive is_admin,
    # so User is always built under the production-like patch from the `http` fixture.
    return User(id=f"{role}-1", username=role, email=f"{role}@example.com", **ROLES[role])


@pytest.fixture
def http():
    """Return `call(role, method, url, body)` that sends a request as `role` through the real guards."""
    holder: dict[str, User] = {}
    app = FastAPI()
    app.include_router(cost_centers_module.router)
    app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)

    async def _identity(request: Request) -> User:
        request.state.user = holder["user"]
        return holder["user"]

    app.dependency_overrides[authenticate] = _identity

    cost_center = _cost_center()
    session_cm = MagicMock()
    session_cm.__enter__.return_value = MagicMock()
    async_session_cm = MagicMock()

    with (
        patch.object(config, "ENV", "dev"),
        patch.object(config, "ENABLE_USER_MANAGEMENT", True),
        patch.object(cost_center_service_module, "customer_config", MagicMock(is_feature_enabled=lambda _: True)),
        patch.object(cost_centers_module, "get_session", return_value=session_cm),
        patch.object(cost_centers_module, "get_async_session", return_value=async_session_cm),
        patch.object(
            cost_centers_module,
            "get_cost_center_budget_summary",
            new=AsyncMock(return_value=CostCenterBudgetSummary(budgets=[])),
        ),
        patch.object(cost_center_repository, "list_paginated", return_value=([cost_center], 1)),
        patch.object(cost_center_repository, "get_active_by_id", return_value=cost_center),
        patch.object(cost_center_repository, "get_by_name_case_insensitive", return_value=None),
        patch.object(cost_center_repository, "create", return_value=cost_center),
        patch.object(cost_center_repository, "update", return_value=cost_center),
        patch.object(application_repository, "count_active_projects_by_cost_center_id", return_value=0),
        patch.object(application_repository, "list_projects_by_cost_center_id", return_value=[]),
        patch.object(application_repository, "get_project_member_counts_bulk", return_value={}),
        patch.object(application_repository, "aget_project_names_by_cost_center_name", new=AsyncMock(return_value=[])),
    ):
        client = TestClient(app)

        def call(role: str, method: str, url: str, body: dict | None):
            holder["user"] = _make_user(role)
            return client.request(method, url, json=body)

        yield call


def test_plain_user_has_no_elevated_role(http):
    """Guards against the ENV=local / flag-off overrides silently promoting test users."""
    user = _make_user("user")
    assert not (user.is_admin or user.is_maintainer or user.is_auditor)


@pytest.mark.parametrize(
    ("role", "allowed"), [("auditor", True), ("maintainer", True), ("admin", True), ("user", False)]
)
@pytest.mark.parametrize(("name", "method", "url", "body", "ok_status"), READS, ids=[r[0] for r in READS])
def test_read_endpoints_authorization(http, name, method, url, body, ok_status, role, allowed):
    response = http(role, method, url, body)

    if allowed:
        assert response.status_code == ok_status, response.text
    else:
        _assert_access_denied(response)


@pytest.mark.parametrize(
    ("role", "allowed"), [("auditor", False), ("maintainer", True), ("admin", True), ("user", False)]
)
@pytest.mark.parametrize(("name", "method", "url", "body", "ok_status"), WRITES, ids=[w[0] for w in WRITES])
def test_write_endpoints_authorization(http, name, method, url, body, ok_status, role, allowed):
    response = http(role, method, url, body)

    if allowed:
        assert response.status_code == ok_status, response.text
    else:
        _assert_access_denied(response)


def _assert_access_denied(response):
    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["message"] == "Access denied"
    assert set(error) == {"message", "details", "help"}
