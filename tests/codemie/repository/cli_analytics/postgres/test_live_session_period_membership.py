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

"""End-to-end period-membership tightening (EPMCDME-16496 AC4) on a real PostgreSQL: a session whose only
signals are housekeeping lifecycle hooks, a slash-command exit with no real prompt/API/tool row, or a
whole-day zero-value lines-of-code point must not be admitted by any of the six session-listing endpoints,
while a genuinely active session in the same window is admitted by every one of them.

Needs `TEST_LOCAL_PG_URL` (a disposable PostgreSQL; the tests migrate it): without it every test is skipped,
and with it a test is skipped (not failed) when that database is unreachable. Each test deletes only its own
sessions. Run in one process, as the tests refresh the same rollup days:

    TEST_LOCAL_PG_URL=postgresql://<user>:<password>@<host>:<port>/<db> poetry run pytest -n0 <this file>
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import date, datetime, timedelta, timezone

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.rollups import RollupRefresher
from codemie.repository.cli_analytics.postgres.runtime import PostgresAnalyticsRuntime
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from tests.codemie.repository.cli_analytics.support.live_rollup_lock import live_rollup_lock
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")

# This file's own advisory-lock constant for the start()-on-an-empty-schema race: distinct from
# test_live_route_pricing.py's 0x6D6F6E6579 and live_rollup_lock's 0x726F6C6C7570. Spells "period" in ASCII
# hex bytes (p=0x70 e=0x65 r=0x72 i=0x69 o=0x6f d=0x64); Task 7 (which extends this file) must keep reusing
# this same constant, not invent a second one.
_SCHEMA_LOCK = 0x706572696F64

# Two days back: the next UTC day's requests are already in the past, and the day never leaves a retention window.
_DAY = datetime.now(timezone.utc).date() - timedelta(days=2)

# A nonempty `cwd` every seeded session's lifecycle/dimension hook carries, so `get_repository_sessions`
# (which only lists sessions with a nonempty `repository`) has something to find for every one of them.
_REPO = "git@example.com:org/period-membership.git"
_MODEL = "claude-sonnet-4-6"

_HOOK = "INSERT INTO hook_events (ts, session_id, event_type, cwd) VALUES ($1, $2, $3, $4)"
# command_name = '' (blank) = a genuine hookless prompt (must still admit); a non-blank value (e.g. 'exit') =
# a slash-command synthetic prompt (must NOT admit on its own).
_USER_PROMPT_LOG = """INSERT INTO log_events (ts, session_id, event_kind, command_name, span_id, severity, resource_id)
    VALUES ($1, $2, 2, $3, $4, 0, 0)"""
_INTERACTION_SPAN = """INSERT INTO spans
    (ts, duration_ns, trace_id, span_id, span_kind, span_name, session_id, status_code, resource_id)
    VALUES ($1, 1000000, $2, $3, 3, 'interaction', $4, 0, 0)"""
_ZERO_LINES_METRIC = """INSERT INTO metric_points
    (ts, metric_name, value, session_id, temporality, is_monotonic, resource_id)
    VALUES ($1, 'claude_code.lines_of_code.count', 0, $2, 0, false, 0)"""
_USAGE = """INSERT INTO usage_requests
    (ts, session_id, request_id, model_raw, model, input_tokens, output_tokens, cache_read_tokens, stop_reason)
    VALUES ($1, $2, $3, $4, $5, 10, 1, 0, 'end_turn')"""


def _id(name: str) -> bytes:
    # A real span_id/trace_id is a unique per-span OTel id; hash (not truncate) so two different names never
    # collapse to the same 8 bytes.
    return hashlib.sha1(name.encode()).digest()[:8]


async def _engine() -> AnalyticsPgEngine:
    settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL, CLI_ANALYTICS_PG_URL=_LOCAL_PG_URL))
    engine = AnalyticsPgEngine(settings)
    try:
        lock = await asyncpg.connect(settings.dsn)
    except (OSError, asyncpg.PostgresError) as exc:  # no reachable database: skip, do not fail
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
    try:
        await lock.execute("SELECT pg_advisory_lock($1)", _SCHEMA_LOCK)
        await PostgresAnalyticsRuntime(engine, settings).start()
    finally:
        await lock.close()  # closing the session releases its advisory lock
    return engine


async def _cleanup(engine: AnalyticsPgEngine, session_ids: list[str]) -> None:
    """Remove the sessions from every analytics table that has a session_id: raw rows, rollups and the queue."""
    async with engine.acquire() as conn:
        tables = await conn.fetch(
            """SELECT DISTINCT table_name FROM information_schema.columns
               WHERE table_schema = 'codemie_analytics' AND column_name = 'session_id'"""
        )
        for row in tables:
            await conn.execute(
                f"DELETE FROM codemie_analytics.{row['table_name']} WHERE session_id = ANY($1::text[])",  # noqa: S608
                session_ids,
            )


async def _refresh(engine: AnalyticsPgEngine, first_day: date = _DAY, last_day: date | None = None) -> None:
    async with live_rollup_lock(engine):
        refresher = RollupRefresher(engine, batch_size=100)
        async with engine.acquire() as conn:
            await refresher.mark_dirty(first_day, last_day or first_day, conn)
        await refresher.refresh()


def _window(start_dt: datetime, end_dt: datetime, session_ids: list[str]) -> LocalAnalyticsFilter:
    return LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=start_dt, end_dt=end_dt).restricted_to(session_ids)


async def _seed_control_session(conn: asyncpg.Connection, sid: str, ts: datetime) -> None:
    """A genuinely active session: one real `agent.prompt.submit` hook, a hookless (blank command_name)
    `user_prompt` log row, and a matching `usage_requests` row."""
    await conn.execute(_HOOK, ts, sid, "agent.prompt.submit", _REPO)
    await conn.execute(_USER_PROMPT_LOG, ts + timedelta(seconds=1), sid, "", _id(f"ctrl-log-{sid}"))
    await conn.execute(_USAGE, ts + timedelta(seconds=1), sid, f"req-{sid}", _MODEL, _MODEL)


async def _assert_admission(
    reader: PostgresCliAnalyticsReader, f: LocalAnalyticsFilter, excluded_sid: str, control_sid: str
) -> None:
    """Assert `excluded_sid` is absent and `control_sid` is present on every one of the six endpoints, for the
    sessions `f` is restricted to. Proves exclusion (not merely an empty-window false negative) because the
    control session passes through the same window and the same six calls."""
    total, page_ids = await reader.get_session_page(f, sort_by="start_time", page=0, per_page=1000)
    assert control_sid in page_ids, "get_session_page: control session missing"
    assert excluded_sid not in page_ids, "get_session_page: excluded session present"
    assert total == len(page_ids)

    kpis = await reader.get_cost_kpis(f)
    assert int(kpis[0]["total_sessions"]) == 1, "get_cost_kpis: total_sessions counts more than the control"

    # `get_users` returns counts, not ids: the control alone is counted, so the excluded session is not
    users = await reader.get_users(f)
    assert sum(int(row["session_count"]) for row in users) == 1, "get_users: counts more than the control"

    repo_rows = await reader.get_repository_sessions(f)
    repo_ids = {row["session_id"] for row in repo_rows}
    assert control_sid in repo_ids, "get_repository_sessions: control session missing"
    assert excluded_sid not in repo_ids, "get_repository_sessions: excluded session present"

    daily_rows = await reader.get_users_daily_activity(f)
    assert (
        sum(row["session_count"] for row in daily_rows) == 1
    ), "get_users_daily_activity: per-day session_count counts more than the control"

    # by="session" (not "day"): `get_class_lists(by="day")` only ever reads nonzero `lines_daily` rows joined
    # through `sel`, so it carries no exclusion signal at all for a session with no lines-of-code activity (the
    # control has none either) -- see the ruling in the task-6 report. `by="session"` is the one source whose
    # `key` is the session id itself.
    class_rows = await reader.get_class_lists(f, by="session")
    class_ids = {row["key"] for row in class_rows}
    assert control_sid in class_ids, "get_class_lists(by='session'): control session missing"
    assert excluded_sid not in class_ids, "get_class_lists(by='session'): excluded session present"


async def _assert_presence(reader: PostgresCliAnalyticsReader, f: LocalAnalyticsFilter, sid: str) -> None:
    """Assert `sid` is present on every one of the six endpoints, for the sessions `f` is restricted to. The
    positive counterpart of `_assert_admission`'s exclusion check: no control is needed here because a false
    pass (the assertion silently vacuous) is impossible for a presence check -- an empty result fails it."""
    total, page_ids = await reader.get_session_page(f, sort_by="start_time", page=0, per_page=1000)
    assert sid in page_ids, "get_session_page: session missing when its window covers real activity"
    assert total == len(page_ids)

    kpis = await reader.get_cost_kpis(f)
    assert int(kpis[0]["total_sessions"]) >= 1, "get_cost_kpis: total_sessions did not count the session"

    users = await reader.get_users(f)
    assert sum(int(row["session_count"]) for row in users) >= 1, "get_users: session missing"

    repo_rows = await reader.get_repository_sessions(f)
    repo_ids = {row["session_id"] for row in repo_rows}
    assert sid in repo_ids, "get_repository_sessions: session missing"

    daily_rows = await reader.get_users_daily_activity(f)
    assert sum(row["session_count"] for row in daily_rows) >= 1, "get_users_daily_activity: session missing"

    class_rows = await reader.get_class_lists(f, by="session")
    class_ids = {row["key"] for row in class_rows}
    assert sid in class_ids, "get_class_lists(by='session'): session missing"


@pytest.mark.asyncio
async def test_lifecycle_hooks_only_session_excluded_from_every_endpoint() -> None:
    excluded = f"t16496-lifecycle-{uuid.uuid4().hex[:8]}"
    control = f"t16496-lifecycle-ctl-{uuid.uuid4().hex[:8]}"
    start = datetime(_DAY.year, _DAY.month, _DAY.day, 12, tzinfo=timezone.utc)  # noon on _DAY
    window = _window(start, start + timedelta(hours=1), [excluded, control])

    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            t0 = start + timedelta(minutes=5)
            # Only lifecycle/housekeeping hooks: no prompt, tool, cost/usage or line-count row ever.
            await conn.execute(_HOOK, t0, excluded, "agent.session.start", _REPO)
            await conn.execute(_HOOK, t0 + timedelta(seconds=1), excluded, "agent.session.env", _REPO)
            await conn.execute(_HOOK, t0 + timedelta(seconds=2), excluded, "agent.session.end", _REPO)
            await conn.execute(_HOOK, t0 + timedelta(seconds=3), excluded, "agent.git.snapshot", _REPO)
            await _seed_control_session(conn, control, start + timedelta(minutes=10))
        await _refresh(engine, _DAY)

        async with engine.acquire() as conn:
            dims = await conn.fetchrow(
                "SELECT started_at, has_activity FROM session_dims WHERE session_id = $1", excluded
            )
        assert dims is not None and dims["started_at"] is not None, "excluded session has no session_dims row"

        reader = PostgresCliAnalyticsReader(engine)
        await _assert_admission(reader, window, excluded, control)
    finally:
        await _cleanup(engine, [excluded, control])
        await engine.close()


@pytest.mark.asyncio
async def test_exit_time_only_session_excluded_from_every_endpoint() -> None:
    excluded = f"t16496-exittime-{uuid.uuid4().hex[:8]}"
    control = f"t16496-exittime-ctl-{uuid.uuid4().hex[:8]}"
    start = datetime(_DAY.year, _DAY.month, _DAY.day, 14, tzinfo=timezone.utc)  # a different hour-preset window
    window = _window(start, start + timedelta(hours=1), [excluded, control])

    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            t0 = start + timedelta(minutes=5)
            # A start hook so the session has a started_at inside the window (every real CLI session writes
            # one) -- otherwise it would be missing from session_dims altogether, not admitted-then-correctly-
            # excluded, which would not exercise Task 1/3's narrowing.
            await conn.execute(_HOOK, t0, excluded, "agent.session.start", _REPO)
            await conn.execute(_ZERO_LINES_METRIC, t0 + timedelta(seconds=1), excluded)
            span_id = _id(f"exit-span-{excluded}")
            await conn.execute(_USER_PROMPT_LOG, t0 + timedelta(seconds=2), excluded, "exit", span_id)
            await conn.execute(
                _INTERACTION_SPAN, t0 + timedelta(seconds=2), _id(f"trace-{excluded}"), span_id, excluded
            )
            await _seed_control_session(conn, control, start + timedelta(minutes=10))
        await _refresh(engine, _DAY)

        async with engine.acquire() as conn:
            dims = await conn.fetchrow(
                "SELECT started_at, has_activity FROM session_dims WHERE session_id = $1", excluded
            )
        assert dims is not None and dims["started_at"] is not None, "excluded session has no session_dims row"
        assert dims["has_activity"] is False, f"excluded session's has_activity is {dims['has_activity']!r}, not False"

        reader = PostgresCliAnalyticsReader(engine)
        await _assert_admission(reader, window, excluded, control)
    finally:
        await _cleanup(engine, [excluded, control])
        await engine.close()


@pytest.mark.asyncio
async def test_whole_day_zero_lines_only_session_excluded_for_last_7_days_and_a_custom_whole_day_window() -> None:
    excluded = f"t16496-wholeday-{uuid.uuid4().hex[:8]}"
    control = f"t16496-wholeday-ctl-{uuid.uuid4().hex[:8]}"
    t0 = datetime(_DAY.year, _DAY.month, _DAY.day, 10, tzinfo=timezone.utc)

    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            # Ruling (see task-6 report): a lone `agent.session.start` hook, same as a real CLI session always
            # writes, plus the zero-value lines point -- not "no hooks at all" (that would leave started_at
            # NULL in session_dims and exclude the session trivially, vacuously, never reaching Task 1/2's
            # narrowed has_activity rule at all).
            await conn.execute(_HOOK, t0, excluded, "agent.session.start", _REPO)
            await conn.execute(_ZERO_LINES_METRIC, t0 + timedelta(seconds=1), excluded)
            await _seed_control_session(conn, control, t0 + timedelta(minutes=10))
        await _refresh(engine, _DAY)

        async with engine.acquire() as conn:
            dims = await conn.fetchrow(
                "SELECT started_at, has_activity FROM session_dims WHERE session_id = $1", excluded
            )
        assert dims is not None and dims["started_at"] is not None, (
            "empirical check failed: excluded session has no started_at in session_dims -- the 'no hooks at all' "
            "ambiguity's reasoning does not hold as expected, see the ruling in the task-6 report"
        )
        assert dims["has_activity"] is False, f"excluded session's has_activity is {dims['has_activity']!r}, not False"

        reader = PostgresCliAnalyticsReader(engine)

        # (i) a last_7_days-style window: a multi-day span with non-midnight boundary hours, so _DAY sits
        # strictly inside it as a whole UTC day (never the window's own first or last calendar day).
        last_7_days = _window(
            datetime(_DAY.year, _DAY.month, _DAY.day, 3, 17, tzinfo=timezone.utc) - timedelta(days=3),
            datetime(_DAY.year, _DAY.month, _DAY.day, 9, 42, tzinfo=timezone.utc) + timedelta(days=3),
            [excluded, control],
        )
        await _assert_admission(reader, last_7_days, excluded, control)

        # (ii) a custom window of whole UTC days: exactly _DAY, bound at UTC midnight on both sides.
        day_start = datetime(_DAY.year, _DAY.month, _DAY.day, tzinfo=timezone.utc)
        whole_day = _window(day_start, day_start + timedelta(days=1) - timedelta(milliseconds=1), [excluded, control])
        await _assert_admission(reader, whole_day, excluded, control)
    finally:
        await _cleanup(engine, [excluded, control])
        await engine.close()


@pytest.mark.asyncio
async def test_resumed_session_housekeeping_only_day_excluded_when_started_inside_or_outside_window() -> None:
    """Two sessions, each with a single real-activity day and a single housekeeping-only (lifecycle-hook-only)
    day, where the housekeeping-only day falls inside the queried window:

    - `excluded_in` starts (lifecycle hooks only -- no `agent.prompt.submit`) on `h_day`, the window's day, and
      has its only real activity later, on `r1_day`: `started_at` lands inside the window, `has_activity` is
      True and `resumed` is True (its real-activity day differs from its start day). Pre-Task-4, arm 1
      (`_STARTED_IN_WINDOW AND has_activity`, no `resumed` check at all) would admit it unconditionally; this is
      the new, previously-failing coverage.
    - `excluded_out` starts (with real activity) on `s2_day`, has `resumed` False (its only real-activity day
      IS its start day; the housekeeping-only day contributes no row to any of the five activity tables and so
      cannot make it `resumed`), and later gets a housekeeping-only continuation on `h_day`. `started_at` is
      outside the window; this half runs through arms 3-5, none of which Task 4 touched, so it is a
      non-regression guard, not new coverage -- it is excluded here by never matching any arm (its own last
      real-activity day is before the window, so even arm 4's `last_activity_day >= start` gate fails).

    Both are included once the window is moved to cover their own real-activity day instead (`excluded_in` via
    arm 4's activity rescue; `excluded_out` via arm 1, unaffected by the `resumed` check either way since its
    `resumed` is False)."""
    h_day = _DAY
    r1_day = _DAY + timedelta(days=1)
    s2_day = _DAY - timedelta(days=1)

    excluded_in = f"t16496-resumed-in-{uuid.uuid4().hex[:8]}"
    control_in = f"t16496-resumed-in-ctl-{uuid.uuid4().hex[:8]}"
    excluded_out = f"t16496-resumed-out-{uuid.uuid4().hex[:8]}"
    control_out = f"t16496-resumed-out-ctl-{uuid.uuid4().hex[:8]}"

    h_start = datetime(h_day.year, h_day.month, h_day.day, 16, tzinfo=timezone.utc)
    window_h_in = _window(h_start, h_start + timedelta(hours=1), [excluded_in, control_in])
    window_h_out = _window(h_start, h_start + timedelta(hours=1), [excluded_out, control_out])

    r1_start = datetime(r1_day.year, r1_day.month, r1_day.day, 9, tzinfo=timezone.utc)
    window_r1 = _window(r1_start, r1_start + timedelta(hours=1), [excluded_in])

    s2_start = datetime(s2_day.year, s2_day.month, s2_day.day, 11, tzinfo=timezone.utc)
    window_s2 = _window(s2_start, s2_start + timedelta(hours=1), [excluded_out])

    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            # excluded_in: lifecycle-hooks-only start inside the window, on h_day; real activity only later,
            # on r1_day -- a resumed session whose start day carries no real signal of its own.
            t0 = h_start + timedelta(minutes=5)
            await conn.execute(_HOOK, t0, excluded_in, "agent.session.start", _REPO)
            await conn.execute(_HOOK, t0 + timedelta(seconds=1), excluded_in, "agent.session.env", _REPO)
            await conn.execute(_HOOK, t0 + timedelta(seconds=2), excluded_in, "agent.session.end", _REPO)
            await _seed_control_session(conn, excluded_in, r1_start + timedelta(minutes=5))
            await _seed_control_session(conn, control_in, h_start + timedelta(minutes=10))

            # excluded_out: real activity (and started_at) on s2_day, outside the window; a lifecycle-hooks-only
            # continuation later, on h_day, inside the window.
            await _seed_control_session(conn, excluded_out, s2_start + timedelta(minutes=5))
            t1 = h_start + timedelta(minutes=20)
            await conn.execute(_HOOK, t1, excluded_out, "agent.session.env", _REPO)
            await conn.execute(_HOOK, t1 + timedelta(seconds=1), excluded_out, "agent.session.end", _REPO)
            await _seed_control_session(conn, control_out, h_start + timedelta(minutes=25))
        await _refresh(engine, s2_day, r1_day)

        async with engine.acquire() as conn:
            dims_in = await conn.fetchrow(
                "SELECT started_at, has_activity, resumed FROM session_dims WHERE session_id = $1", excluded_in
            )
            dims_out = await conn.fetchrow(
                "SELECT started_at, has_activity, resumed FROM session_dims WHERE session_id = $1", excluded_out
            )
        assert dims_in is not None and dims_in["started_at"] is not None, "excluded_in has no session_dims row"
        assert dims_in["has_activity"] is True, f"excluded_in has_activity is {dims_in['has_activity']!r}, not True"
        assert dims_in["resumed"] is True, (
            f"excluded_in resumed is {dims_in['resumed']!r}, not True -- its real-activity day (r1_day) must "
            "differ from its start day (h_day) for this to exercise the previously-failing case"
        )
        assert dims_out is not None and dims_out["started_at"] is not None, "excluded_out has no session_dims row"
        assert dims_out["has_activity"] is True, f"excluded_out has_activity is {dims_out['has_activity']!r}, not True"
        assert dims_out["resumed"] is False, (
            f"excluded_out resumed is {dims_out['resumed']!r}, not False -- its only real-activity day is its own "
            "start day (s2_day); the later housekeeping-only day (h_day) writes no row to any of the five "
            "activity tables and so cannot make it resumed"
        )

        reader = PostgresCliAnalyticsReader(engine)

        # Both excluded while the window covers only each session's housekeeping-only day.
        await _assert_admission(reader, window_h_in, excluded_in, control_in)
        await _assert_admission(reader, window_h_out, excluded_out, control_out)

        # Both included once the window covers their own real-activity day instead.
        await _assert_presence(reader, window_r1, excluded_in)
        await _assert_presence(reader, window_s2, excluded_out)
    finally:
        await _cleanup(engine, [excluded_in, control_in, excluded_out, control_out])
        await engine.close()


@pytest.mark.asyncio
async def test_cross_endpoint_session_totals_agree_for_the_same_filter() -> None:
    """For one filter mixing genuinely-active, housekeeping-only and exit-time-only sessions, every endpoint's
    notion of "how many sessions are in this window" must agree, because every one of them reads the same `sel`
    CTE -- this is a structural property of the six-endpoint rule (protected against Tasks 1-4 breaking it), not
    new behaviour this ticket's fix produces.

    Comparisons actually taken, each justified by the port's real return shape (`ports.py`/`reader.py`), not by
    an assumed shape:
    - `get_session_page`'s `total` (the first tuple element) is the reference total.
    - `get_cost_kpis`'s `total_sessions` -- the one reader method both the Overview and the Cost call sites use,
      so calling it once here covers both.
    - `get_users`: both `sum(session_count)` and the size of the union of `session_ids` are asserted equal to
      the reference total, and to each other. They are NOT independent signals here: `_DEVELOPER`
      (`_session_developer`) is a single deterministic value per session (never a multi-valued attribution), so
      a session can land in exactly one developer's group and the sum can never double-count.
    - `get_repository_sessions` has no aggregate count field (Task 6's finding); the count of distinct
      `session_id`s across its rows stands in for its total.
    - `get_class_lists(by="session")` likewise has no count field; the count of distinct `key`s (the session id
      itself for `by="session"`) stands in for its total.
    - `get_users_daily_activity` groups by (developer, day), so `sum(session_count)` counts session-DAYS, not
      sessions: a session active on two distinct days would appear, correctly, twice. The fixture below gives
      every included session exactly one real-activity day inside the window, which is what makes
      `sum(session_count) == total` a valid comparison here -- it is not a general identity."""
    active1 = f"t16496-xtotal-active-{uuid.uuid4().hex[:8]}"
    active2 = f"t16496-xtotal-active-{uuid.uuid4().hex[:8]}"
    housekeeping = f"t16496-xtotal-house-{uuid.uuid4().hex[:8]}"
    exittime = f"t16496-xtotal-exit-{uuid.uuid4().hex[:8]}"
    ids = [active1, active2, housekeeping, exittime]

    start = datetime(_DAY.year, _DAY.month, _DAY.day, 20, tzinfo=timezone.utc)
    window = _window(start, start + timedelta(hours=1), ids)

    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            await _seed_control_session(conn, active1, start + timedelta(minutes=1))
            await _seed_control_session(conn, active2, start + timedelta(minutes=2))

            # housekeeping-only: lifecycle hooks, no real signal -- has_activity False, excluded from every arm.
            t0 = start + timedelta(minutes=3)
            await conn.execute(_HOOK, t0, housekeeping, "agent.session.start", _REPO)
            await conn.execute(_HOOK, t0 + timedelta(seconds=1), housekeeping, "agent.session.env", _REPO)
            await conn.execute(_HOOK, t0 + timedelta(seconds=2), housekeeping, "agent.session.end", _REPO)

            # exit-time-only: a started_at, a zero-value lines row, and a slash-command synthetic prompt --
            # also has_activity False, also excluded from every arm.
            t1 = start + timedelta(minutes=4)
            await conn.execute(_HOOK, t1, exittime, "agent.session.start", _REPO)
            await conn.execute(_ZERO_LINES_METRIC, t1 + timedelta(seconds=1), exittime)
            span_id = _id(f"xtotal-exit-span-{exittime}")
            await conn.execute(_USER_PROMPT_LOG, t1 + timedelta(seconds=2), exittime, "exit", span_id)
            await conn.execute(
                _INTERACTION_SPAN, t1 + timedelta(seconds=2), _id(f"xtotal-trace-{exittime}"), span_id, exittime
            )
        await _refresh(engine, _DAY)

        reader = PostgresCliAnalyticsReader(engine)

        total, page_ids = await reader.get_session_page(window, sort_by="start_time", page=0, per_page=1000)
        assert set(page_ids) == {active1, active2}, f"get_session_page: unexpected page_ids {page_ids!r}"
        assert total == len(page_ids)
        assert total == 2, f"get_session_page total is {total}, expected 2 (active1, active2 only)"

        kpis = await reader.get_cost_kpis(window)
        cost_total = int(kpis[0]["total_sessions"])
        assert cost_total == total, f"get_cost_kpis total_sessions ({cost_total}) != get_session_page total ({total})"

        users = await reader.get_users(window)
        users_sum = sum(row["session_count"] for row in users)
        assert users_sum == total, (
            f"get_users: sum(session_count) ({users_sum}) != get_session_page total ({total}) -- developer_name "
            "is a single deterministic value per session, so no session can land in two developer groups"
        )

        daily_rows = await reader.get_users_daily_activity(window)
        daily_sum = sum(row["session_count"] for row in daily_rows)
        assert daily_sum == total, (
            f"get_users_daily_activity: sum(session_count) ({daily_sum}) != total ({total}) -- valid only because "
            "every included session here has its one real-activity row on a single calendar day inside the "
            "window; a session active on N distinct days would appear N times, so this sum is not generally a "
            "session total"
        )

        repo_rows = await reader.get_repository_sessions(window)
        repo_ids = {row["session_id"] for row in repo_rows}
        assert repo_ids == {active1, active2}, f"get_repository_sessions: unexpected ids {repo_ids!r}"

        class_rows = await reader.get_class_lists(window, by="session")
        class_ids = {row["key"] for row in class_rows}
        assert class_ids == {active1, active2}, f"get_class_lists(by='session'): unexpected keys {class_ids!r}"
    finally:
        await _cleanup(engine, ids)
        await engine.close()


@pytest.mark.asyncio
async def test_utc_midnight_crossing_hour_preset_excludes_daily_rollup_only_partial_day() -> None:
    """An hour-based preset window that crosses UTC midnight spans two partial days: a sub-24h cross-midnight
    window has no UTC day wholly inside it (`_FIRST_WHOLE_DAY_UTC` > `_LAST_WHOLE_DAY_UTC`), so `_active_in_window`
    falls through to its AND-join, which still requires a raw activity record stamped inside the window's own
    clock-time bounds, not merely a daily-rollup row whose `day` the window's UTC-date range covers.

    `excluded`'s only real activity sits at 10:00 the same UTC day -- well before the window's 23:30 start, so its
    daily rollup row lands on a day the window's date range covers (`window_days` true) but no raw record falls
    inside [window_start, window_end] (`raw` false): the AND-join must still exclude it. `control`'s activity sits
    inside the window's clock-time bounds and is admitted as usual."""
    excluded = f"t16496-midnight-{uuid.uuid4().hex[:8]}"
    control = f"t16496-midnight-ctl-{uuid.uuid4().hex[:8]}"
    window_start = datetime(_DAY.year, _DAY.month, _DAY.day, 23, 30, tzinfo=timezone.utc)
    window_end = window_start + timedelta(hours=1)  # crosses into the next UTC day
    window = _window(window_start, window_end, [excluded, control])

    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            # excluded: real activity well before the window's clock-time start, same UTC calendar day.
            await _seed_control_session(
                conn, excluded, datetime(_DAY.year, _DAY.month, _DAY.day, 10, tzinfo=timezone.utc)
            )
            # control: real activity stamped inside the window's own clock-time bounds.
            await _seed_control_session(conn, control, window_start + timedelta(minutes=5))
        await _refresh(engine, _DAY)

        async with engine.acquire() as conn:
            dims = await conn.fetchrow(
                "SELECT started_at, has_activity, resumed FROM session_dims WHERE session_id = $1", excluded
            )
        assert dims is not None and dims["started_at"] is not None, "excluded session has no session_dims row"
        assert dims["has_activity"] is True, f"excluded session's has_activity is {dims['has_activity']!r}, not True"
        assert dims["resumed"] is False, (
            f"excluded session's resumed is {dims['resumed']!r}, not False -- its only activity day is its own "
            "start day, so this must be the AND-join's raw-record requirement excluding it, not a resumed arm"
        )

        reader = PostgresCliAnalyticsReader(engine)
        await _assert_admission(reader, window, excluded, control)
    finally:
        await _cleanup(engine, [excluded, control])
        await engine.close()


@pytest.mark.asyncio
async def test_underived_resumed_session_decided_live_without_backfill() -> None:
    """Arm 2 ("started, underived") decides a session whose facts are not yet derived (`has_activity`/`resumed`
    both NULL) by reading the rollup day tables directly through `_live_resumed`, standing in for the stored
    `resumed` fact until `session_activity.derive_session_activity` runs (EPMCDME-16993). This test writes
    `session_dims` and `turns_daily` rows directly and deliberately skips `_refresh` -- which always derives the
    facts right after writing day rows (see `session_activity.derive_session_activity`'s call inside
    `RollupRefresher._recompute`) -- so both sessions reach the admission query with `has_activity IS NULL` and
    exercise the live branch for real, not through a hand-mirrored SQL-shape string.

    Both sessions start inside the window and both carry a day row on a day other than their own start day
    (`_live_resumed` True for both): `excluded`'s sits on a day far outside the window entirely, so
    `_active_in_window` finds nothing and it stays excluded; `admitted`'s sits on the window's other whole day,
    so it is admitted -- exactly the distinction a derived `resumed` session would make via `_active_in_window`
    too."""
    s_day = _DAY
    other_whole_day = _DAY + timedelta(days=1)
    far_outside_day = _DAY + timedelta(days=30)

    excluded = f"t16496-liveresumed-{uuid.uuid4().hex[:8]}"
    admitted = f"t16496-liveresumed-adm-{uuid.uuid4().hex[:8]}"
    started_at = datetime(s_day.year, s_day.month, s_day.day, 12, tzinfo=timezone.utc)

    day_start = datetime(s_day.year, s_day.month, s_day.day, tzinfo=timezone.utc)
    window = _window(day_start, day_start + timedelta(days=2) - timedelta(milliseconds=1), [excluded, admitted])

    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            # session_dims rows written directly: started_at set, has_activity/resumed left at their column
            # default (NULL) -- never derived, unlike every other test in this file.
            await conn.execute(
                "INSERT INTO session_dims (session_id, started_at) VALUES ($1, $2), ($3, $2)",
                excluded,
                started_at,
                admitted,
            )
            # excluded: its only non-start-day activity row sits on a day far outside the 2-day window.
            await conn.execute(
                "INSERT INTO turns_daily (day, session_id, turns) VALUES ($1, $2, 1)", far_outside_day, excluded
            )
            # admitted: its non-start-day activity row sits on the window's other whole day.
            await conn.execute(
                "INSERT INTO turns_daily (day, session_id, turns) VALUES ($1, $2, 1)", other_whole_day, admitted
            )

        async with engine.acquire() as conn:
            dims = await conn.fetch(
                "SELECT session_id, has_activity, resumed FROM session_dims WHERE session_id = ANY($1::text[])",
                [excluded, admitted],
            )
        for row in dims:
            assert row["has_activity"] is None, (
                f"{row['session_id']}: has_activity is {row['has_activity']!r}, not NULL -- _refresh must not "
                "have run for this test to exercise the live, underived branch"
            )
            assert row["resumed"] is None, f"{row['session_id']}: resumed is {row['resumed']!r}, not NULL"

        reader = PostgresCliAnalyticsReader(engine)
        _, page_ids = await reader.get_session_page(window, sort_by="start_time", page=0, per_page=1000)
        assert admitted in page_ids, "live-resumed session with in-window activity was not admitted"
        assert excluded not in page_ids, "live-resumed session with no in-window activity was admitted"
    finally:
        await _cleanup(engine, [excluded, admitted])
        await engine.close()


@pytest.mark.asyncio
async def test_session_started_in_a_window_whose_only_prompt_comes_after_the_window_end_is_excluded() -> None:
    """Started at 10:00, idle, first prompt at 11:00: not in 10:00-10:30, nor in 00:00-10:30 (a window that starts at
    midnight but ends mid-day is asked for the raw record too); in 10:00-11:30 it is listed on every endpoint."""
    excluded = f"t16496-lateprompt-{uuid.uuid4().hex[:8]}"
    control = f"t16496-lateprompt-ctl-{uuid.uuid4().hex[:8]}"
    ten = datetime(_DAY.year, _DAY.month, _DAY.day, 10, tzinfo=timezone.utc)
    ids = [excluded, control]

    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            await conn.execute(_HOOK, ten, excluded, "agent.session.start", _REPO)
            await _seed_control_session(conn, excluded, ten + timedelta(hours=1))
            await _seed_control_session(conn, control, ten + timedelta(minutes=10))
        await _refresh(engine, _DAY)

        reader = PostgresCliAnalyticsReader(engine)
        midnight = datetime(_DAY.year, _DAY.month, _DAY.day, tzinfo=timezone.utc)
        for start, end in ((ten, ten + timedelta(minutes=30)), (midnight, ten + timedelta(minutes=30))):
            await _assert_admission(reader, _window(start, end, ids), excluded, control)

        await _assert_presence(reader, _window(ten, ten + timedelta(hours=1, minutes=30), [excluded]), excluded)
    finally:
        await _cleanup(engine, ids)
        await engine.close()


@pytest.mark.asyncio
async def test_a_lone_session_end_does_not_start_the_session_that_reuses_its_id_later() -> None:
    """GitHub Copilot Chat (Copilot CLI target): the pre-warmed session expires unused and its end is forwarded alone;
    minutes later the chat's first prompt comes under the same id. The end is not the start: the session starts at
    its first prompt (also when the end was rolled up before the prompt arrived), and is not listed for a window that
    holds only the end."""
    reused = f"t16496-loneend-{uuid.uuid4().hex[:8]}"
    control = f"t16496-loneend-ctl-{uuid.uuid4().hex[:8]}"
    ids = [reused, control]
    end_at = datetime(_DAY.year, _DAY.month, _DAY.day, 14, 25, 42, tzinfo=timezone.utc)
    prompt_at = end_at + timedelta(minutes=29)

    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            await conn.execute(_HOOK, end_at, reused, "agent.session.end", _REPO)
            await _seed_control_session(conn, control, end_at + timedelta(seconds=5))
        await _refresh(engine, _DAY)  # the end is rolled up alone, before the prompt exists

        async with engine.acquire() as conn:
            await _seed_control_session(conn, reused, prompt_at)
        await _refresh(engine, _DAY)

        async with engine.acquire() as conn:
            dims = await conn.fetchrow(
                "SELECT started_at, activity_started_at FROM session_dims WHERE session_id = $1", reused
            )
        assert dims["started_at"] == prompt_at, f"started_at is {dims['started_at']}, not the first prompt {prompt_at}"
        # _RAW_RANGE_START_FILTER must exclude the lone session.end from fb.first_ts too (rollups.py), or the
        # Sessions start time shown for a no-summary session (activity_started_at) regresses back to it even
        # though started_at (a separate computation, _MERGED_START plus admission) is already correct (MR review)
        assert dims["activity_started_at"] == prompt_at, (
            f"activity_started_at is {dims['activity_started_at']}, not the first prompt {prompt_at} -- "
            "the lone session.end must not be treated as the earliest raw record either"
        )

        reader = PostgresCliAnalyticsReader(engine)
        only_the_end = _window(end_at - timedelta(seconds=12), end_at + timedelta(seconds=18), ids)
        await _assert_admission(reader, only_the_end, reused, control)
        await _assert_presence(
            reader, _window(prompt_at - timedelta(minutes=1), prompt_at + timedelta(minutes=1), [reused]), reused
        )
    finally:
        await _cleanup(engine, ids)
        await engine.close()
