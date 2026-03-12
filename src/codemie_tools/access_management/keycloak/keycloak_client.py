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

import httpx
from langchain_core.tools import ToolException


class KeycloakClient:
    """HTTP client for Keycloak Admin API."""

    def __init__(self, base_url: str, realm: str, client_id: str, client_secret: str, timeout: int = 30):
        """
        Initialize the Keycloak client.

        Args:
            base_url: Base URL of the Keycloak server
            realm: Keycloak realm name
            client_id: Client ID for authentication
            client_secret: Client secret for authentication
            timeout: Request timeout in seconds
        """
        self.base_url = base_url.rstrip('/')
        self.realm = realm
        self.client_id = client_id
        self.client_secret = client_secret
        self.timeout = timeout
        self._access_token: Optional[str] = None

    def _get_admin_token(self) -> str:
        """
        Obtain admin access token from Keycloak.

        Returns:
            str: Access token for admin API calls

        Raises:
            ToolException: If token retrieval fails
        """
        url = f"{self.base_url}/realms/{self.realm}/protocol/openid-connect/token"
        payload = {'client_id': self.client_id, 'client_secret': self.client_secret, 'grant_type': 'client_credentials'}

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, data=payload)
                response.raise_for_status()
                return response.json()['access_token']

        except httpx.HTTPStatusError as e:
            raise ToolException(f"Failed to obtain admin token - HTTP {e.response.status_code}: {e.response.text}")
        except httpx.RequestError as e:
            raise ToolException(f"Failed to obtain admin token - Request failed: {str(e)}")
        except KeyError:
            raise ToolException("Failed to obtain admin token - Invalid response format")
        except Exception as e:
            raise ToolException(f"Failed to obtain admin token - Unexpected error: {str(e)}")

    def _request(self, method: str, endpoint: str, json_data: Optional[Dict[str, Any]] = None) -> str:
        """
        Make an HTTP request to Keycloak Admin API.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            endpoint: API endpoint (relative URL starting with '/')
            json_data: Optional JSON data for request body

        Returns:
            str: Response text

        Raises:
            ToolException: If request fails
        """
        if not endpoint.startswith('/'):
            raise ToolException("The 'relative_url' must start with '/'.")

        # Get fresh token for each request
        access_token = self._get_admin_token()

        url = f"{self.base_url}/admin/realms/{self.realm}{endpoint}"
        headers = {'Authorization': f'Bearer {access_token}', 'Content-Type': 'application/json'}

        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.request(method=method, url=url, headers=headers, json=json_data)
                response.raise_for_status()
                return response.text

        except httpx.HTTPStatusError as e:
            raise ToolException(f"HTTP {e.response.status_code}: {e.response.text}")
        except httpx.RequestError as e:
            raise ToolException(f"Request failed: {str(e)}")
        except Exception as e:
            raise ToolException(f"Unexpected error: {str(e)}")

    def execute_request(self, method: str, relative_url: str, params: Optional[Dict[str, Any]] = None) -> str:
        """
        Execute a request to Keycloak Admin API.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            relative_url: Relative URL starting with '/'
            params: Optional parameters (will be sent as JSON in body)

        Returns:
            str: Response text

        Raises:
            ToolException: If request fails
        """
        return self._request(method, relative_url, json_data=params)

    def health_check(self) -> bool:
        """
        Check if Keycloak server is accessible.

        Returns:
            bool: True if server is accessible

        Raises:
            ToolException: If health check fails
        """
        try:
            # Try to get a token as a health check
            self._get_admin_token()
            return True
        except Exception as e:
            raise ToolException(f"Keycloak health check failed: {str(e)}")
