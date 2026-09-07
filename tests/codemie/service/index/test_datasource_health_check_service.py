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
from unittest.mock import Mock, patch

from codemie.core.constants import DatasourceTypes
from codemie.datasource.exceptions import (
    AmbiguousCustomFieldException,
    ConnectionException,
    InvalidCustomFieldException,
)
from codemie.rest_api.models.index import DatasourceHealthCheckRequest
from codemie.service.index.datasource_health_check_service import IndexHealthCheckService


class TestHealthCheckGit:
    """Tests for IndexHealthCheckService.health_check_git."""

    def _make_request(self, git_url=None, setting_id=None, project_name="test-project"):
        return DatasourceHealthCheckRequest(
            project_name=project_name,
            index_type=DatasourceTypes.GIT,
            git_url=git_url,
            setting_id=setting_id,
        )

    def test_health_check_git_missing_url(self):
        """Returns field_error response when git_url is not provided."""
        request = self._make_request(git_url=None)
        response = IndexHealthCheckService.health_check_git(request, user_id="user1")
        assert response.error is not None
        assert response.error.field_error == "git_url"

    @patch('codemie.service.index.datasource_health_check_service.GitBatchLoader.test_public_access')
    def test_health_check_git_public_success(self, mock_test_public_access):
        """Returns documents_count=0 when public repo is accessible."""
        mock_test_public_access.return_value = None
        request = self._make_request(git_url="https://github.com/owner/public-repo", setting_id=None)
        response = IndexHealthCheckService.health_check_git(request, user_id="user1")
        assert response.error is None
        assert response.documents_count == 0
        mock_test_public_access.assert_called_once_with("https://github.com/owner/public-repo")

    @patch('codemie.service.index.datasource_health_check_service.GitBatchLoader.test_public_access')
    def test_health_check_git_public_inaccessible(self, mock_test_public_access):
        """ConnectionException from test_public_access propagates for handler mapping."""
        mock_test_public_access.side_effect = ConnectionException("git", "not accessible")
        request = self._make_request(git_url="https://github.com/owner/private-repo", setting_id=None)
        with pytest.raises(ConnectionException):
            IndexHealthCheckService.health_check_git(request, user_id="user1")

    @patch('codemie.service.index.datasource_health_check_service.GitBatchLoader.test_connection')
    @patch('codemie.service.index.datasource_health_check_service.SettingsService.get_git_creds')
    def test_health_check_git_authenticated_success(self, mock_get_creds, mock_test_connection):
        """Returns documents_count=0 when authenticated repo is accessible."""
        mock_creds = Mock()
        mock_get_creds.return_value = mock_creds
        mock_test_connection.return_value = None

        request = self._make_request(
            git_url="https://github.com/owner/private-repo",
            setting_id="setting-abc",
        )
        response = IndexHealthCheckService.health_check_git(request, user_id="user1")

        assert response.error is None
        assert response.documents_count == 0
        mock_get_creds.assert_called_once_with(
            user_id="user1",
            project_name="test-project",
            repo_link="https://github.com/owner/private-repo",
            setting_id="setting-abc",
        )
        mock_test_connection.assert_called_once_with("https://github.com/owner/private-repo", mock_creds)

    @patch('codemie.service.index.datasource_health_check_service.GitBatchLoader.test_connection')
    @patch('codemie.service.index.datasource_health_check_service.SettingsService.get_git_creds')
    def test_health_check_git_authenticated_failure(self, mock_get_creds, mock_test_connection):
        """ConnectionException from test_connection propagates for handler mapping."""
        mock_get_creds.return_value = Mock()
        mock_test_connection.side_effect = ConnectionException(
            "git", "Failed to connect to repository at https://github.com/owner/private-repo"
        )
        request = self._make_request(
            git_url="https://github.com/owner/private-repo",
            setting_id="setting-abc",
        )
        with pytest.raises(ConnectionException):
            IndexHealthCheckService.health_check_git(request, user_id="user1")

    @patch('codemie.service.index.datasource_health_check_service.GitBatchLoader.test_public_access')
    def test_health_check_datasource_routes_git(self, mock_test_public_access):
        """health_check_datasource dispatches DatasourceTypes.GIT to health_check_git."""
        mock_test_public_access.return_value = None
        request = DatasourceHealthCheckRequest(
            project_name="test-project",
            index_type=DatasourceTypes.GIT,
            git_url="https://github.com/owner/public-repo",
        )
        response = IndexHealthCheckService.health_check_datasource(request, user_id="user1")
        assert response.error is None
        assert response.documents_count == 0
        mock_test_public_access.assert_called_once()


class TestHealthCheckJira:
    """Tests for the Jira path of IndexHealthCheckService."""

    def _make_request(self, custom_fields=None, jql="project = TEST"):
        return DatasourceHealthCheckRequest(
            project_name="test-project",
            index_type=DatasourceTypes.JIRA,
            jql=jql,
            setting_id="setting-1",
            custom_fields=custom_fields,
        )

    @patch("codemie.service.index.datasource_health_check_service.JiraDatasourceProcessor.check_jira_query")
    @patch("codemie.service.index.datasource_health_check_service.SettingsService.get_jira_creds")
    def test_health_check_jira_forwards_custom_fields(self, mock_get_creds, mock_check):
        """Without this the form's Check connection would validate only the JQL."""
        creds = Mock()
        mock_get_creds.return_value = creds
        mock_check.return_value = 7
        request = self._make_request(custom_fields=["customfield_10001"])

        response = IndexHealthCheckService.health_check_jira(request, user_id="user1")

        mock_check.assert_called_once_with(jql="project = TEST", credentials=creds, custom_fields=["customfield_10001"])
        assert response.documents_count == 7

    @patch("codemie.service.index.datasource_health_check_service.JiraDatasourceProcessor.check_jira_query")
    @patch("codemie.service.index.datasource_health_check_service.SettingsService.get_jira_creds")
    def test_health_check_jira_without_custom_fields(self, mock_get_creds, mock_check):
        mock_check.return_value = 1

        IndexHealthCheckService.health_check_jira(self._make_request(), user_id="user1")

        assert mock_check.call_args.kwargs["custom_fields"] is None

    @patch("codemie.service.index.datasource_health_check_service.JiraDatasourceProcessor.check_jira_query")
    @patch("codemie.service.index.datasource_health_check_service.SettingsService.get_jira_creds")
    def test_invalid_custom_field_becomes_field_error(self, mock_get_creds, mock_check):
        """Unhandled, this exception would 500 the endpoint instead of highlighting the input."""
        mock_check.side_effect = InvalidCustomFieldException(["No Such Field"])
        request = self._make_request(custom_fields=["No Such Field"])

        response = IndexHealthCheckService.health_check_datasource(request, user_id="user1")

        assert response.error is not None
        assert response.error.field_error == "jiraCustomFields"
        assert "No Such Field" in response.error.message

    @patch("codemie.service.index.datasource_health_check_service.JiraDatasourceProcessor.check_jira_query")
    @patch("codemie.service.index.datasource_health_check_service.SettingsService.get_jira_creds")
    def test_ambiguous_custom_field_becomes_field_error(self, mock_get_creds, mock_check):
        mock_check.side_effect = AmbiguousCustomFieldException(["Sprint"])
        request = self._make_request(custom_fields=["Sprint"])

        response = IndexHealthCheckService.health_check_datasource(request, user_id="user1")

        assert response.error is not None
        assert response.error.field_error == "jiraCustomFields"
        assert "Sprint" in response.error.message

    @patch("codemie.service.index.datasource_health_check_service.JiraDatasourceProcessor.check_jira_query")
    @patch("codemie.service.index.datasource_health_check_service.SettingsService.get_jira_creds")
    def test_health_check_datasource_routes_jira(self, mock_get_creds, mock_check):
        mock_check.return_value = 3

        response = IndexHealthCheckService.health_check_datasource(self._make_request(), user_id="user1")

        assert response.documents_count == 3
        mock_check.assert_called_once()
