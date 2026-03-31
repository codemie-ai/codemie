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

"""Unit tests for the ToolkitService."""

from unittest.mock import Mock, patch

import pytest
from codemie_tools.base.file_object import FileObject
from codemie_tools.base.models import Tool, ToolKit, ToolSet
from langchain_core.tools import BaseTool, ToolException

from codemie.core.constants import CodeIndexType, ToolType
from codemie.core.models import AssistantChatRequest, CodeFields, IdeChatRequest, ToolConfig
from codemie.rest_api.models.assistant import Assistant, Context, ContextType, ToolKitDetails
from codemie.rest_api.models.conversation import Conversation
from codemie.rest_api.models.index import CodeIndexInfo, KnowledgeBaseIndexInfo, ProviderIndexInfo
from codemie.rest_api.security.user import User
from codemie.service.tools.toolkit_service import ToolkitService


class TestToolkitService:
    """Test suite for the ToolkitService class."""

    @pytest.fixture
    def mock_assistant(self):
        """Fixture for mocking Assistant."""
        assistant = Mock(spec=Assistant)
        assistant.id = "test-assistant-id"
        assistant.name = "Test Assistant"
        assistant.project = "test-project"
        assistant.toolkits = []
        assistant.context = []
        assistant.assistant_ids = []
        assistant.mcp_servers = []
        assistant.llm_model_type = "gpt-4"
        assistant.created_by = None
        return assistant

    @pytest.fixture
    def mock_user(self):
        """Fixture for mocking User."""
        user = Mock(spec=User)
        user.id = "test-user-id"
        user.name = "Test User"
        user.is_admin = False
        return user

    @pytest.fixture
    def mock_request(self):
        """Fixture for mocking AssistantChatRequest."""
        request = Mock(spec=AssistantChatRequest)
        request.text = "Test request"
        request.history = []
        request.top_k = 5
        request.tools_config = []
        request.conversation_id = "test-conversation-id"
        request.mcp_server_single_usage = False
        request.enable_web_search = None
        request.enable_code_interpreter = None
        return request

    @pytest.fixture
    def mock_toolkit(self):
        """Fixture for mocking ToolKit."""
        toolkit = ToolKit(
            toolkit="test_toolkit",
            tools=[Tool(name="test_tool", description="Test tool", label="Test Tool")],
        )
        return toolkit

    @pytest.fixture
    def mock_tool(self):
        """Fixture for mocking BaseTool."""
        tool = Mock(spec=BaseTool)
        tool.name = "test_tool"
        tool.description = "Test tool description"
        return tool

    def test_get_toolkit_methods(self):
        """Test get_toolkit_methods returns proper mapping."""
        # Mock all dependencies to avoid DB connections and external calls
        with patch("codemie.service.tools.toolkit_service.ToolkitSettingService"):
            with patch("codemie.service.tools.toolkit_service.ResearchToolkit"):
                with patch("codemie.service.tools.toolkit_service.ResearchConfig"):
                    with patch("codemie.service.tools.toolkit_service.config") as mock_config:
                        with patch("codemie.service.tools.toolkit_service.ProviderToolkitsFactory") as mock_provider:
                            # Setup config mocks
                            mock_config.GOOGLE_SEARCH_API_KEY = "test_key"
                            mock_config.GOOGLE_SEARCH_CSE_ID = "test_cse_id"
                            mock_config.TAVILY_API_KEY = "test_tavily_key"

                            # Mock provider factory to return empty list (no provider toolkits)
                            mock_provider.get_toolkits.return_value = []

                            # Call the method
                            methods = ToolkitService.get_toolkit_methods()

                            # Verify that the returned value is a dictionary
                            assert isinstance(methods, dict)

                            # Verify known toolkit types are present
                            assert ToolSet.PLUGIN in methods
                            assert ToolSet.RESEARCH in methods
                            assert ToolSet.FILE_SYSTEM in methods

                            # Verify each value is a callable
                            for method in methods.values():
                                assert callable(method)

    @patch("codemie.service.tools.toolkit_service.ProviderToolkitsFactory")
    def test_get_provider_toolkits_methods(self, mock_factory):
        """Test get_provider_toolkits_methods retrieves provider toolkits."""
        # Setup mock provider toolkit
        mock_provider_toolkit = Mock()
        mock_provider_toolkit.get_tools_ui_info.return_value = {"toolkit": "test_provider"}
        mock_provider_toolkit.get_toolkit.return_value.get_tools.return_value = []
        mock_factory.get_toolkits.return_value = [mock_provider_toolkit]

        # Call method
        result = ToolkitService.get_provider_toolkits_methods()

        # Assertions
        assert isinstance(result, dict)
        assert "test_provider" in result
        assert callable(result["test_provider"])

    def test_get_core_tools(self, mock_toolkit):
        """Test get_core_tools processes toolkits correctly."""
        # Setup mocks
        mock_tool = Mock(spec=BaseTool)
        mock_tool.name = "test_tool"

        with patch.object(ToolkitService, '_initialize_toolkit_tools', return_value=[mock_tool]):
            # Call method
            result = ToolkitService.get_core_tools(
                assistant_toolkits=[mock_toolkit],
                user_id="test-user-id",
                project_name="test-project",
                assistant_id="test-assistant-id",
                tools_config=None,
            )

        # Assertions
        assert len(result) == 1
        assert result[0].name == "test_tool"

    def test_initialize_toolkit_tools(self):
        """Test _initialize_toolkit_tools initializes tools from a toolkit."""
        # Setup mocks
        mock_tool = Mock(spec=BaseTool)
        mock_tool.name = "test_tool"

        tools = [Tool(name="test_tool", description="Test tool", label="Test Tool")]

        with patch.object(ToolkitService, '_initialize_tool', return_value=mock_tool):
            # Call method
            result = ToolkitService._initialize_toolkit_tools(
                assistant_toolkit="test_toolkit",
                assistant_tools=tools,
                user_id="test-user-id",
                project_name="test-project",
                assistant_id="test-assistant-id",
                tools_config=None,
            )

        # Assertions
        assert len(result) == 1
        assert result[0].name == "test_tool"

    @patch("codemie.service.tools.tool_metadata_service.ToolMetadataService")
    def test_initialize_tool_without_config(self, mock_metadata_service):
        """Test _initialize_tool when tool doesn't require configuration."""
        # Setup mocks
        mock_tool_class = Mock()
        mock_tool_instance = Mock(spec=BaseTool)
        mock_tool_class.return_value = mock_tool_instance

        mock_tool_definition = Mock()
        mock_tool_definition.name = "test_tool"
        mock_tool_definition.tool_class = mock_tool_class
        mock_tool_definition.settings_config = False

        mock_toolkit_definition = Mock()
        mock_toolkit_definition.settings_config = False

        # Mock ToolMetadataService
        mock_metadata_service._get_tool_and_toolkit_definitions.return_value = (
            mock_tool_definition,
            mock_toolkit_definition,
        )

        # Call method
        tool_meta = Tool(name="test_tool", description="Test tool", label="Test Tool")
        result = ToolkitService._initialize_tool(
            assistant_toolkit="test_toolkit",
            assistant_tool=tool_meta,
            user_id="test-user-id",
            project_name="test-project",
            assistant_id="test-assistant-id",
            tools_config=None,
        )

        # Assertions
        assert result == mock_tool_instance
        mock_tool_class.assert_called_once()

    @patch("codemie.service.settings.settings.SettingsService")
    @patch("codemie.service.tools.tool_metadata_service.toolkit_provider")
    def test_initialize_tool_with_config(self, mock_toolkit_provider, mock_settings_service):
        """Test _initialize_tool when tool requires configuration."""
        # Setup mocks
        mock_config_class = Mock()
        mock_config_instance = Mock()

        mock_tool_class = Mock()
        mock_tool_instance = Mock(spec=BaseTool)
        mock_tool_class.return_value = mock_tool_instance

        mock_tool_definition = Mock()
        mock_tool_definition.name = "test_tool"
        mock_tool_definition.tool_class = mock_tool_class
        mock_tool_definition.settings_config = True
        mock_tool_definition.config_class = mock_config_class

        mock_toolkit = Mock()
        mock_toolkit_definition = Mock()
        mock_toolkit_definition.settings_config = True
        mock_toolkit.get_definition.return_value = mock_toolkit_definition

        mock_toolkit_provider.get_tool.return_value = mock_tool_definition
        mock_toolkit_provider.get_toolkit.return_value = mock_toolkit
        mock_settings_service.get_config.return_value = mock_config_instance

        # Call method
        tool_meta = Tool(name="test_tool", description="Test tool", label="Test Tool")
        result = ToolkitService._initialize_tool(
            assistant_toolkit="test_toolkit",
            assistant_tool=tool_meta,
            user_id="test-user-id",
            project_name="test-project",
            assistant_id="test-assistant-id",
            tools_config=None,
        )

        # Assertions
        assert result == mock_tool_instance
        mock_settings_service.get_config.assert_called_once()

    @patch("codemie.service.tools.tool_metadata_service.toolkit_provider")
    def test_initialize_tool_no_config_found(self, mock_toolkit_provider):
        """Test _initialize_tool when tool requires config but none is found."""
        # Setup mocks
        mock_tool_definition = Mock()
        mock_tool_definition.name = "test_tool"
        mock_tool_definition.settings_config = True
        mock_tool_definition.tool_class = Mock(spec=BaseTool)

        mock_toolkit = Mock()
        mock_toolkit_definition = Mock()
        mock_toolkit_definition.settings_config = True
        mock_toolkit.get_definition.return_value = mock_toolkit_definition

        mock_toolkit_provider.get_tool.return_value = mock_tool_definition
        mock_toolkit_provider.get_toolkit.return_value = mock_toolkit

        with patch("codemie.service.settings.settings.SettingsService") as mock_settings:
            mock_settings.get_config.return_value = None

            # Call method
            tool_meta = Tool(name="test_tool", description="Test tool", label="Test Tool")
            result = ToolkitService._initialize_tool(
                assistant_toolkit="test_toolkit",
                assistant_tool=tool_meta,
                user_id="test-user-id",
                project_name="test-project",
                assistant_id="test-assistant-id",
                tools_config=None,
            )

            # Assertions
            assert result is None

    @patch("codemie.service.tools.tool_metadata_service.toolkit_provider")
    def test_initialize_tool_toolkit_not_found(self, mock_toolkit_provider):
        """Test _initialize_tool when toolkit is not found."""
        # Setup mocks
        mock_toolkit_provider.get_toolkit.return_value = None

        # Call method
        tool_meta = Tool(name="test_tool", description="Test tool", label="Test Tool")
        result = ToolkitService._initialize_tool(
            assistant_toolkit="unknown_toolkit",
            assistant_tool=tool_meta,
            user_id="test-user-id",
            project_name="test-project",
            assistant_id="test-assistant-id",
            tools_config=None,
        )

        # Assertions
        assert result is None

    def test_find_tool_config_by_name(self):
        """Test _find_tool_config_by_name finds the right config."""
        # Setup test data
        tool_config_1 = Mock(spec=ToolConfig)
        tool_config_1.name = "tool_1"
        tool_config_2 = Mock(spec=ToolConfig)
        tool_config_2.name = "tool_2"
        tools_config = [tool_config_1, tool_config_2]

        # Test finding existing config
        result = ToolkitService._find_tool_config_by_name(tools_config, "tool_1")
        assert result == tool_config_1

        # Test finding non-existing config
        result = ToolkitService._find_tool_config_by_name(tools_config, "tool_3")
        assert result is None

        # Test with None config
        result = ToolkitService._find_tool_config_by_name(None, "tool_1")
        assert result is None

    def test_filter_tools(self, mock_assistant):
        """Test filter_tools filters tools based on assistant configuration."""
        # Setup mock tools
        mock_tool_1 = Mock(spec=BaseTool)
        mock_tool_1.name = "enabled_tool"
        mock_tool_1.metadata = {}

        mock_tool_2 = Mock(spec=BaseTool)
        mock_tool_2.name = "disabled_tool"
        mock_tool_2.metadata = {}

        mock_tool_3 = Mock(spec=BaseTool)
        mock_tool_3.name = "_internal_tool"
        mock_tool_3.metadata = {}

        agent_tools = [mock_tool_1, mock_tool_2, mock_tool_3]

        # Setup assistant toolkits - using Mock to avoid validation
        mock_toolkit = Mock(spec=ToolKitDetails)
        mock_toolkit.toolkit = "test_toolkit"
        mock_tool = Mock(spec=Tool)
        mock_tool.name = "enabled_tool"
        mock_toolkit.tools = [mock_tool]
        assistant_toolkits = [mock_toolkit]

        # Test with internal tools included
        result = ToolkitService.filter_tools(
            assistant_toolkits=assistant_toolkits,
            toolkit_type="test_toolkit",
            agent_tools=agent_tools,
            include_internal_tools=True,
        )

        # Assertions
        assert len(result) == 2  # enabled_tool and _internal_tool
        assert mock_tool_1 in result
        assert mock_tool_3 in result
        assert mock_tool_2 not in result

        # Test with internal tools excluded
        result = ToolkitService.filter_tools(
            assistant_toolkits=assistant_toolkits,
            toolkit_type="test_toolkit",
            agent_tools=agent_tools,
            include_internal_tools=False,
        )

        # Assertions
        assert len(result) == 1  # only enabled_tool
        assert mock_tool_1 in result
        assert mock_tool_2 not in result
        assert mock_tool_3 not in result

    def test_filter_tools_with_plugin_tools(self, mock_assistant):
        """Test filter_tools filters plugin tools by configured names with cleanup."""
        # Setup plugin tools with suffixes and regular tool
        mock_plugin_1 = Mock(spec=BaseTool)
        mock_plugin_1.name = "_jira_tool_abc"
        mock_plugin_1.metadata = {'tool_type': ToolType.PLUGIN}

        mock_plugin_2 = Mock(spec=BaseTool)
        mock_plugin_2.name = "_slack_tool_xyz"
        mock_plugin_2.metadata = {'tool_type': ToolType.PLUGIN}

        mock_regular = Mock(spec=BaseTool)
        mock_regular.name = "regular_tool"
        mock_regular.metadata = {}

        agent_tools = [mock_plugin_1, mock_plugin_2, mock_regular]

        # Setup toolkit with specific plugin tool configured
        mock_toolkit = Mock(spec=ToolKitDetails)
        mock_toolkit.toolkit = ToolSet.PLUGIN
        mock_tool = Mock(spec=Tool)
        mock_tool.name = "_jira_tool"
        mock_toolkit.tools = [mock_tool]
        assistant_toolkits = [mock_toolkit]

        # Test
        result = ToolkitService.filter_tools(
            assistant_toolkits=assistant_toolkits,
            toolkit_type=ToolSet.PLUGIN,
            agent_tools=agent_tools,
            include_internal_tools=False,
        )

        # Assertions - only configured jira tool should be included
        assert len(result) == 1
        assert mock_plugin_1 in result
        assert mock_plugin_2 not in result
        assert mock_regular not in result

    def test_filter_tools_multiple_plugin_toolkits(self, mock_assistant):
        """Test filter_tools collects tools from all plugin toolkits."""
        # Setup plugin tools
        mock_jira = Mock(spec=BaseTool)
        mock_jira.name = "_jira_tool_abc"
        mock_jira.metadata = {'tool_type': ToolType.PLUGIN}

        mock_confluence = Mock(spec=BaseTool)
        mock_confluence.name = "_confluence_tool_xyz"
        mock_confluence.metadata = {'tool_type': ToolType.PLUGIN}

        mock_slack = Mock(spec=BaseTool)
        mock_slack.name = "_slack_tool_def"
        mock_slack.metadata = {'tool_type': ToolType.PLUGIN}

        # Setup multiple plugin toolkits with different tools
        toolkit_1 = Mock(spec=ToolKitDetails)
        toolkit_1.toolkit = ToolSet.PLUGIN
        tool_1 = Mock(spec=Tool)
        tool_1.name = "_jira_tool"
        toolkit_1.tools = [tool_1]

        toolkit_2 = Mock(spec=ToolKitDetails)
        toolkit_2.toolkit = ToolSet.PLUGIN
        tool_2 = Mock(spec=Tool)
        tool_2.name = "_confluence_tool"
        toolkit_2.tools = [tool_2]

        # Test
        result = ToolkitService.filter_tools(
            assistant_toolkits=[toolkit_1, toolkit_2],
            toolkit_type=ToolSet.PLUGIN,
            agent_tools=[mock_jira, mock_confluence, mock_slack],
            include_internal_tools=False,
        )

        # Should include tools from both toolkits, exclude unconfigured
        assert len(result) == 2
        assert mock_jira in result
        assert mock_confluence in result
        assert mock_slack not in result

    def test_determine_mcp_server_lifecycle_from_request(self, mock_request):
        """Test _determine_mcp_server_lifecycle gets value from request."""
        # Setup
        mock_request.mcp_server_single_usage = True

        # Call method
        result = ToolkitService._determine_mcp_server_lifecycle(mock_request)

        # Assertions
        assert result is True

    @patch.object(Conversation, 'get_by_id')
    def test_determine_mcp_server_lifecycle_from_conversation(self, mock_get_by_id, mock_request):
        """Test _determine_mcp_server_lifecycle gets value from conversation."""
        # Setup
        mock_request.mcp_server_single_usage = False
        mock_conversation = Mock(spec=Conversation)
        mock_conversation.mcp_server_single_usage = True
        mock_get_by_id.return_value = mock_conversation

        # Call method
        result = ToolkitService._determine_mcp_server_lifecycle(mock_request)

        # Assertions
        assert result is True

    @patch.object(Conversation, 'get_by_id')
    def test_determine_mcp_server_lifecycle_default(self, mock_get_by_id, mock_request):
        """Test _determine_mcp_server_lifecycle returns default value."""
        # Setup
        mock_request.mcp_server_single_usage = False
        mock_get_by_id.return_value = None

        # Call method
        result = ToolkitService._determine_mcp_server_lifecycle(mock_request)

        # Assertions
        assert result is False

    @patch.object(Conversation, 'get_by_id')
    def test_determine_mcp_server_lifecycle_exception(self, mock_get_by_id, mock_request):
        """Test _determine_mcp_server_lifecycle handles exceptions."""
        # Setup
        mock_request.mcp_server_single_usage = False
        mock_get_by_id.side_effect = Exception("Database error")

        # Call method - should not raise exception
        result = ToolkitService._determine_mcp_server_lifecycle(mock_request)

        # Assertions
        assert result is False

    @patch("codemie.service.tools.toolkit_service.ToolsPreprocessorFactory")
    def test_process_final_tools_traditional(self, mock_preprocessor_factory, mock_assistant, mock_tool):
        """Test _process_final_tools_traditional processes tools correctly."""
        # Setup mocks
        mock_tool_2 = Mock(spec=BaseTool)
        mock_tool_2.name = "test_tool_2"

        tools = [mock_tool, mock_tool_2, mock_tool]  # Duplicate tool

        mock_preprocessor = Mock()
        mock_preprocessor.process.side_effect = lambda x: x  # Return tools unchanged
        mock_preprocessor_factory.create_preprocessor_chain.return_value = [mock_preprocessor]

        # Call method
        result = ToolkitService._process_final_tools_traditional(
            tools=tools, llm_model="gpt-4", assistant=mock_assistant, request_uuid="test-uuid"
        )

        # Assertions
        assert len(result) == 2  # Duplicates removed
        assert mock_tool in result
        assert mock_tool_2 in result

    def test_add_tools_with_creds(self, mock_assistant, mock_user, mock_request):
        """Test add_tools_with_creds adds credential-based tools."""
        # Setup
        mock_tool = Mock(spec=BaseTool)
        mock_tool.name = "test_tool"

        mock_toolkit = Mock(spec=ToolKitDetails)
        mock_toolkit.toolkit = ToolSet.PLUGIN
        mock_toolkit_tool = Mock(spec=Tool)
        mock_toolkit_tool.name = "test_tool"
        mock_toolkit.tools = [mock_toolkit_tool]
        mock_assistant.toolkits = [mock_toolkit]

        # Mock the toolkit method to return tools
        with patch.object(ToolkitService, 'get_toolkit_methods') as mock_get_methods:
            mock_method = Mock(return_value=[mock_tool])
            mock_get_methods.return_value = {ToolSet.PLUGIN: mock_method}

            # Call method without filtering
            result = ToolkitService.add_tools_with_creds(
                assistant=mock_assistant,
                user=mock_user,
                llm_model="gpt-4",
                request_uuid="test-uuid",
                request=mock_request,
                skip_filtering=True,
            )

        # Assertions
        assert len(result) == 1
        assert result[0] == mock_tool

    def test_add_tools_with_creds_with_filtering(self, mock_assistant, mock_user, mock_request):
        """Test add_tools_with_creds with filtering enabled."""
        # Setup
        mock_tool = Mock(spec=BaseTool)
        mock_tool.name = "test_tool"

        mock_toolkit = Mock(spec=ToolKitDetails)
        mock_toolkit.toolkit = ToolSet.PLUGIN
        mock_toolkit_tool = Mock(spec=Tool)
        mock_toolkit_tool.name = "test_tool"
        mock_toolkit.tools = [mock_toolkit_tool]
        mock_assistant.toolkits = [mock_toolkit]

        # Mock the toolkit method and filter
        with patch.object(ToolkitService, 'get_toolkit_methods') as mock_get_methods:
            mock_method = Mock(return_value=[mock_tool])
            mock_get_methods.return_value = {ToolSet.PLUGIN: mock_method}

            with patch.object(ToolkitService, 'filter_tools', return_value=[mock_tool]):
                # Call method with filtering (default)
                result = ToolkitService.add_tools_with_creds(
                    assistant=mock_assistant,
                    user=mock_user,
                    llm_model="gpt-4",
                    request_uuid="test-uuid",
                    request=mock_request,
                    skip_filtering=False,
                )

        # Assertions
        assert len(result) == 1

    @patch("codemie.service.tools.toolkit_service.KBToolkit")
    def test_add_kb_tools(self, mock_kb_toolkit, mock_assistant):
        """Test _add_kb_tools adds knowledge base tools."""
        # Setup
        mock_tool = Mock(spec=BaseTool)
        mock_kb_index = Mock(spec=KnowledgeBaseIndexInfo)

        context = Context(context_type=ContextType.KNOWLEDGE_BASE, name="test-kb")
        mock_assistant.project = "test-project"

        mock_kb_toolkit.get_tools.return_value = [mock_tool]

        # Mock _find_index to return KB index
        with patch.object(ToolkitService, '_find_index', return_value=mock_kb_index):
            tools = []
            ToolkitService._add_kb_tools(tools, context, mock_assistant, "gpt-4")

        # Assertions
        assert len(tools) == 1
        assert tools[0] == mock_tool
        mock_kb_toolkit.get_tools.assert_called_once_with(kb_index=mock_kb_index, llm_model="gpt-4")

    @patch("codemie.service.tools.toolkit_service.KBToolkit")
    def test_add_kb_tools_no_index(self, mock_kb_toolkit, mock_assistant):
        """Test _add_kb_tools when KB index is not found."""
        # Setup
        context = Context(context_type=ContextType.KNOWLEDGE_BASE, name="test-kb")

        # Mock _find_index to return None
        with patch.object(ToolkitService, '_find_index', return_value=None):
            tools = []
            ToolkitService._add_kb_tools(tools, context, mock_assistant, "gpt-4")

        # Assertions
        assert len(tools) == 0
        mock_kb_toolkit.get_tools.assert_not_called()

    @patch("codemie.service.tools.toolkit_service.ProviderToolkitsFactory")
    def test_add_provider_context_tools(self, mock_provider_factory, mock_assistant, mock_user):
        """Test _add_provider_context_tools adds provider tools."""
        # Setup
        mock_tool_class = Mock()
        mock_tool_instance = Mock(spec=BaseTool)
        mock_tool_class.return_value = mock_tool_instance
        mock_tool_class.base_name = "test_tool"

        # Create mock toolkit instance with get_datasource_tools method
        mock_toolkit_instance = Mock()
        mock_toolkit_instance.get_datasource_tools.return_value = [mock_tool_class]

        # Create mock toolkit class (callable that returns instance)
        mock_toolkit_class = Mock()
        mock_toolkit_class.return_value = mock_toolkit_instance

        # Provider factory returns list of toolkit classes (not instances)
        mock_provider_factory.get_toolkits_for_provider.return_value = [mock_toolkit_class]

        mock_index = Mock(spec=ProviderIndexInfo)
        mock_provider_fields = Mock()
        mock_provider_fields.provider_id = "test-provider"
        mock_index.provider_fields = mock_provider_fields

        context = Context(context_type=ContextType.PROVIDER, name="test-provider-context")
        mock_toolkit = Mock(spec=ToolKitDetails)
        mock_toolkit.toolkit = "test"
        mock_toolkit_tool = Mock(spec=Tool)
        mock_toolkit_tool.name = "test_tool"
        mock_toolkit.tools = [mock_toolkit_tool]
        mock_assistant.toolkits = [mock_toolkit]

        # Mock _find_index
        with patch.object(ToolkitService, '_find_index', return_value=mock_index):
            tools = []
            ToolkitService._add_provider_context_tools(tools, mock_assistant, context, mock_user, "test-uuid")

        # Assertions
        assert len(tools) == 1
        assert tools[0] == mock_tool_instance

    @patch("codemie.service.tools.toolkit_service.CodeToolkit")
    def test_add_code_tools(self, mock_code_toolkit, mock_assistant, mock_request):
        """Test _add_code_tools adds code search and read tools."""
        # Setup
        mock_tool = Mock(spec=BaseTool)
        mock_code_toolkit.search_code_tool.return_value = mock_tool
        mock_code_toolkit.get_repo_tree_tool.return_value = mock_tool

        context = Context(context_type=ContextType.CODE, name="test-repo")
        mock_assistant.toolkits = []

        # Mock _get_code_fields
        mock_code_fields = CodeFields(app_name="test-project", repo_name="test-repo", index_type=CodeIndexType.CODE)
        with patch.object(ToolkitService, '_get_code_fields', return_value=mock_code_fields):
            tools = []
            ToolkitService._add_code_tools(tools, context, mock_assistant, mock_request, is_react=True)

        # Assertions
        assert len(tools) == 2  # repo_tree and code_search tools added by default

    @patch("codemie.service.tools.toolkit_service.ToolkitSettingService")
    def test_add_git_related_tools(self, mock_settings_service, mock_assistant, mock_user):
        """Test _add_git_related_tools adds Git tools."""
        # Setup
        mock_tool = Mock(spec=BaseTool)
        mock_settings_service.get_git_tools_with_creds.return_value = [mock_tool]

        context = Context(context_type=ContextType.CODE, name="test-repo")
        mock_toolkit = Mock(spec=ToolKitDetails)
        mock_toolkit.toolkit = ToolSet.GIT
        mock_toolkit.tools = []
        mock_assistant.toolkits = [mock_toolkit]

        # Mock dependencies
        mock_code_fields = CodeFields(app_name="test-project", repo_name="test-repo", index_type=CodeIndexType.CODE)
        with patch.object(ToolkitService, '_get_code_fields', return_value=mock_code_fields):
            with patch.object(ToolkitService, 'filter_tools', return_value=[mock_tool]):
                tools = []
                ToolkitService._add_git_related_tools(
                    tools=tools,
                    context=context,
                    assistant=mock_assistant,
                    user_id=mock_user.id,
                    request_uuid="test-uuid",
                    llm_model="gpt-4",
                    is_react=True,
                )

        # Assertions
        assert len(tools) == 1
        assert tools[0] == mock_tool

    def test_add_git_related_tools_non_code_context(self, mock_assistant, mock_user):
        """Test _add_git_related_tools ignores non-code contexts."""
        # Setup
        context = Context(context_type=ContextType.KNOWLEDGE_BASE, name="test-kb")

        tools = []
        ToolkitService._add_git_related_tools(
            tools=tools,
            context=context,
            assistant=mock_assistant,
            user_id=mock_user.id,
            request_uuid="test-uuid",
            llm_model="gpt-4",
            is_react=True,
        )

        # Assertions
        assert len(tools) == 0

    @patch.object(CodeIndexInfo, 'filter_by_project_and_repo')
    def test_find_code_index(self, mock_filter):
        """Test _find_code_index finds code index."""
        # Setup
        mock_code_index = Mock(spec=CodeIndexInfo)
        mock_filter.return_value = [mock_code_index]

        # Call method
        result = ToolkitService._find_code_index(project_name="test-project", repo_name="test-repo")

        # Assertions
        assert result == mock_code_index
        mock_filter.assert_called_once_with(project_name="test-project", repo_name="test-repo")

    @patch.object(CodeIndexInfo, 'filter_by_project_and_repo')
    def test_find_code_index_not_found(self, mock_filter):
        """Test _find_code_index when index is not found."""
        # Setup
        mock_filter.return_value = []

        # Call method
        result = ToolkitService._find_code_index(project_name="test-project", repo_name="test-repo")

        # Assertions
        assert result is None

    def test_get_code_fields(self, mock_assistant):
        """Test _get_code_fields returns CodeFields object."""
        # Setup
        context = Context(context_type=ContextType.CODE, name="test-repo")
        mock_code_index = Mock(spec=CodeIndexInfo)
        mock_code_index.index_type = "code"

        # Mock _find_code_index
        with patch.object(ToolkitService, '_find_code_index', return_value=mock_code_index):
            # Call method
            result = ToolkitService._get_code_fields(mock_assistant, context)

        # Assertions
        assert isinstance(result, CodeFields)
        assert result.app_name == mock_assistant.project
        assert result.repo_name == context.name
        assert result.index_type == CodeIndexType.CODE

    def test_get_code_fields_raises_exception(self, mock_assistant):
        """Test _get_code_fields raises ToolException when index not found."""
        # Setup
        context = Context(context_type=ContextType.CODE, name="test-repo")

        # Mock _find_code_index to return None
        with patch.object(ToolkitService, '_find_code_index', return_value=None):
            # Call method - should raise ToolException
            with pytest.raises(ToolException) as exc_info:
                ToolkitService._get_code_fields(mock_assistant, context)

            # Assertions
            assert "Repository: test-repo is not found" in str(exc_info.value)

    def test_find_index(self):
        """Test _find_index finds index by type."""
        # Setup
        mock_index = Mock(spec=KnowledgeBaseIndexInfo)

        # Mock the filter method on the class
        with patch.object(KnowledgeBaseIndexInfo, 'filter_by_project_and_repo', return_value=[mock_index]):
            # Call method
            result = ToolkitService._find_index(
                klass=KnowledgeBaseIndexInfo, project_name="test-project", repo_name="test-repo"
            )

        # Assertions
        assert result == mock_index

    def test_find_index_not_found(self):
        """Test _find_index when index is not found."""
        # Mock the filter method to return empty list
        with patch.object(KnowledgeBaseIndexInfo, 'filter_by_project_and_repo', return_value=[]):
            # Call method
            result = ToolkitService._find_index(
                klass=KnowledgeBaseIndexInfo, project_name="test-project", repo_name="test-repo"
            )

        # Assertions
        assert result is None

    @patch("codemie.service.tools.toolkit_service.IdeToolkit")
    @patch("codemie.service.settings.settings.SettingsService")
    def test_add_ide_tools(self, mock_settings_service, mock_ide_toolkit, mock_user):
        """Test add_ide_tools adds IDE integration tools."""
        # Setup
        mock_request = Mock(spec=IdeChatRequest)
        mock_request.ide_installation_id = "test-ide-id"
        mock_request.ide_request_id = "test-request-id"

        mock_tool_def = Mock()
        mock_tool_def.subject = "plugin-key.test_tool"
        mock_request.tools = [mock_tool_def]

        mock_settings = Mock()
        mock_settings.credential.return_value = "plugin-key"
        mock_settings_service.get_ide_settings.return_value = mock_settings
        mock_settings_service.PLUGIN_KEY = "plugin_key"

        mock_tool = Mock(spec=BaseTool)
        mock_ide_toolkit.return_value.get_tools.return_value = [mock_tool]

        # Call method
        result = ToolkitService.add_ide_tools(mock_request, mock_user)

        # Assertions
        assert len(result) == 1

    @patch("codemie.service.settings.settings.SettingsService")
    def test_add_ide_tools_invalid_settings(self, mock_settings_service, mock_user):
        """Test add_ide_tools raises ToolException when settings are invalid."""
        # Setup
        mock_request = Mock(spec=IdeChatRequest)
        mock_request.ide_installation_id = "test-ide-id"
        mock_settings_service.get_ide_settings.return_value = None

        # Call method - should raise ToolException
        with pytest.raises(ToolException) as exc_info:
            ToolkitService.add_ide_tools(mock_request, mock_user)

        # Assertions
        assert "Invalid IDE request" in str(exc_info.value)

    @patch("codemie.service.tools.toolkit_service.FileAnalysisToolkit")
    @patch("codemie.service.tools.toolkit_service.llm_service")
    @patch("codemie.service.tools.toolkit_service.get_llm_by_credentials")
    def test_add_file_tools(self, mock_get_llm, mock_llm_service, mock_file_toolkit, mock_assistant):
        """Test add_file_tools adds file analysis tools."""
        # Setup
        mock_file_object = Mock(spec=FileObject)
        mock_file_object.is_image.return_value = False

        mock_llm = Mock()
        mock_get_llm.return_value = mock_llm

        mock_llm_service.get_llm_deployment_name.return_value = "gpt-4"
        mock_llm_service.get_multimodal_llms.return_value = ["gpt-4-vision"]

        mock_tool = Mock(spec=BaseTool)
        mock_file_toolkit.get_toolkit.return_value.get_tools.return_value = [mock_tool]

        # Call method with already constructed FileObject - no need to mock FileService.get_file_object
        # since we're passing the file_objects directly
        result = ToolkitService.add_file_tools(mock_assistant, [mock_file_object], "test-uuid")

        # Assertions
        assert len(result) == 1

    @patch("codemie.service.tools.toolkit_service.VisionToolkit")
    @patch("codemie.service.tools.toolkit_service.llm_service")
    @patch("codemie.service.tools.toolkit_service.get_llm_by_credentials")
    def test_add_file_tools_with_images(self, mock_get_llm, mock_llm_service, mock_vision_toolkit, mock_assistant):
        """Test add_file_tools processes image files."""
        # Setup
        mock_file_object = Mock(spec=FileObject)
        mock_file_object.is_image.return_value = True

        mock_llm = Mock()
        mock_get_llm.return_value = mock_llm

        mock_llm_service.get_llm_deployment_name.return_value = "gpt-4"
        mock_llm_service.get_multimodal_llms.return_value = ["gpt-4-vision"]  # Has multimodal but using non-multimodal

        mock_tool = Mock(spec=BaseTool)
        mock_vision_toolkit.get_toolkit.return_value.get_tools.return_value = [mock_tool]

        # Mock FileService - need to patch where it's actually used, not imported
        with patch(
            "codemie.service.file_service.file_service.FileService.get_file_object", return_value=mock_file_object
        ):
            # Call method
            result = ToolkitService.add_file_tools(mock_assistant, [mock_file_object], "test-uuid")

        # Assertions
        assert len(result) == 1
        mock_vision_toolkit.get_toolkit.assert_called_once()

    @patch("codemie.service.tools.toolkit_service.llm_service")
    @patch("codemie.service.tools.toolkit_service.get_llm_by_credentials")
    def test_initialize_llm_for_files_multimodal(self, mock_get_llm, mock_llm_service, mock_assistant):
        """Test _initialize_llm_for_files with multimodal LLM."""
        # Setup
        mock_llm = Mock()
        mock_get_llm.return_value = mock_llm

        mock_llm_service.get_llm_deployment_name.return_value = "gpt-4-vision"
        mock_llm_service.get_multimodal_llms.return_value = ["gpt-4-vision"]

        # Call method
        llm, is_multimodal = ToolkitService._initialize_llm_for_files(mock_assistant, "test-uuid")

        # Assertions
        assert llm == mock_llm
        assert is_multimodal is True

    @patch("codemie.service.tools.toolkit_service.llm_service")
    @patch("codemie.service.tools.toolkit_service.get_llm_by_credentials")
    def test_initialize_llm_for_files_non_multimodal(self, mock_get_llm, mock_llm_service, mock_assistant):
        """Test _initialize_llm_for_files with non-multimodal LLM."""
        # Setup
        mock_llm = Mock()
        mock_get_llm.return_value = mock_llm

        mock_llm_service.get_llm_deployment_name.return_value = "gpt-4"
        mock_llm_service.get_multimodal_llms.return_value = ["gpt-4-vision"]

        # Call method
        llm, is_multimodal = ToolkitService._initialize_llm_for_files(mock_assistant, "test-uuid")

        # Assertions
        assert llm == mock_llm
        assert is_multimodal is False

    def test_get_non_image_files(self):
        """Test _get_non_image_files extracts non-image files."""
        # Setup
        mock_file_1 = Mock(spec=FileObject)
        mock_file_1.is_image.return_value = False

        mock_file_2 = Mock(spec=FileObject)
        mock_file_2.is_image.return_value = True

        mock_file_3 = Mock(spec=FileObject)
        mock_file_3.is_image.return_value = False

        # Call method
        result = ToolkitService._get_non_image_files([mock_file_1, mock_file_2, mock_file_3])

        # Assertions
        assert len(result) == 2

    @patch("codemie.service.tools.toolkit_service.VisionToolkit")
    def test_process_image_files(self, mock_vision_toolkit):
        """Test _process_image_files processes image files."""
        # Setup
        mock_file_1 = Mock(spec=FileObject)
        mock_file_1.is_image.return_value = True

        mock_file_2 = Mock(spec=FileObject)
        mock_file_2.is_image.return_value = False

        mock_llm = Mock()
        mock_tool = Mock(spec=BaseTool)
        mock_vision_toolkit.get_toolkit.return_value.get_tools.return_value = [mock_tool]

        # Call method
        result = ToolkitService._process_image_files([mock_file_1, mock_file_2], mock_llm)

        # Assertions
        assert len(result) == 1
        mock_vision_toolkit.get_toolkit.assert_called_once()

    @patch("codemie.service.tools.toolkit_service.config")
    @patch("codemie.service.tools.toolkit_service.ToolkitLookupService")
    def test_get_tools_with_smart_lookup(
        self, mock_lookup_service, mock_config, mock_assistant, mock_request, mock_user
    ):
        """Test get_tools with smart lookup enabled."""
        # Setup
        mock_config.TOOLS_SMART_LOOKUP_ENABLED = True
        mock_assistant.toolkits = []  # No configured tools
        mock_assistant.skill_ids = []  # No attached skills

        mock_toolkit = ToolKit(toolkit="test_toolkit", tools=[Tool(name="test_tool", description="Test", label="Test")])

        mock_lookup_service.build_search_query_with_history.return_value = "test query"
        mock_lookup_service.get_tools_by_query.return_value = [mock_toolkit]

        # Mock dependencies
        with patch.object(ToolkitService, 'get_core_tools', return_value=[]):
            with patch.object(ToolkitService, 'add_context_tools', return_value=[]):
                with patch.object(ToolkitService, '_get_tools', return_value=[]):
                    # Call method
                    ToolkitService.get_tools(
                        assistant=mock_assistant,
                        request=mock_request,
                        user=mock_user,
                        llm_model="gpt-4",
                        request_uuid="test-uuid",
                        smart_tool_selection_enabled=True,
                    )

        # Assertions
        mock_lookup_service.build_search_query_with_history.assert_called_once_with(mock_request)
        mock_lookup_service.get_tools_by_query.assert_called_once_with(query="test query")


class TestGetPluginToolsDelegate:
    """Tests for _get_plugin_tools_delegate method."""

    @pytest.fixture
    def sample_toolkits(self):
        """Create sample toolkits for testing."""
        return [
            ToolKit(toolkit=ToolSet.PLUGIN, tools=[Tool(name="plugin_tool", description="Plugin tool")]),
        ]

    @pytest.fixture
    def mock_assistant(self):
        """Create a mock assistant."""
        assistant = Mock(spec=Assistant)
        assistant.id = "test-assistant-id"
        assistant.project = "test-project"
        assistant.toolkits = []
        return assistant

    @pytest.fixture
    def mock_user(self):
        """Create a mock user."""
        user = Mock(spec=User)
        user.id = "test-user"
        return user

    @pytest.fixture
    def mock_request(self):
        """Create a mock request."""
        request = Mock(spec=AssistantChatRequest)
        request.tools_config = None
        request.enable_web_search = None
        request.enable_code_interpreter = None
        return request

    @patch('codemie.service.tools.plugin_tools_delegate.is_plugin_enabled')
    @patch('codemie.service.tools.plugin_tools_delegate.get_plugin_tools_for_assistant')
    def test_get_plugin_tools_delegate_success(
        self, mock_get_plugin_tools, mock_is_enabled, mock_assistant, mock_user, mock_request, sample_toolkits
    ):
        """Test getting plugin tools successfully."""
        # Arrange
        mock_is_enabled.return_value = True
        mock_assistant.toolkits = sample_toolkits

        mock_tool = Mock()
        mock_tool.name = "plugin_tool_xyz"
        mock_get_plugin_tools.return_value = [mock_tool]

        # Act
        tools = ToolkitService._get_plugin_tools_delegate(
            assistant=mock_assistant,
            user=mock_user,
            request=mock_request,
        )

        # Assert
        assert len(tools) == 1
        mock_get_plugin_tools.assert_called_once_with(
            user_id="test-user",
            project_name="test-project",
            assistant_id="test-assistant-id",
            tool_config=None,
        )

    @patch('codemie.service.tools.plugin_tools_delegate.is_plugin_enabled')
    def test_get_plugin_tools_delegate_not_enabled(self, mock_is_enabled, mock_assistant, mock_user, mock_request):
        """Test getting plugin tools when plugin system is not enabled."""
        # Arrange
        mock_is_enabled.return_value = False

        # Act & Assert
        with pytest.raises(RuntimeError, match="Enterprise plugin system is not available or enabled"):
            ToolkitService._get_plugin_tools_delegate(
                assistant=mock_assistant,
                user=mock_user,
                request=mock_request,
            )

    @patch('codemie.enterprise.plugin.dependencies.PLUGIN_TOOL')
    @patch('codemie.service.tools.plugin_tools_delegate.is_plugin_enabled')
    @patch('codemie.service.tools.plugin_tools_delegate.get_plugin_tools_for_assistant')
    def test_get_plugin_tools_delegate_with_plugin_tool_returns_all(
        self, mock_get_plugin_tools, mock_is_enabled, mock_plugin_tool, mock_assistant, mock_user, mock_request
    ):
        """Test getting plugin tools when 'Plugin' tool exists - should return all tools."""
        # Arrange
        mock_is_enabled.return_value = True
        mock_plugin_tool.name = "Plugin"
        toolkits_with_plugin = [
            ToolKit(toolkit=ToolSet.PLUGIN, tools=[Tool(name="Plugin", description="Plugin tool")]),
        ]
        mock_assistant.toolkits = toolkits_with_plugin

        mock_tool1 = Mock()
        mock_tool1.name = "_jira_tool_abc"
        mock_tool2 = Mock()
        mock_tool2.name = "_confluence_tool_xyz"

        mock_get_plugin_tools.return_value = [mock_tool1, mock_tool2]

        # Act
        tools = ToolkitService._get_plugin_tools_delegate(
            assistant=mock_assistant,
            user=mock_user,
            request=mock_request,
        )

        # Assert
        assert len(tools) == 2
        assert mock_tool1 in tools
        assert mock_tool2 in tools

    @patch('codemie.service.tools.plugin_tools_delegate.is_plugin_enabled')
    @patch('codemie.service.tools.plugin_tools_delegate.get_plugin_tools_for_assistant')
    def test_get_plugin_tools_delegate_filters_by_configured_tools(
        self, mock_get_plugin_tools, mock_is_enabled, mock_assistant, mock_user, mock_request
    ):
        """Test plugin tools filtering when specific plugin tools are configured."""
        # Arrange
        mock_is_enabled.return_value = True
        toolkits_with_specific_tools = [
            ToolKit(
                toolkit=ToolSet.PLUGIN,
                tools=[
                    Tool(name="_jira_tool", description="Jira tool"),
                    Tool(name="_confluence_tool", description="Confluence tool"),
                ],
            ),
        ]
        mock_assistant.toolkits = toolkits_with_specific_tools

        mock_tool1 = Mock()
        mock_tool1.name = "_jira_tool_abc"
        mock_tool2 = Mock()
        mock_tool2.name = "_confluence_tool_xyz"
        mock_tool3 = Mock()
        mock_tool3.name = "_slack_tool_def"

        mock_get_plugin_tools.return_value = [mock_tool1, mock_tool2, mock_tool3]

        # Act
        tools = ToolkitService._get_plugin_tools_delegate(
            assistant=mock_assistant,
            user=mock_user,
            request=mock_request,
        )

        # Assert
        # When specific plugin tools are configured (not 'Plugin'), filter to matching tools
        assert len(tools) == 2  # Only _jira_tool and _confluence_tool match
        assert mock_tool1 in tools
        assert mock_tool2 in tools
        assert mock_tool3 not in tools

    def test_filter_plugin_tools_no_plugin_toolkit(self):
        """Test filter returns empty list when no Plugin toolkit configured."""
        from codemie.service.tools.plugin_tools_delegate import PluginToolsDelegate

        # Arrange
        toolkits = [
            ToolKit(toolkit=ToolSet.GIT, tools=[Tool(name="git_tool", description="Git tool")]),
        ]
        mock_tool = Mock()
        mock_tool.name = "_jira_tool_abc"

        # Act
        result = PluginToolsDelegate._filter_plugin_tools_by_assistant_tools(
            plugin_tools=[mock_tool], assistant_toolkits=toolkits
        )

        # Assert
        assert result == []

    def test_filter_plugin_tools_empty_plugin_toolkit(self):
        """Test filter returns empty list when Plugin toolkit has no tools."""
        from codemie.service.tools.plugin_tools_delegate import PluginToolsDelegate

        # Arrange
        toolkits = [
            ToolKit(toolkit=ToolSet.PLUGIN, tools=[]),
        ]
        mock_tool = Mock()
        mock_tool.name = "_jira_tool_abc"

        # Act
        result = PluginToolsDelegate._filter_plugin_tools_by_assistant_tools(
            plugin_tools=[mock_tool], assistant_toolkits=toolkits
        )

        # Assert
        assert result == []

    @patch('codemie.enterprise.plugin.dependencies.PLUGIN_TOOL')
    def test_has_all_plugin_tools_enabled_true(self, mock_plugin_tool):
        """Test checking for 'Plugin' tool when it exists."""
        from codemie.service.tools.plugin_tools_delegate import PluginToolsDelegate

        # Arrange
        mock_plugin_tool.name = "Plugin"
        toolkits = [
            ToolKit(toolkit=ToolSet.PLUGIN, tools=[Tool(name="Plugin", description="Plugin tool")]),
        ]

        # Act
        result = PluginToolsDelegate.has_all_plugin_tools_enabled(toolkits)

        # Assert
        assert result is True

    @patch('codemie.enterprise.plugin.dependencies.PLUGIN_TOOL')
    def test_has_all_plugin_tools_enabled_false_no_plugin_tool(self, mock_plugin_tool):
        """Test checking for 'Plugin' tool when it doesn't exist."""
        from codemie.service.tools.plugin_tools_delegate import PluginToolsDelegate

        # Arrange
        mock_plugin_tool.name = "Plugin"
        toolkits = [
            ToolKit(toolkit=ToolSet.PLUGIN, tools=[Tool(name="some_other_tool", description="Other tool")]),
        ]

        # Act
        result = PluginToolsDelegate.has_all_plugin_tools_enabled(toolkits)

        # Assert
        assert result is False

    @patch('codemie.enterprise.plugin.dependencies.PLUGIN_TOOL')
    def test_has_all_plugin_tools_enabled_false_no_plugin_toolkit(self, mock_plugin_tool):
        """Test checking for 'Plugin' tool when PLUGIN toolkit doesn't exist."""
        from codemie.service.tools.plugin_tools_delegate import PluginToolsDelegate

        # Arrange
        mock_plugin_tool.name = "Plugin"
        toolkits = [
            ToolKit(toolkit=ToolSet.GIT, tools=[Tool(name="git_tool", description="Git tool")]),
        ]

        # Act
        result = PluginToolsDelegate.has_all_plugin_tools_enabled(toolkits)

        # Assert
        assert result is False


# =============================================================================
# Merge Skill Toolkits Tests
# =============================================================================


class TestMergeSkillToolkits:
    """Tests for ToolkitService._merge_skill_toolkits"""

    def _make_toolkit(self, name: str):
        """Helper: create a Mock toolkit with the given toolkit name."""
        tk = Mock()
        tk.toolkit = name
        return tk

    def _make_assistant(self, skill_ids=None, toolkits=None):
        """Helper: create a Mock assistant with all attributes required by get_tools."""
        assistant = Mock(spec=Assistant)
        assistant.id = "test-assistant-id"
        assistant.name = "Test Assistant"
        assistant.project = "test-project"
        assistant.skill_ids = skill_ids if skill_ids is not None else []
        assistant.toolkits = toolkits if toolkits is not None else []
        assistant.context = []
        assistant.assistant_ids = []
        assistant.mcp_servers = []
        assistant.llm_model_type = "gpt-4"
        assistant.created_by = None
        return assistant

    def _make_skill(self, name: str, toolkits=None):
        """Helper: create a Mock skill."""
        skill = Mock()
        skill.name = name
        skill.toolkits = toolkits if toolkits is not None else []
        return skill

    # ------------------------------------------------------------------
    # No skill_ids – returns assistant toolkits unchanged
    # ------------------------------------------------------------------

    def test_no_skill_ids_returns_assistant_toolkits(self):
        """When no skill_ids, returns copy of assistant.toolkits"""
        tk = self._make_toolkit("git")
        assistant = self._make_assistant(skill_ids=[], toolkits=[tk])

        result = ToolkitService._merge_skill_toolkits(assistant)

        assert result == [tk]

    def test_no_skill_ids_empty_toolkits_returns_empty(self):
        """When no skill_ids and no toolkits, returns empty list"""
        assistant = self._make_assistant(skill_ids=[], toolkits=[])

        result = ToolkitService._merge_skill_toolkits(assistant)

        assert result == []

    def test_no_skill_ids_none_toolkits_returns_empty(self):
        """When no skill_ids and toolkits is None, returns empty list"""
        assistant = self._make_assistant(skill_ids=[], toolkits=None)

        result = ToolkitService._merge_skill_toolkits(assistant)

        assert result == []

    # ------------------------------------------------------------------
    # skill_ids present – merging logic
    # ------------------------------------------------------------------

    def test_merges_new_toolkit_from_skill(self):
        """Skill toolkit not present on assistant is appended"""
        tk_git = self._make_toolkit("git")
        tk_code = self._make_toolkit("code")
        assistant = self._make_assistant(skill_ids=["skill-1"], toolkits=[tk_git])
        skill = self._make_skill("skill-1", toolkits=[tk_code])

        with patch("codemie.service.tools.toolkit_service.SkillRepository") as mock_repo:
            mock_repo.get_by_ids.return_value = [skill]
            result = ToolkitService._merge_skill_toolkits(assistant)

        assert len(result) == 2
        assert tk_git in result
        assert tk_code in result

    def test_deduplicates_existing_toolkit_by_name(self):
        """Skill toolkit with same name as assistant toolkit is not duplicated"""
        tk_git = self._make_toolkit("git")
        tk_git_dup = self._make_toolkit("git")  # Same name, different object
        assistant = self._make_assistant(skill_ids=["skill-1"], toolkits=[tk_git])
        skill = self._make_skill("skill-1", toolkits=[tk_git_dup])

        with patch("codemie.service.tools.toolkit_service.SkillRepository") as mock_repo:
            mock_repo.get_by_ids.return_value = [skill]
            result = ToolkitService._merge_skill_toolkits(assistant)

        assert len(result) == 1
        assert result[0].toolkit == "git"

    def test_merges_toolkits_from_multiple_skills(self):
        """Each skill contributes its unique toolkits"""
        tk_git = self._make_toolkit("git")
        tk_code = self._make_toolkit("code")
        tk_research = self._make_toolkit("research")
        assistant = self._make_assistant(skill_ids=["skill-1", "skill-2"], toolkits=[tk_git])
        skill_1 = self._make_skill("skill-1", toolkits=[tk_code])
        skill_2 = self._make_skill("skill-2", toolkits=[tk_research])

        with patch("codemie.service.tools.toolkit_service.SkillRepository") as mock_repo:
            mock_repo.get_by_ids.return_value = [skill_1, skill_2]
            result = ToolkitService._merge_skill_toolkits(assistant)

        assert len(result) == 3
        names = {tk.toolkit for tk in result}
        assert names == {"git", "code", "research"}

    def test_skill_with_empty_toolkits_does_not_change_result(self):
        """Skill with empty toolkits list does not modify merged output"""
        tk_git = self._make_toolkit("git")
        assistant = self._make_assistant(skill_ids=["skill-1"], toolkits=[tk_git])
        skill = self._make_skill("skill-1", toolkits=[])

        with patch("codemie.service.tools.toolkit_service.SkillRepository") as mock_repo:
            mock_repo.get_by_ids.return_value = [skill]
            result = ToolkitService._merge_skill_toolkits(assistant)

        assert result == [tk_git]

    def test_skill_with_none_toolkits_does_not_change_result(self):
        """Skill with None toolkits does not affect merged output"""
        tk_git = self._make_toolkit("git")
        assistant = self._make_assistant(skill_ids=["skill-1"], toolkits=[tk_git])
        skill = self._make_skill("skill-1", toolkits=None)

        with patch("codemie.service.tools.toolkit_service.SkillRepository") as mock_repo:
            mock_repo.get_by_ids.return_value = [skill]
            result = ToolkitService._merge_skill_toolkits(assistant)

        assert result == [tk_git]

    def test_no_skills_found_returns_assistant_toolkits(self):
        """When SkillRepository returns empty, only assistant toolkits are returned"""
        tk_git = self._make_toolkit("git")
        assistant = self._make_assistant(skill_ids=["nonexistent"], toolkits=[tk_git])

        with patch("codemie.service.tools.toolkit_service.SkillRepository") as mock_repo:
            mock_repo.get_by_ids.return_value = []
            result = ToolkitService._merge_skill_toolkits(assistant)

        assert result == [tk_git]

    def test_cross_skill_deduplication(self):
        """Two skills sharing a toolkit name result in only one entry"""
        tk_shared_1 = self._make_toolkit("shared-toolkit")
        tk_shared_2 = self._make_toolkit("shared-toolkit")
        assistant = self._make_assistant(skill_ids=["skill-1", "skill-2"], toolkits=[])
        skill_1 = self._make_skill("skill-1", toolkits=[tk_shared_1])
        skill_2 = self._make_skill("skill-2", toolkits=[tk_shared_2])

        with patch("codemie.service.tools.toolkit_service.SkillRepository") as mock_repo:
            mock_repo.get_by_ids.return_value = [skill_1, skill_2]
            result = ToolkitService._merge_skill_toolkits(assistant)

        assert len(result) == 1
        assert result[0].toolkit == "shared-toolkit"

    def test_get_tools_calls_merge_skill_toolkits(self):
        """get_tools delegates toolkit selection to _merge_skill_toolkits"""
        assistant = self._make_assistant(skill_ids=["skill-1"], toolkits=[])
        mock_merged_toolkit = self._make_toolkit("git")

        mock_request = Mock(spec=AssistantChatRequest)
        mock_request.text = "test"
        mock_request.history = []
        mock_request.tools_config = []
        mock_request.conversation_id = "test-conv"
        mock_request.mcp_server_single_usage = False
        mock_request.enable_web_search = None
        mock_request.enable_code_interpreter = None
        mock_user = Mock(spec=User)
        mock_user.id = "test-user-id"
        mock_user.is_admin = False
        mock_user.roles = []

        with patch.object(ToolkitService, "_merge_skill_toolkits", return_value=[mock_merged_toolkit]) as mock_merge:
            with patch.object(ToolkitService, "get_core_tools", return_value=[]):
                with patch.object(ToolkitService, "add_context_tools", return_value=[]):
                    with patch.object(ToolkitService, "_get_tools", return_value=[]):
                        ToolkitService.get_tools(
                            assistant=assistant,
                            request=mock_request,
                            user=mock_user,
                            llm_model="gpt-4",
                            request_uuid="test-uuid",
                            smart_tool_selection_enabled=False,
                        )

        mock_merge.assert_called_once_with(assistant)

    def test_get_tools_smart_selection_skipped_when_skills_provide_toolkits(self):
        """Smart tool lookup is skipped when skill-merged toolkits are non-empty"""
        assistant = self._make_assistant(skill_ids=["skill-1"], toolkits=[])
        merged_toolkit = self._make_toolkit("git")

        mock_request = Mock(spec=AssistantChatRequest)
        mock_request.text = "test"
        mock_request.history = []
        mock_request.tools_config = []
        mock_request.conversation_id = "test-conv"
        mock_request.mcp_server_single_usage = False
        mock_request.enable_web_search = None
        mock_request.enable_code_interpreter = None
        mock_user = Mock(spec=User)
        mock_user.id = "test-user-id"
        mock_user.is_admin = False
        mock_user.roles = []

        with patch.object(ToolkitService, "_merge_skill_toolkits", return_value=[merged_toolkit]):
            with patch.object(ToolkitService, "get_core_tools", return_value=[]):
                with patch.object(ToolkitService, "add_context_tools", return_value=[]):
                    with patch.object(ToolkitService, "_get_tools", return_value=[]):
                        with patch("codemie.service.tools.toolkit_service.ToolkitLookupService") as mock_lookup:
                            ToolkitService.get_tools(
                                assistant=assistant,
                                request=mock_request,
                                user=mock_user,
                                llm_model="gpt-4",
                                request_uuid="test-uuid",
                                smart_tool_selection_enabled=True,
                            )

        # Smart lookup should NOT be called because selected_toolkits is non-empty
        mock_lookup.get_tools_by_query.assert_not_called()


# =============================================================================
# Merge Skill MCP Servers Tests
# =============================================================================


class TestMergeSkillMcpServers:
    """Tests for ToolkitService._merge_skill_mcp_servers"""

    def _make_mcp_server(self, name: str):
        """Helper: create a Mock MCP server with the given name."""
        server = Mock()
        server.name = name
        return server

    def _make_assistant(self, skill_ids=None, mcp_servers=None):
        """Helper: create a Mock assistant."""
        assistant = Mock(spec=Assistant)
        assistant.id = "test-assistant-id"
        assistant.name = "Test Assistant"
        assistant.project = "test-project"
        assistant.skill_ids = skill_ids if skill_ids is not None else []
        assistant.mcp_servers = mcp_servers if mcp_servers is not None else []
        return assistant

    def _make_skill(self, name: str, mcp_servers=None):
        """Helper: create a Mock skill."""
        skill = Mock()
        skill.name = name
        skill.mcp_servers = mcp_servers if mcp_servers is not None else []
        return skill

    # ------------------------------------------------------------------
    # No skill_ids – returns assistant mcp_servers unchanged
    # ------------------------------------------------------------------

    def test_no_skill_ids_returns_assistant_mcp_servers(self):
        """When no skill_ids, returns copy of assistant.mcp_servers"""
        server = self._make_mcp_server("my-server")
        assistant = self._make_assistant(skill_ids=[], mcp_servers=[server])

        result = ToolkitService._merge_skill_mcp_servers(assistant)

        assert result == [server]

    def test_no_skill_ids_empty_mcp_servers_returns_empty(self):
        """When no skill_ids and no mcp_servers, returns empty list"""
        assistant = self._make_assistant(skill_ids=[], mcp_servers=[])

        result = ToolkitService._merge_skill_mcp_servers(assistant)

        assert result == []

    def test_no_skill_ids_none_mcp_servers_returns_empty(self):
        """When no skill_ids and mcp_servers is None, returns empty list"""
        assistant = self._make_assistant(skill_ids=[], mcp_servers=None)

        result = ToolkitService._merge_skill_mcp_servers(assistant)

        assert result == []

    # ------------------------------------------------------------------
    # skill_ids present – merging logic
    # ------------------------------------------------------------------

    def test_merges_new_mcp_server_from_skill(self):
        """Skill MCP server not present on assistant is appended"""
        server_a = self._make_mcp_server("server-a")
        server_b = self._make_mcp_server("server-b")
        assistant = self._make_assistant(skill_ids=["skill-1"], mcp_servers=[server_a])
        skill = self._make_skill("skill-1", mcp_servers=[server_b])

        with patch("codemie.service.tools.toolkit_service.SkillRepository") as mock_repo:
            mock_repo.get_by_ids.return_value = [skill]
            result = ToolkitService._merge_skill_mcp_servers(assistant)

        assert len(result) == 2
        assert server_a in result
        assert server_b in result

    def test_deduplicates_existing_mcp_server_by_name(self):
        """Skill MCP server with same name as assistant server is not duplicated"""
        server_a = self._make_mcp_server("server-a")
        server_a_dup = self._make_mcp_server("server-a")
        assistant = self._make_assistant(skill_ids=["skill-1"], mcp_servers=[server_a])
        skill = self._make_skill("skill-1", mcp_servers=[server_a_dup])

        with patch("codemie.service.tools.toolkit_service.SkillRepository") as mock_repo:
            mock_repo.get_by_ids.return_value = [skill]
            result = ToolkitService._merge_skill_mcp_servers(assistant)

        assert len(result) == 1
        assert result[0].name == "server-a"

    def test_merges_mcp_servers_from_multiple_skills(self):
        """Each skill contributes its unique MCP servers"""
        server_a = self._make_mcp_server("server-a")
        server_b = self._make_mcp_server("server-b")
        server_c = self._make_mcp_server("server-c")
        assistant = self._make_assistant(skill_ids=["skill-1", "skill-2"], mcp_servers=[server_a])
        skill_1 = self._make_skill("skill-1", mcp_servers=[server_b])
        skill_2 = self._make_skill("skill-2", mcp_servers=[server_c])

        with patch("codemie.service.tools.toolkit_service.SkillRepository") as mock_repo:
            mock_repo.get_by_ids.return_value = [skill_1, skill_2]
            result = ToolkitService._merge_skill_mcp_servers(assistant)

        assert len(result) == 3
        names = {s.name for s in result}
        assert names == {"server-a", "server-b", "server-c"}

    def test_skill_with_empty_mcp_servers_does_not_change_result(self):
        """Skill with empty mcp_servers list does not modify merged output"""
        server_a = self._make_mcp_server("server-a")
        assistant = self._make_assistant(skill_ids=["skill-1"], mcp_servers=[server_a])
        skill = self._make_skill("skill-1", mcp_servers=[])

        with patch("codemie.service.tools.toolkit_service.SkillRepository") as mock_repo:
            mock_repo.get_by_ids.return_value = [skill]
            result = ToolkitService._merge_skill_mcp_servers(assistant)

        assert result == [server_a]

    def test_skill_with_none_mcp_servers_does_not_change_result(self):
        """Skill with None mcp_servers does not affect merged output"""
        server_a = self._make_mcp_server("server-a")
        assistant = self._make_assistant(skill_ids=["skill-1"], mcp_servers=[server_a])
        skill = self._make_skill("skill-1", mcp_servers=None)

        with patch("codemie.service.tools.toolkit_service.SkillRepository") as mock_repo:
            mock_repo.get_by_ids.return_value = [skill]
            result = ToolkitService._merge_skill_mcp_servers(assistant)

        assert result == [server_a]

    def test_no_skills_found_returns_assistant_mcp_servers(self):
        """When SkillRepository returns empty, only assistant mcp_servers are returned"""
        server_a = self._make_mcp_server("server-a")
        assistant = self._make_assistant(skill_ids=["nonexistent"], mcp_servers=[server_a])

        with patch("codemie.service.tools.toolkit_service.SkillRepository") as mock_repo:
            mock_repo.get_by_ids.return_value = []
            result = ToolkitService._merge_skill_mcp_servers(assistant)

        assert result == [server_a]

    def test_cross_skill_deduplication(self):
        """Two skills sharing an MCP server name result in only one entry"""
        server_shared_1 = self._make_mcp_server("shared-server")
        server_shared_2 = self._make_mcp_server("shared-server")
        assistant = self._make_assistant(skill_ids=["skill-1", "skill-2"], mcp_servers=[])
        skill_1 = self._make_skill("skill-1", mcp_servers=[server_shared_1])
        skill_2 = self._make_skill("skill-2", mcp_servers=[server_shared_2])

        with patch("codemie.service.tools.toolkit_service.SkillRepository") as mock_repo:
            mock_repo.get_by_ids.return_value = [skill_1, skill_2]
            result = ToolkitService._merge_skill_mcp_servers(assistant)

        assert len(result) == 1
        assert result[0].name == "shared-server"
