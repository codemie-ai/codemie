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

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NoReturn, Protocol
from urllib.parse import urlencode, urlsplit

from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException, MCPAuthenticationRequiredException


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


class MCPAuthEnterpriseUnavailableError(Exception):
    """Raised when the optional enterprise MCP auth package is unavailable."""


@dataclass(frozen=True)
class MCPPostAuth401Result:
    retry_auth_headers: dict[str, str] | None = None
    auth_exception: MCPAuthenticationRequiredException | None = None


class _CleanupEnqueuer(Protocol):
    def enqueue_cleanup(self, user_id: str) -> None: ...


def _is_missing_required_value(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _raise_client_error(message: str, details: str, *, code: int = status.HTTP_400_BAD_REQUEST) -> NoReturn:
    raise ExtendedHTTPException(code=code, message=message, details=details, help="Review the MCP auth configuration.")


def _candidate_string(candidate: Mapping[str, Any], field_name: str) -> str | None:
    value = candidate.get(field_name)
    return value if isinstance(value, str) and value.strip() else None


def _get_discovery_result_field(result: Any, field_name: str) -> Any:
    if isinstance(result, Mapping):
        return result.get(field_name)
    return getattr(result, field_name, None)


def _get_discovery_candidate_field(candidate: Mapping[str, Any] | Any, field_name: str) -> Any:
    if isinstance(candidate, Mapping):
        return candidate.get(field_name)
    return getattr(candidate, field_name, None)


def _as_hostname_from_error_context(error_context: Mapping[str, Any]) -> str | None:
    value = error_context.get("issuer") or error_context.get("selected_authorization_server")
    return urlsplit(value).hostname if isinstance(value, str) else None


def _execution_context_attr(execution_context: Any | None, attr_name: str) -> Any:
    return getattr(execution_context, attr_name, None) if execution_context is not None else None


def _is_discovered_auth_config_id(auth_config_id: str | None) -> bool:
    return isinstance(auth_config_id, str) and auth_config_id.startswith("discovered:")


def _build_discovered_initiate_url(discovered_flow_id: str) -> str:
    return f"/v1/mcp-auth/oauth2/initiate?{urlencode({'discovered_flow_id': discovered_flow_id})}"


def _build_recovery_initiate_url(recovery_flow_id: str) -> str:
    return f"/v1/mcp-auth/oauth2/initiate?{urlencode({'recovery_flow_id': recovery_flow_id})}"


def _build_discovered_config_error_payload(
    candidate: Mapping[str, Any],
    error_context: Mapping[str, Any],
    *,
    as_hostname: str | None = None,
) -> dict[str, Any]:
    mcp_config_name = _candidate_string(candidate, "mcp_config_name") or _candidate_string(candidate, "server_name")
    return {
        "mcp_config_id": _candidate_string(candidate, "mcp_config_id"),
        "mcp_config_name": mcp_config_name,
        "mcp_server_name": _candidate_string(candidate, "mcp_server_name") or mcp_config_name,
        "auth_type": "oauth2",
        "as_hostname": as_hostname or _as_hostname_from_error_context(error_context),
        "status": "config_error",
        "error_context": dict(error_context),
    }
