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

"""Enterprise package loader with import resolution and availability checks.

This module provides safe imports from the enterprise package, with graceful
fallback when the package is not installed.

CRITICAL: This loader ONLY handles imports and flags. NO business logic.
"""

from __future__ import annotations

# LangFuse imports
try:
    from codemie_enterprise.langfuse import (
        LangFuseConfig,
        LangFuseService,
        LangfuseContextManager,
        SpanContext,
        TraceContext,
        build_agent_metadata,
        build_workflow_metadata,
        make_observe,
    )

    def _get_langfuse_traces() -> bool:
        # Deferred import: codemie.configs imports codemie.enterprise at module level,
        # so importing at the top of loader.py would create a circular dependency.
        from codemie.configs import config

        return config.LANGFUSE_TRACES

    observe = make_observe(is_enabled=_get_langfuse_traces)
    HAS_LANGFUSE = True
except ImportError:
    LangFuseConfig = None  # type: ignore
    LangFuseService = None  # type: ignore
    TraceContext = None  # type: ignore
    SpanContext = None  # type: ignore
    LangfuseContextManager = None  # type: ignore
    build_workflow_metadata = None  # type: ignore
    build_agent_metadata = None  # type: ignore
    make_observe = None  # type: ignore

    def observe(*_args, **_kwargs):  # type: ignore[misc]
        """No-op observe when codemie_enterprise is not installed."""
        return lambda fn: fn

    HAS_LANGFUSE = False


def has_langfuse() -> bool:
    """Check if LangFuse enterprise feature is available."""
    return HAS_LANGFUSE


# LiteLLM imports
try:
    from codemie_enterprise.litellm import (
        BudgetTable,
        CustomerInfo,
        KeySpendingInfo,
        LiteLLMAPIClient,
        LiteLLMConfig,
        LiteLLMService,
    )
    from codemie_enterprise.litellm.proxy_utils import (
        inject_user_into_body,
        parse_usage_from_response,
    )

    HAS_LITELLM = True
except ImportError:
    BudgetTable = None  # type: ignore
    CustomerInfo = None  # type: ignore
    KeySpendingInfo = None  # type: ignore
    LiteLLMAPIClient = None  # type: ignore
    LiteLLMConfig = None  # type: ignore
    LiteLLMService = None  # type: ignore
    inject_user_into_body = None  # type: ignore
    parse_usage_from_response = None  # type: ignore
    HAS_LITELLM = False


def has_litellm() -> bool:
    """Check if LiteLLM enterprise feature is available."""
    return HAS_LITELLM


# Plugin imports
try:
    from codemie_enterprise.plugin import (
        PluginConfig,
        PluginCredentials,
        PluginService,
        PluginToolkit,
        ToolConsumer,
    )

    HAS_PLUGIN = True
except ImportError:
    PluginConfig = None  # type: ignore
    PluginCredentials = None  # type: ignore
    PluginService = None  # type: ignore
    PluginToolkit = None  # type: ignore
    ToolConsumer = None  # type: ignore
    HAS_PLUGIN = False


def has_plugin() -> bool:
    """Check if plugin enterprise feature is available."""
    return HAS_PLUGIN


# IDP imports (enterprise Keycloak/OIDC providers)
try:
    from codemie_enterprise.idp import (
        IdpConfig,
        IdpUser,
        KeycloakIdpProvider,
        OidcIdpProvider,
        get_available_providers as get_enterprise_idp_providers,
        validate_user_type as enterprise_validate_user_type,
    )

    HAS_IDP = True
except ImportError:
    IdpConfig = None  # type: ignore
    IdpUser = None  # type: ignore
    KeycloakIdpProvider = None  # type: ignore
    OidcIdpProvider = None  # type: ignore
    get_enterprise_idp_providers = None  # type: ignore
    enterprise_validate_user_type = None  # type: ignore
    HAS_IDP = False


def has_idp() -> bool:
    """Check if enterprise IDP providers are available."""
    return HAS_IDP


# Migration imports
try:
    from codemie_enterprise.migration import (
        KeycloakAdminClient,
        KeycloakAdminUser,
    )

    HAS_MIGRATION = True
except ImportError:
    KeycloakAdminClient = None  # type: ignore
    KeycloakAdminUser = None  # type: ignore
    HAS_MIGRATION = False


def has_migration() -> bool:
    """Check if migration enterprise feature is available."""
    return HAS_MIGRATION


# Export all
__all__ = [
    # LangFuse
    "HAS_LANGFUSE",
    "LangFuseConfig",
    "LangFuseService",
    "TraceContext",
    "SpanContext",
    "LangfuseContextManager",
    "build_workflow_metadata",
    "build_agent_metadata",
    "observe",
    "has_langfuse",
    # LiteLLM
    "HAS_LITELLM",
    "BudgetTable",
    "CustomerInfo",
    "KeySpendingInfo",
    "LiteLLMAPIClient",
    "LiteLLMConfig",
    "LiteLLMService",
    "inject_user_into_body",
    "parse_usage_from_response",
    "has_litellm",
    # Plugin
    "HAS_PLUGIN",
    "PluginConfig",
    "PluginCredentials",
    "PluginService",
    "PluginToolkit",
    "ToolConsumer",
    "has_plugin",
    # IDP
    "HAS_IDP",
    "IdpConfig",
    "IdpUser",
    "KeycloakIdpProvider",
    "OidcIdpProvider",
    "get_enterprise_idp_providers",
    "enterprise_validate_user_type",
    "has_idp",
    # Migration
    "HAS_MIGRATION",
    "KeycloakAdminClient",
    "KeycloakAdminUser",
    "has_migration",
]
