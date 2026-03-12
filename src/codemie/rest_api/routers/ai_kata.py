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

from __future__ import annotations

import json
from typing import List, Optional

from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field as PydanticField, ValidationError

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.ai_kata import (
    AIKataRequest,
    AIKataResponse,
    AIKataPaginatedResponse,
    KataTag,
    KataRole,
    KataLevel,
    KataStatus,
    load_kata_tags,
    load_kata_roles,
)
from codemie.rest_api.models.user_kata_progress import KataProgressStatus
from codemie.rest_api.models.standard import PostResponse
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.core.models import BaseResponse
from codemie.service.ai_kata_service import AIKataService
from codemie.service.kata_user_interaction_service import kata_user_interaction_service
from codemie.service.permission.permission_exceptions import PermissionAccessDenied

# Constants
HELP_CONTACT_SUPPORT = "Please try again later or contact support."
MESSAGE_NOT_AUTHORIZED = "Not authorized"
MESSAGE_KATA_NOT_FOUND = "Kata not found"
HELP_VERIFY_KATA_ID = "Please verify the kata ID and try again."


class ReactionRequest(BaseModel):
    """Request model for kata reactions"""

    reaction: Literal["like", "dislike"]


class KataFilters(BaseModel):
    """Validation model for kata list filters"""

    search: Optional[str] = PydanticField(None, max_length=200)
    level: Optional[KataLevel] = None
    tags: Optional[List[str]] = PydanticField(None, max_length=10)
    roles: Optional[List[str]] = PydanticField(None, max_length=10)
    status: Optional[KataStatus] = None
    author: Optional[str] = PydanticField(None, max_length=100)
    progress_status: Optional[KataProgressStatus] = None


router = APIRouter(
    tags=["AI Katas"],
    prefix="/v1",
    dependencies=[Depends(authenticate)],
)

kata_service = AIKataService()


@router.get("/katas/tags", response_model=List[KataTag])
async def get_kata_tags(
    user: User = Depends(authenticate),
) -> List[KataTag]:
    """
    Get all available kata tags.

    Args:
        user: Authenticated user

    Returns:
        List of available kata tags
    """
    return load_kata_tags()


@router.get("/katas/roles", response_model=List[KataRole])
async def get_kata_roles(
    user: User = Depends(authenticate),
) -> List[KataRole]:
    """
    Get all available kata roles.

    Args:
        user: Authenticated user

    Returns:
        List of available kata roles
    """
    return load_kata_roles()


@router.get("/katas", response_model=AIKataPaginatedResponse)
async def list_katas(
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    filters: Optional[str] = Query(default=None),
    user: User = Depends(authenticate),
) -> AIKataPaginatedResponse:
    """
    List katas with pagination and filtering, including user progress.

    Filters format (JSON encoded string):
    {
        "search": "text to search in title/description",
        "level": "beginner|intermediate|advanced",
        "tags": ["tag1", "tag2"],
        "roles": ["role1", "role2"],
        "status": "draft|published|archived",  # Only admins can filter by draft/archived
        "author": "user_id",
        "progress_status": "not_started|in_progress|completed"  # Filter by user's progress
    }

    Note:
    - Regular users can only see published katas
    - Admins can filter by any status (draft, published, archived)
    - progress_status filters katas based on the current user's enrollment status

    Args:
        page: Page number (1-indexed)
        per_page: Items per page (max 100)
        filters: Optional JSON-encoded filter object
        user: Authenticated user

    Returns:
        Paginated list of katas with user progress info
    """
    # Parse and validate filters
    try:
        raw_filters = json.loads(filters) if filters else {}
        parsed_filters = KataFilters(**raw_filters)
    except json.JSONDecodeError:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid filters",
            details="Filters must be a valid encoded JSON object.",
            help="Please check the filters and ensure they are in the correct format.",
        )
    except ValidationError as e:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid filters",
            details=str(e),
            help="Filters must match the expected schema. Check field types and constraints.",
        )

    # Restrict status filter based on user role
    # Regular users can only see published katas
    # Admins can filter by any status
    if not user.is_admin:
        # If regular user tries to filter by non-published status, reject
        if parsed_filters.status and parsed_filters.status != KataStatus.PUBLISHED:
            raise ExtendedHTTPException(
                code=status.HTTP_403_FORBIDDEN,
                message=MESSAGE_NOT_AUTHORIZED,
                details="Regular users can only view published katas.",
                help="Please remove the status filter or set it to 'published'.",
            )
        # Force published status for regular users if no status filter provided
        parsed_filters.status = KataStatus.PUBLISHED

    return kata_service.list_katas(
        page=page,
        per_page=per_page,
        filters=parsed_filters.model_dump(exclude_none=True),
        user_id=user.id,
        is_admin=user.is_admin,
    )


@router.get("/katas/{kata_id}", response_model=AIKataResponse)
async def get_kata(
    kata_id: str,
    user: User = Depends(authenticate),
) -> AIKataResponse:
    """
    Get kata details by ID with user progress info.

    Args:
        kata_id: ID of the kata
        user: Authenticated user

    Returns:
        Kata details with progress info and filtered steps

    Raises:
        ExtendedHTTPException: If kata not found
    """
    kata = kata_service.get_kata(kata_id, user_id=user.id, is_admin=user.is_admin)
    if not kata:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=MESSAGE_KATA_NOT_FOUND,
            details=f"The kata with ID [{kata_id}] could not be found in the system.",
            help=HELP_VERIFY_KATA_ID,
        )

    return kata


@router.post("/katas", response_model=PostResponse, status_code=status.HTTP_201_CREATED)
async def create_kata(
    request: AIKataRequest,
    user: User = Depends(authenticate),
) -> PostResponse:
    """
    Create a new kata (admin only).

    Args:
        request: Kata creation request
        user: Authenticated user

    Returns:
        Created kata ID

    Raises:
        ExtendedHTTPException: If validation fails or user is not admin
    """
    try:
        kata_id = kata_service.create_kata(request=request, user=user)
        return PostResponse(id=kata_id)
    except PermissionAccessDenied as e:
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=MESSAGE_NOT_AUTHORIZED,
            details=str(e),
            help="Please contact an administrator if you need to create a kata.",
        )


@router.put("/katas/{kata_id}", response_model=BaseResponse)
async def update_kata(
    kata_id: str,
    request: AIKataRequest,
    user: User = Depends(authenticate),
) -> BaseResponse:
    """
    Update an existing kata (admin only).

    Args:
        kata_id: ID of the kata to update
        request: Update request
        user: Authenticated user

    Returns:
        Success message

    Raises:
        ExtendedHTTPException: If kata not found, user not authorized, or validation fails
    """
    try:
        success = kata_service.update_kata(kata_id=kata_id, request=request, user=user)
        if success:
            return BaseResponse(message=f"Kata {kata_id} updated successfully")
        else:
            raise ExtendedHTTPException(
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                message="Update failed",
                details="Failed to update the kata due to an internal error.",
                help=HELP_CONTACT_SUPPORT,
            )
    except PermissionAccessDenied as e:
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=MESSAGE_NOT_AUTHORIZED,
            details=str(e),
            help="Please contact an administrator if you need to update a kata.",
        )


@router.post("/katas/{kata_id}/publish", response_model=BaseResponse)
async def publish_kata(
    kata_id: str,
    user: User = Depends(authenticate),
) -> BaseResponse:
    """
    Publish a kata (admin only).

    Args:
        kata_id: ID of the kata to publish
        user: Authenticated user

    Returns:
        Success message

    Raises:
        ExtendedHTTPException: If kata not found or user not authorized
    """
    try:
        success = kata_service.publish_kata(kata_id=kata_id, user=user)
        if success:
            return BaseResponse(message=f"Kata {kata_id} published successfully")
        else:
            raise ExtendedHTTPException(
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                message="Publish failed",
                details="Failed to publish the kata due to an internal error.",
                help=HELP_CONTACT_SUPPORT,
            )
    except PermissionAccessDenied as e:
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=MESSAGE_NOT_AUTHORIZED,
            details=str(e),
            help="Please contact an administrator if you need to publish a kata.",
        )


@router.post("/katas/{kata_id}/unpublish", response_model=BaseResponse)
async def unpublish_kata(
    kata_id: str,
    user: User = Depends(authenticate),
) -> BaseResponse:
    """
    Unpublish a kata (move from published to draft, admin only).

    Args:
        kata_id: ID of the kata to unpublish
        user: Authenticated user

    Returns:
        Success message

    Raises:
        ExtendedHTTPException: If kata not found or user not authorized
    """
    try:
        success = kata_service.unpublish_kata(kata_id=kata_id, user=user)
        if success:
            return BaseResponse(message=f"Kata {kata_id} unpublished successfully")
        else:
            raise ExtendedHTTPException(
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                message="Unpublish failed",
                details="Failed to unpublish the kata due to an internal error.",
                help=HELP_CONTACT_SUPPORT,
            )
    except PermissionAccessDenied as e:
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=MESSAGE_NOT_AUTHORIZED,
            details=str(e),
            help="Please contact an administrator if you need to unpublish a kata.",
        )


@router.post("/katas/{kata_id}/archive", response_model=BaseResponse)
async def archive_kata(
    kata_id: str,
    user: User = Depends(authenticate),
) -> BaseResponse:
    """
    Archive a kata (admin only).

    Args:
        kata_id: ID of the kata to archive
        user: Authenticated user

    Returns:
        Success message

    Raises:
        ExtendedHTTPException: If kata not found or user not authorized
    """
    try:
        success = kata_service.archive_kata(kata_id=kata_id, user=user)
        if success:
            return BaseResponse(message=f"Kata {kata_id} archived successfully")
        else:
            raise ExtendedHTTPException(
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                message="Archive failed",
                details="Failed to archive the kata due to an internal error.",
                help=HELP_CONTACT_SUPPORT,
            )
    except PermissionAccessDenied as e:
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=MESSAGE_NOT_AUTHORIZED,
            details=str(e),
            help="Please contact an administrator if you need to archive a kata.",
        )


@router.delete("/katas/{kata_id}", response_model=BaseResponse)
async def delete_kata(
    kata_id: str,
    user: User = Depends(authenticate),
) -> BaseResponse:
    """
    Delete a kata (admin only).

    Args:
        kata_id: ID of the kata to delete
        user: Authenticated user

    Returns:
        Success message

    Raises:
        ExtendedHTTPException: If kata not found or user not authorized
    """
    try:
        success = kata_service.delete_kata(kata_id=kata_id, user=user)
        if success:
            return BaseResponse(message=f"Kata {kata_id} deleted successfully")
        else:
            raise ExtendedHTTPException(
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                message="Delete failed",
                details="Failed to delete the kata due to an internal error.",
                help=HELP_CONTACT_SUPPORT,
            )
    except PermissionAccessDenied as e:
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message=MESSAGE_NOT_AUTHORIZED,
            details=str(e),
            help="Please contact an administrator if you need to delete a kata.",
        )


@router.post(
    "/katas/{kata_id}/reactions",
    status_code=status.HTTP_200_OK,
)
def react_to_kata(kata_id: str, request: ReactionRequest, user: User = Depends(authenticate)):
    """
    React to a kata with like or dislike.
    If the user already has the opposite reaction, it will be removed.
    Clicking the same reaction type toggles it off.

    Args:
        kata_id: ID of the kata to react to
        request: Reaction request (like or dislike)
        user: Authenticated user

    Returns:
        Reaction response with updated counts

    Example:
        POST /v1/katas/2bdc8012-f37f-48b1-bb14-32814aa62877/reactions
        {
            "reaction": "like"
        }
    """
    # Check if kata exists
    kata = kata_service.get_kata(kata_id, user_id=user.id, is_admin=user.is_admin)
    if not kata:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=MESSAGE_KATA_NOT_FOUND,
            details=f"The kata with ID [{kata_id}] could not be found in the system.",
            help=HELP_VERIFY_KATA_ID,
        )

    # Use service to handle the reaction
    return kata_user_interaction_service.manage_reaction(kata_id, user, request.reaction)


@router.delete(
    "/katas/{kata_id}/reactions",
    status_code=status.HTTP_200_OK,
)
def remove_kata_reactions(kata_id: str, user: User = Depends(authenticate)):
    """
    Remove all reactions (likes/dislikes) from a kata for the current user.

    Args:
        kata_id: ID of the kata
        user: Authenticated user

    Returns:
        Reaction response with updated counts

    Example:
        DELETE /v1/katas/2bdc8012-f37f-48b1-bb14-32814aa62877/reactions
    """
    # Check if kata exists
    kata = kata_service.get_kata(kata_id, user_id=user.id, is_admin=user.is_admin)
    if not kata:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=MESSAGE_KATA_NOT_FOUND,
            details=f"The kata with ID [{kata_id}] could not be found in the system.",
            help=HELP_VERIFY_KATA_ID,
        )

    return kata_user_interaction_service.remove_reactions(kata_id, user)
