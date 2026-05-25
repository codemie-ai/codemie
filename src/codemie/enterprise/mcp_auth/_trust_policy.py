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

import ipaddress
import json
from typing import Any

from codemie.rest_api.models.dynamic_config import ConfigValueType
from codemie.service.dynamic_config_service import DynamicConfigService

from ._constants import (
    MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST,
    MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST_MAX_LENGTH,
    MCP_AUTH_TRUSTED_AS_DOMAINS_KEY,
)


def _build_trust_policy_configuration_error(message: str) -> ValueError:
    try:
        from codemie_enterprise.mcp_auth import TrustPolicyConfigurationError

        return TrustPolicyConfigurationError(message)  # type: ignore[return-value]
    except ImportError:
        return ValueError(message)


async def read_mcp_auth_trusted_as_domains_config() -> str | None:
    dynamic_config = await DynamicConfigService.aget_by_key(MCP_AUTH_TRUSTED_AS_DOMAINS_KEY)
    return _coerce_trusted_as_domains_config_value(dynamic_config)


def read_mcp_auth_trusted_as_domains_config_sync() -> str | None:
    """Synchronous reader used on the caller's side before crossing into a worker-thread loop.

    Why: the async reader awaits the SQLAlchemy async engine bound to the main loop;
    if it is awaited from a fresh asyncio.run loop in a worker thread the engine emits
    "Future attached to a different loop". Reading via the sync engine sidesteps that.
    """
    dynamic_config = DynamicConfigService.get_by_key(MCP_AUTH_TRUSTED_AS_DOMAINS_KEY)
    return _coerce_trusted_as_domains_config_value(dynamic_config)


async def read_mcp_auth_discovery_private_network_allowlist_config() -> tuple[str, ...]:
    dynamic_config = await DynamicConfigService.aget_by_key(MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST)
    return _coerce_private_network_allowlist_config_value(dynamic_config)


def read_mcp_auth_discovery_private_network_allowlist_config_sync() -> tuple[str, ...]:
    """Synchronous counterpart to read_mcp_auth_discovery_private_network_allowlist_config.

    See ``read_mcp_auth_trusted_as_domains_config_sync`` for the loop-affinity rationale.
    """
    dynamic_config = DynamicConfigService.get_by_key(MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST)
    return _coerce_private_network_allowlist_config_value(dynamic_config)


def _coerce_trusted_as_domains_config_value(dynamic_config: Any) -> str | None:
    if dynamic_config is None:
        return None
    if dynamic_config.value_type != ConfigValueType.STRING:
        raise _build_trust_policy_configuration_error(
            f"{MCP_AUTH_TRUSTED_AS_DOMAINS_KEY} must be stored with value_type=string."
        )
    return dynamic_config.value


def _coerce_private_network_allowlist_config_value(dynamic_config: Any) -> tuple[str, ...]:
    if dynamic_config is None:
        return ()
    if dynamic_config.value_type != ConfigValueType.STRING:
        raise _build_trust_policy_configuration_error(
            f"{MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST} must be stored with value_type=string."
        )
    raw_value = dynamic_config.value
    if len(raw_value) > MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST_MAX_LENGTH:
        raise _build_trust_policy_configuration_error(
            f"{MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST} exceeds maximum length."
        )
    return _parse_private_network_allowlist(raw_value)


def _parse_private_network_allowlist(raw_value: str) -> tuple[str, ...]:
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise _build_trust_policy_configuration_error(
            f"{MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST} must contain valid JSON."
        ) from exc
    if not isinstance(payload, list):
        raise _build_trust_policy_configuration_error(
            f"{MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST} must contain a JSON array."
        )

    entries: list[str] = []
    for entry in payload:
        if not isinstance(entry, str) or not entry.strip():
            raise _build_trust_policy_configuration_error(
                f"{MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST} entries must be non-blank strings."
            )
        normalized_entry = entry.strip()
        try:
            ipaddress.ip_network(normalized_entry, strict=False)
        except ValueError as exc:
            raise _build_trust_policy_configuration_error(
                f"{MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST} entries must be CIDR/IP network values."
            ) from exc
        entries.append(normalized_entry)
    return tuple(entries)


def _normalize_discovery_concurrency_limit(value: int) -> int:
    return value if value > 0 else 5


def build_static_trust_policy_service(raw_trusted_as_domains: str | None) -> Any:
    """Return an AuthorizationServerTrustPolicyService whose config_reader returns a prefetched value.

    Why: the singleton AuthorizationServerTrustPolicyService awaits its config_reader
    on cache miss, which reaches the main-loop-bound async DB engine. When the
    discovery probe runs in a fresh asyncio.run loop in a worker thread, that await
    raises "Future attached to a different loop". Callers fetch the raw allowlist
    synchronously before crossing the bridge and pass the value here; the probe then
    only awaits a no-op coroutine and performs pure-CPU hostname matching.
    """
    from codemie_enterprise.mcp_auth import AuthorizationServerTrustPolicyService

    async def _prefetched_reader() -> str | None:
        return raw_trusted_as_domains

    return AuthorizationServerTrustPolicyService(config_reader=_prefetched_reader)
