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

from codemie.clients.postgres import get_async_session
from codemie.configs import config, logger
from codemie.service.budget.scheduler_utils import build_cron_trigger
from codemie.service.budget.active_project_budget_restore_service import (
    active_project_budget_restore_service,
)
from codemie.utils.leader_lock import LeaderLockContext

# Full registry: CA=987654321, Spend=987654322/323/324, LB=987654325, InactiveBudget=987654326,
# InactiveCCBudget=987654327, ActiveBudgetRestore=987654328
_ACTIVE_PROJECT_BUDGET_RESTORE_LOCK_ID = 987654328


class ActiveProjectBudgetScheduler:
    """Scheduler for the active project budget restore job."""

    def __init__(self, scheduler) -> None:
        self.scheduler = scheduler

    def start(self) -> None:
        if config.BUDGET_STOP_ENABLED:
            self._register_active_project_budget_restore_job()

        if not self.scheduler.running:
            self.scheduler.start()

    def _register_active_project_budget_restore_job(self) -> None:
        trigger = build_cron_trigger(config.ACTIVE_PROJECT_BUDGET_RESTORE_SCHEDULE)
        if trigger is None:
            logger.error(
                f"Invalid ACTIVE_PROJECT_BUDGET_RESTORE_SCHEDULE cron expression: "
                f"{config.ACTIVE_PROJECT_BUDGET_RESTORE_SCHEDULE!r}; skipping job registration"
            )
            return

        self.scheduler.add_job(
            self._run_active_project_budget_restore,
            trigger=trigger,
            id="active_project_budget_restore",
            replace_existing=True,
            name="Active Project Budget Restore",
        )
        logger.info(
            f"Registered active project budget restore job with schedule: "
            f"{config.ACTIVE_PROJECT_BUDGET_RESTORE_SCHEDULE!r} (UTC)"
        )

    async def _run_active_project_budget_restore(self) -> None:
        with LeaderLockContext(lock_id=_ACTIVE_PROJECT_BUDGET_RESTORE_LOCK_ID) as lock:
            if not lock.acquired:
                logger.info("Active project budget restore: not the leader, skipping")
                return

            try:
                async with get_async_session() as session:
                    await active_project_budget_restore_service.run(session)
                logger.info("Active project budget restore completed")
            except Exception as e:
                logger.error(f"Active project budget restore failed: {e}", exc_info=True)

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("ActiveProjectBudgetScheduler stopped")
