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

"""Message-format conversion for the Switchyard routing engine.

Converts Anthropic/OpenAI wire-format messages to and from the neutral shape
``libsy.algorithms.stage_router`` expects, and handles the Claude Code
compaction marker. Pure data transformation — no litellm/libsy calls here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

# Claude Code injects this marker when it compacts an overflowed context.
# The Switchyard algorithm self-latches on it (treats every subsequent turn as
# post-compaction). We preserve it only in the most recent window so that
# escalation fires for the first few turns after compaction and then decays.
_COMPACTION_MARKER: str = "session is being continued"
_COMPACTION_PRESERVE_WINDOW: int = 5


def _strip_old_compaction_markers(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    """Remove compaction marker text blocks from messages outside the preserve window.

    Keeps the marker in the last _COMPACTION_PRESERVE_WINDOW messages so the
    algorithm escalates to capable for those turns, then naturally reverts to
    signal-based routing.
    """
    if len(messages) <= _COMPACTION_PRESERVE_WINDOW:
        return messages
    cutoff = len(messages) - _COMPACTION_PRESERVE_WINDOW
    result: list[dict[str, object]] = []
    for i, msg in enumerate(messages):
        if i >= cutoff:
            result.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            result.append(msg)
            continue
        stripped = [
            block
            for block in content
            if not (
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
                and _COMPACTION_MARKER in block["text"].lower()
            )
        ]
        result.append({**msg, "content": stripped} if len(stripped) != len(content) else msg)
    return result


def _has_recent_compaction_marker(messages: list[dict[str, object]]) -> bool:
    """Return True if the compaction marker appears in the most recent preserve window."""
    window = messages[-_COMPACTION_PRESERVE_WINDOW:] if len(messages) > _COMPACTION_PRESERVE_WINDOW else messages
    for msg in window:
        content = msg.get("content")
        if isinstance(content, str) and _COMPACTION_MARKER in content.lower():
            return True
        if isinstance(content, list):
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                    and _COMPACTION_MARKER in block["text"].lower()
                ):
                    return True
    return False


def _extract_text_from_content(content: object) -> str:
    """Return a single joined text string from a neutral content value."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text")
                if isinstance(t, str) and t:
                    parts.append(t)
        return " ".join(parts)
    return ""


def _neutral_to_litellm_messages(request: Mapping[str, object]) -> list[dict[str, str]]:
    """Convert a Switchyard neutral request to a flat list of OpenAI-compatible messages."""
    messages: list[dict[str, str]] = []
    instructions = request.get("instructions")
    for instr in instructions if isinstance(instructions, list) else []:
        if not isinstance(instr, dict):
            continue
        text = _extract_text_from_content(instr.get("content"))
        if text:
            messages.append({"role": "system", "content": text})
    raw_messages = request.get("messages")
    for msg in raw_messages if isinstance(raw_messages, list) else []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        text = _extract_text_from_content(msg.get("content"))
        if text:
            messages.append({"role": str(role), "content": text})
    return messages


def _tool_result_message(msg: dict[str, object]) -> dict[str, object]:
    """OpenAI tool-result message: {"role": "tool", "tool_call_id": ..., "content": ...}."""
    raw_content = msg.get("content")
    text = raw_content if isinstance(raw_content, str) else json.dumps(raw_content)
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_call_id": msg.get("tool_call_id"),
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


def _convert_tool_use_block(block: dict[str, object]) -> dict[str, object]:
    """Anthropic ``tool_use`` block → neutral ``tool_call`` (rename ``input`` → ``arguments``)."""
    return {
        "type": "tool_call",
        "id": block.get("id"),
        "name": block.get("name"),
        "arguments": block.get("input", {}),
    }


def _convert_tool_result_block(block: dict[str, object]) -> dict[str, object]:
    """Anthropic ``tool_result`` block → neutral ``tool_result`` (rename ``tool_use_id`` → ``tool_call_id``)."""
    inner = block.copy()
    if "tool_use_id" in inner:
        inner["tool_call_id"] = inner.pop("tool_use_id")
    # Normalize content: algorithm expects a sequence, not a bare string.
    raw_content = inner.get("content")
    content_blocks: list[object]
    if isinstance(raw_content, str):
        content_blocks = [{"type": "text", "text": raw_content}]
    elif raw_content is None:
        content_blocks = []
    else:
        # Anthropic tool_result content, when not a bare string, is already a list of
        # content blocks per the wire format (same invariant _neutral_content_blocks relies on).
        content_blocks = cast("list[object]", raw_content)
    # is_error: true → inject SOFT-severity sentinel so the algorithm treats
    # the result as a failed tool call (equivalent to "exit_nonzero").
    # The Switchyard error pattern list doesn't include EISDIR or other OS-level
    # errors surfaced via the is_error flag, so we bridge that gap here.
    if inner.get("is_error"):
        content_blocks = [*content_blocks, {"type": "text", "text": "exit_nonzero"}]
    inner["content"] = content_blocks
    return inner


def _convert_content_block(block: dict[str, object]) -> dict[str, object]:
    block_type = block.get("type")
    if block_type == "tool_use":
        return _convert_tool_use_block(block)
    if block_type == "tool_result":
        return _convert_tool_result_block(block)
    if block_type == "thinking":
        # Claude extended-thinking block → neutral reasoning block.
        # Switchyard's ContentBlock enum does not include "thinking"; map it to
        # "reasoning" so the Rust deserializer doesn't fail on the next turn.
        return {"type": "reasoning", "text": block.get("thinking", "")}
    return block


def _oai_tool_call_block(tool_call: dict[str, object]) -> dict[str, object]:
    """OpenAI ``tool_calls`` entry → inline neutral ``tool_call`` block."""
    fn = tool_call.get("function")
    fn = fn if isinstance(fn, dict) else {}
    raw_args = fn.get("arguments") or "{}"
    try:
        arguments: object = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except (json.JSONDecodeError, TypeError):
        arguments = {}
    return {
        "type": "tool_call",
        "id": tool_call.get("id"),
        "name": fn.get("name"),
        "arguments": arguments,
    }


def _neutral_content_blocks(msg: dict[str, object], content: list[object]) -> list[dict[str, object]]:
    # Every block in an Anthropic/OpenAI message's content list is itself a dict — this is
    # a real wire-format invariant enforced by the callers of this module, not a defensive
    # guess, so the cast documents that assumption rather than papering over the wrong shape.
    new_blocks = [_convert_content_block(cast("dict[str, object]", block)) for block in content]

    # OpenAI assistant message: tool calls in separate ``tool_calls`` field.
    oai_tool_calls = msg.get("tool_calls")
    if isinstance(oai_tool_calls, list):
        new_blocks.extend(_oai_tool_call_block(cast("dict[str, object]", tc)) for tc in oai_tool_calls)
    return new_blocks


def _to_neutral_messages(messages: list[dict[str, object]]) -> list[dict[str, object]]:
    """Convert Anthropic or OpenAI messages to Switchyard neutral format.

    The libsy.algorithms.stage_router expects content blocks typed as
    ``tool_call`` / ``tool_result`` (neutral), not Anthropic's ``tool_use``
    or OpenAI's separate ``tool_calls`` field.

    Conversions applied:
    - Anthropic ``tool_use`` block → neutral ``tool_call`` (rename ``input`` → ``arguments``)
    - Anthropic ``tool_result`` block → neutral ``tool_result``
      (rename ``tool_use_id`` → ``tool_call_id``)
    - OpenAI ``tool_calls`` field on assistant msg → inline ``tool_call`` blocks
    - OpenAI ``role: tool`` message → ``role: user`` with ``tool_result`` block
    """
    result: list[dict[str, object]] = []
    for msg in messages:
        role = msg.get("role")
        if role == "tool":
            result.append(_tool_result_message(msg))
            continue

        content = msg.get("content")
        if isinstance(content, str):
            # Plain-string content (e.g. compaction summary) — wrap as a text block
            # so Switchyard sees ContentBlock::Text and can detect the compaction marker.
            result.append({**msg, "content": [{"type": "text", "text": content}]})
            continue
        if not isinstance(content, list):
            result.append(msg)
            continue

        result.append({**msg, "content": _neutral_content_blocks(msg, content)})

    return result
