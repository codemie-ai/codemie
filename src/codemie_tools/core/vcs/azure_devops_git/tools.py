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
import logging
from base64 import b64encode
from typing import Type, Any

import re
import requests
from langchain_core.tools import ToolException
from pydantic import BaseModel

from codemie_tools.base.codemie_tool import CodeMieTool
from .models import AzureDevOpsGitConfig, AzureDevOpsGitInput, AzureDevOpsGitOutput
from .tools_vars import AZURE_DEVOPS_GIT_TOOL

logger = logging.getLogger(__name__)

AZURE_DEVOPS_DEFAULT_HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}


class AzureDevOpsGitTool(CodeMieTool):
    name: str = AZURE_DEVOPS_GIT_TOOL.name
    args_schema: Type[BaseModel] = AzureDevOpsGitInput
    config: AzureDevOpsGitConfig
    description: str = AZURE_DEVOPS_GIT_TOOL.description

    def _make_request(
        self, method: str, url: str, headers: dict[str, str], method_arguments: dict | list
    ) -> requests.Response:
        """
        Make HTTP request with appropriate parameters based on method.

        Args:
            method: HTTP method
            url: Request URL
            headers: Request headers
            method_arguments: Request parameters/data, can be a dict or a list.

        Returns:
            Response object
        """
        params = {}
        # Set the default api-version from config
        params["api-version"] = self.config.api_version

        # For GET requests, all method_arguments are treated as query parameters.
        if method == "GET":
            if not isinstance(method_arguments, dict):
                raise ValueError("method_arguments must be a dictionary for GET requests.")
            if "api-version" in method_arguments:
                params["api-version"] = method_arguments.pop("api-version")
            params.update(method_arguments)
            return requests.request(method=method, url=url, headers=headers, params=params)

        # For non-GET requests (POST, PUT, PATCH, etc.), method_arguments is the body.
        else:
            # If the body is a dictionary, check for api-version override
            if isinstance(method_arguments, dict) and "api-version" in method_arguments:
                # Use the specific version and remove it from the body.
                params["api-version"] = method_arguments.pop("api-version")

            return requests.request(
                method=method,
                url=url,
                headers=headers,
                params=params,  # This will contain the api-version
                json=method_arguments,  # The body can be a dict or a list
            )

    def _create_basic_auth_header(self, token: str) -> str:
        """
        Create Basic Auth header for Azure DevOps API.

        Args:
            token: Personal Access Token

        Returns:
            Basic auth header value
        """
        # Azure DevOps uses Basic Authentication with empty username and PAT as password
        credentials = b64encode(f":{token}".encode()).decode()
        return f"Basic {credentials}"

    def _parse_query(self, query: str | dict[str, Any]) -> dict[str, Any]:
        """
        Parse and validate the input query.

        Args:
            query: A string or dictionary containing request details

        Returns:
            Parsed query as dictionary

        Raises:
            ValueError: If query format is invalid
        """
        if isinstance(query, str):
            try:
                query = json.loads(query)
            except json.JSONDecodeError as e:
                raise ValueError(f"Query must be a valid JSON string: {e}")

        return query

    def _validate_and_extract_query_params(self, query: dict) -> tuple[str, str, dict, dict]:
        """
        Validate and extract parameters from the query.

        Args:
            query: Dictionary with request details

        Returns:
            Tuple of (method, relative_url, method_arguments, custom_headers)

        Raises:
            ValueError: If required parameters are missing or invalid
        """
        # Extract and validate method
        method = query.get("method")
        if not method:
            raise ValueError("'method' is required in the query")

        # Extract and validate URL
        relative_url = query.get("url")
        if not relative_url:
            raise ValueError("'url' is required in the query")

        if not relative_url.startswith("/_apis/git/"):
            raise ValueError("URL must start with '/_apis/git/'")

        # Get optional parameters
        method_arguments = query.get("method_arguments", {})
        custom_headers = query.get("custom_headers")

        return method, relative_url, method_arguments, custom_headers

    def _build_full_url(self, relative_url: str, method_arguments: dict) -> tuple[str, dict]:
        """
        Build the full URL for the API request, processing any placeholders.

        Args:
            relative_url: The relative API URL
            method_arguments: Request parameters/body

        Returns:
            Tuple of (full URL string, modified method_arguments)
        """
        # Make a copy to avoid modifying the original
        method_args = method_arguments.copy()

        # Process URL placeholders and extract project if method_args is a dict
        project = self.config.project
        if isinstance(method_args, dict):
            # Universal placeholder replacement: find all {key} in URL
            placeholders = re.findall(r"\{([^}]+)\}", relative_url)
            for key in placeholders:
                if key in method_args:
                    # Substitute the placeholder and remove the key from arguments to avoid duplication
                    value = str(method_args.pop(key))
                    relative_url = relative_url.replace(f"{{{key}}}", value)

            # Extract project if specified in method_arguments (only if it's a string)
            # If it's a dict (like {"id": "..."}) it's part of the request body, not for URL
            # For GET requests, "project" is a string used in url or query params for filtering.
            # For POST requests, the API might require "project" as an object in the body.
            if "project" in method_args:
                project_value = method_args.get("project")
                if isinstance(project_value, str):
                    project = method_args.pop("project")
                # If it's a dict, leave it in method_args for the request body

        # Construct organization URL from url and organization
        base_url = self.config.url.rstrip("/")
        organization = self.config.organization
        organization_url = f"{base_url}/{organization}"

        # Construct the full URL with project if provided
        url = f"{organization_url}/{project}{relative_url}" if project else f"{organization_url}{relative_url}"

        return url, method_args

    def _build_request_headers(self, custom_headers: dict = None) -> dict:
        """
        Build request headers with authorization and custom headers.

        Args:
            custom_headers: Optional dictionary of additional headers

        Returns:
            Dictionary of headers for the request
        """
        # Start with default headers
        headers = AZURE_DEVOPS_DEFAULT_HEADERS.copy()

        # Add authorization header
        headers["Authorization"] = self._create_basic_auth_header(self.config.token)

        # Add custom headers if provided (filtering out protected headers)
        if custom_headers:
            for key, value in custom_headers.items():
                if key.lower() != "authorization":
                    headers[key] = value

        return headers

    def _process_response(self, response: requests.Response, method: str, url: str) -> AzureDevOpsGitOutput:
        """
        Process the API response to a structured output.

        Args:
            response: Response object from the request
            method: HTTP method used
            url: Full URL that was called

        Returns:
            AzureDevOpsGitOutput object with the response data
        """
        success = response.status_code < 400

        try:
            # Try to parse as JSON
            response_body = response.json()
            logger.debug(
                f"HTTP: {method} {url} -> {response.status_code} {response.reason} {json.dumps(response_body, indent=2)}"
            )

            error_msg = None
            if not success:
                error_msg = f"HTTP {response.status_code}: {response.reason}"
                if isinstance(response_body, dict) and "message" in response_body:
                    error_msg += f" - {response_body['message']}"

            return AzureDevOpsGitOutput(
                success=success,
                status_code=response.status_code,
                method=method,
                url=url,
                data=response_body,
                error=error_msg,
            )

        except json.JSONDecodeError:
            # If not JSON, return text content
            logger.debug(f"HTTP: {method} {url} -> {response.status_code} {response.reason} {response.text}")

            error_msg = None if success else f"HTTP {response.status_code}: {response.reason} - Non-JSON response"

            return AzureDevOpsGitOutput(
                success=success,
                status_code=response.status_code,
                method=method,
                url=url,
                data=response.text,
                error=error_msg,
            )

    def execute(self, query: str | dict[str, Any]) -> AzureDevOpsGitOutput:
        """
        Execute an Azure DevOps Git API request based on a query.

        This tool acts as a generic client for the Azure DevOps Git REST API. The query
        parameter specifies the HTTP method, the relative URL, and the arguments for the API call.

        Args:
            query: A dictionary or JSON string containing request details:
                - method (str): The HTTP method (e.g., "GET", "POST").
                - url (str): The relative API URL (must start with "/_apis/git/").
                - method_arguments (dict, optional): Parameters for the request.
                - custom_headers (dict, optional): Custom headers to add to the request.

        Returns:
            An AzureDevOpsGitOutput object with the structured response data, including
            status code, success flag, and response body.

        Raises:
            ValueError: If the input query is invalid or missing required fields.
            ToolException: If the API request fails due to network issues, authentication problems,
                         or other errors during execution.

        Example:
            >>> query = {
            ...     "method": "GET",
            ...     "url": "/_apis/git/repositories",
            ...     "method_arguments": {"project": "MyProjectName"}
            ... }
            >>> tool.execute(query)
        """
        try:
            # Parse and validate query
            parsed_query = self._parse_query(query)
            method, relative_url, method_arguments, custom_headers = self._validate_and_extract_query_params(
                parsed_query
            )

            # Build URL and prepare request data
            url, method_args = self._build_full_url(relative_url, method_arguments)
            headers = self._build_request_headers(custom_headers)

            # Make the request
            response = self._make_request(method, url, headers, method_args)

            # Process the response
            return self._process_response(response, method, url)

        except (TypeError, ValueError) as e:
            logger.error(f"Invalid request parameters: {e}")
            raise ToolException(f"Invalid request parameters: {e}")
        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
            raise ToolException(f"Request failed: {e}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            raise ToolException(f"Unexpected error: {e}")

    def _healthcheck(self):
        """
        Check if the tool can authenticate and connect to Azure DevOps.

        Raises:
            ToolException: If the health check fails.
        """
        try:
            self.execute(
                query={
                    "method": "GET",
                    "url": "/_apis/git/repositories",
                    "method_arguments": {
                        "$top": 1  # Limit to 1 result for efficiency
                    },
                }
            )
        except Exception as e:
            raise ToolException(f"Azure DevOps Git health check failed: {e}")
