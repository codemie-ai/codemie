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
from typing import List, Optional, Literal
from uuid import uuid4

from pydantic import BaseModel, field_validator

from codemie.core.ability import Owned
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.base import BaseModelWithSQLSupport, PydanticType
from sqlmodel import Field as SQLField, Session, select, Column, Index, text as sqltext

from codemie.rest_api.security.user import User


class GuardrailEntity(str, Enum):
    ASSISTANT = "assistant"
    WORKFLOW = "workflow"
    KNOWLEDGEBASE = "knowledgebase"
    PROJECT = "project"


class BedrockGuardrailData(BaseModel):
    bedrock_guardrail_id: str
    bedrock_version: str
    bedrock_name: str
    bedrock_status: str
    bedrock_created_at: datetime
    bedrock_updated_at: Optional[datetime] = None
    bedrock_aws_settings_id: str


class Guardrail(BaseModelWithSQLSupport, Owned, table=True):
    __tablename__ = "guardrails"  # type: ignore
    project_name: str = SQLField(index=True)
    description: str = SQLField(max_length=500, default="")
    created_by: Optional[CreatedByUser] = SQLField(default=None, sa_column=Column(PydanticType(CreatedByUser)))
    bedrock: Optional[BedrockGuardrailData] = SQLField(
        default=None, sa_column=Column(PydanticType(BedrockGuardrailData))
    )

    # Custom PostgreSQL indexes
    __table_args__ = (
        Index('ix_guardrail_created_by_id', sqltext("(created_by->>'id')")),
        Index('ix_guardrail_created_by_name', sqltext("(created_by->>'name') gin_trgm_ops"), postgresql_using='gin'),
        Index('ix_guardrail_date', 'date'),
        Index(
            'ix_guardrail_bedrock_aws_settings_id',
            sqltext("(bedrock->>'bedrock_aws_settings_id')"),
            postgresql_using='btree',
        ),
        Index(
            'uq_guardrail_bedrock_settings_guardrail_unique',
            sqltext("(bedrock->>'bedrock_aws_settings_id')"),
            sqltext("(bedrock->>'bedrock_guardrail_id')"),
            sqltext("(bedrock->>'bedrock_version')"),
            unique=True,
            postgresql_where=sqltext(
                "(bedrock->>'bedrock_aws_settings_id') IS NOT NULL AND "
                "(bedrock->>'bedrock_guardrail_id') IS NOT NULL AND "
                "(bedrock->>'bedrock_version') IS NOT NULL"
            ),
        ),
    )

    def is_owned_by(self, user: User) -> bool:
        return self.created_by.id == user.id  # type: ignore

    def is_managed_by(self, user: User) -> bool:
        return self.project_name in user.admin_project_names

    def is_shared_with(self, user: User) -> bool:
        return self.project_name in user.project_names

    @classmethod
    def get_by_bedrock_aws_settings_id(cls, bedrock_aws_settings_id: str):
        """
        Retrieve all indexes filtered by bedrock_aws_settings_id.
        """
        with Session(cls.get_engine()) as session:
            statement = select(cls).where(
                cls.bedrock["bedrock_aws_settings_id"].astext == bedrock_aws_settings_id  # type: ignore
            )
            return session.exec(statement).all()


class GuardrailSource(str, Enum):
    INPUT = "input"
    OUTPUT = "output"
    BOTH = "both"


class GuardrailMode(str, Enum):
    ALL = "all"
    FILTERED = "filtered"


class GuardrailAssignment(BaseModelWithSQLSupport, Owned, table=True):
    __tablename__ = "guardrail_assignments"  # type: ignore
    project_name: str = SQLField(index=True)  # for the Owned permissions logic

    id: str = SQLField(default_factory=lambda: str(uuid4()), primary_key=True)
    guardrail_id: str = SQLField(index=True)

    entity_type: GuardrailEntity = SQLField(index=True)
    entity_id: str = SQLField(index=True)  # Can be assistant_id, workflow_id, knowledgebase_id, or project_name

    source: GuardrailSource = SQLField(default=GuardrailSource.INPUT)
    mode: GuardrailMode = SQLField(default=GuardrailMode.FILTERED)
    scope: Optional[GuardrailEntity] = SQLField(default=None)  # For project-level assignments

    created_by: Optional[CreatedByUser] = SQLField(default=None, sa_column=Column(PydanticType(CreatedByUser)))

    __table_args__ = (
        Index('ix_guardrail_assignment_entity', 'entity_type', 'entity_id'),
        Index('ix_guardrail_assignment_guardrail', 'guardrail_id'),
        Index(
            'ix_guardrail_assignment_project_scope',
            'entity_type',
            'entity_id',
            'scope',
            'source',
        ),
        Index('ix_guardrail_assignment_entity_project_source', 'entity_type', 'entity_id', 'source'),
        # Unique constraint for rows with scope
        Index(
            'uq_guardrail_assignment_with_scope',
            'guardrail_id',
            'entity_type',
            'entity_id',
            'scope',
            'source',
            'mode',
            unique=True,
            postgresql_where=sqltext('scope IS NOT NULL'),
        ),
        # Unique constraint for rows without scope
        Index(
            'uq_guardrail_assignment_null_scope',
            'guardrail_id',
            'entity_type',
            'entity_id',
            'source',
            'mode',
            unique=True,
            postgresql_where=sqltext('scope IS NULL'),
        ),
    )

    def is_owned_by(self, user: User) -> bool:
        return self.created_by.id == user.id  # type: ignore

    def is_managed_by(self, user: User) -> bool:
        return self.project_name in user.admin_project_names

    def is_shared_with(self, user: User) -> bool:
        return self.project_name in user.project_names


class GuardrailSettings(BaseModel):
    mode: GuardrailMode
    source: GuardrailSource

    @field_validator('mode', 'source', mode='before')
    def normalize_enums(cls, v):
        """Normalize enum values to lowercase to accept both upper and lowercase."""
        if isinstance(v, str):
            return v.lower()
        return v


class GuardrailSettingsWithAccess(GuardrailSettings):
    editable: bool


class GuardrailAssignmentItem(BaseModel):
    """Represents a guardrail assignment to an entity with specific settings."""

    guardrail_id: str
    mode: GuardrailMode
    source: GuardrailSource
    editable: Optional[bool] = None
    guardrail_name: Optional[str] = None

    @field_validator('mode', 'source', mode='before')
    def normalize_enums(cls, v):
        """Normalize enum values to lowercase to accept both upper and lowercase."""
        if isinstance(v, str):
            return v.lower()
        return v


class EntityAssignmentItem(BaseModel):
    id: str
    settings: List[GuardrailSettings]


class EntityAssignmentConfig(BaseModel):
    settings: Optional[List[GuardrailSettings]] = None
    items: Optional[List[EntityAssignmentItem]] = None


class ProjectAssignmentConfig(BaseModel):
    settings: List[GuardrailSettings]


class GuardrailAssignmentRequestResponse(BaseModel):
    project: Optional[ProjectAssignmentConfig] = None
    assistants: Optional[EntityAssignmentConfig] = None
    datasources: Optional[EntityAssignmentConfig] = None
    workflows: Optional[EntityAssignmentConfig] = None


class BulkAssignmentResult(BaseModel):
    success: int
    failed: int
    errors: List[str]


class GuardrailContentSource(BaseModel):
    text: str
    category: Optional[List[Literal["grounding_source", "query", "guard_content"]]] = None


class GuardrailContentItem(BaseModel):
    type: Literal["text"]  # Only 'text' type is supported for version 1."
    source: GuardrailContentSource


class GuardrailApplyRequest(BaseModel):
    content: List[GuardrailContentItem]
