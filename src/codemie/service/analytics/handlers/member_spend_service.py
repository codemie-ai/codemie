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

from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from codemie.configs import logger
from codemie.repository.application_repository import application_repository
from codemie.repository.budget_repository import budget_repository
from codemie.repository.project_budget_repository import (
    project_budget_assignment_repository,
    project_member_budget_assignment_repository,
)
from codemie.repository.project_spend_tracking_repository import project_spend_tracking_repository
from codemie.repository.user_project_repository import user_project_repository
from codemie.service.analytics.handlers.project_total_refresh import (
    latest_active_total_rows,
    refresh_user_project_totals,
)
from codemie.service.analytics.handlers.spend_snapshot_rows import _is_stale, _rows_to_write, _snapshot_to_row
from codemie.service.budget.budget_enums import AllocationMode, BudgetCategory
from codemie.service.budget.budget_models import member_effective_max_budget
from codemie.service.budget.project_budget_service import ProjectBudgetService
from codemie.service.budget.provider_registry import get_active_provider
from codemie.service.spend_tracking.spend_models import ProjectSpendTracking

_CATEGORY_LABELS = {
    BudgetCategory.PLATFORM.value: "Platform",
    BudgetCategory.CLI.value: "CLI",
    BudgetCategory.PREMIUM_MODELS.value: "Premium models",
}


def _category_columns(key_column_id: str, key_column_label: str) -> list[dict[str, Any]]:
    """Build the column descriptors: one key column followed by one column per budget category.

    Generated from BudgetCategory rather than hardcoded so a new category appears in
    both columns[] and the rows without a frontend release.
    """
    columns: list[dict[str, Any]] = [{"id": key_column_id, "label": key_column_label, "type": "string", "format": None}]
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


def _spend_value(spend_row: Any | None) -> float:
    """Return the row's current-period spend, or 0 when no snapshot exists.

    Absent rows mean "no recorded spend", which must render as 0 rather than being
    omitted — omitting it would make the project or member disappear from the table.
    """
    if spend_row is None:
        return 0
    return float(spend_row.budget_period_spend)


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
    spend_by_category: dict[str, Any],
    allocation_by_category: dict[str, Any],
    reset_by_category: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """Build one table row: key columns, then per-category spend and limit."""
    row: dict[str, Any] = dict(key_values)
    for category in BudgetCategory:
        name = category.value
        row[name] = _spend_value(spend_by_category.get(name))
        row[f"{name}_limit"] = _limit_value(allocation_by_category.get(name))
        if reset_by_category is not None:
            row[f"{name}_reset_at"] = reset_by_category.get(name)
    return row


def _drop_rows_before_cutoff(
    spend_map: dict[tuple[str, str], Any],
    cutoff_for: Callable[[tuple[str, str]], datetime | None],
) -> dict[tuple[str, str], Any]:
    """Drop rows at or before the cutoff ``cutoff_for`` returns for their key; keep the rest.

    Cutoffs come from ``get_project_reset_cutoffs``; see
    ``LiteLLMSpendCollectorService.is_reset_transition`` for why a reset is recorded at all.
    """
    kept: dict[tuple[str, str], Any] = {}
    for key, row in spend_map.items():
        cutoff = cutoff_for(key)
        spend_date = row.spend_date if row.spend_date.tzinfo else row.spend_date.replace(tzinfo=timezone.utc)
        if cutoff is not None and spend_date <= cutoff:
            continue
        kept[key] = row
    return kept


def _build_spend_rows(
    row_keys: list[str],
    key_column_id: str,
    spend_map: dict[tuple[str, str], Any],
    allocation_map: dict[tuple[str, str], Any],
    reset_map: dict[tuple[str, str], str | None] | None = None,
) -> list[dict[str, Any]]:
    """Project one row per key, left-joining spend and allocation limits onto it."""
    rows = []
    for row_key in row_keys:
        spend_by_category = {c.value: spend_map.get((row_key, c.value)) for c in BudgetCategory}
        allocation_by_category = {c.value: allocation_map.get((row_key, c.value)) for c in BudgetCategory}
        reset_by_category = (
            {c.value: reset_map.get((row_key, c.value)) for c in BudgetCategory} if reset_map is not None else None
        )
        rows.append(_build_row({key_column_id: row_key}, spend_by_category, allocation_by_category, reset_by_category))
    return rows


def _newest_spend_date(spend_map: dict[tuple[str, str], Any], project_names: set[str]) -> datetime | None:
    dates = [row.spend_date for (project_name, _), row in spend_map.items() if project_name in project_names]
    return max(dates, default=None)


def _apply_unenforced_limit(
    row: dict[str, Any],
    name: str,
    budget: Any,
    allocation: Any | None,
    project_total: float,
    with_reset: bool,
) -> None:
    """Set one category's limit, bar numerator and reset date on an enforcement-off project row."""
    limit = member_effective_max_budget(allocation, budget, enforce_limit=False)
    if getattr(allocation, "allocation_mode", None) == AllocationMode.FIXED.value:
        project_cap = row[name] + max(0.0, budget.max_budget - project_total)
        row[f"{name}_limit"] = min(limit, project_cap)
        if with_reset and project_cap < limit:
            row[f"{name}_reset_at"] = budget.budget_reset_at
        return
    row[f"{name}_limit"] = limit
    row[f"{name}_percent_spend"] = project_total
    if with_reset:
        row[f"{name}_reset_at"] = budget.budget_reset_at


class MemberSpendService:
    """Per-project member spend analytics with lazy global refresh from LiteLLM.

    Mirrors BudgetUsageService: read from the DB, refresh from the provider only when
    the data is stale, and fall back to whatever the DB holds if the provider fails.

    The refresh is global because the upstream call (/customer/list) is global — it
    returns every customer regardless of what was asked for. Writing all of it means
    one admin click warms the cache for every subsequent request.
    """

    def __init__(self) -> None:
        self._last_checked_at: datetime | None = None
        self._refresh_lock = asyncio.Lock()

    async def get_user_project_spend(
        self,
        session: AsyncSession,
        user_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return (columns, rows) — one row per shared project the user belongs to."""
        return _category_columns("project_name", "Project"), await self.get_user_project_rows(session, user_id)

    async def get_user_project_rows(
        self,
        session: AsyncSession,
        user_id: str,
        *,
        enforced_only: bool = False,
        with_reset: bool = False,
        user_scoped_refresh: bool = False,
        effective_limits: bool = False,
    ) -> list[dict[str, Any]]:
        """Return one dict per shared project the user belongs to.

        With ``user_scoped_refresh`` only this user's member spend is refreshed, keyed on
        the newest of their own rows, instead of the global ``_ensure_fresh`` refresh.

        Without ``effective_limits`` each limit is the stored member allocation. With it,
        rows carry the limit the provider actually enforces, and the selection becomes the
        enforcement-on projects plus the enforcement-off projects that have an active project
        budget (``enforced_only`` is then ignored). Enforcement-on rows are unchanged. For an
        enforcement-off project, see ``_apply_unenforced_limits``.

        Personal projects are always excluded. Their spend is metered against the user
        (spend_subject_type='budget', keyed by username) rather than the project, so a
        personal-project row could only ever read 0 — which reads as "spent nothing"
        when the truth is "not tracked here". That spend is reported by
        /v1/analytics/budget_usage instead.
        """
        if not user_scoped_refresh:
            await self._ensure_fresh(session)

        memberships = await user_project_repository.aget_by_user_id(session, user_id)
        if not memberships:
            return []

        member_project_names = [m.project_name for m in memberships]
        personal_projects = await application_repository.aget_personal_project_names(session, member_project_names)
        project_names = [name for name in member_project_names if name not in personal_projects]

        enforced: set[str] = set()
        if (enforced_only or effective_limits) and project_names:
            from codemie.service.settings.settings import SettingsService

            enforced = await asyncio.to_thread(
                SettingsService.get_projects_enforcing_member_spend_limits, project_names
            )
        unenforced_budget_ids: dict[tuple[str, str], str] = {}
        if effective_limits:
            unenforced_budget_ids = await self._active_project_budget_ids(
                session, [name for name in project_names if name not in enforced]
            )
            budgeted = {project for project, _ in unenforced_budget_ids}
            project_names = [name for name in project_names if name in enforced or name in budgeted]
        elif enforced_only:
            project_names = [name for name in project_names if name in enforced]
        if not project_names:
            return []

        selected = set(project_names)
        cutoffs_by_project = await project_spend_tracking_repository.get_project_reset_cutoffs_for_projects(
            session, project_names
        )
        if user_scoped_refresh and unenforced_budget_ids and self._is_litellm_enabled():
            await refresh_user_project_totals(session, unenforced_budget_ids, cutoffs_by_project)
            await session.rollback()
        allocations = await self._selected_allocations(session, user_id, selected)
        spend_map = await project_spend_tracking_repository.get_latest_member_rows_for_user(session, user_id)

        if (
            user_scoped_refresh
            and allocations
            and _is_stale(_newest_spend_date(spend_map, selected))
            and self._is_litellm_enabled()
        ):
            await self._refresh_user_member_spend(session, user_id, allocations, cutoffs_by_project)
            # The refresh commits, which expires every ORM object loaded so far; reload them.
            await session.rollback()
            allocations = await self._selected_allocations(session, user_id, selected)
            spend_map = await project_spend_tracking_repository.get_latest_member_rows_for_user(session, user_id)

        spend_map = _drop_rows_before_cutoff(spend_map, lambda key: cutoffs_by_project.get(key[0], {}).get(key[1]))
        allocation_map = {(a.project_name, a.budget_category): a for a in allocations}

        reset_map = await self._reset_dates(session, allocation_map) if with_reset else None
        rows = _build_spend_rows(project_names, "project_name", spend_map, allocation_map, reset_map)
        if unenforced_budget_ids:
            await self._apply_unenforced_limits(session, rows, unenforced_budget_ids, allocation_map, with_reset)
        return rows

    @staticmethod
    async def _active_project_budget_ids(session: AsyncSession, project_names: list[str]) -> dict[tuple[str, str], str]:
        """Return the active project budget id per (project, category).

        Plain ids are returned so a later commit, which expires loaded ORM objects, cannot
        invalidate them.
        """
        assignments = await project_budget_assignment_repository.get_active_for_projects(session, project_names)
        return {(a.project_name, a.budget_category): a.budget_id for a in assignments}

    @staticmethod
    async def _apply_unenforced_limits(
        session: AsyncSession,
        rows: list[dict[str, Any]],
        budget_ids: dict[tuple[str, str], str],
        allocation_map: dict[tuple[str, str], Any],
        with_reset: bool,
    ) -> None:
        """Replace the limits of enforcement-off project rows with the limit the provider enforces.

        Without enforcement the provider stops a member only at the project budget, or at a
        fixed override capped by what is left of the project budget. A category without an
        active project budget has no limit. Non-override categories also get
        ``{category}_percent_spend``, the project total that the project budget is measured
        against. A project total row of any other budget than the active one counts as 0.
        The reset date is the project budget's, unless a fixed override is itself the limit.
        """
        budgets_map = await budget_repository.get_by_ids(session, list(set(budget_ids.values())))
        totals = {
            (r.project_name, r.budget_category): float(r.budget_period_spend)
            for r in await latest_active_total_rows(session, budget_ids)
        }
        budgeted = {project for project, _ in budget_ids}
        for row in rows:
            project = row["project_name"]
            if project not in budgeted:
                continue
            for category in BudgetCategory:
                key = (project, category.value)
                budget = budgets_map.get(budget_ids.get(key))
                if budget is None:
                    row[f"{category.value}_limit"] = None
                else:
                    _apply_unenforced_limit(
                        row, category.value, budget, allocation_map.get(key), totals.get(key, 0.0), with_reset
                    )

    @staticmethod
    async def _selected_allocations(session: AsyncSession, user_id: str, project_names: set[str]) -> list[Any]:
        allocations = await project_member_budget_assignment_repository.get_active_by_user(session, user_id)
        return [a for a in allocations if a.project_name in project_names]

    async def _refresh_user_member_spend(
        self,
        session: AsyncSession,
        user_id: str,
        allocations: list[Any],
        cutoffs_by_project: dict[str, dict[str, datetime]],
    ) -> None:
        """Refresh one user's member spend from the provider, like personal spend refreshes.

        Changed spend inserts a row; unchanged spend moves the latest row's spend_date to
        now so the freshness check holds for the TTL. Never raises: on failure the stored
        rows are left as they are.
        """
        try:
            if not await project_spend_tracking_repository.try_lock_member_refresh(session, user_id):
                logger.info(f"Member spend refresh for user={user_id!r} already running elsewhere; serving DB data")
                return

            project_names = {a.project_name for a in allocations}
            latest = await project_spend_tracking_repository.get_latest_member_rows_for_user(session, user_id)
            if not _is_stale(_newest_spend_date(latest, project_names)):
                return

            snapshots = await get_active_provider().collect_member_budget_spend_for_allocations(allocations)
            if not snapshots:
                logger.info(f"Provider returned no member spend for user={user_id!r}")
                return

            now = datetime.now(timezone.utc)
            prev_rows = await project_spend_tracking_repository.get_latest_before_by_member_budget_ids(
                session, [(s.project_name, s.budget_id, s.user_id) for s in snapshots], now
            )
            budgets_map = await budget_repository.get_by_ids(session, list({s.budget_id for s in snapshots}))
            rows, touch_ids = _rows_to_write(
                snapshots,
                prev_rows,
                lambda s: (s.project_name, s.budget_id, s.user_id),
                budgets_map,
                cutoffs_by_project,
                now,
                "member_budget",
            )

            if rows:
                await project_spend_tracking_repository.insert_member_budget_entries(session, rows)
            if touch_ids:
                await project_spend_tracking_repository.touch_member_spend_dates(session, touch_ids, now)
            logger.info(
                f"Refreshed member spend for user={user_id!r}: {len(rows)} inserted, {len(touch_ids)} unchanged"
            )
        except Exception as e:
            logger.warning(f"Member spend refresh for user={user_id!r} failed: {e}. Returning stale DB data.")
            try:
                await session.rollback()
            except Exception as rollback_error:
                logger.warning(f"Rollback after failed member spend refresh also failed: {rollback_error}")

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
        session: AsyncSession,
        project_name: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return (columns, rows) — one row per member of the project, keyed by user_id.

        user_id matches the id returned by GET /v1/admin/users, which is the join key
        the frontend uses against the existing member rows.
        """
        await self._ensure_fresh(session)

        members = await user_project_repository.aget_by_project_name(session, project_name)
        columns = _category_columns("user_id", "User")
        if not members:
            return columns, []

        spend_map = await project_spend_tracking_repository.get_latest_member_rows_for_project(session, project_name)
        cutoffs = await project_spend_tracking_repository.get_project_reset_cutoffs(session, project_name)
        spend_map = _drop_rows_before_cutoff(spend_map, lambda key: cutoffs.get(key[1]))
        allocations = await project_member_budget_assignment_repository.get_active_by_project(session, project_name)

        # The endpoint paginates, so rows need a stable order or pages skip and repeat members.
        user_ids = sorted(m.user_id for m in members)
        allocation_map = {(a.user_id, a.budget_category): a for a in allocations}
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

    async def _ensure_fresh(self, session: AsyncSession) -> None:
        """Refresh all member spend from LiteLLM if the stored data is stale.

        Never raises: a provider failure degrades to serving stale DB rows.

        Must be called BEFORE any other read on this session. insert_member_budget_entries
        commits internally, and with expire_on_commit=True that expires every ORM object
        already loaded in the identity map. Callers therefore refresh first, then read.

        Freshness is the per-process ``_last_checked_at`` marker alone. The newest
        member row cannot serve as the marker: the personal page refreshes one user at a
        time, and its fresh rows would make everyone else's look fresh. Accepted cost: each
        process (and each restart) pays one full provider refresh on its first admin-page
        request per TTL, and refreshes in different processes can overlap.
        """
        if not (_is_stale(self._last_checked_at) and self._is_litellm_enabled()):
            logger.info(f"Returning cached member spend from DB (last checked={self._last_checked_at})")
            return

        async with self._refresh_lock:
            if not _is_stale(self._last_checked_at):
                logger.info(f"Returning cached member spend from DB after lock (last checked={self._last_checked_at})")
                return

            logger.info(f"Refreshing member spend from LiteLLM (last checked={self._last_checked_at})")
            try:
                await self._refresh_all_member_spend(session)
            except Exception as e:
                logger.warning(f"Member spend refresh failed: {e}. Returning stale DB data.")
                try:
                    await session.rollback()
                except Exception as rollback_error:
                    logger.warning(f"Rollback after failed member spend refresh also failed: {rollback_error}")
            finally:
                self._last_checked_at = datetime.now(timezone.utc)

    @staticmethod
    def _is_litellm_enabled() -> bool:
        try:
            from codemie.enterprise.litellm.dependencies import is_litellm_enabled

            return is_litellm_enabled()
        except ImportError:
            return False

    async def _refresh_all_member_spend(self, session: AsyncSession) -> None:
        """Fetch every member snapshot from LiteLLM and persist the ones whose spend moved.

        Reuses the budget provider's bulk collector and the spend collector's delta helpers
        so the lazy path and the nightly batch job write identical rows.
        """
        await project_spend_tracking_repository.lock_all_member_refresh(session)
        snapshots = await get_active_provider().collect_member_budget_spend()
        if not snapshots:
            logger.info("Provider returned no member spend snapshots")
            return

        now = datetime.now(timezone.utc)
        triples = [(s.project_name, s.budget_id, s.user_id) for s in snapshots]
        budgets_map = await budget_repository.get_all_keyed_by_id(session)
        prev_rows = await project_spend_tracking_repository.get_latest_before_by_member_budget_ids(
            session, triples, now
        )

        rows: list[ProjectSpendTracking] = []
        for snapshot in snapshots:
            prev_row = prev_rows.get((snapshot.project_name, snapshot.budget_id, snapshot.user_id))
            row = _snapshot_to_row(snapshot, prev_row, budgets_map.get(snapshot.budget_id), now)
            if row is not None:
                rows.append(row)

        skipped = len(snapshots) - len(rows)
        if not rows:
            logger.info(f"No member spend changed since the last snapshot ({skipped} unchanged)")
            return

        await project_spend_tracking_repository.insert_member_budget_entries(session, rows)
        logger.info(f"Persisted {len(rows)} member spend rows from LiteLLM ({skipped} unchanged)")


member_spend_service = MemberSpendService()
