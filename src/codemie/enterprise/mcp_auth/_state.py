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
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any

from codemie.service.encryption.base_encryption_service import BaseEncryptionService
from codemie.service.encryption.encryption_factory import EncryptionFactory

if TYPE_CHECKING:
    from codemie_enterprise.mcp_auth import (
        RedisEncryption,
        RedisPKCEStore,
        SAMLRelayStateStore,
    )


def _has_mcp_auth_package() -> bool:
    try:
        version("codemie-enterprise")
    except PackageNotFoundError:
        return False
    return True


HAS_MCP_AUTH: bool = _has_mcp_auth_package()

_initialized: bool = False
_bridge_queue: asyncio.Queue[str] | None = None
_bridge_task: asyncio.Task[None] | None = None
_bridge_loop: asyncio.AbstractEventLoop | None = None
_mcp_auth_service: Any = None
_mcp_auth_trust_policy_service: Any = None
_mcp_auth_discovery_cache: Any = None
_mcp_auth_dcr_credentials_cache: Any = None
_mcp_auth_discovered_flow_store: Any = None
_tms: Any = None
_redis_client: Any = None
_registered_resolver_types: set[type] = set()
_pkce_store: RedisPKCEStore | None = None
_saml_relay_state_store: SAMLRelayStateStore | None = None
_redis_encryption: RedisEncryption | None = None
_tms_audit_context_provider: Any = None

encryption_service: BaseEncryptionService = EncryptionFactory().get_current_encryption_service()
