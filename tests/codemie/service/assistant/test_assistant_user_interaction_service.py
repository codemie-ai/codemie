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
Tests for the AssistantUserInterationService
"""

from unittest.mock import MagicMock, patch

import pytest

from codemie.rest_api.models.usage.assistant_user_interaction import ReactionType, AssistantUserInterationSQL
from codemie.service.assistant.assistant_repository import AssistantRepository
from codemie.service.assistant.assistant_user_interaction_service import (
    AssistantUserInterationService,
    ReactionResponse,
)


@pytest.fixture
def mock_repository():
    """Mock repository for testing."""
    repository = MagicMock()
    # Set up the specific methods that we'll use in our tests
    repository.get_like_count = MagicMock()
    repository.get_dislike_count = MagicMock()
    repository.get_by_assistant_and_user = MagicMock()
    repository.get_reactions_by_user = MagicMock()
    repository.set_reaction_value = MagicMock()
    return repository


@pytest.fixture
def mock_assistant_repo():
    """Mock assistant repository for testing."""
    assistant_repo = MagicMock(spec=AssistantRepository)
    return assistant_repo


@pytest.fixture
def service(mock_repository, mock_assistant_repo):
    """Create service instance with mocked dependencies."""
    return AssistantUserInterationService(repository=mock_repository, assistant_repo=mock_assistant_repo)


@pytest.fixture
def test_data():
    """Test data for use across tests."""
    return {
        "assistant_id": "test-assistant-id",
        "reaction": ReactionType.LIKE,
        "like_count": 5,
        "dislike_count": 2,
        "user_id": "test-user-id",
    }


@pytest.fixture
def mock_assistant():
    """Mock assistant object."""
    assistant = MagicMock()
    assistant.id = "test-assistant-id"
    assistant.name = "Test Assistant"
    assistant.project = "test-project"
    assistant.slug = "test-slug"
    return assistant


@pytest.fixture
def mock_interaction():
    """Mock interaction object with a reaction."""
    interaction = MagicMock(spec=AssistantUserInterationSQL)
    interaction.reaction = ReactionType.LIKE
    return interaction


def test_update_reaction_counts_with_reaction(service, mock_repository, mock_assistant_repo, test_data, mock_assistant):
    """Test _update_reaction_counts with a reaction value."""
    # Configure repository mock return values
    mock_repository.get_like_count.return_value = test_data["like_count"]
    mock_repository.get_dislike_count.return_value = test_data["dislike_count"]

    # Configure Assistant.find_by_id mock
    with patch('codemie.rest_api.models.assistant.Assistant.find_by_id', return_value=mock_assistant):
        # Call the method
        result = service._update_reaction_counts(test_data["assistant_id"], test_data["reaction"])

    # Verify repository methods were called
    mock_repository.get_like_count.assert_called_once_with(test_data["assistant_id"])
    mock_repository.get_dislike_count.assert_called_once_with(test_data["assistant_id"])

    # Verify assistant was retrieved and updated
    mock_assistant_repo.update_reaction_counts.assert_called_once_with(
        mock_assistant, test_data["like_count"], test_data["dislike_count"]
    )

    # Verify the result
    assert isinstance(result, ReactionResponse)
    assert result.success is True
    assert result.reaction == test_data["reaction"]
    assert result.like_count == test_data["like_count"]
    assert result.dislike_count == test_data["dislike_count"]


def test_update_reaction_counts_with_no_reaction(
    service, mock_repository, mock_assistant_repo, test_data, mock_assistant
):
    """Test _update_reaction_counts with no reaction value."""
    # Configure repository mock return values
    mock_repository.get_like_count.return_value = test_data["like_count"]
    mock_repository.get_dislike_count.return_value = test_data["dislike_count"]

    # Configure Assistant.find_by_id mock
    with patch('codemie.rest_api.models.assistant.Assistant.find_by_id', return_value=mock_assistant):
        # Call the method with None reaction
        result = service._update_reaction_counts(test_data["assistant_id"], None)

    # Verify repository methods were called
    mock_repository.get_like_count.assert_called_once_with(test_data["assistant_id"])
    mock_repository.get_dislike_count.assert_called_once_with(test_data["assistant_id"])

    # Verify assistant was retrieved and updated
    mock_assistant_repo.update_reaction_counts.assert_called_once_with(
        mock_assistant, test_data["like_count"], test_data["dislike_count"]
    )

    # Verify the result
    assert isinstance(result, ReactionResponse)
    assert result.success is True
    assert result.reaction is None
    assert result.like_count == test_data["like_count"]
    assert result.dislike_count == test_data["dislike_count"]


def test_update_reaction_counts_assistant_not_found(service, mock_repository, mock_assistant_repo, test_data):
    """Test _update_reaction_counts when assistant is not found."""
    # Configure repository mock return values
    mock_repository.get_like_count.return_value = test_data["like_count"]
    mock_repository.get_dislike_count.return_value = test_data["dislike_count"]

    # Configure Assistant.find_by_id to return None (assistant not found)
    with patch('codemie.rest_api.models.assistant.Assistant.find_by_id', return_value=None):
        # Call the method
        result = service._update_reaction_counts(test_data["assistant_id"], test_data["reaction"])

    # Verify repository methods were called
    mock_repository.get_like_count.assert_called_once_with(test_data["assistant_id"])
    mock_repository.get_dislike_count.assert_called_once_with(test_data["assistant_id"])

    # Verify update_reaction_counts was NOT called
    mock_assistant_repo.update_reaction_counts.assert_not_called()

    # Verify the result
    assert isinstance(result, ReactionResponse)
    assert result.success is True
    assert result.reaction == test_data["reaction"]
    assert result.like_count == test_data["like_count"]
    assert result.dislike_count == test_data["dislike_count"]


def test_get_reactions_by_user(service, mock_repository, test_data):
    """Test get_reactions_by_user method."""
    # Mock data for reaction records
    user_id = test_data["user_id"]
    mock_reactions = [
        MagicMock(spec=AssistantUserInterationSQL, assistant_id="assistant-1", reaction=ReactionType.LIKE),
        MagicMock(spec=AssistantUserInterationSQL, assistant_id="assistant-2", reaction=ReactionType.DISLIKE),
    ]

    # Configure repository mock return value
    mock_repository.get_reactions_by_user.return_value = mock_reactions

    # Call the method without reaction type filter
    result = service.get_reactions_by_user(user_id)

    # Verify repository method was called with correct parameters
    mock_repository.get_reactions_by_user.assert_called_once_with(user_id, None)

    # Verify the result
    assert result == mock_reactions
    assert len(result) == 2

    # Reset the mock for next test
    mock_repository.get_reactions_by_user.reset_mock()

    # Test with reaction type filter
    reaction_type = ReactionType.LIKE
    mock_repository.get_reactions_by_user.return_value = [mock_reactions[0]]  # Only the LIKE reaction

    # Call the method with reaction type filter
    filtered_result = service.get_reactions_by_user(user_id, reaction_type)

    # Verify repository method was called with correct parameters
    mock_repository.get_reactions_by_user.assert_called_once_with(user_id, reaction_type)

    # Verify the filtered result
    assert len(filtered_result) == 1
    assert filtered_result == [mock_reactions[0]]


def test_remove_reactions_with_existing_reaction(
    service, mock_repository, mock_assistant_repo, test_data, mock_assistant, mock_interaction
):
    """Test remove_reactions when there is an existing reaction."""
    assistant_id = test_data["assistant_id"]
    user_id = test_data["user_id"]
    like_count = test_data["like_count"]
    dislike_count = test_data["dislike_count"]

    # Configure repository mock return values
    mock_repository.get_by_assistant_and_user.return_value = mock_interaction
    mock_repository.set_reaction_value.return_value = True
    mock_repository.get_like_count.return_value = like_count
    mock_repository.get_dislike_count.return_value = dislike_count

    # Configure Assistant.find_by_id mock
    with patch('codemie.rest_api.models.assistant.Assistant.find_by_id', return_value=mock_assistant):
        with patch(
            'codemie.service.monitoring.agent_monitoring_service.AgentMonitoringService.track_reaction_metric'
        ) as mock_track:
            # Call the method
            result = service.remove_reactions(assistant_id, user_id)

    # Verify repository methods were called with correct parameters
    mock_repository.get_by_assistant_and_user.assert_called_once_with(assistant_id, user_id)
    mock_repository.set_reaction_value.assert_called_once_with(assistant_id, user_id, None)
    mock_repository.get_like_count.assert_called_once_with(assistant_id)
    mock_repository.get_dislike_count.assert_called_once_with(assistant_id)

    # Verify tracking was called
    mock_track.assert_called_once()

    # Verify assistant was updated
    mock_assistant_repo.update_reaction_counts.assert_called_once_with(mock_assistant, like_count, dislike_count)

    # Verify the result
    assert isinstance(result, ReactionResponse)
    assert result.success is True
    assert result.reaction is None
    assert result.like_count == like_count
    assert result.dislike_count == dislike_count


def test_remove_reactions_with_no_interaction(service, mock_repository, test_data, mock_assistant):
    """Test remove_reactions when there is no existing interaction."""
    assistant_id = test_data["assistant_id"]
    user_id = test_data["user_id"]
    like_count = test_data["like_count"]
    dislike_count = test_data["dislike_count"]

    # Configure repository mock return values
    mock_repository.get_by_assistant_and_user.return_value = None  # No interaction exists
    mock_repository.get_like_count.return_value = like_count
    mock_repository.get_dislike_count.return_value = dislike_count

    # Configure Assistant.find_by_id mock
    with patch('codemie.rest_api.models.assistant.Assistant.find_by_id', return_value=mock_assistant):
        # Call the method
        result = service.remove_reactions(assistant_id, user_id)

    # Verify repository methods were called
    mock_repository.get_by_assistant_and_user.assert_called_once_with(assistant_id, user_id)
    # Verify set_reaction_value was NOT called since there's no interaction
    mock_repository.set_reaction_value.assert_not_called()

    # Verify the result
    assert isinstance(result, ReactionResponse)
    assert result.success is True
    assert result.reaction is None
    assert result.like_count == like_count
    assert result.dislike_count == dislike_count


def test_remove_reactions_with_no_reaction(service, mock_repository, test_data, mock_assistant):
    """Test remove_reactions when there is an interaction but no reaction."""
    assistant_id = test_data["assistant_id"]
    user_id = test_data["user_id"]
    like_count = test_data["like_count"]
    dislike_count = test_data["dislike_count"]

    # Create an interaction with no reaction
    interaction_no_reaction = MagicMock(spec=AssistantUserInterationSQL)
    interaction_no_reaction.reaction = None

    # Configure repository mock return values
    mock_repository.get_by_assistant_and_user.return_value = interaction_no_reaction
    mock_repository.get_like_count.return_value = like_count
    mock_repository.get_dislike_count.return_value = dislike_count

    # Configure Assistant.find_by_id mock
    with patch('codemie.rest_api.models.assistant.Assistant.find_by_id', return_value=mock_assistant):
        # Call the method
        result = service.remove_reactions(assistant_id, user_id)

    # Verify repository methods were called
    mock_repository.get_by_assistant_and_user.assert_called_once_with(assistant_id, user_id)
    # Verify set_reaction_value was NOT called since there's no reaction to remove
    mock_repository.set_reaction_value.assert_not_called()

    # Verify the result
    assert isinstance(result, ReactionResponse)
    assert result.success is True
    assert result.reaction is None
    assert result.like_count == like_count
    assert result.dislike_count == dislike_count


def test_remove_reactions_failure(service, mock_repository, test_data, mock_assistant, mock_interaction):
    """Test remove_reactions when the repository update fails."""
    assistant_id = test_data["assistant_id"]
    user_id = test_data["user_id"]

    # Configure repository mock return values
    mock_repository.get_by_assistant_and_user.return_value = mock_interaction
    mock_repository.set_reaction_value.return_value = False  # Update fails

    # Configure Assistant.find_by_id mock
    with patch('codemie.rest_api.models.assistant.Assistant.find_by_id', return_value=mock_assistant):
        with patch(
            'codemie.service.monitoring.agent_monitoring_service.AgentMonitoringService.track_reaction_metric'
        ) as mock_track:
            # Call the method
            result = service.remove_reactions(assistant_id, user_id)

    # Verify repository methods were called
    mock_repository.get_by_assistant_and_user.assert_called_once_with(assistant_id, user_id)
    mock_repository.set_reaction_value.assert_called_once_with(assistant_id, user_id, None)

    # Verify tracking was called with failure
    mock_track.assert_called_once()

    # Verify the result
    assert isinstance(result, ReactionResponse)
    assert result.success is False
    assert result.reaction == mock_interaction.reaction  # Should maintain original reaction
    assert result.like_count == 0
    assert result.dislike_count == 0
    assert result.error == "Failed to remove reaction"


def test_remove_reactions_assistant_not_found(service, mock_repository, test_data, mock_interaction):
    """Test remove_reactions when the assistant is not found."""
    assistant_id = test_data["assistant_id"]
    user_id = test_data["user_id"]
    like_count = test_data["like_count"]
    dislike_count = test_data["dislike_count"]

    # Configure repository mock return values
    mock_repository.get_by_assistant_and_user.return_value = mock_interaction
    mock_repository.set_reaction_value.return_value = True
    mock_repository.get_like_count.return_value = like_count
    mock_repository.get_dislike_count.return_value = dislike_count

    # Configure Assistant.find_by_id to return None (assistant not found)
    with patch('codemie.rest_api.models.assistant.Assistant.find_by_id', return_value=None):
        with patch(
            'codemie.service.monitoring.agent_monitoring_service.AgentMonitoringService.track_reaction_metric'
        ) as mock_track:
            # Call the method
            result = service.remove_reactions(assistant_id, user_id)

    # Verify repository methods were called
    mock_repository.get_by_assistant_and_user.assert_called_once_with(assistant_id, user_id)
    mock_repository.set_reaction_value.assert_called_once_with(assistant_id, user_id, None)

    # Verify tracking was NOT called since there's no assistant
    mock_track.assert_not_called()

    # Verify the result
    assert isinstance(result, ReactionResponse)
    assert result.success is True
    assert result.reaction is None
    assert result.like_count == like_count
    assert result.dislike_count == dislike_count


def test_manage_reaction_with_new_reaction(service, mock_repository, mock_assistant_repo, test_data, mock_assistant):
    """Test manage_reaction when setting a new reaction."""
    assistant_id = test_data["assistant_id"]
    user_id = test_data["user_id"]
    reaction_type = "like"
    like_count = test_data["like_count"]
    dislike_count = test_data["dislike_count"]

    # Configure repository mock return values
    mock_repository.get_by_assistant_and_user.return_value = None  # No previous interaction
    mock_repository.set_reaction_value.return_value = True
    mock_repository.get_like_count.return_value = like_count
    mock_repository.get_dislike_count.return_value = dislike_count

    # Configure Assistant.find_by_id mock
    with patch('codemie.rest_api.models.assistant.Assistant.find_by_id', return_value=mock_assistant):
        with patch(
            'codemie.service.monitoring.agent_monitoring_service.AgentMonitoringService.track_reaction_metric'
        ) as mock_track:
            # Call the method
            result = service.manage_reaction(assistant_id, user_id, reaction_type)

    # Verify repository methods were called
    mock_repository.get_by_assistant_and_user.assert_called_once_with(assistant_id, user_id)
    mock_repository.set_reaction_value.assert_called_once_with(assistant_id, user_id, ReactionType.LIKE)
    mock_repository.get_like_count.assert_called_once()
    mock_repository.get_dislike_count.assert_called_once()

    # Verify tracking was called
    mock_track.assert_called_once()

    # Verify assistant was updated
    mock_assistant_repo.update_reaction_counts.assert_called_once_with(mock_assistant, like_count, dislike_count)

    # Verify the result
    assert isinstance(result, ReactionResponse)
    assert result.success is True
    assert result.reaction == ReactionType.LIKE
    assert result.like_count == like_count
    assert result.dislike_count == dislike_count


def test_manage_reaction_toggle_off_existing_reaction(
    service, mock_repository, mock_assistant_repo, test_data, mock_assistant
):
    """Test manage_reaction when toggling off an existing reaction."""
    assistant_id = test_data["assistant_id"]
    user_id = test_data["user_id"]
    reaction_type = "like"
    like_count = test_data["like_count"]
    dislike_count = test_data["dislike_count"]

    # Create interaction with existing like
    interaction = MagicMock(spec=AssistantUserInterationSQL)
    interaction.reaction = ReactionType.LIKE

    # Configure repository mock return values
    mock_repository.get_by_assistant_and_user.return_value = interaction
    mock_repository.set_reaction_value.return_value = True
    mock_repository.get_like_count.return_value = like_count
    mock_repository.get_dislike_count.return_value = dislike_count

    # Configure Assistant.find_by_id mock
    with patch('codemie.rest_api.models.assistant.Assistant.find_by_id', return_value=mock_assistant):
        with patch(
            'codemie.service.monitoring.agent_monitoring_service.AgentMonitoringService.track_reaction_metric'
        ) as mock_track:
            # Call the method with the same reaction (should toggle off)
            result = service.manage_reaction(assistant_id, user_id, reaction_type)

    # Verify repository methods were called
    mock_repository.get_by_assistant_and_user.assert_called_once_with(assistant_id, user_id)
    mock_repository.set_reaction_value.assert_called_once_with(assistant_id, user_id, None)

    # Verify tracking was called
    mock_track.assert_called_once()

    # Verify the result
    assert isinstance(result, ReactionResponse)
    assert result.success is True
    assert result.reaction is None  # Toggled off
    assert result.like_count == like_count
    assert result.dislike_count == dislike_count


def test_manage_reaction_change_existing_reaction(
    service, mock_repository, mock_assistant_repo, test_data, mock_assistant
):
    """Test manage_reaction when changing from one reaction to another."""
    assistant_id = test_data["assistant_id"]
    user_id = test_data["user_id"]
    like_count = test_data["like_count"]
    dislike_count = test_data["dislike_count"]

    # Create interaction with existing dislike
    interaction = MagicMock(spec=AssistantUserInterationSQL)
    interaction.reaction = ReactionType.DISLIKE

    # Configure repository mock return values
    mock_repository.get_by_assistant_and_user.return_value = interaction
    mock_repository.set_reaction_value.return_value = True
    mock_repository.get_like_count.return_value = like_count
    mock_repository.get_dislike_count.return_value = dislike_count

    # Configure Assistant.find_by_id mock
    with patch('codemie.rest_api.models.assistant.Assistant.find_by_id', return_value=mock_assistant):
        with patch(
            'codemie.service.monitoring.agent_monitoring_service.AgentMonitoringService.track_reaction_metric'
        ) as mock_track:
            # Call the method with a different reaction type
            result = service.manage_reaction(assistant_id, user_id, "like")

    # Verify repository methods were called
    mock_repository.get_by_assistant_and_user.assert_called_once_with(assistant_id, user_id)
    mock_repository.set_reaction_value.assert_called_once_with(assistant_id, user_id, ReactionType.LIKE)

    # Verify tracking was called
    mock_track.assert_called_once()

    # Verify the result
    assert isinstance(result, ReactionResponse)
    assert result.success is True
    assert result.reaction == ReactionType.LIKE  # Changed to like
    assert result.like_count == like_count
    assert result.dislike_count == dislike_count


def test_manage_reaction_invalid_reaction_type(service, mock_repository, test_data, mock_assistant):
    """Test manage_reaction with an invalid reaction type."""
    assistant_id = test_data["assistant_id"]
    user_id = test_data["user_id"]
    invalid_reaction_type = "invalid"

    # Configure Assistant.find_by_id mock
    with patch('codemie.rest_api.models.assistant.Assistant.find_by_id', return_value=mock_assistant):
        with patch(
            'codemie.service.monitoring.agent_monitoring_service.AgentMonitoringService.track_reaction_metric'
        ) as mock_track:
            # Call the method with an invalid reaction type
            result = service.manage_reaction(assistant_id, user_id, invalid_reaction_type)

    # Verify repository methods were NOT called
    mock_repository.get_by_assistant_and_user.assert_not_called()
    mock_repository.set_reaction_value.assert_not_called()

    # Verify tracking was called with error
    mock_track.assert_called_once()

    # Verify the result
    assert isinstance(result, ReactionResponse)
    assert result.success is False
    assert result.reaction is None
    assert result.like_count == 0
    assert result.dislike_count == 0
    assert result.error == "Invalid reaction type"


def test_manage_reaction_database_failure(service, mock_repository, test_data, mock_assistant):
    """Test manage_reaction when the database update fails."""
    assistant_id = test_data["assistant_id"]
    user_id = test_data["user_id"]
    reaction_type = "like"

    # Configure repository mock return values
    mock_repository.get_by_assistant_and_user.return_value = None
    mock_repository.set_reaction_value.return_value = False  # Database update fails

    # Configure Assistant.find_by_id mock
    with patch('codemie.rest_api.models.assistant.Assistant.find_by_id', return_value=mock_assistant):
        with patch(
            'codemie.service.monitoring.agent_monitoring_service.AgentMonitoringService.track_reaction_metric'
        ) as mock_track:
            # Call the method
            result = service.manage_reaction(assistant_id, user_id, reaction_type)

    # Verify repository methods were called
    mock_repository.get_by_assistant_and_user.assert_called_once_with(assistant_id, user_id)
    mock_repository.set_reaction_value.assert_called_once_with(assistant_id, user_id, ReactionType.LIKE)

    # Verify tracking was called with failure
    mock_track.assert_called_once()

    # Verify the result
    assert isinstance(result, ReactionResponse)
    assert result.success is False
    assert result.reaction is None  # No previous reaction
    assert result.like_count == 0
    assert result.dislike_count == 0
    assert result.error == "Failed to update reaction"


def test_manage_reaction_assistant_not_found(service, mock_repository, test_data):
    """Test manage_reaction when the assistant is not found."""
    assistant_id = test_data["assistant_id"]
    user_id = test_data["user_id"]
    reaction_type = "like"

    # Configure Assistant.find_by_id to return None (assistant not found)
    with patch('codemie.rest_api.models.assistant.Assistant.find_by_id', return_value=None):
        with patch(
            'codemie.service.monitoring.agent_monitoring_service.AgentMonitoringService.track_reaction_metric'
        ) as mock_track:
            # Call the method
            service.manage_reaction(assistant_id, user_id, reaction_type)

    # Verify repository methods were called (we should still try to update)
    mock_repository.get_by_assistant_and_user.assert_called_once_with(assistant_id, user_id)
    mock_repository.set_reaction_value.assert_called_once_with(assistant_id, user_id, ReactionType.LIKE)

    # Verify tracking was NOT called since there's no assistant
    mock_track.assert_not_called()
