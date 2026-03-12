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

from unittest.mock import MagicMock, patch

import pytest

from codemie.datasource.confluence_datasource_processor import (
    IndexKnowledgeBaseConfluenceConfig,
)
from codemie.rest_api.models.index import ConfluenceIndexInfo, IndexInfo, JiraIndexInfo
from codemie.rest_api.security.user import User


# New fixture for mock_logger
@pytest.fixture
def mock_logger():
    with patch("codemie.triggers.actors.datasource.logger") as mock_log:
        yield mock_log


@pytest.fixture
def mock_user():
    user = MagicMock(spec=User)
    user.id = "test_user_id"
    return user


@pytest.fixture
def mock_base_index_info():
    mock_info = MagicMock(spec=IndexInfo)
    mock_info.id = "mock_index_id"
    mock_info.description = "A generic description"
    mock_info.prompt = "A generic prompt"
    mock_info.embeddings_model = "test-embedding-model"
    mock_info.summarization_model = "test-summarization-model"
    mock_info.text = "Some text content"
    mock_info.full_name = "Mock Full Name"
    mock_info.created_by = MagicMock(id="user123", email="user@example.com")
    mock_info.branch = "main"
    mock_info.link = "http://localhost:8080"
    mock_info.files_filter = "*.py"
    mock_info.google_doc_link = None
    mock_info.confluence = None
    mock_info.jira = None
    mock_info.setting_id = "test_setting_id"
    mock_info.tokens_usage = MagicMock(total_tokens=100, prompt_tokens=50, completion_tokens=50)
    mock_info.processing_info = {}
    mock_info.provider_fields = MagicMock(aws=MagicMock(region="us-east-1"))
    mock_info.bedrock = MagicMock(region="us-east-1", kb_id="kb-123", ds_id="ds-123")
    mock_info.project_space_visible = True
    return mock_info


@pytest.fixture
def mock_git_repo_get_by_fields(monkeypatch):
    from unittest.mock import MagicMock

    mock_repo_instance = MagicMock()
    mock_repo_instance.name = "mock_repo_name"
    mock_repo_instance.id = "repo_id_123"
    mock_repo_instance.project_name = "test_project"
    mock_repo_instance.repo_name = "test_resource"

    mock_get_by_fields_method = MagicMock(return_value=mock_repo_instance)
    monkeypatch.setattr('codemie.core.models.GitRepo.get_by_fields', mock_get_by_fields_method)
    return mock_get_by_fields_method


@pytest.fixture
def patch_settings_service_get_confluence_creds():
    with patch("codemie.service.settings.settings.SettingsService.get_confluence_creds") as mock_get_creds:
        mock_creds = MagicMock()
        mock_get_creds.return_value = mock_creds
        yield mock_get_creds


@pytest.fixture
def patch_settings_service_get_jira_creds():
    with patch("codemie.service.settings.settings.SettingsService.get_jira_creds") as mock_get_creds:
        mock_creds = MagicMock()
        mock_get_creds.return_value = mock_creds
        yield mock_get_creds


# NEW FIXTURES FOR PROCESSORS
@pytest.fixture
def mock_code_processor_class():
    with patch("codemie.triggers.actors.datasource.CodeDatasourceProcessor") as mock:
        yield mock


@pytest.fixture
def mock_jira_processor_class():
    with patch("codemie.triggers.actors.datasource.JiraDatasourceProcessor") as mock:
        yield mock


@pytest.fixture
def mock_confluence_processor_class():
    with patch("codemie.triggers.actors.datasource.ConfluenceDatasourceProcessor") as mock:
        yield mock


@pytest.fixture
def mock_google_doc_processor_class():
    with patch("codemie.triggers.actors.datasource.GoogleDocDatasourceProcessor") as mock:
        yield mock


@pytest.fixture
def mock_code_index_info(mock_base_index_info):
    mock_base_index_info.index_type = "code"
    mock_base_index_info.setting_id = "test_setting_id_code"
    return mock_base_index_info


# Test for reindex_code
def test_reindex_code(
    mock_logger, mock_user, mock_git_repo_get_by_fields, mock_code_processor_class, mock_code_index_info
):
    from codemie.triggers.actors.datasource import CodeReindexTask, reindex_code

    project_name = 'test_project'
    resource_name = 'test_resource'
    resource_id = 'test_job_id'
    repo_id = 'repo_id_123'

    mock_processor_instance = MagicMock()
    mock_code_processor_class.create_processor.return_value = mock_processor_instance

    payload = CodeReindexTask(
        resource_id=resource_id,
        project_name=project_name,
        resource_name=resource_name,
        user=mock_user,
        repo_id=repo_id,
        index_info=mock_code_index_info,
    )

    reindex_code(payload)

    mock_git_repo_get_by_fields.assert_called_once_with({"id": repo_id, "setting_id": mock_code_index_info.setting_id})

    mock_code_processor_class.create_processor.assert_called_once_with(
        git_repo=mock_git_repo_get_by_fields.return_value,
        user=mock_user,
        index=mock_code_index_info,
        request_uuid=resource_id,
    )

    mock_processor_instance.reprocess.assert_called_once()

    mock_logger.info.assert_any_call(
        "Starting reindexing for %s datasource (Trigger Invoked, job_id: %s, project: %s, resource: %s).",
        mock_code_index_info.index_type,
        resource_id,
        project_name,
        resource_name,
    )
    mock_logger.info.assert_any_call(
        "Successfully initiated reindexing for %s datasource (Trigger Invoked, job_id: %s, project: %s, resource: %s).",
        mock_code_index_info.index_type,
        resource_id,
        project_name,
        resource_name,
    )


@pytest.fixture
def mock_jira_index_info(mock_base_index_info):
    mock_base_index_info.index_type = "jira"
    mock_base_index_info.setting_id = "test_setting_id_jira"
    mock_base_index_info.jira = MagicMock(spec=JiraIndexInfo)
    mock_base_index_info.description = "Jira description"
    mock_base_index_info.project_space_visible = True
    return mock_base_index_info


def test_reindex_jira(
    mock_jira_processor_class,
    mock_user,
    mock_jira_index_info,
    mock_logger,
    patch_settings_service_get_jira_creds,
):
    from codemie.triggers.actors.datasource import JiraReindexTask, reindex_jira

    project_name = 'test_project'
    resource_name = 'test_resource'
    jql = 'test_jql'
    resource_id = 'test_job_id'

    mock_processor_instance = MagicMock()
    mock_jira_processor_class.return_value = mock_processor_instance
    patch_settings_service_get_jira_creds.return_value = MagicMock()

    payload = JiraReindexTask(
        resource_id=resource_id,
        project_name=project_name,
        resource_name=resource_name,
        user=mock_user,
        jql=jql,
        index_info=mock_jira_index_info,
    )
    reindex_jira(payload)

    patch_settings_service_get_jira_creds.assert_called_once_with(
        user_id=mock_user.id,
        project_name=project_name,
        setting_id=mock_jira_index_info.setting_id,
    )

    mock_jira_processor_class.assert_called_once()
    args, kwargs = mock_jira_processor_class.call_args
    assert kwargs["datasource_name"] == resource_name
    assert kwargs["user"] == mock_user
    assert kwargs["project_name"] == project_name
    assert kwargs["credentials"] == patch_settings_service_get_jira_creds.return_value
    assert kwargs["jql"] == jql
    assert kwargs["request_uuid"] == resource_id
    assert kwargs["index_info"] == mock_jira_index_info
    assert kwargs["description"] == mock_jira_index_info.description
    assert kwargs["project_space_visible"] == mock_jira_index_info.project_space_visible
    assert kwargs["embedding_model"] == mock_jira_index_info.embeddings_model

    mock_processor_instance.incremental_reindex.assert_called_once()

    mock_logger.info.assert_any_call(
        "Starting reindexing for %s datasource (Trigger Invoked, job_id: %s, project: %s, resource: %s).",
        mock_jira_index_info.index_type,
        resource_id,
        project_name,
        resource_name,
    )
    mock_logger.info.assert_any_call(
        "Successfully initiated reindexing for %s datasource (Trigger Invoked, job_id: %s, project: %s, resource: %s).",
        mock_jira_index_info.index_type,
        resource_id,
        project_name,
        resource_name,
    )


@pytest.fixture
def mock_confluence_index_info_full(mock_base_index_info):
    mock_confluence_info = MagicMock(spec=ConfluenceIndexInfo)
    mock_confluence_info.cql = "test_cql_query"

    mock_base_index_info.index_type = "confluence"
    mock_base_index_info.setting_id = "test_setting_id_confluence"
    mock_base_index_info.confluence = mock_confluence_info
    mock_base_index_info.description = "Confluence description"
    mock_base_index_info.project_space_visible = True
    return mock_base_index_info


# Test for reindex_confluence
def test_reindex_confluence(
    mock_confluence_processor_class,
    mock_user,
    mock_confluence_index_info_full,
    patch_settings_service_get_confluence_creds,
    mock_logger,
):
    from codemie.triggers.actors.datasource import ConfluenceReindexTask, reindex_confluence

    project_name = 'test_project'
    resource_name = 'test_resource'
    resource_id = 'test_job_id'

    mock_processor_instance = MagicMock()
    mock_confluence_processor_class.return_value = mock_processor_instance

    payload = ConfluenceReindexTask(
        resource_id=resource_id,
        project_name=project_name,
        resource_name=resource_name,
        user=mock_user,
        index_info=mock_confluence_index_info_full,
        confluence_index_info=mock_confluence_index_info_full.confluence,
    )
    reindex_confluence(payload)

    patch_settings_service_get_confluence_creds.assert_called_once_with(
        user_id=mock_user.id,
        project_name=project_name,
        setting_id=mock_confluence_index_info_full.setting_id,
    )

    mock_confluence_processor_class.assert_called_once()
    args, kwargs = mock_confluence_processor_class.call_args
    assert kwargs["datasource_name"] == resource_name
    assert kwargs["user"] == mock_user
    assert kwargs["project_name"] == project_name
    assert kwargs["confluence"] == patch_settings_service_get_confluence_creds.return_value
    assert isinstance(kwargs["index_knowledge_base_config"], IndexKnowledgeBaseConfluenceConfig)
    assert kwargs["index_knowledge_base_config"].cql == "test_cql_query"
    assert kwargs["description"] == mock_confluence_index_info_full.description
    assert kwargs["project_space_visible"] is mock_confluence_index_info_full.project_space_visible
    assert kwargs["index"] == mock_confluence_index_info_full
    assert kwargs["request_uuid"] == resource_id
    assert kwargs["embedding_model"] == mock_confluence_index_info_full.embeddings_model

    mock_processor_instance.reprocess.assert_called_once()

    mock_logger.info.assert_any_call(
        "Starting reindexing for %s datasource (Trigger Invoked, job_id: %s, project: %s, resource: %s).",
        mock_confluence_index_info_full.index_type,
        resource_id,
        project_name,
        resource_name,
    )
    mock_logger.info.assert_any_call(
        "Successfully initiated reindexing for %s datasource (Trigger Invoked, job_id: %s, project: %s, resource: %s).",
        mock_confluence_index_info_full.index_type,
        resource_id,
        project_name,
        resource_name,
    )


@pytest.fixture
def mock_google_index_info(mock_base_index_info):
    mock_base_index_info.index_type = "google"
    mock_base_index_info.google_doc_link = "http://mock.google.doc/link"
    mock_base_index_info.repo_name = "test_resource_google"
    mock_base_index_info.project_name = "test_project_google"
    return mock_base_index_info


# Test for reindex_google
def test_reindex_google(
    mock_google_doc_processor_class,
    mock_user,
    mock_google_index_info,
    mock_logger,
):
    from codemie.triggers.actors.datasource import GoogleReindexTask, reindex_google

    project_name = 'test_project'
    resource_name = 'test_resource'
    resource_id = 'test_job_id'

    mock_processor_instance = MagicMock()
    mock_google_doc_processor_class.return_value = mock_processor_instance

    payload = GoogleReindexTask(
        resource_id=resource_id,
        project_name=project_name,
        resource_name=resource_name,
        user=mock_user,
        index_info=mock_google_index_info,
        google_doc_link=mock_google_index_info.google_doc_link,
    )
    reindex_google(payload)

    mock_google_doc_processor_class.assert_called_once()
    args, kwargs = mock_google_doc_processor_class.call_args
    assert kwargs["datasource_name"] == mock_google_index_info.repo_name
    assert kwargs["user"] == mock_user
    assert kwargs["project_name"] == mock_google_index_info.project_name
    assert kwargs["google_doc"] == mock_google_index_info.google_doc_link
    assert kwargs["description"] == mock_google_index_info.description  # Use mock_google_index_info.description
    assert kwargs["request_uuid"] == resource_id
    assert kwargs["index_info"] == mock_google_index_info
    assert kwargs["embedding_model"] == mock_google_index_info.embeddings_model

    mock_processor_instance.reprocess.assert_called_once()

    mock_logger.info.assert_any_call(
        "Starting reindexing for %s datasource (Trigger Invoked, job_id: %s, project: %s, resource: %s).",
        mock_google_index_info.index_type,
        resource_id,
        project_name,
        resource_name,
    )
    mock_logger.info.assert_any_call(
        "Successfully initiated reindexing for %s datasource (Trigger Invoked, job_id: %s, project: %s, resource: %s).",
        mock_google_index_info.index_type,
        resource_id,
        project_name,
        resource_name,
    )
