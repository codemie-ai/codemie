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
from urllib.parse import unquote

from codemie.enterprise.switchyard.routing_meta import (
    SwitchyardMeta,
    SWITCHYARD_FIELD_TO_HEADER,
    SWITCHYARD_HEADERS,
)


def test_to_headers_includes_only_non_none_fields():
    meta = SwitchyardMeta(tier="efficient", routed_model="claude-haiku-4-5")
    headers = meta.to_headers()
    assert headers["x-codemie-routing-tier"] == "efficient"
    assert headers["x-codemie-routed-model"] == "claude-haiku-4-5"
    assert "x-codemie-routing-classifier-model" not in headers  # None field omitted


def test_classifier_model_round_trips_through_headers_and_dict():
    meta = SwitchyardMeta(classifier_model="gpt-5.6-luna-2026-07-09", classifier_input_tokens=120)
    headers = meta.to_headers()
    assert headers["x-codemie-routing-classifier-model"] == "gpt-5.6-luna-2026-07-09"

    restored_from_headers = SwitchyardMeta.from_headers(headers)
    assert restored_from_headers.classifier_model == "gpt-5.6-luna-2026-07-09"
    assert restored_from_headers.classifier_input_tokens == 120

    restored_from_dict = SwitchyardMeta.from_dict(dataclasses.asdict(meta))
    assert restored_from_dict == meta


def test_to_headers_serialises_float_fields():
    meta = SwitchyardMeta(classifier_cost_usd=0.001234)
    headers = meta.to_headers()
    assert headers["x-codemie-routing-classifier-cost-usd"] == "0.001234"


def test_from_dict_round_trips():
    meta = SwitchyardMeta(routed_model="m", tier="capable", classifier_cost_usd=0.001)
    d = dataclasses.asdict(meta)
    restored = SwitchyardMeta.from_dict(d)
    assert restored == meta


def test_from_dict_ignores_unknown_keys():
    d = {"routed_model": "m", "future_field": "ignored"}
    meta = SwitchyardMeta.from_dict(d)
    assert meta.routed_model == "m"


def test_switchyard_headers_matches_field_to_header_values():
    assert frozenset(SWITCHYARD_FIELD_TO_HEADER.values()) == SWITCHYARD_HEADERS


def test_field_to_header_keys_match_dataclass_fields():
    field_names = {f.name for f in dataclasses.fields(SwitchyardMeta)}
    assert set(SWITCHYARD_FIELD_TO_HEADER.keys()) == field_names


def test_to_headers_percent_encodes_non_ascii_characters():
    # Model/tier names could in principle carry Unicode. Starlette raises
    # UnicodeEncodeError if header values contain chars > 255. Non-ASCII is
    # percent-encoded (reversible) rather than replaced with '?'.
    meta = SwitchyardMeta(requested_model="разработка", classifier_model="→ simple")
    headers = meta.to_headers()
    # All values must be representable as latin-1 (no UnicodeEncodeError)
    for v in headers.values():
        v.encode("latin-1")  # must not raise
    # Original Unicode is fully preserved after percent-decoding
    assert unquote(headers["x-codemie-requested-model"]) == "разработка"
    assert unquote(headers["x-codemie-routing-classifier-model"]) == "→ simple"
