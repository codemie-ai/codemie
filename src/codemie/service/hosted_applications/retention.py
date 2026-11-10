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

"""Bounded primary retention scheduling; physical absence remains a separate required barrier."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import NamedTuple
from uuid import uuid4
from sqlalchemy import delete, exists, func, or_, select, text, update
from sqlalchemy.orm import aliased
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.rest_api.models.permission import ResourceType, ShareLink
from codemie.rest_api.models.hosted_application import (
    HostedApplicationJobs as Job,
    HostedApplicationVersions as Version,
    HostedApplicationStorageAttempts as Attempt,
    HostedApplicationUserState as State,
    HostedApplications as App,
    HostedApplicationOperations as Operation,
    HostedApplicationViewSessions as Credential,
)


class Actor(NamedTuple):
    """Who an audit record names: a platform user's id with kind ``user``, or the background worker."""

    id: str
    kind: str


SYSTEM_ACTOR = Actor("hosted-retention", "system")


def audit_record(app, now, action, target, actor):
    return Operation(
        id=uuid4(),
        actor_id=actor.id,
        actor_kind=actor.kind,
        app_id=app.id,
        target_id=str(target),
        action=action,
        state="completed",
        response_status=202,
        audit_details={"retention": True},
        created_at=now,
        completed_at=now,
    )


async def retire_version(tx, app, version, actor):
    """Block ``version``, queue the removal of its stored bytes and audit ``actor``.

    The caller holds the creator's quota row and the application row (``managed_app(lock=True)``); this takes
    no lock of its own. A version that is live or selected, or that has a ``ready`` or ``running`` job, is
    refused with 409: it is still in use.
    """
    busy = (
        await tx.session.execute(
            select(Job.id).where(Job.version_id == version.id, Job.state.in_(("ready", "running"))).limit(1)
        )
    ).first()
    if version.id in (app.active_version_id, app.selected_version_id) or busy:
        raise HostedApplicationError("VERSION_IN_USE", "This version is in use and cannot be removed", 409)
    version.validation_state, version.security_state = "canceled", "blocked"
    version.revision += 1
    await tx.session.execute(
        update(Attempt)
        .where(
            Attempt.app_id == app.id,
            Attempt.version_id == version.id,
            Attempt.state != "deleted",
        )
        .values(state="delete_requested", writer_fence=uuid4(), updated_at=tx.now)
    )
    tx.session.add(
        Job(
            id=uuid4(),
            app_id=app.id,
            version_id=version.id,
            kind="cleanup_upload",
            state="ready",
            phase="version_retention",
            safe_details={},
            available_at=tx.now,
            created_at=tx.now,
        )
    )
    tx.session.add(audit_record(app, tx.now, "version.retention", version.id, actor))


class RetentionWorker:
    def __init__(
        self,
        repository,
        *,
        accepted=10,
        rejected_days=30,
        audit_days=180,
        batch_size=1000,
        gc_seconds=10.0,
    ):
        if (
            not 1 <= accepted <= 1000
            or not 1 <= rejected_days <= 3650
            or not 1 <= audit_days <= 3650
            or not 1 <= batch_size <= 10000
            or not 0 < gc_seconds <= 60
        ):
            raise ValueError("Invalid hosted retention policy")
        self.repository, self.accepted, self.rejected_days = repository, accepted, rejected_days
        self.audit_days, self.batch_size, self.gc_seconds = audit_days, batch_size, gc_seconds

    def audit(self, app, now, action, target):
        return audit_record(app, now, action, target, SYSTEM_ACTOR)

    async def schedule_version_one(self):
        async with self.repository.sessions() as session, session.begin():
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            ranking = (
                select(
                    Version.id,
                    func.row_number()
                    .over(partition_by=Version.app_id, order_by=Version.sequence.desc())
                    .label("position"),
                )
                .where(Version.validation_state == "accepted")
                .subquery()
            )
            version = (
                await session.execute(
                    select(Version)
                    .join(App, (App.id == Version.app_id))
                    .outerjoin(ranking, ranking.c.id == Version.id)
                    .where(
                        App.lifecycle_state == "active",
                        or_(App.active_version_id.is_(None), App.active_version_id != Version.id),
                        or_(App.selected_version_id.is_(None), App.selected_version_id != Version.id),
                        or_(
                            ranking.c.position > self.accepted,
                            (Version.validation_state.in_(("failed", "rejected", "canceled")))
                            & (Version.created_at <= now - timedelta(days=self.rejected_days)),
                        ),
                        # Any active job (validation, upload cleanup or retention) owns the version for now; its
                        # (version_id, kind) slot is unique, so scheduling here could only conflict.
                        ~exists(
                            select(Job.id).where(
                                Job.version_id == Version.id,
                                Job.state.in_(("ready", "running")),
                            )
                        ),
                    )
                    .order_by(Version.created_at)
                    .limit(1)
                    .with_for_update(of=Version, skip_locked=True)
                )
            ).scalar_one_or_none()
            if version is None:
                return False
            app = (
                await session.execute(select(App).where(App.id == version.app_id).with_for_update(skip_locked=True))
            ).scalar_one_or_none()
            if (
                app is None
                or app.lifecycle_state != "active"
                or version.id in (app.active_version_id, app.selected_version_id)
            ):
                return False
            state = (
                await session.execute(
                    select(State).where(State.principal_id == app.created_by.id).with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if state is None:
                return False
            try:
                await retire_version(SimpleNamespace(session=session, now=now), app, version, SYSTEM_ACTOR)
            except HostedApplicationError:
                return False
            return True

    async def finish_version_one(self):
        async with self.repository.sessions() as session, session.begin():
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            job = (
                await session.execute(
                    select(Job)
                    .join(App, (App.id == Job.app_id))
                    .where(
                        Job.kind == "cleanup_upload",
                        Job.state == "ready",
                        App.lifecycle_state != "purging",
                        ~exists(
                            select(Attempt.id).where(
                                Attempt.app_id == Job.app_id,
                                Attempt.version_id == Job.version_id,
                                Attempt.state != "deleted",
                                or_(Job.phase == "version_retention", Attempt.state == "delete_requested"),
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
                await session.execute(select(App).where(App.id == job.app_id).with_for_update(skip_locked=True))
            ).scalar_one_or_none()
            if app is None:
                return False
            version = (
                await session.execute(
                    select(Version).where(Version.id == job.version_id).with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if version is None:
                return False
            if job.phase == "version_retention":
                if version.id in (app.active_version_id, app.selected_version_id):
                    return False
                target = version.id
                await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
                await session.execute(
                    delete(Credential).where(Credential.app_id == app.id, Credential.version_id == target)
                )
                await session.execute(delete(Job).where(Job.app_id == app.id, Job.version_id == target))
                await session.execute(delete(Attempt).where(Attempt.app_id == app.id, Attempt.version_id == target))
                await session.execute(delete(Version).where(Version.app_id == app.id, Version.id == target))
                await session.execute(
                    update(Operation)
                    .where(
                        Operation.app_id == app.id,
                        Operation.state == "completed",
                        Operation.target_id == str(target),
                        Operation.replay_body.is_not(None),
                    )
                    .values(replay_body=None)
                )
                receipt = self.audit(app, now, "version.retention.complete", target)
                receipt.response_status, receipt.audit_details = 200, {"retention": True, "absence_verified": True}
                session.add(receipt)
                return True
            job.state, job.phase, job.completed_at = "completed", "absence_verified", now
            job.revision += 1
            return True

    async def gc(self):
        """Ordered, bounded batches per backlog until drained or this cycle's time budget is spent.

        Every backlog gets at least one batch per cycle; each batch is its own short transaction.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.gc_seconds
        changed = False
        # Children before parents: auth contexts are removable only once no ticket/session references them,
        # and a purged application's row only once its audit records are gone.
        for step in (
            self._scrub_replays,
            self._delete_expired_credentials,
            self._delete_unbound_contexts,
            self._prune_audit,
            self._delete_purged_applications,
        ):
            while True:
                count = await step()
                changed = changed or count > 0
                if count < self.batch_size or loop.time() >= deadline:
                    break
        # User logout cutoffs are retained.
        return changed

    async def _batch(self, build):
        async with self.repository.sessions() as session, session.begin():
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            return (await session.execute(build(now))).rowcount

    async def _scrub_replays(self):
        def build(now):
            expired = (
                select(Operation.id)
                .where(Operation.replay_body.is_not(None), Operation.replay_expires_at <= now)
                .order_by(Operation.replay_expires_at)
                .limit(self.batch_size)
                .with_for_update(skip_locked=True)
            )
            return update(Operation).where(Operation.id.in_(expired)).values(replay_body=None)

        return await self._batch(build)

    async def _delete_expired_credentials(self):
        def build(now):
            expired = (
                select(Credential.id)
                .where(Credential.kind != "auth_context", Credential.expires_at <= now - timedelta(seconds=5))
                .order_by(Credential.expires_at)
                .limit(self.batch_size)
                .with_for_update(skip_locked=True)
            )
            return delete(Credential).where(Credential.id.in_(expired))

        return await self._batch(build)

    async def _delete_unbound_contexts(self):
        def build(now):
            child = aliased(Credential)
            contexts = (
                select(Credential.id)
                .where(
                    Credential.kind == "auth_context",
                    Credential.binding_valid_until < now - timedelta(seconds=5),
                    ~exists(select(child.id).where(child.parent_id == Credential.id)),
                )
                .order_by(Credential.binding_valid_until)
                .limit(self.batch_size)
                .with_for_update(skip_locked=True)
            )
            return delete(Credential).where(Credential.id.in_(contexts))

        return await self._batch(build)

    async def _prune_audit(self):
        def build(now):
            stale = (
                select(Operation.id)
                .where(Operation.created_at < now - timedelta(days=self.audit_days))
                .order_by(Operation.created_at)
                .limit(self.batch_size)
                .with_for_update(skip_locked=True)
            )
            return delete(Operation).where(Operation.id.in_(stale))

        return await self._batch(build)

    async def _delete_purged_applications(self):
        """Remove purged applications' tombstones, with their job and share links, once the audit period has passed."""
        async with self.repository.sessions() as session, session.begin():
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            stale = (
                (
                    await session.execute(
                        select(App.id)
                        .where(
                            App.lifecycle_state == "purged",
                            App.purged_at < now - timedelta(days=self.audit_days),
                        )
                        .order_by(App.purged_at)
                        .limit(self.batch_size)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            if not stale:
                return 0
            await session.execute(delete(Operation).where(Operation.app_id.in_(stale)))
            await session.execute(delete(Job).where(Job.app_id.in_(stale)))
            # share_links has no foreign key to the app: its rows go explicitly, before the app row. Their grants
            # were removed (with audit) when the delete began, so the permissions foreign key does not hold them.
            await session.execute(
                delete(ShareLink).where(
                    ShareLink.resource_type == ResourceType.HOSTED_APPLICATION, ShareLink.resource_id.in_(stale)
                )
            )
            return (await session.execute(delete(App).where(App.id.in_(stale)))).rowcount
