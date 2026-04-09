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

import boto3
import contextvars
import json
from botocore.config import Config
from langchain_core.vectorstores import VectorStoreRetriever
from langchain_elasticsearch import ElasticsearchStore
from langchain_openai import AzureChatOpenAI
from langchain_openai import AzureOpenAIEmbeddings
from openai.lib.azure import AzureOpenAI
from typing import Optional, Any

from codemie.agents.callbacks.tokens_callback import TokensCalculationCallback
from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.configs import config, logger
from codemie.configs.llm_config import LLMProvider, LLMModel
from codemie.configs.logger import current_user_email
from codemie.core.constants import DEFAULT_MAX_OUTPUT_TOKENS_4K, DEFAULT_MAX_OUTPUT_TOKENS_8K
from codemie.core.models import ElasticSearchKwargs, GitRepo, CodeFields, Application
from codemie.rest_api.models.settings import DialCredentials, LiteLLMContext
from codemie.service.llm_service.llm_service import llm_service, LLMService


def get_embeddings_model(embedding_model: str = llm_service.default_embedding_model) -> Any:
    """Return an embeddings model instance for the given embedding model name.

    Routing rules:
    - Try LiteLLM if enterprise is available and enabled
    - If provider is Google Vertex AI, return VertexAIEmbeddings.
    - If provider is AWS Bedrock, return BedrockEmbeddings.
    - Otherwise, default to AzureOpenAIEmbeddings.
    """
    from codemie.enterprise.litellm import get_litellm_embedding_model

    llm_model_details = llm_service.get_model_details(embedding_model)

    # Try LiteLLM path (abstracted - returns None if not available)
    context = litellm_context.get(None)
    user_email = current_user_email.get()

    litellm_embeddings = get_litellm_embedding_model(
        embedding_model=embedding_model,
        llm_model_details=llm_model_details,
        litellm_context=context,
        user_email=user_email,
    )

    if litellm_embeddings:
        logger.debug(f"Init embeddings via LiteLLM proxy. Model={embedding_model}. Details={llm_model_details}")
        return litellm_embeddings

    provider = llm_model_details.provider
    logger.debug(f"Init embeddings. Model={embedding_model}. Provider={provider}. Details={llm_model_details}")

    if provider == LLMProvider.GOOGLE_VERTEX_AI:
        from langchain_google_vertexai import VertexAIEmbeddings

        return VertexAIEmbeddings(
            project=config.GOOGLE_PROJECT_ID,
            location=config.GOOGLE_VERTEXAI_REGION,
            model_name=embedding_model,
            max_retries=10,
        )
    elif provider == LLMProvider.AWS_BEDROCK:
        from langchain_aws import BedrockEmbeddings

        return BedrockEmbeddings(client=get_bedrock_runtime_client(), model_id=embedding_model)
    else:
        logger.debug(f"Init embeddings with AzureOpenAIEmbeddings. ModelName={embedding_model}")
        return AzureOpenAIEmbeddings(
            api_key=config.AZURE_OPENAI_API_KEY,
            azure_endpoint=config.AZURE_OPENAI_URL,
            deployment=embedding_model,
            model=embedding_model,
            tiktoken_model_name=LLMService.BASE_NAME_GPT_41_MINI,
            openai_api_type=config.OPENAI_API_TYPE,
            openai_api_version=llm_model_details.api_version or config.OPENAI_API_VERSION,
            max_retries=10,
            show_progress_bar=True,
            check_embedding_ctx_length=False,
        )


def get_elasticsearch(index_name, embeddings_model: str = llm_service.default_embedding_model) -> ElasticsearchStore:
    elastic_client = ElasticSearchClient.get_client()
    embeddings = get_embeddings_model(embeddings_model)

    return ElasticsearchStore(
        client=elastic_client,
        index_name=index_name,
        embedding=embeddings,
    )


""" Context var to hold current LLM credentials """
dial_credentials = contextvars.ContextVar("llm_creds")
litellm_context = contextvars.ContextVar("litellm_context")
disable_prompt_cache = contextvars.ContextVar("disable_prompt_cache", default=False)


def set_dial_credentials(creds: DialCredentials | None):
    """Set LLM credentials for current context"""
    if creds:
        dial_credentials.set(creds)


def set_litellm_context(context: LiteLLMContext | None):
    """Set LLM credentials for current context"""
    if context:
        litellm_context.set(context)


def get_current_project(fallback: str | None = None) -> str:
    """Get the billing-aware project from the current LiteLLM context.

    Returns the project resolved by set_llm_context (handles global-assistant
    billing attribution, e.g. user.email instead of assistant.project).
    Falls back to *fallback* when no context has been set yet.
    """
    ctx = litellm_context.get(None)
    if ctx and ctx.current_project:
        return ctx.current_project
    return fallback or ""


def set_disable_prompt_cache(disable: bool):
    """Set prompt cache control flag for current context"""
    disable_prompt_cache.set(disable)


def get_disable_prompt_cache() -> bool:
    """Get prompt cache control flag from current context"""
    return disable_prompt_cache.get(False)


def get_bedrock_runtime_client():
    """
    This function will return bedrock runtime client.
    Uses dedicated AWS_BEDROCK_REGION if provided to avoid dependency on AWS_DEFAULT_REGION.
    """
    region_kwargs = {}
    region = getattr(config, 'AWS_BEDROCK_REGION', '').strip()
    if region:
        region_kwargs['region_name'] = region

    bedrock_runtime_client = boto3.client(
        "bedrock-runtime",
        **region_kwargs,
        config=Config(
            retries={
                "max_attempts": config.AWS_BEDROCK_MAX_RETRIES,
            },
            read_timeout=config.AWS_BEDROCK_READ_TIMEOUT,
        ),
    )

    return bedrock_runtime_client


def _should_wrap_llm_client(llm_model_details: LLMModel, llm: Any) -> bool:
    """
    Determine if LLM client should be wrapped for logging.

    Args:
        llm_model_details: Model configuration details
        llm: The LLM instance to check

    Returns:
        True if client should be wrapped, False otherwise
    """
    if llm_model_details.provider == LLMProvider.ANTHROPIC:
        return False
    if llm_model_details.provider in (LLMProvider.GOOGLE_VERTEX_AI, LLMProvider.VERTEX_AI_ANTHROPIC):
        return False
    return not (hasattr(llm, 'model_name') and llm.model_name)


def get_llm_by_credentials(
    llm_model: str = llm_service.default_llm_model,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    streaming: bool = True,
    request_id: Optional[str] = None,
):
    from codemie.enterprise.litellm import get_litellm_chat_model

    llm_model_details = llm_service.get_model_details(llm_model)

    context = litellm_context.get(None)
    user_email = current_user_email.get()

    litellm_llm = get_litellm_chat_model(
        llm_model_details=llm_model_details,
        litellm_context=context,
        user_email=user_email,
        temperature=temperature,
        top_p=top_p,
        streaming=streaming,
    )

    if litellm_llm:
        llm = litellm_llm
        is_using_litellm = True
    else:
        llm = get_llm_by_credentials_raw(llm_model, temperature, top_p, streaming)
        is_using_litellm = False

    if _should_wrap_llm_client(llm_model_details, llm):
        llm.client = LLMClientWrapper(llm.client)

    if request_id:
        llm.callbacks = [TokensCalculationCallback(request_id=request_id, llm_model=llm_model)]

    is_claude = llm_service.is_claude_models(llm_model_details.base_name)
    is_open_ai = llm_model_details.provider == LLMProvider.AZURE_OPENAI
    is_bedrock = llm_model_details.provider == LLMProvider.AWS_BEDROCK

    # Claude 3.5 models on Bedrock don't support prompt caching with tools
    is_unsupported_bedrock_model = is_bedrock and "claude-3-5-sonnet" in llm_model_details.base_name

    # Vertex AI Claude doesn't support Anthropic beta headers (anthropic_beta)
    # Google wraps the Claude API and doesn't support prompt caching headers
    # Can be enabled via VERTEX_AI_ENABLE_PROMPT_CACHE config if needed
    is_vertex_claude = llm_model_details.provider == LLMProvider.VERTEX_AI_ANTHROPIC
    vertex_ai_cache_enabled = config.VERTEX_AI_ANTHROPIC_ENABLE_PROMPT_CACHE if is_vertex_claude else True

    # Check if caching is disabled via context variable
    cache_disabled = get_disable_prompt_cache()

    # Enable prompt caching for Claude models (except unsupported cases)
    enable_caching = (
        is_claude
        and not is_unsupported_bedrock_model
        and vertex_ai_cache_enabled
        and (not is_open_ai or is_using_litellm)
        and not cache_disabled
    )

    if enable_caching:
        from langchain_anthropic_smart_cache import SmartCacheCallbackHandler

        cache_handler = SmartCacheCallbackHandler()
        if llm.callbacks:
            llm.callbacks.append(cache_handler)
        else:
            llm.callbacks = [cache_handler]
        logger.debug(f"Prompt caching enabled for model={llm_model}")
    elif cache_disabled:
        logger.debug(f"Prompt caching explicitly disabled for model={llm_model}")
    elif is_vertex_claude:
        logger.debug(f"Prompt caching not supported for Vertex AI Claude models: {llm_model}")
    return llm


class LLMClientWrapper:
    def __init__(self, wrapped_class):
        self.wrapped_class = wrapped_class

    def __getattr__(self, attr):
        original_func = getattr(self.wrapped_class, attr)

        def wrapper(*args, **kwargs):
            try:
                if 'body' in kwargs:
                    logger.debug(f"Call LLM with the following body:\n{kwargs['body']}")
                else:
                    logger.debug(f"Call LLM with the following body:\n{json.dumps(kwargs)}")
            except Exception as e:
                logger.warning(f"Exception has been occurred during the logging request to LLM: \n{str(e)}")
            return original_func(*args, **kwargs)

        return wrapper


def filter_params(base_args, optional_args, llm_model_details):
    # Filter parameters based on LLMModel features
    if llm_model_details.features.temperature is False:
        optional_args['temperature'] = 1
    if llm_model_details.features.parallel_tool_calls is False:
        base_args['disabled_params'] = {"parallel_tool_calls": None}
    if llm_model_details.features.streaming is False:
        base_args['streaming'] = False
        base_args['disable_streaming'] = True
        if 'stream_usage' in base_args:
            base_args.pop('stream_usage')
    if llm_model_details.features.max_tokens is False:
        base_args['max_tokens'] = None
    if llm_model_details.features.top_p is False:
        base_args['top_p'] = None
    return {**base_args, **optional_args}


def format_headers_for_openai_api(default_headers: dict) -> None:
    """Modifies the default_headers in-place: If the key is "anthropic_beta", its value is JSON-encoded."""
    for key in default_headers:
        if key == "anthropic_beta":
            default_headers[key] = json.dumps(default_headers[key])


def get_llm_by_credentials_raw(
    llm_model: str = llm_service.default_llm_model,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    streaming: bool = True,
):
    """Get LLM instance based on current context credentials"""
    creds = dial_credentials.get(None)
    llm_model_details = llm_service.get_model_details(llm_model)
    logger.debug(f"Init llm. Model Details={llm_model_details}")

    if not creds:
        providers = {
            LLMProvider.GOOGLE_VERTEX_AI: get_vertex_llm,
            LLMProvider.AWS_BEDROCK: get_bedrock_llm,
            LLMProvider.ANTHROPIC: get_anthropic_llm,
        }
        llm_factory = providers.get(llm_model_details.provider, None)
        if llm_factory:
            llm = llm_factory(
                llm_model_details=llm_model_details,
                temperature=temperature,
                top_p=top_p,
                streaming=streaming,
            )
            if llm:
                return llm

    # Merge static config headers with dynamic headers
    merged_headers = {}
    if llm_model_details.configuration and llm_model_details.configuration.client_headers:
        merged_headers.update(llm_model_details.configuration.client_headers)

    # Resolve api_version: per-request creds > model-level config > global config
    resolved_api_version = creds.api_version if creds else (llm_model_details.api_version or config.OPENAI_API_VERSION)

    optional_args = {k: v for k, v in {}.items() if v is not None}
    base_args = {
        'azure_endpoint': creds.url if creds else config.AZURE_OPENAI_URL,
        'openai_api_version': resolved_api_version,
        'openai_api_key': creds.api_key if creds else config.AZURE_OPENAI_API_KEY,
        'openai_api_type': config.OPENAI_API_TYPE,
        'deployment_name': llm_model_details.deployment_name,
        'model_name': llm_model_details.base_name,
        'streaming': streaming,
        'max_retries': config.AZURE_OPENAI_MAX_RETRIES,
        'max_tokens': llm_model_details.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS_4K,
    }

    # Add custom headers if any exist
    if merged_headers:
        format_headers_for_openai_api(merged_headers)
        base_args['default_headers'] = merged_headers

    return AzureChatOpenAI(**filter_params(base_args, optional_args, llm_model_details))


def get_vertex_llm(
    llm_model_details: LLMModel,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    streaming: bool = True,
):
    # Merge static config headers with dynamic headers
    merged_headers = {}
    if llm_model_details.configuration and llm_model_details.configuration.client_headers:
        merged_headers.update(llm_model_details.configuration.client_headers)

    base_args = {
        'project': config.GOOGLE_PROJECT_ID,
        'location': config.GOOGLE_VERTEXAI_REGION,
        'model_name': llm_model_details.deployment_name,
        'streaming': streaming,
        'max_retries': config.GOOGLE_VERTEXAI_MAX_RETRIES,
        'max_tokens': llm_model_details.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS_8K,
        'temperature': temperature,
        'top_p': top_p,
    }

    # Add headers to client_options if they exist
    if merged_headers:
        base_args['client_options'] = {"additional_headers": merged_headers}
    if llm_service.is_gemini_models(llm_model_details.base_name):
        from langchain_google_vertexai import ChatVertexAI

        return ChatVertexAI(**base_args)
    elif llm_service.is_claude_models(llm_model_details.base_name):
        from langchain_google_vertexai.model_garden import ChatAnthropicVertex

        base_args["location"] = config.GOOGLE_CLAUDE_VERTEXAI_REGION
        return ChatAnthropicVertex(**base_args)


def get_bedrock_llm(
    llm_model_details: LLMModel,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    streaming: bool = True,
):
    from langchain_aws import ChatBedrockConverse

    # Merge static config headers with dynamic headers
    merged_headers = {}
    if llm_model_details.configuration and llm_model_details.configuration.client_headers:
        merged_headers.update(llm_model_details.configuration.client_headers)

    disabled_streaming = not streaming or not llm_model_details.features.streaming
    return ChatBedrockConverse(
        client=get_bedrock_runtime_client(),
        model=llm_model_details.deployment_name,
        temperature=temperature,
        top_p=top_p,
        max_tokens=llm_model_details.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS_8K,
        disable_streaming=disabled_streaming,
        additional_model_request_fields=merged_headers if merged_headers else None,
    )


def get_anthropic_llm(
    llm_model_details: LLMModel,
    temperature: Optional[float] = None,
    top_p: Optional[float] = None,
    streaming: bool = True,
):
    from langchain_anthropic import ChatAnthropic

    # Merge static config headers with dynamic headers
    merged_headers = {}
    if llm_model_details.configuration and llm_model_details.configuration.client_headers:
        merged_headers.update(llm_model_details.configuration.client_headers)

    model_kwargs = {"extra_headers": merged_headers if merged_headers else None}
    return ChatAnthropic(
        model=llm_model_details.deployment_name,
        temperature=temperature,
        top_p=top_p,
        max_tokens=llm_model_details.max_output_tokens or DEFAULT_MAX_OUTPUT_TOKENS_8K,
        max_retries=config.ANTHROPIC_MAX_RETRIES,
        streaming=streaming,
        model_kwargs=model_kwargs,
    )


def get_elasticsearch_retriever(
    index_name: str, top_k: int = 10, embeddings_model: str = llm_service.default_embedding_model
) -> VectorStoreRetriever:
    elastic_search = get_elasticsearch(index_name, embeddings_model)
    search_kwargs = ElasticSearchKwargs(k=top_k).model_dump()

    return elastic_search.as_retriever(search_kwargs=search_kwargs)


def get_indexed_repo(code_field: CodeFields) -> GitRepo:
    repo_id = GitRepo.identifier_from_fields(
        app_id=code_field.app_name, name=code_field.repo_name, index_type=code_field.index_type
    )
    return GitRepo.get_by_id(repo_id)


def get_git_repo_retriever(code_field: CodeFields, top_k: int = 10) -> VectorStoreRetriever:
    indexed_repo = get_indexed_repo(code_field)
    git_repo = get_repo_from_fields(code_field)
    embeddings_model = llm_service.get_embedding_deployment_name(git_repo.embeddings_model)
    return get_elasticsearch_retriever(indexed_repo.get_identifier(), top_k, embeddings_model)


def get_stt_openai_client():
    return AzureOpenAI(
        api_version=config.OPENAI_API_VERSION,
        azure_endpoint=config.STT_API_URL,
        api_key=config.STT_API_KEY,
        azure_deployment=config.STT_API_DEPLOYMENT_NAME,
    )


def get_repo_from_fields(code_fields: CodeFields):
    application = Application.get_by_id(code_fields.app_name)
    git_repos = GitRepo.get_by_app_id(app_id=application.name)
    try:
        repository = next(
            repo
            for repo in git_repos
            if repo.name == code_fields.repo_name and repo.index_type == code_fields.index_type
        )
        return repository
    except StopIteration:
        logger.error(f"Repository [{code_fields.repo_name}] not found")
