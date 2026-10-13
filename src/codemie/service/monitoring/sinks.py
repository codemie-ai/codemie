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

"""The one place that connects the platform's metrics to what the tools report."""

from __future__ import annotations

from codemie.service.monitoring.code_execution_monitoring_service import CodeExecutionMonitoringService
from codemie.service.monitoring.tool_execution_monitoring_service import ToolExecutionMonitoringService


def register_monitoring_sinks() -> None:
    """Make tool runs and sandbox runs reach the metrics. Call it once in every process that runs tools: without it
    nothing is reported (reporting to no sink does nothing, silently). Safe to call again."""
    ToolExecutionMonitoringService.register()
    CodeExecutionMonitoringService.register()
