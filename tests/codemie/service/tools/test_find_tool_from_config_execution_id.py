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

"""Unit tests for ToolsService.find_tool_from_config execution_id -> conversation_id threading."""

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.models import AssistantChatRequest
from codemie.rest_api.security.user import User
from codemie.service.tools.tool_service import ToolsService
from codemie.service.tools.toolkit_settings_service import ToolkitSettingService


@pytest.fixture
def mock_user() -> User:
    user = MagicMock(spec=User)
    user.id = "executor-user-id"
    return user


@pytest.fixture
def mock_assistant():
    assistant = MagicMock()
    assistant.id = "assistant-1"
    assistant.project = "test-project"
    assistant.toolkits = []
    return assistant


@pytest.fixture
def mock_tool_config():
    tool_config = MagicMock()
    tool_config.tool = "write_workspace_file"
    return tool_config


def _toolkit_details(toolkit_name="AgentWorkspace"):
    toolkit = MagicMock()
    toolkit.toolkit = toolkit_name
    toolkit.get_tool_configs.return_value = []
    return toolkit


def _capturing_toolkit_method(captured_requests):
    def fake_toolkit_method(assistant, user, llm_model, request_uuid, request):
        captured_requests.append(request)
        tool = MagicMock()
        tool.name = "write_workspace_file"
        return [tool]

    return fake_toolkit_method


@patch("codemie.service.tools.ToolkitService.get_core_tools")
@patch("codemie.service.tools.tool_service.ToolsService.get_toolkit_from_workflow_tool_config")
def test_find_tool_from_config_threads_execution_id_as_conversation_id(
    mock_get_toolkit_config: MagicMock,
    mock_get_core_tools: MagicMock,
    mock_assistant,
    mock_user,
    mock_tool_config,
) -> None:
    """execution_id must reach AssistantChatRequest.conversation_id when core tools are empty."""
    mock_get_toolkit_config.return_value = _toolkit_details()
    mock_get_core_tools.return_value = []  # force the toolkit-fallback branch (the bug site)

    captured_requests = []
    toolkits = {"AgentWorkspace": _capturing_toolkit_method(captured_requests)}

    ToolsService.find_tool_from_config(
        mock_tool_config,
        toolkits,
        mock_assistant,
        mock_user,
        "test-project",
        execution_id="exec_123",
    )

    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert isinstance(request, AssistantChatRequest)
    assert request.conversation_id == "exec_123"


@patch("codemie.service.tools.ToolkitService.get_core_tools")
@patch("codemie.service.tools.tool_service.ToolsService.get_toolkit_from_workflow_tool_config")
def test_find_tool_from_config_defaults_conversation_id_when_execution_id_omitted(
    mock_get_toolkit_config: MagicMock,
    mock_get_core_tools: MagicMock,
    mock_assistant,
    mock_user,
    mock_tool_config,
) -> None:
    """Backward compatibility: omitting execution_id keeps AssistantChatRequest's random-uuid default."""
    mock_get_toolkit_config.return_value = _toolkit_details()
    mock_get_core_tools.return_value = []

    captured_requests = []
    toolkits = {"AgentWorkspace": _capturing_toolkit_method(captured_requests)}

    ToolsService.find_tool_from_config(
        mock_tool_config,
        toolkits,
        mock_assistant,
        mock_user,
        "test-project",
    )

    assert len(captured_requests) == 1
    request = captured_requests[0]
    assert isinstance(request, AssistantChatRequest)
    assert request.conversation_id  # non-empty random uuid4
    assert request.conversation_id != "exec_123"


def _agent_workspace_toolkit_lambda():
    """Reproduces the exact lambda registered for AGENT_WORKSPACE_TOOLKIT in
    ToolkitService.get_toolkit_methods (toolkit_service.py:196-205), so this test
    exercises the real positional-argument contract between find_tool_from_config
    and the toolkit factory."""
    return lambda assistant, user, llm_model, request_uuid, request: (
        ToolkitSettingService.get_agent_workspace_toolkit(
            assistant,
            assistant.project,
            user,
            llm_model,
            request_uuid,
            request,
        )
    )


@patch("codemie.service.tools.toolkit_settings_service.AgentWorkspaceToolkit")
@patch("codemie.service.tools.ToolkitService.get_core_tools")
@patch("codemie.service.tools.tool_service.ToolsService.get_toolkit_from_workflow_tool_config")
def test_find_tool_from_config_sequential_bare_tool_states_share_conversation_id(
    mock_get_toolkit_config: MagicMock,
    mock_get_core_tools: MagicMock,
    mock_agent_workspace_toolkit: MagicMock,
    mock_assistant,
    mock_user,
) -> None:
    """
    Regression for EPMCDME-14138 acceptance criteria: write_workspace_file then
    execute_workspace_script as sequential bare tool states in the same workflow
    execution must resolve AgentWorkspace via the SAME conversation_id, not two
    different random UUIDs.
    """
    mock_get_core_tools.return_value = []  # force the toolkit-fallback branch for both calls

    def make_toolkit_instance(tool_name):
        instance = MagicMock()
        tool = MagicMock()
        tool.name = tool_name
        instance.get_tools.return_value = [tool]
        return instance

    mock_agent_workspace_toolkit.get_toolkit.side_effect = [
        make_toolkit_instance("write_workspace_file"),
        make_toolkit_instance("execute_workspace_script"),
    ]

    toolkits = {"AgentWorkspace": _agent_workspace_toolkit_lambda()}

    write_tool_config = MagicMock()
    write_tool_config.tool = "write_workspace_file"
    script_tool_config = MagicMock()
    script_tool_config.tool = "execute_workspace_script"

    mock_get_toolkit_config.side_effect = [
        _toolkit_details("AgentWorkspace"),
        _toolkit_details("AgentWorkspace"),
    ]

    # First bare tool state in the execution: write_workspace_file
    ToolsService.find_tool_from_config(
        write_tool_config,
        toolkits,
        mock_assistant,
        mock_user,
        "test-project",
        execution_id="exec_123",
    )
    # Second bare tool state in the SAME execution: execute_workspace_script
    ToolsService.find_tool_from_config(
        script_tool_config,
        toolkits,
        mock_assistant,
        mock_user,
        "test-project",
        execution_id="exec_123",
    )

    assert mock_agent_workspace_toolkit.get_toolkit.call_count == 2
    first_kwargs = mock_agent_workspace_toolkit.get_toolkit.call_args_list[0].kwargs
    second_kwargs = mock_agent_workspace_toolkit.get_toolkit.call_args_list[1].kwargs
    assert first_kwargs["conversation_id"] == "exec_123"
    assert second_kwargs["conversation_id"] == "exec_123"
    assert first_kwargs["conversation_id"] == second_kwargs["conversation_id"]
