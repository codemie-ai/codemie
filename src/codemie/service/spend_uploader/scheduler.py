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

from codemie.configs import config, logger
from codemie.enterprise.spend_uploader import (
    build_spend_uploader_service,
    is_litellm_db_configured,
    is_spend_uploader_available,
    verify_litellm_db_connection,
)
from codemie.service.budget_reset.scheduler import _build_cron_trigger


class SpendUploaderScheduler:
    """Scheduler for the LiteLLM spend uploader (enterprise), which feeds the litellm_spend_logs ledger.

    Registers the job when LITELLM_SPEND_UPLOADER_ENABLED is True.
    """

    def __init__(self, scheduler) -> None:
        """
        Args:
            scheduler: APScheduler AsyncIOScheduler instance
        """
        self.scheduler = scheduler

    def start(self) -> None:
        """Register enabled jobs and start the scheduler."""
        if config.LITELLM_SPEND_UPLOADER_ENABLED:
            self._register_spend_uploader_job()

        if not self.scheduler.running:
            self.scheduler.start()

    def _register_spend_uploader_job(self) -> None:
        """Register the LiteLLM spend uploader cron job (enterprise)."""
        if not is_spend_uploader_available():
            logger.warning(
                "LITELLM_SPEND_UPLOADER_ENABLED is set but the installed codemie-enterprise package does not "
                "provide the spend uploader; skipping job registration"
            )
            return
        if not is_litellm_db_configured():
            logger.error(
                "LITELLM_SPEND_UPLOADER_ENABLED is set but the LiteLLM database is not configured "
                "(LITELLM_SPEND_UPLOADER_LITELLM_DB_URL or _DB_HOST/_DB_USER); skipping job registration"
            )
            return
        # Unlike the checks above, not skipped: credentials that cannot log in must stop the backend at startup.
        verify_litellm_db_connection()

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

        Takes no LeaderLockContext: the uploader guards itself with a session-level advisory lock on its own
        connection, so a second pod's tick finds the lock held and returns without reading. The run is blocking,
        so it goes to a worker thread to keep the event loop free. Catches and logs all exceptions to prevent
        APScheduler from suppressing them silently; a failed run resumes from the last committed page on the
        next tick.
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

    def stop(self) -> None:
        """Stop the scheduler gracefully."""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("SpendUploaderScheduler stopped")
