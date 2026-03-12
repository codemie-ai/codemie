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

import logging
from typing import Optional

try:
    from elasticsearch import Elasticsearch

    elasticsearch_installed = True
except ImportError:
    Elasticsearch = None
    elasticsearch_installed = False

from codemie_tools.data_management.elastic.models import ElasticConfig

logger = logging.getLogger(__name__)


class SearchElasticIndexResults:
    @classmethod
    def _get_client(cls, elastic_config: ElasticConfig) -> Optional[Elasticsearch]:
        if not elasticsearch_installed:
            raise ImportError("'elasticsearch' package is not installed.")
        if elastic_config.api_key:
            return Elasticsearch(
                elastic_config.url, api_key=elastic_config.api_key, verify_certs=False, ssl_show_warn=False
            )
        else:
            return Elasticsearch(elastic_config.url, verify_certs=False, ssl_show_warn=False)

    @classmethod
    def search(cls, index: str, query: str, elastic_config: ElasticConfig):
        client = cls._get_client(elastic_config=elastic_config)
        response = client.search(index=index, body=query)
        return response.body
