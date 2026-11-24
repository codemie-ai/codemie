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

"""How a workflow state writes to the shared context, as pure functions.

The executor and the nodes apply these rules at run time; the save-time contract linker calls the same functions to
predict what a state will find, so a change here reaches both. Dependency free on purpose: the linker must not import
the nodes or the executor, which pull in services.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import Enum
from typing import Any, TypeVar

from codemie.workflows.constants import (
    CLEAR_CONTEXT_STORE_KEEP_CURRENT,
    CONTEXT_STORE_APPEND_MARKER,
    CONTEXT_STORE_DELETE_MARKER,
    CONTEXT_STORE_KEEP_NEW_ONLY_FLAG,
)

# `include_in_iterator_context` value that passes the whole store into an iteration branch.
ITERATOR_INCLUDE_ALL = ["*"]

_Value = TypeVar("_Value")


class StateNodeType(str, Enum):
    """Which node the executor builds for a state, from its type fields."""

    AGENT = "agent"
    CUSTOM = "custom"
    TOOL = "tool"
    SUB_WORKFLOW = "sub_workflow"


def state_node_type(state: object) -> StateNodeType | None:
    """The node type of a state, testing the type fields in the executor's order; `None` when none is set."""
    if getattr(state, "assistant_id", None):
        return StateNodeType.AGENT
    if getattr(state, "custom_node_id", None):
        return StateNodeType.CUSTOM
    if getattr(state, "tool_id", None):
        return StateNodeType.TOOL
    if getattr(state, "workflow_id", None):
        return StateNodeType.SUB_WORKFLOW
    return None


def build_context_update(next_state: Any, collect_values: Callable[[], Mapping[str, Any]]) -> dict[str, Any] | None:
    """The `context_store` update one state returns, before its output key is added.

    `collect_values` gathers the state's own values; it is called only when the update keeps them. `None` makes the
    reducer clear the store. A full reset with an output key returns only the keep-new-only flag, so the output key can
    be added to an otherwise empty store.
    """
    clear_context_store = next_state.clear_context_store
    if isinstance(clear_context_store, bool) and clear_context_store:
        return {CONTEXT_STORE_KEEP_NEW_ONLY_FLAG: True} if next_state.output_key else None

    if not next_state.store_in_context:
        return {}

    new_values = dict(collect_values())

    # Wrap values for append keys with sentinel so the reducer accumulates them
    if next_state.append_to_context:
        new_values = {key: {CONTEXT_STORE_APPEND_MARKER: [value]} for key, value in new_values.items()}

    # Insert sentinel AFTER wrapping so the reducer can recognize it as a plain flag
    if clear_context_store == CLEAR_CONTEXT_STORE_KEEP_CURRENT:
        new_values[CONTEXT_STORE_KEEP_NEW_ONLY_FLAG] = True

    # Deletion markers go last, so a reset key wins over the state's own write of it
    for key in next_state.reset_keys_in_context_store or ():
        new_values[key] = CONTEXT_STORE_DELETE_MARKER

    return new_values


def output_key_context_value(next_state: Any, value: Any) -> Any:
    """What the output key writes into the `context_store` update; it is added last, over any marker for it."""
    if next_state.append_to_context:
        return {CONTEXT_STORE_APPEND_MARKER: [value]}
    return value


def json_pointer_parts(pointer: str) -> list[str]:
    """The keys a workflow JSON pointer follows: split on `/`, slashes at either end ignored, no `~` escapes."""
    return pointer.strip('/').split('/')


def iteration_filter_keys(include_keys: list[str], *, nested: bool) -> list[str] | None:
    """The keys an iteration branch is limited to, or `None` when it sees the whole store.

    A nested branch reuses its parent's store, so only a first-level branch is filtered.
    """
    if nested or include_keys == ITERATOR_INCLUDE_ALL:
        return None
    return include_keys


def iteration_branch_context(
    context_store: Mapping[str, _Value], include_keys: list[str], *, nested: bool
) -> Mapping[str, _Value]:
    """The `context_store` an iteration branch receives: the parent's own store when nested, otherwise a copy,
    filtered by `include_in_iterator_context`."""
    if nested:
        return context_store
    allowed = iteration_filter_keys(include_keys, nested=False)
    if allowed is None:
        return {**context_store}
    return {k: v for k, v in context_store.items() if k in allowed}
