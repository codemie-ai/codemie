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

"""Audit persistence round-trip against a real, migrated PostgreSQL.

EXECUTION-PLAN 5.3; traces F-25, F-30, F-07.

Why a real database migrated by **alembic** and not `SQLModel.metadata.create_all`
(this is the whole point of the file):

The live defect these tests pin is an *ORM / schema* divergence. Migration
`n1a2u3d4i5t6` added `mm_audit_run.started_at TIMESTAMPTZ NOT NULL`, and
`MarketplaceAuditRun` went on declaring only the legacy `started_ts` float. Every
audit INSERT therefore died with `NotNullViolation`, and `audit_service.run_audit`
swallowed it in `except Exception: logger.warning`, so the API answered 202, the
status endpoint reported `error: null`, and `mm_audit_run` never grew a row.

`create_all` builds the schema *from the ORM*, so it reproduces whatever the ORM
believes and can never see that gap -- it is the mocked-session failure mode with
extra steps. Only a schema built by the migrations that production runs can.

Each module run creates its own database, migrates it to `head`, and drops it in
teardown; the dev database is never touched. If PostgreSQL is unreachable these
tests **fail**, they do not skip -- a skipped persistence test is
indistinguishable from a passing one on a dashboard (F-40).
"""

from __future__ import annotations

from typing import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlmodel import Session

from codemie.service.marketplace_management.models import EntityKind

# The throwaway-database bootstrap (`MM_TEST_PG_URL`, the alembic invocation
# and the unreachable-PostgreSQL message) lives in the package `conftest.py`
# as `mm_pg_engine`, shared with `test_audit_service_async.py`.
_SCHEMA = "codemie"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine(mm_pg_engine: Engine) -> Engine:
    """The shared throwaway database built by `conftest.mm_pg_engine`.

    It used to be created here. It moved to the package `conftest` unchanged so
    that `test_audit_service_async.py` -- which now asserts the same database
    invariants, because `uq_mm_audit_run_active` is what enforces them -- can
    share one migrated database instead of standing up a second one.
    """
    return mm_pg_engine


# Three assistants chosen so the deterministic gates have something to say:
# `broken-*` fail several CRITICAL checks (one-character name / description /
# system prompt, no categories), `sound-1` passes them all. What matters is not
# which rules fire but that whatever the engine produced reaches the database.
_SEED = (
    ("broken-1", "x", "x", "x", "[]"),
    ("broken-2", "Duplicate Named Helper", "Duplicate Named Helper", "y", "[]"),
    (
        "sound-1",
        "Release Notes Summariser",
        "Reads a merged pull request and writes the customer-facing release note "
        "for it, in the tone the documentation team already uses.",
        "You are a release-notes writer. Read the diff, identify user-visible "
        "changes, and write one short paragraph per change. Never invent a change "
        "that is not in the diff, and always name the affected component.",
        '["Development"]',
    ),
)


@pytest.fixture
def db(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    """Point the application's `get_session()` at the throwaway database.

    `audit_service` imports `get_session` from `codemie.clients.postgres` at call
    time and that helper builds its session from `PostgresClient.get_engine()`,
    so overriding the engine is enough for the production code path to run
    unmodified against this database.
    """
    from codemie.clients import postgres as pg

    monkeypatch.setattr(pg.PostgresClient, "get_engine", classmethod(lambda cls: engine))

    with engine.connect() as conn:
        for aid, name, description, prompt, categories in _SEED:
            conn.execute(
                text(
                    "insert into codemie.assistants "
                    "(id, name, description, system_prompt, project, shared, is_react, "
                    " created_date, creator, is_global, categories, marketplace_status, "
                    " unique_users_count, unique_likes_count, unique_dislikes_count, version_count) "
                    "values (:id, :n, :d, :p, 'demo', true, true, now(), 'seed-creator', true, "
                    "        cast(:c as jsonb), 'APPROVED', 3, 1, 0, 1)"
                ),
                {"id": aid, "n": name, "d": description, "p": prompt, "c": categories},
            )
        conn.commit()

    with Session(engine) as session:
        yield session
        session.rollback()

    with engine.connect() as conn:
        conn.execute(text(f"delete from {_SCHEMA}.mm_audit_run"))
        conn.execute(text(f"delete from {_SCHEMA}.assistants"))
        conn.commit()


def _scalar(engine: Engine, sql: str, **params: object) -> object:
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()


# ---------------------------------------------------------------------------
# 5.3 -- the live breakage
# ---------------------------------------------------------------------------


def test_audit_run_row_is_written(db: Session, engine: Engine) -> None:
    """A completed audit must leave a row in `mm_audit_run`.

    This is the regression test for the measured live breakage: `POST
    /audit/run` answers 202, `audit_status()` reports `error: null`, the
    container log carries `NotNullViolation`, and `mm_audit_run` does not move.
    RED here surfaces that violation instead of hiding it.
    """
    from codemie.service.marketplace_management.audit_service import AuditCache

    before = _scalar(engine, "select count(*) from codemie.mm_audit_run")
    cache = AuditCache()
    cache.run_audit(EntityKind.ASSISTANT, include_es_similarity=False)

    after = _scalar(engine, "select count(*) from codemie.mm_audit_run")
    assert after == before + 1, "the audit ran but persisted no mm_audit_run row"

    row = None
    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    "select id, status, entity_kind, schema_version, records_processed, "
                    "       started_at, finished_at, total_records "
                    "  from codemie.mm_audit_run order by started_at desc limit 1"
                )
            )
            .mappings()
            .first()
        )

    assert row is not None
    assert row["status"] == "SUCCEEDED"
    assert row["entity_kind"] == "ASSISTANT"
    assert row["schema_version"] >= 2, "a v1 run is invisible to the pinned read path"
    assert row["started_at"] is not None and row["finished_at"] is not None
    assert row["records_processed"] == len(_SEED)
    assert row["total_records"] == len(_SEED)


def test_findings_are_persisted_per_rule(db: Session, engine: Engine) -> None:
    """Every `Finding` the engine produced becomes an `mm_audit_finding` row.

    Today the findings live only inside a Python `GateResult` and are discarded
    when the process ends, which is why a cold `GET /cleanup` reports 0 critical
    failures for assistants the audit had just flagged (F-30).
    """
    from codemie.service.marketplace_management.audit_service import AuditCache

    cache = AuditCache()
    result = cache.run_audit(EntityKind.ASSISTANT, include_es_similarity=False)
    run_id = cache.latest_run_id(EntityKind.ASSISTANT)

    expected: dict[tuple[str, str], str] = {}
    for aid, gate_result in result.gate_results.items():
        for finding in gate_result.findings:
            expected[(aid, finding.rule)] = finding.severity.value
    assert expected, "the seeded catalogue produced no findings — fixture is not exercising the gates"

    with engine.connect() as conn:
        rows = conn.execute(
            text("select assistant_id, rule, severity from codemie.mm_audit_finding " " where audit_run_id = :r"),
            {"r": run_id},
        ).all()

    persisted = {(r[0], r[1]): r[2] for r in rows}
    assert len(rows) == len(expected), f"engine produced {len(expected)} findings, database holds {len(rows)}"
    assert persisted == expected, "a finding reached the database with the wrong severity"


def test_persist_failure_fails_the_run(db: Session, engine: Engine, monkeypatch) -> None:
    """A persistence failure must fail the run loudly, not report a clean finish.

    `run_audit` wrapped `_persist` in `except Exception: logger.warning`, so the
    exact breakage this file exists for looked, from the API, like a successful
    audit (F-25).
    """
    from codemie.service.marketplace_management.audit_service import AuditCache

    cache = AuditCache()

    def _boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(AuditCache, "_write_chunk", _boom)

    cache.start_audit(EntityKind.ASSISTANT, include_es_similarity=False)
    for _ in range(600):
        if not cache.is_running(EntityKind.ASSISTANT):
            break
        import time as _t

        _t.sleep(0.1)

    status = cache.audit_status(EntityKind.ASSISTANT)
    assert status["is_running"] is False
    assert status["error"], "a failed persist reported a clean finish"
    assert "disk on fire" in status["error"]

    run_status = _scalar(engine, "select status from codemie.mm_audit_run order by started_at desc limit 1")
    assert run_status == "FAILED", f"run row status is {run_status!r}, expected 'FAILED'"


def test_partial_run_survives_a_crash(db: Session, engine: Engine, monkeypatch) -> None:
    """A hard kill mid-persist leaves partial-but-consistent children.

    One commit at the end means a crash at 95 % stores nothing (design-persistence
    §4.2 point 3). With chunked commits the run keeps a truthful
    `records_processed` and every persisted child belongs to a persisted verdict,
    which is what the §7 reaper needs in order to promote the run to `PARTIAL`.
    """
    from codemie.service.marketplace_management import audit_service as svc

    monkeypatch.setattr(svc, "PERSIST_CHUNK_SIZE", 1)

    original = svc.AuditCache._write_chunk
    calls = {"n": 0}

    def _die_after_first_chunk(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] > 1:
            # BaseException: `except Exception` cannot rescue it, which is the
            # point — this models SIGKILL, not a handled error.
            raise KeyboardInterrupt("worker killed")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(svc.AuditCache, "_write_chunk", _die_after_first_chunk)

    cache = svc.AuditCache()
    with pytest.raises(KeyboardInterrupt):
        cache.run_audit(EntityKind.ASSISTANT, include_es_similarity=False)

    with engine.connect() as conn:
        run = (
            conn.execute(
                text(
                    "select id, records_processed, heartbeat_at from codemie.mm_audit_run "
                    " order by started_at desc limit 1"
                )
            )
            .mappings()
            .first()
        )
        assert run is not None, "the crash left no run row at all"
        assert run["records_processed"] > 0, "a chunked persist recorded no progress"
        assert run["heartbeat_at"] is not None, "the reaper has no heartbeat to judge staleness by"

        verdicts = conn.execute(
            text("select count(*) from codemie.mm_audit_verdict where audit_run_id = :r"),
            {"r": run["id"]},
        ).scalar()
        assert verdicts == run["records_processed"]

        orphan_findings = conn.execute(
            text(
                "select count(*) from codemie.mm_audit_finding f "
                " where f.audit_run_id = :r and not exists ("
                "   select 1 from codemie.mm_audit_verdict v "
                "    where v.audit_run_id = f.audit_run_id and v.assistant_id = f.assistant_id)"
            ),
            {"r": run["id"]},
        ).scalar()
        assert orphan_findings == 0, "findings were committed for assistants with no verdict row"


def test_reaudit_is_idempotent_within_a_run(db: Session, engine: Engine) -> None:
    """Re-persisting the same run converges instead of erroring (§4.2 point 2).

    The natural keys are `(run, assistant)`, `(run, assistant, rule)` and
    `(run, assistant, other)`; a retried or resumed run must upsert onto them.
    """
    from codemie.service.marketplace_management.audit_service import AuditCache

    cache = AuditCache()
    result = cache.run_audit(EntityKind.ASSISTANT, include_es_similarity=False)
    run_id = cache.latest_run_id(EntityKind.ASSISTANT)

    verdicts_before = _scalar(engine, "select count(*) from codemie.mm_audit_verdict where audit_run_id = :r", r=run_id)
    # Replay the very same run id — this is what a resumed worker does.
    cache._persist(
        run_id=run_id,
        entity_kind=EntityKind.ASSISTANT,
        result=result,
        records=[],
        started_ts=1.0,
        finished_ts=2.0,
    )
    verdicts_after = _scalar(engine, "select count(*) from codemie.mm_audit_verdict where audit_run_id = :r", r=run_id)
    assert verdicts_after == verdicts_before


def test_users_count_is_populated(db: Session, engine: Engine) -> None:
    """`mm_audit_verdict.users_count` carries the adoption behind the row.

    It was hardcoded `None`, so every consumer that wanted "how many people
    would this delete affect" had to re-read the assistants table.
    """
    from codemie.service.marketplace_management.audit_service import AuditCache

    cache = AuditCache()
    cache.run_audit(EntityKind.ASSISTANT, include_es_similarity=False)
    run_id = cache.latest_run_id(EntityKind.ASSISTANT)

    total = _scalar(engine, "select count(*) from codemie.mm_audit_verdict where audit_run_id = :r", r=run_id)
    assert total == len(_SEED), "no verdict rows were written, so this assertion would be vacuous"
    nulls = _scalar(
        engine,
        "select count(*) from codemie.mm_audit_verdict " " where audit_run_id = :r and users_count is null",
        r=run_id,
    )
    assert nulls == 0, f"{nulls} verdict rows still carry a null users_count"


def test_cold_cache_cleanup_row_has_nonzero_critical_count(db: Session, engine: Engine) -> None:
    """A restart must not zero the per-row critical count (F-30, F-07).

    A **fresh** `AuditCache` — exactly the first request after a process restart
    — must serve a Cleanup page whose rows still carry the number of critical
    checks each entity failed. Today every row reads 0 because the counts live
    only in the in-process `GateResult`.
    """
    from codemie.rest_api.routers import marketplace_management as mm
    from codemie.service.marketplace_management.audit_service import AuditCache

    AuditCache().run_audit(EntityKind.ASSISTANT, include_es_similarity=False)

    payload = mm._cleanup_payload(
        EntityKind.ASSISTANT, None, None, None, None, None, None, None, None, None, None, page=1, page_size=50
    )
    assert payload["items"], "the cleanup page is empty for a catalogue that was just audited"
    assert any(row["critical_count"] > 0 for row in payload["items"]), (
        "every row reports critical_count=0 after a restart, " "although the audit recorded critical findings"
    )


def test_cold_hydration_restores_the_whole_result(db: Session, engine: Engine) -> None:
    """A fresh cache must rebuild findings, verdicts and similarity from SQL.

    The old rehydrator returned `gate_results={}` and `similarity_results={}`
    because there was nowhere to read them from, and recovered `per_rule_hits`
    from a JSON blob on the run row. Both tables exist now, so the read is a
    `SELECT`, and the property it guarantees is stronger: what the engine
    produced is what a restarted process sees (F-07, F-30).
    """
    from codemie.service.marketplace_management.audit_service import AuditCache

    produced = AuditCache().run_audit(EntityKind.ASSISTANT, include_es_similarity=False)

    cold = AuditCache()
    rehydrated = cold._hydrate_latest_run(EntityKind.ASSISTANT)

    assert rehydrated is not None, "the run just written was not found"
    assert set(rehydrated.verdicts) == set(produced.verdicts)
    assert rehydrated.total == produced.total

    produced_findings = {
        (aid, f.rule): f.severity.value for aid, gr in produced.gate_results.items() for f in gr.findings
    }
    rehydrated_findings = {
        (aid, f.rule): f.severity.value for aid, gr in rehydrated.gate_results.items() for f in gr.findings
    }
    assert rehydrated_findings == produced_findings

    # `per_rule_hits` is now a projection of the findings, not a JSON blob.
    assert {rule: sorted(ids) for rule, ids in rehydrated.per_rule_hits.items()} == {
        rule: sorted(ids) for rule, ids in produced.per_rule_hits.items()
    }
    assert rehydrated.bucket_usage_retention == produced.bucket_usage_retention

    # Every verdict that was flagged is still flagged, for the same reason.
    for aid, verdict in produced.verdicts.items():
        assert rehydrated.verdicts[aid].reason == verdict.reason
        assert rehydrated.verdicts[aid].bucket == verdict.bucket


# NOTE — the test that used to sit here, `test_an_unknown_reason_code_does_not_
# break_rehydration`, has been deleted rather than ported.
#
# It wrote `cleanup_reason = 'from_the_future'` and required the reader to
# degrade that row to "no reason" instead of raising. Migration `n1a2u3d4i5t6`
# added `ck_mm_audit_verdict_reason` (`cleanup_reason IS NULL OR cleanup_reason
# IN (...)`), so the database now rejects the write outright: the scenario is
# unreachable, and a test that fabricated it by disabling the constraint would
# be asserting against a schema nothing runs. The defensive `_enum` degrade
# stays in `_hydrate_latest_run` — a CHECK constraint can be dropped, and the
# reader should not be the thing that discovers it — but the guarantee now lives
# in the schema, which is the right place for it.
