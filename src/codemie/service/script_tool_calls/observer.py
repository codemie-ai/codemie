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

"""The per-run observer of a workspace script run: what its tool calls were and how they ended.

One observer lives for one run. The channel's polling thread feeds it the settled outcomes, the handler records the
resolved tool of each call under the call's sequence number, and the service reads it once, closed, at run end. An
outcome that settles after ``close`` is not added to the aggregates.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from codemie.core.constants import ToolType
from codemie.service.script_tool_calls.context import ScriptRunContext
from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import (
    CODE_BAD_REQUEST,
    CODE_UNKNOWN_OP,
    TOOL_CALL_OP,
)
from codemie_tools.data_management.code_executor.tool_call_protocol import CallOutcome

UNKNOWN_TOOL: Final[str] = "unknown"


@dataclass(frozen=True)
class CallDetail:
    """What the handler learned about one call after authorization."""

    tool_name: str
    tool_type: str


_UNKNOWN_DETAIL: Final[CallDetail] = CallDetail(UNKNOWN_TOOL, ToolType.INTERNAL.value)


def tool_type_label(value: object) -> str:
    """A tool type as the metrics write it (the enum's value, or the text given)."""
    return value.value if isinstance(value, Enum) else str(value)


@dataclass(frozen=True)
class RunSnapshot:
    """The facts of a finished run, read once at close."""

    bridge_calls: int
    success_calls: int
    failed_calls: int
    abandoned_calls: int
    #: Resolved tool name -> (tokens the model would have written for the calls, tokens it would have read in the answers),
    #: over success-delivered calls only.
    tool_tokens: dict[str, tuple[int, int]]
    attribution: ScriptRunContext | None
    #: The most calls of the run in flight at once (running or queued for a worker); 1 for strictly sequential calls.
    peak_parallel: int = 0
    #: Calls that had to wait for a place in the process-wide gate, their total and longest wait, and calls that gave up
    #: waiting for one.
    gate_waited_calls: int = 0
    gate_wait_seconds: float = 0.0
    gate_wait_max_seconds: float = 0.0
    gate_timeout_calls: int = 0

    @property
    def bridge_ok(self) -> bool:
        return self.success_calls > 0


@dataclass
class _Counts:
    success: int = 0
    failed: int = 0
    abandoned: int = 0
    tool_tokens: dict[str, tuple[int, int]] = field(default_factory=dict)
    peak_parallel: int = 0
    gate_waited_calls: int = 0
    gate_wait_seconds: float = 0.0
    gate_wait_max_seconds: float = 0.0
    gate_timeout_calls: int = 0

    @property
    def total(self) -> int:
        return self.success + self.failed + self.abandoned


def counts_as_call(outcome: CallOutcome) -> bool:
    """Whether a settled outcome is a call of the run: a tool call, a request that never got an operation (unparsed,
    oversize, unfinished), or a request the protocol turned away (an unknown operation, a bad request). A well-formed
    request for some other operation is not a call."""
    return outcome.op is None or outcome.op == TOOL_CALL_OP or outcome.code in (CODE_UNKNOWN_OP, CODE_BAD_REQUEST)


class ScriptRunObserver:
    """Collects the call detail and the outcome aggregates of one script run, under one lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._details: dict[int, CallDetail] = {}
        self._error_classes: dict[int, str] = {}
        self._counts = _Counts()
        self._attribution: ScriptRunContext | None = None
        self._snapshot: RunSnapshot | None = None
        self._tokens: dict[int, tuple[int, int]] = {}
        self._response_cap_tokens: int | None = None

    @property
    def attribution(self) -> ScriptRunContext | None:
        with self._lock:
            return self._attribution

    @property
    def counted_calls(self) -> int:
        """How many outcomes the aggregates hold so far."""
        with self._lock:
            return self._counts.total

    def set_attribution(self, context: ScriptRunContext) -> None:
        with self._lock:
            self._attribution = context

    def set_response_cap_tokens(self, cap: int | None) -> None:
        """Cap what each success call adds to ``tool_tokens`` as response tokens (``None``: no cap)."""
        with self._lock:
            self._response_cap_tokens = cap

    def record_tokens(self, seq: int, request_tokens: int, response_tokens: int) -> None:
        """The tokens of one call's request and answer, under its sequence number (the handler counts them)."""
        with self._lock:
            self._tokens[seq] = (request_tokens, response_tokens)

    def record_gate(self, waited: bool, wait_seconds: float) -> None:
        """One call got its place in the gate; ``waited`` says whether it had to wait for it."""
        with self._lock:
            if self._snapshot is not None or not waited:
                return
            counts = self._counts
            counts.gate_waited_calls += 1
            counts.gate_wait_seconds += wait_seconds
            counts.gate_wait_max_seconds = max(counts.gate_wait_max_seconds, wait_seconds)

    def record_gate_timeout(self, wait_seconds: float) -> None:
        """One call waited for a place in the gate until its time ran out."""
        with self._lock:
            if self._snapshot is not None:
                return
            counts = self._counts
            counts.gate_timeout_calls += 1
            counts.gate_waited_calls += 1
            counts.gate_wait_seconds += wait_seconds
            counts.gate_wait_max_seconds = max(counts.gate_wait_max_seconds, wait_seconds)

    def record_detail(self, seq: int, tool_name: str, tool_type: object) -> None:
        with self._lock:
            self._details[seq] = CallDetail(tool_name, tool_type_label(tool_type))

    def record_error_class(self, seq: int, error_class: str) -> None:
        with self._lock:
            self._error_classes[seq] = error_class

    def error_class_for(self, seq: int | None) -> str | None:
        """The class of the exception that made the call fail, when the handler saw one."""
        with self._lock:
            return self._error_classes.get(seq) if seq is not None else None

    def detail_for(self, seq: int | None) -> CallDetail:
        """The call's resolved tool; ``unknown`` when it never resolved or no detail was recorded (abandoned too)."""
        with self._lock:
            return self._details.get(seq, _UNKNOWN_DETAIL) if seq is not None else _UNKNOWN_DETAIL

    def on_settled(self, outcome: CallOutcome) -> None:
        """Count one settled outcome; ignored for the aggregates once the observer is closed."""
        with self._lock:
            if self._snapshot is not None:
                return
            if not counts_as_call(outcome):
                return
            counts = self._counts
            counts.peak_parallel = max(counts.peak_parallel, outcome.concurrency)
            if outcome.abandoned:
                counts.abandoned += 1
            elif outcome.code is not None:
                counts.failed += 1
            else:
                counts.success += 1
                name = self._details.get(outcome.seq, _UNKNOWN_DETAIL).tool_name
                request, response = counts.tool_tokens.get(name, (0, 0))
                call_request, call_response = self._tokens.get(outcome.seq, (0, 0))
                if self._response_cap_tokens is not None:
                    call_response = min(call_response, self._response_cap_tokens)
                counts.tool_tokens[name] = (request + call_request, response + call_response)

    def close(self) -> RunSnapshot:
        """Freeze the aggregates and return them; every later call returns the same snapshot."""
        with self._lock:
            if self._snapshot is None:
                counts = self._counts
                self._snapshot = RunSnapshot(
                    bridge_calls=counts.total,
                    success_calls=counts.success,
                    failed_calls=counts.failed,
                    abandoned_calls=counts.abandoned,
                    tool_tokens=dict(counts.tool_tokens),
                    attribution=self._attribution,
                    peak_parallel=counts.peak_parallel,
                    gate_waited_calls=counts.gate_waited_calls,
                    gate_wait_seconds=counts.gate_wait_seconds,
                    gate_wait_max_seconds=counts.gate_wait_max_seconds,
                    gate_timeout_calls=counts.gate_timeout_calls,
                )
            return self._snapshot
