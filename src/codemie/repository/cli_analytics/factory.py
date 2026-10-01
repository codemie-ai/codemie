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

"""Selects the CLI Analytics storage adapter from `CLI_ANALYTICS_STORAGE_BACKEND`.

The storage is built lazily on first use and once per process. Each adapter is imported
only when selected, so a PostgreSQL deployment never loads or configures ClickHouse.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from codemie.configs.config import config
from codemie.repository.cli_analytics.ports import CliAnalyticsReader, CliAnalyticsRuntime, CliTelemetryIngestor


@dataclass(frozen=True)
class CliAnalyticsStorage:
    backend: str
    reader: CliAnalyticsReader
    ingestor: CliTelemetryIngestor
    # Startup, background jobs and resources beyond request handling; None when the engine
    # needs none (ClickHouse: the Collector and materialized views do that work).
    runtime: CliAnalyticsRuntime | None = None
    # How far back raw rows reach: dashboard windows are clamped to it, so rollup-backed and
    # raw-backed numbers of one window always cover the same days.
    raw_retention_days: int = 90


_lock = threading.Lock()
_storage: CliAnalyticsStorage | None = None


def _build_clickhouse_storage() -> CliAnalyticsStorage:
    from codemie.clients.clickhouse import ch_query
    from codemie.repository.cli_analytics.clickhouse.ingestor import ClickHouseTelemetryIngestor
    from codemie.repository.cli_analytics.clickhouse.reader import RAW_TTL_DAYS, ClickHouseCliAnalyticsReader

    return CliAnalyticsStorage(
        backend="clickhouse",
        reader=ClickHouseCliAnalyticsReader(ch_query),
        ingestor=ClickHouseTelemetryIngestor(),
        raw_retention_days=RAW_TTL_DAYS,
    )


def _build_postgres_storage() -> CliAnalyticsStorage:
    from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
    from codemie.repository.cli_analytics.postgres.ingestor import PostgresTelemetryIngestor
    from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
    from codemie.repository.cli_analytics.postgres.runtime import PostgresAnalyticsRuntime
    from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings

    settings = AnalyticsPgSettings.from_config(config)
    engine = AnalyticsPgEngine(settings)  # one dedicated pool for ingest, reads and jobs
    return CliAnalyticsStorage(
        backend="postgres",
        reader=PostgresCliAnalyticsReader(engine),
        ingestor=PostgresTelemetryIngestor(engine),
        runtime=PostgresAnalyticsRuntime(engine, settings),
        raw_retention_days=settings.raw_retention_days,
    )


_BUILDERS = {
    "clickhouse": _build_clickhouse_storage,
    "postgres": _build_postgres_storage,
}


def get_cli_analytics_storage() -> CliAnalyticsStorage:
    """The storage adapter selected by configuration, built on first use."""
    global _storage
    if _storage is None:
        with _lock:
            if _storage is None:
                backend = config.CLI_ANALYTICS_STORAGE_BACKEND
                builder = _BUILDERS.get(backend)
                if builder is None:
                    raise ValueError(f"Unsupported CLI_ANALYTICS_STORAGE_BACKEND: {backend!r}")
                _storage = builder()
    return _storage


def reset_cli_analytics_storage() -> None:
    """Forget the built storage so the next call rebuilds it from the current configuration.

    For tests: the previous storage is neither closed nor are its jobs stopped.
    """
    global _storage
    with _lock:
        _storage = None
