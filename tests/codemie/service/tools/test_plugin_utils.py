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

"""Unit tests for plugin utility functions."""

from codemie.service.tools.plugin_utils import cleanup_plugin_tool_name


class TestCleanupPluginToolName:
    """Test suite for cleanup_plugin_tool_name function."""

    def test_cleanup_with_suffix(self):
        """Test cleanup removes suffix after last underscore."""
        result = cleanup_plugin_tool_name("_test_tool_abc")
        assert result == "_test_tool"

    def test_cleanup_multiple_underscores(self):
        """Test cleanup only removes last suffix."""
        result = cleanup_plugin_tool_name("_my_complex_tool_name_xyz")
        assert result == "_my_complex_tool_name"

    def test_cleanup_single_underscore(self):
        """Test cleanup with single underscore."""
        result = cleanup_plugin_tool_name("tool_suffix")
        assert result == "tool"

    def test_cleanup_no_underscore(self):
        """Test cleanup returns name as-is when no underscore."""
        result = cleanup_plugin_tool_name("simpletool")
        assert result == "simpletool"

    def test_cleanup_ends_with_underscore(self):
        """Test cleanup when name ends with underscore."""
        result = cleanup_plugin_tool_name("tool_name_")
        assert result == "tool_name"

    def test_cleanup_only_underscore(self):
        """Test cleanup with only underscore."""
        result = cleanup_plugin_tool_name("_")
        assert result == ""

    def test_cleanup_empty_string(self):
        """Test cleanup with empty string."""
        result = cleanup_plugin_tool_name("")
        assert result == ""

    def test_cleanup_realistic_plugin_tool(self):
        """Test cleanup with realistic plugin tool name."""
        result = cleanup_plugin_tool_name("_create_jira_ticket_abc123")
        assert result == "_create_jira_ticket"
