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

"""Assistants' descriptor for the generic named-grant service.

Assistant ids are text while ``permissions.resource_id`` is a uuid: SQL comparing the two columns goes through
``resource_key`` (a cast to text); Python values are converted by the service.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.user_management import UserDB, eligible_internal_user
from codemie.service.activity.activity_models import ActivityDomain, ActivityEntityType, AssistantEvent
from codemie.service.permission.named_grant_types import NamedGrantDescriptor, ResolvedResource

PERSONAL_INTEGRATION = (
    "Assistant uses a personal integration and cannot be shared with users. "
    "Replace personal integrations with project integrations before sharing."
)


def actor_statement(actor_id: str) -> sa.Select:
    """The actor's users row: eligibility and platform roles (never request roles)."""
    return sa.select(
        eligible_internal_user(UserDB).label("eligible"),
        UserDB.is_admin,
        UserDB.is_maintainer,
    ).where(UserDB.id == actor_id)


def creator_id(assistant: Any) -> str | None:
    if assistant is None:
        return None
    created_by = getattr(assistant, "created_by", None)
    return created_by.id if created_by else getattr(assistant, "creator", None)


def _resolved(assistant: Any, actor_row: Any, actor: Any) -> ResolvedResource:
    """``grant_remover`` for assistants: an eligible platform admin or maintainer, or the assistant's creator.

    Without an assistant row (an orphan grant) only the administrator branch applies.
    """
    creator = creator_id(assistant)
    eligible = bool(actor_row is not None and actor_row.eligible)
    admin = bool(actor_row is not None and (actor_row.is_admin or actor_row.is_maintainer))
    return ResolvedResource(
        project=assistant.project if assistant is not None else None,
        creator_id=creator,
        may_remove=eligible and (admin or (creator is not None and creator == actor.id)),
    )


def resolve(session: Any, resource_id: Any, actor: Any) -> ResolvedResource:
    assistant = session.get(Assistant, str(resource_id))
    return _resolved(assistant, session.execute(actor_statement(actor.id)).first(), actor)


async def aresolve(session: Any, resource_id: Any, actor: Any) -> ResolvedResource:
    assistant = await session.get(Assistant, str(resource_id))
    return _resolved(assistant, (await session.execute(actor_statement(actor.id))).first(), actor)


def before_grant(assistant: Any) -> None:
    """Refuse sharing an assistant whose stored integrations include a personal one."""
    from codemie.service.assistant.assistant_share_service import AssistantShareService  # assistant rules live there

    if AssistantShareService.assistant_uses_personal_integration(assistant):
        raise ExtendedHTTPException(code=400, message=PERSONAL_INTEGRATION)


def is_owner(assistant: Any, actor: Any) -> bool:
    return bool(assistant.is_owned_by(actor))


def _text_key(column: Any) -> Any:
    return sa.cast(column, sa.Text)


def _audit_attributes(resource_id: Any) -> dict[str, Any]:
    # Assistant grant events carried ``assistant_id`` before the move; it stays beside resource_type/resource_id.
    return {"assistant_id": str(resource_id)}


DESCRIPTOR = NamedGrantDescriptor(
    owned_class_name="Assistant",
    activity_domain=ActivityDomain.ASSISTANT,
    activity_entity_type=ActivityEntityType.ASSISTANT,
    event_added=AssistantEvent.GRANT_ADDED,
    event_removed=AssistantEvent.GRANT_REMOVED,
    resolve=resolve,
    aresolve=aresolve,
    before_grant=before_grant,
    is_owner=is_owner,
    not_owner_message="Only the assistant owner can share or revoke access",
    resource_key=_text_key,
    audit_attributes=_audit_attributes,
    recipient=eligible_internal_user,
)
