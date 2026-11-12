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

"""One loader for a user known only by id: LocalIdp and trigger owners (EPMCDME-16625 AR-10).

A scheduled run, its signed loopback callback and a webhook-started workflow see the same owner because they are
all built here. Repositories are imported per call so tests can patch them on their modules.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from codemie.configs import config
from codemie.rest_api.security.user import User, resolve_membership_default


@dataclass(frozen=True, slots=True)
class UserLookup:
    # The active user, or in IDP-claims mode the owner's recent last-login claims; None when neither exists.
    user: User | None
    # A users row exists, active or not: with no user, the id belongs to a deactivated user.
    has_row: bool


def user_from_rows(db_user: Any, projects: Iterable[Any], kbs: Iterable[Any]) -> User:
    """The user as an interactive request sees them: memberships, project-admin rights, default and KBs."""
    projects = list(projects)
    return User(
        id=db_user.id,
        username=db_user.username,
        name=db_user.name or "",
        email=db_user.email,
        picture=db_user.picture or "",
        user_type=db_user.user_type,
        roles=[],
        project_names=[p.project_name for p in projects],
        admin_project_names=[p.project_name for p in projects if p.is_project_admin],
        default_project=resolve_membership_default(projects, db_user.email),
        knowledge_bases=[kb.kb_name for kb in kbs],
        is_admin=db_user.is_admin,
        is_maintainer=db_user.is_maintainer,
        is_auditor=db_user.is_auditor,
        project_limit=db_user.project_limit,
    )


def _reads_snapshot(with_snapshot: bool) -> bool:
    # Snapshots exist only in IDP-claims mode, which keeps no users rows (AR-5: callers opt in).
    return with_snapshot and not config.ENABLE_USER_MANAGEMENT


def load_user_by_id(session, user_id: str, *, with_snapshot: bool) -> UserLookup:
    """Load a user by id; ``with_snapshot`` only for trigger owners and signed loopback calls (AR-5)."""
    from codemie.repository.idp_user_snapshot_repository import idp_user_snapshot_repository
    from codemie.repository.user_kb_repository import user_kb_repository
    from codemie.repository.user_project_repository import user_project_repository
    from codemie.repository.user_repository import user_repository
    from codemie.service.user.idp_user_snapshot_service import current_user_from_snapshot

    db_user = user_repository.get_active_by_id(session, user_id)
    if db_user is not None:
        projects = user_project_repository.get_by_user_id(session, db_user.id)
        kbs = user_kb_repository.get_by_user_id(session, db_user.id)
        return UserLookup(user_from_rows(db_user, projects, kbs), has_row=True)
    if user_repository.get_by_id(session, user_id) is not None:
        return UserLookup(None, has_row=True)
    if not _reads_snapshot(with_snapshot):
        return UserLookup(None, has_row=False)
    return UserLookup(current_user_from_snapshot(idp_user_snapshot_repository.get(session, user_id)), has_row=False)


async def aload_user_by_id(session, user_id: str, *, with_snapshot: bool) -> UserLookup:
    """Async twin of load_user_by_id."""
    from codemie.repository.idp_user_snapshot_repository import idp_user_snapshot_repository
    from codemie.repository.user_kb_repository import user_kb_repository
    from codemie.repository.user_project_repository import user_project_repository
    from codemie.repository.user_repository import user_repository
    from codemie.service.user.idp_user_snapshot_service import current_user_from_snapshot

    db_user = await user_repository.aget_active_by_id(session, user_id)
    if db_user is not None:
        projects = await user_project_repository.aget_by_user_id(session, db_user.id)
        kbs = await user_kb_repository.aget_by_user_id(session, db_user.id)
        return UserLookup(user_from_rows(db_user, projects, kbs), has_row=True)
    if await user_repository.aget_by_id(session, user_id) is not None:
        return UserLookup(None, has_row=True)
    if not _reads_snapshot(with_snapshot):
        return UserLookup(None, has_row=False)
    snapshot = await idp_user_snapshot_repository.aget(session, user_id)
    return UserLookup(current_user_from_snapshot(snapshot), has_row=False)
