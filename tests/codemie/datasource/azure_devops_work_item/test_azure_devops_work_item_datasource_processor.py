# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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
from contextlib import suppress
from datetime import datetime
from unittest.mock import MagicMock, patch

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from codemie.datasource.azure_devops_work_item.azure_devops_work_item_datasource_processor import (
    AzureDevOpsWorkItemDatasourceProcessor,
)
from codemie.rest_api.models.index import AzureDevOpsWorkItemIndexInfo
from codemie.core.models import CreatedByUser
from codemie.rest_api.security.user import User
from codemie.rest_api.models.settings import AzureDevOpsCredentials
from codemie.datasource.callback.base_datasource_callback import DatasourceProcessorCallback


@pytest.fixture
def azure_devops_work_item_processor_fixture():
    credentials = AzureDevOpsCredentials(
        base_url="https://dev.azure.com",
        organization="test-org",
        project="test-project",
        access_token="fake-token",
    )
    user = User(id="1", username="testuser")
    created_by = CreatedByUser(id="1", username="testuser")
    wiql_query = "SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = @project"
    datasource_name = "test_work_item_ds"
    project_name = "test_project"
    description = "Test Azure DevOps Work Item Description"
    index_type = "knowledge_base_azure_devops_work_item"
    setting_id = "setting_id"

    index_info = MagicMock()
    index_info.repo_name = datasource_name
    index_info.full_name = "Test Work Item Datasource"
    index_info.project_name = project_name
    index_info.description = description
    index_info.project_space_visible = True
    index_info.index_type = index_type
    index_info.current_state = 0
    index_info.error = False
    index_info.completed = False
    index_info.created_by = created_by
    index_info.azure_devops_work_item = AzureDevOpsWorkItemIndexInfo(wiql_query=wiql_query)
    index_info.update_date = datetime.now()
    index_info.start_progress = MagicMock()
    index_info.complete_progress = MagicMock()
    index_info.set_error = MagicMock()

    return AzureDevOpsWorkItemDatasourceProcessor(
        datasource_name=datasource_name,
        user=user,
        project_name=project_name,
        credentials=credentials,
        wiql_query=wiql_query,
        index_info=index_info,
        setting_id=setting_id,
    )


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


def test_init(azure_devops_work_item_processor_fixture):
    processor = azure_devops_work_item_processor_fixture

    assert processor.datasource_name == "test_work_item_ds"
    assert processor.project_name == "test_project"
    assert processor.credentials.base_url == "https://dev.azure.com"
    assert processor.credentials.organization == "test-org"
    assert processor.credentials.project == "test-project"
    assert processor.credentials.access_token == "fake-token"
    assert processor.index.repo_name == "test_work_item_ds"


def test_index_name_property(azure_devops_work_item_processor_fixture):
    processor = azure_devops_work_item_processor_fixture
    from codemie.core.models import KnowledgeBase

    expected = KnowledgeBase(
        name=f"{processor.project_name}-{processor.datasource_name}",
        type=processor.INDEX_TYPE,
    ).get_identifier()
    assert processor._index_name == expected


def test_processing_batch_size(azure_devops_work_item_processor_fixture):
    assert azure_devops_work_item_processor_fixture._processing_batch_size > 0


# ---------------------------------------------------------------------------
# _get_splitter
# ---------------------------------------------------------------------------


def test_get_splitter():
    splitter = AzureDevOpsWorkItemDatasourceProcessor._get_splitter()
    assert isinstance(splitter, RecursiveCharacterTextSplitter)
    assert splitter._chunk_size == 1000
    assert splitter._chunk_overlap == 50


# ---------------------------------------------------------------------------
# _process_chunk with metadata propagation
# ---------------------------------------------------------------------------


def test_process_chunk_base_metadata(azure_devops_work_item_processor_fixture):
    processor = azure_devops_work_item_processor_fixture
    document = Document(
        page_content="Work item content",
        metadata={
            "source": "https://dev.azure.com/test-org/test-project/_workitems/edit/42",
            "work_item_id": 42,
            "work_item_type": "Bug",
            "state": "Active",
            "title": "Fix login",
        },
    )
    result = processor._process_chunk("chunk text", document.metadata.copy(), document)

    assert result.page_content == "chunk text"
    assert result.metadata["source"] == document.metadata["source"]
    assert result.metadata["work_item_id"] == 42
    assert result.metadata["work_item_type"] == "Bug"
    assert result.metadata["state"] == "Active"
    assert result.metadata["title"] == "Fix login"
    assert "content_type" not in result.metadata


def test_process_chunk_preserves_comments_content_type(azure_devops_work_item_processor_fixture):
    processor = azure_devops_work_item_processor_fixture
    document = Document(
        page_content="Comment text",
        metadata={
            "source": "https://dev.azure.com/test-org/test-project/_workitems/edit/42#comments",
            "work_item_id": 42,
            "work_item_type": "Bug",
            "state": "Active",
            "title": "Fix login",
            "content_type": "comments",
            "summary": "Comments by Alice",
        },
    )
    result = processor._process_chunk("chunk", document.metadata.copy(), document)

    assert result.metadata["content_type"] == "comments"
    assert result.metadata["summary"] == "Comments by Alice"
    assert "attachment_name" not in result.metadata


def test_process_chunk_preserves_attachment_metadata(azure_devops_work_item_processor_fixture):
    processor = azure_devops_work_item_processor_fixture
    document = Document(
        page_content="Extracted PDF text",
        metadata={
            "source": "https://dev.azure.com/test-org/test-project/_workitems/edit/42#attachment-spec.pdf",
            "work_item_id": 42,
            "work_item_type": "Bug",
            "state": "Active",
            "title": "Fix login",
            "content_type": "attachment",
            "attachment_name": "spec.pdf",
            "attachment_mime_type": "application/pdf",
            "attachment_summary": "Short summary.",
            "summary": "File attachment: spec.pdf (application/pdf) - Short summary.",
        },
    )
    result = processor._process_chunk("chunk", document.metadata.copy(), document)

    assert result.metadata["content_type"] == "attachment"
    assert result.metadata["attachment_name"] == "spec.pdf"
    assert result.metadata["attachment_mime_type"] == "application/pdf"
    assert result.metadata["attachment_summary"] == "Short summary."
    assert "File attachment" in result.metadata["summary"]


def test_process_chunk_no_extra_metadata_for_work_item(azure_devops_work_item_processor_fixture):
    processor = azure_devops_work_item_processor_fixture
    document = Document(
        page_content="Regular work item",
        metadata={
            "source": "https://dev.azure.com/test-org/test-project/_workitems/edit/42",
            "work_item_id": 42,
            "work_item_type": "Bug",
            "state": "Active",
            "title": "Fix login",
        },
    )
    result = processor._process_chunk("chunk", document.metadata.copy(), document)

    assert "content_type" not in result.metadata
    assert "attachment_name" not in result.metadata
    assert "attachment_mime_type" not in result.metadata
    assert "summary" not in result.metadata


# ---------------------------------------------------------------------------
# _init_loader
# ---------------------------------------------------------------------------


def test_init_loader(azure_devops_work_item_processor_fixture):
    processor = azure_devops_work_item_processor_fixture

    with patch(
        "codemie.datasource.azure_devops_work_item.azure_devops_work_item_datasource_processor.AzureDevOpsWorkItemLoader"
    ) as mock_loader:
        processor._init_loader()

    mock_loader.assert_called_once()
    _, kwargs = mock_loader.call_args
    assert kwargs["base_url"] == "https://dev.azure.com"
    assert kwargs["organization"] == "test-org"
    assert kwargs["project"] == "test-project"
    assert kwargs["access_token"] == "fake-token"


@patch("codemie.datasource.azure_devops_work_item.azure_devops_work_item_datasource_processor.llm_service")
def test_init_loader_with_multimodal_llm(mock_llm_service, azure_devops_work_item_processor_fixture):
    mock_llm_model = MagicMock()
    mock_llm_service.get_multimodal_llms.return_value = [mock_llm_model]
    mock_chat_model = MagicMock()

    processor = azure_devops_work_item_processor_fixture

    with patch(
        "codemie.datasource.azure_devops_work_item.azure_devops_work_item_datasource_processor.AzureDevOpsWorkItemLoader"
    ) as mock_loader_class:
        with patch(
            "codemie.core.dependecies.get_llm_by_credentials",
            return_value=mock_chat_model,
        ):
            processor._init_loader()

    _, kwargs = mock_loader_class.call_args
    assert "chat_model" in kwargs


@patch("codemie.datasource.azure_devops_work_item.azure_devops_work_item_datasource_processor.llm_service")
def test_init_loader_no_multimodal_llms(mock_llm_service, azure_devops_work_item_processor_fixture):
    mock_llm_service.get_multimodal_llms.return_value = []

    processor = azure_devops_work_item_processor_fixture

    with patch(
        "codemie.datasource.azure_devops_work_item.azure_devops_work_item_datasource_processor.AzureDevOpsWorkItemLoader"
    ) as mock_loader_class:
        processor._init_loader()

    _, kwargs = mock_loader_class.call_args
    assert kwargs.get("chat_model") is None


@patch("codemie.datasource.azure_devops_work_item.azure_devops_work_item_datasource_processor.llm_service")
def test_init_loader_llm_init_fails_gracefully(mock_llm_service, azure_devops_work_item_processor_fixture):
    mock_llm_model = MagicMock()
    mock_llm_service.get_multimodal_llms.return_value = [mock_llm_model]

    processor = azure_devops_work_item_processor_fixture

    with patch(
        "codemie.datasource.azure_devops_work_item.azure_devops_work_item_datasource_processor.AzureDevOpsWorkItemLoader"
    ) as mock_loader_class:
        with patch(
            "codemie.core.dependecies.get_llm_by_credentials",
            side_effect=Exception("Model not available"),
        ):
            processor._init_loader()  # Must not raise

    _, kwargs = mock_loader_class.call_args
    assert kwargs.get("chat_model") is None


# ---------------------------------------------------------------------------
# _check_docs_health
# ---------------------------------------------------------------------------


def test_check_docs_health(azure_devops_work_item_processor_fixture):
    processor = azure_devops_work_item_processor_fixture
    mock_loader = MagicMock()
    mock_loader.fetch_remote_stats.return_value = {
        "documents_count_key": 5,
        "total_documents": 5,
        "skipped_documents": 0,
    }
    processor._init_loader = MagicMock(return_value=mock_loader)

    count = processor._check_docs_health()

    assert count == 5


def test_check_docs_health_failure(azure_devops_work_item_processor_fixture):
    processor = azure_devops_work_item_processor_fixture
    processor._init_loader = MagicMock()
    processor._init_loader.return_value.fetch_remote_stats.side_effect = Exception("Connection failed")

    with pytest.raises(Exception, match="Connection failed"):
        processor._check_docs_health()


# ---------------------------------------------------------------------------
# process / reprocess integration
# ---------------------------------------------------------------------------


@patch("codemie.datasource.base_datasource_processor.ensure_application_exists")
def test_process_success(mock_ensure_app, azure_devops_work_item_processor_fixture):
    processor = azure_devops_work_item_processor_fixture
    processor._init_loader = MagicMock()
    processor._init_loader.return_value.fetch_remote_stats.return_value = {
        "documents_count": 3,
        "total_documents": 3,
        "skipped_documents": 0,
    }
    processor._init_loader.return_value.lazy_load.return_value = [
        Document(
            page_content="Work item content",
            metadata={
                "source": "https://dev.azure.com/test-org/test-project/_workitems/edit/42",
                "work_item_id": 42,
                "work_item_type": "Bug",
                "state": "Active",
                "title": "Fix login",
            },
        )
    ]
    processor._get_store_by_index = MagicMock()
    processor._get_store_by_index.return_value._store._create_index_if_not_exists = MagicMock()
    processor._get_store_by_index.return_value.add_documents = MagicMock()
    processor._get_splitter = MagicMock()
    processor._get_splitter.return_value.split_text = MagicMock(return_value=["chunk1"])
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
    processor._cleanup_data.assert_not_called()
    mock_ensure_app.assert_called_once_with("test_project")


@patch("codemie.datasource.base_datasource_processor.ensure_application_exists")
def test_process_failure(mock_ensure_app, azure_devops_work_item_processor_fixture):
    processor = azure_devops_work_item_processor_fixture
    processor._init_loader = MagicMock()
    processor._init_loader.return_value.fetch_remote_stats.side_effect = Exception("Fetch failed")
    processor._init_loader.return_value.lazy_load.return_value = []
    processor._get_store_by_index = MagicMock()
    processor._get_splitter = MagicMock()

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
def test_success_full_reindex(mock_ensure_app, azure_devops_work_item_processor_fixture):
    processor = azure_devops_work_item_processor_fixture
    processor._init_loader = MagicMock()
    processor._init_loader.return_value.fetch_remote_stats.return_value = {
        "documents_count": 1,
        "total_documents": 1,
        "skipped_documents": 0,
    }
    processor._init_loader.return_value.lazy_load.return_value = [
        Document(
            page_content="Content",
            metadata={
                "source": "url",
                "work_item_id": 1,
                "work_item_type": "Task",
                "state": "Done",
                "title": "T",
            },
        )
    ]
    processor._get_store_by_index = MagicMock()
    processor._get_store_by_index.return_value._store._create_index_if_not_exists = MagicMock()
    processor._get_store_by_index.return_value.add_documents = MagicMock()
    processor._get_splitter = MagicMock()
    processor._get_splitter.return_value.split_text = MagicMock(return_value=["chunk1"])
    processor._cleanup_data = MagicMock()
    processor._validate_index_and_get_guardrails_for_index = MagicMock(return_value=(processor.index, []))

    mock_callback = MagicMock(spec=DatasourceProcessorCallback)
    with patch(
        "codemie.datasource.base_datasource_processor.DatasourceMonitoringCallback",
        return_value=mock_callback,
    ):
        processor.reprocess()

    mock_callback.on_complete.assert_called_once()
    processor._cleanup_data.assert_called_once()
    assert processor.is_full_reindex
