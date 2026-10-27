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

"""The scheduled heartbeat reaper (EXECUTION-PLAN 6.7, design-persistence.md 7 F4).

The audit worker is a `daemon=True` bare thread, so it dies with its process.
What it leaves is a `mm_audit_run` row stuck at `RUNNING` that no reader can
tell from a live one -- and that row holds `uq_mm_audit_run_active`, the very
index that makes "at most one audit per entity kind" true across pods. Without
a reaper the invariant that protects the catalogue becomes the thing that stops
anyone auditing it again, and the only fix is a human with `psql`.

These tests exercise the **wiring**: every pod schedules the sweep, exactly one
pod issues the statement, a bad night does not take the scheduler down. The
statement itself is pinned against a real, migrated PostgreSQL in
`test_audit_service_async.py::test_stale_heartbeat_is_reaped`.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI


@pytest.fixture
def app() -> FastAPI:
    return FastAPI()


def test_the_reaper_is_armed_by_default(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike retention, this one ships **on**.

    Retention deletes history, so it defaults to off. The reaper only relabels a
    run whose owner is already gone -- and the cost of it being off is that one
    crashed pod locks the whole deployment out of auditing.
    """
    from codemie.configs import config
    from codemie.rest_api.main import _setup_marketplace_audit_reaper_scheduler

    assert config.MARKETPLACE_AUDIT_HEARTBEAT_TIMEOUT_MINUTES > 0
    assert callable(_setup_marketplace_audit_reaper_scheduler)

    async def _scenario() -> None:
        _install(app, monkeypatch, is_leader=False)
        try:
            scheduler = app.state.marketplace_audit_reaper_scheduler
            assert scheduler.get_job("marketplace_audit_reaper") is not None
        finally:
            scheduler.shutdown(wait=False)

    asyncio.run(_scenario())


def test_a_follower_pod_reaps_nothing(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every pod runs the sweep; exactly one of them may issue the UPDATE."""
    calls = _run_one_sweep(app, monkeypatch, is_leader=False)

    assert calls == [], f"a follower pod issued a reap: {calls}"


def test_the_leader_reaps_with_the_configured_timeout(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """The staleness window comes from configuration, not from a literal.

    The count is not asserted: the job is scheduled with `next_run_time=now` so
    it sweeps once at startup, and whether that startup sweep lands inside this
    test's event loop is a timing detail. That it *always* uses the configured
    window is the property.
    """
    calls = _run_one_sweep(app, monkeypatch, is_leader=True, timeout_minutes=7)

    assert calls, "the leader never reached the reaper"
    assert set(calls) == {timedelta(minutes=7)}


def test_a_failing_sweep_does_not_take_the_scheduler_down(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    """One bad sweep must not stop every later one.

    If the job body re-raised, APScheduler would log the traceback under its own
    logger name and the operator watching our logs would see nothing at all --
    the same reasoning the retention purge is built on.
    """
    calls = _run_one_sweep(app, monkeypatch, is_leader=True, raises=True)

    assert calls, "the sweep never reached the reaper"


def _run_one_sweep(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    *,
    is_leader: bool,
    timeout_minutes: int = 10,
    raises: bool = False,
) -> list[timedelta]:
    """Arm the scheduler and fire its job once, inside a single event loop.

    `AsyncIOScheduler.start()` binds to the running loop, so installing and
    firing have to share one -- otherwise the second test in the module starts a
    scheduler against a loop `asyncio.run` has already closed.
    """

    async def _scenario() -> list[timedelta]:
        calls = _install(app, monkeypatch, is_leader=is_leader, timeout_minutes=timeout_minutes, raises=raises)
        scheduler = app.state.marketplace_audit_reaper_scheduler
        job = scheduler.get_job("marketplace_audit_reaper")
        assert job is not None, "the reaper was never scheduled"
        try:
            await job.func()
        finally:
            scheduler.shutdown(wait=False)
        return calls

    return asyncio.run(_scenario())


def _install(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    *,
    is_leader: bool,
    timeout_minutes: int = 10,
    raises: bool = False,
) -> list[timedelta]:
    """Arm the scheduler with a stubbed leader lock and reaper statement."""
    from codemie.configs import config
    from codemie.rest_api import main
    from codemie.service.marketplace_management import audit_service
    from codemie.utils import leader_lock as leader_lock_module

    monkeypatch.setattr(config, "MARKETPLACE_AUDIT_HEARTBEAT_TIMEOUT_MINUTES", timeout_minutes)
    monkeypatch.setattr(config, "MARKETPLACE_MANAGEMENT_ENABLED", True)

    @asynccontextmanager
    async def _lock(_lock_id):
        yield is_leader

    monkeypatch.setattr(leader_lock_module, "async_leader_lock", _lock)

    calls: list[Any] = []

    def _reap(*, stale_after: timedelta, **_kwargs) -> int:
        calls.append(stale_after)
        if raises:
            raise RuntimeError("database went away")
        return 0

    monkeypatch.setattr(audit_service, "reap_stale_audit_runs", _reap)

    main._setup_marketplace_audit_reaper_scheduler(app)
    return calls
