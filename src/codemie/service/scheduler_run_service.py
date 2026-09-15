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

from __future__ import annotations

from typing import Any

from codemie.repository.scheduler_run_repository import SchedulerRunRepository
from codemie.rest_api.models.scheduler_run import (
    LogEntry,
    ResourceRef,
    ProjectRef,
    RunMetrics,
    RunResult,
    SchedulerConfig,
    SchedulerRun,
    SchedulerRunDetail,
    SchedulerRunStats,
)
from codemie.rest_api.routers.utils import raise_conflict, raise_not_found


class SchedulerRunService:
    def __init__(self) -> None:
        self._repo = SchedulerRunRepository()

    def list_runs(self, **kwargs) -> tuple[list[SchedulerRun], int]:
        return self._repo.list_runs(**kwargs)

    def get_stats(self, filters: dict[str, Any]) -> SchedulerRunStats:
        raw = self._repo.get_stats(**filters)
        return SchedulerRunStats(**raw)

    def get_run(self, run_id: str) -> SchedulerRun:
        run = self._repo.get_by_id(run_id)
        if run is None:
            raise_not_found(run_id, "SchedulerRun")
        return run

    def delete_run(self, run_id: str) -> None:
        guard = self._repo.delete_run(run_id)
        if guard == "not_found":
            raise_not_found(run_id, "SchedulerRun")
        if guard == "running":
            raise_conflict("Cannot delete a run with status 'running'")

    def get_logs(self, run_id: str, page: int = 0, per_page: int = 50) -> tuple[list[dict], int]:
        self.get_run(run_id)  # raises 404 if missing
        return self._repo.get_logs_page(run_id, page, per_page)

    @staticmethod
    def _build_metrics(run: SchedulerRun) -> RunMetrics | None:
        if run.metrics:
            return RunMetrics(
                inputTokens=run.metrics.get("inputTokens"),
                outputTokens=run.metrics.get("outputTokens"),
                cost=run.metrics.get("cost"),
            )
        if run.conversation_id:
            try:
                from codemie.rest_api.models.conversation import ConversationMetrics

                cm = ConversationMetrics.get_by_conversation_id(run.conversation_id)
                return RunMetrics(
                    inputTokens=cm.total_input_tokens,
                    outputTokens=cm.total_output_tokens,
                    cost=cm.total_money_spent,
                )
            except Exception:
                pass
        if run.resource_execution_id:
            try:
                from codemie.core.workflow_models.workflow_execution import WorkflowExecution

                executions = WorkflowExecution.get_by_execution_id(run.resource_execution_id)
                if executions:
                    tu = executions[0].tokens_usage
                    if tu:
                        return RunMetrics(
                            inputTokens=tu.input_tokens,
                            outputTokens=tu.output_tokens,
                            cost=tu.money_spent,
                        )
            except Exception:
                pass
        return None

    @staticmethod
    def _build_logs(run: SchedulerRun) -> list[LogEntry]:
        logs = [LogEntry(**entry) for entry in (run.logs or [])]
        if run.error_data:
            error_ts = run.finished_at.isoformat() if run.finished_at else run.started_at.isoformat()
            logs.append(
                LogEntry(
                    timestamp=error_ts,
                    level="ERROR",
                    message=run.error_data.get("message", "Unknown error"),
                    step=run.error_data.get("type"),
                )
            )
        return logs

    @staticmethod
    def _extract_setting_fields(setting) -> dict:
        from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService

        _gcv = SchedulerSettingsService._get_cred_value
        return {
            "cron_expr": _gcv(setting, "schedule") or "" if setting else "",
            "timezone": _gcv(setting, "timezone") or "UTC" if setting else "UTC",
            "resource_type": _gcv(setting, "resource_type") or "" if setting else "",
            "resource_id": _gcv(setting, "resource_id") or "" if setting else "",
            "resource_name": _gcv(setting, "resource_name") or "" if setting else "",
            "project_name": setting.project_name if setting else "",
            "scheduler_name": setting.alias if setting else "",
        }

    @staticmethod
    def _build_sched_config(cron_expr: str, timezone: str) -> "SchedulerConfig | None":
        if not cron_expr:
            return None
        human_readable = cron_expr
        try:
            import cron_descriptor

            human_readable = cron_descriptor.get_description(cron_expr)
        except Exception:
            pass
        return SchedulerConfig(
            cron=cron_expr,
            humanReadableSchedule=human_readable,
            timezone=f"{timezone} (UTC)",
        )

    def get_run_detail(self, run_id: str) -> SchedulerRunDetail:
        from codemie.rest_api.models.settings import Settings

        run = self.get_run(run_id)
        setting = Settings.get_by_id(run.scheduler_id) if run.scheduler_id else None
        sf = self._extract_setting_fields(setting)

        metrics = self._build_metrics(run)
        logs = self._build_logs(run)

        return SchedulerRunDetail(
            id=run.id,
            scheduler={"id": run.scheduler_id or "", "name": sf["scheduler_name"]},
            resource=ResourceRef(
                id=sf["resource_id"],
                name=sf["resource_name"],
                type=sf["resource_type"].capitalize(),
            ),
            project=ProjectRef(id=sf["project_name"], name=sf["project_name"]),
            status=run.status,
            trigger=run.trigger,
            startedAt=run.started_at.isoformat(),
            finishedAt=run.finished_at.isoformat() if run.finished_at else None,
            durationMs=run.duration_ms,
            executionId=run.execution_id,
            schedulerConfig=self._build_sched_config(sf["cron_expr"], sf["timezone"]),
            input=run.input_data,
            result=RunResult(
                available=run.result_data is not None,
                content=run.result_data.get("content") if run.result_data else None,
            ),
            logs=logs,
            metrics=metrics,
            conversationId=run.conversation_id,
            error=run.error_data,
            resourceExecutionId=run.resource_execution_id,
            workflowExecutionId=run.resource_execution_id,
            inputTokens=metrics.inputTokens if metrics else None,
            outputTokens=metrics.outputTokens if metrics else None,
            executionCost=metrics.cost if metrics else None,
        )
