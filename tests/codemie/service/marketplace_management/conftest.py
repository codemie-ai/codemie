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

"""Shared fixtures for marketplace_management verification tests."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import text as _text
from sqlalchemy.engine import Engine as _Engine, make_url as _make_url
from sqlmodel import create_engine as _create_engine

from codemie.configs.marketplace_management import MARKETPLACE_MANAGEMENT_DEFAULTS
from codemie.service.marketplace_management.validator.config import Config
from codemie.service.marketplace_management.validator.models import AssistantRecord


@pytest.fixture
def cfg() -> Config:
    return Config.from_marketplace_management(MARKETPLACE_MANAGEMENT_DEFAULTS)


def make_assistant(**overrides) -> AssistantRecord:
    """Build a valid baseline assistant record; override any field."""
    defaults = {
        "id": "a-1",
        "name": "A Working Assistant",
        "description": (
            "This is a detailed and useful description of what this assistant does. "
            "It is well over one hundred characters long."
        ),
        "system_prompt": (
            "You are a helpful assistant that answers questions clearly and thoroughly. "
            "You always provide sources and citations for your answers. "
            "You must respect user preferences and answer in the same language as the user."
        ),
        "categories": ["general"],
        "tools": ["tool_a", "tool_b"],
        "mcp_servers": [],
        "skills": [],
        "datasources": ["ds_a"],
        "icon_url": "https://example.com/icon.png",
        "is_global": True,
        "shared": True,
        "creator": "creator-1",
        "unique_users_count": 100,
        "unique_likes_count": 5,
        "unique_dislikes_count": 0,
        "clone_count": 1,
        "version_count": 1,
        "updated_date": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "created_date": "2025-01-01T00:00:00Z",
        "raw": {},
    }
    defaults.update(overrides)
    return AssistantRecord(**defaults)


@pytest.fixture
def assistant():
    return make_assistant()


@pytest.fixture
def make():
    return make_assistant


# ---------------------------------------------------------------------------
# A real, migrated PostgreSQL for the tests that assert database invariants
# ---------------------------------------------------------------------------
#
# `uq_mm_audit_run_active` and the heartbeat reaper are *database* behaviour.
# A stubbed session cannot express either: the partial unique index has to be
# the thing that raises, or the test is asserting an in-process check that the
# defect (F-17) is precisely about not having.
#
# Session-scoped so one throwaway database serves every module that needs it
# (`test_audit_persistence_roundtrip.py` and `test_audit_service_async.py`);
# each module still cleans its own rows. If PostgreSQL is unreachable these
# fixtures **fail**, they do not skip — a skipped persistence test is
# indistinguishable from a passing one on a dashboard (F-40).

# The dev stack from `docker-compose.yml` (postgres/password on :5432).
# `pytest.ini` deliberately points PG_URL at a dead port, so this cannot read
# the application config — it is its own, overridable, connection.
MM_ADMIN_URL = os.getenv("MM_TEST_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")
MM_SCHEMA = "codemie"

_REPO_ROOT = Path(__file__).resolve().parents[4]
_ALEMBIC_DIR = _REPO_ROOT / "src" / "external" / "alembic"

_UNREACHABLE = """\
Cannot reach PostgreSQL at {url}.

These tests assert a database round-trip and are meaningless without one, so
they skip when the database is unavailable. Start the dev stack:

    cd codemie && docker compose up -d postgres

or point this file at another instance with MM_TEST_PG_URL.

Underlying error: {err}
"""


def mm_alembic_ok(url: str, *args: str) -> str:
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


@pytest.fixture(scope="session")
def mm_pg_engine() -> Iterator[_Engine]:
    """A throwaway database migrated to `head` by the real migration chain.

    Built by **alembic**, never `SQLModel.metadata.create_all`: `create_all`
    builds the schema from the ORM, so it reproduces whatever the ORM believes
    and can never see an ORM/schema gap — the mocked-session failure mode with
    extra steps.
    """
    admin_url = _make_url(MM_ADMIN_URL)
    db_name = f"mm_persist_test_{_uuid.uuid4().hex[:10]}"
    admin = None
    try:
        admin = _create_engine(admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
        with admin.connect() as conn:
            conn.execute(_text(f'create database "{db_name}"'))
    except Exception as exc:  # noqa: BLE001 - reported verbatim below
        pytest.skip(_UNREACHABLE.format(url=admin_url.render_as_string(hide_password=True), err=exc))

    target_url = admin_url.set(database=db_name)
    try:
        setup = _create_engine(target_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
        with setup.connect() as conn:
            conn.execute(_text(f"create schema if not exists {MM_SCHEMA}"))
            conn.execute(_text("create extension if not exists vector"))
        setup.dispose()

        mm_alembic_ok(target_url.render_as_string(hide_password=False), "upgrade", "head")

        app_engine = _create_engine(
            target_url,
            pool_pre_ping=True,
            connect_args={"options": f"-c search_path={MM_SCHEMA},public"},
        )
        yield app_engine
        app_engine.dispose()
    finally:
        with admin.connect() as conn:
            conn.execute(_text(f'drop database if exists "{db_name}" with (force)'))
        admin.dispose()


class _RecordingHandler(logging.Handler):
    """Collects `LogRecord`s, including their `exc_info`."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def codemie_logs():
    """Capture what the application logger actually emits.

    `pytest`'s own `caplog` sees nothing from this project: `codemie.configs.logger`
    sets `propagate = False` and owns its stderr handler, so records never reach
    the root logger `caplog` attaches to. Asserting `caplog.records == []` about
    application logging therefore always passes -- it is an assertion about an
    empty list, not about the code under test. This fixture attaches to the real
    logger instead, so "no traceback was logged" means it.

    Note that this project's logging folds `exc_info` into the *message* and
    clears `record.exc_info`, so a traceback is found by looking for
    "Traceback (most recent call last)" in `getMessage()`, not by testing the
    `exc_info` attribute -- which is always falsy here.
    """
    from codemie.configs import logger as codemie_logger

    handler = _RecordingHandler()
    previous_level = codemie_logger.level
    codemie_logger.addHandler(handler)
    codemie_logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        codemie_logger.removeHandler(handler)
        codemie_logger.setLevel(previous_level)
