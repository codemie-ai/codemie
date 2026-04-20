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

"""Tests for SharePoint datasource processor."""

import pytest
from contextlib import suppress
from unittest.mock import MagicMock, patch
from datetime import datetime

from langchain_core.documents import Document

from codemie.datasource.sharepoint.sharepoint_datasource_processor import (
    SharePointDatasourceProcessor,
    SharePointProcessorConfig,
)
from codemie.rest_api.models.index import SharePointIndexInfo
from codemie.core.models import CreatedByUser
from codemie.rest_api.security.user import User
from codemie.rest_api.models.settings import SharePointCredentials
from codemie.datasource.callback.base_datasource_callback import (
    DatasourceProcessorCallback,
)


@pytest.fixture
def sharepoint_credentials():
    """Create SharePoint credentials for testing."""
    return SharePointCredentials(
        tenant_id="test-tenant-id",
        client_id="test-client-id",
        client_secret="test-client-secret",
    )


@pytest.fixture
def sharepoint_processor(sharepoint_credentials):
    """Create SharePoint processor instance for testing."""
    user = User(id="1", username="testuser")
    created_by = CreatedByUser(id="1", username="testuser")
    datasource_name = "test_sharepoint_ds"
    project_name = "test_project"
    description = "Test SharePoint Description"
    site_url = "https://tenant.sharepoint.com/sites/testsite"
    index_type = "knowledge_base_sharepoint"
    setting_id = "setting_id"

    index_info = MagicMock()
    index_info.repo_name = datasource_name
    index_info.full_name = "Test SharePoint Datasource"
    index_info.project_name = project_name
    index_info.description = description
    index_info.project_space_visible = True
    index_info.index_type = index_type
    index_info.current_state = 0
    index_info.error = False
    index_info.completed = False
    index_info.created_by = created_by
    index_info.sharepoint = SharePointIndexInfo(
        site_url=site_url,
        include_pages=True,
        include_documents=True,
        include_lists=True,
        max_file_size_mb=50,
    )
    index_info.update_date = datetime.now()
    index_info.start_progress = MagicMock()
    index_info.complete_progress = MagicMock()
    index_info.set_error = MagicMock()

    return SharePointDatasourceProcessor(
        datasource_name=datasource_name,
        user=user,
        project_name=project_name,
        credentials=sharepoint_credentials,
        sp_config=SharePointProcessorConfig(
            site_url=site_url,
            include_pages=True,
            include_documents=True,
            include_lists=True,
            max_file_size_mb=50,
            description=description,
        ),
        index_info=index_info,
        setting_id=setting_id,
    )


class TestSharePointProcessorInit:
    """Test SharePoint processor initialization."""

    def test_init_with_all_params(self, sharepoint_processor, sharepoint_credentials):
        """Test initialization with all parameters."""
        assert sharepoint_processor.datasource_name == "test_sharepoint_ds"
        assert sharepoint_processor.project_name == "test_project"
        assert sharepoint_processor.credentials == sharepoint_credentials
        assert sharepoint_processor.site_url == "https://tenant.sharepoint.com/sites/testsite"
        assert sharepoint_processor.include_pages is True
        assert sharepoint_processor.include_documents is True
        assert sharepoint_processor.include_lists is True
        assert sharepoint_processor.max_file_size_mb == 50
        assert sharepoint_processor.index.repo_name == "test_sharepoint_ds"

    def test_index_type(self, sharepoint_processor):
        """Test index type constant."""
        assert sharepoint_processor.INDEX_TYPE == "knowledge_base_sharepoint"

    def test_index_name_property(self, sharepoint_processor):
        """Test index name property."""
        from codemie.core.models import KnowledgeBase

        expected_index_name = KnowledgeBase(
            name=f"{sharepoint_processor.project_name}-{sharepoint_processor.datasource_name}",
            type=sharepoint_processor.INDEX_TYPE,
        ).get_identifier()
        assert sharepoint_processor._index_name == expected_index_name

    def test_processing_batch_size(self, sharepoint_processor):
        """Test processing batch size property."""
        assert sharepoint_processor._processing_batch_size > 0


class TestSharePointProcessorLoader:
    """Test SharePoint processor loader initialization."""

    def test_init_loader(self, sharepoint_processor):
        """Test loader initialization."""
        with patch("codemie.datasource.sharepoint.sharepoint_datasource_processor.SharePointLoader") as mock_loader:
            sharepoint_processor._init_loader()

        mock_loader.assert_called_once()
        args, kwargs = mock_loader.call_args
        assert kwargs["site_url"] == "https://tenant.sharepoint.com/sites/testsite"
        auth_config = kwargs["auth_config"]
        assert auth_config.tenant_id == "test-tenant-id"
        assert auth_config.client_id == "test-client-id"
        assert auth_config.client_secret == "test-client-secret"
        assert kwargs["include_pages"] is True
        assert kwargs["include_documents"] is True
        assert kwargs["include_lists"] is True
        assert kwargs["max_file_size_mb"] == 50


class TestSharePointProcessorConnectionValidation:
    """Test SharePoint connection validation."""

    @patch("codemie.datasource.sharepoint.sharepoint_datasource_processor.SharePointLoader")
    def test_validate_creds_and_loader_success(self, mock_loader_class, sharepoint_credentials):
        """Test successful credential validation."""
        mock_loader = MagicMock()
        mock_loader.fetch_remote_stats.return_value = {
            "documents_count_key": 10,
            "total_documents": 10,
            "skipped_documents": 0,
        }
        mock_loader_class.return_value = mock_loader

        result = SharePointDatasourceProcessor.validate_creds_and_loader(
            site_url="https://tenant.sharepoint.com/sites/testsite",
            credentials=sharepoint_credentials,
            include_pages=True,
            include_documents=True,
            include_lists=True,
        )

        assert result["documents_count_key"] == 10
        mock_loader.fetch_remote_stats.assert_called_once()

    @patch("codemie.datasource.sharepoint.sharepoint_datasource_processor.SharePointLoader")
    def test_validate_creds_and_loader_auth_error(self, mock_loader_class, sharepoint_credentials):
        """Test credential validation with authentication error."""
        from codemie.datasource.exceptions import UnauthorizedException

        mock_loader = MagicMock()
        mock_loader.fetch_remote_stats.side_effect = UnauthorizedException("SharePoint")
        mock_loader_class.return_value = mock_loader

        with pytest.raises(UnauthorizedException):
            SharePointDatasourceProcessor.validate_creds_and_loader(
                site_url="https://tenant.sharepoint.com/sites/testsite",
                credentials=sharepoint_credentials,
                include_pages=True,
                include_documents=True,
                include_lists=True,
            )

    @patch("codemie.datasource.sharepoint.sharepoint_datasource_processor.SharePointLoader")
    def test_check_sharepoint_connection_success(self, mock_loader_class, sharepoint_credentials):
        """Test successful connection check calls validate_connection and returns None."""
        mock_loader = MagicMock()
        mock_loader.validate_connection.return_value = None
        mock_loader_class.return_value = mock_loader

        result = SharePointDatasourceProcessor.check_sharepoint_connection(
            credentials=sharepoint_credentials,
            site_url="https://tenant.sharepoint.com/sites/testsite",
            include_pages=True,
            include_documents=True,
            include_lists=True,
        )

        assert result is None
        mock_loader.validate_connection.assert_called_once()

    def test_check_sharepoint_connection_empty_url(self, sharepoint_credentials):
        """Test connection check with empty URL."""
        from codemie.datasource.exceptions import InvalidQueryException

        with pytest.raises(InvalidQueryException):
            SharePointDatasourceProcessor.check_sharepoint_connection(
                credentials=sharepoint_credentials,
                site_url="",
            )

    @patch("codemie.datasource.sharepoint.sharepoint_datasource_processor.SharePointLoader")
    def test_check_sharepoint_connection_auth_failure(self, mock_loader_class, sharepoint_credentials):
        """Test connection check propagates UnauthorizedException from validate_connection."""
        from codemie.datasource.exceptions import UnauthorizedException

        mock_loader = MagicMock()
        mock_loader.validate_connection.side_effect = UnauthorizedException("SharePoint")
        mock_loader_class.return_value = mock_loader

        with pytest.raises(UnauthorizedException):
            SharePointDatasourceProcessor.check_sharepoint_connection(
                credentials=sharepoint_credentials,
                site_url="https://tenant.sharepoint.com/sites/testsite",
            )


class TestSharePointProcessorIndex:
    """Test SharePoint processor index operations."""

    @patch("codemie.datasource.sharepoint.sharepoint_datasource_processor.llm_service")
    @patch("codemie.rest_api.models.index.IndexInfo.new")
    def test_init_index(self, mock_index_new, mock_llm_service, sharepoint_processor):
        """Test index initialization."""
        mock_llm_service.default_embedding_model = "text-embedding-3-small"

        mock_index = MagicMock()
        mock_index.repo_name = "test_sharepoint_ds"
        mock_index.project_name = "test_project"
        mock_index.index_type = "knowledge_base_sharepoint"
        mock_index.sharepoint = MagicMock()
        mock_index.sharepoint.site_url = "https://tenant.sharepoint.com/sites/testsite"
        mock_index_new.return_value = mock_index

        processor = sharepoint_processor
        processor.index = None

        with patch.object(processor, "_assign_and_sync_guardrails") as mock_assign:
            processor._init_index()

        assert processor.index is not None
        assert processor.index.repo_name == "test_sharepoint_ds"
        assert processor.index.project_name == "test_project"
        assert processor.index.index_type == "knowledge_base_sharepoint"
        mock_assign.assert_called_once()
        mock_index_new.assert_called_once()


class TestSharePointProcessorProcessing:
    """Test SharePoint processor document processing."""

    @patch("codemie.datasource.base_datasource_processor.ensure_application_exists")
    def test_process_success(self, mock_ensure_app, sharepoint_processor):
        """Test successful processing."""
        processor = sharepoint_processor
        processor._init_loader = MagicMock()
        processor._init_loader.return_value.fetch_remote_stats.return_value = {
            "documents_count_key": 5,
            "total_documents": 5,
            "skipped_documents": 0,
        }
        processor._init_loader.return_value.lazy_load.return_value = [
            Document(
                page_content="Page content",
                metadata={
                    "source": "https://tenant.sharepoint.com/sites/testsite/page",
                    "title": "Test Page",
                    "type": "page",
                },
            )
        ]
        processor._get_store_by_index = MagicMock()
        processor._get_store_by_index.return_value._store._create_index_if_not_exists = MagicMock()
        processor._get_store_by_index.return_value.add_documents = MagicMock()
        processor._get_splitter = MagicMock()
        processor._get_splitter.return_value.split_text = MagicMock(return_value=["chunk1", "chunk2"])
        processor._cleanup_data = MagicMock()

        processor._validate_index_and_get_guardrails_for_index = MagicMock(return_value=(processor.index, []))

        mock_callback = MagicMock(spec=DatasourceProcessorCallback)
        with patch(
            "codemie.datasource.base_datasource_processor.DatasourceMonitoringCallback",
            return_value=mock_callback,
        ):
            processor.process()

        mock_callback.on_complete.assert_called_once()
        mock_callback.on_error.assert_not_called()
        processor.index.start_progress.assert_called_once()
        processor.index.complete_progress.assert_called_once()
        mock_ensure_app.assert_called_once_with("test_project")

    @patch("codemie.datasource.base_datasource_processor.ensure_application_exists")
    def test_process_failure(self, mock_ensure_app, sharepoint_processor):
        """Test processing failure."""
        processor = sharepoint_processor
        processor._init_loader = MagicMock()
        processor._init_loader.return_value.fetch_remote_stats.side_effect = Exception("Failed to fetch stats")

        mock_callback = MagicMock(spec=DatasourceProcessorCallback)
        with patch(
            "codemie.datasource.base_datasource_processor.DatasourceMonitoringCallback",
            return_value=mock_callback,
        ):
            with suppress(Exception):
                processor.process()

        mock_callback.on_complete.assert_not_called()
        mock_callback.on_error.assert_called_once()
        processor.index.complete_progress.assert_not_called()
        processor.index.set_error.assert_called_once()

    @patch("codemie.datasource.base_datasource_processor.ensure_application_exists")
    def test_full_reindex(self, mock_ensure_app, sharepoint_processor):
        """Test full reindex process."""
        processor = sharepoint_processor
        processor._init_loader = MagicMock()
        processor._init_loader.return_value.fetch_remote_stats.return_value = {
            "documents_count_key": 5,
            "total_documents": 5,
            "skipped_documents": 0,
        }
        processor._init_loader.return_value.lazy_load.return_value = [
            Document(
                page_content="Document content",
                metadata={
                    "source": "https://tenant.sharepoint.com/sites/testsite/doc.pdf",
                    "title": "Test Document",
                    "type": "document",
                },
            )
        ]
        processor._get_store_by_index = MagicMock()
        processor._get_store_by_index.return_value._store._create_index_if_not_exists = MagicMock()
        processor._get_store_by_index.return_value.add_documents = MagicMock()
        processor._get_splitter = MagicMock()
        processor._get_splitter.return_value.split_text = MagicMock(return_value=["chunk1", "chunk2"])
        processor._cleanup_data = MagicMock()

        processor._validate_index_and_get_guardrails_for_index = MagicMock(return_value=(processor.index, []))

        mock_callback = MagicMock(spec=DatasourceProcessorCallback)
        with patch(
            "codemie.datasource.base_datasource_processor.DatasourceMonitoringCallback",
            return_value=mock_callback,
        ):
            processor.reprocess()

        mock_callback.on_complete.assert_called_once()
        mock_callback.on_error.assert_not_called()
        processor.index.start_progress.assert_called_once()
        processor.index.complete_progress.assert_called_once()
        processor._cleanup_data.assert_called_once()
        assert processor.is_full_reindex


class TestSharePointProcessorSplitter:
    """Test SharePoint processor text splitter."""

    def test_get_splitter(self):
        """Test getting text splitter."""
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        splitter = SharePointDatasourceProcessor._get_splitter()
        assert isinstance(splitter, RecursiveCharacterTextSplitter)
        assert splitter._chunk_size == 2000
        assert splitter._chunk_overlap == 200


class TestSharePointProcessorCleanup:
    """Test SharePoint processor cleanup."""

    def test_cleanup_data(self, sharepoint_processor):
        """Test cleanup data uses base implementation."""
        processor = sharepoint_processor
        processor.client = MagicMock()

        processor._cleanup_data()

        # SharePoint uses base implementation which deletes entire index
        processor.client.indices.delete.assert_called_once_with(index=processor._index_name)


class TestSharePointProcessorContentTypeFilters:
    """Test content type filtering."""

    def test_include_only_pages(self, sharepoint_credentials):
        """Test processor with only pages enabled."""
        user = User(id="1", username="testuser")
        processor = SharePointDatasourceProcessor(
            datasource_name="test_ds",
            user=user,
            project_name="test_project",
            credentials=sharepoint_credentials,
            sp_config=SharePointProcessorConfig(
                site_url="https://tenant.sharepoint.com/sites/testsite",
                include_pages=True,
                include_documents=False,
                include_lists=False,
            ),
        )

        assert processor.include_pages is True
        assert processor.include_documents is False
        assert processor.include_lists is False

    def test_include_only_documents(self, sharepoint_credentials):
        """Test processor with only documents enabled."""
        user = User(id="1", username="testuser")
        processor = SharePointDatasourceProcessor(
            datasource_name="test_ds",
            user=user,
            project_name="test_project",
            credentials=sharepoint_credentials,
            sp_config=SharePointProcessorConfig(
                site_url="https://tenant.sharepoint.com/sites/testsite",
                include_pages=False,
                include_documents=True,
                include_lists=False,
            ),
        )

        assert processor.include_pages is False
        assert processor.include_documents is True
        assert processor.include_lists is False

    def test_include_only_lists(self, sharepoint_credentials):
        """Test processor with only lists enabled."""
        user = User(id="1", username="testuser")
        processor = SharePointDatasourceProcessor(
            datasource_name="test_ds",
            user=user,
            project_name="test_project",
            credentials=sharepoint_credentials,
            sp_config=SharePointProcessorConfig(
                site_url="https://tenant.sharepoint.com/sites/testsite",
                include_pages=False,
                include_documents=False,
                include_lists=True,
            ),
        )

        assert processor.include_pages is False
        assert processor.include_documents is False
        assert processor.include_lists is True


class TestSharePointProcessorIndexUpdate:
    """Test _init_index update path for an existing index."""

    def test_init_index_updates_existing_sharepoint_config(self, sharepoint_processor):
        """Test that _init_index updates an existing index's SharePoint config fields."""
        processor = sharepoint_processor
        processor.site_url = "https://tenant.sharepoint.com/sites/newsite"
        processor.files_filter = "/Docs/*"
        processor.include_pages = False

        with patch.object(processor, "_assign_and_sync_guardrails"):
            processor._init_index()

        assert processor.index.sharepoint.site_url == "https://tenant.sharepoint.com/sites/newsite"
        assert processor.index.sharepoint.files_filter == "/Docs/*"
        assert processor.index.sharepoint.include_pages is False

    def test_init_index_creates_sharepoint_config_when_missing(self, sharepoint_processor):
        """Test _init_index creates SharePoint config when index exists but has no sharepoint field."""
        processor = sharepoint_processor
        processor.index.sharepoint = None

        with patch.object(processor, "_assign_and_sync_guardrails"):
            processor._init_index()

        assert processor.index.sharepoint is not None
        assert processor.index.sharepoint.site_url == "https://tenant.sharepoint.com/sites/testsite"


class TestSharePointProcessorChunk:
    """Test document chunk processing."""

    def test_process_chunk_returns_document_with_correct_metadata(self, sharepoint_processor):
        """Test _process_chunk preserves source/title/type and strips extra metadata."""
        source_doc = Document(
            page_content="Full content",
            metadata={
                "source": "https://test.com/page",
                "title": "Test Page",
                "type": "page",
                "extra_field": "should_be_dropped",
            },
        )

        result = sharepoint_processor._process_chunk("chunk content", {}, source_doc)

        assert isinstance(result, Document)
        assert result.page_content == "chunk content"
        assert result.metadata["source"] == "https://test.com/page"
        assert result.metadata["title"] == "Test Page"
        assert result.metadata["type"] == "page"
        assert "extra_field" not in result.metadata

    def test_process_chunk_handles_missing_metadata(self, sharepoint_processor):
        """Test _process_chunk handles document with no metadata gracefully."""
        source_doc = Document(page_content="Content", metadata={})

        result = sharepoint_processor._process_chunk("chunk", {}, source_doc)

        assert result.metadata["source"] == ""
        assert result.metadata["title"] == ""
        assert result.metadata["type"] == ""


class TestSharePointProcessorAuthTypes:
    """Test index-level auth type configuration (integration / oauth_codemie / oauth_custom)."""

    def test_auth_type_integration(self, sharepoint_credentials):
        """Test default auth_type=integration is stored correctly."""
        user = User(id="1", username="testuser")
        processor = SharePointDatasourceProcessor(
            datasource_name="test_ds",
            user=user,
            project_name="test_project",
            credentials=sharepoint_credentials,
            sp_config=SharePointProcessorConfig(
                site_url="https://tenant.sharepoint.com/sites/testsite",
                auth_type="integration",
            ),
        )

        assert processor.auth_type == "integration"
        assert processor.oauth_client_id is None
        assert processor.oauth_tenant_id is None

    def test_auth_type_oauth_codemie(self, sharepoint_credentials):
        """Test auth_type=oauth_codemie is stored correctly."""
        user = User(id="1", username="testuser")
        processor = SharePointDatasourceProcessor(
            datasource_name="test_ds",
            user=user,
            project_name="test_project",
            credentials=sharepoint_credentials,
            sp_config=SharePointProcessorConfig(
                site_url="https://tenant.sharepoint.com/sites/testsite",
                auth_type="oauth_codemie",
            ),
        )

        assert processor.auth_type == "oauth_codemie"

    def test_auth_type_oauth_custom(self, sharepoint_credentials):
        """Test auth_type=oauth_custom stores client_id and tenant_id correctly."""
        user = User(id="1", username="testuser")
        processor = SharePointDatasourceProcessor(
            datasource_name="test_ds",
            user=user,
            project_name="test_project",
            credentials=sharepoint_credentials,
            sp_config=SharePointProcessorConfig(
                site_url="https://tenant.sharepoint.com/sites/testsite",
                auth_type="oauth_custom",
                oauth_client_id="custom-client-id",
                oauth_tenant_id="custom-tenant-id",
            ),
        )

        assert processor.auth_type == "oauth_custom"
        assert processor.oauth_client_id == "custom-client-id"
        assert processor.oauth_tenant_id == "custom-tenant-id"

    @patch("codemie.datasource.sharepoint.sharepoint_datasource_processor.llm_service")
    @patch("codemie.rest_api.models.index.IndexInfo.new")
    def test_init_index_stores_auth_type_in_sharepoint_info(
        self, mock_index_new, mock_llm_service, sharepoint_credentials
    ):
        """Test _init_index stores auth_type fields into SharePointIndexInfo."""
        mock_llm_service.default_embedding_model = "text-embedding-3-small"

        mock_index = MagicMock()
        mock_index_new.return_value = mock_index

        user = User(id="1", username="testuser")
        processor = SharePointDatasourceProcessor(
            datasource_name="test_ds",
            user=user,
            project_name="test_project",
            credentials=sharepoint_credentials,
            sp_config=SharePointProcessorConfig(
                site_url="https://tenant.sharepoint.com/sites/testsite",
                auth_type="oauth_custom",
                oauth_client_id="custom-client-id",
                oauth_tenant_id="custom-tenant-id",
            ),
        )
        processor.index = None

        with patch.object(processor, "_assign_and_sync_guardrails"):
            processor._init_index()

        _, kwargs = mock_index_new.call_args
        sharepoint_info = kwargs["sharepoint"]
        assert sharepoint_info.auth_type == "oauth_custom"
        assert sharepoint_info.oauth_client_id == "custom-client-id"
        assert sharepoint_info.oauth_tenant_id == "custom-tenant-id"


class TestSharePointProcessorValidateCredsExtended:
    """Extended tests for validate_creds_and_loader exception branches."""

    @patch("codemie.datasource.sharepoint.sharepoint_datasource_processor.SharePointLoader")
    def test_validate_creds_and_loader_missing_integration(self, mock_loader_class, sharepoint_credentials):
        """Test validate_creds_and_loader re-raises MissingIntegrationException."""
        from codemie.datasource.exceptions import MissingIntegrationException

        mock_loader = MagicMock()
        mock_loader.fetch_remote_stats.side_effect = MissingIntegrationException("SharePoint")
        mock_loader_class.return_value = mock_loader

        with pytest.raises(MissingIntegrationException):
            SharePointDatasourceProcessor.validate_creds_and_loader(
                site_url="https://tenant.sharepoint.com/sites/testsite",
                credentials=sharepoint_credentials,
            )

    @patch("codemie.datasource.sharepoint.sharepoint_datasource_processor.SharePointLoader")
    def test_validate_creds_and_loader_network_error(self, mock_loader_class, sharepoint_credentials):
        """Test validate_creds_and_loader converts network errors to InvalidQueryException."""
        import requests
        from codemie.datasource.exceptions import InvalidQueryException

        mock_loader = MagicMock()
        mock_loader.fetch_remote_stats.side_effect = requests.exceptions.RequestException("timeout")
        mock_loader_class.return_value = mock_loader

        with pytest.raises(InvalidQueryException):
            SharePointDatasourceProcessor.validate_creds_and_loader(
                site_url="https://tenant.sharepoint.com/sites/testsite",
                credentials=sharepoint_credentials,
            )


# ---------------------------------------------------------------------------
# Tests for _on_process_end (OAuth token cleanup)
# ---------------------------------------------------------------------------


class TestSharePointProcessorOnProcessEnd:
    """Tests for SharePointDatasourceProcessor._on_process_end."""

    def test_clears_token_for_oauth_codemie(self, sharepoint_processor):
        """_on_process_end clears token & expires_at for oauth_codemie auth type."""
        processor = sharepoint_processor
        processor.index.sharepoint = MagicMock()
        processor.index.sharepoint.auth_type = "oauth_codemie"
        processor.index.sharepoint.access_token = "secret-token"
        processor.index.sharepoint.expires_at = 9999999999
        processor.index.update = MagicMock()

        processor._on_process_end()

        assert processor.index.sharepoint.access_token == ""
        assert processor.index.sharepoint.expires_at == 0
        processor.index.update.assert_called_once()

    def test_clears_token_for_oauth_custom(self, sharepoint_processor):
        """_on_process_end clears token & expires_at for oauth_custom auth type."""
        processor = sharepoint_processor
        processor.index.sharepoint = MagicMock()
        processor.index.sharepoint.auth_type = "oauth_custom"
        processor.index.sharepoint.access_token = "custom-token"
        processor.index.sharepoint.expires_at = 1234567890
        processor.index.update = MagicMock()

        processor._on_process_end()

        assert processor.index.sharepoint.access_token == ""
        assert processor.index.sharepoint.expires_at == 0
        processor.index.update.assert_called_once()

    def test_no_op_for_integration_auth_type(self, sharepoint_processor):
        """_on_process_end is a no-op for integration auth type."""
        processor = sharepoint_processor
        processor.index.sharepoint = MagicMock()
        processor.index.sharepoint.auth_type = "integration"
        processor.index.update = MagicMock()

        processor._on_process_end()

        processor.index.update.assert_not_called()

    def test_no_op_when_index_is_none(self, sharepoint_processor):
        """_on_process_end is safe when index is None."""
        processor = sharepoint_processor
        processor.index = None

        processor._on_process_end()  # should not raise

    def test_no_op_when_sharepoint_is_none(self, sharepoint_processor):
        """_on_process_end is safe when index.sharepoint is None."""
        processor = sharepoint_processor
        processor.index.sharepoint = None
        processor.index.update = MagicMock()

        processor._on_process_end()

        processor.index.update.assert_not_called()


# ---------------------------------------------------------------------------
# Tests for _cleanup_data_for_incremental_reindex
# ---------------------------------------------------------------------------


class TestSharePointProcessorCleanupIncremental:
    """Tests for SharePointDatasourceProcessor._cleanup_data_for_incremental_reindex."""

    def test_deletes_stale_chunks_for_given_sources(self, sharepoint_processor):
        """Calls delete_by_query with the document sources."""
        from langchain_core.documents import Document

        processor = sharepoint_processor
        processor.client = MagicMock()
        docs = [
            Document(page_content="c1", metadata={"source": "https://sp.com/page1"}),
            Document(page_content="c2", metadata={"source": "https://sp.com/page2"}),
        ]

        processor._cleanup_data_for_incremental_reindex(docs)

        processor.client.delete_by_query.assert_called_once()
        call_kwargs = processor.client.delete_by_query.call_args[1]
        assert "https://sp.com/page1" in call_kwargs["body"]["query"]["terms"]["metadata.source.keyword"]
        assert "https://sp.com/page2" in call_kwargs["body"]["query"]["terms"]["metadata.source.keyword"]

    def test_no_call_when_no_sources(self, sharepoint_processor):
        """Returns early without DB call when docs have no source metadata."""
        from langchain_core.documents import Document

        processor = sharepoint_processor
        processor.client = MagicMock()
        docs = [Document(page_content="c1", metadata={})]

        processor._cleanup_data_for_incremental_reindex(docs)

        processor.client.delete_by_query.assert_not_called()

    def test_no_call_when_empty_list(self, sharepoint_processor):
        """Returns early without DB call when docs list is empty."""
        processor = sharepoint_processor
        processor.client = MagicMock()

        processor._cleanup_data_for_incremental_reindex([])

        processor.client.delete_by_query.assert_not_called()

    def test_logs_error_on_es_exception(self, sharepoint_processor):
        """Logs error but does not re-raise when delete_by_query fails."""
        from langchain_core.documents import Document

        processor = sharepoint_processor
        processor.client = MagicMock()
        processor.client.delete_by_query.side_effect = Exception("ES down")
        docs = [Document(page_content="c", metadata={"source": "https://sp.com/doc"})]

        # Should not raise
        processor._cleanup_data_for_incremental_reindex(docs)

        processor.client.delete_by_query.assert_called_once()


# ---------------------------------------------------------------------------
# Tests for _init_loader — modified_since parameter
# ---------------------------------------------------------------------------


class TestSharePointProcessorInitLoaderModifiedSince:
    """Tests for modified_since injection in _init_loader."""

    @patch("codemie.datasource.sharepoint.sharepoint_datasource_processor.SharePointLoader")
    def test_no_modified_since_when_not_incremental(self, mock_loader_class, sharepoint_processor):
        """No modified_since when is_incremental_reindex is False."""
        processor = sharepoint_processor
        processor.is_incremental_reindex = False

        processor._init_loader()

        _, kwargs = mock_loader_class.call_args
        assert kwargs.get("modified_since") is None

    @patch("codemie.datasource.sharepoint.sharepoint_datasource_processor.SharePointLoader")
    def test_uses_last_reindex_date_for_incremental(self, mock_loader_class, sharepoint_processor):
        """Uses index.last_reindex_date when is_incremental_reindex is True."""
        from datetime import datetime

        processor = sharepoint_processor
        processor.is_incremental_reindex = True
        last_date = datetime(2024, 5, 1)
        processor.index.last_reindex_date = last_date
        processor.index.update_date = datetime(2024, 4, 1)

        processor._init_loader()

        _, kwargs = mock_loader_class.call_args
        assert kwargs.get("modified_since") == last_date

    @patch("codemie.datasource.sharepoint.sharepoint_datasource_processor.SharePointLoader")
    def test_falls_back_to_update_date_when_no_last_reindex(self, mock_loader_class, sharepoint_processor):
        """Falls back to index.update_date when last_reindex_date is None."""
        from datetime import datetime

        processor = sharepoint_processor
        processor.is_incremental_reindex = True
        processor.index.last_reindex_date = None
        update_date = datetime(2024, 4, 1)
        processor.index.update_date = update_date

        processor._init_loader()

        _, kwargs = mock_loader_class.call_args
        assert kwargs.get("modified_since") == update_date


# ---------------------------------------------------------------------------
# Tests for _encrypt_oauth_token / _decrypt_oauth_token
# ---------------------------------------------------------------------------


class TestEncryptDecryptOauthToken:
    """Tests for the module-level _encrypt_oauth_token and _decrypt_oauth_token helpers."""

    def test_encrypt_empty_token_returns_empty(self):
        """Encrypting an empty token returns empty string without calling service."""
        from codemie.datasource.sharepoint.sharepoint_datasource_processor import _encrypt_oauth_token

        with patch("codemie.datasource.sharepoint.sharepoint_datasource_processor.EncryptionFactory") as mock_factory:
            result = _encrypt_oauth_token("")
        assert result == ""
        mock_factory.get_current_encryption_service.assert_not_called()

    def test_encrypt_non_empty_token_calls_service(self):
        """Encrypting a non-empty token delegates to EncryptionFactory."""
        from codemie.datasource.sharepoint.sharepoint_datasource_processor import _encrypt_oauth_token

        with patch("codemie.datasource.sharepoint.sharepoint_datasource_processor.EncryptionFactory") as mock_factory:
            mock_service = MagicMock()
            mock_service.encrypt.return_value = "encrypted-value"
            mock_factory.get_current_encryption_service.return_value = mock_service

            result = _encrypt_oauth_token("plain-token")

        assert result == "encrypted-value"
        mock_service.encrypt.assert_called_once_with("plain-token")

    def test_decrypt_empty_token_returns_empty(self):
        """Decrypting an empty token returns empty string without calling service."""
        from codemie.datasource.sharepoint.sharepoint_datasource_processor import _decrypt_oauth_token

        with patch("codemie.datasource.sharepoint.sharepoint_datasource_processor.EncryptionFactory") as mock_factory:
            result = _decrypt_oauth_token("")
        assert result == ""
        mock_factory.get_current_encryption_service.assert_not_called()

    def test_decrypt_non_empty_token_calls_service(self):
        """Decrypting a non-empty token delegates to EncryptionFactory."""
        from codemie.datasource.sharepoint.sharepoint_datasource_processor import _decrypt_oauth_token

        with patch("codemie.datasource.sharepoint.sharepoint_datasource_processor.EncryptionFactory") as mock_factory:
            mock_service = MagicMock()
            mock_service.decrypt.return_value = "plain-token"
            mock_factory.get_current_encryption_service.return_value = mock_service

            result = _decrypt_oauth_token("encrypted-value")

        assert result == "plain-token"
        mock_service.decrypt.assert_called_once_with("encrypted-value")
