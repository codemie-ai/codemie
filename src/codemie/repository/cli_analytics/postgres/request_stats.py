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

"""What one dashboard request cost the analytics pool: the wait for the gate and for a connection, the SQL time and the
number of statements. The stats live in a context variable, so the pool wrapper (`engine.acquire`) writes them without
the reader knowing. Prometheus objects are installed from outside (`install_metrics`); without them every helper is a
no-op. Nothing here takes a filter value, an e-mail or search text: a metric label is the endpoint, the lane, the
caller or the refusal reason, and nothing else."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass
class RequestStats:
    endpoint: str
    lane: str
    gate_wait: float = 0.0
    pool_wait: float = 0.0
    sql_time: float = 0.0
    statements: int = 0


@dataclass
class CliAnalyticsMetrics:
    """The Prometheus objects (built by `prometheus_config.register_cli_analytics_metrics`)."""

    gate_wait: Any
    pool_wait: Any
    sql_time: Any
    statements: Any
    in_use: Any
    waiting: Any
    ingest_acquire_wait: Any
    ingest_503: Any
    rollup_refused: Any


_stats: ContextVar[RequestStats | None] = ContextVar("cli_analytics_request_stats", default=None)
# Set by the route (its endpoint name) before the handler runs; `dashboard_request` reads it.
current_endpoint: ContextVar[str] = ContextVar("cli_analytics_endpoint", default="unknown")
# False while a light-lane rollup read runs: a merge of resumed sessions that must run live raises `LiveMergeNeeded`
# instead, for the caller to read again in the heavy lane.
live_merge_allowed: ContextVar[bool] = ContextVar("cli_analytics_live_merge_allowed", default=True)
_metrics: CliAnalyticsMetrics | None = None

REFUSAL_REASONS = ("rates", "missing_day", "stale", "too_many_live", "eligibility")


def install_metrics(metrics: CliAnalyticsMetrics | None) -> None:
    global _metrics  # noqa: PLW0603 - set once at startup by the Prometheus configuration, read by every request
    _metrics = metrics


def current() -> RequestStats | None:
    return _stats.get()


@contextlib.contextmanager
def open_request(endpoint: str, lane: str) -> Iterator[RequestStats]:
    """The stats of one request; its histograms are observed when it closes (also when it fails)."""
    stats = RequestStats(endpoint, lane)
    token = _stats.set(stats)
    try:
        yield stats
    finally:
        _stats.reset(token)
        m = _metrics
        if m is not None:
            labels = {"endpoint": endpoint, "lane": lane}
            m.gate_wait.labels(**labels).observe(stats.gate_wait)
            m.pool_wait.labels(**labels).observe(stats.pool_wait)
            m.sql_time.labels(**labels).observe(stats.sql_time)
            m.statements.labels(**labels).observe(stats.statements)


def log_line(stats: RequestStats) -> str:
    return (
        f"cli_analytics request endpoint={stats.endpoint} lane={stats.lane} gate_wait_s={stats.gate_wait:.3f} "
        f"pool_wait_s={stats.pool_wait:.3f} sql_s={stats.sql_time:.3f} statements={stats.statements}"
    )


def connection_logger(stats: RequestStats):
    """A query logger for asyncpg's `Connection.add_query_logger`: counts the statement and its time."""

    def log(record: Any) -> None:
        stats.statements += 1
        stats.sql_time += float(getattr(record, "elapsed", 0.0) or 0.0)

    return log


def pool_waiting(delta: int) -> None:
    if _metrics is not None:
        _metrics.waiting.inc(delta)


def pool_in_use(caller: str, delta: int) -> None:
    if _metrics is not None:
        _metrics.in_use.labels(caller=caller).inc(delta)


def ingest_acquire_waited(seconds: float) -> None:
    if _metrics is not None:
        _metrics.ingest_acquire_wait.observe(seconds)


def ingest_refused() -> None:
    """An ingest request answered 503."""
    if _metrics is not None:
        _metrics.ingest_503.inc()


def rollup_refused(reason: str) -> None:
    if _metrics is not None:
        _metrics.rollup_refused.labels(reason=reason).inc()
