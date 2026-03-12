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

from typing import Type, Union, Dict, Any, Optional

from langchain_core.tools import ToolException
from pydantic import BaseModel, model_validator

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.base.utils import parse_and_escape_args
from .k8s_client import KubernetesClient
from .models import KubernetesConfig, KubernetesInput
from .tools_vars import KUBERNETES_TOOL


class GenericKubernetesTool(CodeMieTool):
    """Generic tool for interacting with Kubernetes API."""

    config: KubernetesConfig
    client: Optional[KubernetesClient] = None
    name: str = KUBERNETES_TOOL.name
    description: str = KUBERNETES_TOOL.description
    args_schema: Type[BaseModel] = KubernetesInput

    @model_validator(mode='after')
    def initialize_client(self) -> 'GenericKubernetesTool':
        """Initialize the Kubernetes client with configuration."""
        self.client = KubernetesClient(url=self.config.url, token=self.config.token, verify_ssl=self.config.verify_ssl)
        return self

    def execute(
        self,
        method: str,
        suburl: str,
        body: Optional[Union[str, Dict[str, Any]]] = None,
        headers: Optional[Union[str, Dict[str, Any]]] = None,
    ) -> str:
        """
        Execute a Kubernetes API request.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, PATCH)
            suburl: Relative API path (must start with /)
            body: Optional JSON object for request body
            headers: Optional JSON object for request headers

        Returns:
            str: Response from Kubernetes API

        Raises:
            ToolException: If operation fails
        """
        try:
            # Validate inputs
            if not suburl:
                raise ToolException("suburl is required for Kubernetes API requests")

            if not method:
                raise ToolException("HTTP method is required for Kubernetes API requests")

            # Parse body using common utility
            parsed_body = parse_and_escape_args(body, item_type="body")

            # Parse headers using common utility
            parsed_headers = parse_and_escape_args(headers, item_type="headers")

            # Make the API call
            response = self.client.call_api(suburl=suburl, method=method, body=parsed_body, headers=parsed_headers)

            return response

        except ToolException:
            raise
        except Exception as e:
            raise ToolException(f"Kubernetes tool execution failed: {str(e)}")

    def _healthcheck(self):
        """
        Check if Kubernetes service is accessible.
        Raises an exception if the service is not accessible.
        """
        self.client.health_check()
