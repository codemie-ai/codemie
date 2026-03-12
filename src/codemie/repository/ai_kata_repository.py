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
from typing import Optional, List, Dict, Any
from uuid import uuid4

from sqlalchemy import and_, or_
from sqlmodel import Session, select, func

from codemie.configs import logger
from codemie.rest_api.models.ai_kata import AIKata, KataLevel, KataStatus
from codemie.rest_api.models.user_kata_progress import UserKataProgress, KataProgressStatus


class AIKataRepository(ABC):
    """
    Abstract base class for AI Kata repository.
    Defines the interface for AI Kata data operations.
    """

    @abstractmethod
    def create(self, kata: AIKata) -> str:
        """
        Create new kata.

        Args:
            kata: AIKata instance to create

        Returns:
            The ID of the created kata
        """
        pass

    @abstractmethod
    def get_by_id(self, kata_id: str) -> Optional[AIKata]:
        """
        Get kata by ID.

        Args:
            kata_id: ID of the kata

        Returns:
            AIKata instance if found, None otherwise
        """
        pass

    @abstractmethod
    def get_all_published(self, page: int = 1, per_page: int = 20) -> List[AIKata]:
        """
        Get all published katas with pagination.

        Args:
            page: Page number (1-indexed)
            per_page: Items per page

        Returns:
            List of published katas
        """
        pass

    @abstractmethod
    def get_by_level(self, level: KataLevel, published_only: bool = True) -> List[AIKata]:
        """
        Get katas by difficulty level.

        Args:
            level: Kata difficulty level
            published_only: Whether to return only published katas

        Returns:
            List of katas with the specified level
        """
        pass

    @abstractmethod
    def search_by_tags(self, tags: List[str], published_only: bool = True) -> List[AIKata]:
        """
        Search katas by tags (OR logic).

        Args:
            tags: List of tags to search for
            published_only: Whether to return only published katas

        Returns:
            List of katas matching any of the tags
        """
        pass

    @abstractmethod
    def update(self, kata_id: str, updates: Dict[str, Any]) -> bool:
        """
        Update kata fields.

        Args:
            kata_id: ID of the kata to update
            updates: Dictionary of fields to update

        Returns:
            True if update was successful, False otherwise
        """
        pass

    @abstractmethod
    def publish(self, kata_id: str) -> bool:
        """
        Publish kata (set status=PUBLISHED).

        Args:
            kata_id: ID of the kata to publish

        Returns:
            True if publish was successful, False otherwise
        """
        pass

    @abstractmethod
    def unpublish(self, kata_id: str) -> bool:
        """
        Unpublish kata (set status=DRAFT).

        Args:
            kata_id: ID of the kata to unpublish

        Returns:
            True if unpublish was successful, False otherwise
        """
        pass

    @abstractmethod
    def archive(self, kata_id: str) -> bool:
        """
        Archive kata (set status=ARCHIVED).

        Args:
            kata_id: ID of the kata to archive

        Returns:
            True if archive was successful, False otherwise
        """
        pass

    @abstractmethod
    def delete(self, kata_id: str) -> bool:
        """
        Delete kata (hard delete).

        Args:
            kata_id: ID of the kata to delete

        Returns:
            True if delete was successful, False otherwise
        """
        pass

    @abstractmethod
    def count_published(self) -> int:
        """
        Get count of published katas.

        Returns:
            Count of published katas
        """
        pass

    @abstractmethod
    def get_with_enrollment_counts(self, kata_ids: List[str]) -> Dict[str, int]:
        """
        Get enrollment counts for multiple katas.

        Args:
            kata_ids: List of kata IDs

        Returns:
            Dict mapping kata_id -> enrollment_count
        """
        pass

    @abstractmethod
    def list_with_filters(
        self, page: int, per_page: int, filters: Dict[str, Any], user_id: Optional[str] = None
    ) -> tuple[List[AIKata], int]:
        """
        List katas with advanced filtering.

        Args:
            page: Page number (1-indexed)
            per_page: Items per page
            filters: Filter dictionary with optional keys:
                - search: text to search in title/description
                - level: KataLevel enum value
                - tags: list of tag IDs
                - roles: list of role IDs
                - status: KataStatus enum value
                - author: creator user ID
                - progress_status: KataProgressStatus enum value (not_started/in_progress/completed)
            user_id: Optional user ID (required for progress_status filter)

        Returns:
            Tuple of (list of katas, total count)
        """
        pass

    @abstractmethod
    def increment_enrollment_count(self, kata_id: str) -> bool:
        """
        Increment enrollment_count by 1 (when user starts kata).

        Args:
            kata_id: ID of the kata

        Returns:
            True if successful
        """
        pass

    @abstractmethod
    def increment_completed_count(self, kata_id: str) -> bool:
        """
        Increment completed_count by 1 (when user completes kata).

        Args:
            kata_id: ID of the kata

        Returns:
            True if successful
        """
        pass

    @abstractmethod
    def decrement_enrollment_count(self, kata_id: str) -> bool:
        """
        Decrement enrollment_count by 1 (when enrollment is cancelled/deleted).

        Args:
            kata_id: ID of the kata

        Returns:
            True if successful
        """
        pass

    @abstractmethod
    def decrement_completed_count(self, kata_id: str) -> bool:
        """
        Decrement completed_count by 1 (when completion is reverted).

        Args:
            kata_id: ID of the kata

        Returns:
            True if successful
        """
        pass

    @abstractmethod
    def update_reaction_counts(self, kata: AIKata, like_count: int, dislike_count: int) -> bool:
        """
        Update the denormalized reaction counts on the kata model.

        Args:
            kata: The kata entity to update
            like_count: Number of likes
            dislike_count: Number of dislikes

        Returns:
            True if successful
        """
        pass


class SQLAIKataRepository(AIKataRepository):
    """
    SQL implementation of the AI Kata repository.
    Uses SQLModel to interact with PostgreSQL database.
    """

    def create(self, kata: AIKata) -> str:
        """
        Create new kata.

        Args:
            kata: AIKata instance to create

        Returns:
            The ID of the created kata
        """
        with Session(AIKata.get_engine()) as session:
            if not kata.id:
                kata.id = str(uuid4())
            if not kata.date:
                kata.date = datetime.now(UTC)
                kata.update_date = kata.date

            session.add(kata)
            session.commit()
            session.refresh(kata)
            logger.info(f"Created AI Kata with ID {kata.id}, title: {kata.title}")
            return kata.id

    def get_by_id(self, kata_id: str) -> Optional[AIKata]:
        """
        Get kata by ID.

        Args:
            kata_id: ID of the kata

        Returns:
            AIKata instance if found, None otherwise
        """
        with Session(AIKata.get_engine()) as session:
            query = select(AIKata).where(AIKata.id == kata_id)
            return session.exec(query).first()

    def get_all_published(self, page: int = 1, per_page: int = 20) -> List[AIKata]:
        """
        Get all published katas with pagination.

        Args:
            page: Page number (1-indexed)
            per_page: Items per page

        Returns:
            List of published katas
        """
        with Session(AIKata.get_engine()) as session:
            query = (
                select(AIKata)
                .where(AIKata.status == KataStatus.PUBLISHED)
                .order_by(AIKata.date.desc())
                .offset((page - 1) * per_page)
                .limit(per_page)
            )
            return list(session.exec(query).all())

    def get_by_level(self, level: KataLevel, published_only: bool = True) -> List[AIKata]:
        """
        Get katas by difficulty level.

        Args:
            level: Kata difficulty level
            published_only: Whether to return only published katas

        Returns:
            List of katas with the specified level
        """
        with Session(AIKata.get_engine()) as session:
            conditions = [AIKata.level == level]

            if published_only:
                conditions.append(AIKata.status == KataStatus.PUBLISHED)

            query = select(AIKata).where(and_(*conditions)).order_by(AIKata.date.desc())
            return list(session.exec(query).all())

    def search_by_tags(self, tags: List[str], published_only: bool = True) -> List[AIKata]:
        """
        Search katas by tags (OR logic).

        Args:
            tags: List of tags to search for
            published_only: Whether to return only published katas

        Returns:
            List of katas matching any of the tags
        """
        with Session(AIKata.get_engine()) as session:
            conditions = []

            if published_only:
                conditions.append(AIKata.status == KataStatus.PUBLISHED)

            # Use PostgreSQL contains operator for each tag (OR logic)
            tag_conditions = [AIKata.tags.contains([tag]) for tag in tags]

            if tag_conditions:
                conditions.append(or_(*tag_conditions))

            query = select(AIKata).where(and_(*conditions)).order_by(AIKata.date.desc())
            return list(session.exec(query).all())

    def update(self, kata_id: str, updates: Dict[str, Any]) -> bool:
        """
        Update kata fields.

        Args:
            kata_id: ID of the kata to update
            updates: Dictionary of fields to update

        Returns:
            True if update was successful, False otherwise
        """
        try:
            with Session(AIKata.get_engine()) as session:
                # Fetch kata within the same session to avoid N+1 query
                kata = session.get(AIKata, kata_id)
                if not kata:
                    logger.warning(f"Cannot update kata {kata_id}: not found")
                    return False

                for key, value in updates.items():
                    if hasattr(kata, key):
                        setattr(kata, key, value)

                kata.update_date = datetime.now(UTC)
                session.add(kata)
                session.commit()
                logger.info(f"Updated AI Kata {kata_id}")
                return True
        except Exception as e:
            logger.error(f"Failed to update kata {kata_id}: {e}", exc_info=True)
            raise

    def publish(self, kata_id: str) -> bool:
        """
        Publish kata (set status=PUBLISHED).

        Args:
            kata_id: ID of the kata to publish

        Returns:
            True if publish was successful, False otherwise
        """
        return self.update(kata_id, {"status": KataStatus.PUBLISHED})

    def unpublish(self, kata_id: str) -> bool:
        """
        Unpublish kata (set status=DRAFT).

        Args:
            kata_id: ID of the kata to unpublish

        Returns:
            True if unpublish was successful, False otherwise
        """
        return self.update(kata_id, {"status": KataStatus.DRAFT})

    def archive(self, kata_id: str) -> bool:
        """
        Archive kata (set status=ARCHIVED).

        Args:
            kata_id: ID of the kata to archive

        Returns:
            True if archive was successful, False otherwise
        """
        return self.update(kata_id, {"status": KataStatus.ARCHIVED})

    def delete(self, kata_id: str) -> bool:
        """
        Delete kata (hard delete).

        Args:
            kata_id: ID of the kata to delete

        Returns:
            True if delete was successful, False otherwise
        """
        kata = self.get_by_id(kata_id)
        if not kata:
            logger.warning(f"Cannot delete kata {kata_id}: not found")
            return False

        with Session(AIKata.get_engine()) as session:
            session.delete(kata)
            session.commit()
            logger.info(f"Deleted AI Kata {kata_id}")
            return True

    def count_published(self) -> int:
        """
        Get count of published katas.

        Returns:
            Count of published katas
        """
        with Session(AIKata.get_engine()) as session:
            query = select(func.count()).select_from(
                select(AIKata).where(AIKata.status == KataStatus.PUBLISHED).subquery()
            )
            return session.exec(query).one()

    def get_with_enrollment_counts(self, kata_ids: List[str]) -> Dict[str, int]:
        """
        Get enrollment counts for multiple katas.

        Note: This method is deprecated. Use progress_repository.bulk_get_enrollment_counts()
        directly from the service layer instead.

        Args:
            kata_ids: List of kata IDs

        Returns:
            Dict mapping kata_id -> enrollment_count from denormalized enrollment_count field
        """
        if not kata_ids:
            return {}

        with Session(AIKata.get_engine()) as session:
            query = select(AIKata.id, AIKata.enrollment_count).where(AIKata.id.in_(kata_ids))
            results = session.exec(query).all()
            return {row[0]: row[1] for row in results}

    def _build_search_condition(self, search_text: str):
        """Build search condition for title and description."""
        search_pattern = f"%{search_text}%"
        return or_(AIKata.title.ilike(search_pattern), AIKata.description.ilike(search_pattern))

    def _build_level_condition(self, level):
        """Build level filter condition."""
        if isinstance(level, str):
            try:
                level = KataLevel(level)
            except ValueError:
                logger.warning(f"Invalid level filter value: {level}")
                return None
        return AIKata.level == level

    def _build_tags_condition(self, tags: List[str]):
        """Build tags filter condition (OR logic)."""
        if not isinstance(tags, list) or not tags:
            return None
        tag_conditions = [AIKata.tags.contains([tag]) for tag in tags]
        return or_(*tag_conditions)

    def _build_roles_condition(self, roles: List[str]):
        """Build roles filter condition (OR logic)."""
        if not isinstance(roles, list) or not roles:
            return None
        role_conditions = [AIKata.roles.contains([role]) for role in roles]
        return or_(*role_conditions)

    def _build_status_condition(self, status):
        """Build status filter condition."""
        if isinstance(status, str):
            try:
                status = KataStatus(status)
            except ValueError:
                logger.warning(f"Invalid status filter value: {status}")
                return None
        return AIKata.status == status.value

    def _add_status_filter(self, conditions: List, status) -> None:
        """
        Add status filter to conditions list with fallback handling.

        Args:
            conditions: List of filter conditions to append to
            status: Status value from filters (can be None)
        """
        if status is not None:
            condition = self._build_status_condition(status)
            if condition is not None:
                conditions.append(condition)
            else:
                # Invalid status value provided, log error and use published as fallback
                logger.error(f"Invalid status value in filters, using published as fallback: {status}")
                conditions.append(AIKata.status == KataStatus.PUBLISHED)
        else:
            # No status filter - this should not happen as router always provides status
            # But if it does, default to published for safety
            logger.warning("No status filter provided to repository, defaulting to published")
            conditions.append(AIKata.status == KataStatus.PUBLISHED)

    def _build_filter_conditions(self, filters: Dict[str, Any]) -> List:
        """Build all filter conditions from filters dictionary."""
        conditions = []

        # Text search in title and description
        if search_text := filters.get("search"):
            conditions.append(self._build_search_condition(search_text))

        # Filter by level
        if level := filters.get("level"):
            condition = self._build_level_condition(level)
            if condition is not None:
                conditions.append(condition)

        # Filter by tags (OR logic - matches any tag)
        if tags := filters.get("tags"):
            condition = self._build_tags_condition(tags)
            if condition is not None:
                conditions.append(condition)

        # Filter by roles (OR logic - matches any role)
        if roles := filters.get("roles"):
            condition = self._build_roles_condition(roles)
            if condition is not None:
                conditions.append(condition)

        # Filter by status - ALWAYS required (no default here, router handles it)
        self._add_status_filter(conditions, filters.get("status"))

        # Filter by author (creator)
        if author := filters.get("author"):
            conditions.append(AIKata.creator_id == author)

        return conditions

    def list_with_filters(
        self, page: int, per_page: int, filters: Dict[str, Any], user_id: Optional[str] = None
    ) -> tuple[List[AIKata], int]:
        """
        List katas with advanced filtering.

        Args:
            page: Page number (1-indexed)
            per_page: Items per page
            filters: Filter dictionary with optional keys:
                - search: text to search in title/description
                - level: KataLevel enum value
                - tags: list of tag IDs
                - roles: list of role IDs
                - status: KataStatus enum value
                - author: creator user ID
                - progress_status: KataProgressStatus enum value (not_started/in_progress/completed)
            user_id: Optional user ID (required for progress_status filter)

        Returns:
            Tuple of (list of katas, total count)
        """
        with Session(AIKata.get_engine()) as session:
            # Build all filter conditions (except progress_status)
            conditions = self._build_filter_conditions(filters)

            # Start with base query
            query = select(AIKata)

            # Handle progress_status filter (requires JOIN with user_kata_progress)
            progress_status = filters.get("progress_status")
            if progress_status and user_id:
                if progress_status == KataProgressStatus.NOT_STARTED:
                    # NOT_STARTED: Find katas where user has NO progress record
                    # Use LEFT JOIN and filter where progress.id IS NULL
                    query = query.outerjoin(
                        UserKataProgress,
                        and_(UserKataProgress.kata_id == AIKata.id, UserKataProgress.user_id == user_id),
                    ).where(UserKataProgress.id.is_(None))
                else:
                    # IN_PROGRESS or COMPLETED: Find katas where user HAS progress with matching status
                    # Use INNER JOIN and filter by status
                    query = query.join(
                        UserKataProgress,
                        and_(UserKataProgress.kata_id == AIKata.id, UserKataProgress.user_id == user_id),
                    ).where(UserKataProgress.status == progress_status)

            # Apply other filter conditions
            if conditions:
                query = query.where(and_(*conditions))

            # Sort by enrollment_count (popularity) descending, then by update_date
            query = query.order_by(AIKata.enrollment_count.desc(), AIKata.update_date.desc())

            # Get total count
            count_query = select(func.count()).select_from(query.subquery())
            total = session.exec(count_query).one()

            # Apply pagination
            query = query.offset((page - 1) * per_page).limit(per_page)

            katas = list(session.exec(query).all())
            return katas, total

    def increment_enrollment_count(self, kata_id: str) -> bool:
        """
        Increment enrollment_count by 1 (when user starts kata).

        Args:
            kata_id: ID of the kata

        Returns:
            True if successful
        """
        kata = self.get_by_id(kata_id)
        if not kata:
            logger.warning(f"Cannot increment enrollment_count for kata {kata_id}: not found")
            return False

        with Session(AIKata.get_engine()) as session:
            kata.enrollment_count += 1
            session.add(kata)
            session.commit()
            logger.debug(f"Incremented enrollment_count for kata {kata_id} to {kata.enrollment_count}")
            return True

    def increment_completed_count(self, kata_id: str) -> bool:
        """
        Increment completed_count by 1 (when user completes kata).

        Args:
            kata_id: ID of the kata

        Returns:
            True if successful
        """
        kata = self.get_by_id(kata_id)
        if not kata:
            logger.warning(f"Cannot increment completed_count for kata {kata_id}: not found")
            return False

        with Session(AIKata.get_engine()) as session:
            kata.completed_count += 1
            session.add(kata)
            session.commit()
            logger.debug(f"Incremented completed_count for kata {kata_id} to {kata.completed_count}")
            return True

    def decrement_enrollment_count(self, kata_id: str) -> bool:
        """
        Decrement enrollment_count by 1 (when enrollment is cancelled/deleted).

        Args:
            kata_id: ID of the kata

        Returns:
            True if successful
        """
        kata = self.get_by_id(kata_id)
        if not kata:
            logger.warning(f"Cannot decrement enrollment_count for kata {kata_id}: not found")
            return False

        with Session(AIKata.get_engine()) as session:
            kata.enrollment_count = max(0, kata.enrollment_count - 1)  # Prevent negative
            session.add(kata)
            session.commit()
            logger.debug(f"Decremented enrollment_count for kata {kata_id} to {kata.enrollment_count}")
            return True

    def decrement_completed_count(self, kata_id: str) -> bool:
        """
        Decrement completed_count by 1 (when completion is reverted).

        Args:
            kata_id: ID of the kata

        Returns:
            True if successful
        """
        kata = self.get_by_id(kata_id)
        if not kata:
            logger.warning(f"Cannot decrement completed_count for kata {kata_id}: not found")
            return False

        with Session(AIKata.get_engine()) as session:
            kata.completed_count = max(0, kata.completed_count - 1)  # Prevent negative
            session.add(kata)
            session.commit()
            logger.debug(f"Decremented completed_count for kata {kata_id} to {kata.completed_count}")
            return True

    def update_reaction_counts(self, kata: AIKata, like_count: int, dislike_count: int) -> bool:
        """
        Update the denormalized reaction counts on the kata model.

        Args:
            kata: The kata entity to update
            like_count: Number of likes
            dislike_count: Number of dislikes

        Returns:
            True if successful
        """
        try:
            with Session(AIKata.get_engine()) as session:
                kata.unique_likes_count = like_count
                kata.unique_dislikes_count = dislike_count
                kata.update_date = datetime.now(UTC)
                session.add(kata)
                session.commit()
                logger.debug(
                    f"Updated reaction counts for kata {kata.id}, likes={like_count}, dislikes={dislike_count}"
                )
                return True
        except Exception as e:
            logger.error(f"Failed to update reaction counts for kata {kata.id}: {e}", exc_info=True)
            return False
