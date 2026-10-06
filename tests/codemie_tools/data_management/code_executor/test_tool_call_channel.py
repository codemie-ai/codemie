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

"""Tests for the backend side channel that answers runtime SDK tool calls.

The stand-in ``ExecRunner`` runs exactly the argv the channel builds, locally, with the temporary workspace root
as the working directory -- the same contract the Kubernetes runner honours on the pod.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from unittest import mock

import pytest

from codemie_tools.data_management.code_executor import exec_runner, tool_call_channel
from codemie_tools.data_management.code_executor.exec_runner import ExecResult
from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import (
    BRIDGE_DIR_NAME,
    FILE_SUFFIX,
    MAX_PAYLOAD_BYTES,
    PROTOCOL_VERSION,
    REQ_PREFIX,
    RESP_PREFIX,
    UNAVAILABLE_MARKER_NAME,
)
from codemie_tools.data_management.code_executor.tool_call_channel import (
    WRITE_RESPONSE_SCRIPT,
    ToolCallChannel,
    exchange_dir_path,
    new_exchange_dir_name,
)
from codemie_tools.data_management.code_executor.tool_call_protocol import (
    DEADLINE_MARGIN_SECONDS,
    RequestDispatcher,
    ToolCallHandler,
    ToolCallRefused,
)

SDK_PATH: Path = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "codemie_tools"
    / "data_management"
    / "code_executor"
    / "runtime_sdk"
    / "codemie_runtime_sdk.py"
)


def _load_sdk() -> ModuleType:
    """Load a fresh copy of the SDK by path, as the sandbox launcher does."""
    spec = importlib.util.spec_from_file_location("codemie_runtime_sdk_channel_test", SDK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LocalExecRunner:
    """Runs the channel's argv locally, rooted at ``cwd``; ``python3`` maps to the test interpreter.

    Mirrors the Kubernetes v4 exec protocol: stdin is written but never half-closed, so a command that waits for
    end of input hangs here exactly as it does on a real exec, and is reported as a failed exec after ``deadline``.
    """

    def __init__(self, cwd: Path, deadline: float = 5.0) -> None:
        self.cwd: Path = cwd
        self.deadline: float = deadline
        self.calls: list[Sequence[str]] = []

    def __call__(self, argv: Sequence[str], stdin: bytes | None = None) -> ExecResult:
        self.calls.append(list(argv))
        command = [sys.executable if argv[0] == "python3" else argv[0], *argv[1:]]
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            process = subprocess.Popen(
                command,
                cwd=self.cwd,
                stdin=subprocess.DEVNULL if stdin is None else subprocess.PIPE,
                stdout=out,
                stderr=err,
            )
            try:
                if stdin is not None and process.stdin is not None:
                    process.stdin.write(stdin)
                    process.stdin.flush()  # deliberately no close(): there is no stdin EOF on a k8s exec
                try:
                    exit_code = process.wait(timeout=self.deadline)
                except subprocess.TimeoutExpired as exc:
                    process.kill()
                    process.wait(timeout=10)
                    raise TimeoutError("exec did not finish: command is waiting for stdin EOF?") from exc
            finally:
                if process.stdin is not None:
                    process.stdin.close()
            out.seek(0)
            err.seek(0)
            return ExecResult(
                stdout=out.read().decode("utf-8", errors="replace"),
                stderr=err.read().decode("utf-8", errors="replace"),
                exit_code=exit_code,
            )


class RecordingExecRunner:
    """Records argv and reports success without touching anything."""

    def __init__(self) -> None:
        self.calls: list[Sequence[str]] = []

    def __call__(self, argv: Sequence[str], stdin: bytes | None = None) -> ExecResult:
        del stdin
        self.calls.append(list(argv))
        return ExecResult(stdout="[]", stderr="", exit_code=0)


class FlakyExecRunner:
    """Fails the first ``failures`` invocations, then delegates to a real local runner."""

    def __init__(self, inner: LocalExecRunner, failures: int) -> None:
        self.inner: LocalExecRunner = inner
        self.failures: int = failures
        self.attempts: int = 0

    def __call__(self, argv: Sequence[str], stdin: bytes | None = None) -> ExecResult:
        self.attempts += 1
        if self.attempts <= self.failures:
            raise OSError("exec websocket broke")
        return self.inner(argv, stdin)


@pytest.fixture
def sdk() -> ModuleType:
    return _load_sdk()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


def _prepare(workspace: Path, sdk: ModuleType) -> str:
    """Create an exchange folder, point the SDK at it, and return its workspace-relative path."""
    exchange_dir = exchange_dir_path(new_exchange_dir_name())
    (workspace / exchange_dir).mkdir(parents=True)
    sdk._configure({"exchange_dir": str(workspace / exchange_dir)})
    return exchange_dir


def _echo(payload: Mapping[str, object]) -> object:
    """Test-local handler: return the caller's payload unchanged."""
    return dict(payload)


def _channel(runner: object, exchange_dir: str, **kwargs: object) -> ToolCallChannel:
    """A channel on fast timings. ``handlers``, ``deadline_margin`` and ``max_payload_bytes`` go to the dispatcher,
    which is given the clock of the channel's ``deadline`` (as the Job bridge does); the rest goes to the channel."""
    handlers = kwargs.pop("handlers", {"echo": _echo})
    margin = kwargs.pop("deadline_margin", DEADLINE_MARGIN_SECONDS)
    max_payload_bytes = kwargs.pop("max_payload_bytes", MAX_PAYLOAD_BYTES)
    options: dict[str, object] = {"poll_interval": 0.02, "backoff_seconds": 0.01}
    options.update(kwargs)
    deadline = options.get("deadline")
    dispatcher = RequestDispatcher(
        handlers,  # type: ignore[arg-type]
        max_payload_bytes=max_payload_bytes,  # type: ignore[arg-type]
        time_left=None if deadline is None else (lambda: deadline - time.monotonic()),  # type: ignore[operator]
        deadline_margin=margin,  # type: ignore[arg-type]
    )
    return ToolCallChannel(runner, exchange_dir, dispatcher=dispatcher, **options)  # type: ignore[arg-type]


@pytest.fixture
def running_channel(workspace: Path, sdk: ModuleType) -> Iterator[tuple[ToolCallChannel, str]]:
    exchange_dir = _prepare(workspace, sdk)
    channel = _channel(LocalExecRunner(workspace), exchange_dir)
    channel.start()
    try:
        yield channel, exchange_dir
    finally:
        channel.stop()


def _wait_for_response(workspace: Path, exchange_dir: str, call_id: str, timeout: float = 5.0) -> Mapping[str, object]:
    path = workspace / exchange_dir / f"{RESP_PREFIX}{call_id}{FILE_SUFFIX}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            parsed: object = json.loads(path.read_text(encoding="utf-8"))
            assert isinstance(parsed, dict)
            return parsed
        time.sleep(0.02)
    raise AssertionError(f"no response for {call_id} within {timeout}s")


def _write_raw_request(workspace: Path, exchange_dir: str, body: bytes) -> str:
    """Drop a request file straight into the folder, bypassing the SDK's own guards."""
    call_id = uuid.uuid4().hex
    directory = workspace / exchange_dir
    tmp = directory / f"tmp.{call_id}"
    tmp.write_bytes(body)
    tmp.replace(directory / f"{REQ_PREFIX}{call_id}{FILE_SUFFIX}")
    return call_id


class TestProtocol:
    def test_echo_returns_the_payload(self, running_channel: tuple[ToolCallChannel, str], sdk: ModuleType) -> None:
        del running_channel

        assert sdk.call("echo", {"greeting": "hello", "n": 3}) == {"greeting": "hello", "n": 3}

    def test_sequential_calls_each_get_their_own_result(
        self, running_channel: tuple[ToolCallChannel, str], sdk: ModuleType
    ) -> None:
        del running_channel

        assert sdk.call("echo", {"i": 1}) == {"i": 1}
        assert sdk.call("echo", {"i": 2}) == {"i": 2}
        assert sdk.call("echo", {"i": 3}) == {"i": 3}

    def test_request_file_is_removed_once_answered(
        self, workspace: Path, running_channel: tuple[ToolCallChannel, str], sdk: ModuleType
    ) -> None:
        _, exchange_dir = running_channel

        sdk.call("echo", {"i": 1})

        assert list((workspace / exchange_dir).glob(f"{REQ_PREFIX}*{FILE_SUFFIX}")) == []

    def test_unknown_op_is_reported_to_the_script(
        self, running_channel: tuple[ToolCallChannel, str], sdk: ModuleType
    ) -> None:
        del running_channel

        with pytest.raises(sdk.ToolCallError) as excinfo:
            sdk.call("delete_everything", {}, timeout=15.0)

        assert excinfo.value.code == "unknown_op"

    def test_malformed_request_is_answered_with_bad_request(
        self, workspace: Path, running_channel: tuple[ToolCallChannel, str]
    ) -> None:
        _, exchange_dir = running_channel
        call_id = _write_raw_request(workspace, exchange_dir, b"{not json")

        response = _wait_for_response(workspace, exchange_dir, call_id)

        assert response["ok"] is False
        assert response["error"] == {"code": "bad_request", "message": "request is not valid JSON"}

    def test_a_hand_crafted_request_id_stays_data(
        self, workspace: Path, running_channel: tuple[ToolCallChannel, str], sdk: ModuleType
    ) -> None:
        """A script owns the exchange folder, so it can name a request file anything a filename may contain."""
        _, exchange_dir = running_channel
        hostile_id = "$(touch pwned)`touch pwned2`;touch pwned3"
        planted = workspace / exchange_dir / f"{REQ_PREFIX}{hostile_id}{FILE_SUFFIX}"
        planted.write_bytes(json.dumps({"v": PROTOCOL_VERSION, "id": hostile_id, "op": "echo", "payload": {}}).encode())

        assert sdk.call("echo", {"still": "served"}) == {"still": "served"}
        assert (workspace / exchange_dir / f"{RESP_PREFIX}{hostile_id}{FILE_SUFFIX}").exists()
        assert not planted.exists()
        assert list(workspace.glob("pwned*")) == [], "request file names must never reach a shell as code"

    def test_oversize_request_is_answered_with_payload_too_large(
        self, workspace: Path, running_channel: tuple[ToolCallChannel, str]
    ) -> None:
        _, exchange_dir = running_channel
        body = json.dumps(
            {"v": PROTOCOL_VERSION, "id": "x", "op": "echo", "payload": {"blob": "z" * (MAX_PAYLOAD_BYTES + 64)}}
        ).encode("utf-8")
        assert len(body) > MAX_PAYLOAD_BYTES
        call_id = _write_raw_request(workspace, exchange_dir, body)

        response = _wait_for_response(workspace, exchange_dir, call_id)

        assert response["ok"] is False
        assert isinstance(response["error"], dict)
        assert response["error"]["code"] == "payload_too_large"

    def test_oversize_result_is_answered_with_payload_too_large(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        handlers: Mapping[str, object] = {"bloat": lambda payload: "z" * (MAX_PAYLOAD_BYTES + 64)}
        channel = _channel(LocalExecRunner(workspace), exchange_dir, handlers=handlers)
        channel.start()
        try:
            with pytest.raises(sdk.ToolCallError) as excinfo:
                sdk.call("bloat", {}, timeout=15.0)
        finally:
            channel.stop()

        assert excinfo.value.code == "payload_too_large"

    def test_oversize_result_message_states_encoded_size_and_limit_in_bytes(
        self, workspace: Path, sdk: ModuleType
    ) -> None:
        exchange_dir = _prepare(workspace, sdk)
        # 3-byte characters: the encoded size differs from the character count.
        text = "\u20ac" * (MAX_PAYLOAD_BYTES // 3 + 10)
        handlers: Mapping[str, ToolCallHandler] = {"bloat": lambda payload: text}
        channel = _channel(LocalExecRunner(workspace), exchange_dir, handlers=handlers)
        channel.start()
        try:
            with pytest.raises(sdk.ToolCallError) as excinfo:
                sdk.call("bloat", {}, timeout=15.0)
        finally:
            channel.stop()

        encoded_size = len(
            json.dumps({"v": PROTOCOL_VERSION, "id": "x" * 32, "ok": True, "result": text}, ensure_ascii=False).encode(
                "utf-8"
            )
        )
        assert excinfo.value.code == "payload_too_large"
        assert f"{encoded_size} bytes" in str(excinfo.value)
        assert f"{MAX_PAYLOAD_BYTES} byte limit" in str(excinfo.value)

    def test_non_ascii_result_under_the_cap_arrives_intact(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        text = "caf\u00e9 \u20ac \u4e2d\u6587 \U0001f600"
        handlers: Mapping[str, ToolCallHandler] = {"text": lambda payload: {"text": text}}
        channel = _channel(LocalExecRunner(workspace), exchange_dir, handlers=handlers)
        channel.start()
        try:
            result = sdk.call("text", {}, timeout=15.0)
        finally:
            channel.stop()

        assert result == {"text": text}

    def test_refused_handler_gives_the_script_its_code_and_message(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)

        def refuse(payload: Mapping[str, object]) -> object:
            raise ToolCallRefused("tool_blocked", "m")

        handlers: Mapping[str, ToolCallHandler] = {"refuse": refuse, "echo": _echo}
        channel = _channel(LocalExecRunner(workspace), exchange_dir, handlers=handlers)
        channel.start()
        try:
            with pytest.raises(sdk.ToolCallError) as excinfo:
                sdk.call("refuse", {}, timeout=15.0)
            assert sdk.call("echo", {"after": "refusal"}, timeout=15.0) == {"after": "refusal"}
        finally:
            channel.stop()

        assert excinfo.value.code == "tool_blocked"
        assert str(excinfo.value) == "m"

    def test_request_with_invalid_utf8_is_rejected_not_repaired(
        self, workspace: Path, running_channel: tuple[ToolCallChannel, str]
    ) -> None:
        """Bytes that are not UTF-8 must not be replaced by U+FFFD and then acted upon."""
        _, exchange_dir = running_channel
        body = b'{"v": 1, "id": "x", "op": "echo", "payload": {"text": "caf\xe9"}}'
        call_id = _write_raw_request(workspace, exchange_dir, body)

        response = _wait_for_response(workspace, exchange_dir, call_id)

        assert response["ok"] is False
        assert response["error"] == {"code": "bad_request", "message": "request is not valid UTF-8"}

    def test_handler_failure_is_answered_and_the_channel_keeps_serving(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)

        def explode(payload: Mapping[str, object]) -> object:
            raise RuntimeError("handler bug")

        handlers: Mapping[str, object] = {"explode": explode, "echo": _echo}
        channel = _channel(LocalExecRunner(workspace), exchange_dir, handlers=handlers)
        channel.start()
        try:
            with pytest.raises(sdk.ToolCallError) as excinfo:
                sdk.call("explode", {}, timeout=15.0)
            assert excinfo.value.code == "internal_error"
            assert "handler bug" not in str(excinfo.value)
            assert sdk.call("echo", {"after": "failure"}, timeout=15.0) == {"after": "failure"}
        finally:
            channel.stop()

    def test_deeply_nested_request_json_is_answered_not_fatal(
        self, workspace: Path, running_channel: tuple[ToolCallChannel, str], sdk: ModuleType
    ) -> None:
        _, exchange_dir = running_channel
        depth = 100_000
        body = ('{"v": 1, "id": "x", "op": "echo", "payload": ' + "[" * depth + "]" * depth + "}").encode("utf-8")
        assert len(body) < MAX_PAYLOAD_BYTES
        call_id = _write_raw_request(workspace, exchange_dir, body)

        response = _wait_for_response(workspace, exchange_dir, call_id)

        assert response["ok"] is False
        assert sdk.call("echo", {"still": "alive"}, timeout=15.0) == {"still": "alive"}


class TestResponseWrite:
    """The response write must terminate on its own: a k8s v4 exec has no stdin half-close."""

    @staticmethod
    def _write(directory: Path, body: bytes, declared: int, *, close_stdin: bool) -> int:
        """Run the write script, feed it ``body`` and return its exit code; stdin stays open unless asked."""
        process = subprocess.Popen(
            [sys.executable, "-c", WRITE_RESPONSE_SCRIPT, "tmp.resp", "resp.json", "req.json", str(declared)],
            cwd=directory,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdin is not None
        try:
            process.stdin.write(body)
            process.stdin.flush()
            if close_stdin:
                process.stdin.close()
            try:
                return process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
                raise AssertionError("response write did not terminate with stdin still open") from None
        finally:
            if not process.stdin.closed:
                process.stdin.close()

    def test_terminates_and_publishes_with_stdin_left_open(self, tmp_path: Path) -> None:
        (tmp_path / "req.json").write_text("{}", encoding="utf-8")
        body = b"x" * 300_000

        exit_code = self._write(tmp_path, body, len(body), close_stdin=False)

        assert exit_code == 0
        assert (tmp_path / "resp.json").read_bytes() == body
        assert not (tmp_path / "req.json").exists()
        assert not (tmp_path / "tmp.resp").exists()

    def test_short_stream_publishes_nothing_and_fails(self, tmp_path: Path) -> None:
        (tmp_path / "req.json").write_text("{}", encoding="utf-8")

        exit_code = self._write(tmp_path, b"abc", 10, close_stdin=True)

        assert exit_code != 0
        assert not (tmp_path / "resp.json").exists()
        assert (tmp_path / "req.json").exists(), "an unanswered request must stay for the retry"

    def test_reads_exactly_the_declared_length(self, tmp_path: Path) -> None:
        (tmp_path / "req.json").write_text("{}", encoding="utf-8")

        exit_code = self._write(tmp_path, b"abcdef", 3, close_stdin=False)

        assert exit_code == 0
        assert (tmp_path / "resp.json").read_bytes() == b"abc"

    def test_respond_declares_the_body_length_in_argv(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        runner = LocalExecRunner(workspace)
        channel = _channel(runner, exchange_dir)
        channel.start()
        try:
            sdk.call("echo", {"n": 1}, timeout=15.0)
        finally:
            channel.stop()

        write_calls = [argv for argv in runner.calls if WRITE_RESPONSE_SCRIPT in argv]
        assert len(write_calls) == 1
        assert write_calls[0][-1].isdigit()


class TestExecFailures:
    def test_transient_exec_failures_are_retried(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        runner = FlakyExecRunner(LocalExecRunner(workspace), failures=2)
        channel = _channel(runner, exchange_dir)
        channel.start()
        try:
            assert sdk.call("echo", {"ok": True}) == {"ok": True}
        finally:
            channel.stop()

        assert runner.attempts > 2

    def test_persistent_exec_failure_keeps_polling_and_the_script_times_out_cleanly(
        self, workspace: Path, sdk: ModuleType
    ) -> None:
        exchange_dir = _prepare(workspace, sdk)
        runner = FlakyExecRunner(LocalExecRunner(workspace), failures=10_000)
        channel = _channel(runner, exchange_dir, poll_backoff_cap=0.05)
        channel.start()
        try:
            with pytest.raises(sdk.ToolCallError) as excinfo:
                sdk.call("echo", {}, timeout=0.6)
        finally:
            channel.stop()

        assert excinfo.value.code == "timeout"
        assert runner.attempts > 6, "the channel kept polling instead of giving up after the first failed poll"
        assert not (workspace / exchange_dir / UNAVAILABLE_MARKER_NAME).exists(), "no deadline, no stop: not given up"

    def test_polling_recovers_after_a_long_failure_and_answers_the_waiting_call(
        self, workspace: Path, sdk: ModuleType
    ) -> None:
        exchange_dir = _prepare(workspace, sdk)
        runner = FlakyExecRunner(LocalExecRunner(workspace), failures=12)  # four failed polls of three attempts
        channel = _channel(runner, exchange_dir, poll_backoff_cap=0.05)
        channel.start()
        try:
            assert sdk.call("echo", {"back": True}, timeout=10.0) == {"back": True}
        finally:
            channel.stop()

    def test_the_pause_between_failed_polls_grows_to_the_cap(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        channel = _channel(
            FlakyExecRunner(LocalExecRunner(workspace), failures=10_000),
            exchange_dir,
            attempts=1,
            backoff_seconds=0.5,
            poll_backoff_cap=2.0,
        )
        pauses: list[float] = []

        def record(seconds: float) -> None:
            pauses.append(seconds)
            if len(pauses) == 6:
                channel._stop_event.set()

        with mock.patch.object(channel, "_sleep", side_effect=record):
            channel._tick_loop()

        assert pauses == [0.5, 1.0, 2.0, 2.0, 2.0, 2.0]

    def test_the_pause_resets_after_a_successful_poll(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        outcomes = iter(["fail", "fail", "ok", "fail"])

        def scripted(argv: Sequence[str], stdin: bytes | None = None) -> ExecResult:
            del argv, stdin
            if next(outcomes) == "fail":
                raise OSError("exec broke")
            return ExecResult(stdout='{"requests": [], "done": false}', stderr="", exit_code=0)

        channel = _channel(scripted, exchange_dir, attempts=1, backoff_seconds=0.5, poll_backoff_cap=4.0)
        pauses: list[float] = []

        def record(seconds: float) -> None:
            pauses.append(seconds)
            if len(pauses) == 4:
                channel._stop_event.set()

        with mock.patch.object(channel, "_sleep", side_effect=record):
            channel._tick_loop()

        assert pauses == [0.5, 1.0, channel._poll_interval, 0.5]

    def test_reaching_the_deadline_gives_up_and_tells_the_script_to_fail_fast(
        self, workspace: Path, sdk: ModuleType
    ) -> None:
        exchange_dir = _prepare(workspace, sdk)
        inner = LocalExecRunner(workspace)

        def polls_fail(argv: Sequence[str], stdin: bytes | None = None) -> ExecResult:
            if tool_call_channel._POLL_SCRIPT in argv:
                raise OSError("poll exec broke")
            return inner(argv, stdin)

        channel = _channel(polls_fail, exchange_dir, deadline=time.monotonic() + 0.4, poll_backoff_cap=0.05)
        channel.start()
        try:
            started = time.monotonic()
            with pytest.raises(sdk.ToolCallError) as excinfo:
                sdk.call("echo", {}, timeout=30.0)
            elapsed = time.monotonic() - started
        finally:
            channel.stop()

        assert excinfo.value.code == "unavailable"
        assert elapsed < 10.0
        assert (workspace / exchange_dir / UNAVAILABLE_MARKER_NAME).exists()

    def test_stopping_the_channel_never_writes_the_unavailable_marker(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        runner = FlakyExecRunner(LocalExecRunner(workspace), failures=10_000)
        channel = _channel(runner, exchange_dir)
        channel.start()
        time.sleep(0.1)

        channel.stop()

        assert not (workspace / exchange_dir / UNAVAILABLE_MARKER_NAME).exists()

    def test_the_same_failure_is_logged_once_per_interval(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        channel = _channel(FlakyExecRunner(LocalExecRunner(workspace), failures=10_000), exchange_dir, attempts=1)
        ticks = 0

        def count(_seconds: float) -> None:
            nonlocal ticks
            ticks += 1
            if ticks == 6:
                channel._stop_event.set()

        with (
            mock.patch.object(tool_call_channel.logger, "warning") as warning,
            mock.patch.object(tool_call_channel.logger, "debug") as debug,
            mock.patch.object(channel, "_sleep", side_effect=count),
        ):
            channel._tick_loop()

        assert warning.call_count == 1
        assert debug.call_count == 5

    def test_failing_response_write_is_retried_and_the_call_is_not_run_again(
        self, workspace: Path, sdk: ModuleType
    ) -> None:
        exchange_dir = _prepare(workspace, sdk)
        inner = LocalExecRunner(workspace)
        write_attempts = 0
        runs = 0

        def counting(payload: Mapping[str, object]) -> object:
            nonlocal runs
            runs += 1
            return dict(payload)

        def writes_fail_first(argv: Sequence[str], stdin: bytes | None = None) -> ExecResult:
            nonlocal write_attempts
            if stdin is not None:
                write_attempts += 1
                if write_attempts <= 4:
                    raise OSError("stdin exec broke")
            return inner(argv, stdin)

        channel = _channel(writes_fail_first, exchange_dir, handlers={"echo": counting}, poll_backoff_cap=0.05)
        channel.start()
        try:
            assert sdk.call("echo", {"x": 1}, timeout=10.0) == {"x": 1}
        finally:
            channel.stop()

        assert write_attempts > 4
        assert runs == 1

    def test_response_write_failing_until_the_deadline_gives_up_and_tells_the_script_to_fail_fast(
        self, workspace: Path, sdk: ModuleType
    ) -> None:
        exchange_dir = _prepare(workspace, sdk)
        inner = LocalExecRunner(workspace)

        def only_writes_fail(argv: Sequence[str], stdin: bytes | None = None) -> ExecResult:
            if stdin is not None:
                raise OSError("stdin exec broke")
            return inner(argv, stdin)

        channel = _channel(only_writes_fail, exchange_dir, deadline=time.monotonic() + 0.5, poll_backoff_cap=0.05)
        channel.start()
        try:
            started = time.monotonic()
            with pytest.raises(sdk.ToolCallError) as excinfo:
                sdk.call("echo", {}, timeout=30.0)
            elapsed = time.monotonic() - started
        finally:
            channel.stop()

        assert excinfo.value.code == "unavailable"
        assert elapsed < 10.0
        assert (workspace / exchange_dir / UNAVAILABLE_MARKER_NAME).exists()

    def test_unexpected_thread_error_also_tells_the_script_to_fail_fast(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        channel = _channel(LocalExecRunner(workspace), exchange_dir)
        with mock.patch.object(ToolCallChannel, "_poll", side_effect=RuntimeError("unexpected")):
            channel.start()
            try:
                with pytest.raises(sdk.ToolCallError) as excinfo:
                    sdk.call("echo", {}, timeout=30.0)
            finally:
                channel.stop()

        assert excinfo.value.code == "unavailable"

    def test_cleanup_retries_back_off_after_stop(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        runner = FlakyExecRunner(LocalExecRunner(workspace), failures=10_000)
        channel = _channel(runner, exchange_dir, attempts=3, backoff_seconds=0.5)
        channel.stop()  # the run is over: the stop event is set before housekeeping starts

        with mock.patch.object(exec_runner.time, "sleep") as sleep:
            channel.remove_exchange_dir()

        assert runner.attempts == 3
        assert [call.args[0] for call in sleep.call_args_list] == [0.5, 1.0]

    def test_cleanup_failure_is_logged_not_raised(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        channel = _channel(
            FlakyExecRunner(LocalExecRunner(workspace), failures=10_000), exchange_dir, backoff_seconds=0.0
        )

        channel.remove_exchange_dir()


class TestLifecycle:
    def test_stop_is_idempotent_and_joins_the_thread(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        channel = _channel(LocalExecRunner(workspace), exchange_dir)
        channel.start()
        channel.start()

        channel.stop()
        channel.stop()

        assert not any(thread.name == "codemie-tool-call-channel" for thread in threading.enumerate())


class TestCleanup:
    def test_remove_exchange_dir_removes_the_run_folder_only(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        (workspace / exchange_dir / "req.stale.json").write_text("{}", encoding="utf-8")

        _channel(LocalExecRunner(workspace), exchange_dir).remove_exchange_dir()

        assert not (workspace / exchange_dir).exists()
        assert (workspace / BRIDGE_DIR_NAME).exists()

    def test_it_spawns_nothing_but_the_remove(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        runner = RecordingExecRunner()

        _channel(runner, exchange_dir).remove_exchange_dir()

        assert runner.calls == [["rm", "-rf", "--", exchange_dir]]

    def test_it_is_skipped_while_the_polling_thread_still_runs(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        runner = RecordingExecRunner()
        channel = _channel(runner, exchange_dir)
        channel._thread = mock.MagicMock(is_alive=mock.MagicMock(return_value=True))

        channel.remove_exchange_dir()

        assert runner.calls == [], "two execs must never overlap"


SHADOWED_MODULES: tuple[str, ...] = ("json", "os", "shutil", "signal", "contextlib")


def _plant_shadow_modules(workspace: Path) -> Path:
    """A script can write any file into the workspace root, the directory every pod-side exec runs in."""
    marker = workspace / "HIJACKED"
    for module in SHADOWED_MODULES:
        (workspace / f"{module}.py").write_text(
            f"open({str(marker)!r}, 'w').close()\nraise SystemExit(99)\n", encoding="utf-8"
        )
    return marker


class TestWorkspaceCannotHijackPodScripts:
    """The backend's pod-side scripts run outside the sandbox guard, so workspace files must not shadow their imports."""

    def test_pod_scripts_are_started_in_isolated_mode(self) -> None:
        assert tool_call_channel.POD_PYTHON == ("python3", "-I", "-c")

    def test_echo_round_trip_ignores_shadow_modules_in_the_workspace(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        marker = _plant_shadow_modules(workspace)
        channel = _channel(LocalExecRunner(workspace), exchange_dir)
        channel.start()
        try:
            assert sdk.call("echo", {"greeting": "hello"}, timeout=5) == {"greeting": "hello"}
        finally:
            channel.stop()

        assert not marker.exists()

    def test_cleanup_ignores_shadow_modules_in_the_workspace(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        marker = _plant_shadow_modules(workspace)
        channel = _channel(LocalExecRunner(workspace), exchange_dir)

        channel.remove_exchange_dir()

        assert not (workspace / exchange_dir).exists()
        assert not marker.exists()


class TestPollScript:
    """The pod-side poll answers pending requests and the done marker in one JSON document."""

    def _poll(self, workspace: Path, exchange_dir: str, *extra: str) -> Mapping[str, object]:
        argv = [
            "python3",
            "-I",
            "-c",
            tool_call_channel._POLL_SCRIPT,
            exchange_dir,
            str(MAX_PAYLOAD_BYTES),
            REQ_PREFIX,
            FILE_SUFFIX,
            *extra,
        ]
        result = LocalExecRunner(workspace)(argv, None)
        assert result.exit_code == 0, result.stderr
        parsed: object = json.loads(result.stdout)
        assert isinstance(parsed, dict)
        return parsed

    def test_empty_folder_reports_no_requests_and_not_done(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)

        assert self._poll(workspace, exchange_dir) == {"requests": [], "done": False}

    def test_without_a_marker_path_done_is_never_reported(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        (workspace / ".done").write_text("", encoding="utf-8")

        assert self._poll(workspace, exchange_dir)["done"] is False

    def test_marker_path_reports_done_once_the_file_exists(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)

        assert self._poll(workspace, exchange_dir, ".done")["done"] is False
        (workspace / ".done").write_text("", encoding="utf-8")
        assert self._poll(workspace, exchange_dir, ".done")["done"] is True

    def test_requests_and_done_come_from_one_exec(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        call_id = _write_raw_request(workspace, exchange_dir, b'{"v": 1, "id": "x", "op": "echo", "payload": {}}')
        (workspace / ".done").write_text("", encoding="utf-8")

        snapshot = self._poll(workspace, exchange_dir, ".done")

        assert snapshot["done"] is True
        requests = snapshot["requests"]
        assert isinstance(requests, list) and [entry["name"] for entry in requests] == [
            f"{REQ_PREFIX}{call_id}{FILE_SUFFIX}"
        ]


def _count_polls(runner: LocalExecRunner) -> int:
    return sum(1 for argv in runner.calls if tool_call_channel._POLL_SCRIPT in argv)


class TestDoneMarker:
    def test_done_is_noticed_by_the_same_exec_that_polls_for_requests(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        runner = LocalExecRunner(workspace)
        channel = _channel(runner, exchange_dir, done_path=".done")
        channel.start()
        try:
            (workspace / ".done").write_text("", encoding="utf-8")

            assert channel.wait_until_done(time.monotonic() + 5.0) is True
        finally:
            channel.stop()

        assert all(tool_call_channel._POLL_SCRIPT in argv for argv in runner.calls), "no separate done check"
        assert all(".done" in argv for argv in runner.calls if tool_call_channel._POLL_SCRIPT in argv)

    def test_polls_do_not_carry_a_marker_path_without_a_done_path(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        runner = LocalExecRunner(workspace)
        channel = _channel(runner, exchange_dir)
        channel.start()
        time.sleep(0.1)
        channel.stop()

        polls = [argv for argv in runner.calls if tool_call_channel._POLL_SCRIPT in argv]
        assert polls and all(argv[8] == "" for argv in polls), "an empty marker path means no done check"

    def test_wait_until_done_gives_up_at_its_deadline(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        channel = _channel(LocalExecRunner(workspace), exchange_dir, done_path=".done")
        channel.start()
        try:
            started = time.monotonic()
            assert channel.wait_until_done(started + 0.3) is False
            assert time.monotonic() - started < 3.0
        finally:
            channel.stop()

    def test_wait_until_done_before_start_is_false_at_once(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        channel = _channel(LocalExecRunner(workspace), exchange_dir, done_path=".done")

        assert channel.wait_until_done(time.monotonic() + 30.0) is False

    def test_wait_until_done_returns_false_when_the_channel_ended_before_the_marker(
        self, workspace: Path, sdk: ModuleType
    ) -> None:
        exchange_dir = _prepare(workspace, sdk)
        channel = _channel(LocalExecRunner(workspace), exchange_dir, done_path=".done")
        with mock.patch.object(ToolCallChannel, "_poll", side_effect=RuntimeError("unexpected")):
            channel.start()
            try:
                started = time.monotonic()
                assert channel.wait_until_done(started + 30.0) is False
                assert time.monotonic() - started < 5.0, "the caller must be able to fall back without waiting"
            finally:
                channel.stop()

    def test_done_is_noticed_while_a_call_is_still_running(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        entered = threading.Event()
        release = threading.Event()

        def slow(payload: Mapping[str, object]) -> object:
            entered.set()
            release.wait(timeout=20.0)
            return dict(payload)

        channel = _channel(LocalExecRunner(workspace), exchange_dir, handlers={"echo": slow}, done_path=".done")
        channel.start()
        try:
            call_id = _write_raw_request(
                workspace,
                exchange_dir,
                json.dumps({"v": PROTOCOL_VERSION, "id": "x", "op": "echo", "payload": {}}).encode(),
            )
            assert entered.wait(timeout=5.0)
            (workspace / ".done").write_text("", encoding="utf-8")

            started = time.monotonic()
            assert channel.wait_until_done(started + 10.0) is True
            assert time.monotonic() - started < 3.0, "the run must not wait for the call to return"
        finally:
            channel.stop()
            release.set()

        time.sleep(0.3)  # the call finishes after the run ended: its answer is dropped, not written
        assert not (workspace / exchange_dir / f"{RESP_PREFIX}{call_id}{FILE_SUFFIX}").exists()

    def test_calls_are_still_served_one_at_a_time(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        running = 0
        peak = 0
        lock = threading.Lock()

        def tracked(payload: Mapping[str, object]) -> object:
            nonlocal running, peak
            with lock:
                running += 1
                peak = max(peak, running)
            time.sleep(0.1)
            with lock:
                running -= 1
            return dict(payload)

        channel = _channel(LocalExecRunner(workspace), exchange_dir, handlers={"echo": tracked})
        channel.start()
        try:
            results: list[object] = []
            threads = [
                threading.Thread(target=lambda n=n: results.append(sdk.call("echo", {"n": n}, timeout=20.0)))
                for n in range(3)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30.0)
        finally:
            channel.stop()

        assert len(results) == 3
        assert peak == 1


class TestDeadlineRefusal:
    def test_a_call_is_refused_when_too_little_time_is_left(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        runs = 0

        def counting(payload: Mapping[str, object]) -> object:
            nonlocal runs
            runs += 1
            return dict(payload)

        channel = _channel(
            LocalExecRunner(workspace),
            exchange_dir,
            handlers={"echo": counting},
            deadline=time.monotonic() + 1.0,
            deadline_margin=5.0,
        )
        channel.start()
        try:
            with pytest.raises(sdk.ToolCallError) as excinfo:
                sdk.call("echo", {}, timeout=10.0)
        finally:
            channel.stop()

        assert excinfo.value.code == "deadline_exceeded"
        assert runs == 0, "the tool must not be started"

    def test_a_call_with_enough_time_left_runs(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        channel = _channel(LocalExecRunner(workspace), exchange_dir, deadline=time.monotonic() + 60.0)
        channel.start()
        try:
            assert sdk.call("echo", {"ok": True}, timeout=10.0) == {"ok": True}
        finally:
            channel.stop()

    def test_an_unknown_operation_is_still_reported_as_such_when_time_is_short(
        self, workspace: Path, sdk: ModuleType
    ) -> None:
        exchange_dir = _prepare(workspace, sdk)
        channel = _channel(
            LocalExecRunner(workspace), exchange_dir, deadline=time.monotonic() + 1.0, deadline_margin=5.0
        )
        channel.start()
        try:
            with pytest.raises(sdk.ToolCallError) as excinfo:
                sdk.call("no_such_op", {}, timeout=10.0)
        finally:
            channel.stop()

        assert excinfo.value.code == "unknown_op"

    def test_the_default_margin_is_two_seconds(self) -> None:
        assert DEADLINE_MARGIN_SECONDS == 2.0

    def test_the_run_without_a_deadline_never_refuses(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        channel = _channel(LocalExecRunner(workspace), exchange_dir, deadline=None, deadline_margin=1e9)
        channel.start()
        try:
            assert sdk.call("echo", {"ok": 1}, timeout=10.0) == {"ok": 1}
        finally:
            channel.stop()


class ExecSerialityRunner:
    """Wraps a runner and records how many execs overlap in time and on which threads they run."""

    def __init__(self, inner: LocalExecRunner) -> None:
        self.inner: LocalExecRunner = inner
        self.active: int = 0
        self.max_active: int = 0
        self.threads: set[str] = set()
        self._lock: threading.Lock = threading.Lock()

    def __call__(self, argv: Sequence[str], stdin: bytes | None = None) -> ExecResult:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.threads.add(threading.current_thread().name)
        try:
            return self.inner(argv, stdin)
        finally:
            with self._lock:
                self.active -= 1


class ConcurrencyProbe:
    """A handler that sleeps and records how many calls ran at the same time."""

    def __init__(self, hold: float = 0.15) -> None:
        self.hold: float = hold
        self.running: int = 0
        self.peak: int = 0
        self.seen: list[object] = []
        self._lock: threading.Lock = threading.Lock()

    def __call__(self, payload: Mapping[str, object]) -> object:
        with self._lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
            self.seen.append(payload["name"])
        time.sleep(self.hold)
        with self._lock:
            self.running -= 1
        return {"result": payload["name"]}


def _batch(sdk: ModuleType, names: Sequence[str], timeout: float = 30.0) -> list[object]:
    return sdk.call_tools([{"name": name} for name in names], timeout=timeout)


class TestParallelCalls:
    def test_calls_of_a_batch_run_at_the_same_time_up_to_the_limit(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        probe = ConcurrencyProbe()
        channel = _channel(
            LocalExecRunner(workspace), exchange_dir, handlers={"tool.call": probe}, max_parallel_calls=3
        )
        channel.start()
        try:
            results = _batch(sdk, ["a", "b", "c"])
        finally:
            channel.stop()

        assert results == [{"result": "a"}, {"result": "b"}, {"result": "c"}], "in the order given"
        assert probe.peak == 3

    def test_a_batch_larger_than_the_limit_waits_for_free_places_and_is_served_in_full(
        self, workspace: Path, sdk: ModuleType
    ) -> None:
        exchange_dir = _prepare(workspace, sdk)
        probe = ConcurrencyProbe(hold=0.1)
        channel = _channel(
            LocalExecRunner(workspace), exchange_dir, handlers={"tool.call": probe}, max_parallel_calls=2
        )
        channel.start()
        try:
            names = [f"t{n}" for n in range(7)]
            results = _batch(sdk, names)
        finally:
            channel.stop()

        assert results == [{"result": name} for name in names]
        assert probe.peak == 2

    def test_with_a_limit_of_one_the_calls_of_a_batch_are_served_one_after_another(
        self, workspace: Path, sdk: ModuleType
    ) -> None:
        exchange_dir = _prepare(workspace, sdk)
        probe = ConcurrencyProbe(hold=0.05)
        channel = _channel(
            LocalExecRunner(workspace), exchange_dir, handlers={"tool.call": probe}, max_parallel_calls=1
        )
        channel.start()
        try:
            results = _batch(sdk, ["a", "b", "c"])
        finally:
            channel.stop()

        assert len(results) == 3
        assert probe.peak == 1

    def test_the_default_is_one_call_at_a_time(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        probe = ConcurrencyProbe(hold=0.05)
        channel = _channel(LocalExecRunner(workspace), exchange_dir, handlers={"tool.call": probe})
        channel.start()
        try:
            _batch(sdk, ["a", "b", "c"])
        finally:
            channel.stop()

        assert probe.peak == 1

    def test_one_refused_call_of_a_batch_is_an_error_item_and_the_others_are_results(
        self, workspace: Path, sdk: ModuleType
    ) -> None:
        exchange_dir = _prepare(workspace, sdk)

        def handler(payload: Mapping[str, object]) -> object:
            if payload["name"] == "refused":
                raise ToolCallRefused("tool_unavailable", "no such tool")
            return {"result": payload["name"]}

        channel = _channel(
            LocalExecRunner(workspace), exchange_dir, handlers={"tool.call": handler}, max_parallel_calls=3
        )
        channel.start()
        try:
            results = _batch(sdk, ["a", "refused", "c"])
        finally:
            channel.stop()

        assert results[0] == {"result": "a"}
        assert results[2] == {"result": "c"}
        assert isinstance(results[1], sdk.ToolCallError)
        assert results[1].code == "tool_unavailable"

    def test_no_two_execs_overlap_and_every_exec_runs_on_the_polling_thread(
        self, workspace: Path, sdk: ModuleType
    ) -> None:
        """The exec client is not safe to share (see exec_runner): handlers run in a pool, execs on one thread."""
        exchange_dir = _prepare(workspace, sdk)
        runner = ExecSerialityRunner(LocalExecRunner(workspace))
        probe = ConcurrencyProbe(hold=0.1)
        channel = _channel(runner, exchange_dir, handlers={"tool.call": probe}, max_parallel_calls=4)
        channel.start()
        try:
            results = _batch(sdk, [f"t{n}" for n in range(8)])
        finally:
            channel.stop()

        assert len(results) == 8
        assert probe.peak > 1, "the handlers really did overlap"
        assert runner.max_active == 1
        assert runner.threads == {"codemie-tool-call-channel"}

    def test_a_request_being_served_is_not_read_again_by_the_next_polls(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        entered = threading.Event()
        release = threading.Event()

        def slow(payload: Mapping[str, object]) -> object:
            entered.set()
            release.wait(timeout=20.0)
            return {"result": 1}

        runner = LocalExecRunner(workspace)
        channel = _channel(runner, exchange_dir, handlers={"tool.call": slow})
        channel.start()
        try:
            call_id = _write_raw_request(
                workspace,
                exchange_dir,
                json.dumps({"v": PROTOCOL_VERSION, "id": "x", "op": "tool.call", "payload": {"name": "t"}}).encode(),
            )
            assert entered.wait(timeout=5.0)
            polls_before = _count_polls(runner)
            time.sleep(0.3)
            later_polls = [argv for argv in runner.calls if tool_call_channel._POLL_SCRIPT in argv][polls_before:]
        finally:
            release.set()
            channel.stop()

        assert later_polls, "polling went on while the call ran"
        assert all(call_id in argv[-1].split(",") for argv in later_polls), "the poll is told what is in flight"

    def test_a_call_that_finishes_after_the_run_ended_is_settled_as_not_delivered_and_writes_nothing(
        self, workspace: Path, sdk: ModuleType
    ) -> None:
        exchange_dir = _prepare(workspace, sdk)
        entered = threading.Event()
        release = threading.Event()
        settled: list[tuple[str, bool]] = []

        def slow(payload: Mapping[str, object]) -> object:
            entered.set()
            release.wait(timeout=20.0)
            return {"result": 1}

        channel = _channel(
            LocalExecRunner(workspace),
            exchange_dir,
            handlers={"tool.call": slow},
            done_path=".done",
            on_settled=lambda outcome: settled.append((outcome.call_id, bool(outcome.delivered))),
        )
        channel.start()
        try:
            call_id = _write_raw_request(
                workspace,
                exchange_dir,
                json.dumps({"v": PROTOCOL_VERSION, "id": "x", "op": "tool.call", "payload": {"name": "t"}}).encode(),
            )
            assert entered.wait(timeout=5.0)
            (workspace / ".done").write_text("", encoding="utf-8")
            assert channel.wait_until_done(time.monotonic() + 10.0) is True
        finally:
            channel.stop()
            release.set()

        time.sleep(0.3)
        assert settled == [(call_id, False)]
        assert not (workspace / exchange_dir / f"{RESP_PREFIX}{call_id}{FILE_SUFFIX}").exists()

    def test_an_answered_call_is_settled_as_delivered(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        settled: list[tuple[str, bool]] = []
        channel = _channel(
            LocalExecRunner(workspace),
            exchange_dir,
            on_settled=lambda outcome: settled.append((outcome.call_id, bool(outcome.delivered))),
        )
        channel.start()
        try:
            call_id = _write_raw_request(
                workspace,
                exchange_dir,
                json.dumps({"v": PROTOCOL_VERSION, "id": "x", "op": "echo", "payload": {"a": 1}}).encode(),
            )
            _wait_for_response(workspace, exchange_dir, call_id)
            deadline = time.monotonic() + 5.0
            while not settled and time.monotonic() < deadline:
                time.sleep(0.02)
        finally:
            channel.stop()

        assert settled == [(call_id, True)]

    def test_a_failing_listener_never_breaks_the_channel(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)

        def broken(outcome: object) -> None:
            raise RuntimeError("listener bug")

        channel = _channel(LocalExecRunner(workspace), exchange_dir, on_settled=broken)
        channel.start()
        try:
            assert sdk.call("echo", {"a": 1}, timeout=10.0) == {"a": 1}
            assert sdk.call("echo", {"a": 2}, timeout=10.0) == {"a": 2}
        finally:
            channel.stop()

    def test_stop_drops_the_calls_that_have_not_started_and_settles_them_as_not_delivered(
        self, workspace: Path, sdk: ModuleType
    ) -> None:
        exchange_dir = _prepare(workspace, sdk)
        entered = threading.Event()
        release = threading.Event()
        started: list[object] = []
        settled: list[tuple[str, bool]] = []

        def slow(payload: Mapping[str, object]) -> object:
            started.append(payload["name"])
            entered.set()
            release.wait(timeout=20.0)
            return {"result": 1}

        channel = _channel(
            LocalExecRunner(workspace),
            exchange_dir,
            handlers={"tool.call": slow},
            max_parallel_calls=1,
            on_settled=lambda outcome: settled.append((outcome.call_id, bool(outcome.delivered))),
        )
        channel.start()
        ids = [
            _write_raw_request(
                workspace,
                exchange_dir,
                json.dumps({"v": PROTOCOL_VERSION, "id": "x", "op": "tool.call", "payload": {"name": name}}).encode(),
            )
            for name in ("first", "second")
        ]
        assert entered.wait(timeout=5.0)
        time.sleep(0.2)  # the second request has been read and is queued behind the first

        channel.stop()
        release.set()
        time.sleep(0.3)

        assert len(started) == 1, "the queued call must never start"
        assert sorted(settled) == sorted((call_id, False) for call_id in ids)

    def test_a_failed_response_write_is_retried_on_a_later_tick_and_settled_once_delivered(
        self, workspace: Path, sdk: ModuleType
    ) -> None:
        exchange_dir = _prepare(workspace, sdk)
        inner = LocalExecRunner(workspace)
        write_failures = 0
        settled: list[tuple[str, bool]] = []

        def writes_fail_first(argv: Sequence[str], stdin: bytes | None = None) -> ExecResult:
            nonlocal write_failures
            if stdin is not None and write_failures < 3:
                write_failures += 1
                raise OSError("stdin exec broke")
            return inner(argv, stdin)

        channel = _channel(
            writes_fail_first,
            exchange_dir,
            attempts=1,
            poll_backoff_cap=0.05,
            on_settled=lambda outcome: settled.append((outcome.call_id, bool(outcome.delivered))),
        )
        channel.start()
        try:
            assert sdk.call("echo", {"x": 1}, timeout=10.0) == {"x": 1}
        finally:
            channel.stop()

        assert write_failures == 3
        assert [delivered for _, delivered in settled] == [True]

    def test_a_request_answered_twice_by_a_lost_acknowledgement_is_not_run_again(
        self, workspace: Path, sdk: ModuleType
    ) -> None:
        """If the request file survives its answer, the next poll lists it again: it must not run a second time."""
        exchange_dir = _prepare(workspace, sdk)
        inner = LocalExecRunner(workspace)
        runs = 0

        def counting(payload: Mapping[str, object]) -> object:
            nonlocal runs
            runs += 1
            return dict(payload)

        channel = _channel(inner, exchange_dir, handlers={"echo": counting})
        channel.start()
        try:
            call_id = _write_raw_request(
                workspace,
                exchange_dir,
                json.dumps({"v": PROTOCOL_VERSION, "id": "x", "op": "echo", "payload": {"a": 1}}).encode(),
            )
            _wait_for_response(workspace, exchange_dir, call_id)
            body = json.dumps({"v": PROTOCOL_VERSION, "id": "x", "op": "echo", "payload": {"a": 1}}).encode()
            (workspace / exchange_dir / f"{REQ_PREFIX}{call_id}{FILE_SUFFIX}").write_bytes(body)  # the request is back
            time.sleep(0.4)
        finally:
            channel.stop()

        assert runs == 1


class TestPollScriptKnownRequests:
    def test_a_known_request_is_reported_by_name_only_and_others_are_read_in_full(
        self, workspace: Path, sdk: ModuleType
    ) -> None:
        exchange_dir = _prepare(workspace, sdk)
        body = b'{"v": 1, "id": "x", "op": "echo", "payload": {}}'
        known_id = _write_raw_request(workspace, exchange_dir, body)
        new_id = _write_raw_request(workspace, exchange_dir, body)
        argv = [
            "python3",
            "-I",
            "-c",
            tool_call_channel._POLL_SCRIPT,
            exchange_dir,
            str(MAX_PAYLOAD_BYTES),
            REQ_PREFIX,
            FILE_SUFFIX,
            "",
            f"{known_id},some-other-id",
        ]

        result = LocalExecRunner(workspace)(argv, None)

        entries = {entry["name"]: entry for entry in json.loads(result.stdout)["requests"]}
        assert entries[f"{REQ_PREFIX}{known_id}{FILE_SUFFIX}"] == {
            "name": f"{REQ_PREFIX}{known_id}{FILE_SUFFIX}",
            "known": True,
        }
        assert entries[f"{REQ_PREFIX}{new_id}{FILE_SUFFIX}"]["body"] == body.decode()


class TestWorkerPool:
    def test_threads_start_on_demand_up_to_the_size_and_are_daemons(self) -> None:
        pool = tool_call_channel._WorkerPool(2, "test-pool")
        release = threading.Event()
        try:
            for _ in range(5):
                pool.submit(lambda: release.wait(timeout=10))
            time.sleep(0.1)

            workers = [t for t in threading.enumerate() if t.name.startswith("test-pool-")]
            assert len(workers) == 2
            assert all(thread.daemon for thread in workers), "a stuck tool call must not hold up the interpreter's exit"
        finally:
            release.set()
            pool.shutdown()

    def test_jobs_run_at_most_size_at_a_time(self) -> None:
        pool = tool_call_channel._WorkerPool(3, "test-pool-peak")
        lock = threading.Lock()
        running = peak = 0
        done = threading.Event()
        finished = 0

        def job() -> None:
            nonlocal running, peak, finished
            with lock:
                running += 1
                peak = max(peak, running)
            time.sleep(0.05)
            with lock:
                running -= 1
                finished += 1
                if finished == 9:
                    done.set()

        try:
            for _ in range(9):
                pool.submit(job)
            assert done.wait(timeout=10)
        finally:
            pool.shutdown()

        assert peak == 3

    def test_shutdown_drops_the_jobs_that_have_not_started_and_ends_the_threads(self) -> None:
        pool = tool_call_channel._WorkerPool(1, "test-pool-drop")
        release = threading.Event()
        started: list[int] = []
        pool.submit(lambda: (started.append(1), release.wait(timeout=10)))
        time.sleep(0.1)
        pool.submit(lambda: started.append(2))

        pool.shutdown()
        release.set()
        time.sleep(0.3)

        assert started == [1]
        assert not any(t.name.startswith("test-pool-drop-") for t in threading.enumerate())

    def test_submitting_after_shutdown_raises(self) -> None:
        pool = tool_call_channel._WorkerPool(1, "test-pool-closed")
        pool.shutdown()

        with pytest.raises(RuntimeError):
            pool.submit(lambda: None)

    def test_a_failing_job_does_not_kill_its_worker(self) -> None:
        pool = tool_call_channel._WorkerPool(1, "test-pool-survive")
        ran = threading.Event()

        def explode() -> None:
            raise RuntimeError("bug")

        try:
            pool.submit(explode)
            pool.submit(ran.set)
            assert ran.wait(timeout=5)
        finally:
            pool.shutdown()


class TestWithdrawnCalls:
    """A script that stops waiting removes its request file; the backend must stop spending effort on that call."""

    def test_a_queued_call_the_script_gave_up_on_never_starts_and_is_settled_as_withdrawn(
        self, workspace: Path, sdk: ModuleType
    ) -> None:
        exchange_dir = _prepare(workspace, sdk)
        entered = threading.Event()
        release = threading.Event()
        started: list[object] = []
        settled: list[object] = []

        def handler(payload: Mapping[str, object]) -> object:
            started.append(payload["name"])
            entered.set()
            release.wait(timeout=20.0)
            return {"result": 1}

        channel = _channel(
            LocalExecRunner(workspace),
            exchange_dir,
            handlers={"tool.call": handler},
            max_parallel_calls=1,
            on_settled=settled.append,
        )
        channel.start()
        try:
            results = sdk.call_tools([{"name": "first"}, {"name": "second"}], timeout=1.0)
            assert [r.code for r in results] == ["timeout", "timeout"]
            assert entered.wait(timeout=5.0)
            time.sleep(0.3)  # the channel notices the removed request files on its next polls
            release.set()
            time.sleep(0.4)
        finally:
            release.set()
            channel.stop()

        assert len(started) == 1, "the call that was still queued when the script gave up never starts"
        assert len(settled) == 2
        assert all(o.delivered is False and o.withdrawn is True for o in settled)  # type: ignore[attr-defined]
        assert list((workspace / exchange_dir).glob(f"{RESP_PREFIX}*")) == [], "no answer is written to nobody"

    def test_the_answer_of_a_call_withdrawn_while_it_ran_is_dropped(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        entered = threading.Event()
        release = threading.Event()
        settled: list[object] = []

        def handler(payload: Mapping[str, object]) -> object:
            entered.set()
            release.wait(timeout=20.0)
            return {"result": 1}

        channel = _channel(
            LocalExecRunner(workspace), exchange_dir, handlers={"tool.call": handler}, on_settled=settled.append
        )
        channel.start()
        try:
            result = sdk.call_tools([{"name": "slow"}], timeout=0.6)[0]
            assert result.code == "timeout"
            assert entered.wait(timeout=5.0)
            time.sleep(0.3)
            release.set()
            time.sleep(0.4)
        finally:
            release.set()
            channel.stop()

        assert [(o.delivered, o.withdrawn) for o in settled] == [(False, True)]  # type: ignore[attr-defined]
        assert list((workspace / exchange_dir).glob(f"{RESP_PREFIX}*")) == []

    def test_a_call_that_is_still_waited_for_is_not_withdrawn(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        probe = ConcurrencyProbe(hold=0.5)
        settled: list[object] = []
        channel = _channel(
            LocalExecRunner(workspace), exchange_dir, handlers={"tool.call": probe}, on_settled=settled.append
        )
        channel.start()
        try:
            result = sdk.call_tools([{"name": "a"}], timeout=10.0)
        finally:
            channel.stop()

        assert result == [{"result": "a"}]
        assert [(o.delivered, o.withdrawn) for o in settled] == [(True, False)]  # type: ignore[attr-defined]

    def test_the_outcome_names_the_tool_and_the_op(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        settled: list[object] = []
        channel = _channel(
            LocalExecRunner(workspace),
            exchange_dir,
            handlers={"tool.call": lambda payload: {"result": 1}},
            on_settled=settled.append,
        )
        channel.start()
        try:
            sdk.call_tools([{"name": "jira_tool"}], timeout=10.0)
            deadline = time.monotonic() + 5.0
            while not settled and time.monotonic() < deadline:
                time.sleep(0.02)
        finally:
            channel.stop()

        outcome = settled[0]
        assert (outcome.op, outcome.tool, outcome.code) == ("tool.call", "jira_tool", None)  # type: ignore[attr-defined]
        assert outcome.response_bytes > 0  # type: ignore[attr-defined]
