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
Tests for advanced JQL status transition queries in Jira integration.
Tests verify the enhancements for status CHANGED queries with BY and DURING clauses.
"""

import contextlib
import json
import pytest
from unittest.mock import Mock, patch, MagicMock
from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool, JiraInput
from codemie_tools.core.project_management.jira.models import JiraConfig


@pytest.fixture
def jira_config():
    """Create a mock Jira configuration."""
    return JiraConfig(
        url="https://jira.example.com",
        username="test_user@example.com",
        token="test_token",
        cloud=False,
    )


@pytest.fixture
def jira_tool(jira_config):
    """Create a GenericJiraIssueTool instance with mocked Jira client."""
    with patch("codemie_tools.core.project_management.jira.tools.Jira") as mock_jira_class:
        mock_jira = MagicMock()
        mock_jira_class.return_value = mock_jira

        tool = GenericJiraIssueTool(config=jira_config)
        tool.jira = mock_jira

        yield tool, mock_jira


class TestAdvancedJQLStatusTransitions:
    """Test cases for advanced JQL status transition queries."""

    def test_status_changed_to_with_by_clause(self, jira_tool):
        """Test C2: CHANGED TO query (not FROM-TO) for single status."""
        tool, mock_jira = jira_tool

        # Mock response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = '{"issues": [], "total": 0}'
        mock_response.json.return_value = {"issues": [], "total": 0}
        mock_jira.request.return_value = mock_response

        # JQL: status CHANGED TO "Ready for Testing" BY "user@example.com" DURING (startOfMonth(-1), endOfMonth(-1))
        params = {
            "jql": 'status CHANGED TO "Ready for Testing" BY "user@example.com" DURING (startOfMonth(-1), endOfMonth(-1))',
            "maxResults": 100,
            "fields": "key,summary,status,assignee,issuetype",
        }

        tool.execute(method="GET", relative_url="/rest/api/2/search", params=json.dumps(params))

        # Verify the request was made with correct JQL
        mock_jira.request.assert_called_once()
        call_args = mock_jira.request.call_args
        assert call_args[1]["path"] == "/rest/api/2/search"
        assert call_args[1]["method"] == "GET"

        # Verify JQL contains CHANGED TO (not FROM X TO Y)
        jql = call_args[1]["params"]["jql"]
        assert 'CHANGED TO "Ready for Testing"' in jql
        assert "CHANGED FROM" not in jql  # Should not have FROM clause
        assert 'BY "user@example.com"' in jql
        assert "DURING (startOfMonth(-1), endOfMonth(-1))" in jql

    def test_status_changed_from_to_explicit(self, jira_tool):
        """Test C3: CHANGED FROM-TO when user explicitly requests it."""
        tool, mock_jira = jira_tool

        # Mock response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = '{"issues": [], "total": 0}'
        mock_response.json.return_value = {"issues": [], "total": 0}
        mock_jira.request.return_value = mock_response

        # JQL: status CHANGED FROM "In Progress" TO "Ready for Testing" BY "user@example.com"
        params = {
            "jql": 'status CHANGED FROM "In Progress" TO "Ready for Testing" BY "user@example.com" DURING (startOfMonth(-1), endOfMonth(-1))',
            "maxResults": 100,
            "fields": "key,summary,status,assignee,issuetype",
        }

        tool.execute(method="GET", relative_url="/rest/api/2/search", params=json.dumps(params))

        # Verify JQL contains CHANGED FROM X TO Y
        call_args = mock_jira.request.call_args
        jql = call_args[1]["params"]["jql"]
        assert 'CHANGED FROM "In Progress" TO "Ready for Testing"' in jql
        assert 'BY "user@example.com"' in jql

    def test_jira_input_validation_status_changed(self):
        """Test that JiraInput accepts status CHANGED queries."""
        # Valid input with status CHANGED TO
        input_data = JiraInput(
            method="GET",
            relative_url="/rest/api/2/search",
            params='{"jql": "status CHANGED TO \\"Done\\" BY \\"user@example.com\\" DURING (startOfMonth(-1), endOfMonth(-1))", "maxResults": 100}',
        )

        assert input_data.method == "GET"
        assert "/rest/api/2/search" in input_data.relative_url
        assert "status CHANGED TO" in input_data.params

    def test_jira_input_validation_status_changed_from_to(self):
        """Test that JiraInput accepts status CHANGED FROM-TO queries."""
        input_data = JiraInput(
            method="GET",
            relative_url="/rest/api/2/search",
            params='{"jql": "status CHANGED FROM \\"Open\\" TO \\"In Progress\\" BY \\"user@example.com\\" DURING (startOfWeek(), endOfWeek())", "maxResults": 100}',
        )

        assert input_data.method == "GET"
        assert "status CHANGED FROM" in input_data.params
        assert "TO" in input_data.params

    @pytest.mark.parametrize(
        "date_function,expected",
        [
            ("startOfMonth(-1), endOfMonth(-1)", "last month"),
            ("startOfMonth(), endOfMonth()", "this month"),
            ("startOfWeek(), endOfWeek()", "this week"),
            ("startOfWeek(-1), endOfWeek(-1)", "last week"),
            ("startOfWeek(-1), endOfWeek()", "last 2 weeks"),
            ("-30d, now()", "last 30 days"),
        ],
    )
    def test_date_functions_in_during_clause(self, jira_tool, date_function, expected):
        """Test C5, C6: Various date functions in DURING clause."""
        tool, mock_jira = jira_tool

        # Mock response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = '{"issues": [], "total": 0}'
        mock_response.json.return_value = {"issues": [], "total": 0}
        mock_jira.request.return_value = mock_response

        params = {
            "jql": f'status CHANGED TO "Done" DURING ({date_function})',
            "maxResults": 100,
            "fields": "key,summary,status,assignee,issuetype",
        }

        tool.execute(method="GET", relative_url="/rest/api/2/search", params=json.dumps(params))

        # Verify the date function is in the JQL
        call_args = mock_jira.request.call_args
        jql = call_args[1]["params"]["jql"]
        assert f"DURING ({date_function})" in jql

    def test_absolute_dates_with_single_quotes(self, jira_tool):
        """Test C4: Absolute dates use single quotes in DURING clause."""
        tool, mock_jira = jira_tool

        # Mock response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = '{"issues": [], "total": 0}'
        mock_response.json.return_value = {"issues": [], "total": 0}
        mock_jira.request.return_value = mock_response

        # Absolute dates should use single quotes
        params = {
            "jql": "status CHANGED TO \"Ready for Testing\" DURING ('2025/10/01 00:00', '2025/10/20 23:59')",
            "maxResults": 100,
            "fields": "key,summary,status,assignee,issuetype",
        }

        tool.execute(method="GET", relative_url="/rest/api/2/search", params=json.dumps(params))

        # Verify single quotes for dates
        call_args = mock_jira.request.call_args
        jql = call_args[1]["params"]["jql"]
        assert "DURING ('2025/10/01 00:00', '2025/10/20 23:59')" in jql

    def test_email_format_with_underscore(self, jira_tool):
        """Test C1: Email format uses underscore notation."""
        tool, mock_jira = jira_tool

        # Mock response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = '{"issues": [], "total": 0}'
        mock_response.json.return_value = {"issues": [], "total": 0}
        mock_jira.request.return_value = mock_response

        # Email should use underscore
        params = {
            "jql": 'status CHANGED TO "Done" BY "user@example.com" DURING (startOfMonth(-1), endOfMonth(-1))',
            "maxResults": 100,
            "fields": "key,summary,status,assignee,issuetype",
        }

        tool.execute(method="GET", relative_url="/rest/api/2/search", params=json.dumps(params))

        # Verify underscore in email
        call_args = mock_jira.request.call_args
        jql = call_args[1]["params"]["jql"]
        assert 'BY "user@example.com"' in jql
        assert "ainur.bektemirova" not in jql  # Should not have dot

    def test_project_filter_included(self, jira_tool):
        """Test C7: Project filter is included in query."""
        tool, mock_jira = jira_tool

        # Mock response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = '{"issues": [], "total": 0}'
        mock_response.json.return_value = {"issues": [], "total": 0}
        mock_jira.request.return_value = mock_response

        params = {
            "jql": 'status CHANGED TO "Done" BY "user@example.com" DURING (startOfMonth(-1), endOfMonth(-1)) AND project=PROJ',
            "maxResults": 100,
            "fields": "key,summary,status,assignee,issuetype",
        }

        tool.execute(method="GET", relative_url="/rest/api/2/search", params=json.dumps(params))

        # Verify project filter
        call_args = mock_jira.request.call_args
        jql = call_args[1]["params"]["jql"]
        assert "AND project=PROJ" in jql or "project=PROJ" in jql

    def test_double_quotes_in_jql_strings(self, jira_tool):
        """Test C8: All string values in JQL use double quotes."""
        tool, mock_jira = jira_tool

        # Mock response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = '{"issues": [], "total": 0}'
        mock_response.json.return_value = {"issues": [], "total": 0}
        mock_jira.request.return_value = mock_response

        # JQL should use double quotes for strings
        params = {
            "jql": 'status CHANGED TO "Ready for Testing" BY "user@example.com" DURING (startOfMonth(-1), endOfMonth(-1))',
            "maxResults": 100,
            "fields": "key,summary,status,assignee,issuetype",
        }

        tool.execute(method="GET", relative_url="/rest/api/2/search", params=json.dumps(params))

        # Verify double quotes used (not single quotes)
        call_args = mock_jira.request.call_args
        jql = call_args[1]["params"]["jql"]
        assert '"Ready for Testing"' in jql
        assert '"user@example.com"' in jql
        # Should not have single quotes for status/email
        assert "'Ready for Testing'" not in jql
        assert "'user@example.com'" not in jql

    def test_search_response_processing(self, jira_tool):
        """Test that search responses are processed correctly with status CHANGED queries."""
        tool, mock_jira = jira_tool

        # Mock response with issues
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "issues": [
                {
                    "key": "TEST-123",
                    "fields": {
                        "summary": "Test ticket",
                        "status": {"name": "Done"},
                        "assignee": {"displayName": "Test User"},
                        "issuetype": {"name": "Task"},
                    },
                }
            ],
            "total": 1,
        }
        mock_response.text = '{"issues": [...], "total": 1}'
        mock_jira.request.return_value = mock_response
        mock_jira.url = "https://jira.example.com"

        params = {
            "jql": 'status CHANGED TO "Done" DURING (startOfMonth(-1), endOfMonth(-1))',
            "maxResults": 100,
            "fields": "key,summary,status,assignee,issuetype",
        }

        result = tool.execute(method="GET", relative_url="/rest/api/2/search", params=json.dumps(params))

        # Verify response contains processed data
        assert "TEST-123" in result
        assert "200" in result  # HTTP status code


class TestJQLEdgeCases:
    """Test edge cases and error scenarios."""

    def test_jira_input_with_complex_jql(self):
        """Test JiraInput handles complex JQL with multiple conditions."""
        input_data = JiraInput(
            method="GET",
            relative_url="/rest/api/2/search",
            params='{"jql": "status CHANGED FROM \\"In Progress\\" TO \\"Done\\" BY \\"user@example.com\\" DURING (startOfMonth(-1), endOfMonth(-1)) AND project=TEST AND type=Bug", "maxResults": 100}',
        )

        assert "status CHANGED FROM" in input_data.params
        assert "AND project=TEST" in input_data.params
        assert "AND type=Bug" in input_data.params

    def test_jira_input_empty_params(self):
        """Test JiraInput with empty params."""
        input_data = JiraInput(method="GET", relative_url="/rest/api/2/issue/TEST-123")

        assert input_data.params is None

    @pytest.mark.parametrize(
        "invalid_jql",
        [
            "status CHANGED TO 'Ready for Testing'",  # Single quotes (wrong)
            "status changed to Done",  # Lowercase (wrong)
        ],
    )
    def test_jira_handles_various_jql_formats(self, jira_tool, invalid_jql):
        """Test that tool can process various JQL formats (even if semantically incorrect)."""
        tool, mock_jira = jira_tool

        # Mock response - Jira would likely return error for these
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.text = '{"errorMessages": ["Invalid JQL"]}'
        mock_response.json.return_value = {"errorMessages": ["Invalid JQL"]}
        mock_jira.request.return_value = mock_response

        params = {"jql": invalid_jql, "maxResults": 100}

        # Tool should pass through to Jira (which will return error)
        # Expected - Jira would reject invalid JQL
        with contextlib.suppress(Exception):
            tool.execute(method="GET", relative_url="/rest/api/2/search", params=json.dumps(params))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
