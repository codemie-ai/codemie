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

from codemie.rest_api.models.assistant import Assistant, Context, ContextType, ToolKitDetails
from codemie.service.llm_service.llm_service import LLMService
from codemie.service.tools import ToolkitService
from codemie.service.tools.toolkit_settings_service import ToolkitSettingService


class TestAssistantServiceAddGitToolsWithNonCodeContext:
    """Test suite for AssistantService._add_git_related_tools method with non-code context."""

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
    def mock_kb_context(self):
        """Create a mock knowledge base context."""
        context = MagicMock(spec=Context)
        context.context_type = ContextType.KNOWLEDGE_BASE
        context.name = "test-kb-repo"
        return context

    @pytest.fixture
    def mock_provider_context(self):
        """Create a mock provider context."""
        context = MagicMock(spec=Context)
        context.context_type = ContextType.PROVIDER
        context.name = "test-provider-repo"
        return context

    def test_add_git_related_tools_with_kb_context(self, mock_assistant, mock_kb_context):
        """
        Test that _add_git_related_tools does not add any Git tools when the context type is KNOWLEDGE_BASE.
        """
        # Arrange
        tools = []
        user_id = "test-user-id"
        request_uuid = "test-request-uuid"
        llm_model = LLMService.BASE_NAME_GPT_41
        is_react = True

        # Set up mocks for methods that should not be called
        with (
            patch.object(ToolkitService, '_get_code_fields', autospec=True) as mock_get_code_fields,
            patch.object(ToolkitSettingService, 'get_git_tools_with_creds') as mock_get_git_tools,
        ):
            # Act
            ToolkitService._add_git_related_tools(
                tools=tools,
                context=mock_kb_context,
                assistant=mock_assistant,
                user_id=user_id,
                request_uuid=request_uuid,
                llm_model=llm_model,
                is_react=is_react,
            )

            # Assert
            # Verify that no tools were added to the tools list
            assert len(tools) == 0

            # Verify that none of these methods were called
            mock_get_code_fields.assert_not_called()
            mock_get_git_tools.assert_not_called()

    def test_add_git_related_tools_with_provider_context(self, mock_assistant, mock_provider_context):
        """
        Test that _add_git_related_tools does not add any Git tools when the context type is PROVIDER.
        """
        # Arrange
        tools = []
        user_id = "test-user-id"
        request_uuid = "test-request-uuid"
        llm_model = LLMService.BASE_NAME_GPT_41
        is_react = True

        # Set up mocks for methods that should not be called
        with (
            patch.object(ToolkitService, '_get_code_fields', autospec=True) as mock_get_code_fields,
            patch.object(ToolkitSettingService, 'get_git_tools_with_creds') as mock_get_git_tools,
        ):
            # Act
            ToolkitService._add_git_related_tools(
                tools=tools,
                context=mock_provider_context,
                assistant=mock_assistant,
                user_id=user_id,
                request_uuid=request_uuid,
                llm_model=llm_model,
                is_react=is_react,
            )

            # Assert
            # Verify that no tools were added to the tools list
            assert len(tools) == 0

            # Verify that none of these methods were called
            mock_get_code_fields.assert_not_called()
            mock_get_git_tools.assert_not_called()

    def test_add_git_related_tools_with_multiple_context_types(
        self, mock_assistant, mock_kb_context, mock_provider_context
    ):
        """
        Test that _add_git_related_tools does not add any Git tools for various non-code context types.
        This test uses parametrization to test multiple context types in one test method.
        """
        # Arrange
        contexts = [mock_kb_context, mock_provider_context]
        user_id = "test-user-id"
        request_uuid = "test-request-uuid"
        llm_model = LLMService.BASE_NAME_GPT_41
        is_react = True

        # Set up mocks for methods that should not be called
        with (
            patch.object(ToolkitService, '_get_code_fields', autospec=True) as mock_get_code_fields,
            patch.object(ToolkitSettingService, 'get_git_tools_with_creds') as mock_get_git_tools,
        ):
            # Act & Assert
            for context in contexts:
                tools = []  # Reset tools for each context

                ToolkitService._add_git_related_tools(
                    tools=tools,
                    context=context,
                    assistant=mock_assistant,
                    user_id=user_id,
                    request_uuid=request_uuid,
                    llm_model=llm_model,
                    is_react=is_react,
                )

                # Verify that no tools were added to the tools list
                assert len(tools) == 0

            # Verify that none of these methods were called across all test iterations
            mock_get_code_fields.assert_not_called()
            mock_get_git_tools.assert_not_called()
