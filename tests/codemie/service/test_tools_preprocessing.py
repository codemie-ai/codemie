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
Tests for tools preprocessing module.

This module tests the functionality of tools preprocessors and their factory.
"""

import textwrap
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools import BaseTool, Tool


from codemie.service.llm_service.llm_service import LLMService
from codemie.service.tools.tools_preprocessing import (
    DescriptionPreprocessor,
    GPT4ToolsPreprocessor,
    ToolsPreprocessorFactory,
    ToolsPreprocessor,
)


class TestDescriptionPreprocessor:
    """Tests for DescriptionPreprocessor."""

    def test_process_empty_tools_list(self):
        """Test processing an empty list of tools."""
        preprocessor = DescriptionPreprocessor()
        result = preprocessor.process([])
        assert result == []

    def test_process_no_description(self):
        """Test processing a tool with no description."""
        tool = Tool(name="test_tool", description="", func=lambda x: x)

        preprocessor = DescriptionPreprocessor()
        result = preprocessor.process([tool])

        assert len(result) == 1
        assert result[0].description == ""

    def test_process_whitespace_only_description(self):
        """Test processing a tool with whitespace-only description."""
        tool = Tool(name="test_tool", description="   \n  \t  ", func=lambda x: x)

        preprocessor = DescriptionPreprocessor()
        result = preprocessor.process([tool])

        assert len(result) == 1
        assert result[0].description == ""

    def test_process_common_indentation_removal(self):
        """Test removal of common indentation from description."""
        description = textwrap.dedent("""
            This is a test description.
              This line has additional indentation.
            This line has normal indentation.
        """)
        tool = Tool(name="test_tool", description=description, func=lambda x: x)

        preprocessor = DescriptionPreprocessor()
        result = preprocessor.process([tool])

        assert len(result) == 1
        assert (
            result[0].description
            == "This is a test description.\n  This line has additional indentation.\nThis line has normal indentation."
        )

    def test_process_multiple_tools(self):
        """Test processing multiple tools simultaneously."""
        tool1 = Tool(name="tool1", description="  \n  Tool 1 description\n  ", func=lambda x: x)

        tool2 = Tool(
            name="tool2",
            description=textwrap.dedent("""
                Tool 2 description
                with multiple lines
            """),
            func=lambda x: x,
        )

        preprocessor = DescriptionPreprocessor()
        result = preprocessor.process([tool1, tool2])

        assert len(result) == 2
        assert result[0].description == "Tool 1 description"
        assert result[1].description == "Tool 2 description\nwith multiple lines"


class TestGPT4ToolsPreprocessor:
    """Tests for GPT4ToolsPreprocessor."""

    def test_process_empty_tools_list(self):
        """Test processing an empty list of tools."""
        preprocessor = GPT4ToolsPreprocessor()
        result = preprocessor.process([])
        assert result == []

    def test_process_no_truncation_needed(self):
        """Test processing a tool with short description that doesn't need truncation."""
        tool = Tool(name="test_tool", description="Short description", func=lambda x: x)

        preprocessor = GPT4ToolsPreprocessor()
        result = preprocessor.process([tool])

        assert len(result) == 1
        assert result[0].description == "Short description"

    def test_process_truncation_required(self):
        """Test truncation of a tool with description longer than max length."""
        # Create a description longer than MAX_DESCRIPTION_LENGTH
        long_description = "A" * (GPT4ToolsPreprocessor.MAX_DESCRIPTION_LENGTH + 100)
        tool = Tool(name="test_tool", description=long_description, func=lambda x: x)

        preprocessor = GPT4ToolsPreprocessor()
        result = preprocessor.process([tool])

        assert len(result) == 1
        assert len(result[0].description) == GPT4ToolsPreprocessor.MAX_DESCRIPTION_LENGTH
        assert result[0].description == "A" * GPT4ToolsPreprocessor.MAX_DESCRIPTION_LENGTH

    def test_process_multiple_tools_with_mixed_lengths(self):
        """Test processing multiple tools with mixed description lengths."""
        tool1 = Tool(name="tool1", description="Short description", func=lambda x: x)

        long_description = "B" * (GPT4ToolsPreprocessor.MAX_DESCRIPTION_LENGTH + 200)
        tool2 = Tool(name="tool2", description=long_description, func=lambda x: x)

        preprocessor = GPT4ToolsPreprocessor()
        result = preprocessor.process([tool1, tool2])

        assert len(result) == 2
        assert result[0].description == "Short description"  # Not truncated
        assert len(result[1].description) == GPT4ToolsPreprocessor.MAX_DESCRIPTION_LENGTH  # Truncated
        assert result[1].description == "B" * GPT4ToolsPreprocessor.MAX_DESCRIPTION_LENGTH

    def test_process_empty_description(self):
        """Test processing a tool with empty description."""
        tool = Tool(name="test_tool", description="", func=lambda x: x)

        preprocessor = GPT4ToolsPreprocessor()
        result = preprocessor.process([tool])

        assert len(result) == 1
        assert result[0].description == ""


class TestToolsPreprocessorFactory:
    """Tests for ToolsPreprocessorFactory."""

    def test_create_default_preprocessor_chain(self):
        """Test creation of default preprocessor chain for unknown models."""
        chain = ToolsPreprocessorFactory.create_preprocessor_chain("unknown-model")
        assert len(chain) == 1
        assert isinstance(chain[0], DescriptionPreprocessor)

    def test_create_gpt4_preprocessor_chain(self):
        """Test creation of GPT-4 specific preprocessor chain."""
        models = ["gpt-4", "gpt-4-turbo", "gpt-4-1106-preview", "GPT-4"]

        for model in models:
            chain = ToolsPreprocessorFactory.create_preprocessor_chain(model)
            assert len(chain) == 2
            assert isinstance(chain[0], DescriptionPreprocessor)
            assert isinstance(chain[1], GPT4ToolsPreprocessor)

    def test_cache_reuse(self):
        """Test that the factory reuses cached preprocessor chains."""
        # Clear any existing cache
        ToolsPreprocessorFactory._preprocessor_cache = {}

        # First call should create a new chain
        ToolsPreprocessorFactory.create_preprocessor_chain(LLMService.BASE_NAME_GPT_41)

        # Create a mock to track if a new chain is created
        with patch.object(
            ToolsPreprocessorFactory,
            '_preprocessor_cache',
            {LLMService.BASE_NAME_GPT_41: [MagicMock(spec=ToolsPreprocessor)]},
        ):
            # Second call should use the cached chain
            chain2 = ToolsPreprocessorFactory.create_preprocessor_chain(LLMService.BASE_NAME_GPT_41)

            # Verify it's the same chain from the cache
            assert chain2 == ToolsPreprocessorFactory._preprocessor_cache[LLMService.BASE_NAME_GPT_41]

    def test_different_models_get_different_chains(self):
        """Test that different models get different preprocessor chains."""
        # Clear any existing cache
        ToolsPreprocessorFactory._preprocessor_cache = {}

        chain1 = ToolsPreprocessorFactory.create_preprocessor_chain(LLMService.BASE_NAME_GPT_41)
        chain2 = ToolsPreprocessorFactory.create_preprocessor_chain("gpt-3.5-turbo")

        assert len(chain1) != len(chain2)
        assert ToolsPreprocessorFactory._preprocessor_cache[LLMService.BASE_NAME_GPT_41] == chain1
        assert ToolsPreprocessorFactory._preprocessor_cache["gpt-3.5-turbo"] == chain2


@pytest.fixture
def mock_tools():
    """Fixture to create a list of mock tools for testing."""
    tool1 = Tool(name="tool1", description="   \n  Tool 1 description\n  ", func=lambda x: x)

    long_description = "B" * (GPT4ToolsPreprocessor.MAX_DESCRIPTION_LENGTH + 200)
    tool2 = Tool(name="tool2", description=long_description, func=lambda x: x)

    return [tool1, tool2]


class TestPreprocessorIntegration:
    """Integration tests for the preprocessor chain."""

    def test_preprocessor_chain_application(self, mock_tools):
        """Test that a chain of preprocessors is applied correctly."""
        # Create a chain of preprocessors manually
        chain = [DescriptionPreprocessor(), GPT4ToolsPreprocessor()]

        # Apply the chain to tools
        processed_tools = mock_tools
        for preprocessor in chain:
            processed_tools = preprocessor.process(processed_tools)

        # Verify the results
        assert processed_tools[0].description == "Tool 1 description"  # Whitespace removed
        assert len(processed_tools[1].description) == GPT4ToolsPreprocessor.MAX_DESCRIPTION_LENGTH  # Truncated

    def test_factory_chain_application(self, mock_tools):
        """Test that a factory-created chain is applied correctly."""
        # Get a chain from the factory
        chain = ToolsPreprocessorFactory.create_preprocessor_chain(LLMService.BASE_NAME_GPT_41)

        # Apply the chain to tools
        processed_tools = mock_tools
        for preprocessor in chain:
            processed_tools = preprocessor.process(processed_tools)

        # Verify the results
        assert processed_tools[0].description == "Tool 1 description"  # Whitespace removed
        assert len(processed_tools[1].description) == GPT4ToolsPreprocessor.MAX_DESCRIPTION_LENGTH  # Truncated


class TestCustomPreprocessor:
    """Tests for creating and using a custom preprocessor."""

    class PrefixPreprocessor(ToolsPreprocessor):
        """A custom preprocessor that adds a prefix to tool descriptions."""

        def __init__(self, prefix="[TOOL] "):
            self.prefix = prefix

        def process(self, tools: list[BaseTool]) -> list[BaseTool]:
            """Add a prefix to all tool descriptions."""
            for tool in tools:
                if tool.description:
                    tool.description = f"{self.prefix}{tool.description}"
            return tools

    def test_custom_preprocessor(self):
        """Test using a custom preprocessor implementation."""
        tool = Tool(name="test_tool", description="Test description", func=lambda x: x)

        preprocessor = self.PrefixPreprocessor(prefix="[CUSTOM] ")
        result = preprocessor.process([tool])

        assert len(result) == 1
        assert result[0].description == "[CUSTOM] Test description"

    def test_custom_preprocessor_in_chain(self):
        """Test using a custom preprocessor in a chain with standard preprocessors."""
        tool = Tool(name="test_tool", description="  \n  Test description  \n  ", func=lambda x: x)

        # Create a chain with standard and custom preprocessors
        chain = [DescriptionPreprocessor(), self.PrefixPreprocessor()]

        # Apply the chain
        processed_tools = [tool]
        for preprocessor in chain:
            processed_tools = preprocessor.process(processed_tools)

        assert processed_tools[0].description == "[TOOL] Test description"
