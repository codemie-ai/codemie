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
from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.rest_api.models.dynamic_config import ConfigValueType, DynamicConfig


@pytest.mark.asyncio
async def test_trust_policy_reader_returns_raw_string_for_valid_dynamic_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies

    config = DynamicConfig(
        id="config-1",
        key=dependencies.MCP_AUTH_TRUSTED_AS_DOMAINS_KEY,
        value='["login.microsoftonline.com"]',
        value_type=ConfigValueType.STRING,
        updated_by="admin",
    )
    mock_get = AsyncMock(return_value=config)
    monkeypatch.setattr(dependencies.DynamicConfigService, "aget_by_key", mock_get)

    assert await dependencies.read_mcp_auth_trusted_as_domains_config() == '["login.microsoftonline.com"]'
    mock_get.assert_awaited_once_with(dependencies.MCP_AUTH_TRUSTED_AS_DOMAINS_KEY)


@pytest.mark.asyncio
async def test_trust_policy_reader_returns_none_when_dynamic_config_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies

    monkeypatch.setattr(dependencies.DynamicConfigService, "aget_by_key", AsyncMock(return_value=None))

    assert await dependencies.read_mcp_auth_trusted_as_domains_config() is None


@pytest.mark.asyncio
async def test_trust_policy_reader_wrong_value_type_fails_closed_without_raw_value_leak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from codemie.enterprise.mcp_auth import dependencies

    config = DynamicConfig(
        id="config-1",
        key=dependencies.MCP_AUTH_TRUSTED_AS_DOMAINS_KEY,
        value='["secret.example.com"]',
        value_type=ConfigValueType.BOOL,
        updated_by="admin",
    )
    monkeypatch.setattr(dependencies.DynamicConfigService, "aget_by_key", AsyncMock(return_value=config))

    with pytest.raises(Exception) as exc_info:
        await dependencies.read_mcp_auth_trusted_as_domains_config()

    assert "MCP_AUTH_TRUSTED_AS_DOMAINS" in str(exc_info.value)
    assert "secret.example.com" not in str(exc_info.value)


def test_get_mcp_auth_trust_policy_service_returns_initialized_service() -> None:
    from codemie.enterprise.mcp_auth import dependencies

    service = object()
    original = dependencies._mcp_auth_trust_policy_service
    dependencies._mcp_auth_trust_policy_service = service
    try:
        assert dependencies.get_mcp_auth_trust_policy_service() is service
    finally:
        dependencies._mcp_auth_trust_policy_service = original


def test_invalidate_mcp_auth_trust_policy_cache_noops_when_unavailable() -> None:
    from codemie.enterprise.mcp_auth import dependencies

    original = dependencies._mcp_auth_trust_policy_service
    dependencies._mcp_auth_trust_policy_service = None
    try:
        dependencies.invalidate_mcp_auth_trust_policy_cache()
    finally:
        dependencies._mcp_auth_trust_policy_service = original


def test_invalidate_mcp_auth_trust_policy_cache_calls_clear_cache() -> None:
    from codemie.enterprise.mcp_auth import dependencies

    service = MagicMock()
    original = dependencies._mcp_auth_trust_policy_service
    dependencies._mcp_auth_trust_policy_service = service
    try:
        dependencies.invalidate_mcp_auth_trust_policy_cache()
    finally:
        dependencies._mcp_auth_trust_policy_service = original

    service.clear_cache.assert_called_once_with()


def test_initialize_mcp_auth_instantiates_trust_policy_service(monkeypatch: pytest.MonkeyPatch) -> None:
    from codemie.configs import config as runtime_config
    from codemie.enterprise.mcp_auth import dependencies
    from codemie.service.security.oidc_token_exchange_service import OIDCTokenExchangeService
    from codemie.service.security.token_exchange_service import TokenExchangeService
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    class FakeResolver:
        def __init__(self, token_management_system, authentication_required_factory, audit_context_provider=None):
            self.token_management_system = token_management_system
            self.authentication_required_factory = authentication_required_factory
            self.audit_context_provider = audit_context_provider

    fake_tms = object()
    fake_service = MagicMock()
    fake_trust_policy_service = MagicMock()
    fake_audit_context_provider = object()
    fake_redis_client = MagicMock(close=MagicMock())
    fake_redis_encryption = MagicMock()

    monkeypatch.setattr(dependencies, "HAS_MCP_AUTH", True)
    monkeypatch.setattr(runtime_config, "MCP_AUTH_ENABLED", True)
    for name, value in {
        "MCP_AUTH_TMS_ENABLED": True,
        "ENV": "local",
        "MCP_AUTH_TMS_KMS_KEY_ID": "test-key",
        "MCP_AUTH_TMS_ENCRYPTION_CONTEXT_PREFIX": "codemie-enterprise:mcp-auth:tms",
        "MCP_AUTH_TMS_REFRESH_TIMEOUT_SECONDS": 2.5,
        "MCP_AUTH_TMS_REDIS_LOCK_ENABLED": True,
        "MCP_AUTH_TMS_REDIS_LOCK_TTL_SECONDS": 10,
        "MCP_AUTH_TMS_AUDIT_REQUIRED": True,
        "MCP_AUTH_TMS_AUDIT_FALLBACK_ENABLED": False,
        "MCP_AUTH_TMS_AUDIT_FALLBACK_SINK_CONFIGURED": False,
        "MCP_AUTH_TMS_ALLOW_MOCK": False,
        "MCP_AUTH_HMAC_SECRET": "x" * 32,
        "ENCRYPTION_TYPE": "plain",
    }.items():
        monkeypatch.setattr(dependencies.config, name, value)

    def create_task(coroutine):
        coroutine.close()
        return MagicMock(done=lambda: False)

    monkeypatch.setattr(dependencies.asyncio, "get_running_loop", lambda: MagicMock(create_task=create_task))
    monkeypatch.setattr(dependencies, "create_redis_client", lambda: fake_redis_client)
    monkeypatch.setattr(MCPToolkitService, "register_auth_resolver", MagicMock())
    monkeypatch.setattr(dependencies, "_build_token_management_system", MagicMock(return_value=fake_tms))

    fake_enterprise_module = MagicMock(
        AuthorizationServerTrustPolicyService=MagicMock(return_value=fake_trust_policy_service),
        ContextVarTMSAuditContextProvider=MagicMock(return_value=fake_audit_context_provider),
        DCRCredentialsCache=MagicMock(),
        DiscoveryMetadataCache=MagicMock(),
        MCPAuthResolver=FakeResolver,
        MCPAuthService=MagicMock(return_value=fake_service),
        MCPAuthServiceConfig=MagicMock(return_value=MagicMock()),
        RedisEncryption=MagicMock(return_value=fake_redis_encryption),
        RedisPKCEStore=MagicMock(),
        SAMLRelayStateStore=MagicMock(),
    )
    monkeypatch.setitem(sys.modules, "codemie_enterprise.mcp_auth", fake_enterprise_module)

    dependencies._initialized = False
    dependencies._bridge_queue = None
    dependencies._bridge_task = None
    dependencies._bridge_loop = None
    dependencies._mcp_auth_service = None
    dependencies._mcp_auth_trust_policy_service = None
    dependencies._redis_client = None
    dependencies._tms = None
    dependencies._tms_audit_context_provider = None
    dependencies._registered_resolver_types.clear()
    MCPToolkitService._auth_resolvers.clear()

    try:
        dependencies.initialize_mcp_auth()

        assert dependencies.get_mcp_auth_trust_policy_service() is fake_trust_policy_service
        fake_enterprise_module.AuthorizationServerTrustPolicyService.assert_called_once_with(
            config_reader=dependencies.read_mcp_auth_trusted_as_domains_config
        )
    finally:
        MCPToolkitService._auth_resolvers.clear()
        dependencies._registered_resolver_types.clear()
        dependencies._initialized = False
        dependencies._bridge_queue = None
        dependencies._bridge_task = None
        dependencies._bridge_loop = None
        dependencies._mcp_auth_service = None
        dependencies._mcp_auth_trust_policy_service = None
        dependencies._redis_client = None
        dependencies._tms = None
        dependencies._pkce_store = None
        dependencies._saml_relay_state_store = None
        dependencies._redis_encryption = None
        dependencies._tms_audit_context_provider = None
        TokenExchangeService.clear_tms()
        OIDCTokenExchangeService.clear_tms()


def test_trust_policy_bridge_public_exports_exist() -> None:
    from codemie.enterprise import mcp_auth
    from codemie.enterprise.mcp_auth import dependencies

    for name in (
        "get_mcp_auth_trust_policy_service",
        "invalidate_mcp_auth_trust_policy_cache",
        "read_mcp_auth_trusted_as_domains_config",
    ):
        assert name in mcp_auth.__all__
        assert callable(getattr(mcp_auth, name))
        assert callable(getattr(dependencies, name))


def test_initialize_mcp_auth_test_cleanup_leaves_no_global_token_state() -> None:
    from codemie.enterprise.mcp_auth import dependencies
    from codemie.service.security.oidc_token_exchange_service import OIDCTokenExchangeService
    from codemie.service.security.token_exchange_service import TokenExchangeService

    assert dependencies._pkce_store is None
    assert dependencies._saml_relay_state_store is None
    assert dependencies._redis_encryption is None
    assert TokenExchangeService._store is None
    assert OIDCTokenExchangeService._store is None
