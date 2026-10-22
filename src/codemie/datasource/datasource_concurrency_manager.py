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

import contextlib
import logging
import threading
import uuid
from typing import Callable, Optional

logger = logging.getLogger(__name__)


_QUEUE_TIMEOUT_ERROR = "Datasource indexing queue timeout exceeded. Please rerun datasource indexing."


class DatasourceConcurrencyManager:
    """
    Per-instance semaphore-based concurrency control for datasource indexing.

    Caps the number of datasources being processed simultaneously on a single backend instance.
    When the limit is reached, excess processing requests block until a slot is freed or the
    queue timeout expires. If an IndexInfo record is provided, it is marked with is_queued=True
    while waiting, giving the frontend visibility into the queued state. On timeout the record
    is marked with an error so the user knows to rerun indexing.
    """

    def __init__(self, max_concurrent: int, enabled: bool, queue_timeout: int = 0) -> None:
        self._semaphore = threading.Semaphore(max_concurrent)
        self._enabled = enabled
        self._max_concurrent = max_concurrent
        # 0 or negative means no timeout (block indefinitely)
        self._queue_timeout: float | None = float(queue_timeout) if queue_timeout > 0 else None

    def _try_set_queued(self, index_info: Optional[object]) -> bool:
        """Mark index_info as queued. Returns True if the state was successfully set."""
        if index_info is None:
            return False
        logger.info(
            f"DatasourceConcurrencyManager. Queuing datasource={index_info.repo_name} "
            f"id={index_info.id}. Max concurrent={self._max_concurrent}, "
            f"queue_timeout={self._queue_timeout}s"
        )
        try:
            index_info.set_queued()
            return True
        except Exception:
            logger.warning(
                f"DatasourceConcurrencyManager. Failed to set queued state for datasource id={index_info.id}",
                exc_info=True,
            )
            return False

    def _try_clear_queued(self, index_info: Optional[object]) -> None:
        """Clear the queued state on index_info, logging on failure."""
        if index_info is None:
            return
        try:
            index_info.clear_queued()
        except Exception:
            logger.warning(
                f"DatasourceConcurrencyManager. Failed to clear queued state for datasource id={index_info.id}",
                exc_info=True,
            )

    def _try_set_error(self, index_info: Optional[object], message: str) -> None:
        """Mark index_info with an error, logging on failure."""
        if index_info is None:
            return
        try:
            index_info.set_error(message)
        except Exception:
            logger.warning(
                f"DatasourceConcurrencyManager. Failed to set error state for datasource id={index_info.id}",
                exc_info=True,
            )

    @staticmethod
    def _resolve_datasource_name(index_info: Optional[object], datasource_name: Optional[str]) -> str:
        return str(datasource_name or getattr(index_info, 'repo_name', None) or 'unknown')

    @staticmethod
    def _slot_context(slot: str, datasource_id: str, datasource: str, operation: str) -> str:
        return f"slot={slot} datasource_id={datasource_id} datasource={datasource} operation={operation}"

    def run(
        self,
        process_func: Callable,
        index_info: Optional[object] = None,
        *,
        datasource_name: Optional[str] = None,
    ) -> None:
        """
        Execute process_func with concurrency control.

        If the concurrency limit is reached, this method blocks until a slot becomes
        available or the queue timeout expires. When index_info is provided, the datasource
        is marked as is_queued=True in the database while waiting, so the frontend can show
        the queued status. If the timeout expires before a slot opens, the datasource is
        marked with an error and process_func is NOT executed.

        Args:
            process_func: The callable to execute (typically BaseDatasourceProcessor.process).
            index_info: Optional IndexInfo instance. When provided and concurrency is at
                        capacity, the record is marked as queued in the DB until a slot opens.
            datasource_name: Optional display name used in slot logs. Falls back to
                        index_info.repo_name when omitted.
        """
        if not self._enabled:
            process_func()
            return

        resolved_name = self._resolve_datasource_name(index_info, datasource_name)
        acquired = self._semaphore.acquire(blocking=False)
        queued_set = False
        if not acquired:
            queued_set = self._try_set_queued(index_info)
            acquired = self._semaphore.acquire(blocking=True, timeout=self._queue_timeout)
            if not acquired:
                logger.error(
                    f"DatasourceConcurrencyManager. Queue timeout exceeded for "
                    f"datasource={resolved_name} "
                    f"id={getattr(index_info, 'id', 'unknown')} "
                    f"after {self._queue_timeout}s"
                )
                self._try_set_error(index_info, _QUEUE_TIMEOUT_ERROR)
                return

        slot_context: Optional[str] = None
        try:
            slot_context = self._slot_context(
                slot=uuid.uuid4().hex[:8],
                datasource_id=str(getattr(index_info, 'id', None) or 'unknown'),
                datasource=resolved_name,
                operation=str(getattr(process_func, '__name__', None) or type(process_func).__name__),
            )
            logger.info(f"DatasourceConcurrencyManager. SlotAcquired. {slot_context}")
            if queued_set:
                self._try_clear_queued(index_info)
            process_func()
        finally:
            self._semaphore.release()
            self._log_slot_released(slot_context)

    @staticmethod
    def _log_slot_released(slot_context: Optional[str]) -> None:
        if slot_context is None:
            return
        with contextlib.suppress(Exception):
            logger.info(f"DatasourceConcurrencyManager. SlotReleased. {slot_context}")


def _create_manager() -> DatasourceConcurrencyManager:
    from codemie.configs.config import config

    return DatasourceConcurrencyManager(
        max_concurrent=config.MAX_CONCURRENT_DATASOURCE_INDEXING,
        enabled=config.DATASOURCE_CONCURRENCY_LIMIT_ENABLED,
        queue_timeout=config.DATASOURCE_QUEUE_TIMEOUT,
    )


datasource_concurrency_manager: DatasourceConcurrencyManager = _create_manager()
