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

"""Cross-process locking for scheduled datasource reindex jobs.

APScheduler's ``max_instances=1`` only prevents a job from overlapping itself within a
single process. It says nothing about two pods, which is exactly the window that opens
if the trigger engine's leader lock is ever held by two nodes at once — during a
PostgreSQL failover, say, when the leader's connection dies and its lock is released
server-side while the old leader keeps running.

A PostgreSQL session-level advisory lock keyed on the datasource closes that window.
Whoever gets the lock reindexes; anyone else skips and says so. When a pod dies its
connection closes and PostgreSQL drops the lock, so there is no lease to expire, no
heartbeat to renew and no stale-lock cleanup to run.
"""

from __future__ import annotations

import functools
import hashlib
import threading
from contextlib import contextmanager

from codemie.clients.postgres import PostgresClient
from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.configs import config, logger
from codemie.rest_api.models.index import IndexInfo
from codemie.utils.leader_lock import LeaderLockContext

_lock_engine = None
_lock_engine_guard = threading.Lock()


def _get_lock_engine():
    """Return the dedicated engine job locks take their connections from.

    A session-level lock pins its connection for as long as it is held — minutes, for a
    reindex. The shared application pool is ``PG_POOL_SIZE`` plus SQLAlchemy's default
    overflow, which is no larger than ``CRON_SCHEDULER_MAX_WORKERS``, so drawing lock
    connections from it would let a full slate of scheduled jobs consume every
    connection and then deadlock waiting for one to run their own queries.
    """
    global _lock_engine
    if _lock_engine is None:
        with _lock_engine_guard:
            if _lock_engine is None:
                _lock_engine = PostgresClient.create_dedicated_engine(pool_size=config.CRON_SCHEDULER_MAX_WORKERS)
    return _lock_engine


def datasource_lock_key(datasource_id: str) -> tuple[int, int]:
    """Derive a stable two-part advisory lock key from a datasource id.

    Uses the two-argument advisory lock form, whose key space PostgreSQL keeps separate
    from the single-argument one. Every other lock in this codebase is single-argument,
    so a datasource lock cannot collide with any of them by construction.

    Both halves of a 64-bit BLAKE2b digest are used rather than a namespace plus a
    32-bit hash: with a 32-bit key, a few thousand datasources carry a small but real
    chance of two of them sharing a key, and the consequence would be one healthy
    reindex silently skipped because an unrelated datasource holds "its" lock.

    Python's built-in ``hash()`` is deliberately not used — it is salted per process, so
    two pods would derive different keys for the same datasource and never contend.
    """
    digest = hashlib.blake2b(str(datasource_id).encode("utf-8"), digest_size=8).digest()
    return (
        int.from_bytes(digest[:4], "big", signed=True),
        int.from_bytes(digest[4:], "big", signed=True),
    )


@contextmanager
def datasource_job_lock(datasource_id: str):
    """Yield True when this process holds the reindex lock for ``datasource_id``.

    The lock is released on the way out whether the body returned or raised.
    """
    with LeaderLockContext(lock_key=datasource_lock_key(datasource_id), engine=_get_lock_engine()) as lock:
        yield lock.acquired


def with_datasource_job_lock(func):
    """Run a datasource actor only if no other process is reindexing that datasource.

    Accepts both actor shapes: those taking a reindex payload and those taking an
    ``IndexInfo`` directly. Also refuses to run any actor at all when retrieval is
    unavailable — see the guard in ``wrapper`` for why that check lives here.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Block scheduled and stale reindex work when retrieval is unavailable.
        if not ElasticSearchClient.is_configured():
            logger.warning(
                "Skipping %s - Elasticsearch is not configured (RETRIEVAL_BACKEND=none or ELASTIC_URL unset)",
                func.__name__,
            )
            return None

        # The scheduler dispatches reindex actors with a keyword argument
        # (kwargs={"payload": payload}) while the stale-indexing watchdog calls
        # resume_stale_datasource positionally, so accept both and pass through
        # untouched rather than re-binding the signature.
        arg = args[0] if args else next(iter(kwargs.values()), None)
        index_info = arg if isinstance(arg, IndexInfo) else getattr(arg, "index_info", None)
        datasource_id = getattr(index_info, "id", None)

        if not datasource_id:
            # Losing a reindex over a missing id would be worse than running unlocked:
            # the duplicate this guards against is rare, a skipped reindex is not.
            logger.warning("Datasource job lock skipped - no datasource id on %s payload", func.__name__)
            return func(*args, **kwargs)

        with datasource_job_lock(datasource_id) as acquired:
            if not acquired:
                logger.warning(
                    "Skipping %s - datasource is already being reindexed by another process: datasource_id=%s",
                    func.__name__,
                    datasource_id,
                )
                return None

            logger.info("Datasource job lock acquired: datasource_id=%s job=%s", datasource_id, func.__name__)
            return func(*args, **kwargs)

    return wrapper
