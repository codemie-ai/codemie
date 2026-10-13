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

"""The one set of dimensions every record of a script run is written under."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from codemie.service.script_tool_calls.context import ScriptRunContext, ScriptScopeKind, ToolListScope
from codemie.service.script_tool_calls.dimensions import bridge_labels, run_dimensions
from codemie.service.script_tool_calls.observer import ScriptRunObserver
from codemie_tools.data_management.code_executor.tool_call_protocol import CallOutcome


def _context(**ids: str) -> ScriptRunContext:
    user = MagicMock(id="u1")
    user.name = "User"
    return ScriptRunContext(
        user=user,
        scope=ToolListScope([]),
        scope_kind=ScriptScopeKind.ASSISTANT,
        assistant_name="asst",
        llm_model="gpt",
        project="proj",
        user_id="u1",
        **ids,
    )


def test_the_run_dimensions_are_user_project_assistant_and_model() -> None:
    assert run_dimensions(_context()) == {
        "assistant_name": "asst",
        "llm_model": "gpt",
        "project": "proj",
        "user_id": "u1",
        "user_name": "User",
    }


def test_ids_are_added_only_when_the_run_has_them() -> None:
    dimensions = run_dimensions(_context(assistant_id="virtual", workflow_id="wf-1"))

    assert (dimensions["assistant_id"], dimensions["workflow_id"]) == ("virtual", "wf-1")
    assert "assistant_id" not in run_dimensions(_context(workflow_id="wf-1"))
    assert "workflow_id" not in run_dimensions(_context(assistant_id="a1"))


def test_a_run_without_a_context_has_empty_dimensions() -> None:
    assert run_dimensions(None) == {
        "assistant_name": "",
        "llm_model": "",
        "project": "",
        "user_id": "",
        "user_name": "",
    }


@pytest.mark.parametrize(
    ("outcomes", "calls", "ok"),
    [
        ([], "none", "no"),
        ([CallOutcome("c1", "tool.call", "t", "tool_failed", 0.1, 5, 1, 4, delivered=True)], "some", "no"),
        ([CallOutcome("c1", "tool.call", "t", None, 0.1, 5, 1, 4, delivered=True)], "some", "yes"),
    ],
    ids=["no calls", "only a failed call", "a successful call"],
)
def test_bridge_labels_say_whether_the_run_called_tools_and_whether_one_succeeded(outcomes, calls, ok) -> None:
    observer = ScriptRunObserver()
    for outcome in outcomes:
        observer.on_settled(outcome)

    assert bridge_labels(observer.close()) == {"bridge_calls": calls, "bridge_ok": ok}


def test_a_script_run_and_a_direct_call_of_one_tool_have_the_same_dimensions() -> None:
    """Both read the tool's metadata through the same function, so one tool's two series cannot drift apart."""
    from codemie.rest_api.security.user import User
    from codemie.service.monitoring.tool_run_context import ToolRunContext
    from codemie.service.script_tool_calls.context import ScriptToolRegistry

    metadata = {"agent_name": "asst", "llm_model": "gpt", "project": "proj", "user_name": "U"}
    user = User(id="u-1", name="U", username="u1")
    registry = ScriptToolRegistry(user, assistant_id="a-1")
    registry.fill([])

    from_context = run_dimensions(registry.context(metadata))
    from_metadata = ToolRunContext.from_metadata({**metadata, "assistant_id": "a-1"}, "u-1").dimensions()

    assert from_context == from_metadata


def test_a_context_survives_the_trip_through_tool_metadata() -> None:
    """The workspace run reports through the tool-metadata channel; the listener must read back the same identity."""
    from codemie.service.monitoring.tool_run_context import ToolRunContext

    context = ToolRunContext(
        user_id="u-1",
        user_name="U",
        assistant_name="asst",
        llm_model="gpt",
        project="proj",
        assistant_id="a-1",
        workflow_id="wf-1",
    )

    assert ToolRunContext.from_metadata(context.to_metadata(), context.user_id).dimensions() == context.dimensions()


def test_the_identity_keys_list_is_exactly_what_the_context_reads_from_tool_metadata() -> None:
    """A key read but not listed (or listed but not read) would make the direct-call series lose a label silently."""
    from collections.abc import Mapping

    from codemie.service.monitoring.tool_run_context import IDENTITY_METADATA_KEYS, ToolRunContext

    read: set[str] = set()

    class Spy(Mapping):
        def __getitem__(self, key):
            raise KeyError(key)

        def get(self, key, default=None):
            read.add(key)
            return default

        def __iter__(self):
            return iter(())

        def __len__(self):
            return 1  # not empty: an empty mapping is replaced by `{}` before it is read

    ToolRunContext.attribution_from_metadata(Spy())

    assert read - {"user_id"} == set(IDENTITY_METADATA_KEYS)
