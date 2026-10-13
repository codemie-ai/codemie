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

"""The one set of dimensions every record of a script run is written under.

Usage, errors, refused, savings estimates and the run record all take their dimensions from here, so any of them can
be cut by user, project, assistant, workflow, model and seed source the same way.
"""

from __future__ import annotations

from codemie.service.monitoring.metrics_constants import MetricsAttributes
from codemie.service.monitoring.tool_run_context import ToolRunContext
from codemie.service.script_tool_calls.context import ScriptRunContext
from codemie.service.script_tool_calls.observer import RunSnapshot

BRIDGE_SOME = "some"
BRIDGE_NONE = "none"
BRIDGE_OK_YES = "yes"
BRIDGE_OK_NO = "no"


def run_dimensions(context: ScriptRunContext | None) -> dict[str, str]:
    """Who ran the script, where and with which model (the run's ``ToolRunContext.dimensions()``)."""
    return (context or ToolRunContext(user_id="")).dimensions()


def run_ok_label(run_ok: bool) -> dict[str, str]:
    """Whether the script run itself ended without an error. A run that failed after some calls succeeded still carries
    their estimate; this label lets a reader keep it apart from a run that finished."""
    return {MetricsAttributes.RUN_OK: BRIDGE_OK_YES if run_ok else BRIDGE_OK_NO}


def bridge_labels(snapshot: RunSnapshot) -> dict[str, str]:
    """Whether the run made tool calls at all (``bridge_calls``) and whether one of them succeeded (``bridge_ok``)."""
    return {
        MetricsAttributes.BRIDGE_CALLS: BRIDGE_SOME if snapshot.bridge_calls > 0 else BRIDGE_NONE,
        MetricsAttributes.BRIDGE_OK: BRIDGE_OK_YES if snapshot.bridge_ok else BRIDGE_OK_NO,
    }
