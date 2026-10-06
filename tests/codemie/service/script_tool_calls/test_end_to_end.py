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

"""End-to-end unit checks of ``tool.call`` through the real channel.

A real SDK client (loaded by path, as the sandbox launcher does) calls a fake assistant-scope tool through a real
``ToolCallChannel`` whose handlers come from ``build_tool_call_handlers`` over a ``ScriptToolRegistry`` context.
Only the pod exec is replaced by ``LocalExecRunner``.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import BaseModel

from codemie.configs.script_call_log_guard import ScriptCallLogFilter, install_script_call_log_guard
from codemie.rest_api.security.user import User
from codemie.service.script_tool_calls.concurrency import ToolRunGate
from codemie.service.script_tool_calls.context import ScriptToolRegistry
from codemie.service.script_tool_calls.handler import build_tool_call_handlers
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import (
    MAX_PAYLOAD_BYTES,
    PROTOCOL_VERSION,
)
from codemie_tools.data_management.code_executor.tool_call_channel import (
    ToolCallChannel,
    exchange_dir_path,
    new_exchange_dir_name,
)
from codemie_tools.data_management.code_executor.tool_call_protocol import RequestDispatcher
from tests.codemie_tools.data_management.code_executor.test_tool_call_channel import (
    LocalExecRunner,
    _load_sdk,
)

ARGS_SENTINEL = "SENTINEL-ARGS-7f3a"
RESULT_SENTINEL = "SENTINEL-RESULT-91bc"
TOOL_LOG_SENTINEL = "SENTINEL-TOOLLOG-c40d"
FAILURE_SENTINEL = "SENTINEL-FAILURE-52e8"

CALL_TIMEOUT_SECONDS: float = 10.0
PROMPT_SECONDS: float = 8.0
_ID_LENGTH: int = 32
_tool_logger = logging.getLogger("codemie.test_end_to_end.tool")


class _Args(BaseModel):
    text: str = ""


class _FakeTool(CodeMieTool):
    """Assistant-scope tool whose ``execute`` returns configurable text, may log a sentinel and may raise."""

    script_callable = True

    name: str = "fake_tool"
    description: str = "returns configurable text"
    args_schema: type[BaseModel] = _Args
    reply: str = "ok"
    log_sentinel: str | None = None
    failure: str | None = None
    seen: list[str] = []

    def execute(self, **kwargs: object) -> str:
        self.seen.append(str(kwargs.get("text", "")))
        if self.log_sentinel is not None:
            _tool_logger.debug("tool internals %s args=%s", self.log_sentinel, kwargs)
        if self.failure is not None:
            raise RuntimeError(self.failure)
        return self.reply


@pytest.fixture
def sdk() -> ModuleType:
    return _load_sdk()


@contextmanager
def _serving(workspace: Path, sdk: ModuleType, tool: CodeMieTool, max_parallel_calls: int = 1) -> Iterator[None]:
    """Fill a registry with ``tool``, start a real channel on its handlers, point the SDK at it."""
    registry = ScriptToolRegistry(User(id="user-1", username="u1"))
    registry.fill([tool])
    handlers = build_tool_call_handlers(registry.context(), gate=ToolRunGate(10))
    exchange_dir = exchange_dir_path(new_exchange_dir_name())
    (workspace / exchange_dir).mkdir(parents=True)
    sdk._configure({"exchange_dir": str(workspace / exchange_dir)})
    channel = ToolCallChannel(
        LocalExecRunner(workspace),
        exchange_dir,
        dispatcher=RequestDispatcher(handlers),
        max_parallel_calls=max_parallel_calls,
        poll_interval=0.02,
        backoff_seconds=0.01,
    )
    channel.start()
    try:
        yield
    finally:
        channel.stop()


def _call(sdk: ModuleType, name: str = "fake_tool", **args: object) -> object:
    return sdk.call("tool.call", {"name": name, "args": args}, timeout=CALL_TIMEOUT_SECONDS)


def _encoded_size(text: str) -> int:
    """Byte size of the channel's response for a tool returning ``text`` (same encoding the channel uses)."""
    response = {"v": PROTOCOL_VERSION, "id": "0" * _ID_LENGTH, "ok": True, "result": {"result": text}}
    return len(json.dumps(response, ensure_ascii=False).encode("utf-8"))


def _text_for_encoded_size(unit: str, target: int) -> str:
    """Smallest ``unit``-repeated text whose response is at least ``target`` bytes."""
    overhead = _encoded_size("")
    unit_bytes = len(unit.encode("utf-8"))
    count = -(-(target - overhead) // unit_bytes)
    return unit * count


def test_result_above_model_token_limit_and_under_cap_arrives_whole(tmp_path: Path, sdk: ModuleType) -> None:
    tool = _FakeTool(reply="word " * 40_000)
    _, token_count = tool._limit_output_content(tool.reply)
    assert token_count > tool.tokens_size_limit  # the model path would truncate this
    assert _encoded_size(tool.reply) < MAX_PAYLOAD_BYTES

    with _serving(tmp_path, sdk, tool):
        result = _call(sdk)

    assert result == {"result": tool.reply}


def test_result_exactly_at_the_cap_arrives_whole(tmp_path: Path, sdk: ModuleType) -> None:
    text = _text_for_encoded_size("a", MAX_PAYLOAD_BYTES)
    assert _encoded_size(text) == MAX_PAYLOAD_BYTES

    with _serving(tmp_path, sdk, _FakeTool(reply=text)):
        result = _call(sdk)

    assert result == {"result": text}


@pytest.mark.parametrize(
    ("unit", "label"),
    [("a", "ascii"), ("€", "three-byte"), ("中", "cjk"), ("\U0001f600", "four-byte")],
)
def test_result_just_above_the_cap_is_refused_promptly(tmp_path: Path, sdk: ModuleType, unit: str, label: str) -> None:
    text = _text_for_encoded_size(unit, MAX_PAYLOAD_BYTES + 1)
    assert _encoded_size(text) > MAX_PAYLOAD_BYTES, label
    if unit != "a":
        assert len(text) < MAX_PAYLOAD_BYTES, label  # under the cap in characters, over it in bytes

    with _serving(tmp_path, sdk, _FakeTool(reply=text)):
        started = time.monotonic()
        with pytest.raises(sdk.ToolCallError) as excinfo:
            _call(sdk)
        elapsed = time.monotonic() - started

    assert excinfo.value.code == "payload_too_large"
    assert elapsed < PROMPT_SECONDS


def test_channel_keeps_serving_after_an_oversize_result(tmp_path: Path, sdk: ModuleType) -> None:
    tool = _FakeTool(reply=_text_for_encoded_size("a", MAX_PAYLOAD_BYTES + 1))

    with _serving(tmp_path, sdk, tool):
        with pytest.raises(sdk.ToolCallError):
            _call(sdk)
        tool.reply = "small"
        assert _call(sdk) == {"result": "small"}


def test_sequential_calls_each_get_their_own_result(tmp_path: Path, sdk: ModuleType) -> None:
    tool = _FakeTool()

    with _serving(tmp_path, sdk, tool):
        tool.reply = "first"
        first = _call(sdk, text="one")
        tool.reply = "second"
        second = _call(sdk, text="two")

    assert first == {"result": "first"}
    assert second == {"result": "second"}
    assert tool.seen == ["one", "two"]


@pytest.fixture
def captured_records() -> Iterator[list[str]]:
    """Capture every record any logger or the root would emit at DEBUG, behind the production script-call filter."""
    install_script_call_log_guard()
    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(f"{record.name}|{record.getMessage()}|{record.exc_text or ''}|{record.args!r}")

    capture = _Capture(level=logging.DEBUG)
    capture.addFilter(ScriptCallLogFilter())
    targets: list[logging.Logger] = [logging.getLogger()]
    targets.extend(
        candidate
        for candidate in list(logging.root.manager.loggerDict.values())
        if isinstance(candidate, logging.Logger)
    )
    saved = [(target, target.level) for target in targets]
    for target in targets:
        target.setLevel(logging.DEBUG)
        target.addHandler(capture)
    try:
        yield records
    finally:
        for target, level in saved:
            target.removeHandler(capture)
            target.setLevel(level)


def _assert_no_sentinels(records: list[str]) -> None:
    joined = "\n".join(records)
    for sentinel in (ARGS_SENTINEL, RESULT_SENTINEL, TOOL_LOG_SENTINEL, FAILURE_SENTINEL):
        assert sentinel not in joined, sentinel


def test_debug_logs_carry_no_sentinel_on_success(tmp_path: Path, sdk: ModuleType, captured_records: list[str]) -> None:
    tool = _FakeTool(reply=f"payload {RESULT_SENTINEL}", log_sentinel=TOOL_LOG_SENTINEL)

    with _serving(tmp_path, sdk, tool):
        result = _call(sdk, text=ARGS_SENTINEL)

    assert result == {"result": f"payload {RESULT_SENTINEL}"}
    assert tool.seen == [ARGS_SENTINEL]
    _assert_no_sentinels(captured_records)


def test_debug_logs_carry_no_sentinel_on_tool_failure(
    tmp_path: Path, sdk: ModuleType, captured_records: list[str]
) -> None:
    tool = _FakeTool(log_sentinel=TOOL_LOG_SENTINEL, failure=f"upstream said {FAILURE_SENTINEL}")

    with _serving(tmp_path, sdk, tool):
        with pytest.raises(sdk.ToolCallError) as excinfo:
            _call(sdk, text=ARGS_SENTINEL)

    assert excinfo.value.code == "tool_failed"
    _assert_no_sentinels(captured_records)


def test_debug_logs_carry_no_sentinel_on_oversize(tmp_path: Path, sdk: ModuleType, captured_records: list[str]) -> None:
    text = RESULT_SENTINEL + _text_for_encoded_size("a", MAX_PAYLOAD_BYTES + 1)
    tool = _FakeTool(reply=text, log_sentinel=TOOL_LOG_SENTINEL)

    with _serving(tmp_path, sdk, tool):
        with pytest.raises(sdk.ToolCallError) as excinfo:
            _call(sdk, text=ARGS_SENTINEL)

    assert excinfo.value.code == "payload_too_large"
    _assert_no_sentinels(captured_records)


def test_call_tools_runs_a_batch_of_independent_calls_through_the_real_handler(tmp_path: Path, sdk: ModuleType) -> None:
    tool = _FakeTool(reply="same")
    _FakeTool.seen = []

    with _serving(tmp_path, sdk, tool, max_parallel_calls=3):
        results = sdk.call_tools(
            [
                {"name": "fake_tool", "args": {"text": "a"}},
                {"name": "missing_tool"},
                {"name": "fake_tool", "args": {"text": "b"}},
            ],
            timeout=CALL_TIMEOUT_SECONDS,
        )

    assert results[0] == {"result": "same"}
    assert results[2] == {"result": "same"}
    assert isinstance(results[1], sdk.ToolCallError)
    assert results[1].code == "tool_unavailable"
    assert sorted(tool.seen) == ["a", "b"]


def test_a_batch_result_over_the_cap_is_an_error_item_and_the_other_items_still_arrive(
    tmp_path: Path, sdk: ModuleType
) -> None:
    class _SizedTool(_FakeTool):
        name: str = "sized_tool"

        def execute(self, **kwargs: object) -> str:
            return "x" * (MAX_PAYLOAD_BYTES + 10) if kwargs.get("text") == "big" else "small"

    with _serving(tmp_path, sdk, _SizedTool(), max_parallel_calls=2):
        results = sdk.call_tools(
            [{"name": "sized_tool", "args": {"text": "big"}}, {"name": "sized_tool", "args": {"text": "ok"}}],
            timeout=CALL_TIMEOUT_SECONDS,
        )

    assert isinstance(results[0], sdk.ToolCallError)
    assert results[0].code == "payload_too_large"
    assert results[1] == {"result": "small"}


def test_a_guarded_script_runs_call_tools_end_to_end_in_a_subprocess(tmp_path: Path, sdk: ModuleType) -> None:
    """The real wrapper (sandbox guard + bootstrap + SDK) in a separate process, served by a real channel."""
    import subprocess
    import sys

    from codemie_tools.data_management.code_executor.sandbox_guard import build_guarded_workspace_script
    from codemie_tools.data_management.code_executor.tool_calling_limits import ToolCallingSettings

    exchange_dir = exchange_dir_path(new_exchange_dir_name())
    script = (
        "from codemie_runtime_sdk import call_tool, call_tools, ToolCallError\n"
        "single = call_tool('fake_tool', {'text': 'one'})\n"
        "batch = call_tools([{'name': 'fake_tool', 'args': {'text': 'a'}}, {'name': 'missing'}, {'name': 'fake_tool', 'args': {'text': 'b'}}])\n"
        "print(single['result'])\n"
        "print([item.code if isinstance(item, ToolCallError) else item['result'] for item in batch])\n"
    )
    (tmp_path / "script.py").write_text(script, encoding="utf-8")
    settings = ToolCallingSettings(run_timeout_seconds=60.0)
    wrapper = build_guarded_workspace_script(
        "script.py", workspace_root=str(tmp_path), sdk_config=settings.sdk_config(exchange_dir, 30.0)
    )
    registry = ScriptToolRegistry(User(id="user-1", username="u1"))
    registry.fill([_FakeTool(reply="served")])
    dispatcher = RequestDispatcher(build_tool_call_handlers(registry.context(), gate=ToolRunGate(10)))
    channel = ToolCallChannel(
        LocalExecRunner(tmp_path),
        exchange_dir,
        dispatcher=dispatcher,
        max_parallel_calls=3,
        poll_interval=0.02,
        backoff_seconds=0.01,
    )
    channel.start()
    try:
        completed = subprocess.run(
            [sys.executable, "-c", wrapper], cwd=tmp_path, text=True, capture_output=True, timeout=60, check=False
        )
    finally:
        channel.stop()

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == ["served", "['served', 'tool_unavailable', 'served']"]
