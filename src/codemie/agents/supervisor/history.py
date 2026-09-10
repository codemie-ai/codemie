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

from collections import deque
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from codemie.agents.agent_runtime_utils import sanitize_rich_history_for_llm
from codemie.agents.supervisor.constants import (
    METADATA_KEY_HANDOFF_BACK,
    METADATA_KEY_HANDOFF_DESTINATION,
    METADATA_KEY_PARALLEL_SUBAGENT_PARENT_HANDOFF,
    METADATA_KEY_SUBAGENT_TASK,
)
from codemie.core.constants import SUPERVISOR_HANDOFF_TOOL_PREFIX
from codemie.core.utils import extract_text_from_llm_output

PARALLEL_SUBAGENT_HANDOFF_ACK_KEY = "__parallel_subagent_handoff_ack"


def _find_tool_message_index(filtered_messages: list[BaseMessage], message: ToolMessage) -> int | None:
    for index in range(len(filtered_messages) - 1, -1, -1):
        existing_message = filtered_messages[index]
        if not isinstance(existing_message, ToolMessage):
            continue
        if existing_message.tool_call_id and existing_message.tool_call_id == message.tool_call_id:
            return index
    return None


def _should_replace_tool_message(existing_message: ToolMessage, message: ToolMessage) -> bool:
    if (
        existing_message.name == message.name
        and existing_message.tool_call_id == message.tool_call_id
        and existing_message.content == message.content
        and existing_message.response_metadata == message.response_metadata
        and existing_message.additional_kwargs == message.additional_kwargs
    ):
        return False

    return not existing_message.content and bool(message.content)


def _append_unique_message(filtered_messages: list[BaseMessage], message: BaseMessage) -> None:
    if isinstance(message, ToolMessage):
        existing_index = _find_tool_message_index(filtered_messages, message)
        if existing_index is not None:
            existing_message = filtered_messages[existing_index]
            if isinstance(existing_message, ToolMessage) and _should_replace_tool_message(existing_message, message):
                filtered_messages[existing_index] = message
            return

    filtered_messages.append(message)


def _build_handoff_tool_call_message(message: ToolMessage) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": message.name,
                "args": {"task": message.content},
                "id": message.tool_call_id,
                "type": "tool_call",
            }
        ],
    )


def _queue_pending_handoff_message(
    message: BaseMessage,
    pending_parallel_handoffs: dict[str, deque[AIMessage]],
    pending_single_handoffs: dict[str, deque[tuple[str, str | None]]],
) -> bool:
    if not isinstance(message, ToolMessage):
        return False
    if message.additional_kwargs.get(PARALLEL_SUBAGENT_HANDOFF_ACK_KEY):
        return True

    destination = str(message.response_metadata.get(METADATA_KEY_HANDOFF_DESTINATION) or "")
    if message.additional_kwargs.get(METADATA_KEY_PARALLEL_SUBAGENT_PARENT_HANDOFF):
        pending_parallel_handoffs.setdefault(destination, deque()).append(_build_handoff_tool_call_message(message))
        return True
    if message.additional_kwargs.get(METADATA_KEY_SUBAGENT_TASK):
        pending_single_handoffs.setdefault(destination, deque()).append((message.name, message.tool_call_id))
        return True
    return False


def _consume_pending_handoff_message(
    message: BaseMessage,
    filtered_messages: list[BaseMessage],
    pending_parallel_handoffs: dict[str, deque[AIMessage]],
    pending_single_handoffs: dict[str, deque[tuple[str, str | None]]],
) -> bool:
    if not isinstance(message, AIMessage):
        return False

    author_name = message.name or ""
    if author_name and pending_parallel_handoffs.get(author_name):
        handoff_message = pending_parallel_handoffs[author_name].popleft()
        _append_unique_message(filtered_messages, handoff_message)
        _append_unique_message(
            filtered_messages,
            ToolMessage(
                content=extract_text_from_llm_output(str(message.content or "")),
                name=handoff_message.tool_calls[0]["name"],
                tool_call_id=handoff_message.tool_calls[0]["id"],
            ),
        )
        return True

    if author_name and pending_single_handoffs.get(author_name):
        tool_name, tool_call_id = pending_single_handoffs[author_name].popleft()
        _append_unique_message(
            filtered_messages,
            ToolMessage(
                content=extract_text_from_llm_output(str(message.content or "")),
                name=tool_name,
                tool_call_id=tool_call_id,
            ),
        )
        return True

    return False


def _is_handoff_back_message(message: BaseMessage) -> bool:
    return bool(getattr(message, "response_metadata", {}).get(METADATA_KEY_HANDOFF_BACK))


def _is_parallel_supervisor_handoff_message(
    message: BaseMessage,
    pending_parallel_handoffs: dict[str, deque[AIMessage]],
) -> bool:
    """Return True for supervisor AIMessages that initiate multiple parallel handoffs AND are
    actively superseded by the current parallel-handoff reconstruction in pending_parallel_handoffs.

    Such messages are superseded by the reconstructed single-tool-call pairs produced by
    _consume_pending_handoff_message and must not be kept in filtered_messages — keeping them
    causes sanitize_rich_history_for_llm to emit spurious warnings when it encounters the
    original multi-call block immediately followed by the reconstructed blocks.

    Single-handoff supervisor messages (exactly one tool_call) are excluded: the single-handoff
    path creates only a ToolMessage response, so the AIMessage is needed for a valid call/response
    pair.

    Historical multi-call messages whose tool calls are NOT represented in pending_parallel_handoffs
    are kept so that their existing ToolMessage responses are not left as orphans.
    """
    if not (isinstance(message, AIMessage) and len(getattr(message, "tool_calls", [])) > 1):
        return False
    if not all(tc.get("name", "").startswith(f"{SUPERVISOR_HANDOFF_TOOL_PREFIX}_") for tc in message.tool_calls):
        return False
    queued_ids = {queued.tool_calls[0]["id"] for pending in pending_parallel_handoffs.values() for queued in pending}
    return any(tc.get("id") in queued_ids for tc in message.tool_calls)


def _process_handoff_message(
    message: BaseMessage,
    filtered_messages: list[BaseMessage],
    pending_parallel_handoffs: dict[str, deque[AIMessage]],
    pending_single_handoffs: dict[str, deque[tuple[str, str | None]]],
) -> None:
    if _queue_pending_handoff_message(message, pending_parallel_handoffs, pending_single_handoffs):
        return
    if _is_parallel_supervisor_handoff_message(message, pending_parallel_handoffs):
        return
    if _consume_pending_handoff_message(
        message,
        filtered_messages,
        pending_parallel_handoffs,
        pending_single_handoffs,
    ):
        return
    _append_unique_message(filtered_messages, message)


def _append_pending_handoffs(
    filtered_messages: list[BaseMessage],
    pending_parallel_handoffs: dict[str, deque[AIMessage]],
    pending_single_handoffs: dict[str, deque[tuple[str, str | None]]],
) -> None:
    for queued_handoffs in pending_parallel_handoffs.values():
        for queued_handoff in queued_handoffs:
            _append_unique_message(filtered_messages, queued_handoff)
            _append_unique_message(
                filtered_messages,
                ToolMessage(
                    content="[no response]",
                    name=queued_handoff.tool_calls[0]["name"],
                    tool_call_id=queued_handoff.tool_calls[0]["id"],
                ),
            )

    for queued_handoffs in pending_single_handoffs.values():
        for tool_name, tool_call_id in queued_handoffs:
            _append_unique_message(
                filtered_messages,
                ToolMessage(content="[no response]", name=tool_name, tool_call_id=tool_call_id),
            )


def _strip_handoff_back_messages_pre_model_hook(state: dict[str, Any]) -> dict[str, Any]:
    messages = state.get("messages", [])
    if not isinstance(messages, list) or not messages:
        return {}

    filtered_messages: list[BaseMessage] = []
    pending_parallel_handoffs: dict[str, deque[AIMessage]] = {}
    pending_single_handoffs: dict[str, deque[tuple[str, str | None]]] = {}

    for message in messages:
        if _is_handoff_back_message(message):
            continue

        _process_handoff_message(
            message,
            filtered_messages,
            pending_parallel_handoffs,
            pending_single_handoffs,
        )

    _append_pending_handoffs(filtered_messages, pending_parallel_handoffs, pending_single_handoffs)

    filtered_messages = sanitize_rich_history_for_llm(filtered_messages)

    if filtered_messages == messages:
        return {}

    return {"llm_input_messages": filtered_messages}


def _strip_subagent_task_messages_pre_model_hook(state: dict[str, Any]) -> dict[str, Any]:
    messages = state.get("messages", [])
    if not isinstance(messages, list) or not messages:
        return {}

    filtered_messages = [
        message
        for message in messages
        if not (
            (isinstance(message, HumanMessage) and message.additional_kwargs.get(METADATA_KEY_SUBAGENT_TASK))
            or (isinstance(message, ToolMessage) and message.additional_kwargs.get(PARALLEL_SUBAGENT_HANDOFF_ACK_KEY))
        )
    ]
    if filtered_messages == messages:
        return {}

    return {"llm_input_messages": filtered_messages}


def _is_subagent_task_message(message: BaseMessage) -> bool:
    return (
        isinstance(message, ToolMessage)
        and bool(message.response_metadata.get(METADATA_KEY_HANDOFF_DESTINATION))
        and (
            message.additional_kwargs.get(METADATA_KEY_SUBAGENT_TASK)
            or message.name.startswith(f"{SUPERVISOR_HANDOFF_TOOL_PREFIX}_")
        )
    )


def _subagent_task_pre_model_hook(state: dict[str, Any]) -> dict[str, Any]:
    messages = state.get("messages", [])
    if not isinstance(messages, list) or not messages:
        return {}

    task_message_index = next(
        (index for index in range(len(messages) - 1, -1, -1) if _is_subagent_task_message(messages[index])),
        None,
    )
    if task_message_index is None:
        return {}

    task_message = messages[task_message_index]
    tail = sanitize_rich_history_for_llm(list(messages[task_message_index + 1 :]))
    llm_input_messages = [HumanMessage(content=task_message.content), *tail]
    return {"llm_input_messages": llm_input_messages}
