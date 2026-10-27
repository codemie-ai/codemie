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

"""Pytest configuration for local_auth_router tests.

This module disables the rate limiter before importing the router module
to prevent rate limiting from interfering with tests.
"""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

import codemie.rest_api.rate_limit

# Disable rate limiter for all tests
codemie.rest_api.rate_limit.limiter.enabled = False


@pytest.fixture(autouse=True)
def _inject_request_uuid(monkeypatch):
    """Tests use bare FastAPI() apps without uuid-injection middleware.
    Provide uuid default on request.state so authenticate() doesn't crash."""
    from starlette.datastructures import State

    original_getattr = State.__getattr__

    def _getattr_with_uuid(self, key):
        if key == "uuid":
            return "test-request-uuid"
        return original_getattr(self, key)

    monkeypatch.setattr(State, "__getattr__", _getattr_with_uuid)


# ---------------------------------------------------------------------------
# A real, migrated PostgreSQL for the publish-gate tests (EXECUTION-PLAN 7.1/7.6)
# ---------------------------------------------------------------------------
#
# Publishing under the approval gate is a *database* transition: a
# compare-and-set on `assistants` plus an `mm_review_decision` insert, in one
# transaction (`queue_service.decide`). A mocked session cannot express either,
# and `SQLModel.metadata.create_all` builds the schema from the ORM's beliefs
# rather than from the migration production runs — exactly the divergence that
# left the audit feature persisting nothing at all (EXECUTION-PLAN 5.3).
#
# Session-scoped so one throwaway database serves every module in this package
# that needs it; each test cleans its own rows. If PostgreSQL is unreachable
# these fixtures **fail**, they do not skip — a skipped persistence test is
# indistinguishable from a passing one on a dashboard (F-40).

_MM_ADMIN_URL = os.getenv("MM_TEST_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")
_MM_SCHEMA = "codemie"
_MM_REPO_ROOT = Path(__file__).resolve().parents[4]
_MM_ALEMBIC_DIR = _MM_REPO_ROOT / "src" / "external" / "alembic"

_MM_UNREACHABLE = """\
Cannot reach PostgreSQL at {url}.

These tests assert a publish-time database transition and are meaningless
without one, so they skip when the database is unavailable. Start the dev stack:

    cd codemie && docker compose up -d postgres

or point this file at another instance with MM_TEST_PG_URL.

Underlying error: {err}
"""


def _mm_alembic_ok(url: str, *args: str) -> str:
    """Invoke alembic exactly as an operator would, against `url`."""
    env = dict(os.environ)
    env["PG_URL"] = url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(_MM_ALEMBIC_DIR),
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


@pytest.fixture(scope="session")
def mm_publish_engine():
    """A throwaway database migrated to `head` by the real migration chain."""
    from sqlalchemy import text as _text
    from sqlalchemy.engine import make_url as _make_url
    from sqlmodel import create_engine as _create_engine

    admin_url = _make_url(_MM_ADMIN_URL)
    db_name = f"mm_publish_test_{uuid.uuid4().hex[:10]}"
    admin = None
    try:
        admin = _create_engine(admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
        with admin.connect() as conn:
            conn.execute(_text(f'create database "{db_name}"'))
    except Exception as exc:  # noqa: BLE001 - reported verbatim below
        pytest.skip(_MM_UNREACHABLE.format(url=admin_url.render_as_string(hide_password=True), err=exc))

    target_url = admin_url.set(database=db_name)
    try:
        setup = _create_engine(target_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
        with setup.connect() as conn:
            conn.execute(_text(f"create schema if not exists {_MM_SCHEMA}"))
            conn.execute(_text("create extension if not exists vector"))
        setup.dispose()

        _mm_alembic_ok(target_url.render_as_string(hide_password=False), "upgrade", "head")

        app_engine = _create_engine(
            target_url,
            pool_pre_ping=True,
            connect_args={"options": f"-c search_path={_MM_SCHEMA},public"},
        )
        yield app_engine
        app_engine.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(_text(f'drop database if exists "{db_name}" with (force)'))
        admin.dispose()


@pytest.fixture
def mm_publish_db(mm_publish_engine, monkeypatch):
    """Point the application's engine at the throwaway database and clean up.

    Both `get_session()` (used by `queue_service`) and
    `BaseModelWithSQLSupport.get_engine()` (used by `Assistant.find_by_id` /
    `.update()`) resolve through `PostgresClient.get_engine`, so one override
    puts the whole production publish path on this database unmodified.
    """
    from sqlalchemy import text as _text
    from codemie.clients import postgres as pg

    monkeypatch.setattr(pg.PostgresClient, "get_engine", classmethod(lambda cls: mm_publish_engine))

    yield mm_publish_engine

    with mm_publish_engine.connect() as conn:
        for table in ("mm_review_decision", "assistants", "users"):
            conn.execute(_text(f"delete from {_MM_SCHEMA}.{table}"))
        conn.commit()
