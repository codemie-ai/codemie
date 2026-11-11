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

"""The message log the truncation status report reads (EPMCDME-16835).

An agent records the messages of its run. A confirmation resume runs on a new agent, so its log is seeded from the
checkpoint the resume continues. When the run stops at the output limit, the log is merged with the messages the
stopping check attached.
"""

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from codemie.agents.truncation_report import split_turn


def subgraph_task_messages(state: Any) -> list[BaseMessage]:
    """AI and tool messages of the sub-graph states of a snapshot's tasks, depth first.

    Paused tasks and tasks that finished in the same step both count: a parallel sibling that finished before the
    pause has its calls only in its own sub-graph state, since its parent keeps the last message alone. A finished
    task without a readable sub-graph state contributes the messages of its result.
    """
    found: list[BaseMessage] = []
    for task in getattr(state, "tasks", ()) or ():
        sub_state = getattr(task, "state", None)
        values = getattr(sub_state, "values", None)
        if not isinstance(values, dict):
            result = getattr(task, "result", None)
            values = result if isinstance(result, dict) else {}
        messages = values.get("messages")
        if isinstance(messages, list):
            found.extend(m for m in messages if isinstance(m, (AIMessage, ToolMessage)))
        found.extend(subgraph_task_messages(sub_state))
    return found


def checkpoint_messages(state: Any) -> list[BaseMessage] | None:
    """The messages a resume continues from: the top-level graph's, then its sub-graphs' AI and tool messages.

    A sub-agent's HumanMessage (its task) is left out, so the user's turn still starts at the request. None when the
    top-level messages cannot be read.
    """
    values = getattr(state, "values", None)
    messages = values.get("messages") if isinstance(values, dict) else None
    if not isinstance(messages, list):
        return None
    seeded = list(messages)
    seen_ids = {m.id for m in seeded if isinstance(m, BaseMessage) and m.id}
    for message in subgraph_task_messages(state):
        if message.id and message.id in seen_ids:
            continue
        seeded.append(message)
        if message.id:
            seen_ids.add(message.id)
    return seeded


def merge_turn_messages(
    run_log: Sequence[BaseMessage] | None, attached: Sequence[BaseMessage] | None
) -> list[BaseMessage] | None:
    """The run log with the messages the stopping check attached merged in (re-review AR-2).

    A check inside a sub-assistant attaches that sub-assistant's own view, where earlier delegations appear only as
    their last reply; the log of the agent delivering the error holds their calls. The attached messages after their
    last HumanMessage add what the log has not seen (a loop run inside one node); earlier attached messages are
    history, and a HumanMessage (a sub-assistant's task) would move the start of the user's turn, so neither is
    merged. Without a log, the attached messages are used as they are.
    """
    if not run_log or attached is None or attached is run_log:
        chosen = attached if attached is not None else run_log
        return list(chosen) if chosen is not None else None
    merged = list(run_log)
    seen_objects = {id(m) for m in merged}
    seen_ids = {m.id for m in merged if m.id}
    for message in split_turn(attached)[1]:
        if not isinstance(message, BaseMessage):
            continue
        if id(message) in seen_objects or (message.id and message.id in seen_ids):
            continue
        merged.append(message)
        seen_objects.add(id(message))
        if message.id:
            seen_ids.add(message.id)
    return merged
