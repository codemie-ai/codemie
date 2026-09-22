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

from codemie.core.proxy_routing_headers import _routing_info_to_headers
from codemie.core.routing_info import RoutingInfo


def test_routing_info_to_headers_serialises_typed_fields():
    info = RoutingInfo(
        routed_model="claude-4-5-haiku",
        requested_model="claude-4-6-sonnet",
        counterfactual_model="claude-4-6-sonnet",
        tier="simple",
        decision_source="llm-classifier",
        routing_source="judge",
        router_type="composite",
        routing_family="switchyard",
        classifier_model="gpt-5.6-luna",
        classifier_input_tokens=120,
        classifier_output_tokens=40,
        classifier_cached_tokens=15,
        classifier_cache_creation_tokens=5,
        classifier_cost_usd=0.0009,
    )
    headers = _routing_info_to_headers(info)
    assert headers["x-codemie-routed-model"] == "claude-4-5-haiku"
    assert headers["x-codemie-requested-model"] == "claude-4-6-sonnet"
    assert headers["x-codemie-routing-counterfactual-model"] == "claude-4-6-sonnet"
    assert headers["x-codemie-routing-tier"] == "simple"
    assert headers["x-codemie-routing-decision-source"] == "llm-classifier"
    assert headers["x-codemie-routing-source"] == "judge"
    assert headers["x-codemie-routing-router-type"] == "composite"
    assert headers["x-codemie-routing-family"] == "switchyard"
    assert headers["x-codemie-routing-classifier-model"] == "gpt-5.6-luna"
    assert headers["x-codemie-routing-classifier-input-tokens"] == "120"
    assert headers["x-codemie-routing-classifier-output-tokens"] == "40"
    assert headers["x-codemie-routing-classifier-cached-tokens"] == "15"
    assert headers["x-codemie-routing-classifier-cache-creation-tokens"] == "5"
    assert headers["x-codemie-routing-classifier-cost-usd"] == "0.0009"


def test_routing_info_to_headers_omits_none_fields():
    headers = _routing_info_to_headers(RoutingInfo())
    assert headers == {}


def test_routing_info_to_headers_works_for_a_litellm_shaped_routing_info():
    """The same codec must serialise a RoutingInfo built from LiteLLM's own headers
    identically to one built from a Switchyard decision — no field is Switchyard-specific."""
    info = RoutingInfo(
        routed_model="claude-sonnet-5",
        tier="complex",
        decision_source="llm-classifier",
        routing_family="litellm",
        router_type="complexity",
    )
    headers = _routing_info_to_headers(info)
    assert headers["x-codemie-routed-model"] == "claude-sonnet-5"
    assert headers["x-codemie-routing-tier"] == "complex"
    assert headers["x-codemie-routing-decision-source"] == "llm-classifier"
    assert headers["x-codemie-routing-family"] == "litellm"
    assert headers["x-codemie-routing-router-type"] == "complexity"
