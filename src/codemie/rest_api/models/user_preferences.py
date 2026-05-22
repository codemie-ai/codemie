# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import SQLModel, Field as SQLField

from codemie.rest_api.models.base import PydanticType


class FavoritesData(BaseModel):
    assistants: list[str] = Field(default_factory=list)
    workflows: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class UserPreferences(SQLModel, table=True):
    """Stores per-user favorites and pinned-assistants preferences."""

    __tablename__ = "user_preferences"

    user_id: str = SQLField(primary_key=True)
    pinned_assistants: list[str] = SQLField(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default="[]"),
    )
    favorites: FavoritesData = SQLField(
        default_factory=FavoritesData,
        sa_column=Column(
            PydanticType(FavoritesData),
            nullable=False,
            server_default='{"assistants":[],"workflows":[],"skills":[]}',
        ),
    )


class FavoriteItem(BaseModel):
    id: str
    icon_url: str
    name: str
    description: str
    type: str | None = None
    is_global: bool | None = None
    shared: bool | None = None
    visibility: str | None = None
    created_by: dict[str, Any] | None = None
    is_favorited: bool = True
    is_pinned: bool = False
    is_liked: bool = False
    is_disliked: bool = False
    unique_likes_count: int = 0
    unique_dislikes_count: int = 0
    assistants_count: int = 0
    user_abilities: list[str] = Field(default_factory=list)


class FavoritesListResponse(BaseModel):
    data: list[FavoriteItem]
    page: int
    per_page: int
    total: int
    pages: int


class UserPreferencesResponse(BaseModel):
    user_id: str
    pinned_assistants: list[str] = Field(default_factory=list)
    favorites: FavoritesData = Field(default_factory=FavoritesData)


class UserPreferencesUpdateRequest(BaseModel):
    pinned_assistants: list[str] | None = None
    favorites: FavoritesData | None = None


@dataclass
class FavoritesListResult:
    data: list[FavoriteItem]
    page: int
    per_page: int
    total: int
    pages: int
