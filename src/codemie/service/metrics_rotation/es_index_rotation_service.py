# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""Quarterly Elasticsearch index rotation for codemie_metrics_logs."""

from __future__ import annotations

from datetime import date, datetime, timezone

from elasticsearch import AsyncElasticsearch, NotFoundError

from codemie.clients.elasticsearch import ElasticSearchClient
from codemie.configs import logger
from codemie.repository.spend_backfill_metrics_repository import LEDGER_ATTRIBUTES_MAPPING

_WRITE_ALIAS = "codemie_metrics_logs_write"
_INDEX_PREFIX = "codemie_metrics_logs"


class MetricsIndexRotationService:
    """Creates the next quarterly index and atomically swaps the write alias."""

    def __init__(self, client: AsyncElasticsearch | None = None) -> None:
        self._client = client or ElasticSearchClient.get_async_client()

    @staticmethod
    def _quarter_index_name(for_date: date) -> str:
        quarter = (for_date.month - 1) // 3 + 1
        return f"{_INDEX_PREFIX}-{for_date.year}-q{quarter}"

    async def rotate(self, new_index: str | None = None) -> None:
        """Create new quarterly index and swap write alias.

        Args:
            new_index: Target index name. Defaults to the current quarter's name.
        """
        target = new_index or MetricsIndexRotationService._quarter_index_name(datetime.now(timezone.utc).date())
        current_write = await self._find_current_write_index()

        if current_write == target:
            logger.info(f"Metrics rotation: {target!r} is already the write index, skipping alias swap")
            return

        if not await self._client.indices.exists(index=target):
            logger.info(f"Metrics rotation: creating index {target!r}")
            await self._client.indices.create(
                index=target,
                mappings={"properties": {"attributes": {"properties": LEDGER_ATTRIBUTES_MAPPING}}},
            )

        logger.info(f"Metrics rotation: swapping alias {_WRITE_ALIAS!r} from {current_write!r} to {target!r}")
        await self._client.indices.update_aliases(
            actions=[
                {"remove": {"index": current_write, "alias": _WRITE_ALIAS}},
                {"add": {"index": target, "alias": _WRITE_ALIAS, "is_write_index": True}},
            ]
        )
        logger.info(f"Metrics rotation: alias swap complete — writes now going to {target!r}")

    async def _find_current_write_index(self) -> str:
        """Return the index name that currently holds the write alias."""
        try:
            alias_info = await self._client.indices.get_alias(name=_WRITE_ALIAS)
        except NotFoundError as e:
            raise RuntimeError(f"Metrics rotation refused: write alias {_WRITE_ALIAS!r} does not exist") from e

        write_indices = [
            index_name
            for index_name, meta in alias_info.items()
            if meta.get("aliases", {}).get(_WRITE_ALIAS, {}).get("is_write_index") is True
        ]
        if len(write_indices) != 1:
            raise RuntimeError(
                f"Metrics rotation refused: alias {_WRITE_ALIAS!r} must have exactly one write index; "
                f"found {len(write_indices)}"
            )
        return write_indices[0]
