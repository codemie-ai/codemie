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

"""Tests for litellm_custom_callbacks.py."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import unquote

import pytest

from codemie.enterprise.litellm.litellm_custom_callbacks import (
    AutorouterCallback,
    _routing_decision_headers,
)

_BASELINE_GROUP_HEADER = "x-litellm-router-savings-baseline-model-group"


@pytest.fixture
def callback():
    return AutorouterCallback()


@pytest.fixture
def full_routing_decision():
    return {
        "tier": "COMPLEX",
        "cause": "llm_classifier",
        "score": 0.75,
        "routed_model": "claude-sonnet-5",
        "classifier_model": "claude-haiku-4-5-20251001",
        "router_model_name": "claude-only-simple-no-aff",
        "router_type": "complexity",
        "signals": ["llm-classifier:COMPLEX"],
    }


@pytest.fixture
def llm_router(monkeypatch):
    """The proxy's module-level router, which the callback reads lazily. Proxy extras are not
    installed in the backend environment, so the module is stood in for directly."""
    router = MagicMock()
    monkeypatch.setitem(sys.modules, "litellm.proxy.proxy_server", SimpleNamespace(llm_router=router))
    return router


async def _headers(callback, data):
    return await callback.async_post_call_response_headers_hook(
        data=data, user_api_key_dict=MagicMock(), response=MagicMock()
    )


class TestRoutingDecisionHeaders:
    def test_serializes_routing_decision_headers_without_domain_types(self):
        headers = _routing_decision_headers({"tier": "COMPLEX", "score": 0.75, "signals": ["llm-classifier:COMPLEX"]})

        assert headers == {
            "x-litellm-router-tier": "COMPLEX",
            "x-litellm-router-score": "0.75",
            "x-litellm-router-signals": '["llm-classifier:COMPLEX"]',
        }

    def test_emits_only_fields_codemie_reads(self):
        """classifier_cost is sent by LiteLLM natively; the rest are not read by CodeMie, and
        escalation_keyword would copy prompt text into a response header."""
        headers = _routing_decision_headers(
            {
                "tier": "SIMPLE",
                "classifier_cost": 0.0011066,
                "escalated": True,
                "escalation_keyword": "prove",
                "conversation_continuing": True,
                "savings_baseline_model": "bedrock/us.anthropic.claude-opus-5",
                "savings_baseline_deployment_id": "claude-opus-5-us-west-2",
            }
        )

        assert headers == {"x-litellm-router-tier": "SIMPLE"}

    def test_percent_encodes_non_ascii_values_reversibly(self):
        headers = _routing_decision_headers({"cause": "правило"})

        assert headers["x-litellm-router-cause"].isascii()
        assert unquote(headers["x-litellm-router-cause"]) == "правило"

    @pytest.mark.parametrize("score", ["0.75", True, None])
    def test_skips_non_numeric_score(self, score):
        assert "x-litellm-router-score" not in _routing_decision_headers({"score": score})

    def test_skips_non_string_values(self):
        assert _routing_decision_headers({"tier": 3, "routed_model": ["x"]}) == {}


class TestResponseHeadersHook:
    @pytest.mark.asyncio
    async def test_returns_all_headers_for_full_routing_decision(self, callback, full_routing_decision):
        headers = await _headers(callback, {"metadata": {"routing_decision": full_routing_decision}})

        assert headers["x-litellm-router-tier"] == "COMPLEX"
        assert headers["x-litellm-router-cause"] == "llm_classifier"
        assert headers["x-litellm-router-score"] == "0.75"
        assert headers["x-litellm-router-routed-model"] == "claude-sonnet-5"
        assert headers["x-litellm-router-classifier-model"] == "claude-haiku-4-5-20251001"
        assert headers["x-litellm-router-model-name"] == "claude-only-simple-no-aff"
        assert headers["x-litellm-router-type"] == "complexity"
        assert headers["x-litellm-router-signals"] == json.dumps(["llm-classifier:COMPLEX"])

    @pytest.mark.asyncio
    async def test_propagates_response_cache_hit(self, callback, full_routing_decision):
        # Cache hits are read off the logging object's ``caching_details``, which LiteLLM
        # populates before returning the cached result. The response object cannot be used:
        # /v1/messages returns a TypedDict with no ``id``/``_hidden_params``.
        data = {
            "metadata": {"routing_decision": full_routing_decision},
            "litellm_logging_obj": MagicMock(caching_details={"cache_hit": True, "cache_duration_ms": 0.1}),
        }

        headers = await _headers(callback, data)

        assert headers["x-litellm-cache-hit"] == "true"

    @pytest.mark.asyncio
    async def test_omits_cache_hit_header_when_not_cached(self, callback, full_routing_decision):
        data = {
            "metadata": {"routing_decision": full_routing_decision},
            "litellm_logging_obj": MagicMock(caching_details=None),
        }

        headers = await _headers(callback, data)

        assert "x-litellm-cache-hit" not in headers

    @pytest.mark.asyncio
    async def test_prefers_litellm_metadata_over_metadata(self, callback, full_routing_decision):
        data = {
            "litellm_metadata": {"routing_decision": full_routing_decision},
            "metadata": {"routing_decision": {"tier": "SIMPLE"}},
        }

        headers = await _headers(callback, data)

        assert headers["x-litellm-router-tier"] == "COMPLEX"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "data",
        [
            {},
            {"metadata": {}},
            {"metadata": {"routing_decision": {}}},
            # The router always writes into a dict bucket, so a string never holds a real decision.
            {"metadata": json.dumps({"routing_decision": {"tier": "COMPLEX"}})},
        ],
        ids=["no-metadata", "no-decision", "empty-decision", "metadata-json-string"],
    )
    async def test_returns_none_without_a_routing_decision(self, callback, data):
        assert await _headers(callback, data) is None

    @pytest.mark.asyncio
    async def test_skips_none_fields(self, callback):
        headers = await _headers(callback, {"metadata": {"routing_decision": {"tier": "SIMPLE", "cause": None}}})

        assert "x-litellm-router-tier" in headers
        assert "x-litellm-router-cause" not in headers


class TestSavingsBaselineModelGroup:
    @pytest.mark.asyncio
    async def test_resolves_baseline_deployment_to_its_model_group(self, callback, llm_router):
        llm_router.get_deployment.return_value = SimpleNamespace(model_name="claude-opus-5")
        decision = {"tier": "SIMPLE", "savings_baseline_deployment_id": "claude-opus-5-us-west-2"}

        headers = await _headers(callback, {"metadata": {"routing_decision": decision}})

        llm_router.get_deployment.assert_called_once_with(model_id="claude-opus-5-us-west-2")
        assert headers[_BASELINE_GROUP_HEADER] == "claude-opus-5"
        assert "x-litellm-router-savings-baseline-deployment-id" not in headers

    @pytest.mark.asyncio
    async def test_omits_header_for_unknown_deployment(self, callback, llm_router):
        llm_router.get_deployment.return_value = None
        decision = {"tier": "SIMPLE", "savings_baseline_deployment_id": "gone"}

        headers = await _headers(callback, {"metadata": {"routing_decision": decision}})

        assert _BASELINE_GROUP_HEADER not in headers
        assert headers["x-litellm-router-tier"] == "SIMPLE"

    @pytest.mark.asyncio
    async def test_skips_lookup_without_baseline_deployment_id(self, callback, llm_router):
        """An operator-configured baseline carries no deployment id; CodeMie then falls back to
        the catalog counterfactual_model."""
        headers = await _headers(callback, {"metadata": {"routing_decision": {"tier": "SIMPLE"}}})

        llm_router.get_deployment.assert_not_called()
        assert _BASELINE_GROUP_HEADER not in headers

    @pytest.mark.asyncio
    async def test_omits_header_when_proxy_router_not_initialised(self, callback, monkeypatch):
        monkeypatch.setitem(sys.modules, "litellm.proxy.proxy_server", SimpleNamespace(llm_router=None))
        decision = {"tier": "SIMPLE", "savings_baseline_deployment_id": "claude-opus-5-us-west-2"}

        headers = await _headers(callback, {"metadata": {"routing_decision": decision}})

        assert headers == {"x-litellm-router-tier": "SIMPLE"}


class TestFailureIsolation:
    """LiteLLM wraps its whole callback loop in one try, so a raise here would also drop every
    later callback's headers. Each step must fail on its own."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("decision", [["x"], "oops", 42])
    async def test_non_dict_decision_is_ignored(self, callback, decision):
        data = {
            "metadata": {"routing_decision": decision},
            "litellm_logging_obj": MagicMock(caching_details={"cache_hit": True}),
        }

        assert await _headers(callback, data) == {"x-litellm-cache-hit": "true"}

    @pytest.mark.asyncio
    async def test_unserializable_signals_drop_only_that_header(self, callback):
        decision = {"tier": "SIMPLE", "signals": {object()}}

        headers = await _headers(callback, {"metadata": {"routing_decision": decision}})

        assert headers == {"x-litellm-router-tier": "SIMPLE"}

    @pytest.mark.asyncio
    async def test_baseline_lookup_error_keeps_routing_headers(self, callback, llm_router):
        llm_router.get_deployment.side_effect = RuntimeError("boom")
        decision = {"tier": "SIMPLE", "savings_baseline_deployment_id": "claude-opus-5-us-west-2"}

        headers = await _headers(callback, {"metadata": {"routing_decision": decision}})

        assert headers == {"x-litellm-router-tier": "SIMPLE"}

    @pytest.mark.asyncio
    async def test_broken_request_data_returns_none_instead_of_raising(self, callback):
        class _BrokenData(dict):
            def get(self, *args):
                raise RuntimeError("boom")

        assert await _headers(callback, _BrokenData()) is None
