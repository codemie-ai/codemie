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

from typing import Dict, Optional

from codemie.rest_api.security.user import User
from codemie.rest_api.models.provider import Provider
from codemie.rest_api.models.index import IndexInfo

from codemie.service.provider.datasource.provider_datasource_schema_service import ProviderDatasourceSchemaService
from codemie.service.provider.datasource.provider_datasource_adapter import ProviderDatasourceAdapter
from codemie.service.provider.util import decrypt_datasource_provider_fields

from .provider_datasource_base_service import ProviderDatasourceBaseService
from .provider_datasource_update_service import ProviderDatasourceUpdateService


class ProviderDatasourceReindexService(ProviderDatasourceBaseService):
    """Handles updating and reindexing of provider datasource record"""

    STARTED_MESSAGE_TEMPLATE = "Reindexing of {} has started in the background"

    def __init__(self, datasource: IndexInfo, user: User, values: Optional[Dict] = None):
        self.datasource = datasource
        self.values = values
        self.provider = Provider.get_by_id(datasource.provider_fields.provider_id)
        self.toolkit_id = datasource.provider_fields.toolkit_id
        self.schema = ProviderDatasourceSchemaService(self.provider).schema_for(
            self.toolkit_id, include_autofilled=True
        )
        self.user = user

    def run(self):
        self._clear_error(self.datasource)

        if self.values:
            update_result = ProviderDatasourceUpdateService(
                datasource=self.datasource, values=self.values, provider=self.provider, user=self.user
            ).run()

            if not update_result:
                return

        try:
            response = ProviderDatasourceAdapter(
                user=self.user,
                project_id=self.datasource.project_name,
                provider_config=self.provider,
                toolkit_config=self._find_toolkit(self.toolkit_id),
                datasource=self.datasource,
            ).reindex(
                base_params=decrypt_datasource_provider_fields(
                    params=self.datasource.provider_fields.base_params, schema=self.schema.base_schema
                ),
                update_params=decrypt_datasource_provider_fields(
                    params=self.datasource.provider_fields.create_params, schema=self.schema.create_schema
                ),
            )

            # Start progress if reindex started in async mode
            if response.status == "Started":
                self.datasource.start_progress(self.datasource.complete_state)

            if response.errors:
                self._handle_error(self.datasource, response.errors)
                return
        except Exception as e:
            self._handle_error(self.datasource, e)

    @property
    def started_message(self) -> str:
        """Returns a message indicating indexing has started."""
        return self.STARTED_MESSAGE_TEMPLATE.format(self.datasource.repo_name)
