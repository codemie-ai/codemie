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

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from codemie.clients.postgres import get_async_session
from codemie.core.models import Application
from codemie.repository.budget_repository import budget_repository
from codemie.repository.project_budget_repository import project_budget_assignment_repository
from codemie.repository.project_spend_tracking_repository import ProjectSpendTrackingRepository
from codemie.rest_api.models.project import (
    ProjectSpendingDetail,
    ProjectSpendingSummary,
    ProjectSpendingWidget,
    SpendingWidgetColumn,
    SpendingWidgetData,
    SpendingWidgetRow,
)

if TYPE_CHECKING:
    from codemie.repository.project_budget_repository import ProjectAssignedBudgetSummaryRow
    from codemie.rest_api.models.project import ProjectDetailResponse, ProjectListItem
    from codemie.rest_api.security.user import User
    from codemie.service.budget.budget_models import Budget
    from codemie.service.spend_tracking.spend_models import ProjectSpendTracking


spend_repo = ProjectSpendTrackingRepository()


_WIDGET_COLUMNS = [
    SpendingWidgetColumn(id="budget_id", label="Budget", type="string", format=None),
    SpendingWidgetColumn(
        id="current_spending",
        label="Budget Period Spend ($)",
        type="number",
        format="currency",
        description="Total amount spent in current budget period",
    ),
    SpendingWidgetColumn(
        id="budget_reset_at",
        label="Budget Reset Date",
        type="string",
        format="timestamp",
        description="Timestamp when budget will reset",
    ),
    SpendingWidgetColumn(id="time_until_reset", label="Time Until Reset", type="string"),
    SpendingWidgetColumn(
        id="budget_limit",
        label="Budget Limit ($)",
        type="number",
        format="currency",
        description="Soft budget limit (warning threshold)",
    ),
    SpendingWidgetColumn(id="total", label="Total", type="number", format="percentage"),
]


def format_time_until_reset(budget_reset_at: datetime | None) -> str | None:
    """Return a human-readable string for time remaining until the next budget reset."""
    if budget_reset_at is None:
        return None
    now = datetime.now(timezone.utc)
    if budget_reset_at.tzinfo is None:
        budget_reset_at = budget_reset_at.replace(tzinfo=timezone.utc)
    delta = budget_reset_at - now
    if delta.total_seconds() <= 0:
        return "0 days"
    days = delta.days
    hours = delta.seconds // 3600
    if days > 0:
        return f"{days} day{'s' if days != 1 else ''}"
    return f"{hours} hour{'s' if hours != 1 else ''}"


def parse_budget_reset_at(value: str | None) -> datetime | None:
    """Parse an ISO 8601 budget_reset_at string from the budgets table to datetime."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_budget_period_open(budget: Budget | None, now: datetime) -> bool:
    """Return True when ``budget`` has a ``budget_reset_at`` later than ``now``.

    Active budgets are refreshed by the daily reset reconciliation, so the answer can be off by
    up to a day around a reset. Removed budgets keep the value they had when they were removed.
    """
    if budget is None:
        return False
    reset_at = parse_budget_reset_at(budget.budget_reset_at)
    if reset_at is None:
        return False
    if reset_at.tzinfo is None:
        reset_at = reset_at.replace(tzinfo=timezone.utc)
    return reset_at > now


def budget_info(budgets_by_id: dict[str, Budget], budget_id: str | None) -> tuple[float | None, datetime | None]:
    """Return (max_budget, budget_reset_at) for a given budget_id, or (None, None)."""
    if budget_id is None:
        return None, None
    budget = budgets_by_id.get(budget_id)
    if budget is None:
        return None, None
    return budget.max_budget, parse_budget_reset_at(budget.budget_reset_at)


def select_category_rows(
    budget_rows: list[ProjectSpendTracking],
    assigned_budgets: list[ProjectAssignedBudgetSummaryRow],
    budgets_by_id: dict[str, Budget],
    now: datetime,
) -> list[tuple[ProjectSpendTracking, ProjectAssignedBudgetSummaryRow | None]]:
    """Pick the latest project_budget row per category that counts toward the current budget period.

    A category with an active budget keeps its row when the row belongs to that budget or to an
    earlier budget whose period is still open. A category without one keeps its row only while
    the row's budget period is open and its spend is above zero.

    Returns:
        (row, active assigned budget or None) pairs
    """
    assigned_by_category = {assigned.budget_category: assigned for assigned in assigned_budgets}
    selected: list[tuple[ProjectSpendTracking, ProjectAssignedBudgetSummaryRow | None]] = []
    for row in budget_rows:
        assigned = assigned_by_category.get(row.budget_category)
        period_open = is_budget_period_open(budgets_by_id.get(row.budget_id), now)
        if assigned is not None:
            if row.budget_id == assigned.budget_id or period_open:
                selected.append((row, assigned))
        elif period_open and float(row.budget_period_spend) > 0:
            selected.append((row, None))
    return selected


def aggregate_budget_window(
    assigned_budgets: list[ProjectAssignedBudgetSummaryRow],
    rows: list[ProjectSpendTracking | None],
    budgets_by_id: dict[str, Budget],
) -> tuple[float | None, datetime | None]:
    """Return a project's combined max_budget and earliest reset across its categories.

    Sums the assigned budgets, covering categories that carry no spend snapshot. Projects
    with no assignments fall back to the budgets behind ``rows``, skipping soft-deleted ones.

    Returns:
        Tuple of (summed max_budget, earliest budget_reset_at), each None when nothing resolves
    """
    if assigned_budgets:
        total = sum(float(row.max_budget) for row in assigned_budgets)
        resets = [parse_budget_reset_at(row.budget_reset_at) for row in assigned_budgets]
    else:
        limits: list[float] = []
        resets = []
        for budget_id in {row.budget_id for row in rows if row is not None and row.budget_id}:
            budget = budgets_by_id.get(budget_id)
            if budget is None or budget.deleted_at is not None:
                continue
            limits.append(float(budget.max_budget))
            resets.append(parse_budget_reset_at(budget.budget_reset_at))
        total = sum(limits) if limits else None

    known_resets = [reset for reset in resets if reset is not None]
    return total, (min(known_resets) if known_resets else None)


def build_widget_rows(
    budget_rows: list[ProjectSpendTracking],
    budgets_by_id: dict[str, Budget] | None = None,
) -> list[SpendingWidgetRow]:
    """Build spending widget rows from budget-based tracking rows."""
    if budgets_by_id is None:
        budgets_by_id = {}
    rows = []
    for row in budget_rows:
        budget_limit, budget_reset_at = budget_info(budgets_by_id, row.budget_id)
        current = float(row.budget_period_spend)
        total_pct = round((current / budget_limit * 100) if budget_limit else 0.0, 2)
        rows.append(
            SpendingWidgetRow(
                budget_id=row.budget_id if row.budget_id else "Default",
                current_spending=current,
                budget_reset_at=budget_reset_at,
                time_until_reset=format_time_until_reset(budget_reset_at),
                budget_limit=budget_limit,
                total=total_pct,
            )
        )
    return rows


def build_category_widget_rows(
    selected: list[tuple[ProjectSpendTracking, ProjectAssignedBudgetSummaryRow | None]],
    budgets_by_id: dict[str, Budget],
) -> list[SpendingWidgetRow]:
    """Build widget rows for project categories; rows without an active budget carry no limit."""
    rows = []
    for spend_row, assigned in selected:
        current = float(spend_row.budget_period_spend)
        if assigned is not None:
            budget_id = assigned.budget_id
            budget_limit = float(assigned.max_budget)
            budget_reset_at = parse_budget_reset_at(assigned.budget_reset_at)
            total_pct = round((current / budget_limit * 100) if budget_limit else 0.0, 2)
        else:
            budget_id = spend_row.budget_id
            budget_limit = None
            _, budget_reset_at = budget_info(budgets_by_id, spend_row.budget_id)
            total_pct = 0.0
        rows.append(
            SpendingWidgetRow(
                budget_id=budget_id,
                budget_category=spend_row.budget_category,
                is_assigned=assigned is not None,
                current_spending=current,
                budget_reset_at=budget_reset_at,
                time_until_reset=format_time_until_reset(budget_reset_at),
                budget_limit=budget_limit,
                total=total_pct,
            )
        )
    return rows


def key_row_to_widget(row: ProjectSpendTracking, budgets_by_id: dict[str, Budget] | None = None) -> SpendingWidgetRow:
    """Build a spending widget row from a key-based tracking row."""
    if budgets_by_id is None:
        budgets_by_id = {}
    budget_limit, budget_reset_at = budget_info(budgets_by_id, row.budget_id)
    current = float(row.budget_period_spend)
    total_pct = round((current / budget_limit * 100) if budget_limit else 0.0, 2)
    return SpendingWidgetRow(
        budget_id="Key",
        current_spending=current,
        budget_reset_at=budget_reset_at,
        time_until_reset=format_time_until_reset(budget_reset_at),
        budget_limit=budget_limit,
        total=total_pct,
    )


def manageable_project_names(enriched_projects: list[dict[str, Any]], user: User) -> list[str]:
    return [
        proj["name"]
        for proj in enriched_projects
        if user.is_admin
        or getattr(user, "is_auditor", False)
        or proj.get("is_project_admin")
        or proj.get("project_type") == Application.ProjectType.PERSONAL
    ]


def latest_key_rows_by_project(key_rows: list[object]) -> dict[str, object]:
    latest_key_by_project: dict[str, object] = {}
    for row in key_rows:
        existing = latest_key_by_project.get(row.project_name)
        if existing is None or row.spend_date > existing.spend_date:
            latest_key_by_project[row.project_name] = row
    return latest_key_by_project


def budget_rows_grouped_by_project(budget_rows: list[object]) -> dict[str, list[object]]:
    latest_budget_by_project_and_id: dict[tuple[str, str | None], object] = {}
    for row in budget_rows:
        key = (row.project_name, row.budget_id)
        existing = latest_budget_by_project_and_id.get(key)
        if existing is None or row.spend_date > existing.spend_date:
            latest_budget_by_project_and_id[key] = row

    budget_rows_by_project: dict[str, list[object]] = {}
    for row in latest_budget_by_project_and_id.values():
        budget_rows_by_project.setdefault(row.project_name, []).append(row)
    return budget_rows_by_project


def project_spending_summary(
    key_row: object | None,
    budget_rows: list[object],
    budgets_map: dict[str, Budget],
    assigned_budgets: list[ProjectAssignedBudgetSummaryRow] | None = None,
) -> ProjectSpendingSummary | None:
    if key_row is None and not budget_rows:
        return None

    current = (float(key_row.budget_period_spend) if key_row is not None else 0.0) + sum(
        float(row.budget_period_spend) for row in budget_rows
    )
    budget_limit, _ = aggregate_budget_window(assigned_budgets or [], [key_row, *budget_rows], budgets_map)
    total_pct = (current / budget_limit * 100) if budget_limit else 0.0

    return ProjectSpendingSummary(
        current_spending=round(current, 2),
        budget_limit=round(budget_limit, 2) if budget_limit is not None else None,
        total_percent=round(total_pct, 2),
    )


async def attach_project_spending_summaries(
    items: list[ProjectListItem],
    enriched_projects: list[dict[str, Any]],
    user: User,
) -> None:
    manageable_names = manageable_project_names(enriched_projects, user)
    if not manageable_names:
        return

    async with get_async_session() as async_session:
        key_rows = await spend_repo.get_latest_spending_by_project(
            async_session, manageable_names, spend_subject_type="key"
        )
        budget_rows = await spend_repo.get_latest_spending_by_project(
            async_session, manageable_names, spend_subject_type="budget"
        )
        project_budget_rows = await spend_repo.get_latest_project_budget_rows_by_category(
            async_session, manageable_names
        )
        budgets_map = await budget_repository.get_all_keyed_by_id(async_session)
        assigned_by_project = await project_budget_assignment_repository.get_assigned_budget_summaries_for_projects(
            async_session, manageable_names
        )

    now = datetime.now(timezone.utc)
    latest_key_by_project = latest_key_rows_by_project(key_rows)
    budget_rows_by_project = budget_rows_grouped_by_project(budget_rows)
    project_budget_rows_by_project: dict[str, list[ProjectSpendTracking]] = {}
    for row in project_budget_rows:
        project_budget_rows_by_project.setdefault(row.project_name, []).append(row)
    for item in items:
        assigned_budgets = assigned_by_project.get(item.name, [])
        project_rows = project_budget_rows_by_project.get(item.name)
        rows = (
            [row for row, _ in select_category_rows(project_rows, assigned_budgets, budgets_map, now)]
            if project_rows
            else budget_rows_by_project.get(item.name, [])
        )
        item.spending = project_spending_summary(
            key_row=latest_key_by_project.get(item.name),
            budget_rows=rows,
            budgets_map=budgets_map,
            assigned_budgets=assigned_budgets,
        )


def can_see_project_spending(user: User, project_detail: dict[str, Any]) -> bool:
    is_personal = project_detail.get("project_type") == Application.ProjectType.PERSONAL
    is_project_admin = bool(project_detail.get("is_project_admin"))
    return user.is_admin or getattr(user, "is_auditor", False) or is_project_admin or is_personal


async def attach_project_detail_spending(
    *,
    response: ProjectDetailResponse,
    project_name: str,
    spending_rows_limit: int,
) -> None:
    async with get_async_session() as async_session:
        key_row = await spend_repo.get_latest_key_spending_for_project(async_session, project_name)
        subject_type = "project_budget"
        budget_rows = await spend_repo.get_latest_project_budget_rows_by_category(async_session, [project_name])
        if not budget_rows:
            subject_type = "budget"
            budget_rows = await spend_repo.get_latest_budget_rows_for_project(
                async_session, project_name, rows_limit=spending_rows_limit
            )
        if not budget_rows and key_row is not None:
            subject_type = "key"
        lifetime_spend = await spend_repo.get_lifetime_spend(
            async_session, project_name, spend_subject_type=subject_type
        )
        budgets_map = await budget_repository.get_all_keyed_by_id(async_session)
        assigned_by_project = await project_budget_assignment_repository.get_assigned_budget_summaries_for_projects(
            async_session, [project_name]
        )

    assigned_budgets = assigned_by_project.get(project_name, [])
    if subject_type == "project_budget":
        selected = select_category_rows(budget_rows, assigned_budgets, budgets_map, datetime.now(timezone.utc))
        budget_rows = [row for row, _ in selected]
        widget_rows = build_category_widget_rows(selected, budgets_map)
    else:
        widget_rows = build_widget_rows(budget_rows, budgets_by_id=budgets_map)
    if budget_rows:
        key_row = None

    populate_project_spending_summary(
        response=response,
        key_row=key_row,
        budget_rows=budget_rows,
        budgets_map=budgets_map,
        lifetime_spend=lifetime_spend,
        assigned_budgets=assigned_budgets,
    )
    populate_project_spending_widget(
        response=response,
        widget_rows=widget_rows,
        key_row=key_row,
        budgets_map=budgets_map,
    )


def populate_project_spending_summary(
    *,
    response: ProjectDetailResponse,
    key_row: ProjectSpendTracking | None,
    budget_rows: list[ProjectSpendTracking],
    budgets_map: dict[str, Budget],
    lifetime_spend: float,
    assigned_budgets: list[ProjectAssignedBudgetSummaryRow] | None = None,
) -> None:
    if key_row is None and not budget_rows:
        return

    current = (float(key_row.budget_period_spend) if key_row is not None else 0.0) + sum(
        float(row.budget_period_spend) for row in budget_rows
    )
    budget_limit, budget_reset_at = aggregate_budget_window(
        assigned_budgets or [], [key_row, *budget_rows], budgets_map
    )
    total_pct = (current / budget_limit * 100) if budget_limit else 0.0
    response.spending = ProjectSpendingDetail(
        current_spending=round(current, 2),
        cumulative_spend=round(lifetime_spend, 2),
        budget_reset_at=budget_reset_at,
        time_until_reset=format_time_until_reset(budget_reset_at),
        budget_limit=round(budget_limit, 2) if budget_limit is not None else None,
        total=round(total_pct, 2),
    )


def populate_project_spending_widget(
    *,
    response: ProjectDetailResponse,
    widget_rows: list[SpendingWidgetRow],
    key_row: ProjectSpendTracking | None,
    budgets_map: dict[str, Budget],
) -> None:
    if key_row is not None:
        widget_rows = [*widget_rows, key_row_to_widget(key_row, budgets_by_id=budgets_map)]
    if widget_rows:
        response.spending_widget = ProjectSpendingWidget(
            data=SpendingWidgetData(columns=_WIDGET_COLUMNS, rows=widget_rows)
        )
