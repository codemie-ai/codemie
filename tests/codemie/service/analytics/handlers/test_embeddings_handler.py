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

"""Unit tests for EmbeddingsHandler."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.embeddings_handler import EmbeddingsHandler
from codemie.service.analytics.query_pipeline import CANDIDATE_ORDER
from codemie.service.spend.coverage import Portion, SpendDimension, SpendResult, TimeRange
from codemie.service.spend.ledger import AccessScope, ReportFilter

#: The boundary the `mock_user` fixture below is entitled to: a plain member of no project at all.
#: The same scope `SecureQueryBuilder` already applied to the candidate buckets these figures foot.
_EXPECTED_SCOPE = AccessScope(unrestricted=False, user_id="test-user-id")

PERIOD_START = datetime(2026, 8, 1, tzinfo=timezone.utc)
PERIOD_END = datetime(2026, 8, 31, tzinfo=timezone.utc)
PERIOD = TimeRange(PERIOD_START, PERIOD_END)

ADA = "codemie-text-embedding-ada-002"
LARGE = "codemie-text-embedding-3-large"


@pytest.fixture
def mock_user():
    """Create mock user."""
    user = MagicMock(spec=User)
    user.project_names = []
    user.admin_project_names = []
    user.is_global_user = False
    user.is_admin_or_maintainer = False
    user.is_admin = False
    user.is_maintainer = False
    user.id = "test-user-id"
    return user


@pytest.fixture
def mock_repository():
    """Create mock repository."""
    return MagicMock(spec=MetricsElasticRepository)


@pytest.fixture
def handler(mock_user, mock_repository):
    """Create handler with mocked dependencies."""
    return EmbeddingsHandler(mock_user, mock_repository)


def _rows(*model_names: str) -> list[dict]:
    """Candidate rows as the parser leaves them: tokens and invocations, no money cell."""
    return [{"model_name": name, "total_input_tokens": 1000, "invocation_count": 5} for name in model_names]


def _answers(by_model: dict[str, SpendResult]) -> AsyncMock:
    """An `aggregate_by_dimension` stub answering per model name.

    `access` and `report_filter` are taken as **required keywords**, so a handler that stopped threading
    its boundary or the dashboard's filter fails here rather than being answered platform-wide.
    """

    async def _call(period, dimension, values, *, access, report_filter):
        assert period == PERIOD
        assert dimension is SpendDimension.MODEL
        assert access == _EXPECTED_SCOPE, "the figure must be narrowed by the caller's own boundary"
        return {value: by_model[value] for value in values if value in by_model}

    return AsyncMock(side_effect=_call)


def _patched_port(port: AsyncMock):
    return patch(
        "codemie.service.analytics.handlers.embeddings_handler.spend_service.aggregate_by_dimension",
        new=port,
    )


class TestEmbeddingsAggregation:
    """The Elasticsearch query no longer carries the money the table is sorted by."""

    def test_aggregation_asks_for_no_money_field(self, handler):
        agg = handler._build_embeddings_usage_aggregation({"match_all": {}}, 10000)

        assert "money_spent" not in json.dumps(agg)
        assert "total_cost" not in agg["aggs"]["paginated_results"]["aggs"]
        assert "total_input_tokens" in agg["aggs"]["paginated_results"]["aggs"]

    def test_candidate_buckets_are_ordered_by_activity(self, handler):
        """Elasticsearch cannot sort on a figure it does not hold, so it ranks by document count."""
        agg = handler._build_embeddings_usage_aggregation({"match_all": {}}, 10000)

        assert agg["aggs"]["paginated_results"]["terms"]["order"] == CANDIDATE_ORDER

    def test_parser_writes_no_cost_cell(self, handler):
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [{"key": ADA, "doc_count": 5, "total_input_tokens": {"value": 1200.0}}]
                }
            }
        }

        rows = handler._parse_embeddings_usage_result(result)

        assert rows == [{"model_name": ADA, "total_input_tokens": 1200, "invocation_count": 5}]


class TestEmbeddingsUsageQuery:
    """What the handler asks the pipeline for."""

    @pytest.mark.asyncio
    async def test_whole_candidate_set_is_enriched_before_paging(self, handler):
        with patch.object(
            handler._pipeline, "execute_enriched_tabular_query", new=AsyncMock(return_value={})
        ) as mock_exec:
            await handler.get_embeddings_usage()

        _, kwargs = mock_exec.call_args
        assert kwargs["row_enricher"].func == handler._enrich_embeddings_spending

    @pytest.mark.asyncio
    async def test_no_totals_aggregation_is_asked_for_because_the_footer_is_summed_over_the_rows(self, handler):
        """With an enricher the pipeline totals the enriched rows; a sibling aggregation would be
        computed and discarded, and a money entry in it would race the enricher's own footer."""
        with patch.object(
            handler._pipeline, "execute_enriched_tabular_query", new=AsyncMock(return_value={})
        ) as mock_exec:
            await handler.get_embeddings_usage()

        _, kwargs = mock_exec.call_args
        assert kwargs.get("totals_aggs") is None

    def test_money_column_is_still_published(self, handler):
        columns = handler._get_embeddings_usage_columns()

        cost = next(column for column in columns if column["id"] == "total_cost_usd")
        assert cost["format"] == "currency"


class TestEmbeddingsSpendingFromThePort:
    """The cost column is read from the spend port, once for every candidate model."""

    @pytest.mark.asyncio
    async def test_exact_figure_renders_with_clean_annotations(self, handler):
        port = _answers({ADA: SpendResult(figure=Decimal("42.5"))})

        with _patched_port(port):
            enriched = await handler._enrich_embeddings_spending(_rows(ADA), PERIOD_START, PERIOD_END)

        assert enriched.rows[0]["total_cost_usd"] == 42.5
        assert enriched.rows[0]["spend_unavailable"] is False
        assert enriched.rows[0]["spend_partial"] is False
        assert enriched.totals["total_cost_usd"] == 42.5
        assert enriched.columns == ("total_cost_usd",)

    @pytest.mark.asyncio
    async def test_port_is_read_once_for_the_whole_candidate_set(self, handler):
        """One round trip: every model shares the one window and the one caller boundary."""
        port = _answers({ADA: SpendResult(figure=Decimal("1")), LARGE: SpendResult(figure=Decimal("2"))})

        with _patched_port(port):
            await handler._enrich_embeddings_spending(_rows(ADA, LARGE), PERIOD_START, PERIOD_END)

        port.assert_awaited_once()
        args, kwargs = port.call_args
        assert args[1] is SpendDimension.MODEL
        assert args[2] == [ADA, LARGE]
        assert kwargs["access"] == _EXPECTED_SCOPE

    @pytest.mark.asyncio
    async def test_repeated_model_is_named_once(self, handler):
        """A key repeated across rows is one value to the port, and every row still gets it."""
        port = _answers({ADA: SpendResult(figure=Decimal("7"))})

        with _patched_port(port):
            enriched = await handler._enrich_embeddings_spending(_rows(ADA, ADA), PERIOD_START, PERIOD_END)

        assert port.call_args.args[2] == [ADA]
        assert [row["total_cost_usd"] for row in enriched.rows] == [7.0, 7.0]

    @pytest.mark.asyncio
    async def test_measured_zero_keeps_its_row_because_this_is_a_usage_table(self, handler):
        """A model that indexed at a measured cost of nothing still indexed: dropping the row would
        take its tokens and invocations out of the table on the strength of the money column."""
        port = _answers({ADA: SpendResult(figure=Decimal("0")), LARGE: SpendResult(figure=Decimal("3"))})

        with _patched_port(port):
            enriched = await handler._enrich_embeddings_spending(_rows(ADA, LARGE), PERIOD_START, PERIOD_END)

        assert [row["model_name"] for row in enriched.rows] == [LARGE, ADA]
        assert enriched.rows[1]["total_cost_usd"] == 0
        assert enriched.totals["total_cost_usd"] == 3

    @pytest.mark.asyncio
    async def test_rows_are_ordered_by_cost_with_an_absent_figure_last(self, handler):
        port = _answers({ADA: SpendResult.no_figure(PERIOD), LARGE: SpendResult(figure=Decimal("3"))})

        with _patched_port(port):
            enriched = await handler._enrich_embeddings_spending(_rows(ADA, LARGE), PERIOD_START, PERIOD_END)

        assert [row["model_name"] for row in enriched.rows] == [LARGE, ADA]

    @pytest.mark.asyncio
    async def test_absent_figure_keeps_the_row_and_labels_it(self, handler):
        """Absent is not zero: dropping the row would shorten the table on a figure nobody has."""
        port = _answers({ADA: SpendResult.no_figure(PERIOD)})

        with _patched_port(port):
            enriched = await handler._enrich_embeddings_spending(_rows(ADA), PERIOD_START, PERIOD_END)

        assert enriched.rows[0]["total_cost_usd"] is None
        assert enriched.rows[0]["spend_unavailable"] is True
        assert enriched.totals["total_cost_usd_spend_unavailable"] is True
        assert "total_cost_usd" not in enriched.totals

    @pytest.mark.asyncio
    async def test_partial_figure_is_carried_into_the_footer(self, handler):
        partial = SpendResult(
            figure=Decimal("10"),
            partial={SpendDimension.DATASOURCE: Portion(spend=Decimal("2"), rows=1)},
        )

        with _patched_port(_answers({ADA: partial})):
            enriched = await handler._enrich_embeddings_spending(_rows(ADA), PERIOD_START, PERIOD_END)

        assert enriched.rows[0]["spend_partial"] is True
        assert enriched.totals["total_cost_usd_spend_partial"] is True
        assert enriched.totals["total_cost_usd_spend_missing_amount"] == 2.0

    @pytest.mark.asyncio
    async def test_unreadable_window_asks_nothing_and_every_row_is_absent(self, handler):
        """A window the port cannot accept yields no figure at all, never a fabricated zero."""
        port = AsyncMock()

        with _patched_port(port):
            enriched = await handler._enrich_embeddings_spending(_rows(ADA, LARGE), PERIOD_END, PERIOD_START)

        port.assert_not_awaited()
        assert [row["total_cost_usd"] for row in enriched.rows] == [None, None]
        assert all(row["spend_unavailable"] is True for row in enriched.rows)

    @pytest.mark.asyncio
    async def test_no_candidate_rows_disturb_no_ledger(self, handler):
        port = AsyncMock()

        with _patched_port(port):
            enriched = await handler._enrich_embeddings_spending([], PERIOD_START, PERIOD_END)

        port.assert_not_awaited()
        assert enriched.rows == []
        assert enriched.totals == {}


class TestTheDashboardFilterReachesTheMoneyRead:
    """RD-D1: the ledger keys a model's rows on the model alone, so a filtered table read without the
    request's users and projects filter would answer each model's spend in every project."""

    ASKED = ReportFilter(projects=("p1",))

    @pytest.mark.asyncio
    async def test_it_is_built_from_the_request_and_handed_to_the_port(self, handler):
        with (
            patch(
                "codemie.service.analytics.handlers.embeddings_handler.report_filter_for",
                new=AsyncMock(return_value=self.ASKED),
            ) as build,
            patch.object(
                handler._pipeline, "execute_enriched_tabular_query", new=AsyncMock(return_value={})
            ) as execute,
        ):
            await handler.get_embeddings_usage(users=["u1"], projects=["p1"])

        build.assert_awaited_once_with(["u1"], ["p1"])
        enricher = execute.await_args.kwargs["row_enricher"]
        assert enricher.keywords["report_filter"] is self.ASKED

        port = _answers({})
        with _patched_port(port):
            await enricher(_rows("ada-002"), PERIOD_START, PERIOD_END)
        assert port.await_args.kwargs["report_filter"] is self.ASKED
