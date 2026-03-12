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

import base64
import json
from typing import List, Optional

from fastapi import APIRouter, status, Depends, Query, BackgroundTasks

from codemie.configs import logger
from codemie.core.ability import Ability, Action
from codemie.core.constants import MermaidMimeType
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import BaseResponse, BaseResponseWithData, CreatedByUser
from codemie.core.workflow_models import (
    CreateWorkflowRequest,
    UpdateWorkflowRequest,
    WorkflowConfig,
    WorkflowConfigTemplate,
    WorkflowListResponse,
    WorkflowErrorFormat,
)
from codemie.core.workflow_models.workflow_config import WorkflowMode
from codemie.rest_api.models.guardrail import GuardrailEntity
from codemie.rest_api.routers.utils import raise_access_denied, run_in_thread_pool, raise_not_found
from codemie.rest_api.security.authentication import authenticate, project_access_check
from codemie.rest_api.security.user import User
from codemie.service.monitoring.workflow_monitoring_service import WorkflowMonitoringService
from codemie.service.guardrail.guardrail_service import GuardrailService
from codemie.service.workflow_config import WorkflowConfigIndexService
from codemie.service.workflow_service import WorkflowService
from codemie.workflows.custom_node_info import CustomNodeInfoService
from codemie.core.workflow_models import CustomNodeSchemaResponse
from codemie.workflows.workflow import WorkflowExecutor

router = APIRouter(
    tags=["Workflow"],
    prefix="/v1",
    dependencies=[],
)
workflow_service = WorkflowService()
workflow_monitoring_service = WorkflowMonitoringService()

WORKFLOW_STARTED_BG_MSG = "Workflow has been triggered in the background"
WORKFLOW_CONFIGURATION_ERROR = "Workflow Configuration error"


@router.get(
    "/workflows/users",
    status_code=status.HTTP_200_OK,
    response_model=list[CreatedByUser],
)
def get_workflow_users(
    user: User = Depends(authenticate),
) -> list[CreatedByUser]:
    """
    Returns list of users who created workflows
    """
    result = WorkflowConfigIndexService.get_users(user=user)
    return result


@router.get(
    "/workflows",
    status_code=status.HTTP_200_OK,
    response_model=WorkflowListResponse,
    response_model_by_alias=True,
)
def get_workflows(
    user: User = Depends(authenticate),
    filter_by_user: bool = Query(False),
    page: int = 0,
    per_page: int = 10,
    filters: Optional[str] = None,
    minimal_response: bool = True,
):
    try:
        parsed_filters = json.loads(filters) if filters else None
    except json.JSONDecodeError:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid filters",
            details="Filters must be a valid encoded JSON object.",
            help="Please check the filters and ensure they are in the correct format. ",
        )

    return WorkflowConfigIndexService.run(
        user=user,
        filter_by_user=filter_by_user,
        page=page,
        per_page=per_page,
        filters=parsed_filters,
        minimal_response=minimal_response,
    )


@router.get(
    "/workflows/prebuilt",
    status_code=status.HTTP_200_OK,
    response_model=List[WorkflowConfigTemplate],
    response_model_by_alias=True,
    summary="Get prebuilt workflows",
    description="Retrieves a list of prebuilt workflows available in the system.",
)
def get_prebuilt_workflows():
    """
    Endpoint to retrieve prebuilt workflows.

    Utilizes the `get_prebuilt_workflows` method from the `WorkflowService` to fetch and return
    a list of prebuilt workflows. This endpoint is useful for clients to discover workflows
    that are readily available for use without the need for custom creation.

    Returns:
        A list of `WorkflowConfig` objects representing the prebuilt workflows.
    """
    prebuilt_workflows = workflow_service.get_prebuilt_workflows()
    return prebuilt_workflows


@router.get(
    "/workflows/prebuilt/{slug}",
    status_code=status.HTTP_200_OK,
    response_model=WorkflowConfigTemplate,
    response_model_by_alias=True,
    summary="Get prebuilt workflow template by slug",
    description="Retrieves a prebuilt workflow template by slug",
)
def get_prebuilt_workflow_by_slug(slug: str, user: User = Depends(authenticate)):
    """
    Endpoint to retrieve prebuilt workflow template by slug.

    Utilizes the `get_prebuilt_workflows` method from the `WorkflowService` to fetch and return
    a list of prebuilt workflows. This endpoint is useful for clients to discover workflows
    that are readily available for use without the need for custom creation.

    Returns:
       `WorkflowConfig` objects representing the prebuilt workflows.
    """
    try:
        prebuilt_workflows = workflow_service.get_prebuilt_workflows()
        template = next(item for item in prebuilt_workflows if item.slug == slug)
        template.project = user.current_project

        return template
    except StopIteration:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Workflow template not found",
            details=f"No workflow template found with the slug '{slug}'.",
            help="Please check the workflow slug and ensure it is correct. ",
        )


@router.get(
    "/workflows/id/{workflow_id}",
    status_code=status.HTTP_200_OK,
    response_model=WorkflowConfig,
    response_model_by_alias=True,
)
def get_workflow(workflow_id: str, user: User = Depends(authenticate)):
    try:
        workflow_config = workflow_service.get_workflow(workflow_id, user)
    except Exception as e:
        logger.error(str(e).strip())
        raise_not_found(resource_id=workflow_id, resource_type="Workflow")

    if not Ability(user).can(Action.READ, workflow_config):
        raise_access_denied("view")

    # Enrich with guardrail assignments
    workflow_config.guardrail_assignments = GuardrailService.get_entity_guardrail_assignments(
        user,
        GuardrailEntity.WORKFLOW,
        str(workflow_config.id),
    )

    return workflow_config


@router.post(
    "/workflows",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponseWithData,
    response_model_by_alias=True,
)
def create_workflow(
    request: CreateWorkflowRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(authenticate),
    error_format: WorkflowErrorFormat = Query(
        WorkflowErrorFormat.STRING, description="Error format: 'string' or 'json'"
    ),
):
    workflow_config = WorkflowConfig(**request.model_dump())
    # Prevent creation of autonomous workflows
    if workflow_config.mode == WorkflowMode.AUTONOMOUS:
        raise ExtendedHTTPException(
            code=status.HTTP_410_GONE,
            message="Autonomous workflows are disabled",
            details="Creating autonomous workflows is not allowed. Only sequential workflows can be created.",
            help="Please set the workflow mode to 'SEQUENTIAL' instead.",
        )
    project_access_check(user, request.project)
    try:
        WorkflowExecutor.validate_workflow(workflow_config=workflow_config, user=user, error_format=error_format)
        workflow_config = workflow_service.create_workflow(workflow_config, user)

        GuardrailService.sync_guardrail_assignments_for_entity(
            user=user,
            entity_type=GuardrailEntity.WORKFLOW,
            entity_id=str(workflow_config.id),
            entity_project_name=workflow_config.project,
            guardrail_assignments=request.guardrail_assignments,
        )

        background_tasks.add_task(run_in_thread_pool, update_workflow_schema, workflow_config, user)

        # Enrich with guardrail assignments
        workflow_config.guardrail_assignments = GuardrailService.get_entity_guardrail_assignments(
            user,
            GuardrailEntity.WORKFLOW,
            str(workflow_config.id),
        )
        return {"message": "Workflow created successfully", "data": workflow_config}
    except Exception as e:
        formatted_exception = e.message if isinstance(e, ExtendedHTTPException) else str(e).strip()
        details = (
            e.args[0]
            if error_format == WorkflowErrorFormat.JSON and e.args and isinstance(e.args[0], dict)
            else formatted_exception
        )
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=WORKFLOW_CONFIGURATION_ERROR,
            details=details,
            help="",
        ) from e


@router.put(
    "/workflows/{workflow_id}",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponseWithData,
    response_model_by_alias=True,
)
def update_workflow(
    workflow_id: str,
    request: UpdateWorkflowRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(authenticate),
    error_format: WorkflowErrorFormat = Query(
        WorkflowErrorFormat.STRING, description="Error format: 'string' or 'json'"
    ),
):
    try:
        workflow = workflow_service.get_workflow(workflow_id=workflow_id)
    except Exception:
        raise_not_found(resource_id=workflow_id, resource_type="Workflow")

    project_access_check(user, request.project)

    if not Ability(user).can(Action.WRITE, workflow):
        raise_access_denied("update")

    try:
        logger.debug(f"Update workflow. Request: {request}")
        updated_config = WorkflowConfig(**request.model_dump())

        # Prevent updating workflows to autonomous mode
        if updated_config.mode == WorkflowMode.AUTONOMOUS:
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message="Autonomous workflows are disabled",
                details="Updating workflows to autonomous mode is not allowed. Only sequential workflows can be used.",
                help="Please set the workflow mode to 'SEQUENTIAL' instead.",
            )
        WorkflowExecutor.validate_workflow(workflow_config=updated_config, user=user, error_format=error_format)
        updated_workflow = workflow_service.update_workflow(workflow, updated_config, user)

        GuardrailService.sync_guardrail_assignments_for_entity(
            user=user,
            entity_type=GuardrailEntity.WORKFLOW,
            entity_id=str(updated_workflow.id),
            entity_project_name=updated_workflow.project,
            guardrail_assignments=request.guardrail_assignments,
        )

        background_tasks.add_task(run_in_thread_pool, update_workflow_schema, updated_workflow, user)

        # Enrich with guardrail assignments
        updated_workflow.guardrail_assignments = GuardrailService.get_entity_guardrail_assignments(
            user,
            GuardrailEntity.WORKFLOW,
            str(updated_workflow.id),
        )
        return {"message": "Workflow updated successfully", "data": updated_workflow}
    except Exception as e:
        formatted_exception = str(e).strip()
        details = (
            e.args[0]
            if error_format == WorkflowErrorFormat.JSON and e.args and isinstance(e.args[0], dict)
            else formatted_exception
        )
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=WORKFLOW_CONFIGURATION_ERROR,
            details=details,
            help="",
        ) from e


@router.delete(
    "/workflows/{workflow_id}",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
    response_model_by_alias=True,
)
def delete_workflow(workflow_id: str, user: User = Depends(authenticate)):
    try:
        workflow = workflow_service.get_workflow(workflow_id=workflow_id)
    except KeyError as e:
        logger.error(str(e).strip())
        raise_not_found(resource_id=workflow_id, resource_type="Workflow")

    if not Ability(user).can(Action.DELETE, workflow):
        raise_access_denied("delete")

    workflow_service.delete_workflow(workflow, user)

    GuardrailService.remove_guardrail_assignments_for_entity(GuardrailEntity.WORKFLOW, str(workflow.id))

    return BaseResponse(message="Specified workflow removed")


@router.post(
    "/workflows/diagram",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponseWithData,
)
def create_workflow_diagram(request: CreateWorkflowRequest, user: User = Depends(authenticate)):
    try:
        workflow_config = WorkflowConfig(**request.model_dump())
        diagram = WorkflowExecutor.validate_workflow_and_draw(
            workflow_config=workflow_config, user=user, error_format=WorkflowErrorFormat.STRING
        )
        b64_diagram = base64.b64encode(diagram).decode("utf-8") if diagram else None
    except Exception as e:
        formatted_exception = str(e).strip()
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=WORKFLOW_CONFIGURATION_ERROR,
            details=f"{formatted_exception}",
            help="",
        ) from e

    if diagram is None:
        raise ExtendedHTTPException(
            code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message="Unable to generate workflow diagram",
            details="Mermaid is not available.",
            help="Try again later",
        )

    return BaseResponseWithData(
        data=f"data:{MermaidMimeType.SVG.value};base64,{b64_diagram}",
        message="Workflow diagram generated successfully",
    )


def update_workflow_schema(workflow_config, user):
    workflow_schema = WorkflowExecutor.validate_workflow_and_draw(workflow_config=workflow_config, user=user)
    workflow_service.save_workflow_schema(workflow_config, workflow_schema)


@router.get("/workflows/custom-nodes", response_model=list[str])
async def get_custom_nodes(user: User = Depends(authenticate)) -> list[str]:
    """Get list of all available custom node types."""
    node_ids = CustomNodeInfoService.get_node_ids()
    return node_ids


@router.get("/workflows/custom-nodes/{custom_node_id}/schema", response_model=CustomNodeSchemaResponse)
async def get_custom_node_schema(custom_node_id: str, user: User = Depends(authenticate)) -> CustomNodeSchemaResponse:
    """Get configuration schema for a specific custom node type."""
    return CustomNodeInfoService.get_node_schema(custom_node_id)
