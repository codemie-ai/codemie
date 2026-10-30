# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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
from codemie.agents.utils import sanitize_datasource_name
from codemie.clients.provider import client as provider_client
from codemie.rest_api.models.provider import ProviderBase, ProviderToolkit
from codemie.rest_api.models.index import ProviderIndexInfo
from codemie.rest_api.security.user import User, UserContext
from codemie.service.provider.util import decrypt_datasource_provider_fields
from codemie.service.provider.datasource import ProviderDatasourceSchemaService
from codemie.service.provider.provider_header_context import ProviderHeaderContext
from codemie.configs import logger
from codemie.clients.provider.client.models.tool_invocation_request import ToolInvocationRequest
from codemie.clients.provider.client.models.tool_invocation_response import ToolInvocationResponse
from codemie.clients.provider.client.models.toolkit_configuration import ToolkitConfiguration
from .util import to_class_name
from .provider_api_client import ProviderAPIClient
from codemie.core.otel_tracing import get_traceparent_headers


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
    ARG_SCHEMA_TYPE_MAPPING = {"String": str, "Number": int, "List": list[str], "Text": str}
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
                    "invoke_headers": Optional[dict],
                },
                "name": self._tool_name,
                "base_name": self.tool_config.name,
                "description": self.tool_config.description,
                "args_schema": self._generate_args_schema(),
                "invoke_headers": None,
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
            return f"{sanitize_datasource_name(self.datasource.repo_name)}_{self.tool_config.name}"

        return self.tool_config.name

    def _generate_execute(self):
        """Generate the tool's execute method; the HTTP call itself is delegated to invoke()."""
        context = self

        def execute(self, *_args, **kwargs):
            response = context.invoke(
                user=self.user,
                project_id=self.project_id,
                request_uuid=self.request_uuid,
                params=kwargs,
                headers=self.invoke_headers,
                datasource=context.datasource,
            )
            return response.result

        return execute

    def invoke(
        self,
        user: User,
        project_id: str,
        request_uuid: str,
        params: dict,
        headers: Optional[dict] = None,
        datasource: Optional[ProviderIndexInfo] = None,
    ) -> ToolInvocationResponse:
        """Invoke this provider tool over HTTP and return the raw ToolInvocationResponse.

        Shared by the dynamically built agent tool (see build()/_generate_execute) and the
        request-hedging fast path (HedgingToolService). Resolves datasource-backed
        configuration parameters, forwards the correlation id from request headers, and
        translates connection failures into ProviderConnectionError.

        Args:
            user: Caller identity, forwarded to the provider as user_id and for auth.
            project_id: Project scope of the invocation.
            request_uuid: Fallback correlation id when no X-Correlation-Id header is present.
            params: Resolved tool parameters.
            headers: Optional request headers forwarded to the provider.
            datasource: Optional datasource backing the tool; falls back to self.datasource.
        """
        log_prefix = f"Invoke provider tool '{self.tool_config.name}' [{request_uuid}]:"
        host = self.provider_config.service_location_url

        api_client: provider_client.ToolInvocationManagementApi = ProviderAPIClient(
            user=user,
            url=host,
            provider_security_config=self.provider_config.configuration,
            log_prefix=log_prefix,
        ).build()

        configuration_params = self._resolve_configuration_params(datasource or self.datasource)

        tool_invocation_request = ToolInvocationRequest(
            user_id=user.id,
            project_id=project_id,
            configuration=ToolkitConfiguration(
                configuration_type=self.CONFIGURATION_TYPE,
                parameters=configuration_params,
            ),
            parameters=params,
            **(
                {"user_context": UserContext.from_user(user).model_dump(exclude_none=True)}
                if self.provider_config.allow_user_context
                else {}
            ),
        )
        if self.provider_config.allow_user_context:
            logger.debug("%s user context: %s", log_prefix, UserContext.from_user(user).masked_for_logging())

        merged_headers = {**(headers or {}), **get_traceparent_headers()}
        x_correlation_id = merged_headers.get(ProviderHeaderContext.HEADERS["CORRELATION_ID"]) or request_uuid

        try:
            logger.info(f"{log_prefix} Invoking tool")
            response = api_client.invoke_tool(
                toolkit_name=self.toolkit_config.name,
                tool_name=self.tool_config.name,
                x_correlation_id=x_correlation_id,
                tool_invocation_request=tool_invocation_request,
                _headers=merged_headers or None,
            )
            logger.info(f"{log_prefix} Invoked tool successfully")
            return response
        except MaxRetryError:
            msg = self.CONNECTION_ERROR_MSG.format(host=host)
            logger.warning(f"{log_prefix} {msg}")
            raise ProviderConnectionError(msg)
        except Exception as e:
            logger.error(f"{log_prefix} Failed to invoke tool: {str(e)}")
            raise e

    def _resolve_configuration_params(self, datasource: Optional[ProviderIndexInfo]) -> dict:
        """Decrypt datasource-backed configuration params, or return {} when no datasource is set."""
        if not datasource:
            return {}

        schema = ProviderDatasourceSchemaService(
            provider=self.provider_config,
        ).schema_for(
            toolkit_id=self.toolkit_config.toolkit_id,
        )
        return decrypt_datasource_provider_fields(
            params=datasource.provider_fields.base_params,
            schema=schema.base_schema,
        )

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
