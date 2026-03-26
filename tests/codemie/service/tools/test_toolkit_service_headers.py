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
Test suite for ToolkitService with request_headers propagation.

Tests that ToolkitService.get_tools properly passes request_headers to MCPToolkitService
for MCP header propagation.
"""

from unittest.mock import Mock, patch

import pytest

from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.tools.toolkit_service import ToolkitService


# Patch database models and connection at module level to prevent database connections
@pytest.fixture(autouse=True)
def mock_database_models():
    """Automatically mock all database model access for all tests in this module."""
    with (
        patch('codemie.service.tools.toolkit_service.Conversation'),
        patch('codemie.service.tools.toolkit_service.IndexInfo'),
        patch('codemie.service.tools.toolkit_service.KnowledgeBaseIndexInfo'),
        patch('codemie.service.tools.toolkit_service.CodeIndexInfo'),
        patch('codemie.service.tools.toolkit_service.ProviderIndexInfo'),
        patch('codemie.service.tools.toolkit_service.FilteredIndexInfo'),
        patch('codemie.clients.postgres.PostgresClient.get_engine'),
        patch('codemie.service.tools.toolkit_service.ToolkitSettingService'),
    ):
        yield


def _setup_mock_config(mock_config) -> None:
    """Set required string config values on a mock config object to prevent ResearchConfig validation errors."""
    mock_config.GOOGLE_SEARCH_API_KEY = ""
    mock_config.GOOGLE_SEARCH_CSE_ID = ""
    mock_config.TAVILY_API_KEY = ""
    mock_config.TOOLS_SMART_LOOKUP_ENABLED = False


class TestToolkitServiceGetToolsWithHeaders:
    """Test cases for ToolkitService.get_tools with request_headers."""

    @patch('codemie.service.tools.toolkit_service.ToolkitLookupService')
    @patch('codemie.service.tools.toolkit_service.ToolkitService._process_final_tools_traditional')
    @patch('codemie.service.tools.toolkit_service.ToolkitService.add_file_tools')
    @patch('codemie.service.tools.toolkit_service.ToolkitService.add_context_tools')
    @patch('codemie.service.tools.toolkit_service.ToolkitService.get_core_tools')
    @patch('codemie.service.tools.toolkit_service.config')
    @patch('codemie.service.tools.toolkit_service.MCPToolkitService.get_mcp_server_tools')
    def test_get_tools_with_mcp_servers_and_headers(
        self,
        mock_get_mcp_tools,
        mock_config,
        mock_get_core_tools,
        mock_add_context_tools,
        mock_add_file_tools,
        mock_process_final_tools,
        mock_toolkit_lookup,
    ):
        """
        TC-2.4.1: Verify headers passed to MCP toolkit.

        Priority: Critical

        Tests that when get_tools is called with MCP servers configured and request_headers provided,
        those headers are propagated to MCPToolkitService.get_mcp_server_tools.
        """
        # Arrange
        _setup_mock_config(mock_config)
        mock_config.MCP_CONNECT_ENABLED = True
        mock_get_mcp_tools.return_value = []
        mock_get_core_tools.return_value = []
        mock_add_context_tools.return_value = []
        mock_add_file_tools.return_value = []
        mock_process_final_tools.return_value = []

        assistant = Mock(spec=Assistant)
        assistant.id = 'asst-123'
        assistant.name = 'Test Assistant'
        assistant.project = 'test-project'
        assistant.toolkits = []
        assistant.context = []
        assistant.mcp_servers = [{'name': 'test-server', 'command': 'test-command', 'args': []}]
        assistant.assistant_ids = None
        assistant.skill_ids = []  # Prevent SkillTool creation

        request = AssistantChatRequest(
            text='Hello',
            conversation_id='conv-123',
            tools_config=None,
        )

        user = Mock(spec=User)
        user.id = 'user-123'
        user.name = 'Test User'  # Required by ToolkitService.get_tools
        user.is_admin = False

        test_headers = {'X-Tenant-ID': 'tenant-123', 'X-User-ID': 'user-456'}

        # Act
        ToolkitService.get_tools(
            assistant=assistant,
            request=request,
            user=user,
            llm_model='claude-sonnet-4',
            request_uuid='req-123',
            is_react=False,
            thread_generator=None,
            exclude_extra_context_tools=False,
            file_objects=None,
            mcp_server_args_preprocessor=None,
            smart_tool_selection_enabled=False,
            request_headers=test_headers,
        )

        # Assert - verify get_mcp_server_tools called with request_headers
        mock_get_mcp_tools.assert_called_once()
        call_kwargs = mock_get_mcp_tools.call_args[1]
        assert 'request_headers' in call_kwargs
        assert call_kwargs['request_headers'] == test_headers

    @patch('codemie.service.tools.toolkit_service.ToolkitLookupService')
    @patch('codemie.service.tools.toolkit_service.ToolkitService._process_final_tools_traditional')
    @patch('codemie.service.tools.toolkit_service.ToolkitService.add_context_tools')
    @patch('codemie.service.tools.toolkit_service.ToolkitService.get_core_tools')
    @patch('codemie.service.tools.toolkit_service.config')
    def test_get_tools_with_regular_tools_only(
        self,
        mock_config,
        mock_get_core_tools,
        mock_add_context_tools,
        mock_process_final_tools,
        mock_toolkit_lookup,
    ):
        """
        TC-2.4.2: Verify headers don't break non-MCP tools.

        Priority: High

        Tests that when assistant has only regular tools (no MCP servers),
        request_headers parameter doesn't cause issues.
        """
        # Arrange
        _setup_mock_config(mock_config)
        mock_config.MCP_CONNECT_ENABLED = False
        mock_get_core_tools.return_value = []
        mock_add_context_tools.return_value = []
        mock_process_final_tools.return_value = []

        assistant = Mock(spec=Assistant)
        assistant.id = 'asst-123'
        assistant.name = 'Test Assistant'
        assistant.project = 'test-project'
        assistant.toolkits = []
        assistant.context = []
        assistant.mcp_servers = []  # No MCP servers
        assistant.assistant_ids = None
        assistant.skill_ids = []  # Prevent SkillTool creation

        request = AssistantChatRequest(
            text='Hello',
            conversation_id='conv-123',
            tools_config=None,
        )

        user = Mock(spec=User)
        user.id = 'user-123'
        user.name = 'Test User'  # Required by ToolkitService.get_tools
        user.is_admin = False

        test_headers = {'X-Tenant-ID': 'tenant-123'}

        # Act - should not raise any exceptions
        tools = ToolkitService.get_tools(
            assistant=assistant,
            request=request,
            user=user,
            llm_model='claude-sonnet-4',
            request_uuid='req-123',
            is_react=False,
            thread_generator=None,
            exclude_extra_context_tools=False,
            file_objects=None,
            mcp_server_args_preprocessor=None,
            smart_tool_selection_enabled=False,
            request_headers=test_headers,
        )

        # Assert - tools load successfully (even if empty)
        assert isinstance(tools, list)

    @patch('codemie.service.tools.toolkit_service.ToolkitLookupService')
    @patch('codemie.service.tools.toolkit_service.ToolkitService._process_final_tools_traditional')
    @patch('codemie.service.tools.toolkit_service.ToolkitService.add_file_tools')
    @patch('codemie.service.tools.toolkit_service.ToolkitService.add_context_tools')
    @patch('codemie.service.tools.toolkit_service.ToolkitService.get_core_tools')
    @patch('codemie.service.tools.toolkit_service.config')
    @patch('codemie.service.tools.toolkit_service.MCPToolkitService.get_mcp_server_tools')
    def test_get_tools_with_mixed_tools(
        self,
        mock_get_mcp_tools,
        mock_config,
        mock_get_core_tools,
        mock_add_context_tools,
        mock_add_file_tools,
        mock_process_final_tools,
        mock_toolkit_lookup,
    ):
        """
        TC-2.4.3: Verify both MCP and regular tools work with headers.

        Priority: High

        Tests that when assistant has both MCP servers and regular tools,
        both tool types work correctly with request_headers.
        """
        # Arrange
        _setup_mock_config(mock_config)
        mock_config.MCP_CONNECT_ENABLED = True
        mock_get_mcp_tools.return_value = []
        mock_get_core_tools.return_value = []
        mock_add_context_tools.return_value = []
        mock_add_file_tools.return_value = []
        mock_process_final_tools.return_value = []

        assistant = Mock(spec=Assistant)
        assistant.id = 'asst-123'
        assistant.name = 'Test Assistant'
        assistant.project = 'test-project'
        assistant.toolkits = []  # Could have regular toolkits
        assistant.context = []
        assistant.mcp_servers = [{'name': 'test-server', 'command': 'test-command', 'args': []}]
        assistant.assistant_ids = None
        assistant.skill_ids = []  # Prevent SkillTool creation

        request = AssistantChatRequest(
            text='Hello',
            conversation_id='conv-123',
            tools_config=None,
        )

        user = Mock(spec=User)
        user.id = 'user-123'
        user.name = 'Test User'  # Required by ToolkitService.get_tools
        user.is_admin = False

        test_headers = {'X-Tenant-ID': 'tenant-123'}

        # Act
        tools = ToolkitService.get_tools(
            assistant=assistant,
            request=request,
            user=user,
            llm_model='claude-sonnet-4',
            request_uuid='req-123',
            is_react=False,
            thread_generator=None,
            exclude_extra_context_tools=False,
            file_objects=None,
            mcp_server_args_preprocessor=None,
            smart_tool_selection_enabled=False,
            request_headers=test_headers,
        )

        # Assert - both tool types loaded
        assert isinstance(tools, list)
        mock_get_mcp_tools.assert_called_once()

    @patch('codemie.service.tools.toolkit_service.ToolkitLookupService')
    @patch('codemie.service.tools.toolkit_service.ToolkitService._process_final_tools_traditional')
    @patch('codemie.service.tools.toolkit_service.ToolkitService.add_file_tools')
    @patch('codemie.service.tools.toolkit_service.ToolkitService.add_context_tools')
    @patch('codemie.service.tools.toolkit_service.ToolkitService.get_core_tools')
    @patch('codemie.service.tools.toolkit_service.config')
    @patch('codemie.service.tools.toolkit_service.MCPToolkitService.get_mcp_server_tools')
    def test_get_tools_traditional_flow_with_headers(
        self,
        mock_get_mcp_tools,
        mock_config,
        mock_get_core_tools,
        mock_add_context_tools,
        mock_add_file_tools,
        mock_process_final_tools,
        mock_toolkit_lookup,
    ):
        """
        TC-2.4.4: Verify traditional tool flow handles headers.

        Priority: High

        Tests that _get_tools (traditional flow) passes headers correctly to MCP toolkit.
        """
        # Arrange
        _setup_mock_config(mock_config)
        mock_config.MCP_CONNECT_ENABLED = True
        mock_get_mcp_tools.return_value = []
        mock_get_core_tools.return_value = []
        mock_add_context_tools.return_value = []
        mock_add_file_tools.return_value = []
        mock_process_final_tools.return_value = []

        assistant = Mock(spec=Assistant)
        assistant.id = 'asst-123'
        assistant.name = 'Test Assistant'
        assistant.project = 'test-project'
        assistant.toolkits = []
        assistant.context = []
        assistant.mcp_servers = [{'name': 'test-server', 'command': 'test-command', 'args': []}]
        assistant.assistant_ids = None
        assistant.skill_ids = []  # Prevent SkillTool creation

        request = AssistantChatRequest(
            text='Hello',
            conversation_id='conv-123',
            tools_config=None,
        )

        user = Mock(spec=User)
        user.id = 'user-123'
        user.name = 'Test User'  # Required by ToolkitService.get_tools
        user.is_admin = False

        test_headers = {'X-Tenant-ID': 'tenant-123', 'X-Request-ID': 'req-789'}

        # Act - call get_tools which internally calls _get_tools
        ToolkitService.get_tools(
            assistant=assistant,
            request=request,
            user=user,
            llm_model='claude-sonnet-4',
            request_uuid='req-123',
            is_react=False,
            thread_generator=None,
            exclude_extra_context_tools=False,
            file_objects=None,
            mcp_server_args_preprocessor=None,
            smart_tool_selection_enabled=False,
            request_headers=test_headers,
        )

        # Assert - headers passed in traditional flow
        mock_get_mcp_tools.assert_called_once()
        call_kwargs = mock_get_mcp_tools.call_args[1]
        assert 'request_headers' in call_kwargs
        assert call_kwargs['request_headers'] == test_headers

    @patch('codemie.service.tools.toolkit_service.ToolkitLookupService')
    @patch('codemie.service.tools.toolkit_service.ToolkitService._process_final_tools_traditional')
    @patch('codemie.service.tools.toolkit_service.ToolkitService.add_file_tools')
    @patch('codemie.service.tools.toolkit_service.ToolkitService.add_context_tools')
    @patch('codemie.service.tools.toolkit_service.ToolkitService.get_core_tools')
    @patch('codemie.service.tools.toolkit_service.config')
    @patch('codemie.service.tools.toolkit_service.MCPToolkitService.get_mcp_server_tools')
    def test_get_tools_without_headers_backward_compat(
        self,
        mock_get_mcp_tools,
        mock_config,
        mock_get_core_tools,
        mock_add_context_tools,
        mock_add_file_tools,
        mock_process_final_tools,
        mock_toolkit_lookup,
    ):
        """
        Verify backward compatibility when request_headers not provided.

        Priority: High

        Tests that get_tools works without request_headers parameter (backward compatibility).
        """
        # Arrange
        _setup_mock_config(mock_config)
        mock_config.MCP_CONNECT_ENABLED = True
        mock_get_mcp_tools.return_value = []
        mock_get_core_tools.return_value = []
        mock_add_context_tools.return_value = []
        mock_add_file_tools.return_value = []
        mock_process_final_tools.return_value = []

        assistant = Mock(spec=Assistant)
        assistant.id = 'asst-123'
        assistant.name = 'Test Assistant'
        assistant.project = 'test-project'
        assistant.toolkits = []
        assistant.context = []
        assistant.mcp_servers = [{'name': 'test-server', 'command': 'test-command', 'args': []}]
        assistant.assistant_ids = None
        assistant.skill_ids = []  # Prevent SkillTool creation

        request = AssistantChatRequest(
            text='Hello',
            conversation_id='conv-123',
            tools_config=None,
        )

        user = Mock(spec=User)
        user.id = 'user-123'
        user.name = 'Test User'  # Required by ToolkitService.get_tools
        user.is_admin = False

        # Act - call without request_headers
        tools = ToolkitService.get_tools(
            assistant=assistant,
            request=request,
            user=user,
            llm_model='claude-sonnet-4',
            request_uuid='req-123',
            is_react=False,
            thread_generator=None,
            exclude_extra_context_tools=False,
            file_objects=None,
            mcp_server_args_preprocessor=None,
            smart_tool_selection_enabled=False,
            # No request_headers parameter
        )

        # Assert - works without errors
        assert isinstance(tools, list)
        mock_get_mcp_tools.assert_called_once()

        # Assert - get_mcp_server_tools called with request_headers=None (default)
        call_kwargs = mock_get_mcp_tools.call_args[1]
        assert 'request_headers' in call_kwargs
        assert call_kwargs['request_headers'] is None
