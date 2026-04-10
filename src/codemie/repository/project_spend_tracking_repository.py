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

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from codemie.service.spend_tracking.spend_models import ProjectSpendTracking


class ProjectSpendTrackingRepository:
    """Async repository for project_spend_tracking table."""

    async def get_latest_before_by_key_hashes(
        self,
        session: AsyncSession,
        key_hashes: list[str],
        before_spend_date: datetime,
    ) -> dict[str, ProjectSpendTracking]:
        """Return the most recent key-based row per key_hash before ``before_spend_date``.

        Used by the spend collector to retrieve the previous snapshot baseline for
        delta calculation. Missing keys are absent from the result (bootstrap case).

        Args:
            session: Async database session
            key_hashes: List of key hashes to look up
            before_spend_date: Upper exclusive bound for the snapshot timestamp

        Returns:
            Dict mapping key_hash to the most recent ProjectSpendTracking row
        """
        if not key_hashes:
            return {}

        # Subquery: max spend_date per key_hash before the target snapshot (key rows only)
        latest_dates_subq = (
            select(
                ProjectSpendTracking.key_hash,
                func.max(ProjectSpendTracking.spend_date).label("max_spend_date"),
            )
            .where(ProjectSpendTracking.key_hash.in_(key_hashes))
            .where(ProjectSpendTracking.spend_date < before_spend_date)
            .where(ProjectSpendTracking.spend_subject_type == "key")
            .group_by(ProjectSpendTracking.key_hash)
            .subquery()
        )

        # Join to get full rows for those latest dates
        stmt = select(ProjectSpendTracking).join(
            latest_dates_subq,
            (ProjectSpendTracking.key_hash == latest_dates_subq.c.key_hash)
            & (ProjectSpendTracking.spend_date == latest_dates_subq.c.max_spend_date),
        )

        result = await session.execute(stmt)
        rows = result.scalars().all()
        return {row.key_hash: row for row in rows}

    async def get_latest_before_by_project_budget_ids(
        self,
        session: AsyncSession,
        project_budget_pairs: list[tuple[str, str]],
        before_spend_date: datetime,
    ) -> dict[tuple[str, str], ProjectSpendTracking]:
        """Return the most recent budget-based row per (project_name, budget_id) before ``before_spend_date``.

        Used by the budget spend collector to retrieve the previous snapshot baseline for
        delta calculation. Missing pairs are absent from the result (bootstrap case).

        Args:
            session: Async database session
            project_budget_pairs: List of (project_name, budget_id) tuples to look up
            before_spend_date: Upper exclusive bound for the snapshot timestamp

        Returns:
            Dict mapping (project_name, budget_id) to the most recent ProjectSpendTracking row
        """
        if not project_budget_pairs:
            return {}

        from sqlalchemy import tuple_ as sa_tuple

        # Subquery: max spend_date per (project_name, budget_id) before the target snapshot
        latest_dates_subq = (
            select(
                ProjectSpendTracking.project_name,
                ProjectSpendTracking.budget_id,
                func.max(ProjectSpendTracking.spend_date).label("max_spend_date"),
            )
            .where(
                sa_tuple(ProjectSpendTracking.project_name, ProjectSpendTracking.budget_id).in_(project_budget_pairs)
            )
            .where(ProjectSpendTracking.spend_date < before_spend_date)
            .where(ProjectSpendTracking.spend_subject_type == "budget")
            .group_by(ProjectSpendTracking.project_name, ProjectSpendTracking.budget_id)
            .subquery()
        )

        stmt = select(ProjectSpendTracking).join(
            latest_dates_subq,
            (ProjectSpendTracking.project_name == latest_dates_subq.c.project_name)
            & (ProjectSpendTracking.budget_id == latest_dates_subq.c.budget_id)
            & (ProjectSpendTracking.spend_date == latest_dates_subq.c.max_spend_date),
        )

        result = await session.execute(stmt)
        rows = result.scalars().all()
        return {(row.project_name, row.budget_id): row for row in rows}

    async def insert_key_entries(
        self,
        session: AsyncSession,
        rows: list[ProjectSpendTracking],
    ) -> None:
        """Bulk upsert key-based rows by ``(project_name, key_hash, spend_date)``.

        Conflict target is the partial unique index for ``spend_subject_type = 'key'``.
        On conflict, all mutable fields are updated.

        Args:
            session: Async database session
            rows: ProjectSpendTracking rows with spend_subject_type='key' to upsert
        """
        if not rows:
            return

        stmt = (
            insert(ProjectSpendTracking)
            .values(
                [
                    {
                        "id": row.id,
                        "project_name": row.project_name,
                        "cost_center_id": row.cost_center_id,
                        "cost_center_name": row.cost_center_name,
                        "key_hash": row.key_hash,
                        "spend_date": row.spend_date,
                        "daily_spend": row.daily_spend,
                        "cumulative_spend": row.cumulative_spend,
                        "budget_period_spend": row.budget_period_spend,
                        "budget_reset_at": row.budget_reset_at,
                        "budget_id": row.budget_id,
                        "soft_budget": row.soft_budget,
                        "max_budget": row.max_budget,
                        "budget_duration": row.budget_duration,
                        "spend_subject_type": "key",
                    }
                    for row in rows
                ]
            )
            .on_conflict_do_update(
                index_elements=["project_name", "key_hash", "spend_date"],
                index_where=(ProjectSpendTracking.spend_subject_type == "key"),
                set_={
                    "daily_spend": insert(ProjectSpendTracking).excluded.daily_spend,
                    "cumulative_spend": insert(ProjectSpendTracking).excluded.cumulative_spend,
                    "budget_period_spend": insert(ProjectSpendTracking).excluded.budget_period_spend,
                    "budget_reset_at": insert(ProjectSpendTracking).excluded.budget_reset_at,
                    "budget_id": insert(ProjectSpendTracking).excluded.budget_id,
                    "soft_budget": insert(ProjectSpendTracking).excluded.soft_budget,
                    "max_budget": insert(ProjectSpendTracking).excluded.max_budget,
                    "budget_duration": insert(ProjectSpendTracking).excluded.budget_duration,
                },
            )
        )

        await session.execute(stmt)
        await session.commit()

    async def insert_budget_entries(
        self,
        session: AsyncSession,
        rows: list[ProjectSpendTracking],
    ) -> None:
        """Bulk upsert budget-based rows by ``(project_name, budget_id, spend_date)``.

        Conflict target is the partial unique index for ``spend_subject_type = 'budget'``.
        On conflict, all mutable fields are updated.

        Args:
            session: Async database session
            rows: ProjectSpendTracking rows with spend_subject_type='budget' to upsert
        """
        if not rows:
            return

        stmt = (
            insert(ProjectSpendTracking)
            .values(
                [
                    {
                        "id": row.id,
                        "project_name": row.project_name,
                        "cost_center_id": row.cost_center_id,
                        "cost_center_name": row.cost_center_name,
                        "key_hash": None,
                        "spend_date": row.spend_date,
                        "daily_spend": row.daily_spend,
                        "cumulative_spend": row.cumulative_spend,
                        "budget_period_spend": row.budget_period_spend,
                        "budget_reset_at": row.budget_reset_at,
                        "budget_id": row.budget_id,
                        "soft_budget": row.soft_budget,
                        "max_budget": row.max_budget,
                        "budget_duration": row.budget_duration,
                        "spend_subject_type": "budget",
                    }
                    for row in rows
                ]
            )
            .on_conflict_do_update(
                index_elements=["project_name", "budget_id", "spend_date"],
                index_where=(ProjectSpendTracking.spend_subject_type == "budget"),
                set_={
                    "daily_spend": insert(ProjectSpendTracking).excluded.daily_spend,
                    "cumulative_spend": insert(ProjectSpendTracking).excluded.cumulative_spend,
                    "budget_period_spend": insert(ProjectSpendTracking).excluded.budget_period_spend,
                    "budget_reset_at": insert(ProjectSpendTracking).excluded.budget_reset_at,
                    "soft_budget": insert(ProjectSpendTracking).excluded.soft_budget,
                    "max_budget": insert(ProjectSpendTracking).excluded.max_budget,
                    "budget_duration": insert(ProjectSpendTracking).excluded.budget_duration,
                },
            )
        )

        await session.execute(stmt)
        await session.commit()

    async def get_entries_for_date(
        self,
        session: AsyncSession,
        spend_date: datetime,
    ) -> list[ProjectSpendTracking]:
        """Return all rows for an exact snapshot timestamp.

        Used by the standalone spend tracking app to read daily snapshots for BigQuery export.

        Args:
            session: Async database session
            spend_date: Snapshot timestamp to query

        Returns:
            List of ProjectSpendTracking rows for the given date
        """
        stmt = select(ProjectSpendTracking).where(ProjectSpendTracking.spend_date == spend_date)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_spending_by_project(
        self,
        session: AsyncSession,
        project_names: list[str],
        spend_subject_type: str | None = None,
    ) -> list[ProjectSpendTracking]:
        """Return the most recent snapshot row per (project_name, budget_id or key_hash).

        Used by project endpoints to build spending summaries.

        Args:
            session: Async database session
            project_names: Project names to query
            spend_subject_type: Filter by subject type ('key' or 'budget'); None returns all

        Returns:
            List of most recent ProjectSpendTracking rows
        """
        if not project_names:
            return []

        # Subquery: max spend_date per (project_name, budget_id, key_hash)
        latest_dates_subq = select(
            ProjectSpendTracking.project_name,
            ProjectSpendTracking.budget_id,
            ProjectSpendTracking.key_hash,
            ProjectSpendTracking.spend_subject_type,
            func.max(ProjectSpendTracking.spend_date).label("max_spend_date"),
        ).where(ProjectSpendTracking.project_name.in_(project_names))

        if spend_subject_type is not None:
            latest_dates_subq = latest_dates_subq.where(ProjectSpendTracking.spend_subject_type == spend_subject_type)

        latest_dates_subq = latest_dates_subq.group_by(
            ProjectSpendTracking.project_name,
            ProjectSpendTracking.budget_id,
            ProjectSpendTracking.key_hash,
            ProjectSpendTracking.spend_subject_type,
        ).subquery()

        stmt = select(ProjectSpendTracking).join(
            latest_dates_subq,
            (ProjectSpendTracking.project_name == latest_dates_subq.c.project_name)
            & (ProjectSpendTracking.spend_date == latest_dates_subq.c.max_spend_date)
            & (ProjectSpendTracking.spend_subject_type == latest_dates_subq.c.spend_subject_type)
            & ProjectSpendTracking.budget_id.is_not_distinct_from(latest_dates_subq.c.budget_id)
            & ProjectSpendTracking.key_hash.is_not_distinct_from(latest_dates_subq.c.key_hash),
        )

        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_key_spending_for_project(
        self,
        session: AsyncSession,
        project_name: str,
    ) -> ProjectSpendTracking | None:
        """Return the most recent key-based row for a project (authoritative total).

        Args:
            session: Async database session
            project_name: Project name to query

        Returns:
            Most recent key-based ProjectSpendTracking row or None
        """
        latest_dates_subq = (
            select(func.max(ProjectSpendTracking.spend_date).label("max_spend_date"))
            .where(ProjectSpendTracking.project_name == project_name)
            .where(ProjectSpendTracking.spend_subject_type == "key")
            .scalar_subquery()
        )

        stmt = (
            select(ProjectSpendTracking)
            .where(ProjectSpendTracking.project_name == project_name)
            .where(ProjectSpendTracking.spend_subject_type == "key")
            .where(ProjectSpendTracking.spend_date == latest_dates_subq)
            .order_by(ProjectSpendTracking.cumulative_spend.desc(), ProjectSpendTracking.key_hash)
            .limit(1)
        )

        result = await session.execute(stmt)
        return result.scalars().first()

    async def get_latest_budget_rows_for_project(
        self,
        session: AsyncSession,
        project_name: str,
        rows_limit: int = 50,
    ) -> list[ProjectSpendTracking]:
        """Return the most recent budget-based rows per budget_id for a project.

        Args:
            session: Async database session
            project_name: Project name to query
            rows_limit: Maximum number of rows to return

        Returns:
            List of most recent budget-based ProjectSpendTracking rows
        """
        latest_dates_subq = (
            select(
                ProjectSpendTracking.budget_id,
                func.max(ProjectSpendTracking.spend_date).label("max_spend_date"),
            )
            .where(ProjectSpendTracking.project_name == project_name)
            .where(ProjectSpendTracking.spend_subject_type == "budget")
            .group_by(ProjectSpendTracking.budget_id)
            .subquery()
        )

        stmt = (
            select(ProjectSpendTracking)
            .join(
                latest_dates_subq,
                (ProjectSpendTracking.budget_id == latest_dates_subq.c.budget_id)
                & (ProjectSpendTracking.spend_date == latest_dates_subq.c.max_spend_date),
            )
            .where(ProjectSpendTracking.project_name == project_name)
            .where(ProjectSpendTracking.spend_subject_type == "budget")
            .limit(rows_limit)
        )

        result = await session.execute(stmt)
        return list(result.scalars().all())
