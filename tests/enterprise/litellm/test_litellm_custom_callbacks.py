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
import time
from unittest.mock import MagicMock

import pytest
from cachetools import TTLCache

from codemie.enterprise.litellm.litellm_custom_callbacks import (
    AutorouterCallback,
    _PROXY_CALL_ID_METADATA_KEY,
    _routing_decision_headers,
)


def _make_reasoning_item(encrypted_content="ZmFrZQ==", rs_id="rs_abc123"):
    return {"type": "reasoning", "id": rs_id, "encrypted_content": encrypted_content}


def _make_user_item(content="follow up"):
    return {"role": "user", "content": content}


def test_default_classifier_cache_expires_entries():
    from codemie.enterprise.litellm.litellm_custom_callbacks import _ExpiringBoundedCache

    cache = _ExpiringBoundedCache(maxsize=1, ttl=0)
    cache["call-1"] = {"usage": {}}

    assert "call-1" not in cache


@pytest.fixture()
def handler():
    from codemie.enterprise.litellm.litellm_custom_callbacks import BedrockCostModelFixLogger

    return BedrockCostModelFixLogger()


class TestAsyncPreCallDeploymentHookStoreFalse:
    """async_pre_call_deployment_hook strips reasoning items when store=False.

    This hook fires after deployment selection and the litellm_params merge, so
    store=False from a model entry's litellm_params is visible here.
    """

    @pytest.mark.asyncio
    async def test_strips_reasoning_items_when_store_false(self, handler):
        kwargs = {
            "store": False,
            "input": [_make_reasoning_item(), _make_user_item()],
        }
        result = await handler.async_pre_call_deployment_hook(kwargs, "responses")

        assert result["input"] == [_make_user_item()]

    @pytest.mark.asyncio
    async def test_strips_multiple_reasoning_items(self, handler):
        kwargs = {
            "store": False,
            "input": [
                _make_reasoning_item(rs_id="rs_1"),
                _make_user_item("first"),
                _make_reasoning_item(rs_id="rs_2"),
                _make_user_item("second"),
            ],
        }
        result = await handler.async_pre_call_deployment_hook(kwargs, "responses")

        assert result["input"] == [_make_user_item("first"), _make_user_item("second")]

    @pytest.mark.asyncio
    async def test_preserves_reasoning_items_without_encrypted_content(self, handler):
        """Items typed as reasoning but lacking encrypted_content are not stripped."""
        bare_reasoning = {"type": "reasoning", "id": "rs_bare"}
        kwargs = {
            "store": False,
            "input": [bare_reasoning, _make_user_item()],
        }
        result = await handler.async_pre_call_deployment_hook(kwargs, "responses")

        assert bare_reasoning in result["input"]

    @pytest.mark.asyncio
    async def test_noop_when_store_true(self, handler):
        original_input = [_make_reasoning_item(), _make_user_item()]
        kwargs = {"store": True, "input": list(original_input)}
        result = await handler.async_pre_call_deployment_hook(kwargs, "responses")

        assert result["input"] == original_input

    @pytest.mark.asyncio
    async def test_noop_when_store_absent(self, handler):
        original_input = [_make_reasoning_item(), _make_user_item()]
        kwargs = {"input": list(original_input)}
        result = await handler.async_pre_call_deployment_hook(kwargs, "responses")

        assert result["input"] == original_input

    @pytest.mark.asyncio
    async def test_noop_when_input_absent(self, handler):
        kwargs = {"store": False}
        result = await handler.async_pre_call_deployment_hook(kwargs, "responses")

        assert "input" not in result

    @pytest.mark.asyncio
    async def test_noop_when_input_empty(self, handler):
        kwargs = {"store": False, "input": []}
        result = await handler.async_pre_call_deployment_hook(kwargs, "responses")

        assert result["input"] == []

    @pytest.mark.asyncio
    async def test_noop_when_no_reasoning_items_present(self, handler):
        user_items = [_make_user_item("hello"), _make_user_item("world")]
        kwargs = {"store": False, "input": list(user_items)}
        result = await handler.async_pre_call_deployment_hook(kwargs, "responses")

        assert result["input"] == user_items

    @pytest.mark.asyncio
    async def test_call_type_none_also_strips(self, handler):
        """Hook is call-type-agnostic: stripping applies regardless of call_type."""
        kwargs = {
            "store": False,
            "input": [_make_reasoning_item(), _make_user_item()],
        }
        result = await handler.async_pre_call_deployment_hook(kwargs, None)

        assert result["input"] == [_make_user_item()]

    @pytest.mark.asyncio
    async def test_noop_when_all_items_are_reasoning(self, handler):
        """When stripping would empty the input list, leave it unchanged."""
        all_reasoning = [
            _make_reasoning_item(rs_id="rs_1"),
            _make_reasoning_item(rs_id="rs_2"),
        ]
        kwargs = {"store": False, "input": list(all_reasoning)}
        result = await handler.async_pre_call_deployment_hook(kwargs, "responses")

        assert result["input"] == all_reasoning

    @pytest.mark.asyncio
    async def test_store_false_from_litellm_params_is_visible(self, handler):
        """Simulate the primary use case: store=False merged from litellm_params."""
        litellm_params = {"store": False, "model": "azure/gpt-5.1-codex"}
        request_kwargs = {"input": [_make_reasoning_item(), _make_user_item()]}
        merged = {**litellm_params, **request_kwargs}

        result = await handler.async_pre_call_deployment_hook(merged, "responses")

        assert result["input"] == [_make_user_item()]


class TestResolvedDeployment:
    """_resolved_deployment extracts the router-resolved model from metadata."""

    def test_reads_from_litellm_metadata(self, handler):
        request_data = {"litellm_metadata": {"deployment": "bedrock/us.anthropic.claude-sonnet-5"}}
        assert handler._resolved_deployment(request_data) == "bedrock/us.anthropic.claude-sonnet-5"

    def test_reads_from_metadata_fallback(self, handler):
        request_data = {"metadata": {"deployment": "bedrock/eu.anthropic.claude-sonnet-5"}}
        assert handler._resolved_deployment(request_data) == "bedrock/eu.anthropic.claude-sonnet-5"

    def test_prefers_litellm_metadata_over_metadata(self, handler):
        request_data = {
            "litellm_metadata": {"deployment": "bedrock/first"},
            "metadata": {"deployment": "bedrock/second"},
        }
        assert handler._resolved_deployment(request_data) == "bedrock/first"

    def test_returns_none_when_absent(self, handler):
        assert handler._resolved_deployment({}) is None

    def test_returns_none_when_deployment_empty_string(self, handler):
        request_data = {"litellm_metadata": {"deployment": ""}}
        assert handler._resolved_deployment(request_data) is None


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


class TestComplexityRouterHeaders:
    def test_serializes_routing_decision_headers_without_domain_types(self):
        headers = _routing_decision_headers({"tier": "COMPLEX", "score": 0.75, "signals": ["llm-classifier:COMPLEX"]})

        assert headers == {
            "x-litellm-router-tier": "COMPLEX",
            "x-litellm-router-score": "0.75",
            "x-litellm-router-signals": '["llm-classifier:COMPLEX"]',
        }

    @pytest.mark.asyncio
    async def test_returns_all_headers_for_full_routing_decision(self, callback, full_routing_decision):
        data = {"metadata": {"routing_decision": full_routing_decision}}
        headers = await callback.async_post_call_response_headers_hook(
            data=data, user_api_key_dict=MagicMock(), response=MagicMock()
        )
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

        headers = await callback.async_post_call_response_headers_hook(
            data=data, user_api_key_dict=MagicMock(), response={"type": "message", "id": "msg_1"}
        )

        assert headers["x-litellm-cache-hit"] == "true"

    @pytest.mark.asyncio
    async def test_omits_cache_hit_header_when_not_cached(self, callback, full_routing_decision):
        data = {
            "metadata": {"routing_decision": full_routing_decision},
            "litellm_logging_obj": MagicMock(caching_details=None),
        }

        headers = await callback.async_post_call_response_headers_hook(
            data=data, user_api_key_dict=MagicMock(), response={"type": "message", "id": "msg_1"}
        )

        assert "x-litellm-cache-hit" not in headers

    @pytest.mark.asyncio
    async def test_prefers_litellm_metadata_over_metadata(self, callback, full_routing_decision):
        data = {
            "litellm_metadata": {"routing_decision": full_routing_decision},
            "metadata": {"routing_decision": {"tier": "SIMPLE"}},
        }
        headers = await callback.async_post_call_response_headers_hook(
            data=data, user_api_key_dict=MagicMock(), response=MagicMock()
        )
        assert headers["x-litellm-router-tier"] == "COMPLEX"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_routing_decision(self, callback):
        data = {"metadata": {}}
        headers = await callback.async_post_call_response_headers_hook(
            data=data, user_api_key_dict=MagicMock(), response=MagicMock()
        )
        assert headers is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_metadata(self, callback):
        data = {}
        headers = await callback.async_post_call_response_headers_hook(
            data=data, user_api_key_dict=MagicMock(), response=MagicMock()
        )
        assert headers is None

    @pytest.mark.asyncio
    async def test_skips_none_fields(self, callback):
        data = {"metadata": {"routing_decision": {"tier": "SIMPLE", "cause": None}}}
        headers = await callback.async_post_call_response_headers_hook(
            data=data, user_api_key_dict=MagicMock(), response=MagicMock()
        )
        assert "x-litellm-router-tier" in headers
        assert "x-litellm-router-cause" not in headers

    @pytest.mark.asyncio
    async def test_handles_metadata_as_json_string(self, callback, full_routing_decision):
        data = {"metadata": json.dumps({"routing_decision": full_routing_decision})}
        headers = await callback.async_post_call_response_headers_hook(
            data=data, user_api_key_dict=MagicMock(), response=MagicMock()
        )
        assert headers["x-litellm-router-tier"] == "COMPLEX"

    @pytest.mark.asyncio
    async def test_returns_none_on_invalid_json_string_metadata(self, callback):
        data = {"metadata": "not-valid-json"}
        headers = await callback.async_post_call_response_headers_hook(
            data=data, user_api_key_dict=MagicMock(), response=MagicMock()
        )
        assert headers is None

    @pytest.mark.asyncio
    async def test_signals_is_json_encoded_list(self, callback):
        data = {"metadata": {"routing_decision": {"signals": ["llm-classifier:COMPLEX", "extra"]}}}
        headers = await callback.async_post_call_response_headers_hook(
            data=data, user_api_key_dict=MagicMock(), response=MagicMock()
        )
        assert headers["x-litellm-router-signals"] == '["llm-classifier:COMPLEX", "extra"]'

    @pytest.mark.asyncio
    async def test_returns_none_when_routing_decision_is_empty_dict(self, callback):
        data = {"metadata": {"routing_decision": {}}}
        headers = await callback.async_post_call_response_headers_hook(
            data=data, user_api_key_dict=MagicMock(), response=MagicMock()
        )
        assert headers is None


class TestClassifierUsageCorrelation:
    """Correlation of classifier sub-call usage via the module-level TTLCache."""

    @pytest.fixture(autouse=True)
    def _isolated_cache(self, monkeypatch):
        """Each test gets its own cache so entries never leak across tests."""
        import codemie.enterprise.litellm.litellm_custom_callbacks as module

        cache: TTLCache = TTLCache(maxsize=200, ttl=120)
        monkeypatch.setattr(module, "_pending_classifier_usage", cache)
        return cache

    @staticmethod
    def _classifier_success_kwargs(proxy_call_id: str) -> dict:
        return {
            "litellm_params": {
                "metadata": {
                    "internal_call_origin": "autorouter_classifier",
                    _PROXY_CALL_ID_METADATA_KEY: proxy_call_id,
                }
            },
            "response_cost": 0.0007,
        }

    @staticmethod
    def _usage_response(prompt_tokens=100, completion_tokens=5, total_tokens=105):
        response = MagicMock()
        response.usage.prompt_tokens = prompt_tokens
        response.usage.completion_tokens = completion_tokens
        response.usage.total_tokens = total_tokens
        return response

    @pytest.mark.asyncio
    async def test_classifier_usage_flows_into_response_headers(self, callback):
        await callback.async_log_success_event(
            kwargs=self._classifier_success_kwargs("call-1"),
            response_obj=self._usage_response(),
            start_time=None,
            end_time=None,
        )

        headers = await callback.async_post_call_response_headers_hook(
            data={"litellm_call_id": "call-1"}, user_api_key_dict=MagicMock(), response=MagicMock()
        )

        assert headers["x-litellm-classifier-prompt-tokens"] == "100"
        assert headers["x-litellm-classifier-completion-tokens"] == "5"
        assert headers["x-litellm-classifier-total-tokens"] == "105"
        assert headers["x-litellm-classifier-cost"] == "0.0007"

    @pytest.mark.asyncio
    async def test_non_classifier_calls_are_ignored(self, callback, _isolated_cache):
        await callback.async_log_success_event(
            kwargs={"litellm_params": {"metadata": {_PROXY_CALL_ID_METADATA_KEY: "call-2"}}},
            response_obj=self._usage_response(),
            start_time=None,
            end_time=None,
        )
        assert "call-2" not in _isolated_cache

    @pytest.mark.asyncio
    async def test_headers_hook_pops_entry_so_it_is_used_once(self, callback, _isolated_cache):
        await callback.async_log_success_event(
            kwargs=self._classifier_success_kwargs("call-3"),
            response_obj=self._usage_response(),
            start_time=None,
            end_time=None,
        )
        assert "call-3" in _isolated_cache

        await callback.async_post_call_response_headers_hook(
            data={"litellm_call_id": "call-3"}, user_api_key_dict=MagicMock(), response=MagicMock()
        )
        assert "call-3" not in _isolated_cache

    @pytest.mark.asyncio
    async def test_orphaned_entry_expires_instead_of_permanently_occupying_a_slot(self, callback, monkeypatch):
        """The TTL is what a plain dict + size cap could not provide: an entry from a request
        that errors out before the headers hook runs must eventually free its slot on its own.
        """
        import codemie.enterprise.litellm.litellm_custom_callbacks as module

        short_lived_cache: TTLCache = TTLCache(maxsize=200, ttl=0.05)
        monkeypatch.setattr(module, "_pending_classifier_usage", short_lived_cache)

        await callback.async_log_success_event(
            kwargs=self._classifier_success_kwargs("orphan"),
            response_obj=self._usage_response(),
            start_time=None,
            end_time=None,
        )
        assert "orphan" in short_lived_cache

        time.sleep(0.1)

        # Never popped by the headers hook (simulating a request that errored earlier),
        # yet it is gone on its own — no permanent slot consumption, no manual cleanup.
        assert "orphan" not in short_lived_cache
