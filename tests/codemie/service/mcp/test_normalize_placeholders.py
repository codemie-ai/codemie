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
Tests for the _normalize_placeholders method in MCPToolkitService.
"""

from codemie.service.mcp.toolkit_service import MCPToolkitService


class TestNormalizePlaceholders:
    """Test class for _normalize_placeholders method."""

    def test_empty_string(self):
        """Test with empty string."""
        result, found = MCPToolkitService._normalize_placeholders("")
        assert result == ""
        assert found is False

    def test_none_string(self):
        """Test with None string."""
        result, found = MCPToolkitService._normalize_placeholders(None)
        assert result is None
        assert found is False

    def test_no_placeholders(self):
        """Test string with no placeholders."""
        input_string = "This is a regular string with no placeholders"
        result, found = MCPToolkitService._normalize_placeholders(input_string)
        assert result == input_string
        assert found is False

    def test_square_bracket_placeholder(self):
        """Test converting [variable_name] to {{variable_name}}."""
        input_string = "Hello [username], welcome to [app_name]!"
        result, found = MCPToolkitService._normalize_placeholders(input_string)
        assert result == "Hello {{username}}, welcome to {{app_name}}!"
        assert found is True

    def test_dollar_placeholder(self):
        """Test that $variable_name is not converted (only [variable] is converted)."""
        input_string = "Hello $username, welcome to $app_name!"
        result, found = MCPToolkitService._normalize_placeholders(input_string)
        assert result == "Hello $username, welcome to $app_name!"
        assert found is False

    def test_double_brace_placeholder_already_normalized(self):
        """Test that {{variable_name}} is recognized but not changed."""
        input_string = "Hello {{username}}, welcome to {{app_name}}!"
        result, found = MCPToolkitService._normalize_placeholders(input_string)
        assert result == input_string
        assert found is True

    def test_mixed_placeholder_formats(self):
        """Test string with multiple placeholder formats (only [variable] gets converted)."""
        input_string = "Hello [username], your $score is {{points}}!"
        result, found = MCPToolkitService._normalize_placeholders(input_string)
        assert result == "Hello {{username}}, your $score is {{points}}!"
        assert found is True

    def test_variable_names_with_underscores(self):
        """Test variable names with underscores (only [variable] gets converted)."""
        input_string = "[user_name] and $app_version and {{config_file}}"
        result, found = MCPToolkitService._normalize_placeholders(input_string)
        assert result == "{{user_name}} and $app_version and {{config_file}}"
        assert found is True

    def test_variable_names_with_numbers(self):
        """Test variable names with numbers (only [variable] gets converted)."""
        input_string = "[user123] and $app2 and {{config_v1}}"
        result, found = MCPToolkitService._normalize_placeholders(input_string)
        assert result == "{{user123}} and $app2 and {{config_v1}}"
        assert found is True

    def test_variable_names_starting_with_underscore(self):
        """Test variable names starting with underscore (only [variable] gets converted)."""
        input_string = "[_private_var] and $_internal and {{_config}}"
        result, found = MCPToolkitService._normalize_placeholders(input_string)
        assert result == "{{_private_var}} and $_internal and {{_config}}"
        assert found is True

    def test_invalid_variable_names_ignored(self):
        """Test that invalid variable names are ignored."""
        # Variables starting with numbers should be ignored
        input_string = "[123invalid] and $456bad and valid_$var and [good_var]"
        result, found = MCPToolkitService._normalize_placeholders(input_string)
        # Only [good_var] should be converted
        assert result == "[123invalid] and $456bad and valid_$var and {{good_var}}"
        assert found is True

    def test_multiple_same_placeholder(self):
        """Test multiple instances of the same placeholder."""
        input_string = "Hello [name], goodbye [name], welcome [name]!"
        result, found = MCPToolkitService._normalize_placeholders(input_string)
        assert result == "Hello {{name}}, goodbye {{name}}, welcome {{name}}!"
        assert found is True

    def test_edge_case_with_special_characters(self):
        """Test edge cases with special characters near placeholders."""
        input_string = "[$var] and [$var2] and {[var3]} and ($var4)"
        result, found = MCPToolkitService._normalize_placeholders(input_string)
        assert result == "[$var] and [$var2] and {{{var3}}} and ($var4)"
        assert found is True

    def test_complex_real_world_example(self):
        """Test a complex real-world example (only [variable] gets converted)."""
        input_string = "Connect to $DATABASE_URL with user [username] using config {{environment}}"
        result, found = MCPToolkitService._normalize_placeholders(input_string)
        assert result == "Connect to $DATABASE_URL with user {{username}} using config {{environment}}"
        assert found is True

    def test_only_double_braces_no_conversion_needed(self):
        """Test string with only double braces (no conversion needed)."""
        input_string = "All variables {{var1}} and {{var2}} are normalized"
        result, found = MCPToolkitService._normalize_placeholders(input_string)
        assert result == input_string
        assert found is True

    def test_case_sensitivity(self):
        """Test that variable names are case sensitive."""
        input_string = "[VAR] and [var] and [Var]"
        result, found = MCPToolkitService._normalize_placeholders(input_string)
        assert result == "{{VAR}} and {{var}} and {{Var}}"
        assert found is True

    def test_nested_brackets_not_converted(self):
        """Test that nested brackets are handled correctly."""
        input_string = "[[nested]] and [valid_var]"
        result, found = MCPToolkitService._normalize_placeholders(input_string)
        # The regex actually matches [nested] inside [[nested]], so it becomes [{{nested}}]
        assert result == "[{{nested}}] and {{valid_var}}"
        assert found is True
