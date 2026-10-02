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
(``.codemie_bridge/<epoch>-<uuid4hex>/`` in the workspace root): ``req.<id>.json`` written by ``call``,
``resp.<id>.json`` written by the backend. One call at a time, single thread.
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

_state: str | None = None


class ToolCallError(Exception):
    """A tool call failed: error response, timeout, oversize request or unavailable tool calling."""

    def __init__(self, message: str, code: str = "error") -> None:
        super().__init__(message)
        self.code: str = code


def _configure(exchange_dir: str) -> None:
    """Bootstrap only: point the SDK at this run's exchange folder (relative paths are fine)."""
    global _state
    _state = exchange_dir


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        return


def _read_response(path: str) -> dict[str, object] | None:
    """Return the parsed response, or None when it is absent or not fully written yet."""
    try:
        with open(path, encoding="utf-8") as handle:
            parsed: object = json.load(handle)
    except (OSError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def call(op: str, payload: dict[str, object], timeout: float | None = None) -> object:
    """Send ``op`` with ``payload`` to the backend and return its result.

    Raises ToolCallError on an error response, timeout, oversize request or when tool calling is unavailable.
    """
    exchange_dir = _state
    if exchange_dir is None:
        raise ToolCallError("tool calling is not available in this run", "unavailable")

    call_id = uuid.uuid4().hex
    request: dict[str, object] = {"v": PROTOCOL_VERSION, "id": call_id, "op": op, "payload": payload}
    body = json.dumps(request).encode("utf-8")
    if len(body) > MAX_PAYLOAD_BYTES:
        raise ToolCallError(f"request exceeds {MAX_PAYLOAD_BYTES} bytes", "payload_too_large")

    req_path = os.path.join(exchange_dir, f"{REQ_PREFIX}{call_id}{FILE_SUFFIX}")
    resp_path = os.path.join(exchange_dir, f"{RESP_PREFIX}{call_id}{FILE_SUFFIX}")
    tmp_path = os.path.join(exchange_dir, f"tmp.{call_id}")
    with open(tmp_path, "wb") as handle:
        handle.write(body)
    os.replace(tmp_path, req_path)

    unavailable_path = os.path.join(exchange_dir, UNAVAILABLE_MARKER_NAME)
    wait = CALL_TIMEOUT_SECONDS if timeout is None else timeout
    deadline = time.monotonic() + wait
    while True:
        response = _read_response(resp_path)
        if response is not None:
            _remove(resp_path)
            if response.get("ok") is True:
                return response.get("result")
            error = response.get("error")
            details: dict[str, object] = error if isinstance(error, dict) else {}
            raise ToolCallError(str(details.get("message", "tool call failed")), str(details.get("code", "error")))
        if os.path.exists(unavailable_path):
            _remove(req_path)
            raise ToolCallError("the backend stopped answering tool calls in this run", "unavailable")
        if time.monotonic() >= deadline:
            _remove(req_path)
            raise ToolCallError(f"no response to {op!r} within {wait} seconds", "timeout")
        time.sleep(POLL_INTERVAL_SECONDS)
