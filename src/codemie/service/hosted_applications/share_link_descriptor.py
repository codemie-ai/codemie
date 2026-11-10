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

"""Hosted applications' descriptor for the generic share-link service."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Text, bindparam, select

from codemie.core.hosted_applications.access import is_grant_remover, readable, refuses_sql
from codemie.core.hosted_applications.errors import HostedApplicationError
from codemie.rest_api.models.hosted_application import HostedApplications as App
from codemie.service.hosted_applications.grant_descriptor import is_owner
from codemie.service.permission.share_link_types import ShareLinkDescriptor

# The hosted recipient rule (``grant_recipient``) admits eligible internal users only.
LINK_REQUIRES_INTERNAL_USER = "Only internal users can open a share link"
ACTIVE = "active"


def share_link_not_available() -> HostedApplicationError:
    """One answer for every way a link can be unusable, so a probe learns nothing about which case it hit."""
    return HostedApplicationError("SHARE_LINK_NOT_AVAILABLE", "This share link is not available", 404)


def opener_refused() -> HostedApplicationError:
    return HostedApplicationError("ACCESS_DENIED", LINK_REQUIRES_INTERNAL_USER, 403)


def access_denied(message: str) -> HostedApplicationError:
    return HostedApplicationError("ACCESS_DENIED", message, 403)


def may_revoke(app: Any, actor: Any) -> bool:
    """The owner, or a platform admin or maintainer (``grant_remover``), revokes the link without suspending (S-17)."""
    created_by = getattr(app, "created_by", None)
    return is_owner(app, actor) or is_grant_remover(created_by.id if created_by else None, actor)


async def areadable(tx: Any, app_id: Any, opener_id: str) -> bool:
    """The opener already reads the active app (creator, manager, project member, Marketplace, any grant)."""
    principal = bindparam("viewer_id", opener_id, type_=Text)
    query = select(App.id).where(App.id == app_id, App.lifecycle_state == ACTIVE, readable(App.__table__, principal))
    return (await tx.session.execute(query)).first() is not None


async def alock(tx: Any, app_id: Any) -> App | None:
    """The active app that does not refuse ``share_on``, locked through ``tx.lock_app`` (no quota lock); else None."""
    return await tx.lock_app(
        select(App).where(App.id == app_id, App.lifecycle_state == ACTIVE, ~refuses_sql(App.__table__, "share_on"))
    )


# Owner rule, pre-check (refused while suspended), activity domain, audit attributes and recipient rule are the
# hosted grant descriptor's (``grant_descriptor.DESCRIPTOR``); only the link-specific rules are here.
DESCRIPTOR = ShareLinkDescriptor(
    event_created="hosted_application.share_link.created",
    event_revoked="hosted_application.share_link.revoked",
    areadable=areadable,
    alock=alock,
    project_of=lambda app: app.project,
    not_available=share_link_not_available,
    opener_refused=opener_refused,
    access_denied=access_denied,
    may_revoke=may_revoke,
)
