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

from codemie.rest_api.models.logs import LogEntry, LogRetrieveRequest
from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.configs.config import config


class AbstractLogService(ABC):
    @classmethod
    @abstractmethod
    def get_logs_by_target_field(cls, target: LogRetrieveRequest) -> list[LogEntry]:
        pass


class LogService(AbstractLogService):
    MAX_RESPONSE_SIZE = 1000

    @classmethod
    def get_logs_by_target_field(cls, target: LogRetrieveRequest) -> list[LogEntry]:
        """
        Retrieves log entries from Elasticsearch matching the specified field and value in the target.

        Args:
            target (LogRetrieveRequest): The request containing the field and value to filter logs.

        Returns:
            list[LogEntry]: A list of log entries matching the specified field.

        Raises:
            ValueError: If required fields ('message' or '@timestamp') are not found in any document.
            RuntimeError: If there is a problem communicating with Elasticsearch.
        """
        client = ElasticSearchClient.get_client()
        query = {"query": {"term": {f"{target.field}.keyword": target.value}}}

        try:
            res = client.search(index=config.ELASTIC_LOGS_INDEX, body=query, size=cls.MAX_RESPONSE_SIZE)
        except Exception as e:
            raise RuntimeError(f"Failed to search logs: {e}")

        log_entries = []
        for hit in res.get("hits", {}).get("hits", []):
            document = hit.get("_source", {})
            message = document.get("message")
            timestamp = document.get("@timestamp")
            if message is None or timestamp is None:
                raise ValueError("Each log document must contain 'message' and '@timestamp' fields.")
            log_entries.append(LogEntry(message=message, timestamp=timestamp))

        return log_entries
