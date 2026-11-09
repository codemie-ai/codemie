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

"""Live-Postgres session list: the DB-chosen page (start_time / cost_usd / ctx_per_call sorts, search, the
unattributed filter) returns the pages, order and totals captured from the retired Python-paged path.

Sessions are seeded straight into session_dims / cost_daily / session_usage under one unique email, and every
read is filtered to that email, so other tests sharing the database cannot change the result. Skipped when
`TEST_LOCAL_PG_URL` is not set or unreachable: point it at a disposable PostgreSQL (the live suite migrates it).
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader, positional
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from codemie.service.analytics.handlers.coding_agent_pricing import model_rates_for
from tests.codemie.repository.cli_analytics.postgres.test_live_session_subagent_cost import (
    _COST_MODEL,
    _COST_MODEL_2,
    _KNOWN_MODEL,
    _lookup,
)
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")

_UNPRICED_MODEL = "not-a-real-model-xyz"
_PRICING = "codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config"

_DIMS_SQL = """
    INSERT INTO session_dims (session_id, started_at, last_event_at, repository, branch, jwt_email, first_prompt)
    VALUES ($1, $2, $3, $4, $5, $6, $7)
"""
_COST_SQL = """
    INSERT INTO cost_daily
        (day, session_id, user_email, model_name, query_source, cost_usd, input_tokens, output_tokens,
         cache_read_tokens, cache_creation_tokens, api_call_count)
    VALUES ($1, $2, $3, $4, 'repl_main_thread', 0.5, $5, 10, $6, 0, $7)
"""
_TOTALS_SQL = """
    INSERT INTO session_usage
        (session_id, day, model, speed, inference_geo, scope_kind, scope_name, agent_id,
         input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, attrs)
    VALUES ($1, $2, $3, '', '', '', '', 'ag1', 100, 20, $4, 0, $5::jsonb)
"""
_TURNS_SQL = "INSERT INTO turns_daily (day, session_id, turns) VALUES ($1, $2, 1)"


async def _engine() -> AnalyticsPgEngine:
    try:
        engine = AnalyticsPgEngine(AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL)))
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return engine
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")


async def _cleanup(engine: AnalyticsPgEngine, session_ids: list[str]) -> None:
    async with engine.acquire() as conn:
        for table in ("session_dims", "cost_daily", "session_usage", "turns_daily"):
            await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", session_ids)  # noqa: S608


def _naive(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class _Seed:
    """Twelve sessions of one user: ties on start_time and on ctx_per_call, cost-less, hooks-only, totals-only,
    several models x days, an unpriced model, an unattributed repository and search terms with `%`, `_` and `\\`."""

    def __init__(self) -> None:
        self.suffix = uuid.uuid4().hex
        self.email = f"page-{self.suffix}@example.com"
        self.t0 = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=5)

    def sid(self, number: int) -> str:
        return f"test-page-{self.suffix}-{number:02d}"

    def ids(self) -> list[str]:
        return [self.sid(n) for n in range(1, 13)]

    def filter(self, **kwargs) -> LocalAnalyticsFilter:
        return LocalAnalyticsFilter(
            scope=EXCLUDE_SCOPE,
            start_dt=_naive(self.t0 - timedelta(days=1)),
            end_dt=_naive(self.t0 + timedelta(days=1)),
            users=[self.email],
            **kwargs,
        )

    async def insert(self, engine: AnalyticsPgEngine) -> None:
        day, before = self.t0.date(), self.t0.date() - timedelta(days=1)
        # (number, start offset in minutes, repository, branch, prompt)
        dims = [
            (1, 0, "repo-a", "main", "fix 100% bug"),
            (2, 0, "repo_b", "main", "snake_case thing"),
            (3, 0, "", "", "plain costless"),
            (4, 1, "repo-a", "feature/x", "totals only"),
            (5, 2, "", "", "100 percent"),
            (6, 2, "repo-a", "main", "no calls"),
            (7, 3, "repo-c", "dev", "totals and cost"),
            (8, 4, "repo-c", "main", "path C:\\dir"),
            (9, 5, "repo-c", "dev", "unpriced model"),
            (10, 6, "", "", "UPPER Case_ term"),
            (11, 6, "", "", "snakeXcase"),
            (12, 7, "repo-a", "dev", "last one"),
        ]
        # (number, day, model, input tokens, cache-read tokens, api calls)
        costs = [
            (1, day, _COST_MODEL, 100, 600, 6),
            (1, before, _COST_MODEL_2, 50, 400, 4),  # 2 models x 2 days: ctx 1000 / 10 calls = 100.0
            (2, day, _KNOWN_MODEL, 100, 400, 4),  # ties with session 1 at ctx 100.0
            (6, day, _KNOWN_MODEL, 10, 77, 0),  # no calls: ctx 0.0
            (7, day, _KNOWN_MODEL, 10, 90, 3),  # 90 + 300 from the totals / 3 calls = 130.0
            (8, day, _KNOWN_MODEL, 10, 100, 3),  # 33.333333333
            (9, day, _UNPRICED_MODEL, 10, 100, 7),  # 14.285714286
            (11, day, _KNOWN_MODEL, 10, 0, 0),
            (12, day, _KNOWN_MODEL, 10, 1, 2),  # 0.5
        ]
        totals = [(4, 500), (7, 300)]
        # sessions 3, 5 and 10 have no cost_daily/session_usage row at all: a turns_daily row of their own
        # represents real conversational activity with no cost, not a zero-activity session.
        activity_only = [3, 5, 10]
        async with engine.acquire() as conn:
            for number, minutes, repository, branch, prompt in dims:
                started = self.t0 + timedelta(minutes=minutes)
                await conn.execute(
                    _DIMS_SQL, self.sid(number), started, started + timedelta(minutes=1), repository, branch,
                    self.email, prompt,
                )  # fmt: skip
            for number, cost_day, model, tokens, cache_read, calls in costs:
                await conn.execute(_COST_SQL, cost_day, self.sid(number), self.email, model, tokens, cache_read, calls)
            for number, cache_read in totals:
                await conn.execute(
                    _TOTALS_SQL, self.sid(number), day, _KNOWN_MODEL, cache_read,
                    json.dumps({"token_source": "subagent_usage"}),
                )  # fmt: skip
            for number in activity_only:
                await conn.execute(_TURNS_SQL, day, self.sid(number))


async def _page(handler: LocalAnalyticsHandler, seed: _Seed, page: int, per_page: int, sort_by: str, **kw):
    data, _, data_as_of = await handler.get_sessions(
        seed.filter(), page, per_page, sort_by, kw.pop("search", None), **kw
    )
    return data, data_as_of


_CAPTURED = json.loads((Path(__file__).parent / "fixtures" / "session_page_captured.json").read_text())
_SEARCHES = [None, "100%", "snake_", "REPO-A", "feature", "\\", "  ", "case_", "no-such-term"]
_SORTS = ("start_time", "ctx_per_call", "cost_usd")


async def _rates(reader: PostgresCliAnalyticsReader, f: LocalAnalyticsFilter) -> list:
    return [model_rates_for(model) for model, _ in await reader.get_window_model_flags(f)]


@pytest.mark.asyncio
async def test_the_db_chosen_page_matches_the_captured_pages_of_the_retired_python_path():
    """`session_page_captured.json` holds what the Python-paged path (and the earlier SQL page for the two sorts it
    had) returned for this very seed, as `sort|search|unattributed -> [total, ordered seed numbers]`."""
    engine = await _engine()
    seed = _Seed()
    try:
        await seed.insert(engine)
        reader = PostgresCliAnalyticsReader(engine)
        compared = 0
        with patch(_PRICING, _lookup):
            rates = await _rates(reader, seed.filter())
            for sort_by in _SORTS:
                for search in _SEARCHES:
                    for unattributed in (False, True):
                        total, expected = _CAPTURED[f"{sort_by}|{search}|{unattributed}"]
                        seen: list[str] = []
                        for per_page in (1000, 1, 5, 3):
                            seen = []
                            for page in range(0, len(expected) // per_page + 2):
                                got_total, ids = await reader.get_session_page(
                                    seed.filter(), sort_by, page, per_page, search, unattributed, rates=rates
                                )
                                assert got_total == total, (sort_by, search, unattributed, page, per_page)
                                assert ids == [seed.sid(n) for n in expected[page * per_page :][:per_page]], (
                                    sort_by, search, unattributed, page, per_page,
                                )  # fmt: skip
                                seen += ids
                                compared += 1
                            assert seen == [seed.sid(n) for n in expected]  # no repeat, no skip across pages
                # a page far past the end is empty but keeps the total, and a huge page number is clamped
                for page in (50, 10**18):
                    got_total, ids = await reader.get_session_page(seed.filter(), sort_by, page, 5, rates=rates)
                    assert (got_total, ids) == (12, [])
        assert compared > 300
    finally:
        await _cleanup(engine, seed.ids())
        await engine.close()


@pytest.mark.asyncio
async def test_a_search_matches_the_session_title_and_a_nul_matches_nothing():
    engine = await _engine()
    seed = _Seed()
    try:
        await seed.insert(engine)
        async with engine.acquire() as conn:
            await conn.execute(
                "UPDATE session_dims SET session_title = $2 WHERE session_id = $1", seed.sid(6), "Quarterly Review"
            )
        reader = PostgresCliAnalyticsReader(engine)
        with patch(_PRICING, _lookup):
            rates = await _rates(reader, seed.filter())
            for sort_by in _SORTS:
                got = await reader.get_session_page(seed.filter(), sort_by, 0, 20, "quarterly rev", rates=rates)
                assert got == (1, [seed.sid(6)]), sort_by
                assert await reader.get_session_page(seed.filter(), sort_by, 0, 20, "a\x00b", rates=rates) == (0, [])
    finally:
        await _cleanup(engine, seed.ids())
        await engine.close()


@pytest.mark.asyncio
async def test_the_framework_page_pages_the_sessions_of_the_accepted_name_sets_in_page_order():
    engine = await _engine()
    seed = _Seed()
    try:
        await seed.insert(engine)
        reader = PostgresCliAnalyticsReader(engine)
        with patch(_PRICING, _lookup):
            rates = [model_rates_for(m) for m in await reader.get_cost_models(seed.filter())]
            expected_flags = await reader.get_window_model_flags(seed.filter())
            for sort_by in _SORTS:
                for search, unattributed in ((None, False), ("REPO-A", False), (None, True)):
                    total, expected = _CAPTURED[f"{sort_by}|{search}|{unattributed}"]
                    seen_sets: list = []

                    def accept_all(sets, seen_sets=seen_sets):
                        seen_sets.append(sets)
                        return sets

                    got = await reader.get_framework_page(
                        seed.filter(), sort_by, 0, 1000, accept_all, rates, search, unattributed
                    )
                    assert (got[0], got[1]) == (total, [seed.sid(n) for n in expected]), (sort_by, search)
                    assert seen_sets == [[[]]]  # no session has a name: one set, the empty one
            assert {m for m, _ in expected_flags} >= {_KNOWN_MODEL, _COST_MODEL, _UNPRICED_MODEL}
            async with engine.acquire() as conn:
                await conn.execute(
                    "UPDATE session_dims SET skills = $2::jsonb WHERE session_id = $1",
                    seed.sid(7), json.dumps({"zeta": 1, "alpha": 2}),
                )  # fmt: skip
            for sort_by in _SORTS:
                sets_seen: list = []

                def accept_named(sets, sets_seen=sets_seen):
                    sets_seen.append(sorted(sets))
                    return [names for names in sets if names]

                total, ids, flags = await reader.get_framework_page(seed.filter(), sort_by, 0, 5, accept_named, rates)
                assert (total, ids) == (1, [seed.sid(7)]), sort_by
                assert sets_seen == [[[], ["alpha", "zeta"]]]
                assert {m for m, _ in flags} >= {_KNOWN_MODEL, _COST_MODEL, _UNPRICED_MODEL}
            # every other session is accepted by the empty set; page 2 of 5 keeps the order and the exact total
            total, expected = _CAPTURED["start_time|None|False"]
            rest = [n for n in expected if n != 7]
            got_total, ids, _ = await reader.get_framework_page(
                seed.filter(), "start_time", 1, 5, lambda sets: [names for names in sets if not names]
            )
            assert (got_total, ids) == (len(rest), [seed.sid(n) for n in rest[5:10]])
            # a page past the end keeps the total
            assert (await reader.get_framework_page(seed.filter(), "start_time", 50, 5, lambda s: s))[:2] == (
                len(expected), [],
            )  # fmt: skip
    finally:
        await _cleanup(engine, seed.ids())
        await engine.close()


@pytest.mark.asyncio
async def test_the_sql_paged_list_orders_by_key_then_session_id_and_counts_every_session():
    engine = await _engine()
    seed = _Seed()
    try:
        await seed.insert(engine)
        handler = LocalAnalyticsHandler(PostgresCliAnalyticsReader(engine))
        n = seed.sid
        with patch(_PRICING, _lookup):
            by_start, _ = await _page(handler, seed, 0, 1000, "start_time")
            by_ctx, _ = await _page(handler, seed, 0, 1000, "ctx_per_call")
            middle, as_of = await _page(handler, seed, 1, 3, "start_time")
            past_end, past_as_of = await _page(handler, seed, 9, 3, "start_time")
            searched, _ = await _page(handler, seed, 0, 1000, "start_time", search="100%")
            unattributed, _ = await _page(handler, seed, 0, 1000, "start_time", is_unattributed=True)

        # start_time DESC, ties (sessions 10 / 11, 5 / 6, 1 / 2 / 3) by session id ASC
        assert [s["trace_id"] for s in by_start["sessions"]] == [n(k) for k in (12, 10, 11, 9, 8, 7, 5, 6, 4, 1, 2, 3)]
        # ctx_per_call DESC, ties by id; sessions without calls (3, 4, 5, 6, 10, 11) report 0.0
        assert [s["trace_id"] for s in by_ctx["sessions"]] == [n(k) for k in (7, 1, 2, 8, 9, 12, 3, 4, 5, 6, 10, 11)]
        assert [s["ctx_per_call"] for s in by_ctx["sessions"]][:3] == [130.0, 100.0, 100.0]
        assert by_start["total"] == by_ctx["total"] == 12
        assert [s["trace_id"] for s in middle["sessions"]] == [n(k) for k in (9, 8, 7)] and middle["total"] == 12
        assert as_of == max(s["start_time"] for s in middle["sessions"])
        assert (past_end["sessions"], past_end["total"], past_as_of) == ([], 12, None)
        assert [s["trace_id"] for s in searched["sessions"]] == [n(1)] and searched["total"] == 1
        assert [s["trace_id"] for s in unattributed["sessions"]] == [n(k) for k in (10, 11, 5, 3)]
        # the cost-less and unpriced sessions are listed, flagged and free
        rows = {s["trace_id"]: s for s in by_start["sessions"]}
        assert [rows[n(k)]["cost_available"] for k in (1, 3, 4, 5, 9)] == [True, False, True, False, False]
        assert rows[n(1)]["model_name"] == f"{_COST_MODEL}, {_COST_MODEL_2}"
        assert rows[n(1)]["api_call_count"] == 10 and rows[n(7)]["cache_read_tokens"] == 390
    finally:
        await _cleanup(engine, seed.ids())
        await engine.close()


@pytest.mark.asyncio
async def test_a_session_page_never_leaves_the_selected_users_sessions():
    engine = await _engine()
    seed, other = _Seed(), _Seed()
    try:
        await seed.insert(engine)
        await other.insert(engine)
        reader = PostgresCliAnalyticsReader(engine)

        total, ids = await reader.get_session_page(seed.filter(), "start_time", 0, 1000)
        assert (total, sorted(ids)) == (12, sorted(seed.ids()))

        # ids of another user's sessions narrow the selection, they never widen it past the user scope
        foreign = seed.filter().restricted_to(other.ids())
        assert await reader.get_session_cost_facts(foreign, include_costless=True) == []
        assert await reader.get_session_model_costs(foreign) == []

        # the per-session queries of a page read that page's sessions only
        page = seed.filter().restricted_to([seed.sid(1), seed.sid(7)])
        facts = await reader.get_session_cost_facts(page, include_costless=True)
        assert sorted(r["session_id"] for r in facts) == [seed.sid(1), seed.sid(7)]
        assert {r["session_id"] for r in await reader.get_session_model_costs(page)} == {seed.sid(1), seed.sid(7)}

        denied = LocalAnalyticsFilter(
            scope=EXCLUDE_SCOPE,
            start_dt=seed.filter().start_dt,
            end_dt=seed.filter().end_dt,
            users=[seed.email],
            deny_all=True,
        )
        assert await reader.get_session_page(denied, "start_time", 0, 20) == (0, [])
    finally:
        await _cleanup(engine, seed.ids() + other.ids())
        await engine.close()


@pytest.mark.asyncio
async def test_the_cost_page_returns_the_captured_pages_and_the_windows_kind_flags_in_one_statement():
    engine = await _engine()
    seed = _Seed()
    try:
        await seed.insert(engine)
        reader = PostgresCliAnalyticsReader(engine)
        with patch(_PRICING, _lookup):
            models = await reader.get_cost_models(seed.filter())
            assert {_KNOWN_MODEL, _COST_MODEL, _UNPRICED_MODEL} <= set(models)
            rates = [model_rates_for(m) for m in await reader.get_cost_models(seed.filter())]
            expected_flags = await reader.get_window_model_flags(seed.filter())
            for search, unattributed in ((None, False), ("REPO-A", False), (None, True)):
                total, expected = _CAPTURED[f"cost_usd|{search}|{unattributed}"]
                for per_page in (1000, 5):
                    seen: list[str] = []
                    for page in range(0, len(expected) // per_page + 2):
                        got_total, ids, flags = await reader.get_cost_page(
                            seed.filter(), page, per_page, rates, search, unattributed
                        )
                        assert got_total == total
                        assert ids == [seed.sid(n) for n in expected[page * per_page :][:per_page]]
                        assert [f for f in flags if f[0] in {m for m, _ in expected_flags}] == [
                            f for f in expected_flags if f[0] in {m for m, _ in flags}
                        ]
                        seen += ids
                    assert seen == [seed.sid(n) for n in expected]
            assert await reader.get_cost_page(seed.filter(), 0, 5, rates, "a\x00b") == (0, [], expected_flags)
    finally:
        await _cleanup(engine, seed.ids())
        await engine.close()


@pytest.mark.asyncio
async def test_the_unpriced_flags_cover_the_window_before_search_framework_and_unattributed():
    """AC13 (amended 2026-11-06): on the cost sort and the framework filter the flags are the window's, not the
    listed sessions'. Session 9 is the only user of the uncatalogued model and none of these requests lists it, yet
    the model is flagged: a documented difference from `main`, which narrowed the flags to the listed sessions."""
    engine = await _engine()
    seed = _Seed()
    try:
        await seed.insert(engine)
        reader = PostgresCliAnalyticsReader(engine)
        with patch(_PRICING, _lookup):
            rates = [model_rates_for(m) for m in await reader.get_cost_models(seed.filter())]
            window = dict(await reader.get_window_model_flags(seed.filter()))
            assert _UNPRICED_MODEL in window
            for search, unattributed in (("REPO-A", False), (None, True)):
                total, ids, flags = await reader.get_cost_page(seed.filter(), 0, 1000, rates, search, unattributed)
                assert seed.sid(9) not in ids and total == len(ids)
                assert dict(flags)[_UNPRICED_MODEL] == window[_UNPRICED_MODEL]
            # a framework filter that accepts only the named session 7
            async with engine.acquire() as conn:
                await conn.execute(
                    "UPDATE session_dims SET skills = $2::jsonb WHERE session_id = $1",
                    seed.sid(7), json.dumps({"alpha": 1}),
                )  # fmt: skip
            for sort_by in ("start_time", "cost_usd"):
                total, ids, flags = await reader.get_framework_page(
                    seed.filter(), sort_by, 0, 5, lambda sets: [n for n in sets if n], rates
                )
                assert (total, ids) == (1, [seed.sid(7)]), sort_by
                assert dict(flags)[_UNPRICED_MODEL] == window[_UNPRICED_MODEL], sort_by
    finally:
        await _cleanup(engine, seed.ids())
        await engine.close()


@pytest.mark.asyncio
async def test_a_project_filtered_start_time_page_is_exact_for_a_selective_and_a_nonexistent_project():
    engine = await _engine()
    seed = _Seed()
    try:
        await seed.insert(engine)
        reader = PostgresCliAnalyticsReader(engine)
        async with engine.acquire() as conn:
            await conn.execute(
                "UPDATE session_dims SET project_name = 'plum-test' WHERE session_id = ANY($1::text[])",
                [seed.sid(3), seed.sid(5), seed.sid(8)],
            )
        total_all, all_ids = await reader.get_session_page(seed.filter(), "start_time", 0, 100)
        assert total_all == 12
        selective = seed.filter(projects=["plum-test"])
        total, ids = await reader.get_session_page(selective, "start_time", 0, 2)
        assert total == 3 and ids == [i for i in all_ids if i in {seed.sid(3), seed.sid(5), seed.sid(8)}][:2]
        total, ids = await reader.get_session_page(selective, "start_time", 1, 2)
        assert total == 3 and len(ids) == 1
        assert await reader.get_session_page(selective, "start_time", 5, 2) == (3, [])  # past the end keeps the total
        assert await reader.get_session_page(seed.filter(projects=["plum"]), "start_time", 0, 20) == (0, [])
    finally:
        await _cleanup(engine, seed.ids())
        await engine.close()


def _unfiltered(seed: _Seed) -> LocalAnalyticsFilter:
    """The unfiltered admin shared list over the seed's window: the request the covering index serves."""
    return LocalAnalyticsFilter(
        scope=EXCLUDE_SCOPE, start_dt=_naive(seed.t0 - timedelta(days=1)), end_dt=_naive(seed.t0 + timedelta(days=1))
    )


def _full_projection(monkeypatch) -> None:
    """Force the pre-change projection of `sel` (every lean column) for the ids-only comparison."""
    original = PostgresCliAnalyticsReader._sessions_cte

    def full(f, lean=False, searchable=False, ids_only=False, with_start=False, split_arms=False):
        return original(f, lean, searchable, False, False, split_arms)

    monkeypatch.setattr(PostgresCliAnalyticsReader, "_sessions_cte", staticmethod(full))


@pytest.mark.asyncio
async def test_the_ids_only_ctx_per_call_page_returns_the_same_ids_order_and_total_as_the_full_projection(monkeypatch):
    engine = await _engine()
    seed = _Seed()
    try:
        await seed.insert(engine)
        reader = PostgresCliAnalyticsReader(engine)
        f = _unfiltered(seed)
        with monkeypatch.context() as m:
            _full_projection(m)
            full = [await reader.get_session_page(f, "ctx_per_call", p, n) for p, n in ((0, 1000), (1, 5), (0, 3))]
        ids_only = [await reader.get_session_page(f, "ctx_per_call", p, n) for p, n in ((0, 1000), (1, 5), (0, 3))]

        assert ids_only == full
        total, ids = ids_only[0]
        assert total >= 12 and set(seed.ids()) <= set(ids)
        mine = [i for i in ids if i in set(seed.ids())]
        n = seed.sid  # ctx_per_call DESC, ties by id ASC (the seeded order of the sql-paged test)
        assert mine == [n(k) for k in (7, 1, 2, 8, 9, 12, 3, 4, 5, 6, 10, 11)]
    finally:
        await _cleanup(engine, seed.ids())
        await engine.close()


@pytest.mark.asyncio
async def test_the_unfiltered_ctx_per_call_window_is_read_index_only_from_the_covering_index():
    engine = await _engine()
    seed = _Seed()
    statements: list[tuple[str, dict]] = []
    bulk = f"test-page-bulk-{seed.suffix}-"
    reader = PostgresCliAnalyticsReader(engine)
    original = reader._q

    async def record(sql, params):
        statements.append((sql, params))
        return await original(sql, params)

    reader._q = record  # type: ignore[method-assign]
    try:
        await seed.insert(engine)
        await reader.get_session_page(_unfiltered(seed), "ctx_per_call", 0, 5)
        sql, params = statements[0]
        text, args = positional(sql, params)
        async with engine.acquire() as conn:
            # A window of a few rows is planned by page-count ties; a real one is not: bulk-seed 50k shared sessions
            # spread over 200 days (about 1% in the window), mark their pages all-visible and ask the planner with no setting changed.
            await conn.execute(
                "INSERT INTO session_dims (session_id, started_at) "
                "SELECT $1::text || g::text, $2::timestamptz - (g % 200) * interval '1 day' FROM generate_series(1, 50000) g",
                bulk, seed.t0,
            )  # fmt: skip
            # the admission rule reads the stored activity fact: a derived window is what the index serves
            await conn.execute(
                "UPDATE session_dims SET has_activity = true WHERE left(session_id, $2) = $1", bulk, len(bulk)
            )
            await conn.execute("VACUUM (ANALYZE) session_dims")
            plan = "\n".join(r[0] for r in await conn.fetch("EXPLAIN " + text, *args))

        assert "Index Only Scan using session_dims_active_shared_cov on session_dims" in plan, plan
    finally:
        await _cleanup(engine, seed.ids())
        async with engine.acquire() as conn:
            await conn.execute("DELETE FROM session_dims WHERE left(session_id, $2) = $1", bulk, len(bulk))
        await engine.close()


_INVOCATION_SQL = """
    INSERT INTO invocations_hourly (hour, session_id, kind, name, calls, success) VALUES ($1, $2, 2, $3, 1, 1)
"""


async def _name_some_sessions(engine: AnalyticsPgEngine, seed: _Seed) -> None:
    """Names from three sources: a skill key, a command, and a dispatch row an hour before the session start (inside
    the bound) and two hours before (outside it), so the names statement reads `started_at` of `sel`."""
    async with engine.acquire() as conn:
        await conn.execute(
            "UPDATE session_dims SET skills = $2::jsonb WHERE session_id = $1", seed.sid(7), json.dumps({"zeta": 1})
        )
        await conn.execute(
            "UPDATE session_dims SET commands = $2::jsonb WHERE session_id = $1",
            seed.sid(2), json.dumps(["/clear", ""]),
        )  # fmt: skip
        await conn.execute(_INVOCATION_SQL, seed.t0.replace(minute=0) - timedelta(hours=1), seed.sid(1), "inside")
        await conn.execute(_INVOCATION_SQL, seed.t0.replace(minute=0) - timedelta(hours=2), seed.sid(3), "outside")


@pytest.mark.asyncio
async def test_the_index_only_framework_request_returns_the_same_pages_totals_and_flags_as_the_full_projection(
    monkeypatch,
):
    engine = await _engine()
    seed = _Seed()
    try:
        await seed.insert(engine)
        await _name_some_sessions(engine, seed)
        reader = PostgresCliAnalyticsReader(engine)
        f = _unfiltered(seed)
        with patch(_PRICING, _lookup):
            rates = [model_rates_for(m) for m in await reader.get_cost_models(f)]

            async def pages():
                out = []
                for sort_by in _SORTS:
                    for accept in (lambda sets: sets, lambda sets: [n for n in sets if n], lambda sets: []):
                        for page, per_page in ((0, 1000), (1, 5), (50, 5)):
                            out.append(await reader.get_framework_page(f, sort_by, page, per_page, accept, rates))
                return out, await reader.get_window_model_flags(f)

            with monkeypatch.context() as m:
                _full_projection(m)
                full = await pages()
            index_only = await pages()

        assert index_only == full
        total, ids, flags = index_only[0][0]
        assert total >= 12 and set(seed.ids()) <= set(ids) and flags
        named = index_only[0][3][1]  # start_time, the named sets only (the second accept), page 0: the named sessions
        assert {seed.sid(1), seed.sid(2), seed.sid(7)} <= set(named) and seed.sid(3) not in set(named)
    finally:
        async with engine.acquire() as conn:
            await conn.execute("DELETE FROM invocations_hourly WHERE session_id = ANY($1::text[])", seed.ids())
        await _cleanup(engine, seed.ids())
        await engine.close()


@pytest.mark.asyncio
async def test_the_unfiltered_framework_names_and_window_flags_are_read_index_only_from_the_covering_index():
    engine = await _engine()
    seed = _Seed()
    bulk = f"test-page-fw-bulk-{seed.suffix}-"
    reader = PostgresCliAnalyticsReader(engine)
    names: list[tuple[str, dict]] = []
    flags: list[tuple[str, dict]] = []
    original_fetch, original_q = reader._fetch, reader._q

    async def record_fetch(conn, sql, params):
        if "CREATE TEMP TABLE" in sql:
            names.append((sql[sql.index("WITH") :], params))
        return await original_fetch(conn, sql, params)

    async def record_q(sql, params):
        if "bool_or(" in sql:
            flags.append((sql, params))
        return await original_q(sql, params)

    reader._fetch, reader._q = record_fetch, record_q  # type: ignore[method-assign]
    try:
        await seed.insert(engine)
        await reader.get_framework_page(_unfiltered(seed), "start_time", 0, 5, lambda sets: sets)
        async with engine.acquire() as conn:
            # Bulk-seed 50k shared sessions over 200 days (about 1% in the window) and mark their pages all-visible,
            # then ask the planner with no setting changed (a window of a few rows is planned by page-count ties).
            await conn.execute(
                "INSERT INTO session_dims (session_id, started_at) "
                "SELECT $1::text || g::text, $2::timestamptz - (g % 200) * interval '1 day' FROM generate_series(1, 50000) g",
                bulk, seed.t0,
            )  # fmt: skip
            # the admission rule reads the stored activity fact: a derived window is what the index serves
            await conn.execute(
                "UPDATE session_dims SET has_activity = true WHERE left(session_id, $2) = $1", bulk, len(bulk)
            )
            await conn.execute("VACUUM (ANALYZE) session_dims")
            assert len(names) == 1 and flags
            for sql, params in (names[0], flags[0]):
                text, args = positional(sql, params)
                plan = "\n".join(r[0] for r in await conn.fetch("EXPLAIN " + text, *args))
                assert "Index Only Scan using session_dims_active_shared_cov on session_dims" in plan, plan
    finally:
        await _cleanup(engine, seed.ids())
        async with engine.acquire() as conn:
            await conn.execute("DELETE FROM session_dims WHERE left(session_id, $2) = $1", bulk, len(bulk))
        await engine.close()
