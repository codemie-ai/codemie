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

"""Usage and refused metrics for the tool calls a workspace script makes.

The listener runs on the channel's polling thread, which has no request contextvars, so everything it writes comes from
the run observer: the attribution captured in the request thread and the resolved tool of the call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from codemie.configs import logger
from codemie.service.monitoring.agent_monitoring_service import AgentMonitoringService
from codemie.service.monitoring.metrics_constants import ScriptCallKind
from codemie.service.script_tool_calls.context import ScriptScopeKind
from codemie.service.script_tool_calls.dimensions import run_dimensions
from codemie.service.script_tool_calls.observer import UNKNOWN_TOOL, ScriptRunObserver, counts_as_call
from codemie_tools.data_management.code_executor.runtime_sdk.codemie_runtime_sdk import (
    CODE_BAD_REQUEST,
    CODE_DEADLINE_EXCEEDED,
    CODE_NO_CONTEXT,
    CODE_TOOL_BLOCKED,
    CODE_TOOL_UNAVAILABLE,
    CODE_UNKNOWN_OP,
)
from codemie_tools.data_management.code_executor.tool_call_protocol import CallOutcome

#: The call was turned away by policy or by the clock; it is not a use of the tool and not a failure of it.
REFUSAL_CODES: Final[frozenset[str]] = frozenset(
    {CODE_TOOL_BLOCKED, CODE_TOOL_UNAVAILABLE, CODE_NO_CONTEXT, CODE_DEADLINE_EXCEEDED}
)
#: The request itself was malformed: no tool was resolved, so the error is counted on the `unknown` tool.
PROTOCOL_FAULT_CODES: Final[frozenset[str]] = frozenset({CODE_UNKNOWN_OP, CODE_BAD_REQUEST})


@dataclass(frozen=True)
class Classification:
    kind: str
    #: False when the tool name must be written as `unknown` whatever the call detail says.
    name_known: bool = True


def classify(outcome: CallOutcome) -> Classification:
    """Map how a call ended to the metric it belongs to. Any code not named above is an error."""
    if outcome.abandoned:
        return Classification(ScriptCallKind.ABANDONED)
    code = outcome.code
    if code is None:
        return Classification(ScriptCallKind.SUCCESS)
    if code in REFUSAL_CODES:
        return Classification(ScriptCallKind.REFUSED)
    return Classification(ScriptCallKind.ERROR, name_known=code not in PROTOCOL_FAULT_CODES)


class ScriptCallMetricsListener:
    """Settled-callback that writes one metric record per call, listed after the observer in the fan-out."""

    def __init__(self, observer: ScriptRunObserver) -> None:
        self._observer = observer

    def __call__(self, outcome: CallOutcome) -> None:
        if not counts_as_call(outcome):
            return
        context = self._observer.attribution
        if context is None:
            logger.warning("Script call %s settled before the run was attributed; no metric written", outcome.call_id)
            return
        result = classify(outcome)
        # A run without a tool-calling context (a direct REST run) is only visible through its refusals.
        if context.scope_kind == ScriptScopeKind.NONE and result.kind != ScriptCallKind.REFUSED:
            return
        detail = self._observer.detail_for(outcome.seq)
        tool_name = detail.tool_name if result.name_known else UNKNOWN_TOOL
        AgentMonitoringService.send_script_tool_call_metrics(
            kind=result.kind,
            tool_name=tool_name,
            tool_type=detail.tool_type,
            dimensions=run_dimensions(context),
            error_code=outcome.code,
            error_class=self._observer.error_class_for(outcome.seq),
        )
