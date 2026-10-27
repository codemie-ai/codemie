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

"""Tests that CleanupRow.detail is surfaced through the API.

EPMCDME-15056: The SQL at audit_read_repository.py:257 already selects
`v.detail`, but the Pydantic model CleanupRow had no `detail` field, so
the value was silently dropped. This test confirms the field propagates
end-to-end.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Iterator

import pytest
from tests.codemie.marketplace_fixtures.mounted_app import mounted_marketplace_app
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine, make_url
from sqlmodel import create_engine

from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

BASE = "/v1/marketplace-management"
CLEANUP = f"{BASE}/entities/assistants/cleanup"

_ADMIN_PG_URL = os.getenv("MM_TEST_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")
_PG_SCHEMA = "codemie"
_ALEMBIC_DIR = Path(__file__).resolve().parents[3] / "src" / "external" / "alembic"


def _pg_reachable() -> bool:
    import socket

    from sqlalchemy.engine import make_url as _make_url

    try:
        u = _make_url(_ADMIN_PG_URL)
        socket.create_connection((u.host or "localhost", u.port or 5432), timeout=2).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_reachable(),
    reason=f"PostgreSQL not reachable at {_ADMIN_PG_URL}",
)

_PG_UNREACHABLE = """\
Cannot reach PostgreSQL at {url}.

`/cleanup` reads SQL; asserting its contract without a database would assert
nothing, so these tests skip when the database is unavailable. Start the dev stack:

    cd codemie && docker compose up -d postgres

or point this file at another instance with MM_TEST_PG_URL.

Underlying error: {err}
"""


# ---------------------------------------------------------------------------
# Infrastructure helpers (mirrors test_marketplace_management_contract.py)
# ---------------------------------------------------------------------------


def _alembic_upgrade(url: str) -> None:
    env = dict(os.environ)
    env["PG_URL"] = url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(_ALEMBIC_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"alembic upgrade head failed ({result.returncode})\n"
            f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
        )


@pytest.fixture(scope="module")
def sql_engine() -> Iterator[Engine]:
    """A throwaway database migrated to `head`, dropped on teardown."""
    admin_url = make_url(_ADMIN_PG_URL)
    db_name = f"mm_detail_test_{uuid.uuid4().hex[:10]}"
    admin = None
    try:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
        with admin.connect() as conn:
            conn.execute(text(f'create database "{db_name}"'))
    except Exception as exc:  # noqa: BLE001 - reported verbatim
        pytest.skip(_PG_UNREACHABLE.format(url=admin_url.render_as_string(hide_password=True), err=exc))

    target = admin_url.set(database=db_name)
    try:
        setup = create_engine(target, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
        with setup.connect() as conn:
            conn.execute(text(f"create schema if not exists {_PG_SCHEMA}"))
            conn.execute(text("create extension if not exists vector"))
        setup.dispose()
        _alembic_upgrade(target.render_as_string(hide_password=False))

        app_engine = create_engine(
            target,
            pool_pre_ping=True,
            connect_args={"options": f"-c search_path={_PG_SCHEMA},public"},
        )
        yield app_engine
        app_engine.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'drop database if exists "{db_name}" with (force)'))
        admin.dispose()


def _insert_run(conn, run_id: str) -> None:
    conn.execute(
        text(
            "insert into codemie.mm_audit_run "
            "(id, entity_kind, started_ts, finished_ts, started_at, finished_at, status, "
            " schema_version, trigger, records_processed, records_failed, total_records, "
            " engine_took_seconds, aggregates) "
            "values (:id, 'ASSISTANT', 1.0, 2.0, now(), now(), 'SUCCEEDED', 2, 'MANUAL', "
            "        0, 0, 2, 0.05, '{}'::jsonb)"
        ),
        {"id": run_id},
    )


def _insert_assistant(
    conn,
    aid: str,
    *,
    name: str,
    likes: int = 0,
    users: int = 0,
    description: str = "d",
    marketplace_status: str = "APPROVED",
    is_global: bool = True,
) -> None:
    conn.execute(
        text(
            "insert into codemie.assistants "
            "(id, name, description, system_prompt, project, shared, is_react, created_date, "
            " creator, is_global, categories, marketplace_status, unique_users_count, "
            " unique_likes_count, unique_dislikes_count, version_count) "
            "values (:id, :n, :d, 'p', 'demo', true, true, now(), 'someone', :g, "
            "        '[]'::jsonb, :ms, :u, :l, 0, 1)"
        ),
        {"id": aid, "n": name, "d": description, "u": users, "l": likes, "g": is_global, "ms": marketplace_status},
    )


def _insert_verdict(conn, run_id: str, aid: str, **cols) -> None:
    payload = {
        "audit_run_id": run_id,
        "assistant_id": aid,
        "bucket": None,
        "bucket_reason": None,
        "verdict_kind": None,
        "cleanup_reason": None,
        "usage_tier": None,
        "users_count": 0,
        "canonical_id": None,
        "canonical_name": None,
        "canonical_users": None,
        "purpose_cluster_id": None,
        "match_score": None,
        "detail": None,
        "critical_count": 0,
        "optional_count": 0,
    }
    payload.update(cols)
    columns = ", ".join(payload)
    binds = ", ".join(f":{k}" for k in payload)
    conn.execute(text(f"insert into codemie.mm_audit_verdict ({columns}) values ({binds})"), payload)


def _point_app_at(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    from codemie.clients import postgres as pg

    monkeypatch.setattr(pg.PostgresClient, "get_engine", classmethod(lambda cls: engine))


# ---------------------------------------------------------------------------
# App / client fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def app() -> Iterator[FastAPI]:
    """The production ASGI app, with the marketplace-management router mounted."""
    yield from mounted_marketplace_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    saved = dict(app.dependency_overrides)
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


def _make_admin_user() -> User:
    user = User(
        id="admin-1",
        username="admin",
        name="Admin",
        email="admin@codemie.ai",
        project_names=["demo"],
    )
    user.is_admin = True
    user.is_maintainer = False
    return user


@pytest.fixture
def admin_client(app: FastAPI, client: TestClient) -> TestClient:
    user = _make_admin_user()

    async def _fake_authenticate(request: Request) -> User:
        request.state.user = user
        return user

    app.dependency_overrides[authenticate] = _fake_authenticate
    return client


# ---------------------------------------------------------------------------
# Data fixture — two assistants, one with detail, one without
# ---------------------------------------------------------------------------


@pytest.fixture
def detail_data(sql_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Insert a run with three verdict rows: CANDIDATE_FOR_CLEANUP (detail set), KEEP (null), REVIEW (detail set)."""
    run_id = "run-detail-test"
    with sql_engine.connect() as conn:
        _insert_run(conn, run_id)
        _insert_assistant(conn, "a-detail-1", name="assistant a-detail-1", users=42, likes=3)
        _insert_assistant(conn, "a-detail-2", name="assistant a-detail-2", users=10, likes=1)
        _insert_assistant(conn, "a-detail-3", name="assistant a-detail-3", users=5, likes=0)
        _insert_verdict(
            conn,
            run_id,
            "a-detail-1",
            verdict_kind="CANDIDATE_FOR_CLEANUP",
            bucket="DELETE",
            detail="Near-duplicate of Foo Bot (42u vs 3u)",
        )
        _insert_verdict(
            conn,
            run_id,
            "a-detail-2",
            verdict_kind="KEEP",
            bucket="VERIFIED",
            detail=None,
        )
        _insert_verdict(
            conn,
            run_id,
            "a-detail-3",
            verdict_kind="REVIEW",
            bucket="COMMUNITY",
            detail="Has 1 critical quality issue.",
        )
        conn.commit()

    _point_app_at(sql_engine, monkeypatch)
    yield

    with sql_engine.connect() as conn:
        conn.execute(text("delete from codemie.mm_audit_run"))
        conn.execute(text("delete from codemie.assistants"))
        conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cleanup_row_surfaces_detail_field(admin_client: TestClient, detail_data) -> None:
    """CleanupRow.detail must be propagated from SQL to the API response.

    EPMCDME-15056: v.detail was already selected in the SQL query but dropped
    by Pydantic because CleanupRow had no `detail` field. After the fix the
    field must appear on every row (populated or null) for all verdict kinds.
    AC10: assert non-null for CANDIDATE_FOR_CLEANUP and REVIEW; null for KEEP.
    """
    # Fetch DELETE bucket rows (a-detail-1 is here — CANDIDATE_FOR_CLEANUP)
    delete_response = admin_client.get(CLEANUP, params={"bucket": "DELETE"})
    assert delete_response.status_code == 200, delete_response.text
    delete_items = {row["id"]: row for row in delete_response.json()["items"]}

    # Fetch VERIFIED bucket rows (a-detail-2 is here — KEEP)
    verified_response = admin_client.get(CLEANUP, params={"bucket": "VERIFIED"})
    assert verified_response.status_code == 200, verified_response.text
    verified_items = {row["id"]: row for row in verified_response.json()["items"]}

    # Fetch COMMUNITY bucket rows (a-detail-3 is here — REVIEW)
    community_response = admin_client.get(CLEANUP, params={"bucket": "COMMUNITY"})
    assert community_response.status_code == 200, community_response.text
    community_items = {row["id"]: row for row in community_response.json()["items"]}

    assert "a-detail-1" in delete_items, "a-detail-1 must appear in DELETE bucket"
    assert "a-detail-2" in verified_items, "a-detail-2 must appear in VERIFIED bucket"
    assert "a-detail-3" in community_items, "a-detail-3 must appear in COMMUNITY bucket"

    row1 = delete_items["a-detail-1"]
    row2 = verified_items["a-detail-2"]
    row3 = community_items["a-detail-3"]

    # The `detail` key must be present on all rows (even when null).
    assert "detail" in row1, "detail key missing from a-detail-1 row"
    assert "detail" in row2, "detail key missing from a-detail-2 row"
    assert "detail" in row3, "detail key missing from a-detail-3 row"

    # CANDIDATE_FOR_CLEANUP must carry the non-null detail string.
    assert (
        row1["detail"] == "Near-duplicate of Foo Bot (42u vs 3u)"
    ), f"expected detail text on a-detail-1, got: {row1['detail']!r}"

    # KEEP+VERIFIED has no detail — value must be null, not a fabricated string.
    assert row2["detail"] is None, f"expected null detail on a-detail-2 (KEEP), got: {row2['detail']!r}"

    # REVIEW must carry a non-null detail string (AC10).
    assert row3["detail"] is not None, "REVIEW row must have a non-null detail field"
    assert (
        row3["detail"] == "Has 1 critical quality issue."
    ), f"expected detail text on a-detail-3 (REVIEW), got: {row3['detail']!r}"
