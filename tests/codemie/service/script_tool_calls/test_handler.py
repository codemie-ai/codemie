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

import json
import logging
from collections.abc import Iterator, Mapping
from unittest.mock import patch

import pytest
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

from codemie.configs.logger import logger as app_logger
from codemie.configs.script_call_log_guard import ScriptCallLogFilter
from codemie.rest_api.security.user import User
from codemie.service.script_tool_calls.concurrency import ToolRunGate
from codemie.service.script_tool_calls.context import ScriptRunContext, ScriptScopeKind, ToolListScope
from codemie.service.script_tool_calls.handler import build_tool_call_handlers
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.http_result import HttpResult
from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import TOOL_CALL_OP
from codemie_tools.data_management.code_executor.tool_call_protocol import ToolCallHandler, ToolCallRefused

SENTINEL = "SENTINEL-SECRET-VALUE"
_tool_logger = logging.getLogger("codemie.test_handler.tool")


class _Args(BaseModel):
    text: str
    count: int = 1
    tags: list[str] = Field(default_factory=list)


class _EchoArgsTool(CodeMieTool):
    script_callable = True
    name: str = "echo_args"
    description: str = "returns the kwargs it received"
    args_schema: type[BaseModel] = _Args

    def execute(self, **kwargs: object) -> dict[str, object]:
        return {"received": kwargs}


class _NoArgsTool(CodeMieTool):
    script_callable = True
    name: str = "no_args"
    description: str = "no args"

    def execute(self, **kwargs: object) -> str:
        return '{"a": 1}'


class _HttpTool(CodeMieTool):
    script_callable = True
    name: str = "http_tool"
    description: str = "returns an HttpResult"
    args_schema: type[BaseModel] = _Args

    def execute(self, **kwargs: object) -> HttpResult:
        return HttpResult(
            method="GET",
            url="https://example.test/x",
            status=404,
            reason=None,
            body='{"missing": true}',
            layout="spaced",
        )


class _CountingTool(CodeMieTool):
    script_callable = True
    name: str = "counting"
    description: str = "counts how often it ran"
    args_schema: type[BaseModel] = _Args
    calls: int = 0

    def execute(self, **kwargs: object) -> str:
        self.calls += 1
        return "ran"


class _AliasArgs(BaseModel):
    query: str = Field(alias="jql")


class _AliasTool(CodeMieTool):
    script_callable = True
    name: str = "alias_tool"
    description: str = "schema with a field alias"
    args_schema: type[BaseModel] = _AliasArgs

    def execute(self, **kwargs: object) -> dict[str, object]:
        return {"received": kwargs}


class _ExtraArgs(BaseModel):
    model_config = ConfigDict(extra="allow")

    text: str


class _ExtraTool(CodeMieTool):
    script_callable = True
    name: str = "extra_tool"
    description: str = "schema that allows extra keys"
    args_schema: type[BaseModel] = _ExtraArgs

    def execute(self, **kwargs: object) -> dict[str, object]:
        return {"received": kwargs}


class _RaisingTool(CodeMieTool):
    script_callable = True
    name: str = "raising"
    description: str = "raises"
    args_schema: type[BaseModel] = _Args
    message: str = "boom"

    def execute(self, **kwargs: object) -> str:
        raise RuntimeError(self.message)


class _LoggingTool(CodeMieTool):
    script_callable = True
    name: str = "logging_tool"
    description: str = "logs the sentinel inside execute"
    args_schema: type[BaseModel] = _Args

    def execute(self, **kwargs: object) -> str:
        _tool_logger.debug("response body %s", SENTINEL)
        return "done"


class _ForbiddenPathsTool(CodeMieTool):
    """Any path other than run_for_script is a test failure (confirmation lives on those paths)."""

    script_callable = True

    name: str = "forbidden_paths"
    description: str = "must be reached via run_for_script only"
    args_schema: type[BaseModel] = _Args

    def execute(self, **kwargs: object) -> str:
        return "ran"

    def _run(self, *args: object, **kwargs: object) -> str:
        raise AssertionError("_run must not be called")

    def run(self, *args: object, **kwargs: object) -> str:  # type: ignore[override]
        raise AssertionError("run must not be called")

    def invoke(self, *args: object, **kwargs: object) -> str:  # type: ignore[override]
        raise AssertionError("invoke must not be called")


class _PlainTool(BaseTool):
    name: str = "plain"
    description: str = "not a CodeMieTool"

    def _run(self, *args: object, **kwargs: object) -> str:
        raise AssertionError("_run must not be called")


class _CapturingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture
def logs() -> Iterator[_CapturingHandler]:
    handler = _CapturingHandler()
    handler.addFilter(ScriptCallLogFilter())
    watched = [app_logger, _tool_logger]
    saved = [(item, item.level, item.propagate) for item in watched]
    for item in watched:
        item.setLevel(logging.DEBUG)
        item.propagate = False
        item.addHandler(handler)
    try:
        yield handler
    finally:
        for item, level, propagate in saved:
            item.removeHandler(handler)
            item.setLevel(level)
            item.propagate = propagate


def _handler(
    *tools: BaseTool, gate: ToolRunGate | None = None, max_payload_bytes: int | None = None
) -> ToolCallHandler:
    context = ScriptRunContext(
        user=User(id="user-1", username="u1"),
        scope=ToolListScope(tools),
        scope_kind=ScriptScopeKind.ASSISTANT,
    )
    options: dict[str, object] = {"gate": gate or ToolRunGate(100)}
    if max_payload_bytes is not None:
        options["max_payload_bytes"] = max_payload_bytes
    handlers = build_tool_call_handlers(context, **options)  # type: ignore[arg-type]
    assert set(handlers) == {TOOL_CALL_OP}
    return handlers[TOOL_CALL_OP]


def _refusal(handler: ToolCallHandler, payload: Mapping[str, object]) -> ToolCallRefused:
    with pytest.raises(ToolCallRefused) as info:
        handler(payload)
    return info.value


def test_op_name() -> None:
    assert TOOL_CALL_OP == "tool.call"


class _BigHttpTool(CodeMieTool):
    script_callable = True
    name: str = "big_http"
    description: str = "returns an HttpResult with a large body"
    args_schema: type[BaseModel] = _Args

    def execute(self, **kwargs: object) -> HttpResult:
        return HttpResult("GET", "https://example.test/x", 200, "OK", '{"data": "' + "x" * 500 + '"}', "spaced")


def test_an_http_body_over_the_cap_is_refused_without_being_parsed() -> None:
    with patch("codemie_tools.base.http_result.json.loads") as parse:
        refused = _refusal(_handler(_BigHttpTool(), max_payload_bytes=100), {"name": "big_http", "args": {"text": "x"}})

    assert refused.code == "payload_too_large"
    assert "big_http" in refused.message
    assert "100" in refused.message
    parse.assert_not_called()


def test_an_http_body_within_the_cap_is_parsed_and_returned() -> None:
    result = _handler(_HttpTool(), max_payload_bytes=1000)({"name": "http_tool", "args": {"text": "hi"}})

    assert result == {"result": {"missing": True}, "http": {"status": 404, "reason": None}}


def test_the_tool_runs_while_holding_the_gate() -> None:
    gate = ToolRunGate(1)
    held: list[bool] = []

    class _Probe(_CountingTool):
        def execute(self, **kwargs: object) -> str:
            held.append(not gate._semaphore.acquire(blocking=False))
            return "ran"

    _handler(_Probe(), gate=gate)({"name": "counting", "args": {"text": "hi"}})

    assert held == [True], "the semaphore is taken around the tool run"
    assert gate._semaphore.acquire(blocking=False), "and released after it"


def test_a_failing_tool_releases_the_gate() -> None:
    gate = ToolRunGate(1)

    _refusal(_handler(_RaisingTool(), gate=gate), {"name": "raising", "args": {"text": "hi"}})

    assert gate._semaphore.acquire(blocking=False)


def test_validation_and_authorization_do_not_take_the_gate() -> None:
    gate = ToolRunGate(1)
    assert gate._semaphore.acquire(blocking=False)  # the only place is taken

    handler = _handler(_EchoArgsTool(), gate=gate)

    assert _refusal(handler, {"name": "unknown", "args": {}}).code == "tool_unavailable"
    assert _refusal(handler, {"name": "echo_args", "args": {"count": "bad"}}).code == "bad_arguments"


def test_the_process_gate_is_used_when_none_is_given() -> None:
    gate = ToolRunGate(1)
    context = ScriptRunContext(
        user=User(id="user-1", username="u1"),
        scope=ToolListScope([_CountingTool()]),
        scope_kind=ScriptScopeKind.ASSISTANT,
    )

    with patch("codemie.service.script_tool_calls.handler.process_gate", return_value=gate) as process_gate:
        handler = build_tool_call_handlers(context)[TOOL_CALL_OP]

    handler({"name": "counting", "args": {"text": "hi"}})
    process_gate.assert_called_once_with()


def test_success_gives_result_only_without_http_block() -> None:
    result = _handler(_EchoArgsTool())({"name": "echo_args", "args": {"text": "hi"}})

    assert result == {"result": {"received": {"text": "hi"}}}
    json.dumps(result)


def test_only_provided_fields_reach_the_tool_with_coerced_values() -> None:
    result = _handler(_EchoArgsTool())({"name": "echo_args", "args": {"text": "hi", "count": "3"}})

    assert result == {"result": {"received": {"text": "hi", "count": 3}}}


def test_tool_without_args_schema_refuses_any_argument_by_name() -> None:
    refused = _refusal(_handler(_NoArgsTool()), {"name": "no_args", "args": {"x": SENTINEL}})

    assert refused.code == "bad_arguments"
    assert "x" in refused.message
    assert SENTINEL not in refused.message


def test_tool_with_non_pydantic_args_schema_is_unavailable() -> None:
    tool = _NoArgsTool(args_schema={"type": "object", "properties": {}})

    refused = _refusal(_handler(tool), {"name": "no_args", "args": {}})

    assert refused.code == "tool_unavailable"


def test_unknown_argument_name_is_bad_arguments_and_the_tool_does_not_run() -> None:
    tool = _CountingTool()

    refused = _refusal(_handler(tool), {"name": "counting", "args": {"text": "hi", "cuont": SENTINEL}})

    assert refused.code == "bad_arguments"
    assert "cuont" in refused.message
    assert "extra_forbidden" in refused.message
    assert SENTINEL not in refused.message
    assert tool.calls == 0


def test_field_alias_is_accepted_as_an_argument_name() -> None:
    result = _handler(_AliasTool())({"name": "alias_tool", "args": {"jql": "project = X"}})

    assert result == {"result": {"received": {"query": "project = X"}}}


def test_schema_that_allows_extras_keeps_them() -> None:
    result = _handler(_ExtraTool())({"name": "extra_tool", "args": {"text": "hi", "more": 2}})

    assert result == {"result": {"received": {"text": "hi", "more": 2}}}


def test_missing_args_means_no_arguments() -> None:
    result = _handler(_NoArgsTool())({"name": "no_args"})

    assert result == {"result": {"a": 1}}


def test_http_result_adds_http_block_and_keeps_non_2xx_as_data() -> None:
    result = _handler(_HttpTool())({"name": "http_tool", "args": {"text": "hi"}})

    assert result == {"result": {"missing": True}, "http": {"status": 404, "reason": None}}
    json.dumps(result)


@pytest.mark.parametrize(
    ("payload", "expected_fragment"),
    [
        ({"args": {}}, "'name'"),
        ({"name": 5, "args": {}}, "'name'"),
        ({"name": "", "args": {}}, "'name'"),
        ({"name": "echo_args", "args": ["x"]}, "'args'"),
        ({"name": "echo_args", "args": "text"}, "'args'"),
    ],
)
def test_malformed_payload_is_bad_arguments_naming_the_field(
    payload: Mapping[str, object], expected_fragment: str
) -> None:
    refused = _refusal(_handler(_EchoArgsTool()), payload)

    assert refused.code == "bad_arguments"
    assert expected_fragment in refused.message


def test_malformed_payload_message_never_carries_the_value() -> None:
    refused = _refusal(_handler(_EchoArgsTool()), {"name": "echo_args", "args": SENTINEL})

    assert SENTINEL not in refused.message


def test_schema_errors_name_fields_and_kinds_but_never_values() -> None:
    refused = _refusal(
        _handler(_EchoArgsTool()),
        {"name": "echo_args", "args": {"count": SENTINEL, "tags": SENTINEL}},
    )

    assert refused.code == "bad_arguments"
    assert "text" in refused.message
    assert "count" in refused.message
    assert "tags" in refused.message
    assert "missing" in refused.message
    assert "int_parsing" in refused.message
    assert SENTINEL not in refused.message


def test_authorization_refusal_passes_through_unchanged() -> None:
    refused = _refusal(_handler(_EchoArgsTool()), {"name": "unknown_tool", "args": {}})

    assert refused.code == "tool_unavailable"


def test_authorization_runs_before_argument_validation() -> None:
    refused = _refusal(_handler(_EchoArgsTool()), {"name": "unknown_tool", "args": {"count": "bad"}})

    assert refused.code == "tool_unavailable"


def test_raising_tool_gives_tool_failed_with_capped_sanitized_cause() -> None:
    tool = _RaisingTool(message="line one\nline two \x00password: hunter2 " + "x" * 1000)

    refused = _refusal(_handler(tool), {"name": "raising", "args": {"text": "hi"}})

    assert refused.code == "tool_failed"
    assert "RuntimeError" in refused.message
    assert "line one line two" in refused.message
    assert "\n" not in refused.message
    assert "\x00" not in refused.message
    assert "hunter2" not in refused.message
    cause = refused.message.split("): ", 1)[1]
    assert len(cause) <= 300


def test_tool_failed_with_empty_exception_message_still_names_the_type() -> None:
    refused = _refusal(_handler(_RaisingTool(message="")), {"name": "raising", "args": {"text": "hi"}})

    assert refused.code == "tool_failed"
    assert "RuntimeError" in refused.message


def test_only_run_for_script_is_used_so_confirmation_is_never_reached() -> None:
    result = _handler(_ForbiddenPathsTool())({"name": "forbidden_paths", "args": {"text": "hi"}})

    assert result == {"result": "ran"}


def test_tool_without_run_for_script_is_unavailable() -> None:
    refused = _refusal(_handler(_PlainTool()), {"name": "plain", "args": {}})

    assert refused.code == "tool_unavailable"


def test_records_logged_inside_execute_are_dropped_by_the_guard(logs: _CapturingHandler) -> None:
    _tool_logger.debug("before %s", "call")

    _handler(_LoggingTool())({"name": "logging_tool", "args": {"text": "hi"}})

    assert logs.messages == ["before call"]
    assert not any(SENTINEL in message for message in logs.messages)


def test_handler_log_line_holds_only_tool_name_code_and_exception_type(logs: _CapturingHandler) -> None:
    tool = _RaisingTool(message=SENTINEL)

    _refusal(_handler(tool), {"name": "raising", "args": {"text": SENTINEL}})

    assert len(logs.messages) == 1
    line = logs.messages[0]
    assert "raising" in line
    assert "tool_failed" in line
    assert "RuntimeError" in line
    assert SENTINEL not in line


def test_bad_arguments_log_line_never_holds_values(logs: _CapturingHandler) -> None:
    _refusal(_handler(_EchoArgsTool()), {"name": "echo_args", "args": {"count": SENTINEL}})

    assert all(SENTINEL not in message for message in logs.messages)
