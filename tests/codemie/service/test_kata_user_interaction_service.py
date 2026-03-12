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

from unittest.mock import MagicMock, patch

import pytest

from codemie.repository.kata_user_interaction_repository import KataUserInteractionRepository
from codemie.rest_api.models.usage.kata_user_interaction import KataUserInteractionSQL, ReactionType
from codemie.rest_api.models.ai_kata import AIKata
from codemie.rest_api.security.user import User
from codemie.service.kata_user_interaction_service import (
    KataUserInteractionService,
    ReactionResponse,
    manage_reaction,
    remove_reactions,
    get_reactions_by_user,
)


@pytest.fixture
def mock_repository():
    """Mock kata user interaction repository."""
    return MagicMock(spec=KataUserInteractionRepository)


@pytest.fixture
def interaction_service(mock_repository):
    """Kata user interaction service with mocked repository."""
    return KataUserInteractionService(repository=mock_repository)


@pytest.fixture
def sample_user():
    """Sample user fixture."""
    return User(id="user123", username="testuser", name="Test User")


@pytest.fixture
def sample_kata():
    """Sample kata fixture."""
    return AIKata(
        id="kata123",
        title="Test Kata",
        description="Test description",
        steps="# Step 1\nContent",
        level="beginner",
        creator_id="creator123",
        creator_name="Creator",
        creator_username="creator",
    )


@pytest.fixture
def sample_interaction():
    """Sample interaction record."""
    return KataUserInteractionSQL(
        id="interaction123",
        kata_id="kata123",
        user_id="user123",
        usage_count=5,
        reaction=None,
    )


@pytest.fixture
def sample_interaction_with_like():
    """Sample interaction record with like."""
    return KataUserInteractionSQL(
        id="interaction123",
        kata_id="kata123",
        user_id="user123",
        usage_count=5,
        reaction=ReactionType.LIKE,
    )


@pytest.fixture
def sample_interaction_with_dislike():
    """Sample interaction record with dislike."""
    return KataUserInteractionSQL(
        id="interaction123",
        kata_id="kata123",
        user_id="user123",
        usage_count=5,
        reaction=ReactionType.DISLIKE,
    )


# record_usage tests


def test_record_usage_success(interaction_service, sample_kata, sample_user, mock_repository):
    """Test successful usage recording."""
    mock_usage = MagicMock()
    mock_usage.usage_count = 1
    mock_repository.record_usage.return_value = mock_usage

    result = interaction_service.record_usage(sample_kata, sample_user)

    assert result is not None
    assert result.usage_count == 1
    mock_repository.record_usage.assert_called_once_with(sample_kata.id, sample_user.id)


def test_record_usage_exception(interaction_service, sample_kata, sample_user, mock_repository):
    """Test usage recording handles exceptions gracefully."""
    mock_repository.record_usage.side_effect = Exception("Database error")

    result = interaction_service.record_usage(sample_kata, sample_user)

    assert result is None
    mock_repository.record_usage.assert_called_once_with(sample_kata.id, sample_user.id)


# manage_reaction tests


def test_manage_reaction_add_like_to_no_reaction(interaction_service, sample_interaction, sample_user, mock_repository):
    """Test adding a like when no reaction exists."""
    mock_repository.get_by_kata_and_user.return_value = sample_interaction
    mock_repository.set_reaction_value.return_value = True
    mock_repository.get_like_count.return_value = 1
    mock_repository.get_dislike_count.return_value = 0

    with patch("codemie.service.kata_user_interaction_service.AIKata.find_by_id", return_value=None):
        result = interaction_service.manage_reaction("kata123", sample_user, "like")

    assert result.success is True
    assert result.reaction == ReactionType.LIKE
    assert result.like_count == 1
    assert result.dislike_count == 0
    mock_repository.set_reaction_value.assert_called_once_with("kata123", "user123", ReactionType.LIKE)


def test_manage_reaction_toggle_like_off(
    interaction_service, sample_interaction_with_like, sample_user, mock_repository
):
    """Test toggling a like off (removing reaction)."""
    mock_repository.get_by_kata_and_user.return_value = sample_interaction_with_like
    mock_repository.set_reaction_value.return_value = True
    mock_repository.get_like_count.return_value = 0
    mock_repository.get_dislike_count.return_value = 0

    with patch("codemie.service.kata_user_interaction_service.AIKata.find_by_id", return_value=None):
        result = interaction_service.manage_reaction("kata123", sample_user, "like")

    assert result.success is True
    assert result.reaction is None
    assert result.like_count == 0
    assert result.dislike_count == 0
    mock_repository.set_reaction_value.assert_called_once_with("kata123", "user123", None)


def test_manage_reaction_change_like_to_dislike(
    interaction_service, sample_interaction_with_like, sample_user, mock_repository
):
    """Test changing reaction from like to dislike."""
    mock_repository.get_by_kata_and_user.return_value = sample_interaction_with_like
    mock_repository.set_reaction_value.return_value = True
    mock_repository.get_like_count.return_value = 0
    mock_repository.get_dislike_count.return_value = 1

    with patch("codemie.service.kata_user_interaction_service.AIKata.find_by_id", return_value=None):
        result = interaction_service.manage_reaction("kata123", sample_user, "dislike")

    assert result.success is True
    assert result.reaction == ReactionType.DISLIKE
    assert result.like_count == 0
    assert result.dislike_count == 1
    mock_repository.set_reaction_value.assert_called_once_with("kata123", "user123", ReactionType.DISLIKE)


def test_manage_reaction_invalid_type(interaction_service, sample_user, mock_repository):
    """Test managing reaction with invalid type."""
    result = interaction_service.manage_reaction("kata123", sample_user, "invalid")

    assert result.success is False
    assert result.error == "Invalid reaction type"
    assert result.like_count == 0
    assert result.dislike_count == 0
    mock_repository.set_reaction_value.assert_not_called()


def test_manage_reaction_no_interaction_record(interaction_service, sample_user, mock_repository):
    """Test managing reaction when no interaction record exists."""
    mock_repository.get_by_kata_and_user.return_value = None
    mock_repository.set_reaction_value.return_value = True
    mock_repository.get_like_count.return_value = 1
    mock_repository.get_dislike_count.return_value = 0

    with patch("codemie.service.kata_user_interaction_service.AIKata.find_by_id", return_value=None):
        result = interaction_service.manage_reaction("kata123", sample_user, "like")

    assert result.success is True
    assert result.reaction == ReactionType.LIKE
    mock_repository.set_reaction_value.assert_called_once_with("kata123", "user123", ReactionType.LIKE)


def test_manage_reaction_set_value_fails(interaction_service, sample_interaction, sample_user, mock_repository):
    """Test managing reaction when set_reaction_value fails."""
    mock_repository.get_by_kata_and_user.return_value = sample_interaction
    mock_repository.set_reaction_value.return_value = False

    result = interaction_service.manage_reaction("kata123", sample_user, "like")

    assert result.success is False
    assert result.error == "Failed to update reaction"
    assert result.reaction is None


def test_manage_reaction_updates_kata_counts(interaction_service, sample_interaction, sample_user, mock_repository):
    """Test that manage_reaction updates kata denormalized counts."""
    mock_kata = MagicMock(spec=AIKata)
    mock_kata.id = "kata123"
    mock_kata.title = "Test Kata"

    mock_repository.get_by_kata_and_user.return_value = sample_interaction
    mock_repository.set_reaction_value.return_value = True
    mock_repository.get_like_count.return_value = 5
    mock_repository.get_dislike_count.return_value = 2

    mock_kata_repository = MagicMock()

    with (
        patch("codemie.service.kata_user_interaction_service.AIKata.find_by_id", return_value=mock_kata),
        patch(
            "codemie.repository.ai_kata_repository.SQLAIKataRepository",
            return_value=mock_kata_repository,
        ),
    ):
        result = interaction_service.manage_reaction("kata123", sample_user, "like")

    assert result.success is True
    assert result.like_count == 5
    assert result.dislike_count == 2
    mock_kata_repository.update_reaction_counts.assert_called_once_with(mock_kata, 5, 2)


# get_reactions_by_user tests


def test_get_reactions_by_user_all_reactions(interaction_service, mock_repository):
    """Test getting all reactions by user."""
    mock_reactions = [
        MagicMock(kata_id="kata1", reaction=ReactionType.LIKE),
        MagicMock(kata_id="kata2", reaction=ReactionType.DISLIKE),
    ]
    mock_repository.get_reactions_by_user.return_value = mock_reactions

    result = interaction_service.get_reactions_by_user("user123")

    assert len(result) == 2
    mock_repository.get_reactions_by_user.assert_called_once_with("user123", None)


def test_get_reactions_by_user_filtered_likes(interaction_service, mock_repository):
    """Test getting only likes by user."""
    mock_reactions = [MagicMock(kata_id="kata1", reaction=ReactionType.LIKE)]
    mock_repository.get_reactions_by_user.return_value = mock_reactions

    result = interaction_service.get_reactions_by_user("user123", ReactionType.LIKE)

    assert len(result) == 1
    assert result[0].reaction == ReactionType.LIKE
    mock_repository.get_reactions_by_user.assert_called_once_with("user123", ReactionType.LIKE)


def test_get_reactions_by_user_filtered_dislikes(interaction_service, mock_repository):
    """Test getting only dislikes by user."""
    mock_reactions = [MagicMock(kata_id="kata2", reaction=ReactionType.DISLIKE)]
    mock_repository.get_reactions_by_user.return_value = mock_reactions

    result = interaction_service.get_reactions_by_user("user123", ReactionType.DISLIKE)

    assert len(result) == 1
    assert result[0].reaction == ReactionType.DISLIKE
    mock_repository.get_reactions_by_user.assert_called_once_with("user123", ReactionType.DISLIKE)


def test_get_reactions_by_user_empty(interaction_service, mock_repository):
    """Test getting reactions when user has none."""
    mock_repository.get_reactions_by_user.return_value = []

    result = interaction_service.get_reactions_by_user("user123")

    assert len(result) == 0
    mock_repository.get_reactions_by_user.assert_called_once_with("user123", None)


# remove_reactions tests


def test_remove_reactions_success(interaction_service, sample_interaction_with_like, sample_user, mock_repository):
    """Test successfully removing a reaction."""
    mock_repository.get_by_kata_and_user.return_value = sample_interaction_with_like
    mock_repository.set_reaction_value.return_value = True
    mock_repository.get_like_count.return_value = 0
    mock_repository.get_dislike_count.return_value = 0

    with patch("codemie.service.kata_user_interaction_service.AIKata.find_by_id", return_value=None):
        result = interaction_service.remove_reactions("kata123", sample_user)

    assert result.success is True
    assert result.reaction is None
    assert result.like_count == 0
    assert result.dislike_count == 0
    mock_repository.set_reaction_value.assert_called_once_with("kata123", "user123", None)


def test_remove_reactions_no_interaction(interaction_service, sample_user, mock_repository):
    """Test removing reactions when no interaction record exists."""
    mock_repository.get_by_kata_and_user.return_value = None
    mock_repository.get_like_count.return_value = 0
    mock_repository.get_dislike_count.return_value = 0

    result = interaction_service.remove_reactions("kata123", sample_user)

    assert result.success is True
    assert result.reaction is None
    mock_repository.set_reaction_value.assert_not_called()


def test_remove_reactions_no_reaction_set(interaction_service, sample_interaction, sample_user, mock_repository):
    """Test removing reactions when interaction has no reaction."""
    mock_repository.get_by_kata_and_user.return_value = sample_interaction
    mock_repository.get_like_count.return_value = 0
    mock_repository.get_dislike_count.return_value = 0

    result = interaction_service.remove_reactions("kata123", sample_user)

    assert result.success is True
    assert result.reaction is None
    mock_repository.set_reaction_value.assert_not_called()


def test_remove_reactions_set_value_fails(
    interaction_service, sample_interaction_with_like, sample_user, mock_repository
):
    """Test removing reactions when set_reaction_value fails."""
    mock_repository.get_by_kata_and_user.return_value = sample_interaction_with_like
    mock_repository.set_reaction_value.return_value = False

    result = interaction_service.remove_reactions("kata123", sample_user)

    assert result.success is False
    assert result.error == "Failed to remove reaction"
    assert result.reaction == ReactionType.LIKE


def test_remove_reactions_updates_kata_counts(
    interaction_service, sample_interaction_with_like, sample_user, mock_repository
):
    """Test that remove_reactions updates kata denormalized counts."""
    mock_kata = MagicMock(spec=AIKata)
    mock_kata.id = "kata123"
    mock_kata.title = "Test Kata"

    mock_repository.get_by_kata_and_user.return_value = sample_interaction_with_like
    mock_repository.set_reaction_value.return_value = True
    mock_repository.get_like_count.return_value = 3
    mock_repository.get_dislike_count.return_value = 1

    mock_kata_repository = MagicMock()

    with (
        patch("codemie.service.kata_user_interaction_service.AIKata.find_by_id", return_value=mock_kata),
        patch(
            "codemie.repository.ai_kata_repository.SQLAIKataRepository",
            return_value=mock_kata_repository,
        ),
    ):
        result = interaction_service.remove_reactions("kata123", sample_user)

    assert result.success is True
    assert result.like_count == 3
    assert result.dislike_count == 1
    mock_kata_repository.update_reaction_counts.assert_called_once_with(mock_kata, 3, 1)


# Module-level function tests


def test_module_level_manage_reaction(sample_user):
    """Test module-level manage_reaction function."""
    with patch("codemie.service.kata_user_interaction_service.kata_user_interaction_service") as mock_service:
        mock_service.manage_reaction.return_value = ReactionResponse(
            success=True, reaction=ReactionType.LIKE, like_count=1, dislike_count=0
        )

        result = manage_reaction("kata123", sample_user, "like")

        assert result.success is True
        assert result.reaction == ReactionType.LIKE
        mock_service.manage_reaction.assert_called_once_with("kata123", sample_user, "like")


def test_module_level_remove_reactions(sample_user):
    """Test module-level remove_reactions function."""
    with patch("codemie.service.kata_user_interaction_service.kata_user_interaction_service") as mock_service:
        mock_service.remove_reactions.return_value = ReactionResponse(
            success=True, reaction=None, like_count=0, dislike_count=0
        )

        result = remove_reactions("kata123", sample_user)

        assert result.success is True
        assert result.reaction is None
        mock_service.remove_reactions.assert_called_once_with("kata123", sample_user)


def test_module_level_get_reactions_by_user():
    """Test module-level get_reactions_by_user function."""
    with patch("codemie.service.kata_user_interaction_service.kata_user_interaction_service") as mock_service:
        mock_reactions = [MagicMock(kata_id="kata1")]
        mock_service.get_reactions_by_user.return_value = mock_reactions

        result = get_reactions_by_user("user123", ReactionType.LIKE)

        assert len(result) == 1
        mock_service.get_reactions_by_user.assert_called_once_with("user123", ReactionType.LIKE)


# Helper method tests


def test_get_current_reaction_state(interaction_service, mock_repository):
    """Test _get_current_reaction_state helper method."""
    mock_repository.get_like_count.return_value = 5
    mock_repository.get_dislike_count.return_value = 3

    result = interaction_service._get_current_reaction_state("kata123", ReactionType.LIKE)

    assert result.success is True
    assert result.reaction == ReactionType.LIKE
    assert result.like_count == 5
    assert result.dislike_count == 3


def test_update_reaction_counts_no_kata(interaction_service, sample_user, mock_repository):
    """Test _update_reaction_counts when kata doesn't exist."""
    mock_repository.get_like_count.return_value = 2
    mock_repository.get_dislike_count.return_value = 1

    with patch("codemie.service.kata_user_interaction_service.AIKata.find_by_id", return_value=None):
        result = interaction_service._update_reaction_counts("kata123", ReactionType.LIKE, sample_user)

    assert result.success is True
    assert result.like_count == 2
    assert result.dislike_count == 1


# Service initialization tests


def test_service_initialization_with_repository():
    """Test service initializes with provided repository."""
    mock_repo = MagicMock(spec=KataUserInteractionRepository)
    service = KataUserInteractionService(repository=mock_repo)

    assert service.repository is mock_repo


def test_service_initialization_without_repository():
    """Test service initializes with default repository."""
    service = KataUserInteractionService()

    assert service.repository is not None
    from codemie.repository.kata_user_interaction_repository import KataUsageRepositoryImpl

    assert isinstance(service.repository, KataUsageRepositoryImpl)
