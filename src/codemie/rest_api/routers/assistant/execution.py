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
from codemie.core.ability import Ability, Action
from codemie.core.errors import ErrorDetailLevel
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
    SettledMoney,
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
from codemie.rest_api.routers.utils import (
    check_user_can_access_assistant,
    get_assistant_by_id_or_raise,
    raise_access_denied,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.assistant_service import AssistantService
from codemie.service.conversation_service import ConversationService
from codemie.service.assistant.tool_output_visibility import strip_response
from codemie.service.mcp.access_control import MCPAccessControlService
from codemie.service.llm_service.llm_service import llm_service
from codemie.service.spend.origin_context import spend_origin
from codemie.service.spend.traffic_class import TrafficOrigin


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
    # Authorisation, the consume and the pinned version are the handler's (rules 4, 5).
    return await asyncio.to_thread(
        _ask_assistant_resume,
        assistant,
        raw_request,
        request,
        user,
        background_tasks,
    )


def _ask_assistant_resume(
    assistant: Assistant,
    raw_request: Request,
    request: ToolCallResumeRequest,
    user: User,
    background_tasks: BackgroundTasks,
):
    """
    Resume a paused turn as an ordinary streamed turn (rule 5). The handler authorises with the master, consumes the
    pause and runs it on the pinned version it restores from the pause; the route strips the stream when the master
    or that version hides tool outputs (fail-closed, as the ordinary route does).
    """
    from codemie.rest_api.handlers.assistant_handlers import get_request_handler

    # A resumed run builds its agent afresh on this thread, so the origin has to be published here
    # exactly as on the first turn; the agent captures it and republishes it on the pool thread.
    with spend_origin(TrafficOrigin.CONVERSATION):
        handler = get_request_handler(assistant, user, raw_request.state.uuid)
        handler.background_tasks = background_tasks
        response, version = handler.resume_tool_call(request, raw_request)
    return strip_response(response) if _effective_hide_tool_outputs(assistant, version) else response


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


def _execution_usage(tokens_usage, money: SettledMoney) -> tuple[int, int, float]:
    """One execution's (input tokens, output tokens, classifier cost); its money is added to `money`."""
    if not tokens_usage:
        # No usage record at all: nothing settled here either, so the total is a subtotal.
        # `unavailable` rather than `pending`, because nothing here says a figure is coming
        # and promising one a later read cannot deliver is the worse of the two mistakes.
        money.mark_unavailable()
        return 0, 0, 0
    if tokens_usage.money_spent is None:
        money.mark_unavailable()
    else:
        # A settled subtotal that is explicitly not final still leaves the sum short.
        money.add(
            tokens_usage.money_spent,
            pending=bool(tokens_usage.money_spent_pending),
            unavailable=bool(tokens_usage.money_spent_unavailable),
        )
    classifier_cost_usd = (tokens_usage.routing.classifier_cost_usd if tokens_usage.routing else None) or 0
    return tokens_usage.input_tokens or 0, tokens_usage.output_tokens or 0, classifier_cost_usd


async def _calculate_workflow_metrics(conversation_id: str, conversation) -> ConversationMetricsOut:
    """
    Calculate metrics for workflow conversations by aggregating tokens_usage from all workflow executions.

    **A pending subtotal is settled from the ledger on the way through** (AD-28 rule 7, by the
    execution's own id - `WorkflowService.hydrate_execution_cost`), and the row keeps only what the
    ledger settled; what it could not answer for is flagged on this response alone.

    **The money total is the settled part, or nothing at all.** `tokens_usage.money_spent` is a
    truthful settled subtotal and not necessarily the whole cost of its execution: the two flags beside
    it say whether a figure is still coming (`money_spent_pending`) or is never coming
    (`money_spent_unavailable`). Collapsing a missing subtotal to `0` used to publish "this execution
    was free", and a conversation with nothing settled used to report `$0.00` as though measured, which
    AD-23 forbids: an absent figure is never a zero. `None` is returned instead, exactly as the
    assistant-conversation twin of this function - `Conversation.get_total_money_spent` - now does, and
    the response model omits the field rather than showing a number nobody measured.

    **A flagged `0.0` is one of those absences, and `money_spent is not None` cannot see it.** It is
    what `RequestSummary.calculate` writes when no run of the execution has settled yet, which on the
    LiteLLM streaming path is the normal case, and `workflow_execution_service` persists it verbatim -
    so gating the total on the figure's presence alone let that zero into the sum and published
    `$0.0000 (awaiting final cost)` where the assistant twin in the same state shows no figure at all.
    Only the flag beside the number tells a measured free execution from an unmeasured one, so the
    total is gated on the same `measured_anything` the twin computes rather than on `is None`.

    A qualified total is published as qualified, on `money_spent_pending` / `money_spent_unavailable`
    of the response - not only logged as it was while the response model had nowhere to put them.
    This is the settled sum of *persisted per-execution
    subtotals*, deliberately, and not a spend-port read: the port answers one `DimensionScope` per
    read, the conversation dimension is not emitted on workflow metric documents at all (only
    `execution_id` and `workflow_id` are), and the execution dimension would need one read per
    execution and then an addition of the figures - which is precisely the arithmetic AD-29 forbids.
    """
    from codemie.core.workflow_models import WorkflowExecution
    from codemie.service.workflow_service import WorkflowService

    # Extract unique execution_ids from conversation history
    execution_ids = {
        message.execution_id
        for message in conversation.history
        if hasattr(message, 'execution_id') and message.execution_id
    }

    # Aggregate tokens_usage from all workflow executions
    total_input_tokens = 0
    total_output_tokens = 0
    money = SettledMoney()
    total_classifier_cost_usd = 0

    for execution_id in execution_ids:
        for execution in WorkflowExecution.get_by_execution_id(execution_id):
            input_tokens, output_tokens, classifier_cost_usd = _execution_usage(
                await WorkflowService.hydrate_execution_cost(execution), money
            )
            total_input_tokens += input_tokens
            total_output_tokens += output_tokens
            total_classifier_cost_usd += classifier_cost_usd

    # No settled subtotal means there is no total - not a zero. A genuine 0.0 (a failed call that
    # cost nothing) is a measured figure and is kept; a flagged 0.0 is not a figure at all and the
    # presence of a number cannot tell the two apart - only the flag beside it can.
    money_spent = money.total()

    logger.debug(
        f"Calculated workflow metrics for conversation {conversation_id}: "
        f"executions_count={len(execution_ids)}, "
        f"input_tokens={total_input_tokens}, output_tokens={total_output_tokens}, "
        f"money_spent={money_spent.total}, "
        f"money_spent_is_subtotal={money_spent.pending or money_spent.unavailable}"
    )

    return ConversationMetricsOut(
        conversation_id=conversation_id,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_money_spent=money_spent.total,
        total_classifier_cost_usd=total_classifier_cost_usd,
        # The signal this function has always computed correctly, now reaching the response instead
        # of only the debug log.
        money_spent_pending=money_spent.pending,
        money_spent_unavailable=money_spent.unavailable,
    )


@router.get(
    "/assistants/metrics/{conversation_id}",
    response_model=ConversationMetricsOut,
    response_model_exclude_none=True,
)
async def conversation_metrics(conversation_id: str, user: User = Depends(authenticate)) -> ConversationMetricsOut:
    """
    Get metrics of the conversation with Assistant or Workflow

    A total still pending when it was written is settled from the ledger here (AD-28 rule 7):
    this is the read behind the chat's "Usage details", so it is where "awaiting final cost"
    turns into the figure once the ledger row has landed.

    Gated like the conversation itself: the totals are that conversation's tokens and money, and
    the read can write the settled figures back, so a caller who may not view the conversation
    gets neither.
    """
    from codemie.rest_api.models.conversation import Conversation

    conversation = Conversation.find_by_id(conversation_id)
    if conversation is not None and not Ability(user).can(Action.READ, conversation):
        raise_access_denied("view")

    # Check if this is a workflow conversation
    if conversation and conversation.is_workflow_conversation:
        return await _calculate_workflow_metrics(conversation_id, conversation)

    # For regular assistant conversations, use existing metrics from database. Only for a conversation that
    # exists: deleting one leaves its metrics row behind, and without the conversation there is nothing to
    # check the caller against - so its totals are not served at all.
    metrics = ConversationMetrics.get_by_fields({"conversation_id": conversation_id}) if conversation else None

    if not metrics:
        logger.info(f"Conversation metrics with given id {conversation_id} are not found")
        # `total_money_spent=None`, not `0`: no metrics row means nobody has priced this conversation,
        # and a zero there is a claim that it was free. The column is already `Optional[float]` because
        # a conversation with nothing settled has no figure either, so absence is representable here.
        metrics = ConversationMetrics(
            conversation_id=conversation_id, total_money_spent=None, total_output_tokens=0, total_input_tokens=0
        )
    elif conversation is not None:
        metrics = await ConversationService.hydrate_conversation_metrics(conversation, metrics)

    total_classifier_cost_usd = conversation.get_total_classifier_cost_usd() if conversation else 0

    return ConversationMetricsOut(
        conversation_id=conversation_id,
        total_input_tokens=metrics.total_input_tokens,
        total_output_tokens=metrics.total_output_tokens,
        total_money_spent=metrics.total_money_spent,
        total_classifier_cost_usd=total_classifier_cost_usd,
        money_spent_pending=metrics.money_spent_pending,
        money_spent_unavailable=metrics.money_spent_unavailable,
    )
