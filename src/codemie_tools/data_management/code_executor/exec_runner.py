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

"""Runs one command in the sandbox pod: the runner protocol, its Kubernetes implementation and the retrying exec.

An :class:`ExecRunner` is **not** safe to call from two threads at once: ``kubernetes.stream.stream`` swaps the API
client's ``request`` function for the duration of a call and puts the old one back, so two overlapping execs on one
client can send one of them as a plain HTTP request. The channel therefore makes every exec from one thread.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, final

from kubernetes.stream import stream

from codemie_tools.data_management.code_executor.k8s_client_manager import KubernetesClientManager

#: Upper bound for one pod-side exec (connect, stdin, run, close). A stalled exec is closed and retried.
DEFAULT_EXEC_TIMEOUT_SECONDS: float = 15.0
DEFAULT_EXEC_ATTEMPTS: int = 3
DEFAULT_BACKOFF_SECONDS: float = 0.5

# Runs the channel's argv with the workspace root as the working directory. $1 is the root; after the shift the
# remaining positional parameters are the command itself, so nothing is ever interpolated into the shell text.
WORKDIR_SH = 'cd "$1" || exit 1; shift 1; exec "$@"'
_STDIN_CHUNK_BYTES: int = 65536


@final
@dataclass(frozen=True)
class ExecResult:
    """Outcome of one pod-side command."""

    stdout: str
    stderr: str
    exit_code: int


class ExecRunner(Protocol):
    """Runs one command in the sandbox, rooted at the workspace root, and returns its outcome."""

    def __call__(self, argv: Sequence[str], stdin: bytes | None = None) -> ExecResult: ...


class ExecFailed(RuntimeError):
    """A pod-side command could not be completed after the configured number of attempts."""


def run_with_retries(
    runner: ExecRunner,
    argv: Sequence[str],
    stdin: bytes | None = None,
    *,
    attempts: int = DEFAULT_EXEC_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    sleep: Callable[[float], object] | None = None,
) -> ExecResult:
    """Run one command with up to ``attempts`` tries and a growing pause (``sleep``) between them.

    A transport error and a non-zero exit are retried alike; after the last one :class:`ExecFailed` is raised.
    ``sleep`` (default ``time.sleep``) lets a caller make the pause interruptible (the channel waits on its stop event).
    """
    limit = max(1, attempts)
    last = "no attempt was made"
    for attempt in range(1, limit + 1):
        try:
            result = runner(argv, stdin)
        except Exception as exc:  # noqa: BLE001 - any transport failure is retried, then gives up
            last = f"{type(exc).__name__}: {exc}"
        else:
            if result.exit_code == 0:
                return result
            last = f"exit code {result.exit_code}: {result.stderr.strip()[:200]}"
        if attempt < limit:
            (sleep or time.sleep)(backoff_seconds * attempt)
    raise ExecFailed(f"{argv[0]} failed after {limit} attempts ({last})")


@final
class KubernetesExecRunner:
    """Runs one command per call in a Job pod, on its own API client.

    Deliberately independent of the sandbox session: it holds nothing but the pod binding and a private
    :class:`KubernetesClientManager`, so it can talk to the pod while the runner waits for the script.
    """

    def __init__(
        self,
        *,
        pod_name: str,
        container_name: str,
        namespace: str,
        workdir: str,
        kubeconfig_path: str | None = None,
        exec_timeout_seconds: float = DEFAULT_EXEC_TIMEOUT_SECONDS,
    ) -> None:
        self.exec_timeout_seconds: float = exec_timeout_seconds
        self.pod_name: str = pod_name
        self.container_name: str = container_name
        self.namespace: str = namespace
        self.workdir: str = workdir
        self._client_manager: KubernetesClientManager = KubernetesClientManager(kubeconfig_path)

    def __call__(self, argv: Sequence[str], stdin: bytes | None = None) -> ExecResult:
        client = self._client_manager.get_client()
        command = ["sh", "-c", WORKDIR_SH, "codemie", self.workdir, *argv]
        response = stream(
            client.connect_get_namespaced_pod_exec,
            self.pod_name,
            self.namespace,
            command=command,
            container=self.container_name,
            stderr=True,
            stdin=stdin is not None,
            stdout=True,
            tty=False,
            _preload_content=False,
        )
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        deadline = time.monotonic() + self.exec_timeout_seconds
        try:
            if stdin is not None:
                # Binary frames only, and no terminating frame: v4.channel.k8s.io cannot half-close stdin, so the
                # command must know how much to read (see the write_response pod script).
                view = memoryview(stdin)
                for offset in range(0, len(view), _STDIN_CHUNK_BYTES):
                    response.write_stdin(bytes(view[offset : offset + _STDIN_CHUNK_BYTES]))
            while response.is_open():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"exec of {argv[0]} did not finish within {self.exec_timeout_seconds:g}s")
                response.update(timeout=min(1.0, remaining))
                if response.peek_stdout():
                    stdout_chunks.append(response.read_stdout())
                if response.peek_stderr():
                    stderr_chunks.append(response.read_stderr())
            exit_code = response.returncode
        finally:
            response.close()
        return ExecResult(
            stdout="".join(stdout_chunks),
            stderr="".join(stderr_chunks),
            exit_code=0 if exit_code is None else int(exit_code),
        )
