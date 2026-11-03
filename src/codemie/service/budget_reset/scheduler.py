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
from codemie.utils.leader_lock import LeaderLockContext

_BUDGET_RESET_RECONCILIATION_LOCK_ID = 987654324


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


class BudgetResetScheduler:
    """Scheduler for the budget-reset background job.

    Registers the daily budget reset reconciliation job when
    LITELLM_BUDGET_RESET_RECONCILIATION_ENABLED is True. Uses the same
    LeaderLockContext advisory-lock pattern as ConversationAnalysisScheduler to
    ensure only one pod runs each job in a multi-replica deployment.
    """

    def __init__(self, scheduler) -> None:
        """
        Args:
            scheduler: APScheduler AsyncIOScheduler instance
        """
        self.scheduler = scheduler

    def start(self) -> None:
        """Register enabled jobs and start the scheduler."""
        if config.LITELLM_BUDGET_RESET_RECONCILIATION_ENABLED:
            self._register_budget_reset_reconciliation_job()

        if not self.scheduler.running:
            self.scheduler.start()

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

    async def _run_budget_reset_reconciliation(self) -> None:
        """Wrapper for the daily budget reset reconciliation job."""
        from codemie.service.budget.daily_reset_reconciliation_service import (
            daily_budget_reset_reconciliation_service,
        )

        with LeaderLockContext(lock_id=_BUDGET_RESET_RECONCILIATION_LOCK_ID) as lock:
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
            logger.info("BudgetResetScheduler stopped")
