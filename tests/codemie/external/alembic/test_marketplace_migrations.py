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

"""Marketplace migration round-trip against a real, disposable PostgreSQL.

Traces F-52, F-53, R9.

Why a real database (F-52):

`mm_audit_verdict.audit_run_id` is a foreign key in the ORM
(`service/marketplace_management/models.py:83`); only `pg_constraint` can
confirm the database actually enforces it, and `ON DELETE CASCADE` retention
depends on `confdeltype` being a property of the installed constraint.

Why the round-trip and not just an upgrade (F-53):

Alembic will happily accept a `downgrade()` that drops a column it never
added, or forgets one it did; the only proof is `upgrade -> downgrade ->
upgrade` against a real database.

The 14 revisions this module used to walk one at a time
(`5df1d2e53fbf` ... `p2a3r4t5i6a7`) were squashed into a single revision,
`f3e2d1c0b9a8`, because none of them had ever been applied to a shared or
production database -- this was brand-new, unreleased functionality, so the
squash is a safe, destructive rewrite rather than a conventional follow-up
migration. Every test here was rewritten to exercise that one revision's
`upgrade()`/`downgrade()` instead of the chain: there is no more "legacy run"
state to seed, no backfill to recover, and no intermediate index form to
replace, because the squashed migration creates every table and index in its
final, correct shape directly.

Each test module run creates its own database and drops it in teardown, so no
state survives the session and the dev database is never touched. If PostgreSQL
is unreachable these tests **fail**, they do not skip -- a skipped migration
test is indistinguishable from a passing one on a dashboard (F-40).
"""

from __future__ import annotations

import ast
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Iterator

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import IntegrityError
from sqlmodel import create_engine

# The dev stack from `docker-compose.yml` (postgres/password on :5432).
# `pytest.ini` deliberately points PG_URL at a dead port, so this cannot read
# the application config -- it is its own, overridable, connection.
_ADMIN_URL = os.getenv("MM_TEST_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")
_SCHEMA = "codemie"

# The revision live on `main` before the squashed marketplace-management
# migration lands. The fixture builds the disposable database up to exactly
# this point, so `upgrade head` below is the one migration under test.
PRE_SQUASH_REVISION = "e7b3a91c4d58"
SQUASHED_REVISION = "f3e2d1c0b9a8"

_REPO_ROOT = Path(__file__).resolve().parents[4]
_ALEMBIC_DIR = _REPO_ROOT / "src" / "external" / "alembic"
_VERSIONS_DIR = _ALEMBIC_DIR / "versions"
# The long-lived alembic process this module drives; see `_AlembicWorker`.
_WORKER_SCRIPT = Path(__file__).resolve().parent / "_alembic_worker.py"

_ASSISTANTS_STATUS_INDEX = "ix_assistants_marketplace_status"
_PENDING_QUEUE_INDEX = "ix_assistants_marketplace_pending_queue"
_CLONE_INDEX = "ix_assistant_clone_event_clone_assistant_id"

_UNREACHABLE = """\
Cannot reach PostgreSQL at {url}.

These tests assert real DDL against a real server and are meaningless without
one, so they skip when the database is unavailable. Start the dev stack:

    cd codemie && docker compose up -d postgres

or point this file at another instance with MM_TEST_PG_URL.

Underlying error: {err}
"""


# ---------------------------------------------------------------------------
# Running alembic (EXECUTION-PLAN 13.2 item 3)
# ---------------------------------------------------------------------------
#
# Still a subprocess, for the reason it always was: `env.py` starts
# `unittest.mock` patches against `alembic_postgresql_enum` at import time and
# never stops them, reloads two of that package's submodules, and builds its
# engine from the process-wide `codemie.configs.config`. Running it inside the
# pytest process would leak all of that into the rest of the suite.
#
# There is exactly one alembic process per module run, and the commands are
# fed to it one at a time over a line protocol (`_alembic_worker.py`), so this
# module does not pay the application import cost once per `alembic` call.


class _AlembicWorker:
    """One long-lived alembic process, one command at a time.

    Failures come back as a traceback rather than an exit code, so `_alembic_ok`
    reconstructs the same `AssertionError` an operator-visible non-zero exit
    used to produce, and appends the tail of the worker's log — which is where
    alembic's own output and every application log line now go.
    """

    _LOG_TAIL_CHARS = 8000

    def __init__(self) -> None:
        self._log = tempfile.NamedTemporaryFile(  # noqa: SIM115 - closed in close()
            mode="w+", suffix=".alembic-worker.log", delete=False
        )
        env = dict(os.environ)
        env.setdefault("PYTHONUNBUFFERED", "1")
        self._proc = subprocess.Popen(
            [sys.executable, str(_WORKER_SCRIPT)],
            cwd=str(_ALEMBIC_DIR),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._log,
            text=True,
            bufsize=1,
        )

    def log_tail(self) -> str:
        self._log.flush()
        with open(self._log.name, encoding="utf-8", errors="replace") as handle:
            return handle.read()[-self._LOG_TAIL_CHARS :]

    def run(self, url: str, args: tuple[str, ...]) -> tuple[bool, str, str]:
        """`(ok, reported output, error)` for one alembic command."""
        if self._proc.poll() is not None:
            raise AssertionError(
                "the alembic worker died before `alembic "
                f"{' '.join(args)}` could run (exit {self._proc.returncode}).\n"
                f"--- worker log ---\n{self.log_tail()}"
            )
        assert self._proc.stdin is not None and self._proc.stdout is not None
        self._proc.stdin.write(json.dumps({"url": url, "args": list(args)}) + "\n")
        self._proc.stdin.flush()

        line = self._proc.stdout.readline()
        if not line:
            raise AssertionError(
                f"the alembic worker answered nothing for `alembic {' '.join(args)}`.\n"
                f"--- worker log ---\n{self.log_tail()}"
            )
        response = json.loads(line)
        return bool(response.get("ok")), response.get("output") or "", response.get("error") or ""

    def close(self) -> None:
        if self._proc.poll() is None:
            try:
                assert self._proc.stdin is not None
                self._proc.stdin.close()
                self._proc.wait(timeout=30)
            except Exception:  # noqa: BLE001 - teardown must not mask a test failure
                self._proc.kill()
        self._log.close()
        with contextlib.suppress(OSError):  # pragma: no cover - best effort
            os.unlink(self._log.name)


_WORKER: "_AlembicWorker | None" = None


def _worker() -> _AlembicWorker:
    global _WORKER
    if _WORKER is None:
        _WORKER = _AlembicWorker()
    return _WORKER


@pytest.fixture(scope="module", autouse=True)
def _alembic_worker_lifetime() -> Iterator[None]:
    """One alembic process for this module, shut down with it.

    Autouse and module-scoped rather than a global: a worker that outlived the
    module would hold an idle interpreter (and a connection pool) for the rest
    of the session.
    """
    yield
    global _WORKER
    if _WORKER is not None:
        _WORKER.close()
        _WORKER = None


def _run_alembic(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one alembic command against `url`, in the module's alembic process.

    Returns a `CompletedProcess` because that is what the call sites read: an
    exit code and the text alembic reported. It is synthesised from the worker's
    answer rather than from a fresh `python -m alembic`, which is the whole
    saving; `returncode` is 0/1 with the traceback on `stderr`.
    """
    ok, output, error = _worker().run(url, args)
    return subprocess.CompletedProcess(
        args=["alembic", *args],
        returncode=0 if ok else 1,
        stdout=output,
        stderr=error,
    )


def _alembic_ok(url: str, *args: str) -> str:
    ok, output, error = _worker().run(url, args)
    if not ok:
        raise AssertionError(
            "alembic {args} failed\n--- reported ---\n{out}\n--- error ---\n{err}\n" "--- worker log ---\n{log}".format(
                args=" ".join(args), out=output, err=error, log=_worker().log_tail()
            )
        )
    return output


def _script_head() -> str:
    """The head of the migration chain on disk, read without running `env.py`."""
    cfg = Config(str(_ALEMBIC_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    assert head is not None
    return head


@pytest.fixture(scope="module")
def disposable_db() -> Iterator[str]:
    """A throwaway database migrated to the squashed migration's head, dropped on teardown."""
    admin_url = make_url(_ADMIN_URL)
    db_name = f"mm_mig_test_{uuid.uuid4().hex[:10]}"
    try:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
        with admin.connect() as conn:
            conn.execute(text(f'create database "{db_name}"'))
    except Exception as exc:  # noqa: BLE001 - reported verbatim below
        pytest.skip(_UNREACHABLE.format(url=admin_url.render_as_string(hide_password=True), err=exc))

    target_url = admin_url.set(database=db_name)
    try:
        setup = create_engine(target_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
        with setup.connect() as conn:
            conn.execute(text(f"create schema if not exists {_SCHEMA}"))
            conn.execute(text("create extension if not exists vector"))
        setup.dispose()

        rendered = target_url.render_as_string(hide_password=False)
        _alembic_ok(rendered, "upgrade", "head")
        yield rendered
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'drop database if exists "{db_name}" with (force)'))
        admin.dispose()


@pytest.fixture(scope="module")
def engine(disposable_db: str) -> Iterator[Engine]:
    eng = create_engine(
        disposable_db,
        pool_pre_ping=True,
        connect_args={"options": f"-c search_path={_SCHEMA},public"},
    )
    yield eng
    eng.dispose()


def _seed_two_good_runs(engine: Engine) -> None:
    """Two runs with verdicts.

    `mm_audit_verdict.audit_run_id` carries a real FK from creation in the
    squashed migration, so an orphaned verdict row cannot exist here the way
    it could during the original multi-step history -- the FK blocks the
    insert outright.

    Pulled out of the `seeded` fixture so the round-trip tests (which drop
    and recreate these tables via `downgrade`/`upgrade`) can reseed after
    restoring `head`, instead of leaving every later test with an empty
    table because the module-scoped fixture only ran once at module setup.
    """
    now = time.time()
    with engine.connect() as conn:
        for idx, run_id in enumerate(("run-keep-a", "run-keep-b")):
            conn.execute(
                text(
                    "insert into codemie.mm_audit_run "
                    "(id, entity_kind, started_ts, finished_ts, started_at, finished_at, "
                    " status, schema_version, total_records, engine_took_seconds) "
                    "values (:id, 'ASSISTANT', :s, :f, now(), now(), 'SUCCEEDED', 2, 2, 0.5)"
                ),
                {"id": run_id, "s": now + idx, "f": now + idx + 1},
            )
            for n in range(2):
                conn.execute(
                    text(
                        "insert into codemie.mm_audit_verdict "
                        "(audit_run_id, assistant_id, bucket, verdict_kind, cleanup_reason, usage_tier) "
                        "values (:r, :a, 'DELETE', 'CANDIDATE_FOR_CLEANUP', 'invalid', 'DEAD')"
                    ),
                    {"r": run_id, "a": f"{run_id}-assistant-{n}"},
                )
        conn.commit()


@pytest.fixture(scope="module", autouse=True)
def seeded(engine: Engine, disposable_db: str) -> None:
    _seed_two_good_runs(engine)


def _scalar(engine: Engine, sql: str) -> object:
    with engine.connect() as conn:
        return conn.execute(text(sql)).scalar()


def _current_revision(engine: Engine) -> str:
    return str(_scalar(engine, "select version_num from codemie.alembic_version"))


# ---------------------------------------------------------------------------
# The round trip
# ---------------------------------------------------------------------------


def test_upgrade_then_downgrade_then_upgrade_is_clean(disposable_db: str, engine: Engine) -> None:
    """`upgrade head -> downgrade -1 -> upgrade head` runs clean and lands the FK.

    The FK assertion is the point: `confdeltype='c'` is `ON DELETE CASCADE`, the
    property `test_deleting_a_run_cascades_to_all_children` depends on. Without
    it, deleting a run leaves its verdicts behind forever.
    """
    _alembic_ok(disposable_db, "upgrade", "head")
    head_revision = _current_revision(engine)
    assert head_revision == _script_head()

    _alembic_ok(disposable_db, "downgrade", PRE_SQUASH_REVISION)
    assert _current_revision(engine) == PRE_SQUASH_REVISION

    _alembic_ok(disposable_db, "upgrade", "head")
    assert _current_revision(engine) == head_revision

    confdeltype = _scalar(
        engine,
        "select confdeltype from pg_constraint where conname = 'fk_mm_audit_verdict_run'",
    )
    assert confdeltype == "c", (
        "fk_mm_audit_verdict_run must be ON DELETE CASCADE (confdeltype='c'); " f"pg_constraint reports {confdeltype!r}"
    )

    # The downgrade/upgrade cycle above dropped and recreated mm_audit_run /
    # mm_audit_verdict, wiping the module-scoped `seeded` fixture's rows.
    # Reseed so later tests in this module still see them.
    _seed_two_good_runs(engine)


def test_downgrade_restores_the_pre_squash_shape(disposable_db: str, engine: Engine) -> None:
    """The squashed migration's downgrade removes everything it added, and only that."""
    _alembic_ok(disposable_db, "upgrade", "head")

    _alembic_ok(disposable_db, "downgrade", PRE_SQUASH_REVISION)
    assert _current_revision(engine) == PRE_SQUASH_REVISION

    with engine.connect() as conn:
        leftover_tables = conn.execute(
            text(
                "select table_name from information_schema.tables "
                "where table_schema = 'codemie' and table_name in "
                "('mm_audit_run', 'mm_audit_verdict', 'mm_audit_finding', 'mm_audit_similarity', "
                " 'mm_llm_assessment', 'mm_review_decision')"
            )
        ).all()
        assistants_columns = {
            r[0]
            for r in conn.execute(
                text(
                    "select column_name from information_schema.columns "
                    "where table_schema = 'codemie' and table_name = 'assistants'"
                )
            ).all()
        }
        clone_event_columns = {
            r[0]
            for r in conn.execute(
                text(
                    "select column_name from information_schema.columns "
                    "where table_schema = 'codemie' and table_name = 'assistant_clone_event'"
                )
            ).all()
        }
        fk = conn.execute(text("select 1 from pg_constraint where conname = 'fk_mm_audit_verdict_run'")).first()

    assert leftover_tables == [], f"downgrade left tables behind: {leftover_tables}"
    assert not (
        assistants_columns
        & {
            "marketplace_status",
            "marketplace_version",
            "is_verified",
            "marketplace_decided_at",
            "marketplace_decided_by",
            "marketplace_decision_reason",
            "marketplace_submitted_at",
        }
    ), f"downgrade left marketplace columns on assistants: {assistants_columns}"
    assert "clone_assistant_id" not in clone_event_columns
    assert fk is None, "fk_mm_audit_verdict_run survived the downgrade"

    _alembic_ok(disposable_db, "upgrade", "head")
    assert _current_revision(engine) == _script_head()

    # Same reseed as the other round-trip test, for the same reason: the
    # downgrade/upgrade cycle above wiped the `seeded` fixture's rows.
    _seed_two_good_runs(engine)


def test_run_lifecycle_columns_exist_with_the_designed_types(engine: Engine) -> None:
    """The lifecycle columns design-persistence.md names, and their types."""
    expected = {
        "schema_version": "smallint",
        "status": "character varying",
        "trigger": "character varying",
        "triggered_by_id": "character varying",
        "runner_id": "text",
        "started_at": "timestamp with time zone",
        "finished_at": "timestamp with time zone",
        "heartbeat_at": "timestamp with time zone",
        "records_processed": "integer",
        "records_failed": "integer",
        "engine_version": "character varying",
        "config_hash": "character varying",
        "similarity_backend": "character varying",
        "es_index_name": "text",
        "es_doc_count": "integer",
        "error": "text",
        "config_snapshot": "jsonb",
        "aggregates": "jsonb",
    }
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "select column_name, data_type from information_schema.columns "
                "where table_schema = 'codemie' and table_name = 'mm_audit_run'"
            )
        ).all()
    actual = dict(rows)
    missing = {c: t for c, t in expected.items() if actual.get(c) != t}
    assert not missing, f"mm_audit_run column mismatch: {missing} (actual: {actual})"


def test_verdict_fk_prevents_orphans(engine: Engine) -> None:
    """The FK declared with the table makes an orphaned verdict row impossible."""
    survivors = _scalar(engine, "select count(*) from codemie.mm_audit_verdict")
    assert survivors == 4, f"expected the 4 parented verdicts to survive, found {survivors}"
    orphans = _scalar(
        engine,
        "select count(*) from codemie.mm_audit_verdict v "
        "where not exists (select 1 from codemie.mm_audit_run r where r.id = v.audit_run_id)",
    )
    assert orphans == 0


def test_new_child_tables_exist_and_cascade_from_the_run(engine: Engine) -> None:
    """mm_audit_finding / mm_audit_similarity / mm_llm_assessment, with the right FK action."""
    with engine.connect() as conn:
        tables = {
            r[0]
            for r in conn.execute(
                text(
                    "select table_name from information_schema.tables "
                    "where table_schema = 'codemie' and table_name in "
                    "('mm_audit_finding', 'mm_audit_similarity', 'mm_llm_assessment')"
                )
            ).all()
        }
        actions = dict(
            conn.execute(
                text(
                    "select c.conrelid::regclass::text, c.confdeltype from pg_constraint c "
                    "join pg_class t on t.oid = c.conrelid "
                    "join pg_namespace n on n.oid = t.relnamespace "
                    "where c.contype = 'f' and n.nspname = 'codemie' "
                    "and c.confrelid = 'codemie.mm_audit_run'::regclass"
                )
            ).all()
        )
    assert tables == {"mm_audit_finding", "mm_audit_similarity", "mm_llm_assessment"}
    assert actions.get("mm_audit_finding") == "c", "findings must cascade with their run"
    assert actions.get("mm_audit_similarity") == "c", "similarity rows must cascade with their run"
    assert actions.get("mm_llm_assessment") == "n", "an LLM assessment outlives its run (SET NULL)"


def test_verdict_gains_the_denormalised_counts_and_cluster(engine: Engine) -> None:
    """critical_count / optional_count / canonical_users / purpose_cluster_id."""
    with engine.connect() as conn:
        rows = dict(
            conn.execute(
                text(
                    "select column_name, is_nullable from information_schema.columns "
                    "where table_schema = 'codemie' and table_name = 'mm_audit_verdict'"
                )
            ).all()
        )
    assert rows.get("critical_count") == "NO"
    assert rows.get("optional_count") == "NO"
    assert rows.get("canonical_users") == "YES"
    assert rows.get("purpose_cluster_id") == "YES"


def test_the_designed_indexes_and_constraints_are_installed(engine: Engine) -> None:
    """Every index and CHECK the read paths and the cross-pod invariant rely on."""
    with engine.connect() as conn:
        indexes = {
            r[0] for r in conn.execute(text("select indexname from pg_indexes where schemaname = 'codemie'")).all()
        }
        checks = {
            r[0]
            for r in conn.execute(
                text(
                    "select c.conname from pg_constraint c "
                    "join pg_namespace n on n.oid = c.connamespace "
                    "where c.contype = 'c' and n.nspname = 'codemie'"
                )
            ).all()
        }
    for name in (
        "ix_mm_audit_run_kind_started",
        "ix_mm_audit_run_kind_latest_serveable",
        "uq_mm_audit_run_active",
        "ix_mm_audit_verdict_run_reason",
        "ix_mm_audit_verdict_run_tier",
        "ix_mm_audit_verdict_run_kind",
        "ix_mm_audit_verdict_run_cluster",
        "ix_mm_audit_finding_run_rule",
        "ix_mm_audit_finding_run_severity",
        "ix_mm_audit_finding_assistant",
        "ix_mm_audit_similarity_run_assistant_rank",
        "uq_mm_llm_assessment_cache",
        "ix_mm_llm_assessment_assistant_created",
    ):
        assert name in indexes, f"missing index {name}"
    assert "ix_mm_audit_run_kind_latest_ok" not in indexes, (
        "the superseded index name must not exist; the squashed migration "
        "creates ix_mm_audit_run_kind_latest_serveable directly"
    )
    for name in (
        "ck_mm_audit_run_status",
        "ck_mm_audit_run_trigger",
        "ck_mm_audit_run_entity_kind",
        "ck_mm_audit_verdict_bucket",
        "ck_mm_audit_verdict_kind",
        "ck_mm_audit_verdict_reason",
        "ck_mm_audit_verdict_tier",
    ):
        assert name in checks, f"missing check constraint {name}"


def test_only_one_non_terminal_run_per_entity_kind(engine: Engine) -> None:
    """`uq_mm_audit_run_active` is the cross-pod replacement for `_running`."""
    insert = text(
        "insert into codemie.mm_audit_run "
        "(id, entity_kind, started_ts, started_at, status, schema_version) "
        "values (:id, 'ASSISTANT', :ts, now(), 'RUNNING', 2)"
    )
    with engine.connect() as conn:
        conn.execute(insert, {"id": "active-1", "ts": time.time()})
        conn.commit()
    try:
        with pytest.raises(IntegrityError):
            with engine.connect() as conn:
                conn.execute(insert, {"id": "active-2", "ts": time.time()})
                conn.commit()
    finally:
        with engine.connect() as conn:
            conn.execute(text("delete from codemie.mm_audit_run where id like 'active-%'"))
            conn.commit()


def test_alembic_has_a_single_head() -> None:
    """Two heads mean `upgrade head` is ambiguous and CI applies one of them."""
    result = _run_alembic(_ADMIN_URL, "heads")
    assert result.returncode == 0, result.stderr
    heads = [line for line in result.stdout.splitlines() if "(head)" in line]
    assert len(heads) == 1, f"expected exactly one alembic head, got: {heads}"


def test_squashed_revision_chains_directly_from_the_pre_squash_head() -> None:
    """`f3e2d1c0b9a8` revises `d9e8f7a6b5c4` -- the two-heads bug this squash fixed."""
    matches = [p for p in _VERSIONS_DIR.glob("*.py") if p.name.startswith(f"{SQUASHED_REVISION}_")]
    assert len(matches) == 1, f"expected exactly one file for revision {SQUASHED_REVISION}, got {matches}"
    source = matches[0].read_text(encoding="utf-8")
    assigned: dict[str, object] = {}
    for node in ast.parse(source).body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Constant)
        ):
            assigned[node.target.id] = node.value.value
    assert assigned.get("revision") == SQUASHED_REVISION
    assert (
        assigned.get("down_revision") == PRE_SQUASH_REVISION
    ), f"{SQUASHED_REVISION} must revise {PRE_SQUASH_REVISION}, found {assigned.get('down_revision')!r}"


# ---------------------------------------------------------------------------
# Indexes on pre-existing tables stay inside the migration transaction
# ---------------------------------------------------------------------------


def _squashed_migration_source() -> str:
    matches = [p for p in _VERSIONS_DIR.glob("*.py") if p.name.startswith(f"{SQUASHED_REVISION}_")]
    assert len(matches) == 1, f"expected exactly one file for revision {SQUASHED_REVISION}, got {matches}"
    return matches[0].read_text(encoding="utf-8")


def test_migration_never_leaves_the_env_transaction() -> None:
    """No `autocommit_block()` and no `postgresql_concurrently` anywhere in the squashed migration.

    `env.py` runs every migration in one transaction that starts with
    `LOCK TABLE alembic_version`, which is what stops two pods migrating in
    parallel. `autocommit_block()` commits that transaction: the lock is gone
    and the columns added before it are committed while the revision is not
    stamped, so a second pod re-runs `add_column` and crashloops on a duplicate
    column (and so does every later start if a later step fails).
    """
    tree = ast.parse(_squashed_migration_source())

    autocommit_blocks = [
        node for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr == "autocommit_block"
    ]
    concurrent_keywords = [
        node for node in ast.walk(tree) if isinstance(node, ast.keyword) and node.arg == "postgresql_concurrently"
    ]

    assert not autocommit_blocks, "autocommit_block() would commit the transaction holding the alembic_version lock"
    assert not concurrent_keywords, "CREATE INDEX CONCURRENTLY cannot run inside the migration transaction"


def test_the_index_on_assistants_is_in_place_after_upgrade(engine: Engine, disposable_db: str) -> None:
    """Run the upgrade and look at pg_indexes."""
    _alembic_ok(disposable_db, "upgrade", "head")
    with engine.connect() as conn:
        row = conn.execute(
            text("select indexdef from pg_indexes where schemaname = 'codemie' and indexname = :n"),
            {"n": _ASSISTANTS_STATUS_INDEX},
        ).first()
    assert row is not None, f"{_ASSISTANTS_STATUS_INDEX} is missing after upgrade"
    assert "marketplace_status" in row[0]


# ---------------------------------------------------------------------------
# assistants.marketplace_status and its CHECK constraint
# ---------------------------------------------------------------------------


def test_the_status_check_is_validated_not_merely_declared(disposable_db: str, engine: Engine) -> None:
    """`NOT VALID` is a deployment tactic, not the end state.

    The constraint is added `NOT VALID` so the brief ACCESS EXCLUSIVE lock does
    not also carry a full table scan, then validated separately under a weaker
    lock. A revision that added it and forgot to validate would enforce the
    check on new rows only, and silently tolerate whatever garbage is already
    stored.
    """
    _alembic_ok(disposable_db, "upgrade", "head")

    with engine.connect() as conn:
        validated = conn.execute(
            text("select convalidated from pg_constraint " " where conname = 'ck_assistants_marketplace_status'")
        ).scalar()
    assert validated is True, "the status CHECK was left NOT VALID"


def _column_default(engine: Engine) -> str:
    return _scalar(
        engine,
        "select column_default from information_schema.columns "
        " where table_schema = 'codemie' and table_name = 'assistants' "
        "   and column_name = 'marketplace_status'",
    )


def test_marketplace_status_is_born_draft() -> None:
    """The column is created with `server_default='DRAFT'` directly.

    The actual shipped code never defaulted this column to `'APPROVED'` --
    that was a narrative in some of the squashed revisions' docstrings that
    did not match what the code they described ever did -- so there is no
    two-stage default transition to replay, and no backfill journal table.
    """
    source = _squashed_migration_source()
    assert '"marketplace_status"' in source or "'marketplace_status'" in source
    assert 'server_default="DRAFT"' in source


def test_the_column_default_is_draft_at_head(disposable_db: str, engine: Engine) -> None:
    _alembic_ok(disposable_db, "upgrade", "head")
    assert "DRAFT" in _column_default(engine), _column_default(engine)


def test_no_status_backfill_journal_table_exists(engine: Engine) -> None:
    """The journal table that made a two-stage default transition reversible never existed here."""
    exists = _scalar(
        engine,
        "select to_regclass('codemie.mm_status_backfill_journal') is not null",
    )
    assert exists is False, "mm_status_backfill_journal should not exist; there is no backfill to journal"


# ---------------------------------------------------------------------------
# mm_review_decision -- the CAS token, the decision log, RE_SUBMIT
# ---------------------------------------------------------------------------


def test_m2_installs_the_cas_token_and_the_decision_log(disposable_db: str, engine: Engine) -> None:
    """The columns and constraints the CAS statement depends on are real DDL.

    `marketplace_version` is what `WHERE marketplace_version = :v` compares
    against, `uq_mm_review_decision_idempotency` is what makes a retry a
    replay, and `uq_mm_review_decision_entity_version` is the backstop that
    makes a lost update impossible even if the CAS were mis-written. A missing
    index here would not fail any Python test -- it would just quietly stop
    protecting anything.
    """
    _alembic_ok(disposable_db, "upgrade", "head")

    with engine.connect() as conn:
        columns = {
            row[0]: row[1]
            for row in conn.execute(
                text(
                    "select column_name, data_type from information_schema.columns "
                    " where table_schema = 'codemie' and table_name = 'assistants' "
                    "   and column_name in ('marketplace_version', 'is_verified', "
                    "        'marketplace_decided_at', 'marketplace_decided_by', "
                    "        'marketplace_decision_reason')"
                )
            ).all()
        }
        indexes = {
            row[0]
            for row in conn.execute(
                text(
                    "select indexname from pg_indexes "
                    " where schemaname = 'codemie' and tablename = 'mm_review_decision'"
                )
            ).all()
        }
        checks = {
            row[0]
            for row in conn.execute(
                text(
                    "select conname from pg_constraint "
                    " where contype = 'c' and conrelid in ("
                    "   'codemie.mm_review_decision'::regclass, 'codemie.assistants'::regclass)"
                )
            ).all()
        }

    assert set(columns) == {
        "marketplace_version",
        "is_verified",
        "marketplace_decided_at",
        "marketplace_decided_by",
        "marketplace_decision_reason",
    }, f"the decision columns are missing: {sorted(columns)}"
    assert columns["marketplace_version"] == "integer"
    assert columns["is_verified"] == "boolean"
    assert columns["marketplace_decided_at"] == "timestamp with time zone"

    for index in (
        "uq_mm_review_decision_idempotency",
        "uq_mm_review_decision_entity_version",
        "ix_mm_review_decision_entity_created",
        "ix_mm_review_decision_decided_by_created",
    ):
        assert index in indexes, f"{index} is missing; {sorted(indexes)}"

    for check in (
        "ck_assistants_marketplace_status",
        "ck_mm_review_decision_override_notice",
        "ck_mm_review_decision_reject_reason",
        "ck_mm_review_decision_version_step",
    ):
        assert check in checks, f"{check} is missing; {sorted(checks)}"


def test_re_submit_decision_constraint_accepts_re_submit_and_rejects_bogus(disposable_db: str, engine: Engine) -> None:
    """RE_SUBMIT is accepted by the constraint; an unknown decision is rejected.

    `ck_mm_review_decision_decision` is created with all six values -- including
    RE_SUBMIT -- from the start; there is no narrower constraint it was ever
    widened from.
    """
    _alembic_ok(disposable_db, "upgrade", "head")

    assistant_id = str(uuid.uuid4())
    try:
        # Insert a RE_SUBMIT row -- must succeed
        with engine.connect() as conn:
            conn.execute(
                text(
                    "INSERT INTO codemie.assistants "
                    "(id, name, description, system_prompt, project, shared, is_react, "
                    " created_date, creator, is_global, categories, marketplace_status, "
                    " version_count, marketplace_version, is_verified) "
                    "VALUES (:id, :name, 'd', 'p', 'demo', true, true, now(), 'someone', "
                    "        false, '[]'::jsonb, 'APPROVED', 1, 0, true)"
                ),
                {"id": assistant_id, "name": "re-submit-test"},
            )
            conn.execute(
                text(
                    "INSERT INTO codemie.mm_review_decision "
                    "(id, entity_kind, entity_id, decision, from_status, to_status, "
                    " from_version, to_version, idempotency_key) "
                    "VALUES (:id, 'ASSISTANT', :eid, 'RE_SUBMIT', 'APPROVED', 'PENDING_REVIEW', "
                    "        0, 1, :ikey)"
                ),
                {"id": str(uuid.uuid4()), "eid": assistant_id, "ikey": str(uuid.uuid4())},
            )
            conn.commit()

        # Insert a BOGUS row -- must raise IntegrityError
        with pytest.raises(IntegrityError):
            with engine.connect() as conn:
                conn.execute(
                    text(
                        "INSERT INTO codemie.mm_review_decision "
                        "(id, entity_kind, entity_id, decision, from_status, to_status, "
                        " from_version, to_version, idempotency_key) "
                        "VALUES (:id, 'ASSISTANT', :eid, 'BOGUS', 'APPROVED', 'PENDING_REVIEW', "
                        "        1, 2, :ikey)"
                    ),
                    {"id": str(uuid.uuid4()), "eid": assistant_id, "ikey": str(uuid.uuid4())},
                )
                conn.commit()
    finally:
        # Scope cleanup to only the rows this test inserted; the disposable_db
        # fixture is module-scoped so broad DELETEs would strip rows other tests
        # depend on.
        with engine.connect() as conn:
            conn.execute(
                text("DELETE FROM codemie.mm_review_decision WHERE entity_id = :eid"),
                {"eid": assistant_id},
            )
            conn.execute(
                text("DELETE FROM codemie.assistants WHERE id = :eid"),
                {"eid": assistant_id},
            )
            conn.commit()


# ---------------------------------------------------------------------------
# The submission timestamp the queue orders by
# ---------------------------------------------------------------------------


def test_assistants_gain_a_real_submission_timestamp(disposable_db: str, engine: Engine) -> None:
    """`marketplace_submitted_at`, plus the index the oldest-first queue reads.

    Without a dedicated column the router had to guess from `update_date`
    (`marketplace_management.py:_submitted_at`), so any edit while pending
    silently rewrote the row's place in the queue.
    """
    _alembic_ok(disposable_db, "upgrade", "head")
    with engine.connect() as conn:
        column = conn.execute(
            text(
                "select data_type, is_nullable from information_schema.columns "
                "where table_schema = 'codemie' and table_name = 'assistants' "
                "and column_name = 'marketplace_submitted_at'"
            )
        ).first()
        indexes = {
            r[0]
            for r in conn.execute(
                text("select indexname from pg_indexes where schemaname = 'codemie' and tablename = 'assistants'")
            ).all()
        }
    assert column is not None, "assistants.marketplace_submitted_at is missing"
    assert column[0] == "timestamp with time zone"
    assert column[1] == "YES", "rows submitted before the gate was armed have no submission time"
    assert (
        _PENDING_QUEUE_INDEX in indexes
    ), "the oldest-first queue read needs an index on (marketplace_status, marketplace_submitted_at)"


# ---------------------------------------------------------------------------
# Retention and cascade
# ---------------------------------------------------------------------------


def test_deleting_a_run_cascades_to_all_children(disposable_db: str, engine: Engine) -> None:
    """One DELETE on the parent reclaims verdicts, findings and similarity rows.

    Retention is a single statement against `mm_audit_run` only; it is correct
    exclusively because the FKs this migration installs are ON DELETE CASCADE.
    The `mm_llm_assessment` row is the deliberate exception -- an assessment
    outlives the run that requested it (SET NULL), so a purge degrades it
    rather than destroying it.
    """
    _alembic_ok(disposable_db, "upgrade", "head")
    run_id = "cascade-victim"
    with engine.connect() as conn:
        conn.execute(
            text(
                "insert into codemie.mm_audit_run "
                "(id, entity_kind, started_ts, started_at, status, schema_version) "
                "values (:r, 'ASSISTANT', :ts, now(), 'SUCCEEDED', 2)"
            ),
            {"r": run_id, "ts": time.time()},
        )
        conn.execute(
            text(
                "insert into codemie.mm_audit_verdict (audit_run_id, assistant_id, bucket) "
                "values (:r, 'victim-1', 'VERIFIED')"
            ),
            {"r": run_id},
        )
        conn.execute(
            text(
                "insert into codemie.mm_audit_finding "
                "(audit_run_id, assistant_id, rule, severity) values (:r, 'victim-1', 'substance', 'CRITICAL')"
            ),
            {"r": run_id},
        )
        conn.execute(
            text(
                "insert into codemie.mm_audit_similarity "
                "(audit_run_id, assistant_id, other_id, rank, combined, source) "
                "values (:r, 'victim-1', 'victim-2', 1, 0.9, 'ES')"
            ),
            {"r": run_id},
        )
        conn.execute(
            text(
                "insert into codemie.mm_llm_assessment "
                "(id, entity_kind, assistant_id, audit_run_id, status, model, prompt_name, "
                " prompt_version, prompt_hash, input_hash) "
                "values ('llm-1', 'ASSISTANT', 'victim-1', :r, 'SUCCEEDED', 'm', 'p', 'v', 'h', 'i')"
            ),
            {"r": run_id},
        )
        conn.commit()

    with engine.connect() as conn:
        conn.execute(text("delete from codemie.mm_audit_run where id = :r"), {"r": run_id})
        conn.commit()

    for table in ("mm_audit_verdict", "mm_audit_finding", "mm_audit_similarity"):
        left = _scalar(engine, f"select count(*) from codemie.{table} where audit_run_id = '{run_id}'")
        assert left == 0, f"{table} did not cascade with its run"
    orphaned_assessment = _scalar(engine, "select audit_run_id from codemie.mm_llm_assessment where id = 'llm-1'")
    assert orphaned_assessment is None, "the assessment must survive the purge with a NULL run"
    with engine.connect() as conn:
        conn.execute(text("delete from codemie.mm_llm_assessment where id = 'llm-1'"))
        conn.commit()


def test_retention_never_deletes_the_newest_good_run(disposable_db: str, engine: Engine) -> None:
    """The retention statement purges by age but always leaves the latest SUCCEEDED run.

    Exercises the production repository, not a copy of the SQL: a retention job
    that drifts from the statement this test pins is exactly the failure the
    test exists to catch.
    """
    from datetime import datetime, timedelta, timezone

    from sqlmodel import Session

    from codemie.service.marketplace_management.audit_retention import (
        marketplace_audit_retention_repository as repo,
    )

    now = datetime.now(timezone.utc)
    ancient = now - timedelta(days=400)
    seeds = (
        # (id, status, started_at) -- all three are older than the cutoff.
        ("old-succeeded", "SUCCEEDED", ancient),
        ("old-failed", "FAILED", ancient + timedelta(days=1)),
        ("newest-succeeded", "SUCCEEDED", ancient + timedelta(days=2)),
    )
    # WORKFLOW, not ASSISTANT: the module fixture seeds two recent ASSISTANT
    # runs, either of which would be the newest good ASSISTANT run and would
    # make "the newest good run survives" trivially true for the wrong reason.
    with engine.connect() as conn:
        for run_id, status, started in seeds:
            conn.execute(
                text(
                    "insert into codemie.mm_audit_run "
                    "(id, entity_kind, started_ts, started_at, status, schema_version) "
                    "values (:r, 'WORKFLOW', :ts, :at, :st, 2)"
                ),
                {"r": run_id, "ts": started.timestamp(), "at": started, "st": status},
            )
        conn.execute(
            text(
                "insert into codemie.mm_audit_verdict (audit_run_id, assistant_id, bucket) "
                "values ('newest-succeeded', 'kept-1', 'VERIFIED')"
            )
        )
        conn.commit()

    try:
        with Session(engine) as session:
            deleted = repo.delete_older_than(entity_kind="WORKFLOW", cutoff=now - timedelta(days=30), session=session)
            session.commit()

        with engine.connect() as conn:
            survivors = {
                r[0]
                for r in conn.execute(
                    text(
                        "select id from codemie.mm_audit_run "
                        "where id in ('old-succeeded', 'old-failed', 'newest-succeeded')"
                    )
                ).all()
            }
        assert survivors == {"newest-succeeded"}, (
            "retention must purge stale runs but never the newest SUCCEEDED one, " f"survivors={sorted(survivors)}"
        )
        assert deleted == 2, f"expected 2 purged runs, repository reported {deleted}"
        # The one run left is the only thing standing between the Overview and
        # an empty page, so its children must still be there.
        assert (
            _scalar(engine, "select count(*) from codemie.mm_audit_verdict where audit_run_id = 'newest-succeeded'")
            == 1
        )
    finally:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "delete from codemie.mm_audit_run "
                    "where id in ('old-succeeded', 'old-failed', 'newest-succeeded')"
                )
            )
            conn.commit()


def test_retention_keeps_the_newest_good_run_even_when_it_is_the_only_one(disposable_db: str, engine: Engine) -> None:
    """A single ancient SUCCEEDED run must survive its own retention sweep.

    Otherwise the first purge on a quiet environment wipes the audit history
    the Overview is served from and leaves the admin UI blank.
    """
    from datetime import datetime, timedelta, timezone

    from sqlmodel import Session

    from codemie.service.marketplace_management.audit_retention import (
        marketplace_audit_retention_repository as repo,
    )

    now = datetime.now(timezone.utc)
    ancient = now - timedelta(days=400)
    with engine.connect() as conn:
        conn.execute(
            text(
                "insert into codemie.mm_audit_run "
                "(id, entity_kind, started_ts, started_at, status, schema_version) "
                "values ('lonely', 'WORKFLOW', :ts, :at, 'SUCCEEDED', 2)"
            ),
            {"ts": ancient.timestamp(), "at": ancient},
        )
        conn.commit()
    try:
        with Session(engine) as session:
            deleted = repo.delete_older_than(entity_kind="WORKFLOW", cutoff=now - timedelta(days=30), session=session)
            session.commit()
        assert deleted == 0
        assert _scalar(engine, "select count(*) from codemie.mm_audit_run where id = 'lonely'") == 1
    finally:
        with engine.connect() as conn:
            conn.execute(text("delete from codemie.mm_audit_run where id = 'lonely'"))
            conn.commit()


def test_retention_is_scoped_to_one_entity_kind(disposable_db: str, engine: Engine) -> None:
    """Purging ASSISTANT history must not touch WORKFLOW history."""
    from datetime import datetime, timedelta, timezone

    from sqlmodel import Session

    from codemie.service.marketplace_management.audit_retention import (
        marketplace_audit_retention_repository as repo,
    )

    now = datetime.now(timezone.utc)
    ancient = now - timedelta(days=400)
    with engine.connect() as conn:
        for run_id, kind in (("wf-old", "WORKFLOW"), ("wf-new", "WORKFLOW"), ("as-old", "ASSISTANT")):
            conn.execute(
                text(
                    "insert into codemie.mm_audit_run "
                    "(id, entity_kind, started_ts, started_at, status, schema_version) "
                    "values (:r, :k, :ts, :at, 'SUCCEEDED', 2)"
                ),
                {"r": run_id, "k": kind, "ts": ancient.timestamp(), "at": ancient},
            )
        conn.commit()
    try:
        with Session(engine) as session:
            repo.delete_older_than(entity_kind="ASSISTANT", cutoff=now - timedelta(days=30), session=session)
            session.commit()
        with engine.connect() as conn:
            remaining = {
                r[0]
                for r in conn.execute(
                    text("select id from codemie.mm_audit_run where id like 'wf-%' or id = 'as-old'")
                ).all()
            }
        # `as-old` goes: it is ancient and it is not the newest good ASSISTANT
        # run (the module fixture's two runs are). Both WORKFLOW runs stay --
        # including `wf-old`, which is just as ancient. Nothing but the
        # entity_kind filter separates those two outcomes.
        assert remaining == {"wf-old", "wf-new"}, (
            "an ASSISTANT purge must leave WORKFLOW runs alone regardless of their age; "
            f"remaining={sorted(remaining)}"
        )
    finally:
        with engine.connect() as conn:
            conn.execute(text("delete from codemie.mm_audit_run where id like 'wf-%' or id = 'as-old'"))
            conn.commit()


# ---------------------------------------------------------------------------
# Deletion and retention of moderation data (F-39, R12, S16)
# ---------------------------------------------------------------------------
#
# `assistant_id` is deliberately given NO foreign key to `assistants.id`,
# because moderation history has to be able to outlive the entity it
# describes. The cost of that decision is that deleting an assistant leaves
# its verdicts, findings, similarity rows and LLM assessments behind forever,
# keyed to an id that no longer resolves -- so the deleted assistant
# reappears in `/cleanup` with a NULL name and clicking it 404s.
#
# The fix therefore cannot be a cascade. It has to be an explicit, stated
# policy and an explicit job. The policy the tests below pin:
#
#   PURGE     mm_audit_verdict, mm_audit_finding, mm_audit_similarity
#             (both the `assistant_id` and the `other_id` side) and
#             mm_llm_assessment -- machine-generated assessments *of the
#             deleted content*, quoting its prompt and description in their
#             detail/message/fields columns, with no accountability value once
#             the subject is gone.
#
#   ANONYMISE mm_review_decision -- a record of a *human administrator's*
#             action. The row survives with its decision, statuses, versions,
#             actor and timestamp; the free text that quotes the deleted
#             content (`reason`, `override_notice`) is replaced by an explicit
#             redaction tombstone, `result` is nulled, and the row is stamped
#             `redacted_at`.

_PURGED_ASSISTANT = "doomed-assistant"
_SURVIVING_ASSISTANT = "surviving-assistant"


def _seed_moderation_history(engine: Engine) -> None:
    """One assistant about to be deleted, one that must be left alone."""
    now = time.time()
    with engine.connect() as conn:
        conn.execute(text("delete from codemie.mm_audit_run where id like 'purge-%'"))
        conn.execute(text("delete from codemie.mm_review_decision"))
        conn.execute(text("delete from codemie.mm_llm_assessment"))
        conn.execute(text("delete from codemie.assistants"))
        for aid in (_PURGED_ASSISTANT, _SURVIVING_ASSISTANT):
            conn.execute(
                text(
                    "insert into codemie.assistants "
                    "(id, name, description, system_prompt, project, shared, is_react, "
                    " created_date, creator, is_global, categories, marketplace_status, "
                    " version_count) "
                    "values (:id, :id, 'd', 'p', 'demo', true, true, now(), 'someone', "
                    "        true, '[]'::jsonb, 'APPROVED', 1)"
                ),
                {"id": aid},
            )
        conn.execute(
            text(
                "insert into codemie.mm_audit_run "
                "(id, entity_kind, started_ts, started_at, status, schema_version) "
                "values ('purge-run', 'ASSISTANT', :ts, now(), 'SUCCEEDED', 2)"
            ),
            {"ts": now},
        )
        for aid in (_PURGED_ASSISTANT, _SURVIVING_ASSISTANT):
            conn.execute(
                text(
                    "insert into codemie.mm_audit_verdict "
                    "(audit_run_id, assistant_id, bucket, verdict_kind, cleanup_reason, detail) "
                    "values ('purge-run', :a, 'DELETE', 'CANDIDATE_FOR_CLEANUP', 'invalid', "
                    "        'system prompt quotes: you are a helpful assistant')"
                ),
                {"a": aid},
            )
            conn.execute(
                text(
                    "insert into codemie.mm_audit_finding "
                    "(audit_run_id, assistant_id, rule, severity, message) "
                    "values ('purge-run', :a, 'name_length', 'CRITICAL', 'Name is 3 chars.')"
                ),
                {"a": aid},
            )
        # The deleted assistant is both a subject of a similarity row and the
        # *neighbour* named in the surviving assistant's row -- `other_name`
        # carries its name, so that row leaks too if only one side is purged.
        conn.execute(
            text(
                "insert into codemie.mm_audit_similarity "
                "(audit_run_id, assistant_id, other_id, other_name, rank, combined, source) "
                "values ('purge-run', :a, :o, :oname, 1, 0.91, 'ES')"
            ),
            {"a": _PURGED_ASSISTANT, "o": _SURVIVING_ASSISTANT, "oname": _SURVIVING_ASSISTANT},
        )
        conn.execute(
            text(
                "insert into codemie.mm_audit_similarity "
                "(audit_run_id, assistant_id, other_id, other_name, rank, combined, source) "
                "values ('purge-run', :a, :o, :oname, 1, 0.91, 'ES')"
            ),
            {"a": _SURVIVING_ASSISTANT, "o": _PURGED_ASSISTANT, "oname": _PURGED_ASSISTANT},
        )
        for idx, aid in enumerate((_PURGED_ASSISTANT, _SURVIVING_ASSISTANT)):
            conn.execute(
                text(
                    "insert into codemie.mm_llm_assessment "
                    "(id, entity_kind, assistant_id, audit_run_id, status, model, prompt_name,"
                    " prompt_version, prompt_hash, input_hash, overall_score, fields) "
                    "values (:i, 'ASSISTANT', :a, 'purge-run', 'SUCCEEDED', 'gpt', 'p', 'v1',"
                    "        :ph, :ih, 42, '{\"summary\": \"quotes the prompt\"}'::jsonb)"
                ),
                {"i": f"llm-purge-{idx}", "a": aid, "ph": f"h{idx}", "ih": f"i{idx}"},
            )
            conn.execute(
                text(
                    "insert into codemie.mm_review_decision "
                    "(id, entity_kind, entity_id, decision, from_status, to_status,"
                    " from_version, to_version, reason, decided_by_email, idempotency_key, result) "
                    "values (:i, 'ASSISTANT', :a, 'REJECT', 'PENDING_REVIEW', 'REJECTED', 1, 2,"
                    "        :reason, 'admin@codemie.ai', :k, '{\"detail\": \"quotes it\"}'::jsonb)"
                ),
                {
                    "i": f"dec-purge-{idx}",
                    "a": aid,
                    "reason": "Rejected because the system prompt duplicates another assistant.",
                    "k": f"key-purge-{idx}",
                },
            )
        conn.commit()


def _cleanup_ids(engine: Engine) -> list[str]:
    """Exactly what `GET /cleanup` would list for the newest SUCCEEDED run."""
    from sqlmodel import Session

    from codemie.service.marketplace_management import audit_read_repository as reads

    with Session(engine) as session:
        run_id = reads.latest_succeeded_run_id(session, "ASSISTANT")
        rows = reads.cleanup_page(
            session,
            run_id,
            page=1,
            page_size=50,
            bucket=None,
            reason=None,
            tier=None,
            verdict=None,
            mcp=None,
            category=None,
            cluster_id=None,
            q=None,
            rule=None,
        )
    return sorted(r["id"] for r in rows)


def _teardown_moderation_history(engine: Engine) -> None:
    with engine.connect() as conn:
        conn.execute(text("delete from codemie.mm_review_decision"))
        conn.execute(text("delete from codemie.mm_llm_assessment"))
        conn.execute(text("delete from codemie.mm_audit_run where id = 'purge-run'"))
        conn.execute(text("delete from codemie.assistants"))
        conn.commit()


def test_deleting_an_assistant_purges_its_moderation_data(disposable_db: str, engine: Engine) -> None:
    """The delete-path hook removes the assessments and redacts the decision.

    Six assertions, one per table plus the surface the user actually reported:
    a deleted assistant must not come back as a Cleanup row (F-39), and the
    assistant next to it in the same run must be untouched.
    """
    from sqlmodel import Session

    from codemie.service.marketplace_management.audit_retention import (
        marketplace_moderation_purge_repository as purge,
    )

    _alembic_ok(disposable_db, "upgrade", "head")
    _seed_moderation_history(engine)

    assert _cleanup_ids(engine) == [_PURGED_ASSISTANT, _SURVIVING_ASSISTANT]

    try:
        with engine.connect() as conn:
            conn.execute(text("delete from codemie.assistants where id = :a"), {"a": _PURGED_ASSISTANT})
            conn.commit()

        with Session(engine) as session:
            removed = purge.purge_entity(entity_kind="ASSISTANT", entity_id=_PURGED_ASSISTANT, session=session)
            session.commit()

        def count(table: str, column: str) -> int:
            return int(
                _scalar(
                    engine,
                    f"select count(*) from codemie.{table} where {column} = '{_PURGED_ASSISTANT}'",
                )
                or 0
            )

        assert count("mm_audit_verdict", "assistant_id") == 0
        assert count("mm_audit_finding", "assistant_id") == 0
        assert count("mm_audit_similarity", "assistant_id") == 0
        assert count("mm_audit_similarity", "other_id") == 0, (
            "the deleted assistant's name survives in `other_name` on a *surviving* "
            "assistant's similarity row unless both sides are purged"
        )
        assert count("mm_llm_assessment", "assistant_id") == 0

        # ANONYMISE, not purge: the reviewer's action survives, its free text does not.
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "select decision, from_status, to_status, decided_by_email, reason,"
                    "       override_notice, result, redacted_at "
                    "  from codemie.mm_review_decision where entity_id = :a"
                ),
                {"a": _PURGED_ASSISTANT},
            ).one()
        assert row[0] == "REJECT" and row[3] == "admin@codemie.ai", (
            "who decided what, and when, is the accountability record this table "
            "exists to preserve; it must survive the purge"
        )
        from codemie.service.marketplace_management.audit_retention import (
            REDACTION_TOMBSTONE,
        )

        assert (
            row[4] == REDACTION_TOMBSTONE and row[6] is None
        ), "free text quoting the deleted assistant must be redacted"
        assert row[7] is not None, "a redaction must be visible, not silent"

        # The surviving assistant keeps everything.
        assert (
            _scalar(
                engine,
                "select count(*) from codemie.mm_audit_verdict " f"where assistant_id = '{_SURVIVING_ASSISTANT}'",
            )
            == 1
        )
        assert (
            _scalar(
                engine,
                "select reason from codemie.mm_review_decision " f"where entity_id = '{_SURVIVING_ASSISTANT}'",
            )
            is not None
        )

        assert removed > 0, "the purge must report what it removed for the log line"
        assert _cleanup_ids(engine) == [
            _SURVIVING_ASSISTANT
        ], "a deleted assistant must not reappear as a Cleanup row (F-39)"
    finally:
        _teardown_moderation_history(engine)


def test_retention_job_sweeps_moderation_data_of_already_deleted_assistants(disposable_db: str, engine: Engine) -> None:
    """The hook cannot fix history; an explicit sweep has to.

    Any assistant deleted before this functionality existed, and any delete
    that raced or crashed past the hook, leaves rows behind. The retention job
    therefore also purges moderation data whose subject no longer exists in
    `assistants` -- scoped to `entity_kind='ASSISTANT'`, because a WORKFLOW
    verdict's `assistant_id` is a workflow id and is *supposed* to be absent
    from `assistants`.
    """
    from sqlmodel import Session

    from codemie.service.marketplace_management.audit_retention import (
        marketplace_moderation_purge_repository as purge,
    )

    _alembic_ok(disposable_db, "upgrade", "head")
    _seed_moderation_history(engine)

    try:
        # A WORKFLOW run whose subject is, correctly, not in `assistants`.
        with engine.connect() as conn:
            conn.execute(
                text(
                    "insert into codemie.mm_audit_run "
                    "(id, entity_kind, started_ts, started_at, status, schema_version) "
                    "values ('purge-wf-run', 'WORKFLOW', :ts, now(), 'SUCCEEDED', 2)"
                ),
                {"ts": time.time()},
            )
            conn.execute(
                text(
                    "insert into codemie.mm_audit_verdict "
                    "(audit_run_id, assistant_id, bucket) "
                    "values ('purge-wf-run', 'some-workflow', 'VERIFIED')"
                )
            )
            conn.execute(text("delete from codemie.assistants where id = :a"), {"a": _PURGED_ASSISTANT})
            conn.commit()

        with Session(engine) as session:
            swept = purge.purge_orphans(entity_kind="ASSISTANT", session=session)
            session.commit()

        assert swept >= 1, "the sweep must report the entities it reclaimed"
        assert (
            _scalar(
                engine,
                "select count(*) from codemie.mm_audit_verdict " f"where assistant_id = '{_PURGED_ASSISTANT}'",
            )
            == 0
        )
        assert (
            _scalar(
                engine,
                "select count(*) from codemie.mm_audit_verdict " "where assistant_id = 'some-workflow'",
            )
            == 1
        ), "an ASSISTANT sweep must never touch WORKFLOW verdicts, whose subjects are not assistants"
        assert (
            _scalar(
                engine,
                "select count(*) from codemie.mm_audit_verdict " f"where assistant_id = '{_SURVIVING_ASSISTANT}'",
            )
            == 1
        )
    finally:
        with engine.connect() as conn:
            conn.execute(text("delete from codemie.mm_audit_run where id = 'purge-wf-run'"))
            conn.commit()
        _teardown_moderation_history(engine)
