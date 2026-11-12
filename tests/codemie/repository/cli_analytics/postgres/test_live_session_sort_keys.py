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

"""Live-Postgres proof of the refresher write path of the stored session sort keys (EPMCDME-16427, part 2).

After `RollupRefresher._recompute` of a key with a `_LOG`, `_USAGE`, `_SPANS` or `_SESSION` bit, every stored column
of `session_dims` must equal what today's statements compute for the session: the ctx key (`reader._sort_clause`),
the cost key (`session_cost_key_sql`, which the Python pricer is already proven against), the classifier names
(`CLASSIFIER_NAMES_SQL`) and the model kinds (`kind_tokens`). The day rows are seeded by hand, so the RECOMPUTE
statements (which rebuild them from raw records) are replaced by none; only the derived steps run.

Skipped when `TEST_LOCAL_PG_URL` is not set or unreachable: point it at a disposable, migrated PostgreSQL.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import asyncpg
import pytest

from codemie.configs.config import Config
from codemie.repository.cli_analytics.ports import ModelRates
from codemie.repository.cli_analytics.postgres import migrations, rollups
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.kind_masks import KIND_BITS
from codemie.repository.cli_analytics.postgres.maintenance import (
    MaintenanceReport,
    PartitionMaintainer,
    RetentionPolicy,
)
from codemie.repository.cli_analytics.postgres.rates_version import rates_version
from codemie.repository.cli_analytics.postgres.reader import (
    PostgresCliAnalyticsReader,
    positional,
    rates_params,
    session_cost_key_sql,
)
from codemie.repository.cli_analytics.postgres.session_derived import CLASSIFIER_NAMES_SQL
from codemie.repository.cli_analytics.postgres.session_sort_keys import update_session_sort_keys
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.service.analytics import cli_analytics_jobs
from codemie.service.analytics.handlers.coding_agent_pricing import TokenCounts, kind_tokens, model_rates_for
from codemie.service.analytics.handlers.session_money import price_session_models
from tests.codemie.repository.cli_analytics.support.scopes import EXCLUDE_SCOPE
from tests.codemie.service.analytics.handlers.test_coding_agent_pricing import (
    _HAIKU,
    _NO_1H,
    _PARTIAL,
    PRICING_CASES,
)

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against")

_MODULE = "codemie.service.analytics.handlers.coding_agent_pricing"
_NOW = datetime.now(timezone.utc).replace(microsecond=0)
_DAY = (_NOW - timedelta(days=2)).date()
_STARTED = _NOW - timedelta(days=2)
_COLUMNS = "cache_read_total, api_call_total, classifier_names, models_used, kind_masks, cost_key, rates_version"
_DIMS_SQL = "INSERT INTO session_dims (session_id, started_at, last_event_at, jwt_email, skills, commands) VALUES ($1, $2, $2, $3, $4::jsonb, $5::jsonb)"
_COST_SQL = """
    INSERT INTO cost_daily
        (day, session_id, user_email, model_name, query_source, cost_usd, input_tokens, output_tokens,
         cache_read_tokens, cache_creation_tokens, cache_creation_5m_tokens, cache_creation_1h_tokens, api_call_count)
    VALUES ($1, $2, $3, $4, 'repl_main_thread', 0, $5, $6, $7, $8, $9, $10, $11)"""
_TOTALS_SQL = """
    INSERT INTO session_usage
        (session_id, day, model, speed, inference_geo, scope_kind, scope_name, agent_id,
         input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, attrs)
    VALUES ($1, $2, $3, '', '', '', '', 'ag1', $4, 0, $5, 0, '{"token_source": "subagent_usage"}'::jsonb)"""
_ALL_BITS = int(rollups._LOG | rollups._USAGE | rollups._SPANS | rollups._SESSION)


async def _engine() -> AnalyticsPgEngine:
    try:
        settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL))
        engine = AnalyticsPgEngine(settings)
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
    migrations.run_migrations(settings)
    today = datetime.now(timezone.utc).date()
    maintainer = PartitionMaintainer(engine, RetentionPolicy.from_settings(settings), clock=lambda: today)
    async with engine.acquire() as conn:
        await maintainer._ensure_partitions(conn, today, MaintenanceReport())
    return engine


class _Seed:
    def __init__(self) -> None:
        self.suffix = uuid.uuid4().hex[:12]
        self.email = f"sortkeys-{self.suffix}@example.com"
        self.ids: list[str] = []

    def sid(self, name: str) -> str:
        return f"test-sortkeys-{self.suffix}-{name}"

    async def session(
        self, conn, name: str, rows=(), skills: dict | None = None, commands: list | None = None, started=_STARTED
    ) -> str:
        """`rows`: (model, input, output, cache_read, cache_creation, cache_5m, cache_1h, calls) per cost_daily row."""
        sid = self.sid(name)
        self.ids.append(sid)
        await conn.execute(_DIMS_SQL, sid, started, self.email, json.dumps(skills or {}), json.dumps(commands or []))
        for row in rows:
            await conn.execute(_COST_SQL, _DAY, sid, self.email, *row)
        return sid

    async def cleanup(self, engine: AnalyticsPgEngine) -> None:
        async with engine.acquire() as conn:
            for table in ("session_dims", "cost_daily", "session_usage", "invocations_hourly"):
                await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", self.ids)  # noqa: S608


def _rates(models) -> dict[str, ModelRates]:
    return {m: model_rates_for(m) for m in models}


def resolver(rates: Mapping[str, ModelRates]) -> Callable[[Iterable[str]], dict[str, ModelRates]]:
    """A rates callable over a fixed mapping: a name outside it has no rates (prices 0)."""
    return lambda names: {n: rates.get(n, ModelRates(model=n)) for n in names}


async def _recompute(
    engine: AnalyticsPgEngine, sids, rates, kinds: int = _ALL_BITS, version: str | None = None
) -> None:
    """One `_recompute` of keys for the sessions; the RECOMPUTE statements are skipped: the day rows are seeded.
    `rates` is a mapping (stamped with its own version) or the rates callable itself (stamped `version`)."""
    refresher = rollups.RollupRefresher(engine, batch_size=len(sids))
    if callable(rates):
        refresher.set_rates(lambda: version, rates)
    else:
        refresher.set_rates(lambda: version or rates_version(rates), resolver(rates))
    keys = [{"day": _DAY, "session_id": s, "kinds": kinds, "version": 1, "timeouts": 0} for s in sids]
    async with engine.acquire() as conn:
        with patch.object(rollups, "RECOMPUTE", []):
            await refresher._recompute(conn, keys)


async def _stored(engine: AnalyticsPgEngine, sid: str):
    async with engine.acquire() as conn:
        return await conn.fetchrow(f"SELECT {_COLUMNS} FROM session_dims WHERE session_id = $1", sid)  # noqa: S608


async def _todays_ctx(engine: AnalyticsPgEngine, sids: list[str]) -> dict[str, float]:
    joins, key = PostgresCliAnalyticsReader._sort_clause("ctx_per_call", None, False, {})
    async with engine.acquire() as conn:
        rows = await conn.fetch(
            f"WITH sel AS (SELECT unnest($1::text[]) AS session_id) SELECT s.session_id, {key} AS k FROM sel s {joins}",  # noqa: S608
            sids,
        )
    return {r["session_id"]: r["k"] for r in rows}


async def _todays_cost(engine: AnalyticsPgEngine, sids: list[str], rates) -> dict[str, float]:
    sql, args = positional(
        f"WITH sel AS (SELECT unnest($ids::text[]) AS session_id) SELECT k.session_id, k.cost_key FROM {session_cost_key_sql()}",  # noqa: S608
        {"ids": sids, **rates_params(list(rates.values()))},
    )
    async with engine.acquire() as conn:
        return {r["session_id"]: r["cost_key"] for r in await conn.fetch(sql, *args)}


async def _todays_names(engine: AnalyticsPgEngine, sids: list[str]) -> dict[str, list[str] | None]:
    async with engine.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT sd.session_id, {CLASSIFIER_NAMES_SQL} AS n FROM session_dims sd WHERE sd.session_id = ANY($1::text[])",  # noqa: S608
            sids,
        )
    return {r["session_id"]: r["n"] for r in rows}


def _mask(counts: dict) -> int:
    kinds = kind_tokens(TokenCounts(**counts))
    return sum(bit for bit, tokens in zip(KIND_BITS.values(), kinds, strict=True) if tokens > 0)


@pytest.mark.asyncio
async def test_every_stored_column_equals_todays_statements_after_a_recompute() -> None:
    engine, seed = await _engine(), _Seed()
    try:
        async with engine.acquire() as conn:
            cases: dict[str, tuple[str, dict]] = {}
            for number, param in enumerate(PRICING_CASES):
                model, counts, _, _ = param.values
                row = (
                    model,
                    counts.get("input", 0),
                    counts.get("output", 0),
                    counts.get("cache_read", 0),
                    counts.get("cache_creation", 0),
                    counts.get("cache_5m"),
                    counts.get("cache_1h"),
                    2 if number % 2 else 0,  # odd cases have calls, even ones are zero-call sessions
                )
                sid = await seed.session(
                    conn,
                    f"c{number:02d}",
                    [row],
                    skills={"zeta": {}, "alpha": {}} if number % 3 == 0 else None,
                    commands=["/beta", 7, "alpha"] if number % 3 == 1 else None,
                )
                cases[sid] = (model, counts)
            first = next(iter(cases))
            # a subagent totals row of the same model joins the same group; its cache read counts for the ctx key
            await conn.execute(_TOTALS_SQL, first, _DAY, "claude-haiku-4-5", 7, 30)
            nocost = await seed.session(conn, "nocost")
            totals_only = await seed.session(conn, "totals-only")
            await conn.execute(_TOTALS_SQL, totals_only, _DAY, "claude-haiku-4-5", 5, 11)
            two_models = await seed.session(
                conn,
                "two-models",
                [("claude-haiku-4-5", 1, 2, 3, 0, None, None, 4), ("partial-model", 5, 0, 0, 0, None, None, 1)],
            )
        sids = list(seed.ids)
        with patch(f"{_MODULE}.llm_service.get_all_llm_model_info", return_value=[_HAIKU, _NO_1H, _PARTIAL]):
            rates = _rates({m for m, _ in cases.values()} | {"claude-haiku-4-5", "partial-model"})
            await _recompute(engine, sids, rates)
            ctx, cost, names = (
                await _todays_ctx(engine, sids),
                await _todays_cost(engine, sids, rates),
                await _todays_names(engine, sids),
            )
            mismatches = []
            for sid in sids:
                row = await _stored(engine, sid)
                assert row["rates_version"] == rates_version(rates)
                calls = row["api_call_total"]
                stored_ctx = 0 if calls == 0 else round(row["cache_read_total"] / calls, 9)
                if float(ctx[sid]) != pytest.approx(float(stored_ctx), abs=1e-9):
                    mismatches.append(("ctx", sid))
                if row["cost_key"] != cost.get(sid, 0):
                    mismatches.append(("cost", sid, row["cost_key"], cost.get(sid)))
                if row["classifier_names"] != names[sid]:
                    mismatches.append(("names", sid))
            assert mismatches == []

            # AC3: the stored key is the Python pricer's cost, rounded, on the shared case table
            reader = PostgresCliAnalyticsReader(engine)
            f = _filter(seed)
            expected = price_session_models(await reader.get_session_model_costs(f)).by_session
            for sid in cases:
                assert float((await _stored(engine, sid))["cost_key"]) == pytest.approx(
                    round(expected[sid], 9), abs=1e-9
                ), cases[sid]
        for sid, (model, counts) in cases.items():
            row = await _stored(engine, sid)
            if sid == first:  # the totals row added 7 input and 30 cache-read tokens to the same model
                counts = {**counts, "input": counts.get("input", 0) + 7, "cache_read": counts.get("cache_read", 0) + 30}
            assert row["models_used"] == [model]
            assert row["kind_masks"] == [_mask(counts)], (model, counts)
        # the sessions with a special shape
        assert (await _stored(engine, nocost))["cost_key"] == 0
        no_row = await _stored(engine, nocost)
        assert (no_row["cache_read_total"], no_row["api_call_total"]) == (0, 0)
        assert no_row["models_used"] is None and no_row["kind_masks"] is None and no_row["classifier_names"] is None
        sub = await _stored(engine, totals_only)
        assert (sub["cache_read_total"], sub["api_call_total"]) == (11, 0)
        assert sub["models_used"] == ["claude-haiku-4-5"] and sub["kind_masks"] == [
            KIND_BITS["input"] | KIND_BITS["cache_read"]
        ]
        assert sub["cost_key"] > 0
        two = await _stored(engine, two_models)
        assert two["models_used"] == ["claude-haiku-4-5", "partial-model"] and (
            two["api_call_total"],
            two["cache_read_total"],
        ) == (5, 3)
    finally:
        await seed.cleanup(engine)
        await engine.close()


def _filter(seed: _Seed):
    from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter

    return LocalAnalyticsFilter(
        scope=EXCLUDE_SCOPE,
        start_dt=(_STARTED - timedelta(days=1)).replace(tzinfo=None),
        end_dt=(_NOW + timedelta(days=1)).replace(tzinfo=None),
        users=[seed.email],
    )


@pytest.mark.asyncio
async def test_an_unchanged_recompute_writes_nothing() -> None:
    engine, seed = await _engine(), _Seed()
    try:
        async with engine.acquire() as conn:
            await seed.session(conn, "a", [("m", 10, 1, 5, 0, None, None, 2)], skills={"s": {}})
            await seed.session(conn, "b")
        rates = {"m": ModelRates(model="m", input=1e-6, output=2e-6)}
        version = rates_version(rates)
        async with engine.acquire() as conn:
            first = await update_session_sort_keys(conn, seed.ids, {seed.sid("a"): ["s"]}, rates, version)
            again = await update_session_sort_keys(conn, seed.ids, {seed.sid("a"): ["s"]}, rates, version)
            other_rates = await update_session_sort_keys(conn, seed.ids, {seed.sid("a"): ["s"]}, rates, "other")
        assert (first, again, other_rates) == (2, 0, 2)
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_a_recompute_picks_up_each_source_a_key_bit_announces() -> None:
    engine, seed = await _engine(), _Seed()
    rates = {"m": ModelRates(model="m", input=1e-6)}
    try:
        async with engine.acquire() as conn:
            sid = await seed.session(conn, "x", [("m", 1_000_000, 0, 0, 0, None, None, 1)])
            nameless = await seed.session(conn, "nameless")
        await _recompute(engine, [sid, nameless], rates, int(rollups._LOG))
        assert (await _stored(engine, sid))["cost_key"] == 1
        assert (await _stored(engine, sid))["classifier_names"] is None

        async with engine.acquire() as conn:  # a new cost_daily row
            await conn.execute(
                _COST_SQL, _DAY - timedelta(days=1), sid, seed.email, "m", 1_000_000, 0, 0, 0, None, None, 2
            )
        await _recompute(engine, [sid], rates, int(rollups._LOG))
        row = await _stored(engine, sid)
        assert (row["cost_key"], row["api_call_total"]) == (2, 3)

        async with engine.acquire() as conn:  # a subagent total, announced by _USAGE alone
            await conn.execute(_TOTALS_SQL, sid, _DAY, "m", 1_000_000, 0)
        await _recompute(engine, [sid], rates, int(rollups._USAGE))
        assert (await _stored(engine, sid))["cost_key"] == 3

        async with engine.acquire() as conn:  # a bit outside the gate does not recompute the keys
            await conn.execute(
                _COST_SQL, _DAY - timedelta(days=2), sid, seed.email, "m", 1_000_000, 0, 0, 0, None, None, 1
            )
        await _recompute(engine, [sid], rates, int(rollups._METRICS))
        assert (await _stored(engine, sid))["cost_key"] == 3

        async with engine.acquire() as conn:  # a new skill name, then changed commands
            await conn.execute("UPDATE session_dims SET skills = '{\"new-skill\": {}}' WHERE session_id = $1", sid)
        await _recompute(engine, [sid], rates, int(rollups._SESSION))
        assert (await _stored(engine, sid))["classifier_names"] == ["new-skill"]
        async with engine.acquire() as conn:
            await conn.execute("UPDATE session_dims SET commands = '[\"/c\", \"/b\"]' WHERE session_id = $1", sid)
        await _recompute(engine, [sid], rates, int(rollups._SESSION))
        assert (await _stored(engine, sid))["classifier_names"] == ["/b", "/c", "new-skill"]
        assert (await _stored(engine, nameless))["classifier_names"] is None
        assert (await _stored(engine, nameless))["cost_key"] == 0
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_a_moved_start_re_derives_the_names_of_the_invocations_it_bounds() -> None:
    engine, seed = await _engine(), _Seed()
    rates = {"m": ModelRates(model="m", input=1e-6)}
    try:
        async with engine.acquire() as conn:
            sid = await seed.session(conn, "moved", [("m", 1, 0, 0, 0, None, None, 1)])
            await conn.execute(
                "INSERT INTO invocations_hourly (hour, session_id, kind, name, calls, success) VALUES ($1, $2, 2, 'early', 1, 1)",
                _STARTED - timedelta(hours=5),
                sid,
            )
        await _recompute(engine, [sid], rates, int(rollups._LOG))
        assert (await _stored(engine, sid))["classifier_names"] is None  # before the start's hour margin

        async with engine.acquire() as conn:
            await conn.execute(
                "UPDATE session_dims SET started_at = $2 WHERE session_id = $1", sid, _STARTED - timedelta(hours=10)
            )
        await _recompute(engine, [sid], rates, int(rollups._LOG))
        assert (await _stored(engine, sid))["classifier_names"] == ["early"]
    finally:
        await seed.cleanup(engine)
        await engine.close()


# A raw name only the request-time resolution prices: its region/provider prefix is stripped to the catalog base name.
ALIAS = "us.anthropic.claude-haiku-4-5"


@pytest.mark.asyncio
async def test_a_raw_model_priced_through_an_alias_is_stored_at_the_request_time_rates() -> None:
    engine, seed = await _engine(), _Seed()
    try:
        async with engine.acquire() as conn:
            await seed.session(conn, "alias", [(ALIAS, 1_000, 200, 3_000, 400, None, None, 2)])
            await seed.session(conn, "exact", [("claude-haiku-4-5", 10, 20, 0, 0, None, None, 1)])
        with patch(f"{_MODULE}.llm_service.get_all_llm_model_info", return_value=[_HAIKU]):
            version = cli_analytics_jobs.catalog_rates_version()
            await _recompute(engine, seed.ids, cli_analytics_jobs.model_rates_by_name, version=version)
            # the request path: the window's raw names, each resolved with model_rates_for
            request = {
                m: model_rates_for(m) for m in await PostgresCliAnalyticsReader(engine).get_cost_models(_filter(seed))
            }
            assert request[ALIAS].input is not None
            today = await _todays_cost(engine, seed.ids, request)
        for sid in seed.ids:
            row = await _stored(engine, sid)
            assert (row["cost_key"], row["rates_version"]) == (today[sid], version)
            assert row["cost_key"] > 0
    finally:
        await seed.cleanup(engine)
        await engine.close()
