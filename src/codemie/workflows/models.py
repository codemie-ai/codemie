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

from typing import TypedDict, Sequence, Optional, Annotated, Any

from langchain_core.messages import BaseMessage
from langgraph.graph import add_messages
from pydantic import Field, BaseModel

from codemie.workflows.constants import CONTEXT_STORE_KEEP_NEW_ONLY_FLAG


# Sentinel value to mark keys for deletion in context store
CONTEXT_STORE_DELETE_MARKER = "__DELETE_KEY__"

# Sentinel wrapper key for accumulating values as a list in context store.
# When a value in `right` is a dict with this key, the reducer appends the
# wrapped list to the existing value instead of overwriting it.
CONTEXT_STORE_APPEND_MARKER = "__APPEND_VALUE__"


def _coerce_to_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    return [value] if value else []


def add_or_replace_context_store(left: dict[str, Any], right: dict[str, Any] | None) -> dict[str, Any]:
    """Merge context store: replace if None provided, otherwise merge dictionaries.

    This reducer is used by LangGraph to merge context_store state updates.
    - If right is None: clears the context store (returns empty dict)
    - Otherwise: merges left and right dicts (right values override left for same keys)
    - Keys with value CONTEXT_STORE_DELETE_MARKER in right are removed from the result
    - Keys whose value in right is {CONTEXT_STORE_APPEND_MARKER: [...]} are accumulated
      as a list rather than overwritten

    Args:
        left: Existing context store dictionary
        right: New context store entries, or None to clear

    Returns:
        Updated context store (cleared if right is None, otherwise merged with deletions applied)
    """
    if right is None:
        return {}

    if right.get(CONTEXT_STORE_KEEP_NEW_ONLY_FLAG, False):
        right.pop(CONTEXT_STORE_KEEP_NEW_ONLY_FLAG)
        merged = right
    else:
        merged = {**left}
        for k, v in right.items():
            if isinstance(v, dict) and CONTEXT_STORE_APPEND_MARKER in v:
                merged[k] = _coerce_to_list(merged.get(k, [])) + v[CONTEXT_STORE_APPEND_MARKER]
            else:
                merged[k] = v

    # Filter out deletion markers
    return {k: v for k, v in merged.items() if v != CONTEXT_STORE_DELETE_MARKER}


class AgentMessages(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    context_store: Annotated[dict[str, Any], add_or_replace_context_store]
    next: Annotated[list, list.__add__]
    final_summary: Annotated[list, list.__add__]
    user_input: str
    previous_execution_state_id: Annotated[Optional[str], lambda left, right: right if right is not None else left]


class SupervisorAgentMessages(AgentMessages):
    task: Optional[str]
    reasoning: Optional[str]


class NextAction(BaseModel):
    next: str = Field(
        description="""Next action to take.
    Must be one of the agent/assistant (member of team) or FINISH action in case everything is done.
    """
    )
    task: str = Field(
        description="""
    Detailed task description, instructions, DoD for agent/assistant to execute.
    This field is required for the agent to execute.
    """,
        default_factory=str,
    )
    reasoning: str = Field(
        description="""The reasoning why it is the best action to take
    and why it should continue or finish. Why it should continue and not finished"""
    )
