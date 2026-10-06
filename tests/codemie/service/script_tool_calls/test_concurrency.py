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

"""The process-wide gate on tool runs made from workspace scripts."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from unittest.mock import patch

import pytest

from codemie.service.script_tool_calls import concurrency
from codemie.service.script_tool_calls.concurrency import ToolRunGate, process_gate
from codemie_tools.data_management.code_executor.tool_call_protocol import (
    PendingRequest,
    RequestDispatcher,
    ToolCallRefused,
)


def _request(op: str = "hold") -> PendingRequest:
    body = '{"v": 1, "id": "x", "op": "' + op + '", "payload": {}}'
    return PendingRequest(call_id="c1", file_name="req.c1.json", oversize=False, body=body)


def _decode(response: bytes) -> Mapping[str, object]:
    import json

    parsed = json.loads(response)
    assert isinstance(parsed, dict)
    return parsed


class TestToolRunGate:
    def test_at_most_the_limit_run_at_the_same_time_across_threads(self) -> None:
        gate = ToolRunGate(2)
        running = 0
        peak = 0
        lock = threading.Lock()

        def work() -> None:
            nonlocal running, peak
            with gate.hold():
                with lock:
                    running += 1
                    peak = max(peak, running)
                time.sleep(0.05)
                with lock:
                    running -= 1

        threads = [threading.Thread(target=work) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert peak == 2

    def test_a_place_is_released_when_the_body_raises(self) -> None:
        gate = ToolRunGate(1)

        with pytest.raises(RuntimeError):
            with gate.hold():
                raise RuntimeError("boom")

        with gate.hold():
            pass

    def test_a_nonsense_limit_still_allows_one_run(self) -> None:
        with ToolRunGate(0).hold():
            pass

    def test_without_a_call_deadline_the_gate_waits_for_a_free_place(self) -> None:
        gate = ToolRunGate(1)
        entered = threading.Event()

        def late() -> None:
            with gate.hold():
                entered.set()

        with gate.hold():
            thread = threading.Thread(target=late)
            thread.start()
            time.sleep(0.1)
            assert not entered.is_set()
        thread.join(timeout=5)

        assert entered.is_set()


class TestGateInsideADispatchedCall:
    """The gate reads the clock of the call being served, which the dispatcher sets."""

    @staticmethod
    def _dispatcher(gate: ToolRunGate, ran: list[str], time_left: float, margin: float = 2.0) -> RequestDispatcher:
        def handler(payload: Mapping[str, object]) -> object:
            with gate.hold():
                ran.append("ran")
                return {"ok": True}

        return RequestDispatcher({"hold": handler}, time_left=lambda: time_left, deadline_margin=margin)

    def test_a_call_that_waits_too_long_for_a_place_is_refused_and_never_started(self) -> None:
        gate = ToolRunGate(1)
        ran: list[str] = []
        dispatcher = self._dispatcher(gate, ran, time_left=2.3)  # 0.3 s of waiting is allowed

        with gate.hold():
            started = time.monotonic()
            response = _decode(dispatcher.dispatch(_request()))
            waited = time.monotonic() - started

        assert response["ok"] is False
        assert response["error"]["code"] == "deadline_exceeded"  # type: ignore[index]
        assert ran == []
        assert 0.2 < waited < 3.0, "the wait counts against the call's time"

    def test_a_call_whose_wait_leaves_too_little_time_is_refused_after_the_place_is_taken(self) -> None:
        gate = ToolRunGate(1)
        ran: list[str] = []
        clock = {"left": 10.0}

        def handler(payload: Mapping[str, object]) -> object:
            with gate.hold():
                ran.append("ran")
                return {"ok": True}

        dispatcher = RequestDispatcher({"hold": handler}, time_left=lambda: clock["left"], deadline_margin=2.0)
        original = gate._semaphore.acquire

        def acquire_then_run_out_of_time(*args: object, **kwargs: object) -> bool:
            result = original(*args, **kwargs)
            clock["left"] = 1.0  # the wait used up the time
            return result

        with patch.object(gate._semaphore, "acquire", side_effect=acquire_then_run_out_of_time):
            response = _decode(dispatcher.dispatch(_request()))

        assert response["error"]["code"] == "deadline_exceeded"  # type: ignore[index]
        assert ran == []
        assert gate._semaphore.acquire(blocking=False), "the place is given back"

    def test_a_call_with_plenty_of_time_runs(self) -> None:
        ran: list[str] = []
        dispatcher = self._dispatcher(ToolRunGate(1), ran, time_left=60.0)

        response = _decode(dispatcher.dispatch(_request()))

        assert response["ok"] is True
        assert ran == ["ran"]

    def test_the_refusal_is_a_tool_call_refused_for_the_handler_chain(self) -> None:
        gate = ToolRunGate(1)
        dispatcher_clock = {"left": 0.5}

        def handler(payload: Mapping[str, object]) -> object:
            with gate.hold():
                return {}

        # The dispatcher itself refuses before the handler when the margin is already gone; call the handler directly
        # inside a clock to see the gate's own refusal.
        from codemie_tools.data_management.code_executor.tool_call_protocol import CallDeadline, _current_deadline

        token = _current_deadline.set(CallDeadline(lambda: dispatcher_clock["left"], 2.0))
        try:
            with pytest.raises(ToolCallRefused) as excinfo:
                handler({})
        finally:
            _current_deadline.reset(token)

        assert excinfo.value.code == "deadline_exceeded"


class TestProcessGate:
    def test_it_is_one_gate_sized_by_the_backend_setting(self) -> None:
        process_gate.cache_clear()
        try:
            with patch.object(concurrency.config, "WORKSPACE_SCRIPT_TOOL_CALLS_MAX_CONCURRENT", 3):
                first = process_gate()
                second = process_gate()
                places = 0
                while first._semaphore.acquire(blocking=False):
                    places += 1
                for _ in range(places):
                    first._semaphore.release()

            assert first is second
            assert places == 3
        finally:
            process_gate.cache_clear()


class TestWithdrawnCallsAndOccupancy:
    def test_a_call_waiting_for_a_place_leaves_the_wait_when_the_script_stops_waiting(self) -> None:
        gate = ToolRunGate(1)
        cancelled = threading.Event()
        ran: list[str] = []

        def handler(payload: Mapping[str, object]) -> object:
            with gate.hold():
                ran.append("ran")
                return {}

        dispatcher = RequestDispatcher({"hold": handler})
        results: list[Mapping[str, object]] = []
        with gate.hold():
            thread = threading.Thread(
                target=lambda: results.append(_decode(dispatcher.handle(_request(), cancelled).body))
            )
            thread.start()
            time.sleep(0.2)
            assert gate.waiting == 1
            cancelled.set()
            thread.join(timeout=3)

        assert not thread.is_alive(), "a withdrawn call must stop waiting at once"
        assert results[0]["error"]["code"] == "timeout"  # type: ignore[index]
        assert ran == []
        assert gate.waiting == 0

    def test_a_call_withdrawn_before_it_took_a_place_never_takes_one(self) -> None:
        gate = ToolRunGate(1)
        cancelled = threading.Event()
        cancelled.set()
        dispatcher = RequestDispatcher({"hold": lambda payload: gate.hold().__enter__()})

        response = _decode(dispatcher.handle(_request(), cancelled).body)

        assert response["error"]["code"] == "timeout"  # type: ignore[index]
        assert gate.running == 0

    def test_running_and_waiting_are_counted(self) -> None:
        gate = ToolRunGate(1)
        assert (gate.limit, gate.running, gate.waiting) == (1, 0, 0)

        with gate.hold():
            assert gate.running == 1
            waiter = threading.Thread(target=lambda: gate.hold().__enter__())
            waiter.daemon = True
            waiter.start()
            time.sleep(0.2)
            assert gate.waiting == 1

        time.sleep(0.3)
        assert gate.waiting == 0

    def test_a_long_wait_is_logged(self) -> None:
        gate = ToolRunGate(1)
        with patch.object(concurrency, "_SLOW_WAIT_LOG_SECONDS", 0.0), patch.object(concurrency.logger, "info") as info:
            with gate.hold():
                pass

        info.assert_called_once()


class TestMaxHoldSeconds:
    def test_without_a_max_hold_the_place_is_held_until_the_body_returns(self) -> None:
        gate = ToolRunGate(1)
        with gate.hold():
            assert gate.running == 1
        assert gate.running == 0
        assert gate.overdue == 0

    def test_a_call_that_outlives_the_hold_limit_gives_back_its_place_while_still_running(self) -> None:
        gate = ToolRunGate(1, max_hold_seconds=0.1)
        entered = threading.Event()
        release = threading.Event()

        def slow() -> None:
            with gate.hold():
                entered.set()
                release.wait(timeout=10)

        thread = threading.Thread(target=slow)
        thread.start()
        try:
            assert entered.wait(timeout=5)
            # The place must become free again although the call above is still inside hold().
            with gate.hold():
                assert gate.overdue == 1
        finally:
            release.set()
            thread.join(timeout=10)

        assert not thread.is_alive()

    def test_a_normal_return_after_the_place_was_already_given_back_does_not_release_twice(self) -> None:
        gate = ToolRunGate(1, max_hold_seconds=0.05)

        with gate.hold():
            time.sleep(0.2)  # outlives the hold limit; the timer releases the place during the body

        # If the body's own exit had released a second time, this would be over-released (and acquire would then
        # let two bodies in at once) or would raise on a plain BoundedSemaphore.
        with gate.hold():
            pass
        assert gate.overdue == 1

    def test_the_warning_names_the_limit_and_is_logged_once(self) -> None:
        gate = ToolRunGate(1, max_hold_seconds=0.05)
        with patch.object(concurrency.logger, "warning") as warning:
            with gate.hold():
                time.sleep(0.2)

        warning.assert_called_once()
        message = warning.call_args.args[0] % warning.call_args.args[1:]
        assert "exceeded" in message

    def test_process_gate_uses_the_configured_hold_limit(self) -> None:
        process_gate.cache_clear()
        try:
            with patch.object(concurrency.config, "WORKSPACE_SCRIPT_TOOL_CALL_MAX_HOLD_SECONDS", 42.0):
                gate = process_gate()
            assert gate._max_hold_seconds == 42.0
        finally:
            process_gate.cache_clear()
