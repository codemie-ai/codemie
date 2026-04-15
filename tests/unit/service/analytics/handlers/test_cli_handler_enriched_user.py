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

"""Unit tests for CLIHandler enriched-user aggregation methods.

Covers:
- _aggregate_user_rows_by_enrichment_field: grouping, "No Data" fallback,
  cost accumulation + rounding, sort order, empty-email handling
- get_cli_insights_by_enriched_user: column definitions, scope label,
  pagination, response structure
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.analytics.handlers.cli_handler import CLIHandler, EnrichedUserScope

_HANDLER_MODULE = "codemie.service.analytics.handlers.cli_handler"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_row(email: str, cost: float = 0.0) -> dict:
    return {
        "user_id": email,
        "user_name": email,
        "user_email": email,
        "total_cost": cost,
        "total_sessions": 1,
        "total_lines_added": 0,
        "total_lines_removed": 0,
        "net_lines": 0,
        "classification": "unknown",
    }


def _enrichment(field: str, value: str) -> MagicMock:
    """Return a fake enrichment object with *field* set to *value*."""
    obj = MagicMock()
    setattr(obj, field, value)
    return obj


@asynccontextmanager
async def _fake_session():
    yield AsyncMock()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_user():
    return MagicMock()


@pytest.fixture()
def mock_repo():
    return MagicMock()


@pytest.fixture()
def handler(mock_user, mock_repo):
    """CLIHandler with faked DB session and mocked ES repository."""
    with patch(f"{_HANDLER_MODULE}.get_async_session", side_effect=_fake_session):
        h = CLIHandler(mock_user, mock_repo)
        yield h


# ---------------------------------------------------------------------------
# _aggregate_user_rows_by_enrichment_field
# ---------------------------------------------------------------------------


class TestAggregateUserRowsByEnrichmentField:
    """Unit tests for _aggregate_user_rows_by_enrichment_field."""

    @pytest.mark.asyncio
    async def test_users_without_enrichment_land_in_no_data_bucket(self, handler):
        user_rows = [_user_row("alice@example.com", cost=5.0)]

        with (
            patch.object(handler, "_get_cli_insights_user_rows", AsyncMock(return_value=user_rows)),
            patch(f"{_HANDLER_MODULE}.user_enrichment_repository.get_by_emails", AsyncMock(return_value={})),
        ):
            result = await handler._aggregate_user_rows_by_enrichment_field("country", None, None, None, None, None)

        assert len(result) == 1
        assert result[0]["country"] == CLIHandler.NO_HR_DATA_LABEL
        assert result[0]["user_count"] == 1

    @pytest.mark.asyncio
    async def test_users_with_null_field_land_in_no_data_bucket(self, handler):
        """Enrichment record exists but the specific field is None."""
        user_rows = [_user_row("alice@example.com", cost=3.0)]
        enrichment = _enrichment("country", None)

        with (
            patch.object(handler, "_get_cli_insights_user_rows", AsyncMock(return_value=user_rows)),
            patch(
                f"{_HANDLER_MODULE}.user_enrichment_repository.get_by_emails",
                AsyncMock(return_value={"alice@example.com": enrichment}),
            ),
        ):
            result = await handler._aggregate_user_rows_by_enrichment_field("country", None, None, None, None, None)

        assert result[0]["country"] == CLIHandler.NO_HR_DATA_LABEL

    @pytest.mark.asyncio
    async def test_groups_users_by_enrichment_field_value(self, handler):
        user_rows = [
            _user_row("alice@example.com", cost=10.0),
            _user_row("bob@example.com", cost=5.0),
            _user_row("carol@example.com", cost=3.0),
        ]
        enrichment_map = {
            "alice@example.com": _enrichment("country", "Poland"),
            "bob@example.com": _enrichment("country", "Poland"),
            "carol@example.com": _enrichment("country", "Germany"),
        }

        with (
            patch.object(handler, "_get_cli_insights_user_rows", AsyncMock(return_value=user_rows)),
            patch(
                f"{_HANDLER_MODULE}.user_enrichment_repository.get_by_emails",
                AsyncMock(return_value=enrichment_map),
            ),
        ):
            result = await handler._aggregate_user_rows_by_enrichment_field("country", None, None, None, None, None)

        by_country = {r["country"]: r for r in result}
        assert by_country["Poland"]["user_count"] == 2
        assert by_country["Poland"]["total_cost"] == 15.0
        assert by_country["Germany"]["user_count"] == 1
        assert by_country["Germany"]["total_cost"] == 3.0

    @pytest.mark.asyncio
    async def test_results_sorted_by_total_cost_descending(self, handler):
        user_rows = [
            _user_row("a@x.com", cost=1.0),
            _user_row("b@x.com", cost=50.0),
            _user_row("c@x.com", cost=10.0),
        ]
        enrichment_map = {
            "a@x.com": _enrichment("country", "Low"),
            "b@x.com": _enrichment("country", "High"),
            "c@x.com": _enrichment("country", "Mid"),
        }

        with (
            patch.object(handler, "_get_cli_insights_user_rows", AsyncMock(return_value=user_rows)),
            patch(
                f"{_HANDLER_MODULE}.user_enrichment_repository.get_by_emails",
                AsyncMock(return_value=enrichment_map),
            ),
        ):
            result = await handler._aggregate_user_rows_by_enrichment_field("country", None, None, None, None, None)

        costs = [r["total_cost"] for r in result]
        assert costs == sorted(costs, reverse=True)

    @pytest.mark.asyncio
    async def test_cost_accumulation_is_rounded_to_two_decimals(self, handler):
        # 1.123 + 2.456 = 3.579; each step rounds to 2 dp:
        #   round(0.0 + 1.123, 2) = 1.12
        #   round(1.12 + 2.456, 2) = 3.58
        user_rows = [
            _user_row("a@x.com", cost=1.123),
            _user_row("b@x.com", cost=2.456),
        ]
        enrichment_map = {
            "a@x.com": _enrichment("country", "Poland"),
            "b@x.com": _enrichment("country", "Poland"),
        }

        with (
            patch.object(handler, "_get_cli_insights_user_rows", AsyncMock(return_value=user_rows)),
            patch(
                f"{_HANDLER_MODULE}.user_enrichment_repository.get_by_emails",
                AsyncMock(return_value=enrichment_map),
            ),
        ):
            result = await handler._aggregate_user_rows_by_enrichment_field("country", None, None, None, None, None)

        assert len(result) == 1
        assert isinstance(result[0]["total_cost"], float)
        assert result[0]["total_cost"] == 3.58

    @pytest.mark.asyncio
    async def test_rows_without_email_are_excluded_from_enrichment_lookup(self, handler):
        user_rows = [{"user_email": None, "total_cost": 7.0}]
        mock_get = AsyncMock(return_value={})

        with (
            patch.object(handler, "_get_cli_insights_user_rows", AsyncMock(return_value=user_rows)),
            patch(f"{_HANDLER_MODULE}.user_enrichment_repository.get_by_emails", mock_get),
        ):
            result = await handler._aggregate_user_rows_by_enrichment_field("country", None, None, None, None, None)

        # The email list passed to the repo should be empty (None was filtered out)
        call_args = mock_get.call_args[0]
        assert call_args[1] == []
        # Row still lands in "No Data"
        assert result[0]["country"] == CLIHandler.NO_HR_DATA_LABEL

    @pytest.mark.asyncio
    async def test_empty_user_rows_returns_empty_list(self, handler):
        with (
            patch.object(handler, "_get_cli_insights_user_rows", AsyncMock(return_value=[])),
            patch(
                f"{_HANDLER_MODULE}.user_enrichment_repository.get_by_emails",
                AsyncMock(return_value={}),
            ),
        ):
            result = await handler._aggregate_user_rows_by_enrichment_field("country", None, None, None, None, None)

        assert result == []

    @pytest.mark.asyncio
    async def test_email_lookup_is_case_insensitive(self, handler):
        """Mixed-case email in user_rows still matches lowercase enrichment key."""
        user_rows = [_user_row("ALICE@EXAMPLE.COM", cost=8.0)]
        enrichment_map = {"alice@example.com": _enrichment("country", "Poland")}

        with (
            patch.object(handler, "_get_cli_insights_user_rows", AsyncMock(return_value=user_rows)),
            patch(
                f"{_HANDLER_MODULE}.user_enrichment_repository.get_by_emails",
                AsyncMock(return_value=enrichment_map),
            ),
        ):
            result = await handler._aggregate_user_rows_by_enrichment_field("country", None, None, None, None, None)

        assert len(result) == 1
        assert result[0]["country"] == "Poland"

    @pytest.mark.asyncio
    async def test_works_with_job_title_field(self, handler):
        user_rows = [_user_row("alice@example.com", cost=5.0)]
        enrichment_map = {"alice@example.com": _enrichment("job_title", "Engineer")}

        with (
            patch.object(handler, "_get_cli_insights_user_rows", AsyncMock(return_value=user_rows)),
            patch(
                f"{_HANDLER_MODULE}.user_enrichment_repository.get_by_emails",
                AsyncMock(return_value=enrichment_map),
            ),
        ):
            result = await handler._aggregate_user_rows_by_enrichment_field("job_title", None, None, None, None, None)

        assert result[0]["job_title"] == "Engineer"


# ---------------------------------------------------------------------------
# get_cli_insights_by_enriched_user
# ---------------------------------------------------------------------------


class TestGetCliInsightsByEnrichedUser:
    """Unit tests for get_cli_insights_by_enriched_user."""

    def _patch_aggregate(self, handler, rows: list[dict]):
        return patch.object(
            handler,
            "_aggregate_user_rows_by_enrichment_field",
            AsyncMock(return_value=rows),
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "scope,field,label",
        [
            (EnrichedUserScope.PRIMARY_SKILL, "primary_skill", "Primary Skill"),
            (EnrichedUserScope.COUNTRY, "country", "Country"),
            (EnrichedUserScope.CITY, "city", "City"),
            (EnrichedUserScope.JOB_TITLE, "job_title", "Job Title"),
        ],
    )
    async def test_column_definitions_match_scope(self, handler, scope, field, label):
        rows = [{field: "X", "user_count": 1, "total_cost": 1.0}]

        with self._patch_aggregate(handler, rows):
            result = await handler.get_cli_insights_by_enriched_user(scope=scope)

        columns = result["data"]["columns"]
        col_ids = [c["id"] for c in columns]
        assert col_ids[0] == field
        assert columns[0]["label"] == label
        assert "user_count" in col_ids
        assert "total_cost" in col_ids

    @pytest.mark.asyncio
    async def test_passes_scope_value_as_field_to_aggregate(self, handler):
        mock_agg = AsyncMock(return_value=[])

        with patch.object(handler, "_aggregate_user_rows_by_enrichment_field", mock_agg):
            await handler.get_cli_insights_by_enriched_user(scope=EnrichedUserScope.COUNTRY)

        assert mock_agg.call_args[0][0] == "country"

    @pytest.mark.asyncio
    async def test_pagination_slices_rows(self, handler):
        rows = [{"country": f"C{i}", "user_count": 1, "total_cost": float(i)} for i in range(10)]

        with self._patch_aggregate(handler, rows):
            result = await handler.get_cli_insights_by_enriched_user(
                scope=EnrichedUserScope.COUNTRY, page=1, per_page=3
            )

        returned_rows = result["data"]["rows"]
        assert len(returned_rows) == 3
        assert result["pagination"]["page"] == 1
        assert result["pagination"]["per_page"] == 3
        assert result["pagination"]["total_count"] == 10

    @pytest.mark.asyncio
    async def test_response_contains_data_metadata_pagination(self, handler):
        with self._patch_aggregate(handler, []):
            result = await handler.get_cli_insights_by_enriched_user(scope=EnrichedUserScope.CITY)

        assert "data" in result
        assert "metadata" in result
        assert "pagination" in result

    @pytest.mark.asyncio
    async def test_forwards_filter_params_to_aggregate(self, handler):
        mock_agg = AsyncMock(return_value=[])

        with patch.object(handler, "_aggregate_user_rows_by_enrichment_field", mock_agg):
            await handler.get_cli_insights_by_enriched_user(
                scope=EnrichedUserScope.JOB_TITLE,
                time_period="last_30_days",
                users=["alice@example.com"],
                projects=["proj-a"],
                page=0,
                per_page=25,
            )

        _, tp, _, _, users, projects = mock_agg.call_args[0]
        assert tp == "last_30_days"
        assert users == ["alice@example.com"]
        assert projects == ["proj-a"]

    @pytest.mark.asyncio
    async def test_last_page_returns_remaining_rows(self, handler):
        rows = [{"primary_skill": f"S{i}", "user_count": 1, "total_cost": float(i)} for i in range(7)]

        with self._patch_aggregate(handler, rows):
            result = await handler.get_cli_insights_by_enriched_user(
                scope=EnrichedUserScope.PRIMARY_SKILL, page=1, per_page=5
            )

        assert len(result["data"]["rows"]) == 2
        assert result["pagination"]["has_more"] is False
