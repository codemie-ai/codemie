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

from __future__ import annotations

import logging

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from codemie.repository.project_budget_repository import (
    ProjectAssignedBudgetSummaryRow,
    project_budget_assignment_repository,
)

# Use the service-layer enum — NOT codemie.enterprise.litellm.budget_categories.BudgetCategory,
# which is a separate, identically-shaped enum that project_budget_repository/projects.py do not use.
from codemie.service.budget.budget_enums import BudgetCategory, SyncStatus
from codemie.service.project.project_spending_service import latest_key_rows_by_project, spend_repo

logger = logging.getLogger(__name__)

# Rows whose provider-side sync hasn't completed (or failed) don't represent an active,
# provider-enforced budget yet. A Budget row can only reach this state transiently: a failed
# sync on the Budget itself rolls back and is never persisted (see
# ProjectBudgetService._sync_created_project_budget), so this guard is defensive/documentary —
# it mirrors how projects.py exposes provider_sync_status as display-only metadata, while making
# the rollup's intent explicit rather than silently inheriting that choice.
_INACTIVE_SYNC_STATUSES = {SyncStatus.PENDING.value, SyncStatus.FAILED.value}


class CostCenterAssignedBudgetSummary(BaseModel):
    """Per-category budget summary aggregated across a cost center's linked projects."""

    budget_category: BudgetCategory
    budget_duration: str | None = None
    soft_budget: float
    max_budget: float
    member_count: int
    allocated_member_budget_total: float
    current_spending: float
    excluded_count: int = 0
    unsynced_count: int = 0


class CostCenterBudgetSummary(BaseModel):
    """Combined budget summary aggregated across a cost center's linked projects.

    Mirrors ProjectSpendingSummary (current_spending, budget_limit, total_percent)
    plus a per-category breakdown list, for data-contract parity with the existing
    project-level pattern in rest_api/models/project.py and
    service/project/project_spending_service.py.
    """

    current_spending: float
    budget_limit: float | None = None
    total_percent: float
    budgets: list[CostCenterAssignedBudgetSummary]


# The common case: most budgets reset monthly. Preferring it as the category's accepted
# window — rather than whichever row happens to be processed first — means a single
# weekly/daily project linked alongside several monthly ones gets excluded instead of
# excluding all the monthly ones (see _pick_category_duration).
_PREFERRED_DURATION = "30d"


def _group_rows_by_category(
    rows_by_project: dict[str, list[ProjectAssignedBudgetSummaryRow]],
) -> dict[BudgetCategory, list[ProjectAssignedBudgetSummaryRow]]:
    """Flatten every project's rows into one list per valid BudgetCategory.

    Rows whose category string isn't a recognized BudgetCategory value are logged and
    dropped here, before any duration/sync-status classification runs.
    """
    grouped: dict[BudgetCategory, list[ProjectAssignedBudgetSummaryRow]] = {}
    for rows in rows_by_project.values():
        for row in rows:
            try:
                category = BudgetCategory(row.budget_category)
            except ValueError:
                logger.warning(
                    f"Skipping assigned budget row with invalid category "
                    f"for project={row.project_name} budget_id={row.budget_id} category={row.budget_category}"
                )
                continue
            grouped.setdefault(category, []).append(row)
    return grouped


def _pick_category_duration(rows: list[ProjectAssignedBudgetSummaryRow]) -> str | None:
    """Pick the budget_duration every accepted row in this category must share.

    Prefers the monthly window whenever any row has it, regardless of row order; falls
    back to the first row's duration only when no row is monthly, so an all-weekly or
    all-daily category still gets a deterministic window.
    """
    for row in rows:
        if row.budget_duration == _PREFERRED_DURATION:
            return _PREFERRED_DURATION
    return rows[0].budget_duration if rows else None


class _RowAggregation:
    """Totals/counts for every project's assigned budget rows, aggregated per category."""

    def __init__(self) -> None:
        self.totals: dict[BudgetCategory, dict[str, float]] = {}
        self.category_duration: dict[BudgetCategory, str] = {}
        self.excluded_counts: dict[BudgetCategory, int] = {}
        self.unsynced_counts: dict[BudgetCategory, int] = {}
        self.accepted_budget_ids: dict[BudgetCategory, set[str]] = {}

    def add_category(self, category: BudgetCategory, rows: list[ProjectAssignedBudgetSummaryRow]) -> None:
        """Split one category's rows into unsynced / excluded / accepted and fold them in."""
        synced_rows = []
        for row in rows:
            if row.provider_sync_status in _INACTIVE_SYNC_STATUSES:
                logger.warning(
                    f"Skipping assigned budget row with inactive provider sync "
                    f"for project={row.project_name} budget_id={row.budget_id} "
                    f"provider_sync_status={row.provider_sync_status}"
                )
                self.unsynced_counts[category] = self.unsynced_counts.get(category, 0) + 1
                continue
            synced_rows.append(row)

        winning_duration = _pick_category_duration(synced_rows)
        if winning_duration is None:
            return
        self.category_duration[category] = winning_duration

        for row in synced_rows:
            if row.budget_duration != winning_duration:
                logger.warning(
                    f"Skipping assigned budget row with mismatched budget_duration "
                    f"for project={row.project_name} budget_id={row.budget_id} category={category.value} "
                    f"accepted_duration={winning_duration} actual_duration={row.budget_duration}"
                )
                self.excluded_counts[category] = self.excluded_counts.get(category, 0) + 1
                continue

            bucket = self.totals.setdefault(
                category, {"soft_budget": 0.0, "max_budget": 0.0, "allocated": 0.0, "spend": 0.0}
            )
            bucket["soft_budget"] += float(row.soft_budget)
            bucket["max_budget"] += float(row.max_budget)
            bucket["allocated"] += float(row.allocated_member_budget_total)
            bucket["spend"] += float(row.current_spending or 0.0)
            self.accepted_budget_ids.setdefault(category, set()).add(row.budget_id)

    def flat_accepted_budget_ids(self) -> list[str]:
        return [budget_id for ids in self.accepted_budget_ids.values() for budget_id in ids]

    def touched_categories(self) -> set[BudgetCategory]:
        return set(self.totals) | set(self.excluded_counts) | set(self.unsynced_counts)


def _aggregate_rows(rows_by_project: dict[str, list[ProjectAssignedBudgetSummaryRow]]) -> _RowAggregation:
    aggregation = _RowAggregation()
    for category, rows in _group_rows_by_category(rows_by_project).items():
        aggregation.add_category(category, rows)
    return aggregation


async def _fetch_key_spend_total(async_session: AsyncSession, project_names: list[str]) -> float:
    """Sum the latest key-based spend row per project, for parity with the project page."""
    if not project_names:
        return 0.0
    key_rows = await spend_repo.get_latest_spending_by_project(async_session, project_names, spend_subject_type="key")
    latest_key_by_project = latest_key_rows_by_project(key_rows)
    return sum(float(row.budget_period_spend) for row in latest_key_by_project.values())


def _build_category_summaries(
    aggregation: _RowAggregation,
    member_counts_by_category: dict[str, int],
) -> list[CostCenterAssignedBudgetSummary]:
    touched = aggregation.touched_categories()
    return [
        CostCenterAssignedBudgetSummary(
            budget_category=category,
            budget_duration=aggregation.category_duration.get(category),
            soft_budget=round(aggregation.totals.get(category, {}).get("soft_budget", 0.0), 2),
            max_budget=round(aggregation.totals.get(category, {}).get("max_budget", 0.0), 2),
            member_count=member_counts_by_category.get(category.value, 0),
            allocated_member_budget_total=round(aggregation.totals.get(category, {}).get("allocated", 0.0), 2),
            current_spending=round(aggregation.totals.get(category, {}).get("spend", 0.0), 2),
            excluded_count=aggregation.excluded_counts.get(category, 0),
            unsynced_count=aggregation.unsynced_counts.get(category, 0),
        )
        for category in BudgetCategory
        if category in touched
    ]


def _compute_grand_totals(
    budgets: list[CostCenterAssignedBudgetSummary],
    key_spend_total: float,
) -> tuple[float | None, float, float]:
    """Combine per-category figures into the response's top-level spend/limit/percent.

    budget_limit is only returned when every category shares one budget_duration — grand
    totals across different reset windows are otherwise ambiguous. total_percent is clamped
    to 100 so it can't disagree with a UI that clamps its own bars the same way.
    """
    durations = {b.budget_duration for b in budgets if b.budget_duration is not None}
    has_single_duration = bool(budgets) and len(durations) <= 1
    budget_limit = round(sum(b.max_budget for b in budgets), 2) if has_single_duration else None

    current_spending = round(sum(b.current_spending for b in budgets) + key_spend_total, 2)
    if not budget_limit:
        return budget_limit, current_spending, 0.0

    total_percent = min(round((current_spending / budget_limit * 100), 2), 100.0)
    return budget_limit, current_spending, total_percent


async def get_cost_center_budget_summary(
    async_session: AsyncSession,
    project_names: list[str],
) -> CostCenterBudgetSummary:
    """Sum platform/cli/premium_models budget rows across the given projects.

    Pure-async and takes an already-resolved project name list so sibling callers
    with their own project lists can reuse this without re-resolving names or
    reimplementing summation. Never touches a sync Session.

    current_spending mirrors the project page's "Spend" (assigned-budget spend plus key
    spend), reusing project_spending_service's bulk key-row helpers rather than
    reimplementing them.
    """
    rows_by_project: dict[str, list[ProjectAssignedBudgetSummaryRow]] = {}
    if project_names:
        rows_by_project = await project_budget_assignment_repository.get_assigned_budget_summaries_for_projects(
            async_session, project_names
        )
    key_spend_total = await _fetch_key_spend_total(async_session, project_names)

    aggregation = _aggregate_rows(rows_by_project)

    member_counts_by_category: dict[str, int] = {}
    if project_names:
        member_counts_by_category = await project_budget_assignment_repository.get_distinct_member_counts_by_category(
            async_session, project_names, budget_ids=aggregation.flat_accepted_budget_ids()
        )

    budgets = _build_category_summaries(aggregation, member_counts_by_category)
    budget_limit, current_spending, total_percent = _compute_grand_totals(budgets, key_spend_total)

    return CostCenterBudgetSummary(
        current_spending=current_spending,
        budget_limit=budget_limit,
        total_percent=total_percent,
        budgets=budgets,
    )
