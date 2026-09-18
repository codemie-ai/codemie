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

"""Helpers for identifying whole-response LiteLLM cache hits."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def is_litellm_proxy_cache_hit(generation_info: Mapping) -> bool:
    """Return True when the LiteLLM proxy response header signals a cache hit.

    LiteLLM's cache key or CodeMie's forwarded cache-hit header means the response
    was served from LiteLLM's LRU prompt cache.
    """
    headers = generation_info.get("headers")
    if not isinstance(headers, Mapping):
        return False
    return "x-litellm-cache-key" in headers or str(headers.get("x-codemie-litellm-cache-hit", "")).lower() == "true"


def is_llm_cache_hit(value: Any) -> bool:
    """Return True when value contains LiteLLM's whole-response cache marker.

    Checks only the known LiteLLM containers: LLMResult.llm_output, per-generation
    generation_info, and message._hidden_params / response_metadata.
    Provider-native prompt-cache fields such as cache_read are intentionally not
    considered whole-response hits.
    """
    llm_output = getattr(value, "llm_output", None)
    if isinstance(llm_output, Mapping) and _is_true(llm_output.get("cache_hit")):
        return True
    generations = getattr(value, "generations", None)
    if isinstance(generations, (list, tuple)) and _any_generation_is_cache_hit(generations):
        return True
    if isinstance(value, Mapping):
        return _is_true(value.get("cache_hit"))
    return False


def _any_generation_is_cache_hit(generations: list | tuple) -> bool:
    for gen_group in generations:
        items = gen_group if isinstance(gen_group, (list, tuple)) else (gen_group,)
        for gen in items:
            if _generation_is_cache_hit(gen):
                return True
    return False


def _generation_is_cache_hit(gen: Any) -> bool:
    info = getattr(gen, "generation_info", None)
    if isinstance(info, Mapping) and _is_true(info.get("cache_hit")):
        return True
    msg = getattr(gen, "message", None)
    if msg is not None:
        for attr in ("_hidden_params", "response_metadata"):
            container = getattr(msg, attr, None)
            if isinstance(container, Mapping) and _is_true(container.get("cache_hit")):
                return True
    return False


def _is_true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")
