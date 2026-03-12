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

from uuid import UUID
from typing import Optional, Any, Self
from sqlmodel import Field as SQLField, Column, Index, UniqueConstraint
from enum import Enum
from pydantic import BaseModel

from codemie.core.ability import Action, Owned
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.base import BaseModelWithSQLSupport, PydanticType
from codemie.rest_api.security.user import User
from codemie.rest_api.models.index import IndexInfo
from codemie.configs.logger import logger


class ResourceType(str, Enum):
    DATASOURCE = "datasource"

    @property
    def resource_class(self):
        """Find resource type class"""
        return self._lookup[self]

    @classmethod
    def type_for_instance(cls, instance: Any):
        """Find a ResourceType for class instance"""
        for key, value in cls._lookup.items():
            if isinstance(instance, value):
                return key

        raise ValueError(f"No matching ResourceType for instance of type {type(instance).__name__}")

    @classmethod
    @property
    def _lookup(cls):
        """Type to class mapping"""
        return {cls.DATASOURCE: IndexInfo}


class PrincipalType(str, Enum):
    APPLICATION = "application"
    USER = "user"


class Permission(BaseModelWithSQLSupport, Owned, table=True):
    """Model for storing permissions for resources, e.x. datasources or assistants"""

    __tablename__ = "permissions"
    __table_args__ = (
        Index("idx_permissions_resource", "resource_type", "resource_id"),
        Index("idx_permissions_principal", "principal_type", "principal_id"),
        UniqueConstraint(
            "resource_type", "resource_id", "principal_type", "principal_id", name="unique_resource_principal"
        ),
    )

    resource_type: ResourceType = SQLField(
        nullable=False, max_length=50, description="Type of resource (datasource, assistant, workflow, etc.)"
    )
    resource_id: UUID = SQLField(nullable=False, description="ID of the resource")
    principal_type: PrincipalType = SQLField(
        nullable=False, max_length=20, description="Type of principal (user, application)"
    )
    principal_id: str = SQLField(nullable=False, max_length=255, description="ID/name/email of the principal")
    permission_level: Action = SQLField(
        default=Action.READ, nullable=False, description="Permission level (READ, WRITE, DELETE)"
    )

    created_by: Optional[CreatedByUser] = SQLField(default=None, sa_column=Column(PydanticType(CreatedByUser)))

    def is_owned_by(self, user: User):
        return self.created_by.id == user.id

    def is_managed_by(self, user: User):
        return self.created_by.id == user.id

    def is_shared_with(self, user: User):
        return self.created_by.id == user.id

    @classmethod
    def get_for(cls, user: User, instance: Any, action: Action) -> Optional[Self]:
        """Get a permission for the given user and instance"""
        try:
            return Permission.get_by_fields(
                {
                    "resource_id": instance.id,
                    "resource_type": ResourceType.type_for_instance(instance),
                    "principal_id": user.username,
                }
            )
        except ValueError:
            return None
        except Exception as e:
            logger.error(f"Error checking permission existence: {e}")
            return None

    @classmethod
    def exists_for(cls, user: User, instance: Any, action: Action) -> bool:
        """Check if a permission exists for the given user, instance, and action"""
        return cls.get_for(user, instance, action) is not None


class PermissionCreateRequest(BaseModel):
    resource_type: ResourceType
    resource_id: str
    principal_type: PrincipalType
    principal_id: str
    permission_level: Action
