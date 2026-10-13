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
from time import time
from typing import Optional

from fastapi import status, Request, BackgroundTasks
from starlette.responses import StreamingResponse

from codemie.configs import logger
from codemie.core.ability import Action
from codemie.core.errors import ErrorDetailLevel
from codemie.core.exceptions import ExtendedHTTPException, MCPAuthenticationRequiredException
from codemie.core.models import BaseModelResponse, AssistantChatRequest
from codemie.rest_api.models.guardrail import GuardrailEntity, GuardrailSource
from codemie.rest_api.handlers.assistant_handlers import get_request_handler, ChatHistoryData
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.base import ConversationStatus
from codemie.service.llm.model_availability_service import ModelAvailabilityService
from codemie.rest_api.routers.assistant._shared import _validate_remote_entities_and_raise
from codemie.rest_api.routers.utils import check_user_can_access_assistant
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_user_interaction_service import assistant_user_interaction_service
from codemie.service.aws_bedrock.bedrock_orchestration_service import BedrockOrchestratorService
from codemie.service.security.token_providers.base_provider import BrokerAuthRequiredException
from codemie.service.guardrail.guardrail_service import GuardrailService
from codemie.service.request_summary_manager import request_summary_manager
from codemie.service.assistant.assistant_version_service import AssistantVersionService


def _apply_skill_ids_to_assistant(assistant: Assistant, request_skill_ids: list[str]) -> None:
    """
    Merges request skill_ids with the assistant's existing skill_ids (modifies assistant in-place).
    Deduplicates skill IDs while preserving order (existing skills first, then new ones).

    IMPORTANT: This function modifies the assistant object in-place. When merging runtime skill_ids
    that should not be persisted to the database, ensure you pass a copy of the assistant object.

    Args:
        assistant: The assistant instance to update (will be modified in-place)
        request_skill_ids: List of skill IDs from the request
    """
    # Get existing skill_ids from assistant (or empty list if None)
    existing_skill_ids = assistant.skill_ids or []

    # Create merged list: existing skills first, then new ones that aren't already included
    seen = set(existing_skill_ids)
    merged_skill_ids = list(existing_skill_ids)

    for skill_id in request_skill_ids:
        if skill_id not in seen:
            merged_skill_ids.append(skill_id)
            seen.add(skill_id)

    # Apply merged list to assistant
    assistant.skill_ids = merged_skill_ids
    logger.debug(
        f"Merged skill_ids for assistant '{assistant.id}': "
        f"existing={existing_skill_ids}, request={request_skill_ids}, merged={merged_skill_ids}"
    )


def _apply_version_or_default(assistant: Assistant, request: AssistantChatRequest) -> Assistant:
    """Apply a specific version's configuration, or ensure the current version is populated."""
    if request.version is not None:
        return AssistantVersionService.apply_version_to_assistant(assistant, request.version)

    # Set the current version number if not already set
    if not hasattr(assistant, 'version') or assistant.version is None:
        assistant.version = assistant.version_count if hasattr(assistant, 'version_count') else 1
    return assistant


def _resolve_chosen_execution_model(assistant: Assistant, request: AssistantChatRequest) -> str | None:
    """
    Resolve "Assistant Default" model according to project rules:
    - request.llm_model overrides everything when set
    - otherwise use assistant.llm_model_type
    - if assistant model is not allowed by project, fallback to project default
    """
    chosen_model = request.llm_model or assistant.llm_model_type
    if not chosen_model:
        return chosen_model

    picked, _ = ModelAvailabilityService.resolve_requested_model(
        assistant.project, chosen_model, is_global=assistant.is_global
    )
    return picked


def _prepare_assistant_for_execution(assistant: Assistant, request: AssistantChatRequest) -> Assistant:
    """
    Prepares an assistant for execution, either using current configuration or a specific version.
    Ensures the assistant version is always populated for Langfuse trace tagging.
    Merges request skill_ids with assistant's existing skill_ids if provided.

    Args:
        assistant: The master assistant record
        request: The chat request containing optional version, sub_assistants_versions, and skill_ids

    Returns:
        Assistant instance with configuration mapped from the specified version and merged skill_ids
    """
    assistant = _apply_version_or_default(assistant, request)

    # Merge request skill_ids with assistant's existing skill_ids if provided
    if request.skill_ids is not None:
        # Create a new assistant instance to avoid modifying the database object
        assistant = Assistant(**assistant.model_dump())
        _apply_skill_ids_to_assistant(assistant, request.skill_ids)

    chosen_model = _resolve_chosen_execution_model(assistant, request)
    if chosen_model:
        assistant.llm_model_type = chosen_model

    return assistant


def _create_assistant_error(message: str, details: str, help_text: str) -> ExtendedHTTPException:
    """
    Creates a standardized assistant error
    """
    return ExtendedHTTPException(
        code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        message=message,
        details=details,
        help=help_text,
    )


def _create_mcp_auth_error(payload: dict) -> ExtendedHTTPException:
    payload = payload or {}
    servers = payload.get('servers') or []
    server_names = (
        ', '.join(
            s.get('mcp_server_name') or s.get('mcp_config_name') or ''
            for s in servers
            if s.get('mcp_server_name') or s.get('mcp_config_name')
        )
        or payload.get('mcp_server_name')
        or payload.get('auth_config_id')
        or 'unknown'
    )
    error_contexts = ', '.join(str(s['error_context']) for s in servers if s.get('error_context')) or str(
        payload.get('error_context') or ''
    )
    details = f'MCP request failed due to 401 Unauthorized or expired/invalid authentication. Server(s): {server_names}'
    if error_contexts:
        details += f'. Context: {error_contexts}'
    return ExtendedHTTPException(
        code=status.HTTP_401_UNAUTHORIZED,
        message='MCP authentication required',
        details=details,
        help=(
            'Refresh or fix the MCP token/configuration, then re-run or '
            'wait for the next webhook/scheduler execution.'
        ),
    )


def _save_error(
    request_uuid: str,
    request: AssistantChatRequest,
    exception: ExtendedHTTPException,
    user: User,
    assistant: Assistant,
):
    """
    Saves error information to chat history
    """
    handler = get_request_handler(assistant, user, request_uuid)
    handler.save_chat_history(
        ChatHistoryData(
            execution_start=time(),
            request=request,
            response=f"{exception.message}: {exception.details}\n{exception.help}",
            thoughts=[],
            status=ConversationStatus.ERROR,
        )
    )


def _ask_virtual_assistant(
    assistant: Assistant,
    raw_request: Request,
    request: AssistantChatRequest,
    user: User,
    background_tasks: BackgroundTasks,
    include_tool_errors: bool = False,
    error_detail_level: ErrorDetailLevel = ErrorDetailLevel.STANDARD,
) -> StreamingResponse | BaseModelResponse:
    """
    Executes a virtual (transient) assistant request.
    Skips ownership checks (no DB record) and usage tracking (id is None).
    Forces save_history=False. Delegates to the shared request handler pipeline.
    """
    request_uuid = raw_request.state.uuid

    # No check_user_can_access_assistant — there is no DB record to check ownership against
    _validate_remote_entities_and_raise(assistant)
    _validate_assistant_supports_files_and_raise(assistant, request.file_names)
    _validate_file_attachment_allowed_and_raise(assistant, request.file_names)
    _validate_assistant_supports_model_change_and_raise(assistant, request.llm_model)

    # Guardrail check is skipped automatically: assistant.id is None, so the
    # `if assistant.id and request.text` guard in _ask_assistant never fires here.

    try:
        # No assistant_user_interaction_service.record_usage — assistant.id is None

        request_summary_manager.create_request_summary(
            request_id=request_uuid,
            project_name=assistant.project,
            user=user.as_user_model(),
        )

        handler = get_request_handler(assistant, user, request_uuid)
        return handler.process_request(
            request,
            background_tasks,
            raw_request,
            include_tool_errors=include_tool_errors,
            error_detail_level=error_detail_level,
        )

    except MCPAuthenticationRequiredException:
        raise
    except BrokerAuthRequiredException:
        raise
    except Exception as e:
        error = _create_assistant_error(
            'Assistant Error',
            f'{str(e)}',
            'We apologize for the inconvenience. Here are some steps you can try:\n'
            '1. Retry your request after a short delay.\n'
            '2. Check if your input is within the expected parameters.\n'
            'If you continue to experience issues, please contact our support team '
            'with the timestamp of your request and any error messages you received.',
        )
        _save_error(request_uuid, request, error, user, assistant)
        logger.error(f'{str(e)}', exc_info=True)
        raise error from e


def _try_save_error(request_uuid, request, error, user, assistant, context: str):
    try:
        _save_error(request_uuid, request, error, user, assistant)
    except Exception:
        logger.exception('Failed to persist %s error for request %s', context, request_uuid)


def _ask_assistant(
    assistant: Assistant,
    raw_request: Request,
    request: AssistantChatRequest,
    user: User,
    background_tasks: BackgroundTasks,
    include_tool_errors: bool = False,
    error_detail_level: ErrorDetailLevel = ErrorDetailLevel.STANDARD,
):
    """
    Internal helper for assistant execution.

    Error handling parameters:
    - include_tool_errors: Include tool error details in response
    - error_detail_level: Error verbosity (minimal/standard/full)

    `user` drives access control, usage recording, request-summary attribution,
    and `get_request_handler` (hence budget-key selection) uniformly — it is the
    single identity resolved by `authenticate()` (including any Teams sender swap).
    """
    request_uuid = raw_request.state.uuid
    check_user_can_access_assistant(user, assistant, "view", Action.READ)
    _validate_remote_entities_and_raise(assistant)
    _validate_assistant_supports_files_and_raise(assistant, request.file_names)
    _validate_file_attachment_allowed_and_raise(assistant, request.file_names)
    _validate_assistant_supports_model_change_and_raise(assistant, request.llm_model)

    if assistant.id and request.text:
        # We do not validate system prompts as the guardrails will just treat 99% of them as PROMPT_ATTACK
        guardrailed_text, blocked_reasons = GuardrailService.apply_guardrails_for_entity(
            GuardrailEntity.ASSISTANT,
            assistant.id,
            assistant.project,
            request.text,
            GuardrailSource.INPUT,
        )

        if blocked_reasons:
            unique_reasons = list({tuple(sorted(d.items())): d for d in blocked_reasons}.values())
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message="Request blocked by guardrails",
                details=f"The following reasons were identified: {json.dumps(unique_reasons, indent=2, default=str)}",
                help="Please review the blocked reasons and adjust your request accordingly.",
            )

        if isinstance(guardrailed_text, str):
            request.text = guardrailed_text

    try:
        assistant_user_interaction_service.record_usage(assistant=assistant, user=user)

        request_summary_manager.create_request_summary(
            request_id=request_uuid,
            project_name=assistant.project,
            user=user.as_user_model(),
        )

        handler = get_request_handler(assistant, user, request_uuid)
        # Pass error handling options to handler
        return handler.process_request(
            request,
            background_tasks,
            raw_request,
            include_tool_errors=include_tool_errors,
            error_detail_level=error_detail_level,
        )

    except MCPAuthenticationRequiredException as exc:
        _try_save_error(
            request_uuid, request, _create_mcp_auth_error(exc.payload), user, assistant, 'MCP authentication'
        )
        raise
    except BrokerAuthRequiredException:
        _try_save_error(
            request_uuid,
            request,
            _create_assistant_error(
                'Broker authentication required',
                'Authentication with the configured broker failed (401 Unauthorized).',
                'Refresh or fix the broker token/configuration, then re-run or wait for the next trigger.',
            ),
            user,
            assistant,
            'broker authentication',
        )
        raise
    except ExtendedHTTPException as ehe:
        _save_error(request_uuid, request, ehe, user, assistant)
        raise ehe
    except Exception as e:
        error_details = f"{type(e).__name__}: {str(e)}" if str(e) else type(e).__name__
        error = _create_assistant_error(
            "Assistant Error",
            error_details,
            "We apologize for the inconvenience. Here are some steps you can try:\n"
            "1. Retry your request after a short delay.\n"
            "2. Check if your input is within the expected parameters.\n"
            "If you continue to experience issues, please contact our support team "
            "with the timestamp of your request and any error messages you received.",
        )
        _save_error(request_uuid, request, error, user, assistant)
        logger.error(error_details, exc_info=True)
        raise error from e


def _validate_file_attachment_allowed_and_raise(
    assistant: Assistant,
    file_names: Optional[list[str]],
) -> None:
    """Raise HTTP 403 if file attachment is disabled for the assistant."""
    if not file_names:
        return
    if assistant.file_attachment_enabled is False:
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="File attachment not allowed",
            details="File uploads are disabled for this assistant.",
            help="Contact your administrator to enable file attachment for this assistant.",
        )


def _validate_assistant_supports_files_and_raise(assistant: Assistant, file_names: Optional[list[str]]):
    """
    Validates whether files have been passed and if an assistant supports file uploads.
    """
    if not file_names or not BedrockOrchestratorService.is_bedrock_assistant(assistant):
        return

    raise ExtendedHTTPException(
        code=status.HTTP_400_BAD_REQUEST,
        message="File uploads are not supported by the bedrock assistants",
        details="Please check the assistant's capabilities or use a different assistant.",
        help="If you believe this is an error, please contact support.",
    )


def _validate_assistant_supports_model_change_and_raise(assistant: Assistant, llm_model: Optional[str]):
    """
    Validates whether a non-default llm model has been passed and if an assistant supports model changes.
    """
    if not llm_model or not BedrockOrchestratorService.is_bedrock_assistant(assistant):
        return

    raise ExtendedHTTPException(
        code=status.HTTP_400_BAD_REQUEST,
        message="Bedrock assistants do not support changing models",
        details="Please check the assistant's capabilities or use a different assistant.",
        help="If you believe this is an error, please contact support.",
    )
