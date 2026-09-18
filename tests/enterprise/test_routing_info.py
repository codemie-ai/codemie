# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the “License”);
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an “AS IS” BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import dataclasses
from typing import ClassVar

import pytest

from codemie.core.routing_info import (
    ClassifierUsage,
    RoutingHeaderCodec,
    RoutingInfo,
    normalize_decision_source,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(" llm_classifier ", "llm-classifier"), ("HEURISTIC_SCORER", "heuristic-scorer"), (None, None)],
)
def test_normalize_decision_source(raw, expected):
    assert normalize_decision_source(raw) == expected


def test_routing_info_has_no_meta_field():
    assert not hasattr(RoutingInfo(), "meta")


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


def test_classifier_usage_is_a_plain_dataclass_with_defaults():
    usage = ClassifierUsage(provider="switchyard", input_tokens=10, output_tokens=5)
    assert usage.provider == "switchyard"
    assert usage.input_tokens == 10
    assert usage.output_tokens == 5
    assert usage.cost_usd is None
    assert usage.model is None


def test_merged_over_sums_additive_fields():
    """merged_over sums classifier tokens and cost across multiple RoutingInfo instances."""
    import functools

    a = RoutingInfo(
        classifier_cost_usd=0.001, classifier_input_tokens=100, classifier_output_tokens=50, classifier_cached_tokens=20
    )
    b = RoutingInfo(
        classifier_cost_usd=0.002, classifier_input_tokens=200, classifier_output_tokens=80, classifier_cached_tokens=0
    )
    c = RoutingInfo(classifier_cost_usd=0.003, classifier_input_tokens=150, classifier_output_tokens=30)
    merged = functools.reduce(lambda acc, x: acc.merged_over(x), [a, b, c], RoutingInfo())
    assert merged.classifier_cost_usd == pytest.approx(0.006)
    assert merged.classifier_input_tokens == 450
    assert merged.classifier_output_tokens == 160
    assert merged.classifier_cached_tokens == 20  # 20 + 0 + None(=0)


def test_merged_over_sums_additive_with_none():
    """None is treated as 0 for additive fields; result is None only when both sides are None."""
    a = RoutingInfo(classifier_input_tokens=100)
    b = RoutingInfo(classifier_input_tokens=None)
    merged = a.merged_over(b)
    assert merged.classifier_input_tokens == 100
    merged2 = RoutingInfo().merged_over(RoutingInfo())
    assert merged2.classifier_input_tokens is None  # both None → None


def test_is_empty_covers_all_fields():
    assert RoutingInfo().is_empty()
    assert not RoutingInfo(requested_model="opus").is_empty()
    assert not RoutingInfo(tier="haiku").is_empty()
    assert not RoutingInfo(decision_source="classifier").is_empty()
    assert not RoutingInfo(confidence=0.9).is_empty()
    assert not RoutingInfo(classifier_input_tokens=10).is_empty()
    assert not RoutingInfo(classifier_output_tokens=5).is_empty()
    assert not RoutingInfo(classifier_cached_tokens=3).is_empty()
    assert not RoutingInfo(classifier_total_tokens=15).is_empty()


def test_is_empty_covers_parity_fields():
    assert not RoutingInfo(routing_family="litellm").is_empty()
    assert not RoutingInfo(routing_cost_known=False).is_empty()
    assert not RoutingInfo(routing_tier_raw="efficient").is_empty()
    assert not RoutingInfo(classifier_model="haiku").is_empty()
    assert not RoutingInfo(router_type="complexity").is_empty()
    assert not RoutingInfo(router_score=0.8).is_empty()
    assert not RoutingInfo(classifier_cache_creation_tokens=4).is_empty()


def test_merged_over_sums_classifier_cache_creation_tokens_and_keeps_latest_metadata():
    base = RoutingInfo(
        routing_family="litellm",
        routing_cost_known=False,
        classifier_cache_creation_tokens=10,
        tier="middle",
    )
    top = RoutingInfo(
        routing_family="switchyard",
        routing_cost_known=True,
        classifier_cache_creation_tokens=4,
        tier="complex",
    )

    merged = top.merged_over(base)

    assert merged.routing_family == "switchyard"
    assert merged.routing_cost_known is True
    assert merged.classifier_cache_creation_tokens == 14
    assert merged.tier == "complex"


def test_classifier_total_tokens_is_additive():
    """classifier_total_tokens is summed like other classifier token fields."""
    a = RoutingInfo(classifier_total_tokens=125)
    b = RoutingInfo(classifier_total_tokens=200)
    merged = a.merged_over(b)
    assert merged.classifier_total_tokens == 325


def test_sum_or_none_treats_zero_as_valid_not_none():
    """_sum_or_none must not coerce explicit 0 to absent; (0 + None) == 0, not None."""
    a = RoutingInfo(classifier_input_tokens=0)
    b = RoutingInfo(classifier_input_tokens=None)
    merged = a.merged_over(b)
    # both not None for a, b is None → result is 0, not None
    assert merged.classifier_input_tokens == 0
    # (None, None) → None
    merged2 = RoutingInfo().merged_over(RoutingInfo())
    assert merged2.classifier_input_tokens is None
