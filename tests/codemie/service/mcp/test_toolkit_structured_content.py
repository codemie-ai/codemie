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
Tests for MCP tool responses that carry data only in structuredContent (e.g. Airtable MCP).
"""

import json
from unittest.mock import MagicMock

import pytest
from pydantic import create_model

from codemie.service.mcp.client import MCPConnectClient
from codemie.service.mcp.models import MCPServerConfig, MCPToolContentItem, MCPToolInvocationResponse
from codemie.service.mcp.toolkit import MCPTool, _convert_mcp_response_to_tool_message
from codemie_tools.base.artifact_publishing import PublishContext

AIRTABLE_LIST_BASES_RESPONSE = {
    "content": [],
    "structuredContent": {
        "bases": [
            {"id": "appBWLGhkPa1EB0ge", "name": "Backoffice Apps", "permissionLevel": "comment"},
        ]
    },
    "isError": False,
}


@pytest.fixture
def tool():
    return MCPTool(
        name="list_bases",
        description="List Airtable bases",
        mcp_client=MagicMock(spec=MCPConnectClient),
        mcp_server_config=MCPServerConfig(url="https://mcp.airtable.com/mcp", type="streamable-http"),
        args_schema=create_model("ListBasesArgs"),
    )


def test_structured_content_is_parsed():
    response = MCPToolInvocationResponse.model_validate(AIRTABLE_LIST_BASES_RESPONSE)

    assert response.structuredContent == AIRTABLE_LIST_BASES_RESPONSE["structuredContent"]


def test_structured_content_defaults_to_none():
    response = MCPToolInvocationResponse.model_validate({"content": [], "isError": False})

    assert response.structuredContent is None
    assert response.structured_content_text() is None


def test_structured_content_only_is_passed_to_llm(tool):
    response = MCPToolInvocationResponse.model_validate(AIRTABLE_LIST_BASES_RESPONSE)

    result = tool._post_process_output_content(response)

    assert json.loads(result) == AIRTABLE_LIST_BASES_RESPONSE["structuredContent"]


_CTX = PublishContext(user_id="user-1", conversation_id="conv-1", turn_id="turn-1")


def test_text_content_takes_precedence_over_structured_content():
    response = MCPToolInvocationResponse(
        content=[MCPToolContentItem(type="text", text='{"bases": []}')],
        structuredContent={"bases": []},
    )

    assert _convert_mcp_response_to_tool_message(response, _CTX) == '{"bases": []}'


def test_token_limit_counts_structured_content(tool):
    tool.tokens_size_limit = 5
    tool.throw_truncated_error = False
    response = MCPToolInvocationResponse.model_validate(AIRTABLE_LIST_BASES_RESPONSE)

    output, token_count = tool._limit_output_content(response)

    assert token_count > 5
    assert output.startswith(tool.truncate_message)
