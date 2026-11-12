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

"""Live-Postgres proof that the money statements of /cost and /users read the stored model groups when they may and
the live groups otherwise, with the same `GroupedMoney` either way (EPMCDME-17051, stored model groups, task 6).
Runs in a private schema (its own `session_sort_keys_control` row), so the state of the shared database does not
matter. Skipped when `TEST_LOCAL_PG_URL` is not set or unreachable."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import patch

import asyncpg
import pytest
import pytest_asyncio

from codemie.configs.config import Config
from codemie.repository.cli_analytics.postgres import migrations, reader as reader_module, session_sort_keys as keys
from codemie.repository.cli_analytics.postgres import stored_mode
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.maintenance import (
    MaintenanceReport,
    PartitionMaintainer,
    RetentionPolicy,
)
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.service.analytics.handlers.coding_agent_pricing import model_rates_for
from tests.codemie.repository.cli_analytics.postgres.test_live_grouped_cost import _GroupedSeed, _seed_all
from tests.codemie.repository.cli_analytics.postgres.live_helpers import _MODULE
from tests.codemie.service.analytics.handlers.test_coding_agent_pricing import _HAIKU, _NO_1H, _PARTIAL

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = [
    pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against"),
    pytest.mark.asyncio,
]

_SCHEMA = "mg_money_test"
_TODAY = datetime.now(timezone.utc).date()
_POLICY = RetentionPolicy(raw_days=90, rollup_days=365, dedup_days=14, premake_weeks=4, session_days=180)
_VERSION = "money-v1"


@pytest_asyncio.fixture
async def env():
    """A private schema with the seeded sessions of the parity test, all stamped, the groups switch on."""
    settings = AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL, CLI_ANALYTICS_PG_SCHEMA=_SCHEMA))
    try:
        admin = await asyncpg.connect(_LOCAL_PG_URL)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")
    await admin.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
    migrations.run_migrations(settings)
    engine = AnalyticsPgEngine(settings)
    async with engine.acquire() as conn:
        await PartitionMaintainer(engine, _POLICY, clock=lambda: _TODAY)._ensure_partitions(
            conn, _TODAY, MaintenanceReport()
        )
    seed = _GroupedSeed()
    try:
        await _seed_all(engine, seed)
        async with engine.acquire() as conn:
            await keys.update_session_sort_keys(conn, seed.sids, {}, {}, _VERSION)
            await keys.enable_model_groups(conn)
            models = [r["model_name"] for r in await conn.fetch("SELECT DISTINCT model_name FROM cost_daily")]
        with patch(f"{_MODULE}.llm_service.get_all_llm_model_info", return_value=[_HAIKU, _NO_1H, _PARTIAL]):
            rates = [model_rates_for(m) for m in sorted(models)]
        yield engine, seed, rates
    finally:
        await engine.close()
        await admin.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
        await admin.close()


class _Statements:
    """Wraps `_fetch` of a reader and records the statement text and parameters of each call."""

    def __init__(self, reader: PostgresCliAnalyticsReader) -> None:
        self.calls: list[tuple[str, dict]] = []
        inner = reader._fetch

        async def record(conn, sql, params):
            self.calls.append((sql, dict(params)))
            return await inner(conn, sql, params)

        reader._fetch = record  # type: ignore[method-assign]

    @property
    def stored(self) -> bool:
        (sql, _), *_ = self.calls[-1:]
        return "session_model_groups" in sql


async def _money(engine, seed, rates, how: str, s_cap: int | None = None):
    reader = PostgresCliAnalyticsReader(engine) if s_cap is None else PostgresCliAnalyticsReader(engine, s_cap=s_cap)
    spy = _Statements(reader)
    f = seed.filter()
    if how == "cost":
        result = await reader.get_cost_money(f, rates)
    else:
        result = await reader.get_users_money_page(f, rates, 3, 0)
    return result, spy


async def _live_money(engine, seed, rates, how: str):
    async with engine.acquire() as conn:
        await keys.disable_model_groups(conn)
    try:
        result, spy = await _money(engine, seed, rates, how)
    finally:
        async with engine.acquire() as conn:
            await keys.enable_model_groups(conn)
    assert not spy.stored
    return result


@pytest.mark.parametrize("how", ["cost", "users"])
async def test_the_stored_statement_returns_the_money_of_the_live_one(env, how) -> None:
    engine, seed, rates = env

    stored, spy = await _money(engine, seed, rates, how)

    assert spy.stored
    assert stored == await _live_money(engine, seed, rates, how)
    assert stored.groups if how == "cost" else stored.money.groups  # the comparison is not of two empty results


@pytest.mark.parametrize("how", ["cost", "users"])
async def test_sessions_without_valid_groups_are_read_live_and_merged(env, how) -> None:
    engine, seed, rates = env
    async with engine.acquire() as conn:
        # an unstamped session with a leftover stale row, an old revision and a session that was never stamped
        victims = sorted(seed.sids)[:3]
        await conn.execute("UPDATE session_dims SET rates_version = NULL WHERE session_id = $1", victims[0])
        await conn.execute("UPDATE session_dims SET model_groups_rev = NULL WHERE session_id = $1", victims[1])
        await conn.execute(
            "UPDATE session_model_groups SET input_tokens = input_tokens + 777 WHERE session_id = $1", victims[2]
        )
        await conn.execute("UPDATE session_dims SET model_groups_rev = 0 WHERE session_id = $1", victims[2])

    stored, spy = await _money(engine, seed, rates, how)

    assert spy.stored
    assert set(spy.calls[-1][1]["stale"]) == set(victims)
    assert stored == await _live_money(engine, seed, rates, how)


@pytest.mark.parametrize("how", ["cost", "users"])
@pytest.mark.parametrize("reason", ["operator_disabled", "never_enabled", "over_cap", "read_error"])
async def test_the_live_form_is_used_when_the_stored_one_may_not_be(env, how, reason, monkeypatch) -> None:
    engine, seed, rates = env
    s_cap = None
    expected = await _live_money(engine, seed, rates, how)
    async with engine.acquire() as conn:
        if reason == "operator_disabled":
            await keys.disable_model_groups(conn)
        elif reason == "never_enabled":
            await conn.execute("UPDATE session_sort_keys_control SET model_groups_enabled = false")
        elif reason == "over_cap":
            await conn.execute("UPDATE session_dims SET model_groups_rev = NULL")
            s_cap = 1
    if reason == "read_error":
        inner = reader_module.model_groups_mode

        async def broken(conn, *args, **kwargs):
            await conn.fetchval("SELECT 1/0")  # a failed statement: aborts the transaction unless it is isolated
            return await inner(conn, *args, **kwargs)

        monkeypatch.setattr(reader_module, "model_groups_mode", broken)
    stored_mode.FALLBACK_WARNINGS.reset()

    with patch.object(stored_mode, "logger") as logger:
        actual, spy = await _money(engine, seed, rates, how, s_cap)

    assert not spy.stored
    assert "stale" not in spy.calls[-1][1]
    assert actual == expected
    if reason in ("over_cap", "read_error"):
        wanted = {"over_cap": stored_mode.GROUPS_S_OVER_CAP, "read_error": stored_mode.GROUPS_READ_ERROR}
        (message,) = [c.args[0] for c in logger.warning.call_args_list]
        assert "stored model groups" in message and f"reason={wanted[reason]}" in message


async def test_a_request_narrowed_to_some_sessions_binds_only_theirs(env) -> None:
    engine, seed, rates = env
    ids = sorted(seed.sids)
    async with engine.acquire() as conn:
        await conn.execute("UPDATE session_dims SET rates_version = NULL WHERE session_id = ANY($1)", ids[:4])
    reader = PostgresCliAnalyticsReader(engine)
    spy = _Statements(reader)
    f = seed.filter()
    narrowed = f.restricted_to(ids[2:6])

    await reader.get_cost_money(narrowed, rates)

    assert spy.stored
    assert set(spy.calls[-1][1]["stale"]) == set(ids[2:4])
