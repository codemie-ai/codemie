# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

import ast
import inspect
import subprocess
import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest


def test_discovery_concurrency_limit_defaults_to_5() -> None:
    from codemie.configs.config import Config

    assert Config.model_fields["MCP_AUTH_DISCOVERY_CONCURRENCY_LIMIT"].default == 5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured_value", "expected_limit"),
    [
        (7, 7),
        (0, 5),
        (-1, 5),
    ],
)
async def test_parallel_discovery_bridge_passes_normalized_concurrency_limit(
    monkeypatch: pytest.MonkeyPatch,
    configured_value: int,
    expected_limit: int,
) -> None:
    from codemie.configs import config as runtime_config
    from codemie.enterprise.mcp_auth import dependencies

    captured: dict[str, Any] = {}

    class FakeDiscoveryProbeCandidate:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    async def fake_probe_discovery_eligible_servers(**kwargs: Any) -> list[str]:
        captured.update(kwargs)
        return ["probe-result"]

    fake_discovery_module = SimpleNamespace(
        DiscoveryProbeCandidate=FakeDiscoveryProbeCandidate,
        probe_discovery_eligible_servers=fake_probe_discovery_eligible_servers,
    )

    monkeypatch.setitem(sys.modules, "codemie_enterprise.mcp_auth.discovery", fake_discovery_module)
    monkeypatch.setattr(dependencies, "HAS_MCP_AUTH", True)
    monkeypatch.setattr(runtime_config, "MCP_AUTH_ENABLED", True)
    monkeypatch.setattr(runtime_config, "MCP_AUTH_DISCOVERY_CONCURRENCY_LIMIT", configured_value)
    monkeypatch.setattr(dependencies, "_mcp_auth_discovery_cache", object())
    monkeypatch.setattr(
        dependencies,
        "_mcp_auth_service",
        SimpleNamespace(
            config=SimpleNamespace(
                discovery_probe_overall_timeout_seconds=30.0,
                enforce_https=True,
                resource_metadata_discovery_timeout_seconds=10.0,
                as_metadata_discovery_timeout_seconds=10.0,
            )
        ),
    )
    sentinel_trust_policy = object()

    result = await dependencies.run_mcp_auth_parallel_discovery_probe(
        [
            {
                "server_name": "Server",
                "mcp_resource_url": "https://mcp.example.com/api/mcp",
                "www_authenticate_header": "Bearer",
            }
        ],
        allowed_private_networks=("10.0.0.0/8",),
        trust_policy_service=sentinel_trust_policy,
    )

    assert result == ["probe-result"]
    assert captured["concurrency_limit"] == expected_limit
    assert captured["discovery_cache"] is dependencies._mcp_auth_discovery_cache
    assert captured["trust_policy_service"] is sentinel_trust_policy
    assert captured["protected_resource_discovery_kwargs"]["allowed_private_networks"] == ("10.0.0.0/8",)
    assert captured["authorization_server_discovery_kwargs"]["allowed_private_networks"] == ("10.0.0.0/8",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failing_setup",
    ["import_error", "missing_symbol", "probe_error"],
)
async def test_parallel_discovery_bridge_returns_per_candidate_failures_on_batch_setup_error(
    monkeypatch: pytest.MonkeyPatch,
    failing_setup: str,
) -> None:
    from codemie.configs import config as runtime_config
    from codemie.enterprise.mcp_auth import dependencies

    class FakeDiscoveryProbeCandidate:
        def __init__(self, **kwargs: Any) -> None:
            self.server_name = kwargs["server_name"]

    async def fake_probe_discovery_eligible_servers(**kwargs: Any) -> list[str]:
        del kwargs
        if failing_setup == "probe_error":
            raise RuntimeError("Cookie=sid Authorization: Bearer secret-token")
        return ["should-not-run"]

    fake_discovery_module = SimpleNamespace(
        DiscoveryProbeCandidate=FakeDiscoveryProbeCandidate,
        probe_discovery_eligible_servers=fake_probe_discovery_eligible_servers,
    )
    if failing_setup == "import_error":
        monkeypatch.setattr(
            dependencies,
            "import_module",
            lambda _: (_ for _ in ()).throw(ImportError("Authorization: Bearer secret-token")),
        )
    elif failing_setup == "missing_symbol":
        fake_discovery_module = SimpleNamespace(DiscoveryProbeCandidate=FakeDiscoveryProbeCandidate)
        monkeypatch.setitem(sys.modules, "codemie_enterprise.mcp_auth.discovery", fake_discovery_module)
    else:
        monkeypatch.setitem(sys.modules, "codemie_enterprise.mcp_auth.discovery", fake_discovery_module)

    monkeypatch.setattr(dependencies, "HAS_MCP_AUTH", True)
    monkeypatch.setattr(runtime_config, "MCP_AUTH_ENABLED", True)
    monkeypatch.setattr(dependencies, "_mcp_auth_discovery_cache", object())
    monkeypatch.setattr(
        dependencies,
        "_mcp_auth_service",
        SimpleNamespace(
            config=SimpleNamespace(
                discovery_probe_overall_timeout_seconds=30.0,
                enforce_https=True,
                resource_metadata_discovery_timeout_seconds=10.0,
                as_metadata_discovery_timeout_seconds=10.0,
            )
        ),
    )

    candidates = [
        {
            "server_name": "Server A",
            "mcp_resource_url": "https://a.example.com/api/mcp",
            "www_authenticate_header": "Bearer",
        },
        {
            "server_name": "Server B",
            "mcp_resource_url": "https://b.example.com/api/mcp",
            "www_authenticate_header": "Bearer",
        },
    ]
    with patch("codemie.enterprise.mcp_auth.dependencies.logger.warning") as mock_warning:
        result = await dependencies.run_mcp_auth_parallel_discovery_probe(
            candidates,
            allowed_private_networks=("10.0.0.0/8",),
            trust_policy_service=object(),
        )

    assert [item["server_name"] for item in result] == ["Server A", "Server B"]
    assert [item["status"] for item in result] == ["discovery_failed", "discovery_failed"]
    assert [item["failure_reason"] for item in result] == [
        "discovery_bridge_unavailable",
        "discovery_bridge_unavailable",
    ]
    result_text = str(result)
    log_text = " ".join(str(call.args[0]) for call in mock_warning.call_args_list)
    for sensitive_text in ("secret-token", "Authorization", "Cookie", "sid="):
        assert sensitive_text not in result_text
        assert sensitive_text not in log_text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_reason", "attempted_mechanisms"),
    [
        ("no_supported_registration_mechanism", ("client_id_metadata_document", "dynamic_client_registration")),
        ("dcr_timeout", ("client_id_metadata_document", "dynamic_client_registration")),
        ("dcr_unavailable", ("dynamic_client_registration",)),
    ],
)
async def test_discovered_auth_gate_registration_failures_return_config_error_payload(
    monkeypatch: pytest.MonkeyPatch,
    failure_reason: str,
    attempted_mechanisms: tuple[str, ...],
) -> None:
    from codemie.configs import config as runtime_config
    from codemie.enterprise.mcp_auth import dependencies
    import codemie_enterprise.mcp_auth as enterprise_mcp_auth

    class FakeDiscoveredFlowStore:
        def __init__(self) -> None:
            self.stored: list[Any] = []

        def store(self, snapshot: Any) -> None:
            self.stored.append(snapshot)

    async def fake_resolve_discovered_oauth2_flow(**kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            status="config_error",
            discovered_flow_id=kwargs["discovered_flow_id"],
            as_hostname="auth.example.com",
            snapshot=SimpleNamespace(discovered_flow_id=kwargs["discovered_flow_id"]),
            error_context={
                "server_name": "Catalog",
                "attempted_mechanisms": attempted_mechanisms,
                "failure_reasons": (failure_reason,),
                "action": "Configure auth_config with pre-registered credentials for this server",
            },
        )

    store = FakeDiscoveredFlowStore()
    monkeypatch.setattr(dependencies, "HAS_MCP_AUTH", True)
    monkeypatch.setattr(runtime_config, "MCP_AUTH_ENABLED", True)
    monkeypatch.setattr(dependencies, "_mcp_auth_discovered_flow_store", store)
    monkeypatch.setattr(dependencies, "build_redirect_uri", lambda: ("https://codemie.example.com/callback", "", False))
    monkeypatch.setattr(
        dependencies,
        "_mcp_auth_service",
        SimpleNamespace(
            config=SimpleNamespace(
                allow_local_client_metadata_document_url=False,
                enforce_https=True,
                dcr_registration_timeout_seconds=10.0,
            )
        ),
    )
    monkeypatch.setattr(enterprise_mcp_auth, "create_discovered_flow_id", lambda: f"flow-{failure_reason}")
    monkeypatch.setattr(enterprise_mcp_auth, "resolve_discovered_oauth2_flow", fake_resolve_discovered_oauth2_flow)

    payloads = await dependencies.build_mcp_auth_discovered_auth_gate_payloads(
        discovery_candidates=[
            {
                "server_name": "Catalog",
                "mcp_server_name": "Catalog",
                "mcp_config_name": "Catalog",
                "mcp_config_id": "mcp-config-1",
                "www_authenticate_header": 'Bearer scope="read", error_description="secret-token"',
            }
        ],
        discovery_results=[
            SimpleNamespace(
                server_name="Catalog",
                status="discovered",
                canonical_resource_uri="https://mcp.example.com/api/mcp",
                protected_resource_metadata={},
                authorization_server_metadata={"issuer": "https://auth.example.com"},
            )
        ],
        user_id="user-1",
        session_binding_hash="s" * 64,
        allowed_private_networks=(),
    )

    assert payloads == [
        {
            "mcp_config_id": "mcp-config-1",
            "mcp_config_name": "Catalog",
            "mcp_server_name": "Catalog",
            "auth_type": "oauth2",
            "as_hostname": "auth.example.com",
            "status": "config_error",
            "error_context": {
                "server_name": "Catalog",
                "attempted_mechanisms": attempted_mechanisms,
                "failure_reasons": (failure_reason,),
                "action": "Configure auth_config with pre-registered credentials for this server",
            },
        }
    ]
    assert store.stored[0].discovered_flow_id == f"flow-{failure_reason}"
    assert "auth_config_id" not in payloads[0]
    assert "initiate_url" not in payloads[0]
    assert "secret-token" not in str(payloads)


def test_dependencies_module_does_not_import_discovery_probe_facade_at_module_load() -> None:
    script = """
import sys
for module_name in list(sys.modules):
    if module_name == "codemie.enterprise.mcp_auth.dependencies":
        sys.modules.pop(module_name, None)
    if module_name == "codemie_enterprise.mcp_auth.discovery":
        sys.modules.pop(module_name, None)
import codemie.enterprise.mcp_auth.dependencies  # noqa: F401
raise SystemExit(1 if "codemie_enterprise.mcp_auth.discovery" in sys.modules else 0)
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_toolkit_service_has_no_direct_codemie_enterprise_import() -> None:
    import codemie.service.mcp.toolkit_service as toolkit_service

    assert "codemie_enterprise" not in inspect.getsource(toolkit_service)


def test_toolkit_service_defers_probe_bridge_dependency_import_to_wrapper_call_time() -> None:
    import codemie.service.mcp.toolkit_service as toolkit_service

    tree = ast.parse(inspect.getsource(toolkit_service))
    top_level_imports = [node.module for node in tree.body if isinstance(node, ast.ImportFrom)]
    wrapper = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_mcp_auth_parallel_discovery_probe"
    )
    wrapper_imports = [node.module for node in ast.walk(wrapper) if isinstance(node, ast.ImportFrom)]

    assert "codemie.enterprise.mcp_auth.dependencies" not in top_level_imports
    assert "codemie.enterprise.mcp_auth.dependencies" in wrapper_imports


@pytest.mark.asyncio
async def test_discovery_probe_bridge_does_not_await_async_db_engine_in_worker_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lock in the fix for "Future attached to a different loop" RuntimeError.

    Reproduce the production call shape: an outer event loop is running (the
    FastAPI request loop), so MCPToolkitService._run_coroutine_sync offloads the
    discovery coroutine to a ThreadPoolExecutor worker that calls asyncio.run.
    The worker's loop is different from this test's loop and from the loop that
    owns the application's async DB engine. Verify that the orchestration
    resolves all DB-backed config synchronously and never invokes the async
    readers from inside the bridged coroutine.
    """
    from unittest.mock import AsyncMock

    from codemie.configs import config as runtime_config
    from codemie.enterprise.mcp_auth import dependencies
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    fake_async_allowlist = AsyncMock(side_effect=AssertionError("async DB read called from bridged path"))
    fake_async_trusted_domains = AsyncMock(side_effect=AssertionError("async DB read called from bridged path"))
    monkeypatch.setattr(dependencies, "read_mcp_auth_discovery_private_network_allowlist_config", fake_async_allowlist)
    monkeypatch.setattr(dependencies, "read_mcp_auth_trusted_as_domains_config", fake_async_trusted_domains)
    monkeypatch.setattr(
        dependencies, "read_mcp_auth_discovery_private_network_allowlist_config_sync", lambda: ("10.0.0.0/8",)
    )
    monkeypatch.setattr(dependencies, "read_mcp_auth_trusted_as_domains_config_sync", lambda: None)
    captured_kwargs: dict[str, Any] = {}

    async def fake_probe_discovery_eligible_servers(**kwargs: Any) -> list[Any]:
        captured_kwargs.update(kwargs)
        return [
            SimpleNamespace(
                server_name="Server",
                status="discovery_failed",
                failure_reason="timeout",
                error_context={},
            )
        ]

    class FakeDiscoveryProbeCandidate:
        def __init__(self, **kwargs: Any) -> None:
            self.server_name = kwargs["server_name"]

    fake_discovery_module = SimpleNamespace(
        DiscoveryProbeCandidate=FakeDiscoveryProbeCandidate,
        probe_discovery_eligible_servers=fake_probe_discovery_eligible_servers,
    )
    monkeypatch.setitem(sys.modules, "codemie_enterprise.mcp_auth.discovery", fake_discovery_module)
    monkeypatch.setattr(dependencies, "HAS_MCP_AUTH", True)
    monkeypatch.setattr(runtime_config, "MCP_AUTH_ENABLED", True)
    monkeypatch.setattr(dependencies, "_mcp_auth_discovery_cache", object())
    monkeypatch.setattr(
        dependencies,
        "_mcp_auth_service",
        SimpleNamespace(
            config=SimpleNamespace(
                discovery_probe_overall_timeout_seconds=30.0,
                enforce_https=True,
                resource_metadata_discovery_timeout_seconds=10.0,
                as_metadata_discovery_timeout_seconds=10.0,
            )
        ),
    )

    auth_failures, _warnings = MCPToolkitService._run_discovery_probe_and_collect_failures(
        discovery_candidates=[
            {
                "server_name": "Server",
                "mcp_resource_url": "https://mcp.example.com/api/mcp",
                "www_authenticate_header": "Bearer",
            }
        ],
        user_id="user-1",
        session_binding_hash="s" * 64,
        workflow_execution_id=None,
    )

    assert auth_failures == []
    assert captured_kwargs["protected_resource_discovery_kwargs"]["allowed_private_networks"] == ("10.0.0.0/8",)
    fake_async_allowlist.assert_not_awaited()
    fake_async_trusted_domains.assert_not_awaited()
