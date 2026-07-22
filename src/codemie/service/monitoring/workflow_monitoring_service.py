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

from typing import Optional

from codemie.configs import logger
from codemie.core.dependecies import get_current_project
from codemie.core.workflow_models import WorkflowConfig, WorkflowExecution, WorkflowExecutionStatusEnum, WorkflowMode
from codemie.rest_api.security.user import User
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import (
    MetricsAttributes,
    WORKFLOW_AI_REFINE_TOTAL_METRIC,
)


class WorkflowMonitoringService(BaseMonitoringService):
    WORKFLOW_BASE_METRIC = "workflow"
    WORKFLOW_EXECUTION_BASE_METRIC = "workflow_execution"

    @classmethod
    def send_workflow_execution_metric(
        cls,
        workflow_execution_config: Optional[WorkflowExecution],
        workflow_config: WorkflowConfig,
        user: User,
        request_id: str = None,
        status: Optional[WorkflowExecutionStatusEnum] = None,
        additional_attributes: Optional[dict] = None,
    ):
        """
        Send workflow execution metrics.

        If request_id is provided, sends individual metrics for each LLM run (with model info).
        Otherwise, sends single aggregated metric (backward compatibility).
        workflow_execution_config may be None when the DB record was deleted mid-run; in that
        case a request_id and status must be supplied so per-run metrics can still be emitted.
        """
        try:
            execution_status, execution_id, execution_time = cls._resolve_execution_state(
                workflow_execution_config, status, request_id
            )
            if request_id and cls._send_per_run_metrics(
                request_id, workflow_config, user, execution_status, execution_id, execution_time, additional_attributes
            ):
                return
            if workflow_execution_config:
                cls._send_aggregated_metric(
                    workflow_execution_config,
                    workflow_config,
                    user,
                    execution_status,
                    execution_id,
                    execution_time,
                    additional_attributes,
                )
        except Exception as e:
            logger.error(f"Failed to send workflow execution metric: {e}")

    @classmethod
    def _resolve_execution_state(
        cls,
        workflow_execution_config: Optional[WorkflowExecution],
        status: Optional[WorkflowExecutionStatusEnum],
        request_id: Optional[str],
    ):
        execution_status = workflow_execution_config.overall_status if workflow_execution_config else status
        execution_id = workflow_execution_config.execution_id if workflow_execution_config else request_id
        delta = (
            workflow_execution_config.update_date - workflow_execution_config.date
            if workflow_execution_config
            else None
        )
        execution_time = delta.total_seconds() if delta is not None else None
        return execution_status, execution_id, execution_time

    @classmethod
    def _build_execution_base_attributes(
        cls,
        workflow_config: WorkflowConfig,
        user: User,
        execution_status: Optional[WorkflowExecutionStatusEnum],
        execution_id: Optional[str],
    ) -> dict:
        return {
            MetricsAttributes.USER_ID: user.id,
            MetricsAttributes.USER_NAME: user.name,
            MetricsAttributes.USER_EMAIL: user.username,
            MetricsAttributes.WORKFLOW_NAME: workflow_config.name,
            MetricsAttributes.PROJECT: get_current_project(fallback=workflow_config.project),
            MetricsAttributes.STATUS: execution_status.name if execution_status else "UNKNOWN",
            MetricsAttributes.MODE: workflow_config.mode,
            MetricsAttributes.EXECUTION_ID: execution_id,
        }

    @classmethod
    def _send_per_run_metrics(
        cls,
        request_id: str,
        workflow_config: WorkflowConfig,
        user: User,
        execution_status: Optional[WorkflowExecutionStatusEnum],
        execution_id: Optional[str],
        execution_time: Optional[float],
        additional_attributes: Optional[dict],
    ) -> bool:
        from codemie.service.request_summary_manager import request_summary_manager

        summary = request_summary_manager.get_summary(request_id)
        if not summary or not summary.llm_runs:
            return False

        logger.debug(
            f"Sending {len(summary.llm_runs)} individual workflow LLM metrics for workflow {workflow_config.name}"
        )
        base = cls._build_execution_base_attributes(workflow_config, user, execution_status, execution_id)
        for llm_run in summary.llm_runs:
            run_attributes = {
                **base,
                MetricsAttributes.REQUEST_ID: request_id,
                MetricsAttributes.LLM_MODEL: llm_run.llm_model,
                MetricsAttributes.INPUT_TOKENS: llm_run.input_tokens,
                MetricsAttributes.OUTPUT_TOKENS: llm_run.output_tokens,
                MetricsAttributes.CACHE_READ_INPUT_TOKENS: llm_run.cached_tokens,
                MetricsAttributes.MONEY_SPENT: llm_run.money_spent,
                MetricsAttributes.CACHED_TOKENS_MONEY_SPENT: llm_run.cached_tokens_money_spent,
                MetricsAttributes.EXECUTION_TIME: execution_time,
            }
            if additional_attributes:
                run_attributes.update(additional_attributes)
            cls.send_count_metric(
                name=cls.WORKFLOW_EXECUTION_BASE_METRIC + "_total",
                attributes=run_attributes,
            )
        return True

    @classmethod
    def _send_aggregated_metric(
        cls,
        workflow_execution_config: WorkflowExecution,
        workflow_config: WorkflowConfig,
        user: User,
        execution_status: Optional[WorkflowExecutionStatusEnum],
        execution_id: Optional[str],
        execution_time: Optional[float],
        additional_attributes: Optional[dict],
    ):
        base = cls._build_execution_base_attributes(workflow_config, user, execution_status, execution_id)
        attributes = {
            **base,
            MetricsAttributes.INPUT_TOKENS: workflow_execution_config.tokens_usage.input_tokens,
            MetricsAttributes.OUTPUT_TOKENS: workflow_execution_config.tokens_usage.output_tokens,
            MetricsAttributes.CACHE_READ_INPUT_TOKENS: workflow_execution_config.tokens_usage.cached_tokens,
            MetricsAttributes.MONEY_SPENT: workflow_execution_config.tokens_usage.money_spent,
            MetricsAttributes.CACHED_TOKENS_MONEY_SPENT: (
                workflow_execution_config.tokens_usage.cached_tokens_money_spent
            ),
            MetricsAttributes.EXECUTION_TIME: execution_time,
        }
        if additional_attributes:
            attributes.update(additional_attributes)
        cls.send_count_metric(
            name=cls.WORKFLOW_EXECUTION_BASE_METRIC + "_total",
            attributes=attributes,
        )

    @classmethod
    def send_create_workflow_metric(
        cls,
        user_id: str,
        user_name: str,
        workflow_name: str,
        project: str,
        success: bool,
        workflow_id: str = "",
        additional_attributes: Optional[dict] = None,
        mode: WorkflowMode = WorkflowMode.SEQUENTIAL,
    ):
        attributes = cls._build_workflow_attributes(
            project, success, user_id, user_name, workflow_id, workflow_name, mode
        )
        if additional_attributes:
            attributes.update(additional_attributes)
        cls.send_count_metric(name=cls.WORKFLOW_BASE_METRIC + "_created_total", attributes=attributes)

    @classmethod
    def send_update_workflow_metric(
        cls,
        user_id: str,
        user_name: str,
        workflow_name: str,
        project: str,
        success: bool,
        workflow_id: str = "",
        additional_attributes: Optional[dict] = None,
        mode: WorkflowMode = WorkflowMode.SEQUENTIAL,
    ):
        attributes = cls._build_workflow_attributes(
            project, success, user_id, user_name, workflow_id, workflow_name, mode
        )
        if additional_attributes:
            attributes.update(additional_attributes)
        cls.send_count_metric(name=cls.WORKFLOW_BASE_METRIC + "_updated_total", attributes=attributes)

    @classmethod
    def send_delete_workflow_metric(
        cls,
        user_id: str,
        user_name: str,
        workflow_name: str,
        project: str,
        success: bool,
        workflow_id: str = "",
        additional_attributes: Optional[dict] = None,
        mode: WorkflowMode = WorkflowMode.SEQUENTIAL,
    ):
        attributes = cls._build_workflow_attributes(
            project, success, user_id, user_name, workflow_id, workflow_name, mode
        )
        if additional_attributes:
            attributes.update(additional_attributes)
        cls.send_count_metric(name=cls.WORKFLOW_BASE_METRIC + "_deleted_total", attributes=attributes)

    @classmethod
    def send_workflow_ai_refine_metric(
        cls,
        user_id: str,
        user_name: str,
        workflow_id: str,
        workflow_name: str,
        project: str,
        success: bool,
        llm_model: Optional[str] = None,
        mode: WorkflowMode = WorkflowMode.SEQUENTIAL,
        additional_attributes: Optional[dict] = None,
    ):
        attributes = cls._build_workflow_attributes(
            project, success, user_id, user_name, workflow_id, workflow_name, mode
        )
        if llm_model:
            attributes[MetricsAttributes.LLM_MODEL] = llm_model
        if additional_attributes:
            attributes.update(additional_attributes)
        cls.send_count_metric(name=WORKFLOW_AI_REFINE_TOTAL_METRIC, attributes=attributes)

    @classmethod
    def _build_workflow_attributes(cls, project, success, user_id, user_name, workflow_id, workflow_name, mode):
        return {
            MetricsAttributes.USER_ID: user_id,
            MetricsAttributes.USER_NAME: user_name,
            MetricsAttributes.WORKFLOW_NAME: workflow_name,
            MetricsAttributes.PROJECT: project,
            MetricsAttributes.STATUS: "success" if success else "error",
            MetricsAttributes.MODE: mode,
        }
