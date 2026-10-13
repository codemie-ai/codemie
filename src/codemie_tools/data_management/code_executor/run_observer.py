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

"""What one sandbox run learned about itself: the executor capacity it asked for, how it ended and how long it took.

The code that holds the capacity (the Job slots, the shared pod pool) and runs the script marks the facts; the tool
that started the run reads them afterwards, once. A run marks each fact at most once, however long it waited or however
often it looked: ``waited`` implies ``requested``, and ``rejected`` implies ``waited``.
"""

from __future__ import annotations

import threading
from enum import Enum

from langchain_core.tools import ToolException


class RunOutcome(str, Enum):
    """How a sandbox run ended."""

    OK = "ok"
    #: The script ran and exited with a non-zero code.
    SCRIPT_ERROR = "script_error"
    #: The sandbox did not get ready, or the run did not finish, within its time budget.
    TIMEOUT = "timeout"
    #: The sandbox itself failed: the Job could not be created, a pod stopped early, an exec call failed.
    SANDBOX_ERROR = "sandbox_error"
    #: No Job slot or pod was available in time.
    CAPACITY_REJECTED = "capacity_rejected"
    #: The code was refused by the security policy before it ran.
    POLICY_DENIED = "policy_denied"
    #: Anything else that raised.
    ERROR = "error"


class SandboxFailure(ToolException):
    """A ``ToolException`` that says how the run ended; the message is the one the user sees."""

    def __init__(self, message: str, outcome: RunOutcome) -> None:
        super().__init__(message)
        self.outcome = outcome


class SandboxRunObserver:
    """The facts of one sandbox run. Safe to mark from the thread that runs it and to read from another."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requested = False
        self._waited = False
        self._rejected = False
        self._failure: RunOutcome | None = None
        self._exit_code: int | None = None
        self._acquire_seconds: float | None = None
        self._pod_start_seconds: float | None = None
        self._pod_seconds: float | None = None

    # ------------------------------------------------------------------ capacity

    def mark_requested(self) -> None:
        """The run asked for a Job slot or a pod."""
        with self._lock:
            self._requested = True

    def mark_waited(self) -> None:
        """The capacity was full, so the run had to wait for it."""
        with self._lock:
            self._requested = True
            self._waited = True

    def mark_rejected(self) -> None:
        """The run got no capacity and was refused."""
        with self._lock:
            self._requested = self._waited = self._rejected = True

    @property
    def requested(self) -> bool:
        with self._lock:
            return self._requested

    @property
    def waited(self) -> bool:
        with self._lock:
            return self._waited

    @property
    def rejected(self) -> bool:
        with self._lock:
            return self._rejected

    # ------------------------------------------------------------------ ending

    def fail(self, error: BaseException) -> None:
        """The run raised: its outcome is the one the error names, or ``error`` for any other exception."""
        outcome = error.outcome if isinstance(error, SandboxFailure) else RunOutcome.ERROR
        with self._lock:
            if self._failure is None:
                self._failure = outcome

    def set_exit_code(self, exit_code: int | None) -> None:
        """The script's exit code, once it ran."""
        with self._lock:
            self._exit_code = exit_code

    @property
    def outcome(self) -> RunOutcome:
        with self._lock:
            if self._rejected:
                return RunOutcome.CAPACITY_REJECTED
            if self._failure is not None and self._failure is not RunOutcome.ERROR:
                return self._failure
            if self._exit_code not in (None, 0):
                # The tool raises "Code execution failed" for such a run, which is a plain error to it.
                return RunOutcome.SCRIPT_ERROR
            return self._failure or RunOutcome.OK

    # ------------------------------------------------------------------ timings

    def set_acquire_seconds(self, seconds: float) -> None:
        """How long the run took to get its capacity (the wait for a Job slot, or getting a pod session)."""
        with self._lock:
            self._acquire_seconds = seconds

    def set_pod_start_seconds(self, seconds: float) -> None:
        """From creating the Job to its pod running."""
        with self._lock:
            self._pod_start_seconds = seconds

    def set_pod_seconds(self, seconds: float) -> None:
        """How long the run held its Job, from the slot to the Job's deletion: the pod's life as the app sees it."""
        with self._lock:
            self._pod_seconds = seconds

    @property
    def acquire_seconds(self) -> float | None:
        with self._lock:
            return self._acquire_seconds

    @property
    def pod_start_seconds(self) -> float | None:
        with self._lock:
            return self._pod_start_seconds

    @property
    def pod_seconds(self) -> float | None:
        with self._lock:
            return self._pod_seconds
