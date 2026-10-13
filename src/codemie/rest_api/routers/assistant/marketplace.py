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

from typing import Literal, Optional, List

from pydantic import BaseModel, Field

from fastapi import APIRouter, status, Request, Depends, BackgroundTasks

from codemie.configs import logger, config
from codemie.configs.logger import set_logging_info
from codemie.core.ability import Action
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import (
    BaseResponse,
)
from codemie.rest_api.models.assistant import (
    Assistant,
    PublishValidationResponse,
    InlineCredential,
    SubAssistantPublishSettings,
    PublishValidationErrorResponse,
)
from codemie.service.assistant_generator_service import AssistantGeneratorService
from codemie.service.assistant.inline_credentials import validate_assistant_inline_integrations
from codemie.rest_api.routers.assistant._shared import (
    _index_marketplace_assistant,
    _raise_assistant_operation_error,
    _track_assistant_management_metric,
    _validate_remote_entities_and_raise,
)
from codemie.rest_api.routers.assistant._sub_assistants import _publish_sub_assistants, _validate_publish_marketplace
from codemie.rest_api.routers.utils import check_user_can_access_assistant, get_assistant_by_id_or_raise
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_user_interaction_service import assistant_user_interaction_service


router = APIRouter()


class ReactionRequest(BaseModel):
    """Request model for assistant reactions"""

    reaction: Literal["like", "dislike"]


class PublishToMarketplaceRequest(BaseModel):
    """Request model for publishing assistant to marketplace"""

    categories: list[str] = Field(
        min_length=1, max_length=3, description="Category IDs for marketplace classification (1-3 required)"
    )
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
    get_assistant_by_id_or_raise(assistant_id)

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
    get_assistant_by_id_or_raise(assistant_id)

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
    assistant = get_assistant_by_id_or_raise(assistant_id)
    check_user_can_access_assistant(user, assistant, "write", Action.WRITE)
    _validate_remote_entities_and_raise(assistant)

    # Check for inline integration credentials in main assistant
    validation_result = validate_assistant_inline_integrations(assistant)
    all_inline_credentials = validation_result["inline_credentials"]
    prompt_variables = validation_result.get("prompt_variables", [])

    # Get sub-assistants information and check their inline credentials
    sub_assistants_info = []
    if assistant.assistant_ids:
        for sub_id in assistant.assistant_ids:
            sub_assistant = get_assistant_by_id_or_raise(sub_id)

            # Validate sub-assistant inline integrations
            sub_validation_result = validate_assistant_inline_integrations(sub_assistant)

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
    assistant = get_assistant_by_id_or_raise(assistant_id)
    _validate_publish_marketplace(assistant, user, request.categories)

    # Set LiteLLM context with user's credentials
    from codemie.service.llm_service.utils import set_llm_context

    set_llm_context(assistant, None, user)

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
    assistant = get_assistant_by_id_or_raise(assistant_id)
    check_user_can_access_assistant(user, assistant, "write", Action.WRITE)

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
    user_email = user.username or user.email or None
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
        f"Assistant ID: {assistant.id}, User ID: {user.id}, User Email: {user_email}, "
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
