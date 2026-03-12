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
from typing import List, Optional

from codemie_tools.base.models import Tool
from pydantic import BaseModel, Field, model_validator

from codemie.chains.base import Thought
from codemie.configs import logger
from codemie.core.ability import Owned, Action
from codemie.core.models import CodeIndexType, ChatMessage, ChatRole
from codemie.rest_api.models.assistant import Context, AssistantType
from codemie.rest_api.models.base import (
    BaseModelWithSQLSupport,
    PydanticListType,
    PydanticType,
)
from codemie.rest_api.models.feedback import MarkEnum
from codemie.rest_api.security.user import User
from sqlmodel import Field as SQLField, Session, delete, Column
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
    ):
        user_message = GeneratedMessage(
            date=datetime.now(),
            role=ChatRole.USER,
            message_raw=user_query_raw,
            file_names=file_names,
            history_index=history_index,
            message=user_query,
        )
        assistant_message = GeneratedMessage(
            date=datetime.now(),
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

        self.history = [
            *(self.history or []),
            user_message,
            assistant_message,
        ]
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
        from codemie.service.conversation.history_materializer import materialize_history

        conversation = super().get_by_id(id_)
        conversation.history = materialize_history(conversation.history, conversation.initial_assistant_id)
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
        from codemie.service.conversation.history_materializer import materialize_history

        conversation = super().find_by_id(id_)
        if conversation:
            conversation.history = materialize_history(conversation.history, conversation.initial_assistant_id)
        return conversation

    @classmethod
    def get_user_conversations(cls, user_id, filters: dict = None) -> List[ConversationListItem]:
        """
        Get all user conversations (both assistant and workflow conversations).
        Uses is_workflow_conversation flag to distinguish conversation types.
        """
        records = cls.get_all_by_fields(fields={"user_id.keyword": user_id, **(filters if filters else {})})

        conversation_list = []
        for conversation in records:
            # Use the is_workflow_conversation flag to determine conversation type
            is_workflow = conversation.is_workflow_conversation or False
            workflow_id = conversation.initial_assistant_id if is_workflow else None
            conversation_id = conversation.conversation_id

            conversation_list.append(
                ConversationListItem(
                    id=conversation.conversation_id,
                    name=conversation.get_conversation_name(),
                    folder=conversation.folder,
                    assistant_ids=conversation.assistant_ids,
                    initial_assistant_id=conversation.initial_assistant_id,
                    pinned=conversation.pinned,
                    date=conversation.update_date or conversation.date,
                    is_workflow=is_workflow,
                    workflow_id=workflow_id,
                    conversation_id=conversation_id if is_workflow else None,
                )
            )

        return sorted(conversation_list, key=lambda x: x.date, reverse=True)

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
    def delete_by_id(cls, conversation_id: str):
        with Session(cls.get_engine()) as session:
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

    assistant_ids: Optional[List[str]] = Field(default_factory=list)
    initial_assistant_id: Optional[str] = None

    # Workflow-specific fields
    is_workflow: Optional[bool] = False
    workflow_id: Optional[str] = None
    conversation_id: Optional[str] = None


class ConversationExportFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    JSON = "json"
