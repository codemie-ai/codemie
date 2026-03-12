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
Repository for assistant usage tracking.
"""

from abc import ABC, abstractmethod
from datetime import datetime, UTC
from typing import Optional, List, Any

from codemie.rest_api.models.usage.assistant_user_interaction import ReactionType
from uuid import uuid4

from sqlalchemy import and_
from sqlmodel import Session, select, func

from codemie.configs import logger
from codemie.rest_api.models.usage.assistant_user_interaction import AssistantUserInterationSQL


class AssistantUserInterationRepository(ABC):
    """
    Abstract base class for assistant usage repository.
    Defines the interface for assistant usage data operations.
    """

    @abstractmethod
    def record_usage(self, assistant_id: str, user_id: str, project: Optional[str] = None) -> Any:
        """
        Record a usage of an assistant by a user.

        Args:
            assistant_id: ID of the assistant being used
            user_id: ID of the user using the assistant
            project: Optional project context

        Returns:
            The updated or created usage record
        """
        pass

    @abstractmethod
    def get_unique_users_count(self, assistant_id: str) -> int:
        """
        Get the count of unique users for an assistant.

        Args:
            assistant_id: ID of the assistant

        Returns:
            Count of unique users who have used this assistant
        """
        pass

    @abstractmethod
    def get_by_assistant_and_user(self, assistant_id: str, user_id: str) -> Optional[Any]:
        """
        Get usage record for a specific assistant and user.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user

        Returns:
            Usage record if found, None otherwise
        """
        pass

    @abstractmethod
    def get_like_count(self, assistant_id: str) -> int:
        """
        Get the count of likes for an assistant.

        Args:
            assistant_id: ID of the assistant

        Returns:
            Count of likes for this assistant
        """
        pass

    @abstractmethod
    def get_dislike_count(self, assistant_id: str) -> int:
        """
        Get the count of dislikes for an assistant.

        Args:
            assistant_id: ID of the assistant

        Returns:
            Count of dislikes for this assistant
        """
        pass

    @abstractmethod
    def get_reactions_by_user(self, user_id: str, reaction_type: Optional[ReactionType] = None) -> List[Any]:
        """
        Get all assistants with reactions by a specific user.

        Args:
            user_id: ID of the user
            reaction_type: Optional filter for specific reaction type

        Returns:
            List of usage records with reactions by the user
        """
        pass


class SQLAssistantUserInterationRepository(AssistantUserInterationRepository):
    """
    SQL implementation of the assistant usage repository.
    Uses SQLModel to interact with the database.
    """

    def record_usage(
        self, assistant_id: str, user_id: str, project: Optional[str] = None
    ) -> AssistantUserInterationSQL:
        """
        Record a usage of an assistant by a user.

        Args:
            assistant_id: ID of the assistant being used
            user_id: ID of the user using the assistant
            project: Optional project context

        Returns:
            The updated or created usage record
        """
        usage = self.get_by_assistant_and_user(assistant_id, user_id)

        if usage:
            # Update existing record
            with Session(AssistantUserInterationSQL.get_engine()) as session:
                usage.usage_count += 1
                usage.last_used_at = datetime.now(UTC)
                usage.update_date = datetime.now(UTC)
                session.add(usage)
                session.commit()
                session.refresh(usage)
                return usage
        else:
            # Create new record with explicit ID
            with Session(AssistantUserInterationSQL.get_engine()) as session:
                usage = AssistantUserInterationSQL(
                    id=str(uuid4()),  # Explicitly set ID to avoid null value issue
                    assistant_id=assistant_id,
                    user_id=user_id,
                    project=project,
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

    def get_unique_users_count(self, assistant_id: str) -> int:
        """
        Get the count of unique users for an assistant.

        Args:
            assistant_id: ID of the assistant

        Returns:
            Count of unique users who have used this assistant
        """
        with Session(AssistantUserInterationSQL.get_engine()) as session:
            query = select(func.count()).select_from(
                select(AssistantUserInterationSQL)
                .where(AssistantUserInterationSQL.assistant_id == assistant_id)
                .subquery()
            )
            return session.exec(query).one()

    def get_by_assistant_and_user(self, assistant_id: str, user_id: str) -> Optional[AssistantUserInterationSQL]:
        """
        Get usage record for a specific assistant and user.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user

        Returns:
            Usage record if found, None otherwise
        """
        with Session(AssistantUserInterationSQL.get_engine()) as session:
            query = select(AssistantUserInterationSQL).where(
                AssistantUserInterationSQL.assistant_id == assistant_id, AssistantUserInterationSQL.user_id == user_id
            )
            return session.exec(query).first()

    def get_like_count(self, assistant_id: str) -> int:
        """
        Get the count of likes for an assistant.

        Args:
            assistant_id: ID of the assistant

        Returns:
            Count of likes for this assistant
        """
        with Session(AssistantUserInterationSQL.get_engine()) as session:
            query = select(func.count()).select_from(
                select(AssistantUserInterationSQL)
                .where(
                    and_(
                        AssistantUserInterationSQL.assistant_id == assistant_id,
                        AssistantUserInterationSQL.reaction == ReactionType.LIKE,
                    )
                )
                .subquery()
            )
            return session.exec(query).one()

    def get_dislike_count(self, assistant_id: str) -> int:
        """
        Get the count of dislikes for an assistant.

        Args:
            assistant_id: ID of the assistant

        Returns:
            Count of dislikes for this assistant
        """
        with Session(AssistantUserInterationSQL.get_engine()) as session:
            query = select(func.count()).select_from(
                select(AssistantUserInterationSQL)
                .where(
                    and_(
                        AssistantUserInterationSQL.assistant_id == assistant_id,
                        AssistantUserInterationSQL.reaction == ReactionType.DISLIKE,
                    )
                )
                .subquery()
            )
            return session.exec(query).one()

    def get_reactions_by_user(
        self, user_id: str, reaction_type: Optional[ReactionType] = None
    ) -> List[AssistantUserInterationSQL]:
        """
        Get all assistants with reactions by a specific user.

        Args:
            user_id: ID of the user
            reaction_type: Optional filter for specific reaction type

        Returns:
            List of usage records with reactions by the user
        """
        logger.debug(f"Repository: Getting reactions for user {user_id} with filter {reaction_type}")
        with Session(AssistantUserInterationSQL.get_engine()) as session:
            conditions = [AssistantUserInterationSQL.user_id == user_id]

            # Add reaction filter if specified
            if reaction_type is not None:
                conditions.append(AssistantUserInterationSQL.reaction == reaction_type)
            else:
                # Only include records with a reaction
                conditions.append(AssistantUserInterationSQL.reaction.is_not(None))

            query = select(AssistantUserInterationSQL).where(and_(*conditions))
            return session.exec(query).all()

    def set_reaction_value(self, assistant_id: str, user_id: str, reaction_value: Optional[ReactionType]) -> bool:
        """
        Set a specific reaction value for an assistant by a user.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user
            reaction_value: The reaction value to set (ReactionType.LIKE, ReactionType.DISLIKE, or None to clear)

        Returns:
            True if operation was successful, False otherwise
        """
        # Validate reaction value
        if reaction_value is not None and reaction_value not in [ReactionType.LIKE, ReactionType.DISLIKE]:
            logger.error(f"Invalid reaction value: {reaction_value}")
            return False

        usage = self.get_by_assistant_and_user(assistant_id, user_id)

        # Check if we need to create a new record
        if not usage and reaction_value:
            usage = self.record_usage(assistant_id, user_id)
        elif not usage and not reaction_value:
            # Nothing to clear if no record exists
            return True

        with Session(AssistantUserInterationSQL.get_engine()) as session:
            now = datetime.now(UTC)

            # Set the reaction value
            usage.reaction = reaction_value
            usage.reaction_at = now if reaction_value else None
            usage.update_date = now

            session.add(usage)
            session.commit()
            return True


# Default implementation
AssistantUsageRepositoryImpl = SQLAssistantUserInterationRepository
