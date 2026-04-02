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

import secrets
from datetime import datetime, timedelta
from enum import Enum
from typing import Literal, Optional, List, ClassVar, Dict, Sequence
from typing_extensions import Annotated
from uuid import uuid4

from codemie.clients.elasticsearch import ElasticSearchClient
from fastapi import UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import computed_field, model_validator, BaseModel, Field, field_validator, StringConstraints

from codemie.configs import logger, config
from codemie.core.ability import Ability, Owned, Action
from codemie.core.models import CreatedByUser, GitRepo, KnowledgeBase, TokensUsage, sanitize_es_index_name
from codemie.datasource.datasources_config import CONFLUENCE_CONFIG, STORAGE_CONFIG
from codemie.rest_api.models.base import BaseModelWithSQLSupport, PydanticType
from codemie.rest_api.models.guardrail import GuardrailAssignmentItem, GuardrailEntity
from codemie.rest_api.security.user import User
from codemie.service.constants import FullDatasourceTypes
from codemie.service.llm_service.llm_service import llm_service
from sqlmodel import Field as SQLField, Session, select, Column, and_, or_, Index, text as sqltext
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.orm.attributes import flag_modified

from codemie.service.guardrail.guardrail_service import GuardrailService
from codemie.service.settings.scheduler_settings_service import (
    SchedulerSettingsService,
    validate_cron_expression,
)


class SearchFields(str, Enum):
    PROJECT_NAME = "project_name.keyword"
    REPO_NAME = "repo_name.keyword"
    CREATED_BY_ID = "created_by.id.keyword"
    CREATED_BY_USER_ID = "created_by.user_id.keyword"
    PROJECT_SPACE_VISIBLE = "project_space_visible"


class SortOrder(str, Enum):
    ASC = "asc"
    DESC = "desc"


class SortKey(str, Enum):
    DATE = "date"
    UPDATE_DATE = "update_date"


class IndexInfoStatus(str, Enum):
    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"
    FAILED = "failed"


class IndexInfoType(str, Enum):
    KB_BEDROCK = "knowledge_base_bedrock"


class IndexTypeByContextTypeMapping(Enum):
    CODE = ("code", "summary", "chunk-summary")
    KNOWLEDGE_BASE = (
        "knowledge_base_confluence",
        "knowledge_base_jira",
        "knowledge_base_xray",
        "knowledge_base_azure_devops_wiki",
        "knowledge_base_azure_devops_work_item",
        "knowledge_base_sharepoint",
        "knowledge_base_file",
        "llm_routing_google",
        "platform_marketplace_assistant",
    )
    PROVIDER = ("provider",)


class IndexDeletedException(Exception): ...


class GuardrailBlockedException(Exception): ...


class IncompatibleIndexTypeException(Exception): ...


class CronExpressionValidatorMixin:
    """Mixin to add cron_expression validation to request models."""

    @model_validator(mode='after')
    def validate_cron_expression_field(self):
        """Validate cron_expression if explicitly provided."""
        # Only validate if the field was explicitly set in the request
        if 'cron_expression' in self.model_fields_set:
            validate_cron_expression(self.cron_expression)
        return self


class ConfluenceIndexInfo(BaseModel):
    cql: str
    include_restricted_content: Optional[bool] = False
    include_archived_content: Optional[bool] = False
    include_attachments: Optional[bool] = False
    include_comments: Optional[bool] = False
    keep_markdown_format: Optional[bool] = False
    keep_newlines: Optional[bool] = False
    max_pages: Optional[int] = CONFLUENCE_CONFIG.loader_max_pages
    pages_per_request: Optional[int] = CONFLUENCE_CONFIG.loader_pages_per_request


class JiraIndexInfo(BaseModel):
    jql: str


class XrayIndexInfo(BaseModel):
    jql: str


class ElasticsearchStatsResponse(BaseModel):
    """Response model for Elasticsearch index statistics."""

    index_name: str = Field(..., description="Name of the index in Elasticsearch")
    size_in_bytes: int = Field(..., ge=0, description="Size of the index in bytes")


class AzureDevOpsWikiIndexInfo(BaseModel):
    wiki_query: str  # Query to filter wiki pages (e.g., path filter or page title filter)
    wiki_name: Optional[str] = None  # Optional: specific wiki name to filter


class AzureDevOpsWorkItemIndexInfo(BaseModel):
    wiql_query: str = ""


_HTTPS_SCHEME = "https://"
_SITE_URL_SCHEME_ERROR = "site_url must start with https://"


class SharePointIndexInfo(BaseModel):
    site_url: str  # e.g., https://tenant.sharepoint.com/sites/sitename
    path_filter: Optional[str] = "*"  # e.g., /Shared Documents/*
    include_pages: Optional[bool] = True
    include_documents: Optional[bool] = True
    include_lists: Optional[bool] = True
    max_file_size_mb: Optional[int] = Field(default=50, gt=0, le=500)
    files_filter: Optional[str] = ""  # Gitignore-style extension/name filter for documents
    auth_type: Literal["integration", "oauth_codemie", "oauth_custom"] = "integration"
    oauth_client_id: Optional[str] = None  # Azure app client ID for oauth_custom
    oauth_tenant_id: Optional[str] = None  # Azure AD tenant ID for single-tenant custom apps
    # OAuth token fields — populated at indexing start for watchdog resume support
    access_token: str = ""  # Stored only for oauth_codemie / oauth_custom auth types
    expires_at: int = 0  # Unix timestamp; 0 means unknown / not set

    @field_validator("site_url")
    @classmethod
    def validate_site_url(cls, v: str) -> str:
        if not v.startswith(_HTTPS_SCHEME):
            raise ValueError(_SITE_URL_SCHEME_ERROR)
        return v


class IndexListItem(BaseModel):
    id: str
    project_name: str
    repo_name: str
    index_type: str
    created_by: Optional[Dict] = None
    project_space_visible: bool = True
    link: Optional[str] = None
    date: Optional[datetime] = None
    update_date: Optional[datetime] = None
    setting_id: Optional[str] = None

    text: str = ""
    full_name: str = ""

    current_state: int = 0
    complete_state: int = 0
    current__chunks_state: Optional[int] = 0
    error: bool = False
    completed: bool = False
    is_fetching: Optional[bool] = False

    user_abilities: Optional[List[Action]] = []

    jira: Optional[JiraIndexInfo] = None
    xray: Optional[XrayIndexInfo] = None
    sharepoint: Optional[SharePointIndexInfo] = None
    aice_datasource_id: Optional[str] = None
    cron_expression: Optional[str] = None


class IndexInfoProviderFields(BaseModel):
    """Store provider fields for IndexInfo object"""

    provider_id: str
    toolkit_id: str

    base_params: Optional[Dict] = Field(default_factory=dict)
    create_params: Optional[Dict] = Field(default_factory=dict)

    otp: Optional[str] = None


class BedrockKnowledgeBaseData(BaseModel):
    bedrock_knowledge_base_id: str
    bedrock_name: str
    bedrock_model_arn: Optional[str] = None
    bedrock_kendra_index_arn: Optional[str] = None
    bedrock_status: str
    bedrock_type: str
    bedrock_created_at: datetime
    bedrock_updated_at: Optional[datetime] = None
    bedrock_storage_type: Optional[str] = None
    bedrock_aws_settings_id: str


class IndexInfo(BaseModelWithSQLSupport, Owned, table=True):
    """
    A base class for both Code and KnowledgeBase index types
    """

    __tablename__ = "index_info"
    project_name: str = SQLField(index=True)
    description: str = SQLField(max_length=500, default="")

    repo_name: str
    index_type: str
    prompt: Optional[str] = None
    embeddings_model: Optional[str] = None
    summarization_model: Optional[str] = None
    current_state: int = 0
    complete_state: int = 0
    current__chunks_state: Optional[int] = 0
    processed_files: Optional[List] = SQLField(default_factory=list, sa_column=Column(MutableList.as_mutable(JSONB())))
    error: bool = SQLField(default=False, index=True)
    completed: bool = SQLField(default=False, index=True)
    text: str = ""
    full_name: str = ""
    created_by: Optional[CreatedByUser] = SQLField(default=None, sa_column=Column(PydanticType(CreatedByUser)))
    project_space_visible: bool = True
    docs_generation: bool = False
    branch: Optional[str] = None
    link: Optional[str] = None
    files_filter: Optional[str] = SQLField(default="")
    google_doc_link: str = ""
    user_abilities: Optional[List[Action]] = SQLField(default=None, sa_column=Column(JSONB))
    confluence: Optional[ConfluenceIndexInfo] = SQLField(
        default=None, sa_column=Column(PydanticType(ConfluenceIndexInfo))
    )
    jira: Optional[JiraIndexInfo] = SQLField(default=None, sa_column=Column(PydanticType(JiraIndexInfo)))
    xray: Optional[XrayIndexInfo] = SQLField(default=None, sa_column=Column(PydanticType(XrayIndexInfo)))
    azure_devops_wiki: Optional[AzureDevOpsWikiIndexInfo] = SQLField(
        default=None, sa_column=Column(PydanticType(AzureDevOpsWikiIndexInfo))
    )
    azure_devops_work_item: Optional[AzureDevOpsWorkItemIndexInfo] = SQLField(
        default=None, sa_column=Column(PydanticType(AzureDevOpsWorkItemIndexInfo))
    )
    sharepoint: Optional[SharePointIndexInfo] = SQLField(
        default=None, sa_column=Column(PydanticType(SharePointIndexInfo))
    )
    is_fetching: Optional[bool] = False
    setting_id: Optional[str] = None
    tokens_usage: Optional[TokensUsage] = SQLField(default=None, sa_column=Column(PydanticType(TokensUsage)))
    processing_info: Optional[Dict] = SQLField(default_factory=dict, sa_column=Column(JSONB))
    provider_fields: Optional[IndexInfoProviderFields] = SQLField(
        default=None, sa_column=Column(PydanticType(IndexInfoProviderFields))
    )
    bedrock: Optional[BedrockKnowledgeBaseData] = SQLField(
        default=None, sa_column=Column(PydanticType(BedrockKnowledgeBaseData))
    )
    uses_legacy_es_naming: bool = SQLField(
        default=False,
        description=(
            "If True, uses legacy ES index naming (repo_name only) "
            "for backward compatibility with pre-EPMCDME-10809 datasources"
        ),
    )
    # Custom PostgreSQL indexes

    __table_args__ = (
        Index(
            'ix_index_info_repo_name', "repo_name", postgresql_using='gin', postgresql_ops={"repo_name": "gin_trgm_ops"}
        ),
        Index(
            'ix_index_info_index_type',
            "index_type",
            postgresql_using='gin',
            postgresql_ops={"index_type": "gin_trgm_ops"},
        ),
        Index('ix_index_info_created_by_id', sqltext("(created_by->>'id')")),
        Index('ix_index_info_created_by_name', sqltext("(created_by->>'name') gin_trgm_ops"), postgresql_using='gin'),
        Index('ix_index_info_date', 'date'),
        Index('ix_index_info_update_date', 'update_date'),
        Index(
            'ix_index_info_bedrock_aws_settings_id',
            sqltext("(bedrock->>'bedrock_aws_settings_id')"),
            postgresql_using='btree',
        ),
        Index(
            'uq_index_info_bedrock_settings_knowledge_base_unique',
            sqltext("(bedrock->>'bedrock_aws_settings_id')"),
            sqltext("(bedrock->>'bedrock_knowledge_base_id')"),
            unique=True,
            postgresql_where=sqltext(
                "(bedrock->>'bedrock_aws_settings_id') IS NOT NULL AND "
                "(bedrock->>'bedrock_knowledge_base_id') IS NOT NULL"
            ),
        ),
        Index('ix_index_info_provider_fields_provider_id', sqltext("(provider_fields->>'provider_id')")),
    )

    @field_validator("link")
    def trim_link(cls, link_value: Optional[str]) -> Optional[str]:
        return link_value.strip() if link_value else link_value

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

    @computed_field(return_type=Optional[datetime])
    @property
    def last_reindex_date(self) -> Optional[datetime]:
        """
        Transient snapshot of the stateful update_date from database before it gets modified.

        This captures the persisted update_date timestamp right before update_index()
        overwrites it with the current time, allowing incremental reindex to query
        changes that occurred since the last successful reindex.

        Unlike update_date (which is stateful and stored in DB), this field is:
        - Not persisted to database
        - Only populated at runtime during update_index()
        - Cleared after the request completes
        """
        return getattr(self, '_last_reindex_date', None)

    @last_reindex_date.setter
    def last_reindex_date(self, value: Optional[datetime]):
        self._last_reindex_date = value

    @classmethod
    def create_from_repo(cls, repo, user):
        return cls.new(
            project_name=repo.app_id,
            repo_name=repo.name,
            description=repo.description,
            branch=repo.branch,
            link=repo.link,
            files_filter=repo.files_filter,
            index_type=repo.index_type.value,
            prompt=repo.prompt,
            project_space_visible=repo.project_space_visible,
            docs_generation=repo.docs_generation,
            embeddings_model=repo.embeddings_model,
            summarization_model=repo.summarization_model,
            user=user,
            setting_id=repo.setting_id,
        )

    @classmethod
    def create_from_file_processor(cls, file_processor, user):
        return cls.new(
            repo_name=file_processor.datasource_name,
            full_name=file_processor.datasource_name,
            project_name=file_processor.project_name,
            description=file_processor.description,
            project_space_visible=file_processor.project_space_visible,
            index_type=file_processor.INDEX_TYPE,
            current_state=0,
            complete_state=0,
            error=False,
            completed=False,
            user=user,
            embeddings_model=file_processor.embedding_model or llm_service.default_embedding_model,
        )

    def update(self, refresh=False, validate=True):
        try:
            super().update(refresh=refresh, validate=validate)
        except StaleDataError:
            raise IndexDeletedException

    def start_fetching(self, is_incremental: bool = False):
        self._reset_state(is_incremental=is_incremental, is_fetching=True)
        self.update(refresh=True)

    def start_progress(self, complete_state: int, processing_info=None, is_incremental: bool = False):
        """Prepare the index for processing"""
        self._reset_state(is_incremental=is_incremental, is_fetching=False, complete_state=complete_state)

        self.processing_info = processing_info or {}
        self.update()

    def _reset_state(self, is_incremental: bool = False, is_fetching: bool = False, complete_state: int = 0):
        """Set initial state before indexing / reindexing starts"""
        self.completed = False
        self.error = False
        self.is_fetching = is_fetching

        if is_incremental and complete_state:
            self.current_state = self.complete_state
            self.complete_state = self.complete_state + complete_state

        if not is_incremental:
            self.current_state = 0
            self.complete_state = complete_state
            self.processed_files = []
            self.current__chunks_state = 0

    def move_progress(self, count=1, chunks_count=1, processed_file: str = None, complete_state: int = None):
        self.current_state += count
        self.current__chunks_state += chunks_count
        self.is_fetching = False

        if processed_file:
            self.processed_files.append(processed_file)
        if complete_state is not None:
            self.complete_state = complete_state

        self.update()

    def decrease_progress(self, count=1, chunks_count=1, processed_file: str = None, complete_state: int = None):
        """
        Decrease progress counters when removing documents from the index.

        This method is the counterpart to move_progress() and is used when documents are removed
        from the index (e.g., unpublishing marketplace assistants).

        Args:
            count (int): Decrement for the current_state. Default is 1.
            chunks_count (int): Decrement for the current_chunks_state. Default is 1.
            processed_file (Optional[str]): The file path to remove from processed_files list.
                If None, no file is removed.
            complete_state (Optional[int]): If provided, sets complete_state to this value.
                Otherwise, decrements complete_state by count.
        """
        self.current_state = max(0, self.current_state - count)
        self.current__chunks_state = max(0, self.current__chunks_state - chunks_count)
        self.is_fetching = False

        if processed_file and processed_file in self.processed_files:
            self.processed_files.remove(processed_file)

        if complete_state is not None:
            self.complete_state = complete_state
        else:
            self.complete_state = max(0, self.complete_state - count)

        self.update()

    def gather_stats(self, count=1, chunks_count=1, processed_document: str = None):
        """
        Gather stats by updating the object's state without writing to DB.

        This method is designed to update the internal state of the object without committing the changes
        to DB. It helps in avoiding frequent writes to the DB, which can be
        overwhelming and inefficient, especially in concurrent processing scenarios where the list of
        processed documents might contain thousands of document paths.

        Args:
            count (int): Increment for the current_state. Default is 1.
            chunks_count (int): Increment for the current_chunks_state. Default is 1.
            processed_document (Optional[str]): The document path to add to the processed_documents list.
            If None, no document is added.
        """
        self.current_state += count
        self.current__chunks_state += chunks_count
        if processed_document and STORAGE_CONFIG.processed_documents_threshold >= len(self.processed_files):
            self.processed_files.append(processed_document)

    def commit_stats(self):
        """
        Commit the current state to DB.

        This method is designed to write the current state of the object to DB. It should be
        called after a batch of operations has been completed to efficiently update the DB,
        reducing the frequency of writes and thereby avoiding the risk of overwhelming the index
        during concurrent processing of large datasets.
        """
        logger.info(
            f"IndexDatasource. CommittingStats. "
            f"Datasource={self.repo_name}. "
            f"ProcessedSources={self.current_state}/{self.complete_state}. "
            f"ProcessedChunks={self.current__chunks_state}"
        )
        self.update()

    def complete_progress(self, complete_state: int = None):
        if complete_state:
            self.complete_state = complete_state
            self.current_state = complete_state
        else:
            self.current_state = self.complete_state
        self.completed = True
        self.is_fetching = False
        self.error = False
        self.update()

    def set_error(self, message: str):
        self.error = True
        self.text = message
        self.is_fetching = False
        self.update()

    def generate_otp(self):
        otp = secrets.token_urlsafe(32)
        if self.provider_fields:
            self.provider_fields.otp = otp
            flag_modified(self, 'provider_fields')
            self.save()
        else:
            raise IncompatibleIndexTypeException("OTP can be generated only for Provider type indexes")
        return otp

    def reset_otp(self):
        if self.provider_fields:
            self.provider_fields.otp = None
            flag_modified(self, 'provider_fields')
            self.save()

    @model_validator(mode='before')
    @classmethod
    def set_full_name(cls, data):
        identifier = data.get("id", str(uuid4()))
        project_name = data["project_name"]
        repo_name = data["repo_name"]
        index_type = data["index_type"]

        data["full_name"] = f"{identifier}-{project_name}-{repo_name}-{index_type}"
        return data

    @classmethod
    def new(
        cls,
        project_name: str,
        repo_name: str,
        index_type: str,
        user: User,
        project_space_visible: bool = False,
        description: str = "",
        branch: Optional[str] = None,
        link: Optional[str] = None,
        embeddings_model: str = llm_service.default_embedding_model,
        summarization_model: str = llm_service.default_llm_model,
        setting_id: Optional[str] = None,
        **kwargs,
    ) -> "IndexInfo":
        prompt = kwargs.get("prompt")
        docs_generation = kwargs.get("docs_generation", False)
        files_filter = kwargs.get("files_filter")
        confluence = kwargs.get("confluence")
        jira = kwargs.get("jira")
        xray = kwargs.get("xray")
        azure_devops_wiki = kwargs.get("azure_devops_wiki")
        azure_devops_work_item = kwargs.get("azure_devops_work_item")
        sharepoint = kwargs.get("sharepoint")
        google_doc_link = kwargs.get("google_doc_link", "")
        obj = cls(
            project_name=project_name,
            repo_name=repo_name,
            description=description,
            index_type=index_type,
            prompt=prompt,
            branch=branch,
            link=link,
            files_filter=files_filter,
            project_space_visible=project_space_visible,
            docs_generation=docs_generation,
            embeddings_model=embeddings_model,
            summarization_model=summarization_model,
            created_by=CreatedByUser(id=user.id, username=user.username, name=user.name),
            id=str(uuid4()),
            setting_id=setting_id,
            confluence=confluence,
            jira=jira,
            xray=xray,
            azure_devops_wiki=azure_devops_wiki,
            azure_devops_work_item=azure_devops_work_item,
            sharepoint=sharepoint,
            google_doc_link=google_doc_link,
        )
        obj.save(refresh=True)
        return obj

    def _reindex_to_new_project(self, new_project_name: str) -> None:
        """Copy ES documents from old project-scoped index to new one when KB is moved."""
        old_index = self.get_index_identifier()
        # Temporarily compute new index name using the new project_name
        new_index = KnowledgeBase(name=f"{new_project_name}-{self.repo_name}", type=self.index_type).get_identifier()
        if old_index == new_index:
            return
        es = ElasticSearchClient.get_client()
        try:
            if not es.indices.exists(index=old_index):
                logger.info(f"ES index {old_index} does not exist, skipping reindex for KB move")
                return
            es.reindex(
                body={"source": {"index": old_index}, "dest": {"index": new_index}},
                wait_for_completion=True,
                refresh=True,
            )
            es.indices.delete(index=old_index, ignore_unavailable=True)
            logger.info(f"Reindexed KB from {old_index} to {new_index} for project move")

            # Migrate legacy datasources to new naming convention when moved
            if self.uses_legacy_es_naming:
                self.uses_legacy_es_naming = False
                logger.info(f"Migrated KB {self.repo_name} from legacy to new ES naming convention")
        except Exception as e:
            logger.error(f"Failed to reindex KB from {old_index} to {new_index} on project move: {e}")

    def _update_query_fields(
        self,
        cql: Optional[str] = None,
        jql: Optional[str] = None,
        wiki_query: Optional[str] = None,
    ) -> None:
        """Update query fields for different datasource types."""
        if cql:
            self.confluence.cql = cql
            flag_modified(self, 'confluence')

        if jql:
            if self.index_type == "knowledge_base_xray":
                self.xray.jql = jql
                flag_modified(self, 'xray')
            else:
                self.jira.jql = jql
                flag_modified(self, 'jira')

        if wiki_query:
            self.azure_devops_wiki.wiki_query = wiki_query
            flag_modified(self, 'azure_devops_wiki')

    def _update_sharepoint_fields(self, **kwargs) -> None:
        """Update SharePoint-specific fields."""
        if not self.sharepoint:
            return

        site_url = kwargs.get("site_url")
        path_filter = kwargs.get("path_filter")
        include_pages = kwargs.get("include_pages")
        include_documents = kwargs.get("include_documents")
        include_lists = kwargs.get("include_lists")
        max_file_size_mb = kwargs.get("max_file_size_mb")
        files_filter = kwargs.get("files_filter")
        auth_type = kwargs.get("auth_type")
        oauth_client_id = kwargs.get("oauth_client_id")
        oauth_tenant_id = kwargs.get("oauth_tenant_id")

        if site_url is not None:
            self.sharepoint.site_url = site_url
        if path_filter is not None:
            self.sharepoint.path_filter = path_filter
        if include_pages is not None:
            self.sharepoint.include_pages = include_pages
        if include_documents is not None:
            self.sharepoint.include_documents = include_documents
        if include_lists is not None:
            self.sharepoint.include_lists = include_lists
        if max_file_size_mb is not None:
            self.sharepoint.max_file_size_mb = max_file_size_mb
        if files_filter is not None:
            self.sharepoint.files_filter = files_filter
        if auth_type is not None:
            self.sharepoint.auth_type = auth_type
        if oauth_client_id is not None:
            self.sharepoint.oauth_client_id = oauth_client_id
        if oauth_tenant_id is not None:
            self.sharepoint.oauth_tenant_id = oauth_tenant_id

        flag_modified(self, 'sharepoint')

    def update_index(
        self,
        user: User,
        project_space_visible: Optional[bool] = None,
        description: Optional[str] = None,
        branch: Optional[str] = None,
        link: Optional[str] = None,
        embeddings_model: Optional[str] = None,
        summarization_model: str = llm_service.default_llm_model,
        cql: Optional[str] = None,
        jql: Optional[str] = None,
        wiki_query: Optional[str] = None,
        wiql_query: Optional[str] = None,
        reset_error: bool = True,
        setting_id: Optional[str] = None,
        project_name: Optional[str] = None,
        guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None,
        **kwargs,
    ) -> "IndexInfo":
        files_filter = kwargs.get("files_filter")
        prompt = kwargs.get("prompt")
        docs_generation = kwargs.get("docs_generation", False)

        # Preserve date before update for incremental reindex
        self.last_reindex_date = self.update_date

        if description:
            self.description = description
        self.prompt = prompt

        if project_space_visible is not None:
            self.project_space_visible = project_space_visible
        self.docs_generation = docs_generation
        if embeddings_model:
            self.embeddings_model = embeddings_model
        self.summarization_model = summarization_model
        self.branch = branch
        self.link = link
        self.files_filter = files_filter

        if reset_error:
            self.error = False

        self._update_query_fields(cql=cql, jql=jql, wiki_query=wiki_query)

        if wiql_query and self.azure_devops_work_item:
            self.azure_devops_work_item.wiql_query = wiql_query
            flag_modified(self, 'azure_devops_work_item')

        self._update_sharepoint_fields(**kwargs)

        if setting_id:
            self.setting_id = setting_id

        # Handle project_name change if provided
        if project_name and project_name != self.project_name:
            if self.index_type.startswith("knowledge_base"):
                self._reindex_to_new_project(project_name)
            self.project_name = project_name

        self.update()

        GuardrailService.sync_guardrail_assignments_for_entity(
            user=user,
            entity_type=GuardrailEntity.KNOWLEDGEBASE,
            entity_id=str(self.id),
            entity_project_name=self.project_name,
            guardrail_assignments=guardrail_assignments,
        )

        return self

    @classmethod
    def get_all_for_user(cls, user: User, project_name: str) -> list[IndexListItem]:
        result = (
            IndexInfo.get_all_by_fields({"error": False, "project_name": project_name})
            if user.is_admin
            else cls.filter_for_user(user=user, project_name=project_name)
        )

        for entry in result:
            entry.user_abilities = Ability(user).list(entry)

        return [
            IndexListItem(
                id=index.id,
                date=index.date,
                update_date=index.update_date,
                project_name=index.project_name,
                repo_name=index.repo_name,
                index_type=index.index_type,
                created_by={
                    'id': index.created_by.id,
                    'username': index.created_by.username,
                    'name': index.created_by.name,
                },
                project_space_visible=index.project_space_visible,
                link=index.link,
                text=index.text,
                full_name=index.full_name,
                current_state=index.current_state,
                complete_state=index.complete_state,
                current__chunks_state=index.current__chunks_state,
                error=index.error,
                completed=index.completed,
                is_fetching=index.is_fetching,
                user_abilities=index.user_abilities,
                jira=index.jira,
                xray=index.xray,
            )
            for index in result
        ]

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

    @classmethod
    def filter_by_projects(cls, projects_names) -> Sequence["IndexInfo"]:
        with Session(cls.get_engine()) as session:
            statement = select(cls)
            statement = statement.where(cls.project_name.in_(projects_names))
            statement = statement.order_by(cls.date.desc())
            return session.exec(statement).all()

    @classmethod
    def filter_for_user(cls, user: User, project_name: str) -> Sequence["IndexInfo"]:
        """
        Returns indices that match any of these conditions:
        - Project name is in user's applications AND project is space visible
        - Project name is in user's admin applications
        - User is the creator of the index
        """
        with Session(cls.get_engine()) as session:
            statement = select(cls).where(
                and_(
                    cls.project_name == project_name,
                    cls.error.is_(False),
                    or_(
                        and_(cls.project_name.in_(user.project_names), cls.project_space_visible),
                        cls.project_name.in_(user.admin_project_names),
                        cls.created_by['id'].astext == user.id,
                    ),
                )
            )
            statement = statement.order_by(cls.date.desc())
            return session.exec(statement).all()

    @classmethod
    def filter_for_user_repo_names(cls, user: User, project_name: str, repo_names: List[str]) -> Sequence["IndexInfo"]:
        with Session(cls.get_engine()) as session:
            statement = select(cls).where(
                and_(
                    cls.project_name == project_name,
                    cls.error.is_(False),
                    cls.repo_name.in_(repo_names),
                    or_(
                        and_(cls.project_name.in_(user.project_names), cls.project_space_visible),
                        cls.project_name.in_(user.admin_project_names),
                        cls.created_by['id'].astext == user.id,
                    ),
                )
            )
            statement = statement.order_by(cls.date.desc())
            entries = session.exec(statement).all()

        for entry in entries:
            entry.user_abilities = Ability(user).list(entry)

        return entries

    @classmethod
    def get_stale_in_progress(cls, threshold_seconds: int, limit: int = 5) -> list["IndexInfo"]:
        """Return IndexInfo records stuck in IN_PROGRESS for longer than threshold.

        Excludes is_fetching=True records: a job actively fetching a large remote
        dataset (Confluence export, large Jira query) may legitimately exceed
        threshold_seconds without refreshing update_date — it is not stuck.
        """
        with Session(cls.get_engine()) as session:
            cutoff = datetime.now() - timedelta(seconds=threshold_seconds)
            statement = (
                select(cls)
                .where(cls.completed == False)  # noqa: E712
                .where(cls.error == False)  # noqa: E712
                .where(cls.is_fetching == False)  # noqa: E712
                .where(or_(cls.update_date.is_(None), cls.update_date < cutoff))
                .order_by(cls.update_date.asc())
                .limit(limit)
            )
            return session.exec(statement).all()

    @classmethod
    def try_claim_for_resume(cls, index_id: str, threshold_seconds: int) -> bool:
        """Atomically claim a stale IN_PROGRESS record for resume.

        Uses a conditional UPDATE so only one caller (pod) wins when multiple
        pods detect the same stale job simultaneously. Returns True if this
        caller successfully claimed the record (rowcount == 1), False if another
        caller already claimed or the record is no longer stale.

        Sets update_date to one second past the staleness cutoff rather than
        datetime.now() to avoid corrupting the incremental reindex watermark:
        update_date is read back as last_reindex_date by incremental processors
        (Jira, Confluence, etc.) to determine which documents to fetch.
        """

        cutoff = datetime.now() - timedelta(seconds=threshold_seconds)
        # Stay just inside the stale window so the claim timestamp does not
        # drift the incremental watermark far into the future.
        claim_timestamp = cutoff + timedelta(seconds=1)
        stmt = (
            sa_update(cls)
            .where(cls.id == index_id)
            .where(cls.completed == False)  # noqa: E712
            .where(cls.error == False)  # noqa: E712
            .where(or_(cls.update_date.is_(None), cls.update_date < cutoff))
            .values(update_date=claim_timestamp)
        )
        with Session(cls.get_engine()) as session:
            result = session.execute(stmt)
            session.commit()
            return result.rowcount == 1

    @classmethod
    def filter_by_project_and_repo(cls, project_name: str, repo_name: str) -> Sequence["IndexInfo"]:
        """
        Find a record by project and repo name
        """
        with Session(cls.get_engine()) as session:
            statement = (
                select(cls)
                .where(and_(cls.project_name == project_name, cls.repo_name == repo_name))
                .order_by(cls.date.desc())
            )
            return session.exec(statement).all()

    @classmethod
    def find_by_name_and_type(
        cls, name: str, index_type: str, project_name: str | None = None
    ) -> Optional["IndexInfo"]:
        try:
            prefixes = IndexTypeByContextTypeMapping[index_type.upper()].value
        except KeyError:
            return None

        conditions = [
            cls.repo_name == name,
            or_(*(cls.index_type.like(f"{p}%") for p in prefixes)),
        ]
        if project_name is not None:
            conditions.append(cls.project_name == project_name)

        statement = select(cls).where(and_(*conditions))
        with Session(cls.get_engine()) as session:
            return session.execute(statement).scalars().first()

    def is_code_index(self) -> bool:
        kb_index = self.index_type.startswith("knowledge_base")
        google_doc_index = self.is_google_doc_index()
        platform_index = self.is_platform_index()
        return not (kb_index or google_doc_index or platform_index)

    def is_google_doc_index(self) -> bool:
        return self.index_type.startswith(FullDatasourceTypes.GOOGLE)

    def is_platform_index(self) -> bool:
        return self.index_type.startswith("platform")

    def get_index_identifier(self) -> str:
        if self.index_type.startswith("knowledge_base") or self.is_google_doc_index():
            # Check legacy naming flag for backward compatibility
            if self.uses_legacy_es_naming:
                # OLD naming: repo_name only (pre-naming-update datasources)
                return KnowledgeBase(name=self.repo_name, type=self.index_type).get_identifier()
            else:
                # NEW naming: project_name-repo_name (post-naming-update datasources)
                kb_name = f"{self.project_name}-{self.repo_name}"
                return KnowledgeBase(name=kb_name, type=self.index_type).get_identifier()
        else:
            # Non-KB types (code, platform, etc.) always use project-scoped naming
            return sanitize_es_index_name('-'.join((self.project_name, self.repo_name, self.index_type)))

    def get_completed_chunks(self, chunks: List[str]) -> List[str]:
        """
        Retrieves a list of completed chunks from the index.

        This function checks if any of the provided chunk names exist in the index.
        It returns a list of chunk names that were found in the index.

        Args:
            chunks (List[str]): A list of chunk names to search for in the index.

        Returns:
            List[str]: A list of chunk names that were found in the Elasticsearch index.
                    If the index doesn't exist, an empty list is returned.
        """
        index_name = self.get_index_identifier()
        elastic_client = ElasticSearchClient.get_client()
        if elastic_client.indices.exists(index=index_name):
            es_query = {
                "bool": {
                    "minimum_should_match": 1,
                    "should": [{"match_phrase": {"metadata.source": i}} for i in chunks],
                }
            }

            search_results = elastic_client.search(
                index=index_name, query=es_query, source=["metadata.source"], size=10000
            )
            completed_chunks = [hit["_source"]["metadata"]["source"] for hit in search_results["hits"]["hits"]]
        else:
            completed_chunks = []

        return completed_chunks

    def delete(self):
        index_name = self.get_index_identifier()

        if self.is_code_index():
            git_repo = GitRepo.find_by_id(index_name)
            if git_repo:
                git_repo.delete()

        elastic_client = ElasticSearchClient.get_client()
        if elastic_client.indices.exists(index=index_name):
            elastic_client.indices.delete(index=index_name)

        # Delete associated scheduler settings (cron jobs)
        if self.created_by and self.created_by.id:
            try:
                SchedulerSettingsService.delete_schedule(resource_id=self.id, user_id=self.created_by.id)
            except Exception as e:
                logger.warning(f"Failed to delete scheduler settings for datasource {self.id}: {e}")

        super().delete()

    def is_owned_by(self, user: User) -> bool:
        return self.created_by.id == user.id

    def is_managed_by(self, user: User) -> bool:
        return self.project_name in user.admin_project_names

    def is_shared_with(self, user: User) -> bool:
        return self.project_name in user.project_names and self.project_space_visible


class FilteredIndexInfo(BaseModel):
    @classmethod
    def get_filter(cls):
        """Returns SQLAlchemy filter condition for the index type"""
        raise NotImplementedError("Subclasses must implement get_filter")

    @classmethod
    def get_all(
        cls, response_class: BaseModel | None = None, page_number: int = 1, items_per_page: int = 10_000
    ) -> Sequence["IndexInfo"]:
        with Session(IndexInfo.get_engine()) as session:
            statement = select(IndexInfo).where(cls.get_filter())
            statement = statement.order_by(IndexInfo.date.desc())
            statement = statement.offset((page_number - 1) * items_per_page)
            statement = statement.limit(items_per_page)
            return session.exec(statement).all()

    @classmethod
    def filter_by_project_and_repo(cls, project_name, repo_name) -> Sequence["IndexInfo"]:
        with Session(IndexInfo.get_engine()) as session:
            statement = select(IndexInfo).where(
                and_(IndexInfo.project_name == project_name, IndexInfo.repo_name == repo_name, cls.get_filter())
            )
            statement = statement.order_by(IndexInfo.date.desc())
            return session.exec(statement).all()

    @classmethod
    def filter_by_repo_name(cls, repo_name) -> Sequence["IndexInfo"]:
        with Session(IndexInfo.get_engine()) as session:
            statement = select(IndexInfo).where(and_(IndexInfo.repo_name == repo_name, cls.get_filter()))
            statement = statement.order_by(IndexInfo.date.desc())
            return session.exec(statement).all()

    @classmethod
    def get_by_user_id(cls, user_id) -> Sequence["IndexInfo"]:
        with Session(IndexInfo.get_engine()) as session:
            statement = select(IndexInfo).where(and_(IndexInfo.created_by['id'].astext == user_id, cls.get_filter()))
            statement = statement.order_by(IndexInfo.date.desc())
            return session.exec(statement).all()

    @classmethod
    def filter_by_names_or_user(cls, project_names: list[str], user: User) -> Sequence["IndexInfo"]:
        """
        Match provided project names or created_by.id equals provided user.id
        """
        with Session(IndexInfo.get_engine()) as session:
            statement = select(IndexInfo).where(
                and_(
                    or_(
                        IndexInfo.project_name.in_(project_names),
                        IndexInfo.created_by['id'].astext == user.id,
                        IndexInfo.project_space_visible,
                    ),
                    cls.get_filter(),
                )
            )
            statement = statement.order_by(IndexInfo.date.desc())
            return session.exec(statement).all()


class CodeIndexInfo(FilteredIndexInfo):
    @classmethod
    def get_filter(cls):
        """Returns filter condition for code-related indices"""
        return or_(
            IndexInfo.index_type.startswith('code'),
            IndexInfo.index_type.startswith('summary'),
            IndexInfo.index_type.startswith('chunk'),
        )


class KnowledgeBaseIndexInfo(FilteredIndexInfo):
    @classmethod
    def get_filter(cls):
        """Returns filter condition for knowledge base indices"""
        return or_(
            IndexInfo.index_type.startswith('knowledge_base_'),
            IndexInfo.index_type.startswith('llm_routing'),
            IndexInfo.index_type.startswith('platform_'),
        )


class ProviderIndexInfo(FilteredIndexInfo):
    @classmethod
    def get_filter(cls):
        """Returns filter condition for provider indices"""
        return IndexInfo.index_type.startswith('provider')


class IndexKnowledgeBaseRequest(BaseModel):
    name: Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][\w-]*$", min_length=4, max_length=50, strict=True)]
    project_name: str
    description: str = Field(min_length=1, max_length=500)
    project_space_visible: Optional[bool] = False
    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None


class IndexKnowledgeBaseConfluenceRequest(CronExpressionValidatorMixin, IndexKnowledgeBaseRequest):
    cql: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
    setting_id: Optional[str] = None
    include_restricted_content: Optional[bool] = False
    include_archived_content: Optional[bool] = False
    include_attachments: Optional[bool] = False
    include_comments: Optional[bool] = False
    keep_markdown_format: Optional[bool] = False
    keep_newlines: Optional[bool] = False
    embedding_model: Optional[str] = None
    cron_expression: Optional[str] = None


class DatasourceHealthCheckRequest(BaseModel):
    project_name: str
    index_type: str
    cql: Optional[str] = None
    jql: Optional[str] = None
    wiki_query: Optional[str] = None
    wiki_name: Optional[str] = None
    wiql_query: Optional[str] = None
    setting_id: str


class ErrorMessage(BaseModel):
    message: str
    details: Optional[str] = None
    help: Optional[str] = None
    field_error: Optional[str] = None


class DatasourceHealthCheckResponse(BaseModel):
    implemented: bool = True
    documents_count: Optional[int] = None
    error: Optional[ErrorMessage] = None


class IndexKnowledgeBaseJIRARequest(CronExpressionValidatorMixin, IndexKnowledgeBaseRequest):
    jql: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
    setting_id: Optional[str] = None
    embedding_model: Optional[str] = None
    cron_expression: Optional[str] = None


class IndexKnowledgeBaseXrayRequest(CronExpressionValidatorMixin, IndexKnowledgeBaseRequest):
    jql: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
    setting_id: Optional[str] = None
    embedding_model: Optional[str] = None
    cron_expression: Optional[str] = None


class IndexKnowledgeBaseAzureDevOpsWikiRequest(CronExpressionValidatorMixin, IndexKnowledgeBaseRequest):
    wiki_query: str = ''
    wiki_name: Optional[str] = None
    setting_id: Optional[str] = None
    embedding_model: Optional[str] = None
    cron_expression: Optional[str] = None


class IndexKnowledgeBaseAzureDevOpsWorkItemRequest(CronExpressionValidatorMixin, IndexKnowledgeBaseRequest):
    wiql_query: str = ""
    setting_id: Optional[str] = None
    embedding_model: Optional[str] = None
    cron_expression: Optional[str] = None


class IndexKnowledgeBaseSharePointRequest(CronExpressionValidatorMixin, IndexKnowledgeBaseRequest):
    site_url: str
    path_filter: Optional[str] = "*"
    include_pages: Optional[bool] = True
    include_documents: Optional[bool] = True
    include_lists: Optional[bool] = True
    max_file_size_mb: Optional[int] = Field(default=50, gt=0, le=500)
    setting_id: Optional[str] = None
    embedding_model: Optional[str] = None
    cron_expression: Optional[str] = None
    files_filter: Optional[str] = ""  # Gitignore-style extension/name filter for documents
    auth_type: Literal["integration", "oauth_codemie", "oauth_custom"] = "integration"
    access_token: Optional[str] = None  # OAuth delegated token (not stored)
    oauth_client_id: Optional[str] = None  # Custom Azure app client ID (oauth_custom only)
    oauth_tenant_id: Optional[str] = None  # Azure AD tenant ID for single-tenant custom apps

    @field_validator("site_url")
    @classmethod
    def validate_site_url(cls, v: str) -> str:
        if not v.startswith(_HTTPS_SCHEME):
            raise ValueError(_SITE_URL_SCHEME_ERROR)
        return v


class IndexKnowledgeBaseGoogleRequest(CronExpressionValidatorMixin, IndexKnowledgeBaseRequest):
    googleDoc: str
    embedding_model: Optional[str] = None
    cron_expression: Optional[str] = None


class UpdateIndexRequest(CronExpressionValidatorMixin, BaseModel):
    name: Optional[str] = None
    description: Optional[str] = Field(max_length=500, default="")
    prompt: Optional[str] = None
    embeddingsModel: Optional[str] = None
    projectSpaceVisible: Optional[bool] = True
    docsGeneration: Optional[bool] = False
    branch: Optional[str] = None
    link: Optional[str] = None
    filesFilter: Optional[str] = Field(default="")
    setting_id: Optional[str] = None
    project_name: Optional[str] = None
    new_project_name: Optional[str] = None  # New field to support project change
    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None
    cron_expression: Optional[str] = None


class ReIndexKnowledgeBaseRequest(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    project_name: str
    description: Optional[str] = None
    project_space_visible: Optional[bool] = None


class UpdateKnowledgeBaseGoogleRequest(CronExpressionValidatorMixin, BaseModel):
    name: str = Field(min_length=1, max_length=500)
    project_name: str
    description: Optional[str] = None
    project_space_visible: Optional[bool] = None
    new_project_name: Optional[str] = None  # Field to support project change
    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None
    cron_expression: Optional[str] = None


class UpdateKnowledgeBaseFilesRequest(BaseModel):
    name: str = Field(min_length=1, max_length=500)
    project_name: str
    description: Optional[str] = None
    project_space_visible: Optional[bool] = None
    new_project_name: Optional[str] = None  # Field to support project change
    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None


class UpdateKnowledgeBaseConfluenceRequest(CronExpressionValidatorMixin, BaseModel):
    name: str = Field(min_length=1, max_length=500)
    project_name: str
    description: Optional[str] = None
    project_space_visible: Optional[bool] = None
    cql: Optional[str] = None
    setting_id: Optional[str] = None
    new_project_name: Optional[str] = None  # Field to support project change
    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None
    cron_expression: Optional[str] = None


class UpdateKnowledgeBaseJiraRequest(CronExpressionValidatorMixin, BaseModel):
    name: str = Field(min_length=1, max_length=500)
    jql: str
    project_name: str
    setting_id: Optional[str] = None
    new_project_name: Optional[str] = None  # Field to support project change

    description: str = Field(default="", max_length=500)
    project_space_visible: Optional[bool] = None
    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None
    cron_expression: Optional[str] = None


class UpdateKnowledgeBaseXrayRequest(CronExpressionValidatorMixin, BaseModel):
    name: str = Field(min_length=1, max_length=500)
    jql: str
    project_name: str
    setting_id: Optional[str] = None
    new_project_name: Optional[str] = None  # Field to support project change

    description: str = Field(default="", max_length=500)
    project_space_visible: Optional[bool] = None
    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None
    cron_expression: Optional[str] = None


class UpdateKnowledgeBaseAzureDevOpsWikiRequest(CronExpressionValidatorMixin, BaseModel):
    name: str = Field(min_length=1, max_length=500)
    wiki_query: str = ''
    wiki_name: Optional[str] = None
    project_name: str
    setting_id: Optional[str] = None
    new_project_name: Optional[str] = None  # Field to support project change

    description: str = Field(default="", max_length=500)
    project_space_visible: Optional[bool] = None
    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None
    cron_expression: Optional[str] = None


class UpdateKnowledgeBaseAzureDevOpsWorkItemRequest(CronExpressionValidatorMixin, BaseModel):
    name: str = Field(min_length=1, max_length=500)
    wiql_query: str = ""
    project_name: str
    setting_id: Optional[str] = None
    new_project_name: Optional[str] = None  # Field to support project change

    description: str = Field(default="", max_length=500)
    project_space_visible: Optional[bool] = None
    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None
    cron_expression: Optional[str] = None


class UpdateKnowledgeBaseSharePointRequest(CronExpressionValidatorMixin, BaseModel):
    name: str = Field(min_length=1, max_length=500)
    project_name: str
    site_url: Optional[str] = None
    path_filter: Optional[str] = None
    include_pages: Optional[bool] = None
    include_documents: Optional[bool] = None
    include_lists: Optional[bool] = None
    max_file_size_mb: Optional[int] = Field(default=None, gt=0, le=500)
    setting_id: Optional[str] = None
    new_project_name: Optional[str] = None  # Field to support project change
    description: Optional[str] = None
    project_space_visible: Optional[bool] = None
    embedding_model: Optional[str] = None
    guardrail_assignments: Optional[List[GuardrailAssignmentItem]] = None
    cron_expression: Optional[str] = None
    files_filter: Optional[str] = Field(
        default=None,
        description=(
            "Gitignore-style filter for documents. "
            "Omit or set to null to keep the stored value; set to empty string to clear the filter."
        ),
    )
    auth_type: Optional[Literal["integration", "oauth_codemie", "oauth_custom"]] = None
    access_token: Optional[str] = None  # OAuth delegated token for this reindex run
    oauth_client_id: Optional[str] = None  # Custom Azure app client ID (oauth_custom only)
    oauth_tenant_id: Optional[str] = None  # Azure AD tenant ID for single-tenant custom apps

    @field_validator("site_url")
    @classmethod
    def validate_site_url(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith(_HTTPS_SCHEME):
            raise ValueError(_SITE_URL_SCHEME_ERROR)
        return v


class IndexKnowledgeBaseFileTypes(Enum):
    PDF = 'pdf'
    TXT = 'txt'
    CSV = 'csv'
    XML = 'xml'
    PPTX = 'pptx'
    DOCX = 'docx'
    XLSX = 'xlsx'
    HTML = 'html'
    EPUB = 'epub'
    IPYNB = 'ipynb'
    MSG = 'msg'
    YAML = 'yaml'
    YML = 'yml'
    JSON = 'json'
    ZIP = 'zip'
    AUDIO = 'mp3'
    IMAGE = 'jpg'

    @classmethod
    def values(cls):
        return [item.value for item in cls]


class IndexKnowledgeBaseFileRequest(IndexKnowledgeBaseRequest):
    files: List[UploadFile]
    csv_separator: Optional[str] = None
    csv_start_row: Optional[int] = None
    csv_rows_per_document: Optional[int] = 1
    embedding_model: Optional[str] = None
    guardrail_assignments: Optional[str] = None  # json encoded list of GuardrailAssignmentItem

    MIN_FILE_COUNT: ClassVar[int] = 1
    MAX_FILE_COUNT: ClassVar[int] = 10

    @field_validator("files")
    def validate_files_count(cls, files):
        if len(files) < cls.MIN_FILE_COUNT:
            raise RequestValidationError(
                [
                    {
                        "loc": ["files"],
                        "msg": f"Too few files. Minimum count is {cls.MIN_FILE_COUNT}",
                        "type": "value_error",
                    }
                ]
            )

        if len(files) > cls.MAX_FILE_COUNT:
            raise RequestValidationError(
                [
                    {
                        "loc": ["files"],
                        "msg": f"Too many files. Maximum count is {cls.MAX_FILE_COUNT}",
                        "type": "value_error",
                    }
                ]
            )
        return files

    @staticmethod
    def check_json_fields(loaded_content: List[Dict[str, str]]):
        for d in loaded_content:
            if "content" not in d:
                raise KeyError("missing 'content' key")
            if "metadata" not in d:
                raise KeyError("missing 'metadata' key")

    @field_validator("files")
    def validate_files_sizes(cls, files):
        for file in files:
            if file.size > config.FILES_STORAGE_MAX_UPLOAD_SIZE:
                raise RequestValidationError(
                    [
                        {
                            "loc": ["files"],
                            "msg": f"File too large. Maximum size is {config.FILES_STORAGE_MAX_UPLOAD_SIZE} bytes",
                            "type": "value_error",
                        }
                    ]
                )

        return files


class GetIndexInfoIDResponse(BaseModel):
    id: str
