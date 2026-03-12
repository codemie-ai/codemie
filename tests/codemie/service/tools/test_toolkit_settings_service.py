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

"""Unit tests for the toolkit settings service."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.models import CodeFields, CodeRepoType, GitRepo, ToolConfig
from codemie.rest_api.models.assistant import Assistant, ContextType
from codemie.rest_api.security.user import User
from codemie.service.tools.toolkit_settings_service import ToolkitSettingService
from codemie_tools.base.models import Tool, ToolKit, ToolSet


@pytest.fixture
def mock_user():
    """Create a mock user."""
    user = MagicMock(spec=User)
    user.id = "test-user-id"
    user.is_admin = False
    return user


@pytest.fixture
def mock_admin_user():
    """Create a mock admin user."""
    user = MagicMock(spec=User)
    user.id = "admin-user-id"
    user.is_admin = True
    return user


@pytest.fixture
def mock_assistant():
    """Create a mock assistant."""
    assistant = MagicMock(spec=Assistant)
    assistant.id = "test-assistant-id"
    assistant.project = "test-project"
    assistant.context = []
    return assistant


@pytest.fixture
def mock_code_fields():
    """Create mock code fields."""
    return CodeFields(app_name="test-app", repo_name="test-repo", index_type="code")


@pytest.fixture
def mock_git_repo():
    """Create a mock git repo."""
    repo = MagicMock(spec=GitRepo)
    repo.link = "https://github.com/test/repo.git"
    repo.branch = "main"
    repo.get_type.return_value = CodeRepoType.GITHUB
    return repo


@pytest.fixture
def sample_tool_configs():
    """Create sample tool configurations."""
    return [
        ToolConfig(name="GIT", integration_id="git-integration-1"),
        ToolConfig(name="Plugin", integration_id="plugin-integration-1"),
    ]


@pytest.fixture
def sample_toolkits():
    """Create sample toolkits list."""
    return [
        ToolKit(toolkit=ToolSet.PLUGIN, tools=[Tool(name="plugin_tool", description="Plugin tool")]),
        ToolKit(toolkit=ToolSet.DATA_MANAGEMENT, tools=[Tool(name="elastic", description="ES tool")]),
        ToolKit(toolkit=ToolSet.PROJECT_MANAGEMENT, tools=[Tool(name="ZephyrScale", description="Zephyr tool")]),
        ToolKit(toolkit=ToolSet.CODE_QUALITY, tools=[Tool(name="Sonar", description="Sonar tool")]),
    ]


class TestFindToolConfigByName:
    """Tests for _find_tool_config_by_name method."""

    def test_find_tool_config_found(self, sample_tool_configs):
        """Test finding an existing tool configuration."""
        # Act
        result = ToolkitSettingService._find_tool_config_by_name(sample_tool_configs, "GIT")

        # Assert
        assert result is not None
        assert result.name == "GIT"
        assert result.integration_id == "git-integration-1"

    def test_find_tool_config_not_found(self, sample_tool_configs):
        """Test finding a non-existent tool configuration."""
        # Act
        result = ToolkitSettingService._find_tool_config_by_name(sample_tool_configs, "NONEXISTENT")

        # Assert
        assert result is None

    def test_find_tool_config_none_list(self):
        """Test with None tools_config list."""
        # Act
        result = ToolkitSettingService._find_tool_config_by_name(None, "GIT")

        # Assert
        assert result is None

    def test_find_tool_config_empty_list(self):
        """Test with empty tools_config list."""
        # Act
        result = ToolkitSettingService._find_tool_config_by_name([], "GIT")

        # Assert
        assert result is None


class TestHasAssistantToolkitToolByName:
    """Tests for _has_assistant_toolkit_tool_by_name method."""

    def test_has_tool_found(self, sample_toolkits):
        """Test when tool is found in toolkits."""
        # Act
        result = ToolkitSettingService._has_assistant_toolkit_tool_by_name(sample_toolkits, "plugin_tool")

        # Assert
        assert result is True

    def test_has_tool_not_found(self, sample_toolkits):
        """Test when tool is not found in toolkits."""
        # Act
        result = ToolkitSettingService._has_assistant_toolkit_tool_by_name(sample_toolkits, "nonexistent_tool")

        # Assert
        assert result is False

    def test_has_tool_empty_toolkits(self):
        """Test with empty toolkits list."""
        # Act
        result = ToolkitSettingService._has_assistant_toolkit_tool_by_name([], "plugin_tool")

        # Assert
        assert result is False


class TestFindCodeIndex:
    """Tests for _find_code_index method."""

    @patch('codemie.service.tools.toolkit_settings_service.CodeIndexInfo')
    def test_find_code_index_found(self, mock_code_index_info):
        """Test finding an existing code index."""
        # Arrange
        mock_index = MagicMock()
        mock_index.index_type = "code"
        mock_code_index_info.filter_by_project_and_repo.return_value = [mock_index]

        # Act
        result = ToolkitSettingService._find_code_index("test-project", "test-repo")

        # Assert
        assert result == mock_index
        mock_code_index_info.filter_by_project_and_repo.assert_called_once_with(
            project_name="test-project", repo_name="test-repo"
        )

    @patch('codemie.service.tools.toolkit_settings_service.CodeIndexInfo')
    def test_find_code_index_not_found(self, mock_code_index_info):
        """Test when code index is not found."""
        # Arrange
        mock_code_index_info.filter_by_project_and_repo.return_value = []

        # Act
        result = ToolkitSettingService._find_code_index("test-project", "test-repo")

        # Assert
        assert result is None


class TestGetGitToolsWithCreds:
    """Tests for get_git_tools_with_creds method."""

    @patch('codemie.service.tools.toolkit_settings_service.get_indexed_repo')
    @patch('codemie.service.settings.settings.SettingsService')
    @patch('codemie.service.tools.toolkit_settings_service.get_llm_by_credentials')
    @patch('codemie.service.tools.toolkit_settings_service.GitToolkit')
    def test_get_git_tools_github_repo(
        self, mock_git_toolkit, mock_get_llm, mock_settings, mock_get_repo, mock_code_fields, mock_git_repo
    ):
        """Test getting git tools for GitHub repository."""
        # Arrange
        mock_get_repo.return_value = mock_git_repo
        mock_creds = MagicMock()
        mock_creds.token = "test-token"
        mock_creds.token_name = "test-token-name"
        mock_settings.get_git_creds.return_value = mock_creds

        mock_llm = MagicMock()
        mock_get_llm.return_value = mock_llm

        mock_toolkit_instance = MagicMock()
        mock_tool = MagicMock()
        mock_tool.name = "git_tool"
        mock_tool.description = "Git tool description"
        mock_toolkit_instance.get_tools.return_value = [mock_tool]
        mock_git_toolkit.get_toolkit.return_value = mock_toolkit_instance

        # Act
        with patch('codemie.service.tools.toolkit_settings_service.CodeToolkit'):
            tools = ToolkitSettingService.get_git_tools_with_creds(
                code_fields=mock_code_fields,
                project_name="test-project",
                user_id="test-user",
                llm_model=mock_llm,
                request_uuid="test-uuid",
            )

        # Assert
        assert len(tools) == 1
        mock_settings.get_git_creds.assert_called_once()
        mock_git_toolkit.get_toolkit.assert_called_once()

    @patch('codemie.service.tools.toolkit_settings_service.get_indexed_repo')
    @patch('codemie.service.settings.settings.SettingsService')
    @patch('codemie.service.tools.toolkit_settings_service.get_llm_by_credentials')
    @patch('codemie.service.tools.toolkit_settings_service.GitToolkit')
    def test_get_git_tools_azure_devops_repo(
        self, mock_git_toolkit, mock_get_llm, mock_settings, mock_get_repo, mock_code_fields
    ):
        """Test getting git tools for Azure DevOps repository."""
        # Arrange
        mock_repo = MagicMock(spec=GitRepo)
        mock_repo.link = "https://dev.azure.com/org/project/_git/repo"
        mock_repo.branch = "main"
        mock_repo.get_type.return_value = CodeRepoType.AZURE_DEVOPS_REPOS
        mock_get_repo.return_value = mock_repo

        mock_git_creds = MagicMock()
        mock_git_creds.token = "test-token"
        mock_git_creds.token_name = "test-token-name"
        mock_settings.get_git_creds.return_value = mock_git_creds

        mock_azure_creds = MagicMock()
        mock_azure_creds.base_url = "https://dev.azure.com"
        mock_azure_creds.organization = "test-org"
        mock_azure_creds.project = "test-project"
        mock_settings.get_azure_devops_creds.return_value = mock_azure_creds

        mock_llm = MagicMock()
        mock_get_llm.return_value = mock_llm

        mock_toolkit_instance = MagicMock()
        mock_toolkit_instance.get_tools.return_value = [MagicMock()]
        mock_git_toolkit.get_toolkit.return_value = mock_toolkit_instance

        # Act
        with patch('codemie.service.tools.toolkit_settings_service.CodeToolkit'):
            tools = ToolkitSettingService.get_git_tools_with_creds(
                code_fields=mock_code_fields,
                project_name="test-project",
                user_id="test-user",
                llm_model=mock_llm,
                request_uuid="test-uuid",
            )

        # Assert
        assert len(tools) == 1
        mock_settings.get_azure_devops_creds.assert_called_once()


class TestGetFileSystemToolkit:
    """Tests for get_file_system_toolkit method."""

    @patch('codemie.service.settings.settings.SettingsService')
    @patch('codemie.service.tools.toolkit_settings_service.get_llm_by_credentials')
    @patch('codemie.service.tools.toolkit_settings_service.FileSystemToolkit')
    @patch('codemie.service.tools.toolkit_settings_service.FileRepositoryFactory')
    def test_get_file_system_toolkit_no_context(
        self, mock_file_repo_factory, mock_fs_toolkit, mock_get_llm, mock_settings, mock_assistant, mock_user
    ):
        """Test getting file system toolkit without context."""
        # Arrange
        mock_llm = MagicMock()
        mock_get_llm.return_value = mock_llm

        mock_file_config = None
        mock_settings.get_file_system_config.return_value = mock_file_config

        mock_toolkit_instance = MagicMock()
        mock_toolkit_instance.get_tools.return_value = [MagicMock()]
        mock_fs_toolkit.get_toolkit.return_value = mock_toolkit_instance

        # Act
        tools = ToolkitSettingService.get_file_system_toolkit(
            assistant=mock_assistant,
            project_name="test-project",
            user=mock_user,
            llm_model=mock_llm,
            request_uuid="test-uuid",
        )

        # Assert
        assert len(tools) == 1
        mock_fs_toolkit.get_toolkit.assert_called_once()

    @patch('codemie.service.settings.settings.SettingsService')
    @patch('codemie.service.tools.toolkit_settings_service.get_llm_by_credentials')
    @patch('codemie.service.tools.toolkit_settings_service.FileSystemToolkit')
    @patch('codemie.service.tools.toolkit_settings_service.FileRepositoryFactory')
    @patch('codemie.service.tools.toolkit_settings_service.ToolkitSettingService._find_code_index')
    def test_get_file_system_toolkit_with_code_context(
        self,
        mock_find_code_index,
        mock_file_repo_factory,
        mock_fs_toolkit,
        mock_get_llm,
        mock_settings,
        mock_assistant,
        mock_user,
    ):
        """Test getting file system toolkit with code context."""
        # Arrange
        mock_context = MagicMock()
        mock_context.context_type = ContextType.CODE
        mock_context.name = "test-repo"
        mock_assistant.context = [mock_context]

        mock_code_index = MagicMock()
        mock_code_index.index_type = "code"
        mock_find_code_index.return_value = mock_code_index

        mock_llm = MagicMock()
        mock_get_llm.return_value = mock_llm

        mock_file_config = MagicMock()
        mock_file_config.root_directory = "/test/dir"
        mock_file_config.activate_command = "source venv/bin/activate"
        mock_settings.get_file_system_config.return_value = mock_file_config

        mock_toolkit_instance = MagicMock()
        mock_toolkit_instance.get_tools.return_value = [MagicMock()]
        mock_fs_toolkit.get_toolkit.return_value = mock_toolkit_instance

        # Act
        tools = ToolkitSettingService.get_file_system_toolkit(
            assistant=mock_assistant,
            project_name="test-project",
            user=mock_user,
            llm_model=mock_llm,
            request_uuid="test-uuid",
        )

        # Assert
        assert len(tools) == 1
        mock_find_code_index.assert_called_once_with("test-project", "test-repo")
