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

import asyncio
from typing import Optional


from fastapi import APIRouter, status, Request, Depends, BackgroundTasks, Query
from starlette.responses import StreamingResponse

from codemie.configs import logger
from codemie.core.ability import Action
from codemie.core.errors import ErrorDetailLevel
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import (
    BaseModelResponse,
    AssistantChatRequest,
    AssistantEvaluationRequest,
    EvaluationResponse,
)
from codemie.service.assistant_evaluation_service import AssistantEvaluationService
from codemie.rest_api.models.assistant import (
    Assistant,
    VirtualAssistantChatRequest,
    ToolCallResumeRequest,
)
from codemie.rest_api.models.conversation import (
    ConversationMetrics,
    ConversationMetricsOut,
)
from codemie.rest_api.routers.assistant._ask import (
    _ask_assistant,
    _ask_virtual_assistant,
    _effective_hide_tool_outputs,
    _prepare_assistant_for_execution,
)
from codemie.rest_api.routers.assistant._shared import (
    _get_assistant_by_slug_or_raise,
)
from codemie.rest_api.routers.utils import check_user_can_access_assistant, get_assistant_by_id_or_raise
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.assistant.tool_output_visibility import apply_to_response
from codemie.service.assistant_service import AssistantService
from codemie.service.mcp.access_control import MCPAccessControlService
from codemie.service.llm_service.llm_service import llm_service


router = APIRouter()


@router.post(
    '/assistants/virtual/model',
    status_code=status.HTTP_200_OK,
    response_model=BaseModelResponse,
    response_model_by_alias=True,
)
async def ask_virtual_assistant(
    raw_request: Request,
    background_tasks: BackgroundTasks,
    request: VirtualAssistantChatRequest,
    user: User = Depends(authenticate),
    include_tool_errors: bool = Query(
        False,
        description='Include tool error details in response',
    ),
    error_detail_level: ErrorDetailLevel = Query(
        ErrorDetailLevel.STANDARD,
        description='Error verbosity level: minimal (code+message), standard (+http_status), full (+all details)',
    ),
) -> StreamingResponse | BaseModelResponse:
    """
    Run inference using an inline assistant definition without a database record.
    The assistant configuration is provided entirely in the request body.
    Conversation history is never persisted.
    """
    asyncio.create_task(raw_request.state.wait_for_disconnect())

    mcp_servers = MCPAccessControlService.sanitize_for_save(request.mcp_servers)

    assistant = Assistant.model_construct(
        id=None,
        name='virtual',
        description='',
        project=user.current_project,
        system_prompt=request.system_prompt,
        llm_model_type=request.llm_model_type or llm_service.default_llm_model,
        temperature=request.temperature,
        top_p=request.top_p,
        tools_tokens_size_limit=request.tools_tokens_size_limit,
        toolkits=request.toolkits,
        context=request.context,
        mcp_servers=mcp_servers,
        skill_ids=request.skill_ids,
        assistant_ids=request.assistant_ids,
        enabled_builtin_subagents=request.enabled_builtin_subagents,
        agent_mode=request.agent_mode,
        plan_prompt=request.plan_prompt,
        smart_tool_selection_enabled=request.smart_tool_selection_enabled,
        prompt_variables=request.prompt_variables,
        shared=False,
        is_global=False,
        is_react=True,
        creator=user.name,
    )

    tools_config = request.tools_config or AssistantService.prepare_tools_config_from_toolkits(assistant.toolkits)

    chat_request = AssistantChatRequest.model_construct(
        conversation_id=request.conversation_id,
        text=request.text,
        content_raw=request.content_raw,
        file_names=request.file_names,
        history=request.history,
        stream=request.stream,
        output_schema=request.output_schema,
        tools_config=tools_config,
        metadata=request.metadata,
        top_k=request.top_k,
        disable_cache=request.disable_cache,
        mcp_server_single_usage=request.mcp_server_single_usage,
        save_history=False,  # Enforced — virtual assistants never persist history
    )

    result = await asyncio.to_thread(
        _ask_virtual_assistant,
        assistant,
        raw_request,
        chat_request,
        user,
        background_tasks,
        include_tool_errors,
        error_detail_level,
    )
    return result


@router.post(
    "/assistants/{assistant_id}/model",
    status_code=status.HTTP_200_OK,
    response_model=BaseModelResponse,
    response_model_by_alias=True,
)
async def ask_assistant_by_id(
    raw_request: Request,
    assistant_id: str,
    background_tasks: BackgroundTasks,
    request: AssistantChatRequest,
    user: User = Depends(authenticate),
    include_tool_errors: bool = Query(
        False,
        description="Include tool error details in response",
    ),
    error_detail_level: ErrorDetailLevel = Query(
        ErrorDetailLevel.STANDARD,
        description="Error verbosity level: minimal (code+message), standard (+http_status), full (+all details)",
    ),
):
    """
    Ask questions for assistant by assistant id.

    Optionally specify a version number in the request body to use a specific
    historical configuration of the assistant.

    Error Handling Parameters:
    - include_tool_errors: When true, tool errors (401, 403, 5xx) are exposed separately
      from the agent's generated text, preventing the LLM from "absorbing" them.
    - error_detail_level: Controls error verbosity (minimal/standard/full)
    """
    asyncio.create_task(raw_request.state.wait_for_disconnect())

    # Get master assistant record
    assistant = get_assistant_by_id_or_raise(assistant_id)

    # Prepare assistant for execution (current config or specific version)
    execution_assistant = _prepare_assistant_for_execution(assistant, request)

    result = await asyncio.to_thread(
        _ask_assistant,
        execution_assistant,
        raw_request,
        request,
        user,
        background_tasks,
        include_tool_errors,
        error_detail_level,
        _effective_hide_tool_outputs(assistant, execution_assistant),
    )
    return result


def _resume_tool_call(
    conversation_id: str,
):
    """Validate that a pending checkpoint and pending tool call exist for this conversation."""
    from codemie.service.conversation_checkpoint_service import ConversationCheckpointService

    svc = ConversationCheckpointService()
    if svc.get_checkpoint(conversation_id) is None:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="No pending tool call found for this conversation.",
        )

    pending_tool_call = svc.get_pending_tool_call(conversation_id)
    if pending_tool_call is None:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="No pending tool call found for this conversation.",
        )


@router.post(
    "/assistants/{assistant_id}/model/tool-call/resume",
    status_code=status.HTTP_200_OK,
    response_model=BaseModelResponse,
    response_model_by_alias=True,
)
async def resume_tool_call(
    raw_request: Request,
    assistant_id: str,
    background_tasks: BackgroundTasks,
    request: ToolCallResumeRequest,
    user: User = Depends(authenticate),
):
    """
    Resume a paused assistant conversation after a tool call confirmation.

    - action="allow": executes the pending tool call and continues the response.
    - action="deny": skips the tool call and continues the response.
    """
    asyncio.create_task(raw_request.state.wait_for_disconnect())
    assistant = get_assistant_by_id_or_raise(assistant_id)
    check_user_can_access_assistant(user, assistant, "view", Action.READ)
    _resume_tool_call(
        conversation_id=request.conversation_id,
    )

    # Intentionally skip _prepare_assistant_for_execution — resume reuses existing session context.
    return await asyncio.to_thread(
        _ask_assistant_resume,
        assistant,
        raw_request,
        request,
        user,
        background_tasks,
    )


def _ask_assistant_resume(
    assistant,
    raw_request: Request,
    request: ToolCallResumeRequest,
    user: User,
    background_tasks: BackgroundTasks,
):
    """
    KNOWN GAP: ToolCallResumeRequest carries no `version`, and resume intentionally skips
    `_prepare_assistant_for_execution` (session context is reused as-is), so there is no pinned
    execution_assistant here to combine via `_effective_hide_tool_outputs` like the other ask
    paths. This hides using only the current master assistant's flag. If a turn was paused while
    running under a pinned version with hide_tool_outputs=True and the master's own flag is later
    turned off before resume, the resumed output would not be hidden. Fixing this requires
    persisting the effective hide decision (or pinned version) in the checkpoint when the tool
    call is first paused — out of scope here.
    """
    from codemie.rest_api.handlers.assistant_handlers import get_request_handler

    request_uuid = raw_request.state.uuid
    handler = get_request_handler(assistant, user, request_uuid)
    return apply_to_response(assistant, handler.handle_tool_call_resume(request, raw_request))


@router.post(
    "/assistants/slug/{assistant_slug:path}/model",
    status_code=status.HTTP_200_OK,
    response_model=BaseModelResponse,
    response_model_by_alias=True,
)
def ask_assistant_by_slug(
    raw_request: Request,
    assistant_slug: str,
    background_tasks: BackgroundTasks,
    request: AssistantChatRequest,
    user: User = Depends(authenticate),
    include_tool_errors: bool = Query(
        False,
        description="Include tool error details in response",
    ),
    error_detail_level: ErrorDetailLevel = Query(
        ErrorDetailLevel.STANDARD,
        description="Error verbosity level: minimal (code+message), standard (+http_status), full (+all details)",
    ),
    project: Optional[str] = Query(
        None,
        description="Project name to scope the slug lookup. Slugs are unique per-project; "
        "pass it to resolve human-readable {project}/{slug} URLs deterministically.",
    ),
):
    """
    Ask questions for assistant by assistant slug.

    Optionally specify a version number in the request body to use a specific
    historical configuration of the assistant.

    Error Handling Parameters:
    - include_tool_errors: When true, tool errors (401, 403, 5xx) are exposed separately
    - error_detail_level: Controls error verbosity (minimal/standard/full)
    """
    # Get master assistant record
    assistant = _get_assistant_by_slug_or_raise(assistant_slug, project)

    # Prepare assistant for execution (current config or specific version)
    execution_assistant = _prepare_assistant_for_execution(assistant, request)

    return _ask_assistant(
        execution_assistant,
        raw_request,
        request,
        user,
        background_tasks,
        include_tool_errors,
        error_detail_level,
        _effective_hide_tool_outputs(assistant, execution_assistant),
    )


@router.post(
    "/assistants/{assistant_id}/evaluate",
    status_code=status.HTTP_200_OK,
    response_model=EvaluationResponse,
)
def evaluate_assistant(
    assistant_id: str,
    request: AssistantEvaluationRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(authenticate),
    raw_request: Request = None,
):
    """
    Evaluate an assistant against a dataset of inputs from Langfuse.
    This endpoint allows you to test an assistant's performance on a dataset asynchronously.
    The evaluation will run in the background, and results will be available in Langfuse.
    This process doesn't involve streaming and other features of the standard assistant endpoint.
    """
    # Get the assistant
    assistant = get_assistant_by_id_or_raise(assistant_id)

    # Check if user can access the assistant
    check_user_can_access_assistant(user, assistant, "view", Action.READ)
    # Use the dedicated evaluation service to process the evaluation asynchronously
    evaluation_response = AssistantEvaluationService.evaluate_assistant(
        assistant=assistant,
        dataset_id=request.dataset_id,
        experiment_name=request.experiment_name,
        llm_model=request.llm_model,
        user=user,
        request_uuid=raw_request.state.uuid,
        background_tasks=background_tasks,
        raw_request=raw_request,
        system_prompt=request.system_prompt,
    )
    logger.info(f"Evaluation task started for experiment: {request.experiment_name}")

    return evaluation_response


def _calculate_workflow_metrics(conversation_id: str, conversation) -> ConversationMetricsOut:
    """
    Calculate metrics for workflow conversations by aggregating tokens_usage from all workflow executions.
    """
    from codemie.core.workflow_models import WorkflowExecution

    # Extract unique execution_ids from conversation history
    execution_ids = {
        message.execution_id
        for message in conversation.history
        if hasattr(message, 'execution_id') and message.execution_id
    }

    # Aggregate tokens_usage from all workflow executions
    total_input_tokens = 0
    total_output_tokens = 0
    total_money_spent = 0
    total_classifier_cost_usd = 0

    for execution_id in execution_ids:
        executions = WorkflowExecution.get_by_execution_id(execution_id)
        for execution in executions:
            if execution.tokens_usage:
                total_input_tokens += execution.tokens_usage.input_tokens or 0
                total_output_tokens += execution.tokens_usage.output_tokens or 0
                total_money_spent += execution.tokens_usage.money_spent or 0
                total_classifier_cost_usd += (
                    execution.tokens_usage.routing.classifier_cost_usd if execution.tokens_usage.routing else None
                ) or 0

    logger.debug(
        f"Calculated workflow metrics for conversation {conversation_id}: "
        f"executions_count={len(execution_ids)}, "
        f"input_tokens={total_input_tokens}, output_tokens={total_output_tokens}, "
        f"money_spent={total_money_spent}"
    )

    return ConversationMetricsOut(
        conversation_id=conversation_id,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_money_spent=total_money_spent,
        total_classifier_cost_usd=total_classifier_cost_usd,
    )


@router.get(
    "/assistants/metrics/{conversation_id}",
    response_model=ConversationMetricsOut,
    response_model_exclude_none=True,
)
def conversation_metrics(conversation_id: str) -> ConversationMetricsOut:
    """
    Get metrics of the conversation with Assistant or Workflow
    """
    from codemie.rest_api.models.conversation import Conversation

    # Check if this is a workflow conversation
    conversation = Conversation.find_by_id(conversation_id)
    if conversation and conversation.is_workflow_conversation:
        return _calculate_workflow_metrics(conversation_id, conversation)

    # For regular assistant conversations, use existing metrics from database
    metrics = ConversationMetrics.get_by_fields({"conversation_id": conversation_id})

    if not metrics:
        logger.info(f"Conversation metrics with given id {conversation_id} are not found")
        metrics = ConversationMetrics(
            conversation_id=conversation_id, total_money_spent=0, total_output_tokens=0, total_input_tokens=0
        )

    total_classifier_cost_usd = conversation.get_total_classifier_cost_usd() if conversation else 0

    return ConversationMetricsOut(
        conversation_id=conversation_id,
        total_input_tokens=metrics.total_input_tokens,
        total_output_tokens=metrics.total_output_tokens,
        total_money_spent=metrics.total_money_spent,
        total_classifier_cost_usd=total_classifier_cost_usd,
    )
