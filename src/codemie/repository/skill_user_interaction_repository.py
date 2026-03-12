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
Repository for skill usage tracking.
"""

from abc import ABC, abstractmethod
from datetime import datetime, UTC
from typing import Any
from uuid import uuid4

from sqlalchemy import and_
from sqlmodel import Session, select, func

from codemie.configs import logger
from codemie.rest_api.models.usage.skill_user_interaction import SkillUserInteraction, ReactionType


class SkillUserInteractionRepository(ABC):
    """
    Abstract base class for skill usage repository.
    Defines the interface for skill usage data operations.
    """

    @abstractmethod
    def record_usage(self, skill_id: str, user_id: str, project: str | None = None) -> Any:
        """
        Record a usage of a skill by a user.

        Args:
            skill_id: ID of the skill being used
            user_id: ID of the user using the skill
            project: Optional project context

        Returns:
            The updated or created usage record
        """
        pass

    @abstractmethod
    def get_unique_users_count(self, skill_id: str) -> int:
        """
        Get the count of unique users for a skill.

        Args:
            skill_id: ID of the skill

        Returns:
            Count of unique users who have used this skill
        """
        pass

    @abstractmethod
    def get_by_skill_and_user(self, skill_id: str, user_id: str) -> Any | None:
        """
        Get usage record for a specific skill and user.

        Args:
            skill_id: ID of the skill
            user_id: ID of the user

        Returns:
            Usage record if found, None otherwise
        """
        pass

    @abstractmethod
    def get_like_count(self, skill_id: str) -> int:
        """
        Get the count of likes for a skill.

        Args:
            skill_id: ID of the skill

        Returns:
            Count of likes for this skill
        """
        pass

    @abstractmethod
    def get_dislike_count(self, skill_id: str) -> int:
        """
        Get the count of dislikes for a skill.

        Args:
            skill_id: ID of the skill

        Returns:
            Count of dislikes for this skill
        """
        pass

    @abstractmethod
    def get_reactions_by_user(self, user_id: str, reaction_type: ReactionType | None = None) -> list[Any]:
        """
        Get all skills with reactions by a specific user.

        Args:
            user_id: ID of the user
            reaction_type: Optional filter for specific reaction type

        Returns:
            List of usage records with reactions by the user
        """
        pass

    @abstractmethod
    def set_reaction_value(self, skill_id: str, user_id: str, reaction_value: ReactionType | None) -> bool:
        """
        Set a specific reaction value for a skill by a user.

        Args:
            skill_id: ID of the skill
            user_id: ID of the user
            reaction_value: The reaction value to set

        Returns:
            True if operation was successful, False otherwise
        """
        pass


class SQLSkillUserInteractionRepository(SkillUserInteractionRepository):
    """
    SQL implementation of the skill usage repository.
    Uses SQLModel to interact with the database.
    """

    def record_usage(self, skill_id: str, user_id: str, project: str | None = None) -> SkillUserInteraction:
        """
        Record a usage of a skill by a user.

        Args:
            skill_id: ID of the skill being used
            user_id: ID of the user using the skill
            project: Optional project context

        Returns:
            The updated or created usage record
        """
        # Use single session for both query and update to reduce database round-trips
        with Session(SkillUserInteraction.get_engine()) as session:
            # Query for existing usage record
            query = select(SkillUserInteraction).where(
                SkillUserInteraction.skill_id == skill_id, SkillUserInteraction.user_id == user_id
            )
            usage = session.exec(query).first()

            now = datetime.now(UTC)

            if usage:
                # Update existing record
                usage.usage_count += 1
                usage.last_used_at = now
                usage.update_date = now
            else:
                # Create new record with explicit ID
                usage = SkillUserInteraction(
                    id=str(uuid4()),
                    skill_id=skill_id,
                    user_id=user_id,
                    project=project,
                    usage_count=1,
                    date=now,
                    update_date=now,
                    first_used_at=now,
                    last_used_at=now,
                )

            session.add(usage)
            session.commit()
            session.refresh(usage)
            return usage

    def get_unique_users_count(self, skill_id: str) -> int:
        """
        Get the count of unique users for a skill.

        Args:
            skill_id: ID of the skill

        Returns:
            Count of unique users who have used this skill
        """
        with Session(SkillUserInteraction.get_engine()) as session:
            query = select(func.count()).select_from(
                select(SkillUserInteraction).where(SkillUserInteraction.skill_id == skill_id).subquery()
            )
            return session.exec(query).one()

    def get_by_skill_and_user(self, skill_id: str, user_id: str) -> SkillUserInteraction | None:
        """
        Get usage record for a specific skill and user.

        Args:
            skill_id: ID of the skill
            user_id: ID of the user

        Returns:
            Usage record if found, None otherwise
        """
        with Session(SkillUserInteraction.get_engine()) as session:
            query = select(SkillUserInteraction).where(
                SkillUserInteraction.skill_id == skill_id, SkillUserInteraction.user_id == user_id
            )
            return session.exec(query).first()

    def get_like_count(self, skill_id: str) -> int:
        """
        Get the count of likes for a skill.

        Args:
            skill_id: ID of the skill

        Returns:
            Count of likes for this skill
        """
        with Session(SkillUserInteraction.get_engine()) as session:
            query = select(func.count()).select_from(
                select(SkillUserInteraction)
                .where(
                    and_(
                        SkillUserInteraction.skill_id == skill_id,
                        SkillUserInteraction.reaction == ReactionType.LIKE,
                    )
                )
                .subquery()
            )
            return session.exec(query).one()

    def get_dislike_count(self, skill_id: str) -> int:
        """
        Get the count of dislikes for a skill.

        Args:
            skill_id: ID of the skill

        Returns:
            Count of dislikes for this skill
        """
        with Session(SkillUserInteraction.get_engine()) as session:
            query = select(func.count()).select_from(
                select(SkillUserInteraction)
                .where(
                    and_(
                        SkillUserInteraction.skill_id == skill_id,
                        SkillUserInteraction.reaction == ReactionType.DISLIKE,
                    )
                )
                .subquery()
            )
            return session.exec(query).one()

    def get_reactions_by_user(
        self, user_id: str, reaction_type: ReactionType | None = None
    ) -> list[SkillUserInteraction]:
        """
        Get all skills with reactions by a specific user.

        Args:
            user_id: ID of the user
            reaction_type: Optional filter for specific reaction type

        Returns:
            List of usage records with reactions by the user
        """
        logger.debug(f"Repository: Getting reactions for user {user_id} with filter {reaction_type}")
        with Session(SkillUserInteraction.get_engine()) as session:
            conditions = [SkillUserInteraction.user_id == user_id]

            # Add reaction filter if specified
            if reaction_type is not None:
                conditions.append(SkillUserInteraction.reaction == reaction_type)
            else:
                # Only include records with a reaction
                conditions.append(SkillUserInteraction.reaction.is_not(None))

            query = select(SkillUserInteraction).where(and_(*conditions))
            return session.exec(query).all()

    def set_reaction_value(self, skill_id: str, user_id: str, reaction_value: ReactionType | None) -> bool:
        """
        Set a specific reaction value for a skill by a user.

        Args:
            skill_id: ID of the skill
            user_id: ID of the user
            reaction_value: The reaction value to set (ReactionType.LIKE, ReactionType.DISLIKE, or None to clear)

        Returns:
            True if operation was successful, False otherwise
        """
        if reaction_value is not None and reaction_value not in [ReactionType.LIKE, ReactionType.DISLIKE]:
            logger.error(f"Invalid reaction value: {reaction_value}")
            return False

        with Session(SkillUserInteraction.get_engine()) as session:
            query = select(SkillUserInteraction).where(
                SkillUserInteraction.skill_id == skill_id, SkillUserInteraction.user_id == user_id
            )
            usage = session.exec(query).first()

            if not usage and reaction_value:
                now = datetime.now(UTC)
                usage = SkillUserInteraction(
                    id=str(uuid4()),
                    skill_id=skill_id,
                    user_id=user_id,
                    usage_count=0,
                    date=now,
                    update_date=now,
                    first_used_at=now,
                    last_used_at=now,
                )
            elif not usage:
                return True

            now = datetime.now(UTC)
            usage.reaction = reaction_value
            usage.reaction_at = now if reaction_value else None
            usage.update_date = now

            session.add(usage)
            session.commit()
            return True


# Default implementation
SkillUsageRepositoryImpl = SQLSkillUserInteractionRepository
