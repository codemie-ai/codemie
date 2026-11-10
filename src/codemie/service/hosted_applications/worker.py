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

"""PostgreSQL leased validation queue. Storage I/O never runs under database locks."""

from __future__ import annotations

import asyncio
import hashlib
import time
from contextlib import suppress
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import exists, func, select
from sqlalchemy.orm import aliased

from codemie.core.hosted_applications.errors import ARTIFACT_REJECTION_CODES, HostedApplicationError
from codemie.core.hosted_applications.manifest import build_manifest, parse_manifest
from codemie.core.hosted_applications.policy import POLICY_DIGEST, POLICY_VERSION
from codemie.core.hosted_applications.protocol import canonical_json
from codemie.repository.hosted_applications.contract import build_writer_lease
from codemie.hosted_apps_validator.contract import LIMIT_REJECTION_CODES, NOT_ACCEPTED_CODES, validation_budget
from codemie.rest_api.models.hosted_application import (
    HostedApplicationJobs as Job,
    HostedApplicationVersions as Version,
    HostedApplicationStorageAttempts as Attempt,
    HostedApplicationUserState as UserState,
    HostedApplications as App,
    HostedApplicationOperations as Operation,
)


# Infrastructure conditions fail one attempt and are retried with a fresh attempt. Artifact findings
# (ARTIFACT_REJECTION_CODES) and limits the upload exceeds (LIMIT_REJECTION_CODES) end validation.
RETRYABLE_ERROR_CODES = frozenset(
    {
        "VALIDATION_UNAVAILABLE",
        "STORAGE_UNAVAILABLE",
        "STORAGE_LIMIT",
        "STORAGE_IMMUTABLE",
        "WRITER_ACTIVE",
        "UPLOAD_SIZE_MISMATCH",
        "CONTENT_UNAVAILABLE",
        "SERVICE_BUSY",
    }
)
# A job the validator never accepted goes back to the queue after this pause; it is not a failed attempt.
REQUEUE_SECONDS = 5
# The database lease is 30 seconds; the worker renews it this often.
HEARTBEAT_SECONDS = 10
# Claim looks past this many queue heads, so one locked row never stalls the whole queue.
CLAIM_CANDIDATES = 5


def retry_delay(job):
    retries = job.safe_details.get("retry_count", 0)
    retries = retries if type(retries) is int and 0 <= retries <= 3 else 3
    return timedelta(seconds=5 * 2**retries)


def canceled():
    return HostedApplicationError("VALIDATION_CANCELED", "Validation is no longer eligible", 409)


def expired():
    # The lease or the job's time budget ran out: a retryable infrastructure outcome, not a changed version.
    return HostedApplicationError("VALIDATION_UNAVAILABLE", "Validation exceeded its time budget", 503)


def fail_attempt(session, *, job, app, version, output, now, code, phase):
    """Terminally fail one queued attempt; its output is reclaimed by the fenced cleanup path."""
    job.state, job.phase, job.completed_at = "failed", phase, now
    job.safe_details = {**job.safe_details, "error_code": code}
    job.available_at = now + retry_delay(job)
    job.revision += 1
    if output is not None and output.state != "deleted":
        output.state, output.writer_fence, output.updated_at = "delete_requested", uuid4(), now
        output.revision += 1
    version.validation_state = "failed"
    version.revision += 1
    session.add(
        Operation(
            id=uuid4(),
            actor_kind="system",
            actor_id="hosted-validation",
            app_id=app.id,
            target_id=str(job.id),
            action="version.validation.failed",
            state="completed",
            response_status=200,
            audit_details={"job_id": str(job.id), "error_code": code, "phase": phase, "validation_status": 503},
            created_at=now,
            completed_at=now,
        )
    )


def configuration(candidate, requested):
    root = requested.get("site_root_hint")
    entry = requested.get("entrypoint_hint")
    if root is None:
        if len(candidate.roots) != 1:
            return None
        root = candidate.roots[0]
    if root not in candidate.roots:
        return None
    start = root + "/" if root else ""
    entries = [item for item in candidate.entries if item.startswith(start)]
    if entry is None:
        if start + "index.html" in entries:
            entry = start + "index.html"
        elif len(entries) == 1:
            entry = entries[0]
        else:
            return None
    if entry not in entries:
        return None
    return root, entry, requested.get("routing_mode", "static")


async def bytes_chunk(data):
    yield data


def security_clear(version):
    """Only a version's own security state gates validation; a suspended application still validates uploads."""
    return version.security_state == "clear"


class ValidationWorker:
    def __init__(self, repository, storage, validator, monotonic=time.monotonic):
        self.repository, self.storage, self.validator = repository, storage, validator
        # Injectable so tests control the clock that stamps each writer lease on arrival.
        self.monotonic = monotonic

    async def claim(self):
        async with self.repository.sessions() as session, session.begin():
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            running, running_output = aliased(Job), aliased(Attempt)
            # Fair share: quota holders without a running validation go first, FIFO within each group.
            holder_busy = exists(
                select(running.id)
                .join(running_output, running_output.id == running.output_attempt_id)
                .where(
                    running.state == "running",
                    running.kind == "validate",
                    running_output.quota_principal_id == Attempt.quota_principal_id,
                )
            )
            # Jobs bound to any policy are claimed: an older pod's queue is rebound, never stranded.
            jobs = (
                (
                    await session.execute(
                        select(Job)
                        .join(Attempt, Attempt.id == Job.output_attempt_id)
                        .where(
                            Job.kind == "validate",
                            Job.state == "ready",
                            Attempt.provider == self.storage.provider,
                            Attempt.namespace.endswith(self.storage.namespace_suffix),
                            Job.available_at <= now,
                        )
                        .order_by(holder_busy, Job.available_at, Job.id)
                        .with_for_update(skip_locked=True)
                        .limit(CLAIM_CANDIDATES)
                    )
                )
                .scalars()
                .all()
            )
            for job in jobs:
                work = await self._claim_candidate(session, job, now)
                if work is not None:
                    break
            else:
                return None
        # The monotonic stamp is taken as soon as the claim transaction has returned.
        work["lease"] = replace(work["lease"], received_at=self.monotonic())
        return work

    def _eligible(self, app, version, job, output, source):
        return not (
            app.lifecycle_state != "active"
            or not security_clear(version)
            or output is None
            or output.state != "planned"
            or output.provider != self.storage.provider
            or not self.storage.owns_namespace(output.namespace)
            or source is None
            or source.state != "stored"
            or source.provider != self.storage.provider
            or not self.storage.owns_namespace(source.namespace)
            or source.receipt_digest != job.source_digest
            or version.configuration_revision != job.configuration_revision
        )

    async def _claim_candidate(self, session, job, now):
        """Lease one ready job, or None to try the next one. Never waits for a locked row."""
        app = (
            await session.execute(
                select(App)
                .where(App.id == job.app_id)
                # FOR NO KEY UPDATE: launch tickets and audit rows keep their foreign-key locks on the app.
                .with_for_update(key_share=True, skip_locked=True)
            )
        ).scalar_one_or_none()
        if app is None:
            return None
        # The quota holder is the one the output attempt was charged to when the upload was reserved.
        holder = (
            await session.execute(select(Attempt.quota_principal_id).where(Attempt.id == job.output_attempt_id))
        ).scalar_one_or_none()
        state = (
            await session.execute(
                select(UserState).where(UserState.principal_id == holder).with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        version = (
            await session.execute(
                select(Version)
                .where(Version.app_id == job.app_id, Version.id == job.version_id)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if state is None or version is None:
            return None
        output = await session.get(Attempt, job.output_attempt_id, with_for_update=True)
        source = await session.get(Attempt, version.source_attempt_id)
        if not self._eligible(app, version, job, output, source):
            # A transient failure: the retry queue picks the version up again once it is eligible
            # otherwise it stays failed.
            fail_attempt(
                session,
                job=job,
                app=app,
                version=version,
                output=output,
                now=now,
                code="VALIDATION_UNAVAILABLE",
                phase="eligibility_changed",
            )
            return None
        if (job.policy_version, job.policy_definition_digest) != (POLICY_VERSION, POLICY_DIGEST):
            # Queued work is a request to validate; its attestation is bound to the policy that executes it.
            job.safe_details = {**job.safe_details, "rebound_from": job.policy_version}
            job.policy_version, job.policy_definition_digest = POLICY_VERSION, POLICY_DIGEST
        token = uuid4()
        budget = validation_budget(version.artifact_kind, source.confirmed_bytes)
        job.state, job.phase, job.lease_token = "running", "inspecting", token
        job.lease_until, job.deadline_at = now + timedelta(seconds=30), now + timedelta(seconds=budget)
        job.revision += 1
        output.state, output.writer_fence, output.lease_until = "writing", token, job.lease_until
        output.write_deadline, output.updated_at = job.deadline_at, now
        version.validation_state = "validating"
        version.revision += 1
        # Explicit JSON snapshots avoid expired SQLAlchemy objects crossing the transaction.
        return {
            "lease": build_writer_lease(job.lease_until, job.deadline_at, now, 0.0),
            "job": job.model_dump(),
            "app": app.model_dump(),
            "version": version.model_dump(),
            "output": output.model_dump(),
            "source": source.model_dump(),
            "token": token,
            "budget": budget,
        }

    async def fence(self, work, *, heartbeat=False):
        async with self.repository.sessions() as session, session.begin():
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            job = await session.get(Job, work["job"]["id"], with_for_update=heartbeat)
            app = await session.get(App, work["app"]["id"])
            version = await session.get(Version, work["version"]["id"])
            if (
                job is None
                or job.state != "running"
                or job.lease_token != work["token"]
                or app is None
                or app.lifecycle_state != "active"
                or version is None
                or version.configuration_revision != job.configuration_revision
                or not security_clear(version)
            ):
                raise canceled()
            if job.lease_until <= now or job.deadline_at <= now:
                raise expired()
            if heartbeat:
                job.lease_until = min(now + timedelta(seconds=30), job.deadline_at)
                output = await session.get(Attempt, job.output_attempt_id, with_for_update=True)
                if (
                    output.writer_fence != work["token"]
                    or output.state != "writing"
                    or output.provider != self.storage.provider
                    or not self.storage.owns_namespace(output.namespace)
                ):
                    raise canceled()
                output.lease_until, output.updated_at = job.lease_until, now
            lease_until, deadline_at = job.lease_until, job.deadline_at
        return build_writer_lease(lease_until, deadline_at, now, self.monotonic())

    async def heartbeat(self, work, task):
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                lease = await self.fence(work, heartbeat=True)
                if work.get("writer") is not None:
                    work["writer"].renew(lease)
            except BaseException:
                task.cancel()
                raise

    async def run_one(self):
        # Claim only while the validator can take work: during an outage queued uploads wait unharmed.
        if not await self.validator.ready():
            return False
        work = await self.claim()
        if work is None:
            return False
        process_task = asyncio.create_task(self.process(work))
        monitor = asyncio.create_task(self.heartbeat(work, process_task))
        try:
            async with asyncio.timeout(work["budget"]):
                await process_task
        except HostedApplicationError as exc:
            if exc.code in NOT_ACCEPTED_CODES:
                await self.requeue(work)
                return False
            await self.fail(work, exc.code)
        except OSError:  # TimeoutError is an OSError
            await self.fail(work, "VALIDATION_UNAVAILABLE")
        except asyncio.CancelledError:
            await asyncio.shield(self.fail(work, "VALIDATION_UNAVAILABLE"))
            if asyncio.current_task().cancelling():
                raise
        except Exception:
            await self.fail(work, "VALIDATION_UNAVAILABLE")
            raise
        finally:
            monitor.cancel()
            with suppress(asyncio.CancelledError, HostedApplicationError):
                await monitor
        return True

    async def process(self, work):
        try:
            await self._process(work)
        finally:
            # The heartbeat must never renew a writer whose context has exited.
            work["writer"] = None

    async def _process(self, work):
        source, output, version = work["source"], work["output"], work["version"]
        async with self.storage.writer(
            output["prefix"],
            byte_limit=output["byte_upper_bound"],
            object_limit=output["object_limit"],
            lease=work["lease"],
        ) as writer:
            work["writer"] = writer
            # Recheck the primary after obtaining the physical writer lock, before any write.
            await self.fence(work)
            async with self.validator.inspect(
                self.storage.read_range(source["prefix"] + "/original", 0, source["confirmed_bytes"] - 1),
                size=source["confirmed_bytes"],
                digest=source["receipt_digest"],
                kind=version["artifact_kind"],
            ) as (identifier, candidate, scanner):
                chosen = configuration(candidate, version["requested_configuration"])
                candidate_data = canonical_json(candidate.to_dict())
                candidate_digest = hashlib.sha256(candidate_data).digest()
                report = {
                    "version": 1,
                    "findings": [
                        {"code": item.code, "path": item.path, "message": item.message} for item in candidate.warnings
                    ],
                    "roots": candidate.roots,
                    "entries": candidate.entries,
                    "candidate_digest": candidate_digest.hex(),
                    "scanner": scanner,
                }
                report_data = canonical_json(report)
                if chosen is None:
                    # Several sites or entry pages: the owner chooses one before anything is published.
                    await writer.put("report.json", bytes_chunk(report_data), expected_size=len(report_data))
                else:
                    manifest_data = await self._write_output(work, writer, identifier, candidate, report_data, chosen)
        # The receipt sets writer_quiesced_at, so it runs only after the writer context has exited: its
        # uploads are completed or aborted and its fence is deleted. The lease still fences cleanup until then.
        work["writer"] = None
        if chosen is None:
            await self.await_configuration(work, candidate, candidate_digest, report_data, scanner)
            return
        await self.accept(
            work,
            candidate,
            candidate_digest,
            report_data,
            scanner,
            chosen,
            hashlib.sha256(manifest_data).digest(),
            len(manifest_data),
        )

    async def _write_output(self, work, writer, identifier, candidate, report_data, chosen):
        """Write the report, every validated file and the manifest; returns the manifest bytes."""
        output = work["output"]
        root, entry, mode = chosen
        manifest = build_manifest(
            candidate, prefix=output["prefix"], site_root=root, entrypoint=entry, routing_mode=mode
        )
        manifest_data = canonical_json(manifest)
        manifest_digest = hashlib.sha256(manifest_data).digest()
        parse_manifest(manifest_data, expected_digest=manifest_digest, prefix=output["prefix"])
        if candidate.total_bytes + len(report_data) + len(manifest_data) > output["byte_upper_bound"]:
            # Decided before any file moves: this upload can never fit its reservation.
            raise HostedApplicationError("VALIDATION_LIMIT_EXCEEDED", "The validation output exceeds its reservation")
        await writer.put("report.json", bytes_chunk(report_data), expected_size=len(report_data))
        for index, item in enumerate(candidate.files):
            stored = await writer.put(
                "assets/" + item.path,
                self.validator.file_chunks(identifier, index, item),
                expected_size=item.size,
            )
            # The digest of the bytes on disk is the one integrity check of each transferred file.
            if stored.digest.hex() != item.sha256:
                raise HostedApplicationError("VALIDATION_UNAVAILABLE", "Validated file integrity mismatch", 503)
        await writer.put("manifest.json", bytes_chunk(manifest_data), expected_size=len(manifest_data))
        return manifest_data

    async def locked_result(self, session, work):
        now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
        state = await session.get(UserState, work["output"]["quota_principal_id"], with_for_update=True)
        app = await session.get(App, work["app"]["id"], with_for_update=True)
        version = await session.get(Version, work["version"]["id"], with_for_update=True)
        job = await session.get(Job, work["job"]["id"], with_for_update=True)
        output = await session.get(Attempt, work["output"]["id"], with_for_update=True)
        source = await session.get(Attempt, version.source_attempt_id)
        if (
            job.state != "running"
            or job.lease_token != work["token"]
            or output.writer_fence != work["token"]
            or output.state != "writing"
            or output.provider != self.storage.provider
            or not self.storage.owns_namespace(output.namespace)
            or app.lifecycle_state != "active"
            or not security_clear(version)
            or version.configuration_revision != job.configuration_revision
            or source.state != "stored"
            or source.provider != self.storage.provider
            or not self.storage.owns_namespace(source.namespace)
            or source.receipt_digest != job.source_digest
            or job.policy_version != POLICY_VERSION
            or job.policy_definition_digest != POLICY_DIGEST
        ):
            raise canceled()
        if job.lease_until <= now or job.deadline_at <= now:
            raise expired()
        return now, state, app, version, job, output

    def receipt(self, state, output, now, size, digest):
        if size > output.byte_upper_bound or size > output.held_bytes:
            raise HostedApplicationError("STORAGE_UNAVAILABLE", "Validation output exceeds its reservation", 503)
        state.reserved_bytes -= output.held_bytes
        state.stored_bytes += size
        state.revision += 1
        state.updated_at = now
        output.held_bytes, output.confirmed_bytes, output.receipt_digest = 0, size, digest
        output.state, output.writer_quiesced_at, output.updated_at = "stored", now, now

    async def await_configuration(self, work, candidate, digest, report, scanner):
        async with self.repository.sessions() as session, session.begin():
            now, state, _, version, job, output = await self.locked_result(session, work)
            self.receipt(state, output, now, len(report), hashlib.sha256(report).digest())
            version.validation_state, version.candidate_manifest_digest = "awaiting_configuration", digest
            version.revision += 1
            job.state, job.phase, job.completed_at = "completed", "awaiting_configuration", now
            job.configuration_digest = hashlib.sha256(canonical_json(version.requested_configuration)).digest()
            job.scanner_version, job.scanner_signature_version = scanner["version"], scanner["signature_version"]
            job.report_key, job.report_digest = output.prefix + "/report.json", hashlib.sha256(report).digest()
            job.safe_details = {
                **job.safe_details,
                "candidate_digest": digest.hex(),
                "root_count": len(candidate.roots),
                "entry_count": len(candidate.entries),
                "file_count": len(candidate.files),
                "expanded_bytes": candidate.total_bytes,
            }
            job.revision += 1

    async def accept(self, work, candidate, candidate_digest, report, scanner, chosen, manifest_digest, manifest_size):
        async with self.repository.sessions() as session, session.begin():
            now, state, app, version, job, output = await self.locked_result(session, work)
            size = candidate.total_bytes + len(report) + manifest_size
            self.receipt(state, output, now, size, manifest_digest)
            root, entry, mode = chosen
            job.state, job.phase, job.completed_at = "passed", "accepted", now
            job.configuration_digest = hashlib.sha256(canonical_json(version.requested_configuration)).digest()
            job.manifest_digest = manifest_digest
            job.scanner_version, job.scanner_signature_version = scanner["version"], scanner["signature_version"]
            job.report_key, job.report_digest = output.prefix + "/report.json", hashlib.sha256(report).digest()
            job.safe_details = {
                **job.safe_details,
                "candidate_digest": candidate_digest.hex(),
                "file_count": len(candidate.files),
                "expanded_bytes": candidate.total_bytes,
            }
            version.validation_state, version.accepted_attempt_id, version.accepted_job_id = (
                "accepted",
                output.id,
                job.id,
            )
            version.manifest_key, version.manifest_digest = output.prefix + "/manifest.json", manifest_digest
            version.site_root, version.entrypoint_path, version.routing_mode = root, entry, mode
            version.candidate_manifest_digest, version.accepted_at = candidate_digest, now
            version.expanded_bytes, version.file_count = candidate.total_bytes, len(candidate.files)
            version.revision += 1
            if app.selected_version_id is None:
                app.selected_version_id = version.id
                app.artifact_kind = version.artifact_kind
                app.revision += 1
                app.updated_at = now
            session.add(
                Operation(
                    id=uuid4(),
                    actor_kind="system",
                    app_id=app.id,
                    target_id=str(version.id),
                    action="version.accept",
                    state="completed",
                    response_status=200,
                    audit_details={"job_id": str(job.id), "policy_version": job.policy_version},
                    created_at=now,
                    completed_at=now,
                )
            )

    async def requeue(self, work):
        """The validator never accepted this job: it waits in the queue again, with no failure and no retry used."""
        async with self.repository.sessions() as session, session.begin():
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            version = await session.get(Version, work["version"]["id"], with_for_update=True)
            job = await session.get(Job, work["job"]["id"], with_for_update=True)
            if job is None or job.state != "running" or job.lease_token != work["token"]:
                return
            output = await session.get(Attempt, job.output_attempt_id, with_for_update=True)
            if output is None or output.state != "writing" or output.writer_fence != work["token"]:
                return
            job.state, job.phase, job.available_at = "ready", "queued", now + timedelta(seconds=REQUEUE_SECONDS)
            job.lease_token = job.lease_until = job.deadline_at = None
            job.revision += 1
            # Nothing was written for this job; a fresh fence keeps the abandoned claim from ever writing.
            output.state, output.writer_fence, output.lease_until = "planned", uuid4(), None
            output.revision, output.updated_at = output.revision + 1, now
            if version.validation_state == "validating":
                version.validation_state = "queued"
                version.revision += 1

    async def fail(self, work, code):
        """End this attempt. Artifact findings block the version; exceeded limits only reject it; else retry."""
        from codemie.service.hosted_applications.observability import hosted_request_monitoring

        rejected = code in ARTIFACT_REJECTION_CODES or code in LIMIT_REJECTION_CODES
        hosted_request_monitoring().validation(rejected)
        async with self.repository.sessions() as session, session.begin():
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            await session.get(UserState, work["output"]["quota_principal_id"], with_for_update=True)
            app = await session.get(App, work["app"]["id"], with_for_update=True)
            version = await session.get(Version, work["version"]["id"], with_for_update=True)
            job = await session.get(Job, work["job"]["id"], with_for_update=True)
            if job is None or job.state != "running" or job.lease_token != work["token"]:
                return
            output = await session.get(Attempt, job.output_attempt_id, with_for_update=True)
            job.state, job.phase, job.completed_at = "rejected" if rejected else "failed", "validation_failed", now
            job.safe_details, job.revision = {**job.safe_details, "error_code": code}, job.revision + 1
            job.available_at = now + retry_delay(job)
            session.add(
                Operation(
                    id=uuid4(),
                    actor_kind="system",
                    actor_id="hosted-validation",
                    app_id=app.id,
                    target_id=str(job.id),
                    action="version.validation.rejected" if rejected else "version.validation.failed",
                    state="completed",
                    response_status=200,
                    audit_details={
                        "job_id": str(job.id),
                        "error_code": code,
                        "validation_status": 422 if rejected else 503,
                    },
                    created_at=now,
                    completed_at=now,
                )
            )
            version.validation_state = "rejected" if rejected else "failed"
            version.revision += 1
            output.state, output.writer_quiesced_at, output.updated_at = "delete_requested", now, now
            if code in ARTIFACT_REJECTION_CODES:
                # A security finding blocks this version only. It was never servable, so the application's
                # live version and the sessions of its current viewers are left untouched.
                version.security_state = "blocked"
