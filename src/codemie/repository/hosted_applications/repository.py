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

"""Scoped primary-database transactions and mandatory audit/idempotency records."""

from __future__ import annotations

import hashlib
import hmac
import re
from contextlib import asynccontextmanager
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import Select, Text, bindparam, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from codemie.core.ability import Action
from codemie.core.hosted_applications.access import manageable
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.identity import VerifiedIdentityContext
from codemie.core.hosted_applications.protocol import canonical_json
from codemie.rest_api.models.hosted_application import (
    HostedApplicationOperations,
    HostedApplicationUserState,
    HostedApplicationViewSessions,
    HostedApplications,
)


OPERATION_KEY_INDEX = "hosted_application_operation_key"


def not_found():
    return HostedApplicationError("NOT_FOUND", "Application not found", 404)


LOCK_NOT_AVAILABLE = "55P03"


def is_lock_timeout(exc: BaseException) -> bool:
    """A PostgreSQL ``lock_not_available`` error (``lock_timeout`` expired), as raised through SQLAlchemy."""
    orig = getattr(exc, "orig", None)
    return (getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)) == LOCK_NOT_AVAILABLE


async def set_lock_timeout(session, milliseconds: int) -> None:
    """``SET LOCAL lock_timeout``: bound every lock wait for the rest of the session's transaction."""
    await session.execute(text(f"SET LOCAL lock_timeout = '{int(milliseconds)}ms'"))


def as_uuid(value):
    try:
        return value if isinstance(value, UUID) else UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise not_found() from exc


class HostedTransaction:
    """One primary transaction of a platform actor.

    ``identity`` is the hosted sign-in recorded on the request (None when none was), and ``quota`` is the quota row
    that this transaction locked: the app creator's after ``managed_app(lock=True)``, or ``lock_quota``'s.
    """

    def __init__(self, session, actor_id, identity, now, repository):
        self.session, self.actor_id, self.identity = session, actor_id, identity
        self.now, self.repository, self.secret = now, repository, repository.secret
        self.quota = None

    async def lock_quota(self, principal_id):
        """Create the principal's quota row if needed, lock it FOR UPDATE and apply the configured limit."""
        await self.session.execute(self.repository.quota_row(principal_id, self.now))
        state = (
            await self.session.execute(
                select(HostedApplicationUserState)
                .where(HostedApplicationUserState.principal_id == principal_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        self.repository.sync_quota(state, self.now)
        self.quota = state
        return state

    async def read_quota(self, principal_id):
        """The principal's quota row without a lock, created and brought to the configured limit if needed."""
        await self.session.execute(self.repository.quota_row(principal_id, self.now))
        state = (
            await self.session.execute(
                select(HostedApplicationUserState).where(HostedApplicationUserState.principal_id == principal_id)
            )
        ).scalar_one()
        if state.byte_limit != self.repository.quota_bytes:
            state = await self.repository._sync_unlocked_quota(self.session, state, self.now)
        return state

    async def managed_app(self, app_id, user, *, lock=False, states=("active",), action=Action.WRITE):
        """The app when ``user`` manages it (creator, project admin, platform admin) for ``action``, else 404.

        ``action`` is WRITE, or DELETE on the delete path (``access.manageable``).

        With ``lock`` the creator's quota row is locked as ``tx.quota`` before the app row (FOR NO KEY UPDATE),
        the hosted lock order, and access is checked again on the locked row. The caller's own row is not locked.
        """
        manager = bindparam("manager_id", user.id, type_=Text)
        query = select(HostedApplications).where(
            HostedApplications.id == as_uuid(app_id),
            HostedApplications.lifecycle_state.in_(states),
            manageable(HostedApplications.__table__, manager, action),
        )
        app = (await self.session.execute(query)).scalar_one_or_none()
        if app is None:
            raise not_found()
        if not lock:
            return app
        await self.lock_quota(app.created_by.id)
        app = await self.lock_app(query)
        if app is None:
            raise not_found()
        return app

    async def set_lock_timeout(self, milliseconds: int) -> None:
        """Bound every lock wait for the rest of this transaction (``SET LOCAL lock_timeout``)."""
        await set_lock_timeout(self.session, milliseconds)

    async def lock_app(self, query: Select) -> HostedApplications | None:
        """Run the caller's whole select with the app row locked and refreshed; None when it selects no row.

        Never raises: each caller answers an empty result with its own error. The quota row is not locked here.
        """
        # FOR NO KEY UPDATE: management edits never change the key, so concurrent launch tickets and
        # audit rows (whose foreign keys take KEY SHARE on this row) do not queue behind them.
        locked = query.with_for_update(key_share=True).execution_options(populate_existing=True)
        return (await self.session.execute(locked)).scalar_one_or_none()

    async def operation(self, action, target, payload, key=None, *, app_id=None):
        key_digest = None
        if key is not None:
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{16,128}", key):
                raise HostedApplicationError("INVALID_IDEMPOTENCY_KEY", "A valid idempotency key is required", 422)
            key_digest = hmac.new(self.secret, b"hosted-idempotency-v1\n" + key.encode(), hashlib.sha256).digest()
        digest = hashlib.sha256(canonical_json(payload)).digest()
        if key_digest:
            previous = await self._previous(action, target, key_digest)
            if previous:
                return previous, self._replay(previous, digest)
        operation = HostedApplicationOperations(
            id=uuid4(),
            actor_id=self.actor_id,
            actor_kind="user",
            app_id=app_id,
            target_id=str(target),
            action=action,
            key_digest=key_digest,
            request_digest=digest if key_digest else None,
            state="pending",
            replay_expires_at=self.now + timedelta(hours=24) if key_digest else None,
            audit_details={"action": action},
            created_at=self.now,
        )
        if not key_digest:
            self.session.add(operation)
            await self.session.flush()
            return operation, None
        try:
            # A savepoint, so a concurrent request with the same key leaves this transaction usable.
            async with self.session.begin_nested():
                self.session.add(operation)
                await self.session.flush()
        except IntegrityError as exc:
            if OPERATION_KEY_INDEX not in str(exc.orig):
                raise
            previous = await self._previous(action, target, key_digest)
            if previous is None:
                raise
            return previous, self._replay(previous, digest)
        return operation, None

    async def _previous(self, action, target, key_digest):
        query = (
            select(HostedApplicationOperations)
            .where(
                HostedApplicationOperations.actor_id == self.actor_id,
                HostedApplicationOperations.action == action,
                HostedApplicationOperations.target_id == str(target),
                HostedApplicationOperations.key_digest == key_digest,
            )
            .with_for_update()
        )
        return (await self.session.execute(query)).scalar_one_or_none()

    def _replay(self, previous, digest):
        if previous.request_digest != digest:
            raise HostedApplicationError(
                "IDEMPOTENCY_CONFLICT", "This request key was used for a different change", 409
            )
        if previous.replay_expires_at <= self.now or previous.replay_body is None:
            raise HostedApplicationError("IDEMPOTENCY_EXPIRED", "Use a new request key for this change", 409)
        return previous.replay_body

    async def finish(self, operation, response, status=200, *, details=None):
        operation.state = "completed"
        operation.completed_at = self.now
        operation.response_status = status
        operation.replay_body = response if operation.key_digest else None
        operation.audit_details = {"action": operation.action, **(details or {})}
        await self.session.flush()
        return response


class HostedApplicationRepository:
    def __init__(self, sessions, *, secret: bytes, quota_bytes: int):
        self.sessions, self.secret, self.quota_bytes = sessions, secret, quota_bytes

    def sync_quota(self, state, now):
        """Apply the configured quota to a row locked FOR UPDATE; there is no per-user override."""
        if state.byte_limit != self.quota_bytes:
            state.byte_limit = self.quota_bytes
            state.revision += 1
            state.updated_at = now

    async def _sync_unlocked_quota(self, session, state, now):
        # Read transactions hold no row lock: an atomic guarded UPDATE cannot lose a concurrent revision.
        await session.execute(
            update(HostedApplicationUserState)
            .where(
                HostedApplicationUserState.principal_id == state.principal_id,
                HostedApplicationUserState.byte_limit != self.quota_bytes,
            )
            .values(byte_limit=self.quota_bytes, revision=HostedApplicationUserState.revision + 1, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        return (
            await session.execute(
                select(HostedApplicationUserState)
                .where(
                    HostedApplicationUserState.principal_id == state.principal_id,
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one()

    def quota_row(self, principal_id, now):
        """Insert the principal's quota row unless it exists."""
        return (
            insert(HostedApplicationUserState)
            .values(
                principal_id=principal_id,
                byte_limit=self.quota_bytes,
                stored_bytes=0,
                reserved_bytes=0,
                revision=1,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["principal_id"])
        )

    @asynccontextmanager
    async def transaction(
        self, user, recorded_identity=None, *, write=False, authorized_until=None, lock_timeout_ms=None
    ):
        """One primary transaction keyed by the platform actor ``user.id``; it locks no quota row by itself.

        ``recorded_identity`` is the hosted sign-in recorded on the request. When it belongs to ``user``, the
        logout cutoff and revoked sign-ins apply to it; a write without one is refused. ``authorized_until`` lets
        work authorized while the sign-in was valid (an upload in progress) outlive the token's expiry until
        that deadline. ``lock_timeout_ms`` bounds every lock wait of the transaction and is its first statement,
        before the transaction's own clock and sign-in reads.
        """
        identity = recorded_identity
        if not isinstance(identity, VerifiedIdentityContext) or identity.principal_id != user.id:
            identity = None
        if write and identity is None:
            raise HostedApplicationError("AUTHENTICATION_REQUIRED", "A supported CodeMie login is required", 401)
        async with self.sessions() as session, session.begin():
            if lock_timeout_ms is not None:
                await set_lock_timeout(session, lock_timeout_ms)
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            if identity is not None:
                await self._check_sign_in(session, identity, now, authorized_until)
            yield HostedTransaction(session, user.id, identity, now, self)

    async def _check_sign_in(self, session, identity, now, authorized_until):
        if identity.expires_at <= now and (authorized_until is None or authorized_until <= now):
            raise HostedApplicationError("AUTHENTICATION_REQUIRED", "Sign in to CodeMie to continue", 401)
        # Read without a lock: the caller's quota row is locked only when the caller's own app is charged.
        state = (
            await session.execute(
                select(HostedApplicationUserState).where(
                    HostedApplicationUserState.principal_id == identity.principal_id
                )
            )
        ).scalar_one_or_none()
        cutoff = state.auth_revoked_before if state is not None else None
        if cutoff and now <= cutoff:
            raise HostedApplicationError(
                "AUTHENTICATION_FRESHNESS_REQUIRED", "Wait five seconds, then sign in to CodeMie again", 401
            )
        if cutoff and identity.issued_at <= cutoff:
            raise HostedApplicationError("AUTHENTICATION_REQUIRED", "A fresh CodeMie login is required", 401)
        revoked = (
            await session.execute(
                select(HostedApplicationViewSessions.id)
                .where(
                    HostedApplicationViewSessions.kind == "auth_context",
                    HostedApplicationViewSessions.principal_id == identity.principal_id,
                    HostedApplicationViewSessions.auth_binding_digest == identity.binding_digest(self.secret),
                    HostedApplicationViewSessions.revoked_at.is_not(None),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if revoked:
            raise HostedApplicationError("AUTHENTICATION_REQUIRED", "A fresh CodeMie login is required", 401)
