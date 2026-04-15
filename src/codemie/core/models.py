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

import os
import uuid
from abc import abstractmethod, ABC
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional, Any
from typing_extensions import Annotated


import requests
from codemie.service.llm_service.llm_service import llm_service
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import (
    BaseModel,
    Field,
    ConfigDict,
    field_validator,
    StringConstraints,
    PrivateAttr,
    model_validator,
    AfterValidator,
)
from pydantic.alias_generators import to_camel

from codemie.configs import config
from codemie.core.ability import Owned
from codemie.core.constants import CodeIndexType, ChatRole, BackgroundTaskStatus

if TYPE_CHECKING:
    from codemie.rest_api.security.user import User
from codemie.core.db_utils import escape_like_wildcards
from codemie.core.utils import get_url_domain
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.errors import AgentErrorDetails, ToolErrorDetails
from codemie.rest_api.models.base import (
    CommonBaseModel,
    BaseModelWithSQLSupport,
    PydanticListType,
)
from codemie.rest_api.models.standard import PostResponse
from sqlmodel import SQLModel, Field as SQLField, Column, CheckConstraint, Index, select, or_, case, Session, String

PORT_PATTERN = r"(?:\d{1,4}|[1-5]\d{4}|6[0-4]\d{3}|65[0-4]\d{2}|655[0-2]\d|6553[0-5])"
LINK_PATTERN = rf"^https?:\/\/[A-Za-z0-9][A-Za-z0-9\-\.]*[A-Za-z0-9](?:\.[A-Za-z]{{2,}}|\:{PORT_PATTERN})(?:\/.*)?$"
NAME_PATTERN = r"^[a-zA-Z0-9][\w-]*$"
BRANCH_PATTERN = r"^[a-zA-Z0-9][\w\./-]*$"


class UserEntity(BaseModel):
    user_id: str
    username: str
    name: Optional[str] = Field(default="")


class BackgroundTaskEntity(BaseModel):
    id: str
    task: str
    user: UserEntity
    final_output: str
    current_step: str
    status: BackgroundTaskStatus
    date: datetime
    update_date: datetime


class AssistantDetails(BaseModel):
    id: str
    name: str


class BackgroundTaskRequest(BaseModel):
    task: str
    user: UserEntity
    assistant: AssistantDetails
    status: BackgroundTaskStatus = Field(default=BackgroundTaskStatus.STARTED)


class ConfiguredModel(BaseModel, populate_by_name=True):
    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)


class AbstractElasticModel(ConfiguredModel, ABC):
    @abstractmethod
    def get_identifier(self) -> str:
        pass


class BaseGitRepo(CommonBaseModel):
    name: Annotated[str, StringConstraints(pattern=NAME_PATTERN, min_length=4, max_length=50, strict=True)] = SQLField(
        sa_type=String
    )
    description: Annotated[str, StringConstraints(min_length=1, max_length=500, strict=True)] = SQLField(sa_type=String)
    link: Annotated[str, StringConstraints(pattern=LINK_PATTERN, min_length=1, max_length=1000, strict=True)] = (
        SQLField(sa_type=String)
    )
    branch: Annotated[str, StringConstraints(pattern=BRANCH_PATTERN, min_length=1, max_length=1000, strict=True)] = (
        SQLField(sa_type=String)
    )
    files_filter: Optional[str] = SQLField(default="")
    index_type: CodeIndexType
    last_indexed_commit: Optional[str] = None
    embeddings_model: Optional[str] = None
    summarization_model: Optional[str] = SQLField(default=llm_service.default_llm_model)
    prompt: Optional[str] = None
    docs_generation: Optional[bool] = SQLField(default=False)
    project_space_visible: bool = SQLField(default=False)
    setting_id: Optional[str] = None
    original_storage: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel)

    @field_validator("link", mode="before")
    def trim_link(cls, link_value: str) -> str:
        return link_value.strip() if link_value else link_value


class AuthenticationType(Enum):
    """
    Enum representing different authentication types for API requests.

    This enum provides:
    1. A key (lowercase) for each authentication type for internal use
    2. A display value (proper case) for each authentication type for header formatting
    3. Case-insensitive comparison with string values

    Example usage:
        auth_type = AuthenticationType.BEARER
        header = f"{auth_type.display_value} {token}"  # "Bearer my-token"

        # Case-insensitive comparison is handled automatically
        if auth_type == "bearer":  # This works!
            print("Using bearer authentication")
    """

    AWS_SIGNATURE = ("aws_signature", "AWS4-HMAC-SHA256")
    BASIC = ("basic", "Basic")
    APIKEY = ("apikey", "ApiKey")
    BEARER = ("bearer", "Bearer")

    def __init__(self, key, display_value):
        self.key = key
        self.display_value = display_value

    def __eq__(self, other):
        """
        Enables case-insensitive comparison with string values.

        Args:
            other: The value to compare with

        Returns:
            bool: True if the values are equal (case-insensitive), False otherwise
        """
        if isinstance(other, str):
            return self.key.lower() == other.lower()
        return super().__eq__(other)

    @classmethod
    def from_string(cls, value: str) -> 'AuthenticationType':
        """
        Get the enum value from a string (case-insensitive).

        Args:
            value: The string value to convert

        Returns:
            AuthenticationType: The matching enum value

        Raises:
            ValueError: If the string doesn't match any enum value
        """
        if isinstance(value, cls):
            return value

        for auth_type in cls:
            if value and auth_type.key.lower() == value.lower():
                return auth_type
        raise ValueError(f"Unknown authentication type: {value}")


class CodeRepoType(str, Enum):
    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"
    AZURE_DEVOPS_REPOS = "azure_devops"
    UNKNOWN = "unknown"

    @classmethod
    def from_link(cls, link: str) -> "CodeRepoType":
        if not link:
            raise ValueError("Repository link cannot be empty. Example: https://github.com")

        link = link.lower()

        if any(identifier in link for identifier in config.GITHUB_IDENTIFIERS):
            return cls.GITHUB
        elif any(identifier in link for identifier in config.GITLAB_IDENTIFIERS):
            return cls.GITLAB
        elif any(identifier in link for identifier in config.BITBUCKET_IDENTIFIERS):
            return cls.BITBUCKET
        elif any(identifier in link for identifier in config.AZURE_DEVOPS_REPOS_IDENTIFIERS):
            return cls.AZURE_DEVOPS_REPOS
        else:
            return cls.UNKNOWN

    @classmethod
    def from_link_probing(cls, link: str, timeout=2.0) -> "CodeRepoType":
        base_url = get_url_domain(link)

        try:
            response = requests.head(base_url, verify=True, timeout=timeout)
        except Exception:
            return cls.UNKNOWN

        for key, value in response.headers.items():
            header = key.lower()
            if header == "x-github-request-id":
                return cls.GITHUB
            if header == "x-gitlab-meta":
                return cls.GITLAB
            if header == "x-vss-authorizationendpoint":
                return cls.AZURE_DEVOPS_REPOS
            if header == "server" and value == "AtlassianEdge":
                return cls.BITBUCKET

        return cls.UNKNOWN


class GitRepo(BaseModelWithSQLSupport, BaseGitRepo, table=True):
    __tablename__ = "repositories"
    _repo_type: Optional[str] = PrivateAttr(default=None)

    app_id: str = SQLField(index=True)

    def save(self, refresh=False, validate=True) -> PostResponse:
        self.id = self.id if self.id else self.get_identifier()
        return super().save(refresh=refresh, validate=validate)

    def get_identifier(self) -> str:
        return self.original_storage or self.identifier_from_fields(self.app_id, self.name, self.index_type)

    @staticmethod
    def identifier_from_fields(app_id: str, name: str, index_type: CodeIndexType):
        return sanitize_es_index_name(f"{app_id}-{name}-{index_type.value}")

    def get_type(self) -> str:
        """
        This method needs special handling of Pydantic private attributes because
        when GitRepo objects are loaded from database, Pydantic private storage (__pydantic_private__)
        is not initialized, causing attribute access errors
        """
        private_storage = getattr(self, '__pydantic_private__', None)
        if private_storage is None or '_repo_type' not in private_storage or self._repo_type is None:
            repo_type = CodeRepoType.from_link(self.link)
            if repo_type == CodeRepoType.UNKNOWN:
                repo_type = CodeRepoType.from_link_probing(self.link)
            self._repo_type = repo_type.value
        return self._repo_type

    def get_repo_local_file_path(self) -> str:
        app_folder = f"{config.REPOS_LOCAL_DIR}/{self.app_id}/{self.name}"
        if not os.path.exists(app_folder):
            os.makedirs(app_folder)
        return app_folder

    @classmethod
    def get_by_app_id(cls, app_id: str):
        return cls.get_all_by_fields({"app_id": app_id})


class ApplicationRequest(ConfiguredModel):
    name: str


class LLMRetirementPair(ConfiguredModel):
    """A single deprecated → replacement model name mapping."""

    deprecated_model: str
    replacement_model: str

    @field_validator("deprecated_model", "replacement_model", mode="before")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Model name must not be empty or whitespace")
        return v

    @model_validator(mode="after")
    def not_same(self) -> "LLMRetirementPair":
        if self.deprecated_model == self.replacement_model:
            raise ValueError("deprecated_model and replacement_model must be different")
        return self


class LLMRetirementRequest(LLMRetirementPair):
    check_models_existence: bool = True


class LLMBulkRetirementRequest(ConfiguredModel):
    retirements: list[LLMRetirementPair] = Field(..., min_length=1, max_length=100)
    check_models_existence: bool = True


class Application(BaseModelWithSQLSupport, Owned, table=True):
    __tablename__ = "applications"

    class ProjectType(str, Enum):
        PERSONAL = "personal"
        SHARED = "shared"

    name: str
    description: Optional[str] = SQLField(default=None, max_length=500)  # Project description
    git_repos: list[GitRepo] = SQLField(default_factory=list, sa_column=Column(PydanticListType(GitRepo)))
    project_type: str = SQLField(default=ProjectType.SHARED)
    created_by: Optional[str] = SQLField(default=None, max_length=255)  # User ID of creator (widened for non-UUID IDPs)
    cost_center_id: Optional[uuid.UUID] = SQLField(default=None, foreign_key="cost_centers.id", index=True)
    deleted_at: Optional[datetime] = SQLField(default=None)  # Soft-delete timestamp for project lifecycle

    # Custom PostgreSQL indexes
    # ix_applications_name: GIN trigram index for ILIKE search performance
    # ix_applications_name_lower: UNIQUE functional index on LOWER(name)
    # for case-insensitive uniqueness (created by migration)
    __table_args__ = (
        Index("ix_applications_name", "name", postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"}),
        CheckConstraint("project_type IN ('personal', 'shared')", name="ck_applications_project_type"),
    )

    def save(self, refresh=False, validate=True) -> PostResponse:
        self.id = self.name
        return super().save(refresh=refresh, validate=validate)

    @classmethod
    def search_by_name(cls, name_query: Optional[str] = None, limit: Optional[int] = None):
        size = limit if limit is not None else 10000

        if name_query:
            # Security: Escape LIKE wildcards to prevent information leakage (Story 2, NFR-3.1)
            escaped_query = escape_like_wildcards(name_query)
            stmt = (
                select(cls)
                .where(or_(cls.name == name_query, cls.name.ilike(f"%{escaped_query}%", escape="\\")))
                .order_by(case((cls.name == name_query, 1), else_=2))
            )
        else:
            stmt = select(cls)

        stmt = stmt.limit(size)

        with Session(cls.get_engine()) as session:
            return session.exec(stmt).all()

    def is_owned_by(self, user: "User") -> bool:
        return self.created_by is not None and self.created_by == user.id

    def is_managed_by(self, user: "User") -> bool:
        return user.is_admin_or_maintainer or self.name in user.admin_project_names

    def is_shared_with(self, user: "User") -> bool:
        if user.is_admin_or_maintainer:
            return True
        if self.project_type == self.ProjectType.PERSONAL:
            return False
        return self.name in user.project_names


class CostCenter(SQLModel, table=True):
    __tablename__ = "cost_centers"

    id: uuid.UUID = SQLField(default_factory=uuid.uuid4, primary_key=True)
    name: str = SQLField(nullable=False, max_length=255)
    description: Optional[str] = SQLField(default=None, max_length=500)
    created_by: str = SQLField(nullable=False, max_length=255)
    date: datetime = SQLField(nullable=False)
    update_date: datetime = SQLField(nullable=False)
    deleted_at: Optional[datetime] = SQLField(default=None, index=True)


_ES_INDEX_INVALID_CHARS = ['"', ' ', '\\', '/', ',', '|', '>', '?', '*', '<', ':', '#']


def sanitize_es_index_name(name: str) -> str:
    """Sanitize a string to be a valid Elasticsearch index name.

    Elasticsearch requires index names to be lowercase and not contain
    specific special characters.
    """
    identifier = name.lower()
    for char in _ES_INDEX_INVALID_CHARS:
        if char in identifier:
            identifier = identifier.replace(char, "_")
    return identifier


class BaseKnowledgeBase(ConfiguredModel):
    name: str


class KnowledgeBase(BaseKnowledgeBase, AbstractElasticModel):
    name: str
    type: str

    def get_identifier(self) -> str:
        return sanitize_es_index_name(self.name)


class ChatMessage(ConfiguredModel):
    role: ChatRole
    message: Optional[str] = Field(default="")

    def convert_to_langchain_message(self):
        msg = HumanMessage(content=self.message) if self.role == ChatRole.USER else AIMessage(content=self.message)

        return msg


class CodeFields(ConfiguredModel):
    app_name: str
    repo_name: str
    index_type: CodeIndexType


class ToolConfig(ConfiguredModel):
    name: str
    tool_creds: Optional[dict[str, Any]] = None
    integration_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_credentials_provided(self) -> "ToolConfig":
        """
        Validate that either tool_creds or integration_id is provided.

        At least one of these fields must be specified for the tool configuration
        to be valid. This ensures that the tool has a way to obtain credentials.
        """
        if not self.tool_creds and not self.integration_id:
            raise ValueError("Either tool_creds or integration_id must be provided")
        if self.tool_creds and self.integration_id:
            raise ValueError("Either tool_creds or integration_id must be provided, but not both")
        return self


class AssistantChatRequest(ConfiguredModel):
    @staticmethod
    def validate_structured_output(value: Optional[dict] = None):
        from codemie.agents.utils import validate_json_schema

        if value:
            check = validate_json_schema(value)
            if not check:
                raise ExtendedHTTPException(code=422, message="Wrong JSON schema for structured output")
        return value

    conversation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    text: Optional[str] = None
    content_raw: Optional[str] = Field(default="")
    file_names: Optional[list[str]] = Field(default_factory=list)
    llm_model: Optional[str] = None
    history: list[ChatMessage] | str = Field(default_factory=list)
    history_index: Optional[int] = Field(default=None)
    mcp_server_single_usage: Optional[bool] = None  # Use conversation default if not specified
    workflow_execution_id: Optional[str] = Field(None, description="Identifier for the workflow execution")
    version: Optional[int] = Field(None, description="Optional version number to use specific assistant configuration")
    sub_assistants_versions: Optional[dict[str, int]] = Field(
        None,
        description="Optional mapping of sub-assistant IDs to their version numbers. "
        "Key: assistant_id, Value: version_number. "
        "Allows pinning specific versions of nested assistants.",
    )

    # Advanced params not generally used:
    stream: Optional[bool] = Field(default=False)
    top_k: int = Field(default=10)
    system_prompt: str = Field(default="")
    background_task: Optional[bool] = Field(default=False)
    metadata: Optional[dict[str, Any]] = None
    tools_config: Optional[list[ToolConfig]] = None
    output_schema: Annotated[Optional[dict], AfterValidator(validate_structured_output)] = None
    propagate_headers: bool = Field(default=False, description="Whether to propagate X-* headers to MCP servers")
    disable_cache: Optional[bool] = Field(
        default=False, description="Disable prompt caching for this request (applies to main agent LLM only)"
    )
    save_history: bool = Field(
        default=True,
        description="Whether to persist conversation history to the database. "
        "Set to False to skip saving this interaction.",
    )
    skill_ids: Optional[list[str]] = Field(
        default=None,
        description=(
            "Optional list of skill IDs to include for this request. "
            "These are merged with the assistant's existing skill_ids."
        ),
    )
    enable_web_search: Optional[bool] = Field(
        default=None,
        description=(
            "Enable web search tools (Google Search, Tavily, Web Scraper) for this request. "
            "Must be explicitly set to True to activate; None or False means disabled. "
            "Also requires the 'webSearch' feature to be enabled in customer config. "
            "Ignored if already enabled on assistant."
        ),
    )
    enable_code_interpreter: Optional[bool] = Field(
        default=None,
        description=(
            "Enable code interpreter tools (Python REPL, Code Executor) for this request. "
            "Must be explicitly set to True to activate; None or False means disabled. "
            "Also requires the 'dynamicCodeInterpreter' feature to be enabled in customer config. "
            "Ignored if already enabled on assistant."
        ),
    )

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

    @model_validator(mode="after")
    def validate_sub_assistants_versions(self):
        """Validate sub_assistants_versions field."""
        if self.sub_assistants_versions:
            # Validate that all version numbers are positive integers
            for assistant_id, version in self.sub_assistants_versions.items():
                if not isinstance(version, int) or version < 1:
                    raise ValueError(
                        f"Invalid version number for assistant '{assistant_id}': {version}. "
                        f"Version must be a positive integer."
                    )
        return self


class AssistantEvaluationRequest(BaseModel):
    """Request for evaluating an assistant on a dataset."""

    dataset_id: str
    experiment_name: str
    system_prompt: Optional[str] = Field(
        default=None, description="If provided, overrides the assistant's default system prompt for this evaluation"
    )
    llm_model: Optional[str] = None


class CreateConversationRequest(ConfiguredModel):
    initial_assistant_id: Optional[str] = None
    folder: Optional[str] = None
    mcp_server_single_usage: Optional[bool] = False
    is_workflow: Optional[bool] = None


class UpdateConversationFolderRequest(ConfiguredModel):
    folder: str


class UpdateConversationRequest(ConfiguredModel):
    folder: Optional[str] = None
    pinned: Optional[bool] = None
    name: Optional[str] = None
    active_assistant_id: Optional[str] = None
    llm_model: Optional[str] = None


class UpdateAiMessageRequest(ConfiguredModel):
    message_index: Optional[int] = None
    message: Optional[str] = None


class ModelKBRequest(ConfiguredModel):
    name: str
    prompt: str = Field(...)
    text: str = Field(min_length=1)
    history: Optional[list[ChatMessage]] = Field(default_factory=list)
    llm_model: str
    conversation_id: str
    stream: Optional[bool] = Field(default=False)
    debug: Optional[bool] = Field(default=False, alias="_debug", exclude=True)


class BaseResponse(ConfiguredModel):
    message: str


class BaseResponseWithData(BaseResponse):
    data: Any = None


class ElasticErrorResponse(ConfiguredModel):
    elastic_error: str


class BaseModelResponse(ConfiguredModel):
    """
    Base response model for assistant /model endpoint.

    Separates successful output from error information to ensure tool errors
    are not absorbed by the model's generated text.
    """

    # Success fields
    generated: Optional[str | dict | BaseModel] = None  # None if error occurred
    time_elapsed: Optional[float] = None
    tokens_used: Optional[int] = None
    thoughts: Optional[list] = None
    task_id: Optional[str] = None

    # Error fields
    success: bool = True  # False if any error occurred
    agent_error: Optional[AgentErrorDetails] = Field(
        None, description="Agent-level error details (token limits, callbacks, etc.)"
    )
    tool_errors: Optional[list[ToolErrorDetails]] = Field(
        None, description="Tool execution errors (HTTP errors, auth failures, etc.)"
    )


class InfoResponse(BaseResponse):
    version: str
    description: str


class ProjectInfoResponse(BaseModel):
    """Project access information in user responses"""

    name: str
    is_project_admin: bool


class UserResponse(BaseModel):
    """User response model with snake_case JSON fields (Story 3)

    Note: Does NOT inherit from ConfiguredModel to avoid camelCase aliasing.
    All fields serialize as snake_case per FR-4.1 terminology standardization.
    """

    user_id: str
    name: str
    username: str
    email: str = Field(default="")  # Added for user management (EPMCDME-10160)
    # Phase 2: Replaced applications/applications_admin with projects array
    projects: list[ProjectInfoResponse] = Field(default_factory=list)
    project_limit: Optional[int] = None  # NULL = unlimited (admins); None when user management disabled
    picture: str = Field(default="")
    knowledge_bases: list[str] = Field(default_factory=list)
    user_type: Optional[str] = None
    # These are kept to support legacy UI.
    # They will be removed once UI is migrated to use projects array instead of applications
    applications: list[str] = Field(default_factory=list)
    applications_admin: list[str] = Field(default_factory=list)
    is_admin: bool = False
    is_maintainer: bool = False


class ApplicationsResponse(ConfiguredModel):
    applications: list[str]


class ExportAssistantRequest(BaseModel):
    env_vars: dict[str, Any] = Field(default_factory=dict)


class ElasticSearchKwargs(BaseModel):
    fetch_k: int = Field(default=100)
    k: int = Field(default=20)


class PlanActions(BaseModel):
    file: str = Field(default="")
    action_type: str = Field(default="")
    action: str = Field(default="")
    description: str = Field(default="")
    file_content: str = Field(default="")


class CreatedByUser(BaseModel):
    id: str
    username: str = Field(default="")
    name: str = Field(default="")


SYSTEM_USER = CreatedByUser(id="00000000-0000-0000-0000-000000000000", username="system", name="System")


class TokensUsage(BaseModel):
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    money_spent: float
    cached_tokens_money_spent: float = 0.0
    cached_tokens_creation_money_spent: float = 0.0


class IdeToolArgument(ConfiguredModel):
    description: Optional[str] = None
    type: str = Field(default="object")
    schema: Optional["IdeToolArgsSchema"] = None


class IdeToolArgsSchema(ConfiguredModel):
    type: str = Field(default="object")
    required: list[str] = Field(default_factory=list)
    properties: dict[str, IdeToolArgument] = Field(default_factory=dict)


class IdeToolDefinition(ConfiguredModel):
    name: str = Field(default="")
    subject: str = Field(default="")
    description: str = Field(default="")
    args_schema: IdeToolArgsSchema


class IdeChatRequest(AssistantChatRequest):
    ide_installation_id: str = Field("")
    ide_request_id: str = Field("")
    tools: list[IdeToolDefinition] = Field(default_factory=list)
    stream: Optional[bool] = Field(default=True)
    prompt_header: Optional[str] = None
    prompt_footer: Optional[str] = None


class VirtualIdeChatRequest(IdeChatRequest):
    virtual_assistant_id: str = Field("")
    system_prompt: str = Field("")
    llm_model: str = Field("")
    temperature: Optional[float] = Field(default=1.0)
    top_p: Optional[float] = None
    project: Optional[str] = None
