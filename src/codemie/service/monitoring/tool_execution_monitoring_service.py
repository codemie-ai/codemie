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

"""How long tools take, for a model's own calls and for the calls a workspace script makes.

One record per run of a tool's own work (``codemie_tool_execution``, log and search index, with the duration as a
number) and one observation in a histogram (``codemie_tool_execution_seconds``, Prometheus, labelled by tool, who called
it and how it ended only: the number as a label of the record would make a series of every call). The run's
identity comes from the metadata the agent stamped on the tool, which is also there on the thread that serves a
script's call, where no request context exists.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from codemie.configs import logger
from codemie.core.constants import (
    TOOL_TYPE,
    ToolType,
)
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService, send_log_metric
from codemie.service.monitoring.metrics_constants import (
    TOOL_EXECUTION_METRIC,
    TOOL_EXECUTION_SECONDS_METRIC,
    MetricsAttributes,
)
from codemie.service.monitoring.tool_run_context import ToolRunContext
from codemie_tools.base.execution_timing import ToolExecution, tool_execution_sinks

STATUS_SUCCESS = "success"
STATUS_ERROR = "error"


def _text(value: Any) -> str:
    if isinstance(value, Enum):
        return value.value
    return "" if value is None else str(value)


class ToolExecutionMonitoringService:
    @staticmethod
    def record(execution: ToolExecution) -> None:
        """Write the record and the histogram observation of one finished tool run. Never raises."""
        try:
            metadata = execution.metadata or {}
            status = STATUS_SUCCESS if execution.ok else STATUS_ERROR
            record: dict[str, Any] = {
                **ToolRunContext.from_metadata(metadata).dimensions(),
                MetricsAttributes.TOOL_NAME: execution.tool_name,
                MetricsAttributes.INVOKED_BY: execution.invoked_by,
                MetricsAttributes.STATUS: status,
                MetricsAttributes.TOOL_TYPE: _text(metadata.get(TOOL_TYPE, ToolType.INTERNAL)),
                "count": 1,
                "duration_seconds": round(execution.duration_seconds, 4),
            }
            if execution.error_class:
                record[MetricsAttributes.ERROR_CLASS] = execution.error_class
            send_log_metric(TOOL_EXECUTION_METRIC, record)
            BaseMonitoringService.record_duration_metric(
                TOOL_EXECUTION_SECONDS_METRIC,
                execution.duration_seconds,
                attributes={
                    MetricsAttributes.TOOL_NAME: execution.tool_name,
                    MetricsAttributes.INVOKED_BY: execution.invoked_by,
                    MetricsAttributes.STATUS: status,
                    MetricsAttributes.USER_ID: "-",
                },
            )
        except Exception as exc:  # noqa: BLE001 - a metric must never fail a tool call
            logger.warning(f"Tool execution metrics failed: {type(exc).__name__}")

    @classmethod
    def register(cls) -> None:
        """Make every tool report its runs here; called once at start-up."""
        tool_execution_sinks.add(cls.record)
