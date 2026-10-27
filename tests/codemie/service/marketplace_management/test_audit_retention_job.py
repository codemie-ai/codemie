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

"""The scheduled purge that runs the 6.5 statement (EXECUTION-PLAN 5.5, F-39).

A retention *statement* nothing calls prunes nothing. `mm_audit_run` and its
children grew to 94 runs and 24 454 verdict rows in three days on the dev
database with no purge anywhere in the codebase, and the existing precedent for
fixing that -- `_setup_activity_events_retention_scheduler` in `rest_api/main.py`
-- is a daily APScheduler job guarded by `async_leader_lock` so N pods do not all
purge at once.

These tests exercise the wiring, not the SQL: the statement itself is pinned
against a real PostgreSQL in
`tests/codemie/external/alembic/test_marketplace_migrations.py`.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI


@pytest.fixture
def app() -> FastAPI:
    return FastAPI()


def test_retention_is_off_until_somebody_turns_it_on(app: FastAPI) -> None:
    """A job that silently starts deleting audit history on upgrade is not a fix.

    `MARKETPLACE_AUDIT_RETENTION_DAYS` defaults to 0, and 0 means "keep
    everything" -- the same shape `ACTIVITY_EVENTS_RETENTION_DAYS <= 0` uses.
    """
    from codemie.configs import config
    from codemie.rest_api.main import _setup_marketplace_audit_retention_scheduler

    assert (
        config.MARKETPLACE_AUDIT_RETENTION_DAYS == 0
    ), "retention must ship disabled; enabling it is a deliberate operator decision"

    _setup_marketplace_audit_retention_scheduler(app)

    assert not hasattr(app.state, "marketplace_audit_retention_scheduler")


def _run_one_purge(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    *,
    is_leader: bool,
    raises: bool = False,
) -> list[tuple[str, Any]]:
    """Arm the scheduler and fire its job once, inside a single event loop.

    `AsyncIOScheduler.start()` binds to the running loop, so installing and
    firing have to share one -- otherwise the second test in the module starts a
    scheduler against a loop `asyncio.run` has already closed.
    """

    async def _scenario() -> list[tuple[str, Any]]:
        calls = _install(app, monkeypatch, retention_days=30, is_leader=is_leader, raises=raises)
        scheduler = app.state.marketplace_audit_retention_scheduler
        job = scheduler.get_job("marketplace_audit_retention_purge")
        assert job is not None, "the purge was never scheduled"
        try:
            await job.func()
        finally:
            scheduler.shutdown(wait=False)
        return calls

    return asyncio.run(_scenario())


def test_a_non_leader_pod_purges_nothing(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every pod runs the scheduler; exactly one of them may run the DELETE."""
    calls = _run_one_purge(app, monkeypatch, is_leader=False)

    assert calls == [], f"a follower pod issued a purge: {calls}"


def test_the_leader_purges_every_entity_kind(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both queues age out, and the cutoff comes from the configured window."""
    from datetime import datetime, timedelta, timezone

    before = datetime.now(timezone.utc)
    calls = _run_one_purge(app, monkeypatch, is_leader=True)
    after = datetime.now(timezone.utc)

    assert [kind for kind, _cutoff in calls] == ["ASSISTANT", "WORKFLOW"]
    for _kind, cutoff in calls:
        assert (
            before - timedelta(days=30, seconds=1) <= cutoff <= after - timedelta(days=30) + timedelta(seconds=1)
        ), f"cutoff {cutoff} does not match a 30-day retention window"


def test_a_failing_purge_does_not_take_the_scheduler_down(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """One bad night must not stop every later purge, exactly as activity events do it.

    The job body swallows and logs; if it re-raised, APScheduler would log the
    traceback under its own logger name and the operator watching our logs would
    see nothing at all.
    """
    calls = _run_one_purge(app, monkeypatch, is_leader=True, raises=True)

    assert calls, "the purge never reached the repository"


def _install(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    *,
    retention_days: int,
    is_leader: bool,
    raises: bool = False,
) -> list[tuple[str, Any]]:
    """Arm the scheduler with a stubbed leader lock, engine and repository."""
    from codemie.clients import postgres as pg
    from codemie.configs import config
    from codemie.rest_api import main
    from codemie.service.marketplace_management import audit_retention
    from codemie.utils import leader_lock as leader_lock_module

    monkeypatch.setattr(config, "MARKETPLACE_AUDIT_RETENTION_DAYS", retention_days)
    monkeypatch.setattr(config, "MARKETPLACE_MANAGEMENT_ENABLED", True)
    monkeypatch.setattr(pg.PostgresClient, "get_engine", classmethod(lambda cls: None))

    @asynccontextmanager
    async def _lock(_lock_id):
        yield is_leader

    monkeypatch.setattr(leader_lock_module, "async_leader_lock", _lock)

    calls: list[tuple[str, Any]] = []

    class _Repo:
        def delete_older_than(self, *, entity_kind, cutoff, session):  # noqa: ANN001
            calls.append((entity_kind, cutoff))
            if raises:
                raise RuntimeError("database went away")
            return 0

    monkeypatch.setattr(audit_retention, "marketplace_audit_retention_repository", _Repo())

    class _Session:
        def __init__(self, _engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def commit(self):
            pass

    monkeypatch.setattr("sqlmodel.Session", _Session)

    main._setup_marketplace_audit_retention_scheduler(app)
    return calls
