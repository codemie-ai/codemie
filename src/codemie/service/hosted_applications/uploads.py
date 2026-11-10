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

"""Staged versions and reservations: every physical write has a durable prefix intent."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select

from codemie.core.hosted_applications.access import refuses
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.policy import POLICY_DIGEST, POLICY_VERSION
from codemie.core.hosted_applications.protocol import check_etag, etag
from codemie.repository.hosted_applications.repository import as_uuid, not_found
from codemie.rest_api.models.hosted_application import (
    HostedApplicationJobs as Job,
    HostedApplicationVersions as Version,
    HostedApplicationStorageAttempts as Attempt,
    HostedApplications as App,
)
from codemie.service.hosted_applications.projections import timestamp

REPORT_HEADROOM = 6 * 1024 * 1024
# Versions one application may keep (retention removes old ones); uploads one owner may have open at once;
# resets of one upload. They bound rows and storage churn without a rate limiter.
MAX_RETAINED_VERSIONS = 100
MAX_OPEN_UPLOADS = 4
MAX_UPLOAD_RESETS = 10
# The storage prefix is rooted at the application: nothing reads an owner back from a key.


def expansion_bound(version):
    return version.expected_bytes if version.artifact_kind == "html" else 500 * 1024 * 1024


def attempt_prefix(app_id, version_id, attempt_id):
    return f"a-{app_id.hex}/{app_id.hex}/{version_id.hex}/{attempt_id.hex}"


def new_attempt(tx, app, version, *, kind, upper_bound, held_bytes, provider, namespace):
    attempt_id = uuid4()
    return Attempt(
        id=attempt_id,
        quota_principal_id=tx.quota.principal_id,
        app_id=app.id,
        version_id=version.id,
        kind=kind,
        provider=provider,
        namespace=namespace,
        prefix=attempt_prefix(app.id, version.id, attempt_id),
        writer_id=uuid4(),
        writer_fence=uuid4(),
        upload_generation=version.upload_generation if kind == "upload" else None,
        revision=1,
        state="planned",
        write_deadline=version.upload_expires_at,
        byte_upper_bound=upper_bound,
        object_limit=1 if kind == "upload" else 5002,
        held_bytes=held_bytes,
        confirmed_bytes=0,
        created_at=tx.now,
        updated_at=tx.now,
    )


class UploadServiceMixin:
    storage = None
    _storage_namespace = "hosted-apps"

    @property
    def storage_namespace(self):
        return self.storage.bind_namespace(self._storage_namespace)

    @storage_namespace.setter
    def storage_namespace(self, value):
        self._storage_namespace = value

    async def _owned_version(self, tx, app, version_id, *, lock=False):
        query = select(Version).where(Version.app_id == app.id, Version.id == as_uuid(version_id))
        if lock:
            query = query.with_for_update()
        version = (await tx.session.execute(query)).scalar_one_or_none()
        if version is None:
            raise not_found()
        return version

    async def _attempt(self, tx, version, *, lock=False):
        query = select(Attempt).where(
            Attempt.app_id == version.app_id,
            Attempt.version_id == version.id,
            Attempt.id == version.source_attempt_id,
        )
        if lock:
            query = query.with_for_update()
        result = (await tx.session.execute(query)).scalar_one_or_none()
        if result is None:
            raise HostedApplicationError("UPLOAD_UNAVAILABLE", "The upload is unavailable", 503)
        if result.provider != self.storage.provider or not self.storage.owns_namespace(result.namespace):
            raise HostedApplicationError(
                "STORAGE_UNAVAILABLE", "This version belongs to another private storage volume", 503
            )
        return result

    @staticmethod
    def _upload_live(tx, app, version):
        if version.upload_expires_at <= tx.now:
            raise HostedApplicationError("UPLOAD_EXPIRED", "This upload expired; create a new version", 409)
        if version.validation_state != "uploading" or version.upload_state in ("canceled", "expired", "completed"):
            raise HostedApplicationError("UPLOAD_IMMUTABLE", "This upload cannot be changed; create a new version", 409)

    @staticmethod
    def _reserve(tx, amount):
        if tx.quota.stored_bytes + tx.quota.reserved_bytes + amount > tx.quota.byte_limit:
            raise HostedApplicationError("QUOTA_EXCEEDED", "Not enough application storage is available", 409)
        tx.quota.reserved_bytes += amount
        tx.quota.revision += 1
        tx.quota.updated_at = tx.now

    @staticmethod
    def _upload_projection(version, attempt):
        return {
            "upload_id": str(version.upload_id),
            "generation": version.upload_generation,
            "state": version.upload_state,
            "etag": etag("upload", version.upload_id, version.upload_revision),
            "expires_at": timestamp(version.upload_expires_at),
            "received_bytes": attempt.confirmed_bytes,
            "sha256": attempt.receipt_digest.hex() if attempt.receipt_digest else None,
            "can_reset": version.validation_state == "uploading" and version.upload_state in ("created", "receiving"),
        }

    @classmethod
    def _begin_projection(cls, app, version, attempt):
        return {
            "version_id": str(version.id),
            "sequence": version.sequence,
            "upload_id": str(version.upload_id),
            "generation": version.upload_generation,
            "expires_at": timestamp(version.upload_expires_at),
            "upload_path": f"v1/hosted-applications/{app.id}/versions/{version.id}/artifact",
            "upload_etag": etag("upload", version.upload_id, version.upload_revision),
            "version_etag": etag("version", version.id, version.revision),
            "app_etag": etag("app", app.id, app.revision),
            "limits": {"byte_size": version.expected_bytes, "expanded_bytes": expansion_bound(version)},
        }

    async def begin_version(self, identity, app_id, request, if_match, key):
        await self.directory.require(identity)
        if self.storage is None:
            raise HostedApplicationError("STORAGE_UNAVAILABLE", "Application storage is unavailable", 503)
        async with self.repository.transaction(*identity, write=True) as tx:
            app = await tx.managed_app(app_id, identity.user, lock=True)
            op, replay = await tx.operation(
                "version.create",
                app.id,
                {"request": request.model_dump(mode="json"), "if_match": if_match},
                key,
                app_id=app.id,
            )
            if replay is not None:
                await self._owned_version(tx, app, replay["version_id"])
                return replay
            check_etag(if_match, "app", app.id, app.revision)
            # The creator's quota row is locked for this transaction, so concurrent starts cannot overshoot.
            open_uploads = (
                await tx.session.execute(
                    select(func.count(Version.id))
                    .join(App, (App.id == Version.app_id))
                    .where(
                        App.created_by["id"].as_string() == tx.quota.principal_id,
                        Version.validation_state == "uploading",
                    )
                )
            ).scalar_one()
            if open_uploads >= MAX_OPEN_UPLOADS:
                raise HostedApplicationError(
                    "UPLOAD_CONCURRENCY_LIMIT",
                    f"You can have up to {MAX_OPEN_UPLOADS} uploads in progress; finish or cancel one first",
                    409,
                )
            sequence = (
                await tx.session.execute(
                    select(func.coalesce(func.max(Version.sequence), 0)).where(Version.app_id == app.id)
                )
            ).scalar_one() + 1
            retained = (
                await tx.session.execute(select(func.count(Version.id)).where(Version.app_id == app.id))
            ).scalar_one()
            if retained >= MAX_RETAINED_VERSIONS or sequence >= 2147483647:
                raise HostedApplicationError(
                    "VERSION_LIMIT", "Remove retained versions before creating another version", 409
                )
            version = Version(
                id=uuid4(),
                app_id=app.id,
                sequence=sequence,
                original_filename=request.filename,
                artifact_kind=request.artifact_kind,
                expected_bytes=request.byte_size,
                expected_digest=bytes.fromhex(request.sha256) if request.sha256 else None,
                upload_id=uuid4(),
                upload_generation=1,
                upload_revision=1,
                upload_state="created",
                upload_expires_at=tx.now + timedelta(hours=24),
                requested_configuration={
                    "routing_mode": request.routing_mode,
                    "site_root_hint": request.site_root_hint,
                    "entrypoint_hint": request.entrypoint_hint,
                },
                configuration_revision=1,
                validation_state="uploading",
                security_state="clear",
                expanded_bytes=0,
                file_count=0,
                revision=1,
                created_at=tx.now,
            )
            reserve = request.byte_size + expansion_bound(version) + REPORT_HEADROOM
            self._reserve(tx, reserve)
            tx.session.add(version)
            await tx.session.flush()
            attempt = new_attempt(
                tx,
                app,
                version,
                kind="upload",
                upper_bound=request.byte_size,
                held_bytes=reserve,
                provider=self.storage.provider,
                namespace=self.storage_namespace,
            )
            tx.session.add(attempt)
            await tx.session.flush()
            version.source_attempt_id = attempt.id
            if app.selected_version_id is None:
                app.artifact_kind = request.artifact_kind
            app.revision += 1
            app.updated_at = tx.now
            return await tx.finish(
                op,
                self._begin_projection(app, version, attempt),
                201,
                details={"version_id": str(version.id), "reserved_bytes": reserve},
            )

    async def get_version(self, identity, app_id, version_id):
        await self.directory.require(identity)
        async with self.repository.transaction(*identity) as tx:
            app = await tx.managed_app(app_id, identity.user)
            version = await self._owned_version(tx, app, version_id)
            attempt = await self._attempt(tx, version)
            # One code policy permits both preview and activation of an eligible version.
            eligible = await self._eligible_version(tx, app, version.id) is not None
            job = (
                await tx.session.execute(
                    select(Job)
                    .where(
                        Job.app_id == app.id,
                        Job.version_id == version.id,
                        Job.kind == "validate",
                    )
                    .order_by(Job.created_at.desc(), Job.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            return self._version_projection(version, attempt, job, app=app, eligible=eligible)

    def _version_projection(self, version, attempt, job, *, app, eligible):
        """The one definition of ``can_activate``: an eligible version, unless a suspension refuses activation."""
        can_activate = eligible and not refuses(app, "activate")
        return {
            "id": str(version.id),
            "sequence": version.sequence,
            "filename": version.original_filename,
            "artifact_kind": version.artifact_kind,
            "expected_bytes": version.expected_bytes,
            "validation_state": version.validation_state,
            "security_state": version.security_state,
            "can_preview": eligible,
            "can_activate": can_activate,
            "revision": version.revision,
            "etag": etag("version", version.id, version.revision),
            "configuration_revision": version.configuration_revision,
            "requested_configuration": version.requested_configuration,
            "candidate_manifest_digest": version.candidate_manifest_digest.hex()
            if version.candidate_manifest_digest
            else None,
            "upload": self._upload_projection(version, attempt),
            "created_at": timestamp(version.created_at),
            "job": {
                "id": str(job.id),
                "state": job.state,
                "phase": job.phase,
                "details": job.safe_details,
                "etag": etag("validation", job.id, job.revision),
            }
            if job
            else None,
            "accepted": {
                "entrypoint_path": version.entrypoint_path,
                "site_root": version.site_root,
                "routing_mode": version.routing_mode,
                "file_count": version.file_count,
                "expanded_bytes": version.expanded_bytes,
                "accepted_at": timestamp(version.accepted_at),
            }
            if version.validation_state == "accepted"
            else None,
        }

    async def reset_upload(self, identity, app_id, version_id, request, if_match, key):
        await self.directory.require(identity)
        async with self.repository.transaction(*identity, write=True) as tx:
            app = await tx.managed_app(app_id, identity.user, lock=True)
            version = await self._owned_version(tx, app, version_id, lock=True)
            old = await self._attempt(tx, version, lock=True)
            op, replay = await tx.operation(
                "upload.reset",
                version.id,
                {"request": request.model_dump(mode="json"), "if_match": if_match},
                key,
                app_id=app.id,
            )
            if replay is not None:
                return replay
            check_etag(if_match, "upload", version.upload_id, version.upload_revision)
            self._upload_live(tx, app, version)
            if request.generation != version.upload_generation:
                raise HostedApplicationError("UPLOAD_GENERATION_CONFLICT", "Reload the upload before retrying", 409)
            if version.upload_state == "stored":
                raise HostedApplicationError("UPLOAD_IMMUTABLE", "Stored artifacts cannot be reset", 409)
            if old.lease_until and old.lease_until > tx.now:
                raise HostedApplicationError("UPLOAD_IN_PROGRESS", "The previous upload is still active", 409)
            # Generation 1 is the first upload; each reset adds one.
            if version.upload_generation > MAX_UPLOAD_RESETS:
                raise HostedApplicationError(
                    "UPLOAD_RESET_LIMIT", "This upload was restarted too many times; create a new version", 409
                )
            reserve = version.expected_bytes + expansion_bound(version) + REPORT_HEADROOM
            # An old writer can only create the original object up to its byte_upper_bound.
            # Transfer only unused output headroom; uncertain original bytes remain charged.
            old_remaining = max(0, old.byte_upper_bound - old.confirmed_bytes)
            transferable = max(0, old.held_bytes - old_remaining)
            if tx.quota.stored_bytes + tx.quota.reserved_bytes - transferable + reserve > tx.quota.byte_limit:
                raise HostedApplicationError(
                    "CLEANUP_PENDING", "Wait for the previous upload to finish cleanup before retrying", 409
                )
            old.held_bytes -= transferable
            tx.quota.reserved_bytes -= transferable
            self._reserve(tx, reserve)
            old.state, old.writer_fence, old.updated_at = "delete_requested", uuid4(), tx.now
            old.revision += 1
            version.upload_generation += 1
            version.upload_revision += 1
            version.revision += 1
            version.upload_state = "created"
            attempt = new_attempt(
                tx,
                app,
                version,
                kind="upload",
                upper_bound=version.expected_bytes,
                held_bytes=reserve,
                provider=self.storage.provider,
                namespace=self.storage_namespace,
            )
            tx.session.add(attempt)
            await tx.session.flush()
            version.source_attempt_id = attempt.id
            # The old prefix is now delete_requested: the cleanup loop removes it and only then refunds it.
            return await tx.finish(op, self._begin_projection(app, version, attempt))

    async def _validation_capacity(self, tx):
        running = (
            await tx.session.execute(
                select(func.count())
                .select_from(Job)
                .join(App, Job.app_id == App.id)
                .where(
                    App.created_by["id"].as_string() == tx.quota.principal_id,
                    Job.kind == "validate",
                    Job.state.in_(("ready", "running")),
                )
            )
        ).scalar_one()
        if running >= 2:
            raise HostedApplicationError(
                "VALIDATION_CONCURRENCY_LIMIT", "Wait for an existing validation to finish", 409
            )

    async def complete_version(self, identity, app_id, version_id, request, if_match, key):
        await self.directory.require(identity)
        async with self.repository.transaction(*identity, write=True) as tx:
            app = await tx.managed_app(app_id, identity.user, lock=True)
            version = await self._owned_version(tx, app, version_id, lock=True)
            source = await self._attempt(tx, version, lock=True)
            op, replay = await tx.operation(
                "version.complete",
                version.id,
                {"request": request.model_dump(mode="json"), "if_match": if_match},
                key,
                app_id=app.id,
            )
            if replay is not None:
                return replay
            check_etag(if_match, "version", version.id, version.revision)
            self._upload_live(tx, app, version)
            if (
                request.generation != version.upload_generation
                or request.upload_id != version.upload_id
                or version.upload_state != "stored"
                or source.state != "stored"
                or source.receipt_digest is None
                or source.receipt_digest.hex() != request.sha256
            ):
                raise HostedApplicationError("UPLOAD_RECEIPT_MISMATCH", "Reload the completed upload receipt", 409)
            await self._validation_capacity(tx)
            output_bound = expansion_bound(version) + REPORT_HEADROOM
            if source.held_bytes < output_bound:
                raise HostedApplicationError(
                    "STORAGE_UNAVAILABLE", "Validation storage reservation is unavailable", 503
                )
            output = new_attempt(
                tx,
                app,
                version,
                kind="validation",
                upper_bound=output_bound,
                held_bytes=output_bound,
                provider=self.storage.provider,
                namespace=self.storage_namespace,
            )
            source.held_bytes -= output_bound
            tx.session.add(output)
            await tx.session.flush()
            job = Job(
                id=uuid4(),
                app_id=app.id,
                version_id=version.id,
                kind="validate",
                state="ready",
                phase="queued",
                output_attempt_id=output.id,
                configuration_revision=version.configuration_revision,
                source_digest=source.receipt_digest,
                policy_version=POLICY_VERSION,
                policy_definition_digest=POLICY_DIGEST,
                revision=1,
                safe_details={},
                available_at=tx.now,
                created_at=tx.now,
            )
            tx.session.add(job)
            version.upload_state, version.validation_state = "completed", "queued"
            version.upload_revision += 1
            version.revision += 1
            version.artifact_digest = source.receipt_digest
            return await tx.finish(
                op,
                {
                    "job_id": str(job.id),
                    "state": "queued",
                    "version_etag": etag("version", version.id, version.revision),
                    "upload_etag": etag("upload", version.upload_id, version.upload_revision),
                },
                202,
            )
