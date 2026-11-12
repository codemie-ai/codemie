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

"""Live-Postgres proof of the stored read of the Sessions list (EPMCDME-16427, part 2): with the kill switch on, a
`ctx_per_call` page reads the stored key of every fresh session (`rates_version` set) and today's per-session
statement for the rest (S), merged by key DESC, session_id COLLATE "C"; the ids, the order and the total equal
today's statements (the switch off) for every search, filter, viewer scope, page and `sel` arm. The switch off, or
more than the cap of S, runs today's statements and logs the reason.

To show which value a page read, some tests overwrite the stored columns with a wrong value: a fresh session then
sorts by it (the stored path read it), a session of S never does (its key is computed live).

The control row is global, so every test restores it. Skipped when `TEST_LOCAL_PG_URL` is not set or unreachable.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import logging
import os
import re
import uuid
from pathlib import Path
from datetime import datetime, time, timedelta, timezone
from unittest.mock import patch

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres import migrations, stored_mode
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.maintenance import (
    MaintenanceReport,
    PartitionMaintainer,
    RetentionPolicy,
)
from codemie.repository.cli_analytics.postgres.rates_version import rates_version
from codemie.repository.cli_analytics.postgres.reader import (
    PostgresCliAnalyticsReader,
    _admitted_arms,
    positional,
    window_params,
)
from codemie.repository.cli_analytics.postgres.session_page_sql import ordered_page_sql
from codemie.repository.cli_analytics.postgres.session_activity import derive_session_activity
from codemie.repository.cli_analytics.postgres.session_sort_keys import (
    derive_session_sort_keys,
    update_session_sort_keys,
)
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.repository.cli_analytics.scope import ViewerScope
from codemie.service.analytics import cli_analytics_jobs
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from codemie.service.analytics.handlers.coding_agent_pricing import model_rates_for
from tests.codemie.repository.cli_analytics.support.scopes import DETAIL_SCOPE, EXCLUDE_SCOPE
from tests.codemie.service.analytics.handlers.test_coding_agent_pricing import _PARTIAL, _priced_model

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")

_MODEL = "claude-sonnet-4-5"
_NOW = datetime.now(timezone.utc).replace(microsecond=0)
_TODAY = _NOW.date()
# The window: noon three days ago to an hour from now. Two whole days inside it, two partial ones.
_START = datetime.combine(_TODAY - timedelta(days=3), time(12), tzinfo=timezone.utc)
_END = _NOW + timedelta(hours=1)
_OLD = _NOW - timedelta(days=10)

_DIMS_SQL = """
    INSERT INTO session_dims (session_id, started_at, last_event_at, repository, branch, jwt_email, first_prompt,
                              owner_email, subscription_type, project_origin, project_name)
    VALUES ($1, $2, $2, $3, 'main', $4, $5, $6, $7, $8, $9)"""
_COST_SQL = """
    INSERT INTO cost_daily
        (day, session_id, user_email, model_name, query_source, cost_usd, input_tokens, output_tokens,
         cache_read_tokens, cache_creation_tokens, api_call_count)
    VALUES ($1, $2, $3, $4, 'repl_main_thread', 0.1, 10, 5, $5, 0, $6)"""
_TOTALS_SQL = """
    INSERT INTO session_usage
        (session_id, day, model, speed, inference_geo, scope_kind, scope_name, agent_id,
         input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, attrs)
    VALUES ($1, $2, $3, '', '', '', '', 'ag1', 100, 20, $4, 0, '{"token_source": "subagent_usage"}'::jsonb)"""
_MODEL_COST_SQL = """
    INSERT INTO cost_daily
        (day, session_id, user_email, model_name, query_source, cost_usd, input_tokens, output_tokens,
         cache_read_tokens, cache_creation_tokens, cache_creation_1h_tokens, api_call_count)
    VALUES ($1, $2, $3, $4, 'repl_main_thread', 0, $5, 0, 0, $6, $7, 1)"""
_TURNS_SQL = "INSERT INTO turns_daily (day, session_id, turns) VALUES ($1, $2, 1)"
_RAW_SQL = "INSERT INTO usage_requests (session_id, ts) VALUES ($1, $2)"
_SET_ENABLED = "UPDATE session_sort_keys_control SET enabled = $1"
_READ_ENABLED = "SELECT coalesce(bool_and(enabled), false) FROM session_sort_keys_control"
_TABLES = ("session_dims", "cost_daily", "session_usage", "usage_requests", "turns_daily")


async def _engine() -> AnalyticsPgEngine:
    try:
        settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL))
        engine = AnalyticsPgEngine(settings)
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
    migrations.run_migrations(settings)
    maintainer = PartitionMaintainer(engine, RetentionPolicy.from_settings(settings), clock=lambda: _TODAY)
    async with engine.acquire() as conn:
        await maintainer._ensure_partitions(conn, _TODAY, MaintenanceReport())
    return engine


def _naive(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class _Session:
    def __init__(
        self,
        name,
        started,
        costs=(),
        totals=0,
        derive=True,
        repository="repo",
        prompt="",
        raw=None,
        models=(),
        turns=False,
    ):
        self.name, self.started, self.costs, self.totals, self.models = name, started, costs, totals, models
        self.turns = turns
        self.derive, self.repository, self.prompt, self.raw = derive, repository, prompt, raw
        self.owner, self.subscription, self.origin, self.project = None, "codemie", "profile", "proj-shared"


class _Seed:
    """Sessions of one user in every `sel` arm, with ctx ties, zero calls, totals-only, a search term, an unattributed
    repository, a Personal session of one owner and sessions of an administered project."""

    def __init__(self) -> None:
        self.suffix = uuid.uuid4().hex[:12]
        self.email = f"stored-{self.suffix}@example.com"
        self.owner = f"owner-{self.suffix}@example.com"
        self.project = f"proj-{self.suffix}"
        d2, d1 = _TODAY - timedelta(days=2), _TODAY - timedelta(days=1)
        edge_start = _START - timedelta(hours=1)  # the window's first UTC day, before the window opens
        self.sessions = [
            # arm 1: started in the window, activity derived
            _Session("a", _NOW - timedelta(hours=5), [(d2, 600, 6), (d1, 400, 4)]),  # 100
            _Session("b", _NOW - timedelta(hours=5), [(d1, 400, 4)]),  # 100, ties with a
            _Session("c", _NOW - timedelta(hours=4), [(d1, 77, 0)]),  # no calls: 0
            _Session("d", _NOW - timedelta(hours=4), totals=500),  # totals only: 0
            _Session("e", _NOW - timedelta(hours=3), [(d1, 90, 3)], totals=300),  # 130
            _Session("f", _NOW - timedelta(hours=3), [(d1, 50, 5)], repository="", prompt="the needle"),  # 10
            # arm 2: started in the window, activity not derived
            _Session("g", _NOW - timedelta(hours=2), [(d1, 100, 3)], derive=False),  # 33.333333333
            # arm 3: started outside, not derived, a row on a whole day inside
            _Session("h", _OLD, [(d2, 100, 7)], derive=False),  # 14.285714286
            # arm 4: started outside, derived, resumed into the window
            _Session("i", _OLD, [(_OLD.date(), 10, 1), (d2, 1, 1)]),  # 5.5
            # arm 5: started outside on the window's first day, derived, not resumed, raw record inside the window
            _Session("j", edge_start, [(edge_start.date(), 50, 5)], raw=_START + timedelta(hours=1)),  # 10
        ]
        personal = _Session("k", _NOW - timedelta(hours=1), [(d1, 20, 1)], prompt="needle mine")  # 20
        personal.owner, personal.subscription, personal.origin, personal.project = (
            self.owner,
            "personal",
            "personal",
            "",
        )
        project_a = _Session("l", _NOW - timedelta(hours=1), [(d1, 30, 1)])  # 30
        project_b = _Session("m", _NOW - timedelta(hours=1), [(d1, 30, 1)], repository="")  # 30, ties with l
        project_a.project = project_b.project = self.project
        self.sessions += [personal, project_a, project_b]

    def sid(self, name: str) -> str:
        return f"test-stored-{self.suffix}-{name}"

    def ids(self) -> list[str]:
        return [self.sid(s.name) for s in self.sessions]

    def filter(self, scope=EXCLUDE_SCOPE, **kwargs) -> LocalAnalyticsFilter:
        return LocalAnalyticsFilter(
            scope=scope, start_dt=_naive(_START), end_dt=_naive(_END), users=[self.email], **kwargs
        )

    def scopes(self) -> dict[str, ViewerScope]:
        return {
            "exclude": EXCLUDE_SCOPE,
            "detail": DETAIL_SCOPE,
            "own_only": ViewerScope(self.owner, False, frozenset(), "own_only"),
            "project_admin": ViewerScope("pa@x.com", False, frozenset({self.project}), "exclude"),
        }

    async def insert(self, engine: AnalyticsPgEngine) -> None:
        async with engine.acquire() as conn:
            for s in self.sessions:
                sid = self.sid(s.name)
                await conn.execute(
                    _DIMS_SQL, sid, s.started, s.repository, self.email, s.prompt, s.owner, s.subscription, s.origin,
                    s.project,
                )  # fmt: skip
                for day, cache_read, calls in s.costs:
                    await conn.execute(_COST_SQL, day, sid, self.email, _MODEL, cache_read, calls)
                for day, model, tokens, cache_write, cache_1h in s.models:
                    await conn.execute(_MODEL_COST_SQL, day, sid, self.email, model, tokens, cache_write, cache_1h)
                if s.totals:
                    await conn.execute(_TOTALS_SQL, sid, _TODAY - timedelta(days=1), _MODEL, s.totals)
                if s.raw:
                    await conn.execute(_RAW_SQL, sid, s.raw)
                if s.turns:
                    await conn.execute(_TURNS_SQL, s.started.date(), sid)
            derived = [self.sid(s.name) for s in self.sessions if s.derive]
            await derive_session_activity(conn, derived, 120)

    async def stamp(self, engine: AnalyticsPgEngine, names, rates=None, version="v-test") -> None:
        """Derive the stored columns of the named sessions with `rates` (stamped `version`); every other one of the
        seed is left without a stamp."""
        async with engine.acquire() as conn:
            await conn.execute(
                "UPDATE session_dims SET rates_version = NULL, cache_read_total = NULL, api_call_total = NULL "
                "WHERE session_id = ANY($1::text[])",
                self.ids(),
            )
            rates = rates if rates is not None else {_MODEL: model_rates_for(_MODEL)}
            await update_session_sort_keys(conn, [self.sid(n) for n in names], {}, rates, version)

    async def stamp_the_rest(self, engine: AnalyticsPgEngine, version: str) -> None:
        """Stamp `version` on every session outside the seed that has no stamp: S is read from the whole table, so the
        leftovers of other tests in the shared database would otherwise count toward it."""
        async with engine.acquire() as conn:
            await conn.execute(
                "UPDATE session_dims SET rates_version = $2 WHERE rates_version IS NULL AND session_id <> ALL($1::text[])",
                self.ids(),
                version,
            )

    async def corrupt(self, engine: AnalyticsPgEngine, name: str, cache_read: int, calls: int) -> None:
        """Overwrite the stored ctx columns of one session (its stamp is kept as it is)."""
        async with engine.acquire() as conn:
            await conn.execute(
                "UPDATE session_dims SET cache_read_total = $2, api_call_total = $3 WHERE session_id = $1",
                self.sid(name),
                cache_read,
                calls,
            )

    async def cleanup(self, engine: AnalyticsPgEngine) -> None:
        async with engine.acquire() as conn:
            for table in _TABLES:
                await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", self.ids())  # noqa: S608


@contextlib.asynccontextmanager
async def _switch(engine: AnalyticsPgEngine):
    """Yields a setter of the kill switch; restores the value found on entry."""
    async with engine.acquire() as conn:
        before = await conn.fetchval(_READ_ENABLED)

    async def set_enabled(value: bool) -> None:
        async with engine.acquire() as conn:
            await conn.execute(_SET_ENABLED, value)

    try:
        yield set_enabled
    finally:
        await set_enabled(bool(before))


@pytest.fixture(autouse=True)
def _fresh_warnings(caplog: pytest.LogCaptureFixture):
    """The platform logger does not propagate to the root, so caplog's handler is attached to the module's logger."""
    stored_mode.FALLBACK_WARNINGS.reset()
    stored_mode.logger.addHandler(caplog.handler)
    yield
    stored_mode.logger.removeHandler(caplog.handler)
    stored_mode.FALLBACK_WARNINGS.reset()


_PAGES = ((0, 1000), (0, 3), (1, 3), (4, 3), (10, 3))
_SEARCHES = (None, "needle", "NEEDLE", "no-such-term")


async def _pages(reader, f, **kwargs):
    return [await reader.get_session_page(f, "ctx_per_call", p, n, **kwargs) for p, n in _PAGES]


async def _compare_everything(engine, seed, set_enabled, reader=None) -> int:
    """Every search, unattributed flag, viewer scope and page: the stored read equals today's statements."""
    reader = reader or PostgresCliAnalyticsReader(engine)
    compared = 0
    for scope_name, scope in seed.scopes().items():
        for search in _SEARCHES:
            for unattributed in (False, True):
                f = seed.filter(scope)
                kwargs = {"search": search, "is_unattributed": unattributed}
                await set_enabled(False)
                expected = await _pages(reader, f, **kwargs)
                await set_enabled(True)
                got = await _pages(reader, f, **kwargs)
                assert got == expected, (scope_name, search, unattributed)
                compared += 1
    return compared


def _all_names(seed: _Seed) -> list[str]:
    return [s.name for s in seed.sessions]


@pytest.mark.asyncio
async def test_the_seed_reaches_every_sel_arm_and_orders_as_today():
    engine = await _engine()
    seed = _Seed()
    try:
        await seed.insert(engine)
        async with _switch(engine) as set_enabled:
            await set_enabled(False)
            total, ids = await PostgresCliAnalyticsReader(engine).get_session_page(
                seed.filter(), "ctx_per_call", 0, 1000
            )
        expected = [seed.sid(n) for n in ("e", "a", "b", "g", "l", "m", "h", "f", "j", "i", "c", "d")]
        assert (total, ids) == (len(expected), expected)  # k is Personal: not in the shared view
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.parametrize("fresh", ["all", "mixed", "none"])
@pytest.mark.asyncio
async def test_the_stored_read_equals_todays_statements(fresh):
    engine = await _engine()
    seed = _Seed()
    names = _all_names(seed)
    stamped = {"all": names, "mixed": names[::2], "none": []}[fresh]
    try:
        await seed.insert(engine)
        await seed.stamp(engine, stamped)
        async with _switch(engine) as set_enabled:
            assert await _compare_everything(engine, seed, set_enabled) == 4 * len(_SEARCHES) * 2
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_the_unfiltered_shared_list_reads_the_same_page_on_both_paths():
    engine = await _engine()
    seed = _Seed()
    try:
        await seed.insert(engine)
        await seed.stamp(engine, _all_names(seed)[::3])
        f = LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=_naive(_START), end_dt=_naive(_END))
        reader = PostgresCliAnalyticsReader(engine)
        async with _switch(engine) as set_enabled:
            await set_enabled(False)
            expected = await reader.get_session_page(f, "ctx_per_call", 0, 50)
            await set_enabled(True)
            assert await reader.get_session_page(f, "ctx_per_call", 0, 50) == expected
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_a_fresh_session_sorts_by_its_stored_key_and_a_session_of_s_by_its_live_key():
    engine = await _engine()
    seed = _Seed()
    try:
        await seed.insert(engine)
        await seed.stamp(engine, [n for n in _all_names(seed) if n != "i"])
        await seed.corrupt(engine, "c", 10_000, 1)  # fresh: the stored key 10000 is read, so c sorts first
        await seed.corrupt(engine, "i", 20_000, 1)  # in S (no stamp): its live key 5.5 is read
        reader = PostgresCliAnalyticsReader(engine)
        async with _switch(engine) as set_enabled:
            await set_enabled(True)
            total, ids = await reader.get_session_page(seed.filter(), "ctx_per_call", 0, 1000)
            await set_enabled(False)
            today = await reader.get_session_page(seed.filter(), "ctx_per_call", 0, 1000)
        assert ids[0] == seed.sid("c") and total == 12
        assert ids.index(seed.sid("i")) == ids.index(seed.sid("j")) + 1  # 5.5, right after j's 10
        assert today[1][0] == seed.sid("e")  # today's statements never read the stored columns
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_the_switch_off_runs_todays_statements_and_logs_the_reason(caplog):
    engine = await _engine()
    seed = _Seed()
    try:
        await seed.insert(engine)
        await seed.stamp(engine, _all_names(seed))
        await seed.corrupt(engine, "c", 10_000, 1)
        reader = PostgresCliAnalyticsReader(engine)
        async with _switch(engine) as set_enabled:
            await set_enabled(False)
            with caplog.at_level(logging.WARNING):
                _, ids = await reader.get_session_page(seed.filter(), "ctx_per_call", 0, 1000)
        assert ids[0] == seed.sid("e")
        assert any("disabled" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_more_sessions_in_s_than_the_cap_run_todays_statements_and_log_the_reason(caplog):
    engine = await _engine()
    seed = _Seed()
    try:
        await seed.insert(engine)
        await seed.stamp(engine, [n for n in _all_names(seed) if n not in ("h", "i")])  # |S| = 2
        await seed.corrupt(engine, "c", 10_000, 1)
        capped = PostgresCliAnalyticsReader(engine, s_cap=1)
        async with _switch(engine) as set_enabled:
            await set_enabled(True)
            with caplog.at_level(logging.WARNING):
                _, over = await capped.get_session_page(seed.filter(), "ctx_per_call", 0, 1000)
            _, within = await PostgresCliAnalyticsReader(engine, s_cap=2).get_session_page(
                seed.filter(), "ctx_per_call", 0, 1000
            )
        assert over[0] == seed.sid("e")  # today's statements: the corrupt stored key is not read
        assert within[0] == seed.sid("c")  # within the cap the stored path is taken
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("s_over_cap" in m and "|S|>1" in m for m in warnings)
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_the_fallback_warning_is_rate_limited(caplog):
    engine = await _engine()
    seed = _Seed()
    try:
        await seed.insert(engine)
        reader = PostgresCliAnalyticsReader(engine)
        async with _switch(engine) as set_enabled:
            await set_enabled(False)
            with caplog.at_level(logging.WARNING):
                for _ in range(3):
                    await reader.get_session_page(seed.filter(), "ctx_per_call", 0, 10)
        assert sum("disabled" in r.getMessage() for r in caplog.records) == 1
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_a_scope_the_stored_path_cannot_express_takes_todays_statements():
    engine = await _engine()
    seed = _Seed()
    try:
        await seed.insert(engine)
        await seed.stamp(engine, _all_names(seed))
        reader = PostgresCliAnalyticsReader(engine)
        async with _switch(engine) as set_enabled:
            await set_enabled(True)
            future = ViewerScope("a@x.com", True, frozenset(), "a-future-mode")  # type: ignore[arg-type]
            mode = await _mode_of(engine, reader, seed.filter(scope=future))
            _, ids = await reader.get_session_page(seed.filter(scope=future), "ctx_per_call", 0, 1000)
        assert mode == stored_mode.StoredMode(False, "unexpressible")
        assert ids == []  # today's statements: an unknown mode allows nothing
    finally:
        await seed.cleanup(engine)
        await engine.close()


async def _mode_of(engine, reader, f):
    async with engine.acquire() as conn:
        return await reader._stored_mode(conn, f)


@pytest.mark.asyncio
async def test_a_stored_read_holds_one_pool_connection():
    engine = await _engine()
    seed = _Seed()
    held = peak = 0
    acquire = engine.acquire

    @contextlib.asynccontextmanager
    async def counting(*args, **kwargs):
        nonlocal held, peak
        async with acquire(*args, **kwargs) as conn:
            held += 1
            peak = max(peak, held)
            try:
                yield conn
            finally:
                held -= 1

    try:
        await seed.insert(engine)
        await seed.stamp(engine, _all_names(seed)[::2])
        async with _switch(engine) as set_enabled:
            await set_enabled(True)
            engine.acquire = counting  # type: ignore[method-assign]
            try:
                async with PostgresCliAnalyticsReader(engine).bounded() as reader:
                    await reader.get_session_page(seed.filter(), "ctx_per_call", 1, 3, search="needle")
            finally:
                engine.acquire = acquire  # type: ignore[method-assign]
        assert 1 <= peak <= 2
    finally:
        await seed.cleanup(engine)
        await engine.close()


# ── the cost_usd sort ──

_PRICING = "codemie.service.analytics.handlers.coding_agent_pricing.llm_service"
# The seed's model, a cheap one, one so cheap that a million tokens cost 1e-10 (a tie under the key precision with
# the cheap one) and one without cache prices; "mystery-model" has no catalog entry.
_CATALOG = [
    _priced_model(_MODEL, 3e-6, 15e-6, 0.3e-6, 3.75e-6, 6e-6),
    _priced_model("cheap-model", 1e-6, 5e-6, 0.1e-6, 1.25e-6, 2e-6),
    _priced_model("tiny-model", 1e-16, 1e-16, 1e-16, 1e-16, 1e-16),
    _PARTIAL,
]
_COST_MODELS = (_MODEL, "cheap-model", "tiny-model", "partial-model", "mystery-model")


class _CostSeed(_Seed):
    """`_Seed` plus sessions of several models: a tie closer than 5e-10, a key-0 session of an uncatalogued model,
    a session without cost rows and one that used a kind its model has no price for."""

    def __init__(self) -> None:
        super().__init__()
        d1, at = _TODAY - timedelta(days=1), _NOW - timedelta(minutes=30)
        self.sessions += [
            _Session("n", at, models=[(d1, "cheap-model", 1_000_000, 0, None)]),  # 1.0
            _Session(
                "o", at, models=[(d1, "cheap-model", 1_000_000, 0, None), (d1, "tiny-model", 1_000_000, 0, None)]
            ),  # 1.0000000001: ties with n
            _Session("p", at, models=[(d1, "mystery-model", 500, 0, None)]),  # 0, unpriced
            _Session("q", at, turns=True),  # no cost row (in the window by its turns): 0
            _Session("r", at, models=[(d1, "partial-model", 100, 1_000, 400)]),  # cache prices missing
        ]


def _cost_rates() -> dict:
    return {m: model_rates_for(m) for m in _COST_MODELS}


@contextlib.contextmanager
def _catalog():
    with (
        patch(f"{_PRICING}.get_all_llm_model_info", return_value=_CATALOG),
        patch(f"{_PRICING}.catalog_source", return_value="litellm"),
    ):
        yield


def _cost_reader(engine, version: str | None, **kwargs) -> PostgresCliAnalyticsReader:
    reader = PostgresCliAnalyticsReader(engine, **kwargs)
    if version is not None:
        reader.set_rates(lambda: version)
    return reader


async def _cost_pages(reader, f, search=None, is_unattributed=False):
    rates = [model_rates_for(m) for m in await reader.get_cost_models(f)]
    return [await reader.get_cost_page(f, p, n, rates, search, is_unattributed) for p, n in _PAGES]


async def _session_lists(handler, f, search=None, is_unattributed=False):
    out = []
    for p, n in _PAGES:
        body, pricer, as_of = await handler.get_sessions(f, p, n, "cost_usd", search, is_unattributed=is_unattributed)
        out.append((body, pricer.unpriced, pricer.catalog_source, as_of))
    return out


async def _stamp_every_session(engine, rates, version) -> None:
    """Derive the stored columns of every session of the database (the unfiltered window reads them all)."""
    async with engine.acquire() as conn:
        ids = [r["session_id"] for r in await conn.fetch("SELECT session_id FROM session_dims")]
        await update_session_sort_keys(conn, ids, {}, rates, version)


@pytest.mark.parametrize("unstamped", [0, 1])
@pytest.mark.asyncio
async def test_the_unfiltered_stored_cost_read_carries_its_columns_in_sel_and_equals_todays_statements(unstamped):
    # No user filter: `sel` is the index-only form and carries cost_key, models_used and kind_masks itself.
    engine = await _engine()
    seed = _CostSeed()
    try:
        await seed.insert(engine)
        with _catalog():
            rates = _cost_rates()
            version = rates_version(rates)
            await _stamp_every_session(engine, rates, version)
            if unstamped:
                async with engine.acquire() as conn:
                    await conn.execute(
                        "UPDATE session_dims SET rates_version = NULL WHERE session_id = $1", seed.sid("d")
                    )
            f = LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=_naive(_START), end_dt=_naive(_END))
            reader = _cost_reader(engine, version)
            async with _switch(engine) as set_enabled:
                await set_enabled(False)
                today = (
                    await reader.get_cost_models(f),
                    await _cost_pages(reader, f),
                    await reader.get_window_model_flags(f),
                )
                await set_enabled(True)
                stored = (
                    await reader.get_cost_models(f),
                    await _cost_pages(reader, f),
                    await reader.get_window_model_flags(f),
                )
        assert stored == today and today[0]
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.parametrize("fresh", ["all", "mixed", "other_version", "none"])
@pytest.mark.asyncio
async def test_the_stored_cost_read_equals_todays_statements(fresh):
    engine = await _engine()
    seed = _CostSeed()
    names = _all_names(seed)
    try:
        await seed.insert(engine)
        with _catalog():
            rates = _cost_rates()
            version = rates_version(rates)
            if fresh == "other_version":  # every key priced by another rate set: all of the window is in S
                await seed.stamp(engine, names, rates, "v-other")
            else:
                await seed.stamp(engine, {"all": names, "mixed": names[::2], "none": []}[fresh], rates, version)
            reader = _cost_reader(engine, version)
            handler = LocalAnalyticsHandler(reader)
            async with _switch(engine) as set_enabled:
                compared = 0
                for scope_name, scope in seed.scopes().items():
                    for search in _SEARCHES:
                        for unattributed in (False, True):
                            case = (scope_name, search, unattributed)
                            await set_enabled(False)
                            expected = await _cost_pages(reader, seed.filter(scope), search, unattributed)
                            expected_lists = await _session_lists(handler, seed.filter(scope), search, unattributed)
                            await set_enabled(True)
                            assert await _cost_pages(reader, seed.filter(scope), search, unattributed) == expected, case
                            got_lists = await _session_lists(handler, seed.filter(scope), search, unattributed)
                            assert got_lists == expected_lists, case
                            compared += 1
                assert compared == 4 * len(_SEARCHES) * 2
                async with engine.acquire() as conn:
                    mode = await reader._stored_mode(conn, seed.filter(), "cost_usd")
        assert mode.stored
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_the_cost_order_breaks_a_tie_under_the_key_precision_by_session_id_and_lists_every_key_0_session():
    engine = await _engine()
    seed = _CostSeed()
    try:
        await seed.insert(engine)
        with _catalog():
            rates = _cost_rates()
            version = rates_version(rates)
            await seed.stamp(engine, _all_names(seed), rates, version)
            async with _switch(engine) as set_enabled:
                await set_enabled(True)
                body, pricer, _ = await LocalAnalyticsHandler(_cost_reader(engine, version)).get_sessions(
                    seed.filter(), 0, 1000, "cost_usd", None
                )
                total, ids, _ = (await _cost_pages(_cost_reader(engine, version), seed.filter()))[0]
        assert [s["trace_id"] for s in body["sessions"]] == ids
        assert ids.index(seed.sid("o")) == ids.index(seed.sid("n")) + 1  # equal keys: session_id ascending
        assert set(ids[-2:]) == {seed.sid("p"), seed.sid("q")}  # key 0: no priced token, no cost row
        assert pricer.unpriced == ["mystery-model", "partial-model"] and pricer.catalog_source == "litellm"
        assert body["total"] == total == 17
    finally:
        await seed.cleanup(engine)
        await engine.close()


async def _set_cost(engine, seed, name, cost_key, version=None):
    async with engine.acquire() as conn:
        await conn.execute(
            "UPDATE session_dims SET cost_key = $2, rates_version = coalesce($3, rates_version) WHERE session_id = $1",
            seed.sid(name),
            cost_key,
            version,
        )


@pytest.mark.asyncio
async def test_a_fresh_cost_key_is_read_and_a_key_of_another_rate_set_never():
    engine = await _engine()
    seed = _CostSeed()
    try:
        await seed.insert(engine)
        with _catalog():
            rates = _cost_rates()
            version = rates_version(rates)
            await seed.stamp(engine, _all_names(seed), rates, version)
            await _set_cost(engine, seed, "c", 1_000_000, "v-old")  # another rate set: in S, its live key is read
            await _set_cost(engine, seed, "d", 500_000)  # fresh: the stored key is read
            reader = _cost_reader(engine, version)
            async with _switch(engine) as set_enabled:
                await set_enabled(False)
                _, today, _ = (await _cost_pages(reader, seed.filter()))[0]
                await set_enabled(True)
                _, got, _ = (await _cost_pages(reader, seed.filter()))[0]
        assert got == [seed.sid("d"), *[sid for sid in today if sid != seed.sid("d")]]
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_after_a_rate_change_no_key_of_the_old_rate_set_is_used(caplog):
    engine = await _engine()
    seed = _CostSeed()
    try:
        await seed.insert(engine)
        with _catalog():
            rates = _cost_rates()
            await seed.stamp(engine, _all_names(seed), rates, rates_version(rates))
            await _set_cost(engine, seed, "d", 500_000)
            reader = _cost_reader(engine, "v-after-the-change")
            async with _switch(engine) as set_enabled:
                await set_enabled(False)
                expected = await _cost_pages(reader, seed.filter())
                await set_enabled(True)
                with caplog.at_level(logging.WARNING):
                    got = await _cost_pages(reader, seed.filter())
                async with engine.acquire() as conn:
                    mode = await reader._stored_mode(conn, seed.filter(), "cost_usd")
        assert got == expected and expected[0][1][0] != seed.sid("d")
        # every session was priced by the old rate set; S is the table's, so it may hold other tests' sessions too
        assert mode.stored and len(mode.stale) >= expected[0][0]
        assert any("s_merged" in r.getMessage() for r in caplog.records)
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_a_reader_without_a_rates_version_runs_todays_cost_statements_and_logs_the_reason(caplog):
    engine = await _engine()
    seed = _CostSeed()
    try:
        await seed.insert(engine)
        with _catalog():
            rates = _cost_rates()
            await seed.stamp(engine, _all_names(seed), rates, rates_version(rates))
            await _set_cost(engine, seed, "d", 500_000)
            reader = _cost_reader(engine, None)
            async with _switch(engine) as set_enabled:
                await set_enabled(True)
                with caplog.at_level(logging.WARNING):
                    got = await _cost_pages(reader, seed.filter())
                await set_enabled(False)
                expected = await _cost_pages(reader, seed.filter())
        assert got == expected and got[0][1][0] != seed.sid("d")
        assert any("no_rates_version" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_more_cost_sessions_in_s_than_the_cap_run_todays_statements_and_log_the_reason(caplog):
    engine = await _engine()
    seed = _CostSeed()
    try:
        await seed.insert(engine)
        with _catalog():
            rates = _cost_rates()
            version = rates_version(rates)
            await seed.stamp(engine, _all_names(seed), rates, version)
            await _set_cost(engine, seed, "h", 0, "v-old")  # |S| = 2: an old stamp ...
            async with engine.acquire() as conn:  # ... and no stamp
                await conn.execute("UPDATE session_dims SET rates_version = NULL WHERE session_id = $1", seed.sid("i"))
            await _set_cost(engine, seed, "d", 500_000)
            async with _switch(engine) as set_enabled:
                await set_enabled(True)
                with caplog.at_level(logging.WARNING):
                    over = await _cost_pages(_cost_reader(engine, version, s_cap=1), seed.filter())
                within = await _cost_pages(_cost_reader(engine, version, s_cap=2), seed.filter())
        assert over[0][1][0] != seed.sid("d")  # today's statements: the corrupt stored key is not read
        assert within[0][1][0] == seed.sid("d")
        assert any("s_over_cap" in r.getMessage() and "|S|>1" in r.getMessage() for r in caplog.records)
    finally:
        await seed.cleanup(engine)
        await engine.close()


_ALL_KINDS = 31  # every priced kind used


@pytest.mark.asyncio
async def test_the_window_flags_read_the_stored_kind_masks_of_fresh_sessions_and_live_groups_of_s():
    engine = await _engine()
    seed = _CostSeed()
    try:
        await seed.insert(engine)
        with _catalog():
            rates = _cost_rates()
            version = rates_version(rates)
            await seed.stamp(engine, [n for n in _all_names(seed) if n != "o"], rates, version)
            async with engine.acquire() as conn:  # n is fresh, o (no stamp) is in S: only n's masks are read
                await conn.execute(
                    "UPDATE session_dims SET kind_masks = ARRAY[$2::int] WHERE session_id = $1",
                    seed.sid("n"),
                    _ALL_KINDS,
                )
                await conn.execute(
                    "UPDATE session_dims SET kind_masks = ARRAY[$2::int, $2::int] WHERE session_id = $1",
                    seed.sid("o"),
                    _ALL_KINDS,
                )
            reader = _cost_reader(engine, version)
            async with _switch(engine) as set_enabled:
                await set_enabled(False)
                today_window = await reader.get_window_model_flags(seed.filter())
                today_page = (await _cost_pages(reader, seed.filter()))[0][2]
                await set_enabled(True)
                window = await reader.get_window_model_flags(seed.filter())
                page = (await _cost_pages(reader, seed.filter()))[0][2]
        expected = [(m, (True,) * 5 if m == "cheap-model" else used) for m, used in today_window]
        assert today_page == today_window
        assert window == expected and page == expected
        assert dict(today_window)["tiny-model"] == (True, False, False, False, False)  # o's live group
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.parametrize("fresh", ["all", "mixed", "none"])
@pytest.mark.asyncio
async def test_the_stored_window_flags_equal_todays(fresh):
    engine = await _engine()
    seed = _CostSeed()
    names = _all_names(seed)
    try:
        await seed.insert(engine)
        with _catalog():
            rates = _cost_rates()
            await seed.stamp(engine, {"all": names, "mixed": names[1::2], "none": []}[fresh], rates, "v-any")
            reader = _cost_reader(engine, "v-current")  # the masks do not depend on the rate set
            async with _switch(engine) as set_enabled:
                for scope in seed.scopes().values():
                    await set_enabled(False)
                    expected = await reader.get_window_model_flags(seed.filter(scope))
                    await set_enabled(True)
                    assert await reader.get_window_model_flags(seed.filter(scope)) == expected
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_keys_derived_with_the_production_rates_order_an_alias_model_as_todays_statements():
    """The refresher's and the backfill's rates callable resolves each raw name as a request does: a session of a raw
    model only the alias resolution prices ("us.anthropic." + the catalog base name) sorts as today's statement."""
    engine = await _engine()
    seed = _CostSeed()
    alias = "us.anthropic." + _MODEL
    seed.sessions.append(
        _Session("s", _NOW - timedelta(minutes=20), models=[(_TODAY - timedelta(days=1), alias, 2_000_000, 0, None)])
    )
    try:
        await seed.insert(engine)
        with _catalog():
            version = cli_analytics_jobs.catalog_rates_version()
            async with engine.acquire() as conn:
                await derive_session_sort_keys(conn, seed.ids(), {}, cli_analytics_jobs.model_rates_by_name, version)
            reader = _cost_reader(engine, version)
            handler = LocalAnalyticsHandler(reader)
            async with _switch(engine) as set_enabled:
                await set_enabled(False)
                expected = await _cost_pages(reader, seed.filter())
                expected_lists = await _session_lists(handler, seed.filter())
                await set_enabled(True)
                got = await _cost_pages(reader, seed.filter())
                got_lists = await _session_lists(handler, seed.filter())
                async with engine.acquire() as conn:
                    mode = await reader._stored_mode(conn, seed.filter(), "cost_usd")
        assert mode.stored and seed.sid("s") not in mode.stale  # the alias session's key was read from session_dims
        assert got == expected and got_lists == expected_lists
        assert expected[0][1][0] == seed.sid("s")  # 2M input tokens at the seed model's rate: the dearest session
    finally:
        await seed.cleanup(engine)
        await engine.close()


# ── the framework filter ──

# Classifier names per session (the keys of `skills`); a, e, g, p have a name the rule matches in the middle of it.
_NAMES = {
    "a": ["my-spec-kit"], "b": ["spec"], "d": ["other"], "e": ["x-spec-y", "other"], "g": ["spec"],
    "h": ["zzz"], "k": ["spec"], "l": ["spec-kit"], "n": ["spec"], "o": ["other"], "p": ["my-spec-kit"],
    "r": ["spec"],
}  # fmt: skip
_RULE = re.compile(r"spec")  # a regex rule, not a name prefix
_FRAMEWORK_PAGES = ((0, 1000), (0, 3), (1, 3), (5, 3))


def _matching(sets):
    return [names for names in sets if any(_RULE.search(n) for n in names)]


async def _name_sessions(engine, seed) -> None:
    async with engine.acquire() as conn:
        for name, names in _NAMES.items():
            await conn.execute(
                "UPDATE session_dims SET skills = $2::jsonb WHERE session_id = $1",
                seed.sid(name),
                json.dumps({n: {} for n in names}),
            )


async def _framework_pages(reader, f, sort_by, search=None, is_unattributed=False):
    rates = [model_rates_for(m) for m in await reader.get_cost_models(f)] if sort_by == "cost_usd" else None
    return [
        await reader.get_framework_page(f, sort_by, p, n, _matching, rates, search, is_unattributed)
        for p, n in _FRAMEWORK_PAGES
    ]


async def _framework_seed(engine, fresh: str):
    seed = _CostSeed()
    await seed.insert(engine)
    await _name_sessions(engine, seed)
    rates = _cost_rates()
    version = rates_version(rates)
    names = _all_names(seed)
    stamped = {"all": names, "mixed": names[::2], "none": []}[fresh]
    async with engine.acquire() as conn:
        await conn.execute(
            "UPDATE session_dims SET rates_version = NULL WHERE session_id = ANY($1::text[])", seed.ids()
        )
        classifier_names = {seed.sid(n): v for n, v in _NAMES.items()}
        await update_session_sort_keys(conn, [seed.sid(n) for n in stamped], classifier_names, rates, version)
    return seed, version


@pytest.mark.parametrize("sort_by", ["start_time", "ctx_per_call", "cost_usd"])
@pytest.mark.parametrize("fresh", ["all", "mixed", "none"])
@pytest.mark.asyncio
async def test_the_stored_framework_read_equals_todays_statements(fresh, sort_by):
    engine = await _engine()
    seed = None
    try:
        with _catalog():
            seed, version = await _framework_seed(engine, fresh)
            reader = _cost_reader(engine, version)
            async with _switch(engine) as set_enabled:
                compared = 0
                unsearched_totals = []  # the first page's total of every view without a search or the unattributed filter
                for scope_name, scope in seed.scopes().items():
                    for search in _SEARCHES:
                        for unattributed in (False, True):
                            f = seed.filter(scope)
                            await set_enabled(False)
                            expected = await _framework_pages(reader, f, sort_by, search, unattributed)
                            await set_enabled(True)
                            got = await _framework_pages(reader, f, sort_by, search, unattributed)
                            assert got == expected, (scope_name, search, unattributed)
                            if search is None and not unattributed:
                                unsearched_totals.append(expected[0][0])
                            compared += 1
                assert compared == 4 * len(_SEARCHES) * 2
                # equality of two empty results proves nothing: some unsearched view must show sessions
                assert any(total > 0 for total in unsearched_totals), unsearched_totals
    finally:
        if seed:
            await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_a_fresh_session_is_classified_by_its_stored_names_and_a_session_of_s_by_its_live_names():
    engine = await _engine()
    seed = None
    try:
        with _catalog():
            seed, version = await _framework_seed(engine, "all")
            async with engine.acquire() as conn:
                await conn.execute(  # fresh: the stored names are read, the rule accepts them
                    "UPDATE session_dims SET classifier_names = ARRAY['stored-spec'] WHERE session_id = $1",
                    seed.sid("c"),
                )
                await conn.execute(  # in S: the stored names are never read
                    "UPDATE session_dims SET classifier_names = ARRAY['stored-spec'], rates_version = NULL "
                    "WHERE session_id = $1",
                    seed.sid("f"),
                )
                await conn.execute(  # in S: its live names are read
                    "UPDATE session_dims SET rates_version = NULL WHERE session_id = $1", seed.sid("a")
                )
            reader = _cost_reader(engine, version)
            async with _switch(engine) as set_enabled:
                await set_enabled(True)
                total, ids, _ = (await _framework_pages(reader, seed.filter(), "start_time"))[0]
    finally:
        if seed:
            await seed.cleanup(engine)
        await engine.close()
    assert seed.sid("c") in ids  # a nameless session whose stored names the rule accepts
    assert seed.sid("f") not in ids  # no live name
    assert seed.sid("a") in ids
    assert total == len(ids)


@pytest.mark.asyncio
async def test_the_framework_request_reports_its_group_count(caplog):
    engine = await _engine()
    seed = None
    try:
        with _catalog():
            seed, version = await _framework_seed(engine, "mixed")
            reader = _cost_reader(engine, version)
            async with _switch(engine) as set_enabled:
                await set_enabled(True)
                with caplog.at_level(logging.INFO, logger=stored_mode.logger.name):
                    await _framework_pages(reader, seed.filter(), "ctx_per_call")
    finally:
        if seed:
            await seed.cleanup(engine)
        await engine.close()
    assert any("framework groups=" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_the_framework_filter_falls_back_when_s_is_over_the_cap_and_when_the_switch_is_off(caplog):
    engine = await _engine()
    seed = None
    try:
        with _catalog():
            seed, version = await _framework_seed(engine, "none")
            async with engine.acquire() as conn:
                await conn.execute(
                    "UPDATE session_dims SET classifier_names = ARRAY['stored-spec'] WHERE session_id = $1",
                    seed.sid("c"),
                )
            async with _switch(engine) as set_enabled:
                await set_enabled(True)
                with caplog.at_level(logging.WARNING):
                    over = await _framework_pages(_cost_reader(engine, version, s_cap=1), seed.filter(), "start_time")
                await set_enabled(False)
                off = await _framework_pages(_cost_reader(engine, version), seed.filter(), "start_time")
    finally:
        if seed:
            await seed.cleanup(engine)
        await engine.close()
    assert over == off
    assert any("s_over_cap" in r.getMessage() for r in caplog.records)


# ── the indexes of revision a1c1a0000012 (EPMCDME-16427, part 2, task 8) ──

_IDX_PREFIX = "idx8-"
_IDX_ROWS = 40_000
_SORT_INDEXES = ("session_dims_fw_win",)


def _sort_indexes_revision():
    path = Path(migrations.ALEMBIC_DIR) / "versions" / "a1c1a0000012_cli_analytics_session_sort_keys.py"
    spec = importlib.util.spec_from_file_location("sort_indexes_live", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _run_statements(conn, statements) -> None:
    for statement in statements:
        await conn.execute(statement.replace("SET LOCAL", "SET"))
    await conn.execute("RESET lock_timeout")


async def _index_state(conn) -> dict[str, bool]:
    rows = await conn.fetch(
        "SELECT c.relname, i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
        "WHERE c.relname = ANY($1) AND pg_table_is_visible(c.oid)",
        list(_SORT_INDEXES),
    )
    return {r["relname"]: r["indisvalid"] for r in rows}


@pytest.mark.asyncio
async def test_the_sort_indexes_are_built_idempotently_rebuilt_when_invalid_and_dropped_by_the_downgrade():
    engine = await _engine()
    revision = _sort_indexes_revision()
    try:
        async with engine.acquire() as conn:
            assert await _index_state(conn) == {name: True for name in _SORT_INDEXES}  # alembic built them
            oids = await conn.fetch("SELECT oid FROM pg_class WHERE relname = ANY($1)", list(_SORT_INDEXES))

            await _run_statements(conn, revision.index_statements())  # idempotent: nothing is rebuilt
            assert await conn.fetch("SELECT oid FROM pg_class WHERE relname = ANY($1)", list(_SORT_INDEXES)) == oids
            defs = {r["indexname"]: r["indexdef"] for r in await conn.fetch(
                "SELECT indexname, indexdef FROM pg_indexes WHERE indexname = ANY($1)", list(_SORT_INDEXES)
            )}  # fmt: skip
            assert all("WHERE (has_activity AND " in d for d in defs.values()), defs  # partial

            await conn.execute(  # a failed CONCURRENTLY pre-build leaves an invalid index of that name
                "UPDATE pg_index SET indisvalid = false WHERE indexrelid = 'session_dims_fw_win'::regclass"
            )
            assert (await _index_state(conn))["session_dims_fw_win"] is False
            await _run_statements(conn, revision.index_statements())
            assert await _index_state(conn) == {name: True for name in _SORT_INDEXES}

            await _run_statements(conn, [f"DROP INDEX IF EXISTS {name}" for name, _ in reversed(revision.INDEXES)])
            assert await _index_state(conn) == {}
            assert await conn.fetchval(
                "SELECT count(*) FROM pg_indexes WHERE indexname = 'session_dims_active_shared_cov'"
            )
    finally:
        async with engine.acquire() as conn:
            await _run_statements(conn, revision.index_statements())  # leave the database at the head
        await engine.close()


def _plan_nodes(plan: dict) -> list[dict]:
    nodes, todo = [], [plan]
    while todo:
        node = todo.pop()
        nodes.append(node)
        todo.extend(node.get("Plans", []))
    return nodes


async def _explain(conn, sql: str, *args) -> list[dict]:
    raw = await conn.fetchval("EXPLAIN (ANALYZE, FORMAT JSON, COSTS OFF, TIMING OFF) " + sql, *args)
    return _plan_nodes(json.loads(raw)[0]["Plan"])


def _scans(nodes: list[dict], relation: str = "session_dims") -> list[tuple[str, str | None]]:
    return [(n["Node Type"], n.get("Index Name")) for n in nodes if n.get("Relation Name") == relation]


@contextlib.asynccontextmanager
async def _indexed_window(engine):
    """40k sessions over 120 days (~1/4 in a 30-day window, 5% Personal, 0.1% underived, 0.2% resumed), all stamped
    `v1`, vacuumed and analysed so the planner sees the index-only fraction; removed afterwards."""
    async with engine.acquire() as conn:
        await conn.execute("DELETE FROM session_dims WHERE session_id LIKE $1", _IDX_PREFIX + "%")
        await conn.execute(
            """
            INSERT INTO session_dims (session_id, started_at, last_event_at, repository, branch, jwt_email,
                subscription_type, project_origin, project_name, has_activity, last_activity_day, resumed,
                cache_read_total, api_call_total, classifier_names, models_used, kind_masks, cost_key, rates_version)
            SELECT $1 || g, ts, ts, 'repo', 'main', 'u@x.com',
                CASE WHEN g % 20 = 0 THEN 'personal' ELSE 'codemie' END,
                CASE WHEN g % 20 = 0 THEN 'personal' ELSE 'profile' END, 'p',
                CASE WHEN g % 1000 = 1 THEN NULL ELSE true END,
                CASE WHEN g % 1000 = 1 THEN NULL ELSE ts::date + (CASE WHEN g % 500 = 2 THEN 3 ELSE 0 END) END,
                CASE WHEN g % 1000 = 1 THEN NULL ELSE g % 500 = 2 END,
                (random() * 1e6)::bigint, (random() * 500)::bigint, ARRAY['n' || (g % 7)], ARRAY['m'], ARRAY[3],
                round((random() * 50)::numeric, 9), 'v1'
            FROM (SELECT g, now() - make_interval(secs => random() * 120 * 86400) AS ts
                  FROM generate_series(1, $2) g) t""",
            _IDX_PREFIX,
            _IDX_ROWS,
        )
        await conn.execute("VACUUM (ANALYZE, DISABLE_PAGE_SKIPPING) session_dims")
    try:
        yield
    finally:
        async with engine.acquire() as conn:
            await conn.execute("DELETE FROM session_dims WHERE session_id LIKE $1", _IDX_PREFIX + "%")
            await conn.execute("VACUUM session_dims")


def _window_sql(days: int = 30) -> str:
    shared = _sort_indexes_revision().SHARED_PREDICATE
    return (
        f"has_activity AND {shared} AND started_at BETWEEN now() - interval '{days} days' "
        "AND now() + interval '1 hour'"
    )


@pytest.mark.asyncio
async def test_the_framework_window_scans_are_index_only_on_the_window_index(record_property):
    engine = await _engine()
    try:
        async with _indexed_window(engine), engine.acquire() as conn:
            where = _window_sql()
            names = await _explain(
                conn, f"SELECT session_id, classifier_names, rates_version FROM session_dims WHERE {where}"
            )
            groups = await _explain(
                conn,
                f"SELECT classifier_names, count(*) FROM session_dims WHERE {where} AND rates_version IS NOT NULL "
                "GROUP BY 1",
            )
        assert _scans(names) == [("Index Only Scan", "session_dims_fw_win")], names
        assert _scans(groups) == [("Index Only Scan", "session_dims_fw_win")], groups
        fetches = {k: [n["Heap Fetches"] for n in v if "Heap Fetches" in n] for k, v in
                   {"fw_win": names, "fw_groups": groups}.items()}  # fmt: skip
        record_property("window_scan_heap_fetches", fetches)
        # Index-only: a few heap fetches remain when rows written by earlier tests of the run are not yet all-visible
        # (an older snapshot in the shared database holds the horizon back, whatever the VACUUM); a heap scan would
        # fetch every row of the window (about a quarter of _IDX_ROWS).
        assert all(len(f) == 1 and f[0] <= _IDX_ROWS // 100 for f in fetches.values()), fetches
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_no_arm_of_sel_scans_session_dims_sequentially(record_property):
    engine = await _engine()
    try:
        f = LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=_naive(_NOW - timedelta(days=30)), end_dt=_naive(_END))
        params = window_params(f)
        async with _indexed_window(engine), engine.acquire() as conn:
            for number, arm in enumerate(_admitted_arms("x", "x.session_id", None), start=1):
                text, args = positional(arm, params)
                nodes = await _explain(conn, text, *args)
                scans = _scans(nodes)
                record_property(f"sel_arm_{number}", scans)
                assert scans and all(kind != "Seq Scan" for kind, _ in scans), (number, scans)
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_the_stored_ctx_page_statement_uses_the_new_indexes_and_never_scans_session_dims_sequentially(
    record_property,
):
    engine = await _engine()
    try:
        f = LocalAnalyticsFilter(scope=EXCLUDE_SCOPE, start_dt=_naive(_NOW - timedelta(days=30)), end_dt=_naive(_END))
        reader = PostgresCliAnalyticsReader(engine)
        ctes, params = reader._ordered_window(f, "ctx_per_call", None, None, False, stored=True)
        params.update(stale=[], limit=20, offset=0)
        text, args = positional(ordered_page_sql(ctes), params)
        async with _indexed_window(engine), engine.acquire() as conn:
            nodes = await _explain(conn, text, *args)
            scans = _scans(nodes)
            record_property("stored_ctx_page", scans)
            assert not [s for s in scans if s[0] == "Seq Scan"], scans
            # At 40k rows (a quarter of them in the window) a bitmap scan of a narrower index can cost less than the
            # index-only scan of the wide covering index (it INCLUDEs the ctx, cost and kind columns); at a million
            # sessions the index-only scan wins. With bitmap and sequential scans off the statement must be served by it.
            await conn.execute("SET enable_bitmapscan = off")
            await conn.execute("SET enable_seqscan = off")
            forced = _scans(await _explain(conn, text, *args))
            record_property("stored_ctx_page_forced", forced)
            assert ("Index Only Scan", "session_dims_fw_win") in forced, forced
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_an_enabled_table_without_any_stamp_runs_todays_statements_and_logs_the_reason(caplog):
    engine = await _engine()
    seed = _Seed()
    try:
        await seed.insert(engine)  # no stamp at all: every session of the window is in S
        await seed.corrupt(engine, "c", 10_000, 1)
        reader = PostgresCliAnalyticsReader(engine, s_cap=1)
        async with _switch(engine) as set_enabled:
            await set_enabled(True)  # as auto-enable or an operator leaves it
            with caplog.at_level(logging.WARNING):
                total, ids = await reader.get_session_page(seed.filter(), "ctx_per_call", 0, 1000)
            await set_enabled(False)
            today = await reader.get_session_page(seed.filter(), "ctx_per_call", 0, 1000)
        assert (total, ids) == today  # correct while nothing is stamped: the stored key 10000 is never read
        assert any("s_over_cap" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_the_migration_seeds_the_control_row_off_and_a_disable_survives_a_rerun_of_the_seed():
    from tests.codemie.repository.cli_analytics.postgres.test_migrations import SORT_KEYS  # noqa: PLC0415

    engine = await _engine()
    try:
        async with engine.acquire() as conn:
            async with conn.transaction():
                table = "pg_temp.sort_keys_control_probe"
                await conn.execute(SORT_KEYS.CREATE_CONTROL.replace(SORT_KEYS.CONTROL_TABLE, table))
                seed = SORT_KEYS.SEED_CONTROL.replace(SORT_KEYS.CONTROL_TABLE, table)
                await conn.execute(seed)
                assert tuple(await conn.fetchrow(f"SELECT enabled, operator_disabled FROM {table}")) == (False, False)
                await conn.execute(f"UPDATE {table} SET enabled = true")
                await conn.execute(seed)  # the migration runs again: the row is not reseeded
                assert await conn.fetchval(f"SELECT enabled FROM {table}") is True
                await conn.execute(f"DROP TABLE {table}")
    finally:
        await engine.close()
