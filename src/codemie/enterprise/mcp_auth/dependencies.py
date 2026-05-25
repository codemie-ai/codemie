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
import sys as _sys
import re
from collections.abc import Callable
from contextlib import suppress
from importlib import import_module  # noqa: F401  (kept for test patches)
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit


from codemie.configs import config
from codemie.clients.redis import create_redis_client
from codemie.configs.logger import logger
from codemie.core.exceptions import MCPAuthenticationRequiredException
from codemie.rest_api.models.dynamic_config import ConfigValueType  # noqa: F401  (kept for test patches)
from codemie.service.dynamic_config_service import DynamicConfigService  # noqa: F401  (kept for test patches)

if TYPE_CHECKING:
    pass

from . import _state
from ._common import (  # noqa: E402, F401
    CallbackPageError,
    MCPAuthEnterpriseUnavailableError,
    MCPPostAuth401Result,
    _CleanupEnqueuer,
    _is_missing_required_value,
    _raise_client_error,
)
from ._guards import (  # noqa: E402, F401
    _require_initialized_callback_dependencies,
    _require_initialized_discovered_flow_store,
    _require_initialized_mcp_auth_components,
    _require_initialized_saml_callback_dependencies,
    _require_initialized_saml_initiate_dependencies,
    _require_initialized_tms,
    _tms_audit_context,
    get_mcp_auth_trust_policy_service,
    invalidate_mcp_auth_trust_policy_cache,
    is_mcp_auth_enabled,
)

HAS_MCP_AUTH = _state.HAS_MCP_AUTH
encryption_service = _state.encryption_service

_self: Any = _sys.modules[__name__]

from ._constants import (  # noqa: E402, F401
    DISCOVERY_BRIDGE_UNAVAILABLE_FAILURE_REASON,
    MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST,
    MCP_AUTH_DISCOVERY_PRIVATE_NETWORK_ALLOWLIST_MAX_LENGTH,
    MCP_AUTH_TRUSTED_AS_DOMAINS_KEY,
    SUPPORTED_AUTH_TYPES,
    _AUTHENTICATION_FAILED_TITLE,
    _CALLBACK_CONFIG_ERROR_MESSAGE,
    _CALLBACK_CONTACT_ADMIN_TEXT,
    _CALLBACK_CONTENT_SECURITY_POLICY,
    _CALLBACK_ERROR_CONFIGURATION,
    _CALLBACK_ERROR_CREDENTIALS_STORE_FAILED,
    _CALLBACK_ERROR_RUNTIME,
    _CALLBACK_ERROR_SESSION_EXPIRED,
    _CALLBACK_ERROR_VERIFICATION_FAILED,
    _CALLBACK_EVENT_TYPE,
    _CALLBACK_EXPIRED_MESSAGE,
    _CALLBACK_FALLBACK_DELAY_MS,
    _CALLBACK_RECOVERY_TEXT,
    _CALLBACK_REDIS_UNAVAILABLE_MESSAGE,
    _CALLBACK_RUNTIME_ERROR_MESSAGE,
    _CALLBACK_SECURITY_HEADERS,
    _CALLBACK_STATE_MAX_AGE,
    _CALLBACK_SUCCESS_CLOSE_MESSAGE,
    _CALLBACK_SUCCESS_MESSAGE,
    _CALLBACK_SUCCESS_OPEN_CODEMIE_MESSAGE,
    _CALLBACK_TMS_STORE_ERROR_MESSAGE,
    _CALLBACK_TRANSITION_MESSAGE,
    _CALLBACK_VERIFICATION_FAILURE_MESSAGE,
    _CLIENT_METADATA_CACHE_CONTROL,
    _CLIENT_METADATA_DOCUMENT_PATH,
    _DISCOVERED_AUTH_CONFIG_ID_PREFIX,
    _DISCOVERED_AUTH_RECOVERY_ACTION,
    _HTTPS_ONLY_FIELDS,
    _INSTALL_ENTERPRISE_MCP_AUTH_HELP,
    _INVALID_MCP_AUTH_CONFIG_MESSAGE,
    _INVALID_MCP_SERVER_URL_MESSAGE,
    _INVALID_OAUTH2_CONFIG_MESSAGE,
    _LOCALHOST_HOSTS,
    _MCP_AUTH_REDIS_RETRY_HELP,
    _MCP_AUTH_RETRY_AFTER_INIT_HELP,
    _MCP_AUTH_SERVICE_UNAVAILABLE_MESSAGE,
    _MCP_AUTH_TEMPORARILY_UNAVAILABLE,
    _OAUTH2_CALLBACK_PAGE_SCRIPT_PATH,
    _OAUTH2_CALLBACK_PATH,
    _POST_AUTH_401_REFRESH_FAILURE_DESCRIPTIONS,
    _REQUIRED_AUTH_FIELDS,
    _RESERVED_DISCOVERED_AUTH_CONFIG_ID_ERROR,
    _SAML_ACS_PATH,
    _SAML_HTTP_ERROR,
    _SP_METADATA_GENERATION_FAILED_MESSAGE,
    _SP_METADATA_SAML_ONLY_MESSAGE,
)


from ._uri import (  # noqa: E402, F401
    _build_callback_uri,
    _derive_resource_uri_without_enterprise,
    _describe_stored_redirect_uri,
    _format_resource_netloc,
    _get_authenticated_bearer_token_hash,
    _is_localhost_hostname,
    _normalize_default_port,
    _normalize_resource_hostname,
    _normalize_resource_path,
    _uses_https,
    build_client_metadata_document_response,
    build_redirect_uri,
    build_saml_acs_url,
    derive_resource_uri,
    ensure_client_metadata_document_available,
)


from ._trust_policy import (  # noqa: E402, F401
    _build_trust_policy_configuration_error,
    _normalize_discovery_concurrency_limit,
    _parse_private_network_allowlist,
    build_static_trust_policy_service,
    read_mcp_auth_discovery_private_network_allowlist_config,
    read_mcp_auth_discovery_private_network_allowlist_config_sync,
    read_mcp_auth_trusted_as_domains_config,
    read_mcp_auth_trusted_as_domains_config_sync,
)


from ._discovery import (  # noqa: E402, F401
    _DiscoveredFlowResolutionConfig,
    _build_discovered_failure_payload,
    _build_discovered_resolved_payload,
    _build_discovery_bridge_unavailable_results,
    _prepare_discovered_flow_resolution_config,
    _resolve_discovered_candidate_payload,
    _select_discovered_candidate_pairs,
    build_mcp_auth_discovered_auth_gate_payloads,
    run_mcp_auth_parallel_discovery_probe,
)


from ._post_auth import (  # noqa: E402, F401
    _InsufficientScopeContext,
    _PostAuth401Identity,
    _attempt_post_auth_401_refresh,
    _auth_config_client_type,
    _auth_config_has_nonblank_client_secret,
    _build_insufficient_scope_discovery_header,
    _build_post_auth_401_decision,
    _build_post_auth_401_exception,
    _build_post_auth_401_refresh_failure_result,
    _extract_insufficient_scope_context,
    _extract_post_auth_401_identity,
    _load_live_discovered_snapshot,
    _log_post_auth_401_refresh_failure,
    _log_post_auth_401_retry_rejected,
    _map_scope_recovery_decision,
    _post_auth_401_auth_type,
    _post_auth_401_identity_result,
    _post_auth_401_initiate_url,
    _public_scope_recovery_auth_config_id,
    _quote_www_authenticate_param,
    _rebuild_discovered_snapshot_from_exact_context,
    _resolve_insufficient_scope_authorization_server_metadata,
    _resolve_insufficient_scope_authorization_server_metadata_async,
    _resolve_post_auth_401_auth_config_id,
    _resolve_post_auth_401_mcp_config_name,
    _resolve_post_auth_401_refresh_identity,
    _run_coroutine_sync,
    _scope_recovery_confidential_client_secret_available,
    _validate_post_auth_401_refreshed_token,
    build_mcp_insufficient_scope_auth_exception,
    build_mcp_post_auth_401_result,
)


# Removed: All post_auth and insufficient_scope code moved to _post_auth.py
# Keep type hints below for now; they're harmless


from ._common import (  # noqa: E402, F401
    _as_hostname_from_error_context,
    _build_discovered_config_error_payload,
    _execution_context_attr,
    _get_discovery_candidate_field,
    _get_discovery_result_field,
)
from ._common import _candidate_string  # noqa: E402, F401
from ._common import _is_discovered_auth_config_id  # noqa: E402, F401
from ._common import _build_discovered_initiate_url, _build_recovery_initiate_url  # noqa: E402, F401


def is_hostname_like(value: str) -> bool:
    return value == "localhost" or bool(re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z0-9-]+", value))


def derive_saml_entity_hostname(entity_id: Any) -> str | None:
    if not isinstance(entity_id, str) or not entity_id:
        return None

    parsed_entity = urlsplit(entity_id)
    if parsed_entity.hostname:
        return parsed_entity.hostname

    if is_hostname_like(entity_id):
        return entity_id

    return None


def derive_as_hostname(auth_type: str | None, auth_config: dict[str, Any] | None) -> str | None:
    if not auth_config:
        return None

    if auth_type == "oauth2":
        return urlsplit(auth_config.get("authorization_url", "") or "").hostname

    if auth_type != "saml":
        return None

    sso_hostname = urlsplit(auth_config.get("sso_url", "") or "").hostname
    if sso_hostname:
        return sso_hostname

    return derive_saml_entity_hostname(auth_config.get("entity_id"))


def derive_initiate_url(auth_type: str | None) -> str | None:
    if auth_type == "oauth2":
        return "/v1/mcp-auth/oauth2/initiate"
    if auth_type == "saml":
        return "/v1/mcp-auth/saml/initiate"
    return None


from ._callback_pages import (  # noqa: E402, F401
    _build_callback_page,
    _build_error_callback_response,
    _build_success_callback_response,
    _build_trusted_callback_error,
    _derive_callback_target_origin,
    build_oauth2_callback_page_script_response,
)


from ._oauth2_callback import (  # noqa: E402, F401
    _build_discovered_oauth2_callback_response,
    _build_oauth2_callback_response,
    _build_recovery_oauth2_callback_response,
    _consume_callback_pkce_state,
    _decode_and_verify_oauth2_callback_state,
    _decrypt_callback_client_secret,
    _exchange_callback_code,
    _is_mcp_auth_redis_unavailable,
    _load_callback_mcp_config,
    _load_discovered_flow_snapshot_or_error,
    _load_raw_callback_oauth_config,
    _load_recovery_snapshot_or_error,
    _recovery_callback_client_secret,
    _resolve_callback_recovery_flow_id,
    _store_callback_token,
    _try_get_trusted_callback_context_from_state,
    _validate_callback_auth_config,
    _validate_callback_state_age,
    _validate_callback_state_matches_pkce,
    _validate_discovered_snapshot_context,
    _validate_recovery_snapshot_context,
    build_oauth2_callback_response,
)


from ._saml_callback import (  # noqa: E402, F401
    _build_saml_callback_response,
    _consume_saml_acs_response,
    _consume_saml_relay_state,
    _decode_and_verify_saml_callback_state,
    _handle_saml_acs_exception,
    _load_raw_callback_saml_config,
    _validate_callback_saml_auth_config,
    _validate_saml_callback_state_age,
    _validate_saml_callback_state_matches_store,
    build_saml_callback_response,
)


from ._initiate import (  # noqa: E402, F401
    _load_discovered_flow_snapshot_for_binding_or_error,
    build_discovered_auth_status_response,
    build_discovered_oauth2_initiate_response,
    build_oauth2_initiate_response,
    build_recovery_oauth2_initiate_response,
    build_saml_initiate_response,
    build_saml_metadata_response,
)


def validate_auth_config_core(raw_dict: dict[str, Any], transport: str) -> list[str]:
    auth_type = raw_dict.get("auth_type")
    required_fields = _REQUIRED_AUTH_FIELDS.get(auth_type) if isinstance(auth_type, str) else None
    if required_fields is None:
        return [f"Unsupported auth_type: {auth_type}"]

    errors: list[str] = []
    auth_config_id = raw_dict.get("id")
    if isinstance(auth_config_id, str) and auth_config_id.strip().startswith(_DISCOVERED_AUTH_CONFIG_ID_PREFIX):
        errors.append(_RESERVED_DISCOVERED_AUTH_CONFIG_ID_ERROR)

    for field_name in required_fields:
        if _is_missing_required_value(raw_dict.get(field_name)):
            errors.append(f"Required field '{field_name}' missing for auth_type '{auth_type}'")

    if auth_type == "saml" and transport == "http":
        errors.append(_SAML_HTTP_ERROR)

    for field_name in _HTTPS_ONLY_FIELDS:
        value = raw_dict.get(field_name)
        if _is_missing_required_value(value):
            continue
        if not _uses_https(value):
            errors.append(f"'{field_name}' must use HTTPS")

    return errors


def validate_auth_config_on_save(raw_dict: dict[str, Any], transport: str) -> list[str]:
    errors = validate_auth_config_core(raw_dict, transport)
    if errors or not HAS_MCP_AUTH:
        return errors

    try:
        from codemie_enterprise.mcp_auth.validation import validate_auth_config_structure
    except ImportError as exc:
        missing_name = getattr(exc, "name", "") or ""
        if missing_name == "codemie_enterprise" or missing_name.startswith("codemie_enterprise."):
            return errors
        raise

    return errors + validate_auth_config_structure(raw_dict, transport)


def decrypt_confidential_client_secret(auth_config: dict[str, Any]) -> str | None:
    if auth_config.get("auth_type") != "oauth2" or auth_config.get("client_type") != "confidential":
        return None

    client_secret = auth_config.get("client_secret")
    if _is_missing_required_value(client_secret):
        return None
    return encryption_service.decrypt(client_secret)


def has_any_credentials_for_auth_config(auth_config_id: str) -> bool:
    """Return True on bridge errors to fail closed and block ID changes."""
    if not is_mcp_auth_enabled():
        return False
    tms = _self._tms
    if tms is None:
        logger.warning(
            "Failed to check stored credentials because MCP auth TMS is not initialized; "
            f"blocking auth_config.id change for auth_config_id={auth_config_id}"
        )
        return True

    try:
        with _tms_audit_context("status_check", correlation_id=auth_config_id):
            return bool(tms.has_any_credentials(auth_config_id))
    except Exception as exc:
        logger.warning(
            "Failed to check stored credentials for "
            f"auth_config_id={auth_config_id}; blocking auth_config.id change: {type(exc).__name__}"
        )
        return True


def invalidate_credentials_for_auth_config(auth_config_id: str) -> None:
    tms = _self._tms
    if not is_mcp_auth_enabled() or tms is None:
        return

    with _tms_audit_context("admin_config_change", correlation_id=auth_config_id):
        tms.invalidate_by_config(auth_config_id)


def _validate_hmac_secret() -> None:
    if not is_mcp_auth_enabled():
        return

    from codemie.configs import config  # deferred import to avoid circular dependency

    secret_length = len(config.MCP_AUTH_HMAC_SECRET.encode("utf-8"))
    if secret_length < 32:
        raise RuntimeError(
            "MCP auth requires MCP_AUTH_HMAC_SECRET to be set to at least 32 bytes when MCP_AUTH_ENABLED=True. "
            "Configure a strong shared secret and restart the application."
        )


def _build_alert_callback() -> Callable[[], None]:
    def _alert() -> None:
        logger.warning("MCP auth Redis health degraded for this instance")

    return _alert


def _build_authentication_required_exception(
    auth_config_id: str,
    *,
    status: str = "authentication_required",  # noqa: A002
    auth_type: str | None = None,
    error_context: str | None = None,
) -> MCPAuthenticationRequiredException:
    payload: dict[str, Any] = dict(get_mcp_auth_status_payload(auth_config_id) or {"auth_config_id": auth_config_id})
    payload.update(
        {
            "status": status,
            "auth_type": auth_type,
            "error_context": error_context,
        }
    )
    return MCPAuthenticationRequiredException(payload)


def _normalize_tms_environment(environment: str) -> str:
    normalized_environment = environment.strip().lower()
    return {
        "development": "dev",
        "develop": "dev",
        "prod": "production",
        "tests": "test",
        "preview": "staging",
        "prod-preview": "staging",
    }.get(normalized_environment, normalized_environment)


def _build_token_management_system(redis_client: Any, audit_context_provider: Any) -> Any:
    from codemie.clients.postgres import PostgresClient
    from codemie.configs import config
    from codemie.service.encryption.encryption_factory import EncryptionFactory, EncryptionType
    from codemie_enterprise.mcp_auth import (
        AEADEnvelopeEncryption,
        ExternalEncryptionServiceKeyManagementProvider,
        MockTokenManagementSystem,
        PostgresTokenManagementSystem,
        RedisTMSRefreshLock,
        TMSConfig,
        TMSRuntimeEnvironment,
    )

    tms_environment = _normalize_tms_environment(config.ENV)

    if tms_environment == TMSRuntimeEnvironment.PRODUCTION and (
        not config.MCP_AUTH_TMS_ENABLED or config.MCP_AUTH_TMS_ALLOW_MOCK
    ):
        raise RuntimeError("production MCP auth requires TMS enabled")

    tms_config = TMSConfig(
        enabled=config.MCP_AUTH_TMS_ENABLED,
        environment=tms_environment,
        refresh_timeout_seconds=config.MCP_AUTH_TMS_REFRESH_TIMEOUT_SECONDS,
        redis_lock_enabled=config.MCP_AUTH_TMS_REDIS_LOCK_ENABLED,
        redis_lock_ttl_seconds=config.MCP_AUTH_TMS_REDIS_LOCK_TTL_SECONDS,
        audit_required=config.MCP_AUTH_TMS_AUDIT_REQUIRED,
        audit_fallback_enabled=config.MCP_AUTH_TMS_AUDIT_FALLBACK_ENABLED,
        audit_fallback_sink_configured=config.MCP_AUTH_TMS_AUDIT_FALLBACK_SINK_CONFIGURED,
        kms_key_id=config.MCP_AUTH_TMS_KMS_KEY_ID,
        encryption_context_prefix=config.MCP_AUTH_TMS_ENCRYPTION_CONTEXT_PREFIX,
        allow_mock_tms=config.MCP_AUTH_TMS_ALLOW_MOCK,
        audit_sanitize_diagnostics=config.MCP_AUTH_TMS_AUDIT_SANITIZE_DIAGNOSTICS,
    )

    if not tms_config.enabled:
        if not tms_config.allow_mock_tms:
            raise RuntimeError("production MCP auth requires TMS enabled or non-production mock guard")
        return MockTokenManagementSystem()

    encryption_type = EncryptionFactory.get_current_encryption_service_type()
    local_encryption_types = {
        EncryptionType.PLAIN_TEXT,
        EncryptionType.BASE64_ENCRYPTION,
    }
    if tms_config.environment == TMSRuntimeEnvironment.PRODUCTION and encryption_type in local_encryption_types:
        raise RuntimeError("Production MCP auth TMS requires a KMS-backed encryption provider")

    if encryption_type in local_encryption_types:
        from codemie_enterprise.mcp_auth.tms_crypto import LocalKeyManagementProvider

        kms_provider = LocalKeyManagementProvider(config.MCP_AUTH_HMAC_SECRET, tms_config.kms_key_id)
    else:
        kms_provider = ExternalEncryptionServiceKeyManagementProvider(
            encryption_service=EncryptionFactory.get_current_encryption_service(),
            kms_key_id=tms_config.kms_key_id,
        )

    refresh_lock = (
        RedisTMSRefreshLock(
            redis_client, tms_config.redis_lock_ttl_seconds, namespace=config.MCP_AUTH_REDIS_KEY_NAMESPACE
        )
        if tms_config.redis_lock_enabled
        else None
    )

    return PostgresTokenManagementSystem(
        config=tms_config,
        connection_factory=lambda: PostgresClient.get_engine().begin(),
        encryption=AEADEnvelopeEncryption(kms_provider=kms_provider, kms_key_id=tms_config.kms_key_id),
        audit_context_provider=audit_context_provider,
        refresh_lock=refresh_lock,
    )


async def _bridge_consumer(bridge_queue: asyncio.Queue[str], mcp_auth_service: _CleanupEnqueuer) -> None:
    while True:
        user_id = await bridge_queue.get()
        try:
            try:
                mcp_auth_service.enqueue_cleanup(user_id)
            except Exception as exc:
                logger.exception(f"MCP auth cleanup bridge failed for user_id={user_id}: {exc}")
        finally:
            bridge_queue.task_done()


def enqueue_mcp_auth_cleanup(user_id: str) -> None:
    if (
        _self._bridge_loop is None
        or _self._bridge_queue is None
        or _self._bridge_task is None
        or _self._bridge_task.done()
    ):
        logger.debug(f"Skipping MCP auth cleanup enqueue for user_id={user_id}: bridge unavailable")
        return

    try:
        _self._bridge_loop.call_soon_threadsafe(_self._bridge_queue.put_nowait, user_id)
    except RuntimeError:
        logger.debug(f"Skipping MCP auth cleanup enqueue for user_id={user_id}: bridge loop closed")


def _cleanup_partial_mcp_auth_startup(bridge_task: Any, mcp_auth_service: Any, redis_client: Any) -> None:
    if bridge_task is not None:
        try:
            bridge_task.cancel()
        except Exception as exc:
            logger.warning(f"MCP auth bridge task cancellation failed after startup error: {type(exc).__name__}")
    if mcp_auth_service is not None:
        try:
            mcp_auth_service.shutdown()
        except Exception as exc:
            logger.warning(f"MCP auth service shutdown failed after startup error: {type(exc).__name__}")
    try:
        redis_client.close()
    except Exception as exc:
        logger.warning(f"MCP auth Redis client shutdown failed after startup error: {type(exc).__name__}")


def initialize_mcp_auth() -> None:
    _validate_hmac_secret()
    if not is_mcp_auth_enabled() or _self._initialized:
        return

    from codemie.service.mcp.toolkit_service import MCPToolkitService
    from codemie_enterprise.mcp_auth import (
        AuthorizationServerTrustPolicyService,
        DCRCredentialsCache,
        ContextVarTMSAuditContextProvider,
        DiscoveryMetadataCache,
        MCPAuthResolver,
        MCPAuthService,
        MCPAuthServiceConfig,
        RedisEncryption,
        RedisDiscoveredFlowStore,
        RedisPKCEStore,
        SAMLRelayStateStore,
    )

    bridge_loop = asyncio.get_running_loop()
    bridge_queue: asyncio.Queue[str] = asyncio.Queue()
    redis_client = create_redis_client()
    bridge_task: Any = None
    mcp_auth_service: Any = None

    try:
        redis_key_namespace = config.MCP_AUTH_REDIS_KEY_NAMESPACE
        redis_encryption = RedisEncryption(config.MCP_AUTH_HMAC_SECRET)
        pkce_store = RedisPKCEStore(redis_client, redis_encryption, namespace=redis_key_namespace)
        saml_relay_state_store = SAMLRelayStateStore(redis_client, redis_encryption, namespace=redis_key_namespace)
        audit_context_provider = ContextVarTMSAuditContextProvider()
        token_management_system = _build_token_management_system(redis_client, audit_context_provider)
        discovery_cache = DiscoveryMetadataCache(redis_client, namespace=redis_key_namespace)
        dcr_credentials_cache = DCRCredentialsCache(redis_client, redis_encryption, namespace=redis_key_namespace)
        discovered_flow_store = RedisDiscoveredFlowStore(redis_client, redis_encryption, namespace=redis_key_namespace)
        mcp_auth_service = MCPAuthService(
            config=MCPAuthServiceConfig(
                redis_key_namespace=redis_key_namespace,
                enforce_https=config.MCP_AUTH_ENFORCE_HTTPS,
                allow_local_client_metadata_document_url=config.MCP_AUTH_ALLOW_LOCAL_CLIENT_METADATA_URL,
                as_metadata_discovery_timeout_seconds=config.MCP_AUTH_AS_METADATA_DISCOVERY_TIMEOUT_SECONDS,
                dcr_registration_timeout_seconds=config.MCP_AUTH_DCR_REGISTRATION_TIMEOUT_SECONDS,
                discovery_probe_overall_timeout_seconds=config.MCP_AUTH_DISCOVERY_PROBE_OVERALL_TIMEOUT_SECONDS,
                resource_metadata_discovery_timeout_seconds=config.MCP_AUTH_RESOURCE_METADATA_DISCOVERY_TIMEOUT_SECONDS,
            ),
            redis_client=redis_client,
            pkce_store=pkce_store,
            discovery_cache=discovery_cache,
            dcr_credentials_cache=dcr_credentials_cache,
            token_management_system=token_management_system,
            alert_callback=_build_alert_callback(),
            audit_context_provider=audit_context_provider,
        )
        trust_policy_service = AuthorizationServerTrustPolicyService(
            config_reader=read_mcp_auth_trusted_as_domains_config
        )
        mcp_auth_service.initialize()
        bridge_task = bridge_loop.create_task(_bridge_consumer(bridge_queue, mcp_auth_service))

        try:
            resolver = MCPAuthResolver(
                token_management_system,
                _build_authentication_required_exception,
                audit_context_provider=audit_context_provider,
                discovery_cache=discovery_cache,
                discovered_flow_store=discovered_flow_store,
            )
        except TypeError:
            resolver = MCPAuthResolver(
                token_management_system,
                _build_authentication_required_exception,
                audit_context_provider=audit_context_provider,
            )
        if type(resolver) not in _self._registered_resolver_types:
            MCPToolkitService.register_auth_resolver(resolver)
            _self._registered_resolver_types.add(type(resolver))
    except Exception:
        _cleanup_partial_mcp_auth_startup(bridge_task, mcp_auth_service, redis_client)
        raise

    _self._bridge_loop = bridge_loop
    _self._bridge_queue = bridge_queue
    _self._redis_client = redis_client
    _self._mcp_auth_service = mcp_auth_service
    _self._mcp_auth_trust_policy_service = trust_policy_service
    _self._mcp_auth_discovery_cache = discovery_cache
    _self._mcp_auth_dcr_credentials_cache = dcr_credentials_cache
    _self._mcp_auth_discovered_flow_store = discovered_flow_store
    _self._bridge_task = bridge_task
    _self._tms = token_management_system
    _self._pkce_store = pkce_store
    _self._saml_relay_state_store = saml_relay_state_store
    _self._redis_encryption = redis_encryption
    _self._tms_audit_context_provider = audit_context_provider

    from codemie.service.security.token_exchange_service import TokenExchangeService
    from codemie.service.security.oidc_token_exchange_service import OIDCTokenExchangeService

    TokenExchangeService.set_tms(token_management_system, audit_context_provider)
    OIDCTokenExchangeService.set_tms(token_management_system, audit_context_provider)

    _self._initialized = True


async def shutdown_mcp_auth() -> None:
    if (
        not _self._initialized
        and _self._bridge_task is None
        and _self._mcp_auth_service is None
        and _self._redis_client is None
    ):
        return

    try:
        bridge_task = _self._bridge_task
        if bridge_task is not None:
            try:
                bridge_task.cancel()
            except Exception as exc:
                logger.warning(f"MCP auth bridge task cancellation failed: {type(exc).__name__}")
            else:
                with suppress(asyncio.CancelledError):
                    try:
                        await bridge_task
                    except Exception as exc:
                        logger.warning(f"MCP auth bridge task shutdown failed: {type(exc).__name__}")

        mcp_auth_service = _self._mcp_auth_service
        if mcp_auth_service is not None:
            try:
                mcp_auth_service.shutdown()
            except Exception as exc:
                logger.warning(f"MCP auth service shutdown failed: {type(exc).__name__}")

        redis_client = _self._redis_client
        if redis_client is not None:
            try:
                redis_client.close()
            except Exception as exc:
                logger.warning(f"MCP auth Redis client shutdown failed: {type(exc).__name__}")
    finally:
        _self._initialized = False
        _self._bridge_queue = None
        _self._bridge_task = None
        _self._bridge_loop = None
        _self._mcp_auth_service = None
        _self._mcp_auth_trust_policy_service = None
        _self._mcp_auth_discovery_cache = None
        _self._mcp_auth_dcr_credentials_cache = None
        _self._mcp_auth_discovered_flow_store = None
        _self._redis_client = None
        _self._tms = None
        _self._pkce_store = None
        _self._saml_relay_state_store = None
        _self._redis_encryption = None
        _self._tms_audit_context_provider = None

        from codemie.service.security.token_exchange_service import TokenExchangeService
        from codemie.service.security.oidc_token_exchange_service import OIDCTokenExchangeService

        TokenExchangeService.clear_tms()
        OIDCTokenExchangeService.clear_tms()

        _self._registered_resolver_types.clear()


def get_mcp_auth_status_payload(auth_config_id: str) -> dict[str, Any] | None:
    from codemie.rest_api.models.mcp_config import MCPConfig

    mcp_config = MCPConfig.get_by_auth_config_id(auth_config_id)
    if mcp_config is None:
        return None

    return {
        "auth_config_id": auth_config_id,
        "mcp_config_id": mcp_config.id,
        "mcp_server_name": mcp_config.name,
    }


def __getattr__(name: str) -> object:
    if hasattr(_state, name):
        return getattr(_state, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
