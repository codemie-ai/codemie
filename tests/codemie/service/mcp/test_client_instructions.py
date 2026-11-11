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

"""The bridge now returns server-side instructions on tools/list."""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.mcp.client import MCPConnectClient
from codemie.service.mcp.models import MCPServerConfig

INSTRUCTIONS = "Always call the tool with sysparm_help=true before first use."


@pytest.fixture
def server_config() -> MCPServerConfig:
    return MCPServerConfig(command="uvx", args=["some-server"], env={})


@contextmanager
def _client_returning(payload: dict):
    """Patch the HTTP client for the duration of the block.

    A context manager rather than a bare patcher.start(): a failure before stop()
    would otherwise leave httpx.AsyncClient patched for the rest of the session.
    """
    mock_response = MagicMock()
    mock_response.json.return_value = payload
    mock_response.raise_for_status = MagicMock()
    with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)
        yield


@pytest.mark.asyncio
async def test_list_tools_with_instructions_parses_the_field(server_config):
    with _client_returning({"tools": [], "instructions": INSTRUCTIONS}):
        response = await MCPConnectClient().list_tools_with_instructions(server_config)

    assert response.instructions == INSTRUCTIONS
    assert response.tools == []


@pytest.mark.asyncio
async def test_instructions_are_none_when_the_bridge_omits_them(server_config):
    with _client_returning({"tools": []}):
        response = await MCPConnectClient().list_tools_with_instructions(server_config)

    assert response.instructions is None


@pytest.mark.asyncio
async def test_list_tools_still_returns_only_the_tool_list(server_config):
    """The existing signature has 14 call sites; it must not change."""
    with _client_returning(
        {
            "tools": [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object"}}],
            "instructions": INSTRUCTIONS,
        }
    ):
        tools = await MCPConnectClient().list_tools(server_config)

    assert [tool.name for tool in tools] == ["echo"]
