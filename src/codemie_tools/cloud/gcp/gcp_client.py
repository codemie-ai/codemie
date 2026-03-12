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
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse

import httpx
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from langchain_core.tools import ToolException


class GCPClient:
    """HTTP client for Google Cloud Platform REST API."""

    # Whitelisted GCP domains
    WHITE_DOMAINS = [".googleapis.com"]

    def __init__(self, service_account_key: str, timeout: int = 30):
        """
        Initialize the GCP client.

        Args:
            service_account_key: Service account key in JSON string format
            timeout: Request timeout in seconds
        """
        self.timeout = timeout
        self.service_account_key = service_account_key
        self.service_account_info = None
        self.project_id = None

    def get_project_id(self) -> str:
        try:
            return self.project_id if self.project_id else self.service_account_info.get("project_id")
        except Exception:
            return "unknown"

    def _get_service_account_info(self) -> Dict[str, Any]:
        # Parse service account key
        if not self.service_account_info:
            try:
                return json.loads(self.service_account_key)
            except json.JSONDecodeError as e:
                raise ToolException(f"Invalid service account key JSON: {str(e)}")
        else:
            return self.service_account_info

    def _validate_domain(self, url: str) -> None:
        """
        Validate that the URL domain is whitelisted for GCP.

        Args:
            url: The URL to validate

        Raises:
            ToolException: If domain is not whitelisted
        """
        domain = urlparse(url).netloc
        if not any(domain.endswith(allowed_domain) for allowed_domain in self.WHITE_DOMAINS):
            raise ToolException(
                f"Domain '{domain}' is not whitelisted for GCP requests. "
                f"Allowed domains: {', '.join(self.WHITE_DOMAINS)}"
            )

    def _get_credentials(self, scopes: List[str]) -> Credentials:
        """
        Get GCP credentials with the specified scopes.

        Args:
            scopes: List of OAuth 2.0 scopes

        Returns:
            Credentials: GCP credentials object

        Raises:
            ToolException: If credential creation fails
        """
        try:
            credentials = Credentials.from_service_account_info(self._get_service_account_info(), scopes=scopes)

            # Refresh the credentials to get a valid token
            auth_request = Request()
            credentials.refresh(auth_request)

            return credentials

        except Exception as e:
            raise ToolException(f"Failed to create GCP credentials: {str(e)}")

    def _validate_scopes(self, scopes: List[str]) -> None:
        """
        Validate that all scope URLs are whitelisted.

        Args:
            scopes: List of OAuth scopes to validate

        Raises:
            ToolException: If any scope URL is not whitelisted
        """
        for scope in scopes:
            # Scopes can be URLs, validate them
            if scope.startswith("http"):
                self._validate_domain(scope)

    def request(self, method: str, scopes: List[str], url: str, optional_args: Optional[Dict[str, Any]] = None) -> str:
        """
        Make an HTTP request to GCP REST API.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            scopes: List of OAuth 2.0 scopes for authentication
            url: Full URL for the request
            optional_args: Optional request parameters (data, json, params, headers, etc.)

        Returns:
            str: Response text or JSON string

        Raises:
            ToolException: If request fails
        """
        # Validate URL and scopes domains
        self._validate_domain(url)
        self._validate_scopes(scopes)

        # Get credentials with specified scopes
        credentials = self._get_credentials(scopes)

        # Prepare headers with OAuth token
        headers = {"Authorization": f"Bearer {credentials.token}"}

        # Merge optional headers if provided
        request_kwargs = optional_args or {}
        if "headers" in request_kwargs:
            request_kwargs["headers"].update(headers)
        else:
            request_kwargs["headers"] = headers

        # Make the request
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.request(method=method.upper(), url=url, **request_kwargs)

                # Check status code
                if response.status_code >= 400:
                    raise ToolException(f"GCP API error: HTTP {response.status_code} - {response.text}")

                # Return response
                if response.text:
                    try:
                        # Try to return as JSON string for better readability
                        return json.dumps(response.json(), indent=2)
                    except json.JSONDecodeError:
                        return response.text
                else:
                    return "Success: The request has been fulfilled and resulted in a new resource being created."

        except httpx.HTTPStatusError as e:
            raise ToolException(f"HTTP {e.response.status_code}: {e.response.text}")
        except httpx.RequestError as e:
            raise ToolException(f"Request failed: {str(e)}")
        except ToolException:
            raise
        except Exception as e:
            raise ToolException(f"Unexpected error: {str(e)}")

    def health_check(self):
        """
        Check if GCP service is accessible by making a test request.

        Raises:
            ToolException: If service is not accessible
        """
        # Use tokeninfo endpoint to validate credentials
        scopes = ["https://www.googleapis.com/auth/cloud-platform"]
        url = "https://www.googleapis.com/oauth2/v3/tokeninfo"
        self.request("GET", scopes, url)
