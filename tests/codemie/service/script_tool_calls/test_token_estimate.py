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

"""The tiktoken estimator's records (direction is the model's: model_output = written, model_input = read) of the estimated-savings metric."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.script_tool_calls.context import ScriptRunContext, ScriptScopeKind, ToolListScope
from codemie.service.script_tool_calls.observer import ScriptRunObserver
from codemie.service.script_tool_calls.token_estimate import (
    _emit_inner_calls,
    emit_savings,
    _emit_script_tool,
)
from codemie_tools.data_management.code_executor.tool_call_protocol import CallOutcome

METRIC = "codemie_script_tokens_estimated"


@pytest.fixture
def send() -> MagicMock:
    with patch.object(BaseMonitoringService, "send_count_metric") as mock:
        yield mock


def _context() -> ScriptRunContext:
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
    )


def _outcome(seq: int, *, code: str | None = None, request: int = 8, response: int = 40) -> CallOutcome:
    return CallOutcome(f"c{seq}", "tool.call", "t", code, 0.1, response, seq, request, delivered=True)


def _records(send: MagicMock) -> list[tuple[str, int, dict]]:
    return [(c.kwargs["name"], c.kwargs["count"], c.kwargs["attributes"]) for c in send.call_args_list]


def _closed_observer() -> ScriptRunObserver:
    observer = ScriptRunObserver()
    observer.set_attribution(_context())
    observer.record_detail(1, "search", "internal")
    observer.record_detail(2, "search", "internal")
    observer.record_detail(3, "read", "internal")
    observer.record_detail(4, "boom", "internal")
    observer.record_tokens(1, 2, 10)
    observer.record_tokens(2, 3, 11)
    observer.record_tokens(3, 1, 1)
    observer.on_settled(_outcome(1))
    observer.on_settled(_outcome(2))
    observer.on_settled(_outcome(3))
    observer.on_settled(_outcome(4, code="tool_failed"))
    return observer


def test_inner_calls_are_aggregated_per_tool_and_exclude_failures(send: MagicMock) -> None:
    snapshot = _closed_observer().close()

    _emit_inner_calls(snapshot, run_ok=True)

    records = _records(send)
    assert len(records) == 4
    by_key = {(a["tool_name"], a["direction"]): n for _, n, a in records}
    assert by_key == {
        ("search", "model_output"): 5,
        ("search", "model_input"): 21,
        ("read", "model_output"): 1,
        ("read", "model_input"): 1,
    }
    for name, _, attrs in records:
        assert name == METRIC
        assert attrs["side"] == "inner_calls"
        assert attrs["estimator"] == "tiktoken_v1"
        assert attrs["assistant_name"] == "asst"
        assert attrs["llm_model"] == "gpt"
        assert attrs["project"] == "proj"
        assert "bridge_calls" not in attrs


def test_no_inner_call_records_without_success(send: MagicMock) -> None:
    observer = ScriptRunObserver()
    observer.set_attribution(_context())

    _emit_inner_calls(observer.close(), run_ok=True)

    send.assert_not_called()


def test_script_tool_records_carry_bridge_labels(send: MagicMock) -> None:
    snapshot = _closed_observer().close()

    _emit_script_tool(snapshot, _context(), written_tokens=26, read_tokens=0, run_ok=True)

    records = _records(send)
    assert [(n, a["direction"]) for _, n, a in records] == [(26, "model_output"), (0, "model_input")]
    for name, _, attrs in records:
        assert name == METRIC
        assert attrs["side"] == "script_tool"
        assert attrs["estimator"] == "tiktoken_v1"
        assert attrs["bridge_calls"] == "some"
        assert attrs["bridge_ok"] == "yes"
        assert "tool_name" not in attrs


def test_script_tool_without_calls_is_none_and_not_ok(send: MagicMock) -> None:
    observer = ScriptRunObserver()

    _emit_script_tool(observer.close(), _context(), written_tokens=8, read_tokens=8, run_ok=True)

    for _, _, attrs in _records(send):
        assert attrs["bridge_calls"] == "none"
        assert attrs["bridge_ok"] == "no"


def test_only_failed_calls_are_some_but_not_ok(send: MagicMock) -> None:
    observer = ScriptRunObserver()
    observer.on_settled(_outcome(1, code="tool_failed"))

    _emit_script_tool(observer.close(), _context(), written_tokens=8, read_tokens=8, run_ok=True)

    for _, _, attrs in _records(send):
        assert attrs["bridge_calls"] == "some"
        assert attrs["bridge_ok"] == "no"


def test_a_failing_sender_never_raises() -> None:
    with patch.object(BaseMonitoringService, "send_count_metric", side_effect=RuntimeError("x")):
        _emit_script_tool(ScriptRunObserver().close(), _context(), written_tokens=8, read_tokens=8, run_ok=True)


def test_nothing_is_written_to_the_tools_usage_tokens_metric(send: MagicMock) -> None:
    snapshot = _closed_observer().close()

    _emit_inner_calls(snapshot, run_ok=True)
    _emit_script_tool(snapshot, _context(), written_tokens=8, read_tokens=8, run_ok=True)

    assert {n for n, _, _ in _records(send)} == {METRIC}


def test_the_per_call_cap_applies_to_each_response() -> None:
    observer = ScriptRunObserver()
    observer.set_response_cap_tokens(100)
    observer.record_detail(1, "search", "internal")
    observer.record_detail(2, "search", "internal")
    observer.record_tokens(1, 5, 500)
    observer.record_tokens(2, 5, 30)
    observer.on_settled(_outcome(1))
    observer.on_settled(_outcome(2))

    assert observer.close().tool_tokens["search"] == (10, 130)


def _context_with_ids() -> ScriptRunContext:
    return ScriptRunContext(
        user=MagicMock(id="u1"),
        scope=ToolListScope([]),
        scope_kind=ScriptScopeKind.ASSISTANT,
        assistant_id="asst-1",
        workflow_id="wf-1",
    )


def test_every_series_carries_the_assistant_and_workflow_ids(send: MagicMock) -> None:
    observer = ScriptRunObserver()
    observer.set_attribution(_context_with_ids())
    observer.record_detail(1, "search", "internal")
    observer.record_tokens(1, 2, 10)
    observer.on_settled(_outcome(1))
    snapshot = observer.close()

    _emit_inner_calls(snapshot, run_ok=True)
    _emit_script_tool(snapshot, _context_with_ids(), written_tokens=8, read_tokens=8, run_ok=True)

    records = _records(send)
    assert len(records) == 4
    for _, _, attributes in records:
        assert (attributes["assistant_id"], attributes["workflow_id"]) == ("asst-1", "wf-1")


def test_the_ids_are_left_out_when_the_run_has_none(send: MagicMock) -> None:
    observer = ScriptRunObserver()
    observer.set_attribution(_context())
    snapshot = observer.close()

    _emit_script_tool(snapshot, _context(), written_tokens=8, read_tokens=8, run_ok=True)

    for _, _, attributes in _records(send):
        assert "assistant_id" not in attributes
        assert "workflow_id" not in attributes


@pytest.mark.parametrize(
    "overrides",
    [{"scope_kind": ScriptScopeKind.NONE}, {"is_tool_step": True}],
    ids=["no-assistant-scope", "workflow-tool-step"],
)
def test_neither_half_of_the_pair_is_written_for_a_run_the_rule_refuses(send: MagicMock, overrides: dict) -> None:
    """The rule is applied here, once, for both halves: a run no model wrote gets no estimate at all."""
    import dataclasses

    context = dataclasses.replace(_context(), **overrides)
    observer = ScriptRunObserver()
    observer.set_attribution(context)
    observer.record_detail(1, "search", "internal")
    observer.record_tokens(1, 2, 10)
    observer.on_settled(_outcome(1))
    snapshot = observer.close()

    emit_savings(snapshot, context, written_tokens=8, read_tokens=8, run_ok=True)

    send.assert_not_called()


def test_a_run_the_rule_admits_gets_both_halves(send: MagicMock) -> None:
    snapshot = _closed_observer().close()

    emit_savings(snapshot, _context(), written_tokens=8, read_tokens=8, run_ok=True)

    assert {attrs["side"] for _, _, attrs in _records(send)} == {"inner_calls", "script_tool"}


def test_both_halves_carry_whether_the_run_ended_without_an_error(send: MagicMock) -> None:
    snapshot = _closed_observer().close()

    emit_savings(snapshot, _context(), written_tokens=8, read_tokens=0, run_ok=False)
    failed = {attrs["run_ok"] for _, _, attrs in _records(send)}
    send.reset_mock()
    emit_savings(snapshot, _context(), written_tokens=8, read_tokens=8, run_ok=True)
    finished = {attrs["run_ok"] for _, _, attrs in _records(send)}

    assert failed == {"no"}
    assert finished == {"yes"}
