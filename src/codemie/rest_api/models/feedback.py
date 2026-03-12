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

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import UUID4, BaseModel, Field

from codemie.rest_api.models.base import (
    BaseModelWithSQLSupport,
    PydanticListType,
)
from sqlmodel import Field as SQLField, Column, UUID, String
from codemie.rest_api.models.standard import AuthorEnum
from codemie.core.models import ChatMessage


class MarkEnum(str, Enum):
    CORRECT = "correct"
    PARTIALLY_CORRECT = "partially correct"
    WRONG = "wrong"


class FeedbackEntry(BaseModelWithSQLSupport, table=True):
    __tablename__ = "feedback"

    request: str
    response: str
    comments: Optional[str] = None
    date: Optional[datetime] = None
    conversationId: UUID4 = SQLField(sa_column=Column("conversation_id", UUID))
    history: Optional[List[ChatMessage]] = SQLField(
        default_factory=list, sa_column=Column(PydanticListType(ChatMessage))
    )
    mark: MarkEnum
    appName: Optional[str] = SQLField(default=None, sa_column=Column("app_name", String))
    repoName: Optional[str] = SQLField(default=None, sa_column=Column("repo_name", String))
    indexType: Optional[str] = SQLField(default=None, sa_column=Column("index_type", String))

    @classmethod
    def delete_feedback(cls, id: str):
        feedback = cls.find_by_id(id)
        if feedback:
            return feedback.delete()
        return {"status": "not found"}


class FeedbackRequest(BaseModel):
    conversation_id: str = Field(..., alias="conversationId")
    assistant_id: str
    request: str
    response: str
    message_index: int = Field(..., alias="messageIndex")
    author: AuthorEnum
    mark: MarkEnum
    feedback_id: Optional[str] = None
    history: Optional[List[ChatMessage]] = None
    comments: Optional[str] = None
    type: Optional[str] = None
    appName: Optional[str] = None
    repoName: Optional[str] = None
    indexType: Optional[str] = None


class FeedbackDeleteRequest(BaseModel):
    conversation_id: str = Field(..., alias="conversationId")
    feedback_id: str = Field(..., alias="feedbackId")
    message_index: int = Field(..., alias="messageIndex")
    assistant_id: str
    author: AuthorEnum
