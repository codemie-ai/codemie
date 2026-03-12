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
from codemie.configs import logger
from codemie.rest_api.security.user import User
from codemie.rest_api.models.provider import Provider
from codemie.rest_api.models.index import IndexInfo

from codemie.service.provider.datasource.provider_datasource_adapter import ProviderDatasourceAdapter

from .provider_datasource_base_service import ProviderDatasourceBaseService


class ProviderDatasourceDeletionService(ProviderDatasourceBaseService):
    """Handles deletion of provider datasource record"""

    def __init__(self, datasource: IndexInfo, user: User):
        self.datasource = datasource
        self.user = user

    def run(self):
        try:
            self.provider = Provider.get_by_id(self.datasource.provider_fields.provider_id)
            toolkit_id = self.datasource.provider_fields.toolkit_id

            response = ProviderDatasourceAdapter(
                user=self.user,
                project_id=self.datasource.project_name,
                provider_config=self.provider,
                toolkit_config=self._find_toolkit(toolkit_id),
                datasource=self.datasource,
            ).delete(
                base_params=self.datasource.provider_fields.base_params,
            )

            if response.errors:
                self._handle_error(self.datasource, response.errors)
                return
        except Exception as e:
            stacktrace = traceback.format_exc()
            logger.error(f"Error deleting datasource in AICE: {e}: {stacktrace}")
            self._handle_error(self.datasource, e)
        finally:
            self.datasource.delete()
