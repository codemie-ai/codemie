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

"""Live-Postgres proof of the maintenance backfill, the re-price and the enable command of the stored session sort keys
(EPMCDME-16427, part 2).

The stored values must equal today's statements (`_todays_ctx`, `_todays_cost`, `_todays_names`) for sessions the
refresher never derived: older than raw retention, subagent-only, without a cost row. The runs are idempotent,
resumable, and safe against the refresher in either order; a re-price writes `cost_key` and the stamp together, so the
cost key of another rate set is never served (the readers require stamp == current).

Skipped when `TEST_LOCAL_PG_URL` is not set or unreachable: point it at a disposable, migrated PostgreSQL.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from codemie.repository.cli_analytics.ports import ModelRates
from codemie.repository.cli_analytics.postgres import session_sort_keys
from codemie.repository.cli_analytics.postgres.rates_version import rates_version
from codemie.repository.cli_analytics.postgres.session_sort_keys import (
    auto_enable_session_sort_keys,
    backfill_session_sort_keys,
    disable_session_sort_keys,
    enable_session_sort_keys,
    reprice_cost_keys,
    update_session_sort_keys,
)
from codemie.repository.cli_analytics.postgres.maintenance import (
    MaintenanceReport,
    PartitionMaintainer,
    RetentionPolicy,
)
from codemie.service.analytics import cli_analytics_jobs
from codemie.service.analytics.handlers.coding_agent_pricing import model_rates_for
from tests.codemie.repository.cli_analytics.postgres.test_live_session_sort_keys import (
    _COST_SQL,
    _HAIKU,
    _MODULE,
    ALIAS,
    _TOTALS_SQL,
    _engine,
    _Seed,
    _stored,
    _todays_cost,
    _todays_ctx,
    _todays_names,
    _filter,
    resolver,
)
from tests.codemie.service.analytics.handlers.test_coding_agent_pricing import _priced_model

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_LOCAL_PG_URL"), reason="TEST_LOCAL_PG_URL is not set: no database to run against"
)

_OLD_DAY = (datetime.now(timezone.utc) - timedelta(days=500)).date()  # past any raw retention
_RATES_A = {"m": ModelRates(model="m", input=1e-6, output=2e-6, cache_read=1e-7)}
_RATES_B = {"m": ModelRates(model="m", input=3e-6, output=2e-6, cache_read=1e-7)}
_BATCH = 2
_FOR_A, _FOR_B = resolver(_RATES_A), resolver(_RATES_B)
_VERSION_A, _VERSION_B = (lambda: rates_version(_RATES_A)), (lambda: rates_version(_RATES_B))


async def _drain(engine) -> None:
    """Stamp every session of the shared test database, so the sessions a test seeds are the only unstamped ones."""
    async with engine.acquire() as conn:
        await backfill_session_sort_keys(conn, _FOR_A, rates_version(_RATES_A), 500)


_CONTROL = "SELECT enabled, operator_disabled FROM session_sort_keys_control"
_RESTORE = "UPDATE session_sort_keys_control SET enabled = $1, operator_disabled = $2"


async def _control_row(engine):
    """The kill switch of the shared test database, to put back when a test that flips it is done."""
    async with engine.acquire() as conn:
        return await conn.fetchrow(_CONTROL)


async def _restore_control_row(engine, before) -> None:
    async with engine.acquire() as conn:
        await conn.execute(_RESTORE, before["enabled"], before["operator_disabled"])


class _Holds:
    """The transactions and tasks a test starts to hold row locks. Whatever is still open when the block ends, also on
    a failed assertion, is cancelled and rolled back, so the locks never block the cleanup into a hang."""

    def __init__(self) -> None:
        self._transactions: list = []
        self._tasks: list[asyncio.Task] = []

    async def __aenter__(self) -> "_Holds":
        return self

    async def begin(self, conn):
        transaction = conn.transaction()
        await transaction.start()
        self._transactions.append(transaction)
        return transaction

    def task(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._tasks.append(task)
        return task

    async def __aexit__(self, *exc) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(BaseException):  # cancelled, or failed with the error the test reports
                await task
        for transaction in reversed(self._transactions):
            with contextlib.suppress(Exception):  # one already committed has nothing to roll back
                await transaction.rollback()


async def _seed_shapes(engine, seed: _Seed) -> list[str]:
    async with engine.acquire() as conn:
        old = await seed.session(conn, "old", [("m", 100, 10, 40, 0, None, None, 2)], skills={"s1": {}})
        await conn.execute("UPDATE cost_daily SET day = $2 WHERE session_id = $1", old, _OLD_DAY)
        subagent_only = await seed.session(conn, "subagent")
        await conn.execute(_TOTALS_SQL, subagent_only, _OLD_DAY, "m", 5, 11)
        await seed.session(conn, "nocost")
        await seed.session(conn, "plain", [("m", 1_000_000, 0, 7, 0, None, None, 0)], commands=["/c"])
        await seed.session(conn, "more", [("m", 1, 2, 3, 4, None, None, 5)])
    return list(seed.ids)


async def _mismatches(engine, sids: list[str], rates) -> list:
    ctx, cost, names = (
        await _todays_ctx(engine, sids),
        await _todays_cost(engine, sids, rates),
        await _todays_names(engine, sids),
    )
    out = []
    for sid in sids:
        row = await _stored(engine, sid)
        calls = row["api_call_total"]
        stored_ctx = 0 if calls == 0 else round(row["cache_read_total"] / calls, 9)
        if row["rates_version"] != rates_version(rates):
            out.append(("stamp", sid))
        if float(ctx[sid]) != pytest.approx(float(stored_ctx), abs=1e-9):
            out.append(("ctx", sid))
        if row["cost_key"] != cost.get(sid, 0):
            out.append(("cost", sid))
        if row["classifier_names"] != names[sid]:
            out.append(("names", sid))
    return out


@pytest.mark.asyncio
async def test_the_backfill_fills_every_session_shape_with_no_mismatch_and_a_rerun_changes_nothing() -> None:
    engine, seed = await _engine(), _Seed()
    try:
        await _drain(engine)
        sids = await _seed_shapes(engine, seed)
        version = rates_version(_RATES_A)

        async with engine.acquire() as conn:
            result = await backfill_session_sort_keys(conn, _FOR_A, version, _BATCH)
        assert result.derived == len(sids) and result.batches >= 3
        assert await _mismatches(engine, sids, _RATES_A) == []
        assert (await _stored(engine, seed.sid("nocost")))["cost_key"] == 0

        async with engine.acquire() as conn:
            before = await conn.fetch(
                "SELECT session_id, xmin::text FROM session_dims WHERE session_id = ANY($1)", sids
            )
            again = await backfill_session_sort_keys(conn, _FOR_A, version, _BATCH)
            after = await conn.fetch("SELECT session_id, xmin::text FROM session_dims WHERE session_id = ANY($1)", sids)
        assert again.derived == 0 and again.batches == 0
        assert sorted(map(tuple, before)) == sorted(map(tuple, after))  # not one row version was written
        assert result.duration_s >= 0
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_an_interrupted_backfill_resumes_where_it_stopped() -> None:
    engine, seed = await _engine(), _Seed()
    try:
        await _drain(engine)
        sids = await _seed_shapes(engine, seed)
        version = rates_version(_RATES_A)
        real, calls = update_session_sort_keys, []

        async def dies_on_the_second_batch(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("pod stopped")
            return await real(*args, **kwargs)

        with patch.object(session_sort_keys, "update_session_sort_keys", dies_on_the_second_batch):
            async with engine.acquire() as conn:
                with pytest.raises(RuntimeError):
                    await backfill_session_sort_keys(conn, _FOR_A, version, _BATCH)
        stamped = [s for s in sids if (await _stored(engine, s))["rates_version"] is not None]
        assert len(stamped) == _BATCH  # the first batch committed, the second rolled back

        async with engine.acquire() as conn:
            rest = await backfill_session_sort_keys(conn, _FOR_A, version, _BATCH)
        assert rest.derived == len(sids) - _BATCH
        assert await _mismatches(engine, sids, _RATES_A) == []
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_a_changed_rate_is_re_priced_and_the_old_cost_key_is_never_current() -> None:
    engine, seed = await _engine(), _Seed()
    try:
        await _drain(engine)
        sids = await _seed_shapes(engine, seed)
        async with engine.acquire() as conn:
            await backfill_session_sort_keys(conn, _FOR_A, rates_version(_RATES_A), 500)
        priced_a = {s: (await _stored(engine, s))["cost_key"] for s in sids}
        new = rates_version(_RATES_B)

        # the catalog changed, the re-price has not run: nothing carries the new stamp, so no reader trusts a cost_key
        async with engine.acquire() as conn:
            assert (
                await conn.fetchval(
                    "SELECT count(*) FROM session_dims WHERE rates_version = $1 AND session_id = ANY($2)", new, sids
                )
                == 0
            )
            result = await reprice_cost_keys(conn, _FOR_B, new, _BATCH)
            current = await conn.fetch(
                "SELECT session_id, cost_key FROM session_dims WHERE rates_version = $1 AND session_id = ANY($2)",
                new,
                sids,
            )
        assert result.repriced >= len(sids) and result.derived == 0
        # cost_key and stamp changed together: every row stamped new holds the new price, none holds the old one
        priced_b = await _todays_cost(engine, sids, _RATES_B)
        assert {r["session_id"]: r["cost_key"] for r in current} == {s: priced_b.get(s, 0) for s in sids}
        assert priced_b[seed.sid("plain")] != priced_a[seed.sid("plain")]
        assert await _mismatches(engine, sids, _RATES_B) == []

        async with engine.acquire() as conn:
            assert (await reprice_cost_keys(conn, _FOR_B, new, _BATCH)).repriced == 0
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_the_backfill_skips_a_session_the_refresher_holds_and_the_refresher_value_stands() -> None:
    engine, seed = await _engine(), _Seed()
    version = rates_version(_RATES_A)
    try:
        await _drain(engine)
        async with engine.acquire() as backfill, engine.acquire() as refresher, _Holds() as holds:
            sid = await seed.session(backfill, "held", [("m", 1_000_000, 0, 0, 0, None, None, 1)])
            tx = await holds.begin(refresher)
            await refresher.execute(_COST_SQL, _OLD_DAY, sid, seed.email, "m", 1_000_000, 0, 0, 0, None, None, 1)
            await update_session_sort_keys(refresher, [sid], {}, _RATES_A, version)  # holds the row until commit

            result = await asyncio.wait_for(backfill_session_sort_keys(backfill, _FOR_A, version, 10), 20)
            assert result.derived == 0  # skipped, not waited for
            await tx.commit()
        row = await _stored(engine, sid)
        assert (row["rates_version"], row["api_call_total"]) == (version, 2)
        assert await _mismatches(engine, [sid], _RATES_A) == []
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_a_refresher_that_waits_for_a_backfill_batch_writes_the_complete_values() -> None:
    engine, seed = await _engine(), _Seed()
    version = rates_version(_RATES_A)
    try:
        await _drain(engine)
        async with engine.acquire() as backfill, engine.acquire() as refresher, _Holds() as holds:
            sid = await seed.session(backfill, "overlap", [("m", 1_000_000, 0, 0, 0, None, None, 1)])
            btx = await holds.begin(backfill)
            await backfill.fetchval("SELECT 1 FROM session_dims WHERE session_id = $1 FOR UPDATE", sid)
            await update_session_sort_keys(backfill, [sid], {}, _RATES_A, version)  # without the refresher's new row

            rtx = await holds.begin(refresher)
            await refresher.execute(_COST_SQL, _OLD_DAY, sid, seed.email, "m", 1_000_000, 0, 0, 0, None, None, 1)
            write = holds.task(update_session_sort_keys(refresher, [sid], {}, _RATES_A, version))
            await asyncio.sleep(0.5)
            assert not write.done(), "the refresher must wait for the row the backfill batch holds"
            await btx.commit()
            await asyncio.wait_for(write, 10)
            await rtx.commit()
        row = await _stored(engine, sid)
        assert (row["api_call_total"], row["cost_key"]) == (2, 2)
        assert await _mismatches(engine, [sid], _RATES_A) == []
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_enable_nulls_the_stamps_of_sessions_active_since_the_switch_was_cleared_then_sets_enabled() -> None:
    engine, seed = await _engine(), _Seed()
    before = await _control_row(engine)
    try:
        await _drain(engine)
        sids = await _seed_shapes(engine, seed)
        async with engine.acquire() as conn:
            await backfill_session_sort_keys(conn, _FOR_A, rates_version(_RATES_A), 500)
            since = datetime.now(timezone.utc) - timedelta(hours=1)
            await conn.execute(
                "UPDATE session_dims SET last_event_at = $2 WHERE session_id = $1",
                seed.sid("plain"),
                since + timedelta(minutes=5),
            )
            await conn.execute(
                "UPDATE session_dims SET last_event_at = $2, last_activity_day = NULL WHERE session_id = ANY($1)",
                [s for s in sids if s != seed.sid("plain")],
                since - timedelta(days=3),
            )
            await disable_session_sort_keys(conn)
            assert not await conn.fetchval("SELECT enabled FROM session_sort_keys_control")

            nulled = await enable_session_sort_keys(conn, since, batch=_BATCH)
            assert await conn.fetchval("SELECT enabled FROM session_sort_keys_control")
        assert nulled >= 1
        assert (await _stored(engine, seed.sid("plain")))["rates_version"] is None
        assert (await _stored(engine, seed.sid("old")))["rates_version"] == rates_version(_RATES_A)

        async with engine.acquire() as conn:  # the nulled sessions re-derive through the ordinary backfill
            await backfill_session_sort_keys(conn, _FOR_A, rates_version(_RATES_A), 500)
            await disable_session_sort_keys(conn)
        assert await _mismatches(engine, sids, _RATES_A) == []
    finally:
        await _restore_control_row(engine, before)
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_the_cli_commands_run_under_the_maintenance_lock_and_yield_to_a_job_that_holds_it() -> None:
    from codemie.repository.cli_analytics.postgres import sort_keys_cli

    engine, seed = await _engine(), _Seed()
    parser = sort_keys_cli._parser()
    before = await _control_row(engine)
    try:
        await _drain(engine)
        sids = await _seed_shapes(engine, seed)

        async with engine.advisory_lock(sort_keys_cli.MAINTENANCE_LOCK) as held:  # a pod's maintenance job
            assert held is not None
            assert await sort_keys_cli.run(engine, parser.parse_args(["backfill"]), _FOR_A, _VERSION_A) is None
            assert (await _stored(engine, sids[0]))["rates_version"] is None

        line = await sort_keys_cli.run(engine, parser.parse_args(["backfill", "--batch", "2"]), _FOR_A, _VERSION_A)
        assert "derived" in line and (await _stored(engine, sids[0]))["rates_version"] == rates_version(_RATES_A)
        line = await sort_keys_cli.run(engine, parser.parse_args(["reprice"]), _FOR_B, _VERSION_B)
        assert "re-priced" in line and await _mismatches(engine, sids, _RATES_B) == []
        assert "nulled" in await sort_keys_cli.run(engine, parser.parse_args(["enable", "--all"]), None)
        assert (await _stored(engine, sids[0]))["rates_version"] is None
        assert "off" in await sort_keys_cli.run(engine, parser.parse_args(["disable"]), None)
        async with engine.acquire() as conn:
            assert not await conn.fetchval("SELECT enabled FROM session_sort_keys_control")
    finally:
        await _restore_control_row(engine, before)
        await seed.cleanup(engine)
        await engine.close()


async def _alias_mismatches(engine, seed, version: str) -> list:
    """Stored cost key and stamp against today's statement priced as a request prices: each raw name of the window
    resolved with model_rates_for."""
    request = {m: model_rates_for(m) for m in await _reader(engine).get_cost_models(_filter(seed))}
    assert request[ALIAS].input is not None  # the alias is priced at request time
    today = await _todays_cost(engine, seed.ids, request)
    out = []
    for sid in seed.ids:
        row = await _stored(engine, sid)
        if (row["cost_key"], row["rates_version"]) != (today[sid], version) or not row["cost_key"] > 0:
            out.append((sid, row["cost_key"], today[sid], row["rates_version"]))
    return out


def _reader(engine):
    from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader

    return PostgresCliAnalyticsReader(engine)


@pytest.mark.asyncio
async def test_the_backfill_and_the_maintenance_re_price_price_an_alias_at_the_request_time_rates() -> None:
    engine, seed = await _engine(), _Seed()
    pricier = _priced_model("claude-haiku-4-5", 4.0e-6, 9.0e-6, 0.5e-6, 2.0e-6, 3.0e-6)
    try:
        await _drain(engine)
        async with engine.acquire() as conn:
            await seed.session(conn, "alias", [(ALIAS, 1_000, 200, 3_000, 400, None, None, 2)])
            await seed.session(conn, "exact", [("claude-haiku-4-5", 10, 20, 0, 0, None, None, 1)])
        with patch(f"{_MODULE}.llm_service.get_all_llm_model_info", return_value=[_HAIKU]):
            first = cli_analytics_jobs.catalog_rates_version()
            async with engine.acquire() as conn:
                await backfill_session_sort_keys(conn, cli_analytics_jobs.model_rates_by_name, first, 500)
            assert await _alias_mismatches(engine, seed, first) == []
            before = (await _stored(engine, seed.sid("alias")))["cost_key"]

        # the alias's catalog entry changes its rates: the version changes and the maintenance step re-prices
        with patch(f"{_MODULE}.llm_service.get_all_llm_model_info", return_value=[pricier]):
            second = cli_analytics_jobs.catalog_rates_version()
            assert second != first
            maintainer = PartitionMaintainer(engine, MagicMock())
            maintainer.set_rates(cli_analytics_jobs.catalog_rates_version, cli_analytics_jobs.model_rates_by_name)
            report = MaintenanceReport()
            async with engine.acquire() as conn:
                await maintainer._backfill_session_sort_keys(conn, report)
            assert report.sort_keys_repriced >= 2
            assert await _alias_mismatches(engine, seed, second) == []
        assert (await _stored(engine, seed.sid("alias")))["cost_key"] != before
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_enable_nulls_a_stamp_whose_activity_columns_are_both_null() -> None:
    engine, seed = await _engine(), _Seed()
    before = await _control_row(engine)
    try:
        await _drain(engine)
        sids = await _seed_shapes(engine, seed)
        async with engine.acquire() as conn:
            await backfill_session_sort_keys(conn, _FOR_A, rates_version(_RATES_A), 500)
            since = datetime.now(timezone.utc) - timedelta(hours=1)
            await conn.execute(
                "UPDATE session_dims SET last_event_at = NULL, last_activity_day = NULL WHERE session_id = $1",
                sids[0],
            )
            await enable_session_sort_keys(conn, since, batch=_BATCH)
            await disable_session_sort_keys(conn)
        assert (await _stored(engine, sids[0]))["rates_version"] is None
    finally:
        await _restore_control_row(engine, before)
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_auto_enable_fires_only_when_every_session_is_stamped_never_after_a_disable_and_is_idempotent() -> None:
    engine, seed = await _engine(), _Seed()
    version = rates_version(_RATES_A)
    try:
        await _drain(engine)
        sids = await _seed_shapes(engine, seed)
        async with engine.acquire() as conn:
            before = await conn.fetchrow(_CONTROL)
            try:
                await disable_session_sort_keys(conn)
                await conn.execute("UPDATE session_dims SET rates_version = NULL WHERE session_id = $1", sids[0])
                await conn.execute("UPDATE session_sort_keys_control SET operator_disabled = false")
                assert not await auto_enable_session_sort_keys(conn, version)  # an unstamped session
                assert not await conn.fetchval("SELECT enabled FROM session_sort_keys_control")

                await backfill_session_sort_keys(conn, _FOR_A, version, 500)
                assert not await auto_enable_session_sort_keys(conn, rates_version(_RATES_B))  # stale for this version
                assert not await conn.fetchval("SELECT enabled FROM session_sort_keys_control")

                await disable_session_sort_keys(conn)  # the operator's intent sticks although everything is stamped
                assert not await auto_enable_session_sort_keys(conn, version)
                assert not await conn.fetchval("SELECT enabled FROM session_sort_keys_control")

                await conn.execute("UPDATE session_sort_keys_control SET operator_disabled = false")
                assert await auto_enable_session_sort_keys(conn, version)  # fully stamped, no operator intent
                assert await conn.fetchval("SELECT enabled FROM session_sort_keys_control")
                assert not await auto_enable_session_sort_keys(conn, version)  # idempotent: nothing changes again

                await disable_session_sort_keys(conn)
                await enable_session_sort_keys(conn, None)  # enable clears the intent (and nulls the stamps)
                assert tuple(await conn.fetchrow(_CONTROL)) == (True, False)
            finally:
                await conn.execute(_RESTORE, before["enabled"], before["operator_disabled"])
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_the_maintenance_step_enables_the_readers_when_stamped_but_not_under_an_empty_catalog() -> None:
    engine, seed = await _engine(), _Seed()
    try:
        await _drain(engine)
        await _seed_shapes(engine, seed)
        async with engine.acquire() as conn:
            before = await conn.fetchrow(_CONTROL)
            try:
                maintainer = PartitionMaintainer(engine, MagicMock())
                with patch(f"{_MODULE}.llm_service.get_all_llm_model_info", return_value=[_HAIKU]):
                    maintainer.set_rates(
                        cli_analytics_jobs.catalog_rates_version, cli_analytics_jobs.model_rates_by_name
                    )
                    await conn.execute(_RESTORE, False, False)
                    await conn.execute("UPDATE session_dims SET rates_version = NULL")
                    await maintainer._backfill_session_sort_keys(conn, MaintenanceReport())
                    assert await conn.fetchval("SELECT enabled FROM session_sort_keys_control")
                with patch(f"{_MODULE}.llm_service.get_all_llm_model_info", return_value=[]):
                    await conn.execute(_RESTORE, False, False)
                    await maintainer._backfill_session_sort_keys(conn, MaintenanceReport())  # empty catalog: nothing
                    assert not await conn.fetchval("SELECT enabled FROM session_sort_keys_control")
            finally:
                await conn.execute(_RESTORE, before["enabled"], before["operator_disabled"])
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_enable_waits_for_a_stamp_a_writer_holds_and_nulls_it_before_the_readers_are_on() -> None:
    engine, seed = await _engine(), _Seed()
    version = rates_version(_RATES_A)
    try:
        await _drain(engine)
        async with (
            engine.acquire() as operator,
            engine.acquire() as writer,
            engine.acquire() as probe,
            _Holds() as holds,
        ):
            sid = await seed.session(operator, "held-stamp", [("m", 1_000_000, 0, 0, 0, None, None, 1)])
            await backfill_session_sort_keys(operator, _FOR_A, version, 500)
            assert (await _stored(engine, sid))["rates_version"] == version
            now = datetime.now(timezone.utc)
            await operator.execute("UPDATE session_dims SET last_event_at = $2 WHERE session_id = $1", sid, now)
            before = await operator.fetchrow(_CONTROL)
            try:
                await disable_session_sort_keys(operator)
                held = await holds.begin(writer)  # a writer of the old code: it holds the row and will not restamp it
                await writer.fetchval("SELECT 1 FROM session_dims WHERE session_id = $1 FOR UPDATE", sid)

                enable = holds.task(enable_session_sort_keys(operator, now - timedelta(hours=1), batch=500))
                await asyncio.sleep(1.0)
                assert not enable.done(), "enable must wait for the row the writer holds, not skip it"
                assert not await probe.fetchval("SELECT enabled FROM session_sort_keys_control")  # still off meanwhile

                await held.commit()
                await asyncio.wait_for(enable, 30)
                assert (await _stored(engine, sid))["rates_version"] is None  # nulled, not served stale
                assert await probe.fetchval("SELECT enabled FROM session_sort_keys_control")
            finally:
                await probe.execute(_RESTORE, before["enabled"], before["operator_disabled"])  # not `operator`: busy
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_retention_unstamps_the_sessions_whose_skill_rows_it_deletes_and_keeps_the_others_stamped() -> None:
    engine, seed = await _engine(), _Seed()
    version = rates_version(_RATES_A)
    try:
        await _drain(engine)
        async with engine.acquire() as conn:
            gone = await seed.session(conn, "ret-gone", [("m", 1_000_000, 0, 0, 0, None, None, 1)], skills={"s1": {}})
            kept = await seed.session(conn, "ret-kept", [("m", 1_000_000, 0, 0, 0, None, None, 1)], skills={"s2": {}})
            await backfill_session_sort_keys(conn, _FOR_A, version, 500)
            assert {(await _stored(engine, s))["rates_version"] for s in (gone, kept)} == {version}
            old = datetime.now(timezone.utc) - timedelta(days=400)  # past any rollup retention
            await conn.execute(
                "INSERT INTO session_skills (session_id, skill_name, updated_at) VALUES ($1, 'x', $3), ($2, 'y', now())",
                gone,
                kept,
                old,
            )

            policy = RetentionPolicy(raw_days=30, rollup_days=90, dedup_days=7, premake_weeks=1)
            report = MaintenanceReport()
            await PartitionMaintainer(engine, policy)._purge_expired_rows(
                conn, datetime.now(timezone.utc).date(), report
            )

        assert (await _stored(engine, gone))["rates_version"] is None  # its source rows are gone: not trusted any more
        assert (await _stored(engine, kept))["rates_version"] == version  # untouched
        async with engine.acquire() as conn:  # and the ordinary backfill rebuilds it
            await backfill_session_sort_keys(conn, _FOR_A, version, 500)
        assert (await _stored(engine, gone))["rates_version"] == version
    finally:
        async with engine.acquire() as conn:
            await conn.execute("DELETE FROM session_skills WHERE session_id = ANY($1::text[])", seed.ids)
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_auto_enable_ignores_sessions_that_just_arrived_but_not_older_unstamped_ones() -> None:
    engine, seed = await _engine(), _Seed()
    version = rates_version(_RATES_A)
    try:
        await _drain(engine)
        sids = await _seed_shapes(engine, seed)
        async with engine.acquire() as conn:
            before = await conn.fetchrow(_CONTROL)
            try:
                await backfill_session_sort_keys(conn, _FOR_A, version, 500)
                await conn.execute("UPDATE session_sort_keys_control SET enabled = false, operator_disabled = false")
                # ingest wrote this one a minute ago, unstamped: the next refresh derives it, the readers compute it
                await conn.execute(
                    "UPDATE session_dims SET rates_version = NULL, last_event_at = now() - interval '1 minute' "
                    "WHERE session_id = $1",
                    sids[0],
                )
                # this one has been unstamped for hours: something is wrong with it, so the switch stays off
                await conn.execute(
                    "UPDATE session_dims SET rates_version = NULL, last_event_at = now() - interval '3 hours' "
                    "WHERE session_id = $1",
                    sids[1],
                )
                assert not await auto_enable_session_sort_keys(conn, version)
                await conn.execute("UPDATE session_dims SET rates_version = $2 WHERE session_id = $1", sids[1], version)
                assert await auto_enable_session_sort_keys(conn, version)
            finally:
                await conn.execute(_RESTORE, before["enabled"], before["operator_disabled"])
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_auto_enable_nulls_the_stamps_of_recently_active_sessions_before_it_switches_on() -> None:
    engine, seed = await _engine(), _Seed()
    version = rates_version(_RATES_A)
    try:
        await _drain(engine)
        sids = await _seed_shapes(engine, seed)
        async with engine.acquire() as conn:
            before = await conn.fetchrow(_CONTROL)
            try:
                await backfill_session_sort_keys(conn, _FOR_A, version, 500)
                await conn.execute("UPDATE session_sort_keys_control SET enabled = false, operator_disabled = false")
                # a pod on the previous code rewrote this session's rollups a few minutes ago, leaving its stamp
                await conn.execute(
                    "UPDATE session_dims SET last_event_at = now() - interval '5 minutes', "
                    "last_activity_day = (now() AT TIME ZONE 'UTC')::date WHERE session_id = $1",
                    sids[0],
                )
                await conn.execute(
                    "UPDATE session_dims SET last_event_at = now() - interval '30 hours', "
                    "last_activity_day = ((now() - interval '30 hours') AT TIME ZONE 'UTC')::date "
                    "WHERE session_id = $1",
                    sids[1],
                )
                assert await auto_enable_session_sort_keys(conn, version)
                assert (await _stored(engine, sids[0]))["rates_version"] is None  # re-derived by the next run
                assert (await _stored(engine, sids[1]))["rates_version"] == version  # outside the sweep window
            finally:
                await conn.execute(_RESTORE, before["enabled"], before["operator_disabled"])
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_a_session_the_database_refuses_is_skipped_and_the_backfill_goes_on() -> None:
    import asyncpg

    engine, seed = await _engine(), _Seed()
    version = rates_version(_RATES_A)
    real = session_sort_keys.update_session_sort_keys
    try:
        await _drain(engine)
        sids = await _seed_shapes(engine, seed)
        bad = sids[2]

        async def refuse(conn, ids, *args, **kwargs):
            if bad in ids:
                raise asyncpg.exceptions.ProgramLimitExceededError("index row size exceeds the btree maximum")
            return await real(conn, ids, *args, **kwargs)

        async with engine.acquire() as conn:
            before = await conn.fetchrow(_CONTROL)
            try:
                await conn.execute(
                    "UPDATE session_dims SET rates_version = NULL WHERE session_id = ANY($1::text[])", sids
                )
                with patch.object(session_sort_keys, "update_session_sort_keys", refuse):
                    result = await backfill_session_sort_keys(conn, _FOR_A, version, 5)  # one batch holds them all
                assert result.derived == len(sids) - 1
                for sid in sids:
                    expected = None if sid == bad else version
                    assert (await _stored(engine, sid))["rates_version"] == expected, sid
                # marked like an oversized session: computed live, no longer pending, so nothing holds the enable back
                assert (await _stored(engine, bad))["classifier_names"] == [session_sort_keys.OVERSIZED_MARK]
                with patch.object(session_sort_keys, "update_session_sort_keys", refuse):
                    assert (await backfill_session_sort_keys(conn, _FOR_A, version, 5)).derived == 0
                await conn.execute("UPDATE session_sort_keys_control SET enabled = false, operator_disabled = false")
                assert await auto_enable_session_sort_keys(conn, version)
            finally:
                await conn.execute(_RESTORE, before["enabled"], before["operator_disabled"])
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_a_session_with_an_oversized_name_set_is_marked_not_stored_and_blocks_nothing() -> None:
    engine, seed = await _engine(), _Seed()
    version = rates_version(_RATES_A)
    try:
        await _drain(engine)
        sids = await _seed_shapes(engine, seed)
        async with engine.acquire() as conn:
            names = {f"skill-{i:03d}-" + "x" * 60: {} for i in range(60)}
            big = await seed.session(conn, "big", [("m", 10, 1, 1, 0, None, None, 1)], skills=names)
        async with engine.acquire() as conn:
            before = await conn.fetchrow(_CONTROL)
            try:
                await conn.execute(
                    "UPDATE session_dims SET rates_version = NULL WHERE session_id = ANY($1::text[])", [*sids, big]
                )
                result = await backfill_session_sort_keys(conn, _FOR_A, version, 500)
                stored = await _stored(engine, big)
                assert stored["rates_version"] is None  # not trusted: the readers compute it live
                assert stored["classifier_names"] == [session_sort_keys.OVERSIZED_MARK]
                assert stored["models_used"] is None
                for sid in sids:
                    assert (await _stored(engine, sid))["rates_version"] == version, sid
                # not picked again, so a second run has nothing to do, and the automatic enable is not held back
                assert (await backfill_session_sort_keys(conn, _FOR_A, version, 500)).derived == 0
                await conn.execute("UPDATE session_sort_keys_control SET enabled = false, operator_disabled = false")
                assert await auto_enable_session_sort_keys(conn, version)
                assert result.derived >= len(sids)
            finally:
                await conn.execute(_RESTORE, before["enabled"], before["operator_disabled"])
    finally:
        await seed.cleanup(engine)
        await engine.close()


@pytest.mark.asyncio
async def test_auto_enable_waits_while_a_connection_of_the_previous_code_is_open() -> None:
    import asyncpg

    from codemie.repository.cli_analytics.postgres.settings import LEGACY_APPLICATION_NAME

    engine, seed = await _engine(), _Seed()
    version = rates_version(_RATES_A)
    try:
        await _drain(engine)
        await _seed_shapes(engine, seed)
        async with engine.acquire() as conn:
            before = await conn.fetchrow(_CONTROL)
            try:
                await backfill_session_sort_keys(conn, _FOR_A, version, 500)
                await conn.execute("UPDATE session_sort_keys_control SET enabled = false, operator_disabled = false")
                old_pod = await asyncpg.connect(
                    os.environ["TEST_LOCAL_PG_URL"], server_settings={"application_name": LEGACY_APPLICATION_NAME}
                )
                try:
                    assert not await auto_enable_session_sort_keys(conn, version)  # the old pod is still connected
                    assert not await conn.fetchval("SELECT enabled FROM session_sort_keys_control")
                finally:
                    await old_pod.close()
                for _ in range(50):  # the server drops the closed connection from its activity view a moment later
                    if not await conn.fetchval(
                        "SELECT count(*) FROM pg_stat_activity WHERE application_name = $1", LEGACY_APPLICATION_NAME
                    ):
                        break
                    await asyncio.sleep(0.1)
                assert await auto_enable_session_sort_keys(conn, version)  # replaced: it switches on by itself
            finally:
                await conn.execute(_RESTORE, before["enabled"], before["operator_disabled"])
    finally:
        await seed.cleanup(engine)
        await engine.close()
