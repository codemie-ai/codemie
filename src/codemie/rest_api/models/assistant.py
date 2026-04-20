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

from datetime import datetime, UTC
from enum import Enum, StrEnum
from typing import Any, Optional, Self, Dict, List

import yaml
from codemie_tools.base.models import ToolKit, Tool
from pydantic import BaseModel, Field, model_validator, field_serializer, computed_field
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field as SQLField, Session, select, Column, and_, Index, text

from codemie.core.ability import Owned, Ability, Action
from codemie.core.constants import CodeIndexType, ProviderIndexType, DEMO_PROJECT
from codemie.core.models import CreatedByUser, ToolConfig
from codemie.rest_api.a2a.types import AgentCard
from codemie.rest_api.models.base import (
    CommonBaseModel,
    BaseModelWithSQLSupport,
    PydanticType,
    PydanticListType,
)
from codemie.rest_api.models.guardrail import GuardrailAssignmentItem, GuardrailEntity
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.models.settings import SettingsBase
from codemie.rest_api.models.standard import PostResponse
from codemie.core.models import BaseResponse
from codemie.rest_api.security.user import User
from codemie.service.mcp.models import MCPServerConfig
from codemie.service.mcp_config_service import MCPConfigService
from codemie.configs.logger import logger
from codemie.service.guardrail.guardrail_service import GuardrailService

from codemie.rest_api.models.assistant_generator import RefineGeneratorResponse

PROMPT_DATE_FORMAT = '%a, %d %b %Y %H:%M:%S'
LANGCHAIN_VARS_REGEXP = r'{.*?}'


class MissingContextException(Exception):
    """Exception raised when expected context is missing."""

    pass


class AgentMode(str, Enum):
    GENERAL = "general"
    PLAN_EXECUTE = "plan_execute"


class AssistantType(str, Enum):
    CODEMIE = "codemie"
    A2A = "A2A"
    BEDROCK_AGENT = "bedrock_agent"
    BEDROCK_AGENTCORE_RUNTIME = "bedrock_agentcore_runtime"


class AssistantOrigin(str, Enum):
    UNKNOWN = "unknown"
    BEDROCK_AGENT_CORE = "bedrock_agentcore"


class ContextType(str, Enum):
    KNOWLEDGE_BASE = "knowledge_base"
    CODE = "code"
    PROVIDER = "provider"


class SettingsConfigLevel(StrEnum):
    """Level at which settings should be configured for a tool."""

    TOOL = "tool"
    TOOLKIT = "toolkit"


class Context(BaseModel):
    """Source for the assistant sources"""

    context_type: ContextType
    name: str

    def __eq__(self, other):
        if isinstance(other, Context):
            return self.context_type == other.context_type and self.name == other.name
        return False

    @classmethod
    def index_info_type(cls, index: IndexInfo):
        """Detect IndexInfo type to be used as assistant context"""
        if index.index_type in list(CodeIndexType):
            return ContextType.CODE

        if index.index_type == ProviderIndexType.PROVIDER:
            return ContextType.PROVIDER

        return ContextType.KNOWLEDGE_BASE

    @classmethod
    def index_info_type_from_index_type(cls, index_type: str) -> ContextType:
        """Convert index_type string to ContextType enum.

        Args:
            index_type: The index type string from IndexInfo

        Returns:
            The corresponding ContextType enum value
        """
        if index_type in [e.value for e in CodeIndexType]:
            return ContextType.CODE

        if index_type == ProviderIndexType.PROVIDER.value:
            return ContextType.PROVIDER

        return ContextType.KNOWLEDGE_BASE


class ToolDetails(Tool):
    name: str
    label: Optional[str] = None
    settings_config: bool = False
    settings: Optional[SettingsBase] = None
    user_description: Optional[str] = None


class ToolKitDetails(ToolKit):
    toolkit: str
    tools: list[ToolDetails]
    label: str = ""
    settings_config: bool = False
    settings: Optional[SettingsBase] = None
    is_external: Optional[bool] = False

    def get_tool_configs(self):
        return [
            ToolConfig(name=tool.settings.credential_type, integration_id=tool.settings.id)
            for tool in self.tools
            if tool.settings
        ]


class MCPServerDetails(BaseModel):
    """
    Configuration details for an MCP server to be used by an assistant.

    This class represents the configuration for an MCP (Model Context Protocol) server
    that can be used by an Assistant to provide tools and capabilities.
    """

    name: str = Field(description="Name of the MCP server configuration")
    description: Optional[str] = Field(None, description="Optional description of the MCP server")
    enabled: bool = Field(True, description="Whether this MCP server is enabled")
    mcp_config_id: Optional[str] = Field(
        None, description="Reference to MCP configuration in catalog (if selected from marketplace)"
    )
    config: Optional[MCPServerConfig] = Field(
        None, description="The MCP server configuration with command, args and environment variables"
    )
    mcp_connect_url: Optional[str] = Field(None, description="URL of the MCP-Connect server")
    tools_tokens_size_limit: Optional[int] = Field(None, description="Maximum size of the tokens for the tools")
    command: Optional[str] = Field(
        None, description="The command used to invoke the MCP server (e.g., 'npx', 'uvx') using a stdio transport"
    )
    arguments: Optional[str] = Field(None, description="list of arguments to pass to the MCP server command")
    settings: Optional[SettingsBase] = None  # Must be renamed to environment_vars
    integration_alias: Optional[str] = None
    mcp_connect_auth_token: Optional[SettingsBase] = None
    resolve_dynamic_values_in_arguments: bool = False
    tools: Optional[List[str]] = Field(
        None,
        description="Optional list of tool names to use from this MCP server. "
        "If specified, only these tools will be available. "
        "If None or empty, all tools from the server will be used.",
    )

    class Config:
        """Pydantic model configuration."""

        json_schema_extra = {
            "examples": [
                {
                    "name": "Filesystem MCP Server",
                    "description": "MCP server for filesystem operations",
                    "enabled": True,
                    "config": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/workspace"],
                        "env": {},
                    },
                },
                {
                    "name": "CLI MCP Server",
                    "description": "MCP server for CLI operations",
                    "enabled": True,
                    "config": {
                        "command": "uvx",
                        "args": ["cli-mcp-server"],
                        "env": {
                            "ALLOWED_DIR": "/home/user/workspace",
                            "ALLOWED_COMMANDS": "all",
                            "ALLOWED_FLAGS": "all",
                            "MAX_COMMAND_LENGTH": "2048",
                            "COMMAND_TIMEOUT": "300",
                            "TIMEOUT": "300",
                        },
                    },
                },
            ]
        }


# PromptVariable model for defining variables in system prompts
class PromptVariable(BaseModel):
    key: str = Field(..., description="The key that will be replaced in the system prompt", max_length=100)
    description: Optional[str] = Field(None, description="Description of what this variable represents", max_length=200)
    default_value: str = Field("", description="Default value for the variable", max_length=500)
    is_sensitive: bool = Field(
        False,
        description=(
            "Whether this variable contains sensitive data (credentials, tokens, etc.) that should be encrypted"
        ),
    )


class BedrockAgentData(BaseModel):
    bedrock_agent_id: str  # Store the Bedrock agent ID
    bedrock_agent_alias_id: str  # Store the Bedrock agent alias
    bedrock_agent_name: str  # Store the Bedrock agent name
    bedrock_agent_description: Optional[str] = None  # Store the Bedrock agent description
    bedrock_agent_version: str  # Version of the Bedrock agent used for this assistant
    bedrock_aws_settings_id: str  # ID of the AWS settings used for Bedrock agent


class BedrockAgentcoreRuntimeData(BaseModel):
    runtime_id: str
    runtime_arn: str
    runtime_endpoint_id: str
    runtime_endpoint_arn: str
    runtime_endpoint_name: str
    runtime_endpoint_live_version: str
    runtime_endpoint_description: Optional[str] = None
    aws_settings_id: str  # ID of the AWS settings
    invocation_json: str  # JSON string for invoking the runtime


class AssistantRequest(BaseModel):
    """
    Model for creating or updating an assistant.
    When updating an assistant, only fields that are explicitly set in the request will be updated.
    Fields that use default values (not explicitly set) will not override existing values.
    """

    name: str
    description: Optional[str] = None
    system_prompt: Optional[str] = None

    project: str = DEMO_PROJECT
    context: list[Context] = Field(default_factory=list)
    icon_url: Optional[str] = None
    llm_model_type: Optional[str] = None
    toolkits: list[ToolKitDetails] = Field(default_factory=list)
    conversation_starters: list[str] = Field(default_factory=list)
    shared: bool = True
    is_react: bool = True
    is_global: Optional[bool] = None
    agent_mode: Optional[AgentMode] = AgentMode.GENERAL
    plan_prompt: Optional[str] = None
    slug: Optional[str] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    smart_tool_selection_enabled: Optional[bool] = False
    mcp_servers: list[MCPServerDetails] = Field(default_factory=list)
    assistant_ids: list[str] = Field(default_factory=list)
    skill_ids: list[str] = Field(default_factory=list)
    bedrock: Optional[BedrockAgentData] = None
    bedrock_agentcore_runtime: Optional[BedrockAgentcoreRuntimeData] = None
    type: AssistantType = Field(default=AssistantType.CODEMIE)
    agent_card: Optional[AgentCard] = Field(default=None)
    categories: list[str] = Field(default_factory=list, max_length=3)
    prompt_variables: Optional[list[PromptVariable]] = Field(
        default_factory=list, description="Optional list of variables that can be used in the system prompt"
    )
    custom_metadata: Optional[dict[str, Any]] = None

    # Add guardrail assignments (NOT stored on Assistant model)
    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None

    # Integration validation flag
    skip_integration_validation: Optional[bool] = Field(
        default=False,
        description="Skip validation of toolkit credentials. Set to True to bypass credential checks.",
    )

    @model_validator(mode='before')
    @classmethod
    def normalize_input_data(cls, data: Any) -> Any:
        """
        Convert category objects to category IDs if they are provided as objects.
        This provides backward compatibility for clients sending full category objects.
        Also sanitizes string fields by removing NULL bytes (0x00) which are not allowed in PostgreSQL.
        """
        if isinstance(data, dict):
            # Remove NULL bytes from string fields to prevent PostgreSQL errors
            string_fields = ['name', 'description', 'system_prompt', 'plan_prompt', 'slug']
            for field in string_fields:
                if field in data and isinstance(data[field], str):
                    data[field] = data[field].replace('\x00', '')

            # Convert category objects to IDs (backward compatibility)
            if 'categories' in data:
                categories = data['categories']
                is_category_object = (
                    categories
                    and isinstance(categories, list)
                    and isinstance(categories[0], dict)
                    and 'id' in categories[0]
                )
                if is_category_object:
                    data['categories'] = [cat['id'] for cat in categories if isinstance(cat, dict) and 'id' in cat]
        return data

    @model_validator(mode='after')
    def validate_and_set_assistant_type(self) -> Self:
        # If agent_card is provided, automatically set type to A2A
        if self.agent_card is not None:
            self.type = AssistantType.A2A
            self.system_prompt = ""
            self.description = self.agent_card.description

        elif self.bedrock and self.bedrock.bedrock_agent_id:
            self.type = AssistantType.BEDROCK_AGENT

        elif self.bedrock_agentcore_runtime and self.bedrock_agentcore_runtime.runtime_endpoint_id:
            self.type = AssistantType.BEDROCK_AGENTCORE_RUNTIME

        # Validate Codemie type requirements
        elif self.type == AssistantType.CODEMIE:
            if self.system_prompt is None:
                raise ValueError("system_prompt is required when type is Codemie")
            if self.llm_model_type is None:
                raise ValueError("llm_model_type is required when type is Codemie")
        return self


class MCPServerCheckRequest(BaseModel):
    mcp_server: MCPServerDetails


class InlineCredential(BaseModel):
    """Model representing an inline credential found in an assistant"""

    toolkit: Optional[str] = None
    tool: Optional[str] = None
    label: Optional[str] = None
    mcp_server: Optional[str] = None
    credential_type: str
    env_vars: Optional[List[str]] = Field(None, description="List of environment variable names")
    sub_assistant_name: Optional[str] = Field(
        None, description="Name of the sub-assistant this credential belongs to (if applicable)"
    )
    sub_assistant_id: Optional[str] = Field(
        None, description="ID of the sub-assistant this credential belongs to (if applicable)"
    )


class SubAssistantPublishSettings(BaseModel):
    """Settings for a sub-assistant when publishing to marketplace"""

    assistant_id: str
    is_global: bool = Field(
        default=True,
        description="Whether this sub-assistant should be visible in the marketplace. "
        "If False, the sub-assistant will be kept private but still usable by the orchestrator.",
    )
    toolkits: Optional[List[ToolKitDetails]] = None
    mcp_servers: Optional[List[MCPServerDetails]] = None
    categories: Optional[List[str]] = Field(default_factory=list, max_length=3)


class QualityValidationResult(BaseModel):
    """Model representing the quality validation result from LLM"""

    decision: str = Field(..., description="Validation decision: 'accept' or 'reject'")
    recommendations: Optional[RefineGeneratorResponse] = Field(
        default=None,
        description="Field-level recommendations for improvement (populated only when decision is 'reject')",
    )


class PublishValidationResponse(BaseModel):
    """Response model for assistant publish validation"""

    requires_confirmation: bool
    message: str
    inline_credentials: list[InlineCredential]
    assistant_id: str
    sub_assistants: Optional[List[Dict[str, Any]]] = Field(
        default_factory=list, description="List of sub-assistants that will also be published"
    )
    prompt_variables: Optional[List[PromptVariable]] = Field(
        default_factory=list, description="List of prompt variables defined in the assistant"
    )


class MissingIntegration(BaseModel):
    """Model representing a single missing tool credential"""

    toolkit: str = Field(..., description="Toolkit name (e.g., 'Data Management')")
    tool: str = Field(..., description="Tool name (e.g., 'sql')")
    label: str = Field(..., description="Display label for the tool (e.g., 'SQL')")
    credential_type: Optional[str] = Field(None, description="Credential type required (e.g., 'AWS', 'Jira')")
    settings_config_level: SettingsConfigLevel = Field(
        default=SettingsConfigLevel.TOOLKIT,
        description="Level at which settings should be configured ('tool' or 'toolkit')",
    )


class MissingIntegrationsByCredentialType(BaseModel):
    """Model representing missing tools grouped by credential type"""

    credential_type: str = Field(..., description="Credential type (e.g., 'AWS', 'Jira', 'Confluence')")
    missing_tools: List[MissingIntegration] = Field(
        ..., description="List of missing tools requiring this credential type"
    )
    # Optional sub-assistant context
    assistant_id: Optional[str] = Field(None, description="Sub-assistant ID (if from sub-assistant)")
    assistant_name: Optional[str] = Field(None, description="Sub-assistant name (if from sub-assistant)")
    icon_url: Optional[str] = Field(None, description="Sub-assistant icon URL (if from sub-assistant)")


class IntegrationValidationResult(BaseModel):
    """Complete validation result for assistant integrations"""

    has_missing_integrations: bool = Field(..., description="Whether any integrations are missing")
    missing_by_credential_type: List[MissingIntegrationsByCredentialType] = Field(
        default_factory=list, description="Missing tools in main assistant grouped by credential type"
    )
    sub_assistants_missing: List[MissingIntegrationsByCredentialType] = Field(
        default_factory=list, description="Missing tools in sub-assistants grouped by credential type"
    )
    message: Optional[str] = Field(None, description="User-friendly message about missing integrations")


class AssistantCreateResponse(BaseResponse):
    """Response model for assistant creation with validation"""

    assistant_id: Optional[str] = Field(
        None, alias="assistantId", description="Created assistant ID (None if validation failed)"
    )
    validation: Optional[IntegrationValidationResult] = Field(
        None, description="Validation result (populated if validation found missing integrations)"
    )


class AssistantUpdateResponse(BaseResponse):
    """Response model for assistant update with validation"""

    validation: Optional[IntegrationValidationResult] = Field(
        None, description="Validation result (populated if validation found missing integrations)"
    )


class PublishValidationErrorResponse(BaseModel):
    requires_confirmation: bool = True
    assistant_id: str
    quality_validation: Optional[QualityValidationResult] = Field(
        default=None, description="Quality validation result from LLM analysis (if applicable)"
    )


class AssistantListResponse(BaseModel):
    id: str
    name: str
    slug: Optional[str]
    type: AssistantType
    description: str
    icon_url: Optional[str] = None
    created_by: Optional[CreatedByUser] = None
    user_abilities: Optional[list] = None
    unique_users_count: Optional[int] = None
    unique_likes_count: Optional[int] = None
    unique_dislikes_count: Optional[int] = None
    categories: Optional[list[str]] = None
    is_global: Optional[bool] = False
    shared: Optional[bool] = False
    origin: Optional[AssistantOrigin] = AssistantOrigin.UNKNOWN


class NestedAssistantResponse(BaseModel):
    """Response model for nested assistants (subassistants) with full configuration details.

    This model includes toolkits and MCP servers so that users can configure their
    integration mappings for subassistants without making additional API calls.
    """

    id: str
    name: str
    slug: Optional[str]
    type: AssistantType
    description: str
    icon_url: Optional[str] = None
    created_by: Optional[CreatedByUser] = None
    user_abilities: Optional[list] = None
    is_global: Optional[bool] = False
    shared: Optional[bool] = False
    categories: Optional[list[str]] = None
    project: str
    llm_model_type: Optional[str] = None
    toolkits: list[ToolKitDetails] = Field(default_factory=list)
    mcp_servers: list[MCPServerDetails] = Field(default_factory=list)
    conversation_starters: list[str] = Field(default_factory=list)
    assistant_ids: list[str] = Field(default_factory=list)
    temperature: Optional[float] = None
    top_p: Optional[float] = None


def get_current_time():
    return datetime.now().strftime(PROMPT_DATE_FORMAT)


class SystemPromptHistory(BaseModel):
    system_prompt: str
    date: datetime
    created_by: Optional[CreatedByUser] = None


class AssistantBase(CommonBaseModel, Owned):
    name: str
    description: str
    system_prompt: str
    system_prompt_history: list[SystemPromptHistory] = SQLField(
        default_factory=list, sa_column=Column(PydanticListType(SystemPromptHistory))
    )
    created_by: Optional[CreatedByUser] = SQLField(default=None, sa_column=Column(PydanticType(CreatedByUser)))
    project: str = SQLField(default=DEMO_PROJECT, index=True)
    icon_url: Optional[str] = SQLField(default=None, index=True)
    llm_model_type: Optional[str] = None
    toolkits: list[ToolKitDetails] = SQLField(default_factory=list, sa_column=Column(PydanticListType(ToolKitDetails)))
    conversation_starters: list[str] = SQLField(default_factory=list, sa_column=Column(JSONB))
    shared: bool = SQLField(default=True, index=True)
    # IMPORTANT: is_react is deprecated and will be removed in future versions. DO NOT USE IT.
    is_react: bool = True
    is_global: Optional[bool] = SQLField(default=False, index=True)
    created_date: datetime = SQLField(default_factory=lambda: datetime.now(UTC))
    updated_date: Optional[datetime] = None
    agent_mode: Optional[AgentMode] = AgentMode.GENERAL
    plan_prompt: Optional[str] = None
    creator: str = SQLField(default="system", index=True)
    slug: Optional[str] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    smart_tool_selection_enabled: Optional[bool] = False
    context: list[Context] = SQLField(default_factory=list, sa_column=Column(PydanticListType(Context)))
    user_abilities: Optional[list[Action]] = SQLField(default=None, sa_column=Column(JSONB))
    mcp_servers: list[MCPServerDetails] = SQLField(
        default_factory=list, sa_column=Column(PydanticListType(MCPServerDetails))
    )
    assistant_ids: list[str] = SQLField(default_factory=list, sa_column=Column(JSONB))
    skill_ids: Optional[list[str]] = SQLField(default_factory=list, sa_column=Column(JSONB))
    nested_assistants: list[NestedAssistantResponse] = SQLField(
        default_factory=list, sa_column=Column(PydanticListType(NestedAssistantResponse))
    )
    bedrock: Optional[BedrockAgentData] = SQLField(default=None, sa_column=Column(PydanticType(BedrockAgentData)))
    bedrock_agentcore_runtime: Optional[BedrockAgentcoreRuntimeData] = SQLField(
        default=None, sa_column=Column(PydanticType(BedrockAgentcoreRuntimeData))
    )
    # A2A specific fields
    type: AssistantType = SQLField(
        default=AssistantType.CODEMIE, sa_column_kwargs={"server_default": AssistantType.CODEMIE.name}
    )
    agent_card: Optional[AgentCard] = SQLField(None, sa_column=Column(PydanticType(AgentCard)))
    prompt_variables: list[PromptVariable] = SQLField(
        default_factory=list, sa_column=Column(PydanticListType(PromptVariable))
    )
    origin: Optional[AssistantOrigin] = SQLField(
        default=AssistantOrigin.UNKNOWN, sa_column_kwargs={"server_default": AssistantOrigin.UNKNOWN.name}
    )
    unique_users_count: Optional[int] = SQLField(default=0, index=False)
    unique_likes_count: Optional[int] = SQLField(default=0, index=False)
    unique_dislikes_count: Optional[int] = SQLField(default=0, index=False)
    categories: list[str] = SQLField(default_factory=list, sa_column=Column(JSONB))
    custom_metadata: Optional[dict[str, Any]] = SQLField(default=None, sa_column=Column(JSONB))

    # Custom PostgreSQL indexes
    __table_args__ = (
        Index('ix_assistants_context', 'context', postgresql_using="gin"),
        Index('ix_assistants_created_by_id', text("(created_by->>'id')")),
        Index('ix_assistants_update_date', 'update_date'),
        Index('ix_assistants_created_by_name', text("(created_by->>'name') gin_trgm_ops"), postgresql_using='gin'),
        Index('ix_assistants_name', "name", postgresql_using='gin', postgresql_ops={"name": "gin_trgm_ops"}),
        Index(
            'ix_assistants_description',
            "description",
            postgresql_using='gin',
            postgresql_ops={"description": "gin_trgm_ops"},
        ),
        Index('ix_assistants_id', "id", postgresql_using='gin', postgresql_ops={"id": "gin_trgm_ops"}),
        Index('ix_assistants_categories', 'categories', postgresql_using='gin'),
        Index('ix_assistants_skill_ids', 'skill_ids', postgresql_using='gin'),
        # Bedrock Agent indexes
        Index(
            'ix_assistants_bedrock_aws_settings_id',
            text("(bedrock->>'bedrock_aws_settings_id')"),
            postgresql_using='btree',
        ),
        Index(
            'uq_assistants_bedrock_settings_agent_alias_unique',
            text("(bedrock->>'bedrock_aws_settings_id')"),
            text("(bedrock->>'bedrock_agent_id')"),
            text("(bedrock->>'bedrock_agent_alias_id')"),
            unique=True,
            postgresql_where=text(
                "(bedrock->>'bedrock_aws_settings_id') IS NOT NULL AND "
                "(bedrock->>'bedrock_agent_id') IS NOT NULL AND "
                "(bedrock->>'bedrock_agent_alias_id') IS NOT NULL"
            ),
        ),
        # Bedrock AgentCore Runtime indexes
        Index(
            'ix_assistants_bedrock_runtime_aws_settings_id',
            text("(bedrock_agentcore_runtime->>'aws_settings_id')"),
            postgresql_using='btree',
        ),
        Index(
            'uq_assistants_bedrock_runtime_settings_endpoint_unique',
            text("(bedrock_agentcore_runtime->>'aws_settings_id')"),
            text("(bedrock_agentcore_runtime->>'runtime_id')"),
            text("(bedrock_agentcore_runtime->>'runtime_endpoint_id')"),
            unique=True,
            postgresql_where=text(
                "(bedrock_agentcore_runtime->>'aws_settings_id') IS NOT NULL AND "
                "(bedrock_agentcore_runtime->>'runtime_id') IS NOT NULL AND "
                "(bedrock_agentcore_runtime->>'runtime_endpoint_id') IS NOT NULL"
            ),
        ),
    )

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

    @computed_field(return_type=Optional[int])
    @property
    def version(self) -> Optional[int]:
        """Transient version field - not stored in database"""
        return getattr(self, '_version', None)

    @version.setter
    def version(self, value: Optional[int]):
        self._version = value

    def model_post_init(self, __context):
        """Initialize transient fields after model creation"""
        super().model_post_init(__context)
        # Initialize _version as instance attribute if not already set
        if not hasattr(self, '_version'):
            object.__setattr__(self, '_version', None)

    @field_serializer("categories")
    def serialize_categories(self, categories_list):
        """Filter out invalid category IDs when serializing the model for API responses."""
        from codemie.service.assistant.category_service import category_service

        # Check if we have enriched categories set (from API enrichment)
        if hasattr(self, '_enriched_categories'):
            return getattr(self, '_enriched_categories')

        if not categories_list:
            return categories_list

        return category_service.filter_valid_category_ids(categories_list)

    @classmethod
    def from_yaml(cls, yaml_str: str, project: str = DEMO_PROJECT):
        """Instantiate an Assistant object from a YAML string"""
        try:
            yaml_dict = yaml.safe_load(yaml_str)
            return cls(**yaml_dict, project=project)
        except yaml.YAMLError as e:
            return f"Error parsing YAML workflow. Yaml: {yaml_str} {e}"

    def is_owned_by(self, user: User):
        if not self.created_by:
            return self.creator == user.id
        return self.created_by.id == user.id

    def is_managed_by(self, user: User):
        return self.project in user.admin_project_names

    def is_shared_with(self, user: User):
        if self.is_global:
            # For marketplace (global) assistants, check if external users have project access
            if user.is_external_user:
                from codemie.configs import config

                allowed_projects = list(set(config.EXTERNAL_USER_ALLOWED_PROJECTS + user.project_names))
                return self.project in allowed_projects
            # Internal users can access all marketplace assistants
            return True
        return self.project in user.project_names and self.shared

    def get_deleted_context(self) -> list[str]:
        index_entities = IndexInfo.filter_by_projects(projects_names=[self.project])
        index_parts = {(index.repo_name, Context.index_info_type(index)) for index in index_entities}
        return list({ctx.name for ctx in self.context if (ctx.name, ctx.context_type) not in index_parts})

    @staticmethod
    def _should_update_field(field: str, field_value: Any, fields_set: set | None) -> bool:
        """Check if a field should be updated based on whether it was explicitly set."""
        return fields_set is None or field in fields_set or field_value is None

    @staticmethod
    def _get_field_value_for_update(field: str, field_value: Any) -> Any:
        """Get the appropriate value for a field update, handling special cases."""
        if field in ('prompt_variables', 'categories') and field_value is None:
            return []
        return field_value

    def _should_update_system_prompt(self, request: AssistantRequest, fields_set: set | None) -> bool:
        """Determine if system_prompt should be updated."""
        if self.system_prompt == request.system_prompt:
            return False
        return fields_set is None or 'system_prompt' in fields_set

    def _map_assistant_request(self, request: AssistantRequest):
        """
        Maps values from an AssistantRequest to this assistant instance.
        Only updates fields that were explicitly set in the request.
        Args:
            request: The AssistantRequest containing the new values
        """
        # Required fields are always updated
        self.name = request.name
        self.updated_date = datetime.now(UTC)

        # Type-safe field mapping using reflection
        fields_set = getattr(request, '__fields_set__', None)
        request_fields = request.model_fields

        # Get all updatable fields except special cases that need custom handling
        updatable_fields = [
            field
            for field in request_fields
            if field not in ('name', 'system_prompt', 'guardrail_assignments', 'skip_integration_validation')  # type: ignore[union-attr]
        ]

        # Update fields based on whether they were explicitly set or we're in legacy mode
        for field in updatable_fields:
            field_value = getattr(request, field)
            if self._should_update_field(field, field_value, fields_set):
                value_to_set = self._get_field_value_for_update(field, field_value)
                setattr(self, field, value_to_set)

        # Special handling for system_prompt
        # Note: system_prompt_history is now populated from AssistantConfiguration versions
        if self._should_update_system_prompt(request, fields_set):
            self.system_prompt = request.system_prompt

    def validate_fields(self) -> str:
        slug_error = self._check_slug_uniqueness()
        if slug_error:
            return slug_error

        categories_error = self._check_categories()
        if categories_error:
            return categories_error

        assistant_ids_error = self._validate_assistant_ids()
        if assistant_ids_error:
            return assistant_ids_error

        prompt_variables_error = self._validate_prompt_variables()
        if prompt_variables_error:
            return prompt_variables_error

        return ""

    def _check_slug_uniqueness(self) -> Optional[str]:
        if self.slug:
            assistant_by_slug = self.get_by_fields({"slug.keyword": self.slug})
            if assistant_by_slug and assistant_by_slug.id != self.id:
                return 'Slug should be unique'
            return ""
        return None

    def _check_categories(self) -> Optional[str]:
        from codemie.service.assistant.category_service import category_service

        if not self.categories:
            return None
        try:
            category_service.validate_category_ids(self.categories)
        except Exception as e:
            return str(e)

        return ""

    def _validate_prompt_variables(self) -> Optional[str]:
        """Validate that prompt variables have unique keys"""
        if not self.prompt_variables:
            return None

        keys = [var.key for var in self.prompt_variables]
        duplicate_keys = {key for key in keys if keys.count(key) > 1}

        if duplicate_keys:
            return f'Duplicate prompt variable keys detected: {", ".join(duplicate_keys)}'

        return None

    def _validate_assistant_ids(self) -> Optional[str]:
        """
        Validate sub-assistant IDs for this assistant.

        Returns:
            None if validation passes, error message string if validation fails

        Validation rules:
            - Admins bypass all validation
            - Marketplace assistants (is_global=True) can always be used
            - Private assistants require project match
            - No circular references allowed
            - No nested assistants allowed (sub-assistants cannot have their own sub-assistants)

        Optimization:
            - Only validates newly added assistant IDs (not previously validated ones)
            - Skip validation if no new assistants were added
        """
        if not self.assistant_ids:
            return None

        # Get only the newly added assistant IDs that need validation
        changed_assistant_ids = self._get_changed_assistant_ids()

        # Skip validation if no new IDs were added (only removals or no changes)
        if not changed_assistant_ids:
            return None

        from codemie.rest_api.security.user_context import get_current_user

        user = get_current_user()
        if not user:
            return "User context is required for assistant validation"

        from codemie.service.assistant.sub_assistant_validator import SubAssistantValidator

        # Validate only newly added assistants
        return SubAssistantValidator().validate_sub_assistants(
            parent=self, sub_assistant_ids=list(changed_assistant_ids), user=user
        )

    def _get_changed_assistant_ids(self) -> set[str]:
        """
        Get the diff of newly added assistant IDs by comparing with database value.

        Returns:
            Set of newly added assistant IDs that need validation.
            Returns all current IDs if this is a new object or if comparison fails (safe default).
        """
        # New object (no id) → validate all current IDs
        if not self.id:
            return set(self.assistant_ids or [])

        try:
            # Create a fresh session and query database value
            with Session(self.get_engine()) as session:
                # Query only the assistant_ids field from the database
                statement = select(Assistant.assistant_ids).where(Assistant.id == self.id)
                db_assistant_ids = session.exec(statement).first()

                # If not found in DB, validate all current IDs (new object being saved)
                if db_assistant_ids is None:
                    return set(self.assistant_ids or [])

                # Get current and DB IDs as sets
                current_ids = set(self.assistant_ids or [])
                db_ids = set(db_assistant_ids or [])

                # Return only newly added IDs (diff: current - db)
                changed_assistant_ids = current_ids - db_ids
                return changed_assistant_ids

        except Exception:
            # If we can't determine (DB error, etc.), validate all current IDs
            # This is the safe default to ensure validation runs when needed
            return set(self.assistant_ids or [])


class Assistant(BaseModelWithSQLSupport, AssistantBase, table=True):
    """SQLModel version of Assistant for PostgreSQL storage"""

    __tablename__ = "assistants"

    # Version tracking field
    version_count: int = SQLField(default=1, index=True, sa_column_kwargs={"server_default": "1"})

    def update_assistant(self, request: AssistantRequest, user: User, change_notes: Optional[str] = None):
        """
        Update assistant and create a new version.

        NOTE: This method now creates a version record using AssistantVersionService.
        Import is done inside method to avoid circular dependency.
        """
        # Import here to avoid circular dependency
        from codemie.service.assistant.assistant_version_service import AssistantVersionService
        from codemie.service.assistant.assistant_version_compare_service import AssistantVersionCompareService

        # Update master record fields (only non-versioned fields like name, slug, etc.)
        self._map_assistant_request(request)

        # Track MCP usage changes
        old_mcp_servers = self.mcp_servers.copy() if self.mcp_servers else []
        self._track_mcp_usage_changes(old_mcp_servers, self.mcp_servers)

        self.update()

        if AssistantVersionCompareService.has_configuration_changes(self.id, request):
            # Create new version with configuration changes
            AssistantVersionService.create_new_version(
                assistant=self, request=request, user=user, change_notes=change_notes
            )

    @staticmethod
    def _track_mcp_usage_changes(old_servers: list, new_servers: list):
        """
        Track MCP config usage count changes during assistant update.

        Args:
            old_servers: Previous list of MCPServerDetails
            new_servers: Updated list of MCPServerDetails
        """
        # Extract config IDs from old and new servers
        old_ids = {
            s.mcp_config_id
            for s in (old_servers or [])
            if hasattr(s, 'mcp_config_id') and s.mcp_config_id and s.enabled
        }
        new_ids = {
            s.mcp_config_id
            for s in (new_servers or [])
            if hasattr(s, 'mcp_config_id') and s.mcp_config_id and s.enabled
        }

        # Increment for newly added configs
        for config_id in new_ids - old_ids:
            try:
                MCPConfigService.increment_usage(config_id)
                logger.debug(f"Incremented usage for added MCP config: {config_id}")
            except Exception as e:
                logger.warning(f"Failed to increment usage for {config_id}: {e}")

        # Decrement for removed configs
        for config_id in old_ids - new_ids:
            try:
                MCPConfigService.decrement_usage(config_id)
                logger.debug(f"Decremented usage for removed MCP config: {config_id}")
            except Exception as e:
                logger.warning(f"Failed to decrement usage for {config_id}: {e}")

    @classmethod
    def delete_assistant(cls, assistant_id: str):
        from codemie.service.settings.scheduler_settings_service import SchedulerSettingsService
        from codemie_tools.base.models import CredentialTypes

        assistant = cls.find_by_id(assistant_id)
        if assistant:
            assistant_project = assistant.project
            assistant_internal_id = str(assistant.id)

            for credential_type in (CredentialTypes.SCHEDULER, CredentialTypes.WEBHOOK):
                try:
                    deleted = SchedulerSettingsService.delete_integrations_by_resource(
                        resource_id=assistant_internal_id,
                        project_name=assistant_project,
                        credential_type=credential_type,
                    )
                    if deleted:
                        logger.info(
                            f"Deleted {deleted} {credential_type.value} integration(s) for assistant {assistant_id}"
                        )
                except Exception as e:
                    logger.warning(
                        f"Failed to delete {credential_type.value} integrations for assistant {assistant_id}: {e}"
                    )

            assistant.delete()
            GuardrailService.remove_guardrail_assignments_for_entity(GuardrailEntity.ASSISTANT, assistant_internal_id)
            return
        return {"status": "not found"}

    # noinspection PyMethodOverriding
    @classmethod
    def get_by_ids(cls, user: User, ids: list[str], parent_assistant: Optional['Assistant'] = None):  # type: ignore[override]
        """
        Get assistants by IDs with proper permission filtering.
        Override base method to add user-based authorization filtering.

        Args:
            user: The user requesting access
            ids: List of assistant IDs to retrieve
            parent_assistant: Optional parent assistant. If provided and is_global=True,
                             subassistants will be accessible even if they're not published

        Returns:
            List of assistants the user has READ access to
        """
        with Session(cls.get_engine()) as session:
            statement = select(cls).where(cls.id.in_(ids))  # type: ignore[attr-defined]
            statement = statement.order_by(cls.update_date.desc())  # type: ignore[attr-defined]
            entries = session.exec(statement).all()

        # If parent assistant is global (published to marketplace), allow access to all its subassistants
        if parent_assistant and parent_assistant.is_global:
            return list(entries)

        return [entry for entry in entries if Ability(user).can(Action.READ, entry)]

    @classmethod
    def get_by_ids_no_permission_check(cls, ids: List[str]) -> List['Assistant']:
        return super().get_by_ids(ids)  # type: ignore

    @classmethod
    def get_by_icon_url(cls, icon_url: str):
        with Session(cls.get_engine()) as session:
            statement = select(cls).where(cls.icon_url == icon_url)
            return session.exec(statement).all()

    @classmethod
    def get_by_bedrock_aws_settings_id(cls, bedrock_aws_settings_id: str):
        """
        Retrieve all assistants filtered by bedrock_aws_settings_id.
        """
        with Session(cls.get_engine()) as session:
            statement = select(cls).where(
                cls.bedrock["bedrock_aws_settings_id"].astext == bedrock_aws_settings_id  # type: ignore
            )
            return session.exec(statement).all()

    @classmethod
    def get_by_bedrock_runtime_aws_settings_id(cls, aws_settings_id: str):
        """
        Retrieve all assistants filtered by bedrock_agentcore_runtime aws setting id.
        """
        with Session(cls.get_engine()) as session:
            statement = select(cls).where(
                cls.bedrock_agentcore_runtime["aws_settings_id"].astext == aws_settings_id  # type: ignore
            )
            return session.exec(statement).all()

    @classmethod
    def by_datasource_run(cls, datasource: IndexInfo):
        with Session(cls.get_engine()) as session:
            statement = select(cls).where(
                and_(
                    cls.project == datasource.project_name,
                    cls.context.cast(JSONB).contains(  # type: ignore[attr-defined]
                        [{'name': datasource.repo_name, 'context_type': Context.index_info_type(datasource)}]
                    ),
                )
            )
            entries = session.exec(statement).all()
        return [AssistantListResponse(**entry.model_dump()) for entry in entries]


# ============================================================================
# Assistant Versioning Models
# ============================================================================


class AssistantConfiguration(BaseModelWithSQLSupport, table=True):
    """
    Version configuration record for an assistant.

    Each record represents a snapshot of the assistant's configuration
    at a specific point in time. Configurations are immutable once created.
    """

    __tablename__ = "assistant_configurations"

    # Primary identification
    id: Optional[str] = SQLField(default=None, primary_key=True)
    assistant_id: str = SQLField(foreign_key="assistants.id", index=True, ondelete="CASCADE")
    version_number: int = SQLField(index=True)

    # Timestamps and attribution
    created_date: datetime = SQLField(default_factory=lambda: datetime.now(UTC))
    created_by: Optional[CreatedByUser] = SQLField(default=None, sa_column=Column(PydanticType(CreatedByUser)))

    # Versioned configuration fields
    description: str
    system_prompt: str
    llm_model_type: Optional[str] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None

    # Complex configuration (JSONB)
    context: list[Context] = SQLField(default_factory=list, sa_column=Column(PydanticListType(Context)))
    toolkits: list[ToolKitDetails] = SQLField(default_factory=list, sa_column=Column(PydanticListType(ToolKitDetails)))
    mcp_servers: list[MCPServerDetails] = SQLField(
        default_factory=list, sa_column=Column(PydanticListType(MCPServerDetails))
    )
    assistant_ids: list[str] = SQLField(default_factory=list, sa_column=Column(JSONB))
    conversation_starters: list[str] = SQLField(default_factory=list, sa_column=Column(JSONB))

    # Optional fields
    bedrock: Optional[BedrockAgentData] = SQLField(default=None, sa_column=Column(PydanticType(BedrockAgentData)))
    bedrock_agentcore_runtime: Optional[BedrockAgentcoreRuntimeData] = SQLField(
        default=None, sa_column=Column(PydanticType(BedrockAgentcoreRuntimeData))
    )
    agent_card: Optional[AgentCard] = SQLField(default=None, sa_column=Column(PydanticType(AgentCard)))
    custom_metadata: Optional[dict[str, Any]] = SQLField(default=None, sa_column=Column(JSONB))

    # Change tracking
    change_notes: Optional[str] = None

    # Indexes
    __table_args__ = (
        Index(
            'ix_assistant_configs_assistant_version',
            'assistant_id',
            'version_number',
            unique=True,
            postgresql_ops={'version_number': 'DESC'},
        ),
        Index('ix_assistant_configs_created_date', 'created_date', postgresql_ops={'created_date': 'DESC'}),
        Index('ix_assistant_configs_created_by_id', text("(created_by->>'id')")),
    )

    @classmethod
    def get_by_assistant_and_version(cls, assistant_id: str, version_number: int) -> Optional['AssistantConfiguration']:
        """Get specific version of assistant configuration"""
        with Session(cls.get_engine()) as session:
            statement = select(cls).where(and_(cls.assistant_id == assistant_id, cls.version_number == version_number))
            return session.exec(statement).first()

    @classmethod
    def get_current_version(cls, assistant_id: str) -> Optional['AssistantConfiguration']:
        """Get the latest version for an assistant"""
        with Session(cls.get_engine()) as session:
            statement = select(cls).where(cls.assistant_id == assistant_id).order_by(cls.version_number.desc())  # type: ignore[attr-defined]
            return session.exec(statement).first()

    @classmethod
    def get_version_history(
        cls, assistant_id: str, page: int = 0, per_page: int = 20
    ) -> list['AssistantConfiguration']:
        """Get version history for an assistant with pagination"""
        with Session(cls.get_engine()) as session:
            statement = (
                select(cls)
                .where(cls.assistant_id == assistant_id)
                .order_by(cls.version_number.desc())  # type: ignore[attr-defined]
                .offset(page * per_page)
                .limit(per_page)
            )
            return list(session.exec(statement).all())

    @classmethod
    def count_versions(cls, assistant_id: str) -> int:
        """Count total versions for an assistant"""
        from sqlmodel import func

        with Session(cls.get_engine()) as session:
            statement = select(func.count()).select_from(cls).where(cls.assistant_id == assistant_id)
            return session.exec(statement).one()

    @classmethod
    def get_latest_version_number(cls, assistant_id: str) -> int:
        """
        Get the latest version number for an assistant by querying the database.
        Returns 0 if no versions exist yet.

        Args:
            assistant_id: The assistant ID

        Returns:
            Latest version number, or 0 if no versions exist
        """
        from sqlmodel import func

        with Session(cls.get_engine()) as session:
            statement = select(func.max(cls.version_number)).where(cls.assistant_id == assistant_id)
            result = session.exec(statement).one()
            return result if result is not None else 0


class AssistantVersionHistoryResponse(BaseModel):
    """Response model for version history listing"""

    versions: list[AssistantConfiguration]
    total_versions: int
    assistant_name: str
    assistant_id: str


class AssistantVersionCompareResponse(BaseModel):
    """Response model for version comparison"""

    assistant_id: str
    version1: AssistantConfiguration
    version2: AssistantConfiguration
    differences: dict[str, Any]  # Output from deepdiff
    change_summary: str  # Human-readable summary


class AssistantRollbackRequest(BaseModel):
    """Request model for version rollback"""

    change_notes: Optional[str] = Field(default=None, description="Notes explaining why this rollback was performed")


class VirtualAssistant(AssistantBase):
    execution_id: Optional[str] = None


class VirtualIdeAssistant(AssistantBase):
    def save(self, refresh=False, validate=True) -> PostResponse:
        # NOOP, Virtual IDE assistant should not persist
        pass

    def delete(self):
        # NOOP, Virtual IDE assistant should not persist
        pass

    def update(self, refresh=False, validate=True):
        # NOOP, Virtual IDE assistant should not persist
        pass


class AssistantHealthCheckRequest(BaseModel):
    """Request model for assistant health check"""

    version: Optional[int] = Field(default=None, description="Optional version to test")


class AssistantHealthCheckError(BaseModel):
    """Error details for assistant health check"""

    message: str
    details: Optional[str] = None
    help: Optional[str] = None
    error_type: Optional[str] = None


class AssistantHealthCheckResponse(BaseModel):
    """Response model for assistant health check"""

    model_config = {"exclude_none": True}

    is_healthy: bool = Field(description="Whether the assistant is healthy and functional")
    assistant_id: str = Field(description="ID of the checked assistant")
    assistant_name: Optional[str] = Field(default=None, description="Name of the checked assistant")
    configuration_valid: bool = Field(description="Whether the assistant configuration is valid")
    execution_successful: bool = Field(
        default=False, description="Whether the assistant successfully executed and generated a response"
    )
    tools_available: Optional[set[str]] = Field(
        default=None, description="Set of tool names actually loaded in the agent"
    )
    tools_available_count: Optional[int] = Field(
        default=None, description="Number of tools actually loaded in the agent"
    )
    tools_misconfigured: Optional[set[str]] = Field(
        default=None, description="Set of expected tool names that failed to load in the agent"
    )
    tools_misconfigured_count: Optional[int] = Field(
        default=None, description="Number of misconfigured tools that failed to load"
    )
    error: Optional[AssistantHealthCheckError] = Field(default=None, description="Error details if health check failed")
