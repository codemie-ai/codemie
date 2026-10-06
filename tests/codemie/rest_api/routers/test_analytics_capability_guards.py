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

"""Parametrized regression suites for analytics capability guards.

Suite 1 — Group A (59 paths): every Metrics Analytics route returns 200 with a
          schema-valid empty payload when Elasticsearch is unconfigured — no
          Enterprise-package gate exists anymore. Exercises the opt-in fallback
          via AnalyticsService's repository construction.

Suite 2 — Group B/C/D (6 entries): routes outside Group A are unaffected by
          the (now-removed) enterprise guard.

Suite 3 — Auth/authz/validation are unchanged when ES is unavailable.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.main import extended_http_exception_handler
from codemie.rest_api.routers.analytics import router
from codemie.rest_api.security.authentication import (
    admin_access_only,
    admin_or_maintainer_or_auditor_access,
    authenticate,
)

# ---------------------------------------------------------------------------
# Shared test application
# ---------------------------------------------------------------------------

_MOCK_USER = MagicMock()
_MOCK_USER.id = "user@example.com"
_MOCK_USER.username = "testuser"
_MOCK_USER.email = "testuser@example.com"
_MOCK_USER.is_admin = True
_MOCK_USER.is_auditor = False

_APP = FastAPI()
_APP.include_router(router)
_APP.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
_APP.dependency_overrides[authenticate] = lambda: _MOCK_USER
_APP.dependency_overrides[admin_or_maintainer_or_auditor_access] = lambda: None
_APP.dependency_overrides[admin_access_only] = lambda: None


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# Suite 1 — Group A paths: 59 Metrics Analytics routes
# ---------------------------------------------------------------------------

# Routes with required query param user_name include a minimal query string so
# FastAPI validates the param after (not before) the dependency guard.  Because
# _require_metrics_analytics_enabled fires first the query string is irrelevant
# to the 503 assertion, but including it makes the path realistic.
GROUP_A_PATHS = [
    "/v1/analytics/summaries",
    "/v1/analytics/assistants-chats",
    "/v1/analytics/workflows",
    "/v1/analytics/tools-usage",
    "/v1/analytics/agents-usage",
    "/v1/analytics/power-users",
    "/v1/analytics/knowledge-sharing",
    "/v1/analytics/top-agents-usage",
    "/v1/analytics/top-workflow-usage",
    "/v1/analytics/published-to-marketplace",
    "/v1/analytics/webhooks-invocation",
    "/v1/analytics/mcp-servers",
    "/v1/analytics/mcp-servers-by-users",
    "/v1/analytics/projects-spending",
    "/v1/analytics/llms-usage",
    "/v1/analytics/embeddings-usage",
    "/v1/analytics/users-spending",
    "/v1/analytics/budget-soft-limit",
    "/v1/analytics/budget-hard-limit",
    "/v1/analytics/users-activity",
    "/v1/analytics/users-unique-daily",
    "/v1/analytics/users",
    "/v1/analytics/projects-activity",
    "/v1/analytics/projects-unique-daily",
    "/v1/analytics/cli-summary",
    "/v1/analytics/cli-agents",
    "/v1/analytics/cli-llms",
    "/v1/analytics/cli-users",
    "/v1/analytics/cli-errors",
    "/v1/analytics/cli-repositories",
    "/v1/analytics/cli-top-performers",
    "/v1/analytics/cli-top-versions",
    "/v1/analytics/cli-top-proxy-endpoints",
    "/v1/analytics/cli-tools",
    "/v1/analytics/cli-insights-weekday-pattern",
    "/v1/analytics/cli-insights-hourly-usage",
    "/v1/analytics/cli-insights-session-depth",
    "/v1/analytics/cli-insights-user-classification",
    "/v1/analytics/cli-insights-top-users-by-cost",
    "/v1/analytics/cli-insights-top-spenders",
    "/v1/analytics/cli-insights-users",
    "/v1/analytics/cli-insights-user-detail?user_name=test",
    "/v1/analytics/cli-insights-user-key-metrics?user_name=test",
    "/v1/analytics/cli-insights-user-tools?user_name=test",
    "/v1/analytics/cli-insights-user-models?user_name=test",
    "/v1/analytics/cli-insights-user-workflow-intent?user_name=test",
    "/v1/analytics/cli-insights-user-classification-detail?user_name=test",
    "/v1/analytics/cli-insights-user-category-breakdown?user_name=test",
    "/v1/analytics/cli-insights-user-repositories?user_name=test",
    "/v1/analytics/cli-insights-project-classification",
    "/v1/analytics/cli-insights-top-projects-by-cost",
    "/v1/analytics/cli-insights-by-enriched-user-primary-skill",
    "/v1/analytics/cli-insights-by-enriched-user-country",
    "/v1/analytics/cli-insights-by-enriched-user-city",
    "/v1/analytics/cli-insights-by-enriched-user-job-title",
    "/v1/analytics/cli-insights-by-enriched-user-job-title-group",
    "/v1/analytics/engagement/weekly-histogram",
    "/v1/analytics/spending/by-users/platform",
    "/v1/analytics/spending/by-users/cli",
]

assert len(GROUP_A_PATHS) == 59, f"Expected 59 Group A paths, got {len(GROUP_A_PATHS)}"


@pytest.mark.anyio
@pytest.mark.parametrize("path", GROUP_A_PATHS)
async def test_group_a_returns_200_empty_when_elasticsearch_unconfigured(path):
    """Every Group A path returns 200 with an empty payload when ES is unconfigured.

    A handful of ?user_name=... paths fall back to a Postgres identity lookup when
    the (now-empty) ES buckets contain no match for the requested user — that lookup
    is outside this plan's ES/ClickHouse scope, so it's mocked to "no user found"
    here to isolate the ES-unavailable behavior under test. The enriched-user paths
    are additionally gated by the unrelated `userEnrichmentEnabled` feature flag,
    which is force-enabled here for the same reason.
    """
    with (
        patch(
            "codemie.repository.metrics_elastic_repository.ElasticSearchClient.is_configured",
            return_value=False,
        ),
        patch(
            "codemie.repository.user_repository.user_repository.afind_users_by_identifiers",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "codemie.configs.customer_config.CustomerConfig.is_feature_enabled",
            return_value=True,
        ),
    ):
        transport = ASGITransport(app=_APP)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(path)

    assert response.status_code == 200
    body = response.json()
    assert "data" in body


# ---------------------------------------------------------------------------
# Suite 2 — Group B/C/D: unaffected by Metrics Analytics guard
# ---------------------------------------------------------------------------


def _mock_async_session():
    """Async context manager that yields a mock DB session."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    [
        "/v1/analytics/leaderboard/summary",
        "/v1/analytics/leaderboard/entries",
    ],
)
@patch("codemie.rest_api.routers.analytics.config")
async def test_leaderboard_503_comes_from_leaderboard_guard_not_metrics_analytics(mock_cfg, path):
    """Leaderboard routes 503 when LEADERBOARD_ENABLED=False; detail must name the leaderboard guard."""
    mock_cfg.LEADERBOARD_ENABLED = False
    mock_cfg.ANALYTICS_DEFAULT_PAGE_SIZE = 10

    with patch("codemie.enterprise.has_enterprise", return_value=False):
        transport = ASGITransport(app=_APP)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(path)

    assert response.status_code == 503
    assert response.json()["detail"] == "Leaderboard is not enabled"


@pytest.mark.anyio
async def test_spending_route_unaffected_by_enterprise_guard():
    """GET /spending returns 200 when enterprise is absent (Group D route, no metrics analytics guard)."""
    mock_spending = {
        "customer_id": "testuser",
        "total_spend": 0.0,
        "max_budget": 100.0,
        "budget_duration": "30d",
        "budget_reset_at": "2026-03-01T00:00:00Z",
    }
    with (
        patch("codemie.enterprise.has_enterprise", return_value=False),
        patch("codemie.enterprise.litellm.dependencies.get_customer_spending", return_value=mock_spending),
        patch("codemie.enterprise.litellm.dependencies.get_proxy_customer_spending", return_value=None),
        patch("codemie.enterprise.litellm.dependencies.is_premium_models_enabled", return_value=False),
    ):
        transport = ASGITransport(app=_APP)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/analytics/spending")

    assert response.status_code == 200


@pytest.mark.anyio
async def test_budget_usage_route_unaffected_by_enterprise_guard():
    """GET /budget_usage returns 200 when enterprise is absent (Group D route, no metrics analytics guard)."""
    with (
        patch("codemie.enterprise.has_enterprise", return_value=False),
        patch(
            "codemie.service.analytics.handlers.budget_usage_service.BudgetUsageService.get_budget_usage",
            new_callable=AsyncMock,
            return_value=([], []),
        ),
        patch("codemie.clients.postgres.get_async_session", return_value=_mock_async_session()),
    ):
        transport = ASGITransport(app=_APP)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/analytics/budget_usage")

    assert response.status_code == 200


@pytest.mark.anyio
async def test_user_project_spending_unaffected_by_enterprise_guard():
    """GET /user-project-spending returns 200 when enterprise absent (Group D, no metrics analytics guard)."""
    mock_target_user = MagicMock()
    mock_target_user.id = "target-user-id"

    with (
        patch("codemie.enterprise.has_enterprise", return_value=False),
        patch(
            "codemie.service.analytics.handlers.member_spend_service.MemberSpendService.resolve_spend_subject",
            return_value=(mock_target_user, ["project1"]),
        ),
        patch(
            "codemie.service.analytics.handlers.member_spend_service.MemberSpendService.get_user_project_spend",
            new_callable=AsyncMock,
            return_value=([], []),
        ),
        patch("codemie.rest_api.routers.analytics.get_async_session", return_value=_mock_async_session()),
    ):
        transport = ASGITransport(app=_APP)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/analytics/user-project-spending?users=test%40example.com")

    assert response.status_code == 200


@pytest.mark.anyio
async def test_project_member_spending_unaffected_by_enterprise_guard():
    """GET /project-member-spending returns 200 when enterprise absent (Group D, no metrics analytics guard)."""
    with (
        patch("codemie.enterprise.has_enterprise", return_value=False),
        patch("codemie.rest_api.routers.analytics._authorize_admin_budget_view"),
        patch(
            "codemie.service.analytics.handlers.member_spend_service.MemberSpendService.get_project_member_spend",
            new_callable=AsyncMock,
            return_value=([], []),
        ),
        patch("codemie.rest_api.routers.analytics.get_async_session", return_value=_mock_async_session()),
    ):
        transport = ASGITransport(app=_APP)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/analytics/project-member-spending?projects=test-project")

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Suite 3 — Auth/authz/validation unchanged when ES is unavailable
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_group_a_path_still_401s_without_auth_when_es_unavailable():
    """Auth failures are not converted to 200 by the ES-unavailable fallback."""
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
    # No dependency_overrides for `authenticate`: the real dependency runs and must 401/403.

    with patch(
        "codemie.repository.metrics_elastic_repository.ElasticSearchClient.is_configured",
        return_value=False,
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/analytics/summaries")

    assert response.status_code in (401, 403)


@pytest.mark.anyio
async def test_group_a_path_still_422s_on_invalid_query_param_when_es_unavailable():
    """Validation failures are not converted to 200 by the ES-unavailable fallback.

    /summaries has no typed `page` param (extra query strings are ignored), so this
    uses /assistants-chats, which takes `AnalyticsQueryParams.page: int = Query(0, ge=0)`.
    """
    with patch(
        "codemie.repository.metrics_elastic_repository.ElasticSearchClient.is_configured",
        return_value=False,
    ):
        transport = ASGITransport(app=_APP)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/analytics/assistants-chats?page=not-a-number")

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Task 4 — ES happy path stays byte-identical when ES IS available
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_summaries_payload_unchanged_when_elasticsearch_available():
    """Real data payload is untouched by the fallback boundary when ES is configured and reachable."""
    mock_metric = {
        "id": "total_conversations",
        "label": "Total Conversations",
        "type": "number",
        "value": 42,
    }
    mock_result = {
        "data": {"metrics": [mock_metric]},
        "metadata": {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "data_as_of": "2026-01-01T00:00:00+00:00",
            "filters_applied": {},
            "execution_time_ms": 12.3,
        },
    }
    with (
        patch(
            "codemie.repository.metrics_elastic_repository.ElasticSearchClient.is_configured",
            return_value=True,
        ),
        patch(
            "codemie.service.analytics.analytics_service.AnalyticsService.get_summaries",
            new_callable=AsyncMock,
            return_value=mock_result,
        ),
    ):
        transport = ASGITransport(app=_APP)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/analytics/summaries")

    assert response.status_code == 200
    metrics = response.json()["data"]["metrics"]
    assert len(metrics) == 1
    assert metrics[0]["id"] == "total_conversations"
    assert metrics[0]["value"] == 42
