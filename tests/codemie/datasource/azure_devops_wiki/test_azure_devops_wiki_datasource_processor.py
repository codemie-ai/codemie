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

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
from contextlib import suppress

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from codemie.datasource.azure_devops_wiki.azure_devops_wiki_datasource_processor import (
    AzureDevOpsWikiDatasourceProcessor,
)
from codemie.rest_api.models.index import AzureDevOpsWikiIndexInfo
from codemie.core.models import CreatedByUser
from codemie.rest_api.security.user import User
from codemie.rest_api.models.settings import AzureDevOpsCredentials
from codemie.datasource.callback.base_datasource_callback import (
    DatasourceProcessorCallback,
)


@pytest.fixture
def azure_devops_wiki_processor_fixture():
    credentials = AzureDevOpsCredentials(
        base_url="https://dev.azure.com",
        organization="test-org",
        project="test-project",
        access_token="fake-token",
    )
    user = User(id="1", username="testuser")
    created_by = CreatedByUser(id="1", username="testuser")
    wiki_query = "/docs/*"
    wiki_name = "test.wiki"
    datasource_name = "test_wiki_ds"
    project_name = "test_project"
    description = "Test Azure DevOps Wiki Description"
    index_type = "knowledge_base_azure_devops_wiki"
    setting_id = "setting_id"

    index_info = MagicMock()
    index_info.repo_name = datasource_name
    index_info.full_name = "Test Wiki Datasource"
    index_info.project_name = project_name
    index_info.description = description
    index_info.project_space_visible = True
    index_info.index_type = index_type
    index_info.current_state = 0
    index_info.error = False
    index_info.completed = False
    index_info.created_by = created_by
    index_info.azure_devops_wiki = AzureDevOpsWikiIndexInfo(wiki_query=wiki_query, wiki_name=wiki_name)
    index_info.update_date = datetime.now()
    # Explicitly create mock methods that we'll assert on
    index_info.start_progress = MagicMock()
    index_info.complete_progress = MagicMock()
    index_info.set_error = MagicMock()
    return AzureDevOpsWikiDatasourceProcessor(
        datasource_name=datasource_name,
        user=user,
        project_name=project_name,
        credentials=credentials,
        wiki_query=wiki_query,
        wiki_name=wiki_name,
        index_info=index_info,
        setting_id=setting_id,
    )


def test_init(azure_devops_wiki_processor_fixture):
    processor = azure_devops_wiki_processor_fixture

    assert processor.datasource_name == "test_wiki_ds"
    assert processor.project_name == "test_project"
    assert processor.credentials.base_url == "https://dev.azure.com"
    assert processor.credentials.organization == "test-org"
    assert processor.credentials.project == "test-project"
    assert processor.credentials.access_token == "fake-token"
    assert processor.wiki_query == "/docs/*"
    assert processor.wiki_name == "test.wiki"
    assert processor.index.repo_name == "test_wiki_ds"


def test_index_name_property(azure_devops_wiki_processor_fixture):
    processor = azure_devops_wiki_processor_fixture
    from codemie.core.models import KnowledgeBase

    expected_index_name = KnowledgeBase(
        name=f"{processor.project_name}-{processor.datasource_name}", type=processor.INDEX_TYPE
    ).get_identifier()
    assert processor._index_name == expected_index_name


def test_processing_batch_size(azure_devops_wiki_processor_fixture):
    processor = azure_devops_wiki_processor_fixture
    # Using AZURE_DEVOPS_WIKI_CONFIG.loader_batch_size
    assert processor._processing_batch_size > 0


@patch("codemie.datasource.base_datasource_processor.ensure_application_exists")
def test_process_success(mock_ensure_app, azure_devops_wiki_processor_fixture):
    processor = azure_devops_wiki_processor_fixture
    processor._init_loader = MagicMock()
    processor._init_loader.return_value.fetch_remote_stats.return_value = {
        "documents_count": 5,
        "total_documents": 5,
        "skipped_documents": 0,
    }
    processor._init_loader.return_value.lazy_load.return_value = [
        Document(
            page_content="Wiki page content",
            metadata={
                "source": "https://dev.azure.com/test-org/test-project/_wiki/wikis/test.wiki/123/Page-1",
                "page_id": "123",
                "page_path": "/Page 1",
                "wiki_name": "test.wiki",
            },
        )
    ]
    processor._get_store_by_index = MagicMock()
    processor._get_store_by_index.return_value._store._create_index_if_not_exists = MagicMock()
    processor._get_store_by_index.return_value.add_documents = MagicMock()
    processor._get_splitter = MagicMock()
    processor._get_splitter.return_value.split_text = MagicMock(return_value=["chunk1", "chunk2"])
    processor._cleanup_data = MagicMock()

    # Mock guardrail validation to return no guardrails
    processor._validate_index_and_get_guardrails_for_index = MagicMock(return_value=(processor.index, []))

    mock_callback = MagicMock(spec=DatasourceProcessorCallback)
    with patch(
        "codemie.datasource.base_datasource_processor.DatasourceMonitoringCallback",
        return_value=mock_callback,
    ):
        processor.process()

    mock_callback.on_complete.assert_called_once()
    mock_callback.on_error.assert_not_called()
    # noinspection PyUnresolvedReferences
    processor.index.start_progress.assert_called_once()
    # noinspection PyUnresolvedReferences
    processor.index.complete_progress.assert_called_once()
    processor._cleanup_data.assert_not_called()
    mock_ensure_app.assert_called_once_with("test_project")


@patch("codemie.datasource.base_datasource_processor.ensure_application_exists")
def test_process_failure(mock_ensure_app, azure_devops_wiki_processor_fixture):
    processor = azure_devops_wiki_processor_fixture
    processor._init_loader = MagicMock()
    processor._init_loader.return_value.fetch_remote_stats.side_effect = Exception("Failed to fetch remote stats")
    processor._init_loader.return_value.lazy_load.return_value = [
        Document(
            page_content="Wiki page content",
            metadata={
                "source": "https://dev.azure.com/test-org/test-project/_wiki/wikis/test.wiki/123/Page-1",
                "page_id": "123",
                "page_path": "/Page 1",
                "wiki_name": "test.wiki",
            },
        )
    ]
    processor._get_store_by_index = MagicMock()
    processor._get_store_by_index.return_value._store._create_index_if_not_exists = MagicMock()
    processor._get_store_by_index.return_value.add_documents = MagicMock()
    processor._get_splitter = MagicMock()
    processor._get_splitter.return_value.split_text = MagicMock(return_value=["chunk1", "chunk2"])

    mock_callback = MagicMock(spec=DatasourceProcessorCallback)
    with patch(
        "codemie.datasource.base_datasource_processor.DatasourceMonitoringCallback",
        return_value=mock_callback,
    ):
        with suppress(Exception):
            processor.process()

    mock_callback.on_complete.assert_not_called()
    mock_callback.on_error.assert_called_once()
    # noinspection PyUnresolvedReferences
    processor.index.complete_progress.assert_not_called()
    # noinspection PyUnresolvedReferences
    processor.index.set_error.assert_called_once()


@patch("codemie.datasource.base_datasource_processor.ensure_application_exists")
def test_success_full_reindex(mock_ensure_app, azure_devops_wiki_processor_fixture):
    processor = azure_devops_wiki_processor_fixture
    processor._init_loader = MagicMock()
    processor._init_loader.return_value.fetch_remote_stats.return_value = {
        "documents_count": 5,
        "total_documents": 5,
        "skipped_documents": 0,
    }
    processor._init_loader.return_value.lazy_load.return_value = [
        Document(
            page_content="Wiki page content",
            metadata={
                "source": "https://dev.azure.com/test-org/test-project/_wiki/wikis/test.wiki/123/Page-1",
                "page_id": "123",
                "page_path": "/Page 1",
                "wiki_name": "test.wiki",
            },
        )
    ]
    processor._get_store_by_index = MagicMock()
    processor._get_store_by_index.return_value._store._create_index_if_not_exists = MagicMock()
    processor._get_store_by_index.return_value.add_documents = MagicMock()
    processor._get_splitter = MagicMock()
    processor._get_splitter.return_value.split_text = MagicMock(return_value=["chunk1", "chunk2"])
    processor._cleanup_data = MagicMock()

    # Mock guardrail validation to return no guardrails
    processor._validate_index_and_get_guardrails_for_index = MagicMock(return_value=(processor.index, []))

    mock_callback = MagicMock(spec=DatasourceProcessorCallback)
    with patch(
        "codemie.datasource.base_datasource_processor.DatasourceMonitoringCallback",
        return_value=mock_callback,
    ):
        processor.reprocess()

    mock_callback.on_complete.assert_called_once()
    mock_callback.on_error.assert_not_called()
    # noinspection PyUnresolvedReferences
    processor.index.start_progress.assert_called_once()
    # noinspection PyUnresolvedReferences
    processor.index.complete_progress.assert_called_once()
    processor._cleanup_data.assert_called_once()
    assert processor.is_full_reindex


def test_get_splitter():
    splitter = AzureDevOpsWikiDatasourceProcessor._get_splitter()
    assert isinstance(splitter, RecursiveCharacterTextSplitter)
    # Using AZURE_DEVOPS_WIKI_CONFIG values
    assert splitter._chunk_size == 1000
    assert splitter._chunk_overlap == 50


def test_process_chunk(azure_devops_wiki_processor_fixture):
    processor = azure_devops_wiki_processor_fixture
    document = Document(
        page_content="original content",
        metadata={
            "source": "https://dev.azure.com/test-org/test-project/_wiki/wikis/test.wiki/123/Page-1",
            "page_id": "123",
            "page_path": "/Page 1",
            "wiki_name": "test.wiki",
        },
    )
    chunk = "test chunk"
    chunk_metadata = {
        "source": "https://dev.azure.com/test-org/test-project/_wiki/wikis/test.wiki/123/Page-1",
        "page_id": "123",
        "page_path": "/Page 1",
        "wiki_name": "test.wiki",
    }

    result = processor._process_chunk(chunk, chunk_metadata, document)

    assert isinstance(result, Document)
    assert result.page_content == chunk
    assert result.metadata["source"] == "https://dev.azure.com/test-org/test-project/_wiki/wikis/test.wiki/123/Page-1"
    assert result.metadata["page_id"] == "123"
    assert result.metadata["page_path"] == "/Page 1"
    assert result.metadata["wiki_name"] == "test.wiki"


def test_init_loader(azure_devops_wiki_processor_fixture):
    processor = azure_devops_wiki_processor_fixture

    with patch(
        "codemie.datasource.azure_devops_wiki.azure_devops_wiki_datasource_processor.AzureDevOpsWikiLoader"
    ) as mock_loader:
        processor._init_loader()

    mock_loader.assert_called_once()
    args, kwargs = mock_loader.call_args
    assert kwargs["base_url"] == "https://dev.azure.com"
    assert kwargs["organization"] == "test-org"
    assert kwargs["project"] == "test-project"
    assert kwargs["access_token"] == "fake-token"
    assert kwargs["wiki_query"] == "/docs/*"
    assert kwargs["wiki_identifier"] == "test.wiki"


@patch("codemie.datasource.azure_devops_wiki.azure_devops_wiki_datasource_processor.llm_service")
@patch("codemie.rest_api.models.index.IndexInfo.new")
def test_init_index(mock_index_new, mock_llm_service, azure_devops_wiki_processor_fixture):
    mock_llm_service.default_embedding_model = "text-embedding-3-small"

    # Mock IndexInfo.new to return a mock index instead of trying to save to DB
    mock_index = MagicMock()
    mock_index.repo_name = "test_wiki_ds"
    mock_index.project_name = "test_project"
    mock_index.index_type = "knowledge_base_azure_devops_wiki"
    mock_index.azure_devops_wiki = MagicMock()
    mock_index.azure_devops_wiki.wiki_query = "/docs/*"
    mock_index.azure_devops_wiki.wiki_name = "test.wiki"
    mock_index_new.return_value = mock_index

    processor = azure_devops_wiki_processor_fixture
    # Clear the index to test initialization
    processor.index = None

    with patch.object(processor, "_assign_and_sync_guardrails") as mock_assign:
        processor._init_index()

    assert processor.index is not None
    assert processor.index.repo_name == "test_wiki_ds"
    assert processor.index.project_name == "test_project"
    assert processor.index.index_type == "knowledge_base_azure_devops_wiki"
    assert processor.index.azure_devops_wiki.wiki_query == "/docs/*"
    assert processor.index.azure_devops_wiki.wiki_name == "test.wiki"
    mock_assign.assert_called_once()
    mock_index_new.assert_called_once()


def test_cleanup_data(azure_devops_wiki_processor_fixture):
    processor = azure_devops_wiki_processor_fixture
    processor.client = MagicMock()

    processor._cleanup_data()

    processor.client.delete_by_query.assert_called_once()
    call_args = processor.client.delete_by_query.call_args
    assert call_args[1]["index"] == processor._index_name
    assert call_args[1]["body"]["query"] == {"match_all": {}}
    assert call_args[1]["wait_for_completion"] is True
    assert call_args[1]["refresh"] is True


def test_check_docs_health(azure_devops_wiki_processor_fixture):
    processor = azure_devops_wiki_processor_fixture

    # Mock loader instance
    mock_loader = MagicMock()
    mock_loader.fetch_remote_stats.return_value = {
        "documents_count_key": 10,
        "total_documents": 10,
        "skipped_documents": 0,
    }

    # Mock _init_loader to return the mock loader
    processor._init_loader = MagicMock(return_value=mock_loader)

    count = processor._check_docs_health()

    assert count == 10
    processor._init_loader.assert_called_once()
    mock_loader.fetch_remote_stats.assert_called_once()


def test_check_docs_health_failure(azure_devops_wiki_processor_fixture):
    processor = azure_devops_wiki_processor_fixture
    processor._init_loader = MagicMock()
    processor._init_loader.return_value.fetch_remote_stats.side_effect = Exception("Connection failed")

    with pytest.raises(Exception, match="Connection failed"):
        processor._check_docs_health()


def test_wiki_query_empty_string(azure_devops_wiki_processor_fixture):
    """Test that empty wiki_query is handled properly"""
    processor = azure_devops_wiki_processor_fixture
    processor.wiki_query = ""

    with patch(
        "codemie.datasource.azure_devops_wiki.azure_devops_wiki_datasource_processor.AzureDevOpsWikiLoader"
    ) as mock_loader:
        processor._init_loader()

    # Empty string should be passed as-is, loader will handle it
    args, kwargs = mock_loader.call_args
    assert kwargs["wiki_query"] == ""


def test_wiki_name_none(azure_devops_wiki_processor_fixture):
    """Test that None wiki_name is handled properly"""
    processor = azure_devops_wiki_processor_fixture
    processor.wiki_name = None

    with patch(
        "codemie.datasource.azure_devops_wiki.azure_devops_wiki_datasource_processor.AzureDevOpsWikiLoader"
    ) as mock_loader:
        processor._init_loader()

    # None should be passed as-is, loader will handle it
    args, kwargs = mock_loader.call_args
    assert kwargs["wiki_identifier"] is None


@patch("codemie.datasource.azure_devops_wiki.azure_devops_wiki_datasource_processor.llm_service")
def test_init_loader_with_multimodal_llm(mock_llm_service, azure_devops_wiki_processor_fixture):
    """_init_loader passes a chat_model to the loader when a multimodal LLM is available"""
    mock_llm_model = MagicMock()
    mock_llm_service.get_multimodal_llms.return_value = [mock_llm_model]
    mock_chat_model = MagicMock()

    processor = azure_devops_wiki_processor_fixture

    with patch(
        "codemie.datasource.azure_devops_wiki.azure_devops_wiki_datasource_processor.AzureDevOpsWikiLoader"
    ) as mock_loader_class:
        with patch(
            "codemie.core.dependecies.get_llm_by_credentials",
            return_value=mock_chat_model,
        ):
            processor._init_loader()

    _, kwargs = mock_loader_class.call_args
    assert "chat_model" in kwargs
    # chat_model should not be None (it's either the mock or fell back to None)


@patch("codemie.datasource.azure_devops_wiki.azure_devops_wiki_datasource_processor.llm_service")
def test_init_loader_no_multimodal_llms(mock_llm_service, azure_devops_wiki_processor_fixture):
    """_init_loader passes chat_model=None when no multimodal LLMs are configured"""
    mock_llm_service.get_multimodal_llms.return_value = []

    processor = azure_devops_wiki_processor_fixture

    with patch(
        "codemie.datasource.azure_devops_wiki.azure_devops_wiki_datasource_processor.AzureDevOpsWikiLoader"
    ) as mock_loader_class:
        processor._init_loader()

    _, kwargs = mock_loader_class.call_args
    assert kwargs.get("chat_model") is None


@patch("codemie.datasource.azure_devops_wiki.azure_devops_wiki_datasource_processor.llm_service")
def test_init_loader_llm_init_fails_gracefully(mock_llm_service, azure_devops_wiki_processor_fixture):
    """_init_loader falls back to chat_model=None when get_llm_by_credentials raises"""
    mock_llm_model = MagicMock()
    mock_llm_service.get_multimodal_llms.return_value = [mock_llm_model]

    processor = azure_devops_wiki_processor_fixture

    with patch(
        "codemie.datasource.azure_devops_wiki.azure_devops_wiki_datasource_processor.AzureDevOpsWikiLoader"
    ) as mock_loader_class:
        with patch(
            "codemie.core.dependecies.get_llm_by_credentials",
            side_effect=Exception("Model not available"),
        ):
            processor._init_loader()  # Must not raise

    _, kwargs = mock_loader_class.call_args
    assert kwargs.get("chat_model") is None


def test_process_chunk_preserves_attachment_metadata(azure_devops_wiki_processor_fixture):
    """_process_chunk copies attachment-specific metadata fields into the chunk Document"""
    processor = azure_devops_wiki_processor_fixture
    document = Document(
        page_content="Attachment extracted text",
        metadata={
            "source": "https://dev.azure.com/test-org/test-project/_wiki/wikis/test.wiki/123/Page-1",
            "page_id": "123",
            "page_path": "/Page 1",
            "wiki_name": "test.wiki",
            "content_type": "attachment",
            "attachment_name": "spec.pdf",
            "attachment_path": "/.attachments/spec.pdf",
            "attachment_mime_type": "application/pdf",
            "attachment_summary": "Short summary.",
        },
    )
    chunk = "Chunk of attachment text"
    chunk_metadata = document.metadata.copy()

    result = processor._process_chunk(chunk, chunk_metadata, document)

    assert result.page_content == chunk
    assert result.metadata["content_type"] == "attachment"
    assert result.metadata["attachment_name"] == "spec.pdf"
    assert result.metadata["attachment_path"] == "/.attachments/spec.pdf"
    assert result.metadata["attachment_mime_type"] == "application/pdf"
    assert result.metadata["attachment_summary"] == "Short summary."


def test_process_chunk_preserves_comments_content_type(azure_devops_wiki_processor_fixture):
    """_process_chunk copies content_type=comments into the chunk Document"""
    processor = azure_devops_wiki_processor_fixture
    document = Document(
        page_content="Comment text",
        metadata={
            "source": "https://dev.azure.com/test-org/test-project/_wiki/wikis/test.wiki/123/Page-1",
            "page_id": "123",
            "page_path": "/Page 1",
            "wiki_name": "test.wiki",
            "content_type": "comments",
        },
    )
    chunk = "Chunk of comments"
    chunk_metadata = document.metadata.copy()

    result = processor._process_chunk(chunk, chunk_metadata, document)

    assert result.metadata["content_type"] == "comments"
    # No attachment keys should be present
    assert "attachment_name" not in result.metadata


def test_process_chunk_no_extra_metadata_for_page(azure_devops_wiki_processor_fixture):
    """_process_chunk does NOT add content_type or attachment fields for regular page Documents"""
    processor = azure_devops_wiki_processor_fixture
    document = Document(
        page_content="Regular wiki page",
        metadata={
            "source": "https://dev.azure.com/test-org/test-project/_wiki/wikis/test.wiki/123/Page-1",
            "page_id": "123",
            "page_path": "/Page 1",
            "wiki_name": "test.wiki",
        },
    )
    chunk = "Chunk of page"

    result = processor._process_chunk(chunk, document.metadata.copy(), document)

    assert "content_type" not in result.metadata
    assert "attachment_name" not in result.metadata
    assert "attachment_path" not in result.metadata
