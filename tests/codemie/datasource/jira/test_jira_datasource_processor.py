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
from requests.exceptions import HTTPError

from codemie.datasource.jira.jira_datasource_processor import JiraDatasourceProcessor
from codemie_tools.core.project_management.jira.models import JiraConfig
from codemie.rest_api.models.index import JiraIndexInfo
from codemie.core.models import CreatedByUser
from codemie.rest_api.security.user import User
from codemie.datasource.callback.base_datasource_callback import (
    DatasourceProcessorCallback,
)
from codemie.datasource.exceptions import EmptyResultException
from codemie.datasource.exceptions import InvalidQueryException


@pytest.fixture
def jira_processor_fixture():
    credentials = JiraConfig(
        cloud=False,
        url="http://fake-jira-url",
        token="fake-token",
        username="fake-username",
    )
    user = User(id="1", username="testuser")
    created_by = CreatedByUser(id="1", username="testuser")
    jql = "project = TEST"
    datasource_name = "test_repo"
    project_name = "test_project"
    description = "Test Description"
    index_type = "knowledge_base_jira"
    setting_id = "setting_id"

    index_info = MagicMock(
        repo_name=datasource_name,
        full_name="Test Repo",
        project_name=project_name,
        description=description,
        project_space_visible=True,
        index_type=index_type,
        current_state=0,
        error=False,
        completed=False,
        created_by=created_by,
        jira=JiraIndexInfo(jql=jql),
        update_date=datetime.now(),
    )
    return JiraDatasourceProcessor(
        datasource_name=datasource_name,
        user=user,
        project_name=project_name,
        credentials=credentials,
        jql=jql,
        index_info=index_info,
        setting_id=setting_id,
    )


def test_init(jira_processor_fixture):
    processor = jira_processor_fixture

    assert processor.datasource_name == "test_repo"
    assert processor.project_name == "test_project"
    assert processor.credentials.url == "http://fake-jira-url"
    assert processor.credentials.token == "fake-token"
    assert processor.jql == "project = TEST"
    assert processor.index.repo_name == "test_repo"


@patch("codemie.datasource.base_datasource_processor.ensure_application_exists")
def test_process_success(mock_ensure_app, jira_processor_fixture):
    processor = jira_processor_fixture
    processor._init_loader = MagicMock()
    processor._init_loader.return_value.fetch_remote_stats.return_value = {"documents_count": 1}
    processor._init_loader.return_value.lazy_load.return_value = [
        Document(page_content="content", metadata={"source": "source", "key": "TEST-1"})
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
    processor.index.start_progress.assert_called_once()
    processor.index.complete_progress.assert_called_once()
    processor._cleanup_data.assert_not_called()
    mock_ensure_app.assert_called_once_with("test_project")


@patch("codemie.datasource.base_datasource_processor.ensure_application_exists")
def test_process_failure(mock_ensure_app, jira_processor_fixture):
    processor = jira_processor_fixture
    processor._init_loader = MagicMock()
    processor._init_loader.return_value.fetch_remote_stats.side_effect = Exception("Failed to fetch remote stats")
    processor._init_loader.return_value.lazy_load.return_value = [
        Document(page_content="content", metadata={"source": "source", "key": "TEST-1"})
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
    processor.index.complete_progress.assert_not_called()
    processor.index.set_error.assert_called_once()


@patch("codemie.datasource.base_datasource_processor.ensure_application_exists")
def test_success_full_reindex(mock_ensure_app, jira_processor_fixture):
    processor = jira_processor_fixture
    processor._init_loader = MagicMock()
    processor._init_loader.return_value.fetch_remote_stats.return_value = {"documents_count": 1}
    processor._init_loader.return_value.lazy_load.return_value = [
        Document(page_content="content", metadata={"source": "source", "key": "TEST-1"})
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
    processor.index.start_progress.assert_called_once()
    processor.index.complete_progress.assert_called_once()
    processor._cleanup_data.assert_called_once()
    assert processor.is_full_reindex


@patch("codemie.datasource.base_datasource_processor.ensure_application_exists")
def test_success_incremental_reindex(mock_ensure_app, jira_processor_fixture):
    processor = jira_processor_fixture
    processor._init_loader = MagicMock()
    processor._init_loader.return_value.fetch_remote_stats.return_value = {"documents_count": 1}
    processor._init_loader.return_value.lazy_load.return_value = [
        Document(page_content="content", metadata={"source": "source", "key": "TEST-1"})
    ]
    processor._get_store_by_index = MagicMock()
    processor._get_store_by_index.return_value._store._create_index_if_not_exists = MagicMock()
    processor._get_store_by_index.return_value.add_documents = MagicMock()
    processor._get_splitter = MagicMock()
    processor._get_splitter.return_value.split_text = MagicMock(return_value=["chunk1", "chunk2"])
    processor._cleanup_data = MagicMock()
    processor._cleanup_data_for_incremental_reindex = MagicMock()

    # Mock guardrail validation to return no guardrails
    processor._validate_index_and_get_guardrails_for_index = MagicMock(return_value=(processor.index, []))

    mock_callback = MagicMock(spec=DatasourceProcessorCallback)
    with patch(
        "codemie.datasource.base_datasource_processor.DatasourceMonitoringCallback",
        return_value=mock_callback,
    ):
        processor.incremental_reindex()

    mock_callback.on_complete.assert_called_once()
    mock_callback.on_error.assert_not_called()
    processor.index.start_progress.assert_called_once()
    processor.index.complete_progress.assert_called_once()
    processor._cleanup_data.assert_not_called()
    processor._cleanup_data_for_incremental_reindex.assert_called_once()
    assert not processor.is_full_reindex
    assert not processor.is_resume_indexing
    assert processor.is_incremental_reindex


def test_get_loader_args():
    credentials = JiraConfig(
        cloud=True,
        url="http://fake-jira-url",
        token="fake-token",
        username="fake-username",
    )
    update_date = datetime.now()

    # Test cloud credentials
    result = JiraDatasourceProcessor._get_loader_args(credentials)
    assert result["cloud"]
    assert result["url"] == credentials.url
    assert result["username"] == credentials.username
    assert result["password"] == credentials.token

    # Test non-cloud credentials
    credentials.cloud = False
    result = JiraDatasourceProcessor._get_loader_args(credentials)
    assert not result["cloud"]
    assert result["url"] == credentials.url
    assert result["token"] == credentials.token
    assert "username" not in result

    # Test with incremental reindex
    result = JiraDatasourceProcessor._get_loader_args(credentials, True, update_date)
    assert "updated_gte" in result
    assert result["updated_gte"] == update_date


def test_get_splitter():
    splitter = JiraDatasourceProcessor._get_splitter()
    assert isinstance(splitter, RecursiveCharacterTextSplitter)
    assert splitter._chunk_size == 1000
    assert splitter._chunk_overlap == 50


def test_process_chunk(jira_processor_fixture):
    processor = jira_processor_fixture
    document = Document(page_content="original content", metadata={"source": "source", "key": "TEST-1"})
    chunk = "test chunk"
    chunk_metadata = {"source": "source", "key": "TEST-1"}

    result = processor._process_chunk(chunk, chunk_metadata, document)

    assert isinstance(result, Document)
    assert result.page_content == chunk
    assert result.metadata["source"] == "source"
    assert result.metadata["key"] == "TEST-1"


def test_init_loader(jira_processor_fixture):
    processor = jira_processor_fixture

    with patch("codemie.datasource.jira.jira_datasource_processor.JiraLoader") as mock_loader:
        processor._init_loader()

    mock_loader.assert_called_once()
    args, kwargs = mock_loader.call_args
    assert kwargs["jql"] == "project = TEST"
    assert not kwargs["cloud"]
    assert kwargs["url"] == "http://fake-jira-url"
    assert kwargs["token"] == "fake-token"


def test_validate_creds_and_loader():
    credentials = JiraConfig(
        cloud=False,
        url="http://fake-jira-url",
        token="fake-token",
        username="fake-username",
    )

    with patch("codemie.datasource.jira.jira_datasource_processor.JiraLoader") as mock_loader:
        mock_loader_instance = MagicMock()
        mock_loader_instance.fetch_remote_stats.return_value = {"documents_count": 10}
        mock_loader.return_value = mock_loader_instance

        result = JiraDatasourceProcessor.validate_creds_and_loader("project = TEST", credentials)

    assert result == {"documents_count": 10}
    mock_loader.assert_called_once()


def test_check_jira_query():
    credentials = JiraConfig(
        cloud=False,
        url="http://fake-jira-url",
        token="fake-token",
        username="fake-username",
    )

    with patch(
        "codemie.datasource.jira.jira_datasource_processor.JiraDatasourceProcessor.validate_creds_and_loader"
    ) as mock_validate:
        mock_validate.return_value = {"documents_count_key": 10}

        result = JiraDatasourceProcessor.check_jira_query("project = TEST", credentials)

    assert result == 10
    mock_validate.assert_called_once_with(jql="project = TEST", credentials=credentials)


def test_check_jira_query_empty_result():
    credentials = JiraConfig(
        cloud=False,
        url="http://fake-jira-url",
        token="fake-token",
        username="fake-username",
    )

    with (
        patch(
            "codemie.datasource.jira.jira_datasource_processor.JiraDatasourceProcessor.validate_creds_and_loader"
        ) as mock_validate,
        pytest.raises(EmptyResultException),
    ):
        mock_validate.return_value = {"documents_count_key": 0}

        JiraDatasourceProcessor.check_jira_query("project = TEST", credentials)


def test_validate_creds_and_loader_invalid_query():
    credentials = JiraConfig(
        cloud=False,
        url="http://fake-jira-url",
        token="fake-token",
        username="fake-username",
    )

    with (
        patch("codemie.datasource.jira.jira_datasource_processor.JiraLoader") as mock_loader,
        pytest.raises(InvalidQueryException),
    ):
        mock_loader_instance = MagicMock()
        mock_loader_instance.fetch_remote_stats.side_effect = HTTPError("Invalid JQL")
        mock_loader.return_value = mock_loader_instance

        JiraDatasourceProcessor.validate_creds_and_loader("invalid query", credentials)


def test_check_jira_query_no_jql():
    credentials = JiraConfig(
        cloud=False,
        url="https://bobs.jira.org",
        token="token",
        username="bob",
    )

    with (
        patch("codemie.datasource.jira.jira_datasource_processor.JiraLoader") as mock_loader,
        pytest.raises(InvalidQueryException),
    ):
        mock_loader.fetch_remote_stats.return_value = {"documents_count_key": 10}
        JiraDatasourceProcessor.check_jira_query("", credentials)


def test_init_loader_with_last_reindex_date(jira_processor_fixture):
    """Test that _init_loader uses last_reindex_date for incremental reindex"""
    processor = jira_processor_fixture
    processor.is_incremental_reindex = True

    # Set up the last_reindex_date on the index
    old_date = datetime(2024, 1, 1, 12, 0, 0)
    processor.index.last_reindex_date = old_date
    processor.index.update_date = datetime.now()

    with patch("codemie.datasource.jira.jira_datasource_processor.JiraLoader") as mock_loader:
        processor._init_loader()

    # Verify that the loader was called with the old date (last_reindex_date)
    mock_loader.assert_called_once()
    args, kwargs = mock_loader.call_args
    assert kwargs["updated_gte"] == old_date


def test_init_loader_without_last_reindex_date(jira_processor_fixture):
    """Test that _init_loader uses update_date when last_reindex_date is not set"""
    processor = jira_processor_fixture
    processor.is_incremental_reindex = False

    # Only set update_date
    current_date = datetime.now()
    processor.index.update_date = current_date
    processor.index.last_reindex_date = None

    with patch("codemie.datasource.jira.jira_datasource_processor.JiraLoader") as mock_loader:
        processor._init_loader()

    # Verify that the loader was called without updated_gte (not incremental)
    mock_loader.assert_called_once()
    args, kwargs = mock_loader.call_args
    assert "updated_gte" not in kwargs
