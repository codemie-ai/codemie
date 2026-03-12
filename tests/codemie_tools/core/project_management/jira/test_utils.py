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

from unittest.mock import MagicMock

import pytest
from atlassian import Jira
from langchain_core.tools import ToolException

from codemie_tools.base.errors import InvalidCredentialsError
from codemie_tools.core.project_management.jira.utils import (
    validate_jira_creds,
    parse_payload_params,
    get_issue_field,
    get_additional_fields,
    process_issue,
    extract_fields_list,
    update_parsed_issue_with_additional_data,
    process_search_response,
)


class TestValidateJiraCreds:
    """Tests for validate_jira_creds function."""

    def test_valid_creds(self):
        """Test with valid credentials."""
        jira = MagicMock(spec=Jira)
        jira.url = "https://jira.example.com"

        # Should not raise an exception
        validate_jira_creds(jira)

    def test_empty_url(self):
        """Test with empty URL."""
        jira = MagicMock(spec=Jira)
        jira.url = ""

        with pytest.raises(InvalidCredentialsError) as excinfo:
            validate_jira_creds(jira)
        assert "Jira URL is required" in str(excinfo.value)

    def test_none_url(self):
        """Test with None URL."""
        jira = MagicMock(spec=Jira)
        jira.url = None

        with pytest.raises(InvalidCredentialsError) as excinfo:
            validate_jira_creds(jira)
        assert "Jira URL is required" in str(excinfo.value)


class TestParsePayloadParams:
    """Tests for parse_payload_params function."""

    # Tests for dict input (new recommended format)
    def test_dict_input_simple(self):
        """Test with simple dict input (recommended format)."""
        params = {"key": "value", "number": 123}
        result = parse_payload_params(params)
        assert result == {"key": "value", "number": 123}

    def test_dict_input_complex_jql(self):
        """Test dict with JQL containing quoted field names."""
        params = {
            "jql": 'project = PROJ AND "epic link" = EPIC-123',
            "fields": ["key", "summary"],
            "maxResults": 10,
        }
        result = parse_payload_params(params)
        assert result["jql"] == 'project = PROJ AND "epic link" = EPIC-123'
        assert result["fields"] == ["key", "summary"]
        assert result["maxResults"] == 10

    def test_dict_input_multiline_description(self):
        """Test dict with multi-line content in descriptions."""
        params = {
            "fields": {
                "project": {"key": "TEST"},
                "summary": "Test Issue",
                "description": "Line 1\nLine 2\n<li>Item</li>\n<p>Paragraph</p>",
            }
        }
        result = parse_payload_params(params)
        assert "\n" in result["fields"]["description"]
        assert "<li>Item</li>" in result["fields"]["description"]
        assert "<p>Paragraph</p>" in result["fields"]["description"]

    def test_dict_input_nested_structure(self):
        """Test dict with deeply nested structure."""
        params = {
            "fields": {
                "project": {"key": "TEST"},
                "summary": "Test Issue",
                "customfield_1000": {"nested": {"level": 2, "data": ["item1", "item2"]}},
            }
        }
        result = parse_payload_params(params)
        assert result["fields"]["customfield_1000"]["nested"]["level"] == 2
        assert result["fields"]["customfield_1000"]["nested"]["data"] == ["item1", "item2"]

    def test_dict_input_with_special_chars(self):
        """Test dict with special characters and quotes."""
        params = {
            "jql": """project = TEST AND summary ~ 'firefox' AND "epic link" = EPIC-123""",
            "fields": ["key", "summary"],
        }
        result = parse_payload_params(params)
        assert "'firefox'" in result["jql"]
        assert '"epic link"' in result["jql"]

    # Tests for string input (legacy format)
    def test_valid_json(self):
        """Test with valid JSON string (legacy format)."""
        params = '{"key": "value", "number": 123}'
        result = parse_payload_params(params)
        assert result == {"key": "value", "number": 123}

    def test_empty_string(self):
        """Test with empty string."""
        result = parse_payload_params("")
        assert result == {}

    def test_none_params(self):
        """Test with None params."""
        result = parse_payload_params(None)
        assert result == {}

    def test_invalid_json(self):
        """Test with invalid JSON string."""
        params = '{"key": "value", number: 123}'  # Missing quotes around number

        with pytest.raises(ToolException) as excinfo:
            parse_payload_params(params)
        assert "JIRA tool exception" in str(excinfo.value)
        assert "not valid JSON" in str(excinfo.value)

    def test_invalid_json_with_enhanced_error(self):
        """Test that invalid JSON provides enhanced error message."""
        params = '{"key": "value", "bad": }'  # Invalid JSON

        with pytest.raises(ToolException) as excinfo:
            parse_payload_params(params)
        error_msg = str(excinfo.value)
        assert "not valid JSON" in error_msg
        assert "Tip: Use dict format" in error_msg
        assert "Example: params=" in error_msg

    def test_invalid_json_complex_escaping_issue(self):
        """Test JSON string with complex escaping issues (mimics the real bug)."""
        # This mimics the bug from the conversation: unescaped quotes in JQL
        params = '{"jql": "project = X AND "epic link" = Y", "fields": ["key"]}'

        with pytest.raises(ToolException) as excinfo:
            parse_payload_params(params)
        assert "not valid JSON" in str(excinfo.value)

    # Tests for invalid types
    def test_invalid_type_list(self):
        """Test with invalid type (list)."""
        params = ["key", "value"]

        with pytest.raises(ToolException) as excinfo:
            parse_payload_params(params)
        assert "Invalid params type" in str(excinfo.value)
        assert "list" in str(excinfo.value)

    def test_invalid_type_number(self):
        """Test with invalid type (number)."""
        params = 123

        with pytest.raises(ToolException) as excinfo:
            parse_payload_params(params)
        assert "Invalid params type" in str(excinfo.value)
        assert "int" in str(excinfo.value)


class TestGetIssueField:
    """Tests for get_issue_field function."""

    def test_existing_field(self):
        """Test getting an existing field."""
        issue = {"fields": {"summary": "Test Issue"}}
        result = get_issue_field(issue, "summary")
        assert result == "Test Issue"

    def test_missing_field(self):
        """Test getting a missing field."""
        issue = {"fields": {"summary": "Test Issue"}}
        result = get_issue_field(issue, "description")
        assert result is None

    def test_missing_field_with_default(self):
        """Test getting a missing field with a default value."""
        issue = {"fields": {"summary": "Test Issue"}}
        result = get_issue_field(issue, "description", "No description")
        assert result == "No description"

    def test_none_field_value(self):
        """Test getting a field with None value."""
        issue = {"fields": {"summary": None}}
        result = get_issue_field(issue, "summary", "Default")
        assert result == "Default"

    def test_none_issue(self):
        """Test with None issue."""
        result = get_issue_field(None, "summary", "Default")
        assert result == "Default"

    def test_no_fields(self):
        """Test with issue having no fields."""
        issue = {}
        result = get_issue_field(issue, "summary", "Default")
        assert result == "Default"


class TestGetAdditionalFields:
    """Tests for get_additional_fields function."""

    def test_get_additional_fields(self):
        """Test getting additional fields."""
        issue = {
            "fields": {
                "summary": "Test Issue",
                "description": "Test Description",
                "priority": {"name": "High"},
            }
        }
        additional_fields = ["description", "priority"]
        result = get_additional_fields(issue, additional_fields)
        assert result == {"description": "Test Description", "priority": {"name": "High"}}

    def test_missing_fields(self):
        """Test with missing fields."""
        issue = {"fields": {"summary": "Test Issue"}}
        additional_fields = ["description", "priority"]
        result = get_additional_fields(issue, additional_fields)
        assert result == {"description": None, "priority": None}


class TestProcessIssue:
    """Tests for process_issue function."""

    def test_process_issue_basic(self):
        """Test processing an issue with basic fields."""
        issue = {
            "key": "TEST-123",
            "fields": {
                "summary": "Test Issue",
                "assignee": {"displayName": "John Doe"},
                "status": {"name": "In Progress"},
                "issuetype": {"name": "Bug"},
            },
        }
        jira_base_url = "https://jira.example.com"
        result = process_issue(jira_base_url, issue)

        assert result["key"] == "TEST-123"
        assert result["url"] == "https://jira.example.com/browse/TEST-123"
        assert result["summary"] == "Test Issue"
        assert result["assignee"] == "John Doe"
        assert result["status"] == "In Progress"
        assert result["issuetype"] == "Bug"

    def test_process_issue_missing_fields(self):
        """Test processing an issue with missing fields."""
        issue = {"key": "TEST-123", "fields": {"summary": "Test Issue"}}
        jira_base_url = "https://jira.example.com"
        result = process_issue(jira_base_url, issue)

        assert result["key"] == "TEST-123"
        assert result["url"] == "https://jira.example.com/browse/TEST-123"
        assert result["summary"] == "Test Issue"
        assert result["assignee"] == "None"
        assert result["status"] == ""
        assert result["issuetype"] == ""

    def test_process_issue_with_additional_fields(self):
        """Test processing an issue with additional fields."""
        issue = {
            "key": "TEST-123",
            "fields": {
                "summary": "Test Issue",
                "assignee": {"displayName": "John Doe"},
                "status": {"name": "In Progress"},
                "issuetype": {"name": "Bug"},
                "description": "Test Description",
                "priority": {"name": "High"},
            },
        }
        jira_base_url = "https://jira.example.com"
        payload_params = {"fields": ["description", "priority"]}
        result = process_issue(jira_base_url, issue, payload_params)

        assert result["key"] == "TEST-123"
        assert result["summary"] == "Test Issue"
        assert result["description"] == "Test Description"
        assert result["priority"] == {"name": "High"}


class TestExtractFieldsList:
    """Tests for extract_fields_list function."""

    def test_extract_fields_string(self):
        """Test extracting fields from a comma-separated string."""
        payload_params = {"fields": "summary,description,priority"}
        result = extract_fields_list(payload_params)
        assert result == ["summary", "description", "priority"]

    def test_extract_fields_list(self):
        """Test extracting fields from a list."""
        payload_params = {"fields": ["summary", "description", "priority"]}
        result = extract_fields_list(payload_params)
        assert result == ["summary", "description", "priority"]

    def test_extract_fields_empty_string(self):
        """Test extracting fields from an empty string."""
        payload_params = {"fields": ""}
        result = extract_fields_list(payload_params)
        assert result == []

    def test_extract_fields_empty_list(self):
        """Test extracting fields from an empty list."""
        payload_params = {"fields": []}
        result = extract_fields_list(payload_params)
        assert result == []

    def test_extract_fields_no_fields(self):
        """Test extracting fields when no fields are specified."""
        payload_params = {}
        result = extract_fields_list(payload_params)
        assert result == []

    def test_extract_fields_none_params(self):
        """Test extracting fields with None params."""
        result = extract_fields_list(None)
        assert result == []


class TestUpdateParsedIssueWithAdditionalData:
    """Tests for update_parsed_issue_with_additional_data function."""

    def test_update_parsed_issue(self):
        """Test updating a parsed issue with additional data."""
        issue = {
            "fields": {
                "description": "Test Description",
                "priority": {"name": "High"},
                "components": [{"name": "Frontend"}, {"name": "Backend"}],
            }
        }
        fields_list = ["description", "priority", "components"]
        parsed_issue = {"key": "TEST-123", "summary": "Test Issue"}

        update_parsed_issue_with_additional_data(issue, fields_list, parsed_issue)

        assert parsed_issue["description"] == "Test Description"
        assert parsed_issue["priority"] == {"name": "High"}
        assert parsed_issue["components"] == [{"name": "Frontend"}, {"name": "Backend"}]

    def test_update_parsed_issue_existing_field(self):
        """Test that existing fields are not overwritten."""
        issue = {"fields": {"summary": "New Summary", "description": "Test Description"}}
        fields_list = ["summary", "description"]
        parsed_issue = {"key": "TEST-123", "summary": "Original Summary"}

        update_parsed_issue_with_additional_data(issue, fields_list, parsed_issue)

        # summary should not be overwritten
        assert parsed_issue["summary"] == "Original Summary"
        assert parsed_issue["description"] == "Test Description"

    def test_update_parsed_issue_none_value(self):
        """Test that None values are not added."""
        issue = {"fields": {"description": None}}
        fields_list = ["description"]
        parsed_issue = {"key": "TEST-123", "summary": "Test Issue"}

        update_parsed_issue_with_additional_data(issue, fields_list, parsed_issue)

        # description should not be added since it's None
        assert "description" not in parsed_issue


class TestProcessSearchResponse:
    """Tests for process_search_response function."""

    def test_process_search_response_success(self):
        """Test processing a successful search response."""
        jira_url = "https://jira.example.com"

        # Mock response
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "issues": [
                {
                    "key": "TEST-123",
                    "fields": {
                        "summary": "Test Issue 1",
                        "assignee": {"displayName": "John Doe"},
                        "status": {"name": "In Progress"},
                        "issuetype": {"name": "Bug"},
                    },
                },
                {
                    "key": "TEST-124",
                    "fields": {
                        "summary": "Test Issue 2",
                        "assignee": {"displayName": "Jane Smith"},
                        "status": {"name": "Done"},
                        "issuetype": {"name": "Task"},
                    },
                },
            ],
            "total": 2,
        }

        result = process_search_response(jira_url, response)

        # Check that the result contains the expected issues
        assert isinstance(result, tuple)
        assert len(result) == 2

        issues_str, total_str = result

        assert "TEST-123" in issues_str
        assert "Test Issue 1" in issues_str
        assert "John Doe" in issues_str
        assert "In Progress" in issues_str
        assert "Bug" in issues_str

        assert "TEST-124" in issues_str
        assert "Test Issue 2" in issues_str
        assert "Jane Smith" in issues_str
        assert "Done" in issues_str
        assert "Task" in issues_str

        assert "Total: 2" in total_str

    def test_process_search_response_error(self):
        """Test processing an error response."""
        jira_url = "https://jira.example.com"

        # Mock response
        response = MagicMock()
        response.status_code = 400
        response.text = "Bad Request"

        result = process_search_response(jira_url, response)

        assert result == "Bad Request"
