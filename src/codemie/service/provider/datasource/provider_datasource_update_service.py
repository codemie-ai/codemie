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

from codemie.rest_api.security.user import User
from codemie.rest_api.models.provider import ProviderBase
from codemie.rest_api.models.index import IndexInfo

from codemie.service.provider.datasource.provider_datasource_schema_service import ProviderDatasourceSchemaService
from codemie.service.provider.util import encrypt_datasource_provider_fields

from .provider_datasource_base_service import ProviderDatasourceBaseService


class ProviderDatasourceUpdateService(ProviderDatasourceBaseService):
    """Handles updating of provider datasource record"""

    UPDATED_MESSAGE_TEMPLATE = "Datasource {} has been updated"

    def __init__(self, datasource: IndexInfo, provider: ProviderBase, values: dict, user: User):
        self.datasource = datasource
        self.toolkit_id = datasource.provider_fields.toolkit_id
        self.provider = provider
        self.schema = ProviderDatasourceSchemaService(provider, user).schema_for(
            self.toolkit_id, include_autofilled=True
        )
        self.values = values
        self.user = user

    def run(self) -> bool:
        """Runs the service"""
        filtered_values = self._filter_masked_fields(self._filter_codemie_fields(self.values))
        toolkit = self._find_toolkit(self.toolkit_id)
        base_params = self._extract_base_params(
            filtered_values,
            existing_values=self.datasource.provider_fields.base_params,
            managed_fields=toolkit.get_managed_fields(),
        )
        create_params = self._extract_create_params(
            filtered_values, existing_values=self.datasource.provider_fields.create_params
        )

        try:
            # Update datasource base fields
            for from_key in self.CODEMIE_FIELDS_MAPPING:
                value = self.values.get(from_key)
                target_key = self.CODEMIE_FIELDS_MAPPING[from_key]
                setattr(self.datasource, target_key, value)

            encrypted_base_params = encrypt_datasource_provider_fields(base_params, schema=self.schema.base_schema)
            self.datasource.provider_fields.base_params = {
                **self.datasource.provider_fields.base_params,
                **encrypted_base_params,
            }

            encrypted_create_params = encrypt_datasource_provider_fields(
                create_params, schema=self.schema.create_schema
            )
            self.datasource.provider_fields.create_params = {
                **self.datasource.provider_fields.create_params,
                **encrypted_create_params,
            }

            self.datasource.update()
        except Exception as e:
            self._handle_error(self.datasource, e)
            return False

        return True

    @property
    def updated_message(self) -> str:
        """Returns a message indicating the datasource has been updated."""
        return self.UPDATED_MESSAGE_TEMPLATE.format(self.datasource.repo_name)
