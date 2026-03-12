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
Service for managing assistant usage data.
"""

from dataclasses import dataclass
from typing import Optional, List, Any

from codemie.rest_api.models.usage.assistant_user_interaction import ReactionType

from codemie.configs import logger
from codemie.repository.assistants.assistant_user_interaction_repository import (
    AssistantUserInterationRepository,
    AssistantUsageRepositoryImpl,
)
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_repository import AssistantRepository
from codemie.service.monitoring.agent_monitoring_service import AgentMonitoringService


@dataclass
class ReactionResponse:
    """Response model for assistant reactions"""

    success: bool
    reaction: Optional[ReactionType]
    like_count: int
    dislike_count: int
    error: Optional[str] = None


class AssistantUserInterationService:
    """
    Service for managing assistant usage data.
    Provides business logic for tracking and retrieving assistant usage information.
    """

    def __init__(
        self,
        repository: Optional[AssistantUserInterationRepository] = None,
        assistant_repo: Optional[AssistantRepository] = None,
    ):
        """
        Initialize the service with a repository.

        Args:
            repository: Repository implementation to use. If None, uses the default implementation.
        """
        self.repository = repository or AssistantUsageRepositoryImpl()
        self.assistant_repo = assistant_repo or AssistantRepository()

    def record_usage(self, assistant: Assistant, user: User) -> Any:
        """
        Record a usage of an assistant by a user.

        Args:
            assistant: The assistant being used
            user: The user performing the action

        Returns:
            The updated or created usage record
        """
        try:
            logger.debug(f"Recording usage for assistant {assistant.id} by user {user.id}")
            usage_record = self.repository.record_usage(assistant.id, user.id, assistant.project)
            if assistant.unique_users_count is None or assistant.unique_users_count == 0:
                self.assistant_repo.increment_usage_count(assistant)
            else:
                unique_users_count = self.repository.get_unique_users_count(assistant.id)
                self.assistant_repo.increment_usage_count(assistant, unique_users_count)
            return usage_record
        except Exception:
            logger.error(f"Failed to record usage for assistant {assistant.id} by user {user.id}", exc_info=True)

    def manage_reaction(self, assistant_id: str, user_id: str, reaction_type: str) -> ReactionResponse:
        """Toggle a reaction (like/dislike) for an assistant by a user.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user
            reaction_type: Type of reaction ('like' or 'dislike') - will be converted to ReactionType enum

        Returns:
            ReactionResponse with reaction status and updated counts
        """
        # Get assistant for metrics tracking
        assistant = Assistant.find_by_id(assistant_id)

        # Validate reaction type
        try:
            # Convert string to enum
            reaction_enum = ReactionType(reaction_type)
        except ValueError:
            if assistant:
                AgentMonitoringService.track_reaction_metric(
                    "add_assistant_reaction",
                    assistant,
                    user_id,
                    False,
                    {"error": "Invalid reaction type", "reaction_type": reaction_type},
                )
            return ReactionResponse(
                success=False, reaction=None, like_count=0, dislike_count=0, error="Invalid reaction type"
            )

        # Get current interaction record
        interaction = self.repository.get_by_assistant_and_user(assistant_id, user_id)

        # Get current reaction state
        current_reaction = interaction.reaction if interaction else None

        # Determine new reaction state (toggle current state)
        new_reaction = None
        if current_reaction != reaction_enum:
            # If current reaction is different or None, set to the new reaction type
            new_reaction = reaction_enum
        # Otherwise, if current reaction matches requested type, toggle it off (set to None)

        # Update reaction in database
        success = self.repository.set_reaction_value(assistant_id, user_id, new_reaction)

        if not success:
            logger.error(f"Failed to update reaction for assistant {assistant_id} and user {user_id}")
            if assistant:
                AgentMonitoringService.track_reaction_metric(
                    "add_assistant_reaction", assistant, user_id, False, {"error": "Database update failed"}
                )
            return ReactionResponse(
                success=False,
                reaction=current_reaction,
                like_count=0,
                dislike_count=0,
                error="Failed to update reaction",
            )

        # Track successful reaction
        if assistant:
            reaction_type_str = reaction_type if new_reaction else "none"
            AgentMonitoringService.track_reaction_metric(
                "add_assistant_reaction",
                assistant,
                user_id,
                True,
                {
                    "reaction_type": reaction_type_str,
                },
            )

        # Update counts and return result
        return self._update_reaction_counts(assistant_id, new_reaction)

    def get_reactions_by_user(self, user_id: str, reaction_type: Optional[ReactionType] = None) -> List[Any]:
        """
        Get all assistants with reactions by a specific user.

        Args:
            user_id: ID of the user
            reaction_type: Optional filter for specific reaction type

        Returns:
            List of usage records with reactions by the user, sorted by reaction_at date (most recent first)
        """
        logger.debug(f"Getting reactions for user {user_id} with filter {reaction_type}")
        return self.repository.get_reactions_by_user(user_id, reaction_type)

    def remove_reactions(self, assistant_id: str, user_id: str) -> ReactionResponse:
        """
        Remove all reactions from an assistant for a user.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user

        Returns:
            ReactionResponse with reaction status and updated counts
        """
        # Get assistant for metrics tracking
        assistant = Assistant.find_by_id(assistant_id)

        # Get current interaction record
        interaction = self.repository.get_by_assistant_and_user(assistant_id, user_id)

        # If no interaction or no reaction, just return current counts
        if not interaction or not interaction.reaction:
            return self._get_current_reaction_state(assistant_id, None)

        # Update reaction in database
        success = self.repository.set_reaction_value(assistant_id, user_id, None)

        if not success:
            if assistant:
                AgentMonitoringService.track_reaction_metric(
                    "remove_assistant_reaction",
                    assistant,
                    user_id,
                    False,
                    {
                        "error": "Database update failed",
                    },
                )
            return ReactionResponse(
                success=False,
                reaction=interaction.reaction,
                like_count=0,
                dislike_count=0,
                error="Failed to remove reaction",
            )

        # Track successful removal
        if assistant:
            AgentMonitoringService.track_reaction_metric(
                "remove_assistant_reaction",
                assistant,
                user_id,
                True,
            )

        # Update counts and return result
        return self._update_reaction_counts(assistant_id, None)

    def _get_current_reaction_state(self, assistant_id: str, reaction: Optional[str]) -> ReactionResponse:
        """Helper method to get current reaction counts without making changes"""
        like_count = self.repository.get_like_count(assistant_id)
        dislike_count = self.repository.get_dislike_count(assistant_id)

        return ReactionResponse(success=True, reaction=reaction, like_count=like_count, dislike_count=dislike_count)

    def _update_reaction_counts(self, assistant_id: str, reaction: Optional[str]) -> ReactionResponse:
        """Helper method to update assistant reaction counts and return results"""
        # Get updated counts
        like_count = self.repository.get_like_count(assistant_id)
        dislike_count = self.repository.get_dislike_count(assistant_id)

        # Update assistant model
        assistant = Assistant.find_by_id(assistant_id)
        if assistant:
            self.assistant_repo.update_reaction_counts(assistant, like_count, dislike_count)

        return ReactionResponse(success=True, reaction=reaction, like_count=like_count, dislike_count=dislike_count)


# Create a singleton instance for easy access
assistant_user_interaction_service = AssistantUserInterationService()


# Module level functions for backwards compatibility and easier testing
def manage_reaction(assistant_id: str, user_id: str, reaction_type: str) -> ReactionResponse:
    """
    Toggle a reaction (like/dislike) for an assistant by a user.
    This is a module-level function that delegates to the singleton instance.

    Args:
        assistant_id: ID of the assistant
        user_id: ID of the user
        reaction_type: Type of reaction ('like' or 'dislike')

    Returns:
        ReactionResponse with reaction status and updated counts
    """
    return assistant_user_interaction_service.manage_reaction(assistant_id, user_id, reaction_type)


def remove_reactions(assistant_id: str, user_id: str) -> ReactionResponse:
    """
    Remove all reactions from an assistant for a user.
    This is a module-level function that delegates to the singleton instance.

    Args:
        assistant_id: ID of the assistant
        user_id: ID of the user

    Returns:
        ReactionResponse with reaction status and updated counts
    """
    return assistant_user_interaction_service.remove_reactions(assistant_id, user_id)


def get_reactions_by_user(user_id: str, reaction_type: Optional[ReactionType] = None) -> List[Any]:
    """
    Get all assistants with reactions by a specific user.
    This is a module-level function that delegates to the singleton instance.

    Args:
        user_id: ID of the user
        reaction_type: Optional filter for specific reaction type

    Returns:
        List of usage records with reactions by the user
    """
    return assistant_user_interaction_service.get_reactions_by_user(user_id, reaction_type)
