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

"""The ``post_model_hook`` confirmation gate of one LangGraph agent.

The hook runs after the model and before routing, so before any tool task of the AI message is scheduled.
It re-runs from the top on resume, so it is deterministic and has no side effects before ``interrupt()``:

1. assign call identities and rewrite the AI message in place (same message id);
2. read each call's carried decision, used only when its ``ns`` is this gate's own ``checkpoint_ns``;
3. classify with the policy and ``is_safe`` only the calls without a carried decision;
4. ``interrupt()`` once with every call of the message if any call is undecided;
5. on resume, deny every call that is still undecided (fail closed) by returning denial ``ToolMessage``s.

In a run that cannot be resumed (spec rule 9) step 4 never happens: every undecided call is answered with the
``UNAVAILABLE_MESSAGE`` ``ToolMessage`` instead, so nothing pauses and the auto-allowed calls run.

The prebuilt ``post_model_hook_router`` then sends only the calls without a ``ToolMessage``, or routes back
to the model when every call was denied. Decisions travel in ``config["configurable"][DECISIONS_CONFIG_KEY]``,
never in the resume value, which is ``"ack"`` for every pending interrupt id.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Callable, Literal

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.types import interrupt
from pydantic import BaseModel

from codemie.agents.tool_confirmation.call_identity import (
    CallIdentity,
    assign_call_identities,
    location_key_from_namespace,
)
from codemie.agents.tool_confirmation.models import AppliedDecision, PendingCallLocation, ToolCallPendingEvent
from codemie.core.models import ToolCallPolicy
from codemie.core.tool_step_states import DENIED_MESSAGE, UNAVAILABLE_MESSAGE

DECISIONS_CONFIG_KEY = "codemie_tool_decisions"
GATE_INTERRUPT_KIND = "codemie_tool_confirmation"

Decision = Literal["allow", "deny"]
_DECISIONS: frozenset[str] = frozenset({"allow", "deny"})
# A call's outcome in one gate pass: a decision, ``"unavailable"`` in a run that cannot pause, or undecided.
_Outcome = Literal["allow", "deny", "unavailable"] | None


class GateCall(BaseModel):
    """One call of the paused AI message. ``decision`` is ``"allow"`` for a call the policy auto-approves."""

    tool_call_id: str
    location_key: str
    checkpoint_ns: str
    event: ToolCallPendingEvent
    decision: Decision | None = None

    @property
    def carrier_key(self) -> str:
        return CallIdentity(wire_id=self.tool_call_id, location_key=self.location_key).carrier_key


class GateInterrupt(BaseModel):
    """The value of the gate's ``interrupt()``: one AI message of one agent."""

    kind: Literal["codemie_tool_confirmation"] = GATE_INTERRUPT_KIND
    checkpoint_ns: str
    location_key: str
    calls: list[GateCall]


def build_decision_carrier(applied: Iterable[AppliedDecision]) -> dict[str, dict[str, str]]:
    """Build the ``DECISIONS_CONFIG_KEY`` value: every call of the pause with its applied action.

    A call missing from the carrier is denied by the gate, so only an allow or a deny entry is ever written here.
    """
    carrier: dict[str, dict[str, str]] = {}
    for decision in applied:
        item = decision.item
        key = CallIdentity(wire_id=item.tool_call_id, location_key=item.location_key).carrier_key
        carrier[key] = {"action": decision.action, "ns": item.checkpoint_ns}
    return carrier


def tool_call_is_safe(tools_by_name: Mapping[str, BaseTool], tool_call: Mapping[str, Any]) -> bool:
    """A call is safe only if its tool is known and says so; unknown tools are treated as mutating."""
    is_safe = getattr(tools_by_name.get(tool_call.get("name", "")), "is_safe", None)
    return bool(is_safe(tool_call.get("args") or {})) if callable(is_safe) else False


def call_needs_confirmation(
    policy: ToolCallPolicy, tools_by_name: Mapping[str, BaseTool], tool_call: Mapping[str, Any]
) -> bool:
    match policy:
        case ToolCallPolicy.ASK_FOR_APPROVAL:
            return True
        case ToolCallPolicy.APPROVE_FOR_ME:
            return not tool_call_is_safe(tools_by_name, tool_call)
        case _:
            return False


def build_confirmation_hook(
    policy: ToolCallPolicy,
    tools: Sequence[BaseTool],
    is_subagent: bool,
    pauses_own_calls: bool,
    resumable: bool,
) -> Callable[[Any, RunnableConfig], dict[str, Any]]:
    """Return the ``post_model_hook`` of one agent.

    The root agent (``is_subagent`` false) has the location key ``""`` whatever its namespace holds
    (a root under an outer graph still has outer segments). A nested agent's key is derived from the gate's
    own ``checkpoint_ns``, so parallel instances differ and a resume reproduces it. With ``pauses_own_calls``
    false the hook only applies the call identity. With ``resumable`` false (the run cannot be resumed) the hook
    never pauses: it answers every call needing confirmation as unavailable.
    """
    tools_by_name = {tool.name: tool for tool in tools}

    def confirmation_hook(state: Any, config: RunnableConfig) -> dict[str, Any]:
        return _gate_update(state, config, policy, tools_by_name, is_subagent, pauses_own_calls, resumable)

    return confirmation_hook


def _gate_update(
    state: Any,
    config: RunnableConfig,
    policy: ToolCallPolicy,
    tools_by_name: Mapping[str, BaseTool],
    is_subagent: bool,
    pauses_own_calls: bool,
    resumable: bool,
) -> dict[str, Any]:
    """One pass of the gate: apply the call identity, then pause on undecided calls and deny the refused ones."""
    messages = _state_messages(state)
    ai_message = messages[-1] if messages else None
    if not isinstance(ai_message, AIMessage) or not ai_message.tool_calls:
        return {}

    configurable = config.get("configurable") or {}
    checkpoint_ns = configurable.get("checkpoint_ns") or ""
    location_key = location_key_from_namespace(checkpoint_ns) if is_subagent else ""
    identities = assign_call_identities(messages, ai_message, location_key)
    identified = _with_wire_ids(ai_message, [identity.wire_id for identity in identities])
    update: list[BaseMessage] = [identified] if identified is not ai_message else []
    if not pauses_own_calls:
        return {"messages": update} if update else {}

    carried = _carried_decisions(configurable.get(DECISIONS_CONFIG_KEY), identities, checkpoint_ns)
    calls = identified.tool_calls
    decisions: list[_Outcome] = list(_call_decisions(policy, tools_by_name, calls, carried))
    if not resumable:
        decisions = ["unavailable" if decision is None else decision for decision in decisions]
    if any(decision is None for decision in decisions):
        gate_interrupt = _gate_interrupt(calls, decisions, messages, is_subagent, location_key, checkpoint_ns)
        interrupt(gate_interrupt.model_dump(mode="json"))

    update.extend(_denials(calls, decisions))
    return {"messages": update} if update else {}


def _denials(calls: list[dict[str, Any]], decisions: list[_Outcome]) -> list[ToolMessage]:
    """The denial result of every call not allowed; an unavailable call says why it did not run."""
    return [
        ToolMessage(
            content=UNAVAILABLE_MESSAGE if decision == "unavailable" else DENIED_MESSAGE,
            tool_call_id=call["id"],
            name=call["name"],
        )
        for call, decision in zip(calls, decisions, strict=True)
        if decision != "allow"
    ]


def _call_decisions(
    policy: ToolCallPolicy,
    tools_by_name: Mapping[str, BaseTool],
    calls: list[dict[str, Any]],
    carried: Mapping[str, Decision],
) -> list[Decision | None]:
    """Each call's carried decision, else ``"allow"`` when it needs no confirmation, else ``None`` (undecided)."""
    return [
        carried.get(call["id"]) or (None if call_needs_confirmation(policy, tools_by_name, call) else "allow")
        for call in calls
    ]


def _state_messages(state: Any) -> list[BaseMessage]:
    if isinstance(state, Mapping):
        return list(state.get("messages") or [])
    return list(getattr(state, "messages", None) or [])


def _carried_decisions(carrier: Any, identities: Sequence[CallIdentity], checkpoint_ns: str) -> dict[str, Decision]:
    """Carried decisions of this gate's calls; an entry of another namespace is ignored."""
    if not isinstance(carrier, Mapping):
        return {}
    decisions: dict[str, Decision] = {}
    for identity in identities:
        entry = carrier.get(identity.carrier_key)
        if isinstance(entry, Mapping) and entry.get("ns") == checkpoint_ns and entry.get("action") in _DECISIONS:
            decisions[identity.wire_id] = entry["action"]
    return decisions


def _with_wire_ids(ai_message: AIMessage, wire_ids: list[str]) -> AIMessage:
    """Return the AI message with its calls carrying ``wire_ids`` (same message id), or itself if unchanged."""
    raw_ids = [call.get("id") for call in ai_message.tool_calls]
    if raw_ids == wire_ids:
        return ai_message
    tool_calls = [{**call, "id": wire_id} for call, wire_id in zip(ai_message.tool_calls, wire_ids, strict=True)]
    content = _content_with_ids(ai_message, raw_ids, wire_ids)
    return ai_message.model_copy(update={"tool_calls": tool_calls, "content": content})


def _content_with_ids(ai_message: AIMessage, raw_ids: list[str | None], wire_ids: list[str]) -> Any:
    """Rewrite each provider ``tool_use`` content block to the wire id of the call it belongs to.

    A block takes the wire id of the first call with its raw id not yet taken, so blocks sharing one raw id get
    their calls' wire ids in order and every block keeps the id of its ``tool_calls`` entry.
    """
    content = ai_message.content
    if not isinstance(content, list):
        return content
    untaken: dict[str | None, deque[str]] = {}
    for raw_id, wire_id in zip(raw_ids, wire_ids, strict=True):
        untaken.setdefault(raw_id, deque()).append(wire_id)
    rewritten = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use" and untaken.get(block.get("id")):
            block = {**block, "id": untaken[block["id"]].popleft()}
        rewritten.append(block)
    return rewritten


def _gate_interrupt(
    calls: list[dict[str, Any]],
    decisions: list[Decision | None],
    messages: Sequence[BaseMessage],
    is_subagent: bool,
    location_key: str,
    checkpoint_ns: str,
) -> GateInterrupt:
    pending_location = _pending_location(messages, is_subagent)
    all_ids = [call["id"] for call in calls]
    return GateInterrupt(
        checkpoint_ns=checkpoint_ns,
        location_key=location_key,
        calls=[
            GateCall(
                tool_call_id=call["id"],
                location_key=location_key,
                checkpoint_ns=checkpoint_ns,
                decision=decision,
                event=ToolCallPendingEvent(
                    pending_tool_call_id=call["id"],
                    tool_name=call["name"],
                    tool_args=call.get("args") or {},
                    all_tool_call_ids=all_ids,
                    location=pending_location,
                ),
            )
            for call, decision in zip(calls, decisions, strict=True)
        ],
    )


def _pending_location(messages: Sequence[BaseMessage], is_subagent: bool) -> PendingCallLocation | None:
    """A sub-assistant's location (``None`` at the root); its container is the step of the latest handoff into it.

    The step's id is the handoff's call identity: its id and the delegating agent's location, which the
    handoff message carries (the stream derives the same step from the delegating agent's chunks).
    """
    if not is_subagent:
        return None
    from codemie.agents.langgraph_agent import LangGraphAgent
    from codemie.agents.supervisor.history import latest_handoff_identity

    handoff = latest_handoff_identity(messages)
    return PendingCallLocation(
        parent_thought_id=str(LangGraphAgent._tool_call_id_to_uuid(handoff.step_key)) if handoff else "",
    )
