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

import asyncio
import contextvars
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from codemie.enterprise.litellm import project_member_sync_executor as executor_module
from codemie.enterprise.litellm.project_member_sync_executor import (
    ProjectMemberSyncExecutor,
    get_project_member_sync_executor,
    shutdown_project_member_sync_executor,
)

WAIT_TIMEOUT = 5.0
_request_marker: contextvars.ContextVar[str] = contextvars.ContextVar("_request_marker", default="unset")


class _RecordingPool(ThreadPoolExecutor):
    """ThreadPoolExecutor that records every instance and every submit."""

    instances: list[_RecordingPool] = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.submits = 0
        _RecordingPool.instances.append(self)

    def submit(self, fn, /, *args, **kwargs):
        self.submits += 1
        return super().submit(fn, *args, **kwargs)


class _FailingSubmitPool(_RecordingPool):
    def submit(self, fn, /, *args, **kwargs):
        self.submits += 1
        raise RuntimeError("submit failed")


@pytest.fixture
def recording_pool(monkeypatch) -> list[_RecordingPool]:
    instances: list[_RecordingPool] = []
    monkeypatch.setattr(_RecordingPool, "instances", instances)
    monkeypatch.setattr(executor_module, "ThreadPoolExecutor", _RecordingPool)
    return instances


async def _wait_until(predicate: Callable[[], bool], timeout: float = WAIT_TIMEOUT) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition was not met in time")
        await asyncio.sleep(0.005)


class _BlockingCalls:
    """Blocking worker functions held open by threading.Events."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.in_flight = 0
        self.max_in_flight = 0
        self.started: dict[str, threading.Event] = {}
        self.release: dict[str, threading.Event] = {}
        self.finished: dict[str, threading.Event] = {}

    def prepare(self, name: str) -> None:
        self.started[name] = threading.Event()
        self.release[name] = threading.Event()
        self.finished[name] = threading.Event()

    def call(self, *, name: str) -> str:
        with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.started[name].set()
        try:
            self.release[name].wait(WAIT_TIMEOUT)
            return name
        finally:
            with self._lock:
                self.in_flight -= 1
            self.finished[name].set()

    def release_all(self) -> None:
        for event in self.release.values():
            event.set()


@pytest.mark.asyncio
async def test_at_most_workers_calls_are_in_flight(recording_pool):
    executor = ProjectMemberSyncExecutor(workers=2)
    calls = _BlockingCalls()
    names = [f"call-{i}" for i in range(5)]
    for name in names:
        calls.prepare(name)
    try:
        tasks = [asyncio.create_task(executor.run(calls.call, name=name)) for name in names]
        await _wait_until(lambda: calls.in_flight == 2)
        # Give the remaining three a chance to (wrongly) get submitted.
        await asyncio.sleep(0.05)

        assert recording_pool[0].submits == 2
        assert calls.in_flight == 2

        calls.release_all()
        results = await asyncio.wait_for(asyncio.gather(*tasks), WAIT_TIMEOUT)

        assert results == names
        assert calls.max_in_flight == 2
        assert recording_pool[0].submits == 5
    finally:
        calls.release_all()
        await executor.shutdown()


@pytest.mark.asyncio
async def test_normal_completion_frees_the_slot(recording_pool):
    executor = ProjectMemberSyncExecutor(workers=2)
    calls = _BlockingCalls()
    try:
        for i in range(6):
            assert await asyncio.wait_for(executor.run(lambda *, value: value * 2, value=i), WAIT_TIMEOUT) == i * 2

        calls.prepare("a")
        calls.prepare("b")
        tasks = [asyncio.create_task(executor.run(calls.call, name=name)) for name in ("a", "b")]
        await _wait_until(lambda: calls.started["a"].is_set() and calls.started["b"].is_set())
        calls.release_all()
        assert await asyncio.wait_for(asyncio.gather(*tasks), WAIT_TIMEOUT) == ["a", "b"]
    finally:
        calls.release_all()
        await executor.shutdown()


@pytest.mark.asyncio
async def test_worker_exception_propagates_and_frees_the_slot(recording_pool):
    executor = ProjectMemberSyncExecutor(workers=2)

    def boom() -> None:
        raise ValueError("call failed")

    try:
        for _ in range(3):
            with pytest.raises(ValueError, match="call failed"):
                await asyncio.wait_for(executor.run(boom), WAIT_TIMEOUT)
        assert await asyncio.wait_for(executor.run(lambda: "ok"), WAIT_TIMEOUT) == "ok"
    finally:
        await executor.shutdown()


@pytest.mark.asyncio
async def test_failing_submit_releases_the_slot_and_reraises(monkeypatch):
    monkeypatch.setattr(executor_module, "ThreadPoolExecutor", _FailingSubmitPool)
    executor = ProjectMemberSyncExecutor(workers=2)
    try:
        # More attempts than slots: a leaked slot would make the third attempt hang.
        for _ in range(3):
            with pytest.raises(RuntimeError, match="submit failed"):
                await asyncio.wait_for(executor.run(lambda: "never"), WAIT_TIMEOUT)

        monkeypatch.setattr(_FailingSubmitPool, "submit", _RecordingPool.submit)
        results = await asyncio.wait_for(
            asyncio.gather(executor.run(lambda: 1), executor.run(lambda: 2)),
            WAIT_TIMEOUT,
        )
        assert results == [1, 2]
    finally:
        await executor.shutdown()


@pytest.mark.asyncio
async def test_cancelled_awaiter_keeps_slot_until_worker_finishes(recording_pool):
    executor = ProjectMemberSyncExecutor(workers=2)
    calls = _BlockingCalls()
    for name in ("a", "b", "c"):
        calls.prepare(name)
    try:
        task_a = asyncio.create_task(executor.run(calls.call, name="a"))
        task_b = asyncio.create_task(executor.run(calls.call, name="b"))
        await _wait_until(lambda: calls.in_flight == 2)

        task_a.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task_a

        task_c = asyncio.create_task(executor.run(calls.call, name="c"))
        await asyncio.sleep(0.05)
        # The cancelled call is still running in its worker and still holds its slot.
        assert not calls.finished["a"].is_set()
        assert not calls.started["c"].is_set()
        assert recording_pool[0].submits == 2

        calls.release["a"].set()
        await _wait_until(calls.started["c"].is_set)
        assert calls.finished["a"].is_set()

        calls.release_all()
        assert await asyncio.wait_for(asyncio.gather(task_b, task_c), WAIT_TIMEOUT) == ["b", "c"]
    finally:
        calls.release_all()
        await executor.shutdown()


@pytest.mark.asyncio
async def test_contextvar_and_otel_context_are_visible_in_worker(recording_pool):
    executor = ProjectMemberSyncExecutor(workers=2)
    span_context = SpanContext(
        trace_id=0x1234,
        span_id=0x5678,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    marker_token = _request_marker.set("request-42")
    otel_token = otel_context.attach(trace.set_span_in_context(NonRecordingSpan(span_context)))

    def read_context() -> tuple[str, int, str]:
        return (
            _request_marker.get(),
            trace.get_current_span().get_span_context().trace_id,
            threading.current_thread().name,
        )

    try:
        marker, trace_id, thread_name = await asyncio.wait_for(executor.run(read_context), WAIT_TIMEOUT)
    finally:
        otel_context.detach(otel_token)
        _request_marker.reset(marker_token)
        await executor.shutdown()

    assert marker == "request-42"
    assert trace_id == 0x1234
    assert thread_name != threading.current_thread().name


@pytest.mark.asyncio
async def test_run_from_a_different_event_loop_raises_without_taking_a_slot(recording_pool):
    executor = ProjectMemberSyncExecutor(workers=1)
    assert await asyncio.wait_for(executor.run(lambda: "bound"), WAIT_TIMEOUT) == "bound"
    foreign_errors: list[BaseException] = []

    def run_on_other_loop() -> None:
        try:
            asyncio.run(asyncio.wait_for(executor.run(lambda: "foreign"), WAIT_TIMEOUT))
        except BaseException as exc:  # noqa: BLE001 - captured for assertion
            foreign_errors.append(exc)

    thread = threading.Thread(target=run_on_other_loop)
    thread.start()
    await asyncio.to_thread(thread.join, WAIT_TIMEOUT)

    assert not thread.is_alive()
    assert len(foreign_errors) == 1
    assert isinstance(foreign_errors[0], RuntimeError)
    assert "different event loop" in str(foreign_errors[0])
    assert recording_pool[0].submits == 1
    # The rejected call must not have consumed the only slot of the bound loop.
    assert await asyncio.wait_for(executor.run(lambda: "still works"), WAIT_TIMEOUT) == "still works"
    await executor.shutdown()


@pytest.mark.asyncio
async def test_shutdown_stops_executor_and_run_raises_afterwards(recording_pool):
    executor = ProjectMemberSyncExecutor(workers=2)
    assert await asyncio.wait_for(executor.run(lambda: "warm"), WAIT_TIMEOUT) == "warm"
    assert len(recording_pool) == 1
    pool = recording_pool[0]

    await executor.shutdown()

    with pytest.raises(RuntimeError):
        pool.submit(lambda: None)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(executor.run(lambda: "late"), WAIT_TIMEOUT)
    assert len(recording_pool) == 1

    await executor.shutdown()  # idempotent
    assert len(recording_pool) == 1


@pytest.mark.asyncio
async def test_shutdown_before_first_run_never_builds_executor(recording_pool):
    executor = ProjectMemberSyncExecutor(workers=2)

    await executor.shutdown()

    with pytest.raises(RuntimeError):
        await asyncio.wait_for(executor.run(lambda: "late"), WAIT_TIMEOUT)
    assert recording_pool == []


@pytest.mark.asyncio
async def test_worker_count_defaults_to_config(recording_pool, monkeypatch):
    monkeypatch.setattr(executor_module.config, "LITELLM_PROJECT_MEMBER_SYNC_WORKERS", 1)
    executor = ProjectMemberSyncExecutor()
    calls = _BlockingCalls()
    for name in ("a", "b"):
        calls.prepare(name)
    try:
        tasks = [asyncio.create_task(executor.run(calls.call, name=name)) for name in ("a", "b")]
        await _wait_until(lambda: calls.in_flight == 1)
        await asyncio.sleep(0.05)
        assert recording_pool[0].submits == 1

        calls.release_all()
        assert await asyncio.wait_for(asyncio.gather(*tasks), WAIT_TIMEOUT) == ["a", "b"]
        assert calls.max_in_flight == 1
    finally:
        calls.release_all()
        await executor.shutdown()


@pytest.mark.asyncio
async def test_singleton_is_reused_and_shut_down(recording_pool, monkeypatch):
    monkeypatch.setattr(executor_module, "_executor", None)

    singleton = get_project_member_sync_executor()
    assert get_project_member_sync_executor() is singleton
    assert await asyncio.wait_for(singleton.run(lambda: "ok"), WAIT_TIMEOUT) == "ok"

    await shutdown_project_member_sync_executor()

    assert get_project_member_sync_executor() is singleton
    with pytest.raises(RuntimeError):
        await asyncio.wait_for(get_project_member_sync_executor().run(lambda: "late"), WAIT_TIMEOUT)
    assert len(recording_pool) == 1
    with pytest.raises(RuntimeError):
        recording_pool[0].submit(lambda: None)


@pytest.mark.asyncio
async def test_singleton_shutdown_before_use_builds_no_pool_and_stays_closed(recording_pool, monkeypatch):
    monkeypatch.setattr(executor_module, "_executor", None)

    await shutdown_project_member_sync_executor()
    await shutdown_project_member_sync_executor()  # idempotent

    with pytest.raises(RuntimeError):
        await asyncio.wait_for(get_project_member_sync_executor().run(lambda: "late"), WAIT_TIMEOUT)
    assert recording_pool == []
