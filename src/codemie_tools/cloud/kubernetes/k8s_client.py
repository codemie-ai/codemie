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

from typing import Any, Dict, Optional

from kubernetes import client
from langchain_core.tools import ToolException


class KubernetesClient:
    """Client for Kubernetes API."""

    def __init__(self, url: str, token: str, verify_ssl: bool = False):
        """
        Initialize the Kubernetes client.

        Args:
            url: Kubernetes API server URL
            token: Bearer token for authentication
            verify_ssl: Whether to verify SSL certificates
        """
        self.url = url
        self.token = token
        self.verify_ssl = verify_ssl

        self.configuration = client.Configuration()
        self.configuration.host = self.url
        self.configuration.verify_ssl = self.verify_ssl
        self.configuration.api_key = {"authorization": f"Bearer {self.token}"}

        self.__api_client: Optional[client.ApiClient] = None

    @property
    def _client(self) -> client.ApiClient:
        """Get or create Kubernetes API client (lazy initialization)."""
        if self.__api_client is None:
            try:
                self.__api_client = client.ApiClient(self.configuration)
            except Exception as e:
                raise ToolException(f"Failed to initialize Kubernetes client: {str(e)}")
        return self.__api_client

    @_client.setter
    def _client(self, value: client.ApiClient) -> None:
        """Set the Kubernetes API client (useful for testing)."""
        self.__api_client = value

    def call_api(
        self,
        suburl: str,
        method: str,
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Make an API call to Kubernetes.

        Args:
            suburl: Relative API path (must start with /)
            method: HTTP method (GET, POST, PUT, DELETE, PATCH)
            body: Optional request body
            headers: Optional request headers

        Returns:
            str: Response data as string

        Raises:
            ToolException: If API call fails
        """
        try:
            # Validate suburl
            if not suburl.startswith("/"):
                raise ToolException(f"Kubernetes API path must start with '/', got: {suburl}")

            # Prepare call_api parameters
            call_params = {
                "auth_settings": ["BearerToken"],
                "response_type": "json",
                "_preload_content": False,
            }

            if headers:
                call_params["header_params"] = headers

            if body:
                call_params["body"] = body

            # Make the API call
            response = self._client.call_api(suburl, method.upper(), **call_params)

            # Parse response
            # response is a tuple: (response_data, status_code, response_headers)
            response_data = response[0]
            status_code = response[1]

            # Check status code
            if status_code >= 400:
                error_text = response_data.data.decode("utf-8") if response_data.data else ""
                raise ToolException(f"Kubernetes API error: HTTP {status_code} - {error_text}")

            # Return decoded response data
            if response_data and response_data.data:
                return response_data.data.decode("utf-8")
            else:
                return "Success: Operation completed successfully"

        except ToolException:
            raise
        except Exception as e:
            raise ToolException(f"Kubernetes API call failed: {str(e)}")

    def health_check(self):
        """
        Check if Kubernetes cluster is accessible by querying /version endpoint.

        Raises:
            ToolException: If cluster is not accessible
        """
        self.call_api("/version", "GET")
