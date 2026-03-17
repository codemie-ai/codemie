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

from apscheduler.triggers.cron import CronTrigger

from codemie.configs import config, logger
from codemie.repository.application_repository import ApplicationRepository
from codemie.repository.project_cost_tracking_repository import ProjectCostTrackingRepository
from codemie.service.chargeback.spend_collector_service import LiteLLMSpendCollectorService
from codemie.service.conversation_analysis.leader_lock import LeaderLockContext

# Distinct advisory lock ID for the chargeback job — must differ from
# LeaderLockContext.ADVISORY_LOCK_ID (987654321) used by ConversationAnalysisService.
_CHARGEBACK_LOCK_ID = 987654322


class ChargebackScheduler:
    """Scheduler for chargeback-related background jobs.

    Registers the LiteLLM spend collector job when
    LITELLM_SPEND_COLLECTOR_ENABLED is True. Uses the same LeaderLockContext
    advisory-lock pattern as ConversationAnalysisScheduler to ensure only one
    pod runs each job in a multi-replica deployment.
    """

    def __init__(self, scheduler) -> None:
        """
        Args:
            scheduler: APScheduler AsyncIOScheduler instance
        """
        self.scheduler = scheduler

        tracking_repository = ProjectCostTrackingRepository()
        self._spend_collector_service = LiteLLMSpendCollectorService(
            app_repository=ApplicationRepository(),
            tracking_repository=tracking_repository,
        )

    def start(self) -> None:
        """Register enabled jobs and start the scheduler."""
        if config.LITELLM_SPEND_COLLECTOR_ENABLED:
            self._register_spend_collector_job()

        if not self.scheduler.running:
            self.scheduler.start()

    def _register_spend_collector_job(self) -> None:
        """Register the LiteLLM spend collector cron job."""
        cron_parts = config.LITELLM_SPEND_COLLECTOR_SCHEDULE.split()
        if len(cron_parts) != 5:
            logger.error(
                f"Invalid LITELLM_SPEND_COLLECTOR_SCHEDULE cron expression: "
                f"{config.LITELLM_SPEND_COLLECTOR_SCHEDULE!r}; skipping job registration"
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
            self._run_spend_collector,
            trigger=trigger,
            id="litellm_spend_collector",
            replace_existing=True,
            name="LiteLLM Spend Collector",
        )
        logger.info(
            f"Registered LiteLLM spend collector job with schedule: "
            f"{config.LITELLM_SPEND_COLLECTOR_SCHEDULE!r} (UTC)"
        )

    async def _run_spend_collector(self) -> None:
        """Wrapper for the spend collector job.

        Uses LeaderLockContext so only one pod runs the collection when
        multiple replicas are deployed. Catches and logs all exceptions to
        prevent APScheduler from suppressing them silently.
        """
        with LeaderLockContext(lock_id=_CHARGEBACK_LOCK_ID) as lock:
            if not lock.acquired:
                logger.info("LiteLLM spend collector: not the leader, skipping")
                return

            try:
                count = await self._spend_collector_service.collect()
                logger.info(f"LiteLLM spend collector completed: {count} rows inserted")
            except Exception as e:
                logger.error(f"LiteLLM spend collector failed: {e}", exc_info=True)

    def stop(self) -> None:
        """Stop the scheduler gracefully."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("ChargebackScheduler stopped")
