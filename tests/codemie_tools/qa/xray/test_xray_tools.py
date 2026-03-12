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

"""Unit tests for Xray tools."""

import pytest
from unittest.mock import patch, Mock
from langchain_core.tools import ToolException

from codemie_tools.qa.xray.models import XrayConfig
from codemie_tools.qa.xray.tools import XrayGetTestsTool, XrayCreateTestTool, XrayExecuteGraphQLTool


class TestXrayGetTestsTool:
    """Test cases for XrayGetTestsTool."""

    def test_tool_name(self):
        """Test that tool name is set correctly."""
        tool = XrayGetTestsTool()
        assert tool.name == "XrayGetTests"

    def test_execute_no_config(self):
        """Test execute without config raises error."""
        tool = XrayGetTestsTool()

        with pytest.raises(ToolException) as exc_info:
            tool.execute(jql='project = "CALC"')

        assert "config is not provided" in str(exc_info.value)

    @patch("codemie_tools.qa.xray.tools.XrayClient")
    def test_execute_with_config(self, mock_client_class):
        """Test execute with valid config."""
        # Mock client and its methods
        mock_client = Mock()
        mock_client.get_tests.return_value = {
            "total": 2,
            "tests": [
                {
                    "issueId": "12345",
                    "jira": {"key": "CALC-1", "summary": "Test 1"},
                    "testType": {"name": "Manual"},
                    "steps": [{"action": "Step 1"}],
                },
                {"issueId": "12346", "jira": {"key": "CALC-2", "summary": "Test 2"}, "testType": {"name": "Generic"}},
            ],
        }
        mock_client_class.return_value = mock_client

        config = XrayConfig(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")
        tool = XrayGetTestsTool(config=config)

        result = tool.execute(jql='project = "CALC"')

        assert "Retrieved 2 test(s)" in result
        assert "CALC-1" in result
        assert "CALC-2" in result
        assert "Manual" in result
        mock_client.get_tests.assert_called_once_with('project = "CALC"', max_results=None)

    @patch("codemie_tools.qa.xray.tools.XrayClient")
    def test_execute_no_tests_found(self, mock_client_class):
        """Test execute when no tests are found."""
        mock_client = Mock()
        mock_client.get_tests.return_value = {"total": 0, "tests": []}
        mock_client_class.return_value = mock_client

        config = XrayConfig(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")
        tool = XrayGetTestsTool(config=config)

        result = tool.execute(jql='project = "NOTFOUND"')

        assert "Retrieved 0 test(s)" in result
        assert "No tests found" in result

    @patch("codemie_tools.qa.xray.tools.XrayClient")
    def test_execute_client_error(self, mock_client_class):
        """Test execute when client raises an error."""
        mock_client = Mock()
        mock_client.get_tests.side_effect = Exception("API Error")
        mock_client_class.return_value = mock_client

        config = XrayConfig(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")
        tool = XrayGetTestsTool(config=config)

        with pytest.raises(ToolException) as exc_info:
            tool.execute(jql='project = "CALC"')

        assert "Failed to retrieve tests" in str(exc_info.value)

    @patch("codemie_tools.qa.xray.tools.XrayClient")
    def test_healthcheck_success(self, mock_client_class):
        """Test successful healthcheck."""
        mock_client = Mock()
        mock_client.health_check.return_value = True
        mock_client_class.return_value = mock_client

        config = XrayConfig(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")
        tool = XrayGetTestsTool(config=config)

        # Should not raise exception
        tool._healthcheck()
        mock_client.health_check.assert_called_once()

    def test_healthcheck_no_config(self):
        """Test healthcheck without config."""
        tool = XrayGetTestsTool()

        with pytest.raises(ToolException):
            tool._healthcheck()


class TestXrayCreateTestTool:
    """Test cases for XrayCreateTestTool."""

    def test_tool_name(self):
        """Test that tool name is set correctly."""
        tool = XrayCreateTestTool()
        assert tool.name == "XrayCreateTest"

    def test_execute_no_config(self):
        """Test execute without config raises error."""
        tool = XrayCreateTestTool()

        with pytest.raises(ToolException) as exc_info:
            tool.execute(graphql_mutation="mutation { ... }")

        assert "config is not provided" in str(exc_info.value)

    @patch("codemie_tools.qa.xray.tools.XrayClient")
    def test_execute_with_config(self, mock_client_class):
        """Test execute with valid config."""
        mock_client = Mock()
        mock_client.create_test.return_value = {
            "test": {
                "issueId": "12345",
                "jira": {"key": "CALC-1", "summary": "New Test"},
                "testType": {"name": "Manual"},
                "steps": [{"action": "Step 1", "result": "Result 1"}],
            },
            "warnings": [],
        }
        mock_client_class.return_value = mock_client

        config = XrayConfig(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")
        tool = XrayCreateTestTool(config=config)

        mutation = """
        mutation {
            createTest(testType: { name: "Manual" })
        }
        """
        result = tool.execute(graphql_mutation=mutation)

        assert "Test created successfully!" in result
        assert "Issue ID: 12345" in result
        assert "Issue Key: CALC-1" in result
        assert "Test Type: Manual" in result
        assert "Steps: 1" in result
        mock_client.create_test.assert_called_once_with(mutation)

    @patch("codemie_tools.qa.xray.tools.XrayClient")
    def test_execute_with_warnings(self, mock_client_class):
        """Test execute with warnings in response."""
        mock_client = Mock()
        mock_client.create_test.return_value = {
            "test": {
                "issueId": "12345",
                "jira": {"key": "CALC-1"},
                "testType": {"name": "Generic"},
                "unstructured": "Test description",
            },
            "warnings": ["Warning: Some fields were ignored"],
        }
        mock_client_class.return_value = mock_client

        config = XrayConfig(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")
        tool = XrayCreateTestTool(config=config)

        result = tool.execute(graphql_mutation="mutation { ... }")

        assert "Test created successfully!" in result
        assert "Warnings:" in result
        assert "Some fields were ignored" in result

    @patch("codemie_tools.qa.xray.tools.XrayClient")
    def test_execute_client_error(self, mock_client_class):
        """Test execute when client raises an error."""
        mock_client = Mock()
        mock_client.create_test.side_effect = Exception("Creation failed")
        mock_client_class.return_value = mock_client

        config = XrayConfig(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")
        tool = XrayCreateTestTool(config=config)

        with pytest.raises(ToolException) as exc_info:
            tool.execute(graphql_mutation="mutation { ... }")

        assert "Failed to create test" in str(exc_info.value)

    @patch("codemie_tools.qa.xray.tools.XrayClient")
    def test_healthcheck_success(self, mock_client_class):
        """Test successful healthcheck."""
        mock_client = Mock()
        mock_client.health_check.return_value = True
        mock_client_class.return_value = mock_client

        config = XrayConfig(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")
        tool = XrayCreateTestTool(config=config)

        # Should not raise exception
        tool._healthcheck()
        mock_client.health_check.assert_called_once()


class TestXrayExecuteGraphQLTool:
    """Test cases for XrayExecuteGraphQLTool."""

    def test_tool_name(self):
        """Test that tool name is set correctly."""
        tool = XrayExecuteGraphQLTool()
        assert tool.name == "XrayExecuteGraphQL"

    def test_execute_no_config(self):
        """Test execute without config raises error."""
        tool = XrayExecuteGraphQLTool()

        with pytest.raises(ToolException) as exc_info:
            tool.execute(graphql="query { ... }")

        assert "config is not provided" in str(exc_info.value)

    @patch("codemie_tools.qa.xray.tools.XrayClient")
    def test_execute_query_with_config(self, mock_client_class):
        """Test execute query with valid config."""
        mock_client = Mock()
        mock_client.execute_custom_graphql.return_value = {"getTests": {"total": 5, "results": [{"issueId": "123"}]}}
        mock_client_class.return_value = mock_client

        config = XrayConfig(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")
        tool = XrayExecuteGraphQLTool(config=config)

        query = "query { getTests { total } }"
        result = tool.execute(graphql=query)

        assert "GraphQL executed successfully!" in result
        assert "getTests" in result
        mock_client.execute_custom_graphql.assert_called_once_with(query)

    @patch("codemie_tools.qa.xray.tools.XrayClient")
    def test_execute_mutation_with_config(self, mock_client_class):
        """Test execute mutation with valid config."""
        mock_client = Mock()
        mock_client.execute_custom_graphql.return_value = {"updateTest": {"test": {"issueId": "12345"}}}
        mock_client_class.return_value = mock_client

        config = XrayConfig(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")
        tool = XrayExecuteGraphQLTool(config=config)

        mutation = "mutation { updateTest(...) { test { issueId } } }"
        result = tool.execute(graphql=mutation)

        assert "GraphQL executed successfully!" in result
        assert "updateTest" in result

    @patch("codemie_tools.qa.xray.tools.XrayClient")
    def test_execute_client_error(self, mock_client_class):
        """Test execute when client raises an error."""
        mock_client = Mock()
        mock_client.execute_custom_graphql.side_effect = Exception("Execution failed")
        mock_client_class.return_value = mock_client

        config = XrayConfig(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")
        tool = XrayExecuteGraphQLTool(config=config)

        with pytest.raises(ToolException) as exc_info:
            tool.execute(graphql="query { ... }")

        assert "Failed to execute GraphQL" in str(exc_info.value)

    @patch("codemie_tools.qa.xray.tools.XrayClient")
    def test_healthcheck_success(self, mock_client_class):
        """Test successful healthcheck."""
        mock_client = Mock()
        mock_client.health_check.return_value = True
        mock_client_class.return_value = mock_client

        config = XrayConfig(base_url="https://xray.cloud.getxray.app", client_id="test_id", client_secret="test_secret")
        tool = XrayExecuteGraphQLTool(config=config)

        # Should not raise exception
        tool._healthcheck()
        mock_client.health_check.assert_called_once()

    def test_healthcheck_no_config(self):
        """Test healthcheck without config."""
        tool = XrayExecuteGraphQLTool()

        with pytest.raises(ToolException):
            tool._healthcheck()
