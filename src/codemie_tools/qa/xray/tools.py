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

"""Xray Cloud tools for test management."""

import json
from typing import Dict, List, Optional, Type

from langchain_core.tools import ToolException
from pydantic import BaseModel

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.qa.xray.models import XrayConfig, XrayGetTestsInput, XrayCreateTestInput, XrayExecuteGraphQLInput
from codemie_tools.qa.xray.tools_vars import XRAY_GET_TESTS_TOOL, XRAY_CREATE_TEST_TOOL, XRAY_EXECUTE_GRAPHQL_TOOL
from codemie_tools.qa.xray.xray_client import XrayClient

# Error message constants
ERROR_CONFIG_NOT_PROVIDED = "Xray config is not provided"
ERROR_CONFIG_NOT_SET = "Xray config is not provided. Please set it before using the tool."


class XrayGetTestsTool(CodeMieTool):
    """Tool for retrieving test cases from Xray Cloud using JQL queries."""

    config: Optional[XrayConfig] = None
    name: str = XRAY_GET_TESTS_TOOL.name
    description: str = XRAY_GET_TESTS_TOOL.description
    args_schema: Type[BaseModel] = XrayGetTestsInput

    def _healthcheck(self):
        """Perform health check by verifying authentication."""
        if not self.config:
            raise ToolException(ERROR_CONFIG_NOT_PROVIDED)

        client = self._create_client()
        client.health_check()

    def _create_client(self) -> XrayClient:
        """Create and return XrayClient instance."""
        return XrayClient(
            base_url=self.config.base_url,
            client_id=self.config.client_id,
            client_secret=self.config.client_secret,
            limit=self.config.limit,
            verify_ssl=self.config.verify_ssl,
        )

    def _format_test_item(self, test: Dict) -> List[str]:
        """Format a single test item for display."""
        lines = []
        jira_info = test.get("jira", {})
        test_type = test.get("testType", {}).get("name", "Unknown")
        issue_key = jira_info.get("key", "N/A") if isinstance(jira_info, dict) else jira_info
        summary = jira_info.get("summary", "") if isinstance(jira_info, dict) else ""

        lines.append(f"  - [{issue_key}] {summary} (Type: {test_type})")

        # Include step count for manual tests
        if test_type == "Manual" and "steps" in test:
            step_count = len(test["steps"])
            lines.append(f"    Steps: {step_count}")

        return lines

    def execute(self, jql: str, max_results: Optional[int] = None) -> str:
        """
        Retrieve test cases from Xray using JQL query.

        Args:
            jql: JQL query string to filter tests
            max_results: Maximum number of results to return (optional)

        Returns:
            str: Formatted string with test count and test details

        Raises:
            ToolException: If config is missing or query execution fails
        """
        if not self.config:
            raise ToolException(ERROR_CONFIG_NOT_SET)

        try:
            client = self._create_client()
            result = client.get_tests(jql, max_results=max_results)

            total = result.get("total", 0)
            tests = result.get("tests", [])

            # Format the response
            response_lines = [f"Retrieved {total} test(s) matching query: {jql}"]

            if tests:
                response_lines.append("\nTests:")
                for test in tests:
                    response_lines.extend(self._format_test_item(test))

                # Add full test details as JSON for reference
                response_lines.append("\nFull test details:")
                response_lines.append(json.dumps(tests, indent=2))
            else:
                response_lines.append("\nNo tests found matching the query.")

            return "\n".join(response_lines)

        except Exception as e:
            raise ToolException(f"Failed to retrieve tests: {str(e)}")


class XrayCreateTestTool(CodeMieTool):
    """Tool for creating new test cases in Xray Cloud using GraphQL mutations."""

    config: Optional[XrayConfig] = None
    name: str = XRAY_CREATE_TEST_TOOL.name
    description: str = XRAY_CREATE_TEST_TOOL.description
    args_schema: Type[BaseModel] = XrayCreateTestInput

    def _healthcheck(self):
        """Perform health check by verifying authentication."""
        if not self.config:
            raise ToolException(ERROR_CONFIG_NOT_PROVIDED)

        client = self._create_client()
        client.health_check()

    def _create_client(self) -> XrayClient:
        """Create and return XrayClient instance."""
        return XrayClient(
            base_url=self.config.base_url,
            client_id=self.config.client_id,
            client_secret=self.config.client_secret,
            limit=self.config.limit,
            verify_ssl=self.config.verify_ssl,
        )

    def _format_test_details(self, test: Dict) -> List[str]:
        """Format test details for display."""
        lines = []
        jira_info = test.get("jira", {})
        test_type = test.get("testType", {}).get("name", "Unknown")
        issue_id = test.get("issueId", "N/A")
        issue_key = jira_info.get("key", "N/A") if isinstance(jira_info, dict) else jira_info

        lines.append("\nTest Details:")
        lines.append(f"  - Issue ID: {issue_id}")
        lines.append(f"  - Issue Key: {issue_key}")
        lines.append(f"  - Test Type: {test_type}")

        # Add type-specific details
        if "steps" in test and test["steps"]:
            lines.append(f"  - Steps: {len(test['steps'])}")
        elif "unstructured" in test:
            lines.append(f"  - Description: {test['unstructured'][:100]}...")
        elif "gherkin" in test:
            lines.append("  - Type: Cucumber (Gherkin)")

        return lines

    def execute(self, graphql_mutation: str) -> str:
        """
        Create a new test case in Xray using GraphQL mutation.

        Args:
            graphql_mutation: GraphQL mutation string to create test

        Returns:
            str: Formatted string with created test details

        Raises:
            ToolException: If config is missing or test creation fails
        """
        if not self.config:
            raise ToolException(ERROR_CONFIG_NOT_SET)

        try:
            client = self._create_client()
            result = client.create_test(graphql_mutation)

            test = result.get("test", {})
            warnings = result.get("warnings", [])

            # Format the response
            response_lines = ["Test created successfully!"]

            if test:
                response_lines.extend(self._format_test_details(test))

            if warnings:
                response_lines.append("\nWarnings:")
                for warning in warnings:
                    response_lines.append(f"  - {warning}")

            # Add full response as JSON for reference
            response_lines.append("\nFull response:")
            response_lines.append(json.dumps(result, indent=2))

            return "\n".join(response_lines)

        except Exception as e:
            raise ToolException(f"Failed to create test: {str(e)}")


class XrayExecuteGraphQLTool(CodeMieTool):
    """Tool for executing custom GraphQL queries and mutations against Xray Cloud API."""

    config: Optional[XrayConfig] = None
    name: str = XRAY_EXECUTE_GRAPHQL_TOOL.name
    description: str = XRAY_EXECUTE_GRAPHQL_TOOL.description
    args_schema: Type[BaseModel] = XrayExecuteGraphQLInput

    def _healthcheck(self):
        """Perform health check by verifying authentication."""
        if not self.config:
            raise ToolException(ERROR_CONFIG_NOT_PROVIDED)

        client = self._create_client()
        client.health_check()

    def _create_client(self) -> XrayClient:
        """Create and return XrayClient instance."""
        return XrayClient(
            base_url=self.config.base_url,
            client_id=self.config.client_id,
            client_secret=self.config.client_secret,
            limit=self.config.limit,
            verify_ssl=self.config.verify_ssl,
        )

    def execute(self, graphql: str) -> str:
        """
        Execute custom GraphQL query or mutation.

        Args:
            graphql: Custom GraphQL query or mutation string

        Returns:
            str: Formatted string with GraphQL execution result

        Raises:
            ToolException: If config is missing or execution fails
        """
        if not self.config:
            raise ToolException(ERROR_CONFIG_NOT_SET)

        try:
            client = self._create_client()
            result = client.execute_custom_graphql(graphql)

            # Format the response
            response_lines = ["GraphQL executed successfully!"]
            response_lines.append("\nResult:")
            response_lines.append(json.dumps(result, indent=2))

            return "\n".join(response_lines)

        except Exception as e:
            raise ToolException(f"Failed to execute GraphQL: {str(e)}")
