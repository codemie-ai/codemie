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

import asyncio
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from codemie.configs import config
from codemie.configs.llm_config import ModelCategory
from codemie.configs.logger import logger
from codemie.core.dependecies import get_llm_by_credentials
from codemie.core.utils import calculate_tokens
from codemie.service.constants import AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED_KEY
from codemie.service.dynamic_config_service import DynamicConfigService
from codemie.service.llm_service.llm_service import llm_service
from codemie.templates.agents.conversation_history_compaction import conversation_history_compaction_prompt


def _is_conversation_replay_v2_enabled() -> bool:
    return DynamicConfigService.get_bool_value_safe(
        AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED_KEY,
        default=config.AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED,
    )


class ConversationHistoryCompactionService:
    """Compact chat history when it grows beyond the configured token budget."""

    @classmethod
    def compact_messages(
        cls,
        messages: list[BaseMessage],
        llm_model: str,
        request_id: str | None = None,
    ) -> list[BaseMessage]:
        if not cls._is_enabled() or not messages:
            return messages

        history_tokens = cls._count_tokens(messages, llm_model)
        trigger_tokens = cls._get_trigger_tokens()
        if history_tokens <= trigger_tokens:
            return messages

        groups = cls._group_messages(messages)
        if len(groups) < 2:
            return messages

        compacted_messages = cls._compact_grouped_messages(
            groups=groups,
            llm_model=llm_model,
            request_id=request_id,
            original_tokens=history_tokens,
        )
        if compacted_messages == messages:
            return messages

        compacted_tokens = cls._count_tokens(compacted_messages, llm_model)
        logger.info(
            f"Compacted conversation history. "
            f"OriginalMessages={len(messages)}, CompactedMessages={len(compacted_messages)}, "
            f"OriginalTokens={history_tokens}, CompactedTokens={compacted_tokens}, "
            f"LLMModel={llm_model}"
        )
        return compacted_messages

    @classmethod
    def build_langgraph_pre_model_hook(
        cls,
        llm_model: str,
        request_id: str | None = None,
    ) -> Any | None:
        if not cls._is_enabled():
            return None

        def pre_model_hook(state: dict[str, Any]) -> dict[str, list[BaseMessage]]:
            messages = state.get("messages", [])
            if not isinstance(messages, list) or not messages:
                return {}

            compacted_messages = asyncio.run(
                cls._compact_messages_async(
                    messages=[message for message in messages if isinstance(message, BaseMessage)],
                    llm_model=llm_model,
                    request_id=request_id,
                )
            )
            if compacted_messages == messages:
                return {}

            return {"llm_input_messages": compacted_messages}

        return pre_model_hook

    @classmethod
    async def _compact_messages_async(
        cls,
        messages: list[BaseMessage],
        llm_model: str,
        request_id: str | None = None,
    ) -> list[BaseMessage]:
        if not messages:
            return messages

        history_tokens = cls._count_tokens(messages, llm_model)
        trigger_tokens = cls._get_trigger_tokens()
        if history_tokens <= trigger_tokens:
            return messages

        groups = cls._group_messages(messages)
        if len(groups) < 2:
            return messages

        compacted_messages = await cls._compact_grouped_messages_async(
            groups=groups,
            llm_model=llm_model,
            request_id=request_id,
            original_tokens=history_tokens,
        )
        if compacted_messages == messages:
            return messages

        compacted_tokens = cls._count_tokens(compacted_messages, llm_model)
        logger.info(
            f"Compacted conversation history (async). "
            f"OriginalMessages={len(messages)}, CompactedMessages={len(compacted_messages)}, "
            f"OriginalTokens={history_tokens}, CompactedTokens={compacted_tokens}, "
            f"LLMModel={llm_model}"
        )
        return compacted_messages

    @classmethod
    async def _compact_grouped_messages_async(
        cls,
        groups: list[list[BaseMessage]],
        llm_model: str,
        request_id: str | None,
        original_tokens: int,
    ) -> list[BaseMessage]:
        preserve_groups = min(config.AI_AGENT_HISTORY_COMPACTION_PRESERVE_GROUPS, len(groups) - 1)
        preserve_groups = max(preserve_groups, 1)

        prefix_groups = groups[:-preserve_groups]
        if not prefix_groups:
            prefix_groups = groups[:-1]
            preserve_groups = 1

        summary_groups = list(prefix_groups)
        tail_groups = groups[-preserve_groups:]
        summary = await cls._build_summary_for_groups_async(summary_groups, llm_model=llm_model, request_id=request_id)
        if not summary:
            return [message for group in groups for message in group]

        summary_message = AIMessage(content=f"{config.AI_AGENT_HISTORY_COMPACTION_SUMMARY_PREFIX}\n{summary}")
        compacted_messages = [summary_message, *[message for group in tail_groups for message in group]]

        target_tokens = cls._get_target_tokens()
        while len(tail_groups) > 1 and cls._count_tokens(compacted_messages, llm_model) > target_tokens:
            summary_groups.append(tail_groups[0])
            tail_groups = tail_groups[1:]
            summary = await cls._build_summary_for_groups_async(
                summary_groups,
                llm_model=llm_model,
                request_id=request_id,
            )
            if not summary:
                break
            summary_message = AIMessage(content=f"{config.AI_AGENT_HISTORY_COMPACTION_SUMMARY_PREFIX}\n{summary}")
            compacted_messages = [summary_message, *[message for group in tail_groups for message in group]]

        logger.info(
            f"Conversation history exceeded compaction threshold. "
            f"OriginalTokens={original_tokens}, TriggerTokens={cls._get_trigger_tokens()}, "
            f"TargetTokens={target_tokens}, PreservedGroups={len(tail_groups)}, "
            f"SummarizedGroups={len(groups) - len(tail_groups)}"
        )
        return compacted_messages

    @classmethod
    async def _build_summary_for_groups_async(
        cls,
        groups: list[list[BaseMessage]],
        llm_model: str,
        request_id: str | None,
    ) -> str:
        history_text = cls._render_groups(groups)
        if not history_text.strip():
            return ""

        batch_texts = cls._create_text_batches(
            history_text=history_text,
            llm_model=llm_model,
            max_tokens=config.AI_AGENT_HISTORY_COMPACTION_BATCH_TOKEN_LIMIT,
        )
        batch_summaries = []
        for batch_text in batch_texts:
            batch_summary = await cls._summarize_text_async(
                batch_text=batch_text, llm_model=llm_model, request_id=request_id
            )
            if batch_summary:
                batch_summaries.append(batch_summary.strip())

        if not batch_summaries:
            return ""

        if len(batch_summaries) == 1:
            return batch_summaries[0]

        combined_batch_summaries = "\n\n".join(batch_summaries)
        return await cls._summarize_text_async(
            batch_text=combined_batch_summaries,
            llm_model=llm_model,
            request_id=request_id,
        )

    @classmethod
    async def _summarize_text_async(
        cls,
        batch_text: str,
        llm_model: str,
        request_id: str | None,
    ) -> str:
        try:
            summarization_model = cls._get_summarization_model(llm_model)
            llm = get_llm_by_credentials(
                llm_model=summarization_model,
                streaming=False,
                request_id=request_id,
            )
            response = await llm.ainvoke(
                [HumanMessage(content=conversation_history_compaction_prompt.format(history_text=batch_text))]
            )
            summary = cls._extract_message_content(response)
            logger.debug(
                f"Built history compaction summary (async). "
                f"InputTokens={calculate_tokens(batch_text, llm_model=summarization_model)}, "
                f"OutputTokens={calculate_tokens(summary, llm_model=summarization_model)}"
            )
            return summary
        except Exception as error:
            logger.error(f"Conversation history compaction failed. Error={error}", exc_info=True)
            return ""

    @classmethod
    def _compact_grouped_messages(
        cls,
        groups: list[list[BaseMessage]],
        llm_model: str,
        request_id: str | None,
        original_tokens: int,
    ) -> list[BaseMessage]:
        preserve_groups = min(config.AI_AGENT_HISTORY_COMPACTION_PRESERVE_GROUPS, len(groups) - 1)
        preserve_groups = max(preserve_groups, 1)

        prefix_groups = groups[:-preserve_groups]
        if not prefix_groups:
            prefix_groups = groups[:-1]
            preserve_groups = 1

        summary_groups = list(prefix_groups)
        tail_groups = groups[-preserve_groups:]
        summary = cls._build_summary_for_groups(summary_groups, llm_model=llm_model, request_id=request_id)
        if not summary:
            return [message for group in groups for message in group]

        summary_message = AIMessage(content=f"{config.AI_AGENT_HISTORY_COMPACTION_SUMMARY_PREFIX}\n{summary}")
        compacted_messages = [summary_message, *[message for group in tail_groups for message in group]]

        target_tokens = cls._get_target_tokens()
        while len(tail_groups) > 1 and cls._count_tokens(compacted_messages, llm_model) > target_tokens:
            summary_groups.append(tail_groups[0])
            tail_groups = tail_groups[1:]
            summary = cls._build_summary_for_groups(
                summary_groups,
                llm_model=llm_model,
                request_id=request_id,
            )
            if not summary:
                break
            summary_message = AIMessage(content=f"{config.AI_AGENT_HISTORY_COMPACTION_SUMMARY_PREFIX}\n{summary}")
            compacted_messages = [summary_message, *[message for group in tail_groups for message in group]]

        logger.info(
            f"Conversation history exceeded compaction threshold. "
            f"OriginalTokens={original_tokens}, TriggerTokens={cls._get_trigger_tokens()}, "
            f"TargetTokens={target_tokens}, PreservedGroups={len(tail_groups)}, "
            f"SummarizedGroups={len(groups) - len(tail_groups)}"
        )
        return compacted_messages

    @classmethod
    def _build_summary_for_groups(
        cls,
        groups: list[list[BaseMessage]],
        llm_model: str,
        request_id: str | None,
    ) -> str:
        history_text = cls._render_groups(groups)
        if not history_text.strip():
            return ""

        batch_texts = cls._create_text_batches(
            history_text=history_text,
            llm_model=llm_model,
            max_tokens=config.AI_AGENT_HISTORY_COMPACTION_BATCH_TOKEN_LIMIT,
        )
        batch_summaries = []
        for batch_text in batch_texts:
            batch_summary = cls._summarize_text(batch_text=batch_text, llm_model=llm_model, request_id=request_id)
            if batch_summary:
                batch_summaries.append(batch_summary.strip())

        if not batch_summaries:
            return ""

        if len(batch_summaries) == 1:
            return batch_summaries[0]

        combined_batch_summaries = "\n\n".join(batch_summaries)
        return cls._summarize_text(
            batch_text=combined_batch_summaries,
            llm_model=llm_model,
            request_id=request_id,
        )

    @classmethod
    def _summarize_text(
        cls,
        batch_text: str,
        llm_model: str,
        request_id: str | None,
    ) -> str:
        try:
            summarization_model = cls._get_summarization_model(llm_model)
            llm = get_llm_by_credentials(
                llm_model=summarization_model,
                streaming=False,
                request_id=request_id,
            )
            response = llm.invoke(
                [HumanMessage(content=conversation_history_compaction_prompt.format(history_text=batch_text))]
            )
            summary = cls._extract_message_content(response)
            logger.debug(
                f"Built history compaction summary. "
                f"InputTokens={calculate_tokens(batch_text, llm_model=summarization_model)}, "
                f"OutputTokens={calculate_tokens(summary, llm_model=summarization_model)}"
            )
            return summary
        except Exception as error:
            logger.error(f"Conversation history compaction failed. Error={error}", exc_info=True)
            return ""

    @classmethod
    def _create_text_batches(
        cls,
        history_text: str,
        llm_model: str,
        max_tokens: int,
    ) -> list[str]:
        if calculate_tokens(history_text, llm_model=llm_model) <= max_tokens:
            return [history_text]

        lines = history_text.splitlines()
        batches: list[str] = []
        current_lines: list[str] = []

        for line in lines:
            for line_chunk in cls._split_oversized_line(line, llm_model=llm_model, max_tokens=max_tokens):
                candidate_lines = [*current_lines, line_chunk]
                candidate_text = "\n".join(candidate_lines)
                if current_lines and calculate_tokens(candidate_text, llm_model=llm_model) > max_tokens:
                    batches.append("\n".join(current_lines))
                    current_lines = [line_chunk]
                    continue
                current_lines = candidate_lines

        if current_lines:
            batches.append("\n".join(current_lines))

        return batches

    @classmethod
    def _split_oversized_line(
        cls,
        line: str,
        llm_model: str,
        max_tokens: int,
    ) -> list[str]:
        if not line:
            return [line]

        if calculate_tokens(line, llm_model=llm_model) <= max_tokens:
            return [line]

        chunks: list[str] = []
        remaining_text = line
        while remaining_text:
            if calculate_tokens(remaining_text, llm_model=llm_model) <= max_tokens:
                chunks.append(remaining_text)
                break

            split_index = cls._find_max_prefix_within_token_limit(
                remaining_text, llm_model=llm_model, max_tokens=max_tokens
            )
            chunk = remaining_text[:split_index].rstrip()
            if not chunk:
                chunk = remaining_text[:split_index]
            chunks.append(chunk)
            remaining_text = remaining_text[split_index:].lstrip()

        return chunks

    @classmethod
    def _find_max_prefix_within_token_limit(
        cls,
        text: str,
        llm_model: str,
        max_tokens: int,
    ) -> int:
        low = 1
        high = len(text)
        best = 1

        while low <= high:
            middle = (low + high) // 2
            candidate = text[:middle]
            if calculate_tokens(candidate, llm_model=llm_model) <= max_tokens:
                best = middle
                low = middle + 1
            else:
                high = middle - 1

        return best

    @classmethod
    def _render_groups(cls, groups: list[list[BaseMessage]]) -> str:
        rendered_groups = []
        for index, group in enumerate(groups, start=1):
            rendered_messages = [cls._render_message(message) for message in group]
            rendered_groups.append(f"[History block {index}]\n" + "\n".join(filter(None, rendered_messages)))
        return "\n\n".join(rendered_groups)

    @classmethod
    def _render_message(cls, message: BaseMessage) -> str:
        content = cls._extract_message_content(message)
        if isinstance(message, HumanMessage):
            return f"User: {content}"
        if isinstance(message, ToolMessage):
            tool_name = message.name or message.additional_kwargs.get("name") or "tool"
            return f"Tool {tool_name}: {content}"
        if isinstance(message, AIMessage):
            if getattr(message, "tool_calls", None):
                tool_calls = [f"{tool_call.get('name')}({tool_call.get('args')})" for tool_call in message.tool_calls]
                tool_calls_text = ", ".join(tool_calls)
                content_suffix = f" Content: {content}" if content else ""
                return f"Assistant tool call: {tool_calls_text}.{content_suffix}".strip()
            return f"Assistant: {content}"
        return f"{message.__class__.__name__}: {content}"

    @classmethod
    def _group_messages(cls, messages: list[BaseMessage]) -> list[list[BaseMessage]]:
        groups: list[list[BaseMessage]] = []
        current_group: list[BaseMessage] = []

        for message in messages:
            if isinstance(message, HumanMessage) and current_group:
                groups.append(current_group)
                current_group = [message]
                continue

            current_group.append(message)

        if current_group:
            groups.append(current_group)

        return groups

    @classmethod
    def _extract_message_content(cls, message: Any) -> str:
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(str(item.get("text", "")))
                    elif item.get("type") == "image":
                        parts.append("[image]")
                    else:
                        parts.append(str(item))
                else:
                    parts.append(str(item))
            return " ".join(part for part in parts if part).strip()
        return str(content).strip()

    @classmethod
    def _count_tokens(cls, messages: list[BaseMessage], llm_model: str) -> int:
        serialized_history = "\n".join(cls._render_message(message) for message in messages)
        return calculate_tokens(serialized_history, llm_model=llm_model)

    @classmethod
    def _get_summarization_model(cls, llm_model: str) -> str:
        try:
            active_model = llm_service.get_model_details(llm_model)
            summarization_model = llm_service.get_default_model_for_category(ModelCategory.SUMMARIZATION)
            if (
                summarization_model
                and active_model.provider
                and summarization_model.provider
                and active_model.provider == summarization_model.provider
            ):
                return summarization_model.base_name
            if summarization_model:
                logger.info(
                    f"Using active chat model for history compaction because the summarization provider boundary "
                    f"could not be verified or differs. "
                    f"ActiveModel={llm_model}, SummarizationModel={summarization_model.base_name}"
                )
        except Exception as error:
            logger.warning(
                f"Failed to resolve dedicated summarization model for history compaction. "
                f"ActiveModel={llm_model}, Error={error}"
            )
        return llm_model

    @classmethod
    def _get_trigger_tokens(cls) -> int:
        return max(
            1, int(config.AI_AGENT_HISTORY_COMPACTION_TOKEN_LIMIT * config.AI_AGENT_HISTORY_COMPACTION_TRIGGER_RATE)
        )

    @classmethod
    def _get_target_tokens(cls) -> int:
        return max(
            1, int(config.AI_AGENT_HISTORY_COMPACTION_TOKEN_LIMIT * config.AI_AGENT_HISTORY_COMPACTION_TARGET_RATE)
        )

    @classmethod
    def _is_enabled(cls) -> bool:
        return (
            _is_conversation_replay_v2_enabled()
            and config.AI_AGENT_HISTORY_COMPACTION_ENABLED
            and config.AI_AGENT_HISTORY_COMPACTION_TOKEN_LIMIT > 0
            and config.AI_AGENT_HISTORY_COMPACTION_BATCH_TOKEN_LIMIT > 0
            and 0 < config.AI_AGENT_HISTORY_COMPACTION_TARGET_RATE < 1
            and 0 < config.AI_AGENT_HISTORY_COMPACTION_TRIGGER_RATE < 1
            and config.AI_AGENT_HISTORY_COMPACTION_TARGET_RATE < config.AI_AGENT_HISTORY_COMPACTION_TRIGGER_RATE
        )
