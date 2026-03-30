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
import asyncio
from time import time
from typing import Annotated, Literal, Optional, List

from pydantic import BaseModel, Field
from sqlmodel import Session, select, and_

from codemie_tools.base.models import ToolKit
from fastapi import APIRouter, status, Request, Depends, BackgroundTasks, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from codemie.configs import logger, config
from codemie.configs.logger import set_logging_info
from codemie.core.ability import Ability, Action
from codemie.core.errors import ErrorDetailLevel
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import (
    BaseResponse,
    BaseModelResponse,
    AssistantChatRequest,
    AssistantEvaluationRequest,
)
from codemie.rest_api.models.guardrail import GuardrailEntity, GuardrailSource
from codemie.service.assistant_evaluation_service import AssistantEvaluationService, EvaluationResponse
from codemie.rest_api.handlers.assistant_handlers import get_request_handler, ChatHistoryData
from codemie.rest_api.models.assistant import (
    Assistant,
    AssistantRequest,
    AssistantListResponse,
    AssistantCreateResponse,
    AssistantUpdateResponse,
    Context,
    CreatedByUser,
    MissingContextException,
    MCPServerCheckRequest,
    MCPServerDetails,
    PublishValidationResponse,
    InlineCredential,
    ToolKitDetails,
    SubAssistantPublishSettings,
    PublishValidationErrorResponse,
    AssistantHealthCheckRequest,
    AssistantHealthCheckResponse,
    AssistantOrigin,
)
from codemie.rest_api.models.assistant_generator import (
    AssistantGeneratorRequest,
    AssistantGeneratorResponse,
    PromptGeneratorResponse,
    PromptGeneratorRequest,
    RefineRequest,
    RefineGeneratorResponse,
    RefineRequestBody,
)
from codemie.service.assistant_generator_service import AssistantGeneratorService, AssistantToolkit, RefinePromptDetails
from codemie.rest_api.models.base import ConversationStatus
from codemie.rest_api.models.conversation import (
    ConversationMetrics,
)
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.models.prebuilt_assistants import PrebuiltAssistant
from codemie.rest_api.routers.utils import raise_access_denied
from codemie.rest_api.security.authentication import project_access_check
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.rest_api.utils.default_applications import ensure_application_exists
from codemie.service.assistant.assistant_repository import AssistantScope, AssistantRepository
from codemie.service.assistant.assistant_user_interaction_service import assistant_user_interaction_service
from codemie.service.assistant.category_service import category_service
from codemie.service.assistant_service import AssistantService
from codemie.service.aws_bedrock.bedrock_agent_service import BedrockAgentService
from codemie.service.aws_bedrock.bedrock_agentcore_runtime_service import BedrockAgentCoreRuntimeService
from codemie.service.aws_bedrock.bedrock_orchestration_service import BedrockOrchestratorService
from codemie.service.mcp.mcp_tester import MCPServerTester
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.security.token_providers.base_provider import BrokerAuthRequiredException
from codemie.service.monitoring.metrics_constants import MetricsAttributes, MCP_SERVERS_ASSISTANT_METRIC
from codemie.service.guardrail.guardrail_service import GuardrailService
from codemie.service.request_summary_manager import request_summary_manager
from codemie.service.assistant.assistant_version_service import AssistantVersionService
from codemie.service.tools import ToolsInfoService
from codemie.service.mcp_config_service import MCPConfigService
from codemie.service.assistant.assistant_health_check_service import AssistantHealthCheckService
from codemie.service.tools.plugin_tools_info_service import PluginToolsInfoService, PluginToolsInfoServiceError


router = APIRouter(
    tags=["Assistant"],
    prefix="/v1",
    dependencies=[],
)


class ReactionRequest(BaseModel):
    """Request model for assistant reactions"""

    reaction: Literal["like", "dislike"]


class CategoriesRequest(BaseModel):
    """Request model for assistant categories"""

    categories: Optional[list[str]] = None


class PublishToMarketplaceRequest(BaseModel):
    """Request model for publishing assistant to marketplace"""

    categories: Optional[list[str]] = None
    sub_assistants_settings: Optional[List[SubAssistantPublishSettings]] = Field(
        default_factory=list,
        description="Settings for each sub-assistant including marketplace visibility (is_global), "
        "toolkits, mcp_servers, and categories",
    )
    ignore_recommendations: bool = Field(
        default=False,
        description="If true, bypasses all validation recommendations and publishes anyway. "
        "This should only be set when user explicitly clicks 'Publish Anyway' button.",
    )


class SystemPromptValidationRequest(BaseModel):
    """Request model for system prompt validation"""

    system_prompt_template: str
    prompt_vars: dict[str, str]
    assistant_id: Optional[str] = None


class SystemPromptValidationResponse(BaseModel):
    """Response model for system prompt validation"""

    rendered_prompt: str
    is_valid: bool = True
    message: str = "System prompt rendered successfully"


@router.get(
    "/assistants",
    status_code=status.HTTP_200_OK,
    response_model=list[AssistantListResponse],
    response_model_by_alias=True,
)
def index_assistants(
    user: User = Depends(authenticate),
    scope: Annotated[AssistantScope, Query()] = AssistantScope.VISIBLE_TO_USER,
    minimal_response: bool = False,
    filters: str = None,
    page: int = 0,
    per_page: int = 12,
):
    """
    Returns all saved assistants
    """
    try:
        parsed_filters = json.loads(filters) if filters else None
    except json.JSONDecodeError:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid filters",
            details="Filters must be a valid encoded JSON object.",
            help="Please check the filters and ensure they are in the correct format. ",
        )

    repository = AssistantRepository()
    result = repository.query(
        user=user,
        scope=scope,
        filters=parsed_filters,
        page=page,
        per_page=per_page,
        minimal_response=minimal_response,
    )

    # Usage stats are added in the repository

    return JSONResponse(content=jsonable_encoder(result), status_code=status.HTTP_200_OK)


@router.get(
    "/assistants/users",
    status_code=status.HTTP_200_OK,
)
def get_assistant_users(
    user: User = Depends(authenticate),
    scope: AssistantScope = AssistantScope.VISIBLE_TO_USER,
):
    """
    Returns list of users who created assistants within the specified scope
    """
    repository = AssistantRepository()
    result = repository.get_users(user=user, scope=scope)

    return result


@router.get(
    "/assistants/id/{assistant_id}",
    status_code=status.HTTP_200_OK,
    response_model=Assistant,
    response_model_by_alias=True,
)
def get_assistant_by_id(request: Request, assistant_id: str, user: User = Depends(authenticate)):
    """
    Returns saved assistant by id
    """
    assistant = _get_assistant_by_id_or_raise(assistant_id)
    assistant.user_abilities = Ability(user).list(assistant)

    _check_user_can_access_assistant(user, assistant, "view", Action.READ)
    _validate_remote_entities_and_raise(assistant)

    # Match the settings_config flag with the correct value from ToolsInfoService
    tools_info = ToolsInfoService.get_tools_info(user=user)
    if assistant.toolkits:
        _enrich_toolkit_settings_config(assistant.toolkits, tools_info)

    assistant.nested_assistants = Assistant.get_by_ids(user, assistant.assistant_ids, parent_assistant=assistant)

    # Enrich toolkits in nested assistants so that user mapping settings can be displayed
    for nested_assistant in assistant.nested_assistants:
        if nested_assistant.toolkits:
            _enrich_toolkit_settings_config(nested_assistant.toolkits, tools_info)

    # Enrich skills with full details (id, name, description)
    skills_data = []
    if assistant.skill_ids:
        from codemie.service.skill_service import SkillService

        skills = SkillService.get_skills_by_ids(assistant.skill_ids, user)
        skills_data = [skill.to_basic_info().model_dump() for skill in skills]

    _get_categories_data(assistant)

    # Mask sensitive prompt variable default values
    _mask_sensitive_prompt_variables(assistant)

    # Enrich system_prompt_history from version configurations
    repository = AssistantRepository()
    repository.enrich_system_prompt_history(assistant)

    # Enrich with guardrail assignments
    assistant.guardrail_assignments = GuardrailService.get_entity_guardrail_assignments(
        user,
        GuardrailEntity.ASSISTANT,
        assistant_id,
    )

    # Convert to dict and add skills
    response_data = jsonable_encoder(assistant)
    response_data["skills"] = skills_data

    return JSONResponse(content=response_data, status_code=status.HTTP_200_OK)


@router.get(
    "/assistants/slug/{assistant_slug:path}",
    status_code=status.HTTP_200_OK,
    response_model=Assistant,
    response_model_by_alias=True,
)
def get_assistant_by_slug(request: Request, assistant_slug: str, user: User = Depends(authenticate)):
    """
    Returns saved assistant by slug
    """
    assistant = _get_assistant_by_slug_or_raise(assistant_slug)
    _check_user_can_access_assistant(user, assistant, "view", Action.READ)
    _validate_remote_entities_and_raise(assistant)

    # Match the settings_config flag with the correct value from ToolsInfoService
    tools_info = ToolsInfoService.get_tools_info(user=user)
    if assistant.toolkits:
        _enrich_toolkit_settings_config(assistant.toolkits, tools_info)

    assistant.nested_assistants = Assistant.get_by_ids(user, assistant.assistant_ids, parent_assistant=assistant)

    # Enrich toolkits in nested assistants so that user mapping settings can be displayed
    for nested_assistant in assistant.nested_assistants:
        if nested_assistant.toolkits:
            _enrich_toolkit_settings_config(nested_assistant.toolkits, tools_info)

    # Enrich skills with full details (id, name, description)
    skills_data = []
    if assistant.skill_ids:
        from codemie.service.skill_service import SkillService

        skills = SkillService.get_skills_by_ids(assistant.skill_ids, user)
        skills_data = [skill.to_basic_info().model_dump() for skill in skills]

    _get_categories_data(assistant)

    # Mask sensitive prompt variable default values
    _mask_sensitive_prompt_variables(assistant)

    # Enrich system_prompt_history from version configurations
    repository = AssistantRepository()
    repository.enrich_system_prompt_history(assistant)

    # Enrich with guardrail assignments
    assistant.guardrail_assignments = GuardrailService.get_entity_guardrail_assignments(
        user,
        GuardrailEntity.ASSISTANT,
        str(assistant.id),
    )

    # Convert to dict and add skills
    response_data = jsonable_encoder(assistant)
    response_data["skills"] = skills_data

    return JSONResponse(content=response_data, status_code=status.HTTP_200_OK)


@router.get(
    "/assistants/prebuilt",
    status_code=status.HTTP_200_OK,
    response_model=list[PrebuiltAssistant],
    response_model_by_alias=True,
)
def get_prebuilt_assistants(user: User = Depends(authenticate)):
    """
    Returns prebuilt assistants
    """
    return PrebuiltAssistant.prebuilt_assistants(user)


@router.get(
    "/assistants/prebuilt/{slug}",
    status_code=status.HTTP_200_OK,
    response_model=PrebuiltAssistant,
    response_model_by_alias=True,
)
def get_prebuilt_assistant_by_slug(slug: str, user: User = Depends(authenticate)):
    """
    Returns prebuilt assistants
    """
    try:
        assistants = PrebuiltAssistant.prebuilt_assistants(user)
        assistant = next(item for item in assistants if item.slug == slug)

        return assistant
    except StopIteration:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Prebuilt assistant not found",
            details=f"No prebuilt assistant found with the slug '{slug}'.",
            help="Please check the assistant slug and ensure it is correct. ",
        )


@router.post(
    "/assistants/{assistant_id}/health",
    status_code=status.HTTP_200_OK,
    response_model=AssistantHealthCheckResponse,
)
def health_check_assistant(
    assistant_id: str,
    raw_request: Request,
    request: AssistantHealthCheckRequest,
    user: User = Depends(authenticate),
):
    """
    Health check endpoint for assistants.

    Similar to the /model endpoint, this validates the assistant exists and is accessible
    before performing the health check.

    Performs a comprehensive health check on an assistant to verify it's properly configured
    and fully functional. The health check validates:

    1. **Configuration**: Checks that the assistant exists and has valid configuration (LLM model, tools, context)
    2. **Functionality**: Actually calls the assistant with a test message (non-streaming) to verify it works end-to-end

    This endpoint is useful for:
    - Validating assistant configuration after creation or updates
    - Troubleshooting assistant issues
    - Monitoring assistant availability
    - Pre-deployment verification
    - Ensuring LLM connectivity and tool functionality

    **Important**: This health check actually calls the LLM with a test message to validate
    the assistant is fully functional. This ensures not just configuration validity but also
    that the assistant can successfully generate responses.

    **Path Parameters:**
    - assistant_id: ID of the assistant to check (required)

    **Request Body (optional):**
    - version: Optional specific version to test

    **Note**: The health check always uses the test message "show tools" and the assistant's
    configured LLM model to validate functionality.

    **Response:**
    - is_healthy: Overall health status (true if all checks pass)
    - configuration_valid: Whether configuration is valid
    - execution_successful: Whether the assistant successfully executed and generated a response
    - tools_available: Number of tools configured for the assistant
    - error: Error details if health check failed

    **Example Usage:**
    ```bash
    POST /v1/assistants/123e4567-e89b-12d3-a456-426614174000/health
    ```
    ```json
    {}
    ```

    Or to test a specific version:
    ```json
    {
        "version": 2
    }
    ```

    **Note**: This endpoint executes a real LLM call, so it will consume tokens and may take
    a few seconds to complete depending on the LLM response time.
    """
    # Check if assistant exists and user has access (similar to /model endpoint)
    assistant = _get_assistant_by_id_or_raise(assistant_id)
    _check_user_can_access_assistant(user, assistant, "view", Action.READ)

    try:
        return AssistantHealthCheckService.health_check_assistant(
            assistant=assistant, request=request, user=user, raw_request=raw_request
        )
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Cannot perform health check on specified assistant",
            details=f"An error occurred while trying to check the assistant: {str(e)}",
            help="Please check the assistant configuration or contact an administrator for assistance.",
        ) from e


@router.get(
    "/assistants/tools",
    status_code=status.HTTP_200_OK,
    response_model=list[ToolKit],
    response_model_by_alias=True,
)
def get_tools(request: Request, user: User = Depends(authenticate)):
    """
    Returns all available tools
    """
    tools_info = ToolsInfoService.get_tools_info(show_for_ui=True, user=user)
    return tools_info


@router.get(
    "/assistants/plugin_tools",
    status_code=status.HTTP_200_OK,
    response_model=list[ToolKit],
    response_model_by_alias=True,
)
def get_plugin_tools(request: Request, user: User = Depends(authenticate), plugin_setting_id: Optional[str] = None):
    """
    Returns plugin tools from plugin integration.

    Args:
        plugin_setting_id: Optional settings ID containing plugin integration credentials.
                          If not provided, attempts to use default plugin setting for user.

    Returns:
        List containing the plugin toolkit with actual plugin tools.
        Returns empty toolkit (with empty tools array) if no plugin tools available.

    Example:
        GET /v1/assistants/plugin_tools
        GET /v1/assistants/plugin_tools?plugin_setting_id=abc-123
    """
    try:
        plugin_toolkit_info = PluginToolsInfoService.get_plugin_toolkit_info(
            plugin_setting_id=plugin_setting_id, user=user, project_name=None
        )

        return [plugin_toolkit_info]
    except PluginToolsInfoServiceError as e:
        logger.error(f"Failed getting plugin tools: {e.message}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=e.message,
            details=e.details,
        ) from e
    except Exception as e:
        logger.error(f"Unexpected error getting plugin tools: {str(e)}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to retrieve plugin tools",
            details=f"An unexpected error occurred: {str(e)}",
            help="Please try again or contact support if the issue persists.",
        ) from e


@router.post(
    "/assistants/mcp_tools",
    status_code=status.HTTP_200_OK,
    response_model=list[ToolKit],
    response_model_by_alias=True,
)
def get_mcp_tools(
    request: MCPServerDetails,
    user: User = Depends(authenticate),
):
    """
    Returns MCP tools from MCP server configuration.

    Accepts an MCP server configuration and returns the list of available tools
    from that MCP server. This allows users to preview tools before adding
    an MCP server to an assistant.

    Args:
        request: MCP server configuration including command, args, and environment variables

    Returns:
        List containing the MCP toolkit with actual tools from the server.
        Returns empty toolkit (with empty tools array) if no tools are available.

    Example:
        POST /v1/assistants/mcp_tools
        {
            "name": "GitHub MCP",
            "description": "GitHub API tools",
            "config": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": "ghp_xxx"}
            }
        }
    """
    from codemie.service.tools.mcp_tools_info_service import MCPToolsInfoService, MCPToolsInfoServiceError

    try:
        mcp_server = request.model_copy(update={"enabled": True})

        toolkit_info = MCPToolsInfoService.get_mcp_toolkit_info(
            mcp_server_config=mcp_server, user=user, project_name=None
        )

        return [toolkit_info]

    except MCPToolsInfoServiceError as e:
        logger.error(f"Failed getting MCP tools: {e.message}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=e.message,
            details=e.details,
            help=(
                "Please check the MCP server configuration (command, args, environment variables) "
                "and ensure the MCP server is accessible."
            ),
        ) from e
    except Exception as e:
        logger.error(f"Unexpected error getting MCP tools: {str(e)}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to retrieve MCP tools",
            details=f"An unexpected error occurred: {str(e)}",
            help="Please try again or contact support if the issue persists.",
        ) from e


@router.get(
    "/assistants/context",
    status_code=status.HTTP_200_OK,
    response_model=list[dict],
)
def get_assistants_context(project_name: str, user: User = Depends(authenticate)):
    """
    Returns all available assistants context (index info - repo or kb)
    """
    result = IndexInfo.get_all_for_user(user=user, project_name=project_name)

    response = [
        {
            "name": item.repo_name,
            "context_type": Context.index_info_type(item),
            "id": item.id,
        }
        for item in result
    ]

    return response


@router.post(
    "/assistants",
    status_code=status.HTTP_200_OK,
    response_model=AssistantCreateResponse,
    response_model_by_alias=True,
)
def create_assistant(request: AssistantRequest, user: User = Depends(authenticate)):
    """
    Save user-specific assistant to DB with project field and create initial version.
    Validates toolkit credentials unless skip_integration_validation is True.
    """
    from codemie.service.assistant.assistant_version_service import AssistantVersionService

    project_access_check(user, request.project)

    # Validate integrations if not skipped
    if not request.skip_integration_validation:
        logger.info(f"Validating integrations for assistant: {request.name}")

        validation_result = _validate_assistant_integrations(request, user)

        if validation_result.has_missing_integrations:
            logger.warning(f"Validation failed: {validation_result.message}")
            return AssistantCreateResponse(
                message="Assistant validation found missing integrations. "
                "Please configure them or set skip_integration_validation=True.",
                assistant_id=None,
                validation=validation_result,
            )

    assistant = Assistant(**request.model_dump(exclude={"guardrail_assignments", "skip_integration_validation"}))
    assistant.created_by = CreatedByUser(
        id=user.id,
        username=user.username,
        name=user.name,
    )
    assistant.project = request.project
    assistant.version_count = 1  # Initialize version counter

    # Filter out datasources that don't exist in the target project
    # This handles cases like cloning an assistant to a different project
    if assistant.context:
        _filter_invalid_datasources(assistant)

    # Encrypt sensitive prompt variable default values before saving
    _encrypt_sensitive_prompt_variables(assistant)

    # Ensure slug is unique before saving
    if assistant.slug:
        assistant.slug = AssistantService.ensure_unique_slug(assistant.slug)

    if request.agent_card and request.agent_card.bedrock_agentcore:
        assistant.origin = AssistantOrigin.BEDROCK_AGENT_CORE

    # Ensure Application exists for the project
    if request.project:
        ensure_application_exists(request.project)

    try:
        assistant.save(refresh=True)

        # Create initial version
        AssistantVersionService.create_initial_version(assistant=assistant, request=request, user=user)

        # Create guardrail assignments in the assignment table
        GuardrailService.sync_guardrail_assignments_for_entity(
            user=user,
            entity_type=GuardrailEntity.ASSISTANT,
            entity_id=str(assistant.id),
            entity_project_name=assistant.project,
            guardrail_assignments=request.guardrail_assignments,
        )

        _track_mcp_usage_on_create(assistant.mcp_servers)
        _track_assistant_management_metric("create_assistant", assistant, user, True)
    except Exception as e:
        _track_assistant_management_metric(
            "create_assistant", assistant, user, False, {"error_class": e.__class__.__name__}
        )
        _raise_assistant_operation_error("save", e)

    return AssistantCreateResponse(message="Specified assistant saved", assistant_id=str(assistant.id))


@router.put(
    "/assistants/{assistant_id}",
    status_code=status.HTTP_200_OK,
    response_model=AssistantUpdateResponse,
    response_model_by_alias=True,
)
def update_assistant(
    assistant_id: str, request: AssistantRequest, background_tasks: BackgroundTasks, user: User = Depends(authenticate)
):
    """
    Update user-specific assistant.
    Validates toolkit credentials unless skip_integration_validation is True.
    """
    project_access_check(user, request.project)
    assistant = _get_assistant_by_id_or_raise(assistant_id)
    _check_user_can_access_assistant(user, assistant, "update", Action.WRITE)
    _validate_remote_entities_and_raise(assistant)

    # Validate integrations if not skipped
    if not request.skip_integration_validation:
        logger.info(f"Validating integrations for assistant update: {assistant_id}")

        validation_result = _validate_assistant_integrations(request, user)

        if validation_result.has_missing_integrations:
            logger.warning(f"Validation failed for assistant {assistant_id}: {validation_result.message}")
            return AssistantUpdateResponse(
                message="Assistant validation found missing integrations. "
                "Please configure them or set skip_integration_validation=True.",
                validation=validation_result,
            )

    repository = AssistantRepository()

    # Preserve existing encrypted values for sensitive variables sent back as masked
    if request.prompt_variables:
        _restore_existing_encrypted_values(request, assistant)

    # Encrypt sensitive prompt variable default values in the request before updating
    if request.prompt_variables:
        _encrypt_prompt_variables_in_request(request)

    try:
        # Check if assistant is published to marketplace before update
        was_global = assistant.is_global

        # Extracting the guardrail_assignments before the update
        guardrail_assignments = request.guardrail_assignments

        repository.update(assistant, request, user)

        # Filter out datasources that don't exist in the target project
        # This handles cases like editing an assistant and changing its project
        if assistant.context:
            _filter_invalid_datasources(assistant)
            assistant.update()  # Save the filtered context

        # If assistant is published to marketplace, reindex it in background to keep data consistent
        if was_global or assistant.is_global:
            # Schedule background task for reindexing
            background_tasks.add_task(
                _index_marketplace_assistant,
                assistant_id=assistant_id,
                user=user,
                is_update=was_global,
            )
            logger.info(
                f"Scheduled background reindexing for marketplace assistant {assistant_id}",
                extra={"assistant_id": assistant_id, "is_global": assistant.is_global, "was_update": was_global},
            )

        # Update guardrail assignments in the assignment table
        GuardrailService.sync_guardrail_assignments_for_entity(
            user=user,
            entity_type=GuardrailEntity.ASSISTANT,
            entity_id=str(assistant.id),
            entity_project_name=assistant.project,
            guardrail_assignments=guardrail_assignments,
        )

        _track_assistant_management_metric("update_assistant", assistant, user, True)
    except Exception as e:
        _track_assistant_management_metric(
            "update_assistant", assistant, user, False, {"error_class": e.__class__.__name__}
        )
        _raise_assistant_operation_error("update", e)

    return AssistantUpdateResponse(message="Specified assistant updated")


@router.delete(
    "/assistants/{assistant_id}",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
    response_model_by_alias=True,
)
def delete_assistant(assistant_id: str, user: User = Depends(authenticate)):
    """
    Update user-specific assistant
    """
    assistant = _get_assistant_by_id_or_raise(assistant_id)
    _check_user_can_access_assistant(user, assistant, "delete", Action.DELETE)

    # Track MCP usage before deletion
    _track_mcp_usage_on_delete(assistant.mcp_servers)

    Assistant.delete_assistant(assistant_id)
    _track_assistant_management_metric("delete_assistant", assistant, user, True)
    return BaseResponse(message="Specified assistant removed")


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
    assistant = _get_assistant_by_id_or_raise(assistant_id)

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
    )
    return result


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
    assistant = _get_assistant_by_slug_or_raise(assistant_slug)

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
    assistant = _get_assistant_by_id_or_raise(assistant_id)

    # Check if user can access the assistant
    _check_user_can_access_assistant(user, assistant, "view", Action.READ)
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


def _validate_assistant_integrations(request: AssistantRequest, user: User):
    """
    Validate that all required integrations are configured for the assistant.

    Args:
        request: Assistant request with toolkits to validate
        user: User making the request

    Returns:
        IntegrationValidationResult with any missing integrations
    """
    from codemie.service.assistant.assistant_integration_validator import AssistantIntegrationValidator

    return AssistantIntegrationValidator.validate_integrations(request, user, request.project)


def _calculate_workflow_metrics(conversation_id: str, conversation) -> ConversationMetrics:
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

    for execution_id in execution_ids:
        executions = WorkflowExecution.get_by_execution_id(execution_id)
        for execution in executions:
            if execution.tokens_usage:
                total_input_tokens += execution.tokens_usage.input_tokens or 0
                total_output_tokens += execution.tokens_usage.output_tokens or 0
                total_money_spent += execution.tokens_usage.money_spent or 0

    logger.debug(
        f"Calculated workflow metrics for conversation {conversation_id}: "
        f"executions_count={len(execution_ids)}, "
        f"input_tokens={total_input_tokens}, output_tokens={total_output_tokens}, "
        f"money_spent={total_money_spent}"
    )

    return ConversationMetrics(
        conversation_id=conversation_id,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        total_money_spent=total_money_spent,
    )


@router.get(
    "/assistants/metrics/{conversation_id}",
    response_model=ConversationMetrics,
    response_model_exclude_none=True,
)
def conversation_metrics(conversation_id: str) -> ConversationMetrics:
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
        return ConversationMetrics(
            conversation_id=conversation_id, total_money_spent=0, total_output_tokens=0, total_input_tokens=0
        )

    return metrics


@router.post(
    "/assistants/{assistant_id}/reactions",
    status_code=status.HTTP_200_OK,
)
def react_to_assistant(assistant_id: str, request: ReactionRequest, user: User = Depends(authenticate)):
    """
    React to an assistant with like or dislike.
    If the user already has the opposite reaction, it will be removed.
    """
    # Check if assistant exists
    _get_assistant_by_id_or_raise(assistant_id)

    # Use service to handle the reaction
    return assistant_user_interaction_service.manage_reaction(assistant_id, user.id, request.reaction)


@router.delete(
    "/assistants/{assistant_id}/reactions",
    status_code=status.HTTP_200_OK,
)
def remove_assistant_reactions(assistant_id: str, user: User = Depends(authenticate)):
    """
    Remove all reactions (likes/dislikes) from an assistant for the current user.
    """
    # Check if assistant exists
    _get_assistant_by_id_or_raise(assistant_id)

    return assistant_user_interaction_service.remove_reactions(assistant_id, user.id)


@router.post(
    "/assistants/{assistant_id}/marketplace/publish/validate",
    status_code=status.HTTP_200_OK,
    response_model=PublishValidationResponse,
)
def validate_assistant_for_marketplace(assistant_id: str, user: User = Depends(authenticate)):
    """
    Validate an assistant before publishing to the marketplace.
    Checks for inline integration credentials in the main assistant and all sub-assistants.
    Returns validation results and information about sub-assistants that will be published.
    """
    # Get the assistant
    assistant = _get_assistant_by_id_or_raise(assistant_id)
    _check_user_can_access_assistant(user, assistant, "write", Action.WRITE)
    _validate_remote_entities_and_raise(assistant)

    # Check for inline integration credentials in main assistant
    validation_result = _validate_assistant_inline_integrations(assistant)
    all_inline_credentials = validation_result["inline_credentials"]
    prompt_variables = validation_result.get("prompt_variables", [])

    # Get sub-assistants information and check their inline credentials
    sub_assistants_info = []
    if assistant.assistant_ids:
        for sub_id in assistant.assistant_ids:
            sub_assistant = _get_assistant_by_id_or_raise(sub_id)

            # Validate sub-assistant inline integrations
            sub_validation_result = _validate_assistant_inline_integrations(sub_assistant)

            # Add sub-assistant name to each credential for better UI display
            for credential in sub_validation_result["inline_credentials"]:
                # Create a new credential with sub-assistant context
                credential_dict = credential.model_dump() if hasattr(credential, 'model_dump') else credential
                credential_dict["sub_assistant_name"] = sub_assistant.name
                credential_dict["sub_assistant_id"] = sub_assistant.id
                all_inline_credentials.append(InlineCredential(**credential_dict))

            sub_assistants_info.append(
                {
                    "id": sub_assistant.id,
                    "name": sub_assistant.name,
                    "description": sub_assistant.description,
                    "is_global": sub_assistant.is_global,
                    "icon_url": sub_assistant.icon_url,
                    "categories": sub_assistant.categories or [],
                }
            )

    # Determine if validation is valid (no inline credentials found)
    is_valid = len(all_inline_credentials) == 0

    if not is_valid:
        response = PublishValidationResponse(
            requires_confirmation=True,
            message="This assistant or its sub-assistants contain inline integration credentials that will be "
            "shared with all users who use this assistant. Please confirm that you want to publish it.",
            inline_credentials=all_inline_credentials,
            assistant_id=assistant_id,
            sub_assistants=sub_assistants_info,
            prompt_variables=prompt_variables,
        )
    else:
        message = f"Assistant {assistant_id} is ready to be published to marketplace"
        if sub_assistants_info:
            message += f" along with {len(sub_assistants_info)} sub-assistant(s)"
        response = PublishValidationResponse(
            requires_confirmation=False,
            message=message,
            inline_credentials=[],
            assistant_id=assistant_id,
            sub_assistants=sub_assistants_info,
            prompt_variables=prompt_variables,
        )

    return response


@router.post(
    "/assistants/{assistant_id}/marketplace/publish",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
)
def publish_assistant_to_marketplace(
    assistant_id: str,
    background_tasks: BackgroundTasks,
    raw_request: Request,
    request: Optional[PublishToMarketplaceRequest] = None,
    user: User = Depends(authenticate),
):
    """
    Publish an assistant to the marketplace, including any sub-assistants.

    When an assistant has sub-assistants, this endpoint will:
    1. Process all sub-assistants with their individual settings (toolkits, mcp_servers, categories, is_global)
    2. Publish sub-assistants to marketplace or keep them private based on is_global flag:
       - is_global=True: Sub-assistant visible in marketplace and usable by orchestrator
       - is_global=False: Sub-assistant kept private (not in marketplace) but still usable by orchestrator
    3. Finally publish the orchestrator assistant to the marketplace

    Only administrators can publish assistants to the marketplace.
    """

    # Get the assistant
    assistant = _get_assistant_by_id_or_raise(assistant_id)
    _validate_publish_marketplace(assistant, user)

    # Set LiteLLM context with user's credentials
    from codemie.service.llm_service.utils import set_llm_context

    set_llm_context(None, user.current_project, user)

    # Track if user bypassed recommendations (for ES analytics)
    published_with_bypass = False
    if config.MARKETPLACE_LLM_VALIDATION_ON_PUBLISH_ENABLED:
        # Validation failed - check if user wants to bypass
        is_bypassing = request and request.ignore_recommendations
        published_with_bypass = _llm_validate_on_marketplace_publish(raw_request, assistant, user, is_bypassing)

    published_sub_count = 0
    total_sub_count = 0

    # Handle sub-assistants publishing
    if assistant.assistant_ids:
        total_sub_count = len(assistant.assistant_ids)
        published_sub_count = _publish_sub_assistants(
            assistant=assistant, sub_assistants_settings=request.sub_assistants_settings if request else [], user=user
        )

    # Update the main assistant
    assistant.is_global = True
    if request and request.categories:
        assistant.categories = request.categories

    try:
        assistant.update(refresh=True)

        # Schedule background task for indexing into marketplace
        background_tasks.add_task(
            _index_marketplace_assistant,
            assistant_id=assistant_id,
            user=user,
        )
        logger.info(
            f"Scheduled background indexing for published assistant {assistant_id}",
            extra={"assistant_id": assistant_id},
        )

        # Track metrics with bypass flag as attribute
        _track_assistant_management_metric(
            "publish_to_marketplace",
            assistant,
            user,
            True,
            {"published_with_ignored_recommendations": published_with_bypass},
        )
    except Exception as e:
        _track_assistant_management_metric(
            "publish_to_marketplace", assistant, user, False, {"error_class": e.__class__.__name__}
        )
        _raise_assistant_operation_error("publish to marketplace", e)

    # Build success message
    message = f"Assistant {assistant_id} published to marketplace successfully"
    if total_sub_count > 0:
        private_sub_count = total_sub_count - published_sub_count
        if published_sub_count > 0 and private_sub_count > 0:
            message += (
                f" along with {published_sub_count} sub-assistant(s) published to marketplace "
                f"and {private_sub_count} kept private"
            )
        elif published_sub_count > 0:
            message += f" along with {published_sub_count} sub-assistant(s) published to marketplace"
        else:
            message += f" with {total_sub_count} sub-assistant(s) kept private"

    return BaseResponse(message=message)


def _validate_sub_assistant(sub_id: str, assistant: Assistant, user: User) -> tuple[str, Assistant]:
    """
    Validate a single sub-assistant for publishing.

    Args:
        sub_id: ID of the sub-assistant to validate
        assistant: The parent assistant
        user: The user performing the operation

    Returns:
        Tuple of (sub_id, sub_assistant)

    Raises:
        ExtendedHTTPException: If validation fails
    """
    try:
        sub_assistant = _get_assistant_by_id_or_raise(sub_id)
        _check_user_can_access_assistant(user, sub_assistant, "write", Action.WRITE)
        return (sub_id, sub_assistant)

    except Exception as e:
        logger.error(
            f"Failed to validate sub-assistant {sub_id} for publishing: {str(e)}",
            extra={"parent_assistant_id": assistant.id, "sub_assistant_id": sub_id},
            exc_info=True,
        )
        _track_assistant_management_metric(
            "publish_sub_assistant_to_marketplace",
            sub_assistant if 'sub_assistant' in locals() else None,
            user,
            False,
            {"error_class": e.__class__.__name__, "parent_assistant_id": assistant.id, "phase": "validation"},
        )
        sub_name = sub_assistant.name if 'sub_assistant' in locals() else sub_id
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=f"Failed to validate sub-assistant '{sub_name}' ({sub_id})",
            details=f"An error occurred while validating sub-assistant: {str(e)}",
            help="Please check that all sub-assistants exist and are accessible. "
            "Ensure you have write permissions for all sub-assistants.",
        ) from e


def _apply_sub_assistant_settings(sub_assistant: Assistant, settings: Optional[SubAssistantPublishSettings]) -> bool:
    """
    Apply settings to a sub-assistant.

    Args:
        sub_assistant: The sub-assistant to update
        settings: Settings to apply (can be None)

    Returns:
        The is_global value to use (True by default)
    """
    is_global = True  # Default: make sub-assistant visible in marketplace

    if settings is None:
        return is_global

    # Override is_global if explicitly provided in settings
    is_global = settings.is_global

    # Update toolkits if provided
    if settings.toolkits is not None:
        sub_assistant.toolkits = settings.toolkits

    # Update MCP servers if provided
    if settings.mcp_servers is not None:
        sub_assistant.mcp_servers = settings.mcp_servers

    # Update categories if provided
    if settings.categories:
        sub_assistant.categories = settings.categories

    return is_global


def _publish_single_sub_assistant(
    sub_id: str,
    sub_assistant: Assistant,
    settings_map: dict,
    assistant: Assistant,
    user: User,
) -> bool:
    """
    Publish a single sub-assistant with settings.

    Args:
        sub_id: ID of the sub-assistant
        sub_assistant: The sub-assistant to publish
        settings_map: Map of assistant IDs to settings
        assistant: The parent assistant
        user: The user performing the operation

    Returns:
        True if published to marketplace (is_global=True), False otherwise

    Raises:
        ExtendedHTTPException: If publishing fails
    """
    try:
        settings = settings_map.get(sub_id)
        is_global = _apply_sub_assistant_settings(sub_assistant, settings)

        # Set marketplace visibility
        sub_assistant.is_global = is_global
        sub_assistant.update(refresh=True)

        _track_assistant_management_metric(
            "publish_sub_assistant_to_marketplace",
            sub_assistant,
            user,
            True,
            {"parent_assistant_id": assistant.id, "is_global": is_global},
        )

        visibility_status = "published to marketplace" if is_global else "kept private (not visible in marketplace)"
        logger.info(
            f"Sub-assistant '{sub_assistant.name}' ({sub_id}) {visibility_status}",
            extra={"parent_assistant_id": assistant.id, "sub_assistant_id": sub_id, "is_global": is_global},
        )

        return is_global

    except Exception as e:
        logger.error(
            f"Failed to publish sub-assistant '{sub_assistant.name}' ({sub_id}) to marketplace: {str(e)}",
            extra={"parent_assistant_id": assistant.id, "sub_assistant_id": sub_id},
            exc_info=True,
        )
        _track_assistant_management_metric(
            "publish_sub_assistant_to_marketplace",
            sub_assistant,
            user,
            False,
            {"error_class": e.__class__.__name__, "parent_assistant_id": assistant.id, "phase": "publishing"},
        )
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message=f"Failed to publish sub-assistant '{sub_assistant.name}' ({sub_id})",
            details=f"An error occurred while publishing sub-assistant: {str(e)}",
            help="Please check that all sub-assistants are valid and accessible. "
            "Ensure you have write permissions for all sub-assistants.",
        ) from e


def _publish_sub_assistants(
    assistant: Assistant, sub_assistants_settings: List[SubAssistantPublishSettings], user: User
) -> int:
    """
    Publish all sub-assistants with their specific settings and marketplace visibility.

    This function handles both public and private sub-assistants:
    - Public sub-assistants (is_global=True): Visible in marketplace and usable by orchestrator
    - Private sub-assistants (is_global=False): Not visible in marketplace but still usable by orchestrator

    The function uses a two-phase approach:
    1. Pre-validate all sub-assistants (fetch and check access)
    2. Publish all sub-assistants (only if all validations pass)

    This prevents partial publish states where some sub-assistants are published
    but others fail validation.

    Args:
        assistant: The parent assistant containing sub-assistants
        sub_assistants_settings: List of settings to apply to each sub-assistant (including is_global flag)
        user: The user performing the operation

    Returns:
        Number of sub-assistants successfully published to marketplace (is_global=True)
    """
    settings_map = {s.assistant_id: s for s in sub_assistants_settings}

    # Phase 1: Pre-validate all sub-assistants before publishing any
    sub_assistants_to_publish = [_validate_sub_assistant(sub_id, assistant, user) for sub_id in assistant.assistant_ids]

    # Phase 2: All validations passed, now publish all sub-assistants
    published_count = 0
    for sub_id, sub_assistant in sub_assistants_to_publish:
        is_global = _publish_single_sub_assistant(sub_id, sub_assistant, settings_map, assistant, user)
        if is_global:
            published_count += 1

    return published_count


def _validate_publish_marketplace(assistant, user):
    """
    Validate that an assistant can be published to the marketplace.

    This function checks:
    - User has write access to the assistant
    - Remote entities (e.g., Bedrock agents) exist if applicable
    """
    _check_user_can_access_assistant(user, assistant, "write", Action.WRITE)
    _validate_remote_entities_and_raise(assistant)


@router.post(
    "/assistants/{assistant_id}/marketplace/unpublish",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
    response_model_by_alias=True,
)
def unpublish_assistant_from_marketplace(
    assistant_id: str, background_tasks: BackgroundTasks, user: User = Depends(authenticate)
):
    """
    Unpublish an assistant from the marketplace by setting its is_global field to False.
    Only administrators can unpublish assistants from the marketplace.
    """
    # Get the assistant
    assistant = _get_assistant_by_id_or_raise(assistant_id)
    _check_user_can_access_assistant(user, assistant, "write", Action.WRITE)

    # Update the is_global field
    assistant.is_global = False
    try:
        assistant.update(refresh=True)

        # Schedule background task for removing from marketplace
        background_tasks.add_task(
            _remove_marketplace_assistant,
            assistant_id=assistant_id,
            assistant_name=assistant.name,
            user=user,
        )
        logger.info(
            f"Scheduled background removal for unpublished assistant {assistant_id}",
            extra={"assistant_id": assistant_id},
        )

        _track_assistant_management_metric("unpublish_from_marketplace", assistant, user, True)
    except Exception as e:
        _track_assistant_management_metric(
            "unpublish_from_marketplace", assistant, user, False, {"error_class": e.__class__.__name__}
        )
        _raise_assistant_operation_error("unpublish from marketplace", e)

    return BaseResponse(message=f"Assistant {assistant_id} unpublished from marketplace successfully")


@router.post(
    "/assistants/generate",
    status_code=status.HTTP_200_OK,
    response_model=AssistantGeneratorResponse,
)
def generate_assistant(raw_request: Request, request: AssistantGeneratorRequest, user: User = Depends(authenticate)):
    """
    Generate assistant details from user input text.
    Returns assistant name, description, conversation starters, and comprehensive system prompt.
    """
    try:
        request_id = raw_request.state.uuid
        set_logging_info(uuid=request_id, user_id=user.id, user_email=user.username)

        # Set LiteLLM context with user's credentials
        from codemie.service.llm_service.utils import set_llm_context

        set_llm_context(None, user.current_project, user)

        result = AssistantGeneratorService.generate_assistant_details(
            text=request.text,
            user=user,
            llm_model=request.llm_model,
            include_tools=request.include_tools,
            request_id=request_id,
        )
        return result
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to generate assistant details",
            details=f"An error occurred while generating assistant details: {str(e)}",
            help="Try refining your input text or using a different model.",
        )


@router.post(
    "/assistants/prompt/generate",
    status_code=status.HTTP_200_OK,
    response_model=PromptGeneratorResponse,
)
def generate_assistant_prompt(
    raw_request: Request, request: PromptGeneratorRequest, user: User = Depends(authenticate)
):
    """
    Generate assistant details from user input text.
    Returns assistant name, description, conversation starters, and comprehensive system prompt.
    Automatically includes available datasources from the user's project.
    """
    try:
        request_id = raw_request.state.uuid
        set_logging_info(uuid=request_id, user_id=user.id, user_email=user.username)

        # Set LiteLLM context with user's credentials
        from codemie.service.llm_service.utils import set_llm_context

        set_llm_context(None, user.current_project, user)

        result = AssistantGeneratorService.generate_assistant_prompt(
            user=user,
            text=request.text,
            existing_prompt=request.system_prompt,
            project=user.current_project,
            llm_model=request.llm_model,
            request_id=request_id,
        )
        return result
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to generate assistant details",
            details=f"An error occurred while generating assistant details: {str(e)}",
            help="Try refining your input text or using a different model.",
        )


@router.post(
    "/assistants/refine",
    status_code=status.HTTP_200_OK,
    response_model=RefineGeneratorResponse,
)
def refine_with_ai(
    raw_request: Request,
    request: RefineRequest = RefineRequestBody,
    user: User = Depends(authenticate),
):
    """
    Accepts assistant draft fields and returns LLM recommendations and datasource assessments.
    """
    try:
        request_id = raw_request.state.uuid
        set_logging_info(uuid=request_id, user_id=user.id, user_email=user.username)

        # Set LiteLLM context with user's credentials
        from codemie.service.llm_service.utils import set_llm_context

        set_llm_context(None, request.project or user.current_project, user)

        refine_details = RefinePromptDetails(
            name=request.name,
            description=request.description,
            categories=request.categories,
            system_prompt=request.system_prompt,
            conversation_starters=request.conversation_starters,
            toolkits=[AssistantToolkit.model_validate(t) for t in request.toolkits] if request.toolkits else None,
            context=request.context,
        )

        result = AssistantGeneratorService.generate_refine_prompt(
            refine_details=refine_details,
            user=user,
            request_id=request_id,
            refine_prompt=request.refine_prompt,
            project=request.project,
            _include_tools=request.include_tools,
            include_context=request.include_context,
            include_categories=request.include_categories,
            llm_model=request.llm_model,
        )

        return result
    except ExtendedHTTPException:
        raise
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to refine assistant with AI",
            details=f"An error occurred while refining assistant: {str(e)}",
            help="Try again later or check the assistant draft fields.",
        )


@router.post(
    "/assistants/mcp/test",
    status_code=status.HTTP_200_OK,
)
def check_mcp_server(request: MCPServerCheckRequest, user: User = Depends(authenticate)):
    """
    Test if MCP server config is valid
    """
    try:
        success, message = MCPServerTester(request, user).test()

        return JSONResponse(status_code=status.HTTP_200_OK, content={"success": success, "message": message})
    except BrokerAuthRequiredException:
        raise
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Cannot test specified mcp server",
            details=f"An error occurred while trying to test the mcp server: {str(e)}",
        ) from e


@router.post(
    "/assistants/system-prompt/validate",
    status_code=status.HTTP_200_OK,
    response_model=SystemPromptValidationResponse,
)
def validate_system_prompt(request: SystemPromptValidationRequest, user: User = Depends(authenticate)):
    """
    Validate and render a system prompt template with provided variables.

    This endpoint allows you to test system prompt templates before using them,
    ensuring that variable names are valid and the template renders correctly.
    It uses sandboxed Jinja2 rendering to prevent SSTI attacks.

    Variable names must be alphanumeric with underscores only (e.g., 'project_name', 'user_123').
    Spaces and special characters are not allowed in variable names.
    """
    try:
        rendered_prompt = AssistantService.render_system_prompt_with_vars(
            system_prompt_template=request.system_prompt_template,
            prompt_vars=request.prompt_vars,
            assistant_id=request.assistant_id,
            user_id=user.id,
        )

        return SystemPromptValidationResponse(
            rendered_prompt=rendered_prompt,
            is_valid=True,
            message="System prompt rendered successfully",
        )

    except Exception as e:
        # Import at function level to avoid circular imports
        from codemie.core.template_security import TemplateSecurityError

        if isinstance(e, TemplateSecurityError):
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="System prompt validation failed",
                details=f"Security violation detected: {str(e)}",
                help="Please review your template for invalid variable names or security issues. "
                "Variable names must be alphanumeric with underscores only (e.g., 'project_name', 'user_123'). "
                "Spaces and special characters are not allowed in variable names.",
            )
        else:
            raise ExtendedHTTPException(
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                message="Failed to validate system prompt",
                details=f"An error occurred while validating the system prompt: {str(e)}",
                help="Please check your template syntax and try again.",
            )


def _get_assistant_by_id_or_raise(assistant_id: str) -> Assistant:
    """
    Retrieves an assistant by ID or raises a standardized exception if not found
    """
    assistant = Assistant.find_by_id(assistant_id)
    if not assistant:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Assistant not found",
            details=f"No assistant found with the id '{assistant_id}'.",
            help="Please check the assistant id and ensure it is correct. ",
        )
    return assistant


def _get_assistant_by_slug_or_raise(assistant_slug: str) -> Assistant:
    """
    Retrieves an assistant by slug or raises a standardized exception if not found
    """
    assistant = Assistant.get_by_fields({"slug.keyword": assistant_slug})
    if not assistant:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Assistant not found",
            details=f"No assistant found with the slug '{assistant_slug}'.",
            help="Please check the assistant slug and ensure it is correct. ",
        )
    return assistant


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
    # Apply specific version configuration if requested
    if request.version is not None:
        assistant = AssistantVersionService.apply_version_to_assistant(assistant, request.version)
    else:
        # Set the current version number if not already set
        if not hasattr(assistant, 'version') or assistant.version is None:
            assistant.version = assistant.version_count if hasattr(assistant, 'version_count') else 1

    # Merge request skill_ids with assistant's existing skill_ids if provided
    if request.skill_ids is not None:
        # Create a new assistant instance to avoid modifying the database object
        assistant = Assistant(**assistant.model_dump())
        _apply_skill_ids_to_assistant(assistant, request.skill_ids)

    return assistant


def _check_user_can_access_assistant(user: User, assistant: Assistant, action_name: str, action_type: Action):
    """
    Checks if a user has permission to perform an action on an assistant
    """
    if not Ability(user).can(action_type, assistant):
        raise_access_denied(action_name)


def _validate_remote_entities_and_raise(assistant: Assistant):
    # Validate Bedrock Agents
    deleted_assistant_names = BedrockAgentService.validate_remote_entity_exists_and_cleanup_with_subassistants(
        assistant
    )

    # Validate Bedrock AgentCore Runtime endpoints
    if not deleted_assistant_names:  # Only check if not already deleted
        deleted_assistant_names = (
            BedrockAgentCoreRuntimeService.validate_remote_entity_exists_and_cleanup_with_subassistants(assistant)
        )

    if deleted_assistant_names:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Requested assistant or subassistants were not found on vendor, deleting from AI/Run.",
            details=f"We haven't found the assistant/s '{', '.join(deleted_assistant_names)}' on the vendor.",
            help="Make sure that the assistant exists on the vendor side and reimport.",
        )


def _track_assistant_management_metric(
    metric_name: str, assistant: Assistant, user: User, success: bool, additional_attributes: dict = None
):
    """
    Tracks metrics for assistant management operations.
    Safely extracts attributes from assistant object to avoid DetachedInstanceError in thread pool contexts.
    """
    # Extract attributes before passing to service to avoid DetachedInstanceError
    # This is necessary because FastAPI runs sync endpoints in thread pools via anyio,
    # and SQLAlchemy objects may be detached after the session closes.
    try:
        assistant_name = assistant.name
        assistant_description = assistant.description
        assistant_project = assistant.project
        assistant_slug = assistant.slug if assistant.slug is not None else assistant.id
        assistant_llm_model = assistant.llm_model_type
        assistant_ids_count = len(assistant.assistant_ids) if assistant.assistant_ids else 0
        mcp_servers = assistant.mcp_servers.copy() if assistant.mcp_servers else []
    except Exception as e:
        # If we can't access attributes (detached instance), skip metrics tracking
        logger.warning(
            f"Failed to track assistant management metric '{metric_name}': unable to access assistant attributes. "
            "This typically occurs when the SQLAlchemy instance is detached from the session.",
            extra={
                "metric_name": metric_name,
                "error_class": e.__class__.__name__,
                "user_id": user.id if user else None,
            },
        )
        return

    # Build attributes dict
    attributes = {
        MetricsAttributes.ASSISTANT_NAME: assistant_name,
        MetricsAttributes.ASSISTANT_DESCRIPTION: assistant_description,
        MetricsAttributes.PROJECT: assistant_project,
        MetricsAttributes.SLUG: assistant_slug,
        MetricsAttributes.LLM_MODEL: assistant_llm_model,
        MetricsAttributes.USER_ID: user.id,
        MetricsAttributes.USER_NAME: user.name,
        MetricsAttributes.USER_EMAIL: user.username,
        MetricsAttributes.NESTED_ASSISTANTS_COUNT: assistant_ids_count,
    }

    # Track MCP server metrics
    for mcp in mcp_servers:
        config = f"{mcp.config}_{mcp.arguments}"
        attributes[MetricsAttributes.MCP_SERVER_NAME] = mcp.name
        attributes[MetricsAttributes.MCP_SERVER_CONFIG] = config
        BaseMonitoringService.send_count_metric(
            name=f"{MCP_SERVERS_ASSISTANT_METRIC}_{metric_name}",
            attributes=attributes,
        )

    if additional_attributes:
        attributes.update(additional_attributes)

    if success:
        BaseMonitoringService.send_count_metric(
            name=metric_name,
            attributes=attributes,
        )
    else:
        BaseMonitoringService.send_count_metric(
            name=metric_name + "_error",
            attributes=attributes,
        )


def _raise_assistant_operation_error(operation: str, exception: Exception):
    """
    Raises a standardized error for assistant operations
    """
    # Get the exception message - handle both ExtendedHTTPException and regular exceptions
    error_message = exception.message if isinstance(exception, ExtendedHTTPException) else str(exception)

    raise ExtendedHTTPException(
        code=status.HTTP_400_BAD_REQUEST,
        message=f"Failed to {operation} assistant",
        details=f"An error occurred while trying to {operation} the assistant: {error_message}",
        help="Please check the assistant data for any invalid fields or constraints. "
        "If you continue to experience issues, please contact support with the error details.",
    ) from exception


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
    """
    request_uuid = raw_request.state.uuid
    _check_user_can_access_assistant(user, assistant, "view", Action.READ)
    _validate_remote_entities_and_raise(assistant)
    _validate_assistant_supports_files_and_raise(assistant, request.file_names)
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

    except MissingContextException as mce:
        error = _create_assistant_error(
            "Assistant Error",
            f"An error occurred during assistant initialization: {str(mce)}\n",
            "We apologize for the inconvenience. Here are some steps you can try:\n"
            "1. Check if the given datasource context is not deleted.\n"
            "2. Check if the correct datasource context is specified for the assistant.\n"
            "If you continue to experience issues, please contact our support team "
            "with the timestamp of your request and any error messages you received.",
        )
        _save_error(request_uuid, request, error, user, assistant)
        raise error from mce
    except BrokerAuthRequiredException:
        raise
    except Exception as e:
        error = _create_assistant_error(
            "Assistant Error",
            f"{str(e)}",
            "We apologize for the inconvenience. Here are some steps you can try:\n"
            "1. Retry your request after a short delay.\n"
            "2. Check if your input is within the expected parameters.\n"
            "If you continue to experience issues, please contact our support team "
            "with the timestamp of your request and any error messages you received.",
        )
        _save_error(request_uuid, request, error, user, assistant)
        logger.error(f"{str(e)}", exc_info=True)
        raise error from e


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


def _validate_assistant_inline_integrations(assistant: Assistant) -> dict:
    """
    Validates if an assistant contains inline integration credentials.
    Note: Prompt variables are now returned separately, not as inline credentials.
    Returns a dict with validation result and details.
    """
    inline_credentials = []

    # Process toolkit credentials
    inline_credentials.extend(_check_toolkit_credentials(assistant.toolkits))

    # Process MCP server credentials
    inline_credentials.extend(_check_mcp_server_credentials(assistant.mcp_servers))

    # Build result with separate prompt_variables field
    result = _build_validation_result(inline_credentials)

    # Add prompt variables separately (not as inline credentials)
    # Mask sensitive variable values for security
    if assistant.prompt_variables:
        result["prompt_variables"] = _get_masked_prompt_variables(assistant.prompt_variables)

    return result


def _get_masked_prompt_variables(prompt_variables: list) -> list:
    """
    Mask the default_value of sensitive prompt variables.
    Returns a new list with masked values for sensitive variables.

    Args:
        prompt_variables: List of prompt variable objects or dicts

    Returns:
        New list with sensitive values masked using SENSITIVE_VALUE_MASK constant
    """
    from codemie.core.constants import SENSITIVE_VALUE_MASK

    if not prompt_variables:
        return []

    masked_variables = []
    for var in prompt_variables:
        # Convert to dict if it's a model object
        if hasattr(var, 'model_dump'):
            var_dict = var.model_dump()
        elif hasattr(var, '__dict__'):
            var_dict = dict(var)
        else:
            var_dict = var

        # Mask the default_value if variable is sensitive
        if var_dict.get('is_sensitive') and var_dict.get('default_value'):
            var_dict['default_value'] = SENSITIVE_VALUE_MASK

        masked_variables.append(var_dict)

    return masked_variables


def _check_toolkit_credentials(toolkits) -> list:
    """
    Check for inline credentials in toolkits and their tools.
    Returns a list of found inline credentials.
    """
    credentials = []

    for toolkit in toolkits:
        # Check toolkit level credentials
        credentials.extend(
            _check_object_credential_values(
                toolkit, "toolkit_settings", toolkit_name=toolkit.toolkit, label=toolkit.label
            )
        )

        # Check tool level credentials
        for tool in toolkit.tools:
            credentials.extend(
                _check_object_credential_values(
                    tool,
                    "tool_settings",
                    toolkit_name=toolkit.toolkit,
                    tool_name=tool.name,
                    label=getattr(tool, "label", tool.name),  # Use label if exists, otherwise name
                )
            )

    return credentials


def _check_object_credential_values(obj, credential_type, **kwargs) -> list:
    """
    Generic helper to check if an object has credential values.
    Returns list with one credential if found, empty list otherwise.
    """
    if not hasattr(obj, "settings") or not obj.settings:
        return []

    if not hasattr(obj.settings, "credential_values") or not obj.settings.credential_values:
        return []

    # Create InlineCredential with dynamic parameters
    cred_params = {"credential_type": credential_type}
    cred_params.update(kwargs)

    # Map parameter names to InlineCredential field names
    param_mapping = {
        "toolkit_name": "toolkit",
        "tool_name": "tool",
        "mcp_server_name": "mcp_server",
        "label": "label",
        "env_vars": "env_vars",
        "credential_type": "credential_type",
    }

    # Adjust parameter names to match InlineCredential fields
    mapped_params = {param_mapping.get(k, k): v for k, v in cred_params.items() if k in param_mapping}

    return [InlineCredential(**mapped_params)]


def _check_mcp_server_credentials(mcp_servers) -> list:
    """
    Check for inline credentials in MCP servers.
    Returns a list of found inline credentials.
    """
    credentials = []

    for server in mcp_servers:
        # Check environment variables
        credentials.extend(
            _check_object_credential_values(
                server, "mcp_environment_vars", mcp_server_name=server.name, toolkit_name="MCP"
            )
        )

        # Check auth token
        if (
            server.mcp_connect_auth_token
            and hasattr(server.mcp_connect_auth_token, "credential_values")
            and server.mcp_connect_auth_token.credential_values
        ):
            credentials.append(
                InlineCredential(mcp_server=server.name, credential_type="mcp_auth_token", toolkit="MCP")
            )

        # Check inline config env vars
        if server.config and server.config.env and len(server.config.env) > 0:
            credentials.append(
                InlineCredential(
                    mcp_server=server.name,
                    credential_type="mcp_inline_config_env",
                    env_vars=list(server.config.env.keys()),
                    toolkit="MCP",
                )
            )

    return credentials


def _build_validation_result(inline_credentials) -> dict:
    """
    Build the final validation result based on found credentials.
    """
    if inline_credentials:
        return {
            "is_valid": False,
            "message": "This assistant contains inline integration credentials that will be shared with all "
            "users who use this assistant. Please confirm that you want to publish it.",
            "inline_credentials": inline_credentials,
        }

    return {"is_valid": True, "inline_credentials": []}


def _enrich_toolkit_settings_config(assistant_toolkits: list[ToolKitDetails], tools_info: list[dict]):
    """
    Updates the settings_config flag in assistant toolkits to match the correct value from tools_info.

    Args:
        assistant_toolkits: list of toolkits from the assistant model
        tools_info: list of toolkit information from ToolsInfoService
    """
    logger.debug("Updating toolkit settings_config flags for assistant")

    # Create lookup dictionary for settings_config
    tools_info_lookup = _create_settings_config_lookup(tools_info)

    # Update settings_config for toolkits and tools
    for toolkit in assistant_toolkits:
        _update_toolkit_settings_config(toolkit, tools_info_lookup)
        _update_tools_settings_config(toolkit, tools_info_lookup)


def _create_settings_config_lookup(tools_info: list[dict]) -> dict:
    """
    Creates a dictionary for quick lookup of settings_config values.
    Args:
        tools_info: list of toolkit information
    Returns:
        dictionary with toolkit and tool names as keys, settings_config as values
    """
    lookup = {}
    for item_info in tools_info:
        toolkit_name = item_info.get("toolkit")
        if not toolkit_name:
            continue

        # Store settings_config for toolkit
        lookup[toolkit_name] = item_info.get("settings_config", False)

        # Store settings_config for tools within the toolkit
        for tool_info in item_info.get("tools", []):
            tool_name = tool_info.get("name")
            if tool_name:
                lookup[(toolkit_name, tool_name)] = tool_info.get("settings_config", False)

    return lookup


def _update_toolkit_settings_config(toolkit, tools_info_lookup: dict):
    """
    Updates the settings_config flag for a single toolkit.
    Args:
        toolkit: The toolkit to update
        tools_info_lookup: dictionary with settings_config values
    """
    # Get settings_config for the toolkit, default to False if not found
    toolkit_info_settings_config = tools_info_lookup.get(toolkit.toolkit, False)

    # Determine the final settings_config value
    if toolkit_info_settings_config and not toolkit.settings:
        toolkit.settings_config = True
    elif toolkit_info_settings_config and toolkit.settings:
        toolkit.settings_config = False
    else:
        toolkit.settings_config = toolkit_info_settings_config


def _update_tools_settings_config(toolkit, tools_info_lookup: dict):
    """
    Updates the settings_config flag for all tools in a toolkit.
    Args:
        toolkit: The toolkit containing tools to update
        tools_info_lookup: dictionary with settings_config values
    """
    for tool in toolkit.tools:
        tool_key = (toolkit.toolkit, tool.name)

        # Get settings_config for the tool, default to False if not found
        tool_info_settings_config = tools_info_lookup.get(tool_key, False)

        # Determine the final settings_config value
        if tool_info_settings_config and not tool.settings:
            tool.settings_config = True
        elif tool_info_settings_config and tool.settings:
            tool.settings_config = False
        else:
            tool.settings_config = tool_info_settings_config


def _get_categories_data(assistant: Assistant):
    """
    Enriches assistant categories from list[str] to list[Category].
    Modifies the assistant object in place.

    Args:
        assistant: The assistant object to enrich
    """

    enriched_categories = category_service.enrich_categories(assistant.categories)
    assistant._enriched_categories = enriched_categories


def _mask_sensitive_prompt_variables(assistant: Assistant):
    """
    Mask default values for sensitive prompt variables in the assistant definition.
    Uses the shared masking function and updates the assistant in place.

    Args:
        assistant: The assistant object to mask
    """
    if not assistant.prompt_variables:
        return

    # Use shared function and update each variable in place
    masked_list = _get_masked_prompt_variables(assistant.prompt_variables)
    for i, masked_var in enumerate(masked_list):
        if i < len(assistant.prompt_variables):
            assistant.prompt_variables[i].default_value = masked_var['default_value']


def _filter_invalid_datasources(assistant: Assistant):
    """
    Filter out datasources that don't exist in the assistant's target project.
    This prevents errors when cloning or editing assistants across projects.
    Uses targeted database query for optimal performance.
    Modifies the assistant object in place.

    Args:
        assistant: The assistant object to filter
    """
    if not assistant.context:
        return

    # Extract datasource names that the assistant references
    datasource_names = {ctx.name for ctx in assistant.context}

    # Query only for the specific datasources we're checking (performance optimization)
    with Session(IndexInfo.get_engine()) as session:
        statement = select(IndexInfo.repo_name, IndexInfo.index_type).where(
            and_(IndexInfo.project_name == assistant.project, IndexInfo.repo_name.in_(datasource_names))
        )
        existing = session.exec(statement).all()

        # Build valid set from query results
        valid_datasources = {
            (repo_name, Context.index_info_type_from_index_type(index_type)) for repo_name, index_type in existing
        }

    # Filter context to only include valid datasources
    original_count = len(assistant.context)
    assistant.context = [ctx for ctx in assistant.context if (ctx.name, ctx.context_type) in valid_datasources]

    # Log if any datasources were filtered out
    if filtered_count := original_count - len(assistant.context):
        logger.info(
            f"Filtered out {filtered_count} invalid datasource(s) for assistant "
            f"'{assistant.name}' in project '{assistant.project}'"
        )


def _encrypt_sensitive_prompt_variables(assistant: Assistant):
    """
    Encrypt default values for sensitive prompt variables in the assistant definition.
    Modifies the assistant object in place.

    Args:
        assistant: The assistant object to encrypt
    """
    from codemie.core.constants import SENSITIVE_VALUE_MASK
    from codemie.service.encryption.encryption_factory import EncryptionFactory

    if not assistant.prompt_variables:
        return

    encryption_service = EncryptionFactory().get_current_encryption_service()

    for var in assistant.prompt_variables:
        if getattr(var, 'is_sensitive', False) and var.default_value and var.default_value != SENSITIVE_VALUE_MASK:
            var.default_value = encryption_service.encrypt(var.default_value)


def _encrypt_prompt_variables_in_request(request: AssistantRequest):
    """
    Encrypt default values for sensitive prompt variables in the request.
    Modifies the request object in place.

    Args:
        request: The assistant request to encrypt
    """
    from codemie.core.constants import SENSITIVE_VALUE_MASK
    from codemie.service.encryption.encryption_factory import EncryptionFactory

    if not request.prompt_variables:
        return

    encryption_service = EncryptionFactory().get_current_encryption_service()

    for var in request.prompt_variables:
        if getattr(var, 'is_sensitive', False) and var.default_value and var.default_value != SENSITIVE_VALUE_MASK:
            var.default_value = encryption_service.encrypt(var.default_value)


def _restore_existing_encrypted_values(request: AssistantRequest, existing_assistant: Assistant):
    """
    For sensitive variables whose value in the request equals the masked sentinel,
    restore the already-encrypted value from the existing assistant so it is not overwritten.
    Modifies the request object in place.

    Args:
        request: The incoming update request
        existing_assistant: The current assistant loaded from the database
    """
    from codemie.core.constants import SENSITIVE_VALUE_MASK

    if not request.prompt_variables or not existing_assistant.prompt_variables:
        return

    existing_by_key = {var.key: var for var in existing_assistant.prompt_variables}

    for var in request.prompt_variables:
        if getattr(var, 'is_sensitive', False) and var.default_value == SENSITIVE_VALUE_MASK:
            existing_var = existing_by_key.get(var.key)
            if existing_var and existing_var.default_value:
                var.default_value = existing_var.default_value


# ============================================================================
# Assistant Versioning Endpoints
# ============================================================================


@router.get(
    "/assistants/{assistant_id}/versions",
    status_code=status.HTTP_200_OK,
    tags=["Assistant Versioning"],
)
def get_assistant_versions(assistant_id: str, user: User = Depends(authenticate), page: int = 0, per_page: int = 20):
    """
    Get version history for an assistant.

    Returns paginated list of all versions with metadata.

    **Query Parameters:**
    - page: Page number (default: 0)
    - per_page: Number of versions per page (default: 20)

    **Returns:**
    - versions: list of version configurations
    - total_versions: Total number of versions
    - assistant_name: Assistant name
    - assistant_id: Assistant ID
    """
    from codemie.service.assistant.assistant_version_service import AssistantVersionService

    assistant = _get_assistant_by_id_or_raise(assistant_id)
    _check_user_can_access_assistant(user, assistant, "view", Action.READ)

    return AssistantVersionService.get_version_history(assistant=assistant, page=page, per_page=per_page)


@router.get(
    "/assistants/{assistant_id}/versions/{version_number}",
    status_code=status.HTTP_200_OK,
    tags=["Assistant Versioning"],
)
def get_assistant_version(assistant_id: str, version_number: int, user: User = Depends(authenticate)):
    """
    Get a specific version of an assistant.

    **Path Parameters:**
    - assistant_id: The assistant ID
    - version_number: The version number to retrieve

    **Returns:**
    Complete configuration for the specified version
    """
    from codemie.service.assistant.assistant_version_service import AssistantVersionService

    assistant = _get_assistant_by_id_or_raise(assistant_id)
    _check_user_can_access_assistant(user, assistant, "view", Action.READ)

    return AssistantVersionService.get_version(assistant_id=assistant_id, version_number=version_number)


@router.get(
    "/assistants/{assistant_id}/versions/{version1}/compare/{version2}",
    status_code=status.HTTP_200_OK,
    tags=["Assistant Versioning"],
)
def compare_assistant_versions(assistant_id: str, version1: int, version2: int, user: User = Depends(authenticate)):
    """
    Compare two versions of an assistant.

    Returns detailed differences between the two versions.

    **Path Parameters:**
    - assistant_id: The assistant ID
    - version1: First version number
    - version2: Second version number

    **Returns:**
    - version1: Complete first version configuration
    - version2: Complete second version configuration
    - differences: Detailed diff object
    - change_summary: Human-readable summary
    """
    from codemie.service.assistant.assistant_version_service import AssistantVersionService
    from codemie.service.assistant.assistant_version_compare_service import AssistantVersionCompareService

    assistant = _get_assistant_by_id_or_raise(assistant_id)
    _check_user_can_access_assistant(user, assistant, "view", Action.READ)

    # Get both versions
    config1 = AssistantVersionService.get_version(assistant_id, version1)
    config2 = AssistantVersionService.get_version(assistant_id, version2)

    # Compare
    return AssistantVersionCompareService.compare_versions(
        assistant_id=assistant_id, version1=config1, version2=config2
    )


@router.post(
    "/assistants/{assistant_id}/versions/{version_number}/rollback",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
    tags=["Assistant Versioning"],
)
def rollback_assistant_version(
    assistant_id: str,
    version_number: int,
    user: User = Depends(authenticate),
):
    """
    Rollback assistant to a previous version.

    Creates a new version with the configuration from the target version.
    This maintains version history immutability.

    **Path Parameters:**
    - assistant_id: The assistant ID
    - version_number: The version to rollback to

    **Returns:**
    Success message with the new version number created from rollback
    """
    from codemie.service.assistant.assistant_version_service import AssistantVersionService

    assistant = _get_assistant_by_id_or_raise(assistant_id)
    _check_user_can_access_assistant(user, assistant, "update", Action.WRITE)
    _validate_remote_entities_and_raise(assistant)

    try:
        new_config = AssistantVersionService.rollback_to_version(
            assistant=assistant, target_version_number=version_number, user=user, change_notes=None
        )

        _track_assistant_management_metric(
            "rollback_version", assistant, user, True, {"target_version": version_number}
        )

        return BaseResponse(
            message=f"Rolled back to version {version_number}. Created new version {new_config.version_number}"
        )

    except Exception as e:
        _track_assistant_management_metric(
            "rollback_version", assistant, user, False, {"error_class": e.__class__.__name__}
        )
        raise


def _track_mcp_usage_on_create(mcp_servers: list):
    """
    Track usage count when assistant is created with MCP servers.

    Args:
        mcp_servers: list of MCPServerDetails from the assistant
    """

    if not mcp_servers:
        return

    for mcp_server in mcp_servers:
        if not mcp_server.enabled:
            continue

        # Track if mcp_config_id is provided (from marketplace selection)
        if hasattr(mcp_server, 'mcp_config_id') and mcp_server.mcp_config_id:
            try:
                MCPConfigService.increment_usage(mcp_server.mcp_config_id)
                logger.debug(f"Incremented usage for MCP config: {mcp_server.mcp_config_id}")
            except Exception as e:
                logger.warning(f"Failed to increment usage for {mcp_server.mcp_config_id}: {e}")


def _track_mcp_usage_on_delete(mcp_servers: list):
    """
    Track usage count when assistant is deleted with MCP servers.

    Args:
        mcp_servers: list of MCPServerDetails from the assistant being deleted
    """
    from codemie.service.mcp_config_service import MCPConfigService

    if not mcp_servers:
        return

    for mcp_server in mcp_servers:
        if hasattr(mcp_server, 'mcp_config_id') and mcp_server.mcp_config_id:
            try:
                MCPConfigService.decrement_usage(mcp_server.mcp_config_id)
                logger.debug(f"Decremented usage for deleted assistant MCP: {mcp_server.mcp_config_id}")
            except Exception as e:
                logger.warning(f"Failed to decrement usage for {mcp_server.mcp_config_id}: {e}")


def _index_marketplace_assistant(assistant_id: str, user: User, is_update: bool = False):
    """
    Background task to index or reindex a marketplace assistant in Elasticsearch.

    This function runs asynchronously after publish/update operations complete,
    ensuring the marketplace index stays in sync with PostgreSQL data without blocking API responses.

    Args:
        assistant_id: ID of the assistant to index
        user: User who triggered the operation (for cost tracking)
        is_update: If True, this is an update of existing document (won't increment progress counters).
                   If False, this is a new document (will increment progress counters).
    """
    from codemie.service.platform.platform_indexing_service import PlatformIndexingService

    from codemie.service.llm_service.utils import set_llm_context

    set_llm_context(None, user.current_project, user)

    operation = "reindexing" if is_update else "indexing"
    try:
        PlatformIndexingService.index_single_assistant(assistant_id, user=user, is_update=is_update)
        logger.info(
            f"Background {operation} completed for marketplace assistant {assistant_id}",
            extra={"assistant_id": assistant_id, "is_update": is_update},
        )
    except Exception as index_error:
        logger.error(
            f"Background {operation} failed for marketplace assistant {assistant_id}: {index_error}",
            extra={"assistant_id": assistant_id, "error": str(index_error), "is_update": is_update},
            exc_info=True,
        )


def _remove_marketplace_assistant(assistant_id: str, assistant_name: str, user: User):
    """
    Background task to remove an unpublished assistant from Elasticsearch.

    This function runs asynchronously after the assistant unpublish completes,
    removing the assistant from the marketplace index without blocking the API response.

    Args:
        assistant_id: ID of the assistant to remove
        assistant_name: Name of the assistant (for metadata tracking)
        user: User who triggered the unpublish (for cost tracking)
    """
    from codemie.service.platform.platform_indexing_service import PlatformIndexingService

    from codemie.service.llm_service.utils import set_llm_context

    set_llm_context(None, user.current_project, user)

    try:
        PlatformIndexingService.remove_single_assistant(assistant_id, assistant_name, user=user)
        logger.info(
            f"Background removal completed for unpublished assistant {assistant_id}",
            extra={"assistant_id": assistant_id},
        )
    except Exception as index_error:
        logger.error(
            f"Background removal failed for unpublished assistant {assistant_id}: {index_error}",
            extra={"assistant_id": assistant_id, "error": str(index_error)},
            exc_info=True,
        )


def _llm_validate_on_marketplace_publish(
    raw_request: Request, assistant: Assistant, user: User, is_bypassing: bool
) -> bool:
    """
    Validates if an assistant can be published to the marketplace.
    Handles bypass logic if user chose to ignore recommendations.
    Raises exception if validation fails and user didn't bypass.

    Args:
        raw_request: The FastAPI request object
        assistant: The assistant to validate
        user: The user publishing the assistant
        is_bypassing: True if user chose to bypass validation recommendations

    Returns:
        True if user bypassed recommendations (for metrics tracking), False otherwise
    """
    # If user already chose to bypass, skip validation entirely
    if is_bypassing:
        logger.info(f"User {user.id} bypassed recommendations for assistant {assistant.id}")
        return True

    request_id = raw_request.state.uuid if hasattr(raw_request.state, "uuid") else None

    # Set user context for LLM budget tracking during validation workflow
    user_email = user.username or user.id
    set_logging_info(
        uuid=request_id or "unknown",
        user_id=user.id,
        conversation_id=request_id or "unknown",
        user_email=user_email,
    )
    logger.debug(
        f"Set user context for assistant validation: "
        f"user_email={user_email}, assistant_id={assistant.id}, request_id={request_id}"
    )

    quality_validation_result = AssistantGeneratorService.validate_assistant_for_publish(
        assistant=assistant, user=user, request_id=request_id
    )

    if quality_validation_result.decision == "accept":
        return False

    # Validation failed
    logger.info(
        f"Assistant publication blocked due to quality validation failure. "
        f"Assistant ID: {assistant.id}, User ID: {user.id}, "
        f"Decision: {quality_validation_result.decision}, "
    )
    raise ExtendedHTTPException(
        code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        message="Assistant quality validation failed. Please improve the assistant before publishing.",
        details=PublishValidationErrorResponse(
            requires_confirmation=True,
            assistant_id=assistant.id,
            quality_validation=quality_validation_result,
        ).model_dump(),
        help=(
            "We recommend using the 'Refine with AI' feature to enhance your assistant's quality. "
            "This will help resolve the validation failures and allow for successful publishing."
        ),
    )
