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

"""Moderation queue ordering and the submission timestamp (EXECUTION-PLAN 5.6).

Traces F-94 (the queue has no defined order) and F-31 (`submitted_at` is
declared and never populated).

Why a real PostgreSQL and not a monkeypatched `list_pending` (F-94):

The defect is that ordering does not exist in the query. `list_pending` issued
a bare `SELECT ... WHERE marketplace_status = 'PENDING_REVIEW'` with no
`ORDER BY`, and the router then sliced the whole result set in Python
(`marketplace_management.py:_pending_payload`). PostgreSQL is free to return
those rows in any order it likes, and it changes that order as the table is
updated -- so page 1 and page 2 of the same queue could overlap, or drop a
submission entirely. A test that stubs `list_pending` with a Python list proves
only that the list it handed in came back; the bug lives between the WHERE
clause and the LIMIT, which is precisely the part a stub replaces.

The database used is a throwaway created and dropped by this module. It holds
only the tables the queue touches, built from the SQLModel metadata,
so the dev database is never touched. If PostgreSQL is unreachable these tests
**fail** skipped when the database is unavailable -- a skipped ordering test is indistinguishable from
a passing one (F-40).

Seeding note: the rows are inserted directly rather than driven through a
publish call, because the approval gate ships OFF until Phase 7 and there is no
product path that produces a `PENDING_REVIEW` assistant yet. `submit()` -- the
service operation Phase 7.1 will call from the publish branch -- is exercised
for real by `test_submitted_at_is_populated_on_submission`.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest
from tests.codemie.marketplace_fixtures.mounted_app import mounted_marketplace_app
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine, make_url
from sqlmodel import Session, SQLModel, create_engine

from codemie.configs import config as _config
from codemie.service.activity.activity_models import ActivityEvent
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.rest_api.models.user_management import UserDB
from codemie.service.marketplace_management.models import (
    MarketplaceAuditRun,
    MarketplaceReviewDecision,
    MarketplaceStatus,
)

# The dev stack from `docker-compose.yml` (postgres/password on :5432).
# `pytest.ini` deliberately points PG_URL at a dead port, so this cannot read
# the application config -- it is its own, overridable, connection.
_ADMIN_URL = os.getenv("MM_TEST_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")
_SCHEMA = "codemie"

PENDING = "/v1/marketplace-management/entities/assistants/pending"

_UNREACHABLE = """\
Cannot reach PostgreSQL at {url}.

These tests assert an ORDER BY that only a real server can honour, so they fail
skipped when the database is unavailable. Start the dev stack:

    cd codemie && docker compose up -d postgres

or point this file at another instance with MM_TEST_PG_URL.

Underlying error: {err}
"""


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    """A throwaway database holding only the tables the queue touches."""
    admin_url = make_url(_ADMIN_URL)
    db_name = f"mm_queue_test_{uuid.uuid4().hex[:10]}"
    try:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
        with admin.connect() as conn:
            conn.execute(text(f'create database "{db_name}"'))
    except Exception as exc:  # noqa: BLE001 - reported verbatim below
        pytest.skip(_UNREACHABLE.format(url=admin_url.render_as_string(hide_password=True), err=exc))

    # Same search_path shape the application engine uses (PostgresClient.get_engine).
    test_engine = create_engine(
        admin_url.set(database=db_name),
        pool_pre_ping=True,
        connect_args={"options": f"-c search_path={_SCHEMA},public"},
    )
    with test_engine.connect() as conn:
        conn.execute(text(f"create schema if not exists {_SCHEMA}"))
        # `assistants` carries gin_trgm_ops indexes and a vector column.
        conn.execute(text("create extension if not exists pg_trgm"))
        conn.execute(text("create extension if not exists vector"))
        conn.commit()
    # `users` comes along because `assistants.marketplace_decided_by` and
    # `mm_review_decision.decided_by_id` both FK it (migration o1r2e3v4i5e6);
    # `mm_review_decision` because every queue transition now writes one in the
    # same transaction as the compare-and-set (EXECUTION-PLAN 6.1), and
    # `mm_audit_run` because a decision may pin the audit run whose evidence the
    # admin acted on (`mm_review_decision.audit_run_id`).
    # `activity_events` because every queue transition now appends its audit
    # event there, in the same transaction as the compare-and-set
    # (EXECUTION-PLAN 9.3, F-35).
    SQLModel.metadata.create_all(
        test_engine,
        tables=[
            UserDB.__table__,
            Assistant.__table__,
            ActivityEvent.__table__,
            MarketplaceAuditRun.__table__,
            MarketplaceReviewDecision.__table__,
        ],
    )
    yield test_engine
    test_engine.dispose()
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
    yield engine
    with engine.connect() as conn:
        conn.execute(text(f"delete from {_SCHEMA}.mm_review_decision"))
        conn.execute(text(f"delete from {_SCHEMA}.activity_events"))
        conn.execute(text(f"delete from {_SCHEMA}.assistants"))
        conn.commit()


@pytest.fixture(scope="module")
def app() -> Iterator[FastAPI]:
    """The production ASGI app, with the marketplace-management router mounted."""
    yield from mounted_marketplace_app()


@pytest.fixture
def admin_client(app: FastAPI) -> Iterator[TestClient]:
    """TestClient over the real app with an admin identity injected."""
    user = User(
        id="admin-1",
        username="admin",
        name="Admin",
        email="admin@codemie.ai",
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


def _seed_pending(engine: Engine, submissions: list[tuple[str, datetime | None]]) -> None:
    """Insert PENDING_REVIEW assistants with the given submission times.

    Inserted directly: the approval gate ships OFF until Phase 7, so no product
    path produces a PENDING_REVIEW row yet.
    """
    with Session(engine) as session:
        for assistant_id, submitted_at in submissions:
            session.add(
                Assistant(
                    id=assistant_id,
                    name=f"assistant {assistant_id}",
                    description="a pending assistant",
                    system_prompt="you are a pending assistant",
                    project="demo",
                    creator="someone",
                    is_global=False,
                    marketplace_status=MarketplaceStatus.PENDING_REVIEW.value,
                    marketplace_submitted_at=submitted_at,
                )
            )
        session.commit()


def _ids(payload: dict) -> list[str]:
    return [item["assistant_id"] for item in payload["items"]]


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_pending_queue_is_oldest_first(db: Engine, admin_client: TestClient) -> None:
    """The oldest submission is reviewed first (specs D-53, P2-18).

    The rows are inserted newest-first so that "whatever Postgres returns" is
    the wrong answer: without an ORDER BY the heap order is the insert order,
    which is exactly backwards.
    """
    base = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    _seed_pending(
        db,
        [
            ("newest", base + timedelta(days=2)),
            ("middle", base + timedelta(days=1)),
            ("oldest", base),
        ],
    )

    response = admin_client.get(PENDING)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 3
    assert _ids(payload) == ["oldest", "middle", "newest"]


def test_pending_ordering_survives_paging(db: Engine, admin_client: TestClient) -> None:
    """Page 1 and page 2 are disjoint and jointly ordered.

    This is what an unordered query actually costs: with no `ORDER BY`, two
    `LIMIT/OFFSET` reads of the same queue are two independent samples, so a
    submission can appear on both pages or on neither.
    """
    base = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    _seed_pending(db, [(f"a-{i}", base + timedelta(hours=i)) for i in reversed(range(6))])

    first = admin_client.get(PENDING, params={"page": 1, "page_size": 3})
    second = admin_client.get(PENDING, params={"page": 2, "page_size": 3})

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    page_1, page_2 = _ids(first.json()), _ids(second.json())

    assert page_1 == ["a-0", "a-1", "a-2"]
    assert page_2 == ["a-3", "a-4", "a-5"]
    assert not set(page_1) & set(page_2), "a submission appeared on two pages"
    assert page_1 + page_2 == sorted(page_1 + page_2), "the pages are not jointly ordered"
    assert first.json()["total"] == 6, "total must count the queue, not the page"


def test_pending_paging_is_pushed_into_sql(db: Engine, admin_client: TestClient) -> None:
    """One page must not cost a full table read.

    The router loaded every pending row and sliced the list in Python
    (`marketplace_management.py:_pending_payload`), so a queue of ten thousand
    submissions built ten thousand `PendingRow` models to render twenty-five of
    them. This watches the statements the driver actually issues, because the
    response body looks identical either way.
    """
    from sqlalchemy import event

    base = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    _seed_pending(db, [(f"b-{i:02d}", base + timedelta(hours=i)) for i in range(30)])

    statements: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, PLR0913
        statements.append(" ".join(statement.split()))

    event.listen(db, "before_cursor_execute", _record)
    try:
        response = admin_client.get(PENDING, params={"page": 2, "page_size": 5})
    finally:
        event.remove(db, "before_cursor_execute", _record)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert _ids(payload) == [f"b-{i:02d}" for i in range(5, 10)]
    assert payload["total"] == 30, "total must count the queue, not the page"

    selects = [s for s in statements if s.lower().startswith("select") and " from assistants" in s.lower()]
    assert selects, f"no SELECT against assistants was issued: {statements}"
    row_reads = [s for s in selects if "count(" not in s.lower()]
    assert row_reads, f"the queue never read any rows: {selects}"
    for statement in row_reads:
        lowered = statement.lower()
        assert " limit " in lowered, f"the page must be bounded in SQL, not sliced in Python: {statement}"
        assert " order by " in lowered, f"a LIMIT without an ORDER BY samples the queue at random: {statement}"


def test_rows_with_no_submission_time_sort_last(db: Engine, admin_client: TestClient) -> None:
    """Legacy rows carry NULL, and must not monopolise the front of the queue.

    Every assistant that exists today was published before the gate was armed,
    so back-dating them is not an option; NULL means "unknown", and unknown
    goes last rather than first.
    """
    base = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    _seed_pending(db, [("unknown", None), ("dated", base)])

    payload = admin_client.get(PENDING).json()

    assert _ids(payload) == ["dated", "unknown"]


# ---------------------------------------------------------------------------
# The submission timestamp
# ---------------------------------------------------------------------------


def test_submitted_at_is_populated_on_submission(db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """`submit()` stamps the queue-entry time and records a SUBMITTED event.

    `marketplace_submitted_at` existed as a declared response field with no
    writer anywhere in the codebase (F-31), and `_record_event` was called only
    from approve / reject / force_publish / withdraw -- so the one transition
    that puts a row *into* the queue left no trace at all.
    """
    from codemie.service.marketplace_management.queue_service import MarketplaceQueueService

    with Session(db) as session:
        session.add(
            Assistant(
                id="fresh",
                name="Fresh Assistant",
                description="about to be submitted",
                system_prompt="you are fresh",
                project="demo",
                creator="someone",
                is_global=False,
                marketplace_status=MarketplaceStatus.DRAFT.value,
            )
        )
        session.commit()

    monkeypatch.setattr(_config, "ACTIVITY_EVENTS_ENABLED", True)

    before = datetime.now(timezone.utc)
    MarketplaceQueueService().submit(assistant_id="fresh", actor="alice")
    after = datetime.now(timezone.utc)

    with Session(db) as session:
        row = session.get(Assistant, "fresh")
        assert row is not None
        assert row.marketplace_status == MarketplaceStatus.PENDING_REVIEW.value
        stamped = row.marketplace_submitted_at
        assert stamped is not None, "submission left no timestamp"
        if stamped.tzinfo is None:
            stamped = stamped.replace(tzinfo=timezone.utc)
        assert before - timedelta(seconds=1) <= stamped <= after + timedelta(seconds=1), (
            f"marketplace_submitted_at={stamped} is not the moment of submission " f"({before} .. {after})"
        )

    # The submission is recorded in `activity_events`, the platform's own
    # append-only log with a reader (EXECUTION-PLAN 9.3, F-35).
    with db.connect() as conn:
        events = conn.execute(
            text(
                "select domain, event_type, entity_type, entity_id, "
                "       attributes ->> 'actor' as actor "
                "  from codemie.activity_events"
            )
        ).all()
    assert [tuple(e) for e in events] == [
        ("marketplace", "marketplace.assistant.submitted", "assistant", "fresh", "alice")
    ]


def test_submission_of_an_unknown_assistant_raises(db: Engine) -> None:
    """Same failure mode as every other queue operation: ValueError -> 404."""
    from codemie.service.marketplace_management.queue_service import MarketplaceQueueService

    with pytest.raises(ValueError):
        MarketplaceQueueService().submit(assistant_id="does-not-exist", actor="alice")


def test_the_pending_row_field_is_named_submitted_at(db: Engine, admin_client: TestClient) -> None:
    """The contract name is `submitted_at`, not `submitted_ts`.

    The backend has always emitted `submitted_at` (`models.PendingRow`), while
    the admin UI reads `item.submitted_ts` and therefore renders an em-dash for
    every row (`MarketplacePendingPage.tsx:334`). Pinning the name here means
    the frontend is the side that has to move.
    """
    _seed_pending(db, [("named", datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc))])

    row = admin_client.get(PENDING).json()["items"][0]

    assert "submitted_at" in row
    assert "submitted_ts" not in row
    assert row["submitted_at"] == datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc).timestamp()


# ---------------------------------------------------------------------------
# Category filter and order_by (EPMCDME-14628)
# ---------------------------------------------------------------------------


def _seed_pending_with_categories(
    engine: Engine,
    rows: list[tuple[str, list[str], datetime | None]],
) -> None:
    """Insert PENDING_REVIEW assistants with specified categories and submission times."""
    with Session(engine) as session:
        for assistant_id, categories, submitted_at in rows:
            session.add(
                Assistant(
                    id=assistant_id,
                    name=f"assistant {assistant_id}",
                    description="a pending assistant",
                    system_prompt="you are a pending assistant",
                    project="demo",
                    creator="someone",
                    is_global=False,
                    marketplace_status=MarketplaceStatus.PENDING_REVIEW.value,
                    marketplace_submitted_at=submitted_at,
                    categories=categories,
                )
            )
        session.commit()


def _patch_category_serializer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the category_service DB lookup during response serialization.

    `Assistant.serialize_categories` calls `category_service.filter_valid_category_ids`
    which queries the `categories` table. That table is not in the throwaway DB
    schema (only the queue-relevant tables are created). Patching here avoids a
    NoReferencedTableError while still exercising the JSONB containment query.
    """
    from codemie.service.assistant import category_service as cs

    monkeypatch.setattr(cs.category_service, "filter_valid_category_ids", lambda ids: ids)


def test_category_filter_returns_only_matching(
    db: Engine, admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """?category=ai returns only assistants whose categories array contains "ai"."""
    _patch_category_serializer(monkeypatch)
    base = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    _seed_pending_with_categories(
        db,
        [
            ("ai-1", ["ai"], base),
            ("ai-2", ["ai", "ml"], base + timedelta(minutes=1)),
            ("ops-1", ["ops"], base + timedelta(minutes=2)),
        ],
    )

    resp = admin_client.get(PENDING + "?category=ai")
    data = resp.json()

    assert resp.status_code == 200, resp.text
    assert data["total"] == 2
    assert all("ai" in r["categories"] for r in data["items"])


def test_category_filter_no_match_returns_empty(
    db: Engine, admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """?category=ops returns an empty page when no pending row has that category."""
    _patch_category_serializer(monkeypatch)
    base = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    _seed_pending_with_categories(db, [("ai-only", ["ai"], base)])

    resp = admin_client.get(PENDING + "?category=ops")
    data = resp.json()

    assert resp.status_code == 200, resp.text
    assert data["total"] == 0
    assert data["items"] == []


def test_order_by_submitted_desc(db: Engine, admin_client: TestClient) -> None:
    """?order_by=submitted_desc returns newest submissions first."""
    base = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    _seed_pending(
        db,
        [
            ("t1", base),
            ("t2", base + timedelta(hours=1)),
            ("t3", base + timedelta(hours=2)),
        ],
    )

    resp = admin_client.get(PENDING + "?order_by=submitted_desc")
    items = [i for i in resp.json()["items"] if i["submitted_at"] is not None]

    assert resp.status_code == 200, resp.text
    assert len(items) >= 2
    for i in range(len(items) - 1):
        assert items[i]["submitted_at"] >= items[i + 1]["submitted_at"]


def test_order_by_name_asc(db: Engine, admin_client: TestClient) -> None:
    """?order_by=name_asc returns assistants sorted alphabetically by name."""
    base = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    with Session(db) as session:
        for name, aid in [("Zebra", "z-1"), ("Alpha", "a-1"), ("Mango", "m-1")]:
            session.add(
                Assistant(
                    id=aid,
                    name=name,
                    description="a pending assistant",
                    system_prompt="you are a pending assistant",
                    project="demo",
                    creator="someone",
                    is_global=False,
                    marketplace_status=MarketplaceStatus.PENDING_REVIEW.value,
                    marketplace_submitted_at=base,
                )
            )
        session.commit()

    resp = admin_client.get(PENDING + "?order_by=name_asc")
    names = [i["name"] for i in resp.json()["items"]]

    assert resp.status_code == 200, resp.text
    assert names == sorted(names)


def test_invalid_order_by_returns_422(admin_client: TestClient) -> None:
    """An unknown order_by value must return 422 (FastAPI enum validation)."""
    resp = admin_client.get(PENDING + "?order_by=bad_value")
    assert resp.status_code == 422, resp.text
