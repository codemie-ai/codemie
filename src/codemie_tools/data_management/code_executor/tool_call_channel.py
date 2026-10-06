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

"""Backend side of the script tool-call bridge: the transport.

A :class:`ToolCallChannel` polls the exchange folder of one run through an :class:`ExecRunner`, hands the requests
the runtime SDK leaves there to a :class:`RequestDispatcher` and writes the responses back. Every pod-side action is
an independent exec that uses only ``sh``, ``rm`` and ``python3``, which lets tests drive the very same argv against a
local directory.

Threads: the handlers run in a thread pool of ``max_parallel_calls`` workers, **every exec runs on the one polling
thread** (an exec client is not safe to share, see :mod:`exec_runner`). The polling thread reads new requests, hands
them to the pool, takes finished responses from a queue and writes them.

All paths the channel emits are relative to the workspace root; supplying that working directory is the runner's job.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import final

from codemie_tools.data_management.code_executor.exec_runner import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_EXEC_ATTEMPTS,
    ExecFailed,
    ExecResult,
    ExecRunner,
    run_with_retries,
)
from codemie_tools.data_management.code_executor.pod_scripts import load_pod_script
from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import (
    BRIDGE_DIR_NAME,
    CODE_INTERNAL_ERROR,
    FILE_SUFFIX,
    REQ_PREFIX,
    RESP_PREFIX,
    UNAVAILABLE_MARKER_NAME,
)
from codemie_tools.data_management.code_executor.tool_call_protocol import (
    CallOutcome,
    DispatchResult,
    PendingRequest,
    RequestDispatcher,
    error_response,
)

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS: float = 0.5
#: Cap of the growing pause between polls while the sandbox exec keeps failing.
POLL_BACKOFF_CAP_SECONDS: float = 5.0
#: The same exec failure is logged at warning at most this often; the rest goes to debug.
FAILURE_LOG_INTERVAL_SECONDS: float = 30.0
STOP_JOIN_TIMEOUT_SECONDS: float = 10.0

_EXCHANGE_NAME_PATTERN: re.Pattern[str] = re.compile(r"^[0-9]+-[0-9a-f]{32}$")
_EXCHANGE_DIR_PATTERN: re.Pattern[str] = re.compile(rf"^{re.escape(BRIDGE_DIR_NAME)}/[0-9]+-[0-9a-f]{{32}}$")

#: How every pod-side script is started. The exec runs in the workspace root, which the script under execution
#: can write to; without -I (isolated mode) that directory is on sys.path, so a workspace file named ``json.py``
#: or ``os.py`` would run in the backend's exec process, outside the sandbox guard.
POD_PYTHON: tuple[str, ...] = ("python3", "-I", "-c")

_POLL_SCRIPT: str = load_pod_script("poll")
WRITE_RESPONSE_SCRIPT: str = load_pod_script("write_response")

# Marks the exchange folder as served by no one, so the SDK fails fast instead of waiting out its call timeout.
# argv: <marker path>.
_MARK_UNAVAILABLE_SH = ': > "$1"'

#: Called with the call's :class:`CallOutcome` once it is settled: its response was written, or the script stopped
#: waiting, or the run ended first (``delivered`` and ``withdrawn`` say which).
SettledCallback = Callable[[CallOutcome], None]


def new_exchange_dir_name() -> str:
    """Name for one run's exchange folder: ``<epoch>-<uuid4hex>``."""
    return f"{int(time.time())}-{uuid.uuid4().hex}"


def exchange_dir_path(name: str) -> str:
    """Workspace-relative path of the exchange folder called ``name``."""
    if not _EXCHANGE_NAME_PATTERN.match(name):
        raise ValueError(f"invalid exchange folder name: {name!r}")
    return f"{BRIDGE_DIR_NAME}/{name}"


@final
@dataclass(frozen=True)
class _PollSnapshot:
    """What one poll exec saw: the requests that are new and whether the script's done marker exists."""

    requests: list[PendingRequest]
    done: bool
    #: Ids of the requests the poll saw that the channel is already serving (reported by name only).
    known: frozenset[str] = frozenset()


@final
class _WorkerPool:
    """A few daemon threads that run submitted jobs; threads start on demand, up to ``size``.

    Not ``ThreadPoolExecutor``: its workers are joined when the interpreter exits, so a tool call stuck in a slow
    third-party request would hold up the backend's shutdown. These threads are daemons, like the channel's own.
    """

    def __init__(self, size: int, name_prefix: str) -> None:
        self._size: int = max(1, size)
        self._name_prefix: str = name_prefix
        self._jobs: queue.SimpleQueue[Callable[[], None] | None] = queue.SimpleQueue()
        self._threads: list[threading.Thread] = []
        self._closed: bool = False

    def submit(self, job: Callable[[], None]) -> None:
        """Queue ``job``; raises ``RuntimeError`` once the pool is shut down. Called from one thread only."""
        if self._closed:
            raise RuntimeError("the worker pool is shut down")
        self._jobs.put(job)
        if len(self._threads) < self._size:
            thread = threading.Thread(target=self._work, name=f"{self._name_prefix}-{len(self._threads)}", daemon=True)
            self._threads.append(thread)
            thread.start()

    def shutdown(self) -> None:
        """Drop the jobs that have not started and let the threads end after the job they are running."""
        self._closed = True
        while True:
            try:
                self._jobs.get_nowait()
            except queue.Empty:
                break
        for _ in self._threads:
            self._jobs.put(None)

    def _work(self) -> None:
        while (job := self._jobs.get()) is not None:
            try:
                job()
            except Exception:  # noqa: BLE001 - a job reports its own failures; a worker must survive a bug
                logger.exception("tool_call_channel: a worker job failed")


@final
class ToolCallChannel:
    """Answers the tool calls of one script run from a daemon thread."""

    def __init__(
        self,
        runner: ExecRunner,
        exchange_dir: str,
        *,
        dispatcher: RequestDispatcher,
        max_parallel_calls: int = 1,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        attempts: int = DEFAULT_EXEC_ATTEMPTS,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        done_path: str | None = None,
        deadline: float | None = None,
        poll_backoff_cap: float = POLL_BACKOFF_CAP_SECONDS,
        on_settled: SettledCallback | None = None,
    ) -> None:
        """``done_path``: the script's done marker, relative to the workspace root; when given, each poll also
        reports whether it exists (see :meth:`wait_until_done`). ``deadline``: the run's ``time.monotonic()``
        deadline; the channel stops polling and gives up at it. ``on_settled`` is told how each call ended.
        """
        if not _EXCHANGE_DIR_PATTERN.match(exchange_dir):
            raise ValueError(f"invalid exchange folder: {exchange_dir!r}")
        self.exchange_dir: str = exchange_dir
        self._runner: ExecRunner = runner
        self._dispatcher: RequestDispatcher = dispatcher
        self._max_parallel_calls: int = max(1, max_parallel_calls)
        self._poll_interval: float = poll_interval
        self._attempts: int = max(1, attempts)
        self._backoff_seconds: float = backoff_seconds
        self._done_path: str | None = done_path
        self._deadline: float | None = deadline
        self._poll_backoff_cap: float = max(poll_backoff_cap, backoff_seconds)
        self._on_settled: SettledCallback | None = on_settled
        # Touched by the polling thread only: the calls being served, and those already answered.
        self._in_flight: set[str] = set()
        self._answered: set[str] = set()
        # The script stopped waiting for these (its request file is gone): they must not start or be answered.
        self._cancel_events: dict[str, threading.Event] = {}
        self._withdrawn: set[str] = set()
        # Filled by the pool's workers, drained by the polling thread (deque appends and pops are atomic).
        self._finished: deque[tuple[PendingRequest, DispatchResult]] = deque()
        self._stop_event: threading.Event = threading.Event()
        self._wake_event: threading.Event = threading.Event()
        self._done_event: threading.Event = threading.Event()
        self._ended_event: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None
        self._pool: _WorkerPool | None = None
        self._last_failure_log: float = float("-inf")

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        """Start the polling thread; a second call is a no-op."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._ended_event.clear()
        self._pool = _WorkerPool(self._max_parallel_calls, "codemie-tool-call")
        thread = threading.Thread(target=self._run, name="codemie-tool-call-channel", daemon=True)
        self._thread = thread
        thread.start()

    def stop(self) -> None:
        """Stop the polling thread and join it, and drop the calls that have not started. Idempotent.

        A call still running in the pool is not waited for: the run is over, so its answer would reach nobody, and it
        is settled as not delivered.
        """
        self._stop_event.set()
        self._wake_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=STOP_JOIN_TIMEOUT_SECONDS)
            if thread.is_alive():
                logger.warning("tool_call_channel: polling thread did not stop within %ss", STOP_JOIN_TIMEOUT_SECONDS)
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown()

    def remove_exchange_dir(self) -> None:
        """Drop this run's exchange folder. Housekeeping must never mask the outcome of the run: a failing exec is
        logged, not raised, and nothing is done while the polling thread is still running (two execs must not overlap;
        the Job, and the folder with it, is deleted right after the run anyway).
        """
        thread = self._thread
        if thread is not None and thread.is_alive():
            logger.warning("tool_call_channel: exchange folder not removed, the polling thread is still running")
            return
        try:
            run_with_retries(
                self._runner,
                ["rm", "-rf", "--", self.exchange_dir],
                attempts=self._attempts,
                backoff_seconds=self._backoff_seconds,
            )
        except ExecFailed as exc:
            logger.warning("tool_call_channel: cleanup could not be completed: %s", exc)

    def wait_until_done(self, deadline: float) -> bool:
        """Block until a poll saw the done marker; ``False`` on ``deadline`` or when the channel stopped polling first.

        On ``False`` the caller falls back to its own done check. Needs ``done_path``.
        """
        if self._thread is None:
            return False
        while True:
            if self._done_event.is_set():
                return True
            if self._ended_event.is_set():
                return self._done_event.is_set()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            self._done_event.wait(min(0.2, remaining))

    # ------------------------------------------------------------------ the polling thread

    def _run(self) -> None:
        try:
            self._tick_loop()
        except Exception:  # noqa: BLE001 - a dying daemon thread must not leave the script waiting in silence
            logger.exception("tool_call_channel: stopping after unexpected error")
            self._mark_unavailable()
        finally:
            self._settle_unfinished()
            self._ended_event.set()

    def _tick_loop(self) -> None:
        """One tick: write the finished responses, poll for new requests and the done marker, hand requests to the pool.

        Exec failures never end the loop: the tick backs off with a growing pause and tries again.
        """
        failure_pause: float = 0.0
        failures: int = 0
        while not self._stop_event.is_set():
            if self._past_deadline():
                logger.warning("tool_call_channel: the run's deadline passed, tool calling is over")
                self._mark_unavailable()
                return
            try:
                self._write_finished()
                sent = frozenset(self._in_flight)
                snapshot = self._poll()
            except ExecFailed as exc:
                failures += 1
                failure_pause = min(max(failure_pause * 2, self._backoff_seconds), self._poll_backoff_cap)
                self._log_exec_failure(exc, failures)
                self._sleep(failure_pause)
                continue
            if failures:
                logger.info("tool_call_channel: polling recovered after %d failed ticks", failures)
                failures = 0
                failure_pause = 0.0
            if snapshot.done:
                self._done_event.set()
                return
            self._withdraw(sent - snapshot.known)
            self._submit(snapshot.requests)
            self._sleep(self._poll_interval)

    def _past_deadline(self) -> bool:
        return self._deadline is not None and time.monotonic() >= self._deadline

    def _run_is_over(self) -> bool:
        return self._stop_event.is_set() or self._done_event.is_set()

    def _sleep(self, seconds: float) -> None:
        """Wait up to ``seconds`` (never past the deadline); stop() and a finished call wake the loop at once."""
        if self._deadline is not None:
            seconds = min(seconds, max(0.0, self._deadline - time.monotonic()))
        self._wake_event.wait(seconds)
        self._wake_event.clear()

    def _log_exec_failure(self, exc: ExecFailed, failures: int) -> None:
        """Warning for the first failure and then at most every FAILURE_LOG_INTERVAL_SECONDS; debug in between."""
        now = time.monotonic()
        if now - self._last_failure_log >= FAILURE_LOG_INTERVAL_SECONDS:
            self._last_failure_log = now
            logger.warning(
                "tool_call_channel: exec failed (%d in a row), retrying with a growing pause: %s", failures, exc
            )
        else:
            logger.debug("tool_call_channel: exec failed again (%d in a row): %s", failures, exc)

    def _exec(self, argv: Sequence[str], stdin: bytes | None = None, *, attempts: int | None = None) -> ExecResult:
        """One pod-side command with retries; the pause between tries wakes at once when the channel stops."""
        return run_with_retries(
            self._runner,
            argv,
            stdin,
            attempts=self._attempts if attempts is None else attempts,
            backoff_seconds=self._backoff_seconds,
            sleep=self._stop_event.wait,
        )

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

    # ------------------------------------------------------------------ polling

    def _poll(self) -> _PollSnapshot:
        argv = [
            *POD_PYTHON,
            _POLL_SCRIPT,
            self.exchange_dir,
            str(self._dispatcher.max_payload_bytes),
            REQ_PREFIX,
            FILE_SUFFIX,
            self._done_path or "",
            ",".join(sorted(self._in_flight)),
        ]
        result = self._exec(argv)
        try:
            parsed: object = json.loads(result.stdout or "{}")
        except ValueError:
            logger.warning("tool_call_channel: poll returned unparseable output")
            return _PollSnapshot(requests=[], done=False)
        if not isinstance(parsed, dict):
            return _PollSnapshot(requests=[], done=False)
        entries = parsed.get("requests")
        requests = (
            [item for item in (self._as_request(entry) for entry in entries) if item is not None]
            if isinstance(entries, list)
            else []
        )
        known = (
            frozenset(call_id for entry in entries if (call_id := self._known_id(entry)) is not None)
            if isinstance(entries, list)
            else frozenset()
        )
        return _PollSnapshot(requests=requests, done=parsed.get("done") is True, known=known)

    @staticmethod
    def _known_id(entry: object) -> str | None:
        """The id of a request the poll reported by name only (one the channel is already serving)."""
        if not isinstance(entry, dict) or entry.get("known") is not True:
            return None
        name = entry.get("name")
        if not isinstance(name, str) or not name.startswith(REQ_PREFIX) or not name.endswith(FILE_SUFFIX):
            return None
        return name[len(REQ_PREFIX) : len(name) - len(FILE_SUFFIX)]

    @staticmethod
    def _as_request(entry: object) -> PendingRequest | None:
        """A request the poll read in full; ``None`` for anything else, including a request reported by name only."""
        if not isinstance(entry, dict) or entry.get("known") is True:
            return None
        name = entry.get("name")
        if not isinstance(name, str) or not name.startswith(REQ_PREFIX) or not name.endswith(FILE_SUFFIX):
            return None
        body = entry.get("body")
        return PendingRequest(
            call_id=name[len(REQ_PREFIX) : len(name) - len(FILE_SUFFIX)],
            file_name=name,
            oversize=entry.get("oversize") is True,
            body=body if isinstance(body, str) else None,
            bad_encoding=entry.get("bad_encoding") is True,
        )

    # ------------------------------------------------------------------ serving

    def _submit(self, requests: Sequence[PendingRequest]) -> None:
        """Hand every request not yet seen to the pool; up to ``max_parallel_calls`` of them run at once."""
        pool = self._pool
        if pool is None:
            return
        for request in requests:
            if request.call_id in self._in_flight or request.call_id in self._answered:
                continue
            self._in_flight.add(request.call_id)
            cancelled = self._cancel_events.setdefault(request.call_id, threading.Event())
            try:
                pool.submit(lambda request=request, cancelled=cancelled: self._serve(request, cancelled))
            except RuntimeError:
                # The pool was shut down by stop(): the run is over.
                self._in_flight.discard(request.call_id)
                self._cancel_events.pop(request.call_id, None)
                return

    def _withdraw(self, call_ids: frozenset[str]) -> None:
        """The script removed these request files, so it gave up on them: tell the calls, which stops one that has not
        started and frees the place it waits for. A call already running cannot be stopped; its answer is dropped."""
        for call_id in call_ids:
            if call_id in self._withdrawn:
                continue
            self._withdrawn.add(call_id)
            event = self._cancel_events.get(call_id)
            if event is not None:
                event.set()
            logger.info("tool_call_channel: the script stopped waiting for call %s", call_id)

    def _serve(self, request: PendingRequest, cancelled: threading.Event) -> None:
        """Runs in a pool worker: build the response and queue it for the polling thread to write."""
        try:
            result = self._dispatcher.handle(request, cancelled)
        except Exception:  # noqa: BLE001 - handle is total; this keeps a request from staying unanswered
            logger.exception("tool_call_channel: serving call %s failed", request.call_id)
            body = error_response(request.call_id, CODE_INTERNAL_ERROR, "the request could not be processed")
            result = DispatchResult(
                body=body,
                outcome=CallOutcome(request.call_id, None, None, CODE_INTERNAL_ERROR, 0.0, len(body)),
            )
        self._finished.append((request, result))
        self._wake_event.set()

    def _write_finished(self) -> None:
        """Write the responses the pool finished. A failed write keeps its response for the next tick.

        The answer to a call the script gave up on is not written: nobody reads it.
        """
        while self._finished and not self._run_is_over():
            request, result = self._finished.popleft()
            call_id = request.call_id
            if call_id in self._withdrawn:
                self._forget(call_id)
                self._settle(result.outcome.settled(delivered=False, withdrawn=True))
                continue
            try:
                self._write_response(request, result.body)
            except ExecFailed:
                self._finished.appendleft((request, result))
                raise
            self._forget(call_id)
            self._answered.add(call_id)
            self._settle(result.outcome.settled(delivered=True))

    def _forget(self, call_id: str) -> None:
        self._in_flight.discard(call_id)
        self._cancel_events.pop(call_id, None)
        self._withdrawn.discard(call_id)

    def _write_response(self, request: PendingRequest, body: bytes) -> None:
        """Publish one answer; the pod script also removes the request file. The call already ran, so only the write
        is retried."""
        tmp_name = f"tmp.resp.{request.call_id}"
        argv = [
            *POD_PYTHON,
            WRITE_RESPONSE_SCRIPT,
            f"{self.exchange_dir}/{tmp_name}",
            f"{self.exchange_dir}/{RESP_PREFIX}{request.call_id}{FILE_SUFFIX}",
            f"{self.exchange_dir}/{request.file_name}",
            str(len(body)),
        ]
        self._exec(argv, stdin=body)

    def _settle(self, outcome: CallOutcome) -> None:
        callback = self._on_settled
        if callback is None:
            return
        try:
            callback(outcome)
        except Exception:  # noqa: BLE001 - a listener must never break the channel
            logger.warning("tool_call_channel: the settled callback failed for call %s", outcome.call_id, exc_info=True)

    def _settle_unfinished(self) -> None:
        """The run is over: every call that was not answered is settled as not delivered, and no response is written."""
        unfinished = sorted(self._in_flight)
        withdrawn = set(self._withdrawn)
        self._in_flight.clear()
        self._finished.clear()
        # Tell every call still waiting for a place in the gate that nobody is left to read its answer: it leaves the
        # wait instead of starting a tool (possibly one with side effects) after the run is over.
        for cancelled in self._cancel_events.values():
            cancelled.set()
        self._cancel_events.clear()
        self._withdrawn.clear()
        for call_id in unfinished:
            logger.info(
                "tool_call_channel: call %s did not finish before the run ended; its answer is dropped", call_id
            )
            self._settle(
                CallOutcome(call_id, None, None, None, 0.0, 0, delivered=False, withdrawn=call_id in withdrawn)
            )
