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

from datetime import datetime
from enum import StrEnum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy import UniqueConstraint
from sqlmodel import Field as SQLField, SQLModel

from codemie.configs import config
from codemie.rest_api.models.base import BaseModelWithSQLSupport

_USERS_ID_FK = "users.id"
_USERS_EMAIL_FK = "users.email"


# ===========================================
# Database Models (SQLModel tables)
# ===========================================


class UserDB(BaseModelWithSQLSupport, table=True):
    """Persistent user storage"""

    __tablename__ = "users"

    id: str = SQLField(primary_key=True, max_length=36)  # IDP JWT 'sub' claim (UUID format)
    username: str = SQLField(unique=True, index=True, nullable=False)
    email: str = SQLField(unique=True, index=True, nullable=False)
    name: Optional[str] = SQLField(default=None)
    password_hash: Optional[str] = SQLField(default=None)  # NULL for IDP users
    picture: Optional[str] = SQLField(default=None)
    user_type: str = SQLField(default="regular")  # 'regular' | 'external'
    is_active: bool = SQLField(default=True, index=True)
    is_admin: bool = SQLField(default=False, index=True)
    is_maintainer: bool = SQLField(default=False)
    auth_source: str = SQLField(default="local")  # 'local' | 'keycloak' | 'oidc'
    email_verified: bool = SQLField(default=False)
    last_login_at: Optional[datetime] = SQLField(default=None)
    project_limit: Optional[int] = SQLField(default=None)  # Max shared projects; NULL = unlimited (admins)
    # Using date/update_date inherited from CommonBaseModel (no created_at/updated_at)
    deleted_at: Optional[datetime] = SQLField(default=None, index=True)


class UserProject(BaseModelWithSQLSupport, table=True):
    """User-to-project access mapping"""

    __tablename__ = "user_projects"

    id: str = SQLField(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = SQLField(foreign_key=_USERS_ID_FK, index=True, nullable=False)
    project_name: str = SQLField(index=True, nullable=False)
    is_project_admin: bool = SQLField(default=False)
    # Using date inherited from CommonBaseModel (no created_at)

    __table_args__ = (UniqueConstraint('user_id', 'project_name', name='uix_user_project'),)


class UserKnowledgeBase(BaseModelWithSQLSupport, table=True):
    """User-to-knowledge-base access mapping"""

    __tablename__ = "user_knowledge_bases"

    id: str = SQLField(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = SQLField(foreign_key=_USERS_ID_FK, index=True, nullable=False)
    kb_name: str = SQLField(index=True, nullable=False)
    # Using date inherited from CommonBaseModel (no created_at)

    __table_args__ = (UniqueConstraint('user_id', 'kb_name', name='uix_user_kb'),)


class EmailVerificationToken(BaseModelWithSQLSupport, table=True):
    """Email verification and password reset tokens"""

    __tablename__ = "email_verification_tokens"

    id: str = SQLField(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = SQLField(foreign_key=_USERS_ID_FK, index=True, nullable=False)
    token_hash: str = SQLField(unique=True, index=True, nullable=False)
    email: str = SQLField(nullable=False)
    token_type: str = SQLField(default="email_verification")  # 'email_verification' | 'password_reset'
    expires_at: datetime = SQLField(index=True, nullable=False)
    used_at: Optional[datetime] = SQLField(default=None)
    # Using date inherited from CommonBaseModel (no created_at)


class UserEnrichment(SQLModel, table=True):
    """
    Enriched user data synced for EPAM users populated by an external codemie-epam-sync service.
    All enrichment fields are optional.
    """

    __tablename__ = "user_enrichment"
    __table_args__ = {"schema": "codemie"}

    email: str = SQLField(primary_key=True, foreign_key="codemie.users.email")
    user_id: str = SQLField(foreign_key="codemie.users.id", nullable=False, index=True)
    first_name: Optional[str] = SQLField(default=None)
    last_name: Optional[str] = SQLField(default=None)
    job_title: Optional[str] = SQLField(default=None, index=True)
    job_function: Optional[str] = SQLField(default=None)
    level: Optional[str] = SQLField(default=None)
    primary_skill: Optional[str] = SQLField(default=None, index=True)
    country: Optional[str] = SQLField(default=None, index=True)
    city: Optional[str] = SQLField(default=None, index=True)
    synced_at: Optional[datetime] = SQLField(default=None)


# ===========================================
# Request/Response DTOs (Pydantic models)
# ===========================================


class RegistrationRequest(BaseModel):
    """Registration request body"""

    email: EmailStr
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=config.PASSWORD_MIN_LENGTH)
    name: Optional[str] = None


class LoginRequest(BaseModel):
    """Login request body

    Note: email is str (not EmailStr) to support IDP users who may have
    non-email identifiers in their email field (e.g., usernames, IDP subjects).
    """

    email: str
    password: str


class LoginResponse(BaseModel):
    """Login response with token"""

    access_token: str
    user: CodeMieUserDetail


class PasswordResetRequest(BaseModel):
    """Password reset request body"""

    token: str
    new_password: str = Field(min_length=config.PASSWORD_MIN_LENGTH)


class PasswordChangeRequest(BaseModel):
    """Password change request body (for authenticated users)"""

    current_password: str
    new_password: str = Field(min_length=config.PASSWORD_MIN_LENGTH)


class ForgotPasswordRequest(BaseModel):
    """Forgot password request body"""

    email: str


class VerifyEmailRequest(BaseModel):
    """Email verification request body"""

    token: str


class UserCreateRequest(BaseModel):
    """Admin user creation request"""

    email: EmailStr
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=config.PASSWORD_MIN_LENGTH)
    name: Optional[str] = None
    is_admin: bool = False
    is_maintainer: bool = False


class UserUpdateRequest(BaseModel):
    """Admin user update request

    Note: email is str (not EmailStr) because IDP users may have
    non-email identifiers as their email field.
    project_limit: None for unlimited (admins), integers for limits (Story 6)

    Field Editability (Story 8):
    - username: Immutable - cannot be changed (will be rejected if provided)
    - email: Conditional - editable only in local mode (IDP mode: rejected)
    - user_type: Conditional - editable only in local mode (IDP mode: rejected)

    F-15: project_limit uses sentinel to distinguish omitted from explicit null.
    - Omitted: project_limit_provided=False, project_limit=None
    - Explicit null: project_limit_provided=True, project_limit=None
    - Explicit value: project_limit_provided=True, project_limit=<int>
    """

    name: Optional[str] = None
    picture: Optional[str] = None
    email: Optional[str] = None  # str for IDP identifiers (not EmailStr); Story 8: local mode only
    username: Optional[str] = None  # Story 8: Immutable - always rejected if provided
    user_type: Optional[str] = None  # Story 8: 'regular' or 'external'; local mode only
    is_admin: Optional[bool] = None
    is_maintainer: Optional[bool] = None
    is_active: Optional[bool] = None  # See Task 18 for deactivation semantics
    project_limit: Optional[int] = None  # Max shared projects; NULL = unlimited (admins only)
    project_limit_provided: bool = Field(default=False, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def detect_project_limit_presence(cls, data):
        """F-15: Detect whether project_limit was explicitly present in the request body."""
        if isinstance(data, dict) and "project_limit" in data:
            data["project_limit_provided"] = True
        return data


class ProfileUpdateRequest(BaseModel):
    """User profile update request (self-service)"""

    name: Optional[str] = None
    picture: Optional[str] = None
    email: Optional[EmailStr] = None


class ProjectInfo(BaseModel):
    """Project access information for user responses"""

    name: str
    is_project_admin: bool


class CodeMieUserDetail(BaseModel):
    """CodeMie view of user details (excludes sensitive fields)"""

    id: str
    username: str
    email: str
    name: Optional[str]
    picture: Optional[str]
    user_type: str
    is_active: bool
    is_admin: bool
    is_maintainer: bool = False
    auth_source: str
    email_verified: bool
    last_login_at: Optional[datetime]
    projects: list[ProjectInfo] = Field(default_factory=list)
    project_limit: Optional[int] = None
    knowledge_bases: list[str] = Field(default_factory=list)
    date: Optional[datetime]  # Creation timestamp (from CommonBaseModel)
    update_date: Optional[datetime]  # Last update timestamp (from CommonBaseModel)
    deleted_at: Optional[datetime]


class UserBudgetAssignmentInfo(BaseModel):
    """Budget assignment summary for user list view."""

    category: str
    budget_id: str
    budget_name: Optional[str] = None
    max_budget: Optional[float] = None
    budget_duration: Optional[str] = None
    budget_reset_at: Optional[str] = None


class AdminUserListItem(BaseModel):
    """Subset of user fields for list view"""

    id: str
    username: str
    email: str
    name: Optional[str]
    user_type: str
    is_active: bool
    is_admin: bool
    is_maintainer: bool = False
    auth_source: str
    last_login_at: Optional[datetime]
    projects: list[ProjectInfo] = Field(default_factory=list)
    budget_assignments: list[UserBudgetAssignmentInfo] = Field(default_factory=list)
    date: Optional[datetime]  # Creation timestamp (from CommonBaseModel)


class AdminUserProject(BaseModel):
    """Project access details for admin view"""

    project_name: str
    is_project_admin: bool
    date: Optional[datetime]  # Creation timestamp (from CommonBaseModel)


class AdminUserKnowledgeBase(BaseModel):
    """Knowledge base access details for admin view"""

    kb_name: str
    date: Optional[datetime]  # Creation timestamp (from CommonBaseModel)


class ProjectAccessRequest(BaseModel):
    """Request to grant project access"""

    project_name: str
    is_project_admin: bool = False


class ProjectAccessUpdateRequest(BaseModel):
    """Request to update project admin status"""

    is_project_admin: bool


class KnowledgeBaseAccessRequest(BaseModel):
    """Request to grant knowledge base access"""

    kb_name: str


class PlatformRole(StrEnum):
    USER = "user"
    PLATFORM_ADMIN = "platform_admin"
    ADMIN = "admin"


class UserListFilters(BaseModel):
    """Parsed filters for GET /users list endpoint."""

    projects: Optional[list[str]] = None
    budgets: Optional[list[str]] = None
    user_type: Optional[str] = None
    is_active: Optional[bool] = None
    platform_role: Optional[PlatformRole] = None


class PaginatedUserListResponse(BaseModel):
    """Paginated user list response"""

    data: list[AdminUserListItem]
    pagination: "PaginationInfo"


class PaginationInfo(BaseModel):
    """Pagination metadata (unchanged from Phase 1)"""

    total: int
    page: int
    per_page: int


# Forward reference resolution
LoginResponse.model_rebuild()
PaginatedUserListResponse.model_rebuild()
