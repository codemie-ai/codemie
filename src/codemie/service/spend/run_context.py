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

"""Which individual LLM call is in flight, so an outbound header can name it.

**Why a context variable and not an argument.** One model object is reused for every iteration of
an agent loop, and its headers are fixed when it is constructed. The outer request id is constant
for that object's lifetime and can ride in `default_headers`; the per-call run id cannot. A naive
implementation sends one run id for many calls, and a per-request cost lookup then returns the
wrong row set - several calls' rows under one id, or none at all - with nothing failing.

**Why `on_chat_model_start` is the right publisher.** LangChain mints the `run_id` there, the
callback is *pre-flight* (it fires before the model generates), and it runs in the same task as the
request that follows. So the value is correct under async and under threads without a lock.

**Why the request id is part of the published value.** The publishing callback is attached only
when a caller supplies a request id, so a model built without one never publishes. Storing the run
id alone would let such a call read whatever a *previous* call left in the same context and stamp a
foreign run id on its rows - the wrong-row-set defect arriving by a different route. Pairing the
two lets a reader require that the scope belongs to it before trusting it.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

__all__ = ["RunScope", "current_run_id", "publish_run_scope", "reset_run_scope"]


@dataclass(frozen=True, slots=True)
class RunScope:
    """The individual LLM call in flight, tagged with the outer request that owns it."""

    request_id: str
    run_id: str


_current_run_scope: ContextVar[RunScope | None] = ContextVar("codemie_current_run_scope", default=None)


def publish_run_scope(*, request_id: str, run_id: str) -> Token[RunScope | None]:
    """Announce the call that is about to go out. Called from the pre-flight callback."""
    return _current_run_scope.set(RunScope(request_id=request_id, run_id=run_id))


def reset_run_scope(token: Token[RunScope | None]) -> None:
    """Restore the previous scope. Provided for tests and for any caller that owns a token."""
    _current_run_scope.reset(token)


def current_run_id(request_id: str | None) -> str | None:
    """The run id in flight for `request_id`, or None.

    Returns None rather than a best guess when the published scope belongs to a **different**
    request. A run id from another request would be well-formed, would produce no error, and would
    attribute this call's cost to that other request's rows - so the only safe answer is to send no
    run id and let the row carry a NULL, which is a state every consumer already handles.
    """
    scope = _current_run_scope.get()
    if scope is None or request_id is None:
        return None
    return scope.run_id if scope.request_id == request_id else None
