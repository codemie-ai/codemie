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
from tests.codemie.service.hosted_applications.conftest import preview_jpeg
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


async def accepted_output(repo, storage, version_id):
    """The accepted version's row, its passed job and output attempt, its manifest and its report bytes."""
    from sqlalchemy import select

    async with repo.sessions() as session:
        row = await session.get(HostedApplicationVersions, UUID(str(version_id)))
        job = (
            await session.execute(
                select(HostedApplicationJobs).where(
                    HostedApplicationJobs.version_id == row.id, HostedApplicationJobs.kind == "validate"
                )
            )
        ).scalar_one()
        output = await session.get(HostedApplicationStorageAttempts, job.output_attempt_id)
    assert row.validation_state == "accepted" and job.state == "passed"
    manifest = await storage.read_small(row.manifest_key, maximum=1024 * 1024)
    report = await storage.read_small(job.report_key, maximum=1024 * 1024)
    return row, job, output, manifest, report


def stored_files(storage):
    return [p for p in storage.base.rglob("*") if p.is_file() and ".locks" not in p.parts]


@pytest.mark.asyncio
async def test_accepted_version_stores_the_preview_pointer(processing, identity):
    from codemie.hosted_apps_validator.contract import CAPTURE_HARD_SECONDS, MAX_CAPTURE_LOCK_WAIT_SECONDS
    from codemie.service.hosted_applications.preview import CAPTURE_OVERHEAD_SECONDS

    service, repo, _, worker, validator = processing
    validator.preview = picture = preview_jpeg()
    context = identity()
    _, version = await queued(service, context)
    assert await worker.run_one()
    row, _, output, manifest_data, report = await accepted_output(repo, service.storage, version["version_id"])
    manifest = json.loads(manifest_data)
    # The pointer is in the version row; the manifest does not carry it.
    assert "preview" not in manifest
    assert row.preview_key == output.prefix + "/preview.jpg"
    assert row.preview_size == len(picture)
    assert row.preview_sha256 == hashlib.sha256(picture).digest()
    assert await read_object(service.storage, output.prefix + "/preview.jpg", len(picture)) == picture
    assert validator.capture_calls == [
        {
            "identifier": "a" * 64,
            "site_root": row.site_root,
            "entrypoint": row.entrypoint_path,
            "routing_mode": "static",
            "lock_wait": MAX_CAPTURE_LOCK_WAIT_SECONDS,
            "timeout": MAX_CAPTURE_LOCK_WAIT_SECONDS + CAPTURE_HARD_SECONDS + CAPTURE_OVERHEAD_SECONDS,
        }
    ]
    files = sum(item["size"] for item in manifest["files"].values())
    assert output.confirmed_bytes == len(report) + len(manifest_data) + files + len(picture)
    async with repo.sessions() as session:
        state = await session.get(HostedApplicationUserState, "owner")
    assert state.stored_bytes == sum(p.stat().st_size for p in stored_files(service.storage))


async def outcome(service, repo, context, app_id, version_id):
    """What the owner and the audit see of one validated version, with the identifiers of its rows left out."""
    from sqlalchemy import select
    from codemie.rest_api.models.hosted_application import HostedApplicationOperations as Operation

    detail = await service.get_version(context, app_id, version_id)
    _, job, _, _, report = await accepted_output(repo, service.storage, version_id)
    async with repo.sessions() as session:
        rows = (await session.execute(select(Operation).where(Operation.app_id == UUID(str(app_id))))).scalars().all()
    audit = sorted(
        (
            row.action,
            row.state,
            row.response_status,
            row.actor_kind,
            str(row.actor_id),
            json.dumps({k: v for k, v in (row.audit_details or {}).items() if k != "job_id"}, sort_keys=True)
            if row.actor_kind == "system"
            else "",
        )
        for row in rows
    )
    verdict = (detail["validation_state"], detail["security_state"], detail["job"]["state"])
    return verdict, report, audit, dict(job.safe_details)


@pytest.mark.asyncio
async def test_preview_leaves_verdict_report_and_audit_unchanged(processing, identity):
    service, repo, _, worker, validator = processing
    context = identity()
    plain_app, plain = await queued(service, context)
    assert await worker.run_one()
    validator.preview = preview_jpeg()
    pictured_app, pictured = await queued(service, context)
    assert await worker.run_one()
    without = await outcome(service, repo, context, plain_app["id"], plain["version_id"])
    with_preview = await outcome(service, repo, context, pictured_app["id"], pictured["version_id"])
    assert without == with_preview
    assert without[0] == ("accepted", "clear", "passed")


def unavailable_error():
    return HostedApplicationError("VALIDATION_UNAVAILABLE", "Artifact validation is unavailable; retry later", 503)


PREVIEW_FAULTS = {
    "capture_error": lambda seam, picture: setattr(seam, "capture_error", unavailable_error()),
    "no_picture": lambda seam, picture: setattr(seam, "preview", None),
    "pull_error": lambda seam, picture: setattr(seam, "preview_pull_error", unavailable_error()),
    "digest_mismatch": lambda seam, picture: setattr(seam, "preview_changes", {"sha256": "0" * 64}),
    "size_mismatch": lambda seam, picture: setattr(seam, "preview_changes", {"size": len(picture) - 1}),
    "not_a_jpeg": lambda seam, picture: setattr(seam, "preview", picture[:-2] + b"\0\0"),
    "wrong_dimensions": lambda seam, picture: setattr(seam, "preview_changes", {"width": 65}),
}


async def assert_accepted_without_preview(service, repo, version_id):
    """The version is accepted, its pointer columns are empty and its attempt holds no picture."""
    row, _, output, manifest_data, report = await accepted_output(repo, service.storage, version_id)
    manifest = json.loads(manifest_data)
    assert "preview" not in manifest
    assert (row.preview_key, row.preview_size, row.preview_sha256) == (None, None, None)
    assert not [p for p in stored_files(service.storage) if p.name == "preview.jpg" and output.prefix in str(p)]
    files = sum(item["size"] for item in manifest["files"].values())
    assert output.confirmed_bytes == len(report) + len(manifest_data) + files


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", sorted(PREVIEW_FAULTS))
async def test_a_preview_that_fails_a_check_is_left_out_and_the_version_is_accepted(processing, identity, fault):
    service, repo, _, worker, validator = processing
    validator.preview = picture = preview_jpeg()
    PREVIEW_FAULTS[fault](validator, picture)
    _, version = await queued(service, identity())
    assert await worker.run_one()
    assert len(validator.capture_calls) == 1
    await assert_accepted_without_preview(service, repo, version["version_id"])


@pytest.mark.asyncio
async def test_an_accept_without_a_picture_clears_a_stale_pointer(processing, identity):
    service, repo, _, worker, validator = processing
    validator.preview = None
    _, version = await queued(service, identity())
    async with repo.sessions() as session:
        row = await session.get(HostedApplicationVersions, UUID(str(version["version_id"])))
        row.preview_key, row.preview_size, row.preview_sha256 = "stale/preview.jpg", 5, b"\x01" * 32
        await session.commit()
    assert await worker.run_one()
    await assert_accepted_without_preview(service, repo, version["version_id"])


def limit_output(worker, **limits):
    """Narrow the claimed job's output reservation as the writer and the room check see it."""
    claim = worker.claim

    async def narrowed():
        work = await claim()
        if work is not None:
            work["output"].update(limits)
        return work

    worker.claim = narrowed


@pytest.mark.asyncio
async def test_no_room_leaves_the_pointer_empty(processing, identity):
    service, repo, _, worker, validator = processing
    context = identity()
    validator.preview = preview_jpeg()
    _, first = await queued(service, context)
    assert await worker.run_one()
    needed = (await accepted_output(repo, service.storage, first["version_id"]))[2].confirmed_bytes
    limit_output(worker, byte_upper_bound=needed - 1)
    _, short = await queued(service, context)
    assert await worker.run_one()
    await assert_accepted_without_preview(service, repo, short["version_id"])
    limit_output(worker, byte_upper_bound=needed)  # Wraps the narrowing above: the last value wins.
    _, exact = await queued(service, context)
    assert await worker.run_one()
    assert (await accepted_output(repo, service.storage, exact["version_id"]))[0].preview_key is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("room,kept", [(2, False), (3, True)])
async def test_a_preview_without_object_room_is_left_out(processing, identity, room, kept):
    service, repo, _, worker, validator = processing
    validator.preview = preview_jpeg()
    files = 1  # One HTML upload is one validated file.
    limit_output(worker, object_limit=files + room)
    _, version = await queued(service, identity())
    assert await worker.run_one()
    assert len(validator.candidate.files) == files
    row = (await accepted_output(repo, service.storage, version["version_id"]))[0]
    assert (row.preview_key is not None) is kept


@pytest.mark.asyncio
@pytest.mark.parametrize("extra,called", [(-1, False), (1, True)])
async def test_capture_starts_only_with_room_for_the_capture_and_the_writes_in_the_budget(
    processing, identity, extra, called
):
    from codemie.service.hosted_applications.preview import CAPTURE_REQUEST_SECONDS, write_seconds

    service, repo, _, worker, validator = processing
    validator.preview = preview_jpeg()
    claim = worker.claim

    async def late_claim():
        work = await claim()
        if work is not None:

            def late():
                # The clock reads ``extra`` seconds more than the capture and the writes after it need.
                candidate = getattr(validator, "candidate", None)
                reserve = CAPTURE_REQUEST_SECONDS + (write_seconds(candidate) if candidate is not None else 0)
                return work["lease"].received_at + work["budget"] - (reserve + extra)

            worker.monotonic = late
        return work

    worker.claim = late_claim
    _, version = await queued(service, identity())
    assert await worker.run_one()
    assert bool(validator.capture_calls) is called
    row = (await accepted_output(repo, service.storage, version["version_id"]))[0]
    assert (row.preview_key is not None) is called


@pytest.fixture
def outcomes(monkeypatch):
    """The automatic cover outcomes the worker counts, in order."""
    from types import SimpleNamespace

    from codemie.service.hosted_applications import observability

    counted = []
    monitoring = SimpleNamespace(preview_capture=counted.append, validation=lambda hard: None)
    monkeypatch.setattr(observability, "hosted_request_monitoring", lambda: monitoring)
    return counted


EXPECTED_OUTCOME = {
    "capture_error": "failed",
    "no_picture": "no_picture",
    "pull_error": "failed",
    "digest_mismatch": "mismatch",
    "size_mismatch": "failed",
    "not_a_jpeg": "mismatch",
    "wrong_dimensions": "mismatch",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", sorted(PREVIEW_FAULTS))
async def test_each_failed_capture_is_counted_once(processing, identity, outcomes, fault):
    service, _, _, worker, validator = processing
    validator.preview = picture = preview_jpeg()
    PREVIEW_FAULTS[fault](validator, picture)
    await queued(service, identity())
    assert await worker.run_one()
    assert outcomes == [EXPECTED_OUTCOME[fault]]


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["off", "busy", "no_picture", "unknown", "failed"])
async def test_a_validator_reason_is_counted_as_it_is(processing, identity, outcomes, reason):
    service, _, _, worker, validator = processing
    validator.no_picture_reason = reason
    await queued(service, identity())
    assert await worker.run_one()
    assert outcomes == [reason]


@pytest.mark.asyncio
async def test_a_stored_picture_is_counted_as_captured(processing, identity, outcomes):
    service, _, _, worker, validator = processing
    validator.preview = preview_jpeg()
    await queued(service, identity())
    assert await worker.run_one()
    assert outcomes == ["captured"]


@pytest.mark.asyncio
async def test_a_write_failure_after_the_pull_is_counted_as_failed_once(processing, identity, outcomes, monkeypatch):
    service, _, _, worker, validator = processing
    validator.preview = preview_jpeg()
    failure = unavailable_error()

    async def broken(*args, **kwargs):
        raise failure

    monkeypatch.setattr(worker, "_write_output", broken)
    seen = []
    fail = worker.fail

    async def failing(work, code):
        seen.append(code)
        await fail(work, code)

    worker.fail = failing
    await queued(service, identity())
    assert await worker.run_one()
    assert outcomes == ["failed"]
    assert seen == [failure.code], "The original error still reaches the job's failure handling"


@pytest.mark.asyncio
async def test_a_picture_without_room_is_counted_as_no_room(processing, identity, outcomes):
    service, _, _, worker, validator = processing
    validator.preview = preview_jpeg()
    limit_output(worker, object_limit=3)
    await queued(service, identity())
    assert await worker.run_one()
    assert outcomes == ["no_room"]


def set_safe_details(worker, details):
    claim = worker.claim

    async def claimed():
        work = await claim()
        if work is not None:
            work["job"]["safe_details"] = details
        return work

    worker.claim = claimed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "details,retried",
    [
        ({"retry_count": 1}, True),
        ({"retry_count": 3}, True),
        ({"retry_count": 0}, False),
        ({"retry_count": True}, False),
        ({"retry_count": "2"}, False),
        ({}, False),
    ],
)
async def test_only_a_job_with_a_positive_integer_retry_count_skips_the_capture(
    processing, identity, outcomes, details, retried
):
    service, repo, _, worker, validator = processing
    validator.preview = preview_jpeg()
    set_safe_details(worker, details)
    _, version = await queued(service, identity())
    assert await worker.run_one()
    assert bool(validator.capture_calls) is not retried
    assert outcomes == (["skipped_retry"] if retried else ["captured"])
    if retried:
        await assert_accepted_without_preview(service, repo, version["version_id"])


@pytest.mark.asyncio
async def test_a_capture_without_budget_is_counted_as_skipped_budget(processing, identity, outcomes):
    from codemie.service.hosted_applications.preview import CAPTURE_REQUEST_SECONDS, write_seconds

    service, _, _, worker, validator = processing
    validator.preview = preview_jpeg()
    claim = worker.claim

    async def late_claim():
        work = await claim()
        if work is not None:

            def late():
                candidate = getattr(validator, "candidate", None)
                reserve = CAPTURE_REQUEST_SECONDS + (write_seconds(candidate) if candidate is not None else 0)
                return work["lease"].received_at + work["budget"] - (reserve - 1)

            worker.monotonic = late
        return work

    worker.claim = late_claim
    await queued(service, identity())
    assert await worker.run_one()
    assert validator.capture_calls == []
    assert outcomes == ["skipped_budget"]


@pytest.mark.asyncio
async def test_the_lock_wait_uses_the_spare_time_of_the_job(processing, identity):
    from codemie.hosted_apps_validator.contract import CAPTURE_HARD_SECONDS
    from codemie.service.hosted_applications.preview import (
        CAPTURE_OVERHEAD_SECONDS,
        CAPTURE_REQUEST_SECONDS,
        write_seconds,
    )

    service, _, _, worker, validator = processing
    claim = worker.claim

    async def tight_claim():
        work = await claim()
        if work is not None:

            def late():
                candidate = getattr(validator, "candidate", None)
                # 10 seconds of spare beyond the shortest capture request.
                reserve = CAPTURE_REQUEST_SECONDS + 10 + (write_seconds(candidate) if candidate is not None else 0)
                return work["lease"].received_at + work["budget"] - reserve

            worker.monotonic = late
        return work

    worker.claim = tight_claim
    await queued(service, identity())
    assert await worker.run_one()
    (call,) = validator.capture_calls
    assert call["lock_wait"] == pytest.approx(13)
    assert call["timeout"] == pytest.approx(13 + CAPTURE_HARD_SECONDS + CAPTURE_OVERHEAD_SECONDS)


@pytest.mark.asyncio
async def test_no_capture_for_a_rejected_version(processing, identity):
    service, _, _, worker, validator = processing
    validator.preview = preview_jpeg()
    validator.rejection = HostedApplicationError("MALWARE_DETECTED", "Rejected")
    context = identity()
    app, version = await queued(service, context)
    assert await worker.run_one()
    assert (await service.get_version(context, app["id"], version["version_id"]))["validation_state"] == "rejected"
    assert validator.capture_calls == []


@pytest.mark.asyncio
async def test_no_capture_for_a_version_awaiting_configuration(processing, identity, monkeypatch):
    from codemie.service.hosted_applications import worker as worker_module

    service, _, _, worker, validator = processing
    validator.preview = preview_jpeg()
    monkeypatch.setattr(worker_module, "configuration", lambda candidate, requested: None)
    context = identity()
    app, version = await queued(service, context)
    assert await worker.run_one()
    detail = await service.get_version(context, app["id"], version["version_id"])
    assert detail["validation_state"] == "awaiting_configuration"
    assert validator.capture_calls == []
    assert not [p for p in stored_files(service.storage) if p.name in ("preview.jpg", "manifest.json")]


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
        "budget": 120.0,
        "job": {"safe_details": {}},
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


async def cross_adapter_worker(tmp_path, adapter):
    """A worker on one CROSS_ADAPTERS storage with no database: the original is stored, ``accept`` is recorded."""
    from tests.codemie.service.hosted_applications.conftest import TrustedValidatorSeam
    from tests.codemie.service.hosted_applications.storage_fakes import writer_lease
    from codemie.service.hosted_applications.worker import bytes_chunk

    storage = adapter(tmp_path / "cross-adapter-storage")
    async with storage.writer(SOURCE_PREFIX, byte_limit=1024, object_limit=1, lease=writer_lease()) as writer:
        await writer.put("original", bytes_chunk(ORIGINAL), expected_size=len(ORIGINAL))
    seam = TrustedValidatorSeam(tmp_path)
    worker = ValidationWorker(None, storage, seam)

    async def database_recheck(work, *, heartbeat=False):
        return None

    accepted = []

    async def accept(*args):
        accepted.append(args)

    worker.fence, worker.accept = database_recheck, accept
    work = {
        "source": {
            "prefix": SOURCE_PREFIX,
            "confirmed_bytes": len(ORIGINAL),
            "receipt_digest": hashlib.sha256(ORIGINAL).digest(),
        },
        "output": {"prefix": OUTPUT_PREFIX, "byte_upper_bound": 1024 * 1024, "object_limit": 100},
        "version": {"artifact_kind": "html", "requested_configuration": {}},
        "lease": writer_lease(),
        "budget": 120.0,
        "job": {"safe_details": {}},
    }
    return worker, storage, seam, work, accepted


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", CROSS_ADAPTERS)
async def test_a_captured_preview_is_stored_and_counted_on_every_adapter(tmp_path, adapter):
    worker, storage, seam, work, accepted = await cross_adapter_worker(tmp_path, adapter)
    seam.preview = picture = preview_jpeg()
    await worker._process(work)
    raw = await storage.read_small(OUTPUT_PREFIX + "/manifest.json", maximum=1024 * 1024)
    manifest = json.loads(raw)
    assert "preview" not in manifest
    assert await read_object(storage, OUTPUT_PREFIX + "/preview.jpg", len(picture)) == picture
    (args,) = accepted
    manifest_data, pointer = args[-2:]
    assert manifest_data == raw
    assert pointer == (OUTPUT_PREFIX + "/preview.jpg", len(picture), hashlib.sha256(picture).digest())
    assert pointer.size == len(picture), "accept() counts the picture's bytes in the attempt and the quota"


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", CROSS_ADAPTERS)
async def test_a_preview_with_a_wrong_digest_is_not_stored_on_any_adapter(tmp_path, adapter):
    worker, storage, seam, work, accepted = await cross_adapter_worker(tmp_path, adapter)
    seam.preview = preview_jpeg()
    seam.preview_changes = {"sha256": "0" * 64}
    await worker._process(work)
    manifest = json.loads(await storage.read_small(OUTPUT_PREFIX + "/manifest.json", maximum=1024 * 1024))
    assert "preview" not in manifest
    with pytest.raises(HostedApplicationError):
        await read_object(storage, OUTPUT_PREFIX + "/preview.jpg", 1)
    (args,) = accepted
    assert args[-1] is None, "No pointer and no picture bytes are counted"


@pytest.mark.asyncio
async def test_no_capture_when_the_budget_cannot_also_cover_the_output_writes(tmp_path):
    from codemie.service.hosted_applications.preview import CAPTURE_REQUEST_SECONDS
    from tests.codemie.service.hosted_applications.storage_fakes import filesystem_adapter

    worker, _, seam, work, accepted = await cross_adapter_worker(tmp_path, filesystem_adapter)
    seam.preview = preview_jpeg()
    # One second more than the capture itself may take: nothing would be left to write the output.
    stamp = work["lease"].received_at + work["budget"] - (CAPTURE_REQUEST_SECONDS + 1)
    worker.monotonic = lambda: stamp
    await worker._process(work)
    assert seam.capture_calls == []
    assert len(accepted) == 1


def test_a_capture_request_covers_the_lock_wait_and_the_hard_limit():
    from codemie.hosted_apps_validator import contract
    from codemie.service.hosted_applications.preview import CAPTURE_REQUEST_SECONDS

    # The validator may wait for its single renderer, then run it for the hard limit; the request outlasts both.
    assert contract.CAPTURE_LOCK_WAIT_SECONDS + contract.CAPTURE_HARD_SECONDS < CAPTURE_REQUEST_SECONDS


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
