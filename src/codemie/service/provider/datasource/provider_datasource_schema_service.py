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

from typing import List, Dict, Optional

from codemie.configs import config
from codemie.rest_api.models.provider import (
    Provider,
    ProviderBase,
    ProviderToolkit,
    ProviderToolMetadata,
    ProviderToolkitConfigParameter,
    ProviderToolArgument,
)
from codemie.rest_api.models.provider import ProviderDataSourceTypeSchema, ProviderDataSourceSchemas
from codemie.rest_api.security.user import User
from codemie.service.index.index_service import IndexStatusService
from .constants import AUTOFILLED_SCHEMA_PARAM_TYPES, AICE_DATSOURCE_IDS_FIELD


class ProviderDatasourceSchemaService:
    """Returns schemas for creating and updating datasources defined by provider configs"""

    @classmethod
    def get_all(cls, user: User) -> List[ProviderDataSourceTypeSchema]:
        """Return schemas for all providers"""
        return [schemas for provider in Provider.get_all() for schemas in cls(provider, user).schemas()]

    def __init__(self, provider: ProviderBase, user: Optional[User] = None):
        self.provider = provider
        self.user = user

    def schemas(self) -> List[ProviderDataSourceSchemas]:
        """Return the schemas for creating and updating provider datasources"""
        return [
            ProviderDataSourceSchemas(
                id=toolkit.toolkit_id,
                name=self._schema_name(toolkit),
                provider_name=self.provider.name,
                base_schema=self._toolkit_base_schema(toolkit),
                create_schema=self._toolkit_creation_schema(toolkit),
            )
            for toolkit in self.provider.provided_toolkits
            if toolkit._has_datasource_definition
        ]

    def schema_for(self, toolkit_id: str, include_autofilled: bool = False) -> ProviderDataSourceSchemas | None:
        """Return the schema for creating and updating a provider datasource"""
        toolkit = next(
            (toolkit for toolkit in self.provider.provided_toolkits if toolkit.toolkit_id == toolkit_id),
            None,
        )

        if not toolkit or not toolkit._has_datasource_definition:
            raise ValueError(f"Toolkit {toolkit_id} does not have a datasource definition")

        return ProviderDataSourceSchemas(
            id=toolkit.toolkit_id,
            name=self._schema_name(toolkit),
            provider_name=self.provider.name,
            base_schema=self._toolkit_base_schema(toolkit, include_autofilled),
            create_schema=self._toolkit_creation_schema(toolkit, include_autofilled),
        )

    def _schema_name(self, toolkit: ProviderToolkit) -> str:
        """Return the name of the schema"""
        return f"{self.provider.name} - {toolkit.name}"

    @staticmethod
    def _toolkit_base_schema(
        toolkit: ProviderToolkit, include_autofilled: bool = False
    ) -> ProviderDataSourceTypeSchema:
        """Adds the base schema for a datasource (used both for creation and update)"""
        schema = ProviderDataSourceTypeSchema(description=toolkit.toolkit_config.description)
        params: ProviderToolkitConfigParameter.ParameterType = toolkit.toolkit_config.parameters
        managed_fields: Dict = toolkit.get_managed_fields()

        for name, param in params.items():
            if (
                name in managed_fields or param.parameter_type in AUTOFILLED_SCHEMA_PARAM_TYPES
            ) and not include_autofilled:
                continue

            schema.parameters.append(
                ProviderDataSourceTypeSchema.Parameter(
                    name=name,
                    description=param.description,
                    required=param.required,
                    parameter_type=param.parameter_type,
                    enum=param.enum,
                    title=param.title,
                    example=param.example,
                )
            )

        return schema

    def _toolkit_creation_schema(
        self,
        toolkit: ProviderToolkit,
        include_autofilled: bool = False,
    ) -> ProviderDataSourceTypeSchema:
        """Return the schema only for creating a datasource."""
        return self._build_schema(
            toolkit=toolkit,
            action=ProviderToolMetadata.ActionType.CREATE,
            include_autofilled=include_autofilled,
        )

    def _build_schema(
        self,
        toolkit: ProviderToolkit,
        action: ProviderToolMetadata.ActionType,
        include_autofilled: bool = False,
    ) -> ProviderDataSourceTypeSchema:
        """Builds the schema for datasource action"""
        description = toolkit.toolkit_config.description
        schema = ProviderDataSourceTypeSchema(description=description)

        tool_params: Dict[str, ProviderToolArgument] = next(
            (tool for tool in toolkit.provided_tools if tool.tool_metadata.tool_action_type == action), None
        )

        if not tool_params:
            return schema

        for name, param in tool_params.args_schema.items():
            if param.arg_type in AUTOFILLED_SCHEMA_PARAM_TYPES and not include_autofilled:
                continue

            if self.provider.name == config.CODE_EXPLORATION_SERVICE_PROVIDER_NAME and name == AICE_DATSOURCE_IDS_FIELD:
                schema = self._handle_aice_datasource_ids(schema, name, param)
                continue

            schema.parameters.append(
                ProviderDataSourceTypeSchema.Parameter(
                    name=name,
                    description=param.description,
                    required=param.required,
                    parameter_type=param.arg_type,
                    enum=param.enum,
                    title=param.title,
                    example=param.example,
                )
            )

        return schema

    def _handle_aice_datasource_ids(
        self, schema: ProviderDataSourceTypeSchema, name: str, param
    ) -> ProviderDataSourceTypeSchema:
        """Special case for AICE datasource ID field"""
        if not self.user:
            return schema

        datasources = IndexStatusService.get_aice_datasources(self.user)
        options = [
            {"label": datasource.name, "value": datasource.datasource_id, "project_name": datasource.project_name}
            for datasource in datasources
        ]

        schema.parameters.append(
            ProviderDataSourceTypeSchema.Parameter(
                name=name,
                description=param.description,
                required=param.required,
                parameter_type=ProviderDataSourceTypeSchema.Parameter.AdditionalTypes.MULTISELECT,
                multiselect_options=options,
                title=param.title,
                example=param.example,
            )
        )

        return schema
