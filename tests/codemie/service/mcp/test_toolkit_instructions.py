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

"""The toolkit carries the server's handshake instructions."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from codemie.service.mcp.models import MCPListToolsResponse, MCPServerConfig
from codemie.service.mcp.client import MCPConnectClient
from codemie.service.mcp.toolkit import MCPToolkitFactory

INSTRUCTIONS = "Always call the tool with sysparm_help=true before first use."


@pytest.fixture
def server_config() -> MCPServerConfig:
    return MCPServerConfig(command="uvx", args=["some-server"], env={})


def _factory_returning(instructions: str | None) -> MCPToolkitFactory:
    client = MagicMock(spec=MCPConnectClient)
    client.list_tools_with_instructions = AsyncMock(
        return_value=MCPListToolsResponse(tools=[], instructions=instructions)
    )
    return MCPToolkitFactory(mcp_client=client)


@pytest.mark.asyncio
async def test_toolkit_carries_the_instructions(server_config):
    toolkit = await _factory_returning(INSTRUCTIONS).create_toolkit(server_config, use_cache=False)

    assert toolkit.instructions == INSTRUCTIONS


@pytest.mark.asyncio
async def test_instructions_are_none_when_the_server_declares_none(server_config):
    toolkit = await _factory_returning(None).create_toolkit(server_config, use_cache=False)

    assert toolkit.instructions is None


@pytest.mark.asyncio
async def test_a_cached_toolkit_still_carries_the_instructions(server_config):
    """AC: subsequent calls in the same session reuse what the handshake produced."""
    factory = _factory_returning(INSTRUCTIONS)

    first = await factory.create_toolkit(server_config, use_cache=True)
    second = await factory.create_toolkit(server_config, use_cache=True)

    assert second is first
    assert second.instructions == INSTRUCTIONS
    assert factory.mcp_client.list_tools_with_instructions.await_count == 1
