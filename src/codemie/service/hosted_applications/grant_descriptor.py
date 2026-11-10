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

"""Hosted applications' descriptor for the generic named-grant service."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from codemie.core.hosted_applications.access import grant_recipient, grant_remover, is_owned_by, suspend_refuses
from codemie.rest_api.models.hosted_application import HostedApplications
from codemie.service.activity.activity_models import ActivityDomain, ActivityEntityType
from codemie.service.permission.named_grant_types import NamedGrantDescriptor, ResolvedResource


def build_resolve_statement() -> sa.Select:
    """One SELECT of scalar subqueries on ``apps.id = :rid``: project, creator id and the removal right.

    Without an app row (an orphan grant) project and creator are NULL and only the administrator branch of
    ``grant_remover(None, actor)`` can hold.
    """
    apps = HostedApplications.__table__
    rid = sa.bindparam("rid", type_=apps.c.id.type)
    actor = sa.bindparam("actor", type_=sa.String)
    by_id = apps.c.id == rid
    project = sa.select(apps.c.project).where(by_id).scalar_subquery()
    creator = sa.select(apps.c.created_by["id"].as_string()).where(by_id).scalar_subquery()
    with_app = sa.exists(sa.select(apps.c.id).where(by_id, grant_remover(apps, actor)))
    may_remove = sa.or_(grant_remover(None, actor), with_app)
    return sa.select(project.label("project"), creator.label("creator_id"), may_remove.label("may_remove"))


def _resolved(row: Any) -> ResolvedResource:
    return ResolvedResource(project=row.project, creator_id=row.creator_id, may_remove=bool(row.may_remove))


def resolve(session: Any, resource_id: Any, actor: Any) -> ResolvedResource:
    row = session.execute(build_resolve_statement(), {"rid": resource_id, "actor": actor.id}).one()
    return _resolved(row)


async def aresolve(session: Any, resource_id: Any, actor: Any) -> ResolvedResource:
    row = (await session.execute(build_resolve_statement(), {"rid": resource_id, "actor": actor.id})).one()
    return _resolved(row)


def before_grant(app: Any) -> None:
    suspend_refuses(app, "share_on")


def audit_attributes(app_id: Any) -> dict[str, Any]:
    # Hosted link events carried ``app_id`` before the common tables; grant and link events keep it beside
    # resource_type/resource_id (the link service reads it from here).
    return {"app_id": str(app_id)}


def is_owner(app: Any, actor: Any) -> bool:
    """The eligible creator; ``actor`` is the platform copy of the user (``access.platform_user``)."""
    created_by = getattr(app, "created_by", None)
    return is_owned_by(created_by.id if created_by else None, actor)


DESCRIPTOR = NamedGrantDescriptor(
    owned_class_name="HostedApplications",
    activity_domain=ActivityDomain.HOSTED_APPLICATION,
    activity_entity_type=ActivityEntityType.HOSTED_APPLICATION,
    event_added="hosted_application.grant.added",
    event_removed="hosted_application.grant.removed",
    resolve=resolve,
    aresolve=aresolve,
    before_grant=before_grant,
    is_owner=is_owner,
    audit_attributes=audit_attributes,
    recipient=grant_recipient,
)
