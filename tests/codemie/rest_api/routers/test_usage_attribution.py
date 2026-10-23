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

"""Usage Attribution router: gates, parameters, error mapping, and the settings kept out of /v1/config."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException, status
from httpx import ASGITransport, AsyncClient

from codemie.configs.customer_config import Component, ComponentSetting
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import extended_http_exception_handler
from codemie.rest_api.routers.analytics import router
from codemie.rest_api.security.authentication import (
    admin_or_maintainer_access_only,
    admin_or_maintainer_or_auditor_access,
    authenticate,
)
from codemie.service.usage_attribution.analysis import AnalysisUnavailableError, RefreshTooSoonError
from codemie.service.usage_attribution.handler import FeatureUnavailableError
from codemie.service.usage_attribution.limits import CustomLimitError
from codemie.service.usage_attribution.settings import encode_overrides

MODULE = "codemie.rest_api.routers.analytics.usage_attribution"
ADMIN = SimpleNamespace(id="admin", is_admin=True, is_maintainer=False, is_auditor=False, name="Admin")
AUDITOR = SimpleNamespace(id="auditor", is_admin=False, is_maintainer=False, is_auditor=True, name="Auditor")
BASE = "/v1/analytics/usage-attribution"
ENDPOINTS = [
    ("get", "/totals"),
    ("get", "/settings"),
    ("get", "/cli-summary"),
    ("get", "/shared-projects"),
    ("get", "/shared-projects/proj"),
    ("get", "/repository-groups"),
    ("get", "/repository-groups/g-0123456789"),
    ("get", "/breakdown"),
    ("get", "/people"),
    ("get", "/people/u1"),
    ("get", "/people-summary"),
    ("get", "/people-groups"),
    ("get", "/people-groups/common/project-0123456789"),
    ("get", "/status"),
    ("post", "/refresh"),
]
ANALYSIS = {
    "computed_at": "2026-10-13T15:00:00Z",
    "cache": "hit",
    "settings": {"hash": "h", "defaults_hash": "d", "schema_version": 1, "overridden": [], "tier": None},
}
TABLE = {"rows": [], "totals": {}, "pagination": {"page": 0, "per_page": 10, "total_count": 0, "has_more": False},
         "counts": {}, "threshold": 1000.0, "analysis": ANALYSIS}  # fmt: skip


def make_app(user=ADMIN, allowed=True) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
    app.dependency_overrides[authenticate] = lambda: user

    def denied():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="not authorized")

    app.dependency_overrides[admin_or_maintainer_or_auditor_access] = (lambda: None) if allowed else denied

    def admin_or_maintainer():
        if not allowed or not (user.is_admin or user.is_maintainer):
            denied()

    app.dependency_overrides[admin_or_maintainer_access_only] = admin_or_maintainer
    return app


def fake_handler() -> MagicMock:
    handler = MagicMock()
    handler.totals = AsyncMock(return_value={"total": 1})
    handler.settings = MagicMock(return_value={"can_tune": True})
    for name in (
        "cli_summary",
        "breakdown",
        "status",
        "shared_project",
        "repository_group",
        "person",
        "people_summary",
        "people_group",
    ):
        setattr(handler, name, AsyncMock(return_value={"x": 1, "analysis": ANALYSIS}))
    for name in ("shared_projects", "repository_groups", "people", "people_groups"):
        setattr(handler, name, AsyncMock(return_value=dict(TABLE)))
    handler.refresh = AsyncMock(return_value={"status": "accepted"})
    return handler


async def call(app: FastAPI, method: str, path: str, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        return await getattr(client, method)(BASE + path, **kwargs)


@pytest.fixture
def enabled():
    with (
        patch(f"{MODULE}.customer_config") as cfg,
        patch("codemie.service.usage_attribution.handler.component_settings", return_value={}),
    ):
        cfg.is_feature_enabled.return_value = True
        yield cfg


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
@pytest.mark.parametrize("flag", [True, False])
async def test_unauthorized_callers_get_403_whatever_the_flag(method, path, flag):
    with patch(f"{MODULE}.customer_config") as cfg:
        cfg.is_feature_enabled.return_value = flag
        response = await call(make_app(allowed=False), method, path)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_the_previous_period_is_not_served(enabled):
    # It compared spend on today's repositories with today's categories, which overstates growth: removed.
    with patch(f"{MODULE}.UsageAttributionHandler", return_value=fake_handler()):
        response = await call(make_app(), "get", "/previous-period")
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
async def test_flag_off_gives_404(method, path):
    with patch(f"{MODULE}.customer_config") as cfg:
        cfg.is_feature_enabled.return_value = False
        response = await call(make_app(), method, path)
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), ENDPOINTS)
async def test_enabled_endpoints_answer(enabled, method, path):
    with patch(f"{MODULE}.UsageAttributionHandler", return_value=fake_handler()):
        response = await call(make_app(), method, path)
    assert response.status_code in (200, 202), response.text
    assert "Cache-Control" in response.headers


@pytest.mark.asyncio
async def test_users_and_projects_parameters_are_ignored(enabled):
    handler = fake_handler()
    with patch(f"{MODULE}.UsageAttributionHandler", return_value=handler):
        response = await call(make_app(), "get", "/cli-summary?users=a@x.com&projects=p1&time_period=last_7_days")
    assert response.status_code == 200
    rng = handler.cli_summary.await_args.args[0]
    assert rng.spec == "last_7_days"
    assert response.json()["range"]["days"] > 7


@pytest.mark.asyncio
async def test_range_is_clamped_to_90_days(enabled):
    with patch(f"{MODULE}.UsageAttributionHandler", return_value=fake_handler()):
        response = await call(make_app(), "get", "/totals?time_period=last_year")
    assert response.json()["range"]["clamped"] is True
    assert response.json()["range"]["days"] == 90


@pytest.mark.asyncio
async def test_invalid_period(enabled):
    with patch(f"{MODULE}.UsageAttributionHandler", return_value=fake_handler()):
        # An unknown period never reaches the handler (nor the logs).
        unknown = await call(make_app(), "get", "/totals?time_period=forever%22%0Aforged")
        reversed_range = await call(
            make_app(), "get", "/totals?start_date=2026-10-10T00:00:00Z&end_date=2026-10-01T00:00:00Z"
        )
        too_short = await call(
            make_app(), "get", "/totals?start_date=2026-10-10T10:01:00Z&end_date=2026-10-10T10:14:00Z"
        )
    assert unknown.status_code == 422
    assert reversed_range.status_code == 400
    assert reversed_range.json()["error"]["details"] == "start_date must be before end_date"
    assert too_short.status_code == 400
    assert "15 minutes" in too_short.json()["error"]["details"]


@pytest.mark.asyncio
async def test_auditors_cannot_recompute(enabled):
    with patch(f"{MODULE}.UsageAttributionHandler", return_value=fake_handler()):
        response = await call(make_app(user=AUDITOR), "post", "/refresh")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_unexpected_errors_do_not_leak_details(enabled):
    handler = fake_handler()
    handler.totals = AsyncMock(side_effect=RuntimeError("connection to db-host:5432 failed: password for app_user"))
    with patch(f"{MODULE}.UsageAttributionHandler", return_value=handler):
        response = await call(make_app(), "get", "/totals")
    assert response.status_code == 500
    assert "db-host" not in response.text and "app_user" not in response.text


@pytest.mark.asyncio
async def test_breakdown_without_user_enrichment_gives_404(enabled):
    handler = fake_handler()
    handler.breakdown = AsyncMock(side_effect=FeatureUnavailableError("needs user enrichment"))
    with patch(f"{MODULE}.UsageAttributionHandler", return_value=handler):
        response = await call(make_app(), "get", "/breakdown")
    assert response.status_code == 404
    assert response.json()["error"] == {
        "message": "Not available",
        "details": "needs user enrichment",
        "help": "Contact your administrator if the problem persists.",
    }  # nothing to wait for: no Retry-After, and no retry_after in the body
    assert "Retry-After" not in response.headers


@pytest.mark.asyncio
async def test_stale_responses_are_not_cached_by_the_browser(enabled):
    handler = fake_handler()
    handler.cli_summary = AsyncMock(return_value={"x": 1, "analysis": {**ANALYSIS, "stale": True}})
    with (
        patch(f"{MODULE}.UsageAttributionHandler", return_value=handler),
        patch(f"{MODULE}.config") as cfg,
    ):
        cfg.is_local = False
        stale = await call(make_app(), "get", "/cli-summary")
        handler.cli_summary = AsyncMock(return_value={"x": 1, "analysis": ANALYSIS})
        fresh = await call(make_app(), "get", "/cli-summary")
    assert stale.headers["Cache-Control"] == "no-store"
    assert fresh.headers["Cache-Control"].startswith("private")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        (encode_overrides({"minimumConfidence": 5}), "minimumConfidence"),
        (encode_overrides({"weights": {}}), "weights"),
        ("not-json", "base64url"),
    ],
)
async def test_invalid_overrides_give_422_naming_the_setting(enabled, overrides, detail):
    with patch(f"{MODULE}.UsageAttributionHandler", return_value=fake_handler()):
        response = await call(make_app(), "get", f"/cli-summary?overrides={overrides}")
    assert response.status_code == 422
    assert detail in response.json()["error"]["details"]


@pytest.mark.asyncio
async def test_auditors_cannot_send_overrides(enabled):
    with patch(f"{MODULE}.UsageAttributionHandler", return_value=fake_handler()):
        forbidden = await call(make_app(AUDITOR), "get", f"/cli-summary?overrides={encode_overrides({'namesCap': 3})}")
        allowed = await call(make_app(AUDITOR), "get", "/cli-summary")
    assert forbidden.status_code == 403
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_custom_computation_limit_gives_429_with_retry_after(enabled):
    handler = fake_handler()
    handler.cli_summary = AsyncMock(side_effect=CustomLimitError("too many", retry_after=1234))
    with patch(f"{MODULE}.UsageAttributionHandler", return_value=handler):
        response = await call(make_app(), "get", f"/cli-summary?overrides={encode_overrides({'namesCap': 3})}")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "1234"
    # The body carries the same wait: a page on another origin can't read Retry-After.
    assert response.json()["error"] == {
        "message": "Too many analyses",
        "details": "too many",
        "help": None,
        "retry_after": 1234,
    }


@pytest.mark.asyncio
async def test_refresh_too_soon_gives_429(enabled):
    handler = fake_handler()
    handler.refresh = AsyncMock(side_effect=RefreshTooSoonError(40))
    with patch(f"{MODULE}.UsageAttributionHandler", return_value=handler):
        response = await call(make_app(), "post", "/refresh")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "40"
    assert response.json()["error"]["retry_after"] == 40


@pytest.mark.asyncio
async def test_unavailable_analysis(enabled):
    handler = fake_handler()
    handler.people = AsyncMock(side_effect=AnalysisUnavailableError("busy", 503, 10))
    with patch(f"{MODULE}.UsageAttributionHandler", return_value=handler):
        response = await call(make_app(), "get", "/people")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "10"
    assert response.json()["error"]["retry_after"] == 10


@pytest.mark.asyncio
async def test_unknown_group_id_format_is_rejected(enabled):
    with patch(f"{MODULE}.UsageAttributionHandler", return_value=fake_handler()):
        response = await call(make_app(), "get", "/repository-groups/../../etc")
    assert response.status_code in (404, 422)


def test_public_config_hides_the_analysis_settings():
    from codemie.service.customer_config_service import _public_view

    component = Component(
        id="features:usageAttribution",
        settings=ComponentSetting(
            enabled=True, name="Usage Attribution", weights={"x": 1}, tuning={"roles": ["admin"]}
        ),
    )
    public = _public_view(component).settings.model_dump()
    assert public["enabled"] is True and public["name"] == "Usage Attribution"
    assert "weights" not in public and "tuning" not in public
    other = Component(
        id="features:cliAnalytics", settings=ComponentSetting(enabled=True, deliveryFramework={"rules": []})
    )
    assert _public_view(other) is other


@pytest.mark.asyncio
async def test_people_accepts_the_candidates_filter(enabled):
    handler = fake_handler()
    with patch(f"{MODULE}.UsageAttributionHandler", return_value=handler):
        response = await call(make_app(), "get", "/people?filter=cand&sort_by=prod_ncb")
    assert response.status_code == 200
    assert handler.people.await_args.args[2] == "cand"


@pytest.mark.asyncio
async def test_people_groups_passes_its_parameters_and_the_none_row(enabled):
    handler = fake_handler()
    handler.people_groups = AsyncMock(return_value=dict(TABLE, none={"id": "none", "candidates": 1, "amount": 60.0}))
    with patch(f"{MODULE}.UsageAttributionHandler", return_value=handler):
        response = await call(make_app(), "get", "/people-groups?group_by=organization&min_amount=500&search=acme")
    assert response.status_code == 200
    args = handler.people_groups.await_args.args
    assert (args[2], args[3], args[4]) == ("organization", 500, "acme")
    assert response.json()["data"]["none"]["amount"] == 60.0


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["group_by=account", "min_amount=-1", "min_amount=100001"])
async def test_people_groups_rejects_bad_parameters(enabled, query):
    with patch(f"{MODULE}.UsageAttributionHandler", return_value=fake_handler()):
        response = await call(make_app(), "get", f"/people-groups?{query}")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_people_group_unknown_id_is_404(enabled):
    from codemie.service.usage_attribution.handler import EntityNotFoundError

    handler = fake_handler()
    handler.people_group = AsyncMock(side_effect=EntityNotFoundError("project-0000000000"))
    with patch(f"{MODULE}.UsageAttributionHandler", return_value=handler):
        response = await call(make_app(), "get", "/people-groups/common/project-0000000000")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_auditor_can_read_people_groups_and_summary(enabled):
    with patch(f"{MODULE}.UsageAttributionHandler", return_value=fake_handler()):
        groups = await call(make_app(user=AUDITOR), "get", "/people-groups")
        summary = await call(make_app(user=AUDITOR), "get", "/people-summary")
    assert (groups.status_code, summary.status_code) == (200, 200)
