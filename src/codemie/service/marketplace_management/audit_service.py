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

"""Thread-safe audit cache for marketplace quality runs.

AUDIT_CACHE — module-level singleton that callers should use.

Usage::

    from codemie.service.marketplace_management.audit_service import AUDIT_CACHE, EntityKind
    result = AUDIT_CACHE.get_or_run(EntityKind.ASSISTANT)

Validator imports (Engine, load_config)
are deferred to first actual use so that importing this module does not
require optional ML dependencies to be installed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import func, select as sa_select, update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from codemie.clients.postgres import get_session
from codemie.service.marketplace_management import audit_read_repository as audit_reads
from codemie.configs.marketplace_management import (
    get_marketplace_management_config,
)
from codemie.service.marketplace_management.entity_adapter import get_adapter
from codemie.service.marketplace_management.models import (
    AUDIT_ENGINE_VERSION,
    AUDIT_SCHEMA_VERSION,
    Bucket as DBBucket,
    EntityKind,
    MarketplaceAuditFinding,
    MarketplaceAuditRun,
    MarketplaceAuditSimilarity,
    MarketplaceAuditVerdict,
    RunStatus,
    RunTrigger,
)
from codemie.service.marketplace_management.observability import (
    METRIC_AUDIT_RUN,
    track_marketplace_metric,
)

if TYPE_CHECKING:
    # Only used for type hints; not evaluated at runtime.
    from codemie.service.marketplace_management.validator.models import AuditResult
    from codemie.service.marketplace_management.validator.config import Config

logger = logging.getLogger(__name__)

# How many entities are written per transaction. One commit at the end means a
# crash at 95 % stores nothing (design-persistence.md §4.2 point 3); a commit
# per chunk leaves partial-but-consistent children and a truthful
# `records_processed` for the §7 reaper to act on.
PERSIST_CHUNK_SIZE = 500

# Upper bound on similarity rows recorded per entity. The engine can emit a pair
# for every other entity above the report threshold; the UI shows the closest
# few and the verdict cites one. Recording all of them would make this table
# quadratic in catalogue size for evidence nobody reads.
SIMILARITY_ROWS_PER_ENTITY = 10


def _runner_id() -> str:
    """Identify the process that owns a run — `<host>/<pid>`.

    Written on every run so a `RUNNING` row with a stale heartbeat can be traced
    to the pod that abandoned it (design-persistence.md §7).
    """
    return f"{socket.gethostname()}/{os.getpid()}"


# How long a non-terminal run may go without a heartbeat before the reaper
# declares its owner dead (design-persistence.md 7, F4).
#
# This is a *silence* limit, not a run-time limit: `run_audit` keeps a heartbeat
# ticking every `AUDIT_HEARTBEAT_INTERVAL_SECONDS` for as long as the engine and
# similarity phases are working (`_heartbeat_while_running`), and `_persist`
# writes its own per chunk. A live run therefore never goes quiet however large
# the catalogue, while an abandoned one (e.g. a killed worker) is reaped and its
# unique-index claim released after an hour of silence. No concurrent-audit
# protection is weakened: that is enforced by uq_mm_audit_run_active, which this
# timeout only needs to release eventually.
AUDIT_HEARTBEAT_TIMEOUT = timedelta(minutes=60)

# How often the scoring phases touch `heartbeat_at`. Far below the timeout so a
# few missed ticks (a slow DB, a busy GIL) cannot make a live run look dead.
AUDIT_HEARTBEAT_INTERVAL_SECONDS = 30.0

# The two statuses `uq_mm_audit_run_active` indexes. A run in either of them
# holds the per-entity-kind claim.
NON_TERMINAL_RUN_STATUSES = (RunStatus.PENDING.value, RunStatus.RUNNING.value)


# The index whose violation means "somebody else is already auditing this kind".
ACTIVE_RUN_INDEX = "uq_mm_audit_run_active"


class AuditAlreadyRunningError(Exception):
    """An audit of this entity kind is already in flight, somewhere.

    Raised when `INSERT INTO mm_audit_run` violates the partial unique index
    `uq_mm_audit_run_active`. That index -- not a dict on a module singleton --
    is the invariant, which is the entire point: the guard it replaces
    (`self._running`) was per-process and per-restart, while `rollout.yaml`
    guarantees pod overlap on every deploy, so two admins served by two pods
    both started full audits over the same catalogue (F-17, F8).

    Carries the in-flight run's identity so the loser is told *which* run is
    already going and can poll it, instead of receiving a 202 for work it did
    not start.
    """

    status_code = 409

    def __init__(
        self,
        *,
        entity_kind: str,
        run_id: Optional[str],
        started_at: Optional[datetime] = None,
        runner_id: Optional[str] = None,
        constraint: Optional[str] = None,
    ) -> None:
        self.entity_kind = entity_kind
        self.run_id = run_id
        self.started_at = started_at
        self.runner_id = runner_id
        self.constraint = constraint
        super().__init__(
            f"An audit of {entity_kind} is already running"
            + (f" (run {run_id}" if run_id else " (run unknown")
            + (f", started by {runner_id}" if runner_id else "")
            + ")."
        )

    def to_detail(self) -> dict[str, Any]:
        """The 409 body. Everything the admin UI needs to explain the refusal."""
        return {
            "error": "audit_already_running",
            "message": str(self),
            "entity_kind": self.entity_kind,
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "runner_id": self.runner_id,
        }


def _violates_active_run_index(exc: IntegrityError) -> bool:
    """Is this IntegrityError specifically the active-run claim being taken?

    Checked by constraint name rather than by catching every IntegrityError:
    `mm_audit_run` also carries a primary key, a foreign key to `users` and
    three CHECK constraints, and reporting any of those as "somebody else is
    auditing" would turn a bug into a mystery.
    """
    diagnostic = getattr(getattr(exc, "orig", None), "diag", None)
    if diagnostic is not None and getattr(diagnostic, "constraint_name", None):
        return diagnostic.constraint_name == ACTIVE_RUN_INDEX
    return ACTIVE_RUN_INDEX in str(getattr(exc, "orig", exc))


def reap_stale_audit_runs(
    *,
    stale_after: timedelta = AUDIT_HEARTBEAT_TIMEOUT,
    session=None,
) -> int:
    """Fail every non-terminal run whose heartbeat has gone quiet (7, F4).

    The worker is a `daemon=True` thread, so it dies with its process. What it
    leaves behind is a `RUNNING` row that no reader can tell from a live one --
    and that row holds `uq_mm_audit_run_active`, so the invariant which stops
    two pods auditing at once becomes the thing that stops *any* pod auditing
    ever again. This single statement ends both problems: the run is honestly
    labelled `FAILED`, and the claim is released.

    Partial children stay. They belong to a `FAILED` run, and every read path is
    pinned to the newest `SUCCEEDED` one, so nobody is served from them.

    `COALESCE(heartbeat_at, started_at)` because `heartbeat_at` is nullable: a
    run that died before its first chunk commit has none, and it is exactly the
    run most in need of reaping.

    Returns the number of runs promoted; safe to call from any pod, but the
    caller is expected to hold `LeaderLockContext` so N pods do not all issue it.
    """
    cutoff = datetime.now(timezone.utc) - stale_after
    table = MarketplaceAuditRun.__table__
    statement = (
        sa_update(table)
        .where(table.c.status.in_(NON_TERMINAL_RUN_STATUSES))
        .where(func.coalesce(table.c.heartbeat_at, table.c.started_at) < cutoff)
        .values(
            status=RunStatus.FAILED.value,
            finished_at=func.now(),
            error=(
                "heartbeat timeout: no progress for more than "
                f"{int(stale_after.total_seconds() // 60)} minutes; "
                "the worker that owned this run is gone"
            ),
        )
    )

    def _execute(active_session) -> int:
        result = active_session.execute(statement)
        active_session.commit()
        return result.rowcount or 0

    if session is not None:
        reaped = _execute(session)
    else:
        with get_session() as own_session:
            reaped = _execute(own_session)

    if reaped:
        logger.warning(
            "[marketplace_audit] reaper: failed %s abandoned audit run(s) with no heartbeat since %s",
            reaped,
            cutoff.isoformat(),
        )
    return reaped


def _strong_neighbour_map(
    purpose_neighbors: Optional[dict[str, list[dict]]], known_ids: Any, threshold: float
) -> dict[str, set[str]]:
    """For each known id, the known neighbours whose cosine reaches *threshold*."""
    strong: dict[str, set[str]] = {}
    for aid, neighbors in (purpose_neighbors or {}).items():
        if aid not in known_ids:
            continue
        picked = {
            nb["other_id"]
            for nb in neighbors
            if nb.get("other_id") in known_ids and float(nb.get("cosine", 0.0)) >= threshold
        }
        if picked:
            strong[aid] = picked
    return strong


def _union_find(parent: dict[str, str]):
    """(find, union) closures over *parent*, which must map every id to itself initially."""

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    return find, union


def _enum_or(cls, value, default=None):
    """A value written by a newer engine must degrade, not raise.

    `cleanup_reason`, `usage_tier` and friends are free text columns, so
    an unknown member is a live possibility across a rolling deploy. One
    such row must not take the whole Overview down.
    """
    if value is None:
        return default
    try:
        return cls(value)
    except Exception:  # noqa: BLE001
        return default


def _rehydrate_verdicts(vm, rows) -> tuple[dict, dict, dict[str, int], tuple[dict, dict, dict]]:
    """(verdicts, usage_tiers, bucket_distribution, (purpose_cluster_id, cluster_size, cluster_canonical))."""
    verdicts: dict = {}
    usage_tiers: dict = {}
    purpose_cluster_id: dict = {}
    cluster_size: dict = {}
    cluster_canonical: dict = {}
    bucket_distribution: dict[str, int] = {"VERIFIED": 0, "COMMUNITY": 0, "DELETE": 0}

    for row in rows:
        aid = row["assistant_id"]
        verdicts[aid] = vm.Verdict(
            kind=_enum_or(vm.VerdictKind, row["verdict_kind"], vm.VerdictKind.KEEP),
            reason=_enum_or(vm.CleanupReason, row["cleanup_reason"]),
            detail=row["detail"] or "",
            canonical_id=row["canonical_id"],
            canonical_name=row["canonical_name"],
            canonical_users=row["canonical_users"],
            match_score=row["match_score"],
            bucket=_enum_or(vm.Bucket, row["bucket"]),
            bucket_reason=row["bucket_reason"],
        )
        if row["bucket"] in bucket_distribution:
            bucket_distribution[row["bucket"]] += 1
        tier = _enum_or(vm.UsageTier, row["usage_tier"])
        if tier is not None:
            usage_tiers[aid] = tier
        cid = row["purpose_cluster_id"]
        if cid is not None:
            purpose_cluster_id[aid] = cid
            cluster_size[cid] = cluster_size.get(cid, 0) + 1
            if row["canonical_id"]:
                cluster_canonical.setdefault(cid, row["canonical_id"])
    return verdicts, usage_tiers, bucket_distribution, (purpose_cluster_id, cluster_size, cluster_canonical)


def _rehydrate_findings(vm, rows) -> tuple[dict, dict[str, list]]:
    gate_results: dict = {}
    per_rule_hits: dict[str, list] = {}
    for row in rows:
        aid = row["assistant_id"]
        gate_result = gate_results.get(aid)
        if gate_result is None:
            gate_result = gate_results[aid] = vm.GateResult(assistant_id=aid)
        gate_result.findings.append(
            vm.Finding(
                rule=row["rule"],
                severity=_enum_or(vm.Severity, row["severity"], vm.Severity.CRITICAL),
                message=row["message"] or "",
                detail=row["detail"] or {},
            )
        )
        per_rule_hits.setdefault(row["rule"], []).append(aid)
    return gate_results, per_rule_hits


def _rehydrate_similarity(vm, rows) -> dict:
    similarity_results: dict = {}
    for row in sorted(rows, key=lambda r: (r["assistant_id"], r["rank"])):
        aid = row["assistant_id"]
        similarity = similarity_results.get(aid)
        if similarity is None:
            similarity = similarity_results[aid] = vm.SimilarityResult(assistant_id=aid)
        similarity.pairs.append(
            vm.SimilarityPair(
                a_id=aid,
                b_id=row["other_id"],
                signals=dict(row["signals"] or {}),
                combined=row["combined"],
                severity=_enum_or(vm.Severity, row["severity"]),
                reason=row["reason"],
            )
        )
    return similarity_results


@dataclass
class _SimilarityPhase:
    """What happened to the similarity half of a run (§7 F2).

    A degraded run must be labelled, never silently downgraded. These fields
    carry the outcome out of the augmentation and onto the persisted run row.
    """

    backend: str
    doc_count: Optional[int] = None
    index_name: Optional[str] = None
    error: Optional[str] = None

    @property
    def unavailable(self) -> bool:
        return self.backend == "UNAVAILABLE"


# `SimilarityPair.reason` -> `mm_audit_similarity.source`. Anything not listed
# is a weighted-score match and files as LEXICAL.
_SIMILARITY_SOURCES = {
    "clone_edge": "CLONE",
    "exact_name_match": "EXACT_NAME",
}


class AuditCache:
    """Thread-safe holder for the latest audit run per EntityKind.

    Modelled on the PoC's ``app/audit.py::AuditCache`` but adapted for
    codemie:

    * Config is loaded from the in-code MarketplaceManagementConfig defaults
      (codemie.configs.marketplace_management) rather than a YAML file.
    * Records come from ``get_adapter(kind).list_records()`` rather than a
      snapshot JSON file.
    * Results are persisted to ``mm_audit_run`` and ``mm_audit_verdict`` via
      a SQLModel Session (same pattern as ``codemie/clients/postgres.py::get_session``).

    Validator imports are deferred to first use (lazy) so this
    class and its module-level singleton can be imported without ML deps.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # Config is loaded lazily on first audit run per entity kind. The
        # per-kind dict lets assistants and workflows carry different gate
        # thresholds / weights without sharing state — see
        # codemie.configs.marketplace_management._KIND_DEFAULTS.
        self._configs: dict[EntityKind, Config] = {}
        # Per-kind state: cached AuditResult and the run ids around it.
        #
        # Two ids, deliberately, because they answer two different questions
        # and conflating them is what produced F-49/F-50:
        #
        # * `_run_ids[kind]` — the run that is happening *now* (or the newest
        #   one that happened). Assigned when the run is claimed, so
        #   `/audit/status` describes the run it is reporting on.
        # * `_result_run_ids[kind]` — the run `_results[kind]` was produced by,
        #   i.e. the evidence `/overview` and `/cleanup` are actually serving.
        #   It only moves when a run finishes.
        #
        # They are the same value except while a run is in flight, which is
        # exactly the window in which the difference matters.
        self._results: dict[EntityKind, AuditResult] = {}
        self._run_ids: dict[EntityKind, str] = {}
        self._result_run_ids: dict[EntityKind, str] = {}
        # Background-run bookkeeping. A full audit is CPU- and IO-heavy
        # (embedding round-trips) and used to be executed inline in the
        # request handler, so `POST /audit/run` stayed open for minutes and
        # starved the worker. It now runs on a worker thread and callers poll
        # `audit_status()`. `_running` is the source of truth for "an audit is
        # in flight", so every admin and every browser tab agrees — including
        # after a page reload, which in-memory UI state could never survive.
        self._running: dict[EntityKind, bool] = {}
        self._started_at: dict[EntityKind, float] = {}
        self._last_error: dict[EntityKind, Optional[str]] = {}
        self._finished_at: dict[EntityKind, float] = {}
        # What happened to the similarity half of the last finished run:
        # status, backend, coverage. `/audit/status` publishes it so a
        # degraded run is visible in the UI rather than only in a log line
        # (EXECUTION-PLAN 9.2, design-persistence.md §7 F2).
        self._degradation: dict[EntityKind, dict] = {}

    @staticmethod
    def _kind_to_key(entity_kind: EntityKind) -> str:
        """Map EntityKind enum → registry key ("assistants" / "workflows")."""
        return {
            EntityKind.ASSISTANT: "assistants",
            EntityKind.WORKFLOW: "workflows",
        }[entity_kind]

    def _config_for(self, entity_kind: EntityKind) -> Config:
        """Return the validator Config for a given entity kind.

        Resolved fresh on every call, not cached across calls. The old
        cache-forever behaviour (compute once per process, invalidate only via
        the explicit `update_config()`/direct-assignment paths) is what MR
        !4070 review comment #18 flagged: `PUT /entities/{kind}/config`
        persists to `DynamicConfigService` (DB-backed, so every pod reads the
        same row) but only updates `_configs[kind]` on the pod that served the
        request — every other pod kept serving its own stale in-memory copy
        indefinitely. `get_marketplace_management_config()` already reads its
        overrides from the DB on every call, so recomputing here just means
        every pod sees the same config within one request instead of only the
        one that happened to handle the write.
        """
        from codemie.service.marketplace_management.validator.config import Config as _Config

        mm_cfg = get_marketplace_management_config(self._kind_to_key(entity_kind))
        cfg = _Config.from_marketplace_management(mm_cfg)
        self._configs[entity_kind] = cfg
        return cfg

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_audit(
        self,
        entity_kind: EntityKind,
        *,
        include_es_similarity: bool = True,
        run_id: Optional[str] = None,
        started_ts: Optional[float] = None,
        trigger: str = RunTrigger.MANUAL.value,
        triggered_by_id: Optional[str] = None,
    ) -> AuditResult:
        """Run a fresh audit for *entity_kind* and persist the outcome.

        Steps:
        1. **Claim the run** — insert `mm_audit_run` with `status='RUNNING'`
           before any work happens, so the claim exists for the whole run and
           `uq_mm_audit_run_active` refuses a second one anywhere in the
           deployment (design-persistence.md 3.3). `start_audit` normally makes
           this claim and passes it in, so the caller gets the 409 synchronously
           rather than from a worker thread nobody is listening to.
        2. Fetch records via the appropriate EntityAdapter.
        3. Run Engine(cfg).audit() — this internally runs the gate checks,
           similarity analysis, verdict aggregation, and the second-pass
           assign_bucket() loop that populates verdict.bucket and
           verdict.bucket_reason on every Verdict.
        4. Augment with ES similarity — the *expensive* half.
        5. Persist a MarketplaceAuditRun row and one MarketplaceAuditVerdict
           row per assistant.
        6. Cache the result and return it.

        **`started_ts` / `finished_ts` bracket steps 1-5, not step 3** (F-96).
        They used to wrap `Engine.audit()` alone, so a run measured at 269
        seconds of wall clock was persisted as 0.5 s -- an under-report of
        roughly 500x, on the number the Overview KPI shows an admin. The
        engine's own cost is not lost: it keeps its own column,
        `engine_took_seconds`, fed by `AuditResult.took_seconds`.
        """
        # Deferred submodule imports: the top-level validator package
        # __init__.py is intentionally empty so importing this service
        # doesn't pull in the engine (only the actual audit run needs it).
        from codemie.service.marketplace_management.validator.engine import Engine

        # Per-kind config lookup — assistants and workflows carry
        # independent gate / similarity / weight tuning.
        with self._lock:
            cfg = self._config_for(entity_kind)

        if run_id is None:
            run_id, started_ts = self._claim_run(
                entity_kind,
                include_es_similarity=include_es_similarity,
                trigger=trigger,
                triggered_by_id=triggered_by_id,
            )
        elif started_ts is None:
            started_ts = time.time()

        # The run id is published *now*, not when the run finishes. It used to
        # be assigned at the very end, so for the entire duration of a run
        # `/audit/status` and `/overview` named the **previous** run — next to a
        # `finished_at` that also belonged to the previous run, and therefore
        # preceded the `started_at` beside it (F-49, F-50).
        with self._lock:
            self._run_ids[entity_kind] = run_id

        with self._heartbeat_while_running(run_id):
            adapter = get_adapter(entity_kind)
            records = adapter.list_records()

            # Recorded clone provenance: the third v1 similarity mechanism beside
            # the structural weights and the exact-name rule (decision D3b). Read
            # once for the whole corpus and handed to the engine, which stays a
            # pure function of its inputs.
            clone_edges = adapter.list_clone_edges()

            result: AuditResult = Engine(cfg).audit(records, clone_edges=clone_edges)

            # Review comment #19: nothing wrote a heartbeat between the claim and
            # the first persisted chunk, so a long engine/ES phase looked identical
            # to a dead worker. One touch after each pre-persist phase keeps the
            # reaper's cutoff from expiring while the run is still legitimately
            # working.
            self._touch_heartbeat(run_id)

            phase = self._run_similarity_phase(
                entity_kind,
                cfg,
                records,
                result,
                run_id=run_id,
                include_es_similarity=include_es_similarity,
            )

            self._touch_heartbeat(run_id)

        # AFTER the similarity phase. This one line is F-96: the similarity
        # phase is where the minutes go, and it used to sit outside the bracket.
        finished_ts = time.time()

        # Persist the run, its verdicts, its findings and its similarity rows.
        #
        # This is deliberately NOT wrapped in `except Exception: logger.warning`
        # any more. That swallow is what made the feature look healthy while it
        # stored nothing: `POST /audit/run` answered 202, `audit_status()`
        # reported `error: null`, and the only trace of a `NotNullViolation` on
        # every single insert was a WARNING line in the container log (F-25).
        # A persist failure is a failed audit and must be reported as one.
        self._persist(
            run_id=run_id,
            entity_kind=entity_kind,
            result=result,
            records=records,
            started_ts=started_ts,
            finished_ts=finished_ts,
            similarity_backend=phase.backend,
            es_doc_count=phase.doc_count,
            es_index_name=phase.index_name,
            degraded_reason=phase.error if phase.unavailable else None,
            trigger=trigger,
            triggered_by_id=triggered_by_id,
        )

        # What `/audit/status` reports for this kind until the next run.
        with self._lock:
            self._degradation[entity_kind] = {
                "status": RunStatus.PARTIAL.value if phase.unavailable else RunStatus.SUCCEEDED.value,
                "similarity_backend": phase.backend,
                "es_doc_count": phase.doc_count,
                "es_index_name": phase.index_name,
                "degraded": phase.unavailable,
                "degraded_reason": phase.error if phase.unavailable else None,
            }

        # Only the cache mutation needs the lock. It used to wrap this whole
        # method, so every other caller that touches `_lock` — a second admin
        # clicking Run audit, a config update — queued behind minutes of engine
        # and Elasticsearch work (F-08). Concurrent runs are already prevented
        # upstream by `start_audit`'s `_running` flag.
        with self._lock:
            self._results[entity_kind] = result
            self._result_run_ids[entity_kind] = run_id
        return result

    def _run_similarity_phase(
        self,
        entity_kind: EntityKind,
        cfg,
        records: list,
        result: AuditResult,
        *,
        run_id: str,
        include_es_similarity: bool,
    ) -> _SimilarityPhase:
        """ES similarity augmentation, then the health read-back.

        The augmentation populates the cluster fields (purpose_cluster_id,
        cluster_size, cluster_canonical) that feed the Overview's Top clusters
        table. It is ON by default so the discovery view isn't empty on first
        load; the trade-off is ~1s per record on cold cache. Callers that need
        the fast path can pass include_es_similarity=False.
        """
        phase = _SimilarityPhase(backend="ES" if include_es_similarity else "NONE")
        if not include_es_similarity:
            logger.debug("ES similarity augmentation skipped (include_es_similarity=False)")
            return phase  # explicit skip, silent -- and NOT a degradation

        try:
            self._augment_with_es(entity_kind, cfg, records, result, phase)
        except Exception as es_exc:
            phase.error = f"{type(es_exc).__name__}: {es_exc}"[:1000]
            logger.warning(
                "ES similarity augmentation failed: %s",
                es_exc,
                exc_info=True,
            )

        self._read_back_similarity_health(entity_kind, records, phase, run_id)
        return phase

    def _read_back_similarity_health(
        self, entity_kind: EntityKind, records: list, phase: _SimilarityPhase, run_id: str
    ) -> None:
        """`knn` swallows per-call failures by contract (an audit must not die
        because a search index blinked), so the failure has to be read back
        rather than caught. Either signal -- the coverage probe or any lookup --
        means similarity did not run as advertised.
        """
        from codemie.service.marketplace_management.es_similarity import (
            marketplace_assistants_index as _es_index,
        )

        lookup_degraded, lookup_error = _es_index.similarity_health()
        if phase.error is None and lookup_error:
            phase.error = lookup_error
        if lookup_degraded or phase.doc_count is None or phase.error:
            phase.backend = "UNAVAILABLE"
            if phase.error is None:
                phase.error = "Elasticsearch similarity was unavailable for this run (no document count could be read)."
            phase.doc_count = None
            logger.error(
                "Marketplace audit %s degraded: similarity backend UNAVAILABLE (%s)",
                run_id,
                phase.error,
            )
        else:
            self._emit_index_coverage_metric(
                entity_kind=entity_kind,
                records=records,
                es_doc_count=phase.doc_count,
            )

    def _augment_with_es(
        self, entity_kind: EntityKind, cfg, records: list, result: AuditResult, phase: _SimilarityPhase
    ) -> None:
        """The ES half: KNN neighbours, clusters, then a second verdict pass."""
        from codemie.service.marketplace_management.es_similarity import (
            MarketplaceAssistantsIndex,
            marketplace_assistants_index as _es_index,
        )
        from codemie.service.marketplace_management.validator.engine import Engine

        # Re-arm the KNN circuit breaker. It trips when the embeddings
        # endpoint stops answering, and without this a single transient
        # outage would silently cost every later audit its similarity
        # neighbours and clusters for the lifetime of the process.
        # Uses the module-level singleton so per-run state is instance-level
        # and concurrent audits cannot clobber each other's circuit state
        # (CR-019).
        _es_index.reset_similarity_circuit()

        # Coverage, measured once per run rather than inferred. `knn`
        # answers `[]` both when there are genuinely no neighbours and when
        # the index is empty or unreachable, so without this number the
        # three cases are indistinguishable (F-27, R14).
        phase.index_name = MarketplaceAssistantsIndex.index_name()
        phase.doc_count = _es_index.doc_count()

        mm_cfg = get_marketplace_management_config(self._kind_to_key(entity_kind))
        queries = self._es_queries(records, result)

        # One embedding round trip per chunk instead of one per record.
        # Measured live: 219 records took 196s as 219 sequential
        # embed-then-query calls, and ~4s as one batch (3.3s to embed all
        # 219, 3ms per by-vector query). That is F-95, and it is also what
        # made the 10s breaker fire: 219 sequential calls is 219 chances to
        # draw one slow enough to trip it, and the old breaker latched on
        # the first and discarded the rest of the run.
        neighbours_by_id = _es_index.knn_batch(queries, k=mm_cfg.similarity.shortlist_size)
        self._merge_es_neighbours(result, records, neighbours_by_id)

        # Build cluster assignments from the ES neighbor edges via
        # connected components — the replacement for the sklearn
        # AgglomerativeClustering path, which was deleted in
        # EXECUTION-PLAN 9.1 because the vector index it clustered
        # was never produced (F-71). Uses the CRITICAL threshold
        # (typically 0.85 — near-duplicate) so clusters represent
        # genuinely overlapping assistants, not merely "vaguely
        # related". The lower report_threshold would collapse
        # everything into one giant transitive cluster.
        self._build_clusters_from_neighbors(
            result=result,
            records=records,
            similarity_threshold=mm_cfg.similarity.critical_threshold,
        )

        # The purpose evidence exists only now, and one of the engine's
        # outcomes is a function of it: CLEANUP:purpose_duplicate. `audit()`
        # necessarily decided before this block ran -- the block reads
        # `result.verdicts` to know what to embed -- so without this second
        # pass that outcome could never be produced by any corpus, and the
        # Cleanup page's `purpose_duplicate` filter would return zero rows on
        # every run whatever the marketplace held (the rule is currently
        # switched off, see `_check_duplicate_verdict`).
        # Same shape as F-71: a consumer running before its producer.
        Engine(cfg).reaggregate(result, records)

    @staticmethod
    def _es_queries(records: list, result: AuditResult) -> list[tuple[str, str]]:
        """(record_id, query_text) for every record that has a verdict and any text."""
        # ada-002 / titan / gecko embedding models all cap at 8k input
        # tokens (~ 24k characters conservatively). Truncate here so a
        # single fat system_prompt doesn't blow up the KNN call.
        max_query_chars = 6000
        queries: list[tuple[str, str]] = []
        for r in records:
            if r.id not in result.verdicts:
                continue
            # Prefer name + description (short, semantically dense) over
            # the raw system_prompt; only fall back to prompt if desc empty.
            text_parts = [r.name or "", r.description or ""]
            if not (r.description or "").strip():
                text_parts.append((r.system_prompt or "")[:max_query_chars])
            query_text = " ".join(p for p in text_parts if p).strip()[:max_query_chars]
            if query_text:
                queries.append((r.id, query_text))
        return queries

    @staticmethod
    def _merge_es_neighbours(result: AuditResult, records: list, neighbours_by_id: dict) -> None:
        # `other_users` is the neighbour's REAL adoption, read from the
        # corpus this run is already holding. It used to be hardcoded to 0,
        # which quietly made the `purpose_duplicate` rule unsatisfiable:
        # that rule asks whether the neighbour is at least three times as
        # used as this record (`engine.aggregate_verdict` rule 2), and
        # `0 >= 3 * anything` is false for every record in the marketplace.
        # It is also what the Cleanup drawer renders as "230u vs 1u".
        users_by_id = {r.id: (r.unique_users_count or 0) for r in records}

        for r_id, es_neighbors in neighbours_by_id.items():
            existing = result.purpose_neighbors.get(r_id) or []
            existing_ids = {n["other_id"] for n in existing}
            for nb in es_neighbors:
                if nb.assistant_id in existing_ids:
                    continue
                existing.append(
                    {
                        "other_id": nb.assistant_id,
                        "other_name": nb.name,
                        "cosine": nb.score,
                        "other_users": users_by_id.get(nb.assistant_id, 0),
                    }
                )
                existing_ids.add(nb.assistant_id)
            result.purpose_neighbors[r_id] = existing

    @staticmethod
    def _emit_index_coverage_metric(
        *,
        entity_kind: EntityKind,
        records: list,
        es_doc_count: Optional[int],
    ) -> None:
        """Publish "similarity compared against N of M" as a metric.

        F-87 is a divergence nobody was measuring: 219 rows carry
        `is_global = true` in Postgres while the `marketplace_assistants`
        index holds 19 documents. Every layer that could have noticed was
        swallowing — the publish path never awaits its indexing task, the
        approve path catches every exception, and the audit reported a clean
        success against 8.7% of the catalogue.

        The audit already knows both numbers at this point, so it is the
        cheapest honest place to emit them. Best-effort: a monitoring backend
        that is down must not fail an audit.
        """
        try:
            from codemie.configs import config
            from codemie.service.marketplace_management.marketplace_index_sync import (
                METRIC_INDEX_DIVERGENCE,
            )
            from codemie.service.monitoring.base_monitoring_service import (
                BaseMonitoringService,
            )

            published = sum(1 for r in records if getattr(r, "is_global", False))
            indexed = int(es_doc_count or 0)
            missing = max(published - indexed, 0)
            # Same metric name and the same three counts as
            # `marketplace_index_sync.measure_index_divergence`, emitted from
            # the numbers this run already has in hand rather than paying a
            # second round trip to each store.
            BaseMonitoringService.send_count_metric(
                name=METRIC_INDEX_DIVERGENCE,
                description="Published entities absent from the marketplace search index",
                attributes={
                    "entity_kind": entity_kind.value,
                    "published_count": published,
                    "indexed_count": indexed,
                    "missing_count": missing,
                    "index_name": config.PLATFORM_MARKETPLACE_DATASOURCE_NAME,
                },
                count=missing,
            )
            if published > indexed:
                logger.warning(
                    "[marketplace_index] %s of %s published %s(s) are missing from %s",
                    published - indexed,
                    published,
                    entity_kind.value.lower(),
                    config.PLATFORM_MARKETPLACE_DATASOURCE_NAME,
                )
        except Exception:  # noqa: BLE001 - observability must never fail a run
            logger.warning("Could not emit marketplace index coverage metric", exc_info=True)

    @staticmethod
    def _build_clusters_from_neighbors(
        *,
        result: "AuditResult",
        records: list,
        similarity_threshold: float,
    ) -> None:
        """Populate cluster fields from ES neighbour edges.

        Replaces the deleted sklearn AgglomerativeClustering path. Two
        assistants sit in the same cluster iff their ES cosine similarity
        exceeds *similarity_threshold* (default from mm_cfg.similarity —
        typically 0.55). Cluster membership is derived via union-find
        (connected components) over the neighbour graph.

        Writes result.purpose_cluster_id / cluster_size / cluster_canonical
        in place so the Overview's Top clusters table populates without any
        new dependencies.
        """
        # Union-find over string ids.
        parent: dict[str, str] = {r.id: r.id for r in records}
        find, union = _union_find(parent)

        # Build an index of each assistant's above-threshold neighbours
        # first, so we can require MUTUAL neighborhood before unioning.
        # Plain "any edge above threshold" produces a giant transitive
        # cluster because pairs like (A,B) 0.86, (B,C) 0.86 collapse A,C,
        # even if (A,C) sits at 0.60. Mutual-neighbor cuts that chain.
        strong = _strong_neighbour_map(result.purpose_neighbors, parent, similarity_threshold)

        for aid, others in strong.items():
            for other in others:
                # Only union when the reverse edge also crosses the bar.
                if aid in strong.get(other, ()):
                    union(aid, other)

        # Group ids by root and assign a stable numeric cluster_id per
        # non-trivial component (skip singletons — those aren't clusters).
        groups: dict[str, list[str]] = {}
        for aid in parent:
            groups.setdefault(find(aid), []).append(aid)

        cluster_size: dict[int, int] = {}
        cluster_canonical: dict[int, str] = {}
        purpose_cluster_id: dict[str, int] = {}

        # Rank groups by size (largest first) so cluster_id 0 = biggest.
        by_users = {r.id: r.unique_users_count for r in records}
        ranked = sorted(
            ((root, members) for root, members in groups.items() if len(members) >= 2),
            key=lambda kv: -len(kv[1]),
        )
        for cid, (_root, members) in enumerate(ranked):
            cluster_size[cid] = len(members)
            # Canonical = the member with the most users (proxy for
            # "official" copy the others duplicate).
            canonical_id = max(members, key=lambda m: by_users.get(m, 0))
            cluster_canonical[cid] = canonical_id
            for m in members:
                purpose_cluster_id[m] = cid

        result.purpose_cluster_id = purpose_cluster_id
        result.cluster_size = cluster_size
        result.cluster_canonical = cluster_canonical

    def get_or_run(self, kind: EntityKind) -> AuditResult:
        """Return the last available AuditResult for *kind* WITHOUT re-running.

        Non-blocking on the audit lock — a long-running run_audit call by
        another admin must not stall page loads for everyone else. Reads
        the in-process cache first (dict get is atomic), then checks whether
        the DB's newest servable run has moved past it, then the DB, then
        returns the empty placeholder. Only mutations of `_results` need
        to hold the lock, so writers and readers never contend.

        1. In-process cache — but only while it still matches the DB's
           newest servable run. MR !4070 review comment #18: with >1 replica,
           a pod that only ever served reads cached whichever run it first
           hydrated and never looked again, so `/overview` on that pod stayed
           pinned to a run another pod (or this one, after a restart) had long
           since superseded — while `/cleanup`, which always resolves
           `latest_succeeded_run_id` itself, had already moved on.
        2. Newest persisted run in the DB — survives backend restarts and
           picks up runs completed by other pods.
        3. Empty placeholder — UI shows "Run audit to populate".
        """
        cached = self._results.get(kind)
        if cached is not None:
            latest_run_id = self.latest_succeeded_run_id(kind)
            if latest_run_id is None or latest_run_id == self._result_run_ids.get(kind):
                return cached

        hydrated = self._hydrate_latest_run(kind)
        if hydrated is not None:
            # Store atomically without blocking on the audit lock — a
            # racing writer will overwrite this with fresh data, which is
            # fine (either value is a valid cache entry).
            self._results[kind] = hydrated
            return hydrated

        return self._empty_result()

    @staticmethod
    def _empty_result() -> "AuditResult":
        """A skeleton AuditResult for the "no audit ever run" state."""
        from codemie.service.marketplace_management.validator.models import AuditResult as _AR  # noqa: N814

        return _AR(
            total=0,
            gate_results={},
            similarity_results={},
            verdicts={},
            usage_tiers={},
            clusters=[],
            per_rule_hits={},
            took_seconds=0.0,
            bucket_distribution={"VERIFIED": 0, "COMMUNITY": 0, "DELETE": 0},
            bucket_usage_retention={},
        )

    def latest_succeeded_run_id(self, kind: EntityKind) -> Optional[str]:
        """The run every read for *kind* is served from, resolved in SQL.

        One row off `ix_mm_audit_run_kind_latest_serveable`. Pinning here rather than
        in each handler is what stops a re-audit landing mid-request from mixing
        two runs into one response (design-persistence.md §4.2 point 4).
        """
        try:
            with get_session() as session:
                return audit_reads.latest_succeeded_run_id(session, kind.value)
        except Exception as exc:  # noqa: BLE001 - a read must not 500 on a cold DB
            logger.warning("Cannot resolve the latest succeeded audit run: %s", exc)
            return None

    def _hydrate_latest_run(self, kind: EntityKind) -> Optional["AuditResult"]:
        """Rebuild an `AuditResult` from the newest servable run in Postgres.

        Replaces `_load_latest_from_db`, which was wrong in two ways that both
        showed up as an empty admin UI after every restart:

        * **It was not pinned.** It took the newest run of *any* status and
          *any* schema version, so it could serve an Overview from a v1 run that
          carries no findings at all, and could disagree with `/cleanup` — which
          is pinned to the newest SUCCEEDED v2 run — about which audit the page
          is showing.
        * **It rebuilt only half the result.** `gate_results` and
          `similarity_results` came back empty because there was nowhere to read
          them from, so `per_rule_hits` was recovered from a JSON blob and the
          per-row critical/optional counts read 0 (F-07, F-30).

        `mm_audit_finding` and `mm_audit_similarity` now exist, so both are read
        back and the rehydrated result carries what the engine produced.
        """
        try:
            from codemie.service.marketplace_management.validator import models as vm
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cannot rehydrate audit — dependency import failed: %s", exc)
            return None

        try:
            with get_session() as session:
                run_id = audit_reads.latest_succeeded_run_id(session, kind.value)
                if run_id is None:
                    return None
                header = audit_reads.run_header(session, run_id) or {}
                verdict_rows = self._run_rows(session, MarketplaceAuditVerdict, run_id)
                finding_rows = self._run_rows(session, MarketplaceAuditFinding, run_id)
                similarity_rows = self._run_rows(session, MarketplaceAuditSimilarity, run_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cannot rehydrate audit from DB: %s", exc)
            return None

        self._run_ids[kind] = run_id
        self._result_run_ids[kind] = run_id

        verdicts, usage_tiers, bucket_distribution, clusters = _rehydrate_verdicts(vm, verdict_rows)
        gate_results, per_rule_hits = _rehydrate_findings(vm, finding_rows)
        similarity_results = _rehydrate_similarity(vm, similarity_rows)

        aggregates = header.get("aggregates") or {}
        rehydrated = vm.AuditResult(
            total=header.get("total_records") or len(verdict_rows),
            gate_results=gate_results,
            similarity_results=similarity_results,
            verdicts=verdicts,
            usage_tiers=usage_tiers,
            clusters=[],
            per_rule_hits=per_rule_hits,
            took_seconds=header.get("engine_took_seconds") or 0.0,
            bucket_distribution=bucket_distribution,
            bucket_usage_retention=dict(aggregates.get("bucket_usage_retention") or {}),
        )
        rehydrated.purpose_cluster_id, rehydrated.cluster_size, rehydrated.cluster_canonical = clusters
        return rehydrated

    @staticmethod
    def _run_rows(session, model, run_id: str) -> list:
        table = model.__table__
        return session.execute(sa_select(table).where(table.c.audit_run_id == run_id)).mappings().all()

    def latest_run_id(self, kind: EntityKind) -> Optional[str]:
        """The run id `GET /overview` should label its numbers with.

        This is the run `get_or_run` is serving — **not** whatever run happens
        to be in flight. A run that has been claimed but has not yet written a
        verdict has produced no evidence, so naming it here would tell an admin
        that the buckets and gate hits on screen came from a run that has not
        finished. `/audit/status` is where the in-flight run is reported.

        Deliberately does NOT take `_lock` — `GET /overview` calls this on
        every load, and a lock taken here queues the whole Overview behind a
        running audit (F-08). A single dict read is atomic under the GIL, the
        same assumption `is_running`, `audit_status` and `get_or_run` already
        make.
        """
        return self._result_run_ids.get(kind)

    # ------------------------------------------------------------------
    # Asynchronous audit execution
    # ------------------------------------------------------------------

    def is_running(self, kind: EntityKind) -> bool:
        """True while a background audit for *kind* is in flight.

        Deliberately does NOT take `_lock`: `run_audit` holds that lock for
        the whole (minutes-long) run, so a status poll that waited on it
        would block for exactly as long as the thing it is reporting on.
        A plain dict read is atomic under the GIL.
        """
        return bool(self._running.get(kind))

    def audit_status(self, kind: EntityKind) -> dict:
        """Status of the most recent / current audit for *kind*.

        Safe to poll frequently — reads only atomic dict lookups.
        """
        degradation = self._degradation.get(kind) or {}
        running = self.is_running(kind)
        status = {
            "is_running": running,
            "run_id": self._run_ids.get(kind),
            "started_at": self._started_at.get(kind),
            "finished_at": self._finished_at.get(kind),
            "error": self._last_error.get(kind),
            # §7 F2. Absent these four, "no duplicates" and "the duplicate
            # detector never ran" are the same response body (F-25, F-27, R14).
            "status": (RunStatus.RUNNING.value if running else degradation.get("status")),
            "similarity_backend": degradation.get("similarity_backend"),
            "es_doc_count": degradation.get("es_doc_count"),
            "degraded": bool(degradation.get("degraded", False)),
            "degraded_reason": degradation.get("degraded_reason"),
        }
        if status["run_id"] is None or degradation == {}:
            # Nothing in *this* process knows about the last run. That is the
            # normal state after a restart or on a second pod, and it used to
            # answer every field null -- so a PARTIAL run written to
            # `mm_audit_run` moments earlier was invisible to the UI that asked
            # for it. One indexed lookup on the newest run of this kind fills
            # only what the process could not supply.
            status = self._fill_status_from_db(kind, status)

        # `error` must stay null for a degraded-but-successful (PARTIAL) run:
        # the frontend treats any non-null `error` as a hard failure and never
        # reads `degraded_reason`, so folding the degradation reason in here
        # made every PARTIAL run (e.g. similarity backend unreachable) report
        # as "Could not finish the audit" even though verdicts were persisted
        # and the run is genuinely usable. `degraded`/`degraded_reason` above
        # already carry that information for the case that needs it.
        return status

    @staticmethod
    def _fill_status_from_db(kind: EntityKind, status: dict) -> dict:
        """Complete an in-process status from the newest persisted run.

        Best-effort and non-authoritative: `is_running` is deliberately left
        alone, because a worker thread is a property of this process and the
        cross-replica claim is already enforced by `uq_mm_audit_run_active`.
        A status poll must never raise, so a database that cannot be reached
        simply leaves the fields as the process had them.
        """
        try:
            with get_session() as session:
                row = (
                    session.execute(
                        sa_select(
                            MarketplaceAuditRun.__table__.c.id,
                            MarketplaceAuditRun.__table__.c.status,
                            MarketplaceAuditRun.__table__.c.similarity_backend,
                            MarketplaceAuditRun.__table__.c.es_doc_count,
                            MarketplaceAuditRun.__table__.c.error,
                            MarketplaceAuditRun.__table__.c.started_ts,
                            MarketplaceAuditRun.__table__.c.finished_ts,
                        )
                        .where(MarketplaceAuditRun.__table__.c.entity_kind == kind.value)
                        .order_by(MarketplaceAuditRun.__table__.c.started_at.desc())
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
        except Exception:  # noqa: BLE001 - a status poll must never fail
            logger.warning("Could not read the last audit run for %s", kind.value)
            return status

        if row is None:
            return status

        degraded = row["similarity_backend"] == "UNAVAILABLE"
        filled = dict(status)
        for key, value in (
            ("run_id", row["id"]),
            ("status", row["status"]),
            ("similarity_backend", row["similarity_backend"]),
            ("es_doc_count", row["es_doc_count"]),
            ("error", row["error"]),
            ("started_at", row["started_ts"]),
            ("finished_at", row["finished_ts"]),
        ):
            if filled.get(key) is None:
                filled[key] = value
        if not filled.get("degraded"):
            filled["degraded"] = degraded
            if degraded and filled.get("degraded_reason") is None:
                filled["degraded_reason"] = row["error"]
        return filled

    def start_audit(
        self,
        kind: EntityKind,
        *,
        include_es_similarity: bool = True,
        trigger: str = RunTrigger.MANUAL.value,
        triggered_by_id: Optional[str] = None,
    ) -> dict:
        """Claim the run in Postgres, then kick it off on a worker thread.

        The claim comes first, and synchronously, so the caller learns *here*
        that somebody else is already auditing — as a
        `AuditAlreadyRunningError` (409) carrying the in-flight run's id.

        The guard this replaces was `self._running`, a dict on a module
        singleton. It could only ever see runs started by *this* process, and
        `rollout.yaml` guarantees pod overlap on every deploy, so two admins on
        two pods each started a full audit over the same catalogue and both
        wrote snapshots (F-17). Worse, the collision was only detected at the
        very end, when the second run's `_persist` hit the unique index — after
        it had already spent the minutes.

        Raises:
            AuditAlreadyRunningError: a run of *kind* is already in flight,
                anywhere in the deployment. `status_code` is 409 and
                `to_detail()` is the response body.
        """
        run_id, started_ts = self._claim_run(
            kind,
            include_es_similarity=include_es_similarity,
            trigger=trigger,
            triggered_by_id=triggered_by_id,
        )

        # The run is claimed and about to happen: count it here rather than in
        # the worker, so the metric records the operator's click even if the
        # thread dies before it can report anything (EXECUTION-PLAN 9.4, F-36).
        track_marketplace_metric(
            METRIC_AUDIT_RUN,
            entity_id=run_id,
            actor_id=triggered_by_id,
            additional_attributes={
                "entity_kind": kind.value if hasattr(kind, "value") else str(kind),
                "trigger": trigger,
                "include_es_similarity": include_es_similarity,
            },
        )

        with self._lock:
            self._running[kind] = True
            self._started_at[kind] = started_ts
            # Cleared, not left behind. It used to keep the *previous* run's
            # value for the whole of this one, so `/audit/status` answered with
            # a `finished_at` that preceded its own `started_at` (F-50).
            self._finished_at[kind] = None
            self._last_error[kind] = None
            self._run_ids[kind] = run_id

        def _worker() -> None:
            try:
                self.run_audit(
                    kind,
                    include_es_similarity=include_es_similarity,
                    run_id=run_id,
                    started_ts=started_ts,
                    trigger=trigger,
                    triggered_by_id=triggered_by_id,
                )
            except Exception as exc:  # noqa: BLE001 - reported via status
                # The thread has no caller to raise into; record the failure so
                # the next poll can surface it instead of the UI spinning forever.
                logger.exception("Marketplace audit failed for %s", kind)
                self._last_error[kind] = str(exc)
                # And release the claim. A run that raised before `_persist`
                # could ever mark it FAILED would otherwise sit `RUNNING`
                # forever, holding `uq_mm_audit_run_active` and locking every
                # pod out of auditing this kind until a human noticed.
                self._fail_run_row(run_id=run_id, error=exc)
            finally:
                self._finished_at[kind] = time.time()
                self._running[kind] = False

        thread = threading.Thread(
            target=_worker,
            name=f"marketplace-audit-{self._kind_to_key(kind)}",
            daemon=True,
        )
        thread.start()
        return self.audit_status(kind)

    # -- the cross-replica claim ------------------------------------------

    def _claim_run(
        self,
        entity_kind: EntityKind,
        *,
        include_es_similarity: bool = True,
        trigger: str = RunTrigger.MANUAL.value,
        triggered_by_id: Optional[str] = None,
    ) -> tuple[str, float]:
        """Open a `RUNNING` run row for *entity_kind*, or raise 409.

        The `INSERT` is the claim and the partial unique index
        `uq_mm_audit_run_active` is the arbiter — the whole coordination
        mechanism is one statement against the only shared, always-present
        substrate this deployment has (design-persistence.md 3.4: redis and
        nats are not running and are not even declared in `docker-compose.yml`).

        Only an `IntegrityError` naming that index becomes a 409. `mm_audit_run`
        also carries a primary key, a foreign key to `users` and three CHECK
        constraints, and reporting one of those as "somebody else is auditing"
        would turn a bug into a mystery.

        Returns `(run_id, started_ts)` — the clock the whole run is measured
        against (F-96).
        """
        run_id = str(uuid.uuid4())
        started_ts = time.time()
        try:
            self._open_run_row(
                run_id=run_id,
                entity_kind=entity_kind,
                started_at=datetime.fromtimestamp(started_ts, tz=timezone.utc),
                trigger=trigger,
                triggered_by_id=triggered_by_id,
                similarity_backend="ES" if include_es_similarity else "NONE",
                config_snapshot=self._config_snapshot(entity_kind),
                started_ts=started_ts,
            )
        except IntegrityError as exc:
            if not _violates_active_run_index(exc):
                raise
            in_flight = self._active_run(entity_kind) or {}
            raise AuditAlreadyRunningError(
                entity_kind=entity_kind.value,
                run_id=in_flight.get("id"),
                started_at=in_flight.get("started_at"),
                runner_id=in_flight.get("runner_id"),
                constraint=ACTIVE_RUN_INDEX,
            ) from exc
        return run_id, started_ts

    @contextmanager
    def _heartbeat_while_running(self, run_id: str) -> Iterator[None]:
        """Keep the run's heartbeat ticking while the body does its scoring work.

        Gate checks, pairwise similarity and the ES phase are long, CPU-bound
        stretches with no natural place to write a heartbeat, and the run is
        otherwise silent until `_persist` starts its first chunk -- so a big
        catalogue could outlast the reaper's cutoff while perfectly alive and
        have its claim failed from under it. A daemon ticker touches
        `heartbeat_at` every `AUDIT_HEARTBEAT_INTERVAL_SECONDS` until the body
        exits, however it exits.
        """
        stop = threading.Event()

        def _tick() -> None:
            while not stop.wait(AUDIT_HEARTBEAT_INTERVAL_SECONDS):
                self._touch_heartbeat(run_id)

        ticker = threading.Thread(target=_tick, name=f"marketplace-audit-heartbeat-{run_id[:8]}", daemon=True)
        ticker.start()
        try:
            yield
        finally:
            stop.set()
            ticker.join(timeout=5)

    @staticmethod
    def _touch_heartbeat(run_id: str) -> None:
        """Mark a non-terminal run as still alive without touching its other columns.

        MR !4070 review comment #19: engine scoring and the ES knn_batch phase
        both run before `_persist`'s first chunk commit, so a run could go the
        entire wall-clock length of those phases with no heartbeat at all --
        the reaper only ever saw `started_at`. Called at phase boundaries in
        `run_audit` so a long-running scoring/ES phase keeps renewing the
        reaper's cutoff instead of racing it blind.
        """
        try:
            with get_session() as session:
                session.execute(
                    sa_update(MarketplaceAuditRun.__table__)
                    .where(MarketplaceAuditRun.__table__.c.id == run_id)
                    .where(MarketplaceAuditRun.__table__.c.status.in_(NON_TERMINAL_RUN_STATUSES))
                    .values(heartbeat_at=func.now())
                )
                session.commit()
        except Exception as exc:  # noqa: BLE001 - a missed heartbeat must not fail the audit
            logger.warning("Could not update heartbeat for audit run %s: %s", run_id, exc)

    @staticmethod
    def _active_run(entity_kind: EntityKind) -> Optional[dict]:
        """The non-terminal run of *entity_kind*, if one is in flight.

        `uq_mm_audit_run_active` guarantees there is at most one, which is what
        lets the 409 name it rather than hand back a list.
        """
        table = MarketplaceAuditRun.__table__
        try:
            with get_session() as session:
                row = (
                    session.execute(
                        sa_select(table.c.id, table.c.started_at, table.c.runner_id)
                        .where(table.c.entity_kind == entity_kind.value)
                        .where(table.c.status.in_(NON_TERMINAL_RUN_STATUSES))
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
                return dict(row) if row else None
        except Exception as exc:  # noqa: BLE001 - the conflict is the headline
            logger.warning("Cannot resolve the in-flight audit run: %s", exc)
            return None

    def update_config(self, entity_kind: EntityKind, updates: dict) -> None:
        """Drop the cached validator Config for a kind so the next read rebuilds it.

        This never writes configuration. Overrides are persisted in
        `DynamicConfigService` (DB-backed, shared by every pod) and
        `get_marketplace_management_config()` merges them over the shipped
        defaults on each call, so `_config_for` already sees them. Saving the
        merged result into `_KIND_DEFAULTS` would make one pod keep a deleted
        override as its default until restart, while other pods used the real
        default. *updates* is therefore accepted only as the empty dict the
        threshold hook passes.
        """
        if updates:
            raise ValueError("update_config() only invalidates; persist overrides through DynamicConfigService")
        with self._lock:
            self._configs.pop(entity_kind, None)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _persist(
        self,
        run_id: str,
        entity_kind: EntityKind,
        result: AuditResult,
        started_ts: float,
        finished_ts: float,
        records: Optional[list] = None,
        similarity_backend: Optional[str] = None,
        es_doc_count: Optional[int] = None,
        es_index_name: Optional[str] = None,
        degraded_reason: Optional[str] = None,
        trigger: str = RunTrigger.MANUAL.value,
        triggered_by_id: Optional[str] = None,
    ) -> None:
        """Write the run and every child row it produced, in bounded chunks.

        Shape (design-persistence.md §4.2):

        1. The run row is opened `RUNNING` before any child is written, so a
           crash is distinguishable from a clean finish and `records_processed`
           has somewhere to accumulate.
        2. Children are upserted on their natural keys — `(run, assistant)`,
           `(run, assistant, rule)`, `(run, assistant, other)` — so a resumed or
           retried run converges instead of erroring on a duplicate key.
        3. One transaction per `PERSIST_CHUNK_SIZE` entities, each touching
           `heartbeat_at` and `records_processed`. Findings and similarity rows
           are committed with the verdicts they belong to, never separately, so
           a partial run never leaves a finding whose verdict is missing.
        4. Anything that goes wrong marks the run `FAILED` (with the error text
           on the row) and re-raises. Failing loudly is the point.
        """
        started_at = datetime.fromtimestamp(started_ts, tz=timezone.utc)
        finished_at = datetime.fromtimestamp(finished_ts, tz=timezone.utc)

        # `users_count` was hardcoded None, so "how many people does this delete
        # affect" needed a second read of the assistants table on every render.
        users_by_id: dict[str, Optional[int]] = {r.id: getattr(r, "unique_users_count", None) for r in (records or [])}

        config_snapshot = self._config_snapshot(entity_kind)
        aggregates = {
            "per_rule_hits": {rule: list(ids) for rule, ids in (result.per_rule_hits or {}).items()},
            "bucket_usage_retention": dict(result.bucket_usage_retention or {}),
            "bucket_distribution": dict(result.bucket_distribution or {}),
        }

        self._open_run_row(
            run_id=run_id,
            entity_kind=entity_kind,
            started_at=started_at,
            trigger=trigger,
            triggered_by_id=triggered_by_id,
            similarity_backend=similarity_backend,
            es_index_name=es_index_name,
            config_snapshot=config_snapshot,
            started_ts=started_ts,
        )

        assistant_ids = list(result.verdicts.keys())
        processed = 0
        try:
            for offset in range(0, len(assistant_ids), PERSIST_CHUNK_SIZE):
                chunk = assistant_ids[offset : offset + PERSIST_CHUNK_SIZE]
                self._write_chunk(
                    run_id=run_id,
                    result=result,
                    users_by_id=users_by_id,
                    assistant_ids=chunk,
                    processed_total=processed + len(chunk),
                )
                processed += len(chunk)

            self._close_run_row(
                run_id=run_id,
                finished_at=finished_at,
                finished_ts=finished_ts,
                records_processed=processed,
                total_records=result.total,
                engine_took_seconds=result.took_seconds,
                aggregates=aggregates,
                config_snapshot=config_snapshot,
                es_doc_count=es_doc_count,
                degraded_reason=degraded_reason,
            )
        except Exception as exc:
            # A BaseException (SIGINT / SystemExit / a killed worker) is
            # deliberately NOT caught: there is no guarantee the process lives
            # long enough to write anything, and the §7 reaper exists precisely
            # to promote such a run to PARTIAL from its stale heartbeat.
            self._fail_run_row(run_id=run_id, error=exc)
            raise

        self._reap_orphans_this_run_may_have_created(entity_kind)

    @staticmethod
    def _reap_orphans_this_run_may_have_created(entity_kind: EntityKind) -> None:
        """Apply the deletion policy to entities that vanished during the run.

        A run reads the corpus, spends time on it, and writes afterwards. An
        entity deleted inside that window is purged of moderation data it does
        not have yet (`Assistant._purge_marketplace_moderation_data` runs
        synchronously in the delete path) and then acquires a verdict row from
        this run — a row the hook already ran too early to see. That is not a
        hypothetical: the dev database carried 1 such row at Phase 11 and 3 at
        Phase 12, one of them beside an `mm_review_decision` whose `redacted_at`
        proves the hook did fire for that assistant.

        `design-persistence.md` §2.4 gives `assistant_id` no foreign key on
        purpose, so nothing in the database reaps them and the daily sweep is up
        to 24 hours away — during which `cleanup_page` renders each one as a row
        with a NULL name that 404s when clicked (F-39). The run that creates the
        orphan is the only place that can be sure to see it, so it applies the
        same policy here: PURGE the machine-generated assessments, ANONYMISE the
        review decision. Deliberately the same repository and the same
        statements as the delete hook and the nightly sweep, so the three cannot
        drift into three different policies.

        Failures are logged and swallowed. The run itself succeeded and its rows
        are already committed; refusing to report that because a cleanup
        statement failed would turn a retention lag into a failed audit, and the
        nightly sweep still reclaims whatever this missed.
        """
        try:
            from codemie.service.marketplace_management.audit_retention import (
                marketplace_moderation_purge_repository,
            )

            with get_session() as session:
                reclaimed = marketplace_moderation_purge_repository.purge_orphans(
                    entity_kind=entity_kind.value, session=session
                )
                session.commit()
            if reclaimed:
                logger.info(
                    "[marketplace_audit] post-run purge: reclaimed moderation data for "
                    "%s %s(s) deleted while the run was in flight",
                    reclaimed,
                    entity_kind.value,
                )
        except Exception as exc:  # noqa: BLE001 - see the docstring
            logger.warning("[marketplace_audit] post-run purge failed for %s: %s", entity_kind.value, exc)

    # -- run row lifecycle -------------------------------------------------

    def _config_snapshot(self, entity_kind: EntityKind) -> Optional[dict]:
        """The validator config that produced this run, as JSON-safe dict."""
        cfg = self._configs.get(entity_kind)
        if cfg is None:
            return None
        try:
            return json.loads(json.dumps(cfg.__dict__, default=str, ensure_ascii=False))
        except Exception:  # noqa: BLE001 - snapshotting config is best-effort
            logger.warning("Could not serialise audit config for run of %s", entity_kind.value)
            return None

    def _open_run_row(
        self,
        *,
        run_id: str,
        entity_kind: EntityKind,
        started_at: datetime,
        trigger: str,
        triggered_by_id: Optional[str],
        similarity_backend: Optional[str],
        config_snapshot: Optional[dict],
        started_ts: float,
        es_index_name: Optional[str] = None,
    ) -> None:
        """Insert (or re-open, on replay) the run row with status RUNNING."""
        config_json = json.dumps(config_snapshot, ensure_ascii=False) if config_snapshot else None
        config_hash = hashlib.sha256(config_json.encode("utf-8")).hexdigest() if config_json else None
        values = {
            "id": run_id,
            "entity_kind": entity_kind.value,
            "schema_version": AUDIT_SCHEMA_VERSION,
            "status": RunStatus.RUNNING.value,
            "trigger": trigger,
            "triggered_by_id": triggered_by_id,
            "runner_id": _runner_id(),
            "started_at": started_at,
            # `now()`, not `started_at`: on a replay or a resumed run the row is
            # re-opened with the run's original start, and stamping that as the
            # heartbeat would move the liveness signal backwards -- straight
            # into the reaper's window.
            "heartbeat_at": func.now(),
            "records_processed": 0,
            "records_failed": 0,
            "engine_version": AUDIT_ENGINE_VERSION,
            "config_hash": config_hash,
            "similarity_backend": similarity_backend,
            "es_index_name": es_index_name,
            "error": None,
            "config_snapshot": config_snapshot,
            # Legacy float epoch — still NOT NULL in the database. See the
            # dual-write note on `MarketplaceAuditRun`.
            "started_ts": started_ts,
        }
        statement = pg_insert(MarketplaceAuditRun.__table__).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=[MarketplaceAuditRun.__table__.c.id],
            set_={k: statement.excluded[k] for k in values if k != "id"},
        )
        with get_session() as session:
            session.execute(statement)
            session.commit()

    def _close_run_row(
        self,
        *,
        run_id: str,
        finished_at: datetime,
        finished_ts: float,
        records_processed: int,
        total_records: int,
        engine_took_seconds: float,
        aggregates: dict,
        config_snapshot: Optional[dict],
        es_doc_count: Optional[int] = None,
        degraded_reason: Optional[str] = None,
    ) -> None:
        """Close the run with the status it actually earned, and its totals.

        `degraded_reason` is the §7 F2 hinge: a run whose similarity backend
        never answered closes `PARTIAL` with the reason on the row, not
        `SUCCEEDED` with an empty duplicates column. The gate verdicts it wrote
        are still correct and still persist -- that is exactly why the label is
        PARTIAL and not FAILED.
        """
        aggregates_json: Optional[str] = None
        try:
            aggregates_json = json.dumps(aggregates, default=str, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            logger.warning("Could not serialise audit aggregates for run %s", run_id)
        config_json = json.dumps(config_snapshot, ensure_ascii=False) if config_snapshot else None

        with get_session() as session:
            session.execute(
                sa_update(MarketplaceAuditRun.__table__)
                .where(MarketplaceAuditRun.__table__.c.id == run_id)
                .values(
                    status=(RunStatus.PARTIAL.value if degraded_reason else RunStatus.SUCCEEDED.value),
                    error=degraded_reason,
                    es_doc_count=es_doc_count,
                    finished_at=finished_at,
                    heartbeat_at=func.now(),
                    records_processed=records_processed,
                    total_records=total_records,
                    engine_took_seconds=engine_took_seconds,
                    aggregates=aggregates,
                    # Legacy mirrors, dual-written for `GET /audit/history` and
                    # for the §6.2 backfill that reads them.
                    finished_ts=finished_ts,
                    aggregates_json=aggregates_json,
                    config_snapshot_json=config_json,
                )
            )
            session.commit()

    def _fail_run_row(self, *, run_id: str, error: BaseException) -> None:
        """Mark the run FAILED with the error on the row.

        Uses its own session: the one that blew up is poisoned, and a run that
        cannot even record its own failure is worse than one that never started.
        """
        try:
            with get_session() as session:
                session.execute(
                    sa_update(MarketplaceAuditRun.__table__)
                    .where(MarketplaceAuditRun.__table__.c.id == run_id)
                    .values(
                        status=RunStatus.FAILED.value,
                        finished_at=func.now(),
                        heartbeat_at=func.now(),
                        error=f"{type(error).__name__}: {error}"[:4000],
                    )
                )
                session.commit()
        except Exception:  # noqa: BLE001 - the original error is what matters
            logger.exception("Could not mark audit run %s as FAILED", run_id)

    # -- child rows --------------------------------------------------------

    def _write_chunk(
        self,
        *,
        run_id: str,
        result: "AuditResult",
        users_by_id: dict,
        assistant_ids: list,
        processed_total: int,
    ) -> None:
        """Upsert one chunk of verdicts + their findings + their similarity rows.

        A single transaction: findings never outlive the verdict they belong to,
        which is what makes a crashed run's children consistent rather than
        merely present.
        """
        verdict_rows = [self._verdict_values(run_id, aid, result, users_by_id) for aid in assistant_ids]
        finding_rows: list[dict] = []
        similarity_rows: list[dict] = []
        for aid in assistant_ids:
            finding_rows.extend(self._finding_values(run_id, aid, result))
            similarity_rows.extend(self._similarity_values(run_id, aid, result))

        with get_session() as session:
            self._upsert(session, MarketplaceAuditVerdict.__table__, verdict_rows, ["audit_run_id", "assistant_id"])
            self._upsert(
                session, MarketplaceAuditFinding.__table__, finding_rows, ["audit_run_id", "assistant_id", "rule"]
            )
            self._upsert(
                session,
                MarketplaceAuditSimilarity.__table__,
                similarity_rows,
                ["audit_run_id", "assistant_id", "other_id"],
            )
            session.execute(
                sa_update(MarketplaceAuditRun.__table__)
                .where(MarketplaceAuditRun.__table__.c.id == run_id)
                .values(records_processed=processed_total, heartbeat_at=func.now())
            )
            session.commit()

    @staticmethod
    def _upsert(session, table, rows: list, key: list) -> None:
        """`INSERT ... ON CONFLICT (natural key) DO UPDATE` — §4.2 point 2."""
        if not rows:
            return
        statement = pg_insert(table).values(rows)
        updatable = [c.name for c in table.columns if c.name not in key]
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[table.c[k] for k in key],
                set_={name: statement.excluded[name] for name in updatable},
            )
        )

    @staticmethod
    def _verdict_values(run_id: str, aid: str, result: "AuditResult", users_by_id: dict) -> dict:
        verdict = result.verdicts[aid]
        usage_tier = result.usage_tiers.get(aid)
        gate_result = result.gate_results.get(aid)

        # verdict.bucket is a validator Bucket enum; the DB column is
        # models.Bucket. Both are str enums with identical values.
        db_bucket: Optional[str] = None
        if verdict.bucket is not None:
            try:
                db_bucket = DBBucket(verdict.bucket.value).value
            except ValueError:
                db_bucket = None

        cluster_id = result.purpose_cluster_id.get(aid)
        canonical_users = verdict.canonical_users
        if canonical_users is None and verdict.canonical_id:
            canonical_users = users_by_id.get(verdict.canonical_id)

        return {
            "audit_run_id": run_id,
            "assistant_id": aid,
            "bucket": db_bucket,
            "bucket_reason": verdict.bucket_reason,
            "verdict_kind": verdict.kind.value if verdict.kind else None,
            "cleanup_reason": verdict.reason.value if verdict.reason else None,
            "usage_tier": usage_tier.value if usage_tier else None,
            "users_count": users_by_id.get(aid),
            "canonical_id": verdict.canonical_id,
            "canonical_name": verdict.canonical_name,
            "canonical_users": canonical_users,
            # -1 is the engine's "singleton" marker, not a cluster.
            "purpose_cluster_id": cluster_id if (cluster_id is not None and cluster_id >= 0) else None,
            "match_score": verdict.match_score,
            "detail": verdict.detail or None,
            "critical_count": gate_result.critical_count if gate_result else 0,
            "optional_count": gate_result.optional_count if gate_result else 0,
        }

    @staticmethod
    def _finding_values(run_id: str, aid: str, result: "AuditResult") -> list:
        gate_result = result.gate_results.get(aid)
        if gate_result is None:
            return []
        # One row per (run, assistant, rule): the last finding wins if a gate
        # emitted the same rule twice, which is what the PK already implies.
        by_rule: dict[str, dict] = {}
        for finding in gate_result.findings:
            by_rule[finding.rule] = {
                "audit_run_id": run_id,
                "assistant_id": aid,
                "rule": finding.rule,
                "severity": finding.severity.value,
                "message": finding.message,
                "detail": finding.detail or None,
            }
        return list(by_rule.values())

    @staticmethod
    def _similarity_values(run_id: str, aid: str, result: "AuditResult") -> list:
        """The evidence behind a duplicate verdict, ES rows tagged as such.

        Engine pairs come first (they are what the verdict was aggregated from),
        then any ES neighbour the engine did not already know about.
        """
        rows: list[dict] = []
        seen: set = {aid}

        similarity = result.similarity_results.get(aid)
        pairs = similarity.pairs if similarity else []
        for pair in pairs:
            other = pair.b_id if pair.a_id == aid else pair.a_id
            if _claim(seen, other):
                rows.append(_pair_row(run_id, aid, other, pair, rank=len(rows) + 1))
            if len(rows) >= SIMILARITY_ROWS_PER_ENTITY:
                return rows

        for neighbour in result.purpose_neighbors.get(aid) or []:
            other = neighbour.get("other_id")
            if _claim(seen, other):
                rows.append(_neighbour_row(run_id, aid, other, neighbour, rank=len(rows) + 1))
            if len(rows) >= SIMILARITY_ROWS_PER_ENTITY:
                break
        return rows


def _claim(seen: set, other: Optional[str]) -> bool:
    """True the first time a non-empty *other* id is offered; records it in *seen*."""
    if not other or other in seen:
        return False
    seen.add(other)
    return True


def _pair_row(run_id: str, aid: str, other: str, pair, *, rank: int) -> dict:
    reason = pair.reason or ""
    return {
        "audit_run_id": run_id,
        "assistant_id": aid,
        "other_id": other,
        "other_name": None,
        "rank": rank,
        "combined": min(1.0, max(0.0, float(pair.combined))),
        "severity": pair.severity.value if pair.severity else None,
        "reason": pair.reason,
        # `source` answers "where did this pair come from". A clone
        # edge is the one row here that is not an inference — it was
        # read off a recorded clone event — so it must not be filed
        # under the lexical bucket with the Jaccard overlaps.
        "source": _SIMILARITY_SOURCES.get(reason, "LEXICAL"),
        "signals": dict(pair.signals or {}),
    }


def _neighbour_row(run_id: str, aid: str, other: str, neighbour: dict, *, rank: int) -> dict:
    return {
        "audit_run_id": run_id,
        "assistant_id": aid,
        "other_id": other,
        "other_name": neighbour.get("other_name"),
        "rank": rank,
        "combined": min(1.0, max(0.0, float(neighbour.get("cosine") or 0.0))),
        "severity": None,
        "reason": "es_knn",
        "source": "ES",
        "signals": {"cosine": neighbour.get("cosine")},
    }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

AUDIT_CACHE = AuditCache()
