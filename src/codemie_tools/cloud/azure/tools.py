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

# Azure Tool for interacting with Azure REST API
from typing import Type, Union, Dict, Any, Optional

from langchain_core.tools import ToolException
from pydantic import BaseModel, model_validator

from codemie_tools.base.codemie_tool import CodeMieTool
from .azure_client import AzureClient
from .models import AzureConfig, AzureInput
from .tools_vars import AZURE_TOOL
from ...base.utils import parse_and_escape_args


class GenericAzureTool(CodeMieTool):
    """Generic tool for interacting with Azure REST API."""

    config: AzureConfig
    client: Optional[AzureClient] = None
    name: str = AZURE_TOOL.name
    description: str = AZURE_TOOL.description
    args_schema: Type[BaseModel] = AzureInput

    @model_validator(mode='after')
    def initialize_client(self) -> 'GenericAzureTool':
        """Initialize the Azure client with configuration."""
        self.client = AzureClient(
            subscription_id=self.config.subscription_id,
            tenant_id=self.config.tenant_id,
            client_id=self.config.client_id,
            client_secret=self.config.client_secret,
        )
        self.description += f"""
            SubscriptionId: {self.config.subscription_id}.
            If some required information is not provided by user, try find by querying API, if not found ask user.
        """
        return self

    def execute(
        self,
        method: str,
        url: str,
        optional_args: Optional[Union[str, Dict[str, Any]]] = None,
        scope: Optional[str] = None,
    ) -> str:
        """
        Execute an Azure REST API request.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            url: Full URL for the Azure API request
            optional_args: Optional JSON object with request parameters
            scope: OAuth scope for authentication (default: Azure Resource Manager)

        Returns:
            str: Response from Azure API

        Raises:
            ToolException: If operation fails
        """
        try:
            # Validate inputs
            if not url:
                raise ToolException("URL is required for Azure API requests")
            if not method:
                raise ToolException("HTTP method is required for Azure API requests")

            # Parse arguments and determine scope
            parsed_args = None if not optional_args else parse_and_escape_args(optional_args, item_type="optional_args")
            request_scope = scope or "https://management.azure.com/.default"

            # Make the request
            return self.client.request(method=method, url=url, scope=request_scope, optional_args=parsed_args)

        except ToolException:
            raise
        except Exception as e:
            raise ToolException(f"Azure tool execution failed: {str(e)}")

    def _healthcheck(self):
        """
        Check if Azure service is accessible.
        Raises an exception if the service is not accessible.
        """
        self.client.health_check()
