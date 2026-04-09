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

import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class DatasourceConcurrencyManager:
    """
    Per-instance semaphore-based concurrency control for datasource indexing.

    Caps the number of datasources being processed simultaneously on a single backend instance.
    When the limit is reached, excess processing requests block until a slot is freed.
    If an IndexInfo record is provided, it is marked with is_queued=True while waiting,
    giving the frontend visibility into the queued state.
    """

    def __init__(self, max_concurrent: int, enabled: bool) -> None:
        self._semaphore = threading.Semaphore(max_concurrent)
        self._enabled = enabled
        self._max_concurrent = max_concurrent

    def run(self, process_func: Callable, index_info: Optional[object] = None) -> None:
        """
        Execute process_func with concurrency control.

        If the concurrency limit is reached, this method blocks until a slot becomes
        available. When index_info is provided, the datasource is marked as is_queued=True
        in the database while waiting, so the frontend can show the queued status.

        Args:
            process_func: The callable to execute (typically BaseDatasourceProcessor.process).
            index_info: Optional IndexInfo instance. When provided and concurrency is at
                        capacity, the record is marked as queued in the DB until a slot opens.
        """
        if not self._enabled:
            process_func()
            return

        acquired = self._semaphore.acquire(blocking=False)
        if not acquired:
            if index_info is not None:
                logger.info(
                    f"DatasourceConcurrencyManager. Queuing datasource={index_info.repo_name} "
                    f"id={index_info.id}. Max concurrent={self._max_concurrent}"
                )
                try:
                    index_info.set_queued()
                except Exception:
                    logger.warning(
                        f"DatasourceConcurrencyManager. Failed to set queued state for "
                        f"datasource id={index_info.id}",
                        exc_info=True,
                    )
            self._semaphore.acquire(blocking=True)

        try:
            if index_info is not None and not acquired:
                try:
                    index_info.clear_queued()
                except Exception:
                    logger.warning(
                        f"DatasourceConcurrencyManager. Failed to clear queued state for "
                        f"datasource id={index_info.id}",
                        exc_info=True,
                    )
            process_func()
        finally:
            self._semaphore.release()


def _create_manager() -> DatasourceConcurrencyManager:
    from codemie.configs.config import config

    return DatasourceConcurrencyManager(
        max_concurrent=config.MAX_CONCURRENT_DATASOURCE_INDEXING,
        enabled=config.DATASOURCE_CONCURRENCY_LIMIT_ENABLED,
    )


datasource_concurrency_manager: DatasourceConcurrencyManager = _create_manager()
