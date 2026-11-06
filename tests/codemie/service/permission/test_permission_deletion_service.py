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
from unittest.mock import patch

import pytest

from codemie.core.ability import Action
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.permission import Permission, PrincipalType, ResourceType
from codemie.rest_api.security.user import User
from codemie.service.permission import PermissionAccessDenied
from codemie.service.permission.permission_deletion_service import PermissionDeletionService

SHARE = "codemie.service.assistant.assistant_share_service.AssistantShareService.revoke_permission"
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


def _run(permission: Permission, user: User):
    with (
        patch(GET, return_value=permission),
        patch(SHARE) as revoke,
        patch.object(Permission, "delete") as plain_delete,
    ):
        try:
            PermissionDeletionService.run(permission_id=permission.id, user=user)
        finally:
            run_mocks = (revoke, plain_delete)
    return run_mocks


@pytest.mark.parametrize(
    "user",
    [
        _user("grantor"),
        _user("admin", admin=True),
    ],
    ids=["grantor", "admin"],
)
def test_assistant_row_delegates_to_share_service(user):
    permission = _permission(ResourceType.ASSISTANT)
    revoke, plain_delete = _run(permission, user)
    revoke.assert_called_once_with(user, permission)
    plain_delete.assert_not_called()


def test_assistant_row_denied_for_other_user():
    permission = _permission(ResourceType.ASSISTANT)
    with patch(GET, return_value=permission), patch(SHARE) as revoke, patch.object(Permission, "delete") as plain:
        with pytest.raises(PermissionAccessDenied):
            PermissionDeletionService.run(permission_id=permission.id, user=_user("other"))
    revoke.assert_not_called()
    plain.assert_not_called()


def test_datasource_row_uses_plain_delete():
    permission = _permission(ResourceType.DATASOURCE)
    revoke, plain_delete = _run(permission, _user("grantor"))
    plain_delete.assert_called_once_with()
    revoke.assert_not_called()
