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

"""Unit tests for enriched-user analytics endpoints.

Covers:
- 403 when userEnrichmentEnabled feature flag is off
- Correct EnrichedUserScope passed for each of the four endpoints
  (primary_skill, country, city, job_title)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.responses import JSONResponse

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers.analytics import (
    CLIEnrichedUserParams,
    get_cli_insights_by_enriched_user_city,
    get_cli_insights_by_enriched_user_country,
    get_cli_insights_by_enriched_user_job_title,
    get_cli_insights_by_enriched_user_primary_skill,
)
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.cli_handler import EnrichedUserScope


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_TABULAR_DATA = {
    "data": {
        "columns": [{"id": "label", "label": "Label", "type": "string"}],
        "rows": [],
        "totals": {},
    },
    "metadata": {
        "timestamp": "2026-01-01T00:00:00Z",
        "data_as_of": "2026-01-01T00:00:00Z",
        "filters_applied": {},
        "execution_time_ms": 1.0,
    },
    "pagination": {"page": 0, "per_page": 50, "total_count": 0, "has_more": False},
}


@pytest.fixture
def mock_user():
    user = MagicMock(spec=User)
    user.id = "user@example.com"
    user.email = "user@example.com"
    user.is_admin = True
    user.project_names = []
    user.admin_project_names = []
    return user


@pytest.fixture
def default_params():
    """Standard CLIEnrichedUserParams with all-None filters."""
    params = MagicMock(spec=CLIEnrichedUserParams)
    params.time_period = None
    params.start_date = None
    params.end_date = None
    params.users = None
    params.users_list = None
    params.projects = None
    params.projects_list = None
    params.page = 0
    params.per_page = 50
    return params


def _patch_feature(enabled: bool):
    mock_cfg = MagicMock()
    mock_cfg.is_feature_enabled.return_value = enabled
    return patch("codemie.rest_api.routers.analytics.customer_config", mock_cfg)


def _patch_service(return_data: dict):
    mock_service = AsyncMock()
    mock_service.get_cli_insights_by_enriched_user.return_value = return_data
    return patch(
        "codemie.rest_api.routers.analytics.AnalyticsService",
        return_value=mock_service,
    ), mock_service


# ---------------------------------------------------------------------------
# Feature-flag guard tests
# ---------------------------------------------------------------------------


class TestFeatureFlagGuard:
    """All four endpoints return 403 when userEnrichmentEnabled is off."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "endpoint,scope",
        [
            (get_cli_insights_by_enriched_user_primary_skill, EnrichedUserScope.PRIMARY_SKILL),
            (get_cli_insights_by_enriched_user_country, EnrichedUserScope.COUNTRY),
            (get_cli_insights_by_enriched_user_city, EnrichedUserScope.CITY),
            (get_cli_insights_by_enriched_user_job_title, EnrichedUserScope.JOB_TITLE),
        ],
    )
    async def test_raises_403_when_feature_disabled(self, mock_user, default_params, endpoint, scope):
        with _patch_feature(False):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await endpoint(user=mock_user, params=default_params)

        assert exc_info.value.code == status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_403_message_mentions_enrichment(self, mock_user, default_params):
        with _patch_feature(False):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                await get_cli_insights_by_enriched_user_country(user=mock_user, params=default_params)

        assert "enrichment" in exc_info.value.message.lower() or "enrichment" in exc_info.value.details.lower()


# ---------------------------------------------------------------------------
# Scope routing tests
# ---------------------------------------------------------------------------


class TestScopeRouting:
    """Each endpoint passes the correct EnrichedUserScope to the service."""

    @pytest.mark.asyncio
    async def test_primary_skill_endpoint_passes_primary_skill_scope(self, mock_user, default_params):
        patch_service, mock_service = _patch_service(_TABULAR_DATA)
        with _patch_feature(True), patch_service:
            await get_cli_insights_by_enriched_user_primary_skill(user=mock_user, params=default_params)

        call_kwargs = mock_service.get_cli_insights_by_enriched_user.call_args[1]
        assert call_kwargs["scope"] == EnrichedUserScope.PRIMARY_SKILL

    @pytest.mark.asyncio
    async def test_country_endpoint_passes_country_scope(self, mock_user, default_params):
        patch_service, mock_service = _patch_service(_TABULAR_DATA)
        with _patch_feature(True), patch_service:
            await get_cli_insights_by_enriched_user_country(user=mock_user, params=default_params)

        call_kwargs = mock_service.get_cli_insights_by_enriched_user.call_args[1]
        assert call_kwargs["scope"] == EnrichedUserScope.COUNTRY

    @pytest.mark.asyncio
    async def test_city_endpoint_passes_city_scope(self, mock_user, default_params):
        patch_service, mock_service = _patch_service(_TABULAR_DATA)
        with _patch_feature(True), patch_service:
            await get_cli_insights_by_enriched_user_city(user=mock_user, params=default_params)

        call_kwargs = mock_service.get_cli_insights_by_enriched_user.call_args[1]
        assert call_kwargs["scope"] == EnrichedUserScope.CITY

    @pytest.mark.asyncio
    async def test_job_title_endpoint_passes_job_title_scope(self, mock_user, default_params):
        patch_service, mock_service = _patch_service(_TABULAR_DATA)
        with _patch_feature(True), patch_service:
            await get_cli_insights_by_enriched_user_job_title(user=mock_user, params=default_params)

        call_kwargs = mock_service.get_cli_insights_by_enriched_user.call_args[1]
        assert call_kwargs["scope"] == EnrichedUserScope.JOB_TITLE


# ---------------------------------------------------------------------------
# Successful response tests
# ---------------------------------------------------------------------------


class TestSuccessfulResponse:
    """When feature is enabled, endpoints return a JSONResponse."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "endpoint",
        [
            get_cli_insights_by_enriched_user_primary_skill,
            get_cli_insights_by_enriched_user_country,
            get_cli_insights_by_enriched_user_city,
            get_cli_insights_by_enriched_user_job_title,
        ],
    )
    async def test_returns_json_response(self, mock_user, default_params, endpoint):
        patch_service, _ = _patch_service(_TABULAR_DATA)
        with _patch_feature(True), patch_service:
            result = await endpoint(user=mock_user, params=default_params)

        assert isinstance(result, JSONResponse)
        assert result.status_code == status.HTTP_200_OK

    @pytest.mark.asyncio
    async def test_filter_params_forwarded_to_service(self, mock_user):
        params = MagicMock(spec=CLIEnrichedUserParams)
        params.time_period = "last_30_days"
        params.start_date = None
        params.end_date = None
        params.users_list = ["alice@example.com"]
        params.projects_list = ["proj-a"]
        params.page = 1
        params.per_page = 25

        patch_service, mock_service = _patch_service(_TABULAR_DATA)
        with _patch_feature(True), patch_service:
            await get_cli_insights_by_enriched_user_country(user=mock_user, params=params)

        call_kwargs = mock_service.get_cli_insights_by_enriched_user.call_args[1]
        assert call_kwargs["time_period"] == "last_30_days"
        assert call_kwargs["users"] == ["alice@example.com"]
        assert call_kwargs["projects"] == ["proj-a"]
        assert call_kwargs["page"] == 1
        assert call_kwargs["per_page"] == 25
