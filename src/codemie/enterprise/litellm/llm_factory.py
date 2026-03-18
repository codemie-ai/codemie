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

import json
import logging
from typing import TYPE_CHECKING, Any, Literal, Optional, TypeVar

from langchain_openai import AzureChatOpenAI
from langchain_core.language_models import LanguageModelInput
from langchain_core.runnables import Runnable
from pydantic import BaseModel

if TYPE_CHECKING:
    from codemie.configs.llm_config import LLMModel
    from codemie.rest_api.models.settings import LiteLLMContext, LiteLLMCredentials
    from langchain_openai import AzureOpenAIEmbeddings

logger = logging.getLogger(__name__)

_BM = TypeVar("_BM", bound=BaseModel)
_DictOrPydanticClass = dict[str, Any] | type[_BM]
_DictOrPydantic = dict | _BM


class LiteLLMChatOpenAI(AzureChatOpenAI):
    """
    Extended AzureChatOpenAI with intelligent structured output method selection.

    This class inherits from AzureChatOpenAI and overrides with_structured_output
    to automatically select the correct method based on the model provider and type:
    - Vertex AI Claude models: Use 'function_calling' method
    - Azure OpenAI models: Use default 'json_schema' method

    This eliminates the need for conditional logic in calling code.

    Attributes:
        llm_model_details: Model configuration for determining provider/type (not a Pydantic field)
    """

    def __init__(self, llm_model_details: "LLMModel", **kwargs: Any):
        """
        Initialize LiteLLMChatOpenAI.

        Args:
            llm_model_details: Model configuration details for determining provider/type
            **kwargs: Keyword arguments passed to AzureChatOpenAI parent class
        """
        # Initialize parent AzureChatOpenAI class first
        super().__init__(**kwargs)

        # Store llm_model_details as a regular attribute (not a Pydantic field)
        # Use object.__setattr__ to bypass Pydantic's __setattr__
        object.__setattr__(self, 'llm_model_details', llm_model_details)

    def with_structured_output(
        self,
        schema: Optional[_DictOrPydanticClass] = None,
        *,
        method: Literal["function_calling", "json_mode", "json_schema"] = "json_schema",
        include_raw: bool = False,
        strict: Optional[bool] = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, _DictOrPydantic]:
        """
        Override with_structured_output to automatically select the correct method.

        Determines the appropriate method based on model provider and type:
        - Vertex AI Claude: Uses 'function_calling' for better compatibility
        - Others: Uses the provided method (default 'json_schema')

        Args:
            schema: Pydantic model or dict defining the output structure
            method: Structured output method (overridden for Vertex Claude)
            include_raw: Whether to include raw response
            strict: Whether to use strict mode
            **kwargs: Additional arguments

        Returns:
            Runnable that produces structured output
        """
        from codemie.configs.llm_config import LLMProvider

        # Determine if we should use function_calling method
        is_vertex_claude = self.llm_model_details.provider == LLMProvider.VERTEX_AI_ANTHROPIC

        # Override method for Vertex Claude models
        if is_vertex_claude:
            logger.debug(
                f"Using function_calling method for Vertex AI Claude model: {self.llm_model_details.base_name}"
            )
            method = "function_calling"
        else:
            logger.debug(f"Using {method} method for model: {self.llm_model_details.base_name}")

        # Call parent's with_structured_output with the selected method
        return super().with_structured_output(schema, method=method, include_raw=include_raw, strict=strict, **kwargs)


def create_litellm_chat_model(
    llm_model_details: "LLMModel",
    litellm_context: Optional["LiteLLMContext"],
    user_email: Optional[str],
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    streaming: bool = True,
) -> AzureChatOpenAI:
    """
    Create LiteLLM chat model instance (enterprise business logic).

    This function creates an AzureChatOpenAI instance configured to connect
    to LiteLLM proxy. It handles:
    - Credentials from context (user-specific) or app key
    - Budget checking for non-credentialed users
    - Header generation (x-litellm-tags, etc.)
    - Model configuration

    Args:
        llm_model_details: LLM model configuration from core
        litellm_context: Optional LiteLLM context with credentials and project info
        user_email: User email for budget tracking
        temperature: Optional temperature override
        top_p: Optional top_p override
        streaming: Enable streaming responses

    Returns:
        AzureChatOpenAI instance configured for LiteLLM proxy

    Raises:
        Exception: If model creation fails
    """

    # Import config dynamically to avoid circular import
    # Config is okay to import since it's just data, not business logic
    from codemie.configs import config

    logger.debug(f"Creating LiteLLM chat model: {llm_model_details.base_name}")

    # Extract credentials from context
    creds: Optional["LiteLLMCredentials"] = litellm_context.credentials if litellm_context else None

    # Generate merged headers (business logic)
    merged_headers = _generate_litellm_headers(llm_model_details, litellm_context)

    # If the key is empty, set placeholder value
    # (empty string would cause LangChain to read from environment)
    if creds and not creds.api_key:
        creds.api_key = "empty_key"

    # Build base arguments for model
    base_args = {
        'azure_endpoint': config.LITE_LLM_URL,
        'openai_api_version': llm_model_details.api_version or config.OPENAI_API_VERSION,
        'openai_api_key': creds.api_key if creds else config.LITE_LLM_APP_KEY,
        'openai_api_type': config.OPENAI_API_TYPE,
        'deployment_name': llm_model_details.base_name,
        'model_name': llm_model_details.base_name,
        'streaming': streaming,
        'max_retries': config.AZURE_OPENAI_MAX_RETRIES,
        'temperature': temperature,
        'top_p': top_p,
    }

    # Handle budget checking for app key users (no credentials)
    if creds:
        logger.debug(f"Using own credentials for LiteLLM by user: {user_email}")
    else:
        # Check budget for non-credentialed users
        from .dependencies import check_user_budget

        check_user_budget(user_id=user_email)
        base_args["model_kwargs"] = {"user": user_email}

    # Enable streaming usage tracking
    if streaming:
        base_args['stream_usage'] = True

    # Add custom headers
    if merged_headers:
        _format_headers_for_openai_api(merged_headers)
        base_args['default_headers'] = merged_headers

    # Filter parameters based on model features
    filtered_args = _filter_litellm_params(base_args, llm_model_details)

    # Return LiteLLMChatOpenAI instance with intelligent structured output method selection
    return LiteLLMChatOpenAI(llm_model_details=llm_model_details, **filtered_args)


def create_litellm_embedding_model(
    embedding_model: str,
    llm_model_details: "LLMModel",
    litellm_context: Optional["LiteLLMContext"],
    user_email: Optional[str],
) -> "AzureOpenAIEmbeddings":
    """
    Create LiteLLM embedding model instance (enterprise business logic).

    This function creates an AzureOpenAIEmbeddings instance configured for
    LiteLLM proxy embeddings endpoint.

    Args:
        embedding_model: Model name for embeddings
        llm_model_details: Model configuration details
        litellm_context: Optional LiteLLM context with credentials
        user_email: User email for budget tracking

    Returns:
        AzureOpenAIEmbeddings instance configured for LiteLLM proxy
    """
    from langchain_openai import AzureOpenAIEmbeddings
    from codemie.configs import config
    from codemie.service.llm_service.llm_service import LLMService

    logger.debug(f"Creating LiteLLM embedding model: {embedding_model}")

    # Extract credentials
    creds: Optional["LiteLLMCredentials"] = litellm_context.credentials if litellm_context else None

    # Generate headers
    merged_headers = _generate_litellm_headers(llm_model_details, litellm_context)

    # Build embedding parameters
    embedding_params = {
        'openai_api_key': creds.api_key if creds and creds.api_key else config.LITE_LLM_APP_KEY,
        "azure_endpoint": config.LITE_LLM_URL,
        "deployment": embedding_model,
        "model": embedding_model,
        "tiktoken_model_name": LLMService.BASE_NAME_GPT_41_MINI,
        "openai_api_type": config.OPENAI_API_TYPE,
        "api_version": config.OPENAI_API_VERSION,
        "max_retries": 10,
        "show_progress_bar": True,
        "check_embedding_ctx_length": False,
    }

    # Handle budget checking
    if creds:
        logger.debug(f"Using own credentials for LiteLLM by user: {user_email}")
    else:
        from .dependencies import check_user_budget

        check_user_budget(user_id=user_email)
        embedding_params["model_kwargs"] = {"user": user_email}

    # Add headers
    if merged_headers:
        embedding_params['default_headers'] = merged_headers

    return AzureOpenAIEmbeddings(**embedding_params)


def generate_litellm_headers_from_context(
    litellm_context: Optional["LiteLLMContext"],
) -> dict[str, str]:
    """
    Generate LiteLLM context-based headers (public API for routers/services).

    Generates x-litellm-tags header based on current project in context.
    This is the public API for code that needs to add LiteLLM headers to requests.

    Args:
        litellm_context: LiteLLM context with current project info

    Returns:
        Dictionary of headers to include in requests (always includes x-litellm-tags)
    """
    from codemie.configs import config

    headers = {}

    # Determine tag value based on context
    tag_value = config.LITE_LLM_TAGS_HEADER_VALUE  # default

    if litellm_context and litellm_context.current_project:
        # Parse allowed projects from config
        allowed_projects = [
            project.strip() for project in config.LITE_LLM_PROJECTS_TO_TAGS_LIST.split(",") if project.strip()
        ]

        # Use project name if it's in allowed list
        if allowed_projects and litellm_context.current_project in allowed_projects:
            tag_value = litellm_context.current_project

    # Always add the header (either project name or default)
    headers["x-litellm-tags"] = tag_value

    return headers


def _generate_litellm_headers(
    llm_model_details: "LLMModel",
    litellm_context: Optional["LiteLLMContext"],
) -> dict[str, str]:
    """
    Generate LiteLLM-specific headers (internal - for model creation).

    Handles:
    - Static model configuration headers
    - Dynamic x-litellm-tags header based on project
    - Project-to-tags mapping

    Args:
        llm_model_details: Model configuration with optional static headers
        litellm_context: LiteLLM context with current project info

    Returns:
        Dictionary of headers to include in requests
    """
    merged_headers = {}

    # Add static headers from model configuration
    if llm_model_details.configuration and llm_model_details.configuration.client_headers:
        merged_headers.update(llm_model_details.configuration.client_headers)

    # Add context-based headers (x-litellm-tags)
    context_headers = generate_litellm_headers_from_context(litellm_context)
    if context_headers:
        merged_headers.update(context_headers)

    return merged_headers


def _format_headers_for_openai_api(default_headers: dict[str, Any]) -> None:
    """
    Format headers for OpenAI API (in-place modification).

    Special handling for anthropic_beta header which needs JSON encoding.

    Args:
        default_headers: Headers dictionary to modify in-place
    """
    for key in default_headers:
        if key == "anthropic_beta":
            default_headers[key] = json.dumps(default_headers[key])


def _filter_litellm_params(
    base_args: dict[str, Any],
    llm_model_details: "LLMModel",
) -> dict[str, Any]:
    """
    Filter LiteLLM model parameters based on model features.

    Some models don't support certain parameters (temperature, streaming, etc.).
    This function removes or adjusts parameters based on model capabilities.

    Args:
        base_args: Base model arguments
        llm_model_details: Model details with feature flags

    Returns:
        Filtered arguments dictionary
    """
    filtered = base_args.copy()

    # Filter based on model features
    if llm_model_details.features.temperature is False:
        filtered['temperature'] = 1

    if llm_model_details.features.parallel_tool_calls is False:
        filtered['disabled_params'] = {"parallel_tool_calls": None}

    if llm_model_details.features.streaming is False:
        filtered['streaming'] = False
        filtered['disable_streaming'] = True
        if 'stream_usage' in filtered:
            filtered.pop('stream_usage')

    if llm_model_details.features.max_tokens is False:
        filtered['max_tokens'] = None

    if llm_model_details.features.top_p is False:
        filtered['top_p'] = None

    return filtered


def get_litellm_chat_model(
    llm_model_details: "LLMModel",
    litellm_context: Optional["LiteLLMContext"],
    user_email: Optional[str],
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    streaming: bool = True,
) -> Optional["AzureChatOpenAI"]:
    """
    Get LiteLLM chat model if enterprise is enabled AND proxy mode is lite_llm.

    This is the public wrapper called from core. It provides graceful degradation:
    - Returns None if LiteLLM not enabled (core falls back to internal providers)
    - Returns None if LLM_PROXY_MODE is not "lite_llm" (use internal proxy instead)
    - Returns None on error (graceful degradation)
    - Returns model if successful

    This function is the abstraction boundary between core and enterprise.
    Core doesn't know HOW LiteLLM works, just calls this and checks for None.

    Args:
        llm_model_details: Model configuration
        litellm_context: LiteLLM context with credentials
        user_email: User email for budget tracking
        temperature: Optional temperature override
        top_p: Optional top_p override
        streaming: Enable streaming

    Returns:
        AzureChatOpenAI configured for LiteLLM, or None if not available
    """
    from .dependencies import is_litellm_enabled
    from codemie.configs import config
    from codemie.core.constants import LLMProxyMode

    # Check if LiteLLM is enabled (HAS_LITELLM + LLM_PROXY_ENABLED)
    if not is_litellm_enabled():
        return None

    # Check if we're specifically using LiteLLM mode (routing check)
    if LLMProxyMode.lite_llm != config.LLM_PROXY_MODE:
        return None

    try:
        return create_litellm_chat_model(
            llm_model_details=llm_model_details,
            litellm_context=litellm_context,
            user_email=user_email,
            temperature=temperature,
            top_p=top_p,
            streaming=streaming,
        )
    except Exception as e:
        logger.warning(f"Failed to create LiteLLM chat model: {e}", exc_info=True)
        return None  # Graceful degradation


def get_litellm_embedding_model(
    embedding_model: str,
    llm_model_details: "LLMModel",
    litellm_context: Optional["LiteLLMContext"],
    user_email: Optional[str],
) -> Optional["AzureOpenAIEmbeddings"]:
    """
    Get LiteLLM embedding model if enterprise is enabled AND proxy mode is lite_llm.

    Similar to get_litellm_chat_model but for embeddings.

    Args:
        embedding_model: Model name
        llm_model_details: Model configuration
        litellm_context: LiteLLM context with credentials
        user_email: User email for budget tracking

    Returns:
        AzureOpenAIEmbeddings configured for LiteLLM, or None if not available
    """
    from .dependencies import is_litellm_enabled
    from codemie.configs import config
    from codemie.core.constants import LLMProxyMode

    # Check if LiteLLM embeddings are explicitly disabled (use native providers instead)
    if config.LLM_PROXY_EMBEDDINGS_DISABLED:
        logger.debug("LiteLLM embeddings disabled via config, falling back to native providers")
        return None

    # Check if LiteLLM is enabled (HAS_LITELLM + LLM_PROXY_ENABLED)
    if not is_litellm_enabled():
        return None

    # Check if we're specifically using LiteLLM mode (routing check)
    if LLMProxyMode.lite_llm != config.LLM_PROXY_MODE:
        return None

    try:
        return create_litellm_embedding_model(
            embedding_model=embedding_model,
            llm_model_details=llm_model_details,
            litellm_context=litellm_context,
            user_email=user_email,
        )
    except Exception as e:
        logger.warning(f"Failed to create LiteLLM embedding model: {e}", exc_info=True)
        return None  # Graceful degradation
