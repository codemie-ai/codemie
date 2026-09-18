# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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
from typing import ClassVar, Final

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
class LiteLLMRouterHeaders(RoutingHeaderCodec):
    """Typed parser for LiteLLM's own external x-litellm-router-*/x-litellm-classifier-*
    response headers — mirrors that foreign wire vocabulary exactly (field names like ``cause``,
    ``router_model_name``, ``score`` are LiteLLM's own, not ours). Used exclusively by
    enterprise/litellm/router.py's routing_info_from_headers()/extract_classifier_usage() to
    translate this wire shape into RoutingInfo's own domain fields (different names on purpose:
    cause->decision_source, router_model_name->requested_model, score->confidence/router_score).
    Read-only in practice: this codebase never builds outgoing x-litellm-router-* headers (those
    are emitted by the external LiteLLM proxy fork's own callback code), so only from_headers()
    (inherited from RoutingHeaderCodec) is exercised in production."""

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
