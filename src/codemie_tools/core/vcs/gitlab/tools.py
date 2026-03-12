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
from typing import Type, Any, Union

import requests
from langchain_core.tools import ToolException
from pydantic import BaseModel, Field

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.core.vcs.utils import _build_headers
from .models import GitlabConfig
from .tools_vars import GITLAB_TOOL

logger = logging.getLogger(__name__)

GITLAB_DEFAULT_HEADERS = {"Accept": "application/json"}


class GitlabInput(BaseModel):
    query: Union[str, dict[str, Any]] = Field(
        description="""
        JSON containing the GitLab API request specification. Must be valid JSON with no comments allowed.

        Required JSON structure:
        {
            "method": "GET|POST|PUT|DELETE|PATCH",
            "url": "/api/v4/...",
            "method_arguments": {request_parameters_or_body_data}
        }

        Optional with custom headers:
        {
            "method": "GET|POST|PUT|DELETE|PATCH",
            "url": "/api/v4/...",
            "method_arguments": {request_parameters_or_body_data},
            "custom_headers": {additional_http_headers}
        }

        Field Requirements:
        - method: HTTP method (GET, POST, PUT, DELETE, PATCH) - REQUIRED
        - url: GitLab API endpoint starting with "/api/v4/" (relative to GitLab server) - REQUIRED
        - method_arguments: Object with request data - REQUIRED (can be empty {})
        - custom_headers: Optional dictionary of additional HTTP headers - OPTIONAL

        Important Notes:
        - GitLab Personal Access Token is automatically added to Authorization header
        - custom_headers cannot override authorization headers (protected for security)
        - GET requests: method_arguments sent as query parameters
        - POST/PUT/DELETE/PATCH requests: method_arguments sent as request body data
        - Response is formatted string: "HTTP: {method} {url} -> {status} {reason} {body}"
        - The entire query must pass json.loads() validation

        Examples:
        Get user: {"method": "GET", "url": "/api/v4/user", "method_arguments": {}}
        List issues: {"method": "GET", "url": "/api/v4/projects/123/issues", "method_arguments": {"state": "opened"}}
        Create MR: {"method": "POST", "url": "/api/v4/projects/123/merge_requests", "method_arguments": {"source_branch": "feature", "target_branch": "main", "title": "New feature"}}
        """
    )


class GitlabTool(CodeMieTool):
    name: str = GITLAB_TOOL.name
    args_schema: Type[BaseModel] = GitlabInput
    config: GitlabConfig
    description: str = GITLAB_TOOL.description

    # High value to support large source files.
    tokens_size_limit: int = 70_000

    def _make_request(
        self, method: str, url: str, headers: dict[str, str], method_arguments: dict
    ) -> requests.Response:
        """
        Make HTTP request with appropriate parameters based on method.

        Args:
            method: HTTP method
            url: Request URL
            headers: Request headers
            method_arguments: Request parameters/data

        Returns:
            Response object
        """
        if method == "GET":
            return requests.request(method=method, url=url, headers=headers, params=method_arguments)
        else:
            return requests.request(method=method, url=url, headers=headers, data=method_arguments)

    def execute(self, query: Union[str, dict[str, Any]], *args) -> str:
        """
        Execute GitLab API request with optional custom headers.

        Args:
            query: JSON containing request details
            *args: Additional arguments

        Returns:
            String response from GitLab API

        Raises:
            ToolException: If credentials are missing or request fails
        """
        try:
            if isinstance(query, str):
                query = json.loads(query)
        except json.JSONDecodeError as e:
            raise ValueError(f"Query must be a JSON string: {e}")

        try:
            method = query.get('method')
            url = f"{self.config.url}/{query.get('url')}"
            method_arguments = query.get("method_arguments", {})

            custom_headers = query.get('custom_headers')
            headers = _build_headers(GITLAB_DEFAULT_HEADERS, self.config.token, custom_headers)
            response = self._make_request(method, url, headers, method_arguments)

            response_string = f"HTTP: {method} {url} -> {response.status_code} {response.reason} {response.text}"
            logger.debug(response_string)
            return response_string

        except (TypeError, json.JSONDecodeError) as e:
            logger.error(f"Failed to parse GitLab response: {e}")
            raise ToolException(f"Failed to parse GitLab response: {e}")
