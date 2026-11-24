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

"""Fail-open LangGraph pre-model hook that compresses the LLM input with Headroom."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import convert_to_openai_messages

from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.headroom_savings import HeadroomSavings
from codemie.core.otel_tracing import set_span_attribute, span
from codemie.core.utils import calculate_tokens
from codemie.service.headroom.factory import get_compressor, get_compressor_mode
from codemie.service.headroom.models import ResolvedHeadroomConfig
from codemie.service.request_summary_manager import RequestSummaryManager


def build_headroom_pre_model_hook(
    cfg: ResolvedHeadroomConfig | None, llm_model: str, request_id: str | None = None
) -> Callable[[dict[str, Any]], dict[str, Any]] | None:
    """Return a pre-model hook for the resolved config, or None when Headroom is off.

    The hook never raises: any failure is logged (exception type only) and the call proceeds unchanged.
    When `request_id` is given, every compression that saves tokens is reported to the request summary.
    """
    if cfg is None:
        return None

    def headroom_pre_model_hook(state: dict[str, Any]) -> dict[str, Any]:
        try:
            return _compress_state(state, cfg, llm_model, request_id)
        except Exception as exc:
            logger.warning(f"Headroom compression skipped ({type(exc).__name__}); using original messages")
            return {}

    return headroom_pre_model_hook


def _compress_state(
    state: dict[str, Any], cfg: ResolvedHeadroomConfig, llm_model: str, request_id: str | None
) -> dict[str, Any]:
    with span("headroom.compress"):
        try:
            return _compress_messages(state, cfg, llm_model, request_id)
        except Exception as exc:
            logger.warning(f"Headroom compression skipped ({type(exc).__name__}); using original messages")
            _record(cfg, "failed_open")
            return {}


def _compress_messages(
    state: dict[str, Any], cfg: ResolvedHeadroomConfig, llm_model: str, request_id: str | None
) -> dict[str, Any]:
    messages: list[BaseMessage] = list(state["messages"])
    local_tokens = _count_text_tokens(messages, llm_model)
    if local_tokens < config.HEADROOM_MIN_TOKENS:
        _record(cfg, "skipped_small", local_tokens, local_tokens)
        return {}

    request = convert_to_openai_messages([_text_only_copy(m) for m in messages])
    outcome = get_compressor().compress(request, llm_model, cfg)
    if len(outcome.messages) != len(messages):
        logger.warning("Headroom compression skipped (message count mismatch); using original messages")
        _record(cfg, "failed_open")
        return {}

    if 0 < outcome.tokens_before <= outcome.tokens_after:
        # The sidecar does not guard against "compression" that grows the input (the SDK does); keep the original.
        _record(cfg, "unchanged", outcome.tokens_before, outcome.tokens_after)
        return {}

    result = [
        _merge(original, compressed, cfg) for original, compressed in zip(messages, outcome.messages, strict=True)
    ]
    if all(new is old for new, old in zip(result, messages, strict=True)):
        # "not_applied": the compressor reported a saving but no message could take it (toggles, non-text content).
        _record(cfg, "not_applied", outcome.tokens_before, outcome.tokens_after)
        return {}
    _record(cfg, "compressed", outcome.tokens_before, outcome.tokens_after)
    _record_savings(request_id, cfg, local_tokens, _count_text_tokens(result, llm_model))
    return {"llm_input_messages": result}


def _record_savings(request_id: str | None, cfg: ResolvedHeadroomConfig, tokens_original: int, tokens_compacted: int):
    """Report the saving to the request summary, measured by recounting the merged messages locally.

    The recount uses the same counter for both modes and does not overstate the saving when part of the compressor's
    output was discarded. Nothing is recorded without a request id or without a saving; failures never propagate.
    """
    saved = tokens_original - tokens_compacted
    if request_id is None or saved <= 0:
        return
    try:
        RequestSummaryManager().add_headroom(
            request_id,
            HeadroomSavings(
                tokens_original=tokens_original,
                tokens_compacted=tokens_compacted,
                tokens_saved=saved,
                mode=get_compressor_mode(),
                strategy=cfg.strategy.value,
            ),
        )
    except Exception as exc:
        logger.warning(f"Headroom savings not recorded ({type(exc).__name__})")


def _record(cfg: ResolvedHeadroomConfig, outcome: str, tokens_before: int = 0, tokens_after: int = 0) -> None:
    """Set the span attributes and write the single info line; telemetry errors never propagate.

    Span duration is recorded by the span itself. `mode` is the configured mode, known without building a compressor.
    """
    try:
        fields = {
            "mode": get_compressor_mode(),
            "strategy": cfg.strategy.value,
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
            "outcome": outcome,
        }
        for key, value in fields.items():
            set_span_attribute(f"codemie.headroom.{key}", value)
        logger.info("headroom.compress " + " ".join(f"{key}={value}" for key, value in fields.items()))
    except Exception as exc:
        logger.warning(f"Headroom telemetry skipped ({type(exc).__name__})")


def _text_blocks(content: Any) -> list[str]:
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [_block_text(block) for block in content if _block_text(block)]
    return []


def _block_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
        return block["text"]
    return ""


def _count_text_tokens(messages: list[BaseMessage], llm_model: str) -> int:
    return sum(calculate_tokens(text, llm_model=llm_model) for m in messages for text in _text_blocks(m.content))


def _text_only_copy(message: BaseMessage) -> BaseMessage:
    """Copy with content reduced to its text; the message count and all other fields are preserved."""
    return message.model_copy(update={"content": "".join(_text_blocks(message.content))})


def _is_compressible(message: BaseMessage, cfg: ResolvedHeadroomConfig) -> bool:
    """Only human input and tool results may take compressed text.

    System prompts and earlier assistant replies always reach the model verbatim: the user refers back to the
    assistant's own words ("step 4 of your plan"), and the request carries them as plain strings, which bypass the
    SDK's own default protection of assistant text blocks.
    """
    if isinstance(message, HumanMessage):
        return cfg.compress_user_messages
    if isinstance(message, ToolMessage):
        return cfg.compress_tool_outputs
    return False


def _plain_text(content: Any) -> str | None:
    """The text of string content or of a list of bare text blocks; None when anything else is present."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.keys() == {"type", "text"} and block["type"] == "text":
            parts.append(block["text"] if isinstance(block["text"], str) else "")
        else:
            return None  # images, cache_control markers and other block kinds keep the original message
    return "".join(parts)


def _merge(original: BaseMessage, compressed: dict[str, Any], cfg: ResolvedHeadroomConfig) -> BaseMessage:
    """Return a copy of `original` carrying the compressed text, or `original` itself when nothing changes."""
    new_content = compressed.get("content")
    if not _is_compressible(original, cfg):
        return original
    original_text = _plain_text(original.content)
    if original_text is None or not isinstance(new_content, str):
        return original
    if new_content == original_text:
        return original
    return original.model_copy(update={"content": new_content})
