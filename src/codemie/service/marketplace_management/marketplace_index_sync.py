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

"""Reliable, *observable* writes into the marketplace search index.

EXECUTION-PLAN 9.6 — traces F-87, F-27, and §7 F7 of design-persistence.md.

Measured on 2026-08-23, before this module existed:

    select count(*) from codemie.assistants where is_global   ->  219
    curl -s localhost:9200/marketplace_assistants/_count      ->   19

Two mechanisms, and they are not the same kind of problem:

1. **Config.** `PLATFORM_DATASOURCES_SYNC_ENABLED` is `False`
   (`configs/config.py:703`), so `PlatformIndexingService.sync_all_platform_datasources`
   never runs at startup (`rest_api/main.py:355`). With no full sync, the only
   writer into the index is the per-publish incremental call, so an assistant
   that became `is_global` any other way — seeded, migrated, or backfilled
   straight into Postgres — is absent by construction. All 19 documents present
   were published through the API, which is the signature of exactly that.
   The fix for this half is to *run the backfill*
   (`POST /v1/admin/marketplace/reindex`), not to change code.

2. **Code.** Every incremental call site swallowed its exception and reported
   success anyway: `queue_service._side_effects` caught `Exception` and returned
   `None`, and `routers/assistant.py` scheduled `_index_marketplace_assistant`
   as a fire-and-forget BackgroundTask that also caught everything. So a failed
   index and a successful one produced the same clean 200, and nothing anywhere
   published the two counts side by side — which is why a 200-document gap could
   persist unnoticed. **That** half is a defect, and this module is its fix.

What this module adds:

* a bounded retry around each single-document operation, because most index
  failures are transient and losing an assistant's marketplace listing to one
  connection reset is not an acceptable outcome;
* an `IndexingOutcome` the caller can put in its response body, so "indexed" and
  "we tried to index" stop being the same answer;
* `measure_index_divergence()`, which publishes both counts as a metric so the
  gap can never again exist without being visible.

The retry queue is derived, not stored: an assistant with `is_global = true` and
no document in the index *is* the backlog, `measure_index_divergence()` is its
readable status, and `POST /v1/admin/marketplace/reindex` drains it. That needs
no new table and, unlike a queue, cannot itself drift out of sync with reality.
"""

from __future__ import annotations

import hashlib
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator, Optional

from codemie.configs import config, logger

# One retry, not five: the caller is inside a request (approve/withdraw) or a
# background task that a user is waiting on the effects of. Two attempts covers
# the connection-reset case that dominates, without turning a dead
# Elasticsearch into a multi-second stall on every moderation action.
INDEX_ATTEMPTS = 2
INDEX_RETRY_BACKOFF_SECONDS = 0.25

METRIC_INDEX_FAILED = "marketplace_index_failed"
METRIC_INDEX_DIVERGENCE = "marketplace_index_divergence"


@dataclass(frozen=True)
class IndexingOutcome:
    """What actually happened to the search-index projection of one entity."""

    # What was actually done to the index -- which is not always what the
    # caller asked for. A background index write that arrives after the
    # assistant has been unpublished is executed as a *removal*, because the
    # committed row is what "should be in the marketplace" means.
    action: str  # "index" | "remove"
    state: str  # "OK" | "FAILED"
    attempts: int = 1
    error: Optional[str] = None
    # True when `action` differs from the caller's request because Postgres had
    # moved on. Not an error -- the opposite: it is the ordering guarantee
    # working. Reported so a superseded write is attributable rather than
    # merely invisible.
    superseded: bool = False

    @property
    def ok(self) -> bool:
        return self.state == "OK"

    def mark_superseded(self) -> "IndexingOutcome":
        return IndexingOutcome(
            action=self.action,
            state=self.state,
            attempts=self.attempts,
            error=self.error,
            superseded=True,
        )

    def as_dict(self) -> dict[str, Any]:
        """The shape that goes into an API response body.

        `superseded` appears only when it is true, the same way `_result_body`
        in `queue_service` omits `override_notice` and `submitted_at` when they
        do not apply. An ordinary index or removal therefore produces exactly
        the body it produced before this rule existed.
        """
        body = {
            "action": self.action,
            "state": self.state,
            "attempts": self.attempts,
            "error": self.error,
        }
        if self.superseded:
            body["superseded"] = True
        return body


def _emit_failure_metric(*, action: str, entity_id: str, error: str, attempts: int) -> None:
    """Publish the failure. Best-effort: observability must not raise."""
    try:
        from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService

        BaseMonitoringService.send_count_metric(
            name=METRIC_INDEX_FAILED,
            description="Marketplace search-index write that did not succeed",
            attributes={
                "action": action,
                "assistant_id": entity_id,
                "attempts": attempts,
                "error": error[:200],
            },
        )
    except Exception:  # noqa: BLE001
        logger.warning("Could not emit marketplace index failure metric", exc_info=True)


def _is_already_absent(exc: BaseException) -> bool:
    """True when *exc* says the document is not in the index.

    De-indexing states a desired end condition -- "this id must not be in the
    index" -- rather than performing a transaction, and Elasticsearch answering
    404 means that condition already holds. Treating it as a failure produced
    two tracebacks, a backoff sleep inside a moderation request, and a FAILED
    outcome, all to report success by another name.

    Duck-typed rather than `except NotFoundError`: this module is imported on
    paths where the Elasticsearch client may not be installed, and the same 404
    reaches us wrapped differently depending on which layer raised it. Any one
    of the three signatures below is conclusive; none of them can be produced by
    a connection error, a mapping conflict, or an auth failure, which are the
    cases that must keep failing.
    """
    if type(exc).__name__ == "NotFoundError":
        return True
    if getattr(exc, "status_code", None) == 404:
        return True
    meta = getattr(exc, "meta", None)
    return getattr(meta, "status", None) == 404


def _run_with_retry(*, action: str, entity_id: str, call, absent_is_success: bool = False) -> IndexingOutcome:
    """Call *call* up to `INDEX_ATTEMPTS` times and report what happened.

    Never raises. The caller's decision has already committed and must not be
    undone by a search index — but it must also not be reported as complete
    when it is not, which is the entire content of F-87.
    """
    last_error: Optional[BaseException] = None
    for attempt in range(1, INDEX_ATTEMPTS + 1):
        try:
            call()
            if attempt > 1:
                logger.info(
                    "marketplace %s for %s succeeded on attempt %s",
                    action,
                    entity_id,
                    attempt,
                )
            return IndexingOutcome(action=action, state="OK", attempts=attempt)
        except Exception as exc:  # noqa: BLE001 - reported, never re-raised
            if absent_is_success and _is_already_absent(exc):
                # Not a failure and not worth a WARNING: the document the
                # caller wanted gone is gone. Retrying cannot improve on it.
                logger.debug(
                    "marketplace %s for %s: document already absent from the index",
                    action,
                    entity_id,
                )
                return IndexingOutcome(action=action, state="OK", attempts=attempt)
            last_error = exc
            logger.warning(
                "marketplace %s attempt %s/%s failed for %s: %s",
                action,
                attempt,
                INDEX_ATTEMPTS,
                entity_id,
                exc,
            )
            if attempt < INDEX_ATTEMPTS:
                time.sleep(INDEX_RETRY_BACKOFF_SECONDS)

    error = f"{type(last_error).__name__}: {last_error}"
    # f-string rather than %-args: this project's JSON log formatter folds
    # `exc_info` into the message and then applies the args to *that*, so the
    # two together raise "not all arguments converted during string formatting"
    # and the failure report is itself lost.
    logger.error(
        f"marketplace {action} failed for {entity_id} after {INDEX_ATTEMPTS} attempts: {error}",
        extra={"entity_id": entity_id, "action": action, "attempts": INDEX_ATTEMPTS},
    )
    _emit_failure_metric(action=action, entity_id=entity_id, error=error, attempts=INDEX_ATTEMPTS)
    return IndexingOutcome(
        action=action,
        state="FAILED",
        attempts=INDEX_ATTEMPTS,
        error=error,
    )


def _import_platform_indexing_service():
    """The indexing back end, imported late and through one named seam.

    Deferred because `platform_indexing_service` pulls in the datasource stack,
    which imports this module back; named because both call sites below need the
    same object and tests need somewhere to stand.
    """
    from codemie.service.platform.platform_indexing_service import PlatformIndexingService

    return PlatformIndexingService


# ---------------------------------------------------------------------------
# Ordering: the committed row decides, and one id is written at a time (13.1)
# ---------------------------------------------------------------------------
#
# Every write into this index is asynchronous with respect to the request that
# caused it. `routers/assistant.py` hands publish, update and unpublish to
# `BackgroundTasks`, which runs sync callables in a threadpool, so two
# consecutive HTTP calls on the same assistant produce two *unordered* threads.
# publish -> unpublish is the case that bites: the removal 404s (correctly
# reported OK since 12.3/D4), and the losing index write lands a second later
# and re-creates the document. Measured 2026-08-25: 226 documents against 219
# rows with `is_global = true`.
#
# Three ways to make that safe were on the table.
#
# * **Sequence the writes per assistant id.** Necessary but not sufficient on
#   its own: serialising two writes does not say which of them should win, and
#   the loser here is the one that arrives *last*.
# * **A version/epoch on the index write.** Elasticsearch supports it natively
#   (`version_type=external`), and it is the textbook answer -- but there is no
#   monotonic version to use. `marketplace_version` is a moderation counter that
#   does not move on unpublish or delete, so it would need a new column, a
#   migration, and `index_single_entity` in the shared platform processor
#   threading a version through for one caller. It also only protects for
#   `index.gc_deletes` (60s by default), after which the delete tombstone is
#   collected and a stale write can land again.
# * **Make the committed row authoritative.** `is_global` in Postgres already
#   *is* the definition of "belongs in the marketplace index" -- it is what
#   `measure_index_divergence()` counts, what the loader selects, and what
#   `_prune_stale_documents` reconciles against. Nothing new has to be invented
#   or migrated for it to be the arbiter.
#
# So: the third, with the first as its mechanism. Each write re-reads the
# committed row at the moment it executes and converges the index on it,
# holding a lock on that assistant id across the read and the write so the two
# cannot interleave. A stale index write becomes a removal; a stale removal
# becomes a no-op. It is correct in *both* directions, which a "delete always
# wins" rule is not -- unpublish -> publish would lose the republished document.
#
# The lock is a Postgres transaction-scoped advisory lock, taken on the same
# session as the truth read. Advisory locks are per-connection, so it serialises
# threads inside one worker and processes across a whole deployment with one
# mechanism; `postgres.py` and `utils/leader_lock.py` already use the same
# primitive, so this is the house pattern rather than an imported one. A
# process-local lock is taken as well, because it is the only one that still
# works when Postgres is the thing that is unreachable.
#
# When the row cannot be read at all, the caller's request is executed
# unchanged. An unknown truth must not be allowed to invent a removal.

_LOCAL_LOCKS: dict[str, threading.Lock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()

# Own half of the two-argument advisory lock key space, so these locks cannot
# collide with the trigger engine's or the migration coordinator's.
_ADVISORY_LOCK_NAMESPACE = 0x6D6B_7069  # "mkpi": marketplace index


def _advisory_key(assistant_id: str) -> int:
    """A stable 32-bit lock key for one assistant id."""
    digest = hashlib.sha256(assistant_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], byteorder="big", signed=True)


def _local_lock(assistant_id: str) -> threading.Lock:
    with _LOCAL_LOCKS_GUARD:
        # Bounded by the number of assistants touched in this process's
        # lifetime; a Lock is ~40 bytes and moderation traffic is human-paced,
        # so this cannot grow to anything worth evicting.
        return _LOCAL_LOCKS.setdefault(assistant_id, threading.Lock())


def _read_committed_state(session, assistant_id: str) -> Optional[bool]:
    """`is_global` for *assistant_id*, or None when the answer is not usable.

    False when the row is gone: a deleted assistant must not be in the
    marketplace index, so "no row" and "not published" are deliberately the
    same answer -- that equivalence is what takes the seven orphaned documents
    out of the index and keeps them out.

    The type check is not ceremony. This value decides whether a document is
    written or deleted, and `bool(x)` is true for almost every object there is,
    including the stand-ins a session may hand back when it is not really
    connected to anything. `measure_index_divergence` already refuses to report
    an unknown count as zero for the same reason; the same rule applies with
    more force here, because guessing wrong deletes a live marketplace listing.
    An unusable read means "no opinion", and the caller does what it was asked.
    """
    from sqlalchemy import text as sa_text

    row = session.execute(
        sa_text("SELECT is_global FROM codemie.assistants WHERE id = :id"),
        {"id": assistant_id},
    ).first()
    if row is None:
        return False
    value = row[0]
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    logger.debug(
        "is_global for %s came back as %r (%s), which is not a boolean; treating the committed state as unknown",
        assistant_id,
        value,
        type(value).__name__,
    )
    return None


@contextmanager
def _committed_state(assistant_id: str) -> Iterator[Optional[bool]]:
    """Hold a lock on *assistant_id* and yield what Postgres says about it.

    Yields True (published), False (not published, or deleted), or None when
    Postgres could not be consulted -- in which case no lock is held either and
    the caller must fall back to doing exactly what it was asked to do.
    """
    from sqlalchemy import text as sa_text

    from codemie.clients.postgres import get_session

    with _local_lock(assistant_id):
        # Acquiring the lock and reading the row is the only part that may fail
        # into the degraded path. An exception raised by the *body* must
        # propagate, not be mistaken for an unreadable database and answered
        # with a second yield.
        session_ctx = None
        try:
            session_ctx = get_session()
            session = session_ctx.__enter__()
            session.execute(
                sa_text("SELECT pg_advisory_xact_lock(:ns, :key)"),
                {"ns": _ADVISORY_LOCK_NAMESPACE, "key": _advisory_key(assistant_id)},
            )
            published = _read_committed_state(session, assistant_id)
        except Exception:  # noqa: BLE001 - degraded, never fatal
            logger.warning(
                "Could not read the committed state of assistant %s; the "
                "marketplace index write will be applied as requested",
                assistant_id,
                exc_info=True,
            )
            if session_ctx is not None:
                try:
                    session_ctx.__exit__(None, None, None)
                except Exception:  # noqa: BLE001
                    logger.debug("Could not close the state-read session", exc_info=True)
            yield None
            return

        try:
            # The lock is held for the whole yield: the Elasticsearch write
            # happens inside this transaction's lifetime, which is the entire
            # point. Nothing is written to Postgres, so the transaction is
            # rolled back rather than committed.
            yield published
        finally:
            try:
                session.rollback()
            finally:
                session_ctx.__exit__(None, None, None)


def _sync(
    assistant_id: str,
    *,
    requested: str,
    assistant_name: Optional[str] = None,
    user: Any = None,
    is_update: bool = True,
) -> IndexingOutcome:
    """Converge the index for one assistant on the committed row."""
    service = _import_platform_indexing_service()

    with _committed_state(assistant_id) as published:
        # CR-017: when DB is unreachable (published is None) an 'index' request
        # cannot be verified against the committed row. Applying it blindly risks
        # writing stale data into the index. Skip the write and emit a WARNING so
        # the caller knows the index was not updated. 'remove' is still safe to
        # execute in the degraded path (removing an unknown assistant cannot
        # surface bad data), matching the reasoning in the docstring above.
        if published is None and requested == "index":
            logger.warning(
                "marketplace index skipped for %s: Postgres could not be consulted; "
                "skipping ES write to avoid indexing data whose published state is unknown. "
                "Re-run POST /v1/admin/marketplace/reindex once the database is reachable.",
                assistant_id,
            )
            return IndexingOutcome(
                action="index",
                state="SKIPPED",
                attempts=0,
                error="DB unreachable; index write skipped to avoid stale data",
            )

        action = _converged_action(requested, published)
        superseded = action != requested
        if superseded:
            logger.info(
                "marketplace %s for %s superseded by the committed row (is_global=%s); applying %s instead",
                requested,
                assistant_id,
                published,
                action,
            )

        if action == "index":
            outcome = _run_with_retry(
                action="index",
                entity_id=assistant_id,
                call=lambda: service.index_single_assistant(assistant_id, user=user, is_update=is_update),
            )
        else:
            outcome = _run_with_retry(
                action="remove",
                entity_id=assistant_id,
                call=lambda: service.remove_single_assistant(assistant_id, assistant_name or assistant_id, user=user),
                # Removal is idempotent: an id that is not in the index is the
                # outcome the caller asked for, so a 404 is success on the
                # first attempt.
                absent_is_success=True,
            )

    return outcome.mark_superseded() if superseded else outcome


def _converged_action(requested: str, published: Optional[bool]) -> str:
    """What the index should converge on: the committed row wins; unknown state keeps the request."""
    if published is None:
        return requested
    return "index" if published else "remove"


def index_assistant(assistant_id: str, *, user: Any = None, is_update: bool = True) -> IndexingOutcome:
    """Put one assistant into the marketplace index, retrying once.

    Downgrades itself to a removal when the committed row says the assistant is
    no longer published -- see the ordering note above. Callers must therefore
    commit their intent to Postgres *before* calling this.
    """
    return _sync(assistant_id, requested="index", user=user, is_update=is_update)


def remove_assistant(assistant_id: str, assistant_name: Optional[str] = None, *, user: Any = None) -> IndexingOutcome:
    """Take one assistant out of the marketplace index, retrying once.

    Downgrades itself to an index write when the committed row says the
    assistant is published again, so a removal that lost its race cannot delete
    a live listing. Callers must commit their intent to Postgres first; on the
    delete path that means deleting the row *before* calling this.
    """
    return _sync(assistant_id, requested="remove", assistant_name=assistant_name, user=user)


# ---------------------------------------------------------------------------
# The divergence
# ---------------------------------------------------------------------------


def _count_published_assistants() -> Optional[int]:
    """`select count(*) from assistants where is_global` — the source of truth."""
    from sqlalchemy import text as sa_text

    from codemie.clients.postgres import get_session

    try:
        with get_session() as session:
            return int(session.execute(sa_text("SELECT count(*) FROM codemie.assistants WHERE is_global")).scalar_one())
    except Exception:  # noqa: BLE001
        logger.warning("Could not count published assistants", exc_info=True)
        return None


def _count_indexed_documents() -> Optional[int]:
    """`_count` on the marketplace index — the projection."""
    from codemie.service.marketplace_management.es_similarity import (
        marketplace_assistants_index as _es_index,
    )

    return _es_index.doc_count()


def measure_index_divergence(*, emit: bool = True) -> dict[str, Any]:
    """Both counts, side by side, optionally as a metric.

    This is the number that makes F-87 impossible to reintroduce quietly. It is
    cheap — one indexed count in Postgres, one `_count` in Elasticsearch — so it
    is emitted after every audit run and after every admin reindex.

    Divergence is counted in **both** directions. `missing` is a published
    assistant with no document; `surplus` is a document with no published
    assistant -- a listing the marketplace still serves for something that was
    unpublished or deleted. The two are the same defect pointing opposite ways
    and only one of them used to be counted.

    A store that cannot be counted yields `None` for its side and `None` for
    both differences: an unknown divergence must not be reported as zero.
    """
    published = _count_published_assistants()
    indexed = _count_indexed_documents()
    both_known = published is not None and indexed is not None
    missing = max(published - indexed, 0) if both_known else None
    # Counted separately, because `missing` alone cannot see this defect at
    # all. It is a one-sided `max(published - indexed, 0)`, so an index holding
    # *too many* documents reads as a clean zero -- which is how seven listings
    # for deleted assistants sat in the index while the number that exists to
    # make divergence impossible to miss said everything was fine (13.1).
    surplus = max(indexed - published, 0) if both_known else None
    divergence = {
        "published": published,
        "indexed": indexed,
        "missing": missing,
        "surplus": surplus,
        "index_name": config.PLATFORM_MARKETPLACE_DATASOURCE_NAME,
    }

    if emit:
        try:
            from codemie.service.monitoring.base_monitoring_service import (
                BaseMonitoringService,
            )

            BaseMonitoringService.send_count_metric(
                name=METRIC_INDEX_DIVERGENCE,
                description="Published assistants absent from the marketplace search index",
                attributes={
                    "published_count": published,
                    "indexed_count": indexed,
                    "missing_count": missing,
                    "surplus_count": surplus,
                    "index_name": divergence["index_name"],
                },
                count=(missing or 0) + (surplus or 0),
            )
        except Exception:  # noqa: BLE001
            logger.warning("Could not emit marketplace index divergence metric", exc_info=True)

    if missing:
        logger.warning(
            "[marketplace_index] %s of %s published assistants are missing from %s; "
            "run POST /v1/admin/marketplace/reindex to backfill",
            missing,
            published,
            divergence["index_name"],
        )
    if surplus:
        logger.warning(
            "[marketplace_index] %s holds %s documents against %s published "
            "assistants: %s listing(s) belong to assistants that are unpublished "
            "or deleted and are still being served; run "
            "POST /v1/admin/marketplace/reindex to reconcile",
            divergence["index_name"],
            indexed,
            published,
            surplus,
        )
    return divergence
