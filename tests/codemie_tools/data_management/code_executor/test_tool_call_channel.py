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

from codemie_tools.data_management.code_executor import tool_call_channel
from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import (
    BRIDGE_DIR_NAME,
    FILE_SUFFIX,
    MAX_PAYLOAD_BYTES,
    PROTOCOL_VERSION,
    REQ_PREFIX,
    RESP_PREFIX,
)
from codemie_tools.data_management.code_executor.llm_sandbox import (
    SANDBOX_SYSTEM_FILE_PREFIX,
    _build_sandbox_system_file_path,
)
from codemie_tools.data_management.code_executor.tool_call_channel import (
    DEFAULT_HANDLERS,
    KILL_SCRIPT,
    PROC_ROOT,
    SANDBOX_CMDLINE_MARKER,
    UNAVAILABLE_MARKER_NAME,
    WRITE_RESPONSE_SCRIPT,
    ExecFailed,
    ExecResult,
    ToolCallChannel,
    exchange_dir_path,
    new_exchange_dir_name,
    sweep_stale_exchange_folders,
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
    sdk._configure(str(workspace / exchange_dir))
    return exchange_dir


def _channel(runner: object, exchange_dir: str, **kwargs: object) -> ToolCallChannel:
    options: dict[str, object] = {"poll_interval": 0.02, "backoff_seconds": 0.01}
    options.update(kwargs)
    return ToolCallChannel(runner, exchange_dir, **options)  # type: ignore[arg-type]


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

    def test_default_registry_holds_only_echo(self) -> None:
        assert set(DEFAULT_HANDLERS) == {"echo"}

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

        handlers: Mapping[str, object] = {"explode": explode, "echo": DEFAULT_HANDLERS["echo"]}
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

    def test_persistent_exec_failure_ends_the_thread_and_the_script_times_out(
        self, workspace: Path, sdk: ModuleType
    ) -> None:
        exchange_dir = _prepare(workspace, sdk)
        runner = FlakyExecRunner(LocalExecRunner(workspace), failures=10_000)
        channel = _channel(runner, exchange_dir)
        channel.start()
        try:
            with pytest.raises(sdk.ToolCallError) as excinfo:
                sdk.call("echo", {}, timeout=1.0)
        finally:
            channel.stop()

        assert excinfo.value.code == "timeout"
        assert runner.attempts == 4, "three poll attempts, one best-effort marker attempt, then no more polling"

    def test_failing_response_write_tells_the_script_to_fail_fast(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        inner = LocalExecRunner(workspace)

        def only_writes_fail(argv: Sequence[str], stdin: bytes | None = None) -> ExecResult:
            if stdin is not None:
                raise OSError("stdin exec broke")
            return inner(argv, stdin)

        channel = _channel(only_writes_fail, exchange_dir)
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
        channel = ToolCallChannel(runner, exchange_dir, attempts=3, backoff_seconds=0.5)
        channel.stop()  # the run is over: the stop event is set before housekeeping starts

        with mock.patch.object(tool_call_channel.time, "sleep") as sleep:
            channel.cleanup(kill=False)

        assert runner.attempts == 3
        assert [call.args[0] for call in sleep.call_args_list] == [0.5, 1.0]

    def test_cleanup_failure_is_logged_not_raised(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        channel = ToolCallChannel(
            FlakyExecRunner(LocalExecRunner(workspace), failures=10_000), exchange_dir, backoff_seconds=0.0
        )

        channel.cleanup(kill=False)

    def test_exec_failed_is_a_runtime_error(self) -> None:
        assert issubclass(ExecFailed, RuntimeError)


class TestLifecycle:
    def test_stop_is_idempotent_and_joins_the_thread(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        channel = _channel(LocalExecRunner(workspace), exchange_dir)
        channel.start()
        channel.start()

        channel.stop()
        channel.stop()

        assert not any(thread.name == "codemie-tool-call-channel" for thread in threading.enumerate())


class TestSweep:
    def test_sweep_keeps_only_the_current_exchange_folder(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        bridge = workspace / BRIDGE_DIR_NAME
        stale_folder = bridge / new_exchange_dir_name()
        stale_folder.mkdir()
        (stale_folder / "req.leftover.json").write_text("{}", encoding="utf-8")
        (bridge / "loose-file").write_text("x", encoding="utf-8")
        (bridge / ".hidden").write_text("x", encoding="utf-8")

        _channel(LocalExecRunner(workspace), exchange_dir).sweep()

        assert sorted(item.name for item in bridge.iterdir()) == [exchange_dir.split("/")[-1]]

    def test_sweep_survives_a_missing_bridge_folder(self, workspace: Path) -> None:
        exchange_dir = exchange_dir_path(new_exchange_dir_name())

        _channel(LocalExecRunner(workspace), exchange_dir).sweep()

        assert not (workspace / BRIDGE_DIR_NAME).exists()

    def test_module_level_sweep_needs_no_channel_and_removes_every_child(self, workspace: Path) -> None:
        bridge = workspace / BRIDGE_DIR_NAME
        (bridge / new_exchange_dir_name()).mkdir(parents=True)
        (bridge / "loose-file").write_text("x", encoding="utf-8")

        sweep_stale_exchange_folders(LocalExecRunner(workspace))

        assert list(bridge.iterdir()) == []

    def test_module_level_sweep_tolerates_a_missing_bridge_folder(self, workspace: Path) -> None:
        sweep_stale_exchange_folders(LocalExecRunner(workspace))

        assert not (workspace / BRIDGE_DIR_NAME).exists()

    def test_module_level_sweep_logs_and_swallows_a_failing_exec(self, workspace: Path) -> None:
        runner = FlakyExecRunner(LocalExecRunner(workspace), failures=10_000)

        sweep_stale_exchange_folders(runner)

        assert runner.attempts >= 1


PROC_AVAILABLE: bool = Path("/proc/self/stat").exists()
needs_proc = pytest.mark.skipif(not PROC_AVAILABLE, reason="start-time matching needs a Linux /proc filesystem")


def _spawn_child(marker: str, cwd: Path | None = None) -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", marker], cwd=cwd)


def _proc_start_time(pid: int) -> str:
    stat_text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    return stat_text.rsplit(")", 1)[1].split()[19]


def _is_alive(process: subprocess.Popen[bytes]) -> bool:
    time.sleep(0.4)
    return process.poll() is None


class TestCleanup:
    def test_cleanup_removes_the_run_folder(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        (workspace / exchange_dir / "req.stale.json").write_text("{}", encoding="utf-8")

        _channel(LocalExecRunner(workspace), exchange_dir).cleanup(kill=False)

        assert not (workspace / exchange_dir).exists()
        assert (workspace / BRIDGE_DIR_NAME).exists()

    def test_cleanup_with_kill_tolerates_a_folder_without_a_pid(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)

        _channel(LocalExecRunner(workspace), exchange_dir).cleanup(kill=True)

        assert not (workspace / exchange_dir).exists()

    def test_absent_start_time_never_kills(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        process = _spawn_child("SANDBOX_absent")
        try:
            (workspace / exchange_dir / "pid").write_text(str(process.pid), encoding="utf-8")

            _channel(LocalExecRunner(workspace), exchange_dir).cleanup(kill=True)

            assert _is_alive(process), "without a recorded start time the pid must never be trusted"
        finally:
            process.kill()
            process.wait(timeout=10)

    @needs_proc
    def test_matching_start_time_and_sandbox_cmdline_is_killed(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        process = _spawn_child("SANDBOX_match", cwd=workspace)
        try:
            (workspace / exchange_dir / "pid").write_text(str(process.pid), encoding="utf-8")
            (workspace / exchange_dir / "start_time").write_text(_proc_start_time(process.pid), encoding="utf-8")

            _channel(LocalExecRunner(workspace), exchange_dir).cleanup(kill=True)

            assert process.wait(timeout=10) != 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)

    def test_cleanup_reads_the_real_proc_filesystem(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        runner = RecordingExecRunner()

        _channel(runner, exchange_dir).cleanup(kill=True)

        kill_argv = next(argv for argv in runner.calls if argv[0] == "python3")
        assert kill_argv[-1] == PROC_ROOT


class TestKillDecision:
    """The kill decision itself, driven against a synthetic /proc so it is verified on every platform."""

    @staticmethod
    def _fake_proc(root: Path, pid: int, *, start_time: str, cmdline: str, cwd: Path) -> Path:
        # Field 22 of /proc/<pid>/stat is the 20th whitespace token after the closing parenthesis of comm.
        fields = ["0"] * 20
        fields[0] = "S"
        fields[19] = start_time
        entry = root / str(pid)
        entry.mkdir(parents=True)
        (entry / "stat").write_text(f"{pid} (python3) " + " ".join(fields) + "\n", encoding="utf-8")
        (entry / "cmdline").write_bytes(cmdline.encode("utf-8") + b"\x00")
        (entry / "cwd").symlink_to(cwd)
        return root

    def _run_kill(self, run_dir: Path, proc_root: Path, workdir: Path) -> None:
        """Runs the kill script as the pod does: the workspace root is the working directory of the exec."""
        completed = subprocess.run(
            [sys.executable, "-I", "-c", KILL_SCRIPT, str(run_dir), SANDBOX_CMDLINE_MARKER, str(proc_root)],
            capture_output=True,
            check=False,
            cwd=workdir,
        )
        assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")

    def test_matching_start_time_and_marker_kills(self, tmp_path: Path) -> None:
        process = _spawn_child("SANDBOX_match")
        try:
            run_dir = tmp_path / "run"
            run_dir.mkdir()
            (run_dir / "pid").write_text(str(process.pid), encoding="utf-8")
            (run_dir / "start_time").write_text("4242", encoding="utf-8")
            proc = self._fake_proc(
                tmp_path / "proc", process.pid, start_time="4242", cmdline="SANDBOX_match", cwd=tmp_path
            )

            self._run_kill(run_dir, proc, tmp_path)

            assert process.wait(timeout=10) != 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)

    def test_mismatched_start_time_leaves_the_process_alive(self, tmp_path: Path) -> None:
        process = _spawn_child("SANDBOX_mismatch")
        try:
            run_dir = tmp_path / "run"
            run_dir.mkdir()
            (run_dir / "pid").write_text(str(process.pid), encoding="utf-8")
            (run_dir / "start_time").write_text("4242", encoding="utf-8")
            proc = self._fake_proc(
                tmp_path / "proc", process.pid, start_time="9999", cmdline="SANDBOX_mismatch", cwd=tmp_path
            )

            self._run_kill(run_dir, proc, tmp_path)

            assert _is_alive(process), "a recycled pid must not be killed"
        finally:
            process.kill()
            process.wait(timeout=10)

    def test_process_without_sandbox_marker_is_left_alive(self, tmp_path: Path) -> None:
        process = _spawn_child("unrelated_worker")
        try:
            run_dir = tmp_path / "run"
            run_dir.mkdir()
            (run_dir / "pid").write_text(str(process.pid), encoding="utf-8")
            (run_dir / "start_time").write_text("4242", encoding="utf-8")
            proc = self._fake_proc(
                tmp_path / "proc", process.pid, start_time="4242", cmdline="unrelated_worker", cwd=tmp_path
            )

            self._run_kill(run_dir, proc, tmp_path)

            assert _is_alive(process), "only a SANDBOX_ command line may be killed"
        finally:
            process.kill()
            process.wait(timeout=10)

    def test_process_working_in_another_directory_is_left_alive(self, tmp_path: Path) -> None:
        """A doctored pid file must not reach a process of another conversation (a different workspace)."""
        workspace = tmp_path / "workspace"
        other_workspace = tmp_path / "other-workspace"
        workspace.mkdir()
        other_workspace.mkdir()
        process = _spawn_child("SANDBOX_other_conversation")
        try:
            run_dir = workspace / "run"
            run_dir.mkdir()
            (run_dir / "pid").write_text(str(process.pid), encoding="utf-8")
            (run_dir / "start_time").write_text("4242", encoding="utf-8")
            proc = self._fake_proc(
                tmp_path / "proc",
                process.pid,
                start_time="4242",
                cmdline="SANDBOX_other_conversation",
                cwd=other_workspace,
            )

            self._run_kill(run_dir, proc, workspace)

            assert _is_alive(process), "pid, start time and marker all match, but the workspace does not"
        finally:
            process.kill()
            process.wait(timeout=10)

    def test_process_with_unreadable_working_directory_is_left_alive(self, tmp_path: Path) -> None:
        process = _spawn_child("SANDBOX_no_cwd")
        try:
            run_dir = tmp_path / "run"
            run_dir.mkdir()
            (run_dir / "pid").write_text(str(process.pid), encoding="utf-8")
            (run_dir / "start_time").write_text("4242", encoding="utf-8")
            proc = self._fake_proc(
                tmp_path / "proc", process.pid, start_time="4242", cmdline="SANDBOX_no_cwd", cwd=tmp_path
            )
            (proc / str(process.pid) / "cwd").unlink()

            self._run_kill(run_dir, proc, tmp_path)

            assert _is_alive(process), "a process whose working directory cannot be read must not be killed"
        finally:
            process.kill()
            process.wait(timeout=10)

    def test_marker_is_the_sandbox_file_prefix(self) -> None:
        assert SANDBOX_CMDLINE_MARKER == SANDBOX_SYSTEM_FILE_PREFIX

    def test_real_sandbox_script_command_line_is_killed(self, tmp_path: Path) -> None:
        """The cmdline is built with the sandbox's own file naming helper, so a renamed prefix breaks this test."""
        script_name = _build_sandbox_system_file_path("/home/codemie/u/conv", "py").name
        process = _spawn_child(script_name)
        try:
            run_dir = tmp_path / "run"
            run_dir.mkdir()
            (run_dir / "pid").write_text(str(process.pid), encoding="utf-8")
            (run_dir / "start_time").write_text("4242", encoding="utf-8")
            proc = self._fake_proc(tmp_path / "proc", process.pid, start_time="4242", cmdline=script_name, cwd=tmp_path)

            self._run_kill(run_dir, proc, tmp_path)

            assert process.wait(timeout=10) != 0
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)

    def test_non_ascii_pid_or_start_time_files_never_kill(self, tmp_path: Path) -> None:
        process = _spawn_child("SANDBOX_garbled")
        try:
            run_dir = tmp_path / "run"
            run_dir.mkdir()
            (run_dir / "pid").write_bytes(str(process.pid).encode() + b"\xff")
            (run_dir / "start_time").write_bytes(b"4242\xff")
            proc = self._fake_proc(
                tmp_path / "proc", process.pid, start_time="4242", cmdline="SANDBOX_garbled", cwd=tmp_path
            )

            self._run_kill(run_dir, proc, tmp_path)

            assert _is_alive(process), "bytes that are not ASCII must fail closed, not be repaired into a pid"
        finally:
            process.kill()
            process.wait(timeout=10)

    def test_unknown_pid_is_a_no_op(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "pid").write_text("999999", encoding="utf-8")
        (run_dir / "start_time").write_text("4242", encoding="utf-8")
        (tmp_path / "proc").mkdir()

        self._run_kill(run_dir, tmp_path / "proc", tmp_path)


class FakeExecResponse:
    """Minimal stand-in for the websocket object ``kubernetes.stream.stream`` returns."""

    def __init__(self, stdout: str, stderr: str, returncode: int | None, *, stall: bool = False) -> None:
        self.returncode: int | None = returncode
        self._stall: bool = stall
        self._stdout: str = stdout
        self._stderr: str = stderr
        self._open: bool = True
        self.stdin_writes: list[object] = []
        self.closed: bool = False

    def is_open(self) -> bool:
        return self._open

    def update(self, timeout: int = 1) -> None:
        if self._stall:
            time.sleep(min(timeout, 0.05))
            return
        self._open = False

    def peek_stdout(self) -> bool:
        return bool(self._stdout)

    def read_stdout(self) -> str:
        value, self._stdout = self._stdout, ""
        return value

    def peek_stderr(self) -> bool:
        return bool(self._stderr)

    def read_stderr(self) -> str:
        value, self._stderr = self._stderr, ""
        return value

    def write_stdin(self, data: object) -> None:
        self.stdin_writes.append(data)

    def close(self) -> None:
        self.closed = True


class TestKubernetesExecRunner:
    @staticmethod
    def _run(response: FakeExecResponse, argv: Sequence[str], stdin: bytes | None) -> tuple[ExecResult, mock.MagicMock]:
        with (
            mock.patch.object(tool_call_channel, "KubernetesClientManager") as manager_cls,
            mock.patch.object(tool_call_channel, "stream", return_value=response) as stream_mock,
        ):
            manager_cls.return_value.get_client.return_value = mock.MagicMock()
            runner = tool_call_channel.KubernetesExecRunner(
                pod_name="executor-pod-1",
                container_name="executor",
                namespace="codemie-code-executor",
                workdir="/home/codemie/user_1/conv_2",
            )
            result = runner(argv, stdin)
        return result, stream_mock

    def test_command_runs_in_the_workspace_root_and_binds_pod_and_container(self) -> None:
        response = FakeExecResponse(stdout="[]", stderr="", returncode=0)

        result, stream_mock = self._run(response, ["python3", "-c", "pass", ".codemie_bridge/x"], None)

        kwargs = stream_mock.call_args.kwargs
        assert stream_mock.call_args.args[1:] == ("executor-pod-1", "codemie-code-executor")
        assert kwargs["container"] == "executor"
        assert kwargs["stdin"] is False
        command = kwargs["command"]
        assert command[0] == "sh"
        assert command[-5:] == [
            "/home/codemie/user_1/conv_2",
            "python3",
            "-c",
            "pass",
            ".codemie_bridge/x",
        ]
        assert result == ExecResult(stdout="[]", stderr="", exit_code=0)

    def test_stdin_is_streamed_as_bytes_and_never_followed_by_an_eof_frame(self) -> None:
        """v4.channel.k8s.io has no stdin half-close: an empty frame is not EOF, so none is sent."""
        response = FakeExecResponse(stdout="", stderr="", returncode=0)
        payload = b"a" * (tool_call_channel._STDIN_CHUNK_BYTES + 5)

        _, stream_mock = self._run(response, ["python3", "-c", "pass"], payload)

        assert stream_mock.call_args.kwargs["stdin"] is True
        assert response.stdin_writes == [payload[: tool_call_channel._STDIN_CHUNK_BYTES], payload[-5:]]
        assert all(isinstance(frame, bytes) and frame for frame in response.stdin_writes)
        assert response.closed is True

    def test_stalled_exec_is_closed_and_reported_at_the_deadline(self) -> None:
        response = FakeExecResponse(stdout="", stderr="", returncode=None, stall=True)

        with (
            mock.patch.object(tool_call_channel, "KubernetesClientManager") as manager_cls,
            mock.patch.object(tool_call_channel, "stream", return_value=response),
        ):
            manager_cls.return_value.get_client.return_value = mock.MagicMock()
            runner = tool_call_channel.KubernetesExecRunner(
                pod_name="p", container_name="c", namespace="n", workdir="/w", exec_timeout_seconds=0.3
            )
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                runner(["python3", "-c", "pass"], b"payload")

        assert time.monotonic() - started < 5.0
        assert response.closed is True

    def test_non_zero_exit_code_is_reported_not_raised(self) -> None:
        response = FakeExecResponse(stdout="", stderr="boom", returncode=7)

        result, _ = self._run(response, ["rm", "-rf", "x"], None)

        assert result == ExecResult(stdout="", stderr="boom", exit_code=7)

    def test_workdir_wrapper_really_changes_directory_and_keeps_argv(self, tmp_path: Path) -> None:
        (tmp_path / "marker.txt").write_text("found", encoding="utf-8")

        completed = subprocess.run(
            ["sh", "-c", tool_call_channel.WORKDIR_SH, "codemie", str(tmp_path), "cat", "--", "marker.txt"],
            cwd="/",
            capture_output=True,
            check=False,
        )

        assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
        assert completed.stdout == b"found"

    def test_missing_exit_code_counts_as_success(self) -> None:
        response = FakeExecResponse(stdout="ok", stderr="", returncode=None)

        result, _ = self._run(response, ["rm", "-rf", "x"], None)

        assert result.exit_code == 0


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

    def test_sweep_and_cleanup_ignore_shadow_modules_in_the_workspace(self, workspace: Path, sdk: ModuleType) -> None:
        exchange_dir = _prepare(workspace, sdk)
        stale_folder = workspace / BRIDGE_DIR_NAME / new_exchange_dir_name()
        stale_folder.mkdir()
        marker = _plant_shadow_modules(workspace)
        channel = _channel(LocalExecRunner(workspace), exchange_dir)

        channel.sweep()
        channel.cleanup(kill=True)

        assert not stale_folder.exists()
        assert not (workspace / exchange_dir).exists()
        assert not marker.exists()
