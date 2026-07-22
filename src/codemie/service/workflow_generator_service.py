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

"""Service to generate workflow configurations from natural language queries."""

from __future__ import annotations

from typing import Optional

from codemie.configs import config
from codemie.configs.logger import current_user_email, logger, logging_user_id
from codemie.core.dependecies import get_project_for_metric
from codemie.core.exceptions import WorkflowGenerationError
from codemie.rest_api.models.guardrail import GuardrailAssignmentItem, GuardrailMode, GuardrailSource
from codemie.rest_api.models.workflow_generator import WorkflowGeneratorResponse, WorkflowRefineResponse
from codemie.rest_api.security.user import User
from codemie.service.guardrail.guardrail_service import GuardrailService
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.monitoring.base_monitoring_service import emit_llm_token_metric, send_log_metric
from codemie.service.monitoring.metrics_constants import (
    WORKFLOW_AI_REFINE_TOTAL_METRIC,
    WORKFLOW_GENERATOR_ERRORS_METRIC,
    WORKFLOW_GENERATOR_TOTAL_METRIC,
    MetricsAttributes,
)
from codemie.service.request_summary_manager import request_summary_manager
from codemie.service.tools.tools_info_service import ToolsInfoService
from codemie.service.workflow_service import WorkflowService
from codemie.workflows.workflow_generator.state import WorkflowGeneratorState
from codemie.workflows.workflow_generator.workflow import WorkflowGeneratorGraph
from codemie.workflows.workflow_generator.workflow_refiner import WorkflowRefinerGraph

_HELP_MESSAGE = "Try again with a different query or model."
_REFINE_HELP_MESSAGE = "Try again with a different instruction or model."


class WorkflowGeneratorService:
    @classmethod
    def generate(
        cls,
        nl_query: str,
        user: User,
        llm_model: Optional[str] = None,
        persist: bool = False,
        guardrail_ids: Optional[list[str]] = None,
        request_id: Optional[str] = None,
    ) -> WorkflowGeneratorResponse:
        if not llm_model:
            llm_model = config.WORKFLOW_GENERATOR_LLM_MODEL or llm_service.default_llm_model

        try:
            available_tools = ToolsInfoService.get_tools_info(user=user, exclude_toolkits=["Plugin"])

            initial_state: WorkflowGeneratorState = {
                "nl_query": nl_query,
                "user": user,
                "project": user.current_project,
                "available_tools": available_tools,
                "intent": None,
                "step_plans": None,
                "current_node_index": 0,
                "previous_node": None,
                "node_plan": None,
                "generated_config": None,
                "validation_errors": [],
                "validation_attempts": 0,
                "failed_step_ids": [],
                "result": None,
                "error": None,
            }

            graph = WorkflowGeneratorGraph(llm_model=llm_model, request_id=request_id)
            final_state = graph.run(initial_state)

            if final_state.get("error"):
                raise WorkflowGenerationError(
                    message="Workflow generation failed after validation retries",
                    details=final_state["error"],
                    help=_HELP_MESSAGE,
                )

            workflow_request = final_state["result"]

            if guardrail_ids:
                workflow_request.guardrail_assignments = [
                    GuardrailAssignmentItem(
                        guardrail_id=gid,
                        mode=GuardrailMode.ALL,
                        source=GuardrailSource.BOTH,
                    )
                    for gid in guardrail_ids
                ]

            workflow_id: Optional[str] = None
            if persist:
                from codemie.core.workflow_models.workflow_config import WorkflowConfig
                from codemie.rest_api.models.guardrail import GuardrailEntity
                from codemie.workflows.workflow import WorkflowExecutor

                workflow_config = WorkflowConfig(**workflow_request.model_dump())
                WorkflowExecutor.validate_workflow(workflow_config=workflow_config, user=user)
                workflow_config = WorkflowService().create_workflow(workflow_config, user)
                GuardrailService.sync_guardrail_assignments_for_entity(
                    user=user,
                    entity_type=GuardrailEntity.WORKFLOW,
                    entity_id=str(workflow_config.id),
                    entity_project_name=workflow_config.project,
                    guardrail_assignments=workflow_request.guardrail_assignments,
                )
                workflow_id = str(workflow_config.id)

            emit_llm_token_metric(
                name=WORKFLOW_GENERATOR_TOTAL_METRIC,
                request_id=request_id,
                base_attributes={
                    MetricsAttributes.LLM_MODEL: llm_model,
                    MetricsAttributes.USER_ID: logging_user_id.get("-"),
                    MetricsAttributes.USER_NAME: current_user_email.get("-"),
                    MetricsAttributes.PROJECT: get_project_for_metric(),
                },
            )

            return WorkflowGeneratorResponse(
                workflow_config=workflow_request,
                workflow_id=workflow_id,
            )

        except WorkflowGenerationError:
            raise
        except Exception as exc:
            logger.error(f"Failed to generate workflow: {exc}", exc_info=True)
            send_log_metric(
                name=WORKFLOW_GENERATOR_ERRORS_METRIC,
                attributes={
                    MetricsAttributes.LLM_MODEL: llm_model or "default",
                    MetricsAttributes.USER_ID: logging_user_id.get("-"),
                    MetricsAttributes.USER_NAME: current_user_email.get("-"),
                    MetricsAttributes.PROJECT: get_project_for_metric(),
                },
            )
            raise WorkflowGenerationError(
                message="Failed to generate workflow",
                details="An error occurred while generating workflow. Check server logs for details.",
                help=_HELP_MESSAGE,
            ) from exc
        finally:
            if request_id:
                request_summary_manager.clear_summary(request_id)

    @classmethod
    def refine_workflow(
        cls,
        yaml_config: str,
        refine_prompt: Optional[str],
        user: User,
        llm_model: Optional[str],
        request_id: Optional[str],
    ) -> WorkflowRefineResponse:
        if not llm_model:
            llm_model = config.WORKFLOW_GENERATOR_LLM_MODEL or llm_service.default_llm_model

        try:
            available_tools = ToolsInfoService.get_tools_info(user=user, exclude_toolkits=["Plugin"])

            initial_state: WorkflowGeneratorState = {
                "nl_query": "",
                "user": user,
                "project": user.current_project,
                "available_tools": available_tools,
                "existing_yaml_config": yaml_config,
                "refine_prompt": refine_prompt or "",
                "intent": None,
                "step_plans": None,
                "current_node_index": 0,
                "previous_node": None,
                "node_plan": None,
                "generated_config": None,
                "validation_errors": [],
                "validation_attempts": 0,
                "failed_step_ids": [],
                "result": None,
                "error": None,
            }

            graph = WorkflowRefinerGraph(llm_model=llm_model, request_id=request_id)
            final_state = graph.run(initial_state)

            if final_state.get("error"):
                raise WorkflowGenerationError(
                    message="Workflow refinement failed after validation retries",
                    details=final_state["error"],
                    help=_REFINE_HELP_MESSAGE,
                )

            if final_state.get("result") is None:
                raise WorkflowGenerationError(
                    message="Workflow refinement produced no result",
                    details="Graph completed without a result.",
                    help=_REFINE_HELP_MESSAGE,
                )

            emit_llm_token_metric(
                name=WORKFLOW_AI_REFINE_TOTAL_METRIC,
                request_id=request_id,
                base_attributes={
                    MetricsAttributes.LLM_MODEL: llm_model,
                    MetricsAttributes.USER_ID: logging_user_id.get("-"),
                    MetricsAttributes.USER_NAME: current_user_email.get("-"),
                    MetricsAttributes.PROJECT: get_project_for_metric(),
                },
            )

            return WorkflowRefineResponse(yaml_config=final_state["result"].yaml_config)

        except WorkflowGenerationError:
            raise
        except Exception as exc:
            logger.error(f"Failed to refine workflow: {exc}", exc_info=True)
            raise WorkflowGenerationError(
                message="Failed to refine workflow",
                details=f"An error occurred while refining the workflow: {str(exc)}",
                help=_REFINE_HELP_MESSAGE,
            ) from exc
        finally:
            if request_id:
                request_summary_manager.clear_summary(request_id)
