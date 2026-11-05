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

"""Unit tests for CLIInsightsHandler enriched-user aggregation methods.

Covers:
- _aggregate_user_rows_by_enrichment_field: grouping, "No Data" fallback,
  ledger cost combination, sort order, empty-email handling
- get_cli_insights_by_enriched_user: column definitions, scope label,
  pagination, response structure
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.analytics.handlers.cli.classification_engine import CLIClassificationEngine
from codemie.service.analytics.handlers.cli.insights_handler import CLIInsightsHandler, EnrichedUserScope
from codemie.service.spend.rendering import RenderedSpend

_HANDLER_MODULE = "codemie.service.analytics.handlers.cli.insights_handler"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _es_bucket(email: str | None, user_id: str | None = None) -> dict:
    """Return a fake Elasticsearch user bucket matching the enrichment aggregation format.

    It only lists the user: the money for each one comes from the spend port, so the bucket carries
    no cost. `email=None` is the live shape of a bucket whose `top_metrics` returned nothing.
    """
    return {
        "key": user_id or f"u-{email}",
        "user_email": {"top": [{"metrics": {"attributes.user_email.keyword": email}}] if email else []},
    }


def _es_result(buckets: list[dict]) -> dict:
    """Wrap buckets in the ES aggregation response envelope."""
    return {"aggregations": {"users": {"buckets": buckets}}}


def _exact(figure: float) -> RenderedSpend:
    """A whole ledger figure."""
    return RenderedSpend(figure=figure, unavailable=False, partial=False, missing_amount=None)


def _absent() -> RenderedSpend:
    """No ledger figure for the user, which is not the same as zero."""
    return RenderedSpend(figure=None, unavailable=True, partial=False, missing_amount=None)


def _answer(handler, buckets: list[dict], spends: list[RenderedSpend]) -> None:
    """Elasticsearch lists the users; the (stubbed) ledger read gives each one's CLI figure, in the same order."""
    handler.repository.execute_aggregation_query.return_value = _es_result(buckets)
    handler._cli_spend_by_user = AsyncMock(return_value=spends)


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
    repo = MagicMock()
    repo.execute_aggregation_query = AsyncMock(return_value=_es_result([]))
    return repo


@pytest.fixture()
def handler(mock_user, mock_repo):
    """CLIInsightsHandler with faked DB session and mocked ES repository."""
    with patch(f"{_HANDLER_MODULE}.get_async_session", side_effect=_fake_session):
        h = CLIInsightsHandler(mock_user, mock_repo)
        yield h


# ---------------------------------------------------------------------------
# _aggregate_user_rows_by_enrichment_field
# ---------------------------------------------------------------------------


class TestAggregateUserRowsByEnrichmentField:
    """Unit tests for _aggregate_user_rows_by_enrichment_field."""

    @staticmethod
    def _enrichments(map_: dict):
        return patch(f"{_HANDLER_MODULE}.user_enrichment_repository.get_by_emails", AsyncMock(return_value=map_))

    @pytest.mark.asyncio
    async def test_each_members_spend_is_read_from_the_ledger_by_user_id(self, handler):
        _answer(
            handler,
            [_es_bucket("alice@example.com", "u-alice"), _es_bucket("bob@example.com", "u-bob")],
            [_exact(1.0), _exact(2.0)],
        )

        with self._enrichments({}):
            await handler._aggregate_user_rows_by_enrichment_field("country", None, None, None, None, ["proj-a"])

        user_ids, _start, _end, projects = handler._cli_spend_by_user.await_args.args
        assert user_ids == ["u-alice", "u-bob"]
        assert projects == ["proj-a"]

    @pytest.mark.asyncio
    async def test_users_without_enrichment_land_in_no_data_bucket(self, handler):
        _answer(handler, [_es_bucket("alice@example.com")], [_exact(5.0)])

        with self._enrichments({}):
            result = await handler._aggregate_user_rows_by_enrichment_field("country", None, None, None, None, None)

        assert len(result) == 1
        assert result[0]["country"] == CLIClassificationEngine.NO_HR_DATA_LABEL
        assert result[0]["user_count"] == 1

    @pytest.mark.asyncio
    async def test_users_with_null_field_land_in_no_data_bucket(self, handler):
        """Enrichment record exists but the specific field is None."""
        _answer(handler, [_es_bucket("alice@example.com")], [_exact(3.0)])

        with self._enrichments({"alice@example.com": _enrichment("country", None)}):
            result = await handler._aggregate_user_rows_by_enrichment_field("country", None, None, None, None, None)

        assert result[0]["country"] == CLIClassificationEngine.NO_HR_DATA_LABEL

    @pytest.mark.asyncio
    async def test_groups_users_by_enrichment_field_value(self, handler):
        _answer(
            handler,
            [_es_bucket("alice@example.com"), _es_bucket("bob@example.com"), _es_bucket("carol@example.com")],
            [_exact(10.0), _exact(5.0), _exact(3.0)],
        )
        enrichment_map = {
            "alice@example.com": _enrichment("country", "Poland"),
            "bob@example.com": _enrichment("country", "Poland"),
            "carol@example.com": _enrichment("country", "Germany"),
        }

        with self._enrichments(enrichment_map):
            result = await handler._aggregate_user_rows_by_enrichment_field("country", None, None, None, None, None)

        by_country = {r["country"]: r for r in result}
        assert by_country["Poland"]["user_count"] == 2
        assert by_country["Poland"]["total_cost"] == 15.0
        assert by_country["Germany"]["user_count"] == 1
        assert by_country["Germany"]["total_cost"] == 3.0

    @pytest.mark.asyncio
    async def test_results_sorted_by_total_cost_descending(self, handler):
        _answer(
            handler,
            [_es_bucket("a@x.com"), _es_bucket("b@x.com"), _es_bucket("c@x.com")],
            [_exact(1.0), _exact(50.0), _exact(10.0)],
        )
        enrichment_map = {
            "a@x.com": _enrichment("country", "Low"),
            "b@x.com": _enrichment("country", "High"),
            "c@x.com": _enrichment("country", "Mid"),
        }

        with self._enrichments(enrichment_map):
            result = await handler._aggregate_user_rows_by_enrichment_field("country", None, None, None, None, None)

        costs = [r["total_cost"] for r in result]
        assert costs == sorted(costs, reverse=True)

    @pytest.mark.asyncio
    async def test_a_dimension_total_is_the_sum_of_its_members_ledger_figures_unrounded(self, handler):
        """Sent unrounded like every ledger cell: a rounded sum of sub-cent figures would read as $0.00."""
        _answer(handler, [_es_bucket("a@x.com"), _es_bucket("b@x.com")], [_exact(1.123), _exact(2.456)])
        enrichment_map = {
            "a@x.com": _enrichment("country", "Poland"),
            "b@x.com": _enrichment("country", "Poland"),
        }

        with self._enrichments(enrichment_map):
            result = await handler._aggregate_user_rows_by_enrichment_field("country", None, None, None, None, None)

        assert len(result) == 1
        assert isinstance(result[0]["total_cost"], float)
        assert result[0]["total_cost"] == pytest.approx(3.579)

    @pytest.mark.asyncio
    async def test_rows_without_email_are_excluded_from_enrichment_lookup(self, handler):
        # Bucket with no top_metrics entry yields user_email=None
        _answer(handler, [_es_bucket(None)], [_exact(7.0)])
        mock_get = AsyncMock(return_value={})

        with patch(f"{_HANDLER_MODULE}.user_enrichment_repository.get_by_emails", mock_get):
            result = await handler._aggregate_user_rows_by_enrichment_field("country", None, None, None, None, None)

        # The email list passed to the repo should be empty (None was filtered out)
        call_args = mock_get.call_args[0]
        assert call_args[1] == []
        # Row still lands in "No Data"
        assert result[0]["country"] == CLIClassificationEngine.NO_HR_DATA_LABEL

    @pytest.mark.asyncio
    async def test_empty_user_rows_returns_empty_list(self, handler):
        _answer(handler, [], [])

        with self._enrichments({}):
            result = await handler._aggregate_user_rows_by_enrichment_field("country", None, None, None, None, None)

        assert result == []

    @pytest.mark.asyncio
    async def test_email_lookup_is_case_insensitive(self, handler):
        """Mixed-case email in user_rows still matches lowercase enrichment key."""
        _answer(handler, [_es_bucket("ALICE@EXAMPLE.COM")], [_exact(8.0)])

        with self._enrichments({"alice@example.com": _enrichment("country", "Poland")}):
            result = await handler._aggregate_user_rows_by_enrichment_field("country", None, None, None, None, None)

        assert len(result) == 1
        assert result[0]["country"] == "Poland"

    @pytest.mark.asyncio
    async def test_a_dimension_whose_members_have_no_ledger_figure_has_no_figure(self, handler):
        """`$0.00` against a job title would read as *this discipline costs nothing*."""
        _answer(handler, [_es_bucket("alice@example.com")], [_absent()])

        with self._enrichments({"alice@example.com": _enrichment("country", "Poland")}):
            result = await handler._aggregate_user_rows_by_enrichment_field("country", None, None, None, None, None)

        assert result[0]["total_cost"] is None
        assert result[0]["spend_unavailable"] is True
        # The head count is a fact and is unaffected by what the money did.
        assert result[0]["user_count"] == 1

    @pytest.mark.asyncio
    async def test_one_member_without_a_figure_makes_the_dimension_total_a_subtotal(self, handler):
        """The group is short by one member's whole spend, and the row says so rather than looking whole."""
        _answer(handler, [_es_bucket("alice@example.com"), _es_bucket("bob@example.com")], [_absent(), _exact(25.0)])
        enrichment_map = {
            "alice@example.com": _enrichment("country", "Poland"),
            "bob@example.com": _enrichment("country", "Poland"),
        }

        with self._enrichments(enrichment_map):
            result = await handler._aggregate_user_rows_by_enrichment_field("country", None, None, None, None, None)

        assert result[0]["total_cost"] == 25.0
        assert result[0]["spend_unavailable"] is True
        assert result[0]["spend_partial"] is True
        assert result[0]["user_count"] == 2

    @pytest.mark.asyncio
    async def test_a_dimension_with_no_figure_is_ordered_last_not_at_zero(self, handler):
        _answer(handler, [_es_bucket("alice@example.com"), _es_bucket("bob@example.com")], [_absent(), _exact(0.5)])
        enrichment_map = {
            "alice@example.com": _enrichment("country", "Unknown-Cost"),
            "bob@example.com": _enrichment("country", "Cheap"),
        }

        with self._enrichments(enrichment_map):
            result = await handler._aggregate_user_rows_by_enrichment_field("country", None, None, None, None, None)

        assert [row["country"] for row in result] == ["Cheap", "Unknown-Cost"]

    @pytest.mark.asyncio
    async def test_a_measured_zero_stays_an_unqualified_zero(self, handler):
        """A measured zero from the ledger is a figure, and the row publishes it bare."""
        _answer(handler, [_es_bucket("alice@example.com")], [_exact(0.0)])

        with self._enrichments({"alice@example.com": _enrichment("country", "Poland")}):
            result = await handler._aggregate_user_rows_by_enrichment_field("country", None, None, None, None, None)

        assert result[0]["total_cost"] == 0.0
        assert result[0]["spend_unavailable"] is False
        assert result[0]["spend_partial"] is False

    @pytest.mark.asyncio
    async def test_works_with_job_title_field(self, handler):
        _answer(handler, [_es_bucket("alice@example.com")], [_exact(5.0)])

        with self._enrichments({"alice@example.com": _enrichment("job_title", "Engineer")}):
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
