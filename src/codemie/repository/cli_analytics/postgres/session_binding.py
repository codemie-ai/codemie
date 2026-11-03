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

"""Session binding and the guard on every ingest write (EPMCDME-16440, spec 6, 6b, 6c).

The first accepted evidence request of a session binds it (owner, destination) in `session_dims`, in the same
transaction and under the same per-session advisory lock as its rows. Every later write of the session, legacy
or evidence, is checked against that binding before anything is stored. Pure functions and SQL only: the
ingestor owns the transaction.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
from collections import Counter
from typing import Any, cast

import asyncpg

from codemie.repository.cli_analytics.ports import (
    REASON_OWNER_CONFLICT,
    REASON_PROJECT_CONFLICT,
    REASON_SESSION_BOUNDARY,
    DestinationValidator,
    SessionBinding,
    SessionResolver,
    TelemetryPayloadTooLargeError,
)
from codemie.repository.cli_analytics.postgres.otlp import DecodedBatch
from codemie.repository.cli_analytics.scope import ORIGIN_LEGACY_PROJECT

logger = logging.getLogger(__name__)

RECORD_KINDS = ("logs", "hooks", "usage", "spans", "metrics")
PERSONAL = "personal"
# Session guard discards by reason, since this process started (read by tests and diagnostics).
SESSION_DISCARDS: Counter[str] = Counter()

# Sorted, so two batches sharing sessions take their locks in one order and cannot deadlock.
SESSION_LOCKS_SQL = "SELECT pg_advisory_xact_lock(k) FROM unnest($1::bigint[]) AS k ORDER BY k"

# The binding of each session (owner_email set = bound) and, for an evidence request ($2), what an unbound
# one already has: its legacy project (session_dims project, else its earliest hook row naming one) and the
# emails known for it (retained session_dims jwt_email, every hook row's user_email), lower-cased as the
# user lookups compare them. No known email = an unavailable legacy identity, which a first binding may adopt.
BINDINGS_SQL = """
SELECT s AS session_id, d.owner_email, d.project_name, d.subscription_type, d.project_origin, d.route_note,
    CASE WHEN $2 AND d.owner_email IS NULL THEN coalesce(nullif(d.project_name, ''), (
        SELECT h.codemie_project_name FROM hook_events h WHERE h.session_id = s AND h.codemie_project_name <> ''
        ORDER BY h.ts LIMIT 1)) END AS legacy_project,
    CASE WHEN $2 AND d.owner_email IS NULL THEN ARRAY(
        SELECT DISTINCT lower(btrim(e)) FROM (SELECT d.jwt_email AS e UNION ALL
            SELECT h.user_email FROM hook_events h WHERE h.session_id = s) known
        WHERE btrim(e) <> '') END AS legacy_emails
FROM unnest($1::text[]) AS s LEFT JOIN session_dims d ON d.session_id = s"""

# The first accepted evidence request binds the session; a bound row only gets updated_at (EPMCDME-16440).
_BINDING_COLUMNS = ("owner_email", "subscription_type", "project_origin", "route_note", "project_name", "jwt_email")
_BIND_SQL = f"""
INSERT INTO session_dims AS sd (session_id, {", ".join(_BINDING_COLUMNS)}, updated_at)
SELECT s, $2, $3, $4, $5, p, $2, now() FROM unnest($1::text[], $6::text[]) AS u(s, p)
ORDER BY s
ON CONFLICT (session_id) DO UPDATE
    SET {", ".join(f"{c} = EXCLUDED.{c}" for c in _BINDING_COLUMNS)}, updated_at = now()
    WHERE sd.owner_email IS NULL"""
_TOUCH_BOUND_SQL = "UPDATE session_dims SET updated_at = now() WHERE session_id = ANY($1::text[])"


@dataclasses.dataclass(frozen=True)
class Sender:
    """Who writes and how the destination is decided, as the ingestor hands them to the storage transaction.

    `user_email` is the authenticated sender ('' = unknown). `binding` is the resolved destination of an
    evidence request (None: legacy, or `resolve` decides it under the lock); `validate` checks a destination
    other than `binding`'s; `evidence` marks an evidence request whichever way its destination is decided.
    """

    user_email: str = ""
    binding: SessionBinding | None = None
    validate: DestinationValidator | None = None
    resolve: SessionResolver | None = None
    evidence: bool = False
    # The project a legacy gate accepted the request for (None for an evidence request or no gate): a session already
    # bound elsewhere is never written under another project, whichever signal carries it.
    request_project: str | None = None


class SessionDiscard(Exception):
    """A session guard rejected the whole request; raised inside its transaction, so nothing is written."""

    def __init__(self, reason: str, session_id: str, other_sessions: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.session_id = session_id
        self.other_sessions = other_sessions


def same(a: str | None, b: str | None) -> bool:
    """Project names compare case-insensitively (the gate's rule)."""
    return (a or "").lower() == (b or "").lower()


def same_user(a: str | None, b: str | None) -> bool:
    """Emails identify a user case-insensitively: the user lookups compare `lower(email)` (user_repository)."""
    return bool(a) and bool(b) and a.strip().lower() == b.strip().lower()  # type: ignore[union-attr]


def private_personal(binding: SessionBinding) -> bool:
    """The owner's personal project (origin `personal`); a Personal-paid binding on a work project is not."""
    return binding.subscription_type == PERSONAL and binding.project_origin == PERSONAL


def check_boundary(batch: DecodedBatch, expected: str | None) -> None:
    """One evidence request is one session (spec 6): every row must carry the declared session id."""
    if expected is None:
        return
    seen = {row.session_id for kind in RECORD_KINDS for row in getattr(batch, kind)}
    if seen and seen != {expected} or "" in seen:
        raise SessionDiscard(REASON_SESSION_BOUNDARY, expected, max(len(seen) - 1, 0))


# One advisory lock is taken per distinct session of a request, all inside one transaction (lock_and_read).
# The evidence CLI sends exactly one session per request; this is a conservative engineering bound on a legacy
# or hostile sender, not a measured capacity limit of the database (that also depends on the lock table size,
# concurrent requests and the connection pool).
MAX_SESSIONS_PER_REQUEST = 100


def check_session_count(batch: DecodedBatch) -> None:
    """Refuse a request over MAX_SESSIONS_PER_REQUEST distinct sessions before any lock, SQL or ledger entry.

    Duplicates of a session count once; the check runs at the public ingest entry, so the isolation retries
    of `_store` (which only ever hold fewer sessions) cannot bypass it.
    """
    if len({row.session_id for kind in RECORD_KINDS for row in getattr(batch, kind) if row.session_id}) > (
        MAX_SESSIONS_PER_REQUEST
    ):
        raise TelemetryPayloadTooLargeError("Too many distinct sessions in one request")


def batch_sessions(batch: DecodedBatch) -> list[str]:
    return sorted({row.session_id for kind in RECORD_KINDS for row in getattr(batch, kind) if row.session_id})


def stored_binding(row: Any) -> SessionBinding | None:
    """The binding a `BINDINGS_SQL` row carries, None for an unbound session."""
    if not row or not row["owner_email"]:
        return None
    return SessionBinding(
        row["project_name"] or "", row["subscription_type"] or "", row["project_origin"] or "", row["route_note"] or ""
    )


def check_owner(sessions: list[str], dims: dict[str, Any], user_email: str) -> None:
    """Every ingest, legacy or evidence: a bound session is written only by its owner. A missing sender is
    never the owner, so it is discarded, not skipped (the sender is compared as the user lookups do).
    An unbound session whose legacy rows name a user is adopted only by that user (an evidence request
    reads `legacy_emails`); with no known email it is adopted by anyone."""
    for s in sessions:
        d = dims.get(s) or {}
        owner = d.get("owner_email")
        known = [owner] if owner else d.get("legacy_emails") or []
        if known and not any(same_user(e, user_email) for e in known):
            raise SessionDiscard(REASON_OWNER_CONFLICT, s, len(sessions) - 1)


def _with_client_fields(stored: SessionBinding, binding: SessionBinding | None) -> SessionBinding:
    """An evidence request records what the client sent next to the stored binding; a legacy one adds nothing."""
    if binding is None:
        return stored
    # dataclasses.replace builds an instance of the argument's own class; the checker only infers DataclassInstance.
    return cast(
        SessionBinding,
        dataclasses.replace(
            stored,
            client_subscription_type=binding.client_subscription_type,
            client_policy_version=binding.client_policy_version,
        ),
    )


def _resolve_session(
    session_id: str, d: dict[str, Any], binding: SessionBinding | None, session_count: int
) -> tuple[SessionBinding, bool] | None:
    """The effective binding of one session and whether it is already bound; None for a legacy request
    on an unbound session (unchanged). Raises SessionDiscard when a Personal request meets another project."""
    conflict = SessionDiscard(REASON_PROJECT_CONFLICT, session_id, session_count - 1)
    if (stored := stored_binding(d)) is not None:
        if binding is not None and private_personal(binding) and not same(stored.project_name, binding.project_name):
            raise conflict  # Personal never joins a shared session
        return _with_client_fields(stored, binding), True
    if binding is None:
        return None
    legacy = d.get("legacy_project")
    stored = binding
    if legacy and private_personal(binding):
        if not same(legacy, binding.project_name):
            raise conflict
        # The owner's own personal project is the legacy anchor: the private origin stays, never a shared one.
    elif legacy:
        # All but private Personal join the legacy project, whether or not the candidate names it: the candidate's
        # origin and note describe a route the legacy rows do not prove, so every classification reads
        # `legacy_project` (approved AC8; a Personal-paid one is never private by omission), as for a legacy
        # adoption in the gate.
        stored = cast(
            SessionBinding,
            dataclasses.replace(binding, project_name=legacy, project_origin=ORIGIN_LEGACY_PROJECT, route_note=""),
        )
    return _with_client_fields(stored, binding), False


def _check_legacy_hooks(batch: DecodedBatch, effective: dict[str, SessionBinding], session_count: int) -> None:
    """Legacy hook rows of a bound session: their effective project must be empty or the bound one."""
    for h in batch.hooks:
        target = effective.get(h.session_id)
        sent = (h.codemie_project_name or "").strip()
        if target is not None and sent and not same(h.codemie_project_name, target.project_name):
            raise SessionDiscard(REASON_PROJECT_CONFLICT, h.session_id, session_count - 1)


def _check_legacy_request_project(
    bound: list[str], effective: dict[str, SessionBinding], request_project: str | None, session_count: int
) -> None:
    """A legacy request is accepted for one project (the gate's canonical name): it never writes a session that is
    bound to another one, on any signal (OTLP logs, metrics and traces carry no project of their own)."""
    if not request_project:
        return
    for s in bound:
        if not same(effective[s].project_name, request_project):
            raise SessionDiscard(REASON_PROJECT_CONFLICT, s, session_count - 1)


def guard(
    batch: DecodedBatch,
    sessions: list[str],
    dims: dict[str, Any],
    user_email: str,
    binding: SessionBinding | None,
    request_project: str | None = None,
) -> tuple[dict[str, SessionBinding], list[str], list[str]]:
    """Spec 6b/6c under the session locks: the effective binding per session that has or gets one, the
    sessions to bind now and the bound ones; raises SessionDiscard for the first session that conflicts."""
    check_owner(sessions, dims, user_email)
    effective: dict[str, SessionBinding] = {}
    to_bind: list[str] = []
    bound: list[str] = []
    for s in sessions:
        resolved = _resolve_session(s, dims.get(s) or {}, binding, len(sessions))
        if resolved is None:
            continue
        effective[s], is_bound = resolved
        (bound if is_bound else to_bind).append(s)
    if binding is None:
        _check_legacy_request_project(bound, effective, request_project, len(sessions))
        _check_legacy_hooks(batch, effective, len(sessions))
    return effective, to_bind, bound


async def validate_effective(
    effective: dict[str, SessionBinding],
    candidate: SessionBinding | None,
    validate: DestinationValidator | None,
    session_count: int,
    bound: list[str],
) -> None:
    """Under the lock, run the service-layer checks on every effective destination the gate did not resolve
    (a bound or legacy project replaced the candidate), so a first-binding race cannot write unchecked. A legacy
    request has no candidate: only the bound destinations it writes are checked (nothing is bound by it)."""
    if validate is None:
        return
    checked: set[tuple[str, bool]] = set()
    for session_id, b in sorted(effective.items()):
        is_bound = session_id in bound
        key = (b.project_name.lower(), is_bound)
        if (candidate is not None and key[0] == candidate.project_name.lower()) or key in checked:
            continue
        checked.add(key)
        if reason := await validate(b, is_bound):
            raise SessionDiscard(reason, session_id, session_count - 1)


def stamped(batch: DecodedBatch, effective: dict[str, SessionBinding]) -> DecodedBatch:
    """Hook rows of bound sessions stored under the binding; the identity (day, h) stays as sent."""
    if effective:
        batch.hooks = [
            h
            if (b := effective.get(h.session_id)) is None
            else h._replace(
                codemie_project_name=b.project_name,
                subscription_type=b.subscription_type,
                project_origin=b.project_origin,
                route_note=b.route_note,
                client_subscription_type=b.client_subscription_type or h.client_subscription_type,
                client_policy_version=b.client_policy_version or h.client_policy_version,
            )
            for h in batch.hooks
        ]
    return batch


def hook_repo_remote(batch: DecodedBatch) -> str | None:
    """The `repo_remote` of the earliest hook row that carries one (registration lookup without the header)."""
    for h in sorted(batch.hooks, key=lambda h: h.ts):
        if (h.repo_remote or "").strip():
            return h.repo_remote
    return None


def session_hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()[:12]


def log_discard(exc: SessionDiscard, dropped: int) -> None:
    SESSION_DISCARDS[exc.reason] += 1
    logger.warning(
        f"cli_analytics: discarded a request, reason={exc.reason} session={session_hash(exc.session_id)} "
        f"dropped={dropped} other_sessions_dropped={exc.other_sessions}"
    )


async def lock_and_read(conn: asyncpg.Connection, lock_keys: list[int], sessions: list[str], evidence: bool) -> dict:
    """Every session of the batch locked (keys sorted), then its binding read: of two batches of one session the
    second waits for the first to commit, so its lookups see the other's rows."""
    await conn.execute(SESSION_LOCKS_SQL, lock_keys)
    return {r["session_id"]: r for r in await conn.fetch(BINDINGS_SQL, sessions, evidence)}


async def bind(
    conn: asyncpg.Connection,
    effective: dict[str, SessionBinding],
    to_bind: list[str],
    bound: list[str],
    user_email: str,
) -> None:
    if to_bind:
        first = effective[to_bind[0]]  # one evidence request: one type, origin and note for all
        projects = [effective[s].project_name for s in to_bind]
        args = (user_email, first.subscription_type, first.project_origin, first.route_note, projects)
        await conn.execute(_BIND_SQL, to_bind, *args)
    if bound:
        await conn.execute(_TOUCH_BOUND_SQL, bound)
