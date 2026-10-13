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

"""The counters, the run record and the duration histograms of sandbox runs."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from codemie.rest_api.security.workflow_context import set_current_workflow_id
from codemie.service.monitoring import code_execution_monitoring_service as module
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.tool_run_context import ToolRunContext
from codemie.service.monitoring.code_execution_monitoring_service import CodeExecutionMonitoringService
from codemie.service.script_tool_calls.observer import RunSnapshot
from codemie_tools.data_management.code_executor.run_observer import RunOutcome, SandboxFailure, SandboxRunObserver

RUNS = "codemie_code_execution_runs_total"
ATTEMPTS = "codemie_code_execution_attempts_total"
WAITS = "codemie_code_execution_capacity_waits_total"
REJECTIONS = "codemie_code_execution_capacity_rejections_total"
RUN_RECORD = "codemie_code_execution_run"


@pytest.fixture
def send() -> MagicMock:
    with patch.object(BaseMonitoringService, "send_count_metric") as mock:
        yield mock


@pytest.fixture
def record() -> MagicMock:
    with patch.object(module, "send_log_metric") as mock:
        yield mock


@pytest.fixture
def durations() -> MagicMock:
    with patch.object(BaseMonitoringService, "record_duration_metric") as mock:
        yield mock


def _send_run(sandbox_run: SandboxRunObserver, elapsed: float = 2.0, calls: RunSnapshot | None = None, **dims: str):
    CodeExecutionMonitoringService.send_run(
        tool_name="code_executor",
        sandbox_mode="sandbox-jobs",
        sandbox_run=sandbox_run,
        elapsed_seconds=elapsed,
        dimensions=dims,
        calls=calls,
    )


def _names(send: MagicMock) -> list[str]:
    return [call.kwargs["name"] for call in send.call_args_list]


def _snapshot(**overrides: object) -> RunSnapshot:
    fields = {
        "bridge_calls": 6,
        "success_calls": 5,
        "failed_calls": 1,
        "abandoned_calls": 0,
        "tool_tokens": {},
        "attribution": None,
        "peak_parallel": 3,
        "gate_waited_calls": 2,
        "gate_wait_seconds": 1.5,
        "gate_wait_max_seconds": 1.0,
        "gate_timeout_calls": 1,
    }
    fields.update(overrides)
    return RunSnapshot(**fields)  # type: ignore[arg-type]


# ------------------------------------------------------------------ counters


def test_a_run_that_never_asked_for_capacity_counts_by_its_outcome_only(send: MagicMock, record, durations) -> None:
    sandbox_run = SandboxRunObserver()
    sandbox_run.fail(SandboxFailure("blocked", RunOutcome.POLICY_DENIED))

    _send_run(sandbox_run)

    assert _names(send) == [RUNS]
    assert send.call_args.kwargs["attributes"]["outcome"] == "policy_denied"


def test_a_run_that_got_its_capacity_is_a_run_and_an_attempt(send: MagicMock, record, durations) -> None:
    sandbox_run = SandboxRunObserver()
    sandbox_run.mark_requested()

    _send_run(sandbox_run)

    assert _names(send) == [RUNS, ATTEMPTS]
    assert send.call_args_list[0].kwargs["attributes"]["outcome"] == "ok"


def test_a_run_that_waited_adds_one_wait(send: MagicMock, record, durations) -> None:
    sandbox_run = SandboxRunObserver()
    sandbox_run.mark_waited()
    sandbox_run.mark_waited()

    _send_run(sandbox_run)

    assert _names(send) == [RUNS, ATTEMPTS, WAITS]


def test_a_rejected_run_adds_a_wait_and_a_rejection(send: MagicMock, record, durations) -> None:
    sandbox_run = SandboxRunObserver()
    sandbox_run.mark_rejected()

    _send_run(sandbox_run)

    assert _names(send) == [RUNS, ATTEMPTS, WAITS, REJECTIONS]
    assert send.call_args_list[0].kwargs["attributes"]["outcome"] == "capacity_rejected"


def test_every_counter_carries_the_tool_the_mode_and_the_dimensions(send: MagicMock, record, durations) -> None:
    sandbox_run = SandboxRunObserver()
    sandbox_run.mark_rejected()

    _send_run(sandbox_run, user_id="u-1", project="proj", assistant_id="a-1")

    for call in send.call_args_list:
        attributes = call.kwargs["attributes"]
        assert attributes["tool_name"] == "code_executor"
        assert attributes["sandbox_mode"] == "sandbox-jobs"
        assert (attributes["user_id"], attributes["project"], attributes["assistant_id"]) == ("u-1", "proj", "a-1")


# ------------------------------------------------------------------ the run record


def test_the_run_record_has_the_outcome_and_the_durations(send: MagicMock, record: MagicMock, durations) -> None:
    sandbox_run = SandboxRunObserver()
    sandbox_run.mark_waited()
    sandbox_run.set_acquire_seconds(1.23456)
    sandbox_run.set_pod_start_seconds(2.0)
    sandbox_run.set_pod_seconds(9.5)
    sandbox_run.set_exit_code(1)

    _send_run(sandbox_run, elapsed=10.0, user_id="u-1")

    name, attributes = record.call_args.args
    assert name == RUN_RECORD
    assert attributes["outcome"] == "script_error"
    assert attributes["count"] == 1
    assert attributes["elapsed_seconds"] == 10.0
    assert attributes["acquire_seconds"] == 1.235
    assert attributes["pod_start_seconds"] == 2.0
    assert attributes["pod_seconds"] == 9.5
    assert attributes["capacity_waited"] is True
    assert attributes["user_id"] == "u-1"
    assert "calls_total" not in attributes


def test_durations_that_were_not_measured_are_left_out(send: MagicMock, record: MagicMock, durations) -> None:
    _send_run(SandboxRunObserver())

    attributes = record.call_args.args[1]
    assert "acquire_seconds" not in attributes
    assert "pod_seconds" not in attributes


def test_the_run_record_has_what_the_script_did_through_the_bridge(
    send: MagicMock, record: MagicMock, durations
) -> None:
    _send_run(SandboxRunObserver(), calls=_snapshot())

    attributes = record.call_args.args[1]
    assert attributes["calls_total"] == 6
    assert attributes["calls_success"] == 5
    assert attributes["peak_parallel"] == 3
    assert attributes["gate_waited_calls"] == 2
    assert attributes["gate_wait_seconds"] == 1.5
    assert attributes["gate_wait_max_seconds"] == 1.0
    assert attributes["gate_timeout_calls"] == 1


# ------------------------------------------------------------------ histograms


def test_durations_go_to_histograms_with_the_tool_and_the_mode_only(
    send: MagicMock, record, durations: MagicMock
) -> None:
    sandbox_run = SandboxRunObserver()
    sandbox_run.mark_requested()
    sandbox_run.set_acquire_seconds(0.5)
    sandbox_run.set_pod_start_seconds(2.0)
    sandbox_run.set_pod_seconds(9.0)

    _send_run(sandbox_run, elapsed=10.0, user_id="u-1", project="proj")

    by_name = {call.args[0]: (call.args[1], call.kwargs["attributes"]) for call in durations.call_args_list}
    assert by_name["codemie_code_execution_run_seconds"][0] == 10.0
    assert by_name["codemie_code_execution_acquire_seconds"][0] == 0.5
    assert by_name["codemie_code_execution_pod_start_seconds"][0] == 2.0
    assert by_name["codemie_code_execution_pod_seconds"][0] == 9.0
    for _, labels in by_name.values():
        assert labels == {"tool_name": "code_executor", "sandbox_mode": "sandbox-jobs", "user_id": "-"}


def test_a_duration_that_was_not_measured_is_not_recorded(send: MagicMock, record, durations: MagicMock) -> None:
    _send_run(SandboxRunObserver(), elapsed=1.0)

    assert [call.args[0] for call in durations.call_args_list] == ["codemie_code_execution_run_seconds"]


def test_a_failing_sender_never_raises(record, durations) -> None:
    sandbox_run = SandboxRunObserver()
    sandbox_run.mark_requested()

    with patch.object(BaseMonitoringService, "send_count_metric", side_effect=RuntimeError("x")):
        _send_run(sandbox_run)


# ------------------------------------------------------------------ dimensions


def test_dimensions_of_a_tool_come_from_the_metadata_the_agent_stamped() -> None:
    metadata = {
        "agent_name": "asst",
        "llm_model": "gpt",
        "user_name": "User",
        "project": "proj",
        "assistant_id": "a-1",
    }

    dimensions = ToolRunContext.from_metadata(metadata, "u-1").dimensions()

    assert dimensions["user_id"] == "u-1"
    assert dimensions["user_name"] == "User"
    assert dimensions["assistant_name"] == "asst"
    assert dimensions["llm_model"] == "gpt"
    assert dimensions["assistant_id"] == "a-1"
    assert "workflow_id" not in dimensions


def test_a_tool_without_metadata_still_has_dimensions() -> None:
    dimensions = ToolRunContext.from_metadata(None, None).dimensions()

    assert dimensions["user_id"] == ""
    assert "assistant_id" not in dimensions


def test_the_workflow_id_is_the_one_the_workflow_executor_bound() -> None:
    set_current_workflow_id("wf-1")
    try:
        dimensions = ToolRunContext.from_metadata({}, "u-1").dimensions()
    finally:
        set_current_workflow_id(None)

    assert dimensions["workflow_id"] == "wf-1"
