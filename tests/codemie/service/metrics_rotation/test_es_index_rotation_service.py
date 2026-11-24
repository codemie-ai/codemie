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

"""Unit tests for MetricsIndexRotationService."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from elasticsearch import NotFoundError

from codemie.configs import config
from codemie.repository.spend_backfill_metrics_repository import LEDGER_ATTRIBUTES_MAPPING
from codemie.service.metrics_rotation.es_index_rotation_service import MetricsIndexRotationService

WRITE_ALIAS = "codemie_metrics_logs_write"
LEDGER_MAPPINGS = {"properties": {"attributes": {"properties": LEDGER_ATTRIBUTES_MAPPING}}}


@pytest.mark.parametrize(
    "test_date,expected_index",
    [
        (date(2026, 1, 1), "codemie_metrics_logs-2026-q1"),
        (date(2026, 4, 1), "codemie_metrics_logs-2026-q2"),
        (date(2026, 7, 15), "codemie_metrics_logs-2026-q3"),
        (date(2026, 10, 31), "codemie_metrics_logs-2026-q4"),
        (date(2027, 1, 1), "codemie_metrics_logs-2027-q1"),
    ],
)
def test_current_quarter_index_name(test_date: date, expected_index: str) -> None:
    assert MetricsIndexRotationService._quarter_index_name(test_date) == expected_index


@pytest.mark.asyncio
async def test_rotate_swaps_alias() -> None:
    """rotate() must create new index and atomically swap the write alias."""
    mock_client = MagicMock()
    mock_client.indices = MagicMock()
    mock_client.indices.create = AsyncMock()
    mock_client.indices.exists = AsyncMock(return_value=False)
    mock_client.indices.update_aliases = AsyncMock()
    mock_client.indices.get_alias = AsyncMock(
        return_value={"codemie_metrics_logs-2026-q1": {"aliases": {WRITE_ALIAS: {"is_write_index": True}}}}
    )

    service = MetricsIndexRotationService(client=mock_client)

    new_index = "codemie_metrics_logs-2026-q2"
    await service.rotate(new_index=new_index)

    mock_client.indices.create.assert_awaited_once_with(index=new_index, mappings=LEDGER_MAPPINGS)
    mock_client.indices.update_aliases.assert_awaited_once_with(
        actions=[
            {"remove": {"index": "codemie_metrics_logs-2026-q1", "alias": WRITE_ALIAS}},
            {"add": {"index": new_index, "alias": WRITE_ALIAS, "is_write_index": True}},
        ]
    )


@pytest.mark.asyncio
async def test_rotate_skips_when_index_already_exists() -> None:
    """rotate() must not create or swap when the target is already the write index."""
    mock_client = MagicMock()
    mock_client.indices = MagicMock()
    mock_client.indices.create = AsyncMock()
    mock_client.indices.exists = AsyncMock()
    mock_client.indices.update_aliases = AsyncMock()
    mock_client.indices.get_alias = AsyncMock(
        return_value={"codemie_metrics_logs-2026-q2": {"aliases": {WRITE_ALIAS: {"is_write_index": True}}}}
    )

    service = MetricsIndexRotationService(client=mock_client)

    await service.rotate(new_index="codemie_metrics_logs-2026-q2")

    mock_client.indices.exists.assert_not_awaited()
    mock_client.indices.create.assert_not_awaited()
    mock_client.indices.update_aliases.assert_not_awaited()


@pytest.mark.asyncio
async def test_rotate_uses_current_quarter_when_no_index_given() -> None:
    """rotate() with no argument derives the index name from today's date."""
    mock_client = MagicMock()
    mock_client.indices = MagicMock()
    mock_client.indices.create = AsyncMock()
    mock_client.indices.exists = AsyncMock(return_value=False)
    mock_client.indices.update_aliases = AsyncMock()
    # Return a different quarter so swap happens
    mock_client.indices.get_alias = AsyncMock(
        return_value={"codemie_metrics_logs-2026-q1": {"aliases": {WRITE_ALIAS: {"is_write_index": True}}}}
    )

    service = MetricsIndexRotationService(client=mock_client)
    await service.rotate()  # no new_index

    # Should have called create with something matching codemie_metrics_logs-YYYY-qN
    mock_client.indices.create.assert_awaited_once()
    called_index = mock_client.indices.create.call_args[1]["index"]
    assert called_index.startswith("codemie_metrics_logs-")
    assert "-q" in called_index


@pytest.mark.asyncio
async def test_rotate_works_without_an_index_template() -> None:
    """rotate() must require only a valid write alias, not an index template."""
    mock_client = MagicMock()
    mock_client.indices = MagicMock()
    mock_client.indices.create = AsyncMock()
    mock_client.indices.exists = AsyncMock(return_value=False)
    mock_client.indices.update_aliases = AsyncMock()
    mock_client.indices.get_alias = AsyncMock(
        return_value={"codemie_metrics_logs-2026-q1": {"aliases": {WRITE_ALIAS: {"is_write_index": True}}}}
    )

    service = MetricsIndexRotationService(client=mock_client)
    await service.rotate(new_index="codemie_metrics_logs-2026-q2")

    mock_client.indices.create.assert_awaited_once_with(index="codemie_metrics_logs-2026-q2", mappings=LEDGER_MAPPINGS)
    mock_client.indices.update_aliases.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("backfill_enabled", [True, False])
async def test_rotate_creates_index_with_ledger_mapping_even_when_backfill_is_disabled(backfill_enabled: bool) -> None:
    """The ledger field types are fixed at creation so a later backfill cannot meet a wrongly typed index."""
    mock_client = MagicMock()
    mock_client.indices = MagicMock()
    mock_client.indices.create = AsyncMock()
    mock_client.indices.exists = AsyncMock(return_value=False)
    mock_client.indices.update_aliases = AsyncMock()
    mock_client.indices.get_alias = AsyncMock(
        return_value={"codemie_metrics_logs-2026-q1": {"aliases": {WRITE_ALIAS: {"is_write_index": True}}}}
    )

    with patch.object(config, "SPEND_BACKFILL_ENABLED", backfill_enabled):
        await MetricsIndexRotationService(client=mock_client).rotate(new_index="codemie_metrics_logs-2026-q2")

    mock_client.indices.create.assert_awaited_once_with(index="codemie_metrics_logs-2026-q2", mappings=LEDGER_MAPPINGS)


@pytest.mark.asyncio
async def test_rotate_refuses_when_write_alias_is_missing() -> None:
    """rotate() must not create or swap an index without the write alias."""
    mock_client = MagicMock()
    mock_client.indices = MagicMock()
    mock_client.indices.create = AsyncMock()
    mock_client.indices.exists = AsyncMock()
    mock_client.indices.update_aliases = AsyncMock()
    mock_client.indices.get_alias = AsyncMock(side_effect=NotFoundError("alias not found", meta=MagicMock(), body={}))

    service = MetricsIndexRotationService(client=mock_client)
    with pytest.raises(RuntimeError, match=WRITE_ALIAS):
        await service.rotate(new_index="codemie_metrics_logs-2026-q2")

    mock_client.indices.exists.assert_not_awaited()
    mock_client.indices.create.assert_not_awaited()
    mock_client.indices.update_aliases.assert_not_awaited()


@pytest.mark.asyncio
async def test_rotate_refuses_when_write_alias_has_multiple_write_indices() -> None:
    """rotate() must not mutate Elasticsearch if the alias has multiple writers."""
    mock_client = MagicMock()
    mock_client.indices = MagicMock()
    mock_client.indices.create = AsyncMock()
    mock_client.indices.exists = AsyncMock()
    mock_client.indices.update_aliases = AsyncMock()
    mock_client.indices.get_alias = AsyncMock(
        return_value={
            "codemie_metrics_logs-2026-q1": {"aliases": {WRITE_ALIAS: {"is_write_index": True}}},
            "codemie_metrics_logs-2026-q2": {"aliases": {WRITE_ALIAS: {"is_write_index": True}}},
        }
    )

    service = MetricsIndexRotationService(client=mock_client)
    with pytest.raises(RuntimeError, match="exactly one write index"):
        await service.rotate(new_index="codemie_metrics_logs-2026-q3")

    mock_client.indices.exists.assert_not_awaited()
    mock_client.indices.create.assert_not_awaited()
    mock_client.indices.update_aliases.assert_not_awaited()


@pytest.mark.asyncio
async def test_rotate_reuses_an_existing_target_index() -> None:
    """rotate() must swap to a pre-existing quarterly index without creating it."""
    mock_client = MagicMock()
    mock_client.indices = MagicMock()
    mock_client.indices.create = AsyncMock()
    mock_client.indices.exists = AsyncMock(return_value=True)
    mock_client.indices.update_aliases = AsyncMock()
    mock_client.indices.get_alias = AsyncMock(
        return_value={"codemie_metrics_logs-2026-q1": {"aliases": {WRITE_ALIAS: {"is_write_index": True}}}}
    )

    service = MetricsIndexRotationService(client=mock_client)
    await service.rotate(new_index="codemie_metrics_logs-2026-q2")

    mock_client.indices.exists.assert_awaited_once_with(index="codemie_metrics_logs-2026-q2")
    mock_client.indices.create.assert_not_awaited()
    mock_client.indices.update_aliases.assert_awaited_once()
