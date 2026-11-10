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

"""The ASSISTANT descriptor of the shared named-grant module (replaces !4256's own share service rules)."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.permission import Permission, ResourceType
from codemie.rest_api.security.user import User
from codemie.service.activity.activity_models import ActivityDomain, ActivityEntityType, AssistantEvent
from codemie.service.permission.named_grant_types import descriptor_for

ASSISTANT_ID = "00000000-0000-0000-0000-000000000001"
SHARE = "codemie.service.assistant.assistant_share_service.AssistantShareService"


def _assistant(created_by="owner-1", creator="system", project="proj"):
    return SimpleNamespace(
        id=ASSISTANT_ID,
        project=project,
        created_by=SimpleNamespace(id=created_by) if created_by else None,
        creator=creator,
        is_owned_by=lambda user: user.id == (created_by or creator),
    )


def _actor_row(eligible=True, is_admin=False, is_maintainer=False):
    return SimpleNamespace(eligible=eligible, is_admin=is_admin, is_maintainer=is_maintainer)


class _Result:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class _Session:
    def __init__(self, assistant, actor_row):
        self.assistant, self.actor_row = assistant, actor_row
        self.statements = []

    def get(self, model, key):
        self.statements.append(("get", model.__name__, key))
        return self.assistant

    def execute(self, statement):
        self.statements.append(statement)
        return _Result(self.actor_row)


class _AsyncSession(_Session):
    async def get(self, model, key):
        return super().get(model, key)

    async def execute(self, statement):
        return super().execute(statement)


def test_assistant_descriptor_fields_keep_the_assistant_event_names():
    descriptor = descriptor_for(ResourceType.ASSISTANT)
    assert descriptor.owned_class_name == "Assistant"
    assert descriptor.activity_domain is ActivityDomain.ASSISTANT
    assert descriptor.activity_entity_type is ActivityEntityType.ASSISTANT
    assert descriptor.event_added == AssistantEvent.GRANT_ADDED
    assert descriptor.event_removed == AssistantEvent.GRANT_REMOVED
    assert descriptor.not_owner_message == "Only the assistant owner can share or revoke access"


def test_assistant_audit_keeps_assistant_id():
    assert descriptor_for(ResourceType.ASSISTANT).audit_attributes(ASSISTANT_ID) == {"assistant_id": ASSISTANT_ID}


def test_assistant_ids_are_text_so_the_key_casts_the_uuid_column():
    key = descriptor_for(ResourceType.ASSISTANT).resource_key(Permission.resource_id)
    assert str(key.compile(dialect=postgresql.dialect())) == "CAST(permissions.resource_id AS TEXT)"
    hosted = descriptor_for(ResourceType.HOSTED_APPLICATION).resource_key(Permission.resource_id)
    assert hosted is Permission.resource_id


def test_before_grant_refuses_a_personal_integration():
    with patch(f"{SHARE}.assistant_uses_personal_integration", return_value=True):
        with pytest.raises(ExtendedHTTPException) as err:
            descriptor_for(ResourceType.ASSISTANT).before_grant(_assistant())
    assert err.value.code == 400
    assert "personal integration" in err.value.message.lower()


def test_before_grant_allows_project_integrations():
    with patch(f"{SHARE}.assistant_uses_personal_integration", return_value=False):
        descriptor_for(ResourceType.ASSISTANT).before_grant(_assistant())


def test_owner_is_the_assistant_creator():
    descriptor = descriptor_for(ResourceType.ASSISTANT)
    assert descriptor.is_owner(_assistant(), User(id="owner-1", username="o"))
    assert not descriptor.is_owner(_assistant(), User(id="other", username="x"))


@pytest.mark.parametrize(
    "assistant, actor_id, row, may_remove",
    [
        (_assistant(), "owner-1", _actor_row(), True),
        (_assistant(created_by=None, creator="legacy-1"), "legacy-1", _actor_row(), True),
        (_assistant(), "admin", _actor_row(is_admin=True), True),
        (_assistant(), "maint", _actor_row(is_maintainer=True), True),
        (_assistant(), "other", _actor_row(), False),
        # The remover rule reads the users row: a deactivated admin or creator may not remove.
        (_assistant(), "admin", _actor_row(eligible=False, is_admin=True), False),
        (_assistant(), "owner-1", _actor_row(eligible=False), False),
        (_assistant(), "ghost", None, False),
        # Orphan grant (assistant gone): only an administrator.
        (None, "owner-1", _actor_row(), False),
        (None, "admin", _actor_row(is_admin=True), True),
    ],
)
def test_resolve_applies_the_grant_remover_rule(assistant, actor_id, row, may_remove):
    session = _Session(assistant, row)
    resolved = descriptor_for(ResourceType.ASSISTANT).resolve(session, ASSISTANT_ID, User(id=actor_id, username="a"))
    assert resolved.may_remove is may_remove
    assert resolved.project == (assistant.project if assistant else None)


def test_resolve_reads_the_users_row_not_request_roles():
    session = _Session(_assistant(), _actor_row())
    admin = User(id="other", username="other")
    admin.is_admin = True
    assert descriptor_for(ResourceType.ASSISTANT).resolve(session, ASSISTANT_ID, admin).may_remove is False
    sql = str(session.statements[-1].compile(dialect=postgresql.dialect()))
    assert "users.is_admin" in sql and "users.is_maintainer" in sql and "users.is_active" in sql


def test_resolve_takes_the_permission_uuid_and_looks_up_the_text_id():
    from uuid import UUID

    session = _Session(_assistant(), _actor_row())
    descriptor_for(ResourceType.ASSISTANT).resolve(session, UUID(ASSISTANT_ID), User(id="owner-1", username="o"))
    assert session.statements[0] == ("get", "Assistant", ASSISTANT_ID)


@pytest.mark.asyncio
async def test_aresolve_matches_resolve():
    session = _AsyncSession(_assistant(), _actor_row())
    resolved = await descriptor_for(ResourceType.ASSISTANT).aresolve(
        session, ASSISTANT_ID, User(id="owner-1", username="o")
    )
    assert resolved.may_remove and resolved.creator_id == "owner-1"


def test_actor_statement_is_one_select_by_id():
    session = _Session(_assistant(), _actor_row())
    descriptor_for(ResourceType.ASSISTANT).resolve(session, ASSISTANT_ID, User(id="owner-1", username="o"))
    assert isinstance(session.statements[-1], sa.Select)
