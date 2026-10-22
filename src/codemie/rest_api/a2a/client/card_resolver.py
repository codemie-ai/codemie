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
from urllib.parse import urlparse

import httpx

from codemie.configs import logger, config
from codemie.rest_api.a2a.types import AgentCard, AgentCardInterfaceError, get_jsonrpc_interface
from codemie.rest_api.a2a.version import A2A_PROTOCOL_VERSION, is_supported_protocol_version
from codemie.rest_api.a2a.utils import get_auth_header
from codemie.service.settings.settings import SettingsService


class A2ACardResolver:
    """Class for resolving A2A agent cards from URLs"""

    @staticmethod
    def normalize_url(url: str) -> str:
        """Normalize a URL by ensuring it has a scheme and no trailing slash"""
        # Remove trailing slash
        if url.endswith('/'):
            url = url[:-1]

        return url

    def build_agent_json_url(self, base_url: str) -> str:
        """Build the URL for fetching the v1.0 agent-card.json"""
        normalized_url = self.normalize_url(base_url)

        if normalized_url.endswith('.well-known/agent-card.json'):
            return normalized_url

        return normalized_url + '/.well-known/agent-card.json'

    @staticmethod
    def _unusable_reason(card: AgentCard) -> Optional[str]:
        """Why CodeMie cannot call this agent (None when it can): needs a JSON-RPC interface speaking v1.0."""
        try:
            interface = get_jsonrpc_interface(card)
        except AgentCardInterfaceError as e:
            return str(e)
        if not is_supported_protocol_version(interface.protocolVersion):
            return (
                f"Agent does not support A2A protocol {A2A_PROTOCOL_VERSION} "
                f"(its JSON-RPC interface declares {interface.protocolVersion})"
            )
        return None

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
                        headers = dict(get_auth_header(creds, "GET", agent_url))
                except Exception as e:
                    logger.warning(f"Failed to get A2A credentials: {e}")

            headers["A2A-Version"] = A2A_PROTOCOL_VERSION

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
                    agent_data["bedrock_agentcore"] = "bedrock-agentcore" in (urlparse(agent_url).hostname or "")
                    agent_data["user_id"] = user_id

                    agent_card = AgentCard.model_validate(agent_data)
                    unusable = self._unusable_reason(agent_card)
                    if unusable:
                        logger.error(f"Agent card rejected: {unusable}")
                        return False, None, unusable
                    return True, agent_card, ""
                except Exception as e:
                    logger.error(f"Error parsing agent card: {e}")
                    return False, None, f"Invalid agent card format: {str(e)}"

        except Exception as e:
            logger.error(f"Error fetching agent card: {e}")
            return False, None, f"Error fetching agent card: {str(e)}"
