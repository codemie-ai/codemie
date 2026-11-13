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

"""Claude prompt caching through each route's own mechanism.

The LiteLLM proxy places the cache markers itself when a request lists ``cache_control_injection_points``.
Direct Anthropic and Vertex Claude models add them from a ``cache_control`` model kwarg, and direct Bedrock models
through langchain-aws cache points. DIAL models have no such option and are left unchanged.
"""

from __future__ import annotations

import sys

from langchain_core.language_models import BaseChatModel
from langchain_openai.chat_models.base import BaseChatOpenAI

from codemie.configs.config import config
from codemie.configs.llm_config import LLMModel, LLMProvider
from codemie.service.llm_service.llm_service import llm_service

CACHE_CONTROL = {"type": "ephemeral"}
_LITELLM_INJECTION_POINTS = (
    {"location": "message", "role": "system"},
    {"location": "message", "index": -1},
    {"location": "tool_config"},
)


def should_cache_prompts(model_details: LLMModel, *, uses_litellm: bool, disabled_for_request: bool) -> bool:
    if disabled_for_request or not llm_service.is_claude_models(model_details.base_name):
        return False
    if model_details.provider == LLMProvider.VERTEX_AI_ANTHROPIC:
        return config.VERTEX_AI_ANTHROPIC_ENABLE_PROMPT_CACHE
    return model_details.provider != LLMProvider.AZURE_OPENAI or uses_litellm


def enable_prompt_caching(llm: BaseChatModel, *, uses_litellm: bool) -> None:
    if uses_litellm and isinstance(llm, BaseChatOpenAI):
        injection_points = [dict(point) for point in _LITELLM_INJECTION_POINTS]
        llm.extra_body = {**(llm.extra_body or {}), "cache_control_injection_points": injection_points}
    elif _is_instance_of_loaded_class(llm, "langchain_anthropic.chat_models", "ChatAnthropic") or (
        _is_instance_of_loaded_class(llm, "langchain_google_vertexai.model_garden", "ChatAnthropicVertex")
    ):
        llm.model_kwargs = {**llm.model_kwargs, "cache_control": dict(CACHE_CONTROL)}
    elif _is_instance_of_loaded_class(llm, "codemie.core.prompt_caching_bedrock", "PromptCachingChatBedrockConverse"):
        llm.prompt_caching = True


def _is_instance_of_loaded_class(llm: BaseChatModel, module_name: str, class_name: str) -> bool:
    # A model can only be an instance of an already imported class, and importing the provider packages here
    # would load them for every model (langchain_google_vertexai alone costs hundreds of MB).
    module = sys.modules.get(module_name)
    return module is not None and isinstance(llm, getattr(module, class_name))
