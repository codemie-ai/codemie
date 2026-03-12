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

import logging
from typing import Type, Optional

from langchain_core.tools import ToolException
from pydantic import BaseModel, model_validator

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.utils import parse_to_dict
from .keycloak_client import KeycloakClient
from .models import KeycloakConfig, KeycloakToolInput
from .tools_vars import KEYCLOAK_TOOL

logger = logging.getLogger(__name__)


class KeycloakTool(CodeMieTool):
    """Generic tool for interacting with Keycloak Admin API."""

    config: KeycloakConfig
    client: Optional[KeycloakClient] = None
    name: str = KEYCLOAK_TOOL.name
    description: str = KEYCLOAK_TOOL.description
    args_schema: Type[BaseModel] = KeycloakToolInput

    @model_validator(mode='after')
    def initialize_client(self) -> 'KeycloakTool':
        """Initialize the Keycloak client with configuration."""
        # Only initialize if all required fields are present
        if self.config.base_url and self.config.realm and self.config.client_id and self.config.client_secret:
            self.client = KeycloakClient(
                base_url=self.config.base_url,
                realm=self.config.realm,
                client_id=self.config.client_id,
                client_secret=self.config.client_secret,
                timeout=self.config.timeout,
            )
        return self

    def execute(self, method: str, relative_url: str, params: Optional[str] = None) -> str:
        """
        Execute a Keycloak Admin API request.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            relative_url: Relative URL starting with '/' (e.g., '/users')
            params: Optional string dictionary of parameters

        Returns:
            str: Response text from Keycloak API

        Raises:
            ToolException: If request fails
        """
        try:
            # Parse params if provided
            payload_params = None
            if params:
                payload_params = parse_to_dict(params)

            # Execute request via client
            response_text = self.client.execute_request(method=method, relative_url=relative_url, params=payload_params)

            return response_text

        except ToolException:
            # Re-raise ToolExceptions from client
            raise
        except Exception as e:
            logger.error(f"Error executing Keycloak request: {str(e)}")
            raise ToolException(f"Failed to execute Keycloak request: {str(e)}")

    def _healthcheck(self):
        """
        Check if Keycloak server is accessible.
        Raises ToolException if health check fails.
        """
        self.client.health_check()
