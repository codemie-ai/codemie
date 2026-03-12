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
Integration tests for tool preprocessors in the ToolkitService.

This module tests the complete flow of how preprocessors are applied to tools
by the ToolkitService, focusing on the integration points.
"""

import pytest
from unittest.mock import MagicMock, patch

from langchain_core.tools import Tool

from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant, ToolKitDetails
from codemie.rest_api.security.user import User
from codemie.service.assistant_service import ToolkitService
from codemie.service.llm_service.llm_service import LLMService
from codemie.service.tools.tools_preprocessing import (
    DescriptionPreprocessor,
    GPT4ToolsPreprocessor,
    ToolsPreprocessor,
    ToolsPreprocessorFactory,
)


class CustomTestPreprocessor(ToolsPreprocessor):
    """Custom preprocessor for testing integrated preprocessing."""

    def __init__(self, prefix="[CUSTOM] "):
        self.prefix = prefix
        self.process_called = False

    def process(self, tools):
        """Add a prefix to all tool descriptions and track being called."""
        import textwrap
        from codemie.configs.logger import logger

        self.process_called = True
        logger.info(f"Running CustomTestPreprocessor with prefix '{self.prefix}' on {len(tools)} tools")

        for tool in tools:
            if tool.description:
                # When we're adding a prefix, first dedent the description to handle
                # potential formatting issues when this runs before DescriptionPreprocessor
                # This handles the case where we run first, before DescriptionPreprocessor
                dedented = textwrap.dedent(tool.description).strip()
                if dedented:
                    tool.description = f"{self.prefix}{dedented}"
                else:
                    tool.description = f"{self.prefix}"

        return tools


@pytest.fixture
def mock_assistant():
    """Create a mock assistant for testing."""
    assistant = MagicMock(spec=Assistant)
    assistant.id = "test-assistant-id"
    assistant.name = "Test Assistant"
    assistant.project = "test-project"
    assistant.is_react = True
    assistant.llm_model_type = LLMService.BASE_NAME_GPT_41

    # Create a mock toolkit
    toolkit = MagicMock(spec=ToolKitDetails)
    toolkit.toolkit = "test-toolkit"
    toolkit.tools = []

    assistant.toolkits = [toolkit]
    assistant.context = []
    assistant.mcp_servers = []
    assistant.assistant_ids = []
    assistant.skill_ids = []  # Explicitly set to empty list to prevent SkillTool creation
    return assistant


@pytest.fixture
def mock_user():
    """Create a mock user for testing."""
    user = MagicMock(spec=User)
    user.id = "test-user-id"
    user.name = "Test User"
    return user


@pytest.fixture
def mock_request():
    """Create a mock request for testing."""
    request = MagicMock(spec=AssistantChatRequest)
    request.text = "Test request"
    request.conversation_id = None
    request.file_name = None
    request.ide_installation_id = None
    request.tools_config = None
    request.enable_web_search = None
    request.enable_code_interpreter = None
    return request


@pytest.fixture
def mock_tools():
    """Create mock tools for testing."""
    tool1 = Tool(name="tool1", func=lambda x: x, description="  \n  Tool 1 description\n  ")
    tool2 = Tool(name="tool2", func=lambda x: x, description="Tool 2 description")

    return [tool1, tool2]


class TestPreprocessorIntegration:
    """Integration tests for preprocessing in the full ToolkitService flow."""

    @patch('codemie.service.tools.ToolkitService.add_tools_with_creds')
    @patch('codemie.service.tools.ToolkitService.add_context_tools')
    @patch('codemie.service.tools.ToolkitService.add_file_tools')
    def test_end_to_end_gpt4_preprocessing(
        self,
        mock_add_file_tools,
        mock_add_context_tools,
        mock_add_tools_with_creds,
        mock_assistant,
        mock_user,
        mock_request,
    ):
        """Test end-to-end preprocessing with GPT-4 model."""
        # Setup tools with a description that needs preprocessing
        tool = Tool(
            name="test_long_tool",
            func=lambda x: x,
            description="  \n  " + ("A" * 2000) + "  \n  ",  # Long description with whitespace
        )

        # Configure mocks
        mock_add_tools_with_creds.return_value = [tool]
        mock_add_context_tools.return_value = []
        mock_add_file_tools.return_value = []

        # Call the method under test with GPT-4
        result = ToolkitService.get_tools(
            mock_assistant, mock_request, mock_user, LLMService.BASE_NAME_GPT_41, "request-uuid"
        )

        # Verify the result
        assert len(result) == 1

        # Check that whitespace was removed (DescriptionPreprocessor)
        assert not result[0].description.startswith("  \n  ")
        assert not result[0].description.endswith("  \n  ")

        # Check that description was truncated (GPT4ToolsPreprocessor)
        assert len(result[0].description) == GPT4ToolsPreprocessor.MAX_DESCRIPTION_LENGTH

    def test_custom_preprocessor_integration(self, mock_assistant, mock_user, mock_request, mock_tools):
        """Test integration with a custom preprocessor added to the chain."""
        # Create a custom preprocessor
        custom_preprocessor = CustomTestPreprocessor()

        # Configure mocks
        with patch('codemie.service.tools.ToolkitService.add_tools_with_creds') as mock_add_tools_with_creds:
            mock_add_tools_with_creds.return_value = mock_tools

            with patch('codemie.service.tools.ToolkitService.add_context_tools') as mock_add_context_tools:
                mock_add_context_tools.return_value = []

                with patch('codemie.service.tools.ToolkitService.add_file_tools') as mock_add_file_tools:
                    mock_add_file_tools.return_value = []

                    # Inject our custom preprocessor
                    with patch.object(
                        ToolsPreprocessorFactory,
                        'create_preprocessor_chain',
                        return_value=[DescriptionPreprocessor(), custom_preprocessor],
                    ):
                        # Call the method under test
                        result = ToolkitService.get_tools(
                            mock_assistant, mock_request, mock_user, "test-model", "request-uuid"
                        )

                        # Verify our custom preprocessor was called
                        assert custom_preprocessor.process_called

                        # Verify the result
                        assert len(result) == 2
                        assert result[0].description == "[CUSTOM] Tool 1 description"
                        assert result[1].description == "[CUSTOM] Tool 2 description"

    @patch('codemie.service.tools.ToolkitService.add_tools_with_creds')
    @patch('codemie.service.tools.ToolkitService.add_context_tools')
    @patch('codemie.service.tools.ToolkitService.add_file_tools')
    def test_preprocessor_order_matters(
        self,
        mock_add_file_tools,
        mock_add_context_tools,
        mock_add_tools_with_creds,
        mock_assistant,
        mock_user,
        mock_request,
    ):
        """Test that the order of preprocessors in the chain matters."""
        # Setup tool with indentation
        tool = Tool(name="test_tool", func=lambda x: x, description="  \n  Test description  \n  ")

        # Configure mocks
        mock_add_tools_with_creds.return_value = [tool]
        mock_add_context_tools.return_value = []
        mock_add_file_tools.return_value = []

        # Create custom preprocessors
        prefix_preprocessor = CustomTestPreprocessor(prefix="[PREFIX] ")

        # Test with prefix first, then description cleanup
        with patch.object(
            ToolsPreprocessorFactory,
            'create_preprocessor_chain',
            # Important: This means the prefix is added to the raw text with whitespace,
            # then the DescriptionPreprocessor cleans up the entire string including the prefix
            return_value=[prefix_preprocessor, DescriptionPreprocessor()],
        ):
            # Create a fresh copy of the tool for each test to avoid shared state
            tool1 = Tool(name="test_tool", func=lambda x: x, description="  \n  Test description  \n  ")
            mock_add_tools_with_creds.return_value = [tool1]

            result1 = ToolkitService.get_tools(mock_assistant, mock_request, mock_user, "test-model", "request-uuid")
            # Prefix is added first, then DescriptionPreprocessor should clean up the whitespace
            # The result should be a clean string with prefix and no extra whitespace
            assert result1[0].description == "[PREFIX] Test description"

        # Test with description cleanup first, then prefix
        with patch.object(
            ToolsPreprocessorFactory,
            'create_preprocessor_chain',
            # First clean the whitespace, then add the prefix to the clean text
            return_value=[DescriptionPreprocessor(), prefix_preprocessor],
        ):
            # Create a fresh copy of the tool for each test to avoid shared state
            tool2 = Tool(name="test_tool", func=lambda x: x, description="  \n  Test description  \n  ")
            mock_add_tools_with_creds.return_value = [tool2]

            result2 = ToolkitService.get_tools(mock_assistant, mock_request, mock_user, "test-model", "request-uuid")
            # Whitespace is removed first by DescriptionPreprocessor, then prefix is added
            assert result2[0].description == "[PREFIX] Test description"
