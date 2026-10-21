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

"""Single policy point for the assistant-level "hide tool outputs" flag."""

import json
import re
from typing import Any, AsyncIterator, Iterable, Iterator

from starlette.responses import StreamingResponse

from codemie.chains.base import Thought
from codemie.configs import logger
from codemie.rest_api.models.assistant import Assistant

# A chunk carrying any of these must survive even when its thought is removed.
_GUARDRAILS_ERROR = "guardrails"
_PAYLOAD_KEYS = ("generated", "generated_chunk", "a2ui", "tool_call_pending", "agent_error", "execution_error", "last")


# The only thought fields a pending tool-call approval needs: tool name and arguments.
_APPROVAL_THOUGHT_KEYS = ("id", "author_name", "author_type", "input_text", "message")


def _is_interrupted(thought: Any) -> bool:
    if isinstance(thought, dict):
        return bool(thought.get("interrupted"))
    return bool(getattr(thought, "interrupted", False))


def _approval_thought(thought: dict) -> dict:
    """Reduce an interrupted thought to what the UI needs for Allow/Deny; drops metadata and children."""
    reduced = {k: thought.get(k) for k in _APPROVAL_THOUGHT_KEYS if k in thought}
    reduced.update(interrupted=True, in_progress=False)
    return reduced


def _approval_thoughts(thoughts: Iterable[Any] | None) -> list:
    """Interrupted thoughts of a stored/returned message, reduced for approval; everything else is dropped."""
    return [
        Thought.model_validate(_approval_thought(t if isinstance(t, dict) else t.model_dump()))
        for t in thoughts or []
        if _is_interrupted(t)
    ]


def is_hidden(assistant: Any) -> bool:
    return bool(getattr(assistant, "hide_tool_outputs", False))


def _serialize(data: dict, line: str) -> str:
    return json.dumps(data) + ("\n" if line.endswith("\n") else "")


def _null_final_tool_errors(data: dict) -> None:
    if data.get("last") and "tool_errors" in data:
        data["tool_errors"] = None


def _strip_line(line: str) -> str | None:
    text = line.strip()
    if not text:
        return line
    try:
        data = json.loads(text)
    except ValueError:
        # Unparseable line: cannot verify it is free of hidden thought content, so drop it
        # rather than let it pass through unfiltered.
        return None
    if not isinstance(data, dict):
        return None
    thought = data.get("thought")
    if isinstance(thought, dict) and _is_interrupted(thought):
        # A pending tool-call confirmation: the UI shows Allow/Deny only for an interrupted thought,
        # so it must survive, reduced to the tool name and arguments.
        data["thought"] = _approval_thought(thought)
        data["debug"] = None
        _null_final_tool_errors(data)
        return _serialize(data, line)
    if thought and not any(data.get(k) for k in _PAYLOAD_KEYS):
        return None
    _surface_security_error(data, thought)
    for key in ("thought", "debug"):
        if key in data:
            data[key] = None
    _null_final_tool_errors(data)
    return _serialize(data, line)


def _surface_security_error(data: dict, thought: Any) -> None:
    """A guardrail block is reported only through a thought; keep its message visible whenever it
    appears, not only on the final chunk (on_llm_error can emit it mid-stream with last=False).
    Any other error thought (e.g. a tool exception) carries tool output and stays hidden."""
    if (
        data.get("execution_error") == _GUARDRAILS_ERROR
        and isinstance(thought, dict)
        and thought.get("error")
        and not data.get("generated")
        and thought.get("message")
    ):
        data["generated"] = thought["message"]


def strip_chunk(chunk: str | bytes) -> str | bytes | None:
    """Strip tool details from an NDJSON chunk. Returns None when the whole chunk must be dropped."""
    is_bytes = isinstance(chunk, bytes)
    text = chunk.decode() if is_bytes else chunk
    # Split on "\n" only: str.splitlines() also breaks on U+2028/U+2029/\x85 etc., which may appear raw inside JSON
    # strings and would split one JSON line into unparseable halves that pass through unstripped.
    lines = [raw for raw in re.split(r"(?<=\n)", text) if raw]
    kept = [out for raw in lines if (out := _strip_line(raw)) is not None]
    if not kept:
        return None
    joined = "".join(kept)
    return joined.encode() if is_bytes else joined


async def _strip_async_iter(iterator) -> AsyncIterator:
    """Apply strip_chunk to each chunk of an async iterator, closing it afterward."""
    try:
        async for chunk in iterator:
            if (out := strip_chunk(chunk)) is not None:
                yield out
    finally:
        aclose = getattr(iterator, "aclose", None)
        if aclose:
            await aclose()


def _strip_sync_iter(iterator) -> Iterator:
    """Apply strip_chunk to each chunk of a sync iterator, closing it afterward."""
    try:
        for chunk in iterator:
            if (out := strip_chunk(chunk)) is not None:
                yield out
    finally:
        close = getattr(iterator, "close", None)
        if close:
            close()


def strip_stream(iterator):
    """Wrap a sync or async iterator of NDJSON chunks, applying strip_chunk to each."""
    if hasattr(iterator, "__aiter__"):
        return _strip_async_iter(iterator)
    return _strip_sync_iter(iterator)


def strip_response(response: Any) -> Any:
    """Unconditionally strip tool details from a StreamingResponse or BaseModelResponse."""
    if isinstance(response, StreamingResponse):
        response.body_iterator = strip_stream(response.body_iterator)
        return response
    if not hasattr(type(response), "model_fields"):
        logger.error(f"Cannot strip tool outputs from unsupported response type {type(response).__name__}")
        return response
    fields = type(response).model_fields
    update = {}
    if "thoughts" in fields:
        update["thoughts"] = _approval_thoughts(getattr(response, "thoughts", None))
    if "tool_errors" in fields:
        update["tool_errors"] = None
    return response.model_copy(update=update) if update else response


def apply_to_response(assistant: Any, response: Any) -> Any:
    """Strip tool details from a StreamingResponse or BaseModelResponse; no-op when not hidden."""
    return strip_response(response) if is_hidden(assistant) else response


def _hidden_ids(assistant_ids: Iterable[str | None]) -> set[str]:
    ids = sorted({i for i in assistant_ids if i})
    if not ids:
        return set()
    resolved = {a.id: is_hidden(a) for a in Assistant.get_by_ids_no_permission_check(ids)}
    # An id that does not resolve (imported placeholder id, deleted assistant) is not hidden:
    # no flagged assistant is known for it, so existing behaviour stays unchanged.
    return {i for i in ids if resolved.get(i, False)}


def is_hidden_by_id(assistant_id: str | None) -> bool:
    """Unfiltered lookup; False for None and for unknown/deleted ids."""
    return bool(_hidden_ids([assistant_id]))


def is_any_hidden(assistant_ids: Iterable[str | None]) -> bool:
    """True if any of the given assistant ids is hidden. Used where a single live stream can't
    be attributed to one specific message/assistant (e.g. after a mid-conversation handoff)."""
    return bool(_hidden_ids(assistant_ids))


def strip_history_for_conversation(conversation: Any) -> list:
    """Strip hidden-assistant thoughts from `conversation.history`.

    No-op for workflow conversations: per spec, workflow/sub-assistant orchestration calls are
    deliberately out of scope for this feature. This also sidesteps a trap — for workflow
    conversations `initial_assistant_id` holds a WorkflowConfig id, not an assistant id, so using
    it as strip_history's fallback would feed a non-assistant id into the assistant lookup.
    """
    history = getattr(conversation, "history", None) or []
    if getattr(conversation, "is_workflow_conversation", False):
        return list(history)
    return strip_history(history, getattr(conversation, "initial_assistant_id", None))


def strip_history(history: list, default_assistant_id: str | None) -> list:
    """Return a new history with thoughts removed for messages of hidden assistants. Never mutates the input."""
    if not history:
        return []
    hidden = _hidden_ids((m.assistant_id or default_assistant_id) for m in history if m.thoughts)
    if not hidden:
        return list(history)
    return [
        m.model_copy(update={"thoughts": _approval_thoughts(m.thoughts)})
        if m.thoughts and (m.assistant_id or default_assistant_id) in hidden
        else m
        for m in history
    ]
