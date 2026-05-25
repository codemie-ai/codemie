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
from typing import TYPE_CHECKING, Any, NoReturn

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
from ._common import CallbackPageError
from ._constants import (
    _AUTHENTICATION_FAILED_TITLE,
    _CALLBACK_CONFIG_ERROR_MESSAGE,
    _CALLBACK_ERROR_CONFIGURATION,
    _CALLBACK_ERROR_RUNTIME,
    _CALLBACK_ERROR_SESSION_EXPIRED,
    _CALLBACK_ERROR_VERIFICATION_FAILED,
    _CALLBACK_REDIS_UNAVAILABLE_MESSAGE,
    _CALLBACK_RUNTIME_ERROR_MESSAGE,
    _CALLBACK_STATE_MAX_AGE,
    _CALLBACK_VERIFICATION_FAILURE_MESSAGE,
    _INSTALL_ENTERPRISE_MCP_AUTH_HELP,
    _MCP_AUTH_TEMPORARILY_UNAVAILABLE,
)
from ._oauth2_callback import _is_mcp_auth_redis_unavailable

# See _oauth2_callback._deps for the rationale: tests patch helpers as
# ``dependencies.X``; internal calls below resolve through this module
# reference at call time so patches take effect.
from . import dependencies as _deps  # noqa: E402

if TYPE_CHECKING:
    from codemie_enterprise.mcp_auth import SAMLRelayStateStore


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


def _validate_saml_callback_state_age(state_payload: Any) -> None:
    issued_at = datetime.fromtimestamp(state_payload.ts, tz=timezone.utc)
    if datetime.now(tz=timezone.utc) - issued_at > _CALLBACK_STATE_MAX_AGE:
        raise _build_trusted_callback_error(
            _CALLBACK_VERIFICATION_FAILURE_MESSAGE,
            auth_config_id=state_payload.auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_VERIFICATION_FAILED,
        )


def _consume_saml_relay_state(relay_state_store: SAMLRelayStateStore, relay_state: str, auth_config_id: str):
    try:
        relay_state_data = relay_state_store.consume(relay_state)
    except Exception as exc:
        if _is_mcp_auth_redis_unavailable(exc):
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

    relay_state_store, redis_encryption, tms = _deps._require_initialized_saml_callback_dependencies()
    state_payload = _deps._decode_and_verify_saml_callback_state(relay_state, redis_encryption.signing_key)
    _deps._validate_saml_callback_state_age(state_payload)
    relay_state_data = _deps._consume_saml_relay_state(relay_state_store, relay_state, state_payload.auth_config_id)
    _deps._validate_saml_callback_state_matches_store(state_payload, relay_state_data)

    mcp_config = _deps._load_callback_mcp_config(state_payload.auth_config_id)
    server_name = getattr(mcp_config, "name", None)
    raw_auth_config = _deps._load_raw_callback_saml_config(mcp_config, state_payload.auth_config_id)
    auth_config = _deps._validate_callback_saml_auth_config(raw_auth_config, server_name, state_payload.auth_config_id)

    try:
        acs_url = _deps.build_saml_acs_url()
    except ExtendedHTTPException as exc:
        raise _build_trusted_callback_error(
            _CALLBACK_CONFIG_ERROR_MESSAGE,
            auth_config_id=state_payload.auth_config_id,
            bridge_error_code=_CALLBACK_ERROR_CONFIGURATION,
            server_name=server_name,
        ) from exc

    try:
        token_data = _deps._consume_saml_acs_response(
            auth_config=auth_config,
            saml_response=saml_response,
            relay_state=relay_state,
            acs_url=acs_url,
            request_id=relay_state_data.authn_request_id,
        )
    except Exception as exc:
        _handle_saml_acs_exception(exc, auth_config_id=state_payload.auth_config_id, server_name=server_name)

    _deps._store_callback_token(
        user_id=state_payload.user_id,
        auth_config_id=state_payload.auth_config_id,
        token_data=token_data,
        server_name=server_name,
        tms=tms,
        audit_source="saml_acs",
    )
    return _build_success_callback_response(server_name, state_payload.auth_config_id)
