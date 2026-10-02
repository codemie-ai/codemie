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

import dataclasses
from unittest.mock import patch

import pytest

from codemie.configs.config import Config, config
from codemie.repository.cli_analytics.factory import (
    CliAnalyticsStorage,
    get_cli_analytics_storage,
    reset_cli_analytics_storage,
)
from codemie.repository.cli_analytics.postgres.ingestor import PostgresTelemetryIngestor
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.runtime import PostgresAnalyticsRuntime


@pytest.fixture(autouse=True)
def _fresh_storage():
    reset_cli_analytics_storage()
    yield
    reset_cli_analytics_storage()


def test_storage_is_postgresql_without_any_engine_setting() -> None:
    assert "CLI_ANALYTICS_STORAGE_BACKEND" not in Config.model_fields

    storage = get_cli_analytics_storage()

    assert isinstance(storage.reader, PostgresCliAnalyticsReader)
    assert isinstance(storage.ingestor, PostgresTelemetryIngestor)
    assert isinstance(storage.runtime, PostgresAnalyticsRuntime)


def test_storage_names_no_engine() -> None:
    # One engine: nothing is left to tell engines apart by.
    assert "backend" not in {field.name for field in dataclasses.fields(CliAnalyticsStorage)}


def test_storage_is_built_once_per_process():
    assert get_cli_analytics_storage() is get_cli_analytics_storage()


def test_reset_builds_a_new_storage_next_time():
    first = get_cli_analytics_storage()
    reset_cli_analytics_storage()

    assert get_cli_analytics_storage() is not first


def test_adapters_share_one_pool_configured_from_settings() -> None:
    with (
        patch.object(config, "CLI_ANALYTICS_PG_SCHEMA", "custom_schema"),
        patch.object(config, "CLI_ANALYTICS_PG_URL", "postgresql://u:p@analytics/db"),
    ):
        storage = get_cli_analytics_storage()

    engine = storage.reader._engine
    assert storage.ingestor._engine is engine
    assert storage.runtime._engine is engine
    assert (engine.settings.schema, engine.settings.dsn) == ("custom_schema", "postgresql://u:p@analytics/db")


def test_raw_rows_reach_back_as_far_as_configured() -> None:
    with patch.object(config, "CLI_ANALYTICS_RAW_RETENTION_DAYS", 30):
        assert get_cli_analytics_storage().raw_retention_days == 30
