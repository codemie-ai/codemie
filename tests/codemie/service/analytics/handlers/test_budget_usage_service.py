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

"""Unit tests for budget_usage_service helpers and BudgetUsageService."""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.analytics.handlers.budget_usage_service import (
    BudgetUsageService,
    _budget_category,
    _build_budget_usage_rows,
    _build_spending_row,
    _calculate_time_until_reset,
    _expand_project_rows,
    _get_key_spending_columns,
)
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.spend.coverage import Portion, SpendDimension, SpendResult, TimeRange
from codemie.service.spend.identity_bridge import ResolvedIdentity
from codemie.service.spend.ledger import PersonalHolder

MODULE = "codemie.service.analytics.handlers.budget_usage_service"

RESET_AT = "2099-07-01T00:00:00Z"
PERIOD = TimeRange(datetime(2099, 6, 1, tzinfo=timezone.utc), datetime(2099, 7, 1, tzinfo=timezone.utc))
ALIASES = frozenset({"alice", "alice@example.com"})
#: A sub-`F` slice of the requested range - what a truncated total is flagged with.
SUB_FLOOR = TimeRange(datetime(2099, 6, 1, tzinfo=timezone.utc), datetime(2099, 6, 10, tzinfo=timezone.utc))
#: Where an overdue RESET_AT moves once reconciled, and the period that reset closes.
MOVED_RESET_AT = "2099-08-01T00:00:00Z"
MOVED_PERIOD = TimeRange(datetime(2099, 7, 1, tzinfo=timezone.utc), datetime(2099, 8, 1, tzinfo=timezone.utc))


def _figure(amount: str) -> SpendResult:
    return SpendResult(figure=Decimal(amount))


def _assignment(budget_id: str, category: str = "platform", spend_reset_at: datetime | None = None):
    return SimpleNamespace(budget_id=budget_id, category=category, spend_reset_at=spend_reset_at)


def _budget(max_budget=100.0, reset_at: str | None = RESET_AT, duration: str = "30d", **record):
    """A budget with no record of a reset CodeMie set, unless `record` gives one."""
    return SimpleNamespace(
        max_budget=max_budget,
        budget_reset_at=reset_at,
        budget_duration=duration,
        pinned_reset_at=record.get("pinned_reset_at"),
        pinned_period_start=record.get("pinned_period_start"),
    )


class _SessionTracker:
    """Stands in for `get_async_session`: records every session it opens and how many are open now."""

    def __init__(self):
        self.sessions: list[AsyncMock] = []
        self.open = 0

    @contextlib.asynccontextmanager
    async def __call__(self):
        session = AsyncMock()
        self.sessions.append(session)
        self.open += 1
        try:
            yield session
        finally:
            self.open -= 1


class TestCalculateTimeUntilReset:
    """Tests for _calculate_time_until_reset."""

    def test_returns_none_for_none_input(self):
        assert _calculate_time_until_reset(None) is None

    def test_returns_none_for_empty_string(self):
        assert _calculate_time_until_reset("") is None

    def test_returns_expired_for_past_timestamp(self):
        result = _calculate_time_until_reset("2000-01-01T00:00:00Z")
        assert result == "Expired"

    def test_returns_formatted_string_for_future_timestamp(self):
        future = datetime(2099, 1, 1, tzinfo=timezone.utc)
        result = _calculate_time_until_reset(future.isoformat())
        assert result is not None
        assert "days" in result
        assert "hours" in result
        assert "mins" in result

    def test_returns_none_for_invalid_format(self):
        result = _calculate_time_until_reset("not-a-date")
        assert result is None

    def test_handles_isoformat_without_z(self):
        future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        result = _calculate_time_until_reset(future)
        assert result is not None
        assert result != "Expired"

    def test_formats_days_hours_minutes_correctly(self):
        # 2 days + 3 hours + 30 minutes from now
        delta = timedelta(days=2, hours=3, minutes=30)
        future = datetime.now(timezone.utc) + delta
        result = _calculate_time_until_reset(future.strftime("%Y-%m-%dT%H:%M:%SZ"))
        assert result is not None
        assert "days" in result
        assert "hours" in result
        assert "mins" in result


class TestCalculateTimeUntilResetNaiveDatetime:
    """Tests _calculate_time_until_reset with timezone-naive ISO strings."""

    def test_handles_naive_isoformat_string_without_timezone(self):
        # No "Z" and no "+00:00" → fromisoformat yields a naive datetime → triggers tzinfo branch
        result = _calculate_time_until_reset("2099-06-15T12:00:00")
        assert result is not None
        assert "days" in result


class TestBuildSpendingRow:
    """Tests for _build_spending_row."""

    def test_calculates_total_percentage_correctly(self):
        spending = {"total_spend": 25.0, "max_budget": 100.0, "budget_reset_at": None}
        row = _build_spending_row("test-project", spending)
        assert row["project_name"] == "test-project"
        assert row["current_spending"] == 25.0
        assert row["budget_limit"] == 100.0
        assert row["total"] == 25.0  # 25/100 * 100

    def test_no_percentage_is_reported_when_no_limit_is_configured(self):
        """`0.0` here rendered as *0% consumed* for a holder whose limit simply does not exist.

        The precedent is `_limit_value` in the member spend service: "None means 'no limit
        configured'. Never substitute 0 - 0 is a real limit and would render as fully consumed."
        """
        spending = {"total_spend": 10.0, "max_budget": None, "budget_reset_at": None}
        row = _build_spending_row("proj", spending)
        assert row["total"] is None
        assert row["budget_limit"] is None

    def test_no_percentage_is_reported_against_a_zero_limit(self):
        """There is no percentage of zero, and 0.0 would read as 0% consumed."""
        spending = {"total_spend": 10.0, "max_budget": 0.0, "budget_reset_at": None}
        row = _build_spending_row("proj", spending)
        assert row["total"] is None

    def test_an_absent_spend_is_unavailable_and_never_zero(self):
        """No record of spending is not a record of no spending."""
        row = _build_spending_row("proj", {"max_budget": 100.0, "budget_reset_at": None})
        assert row["current_spending"] is None
        assert row["total"] is None, "no percentage can be computed from an absent figure"

    def test_a_real_measured_zero_is_still_reported_as_zero(self):
        """The distinction only works in both directions: a genuine zero must survive."""
        row = _build_spending_row("proj", {"total_spend": 0.0, "max_budget": 100.0, "budget_reset_at": None})
        assert row["current_spending"] == 0.0
        assert row["total"] == 0.0

    def test_money_is_sent_unrounded_and_the_limit_rounded(self):
        """The UI formats money: rounded here, a sub-cent spend read as 0 beside unrounded figures."""
        spending = {"total_spend": 10.1234, "max_budget": 33.333, "budget_reset_at": None}
        row = _build_spending_row("proj", spending)
        assert row["current_spending"] == 10.1234
        assert row["budget_limit"] == 33.33

    def test_a_sub_cent_spend_is_not_rounded_to_zero(self):
        row = _build_spending_row("proj", {"total_spend": 0.0042, "max_budget": 10.0, "budget_reset_at": None})
        assert row["current_spending"] == 0.0042
        assert row["total"] == 0.04

    def test_time_until_reset_is_none_when_reset_at_missing(self):
        spending = {"total_spend": 5.0, "max_budget": 10.0, "budget_reset_at": None}
        row = _build_spending_row("proj", spending)
        assert row["time_until_reset"] is None

    def test_an_annotation_it_was_handed_is_passed_through_verbatim(self):
        """A port-fed caller merges `spend_row_fields`, and those values are the truth about its figure."""
        spending = {
            "total_spend": 40.0,
            "max_budget": 100.0,
            "budget_reset_at": None,
            "spend_unavailable": True,
            "spend_partial": True,
            "spend_missing_amount": 2.5,
        }
        row = _build_spending_row("proj", spending)
        assert row["current_spending"] == 40.0
        assert row["spend_unavailable"] is True
        assert row["spend_partial"] is True
        assert row["spend_missing_amount"] == 2.5

    def test_a_figure_supplied_without_an_annotation_is_not_branded_unavailable(self):
        """The router's LiteLLM-fed rows carry no annotation; defaulting them to unavailable would
        brand every live figure on that screen a truncated total."""
        row = _build_spending_row("proj", {"total_spend": 10.0, "max_budget": 100.0, "budget_reset_at": None})
        assert row["spend_unavailable"] is False
        assert row["spend_partial"] is False
        assert row["spend_missing_amount"] is None

    def test_an_absent_figure_without_an_annotation_is_unavailable(self):
        row = _build_spending_row("proj", {"max_budget": 100.0, "budget_reset_at": None})
        assert row["current_spending"] is None
        assert row["spend_unavailable"] is True


class TestBudgetCategory:
    """Tests for _budget_category."""

    def test_maps_a_known_category_to_the_enum_the_port_needs(self):
        assert _budget_category("premium_models") is BudgetCategory.PREMIUM_MODELS

    def test_an_unknown_category_names_no_holder_and_does_not_raise(self):
        """The column is a free-form varchar, and one bad value must not fail the whole read."""
        assert _budget_category("custom_cat") is None


class TestBuildBudgetUsageRows:
    """Tests for _build_budget_usage_rows."""

    def test_returns_correct_columns(self):
        columns, _ = _build_budget_usage_rows("user@example.com", [], {}, {})
        assert columns == _get_key_spending_columns()

    def test_empty_inputs_return_empty_rows(self):
        _, rows = _build_budget_usage_rows("user@example.com", [], {}, {})
        assert rows == []

    def test_platform_category_uses_user_label(self):
        a = _assignment("b1", "platform")
        _, rows = _build_budget_usage_rows("user@example.com", [a], {"b1": _budget()}, {})
        assert rows[0]["project_name"] == "user@example.com"

    def test_cli_category_appends_cli_suffix(self):
        a = _assignment("b2", "cli")
        _, rows = _build_budget_usage_rows("user@example.com", [a], {"b2": _budget(50.0)}, {})
        assert rows[0]["project_name"] == "user@example.com (cli)"

    def test_premium_models_category_appends_premium_suffix(self):
        a = _assignment("b3", "premium_models")
        _, rows = _build_budget_usage_rows("user@example.com", [a], {"b3": _budget(10.0)}, {})
        assert rows[0]["project_name"] == "user@example.com (premium)"

    def test_unknown_category_appends_category_name(self):
        a = _assignment("b4", "custom_cat")
        _, rows = _build_budget_usage_rows("user@example.com", [a], {"b4": _budget(10.0)}, {})
        assert rows[0]["project_name"] == "user@example.com (custom_cat)"

    def test_assignment_with_missing_budget_is_skipped(self):
        a = _assignment("unknown-id", "platform")
        _, rows = _build_budget_usage_rows("user@example.com", [a], {}, {})
        assert rows == []

    def test_a_budget_the_port_reported_no_figure_for_renders_as_unavailable(self):
        """Re-baselined from `spend defaults to zero`: the missing figure used to arrive as `0.0`."""
        a = _assignment("b1", "platform")
        _, rows = _build_budget_usage_rows("user@example.com", [a], {"b1": _budget()}, {})
        assert rows[0]["current_spending"] is None
        assert rows[0]["total"] is None

    def test_a_no_figure_result_is_not_rendered_as_zero_either(self):
        """`SpendResult.no_figure` carries a real answer - *unknown* - and must not become 0.0."""
        a = _assignment("b1", "platform")
        spend_map = {"b1": SpendResult.no_figure(PERIOD)}
        _, rows = _build_budget_usage_rows("user@example.com", [a], {"b1": _budget()}, spend_map)
        assert rows[0]["current_spending"] is None
        assert rows[0]["total"] is None

    def test_a_measured_zero_from_the_port_is_rendered_as_zero(self):
        a = _assignment("b1", "platform")
        _, rows = _build_budget_usage_rows("user@example.com", [a], {"b1": _budget()}, {"b1": _figure("0")})
        assert rows[0]["current_spending"] == 0.0
        assert rows[0]["total"] == 0.0

    def test_the_ports_figure_is_used_when_present(self):
        a = _assignment("b1", "platform")
        _, rows = _build_budget_usage_rows("user@example.com", [a], {"b1": _budget()}, {"b1": _figure("42.5")})
        assert rows[0]["current_spending"] == 42.5
        assert rows[0]["total"] == 42.5


class TestSpendAnnotationReachesTheRow:
    """The figure never travels alone: three states must stay tellable apart on the row."""

    @staticmethod
    def _row(result: SpendResult | None) -> dict:
        """The platform row for one budget the port answered `result` for (None: never asked)."""
        spend_map = {} if result is None else {"b1": result}
        _, rows = _build_budget_usage_rows("user@example.com", [_assignment("b1")], {"b1": _budget()}, spend_map)
        return rows[0]

    def test_an_exact_figure_is_annotated_as_exact(self):
        row = self._row(_figure("42.5"))
        assert row["current_spending"] == 42.5
        assert row["spend_unavailable"] is False
        assert row["spend_partial"] is False
        assert row["spend_missing_amount"] is None

    def test_a_no_figure_result_carries_no_number_and_says_unavailable(self):
        row = self._row(SpendResult.no_figure(PERIOD))
        assert row["current_spending"] is None
        assert row["spend_unavailable"] is True

    def test_a_budget_the_port_was_never_asked_about_says_unavailable(self):
        """No derivable period means no query, which is the same fact to a reader: no figure."""
        row = self._row(None)
        assert row["current_spending"] is None
        assert row["spend_unavailable"] is True

    def test_a_truncated_total_keeps_its_number_and_still_says_unavailable(self):
        """The dangerous pair: drop the flag and a subtotal is presented as a total."""
        row = self._row(SpendResult(figure=Decimal("42.5"), unavailable=SUB_FLOOR))
        assert row["current_spending"] == 42.5
        assert row["spend_unavailable"] is True
        # The figure and the percentage are untouched by the annotation.
        assert row["total"] == 42.5

    def test_a_computable_dimension_hole_reports_the_amount_missing(self):
        row = self._row(
            SpendResult(
                figure=Decimal("42.5"),
                partial={SpendDimension.PROJECT: Portion(spend=Decimal("2.50"), rows=12)},
            )
        )
        assert row["current_spending"] == 42.5
        assert row["spend_partial"] is True
        assert row["spend_missing_amount"] == 2.5
        assert row["spend_unavailable"] is False, "a dimension hole never makes a figure unavailable"

    def test_an_uncomputable_hole_reports_an_unknown_amount_never_a_zero(self):
        """The identity-bridge portion has neither an amount nor a row count to stand in for one."""
        row = self._row(
            SpendResult(
                figure=Decimal("42.5"),
                partial={SpendDimension.IDENTITY_BRIDGE: Portion(spend=None, rows=None)},
            )
        )
        assert row["spend_partial"] is True
        assert row["spend_missing_amount"] is None

    def test_the_annotation_keys_are_not_advertised_as_columns(self):
        """Per-row metadata a client reads beside the figure - not three raw booleans as headings."""
        ids = [c["id"] for c in _get_key_spending_columns()]
        assert "spend_unavailable" not in ids
        assert "spend_partial" not in ids
        assert "spend_missing_amount" not in ids


class TestConsumedPerBudget:
    """Tests for BudgetUsageService._consumed_per_budget - the one spend read."""

    def _port(self, answers: dict | None = None, side_effect=None) -> MagicMock:
        port = MagicMock()
        port.consumed_for_holders = AsyncMock(return_value=answers or {}, side_effect=side_effect)
        return port

    async def _consume(self, port, assignments, budgets_map, aliases=ALIASES) -> dict:
        with patch(f"{MODULE}.spend_service", port):
            return await BudgetUsageService._consumed_per_budget("user-1", assignments, budgets_map, aliases)

    @pytest.mark.asyncio
    async def test_asks_the_port_for_the_personal_holder_over_the_budget_period(self):
        holder = PersonalHolder(user_id="user-1", category=BudgetCategory.PLATFORM, aliases=ALIASES)
        port = self._port({holder: _figure("12.5")})

        spend_map = await self._consume(port, [_assignment("b1")], {"b1": _budget()})

        port.consumed_for_holders.assert_awaited_once_with([holder], PERIOD)
        assert spend_map == {"b1": _figure("12.5")}

    @pytest.mark.asyncio
    async def test_a_reset_codemie_set_starts_the_period_where_it_was_recorded(self):
        """EPMCDME-15204: a budget created 06-08 15:05 first resets 07-09; its first day is in the period."""
        created = datetime(2099, 6, 8, 15, 5, tzinfo=timezone.utc)
        pinned = datetime(2099, 7, 9, tzinfo=timezone.utc)
        budget = _budget(reset_at="2099-07-09T00:00:00Z", pinned_reset_at=pinned, pinned_period_start=created)
        holder = PersonalHolder(user_id="user-1", category=BudgetCategory.PLATFORM, aliases=ALIASES)
        port = self._port({holder: _figure("0.0917")})

        await self._consume(port, [_assignment("b1")], {"b1": budget})

        port.consumed_for_holders.assert_awaited_once_with([holder], TimeRange(created, pinned))

    @pytest.mark.asyncio
    async def test_a_personal_period_is_not_clamped_to_the_assignment_or_the_budget(self):
        """Unlike a project's, a personal period starts at the previous reset however recent its rows.

        `user_budget_assignments.assigned_at` is rewritten to NOW() on every upsert and runtime mirror,
        and LiteLLM's per-user customer counter carries on across a reassignment, so clamping the
        start to either instant would undercount against what the provider enforces (EPMCDME-15236).
        """
        mid_period = datetime(2099, 6, 14, 9, 30, tzinfo=timezone.utc)
        assignment = SimpleNamespace(budget_id="b1", category="platform", assigned_at=mid_period, spend_reset_at=None)
        budget = SimpleNamespace(
            max_budget=100.0,
            budget_reset_at=RESET_AT,
            budget_duration="30d",
            created_at=mid_period,
            pinned_reset_at=None,
            pinned_period_start=None,
        )
        holder = PersonalHolder(user_id="user-1", category=BudgetCategory.PLATFORM, aliases=ALIASES)
        port = self._port({holder: _figure("12.5")})

        await self._consume(port, [assignment], {"b1": budget})

        port.consumed_for_holders.assert_awaited_once_with([holder], PERIOD)

    @pytest.mark.asyncio
    async def test_a_manual_reset_inside_the_period_starts_it_at_the_reset(self):
        reset = datetime(2099, 6, 14, 9, 30, tzinfo=timezone.utc)
        holder = PersonalHolder(user_id="user-1", category=BudgetCategory.PLATFORM, aliases=ALIASES)
        port = self._port({holder: _figure("1.5")})

        await self._consume(port, [_assignment("b1", spend_reset_at=reset)], {"b1": _budget()})

        port.consumed_for_holders.assert_awaited_once_with([holder], TimeRange(reset, PERIOD.end))

    @pytest.mark.asyncio
    async def test_a_naive_manual_reset_is_read_as_utc(self):
        holder = PersonalHolder(user_id="user-1", category=BudgetCategory.PLATFORM, aliases=ALIASES)
        port = self._port({holder: _figure("1.5")})

        await self._consume(port, [_assignment("b1", spend_reset_at=datetime(2099, 6, 14, 9, 30))], {"b1": _budget()})

        port.consumed_for_holders.assert_awaited_once_with(
            [holder], TimeRange(datetime(2099, 6, 14, 9, 30, tzinfo=timezone.utc), PERIOD.end)
        )

    @pytest.mark.asyncio
    async def test_a_manual_reset_from_an_earlier_period_leaves_the_period_unchanged(self):
        earlier = datetime(2099, 5, 20, 9, 30, tzinfo=timezone.utc)
        holder = PersonalHolder(user_id="user-1", category=BudgetCategory.PLATFORM, aliases=ALIASES)
        port = self._port({holder: _figure("1.5")})

        await self._consume(port, [_assignment("b1", spend_reset_at=earlier)], {"b1": _budget()})

        port.consumed_for_holders.assert_awaited_once_with([holder], PERIOD)

    @pytest.mark.asyncio
    async def test_a_reset_moves_only_its_own_budgets_period(self):
        reset = datetime(2099, 6, 14, 9, 30, tzinfo=timezone.utc)
        platform = PersonalHolder(user_id="user-1", category=BudgetCategory.PLATFORM, aliases=ALIASES)
        cli = PersonalHolder(user_id="user-1", category=BudgetCategory.CLI, aliases=ALIASES)
        port = self._port(side_effect=[{platform: _figure("3")}, {cli: _figure("4")}])

        await self._consume(
            port,
            [_assignment("b1", "platform", spend_reset_at=reset), _assignment("b2", "cli")],
            {"b1": _budget(), "b2": _budget()},
        )

        periods = {call.args[1] for call in port.consumed_for_holders.await_args_list}
        assert periods == {TimeRange(reset, PERIOD.end), PERIOD}

    @pytest.mark.asyncio
    async def test_an_unresolvable_alias_set_stays_none_and_is_not_emptied(self):
        """`None` says the person's pre-rollout rows cannot be identified; `frozenset()` claims
        they have no fallback identities, and the port labels only the first."""
        holder = PersonalHolder(user_id="user-1", category=BudgetCategory.PLATFORM, aliases=None)
        port = self._port({holder: _figure("1")})

        await self._consume(port, [_assignment("b1")], {"b1": _budget()}, aliases=None)

        asked_holders, _ = port.consumed_for_holders.await_args.args
        assert asked_holders[0].aliases is None

    @pytest.mark.asyncio
    async def test_budgets_sharing_a_period_are_one_round_trip(self):
        platform = PersonalHolder(user_id="user-1", category=BudgetCategory.PLATFORM, aliases=ALIASES)
        cli = PersonalHolder(user_id="user-1", category=BudgetCategory.CLI, aliases=ALIASES)
        port = self._port({platform: _figure("3"), cli: _figure("4")})

        spend_map = await self._consume(
            port,
            [_assignment("b1", "platform"), _assignment("b2", "cli")],
            {"b1": _budget(), "b2": _budget()},
        )

        assert port.consumed_for_holders.await_count == 1
        assert spend_map == {"b1": _figure("3"), "b2": _figure("4")}

    @pytest.mark.asyncio
    async def test_budgets_on_different_schedules_are_asked_about_per_period(self):
        """All holders in one call share one period, so distinct periods cannot be merged."""
        platform = PersonalHolder(user_id="user-1", category=BudgetCategory.PLATFORM, aliases=ALIASES)
        cli = PersonalHolder(user_id="user-1", category=BudgetCategory.CLI, aliases=ALIASES)
        port = self._port(side_effect=[{platform: _figure("3")}, {cli: _figure("4")}])

        spend_map = await self._consume(
            port,
            [_assignment("b1", "platform"), _assignment("b2", "cli")],
            {"b1": _budget(), "b2": _budget(duration="1d")},
        )

        assert port.consumed_for_holders.await_count == 2
        periods = {call.args[1] for call in port.consumed_for_holders.await_args_list}
        assert periods == {
            PERIOD,
            TimeRange(datetime(2099, 6, 30, tzinfo=timezone.utc), datetime(2099, 7, 1, tzinfo=timezone.utc)),
        }
        assert spend_map == {"b1": _figure("3"), "b2": _figure("4")}

    @pytest.mark.asyncio
    async def test_a_budget_with_no_derivable_period_is_never_queried(self):
        """No reset instant means no budget period, and a rolling window would answer a different
        question. There is no figure, so the port is not asked at all."""
        port = self._port()

        spend_map = await self._consume(port, [_assignment("b1")], {"b1": _budget(reset_at=None)})

        port.consumed_for_holders.assert_not_awaited()
        assert spend_map == {}

    @pytest.mark.asyncio
    async def test_a_budget_whose_duration_grammar_is_unreadable_is_never_queried(self):
        port = self._port()

        spend_map = await self._consume(port, [_assignment("b1")], {"b1": _budget(duration="1mo")})

        port.consumed_for_holders.assert_not_awaited()
        assert spend_map == {}

    @pytest.mark.asyncio
    async def test_an_assignment_naming_no_budget_category_is_never_queried(self):
        port = self._port()

        spend_map = await self._consume(port, [_assignment("b1", "custom_cat")], {"b1": _budget()})

        port.consumed_for_holders.assert_not_awaited()
        assert spend_map == {}

    @pytest.mark.asyncio
    async def test_an_assignment_with_no_budget_row_is_never_queried(self):
        port = self._port()

        spend_map = await self._consume(port, [_assignment("gone")], {})

        port.consumed_for_holders.assert_not_awaited()
        assert spend_map == {}


_MSS = "codemie.service.analytics.handlers.member_spend_service.member_spend_service"


class TestBudgetUsageServiceGetBudgetUsage:
    """Tests for BudgetUsageService.get_budget_usage orchestration."""

    @pytest.fixture(autouse=True)
    def refresh_overdue(self):
        """The reset reconciliation moves nothing unless a test says otherwise - it is a provider call."""
        with patch(
            f"{MODULE}.daily_budget_reset_reconciliation_service.refresh_overdue",
            new_callable=AsyncMock,
            return_value=set(),
        ) as refresh:
            yield refresh

    @pytest.fixture(autouse=True)
    def sessions(self):
        with patch(f"{MODULE}.get_async_session", _SessionTracker()) as tracker:
            yield tracker

    @pytest.fixture(autouse=True)
    def resolve_identity(self):
        with patch(
            f"{MODULE}.resolve_identity", new_callable=AsyncMock, return_value=ResolvedIdentity("user-1", ALIASES)
        ) as resolve:
            yield resolve

    @pytest.fixture(autouse=True)
    def default_project(self):
        """No default project unless a test says otherwise."""
        with patch.object(
            BudgetUsageService, "_resolve_default_project", new_callable=AsyncMock, return_value=(None, False)
        ) as resolve:
            yield resolve

    @pytest.fixture(autouse=True)
    def project_rows(self):
        """The user's project rows come from the member spend service; none unless a test says otherwise."""
        with patch(_MSS) as member_spend:
            member_spend.get_user_project_rows = AsyncMock(return_value=[])
            yield member_spend.get_user_project_rows

    def _repo(self, assignments: list, budgets_map: dict) -> MagicMock:
        repo = MagicMock()
        repo.get_user_category_assignments = AsyncMock(return_value=assignments)
        repo.get_by_ids = AsyncMock(return_value=budgets_map)
        return repo

    @pytest.mark.asyncio
    async def test_renders_the_figure_the_port_reported(self):
        repo = self._repo([_assignment("b1")], {"b1": _budget()})
        with (
            patch("codemie.repository.budget_repository.budget_repository", repo),
            patch.object(
                BudgetUsageService, "_consumed_per_budget", new_callable=AsyncMock, return_value={"b1": _figure("25")}
            ),
        ):
            columns, rows = await BudgetUsageService().get_budget_usage("user-1", "alice@example.com")

        assert columns == _get_key_spending_columns()
        assert rows[0]["project_name"] == "alice@example.com"
        assert rows[0]["current_spending"] == 25.0
        assert rows[0]["total"] == 25.0

    @pytest.mark.asyncio
    async def test_a_budget_the_port_had_no_figure_for_renders_unavailable_not_zero(self):
        repo = self._repo([_assignment("b1")], {"b1": _budget()})
        with (
            patch("codemie.repository.budget_repository.budget_repository", repo),
            patch.object(BudgetUsageService, "_consumed_per_budget", new_callable=AsyncMock, return_value={}),
        ):
            _, rows = await BudgetUsageService().get_budget_usage("user-1", "alice@example.com")

        assert rows[0]["current_spending"] is None
        assert rows[0]["total"] is None
        # Budget policy is untouched by this read.
        assert rows[0]["budget_limit"] == 100.0

    @pytest.mark.asyncio
    async def test_a_truncated_total_reaches_the_reader_with_its_flag(self):
        """End to end: the number is reported, and so is the fact that a ledger is missing from it."""
        repo = self._repo([_assignment("b1")], {"b1": _budget()})
        truncated = SpendResult(figure=Decimal("25"), unavailable=SUB_FLOOR)
        with (
            patch("codemie.repository.budget_repository.budget_repository", repo),
            patch.object(
                BudgetUsageService, "_consumed_per_budget", new_callable=AsyncMock, return_value={"b1": truncated}
            ),
        ):
            _, rows = await BudgetUsageService().get_budget_usage("user-1", "alice@example.com")

        assert rows[0]["current_spending"] == 25.0
        assert rows[0]["spend_unavailable"] is True

    @pytest.mark.asyncio
    async def test_an_overdue_reset_is_reconciled_before_the_period_and_the_display_read_it(
        self, refresh_overdue, sessions
    ):
        """A stored reset that has passed names the period that just closed. The read reconciles it
        first and re-reads the moved instant in a new session - the first is closed by then, and its
        detached rows cannot be refreshed (EPMCDME-15537) - so both the period the port is asked about
        and the reset on screen are the current ones rather than last period's spend shown as "Expired"."""
        budget = _budget()
        repo = self._repo([_assignment("b1")], {"b1": budget})
        repo.get_by_ids.side_effect = [{"b1": budget}, {"b1": _budget(reset_at=MOVED_RESET_AT)}]
        reconciled = []

        async def reconcile(budgets):
            reconciled.extend(budgets)
            return {"b1"}

        open_when_read = []

        async def read_ledger(holders, period):
            open_when_read.append(sessions.open)
            return {}

        refresh_overdue.side_effect = reconcile
        port = MagicMock()
        port.consumed_for_holders = AsyncMock(side_effect=read_ledger)
        with (
            patch("codemie.repository.budget_repository.budget_repository", repo),
            patch(f"{MODULE}.spend_service", port),
        ):
            _, rows = await BudgetUsageService().get_budget_usage("user-1", "alice@example.com")

        assert reconciled == [budget]
        assert repo.get_by_ids.await_args.args == (sessions.sessions[1], ["b1"])
        assert port.consumed_for_holders.await_args.args[1] == MOVED_PERIOD
        assert open_when_read == [0], "the re-read session is closed before the ledger is read"
        assert rows[0]["budget_reset_at"] == MOVED_RESET_AT

    @pytest.mark.asyncio
    async def test_a_reset_nothing_moved_is_not_reloaded(self, refresh_overdue, sessions):
        repo = self._repo([_assignment("b1")], {"b1": _budget()})
        with (
            patch("codemie.repository.budget_repository.budget_repository", repo),
            patch.object(BudgetUsageService, "_consumed_per_budget", new_callable=AsyncMock, return_value={}),
        ):
            await BudgetUsageService().get_budget_usage("user-1", "alice@example.com")

        refresh_overdue.assert_awaited_once()
        repo.get_by_ids.assert_awaited_once()
        assert len(sessions.sessions) == 1

    @pytest.mark.asyncio
    async def test_no_session_is_held_while_the_provider_or_the_ledger_is_called(
        self, refresh_overdue, resolve_identity, sessions
    ):
        """The metadata session is closed before the LiteLLM reconciliation and the ledger read: held
        across them, it kept a second pooled connection idle in transaction per request while the
        ledger read checked out its own (EPMCDME-15537)."""
        open_when_called = {}

        async def reconcile(budgets):
            open_when_called["refresh_overdue"] = sessions.open
            return set()

        async def read_ledger(holders, period):
            open_when_called["consumed_for_holders"] = sessions.open
            return {}

        refresh_overdue.side_effect = reconcile
        port = MagicMock()
        port.consumed_for_holders = AsyncMock(side_effect=read_ledger)
        repo = self._repo([_assignment("b1")], {"b1": _budget()})
        with (
            patch("codemie.repository.budget_repository.budget_repository", repo),
            patch(f"{MODULE}.spend_service", port),
        ):
            await BudgetUsageService().get_budget_usage("user-1", "alice@example.com")

        assert open_when_called == {"refresh_overdue": 0, "consumed_for_holders": 0}
        (metadata_session,) = sessions.sessions
        assert repo.get_user_category_assignments.await_args.args[0] is metadata_session
        assert repo.get_by_ids.await_args.args[0] is metadata_session
        assert resolve_identity.await_args.args == (metadata_session, "user-1")
        asked_holders, _ = port.consumed_for_holders.await_args.args
        assert asked_holders == [PersonalHolder(user_id="user-1", category=BudgetCategory.PLATFORM, aliases=ALIASES)]

    @pytest.mark.asyncio
    async def test_no_assignments_yields_no_rows(self):
        repo = self._repo([], {})
        port = MagicMock()
        port.consumed_for_holders = AsyncMock(return_value={})
        with (
            patch("codemie.repository.budget_repository.budget_repository", repo),
            patch(f"{MODULE}.spend_service", port),
        ):
            _, rows = await BudgetUsageService().get_budget_usage("user-1", "alice@example.com")

        assert rows == []
        assert repo.get_by_ids.await_args.args[1] == []
        port.consumed_for_holders.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_project_rows_follow_the_personal_rows(self, project_rows):
        repo = self._repo([_assignment("b1")], {"b1": _budget()})
        project_rows.return_value = [
            {
                "project_name": "proj-a",
                "platform": 1.0,
                "platform_limit": 10.0,
                "cli": None,
                "cli_limit": None,
                "premium_models": None,
                "premium_models_limit": None,
            }
        ]
        with (
            patch("codemie.repository.budget_repository.budget_repository", repo),
            patch.object(
                BudgetUsageService, "_consumed_per_budget", new_callable=AsyncMock, return_value={"b1": _figure("25")}
            ),
        ):
            _, rows = await BudgetUsageService().get_budget_usage("user-1", "alice@example.com")

        assert [row["project_name"] for row in rows] == ["alice@example.com", "proj-a"]
        project_rows.assert_awaited_once_with("user-1", effective_limits=True, with_reset=True)

    @pytest.mark.asyncio
    async def test_a_failed_project_read_still_returns_the_personal_rows(self, project_rows):
        repo = self._repo([_assignment("b1")], {"b1": _budget()})
        project_rows.side_effect = RuntimeError("ledger down")
        with (
            patch("codemie.repository.budget_repository.budget_repository", repo),
            patch.object(
                BudgetUsageService, "_consumed_per_budget", new_callable=AsyncMock, return_value={"b1": _figure("25")}
            ),
        ):
            _, rows = await BudgetUsageService().get_budget_usage("user-1", "alice@example.com")

        assert [row["project_name"] for row in rows] == ["alice@example.com"]


class TestExpandProjectRows:
    """Tests for _expand_project_rows."""

    def _project_row(self, name, **overrides):
        base = {
            "project_name": name,
            "platform": 1.0,
            "platform_limit": 10.0,
            "cli": 0.5,
            "cli_limit": 5.0,
            "premium_models": 0.0,
            "premium_models_limit": None,
        }
        return {**base, **overrides}

    def test_expand_sorts_by_project_name_case_insensitive(self):
        rows = _expand_project_rows([self._project_row("Zebra"), self._project_row("alpha")])
        names = [r["project_name"] for r in rows]
        assert names == ["alpha", "alpha (cli)", "Zebra", "Zebra (cli)"]

    def test_expand_emits_all_three_categories_when_all_allocated(self):
        row = self._project_row("p", premium_models=0.0, premium_models_limit=2.0)
        names = [r["project_name"] for r in _expand_project_rows([row])]
        assert names == ["p", "p (cli)", "p (premium)"]

    def test_expand_empty_input(self):
        assert _expand_project_rows([]) == []

    def test_expand_skips_spend_with_null_limit(self):
        """Spend left over from a deleted project budget has no limit to report against."""
        row = self._project_row("p", platform=2.5, platform_limit=None)
        result = _expand_project_rows([row])
        assert [r["project_name"] for r in result] == ["p (cli)"]

    def test_expand_null_reset_fields_when_absent(self):
        for r in _expand_project_rows([self._project_row("p")]):
            assert r["budget_reset_at"] is None
            assert r["time_until_reset"] is None

    def test_expand_populates_reset_fields_from_row(self):
        reset_at = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        row = self._project_row("p", platform_reset_at=reset_at)
        platform_row = next(r for r in _expand_project_rows([row]) if r["project_name"] == "p")
        assert platform_row["budget_reset_at"] == reset_at
        assert platform_row["time_until_reset"] is not None

    def test_expand_carries_each_category_spend_annotation(self):
        """A figure the ledger could not give reaches the panel as unavailable, not as an exact blank."""
        row = self._project_row("p", platform=None, platform_spend_unavailable=True, platform_spend_partial=False)
        platform_row = next(r for r in _expand_project_rows([row]) if r["project_name"] == "p")
        assert platform_row["current_spending"] is None
        assert platform_row["spend_unavailable"] is True


_UPR = "codemie.repository.user_project_repository.user_project_repository"
_APR = "codemie.repository.application_repository.application_repository"


def _project_spend_row(name, **overrides):
    base = {
        "project_name": name,
        "platform": 1.0,
        "platform_limit": 10.0,
        "cli": 0.0,
        "cli_limit": None,
        "premium_models": 0.0,
        "premium_models_limit": None,
    }
    return {**base, **overrides}


def _membership(name, is_default=False):
    return SimpleNamespace(project_name=name, is_default=is_default)


class TestGetBudgetUsageDefaultFlagAndOrdering:
    """Default project rows come first, then personal rows, then the remaining projects."""

    @pytest.fixture(autouse=True)
    def _environment(self):
        repo = MagicMock()
        repo.get_user_category_assignments = AsyncMock(return_value=[_assignment("b1")])
        repo.get_by_ids = AsyncMock(return_value={"b1": _budget()})
        with (
            patch(f"{MODULE}.get_async_session", _SessionTracker()),
            patch(
                f"{MODULE}.daily_budget_reset_reconciliation_service.refresh_overdue",
                new_callable=AsyncMock,
                return_value=set(),
            ),
            patch(
                f"{MODULE}.resolve_identity", new_callable=AsyncMock, return_value=ResolvedIdentity("user-1", ALIASES)
            ),
            patch("codemie.repository.budget_repository.budget_repository", repo),
            patch.object(
                BudgetUsageService, "_consumed_per_budget", new_callable=AsyncMock, return_value={"b1": _figure("5")}
            ),
        ):
            yield

    async def _run(self, project_rows, memberships, personal_names=(), email="alice@x.com"):
        with patch(_MSS) as member_spend, patch(_UPR) as upr, patch(_APR) as apr:
            member_spend.get_user_project_rows = AsyncMock(return_value=project_rows)
            if isinstance(memberships, Exception):
                upr.aget_by_user_id = AsyncMock(side_effect=memberships)
            else:
                upr.aget_by_user_id = AsyncMock(return_value=memberships)
            apr.aget_personal_project_names = AsyncMock(return_value=set(personal_names))
            return await BudgetUsageService().get_budget_usage("user-1", "alice", email)

    @pytest.mark.asyncio
    async def test_default_project_rows_first_and_flagged(self):
        projects = [_project_spend_row("alpha"), _project_spend_row("zeta", cli=1.0, cli_limit=5.0)]
        memberships = [_membership("alpha"), _membership("zeta", is_default=True)]

        _, rows = await self._run(projects, memberships)

        assert [r["project_name"] for r in rows] == ["zeta", "zeta (cli)", "alice", "alpha"]
        assert [r["is_default"] for r in rows] == [True, True, False, False]

    @pytest.mark.asyncio
    async def test_personal_default_puts_personal_rows_first_and_flagged(self):
        memberships = [_membership("alpha"), _membership("alice@x.com", is_default=True)]

        _, rows = await self._run([_project_spend_row("alpha")], memberships, personal_names={"alice@x.com"})

        assert [(r["project_name"], r["is_default"]) for r in rows] == [("alice", True), ("alpha", False)]

    @pytest.mark.asyncio
    async def test_missing_email_falls_back_to_first_project_by_name(self):
        projects = [_project_spend_row("zeta"), _project_spend_row("alpha")]

        _, rows = await self._run(projects, [_membership("zeta"), _membership("alpha")], email=None)

        assert [(r["project_name"], r["is_default"]) for r in rows] == [
            ("alpha", True),
            ("alice", False),
            ("zeta", False),
        ]

    @pytest.mark.asyncio
    async def test_no_memberships_flags_nothing(self):
        _, rows = await self._run([_project_spend_row("alpha")], [])

        assert [(r["project_name"], r["is_default"]) for r in rows] == [("alice", False), ("alpha", False)]

    @pytest.mark.asyncio
    async def test_default_resolution_failure_flags_nothing_and_still_returns_rows(self):
        _, rows = await self._run([_project_spend_row("alpha")], RuntimeError("db down"))

        assert [(r["project_name"], r["is_default"]) for r in rows] == [("alice", False), ("alpha", False)]

    @pytest.mark.asyncio
    async def test_columns_do_not_include_is_default(self):
        columns, _ = await self._run([], [])

        assert "is_default" not in [c["id"] for c in columns]


class TestExpandProjectRowsPercentAndDefault:
    def test_percent_spend_is_numerator_when_present(self):
        row = _project_spend_row("p", platform=10.0, platform_limit=100.0, platform_percent_spend=50.0)

        (result,) = _expand_project_rows([row])

        assert result["current_spending"] == 10.0
        assert result["total"] == 50.0

    def test_percent_numerator_falls_back_to_spend_when_absent(self):
        (result,) = _expand_project_rows([_project_spend_row("p", platform=25.0, platform_limit=100.0)])

        assert result["total"] == 25.0

    def test_absent_project_total_leaves_the_percent_absent(self):
        row = _project_spend_row("p", platform=10.0, platform_limit=100.0, platform_percent_spend=None)

        (result,) = _expand_project_rows([row])

        assert result["current_spending"] == 10.0
        assert result["total"] is None

    def test_default_project_rows_are_flagged(self):
        rows = _expand_project_rows([_project_spend_row("a"), _project_spend_row("B")], default_project="b")

        assert [(r["project_name"], r["is_default"]) for r in rows] == [("a", False), ("B", True)]

    def test_rows_not_flagged_without_default(self):
        assert _expand_project_rows([_project_spend_row("a")])[0]["is_default"] is False
