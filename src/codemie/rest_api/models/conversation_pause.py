# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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


"""Tables holding a conversation's tool confirmation pause: LangGraph checkpoints and the pending tool calls."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Column, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

CONVERSATION_ID_FOREIGN_KEY = "conversations.id"


class ConversationCheckpointRow(SQLModel, table=True):
    """One LangGraph checkpoint per (conversation, namespace), with the writes of its checkpoint id.

    ``checkpoint`` and ``metadata`` are nullable: a writes-only row is not a checkpoint.
    """

    __tablename__ = "conversation_checkpoints"

    conversation_id: str = Field(primary_key=True, foreign_key=CONVERSATION_ID_FOREIGN_KEY, ondelete="CASCADE")
    checkpoint_ns: str = Field(primary_key=True)
    checkpoint_id: str | None = Field(default=None)
    checkpoint: dict[str, Any] | None = Field(default=None, sa_column=Column("checkpoint", JSONB, nullable=True))
    # ``metadata`` is reserved on SQLModel classes, so the attribute is named differently.
    checkpoint_metadata: dict[str, Any] | None = Field(default=None, sa_column=Column("metadata", JSONB, nullable=True))
    writes: dict[str, Any] | None = Field(default=None, sa_column=Column("writes", JSONB, nullable=True))


class ConversationPendingToolCallRow(SQLModel, table=True):
    """The one pending pause of a conversation."""

    __tablename__ = "conversation_pending_tool_calls"

    conversation_id: str = Field(primary_key=True, foreign_key=CONVERSATION_ID_FOREIGN_KEY, ondelete="CASCADE")
    root_assistant_id: str | None = Field(default=None)
    history_index: int | None = Field(default=None)
    original_user_message: str | None = Field(
        default=None, sa_column=Column("original_user_message", Text, nullable=True)
    )
    items: list[Any] | None = Field(default=None, sa_column=Column("items", JSONB, nullable=True))
    thoughts: list[Any] | None = Field(default=None, sa_column=Column("thoughts", JSONB, nullable=True))
    payload: dict[str, Any] | None = Field(default=None, sa_column=Column("payload", JSONB, nullable=True))
