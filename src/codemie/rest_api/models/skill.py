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
Skill models for the Skills system.

Skills are modular knowledge units that provide domain-specific instructions,
best practices, and code examples to AI assistants.
"""

from __future__ import annotations

from datetime import datetime, UTC
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field as SQLField, Column

from codemie.core.ability import Owned
from codemie.core.constants import DEMO_PROJECT
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.base import BaseModelWithSQLSupport, CommonBaseModel, PydanticType, PydanticListType
from codemie.rest_api.security.user import User

# ToolKitDetails and MCPServerDetails are imported here to avoid duplication; assistant.py does not
# import skill.py so there is no circular dependency.
from codemie.rest_api.models.assistant import MCPServerDetails, ToolKitDetails


class SkillVisibility(str, Enum):
    """Visibility levels for skills"""

    PRIVATE = "private"  # Only creator can access
    PROJECT = "project"  # All users in same project can access
    PUBLIC = "public"  # All users can access


class SkillScopeFilter(str, Enum):
    """Scope filters for skill listing"""

    MARKETPLACE = "marketplace"
    PROJECT = "project"
    PROJECT_WITH_MARKETPLACE = "project_with_marketplace"
    # Note: Individual project names are handled dynamically


class MarketplaceFilter(str, Enum):
    """Marketplace filtering mode for skill queries"""

    DEFAULT = "default"  # Normal behavior (include marketplace unless project filter set)
    EXCLUDE = "exclude"  # Exclude marketplace (PUBLIC) skills
    INCLUDE = "include"  # Include marketplace skills even when project filter is set


class SkillSortBy(str, Enum):
    """Sort options for skill listing"""

    CREATED_DATE = "created_date"
    ASSISTANTS_COUNT = "assistants_count"
    RELEVANCE = "relevance"


class SkillCategory(str, Enum):
    """Predefined categories for skills"""

    # Core development categories
    DEVELOPMENT = "development"
    ENGINEERING = "engineering"
    TESTING = "testing"
    QUALITY_ASSURANCE = "quality_assurance"
    CODE_REVIEW = "code_review"
    DOCUMENTATION = "documentation"

    # Operations & infrastructure
    DEVOPS = "devops"
    SECURITY = "security"
    COMPLIANCE = "compliance"
    MONITORING_ALERTS = "monitoring_alerts"

    # Architecture & design
    ARCHITECTURE = "architecture"
    UI_UX_DESIGN = "ui_ux_design"

    # Data & analytics
    DATA_ANALYSIS = "data_analysis"
    DATA_ANALYTICS = "data_analytics"

    # Business & management
    PROJECT_MANAGEMENT = "project_management"
    PRODUCT_MANAGEMENT = "product_management"
    BUSINESS_ANALYSIS = "business_analysis"

    # Migration & modernization
    MIGRATION_MODERNIZATION = "migration_modernization"

    # Support & customer
    SUPPORT = "support"
    CUSTOMER_EXPERIENCE = "customer_experience"

    # Knowledge & training
    KNOWLEDGE_MANAGEMENT = "knowledge_management"
    TRAINING = "training"

    # Sales & presales
    PRESALES = "presales"

    # Other
    INTERVIEW = "interview"
    TALENT_ACQUISITION = "talent_acquisition"
    OTHER = "other"


# =============================================================================
# Constants
# =============================================================================

# Validation constants
MAX_CATEGORIES_PER_SKILL = 3
MAX_CATEGORIES_ERROR_MSG = "Maximum 3 categories allowed per skill"
MAX_CONTENT_LENGTH = 30000


# =============================================================================
# Request Models
# =============================================================================


class SkillCreateRequest(BaseModel):
    """Request model for creating a skill"""

    name: str = Field(
        description="Unique skill identifier (kebab-case)",
        min_length=3,
        max_length=64,
    )
    description: str = Field(
        description="Brief description of when to use this skill",
        min_length=10,
        max_length=1000,
    )
    content: str = Field(
        description="Markdown content (skill instructions)",
        min_length=100,
        max_length=MAX_CONTENT_LENGTH,
    )
    project: str = Field(description="Project this skill belongs to")
    visibility: SkillVisibility = Field(
        default=SkillVisibility.PRIVATE,
        description="Access visibility level",
    )
    categories: list[SkillCategory] = Field(
        default_factory=list,
        description="Categories for categorization (max 3)",
    )
    toolkits: list[ToolKitDetails] = Field(
        default_factory=list,
        description="Optional list of tools required for this skill to execute correctly",
    )
    mcp_servers: list[MCPServerDetails] = Field(
        default_factory=list,
        description="Optional list of MCP servers required for this skill to execute correctly",
    )

    @field_validator("name")
    @classmethod
    def validate_name_format(cls, v: str) -> str:
        """Validate name is kebab-case"""
        import re

        if not re.match(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$", v):
            raise ValueError(
                "Name must be kebab-case (lowercase letters, numbers, and hyphens). "
                "Must start and end with a letter or number."
            )
        return v

    @field_validator("categories")
    @classmethod
    def validate_categories_count(cls, v: list[SkillCategory]) -> list[SkillCategory]:
        """Validate maximum 3 categories"""
        if len(v) > MAX_CATEGORIES_PER_SKILL:
            raise ValueError(MAX_CATEGORIES_ERROR_MSG)
        return v


class SkillUpdateRequest(BaseModel):
    """Request model for updating a skill"""

    name: str | None = Field(default=None, min_length=3, max_length=64)
    description: str | None = Field(default=None, min_length=10, max_length=1000)
    content: str | None = Field(default=None, min_length=100, max_length=MAX_CONTENT_LENGTH)
    project: str | None = Field(default=None, description="Project this skill belongs to")
    visibility: SkillVisibility | None = None
    categories: list[SkillCategory] | None = None
    toolkits: list[ToolKitDetails] | None = Field(
        default=None,
        description="Optional list of tools required for this skill to execute correctly",
    )
    mcp_servers: list[MCPServerDetails] | None = Field(
        default=None,
        description="Optional list of MCP servers required for this skill to execute correctly",
    )

    @field_validator("name")
    @classmethod
    def validate_name_format(cls, v: str | None) -> str | None:
        """Validate name is kebab-case if provided"""
        if v is None:
            return v
        import re

        if not re.match(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$", v):
            raise ValueError(
                "Name must be kebab-case (lowercase letters, numbers, and hyphens). "
                "Must start and end with a letter or number."
            )
        return v

    @field_validator("categories")
    @classmethod
    def validate_categories_count(cls, v: list[SkillCategory] | None) -> list[SkillCategory] | None:
        """Validate maximum 3 categories if provided"""
        if v is not None and len(v) > MAX_CATEGORIES_PER_SKILL:
            raise ValueError(MAX_CATEGORIES_ERROR_MSG)
        return v


class SkillImportRequest(BaseModel):
    """Request model for importing skill from .md file"""

    file_content: str = Field(description="Base64-encoded .md file content")
    filename: str
    project: str = Field(description="Project to import skill into")
    visibility: SkillVisibility = SkillVisibility.PRIVATE


class SkillAttachRequest(BaseModel):
    """Request model for attaching a skill to an assistant"""

    skill_id: str = Field(description="ID of the skill to attach")


class SkillBulkAttachRequest(BaseModel):
    """Request model for attaching a skill to multiple assistants"""

    assistant_ids: list[str] = Field(description="List of assistant IDs to attach skill to", min_length=1)


class PublishToMarketplaceRequest(BaseModel):
    """Request model for publishing a skill to marketplace"""

    categories: list[SkillCategory] | None = Field(
        default=None,
        description="Optional categories to update when publishing",
    )

    @field_validator("categories")
    @classmethod
    def validate_categories_count(cls, v: list[SkillCategory] | None) -> list[SkillCategory] | None:
        """Validate maximum 3 categories if provided"""
        if v is not None and len(v) > MAX_CATEGORIES_PER_SKILL:
            raise ValueError(MAX_CATEGORIES_ERROR_MSG)
        return v


class SkillInstructionsGenerateRequest(BaseModel):
    """Request model for generating skill instructions with AI"""

    description: str | None = Field(
        default=None,
        description="User description/instructions (can be empty for automatic quality review in refine mode)",
        max_length=10000,
    )

    existing_instructions: str | None = Field(
        default=None,
        description="Existing instructions to refine/improve (if provided, triggers refine mode)",
        max_length=MAX_CONTENT_LENGTH,
    )

    skill_name: str | None = Field(
        default=None,
        description="Optional skill name for context",
        max_length=64,
    )

    llm_model: str | None = Field(
        default=None,
        description="Optional LLM model to use (defaults to system default)",
    )

    @field_validator("description")
    @classmethod
    def validate_description_length(cls, v: str | None) -> str | None:
        """Validate description length with custom error message"""
        if v is not None and len(v) > 10000:
            raise ValueError("Prompt must not exceed 10000 characters")
        return v


class SkillInstructionsGenerateResponse(BaseModel):
    """Response model for generated skill instructions"""

    instructions: str = Field(
        ...,
        description="Generated skill instructions in Anthropic Claude-compatible format",
        max_length=MAX_CONTENT_LENGTH,
    )

    metadata: dict = Field(
        default_factory=dict,
        description="Additional metadata about generation (model used, tokens, etc.)",
    )


# =============================================================================
# Response Models
# =============================================================================


class SkillCategoryResponse(BaseModel):
    """Category response model"""

    value: str
    label: str


class SkillBasicInfo(BaseModel):
    """Basic skill information for assistant responses"""

    id: str
    name: str
    description: str


class SkillListResponse(BaseModel):
    """Response model for skill list item"""

    id: str
    name: str
    description: str
    project: str
    visibility: SkillVisibility
    created_by: CreatedByUser | None = None
    categories: list[SkillCategory]
    created_date: datetime = Field(serialization_alias="createdDate")
    updated_date: datetime | None = Field(default=None, serialization_alias="updatedDate")
    is_attached: bool = Field(default=False)
    assistants_count: int = Field(default=0)
    user_abilities: list[str] = Field(default_factory=list)  # ["read", "write", "delete"]
    unique_likes_count: int = Field(default=0)
    unique_dislikes_count: int = Field(default=0)


class SkillDetailResponse(BaseModel):
    """Response model for skill details"""

    id: str
    name: str
    description: str
    content: str
    project: str
    visibility: SkillVisibility
    created_by: CreatedByUser | None = None
    categories: list[SkillCategory]
    created_date: datetime = Field(serialization_alias="createdDate")
    updated_date: datetime | None = Field(default=None, serialization_alias="updatedDate")
    assistants_count: int = Field(default=0)
    user_abilities: list[str] = Field(default_factory=list)  # ["read", "write", "delete"]
    unique_likes_count: int = Field(default=0)
    unique_dislikes_count: int = Field(default=0)
    toolkits: list[ToolKitDetails] = Field(default_factory=list)
    mcp_servers: list[MCPServerDetails] = Field(default_factory=list)


class SkillListPaginatedResponse(BaseModel):
    """Paginated response for skill list"""

    skills: list[SkillListResponse]
    page: int
    per_page: int = Field(serialization_alias="perPage")
    total: int
    pages: int


# =============================================================================
# Database Models
# =============================================================================


class SkillBase(CommonBaseModel, Owned):
    """Base model for Skill"""

    id: str = SQLField(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str = SQLField(index=True, max_length=64)
    description: str = SQLField(max_length=1000)
    content: str  # Markdown content

    # Project and visibility
    project: str = SQLField(default=DEMO_PROJECT, index=True)
    visibility: SkillVisibility = SQLField(
        default=SkillVisibility.PRIVATE,
        index=True,
    )

    # Author information
    created_by: CreatedByUser | None = SQLField(default=None, sa_column=Column(PydanticType(CreatedByUser)))

    # Categories as enum array
    categories: list[str] = SQLField(default_factory=list, sa_column=Column(JSONB))

    # Required tools for this skill (optional)
    toolkits: list[ToolKitDetails] = SQLField(
        default_factory=list,
        sa_column=Column(PydanticListType(ToolKitDetails), nullable=False, server_default="[]"),
    )

    # Required MCP servers for this skill (optional)
    mcp_servers: list[MCPServerDetails] = SQLField(
        default_factory=list,
        sa_column=Column(PydanticListType(MCPServerDetails), nullable=False, server_default="[]"),
    )

    # Reaction counts
    unique_likes_count: int = SQLField(default=0, index=False)
    unique_dislikes_count: int = SQLField(default=0, index=False)

    # Timestamps
    created_date: datetime = SQLField(default_factory=lambda: datetime.now(UTC))
    updated_date: datetime | None = SQLField(default=None)

    __table_args__ = (
        Index("uix_skill_name_author_project", "name", text("(created_by->>'id')"), "project", unique=True),
        Index("ix_skill_name", "name"),
        Index("ix_skill_visibility", "visibility"),
        Index("ix_skill_created_by_id", text("(created_by->>'id')")),
        Index("ix_skill_created_by_name", text("(created_by->>'name') gin_trgm_ops"), postgresql_using="gin"),
        Index("ix_skill_project", "project"),
        Index("ix_skill_categories", "categories", postgresql_using="gin"),
    )

    def is_owned_by(self, user: User) -> bool:
        """Check if skill is owned by user"""
        if not self.created_by:
            return False
        return self.created_by.id == user.id

    def is_managed_by(self, user: User) -> bool:
        """Check if user is manager of skill's project"""
        return self.project in user.admin_project_names

    def is_shared_with(self, user: User) -> bool:
        """
        Check if skill is shared with user based on visibility.

        - PUBLIC: Shared with everyone
        - PROJECT: Shared with project members
        - PRIVATE: Not shared (only owner access)
        """
        if self.visibility == SkillVisibility.PUBLIC:
            return True

        if self.visibility == SkillVisibility.PROJECT:
            return self.project in user.project_names

        # PRIVATE visibility - not shared
        return False


class Skill(BaseModelWithSQLSupport, SkillBase, table=True):
    """SQLModel version of Skill for PostgreSQL storage"""

    __tablename__ = "skills"

    def to_list_response(
        self,
        is_attached: bool = False,
        assistants_count: int = 0,
        user_abilities: list[str] | None = None,
    ) -> SkillListResponse:
        """Convert to list response model"""
        return SkillListResponse(
            id=self.id,
            name=self.name,
            description=self.description,
            project=self.project,
            visibility=self.visibility,
            created_by=self.created_by,
            categories=[SkillCategory(c) for c in self.categories if c in [e.value for e in SkillCategory]],
            created_date=self.created_date,
            updated_date=self.updated_date,
            is_attached=is_attached,
            assistants_count=assistants_count,
            user_abilities=user_abilities or [],
            unique_likes_count=self.unique_likes_count or 0,
            unique_dislikes_count=self.unique_dislikes_count or 0,
        )

    def to_detail_response(
        self,
        assistants_count: int = 0,
        user_abilities: list[str] | None = None,
    ) -> SkillDetailResponse:
        """Convert to detail response model"""
        return SkillDetailResponse(
            id=self.id,
            name=self.name,
            description=self.description,
            content=self.content,
            project=self.project,
            visibility=self.visibility,
            created_by=self.created_by,
            categories=[SkillCategory(c) for c in self.categories if c in [e.value for e in SkillCategory]],
            created_date=self.created_date,
            updated_date=self.updated_date,
            assistants_count=assistants_count,
            user_abilities=user_abilities or [],
            unique_likes_count=self.unique_likes_count or 0,
            unique_dislikes_count=self.unique_dislikes_count or 0,
            toolkits=self.toolkits or [],
            mcp_servers=self.mcp_servers or [],
        )

    def to_basic_info(self) -> SkillBasicInfo:
        """Convert to basic info response model (for assistant responses)"""
        return SkillBasicInfo(
            id=self.id,
            name=self.name,
            description=self.description,
        )
