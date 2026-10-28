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

"""MCP_AUTH_CIMD_DISABLED: force DCR by reporting CIMD unsupported to the resolver."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from codemie_enterprise.mcp_auth import AuthorizationServerMetadataDiscoveryResult
from codemie_enterprise.mcp_auth.discovery.client_registration import resolve_client_registration
from codemie_enterprise.mcp_auth.discovery.orchestrator import DiscoveryOrchestrationResult

from codemie.enterprise.mcp_auth import _discovery

REGISTRATION_ENDPOINT = "https://idp.example.com/register"


def _result(cimd, endpoint):
    return DiscoveryOrchestrationResult(
        server_name="OneHub",
        canonical_resource_uri="https://mcp.example.com/",
        status="discovered",
        authorization_server_metadata={
            "issuer": "https://idp.example.com",
            "status": "discovered",
            "authorization_endpoint": "https://idp.example.com/authorize",
            "token_endpoint": "https://idp.example.com/token",
            "response_types_supported": ["code"],
            "code_challenge_methods_supported": ["S256"],
            "client_id_metadata_document_supported": cimd,
            "registration_endpoint": endpoint,
        },
    )


def _as_field(result, field):
    return result.authorization_server_metadata.get(field)


@pytest.fixture
def harness(monkeypatch):
    from codemie.enterprise.mcp_auth import dependencies as deps

    dcr_cache = SimpleNamespace(name="dcr-cache")
    monkeypatch.setattr(
        deps,
        "_mcp_auth_service",
        SimpleNamespace(config=SimpleNamespace(enforce_https=True, dcr_registration_timeout_seconds=5)),
    )
    monkeypatch.setattr(deps, "_mcp_auth_dcr_credentials_cache", dcr_cache)
    monkeypatch.setattr(
        deps, "_require_initialized_discovered_flow_store", lambda: SimpleNamespace(store=lambda s: None)
    )

    calls: list[dict] = []

    async def fake_resolve(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            status="authentication_required",
            auth_config_id="auth-1",
            discovered_flow_id="flow-1",
            as_hostname="idp.example.com",
            snapshot=SimpleNamespace(
                issuer="https://idp.example.com",
                as_hostname="idp.example.com",
                canonical_resource="https://mcp.example.com/",
                registration_method="dcr",
                registration_reason_code="x",
                registration_profile_fingerprint="fp",
            ),
            error_context=None,
        )

    monkeypatch.setitem(
        sys.modules,
        "codemie_enterprise.mcp_auth",
        SimpleNamespace(
            ClientRegistrationNetworkOptions=lambda **kw: SimpleNamespace(**kw),
            MCPAuthRedisUnavailable=RuntimeError,
            create_discovered_flow_id=lambda: "flow-generated-1",
            parse_bearer_challenge_scope=lambda header: None,
            resolve_discovered_oauth2_flow=fake_resolve,
        ),
    )
    return SimpleNamespace(calls=calls, dcr_cache=dcr_cache)


def _set_disabled(monkeypatch, value: bool) -> None:
    monkeypatch.setattr(_discovery.config, "MCP_AUTH_CIMD_DISABLED", value, raising=False)


async def _run(result):
    return await _discovery._resolve_discovered_candidate_payload(
        candidate={"mcp_config_id": "mcp-1", "mcp_config_name": "OneHub"},
        result=result,
        user_id="user-1",
        session_binding_hash="binding-1",
        workflow_execution_id=None,
        flow_config=_discovery._DiscoveredFlowResolutionConfig(
            redirect_uri="https://api.example.com/cb",
            client_metadata_document_url="https://api.example.com/.well-known/cimd",
            allowed_private_networks=(),
            allow_local_client_metadata_document_url=False,
        ),
    )


@pytest.mark.asyncio
async def test_setting_off_passes_result_unchanged(monkeypatch, harness):
    _set_disabled(monkeypatch, False)
    result = _result(True, REGISTRATION_ENDPOINT)

    await _run(result)

    assert harness.calls[0]["discovery_result"] is result


@pytest.mark.asyncio
async def test_on_both_methods_passes_copy_with_cimd_false(monkeypatch, harness):
    _set_disabled(monkeypatch, True)
    result = _result(True, REGISTRATION_ENDPOINT)

    payload = await _run(result)

    passed = harness.calls[0]["discovery_result"]
    assert payload["status"] == "authentication_required"
    assert passed is not result
    assert _as_field(passed, "client_id_metadata_document_supported") is False
    assert _as_field(passed, "registration_endpoint") == REGISTRATION_ENDPOINT
    assert _as_field(result, "client_id_metadata_document_supported") is True


@pytest.mark.asyncio
async def test_on_dcr_only_unchanged_behavior(monkeypatch, harness):
    _set_disabled(monkeypatch, True)
    result = _result(False, REGISTRATION_ENDPOINT)

    await _run(result)

    assert harness.calls[0]["discovery_result"] is result


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [None, "", "   "])
async def test_on_cimd_only_returns_config_error(monkeypatch, harness, endpoint):
    _set_disabled(monkeypatch, True)

    payload = await _run(_result(True, endpoint))

    assert harness.calls == []
    assert payload["status"] == "config_error"
    message = payload["error_context"]
    assert isinstance(message, str)  # the UI drops non-string error_context
    assert "only the client metadata document" in message
    assert "turned off in this environment" in message
    assert "does not support" not in message
    assert "not supported" not in message


@pytest.mark.asyncio
async def test_on_cache_hit_receives_dcr_cache(monkeypatch, harness):
    _set_disabled(monkeypatch, True)

    await _run(_result(True, REGISTRATION_ENDPOINT))

    assert harness.calls[0]["dcr_credentials_cache"] is harness.dcr_cache


@pytest.mark.asyncio
async def test_on_neither_method_calls_resolver(monkeypatch, harness):
    _set_disabled(monkeypatch, True)
    result = _result(False, None)

    await _run(result)

    assert len(harness.calls) == 1
    assert harness.calls[0]["discovery_result"] is result


@pytest.mark.asyncio
async def test_forced_copy_makes_real_resolver_choose_dcr(monkeypatch, harness):
    _set_disabled(monkeypatch, True)
    result = _result(True, REGISTRATION_ENDPOINT)
    await _run(result)
    forced = harness.calls[0]["discovery_result"]

    def register(discovery_result):
        return resolve_client_registration(
            AuthorizationServerMetadataDiscoveryResult.model_validate(discovery_result.authorization_server_metadata),
            client_metadata_document_url="https://api.example.com/.well-known/cimd",
            redirect_uris=["https://api.example.com/cb"],
            timeout_seconds=5,
        )

    # Without a DCR cache the real resolver raises only when it selects DCR.
    with pytest.raises(ValueError, match="dcr_credentials_cache is required"):
        await register(forced)
    assert (await register(result)).method == "client_id_metadata_document"
