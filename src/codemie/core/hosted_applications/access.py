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

"""Platform access rules for hosted applications.

Python predicates answer ``Ability``/``Owned`` questions for one loaded app; the SQL builders express the same
rules for list queries. The module never imports the hosted model: predicates take the app's fields and SQL
builders take the table the caller passes in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import sqlalchemy as sa

from codemie.configs import config
from codemie.core.hosted_applications.errors import HostedApplicationError

from codemie.core.ability import Action, preloaded_grant_covers
from codemie.rest_api.models.permission import ResourceType, grant_exists
from codemie.rest_api.models.user_management import UserDB, UserProject, eligible_internal_user

if TYPE_CHECKING:
    from codemie.core.ability import PreloadedGrants
    from codemie.rest_api.security.user import User

SECURITY_CLEAR = "clear"
SECURITY_SUSPENDED = "suspended"
SUSPEND_GATED_ACTIONS = frozenset({"activate", "publish", "share_on", "move"})
# Actions whose SQL manager term also admits a live named grant at a covering level. Empty while grants are view
# only (D9); edit access for named people adds Action.WRITE here and Role.GRANTED under WRITE in ``Ability``.
# DELETE never takes a grant term, so an edit grantee can never delete.
MANAGER_GRANT_ACTIONS: frozenset[Action] = frozenset()
MANAGER_ACTIONS = (Action.WRITE, Action.DELETE)


def _eligible(user: User) -> bool:
    return user.user_type != config.EXTERNAL_USER_TYPE


def is_eligible(user: User) -> bool:
    """Internal account; on a ``platform_user`` copy also active and not deleted."""
    return _eligible(user)


async def platform_user(session: Any, user: User) -> User:
    """``user`` with its roles read from ``users`` and ``user_projects``, the rows the SQL terms read.

    The request ``User`` may carry roles from the IdP or ``ENV=local``; the Python predicates and the admin gate must
    agree with ``readable``/``manageable``, so they take this copy. An account that is missing, inactive, deleted or
    external comes back with no roles and fails every predicate.
    """

    row = (await session.execute(sa.select(UserDB).where(UserDB.id == user.id))).scalar_one_or_none()
    if row is None or not row.is_active or row.deleted_at is not None or row.user_type == config.EXTERNAL_USER_TYPE:
        return user.model_copy(
            update={
                "user_type": config.EXTERNAL_USER_TYPE,
                "is_admin": False,
                "is_maintainer": False,
                "project_names": [],
                "admin_project_names": [],
            }
        )
    members = (await session.execute(sa.select(UserProject).where(UserProject.user_id == user.id))).scalars().all()
    return user.model_copy(
        update={
            "user_type": row.user_type,
            "is_admin": bool(row.is_admin or row.is_maintainer),
            "is_maintainer": bool(row.is_maintainer),
            "project_names": [member.project_name for member in members],
            "admin_project_names": [member.project_name for member in members if member.is_project_admin],
        }
    )


def is_suspended(app: Any) -> bool:
    return app.security_state == SECURITY_SUSPENDED


def _gated(action: str) -> None:
    if action not in SUSPEND_GATED_ACTIONS:
        raise ValueError(f"{action!r} is not in SUSPEND_GATED_ACTIONS")


def refuses(app: Any, action: str) -> bool:
    """The one predicate over ``SUSPEND_GATED_ACTIONS``: a suspended app refuses ``action``."""
    _gated(action)
    return is_suspended(app)


def refuses_sql(apps: Any, action: str) -> Any:
    """SQL twin of ``refuses`` for queries that must not select an app that refuses ``action``."""
    _gated(action)
    return apps.c.security_state == SECURITY_SUSPENDED


def refused_actions(app: Any) -> list[str]:
    """The gated actions a suspended app refuses (sorted), else []; projected to clients as ``suspension_refuses``."""
    return sorted(SUSPEND_GATED_ACTIONS) if is_suspended(app) else []


def suspend_refuses(app: Any, action: str) -> None:
    """Raise APP_SUSPENDED (409) when a suspended app is asked to ``activate``, ``publish``, ``share_on``, ``move``."""
    if refuses(app, action):
        raise HostedApplicationError("APP_SUSPENDED", f"The application is suspended; {action} is not allowed.", 409)


def is_owned_by(created_by_id: str | None, user: User) -> bool:
    return _eligible(user) and created_by_id is not None and created_by_id == user.id


def is_grant_remover(created_by_id: str | None, user: User) -> bool:
    """Python twin of ``grant_remover`` on a ``platform_user`` copy: a platform admin or maintainer, or the creator.

    Also the rule for revoking an app's share link without suspending it (S-17).
    """
    return is_moderator(user) or (_eligible(user) and created_by_id is not None and created_by_id == user.id)


def is_managed_by(project: str, user: User) -> bool:
    return _eligible(user) and project in user.admin_project_names


def is_shared_with(project: str, shared: bool, is_global: bool, security_state: str, user: User) -> bool:
    """Viewer term: (shared and project member) or marketplace-global; never while suspended.

    Named grants are ``Role.GRANTED`` (``is_granted``), not part of this term.
    """
    if not _eligible(user) or security_state == SECURITY_SUSPENDED:
        return False
    return bool(is_global) or (bool(shared) and project in user.project_names)


def is_granted(security_state: str, user: User, app_id: Any, grants: PreloadedGrants | None, action: Action) -> bool:
    """``Role.GRANTED``: a preloaded grant covering ``action`` for an eligible user; never while suspended.

    Without preloaded ``grants`` there is no grant (fail closed, no lookup).
    """
    if not _eligible(user) or security_state == SECURITY_SUSPENDED:
        return False
    return preloaded_grant_covers(grants, app_id, action)


def grant_recipient(users: Any) -> Any:
    """Recipient policy of hosted named grants (the descriptor's ``recipient``): an eligible internal user.

    Who may be granted and whose stored grant counts; the SQL twin of ``_eligible`` (S-18).
    """

    return eligible_internal_user(users)


def _eligible_user(user_id_expr: Any) -> Any:
    users = UserDB.__table__
    return sa.select(users.c.id).where(users.c.id == user_id_expr, eligible_internal_user(users))


def _membership(apps: Any, user_id_expr: Any, admin_only: bool) -> Any:
    members = UserProject.__table__
    condition = sa.and_(
        members.c.user_id == user_id_expr,
        members.c.project_name == apps.c.project,
    )
    if admin_only:
        condition = sa.and_(condition, members.c.is_project_admin.is_(True))
    return sa.exists().where(condition)


def _platform_admin(user_id_expr: Any) -> Any:
    users = UserDB.__table__
    return sa.exists().where(
        users.c.id == user_id_expr, sa.or_(users.c.is_admin.is_(True), users.c.is_maintainer.is_(True))
    )


def _manager(apps: Any, user_id_expr: Any, action: Action) -> Any:
    if action not in MANAGER_ACTIONS:
        raise ValueError(f"A manager term answers WRITE or DELETE, not {action}")
    terms = [
        apps.c.created_by["id"].as_string() == user_id_expr,
        _membership(apps, user_id_expr, admin_only=True),
        _platform_admin(user_id_expr),
    ]
    if action in MANAGER_GRANT_ACTIONS:
        terms.append(_grant(apps, user_id_expr, action))
    return sa.or_(*terms)


def _grant(apps: Any, user_id_expr: Any, action: Action) -> Any:
    return grant_exists(ResourceType.HOSTED_APPLICATION, apps.c.id, user_id_expr, action, recipient=grant_recipient)


def _viewer(apps: Any, user_id_expr: Any) -> Any:
    return sa.and_(
        apps.c.security_state != SECURITY_SUSPENDED,
        sa.or_(
            apps.c.is_global.is_(True),
            sa.and_(apps.c.shared.is_(True), _membership(apps, user_id_expr, admin_only=False)),
            _grant(apps, user_id_expr, Action.READ),
        ),
    )


def manageable(apps: Any, user_id_expr: Any, action: Action = Action.WRITE) -> Any:
    """Eligible user who is the creator, a project admin of the app's project, or a platform admin.

    ``action`` is WRITE (change the app) or DELETE (delete it); a grant term may join WRITE only
    (``MANAGER_GRANT_ACTIONS``), so the delete path asks for DELETE.
    """
    return sa.and_(sa.exists(_eligible_user(user_id_expr)), _manager(apps, user_id_expr, action))


def is_platform_admin(user: User) -> bool:
    """Eligible platform admin or maintainer, on a ``platform_user`` copy (whose ``is_admin`` covers maintainers)."""
    return _eligible(user) and bool(user.is_admin or getattr(user, "is_maintainer", False))


def is_moderator(user: User) -> bool:
    """A platform admin: may suspend, reinstate and revoke share links. There is no moderation list (S-28);
    a platform admin finds every app in the Project tab instead (``list_scope``)."""
    return is_platform_admin(user)


def grant_remover(apps: Any | None, user_id_expr: Any) -> Any:
    """Eligible user who may revoke a named grant: a platform admin or maintainer, or the app's creator.

    Decided from the ``users`` row, never from request roles. Without an app row (an orphan grant) only the
    administrator branch applies.
    """
    branches = [_platform_admin(user_id_expr)]
    if apps is not None:
        branches.append(apps.c.created_by["id"].as_string() == user_id_expr)
    return sa.and_(sa.exists(_eligible_user(user_id_expr)), sa.or_(*branches))


def readable(apps: Any, user_id_expr: Any) -> Any:
    """Eligible user who manages the app, or views it while it is not suspended."""
    return sa.and_(
        sa.exists(_eligible_user(user_id_expr)),
        sa.or_(_manager(apps, user_id_expr, Action.WRITE), _viewer(apps, user_id_expr)),
    )
