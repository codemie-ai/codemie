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

import copy
import html
import uuid
from datetime import datetime
from typing import Any, List, TYPE_CHECKING, Optional

from codemie_tools.base.utils import get_encoding
from pydantic import BaseModel
from sqlmodel import select, and_, func, or_, text

from codemie.chains.base import Thought
from codemie.clients.postgres import get_session
from codemie.configs import config, logger
from codemie.core.dependecies import get_stt_openai_client
from codemie.core.models import AssistantChatRequest, UpdateConversationRequest, UpdateAiMessageRequest, TokensUsage
from codemie.core.utils import safe_divide
from codemie.rest_api.models.base import ConversationStatus
from codemie.rest_api.models.conversation import (
    Conversation,
    ConversationMetrics,
    GeneratedMessage,
    UserMark,
    UpsertHistoryRequest,
    ConversationListItem,
)
from codemie.rest_api.models.index import SortOrder
from codemie.service.conversation.history_materializer import materialize_history
from codemie.rest_api.models.conversation_folder import ConversationFolder
from codemie.rest_api.models.feedback import FeedbackRequest, FeedbackDeleteRequest
from codemie.rest_api.models.share.shared_conversation import SharedConversation
from codemie.rest_api.models.standard import AuthorEnum
from codemie.rest_api.security.user import User
from codemie.service.llm_service.llm_service import LLMService
from codemie.service.monitoring.conversation_monitoring_service import ConversationMonitoringService

if TYPE_CHECKING:
    from codemie.rest_api.models.assistant import Assistant
else:
    Assistant = Any


class SpendingGroupBreakdown(BaseModel):
    """Spending breakdown by dimension."""

    dimension_type: str  # "assistant" or "workflow"
    dimension_id: str
    dimension_name: str
    money_spent: float
    input_tokens: int
    output_tokens: int
    conversation_count: Optional[int] = None  # For assistants
    workflow_execution_count: Optional[int] = None  # For workflows
    average_cost_per_item: float


class SpendingAnalyticsResult(BaseModel):
    """Result of spending analytics query from ConversationMetrics."""

    total_money_spent: float
    total_input_tokens: int
    total_output_tokens: int
    total_conversations: int
    spending_breakdown: List[SpendingGroupBreakdown]  # Only assistant breakdown


class ConversationMetricsWithAssistants(BaseModel):
    """Single conversation metric with associated assistant IDs."""

    metric: Any  # ConversationMetrics
    assistant_ids: List[str]


class ConversationMetricsResult(BaseModel):
    """Result of conversation metrics query."""

    total_count: int
    metrics_with_assistants: List[ConversationMetricsWithAssistants]


USER_FIELD_KEY = "user_id.keyword"
CATEGORY_FIELD_KEY = "folder.keyword"


class ConversationService:
    """
    Service class for Conversation and related models, captures all business logic.
    """

    @classmethod
    def upsert_chat_history(
        cls,
        assistant_response: str,
        time_elapsed: float,
        tokens_usage: TokensUsage,
        request: AssistantChatRequest,
        assistant: Assistant,
        user: User,
        thoughts: List[Thought],
        status: ConversationStatus = ConversationStatus.SUCCESS,
    ):
        should_create_conversation = False
        llm_model = request.llm_model if request.llm_model else assistant.llm_model_type

        # Find or create conversation
        conversation = Conversation.find_by_id(request.conversation_id)
        if not conversation:
            # Create since it does not exist
            conversation = Conversation(
                id=request.conversation_id,
                conversation_id=request.conversation_id,
                conversation_name=request.text,
                user_id=user.id,
                user_name=user.name,
                assistant_ids=[assistant.id],
                initial_assistant_id=assistant.id,
                project=assistant.project,
            )
            should_create_conversation = True

        # Determine history_index if it's not provided in the request
        history_index = request.history_index
        if history_index is None:
            max_index = -1
            for message in conversation.history:
                if message.history_index is not None and message.history_index > max_index:
                    max_index = message.history_index
            history_index = max_index + 1

        conversation.update_chat_history(
            user_query=request.text,
            user_query_raw=request.content_raw,
            assistant_id=assistant.id,
            project=assistant.project,
            assistant_response=assistant_response,
            thoughts=thoughts,
            history_index=history_index,
            file_names=request.file_names,
            time_elapsed=time_elapsed,
            input_tokens=tokens_usage.input_tokens,
            output_tokens=tokens_usage.output_tokens,
            money_spent=tokens_usage.money_spent,
        )
        ConversationMonitoringService.send_conversation_metric(
            user, assistant, tokens_usage, time_elapsed, conversation.conversation_id, llm_model, status
        )
        conversation.update_conversation_assistants(assistant.id)

        # Update metrics using DRY helper
        cls._upsert_conversation_metrics(
            conversation_id=request.conversation_id,
            user=user,
            assistant_id=assistant.id,
            conversation=conversation,
            project=assistant.project,
        )

        # Save or update conversation
        conversation.save() if should_create_conversation else conversation.update()

    @classmethod
    def upsert_conversation_with_history(
        cls, conversation_id: str, request: UpsertHistoryRequest, user: User
    ) -> dict[str, Any]:
        """
        Upsert conversation with history (idempotent operation).

        Behavior:
        - If conversation doesn't exist: Create with custom ID and provided history
        - If conversation exists: Append only NEW messages (not already present)
        - Uses timestamp comparison to detect new messages

        This enables incremental updates for bulk history imports from any client.

        Args:
            conversation_id: The conversation ID to upsert
            request: UpsertHistoryRequest with assistant_id, folder, and history
            user: The authenticated user

        Returns:
            dict with keys:
            - conversation_id: str
            - new_messages: int (number of messages added)
            - total_messages: int (total messages in conversation)
            - created: bool (whether conversation was newly created)
        """
        # Sanitize messages (ensure message_raw is populated)
        cls._sanitize_message_raw_fields(request.history)

        # Find or create conversation
        conversation = Conversation.find_by_id(conversation_id)

        if conversation:
            # UPDATE EXISTING - append only new messages
            new_messages = cls._append_new_messages(conversation, request.history, request.assistant_id)
            conversation.update()
            created = False
            logger.info(
                f"Updated existing conversation {conversation_id}: added {len(new_messages)} new messages, "
                f"total={len(conversation.history)}"
            )
        else:
            # CREATE NEW - with all history
            conversation = cls._create_conversation_with_history(
                conversation_id=conversation_id, user=user, request=request
            )
            conversation.save()
            new_messages = request.history
            created = True
            logger.info(f"Created new conversation {conversation_id} with {len(request.history)} messages")

        # Update metrics (DRY helper)
        cls._upsert_conversation_metrics(conversation_id, user, request.assistant_id, conversation)

        # Handle folder (DRY helper)
        if request.folder:
            cls._handle_conversation_folder(request.folder, user.id)

        return {
            "conversation_id": conversation_id,
            "new_messages": len(new_messages),
            "total_messages": len(conversation.history),
            "created": created,
        }

    @classmethod
    def _sanitize_message_raw_fields(cls, messages: List[GeneratedMessage]) -> None:
        """
        Ensure message_raw is populated for all messages (required for UI rendering).

        Modifies messages in-place by adding HTML-escaped message_raw if missing.

        Args:
            messages: List of GeneratedMessage objects to sanitize
        """
        for msg in messages:
            if msg.message and not msg.message_raw:
                # Populate message_raw with HTML-escaped message for UI rendering
                msg.message_raw = html.escape(msg.message)

    @classmethod
    def _append_new_messages(
        cls, conversation: Conversation, new_history: List[GeneratedMessage], assistant_id: str
    ) -> List[GeneratedMessage]:
        """
        Merge or append messages to conversation history using full replacement strategy.

        Strategy:
        - If message with (history_index, role) exists: REPLACE entirely with new message
        - If message is new: APPEND

        This provides idempotency and simplicity - latest sync is source of truth.
        Benefits:
        - Simple: No complex field-by-field merge logic
        - Idempotent: Same request can be sent multiple times safely
        - Correct: Latest data always wins, no stale state

        Args:
            conversation: The existing conversation to update
            new_history: List of messages to merge/append
            assistant_id: The assistant ID to add to conversation.assistant_ids

        Returns:
            List of messages that were added or replaced
        """
        if not new_history:
            return []

        # Build index of existing messages by (history_index, role)
        existing_index = {}
        for idx, msg in enumerate(conversation.history):
            if msg.history_index is not None:
                key = (msg.history_index, msg.role)
                existing_index[key] = idx

        replaced_count = 0
        appended_count = 0

        for new_msg in new_history:
            if new_msg.history_index is None:
                # No history_index - always append (edge case, shouldn't happen normally)
                conversation.history.append(new_msg)
                appended_count += 1
                logger.warning(f"Message without history_index appended (role={new_msg.role})")
                continue

            key = (new_msg.history_index, new_msg.role)

            if key in existing_index:
                # REPLACE: Full message replacement (simple and atomic!)
                existing_idx = existing_index[key]
                conversation.history[existing_idx] = new_msg
                replaced_count += 1

                thought_count = len(new_msg.thoughts) if new_msg.thoughts else 0
                logger.debug(
                    f"Replaced message at history_index={new_msg.history_index}, role={new_msg.role} "
                    f"with {thought_count} thoughts"
                )
            else:
                # APPEND: New message
                conversation.history.append(new_msg)
                existing_index[key] = len(conversation.history) - 1
                appended_count += 1

                thought_count = len(new_msg.thoughts) if new_msg.thoughts else 0
                logger.debug(
                    f"Appended new message at history_index={new_msg.history_index}, role={new_msg.role} "
                    f"with {thought_count} thoughts"
                )

        # Update assistant_ids (avoid duplicates)
        if new_history:
            assistant_ids_set = set(conversation.assistant_ids or [])
            assistant_ids_set.add(assistant_id)
            conversation.assistant_ids = list(assistant_ids_set)

        logger.info(
            f"Conversation {conversation.id}: {appended_count} messages appended, "
            f"{replaced_count} messages replaced (total: {len(conversation.history)})"
        )

        return new_history  # Return all messages (both new and replaced)

    @classmethod
    def _create_conversation_with_history(
        cls, conversation_id: str, user: User, request: UpsertHistoryRequest
    ) -> Conversation:
        """
        Create a new conversation with provided history.

        Args:
            conversation_id: The conversation ID (custom ID from client)
            user: The authenticated user
            request: UpsertHistoryRequest with assistant_id, folder, and history

        Returns:
            New Conversation instance (not yet saved)
        """
        return Conversation(
            id=conversation_id,
            conversation_id=conversation_id,
            user_id=user.id,
            user_name=getattr(user, 'name', None) or user.id,  # Fallback to user_id if name is None
            history=request.history,
            assistant_ids=[request.assistant_id],
            initial_assistant_id=request.assistant_id,
            folder=request.folder,
            conversation_name="",
        )

    @classmethod
    def _upsert_conversation_metrics(
        cls, conversation_id: str, user: User, assistant_id: str, conversation: Conversation, project: str = None
    ) -> None:
        """
        Create or update conversation metrics (DRY helper).

        Args:
            conversation_id: The conversation ID
            user: The authenticated user
            assistant_id: The assistant ID
            conversation: The conversation instance with updated history
            project: Optional project name (for new metrics)
        """
        try:
            # Try to get existing metrics
            conversation_metrics = ConversationMetrics.get_by_conversation_id(conversation_id)
            conversation_metrics.calculate_metrics(conversation)
            conversation_metrics.save()
        except KeyError:
            # Create new metrics if they don't exist
            conversation_metrics = ConversationMetrics(
                conversation_id=conversation_id,
                user_id=user.id,
                user_name=getattr(user, 'name', None) or user.id,  # Fallback to user_id if name is None
                assistant_ids=[assistant_id],
                project=project or conversation.project,  # Use provided project or extract from conversation
            )
            conversation_metrics.calculate_metrics(conversation)
            conversation_metrics.save()

    @classmethod
    def _handle_conversation_folder(cls, folder: str, user_id: str) -> None:
        """
        Create folder if needed, or touch timestamp if it exists (DRY helper).

        Args:
            folder: The folder name
            user_id: The user ID
        """
        existing_folder = ConversationFolder.get_by_folder(folder, user_id)

        if not existing_folder:
            # Create folder using model method
            ConversationFolder.create_folder(folder, user_id)
            logger.info(f"Created folder '{folder}' for user {user_id}")
        else:
            # Touch folder to update timestamp
            ConversationFolder.touch_folder(folder, user_id)

    @classmethod
    def add_feedback(cls, request: FeedbackRequest, user: User):
        user_mark = UserMark(
            mark=request.mark,
            comments=request.comments,
            date=datetime.now(),
            type=request.type,
            feedback_id=request.feedback_id,
        )
        conversation = Conversation.get_by_id(request.conversation_id)
        conversation_metrics = ConversationMetrics.get_by_conversation_id(request.conversation_id)

        # Create a new list to ensure SQLAlchemy detects the change
        history = copy.deepcopy(conversation.history)
        # Update the object in the list
        history_message = history[request.message_index]
        history_message.user_mark = user_mark

        # Force SQLAlchemy to detect the change by reassigning the entire list
        conversation.history = history

        conversation_metrics.calculate_metrics(conversation)

        # Send feedback metric for monitoring
        ConversationMonitoringService.send_feedback_metric(
            conversation_id=request.conversation_id,
            assistant_id=request.assistant_id,
            mark=request.mark,
            message_index=request.message_index,
            feedback_id=request.feedback_id,
            comments=request.comments,
            user=user,
            request_type=request.type,
            app_name=request.appName,
            repo_name=request.repoName,
            index_type=request.indexType,
        )

        # Update with refresh to ensure changes are committed
        conversation.update()
        conversation_metrics.update()

    @classmethod
    def remove_feedback(cls, request: FeedbackDeleteRequest, user: User):
        conversation = Conversation.get_by_id(request.conversation_id)
        conversation_metrics = ConversationMetrics.get_by_conversation_id(request.conversation_id)

        # Create a new list to ensure SQLAlchemy detects the change
        history = copy.deepcopy(conversation.history)

        # Update the object in the list
        if request.author == AuthorEnum.OPERATOR:
            history[request.message_index].operator_mark = None
        else:
            history[request.message_index].user_mark = None

        # Force SQLAlchemy to detect the change by reassigning the entire list
        conversation.history = history

        conversation_metrics.calculate_metrics(conversation)

        # Send metric for feedback deletion
        ConversationMonitoringService.send_feedback_delete_metric(
            conversation_id=request.conversation_id,
            feedback_id=request.feedback_id,
            message_index=request.message_index,
            assistant_id=request.assistant_id,
            user=user,
        )

        conversation.update()
        conversation_metrics.update()

    @classmethod
    def calculate_tokens(cls, text: str):
        encoding = get_encoding(LLMService.BASE_NAME_GPT_41_MINI)
        return len(encoding.encode(str(text)))

    @classmethod
    def create_conversation(
        cls,
        user: User,
        initial_assistant_id: str = None,
        folder: str = None,
        mcp_server_single_usage: bool = False,
        is_workflow_conversation: bool = False,
    ):
        conversation_id = str(uuid.uuid4())
        conversation = Conversation(
            id=conversation_id,
            conversation_id=conversation_id,
            conversation_name='',
            user_id=user.id,
            user_name=user.name,
            history=[],
            assistant_ids=[] if not initial_assistant_id else [initial_assistant_id],
            initial_assistant_id=initial_assistant_id,
            folder=folder,
            mcp_server_single_usage=mcp_server_single_usage,
            is_workflow_conversation=is_workflow_conversation,
        )
        conversation.save(refresh=True)

        # Create metrics using DRY helper
        cls._upsert_conversation_metrics(
            conversation_id=conversation_id,
            user=user,
            assistant_id=initial_assistant_id or '',
            conversation=conversation,
        )

        # Handle folder using DRY helper
        if folder:
            cls._handle_conversation_folder(folder, user.id)

        return conversation

    @classmethod
    def delete_conversation_folder(cls, user: User, folder: str, remove_conversations: bool = False):
        folder_conversations = (
            Conversation.get_all_by_fields(
                {
                    CATEGORY_FIELD_KEY: folder,
                    USER_FIELD_KEY: user.id,
                }
            )
            or []
        )

        if remove_conversations:
            for conversation in folder_conversations:
                Conversation.delete_by_id(conversation.id)
                SharedConversation.delete_by_conversation(conversation.id)
        else:
            for conversation in folder_conversations:
                conversation.folder = ""
                conversation.update(refresh=True)

        ConversationFolder.delete_by_folder(folder, user.id)

    @classmethod
    def update_conversation_folder(cls, user: User, folder: str, new_folder: str):
        """Rename a conversation folder."""
        ConversationFolder.delete_by_folder(folder, user.id)
        # Create new folder using model method
        ConversationFolder.create_folder(new_folder, user.id)

        # Update all conversations in the old folder
        folder_conversations = (
            Conversation.get_all_by_fields(
                {
                    CATEGORY_FIELD_KEY: folder,
                    USER_FIELD_KEY: user.id,
                }
            )
            or []
        )

        for conversation in folder_conversations:
            conversation.folder = new_folder
            conversation.update(refresh=True)

    @classmethod
    def update_conversation(cls, conversation: Conversation, request: UpdateConversationRequest):
        old_folder = conversation.folder

        if request.name:
            conversation.conversation_name = request.name
        if request.llm_model:
            conversation.llm_model = request.llm_model
        if request.pinned is not None:
            conversation.pinned = request.pinned
        if request.folder is not None:
            conversation.folder = request.folder
        if request.active_assistant_id and request.active_assistant_id in conversation.assistant_ids:
            # Make active_assistant_id to be the first in assistant_ids array
            assistant_ids = list(conversation.assistant_ids)
            assistant_ids.insert(0, assistant_ids.pop(assistant_ids.index(request.active_assistant_id)))
            conversation.assistant_ids = assistant_ids

        conversation.update()

        # Update folder timestamps when conversation is moved between folders
        if request.folder is not None and old_folder != request.folder and request.folder:
            # Update new folder timestamp
            ConversationFolder.touch_folder(request.folder, conversation.user_id)

        return conversation

    @classmethod
    def remove_conversation_history_index(cls, conversation: Conversation, history_index: int):
        conversation.history = [
            history_message
            for history_message in conversation.history
            if history_message.history_index is not history_index
        ]
        for history_message in conversation.history:
            current_index = history_message.history_index
            history_message.history_index = current_index if current_index < history_index else current_index - 1

        conversation.update_conversation_assistants()
        conversation.update(refresh=True)
        return conversation

    @classmethod
    def clear_conversation_history(cls, conversation: Conversation):
        conversation.history = []

        conversation.update_conversation_assistants()
        conversation.update(refresh=True)
        return conversation

    @classmethod
    def update_conversation_ai_message(
        cls, conversation: Conversation, history_index: int, request: UpdateAiMessageRequest
    ):
        messages = [
            history_message
            for history_message in conversation.history
            if history_message.history_index == history_index
        ]
        user_message = messages[request.message_index * 2]
        ai_message = messages[request.message_index * 2 + 1]
        if user_message and ai_message:
            new_user_message = copy.deepcopy(user_message)
            new_ai_message = copy.deepcopy(ai_message)
            new_ai_message.message = request.message

            conversation.history.append(new_user_message)
            conversation.history.append(new_ai_message)

        conversation.update(refresh=True)
        return conversation

    @classmethod
    def recognize_speech(cls, file):
        client = get_stt_openai_client()
        return client.audio.transcriptions.create(
            model=config.STT_MODEL_NAME,
            file=file,
        ).text

    @classmethod
    def get_conversation_metrics_with_filters(
        cls,
        user_name: Optional[str] = None,
        assistant_ids: Optional[List[str]] = None,
        project: Optional[str] = None,
        since_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> ConversationMetricsResult:
        """
        Query conversation metrics with platform analytics filters.

        Args:
            user_name: Filter by user name (case-insensitive)
            assistant_ids: Filter by assistant IDs (conversations with ANY of these assistants)
            project: Filter by project name
            since_date: Filter by creation date (>=)
            limit: Max results
            offset: Pagination offset

        Returns:
            ConversationMetricsResult with total count and list of metrics with assistant IDs
        """
        filters = []

        if user_name:
            filters.append(func.lower(ConversationMetrics.user_name) == user_name.lower())

        if project:
            filters.append(ConversationMetrics.project == project)

        if since_date:
            filters.append(ConversationMetrics.update_date >= since_date)

        with get_session() as session:
            # Build join query to get assistant_ids from Conversation
            join_stmt = select(ConversationMetrics, Conversation.assistant_ids).join(
                Conversation, ConversationMetrics.conversation_id == Conversation.conversation_id
            )

            # Add assistant_ids filter if specified
            if assistant_ids:
                assistant_filters = [func.jsonb_exists(Conversation.assistant_ids, aid) for aid in assistant_ids]
                filters.append(or_(*assistant_filters))

            # Apply filters
            if filters:
                join_stmt = join_stmt.where(and_(*filters))

            # Count query
            count_stmt = select(func.count(func.distinct(ConversationMetrics.id)))
            count_stmt = count_stmt.select_from(ConversationMetrics).join(
                Conversation, ConversationMetrics.conversation_id == Conversation.conversation_id
            )
            if filters:
                count_stmt = count_stmt.where(and_(*filters))
            total_count = session.exec(count_stmt).one()

            # Data query with pagination
            join_stmt = join_stmt.offset(offset).limit(limit).order_by(ConversationMetrics.update_date.desc())
            results = session.exec(join_stmt).all()

        # Transform results to structured objects
        metrics_with_assistants = [
            ConversationMetricsWithAssistants(metric=metric, assistant_ids=assistant_ids or [])
            for metric, assistant_ids in results
        ]

        return ConversationMetricsResult(total_count=total_count, metrics_with_assistants=metrics_with_assistants)

    @classmethod
    def get_raw_conversations_with_filters(
        cls,
        user_name: Optional[str] = None,
        assistant_ids: Optional[List[str]] = None,
        project: Optional[str] = None,
        since_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[int, List[Conversation]]:
        """
        Query raw conversations with platform analytics filters.

        Args:
            user_name: Filter by user name (case-insensitive)
            assistant_ids: Filter by assistant IDs (conversations with ANY of these assistants)
            project: Filter by project name
            since_date: Filter by creation date (>=)
            limit: Max results
            offset: Pagination offset

        Returns:
            Tuple of (total_count, conversations_list)
        """
        filters = []

        if user_name:
            filters.append(func.lower(Conversation.user_name) == user_name.lower())

        if assistant_ids:
            # Filter conversations that have ANY of the specified assistant IDs
            assistant_filters = [func.jsonb_exists(Conversation.assistant_ids, aid) for aid in assistant_ids]
            filters.append(or_(*assistant_filters))

        if project:
            filters.append(Conversation.project == project)

        if since_date:
            filters.append(Conversation.update_date >= since_date)

        with get_session() as session:
            # Count query
            count_stmt = select(func.count(Conversation.id))
            if filters:
                count_stmt = count_stmt.where(and_(*filters))
            total_count = session.exec(count_stmt).one()

            # Data query with pagination
            stmt = select(Conversation)
            if filters:
                stmt = stmt.where(and_(*filters))
            stmt = stmt.order_by(Conversation.update_date.desc())
            stmt = stmt.offset(offset).limit(limit)
            conversations = session.exec(stmt).all()

        return total_count, list(conversations)

    @classmethod
    def get_spending_analytics(
        cls,
        user_name: Optional[str] = None,
        assistant_id: Optional[str] = None,
        project: Optional[str] = None,
        since_date: Optional[datetime] = None,
        include_breakdown: bool = False,
    ) -> SpendingAnalyticsResult:
        """
        Aggregate spending data from conversations.

        Data sources:
        ConversationMetrics: Token counts, conversation counts, spending details by assistants

        Args:
            user_name: Filter by user name (case-insensitive)
            assistant_id: Filter by assistant ID
            project: Filter by project name
            since_date: Filter by creation date (>=)
            include_breakdown: Include detailed breakdown by assistants

        Returns:
            SpendingAnalyticsResult with conversation spending data
        """
        # Step 1: Get ConversationMetrics data (tokens, conversations, spending details)
        metrics_data = cls._get_conversation_metrics_data(
            user_name=user_name,
            assistant_id=assistant_id,
            project=project,
            since_date=since_date,
        )

        # Step 2: Build assistant breakdown if requested
        spending_breakdown = []
        if include_breakdown:
            spending_breakdown = cls._get_assistant_spending_breakdown(
                user_name=user_name,
                assistant_id=assistant_id,
                project=project,
                since_date=since_date,
            )

        return SpendingAnalyticsResult(
            total_money_spent=metrics_data['total_money_spent'],
            total_input_tokens=metrics_data['total_input_tokens'],
            total_output_tokens=metrics_data['total_output_tokens'],
            total_conversations=metrics_data['total_conversations'],
            spending_breakdown=spending_breakdown,
        )

    @classmethod
    def _get_conversation_metrics_data(
        cls,
        user_name: Optional[str] = None,
        assistant_id: Optional[str] = None,
        project: Optional[str] = None,
        since_date: Optional[datetime] = None,
    ) -> dict:
        """
        Get aggregated data from ConversationMetrics.

        Returns:
            Dict with keys: total_money_spent, total_input_tokens, total_output_tokens, total_conversations
        """
        filters = []

        if user_name:
            filters.append(func.lower(ConversationMetrics.user_name) == user_name.lower())

        if project:
            filters.append(ConversationMetrics.project == project)

        if since_date:
            filters.append(ConversationMetrics.update_date >= since_date)

        # Join with Conversation if filtering by assistant_id
        needs_join = assistant_id is not None

        with get_session() as session:
            total_stmt = select(
                func.sum(ConversationMetrics.total_money_spent).label('total_money'),
                func.sum(ConversationMetrics.total_input_tokens).label('total_input'),
                func.sum(ConversationMetrics.total_output_tokens).label('total_output'),
                func.count(func.distinct(ConversationMetrics.conversation_id)).label('total_conversations'),
            )

            if needs_join:
                total_stmt = total_stmt.select_from(ConversationMetrics).join(
                    Conversation, ConversationMetrics.conversation_id == Conversation.conversation_id
                )
                filters.append(func.jsonb_exists(Conversation.assistant_ids, assistant_id))

            if filters:
                total_stmt = total_stmt.where(and_(*filters))

            result = session.exec(total_stmt).one()

        return {
            'total_money_spent': float(result.total_money or 0),
            'total_input_tokens': int(result.total_input or 0),
            'total_output_tokens': int(result.total_output or 0),
            'total_conversations': int(result.total_conversations or 0),
        }

    @classmethod
    def _get_assistant_spending_breakdown(
        cls,
        user_name: Optional[str] = None,
        assistant_id: Optional[str] = None,
        project: Optional[str] = None,
        since_date: Optional[datetime] = None,
    ) -> List[SpendingGroupBreakdown]:
        """
        Get spending breakdown by assistant.

        Aggregates spending data from ConversationMetrics, grouping by assistant IDs.
        Since assistant_ids is a JSONB array in Conversation, we fetch the data and
        aggregate in Python to avoid raw SQL.

        Note: To prevent memory issues, this method limits results to 10,000 conversations.
        For larger datasets, use additional filters (date range, project, user_name).

        Returns:
            List of assistant spending breakdowns
        """
        from collections import defaultdict

        # Build filters for query
        filters = []

        if user_name:
            filters.append(func.lower(ConversationMetrics.user_name) == user_name.lower())

        if project:
            filters.append(ConversationMetrics.project == project)

        if since_date:
            filters.append(ConversationMetrics.update_date >= since_date)

        # Query ConversationMetrics with Conversation to get assistant_ids
        # Limit to 10K records to prevent memory issues
        max_records = 10000
        with get_session() as session:
            stmt = select(ConversationMetrics, Conversation.assistant_ids).join(
                Conversation, ConversationMetrics.conversation_id == Conversation.conversation_id
            )

            # Add assistant_id filter if specified
            if assistant_id:
                filters.append(func.jsonb_exists(Conversation.assistant_ids, assistant_id))

            if filters:
                stmt = stmt.where(and_(*filters))

            # Add limit and ordering
            stmt = stmt.order_by(ConversationMetrics.update_date.desc()).limit(max_records)

            results = session.exec(stmt).all()

        # Aggregate spending by assistant in Python
        assistant_data = defaultdict(
            lambda: {
                "money_spent": 0.0,
                "input_tokens": 0,
                "output_tokens": 0,
                "conversation_ids": set(),
            }
        )

        for metric, assistant_ids in results:
            # Distribute metrics across all assistants in the conversation
            for aid in assistant_ids or []:
                assistant_data[aid]["money_spent"] += metric.total_money_spent or 0.0
                assistant_data[aid]["input_tokens"] += metric.total_input_tokens or 0
                assistant_data[aid]["output_tokens"] += metric.total_output_tokens or 0
                assistant_data[aid]["conversation_ids"].add(metric.conversation_id)

        # Batch fetch all assistant names to avoid N+1 queries
        assistant_names = cls._batch_fetch_assistant_names(list(assistant_data.keys()))

        # Transform aggregated data to SpendingGroupBreakdown
        breakdown = []
        for assistant_id_str, data in assistant_data.items():
            conv_count = len(data["conversation_ids"])
            avg_cost = safe_divide(data["money_spent"], conv_count)

            breakdown.append(
                cls._create_assistant_spending_breakdown(
                    assistant_id=assistant_id_str,
                    assistant_name=assistant_names.get(assistant_id_str, assistant_id_str),
                    money_spent=data["money_spent"],
                    input_tokens=data["input_tokens"],
                    output_tokens=data["output_tokens"],
                    conversation_count=conv_count,
                    average_cost=avg_cost,
                )
            )

        # Sort by money spent (descending)
        breakdown.sort(key=lambda x: x.money_spent, reverse=True)

        return breakdown

    @classmethod
    def _batch_fetch_assistant_names(cls, assistant_ids: List[str]) -> dict[str, str]:
        """
        Batch fetch assistant names for multiple assistant IDs.

        Args:
            assistant_ids: List of assistant IDs to fetch

        Returns:
            Dictionary mapping assistant_id -> assistant_name
        """
        from codemie.rest_api.models.assistant import Assistant

        if not assistant_ids:
            return {}

        try:
            # Fetch all assistants in one query without permission checks
            # Permission validation is already done at the tool level
            assistants = Assistant.get_by_ids_no_permission_check(assistant_ids)
            return {assistant.id: assistant.name for assistant in (assistants or [])}
        except Exception as e:
            logger.warning(f"Failed to batch fetch assistant names: {e}")
            return {}

    @classmethod
    def _create_assistant_spending_breakdown(
        cls,
        assistant_id: str,
        assistant_name: str,
        money_spent: float,
        input_tokens: int,
        output_tokens: int,
        conversation_count: int,
        average_cost: float,
    ) -> SpendingGroupBreakdown:
        """
        Create SpendingGroupBreakdown for an assistant.

        Args:
            assistant_id: The assistant ID
            assistant_name: The assistant name (pre-fetched)
            money_spent: Total money spent
            input_tokens: Total input tokens
            output_tokens: Total output tokens
            conversation_count: Number of conversations
            average_cost: Average cost per conversation

        Returns:
            SpendingGroupBreakdown instance
        """
        return SpendingGroupBreakdown(
            dimension_type="assistant",
            dimension_id=assistant_id,
            dimension_name=assistant_name,
            money_spent=money_spent,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            conversation_count=conversation_count,
            workflow_execution_count=None,
            average_cost_per_item=average_cost,
        )

    @classmethod
    def get_conversation_analytics_with_metrics(
        cls,
        user_name: Optional[str] = None,
        project: Optional[str] = None,
        since_date: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[int, List[tuple]]:
        """
        Query conversation analytics with joined metrics data.

        Fetches conversation analytics with filters and batch-joins metrics data
        for comprehensive conversation analysis including both qualitative insights
        and quantitative measurements.

        Args:
            user_name: Filter by user name (case-insensitive)
            project: Filter by project name
            since_date: Filter by last analysis date (>=)
            limit: Max results
            offset: Pagination offset

        Returns:
            Tuple of (total_count, list of (ConversationAnalytics, ConversationMetrics or None))
        """
        from codemie.rest_api.models.conversation_analysis import ConversationAnalytics
        from sqlmodel import select, and_, func

        # Build filters for analytics query
        filters = []

        if user_name:
            filters.append(func.lower(ConversationAnalytics.user_name) == user_name.lower())

        if project:
            filters.append(ConversationAnalytics.project == project)

        if since_date:
            filters.append(ConversationAnalytics.last_analysis_date >= since_date)

        with get_session() as session:
            # Count query
            count_stmt = select(func.count(ConversationAnalytics.id))
            if filters:
                count_stmt = count_stmt.where(and_(*filters))
            total_count = session.exec(count_stmt).one()

            # Data query with pagination
            analytics_stmt = select(ConversationAnalytics)
            if filters:
                analytics_stmt = analytics_stmt.where(and_(*filters))
            analytics_stmt = (
                analytics_stmt.order_by(ConversationAnalytics.last_analysis_date.desc()).offset(offset).limit(limit)
            )
            analytics_results = session.exec(analytics_stmt).all()

            # Extract conversation IDs for batch metrics fetch
            conversation_ids = [a.conversation_id for a in analytics_results]

            # Batch fetch metrics by conversation IDs
            metrics_map = {}
            if conversation_ids:
                metrics_stmt = select(ConversationMetrics).where(
                    ConversationMetrics.conversation_id.in_(conversation_ids)
                )
                metrics_results = session.exec(metrics_stmt).all()
                metrics_map = {m.conversation_id: m for m in metrics_results}

        # Combine analytics with metrics (maintaining order)
        combined_results = [(analytics, metrics_map.get(analytics.conversation_id)) for analytics in analytics_results]

        return total_count, combined_results

    # ========== Pagination Helpers ==========
    @classmethod
    def get_user_conversations_paginated(
        cls,
        user_id: str,
        page: int,
        per_page: int,
    ) -> List[ConversationListItem]:
        """DB-level paginated list of user conversations.

        Selects only the scalar columns required for ConversationListItem plus SQL-level
        MIN/MAX subqueries for timestamp bounds. The full ``history`` column is never
        fetched, avoiding large JSONB payloads crossing the network for list requests.
        """
        offset = page * per_page

        with get_session() as session:
            stmt = text("""
                SELECT
                    conversation_id,
                    conversation_name,
                    folder,
                    assistant_ids,
                    initial_assistant_id,
                    pinned,
                    date,
                    update_date,
                    is_workflow_conversation,
                    COALESCE(history->0->>'message', '') AS first_message,
                    (SELECT MIN((elem->>'date')::timestamptz)
                     FROM jsonb_array_elements(COALESCE(history, '[]'::jsonb)) AS elem
                     WHERE NULLIF(TRIM(elem->>'date'), '') IS NOT NULL) AS very_first_msg_at,
                    (SELECT MAX((elem->>'date')::timestamptz)
                     FROM jsonb_array_elements(COALESCE(history, '[]'::jsonb)) AS elem
                     WHERE NULLIF(TRIM(elem->>'date'), '') IS NOT NULL) AS very_last_msg_at
                FROM conversations
                WHERE user_id = :uid
                ORDER BY update_date DESC NULLS LAST
                OFFSET :off LIMIT :lim
            """).bindparams(uid=user_id, off=offset, lim=per_page)
            rows = list(session.exec(stmt).all())

        result = []
        for row in rows:
            is_workflow = row.is_workflow_conversation or False
            first_msg = row.first_message or ""
            # Mirror Conversation.get_conversation_name() truncation logic.
            name = (
                row.conversation_name
                or (first_msg[:50] + "..." if first_msg and len(first_msg) > 50 else first_msg)
                or None
            )
            result.append(
                ConversationListItem(
                    id=row.conversation_id,
                    name=name,
                    folder=row.folder,
                    assistant_ids=row.assistant_ids,
                    initial_assistant_id=row.initial_assistant_id,
                    pinned=row.pinned,
                    date=row.update_date or row.date,
                    is_workflow=is_workflow,
                    workflow_id=row.initial_assistant_id if is_workflow else None,
                    conversation_id=row.conversation_id if is_workflow else None,
                    very_first_msg_at=row.very_first_msg_at,
                    very_last_msg_at=row.very_last_msg_at,
                )
            )
        return result

    @classmethod
    def get_conversation_history_slice(
        cls,
        conversation_id: str,
        page: int,
        per_page: int,
        sort_order: Optional[SortOrder] = None,
    ) -> tuple[Conversation | None, int, datetime | None, datetime | None]:
        """
        Retrieves a DB-level paginated and optionally sorted slice of conversation history.

        Returns:
            tuple: (Conversation instance with sliced history or None, total message count,
            first message timestamp, last message timestamp).
        """
        offset = page * per_page

        with get_session() as session:
            # Retrieve metadata, total count, and true chronological boundary timestamps in one query.
            # MIN/MAX subqueries scan the JSONB array for true first/last by date value,
            # matching the semantics of the Python get_timestamp_bounds() used in other paths.
            meta_stmt = text("""
                SELECT
                    id, conversation_id, conversation_name, llm_model, folder, pinned,
                    user_id, user_name, assistant_ids, assistant_data, initial_assistant_id,
                    final_user_mark, final_operator_mark, project, mcp_server_single_usage,
                    is_workflow_conversation, conversation_details, assistant_details,
                    user_abilities, date, update_date,
                    jsonb_array_length(COALESCE(history, '[]'::jsonb)) AS total_count,
                    (SELECT MIN((elem->>'date')::timestamptz)
                     FROM jsonb_array_elements(COALESCE(history, '[]'::jsonb)) AS elem
                     WHERE NULLIF(TRIM(elem->>'date'), '') IS NOT NULL) AS very_first_msg_at,
                    (SELECT MAX((elem->>'date')::timestamptz)
                     FROM jsonb_array_elements(COALESCE(history, '[]'::jsonb)) AS elem
                     WHERE NULLIF(TRIM(elem->>'date'), '') IS NOT NULL) AS very_last_msg_at
                FROM conversations
                WHERE conversation_id = :cid
            """).bindparams(cid=conversation_id)
            meta_row = session.exec(meta_stmt).first()

            if not meta_row:
                return None, 0, None, None

            # Map sorting preferences to SQL clauses. Fallback to original JSONB array order.
            order_clauses = {
                SortOrder.DESC: "ORDER BY (elem->>'date')::timestamptz DESC NULLS LAST, ord ASC",
                SortOrder.ASC: "ORDER BY (elem->>'date')::timestamptz ASC NULLS LAST, ord ASC",
            }
            order_clause: str = order_clauses.get(sort_order, "ORDER BY ord")

            # Execute DB-level pagination.
            # CROSS JOIN LATERAL unpacks only the necessary slice via LIMIT and OFFSET.
            slice_stmt = text(f"""
                SELECT elem FROM conversations c
                CROSS JOIN LATERAL jsonb_array_elements(c.history) WITH ORDINALITY AS t(elem, ord)
                WHERE c.conversation_id = :cid
                {order_clause}
                OFFSET :off LIMIT :lim
            """).bindparams(cid=conversation_id, off=offset, lim=per_page)
            rows = session.exec(slice_stmt).all()

        raw_messages = [row[0] if hasattr(row, "__getitem__") else row for row in rows]
        messages = [GeneratedMessage.model_validate(m) for m in raw_messages]

        initial_assistant_id = meta_row.initial_assistant_id
        messages = materialize_history(messages, initial_assistant_id)

        # Exclude synthetic aggregation columns before model instantiation.
        conv_kwargs = {
            k: v
            for k, v in meta_row._mapping.items()
            if k not in ('total_count', 'very_first_msg_at', 'very_last_msg_at')
        }

        return (
            Conversation(**conv_kwargs, history=messages),
            meta_row.total_count,
            meta_row.very_first_msg_at,
            meta_row.very_last_msg_at,
        )
