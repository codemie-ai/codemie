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
Service for managing skill usage data.
"""

from dataclasses import dataclass
from typing import Any

from codemie.rest_api.models.usage.skill_user_interaction import ReactionType
from codemie.configs import logger
from codemie.repository.skill_user_interaction_repository import (
    SkillUserInteractionRepository,
    SkillUsageRepositoryImpl,
)
from codemie.repository.skill_repository import SkillRepository


@dataclass
class ReactionResponse:
    """Response model for skill reactions"""

    success: bool
    reaction: ReactionType | None
    like_count: int
    dislike_count: int
    error: str | None = None


class SkillUserInteractionService:
    """
    Service for managing skill usage data.
    Provides business logic for tracking and retrieving skill usage information.
    """

    def __init__(
        self,
        repository: SkillUserInteractionRepository | None = None,
        skill_repo: SkillRepository | None = None,
    ):
        """
        Initialize the service with a repository.

        Args:
            repository: Repository implementation to use. If None, uses the default implementation.
            skill_repo: Skill repository for updating skill metadata
        """
        self.repository = repository or SkillUsageRepositoryImpl()
        self.skill_repo = skill_repo or SkillRepository

    def record_usage(self, skill_id: str, user_id: str, project: str | None = None) -> Any | None:
        """
        Record a usage of a skill by a user.

        Args:
            skill_id: The skill being used
            user_id: The user performing the action
            project: Optional project context

        Returns:
            The updated or created usage record, or None if recording fails

        Raises:
            DatabaseException: If database operation fails critically
        """
        try:
            logger.debug(f"Recording usage for skill {skill_id} by user {user_id}")
            usage_record = self.repository.record_usage(skill_id, user_id, project)
            return usage_record
        except Exception as e:
            logger.error(f"Failed to record usage for skill {skill_id} by user {user_id}: {str(e)}", exc_info=True)
            # For usage tracking, we log but don't fail the operation
            # Return None to indicate tracking failed but don't block the main operation
            return None

    def manage_reaction(self, skill_id: str, user_id: str, reaction_type: str) -> ReactionResponse:
        """Toggle a reaction (like/dislike) for a skill by a user.

        Args:
            skill_id: ID of the skill
            user_id: ID of the user
            reaction_type: Type of reaction ('like' or 'dislike') - will be converted to ReactionType enum

        Returns:
            ReactionResponse with reaction status and updated counts
        """
        # Validate reaction type
        try:
            # Convert string to enum
            reaction_enum = ReactionType(reaction_type)
        except ValueError:
            return ReactionResponse(
                success=False, reaction=None, like_count=0, dislike_count=0, error="Invalid reaction type"
            )

        # Get current interaction record
        interaction = self.repository.get_by_skill_and_user(skill_id, user_id)

        # Get current reaction state
        current_reaction = interaction.reaction if interaction else None

        # Determine new reaction state (toggle current state)
        new_reaction = None
        if current_reaction != reaction_enum:
            # If current reaction is different or None, set to the new reaction type
            new_reaction = reaction_enum
        # Otherwise, if current reaction matches requested type, toggle it off (set to None)

        # Update reaction in database
        success = self.repository.set_reaction_value(skill_id, user_id, new_reaction)

        if not success:
            logger.error(f"Failed to update reaction for skill {skill_id} and user {user_id}")
            return ReactionResponse(
                success=False,
                reaction=current_reaction,
                like_count=0,
                dislike_count=0,
                error="Failed to update reaction",
            )

        # Update counts and return result
        return self._update_reaction_counts(skill_id, new_reaction)

    def get_reactions_by_user(self, user_id: str, reaction_type: ReactionType | None = None) -> list[Any]:
        """
        Get all skills with reactions by a specific user.

        Args:
            user_id: ID of the user
            reaction_type: Optional filter for specific reaction type

        Returns:
            List of usage records with reactions by the user, sorted by reaction_at date (most recent first)
        """
        logger.debug(f"Getting reactions for user {user_id} with filter {reaction_type}")
        return self.repository.get_reactions_by_user(user_id, reaction_type)

    def remove_reactions(self, skill_id: str, user_id: str) -> ReactionResponse:
        """
        Remove all reactions from a skill for a user.

        Args:
            skill_id: ID of the skill
            user_id: ID of the user

        Returns:
            ReactionResponse with reaction status and updated counts
        """
        # Get current interaction record
        interaction = self.repository.get_by_skill_and_user(skill_id, user_id)

        # If no interaction or no reaction, just return current counts
        if not interaction or not interaction.reaction:
            return self._get_current_reaction_state(skill_id, None)

        # Update reaction in database
        success = self.repository.set_reaction_value(skill_id, user_id, None)

        if not success:
            return ReactionResponse(
                success=False,
                reaction=interaction.reaction,
                like_count=0,
                dislike_count=0,
                error="Failed to remove reaction",
            )

        # Update counts and return result
        return self._update_reaction_counts(skill_id, None)

    def _get_current_reaction_state(self, skill_id: str, reaction: str | None) -> ReactionResponse:
        """Helper method to get current reaction counts without making changes"""
        like_count = self.repository.get_like_count(skill_id)
        dislike_count = self.repository.get_dislike_count(skill_id)

        return ReactionResponse(success=True, reaction=reaction, like_count=like_count, dislike_count=dislike_count)

    def _update_reaction_counts(self, skill_id: str, reaction: str | None) -> ReactionResponse:
        """Helper method to update skill reaction counts and return results"""
        # Get updated counts
        like_count = self.repository.get_like_count(skill_id)
        dislike_count = self.repository.get_dislike_count(skill_id)

        # Update skill model
        self.skill_repo.update_reaction_counts(skill_id, like_count, dislike_count)

        return ReactionResponse(success=True, reaction=reaction, like_count=like_count, dislike_count=dislike_count)


# Create a singleton instance for easy access
skill_user_interaction_service = SkillUserInteractionService()


# Module level functions for backwards compatibility and easier testing
def manage_reaction(skill_id: str, user_id: str, reaction_type: str) -> ReactionResponse:
    """
    Toggle a reaction (like/dislike) for a skill by a user.
    This is a module-level function that delegates to the singleton instance.

    Args:
        skill_id: ID of the skill
        user_id: ID of the user
        reaction_type: Type of reaction ('like' or 'dislike')

    Returns:
        ReactionResponse with reaction status and updated counts
    """
    return skill_user_interaction_service.manage_reaction(skill_id, user_id, reaction_type)


def remove_reactions(skill_id: str, user_id: str) -> ReactionResponse:
    """
    Remove all reactions from a skill for a user.
    This is a module-level function that delegates to the singleton instance.

    Args:
        skill_id: ID of the skill
        user_id: ID of the user

    Returns:
        ReactionResponse with reaction status and updated counts
    """
    return skill_user_interaction_service.remove_reactions(skill_id, user_id)


def get_reactions_by_user(user_id: str, reaction_type: ReactionType | None = None) -> list[Any]:
    """
    Get all skills with reactions by a specific user.
    This is a module-level function that delegates to the singleton instance.

    Args:
        user_id: ID of the user
        reaction_type: Optional filter for specific reaction type

    Returns:
        List of usage records with reactions by the user
    """
    return skill_user_interaction_service.get_reactions_by_user(user_id, reaction_type)
