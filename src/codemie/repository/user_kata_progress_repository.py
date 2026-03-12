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

from abc import ABC, abstractmethod
from datetime import datetime, UTC
from typing import Optional, List, Dict
from uuid import uuid4

from sqlalchemy import and_, func, desc
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from codemie.configs import logger
from codemie.rest_api.models.user_kata_progress import UserKataProgress, KataProgressStatus, LeaderboardEntryFromDB
from codemie.rest_api.security.user import User


class UserKataProgressRepository(ABC):
    """
    Abstract base class for User Kata Progress repository.
    Defines the interface for user kata progress data operations.
    """

    @abstractmethod
    def start_kata(self, user: User, kata_id: str) -> str:
        """
        Enroll user in kata (create progress record).

        Args:
            user: User object containing user information
            kata_id: ID of the kata

        Returns:
            The ID of the created progress record

        Raises:
            ValueError: If user is already enrolled
        """
        pass

    @abstractmethod
    def complete_kata(self, user_id: str, kata_id: str) -> bool:
        """
        Mark kata as completed.

        Args:
            user_id: ID of the user
            kata_id: ID of the kata

        Returns:
            True if completion was successful

        Raises:
            ValueError: If user is not enrolled
        """
        pass

    @abstractmethod
    def get_user_progress(self, user_id: str, kata_id: str) -> Optional[UserKataProgress]:
        """
        Get specific user's progress for a kata.

        Args:
            user_id: ID of the user
            kata_id: ID of the kata

        Returns:
            UserKataProgress instance if found, None otherwise
        """
        pass

    @abstractmethod
    def get_user_all_progress(
        self, user_id: str, status: Optional[KataProgressStatus] = None
    ) -> List[UserKataProgress]:
        """
        Get all user's kata progress, optionally filtered by status.

        Args:
            user_id: ID of the user
            status: Optional status filter

        Returns:
            List of user's progress records
        """
        pass

    @abstractmethod
    def get_kata_enrollment_count(self, kata_id: str) -> int:
        """
        Get total enrollment count for kata (in_progress + completed).

        Args:
            kata_id: ID of the kata

        Returns:
            Total enrollment count
        """
        pass

    @abstractmethod
    def get_leaderboard(self, limit: int = 100) -> List[LeaderboardEntryFromDB]:
        """
        Get leaderboard ranked by completed kata count.

        Args:
            limit: Maximum number of entries to return

        Returns:
            List of LeaderboardEntryFromDB with: user_id, user_name, user_username, completed_count, in_progress_count
            Ordered by completed_count DESC
        """
        pass

    @abstractmethod
    def bulk_get_user_progress(self, user_id: str, kata_ids: List[str]) -> Dict[str, UserKataProgress]:
        """
        Efficient batch lookup for user progress across multiple katas.

        Args:
            user_id: ID of the user
            kata_ids: List of kata IDs

        Returns:
            Dict mapping kata_id -> UserKataProgress
        """
        pass

    @abstractmethod
    def bulk_get_enrollment_counts(self, kata_ids: List[str]) -> Dict[str, int]:
        """
        Get enrollment counts for multiple katas.

        Args:
            kata_ids: List of kata IDs

        Returns:
            Dict mapping kata_id -> enrollment_count
        """
        pass


class SQLUserKataProgressRepository(UserKataProgressRepository):
    """
    SQL implementation of the User Kata Progress repository.
    Uses SQLModel to interact with PostgreSQL database.
    """

    def start_kata(self, user: User, kata_id: str) -> str:
        """
        Enroll user in kata (create progress record).

        Args:
            user: User object containing user information
            kata_id: ID of the kata

        Returns:
            The ID of the created progress record

        Raises:
            ValueError: If user is already enrolled
        """
        with Session(UserKataProgress.get_engine()) as session:
            # Check if already enrolled
            existing = session.exec(
                select(UserKataProgress).where(
                    and_(UserKataProgress.user_id == user.id, UserKataProgress.kata_id == kata_id)
                )
            ).first()

            if existing:
                logger.warning(f"User {user.id} already enrolled in kata {kata_id}")
                raise ValueError("You are already enrolled in this kata")

            # Create new progress record
            progress = UserKataProgress(
                id=str(uuid4()),
                user_id=user.id,
                kata_id=kata_id,
                user_name=user.name,
                user_username=user.username,
                status=KataProgressStatus.IN_PROGRESS,
                started_at=datetime.now(UTC),
                date=datetime.now(UTC),
                update_date=datetime.now(UTC),
            )

            try:
                session.add(progress)
                session.commit()
                session.refresh(progress)
                logger.info(f"User {user.id} enrolled in kata {kata_id}, progress ID: {progress.id}")
                return progress.id
            except IntegrityError as e:
                session.rollback()
                logger.error(f"Failed to enroll user {user.id} in kata {kata_id}: {e}")
                raise ValueError("You are already enrolled in this kata")

    def complete_kata(self, user_id: str, kata_id: str) -> bool:
        """
        Mark kata as completed.

        Args:
            user_id: ID of the user
            kata_id: ID of the kata

        Returns:
            True if completion was successful

        Raises:
            ValueError: If user is not enrolled
        """
        try:
            with Session(UserKataProgress.get_engine()) as session:
                progress = session.exec(
                    select(UserKataProgress).where(
                        and_(UserKataProgress.user_id == user_id, UserKataProgress.kata_id == kata_id)
                    )
                ).first()

                if not progress:
                    logger.warning(f"Cannot complete kata {kata_id} for user {user_id}: not enrolled")
                    raise ValueError("You are not enrolled in this kata")

                # Update status and completion timestamp
                progress.status = KataProgressStatus.COMPLETED
                progress.completed_at = datetime.now(UTC)
                progress.update_date = datetime.now(UTC)

                session.add(progress)
                session.commit()
                logger.info(f"User {user_id} completed kata {kata_id}")
                return True
        except ValueError:
            # Re-raise ValueError as it's expected behavior
            raise
        except Exception as e:
            logger.error(f"Failed to complete kata {kata_id} for user {user_id}: {e}", exc_info=True)
            raise

    def get_user_progress(self, user_id: str, kata_id: str) -> Optional[UserKataProgress]:
        """
        Get specific user's progress for a kata.

        Args:
            user_id: ID of the user
            kata_id: ID of the kata

        Returns:
            UserKataProgress instance if found, None otherwise
        """
        with Session(UserKataProgress.get_engine()) as session:
            query = select(UserKataProgress).where(
                and_(UserKataProgress.user_id == user_id, UserKataProgress.kata_id == kata_id)
            )
            return session.exec(query).first()

    def get_user_all_progress(
        self, user_id: str, status: Optional[KataProgressStatus] = None
    ) -> List[UserKataProgress]:
        """
        Get all user's kata progress, optionally filtered by status.

        Args:
            user_id: ID of the user
            status: Optional status filter

        Returns:
            List of user's progress records
        """
        with Session(UserKataProgress.get_engine()) as session:
            conditions = [UserKataProgress.user_id == user_id]

            if status:
                conditions.append(UserKataProgress.status == status)

            query = select(UserKataProgress).where(and_(*conditions)).order_by(UserKataProgress.started_at.desc())
            return list(session.exec(query).all())

    def get_kata_enrollment_count(self, kata_id: str) -> int:
        """
        Get total enrollment count for kata (in_progress + completed).

        Args:
            kata_id: ID of the kata

        Returns:
            Total enrollment count
        """
        with Session(UserKataProgress.get_engine()) as session:
            query = select(func.count()).where(UserKataProgress.kata_id == kata_id)
            return session.exec(query).one()

    def get_leaderboard(self, limit: int = 100) -> List[LeaderboardEntryFromDB]:
        """
        Get leaderboard ranked by completed kata count.

        Args:
            limit: Maximum number of entries to return

        Returns:
            List of LeaderboardEntryFromDB with: user_id, user_name, user_username, completed_count, in_progress_count
            Ordered by completed_count DESC
        """
        with Session(UserKataProgress.get_engine()) as session:
            # Aggregate completed count and get user info
            completed_subquery = (
                select(
                    UserKataProgress.user_id,
                    UserKataProgress.user_name,
                    UserKataProgress.user_username,
                    func.count().label("completed_count"),
                )
                .where(UserKataProgress.status == KataProgressStatus.COMPLETED)
                .group_by(UserKataProgress.user_id, UserKataProgress.user_name, UserKataProgress.user_username)
                .subquery()
            )

            # Aggregate in_progress count
            in_progress_subquery = (
                select(
                    UserKataProgress.user_id,
                    func.count().label("in_progress_count"),
                )
                .where(UserKataProgress.status == KataProgressStatus.IN_PROGRESS)
                .group_by(UserKataProgress.user_id)
                .subquery()
            )

            # Join completed and in_progress counts
            query = (
                select(
                    completed_subquery.c.user_id,
                    completed_subquery.c.user_name,
                    completed_subquery.c.user_username,
                    func.coalesce(completed_subquery.c.completed_count, 0).label("completed_count"),
                    func.coalesce(in_progress_subquery.c.in_progress_count, 0).label("in_progress_count"),
                )
                .outerjoin(in_progress_subquery, completed_subquery.c.user_id == in_progress_subquery.c.user_id)
                .order_by(desc("completed_count"))
                .limit(limit)
            )

            results = session.exec(query).all()

            return [
                LeaderboardEntryFromDB(
                    user_id=row[0],
                    user_name=row[1],
                    user_username=row[2],
                    completed_count=row[3],
                    in_progress_count=row[4],
                )
                for row in results
            ]

    def bulk_get_user_progress(self, user_id: str, kata_ids: List[str]) -> Dict[str, UserKataProgress]:
        """
        Efficient batch lookup for user progress across multiple katas.

        Args:
            user_id: ID of the user
            kata_ids: List of kata IDs

        Returns:
            Dict mapping kata_id -> UserKataProgress
        """
        if not kata_ids:
            return {}

        with Session(UserKataProgress.get_engine()) as session:
            query = select(UserKataProgress).where(
                and_(UserKataProgress.user_id == user_id, UserKataProgress.kata_id.in_(kata_ids))
            )
            results = session.exec(query).all()

            return {progress.kata_id: progress for progress in results}

    def bulk_get_enrollment_counts(self, kata_ids: List[str]) -> Dict[str, int]:
        """
        Get enrollment counts for multiple katas.

        Args:
            kata_ids: List of kata IDs

        Returns:
            Dict mapping kata_id -> enrollment_count
        """
        if not kata_ids:
            return {}

        with Session(UserKataProgress.get_engine()) as session:
            query = (
                select(UserKataProgress.kata_id, func.count().label("count"))
                .where(UserKataProgress.kata_id.in_(kata_ids))
                .group_by(UserKataProgress.kata_id)
            )
            results = session.exec(query).all()

            return {row[0]: row[1] for row in results}
