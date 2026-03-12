# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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
from codemie.service.conversation_analysis.conversation_analysis_service import ConversationAnalysisService


class ConversationAnalysisScheduler:
    """Scheduler for conversation analysis job"""

    def __init__(self, scheduler):
        """
        Args:
            scheduler: APScheduler AsyncIOScheduler instance
        """
        self.scheduler = scheduler
        self.analysis_service = ConversationAnalysisService()
        self.background_processor_task = None

    def start(self):
        """Start scheduled job and background processor"""
        if not config.CONVERSATION_ANALYSIS_ENABLED:
            logger.info("Conversation analysis is disabled, skipping scheduler setup")
            return

        # Parse cron expression
        cron_parts = config.CONVERSATION_ANALYSIS_SCHEDULE.split()
        if len(cron_parts) != 5:
            logger.error(f"Invalid cron expression: {config.CONVERSATION_ANALYSIS_SCHEDULE}")
            return

        minute, hour, day, month, day_of_week = cron_parts

        # Add scheduled job (leader election + queue population)
        cron_trigger = CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=day_of_week)

        self.scheduler.add_job(
            self._run_analysis_job,
            trigger=cron_trigger,
            id="conversation_analysis_job",
            replace_existing=True,
            name="Conversation Analysis - Queue Population",
        )

        # Start the scheduler
        self.scheduler.start()

        logger.info(
            f"Scheduled conversation analysis job with cron: "
            f"{config.CONVERSATION_ANALYSIS_SCHEDULE} "
            f"(start date: {config.CONVERSATION_ANALYSIS_START_DATE})"
        )

        # Start background processor (all pods participate)
        self._start_background_processor()

    def _start_background_processor(self):
        """Start background task that processes queue continuously"""

        async def process_loop():
            """Continuous loop processing queue batches"""
            while True:
                try:
                    result = await self.analysis_service.process_batch()

                    if result["status"] == "no_work":
                        # No work available, wait before checking again
                        await asyncio.sleep(60)  # Check every minute
                    else:
                        # Processed batch, continue immediately
                        await asyncio.sleep(1)  # Small delay to prevent tight loop

                except Exception as e:
                    logger.error(f"Error in conversation analysis background processor: {e}", exc_info=True)
                    await asyncio.sleep(60)  # Wait before retry on error

        # Create background task
        self.background_processor_task = asyncio.create_task(process_loop())
        logger.info("Started conversation analysis background processor")

    async def _run_analysis_job(self):
        """Wrapper for scheduled job execution"""
        try:
            # Use config projects filter for scheduled jobs (None = all projects if config is empty)
            projects_filter = config.CONVERSATION_ANALYSIS_PROJECTS_FILTER or None
            result = await self.analysis_service.schedule_analysis_job(projects=projects_filter)
            logger.info(f"Conversation analysis job result: {result}")
        except Exception as e:
            logger.error(f"Conversation analysis job failed: {e}", exc_info=True)

    def stop(self):
        """Stop scheduler and background processor"""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Stopped conversation analysis scheduler")

        if self.background_processor_task:
            self.background_processor_task.cancel()
            logger.info("Stopped conversation analysis background processor")
