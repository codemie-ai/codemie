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

"""Complete aggregation searches over one short-lived metrics snapshot."""

from __future__ import annotations

import asyncio
import logging
from types import TracebackType
from typing import Any

from elastic_transport import TransportError
from elasticsearch import ApiError, AsyncElasticsearch

from codemie.core.exceptions import ExtendedHTTPException

logger = logging.getLogger(__name__)


class MetricsAggregationSnapshot:
    """Own a PIT and reject incomplete results instead of reporting partial usage."""

    def __init__(self, client: AsyncElasticsearch | None, index: str) -> None:
        self._client = client
        self._index = index
        self._pit_id: str | None = None

    async def __aenter__(self) -> MetricsAggregationSnapshot:
        if self._client is None:
            raise self._unavailable()
        try:
            result = await self._client.options(request_timeout=60).open_point_in_time(
                index=self._index, keep_alive="1m", allow_partial_search_results=False
            )
            self._pit_id = result["id"]
            self._require_complete(result)
            return self
        except (ApiError, TransportError, asyncio.TimeoutError) as exc:
            logger.warning("Failed to open metrics aggregation snapshot", exc_info=True)
            raise self._unavailable() from exc
        except ExtendedHTTPException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        if self._pit_id:
            try:
                await self._client.close_point_in_time(id=self._pit_id)
            except (ApiError, TransportError, asyncio.TimeoutError):
                logger.warning("Failed to close metrics aggregation snapshot", exc_info=True)

    async def search(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            result = await self._client.search(
                body={**body, "pit": {"id": self._pit_id, "keep_alive": "1m"}, "track_total_hits": False},
                allow_partial_search_results=False,
                request_timeout=60,
            )
            self._pit_id = result.get("pit_id", self._pit_id)
            self._require_complete(result)
            return result
        except (ApiError, TransportError, asyncio.TimeoutError) as exc:
            logger.warning("Metrics snapshot aggregation failed", exc_info=True)
            raise self._unavailable() from exc

    @classmethod
    def _require_complete(cls, result: dict[str, Any]) -> None:
        if result.get("timed_out") or result.get("_shards", {}).get("failed", 0):
            raise cls._unavailable()

    @staticmethod
    def _unavailable() -> ExtendedHTTPException:
        return ExtendedHTTPException(
            code=503,
            message="Complete workflow analytics are unavailable",
            details="Elasticsearch could not provide a complete aggregation snapshot.",
            help="Retry the request or narrow the date range.",
        )
