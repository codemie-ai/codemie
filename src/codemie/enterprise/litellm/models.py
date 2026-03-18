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

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from codemie.configs.llm_config import LLMModel


class UserKeysSpending(BaseModel):
    """Pydantic model for user keys spending data grouped by type.

    Used by get_user_keys_spending() to return spending data for virtual keys
    grouped by USER-scoped and PROJECT-scoped keys.

    Attributes:
        user_keys: List of spending dictionaries for USER-scoped keys
        project_keys: List of spending dictionaries for PROJECT-scoped keys
    """

    user_keys: list[dict[str, Any]]
    project_keys: list[dict[str, Any]]


def map_litellm_to_llm_model(litellm_model: dict[str, Any]) -> "LLMModel":
    """
    Map LiteLLM model info dict to core LLMModel dataclass.

    This function stays in core because it depends on core types
    (LLMModel, LLMProvider, LLMFeatures, etc.) that enterprise cannot import.

    Used by get_available_models() for model mapping.

    Args:
        litellm_model: Model info dict from LiteLLM API

    Returns:
        LLMModel instance with all core type mappings

    Usage:
        from codemie.enterprise.litellm import map_litellm_to_llm_model

        llm_model = map_litellm_to_llm_model(raw_model_from_api)
    """
    from codemie.configs.llm_config import (
        CostConfig,
        LLMFeatures,
        LLMModel,
        LLMProvider,
        ModelCategory,
    )

    model_name = litellm_model.get("model_name", "")
    model_info = litellm_model.get("model_info", {})
    litellm_params = litellm_model.get("litellm_params", {})

    # Map provider string to LLMProvider enum (core type)
    provider_str = model_info.get("litellm_provider", "")
    provider_map = {
        "openai": LLMProvider.AZURE_OPENAI,
        "azure": LLMProvider.AZURE_OPENAI,
        "bedrock": LLMProvider.AWS_BEDROCK,
        "vertex_ai": LLMProvider.GOOGLE_VERTEX_AI,
        "anthropic": LLMProvider.ANTHROPIC,
        LLMProvider.VERTEX_AI_ANTHROPIC.value: LLMProvider.VERTEX_AI_ANTHROPIC,
    }
    provider = provider_map.get(provider_str, LLMProvider.AZURE_OPENAI)

    streaming = model_info.get("supports_native_streaming") is not False
    top_p_enabled = model_info.get("top_p") is not False

    # Extract supported OpenAI params for feature detection
    supported_params = model_info.get("supported_openai_params", [])

    # Build LLMFeatures (core type)
    features = LLMFeatures(
        streaming=streaming,
        tools=model_info.get("supports_function_calling", False),
        system_prompt=model_info.get("supports_system_messages", True),
        parallel_tool_calls="parallel_tool_calls" in supported_params,
        temperature="temperature" in supported_params,
        max_tokens="max_tokens" in supported_params or "max_completion_tokens" in supported_params,
        top_p=top_p_enabled,
    )

    # Extract cost information
    cost = None
    input_cost = model_info.get("input_cost_per_token")
    output_cost = model_info.get("output_cost_per_token")
    cache_read_cost = model_info.get("cache_read_input_token_cost")
    cache_creation_cost = model_info.get("cache_creation_input_token_cost")

    if input_cost is not None and output_cost is not None:
        cost = CostConfig(
            input=input_cost,
            output=output_cost,
            cache_read_input_token_cost=cache_read_cost,
            cache_creation_input_token_cost=cache_creation_cost,
        )

    # Extract default categories (core type)
    default_categories_raw = model_info.get("default_for_categories", [])
    default_for_categories = []

    for category in default_categories_raw:
        try:
            default_for_categories.append(ModelCategory(category))
        except ValueError:
            from codemie.configs import logger

            logger.warning(f"Unknown category '{category}' for model {model_name}")

    # Extract model properties
    multimodal = model_info.get("supports_vision", False)
    enabled = model_info.get("enabled", True) is True
    react_agent = not model_info.get("supports_function_calling", False)
    label = model_info.get("label", model_info.get("id", model_name))
    forbidden_for_web = model_info.get("forbidden_for_web", False)
    api_version = litellm_params.get("api_version", None)

    # Return core LLMModel instance
    return LLMModel(
        base_name=model_name,
        deployment_name=model_name,
        label=label,
        multimodal=multimodal,
        react_agent=react_agent,
        enabled=enabled,
        provider=provider,
        features=features,
        default_for_categories=default_for_categories,
        cost=cost,
        default=ModelCategory.GLOBAL in default_for_categories if default_for_categories else False,
        forbidden_for_web=forbidden_for_web,
        api_version=api_version,
    )


def get_user_allowed_models(user_id: str, user_applications: list[str]):
    """
    Get models allowed for external user based on their LiteLLM integration.

    This function combines:
    - get_litellm_credentials_for_user (depends on core SettingsService)
    - Enterprise service for fetching models
    - Core mapping for LLMModel conversion

    Checks user-level LiteLLM credentials across all user applications.
    Fetches models from LiteLLM and maps them to LLMModel objects.

    Args:
        user_id: User ID
        user_applications: List of applications the user has access to

    Returns:
        LiteLLMModels containing chat and embedding models, or None if no integration found

    Usage:
        from codemie.enterprise.litellm import get_user_allowed_models

        models = get_user_allowed_models(user.id, user.project_names)
        if models:
            chat_models = models.chat_models
            embedding_models = models.embedding_models
    """
    from codemie.configs import logger
    from codemie.configs.llm_config import LiteLLMModels, ModelType

    # Import from same module to avoid circular imports
    from .credentials import get_litellm_credentials_for_user
    from .dependencies import get_litellm_service_or_none

    # Get LiteLLM credentials for user (core-dependent)
    litellm_credentials = get_litellm_credentials_for_user(user_id, user_applications)

    if not litellm_credentials:
        logger.info(f"No LiteLLM integration for user {user_id}")
        return None

    # Get enterprise service
    litellm = get_litellm_service_or_none()
    if litellm is None:
        logger.warning("LiteLLM enterprise service not available")
        return None

    # Fetch raw models using user-specific credentials (from enterprise)
    raw_models = litellm.get_available_models(
        user_id=user_id,
        api_key=litellm_credentials.api_key,
    )

    if not raw_models:
        logger.info(f"No models available for user {user_id}")
        return None

    # Map to LLMModel objects and deduplicate (core-dependent)
    chat_models = {}
    embedding_models = {}

    for litellm_model in raw_models:
        try:
            llm_model = map_litellm_to_llm_model(litellm_model)
            if not llm_model.enabled:
                continue

            mode_str = litellm_model.get("model_info", {}).get("mode", ModelType.CHAT.value)

            try:
                model_type = ModelType(mode_str)
            except ValueError:
                logger.warning(f"Unknown model mode '{mode_str}', defaulting to CHAT")
                model_type = ModelType.CHAT

            if model_type == ModelType.EMBEDDING:
                embedding_models[llm_model.base_name] = llm_model
            else:
                chat_models[llm_model.base_name] = llm_model

        except Exception as e:
            logger.error(f"Error mapping model {litellm_model.get('model_name')}: {e}")
            continue

    litellm_models = LiteLLMModels(
        chat_models=list(chat_models.values()),
        embedding_models=list(embedding_models.values()),
    )

    if not litellm_models.chat_models:
        logger.info(f"No chat models available for user {user_id}")
        return None

    logger.info(
        f"Fetched {len(litellm_models.chat_models)} chat models and "
        f"{len(litellm_models.embedding_models)} embedding models for user {user_id}"
    )

    return litellm_models
