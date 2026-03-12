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
Service for managing AI Kata usage and reaction data.
"""

from dataclasses import dataclass
from typing import Optional, List, Any

from codemie.configs import logger
from codemie.repository.kata_user_interaction_repository import (
    KataUserInteractionRepository,
    KataUsageRepositoryImpl,
)
from codemie.rest_api.models.usage.kata_user_interaction import ReactionType
from codemie.rest_api.models.ai_kata import AIKata
from codemie.rest_api.security.user import User
from codemie.service.monitoring.base_monitoring_service import BaseMonitoringService
from codemie.service.monitoring.metrics_constants import MetricsAttributes, KATA_REACTION_METRIC


@dataclass
class ReactionResponse:
    """Response model for kata reactions"""

    success: bool
    reaction: Optional[ReactionType]
    like_count: int
    dislike_count: int
    error: Optional[str] = None


class KataUserInteractionService:
    """
    Service for managing kata usage and reaction data.
    Provides business logic for tracking and retrieving kata usage information.
    """

    def __init__(
        self,
        repository: Optional[KataUserInteractionRepository] = None,
    ):
        """
        Initialize the service with a repository.

        Args:
            repository: Repository implementation to use. If None, uses the default implementation.
        """
        self.repository = repository or KataUsageRepositoryImpl()

    def record_usage(self, kata: AIKata, user: User) -> Any:
        """
        Record a usage of a kata by a user.

        Args:
            kata: The kata being used
            user: The user performing the action

        Returns:
            The updated or created usage record
        """
        try:
            logger.debug(f"Recording usage for kata {kata.id} by user {user.id}")
            usage_record = self.repository.record_usage(kata.id, user.id)
            return usage_record
        except Exception:
            logger.error(f"Failed to record usage for kata {kata.id} by user {user.id}", exc_info=True)

    def manage_reaction(self, kata_id: str, user: User, reaction_type: str) -> ReactionResponse:
        """Toggle a reaction (like/dislike) for a kata by a user.

        Args:
            kata_id: ID of the kata
            user: User performing the reaction
            reaction_type: Type of reaction ('like' or 'dislike') - will be converted to ReactionType enum

        Returns:
            ReactionResponse with reaction status and updated counts
        """
        # Validate reaction type
        try:
            # Convert string to enum
            reaction_enum = ReactionType(reaction_type)
        except ValueError:
            logger.error(f"Invalid reaction type for kata {kata_id}: {reaction_type}")
            return ReactionResponse(
                success=False, reaction=None, like_count=0, dislike_count=0, error="Invalid reaction type"
            )

        # Get current interaction record
        interaction = self.repository.get_by_kata_and_user(kata_id, user.id)

        # Get current reaction state
        current_reaction = interaction.reaction if interaction else None

        # Determine new reaction state (toggle current state)
        new_reaction = None
        if current_reaction != reaction_enum:
            # If current reaction is different or None, set to the new reaction type
            new_reaction = reaction_enum
        # Otherwise, if current reaction matches requested type, toggle it off (set to None)

        # Update reaction in database
        success = self.repository.set_reaction_value(kata_id, user.id, new_reaction)

        if not success:
            logger.error(f"Failed to update reaction for kata {kata_id} and user {user.id}")
            return ReactionResponse(
                success=False,
                reaction=current_reaction,
                like_count=0,
                dislike_count=0,
                error="Failed to update reaction",
            )

        # Update counts and return result
        return self._update_reaction_counts(kata_id, new_reaction, user)

    def get_reactions_by_user(self, user_id: str, reaction_type: Optional[ReactionType] = None) -> List[Any]:
        """
        Get all katas with reactions by a specific user.

        Args:
            user_id: ID of the user
            reaction_type: Optional filter for specific reaction type

        Returns:
            List of usage records with reactions by the user, sorted by reaction_at date (most recent first)
        """
        logger.debug(f"Getting reactions for user {user_id} with filter {reaction_type}")
        return self.repository.get_reactions_by_user(user_id, reaction_type)

    def remove_reactions(self, kata_id: str, user: User) -> ReactionResponse:
        """
        Remove all reactions from a kata for a user.

        Args:
            kata_id: ID of the kata
            user: User whose reactions to remove

        Returns:
            ReactionResponse with reaction status and updated counts
        """
        # Get current interaction record
        interaction = self.repository.get_by_kata_and_user(kata_id, user.id)

        # If no interaction or no reaction, just return current counts
        if not interaction or not interaction.reaction:
            return self._get_current_reaction_state(kata_id, None)

        # Update reaction in database
        success = self.repository.set_reaction_value(kata_id, user.id, None)

        if not success:
            logger.error(f"Failed to remove reaction for kata {kata_id} and user {user.id}")
            return ReactionResponse(
                success=False,
                reaction=interaction.reaction,
                like_count=0,
                dislike_count=0,
                error="Failed to remove reaction",
            )

        # Update counts and return result
        return self._update_reaction_counts(kata_id, None, user)

    def _get_current_reaction_state(self, kata_id: str, reaction: Optional[str]) -> ReactionResponse:
        """Helper method to get current reaction counts without making changes"""
        like_count = self.repository.get_like_count(kata_id)
        dislike_count = self.repository.get_dislike_count(kata_id)

        return ReactionResponse(success=True, reaction=reaction, like_count=like_count, dislike_count=dislike_count)

    def _update_reaction_counts(self, kata_id: str, reaction: Optional[ReactionType], user: User) -> ReactionResponse:
        """Helper method to update kata reaction counts and return results"""
        # Get updated counts
        like_count = self.repository.get_like_count(kata_id)
        dislike_count = self.repository.get_dislike_count(kata_id)

        # Update kata model with denormalized counts
        kata = AIKata.find_by_id(kata_id)
        if kata:
            from codemie.repository.ai_kata_repository import SQLAIKataRepository

            repository = SQLAIKataRepository()
            repository.update_reaction_counts(kata, like_count, dislike_count)

            # Track metrics - determine operation from reaction type
            operation = "remove_reaction" if reaction is None else reaction.value
            self._track_kata_reaction_metric(
                operation=operation,
                kata_title=kata.title,
                reaction_type=reaction.value if reaction else None,
                user=user,
                success=True,
            )

        return ReactionResponse(success=True, reaction=reaction, like_count=like_count, dislike_count=dislike_count)

    def _track_kata_reaction_metric(
        self, operation: str, kata_title: str, reaction_type: str | None, user: User, success: bool
    ):
        """
        Tracks metrics for kata reaction operations.

        Args:
            operation: The operation being performed (like, dislike, remove_reaction)
            kata_title: Title of the kata
            reaction_type: Type of reaction (like/dislike) or None
            user: User performing the operation
            success: Whether the operation succeeded
        """
        try:
            attributes = {
                MetricsAttributes.OPERATION: operation,
                MetricsAttributes.KATA_TITLE: kata_title,
                MetricsAttributes.USER_ID: user.id,
                MetricsAttributes.USER_NAME: user.name,
                MetricsAttributes.USER_EMAIL: user.username,
            }

            if reaction_type:
                attributes[MetricsAttributes.REACTION_TYPE] = reaction_type

            metric_name = KATA_REACTION_METRIC if success else f"{KATA_REACTION_METRIC}_error"
            BaseMonitoringService.send_count_metric(name=metric_name, attributes=attributes)

        except Exception as e:
            logger.warning(
                f"Failed to track kata reaction metric '{operation}': {e}",
                exc_info=True,
            )


# Create a singleton instance for easy access
kata_user_interaction_service = KataUserInteractionService()


# Module level functions for backwards compatibility and easier testing
def manage_reaction(kata_id: str, user: User, reaction_type: str) -> ReactionResponse:
    """
    Toggle a reaction (like/dislike) for a kata by a user.
    This is a module-level function that delegates to the singleton instance.

    Args:
        kata_id: ID of the kata
        user: User performing the reaction
        reaction_type: Type of reaction ('like' or 'dislike')

    Returns:
        ReactionResponse with reaction status and updated counts
    """
    return kata_user_interaction_service.manage_reaction(kata_id, user, reaction_type)


def remove_reactions(kata_id: str, user: User) -> ReactionResponse:
    """
    Remove all reactions from a kata for a user.
    This is a module-level function that delegates to the singleton instance.

    Args:
        kata_id: ID of the kata
        user: User whose reactions to remove

    Returns:
        ReactionResponse with reaction status and updated counts
    """
    return kata_user_interaction_service.remove_reactions(kata_id, user)


def get_reactions_by_user(user_id: str, reaction_type: Optional[ReactionType] = None) -> List[Any]:
    """
    Get all katas with reactions by a specific user.
    This is a module-level function that delegates to the singleton instance.

    Args:
        user_id: ID of the user
        reaction_type: Optional filter for specific reaction type

    Returns:
        List of usage records with reactions by the user
    """
    return kata_user_interaction_service.get_reactions_by_user(user_id, reaction_type)
