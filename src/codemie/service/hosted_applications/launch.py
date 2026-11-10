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

"""Transient normal-login handoff and primary-authorized isolated content sessions."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import NamedTuple
from uuid import uuid4

from sqlalchemy import DateTime, Text, and_, bindparam, case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.core.hosted_applications.identity import ABSOLUTE_AUTH_SECONDS
from codemie.repository.hosted_applications.repository import as_uuid, not_found
from codemie.rest_api.models.hosted_application import (
    HostedApplicationViewSessions as Credential,
    HostedApplicationUserState as State,
    HostedApplications as App,
    HostedApplicationVersions as Version,
    HostedApplicationStorageAttempts as Attempt,
    HostedApplicationOperations as Operation,
)
from codemie.core.hosted_applications.access import manageable, readable
from codemie.service.hosted_applications.projections import timestamp


def access_condition(purpose, user_id_expr):
    """The access module's term for a launch purpose: a preview is for managers, a view for readers."""
    return (manageable if purpose == "owner_preview" else readable)(App.__table__, user_id_expr)


def denied():
    return HostedApplicationError("LAUNCH_EXPIRED", "Return to CodeMie to open this application", 401)


def _renew_context(context, identity, renewed, now):
    """Extend a live auth context for this sign-in; refuse a revoked or expired one."""
    if context.revoked_at:
        raise denied()
    if not identity.expiry_verified:
        # The platform has just accepted this sign-in, so it stays valid like the platform session.
        context.binding_valid_until = max(context.binding_valid_until, renewed)
    if context.binding_valid_until <= now:
        raise denied()
    if identity.sid or not identity.expiry_verified:
        context.expires_at = min(identity.expires_at, context.binding_valid_until)
        context.credential_issued_at = max(context.credential_issued_at, identity.issued_at)
    if context.expires_at <= now:
        raise denied()


class LiveCredential(NamedTuple):
    """Current primary-database state of one ticket or viewing session, checked in one transaction."""

    now: datetime
    credential: Credential
    context: Credential
    app: App
    output: Attempt | None


class HostedApplicationLaunchService:
    def __init__(self, catalog, settings, absolute_auth_seconds=ABSOLUTE_AUTH_SECONDS):
        self.catalog, self.repository = catalog, catalog.repository
        self.settings, self.absolute_auth_seconds = settings, absolute_auth_seconds

    def digest(self, token, kind):
        if not isinstance(token, str) or not re.fullmatch("[A-Za-z0-9_-]{43}", token):
            raise denied()
        return hmac.new(self.settings.secret, ("hosted-" + kind + "-v1\n" + token).encode(), hashlib.sha256).digest()

    def host(self, version_id):
        return self.settings.version_host(version_id)

    async def _context(self, tx):
        identity = tx.identity
        digest = identity.binding_digest(self.settings.secret)
        context = (
            await tx.session.execute(
                select(Credential)
                .where(
                    Credential.kind == "auth_context",
                    Credential.principal_id == identity.principal_id,
                    Credential.auth_binding_digest == digest,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        renewed = tx.now + timedelta(seconds=self.absolute_auth_seconds)
        if context is not None:
            _renew_context(context, identity, renewed, tx.now)
            return context
        signed_in = identity.auth_time if identity.expiry_verified and identity.auth_time else tx.now
        bound = min(signed_in + timedelta(seconds=self.absolute_auth_seconds), renewed)
        expiry = min(identity.expires_at, bound)
        if expiry <= tx.now or identity.issued_at > tx.now + timedelta(seconds=5):
            raise denied()
        context = Credential(
            id=uuid4(),
            kind="auth_context",
            principal_id=identity.principal_id,
            parent_kind="auth_context",
            auth_binding_digest=digest,
            binding_valid_until=bound,
            credential_issued_at=identity.issued_at,
            revision=1,
            created_at=tx.now,
            expires_at=expiry,
        )
        tx.session.add(context)
        await tx.session.flush()
        return context

    async def issue(self, identity, app_id, *, purpose, version_id=None):
        if purpose not in ("owner_preview", "published_view"):
            raise HostedApplicationError("INVALID_LAUNCH", "Invalid application launch")
        async with self.repository.transaction(*identity, write=True) as tx:
            # The caller's quota row serializes this launch with a concurrent sign-out, as before.
            await tx.lock_quota(identity.principal_id)
            # No exclusive lock on the shared app row, so viewer launches never serialize on it (the
            # ticket/audit foreign keys only take KEY SHARE). The ticket records this access revision,
            # which only delete and purge bump; every consume/authorize re-evaluates the access rule itself.
            app = (
                await tx.session.execute(
                    select(App).where(
                        App.id == as_uuid(app_id),
                        App.lifecycle_state == "active",
                        access_condition(purpose, bindparam("viewer_id", identity.principal_id, type_=Text)),
                    )
                )
            ).scalar_one_or_none()
            if app is None:
                raise not_found()
            if purpose == "owner_preview":
                target = as_uuid(version_id) if version_id else app.selected_version_id
            else:
                if app.active_version_id is None:
                    raise HostedApplicationError("NO_ACTIVE_VERSION", "This application has no active version", 409)
                if version_id and as_uuid(version_id) != app.active_version_id:
                    raise not_found()
                target = app.active_version_id
            version = await self.catalog._eligible_version(tx, app, target)
            if version is None:
                raise HostedApplicationError("VERSION_NOT_ACCEPTED", "This version is no longer available", 422)
            context = await self._context(tx)
            token = secrets.token_urlsafe(32)
            expiry = min(tx.now + timedelta(seconds=30), context.expires_at)
            if expiry <= tx.now:
                raise denied()
            tx.session.add(
                Credential(
                    id=uuid4(),
                    kind="ticket",
                    principal_id=identity.principal_id,
                    parent_kind="auth_context",
                    parent_id=context.id,
                    app_id=app.id,
                    version_id=version.id,
                    token_digest=self.digest(token, "ticket"),
                    purpose=purpose,
                    destination_host=self.host(version.id),
                    target_path="/",
                    access_revision=app.access_revision,
                    revision=1,
                    created_at=tx.now,
                    expires_at=expiry,
                )
            )
            tx.session.add(
                Operation(
                    id=uuid4(),
                    actor_id=identity.principal_id,
                    actor_kind="user",
                    app_id=app.id,
                    target_id=str(version.id),
                    action="app.launch",
                    state="completed",
                    response_status=200,
                    audit_details={"purpose": purpose, "authentication_kind": tx.identity.authentication_kind},
                    created_at=tx.now,
                    completed_at=tx.now,
                )
            )
            origin = ("http" if self.settings.local else "https") + "://" + self.host(version.id)
            return {
                "method": "POST",
                "version_id": str(version.id),
                "policy_version": "origin-policy-1",
                "content_origin": origin,
                "action": origin + "/_platform/launch",
                "ticket": token,
                "issued_at": timestamp(tx.now),
                "expires_at": timestamp(expiry),
            }

    async def _load(self, session, token, kind, *, lock=False, version_id=None, host=None):
        """Every credential, login, logout, application and access-rule check in one statement.

        The access rule is the access module's, evaluated for the credential's principal against ``users`` and
        ``user_projects`` in this same statement: ``manageable`` for a preview, ``readable`` for a view.
        """
        context = aliased(Credential)
        allowed = case(
            (Credential.purpose == "owner_preview", access_condition("owner_preview", Credential.principal_id)),
            else_=access_condition("published_view", Credential.principal_id),
        )
        query = (
            select(
                Credential, context, State, App, Attempt, allowed, func.clock_timestamp(type_=DateTime(timezone=True))
            )
            .join(context, context.id == Credential.parent_id)
            .join(App, App.id == Credential.app_id)
            .outerjoin(State, and_(State.principal_id == Credential.principal_id))
            .outerjoin(Version, and_(Version.id == Credential.version_id))
            .outerjoin(Attempt, Attempt.id == Version.accepted_attempt_id)
            .where(Credential.kind == kind, Credential.token_digest == self.digest(token, kind))
        )
        if lock:
            query = query.with_for_update(of=Credential)
        row = (await session.execute(query)).one_or_none()
        if row is None:
            raise denied()
        credential, context, state, app, output, allowed, now = row
        if lock:
            # A one-use ticket can expire while waiting for another consumer's lock.
            now = (await session.execute(select(func.clock_timestamp(type_=DateTime(timezone=True))))).scalar_one()
        if (
            credential.revoked_at
            or credential.expires_at <= now
            or (kind == "ticket" and credential.consumed_at is not None)
            or (version_id is not None and credential.version_id != version_id)
            or (host is not None and (host != credential.destination_host or host != self.host(credential.version_id)))
        ):
            raise denied()
        if (
            context.kind != "auth_context"
            or context.revoked_at
            or context.expires_at <= now
            or context.binding_valid_until <= now
            or state is None
            or (state.auth_revoked_before and context.credential_issued_at <= state.auth_revoked_before)
        ):
            raise denied()
        if (
            not allowed
            or app.lifecycle_state != "active"
            or app.access_revision != credential.access_revision
            or credential.purpose not in ("owner_preview", "published_view")
            or (credential.purpose == "published_view" and app.active_version_id != credential.version_id)
        ):
            raise denied()
        return LiveCredential(now, credential, context, app, output)

    async def _eligible_output(self, session, live):
        """The credential's version must still be eligible under the policy versions this code serves."""
        credential, app = live.credential, live.app
        version = await self.catalog._eligible_version(
            SimpleNamespace(session=session),
            app,
            credential.version_id,
        )
        output = live.output
        if version is None or output is None or output.id != version.accepted_attempt_id or output.state != "stored":
            raise denied()
        return version, output

    async def _authorized(self, token, kind, finish, *, version_id=None, host=None):
        """Run ``finish`` inside the one transaction that performed every live check."""
        async with self.repository.sessions() as session, session.begin():
            live = await self._load(session, token, kind, lock=kind == "ticket", version_id=version_id, host=host)
            version, output = await self._eligible_output(session, live)
            return await finish(session, live, version, output)

    async def consume(self, token, host):
        async def finish(session, live, version, _):
            ticket, context, now = live.credential, live.context, live.now
            value = secrets.token_urlsafe(32)
            expiry = min(now + timedelta(minutes=15), context.expires_at, context.binding_valid_until)
            if expiry <= now:
                raise denied()
            ticket.consumed_at, ticket.revision = now, ticket.revision + 1
            session.add(
                Credential(
                    id=uuid4(),
                    kind="view_session",
                    principal_id=ticket.principal_id,
                    parent_kind="auth_context",
                    parent_id=context.id,
                    app_id=live.app.id,
                    version_id=version.id,
                    token_digest=self.digest(value, "view_session"),
                    purpose=ticket.purpose,
                    target_path=ticket.target_path,
                    access_revision=live.app.access_revision,
                    revision=1,
                    created_at=now,
                    expires_at=expiry,
                )
            )
            return value, ticket.target_path

        return await self._authorized(token, "ticket", finish, host=host)

    async def authorize(self, token, version_id):
        async def finish(_, live, version, output):
            return {
                "app": live.app.model_dump(),
                "version": version.model_dump(),
                "attempt": output.model_dump(),
                "viewer_subject": live.credential.principal_id,
            }

        return await self._authorized(token, "view_session", finish, version_id=version_id)

    async def logout(self, identity):
        if identity is None:
            raise HostedApplicationError("SIGN_OUT_PENDING", "Sign-out is pending; please retry", 503)
        async with self.repository.sessions() as session, session.begin():
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            await session.execute(
                insert(State)
                .values(
                    principal_id=identity.principal_id,
                    byte_limit=self.repository.quota_bytes,
                    stored_bytes=0,
                    reserved_bytes=0,
                    revision=1,
                    updated_at=now,
                )
                .on_conflict_do_nothing(index_elements=["principal_id"])
            )
            state = await session.get(State, identity.principal_id, with_for_update=True)
            if not identity.sid:
                state.auth_revoked_before = max(state.auth_revoked_before or now, now + timedelta(seconds=5))
            state.revision += 1
            state.updated_at = now
            query = update(Credential).where(
                Credential.kind == "auth_context",
                Credential.principal_id == identity.principal_id,
            )
            if identity.sid:
                query = query.where(Credential.auth_binding_digest == identity.binding_digest(self.settings.secret))
            await session.execute(query.values(revoked_at=now))
            # A SID logout before the first launch still creates the retained tombstone.
            if identity.sid:
                existing = (
                    await session.execute(
                        select(Credential.id).where(
                            Credential.kind == "auth_context",
                            Credential.principal_id == identity.principal_id,
                            Credential.auth_binding_digest == identity.binding_digest(self.settings.secret),
                        )
                    )
                ).scalar_one_or_none()
                if existing is None:
                    bound = now + timedelta(seconds=self.absolute_auth_seconds + 5)
                    session.add(
                        Credential(
                            id=uuid4(),
                            kind="auth_context",
                            principal_id=identity.principal_id,
                            parent_kind="auth_context",
                            auth_binding_digest=identity.binding_digest(self.settings.secret),
                            binding_valid_until=bound,
                            credential_issued_at=identity.issued_at,
                            revision=1,
                            created_at=now,
                            expires_at=bound,
                            revoked_at=now,
                        )
                    )
            session.add(
                Operation(
                    id=uuid4(),
                    actor_id=identity.principal_id,
                    actor_kind="user",
                    target_id="sessions",
                    action="sessions.revoke",
                    state="completed",
                    response_status=204,
                    audit_details={},
                    created_at=now,
                    completed_at=now,
                )
            )
