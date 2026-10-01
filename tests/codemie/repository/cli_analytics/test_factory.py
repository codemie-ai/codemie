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

from unittest.mock import patch

import pytest

from codemie.configs.config import config
from codemie.repository.cli_analytics.clickhouse.ingestor import ClickHouseTelemetryIngestor
from codemie.repository.cli_analytics.clickhouse.reader import ClickHouseCliAnalyticsReader
from codemie.repository.cli_analytics.factory import get_cli_analytics_storage, reset_cli_analytics_storage


@pytest.fixture(autouse=True)
def _fresh_storage():
    reset_cli_analytics_storage()
    yield
    reset_cli_analytics_storage()


def test_clickhouse_backend_uses_the_clickhouse_adapters():
    with patch.object(config, "CLI_ANALYTICS_STORAGE_BACKEND", "clickhouse"):
        storage = get_cli_analytics_storage()

    assert storage.backend == "clickhouse"
    assert isinstance(storage.reader, ClickHouseCliAnalyticsReader)
    assert isinstance(storage.ingestor, ClickHouseTelemetryIngestor)
    assert storage.runtime is None


def test_clickhouse_reader_queries_through_the_shared_clickhouse_client():
    from codemie.clients.clickhouse import ch_query

    with patch.object(config, "CLI_ANALYTICS_STORAGE_BACKEND", "clickhouse"):
        storage = get_cli_analytics_storage()

    assert storage.reader._q is ch_query


def test_storage_is_built_once_per_process():
    with patch.object(config, "CLI_ANALYTICS_STORAGE_BACKEND", "clickhouse"):
        assert get_cli_analytics_storage() is get_cli_analytics_storage()


def test_reset_builds_a_new_storage_next_time():
    with patch.object(config, "CLI_ANALYTICS_STORAGE_BACKEND", "clickhouse"):
        first = get_cli_analytics_storage()
        reset_cli_analytics_storage()
        assert get_cli_analytics_storage() is not first


def test_postgres_backend_uses_the_postgres_adapters_and_never_touches_clickhouse():
    from codemie.repository.cli_analytics.postgres.ingestor import PostgresTelemetryIngestor
    from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
    from codemie.repository.cli_analytics.postgres.runtime import PostgresAnalyticsRuntime

    with (
        patch.object(config, "CLI_ANALYTICS_STORAGE_BACKEND", "postgres"),
        patch("codemie.clients.clickhouse.get_client") as clickhouse_client,
    ):
        storage = get_cli_analytics_storage()

    assert storage.backend == "postgres"
    assert isinstance(storage.reader, PostgresCliAnalyticsReader)
    assert isinstance(storage.ingestor, PostgresTelemetryIngestor)
    assert isinstance(storage.runtime, PostgresAnalyticsRuntime)
    clickhouse_client.assert_not_called()


def test_postgres_adapters_share_one_pool_configured_from_settings():
    with (
        patch.object(config, "CLI_ANALYTICS_STORAGE_BACKEND", "postgres"),
        patch.object(config, "CLI_ANALYTICS_PG_SCHEMA", "custom_schema"),
        patch.object(config, "CLI_ANALYTICS_PG_URL", "postgresql://u:p@analytics/db"),
    ):
        storage = get_cli_analytics_storage()

    engine = storage.reader._engine
    assert storage.ingestor._engine is engine
    assert storage.runtime._engine is engine
    assert (engine.settings.schema, engine.settings.dsn) == ("custom_schema", "postgresql://u:p@analytics/db")


def test_unknown_backend_is_rejected():
    with (
        patch.object(config, "CLI_ANALYTICS_STORAGE_BACKEND", "cassandra"),
        pytest.raises(ValueError, match="cassandra"),
    ):
        get_cli_analytics_storage()


@pytest.mark.parametrize(("backend", "days"), [("clickhouse", 90), ("postgres", 30)])
def test_each_storage_states_how_far_back_its_raw_rows_reach(backend, days):
    # ClickHouse: the raw tables' TTL in config/clickhouse/schema.sql; PostgreSQL: configured.
    with (
        patch.object(config, "CLI_ANALYTICS_STORAGE_BACKEND", backend),
        patch.object(config, "CLI_ANALYTICS_RAW_RETENTION_DAYS", 30),
    ):
        assert get_cli_analytics_storage().raw_retention_days == days
