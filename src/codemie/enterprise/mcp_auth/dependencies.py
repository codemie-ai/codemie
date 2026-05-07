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
import hashlib
import html
import re
from datetime import datetime, timedelta, timezone
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext, suppress
from typing import TYPE_CHECKING, Any, NoReturn, Protocol
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx
from fastapi import status
from fastapi.responses import HTMLResponse, Response
from pydantic import ValidationError

from codemie.configs import config
from codemie.clients.redis import create_redis_client
from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException, MCPAuthenticationRequiredException
from codemie.enterprise.loader import HAS_MCP_AUTH
from codemie.rest_api.security.user import User
from codemie.service.encryption.base_encryption_service import BaseEncryptionService
from codemie.service.encryption.encryption_factory import EncryptionFactory

if TYPE_CHECKING:
    from codemie_enterprise.mcp_auth import (
        OAuth2InitiateResponseData,
        RedisEncryption,
        RedisPKCEStore,
        SAMLInitiateResponseData,
        SAMLRelayStateStore,
    )

_initialized = False
_bridge_queue: asyncio.Queue[str] | None = None
_bridge_task: asyncio.Task[None] | None = None
_bridge_loop: asyncio.AbstractEventLoop | None = None
_mcp_auth_service = None
_tms = None
_redis_client = None
_registered_resolver_types: set[type] = set()
_pkce_store: RedisPKCEStore | None = None
_saml_relay_state_store: SAMLRelayStateStore | None = None
_redis_encryption: RedisEncryption | None = None
_tms_audit_context_provider = None

SUPPORTED_AUTH_TYPES = ("oauth2", "saml")
_REQUIRED_AUTH_FIELDS = {
    "oauth2": ("authorization_url", "token_url", "client_id", "client_type", "scopes", "token_delivery"),
    "saml": (
        "sso_url",
        "entity_id",
        "idp_entity_id",
        "idp_x509cert",
        "saml_credential_attribute",
        "saml_session_ttl",
        "token_delivery",
    ),
}
_HTTPS_ONLY_FIELDS = ("authorization_url", "token_url", "sso_url")
_SAML_HTTP_ERROR = "SAML is not supported for HTTP transport. Use OAuth2 for HTTP MCP servers"
encryption_service: BaseEncryptionService = EncryptionFactory().get_current_encryption_service()

_OAUTH2_CALLBACK_PATH = "/v1/mcp-auth/oauth2/callback"
_OAUTH2_CALLBACK_PAGE_SCRIPT_PATH = (
    f"{config.API_ROOT_PATH.strip('/')}/" if config.API_ROOT_PATH else "/"
) + "v1/mcp-auth/oauth2/callback-page.js"
_SAML_ACS_PATH = "/v1/mcp-auth/saml/acs"
_LOCALHOST_HOSTS = {"localhost", "127.0.0.1", "::1"}
_INVALID_OAUTH2_CONFIG_MESSAGE = "Invalid OAuth2 MCP configuration"
_INVALID_MCP_AUTH_CONFIG_MESSAGE = "Invalid MCP auth configuration"
_INVALID_MCP_SERVER_URL_MESSAGE = "Invalid MCP server URL for OAuth2 initiation"
_MCP_AUTH_SERVICE_UNAVAILABLE_MESSAGE = "MCP auth service is not initialized"
_MCP_AUTH_TEMPORARILY_UNAVAILABLE = "MCP auth temporarily unavailable"
_MCP_AUTH_RETRY_AFTER_INIT_HELP = "Try again after the MCP auth service finishes initializing."
_INSTALL_ENTERPRISE_MCP_AUTH_HELP = "Install the enterprise MCP auth package and retry."
_AUTHENTICATION_FAILED_TITLE = "Authentication failed"
_SP_METADATA_SAML_ONLY_MESSAGE = "SP metadata is only available for SAML auth configurations"
_SP_METADATA_GENERATION_FAILED_MESSAGE = "SP metadata generation failed"
_CALLBACK_CONTENT_SECURITY_POLICY = "default-src 'none'; script-src 'self'"
_CALLBACK_SECURITY_HEADERS = {
    "Content-Security-Policy": _CALLBACK_CONTENT_SECURITY_POLICY,
    "X-Frame-Options": "DENY",
}
_CALLBACK_SUCCESS_MESSAGE = "Authentication complete. Return to CodeMie to continue using the MCP server."
_CALLBACK_TRANSITION_MESSAGE = "Completing authentication..."
_CALLBACK_SUCCESS_CLOSE_MESSAGE = "Authentication successful! You can close this tab."
_CALLBACK_SUCCESS_OPEN_CODEMIE_MESSAGE = "Authentication successful! Open CodeMie to continue."
_CALLBACK_VERIFICATION_FAILURE_MESSAGE = (
    "Authentication session could not be verified. Return to CodeMie and try again."
)
_CALLBACK_EXPIRED_MESSAGE = "Authentication session expired. Return to CodeMie and try again."
_CALLBACK_REDIS_UNAVAILABLE_MESSAGE = (
    "Authentication session could not be verified. Return to CodeMie and try again when the service is available."
)
_CALLBACK_CONFIG_ERROR_MESSAGE = (
    "Authentication could not be completed because the MCP server configuration is invalid. "
    "Contact your administrator if the problem persists."
)
_CALLBACK_RUNTIME_ERROR_MESSAGE = "Authentication could not be completed. Return to CodeMie and try again."
_CALLBACK_TMS_STORE_ERROR_MESSAGE = (
    "Authentication succeeded but credentials could not be saved. Return to CodeMie and try again."
)
_CALLBACK_RECOVERY_TEXT = "Return to CodeMie and try again."
_CALLBACK_CONTACT_ADMIN_TEXT = "Contact your administrator if the problem persists."
_CALLBACK_STATE_MAX_AGE = timedelta(minutes=10)
_CALLBACK_EVENT_TYPE = "mcp_auth_callback"
_CALLBACK_ERROR_SESSION_EXPIRED = "session_expired"
_CALLBACK_ERROR_VERIFICATION_FAILED = "verification_failed"
_CALLBACK_ERROR_CONFIGURATION = "configuration_error"
_CALLBACK_ERROR_CREDENTIALS_STORE_FAILED = "credentials_store_failed"
_CALLBACK_ERROR_RUNTIME = "runtime_error"
_CALLBACK_FALLBACK_DELAY_MS = 300


class CallbackPageError(Exception):
    def __init__(
        self,
        message: str,
        *,
        server_name: str | None = None,
        title: str = "Authentication could not be completed",
        error_code: str | None = None,
        auth_config_id: str | None = None,
        bridge_error_code: str | None = None,
        error_description: str | None = None,
        error_uri: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.server_name = server_name
        self.title = title
        self.error_code = error_code
        self.auth_config_id = auth_config_id
        self.bridge_error_code = bridge_error_code
        self.error_description = error_description
        self.error_uri = error_uri


class _CleanupEnqueuer(Protocol):
    def enqueue_cleanup(self, user_id: str) -> None: ...


def _is_missing_required_value(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _uses_https(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False

    return urlsplit(value).scheme.lower() == "https"


def _raise_client_error(message: str, details: str, *, code: int = status.HTTP_400_BAD_REQUEST) -> NoReturn:
    raise ExtendedHTTPException(code=code, message=message, details=details, help="Review the MCP auth configuration.")


def _is_localhost_hostname(hostname: str | None) -> bool:
    return hostname in _LOCALHOST_HOSTS


def _build_callback_uri(path: str, *, invalid_message: str, label: str) -> tuple[str, str, bool]:
    callback_uri = f"{config.CALLBACK_API_BASE_URL.rstrip('/')}{path}"
    parsed_callback_uri = urlsplit(callback_uri)
    hostname = parsed_callback_uri.hostname
    if not hostname:
        _raise_client_error(
            invalid_message,
            f"CALLBACK_API_BASE_URL must produce a valid {label}: {callback_uri}",
        )
    localhost_warning = _is_localhost_hostname(hostname)
    if parsed_callback_uri.scheme.lower() != "https" and not localhost_warning:
        _raise_client_error(
            invalid_message,
            f"{label} must use HTTPS unless hostname is localhost, 127.0.0.1, or ::1: {callback_uri}",
        )
    return callback_uri, parsed_callback_uri.netloc, localhost_warning


def build_redirect_uri() -> tuple[str, str, bool]:
    return _build_callback_uri(
        _OAUTH2_CALLBACK_PATH,
        invalid_message=_INVALID_OAUTH2_CONFIG_MESSAGE,
        label="Redirect URI",
    )


def build_saml_acs_url() -> str:
    acs_url, _, _ = _build_callback_uri(
        _SAML_ACS_PATH,
        invalid_message=_INVALID_MCP_AUTH_CONFIG_MESSAGE,
        label="ACS URL",
    )
    return acs_url


def _normalize_resource_path(path: str) -> str:
    stripped_path = path or ""
    if stripped_path in {"", "/"}:
        return ""
    return stripped_path.rstrip("/") or ""


def _normalize_default_port(parsed_url: SplitResult) -> str:
    hostname = parsed_url.hostname
    if hostname is None:
        return ""
    port = parsed_url.port
    if port is None:
        return hostname
    if (parsed_url.scheme.lower() == "https" and port == 443) or (parsed_url.scheme.lower() == "http" and port == 80):
        return hostname
    return f"{hostname}:{port}"


def derive_resource_uri(server_url: str | None) -> str:
    if not server_url:
        _raise_client_error(
            _INVALID_MCP_SERVER_URL_MESSAGE,
            "MCP server URL is required to derive the OAuth2 resource.",
        )

    parsed_url = urlsplit(server_url)
    if parsed_url.scheme.lower() != "https":
        _raise_client_error(
            _INVALID_MCP_SERVER_URL_MESSAGE,
            f"MCP server URL must use HTTPS for OAuth2 resource derivation: {server_url}",
        )
    if not parsed_url.hostname:
        _raise_client_error(
            _INVALID_MCP_SERVER_URL_MESSAGE,
            f"MCP server URL must include a hostname for OAuth2 resource derivation: {server_url}",
        )

    normalized_url = parsed_url._replace(
        scheme=parsed_url.scheme.lower(),
        netloc=_normalize_default_port(parsed_url),
        path=_normalize_resource_path(parsed_url.path),
        query="",
        fragment="",
    )
    return urlunsplit(normalized_url)


def _get_authenticated_bearer_token_hash(user: User) -> str:
    if not user.auth_token:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=_INVALID_MCP_AUTH_CONFIG_MESSAGE,
            details="Authenticated MCP auth initiation requires a bearer token for session binding.",
            help="Re-authenticate and retry the MCP auth initiation flow.",
        )
    return hashlib.sha256(user.auth_token.encode("utf-8")).hexdigest()


def _require_initialized_mcp_auth_components() -> tuple[RedisPKCEStore, RedisEncryption]:
    if _pkce_store is None or _redis_encryption is None:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details=_MCP_AUTH_SERVICE_UNAVAILABLE_MESSAGE,
            help=_MCP_AUTH_RETRY_AFTER_INIT_HELP,
        )
    return _pkce_store, _redis_encryption


def _require_initialized_saml_initiate_dependencies() -> tuple[SAMLRelayStateStore, RedisEncryption]:
    if _saml_relay_state_store is None or _redis_encryption is None:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details=_MCP_AUTH_SERVICE_UNAVAILABLE_MESSAGE,
            help=_MCP_AUTH_RETRY_AFTER_INIT_HELP,
        )
    return _saml_relay_state_store, _redis_encryption


def _require_initialized_tms() -> Any:
    if _tms is None:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details=_MCP_AUTH_SERVICE_UNAVAILABLE_MESSAGE,
            help=_MCP_AUTH_RETRY_AFTER_INIT_HELP,
        )
    return _tms


def _require_initialized_saml_callback_dependencies() -> tuple[SAMLRelayStateStore, RedisEncryption, Any]:
    if _saml_relay_state_store is None or _redis_encryption is None or _tms is None:
        raise CallbackPageError(
            _CALLBACK_REDIS_UNAVAILABLE_MESSAGE,
            bridge_error_code=_CALLBACK_ERROR_RUNTIME,
        )
    return _saml_relay_state_store, _redis_encryption, _tms


def _require_initialized_callback_dependencies() -> tuple[RedisPKCEStore, RedisEncryption, Any]:
    pkce_store, redis_encryption = _require_initialized_mcp_auth_components()
    return pkce_store, redis_encryption, _require_initialized_tms()


def is_hostname_like(value: str) -> bool:
    return value == "localhost" or bool(re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z0-9-]+", value))


def derive_saml_entity_hostname(entity_id: Any) -> str | None:
    if not isinstance(entity_id, str) or not entity_id:
        return None

    parsed_entity = urlsplit(entity_id)
    if parsed_entity.hostname:
        return parsed_entity.hostname

    if is_hostname_like(entity_id):
        return entity_id

    return None


def derive_as_hostname(auth_type: str | None, auth_config: dict[str, Any] | None) -> str | None:
    if not auth_config:
        return None

    if auth_type == "oauth2":
        return urlsplit(auth_config.get("authorization_url", "") or "").hostname

    if auth_type != "saml":
        return None

    sso_hostname = urlsplit(auth_config.get("sso_url", "") or "").hostname
    if sso_hostname:
        return sso_hostname

    return derive_saml_entity_hostname(auth_config.get("entity_id"))


def derive_initiate_url(auth_type: str | None) -> str | None:
    if auth_type == "oauth2":
        return "/v1/mcp-auth/oauth2/initiate"
    if auth_type == "saml":
        return "/v1/mcp-auth/saml/initiate"
    return None


def _build_callback_page(
    *,
    title: str,
    message: str,
    outcome: str,
    server_name: str | None = None,
    auth_config_id: str | None = None,
    error_code: str | None = None,
    bridge_error_code: str | None = None,
    error_description: str | None = None,
    error_uri: str | None = None,
    noscript_message: str | None = None,
) -> HTMLResponse:
    escaped_title = html.escape(title)
    escaped_message = html.escape(message)
    bootstrap_attributes = [f'data-callback-result="{html.escape(outcome, quote=True)}"']
    if auth_config_id:
        target_origin = _derive_callback_target_origin()
        bootstrap_attributes.extend(
            [
                f'data-auth-config-id="{html.escape(auth_config_id, quote=True)}"',
                f'data-target-origin="{html.escape(target_origin, quote=True)}"',
            ]
        )
        if error_code:
            bootstrap_attributes.append(f'data-idp-error-code="{html.escape(error_code, quote=True)}"')
        if bridge_error_code:
            bootstrap_attributes.append(f'data-bridge-error-code="{html.escape(bridge_error_code, quote=True)}"')

    details: list[str] = []
    if server_name:
        details.append(f"<p>MCP server: <strong>{html.escape(server_name)}</strong></p>")
    if error_code:
        details.append(f"<p>Identity provider error: <code>{html.escape(error_code)}</code></p>")
    if error_description:
        details.append(f"<p>{html.escape(error_description)}</p>")
    if error_uri:
        escaped_error_uri = html.escape(error_uri, quote=True)
        details.append(f'<p><a href="{escaped_error_uri}">{escaped_error_uri}</a></p>')

    if noscript_message:
        details.append(f"<noscript><p>{html.escape(noscript_message)}</p></noscript>")

    content = "".join(
        [
            "<!DOCTYPE html>",
            "<html lang=\"en\">",
            "<head>",
            "<meta charset=\"utf-8\">",
            "<title>CodeMie MCP Authentication</title>",
            "</head>",
            "<body>",
            f"<main {' '.join(bootstrap_attributes)}>",
            f"<h1>{escaped_title}</h1>",
            f"<p data-callback-message>{escaped_message}</p>",
            *details,
            f"<script src=\"{_OAUTH2_CALLBACK_PAGE_SCRIPT_PATH}\"></script>",
            "</main>",
            "</body>",
            "</html>",
        ]
    )
    return HTMLResponse(content=content, status_code=status.HTTP_200_OK, headers=_CALLBACK_SECURITY_HEADERS)


def _build_success_callback_response(server_name: str | None, auth_config_id: str) -> HTMLResponse:
    return _build_callback_page(
        title="Authentication complete",
        message=_CALLBACK_TRANSITION_MESSAGE,
        outcome="success",
        server_name=server_name,
        auth_config_id=auth_config_id,
        noscript_message=_CALLBACK_SUCCESS_MESSAGE,
    )


def _build_error_callback_response(error: CallbackPageError) -> HTMLResponse:
    return _build_callback_page(
        title=error.title,
        message=error.message,
        outcome="error",
        server_name=error.server_name,
        auth_config_id=error.auth_config_id,
        error_code=error.error_code,
        bridge_error_code=error.bridge_error_code,
        error_description=error.error_description,
        error_uri=error.error_uri,
    )


def _derive_callback_target_origin() -> str:
    parsed_origin = urlsplit(config.FRONTEND_URL)
    if not parsed_origin.scheme or not parsed_origin.netloc:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="MCP auth callback origin is invalid",
            details="FRONTEND_URL must include scheme and host for callback postMessage target origin.",
            help="Set FRONTEND_URL to the exact frontend URL and retry.",
        )
    return f"{parsed_origin.scheme}://{parsed_origin.netloc}"


def _build_trusted_callback_error(
    message: str,
    *,
    auth_config_id: str,
    bridge_error_code: str,
    server_name: str | None = None,
    title: str = "Authentication could not be completed",
) -> CallbackPageError:
    return CallbackPageError(
        message,
        server_name=server_name,
        title=title,
        auth_config_id=auth_config_id,
        bridge_error_code=bridge_error_code,
    )


def build_oauth2_callback_page_script_response() -> Response:
    callback_script = f"""
const CALLBACK_EVENT_TYPE = '{_CALLBACK_EVENT_TYPE}';
const CALLBACK_SUCCESS_CLOSE_MESSAGE = '{_CALLBACK_SUCCESS_CLOSE_MESSAGE}';
const CALLBACK_SUCCESS_OPEN_CODEMIE_MESSAGE = '{_CALLBACK_SUCCESS_OPEN_CODEMIE_MESSAGE}';
const CALLBACK_FALLBACK_DELAY_MS = {_CALLBACK_FALLBACK_DELAY_MS};

const main = document.querySelector('main[data-callback-result]');

if (main instanceof HTMLElement) {{
  const message = main.querySelector('[data-callback-message]');
  const authConfigId = main.dataset.authConfigId;
  const targetOrigin = main.dataset.targetOrigin;
  const errorCode = main.dataset.idpErrorCode || main.dataset.bridgeErrorCode;

  const updateMessage = (text) => {{
    if (message instanceof HTMLElement) {{
      message.textContent = text;
    }}
  }};

  if (main.dataset.callbackResult === 'success') {{
    if (!window.opener) {{
      updateMessage(CALLBACK_SUCCESS_OPEN_CODEMIE_MESSAGE);
    }} else if (authConfigId && targetOrigin) {{
      window.opener.postMessage({{
        type: CALLBACK_EVENT_TYPE,
        status: 'success',
        auth_config_id: authConfigId,
      }}, targetOrigin);
      window.close();
      window.setTimeout(() => {{
        if (!window.closed) {{
          updateMessage(CALLBACK_SUCCESS_CLOSE_MESSAGE);
        }}
      }}, CALLBACK_FALLBACK_DELAY_MS);
    }}
  }}

  if (main.dataset.callbackResult === 'error' && window.opener && authConfigId && targetOrigin && errorCode) {{
    window.opener.postMessage({{
      type: CALLBACK_EVENT_TYPE,
      status: 'error',
      error: errorCode,
      auth_config_id: authConfigId,
    }}, targetOrigin);
  }}
}}
""".strip()
    return Response(content=callback_script, media_type="application/javascript")


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


def _decode_and_verify_saml_callback_state(relay_state: str, signing_key: bytes):
    try:
        from codemie_enterprise.mcp_auth import decode_saml_relay_state
    except ImportError as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details="Enterprise MCP auth package is not available for SAML ACS handling.",
            help=_INSTALL_ENTERPRISE_MCP_AUTH_HELP,
        ) from exc

    try:
        return decode_saml_relay_state(relay_state, signing_key)
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


def _load_raw_callback_saml_config(mcp_config: Any, auth_config_id: str) -> dict[str, Any]:
    config_block = getattr(mcp_config, "config", None)
    raw_auth_config = getattr(config_block, "auth_config", None)
    if not isinstance(raw_auth_config, dict) or raw_auth_config.get("auth_type") != "saml":
        raise _build_trusted_callback_error(
            _CALLBACK_CONFIG_ERROR_MESSAGE,
            auth_config_id=auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_CONFIGURATION,
            server_name=getattr(mcp_config, "name", None),
        )
    return raw_auth_config


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


def _validate_saml_callback_state_age(state_payload: Any) -> None:
    issued_at = datetime.fromtimestamp(state_payload.ts, tz=timezone.utc)
    if datetime.now(tz=timezone.utc) - issued_at > _CALLBACK_STATE_MAX_AGE:
        raise _build_trusted_callback_error(
            _CALLBACK_VERIFICATION_FAILURE_MESSAGE,
            auth_config_id=state_payload.auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_VERIFICATION_FAILED,
        )


def _consume_callback_pkce_state(pkce_store: RedisPKCEStore, state: str, auth_config_id: str):
    try:
        pkce_state = pkce_store.consume(state)
    except Exception as exc:
        try:
            from codemie_enterprise.mcp_auth import MCPAuthRedisUnavailable

            _is_redis_unavailable = isinstance(exc, MCPAuthRedisUnavailable)
        except ImportError:
            _is_redis_unavailable = False

        if _is_redis_unavailable:
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


def _consume_saml_relay_state(relay_state_store: SAMLRelayStateStore, relay_state: str, auth_config_id: str):
    try:
        relay_state_data = relay_state_store.consume(relay_state)
    except Exception as exc:
        try:
            from codemie_enterprise.mcp_auth import MCPAuthRedisUnavailable

            is_redis_unavailable = isinstance(exc, MCPAuthRedisUnavailable)
        except ImportError:
            is_redis_unavailable = False

        if is_redis_unavailable:
            raise CallbackPageError(
                _CALLBACK_REDIS_UNAVAILABLE_MESSAGE,
                auth_config_id=auth_config_id,
                bridge_error_code=_CALLBACK_ERROR_RUNTIME,
            ) from exc
        raise

    if relay_state_data is None:
        raise CallbackPageError(
            _CALLBACK_VERIFICATION_FAILURE_MESSAGE,
            auth_config_id=auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_VERIFICATION_FAILED,
        )
    return relay_state_data


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


def _validate_saml_callback_state_matches_store(state_payload: Any, relay_state_data: Any) -> None:
    if (
        state_payload.auth_config_id != relay_state_data.auth_config_id
        or state_payload.user_id != relay_state_data.user_id
        or state_payload.session_binding_hash != relay_state_data.session_binding_hash
    ):
        raise _build_trusted_callback_error(
            _CALLBACK_VERIFICATION_FAILURE_MESSAGE,
            auth_config_id=state_payload.auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_VERIFICATION_FAILED,
        )


def _try_get_trusted_callback_context_from_state(state: str | None) -> tuple[str | None, str | None]:
    if not state:
        return None, None

    try:
        _, redis_encryption = _require_initialized_mcp_auth_components()
        state_payload = _decode_and_verify_oauth2_callback_state(state, redis_encryption.signing_key)
        mcp_config = _load_callback_mcp_config(state_payload.auth_config_id)
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


def _validate_callback_saml_auth_config(raw_auth_config: dict[str, Any], server_name: str | None, auth_config_id: str):
    try:
        from codemie_enterprise.mcp_auth import SAMLAuthConfig
    except ImportError as exc:
        raise _build_trusted_callback_error(
            _CALLBACK_RUNTIME_ERROR_MESSAGE,
            auth_config_id=auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_RUNTIME,
            server_name=server_name,
        ) from exc

    try:
        return SAMLAuthConfig.model_validate(raw_auth_config)
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


def _tms_audit_context(source: str, correlation_id: str | None = None) -> AbstractContextManager[None]:
    if _tms_audit_context_provider is None:
        return nullcontext()
    return _tms_audit_context_provider.context(source=source, correlation_id=correlation_id)


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
        with _tms_audit_context(audit_source, correlation_id=auth_config_id):
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


def _consume_saml_acs_response(
    *,
    auth_config: Any,
    saml_response: str,
    relay_state: str,
    acs_url: str,
    request_id: str,
):
    try:
        from codemie_enterprise.mcp_auth import consume_saml_acs_response as _consume_enterprise_saml_acs_response
    except ImportError as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details="Enterprise MCP auth package is not available for SAML ACS handling.",
            help=_INSTALL_ENTERPRISE_MCP_AUTH_HELP,
        ) from exc

    return _consume_enterprise_saml_acs_response(
        auth_config=auth_config,
        saml_response=saml_response,
        relay_state=relay_state,
        acs_url=acs_url,
        request_id=request_id,
    )


def build_saml_callback_response(*, saml_response: str | None, relay_state: str | None) -> HTMLResponse:
    try:
        return _build_saml_callback_response(
            saml_response=saml_response,
            relay_state=relay_state,
        )
    except CallbackPageError as exc:
        return _build_error_callback_response(exc)
    except ExtendedHTTPException as exc:
        logger.warning(f"MCP SAML callback bridge failed with HTTP exception: {exc.message}")
        return _build_error_callback_response(CallbackPageError(_CALLBACK_RUNTIME_ERROR_MESSAGE))
    except Exception as exc:
        logger.exception(f"Unexpected MCP SAML callback failure: {exc}")
        return _build_error_callback_response(CallbackPageError(_CALLBACK_RUNTIME_ERROR_MESSAGE))


def _handle_saml_acs_exception(exc: Exception, *, auth_config_id: str, server_name: str | None) -> NoReturn:
    try:
        from codemie_enterprise.mcp_auth import (
            SAMLACSError,
            SAMLACSRuntimeError,
            SAMLAssertionExpiredError,
            SAMLAssertionVerificationError,
            SAMLConfigurationError,
        )
    except ImportError:
        raise exc

    if isinstance(exc, SAMLAssertionExpiredError):
        raise _build_trusted_callback_error(
            str(exc),
            auth_config_id=auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_SESSION_EXPIRED,
            server_name=server_name,
            title=_AUTHENTICATION_FAILED_TITLE,
        ) from exc
    if isinstance(exc, SAMLAssertionVerificationError):
        raise _build_trusted_callback_error(
            str(exc),
            auth_config_id=auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_VERIFICATION_FAILED,
            server_name=server_name,
            title=_AUTHENTICATION_FAILED_TITLE,
        ) from exc
    if isinstance(exc, SAMLConfigurationError):
        raise _build_trusted_callback_error(
            _CALLBACK_CONFIG_ERROR_MESSAGE,
            auth_config_id=auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_CONFIGURATION,
            server_name=server_name,
        ) from exc
    if isinstance(exc, (SAMLACSRuntimeError, SAMLACSError)):
        raise _build_trusted_callback_error(
            _CALLBACK_RUNTIME_ERROR_MESSAGE,
            auth_config_id=auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_RUNTIME,
            server_name=server_name,
            title=_AUTHENTICATION_FAILED_TITLE,
        ) from exc
    raise exc


def _build_saml_callback_response(*, saml_response: str | None, relay_state: str | None) -> HTMLResponse:
    if not relay_state or not saml_response:
        raise CallbackPageError(_CALLBACK_VERIFICATION_FAILURE_MESSAGE)

    relay_state_store, redis_encryption, tms = _require_initialized_saml_callback_dependencies()
    state_payload = _decode_and_verify_saml_callback_state(relay_state, redis_encryption.signing_key)
    _validate_saml_callback_state_age(state_payload)
    relay_state_data = _consume_saml_relay_state(relay_state_store, relay_state, state_payload.auth_config_id)
    _validate_saml_callback_state_matches_store(state_payload, relay_state_data)

    mcp_config = _load_callback_mcp_config(state_payload.auth_config_id)
    server_name = getattr(mcp_config, "name", None)
    raw_auth_config = _load_raw_callback_saml_config(mcp_config, state_payload.auth_config_id)
    auth_config = _validate_callback_saml_auth_config(raw_auth_config, server_name, state_payload.auth_config_id)

    try:
        acs_url = build_saml_acs_url()
    except ExtendedHTTPException as exc:
        raise _build_trusted_callback_error(
            _CALLBACK_CONFIG_ERROR_MESSAGE,
            auth_config_id=state_payload.auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_CONFIGURATION,
            server_name=server_name,
        ) from exc

    try:
        token_data = _consume_saml_acs_response(
            auth_config=auth_config,
            saml_response=saml_response,
            relay_state=relay_state,
            acs_url=acs_url,
            request_id=relay_state_data.authn_request_id,
        )
    except Exception as exc:
        _handle_saml_acs_exception(exc, auth_config_id=state_payload.auth_config_id, server_name=server_name)

    _store_callback_token(
        user_id=state_payload.user_id,
        auth_config_id=state_payload.auth_config_id,
        token_data=token_data,
        server_name=server_name,
        tms=tms,
        audit_source="saml_acs",
    )
    return _build_success_callback_response(server_name, state_payload.auth_config_id)


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
    state_payload = _decode_and_verify_oauth2_callback_state(state, redis_encryption.signing_key)
    _validate_callback_state_age(state_payload)
    pkce_state = _consume_callback_pkce_state(pkce_store, state, state_payload.auth_config_id)
    _validate_callback_state_matches_pkce(state_payload, pkce_state)

    mcp_config = _load_callback_mcp_config(state_payload.auth_config_id)
    server_name = getattr(mcp_config, "name", None)
    raw_auth_config = _load_raw_callback_oauth_config(mcp_config, state_payload.auth_config_id)
    auth_config = _validate_callback_auth_config(raw_auth_config, server_name, state_payload.auth_config_id)
    try:
        resource = derive_resource_uri(getattr(mcp_config.config, "url", None))
    except ExtendedHTTPException as exc:
        raise _build_trusted_callback_error(
            _CALLBACK_CONFIG_ERROR_MESSAGE,
            auth_config_id=state_payload.auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_CONFIGURATION,
            server_name=server_name,
        ) from exc
    redirect_uri, _, _ = build_redirect_uri()
    client_secret = _decrypt_callback_client_secret(raw_auth_config, server_name, state_payload.auth_config_id)
    token_data = _exchange_callback_code(
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


def build_oauth2_initiate_response(
    *, raw_auth_config: dict[str, Any], user: User, auth_config_id: str, mcp_server_url: str | None
) -> OAuth2InitiateResponseData:
    if raw_auth_config.get("auth_type") != "oauth2":
        _raise_client_error(
            _INVALID_OAUTH2_CONFIG_MESSAGE,
            f"Expected auth_type='oauth2' but received {raw_auth_config.get('auth_type')!r}.",
        )

    redirect_uri, redirect_uri_hostname, localhost_warning = build_redirect_uri()
    resource = derive_resource_uri(mcp_server_url)
    session_binding_hash = _get_authenticated_bearer_token_hash(user)
    pkce_store, redis_encryption = _require_initialized_mcp_auth_components()

    try:
        from codemie_enterprise.mcp_auth import MCPAuthRedisUnavailable, OAuth2AuthConfig
        from codemie_enterprise.mcp_auth import build_oauth2_initiate_response as _build_enterprise_initiate_response
    except ImportError as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details="Enterprise MCP auth package is not available for OAuth2 initiation.",
            help=_INSTALL_ENTERPRISE_MCP_AUTH_HELP,
        ) from exc

    try:
        auth_config = OAuth2AuthConfig.model_validate(raw_auth_config)
    except ValidationError as exc:
        _raise_client_error(_INVALID_OAUTH2_CONFIG_MESSAGE, f"Stored OAuth2 auth_config is invalid: {exc}")

    try:
        return _build_enterprise_initiate_response(
            auth_config=auth_config,
            pkce_store=pkce_store,
            signing_key=redis_encryption.signing_key,
            user_id=user.id,
            auth_config_id=auth_config_id,
            redirect_uri=redirect_uri,
            redirect_uri_hostname=redirect_uri_hostname,
            localhost_warning=localhost_warning,
            resource=resource,
            session_binding_hash=session_binding_hash,
        )
    except MCPAuthRedisUnavailable as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details=str(exc),
            help="Retry after Redis connectivity is restored.",
        ) from exc


def build_saml_initiate_response(
    *, raw_auth_config: dict[str, Any], user: User, auth_config_id: str
) -> SAMLInitiateResponseData:
    if raw_auth_config.get("auth_type") != "saml":
        _raise_client_error(
            _INVALID_MCP_AUTH_CONFIG_MESSAGE,
            f"Expected auth_type='saml' but received {raw_auth_config.get('auth_type')!r}.",
        )

    acs_url = build_saml_acs_url()
    session_binding_hash = _get_authenticated_bearer_token_hash(user)
    relay_state_store, redis_encryption = _require_initialized_saml_initiate_dependencies()

    try:
        from codemie_enterprise.mcp_auth import MCPAuthRedisUnavailable, SAMLAuthConfig
        from codemie_enterprise.mcp_auth import build_saml_initiate_response as _build_enterprise_initiate_response
    except ImportError as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details="Enterprise MCP auth package is not available for SAML initiation.",
            help=_INSTALL_ENTERPRISE_MCP_AUTH_HELP,
        ) from exc

    try:
        auth_config = SAMLAuthConfig.model_validate(raw_auth_config)
    except ValidationError as exc:
        _raise_client_error(_INVALID_MCP_AUTH_CONFIG_MESSAGE, f"Stored SAML auth_config is invalid: {exc}")

    try:
        return _build_enterprise_initiate_response(
            auth_config=auth_config,
            relay_state_store=relay_state_store,
            signing_key=redis_encryption.signing_key,
            user_id=user.id,
            auth_config_id=auth_config_id,
            session_binding_hash=session_binding_hash,
            acs_url=acs_url,
        )
    except MCPAuthRedisUnavailable as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details=str(exc),
            help="Retry after Redis connectivity is restored.",
        ) from exc
    except ValueError as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="MCP auth initiation failed",
            details=str(exc),
            help="Retry the authentication flow. If the problem persists, contact your administrator.",
        ) from exc


def build_saml_metadata_response(*, auth_config_id: str | None) -> Response:
    if auth_config_id is None or not auth_config_id.strip():
        _raise_client_error(
            _INVALID_MCP_AUTH_CONFIG_MESSAGE,
            "Query parameter auth_config_id is required for SAML metadata generation.",
        )
    auth_config_id = auth_config_id.strip()

    from codemie.rest_api.models.mcp_config import MCPConfig

    mcp_config = MCPConfig.get_by_auth_config_id(auth_config_id)
    if mcp_config is None:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="MCP configuration not found",
            details=f"No MCP configuration found for auth_config_id '{auth_config_id}'.",
            help="Check the auth_config_id and retry.",
        )

    raw_auth_config = getattr(getattr(mcp_config, "config", None), "auth_config", None)
    if not isinstance(raw_auth_config, dict):
        _raise_client_error(
            _INVALID_MCP_AUTH_CONFIG_MESSAGE,
            "MCP configuration does not include a persisted auth_config.",
        )
    if raw_auth_config.get("auth_type") != "saml":
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=_SP_METADATA_SAML_ONLY_MESSAGE,
            details="Stored auth_config is not a SAML configuration.",
            help="Use a SAML auth_config for this endpoint.",
        )

    try:
        from codemie_enterprise.mcp_auth import SAMLAuthConfig, build_saml_sp_metadata
    except ImportError as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details=_MCP_AUTH_SERVICE_UNAVAILABLE_MESSAGE,
            help=_MCP_AUTH_RETRY_AFTER_INIT_HELP,
        ) from exc

    try:
        auth_config = SAMLAuthConfig.model_validate(raw_auth_config)
    except ValidationError as exc:
        _raise_client_error(_INVALID_MCP_AUTH_CONFIG_MESSAGE, f"Stored SAML auth_config is invalid: {exc}")

    try:
        metadata_xml = build_saml_sp_metadata(auth_config=auth_config, acs_url=build_saml_acs_url())
    except ValueError as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=_SP_METADATA_GENERATION_FAILED_MESSAGE,
            details=str(exc),
            help="Retry the authentication flow. If the problem persists, contact your administrator.",
        ) from exc

    return Response(content=metadata_xml, media_type="application/samlmetadata+xml")


def validate_auth_config_core(raw_dict: dict[str, Any], transport: str) -> list[str]:
    auth_type = raw_dict.get("auth_type")
    if auth_type not in SUPPORTED_AUTH_TYPES:
        return [f"Unsupported auth_type: {auth_type}"]

    errors: list[str] = []
    for field_name in _REQUIRED_AUTH_FIELDS[auth_type]:
        if _is_missing_required_value(raw_dict.get(field_name)):
            errors.append(f"Required field '{field_name}' missing for auth_type '{auth_type}'")

    if auth_type == "saml" and transport == "http":
        errors.append(_SAML_HTTP_ERROR)

    for field_name in _HTTPS_ONLY_FIELDS:
        value = raw_dict.get(field_name)
        if _is_missing_required_value(value):
            continue
        if not _uses_https(value):
            errors.append(f"'{field_name}' must use HTTPS")

    return errors


def validate_auth_config_on_save(raw_dict: dict[str, Any], transport: str) -> list[str]:
    errors = validate_auth_config_core(raw_dict, transport)
    if errors or not HAS_MCP_AUTH:
        return errors

    try:
        from codemie_enterprise.mcp_auth.validation import validate_auth_config_structure
    except ImportError as exc:
        missing_name = getattr(exc, "name", "") or ""
        if missing_name == "codemie_enterprise" or missing_name.startswith("codemie_enterprise."):
            return errors
        raise

    return errors + validate_auth_config_structure(raw_dict, transport)


def decrypt_confidential_client_secret(auth_config: dict[str, Any]) -> str | None:
    if auth_config.get("auth_type") != "oauth2" or auth_config.get("client_type") != "confidential":
        return None

    client_secret = auth_config.get("client_secret")
    if _is_missing_required_value(client_secret):
        return None
    return encryption_service.decrypt(client_secret)


def is_mcp_auth_enabled() -> bool:
    """Check if MCP Auth feature is both available and enabled.

    Two conditions must be true:
    1. codemie-enterprise package with mcp_auth module is installed (HAS_MCP_AUTH)
    2. MCP_AUTH_ENABLED environment variable is set to True
    """
    from codemie.configs import config  # deferred import to avoid circular dependency

    if not HAS_MCP_AUTH:
        if config.MCP_AUTH_ENABLED and config.MCP_AUTH_TMS_ENABLED:
            raise RuntimeError("enterprise MCP auth package is unavailable while MCP auth TMS is enabled")
        return False

    return config.MCP_AUTH_ENABLED


def has_any_credentials_for_auth_config(auth_config_id: str) -> bool:
    """Return True on bridge errors to fail closed and block ID changes."""
    if not is_mcp_auth_enabled():
        return False
    if _tms is None:
        logger.warning(
            "Failed to check stored credentials because MCP auth TMS is not initialized; "
            f"blocking auth_config.id change for auth_config_id={auth_config_id}"
        )
        return True

    try:
        with _tms_audit_context("status_check", correlation_id=auth_config_id):
            return bool(_tms.has_any_credentials(auth_config_id))
    except Exception as exc:
        logger.warning(
            "Failed to check stored credentials for "
            f"auth_config_id={auth_config_id}; blocking auth_config.id change: {type(exc).__name__}"
        )
        return True


def invalidate_credentials_for_auth_config(auth_config_id: str) -> None:
    if not is_mcp_auth_enabled() or _tms is None:
        return

    with _tms_audit_context("admin_config_change", correlation_id=auth_config_id):
        _tms.invalidate_by_config(auth_config_id)


def _validate_hmac_secret() -> None:
    if not is_mcp_auth_enabled():
        return

    from codemie.configs import config  # deferred import to avoid circular dependency

    secret_length = len(config.MCP_AUTH_HMAC_SECRET.encode("utf-8"))
    if secret_length < 32:
        raise RuntimeError(
            "MCP auth requires MCP_AUTH_HMAC_SECRET to be set to at least 32 bytes when MCP_AUTH_ENABLED=True. "
            "Configure a strong shared secret and restart the application."
        )


def _build_alert_callback() -> Callable[[], None]:
    def _alert() -> None:
        logger.warning("MCP auth Redis health degraded for this instance")

    return _alert


def _build_authentication_required_exception(
    auth_config_id: str,
    *,
    status: str = "authentication_required",
    auth_type: str | None = None,
    error_context: str | None = None,
) -> MCPAuthenticationRequiredException:
    payload = get_mcp_auth_status_payload(auth_config_id) or {"auth_config_id": auth_config_id}
    payload.update(
        {
            "status": status,
            "auth_type": auth_type,
            "error_context": error_context,
        }
    )
    return MCPAuthenticationRequiredException(payload)


def _normalize_tms_environment(environment: str) -> str:
    normalized_environment = environment.strip().lower()
    return {
        "development": "dev",
        "develop": "dev",
        "prod": "production",
        "tests": "test",
        "preview": "staging",
        "prod-preview": "staging",
    }.get(normalized_environment, normalized_environment)


def _build_token_management_system(redis_client: Any, audit_context_provider: Any) -> Any:
    from codemie.clients.postgres import PostgresClient
    from codemie.configs import config
    from codemie.service.encryption.encryption_factory import EncryptionFactory, EncryptionType
    from codemie_enterprise.mcp_auth import (
        AEADEnvelopeEncryption,
        ExternalEncryptionServiceKeyManagementProvider,
        MockTokenManagementSystem,
        PostgresTokenManagementSystem,
        RedisTMSRefreshLock,
        TMSConfig,
        TMSRuntimeEnvironment,
    )

    tms_environment = _normalize_tms_environment(config.ENV)

    if tms_environment == TMSRuntimeEnvironment.PRODUCTION and (
        not config.MCP_AUTH_TMS_ENABLED or config.MCP_AUTH_TMS_ALLOW_MOCK
    ):
        raise RuntimeError("production MCP auth requires TMS enabled")

    tms_config = TMSConfig(
        enabled=config.MCP_AUTH_TMS_ENABLED,
        environment=tms_environment,
        refresh_timeout_seconds=config.MCP_AUTH_TMS_REFRESH_TIMEOUT_SECONDS,
        redis_lock_enabled=config.MCP_AUTH_TMS_REDIS_LOCK_ENABLED,
        redis_lock_ttl_seconds=config.MCP_AUTH_TMS_REDIS_LOCK_TTL_SECONDS,
        audit_required=config.MCP_AUTH_TMS_AUDIT_REQUIRED,
        audit_fallback_enabled=config.MCP_AUTH_TMS_AUDIT_FALLBACK_ENABLED,
        audit_fallback_sink_configured=config.MCP_AUTH_TMS_AUDIT_FALLBACK_SINK_CONFIGURED,
        kms_key_id=config.MCP_AUTH_TMS_KMS_KEY_ID,
        encryption_context_prefix=config.MCP_AUTH_TMS_ENCRYPTION_CONTEXT_PREFIX,
        allow_mock_tms=config.MCP_AUTH_TMS_ALLOW_MOCK,
        audit_sanitize_diagnostics=config.MCP_AUTH_TMS_AUDIT_SANITIZE_DIAGNOSTICS,
    )

    if not tms_config.enabled:
        if not tms_config.allow_mock_tms:
            raise RuntimeError("production MCP auth requires TMS enabled or non-production mock guard")
        return MockTokenManagementSystem()

    encryption_type = EncryptionFactory.get_current_encryption_service_type()
    local_encryption_types = {
        EncryptionType.PLAIN_TEXT,
        EncryptionType.BASE64_ENCRYPTION,
    }
    if tms_config.environment == TMSRuntimeEnvironment.PRODUCTION and encryption_type in local_encryption_types:
        raise RuntimeError("Production MCP auth TMS requires a KMS-backed encryption provider")

    if encryption_type in local_encryption_types:
        from codemie_enterprise.mcp_auth.tms_crypto import LocalKeyManagementProvider

        kms_provider = LocalKeyManagementProvider(config.MCP_AUTH_HMAC_SECRET, tms_config.kms_key_id)
    else:
        kms_provider = ExternalEncryptionServiceKeyManagementProvider(
            encryption_service=EncryptionFactory.get_current_encryption_service(),
            kms_key_id=tms_config.kms_key_id,
        )

    refresh_lock = (
        RedisTMSRefreshLock(
            redis_client, tms_config.redis_lock_ttl_seconds, namespace=config.MCP_AUTH_REDIS_KEY_NAMESPACE
        )
        if tms_config.redis_lock_enabled
        else None
    )

    return PostgresTokenManagementSystem(
        config=tms_config,
        connection_factory=lambda: PostgresClient.get_engine().begin(),
        encryption=AEADEnvelopeEncryption(kms_provider=kms_provider, kms_key_id=tms_config.kms_key_id),
        audit_context_provider=audit_context_provider,
        refresh_lock=refresh_lock,
    )


async def _bridge_consumer(bridge_queue: asyncio.Queue[str], mcp_auth_service: _CleanupEnqueuer) -> None:
    while True:
        user_id = await bridge_queue.get()
        try:
            try:
                mcp_auth_service.enqueue_cleanup(user_id)
            except Exception as exc:
                logger.exception(f"MCP auth cleanup bridge failed for user_id={user_id}: {exc}")
        finally:
            bridge_queue.task_done()


def enqueue_mcp_auth_cleanup(user_id: str) -> None:
    if _bridge_loop is None or _bridge_queue is None or _bridge_task is None or _bridge_task.done():
        logger.debug(f"Skipping MCP auth cleanup enqueue for user_id={user_id}: bridge unavailable")
        return

    try:
        _bridge_loop.call_soon_threadsafe(_bridge_queue.put_nowait, user_id)
    except RuntimeError:
        logger.debug(f"Skipping MCP auth cleanup enqueue for user_id={user_id}: bridge loop closed")


def _cleanup_partial_mcp_auth_startup(bridge_task: Any, mcp_auth_service: Any, redis_client: Any) -> None:
    if bridge_task is not None:
        try:
            bridge_task.cancel()
        except Exception as exc:
            logger.warning(f"MCP auth bridge task cancellation failed after startup error: {type(exc).__name__}")
    if mcp_auth_service is not None:
        try:
            mcp_auth_service.shutdown()
        except Exception as exc:
            logger.warning(f"MCP auth service shutdown failed after startup error: {type(exc).__name__}")
    try:
        redis_client.close()
    except Exception as exc:
        logger.warning(f"MCP auth Redis client shutdown failed after startup error: {type(exc).__name__}")


def initialize_mcp_auth() -> None:
    global _initialized, _bridge_queue, _bridge_task, _bridge_loop, _mcp_auth_service, _redis_client, _tms
    global _pkce_store, _saml_relay_state_store, _redis_encryption
    global _tms_audit_context_provider

    _validate_hmac_secret()
    if not is_mcp_auth_enabled() or _initialized:
        return

    from codemie.configs import config
    from codemie.service.mcp.toolkit_service import MCPToolkitService
    from codemie_enterprise.mcp_auth import (
        DCRCredentialsCache,
        ContextVarTMSAuditContextProvider,
        DiscoveryMetadataCache,
        MCPAuthResolver,
        MCPAuthService,
        MCPAuthServiceConfig,
        RedisEncryption,
        RedisPKCEStore,
        SAMLRelayStateStore,
    )

    bridge_loop = asyncio.get_running_loop()
    bridge_queue: asyncio.Queue[str] = asyncio.Queue()
    redis_client = create_redis_client()
    bridge_task: Any = None
    mcp_auth_service: Any = None

    try:
        redis_key_namespace = config.MCP_AUTH_REDIS_KEY_NAMESPACE
        redis_encryption = RedisEncryption(config.MCP_AUTH_HMAC_SECRET)
        pkce_store = RedisPKCEStore(redis_client, redis_encryption, namespace=redis_key_namespace)
        saml_relay_state_store = SAMLRelayStateStore(redis_client, redis_encryption, namespace=redis_key_namespace)
        audit_context_provider = ContextVarTMSAuditContextProvider()
        token_management_system = _build_token_management_system(redis_client, audit_context_provider)
        mcp_auth_service = MCPAuthService(
            config=MCPAuthServiceConfig(redis_key_namespace=redis_key_namespace),
            redis_client=redis_client,
            pkce_store=pkce_store,
            discovery_cache=DiscoveryMetadataCache(redis_client, namespace=redis_key_namespace),
            dcr_credentials_cache=DCRCredentialsCache(redis_client, redis_encryption, namespace=redis_key_namespace),
            token_management_system=token_management_system,
            alert_callback=_build_alert_callback(),
            audit_context_provider=audit_context_provider,
        )
        mcp_auth_service.initialize()
        bridge_task = bridge_loop.create_task(_bridge_consumer(bridge_queue, mcp_auth_service))

        resolver = MCPAuthResolver(
            token_management_system,
            _build_authentication_required_exception,
            audit_context_provider=audit_context_provider,
        )
        if type(resolver) not in _registered_resolver_types:
            MCPToolkitService.register_auth_resolver(resolver)
            _registered_resolver_types.add(type(resolver))
    except Exception:
        _cleanup_partial_mcp_auth_startup(bridge_task, mcp_auth_service, redis_client)
        raise

    _bridge_loop = bridge_loop
    _bridge_queue = bridge_queue
    _redis_client = redis_client
    _mcp_auth_service = mcp_auth_service
    _bridge_task = bridge_task
    _tms = token_management_system
    _pkce_store = pkce_store
    _saml_relay_state_store = saml_relay_state_store
    _redis_encryption = redis_encryption
    _tms_audit_context_provider = audit_context_provider

    from codemie.service.security.token_exchange_service import TokenExchangeService
    from codemie.service.security.oidc_token_exchange_service import OIDCTokenExchangeService

    TokenExchangeService.set_tms(token_management_system, audit_context_provider)
    OIDCTokenExchangeService.set_tms(token_management_system, audit_context_provider)

    _initialized = True


async def shutdown_mcp_auth() -> None:
    global _initialized, _bridge_queue, _bridge_task, _bridge_loop, _mcp_auth_service, _redis_client, _tms
    global _pkce_store, _saml_relay_state_store, _redis_encryption
    global _tms_audit_context_provider

    if not _initialized and _bridge_task is None and _mcp_auth_service is None and _redis_client is None:
        return

    try:
        bridge_task = _bridge_task
        if bridge_task is not None:
            try:
                bridge_task.cancel()
            except Exception as exc:
                logger.warning(f"MCP auth bridge task cancellation failed: {type(exc).__name__}")
            else:
                with suppress(asyncio.CancelledError):
                    try:
                        await bridge_task
                    except Exception as exc:
                        logger.warning(f"MCP auth bridge task shutdown failed: {type(exc).__name__}")

        if _mcp_auth_service is not None:
            try:
                _mcp_auth_service.shutdown()
            except Exception as exc:
                logger.warning(f"MCP auth service shutdown failed: {type(exc).__name__}")

        if _redis_client is not None:
            try:
                _redis_client.close()
            except Exception as exc:
                logger.warning(f"MCP auth Redis client shutdown failed: {type(exc).__name__}")
    finally:
        _initialized = False
        _bridge_queue = None
        _bridge_task = None
        _bridge_loop = None
        _mcp_auth_service = None
        _redis_client = None
        _tms = None
        _pkce_store = None
        _saml_relay_state_store = None
        _redis_encryption = None
        _tms_audit_context_provider = None

        from codemie.service.security.token_exchange_service import TokenExchangeService
        from codemie.service.security.oidc_token_exchange_service import OIDCTokenExchangeService

        TokenExchangeService.clear_tms()
        OIDCTokenExchangeService.clear_tms()

        _registered_resolver_types.clear()


def get_mcp_auth_status_payload(auth_config_id: str) -> dict[str, str] | None:
    from codemie.rest_api.models.mcp_config import MCPConfig

    mcp_config = MCPConfig.get_by_auth_config_id(auth_config_id)
    if mcp_config is None:
        return None

    return {
        "auth_config_id": auth_config_id,
        "mcp_config_id": mcp_config.id,
        "mcp_server_name": mcp_config.name,
    }
