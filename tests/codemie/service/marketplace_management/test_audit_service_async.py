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

"""Async-execution and cross-replica coordination behaviour of AuditCache.

A full audit takes minutes. It used to run inline inside the request handler,
so `POST /audit/run` held the connection open, starved the worker, and gave
the UI no way to distinguish "slow" from "never started". These tests pin the
background-execution contract that replaced it.

EXECUTION-PLAN 6.7 (F-17, F-49, F-50, R2) adds the second half: **all** of that
run-coordination state used to be a per-process dict on a module singleton, so
the idempotency guard, `is_running` and `latest_run_id` were per-worker and
per-restart, while `rollout.yaml` guarantees pod overlap on every deploy. The
claim is now a row in `mm_audit_run` and the enforcer is the partial unique
index `uq_mm_audit_run_active` (design-persistence.md 3.3, F8).

That makes these database tests, not dict tests: every one of them runs against
the real, alembic-migrated PostgreSQL from `conftest.mm_pg_engine`. A stubbed
session could not express the property at all -- it would be asserting the very
in-process check the defect is about.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import timedelta
from typing import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from codemie.service.marketplace_management.audit_service import (
    AuditAlreadyRunningError,
    AuditCache,
)
from codemie.service.marketplace_management.models import EntityKind

KIND = EntityKind.ASSISTANT
_SCHEMA = "codemie"


@pytest.fixture(scope="module")
def engine(mm_pg_engine: Engine) -> Engine:
    """The shared, alembic-migrated throwaway database."""
    return mm_pg_engine


@pytest.fixture(autouse=True)
def db(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """Point the application's `get_session()` at the throwaway database.

    Autouse, because `start_audit` is no longer a dict mutation: it claims a
    `mm_audit_run` row, and a claim that cannot reach PostgreSQL must fail
    loudly rather than pretend (EXECUTION-PLAN 5.3 removed exactly that swallow).
    Every test in this module therefore needs a database.
    """
    from codemie.clients import postgres as pg

    monkeypatch.setattr(pg.PostgresClient, "get_engine", classmethod(lambda cls: engine))
    _truncate_runs(engine)
    try:
        yield engine
    finally:
        _truncate_runs(engine)


def _truncate_runs(engine: Engine) -> None:
    with engine.connect() as conn:
        conn.execute(text(f"delete from {_SCHEMA}.mm_audit_run"))
        conn.commit()


def _insert_run(
    engine: Engine,
    *,
    status: str = "RUNNING",
    entity_kind: str = "ASSISTANT",
    heartbeat_age: str = "0 minutes",
    run_id: str | None = None,
) -> str:
    """Write a run row the way *another pod* would have, bypassing this process.

    This is the whole point of the cross-replica tests: nothing in this
    process's `AuditCache` has ever heard of this run.
    """
    run_id = run_id or str(uuid.uuid4())
    with engine.connect() as conn:
        conn.execute(
            text(
                f"insert into {_SCHEMA}.mm_audit_run "
                "(id, entity_kind, schema_version, status, trigger, runner_id, "
                " started_at, heartbeat_at, records_processed, records_failed, started_ts) "
                "values (:id, :k, 2, :s, 'MANUAL', 'other-pod/1', "
                "        now() - cast(:age as interval), now() - cast(:age as interval), "
                "        0, 0, extract(epoch from now()))"
            ),
            {"id": run_id, "k": entity_kind, "s": status, "age": heartbeat_age},
        )
        conn.commit()
    return run_id


def _run_rows(engine: Engine) -> list[dict]:
    with engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                text(
                    "select id, entity_kind, status, error, started_at, finished_at, "
                    "       heartbeat_at, engine_took_seconds, started_ts, finished_ts "
                    f"  from {_SCHEMA}.mm_audit_run order by started_at"
                )
            ).mappings()
        ]


@pytest.fixture
def cache() -> AuditCache:
    return AuditCache()


def test_start_audit_returns_before_the_run_finishes(cache, monkeypatch):
    """The call must not block for the duration of the audit."""
    release = threading.Event()
    started = threading.Event()

    def slow_run(kind, *, include_es_similarity=True, **_run_kwargs):
        started.set()
        release.wait(timeout=5)
        return None

    monkeypatch.setattr(cache, "run_audit", slow_run)

    began = time.time()
    status = cache.start_audit(KIND)
    elapsed = time.time() - began

    try:
        assert status["is_running"] is True
        # Returned essentially immediately rather than waiting on the run.
        assert elapsed < 1.0
        assert started.wait(timeout=5), "worker thread never started"
        assert cache.is_running(KIND) is True
    finally:
        release.set()


def test_second_start_does_not_launch_a_competing_run(cache, monkeypatch, engine):
    """A double-click, or a second admin, must not start a duplicate audit.

    The refusal is now a `409` carrying the in-flight run id rather than a `202`
    echoing the running run's status: an admin who clicked Run audit and got a
    202 has no way to tell "I started it" from "somebody else already had".
    """
    release = threading.Event()
    calls: list[int] = []

    def slow_run(kind, *, include_es_similarity=True, **_run_kwargs):
        calls.append(1)
        release.wait(timeout=5)
        return None

    monkeypatch.setattr(cache, "run_audit", slow_run)

    try:
        first = cache.start_audit(KIND)
        # Wait until the first run is genuinely in flight.
        for _ in range(50):
            if cache.is_running(KIND):
                break
            time.sleep(0.01)

        with pytest.raises(AuditAlreadyRunningError) as conflict:
            cache.start_audit(KIND)
        assert conflict.value.status_code == 409
        assert conflict.value.run_id == first["run_id"]
    finally:
        release.set()

    _wait_until_idle(cache)
    assert len(calls) == 1
    assert len(_run_rows(engine)) == 1, "the losing call still wrote a competing run row"


def test_flag_clears_and_error_is_recorded_when_the_run_raises(cache, monkeypatch, engine):
    """The worker thread has no caller to raise into, so a failure has to be
    readable from the status payload — otherwise the UI spins forever."""

    def boom(kind, *, include_es_similarity=True, **_run_kwargs):
        raise RuntimeError("embedding backend unavailable")

    monkeypatch.setattr(cache, "run_audit", boom)

    cache.start_audit(KIND)
    _wait_until_idle(cache)

    status = cache.audit_status(KIND)
    assert status["is_running"] is False
    assert "embedding backend unavailable" in status["error"]
    assert status["finished_at"] is not None

    # The claim it took at start must be released, or `uq_mm_audit_run_active`
    # keeps the whole deployment from ever auditing again.
    rows = _run_rows(engine)
    assert [r["status"] for r in rows] == ["FAILED"]
    assert "embedding backend unavailable" in (rows[0]["error"] or "")
    assert rows[0]["finished_at"] is not None


def test_status_is_readable_while_the_audit_holds_the_lock(cache, monkeypatch):
    """`run_audit` holds `_lock` for the whole run; a status poll that waited
    on that lock would block for exactly as long as the run it reports on."""
    release = threading.Event()

    def slow_run(kind, *, include_es_similarity=True, **_run_kwargs):
        with cache._lock:  # noqa: SLF001 - reproduces the real locking
            release.wait(timeout=5)
        return None

    monkeypatch.setattr(cache, "run_audit", slow_run)

    try:
        cache.start_audit(KIND)
        for _ in range(50):
            if cache.is_running(KIND):
                break
            time.sleep(0.01)

        began = time.time()
        status = cache.audit_status(KIND)
        assert time.time() - began < 1.0, "audit_status blocked on the audit lock"
        assert status["is_running"] is True
    finally:
        release.set()

    _wait_until_idle(cache)


def test_latest_run_id_does_not_block_on_the_audit_lock(cache, monkeypatch):
    """Same hazard as `audit_status`, on the path `GET /overview` actually takes.

    `run_audit` used to hold `_lock` for the whole (minutes-long) run and
    `latest_run_id` took the same lock for a single dict read, so every
    Overview load queued behind the audit — and under load the sync handlers
    waiting on it drained the shared threadpool (F-08).
    """
    release = threading.Event()

    def slow_run(kind, *, include_es_similarity=True, **_run_kwargs):
        with cache._lock:  # noqa: SLF001 - reproduces the real locking
            release.wait(timeout=5)
        return None

    monkeypatch.setattr(cache, "run_audit", slow_run)

    try:
        cache.start_audit(KIND)
        for _ in range(50):
            if cache.is_running(KIND):
                break
            time.sleep(0.01)

        began = time.time()
        run_id = cache.latest_run_id(KIND)
        elapsed = time.time() - began

        assert elapsed < 0.1, f"latest_run_id blocked on the audit lock ({elapsed:.2f}s)"
        assert run_id is None or isinstance(run_id, str)
    finally:
        release.set()

    _wait_until_idle(cache)


def test_a_second_audit_request_is_not_blocked_by_the_running_one(cache, monkeypatch):
    """`run_audit` must not hold `_lock` across the engine + ES + persist work.

    It used to wrap the whole (minutes-long) body, so every other caller that
    needs the lock queued behind it: a second admin clicking Run audit, or a
    double-click, blocked for the length of the run instead of getting back
    "already running" (F-08).

    Drives the REAL `run_audit` — only the record listing is slowed down, so
    the lock scope under test is the production one, not a stub's.
    """
    import codemie.service.marketplace_management.audit_service as audit_module

    listing = threading.Event()
    release = threading.Event()

    class _SlowAdapter:
        def list_records(self):
            listing.set()
            release.wait(timeout=5)
            return []

        def list_clone_edges(self):
            return []

    monkeypatch.setattr(audit_module, "get_adapter", lambda kind: _SlowAdapter())

    # Exactly what `start_audit` does before handing off to its worker.
    cache._running[KIND] = True  # noqa: SLF001
    worker = threading.Thread(
        target=cache.run_audit,
        args=(KIND,),
        kwargs={"include_es_similarity": False},
        daemon=True,
    )
    worker.start()

    try:
        assert listing.wait(timeout=5), "audit never reached its slow step"

        began = time.time()
        with pytest.raises(AuditAlreadyRunningError):
            cache.start_audit(KIND)
        elapsed = time.time() - began

        assert elapsed < 1.0, f"start_audit blocked on the audit lock ({elapsed:.2f}s)"
    finally:
        release.set()
        cache._running[KIND] = False  # noqa: SLF001
        worker.join(timeout=5)


def _wait_until_idle(cache: AuditCache, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not cache.is_running(KIND):
            return
        time.sleep(0.01)
    raise AssertionError("audit never finished")


# ---------------------------------------------------------------------------
# Rehydration
# ---------------------------------------------------------------------------
#
# The three mock-session tests that used to live here drove
# `_load_latest_from_db` with a hand-built row object and asserted that
# `per_rule_hits` and `bucket_usage_retention` came back out of the
# `aggregates_json` blob.
#
# That function is gone (EXECUTION-PLAN 5.4). `per_rule_hits` is no longer
# recovered from a JSON blob at all -- it is a `GROUP BY` over `mm_audit_finding`
# -- so a stubbed session that returns an invented row can no longer express the
# property. The same guarantee, plus the findings and similarity the old reader
# could not restore, is asserted against a real migrated PostgreSQL in
# `test_audit_persistence_roundtrip.py::test_cold_hydration_restores_the_whole_result`.


# ---------------------------------------------------------------------------
# EXECUTION-PLAN 6.7 — cross-replica coordination (F-17, F-49, F-50, R2)
# ---------------------------------------------------------------------------


def test_second_start_across_processes_is_409(cache, engine):
    """A run claimed by *another pod* must block this one — and the index is why.

    `rollout.yaml` guarantees pod overlap on every deploy, so the second admin
    is routinely served by a different worker than the first. The old guard was
    `self._running`, a dict on a module singleton: a brand-new process knows
    nothing about a run another process started, starts a competing full audit
    over the same catalogue, and both write snapshots (F-17, F8).

    The enforcer under test is the partial unique index
    `uq_mm_audit_run_active` (design-persistence.md 3.3), not an in-process
    check — which is why the row is inserted by raw SQL, the cache is fresh,
    and the assertions reach for the constraint name on the wrapped
    `IntegrityError`.
    """
    other_pods_run = _insert_run(engine, status="RUNNING")

    assert cache.is_running(KIND) is False, (
        "fixture is not exercising the cross-process case — this process already "
        "believes a run is in flight, so an in-process check could pass this test"
    )

    with pytest.raises(AuditAlreadyRunningError) as conflict:
        cache.start_audit(KIND, include_es_similarity=False)

    error = conflict.value
    assert error.status_code == 409
    assert error.run_id == other_pods_run, "the 409 does not carry the in-flight run id"
    # Postgres refused it, not Python.
    assert isinstance(
        error.__cause__, IntegrityError
    ), f"the conflict was not raised by the database ({error.__cause__!r})"
    assert (
        error.constraint == "uq_mm_audit_run_active"
    ), f"a different constraint refused the insert: {error.constraint!r}"
    detail = error.to_detail()
    assert detail["run_id"] == other_pods_run
    assert detail["entity_kind"] == "ASSISTANT"

    rows = _run_rows(engine)
    assert len(rows) == 1, f"a competing run row was written anyway: {rows}"


def test_a_run_for_another_entity_kind_is_not_blocked(cache, engine, monkeypatch):
    """The invariant is one active run *per kind*, not one per deployment."""
    _insert_run(engine, status="RUNNING", entity_kind="WORKFLOW")

    monkeypatch.setattr(
        cache,
        "run_audit",
        lambda kind, *, include_es_similarity=True, **_run_kwargs: None,
    )
    status = cache.start_audit(KIND, include_es_similarity=False)
    _wait_until_idle(cache)

    assert status["run_id"], "an ASSISTANT audit was refused by a WORKFLOW run"
    assert {r["entity_kind"] for r in _run_rows(engine)} == {"ASSISTANT", "WORKFLOW"}


def test_audit_status_run_id_is_the_current_run(cache, engine, monkeypatch):
    """`/audit/status` must describe the run in flight, not the previous one.

    `_run_ids[kind]` was assigned at the *end* of `run_audit`, and `_finished_at`
    was never cleared when a new run started. So while an audit was running the
    status endpoint returned the **previous** run's id together with the
    previous run's `finished_at` — a payload in which `finished_at` precedes
    `started_at` (F-49, F-50). The UI polls this to decide when to refresh, so
    it refreshed against evidence from the run before.
    """
    # Residue of an earlier run in this very process.
    previous_run_id = "previous-run-id"
    cache._run_ids[KIND] = previous_run_id  # noqa: SLF001
    cache._result_run_ids[KIND] = previous_run_id  # noqa: SLF001
    cache._finished_at[KIND] = time.time() - 3600  # noqa: SLF001

    release = threading.Event()

    def slow_run(kind, *, include_es_similarity=True, **_run_kwargs):
        release.wait(timeout=5)
        return None

    monkeypatch.setattr(cache, "run_audit", slow_run)

    try:
        cache.start_audit(KIND, include_es_similarity=False)
        for _ in range(100):
            if cache.is_running(KIND):
                break
            time.sleep(0.01)

        status = cache.audit_status(KIND)
        rows = _run_rows(engine)
        assert len(rows) == 1
        in_flight = rows[0]
        assert in_flight["status"] == "RUNNING"

        assert status["run_id"] != previous_run_id, "status still reports the previous run while a new one is in flight"
        assert status["run_id"] == in_flight["id"], "status run_id does not match the run row this process just claimed"
        assert status["finished_at"] is None, (
            "status carries the previous run's finished_at, so finished_at " "precedes started_at for the run it names"
        )
        assert in_flight["finished_at"] is None
        assert status["started_at"] is not None

        # ...and the *other* id stays put. `/overview` labels its numbers with
        # `latest_run_id`, and a run that has been claimed but has written no
        # verdict yet has produced no numbers to label. The two ids answer two
        # different questions; conflating them is what F-49/F-50 were.
        assert (
            cache.latest_run_id(KIND) != in_flight["id"]
        ), "/overview would claim its numbers came from a run that has not finished"
        assert cache.latest_run_id(KIND) == previous_run_id
    finally:
        release.set()

    _wait_until_idle(cache)


def test_stale_heartbeat_is_reaped(engine):
    """A pod that died mid-run must not lock the entity kind out forever (F4).

    The worker is a `daemon=True` bare thread, so it dies with its process
    leaving a `RUNNING` row that is indistinguishable from a live one — and
    that row holds `uq_mm_audit_run_active`, so no pod can ever start another
    audit of that kind. The reaper is the single statement in
    design-persistence.md 7 F4: promote to `FAILED` anything non-terminal whose
    heartbeat has gone quiet, which also releases the index.
    """
    from codemie.service.marketplace_management.audit_service import reap_stale_audit_runs

    abandoned = _insert_run(engine, status="RUNNING", heartbeat_age="30 minutes")
    healthy = _insert_run(engine, status="RUNNING", entity_kind="WORKFLOW", heartbeat_age="10 seconds")

    reaped = reap_stale_audit_runs(stale_after=timedelta(minutes=10))
    assert reaped == 1, f"the reaper promoted {reaped} run(s), expected exactly the abandoned one"

    rows = {r["id"]: r for r in _run_rows(engine)}
    assert rows[abandoned]["status"] == "FAILED"
    assert "heartbeat" in (rows[abandoned]["error"] or "").lower()
    assert rows[abandoned]["finished_at"] is not None, "a reaped run needs an end time"
    assert rows[healthy]["status"] == "RUNNING", "a live run was reaped"

    # The index is released: the next audit of that kind can now start.
    cache = AuditCache()
    claimed, _started = cache._claim_run(  # noqa: SLF001
        KIND, include_es_similarity=False
    )
    assert claimed and claimed != abandoned


def test_persisted_duration_covers_the_whole_run_not_just_the_engine(cache, engine, monkeypatch):
    """F-96: `started_at`/`finished_at` bracketed only `Engine.audit()`.

    The ES similarity phase runs *after* the engine and is the expensive half —
    a run the orchestrator measured at 269 seconds of wall clock was persisted
    as 0.5 s, under-reporting by ~500x, and that persisted number is what the
    Overview KPI shows an admin. The engine's own cost keeps its own column
    (`engine_took_seconds`); the run's timestamps must span the whole run.
    """
    es_phase_seconds = 0.6

    from codemie.service.marketplace_management import es_similarity

    def _slow_es_phase(cls) -> None:
        time.sleep(es_phase_seconds)

    monkeypatch.setattr(
        es_similarity.MarketplaceAssistantsIndex,
        "reset_similarity_circuit",
        classmethod(_slow_es_phase),
    )

    cache.run_audit(KIND, include_es_similarity=True)

    rows = _run_rows(engine)
    assert len(rows) == 1
    run = rows[0]
    total = (run["finished_at"] - run["started_at"]).total_seconds()
    engine_seconds = run["engine_took_seconds"] or 0.0

    assert total >= engine_seconds, (
        f"persisted total duration {total:.3f}s is shorter than the engine's own " f"{engine_seconds:.3f}s"
    )
    assert total >= es_phase_seconds, (
        f"persisted duration {total:.3f}s excludes the {es_phase_seconds}s similarity "
        "phase — this is the ~500x under-report the Overview KPI shows an admin"
    )
    # The legacy float mirrors that `GET /audit/history` publishes must agree.
    assert run["finished_ts"] - run["started_ts"] == pytest.approx(total, abs=1.0)


# ---------------------------------------------------------------------------
# Degradation must be labelled, never silently downgraded
# ---------------------------------------------------------------------------
#
# EXECUTION-PLAN 9.2 / design-persistence.md §7 F2 (F-25, F-27, R14).
#
# Elasticsearch failure used to be swallowed in three independent layers:
# `es_similarity.knn` returns `[]` when the store cannot be built and `[]`
# again when the query fails, and `run_audit` wrapped the whole similarity
# phase in `except Exception: logger.warning`. The run was then persisted
# `SUCCEEDED` with `similarity_backend='ES'`, so "no duplicates found" and
# "the duplicate detector never ran" produced byte-identical output.
#
# The corpus makes that worse than theoretical: the index holds 19 documents
# against 219 published assistants, so similarity already runs against 8.7% of
# the catalogue and reports the result as complete.


def _degradation_row(engine: Engine) -> dict:
    """The columns §7 F2 requires a degraded run to carry."""
    with engine.connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                text(
                    "select id, status, similarity_backend, es_doc_count, es_index_name, error "
                    f"  from {_SCHEMA}.mm_audit_run order by started_at"
                )
            ).mappings()
        ]
    assert len(rows) == 1, f"expected exactly one run row, got {len(rows)}"
    return rows[0]


class _DeadIndex:
    """An Elasticsearch that refuses every connection."""

    @classmethod
    def get_store(cls):
        raise ConnectionError("elasticsearch unreachable: connection refused")


class _LiveIndex:
    """An Elasticsearch that answers, holding *count* marketplace documents."""

    def __init__(self, count: int) -> None:
        self.count = count

    def client_count(self, **_kwargs):
        return {"count": self.count}

    def similarity_search_with_score(self, query_text, k):  # noqa: ARG002
        return []

    @property
    def client(self):
        outer = self

        class _Client:
            @staticmethod
            def count(**kwargs):
                return outer.client_count(**kwargs)

        return _Client()


def test_es_failure_marks_the_run_partial(cache, engine, monkeypatch):
    """ES down => PARTIAL / UNAVAILABLE, on the row and in `audit_status()`.

    The gate verdicts are still correct and still persist — that is why the
    run is PARTIAL and not FAILED. What must never happen again is the run
    reporting a clean success while the similarity half of the pipeline did
    not execute at all.
    """
    from codemie.service.marketplace_management import es_similarity

    monkeypatch.setattr(
        es_similarity.MarketplaceAssistantsIndex,
        "get_store",
        classmethod(lambda cls: _DeadIndex.get_store()),
    )

    cache.run_audit(KIND, include_es_similarity=True)

    run = _degradation_row(engine)
    assert run["status"] == "PARTIAL", (
        f"a run whose similarity backend never answered was persisted "
        f"{run['status']!r}; a degraded run must be labelled (§7 F2)"
    )
    assert (
        run["similarity_backend"] == "UNAVAILABLE"
    ), f"similarity_backend={run['similarity_backend']!r} claims similarity ran"
    assert run["error"], "the run carries no record of why it degraded"
    assert "elasticsearch" in run["error"].lower()

    status = cache.audit_status(KIND)
    assert status["status"] == "PARTIAL", f"audit_status() hides the degradation: {status}"
    assert status["similarity_backend"] == "UNAVAILABLE", status
    assert status["degraded"] is True, status


def test_es_doc_count_is_recorded_so_coverage_is_visible(cache, engine, monkeypatch):
    """ "similarity ran against 19 of 219" must be a number on the run.

    Without it the UI cannot distinguish a clean marketplace from a similarity
    pass that saw 8.7% of the corpus (F-27), which is the state the live index
    is in right now.
    """
    from codemie.service.marketplace_management import es_similarity

    live = _LiveIndex(count=19)
    monkeypatch.setattr(es_similarity.MarketplaceAssistantsIndex, "get_store", classmethod(lambda cls: live))

    cache.run_audit(KIND, include_es_similarity=True)

    run = _degradation_row(engine)
    assert run["es_doc_count"] == 19, (
        f"es_doc_count={run['es_doc_count']!r}; the run cannot say how much of the "
        "catalogue similarity actually compared against"
    )
    assert run["status"] == "SUCCEEDED", run
    assert run["similarity_backend"] == "ES", run
    assert cache.audit_status(KIND)["es_doc_count"] == 19


def test_skipping_similarity_on_purpose_is_not_a_degradation(cache, engine):
    """`include_es_similarity=False` is a choice, not a failure.

    Labelling it PARTIAL would make the label meaningless: every fast-path
    audit would look broken.
    """
    cache.run_audit(KIND, include_es_similarity=False)

    run = _degradation_row(engine)
    assert run["status"] == "SUCCEEDED", run
    assert run["similarity_backend"] == "NONE", run
    assert cache.audit_status(KIND)["degraded"] is False


def test_status_reports_a_degraded_run_started_by_another_process(cache, engine):
    """A restart, or a second pod, must not erase the degradation label.

    `audit_status()` reads per-process dicts. That is fine for `is_running` --
    a worker thread is a property of *this* process -- but the *outcome* of the
    last run is a property of the deployment, and `rollout.yaml` guarantees pod
    overlap on every deploy (F-17). Observed on the live stack: the container
    reloaded while an audit was in flight and `/audit/status` afterwards
    answered every field null, so a PARTIAL run that had just been written to
    `mm_audit_run` was invisible to the UI that had asked for it.
    """
    run_id = _insert_run(engine, status="PARTIAL")
    with engine.connect() as conn:
        conn.execute(
            text(
                f"update {_SCHEMA}.mm_audit_run "
                "   set similarity_backend='UNAVAILABLE', es_doc_count=null, "
                "       finished_at=now(), finished_ts=extract(epoch from now()), "
                "       error='ConnectionError: elasticsearch unreachable' "
                " where id=:id"
            ),
            {"id": run_id},
        )
        conn.commit()

    status = AuditCache().audit_status(KIND)

    assert status["run_id"] == run_id, status
    assert status["status"] == "PARTIAL", status
    assert status["similarity_backend"] == "UNAVAILABLE", status
    assert status["degraded"] is True, status
    assert "elasticsearch" in (status["error"] or "").lower(), status
    assert status["is_running"] is False, "no audit is running in *this* process"
