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

"""Generic revocable share links (``share_links``) of any type in ``SHARE_LINK_TYPES``.

Every link type is a named-grant type: the owner rule, the pre-check, the activity domain and entity type, the audit
attributes and the recipient rule are read from the type's grant descriptor (``descriptor_for``); the link descriptor
adds only the link events, the open's lock and readability checks, the link answers and the revoke rule.

One live link per resource. A revoked row stays as a tombstone, so its token is never live again. Lookup by token
binds only its SHA-256. Recipients are ``permissions`` rows with ``grant_source='link'`` and ``source_link_id``
= the link id, written by ``named_grant_service`` with the link's owner as the row's creator (the opener stays the
principal and the audit actor). Every change writes a strict activity event in the caller's transaction; events
never carry the token. Nothing here commits: the caller owns the transaction and holds the resource locked.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from codemie.core.models import CreatedByUser
from codemie.rest_api.models.permission import ResourceType, ShareLink
from codemie.rest_api.models.user_management import UserDB
from codemie.service.activity.activity_models import ActivityEventCreate
from codemie.service.activity.activity_repository import activity_event_repository
from codemie.service.permission import named_grant_service
from codemie.service.permission.named_grant_types import NamedGrantDescriptor, descriptor_for
from codemie.service.permission.share_link_types import ShareLinkDescriptor, link_descriptor_for


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---- statements ---------------------------------------------------------------------------------------------------


def live_link_statement(resource_type: ResourceType, resource_id: Any) -> sa.Select:
    return sa.select(ShareLink).where(
        ShareLink.resource_type == resource_type,
        ShareLink.resource_id == resource_id,
        ShareLink.revoked_at.is_(None),
    )


def live_link_by_token_statement(resource_type: ResourceType, token: str) -> sa.Select:
    """``WHERE token_sha256 = :digest AND revoked_at IS NULL``: the token itself is never a bound parameter."""
    return sa.select(ShareLink).where(
        ShareLink.token_sha256 == token_digest(token),
        ShareLink.resource_type == resource_type,
        ShareLink.revoked_at.is_(None),
    )


def _created_by(actor: Any) -> CreatedByUser:
    return CreatedByUser(id=actor.id, username=actor.username, name=actor.name)


def _event(
    grant: NamedGrantDescriptor,
    event_type: str,
    link: ShareLink,
    project: str | None,
    actor: Any,
) -> ActivityEventCreate:
    return ActivityEventCreate(
        domain=grant.activity_domain,
        event_type=event_type,
        entity_type=grant.activity_entity_type,
        entity_id=str(link.resource_id),
        actor_id=actor.id,
        attributes={
            "link_id": str(link.id),
            "resource_type": getattr(link.resource_type, "value", link.resource_type),
            "resource_id": str(link.resource_id),
            "project": project,
            "actor_id": actor.id,
            "actor_username": actor.username,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            **grant.audit_attributes(link.resource_id),
        },
    )


def _check_owner(link_rules: ShareLinkDescriptor, grant: NamedGrantDescriptor, resource: Any, actor: Any) -> None:
    if not grant.is_owner(resource, actor):
        raise link_rules.access_denied(grant.not_owner_message)


# ---- reads ----------------------------------------------------------------------------------------------------------


async def aget_live(session: AsyncSession, resource_type: ResourceType, resource_id: Any) -> ShareLink | None:
    return (await session.execute(live_link_statement(resource_type, resource_id))).scalar_one_or_none()


async def alive_by_token(session: AsyncSession, resource_type: ResourceType, token: str) -> ShareLink | None:
    return (await session.execute(live_link_by_token_statement(resource_type, token))).scalar_one_or_none()


async def areload(session: AsyncSession, link_id: UUID) -> ShareLink | None:
    statement = sa.select(ShareLink).where(ShareLink.id == link_id).execution_options(populate_existing=True)
    return (await session.execute(statement)).scalar_one_or_none()


# ---- owner changes ------------------------------------------------------------------------------------------------


async def aget_or_create_live(
    session: AsyncSession, resource_type: ResourceType, resource_id: Any, actor: Any, project: str | None
) -> ShareLink:
    """The live link, or a new one with one created event. The caller authorizes (``acreate`` for owners)."""
    existing = await aget_live(session, resource_type, resource_id)
    if existing is not None:
        return existing
    token = secrets.token_urlsafe(32)
    link = ShareLink(
        id=uuid4(),
        resource_type=resource_type,
        resource_id=resource_id,
        token=token,
        token_sha256=token_digest(token),
        created_by=_created_by(actor),
        created_at=datetime.now(timezone.utc),
    )
    session.add(link)
    await session.flush()
    event_type = link_descriptor_for(resource_type).event_created
    await activity_event_repository.async_insert_many_strict(
        [_event(descriptor_for(resource_type), event_type, link, project, actor)], session
    )
    return link


async def acreate(
    session: AsyncSession, resource_type: ResourceType, resource: Any, actor: Any, project: str | None
) -> ShareLink:
    """Get or create the live link of ``resource`` (held locked by the caller), for its owner only.

    The owner rule and the pre-check are the type's grant rules (``is_owner``, ``before_grant``): whoever may share
    the resource by name may create its link, and a refusal of named sharing refuses the link too.
    """
    grant = descriptor_for(resource_type)
    _check_owner(link_descriptor_for(resource_type), grant, resource, actor)
    grant.before_grant(resource)
    return await aget_or_create_live(session, resource_type, resource.id, actor, project)


async def arevoke(
    session: AsyncSession, resource_type: ResourceType, resource_id: Any, actor: Any, project: str | None
) -> UUID | None:
    """The one revoke step: tombstone the live link and remove the grants it made, each audited.

    None (and nothing written) when no link is live. The caller authorizes: ``arevoke_checked`` for the link's
    route; deleting the resource revokes as the deleting manager.
    """
    link = await aget_live(session, resource_type, resource_id)
    if link is None:
        return None
    link.revoked_at = datetime.now(timezone.utc)
    link.revoked_by = _created_by(actor)
    await session.flush()
    event_type = link_descriptor_for(resource_type).event_revoked
    await activity_event_repository.async_insert_many_strict(
        [_event(descriptor_for(resource_type), event_type, link, project, actor)], session
    )
    await named_grant_service.aremove_link_grants(
        actor=actor,
        resource_type=resource_type,
        resource_id=resource_id,
        link_id=link.id,
        project=project,
        session=session,
    )
    return link.id


async def arevoke_checked(
    session: AsyncSession, resource_type: ResourceType, resource: Any, actor: Any, project: str | None
) -> UUID | None:
    """Revoke the live link of ``resource`` (held locked by the caller) when the type's ``may_revoke`` admits the
    actor (the grant descriptor's owner rule when it is not set); allowed while suspended. The actor is recorded as
    the revoker."""
    link_rules = link_descriptor_for(resource_type)
    grant = descriptor_for(resource_type)
    allowed = link_rules.may_revoke or grant.is_owner
    if not allowed(resource, actor):
        raise link_rules.access_denied(grant.not_owner_message)
    return await arevoke(session, resource_type, resource.id, actor, project)


# ---- open -----------------------------------------------------------------------------------------------------------


async def aopen(session: AsyncSession, resource_type: ResourceType, token: str, opener: Any, *, context: Any) -> Any:
    """Open a live link: the opener becomes a view-only recipient; returns the resource id.

    Openers who meet the type's recipient rule only (the grant descriptor's ``recipient``, else ``opener_refused``):
    the people a link can admit are the people the type may grant by name. Every unusable link answers
    ``not_available``. Someone who
    can already read the resource gets it with nothing written. Otherwise the resource is locked through the type's
    ``alock``, the link re-read under that lock, and one link grant written (an existing grant of either source
    stays). ``context`` is what the type's checks need (hosted: its transaction).
    """
    descriptor = link_descriptor_for(resource_type)
    eligible = sa.select(UserDB.id).where(UserDB.id == opener.id, descriptor_for(resource_type).recipient(UserDB))
    if (await session.execute(eligible)).first() is None:
        raise descriptor.opener_refused()
    link = await alive_by_token(session, resource_type, token)
    if link is None:
        raise descriptor.not_available()
    if await descriptor.areadable(context, link.resource_id, opener.id):
        return link.resource_id
    resource = await descriptor.alock(context, link.resource_id)
    if resource is None:
        raise descriptor.not_available()
    link = await areload(session, link.id)
    if link is None or link.revoked_at is not None:
        raise descriptor.not_available()
    await named_grant_service.alink_grant(
        session,
        resource_type,
        resource,
        opener,
        link.id,
        descriptor.project_of(resource),
        link.created_by,
    )
    return link.resource_id
