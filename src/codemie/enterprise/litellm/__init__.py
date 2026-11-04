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

# Service management (from dependencies.py)
from .dependencies import (
    is_litellm_enabled,
    initialize_litellm_from_config,
    get_global_litellm_service,
    set_global_litellm_service,
    get_litellm_service_or_none,
    require_litellm_enabled,
    ensure_predefined_budgets,
    sync_budgets_from_litellm,
    backfill_user_budget_assignments,
    backfill_project_budget_assignments_from_settings,
    get_category_budget_id,
    check_user_budget,
    get_customer_spending,
    get_available_models,
    is_proxy_budget_enabled,
    get_proxy_customer_spending,
    get_proxy_username,
    is_premium_models_enabled,
    is_premium_model,
    get_premium_username,
    get_premium_customer_spending,
)

# HTTP client (from client.py)
from .client import (
    get_llm_proxy_client,
    close_llm_proxy_client,
)

# Model mapping (from models.py)
from .models import (
    map_litellm_to_llm_model,
    get_user_allowed_models,
)

# Credentials (from credentials.py)
from .credentials import (
    ResolvedLiteLLMUserCredentials,
    clear_litellm_user_credentials_cache,
    get_litellm_credentials_for_user,
    resolve_litellm_user_credentials,
)

# LLM Factory (from llm_factory.py)
from .llm_factory import (
    create_litellm_chat_model,
    create_litellm_embedding_model,
    generate_litellm_headers_from_context,
    get_litellm_chat_model,
    get_litellm_embedding_model,
)

# STT client (from stt_client.py)
from .stt_client import get_litellm_stt_client

# Proxy Router (from proxy_router.py)
from .proxy_router import proxy_router, register_proxy_endpoints

# Budget helpers (from budget_helpers.py)
from .budget_helpers import (
    create_budget_in_litellm,
    get_budget_reset_at,
    list_budgets_from_litellm,
    reset_customer_spending_in_litellm,
    update_budget_in_litellm,
    update_customer_budget_in_litellm,
)

__all__ = [
    # Service management
    "is_litellm_enabled",
    "initialize_litellm_from_config",
    "get_global_litellm_service",
    "set_global_litellm_service",
    "get_litellm_service_or_none",
    "require_litellm_enabled",
    # Business logic
    "ensure_predefined_budgets",
    "sync_budgets_from_litellm",
    "backfill_user_budget_assignments",
    "backfill_project_budget_assignments_from_settings",
    "get_category_budget_id",
    "check_user_budget",
    "get_customer_spending",
    "get_available_models",
    "is_proxy_budget_enabled",
    "get_proxy_customer_spending",
    "get_proxy_username",
    # Premium models budget
    "is_premium_models_enabled",
    "is_premium_model",
    "get_premium_username",
    "get_premium_customer_spending",
    # HTTP client
    "get_llm_proxy_client",
    "close_llm_proxy_client",
    # Model mapping
    "map_litellm_to_llm_model",
    "get_user_allowed_models",
    # Credentials
    "ResolvedLiteLLMUserCredentials",
    "clear_litellm_user_credentials_cache",
    "get_litellm_credentials_for_user",
    "resolve_litellm_user_credentials",
    # LLM Factory
    "create_litellm_chat_model",
    "create_litellm_embedding_model",
    "generate_litellm_headers_from_context",
    "get_litellm_chat_model",
    "get_litellm_embedding_model",
    "get_litellm_stt_client",
    # Proxy Router
    "proxy_router",
    "register_proxy_endpoints",
    # Budget helpers
    "create_budget_in_litellm",
    "get_budget_reset_at",
    "list_budgets_from_litellm",
    "reset_customer_spending_in_litellm",
    "update_budget_in_litellm",
    "update_customer_budget_in_litellm",
]
