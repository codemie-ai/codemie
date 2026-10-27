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

"""Tests that EntityReviewDetail surfaces verdict fields through the API.

EPMCDME-15056: GET /entities/assistants/{eid} now includes verdict_kind,
cleanup_reason, bucket, and decision_detail when a serveable audit verdict
row exists, and all four are null when no row exists.
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

`/entities/{eid}` reads SQL; asserting its contract without a database would assert
nothing, so these tests skip when the database is unavailable. Start the dev stack:

    cd codemie && docker compose up -d postgres

or point this file at another instance with MM_TEST_PG_URL.

Underlying error: {err}
"""


# ---------------------------------------------------------------------------
# Infrastructure helpers (mirrors test_marketplace_cleanup_detail_field.py)
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
    db_name = f"mm_verdict_test_{uuid.uuid4().hex[:10]}"
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
            "        '[]'::jsonb, :ms, :u, 0, 0, 1)"
        ),
        {"id": aid, "n": name, "d": description, "u": users, "g": is_global, "ms": marketplace_status},
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
# Data fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def verdict_data(sql_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    """Two assistants: one with a serveable verdict, one without."""
    run_id = "run-verdict-detail-test"
    aid_with = "a-verdict-with"
    aid_without = "a-verdict-without"

    with sql_engine.connect() as conn:
        _insert_run(conn, run_id)
        _insert_assistant(conn, aid_with, name="assistant with verdict", users=42)
        _insert_assistant(conn, aid_without, name="assistant without verdict", users=10)
        _insert_verdict(
            conn,
            run_id,
            aid_with,
            verdict_kind="CANDIDATE_FOR_CLEANUP",
            cleanup_reason="duplicate",
            bucket="DELETE",
            detail="Near-duplicate of Foo Bot (42u vs 3u)",
        )
        conn.commit()

    _point_app_at(sql_engine, monkeypatch)
    yield {"aid_with": aid_with, "aid_without": aid_without}

    with sql_engine.connect() as conn:
        conn.execute(text("delete from codemie.mm_audit_run"))
        conn.execute(text("delete from codemie.assistants"))
        conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_entity_detail_includes_verdict_fields_when_verdict_exists(
    admin_client: TestClient, verdict_data: dict
) -> None:
    """EntityReviewDetail must carry verdict fields for an assistant with a serveable verdict.

    EPMCDME-15056: GET /entities/assistants/{eid} now queries mm_audit_verdict
    for the last serveable run and maps the row onto four new fields.
    """
    aid = verdict_data["aid_with"]
    url = f"{BASE}/entities/assistants/{aid}"
    response = admin_client.get(url)
    assert response.status_code == 200, response.text
    body = response.json()

    assert (
        body.get("verdict_kind") == "CANDIDATE_FOR_CLEANUP"
    ), f"expected CANDIDATE_FOR_CLEANUP, got: {body.get('verdict_kind')!r}"
    assert body.get("cleanup_reason") == "duplicate", f"expected 'duplicate', got: {body.get('cleanup_reason')!r}"
    assert body.get("bucket") == "DELETE", f"expected 'DELETE', got: {body.get('bucket')!r}"
    assert (
        body.get("decision_detail") == "Near-duplicate of Foo Bot (42u vs 3u)"
    ), f"expected detail text, got: {body.get('decision_detail')!r}"


def test_entity_detail_verdict_fields_are_null_when_no_verdict(admin_client: TestClient, verdict_data: dict) -> None:
    """EntityReviewDetail must return null verdict fields when no verdict row exists.

    EPMCDME-15056: An assistant that has not been audited must have all four
    verdict fields present in the response but set to null.
    """
    aid = verdict_data["aid_without"]
    url = f"{BASE}/entities/assistants/{aid}"
    response = admin_client.get(url)
    assert response.status_code == 200, response.text
    body = response.json()

    for field in ("verdict_kind", "cleanup_reason", "bucket", "decision_detail"):
        assert field in body, f"field '{field}' must be present in response"
        assert body[field] is None, f"expected null for '{field}', got: {body[field]!r}"


# ---------------------------------------------------------------------------
# CR-009: non-null bucket_reason and usage_tier surfaced through the endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def verdict_data_extended(sql_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    """Assistant with a verdict row carrying non-null bucket_reason and usage_tier."""
    run_id = "run-verdict-extended-test"
    aid = "a-verdict-extended"

    with sql_engine.connect() as conn:
        _insert_run(conn, run_id)
        _insert_assistant(conn, aid, name="assistant with extended verdict", users=5)
        _insert_verdict(
            conn,
            run_id,
            aid,
            verdict_kind="CANDIDATE_FOR_CLEANUP",
            cleanup_reason="dead",
            bucket="COMMUNITY",
            bucket_reason="similarity_too_high",
            usage_tier="COLD",
            detail="Low-adoption assistant",
        )
        conn.commit()

    _point_app_at(sql_engine, monkeypatch)
    yield {"aid": aid}

    with sql_engine.connect() as conn:
        conn.execute(text("delete from codemie.mm_audit_run"))
        conn.execute(text("delete from codemie.assistants"))
        conn.commit()


def test_entity_detail_includes_bucket_reason_and_usage_tier(
    admin_client: TestClient, verdict_data_extended: dict
) -> None:
    """GET /entities/assistants/{eid} must surface non-null bucket_reason and usage_tier.

    CR-009 (EPMCDME-15352): The extended verdict fields must be present and non-null
    in the GET response when the verdict row carries them.
    """
    aid = verdict_data_extended["aid"]
    url = f"{BASE}/entities/assistants/{aid}"
    response = admin_client.get(url)
    assert response.status_code == 200, response.text
    body = response.json()

    assert (
        body.get("bucket_reason") == "similarity_too_high"
    ), f"expected 'similarity_too_high', got: {body.get('bucket_reason')!r}"
    assert body.get("usage_tier") == "COLD", f"expected 'COLD', got: {body.get('usage_tier')!r}"


# ---------------------------------------------------------------------------
# CR-010: polish_check_results present and non-null in endpoint response
# ---------------------------------------------------------------------------


@pytest.fixture
def polish_data(sql_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    """Assistant whose config will produce polish_check_results from the endpoint."""
    run_id = "run-polish-test"
    aid = "a-polish-test"
    # Use a description long enough to satisfy the default 250-char minimum so
    # _review_polish_checks can succeed. The assistant needs at least one
    # category so that check passes too.
    description = "A" * 300

    with sql_engine.connect() as conn:
        _insert_run(conn, run_id)
        # Insert with one category so the categories check can pass/fail predictably.
        conn.execute(
            text(
                "insert into codemie.assistants "
                "(id, name, description, system_prompt, project, shared, is_react, created_date, "
                " creator, is_global, categories, marketplace_status, unique_users_count, "
                " unique_likes_count, unique_dislikes_count, version_count) "
                "values (:id, :n, :d, 'p', 'demo', true, true, now(), 'someone', true, "
                "        '[\"AI\"]'::jsonb, 'APPROVED', 0, 0, 0, 1)"
            ),
            {"id": aid, "n": "polish assistant", "d": description},
        )
        conn.commit()

    _point_app_at(sql_engine, monkeypatch)
    yield {"aid": aid}

    with sql_engine.connect() as conn:
        conn.execute(text("delete from codemie.mm_audit_run"))
        conn.execute(text("delete from codemie.assistants"))
        conn.commit()


def test_entity_detail_includes_polish_check_results(admin_client: TestClient, polish_data: dict) -> None:
    """GET /entities/assistants/{eid} must include polish_check_results as a list of 4 items.

    CR-010 (EPMCDME-15352): _review_polish_checks always returns exactly 4 checks
    (description, categories, icon, starters). The endpoint must surface them as a
    non-null list with len == 4.
    """
    aid = polish_data["aid"]
    url = f"{BASE}/entities/assistants/{aid}"
    response = admin_client.get(url)
    assert response.status_code == 200, response.text
    body = response.json()

    assert isinstance(
        body.get("polish_check_results"), list
    ), f"expected polish_check_results to be a list, got: {body.get('polish_check_results')!r}"
    assert (
        len(body["polish_check_results"]) == 4
    ), f"expected 4 polish check results, got: {len(body['polish_check_results'])}"
