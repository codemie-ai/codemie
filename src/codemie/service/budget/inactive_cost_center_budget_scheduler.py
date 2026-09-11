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
from codemie.service.budget.inactive_cost_center_budget_stop_service import (
    inactive_cost_center_budget_stop_service,
)
from codemie.utils.leader_lock import LeaderLockContext

# Full registry: CA=987654321, Spend=987654322/323/324, LB=987654325, InactiveBudget=987654326,
# InactiveCCBudget=987654327
_INACTIVE_COST_CENTER_BUDGET_STOP_LOCK_ID = 987654327


class InactiveCostCenterBudgetScheduler:
    """Scheduler for the inactive cost center budget stop job."""

    def __init__(self, scheduler) -> None:
        self.scheduler = scheduler

    def start(self) -> None:
        if config.BUDGET_STOP_ENABLED:
            self._register_inactive_cost_center_budget_stop_job()

        if not self.scheduler.running:
            self.scheduler.start()

    def _register_inactive_cost_center_budget_stop_job(self) -> None:
        trigger = build_cron_trigger(config.INACTIVE_COST_CENTER_BUDGET_STOP_SCHEDULE)
        if trigger is None:
            logger.error(
                f"Invalid INACTIVE_COST_CENTER_BUDGET_STOP_SCHEDULE cron expression: "
                f"{config.INACTIVE_COST_CENTER_BUDGET_STOP_SCHEDULE!r}; skipping job registration"
            )
            return

        self.scheduler.add_job(
            self._run_inactive_cost_center_budget_stop,
            trigger=trigger,
            id="inactive_cost_center_budget_stop",
            replace_existing=True,
            name="Inactive Cost Center Budget Stop",
        )
        logger.info(
            f"Registered inactive cost center budget stop job with schedule: "
            f"{config.INACTIVE_COST_CENTER_BUDGET_STOP_SCHEDULE!r} (UTC)"
        )

    async def _run_inactive_cost_center_budget_stop(self) -> None:
        with LeaderLockContext(lock_id=_INACTIVE_COST_CENTER_BUDGET_STOP_LOCK_ID) as lock:
            if not lock.acquired:
                logger.info("Inactive cost center budget stop: not the leader, skipping")
                return

            try:
                async with get_async_session() as session:
                    await inactive_cost_center_budget_stop_service.run(session)
                logger.info("Inactive cost center budget stop completed")
            except Exception as e:
                logger.error(f"Inactive cost center budget stop failed: {e}", exc_info=True)

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("InactiveCostCenterBudgetScheduler stopped")
