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
from collections.abc import Mapping
from typing import ClassVar, Final, cast

from codemie.core.routing_info import RoutingHeaderCodec

_SWITCHYARD_RESPONSE_META_KEY = "_switchyard_routing"

# Key under which the agent path passes the full RoutingDecision through a call's
# RunnableConfig metadata (config={"metadata": {_SWITCHYARD_DECISION_METADATA_KEY: decision}}),
# read by TokensCalculationCallback.on_chat_model_start. This is a separate channel from
# _SWITCHYARD_RESPONSE_META_KEY above: that one is attached to the response *after* the call
# returns (for UI-facing consumers reading the final message), this one is available *before*
# the call even starts (for the callback attached to the concretely selected model, whose
# on_llm_end fires before the response ever reaches back to _agenerate).
_SWITCHYARD_DECISION_METADATA_KEY = "_switchyard_decision"

SWITCHYARD_FIELD_TO_HEADER: Final[dict[str, str]] = {
    "routed_model": "x-codemie-routed-model",
    "requested_model": "x-codemie-requested-model",
    "tier": "x-codemie-routing-tier",
    "decision_source": "x-codemie-routing-decision-source",
    "confidence": "x-codemie-routing-confidence",
    "classifier_model": "x-codemie-routing-classifier-model",
    "classifier_input_tokens": "x-codemie-routing-classifier-input-tokens",
    "classifier_output_tokens": "x-codemie-routing-classifier-output-tokens",
    "classifier_cached_tokens": "x-codemie-routing-classifier-cached-tokens",
    "classifier_cache_creation_tokens": "x-codemie-routing-classifier-cache-creation-tokens",
    "classifier_cost_usd": "x-codemie-routing-classifier-cost-usd",
    "classifier_p_solve": "x-codemie-routing-classifier-p-solve",
    "classifier_crux": "x-codemie-routing-classifier-crux",
    "classifier_primary_rule": "x-codemie-routing-classifier-primary-rule",
    "classifier_capability_boundary": "x-codemie-routing-classifier-capability-boundary",
    "signal_score": "x-codemie-routing-signal-score",
    "signal_confidence": "x-codemie-routing-signal-confidence",
    "signal_severity": "x-codemie-routing-signal-severity",
    "signal_spinning": "x-codemie-routing-signal-spinning",
    "signal_exploring": "x-codemie-routing-signal-exploring",
    "signal_production": "x-codemie-routing-signal-production",
}

SWITCHYARD_HEADERS: Final[frozenset[str]] = frozenset(SWITCHYARD_FIELD_TO_HEADER.values())

_INT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "classifier_input_tokens",
        "classifier_output_tokens",
        "classifier_cached_tokens",
        "classifier_cache_creation_tokens",
    }
)
_FLOAT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "confidence",
        "classifier_cost_usd",
        "classifier_p_solve",
        "signal_score",
        "signal_confidence",
        "signal_severity",
        "signal_spinning",
        "signal_exploring",
        "signal_production",
    }
)


@dataclasses.dataclass
class SwitchyardMeta(RoutingHeaderCodec):
    FIELD_TO_HEADER: ClassVar[dict[str, str]] = SWITCHYARD_FIELD_TO_HEADER
    INT_FIELDS: ClassVar[frozenset[str]] = _INT_FIELDS
    FLOAT_FIELDS: ClassVar[frozenset[str]] = _FLOAT_FIELDS

    routed_model: str | None = None
    requested_model: str | None = None
    tier: str | None = None
    decision_source: str | None = None
    confidence: float | None = None
    classifier_model: str | None = None
    classifier_input_tokens: int | None = None
    classifier_output_tokens: int | None = None
    classifier_cached_tokens: int | None = None
    classifier_cache_creation_tokens: int | None = None
    classifier_cost_usd: float | None = None
    classifier_p_solve: float | None = None
    classifier_crux: str | None = None
    classifier_primary_rule: str | None = None
    classifier_capability_boundary: str | None = None
    signal_score: float | None = None
    signal_confidence: float | None = None
    signal_severity: float | None = None
    signal_spinning: float | None = None
    signal_exploring: float | None = None
    signal_production: float | None = None

    @classmethod
    def from_dict(cls, d: Mapping[str, object]) -> SwitchyardMeta:
        """Construct from a plain dict (e.g. dataclasses.asdict output). Unknown keys are ignored.

        Each value is narrowed to the type its field actually declares (str, int, or float)
        before being handed to the constructor — a value of the wrong shape for its field
        (e.g. a string in an int field) is dropped rather than passed through untyped.
        """
        known = {f.name for f in dataclasses.fields(cls)}
        kwargs: dict[str, int | float | str] = {}
        for key, value in d.items():
            if key not in known or value is None:
                continue
            if key in cls.INT_FIELDS:
                if isinstance(value, int):
                    kwargs[key] = value
            elif key in cls.FLOAT_FIELDS:
                if isinstance(value, (int, float)):
                    kwargs[key] = float(value)
            elif isinstance(value, str):
                kwargs[key] = value
        return cast(SwitchyardMeta, dataclasses.replace(cls(), **kwargs))
