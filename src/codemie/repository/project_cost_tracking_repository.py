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

from codemie.service.chargeback.spend_models import ProjectCostTracking


class ProjectCostTrackingRepository:
    """Async repository for project_cost_tracking table."""

    async def get_latest_before_by_key_hashes(
        self,
        session: AsyncSession,
        key_hashes: list[str],
        before_spend_date: datetime,
    ) -> dict[str, ProjectCostTracking]:
        """Return the most recent row per key_hash before ``before_spend_date``.

        Used by the spend collector to retrieve the previous snapshot baseline for
        delta calculation. Missing keys are absent from the result (bootstrap case).

        Args:
            session: Async database session
            key_hashes: List of key hashes to look up
            before_spend_date: Upper exclusive bound for the snapshot timestamp

        Returns:
            Dict mapping key_hash to the most recent ProjectCostTracking row
        """
        if not key_hashes:
            return {}

        # Subquery: max spend_date per key_hash before the target snapshot
        latest_dates_subq = (
            select(
                ProjectCostTracking.key_hash,
                func.max(ProjectCostTracking.spend_date).label("max_spend_date"),
            )
            .where(ProjectCostTracking.key_hash.in_(key_hashes))
            .where(ProjectCostTracking.spend_date < before_spend_date)
            .group_by(ProjectCostTracking.key_hash)
            .subquery()
        )

        # Join to get full rows for those latest dates
        stmt = select(ProjectCostTracking).join(
            latest_dates_subq,
            (ProjectCostTracking.key_hash == latest_dates_subq.c.key_hash)
            & (ProjectCostTracking.spend_date == latest_dates_subq.c.max_spend_date),
        )

        result = await session.execute(stmt)
        rows = result.scalars().all()
        return {row.key_hash: row for row in rows}

    async def insert_entries(
        self,
        session: AsyncSession,
        rows: list[ProjectCostTracking],
    ) -> None:
        """Bulk upsert rows by ``(key_hash, spend_date)``.

        Args:
            session: Async database session
            rows: ProjectCostTracking rows to insert
        """
        if not rows:
            return

        base_insert = insert(ProjectCostTracking)
        stmt = base_insert.values(
            [
                {
                    "id": row.id,
                    "project_name": row.project_name,
                    "key_hash": row.key_hash,
                    "spend_date": row.spend_date,
                    "daily_spend": row.daily_spend,
                    "cumulative_spend": row.cumulative_spend,
                    "budget_period_spend": row.budget_period_spend,
                    "budget_reset_at": row.budget_reset_at,
                }
                for row in rows
            ]
        ).on_conflict_do_update(
            index_elements=["key_hash", "spend_date"],
            set_={
                "project_name": base_insert.excluded.project_name,
                "daily_spend": base_insert.excluded.daily_spend,
                "cumulative_spend": base_insert.excluded.cumulative_spend,
                "budget_period_spend": base_insert.excluded.budget_period_spend,
                "budget_reset_at": base_insert.excluded.budget_reset_at,
            },
        )

        await session.execute(stmt)
        await session.commit()

    async def get_entries_for_date(
        self,
        session: AsyncSession,
        spend_date: datetime,
    ) -> list[ProjectCostTracking]:
        """Return all rows for an exact snapshot timestamp.

        Used by the standalone chargeback app to read daily snapshots for BigQuery export.

        Args:
            session: Async database session
            spend_date: Snapshot timestamp to query

        Returns:
            List of ProjectCostTracking rows for the given date
        """
        stmt = select(ProjectCostTracking).where(ProjectCostTracking.spend_date == spend_date)
        result = await session.execute(stmt)
        return list(result.scalars().all())
