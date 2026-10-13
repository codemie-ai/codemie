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

"""Metrics of the sandbox runs (``code_executor`` and ``execute_workspace_script``) against the executor capacity.

Counters, all with the tool, the sandbox mode and the run's dimensions: every run by its outcome (``runs``), every run
that asked for capacity (``attempts``), every one of them that had to wait for it (``capacity_waits``, at most one per
run) and every one that was refused for the lack of it (``capacity_rejections``). A rejection is also a wait, and a
wait is also an attempt. Every run is also one record with its numbers (``codemie_code_execution_run``, log and search
index only) and its durations go to histograms.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from codemie.configs import logger
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService, send_log_metric
from codemie.service.monitoring.metrics_constants import (
    CODE_EXECUTION_ACQUIRE_SECONDS_METRIC,
    CODE_EXECUTION_ATTEMPTS_METRIC,
    CODE_EXECUTION_CAPACITY_REJECTIONS_METRIC,
    CODE_EXECUTION_CAPACITY_WAITS_METRIC,
    CODE_EXECUTION_POD_SECONDS_METRIC,
    CODE_EXECUTION_POD_START_SECONDS_METRIC,
    CODE_EXECUTION_RUN_METRIC,
    CODE_EXECUTION_RUN_SECONDS_METRIC,
    CODE_EXECUTION_RUNS_METRIC,
    MetricsAttributes,
)
from codemie.service.monitoring.tool_run_context import ToolRunContext
from codemie_tools.base.execution_timing import SandboxRunReport, ToolCallsSummary, sandbox_run_sinks
from codemie_tools.data_management.code_executor.run_observer import SandboxRunObserver


class CodeExecutionMonitoringService:
    @classmethod
    def register(cls) -> None:
        """Make the sandbox tools report their runs here; called once at start-up."""
        sandbox_run_sinks.add(cls._on_report)

    @classmethod
    def _on_report(cls, report: SandboxRunReport) -> None:
        cls.send_run(
            tool_name=report.tool_name,
            sandbox_mode=report.sandbox_mode,
            sandbox_run=report.sandbox_run,
            elapsed_seconds=report.elapsed_seconds,
            dimensions=ToolRunContext.from_metadata(report.metadata, report.user_id).dimensions(),
            calls=report.calls,
        )

    @staticmethod
    def send_run(
        *,
        tool_name: str,
        sandbox_mode: str,
        sandbox_run: SandboxRunObserver,
        elapsed_seconds: float,
        dimensions: Mapping[str, str],
        calls: ToolCallsSummary | None = None,
    ) -> None:
        """One run's records. ``calls`` holds what the run's script did through the tool-call bridge, when it had one.
        Never raises."""
        try:
            attributes = {
                **dimensions,
                MetricsAttributes.TOOL_NAME: tool_name,
                MetricsAttributes.SANDBOX_MODE: sandbox_mode,
            }
            outcome = sandbox_run.outcome.value
            CodeExecutionMonitoringService._send_counters(attributes, sandbox_run, outcome)
            CodeExecutionMonitoringService._send_run_record(attributes, sandbox_run, outcome, elapsed_seconds, calls)
            CodeExecutionMonitoringService._send_durations(tool_name, sandbox_mode, sandbox_run, elapsed_seconds)
        except Exception as exc:  # noqa: BLE001 - a metric must never fail the run
            logger.warning(f"Code execution metrics failed: {type(exc).__name__}")

    @staticmethod
    def _send_counters(attributes: Mapping[str, Any], sandbox_run: SandboxRunObserver, outcome: str) -> None:
        """Every run counts once by its outcome; a run that asked for capacity also counts as an attempt, a wait or a
        rejection. A run refused before it asked (by the security policy, say) has an outcome but no attempt."""
        send = BaseMonitoringService.send_count_metric
        send(name=CODE_EXECUTION_RUNS_METRIC, attributes={**attributes, MetricsAttributes.OUTCOME: outcome})
        if not sandbox_run.requested:
            return
        send(name=CODE_EXECUTION_ATTEMPTS_METRIC, attributes=dict(attributes))
        if sandbox_run.waited:
            send(name=CODE_EXECUTION_CAPACITY_WAITS_METRIC, attributes=dict(attributes))
        if sandbox_run.rejected:
            send(name=CODE_EXECUTION_CAPACITY_REJECTIONS_METRIC, attributes=dict(attributes))

    @staticmethod
    def _send_run_record(
        attributes: Mapping[str, Any],
        sandbox_run: SandboxRunObserver,
        outcome: str,
        elapsed_seconds: float,
        calls: ToolCallsSummary | None,
    ) -> None:
        """The run as one record with its numbers (durations, calls), for the log and the search index only: a number
        like these as a label of a Prometheus series would make a series of every run."""
        record: dict[str, Any] = {
            **attributes,
            "count": 1,
            MetricsAttributes.OUTCOME: outcome,
            "elapsed_seconds": round(elapsed_seconds, 3),
            "capacity_requested": sandbox_run.requested,
            "capacity_waited": sandbox_run.waited,
        }
        for key, value in (
            ("acquire_seconds", sandbox_run.acquire_seconds),
            ("pod_start_seconds", sandbox_run.pod_start_seconds),
            ("pod_seconds", sandbox_run.pod_seconds),
        ):
            if value is not None:
                record[key] = round(value, 3)
        if calls is not None:
            record.update(
                {
                    "calls_total": calls.bridge_calls,
                    "calls_success": calls.success_calls,
                    "peak_parallel": calls.peak_parallel,
                    "gate_waited_calls": calls.gate_waited_calls,
                    "gate_wait_seconds": round(calls.gate_wait_seconds, 3),
                    "gate_wait_max_seconds": round(calls.gate_wait_max_seconds, 3),
                    "gate_timeout_calls": calls.gate_timeout_calls,
                }
            )
        send_log_metric(CODE_EXECUTION_RUN_METRIC, record)

    @staticmethod
    def _send_durations(
        tool_name: str, sandbox_mode: str, sandbox_run: SandboxRunObserver, elapsed_seconds: float
    ) -> None:
        """Histograms with the tool and mode only (``user_id`` is pinned: the recorder would add the caller's)."""
        labels = {
            MetricsAttributes.TOOL_NAME: tool_name,
            MetricsAttributes.SANDBOX_MODE: sandbox_mode,
            MetricsAttributes.USER_ID: "-",
        }
        record = BaseMonitoringService.record_duration_metric
        record(CODE_EXECUTION_RUN_SECONDS_METRIC, elapsed_seconds, attributes=dict(labels))
        for name, value in (
            (CODE_EXECUTION_ACQUIRE_SECONDS_METRIC, sandbox_run.acquire_seconds),
            (CODE_EXECUTION_POD_START_SECONDS_METRIC, sandbox_run.pod_start_seconds),
            (CODE_EXECUTION_POD_SECONDS_METRIC, sandbox_run.pod_seconds),
        ):
            if value is not None:
                record(name, value, attributes=dict(labels))
