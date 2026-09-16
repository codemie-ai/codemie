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

import contextlib
import json
import uuid
from time import time
from typing import Any, ClassVar

from langchain_core.messages import ToolMessage
from pydantic import BaseModel

from codemie.agents.tool_confirmation.models import ToolCallPendingEvent
from codemie.agents.tool_confirmation.utils import agent_op_span
from codemie.chains.base import StreamedGenerationResult, Thought, ThoughtAuthorType
from codemie.configs.logger import logger, set_logging_info
from codemie.core.models import ToolCallPolicy
from codemie.service.conversation_checkpoint_service import ConversationCheckpointService
from codemie.service.llm_service.utils import set_llm_context

_TOOL_CALL_DENIED = "Tool call was denied by the user."
_TOOL_CALL_PENDING = "Tool call is waiting for user approval."


class ToolCallConfirmationMixin:
    """Mixin that adds LangGraph interrupt / resume logic for tool-call confirmation."""

    _pending_tool_confirmation: ClassVar[bool] = False
    tool_pending_event: ClassVar[ToolCallPendingEvent | None] = None

    def ask_for_tool_confirmation(self, config: dict, last_message: str) -> tuple[str, bool]:
        """Detect interrupt, persist pending tool call, emit SSE. Returns (last_message, needs_auto_resume)."""
        state = self._resolve_state(config)
        if not self._has_tool_interrupt(state):
            return last_message, False

        messages = state.values.get("messages", [])
        if not messages:
            return last_message, False

        last_ai_message = messages[-1]

        if not last_ai_message.tool_calls:
            logger.warning(
                f"Graph interrupted before tools node but last AI message has no tool_calls. "
                f"Agent={self.agent_name}, request_uuid={self.request_uuid}. Skipping interrupt handling."
            )
            return last_message, False

        tool_calls = last_ai_message.tool_calls
        tool_call = tool_calls[0]
        tool_call_args = tool_call.get("args", {})

        if self.tool_call_policy == ToolCallPolicy.APPROVE_FOR_ME and all(
            self._tool_call_is_safe(tc) for tc in tool_calls
        ):
            return last_message, True  # resume

        existing = self._find_interrupted_thought()
        base = existing or {}
        pending_event = ToolCallPendingEvent(
            pending_tool_call_id=tool_call["id"],
            tool_name=tool_call["name"],
            tool_args=tool_call_args,
            display_name=base.get("author_name"),
            all_tool_call_ids=[tc["id"] for tc in tool_calls if tc.get("id")],
        )
        ConversationCheckpointService().save_pending_tool_call(self.conversation_id, pending_event)

        self._pending_tool_confirmation = True

        if not self.thread_generator:
            return last_message, False

        interrupted_thought = Thought(
            id=base.get("id") or str(uuid.uuid4()),
            author_name=base.get("author_name", pending_event.tool_name),
            author_type=base.get("author_type", ThoughtAuthorType.Tool),
            input_text=base.get("input_text", json.dumps(tool_call_args)),
            metadata=base.get("metadata", {}),
            message=_TOOL_CALL_PENDING,
            in_progress=False,
            interrupted=True,
        )
        self.thread_generator.send(StreamedGenerationResult(thought=interrupted_thought, last=True).model_dump_json())

        return last_message, False

    @agent_op_span("agent.resume_from_interrupt")
    def resume_from_interrupt(self) -> None:
        """Resume the interrupted graph, allowing the pending tool call to execute."""
        set_logging_info(
            uuid=self.request_uuid,
            user_id=self.user.id,
            conversation_id=self.conversation_id,
            user_email=self.user.username,
        )
        set_llm_context(self.assistant, None, self.user)
        execution_start = time()
        chunks_collector: list[str] = []

        try:
            self.tool_pending_event = ConversationCheckpointService().get_pending_tool_call(self.conversation_id)
            run_config = self._get_run_config()
            trace_ctx = run_config.pop("_trace_ctx", contextlib.nullcontext())

            with trace_ctx:
                result = self._stream_graph(None, run_config, chunks_collector)
            self.tool_pending_event = None

            if self._pending_tool_confirmation:
                return
            result = json.dumps(result) if isinstance(result, (dict, BaseModel)) else result

            self.thread_generator.send(
                StreamedGenerationResult(
                    generated=result,
                    generated_chunk="",
                    last=True,
                    time_elapsed=time() - execution_start,
                    context=self.thread_context,
                ).model_dump_json()
            )
        except Exception as e:
            self._send_error_to_thread(e, execution_start, chunks_collector)
        finally:
            self.thread_generator.close()

    def deny_unreviewed_sibling_tool_calls(self, pending: ToolCallPendingEvent | None) -> None:
        """Deny every tool call in the batch except the one shown in the confirmation dialog.

        A single AI turn can emit multiple tool calls, but only ``pending_tool_call_id`` is
        surfaced for review. Allowing that one call must not also execute unreviewed siblings
        when the graph step resumes, so they are denied up front.
        """
        if pending is None:
            return

        sibling_ids = [tid for tid in (pending.all_tool_call_ids or []) if tid != pending.pending_tool_call_id]
        if not sibling_ids:
            return

        run_config = self._get_run_config()
        thread_id = run_config.get("thread_id", self.conversation_id)
        update_config = {**run_config, "configurable": {**run_config.get("configurable", {}), "thread_id": thread_id}}
        deny_messages = [ToolMessage(content=_TOOL_CALL_DENIED, tool_call_id=tid) for tid in sibling_ids]

        self.agent_executor.update_state(
            update_config,
            {"messages": deny_messages},
            as_node="tools",
        )

    @agent_op_span("agent.reject_tool_call")
    def reject_tool_call(self, pending: ToolCallPendingEvent | None) -> None:
        """Inject denial ToolMessage then resume graph via resume_from_interrupt."""
        if pending is None:
            return

        tool_call_id = pending.pending_tool_call_id

        if self.thread_generator:
            aborted_thought = Thought(
                id=str(self._tool_call_id_to_uuid(tool_call_id)),
                author_name=pending.display_name or pending.tool_name,
                author_type=ThoughtAuthorType.Tool,
                input_text=json.dumps(pending.tool_args),
                message=_TOOL_CALL_DENIED,
                in_progress=False,
                aborted=True,
                interrupted=False,
            )
            self.thread_generator.send(StreamedGenerationResult(thought=aborted_thought).model_dump_json())

        run_config = self._get_run_config()
        thread_id = run_config.get("thread_id", self.conversation_id)
        update_config = {**run_config, "configurable": {**run_config.get("configurable", {}), "thread_id": thread_id}}
        all_ids = pending.all_tool_call_ids or [tool_call_id]
        deny_messages = [ToolMessage(content=_TOOL_CALL_DENIED, tool_call_id=tid) for tid in all_ids]

        self.agent_executor.update_state(
            update_config,
            {"messages": deny_messages},
            as_node="tools",
        )
        self.resume_from_interrupt()

    def _get_checkpoint_config(self) -> dict:
        """Return the __pregel_checkpointer run-config block for this agent."""
        if self.require_tool_confirmation:
            from codemie.agents.tool_confirmation.conversation_checkpoint_saver import ConversationCheckpointSaver
            from codemie.service.conversation_checkpoint_service import ConversationCheckpointService

            checkpointer = ConversationCheckpointSaver(checkpoint_service=ConversationCheckpointService())
            return {
                "__pregel_checkpointer": checkpointer,
                "max_concurrency": self.MAX_CONCURRENCY,
                "thread_id": self.conversation_id,
            }
        from langgraph.checkpoint.memory import InMemorySaver

        return {
            "__pregel_checkpointer": InMemorySaver(),
            "max_concurrency": self.MAX_CONCURRENCY,
            "thread_id": "thread",
        }

    def _replay_tool_start_on_resume(self, action: Any) -> None:
        """Fire _on_tool_start retroactively on the allow-resume path.

        On resume LangGraph doesn't re-emit the agent chunk with tool_calls, so
        _on_tool_start is never called. We fire it here using the stored
        tool_pending_event so that _on_tool_end can find and close the thought.
        """
        if not (self.require_tool_confirmation and self.tool_pending_event):
            return

        if action.tool_call_id != self.tool_pending_event.pending_tool_call_id:
            return

        run_id = self._tool_call_id_to_uuid(action.tool_call_id or "")

        try:
            self._on_tool_start(
                self.tool_pending_event.display_name or self.tool_pending_event.tool_name,
                json.dumps(self.tool_pending_event.tool_args),
                run_id=run_id,
            )
        finally:
            self.tool_pending_event = None

    def _resolve_state(self, config: dict) -> Any:
        thread_id = config.get("thread_id", self.conversation_id)
        state_config = {**config, "configurable": {**config.get("configurable", {}), "thread_id": thread_id}}
        return self.agent_executor.get_state(state_config)

    def _has_tool_interrupt(self, state: Any) -> bool:
        return bool(state.next and "tools" in state.next)

    def _tool_call_is_safe(self, tool_call: dict) -> bool:
        tool = next((t for t in self.tools if t.name == tool_call["name"]), None)
        return tool is not None and tool.is_safe(tool_call.get("args", {}))

    def _find_interrupted_thought(self) -> dict | None:
        """Return the latest in-progress thought sent by _on_tool_start, or None."""
        if not self.thread_generator:
            return None

        return next((t for t in reversed(self.thread_generator.thoughts) if t.get("in_progress")), None)
