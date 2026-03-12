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

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.user_management import (
    UserCreateRequest,
    UserUpdateRequest,
    CodeMieUserDetail,
    AdminUserProject,
    AdminUserKnowledgeBase,
    ProjectAccessRequest,
    ProjectAccessUpdateRequest,
    KnowledgeBaseAccessRequest,
    PaginatedUserListResponse,
)
from codemie.rest_api.security.authentication import (
    authenticate,
    admin_access_only,
    project_admin_or_super_admin_user_list_access,
    project_admin_or_super_admin_user_detail_access,
)
from codemie.rest_api.security.user import User
from codemie.service.user.user_management_service import user_management_service
from codemie.service.user.password_management_service import password_management_service
from codemie.service.user.user_access_service import user_access_service


_USER_MGMT_NOT_ENABLED = "User management not enabled"

router = APIRouter(
    tags=["user-management"],
    prefix="/v1/admin/users",
    dependencies=[],  # Auth handled per-endpoint with admin_access_only
)


class MessageResponse(BaseModel):
    """Simple message response"""

    message: str


class AdminPasswordChangeRequest(BaseModel):
    """Request body for admin password change"""

    new_password: str


# ===========================================
# User CRUD Endpoints
# ===========================================


@router.get("", response_model=PaginatedUserListResponse)
def list_users(
    page: int = Query(0, ge=0, description="Page number (0-indexed)"),
    per_page: int = Query(20, description="Items per page (10, 20, 50, or 100)"),
    search: Optional[str] = Query(None, description="Search in email, username, name"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    project_name: Optional[str] = Query(None, description="Filter by project access"),
    user_type: Optional[str] = Query(None, description="Filter by user type ('regular' or 'external')"),
    user: User = Depends(authenticate),
    _: None = Depends(project_admin_or_super_admin_user_list_access),
):
    """List all users with pagination and filters

    SuperAdmin or ProjectAdmin access (Story 17).
    Shows ALL users including deactivated.
    Page is 0-indexed (page=0 is first page).
    Includes projects array for each user (optimized with JOIN query).

    Story 10: Filters personal projects based on visibility rules.
    Story 17: Project admins can access this endpoint to search users for project assignment.
    """
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message=_USER_MGMT_NOT_ENABLED)

    # Validate per_page (Story 7: only 10, 20, 50, 100 allowed)
    if per_page not in [10, 20, 50, 100]:
        raise ExtendedHTTPException(code=400, message="per_page must be one of: 10, 20, 50, 100")

    # Story 10: Pass requesting user context for visibility filtering
    return user_management_service.list_users_with_flow(
        requesting_user_id=user.id,
        is_super_admin=user.is_admin,
        page=page,
        per_page=per_page,
        search=search,
        is_active=is_active,
        project_name=project_name,
        user_type=user_type,
    )


@router.get("/{user_id}", response_model=CodeMieUserDetail)
def get_user(
    user_id: str,
    user: User = Depends(authenticate),
    _: None = Depends(project_admin_or_super_admin_user_detail_access),
):
    """Get user details by ID

    SuperAdmin or ProjectAdmin access (Story 18).

    Story 10: Filters personal projects based on visibility rules.
    Story 18: Project admins can view users in projects they admin (with filtered projects array).
    """
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message=_USER_MGMT_NOT_ENABLED)

    # Story 18: Pass is_project_admin flag to service for response filtering
    # Use is_super_admin explicitly to match service expectations
    is_project_admin = user.is_applications_admin and not user.is_super_admin
    return user_management_service.get_user_detail(user_id, user.id, user.is_super_admin, is_project_admin)


@router.post("", response_model=CodeMieUserDetail)
def create_user(data: UserCreateRequest, user: User = Depends(authenticate), _: None = Depends(admin_access_only)):
    """Create a new local user

    SuperAdmin only.
    Only available in local auth mode.
    """
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message=_USER_MGMT_NOT_ENABLED)

    if config.IDP_PROVIDER != "local":
        raise ExtendedHTTPException(code=400, message="User creation only available in local auth mode")

    # Delegate to service layer
    return user_management_service.create_local_user_with_flow(
        email=data.email,
        username=data.username,
        password=data.password,
        name=data.name,
        is_super_admin=data.is_super_admin,
        actor_user_id=user.id,
    )


@router.put("/{user_id}", response_model=CodeMieUserDetail)
def update_user(
    user_id: str, data: UserUpdateRequest, user: User = Depends(authenticate), _: None = Depends(admin_access_only)
):
    """Update user details

    SuperAdmin only.

    Field editability rules (Story 8):
    - name, picture: Always editable
    - email: Local mode only (IDP mode rejects)
    - username: Immutable (always rejected)
    - user_type: Local mode only, 'regular' or 'external'
    - is_super_admin: Subject to revocation protection
    - project_limit: Subject to validation rules
    """
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message=_USER_MGMT_NOT_ENABLED)

    # Delegate to service layer
    return user_management_service.update_user_fields(
        user_id=user_id,
        actor_user_id=user.id,
        name=data.name,
        picture=data.picture,
        email=data.email,
        username=data.username,
        user_type=data.user_type,
        is_super_admin=data.is_super_admin,
        is_active=data.is_active,
        project_limit=data.project_limit,
        project_limit_provided=data.project_limit_provided,
    )


@router.delete("/{user_id}")
def deactivate_user(user_id: str, user: User = Depends(authenticate), _: None = Depends(admin_access_only)):
    """Deactivate (soft delete) a user

    SuperAdmin only.
    Cannot deactivate the last SuperAdmin.
    """
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message=_USER_MGMT_NOT_ENABLED)

    # Delegate to service layer
    return user_management_service.deactivate_user_flow(user_id, user.id)


@router.put("/{user_id}/password")
def admin_change_password(
    user_id: str,
    data: AdminPasswordChangeRequest,
    user: User = Depends(authenticate),
    _: None = Depends(admin_access_only),
):
    """Change user password (admin override, no current password required)

    SuperAdmin only.
    """
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message=_USER_MGMT_NOT_ENABLED)

    # Delegate to service layer
    return password_management_service.admin_change_password_flow(
        user_id=user_id, new_password=data.new_password, actor_user_id=user.id
    )


# ===========================================
# Project Access Management
# ===========================================


@router.get("/{user_id}/projects")
def get_user_projects(user_id: str, user: User = Depends(authenticate), _: None = Depends(admin_access_only)):
    """Get user's project access

    SuperAdmin only.
    """
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message=_USER_MGMT_NOT_ENABLED)

    # Delegate to service layer
    result = user_access_service.get_user_projects_list(user_id)

    # Convert to response model
    return {"projects": [AdminUserProject(**project) for project in result["projects"]]}


@router.post("/{user_id}/projects")
def add_project_access(
    user_id: str, data: ProjectAccessRequest, user: User = Depends(authenticate), _: None = Depends(admin_access_only)
):
    """Grant project access to user

    SuperAdmin only.
    """
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message=_USER_MGMT_NOT_ENABLED)

    # Delegate to service layer
    return user_access_service.grant_project_access(
        user_id=user_id, project_name=data.project_name, is_project_admin=data.is_project_admin, actor_user_id=user.id
    )


@router.put("/{user_id}/projects/{project_name}")
def update_project_access(
    user_id: str,
    project_name: str,
    data: ProjectAccessUpdateRequest,
    user: User = Depends(authenticate),
    _: None = Depends(admin_access_only),
):
    """Update user's project admin status

    SuperAdmin only.
    """
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message=_USER_MGMT_NOT_ENABLED)

    # Delegate to service layer
    return user_access_service.update_user_project_access(
        user_id=user_id, project_name=project_name, is_project_admin=data.is_project_admin, actor_user_id=user.id
    )


@router.delete("/{user_id}/projects/{project_name}")
def remove_project_access(
    user_id: str, project_name: str, user: User = Depends(authenticate), _: None = Depends(admin_access_only)
):
    """Remove user's project access

    SuperAdmin only.
    """
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message=_USER_MGMT_NOT_ENABLED)

    # Delegate to service layer
    return user_access_service.revoke_project_access(user_id=user_id, project_name=project_name, actor_user_id=user.id)


# ===========================================
# Knowledge Base Access Management
# ===========================================


@router.get("/{user_id}/knowledge-bases")
def get_user_knowledge_bases(user_id: str, user: User = Depends(authenticate), _: None = Depends(admin_access_only)):
    """Get user's knowledge base access

    SuperAdmin only.
    """
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message=_USER_MGMT_NOT_ENABLED)

    # Delegate to service layer
    result = user_access_service.get_user_knowledge_bases_list(user_id)

    # Convert to response model
    return {"knowledge_bases": [AdminUserKnowledgeBase(**kb) for kb in result["knowledge_bases"]]}


@router.post("/{user_id}/knowledge-bases")
def add_knowledge_base_access(
    user_id: str,
    data: KnowledgeBaseAccessRequest,
    user: User = Depends(authenticate),
    _: None = Depends(admin_access_only),
):
    """Grant knowledge base access to user

    SuperAdmin only.
    """
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message=_USER_MGMT_NOT_ENABLED)

    # Delegate to service layer
    return user_access_service.grant_kb_access(user_id=user_id, kb_name=data.kb_name, actor_user_id=user.id)


@router.delete("/{user_id}/knowledge-bases/{kb_name}")
def remove_knowledge_base_access(
    user_id: str, kb_name: str, user: User = Depends(authenticate), _: None = Depends(admin_access_only)
):
    """Remove user's knowledge base access

    SuperAdmin only.
    """
    if not config.ENABLE_USER_MANAGEMENT:
        raise ExtendedHTTPException(code=400, message=_USER_MGMT_NOT_ENABLED)

    # Delegate to service layer
    return user_access_service.revoke_kb_access(user_id=user_id, kb_name=kb_name, actor_user_id=user.id)
