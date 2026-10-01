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

"""Partition planning and maintenance runs for the PostgreSQL analytics tables (no database)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from codemie.repository.cli_analytics.postgres import maintenance
from codemie.repository.cli_analytics.postgres.maintenance import (
    LEDGER_TABLE,
    RAW_TABLES,
    ROLLUP_TABLES,
    Partition,
    PartitionMaintainer,
    RetentionPolicy,
    expired_partitions,
    planned_partitions,
    week_start,
)

POLICY = RetentionPolicy(raw_days=90, rollup_days=365, dedup_days=14, premake_weeks=4)
TODAY = date(2026, 9, 23)  # a Wednesday


def _by_table(partitions: list[Partition]) -> dict[str, list[Partition]]:
    out: dict[str, list[Partition]] = {}
    for p in partitions:
        out.setdefault(p.table, []).append(p)
    return out


def test_weeks_start_on_monday():
    assert week_start(date(2026, 9, 23)) == date(2026, 9, 21)
    assert week_start(date(2026, 9, 21)) == date(2026, 9, 21)
    assert week_start(date(2026, 9, 27)) == date(2026, 9, 21)


def test_weekly_partitions_are_named_by_iso_week_including_year_boundaries():
    assert Partition.weekly("spans", date(2026, 9, 21)).name == "spans_w2026_39"
    # 2026-12-28 is the Monday of ISO week 53 of 2026.
    assert Partition.weekly("spans", date(2026, 12, 28)).name == "spans_w2026_53"
    assert Partition.weekly("spans", date(2027, 1, 4)).name == "spans_w2027_01"


def test_raw_tables_get_weekly_partitions_from_the_retention_start_through_the_premake_horizon():
    raw = _by_table(planned_partitions(TODAY, POLICY))["log_events"]

    assert raw[0].lower == date(2026, 6, 22)  # Monday of the week holding today - 90 days
    assert raw[-1].lower == date(2026, 10, 19)  # this week + 4 weeks
    assert all(p.upper - p.lower == timedelta(days=7) for p in raw)
    assert all(a.upper == b.lower for a, b in zip(raw, raw[1:], strict=False))


def test_rollups_get_monthly_partitions_covering_a_year_and_the_premake_horizon():
    cost = _by_table(planned_partitions(TODAY, POLICY))["cost_daily"]

    assert cost[0].lower == date(2025, 9, 1)  # month holding today - 365 days
    assert cost[-1].lower == date(2026, 10, 1)  # month holding today + 4 weeks
    assert cost[-1].upper == date(2026, 11, 1)


def test_ledger_gets_daily_partitions_for_the_dedup_window_and_the_premake_horizon():
    ledger = _by_table(planned_partitions(TODAY, POLICY))[LEDGER_TABLE]

    assert ledger[0].lower == date(2026, 9, 9)
    assert ledger[-1].lower == date(2026, 10, 21)
    assert ledger[0].name == "ingest_dedup_d20260909"


def test_every_partitioned_table_is_planned():
    tables = set(_by_table(planned_partitions(TODAY, POLICY)))

    assert tables == {*RAW_TABLES, *ROLLUP_TABLES, LEDGER_TABLE, "invocations_hourly"}


def test_raw_partition_ddl_uses_utc_timestamp_bounds_and_quoted_names():
    ddl = Partition.weekly("log_events", date(2026, 9, 21)).create_sql()

    assert ddl == (
        'CREATE TABLE IF NOT EXISTS "log_events_w2026_39" PARTITION OF "log_events" '
        "FOR VALUES FROM ('2026-09-21 00:00:00+00') TO ('2026-09-28 00:00:00+00')"
    )


def test_rollup_partition_ddl_uses_date_bounds_and_leaves_room_for_rewrites():
    ddl = Partition.monthly("cost_daily", date(2026, 9, 1)).create_sql()

    assert ddl == (
        'CREATE TABLE IF NOT EXISTS "cost_daily_m202609" PARTITION OF "cost_daily" '
        "FOR VALUES FROM ('2026-09-01') TO ('2026-10-01') "
        "WITH (fillfactor = 90, autovacuum_vacuum_scale_factor = 0.05)"
    )


def test_hourly_rollup_is_partitioned_monthly_on_timestamps():
    ddl = Partition.monthly("invocations_hourly", date(2026, 12, 1)).create_sql()

    assert "FROM ('2026-12-01 00:00:00+00') TO ('2027-01-01 00:00:00+00')" in ddl


def test_expired_partitions_are_the_ones_entirely_older_than_retention():
    existing = [
        ("log_events", "log_events_w2026_25"),  # 2026-06-15..06-22: ends before today - 90 days (06-25)
        ("log_events", "log_events_w2026_26"),  # 2026-06-22..06-29: still holds retained days
        ("log_events", "log_events_default"),
        ("cost_daily", "cost_daily_m202508"),  # ends 2025-09-01, before today - 365
        ("cost_daily", "cost_daily_m202509"),
        ("ingest_dedup", "ingest_dedup_d20260908"),
        ("ingest_dedup", "ingest_dedup_d20260909"),
        ("log_events", "something_else"),
    ]

    expired = expired_partitions(existing, TODAY, POLICY)

    assert expired == [
        ("log_events", "log_events_w2026_25"),
        ("cost_daily", "cost_daily_m202508"),
        ("ingest_dedup", "ingest_dedup_d20260908"),
    ]


@pytest.mark.parametrize("name", ["log_events_default", "log_events_w2026_xx", "cost_daily_m2026", "other_w2026_01"])
def test_unrecognised_names_are_never_dropped(name):
    assert expired_partitions([("log_events", name), ("cost_daily", name)], date(2030, 1, 1), POLICY) == []


# ── PartitionMaintainer.run, with the database steps faked ────────────────────


class RecordingConnection:
    """Records every statement; `today` answers the database-date query."""

    def __init__(self, today: date = TODAY) -> None:
        self.statements: list[str] = []
        self.today = today

    async def execute(self, sql, *args, timeout=None):
        self.statements.append(" ".join(sql.split()))
        return "DELETE 0"

    async def fetchval(self, sql, *args, timeout=None):
        self.statements.append(" ".join(sql.split()))
        return self.today if "now()" in sql else 0

    @asynccontextmanager
    async def _tx(self):
        yield

    def transaction(self):
        return self._tx()


def _steps(**failures):
    names = ("_ensure_partitions", "_drop_expired", "_purge_expired_rows", "_count_default_rows")
    return {name: AsyncMock(side_effect=failures.get(name)) for name in names}


@pytest.mark.asyncio
async def test_a_failing_step_is_logged_and_the_later_steps_still_run():
    steps = _steps(
        _ensure_partitions=asyncpg.exceptions.QueryCanceledError("canceling statement due to statement timeout")
    )
    maintainer = PartitionMaintainer(MagicMock(), POLICY, clock=lambda: TODAY)

    with patch.multiple(PartitionMaintainer, **steps), patch.object(maintenance, "logger") as logger:
        report = await maintainer.run(RecordingConnection())  # type: ignore[arg-type]

    # Retention must keep dropping and purging even while partition creation keeps failing.
    for name in ("_drop_expired", "_purge_expired_rows", "_count_default_rows"):
        steps[name].assert_awaited_once()
    assert report.failed == ["_ensure_partitions"]
    assert "_ensure_partitions" in logger.exception.call_args.args[0]


@pytest.mark.asyncio
async def test_retention_follows_the_database_date_not_the_pods_clock():
    steps = _steps()
    conn = RecordingConnection(today=date(2026, 12, 1))

    with patch.multiple(PartitionMaintainer, **steps):
        await PartitionMaintainer(MagicMock(), POLICY).run(conn)  # type: ignore[arg-type]

    assert steps["_drop_expired"].await_args.args[-2] == date(2026, 12, 1)


@pytest.mark.asyncio
async def test_rows_move_out_of_default_while_it_is_locked_against_inserts():
    conn = RecordingConnection()
    partition = next(p for p in planned_partitions(TODAY, POLICY) if p.table == "log_events")

    await PartitionMaintainer(MagicMock(), POLICY, clock=lambda: TODAY)._create_moving_default_rows(conn, partition)  # type: ignore[arg-type]

    statements = conn.statements
    lock = statements.index('LOCK TABLE "log_events_default" IN SHARE ROW EXCLUSIVE MODE')
    move = next(
        i for i, sql in enumerate(statements) if sql.startswith('WITH moved AS (DELETE FROM "log_events_default"')
    )
    attach = next(i for i, sql in enumerate(statements) if "ATTACH PARTITION" in sql)
    # A row inserted between the move and the attach would make ATTACH fail.
    assert lock < move < attach


SERVER_TIMEOUT = asyncpg.exceptions.QueryCanceledError("canceling statement due to statement timeout")


class FailingConnection(RecordingConnection):
    """Fails every statement that contains `needle`; knows no existing partition."""

    def __init__(self, needle: str, error: BaseException = SERVER_TIMEOUT) -> None:
        super().__init__()
        self.needle = needle
        self.error = error

    async def execute(self, sql, *args, timeout=None):
        if self.needle in sql:
            raise self.error
        return await super().execute(sql, *args, timeout=timeout)

    async def fetch(self, sql, *args):
        return []


@pytest.mark.parametrize("error", [SERVER_TIMEOUT, TimeoutError()], ids=["server-side", "client-side"])
@pytest.mark.asyncio
async def test_a_partition_that_keeps_failing_does_not_stop_the_later_ones(error):
    failing = next(p for p in planned_partitions(TODAY, POLICY) if p.table == "log_events")
    conn = FailingConnection(f'"{failing.name}"', error)
    maintainer = PartitionMaintainer(MagicMock(), POLICY, clock=lambda: TODAY)

    with patch.object(maintenance, "logger"):
        report = await maintainer.run(conn)  # type: ignore[arg-type]

    expected = {p.name for p in planned_partitions(TODAY, POLICY)} - {failing.name}
    assert set(report.created) == expected  # later weeks, rollup months and ledger days too
    assert failing.name in report.failed
    assert report.defaults_ready


@pytest.mark.asyncio
async def test_a_default_partition_that_cannot_be_created_is_reported_not_ready():
    conn = FailingConnection('"spans_default"')

    with patch.object(maintenance, "logger"):
        report = await PartitionMaintainer(MagicMock(), POLICY, clock=lambda: TODAY).run(conn)  # type: ignore[arg-type]

    assert not report.defaults_ready  # ingest into spans has no partition to land in
    assert "spans_default" in report.failed


class ExpiredPartitionsConnection(FailingConnection):
    """Holds three expired partitions (sorted: cost_daily, ingest_dedup, log_events)."""

    EXISTING = [
        {"parent": "cost_daily", "child": "cost_daily_m202508"},
        {"parent": "ingest_dedup", "child": "ingest_dedup_d20260908"},
        {"parent": "log_events", "child": "log_events_w2026_25"},
    ]

    async def fetch(self, sql, *args):
        return self.EXISTING


@pytest.mark.parametrize(
    "error",
    [asyncpg.exceptions.DependentObjectsStillExistError("cannot drop"), SERVER_TIMEOUT, TimeoutError()],
    ids=["dependency", "server-side", "client-side"],
)
@pytest.mark.asyncio
async def test_an_expired_partition_that_cannot_be_dropped_does_not_keep_the_later_ones(error):
    # Retention must go on for every other table: their rows carry user identities.
    conn = ExpiredPartitionsConnection('DROP TABLE IF EXISTS "cost_daily_m202508"', error)
    report = maintenance.MaintenanceReport()

    with patch.object(maintenance, "logger"):
        await PartitionMaintainer(MagicMock(), POLICY, clock=lambda: TODAY)._drop_expired(conn, TODAY, report)  # type: ignore[arg-type]

    assert report.dropped == ["ingest_dedup_d20260908", "log_events_w2026_25"]
    assert report.failed == ["cost_daily_m202508"]


@pytest.mark.parametrize("error", [SERVER_TIMEOUT, TimeoutError()], ids=["server-side", "client-side"])
@pytest.mark.asyncio
async def test_a_purge_that_fails_does_not_stop_the_others(error):
    conn = FailingConnection("DELETE FROM session_dims", error)

    with patch.object(maintenance, "logger"):
        report = await PartitionMaintainer(MagicMock(), POLICY, clock=lambda: TODAY).run(conn)  # type: ignore[arg-type]

    purges = [sql for sql in conn.statements if sql.startswith("DELETE FROM")]
    assert any(sql.startswith("DELETE FROM session_skills") for sql in purges)
    assert any(sql.startswith("DELETE FROM session_attributes") for sql in purges)
    assert "session_dims" in " ".join(report.failed)


class PoolTimeoutConnection(RecordingConnection):
    """Times statements out like asyncpg: one without `timeout=` gets the pool's command
    timeout (a dashboard query's limit), which moving or purging a large table outlasts."""

    POOL_TIMEOUT_S = 35
    SLOW = ("WITH moved AS", "DELETE FROM", "ATTACH PARTITION")
    DURATION_S = 120  # past the pool's limit, within the raised one

    def _run(self, sql: str, timeout: float | None) -> None:
        if any(s in sql for s in self.SLOW) and (timeout or self.POOL_TIMEOUT_S) < self.DURATION_S:
            raise TimeoutError

    async def execute(self, sql, *args, timeout=None):
        self._run(sql, timeout)
        return await super().execute(sql, *args, timeout=timeout)

    async def fetchval(self, sql, *args, timeout=None):
        self._run(sql, timeout)
        return await super().fetchval(sql, *args, timeout=timeout)


@pytest.mark.asyncio
async def test_moves_and_purges_are_not_cut_short_by_the_pools_client_timeout():
    conn = PoolTimeoutConnection()
    partition = next(p for p in planned_partitions(TODAY, POLICY) if p.table == "log_events")
    maintainer = PartitionMaintainer(MagicMock(), POLICY, clock=lambda: TODAY)
    report = maintenance.MaintenanceReport()

    await maintainer._create_moving_default_rows(conn, partition)  # type: ignore[arg-type]
    await maintainer._purge_expired_rows(conn, TODAY, report)  # type: ignore[arg-type]

    assert report.failed == []
    raised = f"SET LOCAL statement_timeout = '{maintenance.MAINTENANCE_STATEMENT_TIMEOUT_S}s'"
    assert conn.statements.count(raised) == 1 + len([s for s in conn.statements if s.startswith("DELETE FROM")])


@pytest.mark.asyncio
async def test_a_step_cut_short_on_the_client_side_does_not_stop_the_others():
    steps = _steps(_purge_expired_rows=TimeoutError())
    maintainer = PartitionMaintainer(MagicMock(), POLICY, clock=lambda: TODAY)

    with patch.multiple(PartitionMaintainer, **steps), patch.object(maintenance, "logger"):
        report = await maintainer.run(RecordingConnection())  # type: ignore[arg-type]

    steps["_count_default_rows"].assert_awaited_once()
    assert report.failed == ["_purge_expired_rows"]
