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

import hashlib
import ipaddress
import re
import sys
from importlib import import_module
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit

from fastapi import status
from fastapi.responses import JSONResponse, Response

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.utils import get_api_root_path
from codemie.rest_api.security.user import User

from ._common import MCPAuthEnterpriseUnavailableError, _raise_client_error
from ._constants import (
    _CLIENT_METADATA_CACHE_CONTROL,
    _CLIENT_METADATA_DOCUMENT_PATH,
    _INVALID_MCP_AUTH_CONFIG_MESSAGE,
    _INVALID_MCP_SERVER_URL_MESSAGE,
    _INVALID_OAUTH2_CONFIG_MESSAGE,
    _LOCALHOST_HOSTS,
    _OAUTH2_CALLBACK_PATH,
    _SAML_ACS_PATH,
)


def _uses_https(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False

    return urlsplit(value).scheme.lower() == "https"


def _is_localhost_hostname(hostname: str | None) -> bool:
    return hostname in _LOCALHOST_HOSTS


def _build_callback_uri(path: str, *, invalid_message: str, label: str) -> tuple[str, str, bool]:
    callback_uri = f"{config.CALLBACK_API_BASE_URL}{get_api_root_path()}{path}"
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


def _describe_stored_redirect_uri(redirect_uri: Any) -> tuple[str, str, bool]:
    if not isinstance(redirect_uri, str) or not redirect_uri.strip():
        _raise_client_error(
            _INVALID_OAUTH2_CONFIG_MESSAGE,
            "Discovered OAuth2 flow snapshot does not include a valid redirect_uri.",
        )
    parsed_redirect_uri = urlsplit(redirect_uri)
    hostname = parsed_redirect_uri.hostname
    if not hostname:
        _raise_client_error(
            _INVALID_OAUTH2_CONFIG_MESSAGE,
            f"Discovered OAuth2 flow snapshot redirect_uri is invalid: {redirect_uri}",
        )
    localhost_warning = _is_localhost_hostname(hostname)
    if parsed_redirect_uri.scheme.lower() != "https" and not localhost_warning:
        _raise_client_error(
            _INVALID_OAUTH2_CONFIG_MESSAGE,
            f"Discovered OAuth2 flow snapshot redirect_uri must use HTTPS unless localhost: {redirect_uri}",
        )
    return redirect_uri, parsed_redirect_uri.netloc, localhost_warning


def build_saml_acs_url() -> str:
    acs_url, _, _ = _build_callback_uri(
        _SAML_ACS_PATH,
        invalid_message=_INVALID_MCP_AUTH_CONFIG_MESSAGE,
        label="ACS URL",
    )
    return acs_url


def ensure_client_metadata_document_available() -> None:
    try:
        import_module("codemie_enterprise.mcp_auth.discovery.client_metadata")
    except ImportError as exc:
        raise MCPAuthEnterpriseUnavailableError from exc


def build_client_metadata_document_response() -> Response:
    """Build the public Client ID Metadata Document response."""

    redirect_uri, _, _ = build_redirect_uri()
    client_metadata_document_url = f"{config.CALLBACK_API_BASE_URL.rstrip('/')}{_CLIENT_METADATA_DOCUMENT_PATH}"
    ensure_client_metadata_document_available()
    try:
        from codemie_enterprise.mcp_auth.discovery.client_metadata import build_client_metadata_document
    except ImportError as exc:
        raise MCPAuthEnterpriseUnavailableError from exc

    try:
        document = build_client_metadata_document(
            client_metadata_document_url=client_metadata_document_url,
            redirect_uris=(redirect_uri,),
        )
    except ValueError as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=_INVALID_MCP_AUTH_CONFIG_MESSAGE,
            details="Client metadata document configuration is invalid.",
            help="Review CALLBACK_API_BASE_URL and OAuth2 callback configuration.",
        ) from exc

    return JSONResponse(
        content=document.model_dump(mode="json"),
        media_type="application/json",
        headers={"Cache-Control": _CLIENT_METADATA_CACHE_CONTROL},
    )


def _normalize_resource_path(path: str) -> str:
    stripped_path = path or ""
    if stripped_path in {"", "/"}:
        return ""
    return stripped_path


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


def _resolve_derive_resource_uri_without_enterprise():
    deps = sys.modules.get("codemie.enterprise.mcp_auth.dependencies")
    if deps is not None:
        return deps._derive_resource_uri_without_enterprise
    return _derive_resource_uri_without_enterprise


def derive_resource_uri(server_url: str | None) -> str:
    if not server_url:
        _raise_client_error(
            _INVALID_MCP_SERVER_URL_MESSAGE,
            "MCP server URL is required to derive the OAuth2 resource.",
        )

    try:
        from codemie_enterprise.mcp_auth.discovery import derive_canonical_mcp_resource_uri
    except ImportError:
        return _resolve_derive_resource_uri_without_enterprise()(server_url)

    try:
        return derive_canonical_mcp_resource_uri(server_url)
    except (ValueError, TypeError):
        _raise_client_error(
            _INVALID_MCP_SERVER_URL_MESSAGE,
            "MCP server URL must be a valid HTTPS DNS URL for OAuth2 resource derivation.",
        )


def _derive_resource_uri_without_enterprise(server_url: str) -> str:
    try:
        parsed_url = urlsplit(server_url)
        hostname = _normalize_resource_hostname(parsed_url.hostname)
        if parsed_url.scheme.lower() != "https":
            raise ValueError("scheme")
        if parsed_url.username is not None or parsed_url.password is not None:
            raise ValueError("userinfo")
        if not hostname:
            raise ValueError("hostname")
        port = parsed_url.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("port")
    except ValueError:
        _raise_client_error(
            _INVALID_MCP_SERVER_URL_MESSAGE,
            "MCP server URL must be a valid HTTPS DNS URL for OAuth2 resource derivation.",
        )

    normalized_url = parsed_url._replace(
        scheme=parsed_url.scheme.lower(),
        netloc=_format_resource_netloc(hostname, parsed_url),
        path=_normalize_resource_path(parsed_url.path),
        query="",
        fragment="",
    )
    return urlunsplit(normalized_url)


def _normalize_resource_hostname(hostname: str | None) -> str:
    if not hostname:
        raise ValueError("hostname")
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("hostname") from exc
    if ascii_hostname.endswith("."):
        ascii_hostname = ascii_hostname[:-1]
    try:
        ipaddress.ip_address(ascii_hostname)
    except ValueError:
        pass
    else:
        raise ValueError("ip_literal")
    if not ascii_hostname or len(ascii_hostname) > 253 or not re.fullmatch(r"[a-z0-9.-]+", ascii_hostname):
        raise ValueError("hostname")
    labels = ascii_hostname.split(".")
    if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in labels):
        raise ValueError("hostname")
    return ascii_hostname


def _format_resource_netloc(hostname: str, parsed_url: SplitResult) -> str:
    port = parsed_url.port
    if port is None or port == 443:
        return hostname
    return f"{hostname}:{port}"


def _get_authenticated_bearer_token_hash(user: User) -> str:
    if not user.auth_token:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=_INVALID_MCP_AUTH_CONFIG_MESSAGE,
            details="Authenticated MCP auth initiation requires a bearer token for session binding.",
            help="Re-authenticate and retry the MCP auth initiation flow.",
        )
    return hashlib.sha256(user.auth_token.encode("utf-8")).hexdigest()
