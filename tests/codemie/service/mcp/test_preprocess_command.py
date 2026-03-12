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
Tests for the _process_string_with_placeholders method in MCPToolkitService.
"""

import pytest
from unittest.mock import patch
from codemie.service.mcp.toolkit_service import MCPToolkitService


class TestProcessStringWithPlaceholders:
    """Test class for _process_string_with_placeholders method."""

    def test_empty_string(self):
        """Test with empty string."""
        result = MCPToolkitService._process_string_with_placeholders("", {}, None)
        assert result == ""

    def test_string_without_placeholders(self):
        """Test string without any placeholders."""
        input_string = "This is a regular string"
        env_vars = {"user": "test_user"}
        result = MCPToolkitService._process_string_with_placeholders(input_string, env_vars, None)
        assert result == input_string

    @patch('codemie.service.tools.dynamic_value_utils.process_string')
    def test_string_with_square_bracket_placeholders(self, mock_process_string):
        """Test string with square bracket placeholders."""
        input_string = "Hello [username]!"
        env_vars = {"username": "test_user"}
        mock_process_string.return_value = "Hello test_user!"

        result = MCPToolkitService._process_string_with_placeholders(input_string, env_vars, None)

        # Should call process_string with normalized placeholders
        mock_process_string.assert_called_once_with(
            source="Hello {{username}}!", context=None, initial_dynamic_vals=env_vars, enable_recursive_resolution=None
        )
        assert result == "Hello test_user!"

    @patch('codemie.service.tools.dynamic_value_utils.process_string')
    def test_string_with_dollar_placeholders(self, mock_process_string):
        """Test string with dollar sign placeholders."""
        input_string = "Connect to $DATABASE_URL"
        env_vars = {"DATABASE_URL": "localhost:5432"}

        result = MCPToolkitService._process_string_with_placeholders(input_string, env_vars, None)

        # Dollar sign placeholders are not recognized by _normalize_placeholders,
        # so process_string should not be called and the original string should be returned
        mock_process_string.assert_not_called()
        assert result == input_string

    def test_string_with_double_brace_placeholders_already_normalized(self):
        """Test string that already has double brace placeholders."""
        input_string = "Hello {{username}}!"
        env_vars = {"username": "test_user"}

        with patch('codemie.service.tools.dynamic_value_utils.process_string') as mock_process_string:
            mock_process_string.return_value = "Hello test_user!"

            result = MCPToolkitService._process_string_with_placeholders(input_string, env_vars, None)

            mock_process_string.assert_called_once_with(
                source=input_string, context=None, initial_dynamic_vals=env_vars, enable_recursive_resolution=None
            )
            assert result == "Hello test_user!"

    def test_string_with_mixed_placeholder_formats(self):
        """Test string with mixed placeholder formats."""
        input_string = "Hello [name], your $score is {{level}}!"
        env_vars = {"name": "Alice", "score": "100", "level": "expert"}

        with patch('codemie.service.tools.dynamic_value_utils.process_string') as mock_process_string:
            mock_process_string.return_value = "Hello Alice, your $score is expert!"

            result = MCPToolkitService._process_string_with_placeholders(input_string, env_vars, None)

            # Should normalize only square brackets to double brace format,
            # dollar signs are not converted
            mock_process_string.assert_called_once_with(
                source="Hello {{name}}, your $score is {{level}}!",
                context=None,
                initial_dynamic_vals=env_vars,
                enable_recursive_resolution=None,
            )
            assert result == "Hello Alice, your $score is expert!"

    def test_with_preprocessor_function(self):
        """Test with a custom preprocessor function."""
        input_string = "Hello [name]!"
        env_vars = {"name": "test_user"}

        def mock_preprocessor(text, env_vars):
            return text.upper()

        result = MCPToolkitService._process_string_with_placeholders(input_string, env_vars, mock_preprocessor)

        # Should use preprocessor instead of process_string
        assert result == "HELLO {{NAME}}!"

    def test_with_preprocessor_function_no_placeholders(self):
        """Test with preprocessor function but no placeholders in string."""
        input_string = "Hello world!"
        env_vars = {"name": "test_user"}

        def mock_preprocessor(text, env_vars):
            return text.upper()

        result = MCPToolkitService._process_string_with_placeholders(input_string, env_vars, mock_preprocessor)

        # Should not call preprocessor since no placeholders were found
        assert result == input_string

    def test_complex_environment_variables(self):
        """Test with complex environment variables."""
        input_string = "Server: $HOST:$PORT, Database: [db_name], Config: {{config_path}}"
        env_vars = {"HOST": "localhost", "PORT": "8080", "db_name": "production", "config_path": "/etc/app.conf"}

        with patch('codemie.service.tools.dynamic_value_utils.process_string') as mock_process_string:
            expected_output = "Server: $HOST:$PORT, Database: production, Config: /etc/app.conf"
            mock_process_string.return_value = expected_output

            result = MCPToolkitService._process_string_with_placeholders(input_string, env_vars, None)

            # Only square brackets should be normalized, dollar signs remain unchanged
            mock_process_string.assert_called_once_with(
                source="Server: $HOST:$PORT, Database: {{db_name}}, Config: {{config_path}}",
                context=None,
                initial_dynamic_vals=env_vars,
                enable_recursive_resolution=None,
            )
            assert result == expected_output

    def test_empty_env_vars(self):
        """Test with empty environment variables dict."""
        input_string = "Hello [name]!"
        env_vars = {}

        with patch('codemie.service.tools.dynamic_value_utils.process_string') as mock_process_string:
            mock_process_string.return_value = "Hello [name]!"  # Unresolved

            MCPToolkitService._process_string_with_placeholders(input_string, env_vars, None)

            mock_process_string.assert_called_once_with(
                source="Hello {{name}}!", context=None, initial_dynamic_vals=env_vars, enable_recursive_resolution=None
            )

    def test_none_env_vars(self):
        """Test with None environment variables."""
        input_string = "Hello [name]!"

        with patch('codemie.service.tools.dynamic_value_utils.process_string') as mock_process_string:
            mock_process_string.return_value = "Hello {{name}}!"  # Unresolved

            MCPToolkitService._process_string_with_placeholders(input_string, None, None)

            mock_process_string.assert_called_once_with(
                source="Hello {{name}}!", context=None, initial_dynamic_vals=None, enable_recursive_resolution=None
            )

    def test_preprocessor_exception_handling(self):
        """Test that preprocessor exceptions are handled gracefully."""
        input_string = "Hello [name]!"
        env_vars = {"name": "test_user"}

        def failing_preprocessor(text, env_vars):
            raise ValueError("Preprocessor failed")

        # Should raise the preprocessor exception
        with pytest.raises(ValueError, match="Preprocessor failed"):
            MCPToolkitService._process_string_with_placeholders(input_string, env_vars, failing_preprocessor)

    def test_integration_with_real_process_string(self):
        """Integration test with the real process_string function (no mocking)."""
        input_string = "Hello [name], you have $count messages"
        env_vars = {"name": "Alice", "count": "5"}

        result = MCPToolkitService._process_string_with_placeholders(input_string, env_vars, None)

        # The process_string function now correctly resolves placeholders using initial_dynamic_vals
        # even when context is None. However, dollar sign placeholders are not normalized,
        # so $count remains unresolved
        assert result == "Hello Alice, you have $count messages"

    def test_variable_names_with_special_characters(self):
        """Test variable names with valid special characters (underscores, numbers)."""
        input_string = "[user_id] and $config_v2 and {{app_name}}"
        env_vars = {"user_id": "123", "config_v2": "production", "app_name": "MyApp"}

        with patch('codemie.service.tools.dynamic_value_utils.process_string') as mock_process_string:
            mock_process_string.return_value = "123 and $config_v2 and MyApp"

            MCPToolkitService._process_string_with_placeholders(input_string, env_vars, None)

            # Only square brackets should be normalized, dollar signs remain unchanged
            mock_process_string.assert_called_once_with(
                source="{{user_id}} and $config_v2 and {{app_name}}",
                context=None,
                initial_dynamic_vals=env_vars,
                enable_recursive_resolution=None,
            )
