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

"""APScheduler-based stale datasource detection scheduler.

Follows the LeaderboardScheduler pattern with async_leader_lock
for multi-pod safety.
"""

from __future__ import annotations

from apscheduler.triggers.cron import CronTrigger

from codemie.clients.postgres import get_async_session
from codemie.configs import config, logger
from codemie.repository.metrics_elastic_repository import MetricsElasticRepository
from codemie.utils.leader_lock import async_leader_lock
from codemie.service.stale_datasource.config import STALE_DATASOURCE_LOCK_ID
from codemie.service.stale_datasource.stale_datasource_service import (
    StaleDatasourceService,
)


class StaleDatasourceScheduler:
    """Scheduler for nightly stale datasource detection.

    Uses async_leader_lock advisory-lock helper to ensure only one pod
    runs the detection in a multi-replica deployment.
    """

    def __init__(self, scheduler) -> None:
        self.scheduler = scheduler

    def start(self) -> None:
        if not config.STALE_DATASOURCE_ENABLED:
            return

        trigger = CronTrigger.from_crontab(config.STALE_DATASOURCE_SCHEDULE, timezone="UTC")
        self.scheduler.add_job(
            self._run_stale_detection,
            trigger=trigger,
            id="stale_datasource_detection",
            replace_existing=True,
            name="Stale Datasource Detection",
        )

        if not self.scheduler.running:
            self.scheduler.start()

        logger.info(
            f"Registered stale datasource detection job with schedule: {config.STALE_DATASOURCE_SCHEDULE!r} (UTC)"
        )

    async def _run_stale_detection(self) -> None:
        """Nightly job with leader lock for multi-pod safety."""
        async with async_leader_lock(STALE_DATASOURCE_LOCK_ID) as acquired:
            if not acquired:
                logger.info("Stale datasource detection: not the leader, skipping")
                return

            try:
                async with get_async_session() as session:
                    service = StaleDatasourceService(session, MetricsElasticRepository())
                    stats = await service.detect_and_mark_stale_datasources()
                    deletion_info = ""
                    if stats.get("deletion_aborted"):
                        deletion_info = ", deletion=ABORTED (circuit breaker)"
                    elif stats.get("stale_rows_swept") is not None:
                        deletion_info = (
                            f", swept={stats['stale_rows_swept']}, "
                            f"deleted={stats['indexes_deleted']}, "
                            f"archived={stats['datasources_archived']}, "
                            f"skipped_shared={stats['skipped_shared_index']}, "
                            f"deletion_errors={stats['deletion_errors']}, "
                            f"reactivated={stats['reactivated']}, "
                            f"recheck_errors={stats['recheck_errors']}"
                        )
                    logger.info(
                        f"Stale datasource detection completed: "
                        f"evaluated={stats['total_evaluated']}, "
                        f"newly_stale={stats['newly_marked_stale']}, "
                        f"errors={stats['errors']}"
                        f"{deletion_info}"
                    )
            except Exception as e:
                logger.error(f"Stale datasource detection failed: {e}", exc_info=True)

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("StaleDatasourceScheduler stopped")
