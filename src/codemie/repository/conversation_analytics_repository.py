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
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from codemie.clients.postgres import PostgresClient
from codemie.configs import logger
from codemie.rest_api.models.conversation_analysis import ConversationAnalytics


class ConversationAnalyticsRepository:
    """Repository for conversation analytics operations with UPSERT support"""

    @staticmethod
    async def upsert_analysis(analytics: ConversationAnalytics) -> tuple[ConversationAnalytics, bool]:
        """
        Save or update conversation analysis result (UPSERT pattern).

        If conversation already analyzed, updates existing record.
        Otherwise, inserts new record.

        Args:
            analytics: ConversationAnalytics instance to save/update

        Returns:
            Tuple of (saved_analytics, is_new_record)
            - is_new_record: True if inserted, False if updated
        """
        async with AsyncSession(PostgresClient.get_async_engine()) as session:
            # Check if analytics already exists for this conversation
            result = await session.execute(
                select(ConversationAnalytics).where(ConversationAnalytics.conversation_id == analytics.conversation_id)
            )
            existing = result.scalar_one_or_none()

            if existing:
                # UPDATE: Preserve existing ID, update all other fields
                analytics.id = existing.id
                analytics.date = existing.date  # Keep original creation date
                analytics.update_date = datetime.now(UTC).replace(tzinfo=None)

                # Update all fields
                for key, value in analytics.model_dump(exclude={"id", "date"}).items():
                    setattr(existing, key, value)

                session.add(existing)
                await session.commit()
                await session.refresh(existing)

                logger.info(
                    f"Updated analysis for conversation {analytics.conversation_id} "
                    f"(user: {analytics.user_name}, messages: {analytics.message_count_at_analysis})"
                )
                return existing, False

            else:
                # INSERT: New analysis
                if not analytics.id:
                    analytics.id = str(uuid4())

                analytics.date = datetime.now(UTC).replace(tzinfo=None)
                analytics.update_date = datetime.now(UTC).replace(tzinfo=None)

                session.add(analytics)
                await session.commit()
                await session.refresh(analytics)

                logger.info(
                    f"Created analysis for conversation {analytics.conversation_id} "
                    f"(user: {analytics.user_name}, messages: {analytics.message_count_at_analysis})"
                )
                return analytics, True

    @staticmethod
    async def get_by_conversation_id(conversation_id: str) -> ConversationAnalytics | None:
        """Get existing analysis for conversation"""
        async with AsyncSession(PostgresClient.get_async_engine()) as session:
            result = await session.execute(
                select(ConversationAnalytics).where(ConversationAnalytics.conversation_id == conversation_id)
            )
            return result.scalar_one_or_none()

    @staticmethod
    async def get_reprocessing_candidates(conversation_ids: list[str]) -> dict[str, ConversationAnalytics]:
        """
        Batch fetch analytics for conversations to check reprocessing eligibility.

        Args:
            conversation_ids: List of conversation IDs to check

        Returns:
            Dict mapping conversation_id -> ConversationAnalytics (only for analyzed conversations)
        """
        if not conversation_ids:
            return {}

        async with AsyncSession(PostgresClient.get_async_engine()) as session:
            result = await session.execute(
                select(ConversationAnalytics).where(ConversationAnalytics.conversation_id.in_(conversation_ids))
            )
            results = result.scalars().all()

            return {analytics.conversation_id: analytics for analytics in results}

    @staticmethod
    async def get_statistics() -> dict:
        """
        Get analytics statistics for monitoring.

        Returns:
            Dict with statistics: total_analyzed, avg_message_count, etc.
        """
        async with AsyncSession(PostgresClient.get_async_engine()) as session:
            result = await session.execute(
                select(
                    func.count(ConversationAnalytics.id).label("total_analyzed"),
                    func.avg(ConversationAnalytics.message_count_at_analysis).label("avg_messages"),
                    func.max(ConversationAnalytics.last_analysis_date).label("latest_analysis"),
                )
            )
            stats = result.one()

            return {
                "total_conversations_analyzed": stats.total_analyzed or 0,
                "avg_message_count": float(stats.avg_messages or 0),
                "latest_analysis_date": stats.latest_analysis,
            }
