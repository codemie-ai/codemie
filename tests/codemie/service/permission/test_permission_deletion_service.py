# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

import uuid
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.ability import Action
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.permission import Permission, PrincipalType, ResourceType
from codemie.rest_api.security.user import User
from codemie.service.permission import PermissionAccessDenied
from codemie.service.permission import permission_deletion_service as deletion_module
from codemie.service.permission.named_grant_types import ResolvedResource
from codemie.service.permission.permission_deletion_service import PermissionDeletionService

GET = "codemie.service.permission.permission_deletion_service.Permission.get_by_id"


def _user(user_id: str, admin: bool = False) -> User:
    user = User(id=user_id, username=user_id)
    user.is_admin = admin  # ENV=local forces is_admin at construction; pin it for the test
    return user


def _permission(resource_type: ResourceType) -> Permission:
    return Permission(
        id=str(uuid.uuid4()),
        resource_type=resource_type,
        resource_id=uuid.uuid4(),
        principal_type=PrincipalType.USER,
        principal_id="uid-1",
        permission_level=Action.READ,
        created_by=CreatedByUser(id="grantor", username="grantor", name="Grantor"),
    )


def _run(permission: Permission, user: User, may_remove: bool):
    """Run the delete with the ASSISTANT descriptor's resolve answering ``may_remove``."""
    resolve = MagicMock(return_value=ResolvedResource(project="proj", creator_id="grantor", may_remove=may_remove))
    session = MagicMock()
    session_cm = MagicMock()
    session_cm.__enter__.return_value = session
    descriptor = deletion_module.descriptor_for(ResourceType.ASSISTANT)
    with (
        patch(GET, return_value=permission),
        patch.object(deletion_module, "descriptor_for", return_value=MagicMock(resolve=resolve)),
        patch.object(deletion_module, "Session", return_value=session_cm),
        patch.object(deletion_module.Permission, "get_engine", return_value=MagicMock()),
        patch.object(deletion_module, "revoke_permission") as revoke,
        patch.object(deletion_module, "Ability") as ability,
        patch.object(Permission, "delete") as plain_delete,
    ):
        try:
            PermissionDeletionService.run(permission_id=permission.id, user=user)
        finally:
            mocks = SimpleMocks(resolve, revoke, ability, plain_delete, session, descriptor)
    return mocks


class SimpleMocks:
    def __init__(self, resolve, revoke, ability, plain_delete, session, descriptor):
        self.resolve, self.revoke, self.ability = resolve, revoke, ability
        self.plain_delete, self.session, self.descriptor = plain_delete, session, descriptor


def test_assistant_row_is_decided_by_the_descriptor_and_revoked_with_audit():
    permission = _permission(ResourceType.ASSISTANT)
    user = _user("creator")
    mocks = _run(permission, user, may_remove=True)
    mocks.resolve.assert_called_once_with(mocks.session, permission.resource_id, user)
    mocks.revoke.assert_called_once_with(user, permission, mocks.session, project="proj")
    mocks.ability.assert_not_called()
    mocks.plain_delete.assert_not_called()
    mocks.session.commit.assert_called_once()


def test_assistant_row_refused_when_the_remover_rule_denies():
    """Rule change: the row's creator or a request-role admin no longer decides; ``grant_remover`` does."""
    permission = _permission(ResourceType.ASSISTANT)
    with pytest.raises(PermissionAccessDenied):
        _run(permission, _user("grantor", admin=True), may_remove=False)


def test_datasource_row_uses_plain_delete():
    permission = _permission(ResourceType.DATASOURCE)
    with (
        patch(GET, return_value=permission),
        patch.object(deletion_module, "revoke_permission") as revoke,
        patch.object(Permission, "delete") as plain_delete,
    ):
        PermissionDeletionService.run(permission_id=permission.id, user=_user("grantor"))
    plain_delete.assert_called_once_with()
    revoke.assert_not_called()
