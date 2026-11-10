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

"""Hosted traffic has its own bounded pool, and one failing maintenance step never starves the others."""

import os
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from codemie.clients import postgres
from codemie.clients.postgres import PostgresClient
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.repository.hosted_applications import factory
from codemie.service.hosted_applications import runtime
from codemie.service.hosted_applications.runtime import HostedApplicationsRuntime
from tests.codemie.service.hosted_applications.storage_fakes import FakeClock, FakeGcs, FakeS3


def filesystem_config(path):
    return SimpleNamespace(FILES_STORAGE_TYPE="filesystem", HOSTED_APPS_STORAGE_DIR=str(path))


def aws_config(grace=30):
    return SimpleNamespace(
        FILES_STORAGE_TYPE="aws",
        HOSTED_APPS_STORAGE_BUCKET="hosted-bucket",
        HOSTED_APPS_STORAGE_S3_GRACE_SECONDS=grace,
    )


def skewed(clock, seconds):
    async def read():
        return clock() + timedelta(seconds=seconds)

    return read


def use_fake_s3(monkeypatch, fake, buckets):
    def build(bucket):
        buckets.append(bucket)
        return fake

    monkeypatch.setattr(factory, "BotoS3Ops", build)


def captured_engine(monkeypatch, module):
    calls = []

    def create(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(url=url, kwargs=kwargs)

    monkeypatch.setattr(module, "create_async_engine", create)
    return calls


def test_hosted_engine_uses_core_database_and_schema_with_its_own_capped_pool(monkeypatch):
    core = captured_engine(monkeypatch, postgres)
    monkeypatch.setattr(PostgresClient, "_async_engines", {})
    PostgresClient.get_async_engine()
    hosted = captured_engine(monkeypatch, runtime)
    monkeypatch.setattr(runtime, "_ENGINES", {})
    engine = runtime.hosted_engine()
    assert runtime.hosted_engine() is engine, "One hosted engine per process"
    (core_url, core_options), (hosted_url, hosted_options) = core[0], hosted[0]
    assert hosted_url == core_url
    assert hosted_options["connect_args"] == core_options["connect_args"]
    assert hosted_options["pool_pre_ping"] is True and hosted_options["max_overflow"] == 0
    assert hosted_options["pool_size"] == runtime.DB_POOL_SIZE == 10
    assert hosted_options["pool_timeout"] == runtime.DB_POOL_TIMEOUT_SECONDS == 2.0
    sample = {"value": [1, "two"]}
    assert hosted_options["json_serializer"](sample) == core_options["json_serializer"](sample)
    assert len(hosted) == 1


def test_hosted_engine_hides_bound_parameters_so_a_failed_statement_cannot_print_a_token(monkeypatch):
    hosted = captured_engine(monkeypatch, runtime)
    monkeypatch.setattr(runtime, "_ENGINES", {})
    runtime.hosted_engine()
    assert hosted[0][1]["hide_parameters"] is True


@pytest.mark.asyncio
async def test_failed_link_insert_leaves_the_token_out_of_logs_and_exception_text(monkeypatch, caplog):
    import logging

    from codemie.rest_api.models.permission import ResourceType
    from codemie.service.permission import share_link_service

    url = os.environ.get("HOSTED_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set HOSTED_TEST_DATABASE_URL to an isolated PostgreSQL database")
    sentinel = "SENTINEL-TOKEN-must-never-be-logged"
    monkeypatch.setattr(share_link_service.secrets, "token_urlsafe", lambda *_: sentinel)
    engine = create_async_engine(url, hide_parameters=True)
    monkeypatch.setattr(runtime, "hosted_engine", lambda: engine)
    caplog.set_level(logging.DEBUG)
    actor = SimpleNamespace(id="u1", username="user", name="User")
    try:
        async with runtime.sessions() as session:
            # A NULL resource id violates NOT NULL on the INSERT, against the real database.
            with pytest.raises(IntegrityError) as failed:
                await share_link_service.aget_or_create_live(
                    session, ResourceType.HOSTED_APPLICATION, None, actor, "project"
                )
        assert sentinel not in str(failed.value)
        assert all(sentinel not in record.getMessage() for record in caplog.records)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_pool_checkout_timeout_is_reported_as_service_busy(monkeypatch):
    url = os.environ.get("HOSTED_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set HOSTED_TEST_DATABASE_URL to an isolated PostgreSQL database")
    small = create_async_engine(url, pool_size=1, max_overflow=0, pool_timeout=0.2)
    monkeypatch.setattr(runtime, "hosted_engine", lambda: small)
    try:
        async with runtime.sessions() as holder:
            await holder.execute(text("SELECT 1"))
            with pytest.raises(HostedApplicationError) as busy:
                async with runtime.sessions() as session:
                    await session.execute(text("SELECT 1"))
        assert busy.value.code == "SERVICE_BUSY" and busy.value.status == 503
        assert busy.value.message == "Applications are busy; retry shortly"
    finally:
        await small.dispose()


@pytest.mark.asyncio
async def test_close_disposes_the_hosted_engine(monkeypatch):
    disposed = []

    class Engine:
        async def dispose(self):
            disposed.append(True)

    monkeypatch.setattr(runtime, "_ENGINES", {os.getpid(): Engine()})
    instance = object.__new__(HostedApplicationsRuntime)
    instance.tasks, instance.ready, instance.settings = [], True, SimpleNamespace(enabled=False)
    await instance.close()
    assert disposed == [True] and os.getpid() not in runtime._ENGINES


@pytest.mark.asyncio
async def test_failing_maintenance_step_never_skips_the_other_steps():
    calls = []

    def step(name, *, result=False, error=None):
        async def run():
            calls.append(name)
            if error:
                raise error
            return result

        return run

    async def observed(operation, _name):
        return await operation()

    instance = object.__new__(HostedApplicationsRuntime)
    instance.maintenance = SimpleNamespace(
        expire=step("expire", error=RuntimeError("expire")),
        cleanup_one=step("cleanup"),
        finish_purge_one=step("purge"),
    )
    instance.retention = SimpleNamespace(
        finish_version_one=step("finish_version"),
        schedule_version_one=step("schedule_version", error=IntegrityError("INSERT", {}, Exception("unique"))),
        gc=step("gc"),
    )
    instance.retries = SimpleNamespace(run_one=step("retry"))
    instance.observability = SimpleNamespace(run=observed, refresh=step("refresh"))
    await instance.maintain_once(retention_due=True)
    assert calls == [
        "expire",
        "cleanup",
        "purge",
        "finish_version",
        "retry",
        "schedule_version",
        "gc",
        "refresh",
    ]
    calls.clear()
    await instance.maintain_once(retention_due=False)
    assert calls == ["expire", "cleanup", "purge", "finish_version", "retry"]


@pytest.mark.asyncio
async def test_hung_storage_keeps_the_feature_closed_instead_of_stalling_startup(monkeypatch, tmp_path):
    import asyncio
    import threading

    release = threading.Event()

    def hung_mount(path):
        release.wait(5)
        raise OSError("volume never answered")

    monkeypatch.setattr(factory, "HostedFileStorage", hung_mount)
    monkeypatch.setattr(runtime, "STORAGE_START_SECONDS", 0.05)
    instance = object.__new__(HostedApplicationsRuntime)
    instance.tasks, instance.ready, instance.settings = [], False, SimpleNamespace(enabled=True)
    instance.config = filesystem_config(tmp_path)
    started = asyncio.get_running_loop().time()
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(instance.start(), 3)
        assert asyncio.get_running_loop().time() - started < 1, "Startup must give up at the storage deadline"
        assert not instance.ready and instance.tasks == []
    finally:
        release.set()


@pytest.mark.asyncio
async def test_storage_opens_on_the_storage_executor_before_workers_start(monkeypatch, tmp_path):
    import threading

    from codemie.repository.hosted_applications.filesystem import HostedFileStorage

    threads = []

    def opened(path):
        threads.append(threading.current_thread().name)
        return HostedFileStorage(path)

    monkeypatch.setattr(factory, "HostedFileStorage", opened)
    instance = object.__new__(HostedApplicationsRuntime)
    instance.config = filesystem_config(tmp_path / "volume")
    instance.repository, instance.validator = object(), object()
    instance.catalog = SimpleNamespace(storage=None, storage_namespace="hosted-apps:volume:x")
    await instance.open_storage()
    assert threads and threads[0].startswith("hosted-storage")
    assert instance.catalog.storage is instance.storage is instance.worker.storage is instance.maintenance.storage


@pytest.mark.asyncio
async def test_filesystem_type_opens_the_filesystem_adapter(tmp_path):
    from codemie.repository.hosted_applications.filesystem import HostedFileStorage

    never = skewed(FakeClock(), 0)
    opened = await factory.open_hosted_storage(filesystem_config(tmp_path / "volume"), never)
    assert isinstance(opened, HostedFileStorage)


@pytest.mark.asyncio
async def test_aws_type_opens_s3_on_the_hosted_bucket(monkeypatch):
    from codemie.repository.hosted_applications.object_storage import HostedObjectStorage

    clock, buckets = FakeClock(), []
    use_fake_s3(monkeypatch, FakeS3(clock=clock), buckets)
    opened = await factory.open_hosted_storage(aws_config(), skewed(clock, 0), pod_clock=clock)
    assert isinstance(opened, HostedObjectStorage) and opened.provider == "aws"
    assert buckets == ["hosted-bucket"]
    assert opened.cleanup_grace_seconds == 30


@pytest.mark.asyncio
async def test_s3_opens_when_the_offsets_sum_stays_within_the_grace(monkeypatch):
    clock = FakeClock()
    use_fake_s3(monkeypatch, FakeS3(clock=clock), [])
    opened = await factory.open_hosted_storage(
        aws_config(grace=30), skewed(clock, 10), pod_clock=skewed_sync(clock, 20)
    )
    assert opened.provider == "aws"


@pytest.mark.asyncio
async def test_s3_refuses_to_open_when_the_offsets_sum_exceeds_the_grace(monkeypatch):
    clock = FakeClock()
    use_fake_s3(monkeypatch, FakeS3(clock=clock), [])
    with pytest.raises(HostedApplicationError) as refused:
        await factory.open_hosted_storage(aws_config(grace=30), skewed(clock, 20), pod_clock=skewed_sync(clock, -11))
    assert refused.value.code == "STORAGE_CLOCK_SKEW"


@pytest.mark.asyncio
async def test_skew_uses_the_midpoint_of_each_clock_around_the_head(monkeypatch):
    clock = FakeClock()
    use_fake_s3(monkeypatch, FakeS3(clock=clock), [])
    # A slow request: each local clock reads 20 s early before the HEAD and 20 s late after it.
    pod_readings, database_readings = iter([-20, 20]), iter([-20, 20])

    def pod():
        return clock() + timedelta(seconds=next(pod_readings))

    async def database():
        return clock() + timedelta(seconds=next(database_readings))

    opened = await factory.open_hosted_storage(aws_config(grace=1), database, pod_clock=pod)
    assert opened.provider == "aws"


@pytest.mark.asyncio
async def test_gcp_type_opens_gcs_on_the_hosted_bucket_without_a_skew_check(monkeypatch):
    from codemie.repository.hosted_applications.object_storage import HostedObjectStorage

    buckets = []

    def build(bucket):
        buckets.append(bucket)
        return FakeGcs()

    async def no_database_clock():
        raise AssertionError("GCS has no presigned expiry, so it reads no clock")

    monkeypatch.setattr(factory, "GcsOps", build)
    config = SimpleNamespace(FILES_STORAGE_TYPE="gcp", HOSTED_APPS_STORAGE_BUCKET="hosted-bucket")
    opened = await factory.open_hosted_storage(config, no_database_clock)
    assert isinstance(opened, HostedObjectStorage) and opened.provider == "gcp"
    assert buckets == ["hosted-bucket"] and opened.cleanup_grace_seconds == 0


def skewed_sync(clock, seconds):
    return lambda: clock() + timedelta(seconds=seconds)


@pytest.mark.asyncio
async def test_clock_skew_leaves_the_runtime_not_ready_and_logs_the_code(monkeypatch):
    clock = FakeClock()
    use_fake_s3(monkeypatch, FakeS3(clock=clock), [])
    monkeypatch.setattr(factory, "utc_now", skewed_sync(clock, 60))
    logged = []
    import sys

    logger_module = sys.modules["codemie.configs.logger"]
    monkeypatch.setattr(logger_module, "logger", SimpleNamespace(error=logged.append, warning=logged.append))
    instance = object.__new__(HostedApplicationsRuntime)
    instance.tasks, instance.ready, instance.settings = [], False, SimpleNamespace(enabled=True)
    instance.config = aws_config()
    instance.database_clock = skewed(clock, 0)
    with pytest.raises(HostedApplicationError):
        await instance.start()
    assert not instance.ready and instance.tasks == []
    assert any("STORAGE_CLOCK_SKEW" in line and "HostedApplicationError" in line for line in logged)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["aws", "gcp"])
async def test_hung_bucket_gives_up_at_the_storage_deadline(monkeypatch, provider):
    import asyncio
    import threading

    clock, release = FakeClock(), threading.Event()
    if provider == "aws":
        fake = FakeS3(clock=clock)
        # The marker request never answers.
        fake.hooks["before_put_if_absent"] = lambda **_: release.wait(3)
        use_fake_s3(monkeypatch, fake, [])
        config = aws_config()
    else:

        def hung_client(bucket):
            # Resolving the default credentials never answers (for example a hung metadata server).
            release.wait(3)
            return FakeGcs()

        monkeypatch.setattr(factory, "GcsOps", hung_client)
        config = SimpleNamespace(FILES_STORAGE_TYPE="gcp", HOSTED_APPS_STORAGE_BUCKET="hosted-bucket")
    monkeypatch.setattr(runtime, "STORAGE_START_SECONDS", 0.05)
    instance = object.__new__(HostedApplicationsRuntime)
    instance.tasks, instance.ready, instance.settings = [], False, SimpleNamespace(enabled=True)
    instance.config = config
    instance.database_clock = skewed(clock, 0)
    started = asyncio.get_running_loop().time()
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(instance.start(), 5)
        assert asyncio.get_running_loop().time() - started < 1, "Startup must give up at the storage deadline"
        assert not instance.ready and instance.tasks == []
    finally:
        release.set()


@pytest.mark.asyncio
async def test_slow_object_open_leaves_the_runtime_not_ready(monkeypatch):
    import asyncio
    import threading

    clock, release = FakeClock(), threading.Event()
    fake = FakeS3(clock=clock)
    fake.hooks["before_server_date"] = lambda **_: release.wait(5)
    use_fake_s3(monkeypatch, fake, [])
    monkeypatch.setattr(runtime, "STORAGE_START_SECONDS", 0.05)
    instance = object.__new__(HostedApplicationsRuntime)
    instance.tasks, instance.ready, instance.settings = [], False, SimpleNamespace(enabled=True)
    instance.config = aws_config()
    instance.database_clock = skewed(clock, 0)
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(instance.start(), 3)
        assert not instance.ready and instance.tasks == []
    finally:
        release.set()
