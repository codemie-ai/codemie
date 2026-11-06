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

import json
import uuid
from collections import Counter
from copy import deepcopy
from typing import Annotated, Optional

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

from codemie.agents.tool_confirmation.conversation_checkpoint_saver import ConversationCheckpointSaver


class FakeCheckpointRepository:
    """In-memory stand-in for ConversationPauseRepository's checkpoint statements, with their atomic contract.

    Each method is one atomic update of one namespace row (the real repository runs one SQL statement).
    """

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.calls: list[str] = []

    def get_checkpoint(self, conversation_id, ns) -> Optional[dict]:
        self.calls.append("get")
        entry = ((self.rows.get(conversation_id) or {}).get("namespaces") or {}).get(ns)
        return deepcopy(entry) if entry is not None else None

    def put_checkpoint(self, conversation_id, ns, entry) -> None:
        self.calls.append("put")
        json.dumps(entry)  # must be JSONB-serialisable
        row = self.rows.setdefault(conversation_id, {})
        spaces = row.setdefault("namespaces", {})
        old_writes = (spaces.get(ns) or {}).get("writes", {})
        spaces[ns] = {
            **entry,
            "writes": {k: v for k, v in old_writes.items() if v["checkpoint_id"] >= entry["checkpoint_id"]},
        }

    def put_writes(self, conversation_id, ns, checkpoint_id, regular, special) -> None:
        self.calls.append("put_writes")
        json.dumps([regular, special])
        row = self.rows.setdefault(conversation_id, {})
        entry = row.setdefault("namespaces", {}).setdefault(ns, {})
        stored_id = entry.get("checkpoint_id")
        if stored_id is not None and checkpoint_id < stored_id:
            return
        writes = entry.setdefault("writes", {})
        for key, item in regular.items():
            writes.setdefault(key, item)
        writes.update(special)


def _cp(cp_id: str, extra: Optional[dict] = None) -> dict:
    return {
        "v": 1,
        "id": cp_id,
        "ts": "2026-01-01",
        "channel_values": extra or {},
        "channel_versions": {},
        "versions_seen": {},
        "pending_sends": [],
    }


META = {"source": "loop", "step": 1, "writes": {}, "parents": {}}


def _cfg(thread="conv_abc", ns="", checkpoint_id=None) -> dict:
    cfg = {"thread_id": thread, "checkpoint_ns": ns}
    if checkpoint_id:
        cfg["checkpoint_id"] = checkpoint_id
    return {"configurable": cfg}


@pytest.fixture
def service():
    return FakeCheckpointRepository()


@pytest.fixture
def saver(service):
    return ConversationCheckpointSaver(service)


def test_get_tuple_returns_none_when_no_checkpoint(saver):
    assert saver.get_tuple(_cfg()) is None


def test_put_keeps_every_namespace(saver, service):
    saver.put(_cfg(ns=""), _cp("001"), META, {})
    saver.put(_cfg(ns=f"agent_a:{uuid.uuid4()}"), _cp("002"), META, {})

    assert set(service.rows["conv_abc"]["namespaces"]) == {
        "",
        next(k for k in service.rows["conv_abc"]["namespaces"] if k),
    }
    root = saver.get_tuple(_cfg(ns=""))
    assert root.checkpoint["id"] == "001"


def test_put_returns_config_with_namespace_and_checkpoint_id(saver):
    out = saver.put(_cfg(ns="agent_a:1"), _cp("007"), META, {})
    assert out == {"configurable": {"thread_id": "conv_abc", "checkpoint_ns": "agent_a:1", "checkpoint_id": "007"}}


def test_get_tuple_returns_namespace_and_checkpoint_id(saver):
    saver.put(_cfg(ns="agent_a:1"), _cp("007"), META, {})
    tup = saver.get_tuple(_cfg(ns="agent_a:1"))
    assert tup.config == {
        "configurable": {"thread_id": "conv_abc", "checkpoint_ns": "agent_a:1", "checkpoint_id": "007"}
    }
    assert tup.metadata["step"] == 1
    assert tup.parent_config is None


def test_get_tuple_other_checkpoint_id_is_not_found(saver):
    saver.put(_cfg(), _cp("007"), META, {})
    assert saver.get_tuple(_cfg(checkpoint_id="006")) is None
    assert saver.get_tuple(_cfg(checkpoint_id="007")) is not None


def test_stored_shape_is_namespaces_with_latest_entry(saver, service):
    saver.put(_cfg(), _cp("001"), META, {})
    saver.put(_cfg(), _cp("002"), META, {})
    stored = service.rows["conv_abc"]
    assert list(stored) == ["namespaces"]
    entry = stored["namespaces"][""]
    assert entry["checkpoint_id"] == "002"
    assert {"checkpoint", "metadata", "writes"} <= set(entry)


def test_put_writes_round_trip_as_pending_writes(saver):
    saver.put(_cfg(), _cp("001"), META, {})
    saver.put_writes(_cfg(checkpoint_id="001"), [("messages", "hi"), ("other", 5)], "task1")
    tup = saver.get_tuple(_cfg())
    assert sorted(tup.pending_writes) == [("task1", "messages", "hi"), ("task1", "other", 5)]


def test_writes_are_keyed_by_checkpoint_task_and_idx(saver, service):
    saver.put(_cfg(), _cp("001"), META, {})
    saver.put_writes(_cfg(checkpoint_id="001"), [("a", 1), ("b", 2)], "t1")
    keys = set(service.rows["conv_abc"]["namespaces"][""]["writes"])
    assert keys == {"001|t1|0", "001|t1|1"}


def test_list_yields_nothing(saver):
    saver.put(_cfg(), _cp("001"), META, {})
    assert list(saver.list(_cfg())) == []


# ---- graph-level behaviour on this saver (spike rows 3a / Q1) -------------------------------------------

CALLS: Counter = Counter()


class ScriptedModel(BaseChatModel):
    """Decides its reply from the message list only, so re-runs are visible in CALLS."""

    role: str

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        CALLS[f"llm:{self.role}"] += 1
        if any(isinstance(m, ToolMessage) for m in messages):
            msg = AIMessage(content=f"{self.role} done")
        else:
            msg = AIMessage(
                content="",
                tool_calls=[
                    {"name": f"{self.role}_tool", "args": {"q": "1"}, "id": f"{self.role}_call", "type": "tool_call"}
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=msg)])


@tool("agent_a_tool")
def guarded_tool(q: str) -> str:
    """Needs confirmation through a dynamic interrupt."""
    if interrupt({"tool": "agent_a_tool"}) != "allow":
        return "DENIED"
    CALLS["tool:agent_a_tool"] += 1
    return "A EXECUTED"


@tool("agent_b_tool")
def free_tool(q: str) -> str:
    """Runs freely."""
    CALLS["tool:agent_b_tool"] += 1
    return "B EXECUTED"


class _State(TypedDict):
    messages: Annotated[list, add_messages]


def _build_graph():
    a = create_react_agent(ScriptedModel(role="agent_a"), [guarded_tool], name="agent_a")
    b = create_react_agent(ScriptedModel(role="agent_b"), [free_tool], name="agent_b")
    builder = StateGraph(_State)
    builder.add_node("agent_a", a)
    builder.add_node("agent_b", b)
    builder.add_edge(START, "agent_a")
    builder.add_edge(START, "agent_b")
    return builder.compile()


def _run_cfg(saver, thread):
    return {"__pregel_checkpointer": saver, "thread_id": thread}


def _state_cfg(saver, thread):
    return {"__pregel_checkpointer": saver, "configurable": {"thread_id": thread}}


@pytest.fixture
def graph_saver(service):
    CALLS.clear()
    return ConversationCheckpointSaver(service)


def test_outer_get_state_finds_nested_interrupt_after_reload(graph_saver, service):
    thread = "conv_nested"
    _build_graph().invoke({"messages": [HumanMessage("go")]}, _run_cfg(graph_saver, thread))

    # reload: a brand-new saver and graph over the stored row only
    reloaded = ConversationCheckpointSaver(service)
    state = _build_graph().get_state(_state_cfg(reloaded, thread))
    interrupts = [i for t in state.tasks for i in t.interrupts]
    assert len(interrupts) == 1
    assert interrupts[0].value == {"tool": "agent_a_tool"}
    namespaces = service.rows[thread]["namespaces"]
    assert "" in namespaces and any(ns.startswith("agent_a:") for ns in namespaces)


def test_finished_parallel_sibling_is_not_rerun_on_resume(graph_saver, service):
    thread = "conv_parallel"
    _build_graph().invoke({"messages": [HumanMessage("go")]}, _run_cfg(graph_saver, thread))
    assert CALLS["tool:agent_b_tool"] == 1 and CALLS["tool:agent_a_tool"] == 0
    llm_b = CALLS["llm:agent_b"]

    reloaded = ConversationCheckpointSaver(service)
    graph = _build_graph()
    state = graph.get_state(_state_cfg(reloaded, thread))
    interrupt_id = next(i.id for t in state.tasks for i in t.interrupts)
    graph.invoke(Command(resume={interrupt_id: "allow"}), _run_cfg(reloaded, thread))

    assert CALLS["tool:agent_a_tool"] == 1
    assert CALLS["tool:agent_b_tool"] == 1
    assert CALLS["llm:agent_b"] == llm_b
