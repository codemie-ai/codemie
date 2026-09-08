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

"""LiteLLM complexity-router (x-litellm-*) routing-metadata extractor."""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult

from codemie.core.routing_info import RoutingInfo
from codemie.enterprise.litellm.litellm_router_meta import LiteLLMRouterMeta


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
            for source in (
                getattr(gen, "generation_info", None),
                getattr(getattr(gen, "message", None), "response_metadata", None),
            ):
                h = _headers_of(source)
                if h:
                    yield h


class LiteLLMRouterExtractor:
    """Extract RoutingInfo from LiteLLM complexity-router response headers."""

    def extract(self, response: LLMResult | AIMessage) -> RoutingInfo:
        info = RoutingInfo()
        for headers in _iter_header_maps(response):
            meta = LiteLLMRouterMeta.from_headers(headers)
            merged = RoutingInfo(
                routed_model=meta.routed_model,
                classifier_cost_usd=meta.classifier_cost_usd,
            )
            info = info.merged_over(merged)
        return info
