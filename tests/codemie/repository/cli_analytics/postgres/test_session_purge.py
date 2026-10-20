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


"""The session purge of the partition maintainer: batch and part statements, the batch loop, parts,
skipped-session logging and failures (no database)."""

from __future__ import annotations

import dataclasses
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import asyncpg
import pytest

from codemie.repository.cli_analytics.postgres import maintenance, session_purge
from codemie.repository.cli_analytics.postgres.maintenance import (
    PartitionMaintainer,
    RetentionPolicy,
    planned_partitions,
)
from tests.codemie.repository.cli_analytics.support.maintenance_connections import (
    COUNT,
    NO_CANDIDATE,
    NOT_TAKEN,
    POLICY,
    PURGE_STARTED,
    SERVER_TIMEOUT,
    TODAY,
    FailingConnection,
    PoolTimeoutConnection,
    RecordingConnection,
)


SESSION_TABLES = ("session_usage", "session_usage_hourly", "subagent_invocations", "session_dims")
SESSION_CHILDREN = ("session_usage", "session_usage_hourly", "subagent_invocations")


def _deletes_from(conn: RecordingConnection, table: str) -> list[str]:
    return [sql for sql in conn.statements if sql.startswith(f"DELETE FROM {table} ")]


@pytest.mark.asyncio
async def test_purge_plan_at_zero_leaves_session_tables() -> None:
    conn = RecordingConnection()

    await PartitionMaintainer(MagicMock(), POLICY, clock=lambda: TODAY)._purge_expired_rows(
        conn,
        TODAY,
        maintenance.MaintenanceReport(),  # type: ignore[arg-type]
    )

    for table in SESSION_TABLES:
        assert _deletes_from(conn, table) == []
    assert _whole_session_purges(conn) == []
    assert _part_transactions(conn) == []
    assert _clock_reads(conn) == []
    assert _count_transactions(conn) == []
    assert len(_deletes_from(conn, "session_skills")) == 1


SESSION_POLICY = RetentionPolicy(raw_days=90, rollup_days=365, dedup_days=14, premake_weeks=4, session_days=180)
# The first midnight UTC after today, from the cutoff ($1 = today - 180 days): 180 + 1 days later.
TOMORROW = "(($1::date + 181)::timestamp AT TIME ZONE 'UTC')"
CUTOFF = "($1::date::timestamp AT TIME ZONE 'UTC')"
RAISED_TIMEOUT = "SET LOCAL statement_timeout = '300s'"
PURGE_LOCK_TIMEOUT = "SET LOCAL lock_timeout = 2000"
SESSION_CUTOFF = date(2026, 3, 27)  # 180 days before 2026-09-23: 23 + 31 + 31 + 30 + 31 + 30 + 4 (Mar 27 to Mar 31)
SKIPPED = "cli_analytics: the session purge skipped "


def _whole_session_purges(conn: RecordingConnection) -> list[str]:
    return [sql for sql in conn.statements if sql.startswith("WITH batch AS MATERIALIZED (SELECT session_id ")]


def _batch_transactions(conn: RecordingConnection) -> list[list[str]]:
    return [tx for tx in conn.transactions if any(s.startswith("WITH batch AS") for s in tx)]


def _part_transactions(conn: RecordingConnection) -> list[list[str]]:
    return [tx for tx in conn.transactions if any(s.startswith("WITH dims AS") for s in tx)]


def _batch_args(conn: RecordingConnection) -> list[tuple[object, ...]]:
    return [args for sql, args in conn.args if sql.startswith("WITH batch AS")]


def _part_args(conn: RecordingConnection) -> list[tuple[object, ...]]:
    return [args for sql, args in conn.args if sql.startswith("WITH dims AS")]


def _count_transactions(conn: RecordingConnection) -> list[list[str]]:
    return [tx for tx in conn.transactions if any(s.startswith(COUNT) for s in tx)]


def _count_args(conn: RecordingConnection) -> list[tuple[object, ...]]:
    return [args for sql, args in conn.args if sql.startswith(COUNT)]


def _clock_reads(conn: RecordingConnection) -> list[int]:
    """The positions of the database time reads among the statements."""
    return [i for i, sql in enumerate(conn.statements) if sql == "SELECT clock_timestamp()"]


def _skipped_lines(logger: MagicMock) -> list[str]:
    return [c.args[0] for c in logger.error.call_args_list if c.args[0].startswith(SKIPPED)]


async def _purge_with_session_retention(
    conn: RecordingConnection, policy: RetentionPolicy = SESSION_POLICY
) -> maintenance.MaintenanceReport:
    report = maintenance.MaintenanceReport()
    await PartitionMaintainer(MagicMock(), policy, clock=lambda: TODAY)._purge_expired_rows(conn, TODAY, report)  # type: ignore[arg-type]
    return report


@pytest.mark.asyncio
async def test_purge_plan_above_zero_deletes_whole_sessions_then_orphans() -> None:
    conn = RecordingConnection()  # the first batch finds no idle session

    report = await _purge_with_session_retention(conn)

    (whole,) = _whole_session_purges(conn)
    orphans = [sql for sql in conn.statements if sql.startswith("DELETE FROM") and "NOT EXISTS" in sql]
    assert [sql.split()[2] for sql in orphans] == ["session_usage", "session_usage_hourly", "subagent_invocations"]
    assert all(conn.statements.index(o) > conn.statements.index(whole) for o in orphans)
    assert report.purged_rows == 0
    for table in SESSION_TABLES:  # no other statement deletes by session age, none by rollup retention
        assert [sql for sql in _deletes_from(conn, table) if "NOT EXISTS" not in sql] == []


@pytest.mark.asyncio
async def test_a_whole_session_purge_skips_held_rows_and_reaches_children_through_the_primary_key() -> None:
    # One statement per batch: its batch CTE decides once which sessions are idle. Child rows are
    # counted and locked per session (an index seek on the primary key, not a scan of the child table),
    # a session with a held child row is skipped instead of waited for, and the deletes take exactly
    # the rows locked for the deleted sessions.
    conn = RecordingConnection()

    await _purge_with_session_retention(conn)

    (whole,) = _whole_session_purges(conn)
    # a candidate over the row cap is heavy; the chosen ones add up to at most the cap
    assert "heavy AS (SELECT session_id FROM visible WHERE n > $3)" in whole
    assert "WHERE w.running <= $3)" in whole
    assert (
        "session_usage_visible.n + session_usage_hourly_visible.n + subagent_invocations_visible.n AS n FROM batch s"
    ) in whole
    assert (
        "chosen AS (SELECT * FROM (SELECT v.*, sum(v.n) OVER (ORDER BY v.session_id) AS running "
        "FROM visible v WHERE v.n <= $3) w WHERE w.running <= $3)"
    ) in whole
    # a held or torn session keeps its session_dims row
    assert (
        "idle AS (DELETE FROM session_dims d USING chosen c WHERE d.session_id = c.session_id "
        "AND NOT EXISTS (SELECT 1 FROM held h WHERE h.session_id = c.session_id) "
        "AND NOT EXISTS (SELECT 1 FROM torn t WHERE t.session_id = c.session_id) RETURNING d.session_id)"
    ) in whole
    assert whole.count(f"updated_at) < {CUTOFF}") == 1  # the idle test is written once, in the batch CTE only
    assert whole.startswith("WITH batch AS MATERIALIZED (SELECT session_id FROM session_dims WHERE coalesce(greatest(")
    batch = whole[: whole.index("LIMIT $2 FOR UPDATE SKIP LOCKED)")]
    assert "ORDER BY" not in batch  # the candidate scan takes sessions in no order
    assert "NOT EXISTS (SELECT 1 FROM unnest($4::text[])" in batch
    for table in SESSION_CHILDREN:
        # counted only up to one past the cap: a heavy session's rows are never all counted
        assert (
            f"CROSS JOIN LATERAL (SELECT count(*) AS n FROM (SELECT 1 FROM {table} "
            f"WHERE {table}.session_id = s.session_id LIMIT $3 + 1) x) {table}_visible"
        ) in whole, table
        assert (
            f"{table}_held AS MATERIALIZED (SELECT l.tid, l.session_id FROM chosen c CROSS JOIN LATERAL "
            f"(SELECT ctid AS tid, session_id FROM {table} WHERE {table}.session_id = c.session_id "
            "FOR UPDATE SKIP LOCKED) l)"
        ) in whole, table
        # the child rows locked for a session that is not held go, whatever the session_dims delete does
        assert (
            f"{table}_purged AS (DELETE FROM {table} WHERE ctid = ANY(ARRAY(SELECT h.tid FROM {table}_held h "
            "WHERE NOT EXISTS (SELECT 1 FROM held x WHERE x.session_id = h.session_id))) RETURNING session_id)"
        ) in whole, table
    assert " NOT IN " not in whole
    assert "session_id IN (SELECT session_id FROM idle)" not in whole
    assert "now()" not in whole
    normalised = " ".join(whole.split())
    # a chosen session is held when any child table locked fewer of its rows than were counted
    assert (
        "held AS (SELECT c.session_id FROM chosen c "
        "LEFT JOIN (SELECT session_id, count(*) AS n FROM session_usage_held GROUP BY session_id) session_usage_locked "
        "ON session_usage_locked.session_id = c.session_id "
        "LEFT JOIN (SELECT session_id, count(*) AS n FROM session_usage_hourly_held GROUP BY session_id) "
        "session_usage_hourly_locked ON session_usage_hourly_locked.session_id = c.session_id "
        "LEFT JOIN (SELECT session_id, count(*) AS n FROM subagent_invocations_held GROUP BY session_id) "
        "subagent_invocations_locked ON subagent_invocations_locked.session_id = c.session_id "
        "WHERE c.session_usage_rows <> coalesce(session_usage_locked.n, 0) "
        "OR c.session_usage_hourly_rows <> coalesce(session_usage_hourly_locked.n, 0) "
        "OR c.subagent_invocations_rows <> coalesce(subagent_invocations_locked.n, 0))"
    ) in normalised
    # a chosen session not held is torn when any child table deleted fewer of its rows than were counted
    assert (
        "torn AS (SELECT c.session_id FROM chosen c "
        "LEFT JOIN (SELECT session_id, count(*) AS n FROM session_usage_purged GROUP BY session_id) "
        "session_usage_deleted ON session_usage_deleted.session_id = c.session_id "
        "LEFT JOIN (SELECT session_id, count(*) AS n FROM session_usage_hourly_purged GROUP BY session_id) "
        "session_usage_hourly_deleted ON session_usage_hourly_deleted.session_id = c.session_id "
        "LEFT JOIN (SELECT session_id, count(*) AS n FROM subagent_invocations_purged GROUP BY session_id) "
        "subagent_invocations_deleted ON subagent_invocations_deleted.session_id = c.session_id "
        "WHERE NOT EXISTS (SELECT 1 FROM held h WHERE h.session_id = c.session_id) "
        "AND (c.session_usage_rows <> coalesce(session_usage_deleted.n, 0) "
        "OR c.session_usage_hourly_rows <> coalesce(session_usage_hourly_deleted.n, 0) "
        "OR c.subagent_invocations_rows <> coalesce(subagent_invocations_deleted.n, 0)))"
    ) in normalised
    # the child deletes come first, torn reads what they deleted, idle reads torn
    assert normalised.index("session_usage_purged AS") < normalised.index("torn AS") < normalised.index("idle AS")
    # the loop unpacks the answer by position: candidates, sessions, rows, held and torn ids, heavy ids
    assert normalised.endswith(
        "RETURNING d.session_id) SELECT (SELECT count(*) FROM batch), (SELECT count(*) FROM idle), "
        "(SELECT count(*) FROM idle) + (SELECT count(*) FROM session_usage_purged) "
        "+ (SELECT count(*) FROM session_usage_hourly_purged) + (SELECT count(*) FROM subagent_invocations_purged), "
        "ARRAY(SELECT session_id FROM held UNION ALL SELECT session_id FROM torn), ARRAY(SELECT session_id FROM heavy)"
    )


@pytest.mark.asyncio
async def test_sessions_are_purged_in_batches_until_one_finds_no_candidate() -> None:
    conn = RecordingConnection(
        batches=(
            (5000, 5000, 75000, [], []),
            (5000, 4999, 74985, ["h1"], []),
            (3, 0, 0, [], ["big1"]),
            NO_CANDIDATE,
        )
    )

    with patch.object(session_purge, "logger"):
        report = await _purge_with_session_retention(conn)

    batches = _batch_transactions(conn)
    assert len(batches) == 4
    # each its own transaction, its own statement timeout and its own short lock timeout
    assert all(tx[:2] == [RAISED_TIMEOUT, PURGE_LOCK_TIMEOUT] and len(tx) == 3 for tx in batches)
    assert all(tx[2].startswith("WITH batch AS MATERIALIZED (SELECT session_id FROM session_dims") for tx in batches)
    skip_lists = [args[3] for args in _batch_args(conn)]
    assert skip_lists == [[], [], ["h1"], ["h1", "big1"]]  # held and heavy sessions leave the later batches
    assert all(args[2] == 100000 for args in _batch_args(conn))
    assert [args[1] for args in _part_args(conn)] == ["big1"]  # then the heavy session, in parts
    assert report.purged_rows == 149985  # every plain DELETE of the fake answers "DELETE 0"
    assert report.failed == []


@pytest.mark.asyncio
async def test_a_batch_that_leaves_candidates_unchosen_shrinks_the_next_one() -> None:
    # Every candidate's child rows are counted; candidates the row cap leaves out are counted again by
    # the next batch, so the next batch takes about what the last one could choose, then grows back.
    conn = RecordingConnection(
        batches=(
            (300, 50, 100050, [], []),  # 50 sessions of 2000 rows fill the row cap
            (100, 48, 96048, ["h1"], []),  # 49 chosen of 100
            (98, 98, 1470, [], []),  # every candidate decided
            (196, 196, 2940, [], []),
            NO_CANDIDATE,
        )
    )

    with patch.object(session_purge, "logger"):
        report = await _purge_with_session_retention(
            conn, dataclasses.replace(SESSION_POLICY, session_purge_batch_size=300)
        )

    assert [args[1] for args in _batch_args(conn)] == [300, 100, 98, 196, 300]
    assert report.failed == []


@pytest.mark.asyncio
async def test_a_batch_that_neither_deletes_nor_skips_a_session_ends_the_purge() -> None:
    # Repeating such a batch would spin for ever under the maintenance advisory lock.
    conn = RecordingConnection(batches=((5, 0, 0, [], []), (5, 0, 0, [], [])))

    with patch.object(session_purge, "logger") as logger:
        report = await _purge_with_session_retention(conn)

    assert len(_batch_transactions(conn)) == 1
    (line,) = [c.args[0] for c in logger.error.call_args_list]
    assert "session purge" in line
    assert "a batch of 5 idle sessions" in line
    assert report.failed == []
    assert _count_args(conn) == []  # the sessions left were never tried, so none is reported as held


@pytest.mark.asyncio
async def test_a_batch_that_only_skips_sessions_already_skipped_ends_the_purge() -> None:
    # A batch returning an id it was told to leave out changes nothing and would repeat for ever.
    conn = RecordingConnection(batches=((5, 0, 0, ["h1"], []), (5, 0, 0, ["h1"], []), (5, 1, 1, [], [])))

    with patch.object(session_purge, "logger") as logger:
        await _purge_with_session_retention(conn)

    assert [args[3] for args in _batch_args(conn)] == [[], ["h1"]]  # the third batch is never sent
    errors = [c.args[0] for c in logger.error.call_args_list]
    assert any("the session purge stopped: a batch of 5 idle sessions" in line for line in errors)


def test_a_session_part_skips_held_rows_and_deletes_the_session_only_when_none_is_held() -> None:
    part = " ".join(session_purge.part_statement(CUTOFF, TOMORROW).split())

    assert part.startswith("WITH dims AS MATERIALIZED (SELECT session_id FROM session_dims WHERE session_id = $2 AND ")
    dims = part[: part.index("visible AS")]
    assert dims.endswith("FOR UPDATE SKIP LOCKED), ")
    for table in ("session_usage", "session_usage_hourly", "subagent_invocations"):
        assert (
            f"CROSS JOIN LATERAL (SELECT count(*) AS n FROM (SELECT 1 FROM {table} "
            f"WHERE {table}.session_id = s.session_id LIMIT $3 + 1) x) {table}_visible"
        ) in part, table
        held = part[part.index(f"{table}_held AS MATERIALIZED (") :]
        assert held[: held.index(") l)")].endswith(f"LIMIT b.{table}_take FOR UPDATE SKIP LOCKED"), table
        assert f"DELETE FROM {table} WHERE ctid = ANY(ARRAY(SELECT tid FROM {table}_held))" in part, table
    done = part[part.index("done AS (DELETE FROM session_dims") :]
    assert (
        "AND b.session_usage_take = b.session_usage_rows AND b.session_usage_hourly_take = b.session_usage_hourly_rows "
        "AND b.subagent_invocations_take = b.subagent_invocations_rows AND NOT EXISTS (SELECT 1 FROM held) RETURNING 1)"
    ) in done  # session_dims goes only with the last of every child table's rows
    # the tables share one budget of $3 rows in turn
    assert (
        "budget AS (SELECT *, least(session_usage_rows, $3) AS session_usage_take, "
        "least(session_usage_hourly_rows, greatest($3 - session_usage_rows, 0)) AS session_usage_hourly_take, "
        "least(subagent_invocations_rows, greatest($3 - session_usage_rows - session_usage_hourly_rows, 0)) "
        "AS subagent_invocations_take FROM visible)"
    ) in part
    assert part.count(f"updated_at) < {CUTOFF}") == 1
    assert " NOT IN " not in part
    assert "now()" not in part
    # held: some child table locked fewer rows than its share of the budget, or deleted fewer than it locked
    assert (
        "held AS (SELECT b.session_id FROM budget b "
        "WHERE b.session_usage_take <> (SELECT count(*) FROM session_usage_held) "
        "OR b.session_usage_hourly_take <> (SELECT count(*) FROM session_usage_hourly_held) "
        "OR b.subagent_invocations_take <> (SELECT count(*) FROM subagent_invocations_held) "
        "OR (SELECT count(*) FROM session_usage_purged) <> (SELECT count(*) FROM session_usage_held) "
        "OR (SELECT count(*) FROM session_usage_hourly_purged) <> (SELECT count(*) FROM session_usage_hourly_held) "
        "OR (SELECT count(*) FROM subagent_invocations_purged) <> (SELECT count(*) FROM subagent_invocations_held))"
    ) in part
    # the loop unpacks the answer by position: taken, rows, held, done
    assert part.endswith(
        "RETURNING 1) SELECT (SELECT count(*) FROM dims), "
        "(SELECT count(*) FROM done) + (SELECT count(*) FROM session_usage_purged) "
        "+ (SELECT count(*) FROM session_usage_hourly_purged) + (SELECT count(*) FROM subagent_invocations_purged), "
        "EXISTS (SELECT 1 FROM held), EXISTS (SELECT 1 FROM done)"
    )


@pytest.mark.asyncio
async def test_a_batch_whose_candidates_are_all_heavy_does_not_end_the_purge() -> None:
    conn = RecordingConnection(batches=((2, 0, 0, [], ["big1", "big2"]), (1, 1, 15, [], []), NO_CANDIDATE))

    with patch.object(session_purge, "logger") as logger:
        report = await _purge_with_session_retention(conn)

    assert len(_batch_transactions(conn)) == 3
    assert [args[3] for args in _batch_args(conn)] == [[], ["big1", "big2"], ["big1", "big2"]]
    assert [args[1] for args in _part_args(conn)] == ["big1", "big2"]
    logger.error.assert_not_called()
    assert report.purged_rows == 15


@pytest.mark.asyncio
async def test_a_heavy_session_is_deleted_in_parts_until_no_child_row_is_left() -> None:
    conn = RecordingConnection(
        batches=((1, 0, 0, [], ["big1"]), NO_CANDIDATE),
        parts=((1, 100000, False, False), (1, 1501, False, True)),  # the last part takes session_dims too
    )

    with patch.object(session_purge, "logger") as logger:
        report = await _purge_with_session_retention(conn)

    parts = _part_transactions(conn)
    assert len(parts) == 2
    assert all(tx[:2] == [RAISED_TIMEOUT, PURGE_LOCK_TIMEOUT] for tx in parts)
    assert _part_args(conn) == [(SESSION_CUTOFF, "big1", 100000)] * 2
    assert report.purged_rows == 101501
    assert _skipped_lines(logger) == []
    assert report.failed == []


@pytest.mark.asyncio
async def test_a_heavy_session_whose_session_dims_row_is_not_taken_is_left_for_a_later_run() -> None:
    conn = RecordingConnection(
        batches=((1, 0, 0, [], ["big1"]), NO_CANDIDATE), parts=(NOT_TAKEN,), counts=((1, ["big1"]),)
    )

    with patch.object(session_purge, "logger") as logger:
        report = await _purge_with_session_retention(conn)

    assert len(_part_transactions(conn)) == 1
    assert report.purged_rows == 0
    assert _count_args(conn) == [(SESSION_CUTOFF, PURGE_STARTED, [])]  # big1 is not held yet: the count finds it
    (line,) = [c.args[0] for c in logger.error.call_args_list]
    assert line.startswith(f"{SKIPPED}1 idle sessions")
    assert line.endswith(": big1")


@pytest.mark.parametrize(
    "answer", [(1, 37, True, False), (1, 0, False, False)], ids=["fewer-locked-than-visible", "nothing-deleted"]
)
@pytest.mark.asyncio
async def test_a_heavy_session_with_held_rows_stops_its_parts_and_is_logged_as_skipped(
    answer: tuple[int, int, bool, bool],
) -> None:
    conn = RecordingConnection(
        batches=((1, 0, 0, [], ["big1"]), NO_CANDIDATE), parts=(answer, (1, 100000, False, False))
    )

    with patch.object(session_purge, "logger") as logger:
        report = await _purge_with_session_retention(conn)

    assert len(_part_transactions(conn)) == 1
    assert report.purged_rows == answer[1]  # what the part deleted stays deleted
    (line,) = _skipped_lines(logger)
    assert line.startswith(f"{SKIPPED}1 idle sessions")
    assert "big1" in line
    assert report.failed == []


@pytest.mark.asyncio
async def test_sessions_held_by_batches_and_parts_are_logged_once() -> None:
    conn = RecordingConnection(batches=((2, 0, 0, ["h1"], ["big1"]), NO_CANDIDATE), parts=((1, 37, True, False),))

    with patch.object(session_purge, "logger") as logger:
        report = await _purge_with_session_retention(conn)

    (line,) = [c.args[0] for c in logger.error.call_args_list]
    assert line.startswith("cli_analytics: the session purge skipped 2 idle sessions")
    assert "h1" in line
    assert "big1" in line
    assert report.failed == []
    assert _count_args(conn) == [(SESSION_CUTOFF, PURGE_STARTED, ["h1", "big1"])]  # the count leaves them out


@pytest.mark.asyncio
async def test_the_skipped_sessions_line_names_at_most_ten() -> None:
    held = [f"held{i:02d}" for i in range(12)]
    conn = RecordingConnection(batches=((12, 0, 0, held, []), NO_CANDIDATE))

    with patch.object(session_purge, "logger") as logger:
        await _purge_with_session_retention(conn)

    (line,) = _skipped_lines(logger)
    assert line.startswith(f"{SKIPPED}12 idle sessions")
    assert [h for h in held if h in line] == held[:10]


@pytest.mark.asyncio
async def test_a_purge_with_no_held_session_logs_no_skipped_sessions() -> None:
    conn = RecordingConnection(batches=((5000, 5000, 75000, [], []), NO_CANDIDATE))

    with patch.object(session_purge, "logger") as logger:
        await _purge_with_session_retention(conn)

    logger.error.assert_not_called()
    assert _count_args(conn) == [(SESSION_CUTOFF, PURGE_STARTED, [])]  # an empty array, not NULL


@pytest.mark.asyncio
async def test_a_client_time_after_today_does_not_keep_a_session_or_an_orphan() -> None:
    # last_event_at, ended_at and a subagent's dates come from the client. A clock error (or intent)
    # that dates one in the future would keep the rows past their retention for ever, so such a time is
    # ignored: the other time decides, and for a session with neither, updated_at.
    conn = RecordingConnection(batches=((1, 0, 0, [], ["big1"]), NO_CANDIDATE))

    with patch.object(session_purge, "logger"):
        await _purge_with_session_retention(conn)

    def past(column: str) -> str:
        return f"CASE WHEN {column} < {TOMORROW} THEN {column} END"

    idle = f"coalesce(greatest({past('last_event_at')}, {past('ended_at')}), updated_at) < {CUTOFF}"
    whole = _whole_session_purges(conn)[0]
    assert idle in whole
    (part,) = [sql for sql in conn.statements if sql.startswith("WITH dims AS")]
    assert idle in part  # a part tests the session again
    subagents = next(sql for sql in conn.statements if sql.startswith("DELETE FROM subagent_invocations "))
    # Own date of a subagent row: its end, else its start; neither (or both in the future): NULL, kept.
    assert f"coalesce({past('ended_at')}, {past('started_at')}) < {CUTOFF}" in subagents
    # The maintainer's clock decides every cutoff; the database clock is read once, only to bound the count of
    # the idle sessions left, never inside a purge statement.
    clocks = [
        sql for sql in conn.statements if any(c in sql for c in ("now()", "clock_timestamp", "current_timestamp"))
    ]
    assert clocks == ["SELECT clock_timestamp()"]


@pytest.mark.asyncio
async def test_session_purges_are_bound_to_the_session_cutoff_and_each_batch_has_its_own_transaction() -> None:
    conn = RecordingConnection(batches=((5000, 5000, 75000, [], []), NO_CANDIDATE))

    await _purge_with_session_retention(conn)

    assert TODAY - timedelta(days=180) == SESSION_CUTOFF
    orphans = [
        (sql, args) for sql, args in conn.args if sql.startswith("DELETE FROM") and sql.split()[2] in SESSION_TABLES
    ]
    # neither the raw nor the rollup cutoff; the batch size of the policy, the row cap, an empty skip list
    assert _batch_args(conn) == [(SESSION_CUTOFF, 5000, 100000, [])] * 2
    assert [args for _, args in orphans] == [(SESSION_CUTOFF,)] * 3
    batches = [sql for sql, _ in conn.args if sql.startswith("WITH batch AS")]
    assert all(CUTOFF in sql or "day < $1::date" in sql for sql in batches + [sql for sql, _ in orphans])
    # each batch: the raised statement timeout, the short lock timeout, then the one statement
    assert [len(tx) for tx in _batch_transactions(conn)] == [3, 3]
    # then the count of idle sessions left, in a transaction of its own, bound to the time read once before
    # the first batch
    (count,) = _count_transactions(conn)
    assert count[:2] == [RAISED_TIMEOUT, PURGE_LOCK_TIMEOUT]
    assert len(count) == 3
    assert count[2].startswith(COUNT)
    assert _count_args(conn) == [(SESSION_CUTOFF, PURGE_STARTED, [])]
    (read,) = _clock_reads(conn)
    assert read < conn.statements.index(_whole_session_purges(conn)[0])

    small = RecordingConnection(batches=((7, 7, 105, [], []), NO_CANDIDATE))
    await _purge_with_session_retention(small, dataclasses.replace(SESSION_POLICY, session_purge_batch_size=7))
    assert _batch_args(small) == [(SESSION_CUTOFF, 7, 100000, [])] * 2


@pytest.mark.parametrize(
    "error",
    [SERVER_TIMEOUT, asyncpg.exceptions.LockNotAvailableError("canceling statement due to lock timeout")],
    ids=["statement-timeout", "lock-timeout"],
)
@pytest.mark.asyncio
async def test_a_failing_session_purge_is_reported_and_does_not_stop_the_others(error: BaseException) -> None:
    # The second batch fails: the first stays deleted and counted, no third batch is tried.
    conn = FailingConnection(
        "WITH batch AS MATERIALIZED (SELECT session_id FROM session_dims",
        error,
        nth=2,
        batches=((5000, 5000, 75000, [], ["big1"]), (5000, 5000, 75000, [], []), (5000, 5000, 75000, [], [])),
    )

    with patch.object(session_purge, "logger") as logger:
        report = await _purge_with_session_retention(conn)

    assert report.failed == ["purge sessions"]
    assert report.purged_rows == 75000
    assert len(_whole_session_purges(conn)) == 1
    assert _part_transactions(conn) == []  # nor the parts of a heavy session
    assert _count_args(conn) == []  # nor the count: the sessions left were never tried
    assert "purging expired rows of sessions failed" in logger.error.call_args.args[0]
    assert any(sql.startswith("DELETE FROM session_attributes") for sql in conn.statements)
    assert len([sql for sql in conn.statements if sql.startswith("DELETE FROM") and "NOT EXISTS" in sql]) == 3


@pytest.mark.asyncio
async def test_a_failing_part_is_reported_and_ends_the_session_purge() -> None:
    conn = FailingConnection(
        "WITH dims AS",
        asyncpg.exceptions.LockNotAvailableError("canceling statement due to lock timeout"),
        nth=2,
        batches=((2, 0, 0, [], ["big1", "big2"]), NO_CANDIDATE),
        parts=((1, 100000, False, False), (1, 100000, False, False)),
    )

    with patch.object(session_purge, "logger") as logger:
        report = await _purge_with_session_retention(conn)

    assert report.failed == ["purge sessions"]
    assert report.purged_rows == 100000
    assert [args[1] for args in _part_args(conn)] == ["big1"]  # no part of big2 is tried
    assert _count_args(conn) == []
    assert "purging expired rows of sessions failed" in logger.error.call_args.args[0]
    assert len([sql for sql in conn.statements if sql.startswith("DELETE FROM") and "NOT EXISTS" in sql]) == 3


@pytest.mark.asyncio
async def test_a_session_purge_failing_after_a_batch_with_held_sessions_still_logs_them() -> None:
    conn = FailingConnection(
        "WITH batch AS MATERIALIZED", SERVER_TIMEOUT, nth=2, batches=((5, 4, 60, ["h1"], []), (5, 5, 75, [], []))
    )

    with patch.object(session_purge, "logger") as logger:
        report = await _purge_with_session_retention(conn)

    assert report.failed == ["purge sessions"]
    (line,) = _skipped_lines(logger)
    assert line.startswith(f"{SKIPPED}1 idle sessions")
    assert "h1" in line
    errors = [c.args[0] for c in logger.error.call_args_list]
    assert len(errors) == 2
    assert any("purging expired rows of sessions failed" in e for e in errors)
    assert _count_args(conn) == []


@pytest.mark.asyncio
async def test_a_session_purge_failing_in_its_first_batch_logs_only_the_failure() -> None:
    conn = FailingConnection("WITH batch AS MATERIALIZED", SERVER_TIMEOUT, nth=1)

    with patch.object(session_purge, "logger") as logger:
        report = await _purge_with_session_retention(conn)

    (line,) = [c.args[0] for c in logger.error.call_args_list]
    assert "purging expired rows of sessions failed" in line
    assert report.failed == ["purge sessions"]
    assert _count_args(conn) == []


@pytest.mark.asyncio
async def test_session_purge_batches_are_not_cut_short_by_the_pools_client_timeout() -> None:
    conn = PoolTimeoutConnection(
        batches=((5000, 5000, 75000, [], ["big1"]), (5000, 5000, 75000, [], []), NO_CANDIDATE),
        parts=((1, 100001, False, True),),
    )
    partition = next(p for p in planned_partitions(TODAY, SESSION_POLICY) if p.table == "log_events")
    maintainer = PartitionMaintainer(MagicMock(), SESSION_POLICY, clock=lambda: TODAY)
    report = maintenance.MaintenanceReport()

    await maintainer._create_moving_default_rows(conn, partition)  # type: ignore[arg-type]
    await maintainer._purge_expired_rows(conn, TODAY, report)  # type: ignore[arg-type]

    assert report.failed == []
    assert report.purged_rows == 250001
    # one per transaction: the move, 13 DEFAULT partitions, 3 other tables, 3 session batches, 1 part, the count,
    # the orphans
    assert conn.statements.count(RAISED_TIMEOUT) == 23
    purge = _batch_transactions(conn) + _part_transactions(conn) + _count_transactions(conn)
    assert [tx.count(PURGE_LOCK_TIMEOUT) for tx in purge] == [1] * 5
    assert len(_count_args(conn)) == 1  # the count was answered, not cut short


def test_the_count_of_idle_sessions_left_takes_no_lock_and_leaves_out_the_held_and_the_rows_touched_meanwhile() -> None:
    count = " ".join(session_purge.idle_left_statement(CUTOFF, TOMORROW).split())

    assert count.startswith("SELECT count(*), (array_agg(session_id))[1:10] FROM session_dims WHERE coalesce(")
    assert count.count(f"updated_at) < {CUTOFF}") == 1  # the idle test of the batches
    assert "AND updated_at < $2" in count
    assert "NOT EXISTS (SELECT 1 FROM unnest($3::text[]) AS s(id) WHERE s.id = session_dims.session_id)" in count
    for absent in ("FOR UPDATE", "SKIP LOCKED", "LIMIT", "now()", "= ANY", "<> ALL"):
        assert absent not in count, absent


@pytest.mark.asyncio
async def test_an_idle_session_the_count_finds_is_logged_as_skipped() -> None:
    conn = RecordingConnection(batches=((5, 5, 75, [], []), NO_CANDIDATE), counts=((1, ["dims1"]),))

    with patch.object(session_purge, "logger") as logger:
        report = await _purge_with_session_retention(conn)

    assert [c.args[0] for c in logger.error.call_args_list] == [
        "cli_analytics: the session purge skipped 1 idle sessions whose rows another transaction holds; "
        "the next run retries them: dims1"
    ]
    assert report.failed == []


@pytest.mark.asyncio
async def test_sessions_the_count_finds_join_the_held_ones_in_one_line() -> None:
    conn = RecordingConnection(batches=((5, 3, 45, ["h1", "h2"], []), NO_CANDIDATE), counts=((1, ["dims1"]),))

    with patch.object(session_purge, "logger") as logger:
        await _purge_with_session_retention(conn)

    (line,) = [c.args[0] for c in logger.error.call_args_list]
    assert line.startswith(f"{SKIPPED}3 idle sessions")
    assert line.endswith(": h1, h2, dims1")
    assert [args[2] for args in _count_args(conn)] == [["h1", "h2"]]


@pytest.mark.asyncio
async def test_the_skipped_line_counts_every_session_the_count_finds_and_names_ten() -> None:
    left = [f"dims{i}" for i in range(10)]
    conn = RecordingConnection(batches=((5, 4, 60, ["h1"], []), NO_CANDIDATE), counts=((25, left),))

    with patch.object(session_purge, "logger") as logger:
        await _purge_with_session_retention(conn)

    (line,) = _skipped_lines(logger)
    assert line.startswith(f"{SKIPPED}26 idle sessions")
    assert line.endswith(": h1, dims0, dims1, dims2, dims3, dims4, dims5, dims6, dims7, dims8")


@pytest.mark.parametrize(
    "error",
    [asyncpg.exceptions.LockNotAvailableError("canceling statement due to lock timeout"), TimeoutError()],
    ids=["lock-timeout", "client-timeout"],
)
@pytest.mark.asyncio
async def test_a_failing_count_is_logged_but_is_not_a_purge_failure(error: BaseException) -> None:
    conn = FailingConnection(COUNT, error, batches=((5, 4, 60, ["h1"], []), NO_CANDIDATE))

    with patch.object(session_purge, "logger") as logger:
        report = await _purge_with_session_retention(conn)

    assert report.failed == []
    assert report.purged_rows == 60
    errors = [c.args[0] for c in logger.error.call_args_list]
    assert errors == [
        f"cli_analytics: could not count the idle sessions the session purge left: {error!r}",
        "cli_analytics: the session purge skipped 1 idle sessions whose rows another transaction holds; "
        "the next run retries them: h1",
    ]


@pytest.mark.asyncio
async def test_each_session_purge_reads_the_database_time_once_before_its_first_batch() -> None:
    conn = RecordingConnection()

    await _purge_with_session_retention(conn)
    await _purge_with_session_retention(conn)

    reads = _clock_reads(conn)
    batches = [i for i, sql in enumerate(conn.statements) if sql.startswith("WITH batch AS")]
    assert len(reads) == 2
    assert reads[0] < batches[0] < reads[1] < batches[1]
    assert _count_args(conn) == [(SESSION_CUTOFF, PURGE_STARTED, [])] * 2
