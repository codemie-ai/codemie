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

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from codemie.service.monitoring.agent_monitoring_service import AgentMonitoringService
from codemie.service.monitoring.metrics_constants import (
    SCRIPT_TOOL_CALLS_REFUSED_METRIC,
    TOOLS_USAGE_ERRORS_METRIC,
    TOOLS_USAGE_TOKENS_METRIC,
    TOOLS_USAGE_TOTAL_METRIC,
    ScriptCallKind,
)
from codemie.service.script_tool_calls.context import ScriptRunContext, ScriptScopeKind, ToolListScope
from codemie.service.script_tool_calls.observer import ScriptRunObserver
from codemie.service.script_tool_calls.outcome_metrics import ScriptCallMetricsListener, classify
from codemie_tools.data_management.code_executor.runtime_sdk import codemie_runtime_sdk as sdk
from codemie_tools.data_management.code_executor.tool_call_protocol import CallOutcome


def _outcome(code=None, *, delivered=True, withdrawn=False, seq=1) -> CallOutcome:
    return CallOutcome("c1", "tool.call", "raw", code, 0.1, 10, seq, 4, delivered=delivered, withdrawn=withdrawn)


ERRORS = [
    sdk.CODE_TOOL_FAILED,
    sdk.CODE_BAD_ARGUMENTS,
    sdk.CODE_PAYLOAD_TOO_LARGE,
    sdk.CODE_INTERNAL_ERROR,
    sdk.CODE_TIMEOUT,
    sdk.CODE_UNAVAILABLE,
    sdk.CODE_ERROR,
]
PROTOCOL_FAULTS = [sdk.CODE_UNKNOWN_OP, sdk.CODE_BAD_REQUEST]
REFUSALS = [sdk.CODE_TOOL_BLOCKED, sdk.CODE_TOOL_UNAVAILABLE, sdk.CODE_NO_CONTEXT, sdk.CODE_DEADLINE_EXCEEDED]


@pytest.mark.parametrize(
    ("outcome", "kind", "name_known"),
    [
        (_outcome(), ScriptCallKind.SUCCESS, True),
        *[(_outcome(code), ScriptCallKind.ERROR, True) for code in ERRORS],
        *[(_outcome(code), ScriptCallKind.ERROR, False) for code in PROTOCOL_FAULTS],
        *[(_outcome(code), ScriptCallKind.REFUSED, True) for code in REFUSALS],
        (_outcome(withdrawn=True, delivered=False), ScriptCallKind.ABANDONED, True),
        (_outcome(None, delivered=False), ScriptCallKind.ABANDONED, True),
    ],
)
def test_the_outcome_map(outcome: CallOutcome, kind: str, name_known: bool) -> None:
    result = classify(outcome)

    assert result.kind == kind
    assert result.name_known is name_known


def test_every_sdk_code_has_an_explicit_place_in_the_map() -> None:
    """A new code of the SDK must be listed above (error, protocol fault or refusal): the default is only a net."""
    listed = {*ERRORS, *PROTOCOL_FAULTS, *REFUSALS}

    assert set(sdk.ERROR_CODES) == listed


def test_an_unlisted_code_is_an_error_not_a_crash() -> None:
    assert classify(_outcome("brand_new")).kind == ScriptCallKind.ERROR


def _context(kind=ScriptScopeKind.ASSISTANT) -> ScriptRunContext:
    user = MagicMock(id="user-1")
    user.name = "Yana"
    return ScriptRunContext(
        user=user,
        scope=ToolListScope([]),
        scope_kind=kind,
        assistant_name="asst",
        llm_model="model-x",
        project="proj",
        user_id="user-1",
    )


def _listener(kind=ScriptScopeKind.ASSISTANT, name="resolved"):
    observer = ScriptRunObserver()
    observer.set_attribution(_context(kind))
    observer.record_detail(1, name, "internal")
    return ScriptCallMetricsListener(observer)


def _emitted(outcome: CallOutcome, **kwargs):
    with patch.object(AgentMonitoringService, "send_count_metric") as send:
        _listener(**kwargs)(outcome)
    return send.call_args_list


def _attrs(call) -> dict:
    return call.kwargs["attributes"]


def test_success_writes_the_usage_total_only() -> None:
    calls = _emitted(_outcome())

    assert [c.kwargs["name"] for c in calls] == [TOOLS_USAGE_TOTAL_METRIC]
    attrs = _attrs(calls[0])
    assert attrs["tool_name"] == "resolved"
    assert attrs["base_tool_name"] == "resolved"
    assert attrs["invoked_by"] == "script"
    assert attrs["status"] == "success"
    assert attrs["assistant_name"] == "asst"
    assert attrs["llm_model"] == "model-x"
    assert attrs["project"] == "proj"
    assert attrs["user_id"] == "user-1"
    assert attrs["user_name"] == "Yana"
    assert attrs["tool_type"] == "internal"
    assert "script_path" not in attrs and "skill_script" not in attrs


def test_nothing_is_ever_written_to_the_tokens_metric() -> None:
    outcomes = [_outcome(), _outcome(sdk.CODE_ERROR), _outcome(sdk.CODE_TOOL_BLOCKED), _outcome(withdrawn=True)]
    names = {c.kwargs["name"] for o in outcomes for c in _emitted(o)}

    assert TOOLS_USAGE_TOKENS_METRIC not in names


def test_a_refusal_writes_only_the_refused_counter() -> None:
    calls = _emitted(_outcome(sdk.CODE_TOOL_BLOCKED))

    assert [c.kwargs["name"] for c in calls] == [SCRIPT_TOOL_CALLS_REFUSED_METRIC]
    assert "invoked_by" not in _attrs(calls[0])  # ticket decision 4: the refused counter has no invoked_by
    assert _attrs(calls[0])["error_code"] == sdk.CODE_TOOL_BLOCKED


def test_an_error_carries_its_code() -> None:
    calls = _emitted(_outcome(sdk.CODE_TOOL_FAILED))

    assert [c.kwargs["name"] for c in calls] == [TOOLS_USAGE_ERRORS_METRIC]
    assert _attrs(calls[0])["error_code"] == sdk.CODE_TOOL_FAILED
    assert _attrs(calls[0])["tool_name"] == "resolved"


def test_a_protocol_fault_is_an_error_on_the_unknown_tool() -> None:
    calls = _emitted(_outcome(sdk.CODE_BAD_REQUEST))

    assert _attrs(calls[0])["tool_name"] == "unknown"
    assert _attrs(calls[0])["base_tool_name"] == "unknown"


def test_an_unresolved_call_is_counted_as_unknown() -> None:
    calls = _emitted(_outcome(sdk.CODE_ERROR, seq=99))

    assert _attrs(calls[0])["tool_name"] == "unknown"


@pytest.mark.parametrize("outcome", [_outcome(withdrawn=True, delivered=False), _outcome(None, delivered=False)])
def test_withdrawn_and_abandoned_write_usage_as_abandoned(outcome: CallOutcome) -> None:
    calls = _emitted(outcome)

    assert [c.kwargs["name"] for c in calls] == [TOOLS_USAGE_TOTAL_METRIC]
    assert _attrs(calls[0])["status"] == "abandoned"


def test_a_tool_step_has_no_assistant_but_keeps_user_and_project() -> None:
    observer = ScriptRunObserver()
    context = _context(ScriptScopeKind.ASSISTANT)
    object.__setattr__(context, "assistant_name", "")
    object.__setattr__(context, "llm_model", "")
    observer.set_attribution(context)
    observer.record_detail(1, "resolved", "internal")

    with patch.object(AgentMonitoringService, "send_count_metric") as send:
        ScriptCallMetricsListener(observer)(_outcome())

    attrs = send.call_args.kwargs["attributes"]
    assert attrs["assistant_name"] == ""
    assert attrs["user_id"] == "user-1"
    assert attrs["project"] == "proj"


def test_a_run_without_context_writes_refused_records_only() -> None:
    refused = _emitted(_outcome(sdk.CODE_NO_CONTEXT), kind=ScriptScopeKind.NONE)
    other = _emitted(_outcome(sdk.CODE_BAD_REQUEST), kind=ScriptScopeKind.NONE)
    abandoned = _emitted(_outcome(withdrawn=True, delivered=False), kind=ScriptScopeKind.NONE)

    assert [c.kwargs["name"] for c in refused] == [SCRIPT_TOOL_CALLS_REFUSED_METRIC]
    assert other == [] and abandoned == []


def test_without_attribution_nothing_is_emitted() -> None:
    with patch.object(AgentMonitoringService, "send_count_metric") as send:
        ScriptCallMetricsListener(ScriptRunObserver())(_outcome())

    send.assert_not_called()


def test_the_listener_writes_the_runs_assistant_and_workflow_ids() -> None:
    observer = ScriptRunObserver()
    observer.set_attribution(
        ScriptRunContext(
            user=MagicMock(id="u", **{"name": "n"}),
            scope=ToolListScope([]),
            scope_kind=ScriptScopeKind.ASSISTANT,
            assistant_id="virtual",
            workflow_id="wf-1",
        )
    )
    observer.record_detail(1, "resolved", "internal")

    with patch.object(AgentMonitoringService, "send_count_metric") as send:
        ScriptCallMetricsListener(observer)(_outcome())

    attrs = _attrs(send.call_args_list[0])
    assert (attrs["assistant_id"], attrs["workflow_id"]) == ("virtual", "wf-1")


def test_a_run_without_ids_writes_neither_label() -> None:
    attrs = _attrs(_emitted(_outcome())[0])

    assert "assistant_id" not in attrs
    assert "workflow_id" not in attrs


def test_an_unknown_operation_is_an_error_on_the_unknown_tool_while_another_operation_writes_nothing() -> None:
    """The real shape of an unknown operation: the request had an op, the protocol answered `unknown_op`."""
    unknown = CallOutcome("c1", "nope", None, sdk.CODE_UNKNOWN_OP, 0.1, 10, 1, 4, delivered=True)
    other = CallOutcome("c2", "other.op", None, None, 0.1, 10, 2, 4, delivered=True)

    errors = _emitted(unknown)

    assert [c.kwargs["name"] for c in errors] == [TOOLS_USAGE_ERRORS_METRIC]
    assert _attrs(errors[0])["tool_name"] == "unknown"
    assert _emitted(other) == []
