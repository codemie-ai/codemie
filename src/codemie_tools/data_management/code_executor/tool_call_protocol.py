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

"""The backend side of the tool-call protocol: one request in, one response out, with no transport.

Nothing here knows how a request reaches the backend or how the answer goes back, and nothing imports
``kubernetes``, so the policy modules (authorizer, handler) can use :class:`ToolCallRefused` without pulling in the
sandbox stack. :class:`RequestDispatcher` is total: whatever goes wrong with one request becomes a coded response.
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import final

from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import (
    CODE_BAD_REQUEST,
    CODE_DEADLINE_EXCEEDED,
    CODE_INTERNAL_ERROR,
    CODE_PAYLOAD_TOO_LARGE,
    CODE_TIMEOUT,
    CODE_UNKNOWN_OP,
    MAX_PAYLOAD_BYTES,
    PROTOCOL_VERSION,
)

logger = logging.getLogger(__name__)

#: A call is not started when less than this much of the run's time is left: it could no longer be delivered.
DEADLINE_MARGIN_SECONDS: float = 2.0

ToolCallHandler = Callable[[Mapping[str, object]], object]


class ToolCallRefused(Exception):
    """Raised by a handler to refuse a call with a specific error ``code`` and script-facing ``message``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code: str = code
        self.message: str = message


@final
@dataclass(frozen=True)
class PendingRequest:
    """One request file as the transport read it: the id, the file it came from and what the file held."""

    call_id: str
    file_name: str
    oversize: bool
    body: str | None
    bad_encoding: bool = False


@final
@dataclass(frozen=True)
class CallDeadline:
    """How much of the run's time is left for a call: the run's clock and the margin a call needs to be delivered."""

    time_left: Callable[[], float | None]
    margin: float = DEADLINE_MARGIN_SECONDS

    def remaining(self) -> float | None:
        """Seconds a call may still wait before it has too little time to start, or ``None`` without a limit."""
        left = self.time_left()
        return None if left is None else left - self.margin

    def ensure(self) -> None:
        """Raise ``ToolCallRefused`` (``deadline_exceeded``) when the call can no longer be started."""
        remaining = self.remaining()
        if remaining is not None and remaining < 0:
            raise ToolCallRefused(
                CODE_DEADLINE_EXCEEDED, "the run has too little time left to start this call; it was not started"
            )


_current_deadline: contextvars.ContextVar[CallDeadline | None] = contextvars.ContextVar(
    "tool_call_deadline", default=None
)


def current_call_deadline() -> CallDeadline | None:
    """The deadline of the call being served, for a handler that waits before it starts work; ``None`` outside one."""
    return _current_deadline.get()


_current_cancel: contextvars.ContextVar[threading.Event | None] = contextvars.ContextVar(
    "tool_call_cancel", default=None
)


def current_call_cancelled() -> threading.Event | None:
    """The event that is set when the script stopped waiting for the call being served; ``None`` outside one."""
    return _current_cancel.get()


def ensure_not_cancelled() -> None:
    """Raise ``ToolCallRefused`` (``timeout``) when the script has stopped waiting for the call being served.

    The answer is never delivered then, so a call that has not started must not start and must not hold a place.
    """
    cancel = _current_cancel.get()
    if cancel is not None and cancel.is_set():
        raise ToolCallRefused(CODE_TIMEOUT, "the script stopped waiting for this call; it was not started")


@final
@dataclass(frozen=True)
class CallOutcome:
    """How one request ended: the one record that the trace, the metric and the audit of later stories consume.

    ``tool`` is the ``name`` of the payload when there is one (the tool of a ``tool.call``), ``code`` the error code the
    script got (``None`` for a result). ``delivered`` and ``withdrawn`` are filled by the transport: whether the answer
    was written, and whether the script stopped waiting before it was.
    """

    call_id: str
    op: str | None
    tool: str | None
    code: str | None
    duration_seconds: float
    response_bytes: int
    delivered: bool | None = None
    withdrawn: bool = False

    def settled(self, *, delivered: bool, withdrawn: bool = False) -> CallOutcome:
        return replace(self, delivered=delivered, withdrawn=withdrawn)


@final
@dataclass(frozen=True)
class DispatchResult:
    """The response bytes of one request and the record of how it ended."""

    body: bytes
    outcome: CallOutcome


def error_response(call_id: str, code: str, message: str) -> bytes:
    return json.dumps(
        {"v": PROTOCOL_VERSION, "id": call_id, "ok": False, "error": {"code": code, "message": message}}
    ).encode("utf-8")


def parse_request(body: str | None) -> tuple[str, Mapping[str, object], str | None]:
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


@final
class _Trace:
    """What the dispatcher learns about one request on the way, for its :class:`CallOutcome`."""

    def __init__(self) -> None:
        self.op: str | None = None
        self.tool: str | None = None
        self.code: str | None = None


@final
class RequestDispatcher:
    """Turns one :class:`PendingRequest` into the bytes of its response."""

    def __init__(
        self,
        handlers: Mapping[str, ToolCallHandler] | None = None,
        *,
        max_payload_bytes: int = MAX_PAYLOAD_BYTES,
        time_left: Callable[[], float | None] | None = None,
        deadline_margin: float = DEADLINE_MARGIN_SECONDS,
    ) -> None:
        """``handlers`` maps an operation to its handler (none: every operation is ``unknown_op``).
        ``time_left`` says how much of the run's time is left; a call is refused when less than ``deadline_margin``
        seconds remain, and the handler can read the same clock through :func:`current_call_deadline`.
        """
        self._handlers: Mapping[str, ToolCallHandler] = {} if handlers is None else dict(handlers)
        self.max_payload_bytes: int = max_payload_bytes
        self._deadline: CallDeadline = CallDeadline(time_left or (lambda: None), deadline_margin)

    def dispatch(self, request: PendingRequest) -> bytes:
        """Answer one request. Total: whatever goes wrong becomes an error response, never an exception."""
        return self.handle(request).body

    def handle(self, request: PendingRequest, cancelled: threading.Event | None = None) -> DispatchResult:
        """:meth:`dispatch` plus the record of how the request ended. Total.

        ``cancelled`` is set by the transport when the script stopped waiting; a request not started by then is not run.
        """
        started = time.monotonic()
        trace = _Trace()
        try:
            body = self._answer(request, cancelled, trace)
        except Exception as exc:  # noqa: BLE001 - one bad request or handler must not end the channel
            logger.warning("tool_call_protocol: request %s failed: %s", request.call_id, type(exc).__name__)
            body = self._fail(trace, request.call_id, CODE_INTERNAL_ERROR, "the request could not be processed")
        outcome = CallOutcome(
            call_id=request.call_id,
            op=trace.op,
            tool=trace.tool,
            code=trace.code,
            duration_seconds=time.monotonic() - started,
            response_bytes=len(body),
        )
        return DispatchResult(body=body, outcome=outcome)

    @staticmethod
    def _fail(trace: _Trace, call_id: str, code: str, message: str) -> bytes:
        trace.code = code
        return error_response(call_id, code, message)

    def _answer(self, request: PendingRequest, cancelled: threading.Event | None, trace: _Trace) -> bytes:
        call_id = request.call_id
        if request.oversize:
            return self._fail(trace, call_id, CODE_PAYLOAD_TOO_LARGE, f"request exceeds {self.max_payload_bytes} bytes")
        if request.bad_encoding:
            return self._fail(trace, call_id, CODE_BAD_REQUEST, "request is not valid UTF-8")
        op, payload, problem = parse_request(request.body)
        if problem is not None:
            return self._fail(trace, call_id, CODE_BAD_REQUEST, problem)
        trace.op = op
        name = payload.get("name")
        trace.tool = name if isinstance(name, str) else None
        handler = self._handlers.get(op)
        if handler is None:
            return self._fail(trace, call_id, CODE_UNKNOWN_OP, f"unknown operation: {op!r}")
        deadline_token = _current_deadline.set(self._deadline)
        cancel_token = _current_cancel.set(cancelled)
        try:
            ensure_not_cancelled()
            self._deadline.ensure()
            result = handler(payload)
            encoded = json.dumps(
                {"v": PROTOCOL_VERSION, "id": call_id, "ok": True, "result": result}, ensure_ascii=False
            ).encode("utf-8")
        except ToolCallRefused as refused:
            return self._fail(trace, call_id, refused.code, refused.message)
        except (TypeError, ValueError) as exc:
            logger.warning("tool_call_protocol: handler %r produced no serialisable result: %s", op, type(exc).__name__)
            return self._fail(trace, call_id, CODE_BAD_REQUEST, f"operation {op!r} produced no valid result")
        finally:
            _current_cancel.reset(cancel_token)
            _current_deadline.reset(deadline_token)
        if len(encoded) > self.max_payload_bytes:
            return self._fail(
                trace,
                call_id,
                CODE_PAYLOAD_TOO_LARGE,
                f"result is {len(encoded)} bytes, above the {self.max_payload_bytes} byte limit",
            )
        return encoded
