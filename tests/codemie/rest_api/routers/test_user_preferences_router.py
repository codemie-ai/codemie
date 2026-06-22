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

"""Unit tests for user preferences router (/v1/preferences).

Covers:
- GET  /v1/preferences/{user_id}         — profile retrieval, 404, access control
- PUT  /v1/preferences/{user_id}         — upsert semantics, full array replacement, idempotency
- GET  /v1/preferences/{user_id}/favorites/assistants  — result shape, filter forwarding, pagination
- GET  /v1/preferences/{user_id}/favorites/skills      — result shape, filter forwarding
- GET  /v1/preferences/{user_id}/favorites/workflows   — result shape, filter forwarding
"""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.user_preferences import (
    FavoritesData,
    FavoriteItem,
    FavoritesListResult,
    UserPreferences,
    UserPreferencesUpdateRequest,
)
from codemie.rest_api.routers.user_preferences_router import (
    get_favorite_assistants,
    get_favorite_skills,
    get_favorite_workflows,
    get_profile,
    upsert_profile,
)
from codemie.rest_api.security.user import User


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def user() -> User:
    return User(
        id="user-123",
        username="testuser",
        email="testuser@example.com",
        name="Test User",
        is_admin=False,
        is_maintainer=False,
        project_names=[],
        admin_project_names=[],
        knowledge_bases=[],
    )


@pytest.fixture
def admin_user():
    mock = MagicMock(spec=User)
    mock.id = "admin-1"
    mock.is_admin = True
    return mock


@pytest.fixture
def other_user():
    mock = MagicMock(spec=User)
    mock.id = "other-999"
    mock.is_admin = False
    return mock


@pytest.fixture
def sample_profile() -> UserPreferences:
    return UserPreferences(
        user_id="user-123",
        pinned_assistants=["a1", "a2"],
        favorites=FavoritesData(
            assistants=["fav-a1"],
            workflows=["fav-w1"],
            skills=["fav-s1"],
        ),
    )


@pytest.fixture
def empty_favorites_result() -> FavoritesListResult:
    return FavoritesListResult(data=[], page=0, per_page=12, total=0, pages=0)


@pytest.fixture
def sample_favorites_result() -> FavoritesListResult:
    return FavoritesListResult(
        data=[FavoriteItem(id="a1", icon_url="icon.png", name="Assistant 1", description="Desc")],
        page=0,
        per_page=12,
        total=1,
        pages=1,
    )


# ---------------------------------------------------------------------------
# GET /v1/preferences/{user_id}
# ---------------------------------------------------------------------------


class TestGetProfile:
    @patch("codemie.rest_api.routers.user_preferences_router.user_preferences_service")
    def test_returns_profile_dto(self, mock_service, user, sample_profile):
        mock_service.get_profile.return_value = sample_profile

        result = get_profile(user_id="user-123", current_user=user)

        mock_service.get_profile.assert_called_once_with("user-123")
        assert result.user_id == "user-123"
        assert result.pinned_assistants == ["a1", "a2"]
        assert result.favorites.assistants == ["fav-a1"]
        assert result.favorites.workflows == ["fav-w1"]
        assert result.favorites.skills == ["fav-s1"]

    @patch("codemie.rest_api.routers.user_preferences_router.user_preferences_service")
    def test_raises_404_when_profile_not_found(self, mock_service, user):
        mock_service.get_profile.side_effect = ExtendedHTTPException(
            code=404, message="Profile not found for user user-123"
        )

        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_profile(user_id="user-123", current_user=user)

        assert exc_info.value.code == 404

    def test_raises_403_when_accessing_other_users_profile(self, other_user):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_profile(user_id="user-123", current_user=other_user)

        assert exc_info.value.code == 403

    @patch("codemie.rest_api.routers.user_preferences_router.user_preferences_service")
    def test_admin_can_access_any_profile(self, mock_service, admin_user, sample_profile):
        mock_service.get_profile.return_value = sample_profile

        result = get_profile(user_id="user-123", current_user=admin_user)

        assert result.user_id == "user-123"


# ---------------------------------------------------------------------------
# PUT /v1/preferences/{user_id}
# ---------------------------------------------------------------------------


class TestUpsertProfile:
    @patch("codemie.rest_api.routers.user_preferences_router.user_preferences_service")
    def test_upsert_returns_updated_dto(self, mock_service, user, sample_profile):
        mock_service.upsert_profile.return_value = sample_profile
        data = UserPreferencesUpdateRequest(
            pinned_assistants=["a1", "a2"],
            favorites=FavoritesData(assistants=["fav-a1"], workflows=["fav-w1"], skills=["fav-s1"]),
        )

        result = upsert_profile(user_id="user-123", data=data, current_user=user)

        mock_service.upsert_profile.assert_called_once_with(
            user_id="user-123",
            pinned_assistants=["a1", "a2"],
            favorites=data.favorites,
        )
        assert result.user_id == "user-123"
        assert result.pinned_assistants == ["a1", "a2"]

    @patch("codemie.rest_api.routers.user_preferences_router.user_preferences_service")
    def test_full_array_replacement_for_pinned_assistants(self, mock_service, user):
        updated_profile = UserPreferences(
            user_id="user-123",
            pinned_assistants=["a3"],
            favorites=FavoritesData(),
        )
        mock_service.upsert_profile.return_value = updated_profile

        data = UserPreferencesUpdateRequest(pinned_assistants=["a3"])
        result = upsert_profile(user_id="user-123", data=data, current_user=user)

        assert result.pinned_assistants == ["a3"]
        call_kwargs = mock_service.upsert_profile.call_args.kwargs
        assert call_kwargs["pinned_assistants"] == ["a3"]

    @patch("codemie.rest_api.routers.user_preferences_router.user_preferences_service")
    def test_idempotent_put_returns_same_result(self, mock_service, user, sample_profile):
        mock_service.upsert_profile.return_value = sample_profile
        data = UserPreferencesUpdateRequest(pinned_assistants=["a1", "a2"])

        result1 = upsert_profile(user_id="user-123", data=data, current_user=user)
        result2 = upsert_profile(user_id="user-123", data=data, current_user=user)

        assert result1.pinned_assistants == result2.pinned_assistants
        assert mock_service.upsert_profile.call_count == 2

    @patch("codemie.rest_api.routers.user_preferences_router.user_preferences_service")
    def test_partial_update_only_pinned(self, mock_service, user):
        profile = UserPreferences(
            user_id="user-123",
            pinned_assistants=["new-a"],
            favorites=FavoritesData(assistants=["fav-a1"]),
        )
        mock_service.upsert_profile.return_value = profile
        data = UserPreferencesUpdateRequest(pinned_assistants=["new-a"])

        result = upsert_profile(user_id="user-123", data=data, current_user=user)

        assert result.pinned_assistants == ["new-a"]
        call_kwargs = mock_service.upsert_profile.call_args.kwargs
        assert call_kwargs["favorites"] is None

    def test_raises_403_for_other_user(self, other_user):
        data = UserPreferencesUpdateRequest(pinned_assistants=[])

        with pytest.raises(ExtendedHTTPException) as exc_info:
            upsert_profile(user_id="user-123", data=data, current_user=other_user)

        assert exc_info.value.code == 403

    @patch("codemie.rest_api.routers.user_preferences_router.user_preferences_service")
    def test_empty_pinned_clears_array(self, mock_service, user):
        cleared_profile = UserPreferences(
            user_id="user-123",
            pinned_assistants=[],
            favorites=FavoritesData(),
        )
        mock_service.upsert_profile.return_value = cleared_profile
        data = UserPreferencesUpdateRequest(pinned_assistants=[])

        result = upsert_profile(user_id="user-123", data=data, current_user=user)

        assert result.pinned_assistants == []


# ---------------------------------------------------------------------------
# GET /v1/preferences/{user_id}/favorites/assistants
# ---------------------------------------------------------------------------


class TestGetFavoriteAssistants:
    @patch("codemie.rest_api.routers.user_preferences_router.user_preferences_service")
    def test_returns_paginated_response(self, mock_service, user, sample_favorites_result):
        mock_service.get_favorite_assistants.return_value = sample_favorites_result

        result = get_favorite_assistants(
            user_id="user-123",
            search=None,
            project=None,
            categories=None,
            created_by=None,
            shared=None,
            page=0,
            per_page=12,
            current_user=user,
        )

        assert result.total == 1
        assert result.pages == 1
        assert result.page == 0
        assert result.per_page == 12
        assert len(result.data) == 1
        assert result.data[0].name == "Assistant 1"

    @patch("codemie.rest_api.routers.user_preferences_router.user_preferences_service")
    def test_empty_when_no_favorites(self, mock_service, user, empty_favorites_result):
        mock_service.get_favorite_assistants.return_value = empty_favorites_result

        result = get_favorite_assistants(
            user_id="user-123",
            search=None,
            project=None,
            categories=None,
            created_by=None,
            shared=None,
            page=0,
            per_page=12,
            current_user=user,
        )

        assert result.data == []
        assert result.total == 0

    @patch("codemie.rest_api.routers.user_preferences_router.user_preferences_service")
    def test_forwards_all_filters_to_service(self, mock_service, user, empty_favorites_result):
        mock_service.get_favorite_assistants.return_value = empty_favorites_result

        get_favorite_assistants(
            user_id="user-123",
            search="gpt",
            project=["proj-1"],
            categories=["llm"],
            created_by="alice",
            shared=True,
            page=1,
            per_page=6,
            current_user=user,
        )

        mock_service.get_favorite_assistants.assert_called_once_with(
            user_id="user-123",
            current_user=user,
            search="gpt",
            project=["proj-1"],
            categories=["llm"],
            created_by="alice",
            shared=True,
            page=1,
            per_page=6,
        )

    def test_raises_403_for_other_user(self, other_user):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_favorite_assistants(
                user_id="user-123",
                search=None,
                project=None,
                categories=None,
                created_by=None,
                shared=None,
                page=0,
                per_page=12,
                current_user=other_user,
            )

        assert exc_info.value.code == 403

    @patch("codemie.rest_api.routers.user_preferences_router.user_preferences_service")
    def test_pagination_second_page(self, mock_service, user):
        page2_result = FavoritesListResult(
            data=[FavoriteItem(id="a13", icon_url="", name="A13", description="")],
            page=1,
            per_page=12,
            total=13,
            pages=2,
        )
        mock_service.get_favorite_assistants.return_value = page2_result

        result = get_favorite_assistants(
            user_id="user-123",
            search=None,
            project=None,
            categories=None,
            created_by=None,
            shared=None,
            page=1,
            per_page=12,
            current_user=user,
        )

        assert result.page == 1
        assert result.pages == 2
        assert result.total == 13


# ---------------------------------------------------------------------------
# GET /v1/preferences/{user_id}/favorites/skills
# ---------------------------------------------------------------------------


class TestGetFavoriteSkills:
    @patch("codemie.rest_api.routers.user_preferences_router.user_preferences_service")
    def test_returns_skills_response(self, mock_service, user):
        result_data = FavoritesListResult(
            data=[FavoriteItem(id="s1", icon_url="", name="Skill 1", description="A skill")],
            page=0,
            per_page=12,
            total=1,
            pages=1,
        )
        mock_service.get_favorite_skills.return_value = result_data

        result = get_favorite_skills(
            user_id="user-123",
            search=None,
            project=None,
            categories=None,
            created_by=None,
            visibility=None,
            page=0,
            per_page=12,
            current_user=user,
        )

        assert result.total == 1
        assert result.data[0].name == "Skill 1"

    @patch("codemie.rest_api.routers.user_preferences_router.user_preferences_service")
    def test_forwards_visibility_filter(self, mock_service, user):
        mock_service.get_favorite_skills.return_value = FavoritesListResult(
            data=[], page=0, per_page=12, total=0, pages=0
        )

        get_favorite_skills(
            user_id="user-123",
            search="py",
            project=["proj-a"],
            categories=["data"],
            created_by="bob",
            visibility="private",
            page=0,
            per_page=12,
            current_user=user,
        )

        mock_service.get_favorite_skills.assert_called_once_with(
            user_id="user-123",
            current_user=user,
            search="py",
            project=["proj-a"],
            categories=["data"],
            created_by="bob",
            visibility="private",
            page=0,
            per_page=12,
        )

    def test_raises_403_for_other_user(self, other_user):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_favorite_skills(
                user_id="user-123",
                search=None,
                project=None,
                categories=None,
                created_by=None,
                visibility=None,
                page=0,
                per_page=12,
                current_user=other_user,
            )

        assert exc_info.value.code == 403


# ---------------------------------------------------------------------------
# GET /v1/preferences/{user_id}/favorites/workflows
# ---------------------------------------------------------------------------


class TestGetFavoriteWorkflows:
    @patch("codemie.rest_api.routers.user_preferences_router.user_preferences_service")
    def test_returns_workflows_response(self, mock_service, user):
        result_data = FavoritesListResult(
            data=[FavoriteItem(id="w1", icon_url="wf.png", name="Workflow 1", description="A workflow")],
            page=0,
            per_page=12,
            total=1,
            pages=1,
        )
        mock_service.get_favorite_workflows.return_value = result_data

        result = get_favorite_workflows(
            user_id="user-123",
            search=None,
            project=None,
            categories=None,
            created_by=None,
            shared=None,
            page=0,
            per_page=12,
            current_user=user,
        )

        assert result.total == 1
        assert result.data[0].name == "Workflow 1"

    @patch("codemie.rest_api.routers.user_preferences_router.user_preferences_service")
    def test_forwards_search_and_shared_filters(self, mock_service, user):
        mock_service.get_favorite_workflows.return_value = FavoritesListResult(
            data=[], page=0, per_page=12, total=0, pages=0
        )

        get_favorite_workflows(
            user_id="user-123",
            search="deploy",
            project=["proj-b"],
            categories=None,
            created_by="carol",
            shared=False,
            page=0,
            per_page=12,
            current_user=user,
        )

        mock_service.get_favorite_workflows.assert_called_once_with(
            user_id="user-123",
            current_user=user,
            search="deploy",
            project=["proj-b"],
            categories=None,
            created_by="carol",
            shared=False,
            page=0,
            per_page=12,
        )

    def test_raises_403_for_other_user(self, other_user):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_favorite_workflows(
                user_id="user-123",
                search=None,
                project=None,
                categories=None,
                created_by=None,
                shared=None,
                page=0,
                per_page=12,
                current_user=other_user,
            )

        assert exc_info.value.code == 403

    @patch("codemie.rest_api.routers.user_preferences_router.user_preferences_service")
    def test_forwards_categories_filter_to_service(self, mock_service, user):
        mock_service.get_favorite_workflows.return_value = FavoritesListResult(
            data=[], page=0, per_page=12, total=0, pages=0
        )

        get_favorite_workflows(
            user_id="user-123",
            search=None,
            project=None,
            categories=["ai"],
            created_by=None,
            shared=None,
            page=0,
            per_page=12,
            current_user=user,
        )

        mock_service.get_favorite_workflows.assert_called_once_with(
            user_id="user-123",
            current_user=user,
            search=None,
            project=None,
            categories=["ai"],
            created_by=None,
            shared=None,
            page=0,
            per_page=12,
        )
