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

"""Header-extraction helpers for LiteLLM complexity-router (x-litellm-*) response headers.

Used by ``LiteLLMRouter.extract`` (``codemie/enterprise/litellm/router.py``) to locate the
headers mapping on an LLM response, wherever LangChain happened to stash it for the given call
path. Also provides catalog lookups for routing dimensions like counterfactual_model.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult


def _headers_of(container: object) -> Mapping[str, object] | None:
    if isinstance(container, Mapping):
        h = container.get("headers")
        if isinstance(h, Mapping):
            return h
    return None


def _iter_header_maps(response: LLMResult | AIMessage) -> Iterator[Mapping[str, object]]:
    if isinstance(response, AIMessage):
        h = _headers_of(getattr(response, "response_metadata", None))
        if h:
            yield h
        return
    for gen_list in getattr(response, "generations", []):
        for gen in gen_list:
            # generation_info is captured directly from LiteLLM's LLMResult before
            # LangChain assembles response_metadata. The latter may contain a corrupted
            # duplicate of the same x-litellm-* values on assistant calls, so never merge
            # it with the authoritative generation_info copy.
            generation_headers = _headers_of(getattr(gen, "generation_info", None))
            if generation_headers is not None:
                yield generation_headers
                continue

            response_headers = _headers_of(getattr(getattr(gen, "message", None), "response_metadata", None))
            if response_headers is not None:
                yield response_headers


def resolve_counterfactual_model(router_model_name: str | None) -> str | None:
    """Look up the router's declared counterfactual_model from the model catalog.

    For Switchyard routers, returns the capable model (implicit, since switchyard.efficient
    is the fallback). For LiteLLM auto-routers, returns the declared counterfactual_model from
    the model's litellm_router config, if present.

    Returns None if the router is not found or has no declared counterfactual anchor.
    Any exception during lookup is swallowed; savings simply aren't computable for that
    response, which is safe — the fields stay None.
    """
    if not router_model_name:
        return None

    from codemie.configs import logger
    from codemie.service.llm_service.llm_service import llm_service

    try:
        model = llm_service.get_model_details(router_model_name)
        if not model:
            return None

        # For Switchyard routers: the capable model is always the counterfactual anchor.
        if model.switchyard:
            # switchyard is a list; if any entry exists, this model is a Switchyard capable.
            return model.base_name

        # For LiteLLM auto-routers: use the declared counterfactual_model.
        if model.litellm_router and model.litellm_router.counterfactual_model:
            return model.litellm_router.counterfactual_model

        return None
    except Exception:
        logger.debug(f"Could not resolve counterfactual_model for router {router_model_name}")
        return None
