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
from collections.abc import Mapping, Sequence
from time import time
from typing import Any
from uuid import UUID

from langchain_core.tools import BaseTool
from langgraph.config import get_config
from langgraph.types import Command
from pydantic import BaseModel

from codemie.agents.agent_runtime_utils import with_captured_origin
from codemie.agents.tool_confirmation.call_identity import CallIdentity, agent_author_keys, delegating_location_key
from codemie.agents.tool_confirmation.confirmation_gate import (
    DECISIONS_CONFIG_KEY,
    GATE_INTERRUPT_KIND,
    GateCall,
    GateInterrupt,
    build_confirmation_hook,
    build_decision_carrier,
)
from codemie.agents.tool_confirmation.models import (
    AppliedDecision,
    DecisionItem,
    StoredPendingToolCall,
    ToolCallPendingEvent,
)
from codemie.agents.tool_confirmation.resume_payload import build_resume_payload
from codemie.agents.tool_confirmation.utils import agent_op_span
from codemie.chains.base import StreamedGenerationResult, Thought, ThoughtAuthorType
from codemie.core.errors import ErrorDetailLevel
from codemie.configs.logger import logger, set_logging_info
from codemie.core import tool_step_states as step_states
from codemie.core.thread import _find_thought as find_thought
from codemie.repository.conversation_pause_repository import ConversationPauseRepository
from codemie.service.conversation_checkpoint_service import ConversationCheckpointService
from codemie.service.llm_service.utils import set_llm_context

_RESUME_ACK = "ack"


class ToolCallConfirmationMixin:
    """Mixin that finds the confirmation gate's pause, stores it, and resumes it with the decisions.

    The gate (``confirmation_gate.build_confirmation_hook``) interrupts in the agent's ``post_model_hook``.
    After a stream, the pending gate interrupts are read from the outer ``get_state(cfg).tasks[].interrupts``
    and stored as one format-2 record. The resume sends every decision in the run-config carrier and one
    ``Command`` acknowledging every pending interrupt id.

    The host sets ``_pending_tool_confirmation``, ``tool_pending_events``, ``can_resume_pause`` and
    ``tree_requires_confirmation`` per instance.
    """

    _pending_tool_confirmation: bool
    can_resume_pause: bool
    # Class default: an agent created without ``__init__`` (tests) belongs to a tree that does not confirm.
    tree_requires_confirmation: bool = False
    tool_pending_events: dict[str, ToolCallPendingEvent]

    def _confirmation_hook(self, tools: list[BaseTool]):
        """The ``post_model_hook`` of every LangGraph agent: it always assigns the call identities (rule 1).

        It pauses the agent's own calls only when the agent's policy requires confirmation. In a run that cannot be
        resumed (``can_resume_pause`` false) the gate answers the calls needing confirmation as unavailable (rule 9).
        """
        return build_confirmation_hook(
            self.tool_call_policy,
            tools,
            is_subagent=self.is_subagent,
            pauses_own_calls=self.require_tool_confirmation,
            resumable=self.can_resume_pause,
        )

    @staticmethod
    def _delegating_location_key() -> str:
        """The location key of the agent running the handoff tool; ``""`` at the root or outside a graph run.

        With the handoff call's id it is the call identity of the delegation step, the sub-agent's container.
        """
        try:
            checkpoint_ns = (get_config().get("configurable") or {}).get("checkpoint_ns") or ""
        except RuntimeError:
            return ""
        return delegating_location_key(checkpoint_ns)

    def ask_for_tool_confirmation(self, config: dict[str, Any]) -> bool:
        """Store the gate's pause, then stream its waiting steps. Returns whether the run is paused.

        The pending row is written before the first waiting step streams, so a resume sent right after the last
        waiting step finds it. The row holds the generator's thoughts as they are after the waiting steps.
        """
        pending = self._pending_gate_interrupts(config)
        if not pending:
            return False

        items = [self._decision_item(call) for _, gate in pending for call in gate.calls]
        waiting = [self._waiting_chunk(item) for item in items if item.shown]
        request = self._pause_request()
        record = StoredPendingToolCall(
            items=items,
            root_assistant_id=str(getattr(self.assistant, "id", "") or ""),
            history_index=getattr(request, "history_index", None),
            original_user_message=getattr(request, "text", None) or "",
            thoughts=self.thread_generator.thoughts_after([json.loads(chunk)["thought"] for chunk in waiting]),
            payload=build_resume_payload(self, carried_ids=self._carried_ids(items)),
        )
        ConversationCheckpointService().write_pause(self.conversation_id, record)
        # Set before any waiting step streams: a streaming failure still leaves the run paused.
        self._pending_tool_confirmation = True

        for chunk in waiting:
            self.thread_generator.send(chunk)
        return True

    def _pause_request(self) -> Any:
        """The request as the handler received it (``history_index`` set by the placeholder save)."""
        return getattr(self, "received_request", None) or self.request

    @property
    def _uses_conversation_saver(self) -> bool:
        """Only a run that can be resumed (a stream with history on) stores its checkpoints.

        Only there does the placeholder save create the conversation row before the build, which the checkpoint
        rows' FK needs; any other run never pauses (rule 9).
        """
        return self.tree_requires_confirmation and bool(self.conversation_id) and self.can_resume_pause

    def _delete_checkpoints_after_run(self) -> None:
        """Delete the conversation's checkpoint rows after a run that ended without a pause (rule 6).

        Runs in the stream's ``finally``: it never raises, so the stream is always closed after it.
        """
        try:
            if not self._uses_conversation_saver or self._pending_tool_confirmation:
                return
            ConversationCheckpointService().delete_checkpoints_if_no_pending(self.conversation_id)
        except Exception as error:
            logger.error(f"Could not delete the checkpoint rows of conversation {self.conversation_id}: {error}")

    def _carried_ids(self, items: Sequence[DecisionItem]) -> list[str]:
        """The allowed steps still running at a re-pause: the generator's allowed ids without a result yet.

        The pause's own items are not carried (they wait), and a step that already finished is not running.
        """
        if not self.thread_generator:
            return []
        own = {item.thought_id for item in items}
        running = []
        for thought_id in sorted(self.thread_generator.allowed_ids - own):
            step = self._find_thought(thought_id)
            if step is not None and step.get("in_progress"):
                running.append(thought_id)
        return running

    @agent_op_span("agent.resume_from_interrupt")
    @with_captured_origin
    def resume_from_interrupt(
        self,
        applied: Sequence[AppliedDecision],
        include_tool_errors: bool = False,
        error_detail_level: ErrorDetailLevel = ErrorDetailLevel.STANDARD,
    ) -> None:
        """Resume the paused graph once, applying every decision of the pause.

        ``applied`` carries every call of the pause with its action (auto-allowed ones as allow). Every pending gate
        interrupt of the graph, read from the stored checkpoints, is resumed; a call no decision names is denied by
        its gate (fails closed). Every agent on a paused call's path is bound again to its container, so the resumed
        steps nest where the paused turn left them. The generator is seeded by the caller; denied steps are not
        streamed again, their state is in the seed. The error chunk follows ``stream``'s rules
        (``include_tool_errors``, ``error_detail_level``).
        """
        set_logging_info(
            uuid=self.request_uuid,
            user_id=self.user.id,
            conversation_id=self.conversation_id,
            username=self.user.username,
        )
        set_llm_context(self.assistant, None, self.user, self.llm_model)
        execution_start = time()
        chunks_collector: list[str] = []

        try:
            run_config = self._get_run_config()
            trace_ctx = run_config.pop("_trace_ctx", contextlib.nullcontext())
            pending_ids = [interrupt_id for interrupt_id, _ in self._pending_gate_interrupts(run_config)]

            self.tool_pending_events = tool_pending_events(applied)
            self._bind_resumed_authors(applied)
            run_config["configurable"] = {
                **(run_config.get("configurable") or {}),
                DECISIONS_CONFIG_KEY: build_decision_carrier(applied),
            }
            resume = Command(resume={interrupt_id: _RESUME_ACK for interrupt_id in pending_ids})

            with trace_ctx:
                result = self._stream_graph(resume, run_config, chunks_collector)
            self.tool_pending_events = {}

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
                    success=True,
                ).model_dump_json()
            )
        except Exception as e:
            self._supervisor_coordinator.reset()
            self._send_error_to_thread(
                e,
                execution_start,
                chunks_collector,
                include_tool_errors=include_tool_errors,
                error_detail_level=error_detail_level,
            )
        finally:
            self._delete_checkpoints_after_run()
            self.thread_generator.close()

    def _bind_resumed_authors(self, applied: Sequence[AppliedDecision]) -> None:
        """Bind every agent on a nested item's path to its container again (rule 10, resume).

        On resume no handoff is streamed again, so the stream would not know where a resumed sub-assistant's
        steps belong. The paused agent is bound to the item's container; each agent above it to the container
        of its own delegation step, read from the saved thoughts.
        """
        for decision in applied:
            item = decision.item
            location = item.event.location
            if location is None or not location.parent_thought_id:
                continue
            authors = agent_author_keys(item.checkpoint_ns)
            container = location.parent_thought_id
            for author in reversed(authors):
                if not container:
                    break
                self._bind_author_to_container(author, container)
                container = (self._find_thought(container) or {}).get("parent_id") or ""

    def _bind_author_to_container(self, author: str, container: str) -> None:
        coordinator = getattr(self, "_supervisor_coordinator", None)
        if coordinator is None or coordinator.binding_for(author) is not None:
            return
        try:
            container_id = UUID(container)
        except ValueError:
            # Not a step id (e.g. a sub-agent's "latest" placeholder): nothing to bind to.
            return
        raw_author = author.split(":")[0]
        coordinator.tracker.activate(raw_author, author, (container_id, None, "", None))
        self.set_thread_context(context={}, parent_thought_id=container, author=author)

    def _pending_gate_interrupts(self, config: dict[str, Any]) -> list[tuple[str, GateInterrupt]]:
        """Gate interrupts of unfinished tasks; a finished task keeps stale interrupts and is skipped."""
        state = self._resolve_state(config)
        pending: dict[str, GateInterrupt] = {}
        for task in getattr(state, "tasks", ()) or ():
            if task.result is not None:
                continue
            for pending_interrupt in task.interrupts:
                value = pending_interrupt.value
                if isinstance(value, Mapping) and value.get("kind") == GATE_INTERRUPT_KIND:
                    pending.setdefault(pending_interrupt.id, GateInterrupt.model_validate(value))
        return list(pending.items())

    def _decision_item(self, call: GateCall) -> DecisionItem:
        # The streamed step's id (the adapter's ``_step_run_id``): the call identity's step key.
        step_key = CallIdentity(wire_id=call.tool_call_id, location_key=call.location_key).step_key
        thought_id = str(self._tool_call_id_to_uuid(step_key))
        started = self._find_thought(thought_id) or {}
        return DecisionItem(
            thought_id=thought_id,
            tool_call_id=call.tool_call_id,
            location_key=call.location_key,
            checkpoint_ns=call.checkpoint_ns,
            event=call.event.model_copy(update={"display_name": started.get("author_name")}),
            auto_allowed=call.decision == "allow",
            auto_denied=call.decision == "deny",
        )

    def _waiting_chunk(self, item: DecisionItem) -> str:
        """The streamed chunk of a shown call's waiting step."""
        base = self._find_thought(item.thought_id) or {}
        event = item.event
        step = step_states.waiting(
            {
                "id": item.thought_id,
                "parent_id": _container_id(item),
                "author_name": base.get("author_name", event.tool_name),
                "author_type": base.get("author_type", ThoughtAuthorType.Tool),
                "input_text": base.get("input_text", json.dumps(event.tool_args)),
                "metadata": base.get("metadata", {}),
            }
        )
        waiting = Thought(**step)
        return StreamedGenerationResult(thought=waiting, last=True).model_dump_json()

    def _send_unavailable_step(self, thought_id: str) -> None:
        """Mark a started step unavailable: the gate refused its call without a pause (rule 9).

        A streamed run sends the state through its generator. A run with no generator (sync, background) keeps
        its steps in the invoke callback's thoughts, which the handler saves, so the state is set there.
        """
        if not self.thread_generator:
            step = find_thought(self.get_thoughts_from_callback(), thought_id)  # type: ignore[attr-defined]
            if step is not None:
                step.update(step_states.unavailable(step))
            return
        step = self._find_thought(thought_id)
        if step is None:
            return
        state = step_states.unavailable({key: value for key, value in step.items() if key != "children"})
        self.thread_generator.send(StreamedGenerationResult(thought=Thought(**state), last=True).model_dump_json())

    def _get_checkpoint_config(self) -> dict:
        """Return the __pregel_checkpointer run-config block for this agent.

        The conversation saver only for a run that can be resumed: one with no stream or with history off has no
        conversation row guaranteed for the checkpoint rows' FK, and its pause is never stored.
        """
        if self._uses_conversation_saver:
            from codemie.agents.tool_confirmation.conversation_checkpoint_saver import ConversationCheckpointSaver

            checkpointer = ConversationCheckpointSaver(ConversationPauseRepository())
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

    def _replay_tool_start_on_resume(
        self, action: Any, author: str | None = None, location_key: str = "", wire_id: str | None = None
    ) -> None:
        """Fire _on_tool_start retroactively for an allowed call on the resume path.

        On resume LangGraph doesn't re-emit the agent chunk with tool_calls, so
        _on_tool_start is never called. We fire it here from the allowed call's stored
        event so that _on_tool_end can find and close the thought. The step id is the call
        identity's step key, so a nested call's step is the one the paused turn streamed.

        ``wire_id`` is the call's wire id, which the stored events are keyed by; the tool message keeps the raw id,
        so the caller resolves it first. ``None`` means the raw id is the wire id.
        """
        wire_id = (action.tool_call_id or "") if wire_id is None else wire_id
        step_key = CallIdentity(wire_id=wire_id, location_key=location_key).step_key
        event = (self.tool_pending_events or {}).pop(step_key, None)
        if event is None:
            return

        run_id = self._tool_call_id_to_uuid(step_key)

        # The thought is named by display_name, which the bridge cannot match to a tool; resolve by the real name.
        try:
            metadata = self._callback_bridge.attribution_metadata(event.tool_name)
        except Exception as error:
            logger.warning(
                f"Could not resolve tool metadata for '{event.tool_name}' on resume; "
                f"the thought is streamed without tool attribution: {error}"
            )
            metadata = {}
        if author is None:
            self._on_tool_start(
                event.display_name or event.tool_name, json.dumps(event.tool_args), run_id=run_id, metadata=metadata
            )
            return
        self._on_tool_start(
            event.display_name or event.tool_name,
            json.dumps(event.tool_args),
            run_id=run_id,
            author=author,
            metadata=metadata,
        )

    def _resolve_state(self, config: dict[str, Any], subgraphs: bool = False) -> Any:
        """The checkpointed graph state; `subgraphs` also loads the sub-graph state of each task of the next step."""
        thread_id = config.get("thread_id", self.conversation_id)
        state_config = {**config, "configurable": {**config.get("configurable", {}), "thread_id": thread_id}}
        if subgraphs:
            return self.agent_executor.get_state(state_config, subgraphs=True)
        return self.agent_executor.get_state(state_config)

    def _find_thought(self, thought_id: str) -> dict[str, Any] | None:
        """Return the latest streamed thought with ``thought_id`` at any depth of the step tree, or None."""
        if not self.thread_generator:
            return None
        return find_thought(self.thread_generator.thoughts, thought_id)


def tool_pending_events(applied: Sequence[AppliedDecision]) -> dict[str, ToolCallPendingEvent]:
    """The stored events of the allowed calls by step key, for the replayed tool start of a resumed run."""
    return {
        CallIdentity(wire_id=d.item.tool_call_id, location_key=d.item.location_key).step_key: d.item.event
        for d in applied
        if d.action == "allow"
    }


def _container_id(item: DecisionItem) -> str | None:
    """The waiting step's immediate container: its delegation step below the root, none at the root."""
    location = item.event.location
    return (location.parent_thought_id or None) if location is not None else None
