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


from sqlalchemy.exc import IntegrityError

from fastapi import APIRouter, status, Depends, BackgroundTasks

from codemie.configs import logger
from codemie.core.ability import Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.utils import slugify
from codemie.core.models import (
    BaseResponse,
)
from codemie.rest_api.models.guardrail import GuardrailEntity
from codemie.rest_api.models.assistant import (
    Assistant,
    AssistantRequest,
    AssistantCreateResponse,
    AssistantUpdateResponse,
    CreatedByUser,
    MCPServerDetails,
    AssistantOrigin,
)
from codemie.rest_api.routers.assistant._shared import (
    _index_marketplace_assistant,
    _raise_assistant_operation_error,
    _track_assistant_management_metric,
    _validate_remote_entities_and_raise,
)
from codemie.rest_api.routers.utils import check_user_can_access_assistant, get_assistant_by_id_or_raise
from codemie.rest_api.security.authentication import project_access_check
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.rest_api.utils.default_applications import ensure_application_exists
from codemie.repository.assistants.assistant_clone_event_repository import AssistantCloneEventRepository
from codemie.service.assistant.assistant_repository import AssistantRepository
from codemie.service.assistant.category_service import category_service
from codemie.service.assistant.sensitive_prompt_variables import (
    encrypt_prompt_variables_in_request,
    encrypt_sensitive_prompt_variables,
    restore_existing_encrypted_values,
)
from codemie.service.assistant_service import AssistantService
from codemie.service.guardrail.guardrail_service import GuardrailService
from codemie.service.mcp_config_service import MCPConfigService
from codemie.service.mcp.access_control import MCPAccessControlService


router = APIRouter()


def _update_source_clone_count(source_assistant_id: str, user: User) -> None:
    """Log a clone event for the source assistant and refresh its clone count.

    Best-effort telemetry: failures are logged and swallowed so a clone-count
    problem never fails the create that triggered it. Skips (no-op) if the
    source assistant does not exist, so an arbitrary/stale id can't be used
    to log clone events for nothing.
    """
    try:
        if not Assistant.find_by_id(source_assistant_id):
            logger.warning(f"Source assistant {source_assistant_id} not found; skipping clone tracking.")
            return
        clone_event_repo = AssistantCloneEventRepository()
        clone_event_repo.log_clone_event(source_assistant_id, user.id)
        AssistantRepository.update_clone_count(source_assistant_id)
    except Exception:
        logger.error(
            f"Failed to update clone count for source assistant {source_assistant_id}",
            exc_info=True,
        )


def _validate_integrations_or_response(request: AssistantRequest, user: User) -> AssistantCreateResponse | None:
    """Run integration validation unless skipped; return an early response on missing integrations, else None."""
    if request.skip_integration_validation:
        return None

    logger.info(f"Validating integrations for assistant: {request.name}")
    validation_result = _validate_assistant_integrations(request, user)

    if not validation_result.has_missing_integrations:
        return None

    logger.warning(f"Validation failed: {validation_result.message}")
    return AssistantCreateResponse(
        message="Assistant validation found missing integrations. "
        "Please configure them or set skip_integration_validation=True.",
        assistant_id=None,
        validation=validation_result,
    )


def _build_assistant_from_create_request(request: AssistantRequest, user: User) -> Assistant:
    """Construct a new, unsaved Assistant from a create request."""
    assistant = Assistant(
        **request.model_dump(exclude={"guardrail_assignments", "skip_integration_validation", "source_assistant_id"})
    )
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
    encrypt_sensitive_prompt_variables(assistant)

    # Slug powers /assistants/{project}/{slug} URLs. A client-supplied slug is kept as-is and
    # validated by _check_slug_uniqueness() on save (per-project collision -> clear error, not a
    # silent suffix). When none is supplied, derive it from the name and auto-suffix collisions;
    # an empty derivation stays NULL (exempt from the partial unique index).
    if not request.slug:
        derived_slug = slugify(assistant.name) or None
        if derived_slug:
            derived_slug = AssistantService.ensure_unique_slug(derived_slug, assistant.project)
        assistant.slug = derived_slug

    if request.agent_card and request.agent_card.bedrock_agentcore:
        assistant.origin = AssistantOrigin.BEDROCK_AGENT_CORE

    return assistant


def _save_new_assistant(assistant: Assistant, request: AssistantRequest, user: User) -> None:
    """Persist a newly built assistant, mapping known integrity errors to friendly responses."""
    from codemie.service.assistant.assistant_version_service import AssistantVersionService

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

        if request.source_assistant_id:
            _update_source_clone_count(request.source_assistant_id, user)

        _track_assistant_management_metric("create_assistant", assistant, user, True)
    except IntegrityError as e:
        # Concurrency backstop. _check_slug_uniqueness() (in validate_fields) reads ES in a
        # separate transaction, so two simultaneous creates can both pass it and collide only at
        # COMMIT on uq_assistants_project_slug. Map that to the same friendly 400 the serial path
        # returns instead of leaking the raw psycopg2 constraint error; other integrity errors keep
        # the previous generic handling.
        _track_assistant_management_metric(
            "create_assistant", assistant, user, False, {"error_class": e.__class__.__name__}
        )
        if "uq_assistants_project_slug" in str(getattr(e, "orig", e)):
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Slug conflict",
                details=(
                    f"Slug '{assistant.slug}' is already used by another assistant in this project. "
                    "Slugs must be unique within a project — please choose a different slug."
                ),
            ) from e
        _raise_assistant_operation_error("save", e)
    except Exception as e:
        _track_assistant_management_metric(
            "create_assistant", assistant, user, False, {"error_class": e.__class__.__name__}
        )
        _raise_assistant_operation_error("save", e)


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
    from codemie.service.llm.model_availability_service import ModelAvailabilityService
    from codemie.core.exceptions import ModelNotWhitelistedException

    project_access_check(user, request.project)

    # Stage 1: Validate model is whitelisted for project
    if request.llm_model_type and not request.is_global:
        try:
            ModelAvailabilityService.validate_model_for_asset_creation(
                request.llm_model_type,
                request.project,
            )
        except ModelNotWhitelistedException as e:
            raise ExtendedHTTPException(
                code=e.code,
                message=e.message,
                details=e.details,
            ) from e

    validation_response = _validate_integrations_or_response(request, user)
    if validation_response:
        return validation_response

    request.mcp_servers = MCPAccessControlService.sanitize_for_save(request.mcp_servers)

    assistant = _build_assistant_from_create_request(request, user)

    # Ensure Application exists for the project
    if request.project:
        ensure_application_exists(request.project)

    _save_new_assistant(assistant, request, user)

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
    assistant = get_assistant_by_id_or_raise(assistant_id)
    check_user_can_access_assistant(user, assistant, "update", Action.WRITE)
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

    request.mcp_servers = MCPAccessControlService.sanitize_for_save(request.mcp_servers)

    repository = AssistantRepository()

    old_mcp_servers = list(assistant.mcp_servers or [])

    # Encrypt new plain-text sensitive values first (masked "********" values are skipped).
    if request.prompt_variables:
        encrypt_prompt_variables_in_request(request)

    # Restore existing encrypted DB values for variables sent back as masked.
    # Must run AFTER encryption to avoid double-encrypting already-encrypted values.
    if request.prompt_variables:
        restore_existing_encrypted_values(request, assistant)

    try:
        # Check if assistant is published to marketplace before update
        was_global = assistant.is_global

        # Validate category IDs (required for marketplace assistants)
        category_service.validate_category_ids(request.categories, required=was_global)

        # Extracting the guardrail_assignments before the update
        guardrail_assignments = request.guardrail_assignments

        repository.update(assistant, request, user)
        _track_mcp_usage_changes(old_mcp_servers, list(assistant.mcp_servers or []))

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
    assistant = get_assistant_by_id_or_raise(assistant_id)
    check_user_can_access_assistant(user, assistant, "delete", Action.DELETE)

    # Track MCP usage before deletion
    _track_mcp_usage_on_delete(assistant.mcp_servers)

    Assistant.delete_assistant(assistant_id)
    _track_assistant_management_metric("delete_assistant", assistant, user, True)
    return BaseResponse(message="Specified assistant removed")


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


def _filter_invalid_datasources(assistant: Assistant):
    """
    Filter out datasources that don't exist in the assistant's target project.
    This prevents errors when cloning or editing assistants across projects.
    Modifies the assistant object in place.
    """
    if dropped := AssistantService.drop_missing_context(assistant):
        logger.info(
            f"Filtered out {len(dropped)} invalid datasource(s) for assistant "
            f"'{assistant.name}' in project '{assistant.project}'"
        )


def _track_mcp_usage_on_create(mcp_servers: list[MCPServerDetails]) -> None:
    """Increment usage counts for all enabled catalog-ref servers on assistant create."""
    if not mcp_servers:
        return
    config_ids = {s.mcp_config_id for s in mcp_servers if s.enabled and s.mcp_config_id}
    try:
        MCPConfigService.adjust_usage(increments=config_ids, decrements=set())
    except Exception as e:
        logger.warning(f"Failed to track MCP usage on create: {e}", exc_info=True)


def _track_mcp_usage_on_delete(mcp_servers: list[MCPServerDetails]) -> None:
    """Decrement usage counts for all catalog-ref servers on assistant delete."""
    if not mcp_servers:
        return
    config_ids = {s.mcp_config_id for s in mcp_servers if s.mcp_config_id}
    try:
        MCPConfigService.adjust_usage(increments=set(), decrements=config_ids)
    except Exception as e:
        logger.warning(f"Failed to track MCP usage on delete: {e}", exc_info=True)


def _track_mcp_usage_changes(old_servers: list[MCPServerDetails], new_servers: list[MCPServerDetails]) -> None:
    """Track usage count delta when assistant MCP servers are updated (BR-8.2)."""
    old_ids = {s.mcp_config_id for s in old_servers if s.enabled and s.mcp_config_id}
    new_ids = {s.mcp_config_id for s in new_servers if s.enabled and s.mcp_config_id}
    try:
        MCPConfigService.adjust_usage(increments=new_ids - old_ids, decrements=old_ids - new_ids)
    except Exception as e:
        logger.warning(f"Failed to track MCP usage changes: {e}", exc_info=True)
