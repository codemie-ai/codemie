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

"""Backend side of the script tool-call bridge.

A :class:`ToolCallChannel` polls the exchange folder of one run through an :class:`ExecRunner` and answers the
requests the runtime SDK leaves there. It never touches the sandbox session or the per-pod lock: every pod-side
action is an independent exec that uses only ``sh``, ``cat``, ``mv``, ``rm`` and ``python3``, which lets tests
drive the very same argv against a local directory.

All paths the channel emits are relative to the workspace root; supplying that working directory is the runner's
job (:class:`KubernetesExecRunner` wraps every command in ``sh -c 'cd <root> ...'``).
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, final

from kubernetes.stream import stream

from codemie_tools.data_management.code_executor.k8s_client_manager import KubernetesClientManager
from codemie_tools.data_management.code_executor.llm_sandbox import SANDBOX_SYSTEM_FILE_PREFIX
from codemie_tools.data_management.code_executor.pod_scripts import load_pod_script
from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import (
    BRIDGE_DIR_NAME,
    FILE_SUFFIX,
    MAX_PAYLOAD_BYTES,
    PROTOCOL_VERSION,
    REQ_PREFIX,
    RESP_PREFIX,
    UNAVAILABLE_MARKER_NAME,
)

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS: float = 0.5
DEFAULT_EXEC_ATTEMPTS: int = 3
DEFAULT_BACKOFF_SECONDS: float = 0.5
STOP_JOIN_TIMEOUT_SECONDS: float = 10.0
#: Upper bound for one pod-side exec (connect, stdin, run, close). A stalled exec is closed and retried.
DEFAULT_EXEC_TIMEOUT_SECONDS: float = 15.0

_EXCHANGE_NAME_PATTERN: re.Pattern[str] = re.compile(r"^[0-9]+-[0-9a-f]{32}$")
_EXCHANGE_DIR_PATTERN: re.Pattern[str] = re.compile(rf"^{re.escape(BRIDGE_DIR_NAME)}/[0-9]+-[0-9a-f]{{32}}$")

#: How every pod-side script is started. The exec runs in the workspace root, which the script under execution
#: can write to; without -I (isolated mode) that directory is on sys.path, so a workspace file named ``json.py``
#: or ``os.py`` would run in the backend's exec process, outside the sandbox guard.
POD_PYTHON: tuple[str, ...] = ("python3", "-I", "-c")

_POLL_SCRIPT: str = load_pod_script("poll")
WRITE_RESPONSE_SCRIPT: str = load_pod_script("write_response")
_SWEEP_SCRIPT: str = load_pod_script("sweep")
KILL_SCRIPT: str = load_pod_script("kill")


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


ToolCallHandler = Callable[[Mapping[str, object]], object]


def _echo(payload: Mapping[str, object]) -> object:
    """The only handler of this sub-task: return the caller's payload unchanged."""
    return dict(payload)


DEFAULT_HANDLERS: Mapping[str, ToolCallHandler] = {"echo": _echo}


def new_exchange_dir_name() -> str:
    """Name for one run's exchange folder: ``<epoch>-<uuid4hex>``."""
    return f"{int(time.time())}-{uuid.uuid4().hex}"


def exchange_dir_path(name: str) -> str:
    """Workspace-relative path of the exchange folder called ``name``."""
    if not _EXCHANGE_NAME_PATTERN.match(name):
        raise ValueError(f"invalid exchange folder name: {name!r}")
    return f"{BRIDGE_DIR_NAME}/{name}"


class ExecFailed(RuntimeError):
    """A pod-side command could not be completed after the configured number of attempts."""


@final
@dataclass(frozen=True)
class _PendingRequest:
    """One ``req.<id>.json`` file seen by a poll."""

    call_id: str
    file_name: str
    oversize: bool
    body: str | None
    bad_encoding: bool = False


# Marks the exchange folder as served by no one, so the SDK fails fast instead of waiting out its call timeout.
# argv: <marker path>.
_MARK_UNAVAILABLE_SH = ': > "$1"'

# Tied to the sandbox's own script naming, so a renamed prefix cannot silently disable the kill.
SANDBOX_CMDLINE_MARKER: str = SANDBOX_SYSTEM_FILE_PREFIX
PROC_ROOT: str = "/proc"


# Runs the channel's argv with the workspace root as the working directory. $1 is the root; after the shift the
# remaining positional parameters are the command itself, so nothing is ever interpolated into the shell text.
WORKDIR_SH = 'cd "$1" || exit 1; shift 1; exec "$@"'
_STDIN_CHUNK_BYTES: int = 65536


@final
class KubernetesExecRunner:
    """Runs one command per call in a pooled sandbox pod, on its own API client.

    Deliberately independent of the sandbox session and the per-pod lock: it holds nothing but the pod binding
    and a private :class:`KubernetesClientManager`, so it can talk to the pod while the session is blocked
    inside a long ``session.run``.
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
                # command must know how much to read (see WRITE_RESPONSE_SCRIPT).
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


def _sweep_argv(keep: str) -> list[str]:
    return [*POD_PYTHON, _SWEEP_SCRIPT, BRIDGE_DIR_NAME, keep]


def sweep_stale_exchange_folders(runner: ExecRunner) -> None:
    """Remove every child of the bridge folder in the runner's workspace, without needing a channel.

    For runs that use no tool calling: a folder left behind by an interrupted tool-calling run of the same
    conversation must not outlive the next run, whatever that run's configuration. The caller holds the pod lock.
    Best effort: a failing exec is logged, never raised.
    """
    try:
        result = runner(_sweep_argv(""), None)
    except Exception as exc:  # noqa: BLE001 - housekeeping must never fail a run
        logger.warning("tool_call_channel: stale exchange sweep failed: %s: %s", type(exc).__name__, exc)
        return
    if result.exit_code != 0:
        logger.warning("tool_call_channel: stale exchange sweep exited with %s", result.exit_code)


@final
class ToolCallChannel:
    """Answers the tool calls of one script run from a daemon thread."""

    def __init__(
        self,
        runner: ExecRunner,
        exchange_dir: str,
        *,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        attempts: int = DEFAULT_EXEC_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        handlers: Mapping[str, ToolCallHandler] | None = None,
    ) -> None:
        if not _EXCHANGE_DIR_PATTERN.match(exchange_dir):
            raise ValueError(f"invalid exchange folder: {exchange_dir!r}")
        self.exchange_dir: str = exchange_dir
        self._runner: ExecRunner = runner
        self._poll_interval: float = poll_interval
        self._attempts: int = max(1, attempts)
        self._backoff_seconds: float = backoff_seconds
        self._handlers: Mapping[str, ToolCallHandler] = DEFAULT_HANDLERS if handlers is None else dict(handlers)
        self._handled: set[str] = set()
        self._stop_event: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ housekeeping

    def sweep(self) -> None:
        """Remove every other child of the bridge folder, so only this run's exchange folder is left."""
        self._exec_tolerantly("sweep", _sweep_argv(self.exchange_dir.split("/")[-1]))

    def cleanup(self, *, kill: bool) -> None:
        """Drop this run's exchange folder, optionally killing the script process it belongs to first.

        Housekeeping must never mask the outcome of the run, so a failing exec is logged, not raised.
        """
        if kill:
            self._exec_tolerantly(
                "kill", [*POD_PYTHON, KILL_SCRIPT, self.exchange_dir, SANDBOX_CMDLINE_MARKER, PROC_ROOT]
            )
        self._exec_tolerantly("cleanup", ["rm", "-rf", "--", self.exchange_dir])

    def _exec_tolerantly(self, what: str, argv: Sequence[str]) -> None:
        """Housekeeping exec. It runs after (or without) a running thread, so it backs off with a real sleep."""
        try:
            self._exec(argv, interruptible=False)
        except ExecFailed as exc:
            logger.warning("tool_call_channel: %s could not be completed: %s", what, exc)

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        """Start the polling thread; a second call is a no-op."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        thread = threading.Thread(target=self._run, name="codemie-tool-call-channel", daemon=True)
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        """Signal the polling thread and join it. Idempotent."""
        self._stop_event.set()
        thread = self._thread
        self._thread = None
        if thread is None:
            return
        thread.join(timeout=STOP_JOIN_TIMEOUT_SECONDS)
        if thread.is_alive():
            logger.warning("tool_call_channel: polling thread did not stop within %ss", STOP_JOIN_TIMEOUT_SECONDS)

    # ------------------------------------------------------------------ polling

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                pending = [item for item in self._poll() if item.call_id not in self._handled]
                for item in pending:
                    self._respond(item)
                if not pending:
                    self._stop_event.wait(self._poll_interval)
        except ExecFailed as exc:
            logger.warning("tool_call_channel: stopping after failed sandbox exec: %s", exc)
            self._mark_unavailable()
        except Exception:  # noqa: BLE001 - a dying daemon thread must not leave the script waiting in silence
            logger.exception("tool_call_channel: stopping after unexpected error")
            self._mark_unavailable()

    def _mark_unavailable(self) -> None:
        """Best effort, one attempt: tell the SDK nobody will answer, so it fails fast instead of timing out."""
        if self._stop_event.is_set():
            return
        try:
            self._exec(
                ["sh", "-c", _MARK_UNAVAILABLE_SH, "codemie", f"{self.exchange_dir}/{UNAVAILABLE_MARKER_NAME}"],
                attempts=1,
            )
        except ExecFailed as exc:
            logger.warning("tool_call_channel: could not mark the channel unavailable: %s", exc)

    def _poll(self) -> list[_PendingRequest]:
        result = self._exec(
            [*POD_PYTHON, _POLL_SCRIPT, self.exchange_dir, str(MAX_PAYLOAD_BYTES), REQ_PREFIX, FILE_SUFFIX]
        )
        try:
            parsed: object = json.loads(result.stdout or "[]")
        except ValueError:
            logger.warning("tool_call_channel: poll returned unparseable output")
            return []
        if not isinstance(parsed, list):
            return []
        return [item for item in (self._as_request(entry) for entry in parsed) if item is not None]

    @staticmethod
    def _as_request(entry: object) -> _PendingRequest | None:
        if not isinstance(entry, dict):
            return None
        name = entry.get("name")
        if not isinstance(name, str) or not name.startswith(REQ_PREFIX) or not name.endswith(FILE_SUFFIX):
            return None
        call_id = name[len(REQ_PREFIX) : len(name) - len(FILE_SUFFIX)]
        body = entry.get("body")
        return _PendingRequest(
            call_id=call_id,
            file_name=name,
            oversize=entry.get("oversize") is True,
            body=body if isinstance(body, str) else None,
            bad_encoding=entry.get("bad_encoding") is True,
        )

    # ------------------------------------------------------------------ handling

    def _respond(self, request: _PendingRequest) -> None:
        body = self._build_response(request)
        tmp_name = f"tmp.resp.{request.call_id}"
        self._exec(
            [
                *POD_PYTHON,
                WRITE_RESPONSE_SCRIPT,
                f"{self.exchange_dir}/{tmp_name}",
                f"{self.exchange_dir}/{RESP_PREFIX}{request.call_id}{FILE_SUFFIX}",
                f"{self.exchange_dir}/{request.file_name}",
                str(len(body)),
            ],
            stdin=body,
        )
        self._handled.add(request.call_id)

    def _build_response(self, request: _PendingRequest) -> bytes:
        """Answer one request. Total: whatever goes wrong becomes an error response, never a dead thread."""
        try:
            return self._answer(request)
        except Exception as exc:  # noqa: BLE001 - one bad request or handler must not end the channel
            logger.warning("tool_call_channel: request %s failed: %s", request.call_id, type(exc).__name__)
            return _error_bytes(request.call_id, "internal_error", "the request could not be processed")

    def _answer(self, request: _PendingRequest) -> bytes:
        if request.oversize:
            return _error_bytes(request.call_id, "payload_too_large", f"request exceeds {MAX_PAYLOAD_BYTES} bytes")
        if request.bad_encoding:
            return _error_bytes(request.call_id, "bad_request", "request is not valid UTF-8")
        op, payload, problem = _parse_request(request.body)
        if problem is not None:
            return _error_bytes(request.call_id, "bad_request", problem)
        handler = self._handlers.get(op)
        if handler is None:
            return _error_bytes(request.call_id, "unknown_op", f"unknown operation: {op!r}")
        try:
            result = handler(payload)
            encoded = json.dumps({"v": PROTOCOL_VERSION, "id": request.call_id, "ok": True, "result": result}).encode(
                "utf-8"
            )
        except (TypeError, ValueError) as exc:
            logger.warning("tool_call_channel: handler %r produced no serialisable result: %s", op, type(exc).__name__)
            return _error_bytes(request.call_id, "bad_request", f"operation {op!r} produced no valid result")
        if len(encoded) > MAX_PAYLOAD_BYTES:
            return _error_bytes(request.call_id, "payload_too_large", f"result exceeds {MAX_PAYLOAD_BYTES} bytes")
        return encoded

    # ------------------------------------------------------------------ exec

    def _exec(
        self,
        argv: Sequence[str],
        stdin: bytes | None = None,
        *,
        attempts: int | None = None,
        interruptible: bool = True,
    ) -> ExecResult:
        """Run one pod-side command with retries.

        ``interruptible`` backoff waits on the stop event so ``stop()`` is prompt; housekeeping runs after the event
        is set, where waiting on it would return at once and fire every retry back to back, so it sleeps instead.
        """
        limit = self._attempts if attempts is None else max(1, attempts)
        last: str = "no attempt was made"
        for attempt in range(1, limit + 1):
            try:
                result = self._runner(argv, stdin)
            except Exception as exc:  # noqa: BLE001 - any transport failure is retried, then gives up
                last = f"{type(exc).__name__}: {exc}"
            else:
                if result.exit_code == 0:
                    return result
                last = f"exit code {result.exit_code}: {result.stderr.strip()[:200]}"
            if attempt < limit:
                delay = self._backoff_seconds * attempt
                if interruptible:
                    self._stop_event.wait(delay)
                else:
                    time.sleep(delay)
        raise ExecFailed(f"{argv[0]} failed after {limit} attempts ({last})")


def _parse_request(body: str | None) -> tuple[str, Mapping[str, object], str | None]:
    """Return ``(op, payload, problem)``; ``problem`` is non-None when the request is unusable."""
    if body is None:
        return "", {}, "request could not be read"
    try:
        parsed: object = json.loads(body)
    except (ValueError, RecursionError):
        return "", {}, "request is not valid JSON"
    if not isinstance(parsed, dict):
        return "", {}, "request is not a JSON object"
    if parsed.get("v") != PROTOCOL_VERSION:
        return "", {}, f"unsupported protocol version: {parsed.get('v')!r}"
    op = parsed.get("op")
    if not isinstance(op, str) or not op:
        return "", {}, "request has no operation"
    payload = parsed.get("payload")
    if not isinstance(payload, dict):
        return op, {}, "request payload is not a JSON object"
    return op, payload, None


def _error_bytes(call_id: str, code: str, message: str) -> bytes:
    return json.dumps(
        {"v": PROTOCOL_VERSION, "id": call_id, "ok": False, "error": {"code": code, "message": message}}
    ).encode("utf-8")
