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

from datetime import datetime, UTC
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import Session, select

from codemie.rest_api.models.user_management import UserKnowledgeBase


class UserKnowledgeBaseRepository:
    """Repository for user-knowledge-base access management (sync SQLModel)"""

    def get_by_user_id(self, session: Session, user_id: str) -> list[UserKnowledgeBase]:
        """Get all knowledge bases for a user

        Args:
            session: Database session
            user_id: User UUID

        Returns:
            List of UserKnowledgeBase records
        """
        statement = select(UserKnowledgeBase).where(UserKnowledgeBase.user_id == user_id)
        return list(session.exec(statement).all())

    def get_by_id(self, session: Session, kb_id: str) -> Optional[UserKnowledgeBase]:
        """Get knowledge base access record by ID

        Args:
            session: Database session
            kb_id: UserKnowledgeBase UUID

        Returns:
            UserKnowledgeBase or None
        """
        statement = select(UserKnowledgeBase).where(UserKnowledgeBase.id == kb_id)
        return session.exec(statement).first()

    def get_by_user_and_kb(self, session: Session, user_id: str, kb_name: str) -> Optional[UserKnowledgeBase]:
        """Get specific knowledge base access for a user

        Args:
            session: Database session
            user_id: User UUID
            kb_name: Knowledge base name

        Returns:
            UserKnowledgeBase or None
        """
        statement = select(UserKnowledgeBase).where(
            UserKnowledgeBase.user_id == user_id, UserKnowledgeBase.kb_name == kb_name
        )
        return session.exec(statement).first()

    def add_kb(self, session: Session, user_id: str, kb_name: str) -> UserKnowledgeBase:
        """Grant knowledge base access to user

        Args:
            session: Database session
            user_id: User UUID
            kb_name: Knowledge base name

        Returns:
            Created UserKnowledgeBase record

        Note:
            Caller should handle duplicate check or rely on DB unique constraint
        """
        now = datetime.now(UTC)
        user_kb = UserKnowledgeBase(user_id=user_id, kb_name=kb_name, date=now, update_date=now)
        session.add(user_kb)
        session.flush()
        session.refresh(user_kb)
        return user_kb

    def remove_kb(self, session: Session, user_id: str, kb_name: str) -> bool:
        """Revoke knowledge base access from user

        Args:
            session: Database session
            user_id: User UUID
            kb_name: Knowledge base name

        Returns:
            True if removed, False if not found
        """
        user_kb = self.get_by_user_and_kb(session, user_id, kb_name)
        if not user_kb:
            return False

        session.delete(user_kb)
        session.flush()
        return True

    def has_access(self, session: Session, user_id: str, kb_name: str) -> bool:
        """Check if user has access to knowledge base

        Args:
            session: Database session
            user_id: User UUID
            kb_name: Knowledge base name

        Returns:
            True if user has access, False otherwise
        """
        return self.get_by_user_and_kb(session, user_id, kb_name) is not None

    def get_kb_names(self, session: Session, user_id: str) -> list[str]:
        """Get list of knowledge base names for a user

        Args:
            session: Database session
            user_id: User UUID

        Returns:
            List of knowledge base names
        """
        kbs = self.get_by_user_id(session, user_id)
        return [kb.kb_name for kb in kbs]

    def delete_all_for_user(self, session: Session, user_id: str) -> int:
        """Delete all knowledge base access for a user

        Args:
            session: Database session
            user_id: User UUID

        Returns:
            Number of records deleted
        """
        kbs = self.get_by_user_id(session, user_id)
        count = len(kbs)
        for kb in kbs:
            session.delete(kb)
        session.flush()
        return count

    # ===========================================
    # Async methods (AsyncSession)
    # ===========================================

    async def aget_by_user_id(self, session: AsyncSession, user_id: str) -> list[UserKnowledgeBase]:
        """Get all knowledge bases for a user (async)"""
        statement = select(UserKnowledgeBase).where(UserKnowledgeBase.user_id == user_id)
        result = await session.execute(statement)
        return list(result.scalars().all())

    async def aadd_kb(self, session: AsyncSession, user_id: str, kb_name: str) -> UserKnowledgeBase:
        """Grant knowledge base access to user (async)"""
        now = datetime.now(UTC).replace(tzinfo=None)
        user_kb = UserKnowledgeBase(user_id=user_id, kb_name=kb_name, date=now, update_date=now)
        session.add(user_kb)
        await session.flush()
        await session.refresh(user_kb)
        return user_kb


# Singleton instance
user_kb_repository = UserKnowledgeBaseRepository()
