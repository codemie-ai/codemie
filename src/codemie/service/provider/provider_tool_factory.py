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

from pydantic import create_model, BaseModel
from typing import Type, Optional
from urllib3.exceptions import MaxRetryError

from codemie_tools.base.codemie_tool import CodeMieTool
from codemie.clients.provider import client as provider_client
from codemie.rest_api.models.provider import ProviderBase, ProviderToolkit
from codemie.rest_api.models.index import ProviderIndexInfo
from codemie.rest_api.security.user import User
from codemie.service.provider.util import decrypt_datasource_provider_fields
from codemie.service.provider.datasource import ProviderDatasourceSchemaService
from codemie.configs import logger
from .util import to_class_name
from .provider_api_client import ProviderAPIClient


class ProviderConnectionError(Exception):
    """Exception raised when a connection to a provider fails"""

    pass


class ProviderToolBase(CodeMieTool):
    """'Implement' ABC for provider tools."""

    def execute(self, *args, **kwargs): ...

    def get_tools_ui_info(self): ...

    def get_toolkit(self): ...


class ProviderToolFactory:
    CLASSNAME_POSTFIX = "Tool"
    ARG_SCHEMA_TYPE_MAPPING = {"String": str, "Number": int, "List": list[str]}
    CONNECTION_ERROR_MSG = "Failed to establish a connection with a tool provider: host: {host}"
    CONFIGURATION_TYPE = "tool_invocation"

    def __init__(
        self,
        provider_config: ProviderBase,
        toolkit_config: ProviderToolkit,
        tool_config: ProviderToolkit.Tool,
        provider_client: provider_client = provider_client,
        datasource: Optional[ProviderIndexInfo] = None,
    ):
        self.provider_config = provider_config
        self.toolkit_config = toolkit_config
        self.tool_config = tool_config
        self.datasource = datasource

    def build(self, datasource: Optional[ProviderIndexInfo] = None):
        """Dynamically build a tool class based on provider configuration."""
        klass_name = to_class_name(self.tool_config.name) + self.CLASSNAME_POSTFIX

        klass = type(
            klass_name,
            (ProviderToolBase,),
            {
                "__module__": __name__,
                "__annotations__": {
                    "name": str,
                    "base_name": str,
                    "description": str,
                    "args_schema": Type[BaseModel],
                    "user": User,
                    "project_id": str,
                    "request_uuid": str,
                },
                "name": self._tool_name,
                "base_name": self.tool_config.name,
                "description": self.tool_config.description,
                "args_schema": self._generate_args_schema(),
            },
        )
        klass.name = self._tool_name
        klass.base_name = self.tool_config.name
        klass.description = self.tool_config.description
        klass.args_schema = self._generate_args_schema()
        klass.execute = self._generate_execute()
        klass.datasource = datasource or None

        return klass

    @property
    def _tool_name(self):
        if self.datasource:
            return f"{self.datasource.repo_name}_{self.tool_config.name}"

        return self.tool_config.name

    def _generate_execute(self):
        """Generate tool execute method"""
        context = self

        def execute(self, *_args, **kwargs):
            log_prefix = f"Execute provider tool '{context.tool_config.name}' [{self.request_uuid}]:"
            host = context.provider_config.service_location_url

            api_client: provider_client.ToolInvocationManagementApi = ProviderAPIClient(
                user=self.user,
                url=host,
                provider_security_config=context.provider_config.configuration,
                log_prefix=log_prefix,
            ).build()

            if context.datasource:
                schema = ProviderDatasourceSchemaService(
                    provider=context.provider_config,
                ).schema_for(
                    toolkit_id=context.toolkit_config.toolkit_id,
                )
                configuration_params = decrypt_datasource_provider_fields(
                    params=context.datasource.provider_fields.base_params, schema=schema.base_schema
                )
            else:
                configuration_params = {}

            payload = {
                "user_id": self.user.id,
                "project_id": self.project_id,
                "configuration": {"configuration_type": context.CONFIGURATION_TYPE, "parameters": configuration_params},
                "parameters": kwargs,
                "async": False,
            }

            try:
                logger.info(f"{log_prefix} Invoking tool")
                response = api_client.invoke_tool(
                    toolkit_name=context.toolkit_config.name,
                    tool_name=context.tool_config.name,
                    x_correlation_id=self.request_uuid,
                    tool_invocation_request=payload,
                )
                logger.info(f"{log_prefix} Invoked tool successfully")
                return response.result
            except MaxRetryError:
                msg = context.CONNECTION_ERROR_MSG.format(host=host)
                logger.warning(f"{log_prefix} {msg}")
                raise ProviderConnectionError(msg)
            except Exception as e:
                logger.error(f"{log_prefix} Failed to invoke tool: {str(e)}")
                raise e

        return execute

    def _generate_args_schema(self) -> BaseModel:
        """Generate args schema for a tool"""
        schema = {}

        for param_name, param_config in self.tool_config.args_schema.items():
            param_type = self.ARG_SCHEMA_TYPE_MAPPING.get(param_config.arg_type.value, str)
            if param_config.required:
                schema[param_name] = (param_type, ...)
            else:
                schema[param_name] = (Optional[param_type], None)

        return create_model("ArgsSchema", **schema)
