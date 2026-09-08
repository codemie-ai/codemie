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
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import codemie.clients.clickhouse as ch_module


@pytest.fixture(autouse=True)
def reset_singleton():
    """Clear the per-thread cached client before and after each test."""
    ch_module._thread_local.client = None
    yield
    ch_module._thread_local.client = None


def test_get_client_calls_connect_only_once():
    mock_client = MagicMock()
    with patch("codemie.clients.clickhouse.clickhouse_connect.get_client", return_value=mock_client) as mock_factory:
        c1 = ch_module.get_client()
        c2 = ch_module.get_client()
    mock_factory.assert_called_once()
    assert c1 is c2


def test_get_client_passes_config_values():
    mock_client = MagicMock()
    with (
        patch("codemie.clients.clickhouse.clickhouse_connect.get_client", return_value=mock_client) as mock_factory,
        patch("codemie.clients.clickhouse.config") as mock_cfg,
    ):
        mock_cfg.CLICKHOUSE_HOST = "ch-host"
        mock_cfg.CLICKHOUSE_PORT = 9000
        mock_cfg.CLICKHOUSE_USER = "admin"
        mock_cfg.CLICKHOUSE_PASSWORD = "secret"
        mock_cfg.CLICKHOUSE_QUERY_TIMEOUT_SECONDS = 60
        ch_module.get_client()
    mock_factory.assert_called_once_with(
        host="ch-host",
        port=9000,
        username="admin",
        password="secret",
        database="codemie_analytics",
        settings={"max_execution_time": 60},
    )


@pytest.mark.asyncio
async def test_ch_query_returns_named_results():
    mock_result = MagicMock()
    mock_result.named_results.return_value = [{"session_id": "abc", "cost_usd": 1.5}]
    mock_client = MagicMock()
    mock_client.query.return_value = mock_result

    async def fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    with (
        patch("codemie.clients.clickhouse.get_client", return_value=mock_client),
        patch("codemie.clients.clickhouse.asyncio.to_thread", side_effect=fake_to_thread),
    ):
        rows = await ch_module.ch_query("SELECT 1", {"k": "v"})

    mock_client.query.assert_called_once_with("SELECT 1", parameters={"k": "v"})
    assert rows == [{"session_id": "abc", "cost_usd": 1.5}]
