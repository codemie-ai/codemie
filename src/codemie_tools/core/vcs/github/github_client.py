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

"""GitHub API client with support for PAT and GitHub App authentication."""

import logging
import time
from typing import Any, Dict, Optional

import requests
from langchain_core.tools import ToolException

from .models import GithubConfig

logger = logging.getLogger(__name__)

# Token expiration buffer (60 seconds before actual expiration)
TOKEN_EXPIRATION_BUFFER = 60


class GithubClient:
    """
    Centralized GitHub API client handling both PAT and GitHub App authentication.

    Supports:
    - Personal Access Token (PAT) authentication
    - GitHub App authentication with automatic token caching and refresh
    """

    def __init__(self, config: GithubConfig):
        """
        Initialize GitHub client with configuration.

        Args:
            config: GithubConfig with either PAT or GitHub App credentials
        """
        self.config = config
        self._installation_token: Optional[str] = None
        self._token_expires_at: Optional[float] = None

    def get_auth_token(self) -> str:
        """
        Get authentication token for GitHub API requests.

        Returns PAT directly or generates/caches GitHub App installation token.

        Returns:
            str: Bearer token for GitHub API authentication

        Raises:
            ToolException: If token generation fails
        """
        if self.config.is_github_app:
            return self._get_github_app_token()
        else:
            return self.config.token

    def _get_github_app_token(self) -> str:
        """
        Get GitHub App installation token with caching.

        Caches token for 1 hour (GitHub default) and auto-refreshes when expired.

        Returns:
            str: Installation access token

        Raises:
            ToolException: If token generation fails
        """
        current_time = time.time()

        # Check if cached token is still valid
        if (
            self._installation_token
            and self._token_expires_at
            and current_time < (self._token_expires_at - TOKEN_EXPIRATION_BUFFER)
        ):
            logger.debug("Using cached GitHub App installation token")
            return self._installation_token

        # Generate new token using PyGithub
        try:
            import github

            logger.info("Generating new GitHub App installation token")

            # Create GithubIntegration instance
            integration = github.GithubIntegration(
                integration_id=self.config.app_id, private_key=self.config.private_key
            )

            # Get installation ID
            installation_id = self.config.installation_id
            if installation_id is None:
                # Auto-fetch first installation
                installations = integration.get_installations()
                try:
                    first_installation = next(iter(installations))
                    installation_id = first_installation.id
                except StopIteration:
                    raise ToolException(
                        "No GitHub App installations found. Please install the app "
                        "or provide installation_id in configuration"
                    )

            # Get access token for the installation
            access_token = integration.get_access_token(installation_id)
            token = access_token.token

            # Cache token with expiration (default 1 hour)
            self._installation_token = token
            self._token_expires_at = current_time + 3600  # 1 hour from now

            logger.info("Successfully generated GitHub App installation token")
            return token

        except ImportError:
            raise ToolException(
                "PyGithub library is required for GitHub App authentication. " "Please install it: pip install PyGithub"
            )
        except github.GithubException as e:
            raise ToolException(f"Failed to generate GitHub App token: {e.data.get('message', str(e))}")
        except ToolException:
            raise
        except Exception as e:
            raise ToolException(f"Unexpected error generating GitHub App token: {str(e)}")

    def make_request(self, method: str, url: str, headers: Dict[str, str], data: Optional[str] = None) -> Any:
        """
        Make authenticated request to GitHub API.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, PATCH)
            url: Complete GitHub API URL
            headers: Request headers (Authorization will be added/overridden)
            data: Optional JSON string for request body

        Returns:
            JSON response from GitHub API

        Raises:
            ToolException: If request fails or authentication fails
        """
        try:
            # Get auth token and add to headers
            token = self.get_auth_token()
            headers = headers.copy()
            headers["Authorization"] = f"Bearer {token}"

            # Make request
            response = requests.request(method=method, url=url, headers=headers, data=data)

            # Handle 401 errors for GitHub App (token might have expired)
            if response.status_code == 401 and self.config.is_github_app:
                logger.info("Received 401 error, refreshing GitHub App token")
                # Clear cached token and retry once
                self._installation_token = None
                self._token_expires_at = None
                token = self.get_auth_token()
                headers["Authorization"] = f"Bearer {token}"

                response = requests.request(method=method, url=url, headers=headers, data=data)

            # Raise for HTTP errors
            response.raise_for_status()

            return response.json()

        except requests.exceptions.HTTPError as e:
            error_msg = f"GitHub API request failed with status {e.response.status_code}"
            try:
                error_data = e.response.json()
                if "message" in error_data:
                    error_msg = f"{error_msg}: {error_data['message']}"
            except Exception:
                error_msg = f"{error_msg}: {e.response.text}"

            raise ToolException(error_msg)
        except requests.exceptions.RequestException as e:
            raise ToolException(f"Failed to connect to GitHub API: {str(e)}")
        except ToolException:
            raise
        except Exception as e:
            raise ToolException(f"Unexpected error making GitHub API request: {str(e)}")
