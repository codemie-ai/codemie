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

"""Shared helpers of the tool-confirmation tests: counted tools, a scripted model, the in-memory checkpoint service
(every stored value goes through JSON like the JSONB column) and a builder of one agent of a tree.

The matching fixtures (``service`` and the counter reset) live in ``conftest.py``.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from typing import Optional
from unittest.mock import Mock, patch

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import BaseTool

from codemie.agents.langgraph_agent import LangGraphAgent
from codemie.agents.tool_confirmation.models import StoredPendingToolCall
from codemie.core.models import ToolCallPolicy
from codemie.core.thread import ThreadedGenerator

CALLS: Counter = Counter()
SEEN: dict[str, list] = {}
BOUND: dict[str, list[list[str]]] = {}
CONV_ID = "conv-confirm"
MIXIN_MODULE = "codemie.agents.tool_confirmation.tool_call_confirmation_mixin"
SERVICE_CLASS = "codemie.service.conversation_checkpoint_service.ConversationCheckpointService"
_AUTO = ToolCallPolicy.AUTO_APPROVE


def reset_counters() -> None:
    CALLS.clear()
    SEEN.clear()
    BOUND.clear()


class CountedTool(BaseTool):
    safe: bool = False

    def is_safe(self, args: dict) -> bool:
        return self.safe

    def _run(self, q: str = "") -> str:
        CALLS[f"tool:{self.name}"] += 1
        return f"{self.name} EXECUTED"


def make_tools() -> list[BaseTool]:
    return [
        CountedTool(name="read_tool", description="read", safe=True),
        CountedTool(name="write_tool", description="write"),
        CountedTool(name="write2_tool", description="write 2"),
        CountedTool(name="unselected_tool", description="never selected"),
    ]


def _args(tool_name: str) -> dict:
    return {"task": f"do {tool_name}"} if tool_name.startswith("transfer_to_") else {"q": "1"}


class ScriptedModel(BaseChatModel):
    """Asks for ``calls`` once (``rounds`` times); once its calls are answered it reports the results.

    Records the messages it saw (``SEEN``) and the tools bound to it (``BOUND``).
    """

    role: str
    calls: list[tuple[str, str]]
    rounds: int = 1

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        BOUND.setdefault(self.role, []).append(sorted(getattr(tool, "name", str(tool)) for tool in tools))
        return self

    def with_structured_output(self, schema, **kwargs):
        """The structured answer to a JSON-schema ``output_schema``: ``answer`` holds the tool results it saw."""

        def answer(messages) -> dict:
            results = [str(m.content) for m in messages if isinstance(m, ToolMessage)]
            return {"answer": f"{self.role} structured: " + " | ".join(results)}

        return RunnableLambda(answer)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        CALLS[f"llm:{self.role}"] += 1
        SEEN[self.role] = list(messages)
        own_names = {name for name, _ in self.calls}
        asked = sum(
            1 for m in messages if isinstance(m, AIMessage) and any(c["name"] in own_names for c in m.tool_calls)
        )
        if asked >= self.rounds or not self.calls:
            answered = [str(m.content) for m in messages if isinstance(m, ToolMessage) and m.name in own_names]
            message = AIMessage(content=f"{self.role} done: " + " | ".join(answered))
        else:
            message = AIMessage(
                content="",
                tool_calls=[{"name": n, "args": _args(n), "id": i, "type": "tool_call"} for n, i in self.calls],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


def _json(value):
    return json.loads(json.dumps(value))


class FakeCheckpointService:
    """In-memory ``ConversationCheckpointService`` and ``ConversationPauseRepository`` in one: the saver's checkpoint
    rows and the pending record. ``patched_storage`` puts it behind both names the agent uses."""

    def __init__(self):
        self.checkpoints: dict[str, dict] = {}
        self.records: dict[str, dict] = {}
        self.checkpoint_deletes = 0

    def get_checkpoint(self, conversation_id, ns) -> Optional[dict]:
        entry = ((self.checkpoints.get(conversation_id) or {}).get("namespaces") or {}).get(ns)
        return deepcopy(entry) if entry is not None else None

    def put_checkpoint(self, conversation_id, ns, entry) -> None:
        spaces = self.checkpoints.setdefault(conversation_id, {}).setdefault("namespaces", {})
        old_writes = (spaces.get(ns) or {}).get("writes", {})
        kept = {k: v for k, v in old_writes.items() if v["checkpoint_id"] >= entry["checkpoint_id"]}
        spaces[ns] = {**_json(entry), "writes": kept}

    def put_writes(self, conversation_id, ns, checkpoint_id, regular, special) -> None:
        entry = self.checkpoints.setdefault(conversation_id, {}).setdefault("namespaces", {}).setdefault(ns, {})
        stored_id = entry.get("checkpoint_id")
        if stored_id is not None and checkpoint_id < stored_id:
            return
        writes = entry.setdefault("writes", {})
        for key, item in _json(regular).items():
            writes.setdefault(key, item)
        writes.update(_json(special))

    def write_pause(self, conversation_id, record: StoredPendingToolCall) -> None:
        self.records[conversation_id] = _json(record.model_dump(mode="json"))

    def delete_checkpoints(self, conversation_id) -> int:
        self.checkpoint_deletes += 1
        return 1 if self.checkpoints.pop(conversation_id, None) is not None else 0

    def delete_checkpoints_if_no_pending(self, conversation_id) -> int:
        if conversation_id in self.records:
            return 0
        return 1 if self.checkpoints.pop(conversation_id, None) is not None else 0

    def record(self, conversation_id=CONV_ID) -> StoredPendingToolCall:
        return StoredPendingToolCall.model_validate(self.records[conversation_id])


@contextmanager
def patched_storage(fake: FakeCheckpointService) -> Iterator[FakeCheckpointService]:
    """The agent's pause service and the saver's repository are ``fake``."""
    with (
        patch(f"{MIXIN_MODULE}.ConversationCheckpointService", return_value=fake),
        patch(f"{MIXIN_MODULE}.ConversationPauseRepository", return_value=fake),
    ):
        yield fake


def make_request() -> Mock:
    request = Mock()
    request.conversation_id = CONV_ID
    request.text = "hi"
    request.history_index = 4
    request.save_history = True
    request.history = []
    request.system_prompt = None
    request.file_names = None
    request.metadata = {}
    request.tools_config = None
    # What a pause stores from the request.
    request.version = None
    request.sub_assistants_versions = None
    request.skill_ids = None
    request.llm_model = None
    request.tool_call_policy = None
    return request


def make_user() -> Mock:
    user = Mock()
    user.id = "user-1"
    user.name = "Test User"
    user.username = "tester"
    return user


def make_assistant(assistant_id: str = "asst-root") -> Mock:
    assistant = Mock()
    assistant.id = assistant_id
    assistant.project = "test"
    assistant.version = None
    assistant.tools_tokens_size_limit = None
    assistant.is_global = False
    return assistant


def build_agent(
    name: str,
    calls: list[tuple[str, str]],
    generator,
    *,
    policy: ToolCallPolicy = _AUTO,
    require: Optional[bool] = None,
    smart: bool = False,
    subagent: bool = False,
    subs: Optional[list[LangGraphAgent]] = None,
    rounds: int = 1,
    tree_requires_confirmation: bool = True,
    can_resume_pause: bool = True,
    output_schema: Optional[dict] = None,
) -> LangGraphAgent:
    """One agent of a tree (confirming unless told otherwise): the root unless ``subagent``, else a sub-assistant."""
    model = ScriptedModel(role=name, calls=calls, rounds=rounds)
    subs = subs or []
    with patch.object(LangGraphAgent, "_initialize_llm", return_value=model):
        return LangGraphAgent(
            agent_name=name,
            description=f"assistant {name}",
            tools=make_tools(),
            request=make_request(),
            system_prompt=f"You are {name}.",
            request_uuid="req-1",
            user=make_user(),
            llm_model="gpt-4.1",
            assistant=make_assistant(f"asst-{name}"),
            thread_generator=generator,
            subagents=[sub.agent_executor for sub in subs] or None,
            subagent_descriptions={sub.agent_name: sub.description for sub in subs} or None,
            require_tool_confirmation=(policy != _AUTO) if require is None else require,
            tool_call_policy=policy,
            smart_tool_selection_enabled=smart,
            is_subagent=subagent,
            tree_requires_confirmation=tree_requires_confirmation,
            can_resume_pause=can_resume_pause,
            output_schema=output_schema,
        )


def sent(generator: ThreadedGenerator) -> list[dict]:
    items = []
    while not generator.queue.empty():
        item = generator.queue.get_nowait()
        if isinstance(item, str):
            items.append(json.loads(item))
    return items


def flat(thoughts: list[dict]) -> list[dict]:
    result = []
    for thought in thoughts:
        result.append(thought)
        result.extend(flat(thought.get("children") or []))
    return result
