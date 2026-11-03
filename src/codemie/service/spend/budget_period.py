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

"""The time range a budget-period spend figure is computed over, defined once.

`SPEC.md` CAP-3 says a holder's consumed amount is aggregated **"over the budget period"**, and
leaves what that period *is* to the budget policy that already exists - reset-window semantics being
an explicit SPEC non-goal. So this module reads the two policy fields already on a budget - and, when
known, the instant the budget began - and turns them into the half-open range every spend read needs.
It defines no policy of its own: it follows the provider's reset rule and derives the range.

**One boundary cannot be derived, so it is recorded.** CodeMie sets a global budget's next reset
itself - wherever it sends the provider no reset, on creating a budget and on changing one, the
enterprise client sends `now + duration` rounded up to a UTC midnight - and that resets no counter:
the period then running carries on to the new reset. Nothing in the provider records where that
period began, so the budget does (`pinned_reset_at`, `pinned_period_start`; see
`running_period_start`), and the range reads it back for as long as the reset is still the one
CodeMie set (EPMCDME-15204).

**Why one module and not a line at each call site.** The figure formerly came from LiteLLM's own
counter, which reset itself, so no CodeMie code ever had to name the window. The ledger has no
counter - it has rows and a range - so every budget surface must now supply one. Four surfaces
supplying it separately is the "five routes to one number" failure arriving in a new place: three
would compute `[reset - duration, reset)` and the fourth `[period_start, now)`, and the two would
disagree by whatever was spent in the remainder of the period, on screens sitting next to each other.

**The range ends at the reset instant, not at "now".** A budget period is the interval the limit
applies to, and the figure is *consumption within that period*. Truncating it at the current instant
returns the same number - nothing is recorded in the future - so the bound is the period's own, one
definition of the interval for every screen.

**A budget with no reset instant has no period, and that is reported as no range rather than as a
guess.** The tempting substitute - `[now - duration, now)` - is a rolling window, not a budget
period: it silently answers a different question, and its answer looks exactly like the right one.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from codemie.configs import logger
from codemie.service.spend.coverage import TimeRange

__all__ = [
    "budget_period_range",
    "later_start",
    "parse_budget_duration",
    "parse_budget_reset_at",
    "running_period_start",
]

#: The duration grammar the budget service already validates on write
#: (`project_budget_service._DURATION_RE`). Re-stated rather than imported to keep this module free
#: of a dependency on the budget service, and deliberately **not widened**: a unit this does not
#: know is reported, never guessed at, because guessing "1mo" is 30 days is a silent 1-day error at
#: the end of every long month.
_DURATION = re.compile(r"^(?P<amount>\d+)(?P<unit>[smhd])$")

_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_MAX_SECONDS = timedelta.max.total_seconds()

#: The duration LiteLLM turns into a calendar-month reset (see `_previous_reset`).
_MONTHLY_DURATION = timedelta(days=30)
#: The duration LiteLLM resets every Monday (`duration_parser._handle_day_reset`).
_WEEKLY_DURATION = timedelta(days=7)


def parse_budget_reset_at(value: str | datetime | None) -> datetime | None:
    """The next reset instant as an aware UTC datetime, or None when the budget has none.

    Accepts the string form the `budgets` table stores as well as an already-parsed value, because
    callers hold both: the ORM column is text, while some paths have parsed it before reaching here.
    A naive datetime is read as UTC - every interval in this design is UTC (AD-20), so attaching the
    zone here is what keeps a naive value from being compared against an aware one and raising.
    """
    if value is None or value == "":
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def parse_budget_duration(value: str | None) -> timedelta | None:
    """A budget duration as a timedelta, or None when it cannot be read.

    Returns None rather than a default for an unreadable value. A default here - a month, say -
    would produce a plausible range for a budget whose real period is a day, and every figure over
    it would be too large by a factor nobody would notice.
    """
    if not value:
        return None
    match = _DURATION.match(value.strip())
    seconds = int(match.group("amount")) * _UNIT_SECONDS[match.group("unit")] if match else 0
    # Zero names no period, and an amount beyond `timedelta` names none it can hold.
    if not 0 < seconds <= _MAX_SECONDS:
        logger.warning(
            f"spend_budget_period: cannot read budget_duration {value!r}; no budget period is derived "
            "from it, so the figure is reported as unavailable rather than over a guessed window"
        )
        return None
    return timedelta(seconds=seconds)


def _as_utc(value: datetime) -> datetime:
    """`value` as an aware UTC datetime; a naive value is read as UTC, like a reset instant."""
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    # TimeRange rejects any offset but zero, so an aware value in another zone is converted.
    return aware.astimezone(timezone.utc)


def later_start(*instants: datetime | None) -> datetime | None:
    """The latest of the given instants as an aware UTC datetime, or None when none is given.

    For combining several "starts no earlier than" bounds into one `effective_from`; a naive value
    is read as UTC, as `budget_period_range` reads it.
    """
    return max((_as_utc(instant) for instant in instants if instant is not None), default=None)


def _previous_reset(reset_at: datetime, duration: timedelta) -> datetime:
    """The scheduled reset instant before `reset_at`, where the current period began.

    LiteLLM resets a `30d` budget on the 1st of every month, not every 30 days
    (`duration_parser._handle_day_reset` hands 30 to `_handle_month_reset`), so the period it
    enforces is one calendar month of 28 to 31 days: `reset - 30 days` would drop the 1st of a 31-day
    month and reach back into January after February. Every other duration is taken as the span
    before the reset: exact for day durations, approximate only for an hour, minute or second count
    that does not divide the next unit up, whose resets LiteLLM re-aligns to the clock (`5h` runs
    20:00, 01:00, 05:00). A reset CodeMie set itself is off this schedule, and its period is recorded
    instead (`_period_start`).
    """
    if duration != _MONTHLY_DURATION or reset_at.day != 1:
        return reset_at - duration
    # Replacing the month is safe because day 1 exists in every month.
    if reset_at.month == 1:
        return reset_at.replace(year=reset_at.year - 1, month=12)
    return reset_at.replace(month=reset_at.month - 1)


def _scheduled_period_start(instant: datetime, duration: timedelta) -> datetime:
    """Where LiteLLM's own schedule starts the period containing `instant`.

    A `30d` period starts on the 1st, a `7d` one on a Monday, any other day count at midnight, and a
    shorter duration on its clock-aligned boundary - all UTC, LiteLLM's default.
    """
    if duration == _MONTHLY_DURATION:
        return instant.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    midnight = instant.replace(hour=0, minute=0, second=0, microsecond=0)
    if duration == _WEEKLY_DURATION:
        return midnight - timedelta(days=midnight.weekday())
    if duration >= timedelta(days=1):
        return midnight
    return midnight + (instant - midnight) // duration * duration


def _next_reset(reset_at: datetime, duration: timedelta) -> datetime:
    """The reset LiteLLM schedules when it resets a budget at `reset_at`: the end of its scheduled period."""
    start = _scheduled_period_start(reset_at, duration)
    if duration != _MONTHLY_DURATION:
        return start + duration
    if start.month == 12:
        return start.replace(year=start.year + 1, month=1)
    return start.replace(month=start.month + 1)


def _latest_reset(reset_at: datetime, duration: timedelta, now: datetime) -> datetime:
    """The last reset LiteLLM has reached by `now`, its schedule rolled on from a reset at `reset_at`."""
    following = _next_reset(reset_at, duration)
    if following > now:
        return reset_at
    if duration != _MONTHLY_DURATION:
        # From its first scheduled reset on, the next one is always a whole duration later.
        return following + (now - following) // duration * duration
    while (later := _next_reset(following, duration)) <= now:
        following = later
    return following


def _period_start(
    reset_at: datetime,
    duration: timedelta,
    pinned_reset_at: datetime | None,
    pinned_period_start: datetime | None,
    now: datetime,
) -> datetime:
    """Where the current period, which ends at `reset_at`, began.

    The provider's schedule decides it, except around a reset CodeMie set itself: while that is still
    the reset, the period runs on from where it was running when CodeMie set it; once the provider
    has reset the budget there, it schedules every reset after it. A recorded reset that moved
    before it was reached was moved outside CodeMie, so the record no longer describes this period.
    """
    scheduled = _previous_reset(reset_at, duration)
    if pinned_reset_at is None or pinned_period_start is None:
        return scheduled
    pinned = _as_utc(pinned_reset_at)
    if pinned == reset_at:
        return _as_utc(pinned_period_start)
    if pinned <= now:
        return max(pinned, scheduled)
    return scheduled


def running_period_start(
    *,
    budget_reset_at: str | datetime | None,
    budget_duration: str | None,
    pinned_reset_at: datetime | None,
    pinned_period_start: datetime | None,
    created_at: datetime | None,
    now: datetime | None = None,
) -> datetime | None:
    """Where the budget's running period began, to record as CodeMie sets the budget's next reset.

    Setting a reset resets no counter, so the period keeps this start and ends at the new reset.
    Callers store the pair as `pinned_reset_at` and `pinned_period_start`, from the budget as it was
    before the change. A budget with no reset yet is new: its first period is the one LiteLLM's own
    schedule puts its creation in, which CodeMie's first reset only prolongs - not clamped to the
    creation, because a person's LiteLLM counter carries on from before the budget was assigned, as
    the period of a budget on the provider's schedule is not (EPMCDME-15204: the reporter's counter
    includes a request made 14 s before the budget existed). A stored reset that has passed was
    reached, and the provider has reset the budget on its own schedule since. A start after `now`
    comes from a reset CodeMie set less than a duration ago, before it kept this record: that period
    is the provider's period the reset was set in, and recording the later start would end the next
    period before it began. None when the period cannot be derived: nothing is then recorded.
    """
    now = now if now is not None else datetime.now(timezone.utc)
    duration = parse_budget_duration(budget_duration)
    if duration is None:
        return None
    reset_at = parse_budget_reset_at(budget_reset_at)
    if reset_at is None:
        return _scheduled_period_start(_as_utc(created_at), duration) if created_at is not None else None
    if reset_at <= now:
        return _latest_reset(reset_at, duration, now)
    started = _period_start(reset_at, duration, pinned_reset_at, pinned_period_start, now)
    return started if started <= now else _scheduled_period_start(now, duration)


def budget_period_range(
    *,
    budget_reset_at: str | datetime | None,
    budget_duration: str | None,
    pinned_reset_at: datetime | None = None,
    pinned_period_start: datetime | None = None,
    effective_from: datetime | None = None,
    now: datetime | None = None,
) -> TimeRange | None:
    """The current budget period as a half-open UTC range, or None if it cannot be derived.

    Half-open like every interval here (AD-20): a spend row landing exactly on the reset instant
    belongs to the *next* period, and a closed upper bound would count it in both.

    The period starts at the previous reset instant, or where a recorded reset CodeMie set says it
    began (`pinned_reset_at`, `pinned_period_start`; see `_period_start`): a global budget created on
    the 9th first resets on the 9th a month later, and without the record everything spent before
    `reset - duration` would fall outside its first period.

    The start moves to `effective_from` if that is later. A project budget created mid-period is
    enforced by LiteLLM only from its creation - its key counter starts at zero then (EPMCDME-15236) -
    so spend from before the budget existed is not consumption of it. Callers pass `effective_from`
    only when they know when enforcement began, and a naive value is read as UTC like the reset
    instant. It never reaches the reset instant for a current period, because
    `effective_from <= now < reset`.

    A reset instant that is not after `now` (default: the current instant) names a period that has
    already closed, and where the current one ends is unknown until a provider reconciles the instant.
    So no period is derived: serving the closed one would show its spend as current, and rolling the
    instant forward by arithmetic is a guess - the same refusal as for an unreadable duration.
    """
    reset_at = parse_budget_reset_at(budget_reset_at)
    duration = parse_budget_duration(budget_duration)
    if reset_at is None or duration is None:
        return None
    now = now if now is not None else datetime.now(timezone.utc)
    if reset_at <= now:
        logger.warning(
            f"spend_budget_period: budget_reset_at {reset_at.isoformat()} has passed and names a closed "
            "period; no current period is derived until a provider reconciles it, so the figure is "
            "reported as unavailable rather than over the closed period"
        )
        return None
    start = _period_start(reset_at, duration, pinned_reset_at, pinned_period_start, now)
    if effective_from is not None:
        start = max(start, _as_utc(effective_from))
    return TimeRange(start, reset_at)
