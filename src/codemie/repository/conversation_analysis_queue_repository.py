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

from datetime import datetime, timedelta, UTC
from typing import List
from uuid import uuid4

from sqlalchemy import func
from sqlmodel import Session, select, and_, or_

from codemie.clients.postgres import PostgresClient
from codemie.configs import logger
from codemie.rest_api.models.conversation_analysis import ConversationAnalysisQueue, AnalysisStatus


class ConversationAnalysisQueueRepository:
    """Repository for conversation analysis queue operations"""

    @staticmethod
    async def add_conversations_to_queue(conversation_ids: List[str]) -> int:
        """
        Add multiple conversations to analysis queue (leader pod only).

        Handles reprocessing: Allows same conversation_id to be queued multiple times,
        but only if no pending/processing record exists.

        Args:
            conversation_ids: List of conversation IDs to analyze

        Returns:
            Number of records inserted (skips duplicates with pending/processing status)
        """
        if not conversation_ids:
            return 0

        with Session(PostgresClient.get_engine()) as session:
            # Batch fetch all existing pending/processing records for given conversation IDs
            # This reduces N queries to 1 query
            existing_records = session.exec(
                select(ConversationAnalysisQueue.conversation_id).where(
                    and_(
                        ConversationAnalysisQueue.conversation_id.in_(conversation_ids),
                        or_(
                            ConversationAnalysisQueue.status == AnalysisStatus.PENDING,
                            ConversationAnalysisQueue.status == AnalysisStatus.PROCESSING,
                        ),
                    )
                )
            ).all()

            # Create set for O(1) lookup
            existing_conv_ids = set(existing_records)
            skipped_count = len(existing_conv_ids)

            # Collect items to insert (filter out existing)
            queue_items_to_insert = []
            now = datetime.now(UTC)

            for conv_id in conversation_ids:
                if conv_id in existing_conv_ids:
                    continue  # Skip - already being processed

                # Prepare new queue item (allows duplicates for reprocessing)
                queue_item = ConversationAnalysisQueue(
                    id=str(uuid4()),
                    conversation_id=conv_id,
                    status=AnalysisStatus.PENDING,
                    retry_count=0,
                    date=now,
                    update_date=now,
                )
                queue_items_to_insert.append(queue_item)

            # Atomic bulk insert - single transaction
            if queue_items_to_insert:
                session.bulk_save_objects(queue_items_to_insert)
                session.commit()

            inserted_count = len(queue_items_to_insert)
            logger.info(
                f"Added {inserted_count} conversations to analysis queue "
                f"(skipped {skipped_count} already pending/processing)"
            )
            return inserted_count

    @staticmethod
    async def claim_batch_for_processing(
        batch_size: int, pod_name: str, max_retries: int = 3
    ) -> List[ConversationAnalysisQueue]:
        """
        Claim a batch of pending records for processing using row-level locking.

        Uses SELECT ... FOR UPDATE SKIP LOCKED for lock-free batch claiming.

        Args:
            batch_size: Number of records to claim
            pod_name: K8s pod name claiming the batch
            max_retries: Maximum retry count to consider

        Returns:
            List of claimed queue items
        """
        with Session(PostgresClient.get_engine()) as session:
            # SELECT ... FOR UPDATE SKIP LOCKED ensures:
            # 1. No two pods claim the same record
            # 2. Pods don't wait for locks (SKIP LOCKED)
            # 3. Transaction isolation guarantees atomicity
            stmt = (
                select(ConversationAnalysisQueue)
                .where(
                    or_(
                        ConversationAnalysisQueue.status == AnalysisStatus.PENDING,
                        and_(
                            ConversationAnalysisQueue.status == AnalysisStatus.FAILED,
                            ConversationAnalysisQueue.retry_count < max_retries,
                        ),
                    )
                )
                .order_by(ConversationAnalysisQueue.date)  # FIFO processing
                .limit(batch_size)
                .with_for_update(skip_locked=True)  # Critical: prevents lock contention
            )

            claimed_items = session.exec(stmt).all()

            if not claimed_items:
                return []

            # Update claimed items to PROCESSING status using bulk update
            now = datetime.now(UTC)
            update_mappings = []

            for item in claimed_items:
                update_mappings.append(
                    {
                        "id": item.id,
                        "status": AnalysisStatus.PROCESSING,
                        "claimed_by_pod": pod_name,
                        "claimed_at": now,
                        "update_date": now,
                    }
                )

            # Atomic bulk update - single transaction
            session.bulk_update_mappings(ConversationAnalysisQueue, update_mappings)
            session.commit()

            # Refresh to get updated state
            for item in claimed_items:
                session.refresh(item)

            logger.info(f"Pod {pod_name} claimed {len(claimed_items)} conversations for analysis")
            return list(claimed_items)

    @staticmethod
    async def mark_completed(queue_id: str) -> None:
        """Mark queue item as completed"""
        with Session(PostgresClient.get_engine()) as session:
            item = session.get(ConversationAnalysisQueue, queue_id)
            if item:
                item.status = AnalysisStatus.COMPLETED
                item.update_date = datetime.now(UTC)
                session.add(item)
                session.commit()

    @staticmethod
    async def mark_failed(queue_id: str, error_message: str) -> None:
        """Mark queue item as failed and increment retry counter"""
        with Session(PostgresClient.get_engine()) as session:
            item = session.get(ConversationAnalysisQueue, queue_id)
            if item:
                item.status = AnalysisStatus.FAILED
                item.retry_count += 1
                item.error_message = error_message[:500]  # Truncate long errors
                item.update_date = datetime.now(UTC)
                session.add(item)
                session.commit()
                logger.warning(
                    f"Conversation {item.conversation_id} analysis failed (attempt {item.retry_count}): {error_message}"
                )

    @staticmethod
    async def get_pending_count() -> int:
        """Get count of pending/failed items in queue"""
        with Session(PostgresClient.get_engine()) as session:
            count = session.exec(
                select(func.count(ConversationAnalysisQueue.id)).where(
                    or_(
                        ConversationAnalysisQueue.status == AnalysisStatus.PENDING,
                        ConversationAnalysisQueue.status == AnalysisStatus.FAILED,
                    )
                )
            ).one()
            return count

    @staticmethod
    async def cleanup_old_records(days_to_keep: int = 30) -> int:
        """
        Delete old completed/failed queue records (optional cleanup task).

        Args:
            days_to_keep: Keep records from last N days, delete older

        Returns:
            Number of records deleted
        """
        cutoff_date = datetime.now(UTC) - timedelta(days=days_to_keep)

        with Session(PostgresClient.get_engine()) as session:
            stmt = select(ConversationAnalysisQueue).where(
                and_(
                    or_(
                        ConversationAnalysisQueue.status == AnalysisStatus.COMPLETED,
                        ConversationAnalysisQueue.status == AnalysisStatus.FAILED,
                    ),
                    ConversationAnalysisQueue.update_date < cutoff_date,
                )
            )

            old_records = session.exec(stmt).all()
            count = len(old_records)

            for record in old_records:
                session.delete(record)

            session.commit()
            logger.info(f"Cleaned up {count} old queue records older than {days_to_keep} days")
            return count
