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
from codemie.datasource.exceptions import ConnectionException
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
