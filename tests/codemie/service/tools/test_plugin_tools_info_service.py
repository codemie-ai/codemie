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

"""Unit tests for the PluginToolsInfoService."""

from unittest.mock import Mock, patch

import pytest

from codemie.enterprise.plugin import PluginToolkitUI
from codemie.rest_api.security.user import User
from codemie.service.tools.plugin_tools_info_service import (
    PluginToolsInfoService,
    PluginToolsInfoServiceError,
)
from codemie_tools.base.models import ToolSet


class TestPluginToolsInfoService:
    """Test suite for the PluginToolsInfoService class."""

    @pytest.fixture
    def mock_user(self):
        """Fixture for mocking User object."""
        user = Mock(spec=User)
        user.id = "test-user-id"
        user.project_names = ["test-project"]
        return user

    @pytest.fixture
    def mock_langchain_tools(self):
        """Fixture for mocking LangChain BaseTool instances returned by enterprise solution."""
        tool1 = Mock()
        tool1.name = "_test_tool_abc"
        tool1.description = "Test tool description"

        tool2 = Mock()
        tool2.name = "_another_tool_xyz"
        tool2.description = "Another tool description"

        return [tool1, tool2]

    @patch("codemie.service.tools.plugin_tools_info_service.get_plugin_tools_for_assistant")
    def test_get_plugin_toolkit_info_success(self, mock_get_tools, mock_user, mock_langchain_tools):
        """Test get_plugin_toolkit_info with valid credentials and tools."""
        from codemie.core.models import ToolConfig

        mock_get_tools.return_value = mock_langchain_tools

        plugin_setting_id = "test-setting-id"
        project_name = "test-project"

        result = PluginToolsInfoService.get_plugin_toolkit_info(
            plugin_setting_id=plugin_setting_id, user=mock_user, project_name=project_name
        )

        # Verify the call - should pass ToolConfig object
        expected_tool_config = ToolConfig(name=ToolSet.PLUGIN.value, integration_id=plugin_setting_id)
        mock_get_tools.assert_called_once_with(
            user_id=mock_user.id,
            project_name=project_name,
            tool_config=expected_tool_config,
        )

        assert isinstance(result, PluginToolkitUI)
        assert len(result.tools) == 2
        assert result.tools[0].name == "_test_tool"
        assert result.tools[0].label == " Test Tool"
        assert result.tools[1].name == "_another_tool"
        assert result.tools[1].label == " Another Tool"

    @patch("codemie.service.tools.plugin_tools_info_service.get_plugin_tools_for_assistant")
    def test_get_plugin_toolkit_info_without_plugin_setting_id(self, mock_get_tools, mock_user, mock_langchain_tools):
        """Test get_plugin_toolkit_info without plugin_setting_id."""
        mock_get_tools.return_value = mock_langchain_tools

        result = PluginToolsInfoService.get_plugin_toolkit_info(
            plugin_setting_id=None, user=mock_user, project_name="test-project"
        )

        mock_get_tools.assert_called_once_with(user_id=mock_user.id, project_name="test-project", tool_config=None)
        assert isinstance(result, PluginToolkitUI)
        assert len(result.tools) == 2

    @patch("codemie.service.tools.plugin_tools_info_service.get_plugin_tools_for_assistant")
    def test_get_plugin_toolkit_info_uses_default_project(self, mock_get_tools, mock_user, mock_langchain_tools):
        """Test get_plugin_toolkit_info uses first application as default project."""
        mock_get_tools.return_value = mock_langchain_tools

        result = PluginToolsInfoService.get_plugin_toolkit_info(
            plugin_setting_id="test-setting-id", user=mock_user, project_name=None
        )

        mock_get_tools.assert_called_once()
        call_args = mock_get_tools.call_args
        assert call_args[1]["project_name"] == "test-project"
        assert isinstance(result, PluginToolkitUI)

    @patch("codemie.service.tools.plugin_tools_info_service.get_plugin_tools_for_assistant")
    def test_get_plugin_toolkit_info_no_tools(self, mock_get_tools, mock_user):
        """Test get_plugin_toolkit_info raises error when no tools returned."""
        mock_get_tools.return_value = []

        with pytest.raises(PluginToolsInfoServiceError, match="No plugin tools found"):
            PluginToolsInfoService.get_plugin_toolkit_info(
                plugin_setting_id="test-setting-id", user=mock_user, project_name="test-project"
            )

    @patch("codemie.service.tools.plugin_tools_info_service.get_plugin_tools_for_assistant")
    def test_get_plugin_toolkit_info_exception(self, mock_get_tools, mock_user):
        """Test get_plugin_toolkit_info handles exceptions from enterprise solution."""
        mock_get_tools.side_effect = Exception("Enterprise plugin error")

        with pytest.raises(PluginToolsInfoServiceError, match="Error retrieving plugin toolkit"):
            PluginToolsInfoService.get_plugin_toolkit_info(
                plugin_setting_id="test-setting-id", user=mock_user, project_name="test-project"
            )

    @patch("codemie.service.tools.plugin_tools_info_service.get_plugin_tools_for_assistant")
    def test_get_plugin_toolkit_info_skips_tools_without_name(self, mock_get_tools, mock_user):
        """Test get_plugin_toolkit_info skips tools without name."""
        tool1 = Mock()
        tool1.name = "_valid_tool_abc"
        tool1.description = "Valid tool"

        tool2 = Mock()
        tool2.name = None
        tool2.description = "No name tool"

        tool3 = Mock()
        tool3.name = ""
        tool3.description = "Empty name tool"

        mock_get_tools.return_value = [tool1, tool2, tool3]

        result = PluginToolsInfoService.get_plugin_toolkit_info(
            plugin_setting_id="test-setting-id", user=mock_user, project_name="test-project"
        )

        assert isinstance(result, PluginToolkitUI)
        assert len(result.tools) == 1
        assert result.tools[0].name == "_valid_tool"

    @patch("codemie.service.tools.plugin_tools_info_service.get_plugin_tools_for_assistant")
    def test_get_plugin_toolkit_info_no_tools_with_valid_names(self, mock_get_tools, mock_user):
        """Test get_plugin_toolkit_info raises error when no tools have valid names."""
        tool1 = Mock()
        tool1.name = None
        tool1.description = "No name"

        tool2 = Mock()
        tool2.name = ""
        tool2.description = "Empty name"

        mock_get_tools.return_value = [tool1, tool2]

        with pytest.raises(PluginToolsInfoServiceError, match="No tools found"):
            PluginToolsInfoService.get_plugin_toolkit_info(
                plugin_setting_id="test-setting-id", user=mock_user, project_name="test-project"
            )

    @patch("codemie.service.tools.plugin_tools_info_service.get_plugin_tools_for_assistant")
    def test_tool_label_formatting(self, mock_get_tools, mock_user):
        """Test that tool labels are formatted correctly (underscores to spaces, title case)."""
        tool = Mock()
        tool.name = "_create_jira_ticket_abc"
        tool.description = "Creates a JIRA ticket"

        mock_get_tools.return_value = [tool]

        result = PluginToolsInfoService.get_plugin_toolkit_info(
            plugin_setting_id="test-setting-id", user=mock_user, project_name="test-project"
        )

        assert result.tools[0].label == " Create Jira Ticket"

    @patch("codemie.service.tools.plugin_tools_info_service.get_plugin_tools_for_assistant")
    def test_get_plugin_toolkit_info_with_empty_applications_list(self, mock_get_tools, mock_langchain_tools):
        """Test get_plugin_toolkit_info when user has empty applications list."""
        user = Mock(spec=User)
        user.id = "test-user-id"
        user.project_names = []

        mock_get_tools.return_value = mock_langchain_tools

        result = PluginToolsInfoService.get_plugin_toolkit_info(
            plugin_setting_id="test-setting-id", user=user, project_name=None
        )

        mock_get_tools.assert_called_once()
        call_args = mock_get_tools.call_args
        assert call_args[1]["project_name"] is None
        assert isinstance(result, PluginToolkitUI)
