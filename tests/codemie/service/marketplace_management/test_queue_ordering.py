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
from datetime import datetime
from typing import Iterator

import pytest
from tests.codemie.marketplace_fixtures.mounted_app import mounted_marketplace_app
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine, make_url
from sqlmodel import Session, SQLModel, create_engine

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


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The submission timestamp
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Category filter and order_by (EPMCDME-14628)
# ---------------------------------------------------------------------------


def test_invalid_order_by_returns_422(admin_client: TestClient) -> None:
    """An unknown order_by value must return 422 (FastAPI enum validation)."""
    resp = admin_client.get(PENDING + "?order_by=bad_value")
    assert resp.status_code == 422, resp.text
