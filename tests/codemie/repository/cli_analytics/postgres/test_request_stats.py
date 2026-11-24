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

"""The per-request stats context: pool wait, statements and SQL time counted at the connection (EPMCDME-17301)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from prometheus_client import CollectorRegistry

from codemie.configs.prometheus_config import register_cli_analytics_metrics
from codemie.repository.cli_analytics.postgres import request_stats, user_rollup
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from tests.codemie.repository.cli_analytics.postgres.test_engine import SETTINGS


class LoggingConnection:
    """A connection that keeps the query loggers it is given, like asyncpg's."""

    def __init__(self) -> None:
        self.loggers: list = []

    def add_query_logger(self, callback) -> None:
        self.loggers.append(callback)

    def remove_query_logger(self, callback) -> None:
        self.loggers.remove(callback)

    def run_statement(self, elapsed: float) -> None:
        for callback in list(self.loggers):
            callback(SimpleNamespace(elapsed=elapsed))


class Pool:
    def __init__(self, connection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def acquire(self, timeout=None):
        yield self.connection


def make_engine(connection) -> AnalyticsPgEngine:
    async def factory(**kwargs):
        return Pool(connection)

    return AnalyticsPgEngine(SETTINGS, pool_factory=factory)


@pytest.fixture
def registry():
    reg = CollectorRegistry()
    register_cli_analytics_metrics(reg)
    yield reg
    request_stats.install_metrics(None)


def sample(reg, name, **labels):
    return reg.get_sample_value(name, labels)


def test_there_is_no_stats_outside_a_request():
    assert request_stats.current() is None


@pytest.mark.asyncio
async def test_statements_and_sql_time_are_counted_on_every_connection_of_the_request():
    conn = LoggingConnection()
    engine = make_engine(conn)
    with request_stats.open_request("get_users", "heavy") as stats:
        assert request_stats.current() is stats
        async with engine.acquire() as c:
            c.run_statement(0.25)
            c.run_statement(0.5)
        assert conn.loggers == []  # taken off before the connection goes back to the pool
    assert request_stats.current() is None
    assert stats.statements == 2
    assert stats.sql_time == pytest.approx(0.75)
    assert stats.pool_wait >= 0


@pytest.mark.asyncio
async def test_a_connection_outside_a_request_gets_no_logger():
    conn = LoggingConnection()
    async with make_engine(conn).acquire():
        assert conn.loggers == []


@pytest.mark.asyncio
async def test_in_use_and_waiting_gauges_follow_the_acquire_by_caller(registry):
    conn = LoggingConnection()
    engine = make_engine(conn)
    with request_stats.open_request("get_users", "heavy"):
        async with engine.acquire():
            assert sample(registry, "cli_analytics_pool_in_use", caller="dashboard") == 1
        assert sample(registry, "cli_analytics_pool_in_use", caller="dashboard") == 0
    async with engine.acquire():
        assert sample(registry, "cli_analytics_pool_in_use", caller="job") == 1
    async with engine.acquire_ingest(timeout=1):
        assert sample(registry, "cli_analytics_pool_in_use", caller="ingest") == 1
    assert sample(registry, "cli_analytics_pool_waiting") == 0
    assert sample(registry, "cli_analytics_ingest_acquire_wait_seconds_count") == 1


@pytest.mark.asyncio
async def test_a_failed_acquire_does_not_leave_the_waiting_gauge_up(registry):
    class Refusing:
        @asynccontextmanager
        async def acquire(self, timeout=None):
            raise TimeoutError
            yield

    async def factory(**kwargs):
        return Refusing()

    with pytest.raises(TimeoutError):
        async with AnalyticsPgEngine(SETTINGS, pool_factory=factory).acquire_ingest(timeout=1):
            pass
    assert sample(registry, "cli_analytics_pool_waiting") == 0
    assert sample(registry, "cli_analytics_pool_in_use", caller="ingest") in (None, 0)


def test_every_listed_metric_is_emitted_once_a_request_closes(registry):
    with request_stats.open_request("get_users", "heavy") as stats:
        stats.gate_wait = 0.1
        stats.pool_wait = 0.2
        stats.sql_time = 0.3
        stats.statements = 4
    request_stats.ingest_refused()
    request_stats.rollup_refused("rates")
    labels = {"endpoint": "get_users", "lane": "heavy"}
    for name in ("gate_wait_seconds", "pool_wait_seconds", "sql_seconds", "statements"):
        assert sample(registry, f"cli_analytics_request_{name}_count", **labels) == 1
    assert sample(registry, "cli_analytics_request_statements_sum", **labels) == 4
    assert sample(registry, "cli_analytics_ingest_503_total") == 1
    assert sample(registry, "cli_analytics_rollup_refused_total", reason="rates") == 1


def test_helpers_are_silent_without_registered_metrics():
    request_stats.install_metrics(None)
    request_stats.ingest_refused()
    request_stats.rollup_refused("missing_day")
    with request_stats.open_request("x", "heavy"):
        pass


def _filter(**kw):
    from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
    from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE

    return LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=datetime(2026, 1, 1), end_dt=datetime(2026, 12, 31), **kw)


@pytest.mark.asyncio
async def test_a_rollup_refusal_counts_its_reason_and_names_no_filter_value(registry):
    now = datetime(2026, 12, 31, tzinfo=timezone.utc)
    f = _filter(users=["someone@example.com"])
    assert await user_rollup.serve_cohort(None, f, lambda m: None, "v1", max_age_s=3600, now=now) is None
    assert await user_rollup.serve_cohort(None, _filter(), lambda m: None, None, max_age_s=3600, now=now) is None
    assert sample(registry, "cli_analytics_rollup_refused_total", reason="eligibility") == 1
    assert sample(registry, "cli_analytics_rollup_refused_total", reason="rates") == 1
    from prometheus_client import generate_latest

    assert "example.com" not in generate_latest(registry).decode()
