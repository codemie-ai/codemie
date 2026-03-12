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

"""
Tests for git datasource validation when token is missing.
Tests both create and reindex operations.
"""

import pytest
from unittest.mock import Mock, patch

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers.index import CreateIndexRequest


class TestCreateDatasourceValidation:
    """Test that create datasource operation validates git credentials."""

    @patch('codemie.rest_api.routers.index.ensure_application_exists')
    @patch('codemie.rest_api.routers.index.index_code_datasource_in_background')
    @patch('codemie.rest_api.routers.index.SettingsService.get_git_creds')
    @patch('codemie.rest_api.routers.index.Application.get_by_id')
    @patch('codemie.rest_api.routers.index.IndexInfo.filter_by_project_and_repo')
    @patch('codemie.rest_api.routers.index.request_summary_manager.create_request_summary')
    def test_create_datasource_validates_credentials(
        self, mock_summary, mock_index_filter, mock_get_app, mock_get_creds, mock_index_bg, mock_ensure_app
    ):
        """Test that creating a datasource validates git credentials."""
        from codemie.rest_api.routers.index import create_index_application

        # Mock that no existing datasource exists
        mock_index_filter.return_value = []

        mock_app = Mock()
        mock_app.name = "test-app"
        mock_get_app.return_value = mock_app

        mock_get_creds.return_value = Mock(token="valid-token")

        mock_request = Mock()
        mock_request.state.uuid = "uuid123"
        mock_request.state.user.is_demo_user = False
        mock_user_model = Mock()
        mock_request.state.user.as_user_model.return_value = mock_user_model

        mock_user = Mock()
        mock_user.id = "user123"

        create_git_repo_request = CreateIndexRequest(
            name="test-repo",
            link="https://gitlab.com/repo",
            branch="main",
            setting_id="setting123",
            description="Test repo",
            index_type="code",
            guardrail_assignments=None,
        )

        # Act
        create_index_application(
            app_name="test-app",
            create_git_repo_request=create_git_repo_request,
            request=mock_request,
            tasks=Mock(),
            user=mock_user,
        )

        # Assert - verify credentials were validated
        mock_get_creds.assert_called_with(
            user_id="user123",
            project_name="test-app",
            repo_link="https://gitlab.com/repo",
            setting_id="setting123",
        )
        assert mock_get_creds.call_count >= 1

    @patch('codemie.rest_api.routers.index.ensure_application_exists')
    @patch('codemie.rest_api.routers.index.SettingsService.get_git_creds')
    @patch('codemie.rest_api.routers.index.Application.get_by_id')
    @patch('codemie.rest_api.routers.index.IndexInfo.filter_by_project_and_repo')
    @patch('codemie.rest_api.routers.index.request_summary_manager.create_request_summary')
    def test_create_datasource_fails_with_missing_token(
        self, mock_summary, mock_index_filter, mock_get_app, mock_get_creds, mock_ensure_app
    ):
        """Test that creating a datasource fails with proper error when token is missing."""
        from codemie.rest_api.routers.index import create_index_application

        mock_index_filter.return_value = []

        mock_app = Mock()
        mock_app.name = "test-app"
        mock_get_app.return_value = mock_app

        # Simulate missing token error
        mock_get_creds.side_effect = Exception(
            "1 validation error for Credentials\ntoken\n  Field required [type=missing]"
        )

        mock_request = Mock()
        mock_request.state.uuid = "uuid123"
        mock_request.state.user.is_demo_user = False
        mock_user_model = Mock()
        mock_request.state.user.as_user_model.return_value = mock_user_model

        mock_user = Mock()
        mock_user.id = "user123"

        create_git_repo_request = CreateIndexRequest(
            name="test-repo",
            link="https://gitlab.com/repo",
            branch="main",
            setting_id="setting123",
            description="Test repo",
            index_type="code",
            guardrail_assignments=None,
        )

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            create_index_application(
                app_name="test-app",
                create_git_repo_request=create_git_repo_request,
                request=mock_request,
                tasks=Mock(),
                user=mock_user,
            )

        assert exc_info.value.code == 422
        assert exc_info.value.message == "Invalid Git Integration Configuration"
        assert "missing a required token" in exc_info.value.details

    @patch('codemie.rest_api.routers.index.ensure_application_exists')
    @patch('codemie.rest_api.routers.index.index_code_datasource_in_background')
    @patch('codemie.rest_api.routers.index.Application.get_by_id')
    @patch('codemie.rest_api.routers.index.IndexInfo.filter_by_project_and_repo')
    @patch('codemie.rest_api.routers.index.request_summary_manager.create_request_summary')
    def test_create_datasource_without_setting_id_skips_validation(
        self,
        mock_summary,
        mock_index_filter,
        mock_get_app,
        mock_index_bg,
        mock_ensure_app,
    ):
        """Test that creating a datasource without git integration skips validation."""
        from codemie.rest_api.routers.index import create_index_application

        mock_index_filter.return_value = []

        mock_app = Mock()
        mock_app.name = "test-app"
        mock_get_app.return_value = mock_app

        mock_request = Mock()
        mock_request.state.uuid = "uuid123"
        mock_request.state.user.is_demo_user = False
        mock_user_model = Mock()
        mock_request.state.user.as_user_model.return_value = mock_user_model

        mock_user = Mock()
        mock_user.id = "user123"

        create_git_repo_request = CreateIndexRequest(
            name="test-repo",
            link="https://gitlab.com/repo",
            branch="main",
            setting_id=None,  # No git integration
            description="Test repo",
            index_type="code",
            guardrail_assignments=None,
        )

        # Act - should not raise exception
        result = create_index_application(
            app_name="test-app",
            create_git_repo_request=create_git_repo_request,
            request=mock_request,
            tasks=Mock(),
            user=mock_user,
        )

        # Assert - should succeed (using datasource name, not project name)
        assert result.message == "Indexing of datasource test-repo has been started in the background"


class TestReindexValidation:
    """Test that reindex operations validate git credentials."""

    @patch('codemie.rest_api.routers.index.update_code_datasource_in_background')
    @patch('codemie.rest_api.routers.index.SettingsService.get_git_creds')
    @patch('codemie.rest_api.routers.index.GitRepo.get_by_app_id')
    @patch('codemie.rest_api.routers.index.Application.get_by_id')
    @patch('codemie.rest_api.routers.index.IndexInfo.filter_by_project_and_repo')
    @patch('codemie.rest_api.routers.index.request_summary_manager.create_request_summary')
    @patch('codemie.rest_api.routers.index.Ability')
    def test_full_reindex_validates_credentials(
        self, mock_ability, mock_summary, mock_index_info, mock_get_app, mock_get_repos, mock_get_creds, mock_update_bg
    ):
        """Test that full reindex action validates git credentials."""
        from codemie.rest_api.routers.index import update_index_application

        mock_index = Mock()
        mock_index.project_name = "test-project"
        mock_index_info.return_value = [mock_index]

        mock_ability_instance = Mock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance

        mock_app = Mock()
        mock_app.name = "test-app"
        mock_get_app.return_value = mock_app

        mock_repo = Mock()
        mock_repo.name = "test-repo"
        mock_repo.link = "https://gitlab.com/repo"
        mock_repo.setting_id = "setting123"
        mock_get_repos.return_value = [mock_repo]

        mock_get_creds.return_value = Mock(token="valid-token")

        mock_request = Mock()
        mock_request.name = None
        mock_request.model_fields_set = set()  # Add model_fields_set for Pydantic compatibility

        mock_raw_request = Mock()
        mock_raw_request.state.uuid = "uuid123"

        mock_user = Mock()
        mock_user.id = "user123"
        mock_user.as_user_model.return_value = Mock()

        # Act
        update_index_application(
            app_name="test-app",
            repo_name="test-repo",
            tasks=Mock(),
            request=mock_request,
            raw_request=mock_raw_request,
            full_reindex=True,
            skip_reindex=False,
            resume_indexing=False,
            user=mock_user,
        )

        # Assert - verify credentials were validated
        mock_get_creds.assert_called_with(
            user_id="user123",
            project_name="test-project",
            repo_link="https://gitlab.com/repo",
            setting_id="setting123",
        )
        assert mock_get_creds.call_count >= 1

    @patch('codemie.rest_api.routers.index.SettingsService.get_git_creds')
    @patch('codemie.rest_api.routers.index.GitRepo.get_by_app_id')
    @patch('codemie.rest_api.routers.index.Application.get_by_id')
    @patch('codemie.rest_api.routers.index.IndexInfo.filter_by_project_and_repo')
    @patch('codemie.rest_api.routers.index.request_summary_manager.create_request_summary')
    @patch('codemie.rest_api.routers.index.Ability')
    def test_full_reindex_fails_when_validation_fails(
        self, mock_ability, mock_summary, mock_index_info, mock_get_app, mock_get_repos, mock_get_creds
    ):
        """Test that full reindex fails with proper error when validation fails."""
        from codemie.rest_api.routers.index import update_index_application

        mock_index = Mock()
        mock_index.project_name = "test-project"
        mock_index_info.return_value = [mock_index]

        mock_ability_instance = Mock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance

        mock_app = Mock()
        mock_app.name = "test-app"
        mock_get_app.return_value = mock_app

        mock_repo = Mock()
        mock_repo.name = "test-repo"
        mock_repo.setting_id = "setting123"
        mock_get_repos.return_value = [mock_repo]

        # Simulate missing token error
        mock_get_creds.side_effect = Exception(
            "1 validation error for Credentials\ntoken\n  Field required [type=missing]"
        )

        mock_request = Mock()
        mock_request.name = None
        mock_request.model_fields_set = set()  # Add model_fields_set for Pydantic compatibility

        mock_raw_request = Mock()
        mock_raw_request.state.uuid = "uuid123"

        mock_user = Mock()
        mock_user.id = "user123"
        mock_user.as_user_model.return_value = Mock()

        # Act & Assert
        with pytest.raises(ExtendedHTTPException) as exc_info:
            update_index_application(
                app_name="test-app",
                repo_name="test-repo",
                tasks=Mock(),
                request=mock_request,
                raw_request=mock_raw_request,
                full_reindex=True,
                skip_reindex=False,
                resume_indexing=False,
                user=mock_user,
            )

        assert exc_info.value.code == 422
        assert exc_info.value.message == "Invalid Git Integration Configuration"
        assert "missing a required token" in exc_info.value.details

    @patch('codemie.rest_api.routers.index.update_code_datasource_in_background')
    @patch('codemie.rest_api.routers.index.SettingsService.get_git_creds')
    @patch('codemie.rest_api.routers.index.GitRepo.get_by_app_id')
    @patch('codemie.rest_api.routers.index.Application.get_by_id')
    @patch('codemie.rest_api.routers.index.IndexInfo.filter_by_project_and_repo')
    @patch('codemie.rest_api.routers.index.request_summary_manager.create_request_summary')
    @patch('codemie.rest_api.routers.index.Ability')
    def test_save_and_reindex_validates_credentials(
        self, mock_ability, mock_summary, mock_index_info, mock_get_app, mock_get_repos, mock_get_creds, mock_update_bg
    ):
        """Test that save and reindex validates git credentials."""
        from codemie.rest_api.routers.index import update_index_application

        mock_index = Mock()
        mock_index.project_name = "test-project"
        mock_index.update_index = Mock()
        mock_index_info.return_value = [mock_index]

        mock_ability_instance = Mock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance

        mock_app = Mock()
        mock_app.name = "test-app"
        mock_get_app.return_value = mock_app

        mock_repo = Mock()
        mock_repo.name = "test-repo"
        mock_repo.link = "https://gitlab.com/repo"
        mock_repo.setting_id = "setting123"
        mock_repo.update = Mock()
        mock_get_repos.return_value = [mock_repo]

        mock_get_creds.return_value = Mock(token="valid-token")

        mock_request = Mock()
        mock_request.name = "test-repo"  # Name provided = save and reindex
        mock_request.new_project_name = None
        mock_request.description = "Updated"
        mock_request.branch = "main"
        mock_request.link = "https://gitlab.com/repo"
        mock_request.setting_id = "setting123"
        mock_request.model_fields_set = set()  # Add model_fields_set for Pydantic compatibility

        mock_raw_request = Mock()
        mock_raw_request.state.uuid = "uuid123"

        mock_user = Mock()
        mock_user.id = "user123"
        mock_user.as_user_model.return_value = Mock()

        # Act
        update_index_application(
            app_name="test-app",
            repo_name="test-repo",
            tasks=Mock(),
            request=mock_request,
            raw_request=mock_raw_request,
            full_reindex=False,
            skip_reindex=False,
            resume_indexing=False,
            user=mock_user,
        )

        # Assert - verify credentials were validated
        mock_get_creds.assert_called_with(
            user_id="user123",
            project_name="test-project",
            repo_link="https://gitlab.com/repo",
            setting_id="setting123",
        )
        assert mock_get_creds.call_count >= 1
