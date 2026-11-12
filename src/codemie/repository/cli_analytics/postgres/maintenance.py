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

"""Partitions and retention of the PostgreSQL analytics tables.

Raw tables are partitioned by week, daily rollups and the hourly rollup by month, the
idempotency ledger by day. Retention drops whole partitions (no DELETE, no vacuum debt),
instead of expiring row by row. Partitions are created ahead of time; each
table also has a DEFAULT partition as a safety net for records outside every planned
range (a laptop clock far off, a spool delivered months late). When a planned partition's
range already has rows in DEFAULT, they are moved into the new partition.

The planning functions are pure; `PartitionMaintainer` applies the plan. Identifiers and
bounds in the DDL come from code constants and dates only, never from input.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

import asyncpg

from codemie.repository.cli_analytics.ports import RatesFor
from codemie.repository.cli_analytics.postgres import session_activity, session_purge, session_sort_keys
from codemie.repository.cli_analytics.postgres.rates_version import EMPTY_RATES_VERSION
from codemie.repository.cli_analytics.postgres.engine import CLIENT_TIMEOUT_MARGIN_S, AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings

logger = logging.getLogger(__name__)

RAW_TABLES: tuple[str, ...] = ("log_events", "hook_events", "spans", "metric_points", "usage_requests")
ROLLUP_TABLES: tuple[str, ...] = (
    "cost_daily",
    "lines_daily",
    "active_time_daily",
    "turns_daily",
    "tool_facts_daily",
    "session_files_daily",
)
HOURLY_TABLE = "invocations_hourly"
LEDGER_TABLE = "ingest_dedup"
PARTITIONED_TABLES: tuple[str, ...] = (*RAW_TABLES, *ROLLUP_TABLES, HOURLY_TABLE, LEDGER_TABLE)

# The column each table is range-partitioned on.
PARTITION_KEY: dict[str, str] = {
    **dict.fromkeys(RAW_TABLES, "ts"),
    **dict.fromkeys(ROLLUP_TABLES, "day"),
    HOURLY_TABLE: "hour",
    LEDGER_TABLE: "day",
}
_TIMESTAMP_KEYED = frozenset({*RAW_TABLES, HOURLY_TABLE})
# Tables whose rows the stored sort keys of a session are derived from, and that retention removes rows of: a session
# that loses rows there must lose its stamp in the same transaction, or the readers keep trusting keys of data that is
# gone (the backfill only derives unstamped sessions and the re-price only those of another catalog version).
_SORT_KEY_SOURCES = frozenset({"cost_daily", HOURLY_TABLE, "session_skills"})
# Sessions of an expiring partition are unstamped in batches of this many, each its own transaction, before the DROP.
_UNSTAMP_BATCH = 5000
_PARTITION_SESSIONS = "SELECT DISTINCT session_id FROM {source} WHERE session_id > $1 ORDER BY session_id LIMIT $2"
_UNSTAMP_SESSIONS_OF = (
    "UPDATE session_dims SET rates_version = NULL WHERE rates_version IS NOT NULL "
    "AND session_id IN (SELECT session_id FROM {source} {where})"
)
# Rollup slices are deleted and re-inserted by the refresher: leave room for the new rows
# on the same page and vacuum sooner than the default 20% of dead rows.
_REWRITTEN_STORAGE = "WITH (fillfactor = 90, autovacuum_vacuum_scale_factor = 0.05)"
_REWRITTEN = frozenset({*ROLLUP_TABLES, HOURLY_TABLE})

# DDL waits at most this long for a lock, then gives up until the next run instead of
# queueing ingest statements behind it for longer.
DDL_LOCK_TIMEOUT_MS = 2000


# Per-session tables with a session_id-leading primary key that go with their session. session_model_groups (stored
# model groups of the Sessions stamp, no foreign key) is derived and has no date: only its session decides.
_SESSION_CHILDREN = ("session_usage", "session_usage_hourly", "subagent_invocations", "session_model_groups")


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def month_start(d: date) -> date:
    return d.replace(day=1)


def next_month(d: date) -> date:
    return date(d.year + d.month // 12, d.month % 12 + 1, 1)


@dataclass(frozen=True)
class RetentionPolicy:
    raw_days: int
    rollup_days: int
    dedup_days: int
    premake_weeks: int
    session_days: int = 0  # 0: the session tables are not purged by age
    session_purge_batch_size: int = 5000

    @classmethod
    def from_settings(cls, settings: AnalyticsPgSettings) -> RetentionPolicy:
        return cls(
            raw_days=settings.raw_retention_days,
            rollup_days=settings.rollup_retention_days,
            dedup_days=settings.dedup_retention_days,
            premake_weeks=settings.partition_premake_weeks,
            session_days=settings.session_retention_days,
            session_purge_batch_size=settings.session_purge_batch_size,
        )

    def retention_days(self, table: str) -> int:
        if table in RAW_TABLES:
            return self.raw_days
        if table == LEDGER_TABLE:
            return self.dedup_days
        return self.rollup_days


@dataclass(frozen=True)
class Partition:
    table: str
    name: str
    lower: date
    upper: date

    @classmethod
    def weekly(cls, table: str, monday: date) -> Partition:
        iso_year, iso_week, _ = monday.isocalendar()
        return cls(table, f"{table}_w{iso_year}_{iso_week:02d}", monday, monday + timedelta(days=7))

    @classmethod
    def monthly(cls, table: str, first: date) -> Partition:
        return cls(table, f"{table}_m{first:%Y%m}", first, next_month(first))

    @classmethod
    def daily(cls, table: str, day: date) -> Partition:
        return cls(table, f"{table}_d{day:%Y%m%d}", day, day + timedelta(days=1))

    def _bound(self, d: date) -> str:
        return f"'{d.isoformat()} 00:00:00+00'" if self.table in _TIMESTAMP_KEYED else f"'{d.isoformat()}'"

    @property
    def bounds_sql(self) -> str:
        return f"FROM ({self._bound(self.lower)}) TO ({self._bound(self.upper)})"

    @property
    def storage_sql(self) -> str:
        return f" {_REWRITTEN_STORAGE}" if self.table in _REWRITTEN else ""

    def create_sql(self) -> str:
        return (
            f"CREATE TABLE IF NOT EXISTS {quote_ident(self.name)} PARTITION OF {quote_ident(self.table)} "
            f"FOR VALUES {self.bounds_sql}{self.storage_sql}"
        )

    def in_range_sql(self) -> str:
        key = quote_ident(PARTITION_KEY[self.table])
        return f"{key} >= {self._bound(self.lower)} AND {key} < {self._bound(self.upper)}"


def default_partition_name(table: str) -> str:
    return f"{table}_default"


def planned_partitions(today: date, policy: RetentionPolicy) -> list[Partition]:
    """Every partition that should exist today: the retention window plus the premake horizon."""
    horizon = today + timedelta(weeks=policy.premake_weeks)
    plan: list[Partition] = []
    for table in RAW_TABLES:
        monday = week_start(today - timedelta(days=policy.raw_days))
        while monday <= week_start(horizon):
            plan.append(Partition.weekly(table, monday))
            monday += timedelta(days=7)
    for table in (*ROLLUP_TABLES, HOURLY_TABLE):
        first = month_start(today - timedelta(days=policy.rollup_days))
        while first <= month_start(horizon):
            plan.append(Partition.monthly(table, first))
            first = next_month(first)
    day = today - timedelta(days=policy.dedup_days)
    while day <= horizon:
        plan.append(Partition.daily(LEDGER_TABLE, day))
        day += timedelta(days=1)
    return plan


def parse_partition(table: str, name: str) -> Partition | None:
    """The partition a name stands for, if it follows this module's naming for `table`."""
    prefix = f"{table}_"
    if not name.startswith(prefix):
        return None
    suffix = name[len(prefix) :]
    try:
        if table in RAW_TABLES and len(suffix) == 8 and suffix[0] == "w" and suffix[5] == "_":
            return Partition.weekly(table, date.fromisocalendar(int(suffix[1:5]), int(suffix[6:8]), 1))
        if table in (*ROLLUP_TABLES, HOURLY_TABLE) and len(suffix) == 7 and suffix[0] == "m":
            return Partition.monthly(table, date(int(suffix[1:5]), int(suffix[5:7]), 1))
        if table == LEDGER_TABLE and len(suffix) == 9 and suffix[0] == "d":
            return Partition.daily(table, date(int(suffix[1:5]), int(suffix[5:7]), int(suffix[7:9])))
    except ValueError:
        return None
    return None


def expired_partitions(
    existing: Iterable[tuple[str, str]], today: date, policy: RetentionPolicy
) -> list[tuple[str, str]]:
    """(table, partition) pairs whose whole range is older than the table's retention."""
    expired = []
    for table, name in existing:
        partition = parse_partition(table, name)
        if partition is not None and partition.upper <= today - timedelta(days=policy.retention_days(table)):
            expired.append((table, name))
    return expired


@dataclass
class MaintenanceReport:
    created: list[str] = field(default_factory=list)
    moved_rows: int = 0
    dropped: list[str] = field(default_factory=list)
    purged_rows: int = 0
    derived_sessions: int = 0  # sessions whose stored activity facts this run derived (see session_activity)
    sort_keys_derived: int = 0  # sessions whose stored sort keys this run derived (see session_sort_keys)
    sort_keys_repriced: int = 0  # sessions whose stored cost key this run re-priced after a rates change
    model_groups_derived: int = 0  # sessions whose stored model groups this run wrote (see session_sort_keys)
    rows_in_default: dict[str, int] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)  # steps, partitions or purges that raised; the rest ran
    defaults_ready: bool = False  # every table has its DEFAULT partition: ingest always has a target


_EXISTING_PARTITIONS_SQL = """
SELECT parent.relname AS parent, child.relname AS child
FROM pg_inherits i
JOIN pg_class child ON child.oid = i.inhrelid
JOIN pg_class parent ON parent.oid = i.inhparent
JOIN pg_namespace ns ON ns.oid = parent.relnamespace
WHERE ns.nspname = $1
"""


# The database's date, not the pod's: a pod clock running ahead would drop partitions early.
TODAY_UTC_SQL = "SELECT (now() AT TIME ZONE 'UTC')::date"
# Moving rows out of a DEFAULT partition and purging old rows can take far longer than a
# dashboard query; they get the rollup refresher's limit.
MAINTENANCE_STATEMENT_TIMEOUT_S = 300
_RAISED_STATEMENT_TIMEOUT = f"SET LOCAL statement_timeout = '{MAINTENANCE_STATEMENT_TIMEOUT_S}s'"
_RAISED_CLIENT_TIMEOUT_S = MAINTENANCE_STATEMENT_TIMEOUT_S + CLIENT_TIMEOUT_MARGIN_S
# What one step, partition or purge may fail with while the others still run: a database
# error, or the client giving up on a statement (asyncpg raises TimeoutError).
_ISOLATED_FAILURES = (asyncpg.PostgresError, TimeoutError)
# Deriving the activity facts of the sessions that predate them: one transaction per batch, one run's share of time.
_ACTIVITY_BATCH = 5_000
_ACTIVITY_BUDGET_S = 120.0
# While more than _ACTIVITY_CATCH_UP_ABOVE sessions still lack their facts (right after the deployment of the revision
# that adds them) a run may work longer, so a large database is derived in a few runs instead of many hourly ones; it
# returns to the steady budget as soon as the backlog is small. The count is capped: it never reads more rows than that.
_ACTIVITY_CATCH_UP_BUDGET_S = 600.0
_ACTIVITY_CATCH_UP_ABOVE = 50_000
# The stored sort keys: a batch is one transaction of this many sessions, a run works this long (split between the
# backfill and the re-price), and the batches are paced. Lists are exact meanwhile (sessions without keys are computed).
_SORT_KEYS_BATCH = 1_000
_SORT_KEYS_BUDGET_S = 120.0
_SORT_KEYS_PAUSE_S = 0.05
# The stored model groups: one batch of this many sessions per run (the hourly job spreads the rollout; a quiet-window
# rollout uses `sort_keys_cli backfill-groups`).
_MODEL_GROUPS_BATCH = 1000
_ACTIVITY_BACKLOG_SQL = (
    "SELECT count(*) FROM (SELECT 1 FROM session_dims WHERE has_activity IS NULL "
    f"LIMIT {_ACTIVITY_CATCH_UP_ABOVE + 1}) p"
)


class PartitionMaintainer:
    def __init__(
        self,
        engine: AnalyticsPgEngine,
        policy: RetentionPolicy,
        clock: Callable[[], date] | None = None,
        ddl_lock_timeout_ms: int = DDL_LOCK_TIMEOUT_MS,
    ) -> None:
        """`clock` is for tests; by default today is the database's UTC date."""
        self._engine = engine
        self._policy = policy
        self._clock = clock
        self._lock_timeout = f"SET LOCAL lock_timeout = {int(ddl_lock_timeout_ms)}"
        self._rates_version: Callable[[], str] | None = None
        self._rates: RatesFor | None = None

    def set_rates(self, current_version: Callable[[], str] | None, rates: RatesFor | None = None) -> None:
        """Keep the callables of the service layer's catalog: the current rates version and the `RatesFor` callable
        the sort-key backfill and re-price price with (the repository never imports the service)."""
        self._rates_version = current_version
        self._rates = rates

    async def run(self, conn: asyncpg.Connection | None = None) -> MaintenanceReport:
        """Runs on `conn` when given (the connection holding the job's advisory lock)."""
        if conn is None:
            async with self._engine.acquire() as acquired:
                return await self.run(acquired)
        report = MaintenanceReport()
        today = self._clock() if self._clock else await conn.fetchval(TODAY_UTC_SQL)
        steps = (
            ("_ensure_partitions", lambda: self._ensure_partitions(conn, today, report)),
            ("_drop_expired", lambda: self._drop_expired(conn, today, report)),
            ("_purge_expired_rows", lambda: self._purge_expired_rows(conn, today, report)),
            ("_derive_session_activity", lambda: self._derive_session_activity(conn, report)),
            ("_backfill_session_sort_keys", lambda: self._backfill_session_sort_keys(conn, report)),
            ("_backfill_model_groups", lambda: self._backfill_model_groups(conn, report)),
            ("_count_default_rows", lambda: self._count_default_rows(conn, report)),
        )
        for name, step in steps:
            try:
                await step()
            except _ISOLATED_FAILURES as exc:
                # A step that keeps failing (a statement timeout on a large DEFAULT partition,
                # say) must not stop the others: retention in particular has to keep running.
                report.failed.append(name)
                logger.exception(f"cli_analytics: maintenance step {name} failed: {exc!r}")
        if report.rows_in_default:
            logger.warning(
                "cli_analytics: rows outside every planned partition (DEFAULT partitions): %s", report.rows_in_default
            )
        return report

    async def _derive_session_activity(self, conn: asyncpg.Connection, report: MaintenanceReport) -> None:
        """Derive the stored activity facts of the sessions that existed before they were stored. A run works
        `_ACTIVITY_BUDGET_S`, or `_ACTIVITY_CATCH_UP_BUDGET_S` while a large backlog is left (the Sessions list is exact
        meanwhile, only slower for the sessions without facts)."""
        backlog = int(await conn.fetchval(_ACTIVITY_BACKLOG_SQL, timeout=_RAISED_CLIENT_TIMEOUT_S))
        budget = _ACTIVITY_CATCH_UP_BUDGET_S if backlog > _ACTIVITY_CATCH_UP_ABOVE else _ACTIVITY_BUDGET_S
        report.derived_sessions += await session_activity.backfill_session_activity(
            conn, _ACTIVITY_BATCH, budget, MAINTENANCE_STATEMENT_TIMEOUT_S
        )

    async def _backfill_session_sort_keys(self, conn: asyncpg.Connection, report: MaintenanceReport) -> None:
        """Derive the stored sort keys of sessions without them, then re-price those priced by another rate set, for at
        most `_SORT_KEYS_BUDGET_S` in all. The version stamped is the current catalog version, read once before any
        rates are resolved, the one the readers compare stamps with. With no rates or version injected, or an empty
        catalog (which would price everything at zero), nothing is done. Last, the readers are turned on once every
        session is stamped (never after an operator's `disable`)."""
        if self._rates is None or self._rates_version is None:
            return
        version = self._rates_version()
        if version == EMPTY_RATES_VERSION:
            return
        rates = self._rates
        started = time.monotonic()
        derived = await session_sort_keys.backfill_session_sort_keys(
            conn,
            rates,
            version,
            _SORT_KEYS_BATCH,
            budget_s=_SORT_KEYS_BUDGET_S / 2,
            pause_s=_SORT_KEYS_PAUSE_S,
            timeout=_RAISED_CLIENT_TIMEOUT_S,
        )
        report.sort_keys_derived += derived.derived
        repriced = await session_sort_keys.reprice_cost_keys(
            conn,
            rates,
            version,
            _SORT_KEYS_BATCH,
            budget_s=max(_SORT_KEYS_BUDGET_S - (time.monotonic() - started), 1.0),
            pause_s=_SORT_KEYS_PAUSE_S,
            timeout=_RAISED_CLIENT_TIMEOUT_S,
        )
        report.sort_keys_repriced += repriced.repriced
        await session_sort_keys.auto_enable_session_sort_keys(conn, version, _RAISED_CLIENT_TIMEOUT_S)
        if derived.derived or repriced.repriced:
            logger.info(
                f"cli_analytics: stored sort keys: {derived.derived} derived, {repriced.repriced} re-priced in "
                f"{derived.duration_s + repriced.duration_s:.1f}s, WAL {derived.wal_bytes}/{repriced.wal_bytes} bytes"
            )

    async def _backfill_model_groups(self, conn: asyncpg.Connection, report: MaintenanceReport) -> None:
        """Write the stored model groups of `_MODEL_GROUPS_BATCH` pending sessions (through the same derivation as the
        sort keys, which needs the rates), then turn the groups' readers on once none is pending (never after an
        operator's `disable-groups`). Separate from the Sessions switch. With no rates or version injected, or an empty
        catalog, nothing is done."""
        if self._rates is None or self._rates_version is None:
            return
        version = self._rates_version()
        if version == EMPTY_RATES_VERSION:
            return
        result = await session_sort_keys.backfill_model_groups(
            conn,
            self._rates,
            version,
            _MODEL_GROUPS_BATCH,
            pause_s=_SORT_KEYS_PAUSE_S,
            timeout=_RAISED_CLIENT_TIMEOUT_S,
            max_batches=1,
        )
        report.model_groups_derived += result.derived
        await session_sort_keys.auto_enable_model_groups(conn, _RAISED_CLIENT_TIMEOUT_S)
        if result.derived:
            logger.info(
                f"cli_analytics: stored model groups: {result.derived} sessions derived in {result.duration_s:.1f}s, "
                f"WAL {result.wal_bytes} bytes"
            )

    async def _existing(self, conn: asyncpg.Connection) -> set[tuple[str, str]]:
        rows = await conn.fetch(_EXISTING_PARTITIONS_SQL, self._engine.settings.schema)
        return {(r["parent"], r["child"]) for r in rows}

    async def _ensure_partitions(self, conn: asyncpg.Connection, today: date, report: MaintenanceReport) -> None:
        # Each partition on its own: one that keeps failing must not keep every later one
        # (future weeks, rollup months, ledger days) from being created.
        existing = await self._existing(conn)
        report.defaults_ready = True
        for table in PARTITIONED_TABLES:
            name = default_partition_name(table)
            if (table, name) in existing:
                continue
            try:
                await self._ddl(
                    conn, f"CREATE TABLE IF NOT EXISTS {quote_ident(name)} PARTITION OF {quote_ident(table)} DEFAULT"
                )
            except _ISOLATED_FAILURES as exc:
                report.defaults_ready = False
                report.failed.append(name)
                logger.error(f"cli_analytics: cannot create {name}, so ingest into {table} fails: {exc!r}")
        for partition in planned_partitions(today, self._policy):
            if (partition.table, partition.name) in existing:
                continue
            try:
                try:
                    await self._ddl(conn, partition.create_sql())
                except asyncpg.CheckViolationError:
                    # The DEFAULT partition already holds rows of this range.
                    report.moved_rows += await self._create_moving_default_rows(conn, partition)
            except asyncpg.LockNotAvailableError:
                # Attaching locks the DEFAULT partition, which a long query may hold; the
                # premake horizon leaves many later runs to try again.
                logger.info("cli_analytics: %s is busy, creating %s on the next run", partition.table, partition.name)
                continue
            except _ISOLATED_FAILURES as exc:
                report.failed.append(partition.name)
                logger.error(f"cli_analytics: cannot create partition {partition.name}: {exc!r}")
                continue
            report.created.append(partition.name)

    async def _create_moving_default_rows(self, conn: asyncpg.Connection, partition: Partition) -> int:
        name, table = quote_ident(partition.name), quote_ident(partition.table)
        default = quote_ident(default_partition_name(partition.table))
        timeout = _RAISED_CLIENT_TIMEOUT_S
        async with conn.transaction():
            await conn.execute(self._lock_timeout, timeout=timeout)
            await conn.execute(_RAISED_STATEMENT_TIMEOUT, timeout=timeout)
            # Held until the attach: a row inserted into DEFAULT in between would make ATTACH fail.
            await conn.execute(f"LOCK TABLE {default} IN SHARE ROW EXCLUSIVE MODE", timeout=timeout)
            await conn.execute(
                f"CREATE TABLE {name} (LIKE {table} INCLUDING DEFAULTS INCLUDING CONSTRAINTS){partition.storage_sql}",
                timeout=timeout,
            )
            moved = await conn.fetchval(
                f"WITH moved AS (DELETE FROM {default} WHERE {partition.in_range_sql()} RETURNING *), "
                f"ins AS (INSERT INTO {name} SELECT * FROM moved RETURNING 1) SELECT count(*) FROM ins",
                timeout=timeout,
            )
            await conn.execute(
                f"ALTER TABLE {table} ATTACH PARTITION {name} FOR VALUES {partition.bounds_sql}", timeout=timeout
            )
        logger.warning("cli_analytics: moved %s rows from %s into new partition %s", moved, default, name)
        return int(moved)

    async def _drop_expired(self, conn: asyncpg.Connection, today: date, report: MaintenanceReport) -> None:
        for _table, name in expired_partitions(sorted(await self._existing(conn)), today, self._policy):
            try:
                if _table in _SORT_KEY_SOURCES:
                    # The batches below are committed before the DROP is tried, so a DROP that then loses its lock
                    # wait would leave the partition in place with its sessions unstamped, on every run. The lock
                    # the DROP needs is taken and given back first: a busy parent skips the partition untouched.
                    await self._ddl(conn, f"LOCK TABLE {quote_ident(_table)} IN ACCESS EXCLUSIVE MODE")
                    # The bulk of the unstamping, in committed batches: one statement over a large partition can
                    # exceed the statement timeout and then stall the partition's retention on every run. The
                    # statement of the DROP's own transaction below only catches sessions restamped meanwhile.
                    await self._unstamp_sessions_of(conn, name)
                before = (
                    [_UNSTAMP_SESSIONS_OF.format(source=quote_ident(name), where="")]
                    if _table in _SORT_KEY_SOURCES
                    else []
                )
                await self._ddl(conn, f"DROP TABLE IF EXISTS {quote_ident(name)}", before)
                report.dropped.append(name)
            except asyncpg.LockNotAvailableError:
                logger.info("cli_analytics: partition %s is busy, dropping it on the next run", name)
            except _ISOLATED_FAILURES as exc:
                # The others still go: retention must not stop at one partition.
                report.failed.append(name)
                logger.error(f"cli_analytics: cannot drop expired partition {name}: {exc!r}")

    @staticmethod
    async def _unstamp_sessions_of(conn: asyncpg.Connection, source: str) -> None:
        """Drop the stamp of every session that has rows in `source`, `_UNSTAMP_BATCH` sessions to a transaction,
        walking the partition in `session_id` order (the partitions carry an index on it)."""
        last = ""
        while True:
            rows = await conn.fetch(
                _PARTITION_SESSIONS.format(source=quote_ident(source)),
                last,
                _UNSTAMP_BATCH,
                timeout=_RAISED_CLIENT_TIMEOUT_S,
            )
            if not rows:
                return
            ids = [r["session_id"] for r in rows]
            await session_sort_keys.unstamp_session_sort_keys(conn, ids, _RAISED_CLIENT_TIMEOUT_S)
            last = ids[-1]

    async def _purge_expired_rows(self, conn: asyncpg.Connection, today: date, report: MaintenanceReport) -> None:
        """Rows retention cannot drop with a partition: DEFAULT partitions and per-session tables."""
        utc_midnight = "($1::date::timestamp AT TIME ZONE 'UTC')"
        statements: list[tuple[str, list[str], date]] = []  # (what, statements of one transaction, cutoff)
        for table in PARTITIONED_TABLES:
            cutoff = today - timedelta(days=self._policy.retention_days(table))
            if table == LEDGER_TABLE:
                # Ledger rows land in DEFAULT when their record day is already past the window:
                # they are kept for the window from delivery, so re-sends are still recognised.
                condition = f"first_seen < {utc_midnight}"
            else:
                key = quote_ident(PARTITION_KEY[table])
                condition = f"{key} < {utc_midnight if table in _TIMESTAMP_KEYED else '$1::date'}"
            default = default_partition_name(table)
            sqls = [f"DELETE FROM {quote_ident(default)} WHERE {condition}"]
            if table in _SORT_KEY_SOURCES:
                sqls.insert(0, _UNSTAMP_SESSIONS_OF.format(source=quote_ident(default), where=f"WHERE {condition}"))
            statements.append((default, sqls, cutoff))
        rollup_cutoff = today - timedelta(days=self._policy.rollup_days)
        raw_cutoff = today - timedelta(days=self._policy.raw_days)
        statements += [
            (
                "session_skills",
                [
                    _UNSTAMP_SESSIONS_OF.format(source="session_skills", where=f"WHERE updated_at < {utc_midnight}"),
                    f"DELETE FROM session_skills WHERE updated_at < {utc_midnight}",
                ],
                rollup_cutoff,
            ),
            ("session_attributes", [f"DELETE FROM session_attributes WHERE updated_at < {utc_midnight}"], raw_cutoff),
            # Only raw rows reference a resource, and they are gone after the raw retention.
            ("otel_resources", [f"DELETE FROM otel_resources WHERE last_seen < {utc_midnight}"], raw_cutoff),
        ]
        for what, sqls, cutoff in statements:
            await self._purge_group(conn, what, sqls, cutoff, report)
        if self._policy.session_days:
            session_cutoff = today - timedelta(days=self._policy.session_days)
            # The first midnight UTC after today, from the cutoff the statements are given.
            tomorrow = f"(($1::date + {int(self._policy.session_days) + 1})::timestamp AT TIME ZONE 'UTC')"
            await session_purge.purge_idle_sessions(
                conn, utc_midnight, tomorrow, session_cutoff, self._policy.session_purge_batch_size, report
            )
            orphans = self._orphan_purge(utc_midnight, tomorrow)
            await self._purge_group(conn, "session orphans", orphans, session_cutoff, report)

    async def _purge_group(
        self, conn: asyncpg.Connection, what: str, sqls: list[str], cutoff: date, report: MaintenanceReport
    ) -> None:
        deleted = 0
        try:
            async with conn.transaction():
                await conn.execute(_RAISED_STATEMENT_TIMEOUT, timeout=_RAISED_CLIENT_TIMEOUT_S)
                for sql in sqls:
                    changed = await self._purge(conn, sql, cutoff)
                    if not sql.startswith("UPDATE"):  # the unstamping of the sessions is no purged row
                        deleted += changed
        except _ISOLATED_FAILURES as exc:
            report.failed.append(f"purge {what}")
            logger.error(f"cli_analytics: purging expired rows of {what} failed: {exc!r}")
            return
        report.purged_rows += deleted

    @staticmethod
    async def _purge(conn: asyncpg.Connection, sql: str, cutoff: date) -> int:
        """How many rows one purge statement deleted, from the status of a plain DELETE."""
        status = await conn.execute(sql, cutoff, timeout=_RAISED_CLIENT_TIMEOUT_S)
        return int(status.rsplit(" ", 1)[-1])

    @staticmethod
    def _past(column: str, tomorrow: str) -> str:
        """`column` unless it lies after today: the time comes from the client, and one in the future
        is a clock error, which must not keep a row past its retention."""
        return f"CASE WHEN {column} < {tomorrow} THEN {column} END"

    @classmethod
    def _orphan_purge(cls, utc_midnight: str, tomorrow: str) -> list[str]:
        """Rows of sessions with no session_dims row, by their own date. A subagent's date is its
        end, else its start, neither believed when it lies after today; a subagent row with no such
        date is kept."""
        subagent_date = f"coalesce({cls._past('ended_at', tomorrow)}, {cls._past('started_at', tomorrow)})"
        own_date = {
            "session_usage": "day < $1::date",
            "session_usage_hourly": f"hour < {utc_midnight}",
            "subagent_invocations": f"{subagent_date} < {utc_midnight}",
            "session_model_groups": "$1::date IS NOT NULL",  # no date of its own; every statement binds the cutoff
        }
        return [
            f"DELETE FROM {t} WHERE {own_date[t]} AND NOT EXISTS (SELECT 1 FROM session_dims d "
            f"WHERE d.session_id = {t}.session_id)"
            for t in _SESSION_CHILDREN
        ]

    async def _count_default_rows(self, conn: asyncpg.Connection, report: MaintenanceReport) -> None:
        for table in PARTITIONED_TABLES:
            count = await conn.fetchval(f"SELECT count(*) FROM {quote_ident(default_partition_name(table))}")
            if count:
                report.rows_in_default[table] = int(count)

    async def _ddl(self, conn: asyncpg.Connection, sql: str, before: Sequence[str] = ()) -> None:
        """`sql` under the DDL lock wait; `before` runs first, in the same transaction (the unstamping of the sessions a
        dropped partition held rows of: a lock timeout of the DROP then rolls it back too)."""
        async with conn.transaction():
            await conn.execute(self._lock_timeout)
            for statement in before:
                await conn.execute(statement, timeout=_RAISED_CLIENT_TIMEOUT_S)
            await conn.execute(sql)
