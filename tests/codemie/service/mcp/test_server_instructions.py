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

"""MCP server instructions carried explicitly from tool loading to the prompt."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from codemie.service.mcp.server_instructions import (
    MCPServerInstructions,
    _delimiter_nonce,
    instructions_for_tools,
    make_server_instructions,
)

INSTRUCTIONS = "Always call the tool with sysparm_help=true before first use."


@pytest.fixture(autouse=True)
def _restore_mcp_singleton():
    """Tests here replace the MCPToolkitService singleton; put it back afterwards.

    Without this the mocked client leaks into unrelated tests under random
    ordering or xdist.
    """
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    saved_instance = getattr(MCPToolkitService, "_instance", None)
    saved_cache = getattr(MCPToolkitService, "_instances_cache", None)
    yield
    MCPToolkitService._instance = saved_instance
    if saved_cache is not None:
        MCPToolkitService._instances_cache = saved_cache


def _record(server_name: str, instructions: str) -> MCPServerInstructions:
    return MCPServerInstructions(server_name=server_name, instructions=instructions)


def _tool(name: str, server_name: str | None) -> SimpleNamespace:
    return SimpleNamespace(name=name, server_name=server_name)


def test_make_returns_a_record_for_non_blank_text():
    assert make_server_instructions("Support Portal", INSTRUCTIONS) == _record("Support Portal", INSTRUCTIONS)


@pytest.mark.parametrize("value", [None, "", "   ", 42, ["list"]])
def test_make_ignores_blank_and_non_string_instructions(value):
    assert make_server_instructions("a", value) is None


def test_instructions_are_kept_when_their_server_still_contributes_a_tool():
    entries = [_record("Support Portal", INSTRUCTIONS)]

    assert instructions_for_tools(entries, [_tool("echo", "Support Portal")]) == entries


def test_instructions_are_dropped_when_none_of_the_server_tools_survive():
    entries = [_record("Support Portal", INSTRUCTIONS)]

    assert instructions_for_tools(entries, [_tool("echo", "other-server")]) == []


def test_a_server_whose_only_tool_was_shadowed_by_a_later_server_is_dropped():
    first = _record("first", "use first")
    second = _record("second", "use second")
    final_tools = [_tool("search", "second")]

    assert instructions_for_tools([first, second], final_tools) == [second]


def test_a_server_keeps_its_block_while_any_of_its_tools_survive():
    entry = _record("first", "use first")
    final_tools = [_tool("fetch", "first")]

    assert instructions_for_tools([entry], final_tools) == [entry]


def test_core_tools_without_a_server_never_keep_a_block():
    entries = [_record("first", "use first")]

    assert instructions_for_tools(entries, [_tool("calculator", None)]) == []


def test_entries_keep_load_order():
    first = _record("first", "one")
    second = _record("second", "two")

    assert instructions_for_tools([first, second], [_tool("b", "second"), _tool("a", "first")]) == [first, second]


def test_delimiter_is_stable_for_a_conversation(monkeypatch):
    monkeypatch.setattr("codemie.service.mcp.server_instructions.config.MCP_INSTRUCTIONS_NONCE_SECRET", "s" * 32)

    assert _delimiter_nonce("conv-1") == _delimiter_nonce("conv-1")
    assert _delimiter_nonce("conv-1") != _delimiter_nonce("conv-2")


def test_short_configured_secret_is_rejected_by_config():
    from pydantic import ValidationError

    from codemie.configs.config import Config

    with pytest.raises(ValidationError, match="at least 32 characters"):
        Config(MCP_INSTRUCTIONS_NONCE_SECRET="short")


def test_empty_or_long_configured_secret_is_accepted_by_config():
    from codemie.configs.config import Config

    assert Config(MCP_INSTRUCTIONS_NONCE_SECRET="").MCP_INSTRUCTIONS_NONCE_SECRET == ""
    assert Config(MCP_INSTRUCTIONS_NONCE_SECRET="k" * 32).MCP_INSTRUCTIONS_NONCE_SECRET == "k" * 32


def test_tool_loading_reports_instructions_under_the_server_name():
    from codemie.rest_api.models.assistant import MCPServerDetails
    from codemie.service.mcp.client import MCPConnectClient
    from codemie.service.mcp.models import MCPListToolsResponse, MCPServerConfig, MCPToolDefinition
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    mock_client = MagicMock(spec=MCPConnectClient)
    mock_client.base_url = "http://mock-mcp-connect"
    mock_client.list_tools_with_instructions = AsyncMock(
        return_value=MCPListToolsResponse(
            tools=[
                MCPToolDefinition(name="echo", description="Echo", inputSchema={"type": "object", "properties": {}})
            ],
            instructions=INSTRUCTIONS,
        )
    )
    MCPToolkitService._instances_cache = {}
    MCPToolkitService._instance = None
    MCPToolkitService.init_singleton(mock_client)
    MCPToolkitService.get_instance().toolkit_factory._toolkit_cache.clear()

    mcp_server = MCPServerDetails(
        name="Support Portal",
        enabled=True,
        config=MCPServerConfig(command="uvx", args=["example-server"], env={}),
    )
    result = MCPToolkitService.get_mcp_server_tools_with_instructions([mcp_server])

    assert result.mcp_instructions == [_record("Support Portal", INSTRUCTIONS)]


def test_a_server_whose_tools_are_all_filtered_out_records_nothing():
    """No tools contributed, so no rules for tools the agent does not have."""
    from codemie.rest_api.models.assistant import MCPServerDetails
    from codemie.service.mcp.client import MCPConnectClient
    from codemie.service.mcp.models import MCPListToolsResponse, MCPServerConfig, MCPToolDefinition
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    mock_client = MagicMock(spec=MCPConnectClient)
    mock_client.base_url = "http://mock-mcp-connect"
    mock_client.list_tools_with_instructions = AsyncMock(
        return_value=MCPListToolsResponse(
            tools=[
                MCPToolDefinition(name="echo", description="Echo", inputSchema={"type": "object", "properties": {}})
            ],
            instructions=INSTRUCTIONS,
        )
    )
    MCPToolkitService._instances_cache = {}
    MCPToolkitService._instance = None
    MCPToolkitService.init_singleton(mock_client)
    MCPToolkitService.get_instance().toolkit_factory._toolkit_cache.clear()

    mcp_server = MCPServerDetails(
        name="Support Portal",
        enabled=True,
        tools=["renamed_away"],
        config=MCPServerConfig(command="uvx", args=["example-server"], env={}),
    )
    result = MCPToolkitService.get_mcp_server_tools_with_instructions([mcp_server])

    assert result.tools == []
    assert result.mcp_instructions == []


def test_loading_without_an_output_list_still_returns_tools():
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    assert MCPToolkitService.get_mcp_server_tools([]) == []


def test_get_tools_reports_nothing_when_mcp_loading_is_skipped():
    from codemie.core.models import AssistantChatRequest
    from codemie.rest_api.models.assistant import Assistant
    from codemie.rest_api.security.user import User
    from codemie.service.tools import toolkit_service as module
    from codemie.service.tools.toolkit_service import ToolkitService

    assistant = MagicMock(spec=Assistant)
    assistant.id = "a1"
    assistant.name = "A"
    assistant.project = "p"
    assistant.toolkits = []
    assistant.context = []
    assistant.mcp_servers = []
    assistant.assistant_ids = []
    assistant.skill_ids = []
    request = MagicMock(spec=AssistantChatRequest)
    request.text = "hi"
    request.conversation_id = "c1"
    request.file_name = None
    request.ide_installation_id = None
    request.tools_config = None
    request.enable_web_search = None
    request.enable_code_interpreter = None
    user = MagicMock(spec=User)
    user.id = "u1"
    user.name = "U"
    user.is_admin = False
    user.is_maintainer = False
    with (
        patch.object(ToolkitService, "add_tools_with_creds", return_value=[]),
        patch.object(ToolkitService, "add_context_tools", return_value=[]),
        patch.object(ToolkitService, "add_file_tools", return_value=[]),
        patch.object(module, "create_skill_tool_if_needed", return_value=None),
        patch.object(module, "create_skill_companion_file_tool_if_needed", return_value=None),
        patch.object(module.config, "MCP_CONNECT_ENABLED", False),
    ):
        result = ToolkitService.collect_agent_tools(assistant, request, user, "test-model", "uuid")

    assert result.mcp_instructions == []


def _init_mock_mcp_service(list_tools_response):
    from codemie.service.mcp.client import MCPConnectClient
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    mock_client = MagicMock(spec=MCPConnectClient)
    mock_client.base_url = "http://mock-mcp-connect"
    mock_client.list_tools_with_instructions = AsyncMock(return_value=list_tools_response)
    MCPToolkitService._instances_cache = {}
    MCPToolkitService._instance = None
    MCPToolkitService.init_singleton(mock_client)
    MCPToolkitService.get_instance().toolkit_factory._toolkit_cache.clear()


def _echo_response():
    from codemie.service.mcp.models import MCPListToolsResponse, MCPToolDefinition

    return MCPListToolsResponse(
        tools=[MCPToolDefinition(name="echo", description="Echo", inputSchema={"type": "object", "properties": {}})],
        instructions=INSTRUCTIONS,
    )


def _server_named(name: str):
    from codemie.rest_api.models.assistant import MCPServerDetails
    from codemie.service.mcp.models import MCPServerConfig

    return MCPServerDetails(
        name=name,
        enabled=True,
        config=MCPServerConfig(command="uvx", args=["example-server"], env={}),
    )


def test_loaded_tools_carry_the_configured_server_name():
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    _init_mock_mcp_service(_echo_response())

    result = MCPToolkitService.get_mcp_server_tools_with_instructions([_server_named("Support Portal")])

    assert [tool.server_name for tool in result.tools] == ["Support Portal"]


def test_one_cached_toolkit_serves_two_server_names_without_mixing_them():
    """The toolkit cache ignores the server name, so the name must be stamped per load."""
    from codemie.service.mcp.toolkit_service import MCPToolkitService

    _init_mock_mcp_service(_echo_response())

    first = MCPToolkitService.get_mcp_server_tools_with_instructions([_server_named("Portal A")])
    second = MCPToolkitService.get_mcp_server_tools_with_instructions([_server_named("Portal B")])

    assert [tool.server_name for tool in first.tools] == ["Portal A"]
    assert [tool.server_name for tool in second.tools] == ["Portal B"]
    assert [tool.name for tool in first.tools] == [tool.name for tool in second.tools]
