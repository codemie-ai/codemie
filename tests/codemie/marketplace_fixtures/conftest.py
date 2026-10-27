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

"""Backend seeding fixtures for the marketplace approval suite.

EXECUTION-PLAN 10.2, `review-e2e-coverage-spec.md` §4.4. The counterpart of
`codemie-ui/e2e/fixtures/marketplace.ts`, and it exists for the same reason:
the eleven downstream cases assert on *rows*, so the code that creates and
removes those rows must be proved, not assumed.

What it provides
----------------

``mm_run_id``
    Per-process run token, ``E2E-<base36 timestamp>-<pid>``.

``marketplace_assistant(case_name)``
    A DRAFT assistant created **through the service layer** — the model's own
    ``save()``, the same path the router takes — never a hand-written INSERT. An
    INSERT would reproduce whatever the test author believes the schema is and
    could never catch an ORM/schema gap, which is the mocked-session failure
    mode with extra steps.

``marketplace_pending_assistant(case_name)``
    The same, then put into the queue through ``MarketplaceQueueService.submit``
    — the real producer, with its compare-and-set and its ledger row.

    §4.4 asked for the PENDING_REVIEW status to be written *directly*, with a
    comment naming **Blocker A**, so ``grep -n "Blocker A"`` would find every
    place needing to switch to the real call once the producer landed. The
    producer has since landed (EXECUTION-PLAN 7.1: publish calls
    ``_submit_for_review`` when ``MARKETPLACE_APPROVAL_GATE_ENABLED`` is on), so
    this fixture goes through it instead. There is deliberately no direct status
    write here and therefore no Blocker A seam left to grep for.

``mm_teardown(ids)``
    Remove rows and every moderation trail they left, idempotently.

Why a throwaway database
------------------------

Built by **alembic**, following the precedent in
``tests/codemie/service/marketplace_management/conftest.py``. `create_all` would
build the schema from the ORM and so could never see an ORM/schema gap. The
database is dropped at the end of the session, so nothing is left behind.

Database coordinates (F-96): the admin connection is to database ``postgres``
and the application schema is ``codemie``. ``psql -d codemie`` fails at connect
with ``FATAL: database "codemie" does not exist``, which downstream reads as "no
rows" rather than "wrong database" — the confusion that finding is about.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, List
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine, make_url
from sqlmodel import Session, create_engine

# The dev stack from `docker-compose.yml`. `pytest.ini` points PG_URL at a dead
# port on purpose, so this is its own connection and is overridable.
MM_ADMIN_URL = os.getenv("MM_TEST_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")
MM_SCHEMA = "codemie"

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_DIR = _REPO_ROOT / "src" / "external" / "alembic"

_UNREACHABLE = """\
Cannot reach PostgreSQL at {url}.

These fixtures require a real database. Start the dev stack:

    cd codemie && docker compose up -d postgres

or point this file at another instance with MM_TEST_PG_URL.

Underlying error: {err}
"""

# Tables that can hold a moderation trail for one entity. `activity_events` is
# written only when ACTIVITY_EVENTS_ENABLED is on, so teardown clears both.
MODERATION_TABLES = ("mm_review_decision", "activity_events")


def _alembic(url: str, *args: str) -> str:
    """Invoke alembic exactly as an operator would, against `url`."""
    env = dict(os.environ)
    env["PG_URL"] = url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(_ALEMBIC_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if result.returncode != 0:
        raise AssertionError(
            "alembic {a} failed ({c})\n--- stdout ---\n{o}\n--- stderr ---\n{e}".format(
                a=" ".join(args), c=result.returncode, o=result.stdout, e=result.stderr
            )
        )
    return result.stdout + result.stderr


@pytest.fixture(scope="package")
def mm_engine() -> Iterator[Engine]:
    """A throwaway database migrated to `head` by the real migration chain.

    **Package**-scoped, not session-scoped (BLOCKER-CI-1). Its only consumer is
    `mm_bind_engine`, whose binding must die with this package -- see there.
    """
    admin_url = make_url(MM_ADMIN_URL)
    db_name = f"mm_seed_fixture_{_uuid.uuid4().hex[:10]}"
    admin = None
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
            conn.execute(text(f"create schema if not exists {MM_SCHEMA}"))
            conn.execute(text("create extension if not exists vector"))
        setup.dispose()

        _alembic(target_url.render_as_string(hide_password=False), "upgrade", "head")

        app_engine = create_engine(
            target_url,
            pool_pre_ping=True,
            connect_args={"options": f"-c search_path={MM_SCHEMA},public"},
        )
        yield app_engine
        app_engine.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'drop database if exists "{db_name}" with (force)'))
        admin.dispose()


@pytest.fixture(scope="package", autouse=True)
def mm_bind_engine(mm_engine: Engine) -> Iterator[Engine]:
    """Point the application's session factory at the throwaway database.

    `tests/conftest.py` installs a session-scoped autouse MagicMock over
    `PostgresClient.get_engine` so that importing a module cannot open a
    connection. That mock is exactly right for unit tests and exactly wrong
    here, where the round-trip *is* the assertion, so this patch sits on top of
    it for the duration of this package.

    **`scope="package"`, and that is the whole point** (BLOCKER-CI-1). It was
    `scope="session"`, and a session-scoped generator fixture is not finalised
    when its package finishes -- it is finalised at the end of the *run*. So
    the patch stayed on for every package collected after this one, and every
    later module that reasoned "`PG_URL` points at a dead port and the session
    is a MagicMock here" was in fact talking to a live, migrated PostgreSQL
    without knowing it.

    That is how the marketplace slice became order-dependent. Two modules --
    `rest_api/routers/test_marketplace_management_contract.py` and
    `service/marketplace_management/test_es_similarity_timeout.py` -- open an
    audit-run claim through `AuditCache`. Against a MagicMock the claim is a
    no-op; against the database that leaked in here it is a real `RUNNING` row
    holding `uq_mm_audit_run_active`, which the first module never released and
    the second module then collided with:

        AuditAlreadyRunningError: An audit of ASSISTANT is already running
        (run 903a6307-..., started by <this-very-pytest-process>)

    Package scope makes the binding end where the package ends, which is what
    every later module already assumed.
    """
    with patch("codemie.clients.postgres.PostgresClient.get_engine", return_value=mm_engine):
        yield mm_engine


# ---------------------------------------------------------------------------
# The run token
# ---------------------------------------------------------------------------

_RUN_ID = f"E2E-{int(datetime.now(timezone.utc).timestamp() * 1000):x}-{os.getpid()}"


@pytest.fixture(scope="session")
def mm_run_id() -> str:
    """`E2E-<hex timestamp>-<pid>`, stable for the whole process.

    Every seeded row carries it in `name` and `description`, so verification is
    always `where name like 'E2E <run_id>%'` and never
    `order by created_date desc limit 1`, which races with `pytest -n`.
    """
    return _RUN_ID


def seeded_name(run_id: str, case_name: str) -> str:
    """`E2E <run_id> <case_name>` — capped at the name_length gate's 60 chars."""
    name = f"E2E {run_id} {case_name}"
    if len(name) > 60:
        raise ValueError(
            f"Seeded name is {len(name)} chars ({name!r}); the name_length gate caps it at 60. "
            f"Shorten the case name to at most {60 - len(f'E2E {run_id} ')} characters."
        )
    return name


def seeded_description(run_id: str, case_name: str) -> str:
    """>= 100 chars for `description_length`, carrying the run token."""
    return (
        f"Disposable backend fixture for run {run_id}, case {case_name!r}. It documents the "
        "marketplace moderation lifecycle — submission, pending review, approval, rejection and "
        "withdrawal — and is removed again by its own teardown."
    )


def seeded_system_prompt(run_id: str, case_name: str) -> str:
    """>= 800 chars, so `system_prompt_length` and `substance` both pass."""
    body = (
        f"You are a marketplace review harness assistant seeded for run {run_id}, "
        f"case {case_name!r}. You exist to exercise the marketplace approval workflow and nothing "
        "else. Answer questions about the moderation lifecycle: how a submission enters the pending "
        "queue, what a moderator sees when they open the review drawer, and which transitions the "
        "state machine allows from DRAFT, PENDING_REVIEW, APPROVED, REJECTED and WITHDRAWN. For "
        "each transition name the actor permitted to perform it and state what happens to the "
        "marketplace search projection as a side effect. When a question falls outside the "
        "moderation lifecycle, say so plainly and stop rather than guessing. Never invent a status "
        "that is not in the transition table, never claim a decision was taken that you cannot "
        "point at a ledger row for, and always distinguish what a submission requested from what a "
        "moderator actually decided. Keep answers short, concrete and free of speculation, and "
        "prefer naming the exact endpoint or table involved over describing it loosely."
    )
    assert len(body) >= 800, f"seed prompt is {len(body)} chars; the gates need >= 800"
    return body


@dataclass(frozen=True)
class SeededAssistant:
    id: str
    name: str
    description: str
    case_name: str
    run_id: str


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------


def teardown_ids(engine: Engine, ids: List[str]) -> None:
    """Delete the rows and every moderation trail they left. Idempotent.

    Per-test, never a global sweep: a sweep would clean up but would also hide
    *which* test leaked.
    """
    if not ids:
        return
    with Session(engine) as session:
        for table in MODERATION_TABLES:
            session.execute(text(f"delete from {table} where entity_id = any(:ids)"), {"ids": list(ids)})
        session.execute(text("delete from assistants where id = any(:ids)"), {"ids": list(ids)})
        session.commit()


@pytest.fixture
def mm_teardown(mm_engine: Engine) -> Callable[[List[str]], None]:
    """Run the same teardown the seeding fixtures run, on ids of your choosing."""

    def _teardown(ids: List[str]) -> None:
        teardown_ids(mm_engine, ids)

    return _teardown


@pytest.fixture
def mm_seeded_ids(mm_engine: Engine) -> Iterator[List[str]]:
    """Ids this test seeded. Cleaned up when the test ends, whatever its verdict."""
    ids: List[str] = []
    try:
        yield ids
    finally:
        teardown_ids(mm_engine, ids)


# ---------------------------------------------------------------------------
# The seeding fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def marketplace_assistant(
    mm_run_id: str, mm_seeded_ids: List[str], mm_bind_engine: Engine
) -> Callable[[str], SeededAssistant]:
    """Create a DRAFT assistant through the service layer and track it for teardown."""
    from codemie.rest_api.models.assistant import Assistant

    def _create(case_name: str) -> SeededAssistant:
        name = seeded_name(mm_run_id, case_name)
        description = seeded_description(mm_run_id, case_name)
        assistant = Assistant(
            name=name,
            description=description,
            system_prompt=seeded_system_prompt(mm_run_id, case_name),
            project="codemie",
            llm_model_type="claude-opus-4-6-20260205",
            categories=["architecture"],
            is_global=False,
            # Explicit: the column's server default is still 'APPROVED' (F-53),
            # so a row that has never been published would otherwise claim to be
            # in the marketplace and SUBMIT would 409 against its own transition
            # table on the very first publish.
            marketplace_status="DRAFT",
            creator="e2e-fixture",
        )
        assistant.save()
        # Track before anything else can raise, so a failed submit still cleans up.
        mm_seeded_ids.append(assistant.id)
        return SeededAssistant(
            id=assistant.id,
            name=name,
            description=description,
            case_name=case_name,
            run_id=mm_run_id,
        )

    return _create


@pytest.fixture
def marketplace_pending_assistant(
    marketplace_assistant: Callable[[str], SeededAssistant],
) -> Callable[[str], SeededAssistant]:
    """Create, then submit through the real queue service, reaching PENDING_REVIEW."""
    from codemie.service.marketplace_management.queue_service import MarketplaceQueueService

    def _create(case_name: str) -> SeededAssistant:
        seeded = marketplace_assistant(case_name)
        MarketplaceQueueService().submit(
            assistant_id=seeded.id,
            actor="e2e-fixture",
            submitted_by="e2e-fixture",
        )
        return seeded

    return _create
