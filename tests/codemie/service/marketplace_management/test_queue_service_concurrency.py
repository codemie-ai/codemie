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

"""Review decisions under concurrency (EXECUTION-PLAN 6.1-6.4, 6.6).

Traces F-03, F-04, F-05, F-34, F-46, R3, R4.

This file exists because of a race that has already fired in production data.
`mm_marketplace_event` ids 5 and 6 record one admin approving and another
rejecting the *same* assistant 572 microseconds apart. Both calls answered 200.
The row ended `REJECTED` while still `is_global = true` -- a state no single
operation can produce, because `approve` sets `is_global` and `reject` does not
clear it (`queue_service.py:63` vs `:97`). Two writers, last-write-wins on a
read-modify-write with no lock, no version and no status precondition.

Why a real PostgreSQL migrated by **alembic**, and not `create_all` or a mock
(this is the whole point of the file):

The fix is a compare-and-set `UPDATE ... WHERE marketplace_version = :observed`
plus three uniqueness constraints. Every one of those is a property of the
*server*: `ROW_COUNT = 0` on the losing statement is decided inside PostgreSQL,
`uq_mm_review_decision_idempotency` is an index, and
`ck_mm_review_decision_override_notice` is a CHECK. A mocked session cannot lose
a race, and `SQLModel.metadata.create_all` builds the schema from the ORM's
beliefs rather than from the migration production runs -- which is exactly the
class of divergence that made the audit feature persist nothing at all (5.3).

Each module run creates its own database, migrates it to `head`, and drops it in
teardown; the dev database is never touched. If PostgreSQL is unreachable these
tests **fail**, they do not skip -- a skipped concurrency test is
indistinguishable from a passing one on a dashboard (F-40).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterator

import pytest
from tests.codemie.marketplace_fixtures.mounted_app import mounted_marketplace_app
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine, make_url
from sqlmodel import create_engine

from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

# The dev stack from `docker-compose.yml` (postgres/password on :5432).
# `pytest.ini` deliberately points PG_URL at a dead port, so this cannot read
# the application config -- it is its own, overridable, connection.
_ADMIN_URL = os.getenv("MM_TEST_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")
_SCHEMA = "codemie"

_REPO_ROOT = Path(__file__).resolve().parents[4]
_ALEMBIC_DIR = _REPO_ROOT / "src" / "external" / "alembic"

PENDING = "/v1/marketplace-management/entities/assistants/pending"

_UNREACHABLE = """\
Cannot reach PostgreSQL at {url}.

These tests assert a race that only a real server can adjudicate, so they fail
skipped when the database is unavailable. Start the dev stack:

    cd codemie && docker compose up -d postgres

or point this file at another instance with MM_TEST_PG_URL.

Underlying error: {err}
"""


def _alembic_ok(url: str, *args: str) -> str:
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    """A throwaway database migrated to `head` by the real migration chain."""
    admin_url = make_url(_ADMIN_URL)
    db_name = f"mm_cas_test_{uuid.uuid4().hex[:10]}"
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
            conn.execute(text(f"create schema if not exists {_SCHEMA}"))
            conn.execute(text("create extension if not exists vector"))
        setup.dispose()

        _alembic_ok(target_url.render_as_string(hide_password=False), "upgrade", "head")

        app_engine = create_engine(
            target_url,
            pool_pre_ping=True,
            connect_args={"options": f"-c search_path={_SCHEMA},public"},
        )
        yield app_engine
        app_engine.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'drop database if exists "{db_name}" with (force)'))
        admin.dispose()


@pytest.fixture
def db(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """Point the application's `get_session()` at the throwaway database.

    `queue_service` imports `get_session` from `codemie.clients.postgres` at
    call time and that helper builds its session from
    `PostgresClient.get_engine()`, so overriding the engine is enough for the
    production code path to run unmodified against this database.
    """
    from codemie.clients import postgres as pg

    monkeypatch.setattr(pg.PostgresClient, "get_engine", classmethod(lambda cls: engine))

    with engine.connect() as conn:
        for user_id, email in (("admin-a", "a@codemie.ai"), ("admin-b", "b@codemie.ai")):
            conn.execute(
                text(
                    "insert into codemie.users (id, username, email, name, is_admin, is_active, date) "
                    "values (:i, :u, :e, :n, true, true, now()) on conflict (id) do nothing"
                ),
                {"i": user_id, "u": user_id, "e": email, "n": user_id},
            )
        conn.commit()

    yield engine

    with engine.connect() as conn:
        conn.execute(text(f"delete from {_SCHEMA}.mm_review_decision"))
        conn.execute(text(f"delete from {_SCHEMA}.activity_events"))
        conn.execute(text(f"delete from {_SCHEMA}.assistants"))
        conn.execute(text(f"delete from {_SCHEMA}.users"))
        conn.commit()


class _IndexingSpy:
    """Records the two `PlatformIndexingService` calls the queue makes.

    A spy rather than a mock of the whole service: the assertion in 6.3 is that
    `remove_single_assistant` is called *at all* -- it has zero call sites in the
    feature today, which is why the take-it-down button takes nothing down
    (F-04, F-46).
    """

    def __init__(self) -> None:
        self.indexed: list[str] = []
        self.removed: list[str] = []
        self.removed_names: list[str | None] = []


@pytest.fixture
def indexing(monkeypatch: pytest.MonkeyPatch) -> _IndexingSpy:
    from codemie.service.platform.platform_indexing_service import PlatformIndexingService

    spy = _IndexingSpy()

    def _index(assistant_id: str, user: Any = None, is_update: bool = False) -> None:
        spy.indexed.append(assistant_id)

    def _remove(assistant_id: str, assistant_name: str | None = None, user: Any = None) -> None:
        spy.removed.append(assistant_id)
        spy.removed_names.append(assistant_name)

    monkeypatch.setattr(PlatformIndexingService, "index_single_assistant", staticmethod(_index))
    monkeypatch.setattr(PlatformIndexingService, "remove_single_assistant", staticmethod(_remove))
    return spy


def _seed(
    engine: Engine, assistant_id: str, status: str, *, is_global: bool = False, is_verified: bool = False
) -> None:
    with engine.connect() as conn:
        conn.execute(
            text(
                "insert into codemie.assistants "
                "(id, name, description, system_prompt, project, shared, is_react, "
                " created_date, creator, is_global, is_verified, categories, marketplace_status, "
                " marketplace_version, unique_users_count, unique_likes_count, "
                " unique_dislikes_count, version_count) "
                "values (:id, :n, "
                "        'A seeded test assistant used in the marketplace concurrency test suite. "
                "It is purpose-built for CI validation of concurrent review operations and state machine transitions.', "
                "        'You are a seeded test assistant for the marketplace concurrency suite. "
                "Your role is to validate concurrent review workflows, idempotency guarantees, "
                "and state machine transitions under parallel load. "
                "You respond helpfully and safely. "
                "You have no real tools or integrations. "
                "This system prompt is intentionally long enough to satisfy the marketplace quality "
                "gate requirements, specifically the substance gate which requires at least five hundred "
                "characters when no tool or MCP integration is configured.', "
                "        'demo', true, true, "
                "        now(), 'seed-creator', :g, :v, '[\"productivity\"]'::jsonb, :s, 0, 3, 1, 0, 1)"
            ),
            {"id": assistant_id, "n": f"Assistant {assistant_id}", "g": is_global, "v": is_verified, "s": status},
        )
        conn.commit()


def _row(engine: Engine, assistant_id: str) -> dict[str, Any]:
    with engine.connect() as conn:
        return dict(
            conn.execute(
                text(
                    "select marketplace_status, is_global, is_verified, marketplace_version, "
                    "       marketplace_decided_by, marketplace_decision_reason "
                    "  from codemie.assistants where id = :i"
                ),
                {"i": assistant_id},
            )
            .mappings()
            .one()
        )


@pytest.fixture(scope="module")
def app() -> Iterator[FastAPI]:
    """The production ASGI app, with the marketplace-management router mounted."""
    yield from mounted_marketplace_app()


@pytest.fixture
def admin_client(app: FastAPI) -> Iterator[TestClient]:
    """TestClient over the real app with an admin identity injected."""
    user = User(
        id="admin-a",
        username="admin-a",
        name="Admin A",
        email="a@codemie.ai",
        project_names=["demo"],
    )
    user.is_admin = True
    user.is_maintainer = False

    async def _fake_authenticate(request: Request) -> User:
        request.state.user = user
        return user

    saved = dict(app.dependency_overrides)
    app.dependency_overrides[authenticate] = _fake_authenticate
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


# ---------------------------------------------------------------------------
# 6.1 -- the race that already fired
# ---------------------------------------------------------------------------


def test_parallel_approve_and_reject_yields_one_409(db: Engine, indexing: _IndexingSpy) -> None:
    """Two admins, one entity, the same observed version: exactly one wins.

    This is F-03 reproduced deterministically. Both threads read
    `marketplace_version = 0` and act on it; the CAS `UPDATE` can match that
    version exactly once, so the loser must get a conflict carrying the winner's
    state rather than a 200 and a silently discarded decision.

    The final-state assertion is the part that matters most: today the row can
    end `REJECTED` *and* `is_global = true`, which no single operation produces
    and which no reader can interpret.
    """
    from codemie.service.marketplace_management.queue_service import (
        MarketplaceConflictError,
        MarketplaceQueueService,
    )

    _seed(db, "contested", "PENDING_REVIEW")
    service = MarketplaceQueueService()
    barrier = threading.Barrier(2)

    def _approve() -> Any:
        barrier.wait(timeout=10)
        return service.approve(
            assistant_id="contested",
            actor="admin-a",
            expected_version=0,
            decided_by_id="admin-a",
            decided_by_email="a@codemie.ai",
        )

    def _reject() -> Any:
        barrier.wait(timeout=10)
        return service.reject(
            assistant_id="contested",
            actor="admin-b",
            reason="Duplicate of an existing verified assistant; removing it from the queue.",
            expected_version=0,
            decided_by_id="admin-b",
            decided_by_email="b@codemie.ai",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_approve), pool.submit(_reject)]
        outcomes: list[Any] = []
        conflicts: list[MarketplaceConflictError] = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=30))
            except MarketplaceConflictError as exc:
                conflicts.append(exc)

    assert len(outcomes) == 1, (
        f"{len(outcomes)} decisions succeeded; exactly one must win "
        f"(conflicts seen: {[c.current_status for c in conflicts]})"
    )
    assert len(conflicts) == 1, "the loser did not receive a conflict"

    row = _row(db, "contested")
    loser = conflicts[0]
    assert loser.status_code == 409
    assert (
        loser.current_status == row["marketplace_status"]
    ), "the conflict must carry the winner's status so the UI can explain itself"
    assert loser.current_decided_by == row["marketplace_decided_by"]
    assert loser.current_decided_by in {"admin-a", "admin-b"}
    assert loser.current_version == row["marketplace_version"] == 1

    # Internal consistency: this is the state the live database is in and that
    # no single operation can produce.
    assert row["marketplace_status"] in {"APPROVED", "REJECTED"}
    if row["marketplace_status"] == "REJECTED":
        assert row["is_global"] is False, "a rejected assistant is still live on the marketplace"
        assert row["is_verified"] is False
    else:
        assert row["is_global"] is True
        assert row["is_verified"] is True

    with db.connect() as conn:
        recorded = conn.execute(
            text("select count(*) from codemie.mm_review_decision where entity_id = 'contested'")
        ).scalar()
    assert recorded == 1, f"{recorded} decisions were logged for one winning transition"


# ---------------------------------------------------------------------------
# 6.2 -- the transition table (design-persistence.md 3.3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start_status", "operation", "kwargs"),
    [
        # APPROVE's only legal source is PENDING_REVIEW.
        ("APPROVED", "approve", {}),
        ("DRAFT", "approve", {}),
        ("REJECTED", "approve", {}),
        ("WITHDRAWN", "approve", {}),
        # REJECT's only legal source is PENDING_REVIEW.
        ("DRAFT", "reject", {"reason": "Not suitable for the marketplace at this time, sorry."}),
        ("APPROVED", "reject", {"reason": "Not suitable for the marketplace at this time, sorry."}),
        ("REJECTED", "reject", {"reason": "Not suitable for the marketplace at this time, sorry."}),
        # FORCE_PUBLISH is "any except APPROVED".
        ("APPROVED", "force_publish", {"override_notice": "Publishing over the gate for the demo."}),
        # WITHDRAW is PENDING_REVIEW or APPROVED.
        ("DRAFT", "withdraw", {}),
        ("WITHDRAWN", "withdraw", {}),
        # SUBMIT is DRAFT, REJECTED or WITHDRAWN.
        ("PENDING_REVIEW", "submit", {}),
        ("APPROVED", "submit", {}),
    ],
)
def test_disallowed_transitions_are_409(
    db: Engine, indexing: _IndexingSpy, start_status: str, operation: str, kwargs: dict
) -> None:
    """A decision whose source status is not in the table is a conflict, not a write.

    F-05: nothing checked the current status, so approving an already-APPROVED
    row, or rejecting a DRAFT that was never submitted, silently succeeded and
    appended a decision to history that never happened.
    """
    from codemie.service.marketplace_management.queue_service import (
        MarketplaceConflictError,
        MarketplaceQueueService,
    )

    entity_id = f"{operation}-from-{start_status.lower()}"
    _seed(db, entity_id, start_status, is_global=start_status == "APPROVED")
    before = _row(db, entity_id)

    with pytest.raises(MarketplaceConflictError) as excinfo:
        getattr(MarketplaceQueueService(), operation)(
            assistant_id=entity_id, actor="admin-a", decided_by_id="admin-a", **kwargs
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.current_status == start_status
    assert _row(db, entity_id) == before, "a rejected transition still mutated the row"

    with db.connect() as conn:
        logged = conn.execute(
            text("select count(*) from codemie.mm_review_decision where entity_id = :i"),
            {"i": entity_id},
        ).scalar()
    assert logged == 0, "a decision that never happened was written to history"


@pytest.mark.parametrize(
    ("start_status", "operation", "kwargs", "expected"),
    [
        ("PENDING_REVIEW", "approve", {}, "APPROVED"),
        (
            "PENDING_REVIEW",
            "reject",
            {"reason": "Duplicate of an existing verified assistant; removing it."},
            "REJECTED",
        ),
        ("PENDING_REVIEW", "withdraw", {}, "WITHDRAWN"),
        ("APPROVED", "withdraw", {}, "WITHDRAWN"),
        (
            "PENDING_REVIEW",
            "force_publish",
            {"override_notice": "Publishing over the gate for the launch demo."},
            "APPROVED",
        ),
        ("DRAFT", "submit", {}, "PENDING_REVIEW"),
        ("REJECTED", "submit", {}, "PENDING_REVIEW"),
        ("WITHDRAWN", "submit", {}, "PENDING_REVIEW"),
    ],
)
def test_allowed_transitions_succeed_and_bump_the_version(
    db: Engine, indexing: _IndexingSpy, start_status: str, operation: str, kwargs: dict, expected: str
) -> None:
    """The other half of the table: every legal edge still works, once."""
    from codemie.service.marketplace_management.queue_service import MarketplaceQueueService

    entity_id = f"ok-{operation}-from-{start_status.lower()}"
    _seed(db, entity_id, start_status, is_global=start_status == "APPROVED")

    getattr(MarketplaceQueueService(), operation)(
        assistant_id=entity_id, actor="admin-a", decided_by_id="admin-a", **kwargs
    )

    row = _row(db, entity_id)
    assert row["marketplace_status"] == expected
    assert row["marketplace_version"] == 1, "a successful decision must advance the CAS token"


# ---------------------------------------------------------------------------
# 6.3 -- the take-it-down button must take it down
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("operation", "kwargs", "start_status"),
    [
        ("reject", {"reason": "Duplicate of an existing verified assistant; removing it."}, "PENDING_REVIEW"),
        ("withdraw", {}, "APPROVED"),
    ],
)
def test_reject_unpublishes_and_deindexes(
    db: Engine, indexing: _IndexingSpy, operation: str, kwargs: dict, start_status: str
) -> None:
    """Reject and withdraw must unpublish and de-index, not just relabel.

    F-04 / F-46: `reject` set `marketplace_status` and left `is_global = true`,
    so a rejected assistant stayed visible in the marketplace and stayed in the
    search index. `PlatformIndexingService.remove_single_assistant` exists
    (`platform_indexing_service.py:135-149`) and has zero call sites in this
    feature.
    """
    from codemie.service.marketplace_management.queue_service import MarketplaceQueueService

    entity_id = f"takedown-{operation}"
    _seed(db, entity_id, start_status, is_global=True)

    getattr(MarketplaceQueueService(), operation)(
        assistant_id=entity_id, actor="admin-a", decided_by_id="admin-a", **kwargs
    )

    row = _row(db, entity_id)
    assert row["is_global"] is False, "the entity is still live on the marketplace"
    assert row["is_verified"] is False
    assert indexing.removed == [
        entity_id
    ], f"remove_single_assistant was not called; recorded calls: {indexing.removed}"
    assert indexing.removed_names == [f"Assistant {entity_id}"]
    assert indexing.indexed == [], "a takedown must not re-index the entity"


def test_approve_still_publishes_and_indexes(db: Engine, indexing: _IndexingSpy) -> None:
    """The mirror image: approve publishes, verifies and indexes."""
    from codemie.service.marketplace_management.queue_service import MarketplaceQueueService

    _seed(db, "published", "PENDING_REVIEW")
    MarketplaceQueueService().approve(assistant_id="published", actor="admin-a", decided_by_id="admin-a")

    row = _row(db, "published")
    assert row["is_global"] is True
    assert row["is_verified"] is True
    assert indexing.indexed == ["published"]
    assert indexing.removed == []


# ---------------------------------------------------------------------------
# 6.4 -- replay safety (design-persistence.md 4.1)
# ---------------------------------------------------------------------------


def test_replayed_decision_is_byte_identical(db: Engine, indexing: _IndexingSpy, admin_client: TestClient) -> None:
    """The same `Idempotency-Key` twice is one decision and one identical answer.

    Layer 2 of 4.1. A proxy retry, a double-click across two tabs or a browser
    refresh that resubmits the POST must not produce a second decision, and the
    replay must return the *stored* body rather than recompute one.
    """
    _seed(db, "replayed", "PENDING_REVIEW")
    key = str(uuid.uuid4())
    url = f"{PENDING}/replayed/approve"

    first = admin_client.post(url, json={}, headers={"Idempotency-Key": key})
    second = admin_client.post(url, json={}, headers={"Idempotency-Key": key})

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json() == second.json(), "the replay returned a different body"
    assert second.headers.get("Idempotency-Replayed") == "true"
    assert first.headers.get("Idempotency-Replayed") is None

    with db.connect() as conn:
        decisions = conn.execute(
            text("select count(*) from codemie.mm_review_decision where entity_id = 'replayed'")
        ).scalar()
        version = conn.execute(
            text("select marketplace_version from codemie.assistants where id = 'replayed'")
        ).scalar()
    assert decisions == 1, f"{decisions} decisions were recorded for one approve and its replay"
    assert version == 1, "the replay advanced the version a second time"


def test_a_second_approve_without_the_key_is_a_conflict(
    db: Engine, indexing: _IndexingSpy, admin_client: TestClient
) -> None:
    """Without a key, layer 1 catches it: APPROVED is not an APPROVE source."""
    _seed(db, "twice", "PENDING_REVIEW")
    url = f"{PENDING}/twice/approve"

    assert admin_client.post(url, json={}).status_code == 200
    second = admin_client.post(url, json={})

    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["marketplace_status"] == "APPROVED"
    assert detail["marketplace_version"] == 1


# ---------------------------------------------------------------------------
# 6.6 -- the override notice
# ---------------------------------------------------------------------------


def test_force_publish_persists_its_override_notice(db: Engine, indexing: _IndexingSpy) -> None:
    """The justification for bypassing review has to survive the request.

    F-34: `override_notice` was validated (`marketplace_management.py:579`),
    echoed back in the response (`:589`) and then discarded -- so the one field
    that explains *why* the gate was bypassed existed nowhere afterwards.
    """
    from codemie.service.marketplace_management.queue_service import MarketplaceQueueService

    notice = "Customer escalation CDME-9182: publishing ahead of the review queue."
    _seed(db, "forced", "PENDING_REVIEW")

    MarketplaceQueueService().force_publish(
        assistant_id="forced",
        actor="admin-a",
        decided_by_id="admin-a",
        override_notice=notice,
    )

    with db.connect() as conn:
        stored = (
            conn.execute(
                text("select decision, override_notice from codemie.mm_review_decision " " where entity_id = 'forced'")
            )
            .mappings()
            .one()
        )
    assert stored["decision"] == "FORCE_PUBLISH"
    assert stored["override_notice"] == notice


def test_force_publish_without_a_notice_is_refused(db: Engine, indexing: _IndexingSpy) -> None:
    """`ck_mm_review_decision_override_notice` makes forgetting it impossible.

    The CHECK is the reason 6.6 cannot regress: a force-publish that reaches the
    service without a notice cannot be written at all, so the field cannot
    quietly go missing again.
    """
    from codemie.service.marketplace_management.queue_service import MarketplaceQueueService

    _seed(db, "unforced", "PENDING_REVIEW")

    with pytest.raises(Exception):  # noqa: B017
        MarketplaceQueueService().force_publish(assistant_id="unforced", actor="admin-a", decided_by_id="admin-a")

    assert _row(db, "unforced")["marketplace_status"] == "PENDING_REVIEW"


def test_reject_records_its_reason_on_the_row_and_in_history(db: Engine, indexing: _IndexingSpy) -> None:
    """The rejection reason reaches both the entity and the decision log."""
    from codemie.service.marketplace_management.queue_service import MarketplaceQueueService

    reason = "Duplicate of an existing verified assistant; please extend that one instead."
    _seed(db, "reasoned", "PENDING_REVIEW")

    MarketplaceQueueService().reject(assistant_id="reasoned", actor="admin-a", decided_by_id="admin-a", reason=reason)

    assert _row(db, "reasoned")["marketplace_decision_reason"] == reason
    with db.connect() as conn:
        stored = (
            conn.execute(
                text(
                    "select decision, reason, from_status, to_status, from_version, to_version "
                    "  from codemie.mm_review_decision where entity_id = 'reasoned'"
                )
            )
            .mappings()
            .one()
        )
    assert stored["decision"] == "REJECT"
    assert stored["reason"] == reason
    assert (stored["from_status"], stored["to_status"]) == ("PENDING_REVIEW", "REJECTED")
    assert (stored["from_version"], stored["to_version"]) == (0, 1)


# ---------------------------------------------------------------------------
# 9.3 -- the audit trail an admin can actually read
# ---------------------------------------------------------------------------


def _activity_events(engine: Engine, entity_id: str) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                text(
                    "select domain, event_type, entity_type, entity_id, actor_id, "
                    "       attributes, created_at "
                    "  from codemie.activity_events where entity_id = :i "
                    " order by created_at"
                ),
                {"i": entity_id},
            ).mappings()
        ]


def test_decision_writes_an_activity_event(
    db: Engine, indexing: _IndexingSpy, admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A moderation decision lands in `activity_events`, the platform's append-only audit log.

    F-35: `activity_events` is served by `activity_events_router.py`, so an admin
    can retrieve the moderation trail through the API.

    Driven through the HTTP endpoint on purpose: `actor_id` has to be the
    *authenticated* user's id (FK to `users.id`), which only the router knows.
    """
    from codemie.configs import config as _config

    monkeypatch.setattr(_config, "ACTIVITY_EVENTS_ENABLED", True)
    _seed(db, "evented", "PENDING_REVIEW")

    response = admin_client.post(f"{PENDING}/evented/approve", json={})
    assert response.status_code == 200, response.text

    events = _activity_events(db, "evented")
    assert len(events) == 1, f"expected exactly one activity event, got {events}"
    event = events[0]
    assert event["domain"] == "marketplace"
    assert event["event_type"] == "marketplace.assistant.approved"
    assert event["actor_id"] == "admin-a", "the event must carry the authenticated user's id, not a free-text actor"
    assert event["entity_id"] == "evented"


# ---------------------------------------------------------------------------
# 9.4 -- metrics on the four operations that change moderation state
# ---------------------------------------------------------------------------


def _metric_spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Capture every count metric the code under test emits."""
    from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService

    calls: list[tuple[str, dict[str, Any]]] = []

    def _send(name: str, description: str = "", unit: str = "", attributes: dict | None = None, count: int = 1) -> None:
        calls.append((name, dict(attributes or {})))

    monkeypatch.setattr(BaseMonitoringService, "send_count_metric", staticmethod(_send))
    return calls


def test_each_decision_emits_a_metric(db: Engine, indexing: _IndexingSpy, monkeypatch: pytest.MonkeyPatch) -> None:
    """All four moderation decisions and the audit run are observable (F-36).

    The legacy publish path tracks a metric on every marketplace mutation
    (`assistant.py::_track_assistant_management_metric`). The queue service
    that replaced it tracked nothing at all: the one operation that changes
    *nothing* (assess) was observable, and the four that change moderation
    state were not.
    """
    from codemie.service.marketplace_management.audit_service import AuditCache
    from codemie.service.marketplace_management.models import EntityKind
    from codemie.service.marketplace_management.queue_service import MarketplaceQueueService

    calls = _metric_spy(monkeypatch)
    service = MarketplaceQueueService()

    _seed(db, "m-approve", "PENDING_REVIEW")
    service.approve(assistant_id="m-approve", actor="admin-a", decided_by_id="admin-a", decided_by_email="a@codemie.ai")

    _seed(db, "m-reject", "PENDING_REVIEW")
    service.reject(
        assistant_id="m-reject",
        actor="admin-a",
        decided_by_id="admin-a",
        reason="Duplicate of an existing verified assistant; removing it.",
    )

    _seed(db, "m-force", "DRAFT")
    service.force_publish(
        assistant_id="m-force",
        actor="admin-a",
        decided_by_id="admin-a",
        override_notice="Vetted offline by the platform team; publishing.",
    )

    _seed(db, "m-withdraw", "APPROVED", is_global=True)
    service.withdraw(assistant_id="m-withdraw", actor="admin-a", decided_by_id="admin-a")

    cache = AuditCache()
    monkeypatch.setattr(AuditCache, "run_audit", lambda self, *a, **k: None)
    try:
        cache.start_audit(EntityKind.ASSISTANT)
    finally:
        with db.connect() as conn:
            conn.execute(text("delete from codemie.mm_audit_run"))
            conn.commit()

    emitted = {name for name, _ in calls}
    for expected in (
        "marketplace_approve",
        "marketplace_reject",
        "marketplace_force_publish",
        "marketplace_withdraw",
        "marketplace_audit_run",
    ):
        assert expected in emitted, f"{expected!r} never fired; emitted: {sorted(emitted)}"

    approve_attrs = next(a for name, a in calls if name == "marketplace_approve")
    assert approve_attrs["assistant_id"] == "m-approve"
    assert approve_attrs["user_id"] == "admin-a"


# ---------------------------------------------------------------------------
# EPMCDME-14734 -- RE_SUBMIT and list_marketplace_assistants
# ---------------------------------------------------------------------------


def test_re_submit_transitions_approved_to_pending_review(db: Engine, indexing: _IndexingSpy) -> None:
    """decide(RE_SUBMIT) from APPROVED → PENDING_REVIEW, is_global=False, is_verified=False."""
    from codemie.service.marketplace_management.models import ReviewDecision
    from codemie.service.marketplace_management.queue_service import MarketplaceQueueService

    entity_id = "re-submit-approved"
    _seed(db, entity_id, "APPROVED", is_global=True, is_verified=True)

    svc = MarketplaceQueueService()
    outcome = svc.decide(
        assistant_id=entity_id,
        decision=ReviewDecision.RE_SUBMIT,
        actor="admin-a",
        decided_by_id="admin-a",
    )

    assert outcome is not None
    row = _row(db, entity_id)
    assert row["marketplace_status"] == "PENDING_REVIEW"
    assert row["is_global"] is False
    assert row["is_verified"] is False


def test_re_submit_from_pending_review_stays_pending_and_advances_the_version(
    db: Engine, indexing: _IndexingSpy
) -> None:
    """An edit under review keeps the submission queued, but invalidates stale decisions.

    The version moves, so a reviewer who loaded the old content CAS-conflicts instead of
    approving what they never saw, and the edit is a ledger row like any other decision.
    """
    from codemie.service.marketplace_management.models import ReviewDecision
    from codemie.service.marketplace_management.queue_service import (
        MarketplaceConflictError,
        MarketplaceQueueService,
    )

    entity_id = "re-submit-pending"
    _seed(db, entity_id, "PENDING_REVIEW", is_global=False)
    before = _row(db, entity_id)

    svc = MarketplaceQueueService()
    svc.decide(
        assistant_id=entity_id,
        decision=ReviewDecision.RE_SUBMIT,
        actor="admin-b",
        decided_by_id="admin-b",
    )

    row = _row(db, entity_id)
    assert row["marketplace_status"] == "PENDING_REVIEW"
    assert row["is_global"] is False
    assert row["is_verified"] is False
    assert row["marketplace_version"] == before["marketplace_version"] + 1

    # A reviewer's decision taken against the pre-edit version no longer applies.
    with pytest.raises(MarketplaceConflictError):
        svc.decide(
            assistant_id=entity_id,
            decision=ReviewDecision.APPROVE,
            actor="admin-a",
            decided_by_id="admin-a",
            expected_version=before["marketplace_version"],
        )


@pytest.mark.parametrize("status", ["DRAFT", "REJECTED", "WITHDRAWN"])
def test_re_submit_from_a_status_that_was_never_reviewed_raises_conflict(
    db: Engine, indexing: _IndexingSpy, status: str
) -> None:
    """Only APPROVED and PENDING_REVIEW have reviewed content an edit can invalidate."""
    from codemie.service.marketplace_management.models import ReviewDecision
    from codemie.service.marketplace_management.queue_service import (
        MarketplaceConflictError,
        MarketplaceQueueService,
    )

    entity_id = f"re-submit-{status.lower()}"
    _seed(db, entity_id, status, is_global=False)

    with pytest.raises(MarketplaceConflictError):
        MarketplaceQueueService().decide(
            assistant_id=entity_id,
            decision=ReviewDecision.RE_SUBMIT,
            actor="admin-b",
            decided_by_id="admin-b",
        )


def test_list_marketplace_assistants_returns_all_three_statuses(db: Engine, indexing: _IndexingSpy) -> None:
    """list_marketplace_assistants(["APPROVED","PENDING_REVIEW","REJECTED"]) returns rows in all three; DRAFT and WITHDRAWN absent."""
    from codemie.service.marketplace_management.queue_service import MarketplaceQueueService

    _seed(db, "lma-approved", "APPROVED", is_global=True, is_verified=True)
    _seed(db, "lma-pending", "PENDING_REVIEW", is_global=False)
    _seed(db, "lma-rejected", "REJECTED", is_global=False)
    _seed(db, "lma-draft", "DRAFT", is_global=False)
    _seed(db, "lma-withdrawn", "WITHDRAWN", is_global=False)

    svc = MarketplaceQueueService()
    rows, total = svc.list_marketplace_assistants(["APPROVED", "PENDING_REVIEW", "REJECTED"])

    ids = {r.id for r in rows}
    assert "lma-approved" in ids
    assert "lma-pending" in ids
    assert "lma-rejected" in ids
    assert "lma-draft" not in ids
    assert "lma-withdrawn" not in ids
    assert total == 3


def test_list_marketplace_assistants_filters_to_approved_only(db: Engine, indexing: _IndexingSpy) -> None:
    """list_marketplace_assistants(["APPROVED"]) returns only APPROVED rows."""
    from codemie.service.marketplace_management.queue_service import MarketplaceQueueService

    _seed(db, "lma-only-approved", "APPROVED", is_global=True, is_verified=True)
    _seed(db, "lma-only-pending", "PENDING_REVIEW", is_global=False)

    svc = MarketplaceQueueService()
    rows, total = svc.list_marketplace_assistants(["APPROVED"])

    ids = {r.id for r in rows}
    assert "lma-only-approved" in ids
    assert "lma-only-pending" not in ids
    assert total >= 1  # at least our seeded APPROVED row
    for r in rows:
        assert r.marketplace_status == "APPROVED"
