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

"""Live-Postgres checks for EPMCDME-16441: `get_session_cost_facts(include_costless=True)` lists sessions
without any cost_daily row, and leaves the default (inner-join) path alone.

Skipped when the local analytics Postgres named by `TEST_LOCAL_PG_URL` is unreachable.
"""

import copy
import json
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.rest_api.routers import cli_analytics as cli_router
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler
from tests.codemie.repository.cli_analytics.postgres.test_live_session_subagent_cost import (
    _COST_MODEL,
    _DAY,
    _KNOWN_MODEL,
    _START,
    _USAGE,
    _cleanup,
    _get_engine,
    _lookup,
    _refresh,
    _seed_requests,
    _seed_start,
    _seed_totals,
    _window,
)
from tests.codemie.repository.cli_analytics.support.scopes import DETAIL_SCOPE

_PRICED = "test-epmcdme-16441-costless-priced"
_COSTLESS = "test-epmcdme-16441-costless-none"
_PRICED2 = _PRICED + "-2"
_COSTLESS2 = _COSTLESS + "-2"
_DETAIL = "test-epmcdme-16441-costless-detail"  # its own id: xdist runs the tests of this file in parallel


async def _seed(engine, with_costless: bool, costless_id: str = _COSTLESS, priced_id: str = _PRICED) -> None:
    await _cleanup(engine, priced_id)
    await _cleanup(engine, costless_id)
    await _seed_start(engine, priced_id)
    await _seed_requests(engine, priced_id)
    if with_costless:
        await _seed_start(engine, costless_id)
        # A real prompt with no cost/usage.request event, not a zero-activity session: agent.prompt.submit
        # rolls up into turns_daily, so the session is still admitted to `sel`.
        async with engine.acquire() as conn:
            await conn.execute(
                "INSERT INTO hook_events (ts, session_id, event_type) VALUES ($1, $2, 'agent.prompt.submit')",
                _START,
                costless_id,
            )
    await _refresh(engine)


@pytest.mark.asyncio
async def test_costless_session_listed_with_null_cost_only_when_opted_in():
    engine = await _get_engine()
    try:
        await _seed(engine, with_costless=True)
        reader = PostgresCliAnalyticsReader(engine)
        default = {r["session_id"]: r for r in await reader.get_session_cost_facts(_window())}
        relaxed = {r["session_id"]: r for r in await reader.get_session_cost_facts(_window(), include_costless=True)}

        assert _COSTLESS not in default
        assert _COSTLESS in relaxed
        assert relaxed[_COSTLESS]["cost_usd"] is None
        assert relaxed[_COSTLESS]["started_at"] is not None
        assert relaxed[_PRICED]["cost_usd"] == pytest.approx(0.10)
    finally:
        await _cleanup(engine, _PRICED)
        await _cleanup(engine, _COSTLESS)
        await engine.close()


@pytest.mark.asyncio
async def test_priced_facts_and_money_rows_are_unchanged_by_a_costless_session():
    engine = await _get_engine()
    try:
        reader = PostgresCliAnalyticsReader(engine)
        await _seed(engine, False, _COSTLESS2, _PRICED2)
        before = (
            [r for r in await reader.get_session_cost_facts(_window()) if r["session_id"] == _PRICED2],
            [r for r in await reader.get_session_model_costs(_window()) if r["session_id"] == _PRICED2],
        )
        await _seed(engine, True, _COSTLESS2, _PRICED2)
        after = (
            [r for r in await reader.get_session_cost_facts(_window()) if r["session_id"] == _PRICED2],
            [r for r in await reader.get_session_model_costs(_window()) if r["session_id"] == _PRICED2],
        )
        assert before == after
        relaxed = [
            r
            for r in await reader.get_session_cost_facts(_window(), include_costless=True)
            if r["session_id"] == _PRICED2
        ]
        assert relaxed == before[0]
    finally:
        await _cleanup(engine, _PRICED2)
        await _cleanup(engine, _COSTLESS2)
        await engine.close()


@pytest.mark.asyncio
async def test_costless_session_detail_resolves_instead_of_404():
    engine = await _get_engine()
    try:
        await _seed(engine, with_costless=True, costless_id=_DETAIL, priced_id=_DETAIL + '-p')
        reader = PostgresCliAnalyticsReader(engine)
        meta = await reader.get_session_detail_meta(_DETAIL, scope=DETAIL_SCOPE)
        assert [r["session_id"] for r in meta] == [_DETAIL]
        assert await reader.get_session_detail_model_costs(_DETAIL, scope=DETAIL_SCOPE) == []
        handler = LocalAnalyticsHandler(reader)
        detail, _, _ = await handler.get_session_detail(_DETAIL, scope=DETAIL_SCOPE)
        assert detail is not None
        assert detail["cost_usd"] == 0.0
        assert detail["cost_available"] is False
    finally:
        await _cleanup(engine, _DETAIL + '-p')
        await _cleanup(engine, _DETAIL)
        await engine.close()


@pytest.mark.asyncio
async def test_kpis_count_a_costless_session_while_priced_aggregates_are_unchanged():
    # EPMCDME-16496: get_cost_kpis counts every session of `sel`, priced or not (the same universe
    # get_session_cost_facts(include_costless=True) already lists), so total_sessions grows by one
    # when a costless session is added; every money/token sum stays the same since it contributes zero.
    engine = await _get_engine()
    priced, costless = _PRICED + "-kpi", _COSTLESS + "-kpi"
    try:
        reader = PostgresCliAnalyticsReader(engine)
        # Only this test's two fixture sessions: other workers write to the same window concurrently.
        own = _window().restricted_to([priced, costless])
        await _seed(engine, False, costless, priced)
        kpis_before = await reader.get_cost_kpis(own)
        priced_before = [
            r for r in await reader.get_session_cost_facts(own, include_costless=True) if r["session_id"] == priced
        ]
        await _seed(engine, True, costless, priced)
        kpis_after = await reader.get_cost_kpis(own)
        facts = await reader.get_session_cost_facts(own, include_costless=True)

        assert kpis_after[0]["total_sessions"] == kpis_before[0]["total_sessions"] + 1
        assert {k: v for k, v in kpis_after[0].items() if k != "total_sessions"} == {
            k: v for k, v in kpis_before[0].items() if k != "total_sessions"
        }
        assert [r for r in facts if r["session_id"] == priced] == priced_before
        assert any(r["session_id"] == costless for r in facts)

        # the seeded request carries no model, so the priced session gets one request of a catalog-priced model
        async with engine.acquire() as conn:
            await conn.execute(
                "INSERT INTO log_events (ts, session_id, event_kind, model, cost_usd, input_tokens, output_tokens,"
                " severity, resource_id) VALUES ($1, $2, 1, $3, 0.01, 10, 5, 9, 1)",
                _START + timedelta(seconds=30),
                priced,
                _KNOWN_MODEL,
            )
        await _refresh(engine)
        with patch("codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config", _lookup):
            result, _, _ = await LocalAnalyticsHandler(reader).get_sessions(own, 0, 1000, "start_time", None)
        by_id = {s["trace_id"]: s for s in result["sessions"]}
        assert by_id[costless]["cost_available"] is False
        assert by_id[costless]["cost_usd"] == 0.0
        assert by_id[priced]["cost_available"] is True
    finally:
        await _cleanup(engine, priced)
        await _cleanup(engine, costless)
        await engine.close()


@pytest.mark.asyncio
async def test_kpis_and_users_sums_are_zero_not_null_when_the_window_is_entirely_costless():
    # CR-002: when every session of a window is costless, sum(c.*) over an all-NULL joined group
    # returns SQL NULL, not the 0 get_cost_kpis's own docstring promises -- same for get_users.
    engine = await _get_engine()
    costless = _COSTLESS + "-allcostless"
    try:
        await _cleanup(engine, costless)
        await _seed_start(engine, costless)
        async with engine.acquire() as conn:
            await conn.execute(
                "INSERT INTO hook_events (ts, session_id, event_type) VALUES ($1, $2, 'agent.prompt.submit')",
                _START,
                costless,
            )
        await _refresh(engine)
        reader = PostgresCliAnalyticsReader(engine)
        own = _window().restricted_to([costless])

        kpis = (await reader.get_cost_kpis(own))[0]
        for key in (
            "total_input_tokens",
            "total_output_tokens",
            "total_cache_read_tokens",
            "total_cache_creation_tokens",
            "total_api_calls",
        ):
            assert kpis[key] == 0, f"{key} should be 0, not {kpis[key]!r}, for an all-costless window"

        users = await reader.get_users(own)
        assert len(users) == 1
        for key in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens"):
            assert users[0][key] == 0, f"{key} should be 0, not {users[0][key]!r}, for an all-costless developer"
    finally:
        await _cleanup(engine, costless)
        await engine.close()


@pytest.mark.asyncio
async def test_session_whose_only_model_is_missing_from_the_catalog_is_cost_available_false():
    """A session with model rows but no catalog price is not free: list and detail answer false and $0.0."""
    engine = await _get_engine()
    unpriced, priced = "test-epmcdme-16441-unpriced-model", "test-epmcdme-16441-unpriced-model-ok"
    try:
        for sid in (unpriced, priced):
            await _cleanup(engine, sid)
            await _seed_start(engine, sid)
        await _seed_totals(engine, unpriced, [{**_USAGE, "model": "not-a-real-model-xyz"}])
        await _seed_totals(engine, priced, [_USAGE])
        await _refresh(engine)

        reader = PostgresCliAnalyticsReader(engine)
        handler = LocalAnalyticsHandler(reader)
        with patch("codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config", _lookup):
            result, _, _ = await handler.get_sessions(_window(), 0, 1000, "start_time", None)
            unpriced_detail, _, _ = await handler.get_session_detail(unpriced, scope=DETAIL_SCOPE)
            priced_detail, _, _ = await handler.get_session_detail(priced, scope=DETAIL_SCOPE)
        by_id = {s["trace_id"]: s for s in result["sessions"]}

        assert (by_id[unpriced]["cost_available"], by_id[unpriced]["cost_usd"]) == (False, 0.0)
        assert (unpriced_detail["cost_available"], unpriced_detail["cost_usd"]) == (False, 0.0)
        assert by_id[priced]["cost_available"] is True
        assert priced_detail["cost_available"] is True
    finally:
        await _cleanup(engine, unpriced)
        await _cleanup(engine, priced)
        await engine.close()


_ALICE, _BOB = "alice-15517@example.com", "bob-15517@example.com"


async def _request(engine, session_id: str, cost: float, model: str | None) -> None:
    async with engine.acquire() as conn:
        await conn.execute(
            "INSERT INTO log_events (ts, session_id, event_kind, model, cost_usd, input_tokens, output_tokens,"
            " severity, resource_id) VALUES ($1, $2, 1, $3, $4, 10, 5, 9, 1)",
            _START + timedelta(seconds=40),
            session_id,
            model,
            cost,
        )


@pytest.mark.asyncio
async def test_overview_and_users_count_the_activity_session_universe():
    """EPMCDME-15517: Activity, Overview and Users agree on every selected session, with or without cost rows."""
    engine = await _get_engine()
    ids = {k: f"test-epmcdme-15517-{k}" for k in ("priced", "zero", "multi", "costless", "private")}
    try:
        for sid in ids.values():
            await _cleanup(engine, sid)
            await _seed_start(engine, sid)
        await _seed_requests(engine, ids["priced"])  # one cost row, 0.10
        await _request(engine, ids["zero"], 0.0, _KNOWN_MODEL)  # a cost row with $0 still counts
        await _request(engine, ids["multi"], 0.02, _KNOWN_MODEL)
        await _request(engine, ids["multi"], 0.03, _COST_MODEL)  # two cost rows: one session
        await _refresh(engine)
        async with engine.acquire() as conn:
            for key, email in (("priced", _ALICE), ("zero", _ALICE), ("multi", _ALICE), ("costless", _BOB)):
                await conn.execute("UPDATE session_dims SET jwt_email = $2 WHERE session_id = $1", ids[key], email)
            # a personal, cost-less session of bob is outside the viewer's scope (exclude): it must stay uncounted
            await conn.execute(
                "UPDATE session_dims SET subscription_type = 'corporate' WHERE session_id = $1", ids["costless"]
            )
            await conn.execute(
                "UPDATE session_dims SET jwt_email = $2, subscription_type = 'personal', project_origin = 'personal'"
                " WHERE session_id = $1",
                ids["private"],
                _BOB,
            )
        # the window admits a session with an activity signal (a lifecycle-only start is not a session): the
        # cost-less sessions did something, only nothing that costs money
        for key in ("costless", "private"):
            await _seed_derived(engine, ids[key], 1, 0, 0, 0, _START + timedelta(seconds=30))

        reader = PostgresCliAnalyticsReader(engine)
        handler = LocalAnalyticsHandler(reader)
        own = _window().restricted_to(list(ids.values()))
        with patch("codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config", _lookup):
            activity = await handler.get_activity(own)
            overview, _, _ = await handler.get_overview(own)
            cost, _ = await handler.get_cost(own)
            users, _ = await handler.get_users(own, None, None)
        by_user = {r["developer_name"]: r for r in users["rows"]}

        assert sum(activity["by_hour"]) == 4
        assert overview["kpis"]["total_sessions"] == 4
        assert cost["kpis"]["total_sessions"] == 4
        # the class lists describe the same sessions as the count: the cost-less session's type is listed
        assert set(overview["kpis"]["subscription_types"]) == {"corporate", "unknown"}
        assert set(cost["kpis"]["subscription_types"]) == {"corporate", "unknown"}
        # avg cost is per spend session (the three that have cost rows), never per costless one
        assert cost["kpis"]["avg_cost_per_session"] == pytest.approx(cost["kpis"]["total_cost_usd"] / 3)
        assert sum(r["session_count"] for r in users["rows"]) == 4
        assert by_user[_ALICE]["session_count"] == 3
        assert by_user[_ALICE]["cost_usd"] == pytest.approx(overview["kpis"]["total_cost_usd"])
        assert overview["kpis"]["total_cost_usd"] > 0
        assert by_user[_ALICE]["input_tokens"] == 1000 + 10 + 20  # the zero-cost and both multi rows, once each
        assert by_user[_BOB]["session_count"] == 1  # a costless-only user is listed
        assert (by_user[_BOB]["cost_usd"], by_user[_BOB]["input_tokens"], by_user[_BOB]["top_model"]) == (0.0, 0, None)

        # the scoped-out session is in none of the counts above (4, not 5); an unknown id and deny-all select nothing
        nobody = own.restricted_to(["test-epmcdme-15517-none"])
        denied = copy.copy(own)
        denied.deny_all = True
        for empty in (nobody, denied):
            assert (await handler.get_users(empty, None, None))[0]["rows"] == []
            assert (await reader.get_cost_kpis(empty))[0]["total_sessions"] == 0
            assert sum((await handler.get_activity(empty))["by_hour"]) == 0
    finally:
        for sid in ids.values():
            await _cleanup(engine, sid)
        await _drop_derived(engine, list(ids.values()))
        await engine.close()


class _Filters:
    """Stands in for the FilterParams dependency: resolves to the fixture's filter."""

    def __init__(self, f) -> None:
        self._f = f

    async def resolve(self, _user):
        return self._f


async def _served(handler, f) -> dict:
    """Overview and Users as the router functions serve them. Not covered: auth, the admin gate, FilterParams
    parsing, and the user-id lookup (stubbed)."""
    with (
        patch.object(cli_router, "_handler", return_value=handler),
        patch.object(cli_router, "_ensure_enabled"),
        patch.object(cli_router, "_attach_user_ids", new=AsyncMock()),
        patch("codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config", _lookup),
    ):
        overview = await cli_router.get_overview(user=None, filters=_Filters(f))
        users = await cli_router.get_users(user=None, filters=_Filters(f), page=None, per_page=None)
    return {"overview": json.loads(overview.body), "users": json.loads(users.body)}


async def _seed_derived(engine, session_id: str, turns: int, calls: int, ok: int, files: int, last_event) -> None:
    """Rollup rows a real session of this shape would have, written after the refresh so it cannot replace them."""
    async with engine.acquire() as conn:
        await conn.execute("INSERT INTO turns_daily VALUES ($1, $2, $3)", _DAY, session_id, turns)
        await conn.execute("INSERT INTO tool_facts_daily VALUES ($1, $2, $3, 0, 0)", _DAY, session_id, calls)
        await conn.execute(
            "INSERT INTO invocations_hourly VALUES ($1, $2, 1, 'Edit', $3, $4)",
            _START.replace(minute=0),
            session_id,
            calls,
            ok,
        )
        for n in range(files):
            await conn.execute(
                "INSERT INTO session_files_daily VALUES ($1, $2, $3, true, false)", _DAY, session_id, f"f{n}.py"
            )
        await conn.execute("UPDATE session_dims SET last_event_at = $2 WHERE session_id = $1", session_id, last_event)


async def _drop_derived(engine, session_ids: list[str]) -> None:
    async with engine.acquire() as conn:
        for table in ("turns_daily", "tool_facts_daily", "session_files_daily", "invocations_hourly"):
            await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1)", session_ids)  # noqa: S608


@pytest.mark.asyncio
@pytest.mark.parametrize("with_priced", [False, True])
async def test_efficiency_includes_costless_activity_without_changing_spend_metrics(with_priced):
    engine = await _get_engine()
    costless, priced = "test-15517-eff-aaa-costless", "test-15517-eff-zzz-priced"
    ids = [costless, priced]
    try:
        for sid in ids:
            await _cleanup(engine, sid)
            await _seed_start(engine, sid)
        if with_priced:
            await _request(engine, priced, 0.05, _COST_MODEL)
        await _refresh(engine)
        await _seed_derived(engine, costless, 3, 1, 1, 1, _START + timedelta(seconds=30))
        reader = PostgresCliAnalyticsReader(engine)
        handler = LocalAnalyticsHandler(reader)
        own = _window().restricted_to(ids)
        with patch("codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config", _lookup):
            efficiency, _ = await handler.get_efficiency(own)
            overview, _, _ = await handler.get_overview(own)
            cost, _ = await handler.get_cost(own)
        assert sum(r["count"] for r in efficiency["session_depth"]) == 1 + with_priced
        assert next(r["count"] for r in efficiency["session_depth"] if r["bucket"] == "2-5") == 1
        assert efficiency["code_changes"]["files_changed"] == overview["kpis"]["total_files_changed"] == 1
        assert efficiency["code_changes"]["files_written"] == 1
        assert efficiency["kpis"]["total_cost_usd"] == pytest.approx(cost["kpis"]["total_cost_usd"])
        assert efficiency["dead_sessions"]["count"] == int(with_priced)
        assert efficiency["dead_sessions"]["pct_of_sessions"] == (100 if with_priced else 0)
        assert efficiency["kpis"]["worst_session_trace_id"] == (priced if with_priced else None)
        assert efficiency["kpis"]["worst_session_ctx_per_call"] == (0 if with_priced else None)
        assert efficiency["kpis"]["avg_context_per_call"] == 0
        if not with_priced:
            assert efficiency["kpis"]["total_cost_usd"] == 0
            assert efficiency["dead_sessions"]["avg_cost_per_dead"] is None
    finally:
        for sid in ids:
            await _cleanup(engine, sid)
        await _drop_derived(engine, ids)
        await engine.close()


@pytest.mark.asyncio
async def test_overview_and_users_session_sums_include_a_costless_session():
    """EPMCDME-15517: turns, tool calls, files and duration of a cost-less session join the priced session's."""
    engine = await _get_engine()
    priced, costless = "test-epmcdme-15517-sums-priced", "test-epmcdme-15517-sums-costless"
    try:
        for sid in (priced, costless):
            await _cleanup(engine, sid)
            await _seed_start(engine, sid)
        await _seed_requests(engine, priced)
        await _refresh(engine)
        async with engine.acquire() as conn:
            for sid, email in ((priced, _ALICE), (costless, _BOB)):
                await conn.execute("UPDATE session_dims SET jwt_email = $2 WHERE session_id = $1", sid, email)
        await _seed_derived(engine, priced, 2, 2, 2, 0, _START + timedelta(seconds=10))
        await _seed_derived(engine, costless, 3, 4, 3, 1, _START + timedelta(minutes=5))

        reader = PostgresCliAnalyticsReader(engine)
        handler = LocalAnalyticsHandler(reader)
        both, only_priced = _window().restricted_to([priced, costless]), _window().restricted_to([priced])
        with patch("codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config", _lookup):
            kpis = (await handler.get_overview(both))[0]["kpis"]
            priced_kpis = (await handler.get_overview(only_priced))[0]["kpis"]
            users = (await handler.get_users(both, None, None))[0]
        durations = {r["session_id"]: r["duration_ms"] for r in await reader.get_session_durations(both)}
        assert durations[costless] == 300_000
        by_user = {r["developer_name"]: r for r in users["rows"]}

        # the endpoint functions (response model included) over the same reader: the served body carries the counts
        body = await _served(handler, both)
        assert body["overview"]["data"]["kpis"]["total_sessions"] == 2
        assert body["overview"]["data"]["kpis"]["total_turns"] == 5
        assert sorted(r["developer_name"] for r in body["users"]["data"]["rows"]) == [_ALICE, _BOB]
        assert body["users"]["data"]["total_count"] == 2

        # the priced session keeps its own sums; the cost-less session adds to them
        assert (priced_kpis["total_turns"], priced_kpis["total_tool_calls"]) == (2, 2)
        assert priced_kpis["total_files_changed"] == 0
        assert kpis["total_turns"] == 5
        assert kpis["total_tool_calls"] == 6
        assert kpis["tool_call_success_rate"] == round(5 / 6 * 100, 1)
        assert kpis["total_files_changed"] == 1
        assert kpis["duration_ms"] == priced_kpis["duration_ms"] + 300_000
        assert kpis["total_cost_usd"] == pytest.approx(priced_kpis["total_cost_usd"])  # cost-less adds no money
        assert (by_user[_BOB]["turns"], by_user[_BOB]["tool_calls"]) == (3, 4)
        assert (by_user[_ALICE]["turns"], by_user[_ALICE]["tool_calls"]) == (2, 2)
        # the average runs over both users' sessions, not the priced one alone
        assert users["avg_session_duration_ms"] == (durations[priced] + durations[costless]) // 2
    finally:
        await _cleanup(engine, priced)
        await _cleanup(engine, costless)
        for table in ("turns_daily", "tool_facts_daily", "session_files_daily", "invocations_hourly"):
            async with engine.acquire() as conn:
                await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1)", [priced, costless])  # noqa: S608
        await engine.close()


@pytest.mark.asyncio
async def test_costless_session_without_session_email_keeps_its_lines_developer_in_users():
    """EPMCDME-15517: Users names a cost-less session's developer exactly as last-active and lines-by-user do:
    session email, else cost email, else the smallest email of its lines rows that report changed lines."""
    engine = await _get_engine()
    ids = {k: f"test-epmcdme-15517-who-{k}" for k in ("fallback", "explicit", "priced", "zero")}
    amy, bob, zed, carol = (f"{n}-15517@example.com" for n in ("amy", "bob", "zed", "carol"))
    try:
        for sid in ids.values():
            await _cleanup(engine, sid)
            await _seed_start(engine, sid)
        await _request(engine, ids["priced"], 0.05, _COST_MODEL)
        await _refresh(engine)
        async with engine.acquire() as conn:
            await conn.execute(
                "UPDATE session_dims SET jwt_email = NULL, dev_name_max = NULL, developer_name = NULL"
                " WHERE session_id = ANY($1)",
                list(ids.values()),
            )
            await conn.execute("UPDATE session_dims SET jwt_email = $2 WHERE session_id = $1", ids["explicit"], carol)
            lines = (  # (session, row email, added, removed)
                ("fallback", "aaa-15517@example.com", 0, 0),  # zero-sum: not identity evidence, although the smallest
                ("fallback", bob, 3, 1),
                ("fallback", amy, 6, 3),  # the smallest email among the rows that report lines
                ("explicit", amy, 4, 0),  # the session's own email outranks any lines email
                ("zero", zed, 0, 0),  # only a zero-sum row: no developer
            )
            for key, email, added, removed in lines:
                await conn.execute(
                    "INSERT INTO lines_daily (day, session_id, user_email, model_name, lines_added, lines_removed)"
                    " VALUES ($1, $2, $3, 'm', $4, $5)",
                    _DAY,
                    ids[key],
                    email,
                    added,
                    removed,
                )

        reader = PostgresCliAnalyticsReader(engine)
        handler = LocalAnalyticsHandler(reader)
        own = _window().restricted_to(list(ids.values()))
        with patch("codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config", _lookup):
            users, _ = await handler.get_users(own, None, None)
        by_user = {r["developer_name"]: r for r in users["rows"]}

        # one row per developer, never a split: the fallback session is amy's, not "unknown", with her lines and
        # last-active; the explicit identity stays carol; the priced and the zero-lines-only sessions stay unknown
        assert sorted(by_user) == [amy, carol, "unknown"]
        assert (by_user[amy]["session_count"], by_user[amy]["net_lines"]) == (1, (3 + 6) - (1 + 3))
        assert by_user[amy]["last_active"] is not None
        assert by_user[carol]["session_count"] == 1
        assert by_user["unknown"]["session_count"] == 2
        assert by_user["unknown"]["cost_usd"] > 0  # the priced sibling keeps its money
        assert sum(r["session_count"] for r in users["rows"]) == 4
    finally:
        for sid in ids.values():
            await _cleanup(engine, sid)
        async with engine.acquire() as conn:
            await conn.execute("DELETE FROM lines_daily WHERE session_id = ANY($1)", list(ids.values()))
        await engine.close()


@pytest.mark.asyncio
async def test_repositories_include_costless_sessions_once_with_spend_unchanged():
    """EPMCDME-15517: a cost-less-only repository and the cost-less branch of a mixed repository are listed,
    counted once, add no money, and follow search, paging and the viewer scope (real reader -> handler)."""
    engine = await _get_engine()
    ids = {k: f"test-epmcdme-15517-repo-{k}" for k in ("priced", "mixed", "only", "private")}
    repos = {"priced": "mixed-15517", "mixed": "mixed-15517", "only": "costless-only-15517", "private": "private-15517"}
    branches = {"priced": "main", "mixed": "feature", "only": "main", "private": "main"}
    try:
        for sid in ids.values():
            await _cleanup(engine, sid)
            await _seed_start(engine, sid)
        await _request(engine, ids["priced"], 0.05, _COST_MODEL)  # a model the catalog prices
        await _refresh(engine)
        async with engine.acquire() as conn:
            for key, sid in ids.items():
                await conn.execute(
                    "UPDATE session_dims SET repository = $2, branch = $3 WHERE session_id = $1",
                    sid,
                    repos[key],
                    branches[key],
                )
            # corporate billing is not spend, so only the cost-less-only repository carries it; the priced one stays
            await conn.execute(
                "UPDATE session_dims SET subscription_type = 'corporate' WHERE session_id = $1", ids["only"]
            )
            # personal, cost-less and outside the viewer's scope (exclude): in no repository
            await conn.execute(
                "UPDATE session_dims SET subscription_type = 'personal', project_origin = 'personal',"
                " jwt_email = $2 WHERE session_id = $1",
                ids["private"],
                _BOB,
            )
        await _seed_derived(engine, ids["only"], 3, 4, 3, 1, _START + timedelta(minutes=5))
        for key in ("mixed", "private"):  # the window admits a session with an activity signal (see above)
            await _seed_derived(engine, ids[key], 1, 0, 0, 0, _START + timedelta(seconds=30))

        reader = PostgresCliAnalyticsReader(engine)
        handler = LocalAnalyticsHandler(reader)
        own = _window().restricted_to(list(ids.values()))
        with patch("codemie.service.analytics.handlers.coding_agent_pricing.lookup_cost_config", _lookup):
            flat, _ = await handler.get_repositories(own, 0, 50, False)
            nested, _ = await handler.get_repositories(own, 0, 50, True)
            found, _ = await handler.get_repositories(own, 0, 50, False, "costless-only")
            first, _ = await handler.get_repositories(own, 0, 1, False)
            second, _ = await handler.get_repositories(own, 1, 1, False)
            total_cost = (await handler.get_overview(own))[0]["kpis"]["total_cost_usd"]
        by_repo = {r["repository"]: r for r in flat["rows"]}

        assert sorted(by_repo) == ["costless-only-15517", "mixed-15517"]  # the scoped-out repository is absent
        assert flat["pagination"]["total_count"] == 2
        mixed, only = by_repo["mixed-15517"], by_repo["costless-only-15517"]
        assert mixed["session_count"] == 2  # the priced and the cost-less session, each once
        assert only["session_count"] == 1
        assert (only["cost_usd"], only["turns"], only["tool_calls"], only["files_changed"]) == (0.0, 3, 4, 1)
        assert only["subscription_types"] == ["corporate"]
        # spend stays cost-based and is not duplicated: the repositories' money is the priced session's alone
        assert mixed["cost_usd"] == pytest.approx(total_cost)
        assert total_cost > 0

        # branches: the cost-less branch of the mixed repository is nested next to the priced one
        nested_by_repo = {r["repository"]: r for r in nested["rows"]}
        assert {b["branch"]: b["session_count"] for b in nested_by_repo["mixed-15517"]["branches"]} == {
            "main": 1,
            "feature": 1,
        }
        assert nested_by_repo["costless-only-15517"]["session_count"] == 1

        # search narrows to the cost-less-only repository; paging walks both repositories without losing one
        assert [r["repository"] for r in found["rows"]] == ["costless-only-15517"]
        assert found["pagination"]["total_count"] == 1
        assert first["pagination"] == {"page": 0, "per_page": 1, "total_count": 2, "has_more": True}
        assert second["pagination"]["has_more"] is False
        assert {r["repository"] for r in first["rows"] + second["rows"]} == set(by_repo)
    finally:
        for sid in ids.values():
            await _cleanup(engine, sid)
        for table in ("turns_daily", "tool_facts_daily", "session_files_daily", "invocations_hourly"):
            async with engine.acquire() as conn:
                await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1)", list(ids.values()))  # noqa: S608
        await engine.close()
