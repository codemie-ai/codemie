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

"""The record and the histogram of one tool run."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from codemie.service.monitoring import tool_execution_monitoring_service as module
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.tool_execution_monitoring_service import ToolExecutionMonitoringService
from codemie_tools.base import execution_timing
from codemie_tools.base.execution_timing import ToolExecution

METADATA = {
    "agent_name": "asst",
    "llm_model": "gpt",
    "user_name": "User",
    "user_id": "u-1",
    "project": "proj",
    "assistant_id": "a-1",
    "workflow_id": "wf-1",
    "tool_type": "plugin",
}


@pytest.fixture
def record() -> MagicMock:
    with patch.object(module, "send_log_metric") as mock:
        yield mock


@pytest.fixture
def histogram() -> MagicMock:
    with patch.object(BaseMonitoringService, "record_duration_metric") as mock:
        yield mock


def test_a_run_is_one_record_with_its_duration_and_identity(record: MagicMock, histogram) -> None:
    ToolExecutionMonitoringService.record(ToolExecution("jira_search", "direct", 1.23456, True, METADATA))

    name, attributes = record.call_args.args
    assert name == "codemie_tool_execution"
    assert attributes["tool_name"] == "jira_search"
    assert attributes["invoked_by"] == "direct"
    assert attributes["status"] == "success"
    assert attributes["duration_seconds"] == 1.2346
    assert attributes["count"] == 1
    assert (attributes["user_id"], attributes["user_name"]) == ("u-1", "User")
    assert (attributes["assistant_name"], attributes["llm_model"], attributes["tool_type"]) == ("asst", "gpt", "plugin")
    assert (attributes["assistant_id"], attributes["workflow_id"]) == ("a-1", "wf-1")


def test_a_failed_run_says_so_and_names_the_error(record: MagicMock, histogram) -> None:
    ToolExecutionMonitoringService.record(ToolExecution("t", "script", 0.5, False, METADATA, "TimeoutError"))

    attributes = record.call_args.args[1]
    assert (attributes["status"], attributes["error_class"], attributes["invoked_by"]) == (
        "error",
        "TimeoutError",
        "script",
    )


def test_ids_the_run_does_not_have_are_left_out(record: MagicMock, histogram) -> None:
    ToolExecutionMonitoringService.record(ToolExecution("t", "direct", 0.1, True, {"agent_name": "asst"}))

    attributes = record.call_args.args[1]
    assert "assistant_id" not in attributes
    assert "workflow_id" not in attributes
    assert "error_class" not in attributes
    assert attributes["tool_type"] == "internal"


def test_a_tool_without_metadata_still_gets_a_record(record: MagicMock, histogram) -> None:
    ToolExecutionMonitoringService.record(ToolExecution("t", "direct", 0.1, True))

    assert record.call_args.args[1]["user_id"] == ""


def test_the_histogram_has_the_tool_who_called_it_and_how_it_ended_only(record, histogram: MagicMock) -> None:
    ToolExecutionMonitoringService.record(ToolExecution("jira_search", "script", 2.0, True, METADATA))

    assert histogram.call_args.args == ("codemie_tool_execution_seconds", 2.0)
    assert histogram.call_args.kwargs["attributes"] == {
        "tool_name": "jira_search",
        "invoked_by": "script",
        "status": "success",
        "user_id": "-",
    }


def test_a_failing_sender_never_raises(histogram) -> None:
    with patch.object(module, "send_log_metric", side_effect=RuntimeError("x")):
        ToolExecutionMonitoringService.record(ToolExecution("t", "direct", 0.1, True))


def test_registering_makes_tools_report_here() -> None:
    try:
        ToolExecutionMonitoringService.register()

        assert ToolExecutionMonitoringService.record in execution_timing.tool_execution_sinks
    finally:
        execution_timing.tool_execution_sinks.clear()


def test_the_application_registers_the_sink_at_start_up() -> None:
    """main.py is not imported here (it pulls in the whole application); its start-up code must call register() at
    module level, unconditionally, or no tool reports its runs."""
    import ast
    from pathlib import Path

    import codemie.rest_api

    tree = ast.parse((Path(codemie.rest_api.__file__).parent / "main.py").read_text())
    calls = [
        node
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and ast.unparse(node.value.func) == "register_monitoring_sinks"
    ]

    assert len(calls) == 1
