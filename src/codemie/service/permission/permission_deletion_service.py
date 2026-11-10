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

from sqlmodel import Session

from codemie.core.ability import Ability, Action
from codemie.rest_api.models.permission import Permission, ResourceType
from codemie.rest_api.security.user import User
from codemie.service.permission.named_grant_service import revoke_permission
from codemie.service.permission.named_grant_types import descriptor_for, is_named_grant
from .permission_exceptions import PermissionAccessDenied


class PermissionDeletionService:
    """Delete resource permissions"""

    @classmethod
    def run(cls, permission_id: str, user: User) -> None:
        permission = Permission.get_by_id(id_=permission_id)

        if is_named_grant(permission.resource_type, permission.principal_type):
            cls._delete_named_grant(permission, user)
            return

        if permission.resource_type not in ResourceType._lookup:
            raise PermissionAccessDenied

        if not Ability(user).can(Action.WRITE, permission):
            raise PermissionAccessDenied

        permission.delete()

    @classmethod
    def _delete_named_grant(cls, permission: Permission, user: User) -> None:
        """Decided by the type's descriptor from the actor's users row; removed with audit in one transaction."""
        descriptor = descriptor_for(permission.resource_type)
        with Session(Permission.get_engine()) as session:
            resolved = descriptor.resolve(session, permission.resource_id, user)
            if not resolved.may_remove:
                raise PermissionAccessDenied
            revoke_permission(user, permission, session, project=resolved.project)
            session.commit()
