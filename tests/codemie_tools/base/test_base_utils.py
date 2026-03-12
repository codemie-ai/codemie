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

import json
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from codemie_tools.base.utils import (
    sanitize_string,
    parse_to_dict,
    parse_tool_input,
    ToolException,
    clean_json_string,
    parse_and_escape_args,
)


class TestUtils:
    def test_sanitize_string(self):
        test_cases = [
            (
                "Error: Unable to connect. Username: admin, Password: secret123, IP: 192.168.1.1",
                "Error: Unable to connect. Username: *** Password: *** IP: [IP_ADDRESS]",
            ),
            ("Contact us at support@example.com", "Contact us at [EMAIL]"),
            ("api_key: abc123xyz, access_token: 987654321", "api_key: [API_KEY] access_token: [API_KEY]"),
            ("Credit card number: 1234-5678-8765-4321", "Credit card number: [CREDIT_CARD]"),
            ("No sensitive data here", "No sensitive data here"),
        ]

        for input_string, expected_output in test_cases:
            assert sanitize_string(input_string) == expected_output

    def test_parse_to_dict(self):
        test_cases = [
            ('{"key": "value"}', {"key": "value"}),
            ("{'key': 'value'}", {"key": "value"}),
            ("{'header':'asdad'}", {"header": "asdad"}),
            ("{'outer': {'inner': 'value'}}", {"outer": {"inner": "value"}}),
            ('test data', {}),
            ('Invalid JSON string', {}),
            ('', {}),
            (None, {}),
        ]

        for input_string, expected_output in test_cases:
            assert parse_to_dict(input_string) == expected_output

    @patch('codemie_tools.base.utils.logger')
    def test_parse_tool_input(self, mock_logger):
        class MockBaseModel(BaseModel):
            key: str

        valid_dict_input = {"key": "value"}
        valid_str_input = '{"key": "value"}'
        invalid_str_input = '{"wrong_key": "value"}'

        # Valid dictionary input
        result = parse_tool_input(MockBaseModel, valid_dict_input)
        assert result == valid_dict_input

        # Valid string input
        result = parse_tool_input(MockBaseModel, valid_str_input)
        assert result == valid_dict_input

        # Invalid string input
        with pytest.raises(ToolException):
            parse_tool_input(MockBaseModel, invalid_str_input)

        # Check logger output
        mock_logger.info.assert_any_call("Starting parser with input: {'key': 'value'}")
        mock_logger.info.assert_any_call("Starting parser with input: {\"key\": \"value\"}")
        mock_logger.info.assert_any_call("isinstance(tool_input, str)")
        mock_logger.info.assert_any_call("else isinstance(tool_input, dict)")

    @pytest.mark.parametrize(
        "input_str, expected_output",
        [
            ("'''\\n{\\n    \"key\": \"value\"\\n}\\n'''", '{\\n    "key": "value"\\n}'),
            ('{"key": "value"}', '{"key": "value"}'),
            ("before{\"outer\": {\"inner\": \"value\"}}after", '{"outer": {"inner": "value"}}'),
            ("This is not a JSON string", "This is not a JSON string"),
            ("", ""),
            ("before{}after", "{}"),
            ("{\"key\": null}", "{\"key\": null}"),
            ("   {\"spaces\": \"around\"}   ", "{\"spaces\": \"around\"}"),
            ("\n\n{\"newlines\": \"before and after\"}\n\n", "{\"newlines\": \"before and after\"}"),
        ],
        ids=[
            "valid_json_with_extra_chars",
            "clean_json_no_change",
            "nested_json_with_extra_chars",
            "no_json_string",
            "empty_string",
            "empty_json_with_extra_chars",
            "json_with_null_value",
            "json_with_surrounding_spaces",
            "json_with_surrounding_newlines",
        ],
    )
    def test_clean_json_string(self, input_str, expected_output):
        assert clean_json_string(input_str) == expected_output

    def test_clean_json_string_invalid(self):
        input_str = """
        {"fields":{"project":{"key":"PROJ"},"summary":"Automate workflow with \
        'diff_update_file_tool' tool","description":"This sub-task involves automating the \
        workflow that utilizes the 'diff_update_file_tool' tool. The goal is to ensure that \
        the tool operates correctly within the workflow and that all related functionality is \
        thoroughly tested.\n\nAcceptance Criteria:\n1. Implement the automated test for the \
        'diff_update_file_tool' tool in the workflow.\n2. Verify that the tool performs the \
        expected operations within the workflow.\n3. Ensure that the workflow completes \
        successfully and provides the correct results.\n4. Test various scenarios to ensure \
        the robustness of the tool within the workflow.\n\nPriority: Major","issuetype":\
        {"name":"Sub-task"},"parent":{"key":"PROJ-4311"},"labels":["AI/RUN",\
        "AI-Generated"]}}
        """
        result = json.loads(clean_json_string(input_str))

        assert result['fields']['project']['key'] == 'PROJ'
        assert result['fields']['labels'] == ['AI/RUN', 'AI-Generated']

    def test_parse_and_escape_args_with_dict(self):
        """Test parse_and_escape_args with a dictionary input"""
        test_dict = {"key": "value"}
        result = parse_and_escape_args(test_dict)
        assert result == test_dict

        # With item_type
        result = parse_and_escape_args(test_dict, item_type="config")
        assert result == test_dict

    def test_parse_and_escape_args_with_json_string(self):
        """Test parse_and_escape_args with a JSON string input"""
        test_json = '{"key": "value"}'
        result = parse_and_escape_args(test_json)
        assert result == {"key": "value"}

        # With item_type
        result = parse_and_escape_args(test_json, item_type="config")
        assert result == {"key": "value"}

    def test_parse_and_escape_args_with_empty_input(self):
        """Test parse_and_escape_args with empty input"""
        assert parse_and_escape_args("") == {}
        assert parse_and_escape_args(None) == {}
        assert parse_and_escape_args("", item_type="config") == {}

    def test_parse_and_escape_args_with_invalid_json(self):
        """Test parse_and_escape_args with invalid JSON input"""
        invalid_json = '{"key": "value"'

        # Without item_type
        with pytest.raises(ToolException) as excinfo:
            parse_and_escape_args(invalid_json)
        assert "Invalid JSON format:" in str(excinfo.value)

        # With item_type
        with pytest.raises(ToolException) as excinfo:
            parse_and_escape_args(invalid_json, item_type="config")
        assert "Invalid JSON format in config:" in str(excinfo.value)

    def test_parse_and_escape_args_with_non_dict_or_string(self):
        """Test parse_and_escape_args with invalid input type"""
        # Without item_type
        with pytest.raises(ToolException) as excinfo:
            parse_and_escape_args(123)
        assert "Input must be a JSON string or dict" in str(excinfo.value)

        # With item_type
        with pytest.raises(ToolException) as excinfo:
            parse_and_escape_args(123, item_type="config")
        assert "config must be a JSON string or dict" in str(excinfo.value)
