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

from __future__ import annotations

from datetime import datetime
from typing import Any

from cachetools import TTLCache

from codemie.configs.config import config
from codemie.configs.logger import logger
from codemie.service.security.token_providers.base_provider import TokenProviderException

_AUDIT_SOURCE = "token_exchange"
_AUDIT_POLICY_ERROR_MESSAGE = "Token cache audit requirement not met"


class TMSTokenStore:
    """TMS-backed token store with local TTLCache fallback.

    All enterprise imports are deferred inside method bodies.
    No enterprise type crosses this class boundary — callers pass raw values.
    """

    def __init__(self, tms: Any, audit_context_provider: Any) -> None:
        self._tms = tms
        self._audit_ctx = audit_context_provider
        self._fallback: TTLCache[str, str] = TTLCache(maxsize=config.TOKEN_CACHE_MAX_SIZE, ttl=config.TOKEN_CACHE_TTL)

    def get(self, user_id: str, auth_config_id: str) -> str | None:
        from codemie_enterprise.mcp_auth import (
            ReAuthenticationRequired,
            TMSAuditError,
            TMSCryptoError,
            TMSPersistenceError,
            TMSUnavailable,
            TokenNotFound,
            TokenRefreshError,
        )

        fallback_key = f"{user_id}:{auth_config_id}"

        try:
            with self._audit_ctx.context(source=_AUDIT_SOURCE, correlation_id=auth_config_id):
                token_data = self._tms.retrieve(user_id, auth_config_id)
            return token_data.access_token
        except TokenNotFound:
            return self._fallback.get(fallback_key)
        except (ReAuthenticationRequired, TokenRefreshError):
            return None
        except (TMSUnavailable, TMSPersistenceError, TMSCryptoError):
            logger.warning(
                f"TMS unavailable for token retrieve, using fallback cache "
                f"for user_id={user_id} auth_config_id={auth_config_id}"
            )
            return self._fallback.get(fallback_key)
        except TMSAuditError as exc:
            raise TokenProviderException(
                message=_AUDIT_POLICY_ERROR_MESSAGE,
                details=f"TMSAuditError: {exc}",
            ) from exc

    def put(
        self,
        user_id: str,
        auth_config_id: str,
        *,
        access_token: str,
        expires_at: datetime | None,
        refresh_token: str | None = None,
        refresh_metadata_kwargs: dict[str, Any] | None = None,
        scope: str | None = None,
    ) -> None:
        from codemie_enterprise.mcp_auth import (
            OAuth2RefreshMetadata,
            OAuth2TokenData,
            TMSAuditError,
            TMSCryptoError,
            TMSPersistenceError,
            TMSUnavailable,
        )

        fallback_key = f"{user_id}:{auth_config_id}"

        if expires_at is None:
            self._fallback[fallback_key] = access_token
            return

        refresh_metadata = OAuth2RefreshMetadata(**refresh_metadata_kwargs) if refresh_metadata_kwargs else None
        token_data = OAuth2TokenData(
            access_token=access_token,
            expires_at=expires_at,
            refresh_token=refresh_token,
            refresh_metadata=refresh_metadata,
            scope=scope,
        )
        if refresh_metadata:
            masked_secret = "***" if refresh_metadata.client_secret else None
            masked = {**refresh_metadata.model_dump(), "client_secret": masked_secret}
            logger.debug(f"Storing token with refresh_metadata={masked}")
        else:
            logger.debug("Storing token without refresh_metadata")
        try:
            with self._audit_ctx.context(source=_AUDIT_SOURCE, correlation_id=auth_config_id):
                self._tms.store(user_id, auth_config_id, token_data)
        except TMSAuditError as exc:
            raise TokenProviderException(
                message=_AUDIT_POLICY_ERROR_MESSAGE,
                details=f"TMSAuditError: {exc}",
            ) from exc
        except (TMSUnavailable, TMSPersistenceError, TMSCryptoError):
            logger.warning(
                f"TMS unavailable for token store, writing to fallback cache "
                f"for user_id={user_id} auth_config_id={auth_config_id}"
            )
            self._fallback[fallback_key] = access_token

    def invalidate(self, user_id: str, auth_config_id: str) -> None:
        from codemie_enterprise.mcp_auth import (
            TMSAuditError,
            TMSCryptoError,
            TMSPersistenceError,
            TMSUnavailable,
        )

        fallback_key = f"{user_id}:{auth_config_id}"

        try:
            with self._audit_ctx.context(source=_AUDIT_SOURCE, correlation_id=auth_config_id):
                self._tms.delete(user_id, auth_config_id)
        except TMSAuditError as exc:
            raise TokenProviderException(
                message=_AUDIT_POLICY_ERROR_MESSAGE,
                details=f"TMSAuditError: {exc}",
            ) from exc
        except (TMSUnavailable, TMSPersistenceError, TMSCryptoError):
            pass

        self._fallback.pop(fallback_key, None)

    def invalidate_all_for_user(self, user_id: str) -> None:
        from codemie_enterprise.mcp_auth import (
            TMSAuditError,
            TMSCryptoError,
            TMSPersistenceError,
            TMSUnavailable,
        )

        try:
            with self._audit_ctx.context(source=_AUDIT_SOURCE, correlation_id=None):
                self._tms.delete_all_for_user(user_id)
        except TMSAuditError as exc:
            raise TokenProviderException(
                message=_AUDIT_POLICY_ERROR_MESSAGE,
                details=f"TMSAuditError: {exc}",
            ) from exc
        except (TMSUnavailable, TMSPersistenceError, TMSCryptoError):
            pass

        prefix = f"{user_id}:"
        keys_to_remove = [k for k in self._fallback if k.startswith(prefix)]
        for k in keys_to_remove:
            self._fallback.pop(k, None)
