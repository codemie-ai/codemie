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

"""LiteLLM proxy callback exposing the complexity router's decision as response headers.

Runs inside the LiteLLM proxy (``litellm_settings.callbacks``), not in CodeMie. Everything
comes from the ``routing_decision`` the router stamps on request metadata before the call,
so it is ready when the headers hook runs, streaming responses included.

Only what CodeMie reads (see ``LiteLLMRouterHeaders``) is emitted:
- the routing fields in ``_STR_FIELD_TO_HEADER`` plus score and signals;
- ``x-litellm-router-savings-baseline-model-group``: the savings baseline deployment
  resolved to its model group, the name CodeMie prices the counterfactual under;
- ``x-litellm-cache-hit`` for whole-response cache hits.

``x-litellm-classifier-cost`` is not emitted here: LiteLLM sends it natively from the same
``routing_decision.classifier_cost``.
"""

import contextlib
import json
from urllib.parse import quote

from litellm.integrations.custom_logger import CustomLogger

_STR_FIELD_TO_HEADER = {
    "tier": "x-litellm-router-tier",
    "cause": "x-litellm-router-cause",
    "routed_model": "x-litellm-router-routed-model",
    "classifier_model": "x-litellm-router-classifier-model",
    "router_model_name": "x-litellm-router-model-name",
    "router_type": "x-litellm-router-type",
}
_SCORE_HEADER = "x-litellm-router-score"
_SIGNALS_HEADER = "x-litellm-router-signals"
_BASELINE_MODEL_GROUP_HEADER = "x-litellm-router-savings-baseline-model-group"
_CACHE_HIT_HEADER = "x-litellm-cache-hit"
# Printable ASCII minus '%': values stay valid latin-1 and reversible via urllib.parse.unquote.
_HEADER_SAFE_CHARS = " " + "".join(chr(code) for code in range(0x21, 0x7F) if chr(code) != "%")


def _encode(raw: str) -> str:
    return quote(raw, safe=_HEADER_SAFE_CHARS)


def _routing_decision(data: dict) -> dict | None:
    """The router's decision for this request. The router always writes it into a dict
    bucket (``litellm_metadata`` when present, else ``metadata``)."""
    for key in ("litellm_metadata", "metadata"):
        metadata = data.get(key)
        if isinstance(metadata, dict):
            decision = metadata.get("routing_decision")
            if isinstance(decision, dict) and decision:
                return decision
    return None


def _routing_decision_headers(decision: dict) -> dict[str, str]:
    headers: dict[str, str] = {}
    for field_name, header_name in _STR_FIELD_TO_HEADER.items():
        value = decision.get(field_name)
        if isinstance(value, str):
            headers[header_name] = _encode(value)
    score = decision.get("score")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        headers[_SCORE_HEADER] = f"{float(score):.6g}"
    signals = decision.get("signals")
    if signals is not None:
        with contextlib.suppress(TypeError, ValueError):
            headers[_SIGNALS_HEADER] = _encode(json.dumps(signals))
    return headers


def _deployment_model_group(deployment_id: object) -> str | None:
    """Model group name of the proxy deployment *deployment_id*, or None when unknown."""
    if not isinstance(deployment_id, str) or not deployment_id:
        return None
    from litellm.proxy.proxy_server import llm_router

    if llm_router is None:
        return None
    deployment = llm_router.get_deployment(model_id=deployment_id)
    model_name = getattr(deployment, "model_name", None)
    return model_name if isinstance(model_name, str) and model_name else None


class AutorouterCallback(CustomLogger):
    """Propagate the complexity-router routing decision as response headers."""

    async def async_post_call_response_headers_hook(
        self,
        data: dict,
        user_api_key_dict,
        response,
        request_headers=None,
        litellm_call_info=None,
    ) -> dict[str, str] | None:
        # LiteLLM catches a raise around its whole callback loop, which would also drop every
        # later callback's headers, so each step fails on its own instead.
        headers: dict[str, str] = {}
        decision: dict | None = None
        with contextlib.suppress(Exception):
            decision = _routing_decision(data)
        if decision:
            with contextlib.suppress(Exception):
                headers.update(_routing_decision_headers(decision))
            with contextlib.suppress(Exception):
                model_group = _deployment_model_group(decision.get("savings_baseline_deployment_id"))
                if model_group:
                    headers[_BASELINE_MODEL_GROUP_HEADER] = _encode(model_group)

        with contextlib.suppress(Exception):
            caching_details = getattr(data.get("litellm_logging_obj"), "caching_details", None)
            if caching_details and caching_details.get("cache_hit") is True:
                headers[_CACHE_HIT_HEADER] = "true"

        return headers or None


autorouter_callback_instance = AutorouterCallback()
