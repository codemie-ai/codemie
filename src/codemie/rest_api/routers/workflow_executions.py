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

import json
import threading
from typing import Dict, Optional, List, Annotated

from elasticsearch import NotFoundError
from fastapi import APIRouter, Query, status, Depends, BackgroundTasks, Body, Request
from starlette.responses import StreamingResponse

from codemie.configs import logger
from codemie.core.ability import Ability, Action
from codemie.core.dependecies import set_disable_prompt_cache
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import BaseResponse
from codemie.core.thread import ThreadedGenerator
from codemie.core.dual_queue import DualQueue
from codemie.core.thought_queue import ThoughtQueue
from codemie.core.workflow_models import (
    WorkflowExecutionState,
    WorkflowExecutionResponse,
    WorkflowExecutionStateResponse,
    WorkflowExecutionStateOutput,
    WorkflowExecutionTransitionResponse,
    CreateWorkflowExecutionRequest,
    WorkflowExecutionStateThoughtWithChildren,
    UpdateWorkflowExecutionOutputRequest,
    WorkflowExecutionOutputChangeRequest,
)
from codemie.core.utils import generate_zip, format_json_content, format_markdown_content
from codemie.core.workflow_models.workflow_config import WorkflowConfigBase, WorkflowMode
from codemie.rest_api.models.base import PaginatedListResponse
from codemie.rest_api.routers.utils import raise_access_denied
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.rest_api.utils.request_utils import extract_custom_headers
from codemie.service.aws_bedrock.bedrock_flow_service import BedrockFlowService
from codemie.service.request_summary_manager import request_summary_manager as request_summary_manager_module
from codemie.service.workflow_execution import (
    WorkflowExecutionService,
    WorkflowThoughtsListService,
    WorkflowExecutionStatesIndexService,
    WorkflowExecutionTransitionsIndexService,
)
from codemie.service.workflow_service import WorkflowService
from codemie.service.workflow_execution.workflow_update_output_service import WorkflowUpdateOutputService
from codemie.service.workflow_execution.workflow_output_change_request_service import WorkflowOutputChangeRequestService
from codemie.workflows.workflow import WorkflowExecutor
from codemie.workflows.utils.html_utils import generate_html_report

router = APIRouter(
    tags=["WorkflowExecutions"],
    prefix="/v1",
    dependencies=[],
)

WORKFLOW_STARTED_BG_MSG = "Workflow has been started"
NDJSON_MEDIA_TYPE = "application/x-ndjson"


def _validate_workflow_access(workflow_id: str, user: User):
    """Validate workflow exists and user has READ access.

    Args:
        workflow_id: The workflow configuration ID
        user: Authenticated user

    Returns:
        Workflow configuration object

    Raises:
        ExtendedHTTPException: 404 if workflow not found, 403 if access denied
    """
    try:
        workflow_config = WorkflowService().get_workflow(workflow_id)
    except KeyError:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Workflow Not Found",
            details="The specified workflow was not found.",
            help="Please ensure that the workflow exists and try again. If the issue persists, contact support.",
        )

    if not Ability(user).can(Action.READ, workflow_config):
        raise_access_denied("view")

    return workflow_config


def _validate_execution_access(execution_id: str, user: User, expected_workflow_id: str | None = None):
    """Validate execution exists, belongs to workflow, and user has READ access via Ability framework."""
    execution = WorkflowService().find_workflow_execution_by_id(execution_id)
    if not execution:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Execution Not Found",
            details="The specified execution was not found.",
            help="",
        )

    # Verify execution belongs to expected workflow
    if expected_workflow_id and execution.workflow_id != expected_workflow_id:
        logger.warning(
            f"Workflow-execution mismatch: execution {execution_id} "
            f"belongs to workflow {execution.workflow_id}, not {expected_workflow_id}"
        )
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid Request",
            details=f"Execution {execution_id} does not belong to workflow {expected_workflow_id}.",
            help="Please verify the workflow_id and execution_id match.",
        )

    if not Ability(user).can(Action.READ, execution):
        raise_access_denied("view")

    return execution


@router.get(
    "/workflows/recent",
    status_code=status.HTTP_200_OK,
    summary="Get recently used workflows for quick access",
)
def get_recent_workflows(
    user: User = Depends(authenticate),
    limit: int = Query(default=3, ge=1, le=10, description="Number of recent workflows to return"),
):
    """
    Get recently used workflows for the authenticated user.
    Returns unique workflows (not individual conversations) ordered by most recent use.

    This endpoint is designed for quick access to recently used workflows,
    similar to "recent assistants" functionality.

    Args:
        user: Authenticated user
        limit: Maximum number of workflows to return (default: 3, max: 10)

    Returns:
        List of recently used workflows with metadata
    """
    try:
        recent_workflows = WorkflowService.get_recent_workflows_for_user(user, limit=limit)
        return recent_workflows
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to Get Recent Workflows",
            details=f"An unexpected error occurred while fetching recent workflows: {str(e)}",
            help="Please try again. If the issue persists, contact support.",
        ) from e


@router.get(
    "/workflows/{workflow_id}/executions",
    status_code=status.HTTP_200_OK,
)
def get_workflow_executions(
    workflow_id: str,
    user: User = Depends(authenticate),
    page: int = 0,
    per_page: int = 10,
    filter_by_project: bool = Query(default=True),
):
    try:
        workflow_config = WorkflowService().get_workflow(workflow_id)
    except Exception:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Workflow Not Found",
            details=f"The workflow with ID [{workflow_id}] could not be found in the system.",
            help="Please ensure that the workflow exists and try again. If the issue persists, contact support.",
        )

    if not Ability(user).list(workflow_config):
        raise_access_denied("list")

    _validate_remote_entities_and_raise(workflow_config)

    execs = WorkflowService().get_workflow_execution_list(
        workflow_id=workflow_id,
        project=workflow_config.project,
        user=user,
        page=page,
        per_page=per_page,
        filter_by_project=filter_by_project,
    )
    return execs


@router.get(
    "/workflows/{workflow_id}/executions/{execution_id}",
    status_code=status.HTTP_200_OK,
    response_model=WorkflowExecutionResponse,
    response_model_by_alias=True,
)
def get_workflow_execution(execution_id: str, user: User = Depends(authenticate)):
    execution = WorkflowService().find_workflow_execution_by_id(execution_id)

    if not execution:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Execution Not Found",
            details="The specified execution was not found.",
            help="",
        )

    if not Ability(user).can(Action.READ, execution):
        raise_access_denied("view")

    return WorkflowExecutionResponse(**execution.model_dump())


@router.post(
    "/workflows/{workflow_id}/executions",
    status_code=status.HTTP_200_OK,
)
def create_workflow_execution(
    request: CreateWorkflowExecutionRequest,
    workflow_id: str,
    background_tasks: BackgroundTasks,
    raw_request: Request,
    user: User = Depends(authenticate),
):
    workflow_config = WorkflowService().get_workflow(workflow_id)

    if not Ability(user).can(Action.READ, workflow_config):
        raise_access_denied("view")

    # Prevent execution of autonomous workflows
    if workflow_config.mode == WorkflowMode.AUTONOMOUS:
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Autonomous workflows are disabled",
            details="Autonomous workflows cannot be executed. Only sequential workflows are allowed.",
            help="Please use a sequential workflow instead.",
        )

    _validate_remote_entities_and_raise(workflow_config)
    _validate_workflow_supports_files_and_raise(workflow_config, request.file_name)

    user_model = user.as_user_model()

    execution = WorkflowService().create_workflow_execution(
        workflow_config,
        user=user_model,
        user_input=request.user_input,
        file_name=request.file_name,
        conversation_id=request.conversation_id,
    )
    request_summary_manager_module.create_request_summary(
        request_id=execution.execution_id,
        project_name=workflow_config.project,
        user=user_model,
    )

    # Extract custom headers if propagation is enabled
    request_headers = extract_custom_headers(raw_request, request.propagate_headers)

    # Set cache control flag for this request
    set_disable_prompt_cache(request.disable_cache or False)

    try:
        # Handle streaming mode - create ThreadedGenerator and pass to executor
        if request.stream:
            # Create ThreadedGenerator for streaming to client
            generator_queue = ThreadedGenerator(
                request_uuid=execution.execution_id,
                user_id=user.id,
                conversation_id=execution.execution_id,
            )

            # Create ThoughtQueue for database persistence
            thought_queue = ThoughtQueue()
            thought_queue.set_context('user_id', user.id)

            # Wrap both queues in DualQueue to enable parallel processing
            # - ThreadedGenerator: streams thoughts to client
            # - ThoughtQueue: saves thoughts to database via ThoughtConsumer
            dual_queue = DualQueue(
                streaming_queue=generator_queue,
                persistence_queue=thought_queue,
            )

            workflow = WorkflowExecutor.create_executor(
                workflow_config=workflow_config,
                user_input=execution.prompt,  # Use augmented prompt from execution (includes history)
                file_name=request.file_name,
                user=user,
                resume_execution=False,
                execution_id=execution.execution_id,
                request_headers=request_headers,
                thought_queue=dual_queue,  # Pass dual queue instead of generator_queue
                session_id=request.session_id,
                disable_cache=request.disable_cache,
                tags=request.tags,
                delete_on_completion=request.delete_on_completion,
            )

            return _handle_streaming_execution(workflow, raw_request, generator_queue)

        # Handle background mode (existing behavior) - no queue provided, will create ThoughtQueue
        workflow = WorkflowExecutor.create_executor(
            workflow_config=workflow_config,
            user_input=request.user_input,
            file_name=request.file_name,
            user=user,
            resume_execution=False,
            execution_id=execution.execution_id,
            request_headers=request_headers,
            session_id=request.session_id,
            disable_cache=request.disable_cache,
            tags=request.tags,
            delete_on_completion=request.delete_on_completion,
        )

        stream = getattr(workflow, "stream")
        background_tasks.add_task(stream)

        return execution
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Workflow Error",
            details=f"An unexpected error occurred during workflow execution: {str(e)}",
            help="We apologize for the inconvenience. Here are some steps you can try:\n"
            "1. Retry your request after a short delay.\n"
            "2. Check if your input is within the expected parameters.\n"
            "If you continue to experience issues, please contact our support team "
            "with the timestamp of your request and any error messages you received.",
        ) from e


@router.put(
    "/workflows/{workflow_id}/executions/{execution_id}/abort",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
    summary="Cancel interrupted workflow execution",
)
def abort_workflow_execution(workflow_id: str, execution_id: str, user: User = Depends(authenticate)):
    workflow_config = WorkflowService().get_workflow(workflow_id)

    if not Ability(user).can(Action.READ, workflow_config):
        raise_access_denied("view")

    WorkflowExecutionService(workflow_config=workflow_config, workflow_execution_id=execution_id, user=user).abort()

    return BaseResponse(message="Workflow execution has been aborted")


@router.put(
    "/workflows/{workflow_id}/executions/{execution_id}/resume",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
    summary="Resume interrupted workflow execution",
)
def resume_workflow_execution(
    workflow_id: str,
    execution_id: str,
    background_tasks: BackgroundTasks,
    raw_request: Request,
    user: User = Depends(authenticate),
    propagate_headers: bool = Query(default=False, description="Propagate X-* headers to MCP servers"),
    disable_cache: bool = Query(default=True, description="Disable prompt caching for resumed execution"),
):
    workflow_config = WorkflowService().get_workflow(workflow_id)

    if not Ability(user).can(Action.READ, workflow_config):
        raise_access_denied("view")

    # Prevent execution of autonomous workflows
    if workflow_config.mode == WorkflowMode.AUTONOMOUS:
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Autonomous workflows are disabled",
            details="Autonomous workflows cannot be executed. Only sequential workflows are allowed.",
            help="Please use a sequential workflow instead.",
        )

    execution = WorkflowService().find_workflow_execution_by_id(execution_id)
    request_summary_manager_module.create_request_summary(
        request_id=execution.execution_id,
        project_name=workflow_config.project,
        user=user.as_user_model(),
    )

    # Extract custom headers if propagation is enabled
    request_headers = extract_custom_headers(raw_request, propagate_headers)

    # Set cache control flag for this request
    set_disable_prompt_cache(disable_cache)

    try:
        workflow = WorkflowExecutor.create_executor(
            workflow_config=workflow_config,
            user_input="",
            user=user,
            resume_execution=True,
            execution_id=execution.execution_id,
            request_headers=request_headers,
        )

        stream = getattr(workflow, "stream")
        background_tasks.add_task(stream)
        return BaseResponse(message="Workflow execution has been resumed")
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Workflow Error",
            details=f"An unexpected error occurred during workflow execution: {str(e)}",
            help="We apologize for the inconvenience. Here are some steps you can try:\n"
            "1. Retry your request after a short delay.\n"
            "2. Check if your input is within the expected parameters.\n"
            "If you continue to experience issues, please contact our support team "
            "with the timestamp of your request and any error messages you received.",
        ) from e


@router.put(
    "/workflows/{workflow_id}/executions/{execution_id}/output",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
    summary="Edit output of last workflow state on 'interrupt_before'",
)
def update_workflow_execution_output(
    workflow_id: str, execution_id: str, body: UpdateWorkflowExecutionOutputRequest, user: User = Depends(authenticate)
):
    workflow_config = WorkflowService().get_workflow(workflow_id)

    if not Ability(user).can(Action.READ, workflow_config):
        raise_access_denied("view")

    try:
        WorkflowUpdateOutputService.run(execution_id=execution_id, state_id=body.state_id, new_output=body.output)

        return BaseResponse(message="Workflow execution output has been edited successfully")
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Workflow Output Editing Error",
            details=f"An unexpected error occurred during workflow output editing: {str(e)}",
            help="",
        ) from e


@router.put(
    "/workflows/{workflow_id}/executions/{execution_id}/output/request_changes",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
    summary="Request output changes with LLM",
)
def request_workflow_execution_output_changes(
    workflow_id: str, execution_id: str, body: WorkflowExecutionOutputChangeRequest, user: User = Depends(authenticate)
):
    workflow_config = WorkflowService().get_workflow(workflow_id)

    if not Ability(user).can(Action.READ, workflow_config):
        raise_access_denied("view")

    try:
        result = WorkflowOutputChangeRequestService.run(
            original_output=body.original_output, changes_request=body.request
        )
        return BaseResponse(message=result)
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Workflow Output Change Request Error",
            details=f"An unexpected error occurred during workflow output change request: {str(e)}",
            help="",
        ) from e


@router.get(
    "/workflows/{workflow_id}/executions/{execution_id}/states",
    status_code=status.HTTP_200_OK,
    response_model=PaginatedListResponse[WorkflowExecutionStateResponse],
    summary="Get states of a workflow execution with root-level thoughts",
)
def index_workflow_execution_states(
    workflow_id: str, execution_id: str, page: int = 0, per_page: int = 10, user: User = Depends(authenticate)
):
    try:
        workflow_config = WorkflowService().get_workflow(workflow_id)
    except KeyError:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Workflow Not Found",
            details="The specified workflow was not found.",
            help="Please ensure that the workflow exists try again. If the issue persists, contact support.",
        )

    if not Ability(user).can(Action.READ, workflow_config):
        raise_access_denied("view")

    return WorkflowExecutionStatesIndexService.run(
        execution_id=execution_id, page=page, per_page=per_page, retrieve_model=WorkflowExecutionStateResponse
    )


@router.get(
    "/workflows/{workflow_id}/executions/{execution_id}/transitions",
    status_code=status.HTTP_200_OK,
    response_model=PaginatedListResponse[WorkflowExecutionTransitionResponse],
    summary="Get all node-to-node transitions of a workflow execution",
)
def index_workflow_execution_transitions(
    workflow_id: str,
    execution_id: str,
    page: int = 0,
    per_page: int = 10,
    user: User = Depends(authenticate),
):
    """Retrieve paginated list of all workflow execution transitions.

    Returns all state-to-state transitions for a workflow execution, ordered chronologically.
    Each transition captures the complete workflow context at the moment of transition.

    Args:
        workflow_id: The workflow configuration ID
        execution_id: The specific execution instance ID
        page: Zero-based page index (default: 0)
        per_page: Number of items per page (default: 10)
        user: Authenticated user (injected)

    Returns:
        PaginatedListResponse containing transition records with pagination metadata

    Raises:
        404: Workflow not found
        403: User lacks READ permission on workflow
        500: Unexpected error during retrieval
    """
    _validate_workflow_access(workflow_id, user)
    _validate_execution_access(execution_id, user, expected_workflow_id=workflow_id)

    return WorkflowExecutionTransitionsIndexService.run(
        execution_id=execution_id,
        page=page,
        per_page=per_page,
    )


@router.get(
    "/workflows/{workflow_id}/executions/{execution_id}/transitions/from/{state_id}",
    status_code=status.HTTP_200_OK,
    response_model=WorkflowExecutionTransitionResponse,
    summary="Get single transition originating from a specific state",
)
def get_workflow_execution_transitions_from_state(
    workflow_id: str,
    execution_id: str,
    state_id: str,
    user: User = Depends(authenticate),
):
    """Retrieve single transition that originated from a specific state.

    Returns the transition record where the given state was the source (from_state_id).
    Useful for tracking what happened after a particular state completed execution.

    Args:
        workflow_id: The workflow configuration ID
        execution_id: The specific execution instance ID
        state_id: The source state ID
        user: Authenticated user (injected)

    Returns:
        WorkflowExecutionTransitionResponse: Single transition record

    Raises:
        404: Workflow or transition not found
        403: User lacks READ permission on workflow
        500: Unexpected error during retrieval
    """
    _validate_workflow_access(workflow_id, user)
    _validate_execution_access(execution_id, user, expected_workflow_id=workflow_id)

    return WorkflowExecutionTransitionsIndexService.get_by_from_state(
        execution_id=execution_id,
        from_state_id=state_id,
    )


@router.get(
    "/workflows/{workflow_id}/executions/{execution_id}/transitions/to/{state_id}",
    status_code=status.HTTP_200_OK,
    response_model=WorkflowExecutionTransitionResponse,
    summary="Get single transition targeting a specific state",
)
def get_workflow_execution_transitions_to_state(
    workflow_id: str,
    execution_id: str,
    state_id: str,
    user: User = Depends(authenticate),
):
    """Retrieve single transition that targeted a specific state.

    Returns the transition record where the given state was the target (to_state_id).
    Useful for tracking what led to a particular state's execution.

    Args:
        workflow_id: The workflow configuration ID
        execution_id: The specific execution instance ID
        state_id: The target state ID
        user: Authenticated user (injected)

    Returns:
        WorkflowExecutionTransitionResponse: Single transition record

    Raises:
        404: Workflow or transition not found
        403: User lacks READ permission on workflow
        500: Unexpected error during retrieval
    """
    _validate_workflow_access(workflow_id, user)
    _validate_execution_access(execution_id, user, expected_workflow_id=workflow_id)

    return WorkflowExecutionTransitionsIndexService.get_by_to_state(
        execution_id=execution_id,
        to_state_id=state_id,
    )


@router.get(
    "/workflows/{workflow_id}/executions/{execution_id}/states/{state_id}/output",
    status_code=status.HTTP_200_OK,
    response_model=WorkflowExecutionStateOutput,
    summary="Get state output of a workflow execution",
)
def get_workflow_execution_state_output(
    workflow_id: str, execution_id: str, state_id: str, user: User = Depends(authenticate)
):
    workflow_config = WorkflowService().get_workflow(workflow_id)

    if not Ability(user).can(Action.READ, workflow_config):
        raise_access_denied("view")

    return WorkflowExecutionStateOutput(output=WorkflowExecutionState.get_by_id(state_id).output)


@router.post(
    "/workflows/{workflow_id}/executions/{execution_id}/thoughts",
    status_code=status.HTTP_200_OK,
    response_model=List[WorkflowExecutionStateThoughtWithChildren],
    summary="Get thoughts of a workflow execution",
)
def get_workflow_execution_thoughts(
    workflow_id: str,
    execution_id: str,
    parent_ids: Annotated[Optional[List[str]], Body()] = None,
    user: User = Depends(authenticate),
):
    workflow_config = WorkflowService().get_workflow(workflow_id)

    if not Ability(user).can(Action.READ, workflow_config):
        raise_access_denied("view")

    execution = WorkflowService().find_workflow_execution_by_id(execution_id)

    return WorkflowThoughtsListService.run(execution, parent_ids)


@router.delete(
    "/workflows/{workflow_id}/executions",
    response_model=BaseResponse,
)
def delete_execution_by_user(workflow_id: str, user: User = Depends(authenticate)) -> BaseResponse:
    workflow_config = WorkflowService().get_workflow(workflow_id)

    if not Ability(user).can(Action.READ, workflow_config):
        raise_access_denied("view")

    WorkflowService().delete_all_executions_by_workflow_id(workflow_id)

    return BaseResponse(message="Execution history cleared")


@router.delete(
    "/workflows/{workflow_id}/executions/{execution_id}",
    response_model=BaseResponse,
)
def delete_conversation_by_id(
    execution_id: str,
    user: User = Depends(authenticate),
) -> BaseResponse:
    """
    Remove an execution by provided execution id
    """
    execution = WorkflowService().find_workflow_execution_by_id(execution_id)

    if not execution:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Execution you are trying to delete is Not Found",
            details="The specified execution was not found.",
            help="",
        )

    if not Ability(user).can(Action.WRITE, execution):
        raise_access_denied("view")

    if execution.conversation_id:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Cannot delete workflow execution",
            details="This workflow execution is part of a conversation and cannot be deleted. ",
            help="",
        )

    WorkflowService().delete_workflow_execution(execution.id)

    return BaseResponse(message="Execution removed")


def _process_state(state: WorkflowExecutionState, output_format: str) -> str:
    markdown_text = state.output or ""
    is_html_format = output_format == "html"
    try:
        json_content = json.loads(markdown_text)
        content = format_json_content(json_content) if is_html_format else json.dumps(json_content, indent=4)
    except json.JSONDecodeError:
        content = format_markdown_content(markdown_text) if is_html_format else markdown_text
    return content


def _extract_html_files(
    workflow_name: str, execution_id: str, states: List[WorkflowExecutionState], combined: bool
) -> Dict[str, str]:
    files = {}
    if combined:
        combined_filename = f"{workflow_name}_{execution_id}.html"
        html_report = generate_html_report(states)
        files[combined_filename] = html_report
    else:
        for state in states:
            state_completed_date = state.completed_at or state.started_at or state.date
            formatted_date = state_completed_date.strftime("%Y-%m-%d_%H-%M-%S.%f")[:-3]
            sanitized_state_name = state.name.replace('/', '_')
            filename = f"{formatted_date}_{sanitized_state_name}_{state.status.value}.html".lower()
            content = _process_state(state, output_format="html")
            files[filename] = content
    return files


def _extract_md_files(
    workflow_name: str, execution_id: str, states: List[WorkflowExecutionState], combined: bool
) -> Dict[str, str]:
    files = {}
    if combined:
        combined_md_filename = f"{workflow_name}_{execution_id}.md"
        combined_md_content = ""
        for state in states:
            sanitized_state_name = state.name.replace('/', '_')
            combined_md_content += f"## {sanitized_state_name}_{state.status.value}\n"
            combined_md_content += _process_state(state, output_format="md")
            combined_md_content += "\n\n"
        files[combined_md_filename] = combined_md_content
    else:
        for state in states:
            state_completed_date = state.completed_at or state.started_at or state.date
            formatted_date = state_completed_date.strftime("%Y-%m-%d_%H-%M-%S.%f")[:-3]
            sanitized_state_name = state.name.replace('/', '_')
            filename = f"{formatted_date}_{sanitized_state_name}_{state.status.value}.md".lower()
            content = _process_state(state, output_format="md")
            files[filename] = content
    return files


@router.get(
    "/workflows/{workflow_id}/executions/{execution_id}/export",
    status_code=status.HTTP_200_OK,
)
def export_workflow_execution(
    workflow_id: str,
    execution_id: str,
    output_format: str = "md",
    combined: Optional[bool] = False,
    user: User = Depends(authenticate),
):
    try:
        logger.info(f"Exporting WorkflowExecution. WorkflowId={workflow_id}. ExecutionId={execution_id}")

        workflow_config = WorkflowService().get_workflow(workflow_id)
        logger.debug(f"Found workflow. WorkflowName={workflow_config.name}")

        if not Ability(user).can(Action.READ, workflow_config):
            raise_access_denied("view")

        response = WorkflowExecutionStatesIndexService.run(
            execution_id=execution_id,
            per_page=10000,
            include_thoughts=False,
        )
        states = response["data"]
        logger.debug(f"Lookup for workflow states. Found {len(states)} states")

        zip_filename = f"{workflow_config.name}_{execution_id}.zip"

        extract_func = _extract_html_files if output_format == "html" else _extract_md_files

        files = extract_func(workflow_config.name, execution_id, states, combined)

        return StreamingResponse(
            content=generate_zip(files),
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={zip_filename}"},
        )

    except NotFoundError:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Execution Not Found",
            details="The specified execution was not found in the workflow.",
            help="Please ensure that the workflow ID and execution ID are correct and try again. "
            "If the issue persists, contact support.",
        )
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Workflow Export Error",
            details=f"An unexpected error occurred while exporting workflow execution: {str(e)}",
            help="We apologize for the inconvenience. "
            "If you continue to experience issues, please contact our support team "
            "with the timestamp of your request and any error messages you received.",
        ) from e


def _handle_streaming_execution(
    workflow: WorkflowExecutor, raw_request: Request, generator_queue: ThreadedGenerator
) -> StreamingResponse:
    """
    Handle synchronous streaming workflow execution.

    Args:
        workflow: WorkflowExecutor already initialized with the generator_queue
        raw_request: FastAPI request for disconnect handling
        generator_queue: ThreadedGenerator that was passed during workflow creation

    Returns:
        StreamingResponse with NDJSON content
    """
    # Handle client disconnect
    raw_request.state.on_disconnect(lambda: _handle_client_disconnect(generator_queue))

    # Serve streaming data
    wrapped_stream = _serve_workflow_stream(workflow, generator_queue)

    # Add CORS headers explicitly for streaming response (wildcard allowed without credentials)
    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Access-Control-Allow-Origin": "*",
    }

    return StreamingResponse(
        content=wrapped_stream,
        media_type=NDJSON_MEDIA_TYPE,
        headers=headers,
    )


def _handle_client_disconnect(threaded_generator: ThreadedGenerator):
    """Stop thread generator queue on client disconnect"""
    if not threaded_generator.is_closed():
        logger.debug("Workflow streaming client disconnected")
        threaded_generator.close()


def _serve_workflow_stream(workflow: WorkflowExecutor, generator_queue: ThreadedGenerator):
    """
    Execute workflow in thread and yield streaming data.

    The workflow is already initialized with the generator_queue, so no mutation is needed.

    Args:
        workflow: WorkflowExecutor with generator_queue already set
        generator_queue: ThreadedGenerator to read messages from
    """
    from codemie.chains.base import StreamedGenerationResult
    from time import time
    from types import SimpleNamespace

    execution_start = time()

    # Run workflow in background thread
    thread = threading.Thread(target=workflow.stream_to_client)
    thread.start()

    # Yield data from queue
    try:
        while True:
            value = generator_queue.queue.get()
            if value is not StopIteration:
                # Parse the streamed generation result
                generation_result = json.loads(value, object_hook=lambda d: SimpleNamespace(**d))

                yield f"{value}\n"
                generator_queue.queue.task_done()
            else:
                # Workflow execution finished - send final message with complete output
                # Get the final workflow output from the execution
                from codemie.service.workflow_service import WorkflowService

                execution = WorkflowService.find_workflow_execution_by_id(workflow.execution_id)

                # Send final message with the complete workflow output
                if execution:
                    final_message = StreamedGenerationResult(
                        generated=generation_result.thought.message,
                        time_elapsed=time() - execution_start,
                        generated_chunk="",
                        last=True,
                    )
                    yield f"{final_message.model_dump_json()}\n"

                break
    finally:
        thread.join(timeout=1)


def _validate_remote_entities_and_raise(entity: WorkflowConfigBase):
    deleted_entity_name = BedrockFlowService.validate_remote_entity_exists_and_cleanup(entity)

    if deleted_entity_name is not None:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Requested entity was not found on vendor, deleting from AI/Run.",
            details=f"We haven't found the entity '{deleted_entity_name}' on the vendor.",
            help="Make sure that the entity exists on the vendor side and reimport.",
        )


def _validate_workflow_supports_files_and_raise(workflow: WorkflowConfigBase, file_name: Optional[str]):
    """
    Validates whether a file has been passed and if a workflow supports file uploads.
    """
    if not file_name or not workflow.bedrock:
        return

    raise ExtendedHTTPException(
        code=status.HTTP_400_BAD_REQUEST,
        message="File uploads are not supported by the bedrock workflows",
        details="Please check the workflow's capabilities or use a different workflow.",
        help="If you believe this is an error, please contact support.",
    )
