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

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from codemie.configs import logger
from codemie.core.models import Application
from codemie.repository.application_repository import application_repository
from codemie.repository.budget_repository import budget_repository
from codemie.repository.project_budget_repository import project_member_budget_assignment_repository
from codemie.repository.project_spend_tracking_repository import project_spend_tracking_repository
from codemie.repository.user_project_repository import user_project_repository
from codemie.service.analytics.handlers.budget_usage_service import _compute_spend_delta
from codemie.service.budget.budget_enums import BudgetCategory
from codemie.service.budget.provider_registry import get_active_provider
from codemie.service.spend_tracking.spend_collector_service import (
    InvalidSpendSnapshotError,
    LiteLLMSpendCollectorService,
)
from codemie.service.spend_tracking.spend_models import ProjectSpendTracking

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
) -> dict[str, Any]:
    """Build one table row: key columns, then per-category spend and limit."""
    row: dict[str, Any] = dict(key_values)
    for category in BudgetCategory:
        name = category.value
        row[name] = _spend_value(spend_by_category.get(name))
        row[f"{name}_limit"] = _limit_value(allocation_by_category.get(name))
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
) -> list[dict[str, Any]]:
    """Project one row per key, left-joining spend and allocation limits onto it."""
    rows = []
    for row_key in row_keys:
        spend_by_category = {c.value: spend_map.get((row_key, c.value)) for c in BudgetCategory}
        allocation_by_category = {c.value: allocation_map.get((row_key, c.value)) for c in BudgetCategory}
        rows.append(_build_row({key_column_id: row_key}, spend_by_category, allocation_by_category))
    return rows


def _snapshot_to_row(
    snapshot: Any,
    prev_row: Any | None,
    budget: Any | None,
    now: datetime,
) -> ProjectSpendTracking | None:
    """Convert one provider snapshot into a member_budget row, or None to skip it."""
    quantize = LiteLLMSpendCollectorService._quantize_spend
    fresh_spend = quantize(snapshot.spend)
    try:
        daily_spend, cumulative_spend = _compute_spend_delta(fresh_spend, prev_row, budget, now)
    except InvalidSpendSnapshotError as exc:
        logger.warning(
            f"Skipping invalid member snapshot for project={snapshot.project_name!r} "
            f"user={snapshot.user_id!r} budget_id={snapshot.budget_id!r}: {exc}"
        )
        return None

    if daily_spend == Decimal("0") and not LiteLLMSpendCollectorService.is_reset_transition(
        prev_row, budget, fresh_spend, now
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
        user_id=snapshot.user_id,
        provider_subject_id=snapshot.provider_subject_id,
        spend_subject_type="member_budget",
    )


class MemberSpendService:
    """Per-project member spend analytics with lazy global refresh from LiteLLM.

    Mirrors BudgetUsageService: read from the DB, refresh from the provider only when
    the data is stale, and fall back to whatever the DB holds if the provider fails.

    The refresh is global because the upstream call (/customer/list) is global — it
    returns every customer regardless of what was asked for. Writing all of it means
    one admin click warms the cache for every subsequent request.
    """

    async def get_user_project_spend(
        self,
        session: AsyncSession,
        user_id: str,
    ) -> tuple[list[dict], list[dict]]:
        """Return (columns, rows) — one row per shared project the user belongs to.

        Personal projects are excluded. Their spend is metered against the user
        (spend_subject_type='budget', keyed by username) rather than the project, so a
        personal-project row could only ever read 0 — which reads as "spent nothing"
        when the truth is "not tracked here". That spend is reported by
        /v1/analytics/budget_usage instead.
        """
        await self._ensure_fresh(session)

        memberships = await user_project_repository.aget_by_user_id(session, user_id)
        columns = _category_columns("project_name", "Project")
        if not memberships:
            return columns, []

        spend_map = await project_spend_tracking_repository.get_latest_member_rows_for_user(session, user_id)
        allocations = await project_member_budget_assignment_repository.get_active_by_user(session, user_id)
        applications = await application_repository.aget_all_non_deleted(session)
        personal_projects = {a.name for a in applications if a.project_type == Application.ProjectType.PERSONAL.value}

        project_names = [m.project_name for m in memberships if m.project_name not in personal_projects]
        cutoffs_by_project = {
            name: await project_spend_tracking_repository.get_project_reset_cutoffs(session, name)
            for name in project_names
        }
        spend_map = _drop_rows_before_cutoff(spend_map, lambda key: cutoffs_by_project.get(key[0], {}).get(key[1]))
        allocation_map = {(a.project_name, a.budget_category): a for a in allocations}
        return columns, _build_spend_rows(project_names, "project_name", spend_map, allocation_map)

    async def get_project_member_spend(
        self,
        session: AsyncSession,
        project_name: str,
    ) -> tuple[list[dict], list[dict]]:
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

        user_ids = [m.user_id for m in members]
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

        A refresh that finds nothing changed writes no row (the collector's sparse-table
        convention), so on a fully idle installation max(spend_date) never advances and
        every request re-checks the provider. That costs one bulk call per request while
        genuinely idle, and self-corrects the moment any member spends anything.
        """
        newest = await project_spend_tracking_repository.get_newest_member_spend_date(session)

        if not (self._needs_refresh(newest) and self._is_litellm_enabled()):
            logger.info(f"Returning cached member spend from DB (newest snapshot={newest})")
            return

        logger.info(f"Refreshing member spend from LiteLLM (newest snapshot={newest})")
        try:
            await self._refresh_all_member_spend(session)
        except Exception as e:
            logger.warning(f"Member spend refresh failed: {e}. Returning stale DB data.")
            # The rollback itself can fail (broken connection, exhausted pool). Serving
            # stale data is the requirement, so a failed rollback must not fail the request.
            try:
                await session.rollback()
            except Exception as rollback_error:
                logger.warning(f"Rollback after failed member spend refresh also failed: {rollback_error}")

    def _needs_refresh(self, newest_spend_date: datetime | None) -> bool:
        """Return True when there is no data at all, or the newest snapshot exceeds the TTL."""
        from codemie.configs.config import config

        if newest_spend_date is None:
            return True
        latest = newest_spend_date
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        age_ms = (datetime.now(timezone.utc) - latest).total_seconds() * 1000
        return age_ms > config.BUDGET_MEMBER_SPEND_STALENESS_THRESHOLD_MS

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
