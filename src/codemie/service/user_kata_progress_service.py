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

from typing import Optional, List

from fastapi import status
from pydantic import BaseModel, Field

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.ai_kata_repository import AIKataRepository, SQLAIKataRepository
from codemie.repository.user_kata_progress_repository import (
    UserKataProgressRepository,
    SQLUserKataProgressRepository,
)
from codemie.rest_api.models.ai_kata import KataStatus
from codemie.rest_api.models.user_kata_progress import (
    UserKataProgressResponse,
    UserLeaderboardEntry,
    KataProgressStatus,
)
from codemie.rest_api.security.user import User


class UserKataProgressService(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    repository: UserKataProgressRepository = Field(default_factory=SQLUserKataProgressRepository)
    kata_repository: AIKataRepository = Field(default_factory=SQLAIKataRepository)

    def start_kata(self, kata_id: str, user: User) -> str:
        """
        Enroll user in kata.

        Args:
            kata_id: ID of the kata
            user: User object containing user information

        Returns:
            Progress ID

        Raises:
            ExtendedHTTPException: If kata not found, not published, or user already enrolled
        """
        logger.info(f"User {user.id} starting kata {kata_id}")

        # Check if kata exists
        kata = self.kata_repository.get_by_id(kata_id)
        if not kata:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message="Kata not found",
                details=f"Kata with ID {kata_id} not found",
                help="Please verify the kata ID and try again.",
            )

        # Check if kata is published
        if kata.status != KataStatus.PUBLISHED:
            logger.warning(f"User {user.id} attempted to enroll in non-published kata {kata_id}")
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Kata not available",
                details="You can only enroll in published katas",
                help="Please wait for the kata to be published.",
            )

        # Create progress record
        try:
            progress_id = self.repository.start_kata(user, kata_id)
            # Update denormalized enrollment_count in ai_katas table
            self.kata_repository.increment_enrollment_count(kata_id)
            logger.info(f"User {user.id} successfully enrolled in kata {kata_id}, progress ID: {progress_id}")
            return progress_id
        except ValueError as e:
            # User already enrolled
            raise ExtendedHTTPException(
                code=status.HTTP_400_BAD_REQUEST,
                message="Already enrolled",
                details=str(e),
                help="You can continue your progress from where you left off.",
            )

    def complete_kata(self, kata_id: str, user: User) -> bool:
        """
        Mark kata as completed.

        Args:
            kata_id: ID of the kata
            user: User object containing user information

        Returns:
            True if successful

        Raises:
            ExtendedHTTPException: If kata not found or user not enrolled
        """
        logger.info(f"User {user.id} completing kata {kata_id}")

        # Check if kata exists
        kata = self.kata_repository.get_by_id(kata_id)
        if not kata:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message="Kata not found",
                details=f"Kata with ID {kata_id} not found",
                help="Please verify the kata ID and try again.",
            )

        # Mark as completed
        try:
            success = self.repository.complete_kata(user.id, kata_id)
            # Update denormalized completed_count in ai_katas table
            self.kata_repository.increment_completed_count(kata_id)
            logger.info(f"User {user.id} successfully completed kata {kata_id}")
            return success
        except ValueError as e:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message="Not enrolled",
                details=str(e),
                help="You must start the kata before completing it.",
            )

    def get_user_progress(self, kata_id: str, user_id: str) -> Optional[UserKataProgressResponse]:
        """
        Get user's progress for specific kata.

        Args:
            kata_id: ID of the kata
            user_id: ID of the user

        Returns:
            User progress if found, None otherwise
        """
        progress = self.repository.get_user_progress(user_id, kata_id)
        if not progress:
            return None

        return self._to_response(progress)

    def get_user_all_progress(
        self, user_id: str, status: Optional[KataProgressStatus] = None
    ) -> List[UserKataProgressResponse]:
        """
        Get all user's progress, optionally filtered by status.

        Args:
            user_id: ID of the user
            status: Optional status filter

        Returns:
            List of user's progress records
        """
        logger.debug(f"Getting all progress for user {user_id}, status={status}")
        progress_list = self.repository.get_user_all_progress(user_id, status)
        return [self._to_response(progress) for progress in progress_list]

    def get_leaderboard(self, limit: int = 100) -> List[UserLeaderboardEntry]:
        """
        Get leaderboard ranked by completed count.

        Args:
            limit: Maximum number of entries (default 100, max 1000)

        Returns:
            List of leaderboard entries with rank
        """
        # Validate and cap limit
        if limit < 1:
            limit = 100
        if limit > 1000:
            limit = 1000

        logger.debug(f"Getting leaderboard, limit={limit}")

        # Get leaderboard data from repository
        leaderboard_data = self.repository.get_leaderboard(limit)

        # Add rank and convert to response model
        result = []
        for rank, entry in enumerate(leaderboard_data, start=1):
            # Use user_username if available, otherwise fallback to user_id
            username = entry.user_username or entry.user_id
            user_name = entry.user_name or entry.user_id

            result.append(
                UserLeaderboardEntry(
                    user_id=entry.user_id,
                    user_name=user_name,
                    username=username,
                    completed_count=entry.completed_count,
                    in_progress_count=entry.in_progress_count,
                    rank=rank,
                )
            )

        return result

    def _to_response(self, progress) -> UserKataProgressResponse:
        """Convert UserKataProgress to UserKataProgressResponse."""
        return UserKataProgressResponse(
            id=progress.id,
            user_id=progress.user_id,
            kata_id=progress.kata_id,
            status=progress.status,
            started_at=progress.started_at,
            completed_at=progress.completed_at,
        )
