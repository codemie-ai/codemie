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

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from codemie.chains.base import ThoughtAuthorType
from codemie.configs import config
from codemie.configs.logger import logger
from codemie.core.constants import ChatRole
from codemie.rest_api.models.conversation import Conversation, GeneratedMessage

SKILL_TOOL_NAME = "skill"
TOOL_REPLAY_TYPE = "tool"
TOOL_STATUS_RUNNING = "running"
TOOL_STATUS_COMPLETED = "completed"
TOOL_STATUS_ERROR = "error"
TOOL_STATUS_INTERRUPTED = "interrupted"
TEXT_LEDGER_MODE = "text_ledger"
NATIVE_TOOLS_MODE = "native_tools"
PLAIN_CHAT_MODE = "plain_chat"
GENERIC_THOUGHT_NAMES = {"CodeMie Thoughts"}
NORMALIZED_GENERIC_THOUGHT_NAMES = {"codemie_thoughts"}


@dataclass(slots=True)
class ToolReplayRecord:
    call_id: str
    tool_name: str
    args: dict[str, Any]
    args_text: str
    status: str
    result_text: str
    result_summary: str
    preserve_full_output: bool = False


@dataclass(slots=True)
class ProjectedTurn:
    index: int
    user_message: GeneratedMessage | None = None
    assistant_message: GeneratedMessage | None = None
    tool_records: list[ToolReplayRecord] = field(default_factory=list)


class ConversationHistoryProjectionService:
    """Build model-facing history from stored conversation records."""

    @classmethod
    def build_for_request(
        cls,
        conversation: Conversation,
        mode: str,
        max_full_tool_turns: int | None = None,
        max_summarized_tool_turns: int | None = None,
    ) -> list[BaseMessage]:
        if max_full_tool_turns is None:
            max_full_tool_turns = config.AI_AGENT_HISTORY_REPLAY_FULL_TOOL_TURNS
        if max_summarized_tool_turns is None:
            max_summarized_tool_turns = config.AI_AGENT_HISTORY_REPLAY_SUMMARIZED_TOOL_TURNS
        logger.info(
            f"Building conversation replay history. ConversationId={conversation.conversation_id}, "
            f"Mode={mode}, StoredMessages={len(conversation.history or [])}"
        )
        if mode == PLAIN_CHAT_MODE:
            history = cls._build_plain_chat_history(conversation)
            cls._log_projected_history(conversation.conversation_id, mode, history)
            return history

        turns = cls._group_turns(conversation)
        if not turns:
            return []

        full_indexes, summarized_indexes = cls._resolve_tool_windows(
            turns=turns,
            max_full_tool_turns=max_full_tool_turns,
            max_summarized_tool_turns=max_summarized_tool_turns,
        )

        projected_messages: list[BaseMessage] = []
        for turn in turns:
            projected_messages.extend(
                cls._render_turn(
                    turn=turn,
                    mode=mode,
                    use_full_results=turn.index in full_indexes,
                    use_summaries=turn.index in summarized_indexes,
                )
            )

        filtered_messages: list[BaseMessage] = []
        for message in projected_messages:
            if getattr(message, "content", None):
                filtered_messages.append(message)
                continue
            if isinstance(message, ToolMessage):
                filtered_messages.append(message)
                continue
            if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
                filtered_messages.append(message)

        cls._log_projected_history(conversation.conversation_id, mode, filtered_messages)
        return filtered_messages

    @classmethod
    def _build_plain_chat_history(cls, conversation: Conversation) -> list[BaseMessage]:
        messages: list[BaseMessage] = []
        for message in conversation.to_chat_history():
            if message.role == ChatRole.USER:
                messages.append(HumanMessage(content=message.message))
            elif message.role == ChatRole.ASSISTANT:
                messages.append(AIMessage(content=message.message))
        return messages

    @classmethod
    def _group_turns(cls, conversation: Conversation) -> list[ProjectedTurn]:
        grouped: dict[int, ProjectedTurn] = {}

        for item in conversation.history or []:
            if not isinstance(item, GeneratedMessage) or item.history_index is None:
                continue

            turn = grouped.setdefault(item.history_index, ProjectedTurn(index=item.history_index))
            if item.role == ChatRole.USER:
                turn.user_message = item
            elif item.role == ChatRole.ASSISTANT:
                turn.assistant_message = item
                turn.tool_records = cls._extract_tool_records(item)

        return [grouped[idx] for idx in sorted(grouped)]

    @classmethod
    def _extract_tool_records(cls, message: GeneratedMessage) -> list[ToolReplayRecord]:
        tool_records: list[ToolReplayRecord] = []

        for thought in message.thoughts or []:
            metadata = thought.metadata or {}
            if not cls._is_replayable_tool(thought.author_name, thought.author_type, metadata):
                continue

            tool_name = str(metadata.get("tool_name") or cls._normalize_tool_name(thought.author_name))
            args_text = str(metadata.get("tool_args_text") or thought.input_text or "")
            args = cls._coerce_tool_args(metadata.get("tool_args"), args_text)
            status = cls._normalize_status(
                metadata.get("status"),
                error=bool(thought.error),
                in_progress=bool(getattr(thought, "in_progress", False)),
            )
            result_text = cls._sanitize_tool_text(thought.message or "")
            result_summary = cls._sanitize_tool_text(
                str(metadata.get("result_summary") or cls._summarize_tool_result(tool_name, result_text))
            )
            preserve_full_output = bool(metadata.get("preserve_full_output")) or tool_name == SKILL_TOOL_NAME

            tool_records.append(
                ToolReplayRecord(
                    call_id=thought.id,
                    tool_name=tool_name,
                    args=args,
                    args_text=args_text,
                    status=status,
                    result_text=result_text,
                    result_summary=result_summary,
                    preserve_full_output=preserve_full_output,
                )
            )

        return tool_records

    @classmethod
    def _is_replayable_tool(cls, author_name: str | None, author_type: str | None, metadata: dict[str, Any]) -> bool:
        normalized_name = cls._normalize_tool_name(str(metadata.get("tool_name") or author_name or ""))
        if normalized_name in NORMALIZED_GENERIC_THOUGHT_NAMES:
            return False

        if metadata.get("replay_type") == TOOL_REPLAY_TYPE:
            return True

        if author_type != ThoughtAuthorType.Tool.value:
            return False

        return bool(author_name and author_name not in GENERIC_THOUGHT_NAMES)

    @classmethod
    def _resolve_tool_windows(
        cls,
        turns: list[ProjectedTurn],
        max_full_tool_turns: int,
        max_summarized_tool_turns: int,
    ) -> tuple[set[int], set[int]]:
        tool_turn_indexes = [turn.index for turn in turns if turn.tool_records]
        if not tool_turn_indexes:
            return set(), set()

        full_indexes = set(tool_turn_indexes[-max_full_tool_turns:]) if max_full_tool_turns > 0 else set()
        summary_end = len(tool_turn_indexes) - max(max_full_tool_turns, 0)
        summary_start = max(0, summary_end - max(max_summarized_tool_turns, 0))
        summarized_indexes = (
            set(tool_turn_indexes[summary_start:summary_end]) if max_summarized_tool_turns > 0 else set()
        )
        return full_indexes, summarized_indexes

    @classmethod
    def _render_turn(
        cls,
        turn: ProjectedTurn,
        mode: str,
        use_full_results: bool,
        use_summaries: bool,
    ) -> list[BaseMessage]:
        messages: list[BaseMessage] = []

        if turn.user_message and turn.user_message.message:
            messages.append(HumanMessage(content=turn.user_message.message))

        if turn.assistant_message is None:
            return messages

        tool_records = turn.tool_records
        assistant_text = turn.assistant_message.message or ""
        should_replay_tools = use_full_results or use_summaries or cls._has_pinned_tool_records(tool_records)
        tool_messages = cls._render_tool_replay_messages(
            mode=mode,
            assistant_text=assistant_text,
            tool_records=tool_records,
            use_full_results=use_full_results,
            use_summaries=use_summaries,
            should_replay_tools=should_replay_tools,
        )
        if tool_messages is not None:
            messages.extend(tool_messages)
            return messages

        if cls._should_append_assistant_message(assistant_text, tool_records):
            messages.append(AIMessage(content=assistant_text))

        return messages

    @classmethod
    def _render_tool_replay_messages(
        cls,
        mode: str,
        assistant_text: str,
        tool_records: list[ToolReplayRecord],
        use_full_results: bool,
        use_summaries: bool,
        should_replay_tools: bool,
    ) -> list[BaseMessage] | None:
        if not tool_records or not should_replay_tools:
            return None

        if mode == NATIVE_TOOLS_MODE:
            return cls._render_native_tool_turn(
                assistant_text=assistant_text,
                tool_records=tool_records,
                use_full_results=use_full_results,
                use_summaries=use_summaries,
            )

        if mode != TEXT_LEDGER_MODE:
            return None

        content = cls._render_text_ledger_turn(
            assistant_text=assistant_text,
            tool_records=tool_records,
            use_full_results=use_full_results,
            use_summaries=use_summaries,
        )
        if not content:
            return []

        return [AIMessage(content=content)]

    @classmethod
    def _render_native_tool_turn(
        cls,
        assistant_text: str,
        tool_records: list[ToolReplayRecord],
        use_full_results: bool,
        use_summaries: bool,
    ) -> list[BaseMessage]:
        messages = cls._render_native_tool_messages(tool_records, use_full_results, use_summaries)
        if cls._should_append_assistant_message(assistant_text, tool_records):
            messages.append(AIMessage(content=assistant_text))
        return messages

    @classmethod
    def _has_pinned_tool_records(cls, tool_records: list[ToolReplayRecord]) -> bool:
        for record in tool_records:
            if record.preserve_full_output:
                return True
            if record.status in {TOOL_STATUS_ERROR, TOOL_STATUS_INTERRUPTED}:
                return True
        return False

    @classmethod
    def _should_append_assistant_message(cls, assistant_text: str, tool_records: list[ToolReplayRecord]) -> bool:
        if not assistant_text:
            return False
        if not tool_records:
            return True

        normalized_assistant_text = cls._normalize_text_for_comparison(assistant_text)
        for record in tool_records:
            replay_text = record.result_text or record.result_summary
            if not replay_text:
                continue
            if normalized_assistant_text == cls._normalize_text_for_comparison(replay_text):
                logger.info(
                    "Skipping duplicate assistant replay message because it matches "
                    f"tool output. Tool={record.tool_name}"
                )
                return False
        return True

    @classmethod
    def _render_native_tool_messages(
        cls,
        tool_records: list[ToolReplayRecord],
        use_full_results: bool,
        use_summaries: bool,
    ) -> list[BaseMessage]:
        messages: list[BaseMessage] = []
        for record in tool_records:
            tool_args = record.args or {"input": record.args_text}
            tool_call = {
                "id": record.call_id,
                "name": record.tool_name,
                "args": tool_args,
                "type": "tool_call",
            }
            messages.append(AIMessage(content="", tool_calls=[tool_call]))
            messages.append(
                ToolMessage(
                    tool_call_id=record.call_id,
                    content=cls._render_tool_result_content(record, use_full_results, use_summaries),
                    additional_kwargs={"name": record.tool_name},
                )
            )
        return messages

    @classmethod
    def _render_text_ledger_turn(
        cls,
        assistant_text: str,
        tool_records: list[ToolReplayRecord],
        use_full_results: bool,
        use_summaries: bool,
    ) -> str:
        ledger_lines = ["Previous tool activity:"]
        for record in tool_records:
            result = cls._render_tool_result_content(record, use_full_results, use_summaries)
            ledger_lines.append(f"- {record.tool_name}({record.args_text or record.args}) -> {record.status}: {result}")

        sections = ["\n".join(ledger_lines)]
        if assistant_text:
            sections.append(f"Assistant response:\n{assistant_text}")

        return "\n\n".join(section for section in sections if section)

    @classmethod
    def _render_tool_result_content(
        cls,
        record: ToolReplayRecord,
        use_full_results: bool,
        use_summaries: bool,
    ) -> str:
        if record.preserve_full_output:
            return record.result_text or record.result_summary

        if record.status in {TOOL_STATUS_ERROR, TOOL_STATUS_INTERRUPTED}:
            fallback_message = (
                "Tool execution was interrupted before a result was produced."
                if record.status == TOOL_STATUS_INTERRUPTED
                else "Tool execution failed."
            )
            return cls._truncate_text(
                record.result_text or record.result_summary or fallback_message,
                config.AI_AGENT_HISTORY_REPLAY_SUMMARY_TOOL_RESULT_LIMIT,
            )

        if use_full_results:
            return cls._truncate_text(
                record.result_text or record.result_summary,
                config.AI_AGENT_HISTORY_REPLAY_FULL_TOOL_RESULT_LIMIT,
            )

        if use_summaries:
            return cls._truncate_text(
                record.result_summary or record.result_text,
                config.AI_AGENT_HISTORY_REPLAY_SUMMARY_TOOL_RESULT_LIMIT,
            )

        return cls._truncate_text(
            record.result_summary or record.result_text,
            config.AI_AGENT_HISTORY_REPLAY_SUMMARY_TOOL_RESULT_LIMIT,
        )

    @classmethod
    def _normalize_status(cls, status: Any, error: bool, in_progress: bool) -> str:
        if status == TOOL_STATUS_RUNNING or in_progress:
            return TOOL_STATUS_INTERRUPTED
        if error:
            return TOOL_STATUS_ERROR
        if status in {TOOL_STATUS_COMPLETED, TOOL_STATUS_ERROR, TOOL_STATUS_INTERRUPTED}:
            return status
        return TOOL_STATUS_COMPLETED

    @classmethod
    def _normalize_tool_name(cls, author_name: str | None) -> str:
        if not author_name:
            return "unknown_tool"
        return author_name.strip().replace(" ", "_").lower()

    @classmethod
    def _coerce_tool_args(cls, tool_args: Any, args_text: str) -> dict[str, Any]:
        if isinstance(tool_args, dict):
            return tool_args

        parsed = cls._safe_parse_args(args_text)
        if isinstance(parsed, dict):
            return parsed

        if args_text:
            return {"input": args_text}
        return {}

    @classmethod
    def _safe_parse_args(cls, args_text: str) -> dict[str, Any] | None:
        if not args_text:
            return None

        for parser in (json.loads, ast.literal_eval):
            try:
                parsed = parser(args_text)
            except (ValueError, SyntaxError, TypeError, RecursionError):
                continue
            if isinstance(parsed, dict):
                return parsed

        return None

    @classmethod
    def _summarize_tool_result(cls, tool_name: str, result_text: str) -> str:
        if not result_text:
            return ""

        limit = (
            config.AI_AGENT_HISTORY_REPLAY_FULL_TOOL_RESULT_LIMIT
            if tool_name == SKILL_TOOL_NAME
            else config.AI_AGENT_HISTORY_REPLAY_SUMMARY_TOOL_RESULT_LIMIT
        )
        return cls._truncate_text(result_text, limit)

    @classmethod
    def _sanitize_tool_text(cls, text: str) -> str:
        return text.strip()

    @classmethod
    def _truncate_text(cls, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        truncated = text[:limit].rstrip()
        logger.debug(f"Truncated replay tool content from {len(text)} to {len(truncated)} chars")
        return f"{truncated}\n...[truncated]"

    @classmethod
    def _log_projected_history(cls, conversation_id: str, mode: str, history: list[BaseMessage]) -> None:
        logger.info(
            f"Projected conversation replay history. ConversationId={conversation_id}, "
            f"Mode={mode}, MessageCount={len(history)}"
        )
        for index, message in enumerate(history):
            logger.debug(
                f"Projected history message[{index}]. ConversationId={conversation_id}, "
                f"Payload={cls._serialize_message_for_log(message)}"
            )

    @classmethod
    def _serialize_message_for_log(cls, message: BaseMessage) -> str:
        content = message.content if isinstance(message.content, str) else str(message.content)
        payload = {
            "type": message.__class__.__name__,
            "content": cls._truncate_text(content, config.AI_AGENT_HISTORY_REPLAY_LOG_CONTENT_LIMIT),
        }
        if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
            payload["tool_calls"] = cls._serialize_tool_calls_for_log(message.tool_calls)
        if isinstance(message, ToolMessage):
            payload["tool_call_id"] = message.tool_call_id
            payload["name"] = message.name or message.additional_kwargs.get("name")
        return json.dumps(payload, ensure_ascii=True, default=str)

    @classmethod
    def _normalize_text_for_comparison(cls, text: str) -> str:
        return " ".join(text.split()).strip()

    @classmethod
    def _serialize_tool_calls_for_log(cls, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        serialized_calls = []
        for tool_call in tool_calls:
            args_text = json.dumps(tool_call.get("args", {}), ensure_ascii=True, default=str)
            serialized_calls.append(
                {
                    "id": tool_call.get("id"),
                    "name": tool_call.get("name"),
                    "args": cls._truncate_text(args_text, config.AI_AGENT_HISTORY_REPLAY_LOG_CONTENT_LIMIT),
                }
            )
        return serialized_calls
