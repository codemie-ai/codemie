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

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from codemie.agents.assistant_agent import AIToolsAgent
from codemie.agents.langgraph_agent import LangGraphAgent
from codemie.core.exceptions import MCPAuthenticationRequiredException


def _auth_required_exception() -> MCPAuthenticationRequiredException:
    return MCPAuthenticationRequiredException(
        {
            "error": "authentication_required",
            "servers": [
                {
                    "mcp_config_id": "mcp-config-1",
                    "mcp_config_name": "OneHub",
                    "auth_type": "oauth2",
                    "status": "authentication_required",
                    "error": "insufficient_scope",
                    "reason": "insufficient_scope",
                    "action": "reauthenticate",
                    "action_label": "Re-authenticate",
                    "recovery_flow_id": "rf-story-7-1",
                    "initiate_url": "/v1/mcp-auth/oauth2/initiate?recovery_flow_id=rf-story-7-1",
                }
            ],
        }
    )


def _assistant_agent() -> AIToolsAgent:
    agent = object.__new__(AIToolsAgent)
    agent.agent_name = "assistant"
    agent.assistant = None
    agent.conversation_id = "conversation-1"
    agent.request = SimpleNamespace(file_names=[], text="run", history=[])
    agent.user = SimpleNamespace(id="user-1", username="user@example.com")
    agent.thread_generator = MagicMock()
    agent.thread_context = {}
    agent.request_uuid = "request-1"
    agent.is_pure_chain = MagicMock(return_value=False)
    agent._get_inputs = MagicMock(return_value={})
    agent._persist_generated_workspace_files = MagicMock()
    agent._get_tool_errors = MagicMock(return_value=None)
    return agent


def _langgraph_agent() -> LangGraphAgent:
    agent = object.__new__(LangGraphAgent)
    agent.agent_name = "langgraph"
    agent.llm_model = "model-1"
    agent.request_uuid = "request-1"
    agent.conversation_id = "conversation-1"
    agent.assistant = None
    agent.user = SimpleNamespace(id="user-1", username="user@example.com")
    agent.request = SimpleNamespace(file_names=[], text="run", history=[])
    agent.thread_generator = MagicMock()
    agent.thread_context = {}
    agent._otel_context = None
    agent.tool_error_callback = MagicMock()
    agent.tool_error_callback.has_errors.return_value = False
    agent._get_inputs = MagicMock(return_value={})
    agent._persist_generated_workspace_files = MagicMock()
    return agent


@pytest.mark.parametrize("method_name", ["invoke", "invoke_task", "generate", "stream"])
def test_ai_tools_agent_propagates_mcp_auth_required_before_generic_error_handling(method_name: str) -> None:
    agent = _assistant_agent()
    auth_error = _auth_required_exception()

    if method_name == "stream":
        agent._agent_streaming = MagicMock(side_effect=auth_error)
    else:
        agent._invoke_agent = MagicMock(side_effect=auth_error)

    with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
        getattr(agent, method_name)()

    assert exc_info.value is auth_error
    if method_name == "stream":
        agent.thread_generator.close.assert_called_once_with(auth_error)
        agent.thread_generator.send.assert_not_called()


@pytest.mark.parametrize("method_name", ["invoke", "invoke_task", "generate", "stream"])
def test_langgraph_agent_propagates_mcp_auth_required_before_generic_error_handling(method_name: str) -> None:
    agent = _langgraph_agent()
    auth_error = _auth_required_exception()

    if method_name == "stream":
        agent._agent_streaming = MagicMock(side_effect=auth_error)
    else:
        agent._invoke_agent = MagicMock(side_effect=auth_error)

    with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
        getattr(agent, method_name)()

    assert exc_info.value is auth_error
    if method_name == "stream":
        agent.thread_generator.close.assert_called_once_with(auth_error)
        agent.thread_generator.send.assert_not_called()


def test_langgraph_invoke_agent_reraises_mcp_auth_required_from_stream_graph() -> None:
    agent = _langgraph_agent()
    auth_error = _auth_required_exception()
    agent._stream_graph = MagicMock(side_effect=auth_error)
    agent._get_run_config = MagicMock(return_value={})

    with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
        agent._invoke_agent({})

    assert exc_info.value is auth_error
