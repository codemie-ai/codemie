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

import dataclasses
from typing import ClassVar

from langchain_core.messages import AIMessage

from codemie.core.routing_info import RoutingHeaderCodec, RoutingInfo, compose_routing_info


def test_merged_over_prefers_self_non_none_fields():
    base = RoutingInfo(routed_model="sonnet", classifier_cost_usd=0.01)
    top = RoutingInfo(routed_model="haiku")  # only routed_model set
    merged = top.merged_over(base)
    assert merged.routed_model == "haiku"  # self wins
    assert merged.classifier_cost_usd == 0.01  # falls through to base


def test_merged_over_keeps_base_when_self_empty():
    base = RoutingInfo(routed_model="sonnet", routed_model_label="Sonnet")
    merged = RoutingInfo().merged_over(base)
    assert merged == base


def test_is_empty():
    assert RoutingInfo().is_empty()
    assert not RoutingInfo(routed_model="x").is_empty()


class _Fixed:
    def __init__(self, info: RoutingInfo) -> None:
        self._info = info

    def extract(self, response):  # noqa: ARG002
        return self._info


def test_compose_first_extractor_wins_per_field():
    first = _Fixed(RoutingInfo(routed_model="haiku"))
    second = _Fixed(RoutingInfo(routed_model="sonnet", classifier_cost_usd=0.02))
    merged = compose_routing_info(response=AIMessage(content=""), extractors=[first, second])
    assert merged.routed_model == "haiku"  # first wins
    assert merged.classifier_cost_usd == 0.02  # only second had it


def test_compose_empty_extractors_returns_empty():
    assert compose_routing_info(response=AIMessage(content=""), extractors=[]).is_empty()


@dataclasses.dataclass
class _FakeRouterMeta(RoutingHeaderCodec):
    """Minimal RoutingHeaderCodec subclass, exercising the shared to_headers/from_headers."""

    FIELD_TO_HEADER: ClassVar[dict[str, str]] = {
        "name": "x-fake-name",
        "score": "x-fake-score",
        "count": "x-fake-count",
    }
    FLOAT_FIELDS: ClassVar[frozenset[str]] = frozenset({"score"})
    INT_FIELDS: ClassVar[frozenset[str]] = frozenset({"count"})

    name: str | None = None
    score: float | None = None
    count: int | None = None


def test_header_codec_to_headers_omits_none_and_formats_floats():
    meta = _FakeRouterMeta(name="haiku", score=0.123456789)
    headers = meta.to_headers()
    assert headers["x-fake-name"] == "haiku"
    assert headers["x-fake-score"] == "0.123457"  # .6g formatting
    assert "x-fake-count" not in headers


def test_header_codec_from_headers_round_trips_typed_fields():
    meta = _FakeRouterMeta(name="sonnet", score=0.5, count=7)
    restored = _FakeRouterMeta.from_headers(meta.to_headers())
    assert restored == meta


def test_header_codec_from_headers_ignores_invalid_numeric_values():
    restored = _FakeRouterMeta.from_headers({"x-fake-score": "not-a-number", "x-fake-count": "also-bad"})
    assert restored.score is None
    assert restored.count is None


def test_header_codec_percent_encodes_non_ascii():
    meta = _FakeRouterMeta(name="разработка")
    headers = meta.to_headers()
    headers["x-fake-name"].encode("latin-1")  # must not raise
    restored = _FakeRouterMeta.from_headers(headers)
    from urllib.parse import unquote

    assert restored.name is not None
    assert unquote(restored.name) == "разработка"
