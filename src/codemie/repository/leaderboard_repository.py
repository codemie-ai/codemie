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

"""Repository for leaderboard snapshot and entry CRUD operations."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from codemie.configs import logger
from codemie.rest_api.models.leaderboard import LeaderboardEntry, LeaderboardSnapshot
from codemie.service.leaderboard.config import SNAPSHOT_TYPE_ROLLING


class LeaderboardRepository:
    """CRUD operations for leaderboard_snapshots and leaderboard_entries."""

    async def create_snapshot(
        self,
        session: AsyncSession,
        period_start: datetime,
        period_end: datetime,
        period_days: int,
        *,
        snapshot_type: str = SNAPSHOT_TYPE_ROLLING,
        season_key: str | None = None,
        period_label: str | None = None,
        is_final: bool = False,
        source_run_type: str = "scheduled",
        comparison_snapshot_id: str | None = None,
        metadata: dict | None = None,
    ) -> LeaderboardSnapshot:
        snapshot = LeaderboardSnapshot(
            id=str(uuid4()),
            period_start=period_start,
            period_end=period_end,
            period_days=period_days,
            snapshot_type=snapshot_type,
            season_key=season_key,
            period_label=period_label,
            is_final=is_final,
            source_run_type=source_run_type,
            comparison_snapshot_id=comparison_snapshot_id,
            status="running",
            metadata_json=metadata or {},
            date=datetime.now(timezone.utc).replace(tzinfo=None),
            update_date=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(snapshot)
        await session.flush()
        return snapshot

    async def update_snapshot_status(
        self,
        session: AsyncSession,
        snapshot_id: str,
        status: str,
        *,
        total_users: int | None = None,
        error: str | None = None,
    ) -> None:
        values: dict = {"status": status, "update_date": datetime.now(timezone.utc).replace(tzinfo=None)}
        if status == "completed":
            values["completed_at"] = datetime.now(timezone.utc).replace(tzinfo=None)
        if total_users is not None:
            values["total_users"] = total_users
        if error is not None:
            values["error_message"] = error

        stmt = update(LeaderboardSnapshot).where(LeaderboardSnapshot.id == snapshot_id).values(**values)
        await session.execute(stmt)

    async def get_latest_snapshot(
        self,
        session: AsyncSession,
        status: str = "completed",
        snapshot_type: str = SNAPSHOT_TYPE_ROLLING,
    ) -> LeaderboardSnapshot | None:
        return await self.get_latest_snapshot_by_type(session, snapshot_type, status=status)

    async def get_latest_snapshot_by_type(
        self,
        session: AsyncSession,
        snapshot_type: str,
        *,
        status: str = "completed",
        is_final: bool | None = None,
    ) -> LeaderboardSnapshot | None:
        stmt = select(LeaderboardSnapshot).where(
            LeaderboardSnapshot.status == status,
            LeaderboardSnapshot.snapshot_type == snapshot_type,
        )
        if is_final is not None:
            stmt = stmt.where(LeaderboardSnapshot.is_final == is_final)
        stmt = stmt.order_by(LeaderboardSnapshot.period_end.desc(), LeaderboardSnapshot.date.desc()).limit(1)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_snapshot_by_id(
        self,
        session: AsyncSession,
        snapshot_id: str,
    ) -> LeaderboardSnapshot | None:
        stmt = select(LeaderboardSnapshot).where(LeaderboardSnapshot.id == snapshot_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_snapshot_by_type_and_key(
        self,
        session: AsyncSession,
        snapshot_type: str,
        season_key: str,
        *,
        status: str = "completed",
        final_only: bool = False,
    ) -> LeaderboardSnapshot | None:
        stmt = select(LeaderboardSnapshot).where(
            LeaderboardSnapshot.snapshot_type == snapshot_type,
            LeaderboardSnapshot.season_key == season_key,
            LeaderboardSnapshot.status == status,
        )
        if final_only:
            stmt = stmt.where(LeaderboardSnapshot.is_final.is_(True))
        stmt = stmt.order_by(LeaderboardSnapshot.date.desc()).limit(1)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_prior_snapshot_by_type(
        self,
        session: AsyncSession,
        snapshot_type: str,
        *,
        before_period_start: datetime,
        status: str = "completed",
        is_final: bool | None = None,
    ) -> LeaderboardSnapshot | None:
        stmt = select(LeaderboardSnapshot).where(
            LeaderboardSnapshot.snapshot_type == snapshot_type,
            LeaderboardSnapshot.status == status,
            LeaderboardSnapshot.period_end < before_period_start,
        )
        if is_final is not None:
            stmt = stmt.where(LeaderboardSnapshot.is_final == is_final)
        stmt = stmt.order_by(LeaderboardSnapshot.period_end.desc(), LeaderboardSnapshot.date.desc()).limit(1)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def season_snapshot_exists(
        self,
        session: AsyncSession,
        snapshot_type: str,
        season_key: str,
    ) -> bool:
        stmt = (
            select(func.count())
            .select_from(LeaderboardSnapshot)
            .where(
                LeaderboardSnapshot.snapshot_type == snapshot_type,
                LeaderboardSnapshot.season_key == season_key,
                LeaderboardSnapshot.status == "completed",
                LeaderboardSnapshot.is_final.is_(True),
            )
        )
        result = await session.execute(stmt)
        return bool(result.scalar_one())

    async def list_snapshots(
        self,
        session: AsyncSession,
        page: int = 0,
        per_page: int = 10,
        *,
        snapshot_type: str | None = None,
        status: str | None = None,
        is_final: bool | None = None,
    ) -> tuple[list[LeaderboardSnapshot], int]:
        base = select(LeaderboardSnapshot)
        if snapshot_type:
            base = base.where(LeaderboardSnapshot.snapshot_type == snapshot_type)
        if status:
            base = base.where(LeaderboardSnapshot.status == status)
        if is_final is not None:
            base = base.where(LeaderboardSnapshot.is_final == is_final)

        count_stmt = select(func.count()).select_from(base.subquery())
        count_result = await session.execute(count_stmt)
        total = count_result.scalar_one()

        stmt = (
            base.order_by(LeaderboardSnapshot.period_end.desc(), LeaderboardSnapshot.date.desc())
            .offset(page * per_page)
            .limit(per_page)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all()), total

    async def bulk_insert_entries(
        self,
        session: AsyncSession,
        entries: list[LeaderboardEntry],
    ) -> int:
        session.add_all(entries)
        await session.flush()
        return len(entries)

    async def get_entries(
        self,
        session: AsyncSession,
        snapshot_id: str,
        page: int = 0,
        per_page: int = 20,
        tier: str | None = None,
        search: str | None = None,
        intent: str | None = None,
        sort_by: str | None = None,
        sort_order: str = "asc",
    ) -> tuple[list[LeaderboardEntry], int]:
        base = select(LeaderboardEntry).where(LeaderboardEntry.snapshot_id == snapshot_id)
        if tier:
            base = base.where(LeaderboardEntry.tier_name == tier)
        if search:
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            base = base.where(
                LeaderboardEntry.user_name.op("ILIKE")(pattern) | LeaderboardEntry.user_email.op("ILIKE")(pattern)
            )
        if intent:
            base = base.where(LeaderboardEntry.usage_intent == intent)

        count_stmt = select(func.count()).select_from(base.subquery())
        count_result = await session.execute(count_stmt)
        total = count_result.scalar_one()

        sort_column = LeaderboardEntry.rank
        allowed_sort = {
            "rank": LeaderboardEntry.rank,
            "total_score": LeaderboardEntry.total_score,
            "user_name": LeaderboardEntry.user_name,
            "tier_level": LeaderboardEntry.tier_level,
        }
        if sort_by and sort_by in allowed_sort:
            sort_column = allowed_sort[sort_by]
        elif sort_by and sort_by not in allowed_sort:
            logger.warning(f"Leaderboard entries: unknown sort_by={sort_by}, falling back to rank")

        if sort_order == "desc":
            stmt = base.order_by(sort_column.desc()).offset(page * per_page).limit(per_page)
        else:
            stmt = base.order_by(sort_column.asc()).offset(page * per_page).limit(per_page)

        result = await session.execute(stmt)
        return list(result.scalars().all()), total

    async def get_entry_by_user(
        self,
        session: AsyncSession,
        snapshot_id: str,
        user_id: str,
    ) -> LeaderboardEntry | None:
        stmt = (
            select(LeaderboardEntry)
            .where(
                LeaderboardEntry.snapshot_id == snapshot_id,
                (LeaderboardEntry.user_id == user_id) | (LeaderboardEntry.user_email == user_id),
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_entries_by_users(
        self,
        session: AsyncSession,
        snapshot_id: str,
        user_ids: list[str],
    ) -> dict[str, LeaderboardEntry]:
        if not user_ids:
            return {}

        stmt = select(LeaderboardEntry).where(
            LeaderboardEntry.snapshot_id == snapshot_id,
            LeaderboardEntry.user_id.in_(user_ids),
        )
        result = await session.execute(stmt)
        return {entry.user_id: entry for entry in result.scalars().all()}

    async def get_tier_distribution(
        self,
        session: AsyncSession,
        snapshot_id: str,
    ) -> list[dict]:
        stmt = (
            select(
                LeaderboardEntry.tier_name,
                LeaderboardEntry.tier_level,
                func.count().label("user_count"),
            )
            .where(LeaderboardEntry.snapshot_id == snapshot_id)
            .group_by(LeaderboardEntry.tier_name, LeaderboardEntry.tier_level)
            .order_by(LeaderboardEntry.tier_level.desc())
        )
        result = await session.execute(stmt)
        return [
            {"tier_name": row.tier_name, "tier_level": row.tier_level, "user_count": row.user_count}
            for row in result.all()
        ]

    async def get_score_distribution(
        self,
        session: AsyncSession,
        snapshot_id: str,
    ) -> list[dict]:
        bucket = func.floor(LeaderboardEntry.total_score / 10) * 10
        stmt = (
            select(
                bucket.label("bucket"),
                func.count().label("count"),
            )
            .where(LeaderboardEntry.snapshot_id == snapshot_id)
            .group_by(bucket)
            .order_by(bucket)
        )
        result = await session.execute(stmt)
        return [
            {
                "range": f"{int(row.bucket)}-{int(row.bucket) + 10}",
                "count": row.count,
            }
            for row in result.all()
        ]

    async def get_dimension_averages(
        self,
        session: AsyncSession,
        snapshot_id: str,
        dimension_ids: list[str],
    ) -> dict[str, float]:
        stmt = text("""
            SELECT dim->>'id' AS dim_id,
                   AVG((dim->>'score')::float) AS avg_score
            FROM leaderboard_entries,
                 jsonb_array_elements(dimensions) AS dim
            WHERE snapshot_id = :snapshot_id
              AND dim->>'id' = ANY(:dim_ids)
            GROUP BY dim->>'id'
        """)
        result = await session.execute(stmt, {"snapshot_id": snapshot_id, "dim_ids": dimension_ids})
        return {row.dim_id: round(float(row.avg_score), 4) for row in result.all()}

    async def get_average_score(
        self,
        session: AsyncSession,
        snapshot_id: str,
    ) -> float | None:
        stmt = select(func.avg(LeaderboardEntry.total_score)).where(LeaderboardEntry.snapshot_id == snapshot_id)
        result = await session.execute(stmt)
        value = result.scalar_one_or_none()
        return round(float(value), 1) if value is not None else None

    async def get_top_entries(
        self,
        session: AsyncSession,
        snapshot_id: str,
        limit: int = 3,
    ) -> list[LeaderboardEntry]:
        stmt = (
            select(LeaderboardEntry)
            .where(LeaderboardEntry.snapshot_id == snapshot_id)
            .order_by(LeaderboardEntry.rank)
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def delete_old_snapshots(
        self,
        session: AsyncSession,
        *,
        snapshot_type: str,
        keep_count: int,
        current_snapshot_id: str | None = None,
        final_only: bool | None = False,
    ) -> int:
        # keep_q: select the IDs of snapshots we want to retain (most recent `keep_count`)
        keep_q = select(LeaderboardSnapshot.id).where(LeaderboardSnapshot.snapshot_type == snapshot_type)
        if final_only is not None:
            keep_q = keep_q.where(LeaderboardSnapshot.is_final == final_only)
        keep_q = keep_q.order_by(LeaderboardSnapshot.period_end.desc(), LeaderboardSnapshot.date.desc()).limit(
            keep_count
        )

        # del_q: delete snapshots NOT in keep_q.
        # The `final_only` filter is applied again here intentionally — it restricts the DELETE
        # to only snapshots matching the same `is_final` criterion, so snapshots with a different
        # `is_final` value are never touched even though they were not included in keep_q.
        del_q = delete(LeaderboardSnapshot).where(
            LeaderboardSnapshot.snapshot_type == snapshot_type,
            LeaderboardSnapshot.id.not_in(keep_q),
        )
        if final_only is not None:
            del_q = del_q.where(LeaderboardSnapshot.is_final == final_only)
        if current_snapshot_id:
            del_q = del_q.where(LeaderboardSnapshot.id != current_snapshot_id)

        result = await session.execute(del_q)
        return result.rowcount or 0


leaderboard_repository = LeaderboardRepository()
