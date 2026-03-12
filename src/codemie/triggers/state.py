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

from typing import Any, Dict
from codemie.configs import logger
from codemie.rest_api.models.base import BaseModelWithElasticSupport
from datetime import datetime
from uuid import uuid4


class TransactionElasticSupport(BaseModelWithElasticSupport):
    def save(self, refresh=False, validate=True):
        if not self.id:
            self.id = str(uuid4())
        if not self.date:
            self.date = datetime.now()
            self.update_date = self.date

        self._validate_if_needed(validate)

        response = self.elastic_client.index(index=self._index, id=self.id, document=self.model_dump(), refresh=True)
        return response["_id"]

    def _validate_if_needed(self, validate):
        if not validate:
            return

        validation_message = self.validate_fields()
        if validation_message:
            raise ValueError(validation_message)

    def update(self, refresh=False, validate=True):
        self.update_date = datetime.now()
        validation_message = self.validate_fields()
        if validation_message:
            raise ValueError(validation_message)
        response = self.elastic_client.update(index=self._index, id=self.id, doc=self.model_dump(), refresh=True)
        return response["_id"]

    def delete(self):
        return self.elastic_client.delete_by_query(
            index=self._index,
            body={"query": {"bool": {"must": [{"match": {"id": self.id}}]}}},
            conflicts="proceed",
            refresh=True,
        )

    @classmethod
    def get_all_by_fields(cls, fields: Dict[str, Any]) -> Any:
        conditions = [{"match": {k: v}} for k, v in fields.items()]
        query = {"query": {"bool": {"must": conditions}}}

        res = cls._client().search(index=cls._index.default, body=query)
        if res["hits"]["hits"]:
            return [cls(**hit["_source"]) for hit in res["hits"]["hits"]]

    @classmethod
    def get_by_fields_sorted(cls, fields: Dict[str, Any]) -> Any | None:
        conditions = [{"match": {k: v}} for k, v in fields.items()]
        query = {"query": {"bool": {"must": conditions}}, "sort": [{"update_date": {"order": "desc"}}]}
        res = cls._client().search(index=cls._index.default, body=query)
        if res["hits"]["hits"]:
            logger.info(f"Found: {cls(**res['hits']['hits'][0]['_source'])}")
            return cls(**res["hits"]["hits"][0]["_source"])
        else:
            logger.info("Nothing found.")
            return None
