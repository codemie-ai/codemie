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

SWITCHYARD_FIELD_TO_HEADER: Final[dict[str, str]] = {
    "routed_model": "x-codemie-routed-model",
    "requested_model": "x-codemie-requested-model",
    "tier": "x-codemie-routing-tier",
    "classifier_model": "x-codemie-routing-classifier-model",
    "classifier_input_tokens": "x-codemie-routing-classifier-input-tokens",
    "classifier_output_tokens": "x-codemie-routing-classifier-output-tokens",
    "classifier_cached_tokens": "x-codemie-routing-classifier-cached-tokens",
    "classifier_cache_creation_tokens": "x-codemie-routing-classifier-cache-creation-tokens",
    "classifier_cost_usd": "x-codemie-routing-classifier-cost-usd",
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
_FLOAT_FIELDS: Final[frozenset[str]] = frozenset({"classifier_cost_usd"})


@dataclasses.dataclass
class SwitchyardMeta(RoutingHeaderCodec):
    FIELD_TO_HEADER: ClassVar[dict[str, str]] = SWITCHYARD_FIELD_TO_HEADER
    INT_FIELDS: ClassVar[frozenset[str]] = _INT_FIELDS
    FLOAT_FIELDS: ClassVar[frozenset[str]] = _FLOAT_FIELDS

    routed_model: str | None = None
    requested_model: str | None = None
    tier: str | None = None
    classifier_model: str | None = None
    classifier_input_tokens: int | None = None
    classifier_output_tokens: int | None = None
    classifier_cached_tokens: int | None = None
    classifier_cache_creation_tokens: int | None = None
    classifier_cost_usd: float | None = None

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
