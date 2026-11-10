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

"""Cleanup requires physical writer exclusion and verified prefix absence before reclaiming quota."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import delete, exists, func, select, text, update

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.hosted_application import (
    HostedApplicationJobs as Job,
    HostedApplicationVersions as Version,
    HostedApplicationStorageAttempts as Attempt,
    HostedApplicationUserState as State,
    HostedApplications as App,
    HostedApplicationViewSessions as Credential,
    HostedApplicationOperations as Operation,
)
from codemie.rest_api.models.hosted_application_cover import HostedApplicationCovers as Cover
from codemie.service.hosted_applications.worker import retry_delay

# Cleanup looks past this many candidates, so one owner's locked rows never stall everyone's cleanup.
CLEANUP_CANDIDATES = 5


class MaintenanceWorker:
    def __init__(self, repository, storage):
        self.repository, self.storage = repository, storage

    async def expire(self):
        async with self.repository.sessions() as session, session.begin():
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            jobs = (
                (
                    await session.execute(
                        select(Job)
                        .where(Job.state == "running", Job.kind == "validate", Job.lease_until < now)
                        .order_by(Job.lease_until)
                        .limit(10)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            for job in jobs:
                app = (
                    await session.execute(
                        select(App)
                        .where(App.id == job.app_id)
                        # FOR NO KEY UPDATE: launch tickets and audit rows keep their foreign-key locks on the app.
                        .with_for_update(key_share=True, skip_locked=True)
                    )
                ).scalar_one_or_none()
                if app is None:
                    continue
                version = (
                    await session.execute(
                        select(Version).where(Version.id == job.version_id).with_for_update(skip_locked=True)
                    )
                ).scalar_one_or_none()
                if version is None:
                    continue
                output = await session.get(Attempt, job.output_attempt_id, with_for_update=True)
                job.state, job.completed_at, job.phase = "failed", now, "lease_expired"
                job.safe_details, job.revision = (
                    {**job.safe_details, "error_code": "VALIDATION_UNAVAILABLE"},
                    job.revision + 1,
                )
                job.available_at = now + retry_delay(job)
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
                        audit_details={
                            "job_id": str(job.id),
                            "error_code": "VALIDATION_UNAVAILABLE",
                            "phase": "lease_expired",
                            "validation_status": 503,
                        },
                        created_at=now,
                        completed_at=now,
                    )
                )
                output.state, output.writer_fence, output.updated_at = "delete_requested", uuid4(), now
                # Lease expiry fences acceptance; physical writer proof is still required.
                version.validation_state = "failed"
                version.revision += 1
            uploads = (
                (
                    await session.execute(
                        select(Version)
                        .where(Version.validation_state == "uploading", Version.upload_expires_at <= now)
                        .order_by(Version.upload_expires_at)
                        .limit(10)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            for version in uploads:
                version.upload_state, version.validation_state = "expired", "canceled"
                version.revision += 1
                version.upload_revision += 1
                attempt = await session.get(Attempt, version.source_attempt_id, with_for_update=True)
                attempt.state, attempt.writer_fence, attempt.updated_at = "delete_requested", uuid4(), now

    async def _lockable(self, session, attempt):
        """Whether the attempt's owner and application rows are free now; never waits while holding attempts."""
        state = (
            await session.execute(
                select(State).where(State.principal_id == attempt.quota_principal_id).with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if state is None:
            return False
        app = (
            await session.execute(
                select(App)
                .where(App.id == attempt.app_id)
                # FOR NO KEY UPDATE: launch tickets and audit rows keep their foreign-key locks on the app.
                .with_for_update(key_share=True, skip_locked=True)
            )
        ).scalar_one_or_none()
        return app is not None

    async def cleanup_one(self):
        async with self.repository.sessions() as session, session.begin():
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            candidates = (
                (
                    await session.execute(
                        select(Attempt)
                        .join(App, (App.id == Attempt.app_id))
                        .where(
                            Attempt.state == "delete_requested",
                            Attempt.provider == self.storage.provider,
                            Attempt.namespace.endswith(self.storage.namespace_suffix),
                            (Attempt.lease_until.is_(None))
                            | (
                                Attempt.lease_until
                                + func.make_interval(0, 0, 0, 0, 0, 0, self.storage.cleanup_grace_seconds)
                                <= now
                            )
                            | Attempt.writer_quiesced_at.is_not(None),
                        )
                        .order_by(Attempt.updated_at)
                        .with_for_update(of=Attempt, skip_locked=True)
                        .limit(CLEANUP_CANDIDATES)
                    )
                )
                .scalars()
                .all()
            )
            attempt = None
            for candidate in candidates:
                if await self._lockable(session, candidate):
                    attempt = candidate
                    break
            if attempt is None:
                return False
            token = uuid4()
            attempt.writer_fence, attempt.lease_until = token, now + timedelta(seconds=30)
            work = attempt.model_dump()
        try:
            async with asyncio.timeout(25):
                proof = await self.storage.delete_prefix(work["prefix"])
            if not proof.writer_quiesced or not proof.absence_verified:
                return False
        except (HostedApplicationError, OSError):
            return False
        async with self.repository.sessions() as session, session.begin():
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            state = await session.get(State, work["quota_principal_id"], with_for_update=True)
            # The application row is locked only to keep the global order: state, application, attempt.
            await session.get(App, work["app_id"], with_for_update=True)
            attempt = await session.get(Attempt, work["id"], with_for_update=True)
            if attempt.state != "delete_requested" or attempt.writer_fence != token:
                return False
            state.stored_bytes -= attempt.confirmed_bytes
            state.reserved_bytes -= attempt.held_bytes
            state.updated_at, state.revision = now, state.revision + 1
            attempt.state, attempt.held_bytes, attempt.confirmed_bytes = "deleted", 0, 0
            attempt.writer_quiesced_at, attempt.absence_verified_at, attempt.updated_at = now, now, now
            attempt.revision += 1
        return True

    async def finish_purge_one(self):
        async with self.repository.sessions() as session, session.begin():
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            job = (
                await session.execute(
                    select(Job)
                    .join(App, (App.id == Job.app_id))
                    .where(
                        Job.kind == "purge",
                        Job.state == "ready",
                        App.lifecycle_state == "purging",
                        ~exists(
                            select(Attempt.id).where(
                                Attempt.app_id == App.id,
                                Attempt.state != "deleted",
                            )
                        ),
                    )
                    .order_by(Job.created_at)
                    .limit(1)
                    .with_for_update(of=Job, skip_locked=True)
                )
            ).scalar_one_or_none()
            if job is None:
                return False
            app = (
                await session.execute(
                    select(App).where(App.id == job.app_id).with_for_update(key_share=True, skip_locked=True)
                )
            ).scalar_one_or_none()
            if app is None or app.lifecycle_state != "purging":
                return False
            owned = (Attempt.app_id == app.id,)
            pending = (
                await session.execute(select(Attempt.id).where(*owned, Attempt.state != "deleted").limit(1))
            ).first()
            if pending:
                return False
            # A cover is charged to the app creator like stored files. Its size cannot change while the application
            # row is locked. Never wait for the owner's state lock while holding these locks: retry later.
            cover_bytes = (
                await session.execute(select(Cover.byte_size).where(Cover.app_id == app.id))
            ).scalar_one_or_none()
            state = None
            if cover_bytes is not None:
                state = (
                    await session.execute(
                        select(State).where(State.principal_id == app.created_by.id).with_for_update(skip_locked=True)
                    )
                ).scalar_one_or_none()
                if state is None:
                    return False
            # Delete circular version/job/attempt references only after physical absence proof.
            await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
            await session.execute(delete(Credential).where(Credential.app_id == app.id))
            # The tombstone row stays, so ON DELETE CASCADE never fires: the cover goes with the other rows.
            await session.execute(delete(Cover).where(Cover.app_id == app.id))
            if state is not None:
                state.stored_bytes -= cover_bytes
                state.updated_at, state.revision = now, state.revision + 1
            await session.execute(delete(Job).where(Job.app_id == app.id, Job.id != job.id))
            await session.execute(delete(Attempt).where(*owned))
            await session.execute(delete(Version).where(Version.app_id == app.id))
            await session.execute(
                update(Operation)
                .where(
                    Operation.app_id == app.id,
                    Operation.state == "completed",
                    Operation.replay_body.is_not(None),
                )
                .values(replay_body=None)
            )
            app.name, app.description = None, None
            # Only the creator's id stays, for the audit records that refer to the row; the name goes.
            app.created_by = CreatedByUser(id=app.created_by.id)
            app.search_text, app.artifact_kind = "", None
            app.selected_version_id, app.active_version_id = None, None
            app.lifecycle_state, app.purged_at, app.updated_at = "purged", now, now
            app.revision += 1
            job.state, job.phase, job.completed_at = "completed", "absence_verified", now
            job.revision += 1
            return True
