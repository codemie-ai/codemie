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

"""Live-Postgres parity of the grouped SQL cost (`priced_groups`, `gc`) with the Python pricer (EPMCDME-17051).

Every case of the shared pricing table is seeded as its own session, plus the edge cases of the shared contract: a
model with no rates, a route outside the list and a NULL route, a mixed-case unpriced pair, a blank model, a
Personal-paid session, a session with only subagent totals and a costless session. Every grouping the contract lists
for EPMCDME-17051 and EPMCDME-17052 must equal the Python path (`price_session_models`, `price_bucket_rows` and
`buckets`), whatever the token source of `priced_groups`: `live` (today's build), `stored` (every session stamped, read
from `session_model_groups`) or `mixed` (half of the sessions unstamped and read live through `$stale`). Skipped when
`TEST_LOCAL_PG_URL` is not set or unreachable.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import timedelta
from unittest.mock import patch

import pytest

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.grouped_cost_sql import (
    Grouping,
    grouped_cost_sql,
    priced_groups_cte,
)
from codemie.repository.cli_analytics.postgres.session_sort_keys import update_session_sort_keys
from codemie.repository.cli_analytics.postgres.reader import (
    PostgresCliAnalyticsReader,
    rates_params,
    window_params,
)
from codemie.repository.cli_analytics.scope import FUNDING_ROUTES
from codemie.service.analytics.handlers.coding_agent_pricing import CatalogPricer, TokenCounts, model_rates_for
from codemie.service.analytics.handlers.funding_buckets import buckets, price_bucket_rows
from codemie.service.analytics.handlers.session_money import price_session_models, token_counts
from tests.codemie.repository.cli_analytics.postgres.live_helpers import (
    _COST_SQL,
    _MODULE,
    _TOTALS_SQL,
    _engine,
    _row,
    _Seed,
)
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE
from tests.codemie.service.analytics.handlers.test_coding_agent_pricing import (
    _HAIKU,
    _NO_1H,
    _PARTIAL,
    PRICING_CASES,
)

_DIMS_SQL = """
    INSERT INTO session_dims (session_id, started_at, last_event_at, jwt_email, project_name, repository, branch,
                              funding_route, subscription_type, project_origin)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
"""
_TURNS_SQL = "INSERT INTO turns_daily (day, session_id, turns) VALUES ($1, $2, 1)"
_TABLES = ("session_dims", "cost_daily", "session_usage", "turns_daily", "session_model_groups")
_TOL = 1e-9

# Every grouping the shared contract lists for EPMCDME-17051 and EPMCDME-17052.
GROUPINGS = [
    Grouping("spend", []),
    Grouping("spend_user", ["user"]),
    Grouping("spend_user_cd", ["user"], cost_daily_only=True),
    Grouping("spend_model", ["model"]),
    Grouping("spend_repo", ["repository"]),
    Grouping("spend_proj_repo", ["project", "repository"]),
    Grouping("spend_proj_repo_branch", ["project", "repository", "branch"]),
    Grouping("route", ["funding_route"]),
    Grouping("billed", ["personal_billed"]),
    Grouping("user_route", ["user", "funding_route"]),
    Grouping("user_billed", ["user", "personal_billed"]),
    Grouping("user_route_cd", ["user", "funding_route"], cost_daily_only=True),
    Grouping("user_billed_cd", ["user", "personal_billed"], cost_daily_only=True),
    Grouping("model_route", ["model", "funding_route"]),
    Grouping("model_billed", ["model", "personal_billed"]),
    Grouping("repo_route", ["repository", "funding_route"]),
    Grouping("repo_billed", ["repository", "personal_billed"]),
    Grouping("proj_repo_route", ["project", "repository", "funding_route"]),
    Grouping("proj_repo_billed", ["project", "repository", "personal_billed"]),
]


class _GroupedSeed(_Seed):
    """Sessions of two developers, with their own project, repository, branch, route and billing class."""

    def __init__(self) -> None:
        super().__init__()
        self.other = f"cost-key-other-{self.suffix}@example.com"
        self.sids: list[str] = []

    def filter(self) -> LocalAnalyticsFilter:
        f = super().filter()
        return LocalAnalyticsFilter(
            scope=EXCLUDE_SCOPE, start_dt=f.start_dt, end_dt=f.end_dt, users=[self.email, self.other]
        )

    async def dims(
        self, engine, name: str, *, email=None, project="p1", repo="r1", branch="main", route=None, sub=None
    ):
        sid = self.sid(name)
        self.sids.append(sid)
        origin = "linked_folder" if sub == "personal" else "profile"
        async with engine.acquire() as conn:
            await conn.execute(
                _DIMS_SQL,
                sid,
                self.t0,
                self.t0 + timedelta(minutes=1),
                email or self.email,
                project,
                repo,
                branch,
                route,
                sub,
                origin,
            )
        return sid

    async def cost(self, engine, sid: str, rows: list[tuple]) -> None:
        async with engine.acquire() as conn:
            for model, *tokens in rows:
                await conn.execute(_COST_SQL, self.t0.date(), sid, self.email, model, *tokens)

    async def cleanup(self, engine) -> None:
        async with engine.acquire() as conn:
            for table in _TABLES:
                await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", self.sids)  # noqa: S608


async def _seed_all(engine, seed: _GroupedSeed) -> dict[str, str]:
    """Seeds every case and returns the named edge-case session ids."""
    routes = ("api_based", "subscription", None, "weird-route")
    for number, param in enumerate(PRICING_CASES):
        model, counts, _, _ = param.values
        sid = await seed.dims(
            engine,
            f"c{number:02d}",
            email=seed.email if number % 2 else seed.other,
            project=f"p{number % 2}",
            repo=f"r{number % 3}",
            branch=f"b{number % 2}",
            route=routes[number % len(routes)],
        )
        await seed.cost(engine, sid, [_row(model, dict(counts))])
    named = {
        "mixed": await seed.dims(engine, "mixed", route="api_based"),
        "blank": await seed.dims(engine, "blank", route="subscription"),
        "personal": await seed.dims(engine, "personal", route="subscription", sub="personal"),
        "subagent": await seed.dims(engine, "subagent", route="api_based", repo="r-sub"),
        "costless": await seed.dims(engine, "costless", route="api_based"),
        # a second session of the same user, route and cells: unpriced names are unioned across sessions, not summed
        "mixed2": await seed.dims(engine, "mixed2", route="api_based"),
    }
    await seed.cost(engine, named["mixed"], [("Foo-X", 5, 0, 0, 0, None, None), ("foo-x", 3, 0, 0, 0, None, None)])
    await seed.cost(engine, named["mixed2"], [("Bar-Y", 2, 0, 0, 0, None, None), ("FOO-X", 1, 0, 0, 0, None, None)])
    await seed.cost(
        engine, named["blank"], [("", 4, 0, 0, 0, None, None), ("claude-haiku-4-5", 10, 1, 0, 0, None, None)]
    )
    await seed.cost(engine, named["personal"], [("claude-haiku-4-5", 1_000, 100, 50, 0, None, None)])
    attrs = json.dumps({"token_source": "subagent_usage"})
    async with engine.acquire() as conn:
        await conn.execute(_TOTALS_SQL, named["subagent"], seed.t0.date(), "claude-haiku-4-5", 1_000, attrs)
        await conn.execute(_TURNS_SQL, seed.t0.date(), named["costless"])
    return named


SOURCES = ("live", "stored", "mixed")
_VERSION = "parity-v1"
# sha256 of `priced_groups_cte()` as it was before the stored source existed: the all-live form must not change.
# Re-pinned on the EPMCDME-16427 part2 base, which moved the shared pricing terms, and for EPMCDME-16457, which dashes
# dotted Claude versions in normalized_model_name, for the `sub_class` column (the subscription class the Overview
# splits its cost by), which only adds a column of `sel`, and for EPMCDME-17260, which drops the `spend` column (every
# session counts toward the money, the Personal-paid ones too).
_LIVE_FORM_SHA256 = "7e2a43e92983b35d6945c8659724878324d831e6a306f7ccb6c022e6a9c88576"


async def _stamp_for(engine, seed: _GroupedSeed, source: str) -> list[str] | None:
    """Stamps the seeded sessions the way the source needs and returns the `$stale` ids (None: the all-live form).
    stored: every session is stamped, S is empty. mixed: every other session stays unstamped and is in S."""
    if source == "live":
        return None
    ids = sorted(seed.sids)
    stamped = ids if source == "stored" else ids[1::2]
    stale = [s for s in ids if s not in stamped]
    async with engine.acquire() as conn:
        await update_session_sort_keys(conn, stamped, {}, {}, _VERSION)
        for sid in stale[:1]:  # leftover rows of an unstamped session must not be read
            await conn.execute(
                "INSERT INTO session_model_groups (session_id, model_name, normalized_model_name, input_tokens, "
                "output_tokens, cache_read_tokens, cache_creation_tokens, has_cost_daily) "
                "VALUES ($1, 'poison', 'poison', 999, 999, 999, 999, true)",
                sid,
            )
    return stale


async def _sql(
    reader: PostgresCliAnalyticsReader, f: LocalAnalyticsFilter, rates, tail: str, stale: list[str] | None = None
) -> list[dict]:
    sel = reader._sessions_cte(f, with_developer=True)
    params = {**window_params(f), **rates_params(rates)}
    if stale is not None:
        params["stale"] = stale
    return await reader._q(f"WITH {sel}, {priced_groups_cte(stored=stale is not None)} {tail}", params)


def _python_rows(funding_rows: list[dict], sessions: dict[str, dict], cost_daily: set[str]) -> list[dict]:
    """The Python-priced (session, raw model) rows with the session keys of the grouping."""
    priced = price_bucket_rows(funding_rows)
    pricer = CatalogPricer()
    for row, source in zip(priced, funding_rows, strict=True):
        session = sessions[row["session_id"]]
        row["cache_read"] = pricer.price(row["model"], TokenCounts(cache_read=token_counts(source).cache_read))
        row["keys"] = {
            "user": session["developer"],
            "project": session["project_name"],
            "repository": session["repository"],
            "branch": session["branch"],
            "model": row["normalized_model_name"],
            "funding_route": row["funding_route"],
            "personal_billed": row["personal_billed"],
        }
        row["has_cost_daily"] = row["session_id"] in cost_daily
    return priced


def _python_groups(rows: list[dict], grouping: Grouping) -> dict[tuple, dict]:
    kept = [
        r
        for r in rows
        if (not grouping.cost_daily_only or r["has_cost_daily"])
        and ("model" not in grouping.keys or r["normalized_model_name"] != "")
    ]
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for r in kept:
        grouped[tuple(r["keys"][k] for k in grouping.keys)].append(r)
    result = {}
    for key, members in grouped.items():
        unpriced = sorted({r["model"] for r in members if r["missing"]})
        result[key] = {
            "cost_usd": sum(r["cost_usd"] for r in members),
            "cache_read_cost_usd": sum(r["cache_read"] for r in members),
            "session_count": len({r["session_id"] for r in members}),
            "unpriced_models": unpriced,
            "cost_incomplete": bool(unpriced),
        }
    return result


def _sql_groups(gc_rows: list[dict], grouping: Grouping) -> dict[tuple, dict]:
    result = {}
    for row in gc_rows:
        if row["grouping"] != grouping.name:
            continue
        key = tuple(row[k] for k in grouping.keys)
        assert key not in result, (grouping.name, key)
        result[key] = {
            "cost_usd": float(row["cost_usd"]),
            "cache_read_cost_usd": float(row["cache_read_cost_usd"]),
            "session_count": row["session_count"],
            "unpriced_models": list(row["unpriced_models"]),
            "cost_incomplete": row["cost_incomplete"],
        }
    return result


def _assert_group_equal(actual: dict, expected: dict, label) -> None:
    assert actual["cost_usd"] == pytest.approx(expected["cost_usd"], abs=_TOL), label
    assert actual["cache_read_cost_usd"] == pytest.approx(expected["cache_read_cost_usd"], abs=_TOL), label
    assert actual["session_count"] == expected["session_count"], label
    assert actual["unpriced_models"] == expected["unpriced_models"], label
    assert actual["cost_incomplete"] == expected["cost_incomplete"], label


def _assert_bucket(actual: dict | None, bucket: dict, label) -> None:
    if actual is None:  # a bucket with no row is the empty bucket
        assert bucket == {"cost_usd": 0, "session_count": 0, "cost_incomplete": False, "unpriced_models": []}, label
        return
    assert actual["cost_usd"] == pytest.approx(bucket["cost_usd"], abs=_TOL), label
    assert actual["session_count"] == bucket["session_count"], label
    assert actual["unpriced_models"] == bucket["unpriced_models"], label
    assert actual["cost_incomplete"] == bucket["cost_incomplete"], label


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.asyncio
async def test_grouped_sql_cost_equals_the_python_pricer_on_every_case(source):
    engine = await _engine()
    seed = _GroupedSeed()
    try:
        named = await _seed_all(engine, seed)
        stale = await _stamp_for(engine, seed, source)
        reader = PostgresCliAnalyticsReader(engine)
        f = seed.filter()
        with patch(f"{_MODULE}.llm_service.get_all_llm_model_info", return_value=[_HAIKU, _NO_1H, _PARTIAL]):
            funding_rows = await reader.get_session_funding_costs(f)
            spend_money = price_session_models(await reader.get_session_model_costs(f))
            rates = [model_rates_for(m) for m in sorted({r["model_name"] for r in funding_rows})]
            sessions = {
                r["session_id"]: r
                for r in await reader._q(
                    f"WITH {reader._sessions_cte(f, with_developer=True)} "
                    "SELECT session_id, developer, project_name, repository, branch FROM sel",
                    window_params(f),
                )
            }
            cost_daily = {r["session_id"] for r in funding_rows if r["session_id"] != named["subagent"]}
            python_rows = _python_rows(funding_rows, sessions, cost_daily)
            priced = price_bucket_rows(funding_rows)

            gc_rows = await _sql(reader, f, rates, f"SELECT * FROM {grouped_cost_sql(GROUPINGS)}", stale)
            per_session = await _sql(
                reader,
                f,
                rates,
                "SELECT session_id, sum(cost) AS cost, sum(cache_read_cost) AS cache_read_cost "
                "FROM priced_groups GROUP BY session_id",
                stale,
            )

        # the seeded edge cases reach the priced set as the contract says
        assert named["costless"] in sessions
        assert named["costless"] not in {r["session_id"] for r in funding_rows}
        assert {named["mixed"], named["blank"], named["personal"], named["subagent"]} <= set(sessions)

        # every grouping equals the Python path, group by group, lists matched exactly
        for grouping in GROUPINGS:
            actual = _sql_groups(gc_rows, grouping)
            expected = _python_groups(python_rows, grouping)
            assert set(actual) == set(expected), grouping.name
            for key, values in expected.items():
                _assert_group_equal(actual[key], values, (grouping.name, key))

        # the spend [] row is today's total and cache read
        (window,) = _sql_groups(gc_rows, GROUPINGS[0]).values()
        assert window["cost_usd"] == pytest.approx(spend_money.total, abs=_TOL)
        assert window["cache_read_cost_usd"] == pytest.approx(spend_money.cache_read, abs=_TOL)
        assert window["unpriced_models"] == sorted(
            {"Foo-X", "foo-x", "FOO-X", "Bar-Y", ""} | spend_unpriced(python_rows)
        )

        # priced_groups summed by session is today's by_session and cache_read_by_session, the Personal-paid one's too
        by_session = {r["session_id"]: r for r in per_session}
        assert set(by_session) == set(spend_money.by_session)
        assert named["personal"] in by_session
        for sid, cost in spend_money.by_session.items():
            assert float(by_session[sid]["cost"]) == pytest.approx(cost, abs=_TOL), sid
            cache_read = spend_money.cache_read_by_session[sid]
            assert float(by_session[sid]["cache_read_cost"]) == pytest.approx(cache_read, abs=_TOL), sid

        # spend per model is today's by_model; the blank model has no model group
        by_model = _sql_groups(gc_rows, GROUPINGS[3])
        assert ("",) not in by_model
        assert {k[0]: v["cost_usd"] for k, v in by_model.items()} == pytest.approx(spend_money.by_model, abs=_TOL)

        # the bucket groupings equal `buckets` over the window, per user and per model
        route, billed = _sql_groups(gc_rows, GROUPINGS[7]), _sql_groups(gc_rows, GROUPINGS[8])
        window_buckets = buckets(priced)
        for name in FUNDING_ROUTES:
            _assert_bucket(route.get((name,)), window_buckets["funding"][name], name)
        _assert_bucket(billed.get((True,)), window_buckets["personal_billed"], "personal_billed")
        user_route, user_billed = _sql_groups(gc_rows, GROUPINGS[11]), _sql_groups(gc_rows, GROUPINGS[12])
        for developer in {s["developer"] for s in sessions.values()}:
            ids = [sid for sid, s in sessions.items() if s["developer"] == developer and sid in cost_daily]
            user_buckets = buckets(priced, session_ids=ids)
            for name in FUNDING_ROUTES:
                _assert_bucket(user_route.get((developer, name)), user_buckets["funding"][name], (developer, name))
            _assert_bucket(user_billed.get((developer, True)), user_buckets["personal_billed"], developer)
        model_route, model_billed = _sql_groups(gc_rows, GROUPINGS[13]), _sql_groups(gc_rows, GROUPINGS[14])
        for model in {r["normalized_model_name"] for r in priced} - {""}:
            model_buckets = buckets(priced, model=model)
            for name in FUNDING_ROUTES:
                _assert_bucket(model_route.get((model, name)), model_buckets["funding"][name], (model, name))
            _assert_bucket(model_billed.get((model, True)), model_buckets["personal_billed"], model)

        # a route outside the list and a NULL route both read 'unknown'
        assert {k[0] for k in route} <= set(FUNDING_ROUTES)

        # cost_daily_only leaves out the session with only subagent totals, the plain grouping keeps it
        assert _python_groups(python_rows, GROUPINGS[1]) != _python_groups(python_rows, GROUPINGS[2])
        sub_repo = _sql_groups(gc_rows, GROUPINGS[4])[("r-sub",)]
        assert sub_repo["session_count"] == 1
        assert sub_repo["cost_usd"] > 0
    finally:
        await seed.cleanup(engine)
        await engine.close()


def spend_unpriced(rows: list[dict]) -> set[str]:
    return {r["model"] for r in rows if r["missing"]}


@pytest.mark.parametrize("stale", [None, []], ids=["live", "stored"])
@pytest.mark.asyncio
async def test_a_filter_matching_nothing_gives_no_rows(stale):
    engine = await _engine()
    seed = _GroupedSeed()
    try:
        reader = PostgresCliAnalyticsReader(engine)
        rows = await _sql(reader, seed.filter(), [], f"SELECT * FROM {grouped_cost_sql(GROUPINGS)}", stale)
        assert rows == []
    finally:
        await engine.close()


def test_grouping_rejects_an_unknown_key():
    with pytest.raises(ValueError, match="key"):
        Grouping("bad", ["developer"])


def test_grouped_cost_sql_rejects_a_duplicate_name():
    with pytest.raises(ValueError, match="duplicate"):
        grouped_cost_sql([Grouping("g", []), Grouping("g", ["user"])])


def test_grouped_cost_sql_rejects_no_grouping():
    with pytest.raises(ValueError):
        grouped_cost_sql([])


def test_gc_carries_one_column_per_used_key_only():
    sql = grouped_cost_sql([Grouping("u", ["user"]), Grouping("m", ["model", "funding_route"])])
    assert '"user"' in sql and " AS model" in sql and " AS funding_route" in sql
    assert " AS repository" not in sql and " AS personal_billed" not in sql


def test_the_default_form_is_the_all_live_form_and_does_not_change():
    default = priced_groups_cte()
    assert default == priced_groups_cte(stored=False)
    assert hashlib.sha256(default.encode()).hexdigest() == _LIVE_FORM_SHA256
    assert "session_model_groups" not in default and "$stale" not in default


def test_the_stored_form_reads_the_table_and_the_live_arm_through_a_materialized_join():
    sql = priced_groups_cte(stored=True)
    assert "FROM session_model_groups" in sql and "unnest($stale::text[])" in sql
    assert "live AS MATERIALIZED" in sql and "priced_groups AS MATERIALIZED" in sql
    assert "IN (SELECT session_id FROM live)" not in sql  # a joined `live`, never IN (subselect)
    assert "UNION ALL" in sql and "JOIN live" in sql


@pytest.mark.parametrize("stored", [False, True])
def test_priced_groups_columns_are_the_same_for_both_forms(stored):
    sql = priced_groups_cte(stored=stored)
    for column in (
        "AS \"user\"",
        "AS project",
        "AS funding_route",
        "AS personal_billed",
        "AS sub_class",
        "AS has_cost_daily",
        "AS cost",
        "AS cache_read_cost",
        "AS unpriced",
        "normalized_model_name",
    ):
        assert column in sql, column
