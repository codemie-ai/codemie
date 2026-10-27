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

"""record_pinned_budget_resets

Revision ID: 9a36991134b3
Revises: c7f15537b2c3
Create Date: 2026-10-12 00:00:00.000000

EPMCDME-15204: where a global budget's period began when CodeMie set its reset itself.

CodeMie sets a global budget's next reset itself - on creation and on every change it sends to the
provider, the enterprise client sends `now + duration` rounded up to a UTC midnight - and the provider
resets no counter then: the running period carries on to the new reset, and nothing records where it
began. Derived as `reset - duration`, a new budget's first period lost its first day: a budget created
2026-10-09 15:05 with its reset on 11-09 00:00 showed $0.00 for the $0.0917 its user's LiteLLM counter
held. `pinned_reset_at` and `pinned_period_start` record the reset CodeMie set and where its period
began; the budget service writes them from now on (`budget_period.running_period_start`).

**Backfill.** The one case existing rows answer exactly is a global budget CodeMie created whose reset is
still the one set at its creation: a UTC midnight at or after `created_at + duration` and less than a
day later. Any later reset that falls on a midnight is a day or more after it, which tells the two apart
for every duration. A budget imported from the provider - the sync names it after its id and gives it
no description, the customer backfill describes it as imported - was created elsewhere, and its
`created_at` is only when it arrived, so it is left out. Its period began where LiteLLM's own schedule starts the period containing the creation (the
1st for `30d`, a Monday for `7d`, midnight for other day counts, the clock-aligned boundary below a
day): the person's counter carries on from before the budget, so the period is not clamped to the
creation. Every other row records nothing, and its period is derived from the provider's schedule as
before. Durations are read with the grammar the budget service validates on write, and the schedule is
restated from `budget_period`, so that this revision does not change with the application.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9a36991134b3"
down_revision: Union[str, None] = "c7f15537b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DURATION = re.compile(r"^(?P<amount>\d+)(?P<unit>[smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
#: Days a reset can be stepped back from without leaving `datetime`'s range, generously.
_MAX_DAYS = 3650
_IMPORTED_BY_BACKFILL = "Imported from provider customer backfill"


def _duration(value: str | None) -> timedelta | None:
    match = _DURATION.match((value or "").strip())
    seconds = int(match["amount"]) * _UNIT_SECONDS[match["unit"]] if match else 0
    return timedelta(seconds=seconds) if 0 < seconds <= _MAX_DAYS * 86400 else None


def _created_by_codemie(budget_id: str, name: str, description: str | None) -> bool:
    return description != _IMPORTED_BY_BACKFILL and not (name == budget_id and description is None)


def _instant(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _is_creation_reset(reset_at: datetime, created_at: datetime, duration: timedelta) -> bool:
    is_midnight = reset_at == reset_at.replace(hour=0, minute=0, second=0, microsecond=0)
    return is_midnight and created_at + duration <= reset_at < created_at + duration + timedelta(days=1)


def _scheduled_period_start(instant: datetime, duration: timedelta) -> datetime:
    if duration == timedelta(days=30):
        return instant.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    midnight = instant.replace(hour=0, minute=0, second=0, microsecond=0)
    if duration == timedelta(days=7):
        return midnight - timedelta(days=midnight.weekday())
    if duration >= timedelta(days=1):
        return midnight
    return midnight + (instant - midnight) // duration * duration


def upgrade() -> None:
    op.add_column("budgets", sa.Column("pinned_reset_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("budgets", sa.Column("pinned_period_start", sa.TIMESTAMP(timezone=True), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT budget_id, name, description, budget_duration, budget_reset_at, created_at FROM budgets "
            "WHERE budget_type = 'global' AND budget_reset_at IS NOT NULL"
        )
    ).all()
    pins = []
    for budget_id, name, description, budget_duration, budget_reset_at, created_at in rows:
        duration = _duration(budget_duration)
        reset_at = _instant(budget_reset_at)
        if (
            duration is not None
            and reset_at is not None
            and _created_by_codemie(budget_id, name, description)
            and _is_creation_reset(reset_at, created_at, duration)
        ):
            created = created_at.astimezone(timezone.utc)
            pins.append(
                {
                    "budget_id": budget_id,
                    "reset_at": reset_at,
                    "period_start": _scheduled_period_start(created, duration),
                }
            )
    if pins:
        bind.execute(
            sa.text(
                "UPDATE budgets SET pinned_reset_at = :reset_at, pinned_period_start = :period_start "
                "WHERE budget_id = :budget_id"
            ),
            pins,
        )


def downgrade() -> None:
    op.drop_column("budgets", "pinned_period_start")
    op.drop_column("budgets", "pinned_reset_at")
