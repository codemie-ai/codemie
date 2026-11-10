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

"""Assistant sharing with named users, on the shared named-grant module (``named_grant_service``).

The rules are the shared ones: eligible grantees only, the live grant rule, preloaded grants (fail closed without a
preload), audited removal through the generic permissions API by ``grant_remover``. What stays assistant-specific is
here and in the ``ASSISTANT`` descriptor: the personal-integration refusal and the REST response shapes.
"""

from __future__ import annotations


from sqlalchemy import Text, cast, select
from sqlmodel import Session

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.assistant import Assistant, AssistantConfiguration, AssistantRequest
from codemie.rest_api.models.base import BaseModelWithSQLSupport
from codemie.rest_api.models.permission import Permission, ResourceType
from codemie.rest_api.models.settings import Settings, SettingType
from codemie.rest_api.security.user import User
from codemie.service.permission import named_grant_service
from codemie.service.permission.named_grant_types import descriptor_for

SEARCH_RESULT_LIMIT = named_grant_service.SEARCH_RESULT_LIMIT
RESOURCE_TYPE = ResourceType.ASSISTANT


class AssistantShareService:
    """Grant and revoke per-user READ access to an assistant through the shared named-grant module."""

    @classmethod
    def grant(cls, owner: User, assistant: Assistant, user_ids: list[str]) -> list[dict[str, str | None]]:
        cls._require_owner(owner, assistant)
        with Session(BaseModelWithSQLSupport.get_engine()) as session:
            locked = cls._locked(session, assistant.id)
            named_grant_service.grant(
                session,
                resource_type=RESOURCE_TYPE,
                resource=locked,
                owner=owner,
                project=locked.project,
                user_ids=user_ids,
            )
            session.commit()
        return cls.list_shared_with(assistant.id)

    @classmethod
    def revoke(cls, owner: User, assistant: Assistant, user_ids: list[str]) -> list[dict[str, str | None]]:
        cls._require_owner(owner, assistant)
        with Session(BaseModelWithSQLSupport.get_engine()) as session:
            locked = cls._locked(session, assistant.id)
            named_grant_service.revoke(
                session,
                resource_type=RESOURCE_TYPE,
                resource=locked,
                owner=owner,
                project=locked.project,
                user_ids=user_ids,
            )
            session.commit()
        return cls.list_shared_with(assistant.id)

    @classmethod
    def remove_all_grants(
        cls, actor: User | None, assistant_id: str, project: str | None = None, session: Session | None = None
    ) -> None:
        """Delete every grant on an assistant (e.g. on assistant delete), one audit event per row.

        actor None means a system-initiated delete: recorded with actor_id None and username "system".
        With a caller-supplied session nothing is committed here; the caller owns the transaction.
        """
        if session is None:
            with Session(BaseModelWithSQLSupport.get_engine()) as own_session:
                named_grant_service.remove_all_grants(actor, RESOURCE_TYPE, assistant_id, project, own_session)
                own_session.commit()
            return
        named_grant_service.remove_all_grants(actor, RESOURCE_TYPE, assistant_id, project, session)

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
    def list_shared_with(cls, assistant_id: str) -> list[dict[str, str | None]]:
        with Session(BaseModelWithSQLSupport.get_engine()) as session:
            return named_grant_service.list_shared_with(session, RESOURCE_TYPE, assistant_id)

    @classmethod
    async def search_sharable_users(cls, query: str) -> list[dict[str, str | None]]:
        from codemie.clients.postgres import get_async_session

        async with get_async_session() as session:
            return await named_grant_service.search_sharable_users(session, RESOURCE_TYPE, query)

    # ---- helpers ----------------------------------------------------------------------------------------------------

    @staticmethod
    def _locked(session: Session, assistant_id: str) -> Assistant:
        """The assistant row locked for the grant change; a concurrently deleted one is 404."""
        statement = select(Assistant).where(Assistant.id == assistant_id).with_for_update(key_share=True)
        locked = session.execute(statement).scalars().first()
        if locked is None:
            raise ExtendedHTTPException(code=404, message="Assistant not found")
        return locked

    @staticmethod
    def _require_owner(owner: User, assistant: Assistant) -> None:
        descriptor = descriptor_for(RESOURCE_TYPE)
        if not descriptor.is_owner(assistant, owner):
            raise ExtendedHTTPException(code=403, message=descriptor.not_owner_message)
