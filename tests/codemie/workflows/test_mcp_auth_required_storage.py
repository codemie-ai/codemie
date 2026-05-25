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

import json
from unittest.mock import MagicMock

from codemie.core.exceptions import MCPAuthenticationRequiredException
from codemie.core.workflow_models import WorkflowExecutionStatusEnum
from codemie.workflows.constants import END_NODE, NEXT_KEY
from codemie.workflows.nodes.base_node import BaseNode


class _InsufficientScopeWorkflowNode(BaseNode[dict[str, object]]):
    def execute(self, state_schema: type[dict[str, object]], execution_context: dict) -> object:
        raise MCPAuthenticationRequiredException(
            {
                "error": "authentication_required",
                "servers": [
                    {
                        "mcp_config_id": "mcp-config-1",
                        "mcp_config_name": "OneHub",
                        "auth_config_id": "auth-config-1",
                        "auth_type": "oauth2",
                        "as_hostname": "auth.example.com",
                        "status": "authentication_required",
                        "error": "insufficient_scope",
                        "reason": "insufficient_scope",
                        "action": "reauthenticate",
                        "action_label": "Re-authenticate",
                        "scope": "read write admin",
                        "required_scopes": ["read", "write", "admin"],
                        "requested_scopes": ["profile", "read", "write", "admin"],
                        "resource_metadata": "https://mcp.example.com/.well-known/oauth-protected-resource",
                        "resource_metadata_redacted": True,
                        "guidance": "OneHub requires additional permissions: read, write, admin",
                        "attempts_used": 0,
                        "attempts_remaining": 2,
                        "recovery_flow_id": "rf-workflow-story-7-1",
                    }
                ],
            }
        )

    def get_task(self, state_schema: type[dict[str, object]], *arg, **kwargs) -> str:
        return "invoke mcp tool"


class _PostAuth401WorkflowNode(BaseNode[dict[str, object]]):
    def execute(self, state_schema: type[dict[str, object]], execution_context: dict) -> object:
        raise MCPAuthenticationRequiredException(
            {
                "error": "authentication_required",
                "servers": [
                    {
                        "mcp_config_id": "mcp-config-1",
                        "mcp_config_name": "OneHub",
                        "auth_config_id": "auth-config-1",
                        "auth_type": "oauth2",
                        "status": "session_expired",
                        "error": "post_auth_401",
                        "reason": "unsupported_bearer_error",
                        "action": "reauthenticate",
                        "action_label": "Re-authenticate",
                        "error_context": {"bearer_error": "invalid_request"},
                    }
                ],
            }
        )

    def get_task(self, state_schema: type[dict[str, object]], *arg, **kwargs) -> str:
        return "invoke mcp tool"


def test_workflow_auth_required_storage_preserves_story_7_1_recovery_payload() -> None:
    workflow_execution_service = MagicMock()
    workflow_execution_service.start_state.return_value = "state-1"
    callback = MagicMock()
    node = _InsufficientScopeWorkflowNode(
        callbacks=[callback],
        workflow_execution_service=workflow_execution_service,
        thought_queue=MagicMock(),
        node_name="MCP Tool Node",
    )

    result = node({})

    workflow_execution_service.finish_state.assert_called_once()
    workflow_execution_service.mark_authentication_required.assert_called_once()
    finish_call = workflow_execution_service.finish_state.call_args.kwargs
    mark_call = workflow_execution_service.mark_authentication_required.call_args.kwargs
    assert finish_call["status"] == WorkflowExecutionStatusEnum.AUTHENTICATION_REQUIRED
    assert finish_call["output"] == mark_call["output"]

    stored_payload = json.loads(mark_call["output"])
    assert stored_payload["error"] == "authentication_required"
    assert stored_payload["node_name"] == "MCP Tool Node"
    server = stored_payload["servers"][0]
    assert server["status"] == "authentication_required"
    assert server["error"] == "insufficient_scope"
    assert server["recovery_flow_id"] == "rf-workflow-story-7-1"
    assert server["required_scopes"] == ["read", "write", "admin"]
    assert server["requested_scopes"] == ["profile", "read", "write", "admin"]
    assert "initiate_url" not in server
    callback.on_node_fail.assert_called_once()
    assert result == {NEXT_KEY: [END_NODE]}


def test_workflow_auth_required_storage_preserves_story_7_2_post_auth_401_payload() -> None:
    workflow_execution_service = MagicMock()
    workflow_execution_service.start_state.return_value = "state-1"
    callback = MagicMock()
    node = _PostAuth401WorkflowNode(
        callbacks=[callback],
        workflow_execution_service=workflow_execution_service,
        thought_queue=MagicMock(),
        node_name="MCP Tool Node",
    )

    result = node({})

    workflow_execution_service.mark_authentication_required.assert_called_once()
    stored_payload = json.loads(workflow_execution_service.mark_authentication_required.call_args.kwargs["output"])
    server = stored_payload["servers"][0]
    assert stored_payload["node_name"] == "MCP Tool Node"
    assert server["status"] == "session_expired"
    assert server["error"] == "post_auth_401"
    assert server["reason"] == "unsupported_bearer_error"
    assert server["action"] == "reauthenticate"
    assert server["error_context"] == {"bearer_error": "invalid_request"}
    assert result == {NEXT_KEY: [END_NODE]}
