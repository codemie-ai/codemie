# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""Tests that AssistantService threads the `effort` generation parameter into the agent factory."""

from unittest.mock import Mock, patch

import pytest

from codemie.core.models import AssistantChatRequest
from codemie.core.workflow_models import WorkflowAssistant
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.security.user import User
from codemie.service.assistant_service import AssistantService


def _mock_assistant(effort):
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
    assistant.effort = effort
    assistant.project = 'test-project'
    assistant.bedrock = None
    assistant.smart_tool_selection_enabled = False
    assistant.prompt_variables = []
    assistant.is_global = False
    assistant.shared = False
    assistant.is_react = False
    assistant.mcp_servers = []
    assistant.tool_permissions = None
    assistant.skill_ids = []
    return assistant


def _mock_user():
    user = Mock(spec=User)
    user.id = 'user-123'
    user.name = 'Test User'
    user.full_name = 'Test User Full Name'
    user.username = 'test@email.com'
    user.is_admin = False
    return user


class TestBuildAgentEffort:
    @pytest.mark.parametrize('effort', ['high', None])
    @patch('codemie.service.llm.model_availability_service.Application.get_by_id', return_value=None)
    @patch('codemie.service.assistant_service.Conversation.find_by_id', return_value=None)
    @patch('codemie.service.assistant_service.AIToolsAgent')
    @patch('codemie.service.assistant_service.LangGraphAgent')
    @patch('codemie.service.assistant_service.config')
    @patch('codemie.service.assistant_service.ToolkitService.get_tools', return_value=[])
    @patch('codemie.service.assistant_service.llm_service')
    @patch('codemie.service.assistant_service.set_llm_context')
    @patch('codemie.service.assistant_service.build_unique_file_objects', return_value={})
    @patch('codemie.service.assistant_service.AssistantService.drop_missing_context', return_value=[])
    @patch(
        'codemie.service.assistant_service.BedrockOrchestratorService.is_bedrock_assistant',
        return_value=False,
    )
    def test_build_agent_passes_assistant_effort_to_agent(
        self,
        _is_bedrock,
        _drop_missing,
        _build_files,
        _set_ctx,
        mock_llm_service,
        _get_tools,
        mock_config,
        _langgraph_agent,
        mock_aitools_agent,
        _find_by_id,
        _get_app,
        effort,
    ):
        mock_llm_service.get_react_llms.return_value = []
        mock_llm_service.default_llm_model = 'claude-sonnet-4'
        mock_config.ENABLE_LANGGRAPH_AITOOLS_AGENT = False

        AssistantService.build_agent(
            assistant=_mock_assistant(effort),
            request=AssistantChatRequest(text='Hello', file_names=[]),
            user=_mock_user(),
            request_uuid='req-123',
        )

        assert mock_aitools_agent.call_args[1]['effort'] == effort


class TestBuildAgentForWorkflowEffort:
    @patch('codemie.service.assistant_service.AIToolsAgent')
    @patch('codemie.service.assistant_service.LangGraphAgent')
    @patch('codemie.service.assistant_service.config')
    @patch('codemie.service.assistant_service.ToolkitService.get_tools', return_value=[])
    @patch('codemie.service.assistant_service.llm_service')
    @patch('codemie.service.assistant_service.set_llm_context')
    @patch('codemie.service.assistant_service.build_unique_file_objects_list', return_value=[])
    @patch('codemie.service.assistant_service.Assistant.get_by_id')
    def test_workflow_override_effort_reaches_agent(
        self,
        mock_get_by_id,
        _build_files,
        _set_ctx,
        mock_llm_service,
        _get_tools,
        mock_config,
        _langgraph_agent,
        mock_aitools_agent,
    ):
        mock_llm_service.get_react_llms.return_value = []
        mock_llm_service.default_llm_model = 'claude-sonnet-4'
        mock_config.ENABLE_LANGGRAPH_AITOOLS_AGENT = False
        mock_get_by_id.return_value = _mock_assistant('high')

        AssistantService.build_agent_for_workflow(
            user_input='Process workflow',
            user=_mock_user(),
            request_uuid='req-123',
            workflow_assistant=WorkflowAssistant(assistant_id='asst-123', effort='max'),
            execution_id='exec-123',
            project_name='test-project',
        )

        assert mock_aitools_agent.call_args[1]['effort'] == 'max'


class TestWorkflowAssistantEffortOverride:
    @patch('codemie.service.assistant_service.Assistant.get_by_id')
    def test_workflow_effort_overrides_assistant_effort(self, mock_get_by_id):
        mock_get_by_id.return_value = _mock_assistant('high')

        result = AssistantService._load_and_configure_workflow_assistant(
            workflow_assistant=WorkflowAssistant(assistant_id='asst-123', effort='max'),
            user=_mock_user(),
            project_name='test-project',
            execution_id='exec-123',
        )

        assert result.effort == 'max'

    @patch('codemie.service.assistant_service.Assistant.get_by_id')
    def test_workflow_none_effort_does_not_clobber_assistant_effort(self, mock_get_by_id):
        mock_get_by_id.return_value = _mock_assistant('high')

        result = AssistantService._load_and_configure_workflow_assistant(
            workflow_assistant=WorkflowAssistant(assistant_id='asst-123', effort=None),
            user=_mock_user(),
            project_name='test-project',
            execution_id='exec-123',
        )

        assert result.effort == 'high'
