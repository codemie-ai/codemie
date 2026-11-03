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

"""The weekly spending chart: every bar is the spend port's answer for one walked bucket."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.security.user import User
from codemie.service.analytics.handlers.engagement_handler import EngagementHandler
from codemie.service.spend.coverage import Portion, SpendDimension, SpendResult, TimeRange
from codemie.service.spend.ledger import BUCKET_WIDTH_3_HOURS, ReportFilter, bucket_starts
from codemie.service.spend.traffic_class import TrafficClass

MODULE = "codemie.service.analytics.handlers.engagement_handler"

SERIES_OF_CLASS = {
    TrafficClass.ASSISTANT: "assistants_spent",
    TrafficClass.WORKFLOW: "workflows_spent",
    TrafficClass.DATASOURCE: "datasources_spent",
    TrafficClass.CLI: "cli_spent",
}


@pytest.fixture
def mock_user():
    """Create mock user."""
    user = MagicMock(spec=User)
    user.project_names = []
    user.admin_project_names = []
    user.is_global_user = False
    user.is_admin = False
    user.is_auditor = False
    user.id = "engagement-test-user"
    return user


@pytest.fixture
def mock_repository():
    """Create mock repository."""
    return MagicMock(spec=MetricsElasticRepository)


@pytest.fixture
def handler(mock_user, mock_repository):
    """Create handler with mocked dependencies."""
    return EngagementHandler(mock_user, mock_repository)


@pytest.fixture
def report_filter():
    """The resolved request filter the handler is expected to hand the port unchanged."""
    return ReportFilter(users=(), projects=("proj-a",))


def _first_bucket_split(results_by_class: dict[TrafficClass, SpendResult]):
    """A port answer that places the given results on the walk's **first** bucket only.

    Built from the period the handler actually passed, so the keys are the ones its own walk
    produces: a split keyed on any other instant would silently render as four absences and every
    assertion about a figure would pass for the wrong reason.
    """

    async def _answer(period, width, **_kwargs):
        first = bucket_starts(period, width)[0]
        return {(first, traffic_class): result for traffic_class, result in results_by_class.items()}

    return AsyncMock(side_effect=_answer)


def _patched(aggregate, filter_result):
    """Patch both port reads the handler makes: the bucketed spend and the request filter."""
    return (
        patch(f"{MODULE}.spend_service.aggregate_by_time_bucket", aggregate),
        patch(f"{MODULE}.report_filter_for", AsyncMock(return_value=filter_result)),
    )


async def _run(handler, aggregate, filter_result, users=None, projects=None, client_source=None):
    aggregate_patch, filter_patch = _patched(aggregate, filter_result)
    with aggregate_patch, filter_patch as filter_for:
        response = await handler.get_weekly_spending(users=users, projects=projects, client_source=client_source)
    return response, filter_for


def _rows(response) -> list[dict]:
    return response["data"]["rows"]


class TestThePortRead:
    """How the chart asks the spend port for its money."""

    @pytest.mark.asyncio
    async def test_the_port_is_asked_once_for_three_hour_buckets_over_the_last_seven_days(self, handler, report_filter):
        """One read for the whole chart, at the grain the widget draws."""
        aggregate = _first_bucket_split({})

        await _run(handler, aggregate, report_filter)

        aggregate.assert_awaited_once()
        period, width = aggregate.await_args.args
        assert width == BUCKET_WIDTH_3_HOURS
        assert isinstance(period, TimeRange)
        # The window is fixed at seven days and ignores the dashboard filter, so it always spans
        # more than six days and never runs past the present moment.
        now = datetime.now(timezone.utc)
        assert period.end <= now + timedelta(seconds=5)
        assert timedelta(days=6) < period.end - period.start <= timedelta(days=8)

    @pytest.mark.asyncio
    async def test_the_access_boundary_comes_from_the_pipeline(self, handler, report_filter):
        """The figure is narrowed by the same context the dashboard's own queries are built under."""
        aggregate = _first_bucket_split({})

        await _run(handler, aggregate, report_filter)

        assert aggregate.await_args.kwargs["access"] == handler._pipeline.access_scope

    @pytest.mark.asyncio
    async def test_the_requests_users_and_projects_reach_the_port_as_the_resolved_filter(self, handler, report_filter):
        """The screen's filter narrows the money exactly as it narrows the rows."""
        aggregate = _first_bucket_split({})

        _, filter_for = await _run(handler, aggregate, report_filter, users=["alice"], projects=["proj-a"])

        filter_for.assert_awaited_once_with(["alice"], ["proj-a"])
        assert aggregate.await_args.kwargs["report_filter"] is report_filter

    @pytest.mark.asyncio
    async def test_no_elasticsearch_query_is_issued(self, handler, mock_repository, report_filter):
        """The chart's money no longer comes from a metric-name aggregation."""
        await _run(handler, _first_bucket_split({}), report_filter)

        mock_repository.execute_aggregation_query.assert_not_called()


class TestTheRows:
    """One row per walked bucket, and what each row carries."""

    @pytest.mark.asyncio
    async def test_every_walked_bucket_becomes_one_row_keyed_on_its_iso_start(self, handler, report_filter):
        """Rows are the port's walk - including the buckets nobody spent in - in walk order."""
        aggregate = _first_bucket_split({})

        response, _ = await _run(handler, aggregate, report_filter)

        walked = bucket_starts(aggregate.await_args.args[0], BUCKET_WIDTH_3_HOURS)
        assert [row["time"] for row in _rows(response)] == [bucket.isoformat() for bucket in walked]

    @pytest.mark.asyncio
    async def test_the_bucket_start_carries_its_utc_offset(self, handler, report_filter):
        """The widget reads an offset-less timestamp in the browser's zone, which shifts every bar."""
        response, _ = await _run(handler, _first_bucket_split({}), report_filter)

        assert _rows(response)[0]["time"].endswith("+00:00")

    @pytest.mark.asyncio
    async def test_each_series_draws_its_own_traffic_class(self, handler, report_filter):
        """A series is one traffic class - distinct figures must not land on the wrong line."""
        figures = {
            TrafficClass.ASSISTANT: SpendResult(figure=Decimal("1.11")),
            TrafficClass.WORKFLOW: SpendResult(figure=Decimal("2.22")),
            TrafficClass.DATASOURCE: SpendResult(figure=Decimal("3.33")),
            TrafficClass.CLI: SpendResult(figure=Decimal("4.44")),
        }

        response, _ = await _run(handler, _first_bucket_split(figures), report_filter)

        first = _rows(response)[0]
        assert {series: first[series] for series in SERIES_OF_CLASS.values()} == {
            "assistants_spent": 1.11,
            "workflows_spent": 2.22,
            "datasources_spent": 3.33,
            "cli_spent": 4.44,
        }


class TestAbsenceIsNotZero:
    """The distinction the whole rewrite exists to keep."""

    @pytest.mark.asyncio
    async def test_a_class_the_port_did_not_answer_for_is_none(self, handler, report_filter):
        """An unanswered series is a gap in the chart, never a bar at zero."""
        response, _ = await _run(
            handler,
            _first_bucket_split({TrafficClass.CLI: SpendResult(figure=Decimal("5"))}),
            report_filter,
        )

        first = _rows(response)[0]
        assert first["assistants_spent"] is None
        assert first["workflows_spent"] is None
        assert first["datasources_spent"] is None
        assert first["cli_spent"] == 5.0

    @pytest.mark.asyncio
    async def test_a_no_figure_answer_is_none_and_flagged_unavailable(self, handler, report_filter):
        """Below the coverage floor the port answers with no figure at all."""
        period = TimeRange(
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 8, tzinfo=timezone.utc),
        )
        response, _ = await _run(
            handler,
            _first_bucket_split({TrafficClass.ASSISTANT: SpendResult.no_figure(period)}),
            report_filter,
        )

        first = _rows(response)[0]
        assert first["assistants_spent"] is None
        assert first["assistants_spent_spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_a_measured_zero_stays_zero(self, handler, report_filter):
        """Nothing spent in a covered bucket is a bar at zero, and must not read as a gap."""
        response, _ = await _run(
            handler,
            _first_bucket_split({TrafficClass.WORKFLOW: SpendResult(figure=Decimal("0"))}),
            report_filter,
        )

        first = _rows(response)[0]
        assert first["workflows_spent"] == 0.0
        assert first["workflows_spent"] is not None

    @pytest.mark.asyncio
    async def test_a_client_source_filter_keeps_every_bucket_and_draws_every_series_absent(
        self, handler, report_filter
    ):
        """No ledger row records a client, so the port is not asked: its figure would be the unnarrowed
        one under the narrowed chart's label. The week is still walked, so the chart keeps its buckets as
        gaps rather than losing them or drawing zero bars."""
        # Pinned so the walk this test expects is the one the handler makes, whatever the clock says.
        week = TimeRange(datetime(2026, 9, 24, tzinfo=timezone.utc), datetime(2026, 10, 1, 7, 12, tzinfo=timezone.utc))
        aggregate = _first_bucket_split({TrafficClass.CLI: SpendResult(figure=Decimal("5"))})

        with patch(f"{MODULE}.TimeParser.parse", return_value=(week.start, week.end)):
            response, _ = await _run(handler, aggregate, report_filter, client_source="teams")

        aggregate.assert_not_awaited()
        rows = _rows(response)
        assert [row["time"] for row in rows] == [
            bucket.isoformat() for bucket in bucket_starts(week, BUCKET_WIDTH_3_HOURS)
        ]
        for row in rows:
            for series in SERIES_OF_CLASS.values():
                assert row[series] is None
                assert row[f"{series}_spend_unavailable"] is True


class TestTheQualificationKeys:
    """What tells a reader a bar is short."""

    @pytest.mark.asyncio
    async def test_a_partial_series_names_its_hole_under_its_own_prefix(self, handler, report_filter):
        """Four figures share a row, so an unprefixed triple could not say which one is short."""
        partial = SpendResult(
            figure=Decimal("2.50"),
            partial={SpendDimension.TRAFFIC_CLASS: Portion(spend=Decimal("0.75"), rows=3)},
        )

        response, _ = await _run(handler, _first_bucket_split({TrafficClass.CLI: partial}), report_filter)

        first = _rows(response)[0]
        assert first["cli_spent"] == 2.5
        assert first["cli_spent_spend_partial"] is True
        assert first["cli_spent_spend_missing_amount"] == 0.75
        # The other three series were answered by nothing partial, so their own triples stay clean.
        assert first["workflows_spent_spend_partial"] is False

    @pytest.mark.asyncio
    async def test_an_exact_series_carries_a_clean_triple(self, handler, report_filter):
        """A figure nothing qualifies must render bare, or every bar would look suspect."""
        response, _ = await _run(
            handler,
            _first_bucket_split({TrafficClass.ASSISTANT: SpendResult(figure=Decimal("9.99"))}),
            report_filter,
        )

        first = _rows(response)[0]
        assert first["assistants_spent_spend_unavailable"] is False
        assert first["assistants_spent_spend_partial"] is False
        assert first["assistants_spent_spend_missing_amount"] is None


class TestTheResponseShape:
    """The contract the widget is already written against."""

    @pytest.mark.asyncio
    async def test_the_response_keeps_its_columns_pagination_and_fixed_timeframe(self, handler, report_filter):
        """The rewrite moved where the money comes from, not what the endpoint returns."""
        response, _ = await _run(handler, _first_bucket_split({}), report_filter)

        rows = _rows(response)
        assert [column["id"] for column in response["data"]["columns"]] == [
            "time",
            "assistants_spent",
            "workflows_spent",
            "datasources_spent",
            "cli_spent",
        ]
        assert response["fixed_timeframe"] == "Last 1 week"
        assert response["pagination"] == {
            "page": 0,
            "per_page": len(rows),
            "total_count": len(rows),
            "has_more": False,
        }

    @pytest.mark.asyncio
    async def test_the_metadata_reports_the_fixed_window_and_the_requests_filters(self, handler, report_filter):
        """A surface answering without Elasticsearch still owes the caller the same metadata block."""
        response, _ = await _run(handler, _first_bucket_split({}), report_filter, users=["alice"], projects=["proj-a"])

        filters_applied = response["metadata"]["filters_applied"]
        assert filters_applied["time_period"] == "last_7_days"
        assert filters_applied["users"] == ["alice"]
        assert filters_applied["projects"] == ["proj-a"]
