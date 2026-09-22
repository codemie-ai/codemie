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

import contextvars
import copy
import html
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, TYPE_CHECKING, Optional, NoReturn

from codemie_tools.base.utils import get_encoding
from fastapi import BackgroundTasks, status
from pydantic import BaseModel
from sqlalchemy import bindparam
from sqlalchemy import update as sa_update
from sqlmodel import select, and_, delete, func, or_, text, Session

from codemie.chains.base import Thought
from codemie.clients.postgres import get_session
from codemie.configs import config, logger
from codemie.core.dependecies import get_stt_openai_client
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import AssistantChatRequest, UpdateConversationRequest, UpdateAiMessageRequest, TokensUsage
from codemie.core.utils import safe_divide
from codemie.rest_api.utils.client_context import ClientSource
from codemie.rest_api.models.base import ConversationStatus
from codemie.rest_api.models.conversation import (
    ChatTurnData,
    Conversation,
    ConversationMetrics,
    ConversationFinishResponse,
    ConversationFinishResult,
    GeneratedMessage,
    UserMark,
    UpsertHistoryRequest,
    ConversationListItem,
    ConversationSearchResponse,
    SearchResultItem,
)
from codemie.rest_api.models.index import SortOrder
from codemie.rest_api.models.conversation_folder import ConversationFolder
from codemie.rest_api.models.assistant_folder import (
    AssistantFolderDeleteResponse,
    AssistantFolderListItem,
)
from codemie.rest_api.models.feedback import FeedbackRequest, FeedbackDeleteRequest
from codemie.rest_api.models.share.shared_conversation import SharedConversation
from codemie.rest_api.models.standard import AuthorEnum
from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie.service.assistant.assistant_repository import AssistantRepository, AssistantScope
from codemie.service.chat_naming_service import ChatNamingService
from codemie.service.conversation.history_materializer import materialize_workflow_conversation
from codemie.service.llm_service.llm_service import LLMService
from codemie.service.monitoring.conversation_monitoring_service import ConversationMonitoringService
from codemie.service.monitoring.routing_monitoring_service import RoutingMonitoringService

if TYPE_CHECKING:
    from codemie.rest_api.models.assistant import Assistant
    from codemie.service.request_summary_manager import LLMRun
else:
    Assistant = Any


def _guard_finished(conversation: Conversation) -> None:
    if conversation.finished_at:
        _raise_conversation_finished(conversation.id)


def _raise_conversation_finished(conversation_id: str) -> NoReturn:
    raise ExtendedHTTPException(
        code=status.HTTP_409_CONFLICT,
        message="Conversation is finished",
        details=f"This conversation was closed and can no longer receive new messages (id={conversation_id}).",
        help="Start a new conversation to continue.",
    )


def _raise_conversation_not_found(conversation_id: str) -> NoReturn:
    raise ExtendedHTTPException(
        code=status.HTTP_404_NOT_FOUND,
        message="Conversation not found",
        details=f"The conversation with ID [{conversation_id}] could not be found in the system.",
        help="Please verify the conversation ID and try again. If you believe this is an error, contact support.",
    )


_CLIENT_TYPE_TO_IMPORT_SOURCE: dict[str, str] = {
    "codemie-claude": "claude_cli",
    "codemie-claude-acp": "claude_cli",
    "claude-desktop": "claude_desktop",
    "codemie-codex": "codex",
    "codemie-gemini": "gemini",
    "codemie-copilot": "copilot_cli",
    "codemie-opencode": "opencode",
    "codemie-pi": "pi",
    "codemie-kimi": "kimi",
    "codemie-kimi-acp": "kimi",
}


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


@dataclass
class UpsertChatHistoryParams:
    """Parameters for upserting chat history and recording conversation metrics."""

    request: AssistantChatRequest
    assistant: Assistant
    user: User
    assistant_response: str = ""
    time_elapsed: float = 0.0
    tokens_usage: Optional[TokensUsage] = None
    thoughts: List[Thought] = field(default_factory=list)
    status: ConversationStatus = ConversationStatus.SUCCESS
    user_message_received_at: datetime | None = None
    a2ui_envelopes: list[dict] | None = None
    request_id: Optional[str] = None
    background_tasks: BackgroundTasks | None = None
    llm_runs: Optional[List["LLMRun"]] = None
    in_progress: bool = False
    client_source: ClientSource | None = None

    def __post_init__(self) -> None:
        if self.tokens_usage is None:
            self.tokens_usage = TokensUsage(input_tokens=0, output_tokens=0, money_spent=0.0)
        if self.thoughts is None:
            self.thoughts = []


class ConversationService:
    """
    Service class for Conversation and related models, captures all business logic.
    """

    @staticmethod
    def _truncate_name(msg: str | None) -> str:
        msg = msg or ""
        return (msg[:50] + "...") if len(msg) > 50 else msg

    @staticmethod
    def _get_initial_image_generation_settings(assistant: Assistant | None) -> dict[str, Any]:
        if not assistant:
            return {
                "enable_image_generation": None,
                "image_generation_model": None,
            }

        return {
            "enable_image_generation": getattr(assistant, "enable_image_generation", None),
            "image_generation_model": getattr(assistant, "image_generation_model", None),
        }

    @classmethod
    def _find_or_create_conversation(
        cls, request: AssistantChatRequest, assistant: Assistant, user: User
    ) -> tuple[Conversation, bool, bool]:
        conversation = Conversation.find_by_id(request.conversation_id)
        if not conversation:
            initial_image_settings = cls._get_initial_image_generation_settings(assistant)
            # Create since it does not exist
            conversation = Conversation(
                id=request.conversation_id,
                conversation_id=request.conversation_id,
                conversation_name=cls._truncate_name(request.text),
                user_id=user.id,
                user_name=user.name,
                assistant_ids=[assistant.id],
                initial_assistant_id=assistant.id,
                project=assistant.project,
                enable_image_generation=initial_image_settings["enable_image_generation"],
                image_generation_model=initial_image_settings["image_generation_model"],
            )
            return conversation, True, True

        is_first_turn = not conversation.history or all(
            getattr(msg, "history_index", None) in (None, 0) for msg in conversation.history
        )
        if is_first_turn and conversation.conversation_name in (
            None,
            "",
            cls._truncate_name(request.text),
        ):
            # Conversation was pre-created without a real name — either genuinely
            # unnamed, or only carrying the UI's optimistic client-side truncated
            # name (codemie-ui sets this via PUT /v1/conversations/{id} before the
            # streaming response completes, see _updateChatNameIfNeeded). Either
            # way it's still eligible for LLM naming; only a real user-chosen name
            # should block it.
            conversation.conversation_name = cls._truncate_name(request.text)
            return conversation, False, True

        return conversation, False, False

    @classmethod
    def find_or_create_conversation(cls, request: AssistantChatRequest, assistant: Assistant, user: User) -> None:
        """Persist a Conversation row for request.conversation_id if none exists yet.

        Tool-call confirmation needs a durable, DB-backed checkpoint keyed by conversation_id
        before the graph starts streaming — LangGraph may checkpoint on the first step, well
        before upsert_chat_history() runs (it only runs once the assistant response is fully
        generated). Without this, turn 1 of a manual-confirmation conversation has no row for
        the checkpointer to attach to, and the pending-tool-call save silently fails to persist.
        """
        conversation, should_create, _ = cls._find_or_create_conversation(request, assistant, user)
        if should_create:
            conversation.save()

    @staticmethod
    def _resolve_history_index(request: AssistantChatRequest, conversation: Conversation) -> int:
        if request.history_index is not None:
            return request.history_index

        max_index = -1
        for message in conversation.history:
            if message.history_index is not None and message.history_index > max_index:
                max_index = message.history_index
        return max_index + 1

    @staticmethod
    def _schedule_naming_background_task(
        background_tasks: BackgroundTasks | None,
        request: AssistantChatRequest,
        assistant_response: str,
        request_id: str | None,
    ) -> None:
        if background_tasks is None:
            return

        # Starlette runs scheduled background tasks in the outer request task,
        # not the copy_context() snapshot active here (see save_chat_history's
        # set_llm_context re-set for why this context is special: it holds the
        # correct litellm_context/dial_credentials/current_user_email). Snapshot
        # it now and run the naming task inside it, or get_llm_by_credentials
        # sees an empty/stale context and every rename silently no-ops.
        naming_context = contextvars.copy_context()
        background_tasks.add_task(
            naming_context.run,
            ChatNamingService.rename_conversation,
            conversation_id=request.conversation_id,
            first_message=request.text,
            assistant_response=assistant_response,
            request_id=request_id,
        )

    @classmethod
    def _emit_routing_metrics(
        cls,
        *,
        user: User,
        assistant: Assistant,
        conversation,
        tokens_usage: TokensUsage,
        llm_runs: Optional[List["LLMRun"]],
        request_id: Optional[str],
    ) -> None:
        """Emit one routing metric per LLM run, or a single collapsed one as a fallback.

        Per-run emission: a single /model generation may involve multiple LLM runs
        (tool-calling loop, fallback/retry, classifier + routed call), each carrying
        its own routing decision. Emitting one event per run (instead of collapsing
        them into RequestSummary.calculate()'s merged RoutingInfo) preserves visibility
        into every routing decision made during the turn, not just the last one.
        """
        if llm_runs:
            for run in llm_runs:
                if run.routing is None or run.routing.is_empty():
                    continue
                RoutingMonitoringService.send_routing_metric(
                    user=user,
                    routing=run.routing,
                    conversation_id=conversation.conversation_id,
                    assistant_id=str(assistant.id),
                    project=str(assistant.project or ""),
                    request_id=request_id,
                    llm_run_id=run.run_id,
                )
            return

        if tokens_usage.routing and not tokens_usage.routing.is_empty():
            RoutingMonitoringService.send_routing_metric(
                user=user,
                routing=tokens_usage.routing,
                conversation_id=conversation.conversation_id,
                assistant_id=str(assistant.id),
                project=str(assistant.project or ""),
                request_id=request_id,
            )

    @staticmethod
    def _resolve_upsert_params(
        params: Optional[UpsertChatHistoryParams],
        kwargs: dict,
    ) -> UpsertChatHistoryParams:
        if params is None:
            return UpsertChatHistoryParams(**kwargs)
        if kwargs:
            raise TypeError("Cannot specify both params and individual keyword arguments to upsert_chat_history")
        return params

    @staticmethod
    def _resolve_turn_status(
        conversation: Conversation,
        status: ConversationStatus,
        in_progress: bool,
    ) -> ConversationStatus:
        if in_progress or not conversation.history:
            return status
        last_msg = conversation.history[-1]
        if getattr(last_msg, "status", None) == ConversationStatus.INTERRUPTED.value:
            return ConversationStatus.INTERRUPTED
        return status

    @classmethod
    def _handle_turn_completion(
        cls,
        *,
        params: UpsertChatHistoryParams,
        conversation: Conversation,
        llm_model: str,
        status: ConversationStatus,
        schedule_naming: bool,
    ) -> None:
        ConversationMonitoringService.send_conversation_metric(
            params.user,
            params.assistant,
            params.tokens_usage,
            params.time_elapsed,
            conversation.conversation_id,
            llm_model,
            status,
            request_id=params.request_id,
            client_source=params.client_source,
        )
        cls._emit_routing_metrics(
            user=params.user,
            assistant=params.assistant,
            conversation=conversation,
            tokens_usage=params.tokens_usage,
            llm_runs=params.llm_runs,
            request_id=params.request_id,
        )
        cls._upsert_conversation_metrics(
            conversation_id=params.request.conversation_id,
            user=params.user,
            assistant_id=params.assistant.id,
            conversation=conversation,
            project=params.assistant.project,
        )
        if schedule_naming:
            cls._schedule_naming_background_task(
                params.background_tasks,
                params.request,
                params.assistant_response,
                params.request_id,
            )

    @classmethod
    def upsert_chat_history(
        cls,
        params: Optional[UpsertChatHistoryParams] = None,
        **kwargs,
    ) -> None:
        """Upsert chat history for a completed or in-progress turn."""
        params = cls._resolve_upsert_params(params, kwargs)

        request = params.request
        assistant = params.assistant
        user = params.user
        in_progress = params.in_progress

        llm_model = request.llm_model or assistant.llm_model_type

        conversation, should_create_conversation, schedule_naming = cls._find_or_create_conversation(
            request, assistant, user
        )
        if not should_create_conversation:
            _guard_finished(conversation)

        request.history_index = cls._resolve_history_index(request, conversation)
        history_index = request.history_index

        replace_latest_variant = request.has_persisted_history_variant()
        status = cls._resolve_turn_status(conversation, params.status, in_progress)

        conversation.update_chat_history(
            ChatTurnData(
                user_query=request.text,
                user_query_raw=request.content_raw or html.escape(request.text or ""),
                assistant_id=assistant.id,
                assistant_response=params.assistant_response,
                thoughts=params.thoughts,
                history_index=history_index,
                file_names=request.file_names,
                time_elapsed=params.time_elapsed,
                input_tokens=params.tokens_usage.input_tokens,
                output_tokens=params.tokens_usage.output_tokens,
                money_spent=params.tokens_usage.money_spent,
                user_message_received_at=params.user_message_received_at,
                a2ui_envelopes=params.a2ui_envelopes,
                # getattr: the request-side A2UI intake fields arrive with the
                # separate intake task; persistence stays additive until then.
                a2ui_action=getattr(request, "a2ui_action", None),
                a2ui_data_model=getattr(request, "a2ui_data_model", None),
                in_progress=in_progress,
                status=status,
            ),
            project=assistant.project,
            replace_latest_variant=replace_latest_variant,
        )
        conversation.update_conversation_assistants(assistant.id)

        if should_create_conversation:
            conversation.save()
        else:
            conversation.update()

        AgentWorkspaceService().sync_uploaded_files(
            conversation_id=request.conversation_id,
            file_urls=request.file_names or [],
            user=user,
        )

        if not in_progress:
            cls._handle_turn_completion(
                params=params,
                conversation=conversation,
                llm_model=llm_model,
                status=status,
                schedule_naming=schedule_naming,
            )

        request.mark_history_variant_persisted()

    @classmethod
    def resolve_chat_import_source(cls, client_type: Optional[str], cli_header: Optional[str]) -> Optional[str]:
        if client_type:
            return _CLIENT_TYPE_TO_IMPORT_SOURCE.get(client_type) or ("claude_code" if cli_header else None)
        return "claude_code" if cli_header else None

    @classmethod
    def upsert_conversation_with_history(
        cls, conversation_id: str, request: UpsertHistoryRequest, user: User, import_source: Optional[str] = None
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
            _guard_finished(conversation)
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
                conversation_id=conversation_id, user=user, request=request, import_source=import_source
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

        # Write-once import_source: set only when the conversation has no existing value
        if import_source and conversation.import_source is None:
            conversation.import_source = import_source
            conversation.update()

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
        cls, conversation_id: str, user: User, request: UpsertHistoryRequest, import_source: Optional[str] = None
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
        first_msg = (request.history[0].message or "") if request.history else ""
        conversation_name = cls._truncate_name(first_msg)
        return Conversation(
            id=conversation_id,
            conversation_id=conversation_id,
            user_id=user.id,
            user_name=getattr(user, 'name', None) or user.id,  # Fallback to user_id if name is None
            history=request.history,
            assistant_ids=[request.assistant_id],
            initial_assistant_id=request.assistant_id,
            folder=request.folder,
            import_source=import_source,
            conversation_name=conversation_name,
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
    def _finish_loaded_conversation(cls, conversation: Conversation) -> ConversationFinishResponse:
        _guard_finished(conversation)
        now = datetime.now()
        with get_session() as session:
            result = session.execute(
                sa_update(Conversation)
                .where(Conversation.id == conversation.id, Conversation.finished_at.is_(None))
                .values(finished_at=now)
            )
            if result.rowcount == 0:
                if session.get(Conversation, conversation.id) is None:
                    _raise_conversation_not_found(conversation.id)
                _raise_conversation_finished(conversation.id)
            session.commit()
        conversation.finished_at = now
        return ConversationFinishResponse(
            conversation_id=conversation.id,
            finished_at=now,
        )

    @classmethod
    def finish_conversation(cls, conversation_id: str) -> ConversationFinishResponse:
        conversation = Conversation.find_by_id(conversation_id)
        if not conversation:
            _raise_conversation_not_found(conversation_id)
        return cls._finish_loaded_conversation(conversation)

    @classmethod
    def finish_conversations_bulk(cls, conversation_ids: list[str]) -> list[ConversationFinishResult]:
        with get_session() as session:
            rows = session.exec(
                select(Conversation.id, Conversation.finished_at).where(Conversation.id.in_(conversation_ids))
            ).all()

        found: dict[str, datetime | None] = {row.id: row.finished_at for row in rows}
        to_finish = list(dict.fromkeys(cid for cid in conversation_ids if cid in found and found[cid] is None))

        now = datetime.now()
        finished_ids: set[str] = set()
        if to_finish:
            with get_session() as session:
                result = session.execute(
                    sa_update(Conversation)
                    .where(Conversation.id.in_(to_finish), Conversation.finished_at.is_(None))
                    .values(finished_at=now)
                    .returning(Conversation.id)
                )
                finished_ids = set(result.scalars().all())
                session.commit()

        unmatched = [cid for cid in to_finish if cid not in finished_ids]
        raced: dict[str, datetime | None] = {}
        if unmatched:
            with get_session() as session:
                raced_rows = session.exec(
                    select(Conversation.id, Conversation.finished_at).where(Conversation.id.in_(unmatched))
                ).all()
                raced = {row.id: row.finished_at for row in raced_rows}

        results = []
        for cid in conversation_ids:
            if cid not in found:
                results.append(ConversationFinishResult(conversation_id=cid, already_finished=False, error="not_found"))
            elif found[cid] is not None:
                results.append(
                    ConversationFinishResult(
                        conversation_id=cid,
                        already_finished=True,
                        finished_at=found[cid],
                    )
                )
            elif cid in finished_ids:
                results.append(ConversationFinishResult(conversation_id=cid, already_finished=False, finished_at=now))
            elif cid not in raced:
                results.append(ConversationFinishResult(conversation_id=cid, already_finished=False, error="not_found"))
            else:
                results.append(
                    ConversationFinishResult(
                        conversation_id=cid,
                        already_finished=True,
                        finished_at=raced[cid],
                    )
                )
        return results

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
        initial_image_settings = {"enable_image_generation": None, "image_generation_model": None}

        if initial_assistant_id and not is_workflow_conversation:
            from codemie.rest_api.models.assistant import Assistant

            assistants = Assistant.get_by_ids(ids=[initial_assistant_id], user=user)
            assistant = assistants[0] if assistants else None
            initial_image_settings = cls._get_initial_image_generation_settings(assistant)

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
            enable_image_generation=initial_image_settings["enable_image_generation"],
            image_generation_model=initial_image_settings["image_generation_model"],
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
    def get_assistant_folders(cls, user: User) -> list[AssistantFolderListItem]:
        with get_session() as session:
            assistant_ids = list(
                session.exec(
                    select(Conversation.initial_assistant_id)
                    .where(
                        Conversation.user_id == user.id,
                        Conversation.initial_assistant_id.is_not(None),
                        Conversation.import_source.is_(None),
                        or_(
                            Conversation.is_workflow_conversation.is_(None),
                            Conversation.is_workflow_conversation.is_(False),
                        ),
                    )
                    .distinct()
                ).all()
            )

        if not assistant_ids:
            return []

        from codemie.rest_api.models.assistant import Assistant

        assistants = Assistant.get_by_ids(user=user, ids=assistant_ids)
        assistants_by_id = {assistant.id: assistant for assistant in assistants}
        return [
            AssistantFolderListItem(
                assistant_id=asst_id,
                name=assistants_by_id[asst_id].name,
                icon_url=assistants_by_id[asst_id].icon_url,
            )
            for asst_id in assistant_ids
            if asst_id in assistants_by_id
        ]

    @classmethod
    def delete_assistant_folder(
        cls,
        user: User,
        assistant_id: str,
        remove_conversations: bool,
    ) -> AssistantFolderDeleteResponse:
        if not remove_conversations:
            return AssistantFolderDeleteResponse(deleted_conversation_ids=[], folder_deleted=False)

        with get_session() as session:
            conversations_to_delete = list(
                session.exec(
                    select(Conversation.id, Conversation.conversation_id).where(
                        Conversation.user_id == user.id,
                        Conversation.initial_assistant_id == assistant_id,
                        or_(Conversation.folder.is_(None), Conversation.folder == ""),
                        Conversation.import_source.is_(None),
                        Conversation.pinned.is_not(True),
                        or_(
                            Conversation.is_workflow_conversation.is_(None),
                            Conversation.is_workflow_conversation.is_(False),
                        ),
                    )
                ).all()
            )
            conversation_ids = [row[0] for row in conversations_to_delete]
            conversation_business_ids = [row[1] for row in conversations_to_delete]

            if conversation_ids:
                from codemie.core.workflow_models.workflow_execution import WorkflowExecution

                WorkflowExecution.delete_by_conversation_ids(session, conversation_ids)
                session.exec(
                    delete(SharedConversation).where(SharedConversation.conversation_id.in_(conversation_business_ids))
                )
                session.exec(
                    delete(ConversationMetrics).where(
                        ConversationMetrics.conversation_id.in_(conversation_business_ids)
                    )
                )
                # Scoped by the PK, not conversation_id (which carries no unique constraint),
                # so this can only ever remove exactly the rows the SELECT above identified.
                session.exec(delete(Conversation).where(Conversation.id.in_(conversation_ids)))

            session.commit()

        return AssistantFolderDeleteResponse(
            deleted_conversation_ids=conversation_business_ids,
            folder_deleted=bool(conversation_ids),
        )

    @classmethod
    def build_new_conversation(
        cls,
        user: User,
        initial_assistant_id: str | None = None,
        is_workflow: bool = False,
        folder: str | None = None,
    ) -> "Conversation":
        """Build a new non-persisted conversation."""

        from codemie.rest_api.models.conversation import Conversation, AssistantDetails
        from codemie.rest_api.models.assistant import Assistant
        from codemie_tools.base.models import Tool
        from codemie.core.workflow_models import WorkflowConfig

        assistant_data: list[AssistantDetails] = []
        assistant_ids: list[str] = []
        initial_image_settings = {"enable_image_generation": None, "image_generation_model": None}

        if initial_assistant_id:
            assistant_ids = [initial_assistant_id]
            if is_workflow:
                try:
                    workflow = WorkflowConfig.get_by_id(initial_assistant_id)
                    assistant_data = [
                        AssistantDetails(
                            assistant_id=workflow.id,
                            assistant_name=workflow.name,
                            assistant_icon=workflow.icon_url,
                            assistant_type=None,
                            context=None,
                            tools=None,
                            conversation_starters=[],
                        )
                    ]
                except KeyError:
                    raise ExtendedHTTPException(
                        code=404,
                        message=f"Workflow {initial_assistant_id} not found.",
                    )
            else:
                assistants = Assistant.get_by_ids(ids=[initial_assistant_id], user=user)
                if not assistants:
                    raise ExtendedHTTPException(
                        code=404,
                        message=f"Assistant {initial_assistant_id} not found.",
                    )
                assistant_data = [
                    AssistantDetails(
                        assistant_id=a.id,
                        assistant_type=a.type,
                        assistant_name=a.name,
                        assistant_icon=a.icon_url,
                        context=a.context,
                        conversation_starters=a.conversation_starters,
                        tools=[
                            Tool(name=tool.name, label=tool.label) for toolkit in a.toolkits for tool in toolkit.tools
                        ],
                    )
                    for a in assistants
                ]
                assistant = assistants[0]
                initial_image_settings = cls._get_initial_image_generation_settings(assistant)

        return Conversation(
            id="new",
            conversation_id="new",
            conversation_name="",
            folder=folder,
            pinned=False,
            history=[],
            user_id=user.id,
            user_name=getattr(user, "name", None) or "",
            assistant_ids=assistant_ids,
            assistant_data=assistant_data,
            initial_assistant_id=initial_assistant_id,
            project=getattr(user, "current_project", None),
            enable_image_generation=initial_image_settings["enable_image_generation"],
            image_generation_model=initial_image_settings["image_generation_model"],
            mcp_server_single_usage=False,
            is_workflow_conversation=bool(is_workflow),
            is_folder_migrated=False,
        )

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
        # Rename in place rather than delete+recreate: a rename must not bump the folder's own
        # update_date (EPMCDME-15009 reopened AC) and must not lose the folder's id/date to a
        # transient delete if a later step in this method were to fail.
        existing_folder = ConversationFolder.get_by_folder(folder, user.id)
        if existing_folder:
            existing_folder.folder_name = new_folder
            existing_folder.update(touch_timestamp=False)
        else:
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
            # A folder rename is not usage for its member conversations either.
            conversation.update(refresh=True, touch_timestamp=False)

    @classmethod
    def move_conversations_to_folder(cls, user: User, conversation_ids: List[str], target_folder: str) -> int:
        """Atomically move user-owned, non-import conversations to an existing Custom Folder."""
        unique_ids = list(dict.fromkeys(conversation_id.strip() for conversation_id in conversation_ids))
        if not unique_ids or any(not conversation_id for conversation_id in unique_ids):
            raise ValueError("At least one valid conversation ID is required")
        if not target_folder.strip():
            raise ValueError("Target folder is required")

        with get_session() as session:
            folder_stmt = text("""
                SELECT id
                FROM conversation_folders
                WHERE user_id = :uid AND folder_name = :folder
                FOR UPDATE
            """).bindparams(uid=user.id, folder=target_folder)
            if session.exec(folder_stmt).first() is None:
                raise ValueError("Target folder does not exist")

            conversations_stmt = (
                text("""
                    SELECT conversation_id, import_source
                    FROM conversations
                    WHERE user_id = :uid AND conversation_id IN :conversation_ids
                    FOR UPDATE
                """)
                .bindparams(bindparam("conversation_ids", expanding=True))
                .bindparams(uid=user.id, conversation_ids=unique_ids)
            )
            conversations = list(session.exec(conversations_stmt).all())
            if len(conversations) != len(unique_ids):
                raise ValueError("One or more conversations were not found")
            if any(conversation.import_source is not None for conversation in conversations):
                raise ValueError("Imported conversations cannot be moved to Custom Folders")

            # A move is not usage and not folder activity (EPMCDME-15009 reopened AC): update
            # only `folder`, never `update_date` on the moved conversations or the target folder.
            update_stmt = (
                text("""
                    UPDATE conversations
                    SET folder = :folder
                    WHERE user_id = :uid AND conversation_id IN :conversation_ids
                """)
                .bindparams(bindparam("conversation_ids", expanding=True))
                .bindparams(uid=user.id, folder=target_folder, conversation_ids=unique_ids)
            )
            session.exec(update_stmt)
            session.commit()

        return len(unique_ids)

    @classmethod
    def update_conversation(cls, conversation: Conversation, request: UpdateConversationRequest):
        fields_set = request.model_fields_set

        if request.name:
            conversation.conversation_name = request.name
        if 'llm_model' in fields_set:
            conversation.llm_model = request.llm_model
        if 'enable_image_generation' in fields_set:
            conversation.enable_image_generation = request.enable_image_generation
        if 'image_generation_model' in fields_set:
            conversation.image_generation_model = request.image_generation_model
        if request.pinned is not None:
            conversation.pinned = request.pinned
        if request.folder is not None:
            conversation.folder = request.folder
        if 'tool_call_policy' in fields_set:
            conversation.tool_call_policy = request.tool_call_policy
        if request.active_assistant_id and request.active_assistant_id in conversation.assistant_ids:
            # Make active_assistant_id to be the first in assistant_ids array
            assistant_ids = list(conversation.assistant_ids)
            assistant_ids.insert(0, assistant_ids.pop(assistant_ids.index(request.active_assistant_id)))
            conversation.assistant_ids = assistant_ids

        # None of the fields this method sets are "usage" (rename, pin/unpin, folder move,
        # model/tool-call-policy config) — real usage is recorded separately when a message is
        # sent/received. Do not bump update_date here, and do not touch the target folder's own
        # update_date on move: neither should reorder the sidebar's recency-based sort.
        conversation.update(touch_timestamp=False)

        return conversation

    @classmethod
    def remove_conversation_history_index(cls, conversation: Conversation, history_index: int):
        _guard_finished(conversation)
        conversation.history = [
            history_message
            for history_message in conversation.history
            if history_message.history_index is not history_index
        ]
        for history_message in conversation.history:
            current_index = history_message.history_index
            history_message.history_index = current_index if current_index < history_index else current_index - 1

        conversation.update_conversation_assistants()
        conversation.update()
        return conversation

    @classmethod
    def clear_conversation_history(cls, conversation: Conversation):
        from codemie.core.workflow_models.workflow_execution import WorkflowExecution  # noqa: PLC0415 — deferred to break circular import

        _guard_finished(conversation)
        conversation.history = []
        conversation.update_conversation_assistants()
        # Persist the emptied history before deleting workflow executions.
        # Two-transaction split is deliberate: update() opens its own session, and
        # WorkflowExecution.delete_by_conversation_ids needs another. A crash
        # between the two leaves executions behind an empty conversation —
        # recoverable (executions can be reaped; history is already gone).
        conversation.update()
        with Session(WorkflowExecution.get_engine()) as session:
            WorkflowExecution.delete_by_conversation_ids(session, [conversation.id])
            session.commit()
        return conversation

    @classmethod
    def update_conversation_ai_message(
        cls, conversation: Conversation, history_index: int, request: UpdateAiMessageRequest
    ):
        _guard_finished(conversation)
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

        conversation.update()
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
        is_finished: Optional[bool] = None,
    ) -> List[ConversationListItem]:
        """DB-level paginated list of user conversations.

        Selects only the scalar columns required for ConversationListItem — the history
        column is never loaded, avoiding expensive TOAST reads for large conversations.
        Timestamp bounds are computed only when CONVERSATION_HISTORY_STATS_ENABLED is True.
        """
        offset = page * per_page

        if config.CONVERSATION_HISTORY_STATS_ENABLED:
            timestamp_sql = """
                (SELECT MIN((elem->>'date')::timestamptz)
                 FROM jsonb_array_elements(COALESCE(history, '[]'::jsonb)) AS elem
                 WHERE NULLIF(TRIM(elem->>'date'), '') IS NOT NULL) AS very_first_msg_at,
                (SELECT MAX((elem->>'date')::timestamptz)
                 FROM jsonb_array_elements(COALESCE(history, '[]'::jsonb)) AS elem
                 WHERE NULLIF(TRIM(elem->>'date'), '') IS NOT NULL) AS very_last_msg_at
            """
        else:
            timestamp_sql = "NULL AS very_first_msg_at, NULL AS very_last_msg_at"

        if is_finished is None:
            is_finished_clause = ""
        elif is_finished:
            is_finished_clause = " AND c.finished_at IS NOT NULL"
        else:
            is_finished_clause = " AND c.finished_at IS NULL"

        with get_session() as session:
            stmt = text(f"""
                SELECT
                    c.conversation_id,
                    c.conversation_name,
                    c.folder,
                    c.assistant_ids,
                    c.initial_assistant_id,
                    c.pinned,
                    c.date,
                    c.update_date,
                    c.is_workflow_conversation,
                    c.finished_at,
                    a.icon_url AS assistant_icon,
                    ARRAY(
                        SELECT linked_assistant.name
                        FROM jsonb_array_elements_text(COALESCE(c.assistant_ids, '[]'::jsonb))
                            AS linked_assistant_id(id)
                        JOIN assistants linked_assistant ON linked_assistant.id = linked_assistant_id.id
                        ORDER BY linked_assistant.name
                    ) AS assistant_names,
                    c.import_source,
                    {timestamp_sql}
                FROM conversations c
                LEFT JOIN assistants a ON a.id = c.initial_assistant_id
                WHERE c.user_id = :uid{is_finished_clause}
                ORDER BY COALESCE(c.update_date, c.date) DESC NULLS LAST, c.conversation_id DESC
                OFFSET :off LIMIT :lim
            """).bindparams(uid=user_id, off=offset, lim=per_page)
            rows = list(session.exec(stmt).all())

        result = []
        for row in rows:
            is_workflow = row.is_workflow_conversation or False
            result.append(
                ConversationListItem(
                    id=row.conversation_id,
                    name=row.conversation_name or None,
                    folder=row.folder,
                    assistant_ids=row.assistant_ids,
                    initial_assistant_id=row.initial_assistant_id,
                    pinned=row.pinned,
                    date=row.update_date or row.date,
                    update_date=row.update_date,
                    is_workflow=is_workflow,
                    workflow_id=row.initial_assistant_id if is_workflow else None,
                    conversation_id=row.conversation_id if is_workflow else None,
                    very_first_msg_at=row.very_first_msg_at,
                    very_last_msg_at=row.very_last_msg_at,
                    assistant_icon=row.assistant_icon,
                    assistant_names=row.assistant_names,
                    import_source=row.import_source,
                    finished_at=row.finished_at,
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
                    user_abilities, date, update_date, finished_at,
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
        materialized = materialize_workflow_conversation(messages, initial_assistant_id)

        # Exclude synthetic aggregation columns before model instantiation.
        conv_kwargs = {
            k: v
            for k, v in meta_row._mapping.items()
            if k not in ('total_count', 'very_first_msg_at', 'very_last_msg_at')
        }

        conversation = Conversation(**conv_kwargs, history=materialized.history)

        return (
            conversation,
            meta_row.total_count,
            meta_row.very_first_msg_at,
            meta_row.very_last_msg_at,
        )

    @staticmethod
    def search_conversations(user: User, query: str, limit: int = 20) -> ConversationSearchResponse:
        """
        Search visible assistants plus conversations and folders by name for a specific user.

        Args:
            user: Authenticated user used for ownership and assistant visibility filters
            query: Search string (case-insensitive partial match)
            limit: Max results to return per entity type

        Returns:
            ConversationSearchResponse with typed assistant, chat, and folder results
        """
        # Search chats
        chat_results = Conversation.search_by_name_and_user(
            user_id=user.id,
            query=query,
            limit=limit,
        )

        # Search folders
        folder_results = ConversationFolder.search_by_name_and_user(
            user_id=user.id,
            query=query,
            limit=limit,
        )

        assistant_results = AssistantRepository().query(
            user=user,
            scope=AssistantScope.VISIBLE_TO_USER,
            filters={'search': query},
            page=0,
            per_page=limit,
            minimal_response=True,
        )['data']

        combined = []

        for assistant in assistant_results:
            combined.append(
                SearchResultItem(
                    id=assistant.id,
                    name=assistant.name,
                    type='assistant',
                    icon_url=assistant.icon_url,
                )
            )

        for chat in chat_results:
            combined.append(
                SearchResultItem(
                    id=chat.id,
                    name=chat.name or '',
                    updated_at=chat.date,
                    type='chat',
                    folder=chat.folder or None,
                    finished_at=chat.finished_at,
                )
            )

        for folder in folder_results:
            combined.append(
                SearchResultItem(
                    id=folder.id,
                    name=folder.folder_name,
                    updated_at=folder.update_date or folder.date,
                    type='folder',
                )
            )

        return ConversationSearchResponse(items=combined)
