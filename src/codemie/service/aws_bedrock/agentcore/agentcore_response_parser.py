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

import json
import uuid
from typing import Any, Iterator, Optional

from codemie.chains.base import Thought, ThoughtAuthorType
from codemie.configs import logger
from codemie.service.aws_bedrock.agentcore.agentcore_config import (
    AgentcoreOutputConfig,
    AgentcoreReasoningConfig,
    AgentcoreResponseConfig,
)
from codemie.service.aws_bedrock.agentcore.utils import resolve_json_path


class AgentcoreResponseError(Exception):
    """Raised when the AgentCore response does not match the configured extraction paths."""


class AgentcoreResponseParser:
    """Parses AgentCore runtime responses into ``(text, thoughts)`` tuples.

    Supports two response modes driven by ``AgentcoreResponseConfig``:

    - **Non-streaming** (``parse_json``): reads a single JSON body, extracts
      the answer text and optional reasoning thoughts via dot-notation paths.
    - **Streaming** (``parse_streaming``): consumes an SSE stream and either
      accumulates the full result (default) or yields chunks progressively
      for live client forwarding (``emit_stream=True``).
    """

    def parse_json(self, body: bytes, config: AgentcoreResponseConfig) -> tuple[str, list[Thought]]:
        """Parse a complete JSON response body into text and thoughts."""
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as exc:
            raise AgentcoreResponseError(
                "AgentCore response could not be decoded as JSON. "
                "The endpoint might be returning an invalid or streaming response — "
                "enable the streaming in the endpoint configuration."
            ) from exc

        body_config = config.body
        raw = resolve_json_path(data, body_config.text_path)

        if raw is None:
            raise AgentcoreResponseError(
                f"AgentCore Error: response field {body_config.text_path!r} not found; "
                "contact the administrator to update the configuration."
            )
        if isinstance(raw, (dict, list)):
            raise AgentcoreResponseError(
                f"AgentCore Error: response field {body_config.text_path!r} has unexpected type {type(raw).__name__}; "
                "contact the administrator to update the configuration."
            )
        text = str(raw)
        thoughts = self._extract_thoughts(data, body_config.reasoning) if body_config.reasoning else []
        logger.debug(f"[AgentCore] JSON response parsed: text_len={len(text)} thoughts={len(thoughts)}")

        return text, thoughts

    def parse_streaming(
        self,
        stream,
        config: AgentcoreResponseConfig,
        emit_stream: bool = False,
    ) -> tuple[str, list[Thought]] | Iterator[tuple[str | None, list[Thought]]]:
        """Parse an SSE stream.

        When ``emit_stream=False`` (default), accumulates and returns ``(full_text, thoughts)``.
        When ``emit_stream=True``, returns an iterator yielding ``(text_chunk, emitted_thoughts)``
        per chunk for progressive forwarding to a client.
        """
        if not emit_stream:
            return self._accumulate_chunks(stream, config)

        return self._iter_chunks(stream, config)

    def _accumulate_chunks(self, stream, config: AgentcoreResponseConfig) -> tuple[str, list[Thought]]:
        parts: list[str] = []
        thoughts: list[Thought] = []
        for text_chunk, emitted in self._iter_chunks(stream, config):
            thoughts.extend(emitted)

            if text_chunk is not None:
                parts.append(str(text_chunk))

        result = "".join(parts)
        logger.debug(f"[AgentCore] Stream parsed: text_len={len(result)} thoughts={len(thoughts)}")
        return result, thoughts

    def _iter_chunks(self, stream, config: AgentcoreResponseConfig) -> Iterator[tuple[str | None, list[Thought]]]:
        chunk_config = config.chunk
        current_thought: Optional[Thought] = None

        for chunk in self._iter_sse_data(stream):
            logger.debug(f"[AgentCore] SSE chunk raw: {chunk!r}")
            if not chunk_config.text_path:
                yield chunk, []
                continue

            try:
                data = json.loads(chunk)
                if isinstance(data, str):
                    data = json.loads(data)
            except json.JSONDecodeError:
                yield chunk, []
                continue

            logger.debug(
                f"[AgentCore] SSE chunk parsed keys={list(data.keys())} reasoning_cfg={chunk_config.reasoning}"
            )

            current_thought, emitted, text = self._process_chunk(data, chunk_config, current_thought)
            yield text, emitted

        if current_thought is not None:
            logger.warning("[AgentCore] Stream ended with an unclosed thought; closing it")
            yield None, [current_thought.model_copy(update={"in_progress": False})]

    @staticmethod
    def _iter_sse_data(stream) -> Iterator[str]:
        """Yield payload from each non-empty line.

        Strips ``data: `` prefix when present; skips SSE control lines
        (``event:``, ``id:``, ``retry:``); yields plain lines as-is so
        runtimes that omit the SSE prefix still produce output.
        """
        for line in stream.iter_lines(chunk_size=256):
            if not line:
                continue
            line_str = line.decode("utf-8") if isinstance(line, bytes) else line
            logger.debug(f"[AgentCore] raw line: {line_str!r}")
            if line_str.startswith("data: "):
                yield line_str[6:]
            elif not line_str.startswith(("event:", "id:", "retry:")):
                yield line_str

    def _process_chunk(
        self,
        data: dict,
        chunk_config: AgentcoreOutputConfig,
        current_thought: Optional[Thought],
    ) -> tuple[Optional[Thought], list[Thought], Any]:
        if chunk_config.reasoning:
            current_thought, emitted, handled = self._handle_reasoning_chunk(
                data, chunk_config.reasoning, current_thought
            )
            if handled:
                return current_thought, emitted, None

        raw = resolve_json_path(data, chunk_config.text_path)
        if raw is None:
            raise AgentcoreResponseError(
                f"AgentCore Error: response field {chunk_config.text_path!r} not found; "
                "contact the administrator to update the configuration."
            )
        if isinstance(raw, (dict, list)):
            raise AgentcoreResponseError(
                f"AgentCore Error: response field {chunk_config.text_path!r} has unexpected type {type(raw).__name__}; "
                "contact the administrator to update the configuration."
            )
        return current_thought, [], raw

    def _handle_reasoning_chunk(
        self,
        data: dict,
        reasoning: AgentcoreReasoningConfig,
        current_thought: Optional[Thought],
    ) -> tuple[Optional[Thought], list[Thought], bool]:
        active = resolve_json_path(data, reasoning.active_path)

        if active is True:
            text = str(resolve_json_path(data, reasoning.text_path) or "")
            name = resolve_json_path(data, reasoning.name_path) if reasoning.name_path else None
            args = resolve_json_path(data, reasoning.args_path) if reasoning.args_path else None
            if current_thought is None:
                current_thought = self._make_thought(text, name, args, in_progress=True)
                logger.debug("[AgentCore] Reasoning thought opened")
            else:
                current_thought.message = (current_thought.message or "") + text
            return current_thought, [current_thought.model_copy(update={"in_progress": True})], True

        if active is False:
            if current_thought is not None:
                logger.debug("[AgentCore] Reasoning thought closed")
                return None, [current_thought.model_copy(update={"in_progress": False})], True
            return current_thought, [], True  # stray close with no open thought — skip, not a text chunk

        return current_thought, [], False  # active is None → no active field → treat as text chunk

    def _extract_thoughts(self, data: dict, reasoning: AgentcoreReasoningConfig) -> list[Thought]:
        """Extract one or more Thought objects from a JSON response using reasoning path config."""
        if reasoning.thoughts_path is not None:
            return self._extract_thoughts_from_array(data, reasoning)

        text_val = resolve_json_path(data, reasoning.text_path)
        if text_val is None:
            logger.debug(f"[AgentCore] Reasoning path {reasoning.text_path!r} resolved to None")
            return []

        if not isinstance(text_val, list):
            name = resolve_json_path(data, reasoning.name_path) if reasoning.name_path else None
            args = resolve_json_path(data, reasoning.args_path) if reasoning.args_path else None
            return [self._make_thought(str(text_val), name, args)]

        name_vals = resolve_json_path(data, reasoning.name_path) if reasoning.name_path else None
        args_vals = resolve_json_path(data, reasoning.args_path) if reasoning.args_path else None
        logger.debug(f"[AgentCore] Reasoning fan-out: {len(text_val)} thoughts extracted")
        return [
            self._make_thought(str(text), self._pick(name_vals, i), self._pick(args_vals, i))
            for i, text in enumerate(text_val)
            if text is not None
        ]

    def _extract_thoughts_from_array(self, data: dict, reasoning: AgentcoreReasoningConfig) -> list[Thought]:
        thoughts_array = resolve_json_path(data, reasoning.thoughts_path)
        if not isinstance(thoughts_array, list):
            logger.debug(
                f"[AgentCore] thoughts_path {reasoning.thoughts_path!r} resolved to "
                f"{type(thoughts_array).__name__}, expected list"
            )
            return []

        result = []
        for item in thoughts_array:
            if isinstance(item, dict):
                text = resolve_json_path(item, reasoning.text_path)
                name = resolve_json_path(item, reasoning.name_path) if reasoning.name_path else None
                args = resolve_json_path(item, reasoning.args_path) if reasoning.args_path else None
            else:
                text = item
                name = None
                args = None
            if text is None:
                continue
            result.append(self._make_thought(str(text), name, args))

        logger.debug(f"[AgentCore] thoughts_path fan-out: {len(result)} thoughts extracted")
        return result

    @staticmethod
    def _pick(vals: Any, i: int) -> Any:
        if isinstance(vals, list):
            return vals[i] if i < len(vals) else None
        return vals

    @staticmethod
    def _serialize_args(args: Any) -> Optional[str]:
        if args is None:
            return None
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                return args
        return json.dumps(args)

    @staticmethod
    def _make_thought(text: str, name: Any = None, args: Any = None, in_progress: bool = False) -> Thought:
        return Thought(
            id=str(uuid.uuid4()),
            in_progress=in_progress,
            message=text,
            author_name=str(name) if name else None,
            input_text=AgentcoreResponseParser._serialize_args(args),
            author_type=ThoughtAuthorType.Agent,
        )
