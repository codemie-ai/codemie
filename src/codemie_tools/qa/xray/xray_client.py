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

"""Xray Cloud GraphQL API client."""

import logging
from typing import Any, Dict, List, Optional

import httpx
from langchain_core.tools import ToolException

logger = logging.getLogger(__name__)

# GraphQL query to get tests from Xray
GET_TESTS_QUERY = """
query GetTests($jql: String!, $limit: Int!, $start: Int) {
    getTests(jql: $jql, limit: $limit, start: $start) {
        total
        start
        limit
        results {
            issueId
            jira(fields: ["key", "summary"])
            projectId
            testType {
                name
                kind
            }
            steps {
                id
                data
                action
                result
                attachments {
                    id
                    filename
                }
            }
            preconditions(limit: $limit) {
                total
                start
                limit
                results {
                    issueId
                    jira(fields: ["key"])
                    projectId
                }
            }
        }
    }
}
"""


class XrayClient:
    """HTTP client for Xray Cloud GraphQL API.

    Handles authentication and GraphQL operations for Xray test management.
    """

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        limit: int = 100,
        verify_ssl: bool = True,
        timeout: int = 30,
    ):
        """
        Initialize the Xray client.

        Args:
            base_url: Base URL for Xray Cloud instance
            client_id: Client ID for authentication
            client_secret: Client secret for authentication
            limit: Maximum number of results per query
            verify_ssl: Whether to verify SSL certificates
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.limit = limit
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._token: Optional[str] = None
        self._graphql_endpoint = f"{self.base_url}/api/v2/graphql"
        self._auth_endpoint = f"{self.base_url}/api/v2/authenticate"

    def _authenticate(self) -> str:
        """
        Authenticate with Xray API and retrieve access token.

        Returns:
            str: Access token for API requests

        Raises:
            ToolException: If authentication fails
        """
        auth_data = {"client_id": self.client_id, "client_secret": self.client_secret}

        try:
            with httpx.Client(timeout=self.timeout, verify=self.verify_ssl) as client:
                response = client.post(self._auth_endpoint, json=auth_data)
                response.raise_for_status()

                # Response is just the token string
                token = response.text.strip('"')
                logger.info("Successfully authenticated with Xray API")
                return token

        except httpx.HTTPStatusError as e:
            masked_secret = (
                "*" * (len(self.client_secret) - 4) + self.client_secret[-4:] if len(self.client_secret) > 4 else "****"
            )
            raise ToolException(
                f"Authentication failed with status {e.response.status_code}. "
                f"Please verify your credentials (client_id: {self.client_id}, "
                f"client_secret: {masked_secret}). "
                f"Error: {e.response.text}"
            )
        except httpx.RequestError as e:
            raise ToolException(f"Failed to connect to Xray API: {str(e)}")
        except Exception as e:
            raise ToolException(f"Unexpected authentication error: {str(e)}")

    def _get_token(self) -> str:
        """Get valid access token, refreshing if necessary."""
        if not self._token:
            self._token = self._authenticate()
        return self._token

    def _parse_error_message(self, response) -> str:
        """Parse error message from HTTP response."""
        try:
            error_data = response.json()
            return error_data.get("error", response.text)
        except Exception:
            return response.text

    def _is_region_error(self, error_message: str) -> bool:
        """Check if error is related to region mismatch."""
        error_lower = error_message.lower()
        return "region" in error_lower or "migrate" in error_lower

    def _handle_401_error(
        self, response, query: str, variables: Optional[Dict[str, Any]], retry_count: int
    ) -> Dict[str, Any]:
        """Handle 401 Unauthorized errors with retry logic."""
        error_message = self._parse_error_message(response)

        # Check if this is a region error or other non-token issue
        if self._is_region_error(error_message):
            raise ToolException(
                f"Xray API access error: {error_message}. "
                "This appears to be a configuration issue, not an authentication problem."
            )

        # Only retry once for potential token expiration
        if retry_count == 0:
            logger.info("Token expired, refreshing...")
            self._token = None
            return self._execute_graphql(query, variables, _retry_count=1)

        # If we already retried, it's not a token expiration issue
        raise ToolException(f"Authentication failed after token refresh. Error: {error_message}")

    def _execute_graphql(
        self, query: str, variables: Optional[Dict[str, Any]] = None, _retry_count: int = 0
    ) -> Dict[str, Any]:
        """
        Execute a GraphQL query or mutation.

        Args:
            query: GraphQL query or mutation string
            variables: Optional variables for the query
            _retry_count: Internal parameter to track retry attempts

        Returns:
            dict: GraphQL response data

        Raises:
            ToolException: If GraphQL execution fails
        """
        token = self._get_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        try:
            with httpx.Client(timeout=self.timeout, verify=self.verify_ssl) as client:
                response = client.post(self._graphql_endpoint, json=payload, headers=headers)
                response.raise_for_status()
                result = response.json()

                # Check for GraphQL errors
                if "errors" in result:
                    error_messages = [err.get("message", str(err)) for err in result["errors"]]
                    raise ToolException(f"GraphQL errors: {'; '.join(error_messages)}")

                return result.get("data", {})

        except httpx.HTTPStatusError as e:
            # Handle 401 Unauthorized errors
            if e.response.status_code == 401:
                return self._handle_401_error(e.response, query, variables, _retry_count)

            raise ToolException(f"HTTP {e.response.status_code} error executing GraphQL: {e.response.text}")
        except httpx.RequestError as e:
            raise ToolException(f"Request failed: {str(e)}")
        except ToolException:
            raise
        except Exception as e:
            raise ToolException(f"Unexpected error executing GraphQL: {str(e)}")

    def _parse_tests(self, test_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Parse and clean up test results.

        Removes empty preconditions and performs data cleanup.

        Args:
            test_results: Raw test results from GraphQL query

        Returns:
            list: Cleaned test results
        """
        for test_item in test_results:
            # Remove preconditions if empty
            if test_item.get("preconditions", {}).get("total", 0) == 0:
                test_item.pop("preconditions", None)
        return test_results

    def get_tests(self, jql: str, max_results: Optional[int] = None) -> Dict[str, Any]:
        """
        Get tests from Xray using JQL query.

        Handles pagination automatically to retrieve matching tests.

        Args:
            jql: JQL query string to filter tests
            max_results: Maximum number of results to return. If None, fetch all.

        Returns:
            dict: Dictionary with total_tests_count (from API), returned_tests_count, and list of tests

        Raises:
            ToolException: If query execution fails
        """
        start_at = 0
        all_tests = []
        total_tests_count = 0

        logger.info(f"Fetching tests with JQL: {jql}, max_results: {max_results}")

        while True:
            # Calculate how many records to fetch in this page
            page_limit = self.limit
            if max_results is not None:
                remaining = max_results - len(all_tests)
                if remaining <= 0:
                    break
                page_limit = min(self.limit, remaining)

            variables = {"jql": jql, "start": start_at, "limit": page_limit}

            try:
                result = self._execute_graphql(GET_TESTS_QUERY, variables)
                get_tests_response = result.get("getTests", {})

                tests = self._parse_tests(get_tests_response.get("results", []))
                total = get_tests_response.get("total", 0)

                # Capture actual total from first API call
                if start_at == 0:
                    total_tests_count = total

                all_tests.extend(tests)

                logger.info(f"Retrieved {len(all_tests)} of {total} tests")

                # Stop if we've reached max_results
                if max_results is not None and len(all_tests) >= max_results:
                    break

                # Check if more results are available
                if len(all_tests) >= total:
                    break

                start_at += page_limit

            except Exception as e:
                raise ToolException(f"Failed to retrieve tests: {str(e)}")

        return {
            "total_tests_count": total_tests_count,
            "returned_tests_count": len(all_tests),
            "tests": all_tests,
        }

    def create_test(self, graphql_mutation: str) -> Dict[str, Any]:
        """
        Create a new test in Xray using GraphQL mutation.

        Args:
            graphql_mutation: GraphQL mutation string to create test

        Returns:
            dict: Created test data including issue ID and key

        Raises:
            ToolException: If test creation fails
        """
        logger.info("Creating new test in Xray")

        try:
            result = self._execute_graphql(graphql_mutation)

            # Extract test creation result
            create_test_response = result.get("createTest", {})

            if not create_test_response:
                raise ToolException("No createTest response in GraphQL result")

            test_data = create_test_response.get("test", {})
            warnings = create_test_response.get("warnings", [])

            response = {"test": test_data}
            if warnings:
                response["warnings"] = warnings

            logger.info(f"Successfully created test: {test_data.get('jira', {})}")

            return response

        except Exception as e:
            raise ToolException(f"Failed to create test: {str(e)}")

    def execute_custom_graphql(self, graphql: str) -> Dict[str, Any]:
        """
        Execute a custom GraphQL query or mutation.

        Args:
            graphql: Custom GraphQL query or mutation string

        Returns:
            dict: GraphQL execution result

        Raises:
            ToolException: If execution fails
        """
        logger.info("Executing custom GraphQL")

        try:
            result = self._execute_graphql(graphql)
            return result

        except Exception as e:
            raise ToolException(f"Failed to execute custom GraphQL: {str(e)}")

    def health_check(self) -> bool:
        """
        Perform a health check by attempting authentication.

        Returns:
            bool: True if health check succeeds

        Raises:
            ToolException: If health check fails
        """
        try:
            token = self._authenticate()
            return bool(token)
        except Exception as e:
            raise ToolException(f"Health check failed: {str(e)}")
