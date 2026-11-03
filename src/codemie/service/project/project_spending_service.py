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
from datetime import UTC, datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from codemie.clients.postgres import get_async_session
from codemie.core.models import Application
from codemie.repository.project_budget_repository import (
    ProjectAssignedBudgetSummaryRow,
    project_budget_assignment_repository,
)
from codemie.rest_api.models.project import (
    ProjectAssignedBudgetSummary,
    ProjectSpendingDetail,
    ProjectSpendingSummary,
    ProjectSpendingWidget,
    SpendingWidgetColumn,
    SpendingWidgetData,
    SpendingWidgetRow,
)
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.budget.daily_reset_reconciliation_service import daily_budget_reset_reconciliation_service
from codemie.service.spend.budget_period import budget_period_range, later_start
from codemie.service.spend.budget_period import parse_budget_reset_at as parse_reset_instant
from codemie.service.spend.coverage import SpendResult, TimeRange
from codemie.service.spend.ledger import ProjectHolder
from codemie.service.spend.rendering import RenderedSpend, combine_rendered_spend, render_budget_spend
from codemie.service.spend.spend_service import spend_service

if TYPE_CHECKING:
    from codemie.rest_api.models.project import ProjectDetailResponse, ProjectListItem
    from codemie.rest_api.security.user import User


logger = logging.getLogger(__name__)


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
        description="Maximum budget for the period (hard limit)",
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


#: An assigned budget's policy row beside the spend port's answer for it. `None` marks a budget the
#: port was never asked about, which renders exactly like a no-figure answer - to a reader the two
#: are the same fact.
_BudgetSpend = tuple[ProjectAssignedBudgetSummaryRow, Optional[SpendResult]]


def _budget_limit_of(row: ProjectAssignedBudgetSummaryRow) -> float | None:
    """The limit a percentage is taken against, straight from the budget the project is assigned."""
    return float(row.max_budget) if row.max_budget is not None else None


def aggregate_budget_window(
    rows: list[ProjectAssignedBudgetSummaryRow],
) -> tuple[float | None, datetime | None]:
    """A project's combined limit and earliest reset across every budget category it is assigned.

    A project can hold several assigned budgets, so reporting one category's limit understates what
    the percentage beside it is taken against - the figure sums every category (EPMCDME-14379).

    Returns:
        Tuple of (summed max_budget, earliest budget_reset_at), each None when nothing resolves
    """
    limits = [float(row.max_budget) for row in rows if row.max_budget is not None]
    resets = [parse_budget_reset_at(row.budget_reset_at) for row in rows]
    known_resets = [reset for reset in resets if reset is not None]
    return (sum(limits) if limits else None), (min(known_resets) if known_resets else None)


def _percent_consumed(figure: float | None, budget_limit: float | None) -> float | None:
    """The share of the limit consumed, or no percentage at all when either side is missing.

    A fabricated 0.0 here renders as "0% consumed", which is a claim about consumption that neither
    an unknown figure nor an absent limit supports.
    """
    if figure is None or not budget_limit:
        return None
    return round(figure / budget_limit * 100, 2)


def build_widget_rows(budget_spend: list[_BudgetSpend], *, is_assigned: bool = True) -> list[SpendingWidgetRow]:
    """One widget row per budget, its figure from that budget's own holder.

    There is no "Key" row any more. The traffic it reported - the CLI and own-credentials paths
    through the proxy - now reaches the ledger carrying both a project and a budget category, so it
    already sits inside the per-category rows and a separate row would show the same money twice.

    A removed category's row (`is_assigned=False`) carries no limit and so no percentage: the limit
    it had ended with its budget.
    """
    rows = []
    for row, result in budget_spend:
        rendered = render_budget_spend(result)
        budget_limit = _budget_limit_of(row) if is_assigned else None
        budget_reset_at = parse_budget_reset_at(row.budget_reset_at)
        rows.append(
            SpendingWidgetRow(
                budget_id=row.budget_id if row.budget_id else "Default",
                budget_category=row.budget_category,
                is_assigned=is_assigned,
                current_spending=rendered.figure,
                budget_reset_at=budget_reset_at,
                time_until_reset=format_time_until_reset(budget_reset_at),
                budget_limit=budget_limit,
                total=_percent_consumed(rendered.figure, budget_limit),
                spend_unavailable=rendered.unavailable,
                spend_partial=rendered.partial,
                spend_missing_amount=rendered.missing_amount,
            )
        )
    return rows


def assigned_category(row: ProjectAssignedBudgetSummaryRow, project_name: str) -> BudgetCategory | None:
    """The row's category as the enum, or None for a value outside it - logged, and the row skipped."""
    try:
        return BudgetCategory(row.budget_category)
    except ValueError:
        logger.warning(
            f"Skipping assigned budget row with invalid category "
            f"for project={project_name} budget_id={row.budget_id} category={row.budget_category}"
        )
        return None


def assigned_budget_summary(
    row: ProjectAssignedBudgetSummaryRow, category: BudgetCategory
) -> ProjectAssignedBudgetSummary:
    """A summary that starts with an absent figure: a row the port is never asked about keeps none."""
    return ProjectAssignedBudgetSummary(
        budget_id=row.budget_id,
        name=row.name,
        budget_category=category,
        soft_budget=row.soft_budget,
        max_budget=row.max_budget,
        budget_duration=row.budget_duration,
        budget_reset_at=row.budget_reset_at,
        provider_sync_status=row.provider_sync_status,
        member_count=row.member_count,
        allocated_member_budget_total=row.allocated_member_budget_total,
        current_spending=None,
    )


def fill_spend(summary: ProjectAssignedBudgetSummary, result: SpendResult | None) -> None:
    """The figure and its annotation, set together: a truncated total looks exactly like a whole
    one until the row says which it is."""
    rendered = render_budget_spend(result)
    # An absent figure is not a zero: leave it absent so nothing renders as "nothing spent".
    if rendered.figure is not None:
        summary.current_spending = rendered.figure
    summary.spend_unavailable = rendered.unavailable
    summary.spend_partial = rendered.partial
    summary.spend_missing_amount = rendered.missing_amount


def budget_period_of(
    row: ProjectAssignedBudgetSummaryRow, removed_rows: list[ProjectAssignedBudgetSummaryRow]
) -> TimeRange | None:
    """The budget's current period, starting no earlier than its category began counting this period.

    That is the budget's own creation - the project key it is enforced through is created in the same
    flow, so its counter starts then (EPMCDME-15236) - unless an earlier budget of the same category
    shares its reset instant: removing a category ends its budget but not its project key, whose counter
    runs on to the reset, so a category assigned again within the period still counts what was spent
    before it was removed (EPMCDME-15048). A manual spend reset later than that moves the start to it.
    """
    return budget_period_range(
        budget_reset_at=row.budget_reset_at,
        budget_duration=row.budget_duration,
        effective_from=later_start(_counting_since(row, removed_rows), row.budget_spend_reset_at),
    )


def _counting_since(
    row: ProjectAssignedBudgetSummaryRow, removed_rows: list[ProjectAssignedBudgetSummaryRow]
) -> datetime | None:
    """The earliest creation among `row` and the removed budgets of its category that share its reset instant."""
    reset_at = parse_reset_instant(row.budget_reset_at)
    created = [
        budget.budget_created_at
        for budget in (row, *removed_rows)
        if budget.budget_category == row.budget_category
        and budget.budget_created_at is not None
        and parse_reset_instant(budget.budget_reset_at) == reset_at
    ]
    return min(created, default=None)


def manageable_project_names(enriched_projects: list[dict[str, Any]], user: User) -> list[str]:
    return [
        proj["name"]
        for proj in enriched_projects
        if user.is_admin
        or getattr(user, "is_auditor", False)
        or proj.get("is_project_admin")
        or proj.get("project_type") == Application.ProjectType.PERSONAL
    ]


async def assigned_budget_rows(
    project_names: list[str], budget_category: str | None = None
) -> dict[str, list[ProjectAssignedBudgetSummaryRow]]:
    """The projects' assigned budget rows, read again if reconciling an overdue reset instant moved any.

    Every period on these screens is derived from the row's stored reset instant, so the rows are
    reconciled before anything is derived from them (see `refresh_overdue`).
    """
    async with get_async_session() as async_session:
        rows_by_project = await project_budget_assignment_repository.get_assigned_budget_summaries_for_projects(
            async_session, project_names, budget_category=budget_category
        )
    rows = [row for project_rows in rows_by_project.values() for row in project_rows]
    if not await daily_budget_reset_reconciliation_service.refresh_overdue(rows):
        return rows_by_project
    async with get_async_session() as async_session:
        return await project_budget_assignment_repository.get_assigned_budget_summaries_for_projects(
            async_session, project_names, budget_category=budget_category
        )


async def removed_budget_rows(project_names: list[str]) -> dict[str, list[ProjectAssignedBudgetSummaryRow]]:
    """The projects' removed budgets, the latest removal first within each category (EPMCDME-15048)."""
    async with get_async_session() as async_session:
        return await project_budget_assignment_repository.get_removed_budget_summaries_for_projects(
            async_session, project_names
        )


def _holder_and_period(
    row: ProjectAssignedBudgetSummaryRow, removed_rows: list[ProjectAssignedBudgetSummaryRow]
) -> tuple[ProjectHolder | None, TimeRange | None]:
    """What to ask the spend port about for one assigned budget, or (None, None) if it cannot be asked."""
    try:
        category = BudgetCategory(row.budget_category)
    except ValueError:
        # Reported as an absent figure rather than dropped from the project: a budget silently left
        # out of a sum shortens the total, and a short total looks exactly like a whole one.
        logger.warning(
            f"Assigned budget row has an unreadable category, so its spend is reported absent "
            f"for project={row.project_name} budget_id={row.budget_id} category={row.budget_category}"
        )
        return None, None

    period = budget_period_of(row, removed_rows)
    # No derivable period means no window to aggregate over, so there is no figure to ask for.
    if period is None:
        return None, None
    return ProjectHolder(project=row.project_name, category=category), period


async def _budget_spend(
    rows_by_project: dict[str, list[ProjectAssignedBudgetSummaryRow]],
    removed_rows_by_project: dict[str, list[ProjectAssignedBudgetSummaryRow]],
) -> dict[str, list[_BudgetSpend]]:
    """Each project's budget rows paired with the spend port's answer for each.

    Every project figure on this router derives from these pairs, so the list summary, the detail
    summary and the widget cannot disagree: three surfaces deriving one figure from three row sets
    is how they came to disagree in the first place.
    """
    holders_by_period: dict[TimeRange, list[ProjectHolder]] = {}
    pending: dict[str, list[tuple[ProjectAssignedBudgetSummaryRow, ProjectHolder | None, TimeRange | None]]] = {}

    for project_name, rows in rows_by_project.items():
        for row in rows:
            holder, period = _holder_and_period(row, removed_rows_by_project.get(project_name, []))
            if period is not None:
                holders_by_period.setdefault(period, []).append(holder)
            pending.setdefault(project_name, []).append((row, holder, period))

    # One round trip per distinct period - budgets sharing a reset schedule share a period, so the
    # whole list screen usually costs a single call.
    spend_by_period = {
        period: await spend_service.consumed_for_holders(holders, period)
        for period, holders in holders_by_period.items()
    }
    return {
        project_name: [
            (row, None if period is None else spend_by_period[period][holder]) for row, holder, period in entries
        ]
        for project_name, entries in pending.items()
    }


def _reset_is_ahead(row: ProjectAssignedBudgetSummaryRow, now: datetime) -> bool:
    reset_at = parse_reset_instant(row.budget_reset_at)
    return reset_at is not None and reset_at > now


def _may_hold_spend(result: SpendResult | None) -> bool:
    """False only for a known, whole zero: an absent or partial figure may hide spend, and leaving it
    out would shorten the project total with nothing on the total saying so."""
    rendered = render_budget_spend(result)
    return rendered.figure is None or rendered.figure > 0 or rendered.partial


async def _removed_budget_spend(
    removed_rows_by_project: dict[str, list[ProjectAssignedBudgetSummaryRow]],
    assigned_rows_by_project: dict[str, list[ProjectAssignedBudgetSummaryRow]],
) -> dict[str, list[_BudgetSpend]]:
    """Each project's spend in categories removed during a budget period that is still open.

    Removing a category ends its budget, not the period its spend belongs to: until the removed
    budget's reset instant that spend is still part of what the project consumed this period, and
    leaving it out made the project total drop the moment a category was removed (EPMCDME-15048).
    A category assigned again is answered by its active budget, whose period then reaches back to the
    removed budget's creation (see `budget_period_of`), so it is not counted here as well.

    The reset instant is checked before a period is derived from it: a removed budget's instant is
    frozen at removal, so a passed one is the normal end of its period, not the stale instant
    `budget_period_range` warns about.
    """
    now = datetime.now(UTC)
    open_rows_by_project: dict[str, list[ProjectAssignedBudgetSummaryRow]] = {}
    for project_name, rows in removed_rows_by_project.items():
        assigned_categories = {row.budget_category for row in assigned_rows_by_project.get(project_name, [])}
        latest_by_category: dict[str, ProjectAssignedBudgetSummaryRow] = {}
        for row in rows:
            latest_by_category.setdefault(row.budget_category, row)
        open_rows = [
            row
            for category, row in latest_by_category.items()
            if category not in assigned_categories and _reset_is_ahead(row, now)
        ]
        if open_rows:
            open_rows_by_project[project_name] = open_rows

    spend_by_project = await _budget_spend(open_rows_by_project, removed_rows_by_project)
    return {
        project_name: [(row, result) for row, result in pairs if _may_hold_spend(result)]
        for project_name, pairs in spend_by_project.items()
    }


async def project_budget_spend(
    project_names: list[str],
) -> tuple[dict[str, list[_BudgetSpend]], dict[str, list[_BudgetSpend]]]:
    """Each project's assigned budgets paired with their spend, and its spend in removed categories
    whose period is still open: the two pair sets every project figure is derived from."""
    rows_by_project = await assigned_budget_rows(project_names)
    removed_rows_by_project = await removed_budget_rows(project_names)
    return (
        await _budget_spend(rows_by_project, removed_rows_by_project),
        await _removed_budget_spend(removed_rows_by_project, rows_by_project),
    )


def _combined_rendered_spend(budget_spend: list[_BudgetSpend]) -> RenderedSpend:
    """A project's total: the sum over its assigned budget categories, with every qualification kept.

    There is no separate API-key term. The key pot existed because LiteLLM counted an API key apart
    from the budgets; that same traffic now reaches the ledger carrying a budget category, so it is
    already inside the per-category figures and adding a key term would count it twice.

    **Delegates to the port rather than repeating it.** This function was the original and the port's
    `combine_rendered_spend` was copied *from* it, which left two implementations of one rule - and the
    copy was then fixed for a defect this one still had: a project's per-category holders all carry the
    **same** period-scoped project portion, so summing their `missing_amount` reported one hole once per
    category. The categories' figures are disjoint and still add; the shared hole is counted once.
    Each is rendered as a budget figure, so that hole is flagged but not sized (EPMCDME-15237).
    """
    return combine_rendered_spend([render_budget_spend(result) for _, result in budget_spend])


def _project_total_spend(
    budget_spend: list[_BudgetSpend],
    removed_spend: list[_BudgetSpend],
) -> tuple[RenderedSpend, float | None, datetime | None]:
    """The project total, the limit its percentage is taken against, and the earliest reset.

    The limit and the reset are budget policy, read from the assigned budgets rather than from the
    ledger: this unit changed only where the consumed figure comes from. A removed category adds its
    spend to the total but nothing to the limit, which stays the sum of the assigned budgets.
    """
    budget_limit, budget_reset_at = aggregate_budget_window([row for row, _ in budget_spend])
    return _combined_rendered_spend(budget_spend + removed_spend), budget_limit, budget_reset_at


def project_spending_summary(
    budget_spend: list[_BudgetSpend],
    removed_spend: list[_BudgetSpend],
) -> ProjectSpendingSummary | None:
    if not budget_spend and not removed_spend:
        return None

    rendered, budget_limit, _ = _project_total_spend(budget_spend, removed_spend)
    return ProjectSpendingSummary(
        # Unrounded, and absent stays absent: the UI formats money, and rounding here turned a real
        # sub-cent spend into 0 that the unrounded budget cards and /v1/admin/users contradict.
        current_spending=rendered.figure,
        budget_limit=round(budget_limit, 2) if budget_limit is not None else None,
        total_percent=_percent_consumed(rendered.figure, budget_limit),
        spend_unavailable=rendered.unavailable,
        spend_partial=rendered.partial,
        spend_missing_amount=rendered.missing_amount,
    )


async def attach_project_spending_summaries(
    items: list[ProjectListItem],
    enriched_projects: list[dict[str, Any]],
    user: User,
) -> None:
    manageable_names = manageable_project_names(enriched_projects, user)
    if not manageable_names:
        return

    spend_by_project, removed_by_project = await project_budget_spend(manageable_names)
    for item in items:
        item.spending = project_spending_summary(
            spend_by_project.get(item.name, []), removed_by_project.get(item.name, [])
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
    rows_by_project = await assigned_budget_rows([project_name])
    removed_rows_by_project = await removed_budget_rows([project_name])

    budget_spend = (await _budget_spend(rows_by_project, removed_rows_by_project)).get(project_name, [])
    removed_spend = (await _removed_budget_spend(removed_rows_by_project, rows_by_project)).get(project_name, [])

    populate_project_spending_summary(response=response, budget_spend=budget_spend, removed_spend=removed_spend)
    widget_rows = build_widget_rows(budget_spend) + build_widget_rows(removed_spend, is_assigned=False)
    # The row cap limits the widget only. Applying it to the summary too would report a project
    # total short by whatever the dropped budgets consumed, with nothing on the row saying so.
    populate_project_spending_widget(response=response, widget_rows=widget_rows[:spending_rows_limit])


def populate_project_spending_summary(
    *,
    response: ProjectDetailResponse,
    budget_spend: list[_BudgetSpend],
    removed_spend: list[_BudgetSpend],
) -> None:
    if not budget_spend and not removed_spend:
        return

    rendered, budget_limit, budget_reset_at = _project_total_spend(budget_spend, removed_spend)
    response.spending = ProjectSpendingDetail(
        # Unrounded, and absent stays absent: the UI formats money, and rounding here turned a real
        # sub-cent spend into 0 that the unrounded budget cards and /v1/admin/users contradict.
        current_spending=rendered.figure,
        budget_reset_at=budget_reset_at,
        time_until_reset=format_time_until_reset(budget_reset_at),
        budget_limit=round(budget_limit, 2) if budget_limit is not None else None,
        total=_percent_consumed(rendered.figure, budget_limit),
        spend_unavailable=rendered.unavailable,
        spend_partial=rendered.partial,
        spend_missing_amount=rendered.missing_amount,
    )


def populate_project_spending_widget(
    *,
    response: ProjectDetailResponse,
    widget_rows: list[SpendingWidgetRow],
) -> None:
    if widget_rows:
        response.spending_widget = ProjectSpendingWidget(
            data=SpendingWidgetData(columns=_WIDGET_COLUMNS, rows=widget_rows)
        )
