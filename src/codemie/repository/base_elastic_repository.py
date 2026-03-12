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

from abc import ABC, abstractmethod
from typing import Optional
from fastapi.exceptions import RequestValidationError

from elasticsearch import Elasticsearch

from codemie.core.models import AbstractElasticModel
from codemie.configs.logger import logger


class BaseElasticRepository(ABC):
    def __init__(self, elastic_client: Elasticsearch, index_name: str):
        self._elastic_client = elastic_client
        self._index_name = index_name

    def get_by_id(self, _id: str) -> AbstractElasticModel:
        item = self._elastic_client.get(index=self._index_name, id=_id)
        return self.to_entity(item["_source"])

    def get_all(self, query: Optional[dict] = None, limit: Optional[int] = None) -> list[AbstractElasticModel]:
        if query is None:
            query = {"match_all": {}}

        size = limit if limit is not None else 10000
        repos_result = self._elastic_client.search(index=self._index_name, query=query, size=size)

        result = []

        for hit in repos_result["hits"]["hits"]:
            try:
                entity = self.to_entity(hit["_source"])
                result.append(entity)
            except RequestValidationError:
                logger.warning(f"Invalid document found in {self._index_name} index: {hit['_source']}")
                continue

        return result

    def search_by_name(self, name_query: Optional[str] = None, limit: Optional[int] = None):
        query = None
        if name_query:
            query = {
                "bool": {
                    "should": [
                        {"term": {"name.keyword": name_query}},  # Exact match gets higher priority
                        {"wildcard": {"name.keyword": f"*{name_query}*"}},
                    ]
                }
            }

        return self.get_all(query=query, limit=limit)

    def save(self, entity: AbstractElasticModel) -> AbstractElasticModel:
        self._elastic_client.index(index=self._index_name, id=entity.get_identifier(), document=entity.model_dump())

        return entity

    def update(self, entity: AbstractElasticModel) -> AbstractElasticModel:
        self._elastic_client.update(index=self._index_name, id=entity.get_identifier(), doc=entity.model_dump())
        return entity

    @abstractmethod
    def to_entity(self, item: dict) -> AbstractElasticModel:
        pass

    def to_document(self, entity: AbstractElasticModel):
        return entity.model_dump()
