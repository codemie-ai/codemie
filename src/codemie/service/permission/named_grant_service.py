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

"""Generic named grants for one user (READ through the APIs today) on any type served by a NamedGrantDescriptor.

Every step is one SQL statement, defined once as a module-level builder and run by a sync function or its ``a``-prefixed
async twin. Nothing here commits: the caller owns the transaction and passes the resource it holds locked.
``grant`` checks the owner through ``descriptor.is_owner``, then calls ``descriptor.before_grant``, then de-duplicates;
``revoke`` checks the owner, then de-duplicates. Audit events are written strictly in the same transaction with a
single flush. ``preload`` / ``apreload`` are the only builders of ``PreloadedGrants``: one statement over
``live_grants``. ``Ability`` calls ``load_class_grants`` on its own the first time a check needs a class's grants;
a caller that already holds them passes them (``Ability(user, grants=...)``).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from codemie.configs import config
from codemie.core.ability import Action, PreloadedGrants, levels_covering
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import CreatedByUser
from codemie.repository.user_repository import user_repository
from codemie.rest_api.models.base import BaseModelWithSQLSupport
from codemie.rest_api.models.permission import Permission, PrincipalType, ResourceType, grant_exists, live_grants
from codemie.rest_api.models.user_management import UserDB
from codemie.service.activity.activity_models import ActivityEventCreate
from codemie.service.activity.activity_repository import activity_event_repository
from codemie.service.permission.named_grant_types import NamedGrantDescriptor, descriptor_for, resource_type_for_class

if TYPE_CHECKING:
    from sqlalchemy.engine import Row
    from sqlalchemy.ext.asyncio import AsyncSession

    from codemie.rest_api.security.user import User

SEARCH_RESULT_LIMIT = 20
UNIQUE_CONSTRAINT = "unique_resource_principal"
GRANT_SOURCE_NAME = "name"
GRANT_SOURCE_LINK = "link"

_DELETED_COLUMNS = (
    Permission.principal_type,
    Permission.principal_id,
    Permission.permission_level,
    Permission.grant_source,
    Permission.source_link_id,
)


def _uuid(value: Any) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _uuids(values: Iterable[Any]) -> list[UUID]:
    """The values that are UUIDs; any other id cannot have a grant row (``permissions.resource_id`` is a uuid)."""
    found = []
    for value in values:
        try:
            found.append(_uuid(value))
        except (TypeError, ValueError):
            continue
    return found


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _enum_name(value: Any) -> Any:
    return getattr(value, "name", value)


# ---- statements (each defined once) -------------------------------------------------------------------------------


def validation_statement(resource_type: ResourceType, resource_id: Any, user_ids: list[str]) -> sa.Select:
    """Users by id with their eligibility and the existing grant on the resource (LEFT JOIN permissions)."""
    return (
        sa.select(
            UserDB.id,
            descriptor_for(resource_type).recipient(UserDB).label("eligible"),
            Permission.grant_source,
            Permission.source_link_id,
            Permission.permission_level,
        )
        .select_from(UserDB)
        .outerjoin(
            Permission,
            sa.and_(
                Permission.resource_type == resource_type,
                Permission.resource_id == _uuid(resource_id),
                Permission.principal_type == PrincipalType.USER,
                Permission.principal_id == UserDB.id,
            ),
        )
        .where(UserDB.id.in_(user_ids))
    )


def grant_rows(
    resource_type: ResourceType,
    resource_id: Any,
    actor: User | None,
    user_ids: list[str],
    now: datetime | None = None,
    level: Action = Action.READ,
) -> list[dict[str, Any]]:
    """The permission rows of by-name grants at ``level``: one USER row per user id, as the grant readers match them."""
    now = now or datetime.now()
    created_by = CreatedByUser(id=actor.id, username=actor.username, name=actor.name) if actor else None
    return [
        {
            "id": str(uuid4()),
            "date": now,
            "update_date": now,
            "resource_type": resource_type,
            "resource_id": _uuid(resource_id),
            "principal_type": PrincipalType.USER,
            "principal_id": uid,
            "permission_level": level,
            "grant_source": GRANT_SOURCE_NAME,
            "source_link_id": None,
            "created_by": created_by,
        }
        for uid in user_ids
    ]


def upsert_statement(
    resource_type: ResourceType,
    resource_id: Any,
    actor: User | None,
    user_ids: list[str],
    level: Action = Action.READ,
) -> sa.Insert:
    """Insert grants by name at ``level``.

    A conflicting link grant becomes a name grant at ``level``; a conflicting name grant is raised to ``level`` when
    its stored level does not cover it, and left alone otherwise (a lower level is set by revoke and grant).
    """
    now = datetime.now()
    statement = insert(Permission).values(grant_rows(resource_type, resource_id, actor, user_ids, now, level))
    return statement.on_conflict_do_update(
        constraint=UNIQUE_CONSTRAINT,
        set_={
            "grant_source": GRANT_SOURCE_NAME,
            "source_link_id": None,
            "permission_level": statement.excluded.permission_level,
            "update_date": now,
        },
        where=sa.or_(
            Permission.grant_source == GRANT_SOURCE_LINK,
            Permission.permission_level.not_in(levels_covering(level)),
        ),
    ).returning(
        Permission.id,
        Permission.principal_type,
        Permission.principal_id,
        Permission.permission_level,
        sa.literal_column("(xmax = 0)").label("inserted"),
    )


def link_grant_statement(
    resource_type: ResourceType, resource_id: Any, user: User, link_id: Any, granted_by: CreatedByUser
) -> sa.Insert:
    """Insert one READ grant for the link opener; any existing row of either source stays as it is.

    The row's ``created_by`` is ``granted_by``, the link's owner: the opener never owns the row that grants it.
    """
    row = {
        **grant_rows(resource_type, resource_id, None, [user.id])[0],
        "grant_source": GRANT_SOURCE_LINK,
        "source_link_id": _uuid(link_id),
        "created_by": granted_by,
    }
    return (
        insert(Permission)
        .values([row])
        .on_conflict_do_nothing(constraint=UNIQUE_CONSTRAINT)
        .returning(Permission.id, Permission.principal_type, Permission.principal_id, Permission.permission_level)
    )


def remove_link_statement(resource_type: ResourceType, resource_id: Any, link_id: Any) -> sa.Delete:
    """Delete the grants still owned by the link; rows upgraded to by-name have a NULL source_link_id and survive."""
    return (
        sa.delete(Permission)
        .where(
            Permission.resource_type == resource_type,
            Permission.resource_id == _uuid(resource_id),
            Permission.grant_source == GRANT_SOURCE_LINK,
            Permission.source_link_id == _uuid(link_id),
        )
        .returning(*_DELETED_COLUMNS)
    )


def revoke_statement(resource_type: ResourceType, resource_id: Any, user_ids: list[str]) -> sa.Delete:
    return (
        sa.delete(Permission)
        .where(
            Permission.resource_type == resource_type,
            Permission.resource_id == _uuid(resource_id),
            Permission.principal_type == PrincipalType.USER,
            Permission.principal_id.in_(user_ids),
        )
        .returning(*_DELETED_COLUMNS)
    )


def revoke_permission_statement(permission_id: Any) -> sa.Delete:
    return sa.delete(Permission).where(Permission.id == permission_id).returning(*_DELETED_COLUMNS)


def remove_all_statement(resource_type: ResourceType, resource_id: Any) -> sa.Delete:
    return (
        sa.delete(Permission)
        .where(Permission.resource_type == resource_type, Permission.resource_id == _uuid(resource_id))
        .returning(*_DELETED_COLUMNS)
    )


def list_statement(resource_type: ResourceType, resource_id: Any) -> sa.Select:
    """USER grants with their user row; a grant whose user is gone still appears (id is NULL)."""
    return (
        sa.select(
            Permission.principal_id,
            Permission.grant_source,
            UserDB.id,
            UserDB.username,
            UserDB.email,
            UserDB.name,
            UserDB.picture,
        )
        .select_from(Permission)
        .outerjoin(UserDB, UserDB.id == Permission.principal_id)
        .where(
            Permission.resource_type == resource_type,
            Permission.resource_id == _uuid(resource_id),
            Permission.principal_type == PrincipalType.USER,
        )
        .order_by(Permission.date)
    )


def preload_statement(
    resource_type: ResourceType, user_id: str, resource_ids: Iterable[Any] | None = None
) -> sa.Select:
    """The user's live grants of the type (``live_grants``), optionally narrowed to the resources to be checked."""
    statement = live_grants(resource_type, sa.literal(user_id), descriptor_for(resource_type).recipient)
    if resource_ids is not None:
        statement = statement.where(Permission.resource_id.in_(_uuids(resource_ids)))
    return statement


def granted_ids_statement(resource_type: ResourceType, principal_id_expr: sa.ColumnElement[Any]) -> sa.Select:
    """Ids of the resources live-granted to the principal, typed as the resource table's id (``resource_key``)."""
    descriptor = descriptor_for(resource_type)
    granted = live_grants(resource_type, principal_id_expr, descriptor.recipient).subquery()
    return sa.select(descriptor.resource_key(granted.c.resource_id))


def grant_exists_term(
    resource_type: ResourceType,
    resource_id_expr: sa.ColumnElement[Any],
    principal_id_expr: sa.ColumnElement[Any],
    action: Action,
) -> sa.Exists:
    """EXISTS of a live grant covering ``action``, with the type's recipient policy and id key (``resource_key``)."""
    descriptor = descriptor_for(resource_type)
    return grant_exists(
        resource_type,
        resource_id_expr,
        principal_id_expr,
        action,
        recipient=descriptor.recipient,
        resource_key=descriptor.resource_key,
    )


# ---- payload builders ---------------------------------------------------------------------------------------------


def _dedupe(user_ids: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(user_ids))


def _check_owner(descriptor: NamedGrantDescriptor, resource: Any, owner: User) -> None:
    """403 unless ``owner`` owns ``resource`` (the row the caller holds locked)."""
    if not descriptor.is_owner(resource, owner):
        raise ExtendedHTTPException(code=403, message=descriptor.not_owner_message)


def _preloaded(resource_type: ResourceType, rows: Iterable[Any]) -> PreloadedGrants:
    """Highest level per resource id; the level order lives only in ``levels_covering``."""
    levels: dict[str, Action] = {}
    for resource_id, level in rows:
        key = str(resource_id)
        if key not in levels or levels[key] not in levels_covering(level):
            levels[key] = level
    return PreloadedGrants(descriptor_for(resource_type).owned_class_name, levels)


def _nothing_to_check(resource_ids: Iterable[Any] | None) -> bool:
    return resource_ids is not None and not _uuids(resource_ids)


def _prepare_grant(resource_type: ResourceType, resource: Any, owner: User, user_ids: Iterable[str]) -> list[str]:
    """Owner check, then the descriptor's ``before_grant``, then de-duplication keeping order.

    The owner's own id is dropped: the owner already has access, and a self-grant row would make a private
    resource look shared with people.
    """
    descriptor = descriptor_for(resource_type)
    _check_owner(descriptor, resource, owner)
    descriptor.before_grant(resource)
    return [uid for uid in _dedupe(user_ids) if uid != owner.id]


def _prepare_revoke(resource_type: ResourceType, resource: Any, owner: User, user_ids: Iterable[str]) -> list[str]:
    """Owner check, then de-duplication keeping order; a suspended resource may still be revoked."""
    _check_owner(descriptor_for(resource_type), resource, owner)
    return _dedupe(user_ids)


def _check_users(found: list[Row[Any]], user_ids: list[str]) -> dict[str, Row[Any]]:
    """404 for an unknown id, 400 for a found but not sharable one; returns the existing grant row per user id."""
    by_id = {row.id: row for row in found}
    for uid in user_ids:
        row = by_id.get(uid)
        if row is None:
            raise ExtendedHTTPException(code=404, message=f"User {uid} not found")
        if not row.eligible:
            raise ExtendedHTTPException(code=400, message=f"User {uid} is not a sharable internal user")
    return by_id


def _event(
    descriptor: NamedGrantDescriptor,
    actor: User | None,
    resource_type: ResourceType,
    resource_id: Any,
    project: str | None,
    principal_type: PrincipalType | str,
    principal_id: str,
    before: Action | None,
    after: Action | None,
    grant_source: str | None,
    extra: dict[str, Any] | None = None,
) -> ActivityEventCreate:
    return ActivityEventCreate(
        domain=descriptor.activity_domain,
        event_type=descriptor.event_added if after is not None else descriptor.event_removed,
        entity_type=descriptor.activity_entity_type,
        entity_id=str(resource_id),
        actor_id=actor.id if actor else None,
        attributes={
            "actor_id": actor.id if actor else None,
            "actor_username": actor.username if actor else "system",
            "principal_type": _enum_name(principal_type),
            "principal_id": principal_id,
            "resource_type": _enum_value(resource_type),
            "resource_id": str(resource_id),
            "project": project,
            "grant_source": grant_source,
            "before": _enum_value(before),
            "after": _enum_value(after),
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            **descriptor.audit_attributes(resource_id),
            **(extra or {}),
        },
    )


def _grant_events(
    resource_type: ResourceType,
    resource_id: Any,
    project: str | None,
    actor: User | None,
    upserted: list[Row[Any]],
    existing: dict[str, Row[Any]],
) -> list[ActivityEventCreate]:
    descriptor = descriptor_for(resource_type)
    events = []
    for row in upserted:
        before = None
        extra = None
        if not row.inserted:
            prior = existing[row.principal_id]
            before = prior.permission_level or Action.READ
            extra = {
                "previous_grant_source": prior.grant_source,
                "previous_source_link_id": str(prior.source_link_id) if prior.source_link_id else None,
            }
        events.append(
            _event(
                descriptor,
                actor,
                resource_type,
                resource_id,
                project,
                row.principal_type,
                row.principal_id,
                before,
                row.permission_level,
                GRANT_SOURCE_NAME,
                extra,
            )
        )
    return events


def _removal_events(
    resource_type: ResourceType, resource_id: Any, project: str | None, actor: User | None, deleted: list[Row[Any]]
) -> list[ActivityEventCreate]:
    descriptor = descriptor_for(resource_type)
    return [
        _event(
            descriptor,
            actor,
            resource_type,
            resource_id,
            project,
            row.principal_type,
            row.principal_id,
            row.permission_level,
            None,
            row.grant_source,
            {"previous_source_link_id": str(row.source_link_id)} if row.source_link_id else None,
        )
        for row in deleted
    ]


def _link_grant_events(
    resource_type: ResourceType,
    resource_id: Any,
    project: str | None,
    user: User,
    link_id: Any,
    inserted: list[Row[Any]],
) -> list[ActivityEventCreate]:
    descriptor = descriptor_for(resource_type)
    return [
        _event(
            descriptor,
            user,
            resource_type,
            resource_id,
            project,
            row.principal_type,
            row.principal_id,
            None,
            Action.READ,
            GRANT_SOURCE_LINK,
            {"source_link_id": str(link_id)},
        )
        for row in inserted
    ]


def _user_payload(row: Row[Any]) -> dict[str, str | None]:
    if row.id is None:
        # Orphan grant (user gone): listed so the owner can revoke it.
        uid = row.principal_id
        return {
            "id": uid,
            "username": uid,
            "email": None,
            "name": None,
            "picture": None,
            "grant_source": row.grant_source,
        }
    return {
        "id": row.id,
        "username": row.username,
        "email": row.email,
        "name": row.name,
        "picture": row.picture,
        "grant_source": row.grant_source,
    }


def _sorted_users(rows: list[Row[Any]]) -> list[dict[str, str | None]]:
    return sorted((_user_payload(r) for r in rows), key=lambda u: u.get("name") or u["username"])


# ---- sync ---------------------------------------------------------------------------------------------------------


def grant(
    session: Session,
    *,
    resource_type: ResourceType,
    resource: Any,
    owner: User,
    project: str | None,
    user_ids: list[str],
    level: Action = Action.READ,
) -> list[str]:
    """Give each user ``level`` by name on ``resource`` (held locked by the caller), for its owner only.

    Only READ is requestable through the APIs today (D9). Returns the user ids whose grant was created, upgraded
    from a link grant or raised to ``level``.
    """
    ids = _prepare_grant(resource_type, resource, owner, user_ids)
    if not ids:
        return []
    resource_id = resource.id
    existing = _check_users(session.execute(validation_statement(resource_type, resource_id, ids)).all(), ids)
    upserted = session.execute(upsert_statement(resource_type, resource_id, owner, ids, level)).all()
    activity_event_repository.insert_many_strict(
        _grant_events(resource_type, resource_id, project, owner, upserted, existing), session
    )
    return [row.principal_id for row in upserted]


def revoke(
    session: Session,
    *,
    resource_type: ResourceType,
    resource: Any,
    owner: User,
    project: str | None,
    user_ids: list[str],
) -> list[str]:
    """Delete the users' grants on ``resource``, for its owner only (a missing grant is a no-op).

    Returns the user ids that had one.
    """
    ids = _prepare_revoke(resource_type, resource, owner, user_ids)
    if not ids:
        return []
    resource_id = resource.id
    deleted = session.execute(revoke_statement(resource_type, resource_id, ids)).all()
    activity_event_repository.insert_many_strict(
        _removal_events(resource_type, resource_id, project, owner, deleted), session
    )
    return [row.principal_id for row in deleted]


def revoke_permission(actor: User | None, permission: Permission, session: Session, project: str | None) -> None:
    """Delete one permission row of a named-grant resource type with audit. The caller authorizes."""
    deleted = session.execute(revoke_permission_statement(permission.id)).all()
    activity_event_repository.insert_many_strict(
        _removal_events(permission.resource_type, permission.resource_id, project, actor, deleted), session
    )


def remove_all_grants(
    actor: User | None, resource_type: ResourceType, resource_id: Any, project: str | None, session: Session
) -> None:
    """Delete every grant on a resource, one audit event per row. actor None is recorded as "system".

    A resource whose id is not a UUID cannot have a grant row, so there is nothing to delete.
    """
    if not _uuids([resource_id]):
        return
    deleted = session.execute(remove_all_statement(resource_type, resource_id)).all()
    activity_event_repository.insert_many_strict(
        _removal_events(resource_type, resource_id, project, actor, deleted), session
    )


def link_grant(
    session: Session,
    resource_type: ResourceType,
    resource: Any,
    user: User,
    link_id: Any,
    project: str | None,
    granted_by: CreatedByUser,
) -> bool:
    """Give ``user`` READ through a share link; True when a row was written (an existing grant is left alone).

    ``granted_by`` (the link's owner) becomes the row's creator; ``user`` is the principal and the audit actor.
    """
    inserted = session.execute(link_grant_statement(resource_type, resource.id, user, link_id, granted_by)).all()
    activity_event_repository.insert_many_strict(
        _link_grant_events(resource_type, resource.id, project, user, link_id, inserted), session
    )
    return bool(inserted)


def remove_link_grants(
    actor: User | None,
    resource_type: ResourceType,
    resource_id: Any,
    link_id: Any,
    project: str | None,
    session: Session,
) -> None:
    """Delete every grant the link still owns, one audit event per row."""
    deleted = session.execute(remove_link_statement(resource_type, resource_id, link_id)).all()
    activity_event_repository.insert_many_strict(
        _removal_events(resource_type, resource_id, project, actor, deleted), session
    )


def list_shared_with(session: Session, resource_type: ResourceType, resource_id: Any) -> list[dict[str, str | None]]:
    if not _uuids([resource_id]):
        return []
    return _sorted_users(session.execute(list_statement(resource_type, resource_id)).all())


def preload(
    session: Session, resource_type: ResourceType, user: User, resource_ids: Iterable[Any] | None = None
) -> PreloadedGrants:
    """The user's live grants of the type for ``Ability(user, grants=...)``; ``resource_ids`` narrows the read."""
    if _nothing_to_check(resource_ids):
        return _preloaded(resource_type, [])
    return _preloaded(resource_type, session.execute(preload_statement(resource_type, user.id, resource_ids)).all())


def load_class_grants(user: User, class_name: str) -> PreloadedGrants | None:
    """All of ``user``'s live grants of the type owning ``class_name``, in one statement: ``Ability``'s grant source.

    ``Ability`` calls it (through ``core.ability.GRANT_LOADER``) the first time a check needs named grants for an
    instance class. Synchronous like ``Ability``: a short-lived session on the platform's sync engine, as the
    assistant read paths used before. None for a class with no named grants.
    """
    resource_type = resource_type_for_class(class_name)
    if resource_type is None:
        return None
    if not config.ENABLE_USER_MANAGEMENT:
        return _preloaded(resource_type, [])  # ``live_grants`` is empty then; skip the round trip
    with Session(BaseModelWithSQLSupport.get_engine()) as session:
        return preload(session, resource_type, user)


# ---- async twins --------------------------------------------------------------------------------------------------


async def agrant(
    session: AsyncSession,
    *,
    resource_type: ResourceType,
    resource: Any,
    owner: User,
    project: str | None,
    user_ids: list[str],
    level: Action = Action.READ,
) -> list[str]:
    ids = _prepare_grant(resource_type, resource, owner, user_ids)
    if not ids:
        return []
    resource_id = resource.id
    found = (await session.execute(validation_statement(resource_type, resource_id, ids))).all()
    existing = _check_users(found, ids)
    upserted = (await session.execute(upsert_statement(resource_type, resource_id, owner, ids, level))).all()
    await activity_event_repository.async_insert_many_strict(
        _grant_events(resource_type, resource_id, project, owner, upserted, existing), session
    )
    return [row.principal_id for row in upserted]


async def arevoke(
    session: AsyncSession,
    *,
    resource_type: ResourceType,
    resource: Any,
    owner: User,
    project: str | None,
    user_ids: list[str],
) -> list[str]:
    ids = _prepare_revoke(resource_type, resource, owner, user_ids)
    if not ids:
        return []
    resource_id = resource.id
    deleted = (await session.execute(revoke_statement(resource_type, resource_id, ids))).all()
    await activity_event_repository.async_insert_many_strict(
        _removal_events(resource_type, resource_id, project, owner, deleted), session
    )
    return [row.principal_id for row in deleted]


async def arevoke_permission(
    actor: User | None, permission: Permission, session: AsyncSession, project: str | None
) -> None:
    deleted = (await session.execute(revoke_permission_statement(permission.id))).all()
    await activity_event_repository.async_insert_many_strict(
        _removal_events(permission.resource_type, permission.resource_id, project, actor, deleted), session
    )


async def aremove_all_grants(
    actor: User | None, resource_type: ResourceType, resource_id: Any, project: str | None, session: AsyncSession
) -> None:
    if not _uuids([resource_id]):
        return
    deleted = (await session.execute(remove_all_statement(resource_type, resource_id))).all()
    await activity_event_repository.async_insert_many_strict(
        _removal_events(resource_type, resource_id, project, actor, deleted), session
    )


async def alink_grant(
    session: AsyncSession,
    resource_type: ResourceType,
    resource: Any,
    user: User,
    link_id: Any,
    project: str | None,
    granted_by: CreatedByUser,
) -> bool:
    statement = link_grant_statement(resource_type, resource.id, user, link_id, granted_by)
    inserted = (await session.execute(statement)).all()
    await activity_event_repository.async_insert_many_strict(
        _link_grant_events(resource_type, resource.id, project, user, link_id, inserted), session
    )
    return bool(inserted)


async def aremove_link_grants(
    actor: User | None,
    resource_type: ResourceType,
    resource_id: Any,
    link_id: Any,
    project: str | None,
    session: AsyncSession,
) -> None:
    deleted = (await session.execute(remove_link_statement(resource_type, resource_id, link_id))).all()
    await activity_event_repository.async_insert_many_strict(
        _removal_events(resource_type, resource_id, project, actor, deleted), session
    )


async def apreload(
    session: AsyncSession, resource_type: ResourceType, user: User, resource_ids: Iterable[Any] | None = None
) -> PreloadedGrants:
    if _nothing_to_check(resource_ids):
        return _preloaded(resource_type, [])
    rows = (await session.execute(preload_statement(resource_type, user.id, resource_ids))).all()
    return _preloaded(resource_type, rows)


async def alist_shared_with(
    session: AsyncSession, resource_type: ResourceType, resource_id: Any
) -> list[dict[str, str | None]]:
    if not _uuids([resource_id]):
        return []
    return _sorted_users((await session.execute(list_statement(resource_type, resource_id))).all())


async def search_sharable_users(
    session: AsyncSession, resource_type: ResourceType, query: str | None
) -> list[dict[str, str | None]]:
    """Users the type may grant (its descriptor's ``recipient`` rule) matching the query, at most
    SEARCH_RESULT_LIMIT (limit is in SQL): the people offered are the people a grant would accept."""
    users = await user_repository.aquery_active_users(
        session,
        search=query or None,
        limit=SEARCH_RESULT_LIMIT,
        condition=descriptor_for(resource_type).recipient(UserDB),
    )
    return [{"id": u.id, "username": u.username, "email": u.email, "name": u.name, "picture": u.picture} for u in users]
