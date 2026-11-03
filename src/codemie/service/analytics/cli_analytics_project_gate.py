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

"""Project gate for CLI analytics ingestion (EPMCDME-16025).

Analytics is stored only for a project that has `cli_analytics_enabled`. The project is the one the
CLI names in `X-CodeMie-Project`; the sender must have access to it, and no explicit project value
inside the request may differ from it. Anything else is discarded with a reason, answered as HTTP 200
(a 403 would suspend the CLI's forwarding, other errors would be retried forever). Only a policy
lookup that fails raises, so the endpoint answers 503 and the CLI retries.

That gate applies to legacy requests. Evidence requests (EPMCDME-16440) get their destination from
`resolve_destination` instead.
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import hmac
import logging
from collections import Counter
from dataclasses import dataclass
from functools import partial
from typing import cast

from codemie.clients.postgres import get_async_session, get_session
from codemie.configs import config
from codemie.core.models import Application
from codemie.repository.application_repository import application_repository
from codemie.repository.cli_analytics.scope import ORIGIN_LEGACY_PROJECT, SHARED_ORIGINS
from codemie.repository.cli_analytics.ports import (
    REASON_PROJECT_CONFLICT,
    IngestGate,
    SessionBinding,
    TelemetryStorageUnavailableError,
)
from codemie.repository.user_project_repository import user_project_repository
from codemie.rest_api.security.user import User, resolve_default_project
from codemie.rest_api.security.user_type_validator import is_personal_project_excluded
from codemie.service.project.personal_project_service import personal_project_service
from codemie.service.project.repository_registration_service import RepositoryRegistrationRepository

logger = logging.getLogger(__name__)

REASON_DISABLED = "cli_analytics_disabled"
REASON_CONTEXT_REQUIRED = "project_context_required"
REASON_CONTEXT_CONFLICT = "project_context_conflict"
_LOOKUP_FAILED_LOG = "cli_analytics: project policy lookup failed: %s"
_LOOKUP_FAILED_MESSAGE = "Cannot read the project's CLI analytics setting"


def _user_hash(user: User) -> str | None:
    """A short keyed hash of the sender for the discard log line; None when no key is configured or the sender
    has no id."""
    key = config.CLI_ANALYTICS_LOG_HMAC_KEY
    if not key or not user.id:
        return None
    return hmac.new(key.encode(), str(user.id).encode(), hashlib.sha256).hexdigest()[:16]


class _ProjectIngestGate:
    """The gate of one request. When it accepts the request, `canonical_project` is the stored name of the
    project it was accepted for (the request may spell it in another case), so what is persisted follows the
    project that was found, not the spelling that was sent."""

    def __init__(self, user: User, header_project: str | None) -> None:
        self._user = user
        self._project = header_project or ""
        self.canonical_project: str | None = None

    @property
    def user_hash(self) -> str | None:
        """Computed on access: only a recorded discard reads it, so an accepted request does no HMAC."""
        return _user_hash(self._user)

    async def validate_destination(self, binding: SessionBinding, bound: bool = False) -> str | None:
        """The ingestor awaits this under the session lock for the project an ALREADY BOUND session is stored under:
        a legacy request never changes it, but it is written, so that project must still be usable by the sender
        (access, live membership, enablement, personal ownership). The gate is not an evidence gate for that: it has
        no `resolve_session` and its request binds nothing."""
        return await validate_destination(self._user, binding, bound)

    async def __call__(self, explicit: frozenset[str]) -> str | None:
        self.canonical_project = None
        project = self._project
        if not project.strip():  # blank counts as absent; the value itself is used exactly as received
            return REASON_CONTEXT_REQUIRED
        # Same rule as the lookup below (it compares lower()): no strip, no casefold.
        if len({value.lower() for value in explicit} | {project.lower()}) > 1:
            return REASON_CONTEXT_CONFLICT
        try:
            async with get_async_session() as session:
                application = await application_repository.aget_active_by_name_case_insensitive(session, project)
                if application is None or application.deleted_at is not None:
                    return REASON_CONTEXT_REQUIRED
                if not self._user.has_access_to_application(application.name):
                    return REASON_CONTEXT_REQUIRED
                if config.ENABLE_USER_MANAGEMENT and not self._user.is_admin_or_maintainer:
                    # Auth caches project lists for 30s. Check current membership so removal
                    # takes effect on every worker as soon as the change is committed.
                    membership = await user_project_repository.aget_by_user_and_project(
                        session, self._user.id, application.name
                    )
                    if membership is None:
                        return REASON_CONTEXT_REQUIRED
        except Exception as exc:
            logger.warning(_LOOKUP_FAILED_LOG, type(exc).__name__)
            raise TelemetryStorageUnavailableError(_LOOKUP_FAILED_MESSAGE) from exc
        if not application.cli_analytics_enabled:
            return REASON_DISABLED
        self.canonical_project = application.name
        return None


def build_ingest_gate(user: User, header_project: str | None) -> IngestGate:
    """The gate for one request: `explicit` is every project value the request body carries."""
    return _ProjectIngestGate(user, header_project)


# --- Destination of an evidence request (EPMCDME-16440, spec 5 and 9) ---------------------------------

PROJECT_SOURCES = frozenset({"linked_folder", "profile"})
ORIGIN_PERSONAL = "personal"
ORIGIN_REGISTERED = "registered_repository"
ORIGIN_DEFAULT = "default_project"
NOTE_FOLDER_NOT_LINKED = "folder_not_linked"
NOTE_LINK_UNAVAILABLE = "linked_project_unavailable"
NOTE_REGISTERED_INACCESSIBLE = "registered_project_inaccessible"
NOTE_REGISTRATION_AMBIGUOUS = "registration_ambiguous"
REASON_PERSONAL_UNAVAILABLE = "personal_destination_unavailable"
REASON_DEFAULT_UNAVAILABLE = "default_project_unavailable"
REASON_SESSION_PROJECT_UNAVAILABLE = "session_project_unavailable"
PERSONAL = Application.ProjectType.PERSONAL
# Routing outcomes since this process started (spec 5, 9): `route_note:<note>` for every stored route note and
# `default_project_unavailable:discard|retry`. Read by tests and diagnostics, like the ingestor's SESSION_DISCARDS.
DESTINATION_EVENTS: Counter[str] = Counter()


@dataclass(frozen=True)
class DestinationDecision:
    """Where an evidence request is stored. `reason` set means a final discard and `project` is None.

    Retryable outcomes (503) are not decisions: the resolver raises `TelemetryStorageUnavailableError`
    whose message is the reason (`default_project_unavailable`, ...).
    """

    subscription_type: str
    client_subscription_type: str = ""
    client_policy_version: str = ""
    project: str | None = None
    project_type: str = ""
    project_origin: str = ""
    route_note: str = ""
    reason: str | None = None


async def resolve_destination(
    user: User,
    *,
    subscription_type: str,
    header_project: str | None,
    project_source: str | None,
    repo_remote: str | None,
    client_subscription_type: str = "",
    client_policy_version: str = "",
) -> DestinationDecision:
    """Destination of an evidence request from the derived `subscription_type` (spec 5, 9).

    Personal goes only to the user's own personal project, except for a user type that cannot have one
    (external, service account): it stays Personal-paid but goes to a work project, routed like anything
    else, and no personal project is created. Anything else goes to the linked project (header with a
    source), then the registered repository, then the effective default project. Enablement follows the
    destination: shared needs `cli_analytics_enabled`; a personal one needs ownership, with no separate
    enablement.
    """
    decide = partial(
        DestinationDecision,
        subscription_type=subscription_type,
        client_subscription_type=client_subscription_type,
        client_policy_version=client_policy_version,
    )
    if _owns_personal_project(user, subscription_type):
        application = await _owned_personal_project(user)
        return decide(project=application.name, project_type=PERSONAL.value, project_origin=ORIGIN_PERSONAL)

    application, origin, note = await _shared_route(user, header_project, project_source, repo_remote)
    if note:
        DESTINATION_EVENTS[f"route_note:{note}"] += 1
    if application is None:
        # AC9: the backend never discards for a missing destination. A user with no project can get one (an admin adds
        # them), so it answers a retryable 503 and the CLI retains the data for its finite retention window (24h by
        # default) and retries; nothing is rerouted to a project the user does not belong to.
        logger.warning("cli_analytics: user has no default project, user_id=%s", user.id)
        DESTINATION_EVENTS[f"{REASON_DEFAULT_UNAVAILABLE}:retry"] += 1
        raise TelemetryStorageUnavailableError(REASON_DEFAULT_UNAVAILABLE)
    if note:
        logger.info("cli_analytics: routed to the default project, route_note=%s", note)
    if application.project_type != PERSONAL and not application.cli_analytics_enabled:
        return decide(reason=REASON_DISABLED, route_note=note)
    return decide(
        project=application.name,
        project_type=Application.ProjectType(application.project_type).value,
        project_origin=origin,
        route_note=note,
    )


def _owns_personal_project(user: User, subscription_type: str) -> bool:
    """A Personal-derived session of a user type that can have a personal project (private, owner-only)."""
    return subscription_type == "personal" and not is_personal_project_excluded(user.user_type or "")


def _registrations(remote: str) -> list:
    with get_session() as session:
        return RepositoryRegistrationRepository.find_matches(session, remote)


def _owns(user: User, application: Application | None) -> bool:
    return application is not None and application.project_type == PERSONAL and application.created_by == user.id


async def _owned_personal_project(user: User) -> Application:
    """The user's personal project, provisioned when missing; never a shared project."""
    application = await _lookup_personal(user)
    if application is None:
        if await personal_project_service.ensure_personal_project_async(user.id, user.email):
            application = await _lookup_personal(user)
        if application is None:
            raise TelemetryStorageUnavailableError(REASON_PERSONAL_UNAVAILABLE)
    return application


async def _lookup_personal(user: User) -> Application | None:
    try:
        async with get_async_session() as session:
            application = await application_repository.aget_active_by_name_case_insensitive(session, user.email)
    except Exception as exc:
        logger.warning("cli_analytics: personal project lookup failed: %s", type(exc).__name__)
        raise TelemetryStorageUnavailableError(REASON_PERSONAL_UNAVAILABLE) from exc
    return application if _owns(user, application) else None


async def _shared_route(
    user: User, header_project: str | None, project_source: str | None, repo_remote: str | None
) -> tuple[Application | None, str, str]:
    """(destination, origin, route_note) for a non-Personal session; destination None = no project at all."""
    try:
        async with get_async_session() as session:
            note = ""
            if project_source in PROJECT_SOURCES and header_project and header_project.strip():
                application = await _routable(session, user, header_project)
                if application is not None:
                    return application, project_source, ""
                note = NOTE_LINK_UNAVAILABLE
            matches = await asyncio.to_thread(_registrations, repo_remote) if repo_remote else []
            if len(matches) > 1:
                logger.error("cli_analytics: repository registered to %d projects", len(matches))
                note = note or NOTE_REGISTRATION_AMBIGUOUS
            elif matches:
                application = await _routable(session, user, matches[0].project_name)
                if application is not None:
                    return application, ORIGIN_REGISTERED, ""
                note = note or NOTE_REGISTERED_INACCESSIBLE
            default = resolve_default_project(user.project_names, user.default_project, user.email)
            if default is None:
                return None, ORIGIN_DEFAULT, note
            application = await _routable(session, user, default)
    except Exception as exc:
        logger.warning(_LOOKUP_FAILED_LOG, type(exc).__name__)
        raise TelemetryStorageUnavailableError(_LOOKUP_FAILED_MESSAGE) from exc
    if application is None:
        # A broken default never reroutes the session elsewhere; the CLI keeps its spool and retries.
        logger.warning("cli_analytics: default project unavailable, user_id=%s", user.id)
        DESTINATION_EVENTS[f"{REASON_DEFAULT_UNAVAILABLE}:retry"] += 1
        raise TelemetryStorageUnavailableError(REASON_DEFAULT_UNAVAILABLE)
    return application, ORIGIN_DEFAULT, note or NOTE_FOLDER_NOT_LINKED


async def _routable(session, user: User, name: str) -> Application | None:
    """A usable project that may receive a NEW session: a user type without a personal project (external, service
    account) never routes to an old personal project of theirs, whatever the classification."""
    application = await _usable(session, user, name)
    if (
        application is not None
        and application.project_type == PERSONAL
        and is_personal_project_excluded(user.user_type or "")
    ):
        return None
    return application


async def _usable(session, user: User, name: str) -> Application | None:
    """The live project when the user may send for it (the legacy gate's checks); another user's personal
    project is never usable, whatever the user's role."""
    application = await application_repository.aget_active_by_name_case_insensitive(session, name)
    if application is None or application.deleted_at is not None:
        return None
    if application.project_type == PERSONAL and not _owns(user, application):
        return None
    if not user.has_access_to_application(application.name):
        return None
    if (
        config.ENABLE_USER_MANAGEMENT
        and not user.is_admin_or_maintainer
        and await user_project_repository.aget_by_user_and_project(session, user.id, application.name) is None
    ):
        return None  # live membership, as in the legacy gate
    return application


async def validate_destination(user: User, binding: SessionBinding, bound: bool) -> str | None:
    """Existence, access, live membership, shared enablement or personal ownership of the destination a session
    is really stored under. A final reason discards; the exception means retry.

    A project that was looked up and is gone or unusable (renamed, deleted, access or membership revoked) is the
    final discard `session_project_unavailable` for a session that is already bound: its history stays, and it is
    neither rerouted nor migrated. For an unbound session (a legacy project) it is retried. A failed lookup is
    always retried, never mistaken for an inaccessible project.
    """
    try:
        async with get_async_session() as session:
            application = await _usable(session, user, binding.project_name)
    except Exception as exc:
        logger.warning(_LOOKUP_FAILED_LOG, type(exc).__name__)
        raise TelemetryStorageUnavailableError(_LOOKUP_FAILED_MESSAGE) from exc
    if application is None:
        logger.warning("cli_analytics: the session's project is unavailable, user_id=%s", user.id)
        if not bound:
            raise TelemetryStorageUnavailableError(REASON_SESSION_PROJECT_UNAVAILABLE)
        DESTINATION_EVENTS[f"{REASON_SESSION_PROJECT_UNAVAILABLE}:discard"] += 1
        return REASON_SESSION_PROJECT_UNAVAILABLE
    if application.project_type == PERSONAL:
        if is_personal_project_excluded(user.user_type or ""):
            logger.error("cli_analytics: personal destination unavailable for user type, user_id=%s", user.id)
            return REASON_PERSONAL_UNAVAILABLE  # an old personal project never receives their sessions
        if binding.subscription_type == "personal" and binding.project_origin in SHARED_ORIGINS:
            return REASON_PROJECT_CONFLICT  # a private project is never stamped with a shared origin
        return None
    return None if application.cli_analytics_enabled else REASON_DISABLED


class EvidenceGate:
    """The gate of one evidence request (EPMCDME-16440): the project context check, then the destination.

    `__call__` checks the project context; `resolve_session` (awaited by the ingestor under the session lock)
    returns the destination, which becomes `binding`. The ingestor may still replace it (a legacy project of the
    session) and then awaits `validate_destination` with the effective binding, so what is written was always
    checked.
    """

    def __init__(
        self,
        user: User,
        *,
        header_project: str | None,
        project_source: str | None,
        repo_remote: str | None,
        subscription_type: str,
        client_subscription_type: str = "",
        client_policy_version: str = "",
    ) -> None:
        self._user = user
        self._header = header_project
        self._source = project_source
        self._remote = repo_remote
        self._subscription_type = subscription_type
        self._private = _owns_personal_project(user, subscription_type)
        self._client = (client_subscription_type, client_policy_version)
        self.binding: SessionBinding | None = None

    @property
    def user_hash(self) -> str | None:
        """Computed on access, like the legacy gate's: only a recorded discard reads it, so an accepted request does
        no HMAC."""
        return _user_hash(self._user)

    async def __call__(self, explicit: frozenset[str]) -> str | None:
        """The project context check only. The destination is resolved by `resolve_session`, which the ingestor
        awaits under the session lock, once the session's stored binding is known."""
        self.binding = None
        values = {value.lower() for value in explicit}
        if (self._header or "").strip():
            values.add((self._header or "").lower())
        return REASON_CONTEXT_CONFLICT if len(values) > 1 else None  # the legacy rule, against the header

    async def resolve_session(
        self, bound: SessionBinding | None, legacy_project: str | None, hook_repo_remote: str | None
    ) -> SessionBinding | str:
        """The destination to store the session under, or a final discard reason (spec 6b).

        A bound session, and a non-Personal request on an unbound session with legacy rows, is validated against
        its actual anchor only: an unrelated candidate failure (a broken default, a registration, the provisioning
        of the personal project) never discards or delays it.
        A Personal-derived request on a session anchored elsewhere (bound, or with legacy rows) is a conflict
        decided by a read-only lookup, so nothing is provisioned for a request that is discarded.
        """
        self.binding = None
        if await self._personal_anchor_conflict(bound.project_name if bound is not None else legacy_project, bound):
            return REASON_PROJECT_CONFLICT
        if bound is not None:
            # dataclasses.replace builds an instance of the argument's own class; the checker infers DataclassInstance.
            stamped = cast(
                SessionBinding,
                dataclasses.replace(
                    bound, client_subscription_type=self._client[0], client_policy_version=self._client[1]
                ),
            )
            return await self._adopt(stamped, bound=True)
        if legacy_project and not self._private:
            # Everything but a private Personal request joins the legacy project of its session: that project is the
            # destination, so a fresh candidate (a broken default or lookup) must not decide a request it never
            # routes. The legacy rows prove no route, so every classification stores the origin `legacy_project`
            # (approved AC8: adoption of an existing legacy project); for Personal-paid it also keeps a missing
            # origin from reading as private. The destination is validated as it is (no personal project for
            # that user type).
            return await self._adopt(
                SessionBinding(
                    project_name=legacy_project,
                    subscription_type=self._subscription_type,
                    project_origin=ORIGIN_LEGACY_PROJECT,
                    client_subscription_type=self._client[0],
                    client_policy_version=self._client[1],
                )
            )
        decision = await resolve_destination(
            self._user,
            subscription_type=self._subscription_type,
            header_project=self._header,
            project_source=self._source,
            repo_remote=self._remote or hook_repo_remote,
            client_subscription_type=self._client[0],
            client_policy_version=self._client[1],
        )
        if decision.reason:
            return decision.reason
        self.binding = SessionBinding(
            project_name=decision.project or "",
            subscription_type=decision.subscription_type,
            project_origin=decision.project_origin,
            route_note=decision.route_note,
            client_subscription_type=decision.client_subscription_type,
            client_policy_version=decision.client_policy_version,
        )
        return self.binding

    async def _personal_anchor_conflict(self, anchor: str | None, bound: SessionBinding | None) -> bool:
        """A private Personal request on a session anchored to any project but the sender's own personal one.
        Read-only lookup: nothing is provisioned for a request that is discarded. A bound session whose own
        personal project is gone is no conflict: it is validated and discarded as `session_project_unavailable`."""
        if not self._private or not anchor:
            return False
        own = await _lookup_personal(self._user)
        if own is not None:
            return own.name.lower() != anchor.lower()
        return not (bound is not None and bound.project_origin == ORIGIN_PERSONAL)

    async def _adopt(self, binding: SessionBinding, bound: bool = False) -> SessionBinding | str:
        """The binding when its destination passes validation (it becomes `self.binding`), else the discard reason;
        a retryable failure raises from `validate_destination`."""
        if reason := await self.validate_destination(binding, bound):
            return reason
        self.binding = binding
        return binding

    async def validate_destination(self, binding: SessionBinding, bound: bool = False) -> str | None:
        return await validate_destination(self._user, binding, bound)


def build_evidence_gate(user: User, **kwargs) -> EvidenceGate:
    return EvidenceGate(user, **kwargs)
