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

import logging
from datetime import datetime, timezone
from typing import Any

from codemie.clients.postgres import get_async_session
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.budget.daily_reset_reconciliation_service import daily_budget_reset_reconciliation_service
from codemie.service.spend.budget_period import budget_period_range
from codemie.service.spend.coverage import SpendResult, TimeRange
from codemie.service.spend.identity_bridge import resolve_identity
from codemie.service.spend.ledger import PersonalHolder
from codemie.service.spend.rendering import FIELD_MISSING, FIELD_PARTIAL, FIELD_UNAVAILABLE, spend_row_fields
from codemie.service.spend.spend_service import spend_service

logger = logging.getLogger(__name__)

#: The keys a row carries beside its figure, in the order the mapping defines them.
_ANNOTATION_FIELDS = (FIELD_UNAVAILABLE, FIELD_PARTIAL, FIELD_MISSING)


def _get_key_spending_columns() -> list[dict]:
    """Get column definitions for budget usage tabular response."""
    return [
        {
            "id": "project_name",
            "label": "Project",
            "type": "string",
            "format": None,
            "description": "",
        },
        {
            "id": "current_spending",
            "label": "Current Spending ($)",
            "type": "number",
            "format": "currency",
            "description": "Total amount spent in current budget period",
        },
        {
            "id": "budget_reset_at",
            "label": "Budget Reset Date",
            "type": "string",
            "format": "timestamp",
            "description": "Timestamp when budget will reset",
        },
        {
            "id": "time_until_reset",
            "label": "Time Until Reset",
            "type": "string",
            "format": None,
            "description": "Time remaining until budget resets",
        },
        {
            "id": "budget_limit",
            "label": "Budget Limit ($)",
            "type": "number",
            "format": "currency",
            "description": "Maximum budget for the period (hard limit)",
        },
        {
            "id": "total",
            "label": "Total",
            "type": "number",
            "format": "percentage",
            "description": "",
        },
    ]


def _calculate_time_until_reset(budget_reset_at: str | None) -> str | None:
    """Calculate formatted time remaining until budget resets."""
    if not budget_reset_at:
        return None
    try:
        reset_time = datetime.fromisoformat(budget_reset_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if reset_time.tzinfo is None:
            reset_time = reset_time.replace(tzinfo=timezone.utc)
        delta = reset_time - now
        if delta.total_seconds() <= 0:
            return "Expired"
        total_seconds = int(delta.total_seconds())
        days = total_seconds // 86400
        remaining_seconds = total_seconds % 86400
        hours = remaining_seconds // 3600
        minutes = (remaining_seconds % 3600) // 60
        return f"{days} days {hours} hours {minutes} mins"
    except (ValueError, AttributeError) as e:
        logger.warning(f"Failed to parse budget_reset_at: {budget_reset_at}, error: {e}")
        return None


def _spend_annotation(spending: dict, figure: float | None) -> dict[str, object]:
    """The row's three companion keys - passed through when the caller carried them, else derived.

    A caller reading through the spend port merges `spend_row_fields` into `spending`, and those
    values are the truth about its figure: a truncated total is a number **and** unavailable, which
    the number alone cannot say. The LiteLLM-fed callers in the analytics router carry no annotation,
    so there the figure is the only fact available - an absent one is unavailable, and one that
    arrived unqualified is exact. Defaulting that second case to unavailable would brand every live
    figure on that screen a truncated total.
    """
    no_figure = spend_row_fields(None)
    derived = no_figure if figure is None else {**no_figure, FIELD_UNAVAILABLE: False}
    return {name: spending.get(name, derived[name]) for name in _ANNOTATION_FIELDS}


def _build_spending_row(label: str, spending: dict, is_default: bool = False) -> dict[str, Any]:
    """Build one spending table row, **without inventing any figure it was not given**.

    Two absences used to be filled in with numbers, and both read as facts on a screen:

    - a **missing spend** became `0.0`, saying the holder spent nothing when the truth is that their
      consumption is unknown. An absent figure is *unavailable*, never zero;
    - a **missing or zero limit** made the consumed percentage `0.0`, which renders as *0% consumed*
      for a holder whose consumption is simply not known. There is no percentage of an absent limit,
      so none is reported.

    The precedent for the limit half already existed three lines from the old code, in
    `_limit_value`: *"None means 'no limit configured'. Never substitute 0 - 0 is a real limit and
    would render as fully consumed."* The absent-**spend** case had simply never been reasoned the
    same way.

    **This function is shared with the analytics router on purpose.** It existed there as a
    byte-identical copy, and that duplication was itself the finding: correcting one copy leaves the
    other rendering the old zeros, on a screen nobody would think to re-check.
    """
    current = spending.get("total_spend")
    limit = spending.get("max_budget")
    reset_at = spending.get("budget_reset_at") or None

    # An enforcement-off project row measures its bar against the project total, not the member's spend.
    percent_numerator = spending.get("percent_spend", current)
    consumed_percent = None
    if percent_numerator is not None and limit:
        consumed_percent = round(percent_numerator / limit * 100, 2)

    return {
        "project_name": label,
        # Unrounded: the UI formats money, and rounding here turned a real sub-cent spend into 0.
        "current_spending": current,
        "budget_reset_at": reset_at,
        "time_until_reset": _calculate_time_until_reset(reset_at) if reset_at else None,
        "budget_limit": round(limit, 2) if limit is not None else None,
        "total": consumed_percent,
        **_spend_annotation(spending, current),
        "is_default": is_default,
    }


_CATEGORY_SUFFIX = {
    BudgetCategory.PLATFORM.value: "",
    BudgetCategory.CLI.value: " (cli)",
    BudgetCategory.PREMIUM_MODELS.value: " (premium)",
}

_CATEGORY_RANK = {cat.value: idx for idx, cat in enumerate(BudgetCategory)}


def _category_label(base_label: str, category: str) -> str:
    return f"{base_label}{_CATEGORY_SUFFIX.get(category, f' ({category})')}"


def _expand_project_rows(
    project_spend_rows: list[dict[str, Any]], default_project: str | None = None
) -> list[dict[str, Any]]:
    """Expand columnar project-spend rows into one row per project per category."""
    rows = []
    default_key = default_project.casefold() if default_project else None
    for project in sorted(project_spend_rows, key=lambda p: p["project_name"].casefold()):
        is_default = default_key is not None and project["project_name"].casefold() == default_key
        for category in BudgetCategory:
            limit = project[f"{category.value}_limit"]
            if limit is None:
                continue
            spending = {
                "total_spend": project[category.value],
                "max_budget": limit,
                "budget_reset_at": project.get(f"{category.value}_reset_at"),
                **{
                    field: project[f"{category.value}_{field}"]
                    for field in _ANNOTATION_FIELDS
                    if f"{category.value}_{field}" in project
                },
            }
            if f"{category.value}_percent_spend" in project:
                spending["percent_spend"] = project[f"{category.value}_percent_spend"]
            rows.append(
                _build_spending_row(_category_label(project["project_name"], category.value), spending, is_default)
            )
    return rows


def _build_budget_usage_rows(
    user_label: str,
    assignments: list,
    budgets_map: dict,
    spend_map: dict,
    is_default: bool = False,
) -> tuple[list[dict], list[dict]]:
    """Build tabular budget usage data from personal budget assignments.

    `spend_map` maps budget_id to the port's `SpendResult`. A budget_id it does not hold, and a
    result whose `figure` is None, both mean *no figure exists* - which `_build_spending_row`
    renders as unavailable rather than as zero.

    Each result's annotation travels with its figure, so a truncated total does not reach the screen
    looking like a whole one.
    """
    rows = []

    for assignment in sorted(assignments, key=lambda a: _CATEGORY_RANK.get(a.category, len(_CATEGORY_RANK))):
        budget = budgets_map.get(assignment.budget_id)
        if budget is None:
            continue
        spend_result = spend_map.get(assignment.budget_id)
        figure = spend_result.figure if spend_result is not None else None
        label = _category_label(user_label, assignment.category)
        spending = {
            "total_spend": float(figure) if figure is not None else None,
            "max_budget": budget.max_budget,
            "budget_reset_at": budget.budget_reset_at,
            **spend_row_fields(spend_result),
        }
        rows.append(_build_spending_row(label, spending, is_default))

    return _get_key_spending_columns(), rows


def _budget_category(category: str) -> BudgetCategory | None:
    """The assignment's category as the enum the port requires, or None if it names no category.

    `user_budget_assignments.category` is a free-form varchar with no database-level vocabulary, so
    a value outside `BudgetCategory` is reachable. It identifies no budget holder, so that row
    renders with an absent figure rather than failing the whole read.
    """
    try:
        return BudgetCategory(category)
    except ValueError:
        logger.warning(f"budget_usage: assignment category {category!r} names no budget category; no figure for it")
        return None


class BudgetUsageService:
    """Budget usage for one person, read through the spend port.

    Every figure comes from `spend_service`; this service resolves *which* holder and *which*
    period to ask about and renders the answer. It computes, fetches and caches no spend of its
    own (AD-1).
    """

    async def get_budget_usage(
        self,
        subject_user_id: str,
        subject_label: str,
        subject_email: str | None = None,
    ) -> tuple[list[dict], list[dict]]:
        """Return (columns, rows) for the budget usage tabular response.

        Owns its sessions and closes each before the provider or the ledger is called: a session
        held open across `refresh_overdue` (a LiteLLM call) and the ledger read (which checks out a
        connection of its own) kept two pooled connections per request (EPMCDME-15537).
        Rows are ordered default project, personal, then the remaining projects.
        """
        from codemie.repository.budget_repository import budget_repository
        from codemie.service.analytics.handlers.member_spend_service import member_spend_service

        default_project, default_is_personal = await self._resolve_default_project(subject_user_id, subject_email)

        try:
            project_rows = _expand_project_rows(
                await member_spend_service.get_user_project_rows(
                    subject_user_id, effective_limits=True, with_reset=True
                ),
                default_project,
            )
        except Exception as e:
            logger.warning(
                f"Failed to fetch project rows for user {subject_user_id}: {e}. Returning personal rows only."
            )
            project_rows = []

        async with get_async_session() as session:
            assignments = await budget_repository.get_user_category_assignments(session, subject_user_id)
            budgets_map = await budget_repository.get_by_ids(session, [a.budget_id for a in assignments])
            # Resolved once for the subject and passed through unchanged. `None` is meaningful: it says
            # this person's pre-rollout rows cannot be identified, which the port labels on the figure.
            # An empty set would instead claim they have no fallback identities.
            aliases = (await resolve_identity(session, subject_user_id)).aliases
        refreshed = await daily_budget_reset_reconciliation_service.refresh_overdue(budgets_map.values())
        if refreshed:
            # Re-read rather than refreshed: the rows left with their closed session (EPMCDME-15537).
            async with get_async_session() as session:
                budgets_map.update(await budget_repository.get_by_ids(session, list(refreshed)))
        spend_map = await self._consumed_per_budget(subject_user_id, assignments, budgets_map, aliases)

        columns, personal_rows = _build_budget_usage_rows(
            subject_label, assignments, budgets_map, spend_map, default_is_personal
        )
        default_rows = [row for row in project_rows if row["is_default"]]
        other_rows = [row for row in project_rows if not row["is_default"]]
        return columns, default_rows + personal_rows + other_rows

    @staticmethod
    async def _resolve_default_project(subject_user_id: str, subject_email: str | None) -> tuple[str | None, bool]:
        """Return (default project name, whether it is a personal project); (None, False) on any failure."""
        from codemie.repository.application_repository import application_repository
        from codemie.repository.user_project_repository import user_project_repository
        from codemie.rest_api.security.user import resolve_membership_default

        try:
            async with get_async_session() as session:
                memberships = await user_project_repository.aget_by_user_id(session, subject_user_id)
                default_project = resolve_membership_default(memberships, subject_email)
                if default_project is None:
                    return None, False
                personal_names = await application_repository.aget_personal_project_names(session, [default_project])
            return default_project, default_project in personal_names
        except Exception as e:
            logger.warning(f"Failed to resolve default project for user {subject_user_id}: {e}")
            return None, False

    @staticmethod
    async def _consumed_per_budget(
        subject_user_id: str,
        assignments: list,
        budgets_map: dict,
        aliases: frozenset[str] | None,
    ) -> dict[str, SpendResult]:
        """What the subject consumed per budget, keyed by budget_id.

        A budget_id absent from the result has **no figure** - never a zero - and was deliberately
        not asked about.
        """
        budget_ids: dict[PersonalHolder, str] = {}
        holders_by_period: dict[TimeRange, list[PersonalHolder]] = {}
        for assignment in assignments:
            budget = budgets_map.get(assignment.budget_id)
            category = _budget_category(assignment.category)
            if budget is None or category is None:
                continue
            # The one definition of a budget period. An inline `[now - duration, now)` is a rolling
            # window that answers a different question just as convincingly. `None` means no period
            # is derivable, and then there is no figure: this row is not queried at all.
            period = budget_period_range(
                budget_reset_at=budget.budget_reset_at,
                budget_duration=budget.budget_duration,
                pinned_reset_at=budget.pinned_reset_at,
                pinned_period_start=budget.pinned_period_start,
                effective_from=assignment.spend_reset_at,
            )
            if period is None:
                logger.warning(
                    f"budget_usage: no budget period derivable for budget_id={assignment.budget_id!r} "
                    f"(reset_at={budget.budget_reset_at!r} duration={budget.budget_duration!r}); "
                    "reporting no figure rather than guessing a window"
                )
                continue
            holder = PersonalHolder(user_id=subject_user_id, category=category, aliases=aliases)
            budget_ids[holder] = assignment.budget_id
            holders_by_period.setdefault(period, []).append(holder)

        # One round trip per distinct period, not per row: budgets on the same reset schedule
        # collapse to the same range, and all holders in one call must share one period.
        spend_map: dict[str, SpendResult] = {}
        for period, holders in holders_by_period.items():
            for holder, result in (await spend_service.consumed_for_holders(holders, period)).items():
                spend_map[budget_ids[holder]] = result
        return spend_map


budget_usage_service = BudgetUsageService()
