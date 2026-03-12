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

from codemie_tools.base.models import ToolSet
from langchain_core.tools import BaseTool

from codemie.core.models import CodeFields
from codemie.rest_api.models.assistant import Assistant, Context, ContextType, ToolKitDetails
from codemie.service.llm_service.llm_service import LLMService
from codemie.service.tools import ToolkitService
from codemie.service.tools.toolkit_settings_service import ToolkitSettingService


class TestAssistantServiceAddGitTools:
    """Test suite for AssistantService._add_git_related_tools method with code context."""

    @pytest.fixture
    def mock_assistant(self):
        """Create a mock assistant with GIT and VCS toolkits."""
        assistant = MagicMock(spec=Assistant)
        assistant.project = "test-project"
        assistant.id = "test-assistant-id"

        # Create toolkit for GIT tools
        git_toolkit = MagicMock(spec=ToolKitDetails)
        git_toolkit.toolkit = ToolSet.GIT
        git_tool1 = MagicMock()
        git_tool1.name = "git_tool1"
        git_tool2 = MagicMock()
        git_tool2.name = "git_tool2"
        git_toolkit.tools = [git_tool1, git_tool2]

        # Create toolkit for VCS tools
        vcs_toolkit = MagicMock(spec=ToolKitDetails)
        vcs_toolkit.toolkit = ToolSet.VCS
        vcs_tool1 = MagicMock()
        vcs_tool1.name = "vcs_tool1"
        vcs_toolkit.tools = [vcs_tool1]

        assistant.toolkits = [git_toolkit, vcs_toolkit]
        return assistant

    @pytest.fixture
    def mock_code_context(self):
        """Create a mock code context."""
        context = MagicMock(spec=Context)
        context.context_type = ContextType.CODE
        context.name = "test-repo"
        return context

    @pytest.fixture
    def mock_code_fields(self):
        """Create mock code fields object."""
        return MagicMock(spec=CodeFields)

    @pytest.fixture
    def mock_git_tools(self):
        """Create mock Git tools."""
        git_tool1 = MagicMock(spec=BaseTool)
        git_tool1.name = "git_tool1"
        git_tool2 = MagicMock(spec=BaseTool)
        git_tool2.name = "git_tool2"
        return [git_tool1, git_tool2]

    @pytest.fixture
    def mock_vcs_tools(self):
        """Create mock VCS tools."""
        vcs_tool1 = MagicMock(spec=BaseTool)
        vcs_tool1.name = "vcs_tool1"
        return [vcs_tool1]

    def test_add_git_related_tools_with_git_toolkit_only(
        self, mock_assistant, mock_code_context, mock_code_fields, mock_git_tools
    ):
        """
        Test that _add_git_related_tools correctly adds only Git tools when
        the assistant has only the GIT toolkit and the context is of type CODE.
        """
        # Arrange
        tools = []
        user_id = "test-user-id"
        request_uuid = "test-request-uuid"
        llm_model = LLMService.BASE_NAME_GPT_41
        is_react = True

        # Modify assistant to have only GIT toolkit
        git_toolkit = [toolkit for toolkit in mock_assistant.toolkits if toolkit.toolkit == ToolSet.GIT][0]
        mock_assistant.toolkits = [git_toolkit]

        # Set up mocks
        with (
            patch.object(ToolkitService, '_get_code_fields', return_value=mock_code_fields, autospec=True),
            patch.object(
                ToolkitSettingService, 'get_git_tools_with_creds', return_value=mock_git_tools
            ) as mock_get_git_tools,
            patch.object(
                ToolkitService, 'filter_tools', side_effect=lambda toolkits, toolkit_type, agent_tools: agent_tools
            ) as mock_filter_tools,
        ):
            # Act
            ToolkitService._add_git_related_tools(
                tools=tools,
                context=mock_code_context,
                assistant=mock_assistant,
                user_id=user_id,
                request_uuid=request_uuid,
                llm_model=llm_model,
                is_react=is_react,
            )

            # Assert
            # Verify that get_git_tools_with_creds was called
            mock_get_git_tools.assert_called_once()

            # Verify that filter_tools was called once (only for GIT)
            assert mock_filter_tools.call_count == 1

            # Verify that only git tools were added to the tools list
            assert len(tools) == 2
            assert all(tool in tools for tool in mock_git_tools)

    def test_add_git_related_tools_with_no_toolkits(self, mock_assistant, mock_code_context, mock_code_fields):
        """
        Test that _add_git_related_tools adds no tools when the assistant has
        neither GIT nor VCS toolkits and the context is of type CODE.
        """
        # Arrange
        tools = []
        user_id = "test-user-id"
        request_uuid = "test-request-uuid"
        llm_model = LLMService.BASE_NAME_GPT_41
        is_react = True

        # Modify assistant to have no relevant toolkits
        other_toolkit = MagicMock(spec=ToolKitDetails)
        other_toolkit.toolkit = "OTHER_TOOLKIT"
        mock_assistant.toolkits = [other_toolkit]

        # Set up mocks
        with (
            patch.object(ToolkitService, '_get_code_fields', return_value=mock_code_fields, autospec=True),
            patch.object(ToolkitSettingService, 'get_git_tools_with_creds') as mock_get_git_tools,
            patch.object(ToolkitService, 'filter_tools') as mock_filter_tools,
        ):
            # Act
            ToolkitService._add_git_related_tools(
                tools=tools,
                context=mock_code_context,
                assistant=mock_assistant,
                user_id=user_id,
                request_uuid=request_uuid,
                llm_model=llm_model,
                is_react=is_react,
            )

            mock_get_git_tools.assert_not_called()

            # Verify that filter_tools was not called
            mock_filter_tools.assert_not_called()

            # Verify that no tools were added to the tools list
            assert len(tools) == 0
