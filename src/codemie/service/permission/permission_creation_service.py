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

from fastapi import status
from sqlalchemy.exc import IntegrityError

from codemie.core.models import CreatedByUser
from codemie.core.ability import Ability, Action
from codemie.rest_api.models.permission import Permission, PermissionCreateRequest, ResourceType, PrincipalType
from codemie.rest_api.security.user import User
from codemie.configs.authorized_apps_config import authorized_applications_config
from .permission_exceptions import PermissionAccessDenied, PermissionResourceNotFound, PermissionPrincipalNotFound
from .permission_base_service import PermissionBaseService


class PermissionCreationService(PermissionBaseService):
    """Create resource permission"""

    @classmethod
    def run(cls, request: PermissionCreateRequest, user: User) -> tuple[Permission, str]:
        try:
            cls._check_ability(resource_id=request.resource_id, resource_type=request.resource_type, user=user)

            cls._check_principal(principal_id=request.principal_id, principal_type=request.principal_type)

            permission = Permission(
                created_by=CreatedByUser(id=user.id, username=user.username, name=user.name), **request.model_dump()
            )
            permission.save()

            return permission, status.HTTP_201_CREATED
        except IntegrityError:
            return cls._handle_already_exists(request=request, user=user)

    @classmethod
    def _handle_already_exists(cls, request: PermissionCreateRequest, user: User) -> tuple[Permission, str]:
        """Assuming permission already exists, find and return it"""
        permission = cls._find_permission(
            resource_id=request.resource_id,
            resource_type=request.resource_type,
            principal_id=request.principal_id,
            user=user,
        )

        if not permission:
            raise PermissionResourceNotFound

        if not Ability(user).can(Action.WRITE, permission):
            raise PermissionAccessDenied

        return permission, status.HTTP_200_OK

    @classmethod
    def _check_ability(cls, resource_id: str, resource_type: ResourceType, user: User):
        """Check user ability to set permission for a given resource"""
        try:
            resource = resource_type.resource_class.get_by_id(id_=resource_id)
        except Exception:
            raise PermissionResourceNotFound

        if not Ability(user).can(Action.WRITE, resource):
            raise PermissionAccessDenied

    @classmethod
    def _check_principal(cls, principal_id: str, principal_type: PrincipalType):
        if (
            principal_type == PrincipalType.APPLICATION
            and principal_id not in authorized_applications_config.applications_names
        ):
            raise PermissionPrincipalNotFound

        if principal_type == PrincipalType.USER:
            raise NotImplementedError("User principal type is not supported yet")
