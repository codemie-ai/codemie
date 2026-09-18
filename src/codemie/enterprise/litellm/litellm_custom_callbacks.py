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

"""Custom LiteLLM proxy callbacks.

BedrockCostModelFixLogger: fix streaming cost model name.

``async_pre_call_deployment_hook`` fires AFTER deployment selection and the
``{**litellm_params, **kwargs}`` merge in the router, so ``store=False`` from a model
entry's ``litellm_params`` is visible here.  ``async_pre_call_hook`` fires earlier — before
that merge — and would only see ``store=False`` when the client sends it explicitly.

The proxy's SSE streaming cost injection computes cost from the client-facing alias
(``request_data["model"]``, e.g. ``claude-sonnet-5``) instead of the router-resolved
deployment (e.g. ``bedrock/us.anthropic.claude-sonnet-5``). For Bedrock models this hits the
Anthropic-direct price table and diverges from the dashboard cost (which is computed from the
resolved model via ``logging_obj``).

This callback runs ``async_post_call_streaming_iterator_hook`` *before* the proxy's cost
injection and rewrites ``request_data["model"]`` to the resolved deployment so both paths use
the same (correct) pricing. Spend/dashboard logging keys off ``logging_obj``, not
``request_data["model"]``, so the change is scoped to the streaming cost calculation.

AutorouterCallback: propagate routing-decision fields and classifier usage as response headers.

The complexity router issues 2 LLM calls per request:
  1. Classifier call  — ``internal_call_origin: "autorouter_classifier"`` in
     ``kwargs["litellm_params"]["metadata"]``; small token count, no routing_decision.
  2. Routed model call — full usage, routing_decision populated.

Routing decision headers: reads ``routing_decision`` from the main-call response metadata
and emits ``x-litellm-router-*`` headers so clients can inspect tier, cause, routed model, etc.

Classifier usage correlation flow:
  • ``async_pre_call_hook`` fires for the outer request and injects the proxy-level
    ``litellm_call_id`` (UUID-X, always set by the proxy) into ``data["metadata"]`` under
    ``_PROXY_CALL_ID_METADATA_KEY``.  The complexity router's ``_classifier_call_metadata``
    copies all metadata keys (except budget-reservation ones) into the classifier sub-call,
    so UUID-X flows into the classifier's ``litellm_params.metadata``.
  • Classifier fires ``async_log_success_event`` (via GLOBAL_LOGGING_WORKER, concurrent with
    the main-model LLM call) → reads UUID-X from its own metadata → stores usage in
    ``_pending_classifier_usage[UUID-X]``.
  • ``async_post_call_response_headers_hook`` fires (before HTTP 200) → looks up UUID-X via
    ``data["litellm_call_id"]`` → emits ``x-litellm-classifier-*`` headers alongside the
    routing-decision headers so the backend can include classifier tokens in usage totals.

Why this works for timing: the classifier completes before the main model, so its logging task
is enqueued to GLOBAL_LOGGING_WORKER first.  The worker runs those tasks concurrently during
the main model's long HTTP await, ensuring ``_pending_classifier_usage`` is populated before
the headers hook fires.

``_pending_classifier_usage`` is a process-local bounded, expiring cache (bounded size AND
time-based expiry), not a plain dict. A plain dict with only a size cap silently stops
recording ANY new classifier usage forever once it fills up with entries that are never popped
(e.g. requests that error out before ``async_post_call_response_headers_hook`` runs) — those
orphaned entries would occupy a slot indefinitely. The TTL means an orphaned entry self-expires
instead of permanently consuming one of the bounded slots.

Process locality is a non-issue for THIS correlation, not a limitation to work around: the
classifier sub-call is dispatched by the router as a plain in-process ``await`` inside the same
coroutine that is already handling the outer request (it calls the model provider directly, it
does not loop back over HTTP through this proxy's own external endpoint) — so
``async_log_success_event`` (classifier) and ``async_post_call_response_headers_hook`` (outer
call) are *structurally guaranteed* to run in the same OS process for a given request,
regardless of how many replicas/pods sit behind the load balancer. Replica count is irrelevant
here. The one config that WOULD break this is running this LiteLLM proxy with more than one
worker process per container (e.g. ``--num_workers`` / ``general_settings.workers`` > 1) — that
splits incoming requests across sibling processes that do not share this module's memory, and
*that* is genuinely a "would need Redis" scenario. Keep this proxy at a single worker per
process, or move to a shared store, if that ever needs to change.
"""

import contextlib
import datetime
import json
import time
from collections import OrderedDict
from typing import Any, AsyncGenerator, Dict, Optional
from urllib.parse import quote

from litellm.caching.dual_cache import DualCache
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy._types import UserAPIKeyAuth
from litellm.utils import CallTypes

# Key injected into the outer request's metadata by async_pre_call_hook so that
# the complexity router's _classifier_call_metadata() copies it into the classifier
# sub-call.  Used to correlate classifier usage with the outer request in callbacks.
_PROXY_CALL_ID_METADATA_KEY = "codemie_proxy_call_id"

_ROUTER_FIELD_TO_HEADER = {
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
_ROUTER_INT_FIELDS = {"classifier_prompt_tokens", "classifier_completion_tokens", "classifier_total_tokens"}
_ROUTER_FLOAT_FIELDS = {"score", "classifier_cost_usd"}
# Deliberately duplicated from codemie.core.routing_info._HEADER_SAFE_CHARS/encode_header_value
# rather than imported: this module is loaded directly by the LiteLLM proxy process as a
# CustomLogger config target (see litellm_config.yaml), so it must have zero import-time or
# runtime dependency on CodeMie's own source tree. Keep both charsets in sync if either changes.
_HEADER_SAFE_CHARS = " " + "".join(chr(code) for code in range(0x21, 0x7F) if chr(code) != "%")


def _routing_decision_headers(decision: dict[str, object]) -> dict[str, str]:
    """Serialize routing decisions without importing CodeMie-only domain modules."""
    headers: dict[str, str] = {}
    for field_name, header_name in _ROUTER_FIELD_TO_HEADER.items():
        value = decision.get(field_name)
        if value is None:
            continue
        if field_name == "signals":
            raw = json.dumps(value)
        elif field_name in _ROUTER_INT_FIELDS:
            if not isinstance(value, int):
                continue
            raw = str(value)
        elif field_name in _ROUTER_FLOAT_FIELDS:
            if not isinstance(value, (int, float)):
                continue
            raw = f"{float(value):.6g}"
        elif isinstance(value, str):
            raw = value
        else:
            continue
        headers[header_name] = quote(raw, safe=_HEADER_SAFE_CHARS)
    return headers


class BedrockCostModelFixLogger(CustomLogger):
    # ------------------------------------------------------------------ #
    # pre-call (post-deployment): strip reasoning items when store=False  #
    # ------------------------------------------------------------------ #

    async def async_pre_call_deployment_hook(
        self,
        kwargs: Dict[str, Any],
        call_type: Optional[CallTypes],
    ) -> Optional[dict]:
        """Strip encrypted reasoning items when store=False.

        When a model entry has ``litellm_params.store: false``, LiteLLM merges
        it into the request kwargs at this deployment-selection hook. Reasoning
        items with ``encrypted_content`` reference server-side state that was
        never persisted (``store=False`` on turn 1), so Azure rejects them with
        400 on turn 2+. This hook strips such items to make the turn stateless.
        """
        if kwargs.get("store") is not False:
            return kwargs

        input_items = kwargs.get("input")
        if not isinstance(input_items, list):
            return kwargs

        filtered = [
            item
            for item in input_items
            if not (isinstance(item, dict) and item.get("type") == "reasoning" and item.get("encrypted_content"))
        ]

        if 0 < len(filtered) < len(input_items):
            kwargs["input"] = filtered

        return kwargs

    # ------------------------------------------------------------------ #
    # post-call streaming: fix Bedrock cost model name                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _resolved_deployment(request_data: dict) -> Optional[str]:
        # New endpoints (e.g. /v1/messages) use "litellm_metadata"; others use "metadata".
        for key in ("litellm_metadata", "metadata"):
            md = request_data.get(key)
            if isinstance(md, dict):
                deployment = md.get("deployment")
                if isinstance(deployment, str) and deployment:
                    return deployment
        return None

    async def async_post_call_streaming_iterator_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        response: Any,
        request_data: dict,
    ) -> AsyncGenerator[Any, None]:
        deployment = self._resolved_deployment(request_data)
        if deployment:
            # Consumed downstream at common_request_processing.py -> cost injection.
            request_data["model"] = deployment
        async for chunk in response:
            yield chunk


def _extract_usage(response_obj: object) -> Optional[dict]:
    usage = getattr(response_obj, "usage", None) if response_obj else None
    if usage is None:
        return None
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


# Classifier usage pending header injection, keyed by the proxy-level litellm_call_id
# (UUID-X, injected into request metadata by async_pre_call_hook).
# Entries are normally popped by async_post_call_response_headers_hook; the TTL cleans up
# orphans (e.g. requests that errored before that hook ran) so they don't permanently occupy
# a slot in the bounded cache. See the module docstring for the correlation flow and the
# known limitation (process-local, does not survive multi-replica LiteLLM deployments).
class _ExpiringBoundedCache:
    def __init__(self, maxsize: int, ttl: float) -> None:
        self._maxsize = maxsize
        self._ttl = ttl
        self._entries: OrderedDict[str, tuple[float, dict]] = OrderedDict()

    def _purge_expired(self) -> None:
        now = time.monotonic()
        while self._entries:
            key, (expires_at, _) = next(iter(self._entries.items()))
            if expires_at > now:
                break
            del self._entries[key]

    def __setitem__(self, key: str, value: dict) -> None:
        self._purge_expired()
        self._entries.pop(key, None)
        while len(self._entries) >= self._maxsize:
            self._entries.popitem(last=False)
        self._entries[key] = (time.monotonic() + self._ttl, value)

    def pop(self, key: str, default: dict | None = None) -> dict | None:
        self._purge_expired()
        entry = self._entries.pop(key, None)
        return default if entry is None else entry[1]

    def __contains__(self, key: object) -> bool:
        self._purge_expired()
        return key in self._entries


_CLASSIFIER_USAGE_MAX_SIZE = 200
_CLASSIFIER_USAGE_TTL_SECONDS = 120
_pending_classifier_usage = _ExpiringBoundedCache(maxsize=_CLASSIFIER_USAGE_MAX_SIZE, ttl=_CLASSIFIER_USAGE_TTL_SECONDS)


def _build_pending_classifier_headers(call_id: str) -> dict[str, str]:
    """Pop pending classifier usage for *call_id* and convert it to response headers."""
    headers: dict[str, str] = {}
    classifier = _pending_classifier_usage.pop(call_id, None)
    if not classifier:
        return headers
    usage = classifier.get("usage") or {}
    for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
        val = usage.get(field)
        if val is not None:
            headers[f"x-litellm-classifier-{field.replace('_', '-')}"] = str(val)
    cost = classifier.get("cost")
    if cost is not None:
        headers["x-litellm-classifier-cost"] = str(cost)
    return headers


class AutorouterCallback(CustomLogger):
    """Propagate complexity-router routing decision and classifier usage as response headers."""

    @staticmethod
    def _extract_routing_decision(data: dict) -> Optional[dict]:
        for key in ("litellm_metadata", "metadata"):
            md = data.get(key)
            if isinstance(md, str):
                try:
                    md = json.loads(md)
                except ValueError:
                    return None
            if isinstance(md, dict):
                decision = md.get("routing_decision")
                if decision:
                    return decision
        return None

    @staticmethod
    def _read_kwargs_metadata(kwargs: dict, key: str) -> Optional[object]:
        # In async_log_success_event, kwargs is model_call_details.
        # Metadata lives at kwargs["litellm_params"]["metadata"], NOT kwargs["metadata"].
        for md in (
            (kwargs.get("litellm_params") or {}).get("metadata"),
            kwargs.get("metadata"),
            kwargs.get("litellm_metadata"),
        ):
            if isinstance(md, str):
                with contextlib.suppress(json.JSONDecodeError, ValueError):
                    md = json.loads(md)
            if isinstance(md, dict):
                val = md.get(key)
                if val is not None:
                    return val
        return None

    async def async_pre_call_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        cache: DualCache,
        data: dict,
        call_type: str,
    ) -> None:
        # Inject the proxy-level litellm_call_id (UUID-X, always set by the proxy) into
        # the request metadata under _PROXY_CALL_ID_METADATA_KEY.  The complexity router's
        # _classifier_call_metadata() copies all non-budget metadata keys into the classifier
        # sub-call, so UUID-X flows into the classifier's litellm_params.metadata.  We then
        # read it in async_log_success_event to store classifier usage under UUID-X, which
        # matches data["litellm_call_id"] in async_post_call_response_headers_hook.
        with contextlib.suppress(Exception):
            call_id = data.get("litellm_call_id")
            if not call_id:
                return
            for md_key in ("metadata", "litellm_metadata"):
                md = data.get(md_key)
                if isinstance(md, dict):
                    md[_PROXY_CALL_ID_METADATA_KEY] = call_id
                    return
            data["metadata"] = {_PROXY_CALL_ID_METADATA_KEY: call_id}

    async def async_log_success_event(
        self,
        kwargs: dict,
        response_obj: object,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
    ) -> None:
        with contextlib.suppress(Exception):
            proxy_call_id = AutorouterCallback._read_kwargs_metadata(kwargs, _PROXY_CALL_ID_METADATA_KEY)
            if AutorouterCallback._read_kwargs_metadata(kwargs, "internal_call_origin") != "autorouter_classifier":
                return
            if isinstance(proxy_call_id, str):
                # The bounded cache evicts the oldest entry rather than dropping the new one.
                _pending_classifier_usage[proxy_call_id] = {
                    "usage": _extract_usage(response_obj),
                    "cost": kwargs.get("response_cost"),
                }

    async def async_post_call_response_headers_hook(
        self,
        data: dict,
        user_api_key_dict,
        response: Any,
        request_headers=None,
        litellm_call_info=None,
    ) -> Optional[Dict[str, str]]:
        headers: Dict[str, str] = {}

        decision = self._extract_routing_decision(data)
        if decision:
            headers.update(_routing_decision_headers(decision))

        with contextlib.suppress(Exception):
            call_id = data.get("litellm_call_id")
            if call_id:
                headers.update(_build_pending_classifier_headers(call_id))

        # Whole-response cache hits are read off the logging object's ``caching_details``,
        # which ``LLMCachingHandler._update_litellm_logging_obj_environment`` populates
        # synchronously *before* the cached result is returned — so it is already set by the
        # time this hook builds headers.
        #
        # The response object itself cannot be used: /v1/messages returns an
        # ``AnthropicMessagesResponse``, a TypedDict, so it carries neither an ``id``
        # attribute nor ``_hidden_params``.  That is also why LiteLLM never emits
        # ``x-litellm-cache-key`` for this endpoint (see caching_handler's
        # ``hasattr(cached_result, "_hidden_params")`` guard), leaving the downstream
        # CodeMie proxy with no header to key off.
        with contextlib.suppress(Exception):
            caching_details = getattr(data.get("litellm_logging_obj"), "caching_details", None)
            if caching_details and caching_details.get("cache_hit") is True:
                headers["x-litellm-cache-hit"] = "true"

        return headers or None


autorouter_callback_instance = AutorouterCallback()
