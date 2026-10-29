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

"""Freshness rules for turning provider spend snapshots into spend tracking rows.

Shared by the live member spend and project total refreshes so both write and touch
rows the same way.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable
from uuid import uuid4

from codemie.configs import logger
from codemie.service.analytics.handlers.budget_usage_service import _compute_spend_delta
from codemie.service.spend_tracking.spend_collector_service import (
    InvalidSpendSnapshotError,
    LiteLLMSpendCollectorService,
)
from codemie.service.spend_tracking.spend_models import ProjectSpendTracking

_as_utc = LiteLLMSpendCollectorService._as_utc


def _snapshot_to_row(
    snapshot: Any,
    prev_row: Any | None,
    budget: Any | None,
    now: datetime,
    *,
    allow_zero: bool = False,
    spend_subject_type: str = "member_budget",
) -> ProjectSpendTracking | None:
    """Convert one provider snapshot into a ``spend_subject_type`` row, or None to skip it.

    ``allow_zero`` keeps a zero-delta row, which seeds the first row of a budget so the
    user-scoped freshness check has a timestamp to read.
    """
    quantize = LiteLLMSpendCollectorService._quantize_spend
    fresh_spend = quantize(snapshot.spend)
    user_id = getattr(snapshot, "user_id", None)
    try:
        daily_spend, cumulative_spend = _compute_spend_delta(fresh_spend, prev_row, budget, now)
    except InvalidSpendSnapshotError as exc:
        logger.warning(
            f"Skipping invalid {spend_subject_type} snapshot for project={snapshot.project_name!r} "
            f"user={user_id!r} budget_id={snapshot.budget_id!r}: {exc}"
        )
        return None

    if (
        daily_spend == Decimal("0")
        and not allow_zero
        and not LiteLLMSpendCollectorService.is_reset_transition(prev_row, budget, fresh_spend, now)
    ):
        return None

    return ProjectSpendTracking(
        id=uuid4(),
        project_name=snapshot.project_name,
        spend_date=now,
        daily_spend=daily_spend,
        cumulative_spend=quantize(
            snapshot.cumulative_spend if snapshot.cumulative_spend is not None else cumulative_spend
        ),
        budget_period_spend=fresh_spend,
        budget_id=snapshot.budget_id,
        budget_category=snapshot.budget_category.value,
        user_id=user_id,
        provider_subject_id=snapshot.provider_subject_id,
        spend_subject_type=spend_subject_type,
    )


def _rows_to_write(
    snapshots: list[Any],
    prev_rows: dict[tuple[str, ...], Any],
    prev_key: Callable[[Any], tuple[str, ...]],
    budgets_map: dict[str, Any],
    cutoffs_by_project: dict[str, dict[str, datetime]],
    now: datetime,
    spend_subject_type: str,
) -> tuple[list[ProjectSpendTracking], list[Any]]:
    """Split provider snapshots into rows to insert and ids of unchanged rows to touch."""
    rows: list[ProjectSpendTracking] = []
    touch_ids = []
    for snapshot in snapshots:
        prev_row = prev_rows.get(prev_key(snapshot))
        budget = budgets_map.get(snapshot.budget_id)
        cutoff = cutoffs_by_project.get(snapshot.project_name, {}).get(snapshot.budget_category.value)
        if _is_hidden_and_unchanged(prev_row, snapshot, cutoff):
            continue
        if _is_unchanged_since(prev_row, snapshot, budget, cutoff, now):
            touch_ids.append(prev_row.id)
            continue
        row = _snapshot_to_row(
            snapshot, prev_row, budget, now, allow_zero=prev_row is None, spend_subject_type=spend_subject_type
        )
        if row is not None:
            rows.append(row)
    return rows, touch_ids


def _is_stale(timestamp: datetime | None) -> bool:
    """Return True when there is no timestamp or it is older than the member spend TTL."""
    from codemie.configs.config import config

    if timestamp is None:
        return True
    age_ms = (datetime.now(timezone.utc) - _as_utc(timestamp)).total_seconds() * 1000
    return age_ms > config.BUDGET_MEMBER_SPEND_STALENESS_THRESHOLD_MS


def _is_hidden_and_unchanged(prev_row: Any | None, snapshot: Any, cutoff: datetime | None) -> bool:
    """Return True when the provider still reports a pre-reset value that the cutoff hides.

    Writing anything here would date the old value after the cutoff and show it again,
    so the row is left alone until the provider's counter moves.
    """
    if prev_row is None or cutoff is None:
        return False
    quantize = LiteLLMSpendCollectorService._quantize_spend
    return quantize(prev_row.budget_period_spend) == quantize(snapshot.spend) and _as_utc(prev_row.spend_date) <= cutoff


def _is_unchanged_since(
    prev_row: Any | None,
    snapshot: Any,
    budget: Any | None,
    cutoff: datetime | None,
    now: datetime,
) -> bool:
    """Return True when ``prev_row`` still holds the provider's current spend and may be touched.

    A row at or before its reset cutoff is never touched: moving its spend_date past the
    cutoff would bring pre-reset spend back on screen. Equal spend across a budget reset
    is a new period, not an unchanged one.
    """
    if prev_row is None:
        return False
    quantize = LiteLLMSpendCollectorService._quantize_spend
    if quantize(prev_row.budget_period_spend) != quantize(snapshot.spend):
        return False
    if cutoff is not None and _as_utc(prev_row.spend_date) <= cutoff:
        return False
    return not LiteLLMSpendCollectorService._did_budget_reset(prev_row, budget, now)
