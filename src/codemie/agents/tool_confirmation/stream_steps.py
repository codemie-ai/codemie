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

"""The call-identity routing of the stream's tool and handoff steps (EPMCDME-16463).

An AI message's steps start only once its call identities are known (the output of the agent's ``post_model_hook``).
``LangGraphEventAdapter`` owns one ``ConfirmationStepRouter``, which holds the deferred steps and starts them
through the adapter's own step starters.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from codemie.agents.tool_confirmation.call_identity import CallIdentity, RoutedCall, agent_location_key
from codemie.agents.tool_confirmation.confirmation_gate import GATE_INTERRUPT_KIND
from codemie.core.tool_step_states import UNAVAILABLE_MESSAGE

_POST_MODEL_HOOK = "post_model_hook"
_INTERRUPT = "__interrupt__"
_NS_SEP = "|"


@dataclass
class _DeferredSteps:
    """An AI message whose tool and handoff steps wait for its call identities (the gate's output)."""

    message: AIMessage
    author: str | None
    location_key: str
    supervisor: bool


class ConfirmationStepRouter:
    """Defers an agent's steps to its call identities and starts them from the gate's output.

    ``start_supervisor_steps`` and ``start_tool_steps`` are the owning adapter's step starters.
    """

    def __init__(
        self,
        agent: Any,
        start_supervisor_steps: Callable[..., None],
        start_tool_steps: Callable[..., None],
    ) -> None:
        self.agent = agent
        self._start_supervisor_steps = start_supervisor_steps
        self._start_tool_steps = start_tool_steps
        # Per agent namespace: the AI messages whose steps start once their call identities are known, in order.
        self._deferred_steps: dict[str, list[_DeferredSteps]] = {}
        # Gate interrupts whose steps were started (a supervisor's interrupt streams at its namespace and the root).
        self._started_interrupts: set[str] = set()
        # Root calls already streamed as unavailable: a smart-selection wrapper's node update repeats its messages.
        self._unavailable_ids: set[str] = set()
        # (location key, raw id) -> the wire ids the gate assigned to that location's calls, in call order. The message
        # keeps the raw id, so a tool's result is resolved to its own wire id here, in the order its step was started.
        # Keyed by location so two agents whose raw ids collide never share a queue.
        self._wire_ids: dict[tuple[str, str], deque[str]] = {}

    @staticmethod
    def namespace_key(namespace: tuple[str, ...]) -> str:
        """The key the deferred steps of an agent namespace are kept under; ``""`` for the root agent."""
        return _NS_SEP.join(namespace)

    @staticmethod
    def location_key(namespace: tuple[str, ...]) -> str:
        """The location key of the agent a chunk comes from, as its gate derives it; ``""`` for the root agent."""
        return agent_location_key(namespace)

    def step_run_id(self, wire_id: str, location_key: str) -> UUID:
        """The step id of a call: ``_tool_call_id_to_uuid`` of its identity's step key (the wire id at the root)."""
        if not location_key:
            return self.agent._tool_call_id_to_uuid(wire_id)
        return self.agent._tool_call_id_to_uuid(CallIdentity(wire_id=wire_id, location_key=location_key).step_key)

    def defer(
        self, namespace: tuple[str, ...], message: AIMessage, author: str | None, location_key: str, supervisor: bool
    ) -> None:
        """Hold the steps of ``message`` until the gate of the agent at ``namespace`` assigns its identities."""
        self._deferred_steps.setdefault(self.namespace_key(namespace), []).append(
            _DeferredSteps(message, author, location_key, supervisor=supervisor)
        )

    def flush_deferred(self) -> None:
        """Start every step still deferred when the stream ends (no hook or interrupt chunk came for it)."""
        while self._deferred_steps:
            _, deferred = self._deferred_steps.popitem()
            for steps in deferred:
                self._start_deferred(steps, steps.message.tool_calls)

    def is_interrupt_update(self, chunk_type: str, value: Any) -> bool:
        """An ``updates`` chunk that carries a gate's interrupt."""
        return chunk_type == "updates" and isinstance(value, dict) and _INTERRUPT in value

    def consume_identity_chunk(self, value: Any, namespace_key: str) -> bool:
        """Start deferred steps from the gate's output; ``True`` when the chunk is the gate's own.

        The gate (``post_model_hook``) never changes the AI message: it returns each call's identity beside its raw id
        (``codemie_call_identities``), or nothing when every raw id is kept (``None`` update). On a pausing pass it
        interrupts first, and its payload lists the same identities. Either way the steps start from the identity the
        gate assigned, never from a second derivation. The hook's other messages (denials on resume) are not tool
        results of the stream.
        """
        if not isinstance(value, dict):
            return False
        if _POST_MODEL_HOOK in value:
            self._start_deferred_steps(namespace_key, value[_POST_MODEL_HOOK])
            return True
        if _INTERRUPT in value:
            self._start_interrupted_steps(value[_INTERRUPT])
            return True
        # A graph without a hook (e.g. the smart-selection agent) never sends one: start with the message's ids.
        self._start_deferred_steps(namespace_key, None)
        if not namespace_key and not getattr(self.agent, "subagents", None):
            self._start_wrapped_unavailable_steps(value)
        return False

    def resolve_wire_id(self, raw_id: str, location_key: str = "") -> str:
        """The wire id of the next started call with this raw id at this location; the raw id when none is queued.

        Results are popped in start order. That holds because LangGraph's ``ToolNode`` runs a turn's calls under
        ``asyncio.gather``, which returns them in call order whatever order they finish in, and the adapter reads
        that list in the same order. So calls sharing a raw id at one location resolve to the wire ids they
        started with.
        """
        key = (location_key, raw_id)
        queue = self._wire_ids.get(key)
        if not queue:
            return raw_id
        wire_id = queue.popleft()
        if not queue:
            del self._wire_ids[key]
        return wire_id

    def _start_deferred_steps(self, namespace_key: str, hook_update: Any) -> None:
        routed = _routed_calls(hook_update)
        messages = hook_update.get("messages", []) if isinstance(hook_update, dict) else []
        answers = [m for m in messages if isinstance(m, ToolMessage)]
        # Calls the gate answered as unavailable (a run that cannot be resumed): their steps start stopped.
        unavailable_ids = {m.tool_call_id for m in answers if m.content == UNAVAILABLE_MESSAGE}
        # Registered once per gate output, whether or not a step is deferred: a resume re-runs the gate but never
        # re-streams the AI message, and its tool results must still resolve to the wire ids the pause stored.
        self._register_wire_ids(routed, {m.tool_call_id for m in answers})
        for deferred in self._deferred_steps.pop(namespace_key, []):
            self._start_identified(deferred, routed, unavailable_ids)

    def _start_identified(self, deferred: _DeferredSteps, routed: list[RoutedCall], unavailable_ids: set[str]) -> None:
        """Start a deferred message's steps under the gate's wire ids; unavailable calls start stopped."""
        raw_calls = deferred.message.tool_calls
        wire_calls = _calls_with_wire_ids(raw_calls, routed)
        self._start_deferred(deferred, wire_calls)
        location_key = deferred.location_key if deferred.supervisor else ""
        for raw_call, wire_call in zip(raw_calls, wire_calls, strict=True):
            if (raw_call.get("id") or "") in unavailable_ids:
                self._send_unavailable(wire_call["id"], location_key)

    def _send_unavailable(self, wire_id: str, location_key: str) -> None:
        if not location_key:
            self._unavailable_ids.add(wire_id)
        self.agent._send_unavailable_step(str(self.step_run_id(wire_id, location_key)))

    def _register_wire_ids(self, routed: list[RoutedCall], answered_ids: set[str]) -> None:
        """Queue each call's wire id under its location and raw id, in call order, for its tool result to resolve.

        A call the gate already answered (denied or unavailable) never reaches tools, so no tool result would drain its
        wire id: only the calls that will run are queued. The location is the identity's own, which is the one the
        adapter derives for that agent's tool results (``agent_location_key`` matches the gate's derivation).
        """
        for call in routed:
            if call.raw_id not in answered_ids:
                key = (call.identity.location_key, call.raw_id)
                self._wire_ids.setdefault(key, deque()).append(call.identity.wire_id)

    def _start_wrapped_unavailable_steps(self, update: dict[str, Any]) -> None:
        """Start the unavailable calls of a smart-selection root as stopped steps.

        Its gate runs inside the wrapper's ``agent`` node, whose inner chunks never stream; the node's update holds
        the turn's messages, so the calls the gate answered as unavailable are read from there.
        """
        for node_update in update.values():
            messages = node_update.get("messages", []) if isinstance(node_update, dict) else []
            turn = messages[_last_human_index(messages) + 1 :]
            unavailable_ids = {
                m.tool_call_id for m in turn if isinstance(m, ToolMessage) and m.content == UNAVAILABLE_MESSAGE
            } - self._unavailable_ids
            for message in turn:
                self._start_unavailable_calls(message, unavailable_ids)

    def _start_unavailable_calls(self, message: Any, unavailable_ids: set[str]) -> None:
        """Start a root AI message's calls answered as unavailable, each as a stopped step."""
        calls = [c for c in getattr(message, "tool_calls", None) or [] if c.get("id") in unavailable_ids]
        if not isinstance(message, AIMessage) or not calls:
            return
        self._start_tool_steps(calls, author=None, location_key="", with_author=False)
        for call in calls:
            self._send_unavailable(call["id"], "")

    def _start_interrupted_steps(self, interrupts: Any) -> None:
        for interrupt in interrupts or ():
            self._start_interrupted_step(interrupt)

    def _start_interrupted_step(self, interrupt: Any) -> None:
        payload = getattr(interrupt, "value", None)
        if not isinstance(payload, dict) or payload.get("kind") != GATE_INTERRUPT_KIND:
            return
        gate_ns = payload.get("checkpoint_ns") or ""
        agent_ns = gate_ns.rpartition(_NS_SEP)[0]
        deferred = self._pop_latest(agent_ns)
        interrupt_id = getattr(interrupt, "id", None)
        if deferred is None and _is_root_gate(payload) and interrupt_id not in self._started_interrupts:
            # A smart-selection root gates inside the wrapper's ``agent`` node (``agent:<t>|post_model_hook:<t>``),
            # whose update never streams before the pause: start the root's steps from the gate's own calls.
            deferred = self._pop_latest("")
            if deferred is None:
                self._started_interrupts.add(interrupt_id)
                self._start_tool_steps(_gate_tool_calls(payload), author=None, location_key="", with_author=False)
                return
        if deferred is None:
            return
        wire_ids = [call.get("tool_call_id") for call in payload.get("calls") or []]
        tool_calls = deferred.message.tool_calls
        if len(wire_ids) == len(tool_calls):
            tool_calls = [{**call, "id": wire_id} for call, wire_id in zip(tool_calls, wire_ids, strict=True)]
        self._started_interrupts.add(interrupt_id)
        self._start_deferred(deferred, tool_calls)

    def _pop_latest(self, namespace_key: str) -> _DeferredSteps | None:
        """The latest deferred message of a namespace (the one a gate interrupts); earlier ones start first."""
        deferred = self._deferred_steps.pop(namespace_key, [])
        for earlier in deferred[:-1]:
            self._start_deferred(earlier, earlier.message.tool_calls)
        return deferred[-1] if deferred else None

    def _start_deferred(self, deferred: _DeferredSteps, tool_calls: list[dict[str, Any]]) -> None:
        if deferred.supervisor:
            self._start_supervisor_steps(tool_calls, author=deferred.author, location_key=deferred.location_key)
        else:
            self._start_tool_steps(tool_calls, author=None, location_key="", with_author=False)


def _routed_calls(hook_update: Any) -> list[RoutedCall]:
    """The gate's routed calls in a hook update; none for a gate that kept every raw id (or no gate)."""
    if not isinstance(hook_update, dict):
        return []
    return list(hook_update.get("codemie_call_identities") or [])


def _calls_with_wire_ids(raw_calls: list[dict[str, Any]], routed: list[RoutedCall]) -> list[dict[str, Any]]:
    """Copies of the message's calls carrying the gate's wire ids; the message itself keeps its raw ids."""
    if not routed or len(routed) != len(raw_calls):
        return raw_calls
    return [{**raw_call, "id": call.identity.wire_id} for raw_call, call in zip(raw_calls, routed, strict=True)]


def _last_human_index(messages: list[Any]) -> int:
    """The position of the turn's input message, or ``-1`` when there is none."""
    return max((index for index, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)


def _is_root_gate(payload: dict[str, Any]) -> bool:
    """The gate of the root agent: none of its calls has a place below the root."""
    calls = payload.get("calls") or []
    return bool(calls) and all(not (call.get("event") or {}).get("location") for call in calls)


def _gate_tool_calls(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The tool calls of a gate's payload, with the ids the gate assigned."""
    return [
        {
            "name": (call.get("event") or {}).get("tool_name", ""),
            "args": (call.get("event") or {}).get("tool_args") or {},
            "id": call.get("tool_call_id"),
            "type": "tool_call",
        }
        for call in payload.get("calls") or []
    ]
