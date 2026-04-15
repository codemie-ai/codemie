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

from unittest.mock import Mock


from codemie.agents.callbacks.utils.name_resolver import (
    NameResolver,
    NoOpNameResolver,
    resolve_tool_display_name,
)


class TestNoOpNameResolver:
    """Test suite for NoOpNameResolver class."""

    def test_returns_input_unchanged(self):
        """Test that NoOpNameResolver returns the input name unchanged."""
        resolver = NoOpNameResolver()
        test_name = "test_assistant_name"

        result = resolver.get_original_sub_assistant_name(test_name)

        assert result == test_name, "NoOpNameResolver should return input unchanged"

    def test_handles_empty_string(self):
        """Test that NoOpNameResolver handles empty string correctly."""
        resolver = NoOpNameResolver()

        result = resolver.get_original_sub_assistant_name("")

        assert result == "", "NoOpNameResolver should handle empty string"

    def test_handles_long_names(self):
        """Test that NoOpNameResolver handles long names without modification."""
        resolver = NoOpNameResolver()
        long_name = "a" * 100

        result = resolver.get_original_sub_assistant_name(long_name)

        assert result == long_name, "NoOpNameResolver should not modify long names"


class TestResolveToolDisplayName:
    """Test suite for resolve_tool_display_name function."""

    def test_regular_tool_formatting(self):
        """Test that regular tools are formatted with spaces and title case."""
        resolver = NoOpNameResolver()
        tool_name = "search_knowledge_base"

        result = resolve_tool_display_name(tool_name, resolver)

        assert result == "Search Knowledge Base", "Regular tool should be title-cased with spaces"

    def test_regular_tool_single_word(self):
        """Test that single-word tool names are title-cased."""
        resolver = NoOpNameResolver()
        tool_name = "calculator"

        result = resolve_tool_display_name(tool_name, resolver)

        assert result == "Calculator", "Single-word tool should be title-cased"

    def test_handoff_tool_name_resolution(self):
        """Test that handoff tools (transfer_to_ prefix) resolve to original sub-assistant names."""
        mock_resolver = Mock(spec=NameResolver)
        mock_resolver.get_original_sub_assistant_name.return_value = "Data Analysis Expert"

        tool_name = "transfer_to_data_analysis_exp_abc123"

        result = resolve_tool_display_name(tool_name, mock_resolver)

        mock_resolver.get_original_sub_assistant_name.assert_called_once_with("data_analysis_exp_abc123")
        assert result == "Data Analysis Expert", "Handoff tool should resolve to original name"

    def test_handoff_tool_with_no_mapping(self):
        """Test that handoff tools without mapping fall back to formatted truncated name."""
        mock_resolver = Mock(spec=NameResolver)
        mock_resolver.get_original_sub_assistant_name.side_effect = lambda x: x

        tool_name = "transfer_to_data_analyst"

        result = resolve_tool_display_name(tool_name, mock_resolver)

        mock_resolver.get_original_sub_assistant_name.assert_called_once_with("data_analyst")
        assert result == "Data Analyst", "Should format the truncated name if no mapping exists"

    def test_tool_name_with_multiple_underscores(self):
        """Test formatting of tool names with multiple consecutive underscores."""
        resolver = NoOpNameResolver()
        tool_name = "search__knowledge__base"

        result = resolve_tool_display_name(tool_name, resolver)

        assert result == "Search  Knowledge  Base", "Multiple underscores should be preserved as spaces"

    def test_handoff_tool_prefix_only(self):
        """Test handling of handoff tool with prefix but no truncated name part."""
        mock_resolver = Mock(spec=NameResolver)
        mock_resolver.get_original_sub_assistant_name.side_effect = lambda x: x

        tool_name = "transfer_to_"

        result = resolve_tool_display_name(tool_name, mock_resolver)

        mock_resolver.get_original_sub_assistant_name.assert_called_once_with("")
        assert result == "", "Empty name after prefix should result in empty display name"

    def test_tool_starting_with_underscore_not_treated_as_handoff(self):
        """Test that tools starting with underscore (but not transfer_to_ prefix) are regular tools."""
        resolver = NoOpNameResolver()
        tool_name = "_internal_helper"

        result = resolve_tool_display_name(tool_name, resolver)

        # Does NOT match "transfer_to_" prefix, so treated as a regular tool
        assert result == " Internal Helper", "Underscore-prefixed tool should be formatted as a regular tool"

    def test_handoff_tool_with_long_truncated_name(self):
        """Test handoff tool with a long truncated name including hash suffix."""
        mock_resolver = Mock(spec=NameResolver)
        mock_resolver.get_original_sub_assistant_name.return_value = (
            "Very Long Original Assistant Name That Was Truncated"
        )

        truncated_name = "very_long_original_assistant_n_f3a8d92e1b"
        tool_name = f"transfer_to_{truncated_name}"

        result = resolve_tool_display_name(tool_name, mock_resolver)

        mock_resolver.get_original_sub_assistant_name.assert_called_once_with(truncated_name)
        assert result == "Very Long Original Assistant Name That Was Truncated"

    def test_non_handoff_tool_not_resolved(self):
        """Test that non-handoff tools are never passed to the name resolver."""
        mock_resolver = Mock(spec=NameResolver)

        tool_name = "search_knowledge_base"

        resolve_tool_display_name(tool_name, mock_resolver)

        mock_resolver.get_original_sub_assistant_name.assert_not_called()


class TestNameResolverProtocol:
    """Test suite to verify NameResolver protocol compliance."""

    def test_mock_resolver_implements_protocol(self):
        """Test that a mock implementing the protocol works correctly."""
        mock_resolver = Mock(spec=NameResolver)
        mock_resolver.get_original_sub_assistant_name.return_value = "test_result"

        result = mock_resolver.get_original_sub_assistant_name("test_input")

        assert result == "test_result"
        mock_resolver.get_original_sub_assistant_name.assert_called_once_with("test_input")

    def test_noop_resolver_conforms_to_protocol(self):
        """Test that NoOpNameResolver conforms to NameResolver protocol."""
        resolver = NoOpNameResolver()

        # Should have the required method
        assert hasattr(resolver, "get_original_sub_assistant_name")
        assert callable(resolver.get_original_sub_assistant_name)

        # Should work as expected
        result = resolver.get_original_sub_assistant_name("test")
        assert result == "test"
