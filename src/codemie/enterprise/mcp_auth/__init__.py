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

from .dependencies import (
    MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST,
    MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST_MAX_LENGTH,
    build_static_trust_policy_service,
    get_mcp_auth_trust_policy_service,
    invalidate_mcp_auth_trust_policy_cache,
    is_mcp_auth_enabled,
    read_mcp_auth_discovery_private_network_allowlist_config,
    read_mcp_auth_discovery_private_network_allowlist_config_sync,
    read_mcp_auth_trusted_as_domains_config,
    read_mcp_auth_trusted_as_domains_config_sync,
)

__all__ = [
    "MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST",
    "MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST_MAX_LENGTH",
    "build_static_trust_policy_service",
    "get_mcp_auth_trust_policy_service",
    "invalidate_mcp_auth_trust_policy_cache",
    "is_mcp_auth_enabled",
    "read_mcp_auth_discovery_private_network_allowlist_config",
    "read_mcp_auth_discovery_private_network_allowlist_config_sync",
    "read_mcp_auth_trusted_as_domains_config",
    "read_mcp_auth_trusted_as_domains_config_sync",
]
