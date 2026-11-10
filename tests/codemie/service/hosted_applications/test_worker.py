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

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.repository.hosted_applications.filesystem import HostedFileStorage
from codemie.rest_api.models.hosted_application import (
    HostedApplicationJobs,
    HostedApplicationVersions,
    HostedApplicationStorageAttempts,
    HostedApplicationUserState,
)
from codemie.rest_api.models.hosted_application_requests import AppCreate, VersionCreate, UploadComplete
from codemie.service.hosted_applications.worker import ValidationWorker
from tests.codemie.service.hosted_applications.storage_fakes import CROSS_ADAPTERS


async def queued(service, context, original=b"<html>Safe visual</html>"):
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

    receipt = await service.transfer(context, app["id"], version["version_id"], 1, version["upload_etag"], data())
    await service.complete_version(
        context,
        app["id"],
        version["version_id"],
        UploadComplete(upload_id=version["upload_id"], generation=1, sha256=receipt["sha256"]),
        receipt["version_etag"],
        str(uuid4()),
    )
    return app, version


async def launchable(service, context, app_id):
    """The working version passes the version eligibility check (what the removed ``can_launch`` flag reported)."""
    async with service.repository.transaction(*context) as tx:
        app = await tx.managed_app(app_id, context.user)
        return await service._eligible_version(tx, app, app.selected_version_id) is not None


@pytest.mark.asyncio
async def test_accepted_output_is_private_immutable_and_quota_matches_real_bytes(processing, identity):
    service, repo, _, worker, _ = processing
    context = identity()
    app, version = await queued(service, context)
    assert await worker.run_one()
    current = await service.get_app(context, app["id"])
    assert (current["shared"], current["is_global"]) == (False, False) and await launchable(service, context, app["id"])
    detail = await service.get_version(context, app["id"], version["version_id"])
    assert detail["validation_state"] == "accepted" and detail["job"]["state"] == "passed"
    async with repo.sessions() as session:
        state = await session.get(HostedApplicationUserState, "owner")
        assert state.reserved_bytes == 0
        assert state.stored_bytes == sum(
            p.stat().st_size for p in service.storage.base.rglob("*") if p.is_file() and ".locks" not in p.parts
        )
    assert not await worker.run_one()


async def read_object(storage, key, size):
    return b"".join([part async for part in storage.read_range(key, 0, size - 1)]) if size else b""


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", CROSS_ADAPTERS)
async def test_accepted_output_lands_on_every_adapter_with_ledger_size_and_digest(
    processing, identity, tmp_path, adapter
):
    from sqlalchemy import select

    service, repo, _, worker, _ = processing
    service.storage = worker.storage = storage = adapter(tmp_path / "cross-adapter-storage")
    context = identity()
    original = b"<html>Safe visual</html>"
    app, version = await queued(service, context, original)
    assert await worker.run_one()
    async with repo.sessions() as session:
        row = (
            await session.execute(
                select(HostedApplicationVersions).where(
                    HostedApplicationVersions.id == UUID(str(version["version_id"]))
                )
            )
        ).scalar_one()
        job = (
            await session.execute(
                select(HostedApplicationJobs).where(
                    HostedApplicationJobs.version_id == row.id, HostedApplicationJobs.state == "passed"
                )
            )
        ).scalar_one()
        source = await session.get(HostedApplicationStorageAttempts, row.source_attempt_id)
        output = await session.get(HostedApplicationStorageAttempts, job.output_attempt_id)
    assert row.validation_state == "accepted" and source.provider == output.provider == storage.provider
    assert await read_object(storage, source.prefix + "/original", len(original)) == original
    assert hashlib.sha256(original).digest() == row.artifact_digest
    report = await storage.read_small(job.report_key, maximum=1024 * 1024)
    assert job.report_key == output.prefix + "/report.json" and hashlib.sha256(report).digest() == job.report_digest
    manifest = await storage.read_small(row.manifest_key, maximum=1024 * 1024)
    assert row.manifest_key == output.prefix + "/manifest.json"
    assert hashlib.sha256(manifest).digest() == row.manifest_digest
    files = json.loads(manifest)["files"].values()
    assert files and all(item["key"].startswith(output.prefix + "/assets/") for item in files)
    for item in files:
        data = await read_object(storage, item["key"], item["size"])
        assert len(data) == item["size"] and hashlib.sha256(data).hexdigest() == item["sha256"]
    assert output.confirmed_bytes == len(report) + len(manifest) + sum(item["size"] for item in files)


@pytest.mark.asyncio
async def test_malware_rejection_never_becomes_executable(processing, identity):
    service, repo, _, worker, validator = processing
    context = identity()
    app, version = await queued(service, context)
    validator.rejection = HostedApplicationError("MALWARE_DETECTED", "Rejected")
    assert await worker.run_one()
    detail = await service.get_version(context, app["id"], version["version_id"])
    assert detail["security_state"] == "blocked" and detail["validation_state"] == "rejected"
    assert not await launchable(service, context, app["id"])
    report = await service.validation_report(context, app["id"], version["version_id"])
    assert report["errors"] == [
        {"code": "MALWARE_DETECTED", "message": "The malware scanner flagged this upload; upload a clean file"}
    ]
    async with repo.sessions() as session:
        state = await session.get(HostedApplicationUserState, "owner")
        assert state.reserved_bytes > 0  # No absence proof yet: unknown output remains charged.


WEBPACK_RUNTIME = (
    b"<html><script>var g=new Function('return this')();"
    b"if(window.Worker){new Worker('w.js')}navigator.serviceWorker.register('/sw.js')</script></html>"
)


@pytest.mark.asyncio
async def test_runtime_neutralized_capabilities_are_informational(processing, identity):
    service, _, _, worker, _ = processing
    context = identity()
    app, version = await queued(service, context, WEBPACK_RUNTIME)
    assert await worker.run_one()
    detail = await service.get_version(context, app["id"], version["version_id"])
    assert detail["validation_state"] == "accepted" and detail["job"]["state"] == "passed"
    assert await launchable(service, context, app["id"])
    report = await service.validation_report(context, app["id"], version["version_id"])
    assert [item["code"] for item in report["warnings"]] == ["RESTRICTED_CAPABILITY"]
    assert report["policy_version"] == "static-v4"


@pytest.mark.asyncio
async def test_expired_lease_cannot_write_or_accept(processing, identity):
    service, repo, _, worker, _ = processing
    app, version = await queued(service, identity())
    work = await worker.claim()
    async with repo.sessions() as session, session.begin():
        job = await session.get(HostedApplicationJobs, work["job"]["id"])
        job.lease_until -= timedelta(minutes=2)
    with pytest.raises(HostedApplicationError):
        await worker.process(work)
    assert list(service.storage.base.rglob("manifest.json")) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("code,status", [("WRITER_ACTIVE", 409), ("STORAGE_LIMIT", 413), ("UPLOAD_SIZE_MISMATCH", 422)])
async def test_infrastructure_conditions_are_retryable_failures(processing, identity, code, status):
    from datetime import timedelta as delta
    from sqlalchemy import select
    from codemie.service.hosted_applications.retry import RetryWorker

    service, repo, _, worker, validator = processing
    context = identity()
    app, version = await queued(service, context)
    validator.rejection = HostedApplicationError(code, "Infrastructure condition", status)
    assert await worker.run_one()
    detail = await service.get_version(context, app["id"], version["version_id"])
    assert detail["validation_state"] == "failed" and detail["security_state"] == "clear"
    assert detail["job"]["state"] == "failed" and detail["job"]["details"]["error_code"] == code
    async with repo.sessions() as session, session.begin():
        job = (
            await session.execute(
                select(HostedApplicationJobs).where(HostedApplicationJobs.id == UUID(detail["job"]["id"]))
            )
        ).scalar_one()
        job.available_at = job.created_at - delta(seconds=1)
    validator.rejection = None
    assert await RetryWorker(repo, service.storage, service.storage_namespace).run_one()
    assert await worker.run_one()
    assert (await service.get_version(context, app["id"], version["version_id"]))["validation_state"] == "accepted"


@pytest.mark.asyncio
async def test_truncated_validator_stream_is_not_an_artifact_rejection(processing, identity):
    service, _, _, worker, validator = processing
    context = identity()
    app, version = await queued(service, context)
    original = validator.file_chunks

    async def truncated(identifier, index, item):
        async for chunk in original(identifier, index, item):
            yield chunk[:-1]

    validator.file_chunks = truncated
    assert await worker.run_one()
    detail = await service.get_version(context, app["id"], version["version_id"])
    assert detail["validation_state"] == "failed" and detail["security_state"] == "clear"
    assert detail["job"]["details"]["error_code"] == "UPLOAD_SIZE_MISMATCH"
    assert (await service.get_app(context, app["id"]))["suspended"] is False


class RecordingStorage(HostedFileStorage):
    """Real filesystem adapter that records the lease handed to the writer and every renewal."""

    def __init__(self, base):
        super().__init__(base)
        self.opened_lease = None
        self.renewed = []

    @asynccontextmanager
    async def writer(self, prefix, *, byte_limit, object_limit, lease=None):
        self.opened_lease = lease
        async with super().writer(prefix, byte_limit=byte_limit, object_limit=object_limit, lease=lease) as writer:
            real = writer.renew

            def renew(value):
                self.renewed.append(value)
                real(value)

            writer.renew = renew
            yield writer


@pytest.mark.asyncio
async def test_validation_writer_gets_claim_lease_and_renewals(processing, identity, tmp_path, monkeypatch):
    from codemie.service.hosted_applications import worker as worker_module

    service, repo, _, worker, validator = processing
    context = identity()
    await queued(service, context)
    service.storage = worker.storage = storage = RecordingStorage(tmp_path / "private-storage")
    ticks = iter(range(1000, 2000))
    issued = []

    def monotonic():
        issued.append(float(next(ticks)))
        return issued[-1]

    worker.monotonic = monotonic
    monkeypatch.setattr(worker_module, "HEARTBEAT_SECONDS", 0.01)
    claims, beats, claim_rows, beat_rows = [], [], [], []
    claim, fence = worker.claim, worker.fence

    async def job_lease_end(job_id):
        # The lease the writer may act on ends at the earlier of the job's lease and its deadline.
        async with repo.sessions() as session:
            job = await session.get(HostedApplicationJobs, job_id)
            return min(job.lease_until, job.deadline_at)

    async def recording_claim():
        claims.append(await claim())
        claim_rows.append(await job_lease_end(claims[-1]["job"]["id"]))
        return claims[-1]

    async def recording_fence(work, *, heartbeat=False):
        lease = await fence(work, heartbeat=heartbeat)
        if heartbeat:
            beats.append(lease)
            beat_rows.append(await job_lease_end(work["job"]["id"]))
        return lease

    worker.claim, worker.fence = recording_claim, recording_fence
    real_inspect = validator.inspect

    @asynccontextmanager
    async def slow_inspect(*args, **kwargs):
        await asyncio.sleep(0.15)
        async with real_inspect(*args, **kwargs) as result:
            yield result

    validator.inspect = slow_inspect
    assert await worker.run_one()
    assert storage.opened_lease == claims[0]["lease"]
    assert claims[0]["lease"].received_at in issued
    assert len(beats) >= 2
    assert storage.renewed == beats
    assert all(lease.received_at in issued for lease in beats)
    # The writer signs from these values: each must be the row's lease end, never a longer window.
    assert claims[0]["lease"].until == claim_rows[0]
    assert [lease.until for lease in beats] == beat_rows
    assert all(lease.until - lease.database_now <= timedelta(seconds=30) for lease in [claims[0]["lease"], *beats])


SOURCE_PREFIX = "attempt/" + "c" * 32
OUTPUT_PREFIX = "attempt/" + "d" * 32
ORIGINAL = b"<html>Safe visual</html>"


def offline_worker(tmp_path, fake, provider):
    """A worker on an object fake with no database: ``fence`` passes, the source original is stored."""
    from codemie.service.hosted_applications.worker import ValidationWorker
    from tests.codemie.service.hosted_applications.conftest import TrustedValidatorSeam
    from tests.codemie.service.hosted_applications.storage_fakes import object_adapter, writer_lease

    storage = object_adapter(fake, provider)
    assert fake.put_if_absent(SOURCE_PREFIX + "/original", ORIGINAL)
    worker = ValidationWorker(None, storage, TrustedValidatorSeam(tmp_path))

    async def database_recheck(work, *, heartbeat=False):
        return None

    worker.fence = database_recheck
    work = {
        "source": {
            "prefix": SOURCE_PREFIX,
            "confirmed_bytes": len(ORIGINAL),
            "receipt_digest": hashlib.sha256(ORIGINAL).digest(),
        },
        "output": {"prefix": OUTPUT_PREFIX, "byte_upper_bound": 1024 * 1024, "object_limit": 100},
        "version": {"artifact_kind": "html", "requested_configuration": {}},
        "lease": writer_lease(),
    }
    return worker, storage, work


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["aws", "gcp"])
@pytest.mark.parametrize("outcome", ["accept", "await_configuration"])
async def test_validation_output_is_marked_quiesced_only_after_the_writer_exits(
    tmp_path, monkeypatch, provider, outcome
):
    from codemie.service.hosted_applications import worker as worker_module
    from tests.codemie.service.hosted_applications.storage_fakes import FakeGcs, FakeS3

    fake = FakeS3() if provider == "aws" else FakeGcs()
    worker, storage, work = offline_worker(tmp_path, fake, provider)
    if outcome == "await_configuration":
        monkeypatch.setattr(worker_module, "configuration", lambda candidate, requested: None)
    receipts = []

    async def receipt(*args):
        # accept() and await_configuration() set writer_quiesced_at: the writer must have exited already.
        receipts.append(
            {
                "writer_open": bool(storage._writers.get(OUTPUT_PREFIX)),
                "fences": [key for key in fake.objects if key.startswith(".fences/")],
                "uploads": list(getattr(fake, "uploads", {})),
            }
        )

    monkeypatch.setattr(worker, outcome, receipt)
    await worker._process(work)
    assert receipts == [{"writer_open": False, "fences": [], "uploads": []}]


@pytest.mark.asyncio
async def test_gcs_writer_that_opens_after_the_cleanup_proof_fails_at_the_database_recheck(tmp_path):
    from codemie.service.hosted_applications.worker import canceled
    from tests.codemie.service.hosted_applications.storage_fakes import FakeGcs

    fake = FakeGcs()
    worker, storage, work = offline_worker(tmp_path, fake, "gcp")
    proof = await storage.delete_prefix(OUTPUT_PREFIX)
    assert proof.writer_quiesced and proof.absence_verified
    proved_at = len(fake.calls)

    async def deleted_attempt(work, *, heartbeat=False):
        # The stalled writer resumes after cleanup deleted its attempt: the recheck after open() refuses it.
        raise canceled()

    worker.fence = deleted_attempt
    with pytest.raises(HostedApplicationError) as error:
        await worker._process(work)
    assert error.value.code == "VALIDATION_CANCELED"
    after_proof = fake.calls[proved_at:]
    assert any(
        call[0] == "create_if_absent" and call[1].startswith(f".fences/{OUTPUT_PREFIX}/") for call in after_proof
    )
    assert [call for call in after_proof if call[0] in ("begin_upload", "compose")] == []
    assert [key for key in fake.objects if key.startswith((OUTPUT_PREFIX + "/", ".fences/", ".staging/"))] == []


class ClaimSession:
    """Answers the claim query with no ready job and records the statements."""

    def __init__(self):
        self.statements = []

    @asynccontextmanager
    async def begin(self):
        yield

    async def execute(self, statement):
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.statements.append(sql)
        if "clock_timestamp" in sql:
            return SimpleNamespace(scalar_one=lambda: datetime(2026, 11, 5, tzinfo=timezone.utc))
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=list))


@pytest.mark.asyncio
async def test_claim_fair_share_groups_by_the_attempts_quota_holder():
    session = ClaimSession()

    @asynccontextmanager
    async def sessions():
        yield session

    storage = SimpleNamespace(provider="fs", namespace_suffix="hosted-apps")
    assert await ValidationWorker(SimpleNamespace(sessions=sessions), storage, None).claim() is None

    claim = session.statements[-1]
    assert "quota_principal_id" in claim and "owner_id" not in claim


def eligibility_rows(*, app_security="clear"):
    app = SimpleNamespace(lifecycle_state="active", security_state=app_security)
    version = SimpleNamespace(security_state="clear", configuration_revision=1)
    job = SimpleNamespace(source_digest=b"d", configuration_revision=1)
    output = SimpleNamespace(state="planned", provider="fs", namespace="hosted-apps")
    source = SimpleNamespace(state="stored", provider="fs", namespace="hosted-apps", receipt_digest=b"d")
    return app, version, job, output, source


@pytest.mark.parametrize("app_security", ["clear", "suspended"])
def test_a_suspended_apps_validation_job_is_still_eligible(app_security):
    storage = SimpleNamespace(provider="fs", owns_namespace=lambda namespace: True)
    worker = ValidationWorker(None, storage, None)

    assert worker._eligible(*eligibility_rows(app_security=app_security))


def test_a_blocked_version_is_not_eligible():
    storage = SimpleNamespace(provider="fs", owns_namespace=lambda namespace: True)
    app, version, job, output, source = eligibility_rows()
    version.security_state = "blocked"

    assert not ValidationWorker(None, storage, None)._eligible(app, version, job, output, source)
