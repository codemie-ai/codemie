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

"""CLI Analytics storage settings (CLI_ANALYTICS_*)."""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from codemie.configs.config import Config


@pytest.fixture
def defaults_only(monkeypatch):
    """Builds a Config from its defaults: no CLI_ANALYTICS_* variable and no .env file is read.

    A developer's .env.local must not change what these tests see.
    """
    for name in list(os.environ):
        if name.startswith("CLI_ANALYTICS_"):
            monkeypatch.delenv(name)
    return lambda: Config(_env_file=None)


def test_no_setting_selects_an_engine() -> None:
    # PostgreSQL is the only engine: there is nothing to select.
    assert "CLI_ANALYTICS_STORAGE_BACKEND" not in Config.model_fields


def test_postgres_defaults_match_the_design(defaults_only):
    c = defaults_only()

    assert c.CLI_ANALYTICS_PG_URL == ""
    assert c.CLI_ANALYTICS_PG_SCHEMA == "codemie_analytics"
    assert c.CLI_ANALYTICS_PG_POOL_SIZE == 8
    assert c.CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS == 30_000
    assert c.CLI_ANALYTICS_PG_WORK_MEM == "32MB"
    assert c.CLI_ANALYTICS_PG_INGEST_ACQUIRE_TIMEOUT_MS == 1_000
    assert c.CLI_ANALYTICS_PG_INGEST_STATEMENT_TIMEOUT_MS == 10_000
    assert c.CLI_ANALYTICS_RAW_RETENTION_DAYS == 90
    assert c.CLI_ANALYTICS_ROLLUP_RETENTION_DAYS == 365
    assert c.CLI_ANALYTICS_DEDUP_RETENTION_DAYS == 14
    assert c.CLI_ANALYTICS_ROLLUP_REFRESH_SECONDS == 30
    assert c.CLI_ANALYTICS_ROLLUP_BATCH_SIZE == 5_000
    assert c.CLI_ANALYTICS_PARTITION_PREMAKE_WEEKS == 4
    assert c.CLI_ANALYTICS_MAINTENANCE_INTERVAL_MINUTES == 60
    assert c.CLI_ANALYTICS_SESSION_RETENTION_DAYS == 0  # above 0 the maintenance job deletes whole idle sessions


@pytest.mark.parametrize("schema", ["Analytics", "1abc", "a-b", 'x"; drop', "a" * 64, ""])
def test_schema_must_be_a_plain_lowercase_identifier(schema):
    # The schema name is placed into DDL and search_path, so only safe identifiers pass.
    with pytest.raises(ValidationError):
        Config(CLI_ANALYTICS_PG_SCHEMA=schema)


@pytest.mark.parametrize("work_mem", ["32MB", "4096kB", "1GB"])
def test_work_mem_accepts_postgres_memory_units(work_mem):
    accepted = Config(CLI_ANALYTICS_PG_WORK_MEM=work_mem).CLI_ANALYTICS_PG_WORK_MEM

    assert accepted == work_mem


@pytest.mark.parametrize("work_mem", ["32", "32 MB", "32mb", "lots", "32MB; SET x"])
def test_work_mem_rejects_anything_else(work_mem):
    with pytest.raises(ValidationError):
        Config(CLI_ANALYTICS_PG_WORK_MEM=work_mem)


@pytest.mark.parametrize(
    "field",
    [
        "CLI_ANALYTICS_PG_POOL_SIZE",
        "CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS",
        "CLI_ANALYTICS_PG_INGEST_ACQUIRE_TIMEOUT_MS",
        "CLI_ANALYTICS_PG_INGEST_STATEMENT_TIMEOUT_MS",
        "CLI_ANALYTICS_RAW_RETENTION_DAYS",
        "CLI_ANALYTICS_ROLLUP_RETENTION_DAYS",
        "CLI_ANALYTICS_DEDUP_RETENTION_DAYS",
        "CLI_ANALYTICS_ROLLUP_REFRESH_SECONDS",
        "CLI_ANALYTICS_ROLLUP_BATCH_SIZE",
        "CLI_ANALYTICS_PARTITION_PREMAKE_WEEKS",
        "CLI_ANALYTICS_MAINTENANCE_INTERVAL_MINUTES",
    ],
)
def test_counts_and_durations_must_be_positive(field):
    with pytest.raises(ValidationError):
        Config(**{field: 0})


def test_analytics_database_url_is_masked_in_the_startup_log():
    safe = Config(CLI_ANALYTICS_PG_URL="postgresql://u:secret@db/analytics").to_safe_dict()

    assert safe["CLI_ANALYTICS_PG_URL"] == "******"


def test_rollups_must_be_kept_at_least_as_long_as_raw_rows():
    # Windows reach as far back as the raw rows; rollups dropped earlier would read as zero.
    with pytest.raises(ValidationError):
        Config(CLI_ANALYTICS_RAW_RETENTION_DAYS=100, CLI_ANALYTICS_ROLLUP_RETENTION_DAYS=90)

    kept = Config(CLI_ANALYTICS_RAW_RETENTION_DAYS=90, CLI_ANALYTICS_ROLLUP_RETENTION_DAYS=90)
    assert kept.CLI_ANALYTICS_ROLLUP_RETENTION_DAYS == 90
