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

"""The budget period, and the four substitutions it refuses to make.

Each refusal is tested rather than assumed, because all four produce a *plausible* range: a rolling
window, a defaulted duration, a closed upper bound and a closed period served as the current one each
return a number that looks right on one screen and disagrees with the screen beside it.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from codemie.service.spend.budget_period import (
    budget_period_range,
    parse_budget_duration,
    parse_budget_reset_at,
    running_period_start,
)

RESET = datetime(2026, 9, 1, tzinfo=timezone.utc)
#: A moment inside the period that ends at RESET, injected so a fixed date never ages into the past.
BEFORE_RESET = RESET - timedelta(hours=1)


class TestTheRangeEndsAtTheResetInstant:
    def test_the_period_is_the_duration_before_the_reset(self) -> None:
        period = budget_period_range(budget_reset_at="2026-09-01T00:00:00Z", budget_duration="7d", now=BEFORE_RESET)
        assert period is not None
        assert period.start == RESET - timedelta(days=7)
        assert period.end == RESET

    def test_the_upper_bound_is_the_reset_and_not_now(self) -> None:
        """A row on the reset instant belongs to the next period; a closed bound counts it twice."""
        period = budget_period_range(budget_reset_at=RESET, budget_duration="1d", now=BEFORE_RESET)
        assert period is not None and period.end == RESET

    def test_every_supported_unit_is_read(self) -> None:
        assert parse_budget_duration("45s") == timedelta(seconds=45)
        assert parse_budget_duration("90m") == timedelta(minutes=90)
        assert parse_budget_duration("12h") == timedelta(hours=12)
        assert parse_budget_duration("7d") == timedelta(days=7)


@pytest.fixture
def local_zone_off_utc(monkeypatch: pytest.MonkeyPatch):
    """On a UTC host, as in CI, a naive value read as local time is the same instant as one read as UTC."""
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=timezone.utc)


def _start(reset_at: datetime, duration: str, effective_from: datetime | None = None) -> datetime:
    """The derived period's start, asked one second before the reset so the period is current."""
    period = budget_period_range(
        budget_reset_at=reset_at,
        budget_duration=duration,
        effective_from=effective_from,
        now=reset_at - timedelta(seconds=1),
    )
    assert period is not None and period.end == reset_at
    return period.start


class TestAThirtyDayBudgetIsACalendarMonth:
    """LiteLLM resets a `30d` budget on the 1st of every month, so its period is the calendar month."""

    def test_a_reset_on_the_first_starts_on_the_first_of_the_month_before(self) -> None:
        """October has 31 days: `reset - 30 days` would start on 10-02 and drop the 1st."""
        assert _start(_utc(2026, 11, 1), "30d") == _utc(2026, 10, 1)

    def test_after_february_the_period_does_not_reach_into_january(self) -> None:
        assert _start(_utc(2026, 3, 1), "30d") == _utc(2026, 2, 1)
        assert _start(_utc(2028, 3, 1), "30d") == _utc(2028, 2, 1)

    def test_a_january_reset_starts_in_the_previous_december(self) -> None:
        assert _start(_utc(2027, 1, 1), "30d") == _utc(2026, 12, 1)

    def test_the_time_of_day_is_kept(self) -> None:
        assert _start(_utc(2026, 11, 1, 6, 30), "30d") == _utc(2026, 10, 1, 6, 30)

    def test_a_reset_off_the_first_keeps_thirty_days(self) -> None:
        """With no record of who set it, an off-schedule reset keeps the span before it."""
        assert _start(_utc(2026, 11, 15), "30d") == _utc(2026, 10, 16)

    def test_other_day_durations_are_unchanged(self) -> None:
        assert _start(_utc(2026, 11, 1), "7d") == _utc(2026, 10, 25)
        assert _start(_utc(2026, 11, 1), "1d") == _utc(2026, 10, 31)


class TestTheStartIsClampedToWhenTheBudgetBegan:
    """Spend from before a budget existed is not consumption of it - LiteLLM's counter starts at zero."""

    def test_a_budget_created_mid_period_starts_at_its_creation(self) -> None:
        began = _utc(2026, 8, 20)
        period = budget_period_range(
            budget_reset_at=RESET, budget_duration="30d", effective_from=began, now=BEFORE_RESET
        )
        assert period is not None
        assert period.start == began and period.end == RESET

    def test_a_budget_older_than_the_period_keeps_the_period_start(self) -> None:
        assert _start(RESET, "30d", effective_from=_utc(2026, 1, 1)) == _utc(2026, 8, 1)

    def test_no_known_beginning_clamps_nothing(self) -> None:
        assert _start(RESET, "30d", effective_from=None) == _utc(2026, 8, 1)

    def test_an_aware_value_in_another_zone_is_the_same_utc_instant(self) -> None:
        """The range rejects any offset but zero, so the instant is converted rather than refused."""
        began = datetime(2026, 8, 20, 3, 0, tzinfo=timezone(timedelta(hours=3)))
        start = _start(RESET, "30d", effective_from=began)
        assert start == _utc(2026, 8, 20) and start.utcoffset() == timedelta(0)

    @pytest.mark.usefixtures("local_zone_off_utc")
    def test_a_naive_value_is_read_as_utc(self) -> None:
        assert _start(RESET, "30d", effective_from=datetime(2026, 8, 20)) == _utc(2026, 8, 20)

    def test_the_figure_counts_nothing_from_before_the_budget_existed(self) -> None:
        """EPMCDME-15236: $0.0576 spent before the budget existed was shown as part of its $0.14."""
        began = _utc(2026, 10, 2, 12, 2, 14)
        period = budget_period_range(
            budget_reset_at="2026-11-01T00:00:00Z",
            budget_duration="30d",
            effective_from=began,
            now=_utc(2026, 10, 5),
        )
        assert period is not None and period.start == began


#: EPMCDME-15204: a 30d global budget created 2099-10-09 15:05; CodeMie set its first reset to the
#: next midnight after 30 days, which prolongs the provider's October period.
CREATED = _utc(2099, 10, 9, 15, 5, 4)
PINNED = _utc(2099, 11, 9)
FIRST_PERIOD_START = _utc(2099, 10, 1)


def _period(reset_at: datetime, now: datetime, duration: str = "30d") -> tuple[datetime, datetime]:
    period = budget_period_range(
        budget_reset_at=reset_at,
        budget_duration=duration,
        pinned_reset_at=PINNED,
        pinned_period_start=FIRST_PERIOD_START,
        now=now,
    )
    assert period is not None
    return period.start, period.end


class TestAResetCodeMieSetKeepsTheRunningPeriod:
    """Setting a reset resets no counter, so the period runs on from where it was running."""

    def test_a_new_budget_s_first_period_runs_on_to_the_reset_codemie_set(self) -> None:
        """It holds the request made 14 s before the budget existed, which LiteLLM's counter counts."""
        start, end = _period(PINNED, now=_utc(2099, 10, 9, 16, 28))
        assert (start, end) == (FIRST_PERIOD_START, PINNED)
        assert start <= CREATED - timedelta(seconds=14) < end

    def test_without_the_record_the_first_day_falls_before_the_period(self) -> None:
        """What the reporter saw: $0.00 for everything spent before 10-10 00:00."""
        assert _start(PINNED, "30d") == _utc(2099, 10, 10)

    def test_after_the_provider_resets_there_the_next_period_starts_at_that_reset(self) -> None:
        """LiteLLM's next reset is the 1st, but its counter was zeroed on the 9th, not on the 1st."""
        assert _period(_utc(2099, 12, 1), now=_utc(2099, 11, 20)) == (PINNED, _utc(2099, 12, 1))

    def test_a_later_period_follows_the_provider_s_schedule(self) -> None:
        assert _period(_utc(2100, 1, 1), now=_utc(2099, 12, 20)) == (_utc(2099, 12, 1), _utc(2100, 1, 1))

    def test_a_record_moved_before_it_was_reached_no_longer_applies(self) -> None:
        """Only CodeMie updates the record, so a reset moved before the recorded one was moved elsewhere."""
        assert _period(_utc(2099, 11, 15), now=_utc(2099, 10, 20)) == (_utc(2099, 10, 16), _utc(2099, 11, 15))


class TestARecordedStartIsNeverAhead:
    """A reset CodeMie set before it kept the record, less than a duration ago, derives a start after now."""

    #: Set on 10-14 by an admin edit before this change: `reset - 30 days` is 10-16, still ahead.
    UNRECORDED = _utc(2099, 11, 15)
    NOW = _utc(2099, 10, 14, 12)

    def test_editing_it_again_records_a_start_that_is_not_ahead(self) -> None:
        """Same day, now `1h`: the new reset is the next midnight, and a start of 10-16 would be after it."""
        started = running_period_start(
            budget_reset_at=self.UNRECORDED,
            budget_duration="30d",
            pinned_reset_at=None,
            pinned_period_start=None,
            created_at=CREATED,
            now=self.NOW,
        )
        assert started == _utc(2099, 10, 1)
        period = budget_period_range(
            budget_reset_at=_utc(2099, 10, 15),
            budget_duration="1h",
            pinned_reset_at=_utc(2099, 10, 15),
            pinned_period_start=started,
            now=self.NOW,
        )
        assert period is not None and (period.start, period.end) == (started, _utc(2099, 10, 15))

    def test_a_project_s_clamp_still_applies(self) -> None:
        period = budget_period_range(
            budget_reset_at=PINNED,
            budget_duration="30d",
            pinned_reset_at=PINNED,
            pinned_period_start=FIRST_PERIOD_START,
            effective_from=_utc(2099, 10, 20),
            now=_utc(2099, 10, 25),
        )
        assert period is not None and period.start == _utc(2099, 10, 20)


class TestTheRunningPeriodIsReadBeforeTheResetMoves:
    """What CodeMie records as it sets a budget's next reset: where the running period began."""

    @staticmethod
    def _running(reset_at, duration: str = "30d", record=(None, None), now: datetime = _utc(2099, 10, 20)):
        return running_period_start(
            budget_reset_at=reset_at,
            budget_duration=duration,
            pinned_reset_at=record[0],
            pinned_period_start=record[1],
            created_at=CREATED,
            now=now,
        )

    def test_a_new_budget_s_first_period_is_the_provider_s_period_containing_its_creation(self) -> None:
        """Not clamped to the creation: the person's counter carries on from before the budget."""
        assert self._running(None) == FIRST_PERIOD_START

    @pytest.mark.parametrize(
        ("duration", "start"),
        [
            pytest.param("7d", _utc(2099, 10, 5), id="a week starts on Monday"),
            pytest.param("1d", _utc(2099, 10, 9), id="a day starts at midnight"),
            pytest.param("14d", _utc(2099, 10, 9), id="any other day count starts at midnight"),
            pytest.param("6h", _utc(2099, 10, 9, 12), id="a shorter duration on its clock-aligned boundary"),
        ],
    )
    def test_the_provider_s_schedule_decides_where_the_first_period_starts(self, duration, start) -> None:
        assert self._running(None, duration=duration) == start

    def test_a_creation_in_another_zone_falls_in_the_utc_period(self) -> None:
        """At 01:05 in UTC+3 it is still the 8th in UTC, where LiteLLM's day begins."""
        created = datetime(2099, 10, 9, 1, 5, tzinfo=timezone(timedelta(hours=3)))
        started = running_period_start(
            budget_reset_at=None,
            budget_duration="1d",
            pinned_reset_at=None,
            pinned_period_start=None,
            created_at=created,
        )
        assert started == _utc(2099, 10, 8) and started.utcoffset() == timedelta(0)

    def test_a_change_during_the_first_period_keeps_its_recorded_start(self) -> None:
        """An admin edit moves the reset again, and the first period still began where it was recorded."""
        assert self._running(PINNED, record=(PINNED, FIRST_PERIOD_START)) == FIRST_PERIOD_START

    def test_a_budget_on_the_provider_s_schedule_began_at_the_scheduled_reset(self) -> None:
        assert self._running(_utc(2099, 11, 1)) == _utc(2099, 10, 1)

    def test_a_passed_reset_was_reached_and_the_schedule_rolled_on(self) -> None:
        """Nothing reconciled the stored reset, so the provider's own schedule says where it is now."""
        assert self._running(_utc(2099, 10, 5), duration="1d", now=_utc(2099, 10, 12, 15)) == _utc(2099, 10, 12)
        assert self._running(PINNED, now=_utc(2100, 1, 20)) == _utc(2100, 1, 1)

    def test_a_passed_reset_reached_just_now_is_the_start(self) -> None:
        assert self._running(_utc(2099, 10, 5), duration="7d", now=_utc(2099, 10, 5)) == _utc(2099, 10, 5)

    def test_a_week_rolls_on_to_the_monday_after_a_reset_codemie_set_on_a_saturday(self) -> None:
        """LiteLLM's next weekly reset is a Monday, not the Saturday a week later."""
        saturday, monday = _utc(2026, 10, 17), _utc(2026, 10, 19)
        assert (saturday.weekday(), monday.weekday()) == (5, 0)
        assert self._running(saturday, duration="7d", now=_utc(2026, 10, 21)) == monday

    def test_an_unreadable_duration_records_nothing(self) -> None:
        assert self._running(PINNED, duration="1mo") is None

    def test_a_zero_duration_names_no_period_and_records_nothing(self) -> None:
        """The write grammar admits `0d`; it has no period to divide a schedule into."""
        assert parse_budget_duration("0d") is None
        assert budget_period_range(budget_reset_at=PINNED, budget_duration="0d", now=CREATED) is None
        assert self._running(None, duration="0d") is None

    def test_a_duration_beyond_any_datetime_names_no_period(self) -> None:
        assert parse_budget_duration("99999999999d") is None

    def test_a_new_budget_without_a_creation_records_nothing(self) -> None:
        started = running_period_start(
            budget_reset_at=None,
            budget_duration="30d",
            pinned_reset_at=None,
            pinned_period_start=None,
            created_at=None,
        )
        assert started is None


class TestWhatItRefusesToSubstitute:
    def test_no_reset_instant_yields_no_period(self) -> None:
        """Not a rolling window ending now - that answers a different question convincingly."""
        assert budget_period_range(budget_reset_at=None, budget_duration="30d") is None
        assert budget_period_range(budget_reset_at="", budget_duration="30d") is None

    def test_an_unreadable_duration_yields_no_period(self) -> None:
        """`1mo` is not in the grammar the budget service validates; guessing 30 days is a silent error."""
        assert parse_budget_duration("1mo") is None
        assert budget_period_range(budget_reset_at=RESET, budget_duration="1mo", now=BEFORE_RESET) is None

    def test_a_missing_duration_yields_no_period(self) -> None:
        assert parse_budget_duration(None) is None
        assert budget_period_range(budget_reset_at=RESET, budget_duration=None, now=BEFORE_RESET) is None

    def test_a_reset_instant_already_passed_yields_no_period(self) -> None:
        """The stored instant names a closed period; the current one ends wherever a provider says."""
        assert budget_period_range(budget_reset_at=RESET, budget_duration="30d", now=RESET + timedelta(days=1)) is None

    def test_a_reset_instant_equal_to_now_yields_no_period(self) -> None:
        """Half-open: at the reset instant the period ending there has already closed."""
        assert budget_period_range(budget_reset_at=RESET, budget_duration="30d", now=RESET) is None

    def test_without_an_injected_now_the_clock_decides(self) -> None:
        assert budget_period_range(budget_reset_at=RESET - timedelta(days=3650), budget_duration="30d") is None


class TestTheResetInstantIsAlwaysAware:
    def test_a_zulu_string_is_read_as_utc(self) -> None:
        assert parse_budget_reset_at("2026-09-01T00:00:00Z") == RESET

    def test_a_naive_value_is_read_as_utc_rather_than_local(self) -> None:
        """Every interval in this design is UTC; a naive value compared against an aware one raises."""
        assert parse_budget_reset_at(datetime(2026, 9, 1)) == RESET

    def test_an_already_aware_datetime_passes_through(self) -> None:
        assert parse_budget_reset_at(RESET) == RESET
