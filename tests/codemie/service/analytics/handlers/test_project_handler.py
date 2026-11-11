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

"""Unit tests for ProjectHandler."""

from __future__ import annotations

import contextlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.configs import config
from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.field_constants import METRIC_NAME_KEYWORD_FIELD
from codemie.service.analytics.handlers.money_coverage import SETTLED_AGG, UNSETTLED_AGG
from codemie.service.analytics.handlers.project_handler import (
    INPUT_TOKENS_FIELD,
    MONEY_SPENT_FIELD,
    PRE_FLOOR_MONEY_BUCKET,
    PRE_FLOOR_MONEY_KEY,
    PRE_FLOOR_MONEY_SUM,
    TIMESTAMP_FIELD,
    UNATTRIBUTED_PROJECT_KEY,
    ProjectHandler,
)
from codemie.service.analytics.query_pipeline import enriched_spend_rows
from codemie.service.spend.coverage import (
    DEFAULT_EMISSION_LAG,
    LedgerCoverage,
    Portion,
    SpendDimension,
    SpendResult,
    TimeRange,
)
from codemie.service.spend.ledger import AccessScope, ReportFilter, UserTerm
from codemie.service.spend.spend_group import SpendGroup
from codemie.service.spend.rendering import (
    apply_spend_cells,
    combine_rendered_spend,
    render_spend,
    time_range_or_none,
)


#: The boundary the `mock_user` fixture below is entitled to: a plain member of no project at all.
#: **Restricted with an empty project set, which matches nothing rather than everything** - the rule
#: `AccessScope` holds and `SecureQueryBuilder`'s `minimum_should_match=0` already applied to the very
#: candidate buckets this table's figures foot.
_EXPECTED_SCOPE = AccessScope(unrestricted=False, user_id="test-user-id")


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
    return ProjectHandler(mock_user, mock_repository)


class TestGetProjectsUniqueDaily:
    """Tests for get_projects_unique_daily method."""

    def test_build_projects_unique_daily_aggregation_structure(self, handler):
        """Verify date_histogram aggregation structure with cardinality on project field."""
        # Arrange
        query = {
            "bool": {
                "filter": [
                    {"range": {"@timestamp": {"gte": "2026-01-01T00:00:00.000Z", "lte": "2026-01-31T23:59:59.999Z"}}}
                ]
            }
        }

        # Act
        agg_body = handler._build_projects_unique_daily_aggregation(query)

        # Assert
        assert agg_body["query"] == query
        assert agg_body["size"] == 0

        # Verify paginated_results structure uses date_histogram
        date_hist_agg = agg_body["aggs"]["paginated_results"]
        assert "date_histogram" in date_hist_agg
        assert date_hist_agg["date_histogram"]["field"] == "time"
        assert date_hist_agg["date_histogram"]["calendar_interval"] == "1d"
        assert date_hist_agg["date_histogram"]["time_zone"] == "UTC"
        assert date_hist_agg["date_histogram"]["order"] == {"_key": "asc"}

        # Verify extended_bounds is present
        assert "extended_bounds" in date_hist_agg["date_histogram"]
        extended_bounds = date_hist_agg["date_histogram"]["extended_bounds"]
        assert "min" in extended_bounds
        assert "max" in extended_bounds

        # Verify nested cardinality aggregation on project field
        assert "aggs" in date_hist_agg
        assert "unique_projects" in date_hist_agg["aggs"]
        assert "cardinality" in date_hist_agg["aggs"]["unique_projects"]
        assert date_hist_agg["aggs"]["unique_projects"]["cardinality"]["field"] == "attributes.project.keyword"

    def test_build_projects_unique_daily_handles_missing_time_range(self, handler):
        """Verify aggregation works without time range (no extended_bounds)."""
        # Arrange
        query = {"bool": {"filter": []}}  # No time range filter

        # Act
        agg_body = handler._build_projects_unique_daily_aggregation(query)

        # Assert
        assert agg_body["query"] == query
        date_hist_agg = agg_body["aggs"]["paginated_results"]
        # extended_bounds should not be present when time range is missing
        assert "extended_bounds" not in date_hist_agg["date_histogram"]

    def test_parse_projects_unique_daily_result_formats_dates_from_key_as_string(self, handler):
        """Verify date formatting from key_as_string and cardinality extraction."""
        # Arrange - Mock ES result with date buckets
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": 1735689600000,  # epoch millis
                            "key_as_string": "2026-01-01T00:00:00.000Z",
                            "unique_projects": {"value": 5},
                        },
                        {
                            "key": 1735776000000,
                            "key_as_string": "2026-01-02T00:00:00.000Z",
                            "unique_projects": {"value": 8},
                        },
                        {
                            "key": 1735862400000,
                            "key_as_string": "2026-01-03T00:00:00.000Z",
                            "unique_projects": {"value": 12},
                        },
                    ]
                }
            }
        }

        # Act
        rows = handler._parse_projects_unique_daily_result(result)

        # Assert
        assert len(rows) == 3
        # Verify first row
        assert rows[0]["date"] == "2026-01-01"
        assert rows[0]["unique_projects"] == 5
        # Verify second row
        assert rows[1]["date"] == "2026-01-02"
        assert rows[1]["unique_projects"] == 8
        # Verify third row
        assert rows[2]["date"] == "2026-01-03"
        assert rows[2]["unique_projects"] == 12

    def test_parse_projects_unique_daily_result_formats_dates_from_epoch_millis(self, handler):
        """Verify date formatting from epoch millis when key_as_string is missing."""
        # Arrange - Mock ES result without key_as_string (fallback to epoch)
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": 1735689600000,  # 2025-01-01T00:00:00.000Z
                            "unique_projects": {"value": 5},
                        },
                        {
                            "key": 1735776000000,  # 2025-01-02T00:00:00.000Z
                            "unique_projects": {"value": 8},
                        },
                    ]
                }
            }
        }

        # Act
        rows = handler._parse_projects_unique_daily_result(result)

        # Assert
        assert len(rows) == 2
        assert rows[0]["date"] == "2025-01-01"
        assert rows[0]["unique_projects"] == 5
        assert rows[1]["date"] == "2025-01-02"
        assert rows[1]["unique_projects"] == 8

    def test_parse_projects_unique_daily_result_handles_empty_buckets(self, handler):
        """Verify empty buckets handling."""
        # Arrange
        result = {"aggregations": {"paginated_results": {"buckets": []}}}

        # Act
        rows = handler._parse_projects_unique_daily_result(result)

        # Assert
        assert rows == []

    def test_parse_projects_unique_daily_result_skips_invalid_date_format(self, handler):
        """Verify buckets with invalid date format are skipped with warning."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key_as_string": "invalid-date-format",  # No "T" separator
                            "unique_projects": {"value": 5},
                        },
                        {
                            "key_as_string": "2026-01-02T00:00:00.000Z",
                            "unique_projects": {"value": 8},
                        },
                    ]
                }
            }
        }

        # Act
        rows = handler._parse_projects_unique_daily_result(result)

        # Assert - First bucket skipped, second bucket processed
        assert len(rows) == 1
        assert rows[0]["date"] == "2026-01-02"
        assert rows[0]["unique_projects"] == 8

    def test_parse_projects_unique_daily_result_skips_missing_key(self, handler):
        """Verify buckets with missing key are skipped with warning."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            # Missing both key_as_string and key
                            "unique_projects": {"value": 5},
                        },
                        {
                            "key_as_string": "2026-01-02T00:00:00.000Z",
                            "unique_projects": {"value": 8},
                        },
                    ]
                }
            }
        }

        # Act
        rows = handler._parse_projects_unique_daily_result(result)

        # Assert - First bucket skipped, second bucket processed
        assert len(rows) == 1
        assert rows[0]["date"] == "2026-01-02"

    def test_parse_projects_unique_daily_result_skips_negative_key_millis(self, handler):
        """Verify buckets with negative key_millis are skipped."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key": -1000,  # Negative epoch millis (invalid)
                            "unique_projects": {"value": 5},
                        },
                        {
                            "key": 1735689600000,  # 2025-01-01T00:00:00.000Z
                            "unique_projects": {"value": 8},
                        },
                    ]
                }
            }
        }

        # Act
        rows = handler._parse_projects_unique_daily_result(result)

        # Assert - First bucket skipped, second bucket processed
        assert len(rows) == 1
        assert rows[0]["date"] == "2025-01-01"
        assert rows[0]["unique_projects"] == 8

    def test_parse_projects_unique_daily_result_converts_unique_projects_to_int(self, handler):
        """Verify unique_projects count is converted to integer."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key_as_string": "2026-01-01T00:00:00.000Z",
                            "unique_projects": {"value": 5.0},  # Float value from ES
                        }
                    ]
                }
            }
        }

        # Act
        rows = handler._parse_projects_unique_daily_result(result)

        # Assert
        assert len(rows) == 1
        assert rows[0]["unique_projects"] == 5
        assert isinstance(rows[0]["unique_projects"], int)

    def test_parse_projects_unique_daily_result_handles_zero_projects(self, handler):
        """Verify buckets with zero unique projects are included."""
        # Arrange
        result = {
            "aggregations": {
                "paginated_results": {
                    "buckets": [
                        {
                            "key_as_string": "2026-01-01T00:00:00.000Z",
                            "unique_projects": {"value": 0},  # Zero projects
                        }
                    ]
                }
            }
        }

        # Act
        rows = handler._parse_projects_unique_daily_result(result)

        # Assert
        assert len(rows) == 1
        assert rows[0]["unique_projects"] == 0

    def test_get_projects_unique_daily_columns(self, handler):
        """Verify column definitions for projects unique daily."""
        # Act
        columns = handler._get_projects_unique_daily_columns()

        # Assert
        assert len(columns) == 2
        # Verify date column
        assert columns[0]["id"] == "date"
        assert columns[0]["label"] == "Date"
        assert columns[0]["type"] == "date"
        # Verify unique_projects column
        assert columns[1]["id"] == "unique_projects"
        assert columns[1]["label"] == "Unique Projects"
        assert columns[1]["type"] == "number"

    def test_build_projects_unique_daily_converts_iso_strings_to_epoch_millis(self, handler):
        """Verify ISO date strings are converted to epoch milliseconds for extended_bounds."""
        # Arrange
        query = {
            "bool": {
                "filter": [
                    {
                        "range": {
                            "@timestamp": {
                                "gte": "2026-01-01T00:00:00.000Z",
                                "lte": "2026-01-31T23:59:59.999Z",
                            }
                        }
                    }
                ]
            }
        }

        # Act
        agg_body = handler._build_projects_unique_daily_aggregation(query)

        # Assert
        extended_bounds = agg_body["aggs"]["paginated_results"]["date_histogram"]["extended_bounds"]
        assert isinstance(extended_bounds["min"], int)
        assert isinstance(extended_bounds["max"], int)
        # Verify they're reasonable epoch milliseconds (13 digits)
        assert len(str(extended_bounds["min"])) == 13
        assert len(str(extended_bounds["max"])) == 13
        assert extended_bounds["min"] < extended_bounds["max"]


class TestGetProjectsSpending:
    """Tests for get_projects_spending method."""

    def test_parse_projects_spending_rows_raw_leaves_the_money_cell_for_the_enricher(self, handler):
        """The money cell is None: which side of the floor the window falls on decides what fills it.

        A bucket with no pre-floor money sub-aggregation - the shape a query built with no floor
        returns - carries an **absent** pre-floor figure, never a zero.
        """
        result = {"aggregations": {"paginated_results": {"buckets": [{"key": "project-alpha", "doc_count": 4}]}}}

        rows = handler._parse_projects_spending_rows_raw(result)

        assert rows[0]["project_name"] == "project-alpha"
        assert rows[0]["total_cost_usd"] is None
        assert rows[0][PRE_FLOOR_MONEY_KEY].figure is None
        assert rows[0][PRE_FLOOR_MONEY_KEY].unavailable is True

    @pytest.mark.asyncio
    async def test_get_projects_spending_returns_raw_total_without_cli_adjustment(self, handler):
        """get_projects_spending returns the pipeline total untouched — no CLI adjustment term."""
        raw_rows = [
            {"project_name": "proj-a", "total_cost_usd": 800.0},
        ]
        mock_pipeline_result = {
            "data": {"rows": raw_rows, "columns": []},
            "totals": {},
            "pagination": {"page": 0, "per_page": 20, "total_count": 1, "has_more": False},
        }

        with patch.object(
            handler._pipeline, "execute_enriched_tabular_query", new=AsyncMock(return_value=mock_pipeline_result)
        ):
            result = await handler.get_projects_spending(time_period="last_30_days")

        rows = result["data"]["rows"]
        assert rows == [{"project_name": "proj-a", "total_cost_usd": 800.0}]


PERIOD_START = datetime(2026, 8, 1, tzinfo=timezone.utc)
PERIOD_END = datetime(2026, 8, 31, tzinfo=timezone.utc)
PERIOD = TimeRange(PERIOD_START, PERIOD_END)


def _spending_buckets(*project_names_and_counts: tuple[str, int]) -> dict:
    return {
        "aggregations": {
            "paginated_results": {
                "buckets": [{"key": name, "doc_count": count} for name, count in project_names_and_counts]
            }
        }
    }


@contextlib.asynccontextmanager
async def _fake_session():
    yield MagicMock()


def _patched_projects_port(port: AsyncMock, personal_owners: dict[str, set] | AsyncMock | None = None):
    """Patch the personal-project owner lookup, and the spend port itself.

    `personal_owners` may be the mapping the lookup answers with or the `AsyncMock` standing in for it, so
    a test can assert the lookup was **not** made at all - which is what a window wholly below the floor
    must do: it asks the ledger nothing, so it names no project to look up.

    **The port's dimension read is patched to fail if it is called at all**, in every test that uses
    this helper. There is no window on this path that reads it any more: below the floor the money is
    the candidate aggregation's own, and a second, parallel Elasticsearch read is precisely the thing
    that filtered the raw `attributes.project.keyword` while the rows were keyed on a normalised
    project, lost the `users` request filter, and had to re-derive the money metric set. This
    assertion is what stops it coming back.
    """
    owners = (
        personal_owners if isinstance(personal_owners, AsyncMock) else AsyncMock(return_value=personal_owners or {})
    )
    stack = contextlib.ExitStack()
    stack.enter_context(patch("codemie.service.analytics.handlers.ledger_group_spend.get_async_session", _fake_session))
    stack.enter_context(
        patch(
            "codemie.service.analytics.handlers.ledger_group_spend.application_repository."
            "aget_personal_project_owners_by_name",
            new=owners,
        )
    )
    stack.enter_context(
        patch(
            "codemie.service.analytics.handlers.ledger_group_spend.spend_service.aggregate_by_spend_group_per_filter",
            new=port,
        )
    )
    stack.enter_context(
        patch(
            "codemie.service.analytics.handlers.ledger_group_spend.spend_service.aggregate_by_dimension",
            new=AsyncMock(side_effect=AssertionError("no money on this table comes from a parallel dimension read")),
        )
    )
    return stack


def _money_bucket(key: str, *, money: float | None, settled: int | None = 1, unsettled: int = 0) -> dict:
    """One terms bucket, shaped exactly as `_pre_floor_money_aggs` makes Elasticsearch return it.

    `money=None` with `settled=0` is the real shape of *nothing settled below the floor*: the `sum`
    reports no value because it summed no document, which is an absent figure and not a zero.
    """
    return {
        "key": key,
        "doc_count": 7,
        PRE_FLOOR_MONEY_BUCKET: {
            "doc_count": 7,
            PRE_FLOOR_MONEY_SUM: {"value": money},
            SETTLED_AGG: {"value": settled},
            UNSETTLED_AGG: {"doc_count": unsettled},
        },
    }


def _candidate_rows(handler: ProjectHandler, *buckets: dict) -> list[dict]:
    """The candidate rows the pipeline hands the enricher, built by the handler's own parser.

    Built through the parser rather than by hand on purpose: the pre-floor money reaches the enricher
    only because the builder, the parser and the enricher agree on one bucket name and one sum name,
    and a hand-written row would let all three drift while every test still passed.
    """
    return handler._parse_projects_spending_rows_raw({"aggregations": {"paginated_results": {"buckets": buckets}}})


def _answers(**by_project: SpendResult | None):
    """An aggregate_by_spend_group_per_filter stub answering per project name, over the whole requested window."""
    return _answers_over(PERIOD, **by_project)


#: The filter of a dashboard with nothing selected - what every test not about filtering asks with.
NO_FILTER = ReportFilter()


def _answers_over(
    expected_period: TimeRange, expected_filter: ReportFilter = NO_FILTER, /, **by_project: SpendResult | None
):
    """`_answers`, pinned to the window the ledger read is supposed to cover.

    One read answers every row, each for a filter naming only that project inside the dashboard's own
    filter, so every filter is asserted whole: the row's project, no owners (these projects are shared),
    and the dashboard's users term - which is also the common filter the read narrows its one scan by.
    `access` is taken as a **required keyword**, so a handler that stopped
    threading its boundary fails here rather than being answered with every member's spend: the total
    is a project's *whole* spend, and the rows it foots are already narrowed to what the caller may see.

    The period is asserted rather than ignored because a crossing window must reach this read as its
    **at-or-above-`F` leg only**. Handed the whole window, this read and the pre-floor aggregation
    would both cover the money above the floor and the row would foot at nearly double.
    """

    async def _call(period, *, access, common_filter, report_filters):
        assert period == expected_period, "the ledger read must cover only the at-or-above-floor leg"
        assert access == _EXPECTED_SCOPE, "the figure must be narrowed by the caller's own boundary"
        assert common_filter == replace(
            expected_filter, projects=(), personal_project_owners=()
        ), "the read must be narrowed by the dashboard's own users term"
        return {report_filter: _project_answer(period, report_filter) for report_filter in report_filters}

    def _project_answer(period, report_filter):
        (project,) = report_filter.projects
        assert report_filter == replace(
            expected_filter, projects=(project,), personal_project_owners=()
        ), "the figure must be that project, inside the dashboard's own filter"
        figure = by_project.get(project)
        totals = {SpendGroup.TOTAL: figure} if figure is not None else {}
        return {group: SpendResult.no_figure(period) for group in SpendGroup} | totals

    return AsyncMock(side_effect=_call)


class TestSpendTableHelpers:
    """The money rules both spending tables share."""

    def test_time_range_or_none_reads_a_missing_offset_as_utc(self):
        assert time_range_or_none(datetime(2026, 8, 1), datetime(2026, 8, 31)) == PERIOD

    def test_time_range_or_none_returns_none_for_a_window_that_is_not_one(self):
        assert time_range_or_none(PERIOD_END, PERIOD_END) is None

    def test_apply_spend_cells_renders_an_exact_figure_with_clean_annotations(self):
        rows = [{"total_cost_usd": None}]
        apply_spend_cells(rows, [render_spend(SpendResult(figure=Decimal("12.34")))], "total_cost_usd")
        assert rows[0] == {
            "total_cost_usd": 12.34,
            "spend_unavailable": False,
            "spend_partial": False,
            "spend_missing_amount": None,
        }

    def test_apply_spend_cells_keeps_an_absent_figure_absent(self):
        rows = [{"total_cost_usd": None}]
        apply_spend_cells(rows, [render_spend(SpendResult.no_figure(PERIOD))], "total_cost_usd")
        assert rows[0]["total_cost_usd"] is None
        assert rows[0]["spend_unavailable"] is True

    def test_apply_spend_cells_renders_a_measured_zero_as_zero(self):
        """The mirrored defect: a measured zero is a fact and renders as one."""
        rows = [{"total_cost_usd": None}]
        apply_spend_cells(rows, [render_spend(SpendResult(figure=Decimal("0")))], "total_cost_usd")
        assert rows[0]["total_cost_usd"] == 0.0
        assert rows[0]["spend_unavailable"] is False

    def test_apply_spend_cells_keeps_a_truncated_figure_and_says_it_is_truncated(self):
        rows = [{"total_cost_usd": None}]
        truncated = SpendResult(figure=Decimal("9.5"), unavailable=PERIOD)
        apply_spend_cells(rows, [render_spend(truncated)], "total_cost_usd")
        assert rows[0]["total_cost_usd"] == 9.5
        assert rows[0]["spend_unavailable"] is True

    def test_combine_rendered_spend_labels_a_total_short_by_one_part(self):
        combined = combine_rendered_spend(
            [render_spend(SpendResult(figure=Decimal("5"))), render_spend(SpendResult.no_figure(PERIOD))]
        )
        assert combined.figure == 5.0
        assert combined.partial is True
        assert combined.unavailable is True

    def test_combine_rendered_spend_reports_no_figure_when_no_part_has_one(self):
        combined = combine_rendered_spend([render_spend(None), render_spend(SpendResult.no_figure(PERIOD))])
        assert combined.figure is None
        assert combined.unavailable is True

    def test_combine_rendered_spend_keeps_an_unknown_hole_unknown(self):
        with_unknown_hole = SpendResult(
            figure=Decimal("4"),
            partial={SpendDimension.IDENTITY_BRIDGE: Portion(spend=None, rows=None)},
        )
        combined = combine_rendered_spend(
            [render_spend(with_unknown_hole), render_spend(SpendResult(figure=Decimal("1")))]
        )
        assert combined.figure == 5.0
        assert combined.partial is True
        assert combined.missing_amount is None

    def test_enriched_spend_rows_drops_a_measured_zero_but_keeps_an_absent_figure(self):
        rows = [{"id": "zero", "total_cost_usd": None}, {"id": "absent", "total_cost_usd": None}]
        rendered = [render_spend(SpendResult(figure=Decimal("0"))), render_spend(SpendResult.no_figure(PERIOD))]

        enriched = enriched_spend_rows(rows, rendered, "total_cost_usd")

        assert [row["id"] for row in enriched.rows] == ["absent"]
        assert enriched.columns == ("total_cost_usd",)

    def test_enriched_spend_rows_keeps_a_qualified_zero_and_its_annotation(self):
        """Only an *exact* zero is a measured one, and the filter must say so.

        A figure of `0` carrying an `unavailable` flag means *nothing was spent in the part of the
        window the ledger covers, and the rest is unknown*; one carrying a `partial` portion means
        *the attributed rows total zero and an unattributed amount is missing*. Dropped as measured
        zeros, the rows vanish **and their annotations vanish with them**, and the footer — computed
        over the kept rows only — then asserts the table total is exact. The second form needs no
        coverage-floor crossing, so it reaches a live report on ordinary data.
        """
        zero_below_floor = SpendResult(figure=Decimal("0"), unavailable=PERIOD)
        zero_with_unattributed = SpendResult(
            figure=Decimal("0"),
            partial={SpendDimension.IDENTITY_BRIDGE: Portion(spend=None, rows=None)},
        )
        rows = [
            {"id": "below_floor", "total_cost_usd": None},
            {"id": "unattributed", "total_cost_usd": None},
            {"id": "measured_zero", "total_cost_usd": None},
            {"id": "exact", "total_cost_usd": None},
        ]
        rendered = [
            render_spend(zero_below_floor),
            render_spend(zero_with_unattributed),
            render_spend(SpendResult(figure=Decimal("0"))),
            render_spend(SpendResult(figure=Decimal("10"))),
        ]

        enriched = enriched_spend_rows(rows, rendered, "total_cost_usd")

        kept = {row["id"]: row for row in enriched.rows}
        # The product rule survives: a genuine measured zero is still dropped.
        assert "measured_zero" not in kept
        assert kept["below_floor"]["spend_unavailable"] is True
        assert kept["below_floor"]["total_cost_usd"] == 0.0
        assert kept["unattributed"]["spend_partial"] is True
        assert kept["unattributed"]["total_cost_usd"] == 0.0
        # And the footer can no longer claim the total is exact.
        assert enriched.totals["total_cost_usd_spend_unavailable"] is True
        assert enriched.totals["total_cost_usd_spend_partial"] is True

    def test_enriched_footer_omits_the_money_key_when_no_row_produced_a_figure(self):
        rows = [{"id": "a", "total_cost_usd": None}]
        enriched = enriched_spend_rows(rows, [render_spend(SpendResult.no_figure(PERIOD))], "total_cost_usd")

        assert "total_cost_usd" not in enriched.totals
        assert enriched.totals["total_cost_usd_spend_unavailable"] is True

    def test_enriched_footer_says_a_total_summed_from_some_rows_is_partial(self):
        """The MIXED case: a footer over rows that only partly resolved cannot look whole."""
        rows = [{"id": "a", "total_cost_usd": None}, {"id": "b", "total_cost_usd": None}]
        rendered = [render_spend(SpendResult(figure=Decimal("7.005"))), render_spend(SpendResult.no_figure(PERIOD))]

        enriched = enriched_spend_rows(rows, rendered, "total_cost_usd")

        assert enriched.totals["total_cost_usd"] == 7.005
        assert enriched.totals["total_cost_usd_spend_partial"] is True
        assert enriched.totals["total_cost_usd_spend_unavailable"] is True


class TestProjectsSpendingFromLedger:
    """The projects table reads each project's whole total from the spend port."""

    def test_without_a_floor_the_aggregation_reads_no_money(self, handler):
        """No floor means no window can reach below one, so this query is what it was before the cutover."""
        agg_body = handler._build_projects_spending_aggregation({"bool": {"filter": []}}, 10000, None)

        projects_agg = agg_body["aggs"]["paginated_results"]
        assert projects_agg["terms"]["order"] == {"_count": "desc"}
        assert "aggs" not in projects_agg
        assert MONEY_SPENT_FIELD not in json.dumps(agg_body)

    @pytest.mark.asyncio
    async def test_project_total_is_the_ledgers_total_for_that_project(self, handler):
        rows = [{"project_name": "atlas", "total_cost_usd": None}]
        port = _answers(atlas=SpendResult(figure=Decimal("3.0")))

        with _patched_projects_port(port):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, None)

        assert enriched.rows[0]["total_cost_usd"] == 3.0
        assert enriched.rows[0]["spend_unavailable"] is False
        assert enriched.rows[0]["spend_partial"] is False
        assert enriched.rows[0]["spend_missing_amount"] is None

    @pytest.mark.asyncio
    async def test_the_total_group_is_read_so_no_category_is_left_out(self, handler):
        """A project budgeted for one pot still shows what it spent in the others."""
        rows = [{"project_name": "atlas", "total_cost_usd": None}]
        everything = {
            SpendGroup.TOTAL: SpendResult(figure=Decimal("9")),
            SpendGroup.CLI: SpendResult(figure=Decimal("2")),
            SpendGroup.PLATFORM: SpendResult(figure=Decimal("7")),
        }
        atlas = ReportFilter(projects=("atlas",))
        port = AsyncMock(
            return_value={atlas: {group: SpendResult.no_figure(PERIOD) for group in SpendGroup} | everything}
        )

        with _patched_projects_port(port):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, None)

        assert enriched.rows[0]["total_cost_usd"] == 9.0

    @pytest.mark.asyncio
    async def test_a_project_with_no_assigned_budget_still_shows_what_it_spent(self, handler):
        """Nothing here asks which budgets a project has: a budget is a limit, and this is what was spent."""
        rows = [{"project_name": "unbudgeted", "total_cost_usd": None}]
        port = _answers(unbudgeted=SpendResult(figure=Decimal("4.5")))

        with _patched_projects_port(port):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, None)

        port.assert_awaited_once()
        assert enriched.rows[0]["total_cost_usd"] == 4.5
        assert enriched.rows[0]["spend_unavailable"] is False

    @pytest.mark.asyncio
    async def test_a_personal_project_is_read_through_its_owner(self, handler):
        """Its calls reach the ledger with no project at all, so the filter carries the owner that names them."""
        rows = [{"project_name": "ada@example.com", "total_cost_usd": None}]
        personal = ReportFilter(projects=("ada@example.com",), personal_project_owners=("u-ada",))
        port = AsyncMock(
            return_value={
                personal: {group: SpendResult.no_figure(PERIOD) for group in SpendGroup}
                | {SpendGroup.TOTAL: SpendResult(figure=Decimal("8"))}
            }
        )

        with _patched_projects_port(port, {"ada@example.com": {"u-ada"}}):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, None)

        assert port.await_args.kwargs["report_filters"] == [personal]
        assert enriched.rows[0]["total_cost_usd"] == 8.0

    @pytest.mark.asyncio
    async def test_the_bucket_naming_no_project_gets_no_figure_and_is_not_asked_about(self, handler):
        """`unknown` is not a project: a filter naming it matches no ledger row and would publish a measured $0.00."""
        rows = [{"project_name": UNATTRIBUTED_PROJECT_KEY, "total_cost_usd": None}]
        port = AsyncMock()

        with _patched_projects_port(port):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, None)

        port.assert_not_awaited()
        assert enriched.rows[0]["total_cost_usd"] is None
        assert enriched.rows[0]["spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_no_figure_renders_absent_and_unavailable(self, handler):
        rows = [{"project_name": "atlas", "total_cost_usd": None}]
        port = _answers(atlas=SpendResult.no_figure(PERIOD))

        with _patched_projects_port(port):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, None)

        assert enriched.rows[0]["total_cost_usd"] is None
        assert enriched.rows[0]["spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_measured_zero_is_a_zero_and_leaves_the_table(self, handler):
        rows = [{"project_name": "atlas", "total_cost_usd": None}]
        port = _answers(atlas=SpendResult(figure=Decimal("0")))

        with _patched_projects_port(port):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, None)

        # The cell is a measured zero, never an absence - and a zero-spend project is not listed,
        # exactly as the Elasticsearch zero-cost selector used to leave it out.
        assert rows[0]["total_cost_usd"] == 0.0
        assert enriched.rows == []

    @pytest.mark.asyncio
    async def test_truncated_figure_keeps_its_number_and_its_label(self, handler):
        rows = [{"project_name": "atlas", "total_cost_usd": None}]
        port = _answers(atlas=SpendResult(figure=Decimal("6.25"), unavailable=PERIOD))

        with _patched_projects_port(port):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, None)

        assert enriched.rows[0]["total_cost_usd"] == 6.25
        assert enriched.rows[0]["spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_rows_are_ordered_by_the_ledger_figure_not_by_doc_count(self, handler):
        rows = [{"project_name": "busy", "total_cost_usd": None}, {"project_name": "costly", "total_cost_usd": None}]
        port = _answers(busy=SpendResult(figure=Decimal("1")), costly=SpendResult(figure=Decimal("50")))

        with _patched_projects_port(port):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, None)

        assert [row["project_name"] for row in enriched.rows] == ["costly", "busy"]
        assert port.await_count == 1, "one read for every candidate project"

    @pytest.mark.asyncio
    async def test_absent_figure_sorts_last_and_is_never_sorted_as_zero(self, handler):
        rows = [{"project_name": "absent", "total_cost_usd": None}, {"project_name": "tiny", "total_cost_usd": None}]
        port = _answers(absent=SpendResult.no_figure(PERIOD), tiny=SpendResult(figure=Decimal("0.01")))

        with _patched_projects_port(port):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, None)

        assert [row["project_name"] for row in enriched.rows] == ["tiny", "absent"]
        # No figure was invented to make the row sortable.
        assert enriched.rows[1]["total_cost_usd"] is None

    @pytest.mark.asyncio
    async def test_a_short_ledger_figure_labels_the_row_partial(self, handler):
        rows = [{"project_name": "atlas", "total_cost_usd": None}]
        short = SpendResult(
            figure=Decimal("3"), partial={SpendDimension.IDENTITY_BRIDGE: Portion(spend=None, rows=None)}
        )

        with _patched_projects_port(_answers(atlas=short)):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, None)

        assert enriched.rows[0]["total_cost_usd"] == 3.0
        assert enriched.rows[0]["spend_partial"] is True

    @pytest.mark.asyncio
    async def test_unreadable_window_asks_the_port_nothing(self, handler):
        rows = [{"project_name": "atlas", "total_cost_usd": None}]
        port = AsyncMock()

        with _patched_projects_port(port):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_END, PERIOD_END, None)

        port.assert_not_awaited()
        assert enriched.rows[0]["total_cost_usd"] is None
        assert enriched.rows[0]["spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_annotation_keys_stay_off_the_advertised_columns(self, handler):
        assert [column["id"] for column in handler._get_projects_spending_columns()] == [
            "project_name",
            "total_cost_usd",
        ]


#: A floor sitting inside `PERIOD`, so the same window is crossing, wholly below or wholly above
#: depending only on which floor a test publishes.
FLOOR = datetime(2026, 8, 15, tzinfo=timezone.utc)
PRE_FLOOR_LEG = TimeRange(PERIOD_START, FLOOR)
AT_OR_ABOVE_FLOOR_LEG = TimeRange(FLOOR, PERIOD_END)


@contextlib.contextmanager
def _cutover_at(floor: datetime | None, *, enabled: bool = True):
    """Publish `floor` as the **registered** coverage floor for the body of a test.

    Patched at `ledger_coverage()` rather than by setting config alone, because that is the whole of
    what this handler is required to read. A test that published the floor through config would pass
    just as happily against a handler doing its own config read - the second read that lets this table
    and the ledger sit on opposite sides of one floor mid-change.

    `floor=None` is the unactivated ledger: no floor exists, so nothing splits.
    """
    coverage = LedgerCoverage(coverage_floor=floor, ready=floor is not None)
    with (
        patch("codemie.service.analytics.handlers.project_handler.ledger_coverage", return_value=coverage),
        patch.object(config, "SPEND_LEDGER_LEGACY_BELOW_FLOOR", enabled),
    ):
        yield


#: A floor above the whole of `PERIOD`, so a test about the pre-floor leg is not also a test about legs.
BELOW_FLOOR_ONLY = datetime(2026, 9, 1, tzinfo=timezone.utc)

#: A floor below the whole of `PERIOD`: every instant in the window is at or above it.
ABOVE_FLOOR_ONLY = datetime(2026, 7, 1, tzinfo=timezone.utc)


class TestThePreFloorBoundaryIsReadOnce:
    """Where the boundary that decides the split comes from, and what turns it off.

    One value answers two questions in one request - which window the money sub-aggregation is scoped
    to, and which legs the enricher runs - so it is resolved once and handed to both. A second read a
    request apart could disagree with the first, leaving money clipped at `F` and then footed as
    though it covered the whole window.
    """

    def test_the_floor_is_the_registered_one(self, handler):
        with _cutover_at(FLOOR):
            assert handler._pre_floor_boundary() == FLOOR

    def test_the_kill_switch_off_means_no_boundary_at_all(self, handler):
        with _cutover_at(FLOOR, enabled=False):
            assert handler._pre_floor_boundary() is None

    def test_unactivated_coverage_publishes_no_boundary(self, handler):
        """No floor has been published, so there is nothing to split at."""
        with _cutover_at(None):
            assert handler._pre_floor_boundary() is None

    def test_a_published_floor_is_ignored_while_the_ledger_is_not_ready(self, handler):
        """`activated` is readiness **and** a floor; a floor alone is not a boundary anything may serve."""
        coverage = LedgerCoverage(coverage_floor=FLOOR, ready=False)
        with (
            patch("codemie.service.analytics.handlers.project_handler.ledger_coverage", return_value=coverage),
            patch.object(config, "SPEND_LEDGER_LEGACY_BELOW_FLOOR", True),
        ):
            assert handler._pre_floor_boundary() is None


class TestTheAggregationCarriesItsOwnPreFloorMoney:
    """The candidate query measures the money the pre-floor leg is served from, in the same pass.

    That sum is grouped by the *normalised* bucket key the script emits, narrowed by the `users`
    request filter and the `SecureQueryBuilder` boundary, and scoped to the spending metric set -
    all four for free, because it is computed inside the one query that produced the rows.
    """

    def test_the_money_bucket_is_bounded_at_the_floor(self, handler):
        agg_body = handler._build_projects_spending_aggregation({"bool": {"filter": []}}, 10000, FLOOR)

        money = agg_body["aggs"]["paginated_results"]["aggs"][PRE_FLOOR_MONEY_BUCKET]
        assert money["filter"] == {
            "range": {TIMESTAMP_FIELD: {"lt": FLOOR.isoformat(), "format": "strict_date_optional_time"}}
        }
        assert money["aggs"][PRE_FLOOR_MONEY_SUM] == {"sum": {"field": MONEY_SPENT_FIELD}}

    def test_the_floor_instant_belongs_to_the_at_or_above_leg(self, handler):
        """`lt`, never `lte`: a closed lower bound lands the boundary row in both legs and doubles it."""
        agg_body = handler._build_projects_spending_aggregation({"bool": {"filter": []}}, 10000, FLOOR)

        bound = agg_body["aggs"]["paginated_results"]["aggs"][PRE_FLOOR_MONEY_BUCKET]["filter"]["range"]
        assert "lte" not in bound[TIMESTAMP_FIELD]

    def test_the_money_sum_says_whether_it_is_whole(self, handler):
        """A `sum` over `money_spent` skips every unsettled cost, so it is read with its coverage."""
        agg_body = handler._build_projects_spending_aggregation({"bool": {"filter": []}}, 10000, FLOOR)

        measured = agg_body["aggs"]["paginated_results"]["aggs"][PRE_FLOOR_MONEY_BUCKET]["aggs"]
        assert SETTLED_AGG in measured
        assert UNSETTLED_AGG in measured

    def test_only_documents_that_billed_a_model_can_be_counted_unsettled(self, handler):
        """The `SPENDING_METRICS` filter does not make every document in this bucket cost-bearing.

        `codemie_tools_usage_tokens` is in that list and has two emitters: one bills a completion and
        writes token counts (and money, once the cost settles), the other writes the same metric name
        for every successful tool invocation with neither. Unnarrowed, that second emitter's documents
        are counted as holes in nearly every project, and every pre-floor row is published `partial` -
        an "(at least; ...)" this table never showed before the cutover.
        """
        agg_body = handler._build_projects_spending_aggregation({"bool": {"filter": []}}, 10000, FLOOR)

        unsettled = agg_body["aggs"]["paginated_results"]["aggs"][PRE_FLOOR_MONEY_BUCKET]["aggs"][UNSETTLED_AGG]
        assert unsettled == {
            "filter": {
                "bool": {
                    "must_not": [{"exists": {"field": MONEY_SPENT_FIELD}}],
                    "filter": [{"exists": {"field": INPUT_TOKENS_FIELD}}],
                }
            }
        }

    def test_the_narrowing_does_not_silence_a_real_hole_by_metric_name(self, handler):
        """Excluding the tools metric by name would drop its *billed* calls from the count too.

        Its money-writing emitter is the one that goes unsettled on a streaming response, so a
        name-shaped narrowing takes out real holes along with phantom ones and leaves a short figure
        reading exact. The token counts separate the two emitters; the metric name cannot.
        """
        agg_body = handler._build_projects_spending_aggregation({"bool": {"filter": []}}, 10000, FLOOR)

        unsettled = agg_body["aggs"]["paginated_results"]["aggs"][PRE_FLOOR_MONEY_BUCKET]["aggs"][UNSETTLED_AGG]
        assert METRIC_NAME_KEYWORD_FIELD not in json.dumps(unsettled)

    def test_the_aggregation_emits_the_sentinel_the_money_is_grouped_under(self, handler):
        """One spelling of the sentinel, and the money bucket hangs off the same normalised key."""
        agg_body = handler._build_projects_spending_aggregation({"bool": {"filter": []}}, 10000, FLOOR)

        script = agg_body["aggs"]["paginated_results"]["terms"]["script"]["source"]
        assert f": '{UNATTRIBUTED_PROJECT_KEY}';" in script


class TestProjectsSpendingAcrossTheCoverageFloor:
    """Where each leg's money comes from, and that neither leg is ever asked twice.

    At or above the floor a project's total is the ledger's total for that project, read from
    `litellm_spend_logs`. Below the floor the ledger holds nothing, so the same read would empty the
    table for every window the platform had before the rollout. Below the floor the money is instead
    the candidate aggregation's own per-project sum - the number this table printed before the cutover,
    computed by the very query that produced the rows.
    """

    @pytest.mark.asyncio
    async def test_window_wholly_below_the_floor_uses_the_aggregations_own_money(self, handler):
        """The pre-floor figure is the bucket's, and **nothing is fetched for it**."""
        rows = _candidate_rows(handler, _money_bucket("atlas", money=40.0))
        consumed = AsyncMock()
        owners = AsyncMock(return_value={})

        with _patched_projects_port(consumed, owners):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, BELOW_FLOOR_ONLY)

        # No ledger read, no personal-project lookup, and - asserted by the helper itself - no
        # parallel dimension read: below the floor this table asks the spend port nothing at all.
        consumed.assert_not_awaited()
        owners.assert_not_awaited()
        assert enriched.rows[0]["total_cost_usd"] == 40.0
        assert enriched.rows[0]["spend_unavailable"] is False
        assert enriched.rows[0]["spend_partial"] is False

    @pytest.mark.asyncio
    async def test_documents_that_never_billed_a_model_leave_the_row_unqualified(self, handler):
        """Seven documents in the bucket, four of them settled a cost, and the row still reads clean.

        The other three carried no money because nothing ever called a model for them - the tool-count
        emitter's documents, which share `codemie_tools_usage_tokens` with the emitter that does bill
        one. `_pre_floor_money_aggs` keeps them out of the unsettled count, so this row publishes the
        bare figure production shows rather than acquiring an "(at least; ...)" it never had.
        """
        rows = _candidate_rows(handler, _money_bucket("atlas", money=40.0, settled=4, unsettled=0))

        with _patched_projects_port(AsyncMock()):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, BELOW_FLOOR_ONLY)

        assert enriched.rows[0]["total_cost_usd"] == 40.0
        assert enriched.rows[0]["spend_partial"] is False
        assert enriched.rows[0]["spend_unavailable"] is False

    @pytest.mark.asyncio
    async def test_a_project_with_no_pre_floor_documents_is_absent_never_zero(self, handler):
        """Nothing settled below the floor is an absent figure - `$0.00` would claim it spent nothing."""
        rows = _candidate_rows(handler, _money_bucket("quiet", money=None, settled=0))

        with _patched_projects_port(AsyncMock()):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, BELOW_FLOOR_ONLY)

        assert enriched.rows[0]["total_cost_usd"] is None
        assert enriched.rows[0]["spend_unavailable"] is True
        assert enriched.rows[0]["project_name"] == "quiet", "and the row stays on the table"

    @pytest.mark.asyncio
    async def test_an_unsettled_cost_below_the_floor_keeps_its_figure_and_says_it_is_short(self, handler):
        """A cost-bearing document that carried no money is a hole the sum silently skipped."""
        rows = _candidate_rows(handler, _money_bucket("atlas", money=12.5, settled=4, unsettled=2))

        with _patched_projects_port(AsyncMock()):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, BELOW_FLOOR_ONLY)

        assert enriched.rows[0]["total_cost_usd"] == 12.5
        assert enriched.rows[0]["spend_partial"] is True
        assert enriched.rows[0]["spend_unavailable"] is False, "the figure is not truncated, only incomplete"

    @pytest.mark.asyncio
    async def test_a_measured_zero_below_the_floor_is_still_a_measured_zero(self, handler):
        """The product rule survives: documents settled, and they settled at nothing spent."""
        rows = _candidate_rows(handler, _money_bucket("atlas", money=0.0, settled=3))

        with _patched_projects_port(AsyncMock()):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, BELOW_FLOOR_ONLY)

        assert rows[0]["total_cost_usd"] == 0.0
        assert enriched.rows == []

    def test_a_sub_cent_sum_is_not_rounded_into_a_measured_zero(self, handler):
        """Three states below the floor, three figures - and the middle one is the one rounding erases.

        `read_money_coverage` rounds for display, so a project whose whole pre-floor spend is under half
        a cent becomes the exact `0.0` this table drops rows on. Absent stays absent, a measured zero
        stays a measured zero, and every other figure is still rounded to two places here rather than
        at each card.
        """
        rows = _candidate_rows(
            handler,
            _money_bucket("tiny", money=0.004, settled=2),
            _money_bucket("free", money=0.0, settled=3),
            _money_bucket("quiet", money=None, settled=0),
            _money_bucket("busy", money=12.3456, settled=9),
        )

        assert [row[PRE_FLOOR_MONEY_KEY].figure for row in rows] == [0.004, 0.0, None, 12.35]

    @pytest.mark.asyncio
    async def test_a_sub_cent_pre_floor_spend_keeps_its_row(self, handler):
        """Under half a cent is money spent, and the pre-cutover selector tested the unrounded sum.

        `filter_zero_cost` kept every bucket with `params.cost > 0`, so production prints this row.
        Rounded to `$0.00` on the way to `enriched_spend_rows` it would be dropped as a measured zero -
        a row vanishing below the floor and nowhere else.
        """
        rows = _candidate_rows(handler, _money_bucket("thrifty", money=0.004, settled=2))

        with _patched_projects_port(AsyncMock()):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, BELOW_FLOOR_ONLY)

        assert [row["project_name"] for row in enriched.rows] == ["thrifty"]
        assert enriched.rows[0]["total_cost_usd"] == 0.004
        assert enriched.rows[0]["spend_unavailable"] is False
        assert enriched.rows[0]["spend_partial"] is False

    @pytest.mark.asyncio
    async def test_window_wholly_above_the_floor_reads_only_the_ledger_total(self, handler):
        """One ledger read per project over the whole window, and nothing from the bucket."""
        rows = _candidate_rows(handler, _money_bucket("atlas", money=999.0))
        consumed = _answers(atlas=SpendResult(figure=Decimal("12")))

        with _patched_projects_port(consumed):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, ABOVE_FLOOR_ONLY)

        consumed.assert_awaited_once()
        # The bucket's own money is ignored entirely above the floor - counted there it would be a
        # second copy of the same window's spend, arriving through the other shape.
        assert enriched.rows[0]["total_cost_usd"] == 12.0

    @pytest.mark.asyncio
    async def test_the_kill_switch_off_keeps_the_above_floor_shape_below_the_floor(self, handler):
        """With no boundary at all a pre-floor window behaves exactly as it did before any of this."""
        rows = _candidate_rows(handler, _money_bucket("atlas", money=999.0))
        consumed = _answers(atlas=SpendResult(figure=Decimal("3")))

        with _patched_projects_port(consumed):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, None)

        assert enriched.rows[0]["total_cost_usd"] == 3.0

    @pytest.mark.asyncio
    async def test_crossing_window_shows_one_seamless_total(self, handler):
        rows = _candidate_rows(handler, _money_bucket("atlas", money=40.0))
        consumed = _answers_over(AT_OR_ABOVE_FLOOR_LEG, atlas=SpendResult(figure=Decimal("10")))

        with _patched_projects_port(consumed):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, FLOOR)

        # One figure, both legs in it, and no third state on the wire for a screen to fail to render.
        assert enriched.rows[0]["total_cost_usd"] == 50.0
        assert enriched.rows[0]["spend_unavailable"] is False
        assert enriched.rows[0]["spend_partial"] is False
        assert enriched.rows[0]["spend_missing_amount"] is None
        assert set(enriched.rows[0]) == {
            "project_name",
            "total_cost_usd",
            "spend_unavailable",
            "spend_partial",
            "spend_missing_amount",
        }, "the carrier the pre-floor money arrived on is popped, never published"

    @pytest.mark.asyncio
    async def test_crossing_window_does_not_count_the_above_floor_part_twice(self, handler):
        """The two legs tile `[start, F)` and `[F, end)` and overlap nowhere.

        The aggregation covers the *whole* window, so its money is clipped at `F` in Elasticsearch -
        `TestTheAggregationCarriesItsOwnPreFloorMoney` pins that clause - and the ledger stub asserts
        its own window here. A handler that handed the ledger read the whole crossing period, or that
        footed an unclipped bucket sum beside it, reports the above-floor money twice; both halves
        look entirely plausible on their own and the row reads exact either way.
        """
        rows = _candidate_rows(handler, _money_bucket("atlas", money=40.0))
        consumed = _answers_over(AT_OR_ABOVE_FLOOR_LEG, atlas=SpendResult(figure=Decimal("10")))

        with _patched_projects_port(consumed):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, FLOOR)

        assert consumed.await_args.args[0] == AT_OR_ABOVE_FLOOR_LEG
        assert enriched.rows[0]["total_cost_usd"] == 50.0, "40 below plus 10 above, and neither doubled"

    @pytest.mark.asyncio
    async def test_a_leg_that_found_no_figure_labels_the_row_rather_than_shortening_it(self, handler):
        """The above-floor leg answered and the below-floor one had nothing settled."""
        rows = _candidate_rows(handler, _money_bucket("atlas", money=None, settled=0))
        consumed = _answers_over(AT_OR_ABOVE_FLOOR_LEG, atlas=SpendResult(figure=Decimal("7")))

        with _patched_projects_port(consumed):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, FLOOR)

        assert enriched.rows[0]["total_cost_usd"] == 7.0
        assert enriched.rows[0]["spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_the_bucket_naming_no_project_stays_absent_above_the_floor(self, handler):
        rows = _candidate_rows(handler, _money_bucket(UNATTRIBUTED_PROJECT_KEY, money=5.0))
        consumed = AsyncMock()

        with _patched_projects_port(consumed):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, ABOVE_FLOOR_ONLY)

        consumed.assert_not_awaited()
        assert enriched.rows[0]["total_cost_usd"] is None
        assert enriched.rows[0]["spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_an_unreadable_window_reads_nothing_and_publishes_no_figure(self, handler):
        rows = _candidate_rows(handler, _money_bucket("atlas", money=5.0))
        consumed = AsyncMock()

        with _patched_projects_port(consumed):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_END, PERIOD_END, FLOOR)

        consumed.assert_not_awaited()
        assert enriched.rows[0]["total_cost_usd"] is None
        assert enriched.rows[0]["spend_unavailable"] is True


class TestBucketKeysOnlyTheAggregationCanExpress:
    """The row set is keyed on a *normalised* project, and its money is now keyed on the same thing.

    `_build_projects_spending_aggregation` files a document with no project of its own under its
    user's email, and under `UNATTRIBUTED_PROJECT_KEY` when it has neither - and
    `get_project_for_metric()` returns `'-'` in ordinary cases, so those buckets are a routine part of
    this table rather than an edge of it.

    A read that filtered the raw `attributes.project.keyword` matched **no document** for such a key
    and answered a measured `$0.00`: real money published as nothing spent, and the row then dropped
    from the table by the zero-spend filter, annotation and all. The guard against it tested the key
    for an `'@'` - and `user_email` is written from the free-form `user.username`, so a non-email
    principal walked straight through it. The bucket's own sum has no such failure mode: it is grouped
    by the key it is reported under, whatever that key happens to be.
    """

    @pytest.mark.parametrize(
        "bucket_key",
        [UNATTRIBUTED_PROJECT_KEY, "sam@example.com", "service-account-ci-bot"],
        ids=["the-unattributed-sentinel", "a-user-email-fallback", "a-non-email-principal"],
    )
    @pytest.mark.asyncio
    async def test_a_key_the_script_invented_gets_the_figure_production_prints(self, handler, bucket_key):
        """Including the one the `'@'` heuristic missed: a service account is a username, not an email."""
        rows = _candidate_rows(handler, _money_bucket(bucket_key, money=12.5))

        with _patched_projects_port(AsyncMock()):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, BELOW_FLOOR_ONLY)

        assert enriched.rows[0]["project_name"] == bucket_key
        assert enriched.rows[0]["total_cost_usd"] == 12.5
        assert enriched.rows[0]["spend_unavailable"] is False, "the figure is the bucket's own, and it is whole"
        assert enriched.rows[0]["spend_partial"] is False


class TestTheUserFilterReachesThePreFloorMoney:
    """A user-filtered table foots each row with **that user's** pre-floor spend, not the project's.

    The whole point of taking the money from the candidate aggregation: `users` is applied by
    `_build_query` to that one query, so a figure measured inside it is narrowed by construction. The
    parallel read this replaced never received the filter and footed every row with the project's
    whole spend - larger than production, exact-looking, and flagged by nothing.
    """

    @staticmethod
    def _repository_answering_from_its_own_query(*, whole_project: float, bobs_share: float) -> AsyncMock:
        """A stub that answers from the query it is actually handed.

        Money taken from anywhere but that query cannot pass: the filtered and unfiltered answers are
        different numbers, and only the query says which one this is.
        """

        async def _execute(agg_body: dict) -> dict:
            filtered = "bob@example.com" in json.dumps(agg_body["query"])
            money = bobs_share if filtered else whole_project
            return {"aggregations": {"paginated_results": {"buckets": [_money_bucket("atlas", money=money)]}}}

        return AsyncMock(side_effect=_execute)

    @pytest.mark.asyncio
    async def test_the_pre_floor_figure_is_narrowed_to_the_filtered_user(self, mock_user, mock_repository):
        handler = ProjectHandler(mock_user, mock_repository)
        mock_repository.execute_aggregation_query = self._repository_answering_from_its_own_query(
            whole_project=120.0, bobs_share=30.0
        )

        with _cutover_at(BELOW_FLOOR_ONLY):
            result = await handler.get_projects_spending(
                start_date=PERIOD_START, end_date=PERIOD_END, users=["bob@example.com"]
            )

        assert result["data"]["rows"][0]["total_cost_usd"] == 30.0

    @pytest.mark.asyncio
    async def test_an_unfiltered_table_still_foots_the_whole_project(self, mock_user, mock_repository):
        """The other half of the same proof: without the filter the figure is the project's total."""
        handler = ProjectHandler(mock_user, mock_repository)
        mock_repository.execute_aggregation_query = self._repository_answering_from_its_own_query(
            whole_project=120.0, bobs_share=30.0
        )

        with _cutover_at(BELOW_FLOOR_ONLY):
            result = await handler.get_projects_spending(start_date=PERIOD_START, end_date=PERIOD_END)

        assert result["data"]["rows"][0]["total_cost_usd"] == 120.0


class TestTheUserFilterReachesTheAtOrAboveFloorMoney:
    """The ledger half of the same proof: above the floor each row is a ledger read for that project, which
    answers the project's whole spend within the caller's boundary - so a table filtered to one user
    footed every row with every member's spend until the dashboard's filter reached that read too."""

    @pytest.mark.asyncio
    async def test_the_ledger_read_is_given_the_dashboards_filter(self, handler, mock_repository):
        mock_repository.execute_aggregation_query.return_value = _spending_buckets(("atlas", 7))
        asked = ReportFilter(users=(UserTerm("bob-id", frozenset({"bob@example.com"})),))
        build = AsyncMock(return_value=asked)
        consumed = _answers_over(PERIOD, asked, atlas=SpendResult(figure=Decimal("5")))

        with (
            _cutover_at(ABOVE_FLOOR_ONLY),
            _patched_projects_port(consumed),
            patch("codemie.service.analytics.handlers.project_handler.report_filter_for", new=build),
        ):
            result = await handler.get_projects_spending(
                start_date=PERIOD_START, end_date=PERIOD_END, users=["bob@example.com"]
            )

        build.assert_awaited_once_with(["bob@example.com"], None)
        consumed.assert_awaited_once()
        assert result["data"]["rows"][0]["total_cost_usd"] == 5.0

    @pytest.mark.asyncio
    async def test_an_empty_table_pays_no_identity_lookup(self, handler):
        """With no row there is no project to read for, so building the filter would spend a lookup on
        nothing - and on a database outage count a read failure for a read that never ran."""
        build = AsyncMock()
        consumed = AsyncMock()

        with (
            _patched_projects_port(consumed),
            patch("codemie.service.analytics.handlers.project_handler.report_filter_for", new=build),
        ):
            await handler._enrich_projects_spending(
                [], PERIOD_START, PERIOD_END, ABOVE_FLOOR_ONLY, users=["bob@example.com"]
            )

        build.assert_not_awaited()
        consumed.assert_not_awaited()


class TestAClientSourceFilterReadsNoMoney:
    """No ledger row records which client a call came from, so a client-narrowed table's figure would be
    the unnarrowed one under the narrowed label. The port is not asked, and every row stays listed absent."""

    @pytest.mark.asyncio
    async def test_the_port_is_not_asked_and_the_row_stays_listed_absent(self, handler, mock_repository):
        mock_repository.execute_aggregation_query.return_value = _spending_buckets(("atlas", 7))
        consumed = _answers(atlas=SpendResult(figure=Decimal("5")))

        with _cutover_at(ABOVE_FLOOR_ONLY), _patched_projects_port(consumed):
            result = await handler.get_projects_spending(
                start_date=PERIOD_START, end_date=PERIOD_END, client_source="teams"
            )

        consumed.assert_not_awaited()
        row = result["data"]["rows"][0]
        assert row["project_name"] == "atlas"
        assert row["total_cost_usd"] is None
        assert row["spend_unavailable"] is True
        assert "total_cost_usd" not in result["data"]["totals"]
        assert result["data"]["totals"]["total_cost_usd_spend_unavailable"] is True


class TestTheTwoLegsMeasureTimeOnTwoDifferentClocks:
    """**Known, accepted limitation. These tests assert what this table does, not what it should do.**

    The legs tile the requested window as instants. They do not tile the *rows*: the pre-floor leg is
    an Elasticsearch aggregation clipped on `@timestamp` - metric **emission** time - while the
    at-or-above leg reads `litellm_spend_logs`, which admits a row by `start_time`, when the call
    **started**. A call whose two stamps fall on opposite sides of `F` is claimed by neither leg or by
    both, and the row carries no flag and no portion either way: it foots short, or doubled, and reads
    exact.

    This is `cutover.py`'s exposure reaching a surface, not a second one, and it is bounded the same
    way - by the emission lag `L` (`SPEND_LEDGER_EMISSION_LAG_HOURS`, 24 hours by default). No fix
    belongs here: a metric document has no `start_time` for any query of this table's to partition on,
    and the mitigations are the owner's to choose. `tests/codemie/service/spend/test_cutover.py` pins
    the same two directions at the ledger seam; what these add is that the money surface built on it
    cannot quietly declare the exposure solved.
    """

    @staticmethod
    async def _crossing_row(handler, *, below_floor_sum: float, above_floor: str) -> tuple[dict, list]:
        """`PERIOD` crosses `FLOOR`, so both legs run - each admitting the one call on its own clock."""
        rows = _candidate_rows(handler, _money_bucket("atlas", money=below_floor_sum))
        consumed = _answers_over(AT_OR_ABOVE_FLOOR_LEG, atlas=SpendResult(figure=Decimal(above_floor)))

        with _patched_projects_port(consumed):
            enriched = await handler._enrich_projects_spending(rows, PERIOD_START, PERIOD_END, FLOOR)

        return rows[0], enriched.rows

    @pytest.mark.asyncio
    async def test_a_call_starting_before_the_floor_but_emitted_after_it_is_dropped_by_both_legs(self, handler):
        """The production case: started ten minutes below `F`, its metric document landed five above.

        The project spent $10.00 before the floor on documents that landed there too, and $0.50 more
        on this call. The aggregation's `@timestamp < F` clause excludes the late document, and the
        ledger's `start_time >= F` clause excludes the row, so the leg sums are $10.00 and $0.00.

        **The assertions are the defect, not the requirement.** The missing $0.50 alone could be read
        as a rounding of little consequence; the clean annotation is what makes it serious.
        """
        started_at = FLOOR - timedelta(minutes=10)
        assert FLOOR - DEFAULT_EMISSION_LAG <= started_at < FLOOR, "inside the band L bounds, by construction"

        cell, kept = await self._crossing_row(handler, below_floor_sum=10.0, above_floor="0")

        assert cell["total_cost_usd"] == 10.0, "KNOWN LIMITATION: 0.50 was spent inside the window, neither leg has it"
        assert cell["spend_unavailable"] is False, "KNOWN LIMITATION: the shortfall raises no flag"
        assert cell["spend_partial"] is False, "KNOWN LIMITATION: and labels no dimension"
        assert kept[0]["total_cost_usd"] == 10.0, "KNOWN LIMITATION: a short figure presents as a correct one"

    @pytest.mark.asyncio
    async def test_a_call_starting_after_the_floor_but_emitted_before_it_is_counted_by_both_legs(self, handler):
        """The mirror, which needs the two writers' clocks to disagree - and says nothing when they do.

        A document emitted *before* its own row's `start_time` is not physically possible on one clock;
        it is what two hosts produce when the one stamping the metric runs behind the one stamping the
        ledger row. The $0.50 is inside the aggregation's pre-floor clip **and** inside the ledger's
        at-or-above window, so both legs report it.
        """
        cell, kept = await self._crossing_row(handler, below_floor_sum=0.5, above_floor="0.5")

        assert cell["total_cost_usd"] == 1.0, "KNOWN LIMITATION: 0.50 spent once, claimed by both legs"
        assert kept[0]["spend_unavailable"] is False, "KNOWN LIMITATION: a doubled figure presents as a correct one"
        assert kept[0]["spend_partial"] is False
