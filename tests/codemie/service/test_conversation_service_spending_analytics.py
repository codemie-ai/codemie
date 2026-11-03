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

"""Conversation spending analytics reads its money from the spend port, per assistant, in scope."""

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from codemie.service.conversation_service import ConversationService, SpendingAnalyticsResult
from codemie.service.spend.coverage import Portion, SpendDimension, SpendResult, TimeRange
from codemie.service.spend.ledger import UserTerm

MODULE = "codemie.service.conversation_service"

PERIOD = TimeRange(datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 1, tzinfo=timezone.utc))
ALICE = UserTerm(user_id="user-1", aliases=frozenset({"alice", "alice@example.com"}))


def _run_now(coro, *, timeout=30.0, on_timeout="operation"):
    """Stand-in for `run_coroutine_blocking` that runs the coroutine here and now."""
    return asyncio.run(coro)


def _timed_out(coro, *, timeout=30.0, on_timeout="operation"):
    """Stand-in for a `run_coroutine_blocking` that timed out: no value, and it does **not** raise."""
    coro.close()
    return None


def _metric(conversation_id, input_tokens=10, output_tokens=5):
    """A ConversationMetrics row carrying activity only - money no longer travels this way."""
    metric = MagicMock()
    metric.conversation_id = conversation_id
    metric.total_input_tokens = input_tokens
    metric.total_output_tokens = output_tokens
    return metric


@contextmanager
def _rows(joined):
    """The `(ConversationMetrics, assistant_ids)` pairs the join returns; yields the session it ran on."""
    session = MagicMock()
    session.exec.return_value.all.return_value = joined
    fake = MagicMock()
    fake.__enter__ = MagicMock(return_value=session)
    fake.__exit__ = MagicMock(return_value=False)
    with (
        patch(f"{MODULE}.get_session", return_value=fake),
        patch.object(ConversationService, "_batch_fetch_assistant_names", return_value={}),
    ):
        yield session


@contextmanager
def _totals():
    """The activity totals row; yields the session the totals statement ran on."""
    session = MagicMock()
    session.exec.return_value.one.return_value = MagicMock(total_input=10, total_output=20, total_conversations=3)
    fake = MagicMock()
    fake.__enter__ = MagicMock(return_value=session)
    fake.__exit__ = MagicMock(return_value=False)
    with patch(f"{MODULE}.get_session", return_value=fake):
        yield session


def _where(session) -> str:
    """The WHERE clause of the statement the session executed, as PostgreSQL would receive it."""
    statement = session.exec.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    return sql.split("WHERE", 1)[1].split("ORDER BY", 1)[0]


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


class TestTheAssistantBreakdownKeepsThisReportsOwnFiltering:
    def test_every_assistant_is_read_with_the_whole_scope(self, port):
        port.aggregate_composed = AsyncMock(
            side_effect=[SpendResult(figure=Decimal("4")), SpendResult(figure=Decimal("11"))]
        )
        joined = [(_metric("c-1"), ["as-a"]), (_metric("c-2"), ["as-b"])]

        with _rows(joined), patch(f"{MODULE}.run_coroutine_blocking", _run_now):
            breakdown = ConversationService._get_assistant_spending_breakdown(
                project="acme", user_term=ALICE, period=PERIOD
            )

        assert [row.money_spent for row in breakdown] == [11.0, 4.0]
        scoped = [call.args[1] for call in port.aggregate_composed.await_args_list]
        assert {scope.dimension.value for scope in scoped} == {"as-a", "as-b"}
        for scope in scoped:
            assert scope.user is ALICE
            assert scope.project == "acme"
            assert scope.dimension.dimension is SpendDimension.ASSISTANT
        port.aggregate_by_dimension.assert_not_awaited()

    def test_a_personal_projects_owner_rides_on_every_assistants_scope(self, port):
        """UA-H-D3 (pass H): the project term alone matches none of a personal project's rows."""
        port.aggregate_composed = AsyncMock(return_value=SpendResult(figure=Decimal("1")))

        with _rows([(_metric("c-1"), ["as-a"])]), patch(f"{MODULE}.run_coroutine_blocking", _run_now):
            ConversationService._get_assistant_spending_breakdown(
                project="bob@example.com", user_term=None, period=PERIOD, project_owners=("owner-1",)
            )

        (scope,) = [call.args[1] for call in port.aggregate_composed.await_args_list]
        assert (scope.project, scope.project_owners) == ("bob@example.com", ("owner-1",))

    def test_activity_is_still_aggregated_per_assistant(self, port):
        port.aggregate_composed = AsyncMock(return_value=SpendResult(figure=Decimal("6")))
        # One conversation touching two assistants, then a second on one of them.
        joined = [
            (_metric("c-1", input_tokens=10, output_tokens=1), ["as-a", "as-b"]),
            (_metric("c-2", input_tokens=20, output_tokens=2), ["as-a"]),
        ]

        with _rows(joined), patch(f"{MODULE}.run_coroutine_blocking", _run_now):
            breakdown = ConversationService._get_assistant_spending_breakdown(user_term=None, period=PERIOD)

        by_id = {row.dimension_id: row for row in breakdown}
        assert (by_id["as-a"].input_tokens, by_id["as-a"].conversation_count) == (30, 2)
        assert (by_id["as-b"].input_tokens, by_id["as-b"].conversation_count) == (10, 1)
        # The same figure divided by two different counts - the port is asked once per assistant.
        assert by_id["as-a"].average_cost_per_item == 3.0
        assert by_id["as-b"].average_cost_per_item == 6.0


class TestAnAbsentAssistantFigureIsNeverAZero:
    def test_no_figure_is_none_and_carries_its_qualification(self, port):
        port.aggregate_composed = AsyncMock(return_value=SpendResult.no_figure(PERIOD))

        with _rows([(_metric("c-1"), ["as-a"])]), patch(f"{MODULE}.run_coroutine_blocking", _run_now):
            row = ConversationService._get_assistant_spending_breakdown(user_term=None, period=PERIOD)[0]

        assert row.money_spent is None
        assert row.spend_unavailable is True
        assert row.average_cost_per_item is None

    def test_a_dimension_hole_is_named_but_not_sized(self, port):
        # The residue is the platform's unbucketed money for the dimension, not narrowed to this row's
        # user or project, so sizing it would publish other people's spend.
        port.aggregate_composed = AsyncMock(
            return_value=SpendResult(
                figure=Decimal("9"), partial={SpendDimension.ASSISTANT: Portion(spend=Decimal("1.5"), rows=3)}
            )
        )

        with _rows([(_metric("c-1"), ["as-a"])]), patch(f"{MODULE}.run_coroutine_blocking", _run_now):
            row = ConversationService._get_assistant_spending_breakdown(user_term=None, period=PERIOD)[0]

        assert row.money_spent == 9.0
        assert row.spend_partial is True
        assert row.spend_missing_amount is None

    def test_a_project_hole_is_named_but_not_sized(self, port):
        # The project portion is the platform's project-less money below the identity floor: on one
        # row of one project it would read as that row's own unattributed spend (EPMCDME-15237).
        port.aggregate_composed = AsyncMock(
            return_value=SpendResult(
                figure=Decimal("9"), partial={SpendDimension.PROJECT: Portion(spend=Decimal("136040.59"), rows=9)}
            )
        )

        with _rows([(_metric("c-1"), ["as-a"])]), patch(f"{MODULE}.run_coroutine_blocking", _run_now):
            row = ConversationService._get_assistant_spending_breakdown(project="acme", user_term=ALICE, period=PERIOD)[
                0
            ]

        assert row.money_spent == 9.0
        assert row.spend_partial is True
        assert row.spend_missing_amount is None

    def test_a_timeout_degrades_every_figure_instead_of_raising(self, port):
        port.aggregate_composed = AsyncMock(return_value=SpendResult(figure=Decimal("9")))

        with _rows([(_metric("c-1"), ["as-a"])]), patch(f"{MODULE}.run_coroutine_blocking", _timed_out):
            row = ConversationService._get_assistant_spending_breakdown(user_term=None, period=PERIOD)[0]

        assert row.money_spent is None
        assert row.spend_unavailable is True
        assert row.conversation_count == 1

    def test_no_window_means_the_port_is_not_asked_at_all(self, port):
        port.aggregate_composed = AsyncMock(return_value=SpendResult(figure=Decimal("9")))

        with _rows([(_metric("c-1"), ["as-a"])]), patch(f"{MODULE}.run_coroutine_blocking", _run_now):
            row = ConversationService._get_assistant_spending_breakdown(user_term=None, period=None)[0]

        port.aggregate_composed.assert_not_awaited()
        assert row.money_spent is None
        assert row.spend_unavailable is True


class TestTheActivityQueryNoLongerCarriesMoney:
    def test_the_metrics_answer_has_no_money_key(self):
        session = MagicMock()
        session.exec.return_value.one.return_value = MagicMock(total_input=10, total_output=20, total_conversations=3)
        fake = MagicMock()
        fake.__enter__ = MagicMock(return_value=session)
        fake.__exit__ = MagicMock(return_value=False)

        with patch(f"{MODULE}.get_session", return_value=fake):
            metrics = ConversationService._get_conversation_metrics_data()

        assert metrics == {"total_input_tokens": 10, "total_output_tokens": 20, "total_conversations": 3}
        assert "total_money_spent" not in metrics

    def test_the_result_model_offers_no_money_total_to_add(self):
        assert "total_money_spent" not in SpendingAnalyticsResult.model_fields


class TestTheActivityCountsTheSamePersonAsTheMoney:
    """UA-H-D1 (pass H): the money was read for the resolved person while the counts matched the raw
    `user_name` against the stored display name - none at all for an email, everybody sharing the name
    for a display name. Both halves are now keyed on the one resolved `users.id`."""

    def test_the_totals_are_counted_for_the_resolved_id_and_not_by_display_name(self):
        with _totals() as session:
            ConversationService.get_spending_analytics(user_term=ALICE, period=PERIOD)

        where = _where(session)
        assert "conversation_metrics.user_id = 'user-1'" in where
        assert "user_name" not in where

    def test_the_assistant_breakdown_is_counted_for_the_resolved_id_and_not_by_display_name(self, port):
        port.aggregate_composed = AsyncMock(return_value=SpendResult(figure=Decimal("1")))

        with _rows([(_metric("c-1"), ["as-a"])]) as session, patch(f"{MODULE}.run_coroutine_blocking", _run_now):
            ConversationService._get_assistant_spending_breakdown(user_term=ALICE, period=PERIOD)

        where = _where(session)
        assert "conversation_metrics.user_id = 'user-1'" in where
        assert "user_name" not in where

    def test_no_user_term_puts_no_user_filter_on_the_activity(self):
        with _totals() as session:
            ConversationService.get_spending_analytics(project="acme", user_term=None, period=PERIOD)

        where = _where(session)
        assert "user_id" not in where
        assert "conversation_metrics.project = 'acme'" in where
