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

import dataclasses
import json
from collections.abc import Mapping
from typing import ClassVar, Final, cast

from codemie.core.routing_info import RoutingHeaderCodec

LITELLM_ROUTER_FIELD_TO_HEADER: Final[dict[str, str]] = {
    "tier": "x-litellm-router-tier",
    "cause": "x-litellm-router-cause",
    "score": "x-litellm-router-score",
    "routed_model": "x-litellm-router-routed-model",
    "classifier_model": "x-litellm-router-classifier-model",
    "router_model_name": "x-litellm-router-model-name",
    "router_type": "x-litellm-router-type",
    "signals": "x-litellm-router-signals",
    "escalated": "x-litellm-router-escalated",
    "escalation_keyword": "x-litellm-router-escalation-keyword",
    "classifier_prompt_tokens": "x-litellm-classifier-prompt-tokens",
    "classifier_completion_tokens": "x-litellm-classifier-completion-tokens",
    "classifier_total_tokens": "x-litellm-classifier-total-tokens",
    "classifier_cost_usd": "x-litellm-classifier-cost",
}

LITELLM_ROUTER_HEADERS: Final[frozenset[str]] = frozenset(LITELLM_ROUTER_FIELD_TO_HEADER.values())

_INT_FIELDS: Final[frozenset[str]] = frozenset(
    {"classifier_prompt_tokens", "classifier_completion_tokens", "classifier_total_tokens"}
)
_FLOAT_FIELDS: Final[frozenset[str]] = frozenset({"score", "classifier_cost_usd"})


@dataclasses.dataclass
class LiteLLMRouterMeta(RoutingHeaderCodec):
    FIELD_TO_HEADER: ClassVar[dict[str, str]] = LITELLM_ROUTER_FIELD_TO_HEADER
    INT_FIELDS: ClassVar[frozenset[str]] = _INT_FIELDS
    FLOAT_FIELDS: ClassVar[frozenset[str]] = _FLOAT_FIELDS

    tier: str | None = None
    cause: str | None = None
    score: float | None = None
    routed_model: str | None = None
    classifier_model: str | None = None
    router_model_name: str | None = None
    router_type: str | None = None
    signals: str | None = None
    escalated: str | None = None
    escalation_keyword: str | None = None
    classifier_prompt_tokens: int | None = None
    classifier_completion_tokens: int | None = None
    classifier_total_tokens: int | None = None
    classifier_cost_usd: float | None = None

    @classmethod
    def from_routing_decision(cls, decision: Mapping[str, object]) -> LiteLLMRouterMeta:
        """Populate from LiteLLM routing_decision dict (used in AutorouterCallback).

        Each value is narrowed to the type its field actually declares (str, int, or
        float) before being handed to the constructor, mirroring SwitchyardMeta.from_dict.
        """
        known = {f.name for f in dataclasses.fields(cls)}
        kwargs: dict[str, int | float | str] = {}
        for field_name in known:
            value = decision.get(field_name)
            if value is None:
                continue
            if field_name == "signals":
                kwargs[field_name] = json.dumps(value)
            elif field_name in cls.INT_FIELDS:
                if isinstance(value, int):
                    kwargs[field_name] = value
            elif field_name in cls.FLOAT_FIELDS:
                if isinstance(value, (int, float)):
                    kwargs[field_name] = float(value)
            elif isinstance(value, str):
                kwargs[field_name] = value
        return cast(LiteLLMRouterMeta, dataclasses.replace(cls(), **kwargs))
