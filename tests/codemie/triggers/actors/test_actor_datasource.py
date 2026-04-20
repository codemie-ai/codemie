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
    user.roles = []
    user.is_admin = False
    user.is_maintainer = False
    user.is_admin_or_maintainer = False
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


# ---------------------------------------------------------------------------
# Tests for resume_stale_datasource and its helpers
# ---------------------------------------------------------------------------


def _make_index_info(index_type: str, index_id: str = "idx-1") -> MagicMock:
    """Build a minimal mock IndexInfo for watchdog resume tests."""
    mock_info = MagicMock(spec=IndexInfo)
    mock_info.id = index_id
    mock_info.index_type = index_type
    mock_info.repo_name = "test-repo"
    mock_info.project_name = "test-project"
    mock_info.description = "desc"
    mock_info.project_space_visible = False
    mock_info.embeddings_model = "embed-model"
    mock_info.setting_id = "setting-123"
    mock_info.created_by = MagicMock(id="user-1")
    mock_info.google_doc_link = None
    mock_info.confluence = None
    mock_info.jira = None
    mock_info.sharepoint = None
    mock_info.xray = None
    return mock_info


class TestResumeStaleDataSource:
    """Tests for resume_stale_datasource dispatch function."""

    def test_skips_unsupported_type(self, mock_logger):
        """Unsupported index types are skipped with a warning."""
        from codemie.triggers.actors.datasource import resume_stale_datasource

        index_info = _make_index_info("knowledge_base_file")
        resume_stale_datasource(index_info)

        mock_logger.warning.assert_called()

    def test_skips_when_created_by_missing(self, mock_logger):
        """Missing created_by causes an error log and early return."""
        from codemie.triggers.actors.datasource import resume_stale_datasource

        index_info = _make_index_info("knowledge_base_jira")
        index_info.created_by = None

        resume_stale_datasource(index_info)

        mock_logger.error.assert_called()

    def test_skips_when_created_by_id_missing(self, mock_logger):
        """Missing created_by.id causes an error log and early return."""
        from codemie.triggers.actors.datasource import resume_stale_datasource

        index_info = _make_index_info("knowledge_base_jira")
        index_info.created_by = MagicMock(id=None)

        resume_stale_datasource(index_info)

        mock_logger.error.assert_called()

    def test_warns_for_unrecognised_type(self, mock_logger):
        """An unrecognised (but not explicitly unsupported) type logs a warning."""
        from codemie.triggers.actors.datasource import resume_stale_datasource

        index_info = _make_index_info("some_unknown_type")

        resume_stale_datasource(index_info)

        mock_logger.warning.assert_called()

    def test_dispatches_to_jira_handler(self, mock_logger):
        """Dispatches to _resume_jira for knowledge_base_jira type."""
        import codemie.triggers.actors.datasource as ds_module
        from codemie.triggers.actors.datasource import resume_stale_datasource

        index_info = _make_index_info("knowledge_base_jira")
        mock_handler = MagicMock()
        original = ds_module._RESUME_DISPATCH.get("knowledge_base_jira")
        try:
            ds_module._RESUME_DISPATCH["knowledge_base_jira"] = mock_handler
            resume_stale_datasource(index_info)
        finally:
            ds_module._RESUME_DISPATCH["knowledge_base_jira"] = original

        mock_handler.assert_called_once()

    def test_dispatches_to_confluence_handler(self, mock_logger):
        """Dispatches to _resume_confluence for knowledge_base_confluence type."""
        import codemie.triggers.actors.datasource as ds_module
        from codemie.triggers.actors.datasource import resume_stale_datasource

        index_info = _make_index_info("knowledge_base_confluence")
        mock_handler = MagicMock()
        original = ds_module._RESUME_DISPATCH.get("knowledge_base_confluence")
        try:
            ds_module._RESUME_DISPATCH["knowledge_base_confluence"] = mock_handler
            resume_stale_datasource(index_info)
        finally:
            ds_module._RESUME_DISPATCH["knowledge_base_confluence"] = original

        mock_handler.assert_called_once()

    def test_dispatches_to_sharepoint_handler(self, mock_logger):
        """Dispatches to _resume_sharepoint for knowledge_base_sharepoint type."""
        import codemie.triggers.actors.datasource as ds_module
        from codemie.triggers.actors.datasource import resume_stale_datasource

        index_info = _make_index_info("knowledge_base_sharepoint")
        mock_handler = MagicMock()
        original = ds_module._RESUME_DISPATCH.get("knowledge_base_sharepoint")
        try:
            ds_module._RESUME_DISPATCH["knowledge_base_sharepoint"] = mock_handler
            resume_stale_datasource(index_info)
        finally:
            ds_module._RESUME_DISPATCH["knowledge_base_sharepoint"] = original

        mock_handler.assert_called_once()

    def test_dispatches_to_google_doc_handler(self, mock_logger):
        """Dispatches to _resume_google_doc for llm_routing_google type."""
        import codemie.triggers.actors.datasource as ds_module
        from codemie.triggers.actors.datasource import resume_stale_datasource

        index_info = _make_index_info("llm_routing_google")
        mock_handler = MagicMock()
        original = ds_module._RESUME_DISPATCH.get("llm_routing_google")
        try:
            ds_module._RESUME_DISPATCH["llm_routing_google"] = mock_handler
            resume_stale_datasource(index_info)
        finally:
            ds_module._RESUME_DISPATCH["llm_routing_google"] = original

        mock_handler.assert_called_once()


class TestResumeJira:
    """Tests for _resume_jira helper."""

    def test_logs_error_when_no_jira_creds(self, mock_logger):
        """Logs error and returns early when jira credentials are not found."""
        from codemie.triggers.actors.datasource import _resume_jira

        index_info = _make_index_info("knowledge_base_jira")
        index_info.jira = MagicMock(jql="project = X")
        user = MagicMock(id="u1")

        with patch(
            "codemie.triggers.actors.datasource.SettingsService.get_jira_creds",
            return_value=None,
        ):
            _resume_jira(index_info, user, "req-1")

        mock_logger.error.assert_called()

    def test_calls_resume_when_creds_found(self):
        """Calls .resume() on processor when credentials are found."""
        from codemie.triggers.actors.datasource import _resume_jira

        index_info = _make_index_info("knowledge_base_jira")
        index_info.jira = MagicMock(jql="project = X")
        user = MagicMock(id="u1")

        mock_processor = MagicMock()
        with (
            patch(
                "codemie.triggers.actors.datasource.SettingsService.get_jira_creds",
                return_value=MagicMock(),
            ),
            patch(
                "codemie.triggers.actors.datasource.JiraDatasourceProcessor",
                return_value=mock_processor,
            ),
        ):
            _resume_jira(index_info, user, "req-1")

        mock_processor.resume.assert_called_once()


class TestResumeConfluence:
    """Tests for _resume_confluence helper."""

    def test_logs_error_when_no_confluence_index_info(self, mock_logger):
        """Logs error when confluence index info is missing."""
        from codemie.triggers.actors.datasource import _resume_confluence

        index_info = _make_index_info("knowledge_base_confluence")
        index_info.confluence = None
        user = MagicMock(id="u1")

        _resume_confluence(index_info, user, "req-1")

        mock_logger.error.assert_called()

    def test_logs_error_when_no_confluence_creds(self, mock_logger):
        """Logs error when confluence credentials are not found."""
        from codemie.triggers.actors.datasource import _resume_confluence

        index_info = _make_index_info("knowledge_base_confluence")
        index_info.confluence = MagicMock()
        user = MagicMock(id="u1")

        with patch(
            "codemie.triggers.actors.datasource.SettingsService.get_confluence_creds",
            return_value=None,
        ):
            _resume_confluence(index_info, user, "req-1")

        mock_logger.error.assert_called()

    def test_calls_resume_when_creds_found(self):
        """Calls .resume() on processor when credentials are found."""
        from codemie.triggers.actors.datasource import _resume_confluence

        index_info = _make_index_info("knowledge_base_confluence")
        index_info.confluence = MagicMock()
        user = MagicMock(id="u1")

        mock_processor = MagicMock()
        with (
            patch(
                "codemie.triggers.actors.datasource.SettingsService.get_confluence_creds",
                return_value=MagicMock(),
            ),
            patch(
                "codemie.triggers.actors.datasource.ConfluenceDatasourceProcessor",
                return_value=mock_processor,
            ),
            patch(
                "codemie.triggers.actors.datasource.IndexKnowledgeBaseConfluenceConfig" ".from_confluence_index_info",
                return_value=MagicMock(),
            ),
        ):
            _resume_confluence(index_info, user, "req-1")

        mock_processor.resume.assert_called_once()


class TestResumeGoogleDoc:
    """Tests for _resume_google_doc helper."""

    def test_logs_error_when_no_google_doc_link(self, mock_logger):
        """Logs error when google_doc_link is missing."""
        from codemie.triggers.actors.datasource import _resume_google_doc

        index_info = _make_index_info("llm_routing_google")
        index_info.google_doc_link = None
        user = MagicMock(id="u1")

        _resume_google_doc(index_info, user, "req-1")

        mock_logger.error.assert_called()

    def test_calls_resume_when_link_present(self):
        """Calls .resume() on processor when google_doc_link is present."""
        from codemie.triggers.actors.datasource import _resume_google_doc

        index_info = _make_index_info("llm_routing_google")
        index_info.google_doc_link = "https://docs.google.com/doc/1"
        user = MagicMock(id="u1")

        mock_processor = MagicMock()
        with patch(
            "codemie.triggers.actors.datasource.GoogleDocDatasourceProcessor",
            return_value=mock_processor,
        ):
            _resume_google_doc(index_info, user, "req-1")

        mock_processor.resume.assert_called_once()


class TestGetSharepointOauthCreds:
    """Tests for _get_sharepoint_oauth_creds helper."""

    def test_returns_none_when_token_missing(self, mock_logger):
        """Returns None and logs warning when access_token is empty."""
        from codemie.triggers.actors.datasource import _get_sharepoint_oauth_creds
        import time

        sp_index_info = MagicMock()
        sp_index_info.access_token = ""
        sp_index_info.expires_at = int(time.time()) + 3600
        index_info = _make_index_info("knowledge_base_sharepoint")

        result = _get_sharepoint_oauth_creds(sp_index_info, index_info)

        assert result is None
        mock_logger.warning.assert_called()

    def test_returns_none_when_token_expired(self, mock_logger):
        """Returns None and logs warning when token is expired."""
        from codemie.triggers.actors.datasource import _get_sharepoint_oauth_creds

        sp_index_info = MagicMock()
        sp_index_info.access_token = "expired-token"
        sp_index_info.expires_at = 1  # Unix epoch — definitely expired
        index_info = _make_index_info("knowledge_base_sharepoint")

        result = _get_sharepoint_oauth_creds(sp_index_info, index_info)

        assert result is None
        mock_logger.warning.assert_called()

    def test_returns_creds_when_token_valid(self):
        """Returns SharePointCredentials when token is present and not expired."""
        import time
        from codemie.triggers.actors.datasource import _get_sharepoint_oauth_creds

        sp_index_info = MagicMock()
        sp_index_info.access_token = "encrypted-tok"
        sp_index_info.expires_at = int(time.time()) + 3600

        index_info = _make_index_info("knowledge_base_sharepoint")

        # _decrypt_oauth_token is imported lazily inside _get_sharepoint_oauth_creds
        # so patch it at its definition location.
        with patch(
            "codemie.datasource.sharepoint.sharepoint_datasource_processor._decrypt_oauth_token",
            return_value="plain-token",
        ):
            result = _get_sharepoint_oauth_creds(sp_index_info, index_info)

        assert result is not None
        assert result.access_token == "plain-token"


class TestResumeSharepoint:
    """Tests for _resume_sharepoint helper."""

    def test_logs_error_when_no_sharepoint_index_info(self, mock_logger):
        """Logs error when sharepoint config is missing on index."""
        from codemie.triggers.actors.datasource import _resume_sharepoint

        index_info = _make_index_info("knowledge_base_sharepoint")
        index_info.sharepoint = None
        user = MagicMock(id="u1")

        _resume_sharepoint(index_info, user, "req-1")

        mock_logger.error.assert_called()

    def test_skips_when_oauth_creds_expired(self, mock_logger):
        """Returns early without creating processor when OAuth token is expired."""
        from codemie.triggers.actors.datasource import _resume_sharepoint

        index_info = _make_index_info("knowledge_base_sharepoint")
        sp_info = MagicMock()
        sp_info.auth_type = "oauth_codemie"
        sp_info.access_token = ""
        sp_info.expires_at = 1
        index_info.sharepoint = sp_info
        user = MagicMock(id="u1")

        with patch("codemie.triggers.actors.datasource.SharePointDatasourceProcessor") as mock_proc_class:
            _resume_sharepoint(index_info, user, "req-1")

        mock_proc_class.assert_not_called()

    def test_calls_resume_for_integration_auth(self):
        """Creates processor and calls .resume() for integration auth type."""
        from codemie.triggers.actors.datasource import _resume_sharepoint

        index_info = _make_index_info("knowledge_base_sharepoint")
        sp_info = MagicMock()
        sp_info.auth_type = "integration"
        sp_info.site_url = "https://tenant.sharepoint.com/sites/test"
        sp_info.include_pages = True
        sp_info.include_documents = True
        sp_info.include_lists = True
        sp_info.max_file_size_mb = 50
        sp_info.files_filter = ""
        sp_info.oauth_client_id = None
        sp_info.oauth_tenant_id = None
        index_info.sharepoint = sp_info
        user = MagicMock(id="u1")

        mock_processor = MagicMock()
        with (
            patch(
                "codemie.triggers.actors.datasource.SettingsService.get_sharepoint_creds",
                return_value=MagicMock(),
            ),
            patch(
                "codemie.triggers.actors.datasource.SharePointDatasourceProcessor",
                return_value=mock_processor,
            ),
        ):
            _resume_sharepoint(index_info, user, "req-1")

        mock_processor.resume.assert_called_once()
