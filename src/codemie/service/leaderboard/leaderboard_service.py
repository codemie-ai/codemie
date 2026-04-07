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

"""Leaderboard orchestration service.

Runs the collect -> score -> persist pipeline and stores results in PostgreSQL.
Supports rolling live snapshots plus archived monthly and quarterly seasons.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from codemie.configs import config, logger
from codemie.repository.leaderboard_repository import leaderboard_repository
from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.rest_api.models.leaderboard import LeaderboardEntry
from codemie.service.leaderboard.collector import LeaderboardCollector
from codemie.service.leaderboard.config import (
    RUN_TYPE_BACKFILL,
    RUN_TYPE_MANUAL,
    RUN_TYPE_SCHEDULED,
    SNAPSHOT_TYPE_MONTHLY,
    SNAPSHOT_TYPE_QUARTERLY,
    SNAPSHOT_TYPE_ROLLING,
    VIEW_CURRENT,
    VIEW_MONTHLY,
    VIEW_QUARTERLY,
    leaderboard_settings,
)
from codemie.service.leaderboard.scorer import LeaderboardScorer


@dataclass(frozen=True)
class SnapshotSpec:
    """Computation contract for a leaderboard snapshot."""

    period_start: datetime
    period_end: datetime
    period_days: int
    snapshot_type: str
    season_key: str | None
    period_label: str
    is_final: bool
    source_run_type: str


def _utcnow_naive() -> datetime:
    """Return current UTC time as a timezone-naive datetime for PG TIMESTAMP columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _start_of_day(day: date) -> datetime:
    return datetime.combine(day, time.min)


def _end_of_day(day: date) -> datetime:
    return datetime.combine(day, time.max)


def _days_inclusive(period_start: datetime, period_end: datetime) -> int:
    return (period_end.date() - period_start.date()).days + 1


class LeaderboardService:
    """Orchestrates leaderboard computation: collect -> score -> persist."""

    def __init__(
        self,
        session: AsyncSession,
        es_repository: MetricsElasticRepository | None = None,
    ) -> None:
        self._session = session
        self._es_repository = es_repository or MetricsElasticRepository()
        self._repository = leaderboard_repository

    async def compute_and_store(self, period_days: int = 30) -> str:
        """Backward-compatible rolling computation entrypoint."""
        return await self.compute_rolling_snapshot(period_days=period_days, source_run_type=RUN_TYPE_MANUAL)

    async def compute_rolling_snapshot(
        self,
        *,
        period_days: int = 30,
        source_run_type: str = RUN_TYPE_SCHEDULED,
    ) -> str:
        period_end = _utcnow_naive()
        period_start = period_end - timedelta(days=max(period_days - 1, 0))
        spec = SnapshotSpec(
            period_start=period_start,
            period_end=period_end,
            period_days=period_days,
            snapshot_type=SNAPSHOT_TYPE_ROLLING,
            season_key=None,
            period_label=f"Current {period_days} days",
            is_final=False,
            source_run_type=source_run_type,
        )
        snapshot_id = await self._compute_snapshot(spec)
        await self._cleanup_rolling_snapshots(current_snapshot_id=snapshot_id)
        return snapshot_id

    async def compute_monthly_archive(
        self,
        *,
        season_key: str | None = None,
        source_run_type: str = RUN_TYPE_SCHEDULED,
    ) -> str:
        spec = self._build_monthly_spec(season_key, source_run_type)
        return await self._compute_final_snapshot(spec)

    async def compute_quarterly_archive(
        self,
        *,
        season_key: str | None = None,
        source_run_type: str = RUN_TYPE_SCHEDULED,
    ) -> str:
        spec = self._build_quarterly_spec(season_key, source_run_type)
        return await self._compute_final_snapshot(spec)

    async def compute_for_view(
        self,
        *,
        view: str = VIEW_CURRENT,
        period_days: int = 30,
        season_key: str | None = None,
        source_run_type: str = RUN_TYPE_MANUAL,
    ) -> str:
        if view == VIEW_CURRENT:
            return await self.compute_rolling_snapshot(period_days=period_days, source_run_type=source_run_type)
        if view == VIEW_MONTHLY:
            run_type = RUN_TYPE_BACKFILL if season_key else source_run_type
            return await self.compute_monthly_archive(season_key=season_key, source_run_type=run_type)
        if view == VIEW_QUARTERLY:
            run_type = RUN_TYPE_BACKFILL if season_key else source_run_type
            return await self.compute_quarterly_archive(season_key=season_key, source_run_type=run_type)
        raise ValueError(f"Unsupported leaderboard view={view}")

    async def compute_missing_archives(self) -> list[str]:
        """Create missing seasonal archives for the latest closed month and quarter."""
        created_snapshot_ids: list[str] = []

        monthly_spec = self._build_monthly_spec(None, RUN_TYPE_SCHEDULED)
        if not await self._repository.season_snapshot_exists(
            self._session,
            monthly_spec.snapshot_type,
            monthly_spec.season_key or "",
        ):
            created_snapshot_ids.append(await self._compute_final_snapshot(monthly_spec))

        quarterly_spec = self._build_quarterly_spec(None, RUN_TYPE_SCHEDULED)
        if not await self._repository.season_snapshot_exists(
            self._session,
            quarterly_spec.snapshot_type,
            quarterly_spec.season_key or "",
        ):
            created_snapshot_ids.append(await self._compute_final_snapshot(quarterly_spec))

        return created_snapshot_ids

    async def _compute_final_snapshot(self, spec: SnapshotSpec) -> str:
        existing = await self._repository.get_snapshot_by_type_and_key(
            self._session,
            spec.snapshot_type,
            spec.season_key or "",
            status="completed",
            final_only=True,
        )
        if existing:
            logger.info(
                f"Leaderboard archive already exists for type={spec.snapshot_type} season_key={spec.season_key}: "
                f"snapshot_id={existing.id}"
            )
            return existing.id
        return await self._compute_snapshot(spec)

    async def _compute_snapshot(self, spec: SnapshotSpec) -> str:
        if spec.is_final:
            comparison_snapshot = await self._repository.get_prior_snapshot_by_type(
                self._session,
                spec.snapshot_type,
                before_period_start=spec.period_start,
                is_final=True,
            )
        else:
            # For rolling snapshots, windows overlap so period_end < period_start
            # would skip the immediately previous snapshot. Instead, fetch the
            # latest completed rolling snapshot (the current one is still "running").
            comparison_snapshot = await self._repository.get_latest_snapshot_by_type(
                self._session,
                spec.snapshot_type,
                status="completed",
            )
        snapshot = await self._repository.create_snapshot(
            self._session,
            spec.period_start,
            spec.period_end,
            spec.period_days,
            snapshot_type=spec.snapshot_type,
            season_key=spec.season_key,
            period_label=spec.period_label,
            is_final=spec.is_final,
            source_run_type=spec.source_run_type,
            comparison_snapshot_id=comparison_snapshot.id if comparison_snapshot else None,
            metadata={"view_label": spec.period_label},
        )
        snapshot_id = snapshot.id
        logger.info(
            f"Leaderboard computation started: snapshot_id={snapshot_id}, snapshot_type={spec.snapshot_type}, "
            f"season_key={spec.season_key}, period_start={spec.period_start.isoformat()}, "
            f"period_end={spec.period_end.isoformat()}"
        )

        try:
            collector = LeaderboardCollector(self._session, self._es_repository)
            raw_metrics = await collector.collect(spec.period_start, spec.period_end)
            logger.info(f"Leaderboard collected {len(raw_metrics)} user metrics for snapshot_id={snapshot_id}")

            scorer = LeaderboardScorer(leaderboard_settings)
            scored_entries = scorer.score_all(raw_metrics)
            logger.info(f"Leaderboard scored {len(scored_entries)} users for snapshot_id={snapshot_id}")

            now = _utcnow_naive()
            entries = [
                LeaderboardEntry(
                    id=str(uuid4()),
                    snapshot_id=snapshot_id,
                    user_id=e.user_id,
                    user_name=e.user_name,
                    user_email=e.user_email,
                    rank=e.rank,
                    total_score=e.total_score,
                    tier_name=e.tier_name,
                    tier_level=e.tier_level,
                    usage_intent=e.usage_intent,
                    dimensions=[
                        {
                            "id": d.id,
                            "label": d.label,
                            "weight": d.weight,
                            "score": round(d.score, 4),
                            "components": d.components,
                        }
                        for d in e.dimensions
                    ],
                    summary_metrics=e.summary_metrics,
                    projects=e.projects,
                    date=now,
                    update_date=now,
                )
                for e in scored_entries
            ]
            await self._repository.bulk_insert_entries(self._session, entries)

            await self._repository.update_snapshot_status(
                self._session,
                snapshot_id,
                "completed",
                total_users=len(scored_entries),
            )
            await self._session.commit()

            logger.info(
                f"Leaderboard computation completed: snapshot_id={snapshot_id}, snapshot_type={spec.snapshot_type}, "
                f"season_key={spec.season_key}, users={len(scored_entries)}"
            )
            return snapshot_id

        except Exception as exc:
            logger.error(f"Leaderboard computation failed for snapshot_id={snapshot_id}: {exc}", exc_info=True)
            try:
                await self._repository.update_snapshot_status(self._session, snapshot_id, "failed", error=str(exc))
                await self._session.commit()
            except Exception as commit_err:
                logger.error(f"Failed to mark snapshot as failed: {commit_err}", exc_info=True)
            raise

    async def _cleanup_rolling_snapshots(self, current_snapshot_id: str) -> None:
        deleted = await self._repository.delete_old_snapshots(
            self._session,
            snapshot_type=SNAPSHOT_TYPE_ROLLING,
            keep_count=config.LEADERBOARD_KEEP_ROLLING_SNAPSHOTS,
            current_snapshot_id=current_snapshot_id,
            final_only=False,
        )
        if deleted:
            await self._session.commit()
            logger.info(f"Leaderboard cleanup: deleted {deleted} old rolling snapshots")

    def _build_monthly_spec(self, season_key: str | None, source_run_type: str) -> SnapshotSpec:
        if season_key:
            year, month = self._parse_monthly_key(season_key)
        else:
            year, month = self._latest_closed_month(_utcnow_naive().date())
            season_key = f"{year:04d}-{month:02d}"

        last_day = monthrange(year, month)[1]
        period_start = _start_of_day(date(year, month, 1))
        period_end = _end_of_day(date(year, month, last_day))
        return SnapshotSpec(
            period_start=period_start,
            period_end=period_end,
            period_days=_days_inclusive(period_start, period_end),
            snapshot_type=SNAPSHOT_TYPE_MONTHLY,
            season_key=season_key,
            period_label=f"{period_start.strftime('%B %Y')}",
            is_final=True,
            source_run_type=source_run_type,
        )

    def _build_quarterly_spec(self, season_key: str | None, source_run_type: str) -> SnapshotSpec:
        if season_key:
            year, quarter = self._parse_quarterly_key(season_key)
        else:
            year, quarter = self._latest_closed_quarter(_utcnow_naive().date())
            season_key = f"{year:04d}-Q{quarter}"

        start_month = (quarter - 1) * 3 + 1
        end_month = start_month + 2
        last_day = monthrange(year, end_month)[1]
        period_start = _start_of_day(date(year, start_month, 1))
        period_end = _end_of_day(date(year, end_month, last_day))
        return SnapshotSpec(
            period_start=period_start,
            period_end=period_end,
            period_days=_days_inclusive(period_start, period_end),
            snapshot_type=SNAPSHOT_TYPE_QUARTERLY,
            season_key=season_key,
            period_label=f"Q{quarter} {year}",
            is_final=True,
            source_run_type=source_run_type,
        )

    @staticmethod
    def _latest_closed_month(today: date) -> tuple[int, int]:
        if today.month == 1:
            return today.year - 1, 12
        return today.year, today.month - 1

    @staticmethod
    def _latest_closed_quarter(today: date) -> tuple[int, int]:
        current_quarter = ((today.month - 1) // 3) + 1
        previous_quarter = current_quarter - 1
        year = today.year
        if previous_quarter == 0:
            previous_quarter = 4
            year -= 1
        return year, previous_quarter

    @staticmethod
    def _parse_monthly_key(season_key: str) -> tuple[int, int]:
        try:
            year_text, month_text = season_key.split("-", maxsplit=1)
            year = int(year_text)
            month = int(month_text)
        except ValueError as exc:
            raise ValueError(f"Invalid monthly season_key={season_key}. Expected YYYY-MM") from exc

        if month < 1 or month > 12:
            raise ValueError(f"Invalid monthly season_key={season_key}. Month must be 01-12")
        return year, month

    @staticmethod
    def _parse_quarterly_key(season_key: str) -> tuple[int, int]:
        try:
            year_text, quarter_text = season_key.split("-", maxsplit=1)
            year = int(year_text)
            if not quarter_text.startswith("Q"):
                raise ValueError
            quarter = int(quarter_text[1:])
        except ValueError as exc:
            raise ValueError(f"Invalid quarterly season_key={season_key}. Expected YYYY-QN") from exc

        if quarter < 1 or quarter > 4:
            raise ValueError(f"Invalid quarterly season_key={season_key}. Quarter must be Q1-Q4")
        return year, quarter
