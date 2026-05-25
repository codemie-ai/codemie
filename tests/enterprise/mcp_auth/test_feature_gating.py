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

"""Tests for MCP Auth enterprise feature gating (Story 1.1).

Covers:
- Config flag MCP_AUTH_ENABLED defaults to False
- Loader HAS_MCP_AUTH flag and has_mcp_auth() predicate
- Bridge layer is_mcp_auth_enabled() dual-gate logic
- Zero blast radius when feature is disabled
"""

from __future__ import annotations

import inspect
import importlib
import sys
from unittest.mock import MagicMock

import pytest


# ===========================================
# Task 1: Config flag tests
# ===========================================


def test_mcp_auth_enabled_defaults_to_false():
    """AC#3: MCP_AUTH_ENABLED defaults to False"""
    from codemie.configs.config import Config

    assert Config.model_fields["MCP_AUTH_ENABLED"].default is False


def test_mcp_auth_enabled_from_env_var(monkeypatch):
    """AC#1: MCP_AUTH_ENABLED is populated from environment variable"""
    monkeypatch.setenv("MCP_AUTH_ENABLED", "true")

    from codemie.configs.config import Config

    config = Config(_env_file=None)  # type: ignore[call-arg]
    assert config.MCP_AUTH_ENABLED is True


def test_mcp_auth_hmac_secret_defaults_to_empty_string():
    from codemie.configs.config import Config

    assert Config.model_fields["MCP_AUTH_HMAC_SECRET"].default == ""


# ===========================================
# Task 2: Loader flag and predicate tests
# ===========================================


def test_has_mcp_auth_flag_exists():
    """HAS_MCP_AUTH flag is exported from loader"""
    from codemie.enterprise.loader import HAS_MCP_AUTH

    assert isinstance(HAS_MCP_AUTH, bool)


def test_has_mcp_auth_is_false_without_enterprise(monkeypatch):
    """AC#2: HAS_MCP_AUTH is False when enterprise package not installed"""
    monkeypatch.setattr("codemie.enterprise.loader.HAS_MCP_AUTH", False)

    from codemie.enterprise.loader import has_mcp_auth

    assert has_mcp_auth() is False


def test_has_mcp_auth_predicate_returns_false(monkeypatch):
    """has_mcp_auth() returns False when enterprise package not installed"""
    monkeypatch.setattr("codemie.enterprise.loader.HAS_MCP_AUTH", False)

    from codemie.enterprise.loader import has_mcp_auth

    assert has_mcp_auth() is False


def test_has_mcp_auth_predicate_returns_true_when_installed(monkeypatch):
    """has_mcp_auth() returns True when enterprise mcp_auth is available"""
    monkeypatch.setattr("codemie.enterprise.loader.HAS_MCP_AUTH", True)

    from codemie.enterprise.loader import has_mcp_auth

    assert has_mcp_auth() is True


def test_mcp_auth_exports_in_loader_all():
    """HAS_MCP_AUTH and has_mcp_auth are in loader.__all__"""
    from codemie.enterprise import loader

    assert "HAS_MCP_AUTH" in loader.__all__
    assert "has_mcp_auth" in loader.__all__


# ===========================================
# Task 3: Bridge layer dual-gate tests
# ===========================================


def test_is_mcp_auth_enabled_false_when_no_package(monkeypatch):
    """AC#2: is_mcp_auth_enabled() returns False when enterprise not installed"""
    monkeypatch.setattr("codemie.enterprise.mcp_auth.dependencies.HAS_MCP_AUTH", False)
    from codemie.configs import config

    monkeypatch.setattr(config, "MCP_AUTH_ENABLED", False)
    monkeypatch.setattr(config, "MCP_AUTH_TMS_ENABLED", False)

    from codemie.enterprise.mcp_auth.dependencies import is_mcp_auth_enabled

    assert is_mcp_auth_enabled() is False


def test_is_mcp_auth_enabled_fails_closed_when_tms_enabled_without_package(monkeypatch):
    monkeypatch.setattr("codemie.enterprise.mcp_auth.dependencies.HAS_MCP_AUTH", False)
    from codemie.configs import config

    monkeypatch.setattr(config, "MCP_AUTH_ENABLED", True)
    monkeypatch.setattr(config, "MCP_AUTH_TMS_ENABLED", True)

    from codemie.enterprise.mcp_auth.dependencies import is_mcp_auth_enabled

    with pytest.raises(RuntimeError, match="enterprise MCP auth package"):
        is_mcp_auth_enabled()


def test_is_mcp_auth_enabled_false_when_config_disabled(monkeypatch):
    """AC#3: is_mcp_auth_enabled() returns False when MCP_AUTH_ENABLED=False"""
    monkeypatch.setattr("codemie.enterprise.mcp_auth.dependencies.HAS_MCP_AUTH", True)

    from codemie.configs import config

    monkeypatch.setattr(config, "MCP_AUTH_ENABLED", False)

    from codemie.enterprise.mcp_auth.dependencies import is_mcp_auth_enabled

    assert is_mcp_auth_enabled() is False


def test_is_mcp_auth_enabled_true_when_both_gates_satisfied(monkeypatch):
    """AC#1: is_mcp_auth_enabled() returns True when both gates pass"""
    monkeypatch.setattr("codemie.enterprise.mcp_auth.dependencies.HAS_MCP_AUTH", True)

    from codemie.configs import config

    monkeypatch.setattr(config, "MCP_AUTH_ENABLED", True)

    from codemie.enterprise.mcp_auth.dependencies import is_mcp_auth_enabled

    assert is_mcp_auth_enabled() is True


def test_is_mcp_auth_enabled_reexported_from_bridge_init():
    """is_mcp_auth_enabled is importable from codemie.enterprise.mcp_auth"""
    from codemie.enterprise.mcp_auth import is_mcp_auth_enabled

    assert callable(is_mcp_auth_enabled)


def test_is_mcp_auth_enabled_reexported_from_enterprise_init():
    """is_mcp_auth_enabled is importable from codemie.enterprise"""
    from codemie.enterprise import is_mcp_auth_enabled

    assert callable(is_mcp_auth_enabled)


def test_has_mcp_auth_reexported_from_enterprise_init():
    """HAS_MCP_AUTH and has_mcp_auth are importable from codemie.enterprise"""
    from codemie.enterprise import HAS_MCP_AUTH, has_mcp_auth

    assert isinstance(HAS_MCP_AUTH, bool)
    assert callable(has_mcp_auth)


# ===========================================
# Task 4: Zero blast radius tests
# ===========================================


def test_no_side_effects_on_import():
    """AC#4: Importing MCP auth modules has no side effects even on fresh load.

    Only evicts the mcp_auth-specific modules — NOT the shared loader or
    enterprise __init__ — so that other tests' monkeypatched HAS_* flags
    are not clobbered by a fresh loader re-import.
    """
    import codemie.enterprise as _enterprise_pkg

    mcp_auth_modules = [
        "codemie.enterprise.mcp_auth.dependencies",
        "codemie.enterprise.mcp_auth",
    ]
    original_modules = {name: sys.modules.get(name) for name in mcp_auth_modules}
    original_mcp_auth_attr = getattr(_enterprise_pkg, "mcp_auth", None)

    try:
        for name in mcp_auth_modules:
            sys.modules.pop(name, None)

        fresh_deps = importlib.import_module("codemie.enterprise.mcp_auth.dependencies")
        fresh_mcp_auth = importlib.import_module("codemie.enterprise.mcp_auth")

        assert callable(fresh_deps.is_mcp_auth_enabled)
        assert callable(fresh_mcp_auth.is_mcp_auth_enabled)
    finally:
        for name in mcp_auth_modules:
            sys.modules.pop(name, None)
        for name, module in original_modules.items():
            if module is not None:
                sys.modules[name] = module
        # Restore the mcp_auth attribute on the parent package so that
        # `from codemie.enterprise import mcp_auth` in subsequent tests
        # returns the original module, not the fresh one loaded here.
        if original_mcp_auth_attr is not None:
            _enterprise_pkg.mcp_auth = original_mcp_auth_attr
        elif hasattr(_enterprise_pkg, "mcp_auth"):
            delattr(_enterprise_pkg, "mcp_auth")


def test_is_mcp_auth_enabled_returns_false_cleanly_without_enterprise():
    """AC#4: is_mcp_auth_enabled() returns False cleanly without enterprise"""
    from codemie.enterprise.mcp_auth.dependencies import is_mcp_auth_enabled

    # Should return False without raising
    result = is_mcp_auth_enabled()
    assert result is False


def test_build_authentication_required_exception_adds_status_aware_payload(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies

    monkeypatch.setattr(
        dependencies,
        "get_mcp_auth_status_payload",
        lambda auth_config_id: {
            "auth_config_id": auth_config_id,
            "mcp_config_id": "mcp-1",
            "mcp_server_name": "server-1",
        },
    )

    exception = dependencies._build_authentication_required_exception(
        "auth-1",
        status="config_error",
        auth_type="oauth2",
        error_context="Credential storage service unavailable. Contact your administrator.",
    )

    assert exception.payload == {
        "auth_config_id": "auth-1",
        "mcp_config_id": "mcp-1",
        "mcp_server_name": "server-1",
        "status": "config_error",
        "auth_type": "oauth2",
        "error_context": "Credential storage service unavailable. Contact your administrator.",
    }


def test_build_authentication_required_exception_keeps_optional_fields_json_serializable(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies

    monkeypatch.setattr(dependencies, "get_mcp_auth_status_payload", lambda auth_config_id: None)

    exception = dependencies._build_authentication_required_exception("auth-1", status="session_expired")

    assert exception.payload == {
        "auth_config_id": "auth-1",
        "status": "session_expired",
        "auth_type": None,
        "error_context": None,
    }


def test_build_authentication_required_exception_signature_is_keyword_only_for_status_metadata() -> None:
    from codemie.enterprise.mcp_auth import dependencies

    signature = inspect.signature(dependencies._build_authentication_required_exception)

    assert str(signature) == (
        "(auth_config_id: 'str', *, status: 'str' = 'authentication_required', "
        "auth_type: 'str | None' = None, error_context: 'str | None' = None) "
        "-> 'MCPAuthenticationRequiredException'"
    )

    with pytest.raises(TypeError):
        dependencies._build_authentication_required_exception("auth-1", "config_error")


def test_initialize_mcp_auth_skips_activation_time_registration_when_enterprise_package_missing(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    create_redis_client = MagicMock()
    monkeypatch.setattr(dependencies, "HAS_MCP_AUTH", False)
    monkeypatch.setattr(dependencies, "create_redis_client", create_redis_client)
    dependencies._registered_resolver_types.clear()
    MCPToolkitService._auth_resolvers.clear()

    try:
        dependencies.initialize_mcp_auth()

        create_redis_client.assert_not_called()
        assert MCPToolkitService._auth_resolvers == []
        assert dependencies._registered_resolver_types == set()
    finally:
        MCPToolkitService._auth_resolvers.clear()
        dependencies._registered_resolver_types.clear()


def test_initialize_mcp_auth_skips_activation_time_registration_when_feature_disabled(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies
    from codemie.configs import config as runtime_config
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    create_redis_client = MagicMock()
    monkeypatch.setattr(dependencies, "HAS_MCP_AUTH", True)
    monkeypatch.setattr(runtime_config, "MCP_AUTH_ENABLED", False)
    monkeypatch.setattr(dependencies, "create_redis_client", create_redis_client)
    dependencies._registered_resolver_types.clear()
    MCPToolkitService._auth_resolvers.clear()

    try:
        dependencies.initialize_mcp_auth()

        create_redis_client.assert_not_called()
        assert MCPToolkitService._auth_resolvers == []
        assert dependencies._registered_resolver_types == set()
    finally:
        MCPToolkitService._auth_resolvers.clear()
        dependencies._registered_resolver_types.clear()


def test_initialize_mcp_auth_raises_for_missing_secret_when_feature_enabled(monkeypatch):
    from codemie.enterprise.mcp_auth import dependencies
    from codemie.configs import config as runtime_config

    monkeypatch.setattr(dependencies, "HAS_MCP_AUTH", True)
    monkeypatch.setattr(runtime_config, "MCP_AUTH_ENABLED", True)
    monkeypatch.setattr(runtime_config, "MCP_AUTH_HMAC_SECRET", "")

    initialize_mcp_auth = dependencies.initialize_mcp_auth

    with pytest.raises(RuntimeError, match="MCP_AUTH_HMAC_SECRET"):
        initialize_mcp_auth()


def test_initialize_mcp_auth_raises_for_weak_secret_when_feature_enabled(monkeypatch):
    from codemie.enterprise.mcp_auth import dependencies
    from codemie.configs import config as runtime_config

    monkeypatch.setattr(dependencies, "HAS_MCP_AUTH", True)
    monkeypatch.setattr(runtime_config, "MCP_AUTH_ENABLED", True)
    monkeypatch.setattr(runtime_config, "MCP_AUTH_HMAC_SECRET", "short-secret")

    initialize_mcp_auth = dependencies.initialize_mcp_auth

    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        initialize_mcp_auth()


def test_initialize_mcp_auth_registers_resolver_once(monkeypatch):
    from codemie.enterprise.mcp_auth import dependencies
    from codemie.configs import config as runtime_config
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    class FakeResolver:
        def __init__(self, token_management_system, authentication_required_factory, audit_context_provider=None):
            self.token_management_system = token_management_system
            self.authentication_required_factory = authentication_required_factory
            self.audit_context_provider = audit_context_provider

        def can_handle(self, server_config):
            return True

        def resolve(self, server_config, user_id, execution_context=None):
            return None

    def create_task(coroutine):
        coroutine.close()
        return MagicMock(done=lambda: False)

    fake_enterprise_module = MagicMock(
        DCRCredentialsCache=MagicMock(),
        DiscoveryMetadataCache=MagicMock(),
        MCPAuthResolver=FakeResolver,
        MCPAuthService=MagicMock(return_value=MagicMock(initialize=MagicMock(), shutdown=MagicMock())),
        MCPAuthServiceConfig=MagicMock(return_value=MagicMock()),
        MockTokenManagementSystem=MagicMock(return_value=MagicMock()),
        RedisEncryption=MagicMock(return_value=MagicMock()),
        RedisPKCEStore=MagicMock(),
    )

    monkeypatch.setattr(dependencies, "HAS_MCP_AUTH", True)
    monkeypatch.setattr(runtime_config, "MCP_AUTH_ENABLED", True)
    monkeypatch.setattr(runtime_config, "MCP_AUTH_HMAC_SECRET", "s" * 32)
    monkeypatch.setattr(dependencies.asyncio, "get_running_loop", lambda: MagicMock(create_task=create_task))
    monkeypatch.setattr(dependencies, "create_redis_client", lambda: MagicMock(close=MagicMock()))
    monkeypatch.setattr(dependencies, "_build_token_management_system", MagicMock(return_value=MagicMock()))
    monkeypatch.setitem(sys.modules, "codemie_enterprise.mcp_auth", fake_enterprise_module)

    dependencies._initialized = False
    dependencies._bridge_queue = None
    dependencies._bridge_task = None
    dependencies._bridge_loop = None
    dependencies._mcp_auth_service = None
    dependencies._redis_client = None
    dependencies._registered_resolver_types.clear()
    MCPToolkitService._auth_resolvers.clear()

    try:
        dependencies.initialize_mcp_auth()
        dependencies.initialize_mcp_auth()

        assert len(MCPToolkitService._auth_resolvers) == 1
    finally:
        MCPToolkitService._auth_resolvers.clear()
        dependencies._registered_resolver_types.clear()
        dependencies._initialized = False
        dependencies._bridge_queue = None
        dependencies._bridge_task = None
        dependencies._bridge_loop = None
        dependencies._mcp_auth_service = None
        dependencies._redis_client = None


@pytest.mark.asyncio
async def test_bridge_consumer_logs_and_continues_on_unexpected_enqueue_failure(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies

    bridge_queue: dependencies.asyncio.Queue[str] = dependencies.asyncio.Queue()
    log_messages: list[str] = []

    class FlakyService:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def enqueue_cleanup(self, user_id: str) -> None:
            self.calls.append(user_id)
            if user_id == "user-1":
                raise RuntimeError("boom")

    service = FlakyService()
    monkeypatch.setattr(dependencies.logger, "exception", log_messages.append)

    consumer_task = dependencies.asyncio.create_task(dependencies._bridge_consumer(bridge_queue, service))
    await bridge_queue.put("user-1")
    await bridge_queue.put("user-2")
    await dependencies.asyncio.wait_for(bridge_queue.join(), timeout=1)
    consumer_task.cancel()
    with pytest.raises(dependencies.asyncio.CancelledError):
        await consumer_task

    assert service.calls == ["user-1", "user-2"]
    assert any("user_id=user-1" in message for message in log_messages)


def test_initialize_mcp_auth_closes_partial_resources_when_startup_fails(monkeypatch):
    from codemie.enterprise.mcp_auth import dependencies
    from codemie.configs import config as runtime_config

    redis_client = MagicMock()
    created_service = MagicMock()
    fake_resolver = MagicMock()
    fake_enterprise_module = MagicMock(
        DCRCredentialsCache=MagicMock(),
        DiscoveryMetadataCache=MagicMock(),
        MCPAuthResolver=MagicMock(return_value=fake_resolver),
        MCPAuthService=MagicMock(return_value=created_service),
        MCPAuthServiceConfig=MagicMock(return_value=MagicMock()),
        MockTokenManagementSystem=MagicMock(return_value=MagicMock()),
        RedisEncryption=MagicMock(return_value=MagicMock()),
        RedisPKCEStore=MagicMock(),
    )
    loop = MagicMock()

    def raise_create_task_error(coroutine):
        coroutine.close()
        raise RuntimeError("create_task failed")

    loop.create_task.side_effect = raise_create_task_error

    monkeypatch.setattr(dependencies, "HAS_MCP_AUTH", True)
    monkeypatch.setattr(runtime_config, "MCP_AUTH_ENABLED", True)
    monkeypatch.setattr(runtime_config, "MCP_AUTH_HMAC_SECRET", "s" * 32)
    monkeypatch.setattr(dependencies.asyncio, "get_running_loop", lambda: loop)
    monkeypatch.setattr(dependencies, "create_redis_client", lambda: redis_client)
    monkeypatch.setattr(dependencies, "_build_token_management_system", MagicMock(return_value=MagicMock()))
    monkeypatch.setitem(sys.modules, "codemie_enterprise.mcp_auth", fake_enterprise_module)

    dependencies._initialized = False
    dependencies._bridge_queue = None
    dependencies._bridge_task = None
    dependencies._bridge_loop = None
    dependencies._mcp_auth_service = None
    dependencies._redis_client = None
    dependencies._registered_resolver_types.clear()

    with pytest.raises(RuntimeError, match="create_task failed"):
        dependencies.initialize_mcp_auth()

    created_service.initialize.assert_called_once_with()
    created_service.shutdown.assert_called_once_with()
    redis_client.close.assert_called_once_with()
    assert dependencies._initialized is False
    assert dependencies._bridge_queue is None
    assert dependencies._bridge_task is None
    assert dependencies._bridge_loop is None
    assert dependencies._mcp_auth_service is None
    assert dependencies._redis_client is None
    assert len(dependencies._registered_resolver_types) == 0


def test_enqueue_mcp_auth_cleanup_returns_cleanly_when_bridge_unavailable(monkeypatch):
    from codemie.enterprise.mcp_auth import dependencies

    dependencies._bridge_loop = None
    dependencies._bridge_queue = None
    dependencies._bridge_task = None

    debug_messages: list[str] = []
    monkeypatch.setattr(dependencies.logger, "debug", debug_messages.append)

    dependencies.enqueue_mcp_auth_cleanup("user-1")

    assert debug_messages


def test_has_any_credentials_for_auth_config_returns_false_when_mcp_auth_disabled(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies

    dependencies._tms = None
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: False)

    assert dependencies.has_any_credentials_for_auth_config("auth-id") is False


def test_has_any_credentials_for_auth_config_fails_closed_when_tms_uninitialized(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies

    warning_messages: list[str] = []
    dependencies._tms = None
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    monkeypatch.setattr(dependencies.logger, "warning", warning_messages.append)

    assert dependencies.has_any_credentials_for_auth_config("auth-id") is True
    assert any("auth_config_id=auth-id" in message for message in warning_messages)


def test_has_any_credentials_for_auth_config_fails_closed_on_tms_error(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies

    warning_messages: list[str] = []

    class BrokenTMS:
        def has_any_credentials(self, auth_config_id: str) -> bool:
            raise RuntimeError(f"secret-bearing-message:{auth_config_id}")

    dependencies._tms = BrokenTMS()
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)
    monkeypatch.setattr(dependencies.logger, "warning", warning_messages.append)

    assert dependencies.has_any_credentials_for_auth_config("auth-id") is True
    assert any("auth_config_id=auth-id" in message for message in warning_messages)
    assert all("secret-bearing-message" not in message for message in warning_messages)


def test_invalidate_credentials_for_auth_config_is_noop_when_bridge_unavailable(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies

    dependencies._tms = None
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)

    dependencies.invalidate_credentials_for_auth_config("auth-id")


def test_invalidate_credentials_for_auth_config_delegates_to_tms(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies

    tms = MagicMock()
    dependencies._tms = tms
    monkeypatch.setattr(dependencies, "is_mcp_auth_enabled", lambda: True)

    dependencies.invalidate_credentials_for_auth_config("auth-id")

    tms.invalidate_by_config.assert_called_once_with("auth-id")


@pytest.mark.asyncio
async def test_shutdown_mcp_auth_resets_bridge_state() -> None:
    from codemie.enterprise.mcp_auth import dependencies

    bridge_task = MagicMock()
    bridge_task.cancel = MagicMock()

    async def awaitable_task():
        return None

    class AwaitableBridgeTask:
        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            bridge_task.cancel()

        def __await__(self):
            return awaitable_task().__await__()

    redis_client = MagicMock()
    mcp_auth_service = MagicMock()
    dependencies._initialized = True
    dependencies._bridge_queue = MagicMock()
    dependencies._bridge_task = AwaitableBridgeTask()
    dependencies._bridge_loop = MagicMock()
    dependencies._mcp_auth_service = mcp_auth_service
    dependencies._redis_client = redis_client
    dependencies._tms = MagicMock()
    dependencies._tms_audit_context_provider = MagicMock()
    dependencies._registered_resolver_types = {object}

    await dependencies.shutdown_mcp_auth()

    bridge_task.cancel.assert_called_once_with()
    mcp_auth_service.shutdown.assert_called_once_with()
    redis_client.close.assert_called_once_with()
    assert dependencies._initialized is False
    assert dependencies._bridge_queue is None
    assert dependencies._bridge_task is None
    assert dependencies._bridge_loop is None
    assert dependencies._mcp_auth_service is None
    assert dependencies._redis_client is None
    assert dependencies._tms is None
    assert dependencies._tms_audit_context_provider is None
    assert len(dependencies._registered_resolver_types) == 0


@pytest.mark.asyncio
async def test_shutdown_mcp_auth_resets_bridge_state_when_cleanup_steps_fail() -> None:
    from codemie.enterprise.mcp_auth import dependencies

    class FailingBridgeTask:
        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            raise RuntimeError("cancel failed")

    dependencies._initialized = True
    dependencies._bridge_queue = MagicMock()
    dependencies._bridge_task = FailingBridgeTask()
    dependencies._bridge_loop = MagicMock()
    dependencies._mcp_auth_service = MagicMock(shutdown=MagicMock(side_effect=RuntimeError("shutdown failed")))
    dependencies._redis_client = MagicMock(close=MagicMock(side_effect=RuntimeError("close failed")))
    dependencies._tms = MagicMock()
    dependencies._tms_audit_context_provider = MagicMock()
    dependencies._registered_resolver_types = {object}

    await dependencies.shutdown_mcp_auth()

    assert dependencies._initialized is False
    assert dependencies._bridge_queue is None
    assert dependencies._bridge_task is None
    assert dependencies._bridge_loop is None
    assert dependencies._mcp_auth_service is None
    assert dependencies._redis_client is None
    assert dependencies._tms is None
    assert dependencies._tms_audit_context_provider is None
    assert len(dependencies._registered_resolver_types) == 0


def test_initialize_mcp_auth_preserves_startup_failure_when_cleanup_steps_fail(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies
    from codemie.configs import config as runtime_config
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    class FakeResolver:
        def __init__(self, token_management_system, authentication_required_factory, audit_context_provider=None):
            self.token_management_system = token_management_system
            self.authentication_required_factory = authentication_required_factory
            self.audit_context_provider = audit_context_provider

    bridge_task = MagicMock()
    bridge_task.cancel.side_effect = RuntimeError("cancel-secret")
    redis_client = MagicMock()
    redis_client.close.side_effect = RuntimeError("redis-secret")
    created_service = MagicMock()
    created_service.shutdown.side_effect = RuntimeError("shutdown-secret")
    fake_enterprise_module = MagicMock(
        DCRCredentialsCache=MagicMock(),
        DiscoveryMetadataCache=MagicMock(),
        MCPAuthResolver=FakeResolver,
        MCPAuthService=MagicMock(return_value=created_service),
        MCPAuthServiceConfig=MagicMock(return_value=MagicMock()),
        MockTokenManagementSystem=MagicMock(return_value=MagicMock()),
        RedisEncryption=MagicMock(return_value=MagicMock()),
        RedisPKCEStore=MagicMock(),
    )

    def create_task(coroutine):
        coroutine.close()
        return bridge_task

    warning_messages: list[str] = []
    monkeypatch.setattr(dependencies, "HAS_MCP_AUTH", True)
    monkeypatch.setattr(runtime_config, "MCP_AUTH_ENABLED", True)
    monkeypatch.setattr(runtime_config, "MCP_AUTH_HMAC_SECRET", "s" * 32)
    monkeypatch.setattr(dependencies.asyncio, "get_running_loop", lambda: MagicMock(create_task=create_task))
    monkeypatch.setattr(dependencies, "create_redis_client", lambda: redis_client)
    monkeypatch.setattr(dependencies, "_build_token_management_system", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(dependencies.logger, "warning", warning_messages.append)
    monkeypatch.setattr(
        MCPToolkitService,
        "register_auth_resolver",
        MagicMock(side_effect=RuntimeError("registration failed")),
    )
    monkeypatch.setitem(sys.modules, "codemie_enterprise.mcp_auth", fake_enterprise_module)

    dependencies._initialized = False
    dependencies._bridge_queue = None
    dependencies._bridge_task = None
    dependencies._bridge_loop = None
    dependencies._mcp_auth_service = None
    dependencies._redis_client = None
    dependencies._registered_resolver_types.clear()

    try:
        with pytest.raises(RuntimeError, match="registration failed"):
            dependencies.initialize_mcp_auth()

        bridge_task.cancel.assert_called_once_with()
        created_service.shutdown.assert_called_once_with()
        redis_client.close.assert_called_once_with()
        assert all("cancel-secret" not in message for message in warning_messages)
        assert all("shutdown-secret" not in message for message in warning_messages)
        assert all("redis-secret" not in message for message in warning_messages)
    finally:
        MCPToolkitService._auth_resolvers.clear()
        dependencies._registered_resolver_types.clear()
