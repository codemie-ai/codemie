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

import sys
from contextlib import AbstractContextManager, nullcontext
from typing import TYPE_CHECKING, Any

from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException

if TYPE_CHECKING:
    from codemie_enterprise.mcp_auth import (
        RedisEncryption,
        RedisPKCEStore,
        SAMLRelayStateStore,
    )

_MCP_AUTH_TEMPORARILY_UNAVAILABLE = "MCP auth temporarily unavailable"
_MCP_AUTH_SERVICE_UNAVAILABLE_MESSAGE = "MCP auth service is not initialized"
_MCP_AUTH_RETRY_AFTER_INIT_HELP = "Try again after the MCP auth service finishes initializing."

_DEPS_MODULE = "codemie.enterprise.mcp_auth.dependencies"


def _deps() -> Any:
    return sys.modules[_DEPS_MODULE]


def _require_initialized_mcp_auth_components() -> tuple[RedisPKCEStore, RedisEncryption]:
    deps = _deps()
    if deps._pkce_store is None or deps._redis_encryption is None:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details=_MCP_AUTH_SERVICE_UNAVAILABLE_MESSAGE,
            help=_MCP_AUTH_RETRY_AFTER_INIT_HELP,
        )
    return deps._pkce_store, deps._redis_encryption


def _require_initialized_saml_initiate_dependencies() -> tuple[SAMLRelayStateStore, RedisEncryption]:
    deps = _deps()
    if deps._saml_relay_state_store is None or deps._redis_encryption is None:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details=_MCP_AUTH_SERVICE_UNAVAILABLE_MESSAGE,
            help=_MCP_AUTH_RETRY_AFTER_INIT_HELP,
        )
    return deps._saml_relay_state_store, deps._redis_encryption


def _require_initialized_tms() -> Any:
    deps = _deps()
    if deps._tms is None:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details=_MCP_AUTH_SERVICE_UNAVAILABLE_MESSAGE,
            help=_MCP_AUTH_RETRY_AFTER_INIT_HELP,
        )
    return deps._tms


def _require_initialized_discovered_flow_store() -> Any:
    deps = _deps()
    if deps._mcp_auth_discovered_flow_store is None:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=_MCP_AUTH_TEMPORARILY_UNAVAILABLE,
            details=_MCP_AUTH_SERVICE_UNAVAILABLE_MESSAGE,
            help=_MCP_AUTH_RETRY_AFTER_INIT_HELP,
        )
    return deps._mcp_auth_discovered_flow_store


def _require_initialized_saml_callback_dependencies() -> tuple[SAMLRelayStateStore, RedisEncryption, Any]:
    deps = _deps()
    if deps._saml_relay_state_store is None or deps._redis_encryption is None or deps._tms is None:
        from ._common import CallbackPageError

        raise CallbackPageError(
            "Authentication session could not be verified. "
            "Return to CodeMie and try again when the service is available.",
            bridge_error_code="runtime_error",
        )
    return deps._saml_relay_state_store, deps._redis_encryption, deps._tms


def _require_initialized_callback_dependencies() -> tuple[RedisPKCEStore, RedisEncryption, Any]:
    pkce_store, redis_encryption = _require_initialized_mcp_auth_components()
    return pkce_store, redis_encryption, _require_initialized_tms()


def _tms_audit_context(source: str, correlation_id: str | None = None) -> AbstractContextManager[None]:
    deps = _deps()
    if deps._tms_audit_context_provider is None:
        return nullcontext()
    return deps._tms_audit_context_provider.context(source=source, correlation_id=correlation_id)


def is_mcp_auth_enabled() -> bool:
    from codemie.configs import config

    deps = _deps()
    if not deps.HAS_MCP_AUTH:
        if config.MCP_AUTH_ENABLED and config.MCP_AUTH_TMS_ENABLED:
            raise RuntimeError("enterprise MCP auth package is unavailable while MCP auth TMS is enabled")
        return False

    return config.MCP_AUTH_ENABLED


def get_mcp_auth_trust_policy_service() -> Any | None:
    return _deps()._mcp_auth_trust_policy_service


def invalidate_mcp_auth_trust_policy_cache() -> None:
    deps = _deps()
    if deps._mcp_auth_trust_policy_service is None:
        return

    clear_cache = getattr(deps._mcp_auth_trust_policy_service, "clear_cache", None)
    if clear_cache is None:
        return
    clear_cache()
