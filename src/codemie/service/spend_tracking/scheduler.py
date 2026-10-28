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

from apscheduler.triggers.cron import CronTrigger

from codemie.configs import config, logger
from codemie.enterprise.spend_uploader import (
    build_spend_uploader_service,
    get_litellm_db_url,
    is_spend_uploader_available,
)
from codemie.repository.application_repository import ApplicationRepository
from codemie.repository.project_spend_tracking_repository import ProjectSpendTrackingRepository
from codemie.service.spend_tracking.config import SPEND_TRACKING_LOCK_ID
from codemie.service.spend_tracking.spend_collector_service import LiteLLMSpendCollectorService
from codemie.utils.leader_lock import LeaderLockContext

_SPEND_TRACKING_RESET_WINDOW_LOCK_ID = 987654323
_SPEND_TRACKING_RESET_RECONCILIATION_LOCK_ID = 987654324


def _build_cron_trigger(cron_expression: str) -> CronTrigger | None:
    """Return a UTC cron trigger or None when the expression is invalid."""
    cron_parts = cron_expression.split()
    if len(cron_parts) != 5:
        return None

    minute, hour, day, month, day_of_week = cron_parts
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
        timezone="UTC",
    )


class SpendTrackingScheduler:
    """Scheduler for spend-tracking background jobs.

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

        tracking_repository = ProjectSpendTrackingRepository()
        self._spend_collector_service = LiteLLMSpendCollectorService(
            app_repository=ApplicationRepository(),
            tracking_repository=tracking_repository,
        )

    def start(self) -> None:
        """Register enabled jobs and start the scheduler."""
        if config.LITELLM_SPEND_COLLECTOR_ENABLED:
            self._register_spend_collector_job()
        if config.LITELLM_BUDGET_RESET_TRACKER_ENABLED:
            self._register_budget_reset_tracker_job()
        if config.LITELLM_BUDGET_RESET_RECONCILIATION_ENABLED:
            self._register_budget_reset_reconciliation_job()
        if config.LITELLM_SPEND_UPLOADER_ENABLED:
            self._register_spend_uploader_job()

        if not self.scheduler.running:
            self.scheduler.start()

    def _register_spend_collector_job(self) -> None:
        """Register the LiteLLM spend collector cron job."""
        trigger = _build_cron_trigger(config.LITELLM_SPEND_COLLECTOR_SCHEDULE)
        if trigger is None:
            logger.error(
                f"Invalid LITELLM_SPEND_COLLECTOR_SCHEDULE cron expression: "
                f"{config.LITELLM_SPEND_COLLECTOR_SCHEDULE!r}; skipping job registration"
            )
            return

        self.scheduler.add_job(
            self._run_spend_collector,
            trigger=trigger,
            id="litellm_spend_collector",
            replace_existing=True,
            name="LiteLLM Spend Collector",
        )
        logger.info(
            f"Registered LiteLLM spend collector job with schedule: {config.LITELLM_SPEND_COLLECTOR_SCHEDULE!r} (UTC)"
        )

    def _register_budget_reset_tracker_job(self) -> None:
        """Register the member budget reset-window tracker cron job."""
        trigger = _build_cron_trigger(config.LITELLM_BUDGET_RESET_TRACKER_SCHEDULE)
        if trigger is None:
            logger.error(
                f"Invalid LITELLM_BUDGET_RESET_TRACKER_SCHEDULE cron expression: "
                f"{config.LITELLM_BUDGET_RESET_TRACKER_SCHEDULE!r}; skipping job registration"
            )
            return

        self.scheduler.add_job(
            self._run_budget_reset_tracker,
            trigger=trigger,
            id="litellm_budget_reset_tracker",
            replace_existing=True,
            name="LiteLLM Budget Reset Tracker",
        )
        logger.info(
            f"Registered LiteLLM budget reset tracker job with schedule: "
            f"{config.LITELLM_BUDGET_RESET_TRACKER_SCHEDULE!r} (UTC)"
        )

    def _register_budget_reset_reconciliation_job(self) -> None:
        """Register the daily budget reset reconciliation cron job."""
        trigger = _build_cron_trigger(config.LITELLM_BUDGET_RESET_RECONCILIATION_SCHEDULE)
        if trigger is None:
            logger.error(
                "Invalid LITELLM_BUDGET_RESET_RECONCILIATION_SCHEDULE cron expression: "
                f"{config.LITELLM_BUDGET_RESET_RECONCILIATION_SCHEDULE!r}; skipping job registration"
            )
            return

        self.scheduler.add_job(
            self._run_budget_reset_reconciliation,
            trigger=trigger,
            id="litellm_budget_reset_reconciliation",
            replace_existing=True,
            name="LiteLLM Budget Reset Reconciliation",
        )
        logger.info(
            "Registered LiteLLM budget reset reconciliation job with schedule: "
            f"{config.LITELLM_BUDGET_RESET_RECONCILIATION_SCHEDULE!r} (UTC)"
        )

    def _register_spend_uploader_job(self) -> None:
        """Register the LiteLLM spend uploader cron job (enterprise)."""
        if not is_spend_uploader_available():
            logger.warning(
                "LITELLM_SPEND_UPLOADER_ENABLED is set but the installed codemie-enterprise package does not "
                "provide the spend uploader; skipping job registration"
            )
            return
        if not get_litellm_db_url():
            logger.error(
                "LITELLM_SPEND_UPLOADER_ENABLED is set but the LiteLLM database is not configured "
                "(LITELLM_SPEND_UPLOADER_LITELLM_DB_URL or _DB_HOST/_DB_USER); skipping job registration"
            )
            return

        trigger = _build_cron_trigger(config.LITELLM_SPEND_UPLOADER_SCHEDULE)
        if trigger is None:
            logger.error(
                f"Invalid LITELLM_SPEND_UPLOADER_SCHEDULE cron expression: "
                f"{config.LITELLM_SPEND_UPLOADER_SCHEDULE!r}; skipping job registration"
            )
            return

        self.scheduler.add_job(
            self._run_spend_uploader,
            trigger=trigger,
            id="litellm_spend_uploader",
            replace_existing=True,
            name="LiteLLM Spend Uploader",
            # A long run (the first historical load) must not stack ticks behind it.
            max_instances=1,
            coalesce=True,
        )
        logger.info(
            f"Registered LiteLLM spend uploader job with schedule: {config.LITELLM_SPEND_UPLOADER_SCHEDULE!r} (UTC)"
        )

    async def _run_spend_collector(self) -> None:
        """Wrapper for the spend collector job.

        Uses LeaderLockContext so only one pod runs the collection when
        multiple replicas are deployed. Catches and logs all exceptions to
        prevent APScheduler from suppressing them silently.
        """
        with LeaderLockContext(lock_id=SPEND_TRACKING_LOCK_ID) as lock:
            if not lock.acquired:
                logger.info("LiteLLM spend collector: not the leader, skipping")
                return

            try:
                count = await self._spend_collector_service.collect()
                logger.info(f"LiteLLM spend collector completed: {count} rows inserted")
            except Exception as e:
                logger.error(f"LiteLLM spend collector failed: {e}", exc_info=True)

    @staticmethod
    def _build_and_run_spend_uploader():
        """Build the service and run one feed tick; blocking, so call it from a worker thread.

        Building belongs here too: with IAM auth it fetches a token over the network.
        """
        service = build_spend_uploader_service()
        if service is None:
            return None
        return service.run_feed()

    async def _run_spend_uploader(self) -> None:
        """Wrapper for the spend uploader job.

        Unlike the other jobs this one takes no LeaderLockContext: the uploader guards itself with a session-level
        advisory lock on its own connection, so a second pod's tick finds the lock held and returns without reading.
        The run is blocking, so it goes to a worker thread to keep the event loop free. Catches and logs all
        exceptions to prevent APScheduler from suppressing them silently; a failed run resumes from the last
        committed page on the next tick.
        """
        try:
            summary = await asyncio.to_thread(self._build_and_run_spend_uploader)
        except Exception as e:
            logger.error(f"LiteLLM spend uploader failed: {type(e).__name__}", exc_info=True)
            return

        if summary is None:
            logger.info("LiteLLM spend uploader: nothing to do (unavailable, lock held by another run, or no data)")
            return
        logger.info(
            f"LiteLLM spend uploader completed: rows_read={summary.rows_read}, rows_changed={summary.rows_changed}, "
            f"pages={summary.pages}, elapsed={summary.elapsed_s:.2f}s"
        )

    async def _run_budget_reset_tracker(self) -> None:
        """Wrapper for the member budget reset-window tracker job."""
        with LeaderLockContext(lock_id=_SPEND_TRACKING_RESET_WINDOW_LOCK_ID) as lock:
            if not lock.acquired:
                logger.info("LiteLLM budget reset tracker: not the leader, skipping")
                return

            try:
                count = await self._spend_collector_service.collect_member_budget_reset_window()
                logger.info(f"LiteLLM budget reset tracker completed: {count} rows inserted")
            except Exception as e:
                logger.error(f"LiteLLM budget reset tracker failed: {e}", exc_info=True)

    async def _run_budget_reset_reconciliation(self) -> None:
        """Wrapper for the daily budget reset reconciliation job."""
        from codemie.service.budget.daily_reset_reconciliation_service import (
            daily_budget_reset_reconciliation_service,
        )

        with LeaderLockContext(lock_id=_SPEND_TRACKING_RESET_RECONCILIATION_LOCK_ID) as lock:
            if not lock.acquired:
                logger.info("LiteLLM budget reset reconciliation: not the leader, skipping")
                return

            try:
                summary = await daily_budget_reset_reconciliation_service.run()
                logger.info(
                    "LiteLLM budget reset reconciliation completed: "
                    f"updated={summary.updated}, failed={summary.failed}, "
                    f"mismatch_warnings={summary.mismatch_warnings}"
                )
            except Exception as e:
                logger.error(f"LiteLLM budget reset reconciliation failed: {e}", exc_info=True)

    def stop(self) -> None:
        """Stop the scheduler gracefully."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("SpendTrackingScheduler stopped")
