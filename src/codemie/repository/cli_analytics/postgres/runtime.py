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

"""Startup, background jobs and shutdown of the PostgreSQL analytics storage.

The application must start even when the analytics database cannot be reached: a failed
migration is logged, ingest answers 503 (the plugin keeps its spool and retries), and
the maintenance job tries the migration again on every run. The two jobs run on one pod
at a time, chosen by an advisory lock on the analytics database.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from collections.abc import Callable

from codemie.repository.cli_analytics.ports import ScheduledJob
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.maintenance import PartitionMaintainer, RetentionPolicy
from codemie.repository.cli_analytics.postgres.migrations import run_migrations
from codemie.repository.cli_analytics.postgres.rollups import RollupRefresher
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings

logger = logging.getLogger(__name__)

# Alert when queued rollup work is older than this: dashboards are that stale.
STALE_QUEUE_SECONDS = 300
# While the queue stays stale the warning repeats, at most this often.
STALE_WARNING_INTERVAL_SECONDS = 300


def _settle(future: asyncio.Future[None], error: BaseException | None) -> None:
    if future.done():  # the waiting task was cancelled
        return
    if error is None:
        future.set_result(None)
    else:
        future.set_exception(error)


async def _run_in_daemon_thread(fn: Callable[[], None], name: str) -> None:
    """Run a blocking call in a daemon thread and wait for it.

    A migration can wait minutes for another pod's migration lock. The event loop and the
    interpreter join executor threads (asyncio.to_thread) on exit, so a stopping pod would
    wait with it; nothing waits for a daemon thread once its caller is cancelled.
    """
    loop = asyncio.get_running_loop()
    done: asyncio.Future[None] = loop.create_future()

    def run() -> None:
        error: BaseException | None = RuntimeError(f"{name} stopped unexpectedly")
        try:
            fn()
            error = None
        except Exception as exc:
            error = exc
        finally:
            with contextlib.suppress(RuntimeError):  # the loop has closed: nobody is waiting
                loop.call_soon_threadsafe(_settle, done, error)

    threading.Thread(target=run, name=name, daemon=True).start()
    await done


class PostgresAnalyticsRuntime:
    def __init__(
        self,
        engine: AnalyticsPgEngine,
        settings: AnalyticsPgSettings,
        migrate: Callable[[AnalyticsPgSettings], None] = run_migrations,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._migrate = migrate
        self._refresher = self._build_refresher(classify=None)
        self._maintainer = PartitionMaintainer(engine, RetentionPolicy.from_settings(settings))
        self._schema_ready = False
        self._partitions_ready = False  # every DEFAULT partition exists, so ingest has a target
        self._schema_lock = asyncio.Lock()
        self._last_stale_warning: float | None = None

    def _build_refresher(self, classify: Callable[[list[str]], str] | None) -> RollupRefresher:
        # A refresh may run longer than its interval when it has catching up to do, but
        # never much longer, so the leader lock is handed back regularly.
        return RollupRefresher(
            self._engine,
            batch_size=self._settings.rollup_batch_size,
            max_run_seconds=max(60.0, 4 * self._settings.rollup_refresh_seconds),
            raw_retention_days=self._settings.raw_retention_days,
            classify=classify,
        )

    def set_classifier(self, classify: Callable[[list[str]], str] | None) -> None:
        """Hand the refresher the delivery-framework classifier; called before the jobs start."""
        self._refresher = self._build_refresher(classify)

    async def start(self) -> None:
        """Bring the schema up to date and create the partitions ingest needs; never raises."""
        try:
            await self.run_maintenance()
        except Exception:
            logger.exception("cli_analytics: startup maintenance failed; the maintenance job will retry")

    def jobs(self) -> list[ScheduledJob]:
        return [
            ScheduledJob("cli_analytics_rollup_refresh", self._settings.rollup_refresh_seconds, self.refresh_rollups),
            ScheduledJob(
                "cli_analytics_maintenance", self._settings.maintenance_interval_minutes * 60, self.run_maintenance
            ),
        ]

    async def refresh_rollups(self) -> None:
        if not (self._schema_ready and self._partitions_ready):
            # Migration or partitioning failed at startup: retry now, not at the next maintenance.
            try:
                await self.run_maintenance()
            except Exception:
                logger.exception("cli_analytics: maintenance failed before the rollup refresh; refreshing anyway")
            if not self._schema_ready:
                return
        async with self._engine.advisory_lock("rollup-refresher") as conn:
            if conn is not None:
                await self._refresher.refresh(conn)
                self._report_backlog(*await self._refresher.backlog(conn))

    async def run_maintenance(self) -> None:
        if not await self._ensure_schema():
            return
        async with self._engine.advisory_lock("maintenance") as conn:
            if conn is None:
                return
            report = await self._maintainer.run(conn)
            self._partitions_ready = report.defaults_ready
            if report.created or report.dropped:
                logger.info(
                    "cli_analytics: partitions created=%d dropped=%d moved_rows=%d purged_rows=%d",
                    len(report.created),
                    len(report.dropped),
                    report.moved_rows,
                    report.purged_rows,
                )

    def _report_backlog(self, queued: int, oldest: float | None) -> None:
        """Warn while the rollup queue lags behind (a freeze, or ingest outpacing the refresher)."""
        if oldest is None or oldest <= STALE_QUEUE_SECONDS:
            return
        now = time.monotonic()
        if self._last_stale_warning is not None and now - self._last_stale_warning < STALE_WARNING_INTERVAL_SECONDS:
            return
        self._last_stale_warning = now
        logger.warning(
            "cli_analytics: rollup queue has %d keys, the oldest waiting %.0fs; dashboards lag behind",
            queued,
            oldest,
        )

    async def _ensure_schema(self) -> bool:
        async with self._schema_lock:  # startup and the first job may both get here
            if not self._schema_ready:
                try:
                    await _run_in_daemon_thread(lambda: self._migrate(self._settings), "cli-analytics-migrations")
                except Exception:
                    logger.exception("cli_analytics: analytics schema migration failed; the next job retries it")
                    return False
                self._schema_ready = True
        return True

    async def aclose(self) -> None:
        await self._engine.close()
