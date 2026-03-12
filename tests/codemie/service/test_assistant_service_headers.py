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
Test suite for AssistantService with request_headers propagation.

Tests that AssistantService.build_agent and build_agent_for_workflow properly pass
request_headers to the ToolkitService for MCP header propagation.
"""

from unittest.mock import Mock, patch


from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.assistant_service import AssistantService


class TestAssistantServiceBuildAgentWithHeaders:
    """Test cases for AssistantService.build_agent with request_headers."""

    @patch('codemie.service.assistant_service.AIToolsAgent')
    @patch('codemie.service.assistant_service.LangGraphAgent')
    @patch('codemie.service.assistant_service.config')
    @patch('codemie.service.assistant_service.ToolkitService.get_tools')
    @patch('codemie.service.assistant_service.llm_service')
    @patch('codemie.service.assistant_service.set_llm_context')
    @patch('codemie.service.assistant_service.build_unique_file_objects')
    @patch('codemie.service.assistant_service.AssistantService.check_context')
    @patch('codemie.service.assistant_service.BedrockOrchestratorService.is_bedrock_assistant')
    def test_build_agent_with_request_headers(
        self,
        mock_is_bedrock_assistant,
        mock_check_context,
        mock_build_file_objects,
        mock_set_llm_context,
        mock_llm_service,
        mock_get_tools,
        mock_config,
        mock_langgraph_agent,
        mock_aitools_agent,
    ):
        """
        TC-2.3.1: Verify build_agent passes headers to toolkit service.

        Priority: Critical

        Tests that when build_agent is called with request_headers,
        those headers are propagated to ToolkitService.get_tools.
        """
        # Arrange
        mock_is_bedrock_assistant.return_value = False  # Not a Bedrock assistant
        mock_build_file_objects.return_value = {}
        mock_get_tools.return_value = []
        mock_llm_service.get_react_llms.return_value = []
        mock_llm_service.default_llm_model = 'claude-sonnet-4'
        mock_config.ENABLE_LANGGRAPH_AITOOLS_AGENT = False
        mock_aitools_agent.return_value = Mock()

        assistant = Mock(spec=Assistant)
        assistant.id = 'asst-123'
        assistant.name = 'Test Assistant'
        assistant.description = 'Test Description'
        assistant.system_prompt = 'You are a helpful assistant'
        assistant.context = []
        assistant.toolkits = []
        assistant.llm_model_type = 'claude-sonnet-4'
        assistant.temperature = 0.7
        assistant.top_p = 0.9
        assistant.project = 'test-project'
        assistant.bedrock = None
        assistant.smart_tool_selection_enabled = False
        assistant.prompt_variables = []
        assistant.is_global = False  # Not a marketplace assistant
        assistant.mcp_servers = []

        request = AssistantChatRequest(text='Hello', file_names=[])
        user = Mock(spec=User)
        user.id = 'user-123'
        user.name = 'Test User'
        user.full_name = 'Test User Full'

        test_headers = {'X-Tenant-ID': 'tenant-123', 'X-User-ID': 'user-456'}

        # Act
        AssistantService.build_agent(
            assistant=assistant,
            request=request,
            user=user,
            request_uuid='req-123',
            thread_generator=None,
            tool_callbacks=None,
            request_headers=test_headers,
        )

        # Assert - verify get_tools called with request_headers
        mock_get_tools.assert_called_once()
        call_kwargs = mock_get_tools.call_args[1]
        assert 'request_headers' in call_kwargs
        assert call_kwargs['request_headers'] == test_headers

    @patch('codemie.service.assistant_service.AIToolsAgent')
    @patch('codemie.service.assistant_service.LangGraphAgent')
    @patch('codemie.service.assistant_service.config')
    @patch('codemie.service.assistant_service.ToolkitService.get_tools')
    @patch('codemie.service.assistant_service.llm_service')
    @patch('codemie.service.assistant_service.set_llm_context')
    @patch('codemie.service.assistant_service.build_unique_file_objects')
    @patch('codemie.service.assistant_service.AssistantService.check_context')
    @patch('codemie.service.assistant_service.BedrockOrchestratorService.is_bedrock_assistant')
    def test_build_agent_without_request_headers(
        self,
        mock_is_bedrock_assistant,
        mock_check_context,
        mock_build_file_objects,
        mock_set_llm_context,
        mock_llm_service,
        mock_get_tools,
        mock_config,
        mock_langgraph_agent,
        mock_aitools_agent,
    ):
        """
        TC-2.3.2: Verify build_agent without request_headers (backward compatibility).

        Priority: High

        Tests that when build_agent is called without request_headers parameter,
        it still works correctly (backward compatibility).
        """
        # Arrange
        mock_is_bedrock_assistant.return_value = False  # Not a Bedrock assistant
        mock_build_file_objects.return_value = {}
        mock_get_tools.return_value = []
        mock_llm_service.get_react_llms.return_value = []
        mock_llm_service.default_llm_model = 'claude-sonnet-4'
        mock_config.ENABLE_LANGGRAPH_AITOOLS_AGENT = False
        mock_aitools_agent.return_value = Mock()

        assistant = Mock(spec=Assistant)
        assistant.id = 'asst-123'
        assistant.name = 'Test Assistant'
        assistant.description = 'Test Description'
        assistant.system_prompt = 'You are a helpful assistant'
        assistant.context = []
        assistant.toolkits = []
        assistant.llm_model_type = 'claude-sonnet-4'
        assistant.temperature = 0.7
        assistant.top_p = 0.9
        assistant.project = 'test-project'
        assistant.bedrock = None
        assistant.smart_tool_selection_enabled = False
        assistant.prompt_variables = []
        assistant.is_global = False  # Not a marketplace assistant
        assistant.mcp_servers = []

        request = AssistantChatRequest(text='Hello', file_names=[])
        user = Mock(spec=User)
        user.id = 'user-123'
        user.name = 'Test User'
        user.full_name = 'Test User Full'

        # Act - call without request_headers (backward compatibility)
        agent = AssistantService.build_agent(
            assistant=assistant,
            request=request,
            user=user,
            request_uuid='req-123',
            thread_generator=None,
            tool_callbacks=None,
            # No request_headers parameter
        )

        # Assert - agent builds successfully
        assert agent is not None
        mock_get_tools.assert_called_once()

        # Assert - get_tools called with request_headers=None (default)
        call_kwargs = mock_get_tools.call_args[1]
        assert 'request_headers' in call_kwargs
        assert call_kwargs['request_headers'] is None


class TestAssistantServiceBuildAgentForWorkflowWithHeaders:
    """Test cases for AssistantService.build_agent_for_workflow with request_headers."""

    @patch('codemie.service.assistant_service.AIToolsAgent')
    @patch('codemie.service.assistant_service.LangGraphAgent')
    @patch('codemie.service.assistant_service.config')
    @patch('codemie.service.assistant_service.ToolkitService.get_tools')
    @patch('codemie.service.assistant_service.llm_service')
    @patch('codemie.service.assistant_service.set_llm_context')
    @patch('codemie.service.assistant_service.build_unique_file_objects_list')
    @patch('codemie.service.assistant_service.Assistant.get_by_id')
    def test_build_agent_for_workflow_with_headers(
        self,
        mock_get_by_id,
        mock_build_file_objects_list,
        mock_set_llm_context,
        mock_llm_service,
        mock_get_tools,
        mock_config,
        mock_langgraph_agent,
        mock_aitools_agent,
    ):
        """
        TC-2.3.3: Verify build_agent_for_workflow with headers.

        Priority: Critical

        Tests that when building a workflow agent with request_headers,
        those headers are propagated to ToolkitService.get_tools.
        """
        # Arrange
        mock_build_file_objects_list.return_value = []
        mock_get_tools.return_value = []
        mock_llm_service.get_react_llms.return_value = []
        mock_llm_service.default_llm_model = 'claude-sonnet-4'
        mock_config.ENABLE_LANGGRAPH_AITOOLS_AGENT = False
        mock_aitools_agent.return_value = Mock()

        assistant = Mock(spec=Assistant)
        assistant.id = 'asst-123'
        assistant.name = 'Workflow Assistant'
        assistant.description = 'Workflow Description'
        assistant.system_prompt = 'You are a workflow assistant'
        assistant.context = []
        assistant.toolkits = []
        assistant.llm_model_type = 'claude-sonnet-4'
        assistant.temperature = 0.7
        assistant.top_p = 0.9
        assistant.project = 'test-project'
        assistant.is_react = False
        assistant.prompt_variables = []
        mock_get_by_id.return_value = assistant

        user = Mock(spec=User)
        user.id = 'user-123'
        user.name = 'Test User'
        user.full_name = 'Test User Full'

        from codemie.core.workflow_models import WorkflowAssistant

        workflow_assistant = WorkflowAssistant(
            assistant_id='asst-123',
            model='claude-sonnet-4',
            temperature=0.7,
            system_prompt=None,
            exclude_extra_context_tools=False,
            limit_tool_output_tokens=None,
        )

        test_headers = {'X-Tenant-ID': 'tenant-123', 'X-Workflow-ID': 'wf-789'}

        # Act
        AssistantService.build_agent_for_workflow(
            user_input='Process workflow',
            user=user,
            request_uuid='req-123',
            workflow_assistant=workflow_assistant,
            workflow_state=None,
            tool_callbacks=None,
            thread_generator=None,
            file_names=None,
            resume_execution=False,
            execution_id='exec-123',
            project_name='test-project',
            mcp_server_args_preprocessor=None,
            request_headers=test_headers,
        )

        # Assert - verify get_tools called with request_headers
        mock_get_tools.assert_called_once()
        call_kwargs = mock_get_tools.call_args[1]
        assert 'request_headers' in call_kwargs
        assert call_kwargs['request_headers'] == test_headers

    @patch('codemie.service.assistant_service.AIToolsAgent')
    @patch('codemie.service.assistant_service.LangGraphAgent')
    @patch('codemie.service.assistant_service.config')
    @patch('codemie.service.assistant_service.ToolkitService.get_tools')
    @patch('codemie.service.assistant_service.llm_service')
    @patch('codemie.service.assistant_service.set_llm_context')
    @patch('codemie.service.assistant_service.build_unique_file_objects_list')
    @patch('codemie.service.assistant_service.Assistant.get_by_id')
    def test_build_agent_for_workflow_without_headers(
        self,
        mock_get_by_id,
        mock_build_file_objects_list,
        mock_set_llm_context,
        mock_llm_service,
        mock_get_tools,
        mock_config,
        mock_langgraph_agent,
        mock_aitools_agent,
    ):
        """
        Verify build_agent_for_workflow without headers (backward compatibility).

        Priority: High

        Tests that workflows work correctly without request_headers parameter.
        """
        # Arrange
        mock_build_file_objects_list.return_value = []
        mock_get_tools.return_value = []
        mock_llm_service.get_react_llms.return_value = []
        mock_llm_service.default_llm_model = 'claude-sonnet-4'
        mock_config.ENABLE_LANGGRAPH_AITOOLS_AGENT = False
        mock_aitools_agent.return_value = Mock()

        assistant = Mock(spec=Assistant)
        assistant.id = 'asst-123'
        assistant.name = 'Workflow Assistant'
        assistant.description = 'Workflow Description'
        assistant.system_prompt = 'You are a workflow assistant'
        assistant.context = []
        assistant.toolkits = []
        assistant.llm_model_type = 'claude-sonnet-4'
        assistant.temperature = 0.7
        assistant.top_p = 0.9
        assistant.project = 'test-project'
        assistant.is_react = False
        assistant.prompt_variables = []
        mock_get_by_id.return_value = assistant

        user = Mock(spec=User)
        user.id = 'user-123'
        user.name = 'Test User'
        user.full_name = 'Test User Full'

        from codemie.core.workflow_models import WorkflowAssistant

        workflow_assistant = WorkflowAssistant(
            assistant_id='asst-123',
            model='claude-sonnet-4',
            temperature=0.7,
            system_prompt=None,
            exclude_extra_context_tools=False,
            limit_tool_output_tokens=None,
        )

        # Act - call without request_headers
        agent = AssistantService.build_agent_for_workflow(
            user_input='Process workflow',
            user=user,
            request_uuid='req-123',
            workflow_assistant=workflow_assistant,
            workflow_state=None,
            tool_callbacks=None,
            thread_generator=None,
            file_names=None,
            resume_execution=False,
            execution_id='exec-123',
            project_name='test-project',
            mcp_server_args_preprocessor=None,
            # No request_headers parameter
        )

        # Assert - agent builds successfully
        assert agent is not None
        mock_get_tools.assert_called_once()

        # Assert - get_tools called with request_headers=None (default)
        call_kwargs = mock_get_tools.call_args[1]
        assert 'request_headers' in call_kwargs
        assert call_kwargs['request_headers'] is None
