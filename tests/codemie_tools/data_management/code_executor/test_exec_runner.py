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

"""Tests for the Kubernetes exec runner and the retrying exec."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

import pytest

from codemie_tools.data_management.code_executor import exec_runner
from codemie_tools.data_management.code_executor.exec_runner import ExecFailed, ExecResult, run_with_retries


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
            mock.patch.object(exec_runner, "KubernetesClientManager") as manager_cls,
            mock.patch.object(exec_runner, "stream", return_value=response) as stream_mock,
        ):
            manager_cls.return_value.get_client.return_value = mock.MagicMock()
            runner = exec_runner.KubernetesExecRunner(
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
        payload = b"a" * (exec_runner._STDIN_CHUNK_BYTES + 5)

        _, stream_mock = self._run(response, ["python3", "-c", "pass"], payload)

        assert stream_mock.call_args.kwargs["stdin"] is True
        assert response.stdin_writes == [payload[: exec_runner._STDIN_CHUNK_BYTES], payload[-5:]]
        assert all(isinstance(frame, bytes) and frame for frame in response.stdin_writes)
        assert response.closed is True

    def test_stalled_exec_is_closed_and_reported_at_the_deadline(self) -> None:
        response = FakeExecResponse(stdout="", stderr="", returncode=None, stall=True)

        with (
            mock.patch.object(exec_runner, "KubernetesClientManager") as manager_cls,
            mock.patch.object(exec_runner, "stream", return_value=response),
        ):
            manager_cls.return_value.get_client.return_value = mock.MagicMock()
            runner = exec_runner.KubernetesExecRunner(
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
            ["sh", "-c", exec_runner.WORKDIR_SH, "codemie", str(tmp_path), "cat", "--", "marker.txt"],
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


class TestRunWithRetries:
    @staticmethod
    def _flaky(failures: int) -> tuple[list[Sequence[str]], exec_runner.ExecRunner]:
        calls: list[Sequence[str]] = []

        def runner(argv: Sequence[str], stdin: bytes | None = None) -> ExecResult:
            calls.append(list(argv))
            if len(calls) <= failures:
                raise OSError("exec broke")
            return ExecResult(stdout="ok", stderr="", exit_code=0)

        return calls, runner

    def test_a_transient_failure_is_retried_with_a_growing_pause(self) -> None:
        calls, runner = self._flaky(2)
        pauses: list[float] = []

        result = run_with_retries(runner, ["x"], attempts=3, backoff_seconds=0.5, sleep=pauses.append)

        assert result.stdout == "ok"
        assert len(calls) == 3
        assert pauses == [0.5, 1.0]

    def test_a_non_zero_exit_is_retried_like_a_transport_error(self) -> None:
        outcomes = iter([7, 0])

        def runner(argv: Sequence[str], stdin: bytes | None = None) -> ExecResult:
            return ExecResult(stdout="", stderr="boom", exit_code=next(outcomes))

        assert run_with_retries(runner, ["x"], attempts=2, backoff_seconds=0, sleep=lambda _: None).exit_code == 0

    def test_after_the_last_attempt_exec_failed_names_the_command_and_the_cause(self) -> None:
        _, runner = self._flaky(100)

        with pytest.raises(ExecFailed) as excinfo:
            run_with_retries(runner, ["rm", "-rf", "x"], attempts=2, backoff_seconds=0, sleep=lambda _: None)

        assert "rm failed after 2 attempts" in str(excinfo.value)
        assert "OSError" in str(excinfo.value)

    def test_the_stdin_is_passed_on_every_attempt(self) -> None:
        seen: list[bytes | None] = []

        def runner(argv: Sequence[str], stdin: bytes | None = None) -> ExecResult:
            seen.append(stdin)
            return ExecResult(stdout="", stderr="", exit_code=1 if len(seen) < 2 else 0)

        run_with_retries(runner, ["x"], b"body", attempts=2, backoff_seconds=0, sleep=lambda _: None)

        assert seen == [b"body", b"body"]

    def test_the_default_pause_is_a_real_sleep_looked_up_when_needed(self) -> None:
        _, runner = self._flaky(1)

        with mock.patch.object(exec_runner.time, "sleep") as sleep:
            run_with_retries(runner, ["x"], attempts=2, backoff_seconds=0.25)

        sleep.assert_called_once_with(0.25)

    def test_at_least_one_attempt_is_made(self) -> None:
        calls, runner = self._flaky(0)

        run_with_retries(runner, ["x"], attempts=0)

        assert len(calls) == 1

    def test_exec_failed_is_a_runtime_error(self) -> None:
        assert issubclass(ExecFailed, RuntimeError)
