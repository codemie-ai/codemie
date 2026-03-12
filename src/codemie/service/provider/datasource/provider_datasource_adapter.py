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

from uuid import uuid4
from typing import Dict, Optional

from codemie.configs import logger, config
from codemie.rest_api.models.provider import ProviderToolMetadata
from codemie.clients.provider import client as provider_client
from codemie.rest_api.security.user import User
from codemie.rest_api.models.provider import ProviderBase, ProviderToolkit
from codemie.rest_api.models.index import IndexInfo
from codemie.service.provider.provider_api_client import ProviderAPIClient


class ProviderDatasourceAdapter:
    """Adapter for creating, updating, and deleting datasources using the provider's API"""

    def __init__(
        self,
        user: User,
        provider_config: ProviderBase,
        toolkit_config: ProviderToolkit,
        project_id: str,
        datasource: IndexInfo,
    ):
        self.user = user
        self.project_id = project_id
        self.provider_config = provider_config
        self.toolkit_config = toolkit_config
        self.correlation_id = str(uuid4())
        self.datasource = datasource

    NO_TOOL_FOUND_ERROR = "No tool found for action {action}"

    def create(self, base_params: Dict, create_params: Dict):
        """Create a datasource using the provider's API"""
        return self._send_request(
            base_params=base_params, request_params=create_params, action=ProviderToolMetadata.ActionType.CREATE
        )

    def delete(self, base_params: Dict):
        """Delete a datasource using the provider's API"""
        return self._send_request(base_params=base_params, action=ProviderToolMetadata.ActionType.REMOVE)

    def reindex(self, base_params: Dict, update_params: Optional[Dict] = None):
        """Reindex a datasource using the provider's API"""
        params = {
            "base_params": base_params,
            "action": ProviderToolMetadata.ActionType.MODIFY,
        }
        if update_params:
            params["request_params"] = update_params

        return self._send_request(**params)

    def _send_request(
        self, base_params: Dict, action: ProviderToolMetadata.ActionType, request_params: Optional[Dict] = None
    ):
        """Buildc and send a request to the provider's API"""
        if not request_params:
            request_params = {}

        log_prefix = f"{self.provider_config.name} [{self.correlation_id}]:"
        host = self.provider_config.service_location_url

        api_client: provider_client.ToolInvocationManagementApi = ProviderAPIClient(
            user=self.user, url=host, provider_security_config=self.provider_config.configuration, log_prefix=log_prefix
        ).build()

        tool = self._find_tool_for_action(action)

        payload = {
            "user_id": self.user.id,
            "project_id": self.project_id,
            "configuration": {"configuration_type": "datasource", "parameters": base_params},
            "parameters": request_params,
            "async": tool.async_invocation_supported,
        }

        if tool.async_invocation_supported and action in (
            ProviderToolMetadata.ActionType.CREATE,
            ProviderToolMetadata.ActionType.MODIFY,
        ):
            otp = self.datasource.generate_otp()
            payload["callback_url"] = (
                f"{config.CALLBACK_API_BASE_URL}{config.API_ROOT_PATH}/v1/callbacks/index/{self.datasource.id}"
            )
        else:
            otp = None

        try:
            logger.info(f"{log_prefix} Datasource action {action.value.capitalize()} has started")
            response = api_client.invoke_tool(
                toolkit_name=self.toolkit_config.name,
                tool_name=tool.name,
                x_correlation_id=self.correlation_id,
                x_callback_otp=otp,
                tool_invocation_request=payload,
            )

            logger.info(f"{log_prefix} Datasource action {action.value.capitalize()} is successful")
            return response
        except Exception as e:
            logger.error(f"{log_prefix} Datasource action {action.value.capitalize()} error - {str(e)}")
            raise e

    def _find_tool_for_action(self, action: ProviderToolMetadata.ActionType) -> ProviderToolkit.Tool:
        """Find the tool for the given action"""
        tool = next(
            (
                tool
                for tool in self.toolkit_config.provided_tools
                if tool.tool_metadata.tool_action_type == action
                and tool.tool_metadata.tool_purpose == ProviderToolMetadata.Purpose.LIFE_CYCLE_MANAGEMENT
            ),
            None,
        )

        if not tool:
            raise ValueError(self.NO_TOOL_FOUND_ERROR.format(action=action))

        return tool
