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

"""
REST API endpoints for Skills management.
"""

import json

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel

from codemie.rest_api.models.skill import (
    MarketplaceFilter,
    PublishToMarketplaceRequest,
    SkillAttachRequest,
    SkillBulkAttachRequest,
    SkillCategory,
    SkillCreateRequest,
    SkillDetailResponse,
    SkillImportRequest,
    SkillInstructionsGenerateRequest,
    SkillInstructionsGenerateResponse,
    SkillListPaginatedResponse,
    SkillListResponse,
    SkillScopeFilter,
    SkillSortBy,
    SkillUpdateRequest,
    SkillVisibility,
)
from codemie.rest_api.models.assistant import AssistantListResponse
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.rest_api.models.usage.assistant_user_interaction import ReactionType
from codemie.service.skill_service import SkillService
from codemie.service.skill_user_interaction_service import skill_user_interaction_service
from codemie.core.models import BaseResponse, CreatedByUser
from codemie.core.exceptions import ExtendedHTTPException

router = APIRouter(
    tags=["Skills"],
    prefix="/v1",
    dependencies=[],
)


class ReactionRequest(BaseModel):
    """Request model for skill reactions"""

    reaction: ReactionType


# =============================================================================
# Helper Functions
# =============================================================================


def _parse_filters(filters: str | None) -> dict:
    """
    Parse JSON filters parameter.

    Args:
        filters: JSON-encoded filter string

    Returns:
        Parsed filters dict

    Raises:
        ExtendedHTTPException: If filters are invalid JSON
    """
    try:
        return json.loads(filters) if filters else {}
    except json.JSONDecodeError:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid filters",
            details="Filters must be a valid encoded JSON object.",
            help="Please check the filters and ensure they are in the correct format.",
        )


def _parse_scope_filter(scope_value: str | None, user: User) -> tuple[str | None, MarketplaceFilter, str | None]:
    """
    Parse scope filter and derive project/visibility/marketplace settings.

    Args:
        scope_value: Scope filter value from request
        user: Current user

    Returns:
        Tuple of (project, marketplace_filter, visibility_str)
    """
    project = None
    marketplace_filter = MarketplaceFilter.DEFAULT
    visibility_str = None

    if scope_value == SkillScopeFilter.MARKETPLACE.value:
        # Show only marketplace (PUBLIC) skills
        visibility_str = SkillVisibility.PUBLIC.value
    elif scope_value == SkillScopeFilter.PROJECT.value:
        # Show only non-marketplace skills (PROJECT and PRIVATE)
        marketplace_filter = MarketplaceFilter.EXCLUDE
    elif scope_value == SkillScopeFilter.PROJECT_WITH_MARKETPLACE.value:
        # Show both project-specific and marketplace (PUBLIC) skills
        marketplace_filter = MarketplaceFilter.INCLUDE
    elif scope_value and scope_value in user.project_names:
        # Filter by specific project name
        project = scope_value

    return project, marketplace_filter, visibility_str


def _parse_visibility(visibility_str: str | None) -> SkillVisibility | None:
    """
    Convert visibility string to enum.

    Args:
        visibility_str: Visibility string from request

    Returns:
        SkillVisibility enum or None

    Raises:
        ExtendedHTTPException: If visibility value is invalid
    """
    if not visibility_str:
        return None

    try:
        return SkillVisibility(visibility_str)
    except ValueError:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid visibility value",
            details=f"Visibility must be one of: {', '.join([v.value for v in SkillVisibility])}",
            help="Check the visibility filter value",
        )


def _parse_categories(categories_str: list | None) -> list[SkillCategory] | None:
    """
    Convert category strings to enums.

    Args:
        categories_str: List of category strings from request

    Returns:
        List of SkillCategory enums or None

    Raises:
        ExtendedHTTPException: If categories format or values are invalid
    """
    if not categories_str:
        return None

    if not isinstance(categories_str, list):
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid categories format",
            details="Categories must be an array of strings",
            help="Check the categories filter format",
        )

    try:
        return [SkillCategory(cat) for cat in categories_str]
    except ValueError as e:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid category value",
            details=f"Invalid category in list: {str(e)}",
            help=f"Categories must be one of: {', '.join([c.value for c in SkillCategory])}",
        )


# =============================================================================
# Skill CRUD Endpoints
# =============================================================================


@router.get(
    "/skills",
    status_code=status.HTTP_200_OK,
    response_model=SkillListPaginatedResponse,
    response_model_by_alias=True,
)
def list_skills(
    user: User = Depends(authenticate),
    filters: str | None = Query(None, description="JSON-encoded filter object"),
    assistant_id: str | None = Query(None, description="Mark skills attached to this assistant"),
    page: int = Query(0, ge=0, description="Page number (0-indexed)"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_by: SkillSortBy | None = Query(
        None, description="Sort by field ('created_date', 'assistants_count', or 'relevance')"
    ),
):
    """
    List skills accessible to the current user.

    Returns skills that the user can access based on visibility rules:
    - Own skills (any visibility)
    - Project skills where user has project access
    - Public skills

    Supports filtering via the 'filters' parameter (JSON object):
    - scope: Filter by access scope:
      - "marketplace": Only PUBLIC skills
      - "project": Only non-marketplace skills (PROJECT and PRIVATE)
      - "project_with_marketplace": Both project-specific AND marketplace skills
        (requires 'project' filter)
      - <project-name>: Specific project skills (excludes PUBLIC)
    - project: Filter by project name (string or array). Required when scope="project_with_marketplace"
    - visibility: Filter by visibility level (private, project, public)
    - categories: Filter by categories (array of category values)
    - search: Search by skill name (case-insensitive)
    - created_by: Filter by creator user name

    Supports sorting via 'sort_by' parameter:
    - assistants_count: Sort by number of assistants using the skill (descending)
    - created_date: Sort by creation date (descending)
    - relevance: Sort by relevance with context-aware 4-priority ranking

      WITHOUT project filter (chat with assistant):
        1. User's own non-marketplace skills (all projects)
        2. User's own marketplace skills (PUBLIC)
        3. Other users' non-marketplace skills (all accessible projects)
        4. Other users' marketplace skills (PUBLIC)
      Example: ?assistant_id=X&sort_by=relevance

      WITH project filter (assistant editing):
        1. User's own non-marketplace skills (all projects)
        2. User's own marketplace skills (PUBLIC)
        3. Other users' non-marketplace skills from specified project only
        4. Other users' marketplace skills (PUBLIC)
      Example: ?filters={"project":"demo","scope":"project_with_marketplace"}&sort_by=relevance
    - Default: relevance when assistant_id OR single project provided, otherwise
      created_date (non-marketplace) or assistants_count (marketplace)
    """
    # Parse filters parameter
    parsed_filters = _parse_filters(filters)

    # Extract filter values
    scope_value = parsed_filters.get("scope")
    project_filter = parsed_filters.get("project")  # Explicit project filter (string or list)
    visibility_str = parsed_filters.get("visibility")
    categories_str = parsed_filters.get("categories")
    search_query = parsed_filters.get("search")
    created_by = parsed_filters.get("created_by")

    # Parse scope filter (handles project, marketplace, exclusions)
    project, marketplace_filter, derived_visibility = _parse_scope_filter(scope_value, user)

    # If explicit project is provided, override scope-derived project
    # Normalize to list[str] for consistent handling (supports both single string and array)
    if project_filter:
        project = [p for p in project_filter if p] if isinstance(project_filter, list) else [project_filter]
    elif project:
        # scope-derived project is always a single string, normalize to list
        project = [project]

    # Validate: project_with_marketplace scope requires a project filter
    if marketplace_filter == MarketplaceFilter.INCLUDE and not project:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid scope configuration",
            details="The 'project_with_marketplace' scope requires a 'project' filter to be specified",
            help="Add a 'project' filter with at least one project name",
        )

    # Use derived visibility if no explicit visibility provided
    if not visibility_str and derived_visibility:
        visibility_str = derived_visibility

    # Convert visibility string to enum
    visibility = _parse_visibility(visibility_str)

    # Convert categories strings to enums
    categories = _parse_categories(categories_str)

    # No validation needed - backend will handle sorting based on available context

    # Auto-set default sort_by based on scope/visibility
    if sort_by is None:
        is_marketplace_view = scope_value == SkillScopeFilter.MARKETPLACE.value or visibility == SkillVisibility.PUBLIC
        sort_by = SkillSortBy.ASSISTANTS_COUNT if is_marketplace_view else SkillSortBy.CREATED_DATE

    return SkillService.list_skills(
        user=user,
        project=project,
        visibility=visibility,
        categories=categories,
        search_query=search_query,
        created_by=created_by,
        page=page,
        per_page=per_page,
        assistant_id=assistant_id,
        marketplace_filter=marketplace_filter,
        sort_by=sort_by,
    )


@router.get(
    "/skills/categories",
    status_code=status.HTTP_200_OK,
)
def list_skill_categories(
    user: User = Depends(authenticate),
):
    """
    List all available skill categories.
    """
    return [{"value": c.value, "label": c.value.replace("_", " ").title()} for c in SkillCategory]


@router.get(
    "/skills/users",
    status_code=status.HTTP_200_OK,
    response_model=list[CreatedByUser],
)
def get_skill_users(
    user: User = Depends(authenticate),
):
    """
    Returns list of users who created skills accessible to the current user.

    Access is based on visibility rules:
    - Own skills (any visibility)
    - Project skills where user has project access
    - Public skills
    """
    return SkillService.get_skill_users(user)


@router.get(
    "/skills/{skill_id}",
    status_code=status.HTTP_200_OK,
    response_model=SkillDetailResponse,
    response_model_by_alias=True,
)
def get_skill_by_id(
    skill_id: str,
    user: User = Depends(authenticate),
):
    """
    Get skill details by ID.

    Returns full skill details including content, if user has read access.
    """
    return SkillService.get_skill_by_id(skill_id, user)


@router.post(
    "/skills",
    status_code=status.HTTP_201_CREATED,
    response_model=SkillDetailResponse,
    response_model_by_alias=True,
)
def create_skill(
    request: SkillCreateRequest,
    user: User = Depends(authenticate),
):
    """
    Create a new skill.

    The skill name must be unique per user per project (case-insensitive).
    Name must be kebab-case (lowercase letters, numbers, and hyphens).
    """
    return SkillService.create_skill(request, user)


@router.put(
    "/skills/{skill_id}",
    status_code=status.HTTP_200_OK,
    response_model=SkillDetailResponse,
    response_model_by_alias=True,
)
def update_skill(
    skill_id: str,
    request: SkillUpdateRequest,
    user: User = Depends(authenticate),
):
    """
    Update an existing skill.

    Only the skill owner can update. Content changes auto-increment the version.
    """
    return SkillService.update_skill(skill_id, request, user)


@router.delete(
    "/skills/{skill_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_skill(
    skill_id: str,
    user: User = Depends(authenticate),
):
    """
    Delete a skill.

    Only the skill owner can delete. This also removes the skill from any
    assistants that reference it.
    """
    SkillService.delete_skill(skill_id, user)
    return None


# =============================================================================
# Import/Export Endpoints
# =============================================================================


@router.post(
    "/skills/import",
    status_code=status.HTTP_201_CREATED,
    response_model=SkillDetailResponse,
    response_model_by_alias=True,
)
def import_skill(
    request: SkillImportRequest,
    user: User = Depends(authenticate),
):
    """
    Import a skill from a .md file.

    The file must have YAML frontmatter with 'name' and 'description' fields.
    The rest of the file becomes the skill content.

    Example file format:
    ```
    ---
    name: my-skill
    description: Description of when to use this skill
    ---

    # Skill Content

    Your markdown instructions here...
    ```
    """
    return SkillService.import_skill(
        file_content=request.file_content,
        filename=request.filename,
        project=request.project,
        user=user,
        visibility=request.visibility,
    )


@router.get(
    "/skills/{skill_id}/export",
    status_code=status.HTTP_200_OK,
)
def export_skill(
    skill_id: str,
    user: User = Depends(authenticate),
):
    """
    Export a skill as a .md file.

    Returns the skill in markdown format with YAML frontmatter containing
    only the name and description.
    """
    result = SkillService.export_skill(skill_id, user)

    return Response(
        content=result.content,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="{result.filename}"',
        },
    )


# =============================================================================
# Assistant-Skill Association Endpoints
# =============================================================================


@router.get(
    "/assistants/{assistant_id}/skills",
    status_code=status.HTTP_200_OK,
    response_model=list[SkillListResponse],
    response_model_by_alias=True,
)
def get_assistant_skills(
    assistant_id: str,
    user: User = Depends(authenticate),
):
    """
    List all skills attached to an assistant.

    Returns skills that the user has read access to.
    """
    return SkillService.get_skills_for_assistant(assistant_id, user)


@router.get(
    "/skills/{skill_id}/assistants",
    status_code=status.HTTP_200_OK,
    response_model=list[AssistantListResponse],
)
def get_skill_assistants(
    skill_id: str,
    user: User = Depends(authenticate),
):
    """
    List all assistants that use the specified skill.

    Returns assistants that:
    - Have the skill attached to them (skill_id in their skill_ids array)
    - The user has read access to

    This is the inverse of GET /assistants/{assistant_id}/skills.
    """
    return SkillService.get_assistants_for_skill(skill_id, user)


@router.post(
    "/assistants/{assistant_id}/skills",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
)
def attach_skill_to_assistant(
    assistant_id: str,
    request: SkillAttachRequest,
    user: User = Depends(authenticate),
):
    """
    Attach a skill to an assistant.

    The user must own the assistant and have read access to the skill.
    """
    SkillService.attach_skill_to_assistant(assistant_id, request.skill_id, user)
    return BaseResponse(message="Skill attached successfully")


@router.post(
    "/skills/{skill_id}/assistants/bulk-attach",
    status_code=status.HTTP_200_OK,
)
def bulk_attach_skill_to_assistants(
    skill_id: str,
    request: SkillBulkAttachRequest,
    user: User = Depends(authenticate),
):
    """
    Attach a skill to multiple assistants in bulk.

    The user must have read access to the skill and own all target assistants.

    Returns summary of operation with success count and list of any failures.
    """
    result = SkillService.bulk_attach_skill_to_assistants(skill_id, request.assistant_ids, user)

    return {
        "message": f"Skill attached to {result['success_count']} of {result['total_requested']} assistants",
        "success_count": result["success_count"],
        "total_requested": result["total_requested"],
        "failures": result["failures"],
    }


@router.delete(
    "/assistants/{assistant_id}/skills/{skill_id}",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
)
def detach_skill_from_assistant(
    assistant_id: str,
    skill_id: str,
    user: User = Depends(authenticate),
):
    """
    Detach a skill from an assistant.

    The user must own the assistant.
    """
    SkillService.detach_skill_from_assistant(assistant_id, skill_id, user)
    return BaseResponse(message="Skill detached successfully")


# =============================================================================
# Reaction Endpoints
# =============================================================================


@router.post(
    "/skills/{skill_id}/reactions",
    status_code=status.HTTP_200_OK,
)
def react_to_skill(skill_id: str, request: ReactionRequest, user: User = Depends(authenticate)):
    """
    React to a skill with like or dislike.
    If the user already has the opposite reaction, it will be removed.
    """
    # Check if skill exists and user has read access
    SkillService.get_skill_by_id(skill_id, user)

    # Use service to handle the reaction
    return skill_user_interaction_service.manage_reaction(skill_id, user.id, request.reaction)


@router.delete(
    "/skills/{skill_id}/reactions",
    status_code=status.HTTP_200_OK,
)
def remove_skill_reactions(skill_id: str, user: User = Depends(authenticate)):
    """
    Remove all reactions (likes/dislikes) from a skill for the current user.
    """
    # Check if skill exists and user has read access
    SkillService.get_skill_by_id(skill_id, user)

    return skill_user_interaction_service.remove_reactions(skill_id, user.id)


# =============================================================================
# Marketplace Endpoints
# =============================================================================


@router.post(
    "/skills/{skill_id}/marketplace/publish",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
)
def publish_skill_to_marketplace(
    skill_id: str,
    request: PublishToMarketplaceRequest | None = None,
    user: User = Depends(authenticate),
):
    """
    Publish a skill to the marketplace.

    This makes the skill globally accessible (is_global=True) to all users.
    Only the skill owner or administrators can publish skills.

    Optionally, categories can be updated during publishing.
    """
    categories = request.categories if request and request.categories else None

    SkillService.publish_to_marketplace(skill_id, user, categories)
    return BaseResponse(message=f"Skill {skill_id} published to marketplace successfully")


@router.post(
    "/skills/{skill_id}/marketplace/unpublish",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
)
def unpublish_skill_from_marketplace(
    skill_id: str,
    user: User = Depends(authenticate),
):
    """
    Unpublish a skill from the marketplace.

    This removes global access (is_global=False) from the skill.
    Only the skill owner or administrators can unpublish skills.
    """
    SkillService.unpublish_from_marketplace(skill_id, user)
    return BaseResponse(message=f"Skill {skill_id} unpublished from marketplace successfully")


# =============================================================================
# AI Generation Endpoints
# =============================================================================


@router.post(
    "/skills/instructions/generate",
    status_code=status.HTTP_200_OK,
    response_model=SkillInstructionsGenerateResponse,
)
def generate_skill_instructions(
    raw_request: Request,
    request: SkillInstructionsGenerateRequest,
    user: User = Depends(authenticate),
):
    """
    Generate skill instructions from user description.
    Returns comprehensive instructions in Anthropic Claude-compatible format.
    Automatically refines existing instructions if provided.
    """
    try:
        request_id = raw_request.state.uuid

        from codemie.configs.logger import set_logging_info

        set_logging_info(uuid=request_id, user_id=user.id, user_email=user.username)

        # Set LiteLLM context with user's credentials
        from codemie.service.llm_service.utils import set_llm_context

        set_llm_context(project_name=user.current_project, user_id=user.id)

        result = SkillService.generate_instructions(
            description=request.description,
            user=user,
            existing_content=request.existing_content,
            skill_name=request.skill_name,
            llm_model=request.llm_model,
            request_id=request_id,
        )

        return result

    except Exception as e:
        from codemie.configs import logger

        logger.error(f"Failed to generate skill instructions: {str(e)}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to generate instructions",
            details=f"An error occurred while generating instructions: {str(e)}",
            help="Try refining your description or using a different model.",
        )
