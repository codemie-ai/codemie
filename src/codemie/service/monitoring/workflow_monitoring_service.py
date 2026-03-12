# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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
from codemie.core.workflow_models import WorkflowConfig, WorkflowExecution, WorkflowMode
from codemie.rest_api.security.user import User
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import MetricsAttributes


class WorkflowMonitoringService(BaseMonitoringService):
    WORKFLOW_BASE_METRIC = "workflow"
    WORKFLOW_EXECUTION_BASE_METRIC = "workflow_execution"

    @classmethod
    def send_workflow_execution_metric(
        cls,
        workflow_execution_config: WorkflowExecution,
        workflow_config: WorkflowConfig,
        user: User,
        request_id: str = None,
        additional_attributes: Optional[dict] = None,
    ):
        """
        Send workflow execution metrics.

        If request_id is provided, sends individual metrics for each LLM run (with model info).
        Otherwise, sends single aggregated metric (backward compatibility).
        """
        try:
            delta = workflow_execution_config.update_date - workflow_execution_config.date

            # If request_id provided, send individual metrics per LLM run
            if request_id:
                from codemie.service.request_summary_manager import request_summary_manager

                summary = request_summary_manager.get_summary(request_id)

                if summary and summary.llm_runs:
                    logger.debug(
                        f"Sending {len(summary.llm_runs)} individual workflow LLM metrics "
                        f"for workflow {workflow_config.name}"
                    )

                    for llm_run in summary.llm_runs:
                        run_attributes = {
                            MetricsAttributes.USER_ID: user.id,
                            MetricsAttributes.USER_NAME: user.name,
                            MetricsAttributes.USER_EMAIL: user.username,
                            MetricsAttributes.WORKFLOW_NAME: workflow_config.name,
                            MetricsAttributes.PROJECT: workflow_config.project,
                            MetricsAttributes.STATUS: workflow_execution_config.overall_status.name,
                            MetricsAttributes.MODE: workflow_config.mode,
                            MetricsAttributes.EXECUTION_ID: workflow_execution_config.execution_id,
                            MetricsAttributes.LLM_MODEL: llm_run.llm_model,  # Include model for each run
                            MetricsAttributes.INPUT_TOKENS: llm_run.input_tokens,
                            MetricsAttributes.OUTPUT_TOKENS: llm_run.output_tokens,
                            MetricsAttributes.CACHE_READ_INPUT_TOKENS: llm_run.cached_tokens,
                            MetricsAttributes.MONEY_SPENT: llm_run.money_spent,
                            MetricsAttributes.CACHED_TOKENS_MONEY_SPENT: llm_run.cached_tokens_money_spent,
                            MetricsAttributes.EXECUTION_TIME: delta.total_seconds(),
                        }
                        if additional_attributes:
                            run_attributes.update(additional_attributes)

                        cls.send_count_metric(
                            name=cls.WORKFLOW_EXECUTION_BASE_METRIC + "_total",
                            attributes=run_attributes,
                        )
                    return  # Don't send aggregated metric if we sent individual ones

            # Fallback: send aggregated metric (backward compatibility)
            attributes = {
                MetricsAttributes.USER_ID: user.id,
                MetricsAttributes.USER_NAME: user.name,
                MetricsAttributes.USER_EMAIL: user.username,
                MetricsAttributes.WORKFLOW_NAME: workflow_config.name,
                MetricsAttributes.PROJECT: workflow_config.project,
                MetricsAttributes.STATUS: workflow_execution_config.overall_status.name,
                MetricsAttributes.MODE: workflow_config.mode,
                MetricsAttributes.EXECUTION_ID: workflow_execution_config.execution_id,
                MetricsAttributes.INPUT_TOKENS: workflow_execution_config.tokens_usage.input_tokens,
                MetricsAttributes.OUTPUT_TOKENS: workflow_execution_config.tokens_usage.output_tokens,
                MetricsAttributes.CACHE_READ_INPUT_TOKENS: workflow_execution_config.tokens_usage.cached_tokens,
                MetricsAttributes.MONEY_SPENT: workflow_execution_config.tokens_usage.money_spent,
                MetricsAttributes.CACHED_TOKENS_MONEY_SPENT: (
                    workflow_execution_config.tokens_usage.cached_tokens_money_spent
                ),
                MetricsAttributes.EXECUTION_TIME: delta.total_seconds(),
            }
            if additional_attributes:
                attributes.update(additional_attributes)
            cls.send_count_metric(
                name=cls.WORKFLOW_EXECUTION_BASE_METRIC + "_total",
                attributes=attributes,
            )
        except Exception as e:
            logger.error(f"Failed to send workflow execution metric: {e}")

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
    def _build_workflow_attributes(cls, project, success, user_id, user_name, workflow_id, workflow_name, mode):
        return {
            MetricsAttributes.USER_ID: user_id,
            MetricsAttributes.USER_NAME: user_name,
            MetricsAttributes.WORKFLOW_NAME: workflow_name,
            MetricsAttributes.PROJECT: project,
            MetricsAttributes.STATUS: "success" if success else "error",
            MetricsAttributes.MODE: mode,
        }
