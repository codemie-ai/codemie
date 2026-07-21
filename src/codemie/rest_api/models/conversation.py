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

from datetime import datetime
from typing import List, Literal, Optional

from codemie_tools.base.models import Tool
from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from codemie.chains.base import Thought
from codemie.clients.postgres import get_session
from codemie.configs import config, logger
from codemie.core.ability import Owned, Action
from codemie.core.db_utils import escape_like_wildcards
from codemie.core.exceptions import ValidationException
from codemie.core.models import CodeIndexType, ChatMessage, ChatRole
from codemie.rest_api.models.assistant import Context, AssistantType
from codemie.rest_api.models.base import (
    BaseModelWithSQLSupport,
    PaginationData,
    PydanticListType,
    PydanticType,
)
from codemie.rest_api.models.feedback import MarkEnum
from codemie.rest_api.security.user import User
from sqlmodel import Field as SQLField, Session, delete, select, Column, text
from sqlalchemy import Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableList
from enum import StrEnum


class Operator(BaseModel):
    user_id: str
    name: str


class LegacyChatDetails(BaseModel):
    llm_model: Optional[str] = None
    context: Optional[List[Context]] = None

    # Legacy for backward compatibility
    app_name: Optional[str] = None
    repo_name: Optional[str] = None
    index_type: Optional[CodeIndexType] = None


class AssistantDetails(BaseModel):
    assistant_id: Optional[str] = None
    assistant_name: Optional[str] = None
    assistant_icon: Optional[str] = None
    assistant_type: Optional[AssistantType] = None
    context: Optional[List[Context | str]] = None
    tools: Optional[List[Tool]] = None
    conversation_starters: List[str] = Field(default_factory=list)


class UserMark(BaseModel):
    mark: MarkEnum
    rating: Optional[int] = Field(ge=0, le=100, default=None)
    comments: Optional[str] = None
    date: Optional[datetime] = None
    type: Optional[str] = None
    feedback_id: Optional[str] = None


class FinalOperatorFeedback(UserMark):
    mark: Optional[MarkEnum] = None
    rating: Optional[int] = Field(ge=0, le=100, default=None)
    comments: Optional[str] = None
    date: Optional[datetime] = None
    operator: Optional[Operator] = None


class GeneratedMessage(ChatMessage):
    history_index: Optional[int] = None
    date: Optional[datetime] = None
    response_time: Optional[float] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_creation_input_tokens: Optional[int] = None  # Cache write tokens (Claude prompt caching)
    cache_read_input_tokens: Optional[int] = None  # Cache read tokens (Claude prompt caching)
    money_spent: Optional[float] = None
    user_mark: Optional[UserMark] = None
    operator_mark: Optional[UserMark] = None
    ## User message fields
    message_raw: Optional[str] = None
    file_names: Optional[List[str]] = Field(default_factory=list)
    ## Assistant message fields
    assistant_id: Optional[str] = None
    thoughts: Optional[List[Thought]] = None
    ## Workflow execution reference fields
    workflow_execution_ref: Optional[bool] = None  # Marker that this is a reference to workflow execution
    execution_id: Optional[str] = None  # Reference to WorkflowExecution.execution_id

    @classmethod
    @model_validator(mode="before")
    def before_init(cls, values):
        """Handle backward compatibility for file_name/file_names fields."""
        if "file_name" in values:
            if "file_names" in values:
                raise ValueError("Cannot provide both file_name and file_names. Use only file_names.")

            file_name = values.pop("file_name")
            if file_name and isinstance(file_name, str) and file_name.strip():
                values["file_names"] = [file_name]
        return values

    def model_dump(self, **kwargs):
        """Custom model_dump method to include file_name if file_names has a single item."""
        data = super().model_dump(**kwargs)
        # If file_names has exactly one item, add it as file_name as well
        if data.get('file_names') and len(data['file_names']) == 1:
            data['file_name'] = data['file_names'][0]
        return data


class UpsertHistoryRequest(BaseModel):
    """
    Request model for upserting conversation history.
    Used by clients to bulk import or incrementally sync conversation data.
    """

    assistant_id: str = Field(description="Assistant ID (can be placeholder for imports)")
    folder: Optional[str] = Field(default=None, description="Folder for organizing conversations")
    history: List[GeneratedMessage] = Field(description="List of conversation messages to upsert")


class UpsertHistoryResponse(BaseModel):
    """
    Response model for upsert conversation history endpoint.
    Provides metadata about the upsert operation results.
    """

    conversation_id: str = Field(description="The conversation ID")
    new_messages: int = Field(description="Number of messages added in this request")
    total_messages: int = Field(description="Total number of messages in the conversation after upsert")
    created: bool = Field(description="Whether the conversation was newly created (true) or updated (false)")


class ConversationMetrics(BaseModelWithSQLSupport, table=True):
    __tablename__ = "conversation_metrics"

    conversation_id: str = SQLField(index=True)
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    project: Optional[str] = None
    final_user_rating: Optional[int] = None
    final_operator_rating: Optional[int] = None
    avg_user_rating: Optional[float] = None
    avg_operator_rating: Optional[float] = None
    avg_response_time: Optional[float] = None
    number_of_messages: Optional[int] = None
    total_input_tokens: Optional[int] = None
    total_output_tokens: Optional[int] = None
    total_money_spent: Optional[float] = None

    @classmethod
    def get_by_conversation_id(cls, conversation_id: str) -> ConversationMetrics:
        res = cls.get_by_fields({"conversation_id": conversation_id})
        if res:
            return res
        else:
            raise KeyError(f"Metrics for conversation {conversation_id} does not exist")

    def calculate_metrics(self, conversation: "Conversation"):
        self.conversation_id = conversation.conversation_id
        self.project = conversation.project
        self.user_id = conversation.user_id
        self.final_user_rating = conversation.final_user_mark.rating if conversation.final_user_mark else None
        self.final_operator_rating = (
            conversation.final_operator_mark.rating if conversation.final_operator_mark else None
        )
        self.avg_user_rating = conversation.get_average_user_rating()
        self.avg_operator_rating = conversation.get_average_operator_rating()
        self.number_of_messages = len(conversation.history)
        self.total_input_tokens = conversation.get_total_input_tokens()
        self.total_output_tokens = conversation.get_total_output_tokens()
        self.total_money_spent = conversation.get_total_money_spent()
        self.avg_response_time = conversation.get_average_response_time()


class Conversation(BaseModelWithSQLSupport, Owned, table=True):
    __tablename__ = "conversations"

    conversation_id: str = SQLField(index=True)
    conversation_name: Optional[str] = None
    llm_model: Optional[str] = None
    enable_image_generation: Optional[bool] = None
    image_generation_model: Optional[str] = None
    folder: Optional[str] = None
    pinned: Optional[bool] = False
    history: Optional[List[GeneratedMessage]] = SQLField(
        default_factory=list, sa_column=Column(PydanticListType(GeneratedMessage))
    )
    user_id: Optional[str] = SQLField(default=None, index=True)
    user_name: Optional[str] = None
    assistant_ids: Optional[List[str]] = SQLField(default_factory=list, sa_column=Column(MutableList.as_mutable(JSONB)))
    assistant_data: Optional[List[AssistantDetails]] = SQLField(
        default_factory=list, sa_column=Column(PydanticListType(AssistantDetails))
    )
    initial_assistant_id: Optional[str] = None
    final_user_mark: Optional[UserMark] = SQLField(default=None, sa_column=Column(PydanticType(UserMark)))
    final_operator_mark: Optional[FinalOperatorFeedback] = SQLField(
        default=None, sa_column=Column(PydanticType(FinalOperatorFeedback))
    )
    project: Optional[str] = None
    mcp_server_single_usage: Optional[bool] = SQLField(
        default=False,
        sa_column=Column(Boolean),
        description="Whether MCP servers should be single-use (True) or persistent (False)",
    )
    is_workflow_conversation: Optional[bool] = SQLField(
        default=False,
        sa_column=Column(Boolean),
        description="True if this conversation is based on a workflow, False for assistant conversations",
    )

    # Legacy
    conversation_details: Optional[LegacyChatDetails] = SQLField(
        default=None, sa_column=Column(PydanticType(LegacyChatDetails))
    )
    assistant_details: Optional[AssistantDetails] = SQLField(
        default=None, sa_column=Column(PydanticType(AssistantDetails))
    )

    user_abilities: Optional[List[Action]] = SQLField(default=None, sa_column=Column(JSONB))

    # Remove this after the migration is done
    is_folder_migrated: Optional[bool] = False
    category: Optional[str] = None

    def get_average_user_rating(self):
        user_ratings = []
        for message in self.history:
            if isinstance(message, GeneratedMessage) and message.user_mark:
                if message.user_mark.rating:
                    user_ratings.append(message.user_mark.rating)
                elif message.user_mark.mark == MarkEnum.CORRECT:
                    user_ratings.append(100)
                elif message.user_mark.mark == MarkEnum.WRONG:
                    user_ratings.append(0)
                elif message.user_mark.mark == MarkEnum.PARTIALLY_CORRECT:
                    user_ratings.append(50)
        return sum(user_ratings) / len(user_ratings) if user_ratings else 0

    def get_average_operator_rating(self):
        operator_ratings = []
        for message in self.history:
            if isinstance(message, GeneratedMessage) and message.operator_mark:
                operator_ratings.append(message.operator_mark.rating)
        return sum(operator_ratings) / len(operator_ratings) if operator_ratings else 0

    def get_average_response_time(self):
        response_times = []
        for message in self.history:
            if isinstance(message, GeneratedMessage) and message.response_time:
                response_times.append(message.response_time)
        return sum(response_times) / len(response_times) if response_times else 0

    def get_total_input_tokens(self):
        tokens = []
        for message in self.history:
            if isinstance(message, GeneratedMessage) and message.input_tokens:
                tokens.append(message.input_tokens)
        return sum(tokens) if tokens else 0

    def get_total_output_tokens(self):
        tokens = []
        for message in self.history:
            if isinstance(message, GeneratedMessage) and message.output_tokens:
                tokens.append(message.output_tokens)
        return sum(tokens) if tokens else 0

    def get_total_money_spent(self):
        money_spent = []
        for message in self.history:
            if isinstance(message, GeneratedMessage) and message.money_spent:
                money_spent.append(message.money_spent)
        return sum(money_spent) if money_spent else 0

    @staticmethod
    def _build_chat_history_messages(
        user_query: str,
        user_query_raw: str,
        assistant_id: str,
        assistant_response: str,
        thoughts: List[Thought],
        history_index: int,
        time_elapsed: float,
        input_tokens: int,
        output_tokens: int,
        file_names: list[str],
        money_spent: float,
        user_message_received_at: datetime | None = None,
    ) -> tuple[GeneratedMessage, GeneratedMessage]:
        assistant_responded_at = datetime.now()
        user_message = GeneratedMessage(
            date=user_message_received_at or assistant_responded_at,
            role=ChatRole.USER,
            message_raw=user_query_raw,
            file_names=file_names,
            history_index=history_index,
            message=user_query,
        )
        assistant_message = GeneratedMessage(
            date=assistant_responded_at,
            role=ChatRole.ASSISTANT,
            message=assistant_response,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            money_spent=money_spent,
            response_time=time_elapsed,
            history_index=history_index,
            thoughts=thoughts,
            assistant_id=assistant_id,
        )
        return user_message, assistant_message

    @staticmethod
    def _build_retained_history(
        existing_history: list[GeneratedMessage],
        history_index: int,
        replace_latest_variant: bool,
    ) -> list[GeneratedMessage]:
        if not replace_latest_variant:
            return existing_history

        retained_history = []
        removed_user_message = False
        removed_assistant_message = False

        for message in reversed(existing_history):
            if message.history_index == history_index:
                if message.role == ChatRole.USER and not removed_user_message:
                    removed_user_message = True
                    continue
                if message.role == ChatRole.ASSISTANT and not removed_assistant_message:
                    removed_assistant_message = True
                    continue

            retained_history.append(message)

        retained_history.reverse()
        return retained_history

    def update_chat_history(
        self,
        user_query: str,
        user_query_raw: str,
        assistant_id: str,
        project: str,
        assistant_response: str,
        thoughts: List[Thought],
        history_index: int,
        time_elapsed: float,
        input_tokens: int,
        output_tokens: int,
        file_names: list[str],
        money_spent: float,
        replace_latest_variant: bool = False,
        user_message_received_at: datetime | None = None,
    ):
        user_message, assistant_message = self._build_chat_history_messages(
            user_query=user_query,
            user_query_raw=user_query_raw,
            assistant_id=assistant_id,
            assistant_response=assistant_response,
            thoughts=thoughts,
            history_index=history_index,
            time_elapsed=time_elapsed,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            file_names=file_names,
            money_spent=money_spent,
            user_message_received_at=user_message_received_at,
        )

        retained_history = self._build_retained_history(
            existing_history=list(self.history or []),
            history_index=history_index,
            replace_latest_variant=replace_latest_variant,
        )

        self.history = [*retained_history, user_message, assistant_message]
        self.project = project

    def update_conversation_assistants(self, active_assistant_id: str = None):
        # Make active_assistant_id to be the first in the list
        if active_assistant_id:
            self.assistant_ids.insert(0, active_assistant_id)
        history_assistant_ids = [history_message.assistant_id for history_message in self.history]
        # Remove duplicates and ids that are not present in history:
        self.assistant_ids = [
            assistant_id
            for n, assistant_id in enumerate(self.assistant_ids)
            if assistant_id not in self.assistant_ids[:n] and assistant_id in history_assistant_ids
        ]
        if len(self.assistant_ids) == 0 and self.initial_assistant_id:
            self.assistant_ids = [self.initial_assistant_id]
        if len(self.assistant_ids) > 0 and not self.initial_assistant_id:
            self.initial_assistant_id = self.assistant_ids[0]

    def get_conversation_name(self):
        if not len(self.history):
            return self.conversation_name

        message = self.history[0].message
        return self.conversation_name or (message[:50] + '...' if message and len(message) > 50 else message)

    @classmethod
    def get_by_id(cls, id_: str) -> "Conversation":
        """
        Get conversation by ID and automatically materialize workflow execution references.

        This override ensures that any workflow execution references in the conversation
        history are automatically resolved to their full content when the conversation
        is retrieved from the database.

        Args:
            id_: The conversation ID

        Returns:
            Conversation with materialized history

        Raises:
            KeyError: If conversation not found
        """
        from codemie.service.conversation.history_materializer import materialize_workflow_conversation

        conversation = super().get_by_id(id_)
        result = materialize_workflow_conversation(conversation.history, conversation.initial_assistant_id)
        conversation.history = result.history
        return conversation

    @classmethod
    def find_by_id(cls, id_: str) -> Optional["Conversation"]:
        """
        Find conversation by ID and automatically materialize workflow execution references.

        Similar to get_by_id but returns None instead of raising KeyError if not found.

        Args:
            id_: The conversation ID

        Returns:
            Conversation with materialized history, or None if not found
        """
        from codemie.service.conversation.history_materializer import materialize_workflow_conversation

        conversation = super().find_by_id(id_)
        if conversation:
            result = materialize_workflow_conversation(conversation.history, conversation.initial_assistant_id)
            conversation.history = result.history
        return conversation

    @staticmethod
    def _build_filter_sql(filters: dict, allowed_columns: set) -> tuple[str, dict]:
        clauses = ""
        params: dict = {}
        for i, (key, value) in enumerate(filters.items()):
            clean_key = key.replace(".keyword", "")
            if clean_key not in allowed_columns:
                raise ValidationException(f"Unsupported filter: {clean_key}")
            if value is None:
                clauses += f" AND c.{clean_key} IS NULL"
                continue
            param_name = f"filter_{i}"
            clauses += f" AND c.{clean_key} = :{param_name}"
            params[param_name] = value
        return clauses, params

    @classmethod
    def get_user_conversations(cls, user_id: str, filters: dict = None) -> List[ConversationListItem]:
        """
        Get all user conversations (both assistant and workflow conversations).
        Uses is_workflow_conversation flag to distinguish conversation types.

        Fetches only the scalar columns needed for ConversationListItem — the history
        column is never loaded, avoiding expensive TOAST reads for large conversations.
        Timestamp bounds (very_first_msg_at / very_last_msg_at) are computed only when
        CONVERSATION_HISTORY_STATS_ENABLED is True.
        """
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

        allowed_filter_columns = {
            "initial_assistant_id",
            "is_workflow_conversation",
            "folder",
            "pinned",
            "project",
        }

        filter_clauses, extra_params = cls._build_filter_sql(filters or {}, allowed_filter_columns)
        params: dict = {"uid": user_id, **extra_params}

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
                a.icon_url AS assistant_icon,
                ARRAY(
                    SELECT linked_assistant.name
                    FROM jsonb_array_elements_text(COALESCE(c.assistant_ids, '[]'::jsonb))
                        AS linked_assistant_id(id)
                    JOIN assistants linked_assistant ON linked_assistant.id = linked_assistant_id.id
                    ORDER BY linked_assistant.name
                ) AS assistant_names,
                {timestamp_sql}
            FROM conversations c
            LEFT JOIN assistants a ON a.id = c.initial_assistant_id
            WHERE c.user_id = :uid{filter_clauses}
            ORDER BY COALESCE(c.update_date, c.date) DESC NULLS LAST
        """).bindparams(**params)

        with get_session() as session:
            rows = list(session.exec(stmt).all())

        result = []
        for row in rows:
            is_workflow = bool(row.is_workflow_conversation)
            result.append(
                ConversationListItem(
                    id=row.conversation_id,
                    name=row.conversation_name or "",
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
                )
            )
        return result

    def find_messages(self, history_index: int, message_index: int) -> tuple[GeneratedMessage, GeneratedMessage]:
        """Find message pair by history and message index"""
        messages = [
            history_message for history_message in self.history if history_message.history_index == history_index
        ]
        user_message = messages[message_index * 2]
        ai_message = messages[message_index * 2 + 1]
        return user_message, ai_message

    def to_chat_history(self) -> List[ChatMessage]:
        """
        Convert conversation history to ChatMessage list.

        Returns:
            List of ChatMessage objects representing the conversation history.
            For each unique combination of role and history_index, only one message is included.
        """
        if not self.history:
            return []

        unique_messages = {}

        for message in self.history:
            if not isinstance(message, GeneratedMessage):
                continue

            if message.history_index is None:
                continue

            # Use tuple of (role, history_index) as the key
            key = (message.role, message.history_index)
            unique_messages[key] = message

        chat_messages: List[ChatMessage] = []
        for (role, _), message in unique_messages.items():
            match role:
                case ChatRole.USER.value:
                    chat_message = ChatMessage(role=ChatRole.USER, message=message.message or "")
                    chat_messages.append(chat_message)
                case ChatRole.ASSISTANT.value:
                    chat_message = ChatMessage(role=ChatRole.ASSISTANT, message=message.message or "")
                    chat_messages.append(chat_message)
                case _:
                    # Skip messages with unknown roles
                    logger.debug(f"Skipping message with unknown role: {role}")

        logger.debug(f"Converted conversation history to {len(chat_messages)} chat messages")
        return chat_messages

    def is_owned_by(self, user: User):
        return self.user_id == user.id

    def is_managed_by(self, user: User):
        return False

    def is_shared_with(self, user: User):
        return False

    @classmethod
    def search_by_name_and_user(cls, user_id: str, query: str, limit: int = 20) -> List['ConversationListItem']:
        """
        Search conversations by partial name match for a specific user.

        Special handling: When query matches "new chat" (the display name for empty conversations),
        also includes conversations with empty/null names.

        Args:
            user_id: User ID to filter by
            query: Search string (case-insensitive partial match)
            limit: Max results to return

        Returns:
            List of matching ConversationListItem objects, sorted by update_date DESC
        """
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
                is_workflow_conversation
            FROM conversations
            WHERE user_id = :uid
              AND (
                LOWER(conversation_name) LIKE :pattern
                OR (
                  LOWER('new chat') LIKE :pattern
                  AND (conversation_name IS NULL OR conversation_name = '')
                )
              )
            ORDER BY COALESCE(update_date, date) DESC NULLS LAST
            LIMIT :limit
        """).bindparams(uid=user_id, pattern=f'%{escape_like_wildcards(query.lower())}%', limit=limit)

        with get_session() as session:
            rows = list(session.exec(stmt).all())

        result = []
        for row in rows:
            is_workflow = bool(row.is_workflow_conversation)
            result.append(
                ConversationListItem(
                    id=row.conversation_id,
                    name=row.conversation_name or '',
                    folder=row.folder,
                    assistant_ids=row.assistant_ids,
                    initial_assistant_id=row.initial_assistant_id,
                    pinned=row.pinned,
                    date=row.update_date or row.date,
                    is_workflow=is_workflow,
                    workflow_id=row.initial_assistant_id if is_workflow else None,
                    conversation_id=row.conversation_id if is_workflow else None,
                )
            )
        return result

    @classmethod
    def delete_by_id(cls, conversation_id: str):
        with Session(cls.get_engine()) as session:
            from codemie.core.workflow_models.workflow_execution import WorkflowExecution  # noqa: PLC0415 — deferred to break circular import (workflow_execution imports GeneratedMessage from this module)

            WorkflowExecution.delete_by_conversation_ids(session, [conversation_id])
            statement = delete(cls).where(cls.id == conversation_id)
            result = session.exec(statement)
            session.commit()
        if result.rowcount > 0:
            return {"status": "deleted"}
        else:
            return {"status": "not found"}

    @classmethod
    def delete_by_user(cls, user_id: str):
        with Session(cls.get_engine()) as session:
            # Collect conversation IDs for this user before deleting them so
            # the workflow-execution cascade can be scoped correctly.
            conv_ids = [row.id for row in session.exec(select(cls).where(cls.user_id == user_id)).all()]
            if conv_ids:
                from codemie.core.workflow_models.workflow_execution import WorkflowExecution  # noqa: PLC0415 — deferred to break circular import (workflow_execution imports GeneratedMessage from this module)

                WorkflowExecution.delete_by_conversation_ids(session, conv_ids)
            statement = delete(cls).where(cls.user_id == user_id)
            result = session.exec(statement)
            session.commit()
        return result


class ConversationListItem(BaseModel):
    id: str
    name: Optional[str] = None
    folder: Optional[str] = None
    pinned: Optional[bool] = False
    date: datetime
    update_date: Optional[datetime] = None

    assistant_ids: Optional[List[str]] = Field(default_factory=list)
    initial_assistant_id: Optional[str] = None

    very_first_msg_at: Optional[datetime] = None
    very_last_msg_at: Optional[datetime] = None

    # Workflow-specific fields
    is_workflow: Optional[bool] = False
    workflow_id: Optional[str] = None
    conversation_id: Optional[str] = None

    # Assistant display fields
    assistant_icon: Optional[str] = None
    assistant_names: Optional[List[str]] = Field(default_factory=list)


class ConversationResponse(BaseModel):
    """
    Response DTO for GET /v1/conversations/{conversation_id}.

    Standalone Pydantic model for API responses. Does not inherit from ORM.
    Used to serialize Conversation (ORM) into a validated response payload.
    """

    model_config = ConfigDict(from_attributes=True)

    id: Optional[str] = None
    conversation_id: str
    conversation_name: Optional[str] = None
    llm_model: Optional[str] = None
    enable_image_generation: Optional[bool] = None
    image_generation_model: Optional[str] = None
    folder: Optional[str] = None
    pinned: Optional[bool] = False
    history: Optional[List[GeneratedMessage]] = Field(default_factory=list)
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    assistant_ids: Optional[List[str]] = Field(default_factory=list)
    assistant_data: Optional[List[AssistantDetails]] = Field(default_factory=list)
    initial_assistant_id: Optional[str] = None
    final_user_mark: Optional[UserMark] = None
    final_operator_mark: Optional[FinalOperatorFeedback] = None
    project: Optional[str] = None
    mcp_server_single_usage: Optional[bool] = False
    is_workflow_conversation: Optional[bool] = False
    conversation_details: Optional[LegacyChatDetails] = None
    assistant_details: Optional[AssistantDetails] = None
    user_abilities: Optional[List[Action]] = None
    is_folder_migrated: Optional[bool] = False
    category: Optional[str] = None
    date: Optional[datetime] = None
    update_date: Optional[datetime] = None
    very_first_msg_at: Optional[datetime] = None
    very_last_msg_at: Optional[datetime] = None

    pagination: Optional[ConversationHistoryPaginationData] = None

    @model_serializer(mode="wrap")
    def _exclude_null_pagination(self, handler):
        """Supports backward compatibility with request
        without pagination
        """
        data = handler(self)
        if data.get("pagination") is None:
            data.pop("pagination", None)
        return data


class ConversationExportFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    JSON = "json"


class ConversationHistoryPaginationData(PaginationData):
    """History pagination metadata with navigation helpers."""

    has_next: bool
    has_previous: bool


class SearchResultItem(BaseModel):
    """Single search result item (chat or folder)"""

    id: str  # Chat ID or Folder ID
    name: str  # Chat or folder name
    updated_at: datetime  # Last update timestamp
    type: Literal['chat', 'folder']  # Discriminator
    folder: Optional[str] = None  # Parent folder (for chats only)


class ConversationSearchResponse(BaseModel):
    """Response for conversation search endpoint"""

    items: List[SearchResultItem]  # Combined chats + folders, sorted by updated_at DESC
