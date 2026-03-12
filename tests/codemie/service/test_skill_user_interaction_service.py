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
Unit tests for SkillUserInteractionService.

Tests business logic for skill reactions and usage tracking.
"""

from unittest.mock import MagicMock, patch

import pytest

from codemie.rest_api.models.usage.skill_user_interaction import ReactionType
from codemie.service.skill_user_interaction_service import (
    SkillUserInteractionService,
    ReactionResponse,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_repository():
    """Mock SkillUserInteractionRepository"""
    return MagicMock()


@pytest.fixture
def mock_skill_repo():
    """Mock SkillRepository"""
    return MagicMock()


@pytest.fixture
def service(mock_repository, mock_skill_repo):
    """Create service with mocked dependencies"""
    return SkillUserInteractionService(repository=mock_repository, skill_repo=mock_skill_repo)


@pytest.fixture
def mock_interaction():
    """Create a mock interaction record"""
    interaction = MagicMock()
    interaction.skill_id = "skill-123"
    interaction.user_id = "user-123"
    interaction.reaction = None
    return interaction


# =============================================================================
# Record Usage Tests
# =============================================================================


class TestRecordUsage:
    """Test record_usage method"""

    def test_record_usage_success(self, service, mock_repository):
        # Arrange
        mock_record = MagicMock()
        mock_record.skill_id = "skill-123"
        mock_repository.record_usage.return_value = mock_record

        # Act
        result = service.record_usage("skill-123", "user-123", "project-a")

        # Assert
        mock_repository.record_usage.assert_called_once_with("skill-123", "user-123", "project-a")
        assert result == mock_record

    def test_record_usage_without_project(self, service, mock_repository):
        # Arrange
        mock_record = MagicMock()
        mock_repository.record_usage.return_value = mock_record

        # Act
        result = service.record_usage("skill-123", "user-123")

        # Assert
        mock_repository.record_usage.assert_called_once_with("skill-123", "user-123", None)
        assert result == mock_record

    def test_record_usage_handles_exception(self, service, mock_repository):
        # Arrange
        mock_repository.record_usage.side_effect = Exception("Database error")

        # Act
        result = service.record_usage("skill-123", "user-123")

        # Assert - should return None and not raise exception
        assert result is None


# =============================================================================
# Manage Reaction Tests
# =============================================================================


class TestManageReaction:
    """Test manage_reaction method"""

    def test_manage_reaction_add_like(self, service, mock_repository, mock_skill_repo, mock_interaction):
        # Arrange - no existing reaction
        mock_interaction.reaction = None
        mock_repository.get_by_skill_and_user.return_value = mock_interaction
        mock_repository.set_reaction_value.return_value = True
        mock_repository.get_like_count.return_value = 5
        mock_repository.get_dislike_count.return_value = 2

        # Act
        result = service.manage_reaction("skill-123", "user-123", "like")

        # Assert
        mock_repository.set_reaction_value.assert_called_once_with("skill-123", "user-123", ReactionType.LIKE)
        mock_skill_repo.update_reaction_counts.assert_called_once_with("skill-123", 5, 2)
        assert result.success is True
        assert result.reaction == ReactionType.LIKE
        assert result.like_count == 5
        assert result.dislike_count == 2

    def test_manage_reaction_add_dislike(self, service, mock_repository, mock_skill_repo, mock_interaction):
        # Arrange - no existing reaction
        mock_interaction.reaction = None
        mock_repository.get_by_skill_and_user.return_value = mock_interaction
        mock_repository.set_reaction_value.return_value = True
        mock_repository.get_like_count.return_value = 3
        mock_repository.get_dislike_count.return_value = 7

        # Act
        result = service.manage_reaction("skill-123", "user-123", "dislike")

        # Assert
        mock_repository.set_reaction_value.assert_called_once_with("skill-123", "user-123", ReactionType.DISLIKE)
        assert result.success is True
        assert result.reaction == ReactionType.DISLIKE
        assert result.like_count == 3
        assert result.dislike_count == 7

    def test_manage_reaction_toggle_off_like(self, service, mock_repository, mock_skill_repo, mock_interaction):
        # Arrange - user already liked, clicking like again should toggle off
        mock_interaction.reaction = ReactionType.LIKE
        mock_repository.get_by_skill_and_user.return_value = mock_interaction
        mock_repository.set_reaction_value.return_value = True
        mock_repository.get_like_count.return_value = 4
        mock_repository.get_dislike_count.return_value = 2

        # Act
        result = service.manage_reaction("skill-123", "user-123", "like")

        # Assert - should set reaction to None
        mock_repository.set_reaction_value.assert_called_once_with("skill-123", "user-123", None)
        assert result.success is True
        assert result.reaction is None
        assert result.like_count == 4

    def test_manage_reaction_switch_like_to_dislike(self, service, mock_repository, mock_skill_repo, mock_interaction):
        # Arrange - user liked, now clicking dislike
        mock_interaction.reaction = ReactionType.LIKE
        mock_repository.get_by_skill_and_user.return_value = mock_interaction
        mock_repository.set_reaction_value.return_value = True
        mock_repository.get_like_count.return_value = 3
        mock_repository.get_dislike_count.return_value = 5

        # Act
        result = service.manage_reaction("skill-123", "user-123", "dislike")

        # Assert
        mock_repository.set_reaction_value.assert_called_once_with("skill-123", "user-123", ReactionType.DISLIKE)
        assert result.success is True
        assert result.reaction == ReactionType.DISLIKE

    def test_manage_reaction_invalid_type(self, service, mock_repository):
        # Act
        result = service.manage_reaction("skill-123", "user-123", "invalid")

        # Assert
        assert result.success is False
        assert result.reaction is None
        assert result.error == "Invalid reaction type"
        mock_repository.set_reaction_value.assert_not_called()

    def test_manage_reaction_database_failure(self, service, mock_repository, mock_interaction):
        # Arrange
        mock_interaction.reaction = None
        mock_repository.get_by_skill_and_user.return_value = mock_interaction
        mock_repository.set_reaction_value.return_value = False

        # Act
        result = service.manage_reaction("skill-123", "user-123", "like")

        # Assert
        assert result.success is False
        assert result.error == "Failed to update reaction"

    def test_manage_reaction_no_existing_interaction(self, service, mock_repository, mock_skill_repo):
        # Arrange - no existing interaction record
        mock_repository.get_by_skill_and_user.return_value = None
        mock_repository.set_reaction_value.return_value = True
        mock_repository.get_like_count.return_value = 1
        mock_repository.get_dislike_count.return_value = 0

        # Act
        result = service.manage_reaction("skill-123", "user-123", "like")

        # Assert
        mock_repository.set_reaction_value.assert_called_once_with("skill-123", "user-123", ReactionType.LIKE)
        assert result.success is True
        assert result.reaction == ReactionType.LIKE


# =============================================================================
# Remove Reactions Tests
# =============================================================================


class TestRemoveReactions:
    """Test remove_reactions method"""

    def test_remove_reactions_success(self, service, mock_repository, mock_skill_repo, mock_interaction):
        # Arrange
        mock_interaction.reaction = ReactionType.LIKE
        mock_repository.get_by_skill_and_user.return_value = mock_interaction
        mock_repository.set_reaction_value.return_value = True
        mock_repository.get_like_count.return_value = 3
        mock_repository.get_dislike_count.return_value = 2

        # Act
        result = service.remove_reactions("skill-123", "user-123")

        # Assert
        mock_repository.set_reaction_value.assert_called_once_with("skill-123", "user-123", None)
        mock_skill_repo.update_reaction_counts.assert_called_once_with("skill-123", 3, 2)
        assert result.success is True
        assert result.reaction is None

    def test_remove_reactions_no_existing_reaction(self, service, mock_repository, mock_interaction):
        # Arrange - no reaction to remove
        mock_interaction.reaction = None
        mock_repository.get_by_skill_and_user.return_value = mock_interaction
        mock_repository.get_like_count.return_value = 5
        mock_repository.get_dislike_count.return_value = 3

        # Act
        result = service.remove_reactions("skill-123", "user-123")

        # Assert - should not call set_reaction_value
        mock_repository.set_reaction_value.assert_not_called()
        assert result.success is True
        assert result.reaction is None
        assert result.like_count == 5
        assert result.dislike_count == 3

    def test_remove_reactions_no_interaction_record(self, service, mock_repository):
        # Arrange - no interaction record exists
        mock_repository.get_by_skill_and_user.return_value = None
        mock_repository.get_like_count.return_value = 0
        mock_repository.get_dislike_count.return_value = 0

        # Act
        result = service.remove_reactions("skill-123", "user-123")

        # Assert
        mock_repository.set_reaction_value.assert_not_called()
        assert result.success is True
        assert result.reaction is None

    def test_remove_reactions_database_failure(self, service, mock_repository, mock_interaction):
        # Arrange
        mock_interaction.reaction = ReactionType.LIKE
        mock_repository.get_by_skill_and_user.return_value = mock_interaction
        mock_repository.set_reaction_value.return_value = False

        # Act
        result = service.remove_reactions("skill-123", "user-123")

        # Assert
        assert result.success is False
        assert result.reaction == ReactionType.LIKE
        assert result.error == "Failed to remove reaction"


# =============================================================================
# Get Reactions Tests
# =============================================================================


class TestGetReactionsByUser:
    """Test get_reactions_by_user method"""

    def test_get_reactions_by_user_all(self, service, mock_repository):
        # Arrange
        mock_reactions = [MagicMock(), MagicMock()]
        mock_repository.get_reactions_by_user.return_value = mock_reactions

        # Act
        result = service.get_reactions_by_user("user-123")

        # Assert
        mock_repository.get_reactions_by_user.assert_called_once_with("user-123", None)
        assert result == mock_reactions

    def test_get_reactions_by_user_likes_only(self, service, mock_repository):
        # Arrange
        mock_reactions = [MagicMock()]
        mock_repository.get_reactions_by_user.return_value = mock_reactions

        # Act
        result = service.get_reactions_by_user("user-123", ReactionType.LIKE)

        # Assert
        mock_repository.get_reactions_by_user.assert_called_once_with("user-123", ReactionType.LIKE)
        assert result == mock_reactions

    def test_get_reactions_by_user_dislikes_only(self, service, mock_repository):
        # Arrange
        mock_reactions = [MagicMock()]
        mock_repository.get_reactions_by_user.return_value = mock_reactions

        # Act
        result = service.get_reactions_by_user("user-123", ReactionType.DISLIKE)

        # Assert
        mock_repository.get_reactions_by_user.assert_called_once_with("user-123", ReactionType.DISLIKE)
        assert result == mock_reactions


# =============================================================================
# Module-Level Function Tests
# =============================================================================


class TestModuleLevelFunctions:
    """Test module-level wrapper functions"""

    @patch("codemie.service.skill_user_interaction_service.skill_user_interaction_service")
    def test_module_manage_reaction(self, mock_singleton):
        """Test module-level manage_reaction delegates to singleton"""
        from codemie.service.skill_user_interaction_service import manage_reaction

        # Arrange
        mock_response = ReactionResponse(success=True, reaction=ReactionType.LIKE, like_count=5, dislike_count=2)
        mock_singleton.manage_reaction.return_value = mock_response

        # Act
        result = manage_reaction("skill-123", "user-123", "like")

        # Assert
        mock_singleton.manage_reaction.assert_called_once_with("skill-123", "user-123", "like")
        assert result == mock_response

    @patch("codemie.service.skill_user_interaction_service.skill_user_interaction_service")
    def test_module_remove_reactions(self, mock_singleton):
        """Test module-level remove_reactions delegates to singleton"""
        from codemie.service.skill_user_interaction_service import remove_reactions

        # Arrange
        mock_response = ReactionResponse(success=True, reaction=None, like_count=4, dislike_count=2)
        mock_singleton.remove_reactions.return_value = mock_response

        # Act
        result = remove_reactions("skill-123", "user-123")

        # Assert
        mock_singleton.remove_reactions.assert_called_once_with("skill-123", "user-123")
        assert result == mock_response

    @patch("codemie.service.skill_user_interaction_service.skill_user_interaction_service")
    def test_module_get_reactions_by_user(self, mock_singleton):
        """Test module-level get_reactions_by_user delegates to singleton"""
        from codemie.service.skill_user_interaction_service import get_reactions_by_user

        # Arrange
        mock_reactions = [MagicMock(), MagicMock()]
        mock_singleton.get_reactions_by_user.return_value = mock_reactions

        # Act
        result = get_reactions_by_user("user-123", ReactionType.LIKE)

        # Assert
        mock_singleton.get_reactions_by_user.assert_called_once_with("user-123", ReactionType.LIKE)
        assert result == mock_reactions


# =============================================================================
# Singleton Instance Test
# =============================================================================


def test_singleton_instance():
    """Test that skill_user_interaction_service singleton is properly instantiated"""
    from codemie.service.skill_user_interaction_service import skill_user_interaction_service

    assert isinstance(skill_user_interaction_service, SkillUserInteractionService)
