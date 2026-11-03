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

from __future__ import annotations

import asyncio
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from codemie.clients.postgres import get_async_session
from codemie.repository.application_repository import application_repository
from codemie.repository.budget_repository import budget_repository
from codemie.repository.project_budget_repository import (
    project_budget_assignment_repository,
    project_member_budget_assignment_repository,
)
from codemie.repository.user_project_repository import user_project_repository
from codemie.service.budget.budget_enums import AllocationMode, BudgetCategory
from codemie.service.budget.budget_models import member_effective_max_budget
from codemie.service.budget.daily_reset_reconciliation_service import daily_budget_reset_reconciliation_service
from codemie.service.budget.project_budget_service import ProjectBudgetService
from codemie.service.spend.budget_period import budget_period_range, later_start
from codemie.service.spend.coverage import SpendResult, TimeRange
from codemie.service.spend.ledger import ProjectHolder, ProjectMemberHolder
from codemie.service.spend.rendering import render_budget_spend, spend_fields
from codemie.service.spend.spend_service import spend_service

_CATEGORY_LABELS = {
    BudgetCategory.PLATFORM.value: "Platform",
    BudgetCategory.CLI.value: "CLI",
    BudgetCategory.PREMIUM_MODELS.value: "Premium models",
}


def _category_columns(key_column_id: str, key_column_label: str) -> list[dict]:
    """Build the column descriptors: one key column followed by one column per budget category.

    Generated from BudgetCategory rather than hardcoded so a new category appears in
    both columns[] and the rows without a frontend release.
    """
    columns: list[dict] = [{"id": key_column_id, "label": key_column_label, "type": "string", "format": None}]
    for category in BudgetCategory:
        columns.append(
            {
                "id": category.value,
                "label": _CATEGORY_LABELS.get(category.value, category.value),
                "type": "number",
                "format": "currency",
            }
        )
    return columns


def _spend_cell(result: SpendResult | None) -> float | None:
    """Render one consumed figure, keeping "no figure" distinct from "measured zero".

    None means the port produced no figure — no derivable budget period, or a ledger that
    could not answer. Never substitute 0: a fabricated zero reads as "spent nothing", which is
    a different fact from "unknown", and a client that renders it cannot tell them apart.
    Decimal("0") is a real measured zero and passes through as 0.0.
    """
    return render_budget_spend(result).figure


def _limit_value(allocation: Any | None) -> float | None:
    """Return the allocated max budget, or None when no allocation is configured.

    None means "no limit configured". Never substitute 0 — 0 is a real limit and
    would render as fully consumed.
    """
    if allocation is None:
        return None
    return allocation.allocated_max_budget


def _build_row(
    key_values: dict[str, Any],
    spend_by_category: dict[str, SpendResult | None],
    allocation_by_category: dict[str, Any],
    reset_by_category: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Build one table row: key columns, then per-category spend, limit and spend annotation."""
    row: dict[str, Any] = dict(key_values)
    for category in BudgetCategory:
        name = category.value
        result = spend_by_category.get(name)
        row[name] = _spend_cell(result)
        row[f"{name}_limit"] = _limit_value(allocation_by_category.get(name))
        # Keyed per category like `<category>_limit`: one row holds every category's figure, so one
        # unsuffixed set of keys could not say which of them it qualifies.
        row.update({f"{name}_{field}": value for field, value in spend_fields(render_budget_spend(result)).items()})
        if reset_by_category is not None:
            row[f"{name}_reset_at"] = reset_by_category.get(name)
    return row


def _build_spend_rows(
    row_keys: list[str],
    key_column_id: str,
    spend_map: dict[tuple[str, str], SpendResult],
    allocation_map: dict[tuple[str, str], Any],
    reset_map: dict[tuple[str, str], str | None] | None = None,
) -> list[dict[str, Any]]:
    """Project one row per key, left-joining spend and allocation limits onto it.

    A key absent from spend_map and a key whose figure is None mean the same thing — no figure —
    and both must still yield a row, or the project or member disappears from the table.
    """
    rows = []
    for row_key in row_keys:
        spend_by_category = {c.value: spend_map.get((row_key, c.value)) for c in BudgetCategory}
        allocation_by_category = {c.value: allocation_map.get((row_key, c.value)) for c in BudgetCategory}
        reset_by_category = (
            {c.value: reset_map.get((row_key, c.value)) for c in BudgetCategory} if reset_map is not None else None
        )
        rows.append(_build_row({key_column_id: row_key}, spend_by_category, allocation_by_category, reset_by_category))
    return rows


def _governing_budget(
    key: tuple[str, str],
    allocation_map: dict[tuple[str, str], Any],
    budgets: dict[str, Any],
    budget_id_by_key: dict[tuple[str, str], str] | None,
) -> Any | None:
    """The budget whose period a (row key, category) is read over, or None when there is none."""
    if budget_id_by_key is not None and key in budget_id_by_key:
        return budgets.get(budget_id_by_key[key])
    allocation = allocation_map.get(key)
    return budgets.get(allocation.project_budget_id) if allocation is not None else None


def _budget_period(budget: Any | None) -> TimeRange | None:
    if budget is None:
        return None
    return budget_period_range(
        budget_reset_at=budget.budget_reset_at,
        budget_duration=budget.budget_duration,
        effective_from=later_start(budget.created_at, budget.spend_reset_at),
    )


async def _consumed_by_key(
    row_keys: list[str],
    allocation_map: dict[tuple[str, str], Any],
    budgets: dict[str, Any],
    holder_for: Callable[[str, BudgetCategory], ProjectMemberHolder],
    budget_id_by_key: dict[tuple[str, str], str] | None = None,
) -> dict[tuple[str, str], SpendResult]:
    """Read every (row key, category)'s consumed amount through the spend port.

        The whole `SpendResult` is kept rather than its `figure`: a truncated or dimension-incomplete
    total is a number *plus* an annotation, and dropping the annotation here would leave it
    rendering exactly like an exact figure.

    The period comes from the allocation's project category budget, which is the row that
    carries the whole schedule: child budgets copy their duration from it, and the daily reset
    reconciliation treats a member reset instant drifting from its parent's as a defect. A pair
    whose budget yields no period is left out entirely — there is no figure for it, and a range
    built inline instead would answer a rolling-window question convincingly and wrongly. The
    period starts no earlier than that budget's creation, so a member figure covers the same window
    as its project's figure: the budget card, Budget Period Spend and the member allocations agree
    (EPMCDME-15237 AC3).

    Holders are grouped by period so the port is asked once per distinct period rather than once
    per holder; budgets on the same reset schedule collapse to the same range, so a project's
    whole member table is normally one round trip.

    `budgets` are those project category budgets keyed by budget_id, read by the caller in a session
    it has already closed: neither the provider call that reconciles an overdue reset nor the ledger
    read runs while a pooled connection is held for them (EPMCDME-15537). A budget whose reset that
    reconciliation moved is re-read in a short session of its own before any period is derived.

    `budget_id_by_key` names the budget whose period a (row key, category) is read over, in place of
    its allocation's; an enforcement-off project row may have no allocation at all.
    """
    refreshed = await daily_budget_reset_reconciliation_service.refresh_overdue(budgets.values())
    if refreshed:
        async with get_async_session() as session:
            budgets.update(await budget_repository.get_by_ids(session, list(refreshed)))

    holders_by_period: dict[TimeRange, list[ProjectMemberHolder]] = {}
    key_by_holder: dict[ProjectMemberHolder, tuple[str, str]] = {}
    for row_key in row_keys:
        # Iterating the enum rather than the allocations' raw budget_category strings keeps an
        # unrecognised category out of the holder, which only accepts a BudgetCategory.
        for category in BudgetCategory:
            key = (row_key, category.value)
            period = _budget_period(_governing_budget(key, allocation_map, budgets, budget_id_by_key))
            if period is None:
                continue
            holder = holder_for(row_key, category)
            key_by_holder[holder] = key
            holders_by_period.setdefault(period, []).append(holder)

    consumed: dict[tuple[str, str], SpendResult] = {}
    for period, holders in holders_by_period.items():
        results = await spend_service.consumed_for_holders(holders, period)
        for holder in holders:
            consumed[key_by_holder[holder]] = results[holder]
    return consumed


async def _project_totals(
    budget_ids: dict[tuple[str, str], str],
    budgets: dict[str, Any],
) -> dict[tuple[str, str], SpendResult]:
    """Each (project, category)'s project total over its project budget's period, read through the port."""
    holders_by_period: dict[TimeRange, list[ProjectHolder]] = {}
    for (project_name, category_value), budget_id in budget_ids.items():
        budget = budgets.get(budget_id)
        if budget is None or category_value not in _CATEGORY_LABELS:
            continue
        period = budget_period_range(
            budget_reset_at=budget.budget_reset_at,
            budget_duration=budget.budget_duration,
            effective_from=later_start(budget.created_at, budget.spend_reset_at),
        )
        if period is not None:
            holders_by_period.setdefault(period, []).append(ProjectHolder(project_name, BudgetCategory(category_value)))

    totals: dict[tuple[str, str], SpendResult] = {}
    for period, holders in holders_by_period.items():
        results = await spend_service.consumed_for_holders(holders, period)
        for holder in holders:
            totals[(holder.project, holder.category.value)] = results[holder]
    return totals


def _apply_unenforced_limit(
    row: dict[str, Any],
    name: str,
    budget: Any,
    allocation: Any | None,
    project_total: SpendResult | None,
    with_reset: bool,
) -> None:
    """Set one category's limit, bar numerator and reset date on an enforcement-off project row.

    Without enforcement the provider stops a member only at the project budget, or at a fixed
    override capped by what is left of the project budget. A non-override row's bar measures the
    project total against the project budget, so it gets ``<category>_percent_spend``; an absent
    total stays absent. A fixed override whose cap cannot be derived keeps the override itself.
    """
    limit = member_effective_max_budget(allocation, budget, enforce_limit=False)
    total = render_budget_spend(project_total).figure
    if getattr(allocation, "allocation_mode", None) == AllocationMode.FIXED.value:
        member_spend = row[name]
        if member_spend is None or total is None:
            row[f"{name}_limit"] = limit
            return
        project_cap = member_spend + max(0.0, budget.max_budget - total)
        row[f"{name}_limit"] = min(limit, project_cap)
        if with_reset and project_cap < limit:
            row[f"{name}_reset_at"] = budget.budget_reset_at
        return
    row[f"{name}_limit"] = limit
    row[f"{name}_percent_spend"] = total
    if with_reset:
        row[f"{name}_reset_at"] = budget.budget_reset_at


class MemberSpendService:
    """Per-project member spend analytics, read from the ledger through the spend port.

    Both readers are pure reads: membership drives the rows, the port supplies the figures, and a
    row whose figure is absent keeps its place in the table with an empty spend cell.
    """

    async def get_user_project_spend(
        self,
        user_id: str,
    ) -> tuple[list[dict], list[dict]]:
        """Return (columns, rows) — one row per shared project the user belongs to."""
        return _category_columns("project_name", "Project"), await self.get_user_project_rows(user_id)

    async def get_user_project_rows(
        self,
        user_id: str,
        *,
        enforced_only: bool = False,
        with_reset: bool = False,
        effective_limits: bool = False,
    ) -> list[dict[str, Any]]:
        """Return one row per shared project the user belongs to.

        ``enforced_only`` keeps only the projects that enforce member spend limits; ``with_reset``
        adds each category's ``<category>_reset_at``, taken from the budget that governs the member.

        With ``effective_limits`` rows carry the limit the provider actually enforces, and the
        selection becomes the enforcement-on projects plus the enforcement-off projects that have an
        active project budget (``enforced_only`` is then ignored). Enforcement-on rows are unchanged;
        for an enforcement-off project, see ``_apply_unenforced_limit``.

        Personal projects are excluded. Their spend is metered against the user rather than
        against a project member budget, so a personal-project row could only ever read empty —
        which reads as "spent nothing" when the truth is "not tracked here". That spend is
        reported by /v1/analytics/budget_usage instead.
        """
        async with get_async_session() as session:
            memberships = await user_project_repository.aget_by_user_id(session, user_id)
            if not memberships:
                return []

            allocations = await project_member_budget_assignment_repository.get_active_by_user(session, user_id)
            # Only the user's own memberships are asked about: loading every application to find the
            # personal ones read ~45k rows per call (EPMCDME-15537).
            personal_projects = await application_repository.aget_personal_project_names(
                session, [m.project_name for m in memberships]
            )
            budgets = await budget_repository.get_by_ids(session, list({a.project_budget_id for a in allocations}))
            allocation_map = {(a.project_name, a.budget_category): a for a in allocations}
            reset_map = await self._reset_dates(session, allocation_map) if with_reset else None

        project_names = [m.project_name for m in memberships if m.project_name not in personal_projects]
        enforced: set[str] = set()
        if (enforced_only or effective_limits) and project_names:
            from codemie.service.settings.settings import SettingsService

            enforced = await asyncio.to_thread(
                SettingsService.get_projects_enforcing_member_spend_limits, project_names
            )
        unenforced_budget_ids: dict[tuple[str, str], str] = {}
        if effective_limits:
            unenforced = [name for name in project_names if name not in enforced]
            if unenforced:
                async with get_async_session() as session:
                    assignments = await project_budget_assignment_repository.get_active_for_projects(
                        session, unenforced
                    )
                    unenforced_budget_ids = {(a.project_name, a.budget_category): a.budget_id for a in assignments}
                    budgets.update(
                        await budget_repository.get_by_ids(session, list(set(unenforced_budget_ids.values())))
                    )
            budgeted = {project for project, _ in unenforced_budget_ids}
            project_names = [name for name in project_names if name in enforced or name in budgeted]
        elif enforced_only:
            project_names = [name for name in project_names if name in enforced]
        if not project_names:
            return []

        spend_map = await _consumed_by_key(
            project_names,
            allocation_map,
            budgets,
            lambda project_name, category: ProjectMemberHolder(
                project=project_name,
                category=category,
                user_id=user_id,
            ),
            unenforced_budget_ids,
        )
        rows = _build_spend_rows(project_names, "project_name", spend_map, allocation_map, reset_map)
        if unenforced_budget_ids:
            await self._apply_unenforced_limits(rows, unenforced_budget_ids, budgets, allocation_map, with_reset)
        return rows

    @staticmethod
    async def _apply_unenforced_limits(
        rows: list[dict[str, Any]],
        budget_ids: dict[tuple[str, str], str],
        budgets: dict[str, Any],
        allocation_map: dict[tuple[str, str], Any],
        with_reset: bool,
    ) -> None:
        """Replace the limits of enforcement-off project rows with the limit the provider enforces.

        A category without an active project budget has no limit.
        """
        totals = await _project_totals(budget_ids, budgets)
        budgeted = {project for project, _ in budget_ids}
        for row in rows:
            project = row["project_name"]
            if project not in budgeted:
                continue
            for category in BudgetCategory:
                key = (project, category.value)
                budget = budgets.get(budget_ids.get(key))
                if budget is None:
                    row[f"{category.value}_limit"] = None
                else:
                    _apply_unenforced_limit(
                        row, category.value, budget, allocation_map.get(key), totals.get(key), with_reset
                    )

    @staticmethod
    async def _reset_dates(
        session: AsyncSession,
        allocation_map: dict[tuple[str, str], Any],
    ) -> dict[tuple[str, str], str | None]:
        """Return each allocation's reset date, taken from the budget that governs it."""
        member_budget_ids = {
            key: ProjectBudgetService._effective_member_budget_id(alloc.project_budget_id, alloc)
            for key, alloc in allocation_map.items()
        }
        budgets_map = await budget_repository.get_by_ids(session, list(member_budget_ids.values()))
        reset_dates: dict[tuple[str, str], str | None] = {}
        for key, alloc in allocation_map.items():
            budget = budgets_map.get(member_budget_ids[key])
            reset_dates[key] = (budget.budget_reset_at if budget else None) or alloc.budget_reset_at
        return reset_dates

    async def get_project_member_spend(
        self,
        project_name: str,
    ) -> tuple[list[dict], list[dict]]:
        """Return (columns, rows) — one row per member of the project, keyed by user_id.

        user_id matches the id returned by GET /v1/admin/users, which is the join key
        the frontend uses against the existing member rows.
        """
        async with get_async_session() as session:
            members = await user_project_repository.aget_by_project_name(session, project_name)
            columns = _category_columns("user_id", "User")
            if not members:
                return columns, []

            allocations = await project_member_budget_assignment_repository.get_active_by_project(session, project_name)
            budgets = await budget_repository.get_by_ids(session, list({a.project_budget_id for a in allocations}))

        # The endpoint paginates, so rows need a stable order or pages skip and repeat members.
        user_ids = sorted(m.user_id for m in members)
        allocation_map = {(a.user_id, a.budget_category): a for a in allocations}
        spend_map = await _consumed_by_key(
            user_ids,
            allocation_map,
            budgets,
            lambda member_user_id, category: ProjectMemberHolder(
                project=project_name,
                category=category,
                user_id=member_user_id,
            ),
        )
        return columns, _build_spend_rows(user_ids, "user_id", spend_map, allocation_map)

    @staticmethod
    def resolve_spend_subject(target_email: str) -> tuple[Any, set[str]]:
        """Resolve the target user and their project memberships for authorization.

        Returns (user_or_None, project_names). The caller authorizes against the
        returned project set before requesting any spend.
        """
        from codemie.clients.postgres import get_session
        from codemie.service.user.user_management_service import UserManagementService

        with get_session() as session:
            db_user = UserManagementService.get_user_by_email(session, target_email)
            project_names = (
                user_project_repository.get_project_names_for_user(session, str(db_user.id)) if db_user else set()
            )
        return db_user, set(project_names)


member_spend_service = MemberSpendService()
