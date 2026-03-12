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

import os

from elasticsearch import AsyncElasticsearch, Elasticsearch

from codemie.configs import config


class ElasticSearchClient:
    _clients: dict[int, Elasticsearch] = {}
    _async_clients: dict[int, AsyncElasticsearch] = {}

    @classmethod
    def get_client(cls) -> Elasticsearch:
        """
        For each new process we create new engine, to not use parent engine to prevent errors
        """
        pid = os.getpid()
        if pid not in cls._clients:
            cls._clients[pid] = Elasticsearch(
                config.ELASTIC_URL,
                basic_auth=(config.ELASTIC_USERNAME, config.ELASTIC_PASSWORD),
                verify_certs=False,
                ssl_show_warn=False,
            )
        return cls._clients[pid]

    @classmethod
    def get_async_client(cls) -> AsyncElasticsearch:
        """Get async Elasticsearch client for current process.

        For each new process we create a new async client to avoid sharing
        connections across process boundaries, which can cause errors.
        """
        pid = os.getpid()
        if pid not in cls._async_clients:
            cls._async_clients[pid] = AsyncElasticsearch(
                config.ELASTIC_URL,
                basic_auth=(config.ELASTIC_USERNAME, config.ELASTIC_PASSWORD),
                verify_certs=False,
                ssl_show_warn=False,
            )
        return cls._async_clients[pid]
