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

import yaml
from datetime import datetime
from typing import List, Optional

from langgraph.pregel._retry import RetryPolicy
from pydantic import BaseModel, ValidationError, computed_field

from codemie.configs import config, logger
from codemie.core.ability import Owned, Action
from codemie.core.constants import DEMO_PROJECT
from codemie.core.models import UserEntity
from codemie.rest_api.models.base import (
    CommonBaseModel,
    BaseModelWithSQLSupport,
    PydanticType,
    PydanticListType,
)
from codemie.rest_api.models.guardrail import GuardrailAssignmentItem
from codemie.rest_api.security.user import User
from codemie.core.workflow_models.constants import (
    RETRY_POLICY_DEFAULT_BACKOFF_FACTOR,
    RETRY_POLICY_DEFAULT_INITIAL_INTERVAL,
    RETRY_POLICY_DEFAULT_MAX_ATTEMPTS,
    RETRY_POLICY_DEFAULT_MAX_INTERVAL,
)
from codemie.core.workflow_models.workflow_models import (
    CustomWorkflowNode,
    WorkflowState,
    WorkflowTool,
    WorkflowMode,
    WorkflowAssistant,
    WorkflowRetryPolicy,
)
from sqlmodel import Field as SQLField, Column, Index, text, Session, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import Text


class YamlConfigHistory(BaseModel):
    yaml_config: str
    date: datetime
    created_by: Optional[UserEntity] = None


class BedrockFlowData(BaseModel):
    bedrock_flow_id: str
    bedrock_flow_alias_id: str
    bedrock_aws_settings_id: str


class WorkflowConfigBase(CommonBaseModel, Owned):
    assistants: Optional[List[WorkflowAssistant]] = SQLField(
        default_factory=list, sa_column=Column(PydanticListType(WorkflowAssistant))
    )
    custom_nodes: Optional[List[CustomWorkflowNode]] = SQLField(
        default_factory=list, sa_column=Column(PydanticListType(CustomWorkflowNode))
    )
    created_by: Optional[UserEntity] = SQLField(default=None, sa_column=Column(PydanticType(UserEntity)))
    tools: Optional[List[WorkflowTool]] = SQLField(
        default_factory=list, sa_column=Column(PydanticListType(WorkflowTool))
    )
    description: str
    icon_url: Optional[str] = None
    mode: WorkflowMode = WorkflowMode.SEQUENTIAL
    name: str
    type: Optional[str] = None
    project: str = SQLField(default=DEMO_PROJECT, index=True)
    shared: bool = SQLField(default=True, index=True)
    states: Optional[List[WorkflowState]] = SQLField(
        default_factory=list, sa_column=Column(PydanticListType(WorkflowState))
    )
    schema_url: Optional[str] = None
    supervisor_prompt: Optional[str] = None
    updated_by: Optional[UserEntity] = SQLField(default=None, sa_column=Column(PydanticType(UserEntity)))
    user_abilities: Optional[List[Action]] = SQLField(default_factory=list, sa_column=Column(JSONB))
    yaml_config: Optional[str] = None
    yaml_config_history: List[YamlConfigHistory] = SQLField(
        default_factory=list, sa_column=Column(PydanticListType(YamlConfigHistory))
    )

    tokens_limit_before_summarization: Optional[int] = None
    retry_policy: WorkflowRetryPolicy = SQLField(default=None, sa_column=Column(PydanticType(WorkflowRetryPolicy)))
    messages_limit_before_summarization: Optional[int] = None
    enable_summarization_node: Optional[bool] = True
    recursion_limit: Optional[int] = None
    max_concurrency: Optional[int] = None
    verbose: Optional[bool] = True  # Write thoughts or not
    max_iteration_key_output_limit: Optional[int] = 200
    are_tools_migrated: Optional[bool] = None
    bedrock: Optional[BedrockFlowData] = SQLField(default=None, sa_column=Column(PydanticType(BedrockFlowData)))
    meta_config: Optional[str] = SQLField(default=None, sa_column=Column(Text))

    # Custom PostgreSQL indexes
    __table_args__ = (
        Index('ix_workflows_created_by_user_id', text("(created_by->>'user_id')")),
        Index('ix_workflows_update_date', 'update_date'),
        Index('ix_workflows_name', "name", postgresql_using='gin', postgresql_ops={"name": "gin_trgm_ops"}),
        Index(
            'ix_workflows_description',
            "description",
            postgresql_using='gin',
            postgresql_ops={"description": "gin_trgm_ops"},
        ),
        Index(
            'ix_workflows_bedrock_aws_settings_id',
            text("(bedrock->>'bedrock_aws_settings_id')"),
            postgresql_using='btree',
        ),
        Index(
            'uq_workflows_bedrock_settings_flow_unique',
            text("(bedrock->>'bedrock_aws_settings_id')"),
            text("(bedrock->>'bedrock_flow_id')"),
            text("(bedrock->>'bedrock_flow_alias_id')"),
            unique=True,
            postgresql_where=text(
                "(bedrock->>'bedrock_aws_settings_id') IS NOT NULL AND "
                "(bedrock->>'bedrock_flow_id') IS NOT NULL AND "
                "(bedrock->>'bedrock_flow_alias_id') IS NOT NULL"
            ),
        ),
    )

    def __init__(self, **data):
        # Handle retry_policy before SQLModel initialization
        if (
            'retry_policy' not in data
            or data['retry_policy'] is None
            or (isinstance(data['retry_policy'], str) and data['retry_policy'].lower() == 'none')
        ):
            data['retry_policy'] = {}
        super().__init__(**data)

    @computed_field(return_type=Optional[List[GuardrailAssignmentItem]])
    @property
    def guardrail_assignments(self) -> Optional[List[GuardrailAssignmentItem]]:
        """
        Transient guardrail_assignments field - not stored in database

        ! Get object request scope only !
        """
        return getattr(self, '_guardrail_assignments', None)

    @guardrail_assignments.setter
    def guardrail_assignments(self, value: Optional[List[GuardrailAssignmentItem]]):
        self._guardrail_assignments = value

    @classmethod
    def from_yaml(cls, yaml_obj: str | dict, *_args, **kwargs):
        """Instantiate a WorkflowConfig object from a YAML string"""
        try:
            yaml_dict = yaml.safe_load(yaml_obj) if isinstance(yaml_obj, str) else yaml_obj
            name = yaml_dict.get('name')
            description = yaml_dict.get('description')
            mode = yaml_dict.get('mode')
            yaml_config = yaml_dict.get("execution_config", {})
            assistants = yaml_config.get('assistants', [])
            tools = yaml_config.get('tools', [])
            custom_nodes = yaml_config.get('custom_nodes', [])
            states = yaml_config.get('states', [])
            retry_policy = yaml_config.get('retry_policy')
            messages_limit_before_summarization = yaml_config.get('messages_limit_before_summarization')
            tokens_limit_before_summarization = yaml_config.get('tokens_limit_before_summarization')
            workflow_type = yaml_config.get('type')
            enable_summarization_node = yaml_config.get('enable_summarization_node', False)
            icon_url = yaml_dict.get('icon_url', None)
            verbose = yaml_dict.get('verbose', True)
            max_iteration_key_output_limit = yaml_config.get('max_iteration_key_output_limit', 200)

            return cls(
                name=name,
                description=description,
                icon_url=icon_url,
                mode=mode,
                yaml_config=yaml.safe_dump(yaml_config),
                assistants=assistants,
                custom_nodes=custom_nodes,
                tools=tools,
                states=states,
                retry_policy=retry_policy,
                messages_limit_before_summarization=messages_limit_before_summarization,
                tokens_limit_before_summarization=tokens_limit_before_summarization,
                type=workflow_type,
                enable_summarization_node=enable_summarization_node,
                verbose=verbose,
                max_iteration_key_output_limit=max_iteration_key_output_limit,
                **kwargs,
            )
        except ValidationError:
            logger.error(f"Workflow config is invalid. Yaml: {yaml_obj}", exc_info=True)
            return None
        except yaml.YAMLError:
            logger.error(f"Error parsing YAML codemie.core.workflow. Yaml: {yaml_obj}", exc_info=True)
            return None

    def parse_execution_config(self):
        if not self.yaml_config:
            logger.error(
                f"Parse execution config failed for {self.name}. "
                f"execution_config section is absent in {self.yaml_config}"
            )
            return
        yaml_data = yaml.safe_load(self.yaml_config) or {}
        self.assistants = [WorkflowAssistant(**assistant_data) for assistant_data in yaml_data.get("assistants", [])]
        self.custom_nodes = [CustomWorkflowNode(**tool_data) for tool_data in yaml_data.get("custom_nodes", [])]
        self.tools = [WorkflowTool(**tool_data) for tool_data in yaml_data.get("tools", [])]

        if "states" in yaml_data:
            self.states = []
            for state_data in yaml_data["states"]:
                new_state = WorkflowState(**state_data)
                retry_policy = state_data.get("retry_policy", {})
                new_state.retry_policy = WorkflowRetryPolicy(**retry_policy)
                self.states.append(new_state)

        self.retry_policy = WorkflowRetryPolicy(**yaml_data.get("retry_policy", {}))

        self.messages_limit_before_summarization = yaml_data.get("messages_limit_before_summarization")
        self.tokens_limit_before_summarization = yaml_data.get("tokens_limit_before_summarization")
        self.type = yaml_data.get("type", 'generic')
        self.enable_summarization_node = yaml_data.get("enable_summarization_node", True)
        self.recursion_limit = yaml_data.get("recursion_limit", None)
        self.max_concurrency = yaml_data.get("max_concurrency", None)
        self.verbose = yaml_data.get("verbose", True)
        self.max_iteration_key_output_limit = yaml_data.get("max_iteration_key_output_limit", 200)

    def get_max_concurrency(self):
        """Get the maximum concurrency for the codemie.core.workflow based on the configuration"""
        if self.max_concurrency is None:
            return config.WORKFLOW_DEFAULT_CONCURRENCY

        if self.max_concurrency > config.WORKFLOW_MAX_CONCURRENCY:
            return config.WORKFLOW_MAX_CONCURRENCY

        if self.max_concurrency < 1:
            return 1

        return self.max_concurrency

    def get_effective_retry_policy(self, state: WorkflowState) -> RetryPolicy:
        effective_policy = WorkflowConfigBase._get_default_retry_policy().model_dump()
        workflow_retry_policy = self.retry_policy.model_dump(exclude_none=True)
        state_retry_policy = state.retry_policy.model_dump(exclude_none=True)
        effective_policy.update(workflow_retry_policy)
        effective_policy.update(state_retry_policy)
        retry_policy = RetryPolicy(**effective_policy, retry_on=WorkflowRetryPolicy.custom_retry_on)
        return retry_policy

    @staticmethod
    def _get_default_retry_policy() -> WorkflowRetryPolicy:
        return WorkflowRetryPolicy(
            initial_interval=RETRY_POLICY_DEFAULT_INITIAL_INTERVAL,
            backoff_factor=RETRY_POLICY_DEFAULT_BACKOFF_FACTOR,
            max_interval=RETRY_POLICY_DEFAULT_MAX_INTERVAL,
            max_attempts=RETRY_POLICY_DEFAULT_MAX_ATTEMPTS,
        )

    def is_owned_by(self, user: User):
        return self.created_by.user_id == user.id

    def is_managed_by(self, user: User):
        return self.project in user.admin_project_names

    def is_shared_with(self, user: User):
        return self.project in user.project_names and self.shared


class WorkflowConfig(BaseModelWithSQLSupport, WorkflowConfigBase, table=True):
    __tablename__ = "workflows"

    @classmethod
    def delete(cls, workflow_id: str):
        workflow = cls.find_by_id(workflow_id)
        if workflow:
            return BaseModelWithSQLSupport.delete(workflow)
        return {"status": "not found"}

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


class WorkflowConfigTemplate(WorkflowConfigBase):
    slug: str
    video_link: Optional[str] = None

    @classmethod
    def from_yaml(cls, yaml_str: str):
        """Initialize from YAML with Link to video"""
        try:
            yaml_dict = yaml.safe_load(yaml_str)
            return super().from_yaml(yaml_dict, slug=yaml_dict.get('slug'), video_link=yaml_dict.get('video_link'))
        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML codemie.core.workflow. Yaml: {yaml_str} {e}", exc_info=True)
            return None


class WorkflowConfigListResponse(BaseModel):
    """Minimal response model for workflow list (excludes heavy fields for performance)"""

    id: Optional[str] = None
    name: str
    description: str
    icon_url: Optional[str] = None
    created_by: Optional[UserEntity] = None
    updated_by: Optional[UserEntity] = None
    project: str
    shared: bool
    mode: WorkflowMode
    type: Optional[str] = None
    schema_url: Optional[str] = None
    date: Optional[datetime] = None
    update_date: Optional[datetime] = None
    user_abilities: Optional[List[Action]] = None


class WorkflowListResponse(BaseModel):
    class Pagination(BaseModel):
        page: int
        pages: int
        total: int
        per_page: int

    data: list[WorkflowConfigBase] | list[WorkflowConfigListResponse]
    pagination: Pagination
