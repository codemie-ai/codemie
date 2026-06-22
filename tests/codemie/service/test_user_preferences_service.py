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

"""Unit tests for UserPreferencesService.

Covers:
- get_profile: 404 when profile absent, returns profile when present
- upsert_profile: create semantics, update (full array replacement), idempotency
- get_favorite_assistants: empty when no favorites, missing IDs silently skipped
- get_favorite_skills: empty when no favorites, missing IDs silently skipped
- get_favorite_workflows: empty when no favorites, missing IDs silently skipped
"""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.user_preferences import (
    FavoritesData,
    UserPreferences,
)
from codemie.rest_api.security.user import User
from codemie.service.user_preferences_service import UserPreferencesService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_mock():
    mock_session = MagicMock()
    return mock_session


def _make_user() -> MagicMock:
    mock = MagicMock(spec=User)
    mock.id = "user-123"
    mock.is_admin = False
    return mock


def _make_profile(
    user_id: str = "user-123",
    pinned: list | None = None,
    assistants: list | None = None,
    workflows: list | None = None,
    skills: list | None = None,
) -> UserPreferences:
    return UserPreferences(
        user_id=user_id,
        pinned_assistants=pinned or [],
        favorites=FavoritesData(
            assistants=assistants or [],
            workflows=workflows or [],
            skills=skills or [],
        ),
    )


# ---------------------------------------------------------------------------
# get_profile
# ---------------------------------------------------------------------------


class TestGetProfile:
    @patch("codemie.service.user_preferences_service.user_preferences_repository")
    @patch("codemie.service.user_preferences_service.get_session")
    def test_raises_404_when_profile_not_found(self, mock_get_session, mock_repo):
        mock_session = _make_session_mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False
        mock_repo.get_by_user_id.return_value = None

        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserPreferencesService.get_profile("user-123")

        assert exc_info.value.code == 404
        mock_repo.get_by_user_id.assert_called_once_with(mock_session, "user-123")

    @patch("codemie.service.user_preferences_service.user_preferences_repository")
    @patch("codemie.service.user_preferences_service.get_session")
    def test_returns_profile_when_found(self, mock_get_session, mock_repo):
        mock_session = _make_session_mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False

        profile = _make_profile(pinned=["a1"], assistants=["fav-1"])
        mock_repo.get_by_user_id.return_value = profile

        result = UserPreferencesService.get_profile("user-123")

        assert result.user_id == "user-123"
        assert result.pinned_assistants == ["a1"]
        assert result.favorites.assistants == ["fav-1"]


# ---------------------------------------------------------------------------
# upsert_profile
# ---------------------------------------------------------------------------


class TestUpsertProfile:
    @patch("codemie.service.user_preferences_service.user_preferences_repository")
    @patch("codemie.service.user_preferences_service.get_session")
    def test_creates_new_profile(self, mock_get_session, mock_repo):
        mock_session = _make_session_mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False

        new_profile = _make_profile(pinned=["a1", "a2"], assistants=["fav-a1"])
        mock_repo.upsert.return_value = new_profile

        favorites = FavoritesData(assistants=["fav-a1"])
        result = UserPreferencesService.upsert_profile(
            user_id="user-123",
            pinned_assistants=["a1", "a2"],
            favorites=favorites,
        )

        mock_repo.upsert.assert_called_once_with(mock_session, "user-123", ["a1", "a2"], favorites)
        assert result.pinned_assistants == ["a1", "a2"]

    @patch("codemie.service.user_preferences_service.user_preferences_repository")
    @patch("codemie.service.user_preferences_service.get_session")
    def test_full_array_replacement_overwrites_pinned(self, mock_get_session, mock_repo):
        mock_session = _make_session_mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False

        updated_profile = _make_profile(pinned=["a3"])
        mock_repo.upsert.return_value = updated_profile

        result = UserPreferencesService.upsert_profile(
            user_id="user-123",
            pinned_assistants=["a3"],
            favorites=None,
        )

        call_args = mock_repo.upsert.call_args
        assert call_args.args[2] == ["a3"]
        assert result.pinned_assistants == ["a3"]

    @patch("codemie.service.user_preferences_service.user_preferences_repository")
    @patch("codemie.service.user_preferences_service.get_session")
    def test_clears_pinned_with_empty_array(self, mock_get_session, mock_repo):
        mock_session = _make_session_mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False

        cleared_profile = _make_profile(pinned=[])
        mock_repo.upsert.return_value = cleared_profile

        result = UserPreferencesService.upsert_profile(
            user_id="user-123",
            pinned_assistants=[],
            favorites=None,
        )

        call_args = mock_repo.upsert.call_args
        assert call_args.args[2] == []
        assert result.pinned_assistants == []

    @patch("codemie.service.user_preferences_service.user_preferences_repository")
    @patch("codemie.service.user_preferences_service.get_session")
    def test_idempotent_repeated_put_returns_same_result(self, mock_get_session, mock_repo):
        mock_session = _make_session_mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False

        profile = _make_profile(pinned=["a1", "a2"])
        mock_repo.upsert.return_value = profile

        result1 = UserPreferencesService.upsert_profile("user-123", ["a1", "a2"], None)
        result2 = UserPreferencesService.upsert_profile("user-123", ["a1", "a2"], None)

        assert result1.pinned_assistants == result2.pinned_assistants
        assert mock_repo.upsert.call_count == 2

    @patch("codemie.service.user_preferences_service.user_preferences_repository")
    @patch("codemie.service.user_preferences_service.get_session")
    def test_partial_update_passes_none_for_unchanged_field(self, mock_get_session, mock_repo):
        mock_session = _make_session_mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False

        mock_repo.upsert.return_value = _make_profile()

        UserPreferencesService.upsert_profile(
            user_id="user-123",
            pinned_assistants=["a5"],
            favorites=None,
        )

        call_args = mock_repo.upsert.call_args
        assert call_args.args[3] is None


# ---------------------------------------------------------------------------
# get_favorite_assistants
# ---------------------------------------------------------------------------


class TestGetFavoriteAssistants:
    @patch("codemie.service.user_preferences_service.user_preferences_repository")
    @patch("codemie.service.user_preferences_service.get_session")
    def test_returns_empty_when_profile_has_no_assistant_favorites(self, mock_get_session, mock_repo):
        mock_session = _make_session_mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False

        profile = _make_profile(assistants=[])
        mock_repo.get_by_user_id.return_value = profile

        result = UserPreferencesService.get_favorite_assistants("user-123", current_user=_make_user())

        assert result.data == []
        assert result.total == 0
        mock_session.exec.assert_not_called()

    @patch("codemie.service.user_preferences_service.user_preferences_repository")
    @patch("codemie.service.user_preferences_service.get_session")
    def test_returns_empty_when_profile_not_found(self, mock_get_session, mock_repo):
        mock_session = _make_session_mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False

        mock_repo.get_by_user_id.return_value = None

        result = UserPreferencesService.get_favorite_assistants("user-123", current_user=_make_user())

        assert result.data == []
        assert result.total == 0

    @patch("codemie.service.user_preferences_service.Ability")
    @patch("codemie.service.user_preferences_service.get_assistant_reactions_by_user")
    @patch("codemie.service.user_preferences_service.user_preferences_repository")
    @patch("codemie.service.user_preferences_service.get_session")
    def test_missing_ids_silently_skipped(
        self, mock_get_session, mock_repo, mock_get_assistant_reactions, mock_ability_cls
    ):
        mock_session = _make_session_mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False
        mock_get_assistant_reactions.return_value = []
        mock_ability_cls.return_value.list.return_value = []

        profile = _make_profile(assistants=["a1", "a2", "deleted-id"])
        mock_repo.get_by_user_id.return_value = profile

        mock_a1 = MagicMock()
        mock_a1.id = "a1"
        mock_a1.name = "Assistant 1"
        mock_a1.description = "Desc 1"
        mock_a1.icon_url = "icon1.png"
        mock_a1.type = None
        mock_a1.is_global = None
        mock_a1.shared = None
        mock_a1.created_by = None
        mock_a1.unique_likes_count = 0
        mock_a1.unique_dislikes_count = 0
        mock_a1.categories = []

        mock_a2 = MagicMock()
        mock_a2.id = "a2"
        mock_a2.name = "Assistant 2"
        mock_a2.description = "Desc 2"
        mock_a2.icon_url = None
        mock_a2.type = None
        mock_a2.is_global = None
        mock_a2.shared = None
        mock_a2.created_by = None
        mock_a2.unique_likes_count = 0
        mock_a2.unique_dislikes_count = 0
        mock_a2.categories = []

        count_result = MagicMock()
        count_result.one.return_value = 2
        data_result = MagicMock()
        data_result.all.return_value = [mock_a1, mock_a2]
        mock_session.exec.side_effect = [count_result, data_result]

        result = UserPreferencesService.get_favorite_assistants("user-123", current_user=_make_user())

        assert len(result.data) == 2
        assert result.total == 2
        assert result.data[0].id == "a1"
        assert result.data[1].id == "a2"
        assert result.data[1].icon_url == ""

    @patch("codemie.service.user_preferences_service.Ability")
    @patch("codemie.service.user_preferences_service.get_assistant_reactions_by_user")
    @patch("codemie.service.user_preferences_service.user_preferences_repository")
    @patch("codemie.service.user_preferences_service.get_session")
    def test_categories_populated_in_favorite_item(
        self, mock_get_session, mock_repo, mock_get_assistant_reactions, mock_ability_cls
    ):
        mock_session = _make_session_mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False
        mock_get_assistant_reactions.return_value = []
        mock_ability_cls.return_value.list.return_value = []

        profile = _make_profile(assistants=["a1"])
        mock_repo.get_by_user_id.return_value = profile

        mock_a1 = MagicMock()
        mock_a1.id = "a1"
        mock_a1.name = "Assistant 1"
        mock_a1.description = "Desc"
        mock_a1.icon_url = "icon.png"
        mock_a1.type = None
        mock_a1.is_global = None
        mock_a1.shared = None
        mock_a1.created_by = None
        mock_a1.unique_likes_count = 0
        mock_a1.unique_dislikes_count = 0
        mock_a1.categories = ["ai", "dev"]

        count_result = MagicMock()
        count_result.one.return_value = 1
        data_result = MagicMock()
        data_result.all.return_value = [mock_a1]
        mock_session.exec.side_effect = [count_result, data_result]

        result = UserPreferencesService.get_favorite_assistants("user-123", current_user=_make_user())

        assert result.data[0].categories == ["ai", "dev"]

    @patch("codemie.service.user_preferences_service.Ability")
    @patch("codemie.service.user_preferences_service.get_assistant_reactions_by_user")
    @patch("codemie.service.user_preferences_service.user_preferences_repository")
    @patch("codemie.service.user_preferences_service.get_session")
    def test_pagination_math_correct(self, mock_get_session, mock_repo, mock_get_assistant_reactions, mock_ability_cls):
        mock_session = _make_session_mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False
        mock_get_assistant_reactions.return_value = []
        mock_ability_cls.return_value.list.return_value = []

        profile = _make_profile(assistants=[f"a{i}" for i in range(25)])
        mock_repo.get_by_user_id.return_value = profile

        count_result = MagicMock()
        count_result.one.return_value = 25
        data_result = MagicMock()
        data_result.all.return_value = []
        mock_session.exec.side_effect = [count_result, data_result]

        result = UserPreferencesService.get_favorite_assistants(
            "user-123", current_user=_make_user(), page=0, per_page=12
        )

        assert result.total == 25
        assert result.pages == 3


# ---------------------------------------------------------------------------
# get_favorite_skills
# ---------------------------------------------------------------------------


class TestGetFavoriteSkills:
    @patch("codemie.service.user_preferences_service.user_preferences_repository")
    @patch("codemie.service.user_preferences_service.get_session")
    def test_returns_empty_when_no_skill_favorites(self, mock_get_session, mock_repo):
        mock_session = _make_session_mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False

        profile = _make_profile(skills=[])
        mock_repo.get_by_user_id.return_value = profile

        result = UserPreferencesService.get_favorite_skills("user-123", current_user=_make_user())

        assert result.data == []
        assert result.total == 0

    @patch("codemie.service.user_preferences_service.Ability")
    @patch("codemie.service.user_preferences_service.get_skill_reactions_by_user")
    @patch("codemie.repository.skill_repository.SkillRepository.get_assistants_count_for_skills")
    @patch("codemie.service.user_preferences_service.user_preferences_repository")
    @patch("codemie.service.user_preferences_service.get_session")
    def test_missing_ids_silently_skipped(
        self, mock_get_session, mock_repo, mock_assistants_count, mock_get_skill_reactions, mock_ability_cls
    ):
        mock_session = _make_session_mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False

        mock_assistants_count.return_value = {}
        mock_get_skill_reactions.return_value = []
        mock_ability_cls.return_value.list.return_value = []

        profile = _make_profile(skills=["s1", "s2", "deleted-skill"])
        mock_repo.get_by_user_id.return_value = profile

        mock_s1 = MagicMock()
        mock_s1.id = "s1"
        mock_s1.name = "Skill 1"
        mock_s1.description = "A skill"
        mock_s1.created_by = None
        mock_s1.visibility = None
        mock_s1.unique_likes_count = 0
        mock_s1.unique_dislikes_count = 0
        mock_s1.categories = []

        count_result = MagicMock()
        count_result.one.return_value = 1
        data_result = MagicMock()
        data_result.all.return_value = [mock_s1]
        mock_session.exec.side_effect = [count_result, data_result]

        result = UserPreferencesService.get_favorite_skills("user-123", current_user=_make_user())

        assert len(result.data) == 1
        assert result.total == 1
        assert result.data[0].id == "s1"

    @patch("codemie.service.user_preferences_service.Ability")
    @patch("codemie.service.user_preferences_service.get_skill_reactions_by_user")
    @patch("codemie.repository.skill_repository.SkillRepository.get_assistants_count_for_skills")
    @patch("codemie.service.user_preferences_service.user_preferences_repository")
    @patch("codemie.service.user_preferences_service.get_session")
    def test_categories_populated_in_favorite_item(
        self, mock_get_session, mock_repo, mock_assistants_count, mock_get_skill_reactions, mock_ability_cls
    ):
        mock_session = _make_session_mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False
        mock_assistants_count.return_value = {}
        mock_get_skill_reactions.return_value = []
        mock_ability_cls.return_value.list.return_value = []

        profile = _make_profile(skills=["s1"])
        mock_repo.get_by_user_id.return_value = profile

        mock_s1 = MagicMock()
        mock_s1.id = "s1"
        mock_s1.name = "Skill 1"
        mock_s1.description = "A skill"
        mock_s1.created_by = None
        mock_s1.visibility = None
        mock_s1.unique_likes_count = 0
        mock_s1.unique_dislikes_count = 0
        mock_s1.categories = ["data", "ml"]

        count_result = MagicMock()
        count_result.one.return_value = 1
        data_result = MagicMock()
        data_result.all.return_value = [mock_s1]
        mock_session.exec.side_effect = [count_result, data_result]

        result = UserPreferencesService.get_favorite_skills("user-123", current_user=_make_user())

        assert result.data[0].categories == ["data", "ml"]


# ---------------------------------------------------------------------------
# get_favorite_workflows
# ---------------------------------------------------------------------------


class TestGetFavoriteWorkflows:
    @patch("codemie.service.user_preferences_service.user_preferences_repository")
    @patch("codemie.service.user_preferences_service.get_session")
    def test_returns_empty_when_no_workflow_favorites(self, mock_get_session, mock_repo):
        mock_session = _make_session_mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False

        profile = _make_profile(workflows=[])
        mock_repo.get_by_user_id.return_value = profile

        result = UserPreferencesService.get_favorite_workflows("user-123", current_user=_make_user())

        assert result.data == []
        assert result.total == 0

    @patch("codemie.service.user_preferences_service.Ability")
    @patch("codemie.service.user_preferences_service.user_preferences_repository")
    @patch("codemie.service.user_preferences_service.get_session")
    def test_missing_ids_silently_skipped(self, mock_get_session, mock_repo, mock_ability_cls):
        mock_session = _make_session_mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False
        mock_ability_cls.return_value.list.return_value = []

        profile = _make_profile(workflows=["w1", "w2", "deleted-wf"])
        mock_repo.get_by_user_id.return_value = profile

        mock_w1 = MagicMock()
        mock_w1.id = "w1"
        mock_w1.name = "Workflow 1"
        mock_w1.description = "A workflow"
        mock_w1.icon_url = "wf.png"
        mock_w1.shared = None
        mock_w1.created_by = None
        mock_w1.is_global = None
        mock_w1.categories = []

        count_result = MagicMock()
        count_result.one.return_value = 1
        data_result = MagicMock()
        data_result.all.return_value = [mock_w1]
        mock_session.exec.side_effect = [count_result, data_result]

        result = UserPreferencesService.get_favorite_workflows("user-123", current_user=_make_user())

        assert len(result.data) == 1
        assert result.total == 1
        assert result.data[0].id == "w1"
        assert result.data[0].icon_url == "wf.png"

    @patch("codemie.service.user_preferences_service.Ability")
    @patch("codemie.service.user_preferences_service.user_preferences_repository")
    @patch("codemie.service.user_preferences_service.get_session")
    def test_categories_populated_in_favorite_item(self, mock_get_session, mock_repo, mock_ability_cls):
        mock_session = _make_session_mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False
        mock_ability_cls.return_value.list.return_value = []

        profile = _make_profile(workflows=["w1"])
        mock_repo.get_by_user_id.return_value = profile

        mock_w1 = MagicMock()
        mock_w1.id = "w1"
        mock_w1.name = "Workflow 1"
        mock_w1.description = "A workflow"
        mock_w1.icon_url = "wf.png"
        mock_w1.shared = None
        mock_w1.created_by = None
        mock_w1.is_global = None
        mock_w1.categories = ["ai", "dev"]

        count_result = MagicMock()
        count_result.one.return_value = 1
        data_result = MagicMock()
        data_result.all.return_value = [mock_w1]
        mock_session.exec.side_effect = [count_result, data_result]

        result = UserPreferencesService.get_favorite_workflows("user-123", current_user=_make_user())

        assert result.data[0].categories == ["ai", "dev"]

    @patch("codemie.service.user_preferences_service.Ability")
    @patch("codemie.service.user_preferences_service.user_preferences_repository")
    @patch("codemie.service.user_preferences_service.get_session")
    def test_categories_filter_accepted(self, mock_get_session, mock_repo, mock_ability_cls):
        mock_session = _make_session_mock()
        mock_get_session.return_value.__enter__.return_value = mock_session
        mock_get_session.return_value.__exit__.return_value = False
        mock_ability_cls.return_value.list.return_value = []

        profile = _make_profile(workflows=["w1"])
        mock_repo.get_by_user_id.return_value = profile

        mock_w1 = MagicMock()
        mock_w1.id = "w1"
        mock_w1.name = "Workflow 1"
        mock_w1.description = "A workflow"
        mock_w1.icon_url = "wf.png"
        mock_w1.shared = None
        mock_w1.created_by = None
        mock_w1.is_global = None
        mock_w1.categories = ["ai"]

        count_result = MagicMock()
        count_result.one.return_value = 1
        data_result = MagicMock()
        data_result.all.return_value = [mock_w1]
        mock_session.exec.side_effect = [count_result, data_result]

        result = UserPreferencesService.get_favorite_workflows("user-123", current_user=_make_user(), categories=["ai"])

        assert result.total == 1
        assert result.data[0].categories == ["ai"]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_singleton_instance():
    from codemie.service.user_preferences_service import user_preferences_service

    assert isinstance(user_preferences_service, UserPreferencesService)
