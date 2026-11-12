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

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from codemie.repository.cli_analytics.ports import ModelRates
from codemie.repository.cli_analytics.postgres import maintenance, session_activity, session_sort_keys
from codemie.repository.cli_analytics.postgres.rates_version import rates_version
from codemie.repository.cli_analytics.postgres.maintenance import (
    LEDGER_TABLE,
    RAW_TABLES,
    ROLLUP_TABLES,
    Partition,
    PartitionMaintainer,
    expired_partitions,
    planned_partitions,
    week_start,
)

from tests.codemie.repository.cli_analytics.support.maintenance_connections import (
    POLICY,
    SERVER_TIMEOUT,
    TODAY,
    FailingConnection,
    PoolTimeoutConnection,
    RecordingConnection,
)


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


_REAL_DERIVE_SESSION_ACTIVITY = PartitionMaintainer._derive_session_activity  # before the fixture below replaces it


@pytest.fixture(autouse=True)
def _no_activity_backfill():  # also the sort-key step, which needs rates and a database
    """The other tests' connection fakes do not model the backfill's statements, so the step is replaced there; the
    tests of the step itself (the end of this file) call `_REAL_DERIVE_SESSION_ACTIVITY` with the backfill patched."""
    with (
        patch.object(PartitionMaintainer, "_derive_session_activity", AsyncMock()),
        patch.object(PartitionMaintainer, "_backfill_session_sort_keys", AsyncMock()),
        patch.object(PartitionMaintainer, "_backfill_model_groups", AsyncMock()),
    ):
        yield


def _steps(**failures):
    names = (
        "_ensure_partitions",
        "_drop_expired",
        "_purge_expired_rows",
        "_derive_session_activity",
        "_backfill_session_sort_keys",
        "_backfill_model_groups",
        "_count_default_rows",
    )
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
    for name in ("_drop_expired", "_purge_expired_rows", "_derive_session_activity", "_count_default_rows"):
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

    async def fetch(self, sql, *args, timeout=None):
        if sql.startswith("SELECT DISTINCT session_id"):  # the sessions of a partition, one batch, then none
            self.statements.append(sql)
            return [{"session_id": "s1"}, {"session_id": "s2"}] if args[0] == "" else []
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


@pytest.mark.asyncio
async def test_an_expired_partition_of_a_sort_key_source_unstamps_its_sessions_in_batches_before_it_is_dropped():
    conn = ExpiredPartitionsConnection("no statement fails")
    report = maintenance.MaintenanceReport()

    await PartitionMaintainer(MagicMock(), POLICY, clock=lambda: TODAY)._drop_expired(conn, TODAY, report)  # type: ignore[arg-type]

    statements = conn.statements
    drop = statements.index('DROP TABLE IF EXISTS "cost_daily_m202508"')
    walk = next(i for i, sql in enumerate(statements) if sql.startswith("SELECT DISTINCT session_id"))
    batch = next(
        i
        for i, sql in enumerate(statements)
        if sql.startswith("UPDATE session_dims SET rates_version = NULL WHERE session_id = ANY")
    )
    inside = next(i for i, sql in enumerate(statements) if 'SELECT session_id FROM "cost_daily_m202508"' in sql)
    # the stored keys read these rows: the sessions lose their stamp, first in committed batches (a walk of the
    # partition by session_id, not one statement over all of it), then the DROP's own transaction catches the rest
    assert walk < batch < inside < drop
    assert 'FROM "cost_daily_m202508"' in statements[walk] and "ORDER BY session_id LIMIT" in statements[walk]
    assert "rates_version IS NOT NULL" in statements[inside]
    # a table the keys are not derived from drops without it
    others = [sql for sql in statements if sql.startswith("UPDATE session_dims")]
    assert len(others) == 2


@pytest.mark.asyncio
async def test_the_purge_unstamps_the_sessions_of_the_rows_it_deletes_and_counts_only_the_deleted_rows():
    conn = FailingConnection("no statement fails")

    await PartitionMaintainer(MagicMock(), POLICY, clock=lambda: TODAY).run(conn)  # type: ignore[arg-type]

    statements = conn.statements
    for source, delete in (
        ("session_skills", "DELETE FROM session_skills"),
        ("cost_daily_default", 'DELETE FROM "cost_daily_default"'),
        ("invocations_hourly_default", 'DELETE FROM "invocations_hourly_default"'),
    ):
        unstamp = next(i for i, sql in enumerate(statements) if sql.startswith("UPDATE session_dims") and source in sql)
        removal = next(i for i, sql in enumerate(statements) if sql.startswith(delete))
        assert unstamp < removal, source  # also in one transaction: the group runs inside `conn.transaction()`
    assert not any(
        sql.startswith("UPDATE session_dims") and ("ingest_dedup" in sql or "log_events" in sql) for sql in statements
    )


@pytest.mark.asyncio
async def test_the_unstamped_sessions_are_not_counted_as_purged_rows():
    class _Counting(FailingConnection):
        async def execute(self, sql, *args, timeout=None):
            await super().execute(sql, *args, timeout=timeout)
            return "UPDATE 7" if sql.startswith("UPDATE session_dims") else "DELETE 3"

    conn = _Counting("no statement fails")

    report = await PartitionMaintainer(MagicMock(), POLICY, clock=lambda: TODAY).run(conn)  # type: ignore[arg-type]

    deletes = sum(1 for sql in conn.statements if sql.startswith("DELETE FROM"))
    assert deletes and report.purged_rows == deletes * 3  # an UPDATE of 7 sessions adds nothing


@pytest.mark.parametrize("error", [SERVER_TIMEOUT, TimeoutError()], ids=["server-side", "client-side"])
@pytest.mark.asyncio
async def test_a_purge_that_fails_does_not_stop_the_others(error):
    conn = FailingConnection("DELETE FROM session_skills", error)

    with patch.object(maintenance, "logger"):
        report = await PartitionMaintainer(MagicMock(), POLICY, clock=lambda: TODAY).run(conn)  # type: ignore[arg-type]

    purges = [sql for sql in conn.statements if sql.startswith("DELETE FROM")]
    assert any(sql.startswith("DELETE FROM session_attributes") for sql in purges)
    assert "session_skills" in " ".join(report.failed)


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


def test_usage_requests_is_a_raw_table_partitioned_on_ts() -> None:
    assert "usage_requests" in RAW_TABLES
    assert maintenance.PARTITION_KEY["usage_requests"] == "ts"
    assert "usage_requests" in maintenance.PARTITIONED_TABLES


def _run_conn_with_backlog() -> RecordingConnection:
    """RecordingConnection answers fetchval with the database date; the activity step's count needs a number."""
    conn = RecordingConnection()
    original = conn.fetchval

    async def fetchval(sql, *args, **kwargs):
        return 0 if "has_activity IS NULL" in sql else await original(sql, *args, **kwargs)

    conn.fetchval = fetchval  # type: ignore[method-assign]
    return conn


def _conn_with_backlog(backlog: int) -> MagicMock:
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=backlog)
    return conn


@pytest.mark.asyncio
async def test_the_activity_step_runs_the_backfill_with_its_batch_budget_and_statement_timeout_and_reports_the_count():
    conn = _conn_with_backlog(10)
    report = maintenance.MaintenanceReport()
    backfill = AsyncMock(return_value=1234)

    with patch.object(session_activity, "backfill_session_activity", backfill):
        await _REAL_DERIVE_SESSION_ACTIVITY(PartitionMaintainer(MagicMock(), POLICY), conn, report)  # type: ignore[arg-type]

    # 5,000 sessions a transaction, the steady 120 s with a small backlog, the maintenance statement timeout: a wrong
    # value or a dropped result would leave the sessions that predate the facts NULL for good
    backfill.assert_awaited_once_with(conn, 5000, 120.0, maintenance.MAINTENANCE_STATEMENT_TIMEOUT_S)
    assert report.derived_sessions == 1234


@pytest.mark.asyncio
async def test_the_activity_step_works_longer_while_a_large_backlog_is_left_and_not_at_the_threshold() -> None:
    above, at = maintenance._ACTIVITY_CATCH_UP_ABOVE + 1, maintenance._ACTIVITY_CATCH_UP_ABOVE
    for backlog, budget in ((above, 600.0), (at, 120.0), (0, 120.0)):
        conn = _conn_with_backlog(backlog)
        backfill = AsyncMock(return_value=0)

        with patch.object(session_activity, "backfill_session_activity", backfill):
            await _REAL_DERIVE_SESSION_ACTIVITY(  # type: ignore[arg-type]
                PartitionMaintainer(MagicMock(), POLICY), conn, maintenance.MaintenanceReport()
            )

        assert backfill.await_args.args[2] == budget, backlog


@pytest.mark.asyncio
async def test_the_backlog_count_never_reads_more_rows_than_the_catch_up_threshold_needs() -> None:
    conn = _conn_with_backlog(0)

    with patch.object(session_activity, "backfill_session_activity", AsyncMock(return_value=0)):
        await _REAL_DERIVE_SESSION_ACTIVITY(  # type: ignore[arg-type]
            PartitionMaintainer(MagicMock(), POLICY), conn, maintenance.MaintenanceReport()
        )

    sql = conn.fetchval.await_args.args[0]
    assert f"LIMIT {maintenance._ACTIVITY_CATCH_UP_ABOVE + 1}" in sql and "has_activity IS NULL" in sql


@pytest.mark.asyncio
async def test_the_activity_step_adds_to_what_the_report_already_counts():
    report = maintenance.MaintenanceReport(derived_sessions=10)

    with patch.object(session_activity, "backfill_session_activity", AsyncMock(return_value=5)):
        await _REAL_DERIVE_SESSION_ACTIVITY(  # type: ignore[arg-type]
            PartitionMaintainer(MagicMock(), POLICY), _conn_with_backlog(0), report
        )

    assert report.derived_sessions == 15


@pytest.mark.asyncio
async def test_a_failing_activity_step_is_reported_and_does_not_stop_the_other_steps():
    steps = _steps()
    del steps["_derive_session_activity"]  # the real step runs, with a backfill that fails
    maintainer = PartitionMaintainer(MagicMock(), POLICY, clock=lambda: TODAY)
    failing = AsyncMock(
        side_effect=asyncpg.exceptions.QueryCanceledError("canceling statement due to statement timeout")
    )

    with (
        patch.multiple(PartitionMaintainer, **steps),
        patch.object(PartitionMaintainer, "_derive_session_activity", _REAL_DERIVE_SESSION_ACTIVITY),
        patch.object(session_activity, "backfill_session_activity", failing),
        patch.object(maintenance, "logger"),
    ):
        report = await maintainer.run(_run_conn_with_backlog())  # type: ignore[arg-type]

    assert report.failed == ["_derive_session_activity"]
    steps["_count_default_rows"].assert_awaited_once()  # the step after it still ran
    failing.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_activity_step_runs_after_the_retention_steps():
    order: list[str] = []
    steps = _steps()
    for name, step in steps.items():
        step.side_effect = lambda *a, _n=name, **k: order.append(_n)

    with patch.multiple(PartitionMaintainer, **steps):
        await PartitionMaintainer(MagicMock(), POLICY, clock=lambda: TODAY).run(RecordingConnection())  # type: ignore[arg-type]

    # retention first: a slow backfill must not delay dropping partitions or purging sessions
    assert order.index("_derive_session_activity") > order.index("_purge_expired_rows")


def _rates_for(names):  # the rates callable the step is given
    return {n: ModelRates(model=n, input=1e-6) for n in names}


_REAL_SORT_KEYS_STEP = PartitionMaintainer._backfill_session_sort_keys


def _result(derived=0, repriced=0):
    return session_sort_keys.SortKeysResult(derived, repriced, 1, 0.1, 100)


@pytest.mark.asyncio
async def test_the_sort_keys_step_backfills_then_re_prices_with_the_rates_it_was_given_and_reports_the_counts():
    maintainer = PartitionMaintainer(MagicMock(), POLICY)
    maintainer.set_rates(lambda: "v-now", _rates_for)
    backfill, reprice = AsyncMock(return_value=_result(derived=7)), AsyncMock(return_value=_result(repriced=3))
    auto_enable = AsyncMock(return_value=False)
    report = maintenance.MaintenanceReport()

    with (
        patch.object(session_sort_keys, "backfill_session_sort_keys", backfill),
        patch.object(session_sort_keys, "reprice_cost_keys", reprice),
        patch.object(session_sort_keys, "auto_enable_session_sort_keys", auto_enable),
    ):
        await _REAL_SORT_KEYS_STEP(maintainer, MagicMock(), report)

    # the stamp is the current catalog version, the one the readers compare with; the rates callable is handed on
    for call in (backfill, reprice):
        args = call.await_args.args
        assert args[1:4] == (_rates_for, "v-now", maintenance._SORT_KEYS_BATCH)
        assert call.await_args.kwargs["budget_s"] <= maintenance._SORT_KEYS_BUDGET_S
    assert (report.sort_keys_derived, report.sort_keys_repriced) == (7, 3)
    # last, the readers are switched on once every session carries the current stamp
    assert auto_enable.await_args.args[1] == "v-now"


@pytest.mark.asyncio
async def test_the_sort_keys_step_splits_one_budget_between_backfill_and_re_price():
    maintainer = PartitionMaintainer(MagicMock(), POLICY)
    maintainer.set_rates(lambda: "v-now", _rates_for)
    backfill = AsyncMock(return_value=_result())
    reprice = AsyncMock(return_value=_result())

    with (
        patch.object(session_sort_keys, "backfill_session_sort_keys", backfill),
        patch.object(session_sort_keys, "reprice_cost_keys", reprice),
        patch.object(session_sort_keys, "auto_enable_session_sort_keys", AsyncMock(return_value=False)),
    ):
        await _REAL_SORT_KEYS_STEP(maintainer, MagicMock(), maintenance.MaintenanceReport())

    # the backfill may not take it all; the re-price gets what the real time left of the one budget (here nearly all)
    assert backfill.await_args.kwargs["budget_s"] == maintenance._SORT_KEYS_BUDGET_S / 2
    assert 0 < reprice.await_args.kwargs["budget_s"] <= maintenance._SORT_KEYS_BUDGET_S


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version", "rates"),
    [(None, _rates_for), (lambda: "v-now", None), (lambda: rates_version({}), _rates_for)],
    ids=["no-version", "no-rates", "empty-catalog"],
)
async def test_the_sort_keys_step_does_nothing_without_rates(version, rates):
    maintainer = PartitionMaintainer(MagicMock(), POLICY)
    maintainer.set_rates(version, rates)
    backfill = AsyncMock()

    with (
        patch.object(session_sort_keys, "backfill_session_sort_keys", backfill),
        patch.object(session_sort_keys, "reprice_cost_keys", backfill),
    ):
        await _REAL_SORT_KEYS_STEP(maintainer, MagicMock(), maintenance.MaintenanceReport())

    backfill.assert_not_awaited()  # an empty catalog would stamp every session with a zero price


@pytest.mark.asyncio
async def test_a_failing_sort_keys_step_is_reported_and_the_other_steps_run():
    steps = _steps()
    del steps["_backfill_session_sort_keys"]
    maintainer = PartitionMaintainer(MagicMock(), POLICY, clock=lambda: TODAY)
    maintainer.set_rates(lambda: "v-now", _rates_for)
    failing = AsyncMock(side_effect=asyncpg.exceptions.QueryCanceledError("statement timeout"))

    with (
        patch.multiple(PartitionMaintainer, **steps),
        patch.object(PartitionMaintainer, "_backfill_session_sort_keys", _REAL_SORT_KEYS_STEP),
        patch.object(session_sort_keys, "backfill_session_sort_keys", failing),
        patch.object(maintenance, "logger"),
    ):
        report = await maintainer.run(RecordingConnection())  # type: ignore[arg-type]

    assert report.failed == ["_backfill_session_sort_keys"]
    steps["_count_default_rows"].assert_awaited_once()


@pytest.mark.asyncio
async def test_the_sort_keys_step_runs_after_the_retention_steps_and_the_activity_step():
    order: list[str] = []
    steps = _steps()
    for name, step in steps.items():
        step.side_effect = lambda *a, _n=name, **k: order.append(_n)

    with patch.multiple(PartitionMaintainer, **steps):
        await PartitionMaintainer(MagicMock(), POLICY, clock=lambda: TODAY).run(RecordingConnection())  # type: ignore[arg-type]

    assert order.index("_backfill_session_sort_keys") > order.index("_derive_session_activity")


_REAL_GROUPS_STEP = PartitionMaintainer._backfill_model_groups


def _groups_result(derived=0):
    return session_sort_keys.ModelGroupsResult(derived, 1, 0.1, 100)


@pytest.mark.asyncio
async def test_the_model_groups_step_derives_one_batch_with_the_rates_it_was_given_then_tries_the_auto_enable():
    maintainer = PartitionMaintainer(MagicMock(), POLICY)
    maintainer.set_rates(lambda: "v-now", _rates_for)
    backfill, auto_enable = AsyncMock(return_value=_groups_result(derived=4)), AsyncMock(return_value=False)
    report = maintenance.MaintenanceReport()

    with (
        patch.object(session_sort_keys, "backfill_model_groups", backfill),
        patch.object(session_sort_keys, "auto_enable_model_groups", auto_enable),
    ):
        await _REAL_GROUPS_STEP(maintainer, MagicMock(), report)

    args, kwargs = backfill.await_args.args, backfill.await_args.kwargs
    assert args[1:4] == (_rates_for, "v-now", maintenance._MODEL_GROUPS_BATCH)
    assert kwargs["max_batches"] == 1 and kwargs["pause_s"] == maintenance._SORT_KEYS_PAUSE_S
    assert report.model_groups_derived == 4
    auto_enable.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version", "rates"),
    [(None, _rates_for), (lambda: "v-now", None), (lambda: rates_version({}), _rates_for)],
    ids=["no-version", "no-rates", "empty-catalog"],
)
async def test_the_model_groups_step_does_nothing_without_rates(version, rates):
    maintainer = PartitionMaintainer(MagicMock(), POLICY)
    maintainer.set_rates(version, rates)
    backfill = AsyncMock()

    with patch.object(session_sort_keys, "backfill_model_groups", backfill):
        await _REAL_GROUPS_STEP(maintainer, MagicMock(), maintenance.MaintenanceReport())

    backfill.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_model_groups_step_runs_after_the_sort_keys_step_and_a_failure_is_isolated():
    order: list[str] = []
    steps = _steps()
    for name, step in steps.items():
        step.side_effect = lambda *a, _n=name, **k: order.append(_n)
    with patch.multiple(PartitionMaintainer, **steps):
        await PartitionMaintainer(MagicMock(), POLICY, clock=lambda: TODAY).run(RecordingConnection())  # type: ignore[arg-type]
    assert order.index("_backfill_model_groups") > order.index("_backfill_session_sort_keys")

    steps = _steps(_backfill_model_groups=asyncpg.exceptions.QueryCanceledError("statement timeout"))
    with patch.multiple(PartitionMaintainer, **steps), patch.object(maintenance, "logger"):
        report = await PartitionMaintainer(MagicMock(), POLICY, clock=lambda: TODAY).run(RecordingConnection())  # type: ignore[arg-type]
    assert report.failed == ["_backfill_model_groups"]
    steps["_count_default_rows"].assert_awaited_once()


@pytest.mark.asyncio
async def test_a_busy_parent_leaves_an_expired_partition_and_its_sessions_stamps_untouched():
    # The unstamping is committed before the DROP: without the lock probe a DROP that loses its lock wait would leave
    # the partition in place with its sessions unstamped, again on every run.
    from contextlib import asynccontextmanager

    class BusyParent:
        def __init__(self):
            self.executed = []

        async def execute(self, sql, *args, timeout=None):
            self.executed.append(sql)
            if sql.startswith("LOCK TABLE"):
                raise asyncpg.LockNotAvailableError("canceling statement due to lock timeout")

        def transaction(self):
            @asynccontextmanager
            async def tx():
                yield

            return tx()

    conn = BusyParent()
    maintainer = PartitionMaintainer(MagicMock(), MagicMock())  # the policy is not read: the partitions are given
    report = maintenance.MaintenanceReport()
    with (
        patch.object(maintainer, "_existing", AsyncMock(return_value=[("cost_daily", "cost_daily_p")])),
        patch.object(maintenance, "expired_partitions", return_value=[("cost_daily", "cost_daily_p")]),
        patch.object(maintainer, "_unstamp_sessions_of", AsyncMock()) as unstamp,
    ):
        await maintainer._drop_expired(conn, date(2026, 11, 12), report)

    unstamp.assert_not_awaited()
    assert not any(sql.startswith(("DROP", "UPDATE")) for sql in conn.executed)
    assert report.dropped == [] and report.failed == []
