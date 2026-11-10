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

"""Read-only directory with a short, bounded membership cache.

Names the people behind CodeMie's ``users`` rows. It decides nothing about who may use an app: that is
the access module's, evaluated against ``users`` and ``user_projects`` in the same statement. Lookups are cached
per principal for at most CACHE_SECONDS, storing only the decision and the display name. Errors and
unavailability are never cached.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from time import monotonic

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.repository.hosted_applications.repository import not_found
from codemie.rest_api.models.user_management import UserDB
from codemie.service.hosted_applications.observability import hosted_request_monitoring

MAX_BATCH = 100
# Membership decisions kept per process, and for how long: an account deactivated in CodeMie
# loses access to open applications within CACHE_SECONDS.
CACHE_SECONDS = 5.0
CACHE_ENTRIES = 4096
INELIGIBLE = object()


def _retrieve(task):
    # Every waiter may have been cancelled; never leave an unretrieved task exception behind.
    if not task.cancelled():
        task.exception()


def _unavailable():
    return HostedApplicationError("AUTHORIZATION_UNAVAILABLE", "The identity directory is unavailable", 503)


class HostedIdentityDirectory:
    def __init__(
        self,
        sessions,
        config,
        *,
        clock=time.monotonic,
        cache_seconds=CACHE_SECONDS,
    ):
        self.sessions, self.config = sessions, config
        self.clock = clock
        self.cache_seconds = cache_seconds
        self.cache_entries = CACHE_ENTRIES
        self.memberships = OrderedDict()
        self._pending = {}

    @staticmethod
    def _projection(user):
        return {
            "principal_id": user.id,
            "display_name": (user.name or user.username or "CodeMie user")[:255],
            "email": user.email[:255],
        }

    async def require(self, context):
        return await self.resolve(context.principal_id)

    async def resolve(self, principal):
        """An eligible member's ``principal_id`` and ``display_name``.

        Raises 404 when the principal is not an eligible member and 503 when the directory is
        unavailable. Concurrent misses for one principal share a single live lookup.
        """
        key = principal
        known = self._fresh(key)
        if known is None:
            task = self._pending.get(key)
            if task is None:
                task = asyncio.ensure_future(self._refresh([principal], contact=False))
                self._pending[key] = task
                task.add_done_callback(_retrieve)
                task.add_done_callback(
                    lambda done: self._pending.pop(key, None) if self._pending.get(key) is done else None
                )
            found = await asyncio.shield(task)
            if principal not in found:
                raise not_found()
            return found[principal]
        if not known[0]:
            raise not_found()
        return {"principal_id": principal, "display_name": known[1]}

    async def resolve_many(self, principals, *, contact=False):
        """Eligible members among ``principals``, keyed by principal; ineligible ones are omitted.

        One ``users`` query serves the whole batch. Without ``contact``, cached decisions are reused and only display
        names are returned. With ``contact``, every lookup is live and includes the email address, which
        is never cached.
        """
        wanted = list(dict.fromkeys(principals))
        if len(wanted) > MAX_BATCH:
            raise HostedApplicationError("INVALID_FILTER", "Too many people in one request")
        found, missing = {}, []
        for principal in wanted:
            known = None if contact else self._fresh(principal)
            if known is None:
                missing.append(principal)
            elif known[0]:
                found[principal] = {"principal_id": principal, "display_name": known[1]}
        if missing:
            found |= await self._refresh(missing, contact=contact)
        return found

    def cached(self, principal):
        """Non-blocking probe: a fresh eligible (True) or ineligible (False) decision, else None."""
        known = self._fresh(principal)
        return None if known is None else known[0]

    def _fresh(self, key):
        entry = self.memberships.get(key)
        if entry is None:
            return None
        if entry[0] <= self.clock():
            del self.memberships[key]
            return None
        self.memberships.move_to_end(key)
        return entry[1], entry[2]

    def _remember(self, key, eligible, display_name, observed):
        if self.cache_seconds <= 0:
            return
        expires = observed + self.cache_seconds
        current = self.memberships.get(key)
        if current is not None and current[0] > expires:
            return
        self.memberships[key] = (expires, eligible, display_name)
        self.memberships.move_to_end(key)
        while len(self.memberships) > self.cache_entries:
            self.memberships.popitem(last=False)

    def _local_verdict(self, user):
        """A member's profile, or INELIGIBLE for a missing ``users`` row; who may use an app is the access module's."""
        if user is None:
            return INELIGIBLE
        return self._projection(user)

    async def _refresh(self, principals, *, contact):
        """Live decisions for ``principals``; every definitive outcome refreshes the cache."""
        started, observed, allowed = monotonic(), self.clock(), False
        try:
            try:
                async with self.sessions() as session:
                    rows = (await session.execute(select(UserDB).where(UserDB.id.in_(principals)))).scalars().all()
            except SQLAlchemyError as exc:
                raise _unavailable() from exc
            users = {row.id: row for row in rows}
            verdicts = {principal: self._local_verdict(users.get(principal)) for principal in principals}
            found = {}
            for principal, verdict in verdicts.items():
                eligible = verdict is not INELIGIBLE
                self._remember(principal, eligible, verdict["display_name"] if eligible else None, observed)
                if eligible:
                    found[principal] = (
                        verdict if contact else {key: verdict[key] for key in ("principal_id", "display_name")}
                    )
            allowed = True
            return found
        finally:
            hosted_request_monitoring().directory(monotonic() - started, allowed)
