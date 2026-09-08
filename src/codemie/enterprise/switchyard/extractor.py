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

"""Switchyard routing-metadata extractor."""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from langchain_core.messages import AIMessage
from langchain_core.outputs import LLMResult

from codemie.core.routing_info import RoutingInfo
from codemie.enterprise.switchyard.routing_meta import SwitchyardMeta, _SWITCHYARD_RESPONSE_META_KEY


def _iter_response_metadata(response: LLMResult | AIMessage) -> Iterator[Mapping[str, object]]:
    if isinstance(response, AIMessage):
        rm = getattr(response, "response_metadata", None)
        if isinstance(rm, Mapping):
            yield rm
        return
    for gen_list in response.generations:
        for gen in gen_list:
            rm = getattr(getattr(gen, "message", None), "response_metadata", None)
            if isinstance(rm, Mapping):
                yield rm


def _routing_info_from_metadata(rm: Mapping[str, object]) -> RoutingInfo:
    """Extract RoutingInfo from the typed SwitchyardMeta stored under _SWITCHYARD_RESPONSE_META_KEY.

    This is the only wire format Switchyard has ever shipped (the feature has no production
    users yet), so there is no legacy format to fall back to here.
    """
    raw = rm.get(_SWITCHYARD_RESPONSE_META_KEY)
    if isinstance(raw, dict):
        meta = SwitchyardMeta.from_dict(raw)
        return RoutingInfo(
            routed_model=meta.routed_model,
            classifier_cost_usd=meta.classifier_cost_usd,
        )
    return RoutingInfo()


class SwitchyardRoutingExtractor:
    """Extract RoutingInfo from Switchyard response metadata."""

    def extract(self, response: LLMResult | AIMessage) -> RoutingInfo:
        info = RoutingInfo()
        for rm in _iter_response_metadata(response):
            merged = _routing_info_from_metadata(rm)
            info = info.merged_over(merged)
        return info
