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

"""SharePoint datasource processor for indexing SharePoint Online content."""

from __future__ import annotations

from dataclasses import dataclass, field

import requests
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from codemie.configs import logger
from codemie.core.models import KnowledgeBase
from codemie.datasource.base_datasource_processor import (
    BaseDatasourceProcessor,
    DatasourceProcessorCallback,
)
from codemie.datasource.datasources_config import SHAREPOINT_CONFIG
from codemie.datasource.exceptions import (
    InvalidQueryException,
    MissingIntegrationException,
    UnauthorizedException,
)
from codemie.datasource.loader.sharepoint_loader import SharePointAuthConfig, SharePointLoader
from codemie.rest_api.models.guardrail import GuardrailAssignmentItem
from codemie.rest_api.models.index import IndexInfo, SharePointIndexInfo
from codemie.rest_api.models.settings import SharePointCredentials
from codemie.rest_api.security.user import User
from codemie.service.encryption.encryption_factory import EncryptionFactory
from codemie.service.llm_service.llm_service import llm_service


def _encrypt_oauth_token(token: str) -> str:
    """Encrypt an OAuth bearer token before persisting to the database."""
    if not token:
        return ""
    return EncryptionFactory.get_current_encryption_service().encrypt(token)


def _decrypt_oauth_token(encrypted_token: str) -> str:
    """Decrypt an OAuth bearer token retrieved from the database."""
    if not encrypted_token:
        return ""
    return EncryptionFactory.get_current_encryption_service().decrypt(encrypted_token)


@dataclass
class SharePointProcessorConfig:
    """Content and metadata configuration for SharePoint datasource processor."""

    site_url: str
    path_filter: str = "*"
    include_pages: bool = True
    include_documents: bool = True
    include_lists: bool = True
    max_file_size_mb: int = 50
    files_filter: str = field(default="")
    auth_type: str = "integration"
    oauth_client_id: str | None = None
    oauth_tenant_id: str | None = None
    description: str = field(default="")
    project_space_visible: bool = False


class SharePointDatasourceProcessor(BaseDatasourceProcessor):
    """
    Processor for SharePoint Online datasources.

    Handles indexing of SharePoint site pages, documents, and lists using
    Microsoft Graph API with Azure AD authentication.
    """

    INDEX_TYPE = "knowledge_base_sharepoint"

    def __init__(
        self,
        *,
        datasource_name: str,
        user: User,
        project_name: str,
        credentials: SharePointCredentials,
        sp_config: SharePointProcessorConfig,
        setting_id: str | None = None,
        embedding_model: str | None = None,
        cron_expression: str | None = None,
        index_info: IndexInfo | None = None,
        callbacks: list[DatasourceProcessorCallback] | None = None,
        request_uuid: str | None = None,
        guardrail_assignments: list[GuardrailAssignmentItem] | None = None,
    ):
        """
        Initialize SharePoint datasource processor.

        Args:
            datasource_name: Name of the datasource
            user: User performing the indexing
            project_name: Project name
            credentials: SharePoint credentials (tenant_id, client_id, client_secret)
            sp_config: SharePoint content and metadata configuration
            setting_id: Settings identifier for credential lookup
            embedding_model: Embedding model override
            cron_expression: Cron expression for scheduled reindexing
            index_info: Existing index info (for updates)
            callbacks: Processing callbacks
            request_uuid: Request UUID for tracking
            guardrail_assignments: Guardrail assignments
        """
        self.project_name = project_name
        self.description = sp_config.description
        self.credentials = credentials
        self.site_url = sp_config.site_url
        self.path_filter = sp_config.path_filter
        self.include_pages = sp_config.include_pages
        self.include_documents = sp_config.include_documents
        self.include_lists = sp_config.include_lists
        self.max_file_size_mb = sp_config.max_file_size_mb
        self.files_filter = sp_config.files_filter
        self.auth_type = sp_config.auth_type
        self.oauth_client_id = sp_config.oauth_client_id
        self.oauth_tenant_id = sp_config.oauth_tenant_id
        self.setting_id = setting_id
        self.embedding_model = embedding_model
        self.project_space_visible = sp_config.project_space_visible

        # Log content type configuration
        logger.info(
            f"SharePointDatasourceProcessor initialized for '{datasource_name}' - "
            f"include_pages={sp_config.include_pages}, "
            f"include_documents={sp_config.include_documents}, "
            f"include_lists={sp_config.include_lists}"
        )

        super().__init__(
            datasource_name=datasource_name,
            user=user,
            index=index_info,
            callbacks=callbacks,
            request_uuid=request_uuid,
            guardrail_assignments=guardrail_assignments,
            cron_expression=cron_expression,
        )

    @property
    def _index_name(self) -> str:
        """Get the Elasticsearch index name."""
        return KnowledgeBase(name=f"{self.project_name}-{self.datasource_name}", type=self.INDEX_TYPE).get_identifier()

    @property
    def _processing_batch_size(self) -> int:
        """Get the processing batch size."""
        return SHAREPOINT_CONFIG.loader_batch_size

    def _init_loader(self):
        """Initialize the SharePoint loader."""
        modified_since = None
        if self.is_incremental_reindex and self.index:
            modified_since = self.index.last_reindex_date or self.index.update_date
        return SharePointLoader(
            site_url=self.site_url,
            path_filter=self.path_filter,
            auth_config=SharePointAuthConfig(
                auth_type=self.credentials.auth_type,
                tenant_id=self.credentials.tenant_id or "",
                client_id=self.credentials.client_id or "",
                client_secret=self.credentials.client_secret or "",
                access_token=self.credentials.access_token or "",
                refresh_token=self.credentials.refresh_token or "",
                expires_at=self.credentials.expires_at or 0,
                setting_id=self.setting_id,
            ),
            include_pages=self.include_pages,
            include_documents=self.include_documents,
            include_lists=self.include_lists,
            max_file_size_mb=self.max_file_size_mb,
            files_filter=self.files_filter,
            request_uuid=self.request_uuid,
            modified_since=modified_since,
        )

    def _cleanup_data_for_incremental_reindex(self, docs_to_be_indexed: list[Document]) -> None:
        """Delete existing index chunks for documents that have changed, so they get re-indexed cleanly."""
        sources = [doc.metadata["source"] for doc in docs_to_be_indexed if doc.metadata.get("source")]
        if not sources:
            return
        try:
            self.client.delete_by_query(
                index=self._index_name,
                body={"query": {"terms": {"metadata.source.keyword": sources}}},
                wait_for_completion=True,
                refresh=True,
            )
            logger.info(f"Incremental reindex: removed stale chunks for {len(sources)} SharePoint sources")
        except Exception as e:
            logger.error(f"Incremental reindex: failed to delete stale chunks: {e}")

    def _init_index(self):
        """Initialize or retrieve the index configuration."""
        if not self.index:
            self.index = IndexInfo.new(
                repo_name=self.datasource_name,
                full_name=self.datasource_name,
                project_name=self.project_name,
                description=self.description,
                project_space_visible=self.project_space_visible,
                index_type=self.INDEX_TYPE,
                user=self.user,
                sharepoint=SharePointIndexInfo(
                    site_url=self.site_url,
                    path_filter=self.path_filter,
                    include_pages=self.include_pages,
                    include_documents=self.include_documents,
                    include_lists=self.include_lists,
                    max_file_size_mb=self.max_file_size_mb,
                    files_filter=self.files_filter,
                    auth_type=self.auth_type,
                    oauth_client_id=self.oauth_client_id,
                    oauth_tenant_id=self.oauth_tenant_id,
                    access_token=_encrypt_oauth_token(self.credentials.access_token or ""),
                    expires_at=self.credentials.expires_at or 0,
                ),
                embeddings_model=self.embedding_model or llm_service.default_embedding_model,
                setting_id=self.setting_id,
            )
        else:
            # Update existing index configuration with current values
            if not self.index.sharepoint:
                # Create SharePoint config if it doesn't exist (shouldn't happen, but defensive)
                self.index.sharepoint = SharePointIndexInfo(
                    site_url=self.site_url,
                    path_filter=self.path_filter,
                    include_pages=self.include_pages,
                    include_documents=self.include_documents,
                    include_lists=self.include_lists,
                    max_file_size_mb=self.max_file_size_mb,
                    files_filter=self.files_filter,
                    auth_type=self.auth_type,
                    oauth_client_id=self.oauth_client_id,
                    oauth_tenant_id=self.oauth_tenant_id,
                    access_token=_encrypt_oauth_token(self.credentials.access_token or ""),
                    expires_at=self.credentials.expires_at or 0,
                )
            else:
                # Update existing SharePoint config
                self.index.sharepoint.site_url = self.site_url
                self.index.sharepoint.path_filter = self.path_filter
                self.index.sharepoint.include_pages = self.include_pages
                self.index.sharepoint.include_documents = self.include_documents
                self.index.sharepoint.include_lists = self.include_lists
                self.index.sharepoint.max_file_size_mb = self.max_file_size_mb
                self.index.sharepoint.files_filter = self.files_filter
                self.index.sharepoint.auth_type = self.auth_type
                self.index.sharepoint.oauth_client_id = self.oauth_client_id
                self.index.sharepoint.oauth_tenant_id = self.oauth_tenant_id
                self.index.sharepoint.access_token = _encrypt_oauth_token(self.credentials.access_token or "")
                self.index.sharepoint.expires_at = self.credentials.expires_at or 0

        self._assign_and_sync_guardrails()

    def _process_chunk(self, chunk: str, chunk_metadata, document: Document) -> Document:
        """
        Process a document chunk and add metadata.

        Args:
            chunk: Chunk text
            chunk_metadata: Chunk metadata
            document: Original document

        Returns:
            Document with metadata
        """
        return Document(
            page_content=chunk,
            metadata={
                "source": document.metadata.get("source", ""),
                "title": document.metadata.get("title", ""),
                "type": document.metadata.get("type", ""),
            },
        )

    @classmethod
    def _get_splitter(cls, document: Document | None = None) -> RecursiveCharacterTextSplitter:
        """
        Get the text splitter for chunking documents.

        Args:
            document: Optional document (unused)

        Returns:
            Text splitter instance
        """
        return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name="o200k_base",
            chunk_size=SHAREPOINT_CONFIG.chunk_size,
            disallowed_special={},
            chunk_overlap=SHAREPOINT_CONFIG.chunk_overlap,
        )

    def _on_process_end(self):
        """Clear stored OAuth token after indexing completes to avoid persisting sensitive data."""
        if (
            self.index
            and self.index.sharepoint
            and self.index.sharepoint.auth_type in ("oauth_codemie", "oauth_custom")
        ):
            self.index.sharepoint.access_token = ""
            self.index.sharepoint.expires_at = 0
            self.index.update()

    @classmethod
    def validate_creds_and_loader(
        cls,
        site_url: str,
        path_filter: str,
        credentials: SharePointCredentials,
        include_pages: bool = True,
        include_documents: bool = True,
        include_lists: bool = True,
    ) -> dict[str, int]:
        """
        Validate credentials and return stats.

        Args:
            site_url: SharePoint site URL
            path_filter: Path filter
            credentials: SharePoint credentials
            include_pages: Whether to include site pages
            include_documents: Whether to include documents
            include_lists: Whether to include list items

        Returns:
            Dictionary with document count

        Raises:
            InvalidQueryException: If connection fails
        """
        loader = SharePointLoader(
            site_url=site_url,
            path_filter=path_filter,
            auth_config=SharePointAuthConfig(
                auth_type=credentials.auth_type,
                tenant_id=credentials.tenant_id or "",
                client_id=credentials.client_id or "",
                client_secret=credentials.client_secret or "",
                access_token=credentials.access_token or "",
                refresh_token=credentials.refresh_token or "",
                expires_at=credentials.expires_at or 0,
            ),
            include_pages=include_pages,
            include_documents=include_documents,
            include_lists=include_lists,
        )
        try:
            stats = loader.fetch_remote_stats()
            return stats
        except UnauthorizedException as e:
            # Authentication failed - already has user-friendly message
            logger.error(f"SharePoint authentication failed for {site_url}: {e}")
            raise
        except MissingIntegrationException as e:
            # Missing credentials - already has user-friendly message
            logger.error(f"SharePoint credentials missing for {site_url}: {e}")
            raise
        except requests.exceptions.RequestException as e:
            # Network/HTTP errors - convert to InvalidQueryException
            logger.error(f"SharePoint connection error for {site_url}: {e}")
            raise InvalidQueryException("SharePoint Connection", str(e))

    @classmethod
    def check_sharepoint_connection(
        cls,
        credentials: SharePointCredentials,
        site_url: str,
        path_filter: str = "*",
        include_pages: bool = True,
        include_documents: bool = True,
        include_lists: bool = True,
        setting_id: str | None = None,
    ) -> None:
        """
        Validate SharePoint connection with a lightweight site-accessibility check.

        Performs a single API call to verify credentials and site URL are valid.
        Does not traverse files or count documents, so it returns quickly regardless
        of how many files the site contains.

        Note: For oauth auth_type, only the presence of a stored access token is
        checked — token expiry is not validated here and will surface at indexing time.

        Args:
            credentials: SharePoint credentials
            site_url: SharePoint site URL
            path_filter: Path filter (stored for indexing, not validated here)
            include_pages: Whether to include site pages
            include_documents: Whether to include documents
            include_lists: Whether to include list items
            setting_id: OAuth setting ID used for token refresh (oauth auth types only)

        Raises:
            InvalidQueryException: If site URL is missing or connection fails
            UnauthorizedException: If authentication fails
            MissingIntegrationException: If credentials are missing
        """
        if not site_url or not site_url.strip():
            raise InvalidQueryException("SharePoint Site URL", "Site URL is required")

        loader = SharePointLoader(
            site_url=site_url,
            path_filter=path_filter,
            auth_config=SharePointAuthConfig(
                auth_type=credentials.auth_type,
                tenant_id=credentials.tenant_id or "",
                client_id=credentials.client_id or "",
                client_secret=credentials.client_secret or "",
                access_token=credentials.access_token or "",
                refresh_token=credentials.refresh_token or "",
                expires_at=credentials.expires_at or 0,
                setting_id=setting_id,
            ),
            include_pages=include_pages,
            include_documents=include_documents,
            include_lists=include_lists,
        )
        try:
            loader.validate_connection()
        except UnauthorizedException as e:
            logger.error(f"SharePoint authentication failed for {site_url}: {e}")
            raise
        except MissingIntegrationException as e:
            logger.error(f"SharePoint credentials missing for {site_url}: {e}")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"SharePoint connection error for {site_url}: {e}")
            raise InvalidQueryException("SharePoint Connection", str(e))
