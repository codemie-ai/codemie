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

from elasticsearch import NotFoundError

from codemie.configs import logger
from codemie.rest_api.models.assistant import Assistant
from codemie.rest_api.models.index import IndexInfo
from codemie.service.constants import FullDatasourceTypes
from codemie.triggers.trigger_exceptions import DatasourceNotValidated, NotImplementedDatasource


def validate_assistant(assistant_id):
    """Validate assistant"""
    try:
        assistant = Assistant.get_by_id(id_=assistant_id)
        logger.debug("Assistant validated: %s", assistant_id)
        return assistant
    except NotFoundError:
        logger.error("Assistant not found: %s", assistant_id)
        return None


DATASOURCE_WITHOUT_SETTING_ID = [
    FullDatasourceTypes.GOOGLE,
    FullDatasourceTypes.PROVIDER,
]


def validate_datasource(datasource_id) -> IndexInfo | None:
    """Validate datasource"""
    try:
        ds: IndexInfo | None = IndexInfo.get_by_id(id_=datasource_id)
        if not ds:
            return None

        if ds.is_code_index() or ds.index_type in [
            FullDatasourceTypes.CONFLUENCE,
            FullDatasourceTypes.JIRA,
            FullDatasourceTypes.GOOGLE,
            FullDatasourceTypes.AZURE_DEVOPS_WIKI,
            FullDatasourceTypes.AZURE_DEVOPS_WORK_ITEM,
            FullDatasourceTypes.PROVIDER,
        ]:
            logger.debug("Datasource validated: %s", datasource_id)

            if not ds.setting_id and ds.index_type not in DATASOURCE_WITHOUT_SETTING_ID:
                logger.error("Datasource require setting_id: %s", datasource_id)
                raise DatasourceNotValidated(
                    f"Datasource '{datasource_id}' is missing repository or setting ID.",
                )

            return ds
        elif ds:
            raise NotImplementedDatasource(f"Datasource type '{ds.index_type}' is not supported via webhook.")

        return None
    except NotFoundError:
        logger.error("Datasource not found: %s", datasource_id)
        return None
