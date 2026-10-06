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

"""Regression for EPMCDME-12505: a deleted datasource must not break assistant initialization."""

from unittest.mock import Mock, patch

import pytest

from codemie.core.models import AssistantChatRequest
from codemie.rest_api.models.assistant import Assistant, Context, ContextType
from codemie.rest_api.security.user import User
from codemie.service.assistant_service import AssistantService

KB = Context(context_type=ContextType.KNOWLEDGE_BASE, name="kb-alive")
CODE_GONE = Context(context_type=ContextType.CODE, name="repo-deleted")


def _assistant(context):
    assistant = Mock(spec=Assistant)
    assistant.id = "asst-123"
    assistant.name = "Test Assistant"
    assistant.description = "d"
    assistant.system_prompt = "You are a helpful assistant"
    assistant.context = context
    assistant.toolkits = []
    assistant.llm_model_type = "claude-sonnet-4"
    assistant.temperature = 0.7
    assistant.top_p = 0.9
    assistant.project = "test-project"
    assistant.bedrock = None
    assistant.smart_tool_selection_enabled = False
    assistant.prompt_variables = []
    assistant.is_global = False
    assistant.mcp_servers = []
    return assistant


def _user():
    user = Mock(spec=User)
    user.id = "user-123"
    user.name = "Test User"
    user.full_name = "Test User Full Name"
    user.username = "test@email.com"
    return user


@pytest.mark.parametrize(
    "context, existing, expected",
    [
        ([KB, CODE_GONE], {("kb-alive", ContextType.KNOWLEDGE_BASE)}, [KB]),
        ([CODE_GONE], set(), []),
    ],
    ids=["one-deleted", "all-deleted"],
)
@patch("codemie.service.assistant_service.AssistantService._existing_context_keys")
@patch("codemie.service.assistant_service.Conversation.find_by_id", return_value=None)
@patch("codemie.service.assistant_service.AIToolsAgent")
@patch("codemie.service.assistant_service.LangGraphAgent")
@patch("codemie.service.assistant_service.config")
@patch("codemie.service.assistant_service.ToolkitService.get_tools", return_value=[])
@patch("codemie.service.assistant_service.llm_service")
@patch("codemie.service.assistant_service.set_llm_context")
@patch("codemie.service.assistant_service.build_unique_file_objects", return_value={})
@patch("codemie.service.assistant_service.BedrockOrchestratorService.is_bedrock_assistant", return_value=False)
def test_build_agent_with_deleted_datasource_builds_with_remaining(
    _bedrock,
    _files,
    _llm_ctx,
    mock_llm_service,
    mock_get_tools,
    mock_config,
    _langgraph,
    mock_aitools,
    _find,
    mock_keys,
    context,
    existing,
    expected,
):
    mock_keys.return_value = existing
    mock_llm_service.get_react_llms.return_value = []
    mock_llm_service.default_llm_model = "claude-sonnet-4"
    mock_config.ENABLE_LANGGRAPH_AITOOLS_AGENT = False
    mock_aitools.return_value = Mock()
    assistant = _assistant(list(context))

    AssistantService.build_agent(
        assistant=assistant,
        request=AssistantChatRequest(text="Hello", file_names=[]),
        user=_user(),
        request_uuid="req-123",
        thread_generator=None,
        tool_callbacks=None,
    )

    mock_get_tools.assert_called_once()
    assert mock_get_tools.call_args[0][0].context == expected
    assistant.update.assert_not_called()
    assistant.save.assert_not_called()
