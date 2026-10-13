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

"""Fan one settled call out to the run observer first, then to each listener, each isolated from the others."""

from __future__ import annotations

from collections.abc import Callable

from codemie.configs import logger
from codemie.service.script_tool_calls.observer import ScriptRunObserver
from codemie_tools.data_management.code_executor.tool_call_protocol import CallOutcome

SettledListener = Callable[[CallOutcome], None]


def fan_out(observer: ScriptRunObserver, *listeners: SettledListener) -> SettledListener:
    """A settled-callback: the observer counts the outcome first, then the listeners run in order.

    A failing party is logged and skipped, never raised, so one listener cannot hide the next. Listeners run on the
    channel's polling thread, which also does the exec I/O, so they must not block.
    """

    def on_settled(outcome: CallOutcome) -> None:
        for receiver in (observer.on_settled, *listeners):
            try:
                receiver(outcome)
            except Exception:  # noqa: BLE001 - a listener failure must never reach the run or other listeners
                logger.warning("Script call listener failed for call %s", outcome.call_id, exc_info=True)

    return on_settled
