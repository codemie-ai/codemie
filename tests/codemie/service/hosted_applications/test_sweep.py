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

"""The reconciliation sweep deletes old orphans only, and never delays the maintenance loop."""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from codemie.repository.hosted_applications.object_storage import HostedObjectStorage, ObjectUnavailable
from codemie.service.hosted_applications import runtime as runtime_module
from codemie.service.hosted_applications.runtime import HostedApplicationsRuntime
from codemie.service.hosted_applications.sweep import HostedStorageSweep, database_lookup
from tests.codemie.service.hosted_applications.storage_fakes import FakeClock, FakeGcs, FakeS3, object_adapter

ORG = "a-" + "a" * 32


def prefix(number: int) -> str:
    return f"{ORG}/{'1' * 32}/{'2' * 32}/{number:032x}"


def owner_prefix(segment: str, number: int) -> str:
    return f"{segment}/{'1' * 32}/{'2' * 32}/{number:032x}"


class Recorder:
    def __init__(self):
        self.errors: list[str] = []

    def error(self, message, *args, **kwargs):
        self.errors.append(message)

    def warning(self, message, *args, **kwargs):
        pass


def make_lookup(states: dict[str, str]):
    async def lookup(prefixes):
        return {name: states[name] for name in prefixes if name in states}

    return lookup


def seed(ops, key: str, modified):
    if isinstance(ops, FakeGcs):
        ops.objects[key] = (b"x", 1, modified)
    else:
        ops.objects[key] = (b"x", modified)


def build(ops, provider, states, **options):
    storage = object_adapter(ops, provider)
    log = Recorder()
    sweep = HostedStorageSweep(
        storage,
        make_lookup(states),
        clock=ops.clock,
        budget_seconds=options.pop("budget_seconds", 20),
        logger=log,
        **options,
    )
    return sweep, log


@pytest.mark.asyncio
@pytest.mark.parametrize("ops_class,provider", [(FakeS3, "aws"), (FakeGcs, "gcp")])
async def test_sweep_deletes_old_orphans_only(ops_class, provider):
    clock = FakeClock()
    ops = ops_class(clock=clock, page_size=3)
    old = clock() - timedelta(days=2)
    new = clock() - timedelta(hours=1)
    deleted, live, missing, fresh_missing = prefix(1), prefix(2), prefix(3), prefix(4)
    for name in (deleted, live, missing):
        seed(ops, f"{name}/a.txt", old)
    seed(ops, f"{deleted}/b.txt", new)
    seed(ops, f"{live}/b.txt", old)
    seed(ops, f"{fresh_missing}/a.txt", new)
    for reserved in (".hosted/other", f".fences/{missing}/x", f".staging/{missing}/y", "stray/file.txt"):
        seed(ops, reserved, old)
    sweep, log = build(ops, provider, {deleted: "deleted", live: "stored"})
    assert await sweep.run_once() == 2
    remaining = set(ops.objects)
    assert f"{deleted}/a.txt" not in remaining
    assert f"{missing}/a.txt" not in remaining
    assert {
        f"{deleted}/b.txt",
        f"{live}/a.txt",
        f"{live}/b.txt",
        f"{fresh_missing}/a.txt",
        ".hosted/other",
        f".fences/{missing}/x",
        f".staging/{missing}/y",
        "stray/file.txt",
    } <= remaining
    assert any("hosted_storage_orphans_deleted" in message and "count=1" in message for message in log.errors)
    assert sum(int(message.rsplit("count=", 1)[1]) for message in log.errors) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("ops_class,provider", [(FakeS3, "aws"), (FakeGcs, "gcp")])
async def test_sweep_deletes_attempt_orphans_and_keeps_look_alike_keys(ops_class, provider):
    clock = FakeClock()
    ops = ops_class(clock=clock, page_size=3)
    old = clock() - timedelta(days=2)
    orphan, live = prefix(1), prefix(2)
    for name in (orphan, live):
        seed(ops, f"{name}/a.txt", old)
    # Keys a shared bucket can hold that are not attempts: owner or organization roots and a short app segment.
    look_alikes = {
        owner_prefix("u-f_1a2b_john_doe_example_com", 5) + "/a.txt",
        owner_prefix("o-" + "b" * 32, 6) + "/a.txt",
        owner_prefix("a-" + "c" * 31, 7) + "/a.txt",
    }
    for key in look_alikes:
        seed(ops, key, old)
    sweep, _ = build(ops, provider, {live: "stored"})
    assert await sweep.run_once() == 1
    remaining = set(ops.objects)
    assert f"{orphan}/a.txt" not in remaining
    assert {f"{live}/a.txt", *look_alikes} <= remaining


@pytest.mark.asyncio
async def test_sweep_resumes_from_the_saved_token():
    clock = FakeClock()
    ops = FakeS3(clock=clock, page_size=2)
    old = clock() - timedelta(days=2)
    names = [prefix(index) for index in range(1, 4)]
    for name in names:
        seed(ops, f"{name}/a.txt", old)
    monotonic = iter(range(0, 1000, 30))
    sweep, _ = build(ops, "aws", {}, budget_seconds=20, monotonic=lambda: next(monotonic))
    await sweep.run_once()
    assert sweep.token is not None
    assert 0 < len([key for key in ops.objects if key.startswith(ORG)]) < 3
    first_token = sweep.token
    await sweep.run_once()
    listed = [call for call in ops.calls if call[0] == "list_keys"]
    assert any(call[2] == first_token for call in listed)


@pytest.mark.asyncio
async def test_sweep_does_not_run_on_filesystem(monkeypatch):
    runtime = object.__new__(HostedApplicationsRuntime)
    runtime.settings = SimpleNamespace(enabled=True)
    runtime.tasks = []
    runtime.ready = False

    async def open_storage():
        runtime.storage = SimpleNamespace()

    async def idle():
        await asyncio.sleep(3600)

    runtime.open_storage = open_storage
    runtime.work = idle
    runtime.maintain = idle
    runtime.sweep_storage = idle
    await runtime.start()
    names = {task.get_name() for task in runtime.tasks}
    for task in runtime.tasks:
        task.cancel()
    assert "hosted-maintenance" in names
    assert "hosted-storage-sweep" not in names


@pytest.mark.asyncio
async def test_sweep_runs_on_object_storage_with_the_configured_budget():
    runtime = object.__new__(HostedApplicationsRuntime)
    runtime.settings = SimpleNamespace(enabled=True)
    runtime.tasks = []
    runtime.ready = False
    runtime.repository = object()
    runtime.config = SimpleNamespace(
        HOSTED_APPS_STORAGE_SWEEP_SECONDS=3600, HOSTED_APPS_STORAGE_SWEEP_BUDGET_SECONDS=17
    )

    async def open_storage():
        runtime.storage = object_adapter(FakeS3(), "aws")

    async def idle():
        await asyncio.sleep(3600)

    runtime.open_storage = open_storage
    runtime.work = idle
    runtime.maintain = idle
    runtime.sweep_storage = idle
    await runtime.start()
    names = {task.get_name() for task in runtime.tasks}
    for task in runtime.tasks:
        task.cancel()
    await asyncio.gather(*runtime.tasks, return_exceptions=True)
    assert {"hosted-maintenance", "hosted-storage-sweep"} <= names
    assert runtime.sweep.storage is runtime.storage and runtime.sweep.budget_seconds == 17


@pytest.mark.asyncio
async def test_sweep_interval_never_drops_below_one_second(monkeypatch):
    slept = []

    async def recording_sleep(seconds):
        slept.append(seconds)
        raise asyncio.CancelledError

    runtime = object.__new__(HostedApplicationsRuntime)
    runtime.config = SimpleNamespace(HOSTED_APPS_STORAGE_SWEEP_SECONDS=0)

    async def nothing():
        return 0

    runtime.sweep = SimpleNamespace(run_once=nothing)
    monkeypatch.setattr(runtime_module.asyncio, "sleep", recording_sleep)
    monkeypatch.setattr(runtime_module, "async_leader_lock", leader_lock(True, []))
    with pytest.raises(asyncio.CancelledError):
        await runtime.sweep_storage()
    assert slept == [1]


def leader_lock(acquired, taken):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lock(lock_id):
        taken.append(lock_id)
        yield acquired

    return lock


@pytest.mark.asyncio
@pytest.mark.parametrize("leader", [True, False])
async def test_only_the_leader_replica_walks_the_bucket(monkeypatch, leader):
    """Finding 5: each sweep iteration runs under the platform's leader lock, so one replica lists the bucket."""
    runs, taken = [], []

    async def run_once():
        runs.append(1)
        return 0

    runtime = object.__new__(HostedApplicationsRuntime)
    runtime.sweep = SimpleNamespace(run_once=run_once)
    monkeypatch.setattr(runtime_module, "async_leader_lock", leader_lock(leader, taken))
    await runtime.sweep_storage_once()
    assert taken == [runtime_module.HOSTED_STORAGE_SWEEP_LOCK_ID]
    assert runs == ([1] if leader else [])


def test_the_sweep_lock_id_is_not_used_by_another_scheduler():
    import re
    from pathlib import Path

    source = Path(runtime_module.__file__).resolve().parents[2]
    lock_id = str(runtime_module.HOSTED_STORAGE_SWEEP_LOCK_ID)
    users = [
        path
        for path in source.rglob("*.py")
        if re.search(rf"\b{lock_id}\b", path.read_text(encoding="utf-8").replace("_", ""))
    ]
    assert [path.name for path in users] == ["runtime.py"]


@pytest.mark.asyncio
async def test_rejected_saved_token_restarts_the_sweep_from_the_beginning():
    clock = FakeClock()
    ops = FakeS3(clock=clock)
    seed(ops, f"{prefix(1)}/a.txt", clock() - timedelta(days=2))

    def reject(prefix, token):
        if token == "stale":
            raise ObjectUnavailable("InvalidArgument", 400)

    ops.hooks["before_list_keys"] = reject
    sweep, log = build(ops, "aws", {})
    sweep.token = "stale"
    with pytest.raises(ObjectUnavailable):
        await sweep.run_once()
    assert sweep.token is None
    assert await sweep.run_once() == 1


@pytest.mark.asyncio
async def test_transient_listing_failure_keeps_the_saved_token():
    ops = FakeS3()

    def unavailable(prefix, token):
        raise ObjectUnavailable("SlowDown", 503)

    ops.hooks["before_list_keys"] = unavailable
    sweep, _ = build(ops, "aws", {})
    sweep.token = "saved"
    with pytest.raises(ObjectUnavailable):
        await sweep.run_once()
    assert sweep.token == "saved"


@pytest.mark.asyncio
@pytest.mark.parametrize("ops_class,provider", [(FakeS3, "aws"), (FakeGcs, "gcp")])
async def test_database_lookup_keeps_live_attempts_and_removes_deleted_or_row_less_ones(
    catalog, identity, ops_class, provider
):
    from sqlalchemy import select

    from codemie.rest_api.models.hosted_application import HostedApplicationStorageAttempts as Attempt
    from codemie.rest_api.models.hosted_application_requests import AppCreate, VersionCreate

    service, repo, _ = catalog
    ops = ops_class()
    service.storage = storage = object_adapter(ops, provider)
    context = identity()
    original = b"<html>Stored</html>"
    app = await service.create_app(
        context, AppCreate(name="Visual", description="Sample", project="demo"), str(uuid4())
    )
    version = await service.begin_version(
        context,
        app["id"],
        VersionCreate(filename="visual.html", artifact_kind="html", byte_size=len(original)),
        app["etag"],
        str(uuid4()),
    )

    async def data():
        yield original

    await service.transfer(context, app["id"], version["version_id"], 1, version["upload_etag"], data())
    async with repo.sessions() as session:
        attempt = (await session.execute(select(Attempt).where(Attempt.state == "stored"))).scalars().one()
    old = datetime.now(timezone.utc) - timedelta(days=2)
    live_key = attempt.prefix + "/old.txt"
    row_less_key = "a-" + uuid4().hex + "/" + "/".join(uuid4().hex for _ in range(3)) + "/old.txt"
    seed(ops, live_key, old)
    seed(ops, row_less_key, old)
    sweep = HostedStorageSweep(storage, database_lookup(repo), budget_seconds=20, logger=Recorder())
    assert await sweep.run_once() == 1
    assert live_key in ops.objects and attempt.prefix + "/original" in ops.objects
    assert row_less_key not in ops.objects
    async with repo.sessions() as session, session.begin():
        # A deleted attempt holds no bytes and records a verified absence after its writer quiesced.
        row = await session.get(Attempt, attempt.id)
        now = datetime.now(timezone.utc)
        row.state, row.held_bytes, row.confirmed_bytes = "deleted", 0, 0
        row.writer_quiesced_at = row.absence_verified_at = now
    await sweep.run_once()
    assert live_key not in ops.objects


@pytest.mark.asyncio
async def test_long_sweep_does_not_delay_maintenance(monkeypatch):
    real_sleep = asyncio.sleep

    async def fast_sleep(seconds):
        await real_sleep(0.005)

    ops = FakeS3()
    entered, release = threading.Event(), threading.Event()

    def block(**_):
        entered.set()
        release.wait(10)

    ops.hooks["before_list_keys"] = block
    storage = object_adapter(ops, "aws")
    assert isinstance(storage, HostedObjectStorage)
    runtime = object.__new__(HostedApplicationsRuntime)
    runtime.settings = SimpleNamespace(enabled=True)
    runtime.tasks = []
    runtime.ready = False
    runtime.storage = storage
    runtime.config = SimpleNamespace(
        HOSTED_APPS_STORAGE_SWEEP_SECONDS=3600, HOSTED_APPS_STORAGE_SWEEP_BUDGET_SECONDS=20
    )
    runtime.sweep = HostedStorageSweep(storage, make_lookup({}), clock=ops.clock, budget_seconds=20, logger=Recorder())
    steps: list[str] = []

    async def maintain_once(*, retention_due):
        steps.append("cleanup expiry retry")

    runtime.maintain_once = maintain_once
    monkeypatch.setattr(runtime_module.asyncio, "sleep", fast_sleep)
    sweeping = asyncio.create_task(runtime.sweep_storage())
    maintaining = asyncio.create_task(runtime.maintain())
    try:
        for _ in range(400):
            if entered.is_set():
                break
            await real_sleep(0.01)
        assert entered.is_set()
        before = len(steps)
        await real_sleep(0.2)
        assert len(steps) > before + 3
    finally:
        release.set()
        for task in (sweeping, maintaining):
            task.cancel()
        await asyncio.gather(sweeping, maintaining, return_exceptions=True)
