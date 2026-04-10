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

"""APScheduler-based leaderboard computation scheduler.

Follows the SpendTrackingScheduler pattern with LeaderLockContext
for multi-pod safety.
"""

from __future__ import annotations

import asyncio

from apscheduler.triggers.cron import CronTrigger

from codemie.clients.postgres import get_async_session
from codemie.configs import config, logger
from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.service.conversation_analysis.leader_lock import LeaderLockContext
from codemie.service.leaderboard.config import LEADERBOARD_LOCK_ID
from codemie.service.leaderboard.leaderboard_service import LeaderboardService


class LeaderboardScheduler:
    """Scheduler for nightly leaderboard computation.

    Uses LeaderLockContext advisory-lock pattern to ensure only one pod
    runs the computation in a multi-replica deployment.
    """

    def __init__(self, scheduler) -> None:
        self.scheduler = scheduler

    def start(self) -> None:
        if not config.LEADERBOARD_ENABLED:
            return

        cron_parts = config.LEADERBOARD_SCHEDULE.split()
        if len(cron_parts) != 5:
            logger.error(
                f"Invalid LEADERBOARD_SCHEDULE cron expression: "
                f"{config.LEADERBOARD_SCHEDULE!r}; skipping job registration"
            )
            return

        minute, hour, day, month, day_of_week = cron_parts
        trigger = CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
            timezone="UTC",
        )
        self.scheduler.add_job(
            self._run_leaderboard_computation,
            trigger=trigger,
            id="leaderboard_computation",
            replace_existing=True,
            name="Leaderboard Computation",
        )

        if not self.scheduler.running:
            self.scheduler.start()

        logger.info(f"Registered leaderboard computation job with schedule: {config.LEADERBOARD_SCHEDULE!r} (UTC)")

    async def _run_leaderboard_computation(self) -> None:
        """Nightly job with leader lock for multi-pod safety."""
        lock = await asyncio.to_thread(self._acquire_leader_lock)
        if lock is None:
            return

        try:
            async with get_async_session() as session:
                service = LeaderboardService(session, MetricsElasticRepository())
                rolling_snapshot_id = await service.compute_rolling_snapshot(
                    period_days=config.LEADERBOARD_PERIOD_DAYS,
                )
                archived_snapshot_ids = await service.compute_missing_archives()
                logger.info(
                    f"Leaderboard computation completed: rolling_snapshot_id={rolling_snapshot_id}, "
                    f"archived_snapshot_ids={archived_snapshot_ids}"
                )
        except Exception as e:
            logger.error(f"Leaderboard computation failed: {e}", exc_info=True)
        finally:
            await asyncio.to_thread(self._release_leader_lock, lock)

    @staticmethod
    def _acquire_leader_lock() -> LeaderLockContext | None:
        """Acquire the leader lock synchronously (run via to_thread)."""
        lock = LeaderLockContext(lock_id=LEADERBOARD_LOCK_ID)
        lock.__enter__()
        if not lock.acquired:
            logger.info("Leaderboard computation: not the leader, skipping")
            lock.__exit__(None, None, None)
            return None
        return lock

    @staticmethod
    def _release_leader_lock(lock: LeaderLockContext) -> None:
        """Release the leader lock synchronously (run via to_thread)."""
        lock.__exit__(None, None, None)

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("LeaderboardScheduler stopped")
