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

from fastapi import APIRouter, Depends, Query
from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.user_preferences import (
    FavoritesListResponse,
    UserPreferencesResponse,
    UserPreferencesUpdateRequest,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.user_preferences_service import user_preferences_service

router = APIRouter(
    tags=["Preferences"],
    prefix="/v1/preferences",
)


def _check_user_access(user_id: str, current_user: User) -> None:
    if current_user.id != user_id and not current_user.is_admin:
        raise ExtendedHTTPException(code=403, message="Access denied")


@router.get("/{user_id}", response_model=UserPreferencesResponse, status_code=status.HTTP_200_OK)
def get_profile(user_id: str, current_user: User = Depends(authenticate)):
    """Return user favorites and pinned-assistants profile. Returns 404 if never created."""
    _check_user_access(user_id, current_user)
    profile = user_preferences_service.get_profile(user_id)
    return UserPreferencesResponse(
        user_id=profile.user_id,
        pinned_assistants=profile.pinned_assistants,
        favorites=profile.favorites,
    )


@router.put("/{user_id}", response_model=UserPreferencesResponse, status_code=status.HTTP_200_OK)
def upsert_profile(
    user_id: str,
    data: UserPreferencesUpdateRequest,
    current_user: User = Depends(authenticate),
):
    """Upsert user preferences. Creates if not exists, updates otherwise. Returns updated DTO."""
    _check_user_access(user_id, current_user)
    profile = user_preferences_service.upsert_profile(
        user_id=user_id,
        pinned_assistants=data.pinned_assistants,
        favorites=data.favorites,
    )
    return UserPreferencesResponse(
        user_id=profile.user_id,
        pinned_assistants=profile.pinned_assistants,
        favorites=profile.favorites,
    )


@router.get(
    "/{user_id}/favorites/assistants",
    response_model=FavoritesListResponse,
    status_code=status.HTTP_200_OK,
)
def get_favorite_assistants(
    user_id: str,
    search: str | None = Query(default=None),
    project: list[str] | None = Query(default=None),
    categories: list[str] | None = Query(default=None),
    created_by: str | None = Query(default=None),
    shared: bool | None = Query(default=None),
    page: int = Query(default=0, ge=0),
    per_page: int = Query(default=12, ge=1, le=100),
    current_user: User = Depends(authenticate),
):
    """List favorited assistants with rendering data, filters, and pagination."""
    _check_user_access(user_id, current_user)
    result = user_preferences_service.get_favorite_assistants(
        user_id=user_id,
        current_user=current_user,
        search=search,
        project=project,
        categories=categories,
        created_by=created_by,
        shared=shared,
        page=page,
        per_page=per_page,
    )
    return FavoritesListResponse(
        data=result.data,
        page=result.page,
        per_page=result.per_page,
        total=result.total,
        pages=result.pages,
    )


@router.get(
    "/{user_id}/favorites/skills",
    response_model=FavoritesListResponse,
    status_code=status.HTTP_200_OK,
)
def get_favorite_skills(
    user_id: str,
    search: str | None = Query(default=None),
    project: list[str] | None = Query(default=None),
    categories: list[str] | None = Query(default=None),
    created_by: str | None = Query(default=None),
    visibility: str | None = Query(default=None),
    page: int = Query(default=0, ge=0),
    per_page: int = Query(default=12, ge=1, le=100),
    current_user: User = Depends(authenticate),
):
    """List favorited skills with rendering data, filters, and pagination."""
    _check_user_access(user_id, current_user)
    result = user_preferences_service.get_favorite_skills(
        user_id=user_id,
        current_user=current_user,
        search=search,
        project=project,
        categories=categories,
        created_by=created_by,
        visibility=visibility,
        page=page,
        per_page=per_page,
    )
    return FavoritesListResponse(
        data=result.data,
        page=result.page,
        per_page=result.per_page,
        total=result.total,
        pages=result.pages,
    )


@router.get(
    "/{user_id}/favorites/workflows",
    response_model=FavoritesListResponse,
    status_code=status.HTTP_200_OK,
)
def get_favorite_workflows(
    user_id: str,
    search: str | None = Query(default=None),
    project: list[str] | None = Query(default=None),
    categories: list[str] | None = Query(default=None),
    created_by: str | None = Query(default=None),
    shared: bool | None = Query(default=None),
    page: int = Query(default=0, ge=0),
    per_page: int = Query(default=12, ge=1, le=100),
    current_user: User = Depends(authenticate),
):
    """List favorited workflows with rendering data, filters, and pagination."""
    _check_user_access(user_id, current_user)
    result = user_preferences_service.get_favorite_workflows(
        user_id=user_id,
        current_user=current_user,
        search=search,
        project=project,
        categories=categories,
        created_by=created_by,
        shared=shared,
        page=page,
        per_page=per_page,
    )
    return FavoritesListResponse(
        data=result.data,
        page=result.page,
        per_page=result.per_page,
        total=result.total,
        pages=result.pages,
    )
