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

from typing import Dict, Any, Optional
from urllib.parse import urlparse

import httpx
from azure.identity import ClientSecretCredential
from langchain_core.tools import ToolException


class AzureClient:
    """HTTP client for Azure REST API."""

    # Whitelisted Azure domains
    WHITE_DOMAINS = [".azure.com", ".azure.net", ".microsoft.com", ".windows.net", ".microsoftonline.com"]

    def __init__(self, subscription_id: str, tenant_id: str, client_id: str, client_secret: str, timeout: int = 30):
        """
        Initialize the Azure client.

        Args:
            subscription_id: Azure subscription ID
            tenant_id: Azure tenant ID
            client_id: Azure client (application) ID
            client_secret: Azure client secret
            timeout: Request timeout in seconds
        """
        self.subscription_id = subscription_id
        self.tenant_id = tenant_id
        self.timeout = timeout
        self.client_id = client_id
        self.client_secret = client_secret

    def get_credentials(self) -> ClientSecretCredential:
        # Initialize Azure credential
        try:
            return ClientSecretCredential(
                tenant_id=self.tenant_id,
                client_id=self.client_id,
                client_secret=self.client_secret,
            )
        except Exception as e:
            raise ToolException(f"Failed to initialize Azure credentials: {str(e)}")

    def _validate_domain(self, url: str) -> None:
        """
        Validate that the URL domain is whitelisted for Azure.

        Args:
            url: The URL to validate

        Raises:
            ToolException: If domain is not whitelisted
        """
        domain = urlparse(url).netloc
        if not any(domain.endswith(allowed_domain) for allowed_domain in self.WHITE_DOMAINS):
            raise ToolException(
                f"Domain '{domain}' is not whitelisted for Azure requests. "
                f"Allowed domains: {', '.join(self.WHITE_DOMAINS)}"
            )

    def _get_access_token(self, scope: str) -> str:
        """
        Get an access token for the specified scope.

        Args:
            scope: OAuth scope for the token

        Returns:
            str: Access token

        Raises:
            ToolException: If token acquisition fails
        """
        try:
            token = self.get_credentials().get_token(scope)
            return token.token
        except Exception as e:
            raise ToolException(f"Failed to acquire Azure access token: {str(e)}")

    def request(
        self,
        method: str,
        url: str,
        scope: str = "https://management.azure.com/.default",
        optional_args: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Make an HTTP request to Azure REST API.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            url: Full URL for the request
            scope: OAuth scope for authentication
            optional_args: Optional request parameters (data, headers, params, etc.)

        Returns:
            str: Response text

        Raises:
            ToolException: If request fails
        """
        # Validate URL domain
        self._validate_domain(url)

        # Get access token
        token = self._get_access_token(scope)

        # Prepare headers
        headers = {"Authorization": f"Bearer {token}"}

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
                    raise ToolException(f"Azure API error: HTTP {response.status_code} - {response.text}")

                return response.text

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
        Check if Azure service is accessible by listing resource groups.
        """
        if not self.subscription_id:
            raise ToolException("Subscription ID is required.")
        url = f"https://management.azure.com/subscriptions/{self.subscription_id}/resourcegroups?api-version=2021-04-01"
        self.request("GET", url)
