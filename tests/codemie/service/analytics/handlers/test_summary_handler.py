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

"""Unit tests for SummaryHandler.

The money cards are served by the spend port and the token and count cards by Elasticsearch, so the
two halves are pinned separately: the aggregation must name no money field at all, and every
currency card must carry the port group's figure together with the qualification that came with it.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.summary_handler import SummaryHandler
from codemie.service.spend.coverage import Portion, SpendDimension, SpendResult, TimeRange
from codemie.service.spend.ledger import ReportFilter, UserTerm
from codemie.service.spend.spend_group import SpendGroup

MODULE = "codemie.service.analytics.handlers.summary_handler"

#: The four cards whose figure comes from the spend port, and so the only ones that can be absent.
_MONEY_CARDS = {"total_money_spent", "platform_cost", "cli_cost", "embedding_cost"}

_PERIOD = TimeRange(datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 1, tzinfo=timezone.utc))


@pytest.fixture
def mock_user():
    """Create mock user."""
    user = MagicMock(spec=User)
    user.project_names = []
    user.admin_project_names = []
    user.is_global_user = False
    user.is_admin = False
    user.id = "test-user-id"
    return user


@pytest.fixture
def mock_repository():
    """Create mock repository."""
    return MagicMock(spec=MetricsElasticRepository)


@pytest.fixture
def handler(mock_user, mock_repository):
    """Create handler with mocked dependencies."""
    return SummaryHandler(mock_user, mock_repository)


def _card(metrics: list[dict], metric_id: str) -> dict:
    return next(metric for metric in metrics if metric["id"] == metric_id)


def _exact(figure: str) -> SpendResult:
    """A figure the port read whole."""
    return SpendResult(figure=Decimal(figure))


def _subtotal(figure: str, missing: str) -> SpendResult:
    """A figure short by a named dimension hole of a known size."""
    return SpendResult(
        figure=Decimal(figure),
        partial={SpendDimension.MODEL: Portion(spend=Decimal(missing), rows=3)},
    )


def _groups(**figures: SpendResult) -> dict[SpendGroup, SpendResult]:
    """The port's answer, keyed by group; a group left out is one the port did not answer."""
    return {SpendGroup[name.upper()]: result for name, result in figures.items()}


def _all_groups() -> dict[SpendGroup, SpendResult]:
    return _groups(total=_exact("123.46"), platform=_exact("98.46"), cli=_exact("25.00"), embedding=_exact("0.50"))


def _es_aggregations(**overrides) -> dict:
    """The token and count aggregations, in the shape Elasticsearch returns them."""
    aggregations = {
        "total_tokens_agg": {
            "input_tokens": {"value": 100000},
            "output_tokens": {"value": 50000},
            "cache_read_input_tokens": {"value": 20000},
            "cache_creation_tokens": {"value": 1000},
        },
        "unique_assistants": {"count": {"value": 100}},
        "unique_workflows": {"count": {"value": 50}},
        "embedding_metrics": {"input_tokens": {"value": 5000}},
        "cli_invoked": {"doc_count": 10},
        "mcps_invoked": {"count": {"value": 2}},
        "webhooks_invoked": {"count": {"value": 5}},
        "skills_invoked": {"count": {"value": 7}},
    }
    aggregations.update(overrides)
    return {"aggregations": aggregations}


@contextmanager
def _port(groups: dict[SpendGroup, SpendResult], report_filter: ReportFilter | None = None):
    """The spend port and the request-filter helper, both under the handler's own module."""
    resolved = report_filter if report_filter is not None else ReportFilter()
    with (
        patch(f"{MODULE}.spend_service") as service,
        patch(f"{MODULE}.report_filter_for", new=AsyncMock(return_value=resolved)) as filter_for,
    ):
        service.aggregate_by_spend_group = AsyncMock(return_value=groups)
        yield service, filter_for


class TestGetSummaries:
    """Tests for get_summaries method."""

    @pytest.mark.asyncio
    async def test_money_cards_come_from_the_port_and_token_cards_from_elasticsearch(self, handler, mock_repository):
        """The two halves of the response arrive from two sources and neither serves the other's card."""
        mock_repository.execute_aggregation_query.side_effect = [
            {"aggregations": {"unique_users": {"value": 12}}},
            _es_aggregations(),
        ]

        with _port(_all_groups()):
            result = await handler.get_summaries(time_period="last_30_days", users=["user1"])

        metrics = result["data"]["metrics"]
        assert _card(metrics, "total_money_spent")["value"] == 123.46
        assert _card(metrics, "platform_cost")["value"] == 98.46
        assert _card(metrics, "cli_cost")["value"] == 25.00
        assert _card(metrics, "embedding_cost")["value"] == 0.50
        assert _card(metrics, "total_input_tokens")["value"] == 100000
        # Two Elasticsearch round trips, the unique-users query and the main one - the money added
        # none, because it does not come from Elasticsearch at all.
        assert mock_repository.execute_aggregation_query.call_count == 2

    @pytest.mark.asyncio
    async def test_the_port_is_read_once_with_the_callers_boundary_and_the_requests_filter(
        self, handler, mock_repository
    ):
        """One read per request, narrowed to the caller and filtered to what the screen asked about.

        The boundary is the pipeline's own `access_scope`: a handler deriving a second one could
        drift from the query the rows beside these cards came from.
        """
        mock_repository.execute_aggregation_query.side_effect = [
            {"aggregations": {"unique_users": {"value": 1}}},
            _es_aggregations(),
        ]
        requested = ReportFilter(users=(UserTerm(user_id="u1", aliases=frozenset({"alice"})),), projects=("alpha",))

        start = datetime(2026, 8, 1, tzinfo=timezone.utc)
        end = datetime(2026, 8, 8, tzinfo=timezone.utc)
        with _port(_all_groups(), report_filter=requested) as (service, filter_for):
            await handler.get_summaries(start_date=start, end_date=end, users=["u1"], projects=["alpha"])

        service.aggregate_by_spend_group.assert_awaited_once()
        call = service.aggregate_by_spend_group.await_args
        assert call.args[0] == TimeRange(start, end), "the request's own window, never a default or a budget period"
        assert call.kwargs["access"] == handler._pipeline.access_scope
        assert call.kwargs["report_filter"] is requested
        filter_for.assert_awaited_once_with(["u1"], ["alpha"])

    @pytest.mark.asyncio
    async def test_a_window_the_port_cannot_accept_asks_nothing_and_renders_every_money_card_absent(
        self, handler, mock_repository
    ):
        """An empty window is not a window: the port stays unasked and no figure is fabricated for it."""
        instant = datetime.now(timezone.utc) - timedelta(days=1)
        mock_repository.execute_aggregation_query.side_effect = [
            {"aggregations": {"unique_users": {"value": 0}}},
            _es_aggregations(),
        ]

        with _port(_all_groups()) as (service, filter_for):
            result = await handler.get_summaries(start_date=instant, end_date=instant)

        service.aggregate_by_spend_group.assert_not_awaited()
        filter_for.assert_not_awaited()
        for metric_id in _MONEY_CARDS:
            card = _card(result["data"]["metrics"], metric_id)
            assert card["value"] is None
            assert card["spend_unavailable"] is True
        # The rest of the screen is unaffected: the tokens were counted and are published.
        assert _card(result["data"]["metrics"], "total_tokens")["value"] == 171000

    @pytest.mark.asyncio
    async def test_get_summaries_applies_client_source_filter(self, handler, mock_repository):
        """Verify client_source is threaded into the ES query as a term filter on the keyword field."""
        mock_repository.execute_aggregation_query.side_effect = [
            {"aggregations": {"unique_users": {"value": 0}}},
            _es_aggregations(),
        ]

        with _port(_all_groups()):
            await handler.get_summaries(time_period="last_7_days", client_source="teams")

        # Assert - at least one of the executed queries carries the client_source term filter
        found = False
        for call in mock_repository.execute_aggregation_query.call_args_list:
            body = call.args[0] if call.args else call.kwargs.get("query", {})
            if "attributes.client_source.keyword" in str(body) and "teams" in str(body):
                found = True
                break
        assert found, "Expected attributes.client_source.keyword=teams term filter in at least one query"

    @pytest.mark.asyncio
    async def test_a_client_source_filter_asks_the_port_nothing_and_renders_every_money_card_absent(
        self, handler, mock_repository
    ):
        """The ledger records no client, so its figure would be the whole platform's under one client's label."""
        mock_repository.execute_aggregation_query.side_effect = [
            {"aggregations": {"unique_users": {"value": 0}}},
            _es_aggregations(),
        ]

        with _port(_all_groups()) as (service, filter_for):
            result = await handler.get_summaries(time_period="last_7_days", client_source="teams")

        service.aggregate_by_spend_group.assert_not_awaited()
        filter_for.assert_not_awaited()
        for metric_id in _MONEY_CARDS:
            card = _card(result["data"]["metrics"], metric_id)
            assert card["value"] is None
            assert card["spend_unavailable"] is True
        # The token cards keep the narrowing Elasticsearch applied.
        assert _card(result["data"]["metrics"], "total_tokens")["value"] == 171000


class TestAggregationBuilder:
    """Tests for aggregation builder methods."""

    def test_build_summaries_aggregation_structure(self, handler):
        """Verify aggregation structure for ES query (tokens, counts and invocations)."""
        test_query = {"bool": {"filter": []}}

        agg_body = handler._build_summaries_aggregation(test_query)

        assert agg_body["query"] == test_query
        assert agg_body["size"] == 0  # Aggregation only, no docs
        assert set(agg_body["aggs"]) == {
            "total_tokens_agg",
            "unique_assistants",
            "unique_workflows",
            "embedding_metrics",
            "cli_invoked",
            "mcps_invoked",
            "webhooks_invoked",
            "skills_invoked",
        }

        tokens_aggs = agg_body["aggs"]["total_tokens_agg"]["aggs"]
        assert tokens_aggs["input_tokens"]["sum"]["field"] == "attributes.input_tokens"
        assert tokens_aggs["cache_read_input_tokens"]["sum"]["field"] == "attributes.cache_read_input_tokens"
        assert tokens_aggs["output_tokens"]["sum"]["field"] == "attributes.output_tokens"
        assert tokens_aggs["cache_creation_tokens"]["sum"]["field"] == "attributes.cache_creation_tokens"

        # The embedding bucket survives for its token count alone.
        assert set(agg_body["aggs"]["embedding_metrics"]["aggs"]) == {"input_tokens"}

    def test_the_aggregation_names_no_money_field(self, handler):
        """Every currency card is the port's, so this query has no business summing money.

        A money sub-aggregation left behind would be a second figure for the same card, floored
        differently from the port's and free to disagree with it.
        """
        agg_body = handler._build_summaries_aggregation({"bool": {"filter": []}})

        assert "money_spent" not in json.dumps(agg_body)


class TestMetricsBuilder:
    """Tests for metrics builder methods."""

    def test_token_and_count_cards_are_parsed_from_elasticsearch(self, handler):
        """The half of the screen the aggregation still answers."""
        metrics = handler._build_summaries_metrics(_es_aggregations(), 12, _all_groups())

        assert _card(metrics, "total_input_tokens")["value"] == 100000
        assert _card(metrics, "total_cached_input_tokens")["value"] == 20000
        assert _card(metrics, "total_output_tokens")["value"] == 50000
        assert _card(metrics, "total_cache_creation_tokens")["value"] == 1000
        assert _card(metrics, "total_tokens")["value"] == 171000
        assert _card(metrics, "unique_assistants_invoked")["value"] == 100
        assert _card(metrics, "unique_workflows_invoked")["value"] == 50
        assert _card(metrics, "cli_invoked")["value"] == 10
        assert _card(metrics, "mcps_invoked")["value"] == 2
        assert _card(metrics, "webhooks_invoked")["value"] == 5
        assert _card(metrics, "skills_invoked")["value"] == 7

    def test_metrics_count_is_15(self, handler):
        """Verify response contains exactly 15 metrics, four of them money."""
        metrics = handler._build_summaries_metrics(_es_aggregations(), 2, _all_groups())

        assert len(metrics) == 15
        assert _MONEY_CARDS.issubset({metric["id"] for metric in metrics})

    def test_money_cards_carry_the_ports_figure_and_its_qualification(self, handler):
        """A subtotal is published as a subtotal, with the hole the port measured beside it."""
        groups = _groups(
            total=_subtotal("123.46", "4.50"),
            platform=_exact("98.46"),
            cli=_exact("25.00"),
            embedding=_exact("0.50"),
        )

        metrics = handler._build_summaries_metrics(_es_aggregations(), 1, groups)

        total = _card(metrics, "total_money_spent")
        assert total["value"] == 123.46
        assert (total["spend_partial"], total["spend_unavailable"], total["spend_missing_amount"]) == (
            True,
            False,
            4.50,
        )

        # A figure the port read whole is the one state a currency card may render bare.
        platform = _card(metrics, "platform_cost")
        assert (platform["spend_partial"], platform["spend_unavailable"], platform["spend_missing_amount"]) == (
            False,
            False,
            None,
        )

    def test_a_group_the_port_did_not_answer_renders_absent(self, handler):
        """No answer is no figure. A zero here would claim that part of the window cost nothing."""
        groups = _groups(total=_exact("123.46"), platform=_exact("98.46"), cli=_exact("25.00"))

        metrics = handler._build_summaries_metrics(_es_aggregations(), 1, groups)

        embedding = _card(metrics, "embedding_cost")
        assert embedding["value"] is None
        assert embedding["spend_unavailable"] is True
        assert embedding["spend_partial"] is False

    def test_a_group_the_port_could_not_read_renders_absent(self, handler):
        """The port's own no-figure answer reaches the card as absence, not as a number."""
        groups = _all_groups() | {SpendGroup.CLI: SpendResult.no_figure(_PERIOD)}

        metrics = handler._build_summaries_metrics(_es_aggregations(), 1, groups)

        cli = _card(metrics, "cli_cost")
        assert cli["value"] is None
        assert cli["spend_unavailable"] is True

    def test_a_measured_zero_stays_a_zero_and_stays_unqualified(self, handler):
        """The window in which nothing was spent is a fact, and the only zero a card may show."""
        groups = _all_groups() | {SpendGroup.CLI: _exact("0")}

        metrics = handler._build_summaries_metrics(_es_aggregations(), 1, groups)

        cli = _card(metrics, "cli_cost")
        assert cli["value"] == 0.0
        assert (cli["spend_unavailable"], cli["spend_partial"], cli["spend_missing_amount"]) == (False, False, None)

    def test_embedding_cost_is_published_as_a_qualified_card(self, handler):
        """The embedding figure is a card of its own now, and carries the same annotation triple."""
        groups = _all_groups() | {SpendGroup.EMBEDDING: _subtotal("0.50", "0.02")}

        metrics = handler._build_summaries_metrics(_es_aggregations(), 1, groups)

        embedding = _card(metrics, "embedding_cost")
        assert embedding["label"] == "Embedding Cost"
        assert embedding["format"] == "currency"
        assert embedding["value"] == 0.50
        assert embedding["spend_partial"] is True
        assert embedding["spend_missing_amount"] == 0.02
        # It sits beside the CLI card, where the screen expects the third slice of the total.
        ids = [metric["id"] for metric in metrics]
        assert ids.index("embedding_cost") == ids.index("cli_cost") + 1

    def test_platform_cost_card_metadata(self, handler):
        """The platform card says what it now covers: everything but CLI, embeddings included."""
        platform = _card(handler._build_summaries_metrics(_es_aggregations(), 1, _all_groups()), "platform_cost")

        assert platform["type"] == "number"
        assert platform["format"] == "currency"
        assert "CLI" in platform["description"]
        assert "embeddings" in platform["description"]

    def test_missing_aggregations_leave_counts_at_zero_and_money_untouched(self, handler):
        """Elasticsearch returning nothing empties the counts and cannot empty a money card."""
        metrics = handler._build_summaries_metrics({"aggregations": {}}, 0, _all_groups())

        assert len(metrics) == 15
        for metric in metrics:
            if metric["id"] in _MONEY_CARDS:
                assert metric["value"] is not None
            else:
                assert metric["value"] == 0

    def test_no_groups_at_all_renders_every_money_card_absent(self, handler):
        """The shape a window the port was never asked about produces."""
        metrics = handler._build_summaries_metrics(_es_aggregations(), 1, {})

        for metric_id in _MONEY_CARDS:
            card = _card(metrics, metric_id)
            assert card["value"] is None
            assert card["spend_unavailable"] is True

    def test_the_log_line_qualifies_every_money_figure(self, handler):
        """A log is a surface too: an absent figure printed as $0.00 is the same false claim there."""
        groups = _groups(total=_subtotal("123.46", "4.50"), platform=_exact("98.46"), cli=_exact("25.00"))

        with patch(f"{MODULE}.logger") as mock_logger:
            handler._build_summaries_metrics(_es_aggregations(), 1, groups)

        line = mock_logger.debug.call_args.args[0]
        assert "total_money_spent=$123.46 (subtotal)" in line
        assert "cli_cost=$25.00" in line
        assert "embedding_cost=unavailable" in line
