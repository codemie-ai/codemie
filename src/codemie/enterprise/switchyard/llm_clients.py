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

"""LlmClient implementations that libsy.algorithms.stage_router calls into.

_RoutingLlmClient is a stub used for the non-classifier "capable"/"efficient"
targets (the algorithm doesn't need a real response for those, only the
routing decision). _ClassifierLlmClient makes a real LLM call for the
"classifier" target and tracks its own token/cost usage.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import litellm
from pydantic import BaseModel

from codemie.configs import logger
from codemie.enterprise.switchyard.message_format import _neutral_to_litellm_messages

# litellm prints "Provider List: ..." to stdout for every unknown model name.
# We suppress that noise here; our own logging covers errors and decisions.
litellm.suppress_debug_info = True

# Base request shape expected by the Switchyard wire format.
# The `messages` key is replaced at call time with the real conversation history.
_BASE_REQUEST: dict[str, object] = {
    "model": "auto",
    "instructions": [],
    "messages": [],
    "tools": [],
    "sampling": {},
    "output": {},
    "reasoning": {},
    "stream": False,
}

# Dummy response returned by _RoutingLlmClient.
# The stage_router scorer reads ToolSignals from the REQUEST side only
# (tool call names + tool result error texts from message history).
# The response content and token counts do not affect the routing decision,
# so a fixed placeholder is sufficient.
_DUMMY_RESPONSE: dict[str, object] = {
    "id": "switchyard-routing",
    "model": "auto",
    "outputs": [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": ""}],
            "stop_reason": "end_turn",
        }
    ],
    "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
}


@dataclass
class _ClassifierUsage:
    """Token and cost totals from a single classifier invocation."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float | None = None
    called: bool = False
    classifier_used: bool = False  # set True before acompletion — survives cache/zero-token responses


class _RoutingLlmClient:
    """LlmClient stub that returns a fixed dummy response.

    The algorithm calls this when it needs to "complete" a model call during
    routing.  Since we only care about the routing decision (not the response),
    the client always returns _DUMMY_RESPONSE regardless of the request.
    """

    async def call(self, _request: Mapping[str, object]) -> Mapping[str, object]:
        return _DUMMY_RESPONSE


class _ClassifierLlmClient:
    """Single-use LLM client for one classifier invocation.

    Created fresh per pick_model call so usage state belongs to that call's
    local scope — no shared mutable state, no reset dance between calls.
    Routes through the LiteLLM proxy (LITE_LLM_URL) via litellm.acompletion.
    """

    def __init__(self, model: str) -> None:
        self._model = model
        self.usage: _ClassifierUsage = _ClassifierUsage()

    async def call(self, request: Mapping[str, object]) -> Mapping[str, object]:
        messages = _neutral_to_litellm_messages(request)
        response_format = self._extract_response_format(request)

        self.usage.classifier_used = True
        logger.info("[SWITCHYARD-PROXY] Classifier call: model=%s messages=%d", self._model, len(messages))
        response = await self._call_litellm(messages, response_format)

        raw_usage = getattr(response, "usage", None)
        self._record_usage(raw_usage)
        self._record_cost()
        self.usage.called = True

        return self._build_dummy_response(self._extract_text(response))

    @staticmethod
    def _extract_response_format(request: Mapping[str, object]) -> dict[str, object] | type[BaseModel] | None:
        output = request.get("output")
        response_format = output.get("response_format") if isinstance(output, dict) else None
        if isinstance(response_format, dict):
            return response_format
        if isinstance(response_format, type) and issubclass(response_format, BaseModel):
            return response_format
        return None

    async def _call_litellm(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, object] | type[BaseModel] | None,
    ) -> litellm.ModelResponse | litellm.CustomStreamWrapper:
        from codemie.configs import config as _app_config  # lazy import to avoid circular deps

        try:
            # Prefix "openai/" so litellm knows to use the OpenAI-compatible protocol.
            # The actual model name is preserved; the proxy routes it to the right backend.
            return await litellm.acompletion(
                model=f"openai/{self._model}",
                messages=messages,
                max_tokens=4096,
                api_base=_app_config.LITE_LLM_URL or None,
                api_key=_app_config.LITE_LLM_APP_KEY or None,
                response_format=response_format,
            )
        except Exception as exc:
            logger.warning("[SWITCHYARD-PROXY] Classifier call failed (model=%s): %s", self._model, exc)
            raise

    def _record_usage(self, raw_usage: object) -> None:
        if raw_usage is None:
            return
        self.usage.input_tokens = getattr(raw_usage, "prompt_tokens", None) or 0
        self.usage.output_tokens = getattr(raw_usage, "completion_tokens", None) or 0

        # Cache tokens — OpenAI format: usage.prompt_tokens_details.cached_tokens
        # Anthropic format (if ever used): usage.cache_read_input_tokens / cache_creation_input_tokens
        details = getattr(raw_usage, "prompt_tokens_details", None)
        self.usage.cached_tokens = (
            getattr(details, "cached_tokens", None) or 0
            if details
            else getattr(raw_usage, "cache_read_input_tokens", None) or 0
        )
        self.usage.cache_creation_tokens = getattr(raw_usage, "cache_creation_input_tokens", None) or 0

    def _record_cost(self) -> None:
        try:
            from codemie.core.utils import calculate_token_cost  # noqa: PLC0415
            from codemie.service.llm_service.llm_service import llm_service  # noqa: PLC0415

            cost_config = llm_service.get_model_cost(self._model)
            if cost_config is None:
                return
            total_cost, _, _ = calculate_token_cost(
                llm_model=self._model,
                cost_config=cost_config,
                input_tokens=self.usage.input_tokens,
                output_tokens=self.usage.output_tokens,
                cached_tokens=self.usage.cached_tokens,
                cache_creation_tokens=self.usage.cache_creation_tokens,
            )
            self.usage.cost_usd = total_cost
        except Exception as cost_exc:
            logger.debug("[SWITCHYARD-PROXY] Classifier cost calculation failed: %s", cost_exc)
            self.usage.cost_usd = None

    @staticmethod
    def _extract_text(response: object) -> str:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""
        msg_content = getattr(choices[0].message, "content", None)
        return msg_content if isinstance(msg_content, str) else ""

    def _build_dummy_response(self, text: str) -> dict[str, object]:
        return {
            "model": self._model,
            "outputs": [
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}],
                    "stop_reason": "end_turn",
                }
            ],
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
                "total_tokens": self.usage.input_tokens + self.usage.output_tokens,
            },
        }
