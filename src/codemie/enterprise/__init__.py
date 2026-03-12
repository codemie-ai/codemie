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

"""Enterprise integration layer - imports from enterprise package"""

from __future__ import annotations

from .loader import (
    HAS_IDP,
    HAS_LANGFUSE,
    HAS_LITELLM,
    HAS_PLUGIN,
    LangFuseConfig,
    LangFuseService,
    LangfuseContextManager,
    LiteLLMAPIClient,
    LiteLLMConfig,
    LiteLLMService,
    PluginConfig,
    PluginCredentials,
    PluginService,
    PluginToolkit,
    ToolConsumer,
    BudgetTable,
    CustomerInfo,
    KeySpendingInfo,
    SpanContext,
    TraceContext,
    build_agent_metadata,
    build_workflow_metadata,
    has_idp,
    has_langfuse,
    has_litellm,
    has_plugin,
)

# LangFuse integration
from .langfuse import (
    build_agent_metadata_with_workflow_context,
    clear_workflow_trace_context,
    create_workflow_trace_context,
    get_global_langfuse_service,
    get_langfuse_callback_handler,
    get_langfuse_client_or_none,
    get_langfuse_service,
    get_workflow_trace_context,
    initialize_langfuse_from_config,
    is_langfuse_enabled,
    require_langfuse_client,
    set_global_langfuse_service,
)

# LiteLLM integration
from .litellm import (
    check_user_budget,
    close_llm_proxy_client,
    create_litellm_chat_model,
    create_litellm_embedding_model,
    ensure_litellm_default_budget,
    generate_litellm_headers_from_context,
    get_available_models,
    get_customer_spending,
    get_global_litellm_service,
    get_key_spending_info,
    get_litellm_chat_model,
    get_litellm_credentials_for_user,
    get_litellm_embedding_model,
    get_litellm_service_or_none,
    get_llm_proxy_client,
    get_user_allowed_models,
    initialize_litellm_from_config,
    is_litellm_enabled,
    map_litellm_to_llm_model,
    proxy_router,
    register_proxy_endpoints,
    require_litellm_enabled,
    set_global_litellm_service,
)

# Plugin integration
from .plugin import (
    get_global_plugin_service,
    get_plugin_service_or_none,
    get_plugin_tools_for_assistant,
    initialize_plugin_from_config,
    is_plugin_enabled,
    set_global_plugin_service,
)

# IDP integration
from .idp import (
    is_enterprise_idp_available,
    register_enterprise_idps,
)

__all__ = [
    # Constants
    "HAS_IDP",
    "HAS_LANGFUSE",
    "HAS_LITELLM",
    "HAS_PLUGIN",
    # Classes and Types
    "BudgetTable",
    "CustomerInfo",
    "KeySpendingInfo",
    "LangFuseConfig",
    "LangFuseService",
    "LangfuseContextManager",
    "LiteLLMAPIClient",
    "LiteLLMConfig",
    "LiteLLMService",
    "PluginConfig",
    "PluginCredentials",
    "PluginService",
    "PluginToolkit",
    "ToolConsumer",
    "SpanContext",
    "TraceContext",
    # Loader functions
    "build_agent_metadata",
    "build_workflow_metadata",
    "has_langfuse",
    "has_litellm",
    "has_plugin",
    # LangFuse functions
    "build_agent_metadata_with_workflow_context",
    "clear_workflow_trace_context",
    "create_workflow_trace_context",
    "get_global_langfuse_service",
    "get_langfuse_callback_handler",
    "get_langfuse_client_or_none",
    "get_langfuse_service",
    "get_workflow_trace_context",
    "initialize_langfuse_from_config",
    "is_langfuse_enabled",
    "require_langfuse_client",
    "set_global_langfuse_service",
    # LiteLLM functions
    "check_user_budget",
    "close_llm_proxy_client",
    "create_litellm_chat_model",
    "create_litellm_embedding_model",
    "ensure_litellm_default_budget",
    "generate_litellm_headers_from_context",
    "get_available_models",
    "get_customer_spending",
    "get_global_litellm_service",
    "get_key_spending_info",
    "get_litellm_chat_model",
    "get_litellm_credentials_for_user",
    "get_litellm_embedding_model",
    "get_litellm_service_or_none",
    "get_llm_proxy_client",
    "get_user_allowed_models",
    "initialize_litellm_from_config",
    "is_litellm_enabled",
    "map_litellm_to_llm_model",
    "proxy_router",
    "register_proxy_endpoints",
    "require_litellm_enabled",
    "set_global_litellm_service",
    # Plugin functions
    "get_global_plugin_service",
    "get_plugin_service_or_none",
    "get_plugin_tools_for_assistant",
    "initialize_plugin_from_config",
    "is_plugin_enabled",
    "set_global_plugin_service",
    # IDP functions
    "has_idp",
    "is_enterprise_idp_available",
    "register_enterprise_idps",
]
