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
Test suite for refactored AssistantService helper methods.

Tests the private helper methods introduced during complexity reduction refactoring:
- _build_bedrock_agent
- _apply_marketplace_tool_mappings
- _prepare_system_prompt
- _configure_langgraph_agent
- _load_and_configure_workflow_assistant
- _prepare_workflow_system_prompt
- _select_agent_class_for_workflow
"""

from unittest.mock import Mock, patch

import pytest

from codemie.core.models import AssistantChatRequest, IdeChatRequest, ToolConfig
from codemie.core.workflow_models import WorkflowAssistant
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.assistant_service import AssistantService


class TestBuildBedrockAgent:
    """Test cases for _build_bedrock_agent helper method."""

    @patch('codemie.service.assistant_service.AIToolsAgent')
    @patch('codemie.service.assistant_service.AssistantService.get_system_prompt')
    def test_build_bedrock_agent_creates_aitools_agent(
        self,
        mock_get_system_prompt,
        mock_aitools_agent,
    ):
        """Test that _build_bedrock_agent creates AIToolsAgent with correct parameters."""
        # Arrange
        mock_get_system_prompt.return_value = "Test system prompt"
        mock_agent_instance = Mock()
        mock_aitools_agent.return_value = mock_agent_instance

        assistant = Mock(spec=Assistant)
        assistant.id = 'bedrock-123'
        assistant.name = 'Bedrock Assistant'
        assistant.description = 'Bedrock Description'
        assistant.temperature = 0.7
        assistant.top_p = 0.9

        request = AssistantChatRequest(text='Hello Bedrock', file_names=[])
        user = Mock(spec=User)
        user.id = 'user-123'

        # Act
        result = AssistantService._build_bedrock_agent(
            assistant=assistant,
            request=request,
            user=user,
            request_uuid='req-123',
            thread_generator=None,
            tool_callbacks=None,
        )

        # Assert
        assert result == mock_agent_instance
        mock_aitools_agent.assert_called_once()
        call_kwargs = mock_aitools_agent.call_args[1]

        assert call_kwargs['agent_name'] == 'Bedrock Assistant'
        assert call_kwargs['description'] == 'Bedrock Description'
        assert call_kwargs['tools'] == []  # Bedrock agents don't use tools
        assert call_kwargs['is_react'] is False
        assert call_kwargs['llm_model'] == ''
        assert call_kwargs['temperature'] == 0.7
        assert call_kwargs['top_p'] == 0.9

    @patch('codemie.service.assistant_service.AIToolsAgent')
    @patch('codemie.service.assistant_service.AssistantService.get_system_prompt')
    def test_build_bedrock_agent_with_callbacks(
        self,
        mock_get_system_prompt,
        mock_aitools_agent,
    ):
        """Test that _build_bedrock_agent passes through callbacks."""
        # Arrange
        mock_get_system_prompt.return_value = "Test system prompt"
        mock_aitools_agent.return_value = Mock()

        assistant = Mock(spec=Assistant)
        assistant.id = 'bedrock-123'
        assistant.name = 'Bedrock Assistant'
        assistant.description = 'Test'
        assistant.temperature = 0.7
        assistant.top_p = 0.9

        request = AssistantChatRequest(text='Test', file_names=[])
        user = Mock(spec=User)
        user.id = 'user-123'

        test_callbacks = [Mock(), Mock()]

        # Act
        AssistantService._build_bedrock_agent(
            assistant=assistant,
            request=request,
            user=user,
            request_uuid='req-123',
            thread_generator=None,
            tool_callbacks=test_callbacks,
        )

        # Assert
        call_kwargs = mock_aitools_agent.call_args[1]
        assert call_kwargs['callbacks'] == test_callbacks


class TestApplyMarketplaceToolMappings:
    """Test cases for _apply_marketplace_tool_mappings helper method."""

    def test_apply_marketplace_tool_mappings_skips_non_global_assistant(self):
        """Test that non-global assistants are skipped."""
        # Arrange
        assistant = Mock(spec=Assistant)
        assistant.is_global = False

        user = Mock(spec=User)
        request = AssistantChatRequest(text='Test', file_names=[])

        # Act
        AssistantService._apply_marketplace_tool_mappings(assistant, user, request)

        # Assert
        # Should return early without modifying request
        assert request.tools_config is None

    @patch('codemie.service.assistant_service.assistant_user_mapping_service')
    def test_apply_marketplace_tool_mappings_with_no_mapping(
        self,
        mock_mapping_service,
    ):
        """Test marketplace assistant with no user mappings."""
        # Arrange
        mock_mapping_service.get_mapping.return_value = None

        assistant = Mock(spec=Assistant)
        assistant.id = 'asst-global'
        assistant.is_global = True
        assistant.mcp_servers = []

        user = Mock(spec=User)
        user.id = 'user-123'

        request = AssistantChatRequest(text='Test', file_names=[])

        # Act
        AssistantService._apply_marketplace_tool_mappings(assistant, user, request)

        # Assert
        assert request.tools_config is None
        mock_mapping_service.get_mapping.assert_called_once_with(assistant_id='asst-global', user_id='user-123')

    @patch('codemie.service.assistant_service.assistant_user_mapping_service')
    def test_apply_marketplace_tool_mappings_adds_tools_config(
        self,
        mock_mapping_service,
    ):
        """Test marketplace assistant with tool mappings."""
        # Arrange
        mock_tool_config1 = Mock()
        mock_tool_config1.name = 'tool1'
        mock_tool_config1.integration_id = 'int1'

        mock_tool_config2 = Mock()
        mock_tool_config2.name = 'tool2'
        mock_tool_config2.integration_id = 'int2'

        mock_mapping = Mock()
        mock_mapping.tools_config = [mock_tool_config1, mock_tool_config2]
        mock_mapping_service.get_mapping.return_value = mock_mapping

        assistant = Mock(spec=Assistant)
        assistant.id = 'asst-global'
        assistant.is_global = True
        assistant.mcp_servers = []

        user = Mock(spec=User)
        user.id = 'user-123'

        request = AssistantChatRequest(text='Test', file_names=[])

        # Act
        AssistantService._apply_marketplace_tool_mappings(assistant, user, request)

        # Assert
        assert request.tools_config is not None
        assert len(request.tools_config) == 2
        assert request.tools_config[0].name == 'tool1'
        assert request.tools_config[0].integration_id == 'int1'
        assert request.tools_config[1].name == 'tool2'
        assert request.tools_config[1].integration_id == 'int2'

    @patch('codemie.service.assistant_service.assistant_user_mapping_service')
    def test_apply_marketplace_tool_mappings_merges_with_existing(
        self,
        mock_mapping_service,
    ):
        """Test that tool mappings merge with existing request tools_config."""
        # Arrange
        mock_tool_config = Mock()
        mock_tool_config.name = 'mapped_tool'
        mock_tool_config.integration_id = 'mapped_int'

        mock_mapping = Mock()
        mock_mapping.tools_config = [mock_tool_config]
        mock_mapping_service.get_mapping.return_value = mock_mapping

        assistant = Mock(spec=Assistant)
        assistant.id = 'asst-global'
        assistant.is_global = True
        assistant.mcp_servers = []

        user = Mock(spec=User)
        user.id = 'user-123'

        existing_tool = ToolConfig(name='existing_tool', integration_id='existing_int')
        request = AssistantChatRequest(text='Test', file_names=[], tools_config=[existing_tool])

        # Act
        AssistantService._apply_marketplace_tool_mappings(assistant, user, request)

        # Assert
        assert len(request.tools_config) == 2
        assert request.tools_config[0].name == 'existing_tool'
        assert request.tools_config[1].name == 'mapped_tool'


class TestPrepareSystemPrompt:
    """Test cases for _prepare_system_prompt helper method."""

    @patch('codemie.service.assistant_service.AssistantService.get_system_prompt')
    def test_prepare_system_prompt_basic(
        self,
        mock_get_system_prompt,
    ):
        """Test basic system prompt preparation."""
        # Arrange
        mock_get_system_prompt.return_value = "Base system prompt"

        assistant = Mock(spec=Assistant)
        assistant.skill_ids = []
        user = Mock(spec=User)
        user.id = 'user-123'
        user.full_name = 'Test User'

        request = AssistantChatRequest(text='Test', file_names=[])

        # Act
        result = AssistantService._prepare_system_prompt(assistant, user, request)

        # Assert
        assert result == "Base system prompt"
        mock_get_system_prompt.assert_called_once_with(assistant, user_id='user-123', current_user='Test User')

    @patch('codemie.service.assistant_service.AssistantService.get_system_prompt')
    def test_prepare_system_prompt_with_skills(
        self,
        mock_get_system_prompt,
    ):
        """Test that skills suffix is appended when assistant has attached skills."""
        # Arrange
        mock_get_system_prompt.return_value = "Base system prompt"

        assistant = Mock(spec=Assistant)
        assistant.skill_ids = ["skill-1", "skill-2"]
        user = Mock(spec=User)
        user.id = 'user-123'
        user.full_name = 'Test User'

        request = AssistantChatRequest(text='Test', file_names=[])

        # Act
        result = AssistantService._prepare_system_prompt(assistant, user, request)

        # Assert
        assert result.startswith("Base system prompt")
        assert "skill" in result
        assert "available skills" in result

    @patch('codemie.service.assistant_service.AssistantService.decorate_system_prompt')
    @patch('codemie.service.assistant_service.AssistantService.get_system_prompt')
    def test_prepare_system_prompt_with_ide_chat_request(
        self,
        mock_get_system_prompt,
        mock_decorate,
    ):
        """Test system prompt preparation with IdeChatRequest decoration."""
        # Arrange
        mock_get_system_prompt.return_value = "Base prompt"
        mock_decorate.return_value = "Decorated prompt"

        assistant = Mock(spec=Assistant)
        assistant.skill_ids = []
        user = Mock(spec=User)
        user.id = 'user-123'
        user.full_name = 'Test User'

        request = IdeChatRequest(text='Test', file_names=[], prompt_header='Header', prompt_footer='Footer')

        # Act
        result = AssistantService._prepare_system_prompt(assistant, user, request)

        # Assert
        assert result == "Decorated prompt"
        mock_decorate.assert_called_once_with("Base prompt", request)

    @patch('codemie.service.assistant_service.AssistantService.get_system_prompt')
    def test_prepare_system_prompt_with_output_schema(
        self,
        mock_get_system_prompt,
    ):
        """Test system prompt preparation with output schema."""
        # Arrange
        mock_get_system_prompt.return_value = "Base prompt"

        assistant = Mock(spec=Assistant)
        assistant.skill_ids = []
        assistant.suggested_json_prompt = "JSON format: {schema}"

        user = Mock(spec=User)
        user.id = 'user-123'
        user.full_name = 'Test User'

        output_schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        request = AssistantChatRequest(text='Test', file_names=[], output_schema=output_schema)

        # Act
        result = AssistantService._prepare_system_prompt(assistant, user, request)

        # Assert
        assert "Base prompt" in result
        assert '"type": "object"' in result  # Schema should be included


class TestConfigureLanggraphAgent:
    """Test cases for _configure_langgraph_agent helper method."""

    @patch('codemie.service.assistant_service.AssistantService._get_subagent_descriptions')
    @patch('codemie.service.assistant_service.AssistantService._create_subagent_executors')
    def test_configure_langgraph_agent_with_subagents(
        self,
        mock_create_subagents,
        mock_get_descriptions,
    ):
        """Test LangGraph configuration with subagents."""
        # Arrange
        mock_subagent1 = Mock()
        mock_subagent2 = Mock()
        mock_create_subagents.return_value = [mock_subagent1, mock_subagent2]
        mock_get_descriptions.return_value = {'subagent1': 'Description 1', 'subagent2': 'Description 2'}

        assistant = Mock(spec=Assistant)
        user = Mock(spec=User)
        request = AssistantChatRequest(text='Test', file_names=[])

        agent_kwargs = {}

        # Act
        AssistantService._configure_langgraph_agent(
            agent_kwargs=agent_kwargs,
            assistant=assistant,
            user=user,
            request=request,
            request_uuid='req-123',
            thread_generator=None,
            llm_model='claude-sonnet-4',
            smart_tool_selection_enabled=True,
        )

        # Assert
        assert agent_kwargs['smart_tool_selection_enabled'] is True
        assert agent_kwargs['subagents'] == [mock_subagent1, mock_subagent2]
        assert agent_kwargs['subagent_descriptions'] == {'subagent1': 'Description 1', 'subagent2': 'Description 2'}

    @patch('codemie.service.assistant_service.AssistantService._create_subagent_executors')
    def test_configure_langgraph_agent_without_subagents(
        self,
        mock_create_subagents,
    ):
        """Test LangGraph configuration without subagents."""
        # Arrange
        mock_create_subagents.return_value = None

        assistant = Mock(spec=Assistant)
        user = Mock(spec=User)
        request = AssistantChatRequest(text='Test', file_names=[])

        agent_kwargs = {}

        # Act
        AssistantService._configure_langgraph_agent(
            agent_kwargs=agent_kwargs,
            assistant=assistant,
            user=user,
            request=request,
            request_uuid='req-123',
            thread_generator=None,
            llm_model='claude-sonnet-4',
            smart_tool_selection_enabled=False,
        )

        # Assert
        assert agent_kwargs['smart_tool_selection_enabled'] is False
        assert 'subagents' not in agent_kwargs
        assert 'subagent_descriptions' not in agent_kwargs


class TestLoadAndConfigureWorkflowAssistant:
    """Test cases for _load_and_configure_workflow_assistant helper method."""

    @patch('codemie.service.assistant_service.Assistant.get_by_id')
    def test_load_workflow_assistant_from_database(
        self,
        mock_get_by_id,
    ):
        """Test loading assistant from database for workflow."""
        # Arrange
        mock_assistant = Mock(spec=Assistant)
        mock_assistant.id = 'asst-123'
        mock_assistant.llm_model_type = 'claude-sonnet-4'
        mock_assistant.temperature = 0.5
        mock_assistant.version = 2
        mock_get_by_id.return_value = mock_assistant

        workflow_assistant = WorkflowAssistant(
            assistant_id='asst-123',
            model='claude-opus-4',
            temperature=0.9,
        )

        user = Mock(spec=User)

        # Act
        result = AssistantService._load_and_configure_workflow_assistant(
            workflow_assistant=workflow_assistant,
            user=user,
            project_name='test-project',
            execution_id='exec-123',
        )

        # Assert
        assert result == mock_assistant
        assert result.llm_model_type == 'claude-opus-4'  # Override from workflow
        assert result.temperature == 0.9  # Override from workflow
        mock_get_by_id.assert_called_once_with('asst-123')

    @patch('codemie.service.assistant_service.Assistant.get_by_id')
    def test_load_workflow_assistant_sets_version_if_missing(
        self,
        mock_get_by_id,
    ):
        """Test that version is set if missing on database assistant."""
        # Arrange
        mock_assistant = Mock(spec=Assistant)
        mock_assistant.id = 'asst-123'
        mock_assistant.llm_model_type = 'claude-sonnet-4'
        mock_assistant.temperature = 0.5
        mock_assistant.version = None
        mock_assistant.version_count = 3
        mock_get_by_id.return_value = mock_assistant

        workflow_assistant = WorkflowAssistant(assistant_id='asst-123')
        user = Mock(spec=User)

        # Act
        result = AssistantService._load_and_configure_workflow_assistant(
            workflow_assistant=workflow_assistant,
            user=user,
            project_name='test-project',
            execution_id='exec-123',
        )

        # Assert
        assert result.version == 3

    @patch('codemie.service.assistant_service.VirtualAssistantService.create_from_virtual_asst_config')
    def test_load_workflow_assistant_creates_virtual(
        self,
        mock_create_virtual,
    ):
        """Test creating virtual assistant for workflow."""
        # Arrange
        mock_assistant = Mock(spec=Assistant)
        mock_assistant.llm_model_type = None
        mock_assistant.temperature = None
        mock_create_virtual.return_value = mock_assistant

        workflow_assistant = WorkflowAssistant(
            assistant_id=None,  # No ID means virtual
            model='claude-sonnet-4',
        )

        user = Mock(spec=User)

        # Act
        with patch('codemie.service.assistant_service.llm_service') as mock_llm_service:
            mock_llm_service.default_llm_model = 'default-model'

            result = AssistantService._load_and_configure_workflow_assistant(
                workflow_assistant=workflow_assistant,
                user=user,
                project_name='test-project',
                execution_id='exec-123',
            )

        # Assert
        assert result == mock_assistant
        mock_create_virtual.assert_called_once()

    @patch('codemie.service.assistant_service.Assistant.get_by_id')
    def test_load_workflow_assistant_raises_on_not_found(
        self,
        mock_get_by_id,
    ):
        """Test that ValueError is raised when assistant not found."""
        # Arrange
        from elasticsearch import NotFoundError

        mock_get_by_id.side_effect = NotFoundError('Not found', {}, {})

        workflow_assistant = WorkflowAssistant(assistant_id='missing-123')
        user = Mock(spec=User)

        # Act & Assert
        with pytest.raises(ValueError, match="Assistant wasn't found"):
            AssistantService._load_and_configure_workflow_assistant(
                workflow_assistant=workflow_assistant,
                user=user,
                project_name='test-project',
                execution_id='exec-123',
            )


class TestPrepareWorkflowSystemPrompt:
    """Test cases for _prepare_workflow_system_prompt helper method."""

    @patch('codemie.service.assistant_service.AssistantService.get_system_prompt')
    def test_prepare_workflow_system_prompt_uses_workflow_prompt(
        self,
        mock_get_system_prompt,
    ):
        """Test that workflow system prompt is used if provided."""
        # Arrange
        workflow_assistant = WorkflowAssistant(assistant_id='asst-123', system_prompt='Custom workflow prompt')

        assistant = Mock(spec=Assistant)
        user = Mock(spec=User)

        # Act
        result_prompt, result_schema = AssistantService._prepare_workflow_system_prompt(
            workflow_assistant=workflow_assistant,
            assistant=assistant,
            user=user,
            workflow_state=None,
            mcp_server_args_preprocessor=None,
        )

        # Assert
        assert result_prompt == 'Custom workflow prompt'
        assert result_schema is None
        mock_get_system_prompt.assert_not_called()

    @patch('codemie.service.assistant_service.AssistantService.get_system_prompt')
    def test_prepare_workflow_system_prompt_falls_back_to_assistant_prompt(
        self,
        mock_get_system_prompt,
    ):
        """Test fallback to assistant system prompt."""
        # Arrange
        mock_get_system_prompt.return_value = 'Assistant system prompt'

        workflow_assistant = WorkflowAssistant(assistant_id='asst-123', system_prompt=None)

        assistant = Mock(spec=Assistant)
        user = Mock(spec=User)
        user.id = 'user-123'
        user.full_name = 'Test User'

        # Act
        result_prompt, result_schema = AssistantService._prepare_workflow_system_prompt(
            workflow_assistant=workflow_assistant,
            assistant=assistant,
            user=user,
            workflow_state=None,
            mcp_server_args_preprocessor=None,
        )

        # Assert
        assert result_prompt == 'Assistant system prompt'
        assert result_schema is None

    @patch('codemie.service.assistant_service.AssistantService.load_and_validate_schema')
    @patch('codemie.service.assistant_service.AssistantService.get_system_prompt')
    def test_prepare_workflow_system_prompt_with_output_schema(
        self,
        mock_get_system_prompt,
        mock_load_schema,
    ):
        """Test workflow system prompt with output schema."""
        # Arrange
        mock_get_system_prompt.return_value = 'Base prompt'
        mock_load_schema.return_value = {"type": "object"}

        workflow_assistant = WorkflowAssistant(assistant_id='asst-123', system_prompt=None)

        assistant = Mock(spec=Assistant)
        user = Mock(spec=User)
        user.id = 'user-123'
        user.full_name = 'Test User'

        workflow_state = Mock()
        workflow_state.output_schema = '{"type": "object"}'

        # Act
        result_prompt, result_schema = AssistantService._prepare_workflow_system_prompt(
            workflow_assistant=workflow_assistant,
            assistant=assistant,
            user=user,
            workflow_state=workflow_state,
            mcp_server_args_preprocessor=None,
        )

        # Assert
        assert "Base prompt" in result_prompt
        assert '"type": "object"' in result_prompt
        assert result_schema == {"type": "object"}


class TestSelectAgentClassForWorkflow:
    """Test cases for _select_agent_class_for_workflow helper method."""

    @patch('codemie.service.assistant_service.config')
    @patch('codemie.service.assistant_service.llm_service')
    @patch('codemie.service.assistant_service.BedrockOrchestratorService')
    def test_select_agent_class_returns_langgraph(
        self,
        mock_bedrock,
        mock_llm_service,
        mock_config,
    ):
        """Test that LangGraphAgent is selected when conditions are met."""
        # Arrange
        from codemie.agents.langgraph_agent import LangGraphAgent

        mock_config.ENABLE_LANGGRAPH_AITOOLS_AGENT = True
        mock_bedrock.is_bedrock_assistant.return_value = False
        mock_llm_service.get_react_llms.return_value = []

        assistant = Mock(spec=Assistant)

        # Act
        result = AssistantService._select_agent_class_for_workflow(assistant=assistant, llm_model='claude-sonnet-4')

        # Assert
        assert result == LangGraphAgent

    @patch('codemie.service.assistant_service.config')
    @patch('codemie.service.assistant_service.llm_service')
    @patch('codemie.service.assistant_service.BedrockOrchestratorService')
    def test_select_agent_class_returns_aitools_for_bedrock(
        self,
        mock_bedrock,
        mock_llm_service,
        mock_config,
    ):
        """Test that AIToolsAgent is selected for Bedrock assistants."""
        # Arrange
        from codemie.agents.assistant_agent import AIToolsAgent

        mock_config.ENABLE_LANGGRAPH_AITOOLS_AGENT = True
        mock_bedrock.is_bedrock_assistant.return_value = True
        mock_llm_service.get_react_llms.return_value = []

        assistant = Mock(spec=Assistant)

        # Act
        result = AssistantService._select_agent_class_for_workflow(assistant=assistant, llm_model='claude-sonnet-4')

        # Assert
        assert result == AIToolsAgent

    @patch('codemie.service.assistant_service.config')
    @patch('codemie.service.assistant_service.llm_service')
    @patch('codemie.service.assistant_service.BedrockOrchestratorService')
    def test_select_agent_class_returns_aitools_for_react(
        self,
        mock_bedrock,
        mock_llm_service,
        mock_config,
    ):
        """Test that AIToolsAgent is selected for ReAct models."""
        # Arrange
        from codemie.agents.assistant_agent import AIToolsAgent

        mock_config.ENABLE_LANGGRAPH_AITOOLS_AGENT = True
        mock_bedrock.is_bedrock_assistant.return_value = False
        mock_llm_service.get_react_llms.return_value = ['gpt-3.5-turbo']

        assistant = Mock(spec=Assistant)

        # Act
        result = AssistantService._select_agent_class_for_workflow(assistant=assistant, llm_model='gpt-3.5-turbo')

        # Assert
        assert result == AIToolsAgent
