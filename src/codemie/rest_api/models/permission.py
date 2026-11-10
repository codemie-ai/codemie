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

from datetime import datetime
from uuid import UUID
from typing import Optional, Any, Callable, Self
import sqlalchemy as sa
from sqlmodel import Field as SQLField, Column, Index, Session, SQLModel, UniqueConstraint, select
from enum import Enum
from pydantic import BaseModel

from codemie.core.ability import Action, Owned, levels_covering
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.base import BaseModelWithSQLSupport, PydanticType
from codemie.rest_api.security.user import User
from codemie.rest_api.models.index import IndexInfo
from codemie.configs import config
from codemie.configs.logger import logger
from codemie.rest_api.models.user_management import UserDB


class ResourceType(str, Enum):
    DATASOURCE = "datasource"
    ASSISTANT = "assistant"
    HOSTED_APPLICATION = "hosted_application"

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
        """Type to class mapping of APPLICATION-principal permissions; named-grant types are not here."""
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
        sa.CheckConstraint(
            "(grant_source IS NULL AND source_link_id IS NULL)"
            " OR (grant_source = 'name' AND source_link_id IS NULL)"
            " OR (grant_source = 'link' AND source_link_id IS NOT NULL)",
            name="permissions_grant_source_check",
        ),
    )

    resource_type: ResourceType = SQLField(
        sa_column=Column(
            sa.Enum(ResourceType, schema="codemie"),
            nullable=False,
        ),
        description="Type of resource (datasource, assistant, workflow, etc.)",
    )
    resource_id: UUID = SQLField(nullable=False, description="ID of the resource")
    principal_type: PrincipalType = SQLField(
        sa_column=Column(
            sa.Enum(PrincipalType, schema="codemie"),
            nullable=False,
        ),
        description="Type of principal (user, application)",
    )
    principal_id: str = SQLField(nullable=False, max_length=255, description="ID/name/email of the principal")
    permission_level: Action = SQLField(
        sa_column=Column(
            sa.Enum(Action, schema="codemie"),
            nullable=False,
        ),
        default=Action.READ,
        description="Permission level (READ, WRITE, DELETE)",
    )
    grant_source: str | None = SQLField(
        default=None,
        sa_column=Column(sa.String(16), nullable=True),
        description="How a named grant was made: 'name' or 'link'; null for older rows",
    )
    source_link_id: UUID | None = SQLField(
        default=None,
        sa_column=Column(
            sa.Uuid,
            # RESTRICT: a link row never takes its grants with it; they are removed explicitly, with audit.
            sa.ForeignKey("share_links.id", ondelete="RESTRICT", name="permissions_source_link_id_fkey"),
            nullable=True,
        ),
        description="Share link (share_links.id) a 'link' grant came from; null otherwise",
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
        """Get an APPLICATION permission for the given user and instance covering the action"""
        try:
            resource_type = ResourceType.type_for_instance(instance)
            statement = select(Permission).where(
                Permission.resource_id == instance.id,
                Permission.resource_type == resource_type,
                Permission.principal_type == PrincipalType.APPLICATION,
                Permission.principal_id == user.username,
                Permission.permission_level.in_(levels_covering(action)),
            )
            with Session(cls.get_engine()) as session:
                return session.exec(statement).first()
        except ValueError:
            return None
        except Exception as e:
            logger.error(f"Error checking permission existence: {e}")
            return None

    @classmethod
    def exists_for(cls, user: User, instance: Any, action: Action) -> bool:
        """Check if a permission exists for the given user, instance, and action"""
        return cls.get_for(user, instance, action) is not None


class ShareLink(SQLModel, table=True):
    """One revocable share link of a resource, for every link-sharing resource type (``SHARE_LINK_TYPES``).

    Keyed like ``permissions`` (``resource_type``, ``resource_id``) and, like it, without a foreign key to the
    resource: each type deletes its rows explicitly. At most one live link per resource; a revoked row stays as a
    tombstone, so its token is never live again. Link grants are ``permissions`` rows whose ``source_link_id`` is
    this id.
    """

    __tablename__ = "share_links"
    __table_args__ = (
        Index(
            "uq_share_links_live",
            "resource_type",
            "resource_id",
            unique=True,
            postgresql_where=sa.text("revoked_at IS NULL"),
        ),
    )

    id: UUID = SQLField(sa_column=Column(sa.Uuid, nullable=False, primary_key=True))
    resource_type: ResourceType = SQLField(
        sa_column=Column(sa.Enum(ResourceType, schema="codemie"), nullable=False),
    )
    resource_id: UUID = SQLField(nullable=False)
    token: str = SQLField(sa_column=Column(sa.Text, nullable=False))
    token_sha256: str = SQLField(sa_column=Column(sa.Text, nullable=False, unique=True))
    created_by: CreatedByUser = SQLField(sa_column=Column(PydanticType(CreatedByUser), nullable=False))
    created_at: datetime = SQLField(sa_column=Column(sa.DateTime(timezone=True), nullable=False))
    revoked_at: datetime | None = SQLField(default=None, sa_column=Column(sa.DateTime(timezone=True), nullable=True))
    revoked_by: CreatedByUser | None = SQLField(
        default=None, sa_column=Column(PydanticType(CreatedByUser), nullable=True)
    )


class PermissionCreateRequest(BaseModel):
    resource_type: ResourceType
    resource_id: str
    principal_type: PrincipalType
    principal_id: str
    permission_level: Action


def _same_key(column: Any) -> Any:
    return column


def live_grants(
    resource_type: ResourceType,
    principal_id_expr: sa.ColumnElement[Any],
    recipient: Callable[[Any], sa.ColumnElement[bool]],
) -> sa.Select:
    """The only statement of a live USER grant: (resource_id, permission_level).

    Counts only for a grantee whose ``users`` row meets the type's ``recipient`` policy (its descriptor's; hosted
    and assistants: eligible internal user) while ENABLE_USER_MANAGEMENT is on; otherwise empty.
    """
    columns = sa.select(Permission.resource_id, Permission.permission_level)
    if not config.ENABLE_USER_MANAGEMENT:
        return columns.where(sa.false())
    return columns.join(UserDB, UserDB.id == Permission.principal_id).where(
        Permission.principal_type == PrincipalType.USER,
        Permission.resource_type == resource_type,
        Permission.principal_id == principal_id_expr,
        recipient(UserDB),
    )


def grant_exists(
    resource_type: ResourceType,
    resource_id_expr: sa.ColumnElement[Any],
    principal_id_expr: sa.ColumnElement[Any],
    action: Action,
    *,
    recipient: Callable[[Any], sa.ColumnElement[bool]],
    resource_key: Callable[[Any], Any] = _same_key,
) -> sa.Exists:
    """EXISTS over live_grants for the resource at a level covering the action.

    ``resource_key`` turns ``permissions.resource_id`` (uuid) into the resource table's id type; for uuid ids it is
    the column itself, so ``unique_resource_principal`` serves the probe (no cast).
    """
    return sa.exists(
        live_grants(resource_type, principal_id_expr, recipient).where(
            resource_key(Permission.resource_id) == resource_id_expr,
            Permission.permission_level.in_(levels_covering(action)),
        )
    )
