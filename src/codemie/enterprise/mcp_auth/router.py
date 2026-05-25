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

from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, Form, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from codemie.core.exceptions import ExtendedHTTPException
from codemie.enterprise.mcp_auth.dependencies import (
    SUPPORTED_AUTH_TYPES,
    _INVALID_MCP_AUTH_CONFIG_MESSAGE,
    _require_initialized_tms,
    build_client_metadata_document_response,
    build_discovered_auth_status_response,
    build_discovered_oauth2_initiate_response,
    build_oauth2_callback_response,
    build_oauth2_callback_page_script_response,
    build_oauth2_initiate_response,
    build_recovery_oauth2_initiate_response,
    build_saml_callback_response,
    build_saml_initiate_response,
    build_saml_metadata_response,
    derive_as_hostname,
    derive_initiate_url,
    ensure_client_metadata_document_available,
    is_mcp_auth_enabled,
    MCPAuthEnterpriseUnavailableError,
)
from codemie.rest_api.models.mcp_config import MCPConfig
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

_MCP_AUTH_ROUTER_TAG = "MCP Auth"

router = APIRouter(
    tags=[_MCP_AUTH_ROUTER_TAG],
    prefix="/v1/mcp-auth",
)

enabled_router = APIRouter(
    tags=[_MCP_AUTH_ROUTER_TAG],
    prefix="/v1/mcp-auth",
)

cimd_router = APIRouter(tags=[_MCP_AUTH_ROUTER_TAG])

enabled_cimd_router = APIRouter(tags=[_MCP_AUTH_ROUTER_TAG])


class MCPAuthDisabledResponse(BaseModel):
    feature: str
    state: str
    action: str


class OAuth2InitiateRequest(BaseModel):
    mcp_config_id: str = Field(min_length=1)
    discovered_flow_id: str | None = Field(default=None, min_length=1)
    recovery_flow_id: str | None = Field(default=None, min_length=1)


class OAuth2InitiateResponse(BaseModel):
    auth_url: str
    redirect_uri_hostname: str
    localhost_warning: bool


class SAMLInitiateRequest(BaseModel):
    mcp_config_id: str = Field(min_length=1)


class SAMLInitiateResponse(BaseModel):
    auth_url: str


class MCPAuthStatusResponse(BaseModel):
    mcp_config_id: str
    mcp_config_name: str
    mcp_server_name: str
    auth_config_id: str | None
    auth_type: Literal["oauth2", "saml"]
    as_hostname: str | None
    status: Literal["authenticated", "authentication_required", "session_expired", "config_error"]
    error_context: str | dict[str, Any] | None
    initiate_url: str | None


_DISABLED_RESPONSE = MCPAuthDisabledResponse(
    feature="MCP Authorization",
    state="inactive — enterprise package not installed or MCP_AUTH_ENABLED not set",
    action="Enable MCP_AUTH_ENABLED and install the enterprise package",
)

_CLIENT_METADATA_DOCUMENT_PATH = "/oauth/client-metadata.json"
_CLIENT_METADATA_QUERY_REJECTED_MESSAGE = "Invalid client metadata document request"
_CLIENT_METADATA_QUERY_REJECTED_DETAILS = "Query strings are not allowed for this endpoint."
_CLIENT_METADATA_CONFIG_ERROR_DETAILS = "Client metadata document configuration is invalid."
_CLIENT_METADATA_CONFIG_ERROR_HELP = "Review CALLBACK_API_BASE_URL and OAuth2 callback configuration."


def _disabled_json_response() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=_DISABLED_RESPONSE.model_dump(),
    )


@router.post(
    "/oauth2/initiate",
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    response_model=MCPAuthDisabledResponse,
)
def initiate_oauth2() -> MCPAuthDisabledResponse:
    return _DISABLED_RESPONSE


@cimd_router.get(
    _CLIENT_METADATA_DOCUMENT_PATH,
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    response_model=MCPAuthDisabledResponse,
)
def client_metadata_document() -> MCPAuthDisabledResponse:
    return _DISABLED_RESPONSE


@enabled_cimd_router.get(
    _CLIENT_METADATA_DOCUMENT_PATH,
    response_class=Response,
)
def client_metadata_document_enabled(request: Request) -> Response:
    try:
        ensure_client_metadata_document_available()
    except MCPAuthEnterpriseUnavailableError:
        return _disabled_json_response()
    if request.url.query:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=_CLIENT_METADATA_QUERY_REJECTED_MESSAGE,
            details=_CLIENT_METADATA_QUERY_REJECTED_DETAILS,
            help="Request the metadata document URL without query parameters.",
        )
    try:
        return build_client_metadata_document_response()
    except MCPAuthEnterpriseUnavailableError:
        return _disabled_json_response()
    except ExtendedHTTPException:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=_INVALID_MCP_AUTH_CONFIG_MESSAGE,
            details=_CLIENT_METADATA_CONFIG_ERROR_DETAILS,
            help=_CLIENT_METADATA_CONFIG_ERROR_HELP,
        ) from None


def _get_mcp_config_or_raise(config_id: str) -> MCPConfig:
    mcp_config = MCPConfig.find_by_id(config_id)
    if mcp_config is None:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="MCP configuration not found",
            details=f"No MCP configuration found with id '{config_id}'.",
            help="Check the mcp_config_id and retry.",
        )
    return mcp_config


def _check_mcp_config_access(user: User, mcp_config: MCPConfig) -> None:
    if user.is_admin_or_maintainer or mcp_config.user_id == user.id or mcp_config.is_public:
        return
    raise ExtendedHTTPException(
        code=status.HTTP_403_FORBIDDEN,
        message="Access denied",
        details="You do not have permission to initiate authentication for this MCP configuration.",
        help="Ask the config owner or an administrator for access.",
    )


def _get_raw_oauth_config_or_raise(mcp_config: MCPConfig) -> tuple[dict[str, Any], str]:
    raw_auth_config, auth_config_id, auth_type = _get_raw_supported_auth_config_or_raise(mcp_config)
    if auth_type != "oauth2":
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=_INVALID_MCP_AUTH_CONFIG_MESSAGE,
            details="Stored auth_config is not an OAuth2 configuration.",
            help="Use an OAuth2 auth_config for this endpoint.",
        )
    return raw_auth_config, auth_config_id


def _get_raw_supported_auth_config_or_raise(mcp_config: MCPConfig) -> tuple[dict[str, Any], str, str]:
    if mcp_config.config is None:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=_INVALID_MCP_AUTH_CONFIG_MESSAGE,
            details="MCP configuration does not include a server config block.",
            help="Update the MCP configuration before checking auth status.",
        )

    raw_auth_config = mcp_config.config.auth_config
    if not isinstance(raw_auth_config, dict):
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=_INVALID_MCP_AUTH_CONFIG_MESSAGE,
            details="MCP configuration does not include a persisted auth_config.",
            help="Update the MCP configuration before checking auth status.",
        )

    auth_config_id = raw_auth_config.get("id")
    if not isinstance(auth_config_id, str) or not auth_config_id.strip():
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=_INVALID_MCP_AUTH_CONFIG_MESSAGE,
            details="Persisted auth_config.id is required for auth status checks.",
            help="Re-save the MCP auth configuration to generate a stable auth_config.id.",
        )

    auth_type = raw_auth_config.get("auth_type")
    if auth_type not in SUPPORTED_AUTH_TYPES:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=_INVALID_MCP_AUTH_CONFIG_MESSAGE,
            details="Stored auth_config uses an unsupported auth_type.",
            help="Use an OAuth2 or SAML auth_config for this endpoint.",
        )

    return raw_auth_config, auth_config_id, auth_type


def _get_raw_saml_config_or_raise(mcp_config: MCPConfig) -> tuple[dict[str, Any], str]:
    raw_auth_config, auth_config_id, auth_type = _get_raw_supported_auth_config_or_raise(mcp_config)
    if auth_type != "saml":
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=_INVALID_MCP_AUTH_CONFIG_MESSAGE,
            details="Stored auth_config is not a SAML configuration.",
            help="Use a SAML auth_config for this endpoint.",
        )
    return raw_auth_config, auth_config_id


def _evaluate_auth_status(
    *,
    tms: Any,
    user_id: str,
    auth_config_id: str,
    raw_auth_config: dict[str, Any],
) -> tuple[str, str | None, str | None]:
    from codemie_enterprise.mcp_auth import evaluate_auth_status

    return evaluate_auth_status(
        tms=tms,
        user_id=user_id,
        auth_config_id=auth_config_id,
        raw_auth_config=raw_auth_config,
    )


@enabled_router.post(
    "/oauth2/initiate",
    status_code=status.HTTP_200_OK,
    response_model=OAuth2InitiateResponse,
)
def initiate_oauth2_enabled(
    payload: OAuth2InitiateRequest,
    discovered_flow_id: str | None = Query(default=None),
    recovery_flow_id: str | None = Query(default=None),
    user: User = Depends(authenticate),
) -> OAuth2InitiateResponse:
    mcp_config = _get_mcp_config_or_raise(payload.mcp_config_id)
    _check_mcp_config_access(user, mcp_config)
    resolved_recovery_flow_id = _resolve_recovery_flow_id(payload.recovery_flow_id, recovery_flow_id)
    if resolved_recovery_flow_id and (payload.discovered_flow_id or discovered_flow_id):
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=_INVALID_MCP_AUTH_CONFIG_MESSAGE,
            details="recovery_flow_id cannot be combined with discovered_flow_id.",
            help="Retry with only recovery_flow_id for insufficient-scope recovery.",
        )
    if resolved_recovery_flow_id:
        response_data = build_recovery_oauth2_initiate_response(
            mcp_config=mcp_config,
            user=user,
            recovery_flow_id=resolved_recovery_flow_id,
        )
        return OAuth2InitiateResponse.model_validate(response_data.model_dump())
    resolved_discovered_flow_id = payload.discovered_flow_id or discovered_flow_id
    if resolved_discovered_flow_id:
        response_data = build_discovered_oauth2_initiate_response(
            mcp_config=mcp_config,
            user=user,
            discovered_flow_id=resolved_discovered_flow_id,
        )
        return OAuth2InitiateResponse.model_validate(response_data.model_dump())
    raw_mcp_auth_config = getattr(getattr(mcp_config, "config", None), "auth_config", None)
    if raw_mcp_auth_config is None:
        response_data = build_discovered_oauth2_initiate_response(
            mcp_config=mcp_config,
            user=user,
            discovered_flow_id=None,
        )
        return OAuth2InitiateResponse.model_validate(response_data.model_dump())
    raw_auth_config, auth_config_id = _get_raw_oauth_config_or_raise(mcp_config)
    response_data = build_oauth2_initiate_response(
        raw_auth_config=raw_auth_config,
        user=user,
        auth_config_id=auth_config_id,
        mcp_server_url=mcp_config.config.url,
    )
    return OAuth2InitiateResponse.model_validate(response_data.model_dump())


def _resolve_recovery_flow_id(body_value: str | None, query_value: str | None) -> str | None:
    if body_value and query_value and body_value != query_value:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=_INVALID_MCP_AUTH_CONFIG_MESSAGE,
            details="Body and query recovery_flow_id values must match.",
            help="Retry with a single recovery_flow_id value.",
        )
    return body_value or query_value


@router.get(
    "/oauth2/callback",
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    response_model=MCPAuthDisabledResponse,
)
def oauth2_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
) -> MCPAuthDisabledResponse:
    return _DISABLED_RESPONSE


@enabled_router.get(
    "/oauth2/callback-page.js",
    response_class=Response,
)
def oauth2_callback_page_script_enabled() -> Response:
    return build_oauth2_callback_page_script_response()


@enabled_router.get(
    "/oauth2/callback",
    response_class=HTMLResponse,
)
def oauth2_callback_enabled(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    error_uri: str | None = Query(default=None),
) -> HTMLResponse:
    return build_oauth2_callback_response(
        code=code,
        state=state,
        error=error,
        error_description=error_description,
        error_uri=error_uri,
    )


@router.post(
    "/saml/initiate",
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    response_model=MCPAuthDisabledResponse,
)
def initiate_saml() -> MCPAuthDisabledResponse:
    return _DISABLED_RESPONSE


@enabled_router.post(
    "/saml/initiate",
    status_code=status.HTTP_200_OK,
    response_model=SAMLInitiateResponse,
)
def initiate_saml_enabled(
    payload: SAMLInitiateRequest,
    user: User = Depends(authenticate),
) -> SAMLInitiateResponse:
    mcp_config = _get_mcp_config_or_raise(payload.mcp_config_id)
    _check_mcp_config_access(user, mcp_config)
    raw_auth_config, auth_config_id = _get_raw_saml_config_or_raise(mcp_config)
    response_data = build_saml_initiate_response(
        raw_auth_config=raw_auth_config,
        user=user,
        auth_config_id=auth_config_id,
    )
    return SAMLInitiateResponse.model_validate(response_data.model_dump())


@router.post(
    "/saml/acs",
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    response_model=MCPAuthDisabledResponse,
)
def saml_acs() -> MCPAuthDisabledResponse:
    return _DISABLED_RESPONSE


@enabled_router.post(
    "/saml/acs",
    response_class=HTMLResponse,
)
def saml_acs_enabled(
    saml_response: str | None = Form(default=None, alias="SAMLResponse"),
    relay_state: str | None = Form(default=None, alias="RelayState"),
) -> HTMLResponse:
    return build_saml_callback_response(
        saml_response=saml_response,
        relay_state=relay_state,
    )


@router.get(
    "/saml/metadata",
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    response_model=MCPAuthDisabledResponse,
)
def saml_metadata(auth_config_id: str | None = Query(default=None)) -> MCPAuthDisabledResponse:
    return _DISABLED_RESPONSE


@enabled_router.get(
    "/saml/metadata",
    response_class=Response,
)
def saml_metadata_enabled(auth_config_id: str | None = Query(default=None)) -> Response:
    return build_saml_metadata_response(auth_config_id=auth_config_id)


@router.get(
    "/status",
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    response_model=MCPAuthDisabledResponse,
)
def mcp_auth_status(mcp_config_id: str | None = Query(default=None)) -> MCPAuthDisabledResponse:
    return _DISABLED_RESPONSE


@enabled_router.get(
    "/status",
    status_code=status.HTTP_200_OK,
    response_model=MCPAuthStatusResponse,
)
def mcp_auth_status_enabled(
    mcp_config_id: str = Query(min_length=1),
    user: User = Depends(authenticate),
) -> MCPAuthStatusResponse:
    mcp_config = _get_mcp_config_or_raise(mcp_config_id)
    _check_mcp_config_access(user, mcp_config)
    raw_mcp_auth_config = getattr(getattr(mcp_config, "config", None), "auth_config", None)
    if not isinstance(raw_mcp_auth_config, dict):
        discovered_status = build_discovered_auth_status_response(mcp_config=mcp_config, user=user)
        return MCPAuthStatusResponse.model_validate(discovered_status)
    raw_auth_config, auth_config_id, auth_type = _get_raw_supported_auth_config_or_raise(mcp_config)
    tms = _require_initialized_tms()
    status_value, resolved_auth_type, error_context = _evaluate_auth_status(
        tms=tms,
        user_id=user.id,
        auth_config_id=auth_config_id,
        raw_auth_config=raw_auth_config,
    )
    mcp_config_name = mcp_config.name
    return MCPAuthStatusResponse(
        mcp_config_id=mcp_config.id,
        mcp_config_name=mcp_config_name,
        mcp_server_name=mcp_config_name,
        auth_config_id=auth_config_id,
        auth_type=cast(Literal["oauth2", "saml"], resolved_auth_type or auth_type),
        as_hostname=derive_as_hostname(auth_type, raw_auth_config),
        status=cast(
            Literal["authenticated", "authentication_required", "session_expired", "config_error"],
            status_value,
        ),
        error_context=error_context,
        initiate_url=cast(str, derive_initiate_url(auth_type)),
    )


def get_mcp_auth_router() -> APIRouter:
    if not is_mcp_auth_enabled():
        return router
    return enabled_router


def get_cimd_router() -> APIRouter:
    if not is_mcp_auth_enabled():
        return cimd_router
    return enabled_cimd_router
