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
Repository for AI Kata usage and reaction tracking.
"""

from abc import ABC, abstractmethod
from datetime import datetime, UTC
from typing import Optional, List, Any
from uuid import uuid4

from sqlalchemy import and_
from sqlmodel import Session, select, func

from codemie.configs import logger
from codemie.rest_api.models.usage.kata_user_interaction import KataUserInteractionSQL, ReactionType


class KataUserInteractionRepository(ABC):
    """
    Abstract base class for kata usage repository.
    Defines the interface for kata usage data operations.
    """

    @abstractmethod
    def record_usage(self, kata_id: str, user_id: str) -> Any:
        """
        Record a usage of a kata by a user.

        Args:
            kata_id: ID of the kata being used
            user_id: ID of the user using the kata

        Returns:
            The updated or created usage record
        """
        pass

    @abstractmethod
    def get_unique_users_count(self, kata_id: str) -> int:
        """
        Get the count of unique users for a kata.

        Args:
            kata_id: ID of the kata

        Returns:
            Count of unique users who have used this kata
        """
        pass

    @abstractmethod
    def get_by_kata_and_user(self, kata_id: str, user_id: str) -> Optional[Any]:
        """
        Get usage record for a specific kata and user.

        Args:
            kata_id: ID of the kata
            user_id: ID of the user

        Returns:
            Usage record if found, None otherwise
        """
        pass

    @abstractmethod
    def get_like_count(self, kata_id: str) -> int:
        """
        Get the count of likes for a kata.

        Args:
            kata_id: ID of the kata

        Returns:
            Count of likes for this kata
        """
        pass

    @abstractmethod
    def get_dislike_count(self, kata_id: str) -> int:
        """
        Get the count of dislikes for a kata.

        Args:
            kata_id: ID of the kata

        Returns:
            Count of dislikes for this kata
        """
        pass

    @abstractmethod
    def get_reactions_by_user(self, user_id: str, reaction_type: Optional[ReactionType] = None) -> List[Any]:
        """
        Get all katas with reactions by a specific user.

        Args:
            user_id: ID of the user
            reaction_type: Optional filter for specific reaction type

        Returns:
            List of usage records with reactions by the user
        """
        pass

    @abstractmethod
    def set_reaction_value(self, kata_id: str, user_id: str, reaction_value: Optional[ReactionType]) -> bool:
        """
        Set a specific reaction value for a kata by a user.

        Args:
            kata_id: ID of the kata
            user_id: ID of the user
            reaction_value: The reaction value to set (ReactionType.LIKE, ReactionType.DISLIKE, or None to clear)

        Returns:
            True if operation was successful, False otherwise
        """
        pass

    @abstractmethod
    def bulk_get_user_reactions(self, user_id: str, kata_ids: List[str]) -> dict[str, Optional[ReactionType]]:
        """
        Get user reactions for multiple katas at once.

        Args:
            user_id: ID of the user
            kata_ids: List of kata IDs to get reactions for

        Returns:
            Dictionary mapping kata_id -> ReactionType (or None if no reaction)
        """
        pass


class SQLKataUserInteractionRepository(KataUserInteractionRepository):
    """
    SQL implementation of the kata usage repository.
    Uses SQLModel to interact with the database.
    """

    def record_usage(self, kata_id: str, user_id: str) -> KataUserInteractionSQL:
        """
        Record a usage of a kata by a user.

        Args:
            kata_id: ID of the kata being used
            user_id: ID of the user using the kata

        Returns:
            The updated or created usage record
        """
        usage = self.get_by_kata_and_user(kata_id, user_id)

        if usage:
            # Update existing record
            with Session(KataUserInteractionSQL.get_engine()) as session:
                usage.usage_count += 1
                usage.last_used_at = datetime.now(UTC)
                usage.update_date = datetime.now(UTC)
                session.add(usage)
                session.commit()
                session.refresh(usage)
                return usage
        else:
            # Create new record with explicit ID
            with Session(KataUserInteractionSQL.get_engine()) as session:
                usage = KataUserInteractionSQL(
                    id=str(uuid4()),  # Explicitly set ID to avoid null value issue
                    kata_id=kata_id,
                    user_id=user_id,
                    usage_count=1,
                    date=datetime.now(UTC),
                    update_date=datetime.now(UTC),
                    first_used_at=datetime.now(UTC),
                    last_used_at=datetime.now(UTC),
                )
                session.add(usage)
                session.commit()
                session.refresh(usage)
                return usage

    def get_unique_users_count(self, kata_id: str) -> int:
        """
        Get the count of unique users for a kata.

        Args:
            kata_id: ID of the kata

        Returns:
            Count of unique users who have used this kata
        """
        with Session(KataUserInteractionSQL.get_engine()) as session:
            query = select(func.count()).select_from(
                select(KataUserInteractionSQL).where(KataUserInteractionSQL.kata_id == kata_id).subquery()
            )
            return session.exec(query).one()

    def get_by_kata_and_user(self, kata_id: str, user_id: str) -> Optional[KataUserInteractionSQL]:
        """
        Get usage record for a specific kata and user.

        Args:
            kata_id: ID of the kata
            user_id: ID of the user

        Returns:
            Usage record if found, None otherwise
        """
        with Session(KataUserInteractionSQL.get_engine()) as session:
            query = select(KataUserInteractionSQL).where(
                KataUserInteractionSQL.kata_id == kata_id, KataUserInteractionSQL.user_id == user_id
            )
            return session.exec(query).first()

    def get_like_count(self, kata_id: str) -> int:
        """
        Get the count of likes for a kata.

        Args:
            kata_id: ID of the kata

        Returns:
            Count of likes for this kata
        """
        with Session(KataUserInteractionSQL.get_engine()) as session:
            query = select(func.count()).select_from(
                select(KataUserInteractionSQL)
                .where(
                    and_(
                        KataUserInteractionSQL.kata_id == kata_id,
                        KataUserInteractionSQL.reaction == ReactionType.LIKE,
                    )
                )
                .subquery()
            )
            return session.exec(query).one()

    def get_dislike_count(self, kata_id: str) -> int:
        """
        Get the count of dislikes for a kata.

        Args:
            kata_id: ID of the kata

        Returns:
            Count of dislikes for this kata
        """
        with Session(KataUserInteractionSQL.get_engine()) as session:
            query = select(func.count()).select_from(
                select(KataUserInteractionSQL)
                .where(
                    and_(
                        KataUserInteractionSQL.kata_id == kata_id,
                        KataUserInteractionSQL.reaction == ReactionType.DISLIKE,
                    )
                )
                .subquery()
            )
            return session.exec(query).one()

    def get_reactions_by_user(
        self, user_id: str, reaction_type: Optional[ReactionType] = None
    ) -> List[KataUserInteractionSQL]:
        """
        Get all katas with reactions by a specific user.

        Args:
            user_id: ID of the user
            reaction_type: Optional filter for specific reaction type

        Returns:
            List of usage records with reactions by the user, sorted by reaction_at date (most recent first)
        """
        logger.debug(f"Repository: Getting reactions for user {user_id} with filter {reaction_type}")
        with Session(KataUserInteractionSQL.get_engine()) as session:
            conditions = [KataUserInteractionSQL.user_id == user_id]

            # Add reaction filter if specified
            if reaction_type is not None:
                conditions.append(KataUserInteractionSQL.reaction == reaction_type)
            else:
                # Only include records with a reaction
                conditions.append(KataUserInteractionSQL.reaction.is_not(None))

            query = select(KataUserInteractionSQL).where(and_(*conditions))
            return session.exec(query).all()

    def set_reaction_value(self, kata_id: str, user_id: str, reaction_value: Optional[ReactionType]) -> bool:
        """
        Set a specific reaction value for a kata by a user.

        Args:
            kata_id: ID of the kata
            user_id: ID of the user
            reaction_value: The reaction value to set (ReactionType.LIKE, ReactionType.DISLIKE, or None to clear)

        Returns:
            True if operation was successful, False otherwise
        """
        # Validate reaction value
        if reaction_value is not None and reaction_value not in [ReactionType.LIKE, ReactionType.DISLIKE]:
            logger.error(f"Invalid reaction value: {reaction_value}")
            return False

        usage = self.get_by_kata_and_user(kata_id, user_id)

        # Check if we need to create a new record
        if not usage and reaction_value:
            usage = self.record_usage(kata_id, user_id)
        elif not usage and not reaction_value:
            # Nothing to clear if no record exists
            return True

        with Session(KataUserInteractionSQL.get_engine()) as session:
            now = datetime.now(UTC)

            # Set the reaction value
            usage.reaction = reaction_value
            usage.reaction_at = now if reaction_value else None
            usage.update_date = now

            session.add(usage)
            session.commit()
            return True

    def bulk_get_user_reactions(self, user_id: str, kata_ids: List[str]) -> dict[str, Optional[ReactionType]]:
        """
        Get user reactions for multiple katas at once.

        Args:
            user_id: ID of the user
            kata_ids: List of kata IDs to get reactions for

        Returns:
            Dictionary mapping kata_id -> ReactionType (or None if no reaction)
        """
        if not kata_ids:
            return {}

        with Session(KataUserInteractionSQL.get_engine()) as session:
            query = select(KataUserInteractionSQL.kata_id, KataUserInteractionSQL.reaction).where(
                and_(KataUserInteractionSQL.user_id == user_id, KataUserInteractionSQL.kata_id.in_(kata_ids))
            )
            results = session.exec(query).all()

            # Build map with all kata_ids defaulting to None
            reaction_map = {kata_id: None for kata_id in kata_ids}

            # Update with actual reactions
            for kata_id, reaction in results:
                reaction_map[kata_id] = reaction

            return reaction_map


# Default implementation
KataUsageRepositoryImpl = SQLKataUserInteractionRepository
