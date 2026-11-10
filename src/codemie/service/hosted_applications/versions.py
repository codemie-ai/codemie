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

"""Owner-only version controls, safe validation reports, and fresh verification attempts."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from sqlalchemy import select, func

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.policy import POLICY_VERSION, POLICY_DIGEST
from codemie.core.hosted_applications.protocol import check_etag, etag, encode_cursor, decode_cursor
from codemie.rest_api.models.hosted_application import (
    HostedApplicationJobs as Job,
    HostedApplicationVersions as Version,
    HostedApplicationStorageAttempts as Attempt,
)
from codemie.core.hosted_applications.validation import ValidationLimits
from codemie.service.hosted_applications.uploads import new_attempt, expansion_bound, REPORT_HEADROOM
from codemie.service.hosted_applications.worker import RETRYABLE_ERROR_CODES

_LIMITS = ValidationLimits()
_MIB = 1024 * 1024
_TOO_LARGE = (
    f"The upload is larger than the limit: {_LIMITS.max_html_bytes // _MIB} MiB for HTML, "
    f"{_LIMITS.max_zip_bytes // _MIB} MiB for a ZIP"
)
_UNSAFE_PATH = (
    "The ZIP contains an unsafe path, such as ../, an absolute path or a backslash; zip your build folder again"
)
_EXPANSION = (
    f"The ZIP is empty or too large when unpacked: at most {_LIMITS.max_file_bytes // _MIB} MiB per file, "
    f"{_LIMITS.max_expanded_bytes // _MIB} MiB in total, and no file compressed more than {_LIMITS.max_ratio} times"
)
# Rejections report a stable code plus fixed guidance. The guidance never quotes paths, file
# contents or scanner output, so it cannot echo a detected secret back to anyone.
ERROR_MESSAGES = {
    "VALIDATION_CANCELED": "Validation stopped because this version changed; validate it again",
    "MALWARE_DETECTED": "The malware scanner flagged this upload; upload a clean file",
    "SECRET_DETECTED": (
        "A credential such as a private key or an access token was found; remove it, revoke it, and upload again"
    ),
    "UNSUPPORTED_ARTIFACT": (
        "Only browser build files are allowed; remove server code, source maps, node_modules, package files, "
        "hidden files and unsupported file types"
    ),
    "INVALID_ENCODING": "A text file is not valid UTF-8; save it as UTF-8 and upload again",
    "UNSAFE_SVG": "An SVG file is malformed or uses DOCTYPE or entity declarations; export it again as plain SVG",
    "INVALID_HTML": "The file is not an HTML document; upload an .html file or a ZIP of your build",
    "UNSUPPORTED_ARCHIVE": "The ZIP contains links, device files or encrypted entries; zip it again without them",
    "ARCHIVE_PATH_COLLISION": "Two files in the ZIP have the same path when letter case is ignored; rename one",
    "ARCHIVE_FILE_LIMIT": f"The ZIP contains more than {_LIMITS.max_files:,} files; remove files the app does not use",
    "UPLOAD_TOO_LARGE": _TOO_LARGE,
    "ARTIFACT_TOO_LARGE": _TOO_LARGE,
    "ENTRYPOINT_REQUIRED": "No HTML page was found; include an index.html",
    "INVALID_ARCHIVE": "The ZIP is damaged or in an unsupported format; create it again with a standard ZIP tool",
    "INVALID_PATH": _UNSAFE_PATH,
    "INVALID_ARCHIVE_PATH": _UNSAFE_PATH,
    "ARCHIVE_EXPANSION_LIMIT": _EXPANSION,
    "ZIP_BOMB": _EXPANSION,
    "SCAN_LIMIT_EXCEEDED": (
        "The malware scanner could not scan the whole upload within its size, depth or time limits; "
        "upload a smaller or simpler bundle"
    ),
    "VALIDATION_LIMIT_EXCEEDED": (
        "The upload is too large or complex to validate (for example too many findings or very long paths); "
        "simplify the bundle and upload it again"
    ),
}


def error_message(code):
    if code in ERROR_MESSAGES:
        return ERROR_MESSAGES[code]
    if code in RETRYABLE_ERROR_CODES:
        return "Validation could not finish because of a temporary platform condition; retry later"
    return "This artifact did not pass validation"


class VersionServiceMixin:
    async def _latest_job(self, tx, version, *, lock=False):
        query = (
            select(Job)
            .where(
                Job.app_id == version.app_id,
                Job.version_id == version.id,
                Job.kind == "validate",
            )
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(1)
        )
        if lock:
            query = query.with_for_update()
        job = (await tx.session.execute(query)).scalar_one_or_none()
        if job is None:
            raise HostedApplicationError("VALIDATION_UNAVAILABLE", "Validation details are unavailable", 503)
        return job

    async def list_versions(self, identity, app_id, *, limit=20, cursor=None):
        await self.directory.require(identity)
        if not 1 <= limit <= 100:
            raise HostedApplicationError("INVALID_FILTER", "Invalid version list")
        scope = {
            "actor": identity.principal_id,
            "app": str(app_id),
            "kind": "versions",
        }
        async with self.repository.transaction(*identity) as tx:
            app = await tx.managed_app(app_id, identity.user)
            query = select(Version).where(Version.app_id == app.id)
            if cursor:
                position = decode_cursor(cursor, scope, self.repository.secret)
                if type(position.get("sequence")) is not int:
                    raise HostedApplicationError("INVALID_CURSOR", "Invalid version list")
                query = query.where(Version.sequence < position["sequence"])
            rows = (await tx.session.execute(query.order_by(Version.sequence.desc()).limit(limit + 1))).scalars().all()
            page = rows[:limit]
            attempts = {
                item.id: item
                for item in (
                    await tx.session.execute(
                        select(Attempt).where(
                            Attempt.app_id == app.id,
                            Attempt.id.in_([item.source_attempt_id for item in page]),
                        )
                    )
                )
                .scalars()
                .all()
            }
            latest = (
                select(
                    Job.id,
                    func.row_number()
                    .over(partition_by=Job.version_id, order_by=(Job.created_at.desc(), Job.id.desc()))
                    .label('position'),
                )
                .where(
                    Job.app_id == app.id,
                    Job.version_id.in_([item.id for item in page]),
                    Job.kind == "validate",
                )
                .subquery()
            )
            jobs = {
                item.version_id: item
                for item in (
                    await tx.session.execute(
                        select(Job).join(latest, latest.c.id == Job.id).where(latest.c.position == 1)
                    )
                )
                .scalars()
                .all()
            }
            targets = [(app, item.id) for item in page]
            # One code policy permits both preview and activation of an eligible version.
            eligible = await self._eligible_versions(tx, targets, keyed_by_version=True)
            items = [
                self._version_projection(
                    item,
                    attempts.get(item.source_attempt_id),
                    jobs.get(item.id),
                    app=app,
                    eligible=item.id in eligible,
                )
                for item in page
            ]
            return {
                "items": items,
                "next_cursor": encode_cursor(scope, {"sequence": rows[limit - 1].sequence}, self.repository.secret)
                if len(rows) > limit
                else None,
            }

    async def validation_report(self, identity, app_id, version_id, *, offset=0, limit=100):
        await self.directory.require(identity)
        if not 0 <= offset <= 10000 or not 1 <= limit <= 100:
            raise HostedApplicationError("INVALID_FILTER", "Invalid validation report page")
        async with self.repository.transaction(*identity) as tx:
            app = await tx.managed_app(app_id, identity.user)
            version = await self._owned_version(tx, app, version_id)
            snapshot = await self._validation_snapshot(tx, version)
        return await self._read_validation_report(snapshot, offset=offset, limit=limit)

    async def _validation_snapshot(self, tx, version):
        job = await self._latest_job(tx, version)
        return (
            job.model_dump(),
            version.validation_state,
            version.candidate_manifest_digest,
            version.configuration_revision,
        )

    async def _read_validation_report(self, snapshot, *, offset=0, limit=100):
        if not 0 <= offset <= 10000 or not 1 <= limit <= 100:
            raise HostedApplicationError("INVALID_FILTER", "Invalid validation report page")
        record, state, candidate_digest, configuration_revision = snapshot
        report = None
        if record["report_key"]:
            data = await self.storage.read_small(record["report_key"], maximum=REPORT_HEADROOM)
            if hashlib.sha256(data).digest() != record["report_digest"]:
                raise HostedApplicationError("VALIDATION_UNAVAILABLE", "Validation report integrity failed", 503)
            try:
                report = json.loads(data)
            except (ValueError, TypeError) as exc:
                raise HostedApplicationError("VALIDATION_UNAVAILABLE", "Validation report is unavailable", 503) from exc
        findings = report.get("findings", []) if report else []
        return {
            "state": state,
            "job_id": str(record["id"]),
            "phase": record["phase"],
            "candidate_manifest_digest": candidate_digest.hex() if candidate_digest else None,
            "roots": report.get("roots", []) if report else [],
            "entries": report.get("entries", []) if report else [],
            "warnings": findings[offset : offset + limit],
            "errors": [
                {
                    "code": record["safe_details"]["error_code"],
                    "message": error_message(record["safe_details"]["error_code"]),
                }
            ]
            if record["safe_details"].get("error_code")
            else [],
            "configuration_revision": configuration_revision,
            "file_count": record["safe_details"].get("file_count", 0),
            "expanded_bytes": record["safe_details"].get("expanded_bytes", 0),
            "next_offset": offset + limit if offset + limit < len(findings) else None,
            "error_code": record["safe_details"].get("error_code"),
            "scanner": report.get("scanner") if report else None,
            "policy_version": record["policy_version"],
            "etag": etag("validation", record["id"], record["revision"]),
        }

    async def _queue_verification(self, tx, app, version, previous, *, retry=False):
        await self._validation_capacity(tx)
        source = await self._attempt(tx, version, lock=True)
        if source.state != "stored" or source.receipt_digest != version.artifact_digest:
            raise HostedApplicationError(
                "UPLOAD_UNAVAILABLE", "Upload a new artifact; the original is unavailable", 409
            )
        reserve = expansion_bound(version) + REPORT_HEADROOM
        self._reserve(tx, reserve)
        output = new_attempt(
            tx,
            app,
            version,
            kind="validation",
            upper_bound=reserve,
            held_bytes=reserve,
            provider=self.storage.provider,
            namespace=self.storage_namespace,
        )
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
            previous_job_id=previous.id,
            configuration_revision=version.configuration_revision,
            source_digest=source.receipt_digest,
            policy_version=POLICY_VERSION,
            policy_definition_digest=POLICY_DIGEST,
            revision=1,
            safe_details={"retry_count": previous.safe_details.get("retry_count", 0) + 1 if retry else 0},
            available_at=tx.now,
            created_at=tx.now,
        )
        tx.session.add(job)
        version.validation_state, version.revision = "queued", version.revision + 1
        return {"job_id": str(job.id), "state": "queued", "version_etag": etag("version", version.id, version.revision)}

    async def configure_version(self, identity, app_id, version_id, request, if_match, key):
        await self.directory.require(identity)
        options = await self.validation_report(identity, app_id, version_id)
        async with self.repository.transaction(*identity, write=True) as tx:
            app = await tx.managed_app(app_id, identity.user, lock=True)
            version = await self._owned_version(tx, app, version_id, lock=True)
            job = await self._latest_job(tx, version, lock=True)
            op, replay = await tx.operation(
                "version.configure",
                version.id,
                {"request": request.model_dump(mode="json"), "if_match": if_match},
                key,
                app_id=app.id,
            )
            if replay is not None:
                return replay
            check_etag(if_match, "version", version.id, version.revision)
            if (
                version.validation_state != "awaiting_configuration"
                or job.state != "completed"
                or request.configuration_revision != version.configuration_revision
                or request.candidate_manifest_digest
                != (version.candidate_manifest_digest.hex() if version.candidate_manifest_digest else None)
                or str(job.id) != options["job_id"]
                or request.site_root not in options["roots"]
                or request.entrypoint_path not in options["entries"]
                or (request.site_root and not request.entrypoint_path.startswith(request.site_root + "/"))
            ):
                raise HostedApplicationError("CONFIGURATION_CONFLICT", "Reload the detected configuration options", 409)
            version.requested_configuration = {
                "site_root_hint": request.site_root,
                "entrypoint_hint": request.entrypoint_path,
                "routing_mode": request.routing_mode,
            }
            version.configuration_revision += 1
            return await tx.finish(op, await self._queue_verification(tx, app, version, job), 202)

    async def version_action(self, identity, app_id, version_id, action, if_match, key):
        await self.directory.require(identity)
        async with self.repository.transaction(*identity, write=True) as tx:
            app = await tx.managed_app(app_id, identity.user, lock=True)
            version = await self._owned_version(tx, app, version_id, lock=True)
            op, replay = await tx.operation("version." + action, version.id, {"if_match": if_match}, key, app_id=app.id)
            if replay is not None:
                return replay
            check_etag(if_match, "version", version.id, version.revision)
            if action == "retry":
                job = await self._latest_job(tx, version, lock=True)
                if (
                    version.validation_state != "failed"
                    or job.state != "failed"
                    or job.safe_details.get("retry_count", 0) >= 3
                ):
                    raise HostedApplicationError("VALIDATION_IMMUTABLE", "Upload a new artifact to continue", 409)
                return await tx.finish(op, await self._queue_verification(tx, app, version, job, retry=True), 202)
            if action != "cancel" or version.validation_state in ("accepted", "canceled", "rejected"):
                raise HostedApplicationError("VALIDATION_IMMUTABLE", "This version cannot be canceled", 409)
            jobs = (
                (
                    await tx.session.execute(
                        select(Job)
                        .where(
                            Job.app_id == app.id,
                            Job.version_id == version.id,
                            Job.state.in_(("ready", "running")),
                        )
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            for job in jobs:
                job.state, job.completed_at, job.phase, job.revision = "canceled", tx.now, "canceled", job.revision + 1
                job.lease_token = uuid4()
            attempts = (
                (
                    await tx.session.execute(
                        select(Attempt)
                        .where(
                            Attempt.app_id == app.id,
                            Attempt.version_id == version.id,
                            Attempt.state != "deleted",
                        )
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            for attempt in attempts:
                attempt.state, attempt.writer_fence, attempt.updated_at = "delete_requested", uuid4(), tx.now
            version.upload_state, version.validation_state = "canceled", "canceled"
            version.revision += 1
            version.upload_revision += 1
            # Its prefixes are delete_requested: the cleanup loop removes them and only then refunds them.
            return await tx.finish(
                op, {"state": "canceled", "version_etag": etag("version", version.id, version.revision)}, 202
            )
