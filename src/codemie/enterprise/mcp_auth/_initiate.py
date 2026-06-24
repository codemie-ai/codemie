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

from typing import TYPE_CHECKING, Any

from fastapi import status
from fastapi.responses import Response
from pydantic import ValidationError

from codemie.core.exceptions import ExtendedHTTPException, MCPAuthenticationRequiredException
from codemie.rest_api.security.user import User

from ._common import _build_discovered_initiate_url, _raise_client_error
from ._constants import (
    _INSTALL_ENTERPRISE_MCP_AUTH_HELP,
    _INVALID_MCP_AUTH_CONFIG_MESSAGE,
    _INVALID_OAUTH2_CONFIG_MESSAGE,
    _MCP_AUTH_REDIS_RETRY_HELP,
    _MCP_AUTH_RETRY_AFTER_INIT_HELP,
    _MCP_AUTH_SERVICE_UNAVAILABLE_MESSAGE,
    _MCP_AUTH_TEMPORARILY_UNAVAILABLE,
    _SP_METADATA_GENERATION_FAILED_MESSAGE,
    _SP_METADATA_SAML_ONLY_MESSAGE,
)
from ._post_auth import _map_scope_recovery_decision
from ._uri import _describe_stored_redirect_uri, _get_authenticated_bearer_token_hash

# See _oauth2_callback._deps for the rationale: tests patch helpers as
# ``dependencies.X``; internal calls below resolve through this module
# reference at call time so patches take effect.
from . import dependencies as _deps  # noqa: E402

if TYPE_CHECKING:
    from codemie_enterprise.mcp_auth import OAuth2InitiateResponseData, SAMLInitiateResponseData


def build_oauth2_initiate_response(
    *, raw_auth_config: dict[str, Any], user: User, auth_config_id: str, mcp_server_url: str | None
) -> OAuth2InitiateResponseData:
    if raw_auth_config.get("auth_type") != "oauth2":
        _raise_client_error(
            _INVALID_OAUTH2_CONFIG_MESSAGE,
            f"Expected auth_type='oauth2' but received {raw_auth_config.get('auth_type')!r}.",
        )

    redirect_uri, redirect_uri_hostname, localhost_warning = _deps.build_redirect_uri()
    resource = _deps.derive_resource_uri(mcp_server_url)
    session_binding_hash = _get_authenticated_bearer_token_hash(user)
    pkce_store, redis_encryption = _deps._require_initialized_mcp_auth_components()

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
            help=_MCP_AUTH_REDIS_RETRY_HELP,
        ) from exc


def build_discovered_oauth2_initiate_response(
    *,
    mcp_config: Any,
    user: User,
    discovered_flow_id: str | None = None,
) -> OAuth2InitiateResponseData:
    if getattr(mcp_config, "config", None) is None:
        _raise_client_error(
            _INVALID_OAUTH2_CONFIG_MESSAGE,
            "MCP configuration does not include a server config block.",
        )
    if isinstance(getattr(mcp_config.config, "auth_config", None), dict):
        _raise_client_error(
            _INVALID_OAUTH2_CONFIG_MESSAGE,
            "Discovered OAuth2 initiation is only valid for MCP servers without persisted auth_config.",
        )

    session_binding_hash = _get_authenticated_bearer_token_hash(user)

    # Non-raising probe — only genuine None triggers heal; infra exceptions propagate unchanged
    _store = _deps._require_initialized_discovered_flow_store()
    _probe = (
        _store.get(discovered_flow_id)
        if discovered_flow_id
        else _store.get_for_binding(user.id, session_binding_hash, mcp_config.id)
    )

    # Heal on genuine absence
    if _probe is None:
        from codemie.service.mcp.toolkit_service import MCPToolkitService  # lazy — avoids import cycle

        _new_flow_id = MCPToolkitService.ensure_discovered_snapshot_for_server(
            mcp_config=mcp_config,
            user_id=user.id,
            session_binding_hash=session_binding_hash,
        )
        if _new_flow_id is not None:
            _probe = _store.get_for_binding(user.id, session_binding_hash, mcp_config.id)

    # Fall back to original raising loaders — same 400 as today on total miss
    if _probe is not None:
        snapshot = _probe
    elif discovered_flow_id:
        snapshot = _deps._load_discovered_flow_snapshot_or_error(discovered_flow_id)
    else:
        snapshot = _load_discovered_flow_snapshot_for_binding_or_error(
            user_id=user.id,
            session_binding_hash=session_binding_hash,
            mcp_config_id=mcp_config.id,
        )
    _deps._validate_discovered_snapshot_context(
        snapshot,
        user_id=user.id,
        session_binding_hash=session_binding_hash,
        mcp_config_id=mcp_config.id,
    )
    if getattr(snapshot, "status", None) != "authentication_required" or getattr(snapshot, "flow_config", None) is None:
        _raise_client_error(
            _INVALID_OAUTH2_CONFIG_MESSAGE,
            "Discovered OAuth2 flow cannot be initiated. Configure auth_config manually for this server.",
        )

    redirect_uri, redirect_uri_hostname, localhost_warning = _describe_stored_redirect_uri(
        getattr(snapshot, "redirect_uri", None)
    )
    pkce_store, redis_encryption = _deps._require_initialized_mcp_auth_components()
    try:
        from codemie_enterprise.mcp_auth import MCPAuthRedisUnavailable
        from codemie_enterprise.mcp_auth import build_oauth2_initiate_response as _build_enterprise_initiate_response
    except ImportError as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details="Enterprise MCP auth package is not available for OAuth2 initiation.",
            help=_INSTALL_ENTERPRISE_MCP_AUTH_HELP,
        ) from exc

    try:
        return _build_enterprise_initiate_response(
            auth_config=snapshot.flow_config,
            pkce_store=pkce_store,
            signing_key=redis_encryption.signing_key,
            user_id=user.id,
            auth_config_id=snapshot.discovered_auth_id,
            redirect_uri=redirect_uri,
            redirect_uri_hostname=redirect_uri_hostname,
            localhost_warning=localhost_warning,
            resource=snapshot.canonical_resource,
            session_binding_hash=session_binding_hash,
            discovered_flow_id=snapshot.discovered_flow_id,
        )
    except MCPAuthRedisUnavailable as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details=str(exc),
            help=_MCP_AUTH_REDIS_RETRY_HELP,
        ) from exc


def build_recovery_oauth2_initiate_response(
    *,
    mcp_config: Any,
    user: User,
    recovery_flow_id: str,
) -> OAuth2InitiateResponseData:
    session_binding_hash = _get_authenticated_bearer_token_hash(user)
    try:
        from codemie_enterprise.mcp_auth import (
            MCPAuthRedisUnavailable,
            RecoveryAttemptsExhausted,
            build_recovery_oauth2_initiate_response as _build_enterprise_recovery_initiate_response,
            get_recovery_oauth2_initiate_exhausted_decision,
        )
    except ImportError as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details="Enterprise MCP auth package is not available for recovery OAuth2 initiation.",
            help=_INSTALL_ENTERPRISE_MCP_AUTH_HELP,
        ) from exc

    exhausted_decision = get_recovery_oauth2_initiate_exhausted_decision(
        recovery_flow_id=recovery_flow_id,
        mcp_config_id=mcp_config.id,
        user_id=user.id,
        session_binding_hash=session_binding_hash,
    )
    if exhausted_decision is not None:
        server_payload = _map_scope_recovery_decision(exhausted_decision, workflow_execution_id=None)
        raise MCPAuthenticationRequiredException({"error": "authentication_required", "servers": [server_payload]})

    redirect_uri, redirect_uri_hostname, localhost_warning = _deps.build_redirect_uri()
    pkce_store, redis_encryption = _deps._require_initialized_mcp_auth_components()
    try:
        return _build_enterprise_recovery_initiate_response(
            recovery_flow_id=recovery_flow_id,
            mcp_config_id=mcp_config.id,
            pkce_store=pkce_store,
            signing_key=redis_encryption.signing_key,
            user_id=user.id,
            session_binding_hash=session_binding_hash,
            redirect_uri=redirect_uri,
            redirect_uri_hostname=redirect_uri_hostname,
            localhost_warning=localhost_warning,
        )
    except RecoveryAttemptsExhausted as exc:
        server_payload = _map_scope_recovery_decision(exc.decision, workflow_execution_id=None)
        raise MCPAuthenticationRequiredException(
            {"error": "authentication_required", "servers": [server_payload]}
        ) from exc
    except MCPAuthRedisUnavailable as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details=str(exc),
            help=_MCP_AUTH_REDIS_RETRY_HELP,
        ) from exc


def _load_discovered_flow_snapshot_for_binding_or_error(
    *,
    user_id: str,
    session_binding_hash: str,
    mcp_config_id: str,
) -> Any:
    try:
        snapshot = _deps._require_initialized_discovered_flow_store().get_for_binding(
            user_id,
            session_binding_hash,
            mcp_config_id,
        )
    except ExtendedHTTPException:
        _raise_client_error(
            _INVALID_MCP_AUTH_CONFIG_MESSAGE,
            "No discovered OAuth2 flow is available for this MCP configuration and session.",
        )
    except Exception as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details=_MCP_AUTH_SERVICE_UNAVAILABLE_MESSAGE,
            help=_MCP_AUTH_RETRY_AFTER_INIT_HELP,
        ) from exc
    if snapshot is None:
        _raise_client_error(
            _INVALID_OAUTH2_CONFIG_MESSAGE,
            "No discovered OAuth2 flow is available for this MCP configuration and session.",
        )
    return snapshot


def build_saml_initiate_response(
    *, raw_auth_config: dict[str, Any], user: User, auth_config_id: str
) -> SAMLInitiateResponseData:
    if raw_auth_config.get("auth_type") != "saml":
        _raise_client_error(
            _INVALID_MCP_AUTH_CONFIG_MESSAGE,
            f"Expected auth_type='saml' but received {raw_auth_config.get('auth_type')!r}.",
        )

    acs_url = _deps.build_saml_acs_url()
    session_binding_hash = _get_authenticated_bearer_token_hash(user)
    relay_state_store, redis_encryption = _deps._require_initialized_saml_initiate_dependencies()

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
            help=_MCP_AUTH_REDIS_RETRY_HELP,
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
        metadata_xml = build_saml_sp_metadata(auth_config=auth_config, acs_url=_deps.build_saml_acs_url())
    except ValueError as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=_SP_METADATA_GENERATION_FAILED_MESSAGE,
            details=str(exc),
            help="Retry the authentication flow. If the problem persists, contact your administrator.",
        ) from exc

    return Response(content=metadata_xml, media_type="application/samlmetadata+xml")


def build_discovered_auth_status_response(*, mcp_config: Any, user: User) -> dict[str, Any]:
    if getattr(mcp_config, "config", None) is None or not getattr(mcp_config.config, "url", None):
        _raise_client_error(
            _INVALID_MCP_AUTH_CONFIG_MESSAGE,
            "MCP configuration does not include a server URL for discovered auth status.",
        )

    session_binding_hash = _get_authenticated_bearer_token_hash(user)
    snapshot = _load_discovered_flow_snapshot_for_binding_or_error(
        user_id=user.id,
        session_binding_hash=session_binding_hash,
        mcp_config_id=mcp_config.id,
    )
    _deps._validate_discovered_snapshot_context(
        snapshot,
        user_id=user.id,
        session_binding_hash=session_binding_hash,
        mcp_config_id=mcp_config.id,
    )
    if snapshot.status == "config_error":
        return {
            "mcp_config_id": mcp_config.id,
            "mcp_config_name": mcp_config.name,
            "mcp_server_name": mcp_config.name,
            "auth_config_id": snapshot.discovered_auth_id,
            "auth_type": "oauth2",
            "as_hostname": snapshot.as_hostname,
            "status": "config_error",
            "error_context": snapshot.error_context,
            "initiate_url": None,
        }

    tms = _deps._require_initialized_tms()
    try:
        from codemie_enterprise.mcp_auth import evaluate_discovered_auth_status
    except ImportError as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details="Enterprise MCP auth package is not available for status handling.",
            help=_INSTALL_ENTERPRISE_MCP_AUTH_HELP,
        ) from exc

    status_value, resolved_auth_type, error_context = evaluate_discovered_auth_status(
        tms=tms,
        user_id=user.id,
        auth_config_id=snapshot.discovered_auth_id,
    )
    return {
        "mcp_config_id": mcp_config.id,
        "mcp_config_name": mcp_config.name,
        "mcp_server_name": mcp_config.name,
        "auth_config_id": snapshot.discovered_auth_id,
        "auth_type": resolved_auth_type or "oauth2",
        "as_hostname": snapshot.as_hostname,
        "status": status_value,
        "error_context": error_context,
        "initiate_url": _build_discovered_initiate_url(snapshot.discovered_flow_id),
    }
