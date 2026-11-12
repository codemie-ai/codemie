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

"""Owner identity for runs that start without a request in IDP-claims mode (EPMCDME-16625 D7, D8).

IDP-claims mode (ENABLE_USER_MANAGEMENT=False) rebuilds users from JWT claims and keeps no users rows. Scheduled
and webhook runs and their loopback calls have no token, so without this they would run as a bare user id: no
memberships, no default project and no personal identity. Login stores the claims; those runs read them back.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Optional

from cachetools import TTLCache

from codemie.configs import config, logger
from codemie.rest_api.models.user_management import IdpUserSnapshot
from codemie.rest_api.security.user import User

# A user's claims rarely change: write once per pod per hour, or as soon as they differ.
_REFRESH_SECONDS = 3600
_written: TTLCache = TTLCache(maxsize=50_000, ttl=_REFRESH_SECONDS)


# Rows past the identity retention are never read again; delete them at most once per pod per hour.
_PRUNE_SECONDS = 3600
_last_prune: float | None = None


def _claims(user: User) -> tuple:
    return (
        user.username,
        user.email or None,
        user.name or None,
        tuple(sorted(user.project_names or [])),
    )


def user_from_snapshot(snapshot: IdpUserSnapshot) -> User:
    """The user as their last login's claims showed them; budget attribution derives the default, as at login.

    Memberships only: project-admin rights are never replayed from a snapshot (EPMCDME-16625 AR-5).
    """
    project_names = list(snapshot.project_names or [])
    return User(
        id=snapshot.id,
        username=snapshot.username,
        name=snapshot.name or "",
        email=snapshot.email or "",
        project_names=project_names,
        admin_project_names=[],
    )


def current_user_from_snapshot(snapshot: IdpUserSnapshot | None) -> Optional[User]:
    """The owner as their last login showed them, or None when there is no snapshot to use (a bare user id).

    Claims are refreshed by every login (hourly at most). A user who stopped logging in may have lost access, so
    after IDP_USER_SNAPSHOT_MAX_AGE_HOURS their memberships are no longer trusted, but their identity still is:
    their runs are then charged to their own personal budget, not to a customer named after the id (EPMCDME-16625
    AR-1 of the final review). After IDP_USER_SNAPSHOT_IDENTITY_RETENTION_DAYS the row is pruned.
    """
    if snapshot is None:
        return None
    seen = snapshot.update_date or snapshot.date
    if seen is None:
        return None
    age = datetime.now(UTC).replace(tzinfo=None) - seen
    if age > _identity_retention():
        return None
    if age > timedelta(hours=config.IDP_USER_SNAPSHOT_MAX_AGE_HOURS):
        logger.info(f"idp_user_snapshot_memberships_expired: user_id={snapshot.id!r} last_seen={seen}")
        return _identity_only(snapshot)
    return user_from_snapshot(snapshot)


def _identity_only(snapshot: IdpUserSnapshot) -> User:
    return User(
        id=snapshot.id,
        username=snapshot.username,
        name=snapshot.name or "",
        email=snapshot.email or "",
        project_names=[],
        admin_project_names=[],
    )


def _identity_retention() -> timedelta:
    # Never shorter than the membership trust window: a trusted snapshot must still exist.
    return max(
        timedelta(days=config.IDP_USER_SNAPSHOT_IDENTITY_RETENTION_DAYS),
        timedelta(hours=config.IDP_USER_SNAPSHOT_MAX_AGE_HOURS),
    )


class IdpUserSnapshotService:
    async def remember(self, user: User) -> None:
        """Store the user's claims; never fails authentication."""
        if not user.id or not user.username:
            return
        claims = _claims(user)
        if _written.get(user.id) == claims:
            return
        from codemie.clients.postgres import get_async_session
        from codemie.repository.idp_user_snapshot_repository import idp_user_snapshot_repository

        username, email, name, project_names = claims
        try:
            async with get_async_session() as session:
                # Project-admin claims are not stored: snapshots never grant them (AR-5).
                await idp_user_snapshot_repository.aupsert(
                    session,
                    user_id=user.id,
                    username=username,
                    email=email,
                    name=name,
                    project_names=list(project_names),
                    admin_project_names=[],
                )
                await session.commit()
            _written[user.id] = claims
        except Exception as e:
            logger.warning(f"idp_user_snapshot_write_failed: user_id={user.id!r} error={e}")
            return
        await self._prune_if_due()

    async def _prune_if_due(self) -> None:
        """Delete snapshots past the identity retention, at most hourly per pod (EPMCDME-16625 AR-9).

        Such rows are never trusted again, so keeping them only keeps personal data. Never fails the login.
        """
        global _last_prune
        now = time.monotonic()
        if _last_prune is not None and now - _last_prune < _PRUNE_SECONDS:
            return
        _last_prune = now
        from codemie.clients.postgres import get_async_session
        from codemie.repository.idp_user_snapshot_repository import idp_user_snapshot_repository

        cutoff = datetime.now(UTC).replace(tzinfo=None) - _identity_retention()
        try:
            async with get_async_session() as session:
                deleted = await idp_user_snapshot_repository.adelete_last_seen_before(session, cutoff)
                await session.commit()
            logger.info(f"idp_user_snapshot_pruned: deleted={deleted} cutoff={cutoff.isoformat()}")
        except Exception as e:
            logger.warning(f"idp_user_snapshot_prune_failed: error={e}")


idp_user_snapshot_service = IdpUserSnapshotService()
