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

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import UserResponse, ProjectInfoResponse
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.user.user_profile_service import user_profile_service


router = APIRouter(
    tags=["user-profile"],
    prefix="/v1/user",
    dependencies=[],  # Auth handled per-endpoint
)


class UserProfileUpdateRequest(BaseModel):
    """Request body for user profile update (local users only)

    Note: Uses EmailStr because local users must have valid email addresses.
    """

    name: Optional[str] = None
    picture: Optional[str] = None
    email: Optional[EmailStr] = None  # EmailStr for local users (valid email required)


@router.put("/profile", response_model=UserResponse)
async def update_profile(data: UserProfileUpdateRequest, user: User = Depends(authenticate)):
    """Update current user's profile

    Local auth mode only.

    Allows user to update their own:
    - name
    - picture
    - email (if email verification is disabled, otherwise requires re-verification)
    """
    # Restrict to local auth mode only
    if config.IDP_PROVIDER != "local":
        raise ExtendedHTTPException(
            code=403, message="Profile update only available in local auth mode. Use IDP to manage profile."
        )

    # Check feature flag
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message="User management not enabled")

    # Delegate to service layer (handles all business logic and email sending)
    updated_user = await user_profile_service.update_profile(
        user_id=user.id, name=data.name, picture=data.picture, email=data.email
    )

    # F-10: Query DB directly for projects (consistent with admin endpoints)
    from codemie.clients.postgres import get_session
    from codemie.repository.user_project_repository import user_project_repository

    with get_session() as session:
        user_projects = user_project_repository.get_by_user_id(session, user.id)
        projects = [
            ProjectInfoResponse(name=p.project_name, is_project_admin=p.is_project_admin) for p in user_projects
        ]

    return UserResponse(
        user_id=updated_user.id,
        name=updated_user.name or "",
        username=updated_user.username,
        email=updated_user.email,
        is_admin=user.is_admin,
        is_maintainer=user.is_maintainer,
        projects=projects,
        picture=updated_user.picture or "",
        knowledge_bases=user.knowledge_bases,
        user_type=updated_user.user_type,
    )
