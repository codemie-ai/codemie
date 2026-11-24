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

"""Live-Postgres check that the ranking rows of the start-day rollup serve the Repositories and Projects tabs the
response the live statements give (EPMCDME-17301): counts, cost, order, ties, the unattributed project last, after a new
session, a resumed session, a rates change and a late event, with and without the subscription and origin filters. A
request with a search, or a scope the rollup is not made for, never reads it. Skipped without `TEST_LOCAL_PG_URL`."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from codemie.configs.config import config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres import user_rollup as ur
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.service.analytics.dashboard_cache import reset_dashboard_cache, rollup_key
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from codemie.service.analytics.handlers.coding_agent_pricing import model_rates_for
from tests.codemie.repository.cli_analytics.postgres import test_live_dashboard_aggregates as live
from tests.codemie.service.analytics.handlers.test_projects_parity import _same

pytestmark = pytest.mark.skipif(
    not live._LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against"
)

VERSION, OTHER_VERSION = "test-ranking-rates-v1", "test-ranking-rates-v2"
_ROLLUP_TABLES = (*ur._DAY_TABLES, "user_rollup_day_state")
_PAGES = [(0, 2), (1, 2), (0, 50), (9, 2)]


def _dearer(model: str):
    """The lookup of the live seed with every input price doubled: what a rates change does to a built day."""
    config_of = live._lookup(model)
    return None if config_of is None else config_of.model_copy(update={"input": config_of.input * 2})


class _Window:
    """The rollup scope over whole days up to now, as the router builds it for a super admin."""

    def __init__(self, seed: live._Seed, offset: timedelta = timedelta(0), **filters) -> None:
        # Yesterday is always in it: a window of the last day or less is answered by the live statements only with raw
        # events of its own (the period-membership rule), which the seeds do not hold, and never from the rollup.
        today = datetime.now(timezone.utc).date()
        self.days = sorted({seed.t0.date(), today - timedelta(days=1), today})
        self.start = datetime(self.days[0].year, self.days[0].month, self.days[0].day, tzinfo=timezone.utc) + offset
        self.filters = filters

    def filter(self) -> LocalAnalyticsFilter:
        return LocalAnalyticsFilter(
            scope=ur.ROLLUP_SCOPE, start_dt=self.start, end_dt=datetime.now(timezone.utc), **self.filters
        )


async def _forget_days(engine, days) -> None:
    async with engine.acquire() as conn:
        for table in _ROLLUP_TABLES:
            await conn.execute(f"DELETE FROM {table} WHERE start_day = ANY($1::date[])", days)  # noqa: S608 - fixed names


async def _build(engine, reader, days, version: str, lookup=live._lookup) -> None:
    """The job's work for the days: build them under `version`'s rates."""
    reader.set_rates(lambda: version)
    with patch(live._PRICING, lookup):
        async with engine.acquire() as conn:
            signatures = await ur.day_signatures(conn, days[0], days[-1])
            for day in days:
                await ur.build_day(reader, conn, day, model_rates_for, version, signatures.get(day, ur._NO_SESSIONS))


class _Spy:
    """The answers of the reader's ranking reads and its Efficiency read, in order."""

    def __init__(self) -> None:
        self.served: list[bool] = []

    def patches(self):
        patches = []
        for name in ("rollup_repository_ranking", "rollup_project_page", "rollup_efficiency"):
            original = getattr(PostgresCliAnalyticsReader, name)

            def wrap(original=original):
                async def call(reader, *args, **kwargs):
                    answer = await original(reader, *args, **kwargs)
                    self.served.append(answer is not None)
                    return answer

                return call

            patches.append(patch.object(PostgresCliAnalyticsReader, name, wrap()))
        return patches


async def _responses(handler, window: _Window, lookup=live._lookup, rollup: bool = True) -> dict:
    """Every request the tests compare: the Repositories tab (with and without branches, and with the unattributed group
    last, four pages), the Projects tab (nested, and totals only) in four pages, each with the answer cache emptied
    first."""
    out = {}
    with patch(live._PRICING, lookup), patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", rollup):
        for include_branches in (False, True):
            for page, per_page in _PAGES:
                reset_dashboard_cache()
                data, pricer = await handler.get_repositories(window.filter(), page, per_page, include_branches, None)
                out["repositories", include_branches, page, per_page] = (data, list(pricer.unpriced))
        for page, per_page in _PAGES:
            reset_dashboard_cache()
            data, pricer = await handler.get_repositories(window.filter(), page, per_page, False, None, True)
            out["repositories", "unattributed_last", page, per_page] = (data, list(pricer.unpriced))
        for page, per_page in _PAGES:
            reset_dashboard_cache()
            data, pricer = await handler.get_projects(window.filter(), page, per_page, None)
            out["projects", page, per_page] = (data, list(pricer.unpriced))
        for page, per_page in _PAGES:
            reset_dashboard_cache()
            data, pricer = await handler.get_projects(window.filter(), page, per_page, None, include_repositories=False)
            out["projects", "totals only", page, per_page] = (data, list(pricer.unpriced))
        reset_dashboard_cache()
        data, pricer = await handler.get_efficiency(window.filter(), include_class_lists=False, include_buckets=False)
        out["efficiency"] = (data, list(pricer.unpriced))
    return out


def _assert_equal(served: dict, live_answers: dict) -> None:
    assert served.keys() == live_answers.keys()
    for key in served:
        data, unpriced = served[key]
        want, want_unpriced = live_answers[key]
        assert _same(json.loads(json.dumps(data, default=str)), json.loads(json.dumps(want, default=str))) == [], key
        assert unpriced == want_unpriced, key


async def _check(
    engine, reader, handler, window: _Window, *, built_with: str = VERSION, lookup=live._lookup, must_serve: bool = True
) -> None:
    """The rollup answers (it must, unless the window leaves it no whole day) the same as the live statements."""
    spy = _Spy()
    patches = spy.patches()
    for p in patches:
        p.start()
    try:
        served = await _responses(handler, window, lookup)
        assert spy.served and (all(spy.served) or not must_serve), "the rollup refused a request it must answer"
        if must_serve:  # the Efficiency answer it compares is not an empty one
            assert served["efficiency"][0]["kpis"]["worst_session_trace_id"], "no worst session in the answer"
        spy.served.clear()
        reader.set_rates(lambda: built_with)
        wanted = await _responses(handler, window, lookup, rollup=False)
        assert not spy.served  # the kill switch: the live statements only
    finally:
        for p in patches:
            p.stop()
    _assert_equal(served, wanted)


async def _add_resumed(engine, seed: live._Seed) -> list[str]:
    """A session that started two days before the window and was active in it: the start-day rows cannot hold it."""
    sid = f"test-agg-{seed.suffix}-resumed"
    day = seed.t0.date()
    started = seed.t0 - timedelta(days=2)
    async with engine.acquire() as conn:
        await conn.execute(
            "INSERT INTO session_dims (session_id, started_at, last_event_at, repository, branch, project_name,"
            " jwt_email, subscription_type, project_origin, funding_route, first_prompt, has_activity, resumed,"
            " last_activity_day) VALUES ($1, $2, $3, 'repo-a', 'main', 'P2', $4, 'codemie', 'profile', 'api_based',"
            " 'resumed', true, true, $5)",
            sid, started, seed.t0, seed.email, day,
        )  # fmt: skip
        await conn.execute(
            "INSERT INTO cost_daily (day, session_id, user_email, model_name, query_source, cost_usd, input_tokens,"
            " output_tokens, cache_read_tokens, cache_creation_tokens, api_call_count)"
            " VALUES ($1, $2, $3, $4, 'main', 0, 500, 50, 0, 0, 2)",
            day, sid, seed.email, live._COST_MODEL,
        )  # fmt: skip
        await conn.execute("INSERT INTO turns_daily (day, session_id, turns) VALUES ($1, $2, 3)", day, sid)
    return [sid]


async def _cleanup_ids(engine, ids: list[str]) -> None:
    async with engine.acquire() as conn:
        for table in live._TABLES:
            await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", ids)  # noqa: S608


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filters",
    [{}, {"subscription_types": ["codemie"]}, {"project_origins": ["profile"]}, {"subscription_types": ["corporate"]}],
    ids=["unfiltered", "subscription", "origin", "other-subscription"],
)
async def test_the_rollup_serves_the_rankings_the_live_statements_give_after_every_kind_of_change(filters):
    engine, seed = await live._engine(), live._Seed()
    window, extra = _Window(seed, **filters), live._Seed()
    resumed: list[str] = []
    try:
        await seed.insert(engine)
        reader = PostgresCliAnalyticsReader(engine)
        handler = LocalAnalyticsHandler(reader)
        await _build(engine, reader, window.days, VERSION)
        await _check(engine, reader, handler, window)  # the first build

        await extra.insert(engine)  # new sessions of the same groups, a day rebuilt
        await _build(engine, reader, window.days, VERSION)
        await _check(engine, reader, handler, window)

        resumed = await _add_resumed(engine, seed)  # started before the window: read live and combined
        await _check(engine, reader, handler, window)

        async with engine.acquire() as conn:  # a late event: tokens of another model on a built session
            await conn.execute(
                "INSERT INTO cost_daily (day, session_id, user_email, model_name, query_source, cost_usd,"
                " input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, api_call_count)"
                " VALUES ($1, $2, $3, $4, 'main', 0, 900, 90, 0, 0, 1)",
                seed.t0.date(), seed.sid(4), seed.email, live._KNOWN_MODEL,
            )  # fmt: skip
        await _build(engine, reader, window.days, VERSION)
        await _check(engine, reader, handler, window)

        # a rates change: the built days are under the old rates, so the rollup refuses until they are rebuilt
        reader.set_rates(lambda: OTHER_VERSION)
        spy = _Spy()
        patches = spy.patches()
        for p in patches:
            p.start()
        try:
            refused = await _responses(handler, window, _dearer)
        finally:
            for p in patches:
                p.stop()
        assert spy.served and not any(spy.served)
        await _build(engine, reader, window.days, OTHER_VERSION, _dearer)
        await _check(engine, reader, handler, window, built_with=OTHER_VERSION, lookup=_dearer)
        _assert_equal(refused, await _responses(handler, window, _dearer, rollup=False))
    finally:
        await seed.cleanup(engine)
        await extra.cleanup(engine)
        await _cleanup_ids(engine, resumed)
        await _forget_days(engine, window.days)
        reset_dashboard_cache()


@pytest.mark.asyncio
async def test_unattributed_project_is_last_and_ties_order_by_name_on_both_paths():
    engine, seed = await live._engine(), live._Seed()
    window = _Window(seed)
    try:
        await seed.insert(engine)
        reader = PostgresCliAnalyticsReader(engine)
        await _build(engine, reader, window.days, VERSION)
        handler = LocalAnalyticsHandler(reader)
        served = await _responses(handler, window)
        names = [row["project_name"] for row in served["projects", 0, 50][0]["rows"]]
        assert None in names and names.index(None) == len(names) - 1  # the unattributed project is last
        repositories = served["repositories", False, 0, 50][0]["rows"]
        order = [(-r["session_count"], -r["cost_usd"], r["repository"] or "") for r in repositories]
        assert order == sorted(order)  # sessions, spend, then the name in code-point order
    finally:
        await seed.cleanup(engine)
        await _forget_days(engine, window.days)
        reset_dashboard_cache()


@pytest.mark.asyncio
async def test_a_request_with_a_search_or_another_scope_never_reads_the_rollup_and_gives_the_live_result():
    engine, seed = await live._engine(), live._Seed()
    window = _Window(seed)
    try:
        await seed.insert(engine)
        reader = PostgresCliAnalyticsReader(engine)
        await _build(engine, reader, window.days, VERSION)
        handler = LocalAnalyticsHandler(reader)
        spy = _Spy()
        patches = spy.patches()
        for p in patches:
            p.start()
        try:
            with patch(live._PRICING, live._lookup), patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True):
                reset_dashboard_cache()
                await handler.get_repositories(window.filter(), 0, 5, False, "repo")
                await handler.get_repositories(window.filter(), 0, 5, True, "repo")
                await handler.get_projects(window.filter(), 0, 5, "repo")
                assert spy.served == []  # not even asked
                # the developer scope is not the rollup's: it is asked, refuses, and the live statements answer
                narrowed = seed.filter()
                answer, _ = await handler.get_repositories(narrowed, 0, 5, False, None)
                projects, _ = await handler.get_projects(narrowed, 0, 5, None)
            assert spy.served == [False, False]
            with patch(live._PRICING, live._lookup), patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", False):
                want, _ = await handler.get_repositories(narrowed, 0, 5, False, None)
                want_projects, _ = await handler.get_projects(narrowed, 0, 5, None)
            assert answer == want and projects == want_projects and answer["rows"]
        finally:
            for p in patches:
                p.stop()
    finally:
        await seed.cleanup(engine)
        await _forget_days(engine, window.days)
        reset_dashboard_cache()


@pytest.mark.asyncio
async def test_a_cached_page_is_not_served_for_another_page():
    engine, seed = await live._engine(), live._Seed()
    window = _Window(seed)
    try:
        await seed.insert(engine)
        reader = PostgresCliAnalyticsReader(engine)
        await _build(engine, reader, window.days, VERSION)
        handler = LocalAnalyticsHandler(reader)
        reset_dashboard_cache()
        with patch(live._PRICING, live._lookup), patch.object(config, "CLI_ANALYTICS_USER_ROLLUP_ENABLED", True):
            first, _ = await handler.get_repositories(window.filter(), 0, 2, False, None)
            second, _ = await handler.get_repositories(window.filter(), 1, 2, False, None)  # no reset between
            again, _ = await handler.get_repositories(window.filter(), 0, 2, False, None)
        assert [r["repository"] for r in first["rows"]] != [r["repository"] for r in second["rows"]]
        assert first["rows"] == again["rows"] and second["pagination"]["page"] == 1
        f = window.filter()
        keys = {
            rollup_key(f, "rollup_repository_ranking", branches, page, size)
            for branches in (False, True)
            for page, size in _PAGES
        }
        assert len(keys) == 8  # the branches flag, the page and its size are all in the key
    finally:
        await seed.cleanup(engine)
        await _forget_days(engine, window.days)
        reset_dashboard_cache()


@pytest.mark.asyncio
async def test_a_window_that_starts_inside_a_day_reads_the_first_day_live_and_gives_the_live_rankings():
    engine, seed = await live._engine(), live._Seed()
    window = _Window(seed, offset=timedelta(minutes=1))
    try:
        await seed.insert(engine)
        reader = PostgresCliAnalyticsReader(engine)
        await _build(engine, reader, window.days, VERSION)
        await _check(engine, reader, LocalAnalyticsHandler(reader), window, must_serve=False)
    finally:
        await seed.cleanup(engine)
        await _forget_days(engine, window.days)
        reset_dashboard_cache()
