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

"""Unit tests for WorkflowHandler.

Both of its tables group on `attributes.workflow_name.keyword` - the only key every production
workflow document carries - and read their money in two legs joined at the coverage floor: the
bucket's own qualified Elasticsearch sum below `F`, the spend port at or above it, keyed on the id a
name resolves to when it resolves to exactly one. The tests are organised around the three things
that costs and buys: which rows exist, where each leg's money comes from, and what a name that is
not one live workflow renders.
"""

from __future__ import annotations

import contextlib
import json
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs import config
from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.money_coverage import SETTLED_AGG, UNSETTLED_AGG
from codemie.service.analytics.handlers.project_handler import ProjectHandler
from codemie.service.analytics.handlers.workflow_handler import (
    INPUT_TOKENS_FIELD,
    MONEY_SPENT_FIELD,
    PRE_FLOOR_MONEY_BUCKET,
    PRE_FLOOR_MONEY_KEY,
    PRE_FLOOR_MONEY_SUM,
    TIMESTAMP_FIELD,
    WORKFLOW_NAME_KEYWORD_FIELD,
    WorkflowHandler,
)
from codemie.service.spend.coverage import (
    DEFAULT_EMISSION_LAG,
    LedgerCoverage,
    Portion,
    SpendDimension,
    SpendResult,
    TimeRange,
)
from codemie.service.spend.ledger import AccessScope, ReportFilter
from codemie.service.spend.ledger_registry import register_ledger_coverage, reset_ledgers
from codemie.service.spend.rendering import RenderedSpend, render_spend

WORKFLOW_DIRECTORY = "codemie.core.workflow_models.workflow_config.WorkflowConfig.get_ids_by_names"
PORT = "codemie.service.analytics.handlers.workflow_handler.spend_service.aggregate_by_dimension"
REGISTERED_COVERAGE = "codemie.service.analytics.handlers.workflow_handler.ledger_coverage"

PERIOD_START = datetime(2026, 8, 1, tzinfo=timezone.utc)
PERIOD_END = datetime(2026, 8, 31, tzinfo=timezone.utc)
PERIOD = TimeRange(PERIOD_START, PERIOD_END)

#: A floor sitting inside `PERIOD`, so the same window is crossing, wholly below or wholly above
#: depending only on which floor a test publishes.
FLOOR = datetime(2026, 8, 15, tzinfo=timezone.utc)
AT_OR_ABOVE_FLOOR_LEG = TimeRange(FLOOR, PERIOD_END)
#: A floor above the whole of `PERIOD`: every instant in the window is below it.
BELOW_FLOOR_ONLY = datetime(2026, 9, 1, tzinfo=timezone.utc)
#: A floor below the whole of `PERIOD`: every instant in the window is at or above it.
ABOVE_FLOOR_ONLY = datetime(2026, 7, 1, tzinfo=timezone.utc)

#: The boundary the `mock_user` fixture below is entitled to: a plain member of no project at all.
#: **Restricted with an empty project set, which matches nothing rather than everything** - the rule
#: `AccessScope` holds and `SecureQueryBuilder`'s `minimum_should_match=0` already applied to the rows
#: this table's figures foot.
_EXPECTED_SCOPE = AccessScope(unrestricted=False, user_id="test-user-id")


@pytest.fixture
def mock_user():
    user = MagicMock(spec=User)
    user.project_names = []
    user.admin_project_names = []
    user.is_global_user = False
    user.is_admin = False
    user.id = "test-user-id"
    return user


@pytest.fixture
def mock_repository():
    return MagicMock(spec=MetricsElasticRepository)


@pytest.fixture
def handler(mock_user, mock_repository):
    return WorkflowHandler(mock_user, mock_repository)


def _money_bucket(*, money: float | None, settled: int | None = 1, unsettled: int = 0) -> dict:
    """A pre-floor money sub-aggregation as Elasticsearch returns it."""
    return {
        PRE_FLOOR_MONEY_BUCKET: {
            PRE_FLOOR_MONEY_SUM: {"value": money},
            SETTLED_AGG: {"value": settled},
            UNSETTLED_AGG: {"doc_count": unsettled},
        }
    }


def _workflows_bucket(name: str, *, runs: int = 4, pre_floor: dict | None = None) -> dict:
    """One `get_workflows` bucket, keyed on the name the aggregation groups by."""
    return {
        "key": name,
        "unique_executions": {"value": runs},
        "successful": {"unique_count": {"value": runs}},
        "failed": {"unique_count": {"value": 0}},
        "aborted": {"unique_count": {"value": 0}},
        "total_tokens": {"value": 1000},
        "avg_time": {"value": 1.5},
        "unique_users": {"value": 2},
        "last_run": {"value_as_string": "2026-08-20T10:00:00.000Z"},
        **(pre_floor or {}),
    }


def _top_usage_bucket(name: str, *, invocations: int = 7, pre_floor: dict | None = None) -> dict:
    """One `get_top_workflow_usage` bucket, keyed on the name the aggregation groups by."""
    return {
        "key": name,
        "1-bucket": {"doc_count": invocations},
        "3-bucket": {"3-metric": {"value": 3}},
        "4-bucket": {"4-metric": {"top": [{"metrics": {"attributes.user_email.keyword": "someone@example.com"}}]}},
        **(pre_floor or {}),
    }


def _as_result(buckets: list[dict]) -> dict:
    return {"aggregations": {"paginated_results": {"buckets": buckets}}}


def _directory(**ids_by_name: str | list[str]):
    """The workflow directory, answering with every LIVE id carrying each name."""
    pairs = [(one_id, name) for name, ids in ids_by_name.items() for one_id in ([ids] if isinstance(ids, str) else ids)]
    return patch(WORKFLOW_DIRECTORY, return_value=pairs)


def _patched_port(by_workflow_id: dict[str, SpendResult], *, expect_period: TimeRange | None = PERIOD):
    """The spend port, answering per workflow id over the leg it is handed.

    **`access` is asserted on every call rather than merely accepted.** The port takes it as a
    required keyword precisely so a consumer that forgets it raises, and a double that accepted it
    loosely would let a handler pass one table's boundary and drop another's - so every read routed
    through here is held to carrying the fixture user's own scope. `report_filter` is required here for
    the same reason: a call site that stopped threading the dashboard's filter fails rather than passes.
    """

    async def _call(period, dimension, values, *, access, report_filter):
        if expect_period is not None:
            assert period == expect_period
        assert dimension is SpendDimension.WORKFLOW
        assert access == _EXPECTED_SCOPE, "the figure must be narrowed by the caller's own boundary"
        return {value: by_workflow_id[value] for value in values if value in by_workflow_id}

    return patch(PORT, new=AsyncMock(side_effect=_call))


@contextlib.contextmanager
def _cutover_at(floor: datetime | None, *, enabled: bool = True):
    """Publish `floor` as the **registered** coverage floor for the body of a test.

    Patched at `ledger_coverage()` rather than by setting config alone, because that is the whole of
    what this handler is required to read. `floor=None` is the unactivated ledger: nothing splits.
    """
    coverage = LedgerCoverage(coverage_floor=floor, ready=floor is not None)
    with (
        patch(REGISTERED_COVERAGE, return_value=coverage),
        patch.object(config, "SPEND_LEDGER_LEGACY_BELOW_FLOOR", enabled),
    ):
        yield


def _candidate(name: str, *, total_runs: int = 4, pre_floor: RenderedSpend | None = None) -> dict:
    """A parsed `get_workflows` row as the enricher receives it."""
    return {
        "workflow_name": name,
        "total_runs": total_runs,
        PRE_FLOOR_MONEY_KEY: render_spend(None) if pre_floor is None else pre_floor,
    }


def _measured(money: float) -> RenderedSpend:
    return RenderedSpend(figure=money, unavailable=False, partial=False, missing_amount=None)


class TestTheAggregationsGroupOnTheNameAndCarryMoneyOnlyBelowTheFloor:
    """AD-14/AD-29 at the metric store: Elasticsearch supplies runs, tokens and labels, and its own
    money only where the ledger has none - inside a bucket bounded at `F`."""

    def test_the_workflows_aggregation_groups_on_the_name(self, handler):
        agg = handler._build_workflows_aggregation({"bool": {"filter": []}}, fetch_size=100)["aggs"][
            "paginated_results"
        ]

        assert agg["terms"]["field"] == WORKFLOW_NAME_KEYWORD_FIELD
        assert agg["terms"]["size"] == 100
        # Candidate order stays document count: it is the only activity proxy Elasticsearch still
        # holds now that the money it could have ranked by lives in the ledger.
        assert agg["terms"]["order"] == {"_count": "desc"}

    def test_without_a_floor_the_workflows_aggregation_names_no_money_field_at_all(self, handler):
        """Asserted on the serialised body, because that is where the rule can be broken silently:
        a re-added `sum` over `attributes.money_spent` reaches a screen as a plausible number, and
        only the absence of the token itself proves the second source is gone."""
        body = json.dumps(handler._build_workflows_aggregation({"bool": {"filter": []}}, fetch_size=100))

        assert "money_spent" not in body
        assert "total_cost" not in body
        assert "workflow_id" not in body
        # Everything Elasticsearch is still the authority for is untouched.
        for kept in ("unique_executions", "successful", "failed", "aborted", "total_tokens", "avg_time", "last_run"):
            assert kept in body

    def test_with_a_floor_the_only_money_sum_sits_in_a_bucket_bounded_below_it(self, handler):
        sub_aggs = handler._build_workflows_aggregation({"bool": {"filter": []}}, fetch_size=100, pre_floor=FLOOR)[
            "aggs"
        ]["paginated_results"]["aggs"]
        money = sub_aggs[PRE_FLOOR_MONEY_BUCKET]

        assert money["filter"] == {
            "range": {TIMESTAMP_FIELD: {"lt": FLOOR.isoformat(), "format": "strict_date_optional_time"}}
        }
        assert money["aggs"][PRE_FLOOR_MONEY_SUM] == {"sum": {"field": MONEY_SPENT_FIELD}}
        # The sum says whether it is whole, counting only documents that billed a model as unsettled.
        assert money["aggs"][SETTLED_AGG] == {"value_count": {"field": MONEY_SPENT_FIELD}}
        assert money["aggs"][UNSETTLED_AGG]["filter"]["bool"]["filter"] == [{"exists": {"field": INPUT_TOKENS_FIELD}}]
        # And it is the only money anywhere in the body.
        body = json.dumps(sub_aggs)
        assert body.count(MONEY_SPENT_FIELD) == 3  # the sum, the settled count, the unsettled exists

    def test_the_floor_instant_belongs_to_the_at_or_above_leg(self, handler):
        """`lt`, never `lte`: adjacent half-open ranges tile exactly, and the boundary row lands once."""
        money = handler._build_workflows_aggregation({"bool": {"filter": []}}, fetch_size=100, pre_floor=FLOOR)["aggs"][
            "paginated_results"
        ]["aggs"][PRE_FLOOR_MONEY_BUCKET]

        assert "lt" in money["filter"]["range"][TIMESTAMP_FIELD]
        assert "lte" not in money["filter"]["range"][TIMESTAMP_FIELD]

    def test_the_top_usage_aggregation_groups_on_the_name_and_names_no_money_without_a_floor(self, handler):
        agg_body = handler._build_top_workflow_usage_aggregation(
            {"bool": {"filter": []}}, fetch_size=100, metric_name_field="metric_name.keyword"
        )
        agg = agg_body["aggs"]["paginated_results"]

        assert agg["terms"]["field"] == WORKFLOW_NAME_KEYWORD_FIELD
        assert agg["terms"]["order"] == {"1-bucket": "desc"}
        assert "money_spent" not in json.dumps(agg_body)
        # `2-bucket` held nothing but the whole-window money sum, so it is gone rather than emptied.
        assert "2-bucket" not in agg["aggs"]
        assert set(agg["aggs"]) == {"1-bucket", "3-bucket", "4-bucket"}

    def test_the_top_usage_pre_floor_money_sits_behind_the_execution_metric_term(self, handler):
        """Its query carries no metric filter, so the bucket holds every document naming the workflow;
        the money this table always printed sat behind the execution-metric term, and so must this."""
        agg = handler._build_top_workflow_usage_aggregation(
            {"bool": {"filter": []}}, fetch_size=100, metric_name_field="metric_name.keyword", pre_floor=FLOOR
        )["aggs"]["paginated_results"]["aggs"]
        money = agg[PRE_FLOOR_MONEY_BUCKET]

        assert money["filter"]["bool"]["filter"][0] == {
            "range": {TIMESTAMP_FIELD: {"lt": FLOOR.isoformat(), "format": "strict_date_optional_time"}}
        }
        assert money["filter"]["bool"]["filter"][1] == agg["1-bucket"]["filter"]
        assert money["aggs"][PRE_FLOOR_MONEY_SUM] == {"sum": {"field": MONEY_SPENT_FIELD}}


class TestRowsKeyOnTheName:
    """Which rows exist, and what each carries to the enricher."""

    def test_a_row_keys_on_the_name_and_carries_no_id(self, handler):
        rows = handler._parse_workflows_result(_as_result([_workflows_bucket("Nightly Report")]))

        assert len(rows) == 1
        assert rows[0]["workflow_name"] == "Nightly Report"
        assert "workflow_id" not in rows[0]

    def test_a_renamed_workflow_is_two_rows(self, handler):
        """The accepted cost of the name key: the metric store holds no id for the old name's rows."""
        rows = handler._parse_workflows_result(
            _as_result([_workflows_bucket("Name Before The Rename"), _workflows_bucket("Name After The Rename")])
        )

        assert [row["workflow_name"] for row in rows] == ["Name Before The Rename", "Name After The Rename"]

    def test_two_workflows_sharing_one_name_are_one_row(self, handler):
        """Elasticsearch formed one bucket for the shared name; the parser reports what was formed."""
        rows = handler._parse_workflows_result(_as_result([_workflows_bucket("Deploy")]))

        assert [row["workflow_name"] for row in rows] == ["Deploy"]

    def test_the_parser_makes_no_directory_query(self, handler):
        """The directory is consulted only where an at-or-above leg exists, which the enricher decides."""
        with patch(WORKFLOW_DIRECTORY) as directory:
            handler._parse_workflows_result(_as_result([_workflows_bucket("Nightly Report")]))

        directory.assert_not_called()

    def test_the_parser_writes_neither_cost_column_and_carries_the_pre_floor_money_privately(self, handler):
        """A placeholder for a figure nobody has read yet is the fabricated zero this unit removes."""
        rows = handler._parse_workflows_result(
            _as_result([_workflows_bucket("Nightly Report", pre_floor=_money_bucket(money=12.5))])
        )

        assert "total_cost_usd" not in rows[0]
        assert "avg_cost_usd" not in rows[0]
        assert rows[0][PRE_FLOOR_MONEY_KEY] == _measured(12.5)

    def test_a_bucket_without_a_money_bucket_carries_an_absent_figure_never_a_zero(self, handler):
        rows = handler._parse_workflows_result(_as_result([_workflows_bucket("Nightly Report")]))

        assert rows[0][PRE_FLOOR_MONEY_KEY].figure is None
        assert rows[0][PRE_FLOOR_MONEY_KEY].unavailable is True

    def test_a_bucket_where_nothing_settled_is_absent_and_an_unsettled_cost_is_partial(self, handler):
        nothing_settled = _workflows_bucket("a", pre_floor=_money_bucket(money=0.0, settled=0))
        one_outstanding = _workflows_bucket("b", pre_floor=_money_bucket(money=3.0, settled=2, unsettled=1))
        rows = handler._parse_workflows_result(_as_result([nothing_settled, one_outstanding]))

        assert rows[0][PRE_FLOOR_MONEY_KEY].figure is None
        assert rows[1][PRE_FLOOR_MONEY_KEY].figure == 3.0
        assert rows[1][PRE_FLOOR_MONEY_KEY].partial is True

    def test_the_top_usage_parser_keys_on_the_name_and_writes_no_money(self, handler):
        rows = handler._parse_top_workflow_usage_result(
            _as_result([_top_usage_bucket("Nightly Report", pre_floor=_money_bucket(money=4.0))])
        )

        assert rows[0]["workflow_name"] == "Nightly Report"
        assert "workflow_id" not in rows[0]
        assert rows[0]["invocations"] == 7
        assert "money_spent" not in rows[0]
        assert rows[0][PRE_FLOOR_MONEY_KEY] == _measured(4.0)


class TestWorkflowsMoneyComesFromTheSpendPort:
    """AD-1/AD-14 over a window wholly at or above `F`: the figure and the ratio come from the ledger,
    keyed on the one live id each name resolves to, and every absence stays absent."""

    @pytest.mark.asyncio
    async def test_the_port_is_keyed_on_the_resolved_ids_not_the_names(self, handler):
        rows = [_candidate("Nightly Report"), _candidate("Deploy")]

        with _directory(**{"Nightly Report": "wf-42", "Deploy": "wf-7"}), _patched_port({}) as port:
            await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END)

        assert port.await_args.args[2] == ["wf-42", "wf-7"]
        # A name is mutable, so asking about it would name a bucket the ledger never formed.
        assert "Nightly Report" not in port.await_args.args[2]

    @pytest.mark.asyncio
    async def test_the_private_key_is_consumed_and_never_reaches_the_response(self, handler):
        rows = [_candidate("Nightly Report")]

        with _directory(**{"Nightly Report": "wf-42"}), _patched_port({"wf-42": SpendResult(figure=Decimal("3"))}):
            enriched = await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END)

        assert PRE_FLOOR_MONEY_KEY not in enriched.rows[0]
        assert "workflow_id" not in enriched.rows[0]
        assert enriched.rows[0]["workflow_name"] == "Nightly Report"

    @pytest.mark.asyncio
    async def test_an_exact_figure_renders_the_ratio_and_clean_annotations(self, handler):
        rows = [_candidate("Nightly Report", total_runs=8)]

        with _directory(**{"Nightly Report": "wf-42"}), _patched_port({"wf-42": SpendResult(figure=Decimal("12.34"))}):
            enriched = await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END)

        row = enriched.rows[0]
        assert row["total_cost_usd"] == 12.34
        assert row["avg_cost_usd"] == 1.5425
        assert row["spend_unavailable"] is False
        assert row["spend_partial"] is False
        assert row["spend_missing_amount"] is None
        assert enriched.totals["total_cost_usd"] == 12.34

    @pytest.mark.asyncio
    async def test_a_measured_zero_renders_as_a_zero_and_the_row_survives(self, handler):
        """A spending table drops a zero row; this one must not - it is an activity record."""
        rows = [_candidate("Nightly Report")]

        with _directory(**{"Nightly Report": "wf-42"}), _patched_port({"wf-42": SpendResult(figure=Decimal("0"))}):
            enriched = await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END)

        assert len(enriched.rows) == 1
        assert enriched.rows[0]["total_cost_usd"] == 0.0
        assert enriched.rows[0]["avg_cost_usd"] == 0.0
        assert enriched.rows[0]["spend_unavailable"] is False

    @pytest.mark.asyncio
    async def test_an_absent_figure_renders_no_number_anywhere_and_says_so(self, handler):
        """AD-23: no figure exists, so nothing numeric is emitted for it - not the cost, not the ratio."""
        rows = [_candidate("Nightly Report")]

        with _directory(**{"Nightly Report": "wf-42"}), _patched_port({"wf-42": SpendResult.no_figure(PERIOD)}):
            enriched = await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END)

        row = enriched.rows[0]
        assert row["total_cost_usd"] is None
        assert row["avg_cost_usd"] is None
        assert row["spend_unavailable"] is True
        # Not a single fabricated zero in the money the row carries; booleans are excluded because
        # `False == 0.0` in Python.
        ledger_filled = ["total_cost_usd", "avg_cost_usd", "spend_missing_amount"]
        assert 0.0 not in [row[cell] for cell in ledger_filled if not isinstance(row[cell], bool)]
        assert "total_cost_usd" not in enriched.totals
        assert enriched.totals["total_cost_usd_spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_a_partial_figure_renders_its_number_with_the_qualification(self, handler):
        rows = [_candidate("Nightly Report", total_runs=4)]
        with_hole = SpendResult(
            figure=Decimal("8"),
            partial={SpendDimension.IDENTITY_BRIDGE: Portion(spend=None, rows=None)},
        )

        with _directory(**{"Nightly Report": "wf-42"}), _patched_port({"wf-42": with_hole}):
            enriched = await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END)

        row = enriched.rows[0]
        assert row["total_cost_usd"] == 8.0
        # The ratio is computable and inherits the row's qualification rather than carrying its own.
        assert row["avg_cost_usd"] == 2.0
        assert row["spend_partial"] is True
        assert row["spend_missing_amount"] is None

    @pytest.mark.asyncio
    async def test_no_runs_yields_no_ratio_rather_than_a_zero(self, handler):
        """`avg_cost = total_cost / total_runs` used to answer `0` here, which is a claim, not a gap."""
        rows = [_candidate("Nightly Report", total_runs=0)]

        with _directory(**{"Nightly Report": "wf-42"}), _patched_port({"wf-42": SpendResult(figure=Decimal("5"))}):
            enriched = await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END)

        assert enriched.rows[0]["total_cost_usd"] == 5.0
        assert enriched.rows[0]["avg_cost_usd"] is None

    @pytest.mark.asyncio
    async def test_the_money_order_puts_an_absent_figure_last_rather_than_at_zero(self, handler):
        """A **measured zero** stands between the figures and the absence, which is what makes this
        assertion falsifiable: ranking an absent figure as `0.0` ties it with the measured zero, and
        only their relative order tells the two apart."""
        rows = [_candidate("absent"), _candidate("zero"), _candidate("small"), _candidate("large")]
        answers = {
            "id-absent": SpendResult.no_figure(PERIOD),
            "id-zero": SpendResult(figure=Decimal("0")),
            "id-small": SpendResult(figure=Decimal("1")),
            "id-large": SpendResult(figure=Decimal("90")),
        }

        with (
            _directory(absent="id-absent", zero="id-zero", small="id-small", large="id-large"),
            _patched_port(answers),
        ):
            enriched = await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END)

        assert [row["workflow_name"] for row in enriched.rows] == ["large", "small", "zero", "absent"]
        # The footer is short by the row nobody has a figure for, and says so rather than looking whole.
        assert enriched.totals["total_cost_usd"] == 91.0
        assert enriched.totals["total_cost_usd_spend_partial"] is True

    @pytest.mark.asyncio
    async def test_a_window_the_port_cannot_read_asks_nothing_and_fabricates_nothing(self, handler):
        rows = [_candidate("Nightly Report", pre_floor=_measured(7.0))]

        with (
            _directory(**{"Nightly Report": "wf-42"}) as directory,
            _patched_port({"wf-42": SpendResult(figure=Decimal("7"))}) as port,
        ):
            enriched = await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_START)

        port.assert_not_awaited()
        directory.assert_not_called()
        assert enriched.rows[0]["total_cost_usd"] is None
        assert enriched.rows[0]["avg_cost_usd"] is None
        assert enriched.rows[0]["spend_unavailable"] is True
        assert PRE_FLOOR_MONEY_KEY not in enriched.rows[0]

    @pytest.mark.asyncio
    async def test_the_enricher_owns_the_money_column_so_one_writer_fills_the_footer(self, handler):
        rows = [_candidate("Nightly Report")]

        with _directory(**{"Nightly Report": "wf-42"}), _patched_port({"wf-42": SpendResult(figure=Decimal("2"))}):
            enriched = await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END)

        # The per-run ratio is not named: `TotalsCalculator` already excludes an `avg_*` column from
        # every footer by pattern, so claiming it here would put a second writer on one key.
        assert enriched.columns == ("total_cost_usd",)

    def test_the_advertised_money_columns_are_unchanged(self, handler):
        """The column ids and labels the UI reads are the ones this surface always had."""
        money = {
            column["id"]: column["label"]
            for column in handler._get_workflows_columns()
            if column["id"] in {"total_cost_usd", "avg_cost_usd"}
        }

        assert money == {"total_cost_usd": "Total Cost ($)", "avg_cost_usd": "Avg Cost ($)"}
        assert [column["id"] for column in handler._get_workflows_columns()][0] == "workflow_name"


class TestNameResolution:
    """AD-29 at the seam between a name and the ledger's id: exactly one live workflow, or nothing."""

    @pytest.mark.asyncio
    async def test_a_unique_live_name_reads_the_port_by_its_id(self, handler):
        rows = [_candidate("Nightly Report")]

        with (
            _directory(**{"Nightly Report": "wf-42"}),
            _patched_port({"wf-42": SpendResult(figure=Decimal("3"))}) as port,
        ):
            enriched = await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END)

        assert port.await_args.args[2] == ["wf-42"]
        assert enriched.rows[0]["total_cost_usd"] == 3.0

    @pytest.mark.asyncio
    async def test_the_directory_is_read_in_a_worker_thread_never_on_the_event_loop(self, handler):
        """EPMCDME-15537: the lookup is a synchronous database read, and the backend serves every
        request from one event loop."""
        threads = []

        def _record_thread(_names):
            threads.append(threading.get_ident())
            return [("wf-42", "Nightly Report")]

        with patch(WORKFLOW_DIRECTORY, side_effect=_record_thread), _patched_port({}):
            await handler._enrich_workflows_spending([_candidate("Nightly Report")], PERIOD_START, PERIOD_END)

        assert len(threads) == 1
        assert threads[0] != threading.get_ident()

    @pytest.mark.asyncio
    async def test_a_name_two_live_workflows_share_is_absent_and_never_asked_about(self, handler):
        """Placing the money in both buckets doubles it; placing it in one is a silently wrong bucket."""
        rows = [_candidate("Deploy"), _candidate("Nightly Report")]

        with (
            _directory(Deploy=["wf-1", "wf-2"], **{"Nightly Report": "wf-42"}),
            _patched_port({"wf-42": SpendResult(figure=Decimal("3"))}) as port,
        ):
            enriched = await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END)

        assert port.await_args.args[2] == ["wf-42"]
        by_name = {row["workflow_name"]: row for row in enriched.rows}
        assert by_name["Deploy"]["total_cost_usd"] is None
        assert by_name["Deploy"]["spend_unavailable"] is True
        assert by_name["Nightly Report"]["total_cost_usd"] == 3.0

    @pytest.mark.asyncio
    async def test_a_name_no_live_workflow_carries_is_absent(self, handler):
        """A deleted workflow's history keeps its row and its counts; its ledger money has no id to be read by."""
        rows = [_candidate("Deleted Workflow")]

        with _directory(), _patched_port({}) as port:
            enriched = await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END)

        port.assert_not_awaited()
        assert enriched.rows[0]["workflow_name"] == "Deleted Workflow"
        assert enriched.rows[0]["total_cost_usd"] is None
        assert enriched.rows[0]["spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_a_directory_failure_leaves_every_at_or_above_leg_absent_and_never_raises(self, handler):
        rows = [_candidate("Nightly Report"), _candidate("Deploy")]

        with (
            patch(WORKFLOW_DIRECTORY, side_effect=RuntimeError("database is down")),
            patch("codemie.service.analytics.handlers.workflow_handler.logger") as log,
            _patched_port({}) as port,
        ):
            enriched = await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END)

        port.assert_not_awaited()
        assert [row["workflow_name"] for row in enriched.rows] == ["Nightly Report", "Deploy"]
        assert all(row["total_cost_usd"] is None and row["spend_unavailable"] is True for row in enriched.rows)
        # Worth a warning, unlike a name the directory simply does not hold.
        log.warning.assert_called_once()
        assert "database is down" in log.warning.call_args.args[0]

    def test_a_lookup_failure_is_reported_and_never_raised(self):
        with patch(WORKFLOW_DIRECTORY, side_effect=RuntimeError("database is down")):
            ids, failed = WorkflowHandler._resolve_workflow_ids(["Nightly Report"])

        assert ids == {}
        assert failed is True

    def test_names_are_deduplicated_and_blanks_dropped_before_the_lookup(self):
        with patch(WORKFLOW_DIRECTORY, return_value=[]) as directory:
            WorkflowHandler._resolve_workflow_ids(["Deploy", "Deploy", "", None, "Nightly Report"])

        directory.assert_called_once_with(["Deploy", "Nightly Report"])

    def test_no_names_makes_no_query(self):
        with patch(WORKFLOW_DIRECTORY) as directory:
            assert WorkflowHandler._resolve_workflow_ids([]) == ({}, False)
        directory.assert_not_called()

    def test_every_live_id_per_name_is_reported(self):
        with _directory(Deploy=["wf-1", "wf-2"], **{"Nightly Report": "wf-42"}):
            ids, failed = WorkflowHandler._resolve_workflow_ids(["Deploy", "Nightly Report", "Gone"])

        assert ids == {"Deploy": ["wf-1", "wf-2"], "Nightly Report": ["wf-42"]}
        assert failed is False

    @pytest.mark.asyncio
    async def test_a_rename_inside_the_window_lands_every_at_or_above_row_on_the_current_name(self, handler):
        """Known and accepted: the ledger keys on the id, so the old name's leg is absent and the new
        name's leg carries all of that workflow's money. The table still foots - a live id has exactly
        one current name - and the split across two rows is the name key's documented cost."""
        rows = [_candidate("Old Name"), _candidate("New Name")]

        with _directory(**{"New Name": "wf-1"}), _patched_port({"wf-1": SpendResult(figure=Decimal("9"))}):
            enriched = await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END)

        by_name = {row["workflow_name"]: row for row in enriched.rows}
        assert by_name["New Name"]["total_cost_usd"] == 9.0
        assert by_name["Old Name"]["total_cost_usd"] is None
        assert enriched.totals["total_cost_usd"] == 9.0


class TestWorkflowsAcrossTheCoverageFloor:
    """Where a row's money comes from depends only on where the window sits relative to `F`."""

    @pytest.mark.asyncio
    async def test_a_window_wholly_below_the_floor_uses_the_aggregations_own_money(self, handler):
        rows = [_candidate("Nightly Report", total_runs=4, pre_floor=_measured(10.0))]

        with _directory(**{"Nightly Report": "wf-42"}) as directory, _patched_port({}) as port:
            enriched = await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END, BELOW_FLOOR_ONLY)

        port.assert_not_awaited()
        directory.assert_not_called()
        row = enriched.rows[0]
        assert row["total_cost_usd"] == 10.0
        assert row["avg_cost_usd"] == 2.5
        assert row["spend_unavailable"] is False
        assert row["spend_partial"] is False

    @pytest.mark.asyncio
    async def test_a_window_wholly_above_the_floor_reads_only_the_port(self, handler):
        """The pre-floor figure a builder may have attached is not this window's money."""
        rows = [_candidate("Nightly Report", pre_floor=_measured(999.0))]

        with (
            _directory(**{"Nightly Report": "wf-42"}),
            _patched_port({"wf-42": SpendResult(figure=Decimal("4"))}, expect_period=PERIOD),
        ):
            enriched = await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END, ABOVE_FLOOR_ONLY)

        assert enriched.rows[0]["total_cost_usd"] == 4.0
        assert enriched.rows[0]["spend_unavailable"] is False

    @pytest.mark.asyncio
    async def test_the_kill_switch_off_keeps_the_above_floor_shape_over_the_whole_window(self, handler):
        rows = [_candidate("Nightly Report", pre_floor=_measured(999.0))]

        with (
            _directory(**{"Nightly Report": "wf-42"}),
            _patched_port({"wf-42": SpendResult(figure=Decimal("4"))}, expect_period=PERIOD),
        ):
            enriched = await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END, None)

        assert enriched.rows[0]["total_cost_usd"] == 4.0

    @pytest.mark.asyncio
    async def test_a_crossing_window_shows_one_seamless_total(self, handler):
        rows = [_candidate("Nightly Report", total_runs=4, pre_floor=_measured(10.0))]

        with (
            _directory(**{"Nightly Report": "wf-42"}),
            _patched_port({"wf-42": SpendResult(figure=Decimal("2.5"))}, expect_period=AT_OR_ABOVE_FLOOR_LEG),
        ):
            enriched = await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END, FLOOR)

        row = enriched.rows[0]
        assert row["total_cost_usd"] == 12.5
        assert row["avg_cost_usd"] == 3.125
        assert row["spend_unavailable"] is False
        assert row["spend_partial"] is False
        assert enriched.totals["total_cost_usd"] == 12.5

    @pytest.mark.asyncio
    async def test_a_crossing_window_asks_the_port_over_the_at_or_above_leg_only(self, handler):
        """Handing the port the whole window flags `[start, F)` unavailable on money the aggregation
        already measured there, and the row double-labels."""
        rows = [_candidate("Nightly Report", pre_floor=_measured(10.0))]

        with (
            _directory(**{"Nightly Report": "wf-42"}),
            _patched_port({"wf-42": SpendResult(figure=Decimal("2.5"))}, expect_period=None) as port,
        ):
            await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END, FLOOR)

        assert port.await_args.args[0] == AT_OR_ABOVE_FLOOR_LEG

    @pytest.mark.asyncio
    async def test_a_leg_that_found_no_figure_labels_the_row_rather_than_shortening_it(self, handler):
        rows = [_candidate("Nightly Report", pre_floor=render_spend(None))]

        with (
            _directory(**{"Nightly Report": "wf-42"}),
            _patched_port({"wf-42": SpendResult(figure=Decimal("2.5"))}, expect_period=AT_OR_ABOVE_FLOOR_LEG),
        ):
            enriched = await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END, FLOOR)

        row = enriched.rows[0]
        assert row["total_cost_usd"] == 2.5
        assert row["spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_a_measured_zero_below_the_floor_is_still_a_measured_zero(self, handler):
        rows = [_candidate("Nightly Report", pre_floor=_measured(0.0))]

        with _directory() as directory, _patched_port({}):
            enriched = await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END, BELOW_FLOOR_ONLY)

        directory.assert_not_called()
        assert len(enriched.rows) == 1
        assert enriched.rows[0]["total_cost_usd"] == 0.0
        assert enriched.rows[0]["spend_unavailable"] is False

    @pytest.mark.asyncio
    async def test_the_top_usage_table_splits_the_same_way(self, handler):
        rows = [{"workflow_name": "Nightly Report", "invocations": 7, PRE_FLOOR_MONEY_KEY: _measured(10.0)}]

        with (
            _directory(**{"Nightly Report": "wf-42"}),
            _patched_port({"wf-42": SpendResult(figure=Decimal("2.5"))}, expect_period=AT_OR_ABOVE_FLOOR_LEG),
        ):
            enriched = await handler._enrich_top_workflow_usage_spending(rows, PERIOD_START, PERIOD_END, FLOOR)

        assert enriched.rows[0]["money_spent"] == 12.5
        assert PRE_FLOOR_MONEY_KEY not in enriched.rows[0]
        assert enriched.totals["money_spent"] == 12.5


class TestThePreFloorBoundaryIsReadOnce:
    """Where the boundary that decides the split comes from, and what turns it off."""

    def test_the_floor_is_the_registered_one(self, handler):
        with _cutover_at(FLOOR):
            assert handler._pre_floor_boundary() == FLOOR

    def test_the_kill_switch_off_means_no_boundary_at_all(self, handler):
        with _cutover_at(FLOOR, enabled=False):
            assert handler._pre_floor_boundary() is None

    def test_unactivated_coverage_publishes_no_boundary(self, handler):
        with _cutover_at(None):
            assert handler._pre_floor_boundary() is None

    def test_a_published_floor_is_ignored_while_the_ledger_is_not_ready(self, handler):
        coverage = LedgerCoverage(coverage_floor=FLOOR, ready=False)
        with (
            patch(REGISTERED_COVERAGE, return_value=coverage),
            patch.object(config, "SPEND_LEDGER_LEGACY_BELOW_FLOOR", True),
        ):
            assert handler._pre_floor_boundary() is None

    def test_the_project_and_workflow_tables_read_one_floor(self, handler, mock_user, mock_repository):
        """Both delegate the decision to `money_coverage.pre_floor_boundary` over the same registry, so
        the two tables cannot sit on opposite sides of one floor. Registered for real rather than
        patched, because the two handlers read the registry through two module names."""
        reset_ledgers()
        try:
            register_ledger_coverage(LedgerCoverage(coverage_floor=FLOOR, ready=True))
            with patch.object(config, "SPEND_LEDGER_LEGACY_BELOW_FLOOR", True):
                assert handler._pre_floor_boundary() == FLOOR
                assert ProjectHandler(mock_user, mock_repository)._pre_floor_boundary() == FLOOR
        finally:
            reset_ledgers()

    @pytest.mark.asyncio
    async def test_the_boundary_is_read_once_per_request_and_handed_to_both_halves(self, handler):
        """The value scopes the money sub-aggregation and decides which legs the enricher runs; two
        reads a request apart could disagree, leaving money clipped at a floor the split no longer
        believes in."""
        captured: dict[str, object] = {}

        async def _execute(**kwargs):
            captured["agg"] = kwargs["agg_builder"]({"bool": {"filter": []}}, 10)
            captured["enricher"] = kwargs["row_enricher"]
            return {"data": {"rows": []}}

        with (
            _cutover_at(FLOOR),
            patch.object(handler._pipeline, "execute_enriched_tabular_query", new=AsyncMock(side_effect=_execute)),
        ):
            await handler.get_workflows(time_period="last_30_days")
            assert PRE_FLOOR_MONEY_BUCKET in captured["agg"]["aggs"]["paginated_results"]["aggs"]

        # The enricher closes over the floor it was built with, so a change after the read does nothing.
        rows = [_candidate("Nightly Report", pre_floor=_measured(10.0))]
        with _directory(**{"Nightly Report": "wf-42"}), _patched_port({}, expect_period=AT_OR_ABOVE_FLOOR_LEG) as port:
            enriched = await captured["enricher"](rows, PERIOD_START, PERIOD_END)
        assert port.await_args.args[0] == AT_OR_ABOVE_FLOOR_LEG
        assert enriched.rows[0]["total_cost_usd"] == 10.0
        assert enriched.rows[0]["spend_unavailable"] is True


class TestTheTwoLegsMeasureTimeOnTwoDifferentClocks:
    """**Known, accepted limitation. These tests assert what this table does, not what it should do.**

    The legs tile the requested window as instants. They do not tile the *rows*: the pre-floor leg is
    an Elasticsearch aggregation clipped on `@timestamp` - metric **emission** time - while the
    at-or-above leg reads `litellm_spend_logs`, which admits a row by `start_time`, when the call
    **started**. A call whose two stamps fall on opposite sides of `F` is claimed by neither leg or by
    both, and the row carries no flag and no portion either way: it foots short, or doubled, and reads
    exact. `cutover.py`'s exposure reaching a surface, bounded by the emission lag `L`; the
    mitigations are the owner's to choose (Q32: accept, and cut at a low-traffic hour).
    """

    @staticmethod
    async def _crossing_row(handler, *, below_floor_sum: float, above_floor: str) -> dict:
        rows = [_candidate("atlas", pre_floor=_measured(below_floor_sum))]
        with (
            _directory(atlas="wf-atlas"),
            _patched_port({"wf-atlas": SpendResult(figure=Decimal(above_floor))}, expect_period=AT_OR_ABOVE_FLOOR_LEG),
        ):
            enriched = await handler._enrich_workflows_spending(rows, PERIOD_START, PERIOD_END, FLOOR)
        return enriched.rows[0]

    @pytest.mark.asyncio
    async def test_a_call_starting_before_the_floor_but_emitted_after_it_is_dropped_by_both_legs(self, handler):
        started_at = FLOOR - timedelta(minutes=10)
        assert FLOOR - DEFAULT_EMISSION_LAG <= started_at < FLOOR, "inside the band L bounds, by construction"

        row = await self._crossing_row(handler, below_floor_sum=10.0, above_floor="0")

        assert row["total_cost_usd"] == 10.0, "KNOWN LIMITATION: 0.50 was spent inside the window, neither leg has it"
        assert row["spend_unavailable"] is False, "KNOWN LIMITATION: the shortfall raises no flag"
        assert row["spend_partial"] is False, "KNOWN LIMITATION: and labels no dimension"

    @pytest.mark.asyncio
    async def test_a_call_starting_after_the_floor_but_emitted_before_it_is_counted_by_both_legs(self, handler):
        row = await self._crossing_row(handler, below_floor_sum=0.5, above_floor="0.5")

        assert row["total_cost_usd"] == 1.0, "KNOWN LIMITATION: 0.50 spent once, claimed by both legs"
        assert row["spend_unavailable"] is False, "KNOWN LIMITATION: a doubled figure presents as a correct one"
        assert row["spend_partial"] is False


class TestTopWorkflowUsageMoneyComesFromTheSpendPort:
    """The second surface: one money column, no ratio."""

    @staticmethod
    def _candidate(name: str) -> dict:
        return {"workflow_name": name, "invocations": 7, PRE_FLOOR_MONEY_KEY: render_spend(None)}

    @pytest.mark.asyncio
    async def test_the_port_is_keyed_on_the_resolved_id(self, handler):
        rows = [self._candidate("Nightly Report")]

        with _directory(**{"Nightly Report": "wf-42"}), _patched_port({}) as port:
            enriched = await handler._enrich_top_workflow_usage_spending(rows, PERIOD_START, PERIOD_END)

        assert port.await_args.args[2] == ["wf-42"]
        assert PRE_FLOOR_MONEY_KEY not in enriched.rows[0]

    @pytest.mark.asyncio
    async def test_a_figure_fills_the_cell_and_the_footer(self, handler):
        rows = [self._candidate("Nightly Report")]

        with _directory(**{"Nightly Report": "wf-42"}), _patched_port({"wf-42": SpendResult(figure=Decimal("4.5"))}):
            enriched = await handler._enrich_top_workflow_usage_spending(rows, PERIOD_START, PERIOD_END)

        assert enriched.rows[0]["money_spent"] == 4.5
        assert enriched.columns == ("money_spent",)
        assert enriched.totals["money_spent"] == 4.5

    @pytest.mark.asyncio
    async def test_an_absent_figure_renders_no_number_and_says_so(self, handler):
        rows = [self._candidate("Nightly Report")]

        with _directory(**{"Nightly Report": "wf-42"}), _patched_port({"wf-42": SpendResult.no_figure(PERIOD)}):
            enriched = await handler._enrich_top_workflow_usage_spending(rows, PERIOD_START, PERIOD_END)

        assert enriched.rows[0]["money_spent"] is None
        assert enriched.rows[0]["spend_unavailable"] is True
        assert "money_spent" not in enriched.totals
        assert enriched.totals["money_spent_spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_a_measured_zero_keeps_its_row(self, handler):
        rows = [self._candidate("Nightly Report")]

        with _directory(**{"Nightly Report": "wf-42"}), _patched_port({"wf-42": SpendResult(figure=Decimal("0"))}):
            enriched = await handler._enrich_top_workflow_usage_spending(rows, PERIOD_START, PERIOD_END)

        assert len(enriched.rows) == 1
        assert enriched.rows[0]["money_spent"] == 0.0

    @pytest.mark.asyncio
    async def test_the_money_order_puts_an_absent_figure_last_rather_than_at_zero(self, handler):
        rows = [self._candidate("absent"), self._candidate("zero"), self._candidate("large")]
        answers = {
            "id-absent": SpendResult.no_figure(PERIOD),
            "id-zero": SpendResult(figure=Decimal("0")),
            "id-large": SpendResult(figure=Decimal("9")),
        }

        with _directory(absent="id-absent", zero="id-zero", large="id-large"), _patched_port(answers):
            enriched = await handler._enrich_top_workflow_usage_spending(rows, PERIOD_START, PERIOD_END)

        assert [row["workflow_name"] for row in enriched.rows] == ["large", "zero", "absent"]


class TestAClientSourceFilterReadsNoMoney:
    """No ledger row records which client a call came from, so a client-narrowed table's figure would be
    the unnarrowed one under the narrowed label. The port is not asked; the counts Elasticsearch narrowed
    stay, and every money cell is absent."""

    @pytest.mark.asyncio
    async def test_the_workflows_table_asks_the_port_nothing(self, handler, mock_repository):
        mock_repository.execute_aggregation_query.return_value = _as_result([_workflows_bucket("Nightly Report")])

        with (
            _cutover_at(ABOVE_FLOOR_ONLY),
            _directory(**{"Nightly Report": "wf-42"}),
            _patched_port({"wf-42": SpendResult(figure=Decimal("4"))}) as port,
        ):
            result = await handler.get_workflows(start_date=PERIOD_START, end_date=PERIOD_END, client_source="teams")

        port.assert_not_awaited()
        row = result["data"]["rows"][0]
        assert row["total_cost_usd"] is None
        assert row["avg_cost_usd"] is None
        assert row["spend_unavailable"] is True
        assert (row["total_runs"], row["total_tokens"]) == (4, 1000)
        assert "total_cost_usd" not in result["data"]["totals"]
        assert result["data"]["totals"]["total_cost_usd_spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_the_top_usage_table_asks_the_port_nothing(self, handler, mock_repository):
        mock_repository.execute_aggregation_query.return_value = _as_result([_top_usage_bucket("Nightly Report")])

        with (
            _cutover_at(ABOVE_FLOOR_ONLY),
            _directory(**{"Nightly Report": "wf-42"}),
            _patched_port({"wf-42": SpendResult(figure=Decimal("4"))}) as port,
        ):
            result = await handler.get_top_workflow_usage(
                start_date=PERIOD_START, end_date=PERIOD_END, client_source="teams"
            )

        port.assert_not_awaited()
        row = result["data"]["rows"][0]
        assert row["money_spent"] is None
        assert row["spend_unavailable"] is True
        assert (row["invocations"], row["unique_users"]) == (7, 3)
        assert "money_spent" not in result["data"]["totals"]
        assert result["data"]["totals"]["money_spent_spend_unavailable"] is True


class TestTheAveragePerRunCost:
    """A ratio is emitted only where one exists."""

    def test_a_figure_over_runs_is_the_per_run_cost(self):
        assert WorkflowHandler._avg_cost(10.0, 4) == 2.5

    def test_no_runs_is_no_ratio(self):
        assert WorkflowHandler._avg_cost(10.0, 0) is None

    def test_an_absent_figure_is_no_ratio(self):
        """Dividing an unknown numerator by a count does not make the quotient knowable."""
        assert WorkflowHandler._avg_cost(None, 4) is None

    def test_a_measured_zero_over_runs_is_a_measured_zero(self):
        assert WorkflowHandler._avg_cost(0.0, 4) == 0.0


class TestTheDashboardFilterReachesTheMoneyRead:
    """RD-D1: a workflow's money is narrowed by the request's own users and projects filter.

    The ledger keys a workflow's rows on its own column, which holds its executions in every project
    and by every person, so a filtered table read without the filter shows the workflow's spend
    everywhere.
    """

    ASKED = ReportFilter(projects=("p1",))
    FILTER_FOR = "codemie.service.analytics.handlers.workflow_handler.report_filter_for"

    @pytest.mark.parametrize("entry", ["get_workflows", "get_top_workflow_usage"])
    @pytest.mark.asyncio
    async def test_each_table_builds_it_from_the_request_and_the_port_receives_it(self, handler, entry):
        captured: dict[str, object] = {}

        async def _execute(**kwargs):
            captured["enricher"] = kwargs["row_enricher"]
            return {"data": {"rows": []}}

        with (
            _cutover_at(FLOOR),
            patch(self.FILTER_FOR, new=AsyncMock(return_value=self.ASKED)) as build,
            patch.object(handler._pipeline, "execute_enriched_tabular_query", new=AsyncMock(side_effect=_execute)),
        ):
            await getattr(handler, entry)(time_period="last_30_days", users=["u1"], projects=["p1"])

        build.assert_awaited_once_with(["u1"], ["p1"])
        rows = [_candidate("Nightly Report", pre_floor=_measured(10.0))]
        with _directory(**{"Nightly Report": "wf-42"}), _patched_port({}, expect_period=AT_OR_ABOVE_FLOOR_LEG) as port:
            await captured["enricher"](rows, PERIOD_START, PERIOD_END)
        assert port.await_args.kwargs["report_filter"] is self.ASKED
