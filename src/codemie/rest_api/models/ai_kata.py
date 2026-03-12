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
from enum import Enum
from typing import Optional, List, TYPE_CHECKING

import yaml
from pydantic import BaseModel, Field as PydanticField
from sqlalchemy import Column, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableList
from sqlmodel import Field

from codemie.configs import config
from codemie.rest_api.models.base import BaseModelWithSQLSupport, PydanticListType, PaginatedListResponse

if TYPE_CHECKING:
    from codemie.rest_api.models.user_kata_progress import UserKataProgressResponse


class KataLevel(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class KataStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class KataLink(BaseModel):
    title: str
    url: str
    type: str


class AIKata(BaseModelWithSQLSupport, table=True):
    __tablename__ = "ai_katas"

    title: str = Field(max_length=200, index=True)
    description: str = Field(max_length=1000)
    steps: str = Field(sa_column=Column(Text))
    level: KataLevel = Field(default=KataLevel.BEGINNER)
    creator_id: str = Field(index=True)
    creator_name: Optional[str] = Field(default=None, max_length=200)
    creator_username: Optional[str] = Field(default=None, max_length=200)
    duration_minutes: int = Field(default=15, ge=5, le=240)
    tags: List[str] = Field(default_factory=list, sa_column=Column(MutableList.as_mutable(JSONB)))
    roles: List[str] = Field(default_factory=list, sa_column=Column(MutableList.as_mutable(JSONB)))
    links: Optional[List[KataLink]] = Field(default_factory=list, sa_column=Column(PydanticListType(KataLink)))
    references: Optional[List[str]] = Field(default_factory=list, sa_column=Column(MutableList.as_mutable(JSONB)))
    status: KataStatus = Field(default=KataStatus.DRAFT, index=True)
    image_url: Optional[str] = Field(default=None, max_length=500)

    # Version tracking for system katas
    version: Optional[str] = Field(default=None, max_length=50)  # Semver from kata.yaml (e.g., "1.0.0")
    content_checksum: Optional[str] = Field(default=None, max_length=64, index=True)  # SHA256 of content

    # Denormalized counters for performance (updated by user_kata_progress actions)
    enrollment_count: int = Field(default=0, ge=0, index=True)  # Total enrolled users (in_progress + completed)
    completed_count: int = Field(default=0, ge=0, index=True)  # Total users who completed
    unique_likes_count: int = Field(default=0, ge=0, index=True)  # Total likes
    unique_dislikes_count: int = Field(default=0, ge=0, index=True)  # Total dislikes


class AIKataRequest(BaseModel):
    title: str = PydanticField(min_length=1, max_length=200)
    description: str = PydanticField(min_length=1, max_length=1000)
    steps: str = PydanticField(min_length=1)
    level: KataLevel = KataLevel.BEGINNER
    duration_minutes: int = PydanticField(ge=5, le=240, default=15)
    tags: List[str] = PydanticField(default_factory=list, max_length=10)
    roles: List[str] = PydanticField(default_factory=list, max_length=10)
    links: Optional[List[KataLink]] = None
    references: Optional[List[str]] = None
    image_url: Optional[str] = PydanticField(default=None, max_length=500)


class AIKataResponse(BaseModel):
    id: str
    title: str
    description: str
    steps: str
    level: KataLevel
    creator_id: str
    creator_name: Optional[str]
    creator_username: Optional[str]
    duration_minutes: int
    tags: List[str]
    roles: List[str]
    links: Optional[List[KataLink]]
    references: Optional[List[str]]
    status: KataStatus
    date: datetime
    update_date: Optional[datetime]
    image_url: Optional[str]

    # User progress info (always present, never null)
    user_progress: "UserKataProgressResponse"
    enrollment_count: int = 0
    unique_likes_count: int = 0
    unique_dislikes_count: int = 0


class AIKataListResponse(BaseModel):
    id: str
    title: str
    description: str
    level: KataLevel
    creator_name: Optional[str]
    creator_username: Optional[str]
    duration_minutes: int
    tags: List[str]
    roles: List[str]
    status: KataStatus
    date: datetime
    image_url: Optional[str]

    # User progress info (always present, never null)
    user_progress: "UserKataProgressResponse"
    enrollment_count: int = 0
    unique_likes_count: int = 0
    unique_dislikes_count: int = 0


class AIKataPaginatedResponse(PaginatedListResponse[AIKataListResponse]):
    pass


class KataTag(BaseModel):
    id: str
    name: str
    description: str


class KataRole(BaseModel):
    id: str
    name: str
    description: str


def load_kata_tags() -> List[KataTag]:
    """
    Load kata tags from YAML configuration file.

    Returns:
        List of KataTag objects
    """
    from codemie.configs import logger

    try:
        with open(config.KATA_TAGS_CONFIG_PATH, "r") as f:
            data = yaml.safe_load(f)
            return [KataTag(**tag) for tag in data.get("tags", [])]
    except FileNotFoundError:
        logger.warning(f"Kata tags config not found: {config.KATA_TAGS_CONFIG_PATH}")
        return []
    except yaml.YAMLError as e:
        logger.error(f"Invalid YAML in kata tags config: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"Failed to load kata tags: {e}", exc_info=True)
        return []


def get_valid_kata_tag_ids() -> List[str]:
    """
    Get list of valid kata tag IDs.

    Returns:
        List of tag IDs
    """
    return [tag.id for tag in load_kata_tags()]


def load_kata_roles() -> List[KataRole]:
    """
    Load kata roles from YAML configuration file.

    Returns:
        List of KataRole objects
    """
    from codemie.configs import logger

    try:
        with open(config.KATA_ROLES_CONFIG_PATH, "r") as f:
            data = yaml.safe_load(f)
            return [KataRole(**role) for role in data.get("roles", [])]
    except FileNotFoundError:
        logger.warning(f"Kata roles config not found: {config.KATA_ROLES_CONFIG_PATH}")
        return []
    except yaml.YAMLError as e:
        logger.error(f"Invalid YAML in kata roles config: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"Failed to load kata roles: {e}", exc_info=True)
        return []


def get_valid_kata_role_ids() -> List[str]:
    """
    Get list of valid kata role IDs.

    Returns:
        List of role IDs
    """
    return [role.id for role in load_kata_roles()]


# Rebuild models after UserKataProgressResponse is defined
def _rebuild_models():
    """Rebuild models with forward references after all imports are complete."""
    try:
        from codemie.rest_api.models.user_kata_progress import UserKataProgressResponse  # noqa: F401

        AIKataResponse.model_rebuild()
        AIKataListResponse.model_rebuild()
    except ImportError:
        pass


# Call rebuild when module is imported
_rebuild_models()
