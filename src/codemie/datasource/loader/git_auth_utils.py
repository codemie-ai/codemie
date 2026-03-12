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

"""Git authentication utilities for datasource indexing."""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def get_github_app_token(app_id: int, private_key: str, installation_id: Optional[int] = None) -> str:
    """
    Generate GitHub App installation access token using PyGithub.

    Args:
        app_id: GitHub App ID
        private_key: Private key in PEM format
        installation_id: Installation ID (optional, will auto-detect)

    Returns:
        str: Installation access token

    Raises:
        ValueError: If token generation fails
    """
    try:
        from github import GithubIntegration
    except ImportError:
        raise ImportError("PyGithub is required for GitHub App authentication")

    try:
        integration = GithubIntegration(integration_id=app_id, private_key=private_key)

        # Get or auto-detect installation ID
        if installation_id is None:
            installations = integration.get_installations()
            first_installation = next(iter(installations))
            installation_id = first_installation.id

        # Get access token
        access_token = integration.get_access_token(installation_id)
        return access_token.token

    except Exception as e:
        logger.error(f"Failed to generate GitHub App token: {e}")
        raise ValueError(f"GitHub App authentication failed: {str(e)}")
