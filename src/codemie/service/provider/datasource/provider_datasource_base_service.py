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

import traceback
from uuid import uuid4
from typing import Optional

from codemie.configs import logger
from codemie.core.models import CreatedByUser
from codemie.rest_api.models.provider import ProviderToolkit
from codemie.rest_api.models.index import IndexInfo
from codemie.rest_api.models.provider import ProviderToolkitConfigParameter
from codemie.service.provider.datasource.constants import AUTOFILLED_SCHEMA_PARAM_TYPES


class ProviderDatasourceBaseService:
    CODEMIE_FIELDS_MAPPING = {
        "name": "repo_name",
        "project_name": "project_name",
        "description": "description",
        "project_space_visible": "project_space_visible",
    }  # Codemie fields, not required for provider API call

    MASKED_VALUE: str = "*" * 10

    def run(self):
        """Runs the service."""
        raise NotImplementedError

    @property
    def _created_by_user(self) -> dict:
        """Returns a dictionary representing the CreatedByUser object."""
        return CreatedByUser(id=self.user.id, username=self.user.username, name=self.user.name).dict()

    def _handle_error(self, index_info: IndexInfo, error: Exception):
        """Handles an error during processing."""
        stacktrace = traceback.format_exc()
        logger.error(f"Error modifying datasource: {error}: {stacktrace}")
        index_info.error = True
        index_info.text = f"{error}"
        index_info.save()

    def _clear_error(self, index_info: IndexInfo):
        """Clears the error state of the index info."""
        index_info.error = False
        index_info.text = ""
        index_info.save()

    def _find_toolkit(self, toolkit_id: str) -> ProviderToolkit:
        """Finds and returns a toolkit by ID."""
        toolkit = next(
            (toolkit for toolkit in self.provider.provided_toolkits if toolkit.toolkit_id == toolkit_id), None
        )

        if not toolkit:
            raise ValueError(f"Toolkit not found: {toolkit_id} for provider: {self.provider.name}")

        return toolkit

    def _filter_codemie_fields(self, values: dict) -> dict:
        """
        Removes unnecessary Codemie fields from the provided values.
        If field is present in the schema, it will be kept.
        """
        schema_fields = self.schema.field_names if self.schema else []
        return {key: value for key, value in values.items() if key in schema_fields}

    def _filter_masked_fields(self, values: dict) -> dict:
        """Filters masked values"""
        return {key: value for key, value in values.items() if value != self.MASKED_VALUE}

    def _extract_base_params(
        self,
        provider_values: dict,
        managed_fields: dict,
        existing_values: Optional[dict] = None,
    ) -> dict:
        """Extracts and returns base parameters from provider values."""
        return self._extract_params(
            provider_values, self.schema.base_schema.parameters, existing_values, managed_fields
        )

    def _extract_create_params(self, provider_values: dict, existing_values: Optional[dict] = None) -> dict:
        """Extracts and returns create parameters from provider values."""
        return self._extract_params(provider_values, self.schema.create_schema.parameters, existing_values)

    def _extract_params(
        self,
        provider_values: dict,
        param_definitions: list,
        existing_values: Optional[dict] = None,
        managed_fields: Optional[dict] = None,
    ) -> dict:
        """Extracts parameters based on schema definitions."""
        params = {}

        for param_def in param_definitions:
            name = param_def.name

            if param_def.parameter_type in AUTOFILLED_SCHEMA_PARAM_TYPES:
                if existing_values and name in existing_values:
                    params[name] = existing_values[name]
                else:
                    params[name] = self._autofill_param(param_def.parameter_type)
            elif name in provider_values:
                params[name] = provider_values[name]
            elif managed_fields and name in managed_fields:
                params[name] = managed_fields[name]
            elif param_def.required and param_def.parameter_type != ProviderToolkitConfigParameter.ParameterType.SECRET:
                raise ValueError(f"Missing required parameter: {name}")

        return params

    def _autofill_param(self, param_type: str) -> str:
        """Generates values for autofilled parameters."""
        if param_type == ProviderToolkitConfigParameter.ParameterType.UUID:
            return str(uuid4())
        raise ValueError(f"Unknown autofill parameter type: {param_type}")
