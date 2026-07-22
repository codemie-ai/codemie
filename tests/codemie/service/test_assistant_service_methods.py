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

    def test_apply_marketplace_tool_mappings_skips_private_assistant(self):
        """Test that private (non-shared) assistants are skipped."""
        # Arrange
        assistant = Mock(spec=Assistant)
        assistant.is_global = False
        assistant.shared = False

        user = Mock(spec=User)
        request = AssistantChatRequest(text='Test', file_names=[])

        # Act
        AssistantService._apply_marketplace_tool_mappings(assistant, user, request)

        # Assert
        # Should return early without modifying request
        assert request.tools_config is None

    @patch('codemie.service.assistant_service.assistant_user_mapping_service')
    def test_apply_marketplace_tool_mappings_shared_non_global_applies_only_mcp(
        self,
        mock_mapping_service,
    ):
        """Shared, non-global assistants only receive extended-gate (MCP) mappings."""
        # Arrange
        mcp_tool_config = Mock()
        mcp_tool_config.name = 'MCP:server1'
        mcp_tool_config.integration_id = 'mcp-int'

        regular_tool_config = Mock()
        regular_tool_config.name = 'Git'
        regular_tool_config.integration_id = 'git-int'

        mock_mapping = Mock()
        mock_mapping.tools_config = [mcp_tool_config, regular_tool_config]
        mock_mapping_service.get_mapping.return_value = mock_mapping

        assistant = Mock(spec=Assistant)
        assistant.id = 'asst-project'
        assistant.is_global = False
        assistant.shared = True
        assistant.mcp_servers = []

        user = Mock(spec=User)
        user.id = 'user-123'

        request = AssistantChatRequest(text='Test', file_names=[])

        # Act
        AssistantService._apply_marketplace_tool_mappings(assistant, user, request)

        # Assert — only the MCP mapping is applied; the regular tool mapping is skipped
        assert request.tools_config is not None
        assert len(request.tools_config) == 1
        assert request.tools_config[0].name == 'MCP:server1'
        assert request.tools_config[0].integration_id == 'mcp-int'

    @patch('codemie.service.assistant_service.assistant_user_mapping_service')
    def test_apply_marketplace_tool_mappings_shared_non_global_without_mcp_noop(
        self,
        mock_mapping_service,
    ):
        """Shared, non-global assistants with only regular mappings apply nothing."""
        # Arrange
        regular_tool_config = Mock()
        regular_tool_config.name = 'Git'
        regular_tool_config.integration_id = 'git-int'

        mock_mapping = Mock()
        mock_mapping.tools_config = [regular_tool_config]
        mock_mapping_service.get_mapping.return_value = mock_mapping

        assistant = Mock(spec=Assistant)
        assistant.id = 'asst-project'
        assistant.is_global = False
        assistant.shared = True
        assistant.mcp_servers = []

        user = Mock(spec=User)
        user.id = 'user-123'

        request = AssistantChatRequest(text='Test', file_names=[])

        # Act
        AssistantService._apply_marketplace_tool_mappings(assistant, user, request)

        # Assert
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
    def test_apply_marketplace_tool_mappings_global_not_shared_still_applies_all(
        self,
        mock_mapping_service,
    ):
        """Rare is_global && not shared: the early gate must not block; is_global applies all mappings."""
        # Arrange
        mcp_tool_config = Mock()
        mcp_tool_config.name = 'MCP:server1'
        mcp_tool_config.integration_id = 'mcp-int'

        regular_tool_config = Mock()
        regular_tool_config.name = 'Git'
        regular_tool_config.integration_id = 'git-int'

        mock_mapping = Mock()
        mock_mapping.tools_config = [mcp_tool_config, regular_tool_config]
        mock_mapping_service.get_mapping.return_value = mock_mapping

        assistant = Mock(spec=Assistant)
        assistant.id = 'asst-global-not-shared'
        assistant.is_global = True
        assistant.shared = False
        assistant.mcp_servers = []

        user = Mock(spec=User)
        user.id = 'user-123'

        request = AssistantChatRequest(text='Test', file_names=[])

        # Act
        AssistantService._apply_marketplace_tool_mappings(assistant, user, request)

        # Assert — global branch applies ALL mappings (not just MCP)
        assert request.tools_config is not None
        assert {tc.name for tc in request.tools_config} == {'MCP:server1', 'Git'}

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


class TestApplyConversationRuntimeOverrides:
    @patch('codemie.service.assistant_service.Conversation.find_by_id')
    def test_applies_saved_conversation_image_generation_settings(self, mock_find_by_id):
        assistant = Mock(spec=Assistant)
        assistant.enable_image_generation = True
        assistant.image_generation_model = "assistant-image-model"

        conversation = Mock()
        conversation.llm_model = "conversation-model"
        conversation.enable_image_generation = False
        conversation.image_generation_model = "conversation-image-model"
        mock_find_by_id.return_value = conversation

        request = AssistantChatRequest(text='Test', file_names=[], conversation_id='conv-1')

        AssistantService._apply_conversation_runtime_overrides(assistant, request)

        assert request.llm_model == "conversation-model"
        assert request.enable_image_generation is False
        assert request.image_generation_model == "conversation-image-model"
        # Assistant capability flags should not be mutated by conversation/request overrides.
        # Overrides are applied to the request only (runtime behavior), especially to avoid
        # leaking parent assistant capabilities into sub-assistants.
        assert assistant.enable_image_generation is True
        assert assistant.image_generation_model == "assistant-image-model"

    @patch('codemie.service.assistant_service.Conversation.find_by_id')
    def test_keeps_explicit_request_image_generation_overrides(self, mock_find_by_id):
        assistant = Mock(spec=Assistant)
        assistant.enable_image_generation = False
        assistant.image_generation_model = None

        conversation = Mock()
        conversation.llm_model = "conversation-model"
        conversation.enable_image_generation = False
        conversation.image_generation_model = "conversation-image-model"
        mock_find_by_id.return_value = conversation

        request = AssistantChatRequest(
            text='Test',
            file_names=[],
            conversation_id='conv-1',
            llm_model='request-model',
            enable_image_generation=True,
            image_generation_model='request-image-model',
        )

        AssistantService._apply_conversation_runtime_overrides(assistant, request)

        assert request.llm_model == "request-model"
        assert request.enable_image_generation is True
        assert request.image_generation_model == "request-image-model"
        # Assistant capability flags should not be mutated by request overrides.
        assert assistant.enable_image_generation is False
        assert assistant.image_generation_model is None

    @patch('codemie.service.assistant_service.Conversation.find_by_id')
    def test_does_not_override_assistant_with_unset_conversation_image_generation(self, mock_find_by_id):
        assistant = Mock(spec=Assistant)
        assistant.enable_image_generation = True
        assistant.image_generation_model = "assistant-image-model"

        conversation = Mock()
        conversation.llm_model = None
        conversation.enable_image_generation = None
        conversation.image_generation_model = None
        mock_find_by_id.return_value = conversation

        request = AssistantChatRequest(text='Test', file_names=[], conversation_id='conv-1')

        AssistantService._apply_conversation_runtime_overrides(assistant, request)

        assert request.enable_image_generation is None
        assert request.image_generation_model is None
        assert assistant.enable_image_generation is True
        assert assistant.image_generation_model == "assistant-image-model"


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
        assistant.interactive_features = None
        user = Mock(spec=User)
        user.id = 'user-123'
        user.username = "test@email.com"

        request = AssistantChatRequest(text='Test', file_names=[])

        # Act
        result = AssistantService._prepare_system_prompt(assistant, user, request)

        # Assert
        assert result == "Base system prompt"
        mock_get_system_prompt.assert_called_once_with(assistant, user_id='user-123', current_user='test@email.com')

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
        user.username = "test@email.com"

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
        assistant.interactive_features = None
        user = Mock(spec=User)
        user.id = 'user-123'
        user.username = "test@email.com"

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
        assistant.interactive_features = None
        assistant.suggested_json_prompt = "JSON format: {schema}"

        user = Mock(spec=User)
        user.id = 'user-123'
        user.username = "test@email.com"

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
        assistant.skill_ids = []
        assistant.interactive_features = None
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
        assistant.skill_ids = []
        assistant.interactive_features = None
        user = Mock(spec=User)
        user.id = 'user-123'
        user.username = "test@email.com"

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

    @patch('codemie.service.assistant_service.AssistantService.get_system_prompt')
    def test_prepare_workflow_system_prompt_appends_skills_suffix_when_skill_ids_present(
        self,
        mock_get_system_prompt,
    ):
        """Test that skills suffix is appended to system prompt when assistant has skill_ids."""
        # Arrange
        mock_get_system_prompt.return_value = 'Base prompt'

        workflow_assistant = WorkflowAssistant(assistant_id='asst-123', system_prompt=None)

        assistant = Mock(spec=Assistant)
        assistant.skill_ids = ['skill-1', 'skill-2']
        user = Mock(spec=User)
        user.id = 'user-123'
        user.username = "test@email.com"

        # Act
        result_prompt, result_schema = AssistantService._prepare_workflow_system_prompt(
            workflow_assistant=workflow_assistant,
            assistant=assistant,
            user=user,
            workflow_state=None,
            mcp_server_args_preprocessor=None,
        )

        # Assert
        assert 'Base prompt' in result_prompt
        assert 'skill' in result_prompt
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
        assistant.skill_ids = []
        assistant.interactive_features = None
        user = Mock(spec=User)
        user.id = 'user-123'
        user.username = "test@email.com"

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


class TestEnsureUniqueSlug:
    """Test cases for AssistantService.ensure_unique_slug (per-project uniqueness)."""

    @patch('codemie.service.assistant_service.Assistant.get_by_fields')
    def test_returns_slug_unchanged_when_free_in_project(self, mock_get_by_fields):
        # No existing assistant with this slug in the project.
        mock_get_by_fields.return_value = None

        result = AssistantService.ensure_unique_slug("knowledge-companion", "scor-gits")

        assert result == "knowledge-companion"
        # Collision check must be scoped to the project, not global.
        mock_get_by_fields.assert_called_once_with(
            {"slug.keyword": "knowledge-companion", "project.keyword": "scor-gits"}
        )

    @patch('codemie.service.assistant_service.Assistant.get_by_fields')
    def test_appends_suffix_when_slug_taken_in_project(self, mock_get_by_fields):
        # Slug already exists within the same project -> a suffixed variant is returned.
        mock_get_by_fields.return_value = Mock(spec=Assistant)

        result = AssistantService.ensure_unique_slug("knowledge-companion", "scor-gits")

        assert result != "knowledge-companion"
        assert result.startswith("knowledge-companion_")

    @patch('codemie.service.assistant_service.Assistant.get_by_fields')
    def test_no_project_skips_collision_check(self, mock_get_by_fields):
        # Without a project there is no per-project scope: return the slug untouched
        # and never query (a None/empty project filter has undefined semantics).
        for empty_project in (None, ""):
            result = AssistantService.ensure_unique_slug("knowledge-companion", empty_project)
            assert result == "knowledge-companion"
        mock_get_by_fields.assert_not_called()


class TestInteractivePromptGating:
    @patch('codemie.service.assistant_service.AssistantService.get_system_prompt')
    def test_prompt_injected_only_with_thread_generator(self, mock_get_prompt):
        from unittest.mock import MagicMock, patch as _patch
        from codemie.core.interactive import InteractiveFeaturesConfig
        from codemie.service.assistant_service import AssistantService

        mock_get_prompt.return_value = "Base"
        assistant = Mock(spec=Assistant)
        assistant.skill_ids = []
        assistant.interactive_features = InteractiveFeaturesConfig(action_buttons=True)
        user = Mock(spec=User)
        user.id = "u"
        user.username = "u@e.com"
        request = AssistantChatRequest(text="hi", file_names=[])

        flag = MagicMock()
        flag.is_feature_enabled.return_value = True
        with _patch("codemie.service.assistant_service.customer_config", flag):
            # Non-streaming (no thread_generator): tool absent -> prompt must NOT advertise it
            without = AssistantService._prepare_system_prompt(assistant, user, request, None)
            # Streaming (thread_generator present): prompt advertises the tool
            with_tg = AssistantService._prepare_system_prompt(assistant, user, request, MagicMock())

        assert "request_user_input" not in without
        assert "request_user_input" in with_tg
