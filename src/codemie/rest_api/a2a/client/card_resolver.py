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

from typing import Optional, Tuple

import httpx

from codemie.configs import logger, config
from codemie.rest_api.a2a.types import AgentCard
from codemie.rest_api.a2a.utils import get_auth_header
from codemie.service.settings.settings import SettingsService


class A2ACardResolver:
    """Class for resolving A2A agent cards from URLs"""

    def __init__(self):
        """Initialize the card resolver"""
        self.bedrock_agentcore: bool = False

    @staticmethod
    def normalize_url(url: str) -> str:
        """Normalize a URL by ensuring it has a scheme and no trailing slash"""
        # Remove trailing slash
        if url.endswith('/'):
            url = url[:-1]

        return url

    def build_agent_json_url(self, base_url: str) -> str:
        """Build the URL for fetching agent.json"""
        normalized_url = self.normalize_url(base_url)

        # Check if URL already ends with .well-known/agent.json
        if normalized_url.endswith(('.well-known/agent.json', '.well-known/agent-card.json')):
            return normalized_url

        # Add .well-known/agent.json path
        if not normalized_url.endswith('/'):
            normalized_url += '/'

        # Add special handling for bedrock-agentcore
        if "bedrock-agentcore" in normalized_url:
            self.bedrock_agentcore = True
            return normalized_url + '.well-known/agent-card.json'

        return normalized_url + '.well-known/agent.json'

    async def fetch_agent_card(
        self,
        url: str,
        project_name: Optional[str] = None,
        user_id: Optional[str] = None,
        integration_id: Optional[str] = None,
    ) -> Tuple[bool, Optional[AgentCard], str]:
        """
        Fetch an agent card from a remote URL

        Args:
            url: The base URL of the agent
            project_name: Optional project name to retrieve credentials for
            user_id: Optional user ID to retrieve credentials for
            integration_id: Optional ID of the integration to use for authentication

        Returns:
            Tuple of (success, agent_card, error_message)
        """
        try:
            agent_url = self.build_agent_json_url(url)

            logger.info(f"Fetching agent card from {agent_url}")

            # Get authentication credentials from settings service if available
            headers = {}
            if user_id and project_name:
                try:
                    creds = SettingsService.get_a2a_creds(
                        user_id=user_id, project_name=project_name, integration_id=integration_id
                    )
                    if creds:
                        headers = get_auth_header(creds, "GET", agent_url)
                except Exception as e:
                    logger.warning(f"Failed to get A2A credentials: {e}")

            async with httpx.AsyncClient(
                verify=config.HTTPS_VERIFY_SSL,
                follow_redirects=True,
            ) as client:
                response = await client.get(agent_url, headers=headers, timeout=config.A2A_AGENT_CARD_FETCH_TIMEOUT)

                if response.status_code != 200:
                    logger.error(f"Error fetching agent card: {response.status_code} {response.text}")
                    return False, None, f"Error fetching agent card: HTTP {response.status_code}"

                try:
                    logger.debug(f"Response content: {response.text}")
                    agent_data = response.json()
                    agent_data["project_name"] = project_name
                    agent_data["integration_id"] = integration_id
                    agent_data["user_id"] = user_id
                    agent_data["bedrock_agentcore"] = self.bedrock_agentcore

                    agent_card = AgentCard.model_validate(agent_data)
                    return True, agent_card, ""
                except Exception as e:
                    logger.error(f"Error parsing agent card: {e}")
                    return False, None, f"Invalid agent card format: {str(e)}"

        except Exception as e:
            logger.error(f"Error fetching agent card: {e}")
            return False, None, f"Error fetching agent card: {str(e)}"
