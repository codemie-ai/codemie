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

import asyncio
import sys
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException, MCPAuthenticationRequiredException

from ._common import (
    CallbackPageError,
    MCPPostAuth401Result,
    _build_discovered_initiate_url,
    _build_recovery_initiate_url,
    _execution_context_attr,
    _is_discovered_auth_config_id,
)
from ._constants import _POST_AUTH_401_REFRESH_FAILURE_DESCRIPTIONS

_DEPS_MODULE = "codemie.enterprise.mcp_auth.dependencies"
_MCP_SERVER_FALLBACK_NAME = "MCP server"


def _deps() -> Any:
    return sys.modules[_DEPS_MODULE]


# ----------------------------- Insufficient Scope (403) -----------------------------


@dataclass(frozen=True)
class _InsufficientScopeContext:
    auth_config: Any
    auth_config_id: Any
    discovered_auth_config_id: Any
    mcp_config_id: Any
    mcp_config_name: Any
    user_id: Any
    session_binding_hash: Any
    conversation_id: Any
    workflow_execution_id: Any
    server_url: Any
    token_data: Any


def _extract_insufficient_scope_context(server_config: Any, execution_context: Any | None) -> _InsufficientScopeContext:
    raw_auth_config = getattr(server_config, "auth_config", None)
    persisted_auth_config_id = raw_auth_config.get("id") if isinstance(raw_auth_config, dict) else None
    discovered_auth_config = _execution_context_attr(execution_context, "oauth2_auth_config")
    discovered_auth_config_id = _execution_context_attr(execution_context, "oauth2_auth_config_id")
    mcp_config_id = getattr(server_config, "mcp_config_id", None)
    return _InsufficientScopeContext(
        auth_config=discovered_auth_config or raw_auth_config,
        auth_config_id=discovered_auth_config_id or persisted_auth_config_id,
        discovered_auth_config_id=discovered_auth_config_id,
        mcp_config_id=mcp_config_id,
        mcp_config_name=getattr(server_config, "mcp_config_name", None) or mcp_config_id or _MCP_SERVER_FALLBACK_NAME,
        user_id=_execution_context_attr(execution_context, "user_id"),
        session_binding_hash=_execution_context_attr(execution_context, "session_binding_hash"),
        conversation_id=_execution_context_attr(execution_context, "conversation_id"),
        workflow_execution_id=_execution_context_attr(execution_context, "workflow_execution_id"),
        server_url=getattr(server_config, "url", None),
        token_data=_execution_context_attr(execution_context, "oauth2_token_data"),
    )


def build_mcp_insufficient_scope_auth_exception(
    *,
    status_code: int,
    www_authenticate_header: str | None,
    server_config: Any,
    execution_context: Any | None,
) -> MCPAuthenticationRequiredException | None:
    """Build auth-required recovery exception for post-auth 403 insufficient_scope responses."""
    if not _deps().is_mcp_auth_enabled():
        return None

    try:
        from codemie_enterprise.mcp_auth import (
            RecoveryRequest,
            build_insufficient_scope_recovery,
            parse_insufficient_scope_challenge,
        )
    except ImportError:
        return None
    challenge = parse_insufficient_scope_challenge(status_code, www_authenticate_header)
    if challenge is None:
        return None

    ctx = _extract_insufficient_scope_context(server_config, execution_context)
    confidential_client_secret_available = _scope_recovery_confidential_client_secret_available(
        auth_config=ctx.auth_config,
        auth_config_id=ctx.auth_config_id,
        discovered_auth_config_id=ctx.discovered_auth_config_id,
    )
    authorization_server_metadata = _deps()._resolve_insufficient_scope_authorization_server_metadata(
        server_config=server_config,
        challenge=challenge,
    )
    request = RecoveryRequest(
        status_code=status_code,
        www_authenticate_header=www_authenticate_header,
        user_id=ctx.user_id,
        mcp_config_id=ctx.mcp_config_id,
        mcp_config_name=ctx.mcp_config_name,
        auth_config_id=ctx.auth_config_id,
        token_storage_auth_config_id=ctx.auth_config_id,
        session_binding_hash=ctx.session_binding_hash,
        conversation_id=ctx.conversation_id,
        workflow_execution_id=ctx.workflow_execution_id,
        server_url=ctx.server_url,
        auth_config=ctx.auth_config,
        token_data=ctx.token_data,
        confidential_client_secret_available=confidential_client_secret_available,
        authorization_server_metadata=authorization_server_metadata,
        authorization_server_metadata_validated=authorization_server_metadata is not None,
    )
    decision = build_insufficient_scope_recovery(request)
    if decision is None:
        return None

    server_payload = _map_scope_recovery_decision(decision, workflow_execution_id=request.workflow_execution_id)
    return MCPAuthenticationRequiredException({"error": "authentication_required", "servers": [server_payload]})


def _resolve_insufficient_scope_authorization_server_metadata(
    *,
    server_config: Any,
    challenge: Any,
) -> dict[str, Any] | None:
    server_url = getattr(server_config, "url", None)
    discovery_header = _build_insufficient_scope_discovery_header(challenge)
    if not isinstance(server_url, str) or not server_url.strip() or discovery_header is None:
        return None
    deps = _deps()
    if deps._mcp_auth_trust_policy_service is None:
        return None
    try:
        # Resolve DB-backed config on the caller's loop so the bridged coroutine
        # never awaits the main-loop-bound async engine from a worker thread loop.
        allowed_private_networks = deps.read_mcp_auth_discovery_private_network_allowlist_config_sync()
        trust_policy_service = deps.build_static_trust_policy_service(
            deps.read_mcp_auth_trusted_as_domains_config_sync()
        )
        return _run_coroutine_sync(
            _resolve_insufficient_scope_authorization_server_metadata_async(
                server_url=server_url,
                server_name=getattr(server_config, "mcp_config_name", None) or _MCP_SERVER_FALLBACK_NAME,
                www_authenticate_header=discovery_header,
                trust_policy_service=trust_policy_service,
                allowed_private_networks=allowed_private_networks,
            )
        )
    except Exception as exc:
        logger.warning(f"MCP auth 403 resource metadata discovery failed: {type(exc).__name__}")
        return None


def _build_insufficient_scope_discovery_header(challenge: Any) -> str | None:
    resource_metadata = getattr(challenge, "resource_metadata_url_internal", None)
    if not isinstance(resource_metadata, str) or not resource_metadata.strip():
        return None
    params = []
    scope = getattr(challenge, "scope", None)
    if isinstance(scope, str) and scope:
        params.append(f"scope={_quote_www_authenticate_param(scope)}")
    params.append(f"resource_metadata={_quote_www_authenticate_param(resource_metadata)}")
    return f"Bearer {', '.join(params)}"


def _quote_www_authenticate_param(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


async def _resolve_insufficient_scope_authorization_server_metadata_async(
    *,
    server_url: str,
    server_name: str,
    www_authenticate_header: str,
    trust_policy_service: Any,
    allowed_private_networks: tuple[str, ...],
) -> dict[str, Any] | None:
    try:
        from codemie_enterprise.mcp_auth import (
            discover_authorization_server_metadata,
            discover_protected_resource_metadata,
        )
    except ImportError:
        return None

    prm_result = await discover_protected_resource_metadata(
        server_name=server_name,
        mcp_resource_url=server_url,
        www_authenticate_header=www_authenticate_header,
        trust_policy_service=trust_policy_service,
        allowed_private_networks=allowed_private_networks,
    )
    selected_as = getattr(prm_result, "selected_authorization_server", None)
    if getattr(prm_result, "status", None) != "discovered" or not isinstance(selected_as, str) or not selected_as:
        return None
    as_result = await discover_authorization_server_metadata(
        selected_as,
        allowed_private_networks=allowed_private_networks,
    )
    if getattr(as_result, "status", None) != "discovered":
        return None
    return {
        "issuer": as_result.issuer,
        "authorization_endpoint": as_result.authorization_endpoint,
        "token_endpoint": as_result.token_endpoint,
    }


def _run_coroutine_sync(coroutine: Any) -> Any:
    try:
        asyncio.get_running_loop()
        in_running_loop = True
    except RuntimeError:
        in_running_loop = False

    if in_running_loop:
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(lambda: asyncio.run(coroutine)).result()
    return asyncio.run(coroutine)


def _map_scope_recovery_decision(decision: Any, *, workflow_execution_id: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mcp_config_id": decision.mcp_config_id,
        "mcp_config_name": decision.mcp_config_name,
        "mcp_server_name": decision.mcp_config_name,
        "auth_config_id": _public_scope_recovery_auth_config_id(decision),
        "auth_type": "oauth2",
        "as_hostname": decision.as_hostname,
        "status": decision.status,
        "error": decision.error,
        "reason": decision.reason,
        "guidance": decision.guidance,
        "attempts_used": decision.attempts_used,
        "attempts_remaining": decision.attempts_remaining,
    }
    optional_fields = {
        "scope": decision.scope,
        "required_scopes": list(decision.required_scopes),
        "requested_scopes": list(decision.requested_scopes),
        "resource_metadata": decision.resource_metadata,
        "resource_metadata_redacted": decision.resource_metadata_redacted,
        "error_description": decision.error_description,
        "action": decision.action,
        "action_label": decision.action_label,
        "recovery_flow_id": decision.recovery_flow_id,
    }
    for key, value in optional_fields.items():
        if value is not None and value != []:
            payload[key] = value
    if decision.recovery_flow_id is not None and workflow_execution_id is None:
        payload["initiate_url"] = _build_recovery_initiate_url(decision.recovery_flow_id)
    return {key: value for key, value in payload.items() if value is not None}


def _scope_recovery_confidential_client_secret_available(
    *,
    auth_config: Any,
    auth_config_id: str | None,
    discovered_auth_config_id: str | None,
) -> bool | None:
    client_type = _auth_config_client_type(auth_config)
    if client_type != "confidential":
        return None
    if _is_discovered_auth_config_id(discovered_auth_config_id or auth_config_id):
        return _auth_config_has_nonblank_client_secret(auth_config)
    if not auth_config_id:
        return False
    deps = _deps()
    try:
        mcp_config = deps._load_callback_mcp_config(auth_config_id)
        raw_auth_config = deps._load_raw_callback_oauth_config(mcp_config, auth_config_id)
        deps._decrypt_callback_client_secret(raw_auth_config, None, auth_config_id)
        return True
    except (CallbackPageError, ExtendedHTTPException, ValueError, TypeError):
        logger.warning(
            f"MCP auth 403 recovery confidential client secret unavailable for auth_config_id={auth_config_id}"
        )
        return False


def _auth_config_client_type(auth_config: Any) -> str | None:
    if isinstance(auth_config, Mapping):
        value = auth_config.get("client_type")
    else:
        value = getattr(auth_config, "client_type", None)
    return value if isinstance(value, str) else None


def _auth_config_has_nonblank_client_secret(auth_config: Any) -> bool:
    if isinstance(auth_config, Mapping):
        value = auth_config.get("client_secret")
    else:
        value = getattr(auth_config, "client_secret", None)
    return isinstance(value, str) and bool(value.strip())


def _public_scope_recovery_auth_config_id(decision: Any) -> str | None:
    auth_config_id = getattr(decision, "auth_config_id", None)
    if (
        getattr(decision, "status", None) == "config_error"
        and getattr(decision, "error", None) == "scope_escalation_config_error"
        and _is_discovered_auth_config_id(auth_config_id)
    ):
        return None
    return auth_config_id if isinstance(auth_config_id, str) else None


# ----------------------------- Post-Auth 401 -----------------------------


def _build_post_auth_401_refresh_failure_result(
    *,
    decision: Any,
    server_config: Any,
    execution_context: Any | None,
    auth_config_id: str,
    failure_key: str,
    public_reason: str,
    exc: Exception,
) -> MCPPostAuth401Result:
    message, level = _POST_AUTH_401_REFRESH_FAILURE_DESCRIPTIONS[failure_key]
    _log_post_auth_401_refresh_failure(server_config, auth_config_id, public_reason, exc, level=level)
    return MCPPostAuth401Result(
        auth_exception=_build_post_auth_401_exception(
            decision=decision,
            server_config=server_config,
            execution_context=execution_context,
            reason=public_reason,
            error_context={"reason": message},
            auth_config_id=auth_config_id,
        )
    )


def _attempt_post_auth_401_refresh(
    *,
    user_id: str,
    auth_config_id: str,
    decision: Any,
    server_config: Any,
    execution_context: Any | None,
) -> tuple[Any, MCPPostAuth401Result | None]:
    from codemie_enterprise.mcp_auth import (
        ReAuthenticationRequired,
        TMSAuditError,
        TMSCryptoError,
        TMSPersistenceError,
        TMSUnavailable,
        TokenNotFound,
        TokenRefreshError,
    )

    handlers: tuple[tuple[type[Exception], str, str], ...] = (
        (TokenNotFound, "token_not_found", "token_not_found"),
        (ReAuthenticationRequired, "reauth_required_expired", "reauth_required"),
        (TMSCryptoError, "reauth_required_crypto", "reauth_required"),
        (TMSUnavailable, "tms_unavailable", "tms_unavailable"),
        (TMSPersistenceError, "tms_persistence_error", "tms_persistence_error"),
        (TMSAuditError, "tms_audit_persistence_error", "tms_persistence_error"),
        (TokenRefreshError, "token_refresh_error", "token_refresh_error"),
    )
    catchable: tuple[type[Exception], ...] = tuple(handler[0] for handler in handlers)

    deps = _deps()
    try:
        with deps._tms_audit_context("post_auth_invalid_token", correlation_id=auth_config_id):
            return deps._require_initialized_tms().force_refresh(user_id, auth_config_id), None
    except catchable as exc:
        for exc_type, failure_key, public_reason in handlers:
            if isinstance(exc, exc_type):
                return None, _build_post_auth_401_refresh_failure_result(
                    decision=decision,
                    server_config=server_config,
                    execution_context=execution_context,
                    auth_config_id=auth_config_id,
                    failure_key=failure_key,
                    public_reason=public_reason,
                    exc=exc,
                )
        raise


def _validate_post_auth_401_refreshed_token(
    refreshed: Any,
    *,
    decision: Any,
    server_config: Any,
    execution_context: Any | None,
    auth_config_id: str,
) -> MCPPostAuth401Result:
    access_token = getattr(refreshed, "access_token", None)
    token_type = getattr(refreshed, "token_type", "Bearer") or "Bearer"
    if not isinstance(access_token, str) or not access_token.strip():
        invalid_reason = "Credential refresh returned no usable OAuth2 access token."
    elif not isinstance(token_type, str) or token_type.lower() != "bearer":
        invalid_reason = "Credential refresh returned a non-Bearer OAuth2 token."
    else:
        return MCPPostAuth401Result(retry_auth_headers={"Authorization": f"Bearer {access_token.strip()}"})
    return MCPPostAuth401Result(
        auth_exception=_build_post_auth_401_exception(
            decision=decision,
            server_config=server_config,
            execution_context=execution_context,
            reason="refresh_result_invalid",
            error_context={"reason": invalid_reason},
            auth_config_id=auth_config_id,
        )
    )


def _build_post_auth_401_decision(server_config: Any, status_code: int, www_authenticate_header: str | None) -> Any:
    from codemie_enterprise.mcp_auth import PostAuth401Request, build_post_auth_401_decision

    return build_post_auth_401_decision(
        PostAuth401Request(
            status_code=status_code,
            www_authenticate_header=www_authenticate_header,
            mcp_config_name=getattr(server_config, "mcp_config_name", None)
            or getattr(server_config, "mcp_config_id", None)
            or _MCP_SERVER_FALLBACK_NAME,
        )
    )


def build_mcp_post_auth_401_result(
    *,
    status_code: int,
    www_authenticate_header: str | None,
    server_config: Any,
    execution_context: Any | None,
    refresh_allowed: bool = True,
) -> MCPPostAuth401Result | None:
    """Build retry headers or auth-required payload for post-auth tools/call 401 responses."""
    if not _deps().is_mcp_auth_enabled():
        return None

    try:
        decision = _build_post_auth_401_decision(server_config, status_code, www_authenticate_header)
    except ImportError:
        return None
    if decision is None:
        return None
    if not refresh_allowed:
        _log_post_auth_401_retry_rejected(server_config, execution_context)
        return MCPPostAuth401Result(
            auth_exception=_build_post_auth_401_exception(
                decision=decision,
                server_config=server_config,
                execution_context=execution_context,
                reason="retry_401_after_refresh",
                error_context={"reason": "retry_401_after_refresh"},
            )
        )
    if not getattr(decision, "should_refresh", False):
        return MCPPostAuth401Result(
            auth_exception=_build_post_auth_401_exception(
                decision=decision,
                server_config=server_config,
                execution_context=execution_context,
            )
        )

    identity = _resolve_post_auth_401_refresh_identity(server_config, execution_context)
    if identity["error"] is not None:
        return MCPPostAuth401Result(
            auth_exception=_build_post_auth_401_exception(
                decision=decision,
                server_config=server_config,
                execution_context=execution_context,
                reason=identity["error"],
                error_context=identity["error_context"],
                auth_config_id=identity["auth_config_id"],
                suppress_auth_config_id=identity.get("suppress_auth_config_id", False),
            )
        )

    refreshed, failure_result = _attempt_post_auth_401_refresh(
        user_id=identity["user_id"],
        auth_config_id=identity["auth_config_id"],
        decision=decision,
        server_config=server_config,
        execution_context=execution_context,
    )
    if failure_result is not None:
        return failure_result
    return _validate_post_auth_401_refreshed_token(
        refreshed,
        decision=decision,
        server_config=server_config,
        execution_context=execution_context,
        auth_config_id=identity["auth_config_id"],
    )


@dataclass(frozen=True)
class _PostAuth401Identity:
    user_id: Any
    configured_auth_config_id: Any
    configured_auth_type: Any
    token_delivery_method: Any
    discovered_auth_config_id: Any

    @property
    def auth_config_id(self) -> Any:
        return self.discovered_auth_config_id or self.configured_auth_config_id

    @property
    def has_identity_conflict(self) -> bool:
        return bool(
            self.configured_auth_config_id
            and self.discovered_auth_config_id
            and self.configured_auth_config_id != self.discovered_auth_config_id
        )

    @property
    def is_non_oauth2_configured(self) -> bool:
        return bool(
            self.configured_auth_config_id
            and self.configured_auth_type != "oauth2"
            and not self.discovered_auth_config_id
        )

    @property
    def is_env_token_delivery(self) -> bool:
        return bool(
            self.configured_auth_config_id
            and not self.discovered_auth_config_id
            and isinstance(self.token_delivery_method, str)
            and self.token_delivery_method.lower() == "env"
        )


def _extract_post_auth_401_identity(server_config: Any, execution_context: Any | None) -> _PostAuth401Identity:
    user_id = getattr(execution_context, "user_id", None) if execution_context is not None else None
    raw_auth_config = getattr(server_config, "auth_config", None)
    configured_auth_config_id = raw_auth_config.get("id") if isinstance(raw_auth_config, Mapping) else None
    configured_auth_type = raw_auth_config.get("auth_type") if isinstance(raw_auth_config, Mapping) else None
    raw_token_delivery = raw_auth_config.get("token_delivery") if isinstance(raw_auth_config, Mapping) else None
    token_delivery_method = raw_token_delivery.get("method") if isinstance(raw_token_delivery, Mapping) else None
    discovered_auth_config_id = (
        getattr(execution_context, "oauth2_auth_config_id", None) if execution_context is not None else None
    )
    return _PostAuth401Identity(
        user_id=user_id,
        configured_auth_config_id=configured_auth_config_id,
        configured_auth_type=configured_auth_type,
        token_delivery_method=token_delivery_method,
        discovered_auth_config_id=discovered_auth_config_id,
    )


def _post_auth_401_identity_result(
    error: str | None,
    reason: str,
    *,
    user_id: Any,
    auth_config_id: Any,
    suppress_auth_config_id: bool = False,
) -> dict[str, Any]:
    return {
        "error": error,
        "error_context": {"reason": reason} if reason else {},
        "user_id": user_id,
        "auth_config_id": auth_config_id,
        "suppress_auth_config_id": suppress_auth_config_id,
    }


def _resolve_post_auth_401_refresh_identity(server_config: Any, execution_context: Any | None) -> dict[str, Any]:
    identity = _extract_post_auth_401_identity(server_config, execution_context)
    if not isinstance(identity.user_id, str) or not identity.user_id.strip():
        return _post_auth_401_identity_result(
            "missing_user_id",
            "Missing user identity.",
            user_id=None,
            auth_config_id=identity.auth_config_id,
        )
    if identity.has_identity_conflict:
        logger.warning(
            "MCP post-auth OAuth2 identity conflict: "
            f"mcp_config_id={getattr(server_config, 'mcp_config_id', None)}, "
            f"configured_auth_config_id={identity.configured_auth_config_id}, "
            f"discovered_auth_config_id={identity.discovered_auth_config_id}"
        )
        return _post_auth_401_identity_result(
            "auth_identity_conflict",
            "Configured and discovered OAuth2 credential identities conflict.",
            user_id=identity.user_id,
            auth_config_id=None,
            suppress_auth_config_id=True,
        )
    if identity.is_non_oauth2_configured:
        return _post_auth_401_identity_result(
            "non_oauth2_identity",
            "Resolved credential is not OAuth2.",
            user_id=identity.user_id,
            auth_config_id=identity.configured_auth_config_id,
        )
    if identity.is_env_token_delivery:
        return _post_auth_401_identity_result(
            "env_token_delivery_unsupported",
            "OAuth2 env-delivered credentials cannot be force-refreshed.",
            user_id=identity.user_id,
            auth_config_id=identity.configured_auth_config_id,
        )
    if not isinstance(identity.auth_config_id, str) or not identity.auth_config_id.strip():
        return _post_auth_401_identity_result(
            "missing_auth_config_id",
            "Missing OAuth2 credential identity.",
            user_id=identity.user_id,
            auth_config_id=None,
        )
    return _post_auth_401_identity_result(
        None,
        "",
        user_id=identity.user_id,
        auth_config_id=identity.auth_config_id,
    )


def _log_post_auth_401_retry_rejected(server_config: Any, execution_context: Any | None) -> None:
    raw_auth_config = getattr(server_config, "auth_config", None)
    configured_auth_config_id = raw_auth_config.get("id") if isinstance(raw_auth_config, Mapping) else None
    discovered_auth_config_id = (
        getattr(execution_context, "oauth2_auth_config_id", None) if execution_context is not None else None
    )
    auth_config_id = discovered_auth_config_id or configured_auth_config_id
    logger.warning(
        "MCP post-auth retry returned 401 after OAuth2 refresh: "
        f"mcp_config_id={getattr(server_config, 'mcp_config_id', None)}, auth_config_id={auth_config_id}"
    )


def _log_post_auth_401_refresh_failure(
    server_config: Any,
    auth_config_id: str,
    reason: str,
    failure: Exception,
    *,
    level: str,
) -> None:
    message = (
        "MCP post-auth OAuth2 refresh failed: "
        f"mcp_config_id={getattr(server_config, 'mcp_config_id', None)}, "
        f"auth_config_id={auth_config_id}, reason={reason}, failure_type={type(failure).__name__}"
    )
    if level == "error":
        logger.error(message)
    elif level == "info":
        logger.info(message)
    else:
        logger.warning(message)


def _resolve_post_auth_401_auth_config_id(
    *,
    raw_auth_config: Any,
    execution_context: Any | None,
    auth_config_id: str | None,
    suppress_auth_config_id: bool,
) -> str | None:
    if suppress_auth_config_id:
        return None
    discovered = getattr(execution_context, "oauth2_auth_config_id", None) if execution_context is not None else None
    configured = raw_auth_config.get("id") if isinstance(raw_auth_config, Mapping) else None
    return auth_config_id or discovered or configured


def _resolve_post_auth_401_mcp_config_name(decision: Any, server_config: Any, mcp_config_id: Any) -> Any:
    return (
        getattr(decision, "mcp_config_name", None)
        or getattr(server_config, "mcp_config_name", None)
        or mcp_config_id
        or _MCP_SERVER_FALLBACK_NAME
    )


def _build_post_auth_401_exception(
    *,
    decision: Any,
    server_config: Any,
    execution_context: Any | None,
    reason: str | None = None,
    error_context: Mapping[str, Any] | None = None,
    auth_config_id: str | None = None,
    suppress_auth_config_id: bool = False,
) -> MCPAuthenticationRequiredException:
    raw_auth_config = getattr(server_config, "auth_config", None)
    resolved_auth_config_id = _resolve_post_auth_401_auth_config_id(
        raw_auth_config=raw_auth_config,
        execution_context=execution_context,
        auth_config_id=auth_config_id,
        suppress_auth_config_id=suppress_auth_config_id,
    )
    auth_type = _post_auth_401_auth_type(raw_auth_config, resolved_auth_config_id)
    mcp_config_id = getattr(server_config, "mcp_config_id", None)
    mcp_config_name = _resolve_post_auth_401_mcp_config_name(decision, server_config, mcp_config_id)
    server_payload: dict[str, Any] = {
        "mcp_config_id": mcp_config_id,
        "mcp_config_name": mcp_config_name,
        "mcp_server_name": mcp_config_name,
        "auth_config_id": resolved_auth_config_id,
        "auth_type": auth_type,
        "status": "session_expired",
        "error": "post_auth_401",
        "reason": reason or getattr(decision, "reason", "post_auth_401"),
        "action": "reauthenticate",
        "action_label": "Re-authenticate",
        "error_context": dict(error_context or getattr(decision, "error_context", {}) or {}),
    }
    workflow_execution_id = (
        getattr(execution_context, "workflow_execution_id", None) if execution_context is not None else None
    )
    if workflow_execution_id is None:
        initiate_url = _post_auth_401_initiate_url(resolved_auth_config_id, auth_type, server_config, execution_context)
        if initiate_url is not None:
            server_payload["initiate_url"] = initiate_url
    return MCPAuthenticationRequiredException(
        {
            "error": "authentication_required",
            "servers": [{key: value for key, value in server_payload.items() if value is not None}],
        }
    )


def _post_auth_401_auth_type(raw_auth_config: Any, auth_config_id: str | None) -> str:
    configured_auth_type = raw_auth_config.get("auth_type") if isinstance(raw_auth_config, Mapping) else None
    if isinstance(configured_auth_type, str) and configured_auth_type.strip():
        return configured_auth_type
    if _is_discovered_auth_config_id(auth_config_id) or auth_config_id:
        return "oauth2"
    return "unknown"


def _post_auth_401_initiate_url(
    auth_config_id: str | None, auth_type: str, server_config: Any, execution_context: Any | None
) -> str | None:
    if auth_type != "oauth2" or not auth_config_id:
        return None
    if _is_discovered_auth_config_id(auth_config_id):
        snapshot = _load_live_discovered_snapshot(server_config, execution_context, auth_config_id)
        discovered_flow_id = getattr(snapshot, "discovered_flow_id", None) if snapshot is not None else None
        return _build_discovered_initiate_url(discovered_flow_id) if isinstance(discovered_flow_id, str) else None
    return _deps().derive_initiate_url("oauth2")


def _load_live_discovered_snapshot(
    server_config: Any, execution_context: Any | None, auth_config_id: str | None
) -> Any | None:
    deps = _deps()
    discovered_flow_store = deps._mcp_auth_discovered_flow_store
    if execution_context is None or discovered_flow_store is None:
        return None
    user_id = getattr(execution_context, "user_id", None)
    session_binding_hash = getattr(execution_context, "session_binding_hash", None)
    mcp_config_id = getattr(server_config, "mcp_config_id", None)
    if not (
        isinstance(user_id, str)
        and user_id
        and isinstance(session_binding_hash, str)
        and session_binding_hash
        and isinstance(mcp_config_id, str)
        and mcp_config_id
    ):
        return None
    try:
        snapshot = discovered_flow_store.get_for_binding(user_id, session_binding_hash, mcp_config_id)
    except Exception as exc:
        logger.warning(f"MCP auth discovered re-auth snapshot unavailable after post-auth 401: {type(exc).__name__}")
        snapshot = None
    if getattr(snapshot, "discovered_auth_id", None) == auth_config_id:
        return snapshot
    return _rebuild_discovered_snapshot_from_exact_context(
        server_config=server_config,
        execution_context=execution_context,
        auth_config_id=auth_config_id,
    )


def _rebuild_discovered_snapshot_from_exact_context(
    *,
    server_config: Any,
    execution_context: Any,
    auth_config_id: str | None,
) -> Any | None:
    if not isinstance(auth_config_id, str) or not _is_discovered_auth_config_id(auth_config_id):
        return None
    raw_flow_config = getattr(execution_context, "oauth2_auth_config", None)
    if raw_flow_config is None:
        return None
    user_id = getattr(execution_context, "user_id", None)
    session_binding_hash = getattr(execution_context, "session_binding_hash", None)
    mcp_config_id = getattr(server_config, "mcp_config_id", None)
    if not (
        isinstance(user_id, str)
        and user_id
        and isinstance(session_binding_hash, str)
        and session_binding_hash
        and isinstance(mcp_config_id, str)
        and mcp_config_id
    ):
        return None
    deps = _deps()
    discovered_flow_store = deps._mcp_auth_discovered_flow_store
    if discovered_flow_store is None:
        return None
    try:
        from codemie_enterprise.mcp_auth import (
            DiscoveredOAuth2FlowConfig,
            DiscoveredOAuth2FlowSnapshot,
            create_discovered_flow_id,
        )

        flow_config = (
            raw_flow_config
            if isinstance(raw_flow_config, DiscoveredOAuth2FlowConfig)
            else DiscoveredOAuth2FlowConfig.model_validate(raw_flow_config)
        )
        redirect_uri, _, _ = deps.build_redirect_uri()
        snapshot = DiscoveredOAuth2FlowSnapshot(
            status="authentication_required",
            discovered_flow_id=create_discovered_flow_id(),
            discovered_auth_id=auth_config_id,
            mcp_config_id=mcp_config_id,
            mcp_config_name=getattr(server_config, "mcp_config_name", None) or mcp_config_id,
            user_id=user_id,
            session_binding_hash=session_binding_hash,
            canonical_resource=flow_config.resource,
            redirect_uri=redirect_uri,
            issuer=flow_config.issuer,
            selected_authorization_server=flow_config.issuer,
            as_hostname=urlsplit(flow_config.issuer).hostname,
            flow_config=flow_config,
        )
        discovered_flow_store.store(snapshot)
        return snapshot
    except Exception as exc:
        logger.warning(f"MCP auth discovered re-auth snapshot rebuild unavailable: {type(exc).__name__}")
        return None
