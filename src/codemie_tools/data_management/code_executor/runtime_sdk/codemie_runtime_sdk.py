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

"""Runtime SDK for scripts run by the workspace script runner: call the backend during a run.

Standalone by design: stdlib only, no ``__file__`` use, no relative imports, no output on stdout. It is executed
into a module object on the sandbox pod and also imported by the backend for its protocol constants, so the top
level holds only constants and definitions. Requests and responses are JSON files in an exchange folder
(``.codemie_bridge/<epoch>-<uuid4hex>/`` in the workspace root): ``req.<id>.json`` written by the SDK,
``resp.<id>.json`` written by the backend. Every request carries a unique id, so the backend can serve several at
once. Scripts call ``call_tool`` for one tool and ``call_tools`` for several at once.
"""

import json
import os
import time
import uuid

PROTOCOL_VERSION: int = 1
BRIDGE_DIR_NAME: str = ".codemie_bridge"
REQ_PREFIX: str = "req."
RESP_PREFIX: str = "resp."
FILE_SUFFIX: str = ".json"
UNAVAILABLE_MARKER_NAME: str = "channel_unavailable"
MAX_PAYLOAD_BYTES: int = 256 * 1024
CALL_TIMEOUT_SECONDS: float = 100.0
POLL_INTERVAL_SECONDS: float = 0.1
#: Most calls one ``call_tools`` batch may hold; a bigger list is refused before anything is sent.
MAX_BATCH_CALLS: int = 32
TOOL_CALL_OP: str = "tool.call"

# Error codes. The set is closed: ERROR_CODES is built from these constants and a contract test fails when the
# backend can emit anything else.
CODE_NO_CONTEXT: str = "no_context"
CODE_TOOL_BLOCKED: str = "tool_blocked"
CODE_TOOL_UNAVAILABLE: str = "tool_unavailable"
CODE_BAD_ARGUMENTS: str = "bad_arguments"
CODE_TOOL_FAILED: str = "tool_failed"
CODE_UNKNOWN_OP: str = "unknown_op"
CODE_BAD_REQUEST: str = "bad_request"
CODE_PAYLOAD_TOO_LARGE: str = "payload_too_large"
CODE_INTERNAL_ERROR: str = "internal_error"
CODE_UNAVAILABLE: str = "unavailable"
CODE_TIMEOUT: str = "timeout"
CODE_ERROR: str = "error"
CODE_DEADLINE_EXCEEDED: str = "deadline_exceeded"

ERROR_CODES: frozenset[str] = frozenset(
    {
        CODE_NO_CONTEXT,
        CODE_TOOL_BLOCKED,
        CODE_TOOL_UNAVAILABLE,
        CODE_BAD_ARGUMENTS,
        CODE_TOOL_FAILED,
        CODE_UNKNOWN_OP,
        CODE_BAD_REQUEST,
        CODE_PAYLOAD_TOO_LARGE,
        CODE_INTERNAL_ERROR,
        CODE_UNAVAILABLE,
        CODE_TIMEOUT,
        CODE_ERROR,
        CODE_DEADLINE_EXCEEDED,
    }
)

_MAY_HAVE_COMPLETED: str = (
    "the call may already have run on the backend (check error.may_have_run); do not retry a call that changes data"
    " without checking first"
)

# Whether trying the SAME call again could succeed. A code not listed here is not retryable: the call would fail
# the same way again (a bad argument, a tool that is never available, a run that is ending), so fix the request or
# wait for a new run instead of retrying it.
_RETRYABLE_CODES: frozenset[str] = frozenset(
    {CODE_TOOL_FAILED, CODE_INTERNAL_ERROR, CODE_UNAVAILABLE, CODE_TIMEOUT, CODE_ERROR}
)
# Whether the tool may already have run before the error was raised, so a call that changes data (create, update,
# delete, send) must not be retried without checking first. A code not listed here means the call never reached the
# tool: nothing happened, and it is safe to retry even a call that changes data.
_MAY_HAVE_RUN_CODES: frozenset[str] = frozenset(
    {CODE_PAYLOAD_TOO_LARGE, CODE_INTERNAL_ERROR, CODE_TOOL_FAILED, CODE_UNAVAILABLE, CODE_TIMEOUT, CODE_ERROR}
)

_exchange_dir: str | None = None
_run_seconds: float | None = None
_max_payload_bytes: int = MAX_PAYLOAD_BYTES
_started: float = 0.0


class ToolCallError(Exception):
    """A tool call failed: error response, timeout, oversize request or unavailable tool calling.

    ``retryable`` says whether trying the same call again could succeed; ``may_have_run`` whether the tool may
    already have run, so a call that changes data must not be retried without checking first. Both default to what
    ``code`` alone implies (``False`` for a code this says nothing about, the safe default either way); the SDK
    overrides a default when one ``code`` covers cases that disagree on it (``unavailable`` covers both "never
    configured", which is certain, and "stopped answering mid-run", which is not).
    """

    def __init__(
        self, message: str, code: str = CODE_ERROR, *, retryable: bool | None = None, may_have_run: bool | None = None
    ) -> None:
        super().__init__(message)
        self.code: str = code
        self._retryable: bool | None = retryable
        self._may_have_run: bool | None = may_have_run

    @property
    def retryable(self) -> bool:
        return self._retryable if self._retryable is not None else self.code in _RETRYABLE_CODES

    @property
    def may_have_run(self) -> bool:
        return self._may_have_run if self._may_have_run is not None else self.code in _MAY_HAVE_RUN_CODES


def _configure(config: dict[str, object]) -> None:
    """Bootstrap only: set the run's configuration and start its clock.

    Keys: ``exchange_dir`` (this run's exchange folder, relative paths are fine; absent means tool calling is
    unavailable), ``run_seconds`` (the run's tool-calling limit: a call never waits beyond the time left of it) and
    ``max_payload_bytes`` (the size cap of one request; defaults to MAX_PAYLOAD_BYTES).
    """
    global _exchange_dir, _run_seconds, _max_payload_bytes, _started
    exchange_dir = config.get("exchange_dir")
    run_seconds = config.get("run_seconds")
    max_payload_bytes = config.get("max_payload_bytes")
    _exchange_dir = exchange_dir if isinstance(exchange_dir, str) else None
    _run_seconds = float(run_seconds) if isinstance(run_seconds, (int, float)) else None
    _max_payload_bytes = int(max_payload_bytes) if isinstance(max_payload_bytes, int) else MAX_PAYLOAD_BYTES
    _started = time.monotonic()


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        return


def _read_response(path: str) -> dict[str, object] | None:
    """Return the parsed response, or None when it is absent or not fully written yet."""
    try:
        with open(path, encoding="utf-8") as handle:
            parsed = json.load(handle)
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _prepare() -> tuple[str, float | None]:
    """``(exchange_dir, time_left)`` for a new call, or raise when none may be sent.

    ``time_left`` is the time left of the run's limit in seconds, or None when the run has no limit.
    """
    exchange_dir = _exchange_dir
    if exchange_dir is None:
        raise ToolCallError(
            "tool calling is not available in this run; this will not change within the run, so do not retry",
            CODE_UNAVAILABLE,
            retryable=False,
            may_have_run=False,
        )
    if _run_seconds is None:
        return exchange_dir, None
    time_left = _run_seconds - (time.monotonic() - _started)
    if time_left <= 0:
        raise ToolCallError(
            "the run's tool-calling time limit is used up; the call was not sent, and no time is left in this run"
            " to retry it",
            CODE_TIMEOUT,
            retryable=False,
            may_have_run=False,
        )
    return exchange_dir, time_left


def _wait_seconds(timeout: float | None, time_left: float | None) -> float:
    """How long to wait for the answers: the timeout (default CALL_TIMEOUT_SECONDS), never beyond the run's limit."""
    wait = CALL_TIMEOUT_SECONDS if timeout is None else timeout
    return wait if time_left is None else min(wait, time_left)


def _encode(op: str, payload: dict[str, object]) -> tuple[str, bytes]:
    """``(call_id, body)`` of one request, or raise when it is over the size cap."""
    call_id = uuid.uuid4().hex
    request = {"v": PROTOCOL_VERSION, "id": call_id, "op": op, "payload": payload}
    body = json.dumps(request).encode("utf-8")
    if len(body) > _max_payload_bytes:
        raise ToolCallError(
            f"request exceeds {_max_payload_bytes} bytes; call with fewer or smaller arguments",
            CODE_PAYLOAD_TOO_LARGE,
            may_have_run=False,  # caught before anything was sent
        )
    return call_id, body


def _unwrap(response: dict[str, object] | ToolCallError) -> object:
    """The result of an answer, or raise the error it carries. An error item (from ``_exchange``) is raised as is."""
    if isinstance(response, ToolCallError):
        raise response
    if response.get("ok") is True:
        return response.get("result")
    error = response.get("error")
    details = error if isinstance(error, dict) else {}
    raise ToolCallError(str(details.get("message", "tool call failed")), str(details.get("code", CODE_ERROR)))


def _exchange(
    exchange_dir: str, requests: list[tuple[str, bytes, str]], wait: float
) -> dict[str, dict[str, object] | ToolCallError]:
    """The file exchange for ``requests``, a list of ``(call_id, body, op)``: write them all, then wait for all answers.

    Returns ``{call_id: answer}`` where an answer is the parsed response or a ToolCallError for a request that was
    not answered (the backend stopped answering, or ``wait`` seconds passed). A request that is given up on has its
    request file removed. One loop serves the whole list, so a batch costs no threads.
    """
    paths: dict[str, str] = {}
    try:
        for call_id, body, _ in requests:
            req_path = os.path.join(exchange_dir, f"{REQ_PREFIX}{call_id}{FILE_SUFFIX}")
            tmp_path = os.path.join(exchange_dir, f"tmp.{call_id}")
            with open(tmp_path, "wb") as handle:
                handle.write(body)
            os.replace(tmp_path, req_path)
            paths[call_id] = req_path
    except BaseException:
        for req_path in paths.values():
            _remove(req_path)
        raise

    ops: dict[str, str] = {call_id: op for call_id, _, op in requests}
    unavailable_path = os.path.join(exchange_dir, UNAVAILABLE_MARKER_NAME)
    answers: dict[str, dict[str, object] | ToolCallError] = {}
    deadline = time.monotonic() + wait
    while True:
        for call_id in list(paths):
            resp_path = os.path.join(exchange_dir, f"{RESP_PREFIX}{call_id}{FILE_SUFFIX}")
            response = _read_response(resp_path)
            if response is not None:
                _remove(resp_path)
                answers[call_id] = response
                del paths[call_id]
        if not paths:
            return answers
        if os.path.exists(unavailable_path):
            failure = ToolCallError(
                f"the backend stopped answering tool calls in this run; {_MAY_HAVE_COMPLETED}", CODE_UNAVAILABLE
            )
            break
        if time.monotonic() >= deadline:
            failure = None
            break
        time.sleep(POLL_INTERVAL_SECONDS)
    for call_id, req_path in paths.items():
        _remove(req_path)
        answers[call_id] = failure or ToolCallError(
            f"no response to {ops[call_id]!r} within {wait:.1f} seconds; {_MAY_HAVE_COMPLETED}", CODE_TIMEOUT
        )
    return answers


def call(op: str, payload: dict[str, object], timeout: float | None = None) -> object:
    """Send ``op`` with ``payload`` to the backend and return its result.

    Raises ToolCallError on an error response, timeout, oversize request or when tool calling is unavailable.
    """
    exchange_dir, time_left = _prepare()
    call_id, body = _encode(op, payload)
    wait = _wait_seconds(timeout, time_left)
    return _unwrap(_exchange(exchange_dir, [(call_id, body, op)], wait)[call_id])


def call_tool(name: str, args: dict[str, object] | None = None, *, timeout: float | None = None) -> object:
    """Call the platform tool ``name`` with ``args`` and return the envelope ``{"result": ..., "http"?: ...}``.

    A thin wrapper over ``call``; raises ToolCallError with a code from ERROR_CODES on any failure.
    """
    return call(TOOL_CALL_OP, {"name": name, "args": args or {}}, timeout)


def call_tools(calls: list[dict[str, object]], *, timeout: float | None = None) -> list[object]:
    """Call several tools at once; ``calls`` is a list of dicts, each with ``"name"`` and optionally ``"args"``.

    Any other key of an item goes to the backend with the call (a setting of one call), so a call can grow a setting
    without a new SDK name. Returns a list in the order given: the envelope of a call that succeeded, or a ToolCallError
    (returned, not raised) for a call that failed, so one failure never hides the other results. ``timeout`` is for the
    whole batch and never goes beyond the time left of the run's limit; a call not answered by then is withdrawn, so
    the backend does not start it if it has not started yet. Raises ToolCallError for a problem with the batch itself:
    more than MAX_BATCH_CALLS calls, an item that is not a dict with a string ``"name"``, or tool calling not available.
    Do not put calls that change data, or calls that depend on each other, into one batch.
    """
    if not isinstance(calls, (list, tuple)):
        raise ToolCallError("calls must be a list of dicts, each with a 'name'", CODE_BAD_ARGUMENTS)
    if len(calls) > MAX_BATCH_CALLS:
        raise ToolCallError(
            f"a batch holds at most {MAX_BATCH_CALLS} calls, got {len(calls)}; split into smaller batches",
            CODE_BAD_ARGUMENTS,
        )
    payloads: list[dict[str, object]] = []
    for item in calls:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ToolCallError("each call must be a dict with a string 'name'", CODE_BAD_ARGUMENTS)
        payload = dict(item)
        payload["args"] = item.get("args") or {}
        payloads.append(payload)
    if not payloads:
        return []
    exchange_dir, time_left = _prepare()
    results: list[object] = [None] * len(payloads)
    outgoing: list[tuple[str, bytes, str]] = []
    positions: dict[str, int] = {}
    for index, payload in enumerate(payloads):
        try:
            call_id, body = _encode(TOOL_CALL_OP, payload)
        except ToolCallError as error:
            results[index] = error
            continue
        outgoing.append((call_id, body, TOOL_CALL_OP))
        positions[call_id] = index
    if outgoing:
        answers = _exchange(exchange_dir, outgoing, _wait_seconds(timeout, time_left))
        for call_id, answer in answers.items():
            try:
                results[positions[call_id]] = _unwrap(answer)
            except ToolCallError as error:
                results[positions[call_id]] = error
    return results
