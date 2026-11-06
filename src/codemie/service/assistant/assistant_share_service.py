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

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Text, cast, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import Session

from codemie.configs import config
from codemie.core.ability import Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import CreatedByUser
from codemie.repository.user_repository import UserRepository
from codemie.rest_api.models.assistant import Assistant, AssistantConfiguration, AssistantRequest
from codemie.rest_api.models.base import BaseModelWithSQLSupport
from codemie.rest_api.models.permission import Permission, PrincipalType, ResourceType
from codemie.rest_api.models.settings import Settings, SettingType
from codemie.rest_api.security.user import User
from codemie.service.activity.activity_models import (
    ActivityDomain,
    ActivityEntityType,
    ActivityEventCreate,
    AssistantEvent,
)
from codemie.service.activity.activity_repository import activity_event_repository

SEARCH_RESULT_LIMIT = 20
_DELETED_COLUMNS = (Permission.principal_type, Permission.principal_id, Permission.permission_level)


def _enum_value(value):
    return getattr(value, "value", value)


def _enum_name(value):
    return getattr(value, "name", value)


class AssistantShareService:
    """Grant and revoke per-user READ access to an assistant."""

    @classmethod
    def grant(cls, owner: User, assistant: Assistant, user_ids: list[str]) -> list[dict[str, str | None]]:
        cls._require_owner(owner, assistant)
        user_ids = list(dict.fromkeys(user_ids))
        if cls.assistant_uses_personal_integration(assistant):
            raise ExtendedHTTPException(
                code=400,
                message="Assistant uses a personal integration and cannot be shared with users. "
                "Replace personal integrations with project integrations before sharing.",
            )
        repo = UserRepository()
        with Session(BaseModelWithSQLSupport.get_engine()) as session:
            for uid in user_ids:
                db_user = repo.get_by_id(session, uid)
                if not db_user:
                    raise ExtendedHTTPException(
                        code=404,
                        message=f"User {uid} not found",
                    )
                if db_user.user_type == config.EXTERNAL_USER_TYPE or not db_user.is_active or db_user.deleted_at:
                    raise ExtendedHTTPException(
                        code=400,
                        message=f"User {uid} is not a sharable internal user",
                    )
            now = datetime.now()
            for uid in user_ids:
                stmt = (
                    insert(Permission)
                    .values(
                        id=str(uuid4()),
                        date=now,
                        update_date=now,
                        resource_type=ResourceType.ASSISTANT,
                        resource_id=assistant.id,
                        principal_type=PrincipalType.USER,
                        principal_id=uid,
                        permission_level=Action.READ,
                        created_by=CreatedByUser(id=owner.id, username=owner.username, name=owner.name),
                    )
                    .on_conflict_do_nothing(constraint="unique_resource_principal")
                    .returning(Permission.id)
                )
                # No row returned means the grant already existed (repeat or concurrent request).
                if session.execute(stmt).scalar_one_or_none() is not None:
                    cls._audit(session, owner, assistant.id, assistant.project, uid, None, Action.READ)
            session.commit()
        return cls.list_shared_with(assistant.id)

    @classmethod
    def revoke(cls, owner: User, assistant: Assistant, user_ids: list[str]) -> list[dict[str, str | None]]:
        cls._require_owner(owner, assistant)
        with Session(BaseModelWithSQLSupport.get_engine()) as session:
            for uid in dict.fromkeys(user_ids):
                stmt = (
                    delete(Permission)
                    .where(
                        Permission.resource_type == ResourceType.ASSISTANT,
                        Permission.resource_id == assistant.id,
                        Permission.principal_type == PrincipalType.USER,
                        Permission.principal_id == uid,
                    )
                    .returning(*_DELETED_COLUMNS)
                )
                cls._audit_deleted(session, owner, assistant.id, assistant.project, session.execute(stmt).first())
            session.commit()
        return cls.list_shared_with(assistant.id)

    @classmethod
    def revoke_permission(cls, actor: User, permission: Permission) -> None:
        """Delete one ASSISTANT permission row with audit. The caller authorizes."""
        assistant = Assistant.find_by_id(str(permission.resource_id))
        project = assistant.project if assistant else None
        with Session(BaseModelWithSQLSupport.get_engine()) as session:
            stmt = delete(Permission).where(Permission.id == permission.id).returning(*_DELETED_COLUMNS)
            cls._audit_deleted(session, actor, str(permission.resource_id), project, session.execute(stmt).first())
            session.commit()

    @classmethod
    def remove_all_grants(
        cls, actor: User | None, assistant_id: str, project: str | None = None, session: Session | None = None
    ) -> None:
        """Delete every live grant on an assistant (e.g. on assistant delete), one audit event per row.

        actor None means a system-initiated delete: recorded with actor_id None and username "system".
        With a caller-supplied session nothing is committed here; the caller owns the transaction.
        """
        if session is None:
            with Session(BaseModelWithSQLSupport.get_engine()) as own_session:
                cls.remove_all_grants(actor, assistant_id, project, own_session)
                own_session.commit()
            return
        stmt = (
            delete(Permission)
            .where(Permission.resource_type == ResourceType.ASSISTANT, Permission.resource_id == assistant_id)
            .returning(*_DELETED_COLUMNS)
        )
        for row in session.execute(stmt).all():
            cls._audit_deleted(session, actor, assistant_id, project, row)

    @classmethod
    def has_named_grants(cls, assistant_id: str) -> bool:
        with Session(BaseModelWithSQLSupport.get_engine()) as session:
            stmt = (
                select(Permission.id)
                # Compare as text like the other named-grant lookups: a str bound to the UUID column fails off Postgres
                .where(
                    Permission.resource_type == ResourceType.ASSISTANT,
                    cast(Permission.resource_id, Text) == assistant_id,
                )
                .limit(1)
            )
            return session.execute(stmt).first() is not None

    @classmethod
    def ensure_no_personal_integration_if_shared(
        cls, assistant_id: str, assistant_like: Assistant | AssistantRequest | AssistantConfiguration
    ) -> None:
        """Reject a config with personal integrations for an assistant shared with named users.

        Inactive when user management is off: named grants are not honored then, so nothing is shared.
        """
        if (
            config.ENABLE_USER_MANAGEMENT
            and cls.has_named_grants(assistant_id)
            and cls.assistant_uses_personal_integration(assistant_like)
        ):
            raise ExtendedHTTPException(
                code=400,
                message="Personal integrations cannot be used in a shared assistant. "
                "Remove sharing first, or switch to project integrations.",
            )

    @staticmethod
    def assistant_uses_personal_integration(
        assistant_like: Assistant | AssistantRequest | AssistantConfiguration,
    ) -> bool:
        """True if any referenced stored integration is personal; payload setting_type is never trusted."""
        refs = []
        for toolkit in getattr(assistant_like, "toolkits", None) or []:
            refs.append(toolkit.settings)
            refs.extend(tool.settings for tool in toolkit.tools or [])
        for server in getattr(assistant_like, "mcp_servers", None) or []:
            refs.extend((server.settings, server.mcp_connect_auth_token))
        setting_ids = [ref.id for ref in refs if ref is not None and ref.id]
        # Bedrock assistants keep no toolkits; their AWS credentials hang off these two fields
        for data, field in (
            (getattr(assistant_like, "bedrock", None), "bedrock_aws_settings_id"),
            (getattr(assistant_like, "bedrock_agentcore_runtime", None), "aws_settings_id"),
        ):
            if data is not None and getattr(data, field, None):
                setting_ids.append(getattr(data, field))
        setting_ids = list(dict.fromkeys(setting_ids))
        if not setting_ids:
            return False
        return any(s.setting_type == SettingType.USER for s in Settings.get_by_ids(setting_ids))

    @classmethod
    def list_for_owner(cls, owner: User, assistant: Assistant) -> list[dict[str, str | None]]:
        cls._require_owner(owner, assistant)
        return cls.list_shared_with(assistant.id)

    @classmethod
    async def search_sharable_users_for_owner(
        cls, owner: User, assistant: Assistant, query: str
    ) -> list[dict[str, str | None]]:
        cls._require_owner(owner, assistant)
        return await cls.search_sharable_users(query)

    @classmethod
    def _audit_deleted(cls, session: Session, actor: User, assistant_id: str, project, row) -> None:
        if row is not None:
            cls._audit(
                session,
                actor,
                assistant_id,
                project,
                row.principal_id,
                row.permission_level,
                None,
                principal_type=row.principal_type,
            )

    @staticmethod
    def _audit(
        session: Session,
        actor: User,
        assistant_id: str,
        project,
        principal_id: str,
        before,
        after,
        principal_type=PrincipalType.USER,
    ) -> None:
        activity_event_repository.insert_strict(
            ActivityEventCreate(
                domain=ActivityDomain.ASSISTANT,
                event_type=AssistantEvent.GRANT_ADDED if after is not None else AssistantEvent.GRANT_REMOVED,
                entity_type=ActivityEntityType.ASSISTANT,
                entity_id=str(assistant_id),
                actor_id=actor.id if actor else None,
                attributes={
                    "actor_id": actor.id if actor else None,
                    "actor_username": actor.username if actor else "system",
                    "principal_type": _enum_name(principal_type),
                    "principal_id": principal_id,
                    "assistant_id": str(assistant_id),
                    "project": project,
                    "before": _enum_value(before),
                    "after": _enum_value(after),
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                },
            ),
            session,
        )

    @classmethod
    def list_shared_with(cls, assistant_id: str) -> list[dict[str, str | None]]:
        perms = Permission.get_all_by_fields(
            {
                "resource_type": ResourceType.ASSISTANT,
                "resource_id": assistant_id,
                "principal_type": PrincipalType.USER,
            },
            order_by="date",
        )
        repo = UserRepository()
        result = []
        with Session(BaseModelWithSQLSupport.get_engine()) as session:
            for perm in perms:
                db_user = repo.get_by_id(session, perm.principal_id)
                if db_user:
                    result.append(
                        {
                            "id": db_user.id,
                            "username": db_user.username,
                            "email": db_user.email,
                            "name": db_user.name,
                            "picture": getattr(db_user, "picture", None),
                        }
                    )
                else:
                    # Orphan grant (user gone): listed so the owner can revoke it; has_named_grants counts it.
                    uid = perm.principal_id
                    result.append({"id": uid, "username": uid, "email": None, "name": None, "picture": None})
        return sorted(result, key=lambda u: u.get("name") or u["username"])

    @classmethod
    async def search_sharable_users(cls, query: str) -> list[dict[str, str | None]]:
        from codemie.clients.postgres import get_async_session
        from codemie.repository.user_repository import user_repository as _user_repo

        async with get_async_session() as session:
            users = await _user_repo.aquery_active_users(
                session,
                search=query or None,
                exclude_user_type=config.EXTERNAL_USER_TYPE,
            )
        # ponytail: repository has no limit arg, so the cap is applied after the query; add LIMIT if lists grow large.
        return [
            {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "name": u.name,
                "picture": getattr(u, "picture", None),
            }
            for u in users[:SEARCH_RESULT_LIMIT]
        ]

    @staticmethod
    def _require_owner(owner: User, assistant: Assistant) -> None:
        if not assistant.is_owned_by(owner):
            raise ExtendedHTTPException(
                code=403,
                message="Only the assistant owner can share or revoke access",
            )
