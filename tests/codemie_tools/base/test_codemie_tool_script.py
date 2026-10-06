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

from __future__ import annotations

from datetime import date
from typing import ClassVar

import pytest
from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.http_result import HttpResult
from codemie_tools.base.script_result import JsonValue, ScriptResult, ScriptResultTooLarge


class _Item(BaseModel):
    name: str
    day: date
    tags: list[str] = Field(default_factory=list)


class _SourceOutput:
    def to_script_result(self, max_bytes: int | None = None) -> ScriptResult:
        return ScriptResult(result={"from": "source"})

    def __str__(self) -> str:
        return "llm text"


class _Opaque:
    def __str__(self) -> str:
        return "opaque-value"


class _ToolConfig(BaseModel):
    token: str = Field(default="", json_schema_extra={"required_at_runtime": True})


class _ScriptTool(CodeMieTool):
    name: str = "script_tool"
    description: str = "Returns whatever it was told to return."
    output: object = None
    error: Exception | None = None
    tokens_size_limit: int = 5
    calls: ClassVar[list[dict[str, object]]] = []

    def execute(self, *args: object, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        return self.output


class _ConfiguredTool(_ScriptTool):
    config: _ToolConfig = _ToolConfig()


def _structured(output: object) -> ScriptResult:
    return _ScriptTool(output=output).execute_structured()


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (_SourceOutput(), {"from": "source"}),
        (_Item(name="a", day=date(2026, 1, 2), tags=["x"]), {"name": "a", "day": "2026-01-02", "tags": ["x"]}),
        ({"a": 1, "b": [1, "x", None]}, {"a": 1, "b": [1, "x", None]}),
        ({"d": date(2026, 1, 2), 3: {"o": _Opaque()}}, {"d": "2026-01-02", "3": {"o": "opaque-value"}}),
        ([1, _Opaque(), {"k": _Opaque()}], [1, "opaque-value", {"k": "opaque-value"}]),
        (None, None),
        (True, True),
        (False, False),
        (7, 7),
        (1.5, 1.5),
        ('{"a": [1, 2]}', {"a": [1, 2]}),
        ('  [1, {"b": 2}]  ', [1, {"b": 2}]),
        ("{not json}", "{not json}"),
        ("[unterminated", "[unterminated"),
        ("plain text", "plain text"),
        ("42", "42"),
        ("", ""),
        (_Opaque(), "opaque-value"),
        (("a", "b"), "('a', 'b')"),
    ],
)
def test_execute_structured_converts_raw_output(output: object, expected: JsonValue) -> None:
    result = _structured(output)

    assert result == ScriptResult(result=expected)
    assert result.http_status is None
    assert result.http_reason is None


def test_execute_structured_fills_http_fields_and_parsed_body() -> None:
    http = HttpResult("GET", "https://x.test/a", 404, "Not Found", '{"error": "nope"}', "spaced")

    result = _structured(http)

    assert result == ScriptResult(result={"error": "nope"}, http_status=404, http_reason="Not Found")


def test_execute_structured_http_result_with_plain_body_and_no_reason() -> None:
    http = HttpResult("GET", "https://x.test/a", 200, None, "hello", "compact")

    result = _structured(http)

    assert result == ScriptResult(result="hello", http_status=200, http_reason=None)


def test_execute_structured_passes_arguments_to_execute() -> None:
    _ScriptTool.calls.clear()

    _ScriptTool(output="ok").execute_structured(query="q", limit=3)

    assert _ScriptTool.calls == [{"query": "q", "limit": 3}]


def test_execute_structured_propagates_exceptions_unchanged() -> None:
    boom = RuntimeError("boom")

    with pytest.raises(RuntimeError) as excinfo:
        _ScriptTool(error=boom).execute_structured()

    assert excinfo.value is boom


def test_run_for_script_does_no_token_limiting() -> None:
    big = "word " * 100_000
    tool = _ScriptTool(output=big)

    result = tool.run_for_script({})

    assert result == ScriptResult(result=big)
    assert tool.tokens_size_limit == 5


def test_run_for_script_raises_instead_of_returning_failures() -> None:
    boom = ValueError("bad")

    with pytest.raises(ValueError) as excinfo:
        _ScriptTool(error=boom).run_for_script({})

    assert excinfo.value is boom


def test_run_for_script_validates_config_first() -> None:
    _ScriptTool.calls.clear()

    with pytest.raises(ValueError, match="Tool config is not set"):
        _ConfiguredTool(output="ok").run_for_script({})

    assert _ScriptTool.calls == []


def test_run_for_script_runs_with_valid_config() -> None:
    tool = _ConfiguredTool(output={"ok": True}, config=_ToolConfig(token="t"))

    assert tool.run_for_script({"a": 1}) == ScriptResult(result={"ok": True})


def test_run_for_script_passes_the_arguments_as_keywords_to_execute() -> None:
    _ScriptTool.calls.clear()

    _ScriptTool(output="ok").run_for_script({"query": "q", "limit": 3})

    assert _ScriptTool.calls == [{"query": "q", "limit": 3}]


class _ScriptOnlyTool(_ScriptTool):
    """A tool whose script run differs from its model run, through the one hook."""

    def _execute_for_script(self, *args: object, **kwargs: object) -> object:
        return {"for": "script", "args": dict(kwargs)}


def test_the_hook_is_what_the_script_path_runs_and_execute_stays_the_model_path() -> None:
    tool = _ScriptOnlyTool(output="for the model")

    assert tool.run_for_script({"a": 1}) == ScriptResult(result={"for": "script", "args": {"a": 1}})
    assert tool.execute_structured(a=1) == ScriptResult(result={"for": "script", "args": {"a": 1}})
    assert tool.execute() == "for the model"


def test_the_default_hook_is_execute() -> None:
    assert _ScriptTool(output="x")._execute_for_script() == "x"


class _SizeAware:
    def __init__(self) -> None:
        self.seen: list[int | None] = []

    def to_script_result(self, max_bytes: int | None = None) -> ScriptResult:
        self.seen.append(max_bytes)
        if max_bytes is not None and max_bytes < 10:
            raise ScriptResultTooLarge(10)
        return ScriptResult(result="fits")


def test_the_size_cap_reaches_the_output_that_can_use_it() -> None:
    source = _SizeAware()

    assert _ScriptTool(output=source).run_for_script({}, max_result_bytes=100) == ScriptResult(result="fits")
    assert source.seen == [100]


def test_an_output_that_knows_it_is_too_large_is_refused_by_the_cap() -> None:
    with pytest.raises(ScriptResultTooLarge):
        _ScriptTool(output=_SizeAware()).run_for_script({}, max_result_bytes=5)


def test_without_a_cap_the_output_gets_none() -> None:
    source = _SizeAware()

    _ScriptTool(output=source).run_for_script({})

    assert source.seen == [None]
