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

"""Workflow spending analytics reads its money from the spend port, per workflow, in scope."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from codemie.service.spend.coverage import Portion, SpendDimension, SpendResult, TimeRange
from codemie.service.spend.ledger import UserTerm
from codemie.service.workflow_service import WorkflowService

MODULE = "codemie.service.workflow_service"

PERIOD = TimeRange(datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 1, tzinfo=timezone.utc))
ALICE = UserTerm(user_id="user-1", aliases=frozenset({"alice", "alice@example.com"}))


def _run_now(coro, *, timeout=30.0, on_timeout="operation"):
    """Stand-in for `run_coroutine_blocking` that runs the coroutine here and now."""
    return asyncio.run(coro)


def _timed_out(coro, *, timeout=30.0, on_timeout="operation"):
    """Stand-in for a `run_coroutine_blocking` that timed out: no value, and it does **not** raise."""
    coro.close()
    return None


def _session(rows):
    """A session whose grouped activity query returns `rows`.

    Each row is `(workflow_id, workflow_name, input_tokens, output_tokens, execution_count)` - the
    shape the group-by produces now that no money column is selected.
    """
    session = MagicMock()
    session.exec.return_value.all.return_value = rows
    return session


@pytest.fixture
def port():
    """The spend port, with the two reads that would drop this report's filters wired to fail."""
    service = MagicMock()
    service.aggregate_by_dimension = AsyncMock(
        side_effect=AssertionError(
            "aggregate_by_dimension is a dashboard table's read and drops this report's own scope"
        )
    )
    service.aggregate = AsyncMock(side_effect=AssertionError("aggregate(period, None) is the platform total"))
    with patch(f"{MODULE}.spend_service", service):
        yield service


class TestTheBreakdownKeepsThisReportsOwnFilteringOnEveryWorkflow:
    """`aggregate_by_dimension(period, WORKFLOW, ids)` is a dashboard table's read: it takes no
    `ComposedScope`, so it would report every other user's spend on the same workflow as if it were this
    user's. One composed read per id keeps the conjunction."""

    def test_every_workflow_is_read_with_the_whole_scope(self, port):
        port.aggregate_composed = AsyncMock(
            side_effect=[SpendResult(figure=Decimal("3")), SpendResult(figure=Decimal("9"))]
        )
        rows = [("wf-a", "Alpha", 10, 5, 2), ("wf-b", "Beta", 20, 10, 4)]

        with patch(f"{MODULE}.run_coroutine_blocking", _run_now):
            breakdown = WorkflowService._get_workflow_breakdown(
                _session(rows), [], user_term=ALICE, project="acme", period=PERIOD
            )

        assert [row["money_spent"] for row in breakdown] == [9.0, 3.0]
        assert port.aggregate_composed.await_count == 2
        scoped = [call.args[1] for call in port.aggregate_composed.await_args_list]
        assert {scope.dimension.value for scope in scoped} == {"wf-a", "wf-b"}
        for scope in scoped:
            # The report's own filters ride on every id, never only on the SQL half.
            assert scope.user is ALICE
            assert scope.project == "acme"
            assert scope.dimension.dimension is SpendDimension.WORKFLOW
        port.aggregate_by_dimension.assert_not_awaited()

    def test_a_personal_projects_owner_rides_on_every_workflows_scope(self, port):
        """UA-H-D3 (pass H): the project term alone matches none of a personal project's rows."""
        port.aggregate_composed = AsyncMock(return_value=SpendResult(figure=Decimal("1")))

        with patch(f"{MODULE}.run_coroutine_blocking", _run_now):
            WorkflowService._get_workflow_breakdown(
                _session([("wf-a", "Alpha", 1, 1, 1)]),
                [],
                user_term=None,
                project="bob@example.com",
                period=PERIOD,
                project_owners=("owner-1",),
            )

        (scope,) = [call.args[1] for call in port.aggregate_composed.await_args_list]
        assert (scope.project, scope.project_owners) == ("bob@example.com", ("owner-1",))

    def test_the_round_trip_count_is_the_breakdowns_own_row_count(self, port):
        port.aggregate_composed = AsyncMock(return_value=SpendResult(figure=Decimal("1")))
        rows = [(f"wf-{index}", f"W{index}", 1, 1, 1) for index in range(7)]

        with patch(f"{MODULE}.run_coroutine_blocking", _run_now):
            WorkflowService._get_workflow_breakdown(_session(rows), [], user_term=ALICE, project=None, period=PERIOD)

        assert port.aggregate_composed.await_count == len(rows)

    def test_rows_are_ordered_by_the_port_figure_and_an_absent_one_sorts_last(self, port):
        port.aggregate_composed = AsyncMock(
            side_effect=[
                SpendResult(figure=Decimal("1")),
                SpendResult.no_figure(PERIOD),
                SpendResult(figure=Decimal("8")),
            ]
        )
        rows = [("wf-a", "Alpha", 1, 1, 1), ("wf-b", "Beta", 1, 1, 1), ("wf-c", "Gamma", 1, 1, 1)]

        with patch(f"{MODULE}.run_coroutine_blocking", _run_now):
            breakdown = WorkflowService._get_workflow_breakdown(
                _session(rows), [], user_term=None, project=None, period=PERIOD
            )

        assert [row["dimension_id"] for row in breakdown] == ["wf-c", "wf-a", "wf-b"]
        # The absent figure is last because it is absent, not because it was read as a zero.
        assert breakdown[-1]["money_spent"] is None

    def test_activity_still_comes_from_the_group_by(self, port):
        port.aggregate_composed = AsyncMock(return_value=SpendResult(figure=Decimal("6")))
        rows = [("wf-a", "Alpha", 111, 222, 3)]

        with patch(f"{MODULE}.run_coroutine_blocking", _run_now):
            breakdown = WorkflowService._get_workflow_breakdown(
                _session(rows), [], user_term=None, project=None, period=PERIOD
            )

        row = breakdown[0]
        assert (row["input_tokens"], row["output_tokens"], row["workflow_execution_count"]) == (111, 222, 3)
        assert row["dimension_name"] == "Alpha"
        assert row["money_spent"] == 6.0
        assert row["average_cost_per_item"] == 2.0


class TestAnAbsentWorkflowFigureIsNeverAZero:
    def test_no_figure_is_none_and_carries_its_qualification(self, port):
        port.aggregate_composed = AsyncMock(return_value=SpendResult.no_figure(PERIOD))
        rows = [("wf-a", "Alpha", 1, 1, 2)]

        with patch(f"{MODULE}.run_coroutine_blocking", _run_now):
            row = WorkflowService._get_workflow_breakdown(
                _session(rows), [], user_term=None, project=None, period=PERIOD
            )[0]

        assert row["money_spent"] is None
        assert row["spend_unavailable"] is True
        # An unknown numerator makes the per-run cost unknown too - not zero.
        assert row["average_cost_per_item"] is None

    def test_a_dimension_hole_is_named_but_not_sized(self, port):
        # The residue is the platform's unbucketed money for the dimension, not narrowed to this row's
        # user or project, so sizing it would publish other people's spend.
        port.aggregate_composed = AsyncMock(
            return_value=SpendResult(
                figure=Decimal("10"), partial={SpendDimension.WORKFLOW: Portion(spend=Decimal("4"), rows=2)}
            )
        )
        rows = [("wf-a", "Alpha", 1, 1, 5)]

        with patch(f"{MODULE}.run_coroutine_blocking", _run_now):
            row = WorkflowService._get_workflow_breakdown(
                _session(rows), [], user_term=None, project=None, period=PERIOD
            )[0]

        assert row["money_spent"] == 10.0
        assert row["spend_partial"] is True
        assert row["spend_missing_amount"] is None
        # A qualified figure still yields a ratio, and it inherits the row's qualification.
        assert row["average_cost_per_item"] == 2.0

    def test_a_project_hole_is_named_but_not_sized(self, port):
        # The project portion is the platform's project-less money below the identity floor: on one
        # row of one project it would read as that row's own unattributed spend (EPMCDME-15237).
        port.aggregate_composed = AsyncMock(
            return_value=SpendResult(
                figure=Decimal("10"), partial={SpendDimension.PROJECT: Portion(spend=Decimal("136040.59"), rows=9)}
            )
        )
        rows = [("wf-a", "Alpha", 1, 1, 5)]

        with patch(f"{MODULE}.run_coroutine_blocking", _run_now):
            row = WorkflowService._get_workflow_breakdown(
                _session(rows), [], user_term=ALICE, project="acme", period=PERIOD
            )[0]

        assert row["money_spent"] == 10.0
        assert row["spend_partial"] is True
        assert row["spend_missing_amount"] is None

    def test_a_timeout_degrades_every_figure_instead_of_raising(self, port):
        port.aggregate_composed = AsyncMock(return_value=SpendResult(figure=Decimal("10")))
        rows = [("wf-a", "Alpha", 1, 1, 1), ("wf-b", "Beta", 2, 2, 2)]

        with patch(f"{MODULE}.run_coroutine_blocking", _timed_out):
            breakdown = WorkflowService._get_workflow_breakdown(
                _session(rows), [], user_term=None, project=None, period=PERIOD
            )

        assert [row["money_spent"] for row in breakdown] == [None, None]
        assert all(row["spend_unavailable"] for row in breakdown)
        # The activity half still answers.
        assert {row["workflow_execution_count"] for row in breakdown} == {1, 2}

    def test_no_window_means_the_port_is_not_asked_at_all(self, port):
        port.aggregate_composed = AsyncMock(return_value=SpendResult(figure=Decimal("10")))
        rows = [("wf-a", "Alpha", 1, 1, 1)]

        with patch(f"{MODULE}.run_coroutine_blocking", _run_now):
            row = WorkflowService._get_workflow_breakdown(
                _session(rows), [], user_term=None, project=None, period=None
            )[0]

        port.aggregate_composed.assert_not_awaited()
        assert row["money_spent"] is None
        assert row["spend_unavailable"] is True


class TestTheActivityQueryNoLongerCarriesMoney:
    def test_the_totals_answer_has_no_money_key(self):
        session = MagicMock()
        session.exec.return_value.one.return_value = MagicMock(total_input=10, total_output=20, total_executions=3)

        totals = WorkflowService._get_workflow_totals(session, [])

        assert totals == {
            "total_input_tokens": 10,
            "total_output_tokens": 20,
            "total_workflow_executions": 3,
        }
        assert "total_money_spent" not in totals


NO_ACTIVITY = {"total_input_tokens": 0, "total_output_tokens": 0, "total_workflow_executions": 0}


def _sql(filters) -> list[str]:
    return [str(f.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})) for f in filters]


class TestTheActivityCountsTheSamePersonAsTheMoney:
    """UA-H-D1 (pass H): executions were matched on `created_by.name` (a display name) while the money
    was read for the resolved person. Both halves are now keyed on the one resolved `users.id`."""

    def test_the_totals_are_filtered_on_the_resolved_id_and_not_on_the_display_name(self):
        with (
            patch(f"{MODULE}.Session"),
            patch.object(WorkflowService, "_get_workflow_totals", return_value=NO_ACTIVITY) as totals,
        ):
            WorkflowService.get_workflow_spending_analytics(project="acme", user_term=ALICE)

        sql = _sql(totals.call_args.args[1])
        assert "(workflow_executions.created_by ->> 'user_id') = 'user-1'" in sql
        assert "workflow_executions.project = 'acme'" in sql
        assert not any("'name'" in clause for clause in sql)

    def test_no_user_term_puts_no_user_filter_on_the_activity(self):
        with (
            patch(f"{MODULE}.Session"),
            patch.object(WorkflowService, "_get_workflow_totals", return_value=NO_ACTIVITY) as totals,
        ):
            WorkflowService.get_workflow_spending_analytics(project="acme", user_term=None)

        assert _sql(totals.call_args.args[1]) == ["workflow_executions.project = 'acme'"]
