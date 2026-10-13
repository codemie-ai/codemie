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

"""What the tools tell whoever listens: how long a tool's own work took, and how a sandbox run went.

``CodeMieTool`` times the call of its ``execute`` (for a model's call) or of its script twin (for a workspace script's
call) and reports it here; a sandbox run reports its facts the same way. The platform adds its sinks at start-up (any
number of them); without one, reporting does nothing, so the tools need no knowledge of metrics. A sink must not
raise, and one that does never reaches the tool.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Generic, Protocol, TypeVar

from codemie.configs.logger import logger

if TYPE_CHECKING:
    from codemie_tools.data_management.code_executor.run_observer import SandboxRunObserver

INVOKED_BY_DIRECT: Final[str] = "direct"
INVOKED_BY_SCRIPT: Final[str] = "script"

T = TypeVar("T")


class Sinks(Generic[T]):
    """The listeners of one kind of report. Safe to add to and report from any thread; a failing sink never reaches the
    reporter nor hides the next sink."""

    def __init__(self, what: str) -> None:
        self._what = what
        self._lock = threading.Lock()
        self._sinks: tuple[Callable[[T], None], ...] = ()

    def add(self, sink: Callable[[T], None]) -> None:
        """Make ``sink`` hear about every report (a sink already added is not added twice)."""
        with self._lock:
            if sink not in self._sinks:
                self._sinks = (*self._sinks, sink)

    def remove(self, sink: Callable[[T], None]) -> None:
        with self._lock:
            self._sinks = tuple(known for known in self._sinks if known != sink)

    def clear(self) -> None:
        with self._lock:
            self._sinks = ()

    def snapshot(self) -> tuple[Callable[[T], None], ...]:
        """The sinks now added, to put back with ``restore`` (for tests that must not leak their sinks)."""
        with self._lock:
            return self._sinks

    def restore(self, snapshot: tuple[Callable[[T], None], ...]) -> None:
        with self._lock:
            self._sinks = snapshot

    def __contains__(self, sink: object) -> bool:
        return sink in self._sinks

    def report(self, event: T) -> None:
        """Hand ``event`` to every sink, in the order they were added. Never raises."""
        for sink in self._sinks:
            try:
                sink(event)
            except Exception as exc:  # noqa: BLE001 - reporting must never fail the reporter
                logger.warning(f"{self._what} report failed: {type(exc).__name__}")


@dataclass(frozen=True)
class ToolExecution:
    """One run of a tool's own work.

    ``duration_seconds`` covers the tool's ``execute`` only: not the configuration check, the limiting of its output by
    tokens, nor the conversion of the result. ``metadata`` is the tool's metadata as the agent stamped it (the run's
    user, assistant, project, model and ids); ``error_class`` is the exception's class name when the run failed.
    """

    tool_name: str
    invoked_by: str
    duration_seconds: float
    ok: bool
    metadata: Mapping[str, Any] | None = None
    error_class: str | None = None


class ToolCallsSummary(Protocol):
    """What a script did through the tool-call bridge during a run. The platform's run snapshot has these attributes;
    the tools library only needs to carry it."""

    @property
    def bridge_calls(self) -> int: ...
    @property
    def success_calls(self) -> int: ...
    @property
    def peak_parallel(self) -> int: ...
    @property
    def gate_waited_calls(self) -> int: ...
    @property
    def gate_wait_seconds(self) -> float: ...
    @property
    def gate_wait_max_seconds(self) -> float: ...
    @property
    def gate_timeout_calls(self) -> int: ...


@dataclass(frozen=True)
class SandboxRunReport:
    """One finished run of a sandbox tool: its facts, and who ran it (the tool's ``metadata`` as the agent stamped it,
    and the ``user_id``). ``calls`` is set for a run that had a tool-call bridge."""

    tool_name: str
    sandbox_mode: str
    sandbox_run: SandboxRunObserver
    elapsed_seconds: float
    metadata: Mapping[str, Any] | None = None
    user_id: str | None = None
    calls: ToolCallsSummary | None = None


tool_execution_sinks: Final[Sinks[ToolExecution]] = Sinks("Tool execution")
sandbox_run_sinks: Final[Sinks[SandboxRunReport]] = Sinks("Sandbox run")


def report_tool_execution(execution: ToolExecution) -> None:
    """Hand a finished tool run to every sink. Never raises."""
    tool_execution_sinks.report(execution)


def report_sandbox_run(report: SandboxRunReport) -> None:
    """Hand a finished sandbox run to every sink. Never raises."""
    sandbox_run_sinks.report(report)
