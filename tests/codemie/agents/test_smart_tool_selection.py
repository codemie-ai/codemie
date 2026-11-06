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

"""Tests for smart tool selection functionality.

This module tests the complete smart tool selection implementation including:
- Tool registry and state management
- Tool selector with ToolkitLookupService integration
- Smart ReAct agent wrapper
- Integration with LangGraphAgent
"""

import pytest
from unittest.mock import Mock, patch
from pydantic import BaseModel

from langchain_core.tools import tool

from codemie.agents.smart_tool_state import ToolRegistry, add_tool_ids
from codemie.agents.smart_tool_selector import SmartToolSelector
from codemie.agents.smart_react_agent import create_smart_react_agent


# Test tools
@tool
def search_code(query: str) -> str:
    """Search through code files."""
    return f"Found code matching: {query}"


@tool
def read_file(path: str) -> str:
    """Read a file from filesystem."""
    return f"Content of {path}"


@tool
def list_files(directory: str) -> str:
    """List files in a directory."""
    return f"Files in {directory}"


@tool
def get_weather(city: str) -> str:
    """Get weather for a city."""
    return f"Weather in {city}: 75°F, sunny"


@tool
def calculate(expression: str) -> str:
    """Calculate a mathematical expression."""
    # Safe calculation for test purposes - only supports basic operations
    try:
        # Only allow digits, basic operators, parentheses, and whitespace
        import re

        if re.match(r'^[\d\s+\-*/().]+$', expression):
            # Using a simple calculator for test purposes
            result = str(float(eval(expression)))  # noqa: S307
            return f"Result: {result}"
        return "Error: Invalid expression"
    except Exception:
        return "Error: Cannot calculate"


@tool
def search_web(query: str) -> str:
    """Search the web."""
    return f"Web results for: {query}"


class WeatherResponse(BaseModel):
    """Structured response for weather queries."""

    temperature: float
    conditions: str


class TestToolRegistry:
    """Tests for ToolRegistry class."""

    def test_tool_registry_initialization(self):
        """Test that tool registry initializes correctly."""
        tools = [search_code, read_file, list_files]
        registry = ToolRegistry(tools)

        assert len(registry) == 3
        assert len(registry.registry) == 3
        assert len(registry.name_to_id) == 3
        assert len(registry.id_to_name) == 3

    def test_get_tool_by_id(self):
        """Test retrieving tool by ID."""
        tools = [search_code, read_file]
        registry = ToolRegistry(tools)

        tool_id = registry.get_tool_id("search_code")
        assert tool_id is not None

        tool = registry.get_tool_by_id(tool_id)
        assert tool is not None
        assert tool.name == "search_code"

    def test_get_tools_by_ids(self):
        """Test retrieving multiple tools by IDs."""
        tools = [search_code, read_file, list_files]
        registry = ToolRegistry(tools)

        all_ids = registry.get_all_tool_ids()
        retrieved_tools = registry.get_tools_by_ids(all_ids[:2])

        assert len(retrieved_tools) == 2
        assert all(isinstance(t, type(search_code)) for t in retrieved_tools)

    def test_get_tool_id_by_name(self):
        """Test getting tool ID by name."""
        tools = [search_code]
        registry = ToolRegistry(tools)

        tool_id = registry.get_tool_id("search_code")
        assert tool_id is not None
        assert "search_code" in tool_id

    def test_get_tool_name_by_id(self):
        """Test getting tool name by ID."""
        tools = [read_file]
        registry = ToolRegistry(tools)

        tool_id = registry.get_tool_id("read_file")
        tool_name = registry.get_tool_name(tool_id)

        assert tool_name == "read_file"

    def test_contains_check(self):
        """Test __contains__ method."""
        tools = [search_code]
        registry = ToolRegistry(tools)

        tool_id = registry.get_tool_id("search_code")
        assert tool_id in registry
        assert "invalid_id" not in registry


class TestAddToolIdsReducer:
    """Tests for the add_tool_ids reducer function."""

    def test_add_tool_ids_no_duplicates(self):
        """Test that reducer prevents duplicates."""
        left = ["tool1", "tool2"]
        right = ["tool2", "tool3"]

        result = add_tool_ids(left, right)

        assert result == ["tool1", "tool2", "tool3"]
        assert len(result) == 3

    def test_add_tool_ids_empty_left(self):
        """Test with empty left list."""
        left = []
        right = ["tool1", "tool2"]

        result = add_tool_ids(left, right)

        assert result == ["tool1", "tool2"]

    def test_add_tool_ids_empty_right(self):
        """Test with empty right list."""
        left = ["tool1", "tool2"]
        right = []

        result = add_tool_ids(left, right)

        assert result == ["tool1", "tool2"]


class TestSmartToolSelector:
    """Tests for SmartToolSelector class."""

    def test_selector_initialization(self):
        """Test selector initializes correctly."""
        tools = [search_code, read_file, list_files]
        registry = ToolRegistry(tools)

        selector = SmartToolSelector(tool_registry=registry.registry, default_limit=3)

        assert selector.default_limit == 3
        assert len(selector.tool_registry) == 3
        assert len(selector.name_to_id) == 3

    @patch('codemie.agents.smart_tool_selector.ToolkitLookupService.get_tools_by_query')
    def test_select_tools_with_query(self, mock_get_tools):
        """Test tool selection with query."""
        from codemie_tools.base.models import Tool, ToolKit

        # Mock ToolkitLookupService response
        mock_toolkit = ToolKit(
            toolkit="code",
            tools=[
                Tool(name="search_code", description="Search code"),
                Tool(name="read_file", description="Read file"),
            ],
        )
        mock_get_tools.return_value = [mock_toolkit]

        # Create registry and selector
        tools = [search_code, read_file, list_files]
        registry = ToolRegistry(tools)
        selector = SmartToolSelector(registry.registry, default_limit=2)

        # Select tools
        tool_ids, tool_instances = selector.select_tools(query="search through code", limit=2)

        # Verify
        assert len(tool_instances) <= 2
        assert mock_get_tools.called
        # Verify tool_names_filter was passed with available tool names
        call_kwargs = mock_get_tools.call_args.kwargs
        assert 'tool_names_filter' in call_kwargs
        assert set(call_kwargs['tool_names_filter']) == {'search_code', 'read_file', 'list_files'}
        assert all(isinstance(t, type(search_code)) for t in tool_instances)

    def test_get_default_tools(self):
        """Test getting default tools."""
        tools = [search_code, read_file, list_files]
        registry = ToolRegistry(tools)
        selector = SmartToolSelector(registry.registry, default_limit=3)

        tool_ids, tool_instances = selector.get_default_tools(count=2)

        assert len(tool_ids) == 2
        assert len(tool_instances) == 2

    def test_build_search_query_with_history(self):
        """Test building context-aware search query."""
        from langchain_core.messages import HumanMessage, AIMessage

        tools = [search_code]
        registry = ToolRegistry(tools)
        selector = SmartToolSelector(registry.registry)

        history = [HumanMessage(content="I'm working on a Python project"), AIMessage(content="Great! How can I help?")]

        enhanced_query = selector._build_search_query(query="what files are there?", history=history)

        assert "what files are there?" in enhanced_query
        assert "Context:" in enhanced_query
        assert "Python project" in enhanced_query

    def test_extract_tool_names(self):
        """Test extracting tool names from toolkits."""
        from codemie_tools.base.models import Tool, ToolKit

        tools = [search_code, read_file]
        registry = ToolRegistry(tools)
        selector = SmartToolSelector(registry.registry)

        mock_toolkits = [
            ToolKit(
                toolkit="code",
                tools=[Tool(name="search_code", description="Search"), Tool(name="read_file", description="Read")],
            )
        ]

        tool_names = selector._extract_tool_names(mock_toolkits, limit=2)

        assert len(tool_names) == 2
        assert "search_code" in tool_names
        assert "read_file" in tool_names

    @patch('codemie.agents.smart_tool_selector.ToolkitLookupService.get_tools_by_query')
    def test_select_tools_filters_by_available_tools(self, mock_get_tools):
        """Test that tool selection filters by assistant's available tools."""
        from codemie_tools.base.models import Tool, ToolKit

        # Create registry with only search_code and read_file
        tools = [search_code, read_file]  # NOT including list_files
        registry = ToolRegistry(tools)
        selector = SmartToolSelector(registry.registry, default_limit=3)

        # Mock returns list_files which is NOT in registry
        mock_toolkit = ToolKit(
            toolkit="code",
            tools=[
                Tool(name="search_code", description="Search code"),
                Tool(name="list_files", description="List files"),  # NOT in registry
            ],
        )
        mock_get_tools.return_value = [mock_toolkit]

        # Select tools
        tool_ids, tool_instances = selector.select_tools(query="list code files", limit=3)

        # Verify that filter was passed with ONLY available tools
        call_kwargs = mock_get_tools.call_args.kwargs
        assert 'tool_names_filter' in call_kwargs
        # Should only include tools in registry
        assert set(call_kwargs['tool_names_filter']) == {'search_code', 'read_file'}
        # Should NOT include list_files
        assert 'list_files' not in call_kwargs['tool_names_filter']

        # Verify that only tools from registry are returned
        # (list_files from mock should be filtered out in conversion)
        assert len(tool_instances) == 1  # Only search_code
        assert all(t.name in ['search_code', 'read_file'] for t in tool_instances)


class TestSmartReactAgent:
    """Tests for create_smart_react_agent function."""

    @patch('codemie.agents.smart_react_agent.config')
    @patch('codemie.agents.smart_react_agent.create_react_agent')
    def test_fallback_to_standard_agent(self, mock_create_react, mock_config):
        """Test that standard agent is used when below threshold."""
        mock_config.TOOL_SELECTION_THRESHOLD = 10

        from langchain_openai import ChatOpenAI

        model = Mock(spec=ChatOpenAI)

        tools = [search_code, read_file]  # Only 2 tools, below threshold

        create_smart_react_agent(model=model, tools=tools, tool_selection_enabled=True, tool_selection_limit=3)

        # Should call standard create_react_agent
        assert mock_create_react.called

    @patch('codemie.agents.smart_react_agent.config')
    def test_smart_agent_with_structured_output(self, mock_config):
        """Test that smart agent preserves structured output capability."""
        mock_config.TOOL_SELECTION_THRESHOLD = 2

        from langchain_openai import ChatOpenAI

        model = Mock(spec=ChatOpenAI)

        tools = [get_weather, calculate, search_web]  # 3 tools, above threshold

        # Create agent with structured output
        agent = create_smart_react_agent(
            model=model,
            tools=tools,
            response_format=WeatherResponse,  # Structured output
            tool_selection_enabled=True,
            tool_selection_limit=2,
        )

        # Verify agent is created (graph compiled)
        assert agent is not None

    @patch('codemie.agents.smart_react_agent.config')
    def test_smart_agent_initialization_logging(self, mock_config):
        """Test that initialization logs appropriate messages."""
        mock_config.TOOL_SELECTION_THRESHOLD = 2

        from langchain_openai import ChatOpenAI

        model = Mock(spec=ChatOpenAI)

        tools = [search_code, read_file, list_files, get_weather, calculate]

        with patch('codemie.agents.smart_react_agent.logger') as mock_logger:
            create_smart_react_agent(model=model, tools=tools, tool_selection_enabled=True, tool_selection_limit=3)

            # Verify logging was called
            assert mock_logger.info.called


class TestLangGraphAgentIntegration:
    """Tests for LangGraphAgent integration with smart tool selection."""

    @patch('codemie.agents.langgraph_agent.config')
    def test_smart_tools_enabled_in_init(self, mock_config):
        """Test that smart tools can be enabled via __init__."""
        mock_config.TOOL_SELECTION_ENABLED = True
        mock_config.TOOL_SELECTION_LIMIT = 3
        mock_config.TOOL_SELECTION_THRESHOLD = 2

        # Would need to create a full LangGraphAgent instance with mocks
        # This is a placeholder for integration testing
        pass

    @patch('codemie.agents.langgraph_agent.config')
    @patch('codemie.agents.langgraph_agent.create_smart_react_agent')
    def test_init_agent_uses_smart_selection(self, mock_smart_agent, mock_config):
        """Test that init_agent uses smart selection when conditions are met."""
        mock_config.TOOL_SELECTION_ENABLED = True
        mock_config.TOOL_SELECTION_THRESHOLD = 2

        # This would require full LangGraphAgent setup
        # Placeholder for integration test
        pass


@pytest.mark.asyncio
class TestEndToEndSmartToolSelection:
    """End-to-end integration tests."""

    @patch('codemie.agents.smart_tool_selector.ToolkitLookupService.get_tools_by_query')
    @patch('codemie.agents.smart_react_agent.config')
    async def test_full_flow_with_tool_selection(self, mock_config, mock_get_tools):
        """Test complete flow from query to tool selection to execution."""
        from codemie_tools.base.models import Tool, ToolKit

        mock_config.TOOL_SELECTION_THRESHOLD = 2

        # Mock tool search results
        mock_toolkit = ToolKit(toolkit="code", tools=[Tool(name="search_code", description="Search code")])
        mock_get_tools.return_value = [mock_toolkit]

        # Create agent with many tools
        from langchain_openai import ChatOpenAI

        model = Mock(spec=ChatOpenAI)
        tools = [search_code, read_file, list_files, get_weather, calculate, search_web]

        agent = create_smart_react_agent(model=model, tools=tools, tool_selection_enabled=True, tool_selection_limit=2)

        assert agent is not None
        # Further testing would require mocking the full LangGraph execution


class TestDeterministicToolIdsAndHygiene:
    """Task 3 of EPMCDME-16463: ids survive a rebuild; no silent fallbacks; unique graph name."""

    def test_two_registries_from_same_tools_have_equal_ids(self):
        tools = [search_code, read_file, list_files]

        first = ToolRegistry(tools)
        second = ToolRegistry(tools)

        assert first.name_to_id == second.name_to_id
        assert set(first.registry) == set(second.registry)
        assert first.name_to_id["search_code"] == "search_code_"

    def test_toolkit_is_part_of_the_id(self):
        tool_obj = Mock()
        tool_obj.name = "t"
        tool_obj.metadata = {"toolkit": "git"}
        tool_obj.toolkit = "git"

        assert ToolRegistry([tool_obj]).name_to_id["t"] == "t_git"

    def test_two_tools_with_one_name_and_no_toolkit_both_stay_with_distinct_stable_ids(self):
        """AR-16: same-named tools without a toolkit (e.g. from two MCP servers) both stay selectable."""

        def same_name(description: str):
            tool_obj = Mock()
            tool_obj.name = "fetch"
            tool_obj.description = description
            tool_obj.metadata = {}
            tool_obj.toolkit = None
            return tool_obj

        tools = [same_name("first"), same_name("second")]

        first, second = ToolRegistry(tools), ToolRegistry(tools)

        assert len(first.registry) == 2
        assert [tool_obj.description for tool_obj in first.registry.values()] == ["first", "second"]
        assert list(first.registry) == list(second.registry)
        assert first.get_tools_by_ids(list(first.registry)) == tools

    def test_a_resumed_run_drops_a_selected_id_that_no_longer_resolves_as_a_fresh_run_does(self):
        from codemie.agents import smart_react_agent as module
        from codemie.agents.tool_confirmation.confirmation_gate import DECISIONS_CONFIG_KEY

        registry = ToolRegistry([search_code, read_file])
        workflow = module._build_smart_react_workflow(
            model=Mock(),
            tools=[search_code, read_file],
            tool_registry_obj=registry,
            tool_selector=Mock(),
            tool_selection_limit=2,
            prompt=None,
            response_format=None,
            name="a",
            parallel_tool_calls=None,
        )
        sub_agent = Mock()
        sub_agent.invoke.return_value = {"messages": []}
        resumed = {"configurable": {DECISIONS_CONFIG_KEY: {}}}
        with patch.object(module, "_create_sub_agent", return_value=sub_agent) as create:
            workflow.nodes["agent"].runnable.invoke({"selected_tool_ids": ["search_code_", "gone_"]}, resumed)

        assert create.call_args.kwargs["available_tools"] == [search_code]

    def test_an_ordinary_turn_keeps_the_resolved_tools_or_falls_back_to_all(self):
        from codemie.agents.smart_react_agent import _resolve_available_tools

        registry = ToolRegistry([search_code, read_file])
        selected = registry.name_to_id["search_code"]

        assert _resolve_available_tools([selected, "gone_"], [search_code, read_file], registry) == [search_code]
        assert _resolve_available_tools(["gone_"], [search_code, read_file], registry) == [search_code, read_file]

    def test_selected_ids_resolve_against_rebuilt_registry(self):
        from codemie.agents.smart_react_agent import _resolve_available_tools

        selected = ToolRegistry([search_code, read_file]).name_to_id["read_file"]
        rebuilt = ToolRegistry([search_code, read_file])

        assert _resolve_available_tools([selected], [search_code, read_file], rebuilt) == [read_file]

    @patch('codemie.agents.smart_react_agent.config')
    def test_smart_graph_gets_agent_name(self, mock_config):
        mock_config.TOOL_SELECTION_THRESHOLD = 2
        from langchain_openai import ChatOpenAI

        graph = create_smart_react_agent(
            model=Mock(spec=ChatOpenAI),
            tools=[search_code, read_file, list_files],
            name="my-unique-agent",
            tool_selection_enabled=True,
        )

        assert graph.name == "my-unique-agent"

    def test_agent_node_reraises_graph_interrupt_without_error_log(self):
        from langgraph.errors import GraphInterrupt
        from codemie.agents import smart_react_agent as module

        sub_agent = Mock()
        sub_agent.invoke.side_effect = GraphInterrupt()
        registry = ToolRegistry([search_code, read_file])
        with (
            patch.object(module, "_create_sub_agent", return_value=sub_agent),
            patch.object(module.logger, "error") as log_error,
            pytest.raises(GraphInterrupt),
        ):
            module._execute_agent_with_selected_tools(
                state={"selected_tool_ids": ["search_code_"]},
                model=Mock(),
                all_tools=[search_code, read_file],
                tool_registry_obj=registry,
                prompt=None,
                response_format=None,
                name="a",
                parallel_tool_calls=None,
            )

        log_error.assert_not_called()
