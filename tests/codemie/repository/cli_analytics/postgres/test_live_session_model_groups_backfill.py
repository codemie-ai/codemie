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

"""Live-Postgres proof of the backfill, the pending predicate and the separate kill switch of the stored model groups
(EPMCDME-17051, stored model groups, task 5). Runs in a private schema (its own `session_sort_keys_control` row), so
the state of the shared database does not matter. Skipped when `TEST_LOCAL_PG_URL` is not set."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest
import pytest_asyncio

from codemie.configs.config import Config
from codemie.repository.cli_analytics.postgres import migrations, session_sort_keys as keys
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.maintenance import (
    MaintenanceReport,
    PartitionMaintainer,
    RetentionPolicy,
)
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.repository.cli_analytics.postgres import stored_mode
from tests.codemie.repository.cli_analytics.postgres.test_live_session_model_groups import (
    _COST_SQL,
    _DIMS_SQL,
    _LIVE_SQL,
    _STORED_SQL,
)

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "")
pytestmark = [
    pytest.mark.skipif(not _LOCAL_PG_URL, reason="TEST_LOCAL_PG_URL is not set: no database to run against"),
    pytest.mark.asyncio,
]

_SCHEMA = "mg_bf_test"
_TODAY = datetime.now(timezone.utc).date()
_DAY = _TODAY - timedelta(days=2)
_STARTED = datetime.now(timezone.utc) - timedelta(days=2)
_VERSION = "bf-v1"
_POLICY = RetentionPolicy(raw_days=90, rollup_days=365, dedup_days=14, premake_weeks=4, session_days=180)
_CONTROL = (
    "SELECT enabled, operator_disabled, model_groups_enabled, model_groups_operator_disabled "
    "FROM session_sort_keys_control"
)
_REV = "SELECT model_groups_rev FROM session_dims WHERE session_id = $1"


def _no_rates(_names):
    return {}


@pytest_asyncio.fixture
async def env():
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
    try:
        yield engine
    finally:
        await engine.close()
        await admin.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE")
        await admin.close()


async def _session(conn, sid: str, models=("m1",), stamp: bool = True, last_event=_STARTED) -> str:
    await conn.execute(_DIMS_SQL, sid, last_event, f"{sid}@example.com")
    for i, model in enumerate(models):
        await conn.execute(_COST_SQL, _DAY - timedelta(days=i), sid, "u@example.com", model, 1, 2, 3, 4, None, None)
    if stamp:
        await keys.update_session_sort_keys(conn, [sid], {}, {}, _VERSION)
    return sid


async def _make_pending(conn, *sids: str) -> None:
    """A session stamped before the groups existed: stamp set, `model_groups_rev` NULL, no rows."""
    await conn.execute("UPDATE session_dims SET model_groups_rev = NULL WHERE session_id = ANY($1)", list(sids))
    await conn.execute("DELETE FROM session_model_groups WHERE session_id = ANY($1)", list(sids))


async def _control(conn) -> tuple:
    return tuple(await conn.fetchrow(_CONTROL))


async def test_the_pending_count_has_every_unstamped_or_unrevised_session_but_not_the_never_valid(env) -> None:
    async with env.acquire() as conn:
        await _session(conn, "valid")
        old = await _session(conn, "old-stamp")
        await _make_pending(conn, old)
        await _session(conn, "unstamped", stamp=False)
        await _session(conn, "long", models=("m1", "x" * (keys.MAX_MODEL_NAME_BYTES + 1)))
        await _session(conn, "arrays", models=[f"model-{i:04d}-{'z' * 40}" for i in range(80)])
        assert await conn.fetchval("SELECT classifier_names FROM session_dims WHERE session_id = 'arrays'") is not None

        assert await keys.count_pending_model_groups(conn) == 2  # old-stamp and unstamped
        assert await keys.count_never_valid_model_groups(conn) == 2  # long and arrays


async def test_the_backfill_converges_to_the_live_groups_and_a_rerun_writes_nothing(env) -> None:
    async with env.acquire() as conn:
        sids = [await _session(conn, f"s{i}", models=("m1", f"m{i}")) for i in range(5)]
        await _make_pending(conn, *sids)
        await conn.execute("UPDATE session_dims SET rates_version = NULL WHERE session_id = 's0'")

        result = await keys.backfill_model_groups(conn, _no_rates, _VERSION, 2)

        assert result.derived == 5 and result.batches == 3
        assert await keys.count_pending_model_groups(conn) == 0
        for sid in sids:
            assert [tuple(r) for r in await conn.fetch(_STORED_SQL, sid)] == [
                tuple(r) for r in await conn.fetch(_LIVE_SQL, sid)
            ]
            assert await conn.fetchval(_REV, sid) == keys.MODEL_GROUPS_REV
        xmin = await conn.fetch("SELECT session_id, xmin::text FROM session_model_groups ORDER BY 1, model_name")

        again = await keys.backfill_model_groups(conn, _no_rates, _VERSION, 2)

        assert again.derived == 0 and again.batches == 0
        assert (
            await conn.fetch("SELECT session_id, xmin::text FROM session_model_groups ORDER BY 1, model_name") == xmin
        )


async def test_the_backfill_is_resumable_one_batch_per_run(env) -> None:
    async with env.acquire() as conn:
        sids = [await _session(conn, f"r{i}") for i in range(5)]
        await _make_pending(conn, *sids)

        first = await keys.backfill_model_groups(conn, _no_rates, _VERSION, 2, max_batches=1)
        assert (first.derived, first.batches) == (2, 1)
        assert await keys.count_pending_model_groups(conn) == 3

        second = await keys.backfill_model_groups(conn, _no_rates, _VERSION, 2, max_batches=1)
        assert second.derived == 2
        third = await keys.backfill_model_groups(conn, _no_rates, _VERSION, 2, max_batches=1)
        assert third.derived == 1 and await keys.count_pending_model_groups(conn) == 0


async def test_never_valid_sessions_are_not_derived_and_do_not_block_the_auto_enable(env) -> None:
    async with env.acquire() as conn:
        good = await _session(conn, "good")
        await _session(conn, "long", models=("m1", "x" * (keys.MAX_MODEL_NAME_BYTES + 1)))
        await _make_pending(conn, good)

        result = await keys.backfill_model_groups(conn, _no_rates, _VERSION, 10)

        assert result.derived == 1  # not the long-named one
        assert await keys.count_pending_model_groups(conn) == 0
        assert await keys.auto_enable_model_groups(conn)
        assert (await _control(conn))[2] is True


async def test_the_auto_enable_fires_only_at_zero_pending_and_not_for_a_session_that_just_arrived(env) -> None:
    async with env.acquire() as conn:
        sid = await _session(conn, "p1")
        await _make_pending(conn, sid)
        assert not await keys.auto_enable_model_groups(conn)
        assert (await _control(conn))[2] is False

        await keys.backfill_model_groups(conn, _no_rates, _VERSION, 10)
        await _session(conn, "fresh", stamp=False, last_event=datetime.now(timezone.utc))  # in flight: ignored
        assert await keys.auto_enable_model_groups(conn)
        assert (await _control(conn))[2] is True
        assert not await keys.auto_enable_model_groups(conn)  # idempotent


async def test_disable_groups_wins_over_the_auto_enable_and_enable_groups_clears_it(env) -> None:
    async with env.acquire() as conn:
        await _session(conn, "d1")
        await keys.disable_model_groups(conn)
        assert await keys.count_pending_model_groups(conn) == 0
        assert not await keys.auto_enable_model_groups(conn)
        assert (await _control(conn))[2:] == (False, True)

        await keys.enable_model_groups(conn)
        assert (await _control(conn))[2:] == (True, False)
        await keys.disable_model_groups(conn)
        assert (await _control(conn))[2:] == (False, True)


async def test_the_groups_switch_and_the_sessions_switch_do_not_touch_each_other(env) -> None:
    async with env.acquire() as conn:
        await _session(conn, "x1")
        sessions_before = (await _control(conn))[:2]
        await keys.enable_model_groups(conn)
        await keys.disable_model_groups(conn)
        await keys.auto_enable_model_groups(conn)
        assert (await _control(conn))[:2] == sessions_before == (False, False)

        await keys.enable_session_sort_keys(conn, None)
        await keys.disable_session_sort_keys(conn)
        assert (await _control(conn))[2:] == (False, True)  # still what disable_model_groups left


async def test_the_mode_says_why_the_readers_take_the_live_arm_and_reports_the_stale_set(env) -> None:
    async with env.acquire() as conn:
        good = await _session(conn, "g1")
        mode = await stored_mode.model_groups_mode(conn)
        assert (mode.stored, mode.reason) == (False, stored_mode.GROUPS_DISABLED)

        await keys.enable_model_groups(conn)
        mode = await stored_mode.model_groups_mode(conn)
        assert (mode.stored, mode.reason, mode.stale) == (True, None, ())

        stale = await _session(conn, "g2", stamp=False)
        long = await _session(conn, "g3", models=("x" * (keys.MAX_MODEL_NAME_BYTES + 1),))
        mode = await stored_mode.model_groups_mode(conn)
        assert (mode.stored, mode.stale) == (True, (stale, long))  # the never-valid one is read live too

        assert (await stored_mode.model_groups_mode(conn, session_ids=[good, stale])).stale == (stale,)

        await keys.disable_model_groups(conn)  # operator-disabled wins whatever enabled says
        await conn.execute("UPDATE session_sort_keys_control SET model_groups_enabled = true")
        assert (await stored_mode.model_groups_mode(conn)).reason == stored_mode.GROUPS_DISABLED


async def test_a_stale_set_above_the_cap_falls_back_to_all_live(env, monkeypatch) -> None:
    monkeypatch.setattr(stored_mode, "GROUPS_S_CAP", 2)
    async with env.acquire() as conn:
        await keys.enable_model_groups(conn)
        for i in range(3):
            await _session(conn, f"c{i}", stamp=False)

        mode = await stored_mode.model_groups_mode(conn)

        assert (mode.stored, mode.reason, mode.stale) == (False, stored_mode.GROUPS_S_OVER_CAP, ())


async def test_the_pending_predicate_uses_the_partial_index(env) -> None:
    async with env.acquire() as conn:
        for i in range(3):
            await _session(conn, f"i{i}")
        await conn.execute("SET enable_seqscan = off")
        plan = "\n".join(
            r[0] for r in await conn.fetch(f"EXPLAIN SELECT session_id FROM session_dims WHERE {keys.PENDING_GROUPS}")
        )
        assert "session_dims_model_groups_pending" in plan, plan


async def test_the_cli_backfills_flips_the_groups_switch_and_reports_the_status(env) -> None:
    from codemie.repository.cli_analytics.postgres import sort_keys_cli

    parse = sort_keys_cli._parser().parse_args
    async with env.acquire() as conn:
        sids = [await _session(conn, f"cli{i}") for i in range(3)]
        await _make_pending(conn, *sids)
        await _session(conn, "cli-long", models=("x" * (keys.MAX_MODEL_NAME_BYTES + 1),))

    line = await sort_keys_cli.run(env, parse(["status"]))
    assert "model groups: pending=3 never_valid=1 enabled=False operator_disabled=False" in line

    line = await sort_keys_cli.run(
        env,
        parse(["backfill-groups", "--batch", "2", "--max-batches", "1", "--pause-ms", "0"]),
        _no_rates,
        lambda: _VERSION,
    )
    assert line.startswith("backfill-groups: 2 derived in 1 batches")
    line = await sort_keys_cli.run(
        env, parse(["backfill-groups", "--batch", "2", "--pause-ms", "0"]), _no_rates, lambda: _VERSION
    )
    assert line.startswith("backfill-groups: 1 derived")

    assert "pending=0 never_valid=1" in await sort_keys_cli.run(env, parse(["status"]))
    assert "on" in await sort_keys_cli.run(env, parse(["enable-groups"]))
    assert "enabled=True operator_disabled=False" in await sort_keys_cli.run(env, parse(["status"]))
    assert "off" in await sort_keys_cli.run(env, parse(["disable-groups"]))
    line = await sort_keys_cli.run(env, parse(["status"]))
    assert "model groups: pending=0 never_valid=1 enabled=False operator_disabled=True" in line
    assert "sessions: enabled=False operator_disabled=False" in line  # the Sessions switch is untouched
