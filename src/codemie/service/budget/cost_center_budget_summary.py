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

from codemie.clients.postgres import get_async_session
from codemie.repository.project_budget_repository import (
    ProjectAssignedBudgetSummaryRow,
    project_budget_assignment_repository,
)

# Use the service-layer enum — NOT codemie.enterprise.litellm.budget_categories.BudgetCategory,
# which is a separate, identically-shaped enum that project_budget_repository/projects.py do not use.
from codemie.service.budget.budget_enums import BudgetCategory, SyncStatus
from codemie.service.project.project_spending_service import project_budget_spend
from codemie.service.spend.coverage import SpendResult
from codemie.service.spend.rendering import RenderedSpend, combine_rendered_spend, render_budget_spend

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
    # None means no figure, never a zero; the spend_* fields say what qualifies a figure that exists.
    current_spending: float | None = None
    spend_unavailable: bool = False
    spend_partial: bool = False
    spend_missing_amount: float | None = None
    excluded_count: int = 0
    unsynced_count: int = 0


class CostCenterBudgetSummary(BaseModel):
    """Combined budget summary aggregated across a cost center's linked projects.

    Mirrors ProjectSpendingSummary (current_spending, budget_limit, total_percent and the spend_*
    annotation) plus a per-category breakdown list, for data-contract parity with the existing
    project-level pattern in rest_api/models/project.py and
    service/project/project_spending_service.py.
    """

    current_spending: float | None = None
    budget_limit: float | None = None
    total_percent: float | None = None
    spend_unavailable: bool = False
    spend_partial: bool = False
    spend_missing_amount: float | None = None
    budgets: list[CostCenterAssignedBudgetSummary]


# The common case: most budgets reset monthly. Preferring it as the category's accepted
# window — rather than whichever row happens to be processed first — means a single
# weekly/daily project linked alongside several monthly ones gets excluded instead of
# excluding all the monthly ones (see _pick_category_duration).
_PREFERRED_DURATION = "30d"

_BudgetSpend = tuple[ProjectAssignedBudgetSummaryRow, SpendResult | None]


def _group_by_category(
    spend_by_project: dict[str, list[_BudgetSpend]],
) -> dict[BudgetCategory, list[_BudgetSpend]]:
    """Flatten every project's budget rows into one list per valid BudgetCategory.

    Rows whose category string isn't a recognized BudgetCategory value are logged and
    dropped here, before any duration/sync-status classification runs.
    """
    grouped: dict[BudgetCategory, list[_BudgetSpend]] = {}
    for pairs in spend_by_project.values():
        for row, result in pairs:
            try:
                category = BudgetCategory(row.budget_category)
            except ValueError:
                logger.warning(
                    f"Skipping assigned budget row with invalid category "
                    f"for project={row.project_name} budget_id={row.budget_id} category={row.budget_category}"
                )
                continue
            grouped.setdefault(category, []).append((row, result))
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
        self.spend: dict[BudgetCategory, list[RenderedSpend]] = {}
        self.category_duration: dict[BudgetCategory, str] = {}
        self.excluded_counts: dict[BudgetCategory, int] = {}
        self.unsynced_counts: dict[BudgetCategory, int] = {}
        self.accepted_budget_ids: dict[BudgetCategory, set[str]] = {}

    def add_category(self, category: BudgetCategory, pairs: list[_BudgetSpend]) -> None:
        """Split one category's rows into unsynced / excluded / accepted and fold them in."""
        synced = []
        for row, result in pairs:
            if row.provider_sync_status in _INACTIVE_SYNC_STATUSES:
                logger.warning(
                    f"Skipping assigned budget row with inactive provider sync "
                    f"for project={row.project_name} budget_id={row.budget_id} "
                    f"provider_sync_status={row.provider_sync_status}"
                )
                self.unsynced_counts[category] = self.unsynced_counts.get(category, 0) + 1
                continue
            synced.append((row, result))

        winning_duration = _pick_category_duration([row for row, _ in synced])
        if winning_duration is None:
            return
        self.category_duration[category] = winning_duration

        for row, result in synced:
            if row.budget_duration != winning_duration:
                logger.warning(
                    f"Skipping assigned budget row with mismatched budget_duration "
                    f"for project={row.project_name} budget_id={row.budget_id} category={category.value} "
                    f"accepted_duration={winning_duration} actual_duration={row.budget_duration}"
                )
                self.excluded_counts[category] = self.excluded_counts.get(category, 0) + 1
                continue

            bucket = self.totals.setdefault(category, {"soft_budget": 0.0, "max_budget": 0.0, "allocated": 0.0})
            bucket["soft_budget"] += float(row.soft_budget)
            bucket["max_budget"] += float(row.max_budget)
            bucket["allocated"] += float(row.allocated_member_budget_total)
            self.spend.setdefault(category, []).append(render_budget_spend(result))
            self.accepted_budget_ids.setdefault(category, set()).add(row.budget_id)

    def category_spend(self, category: BudgetCategory) -> RenderedSpend | None:
        """The category's combined figure, or None when no budget of it was accepted."""
        rendered = self.spend.get(category)
        return combine_rendered_spend(rendered) if rendered else None

    def flat_accepted_budget_ids(self) -> list[str]:
        return [budget_id for ids in self.accepted_budget_ids.values() for budget_id in ids]

    def touched_categories(self) -> set[BudgetCategory]:
        return set(self.totals) | set(self.excluded_counts) | set(self.unsynced_counts)


def _aggregate(spend_by_project: dict[str, list[_BudgetSpend]]) -> _RowAggregation:
    aggregation = _RowAggregation()
    for category, pairs in _group_by_category(spend_by_project).items():
        aggregation.add_category(category, pairs)
    return aggregation


def _build_category_summaries(
    aggregation: _RowAggregation,
    member_counts_by_category: dict[str, int],
) -> list[CostCenterAssignedBudgetSummary]:
    summaries = []
    for category in BudgetCategory:
        if category not in aggregation.touched_categories():
            continue
        totals = aggregation.totals.get(category, {})
        rendered = aggregation.category_spend(category)
        summaries.append(
            CostCenterAssignedBudgetSummary(
                budget_category=category,
                budget_duration=aggregation.category_duration.get(category),
                soft_budget=round(totals.get("soft_budget", 0.0), 2),
                max_budget=round(totals.get("max_budget", 0.0), 2),
                member_count=member_counts_by_category.get(category.value, 0),
                allocated_member_budget_total=round(totals.get("allocated", 0.0), 2),
                # Unrounded, like the project cards: rounding turned a real sub-cent spend into 0.
                current_spending=rendered.figure if rendered else None,
                spend_unavailable=rendered.unavailable if rendered else False,
                spend_partial=rendered.partial if rendered else False,
                spend_missing_amount=rendered.missing_amount if rendered else None,
                excluded_count=aggregation.excluded_counts.get(category, 0),
                unsynced_count=aggregation.unsynced_counts.get(category, 0),
            )
        )
    return summaries


def _grand_total(aggregation: _RowAggregation, removed_by_project: dict[str, list[_BudgetSpend]]) -> RenderedSpend:
    """Every accepted category's spend plus the projects' spend in removed categories still in their period.

    There is no separate API-key term: key traffic reaches the ledger carrying a budget category, so it
    is already inside the category figures. Removed categories add spend but no limit, as on the
    project page (EPMCDME-15048).
    """
    rendered = [spend for spend in aggregation.spend.values() for spend in spend]
    rendered += [render_budget_spend(result) for pairs in removed_by_project.values() for _, result in pairs]
    return combine_rendered_spend(rendered)


def _budget_limit(budgets: list[CostCenterAssignedBudgetSummary]) -> float | None:
    """The summed limit, only when every category shares one budget_duration.

    Grand totals across different reset windows are otherwise ambiguous.
    """
    durations = {b.budget_duration for b in budgets if b.budget_duration is not None}
    if not budgets or len(durations) > 1:
        return None
    return round(sum(b.max_budget for b in budgets), 2)


def _total_percent(current_spending: float | None, budget_limit: float | None) -> float | None:
    """Consumed percentage, clamped to 100 like the UI clamps its bars; None when either side is absent."""
    if current_spending is None or not budget_limit:
        return None
    return min(round(current_spending / budget_limit * 100, 2), 100.0)


async def get_cost_center_budget_summary(project_names: list[str]) -> CostCenterBudgetSummary:
    """Sum platform/cli/premium_models budget rows across the given projects.

    Takes an already-resolved project name list so sibling callers with their own project lists
    can reuse this without re-resolving names or reimplementing summation. Opens only short
    sessions of its own and holds none across the ledger read (EPMCDME-15537).

    current_spending mirrors the project page's "Spend": it is read through the same
    `project_budget_spend` pairs the project cards derive their figures from.
    """
    if not project_names:
        return CostCenterBudgetSummary(budgets=[])

    spend_by_project, removed_by_project = await project_budget_spend(project_names)
    aggregation = _aggregate(spend_by_project)

    async with get_async_session() as async_session:
        member_counts_by_category = await project_budget_assignment_repository.get_distinct_member_counts_by_category(
            async_session, project_names, budget_ids=aggregation.flat_accepted_budget_ids()
        )

    budgets = _build_category_summaries(aggregation, member_counts_by_category)
    total = _grand_total(aggregation, removed_by_project)
    budget_limit = _budget_limit(budgets)

    return CostCenterBudgetSummary(
        current_spending=total.figure,
        budget_limit=budget_limit,
        total_percent=_total_percent(total.figure, budget_limit),
        spend_unavailable=total.unavailable,
        spend_partial=total.partial,
        spend_missing_amount=total.missing_amount,
        budgets=budgets,
    )
