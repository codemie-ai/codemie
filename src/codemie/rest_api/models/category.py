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
Category models for assistant categorization.
"""

from datetime import datetime
from typing import Annotated, List, Optional

from pydantic import AfterValidator, BaseModel, Field, StrictStr
from sqlmodel import Field as SQLField

from codemie.rest_api.models.base import BaseModelWithSQLSupport, CommonBaseModel

# ============================================================================
# Utility Functions
# ============================================================================


class CategoryBase(CommonBaseModel):
    """Base model for Category"""

    name: str = SQLField(index=True, unique=True)
    description: Optional[str] = None


class Category(BaseModelWithSQLSupport, CategoryBase, table=True):
    """SQLModel version of Category for PostgreSQL storage with auto-generated human-readable IDs"""

    __tablename__ = "categories"


# ============================================================================
# Category Selection Types
# ============================================================================

CATEGORY_ID_MAX_LENGTH = 256
MAX_CATEGORIES = 3
TOO_MANY_CATEGORIES = "Maximum 3 categories allowed"
DUPLICATE_CATEGORIES = "category IDs must be unique"
CATEGORY_REQUIRED = "At least one category is required"


CategoryId = Annotated[StrictStr, Field(min_length=1, max_length=CATEGORY_ID_MAX_LENGTH)]


def _selection(ids: list[str]) -> list[str]:
    if len(ids) > MAX_CATEGORIES:
        raise ValueError(TOO_MANY_CATEGORIES)
    if len(set(ids)) != len(ids):
        raise ValueError(DUPLICATE_CATEGORIES)
    return ids


def _required(ids: list[str]) -> list[str]:
    if not ids:
        raise ValueError(CATEGORY_REQUIRED)
    return _selection(ids)


CategoryIds = Annotated[list[CategoryId], AfterValidator(_selection)]
RequiredCategoryIds = Annotated[list[CategoryId], AfterValidator(_required)]


# ============================================================================
# Response Models
# ============================================================================


class CategoryResponse(BaseModel):
    """Response model for category with assistant counts"""

    id: str
    name: str
    description: Optional[str] = None
    marketplace_assistants_count: Optional[int] = Field(default=0, serialization_alias="marketplaceAssistantCount")
    project_assistants_count: Optional[int] = Field(default=0, serialization_alias="projectAssistantCount")
    date: datetime = Field(serialization_alias="createdAt")
    update_date: Optional[datetime] = Field(default=None, serialization_alias="updatedAt")


class CategoryListResponse(BaseModel):
    """Paginated category list response"""

    categories: List[CategoryResponse] = Field(default_factory=list)
    page: int
    per_page: int
    total: int
    pages: int


# ============================================================================
# Request Models
# ============================================================================


class CategoryCreateRequest(BaseModel):
    """Request model for creating a category"""

    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)


class CategoryUpdateRequest(BaseModel):
    """Request model for updating a category"""

    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
