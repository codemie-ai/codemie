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

from elasticsearch import NotFoundError
from fastapi import APIRouter, status, Request, Depends, Query
from fastapi.responses import RedirectResponse
from typing import Optional, Union

from codemie.rest_api.models.usage.assistant_user_interaction import ReactionType

from codemie.configs import config, logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import UserResponse, ProjectInfoResponse
from codemie.rest_api.models.user import UserData, UserDataChangeRequest
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.user_reactions import (
    AssistantReactionResponse,
    MinimalAssistantReactionResponse,
    MinimalSkillReactionResponse,
    ResourceType,
    SkillReactionResponse,
    UserReactionsResponse,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.idp import get_idp_provider
from codemie.rest_api.security.user import User
from codemie.service.monitoring.conversation_monitoring_service import ConversationMonitoringService
from codemie.service.assistant.assistant_user_interaction_service import assistant_user_interaction_service
from codemie.service.skill_user_interaction_service import skill_user_interaction_service
from codemie.service.skill_service import SkillService

conversation_monitoring_service = ConversationMonitoringService()

router = APIRouter(
    tags=["User"],
    prefix="/v1",
    dependencies=[],
)


def _get_user_response(user: User) -> UserResponse:
    """Helper to build UserResponse from authenticated user.

    F-10: When user management is enabled, queries DB directly for projects
    instead of relying on the security context indirection.
    """
    if config.ENABLE_USER_MANAGEMENT:
        from codemie.clients.postgres import get_session
        from codemie.repository.user_project_repository import user_project_repository

        with get_session() as session:
            user_projects = user_project_repository.get_by_user_id(session, user.id)
            projects = [
                ProjectInfoResponse(name=p.project_name, is_project_admin=p.is_project_admin) for p in user_projects
            ]
    else:
        # Legacy path: derive from security context (IDP mode)
        projects = [
            ProjectInfoResponse(name=name, is_project_admin=name in user.admin_project_names)
            for name in user.project_names
        ]

    return UserResponse(
        user_id=user.id,
        name=user.full_name,
        username=user.username,
        email=user.email,
        is_admin=user.is_admin,
        picture=user.picture,
        projects=projects,
        project_limit=user.project_limit,
        knowledge_bases=user.knowledge_bases,
        user_type=user.user_type,
        applications=user.applications,
        applications_admin=user.applications_admin,
    )


@router.get(
    "/user",
    dependencies=[Depends(authenticate)],
    status_code=status.HTTP_200_OK,
    response_model=UserResponse,
)
def me(request: Request):
    """
    Returns the current user data (user profile)
    """
    user = request.state.user
    return _get_user_response(user)


@router.get(
    "/profile",
    dependencies=[Depends(authenticate)],
    status_code=status.HTTP_200_OK,
    response_model=UserResponse,
)
def get_profile(request: Request):
    """
    Returns the current user profile (alias for /v1/user)

    Story 3 AC: User profile response (GET /v1/profile) includes projects array
    """
    user = request.state.user
    return _get_user_response(user)


@router.get("/user/log_out", include_in_schema=False)
def logout() -> RedirectResponse:
    """
    Deletes keycloak cookie and redirects to keycloak logout page
    """

    response = RedirectResponse(url=config.KEYCLOAK_LOGOUT_URL, status_code=status.HTTP_302_FOUND)
    response.delete_cookie(get_idp_provider().get_session_cookie())

    return response


@router.get(
    "/user/data",
    status_code=status.HTTP_200_OK,
)
def get_user_data(user: User = Depends(authenticate)):
    """
    Returns saved user data or creates a new one
    """
    user_data = UserData.get_by_fields({"user_id.keyword": user.id})

    if not user_data:
        user_data = UserData(user_id=user.id, sidebar_view="flat")
        user_data.save()

    return {
        **user_data.dict(),
        "stt_support": bool(config.STT_API_KEY),
    }


@router.put(
    "/user/data",
    status_code=status.HTTP_200_OK,
    response_model=UserData,
    response_model_by_alias=True,
)
def update_user_data(request: UserDataChangeRequest, user: User = Depends(authenticate)):
    """
    Update or create user data for specified user
    """

    try:
        data: UserData = UserData.get_by_fields({"user_id.keyword": user.id})
    except NotFoundError:
        data = UserData(user_id=user.id, sidebar_view=request.sidebar_view)
        data.save()
        return data

    try:
        if request.sidebar_view:
            data.sidebar_view = request.sidebar_view
            conversation_monitoring_service.send_view_mode_metric(
                request.sidebar_view,
                user_id=user.id,
                user_name=user.full_name,
            )
        data.update()

        return data
    except Exception as e:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="Cannot change specified user data",
            details=f"An error occurred while trying to create the setting: {str(e)}",
        ) from e


# =============================================================================
# Helper Functions for User Reactions
# =============================================================================


def _process_assistant_reactions(
    user: User,
    reaction_enum: Optional[ReactionType],
    include_details: bool,
) -> list[Union[AssistantReactionResponse, MinimalAssistantReactionResponse]]:
    """
    Process and format assistant reactions for the current user.

    Args:
        user: Current authenticated user
        reaction_enum: Optional reaction type filter
        include_details: Whether to include full assistant details

    Returns:
        List of assistant reaction responses (detailed or minimal)
    """
    assistant_reactions = assistant_user_interaction_service.get_reactions_by_user(user.id, reaction_enum)

    if not assistant_reactions:
        return []

    if include_details:
        # Get all assistant IDs for batch retrieval
        assistant_ids = [record.assistant_id for record in assistant_reactions]
        logger.debug(f"Fetching details for {len(assistant_ids)} assistants")

        # Get all assistants in one batch
        assistants = {a.id: a for a in Assistant.get_by_ids(user, assistant_ids)}
        logger.debug(f"Retrieved {len(assistants)} assistants from database")

        # Create result with assistant details
        result = []
        for record in assistant_reactions:
            assistant = assistants.get(record.assistant_id)
            if assistant:
                result.append(
                    AssistantReactionResponse(
                        resource_id=record.assistant_id,
                        name=assistant.name,
                        description=assistant.description,
                        project=assistant.project,
                        reaction=record.reaction.value,
                        reaction_at=record.reaction_at,
                        slug=assistant.slug,
                        icon=assistant.icon,
                    )
                )
            else:
                # Include minimal info if assistant details not found
                result.append(
                    MinimalAssistantReactionResponse(
                        resource_id=record.assistant_id,
                        reaction=record.reaction.value,
                        reaction_at=record.reaction_at,
                    )
                )
        return result
    else:
        # Just include minimal info without assistant details
        return [
            MinimalAssistantReactionResponse(
                resource_id=record.assistant_id,
                reaction=record.reaction.value,
                reaction_at=record.reaction_at,
            )
            for record in assistant_reactions
        ]


def _process_skill_reactions(
    user: User,
    reaction_enum: Optional[ReactionType],
    include_details: bool,
) -> list[Union[SkillReactionResponse, MinimalSkillReactionResponse]]:
    """
    Process and format skill reactions for the current user.

    Args:
        user: Current authenticated user
        reaction_enum: Optional reaction type filter
        include_details: Whether to include full skill details

    Returns:
        List of skill reaction responses (detailed or minimal)
    """
    skill_reactions = skill_user_interaction_service.get_reactions_by_user(user.id, reaction_enum)

    if not skill_reactions:
        return []

    if include_details:
        # Get all skill IDs for batch retrieval
        skill_ids = [record.skill_id for record in skill_reactions]
        logger.debug(f"Fetching details for {len(skill_ids)} skills")

        # Get all skills in one batch - using get_skills_by_ids which filters by access
        skills_list = SkillService.get_skills_by_ids(skill_ids, user)
        skills = {s.id: s for s in skills_list}
        logger.debug(f"Retrieved {len(skills)} skills from database")

        # Create result with skill details
        result = []
        for record in skill_reactions:
            skill = skills.get(record.skill_id)
            if skill:
                result.append(
                    SkillReactionResponse(
                        resource_id=record.skill_id,
                        name=skill.name,
                        description=skill.description,
                        project=skill.project,
                        reaction=record.reaction.value,
                        reaction_at=record.reaction_at,
                        visibility=skill.visibility.value,
                        categories=list(skill.categories) if skill.categories else [],
                    )
                )
            else:
                # Include minimal info if skill details not found
                result.append(
                    MinimalSkillReactionResponse(
                        resource_id=record.skill_id,
                        reaction=record.reaction.value,
                        reaction_at=record.reaction_at,
                    )
                )
        return result
    else:
        # Just include minimal info without skill details
        return [
            MinimalSkillReactionResponse(
                resource_id=record.skill_id,
                reaction=record.reaction.value,
                reaction_at=record.reaction_at,
            )
            for record in skill_reactions
        ]


# =============================================================================
# API Endpoints
# =============================================================================


@router.get(
    "/user/reactions",
    status_code=status.HTTP_200_OK,
    response_model=UserReactionsResponse,
    response_model_by_alias=True,
)
def get_user_reactions(
    user: User = Depends(authenticate),
    include_details: bool = Query(False, description="Whether to include resource details in the response"),
    reaction_type: Optional[str] = Query(
        None, description="Filter by reaction type (like, dislike). If not provided, returns all reactions."
    ),
    resource_type: ResourceType = Query(
        ResourceType.ASSISTANTS,
        description="Type of resource to get reactions for: 'assistants', 'skills', or 'all'. Default: 'assistants'",
    ),
):
    """
    Returns a list of resources (assistants and/or skills) that the current user has reacted to.

    This endpoint retrieves all resources that have been reacted to by the currently
    authenticated user. For each resource, it returns the resource ID, type, reaction type,
    and when the reaction was made. If include_details is True, it also includes additional
    information such as name, description, project, and other resource-specific fields.

    Args:
        user: Current authenticated user
        include_details: Whether to include full resource details
        reaction_type: Filter by 'like' or 'dislike'
        resource_type: Filter by resource type (enum handles validation)
    """
    try:
        # Convert reaction_type string to enum if provided
        reaction_enum = None
        if reaction_type:
            try:
                reaction_enum = ReactionType(reaction_type.lower())
            except ValueError:
                raise ExtendedHTTPException(
                    code=status.HTTP_400_BAD_REQUEST,
                    message="Invalid reaction type",
                    details=f"Reaction type must be 'like' or 'dislike', got: {reaction_type}",
                )

        result = []

        # Get assistant reactions
        if resource_type in (ResourceType.ASSISTANTS, ResourceType.ALL):
            result.extend(_process_assistant_reactions(user, reaction_enum, include_details))

        # Get skill reactions
        if resource_type in (ResourceType.SKILLS, ResourceType.ALL):
            result.extend(_process_skill_reactions(user, reaction_enum, include_details))

        # Sort by reaction_at (most recent first)
        result.sort(key=lambda x: x.reaction_at, reverse=True)

        return UserReactionsResponse(items=result)

    except ExtendedHTTPException:
        # Re-raise ExtendedHTTPException without wrapping
        raise
    except Exception as e:
        logger.error(f"Error retrieving reactions for user {user.id}: {str(e)}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to retrieve reactions",
            details=f"An error occurred while retrieving reactions: {str(e)}",
            help="Please try again later. If the problem persists, contact support.",
        ) from e
