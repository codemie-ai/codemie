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
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import status
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException

from ._callback_pages import (
    _build_error_callback_response,
    _build_success_callback_response,
    _build_trusted_callback_error,
)
from ._common import (
    CallbackPageError,
    _is_discovered_auth_config_id,
    _raise_client_error,
)
from ._constants import (
    _AUTHENTICATION_FAILED_TITLE,
    _CALLBACK_CONFIG_ERROR_MESSAGE,
    _CALLBACK_ERROR_CONFIGURATION,
    _CALLBACK_ERROR_CREDENTIALS_STORE_FAILED,
    _CALLBACK_ERROR_RUNTIME,
    _CALLBACK_ERROR_SESSION_EXPIRED,
    _CALLBACK_ERROR_VERIFICATION_FAILED,
    _CALLBACK_EXPIRED_MESSAGE,
    _CALLBACK_RECOVERY_TEXT,
    _CALLBACK_REDIS_UNAVAILABLE_MESSAGE,
    _CALLBACK_RUNTIME_ERROR_MESSAGE,
    _CALLBACK_STATE_MAX_AGE,
    _CALLBACK_TMS_STORE_ERROR_MESSAGE,
    _CALLBACK_VERIFICATION_FAILURE_MESSAGE,
    _INSTALL_ENTERPRISE_MCP_AUTH_HELP,
    _INVALID_OAUTH2_CONFIG_MESSAGE,
    _MCP_AUTH_RETRY_AFTER_INIT_HELP,
    _MCP_AUTH_SERVICE_UNAVAILABLE_MESSAGE,
    _MCP_AUTH_TEMPORARILY_UNAVAILABLE,
)
from ._guards import (
    _require_initialized_callback_dependencies,
    _require_initialized_discovered_flow_store,
)
from ._uri import (
    _describe_stored_redirect_uri,
)

# Indirection: tests patch helpers as ``dependencies.X``. Internal calls below
# resolve through this module reference at call time so those patches take
# effect even though the helper bodies live here. The circular import is safe
# because we only access attributes inside function bodies, never at import
# time.
from . import dependencies as _deps  # noqa: E402

if TYPE_CHECKING:
    from codemie_enterprise.mcp_auth import RedisPKCEStore


def _decode_and_verify_oauth2_callback_state(state: str, signing_key: bytes):
    try:
        from codemie_enterprise.mcp_auth import decode_and_verify_oauth2_state
    except ImportError as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details="Enterprise MCP auth package is not available for OAuth2 callback handling.",
            help=_INSTALL_ENTERPRISE_MCP_AUTH_HELP,
        ) from exc

    try:
        return decode_and_verify_oauth2_state(state, signing_key)
    except ValueError as exc:
        raise CallbackPageError(_CALLBACK_VERIFICATION_FAILURE_MESSAGE) from exc


def _load_callback_mcp_config(auth_config_id: str):
    from codemie.rest_api.models.mcp_config import MCPConfig

    try:
        mcp_config = MCPConfig.get_by_auth_config_id(auth_config_id)
    except Exception as exc:
        raise _build_trusted_callback_error(
            _CALLBACK_RUNTIME_ERROR_MESSAGE,
            auth_config_id=auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_RUNTIME,
        ) from exc

    if mcp_config is None:
        raise _build_trusted_callback_error(
            _CALLBACK_CONFIG_ERROR_MESSAGE,
            auth_config_id=auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_CONFIGURATION,
        )
    return mcp_config


def _load_raw_callback_oauth_config(mcp_config: Any, auth_config_id: str) -> dict[str, Any]:
    config_block = getattr(mcp_config, "config", None)
    raw_auth_config = getattr(config_block, "auth_config", None)
    if not isinstance(raw_auth_config, dict) or raw_auth_config.get("auth_type") != "oauth2":
        raise _build_trusted_callback_error(
            _CALLBACK_CONFIG_ERROR_MESSAGE,
            auth_config_id=auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_CONFIGURATION,
            server_name=getattr(mcp_config, "name", None),
        )
    return raw_auth_config


def _validate_callback_state_age(state_payload: Any) -> None:
    issued_at = datetime.fromtimestamp(state_payload.ts, tz=timezone.utc)
    if datetime.now(tz=timezone.utc) - issued_at > _CALLBACK_STATE_MAX_AGE:
        raise _build_trusted_callback_error(
            _CALLBACK_EXPIRED_MESSAGE,
            auth_config_id=state_payload.auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_SESSION_EXPIRED,
        )


def _is_mcp_auth_redis_unavailable(exc: Exception) -> bool:
    try:
        from codemie_enterprise.mcp_auth import MCPAuthRedisUnavailable
    except ImportError:
        return False
    return isinstance(exc, MCPAuthRedisUnavailable)


def _consume_callback_pkce_state(pkce_store: RedisPKCEStore, state: str, auth_config_id: str):
    try:
        pkce_state = pkce_store.consume(state)
    except Exception as exc:
        if _is_mcp_auth_redis_unavailable(exc):
            raise CallbackPageError(
                _CALLBACK_REDIS_UNAVAILABLE_MESSAGE,
                auth_config_id=auth_config_id,
                bridge_error_code=_CALLBACK_ERROR_RUNTIME,
            ) from exc
        raise

    if pkce_state is None:
        raise CallbackPageError(
            _CALLBACK_EXPIRED_MESSAGE,
            auth_config_id=auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_SESSION_EXPIRED,
        )
    return pkce_state


def _validate_callback_state_matches_pkce(state_payload: Any, pkce_state: Any) -> None:
    if (
        state_payload.auth_config_id != pkce_state.auth_config_id
        or state_payload.user_id != pkce_state.user_id
        or state_payload.session_binding_hash != pkce_state.session_binding_hash
    ):
        raise _build_trusted_callback_error(
            _CALLBACK_VERIFICATION_FAILURE_MESSAGE,
            auth_config_id=state_payload.auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_VERIFICATION_FAILED,
        )


def _load_discovered_flow_snapshot_or_error(discovered_flow_id: str, auth_config_id: str | None = None) -> Any:
    try:
        snapshot = _require_initialized_discovered_flow_store().get(discovered_flow_id)
    except ExtendedHTTPException as exc:
        if auth_config_id is not None:
            raise CallbackPageError(
                _CALLBACK_REDIS_UNAVAILABLE_MESSAGE,
                auth_config_id=auth_config_id,
                bridge_error_code=_CALLBACK_ERROR_RUNTIME,
            ) from exc
        raise
    except Exception as exc:
        if auth_config_id is not None:
            raise CallbackPageError(
                _CALLBACK_REDIS_UNAVAILABLE_MESSAGE,
                auth_config_id=auth_config_id,
                bridge_error_code=_CALLBACK_ERROR_RUNTIME,
            ) from exc
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details=_MCP_AUTH_SERVICE_UNAVAILABLE_MESSAGE,
            help=_MCP_AUTH_RETRY_AFTER_INIT_HELP,
        ) from exc
    if snapshot is None:
        if auth_config_id is not None:
            raise CallbackPageError(
                _CALLBACK_EXPIRED_MESSAGE,
                auth_config_id=auth_config_id,
                bridge_error_code=_CALLBACK_ERROR_SESSION_EXPIRED,
            )
        _raise_client_error(
            _INVALID_OAUTH2_CONFIG_MESSAGE,
            "Discovered OAuth2 flow is missing or expired.",
        )
    return snapshot


def _load_recovery_snapshot_or_error(recovery_flow_id: str, auth_config_id: str | None = None) -> Any:
    try:
        from codemie_enterprise.mcp_auth import get_recovery_snapshot
    except ImportError as exc:
        raise CallbackPageError(
            _CALLBACK_RUNTIME_ERROR_MESSAGE,
            auth_config_id=auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_RUNTIME,
        ) from exc
    try:
        snapshot = get_recovery_snapshot(recovery_flow_id)
    except Exception as exc:
        raise CallbackPageError(
            _CALLBACK_REDIS_UNAVAILABLE_MESSAGE,
            auth_config_id=auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_RUNTIME,
        ) from exc
    if snapshot is None:
        raise CallbackPageError(
            _CALLBACK_EXPIRED_MESSAGE,
            auth_config_id=auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_SESSION_EXPIRED,
        )
    return snapshot


def _validate_discovered_snapshot_context(
    snapshot: Any,
    *,
    user_id: str,
    session_binding_hash: str,
    mcp_config_id: str | None,
    auth_config_id: str | None = None,
) -> None:
    if (
        snapshot.user_id == user_id
        and snapshot.session_binding_hash == session_binding_hash
        and (mcp_config_id is None or snapshot.mcp_config_id == mcp_config_id)
        and (auth_config_id is None or snapshot.discovered_auth_id == auth_config_id)
    ):
        return
    if auth_config_id is not None:
        raise _build_trusted_callback_error(
            _CALLBACK_VERIFICATION_FAILURE_MESSAGE,
            auth_config_id=auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_VERIFICATION_FAILED,
        )
    _raise_client_error(
        _INVALID_OAUTH2_CONFIG_MESSAGE,
        "Discovered OAuth2 flow is expired or does not match the authenticated session.",
    )


def _validate_recovery_snapshot_context(
    snapshot: Any,
    *,
    state_payload: Any,
    pkce_state: Any,
    recovery_flow_id: str,
) -> None:
    expected_auth_config_id = getattr(snapshot, "token_storage_auth_config_id", None) or getattr(
        snapshot,
        "auth_config_id",
        None,
    )
    if (
        getattr(snapshot, "recovery_flow_id", None) == recovery_flow_id
        and getattr(snapshot, "user_id", None) == state_payload.user_id
        and getattr(pkce_state, "user_id", None) == state_payload.user_id
        and getattr(state_payload, "recovery_flow_id", None) == recovery_flow_id
        and getattr(pkce_state, "recovery_flow_id", None) == recovery_flow_id
        and (
            not getattr(snapshot, "session_binding_hash", None)
            or snapshot.session_binding_hash == state_payload.session_binding_hash
        )
        and (expected_auth_config_id is None or expected_auth_config_id == state_payload.auth_config_id)
    ):
        return
    raise _build_trusted_callback_error(
        _CALLBACK_VERIFICATION_FAILURE_MESSAGE,
        auth_config_id=state_payload.auth_config_id,
        bridge_error_code=_CALLBACK_ERROR_VERIFICATION_FAILED,
        server_name=getattr(snapshot, "mcp_config_name", None),
    )


def _resolve_callback_recovery_flow_id(state_payload: Any, pkce_state: Any) -> str | None:
    state_recovery_flow_id = getattr(state_payload, "recovery_flow_id", None)
    pkce_recovery_flow_id = getattr(pkce_state, "recovery_flow_id", None)
    if state_recovery_flow_id is None and pkce_recovery_flow_id is None:
        return None
    if (
        not isinstance(state_recovery_flow_id, str)
        or not state_recovery_flow_id
        or not isinstance(pkce_recovery_flow_id, str)
        or not pkce_recovery_flow_id
        or state_recovery_flow_id != pkce_recovery_flow_id
    ):
        raise _build_trusted_callback_error(
            _CALLBACK_VERIFICATION_FAILURE_MESSAGE,
            auth_config_id=state_payload.auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_VERIFICATION_FAILED,
        )
    return state_recovery_flow_id


def _try_get_trusted_callback_context_from_state(state: str | None) -> tuple[str | None, str | None]:
    if not state:
        return None, None

    try:
        _, redis_encryption = _deps._require_initialized_mcp_auth_components()
        state_payload = _deps._decode_and_verify_oauth2_callback_state(state, redis_encryption.signing_key)
        recovery_flow_id = getattr(state_payload, "recovery_flow_id", None)
        if isinstance(recovery_flow_id, str) and recovery_flow_id:
            snapshot = _deps._load_recovery_snapshot_or_error(
                recovery_flow_id,
                auth_config_id=state_payload.auth_config_id,
            )
            return state_payload.auth_config_id, getattr(snapshot, "mcp_config_name", None)
        discovered_flow_id = getattr(state_payload, "discovered_flow_id", None)
        if isinstance(discovered_flow_id, str) and discovered_flow_id:
            snapshot = _deps._load_discovered_flow_snapshot_or_error(
                discovered_flow_id,
                auth_config_id=state_payload.auth_config_id,
            )
            return state_payload.auth_config_id, getattr(snapshot, "mcp_config_name", None)
        mcp_config = _deps._load_callback_mcp_config(state_payload.auth_config_id)
        return state_payload.auth_config_id, getattr(mcp_config, "name", None)
    except (CallbackPageError, ExtendedHTTPException):
        return None, None
    except Exception as exc:
        logger.warning(f"Failed to resolve trusted callback context from state: {exc}")
        return None, None


def _validate_callback_auth_config(raw_auth_config: dict[str, Any], server_name: str | None, auth_config_id: str):
    try:
        from codemie_enterprise.mcp_auth import OAuth2AuthConfig
    except ImportError as exc:
        raise _build_trusted_callback_error(
            _CALLBACK_RUNTIME_ERROR_MESSAGE,
            auth_config_id=auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_RUNTIME,
            server_name=server_name,
        ) from exc

    try:
        return OAuth2AuthConfig.model_validate(raw_auth_config)
    except ValidationError as exc:
        raise _build_trusted_callback_error(
            _CALLBACK_CONFIG_ERROR_MESSAGE,
            auth_config_id=auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_CONFIGURATION,
            server_name=server_name,
        ) from exc


def _decrypt_callback_client_secret(
    raw_auth_config: dict[str, Any], server_name: str | None, auth_config_id: str
) -> str | None:
    if raw_auth_config.get("client_type") != "confidential":
        return None

    from .dependencies import decrypt_confidential_client_secret

    client_secret = decrypt_confidential_client_secret(raw_auth_config)
    if not client_secret:
        raise _build_trusted_callback_error(
            _CALLBACK_CONFIG_ERROR_MESSAGE,
            auth_config_id=auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_CONFIGURATION,
            server_name=server_name,
        )
    return client_secret


def _exchange_callback_code(
    *,
    auth_config: Any,
    state_payload: Any,
    pkce_state: Any,
    redirect_uri: str,
    resource: str,
    code: str,
    client_secret: str | None,
    server_name: str | None,
    auth_config_id: str,
):
    try:
        from codemie_enterprise.mcp_auth import MCPAuthTokenExchangeError, exchange_oauth2_callback_code
    except ImportError as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details="Enterprise MCP auth package is not available for OAuth2 callback handling.",
            help=_INSTALL_ENTERPRISE_MCP_AUTH_HELP,
        ) from exc

    try:
        with httpx.Client(timeout=2.0) as http_client:
            return exchange_oauth2_callback_code(
                auth_config=auth_config,
                state_payload=state_payload,
                pkce_state=pkce_state,
                redirect_uri=redirect_uri,
                resource=resource,
                code=code,
                client_secret=client_secret,
                http_client=http_client,
            )
    except MCPAuthTokenExchangeError as exc:
        raise _build_trusted_callback_error(
            _CALLBACK_RUNTIME_ERROR_MESSAGE,
            auth_config_id=auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_RUNTIME,
            server_name=server_name,
            title=_AUTHENTICATION_FAILED_TITLE,
        ) from exc


def _store_callback_token(
    *,
    user_id: str,
    auth_config_id: str,
    token_data: Any,
    server_name: str | None,
    tms: Any,
    audit_source: str,
) -> None:
    try:
        with _deps._tms_audit_context(audit_source, correlation_id=auth_config_id):
            tms.store(user_id, auth_config_id, token_data)
    except Exception as exc:
        logger.warning(
            "Failed to persist MCP auth callback credentials for "
            f"auth_config_id={auth_config_id}: {type(exc).__name__}"
        )
        raise _build_trusted_callback_error(
            _CALLBACK_TMS_STORE_ERROR_MESSAGE,
            auth_config_id=auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_CREDENTIALS_STORE_FAILED,
            server_name=server_name,
            title="Authentication could not be saved",
        ) from exc


def build_oauth2_callback_response(
    *,
    code: str | None,
    state: str | None,
    error: str | None,
    error_description: str | None,
    error_uri: str | None,
) -> HTMLResponse:
    try:
        return _build_oauth2_callback_response(
            code=code,
            state=state,
            error=error,
            error_description=error_description,
            error_uri=error_uri,
        )
    except CallbackPageError as exc:
        return _build_error_callback_response(exc)
    except ExtendedHTTPException as exc:
        logger.warning(f"MCP OAuth2 callback bridge failed with HTTP exception: {exc.message}")
        return _build_error_callback_response(CallbackPageError(_CALLBACK_CONFIG_ERROR_MESSAGE))
    except Exception as exc:
        logger.exception(f"Unexpected MCP OAuth2 callback failure: {exc}")
        return _build_error_callback_response(CallbackPageError(_CALLBACK_RUNTIME_ERROR_MESSAGE))


def _build_oauth2_callback_response(
    *,
    code: str | None,
    state: str | None,
    error: str | None,
    error_description: str | None,
    error_uri: str | None,
) -> HTMLResponse:
    if error:
        auth_config_id, server_name = _try_get_trusted_callback_context_from_state(state)
        return _build_error_callback_response(
            CallbackPageError(
                _CALLBACK_RECOVERY_TEXT,
                title=_AUTHENTICATION_FAILED_TITLE,
                server_name=server_name,
                error_code=error,
                auth_config_id=auth_config_id,
                error_description=error_description,
                error_uri=error_uri,
            )
        )

    if not state or not code:
        raise CallbackPageError(_CALLBACK_VERIFICATION_FAILURE_MESSAGE)

    pkce_store, redis_encryption, tms = _require_initialized_callback_dependencies()
    state_payload = _deps._decode_and_verify_oauth2_callback_state(state, redis_encryption.signing_key)
    _validate_callback_state_age(state_payload)
    pkce_state = _deps._consume_callback_pkce_state(pkce_store, state, state_payload.auth_config_id)
    _validate_callback_state_matches_pkce(state_payload, pkce_state)
    recovery_flow_id = _resolve_callback_recovery_flow_id(state_payload, pkce_state)
    if recovery_flow_id is not None:
        return _build_recovery_oauth2_callback_response(
            code=code,
            state_payload=state_payload,
            pkce_state=pkce_state,
            recovery_flow_id=recovery_flow_id,
            tms=tms,
        )
    discovered_flow_id = getattr(state_payload, "discovered_flow_id", None) or getattr(
        pkce_state,
        "discovered_flow_id",
        None,
    )
    if isinstance(discovered_flow_id, str) and discovered_flow_id:
        return _build_discovered_oauth2_callback_response(
            code=code,
            state_payload=state_payload,
            pkce_state=pkce_state,
            discovered_flow_id=discovered_flow_id,
            tms=tms,
        )

    mcp_config = _deps._load_callback_mcp_config(state_payload.auth_config_id)
    server_name = getattr(mcp_config, "name", None)
    raw_auth_config = _deps._load_raw_callback_oauth_config(mcp_config, state_payload.auth_config_id)
    auth_config = _deps._validate_callback_auth_config(raw_auth_config, server_name, state_payload.auth_config_id)
    try:
        resource = _deps.derive_resource_uri(getattr(mcp_config.config, "url", None))
    except ExtendedHTTPException as exc:
        raise _build_trusted_callback_error(
            _CALLBACK_CONFIG_ERROR_MESSAGE,
            auth_config_id=state_payload.auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_CONFIGURATION,
            server_name=server_name,
        ) from exc
    redirect_uri, _, _ = _deps.build_redirect_uri()
    client_secret = _decrypt_callback_client_secret(raw_auth_config, server_name, state_payload.auth_config_id)
    token_data = _deps._exchange_callback_code(
        auth_config=auth_config,
        state_payload=state_payload,
        pkce_state=pkce_state,
        redirect_uri=redirect_uri,
        resource=resource,
        code=code,
        client_secret=client_secret,
        server_name=server_name,
        auth_config_id=state_payload.auth_config_id,
    )
    _store_callback_token(
        user_id=state_payload.user_id,
        auth_config_id=state_payload.auth_config_id,
        token_data=token_data,
        server_name=server_name,
        tms=tms,
        audit_source="oauth2_callback",
    )
    return _build_success_callback_response(server_name, state_payload.auth_config_id)


def _build_recovery_oauth2_callback_response(
    *,
    code: str,
    state_payload: Any,
    pkce_state: Any,
    recovery_flow_id: str,
    tms: Any,
) -> HTMLResponse:
    snapshot = _deps._load_recovery_snapshot_or_error(
        recovery_flow_id,
        auth_config_id=state_payload.auth_config_id,
    )
    _validate_recovery_snapshot_context(
        snapshot,
        state_payload=state_payload,
        pkce_state=pkce_state,
        recovery_flow_id=recovery_flow_id,
    )
    auth_config = getattr(snapshot, "auth_config", None)
    raw_token_storage_auth_config_id = getattr(snapshot, "token_storage_auth_config_id", None) or getattr(
        snapshot,
        "auth_config_id",
        None,
    )
    server_name = getattr(snapshot, "mcp_config_name", None)
    if (
        auth_config is None
        or not isinstance(raw_token_storage_auth_config_id, str)
        or not raw_token_storage_auth_config_id
    ):
        raise _build_trusted_callback_error(
            _CALLBACK_CONFIG_ERROR_MESSAGE,
            auth_config_id=state_payload.auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_CONFIGURATION,
            server_name=server_name,
        )
    token_storage_auth_config_id: str = raw_token_storage_auth_config_id

    try:
        redirect_uri, _, _ = _deps.build_redirect_uri()
    except ExtendedHTTPException as exc:
        raise _build_trusted_callback_error(
            _CALLBACK_CONFIG_ERROR_MESSAGE,
            auth_config_id=token_storage_auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_CONFIGURATION,
            server_name=server_name,
        ) from exc
    client_secret = _recovery_callback_client_secret(auth_config, token_storage_auth_config_id, server_name)
    token_data = _deps._exchange_callback_code(
        auth_config=auth_config,
        state_payload=state_payload,
        pkce_state=pkce_state,
        redirect_uri=redirect_uri,
        resource=getattr(snapshot, "resource", None) or getattr(snapshot, "resource_metadata_url_internal", None) or "",
        code=code,
        client_secret=client_secret,
        server_name=server_name,
        auth_config_id=token_storage_auth_config_id,
    )
    _store_callback_token(
        user_id=state_payload.user_id,
        auth_config_id=token_storage_auth_config_id,
        token_data=token_data,
        server_name=server_name,
        tms=tms,
        audit_source="oauth2_callback",
    )
    return _build_success_callback_response(server_name, token_storage_auth_config_id)


def _recovery_callback_client_secret(
    auth_config: Any,
    token_storage_auth_config_id: str,
    server_name: str | None,
) -> str | None:
    if getattr(auth_config, "client_type", None) != "confidential":
        return None
    if _is_discovered_auth_config_id(token_storage_auth_config_id):
        return None
    mcp_config = _deps._load_callback_mcp_config(token_storage_auth_config_id)
    raw_auth_config = _deps._load_raw_callback_oauth_config(mcp_config, token_storage_auth_config_id)
    return _decrypt_callback_client_secret(raw_auth_config, server_name, token_storage_auth_config_id)


def _build_discovered_oauth2_callback_response(
    *,
    code: str,
    state_payload: Any,
    pkce_state: Any,
    discovered_flow_id: str,
    tms: Any,
) -> HTMLResponse:
    snapshot = _deps._load_discovered_flow_snapshot_or_error(
        discovered_flow_id,
        auth_config_id=state_payload.auth_config_id,
    )
    _validate_discovered_snapshot_context(
        snapshot,
        user_id=state_payload.user_id,
        session_binding_hash=state_payload.session_binding_hash,
        mcp_config_id=getattr(snapshot, "mcp_config_id", None),
        auth_config_id=state_payload.auth_config_id,
    )
    if getattr(snapshot, "status", None) != "authentication_required" or getattr(snapshot, "flow_config", None) is None:
        raise _build_trusted_callback_error(
            _CALLBACK_CONFIG_ERROR_MESSAGE,
            auth_config_id=state_payload.auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_CONFIGURATION,
            server_name=getattr(snapshot, "mcp_config_name", None),
        )

    token_data = _deps._exchange_callback_code(
        auth_config=snapshot.flow_config,
        state_payload=state_payload,
        pkce_state=pkce_state,
        redirect_uri=_describe_stored_redirect_uri(getattr(snapshot, "redirect_uri", None))[0],
        resource=snapshot.canonical_resource,
        code=code,
        client_secret=None,
        server_name=snapshot.mcp_config_name,
        auth_config_id=snapshot.discovered_auth_id,
    )
    _store_callback_token(
        user_id=state_payload.user_id,
        auth_config_id=snapshot.discovered_auth_id,
        token_data=token_data,
        server_name=snapshot.mcp_config_name,
        tms=tms,
        audit_source="oauth2_callback",
    )
    return _build_success_callback_response(snapshot.mcp_config_name, snapshot.discovered_auth_id)
