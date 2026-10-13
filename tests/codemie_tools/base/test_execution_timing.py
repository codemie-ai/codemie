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

"""A tool reports how long its own work took, for a model's call and for a script's call."""

from __future__ import annotations

import time
from typing import ClassVar

import pytest
from langchain_core.tools.base import ToolException
from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.execution_timing import (
    ToolExecution,
    report_tool_execution,
    tool_execution_sinks,
)


class _ToolConfig(BaseModel):
    token: str = Field(default="", json_schema_extra={"required_at_runtime": True})


class _Tool(CodeMieTool):
    name: str = "timed_tool"
    description: str = "Takes a moment and returns a value."
    pause: float = 0.0
    error: Exception | None = None
    tokens_size_limit: int = 1000
    calls: ClassVar[int] = 0

    def execute(self, *args: object, **kwargs: object) -> object:
        time.sleep(self.pause)
        if self.error is not None:
            raise self.error
        return {"answer": 1}


class _ConfiguredTool(_Tool):
    config: _ToolConfig = _ToolConfig()


@pytest.fixture
def reports():
    heard: list[ToolExecution] = []
    tool_execution_sinks.clear()
    tool_execution_sinks.add(heard.append)
    yield heard
    tool_execution_sinks.clear()


def test_a_model_call_is_reported_as_direct_with_its_duration(reports) -> None:
    tool = _Tool(pause=0.05, metadata={"agent_name": "asst"})

    tool._run()

    assert len(reports) == 1
    assert (reports[0].tool_name, reports[0].invoked_by, reports[0].ok) == ("timed_tool", "direct", True)
    assert reports[0].duration_seconds >= 0.05
    assert reports[0].metadata == {"agent_name": "asst"}
    assert reports[0].error_class is None


def test_a_script_call_is_reported_as_script(reports) -> None:
    _Tool(pause=0.03).run_for_script({})

    assert [(r.invoked_by, r.ok) for r in reports] == [("script", True)]
    assert reports[0].duration_seconds >= 0.03


def test_the_structured_twin_is_reported_as_script_too(reports) -> None:
    _Tool().execute_structured()

    assert [r.invoked_by for r in reports] == ["script"]


def test_a_failing_call_is_reported_with_the_class_of_the_error_and_still_raises(reports) -> None:
    tool = _Tool(error=ValueError("boom"))

    with pytest.raises(ToolException):
        tool._run()

    assert (reports[0].ok, reports[0].error_class) == (False, "ValueError")


def test_a_failing_script_call_raises_unchanged(reports) -> None:
    tool = _Tool(error=KeyError("missing"))

    with pytest.raises(KeyError):
        tool.run_for_script({})

    assert (reports[0].invoked_by, reports[0].ok, reports[0].error_class) == ("script", False, "KeyError")


def test_only_the_tools_own_work_is_timed_not_the_configuration_check(reports) -> None:
    tool = _ConfiguredTool()  # a required token is missing: the config check fails before execute

    with pytest.raises(Exception):  # noqa: B017, PT011
        tool.run_for_script({})

    assert reports == []


def test_the_output_limit_is_not_part_of_the_duration(reports) -> None:
    tool = _Tool(pause=0.0)
    slow_limit = tool._limit_output_content

    def slower(output):
        time.sleep(0.2)
        return slow_limit(output)

    object.__setattr__(tool, "_limit_output_content", slower)
    tool._run()

    assert reports[0].duration_seconds < 0.15


def test_without_a_sink_nothing_happens() -> None:
    tool_execution_sinks.clear()

    assert _Tool()._run() is not None


def test_a_sink_that_raises_never_reaches_the_tool() -> None:
    def broken(execution: ToolExecution) -> None:
        raise RuntimeError("sink down")

    tool_execution_sinks.clear()
    tool_execution_sinks.add(broken)
    try:
        assert _Tool()._run() is not None
    finally:
        tool_execution_sinks.clear()


def test_a_report_can_be_made_directly(reports) -> None:
    report_tool_execution(ToolExecution("t", "direct", 0.1, True))

    assert reports[0].tool_name == "t"


def test_sinks_are_called_in_the_order_added_and_a_sink_is_added_once() -> None:
    from codemie_tools.base.execution_timing import Sinks

    sinks: Sinks[int] = Sinks("test")
    heard: list[str] = []

    def first(_: int) -> None:
        heard.append("first")

    def second(_: int) -> None:
        heard.append("second")

    sinks.add(first)
    sinks.add(second)
    sinks.add(first)
    sinks.report(1)

    assert heard == ["first", "second"]


def test_a_removed_sink_hears_nothing_and_a_failing_one_does_not_hide_the_next() -> None:
    from codemie_tools.base.execution_timing import Sinks

    sinks: Sinks[int] = Sinks("test")
    heard: list[int] = []
    removed = heard.append

    def broken(_: int) -> None:
        raise RuntimeError("boom")

    sinks.add(broken)
    sinks.add(removed)
    sinks.add(lambda value: heard.append(value * 10))
    sinks.remove(removed)
    sinks.report(2)

    assert heard == [20]
