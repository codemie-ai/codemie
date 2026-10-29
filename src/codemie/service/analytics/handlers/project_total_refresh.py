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

"""Live refresh of project budget totals (``project_budget`` rows) under per-project locks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from codemie.configs import logger
from codemie.repository.budget_repository import budget_repository
from codemie.repository.project_spend_tracking_repository import project_spend_tracking_repository
from codemie.service.analytics.handlers.spend_snapshot_rows import _as_utc, _is_stale, _rows_to_write
from codemie.service.budget.provider_registry import get_active_provider
from codemie.service.spend_tracking.spend_models import ProjectSpendTracking


async def latest_active_total_rows(session: AsyncSession, budget_ids: dict[tuple[str, str], str]) -> list[Any]:
    """Return the newest total row per (project, category) that belongs to that category's active budget.

    ``budget_ids`` maps (project, category) to the active project budget id. A newest row of
    any other budget is left out.
    """
    project_names = list(dict.fromkeys(project for project, _ in budget_ids))
    latest_rows = await project_spend_tracking_repository.get_latest_project_budget_rows_by_category(
        session, project_names
    )
    return [row for row in latest_rows if budget_ids.get((row.project_name, row.budget_category)) == row.budget_id]


async def refresh_user_project_totals(
    session: AsyncSession,
    budget_ids: dict[tuple[str, str], str],
    cutoffs_by_project: dict[str, dict[str, datetime]],
) -> None:
    """Refresh the stale totals of the given enforcement-off projects, one locked transaction each.

    ``budget_ids`` maps (project, category) to the active project budget id. Every
    project's transaction is ended before the next starts, so no lock is held across
    projects. Never raises: a project that fails keeps its stored totals.
    """
    try:
        stale_projects = _stale_projects(budget_ids, await latest_active_total_rows(session, budget_ids))
    except Exception as e:
        logger.warning(f"Project total staleness check failed: {e}. Returning stale DB data.")
        return

    for project_name in stale_projects:
        project_budget_ids = {key: budget_id for key, budget_id in budget_ids.items() if key[0] == project_name}
        try:
            await _refresh_project_total(session, project_name, project_budget_ids, cutoffs_by_project)
        except Exception as e:
            logger.warning(f"Project total refresh for project={project_name!r} failed: {e}. Keeping stored data.")
        try:
            await session.rollback()
        except Exception as rollback_error:
            logger.warning(f"Rollback after project total refresh of {project_name!r} failed: {rollback_error}")


def _stale_projects(budget_ids: dict[tuple[str, str], str], active_rows: list[Any]) -> list[str]:
    """Return the projects whose newest active total row is missing or past the TTL."""
    newest: dict[str, datetime] = {}
    for row in active_rows:
        spend_date = _as_utc(row.spend_date)
        newest[row.project_name] = max(newest.get(row.project_name, spend_date), spend_date)
    return [name for name in dict.fromkeys(project for project, _ in budget_ids) if _is_stale(newest.get(name))]


async def _refresh_project_total(
    session: AsyncSession,
    project_name: str,
    budget_ids: dict[tuple[str, str], str],
    cutoffs_by_project: dict[str, dict[str, datetime]],
) -> None:
    """Refresh one project's totals inside the caller's transaction, committing any write.

    When the provider gives nothing that can be written, the stored active totals are
    touched instead, so the project stays fresh for the TTL rather than being fetched
    again on every load.
    """
    tracking = project_spend_tracking_repository
    if not await tracking.try_lock_project_budget_refresh(session, project_name):
        logger.info(f"Project total refresh for project={project_name!r} already running elsewhere")
        return
    active_rows = await latest_active_total_rows(session, budget_ids)
    if not _stale_projects(budget_ids, active_rows):
        return

    snapshots = await get_active_provider().collect_project_budget_spend_for_projects([project_name])
    now = datetime.now(timezone.utc)
    rows, touch_ids = await _rows_for_snapshots(session, snapshots, cutoffs_by_project, now)
    if not rows and not touch_ids:
        touch_ids = _ids_after_cutoff(active_rows, cutoffs_by_project)
        logger.info(
            f"Nothing to write for project={project_name!r} ({len(snapshots)} snapshot(s)); "
            f"keeping {len(touch_ids)} stored total(s) fresh"
        )

    if touch_ids:
        await tracking.touch_project_budget_spend_dates(session, touch_ids, now)
    if rows:
        await tracking.insert_project_budget_entries(session, rows)
    else:
        await session.commit()
    logger.info(
        f"Refreshed project total for project={project_name!r}: {len(rows)} inserted, {len(touch_ids)} unchanged"
    )


async def _rows_for_snapshots(
    session: AsyncSession,
    snapshots: list[Any],
    cutoffs_by_project: dict[str, dict[str, datetime]],
    now: datetime,
) -> tuple[list[ProjectSpendTracking], list[Any]]:
    if not snapshots:
        return [], []
    prev_rows = await project_spend_tracking_repository.get_latest_before_by_project_budget_ids(
        session, [(s.project_name, s.budget_id) for s in snapshots], now, spend_subject_type="project_budget"
    )
    budgets_map = await budget_repository.get_by_ids(session, list({s.budget_id for s in snapshots}))
    return _rows_to_write(
        snapshots,
        prev_rows,
        lambda s: (s.project_name, s.budget_id),
        budgets_map,
        cutoffs_by_project,
        now,
        "project_budget",
    )


def _ids_after_cutoff(rows: list[Any], cutoffs_by_project: dict[str, dict[str, datetime]]) -> list[Any]:
    """Return the ids of rows dated after their reset cutoff; touching any other would show pre-reset spend."""
    ids = []
    for row in rows:
        cutoff = cutoffs_by_project.get(row.project_name, {}).get(row.budget_category)
        if cutoff is None or _as_utc(row.spend_date) > cutoff:
            ids.append(row.id)
    return ids
