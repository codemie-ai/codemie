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
from codemie.rest_api.models.index import IndexInfo, IndexInfoProviderFields
from codemie.rest_api.utils.default_applications import ensure_application_exists

from codemie.service.provider.datasource.provider_datasource_schema_service import ProviderDatasourceSchemaService
from codemie.service.provider.datasource.provider_datasource_adapter import ProviderDatasourceAdapter
from codemie.service.provider.datasource.constants import PROVIDER_INDEX_TYPE
from codemie.service.provider.util import encrypt_datasource_provider_fields

from .provider_datasource_base_service import ProviderDatasourceBaseService


class ProviderDatasourceCreationService(ProviderDatasourceBaseService):
    """Handles creation of provider datasource records and triggers provider API indexing."""

    STARTED_MESSAGE_TEMPLATE = "Indexing of {} has started in the background"

    def __init__(self, provider: ProviderBase, toolkit_id: str, values: dict, user: User):
        self.provider = provider
        self.toolkit_id = toolkit_id
        self.schema = ProviderDatasourceSchemaService(provider, user).schema_for(toolkit_id, include_autofilled=True)
        self.values = values
        self.user = user

    def run(self):
        """Runs the service"""
        toolkit = self._find_toolkit(self.toolkit_id)

        filtered_values = self._filter_codemie_fields(self.values)
        base_params = self._extract_base_params(filtered_values, managed_fields=toolkit.get_managed_fields())
        create_params = self._extract_create_params(filtered_values)

        # Ensure Application exists for the project_name
        project_name = self.values.get("project_name")
        if project_name:
            ensure_application_exists(project_name)

        try:
            index_info = self._create_index_info(base_params, create_params)
        except Exception as e:
            self._handle_error(index_info, e)
            raise ValueError(f"Error creating index info: {e}")

        index_info.save()

        try:
            response = ProviderDatasourceAdapter(
                user=self.user,
                project_id=index_info.project_name,
                provider_config=self.provider,
                toolkit_config=toolkit,
                datasource=index_info,
            ).create(
                base_params=base_params,
                create_params=create_params,
            )

            if response.errors:
                self._handle_error(index_info, response.errors)
                return

            # Complete progress if indexing started in sync mode
            if response.status == "Completed":
                index_info.complete_progress()
        except Exception as e:
            self._handle_error(index_info, e)

    @property
    def started_message(self) -> str:
        """Returns a message indicating indexing has started."""
        return self.STARTED_MESSAGE_TEMPLATE.format(self.values.get("name"))

    def _create_index_info(self, base_params: dict, create_params: dict) -> IndexInfo:
        """Creates and returns an IndexInfo object."""
        encrypted_base_params = encrypt_datasource_provider_fields(
            base_params,
            schema=self.schema.base_schema,
        )
        encrypted_create_params = encrypt_datasource_provider_fields(
            create_params,
            schema=self.schema.create_schema,
        )

        return IndexInfo(
            repo_name=self.values.get("name"),
            index_type=PROVIDER_INDEX_TYPE,
            project_name=self.values.get("project_name"),
            description=self.values.get("description"),
            project_space_visible=self.values.get("project_space_visible"),
            current_state=0,
            complete_state=0,
            completed=False,
            created_by=self._created_by_user,
            provider_fields=IndexInfoProviderFields(
                provider_id=self.provider.id,
                toolkit_id=self.toolkit_id,
                base_params=encrypted_base_params,
                create_params=encrypted_create_params,
            ),
        )
