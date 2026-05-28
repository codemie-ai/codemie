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
from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID

from langchain_core.messages import AIMessage, HumanMessage

from codemie.agents.supervisor.constants import METADATA_KEY_HANDOFF_BACK
from codemie.core.utils import extract_text_from_llm_output, unpack_json_strings


@dataclass(frozen=True)
class _SupervisorChunkContext:
    """Normalized view over LangGraph supervisor chunk metadata."""

    chunk_type: str
    value: Any
    raw_author: str
    author_key: str
    author: str | None
    delegated_task: str | None

    @classmethod
    def from_chunk(cls, chunk: tuple[Any, str, Any], default_name: str = "supervisor") -> "_SupervisorChunkContext":
        chunk_author, chunk_type, value = chunk
        author_key = default_name
        raw_author = default_name

        if chunk_author:
            author_key = chunk_author[-1]
            raw_author = author_key.split(":")[0]

        delegated_task = None
        if chunk_type == "messages" and isinstance(value, tuple) and value:
            message = value[0]
            if isinstance(message, HumanMessage) and isinstance(message.content, str):
                delegated_task = message.content

        return cls(
            chunk_type=chunk_type,
            value=value,
            raw_author=raw_author,
            author_key=author_key,
            author=None if raw_author == default_name else author_key,
            delegated_task=delegated_task,
        )


HandoffRunBinding = tuple[UUID, str | None]
PendingHandoff = tuple[UUID, str | None, str, str | None]


class _SupervisorHandoffTracker:
    """Track pending and active supervisor handoffs across subagent chunks."""

    def __init__(self) -> None:
        self.run_bindings: dict[str, HandoffRunBinding] = {}
        self.pending: dict[str, deque[PendingHandoff]] = {}
        self.active: dict[str, list[PendingHandoff]] = {}

    def clear_pending(self) -> None:
        self.pending.clear()

    def queue_pending(self, raw_author: str, handoff: PendingHandoff) -> None:
        self.pending.setdefault(raw_author, deque()).append(handoff)

    def has_pending(self, raw_author: str) -> bool:
        return raw_author in self.pending

    def promote_pending(self, raw_author: str, task: str | None) -> PendingHandoff:
        pending_handoffs = self.pending[raw_author]
        handoff = self._pop_pending_handoff_for_task(pending_handoffs, task)
        if not pending_handoffs:
            self.pending.pop(raw_author, None)
        return handoff

    def activate(self, raw_author: str, author: str, handoff: PendingHandoff) -> HandoffRunBinding:
        self.active.setdefault(raw_author, []).append(handoff)
        binding = (handoff[0], handoff[1])
        self.run_bindings[author] = binding
        return binding

    def binding_for(self, author: str | None) -> HandoffRunBinding | None:
        if not author:
            return None
        return self.run_bindings.get(author)

    def rebind_author(self, author: str, raw_author: str, task: str) -> HandoffRunBinding | None:
        active_handoff = next((handoff for handoff in self.active.get(raw_author, []) if handoff[2] == task), None)
        if not active_handoff:
            return None

        binding = (active_handoff[0], active_handoff[1])
        if self.run_bindings.get(author) == binding:
            return None

        self.run_bindings[author] = binding
        return binding

    def complete(self, author: str | None) -> HandoffRunBinding | None:
        if not author:
            return None

        binding = self.run_bindings.pop(author, None)
        if not binding:
            return None

        self._remove_active_handoff(binding[0])
        return binding

    @staticmethod
    def _pop_pending_handoff_for_task(
        pending_handoffs: deque[PendingHandoff],
        task: str | None,
    ) -> PendingHandoff:
        if task:
            for index, pending_handoff in enumerate(pending_handoffs):
                if pending_handoff[2] == task:
                    matched_handoff = pending_handoff
                    del pending_handoffs[index]
                    return matched_handoff
        return pending_handoffs.popleft()

    def _remove_active_handoff(self, run_id: UUID) -> None:
        for raw_author, active_handoffs in list(self.active.items()):
            remaining_handoffs = [handoff for handoff in active_handoffs if handoff[0] != run_id]
            if remaining_handoffs:
                self.active[raw_author] = remaining_handoffs
            else:
                self.active.pop(raw_author, None)


class SupervisorCoordinator:
    def __init__(
        self,
        *,
        handoff_tool_prefix: str,
        extract_agent_name_from_tool: Callable[[str], str],
        tool_call_id_to_uuid: Callable[[str], UUID],
        resolve_display_name: Callable[[str, int, int], str],
        emit_handoff: Callable[..., None],
        emit_subassistant_back: Callable[..., None],
        set_thread_context: Callable[..., None],
    ) -> None:
        self.handoff_tool_prefix = handoff_tool_prefix
        self.extract_agent_name_from_tool = extract_agent_name_from_tool
        self.tool_call_id_to_uuid = tool_call_id_to_uuid
        self.resolve_display_name = resolve_display_name
        self.emit_handoff = emit_handoff
        self.emit_subassistant_back = emit_subassistant_back
        self.set_thread_context = set_thread_context
        self.tracker = _SupervisorHandoffTracker()

    def binding_for(self, author: str | None) -> HandoffRunBinding | None:
        return self.tracker.binding_for(author)

    def queue_supervisor_handoffs(self, handoff_calls: list[dict[str, Any]], author: str | None = None) -> None:
        self.tracker.clear_pending()
        handoff_counts: dict[str, int] = {}
        handoff_indexes: dict[str, int] = {}
        for tool_call in handoff_calls:
            agent_name = self.extract_agent_name_from_tool(tool_call["name"])
            handoff_counts[agent_name] = handoff_counts.get(agent_name, 0) + 1

        for tool_call in handoff_calls:
            agent_name = self.extract_agent_name_from_tool(tool_call["name"])
            run_id = self.tool_call_id_to_uuid(tool_call.get("id", ""))
            tool_args = unpack_json_strings(tool_call.get("args", {}))
            task = tool_args.get("task", "") if isinstance(tool_args, dict) else ""
            handoff_indexes[agent_name] = handoff_indexes.get(agent_name, 0) + 1
            display_name = self.resolve_display_name(
                agent_name,
                handoff_indexes[agent_name],
                handoff_counts[agent_name],
            )
            self.tracker.queue_pending(agent_name, (run_id, author, task, display_name))

    def handle_supervisor_handoff_back(
        self,
        messages: list[Any],
        run_id: HandoffRunBinding | None,
        author: str | None = None,
    ) -> None:
        subassistant_answer = ""
        for message in reversed(messages):
            if getattr(message, "response_metadata", {}).get(METADATA_KEY_HANDOFF_BACK):
                continue
            if getattr(message, "content", None):
                subassistant_answer = extract_text_from_llm_output(str(message.content))
                break

        if not run_id:
            return

        completed_binding = self.tracker.complete(author) or run_id
        actual_run_id, supervisor_author = completed_binding
        self.emit_subassistant_back(subassistant_answer, actual_run_id, supervisor_author)

    def handle_supervisor_subassistant_result(
        self,
        messages: list[Any],
        run_id: HandoffRunBinding | None,
        author: str | None = None,
    ) -> None:
        if not run_id:
            return

        last_message = messages[-1] if messages else None
        if not isinstance(last_message, AIMessage):
            return
        if getattr(last_message, "tool_calls", None):
            return

        subassistant_answer = extract_text_from_llm_output(str(last_message.content or ""))
        completed_binding = self.tracker.complete(author) or run_id
        actual_run_id, supervisor_author = completed_binding
        self.emit_subassistant_back(subassistant_answer, actual_run_id, supervisor_author)

    def promote_pending_handoff(self, context: _SupervisorChunkContext) -> None:
        if not context.author or not self.tracker.has_pending(context.raw_author):
            return

        run_id, supervisor_author, task, display_name = self.tracker.promote_pending(
            context.raw_author,
            context.delegated_task,
        )

        self.emit_handoff(
            f"{self.handoff_tool_prefix}_{context.raw_author}",
            run_id,
            task,
            author=supervisor_author,
            display_name=display_name,
        )
        self.tracker.activate(
            context.raw_author,
            context.author,
            (run_id, supervisor_author, task, display_name),
        )
        self.set_thread_context(context={}, parent_thought_id=str(run_id), author=context.author)

    def rebind_author_to_active_handoff(self, author: str, raw_author: str, task: str) -> None:
        binding = self.tracker.rebind_author(author, raw_author, task)
        if not binding:
            return

        run_id, _ = binding
        self.set_thread_context(context={}, parent_thought_id=str(run_id), author=author)
