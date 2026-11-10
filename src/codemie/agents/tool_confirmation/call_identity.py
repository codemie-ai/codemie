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

"""The single call-identity derivation for tool-call confirmation.

A call's identity is its wire id (the id the model sees on the AI message's call and its
``ToolMessage``) plus the agent's location key, kept beside the id, never inside it.
Every consumer (gate, decision items, carrier, stream steps) uses this module; none derives its own.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage, ToolMessage
from langgraph.prebuilt.chat_agent_executor import AgentState, AgentStateWithStructuredResponse
from typing_extensions import NotRequired

_UNSAFE_CHARS = re.compile(r"[^a-zA-Z0-9_-]")
_WIRE_ID = re.compile(r"^[a-zA-Z0-9_-]{1,40}$")
_RAW_PREFIX_LEN = 28
_NS_SEP = "|"
_NS_END = ":"
_POST_MODEL_HOOK = "post_model_hook"
_SUPERVISOR_NODE = "supervisor"
_TOOLS_NODE = "tools"


@dataclass(frozen=True)
class CallIdentity:
    wire_id: str
    location_key: str

    @property
    def carrier_key(self) -> str:
        """Key of this call in ``configurable["codemie_tool_decisions"]``."""
        return f"{self.location_key}/{self.wire_id}"

    @property
    def step_key(self) -> str:
        """What the call's streamed step id is derived from (``LangGraphAgent._tool_call_id_to_uuid``).

        At the root it is the wire id, so a root step keeps main's id (AC11); below it the location is
        included, so two siblings whose calls share an id get two steps.
        """
        return self.carrier_key if self.location_key else self.wire_id


@dataclass(frozen=True)
class RoutedCall:
    """One call of the gate's output: the raw id the AI message keeps, beside the call's identity.

    The raw id is kept beside the identity, never inside it, so the carrier and step keys stay those of the wire id.
    The stream reads a tool result's raw id back to its wire id from this pair.
    """

    raw_id: str
    identity: CallIdentity


class ConfirmationAgentState(AgentState):
    """``AgentState`` plus the gate's identities: LangGraph drops an update key the state schema does not declare.

    Without the key a ``post_model_hook`` update reaches the stream as messages only, so the steps could not start
    from the identities the gate assigned.
    """

    codemie_call_identities: NotRequired[list[RoutedCall]]


class ConfirmationAgentStateWithStructuredResponse(AgentStateWithStructuredResponse):
    """``ConfirmationAgentState`` for an agent with a structured response (``create_react_agent`` requires that key)."""

    codemie_call_identities: NotRequired[list[RoutedCall]]


def confirmation_state_schema(response_format: Any) -> type:
    """The state schema of a ReAct agent or supervisor that carries the gate's identities.

    The structured-response key is required by LangGraph whenever a response format is set, so the schema follows it.
    """
    return ConfirmationAgentStateWithStructuredResponse if response_format is not None else ConfirmationAgentState


def _short_hash(*parts: object) -> str:
    data = "\x00".join(str(part) for part in parts).encode()
    return hashlib.blake2b(data, digest_size=4).hexdigest()


def _rewrite(raw: str, position: int, attempt: int) -> str:
    prefix = _UNSAFE_CHARS.sub("_", raw[:_RAW_PREFIX_LEN])
    return f"{prefix}_{_short_hash(raw, position, attempt)}"


def _answered_ids(messages: Sequence[BaseMessage]) -> set[str]:
    return {message.tool_call_id for message in messages if isinstance(message, ToolMessage)}


def assign_call_identities(messages: Sequence[BaseMessage], ai_message: Any, location_key: str) -> list[CallIdentity]:
    """Return one identity per tool call of ``ai_message``, in order.

    An id is kept unless it is already answered by a ``ToolMessage`` in ``messages``, repeats an
    earlier call of the same message (judged by position, empty ids included) or is not a safe
    provider id (empty, unsafe characters or over 40 chars). A kept id is returned
    unchanged; a rewritten one is ``<raw[:28] made safe>_<8 hex>``, deterministic in (raw, position)
    and always matching ``^[a-zA-Z0-9_-]{1,40}$``.
    """
    raw_ids = [call.get("id") or "" for call in (getattr(ai_message, "tool_calls", None) or [])]
    answered = _answered_ids(messages)
    taken = answered | {raw for raw in raw_ids if raw}
    seen: set[str] = set()
    identities: list[CallIdentity] = []
    for position, raw in enumerate(raw_ids):
        wire_id = raw
        if raw in answered or raw in seen or not _WIRE_ID.match(raw):
            attempt = 0
            wire_id = _rewrite(raw, position, attempt)
            while wire_id in taken or wire_id in seen:
                attempt += 1
                wire_id = _rewrite(raw, position, attempt)
        seen.add(raw)
        seen.add(wire_id)
        identities.append(CallIdentity(wire_id=wire_id, location_key=location_key))
    return identities


def location_key_from_namespace(checkpoint_ns: str) -> str:
    """Reduce a gate task's checkpoint namespace to a stable per-instance location key.

    The ``post_model_hook`` segment is dropped and each task id is replaced by a short stable hash, so
    two parallel instances of one agent differ while a resume (same deterministic task ids) reproduces
    the key. A namespace with nothing left (the root agent) gives ``""``.
    """
    segments = []
    for segment in checkpoint_ns.split(_NS_SEP) if checkpoint_ns else []:
        node, _, task_id = segment.partition(_NS_END)
        if node == _POST_MODEL_HOOK:
            continue
        segments.append(f"{node}{_NS_END}{_short_hash(task_id)}" if task_id else node)
    return _NS_SEP.join(segments)


def agent_author_keys(checkpoint_ns: str) -> list[str]:
    """The stream author key of every agent on a gate's namespace, root's child first.

    Each segment ``"<node>:<task>"`` of the gate's namespace (its ``post_model_hook`` segment dropped) is the
    last namespace segment of one agent's chunks, which is how the stream names that agent.

    A nested orchestrator's internal ``supervisor`` segment is dropped too: the stream names that agent by the
    orchestrator's own node (``_SupervisorChunkContext.from_chunk``), so it is one assistant level, not two.
    """
    segments = checkpoint_ns.split(_NS_SEP) if checkpoint_ns else []
    return [
        segment
        for index, segment in enumerate(segments)
        if segment.partition(_NS_END)[0] != _POST_MODEL_HOOK
        and not (index > 0 and segment.partition(_NS_END)[0] == _SUPERVISOR_NODE)
    ]


def agent_location_key(namespace: Sequence[str]) -> str:
    """The location key of the agent whose chunks stream at ``namespace``, as its gate derives it.

    ``""`` for the root agent, including the root supervisor's own agent at ``("supervisor:<task>",)``.
    """
    if not namespace or (len(namespace) == 1 and namespace[0].partition(_NS_END)[0] == _SUPERVISOR_NODE):
        return ""
    return location_key_from_namespace(_NS_SEP.join(namespace))


def delegating_location_key(tool_checkpoint_ns: str) -> str:
    """The location key of the agent that runs a tool (a handoff) at ``tool_checkpoint_ns``.

    The tool task's own ``tools:<task>`` segment is dropped; what is left is the calling agent's namespace.
    """
    segments = tool_checkpoint_ns.split(_NS_SEP) if tool_checkpoint_ns else []
    if segments and segments[-1].partition(_NS_END)[0] == _TOOLS_NODE:
        segments = segments[:-1]
    return agent_location_key(segments)
