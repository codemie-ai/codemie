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

import inspect
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from codemie.configs import config
from codemie.configs.logger import logger

from ._common import (
    _build_discovered_config_error_payload,
    _build_discovered_initiate_url,
    _candidate_string,
    _get_discovery_candidate_field,
)
from ._constants import (
    _CLIENT_METADATA_DOCUMENT_PATH,
    _DISCOVERED_AUTH_RECOVERY_ACTION,
    DISCOVERY_BRIDGE_UNAVAILABLE_FAILURE_REASON,
)
from ._guards import is_mcp_auth_enabled
from ._trust_policy import _normalize_discovery_concurrency_limit

_DEPS_MODULE = "codemie.enterprise.mcp_auth.dependencies"


def _deps() -> Any:
    return sys.modules[_DEPS_MODULE]


@dataclass(frozen=True)
class _DiscoveredFlowResolutionConfig:
    redirect_uri: str
    client_metadata_document_url: str
    allowed_private_networks: tuple[str, ...]
    allow_local_client_metadata_document_url: bool


async def run_mcp_auth_parallel_discovery_probe(
    candidates: Iterable[Mapping[str, Any] | Any],
    *,
    allowed_private_networks: tuple[str, ...],
    trust_policy_service: Any,
) -> list[Any]:
    candidate_list = list(candidates)
    if not is_mcp_auth_enabled():
        return []
    deps = _deps()
    if deps._mcp_auth_discovery_cache is None or deps._mcp_auth_service is None or trust_policy_service is None:
        logger.warning("Skipping MCP auth discovery probe because MCP auth discovery dependencies are not initialized")
        return []

    mcp_auth_config = deps._mcp_auth_service.config
    try:
        discovery_module = deps.import_module("codemie_enterprise.mcp_auth.discovery")
        discovery_probe_candidate = getattr(discovery_module, "DiscoveryProbeCandidate")
        probe_discovery_eligible_servers = getattr(discovery_module, "probe_discovery_eligible_servers")
        typed_candidates = [
            discovery_probe_candidate(**candidate) if isinstance(candidate, Mapping) else candidate
            for candidate in candidate_list
        ]

        return await probe_discovery_eligible_servers(
            candidates=typed_candidates,
            discovery_cache=deps._mcp_auth_discovery_cache,
            trust_policy_service=trust_policy_service,
            concurrency_limit=_normalize_discovery_concurrency_limit(config.MCP_AUTH_DISCOVERY_CONCURRENCY_LIMIT),
            overall_timeout_seconds=mcp_auth_config.discovery_probe_overall_timeout_seconds,
            protected_resource_discovery_kwargs={
                "allowed_private_networks": allowed_private_networks,
                "enforce_https": mcp_auth_config.enforce_https,
                "discovery_timeout_seconds": mcp_auth_config.resource_metadata_discovery_timeout_seconds,
            },
            authorization_server_discovery_kwargs={
                "allowed_private_networks": allowed_private_networks,
                "enforce_https": config.MCP_AUTH_ENFORCE_HTTPS,
                "discovery_timeout_seconds": mcp_auth_config.as_metadata_discovery_timeout_seconds,
            },
        )
    except Exception as exc:
        logger.warning(f"MCP auth discovery bridge unavailable; returning warning results: {exc}")
        return _build_discovery_bridge_unavailable_results(candidate_list)


def _build_discovery_bridge_unavailable_results(candidates: Iterable[Mapping[str, Any] | Any]) -> list[dict[str, Any]]:
    return [
        {
            "server_name": _get_discovery_candidate_field(candidate, "server_name"),
            "status": "discovery_failed",
            "failure_reason": DISCOVERY_BRIDGE_UNAVAILABLE_FAILURE_REASON,
            "error_context": {"reason": "Discovery bridge unavailable"},
        }
        for candidate in candidates
    ]


def _build_discovered_failure_payload(
    candidate: Mapping[str, Any], server_name: Any, failure_reason: str
) -> dict[str, Any]:
    return _build_discovered_config_error_payload(
        candidate,
        {
            "server_name": server_name or _candidate_string(candidate, "server_name") or "MCP server",
            "attempted_mechanisms": (),
            "failure_reasons": (failure_reason,),
            "action": _DISCOVERED_AUTH_RECOVERY_ACTION,
        },
    )


def _prepare_discovered_flow_resolution_config(
    *,
    allowed_private_networks: tuple[str, ...],
) -> _DiscoveredFlowResolutionConfig:
    deps = _deps()
    redirect_uri, _, _ = deps.build_redirect_uri()
    client_metadata_document_url = f"{config.CALLBACK_API_BASE_URL.rstrip('/')}{_CLIENT_METADATA_DOCUMENT_PATH}"
    return _DiscoveredFlowResolutionConfig(
        redirect_uri=redirect_uri,
        client_metadata_document_url=client_metadata_document_url,
        allowed_private_networks=allowed_private_networks,
        allow_local_client_metadata_document_url=deps._mcp_auth_service.config.allow_local_client_metadata_document_url,
    )


def _build_discovered_resolved_payload(
    *,
    candidate: Mapping[str, Any],
    resolution: Any,
    mcp_config_id: str,
    mcp_config_name: str,
    workflow_execution_id: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "auth_config_id": resolution.auth_config_id,
        "discovered_flow_id": resolution.discovered_flow_id,
        "mcp_config_id": mcp_config_id,
        "mcp_config_name": mcp_config_name,
        "mcp_server_name": _candidate_string(candidate, "mcp_server_name") or mcp_config_name,
        "auth_type": "oauth2",
        "as_hostname": resolution.as_hostname,
        "status": "authentication_required",
        "error_context": None,
    }
    if workflow_execution_id is None:
        payload["initiate_url"] = _build_discovered_initiate_url(resolution.discovered_flow_id)
    return payload


async def _resolve_discovered_candidate_payload(
    *,
    candidate: Mapping[str, Any],
    result: Any,
    user_id: str | None,
    session_binding_hash: str | None,
    workflow_execution_id: str | None,
    flow_config: _DiscoveredFlowResolutionConfig,
) -> dict[str, Any]:
    from codemie_enterprise.mcp_auth import (
        ClientRegistrationNetworkOptions,
        MCPAuthRedisUnavailable,
        create_discovered_flow_id,
        parse_bearer_challenge_scope,
        resolve_discovered_oauth2_flow,
    )

    deps = _deps()
    mcp_config_id = _candidate_string(candidate, "mcp_config_id")
    mcp_config_name = _candidate_string(candidate, "mcp_config_name") or _candidate_string(candidate, "server_name")
    if not user_id or not session_binding_hash or not mcp_config_id or not mcp_config_name:
        return _build_discovered_failure_payload(candidate, mcp_config_name, "discovered_flow_binding_unavailable")

    try:
        resolution = await resolve_discovered_oauth2_flow(
            discovery_result=result,
            mcp_config_id=mcp_config_id,
            mcp_config_name=mcp_config_name,
            user_id=user_id,
            session_binding_hash=session_binding_hash,
            discovered_flow_id=create_discovered_flow_id(),
            current_challenge_scope=parse_bearer_challenge_scope(candidate.get("www_authenticate_header")),
            client_metadata_document_url=flow_config.client_metadata_document_url,
            redirect_uris=(flow_config.redirect_uri,),
            dcr_credentials_cache=deps._mcp_auth_dcr_credentials_cache,
            network_options=ClientRegistrationNetworkOptions(
                allowed_private_networks=flow_config.allowed_private_networks,
                allow_local_client_metadata_document_url=flow_config.allow_local_client_metadata_document_url,
                enforce_https=deps._mcp_auth_service.config.enforce_https,
            ),
            dcr_timeout_seconds=deps._mcp_auth_service.config.dcr_registration_timeout_seconds,
            **(
                {"allow_issuer_prefix_match": bool(candidate.get("allow_issuer_prefix_match"))}
                if "allow_issuer_prefix_match" in inspect.signature(resolve_discovered_oauth2_flow).parameters
                else {}
            ),
        )
        deps._require_initialized_discovered_flow_store().store(resolution.snapshot)
    except MCPAuthRedisUnavailable as exc:
        logger.warning(f"MCP auth discovered flow handoff storage unavailable: {exc}")
        return _build_discovered_failure_payload(candidate, mcp_config_name, "discovered_flow_store_unavailable")
    except Exception as exc:
        logger.warning(f"MCP auth discovered flow payload build failed: {exc}")
        return _build_discovered_failure_payload(candidate, mcp_config_name, "discovered_flow_resolution_failed")

    if resolution.status == "config_error":
        return _build_discovered_config_error_payload(
            candidate,
            resolution.error_context or {},
            as_hostname=resolution.as_hostname,
        )

    return _build_discovered_resolved_payload(
        candidate=candidate,
        resolution=resolution,
        mcp_config_id=mcp_config_id,
        mcp_config_name=mcp_config_name,
        workflow_execution_id=workflow_execution_id,
    )


def _select_discovered_candidate_pairs(
    candidate_list: list[Mapping[str, Any]], result_list: list[Any]
) -> list[tuple[Mapping[str, Any], Any]]:
    from ._common import _get_discovery_result_field

    return [
        (candidate, result_list[i])
        for i, candidate in enumerate(candidate_list)
        if i < len(result_list) and _get_discovery_result_field(result_list[i], "status") == "discovered"
    ]


async def build_mcp_auth_discovered_auth_gate_payloads(
    *,
    discovery_candidates: Iterable[Mapping[str, Any]],
    discovery_results: Iterable[Any],
    user_id: str | None,
    session_binding_hash: str | None,
    allowed_private_networks: tuple[str, ...],
    workflow_execution_id: str | None = None,
) -> list[dict[str, Any]]:
    if not is_mcp_auth_enabled():
        return []
    discovered_pairs = _select_discovered_candidate_pairs(list(discovery_candidates), list(discovery_results))
    if not discovered_pairs:
        return []

    try:
        import codemie_enterprise.mcp_auth as _mcp_auth_bridge  # noqa: F401
    except ImportError:
        return []

    try:
        flow_config = _prepare_discovered_flow_resolution_config(
            allowed_private_networks=allowed_private_networks,
        )
    except Exception as exc:
        logger.warning(f"MCP auth discovered flow configuration unavailable: {exc}")
        return [
            _build_discovered_failure_payload(
                candidate,
                _candidate_string(candidate, "mcp_config_name") or _candidate_string(candidate, "server_name"),
                "discovered_flow_configuration_unavailable",
            )
            for candidate, _result in discovered_pairs
        ]

    payloads: list[dict[str, Any]] = []
    for candidate, result in discovered_pairs:
        payloads.append(
            await _resolve_discovered_candidate_payload(
                candidate=candidate,
                result=result,
                user_id=user_id,
                session_binding_hash=session_binding_hash,
                workflow_execution_id=workflow_execution_id,
                flow_config=flow_config,
            )
        )
    return payloads
