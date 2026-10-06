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

"""The process-wide limit on tool calls made from workspace scripts."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache

from codemie.configs import config, logger
from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import CODE_DEADLINE_EXCEEDED
from codemie_tools.data_management.code_executor.tool_call_protocol import (
    ToolCallRefused,
    current_call_deadline,
    ensure_not_cancelled,
)

_WAIT_SLICE_SECONDS: float = 0.1
#: A wait for a place at least this long is logged, so a crowded process is visible.
_SLOW_WAIT_LOG_SECONDS: float = 1.0


class ToolRunGate:
    """Bounds how many tool runs execute at the same time, across every script run of the process.

    Each run is held to its own ``maxParallelCalls``; without this shared limit the worst case would be the number of
    runs times that value. The wait for a free place counts against the call's time: a call whose wait leaves too
    little of the run's time is refused ``deadline_exceeded`` and never started. A call whose script stopped waiting
    (the request was withdrawn) leaves the wait at once, so it neither starts nor holds a place.

    Nothing can stop a tool call that is already running (there is no way to interrupt arbitrary Python code or a
    network call inside it); a hung call therefore keeps its worker thread forever. Without ``max_hold_seconds`` it
    would also keep its place forever, and enough hung calls (one per run, up to the number of runs sharing this
    process) would starve every run on the replica until it restarts. ``max_hold_seconds`` bounds that: past it the
    place is given back for other calls to use, whether or not the call itself has returned. This does not free the
    thread or close whatever connection the call is waiting on - the number of calls truly running can then exceed
    ``limit`` - it only keeps the gate itself from jamming.
    """

    def __init__(self, limit: int, max_hold_seconds: float | None = None) -> None:
        self._limit: int = max(1, limit)
        self._max_hold_seconds: float | None = max_hold_seconds
        self._semaphore: threading.BoundedSemaphore = threading.BoundedSemaphore(self._limit)
        self._lock: threading.Lock = threading.Lock()
        self._running: int = 0
        self._waiting: int = 0
        #: Places given back early because the call holding them ran past ``max_hold_seconds``; for the occupancy log.
        self._overdue: int = 0

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def running(self) -> int:
        """Tool runs holding a place right now."""
        return self._running

    @property
    def waiting(self) -> int:
        """Calls waiting for a place right now."""
        return self._waiting

    @property
    def overdue(self) -> int:
        """How many places were ever given back early because the call holding them ran past the hold limit."""
        return self._overdue

    def _acquire(self, remaining: float | None) -> bool:
        """Wait for a place for at most ``remaining`` seconds (``None``: as long as it takes), in short slices so
        that a withdrawn call stops waiting."""
        give_up_at = None if remaining is None else time.monotonic() + max(remaining, 0.0)
        while True:
            ensure_not_cancelled()
            if self._semaphore.acquire(blocking=False):
                return True
            now = time.monotonic()
            if give_up_at is not None and now >= give_up_at:
                return False
            wait = _WAIT_SLICE_SECONDS if give_up_at is None else min(_WAIT_SLICE_SECONDS, give_up_at - now)
            if self._semaphore.acquire(timeout=wait):
                return True

    @contextmanager
    def hold(self) -> Iterator[None]:
        """Wait for a free place, then run the body holding it. Takes the clock of the call being served, if any.

        Past ``max_hold_seconds`` the place is released on a timer, whether or not the body has returned (see the
        class docstring); the body itself keeps running and its own return is then a no-op for the gate.
        """
        deadline = current_call_deadline()
        remaining = None if deadline is None else deadline.remaining()
        started = time.monotonic()
        with self._lock:
            self._waiting += 1
        try:
            acquired = self._acquire(remaining)
        finally:
            with self._lock:
                self._waiting -= 1
        waited = time.monotonic() - started
        if waited >= _SLOW_WAIT_LOG_SECONDS:
            logger.info(
                "script tool-call gate: waited %.1fs for a place (%d of %d in use, %d waiting)",
                waited,
                self._running,
                self._limit,
                self._waiting,
            )
        if not acquired:
            raise ToolCallRefused(
                CODE_DEADLINE_EXCEEDED,
                "no free place became available before the run's time ran out; the call was not started",
            )
        with self._lock:
            self._running += 1
        released = threading.Event()
        timer = self._start_hold_timer(released)
        try:
            if deadline is not None:
                deadline.ensure()
            ensure_not_cancelled()
            yield
        finally:
            if timer is not None:
                timer.cancel()
            self._release_once(released)

    def _start_hold_timer(self, released: threading.Event) -> threading.Timer | None:
        if self._max_hold_seconds is None:
            return None

        def give_back() -> None:
            if self._release_once(released, overdue=True):
                logger.warning(
                    "script tool-call gate: a call exceeded %.0fs and gave up its place while still running; "
                    "the gate may now hold more than its limit of %d running calls",
                    self._max_hold_seconds,
                    self._limit,
                )

        timer = threading.Timer(self._max_hold_seconds, give_back)
        timer.daemon = True
        timer.start()
        return timer

    def _release_once(self, released: threading.Event, *, overdue: bool = False) -> bool:
        """Release the place unless it already was (by the timer, or by this call returning first)."""
        if released.is_set():
            return False
        released.set()
        with self._lock:
            self._running -= 1
            if overdue:
                self._overdue += 1
        self._semaphore.release()
        return True


@cache
def process_gate() -> ToolRunGate:
    """The one gate of this process: ``WORKSPACE_SCRIPT_TOOL_CALLS_MAX_CONCURRENT`` places, each given back after
    ``WORKSPACE_SCRIPT_TOOL_CALL_MAX_HOLD_SECONDS`` even if the call holding it has not returned."""
    return ToolRunGate(
        config.WORKSPACE_SCRIPT_TOOL_CALLS_MAX_CONCURRENT,
        max_hold_seconds=config.WORKSPACE_SCRIPT_TOOL_CALL_MAX_HOLD_SECONDS,
    )
