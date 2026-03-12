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
from typing import Any, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Session, select

from codemie.core.ability import Owned, Ability, Action
from codemie.core.models import UserEntity, TokensUsage
from codemie.rest_api.models.base import (
    CommonBaseModel,
    BaseModelWithSQLSupport,
    PydanticType,
    PydanticListType,
)
from codemie.rest_api.models.conversation import GeneratedMessage
from codemie.rest_api.security.user import User
from sqlmodel import Column, Field as SQLField, Index, text


class WorkflowExecutionStatusEnum(str, Enum):
    IN_PROGRESS = "In Progress"
    NOT_STARTED = "Not Started"
    INTERRUPTED = "Interrupted"
    FAILED = "Failed"
    SUCCEEDED = "Succeeded"
    ABORTED = "Aborted"


class WorkflowExecutionCheckpoint(BaseModel):
    timestamp: str
    data: str
    metadata: str


class WorkflowExecutionStateThought(BaseModelWithSQLSupport, table=True):
    """Holds output of codemie.core.workflow execution state. Can be either root-level or nested"""

    __tablename__ = "workflow_execution_state_thoughts"

    execution_state_id: str = SQLField(index=True)
    parent_id: Optional[str] = SQLField(default=None, index=True)
    author_name: str
    author_type: str
    content: str
    input_text: Optional[str] = None

    @classmethod
    def get_root(
        cls, state_ids: List[str], include_children_field=False
    ) -> List['WorkflowExecutionStateThoughtShort|WorkflowExecutionStateThoughtWithChildren']:
        """Returns thought without the parent id (root-level)"""
        response_class = (
            WorkflowExecutionStateThoughtWithChildren if include_children_field else WorkflowExecutionStateThoughtShort
        )

        with Session(cls.get_engine()) as session:
            query = (
                select(cls)
                .where(
                    cls.execution_state_id.in_(state_ids),
                    cls.parent_id.is_(None),  # Root level thoughts have no parent
                )
                .order_by(cls.date.asc())
            )

            results = session.exec(query).all()
        return [response_class(**result.model_dump()) for result in results]

    @classmethod
    def get_all(cls, ids: List[str]) -> List['WorkflowExecutionStateThoughtWithChildren']:
        """Returns all thoughts by state ids"""
        with Session(cls.get_engine()) as session:
            query = select(cls).where(cls.id.in_(ids)).order_by(cls.date.asc())

            results = session.exec(query).all()
        return [WorkflowExecutionStateThoughtWithChildren(**result.model_dump()) for result in results]

    @classmethod
    def get_all_by_parent_ids(
        cls,
        parent_ids: List[str],
    ) -> List['WorkflowExecutionStateThoughtWithChildren']:
        """Returns all thoughts by parent ids"""
        with Session(cls.get_engine()) as session:
            query = select(cls).where(cls.parent_id.in_(parent_ids)).order_by(cls.date.asc())

            results = session.exec(query).all()
        return [WorkflowExecutionStateThoughtWithChildren(**result.model_dump()) for result in results]


class BaseWorkflowExecutionState(CommonBaseModel):
    execution_id: str = SQLField(index=True)
    name: str
    task: Optional[str] = ""
    status: WorkflowExecutionStatusEnum = WorkflowExecutionStatusEnum.NOT_STARTED
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class WorkflowExecutionState(BaseModelWithSQLSupport, BaseWorkflowExecutionState, table=True):
    __tablename__ = "workflow_execution_states"
    output: Optional[str] = None


class WorkflowExecutionTransition(BaseModelWithSQLSupport, table=True):
    """Captures intermediate workflow state transitions between nodes.

    Records the transition from one workflow node to another, including:
    - from_state: The node that just completed execution
    - to_state: The next node to be executed
    - workflow_context: Full LangGraph state snapshot at transition point (JSONB)
    - date: Timestamp inherited from base model (automatically set on save)

    This provides observability and debugging capabilities by allowing replay
    of the exact data flow between workflow nodes.
    """

    __tablename__ = "workflow_execution_transitions"

    execution_id: str = SQLField(index=True)
    from_state_id: Optional[str] = SQLField(default=None, index=True)
    to_state_id: Optional[str] = SQLField(default=None, index=True)
    workflow_context: dict[str, Any] = SQLField(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )


class WorkflowExecution(BaseModelWithSQLSupport, Owned, table=True):
    __tablename__ = "workflow_executions"

    workflow_id: str = SQLField(index=True)
    execution_id: str = SQLField(index=True)
    overall_status: WorkflowExecutionStatusEnum = WorkflowExecutionStatusEnum.NOT_STARTED
    output: Optional[str] = None
    name: Optional[str] = None
    prompt: Optional[str] = None
    file_name: Optional[str] = None
    created_by: Optional[UserEntity] = SQLField(default=None, sa_column=Column(PydanticType(UserEntity)))
    updated_by: Optional[UserEntity] = SQLField(default=None, sa_column=Column(PydanticType(UserEntity)))
    checkpoints: Optional[List[WorkflowExecutionCheckpoint]] = SQLField(
        default_factory=list, sa_column=Column(PydanticListType(WorkflowExecutionCheckpoint))
    )
    tokens_usage: Optional[TokensUsage] = SQLField(default=None, sa_column=Column(PydanticType(TokensUsage)))
    project: Optional[str] = SQLField(default=None)
    date: Optional[datetime] = None
    update_date: Optional[datetime] = None
    history: Optional[List[GeneratedMessage]] = SQLField(
        default_factory=list, sa_column=Column(PydanticListType(GeneratedMessage))
    )
    conversation_id: Optional[str] = SQLField(default=None, index=True)
    # Custom PostgreSQL indexes
    __table_args__ = (Index('ix_workflow_executions_created_by_user_id', text("(created_by->>'user_id')")),)

    @classmethod
    def get_by_workflow_id(cls, workflow_id: str, user: Optional[User] = None):
        entries = cls.get_all_by_fields({"workflow_id": workflow_id})
        if user:
            return [entry for entry in entries if Ability(user).can(Action.READ, entry)]

        return entries

    @classmethod
    def get_by_execution_id(cls, execution_id: str):
        return cls.get_all_by_fields({"execution_id": execution_id})

    @classmethod
    def delete(cls, execution_config_id: str):
        from sqlalchemy import delete as sql_delete

        with Session(cls.get_engine()) as session:
            execution = session.get(cls, execution_config_id)
            if execution:
                # Collect state IDs so we can cascade to thoughts (grandchild records)
                state_ids = [
                    row.id
                    for row in session.exec(
                        select(WorkflowExecutionState).where(
                            WorkflowExecutionState.execution_id == execution.execution_id
                        )
                    ).all()
                ]
                # Delete thoughts first (grandchild → child → parent order)
                if state_ids:
                    session.exec(
                        sql_delete(WorkflowExecutionStateThought).where(
                            WorkflowExecutionStateThought.execution_state_id.in_(state_ids)
                        )
                    )
                # Bulk-delete transitions (child level - linked to execution)
                session.exec(
                    sql_delete(WorkflowExecutionTransition).where(
                        WorkflowExecutionTransition.execution_id == execution.execution_id
                    )
                )
                # Bulk-delete states
                session.exec(
                    sql_delete(WorkflowExecutionState).where(
                        WorkflowExecutionState.execution_id == execution.execution_id
                    )
                )
                # Delete parent execution
                session.delete(execution)
                session.commit()
                return {"status": "deleted"}
            return {"status": "not found"}

    def start_progress(self):
        self.overall_status = WorkflowExecutionStatusEnum.IN_PROGRESS
        self.save()

    def is_owned_by(self, user: User):
        return self.created_by.user_id == user.id

    def is_managed_by(self, user: User):
        return self.project in user.admin_project_names

    def is_shared_with(self, user: User):
        return self.project in user.project_names


class UpdateWorkflowExecutionOutputRequest(BaseModel):
    output: str = Field(min_length=1)
    state_id: str


class WorkflowExecutionOutputChangeRequest(BaseModel):
    original_output: str
    request: str


class WorkflowExecutionResponse(BaseModel):
    id: Optional[str] = None
    prompt: Optional[str] = None
    file_name: Optional[str] = None
    workflow_id: str
    execution_id: str
    conversation_id: Optional[str] = None
    overall_status: WorkflowExecutionStatusEnum = WorkflowExecutionStatusEnum.NOT_STARTED
    tokens_usage: Optional[TokensUsage] = None
    created_by: Optional[UserEntity] = None
    updated_by: Optional[UserEntity] = None
    date: Optional[datetime] = None
    update_date: Optional[datetime] = None


class CreateWorkflowExecutionRequest(BaseModel):
    user_input: Optional[str] = ""
    file_name: Optional[str] = None
    propagate_headers: bool = False
    stream: bool = False
    conversation_id: Optional[str] = None  # Continue existing conversation or create new one
    session_id: Optional[str] = Field(
        default=None,
        description="Session identifier for Langfuse tracing. If not provided, execution_id will be used.",
    )
    disable_cache: Optional[bool] = Field(
        default=False,
        description="Disable prompt caching for this workflow execution (applies to workflow agent LLMs only)",
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="Tags to attach to the Langfuse workflow execution trace.",
    )
    delete_on_completion: bool = Field(
        default=False,
        description="If true, automatically delete the workflow execution after completion (success or failure). "
        "If Langfuse traces are enabled, deletion occurs after traces are submitted.",
    )


class WorkflowExecutionStateThoughtShort(BaseModel):
    id: str
    execution_state_id: str
    parent_id: Optional[str] = None
    author_name: str
    author_type: str
    date: datetime


class WorkflowExecutionStateThoughtWithChildren(BaseModel):
    id: str
    execution_state_id: str
    parent_id: Optional[str] = None
    author_name: str
    author_type: str
    input_text: Optional[str] = None
    content: str
    date: datetime
    children: Optional[List['WorkflowExecutionStateThoughtWithChildren']] = None


class WorkflowExecutionStateResponse(BaseWorkflowExecutionState):
    thoughts: List[WorkflowExecutionStateThoughtShort] = []


class WorkflowExecutionStateOutput(BaseModel):
    output: Optional[str] = None


class WorkflowExecutionStateWithThougths(BaseWorkflowExecutionState):
    output: Optional[str] = None
    thoughts: List[WorkflowExecutionStateThoughtShort] = []


class WorkflowExecutionTransitionResponse(BaseModel):
    """Response model for workflow execution transition records.

    Represents a single node-to-node transition with full context snapshot.
    """

    id: str
    execution_id: str
    from_state_id: Optional[str] = None
    to_state_id: Optional[str] = None
    workflow_context: dict[str, Any]
    date: datetime


class WorkflowConversationListItem(BaseModel):
    """Workflow conversation list item for chat history display"""

    conversation_id: str
    workflow_id: str
    workflow_name: str
    execution_count: int  # Number of executions in this conversation
    last_user_input: Optional[str] = None
    last_execution_id: Optional[str] = None
    last_execution_status: Optional[WorkflowExecutionStatusEnum] = None
    date: datetime  # Date of first execution
    update_date: Optional[datetime] = None  # Date of last execution
