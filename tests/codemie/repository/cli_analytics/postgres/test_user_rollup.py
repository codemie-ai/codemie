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

"""The per-user start-day rollup: how days combine, how the Users page's groupings and ranking are assembled from them,
who may read it, and which days the job rebuilds (EPMCDME-17051). No database."""

from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.ports import (
    G_MODEL,
    G_PERSONAL,
    G_ROUTE,
    G_TOTAL,
    G_USER,
    G_USER_PERSONAL,
    G_USER_ROUTE,
)
from codemie.repository.cli_analytics.postgres import user_rollup as ur
from codemie.repository.cli_analytics.scope import ViewerScope

NOW = datetime(2026, 11, 11, 12, 0, 30, tzinfo=timezone.utc)
MIDNIGHT = datetime(2026, 10, 12, tzinfo=timezone.utc)


def window(start=MIDNIGHT, end=NOW, scope=ur.ROLLUP_SCOPE, **filters):
    return LocalAnalyticsFilter(start_dt=start, end_dt=end, scope=scope, **filters)


# ── combining days ──


def test_user_aggregates_add_up_and_keep_the_latest_event_and_the_union_of_classes():
    a = UserAgg_(session_count=2, turns=3, net_lines=5, last_active=datetime(2026, 11, 1), subs={"codemie"})
    b = UserAgg_(session_count=1, turns=4, net_lines=None, last_active=datetime(2026, 11, 3), subs={"personal"})

    a.merge(b)

    assert (a.session_count, a.turns) == (3, 7)
    assert a.net_lines == 5  # a day without lines rows adds nothing and does not turn a known sum into unknown
    assert a.last_active == datetime(2026, 11, 3)
    assert a.subscription_types == {"codemie", "personal"}


def test_net_lines_stay_unknown_until_some_day_has_lines():
    a, b = UserAgg_(), UserAgg_()
    a.merge(b)
    assert a.net_lines is None
    a.merge(UserAgg_(net_lines=0))
    assert a.net_lines == 0  # a known zero is not unknown


def test_the_top_model_is_the_first_key_across_days():
    day = date(2026, 11, 1)
    most_calls = (9, day, "s2", "e", "model-a", "q")
    earlier_day = (5, date(2026, 10, 30), "s1", "e", "model-b", "q")
    same_calls_other_session = (9, day, "s1", "e", "model-c", "q")

    a = ur.UserAgg(top=earlier_day)
    a.merge(ur.UserAgg(top=most_calls))
    assert a.top == most_calls  # more calls wins whatever the day
    a.merge(ur.UserAgg(top=same_calls_other_session))
    assert a.top == same_calls_other_session  # equal calls: the smaller session id
    a.merge(ur.UserAgg(top=None))
    assert a.top == same_calls_other_session  # no cost rows that day


def test_money_cells_add_and_union_the_unpriced_names():
    a = ur.MoneyAgg(cost_all=Decimal("1.5"), sessions_all=2, unpriced_all={"x"})
    a.merge(ur.MoneyAgg(cost_all=Decimal("0.25"), sessions_all=1, unpriced_all={"y"}))
    assert (a.cost_all, a.sessions_all, a.unpriced_all) == (Decimal("1.75"), 3, {"x", "y"})


def UserAgg_(subs=(), **kwargs):  # noqa: N802 - a short constructor for the tests
    return ur.UserAgg(subscription_types=set(subs), **kwargs)


# ── the Users page's groupings and ranking ──


def cohort():
    c = ur.Cohort()
    for name in ("B@x", "a@x", "zero@x", "pers@x"):
        c.users[name] = ur.UserAgg(session_count=1)
    # B and a tie on cost; zero has sessions but nothing priced; pers is billed to a personal plan
    c.money[("B@x", "api_based", False)] = ur.MoneyAgg(Decimal("2"), 1, set(), Decimal("2"), 1, set())
    c.money[("a@x", "subscription", False)] = ur.MoneyAgg(Decimal("2"), 1, {"m"}, Decimal("0"), 0, set())
    c.money[("pers@x", "api_based", True)] = ur.MoneyAgg(Decimal("9"), 1, set(), Decimal("9"), 1, set())
    return c


def test_the_ranking_orders_by_spend_then_code_point_and_puts_costless_users_last():
    served = ur.assemble(cohort())

    # equal spend: "B" < "a" in code-point order; the personal-billed user's cost is not spend
    assert served.money_page.developers == ["B@x", "a@x", "pers@x", "zero@x"]
    assert served.money_page.total_count == 4


def test_the_groupings_follow_the_users_page_rules():
    groups = ur.assemble(cohort()).money_page.money.groups

    total = groups[G_TOTAL][0]
    assert (total.cost_usd, total.session_count) == (4.0, 2)  # personal-billed is left out of spend
    assert total.unpriced_models == ("m",) and total.cost_incomplete
    by_user = {g.keys["user"]: g for g in groups[G_USER]}
    assert set(by_user) == {"B@x", "a@x"}  # spend only
    assert by_user["a@x"].cost_usd == 2.0 and by_user["a@x"].session_count == 1  # not limited to cost_daily rows
    assert {g.keys["funding_route"]: g.session_count for g in groups[G_ROUTE]} == {"api_based": 2, "subscription": 1}
    assert {g.keys["personal_billed"]: g.cost_usd for g in groups[G_PERSONAL]} == {False: 4.0, True: 9.0}
    assert {(g.keys["user"], g.keys["funding_route"]) for g in groups[G_USER_ROUTE]} == {
        ("B@x", "api_based"),
        ("a@x", "subscription"),
        ("pers@x", "api_based"),
    }
    assert {(g.keys["user"], g.keys["personal_billed"]) for g in groups[G_USER_PERSONAL]} == {
        ("B@x", False),
        ("a@x", False),
        ("pers@x", True),
    }


def test_the_rows_carry_what_the_handler_reads():
    c = cohort()
    c.users["a@x"] = ur.UserAgg(
        session_count=3,
        input_tokens=7,
        turns=2,
        tool_calls=5,
        tool_successes=4,
        net_lines=-1,
        last_active=datetime(2026, 11, 2, 3),
        subscription_types={"personal", "codemie"},
        top=(4, date(2026, 11, 1), "s", "e", "claude-x", "q"),
    )

    served = ur.assemble(c)

    row = next(r for r in served.user_rows if r["developer_name"] == "a@x")
    assert (row["session_count"], row["input_tokens"], row["top_model"]) == (3, 7, "claude-x")
    assert row["subscription_types"] == ["codemie", "personal"]
    assert next(r for r in served.developer_facts if r["developer"] == "a@x") == {
        "developer": "a@x",
        "turns": 2,
        "tool_calls": 5,
        "tool_successes": 4,
    }
    assert list(served.lines_rows) == [{"developer_name": "a@x", "net_lines": -1}]
    assert [r["developer_name"] for r in served.last_active_rows] == ["a@x"]


# ── who may read it ──


def test_a_super_admin_window_up_to_now_may_read_the_rollup():
    assert ur.eligible(window(), NOW)


@pytest.mark.parametrize(
    "filters",
    [
        {"subscription_types": ["codemie"]},
        {"project_origins": ["profile", "linked_folder"]},
        {"subscription_types": ["codemie"], "project_origins": ["profile"]},
    ],
)
def test_the_two_class_filters_may_read_the_rollup_whose_rows_are_keyed_by_them(filters):
    assert ur.eligible(window(**filters), NOW)


def test_a_day_is_built_under_the_filter_of_one_class_pair():
    f = ur._day_filter(date(2026, 11, 1), "codemie", "profile")

    assert (f.subscription_types, f.project_origins) == (["codemie"], ["profile"])
    assert f.start_dt == datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert f.end_dt < datetime(2026, 11, 2, tzinfo=timezone.utc)
    assert f.scope == ur.ROLLUP_SCOPE


def test_the_money_cells_fold_without_changing_the_cohort():
    c = cohort()
    c.money[("a@x", "api_based", False)] = ur.MoneyAgg(Decimal("1"), 1, {"n"}, Decimal("1"), 1, set())
    before = {key: set(m.unpriced_all) for key, m in c.money.items()}

    groups = ur.assemble(c).money_page.money.groups

    by_user = {g.keys["user"]: g for g in groups[G_USER]}
    assert (by_user["a@x"].cost_usd, by_user["a@x"].session_count) == (3.0, 2)
    assert by_user["a@x"].unpriced_models == ("m", "n")
    assert {key: m.unpriced_all for key, m in c.money.items()} == before  # the cells' own sets are not written to


@pytest.mark.parametrize(
    "f",
    [
        window(scope=ViewerScope("a@x", False, frozenset({"p"}), "exclude")),  # a project admin
        window(scope=ViewerScope("a@x", True, frozenset(), "own_only")),
        window(scope=ViewerScope("a@x", True, frozenset(), "detail")),
        window(scope=None),
        window(users=["u@x"]),
        window(projects=["p"]),
        window(repositories=["r"]),
        window(branch="main"),
        window(deny_all=True),
        window(project_unattributed=True),
        window(developers=["d@x"]),
        window(start=MIDNIGHT + timedelta(hours=6)),  # not a whole day
        window(end=NOW - timedelta(days=1)),  # not up to now
    ],
    ids=lambda f: "x",
)
def test_anything_the_rollup_cannot_express_takes_the_live_path(f):
    assert not ur.eligible(f, NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize("hours", [1, 6])
async def test_a_window_inside_the_last_utc_day_takes_the_live_path_without_reading_the_rollup(hours):
    f = window(start=NOW - timedelta(hours=hours))

    # reader=None: the call fails if the rollup is read at all
    assert await ur.serve_cohort(None, f, lambda m: None, "v1", max_age_s=3600, now=NOW) is None


def test_a_request_with_session_ids_takes_the_live_path():
    assert not ur.eligible(window().restricted_to(["s"]), NOW)


# ── which days the job rebuilds ──


class FakeConn:
    def __init__(self, signatures, states):
        self.signatures, self.states, self.executed = signatures, states, []

    async def fetch(self, sql, *args):
        if "FROM session_dims" in sql:
            first, end = args  # like the statement: the days from `first` up to, not including, `end`
            return [dict(day=d, **s) for d, s in self.signatures.items() if first <= ur._day_start(d) < end]
        return list(self.states.values())

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


def state(day, version="v1", built_ago=3600, shape=ur.ROLLUP_SHAPE, **sig):
    base = {name: Decimal(0) for name in ur._SIGNATURE_FIELDS} | {k: Decimal(v) for k, v in sig.items()}
    return {
        "start_day": day,
        "rates_version": version,
        "shape": shape,
        "built_at": NOW - timedelta(seconds=built_ago),
        **base,
    }


def signature(**sig):
    return {name: Decimal(0) for name in ur._SIGNATURE_FIELDS} | {k: Decimal(v) for k, v in sig.items()}


async def run_refresh(monkeypatch, conn, **kwargs):
    built = []

    async def fake_build(reader, c, day, rate_of, version, sig):
        built.append(day)
        return 1

    monkeypatch.setattr(ur, "build_day", fake_build)
    monkeypatch.setattr(ur, "datetime", FrozenDatetime)
    rebuilt = await ur.refresh(None, conn, days=3, run_seconds=60, rate_of=None, version="v1", **kwargs)
    return rebuilt, built


TODAY = NOW.date()
DAYS = [TODAY - timedelta(days=n) for n in range(3)]


@pytest.mark.asyncio
async def test_a_day_never_built_or_built_under_other_rates_is_rebuilt_newest_first(monkeypatch):
    states = {DAYS[1]: state(DAYS[1], version="old")}  # today and two days ago were never built
    conn = FakeConn({}, states)

    rebuilt, _ = await run_refresh(monkeypatch, conn, min_rebuild_s=999)

    assert rebuilt == DAYS  # the hold does not apply to a day without a trustworthy state


@pytest.mark.asyncio
async def test_a_due_today_comes_first_then_the_days_never_built(monkeypatch):
    # today exists but changed long ago; the two older days were never built
    conn = FakeConn({DAYS[0]: signature(sessions=1)}, {DAYS[0]: state(DAYS[0], built_ago=99999)})

    rebuilt, _ = await run_refresh(monkeypatch, conn, min_rebuild_s=120)

    assert rebuilt == [DAYS[0], DAYS[1], DAYS[2]]  # today stays fresh during a backfill


@pytest.mark.asyncio
async def test_a_held_today_does_not_delay_the_days_never_built(monkeypatch):
    conn = FakeConn({DAYS[0]: signature(sessions=1)}, {DAYS[0]: state(DAYS[0], built_ago=30)})

    rebuilt, _ = await run_refresh(monkeypatch, conn, min_rebuild_s=120)

    assert rebuilt == [DAYS[1], DAYS[2]]


@pytest.mark.asyncio
async def test_a_changed_day_waits_for_its_hold_before_it_is_rebuilt(monkeypatch):
    states = {d: state(d, built_ago=60) for d in DAYS}
    conn = FakeConn({d: signature(sessions=1) for d in DAYS}, states)  # every day changed a minute after its build

    rebuilt, _ = await run_refresh(monkeypatch, conn, min_rebuild_s=120)
    assert rebuilt == []  # today is held for 120 s, a past day for 1200 s

    states[DAYS[0]] = state(DAYS[0], built_ago=130)
    rebuilt, _ = await run_refresh(monkeypatch, conn, min_rebuild_s=120)
    assert rebuilt == [DAYS[0]]


@pytest.mark.asyncio
async def test_an_unchanged_day_is_left_alone(monkeypatch):
    states = {d: state(d, sessions=2, last_sum="5") for d in DAYS}
    conn = FakeConn({d: signature(sessions=2, last_sum="5") for d in DAYS}, states)

    rebuilt, _ = await run_refresh(monkeypatch, conn)

    assert rebuilt == []


@pytest.mark.asyncio
async def test_the_job_confirms_the_days_it_found_unchanged_so_the_readers_keep_trusting_them(monkeypatch):
    states = {DAYS[0]: state(DAYS[0], built_ago=99999, sessions=2), DAYS[1]: state(DAYS[1], built_ago=60)}
    states[DAYS[2]] = state(DAYS[2], built_ago=99999, sessions=1)
    sigs = {DAYS[0]: signature(sessions=2), DAYS[1]: signature(sessions=5), DAYS[2]: signature(sessions=1)}
    conn = FakeConn(sigs, states)

    await run_refresh(monkeypatch, conn, min_rebuild_s=120)

    touched = [args[0] for sql, args in conn.executed if "SET checked_at" in sql]
    assert touched == [[DAYS[0], DAYS[2]]]  # unchanged; the changed day DAYS[1] (held) is not confirmed


@pytest.mark.asyncio
async def test_a_light_run_checks_only_the_recent_days_for_changes(monkeypatch):
    states = {d: state(d, built_ago=99999) for d in DAYS}
    changed = {d: signature(sessions=1) for d in DAYS}  # every day differs from its state
    conn = FakeConn(changed, states)

    light, _ = await run_refresh(monkeypatch, conn, full_scan=False)
    full, _ = await run_refresh(monkeypatch, conn, full_scan=True)

    assert light == DAYS[: ur.RECENT_DAYS]
    assert full == DAYS


@pytest.mark.asyncio
async def test_a_day_built_by_a_light_run_is_stored_with_its_own_signature(monkeypatch):
    # The backfill runs as light runs. An old day it builds must carry the signature of its sessions, not an empty
    # one: the next full scan would otherwise find every such day changed and build it a second time.
    conn = FakeConn({d: signature(sessions=7, cost_sum="3") for d in DAYS}, {})
    seen = {}

    async def fake_build(reader, c, day, rate_of, version, sig):
        seen[day] = sig
        return 1

    monkeypatch.setattr(ur, "build_day", fake_build)
    monkeypatch.setattr(ur, "datetime", FrozenDatetime)

    await ur.refresh(None, conn, days=3, run_seconds=60, rate_of=None, version="v1", full_scan=False)

    assert set(seen) == set(DAYS)
    assert all(sig["sessions"] == 7 and sig["cost_sum"] == 3 for sig in seen.values())


@pytest.mark.asyncio
async def test_a_run_stops_rebuilding_when_its_time_is_used_up(monkeypatch):
    conn = FakeConn({}, {})
    built = []

    async def fake_build(reader, c, day, rate_of, version, sig):
        built.append(day)

    monkeypatch.setattr(ur, "build_day", fake_build)
    monkeypatch.setattr(ur, "datetime", FrozenDatetime)

    rebuilt = await ur.refresh(None, conn, days=3, run_seconds=-1, rate_of=None, version="v1")

    assert rebuilt == [DAYS[0]]  # at least the newest day: a run always makes progress


@pytest.mark.asyncio
async def test_the_recent_days_are_confirmed_apart_from_the_rebuilds(monkeypatch):
    monkeypatch.setattr(ur, "datetime", FrozenDatetime)
    states = {
        DAYS[0]: state(DAYS[0], built_ago=99999, sessions=2),  # unchanged
        DAYS[1]: state(DAYS[1], built_ago=99999, sessions=1),  # changed since it was built
    }
    sigs = {DAYS[0]: signature(sessions=2), DAYS[1]: signature(sessions=3)}
    conn = FakeConn(sigs, states)

    confirmed = await ur.confirm_recent(conn, version="v1")

    assert confirmed == [DAYS[0]]
    assert [args[0] for sql, args in conn.executed if "SET checked_at" in sql] == [[DAYS[0]]]


@pytest.mark.asyncio
async def test_a_day_built_under_other_rates_is_not_confirmed(monkeypatch):
    monkeypatch.setattr(ur, "datetime", FrozenDatetime)
    conn = FakeConn({DAYS[0]: signature(sessions=2)}, {DAYS[0]: state(DAYS[0], version="old", sessions=2)})

    assert await ur.confirm_recent(conn, version="v1") == []
    assert conn.executed == []


# ── /cost from the rollup ──


def cost_cohort():
    c = ur.Cohort()
    for name, n, tokens in (("a@x", 3, 10), ("b@x", 1, 5), ("none@x", 2, 7), ("pers@x", 1, 100)):
        c.users[name] = ur.UserAgg(session_count=n, input_tokens=tokens, output_tokens=1)
    # a: priced sessions of which one has no cost_daily row (all=3, cd=2); b: one; none: sessions but nothing priced;
    # pers: billed to a personal plan, so out of every spend value but still a listed developer
    c.money[("a@x", "api_based", False)] = ur.MoneyAgg(Decimal("3"), 3, {"m"}, Decimal("2"), 2, set())
    c.money[("b@x", "subscription", False)] = ur.MoneyAgg(Decimal("1.5"), 1, set(), Decimal("1.5"), 1, set())
    c.money[("pers@x", "api_based", True)] = ur.MoneyAgg(Decimal("9"), 1, set(), Decimal("9"), 1, set())
    c.models["claude-x"] = ur.ModelAgg(Decimal("4.5"), 4, set())
    c.models["claude-y"] = ur.ModelAgg(Decimal(0), 0, {"raw-y"})
    return c


def test_the_cost_totals_follow_the_cost_page_rules():
    served = ur.assemble_cost(cost_cohort())
    groups = served.money.groups

    total = groups[G_TOTAL][0]
    assert (total.cost_usd, total.session_count) == (4.5, 4)  # spend: personal-billed left out, every priced session
    assert total.unpriced_models == ("m",) and total.cost_incomplete
    assert served.total_sessions == 7  # every session of the window, priced or not
    assert served.spend_sessions == 3  # the spend sessions that have a cost_daily row
    assert served.total_tokens == 10 + 1 + 5 + 1 + 7 + 1 + 100 + 1  # every developer, personal-billed included


def test_the_cost_per_developer_counts_only_the_cost_daily_sessions_and_lists_everyone_who_has_one():
    served = ur.assemble_cost(cost_cohort())
    by_user = {g.keys["user"]: g for g in served.money.groups[G_USER]}

    assert (by_user["a@x"].cost_usd, by_user["a@x"].session_count) == (2.0, 2)  # cd: not the 3 of the total
    assert set(by_user) == {"a@x", "b@x"}  # spend only: the personal-billed developer has no figure
    assert served.developers == ["a@x", "b@x", "pers@x"]  # listed: has a cost_daily session, any billing


def test_the_cost_per_model_keeps_a_model_with_only_unpriced_names():
    served = ur.assemble_cost(cost_cohort())
    by_model = {g.keys["model"]: g for g in served.money.groups[G_MODEL]}

    assert (by_model["claude-x"].cost_usd, by_model["claude-x"].session_count) == (4.5, 4)
    assert by_model["claude-y"].unpriced_models == ("raw-y",) and by_model["claude-y"].cost_incomplete


def test_an_empty_window_has_no_total_group():
    served = ur.assemble_cost(ur.Cohort())

    assert served.money.groups[G_TOTAL] == [] and served.developers == []
    assert (served.total_sessions, served.spend_sessions, served.total_tokens) == (0, 0, 0)


def test_model_rows_add_across_days_and_union_the_unpriced_names():
    a = ur.Cohort(models={"m": ur.ModelAgg(Decimal("1.5"), 2, {"x"})})
    a.merge(ur.Cohort(models={"m": ur.ModelAgg(Decimal("0.25"), 1, {"y"}), "n": ur.ModelAgg(Decimal(1), 1, set())}))

    assert (a.models["m"].cost, a.models["m"].sessions, a.models["m"].unpriced) == (Decimal("1.75"), 3, {"x", "y"})
    assert set(a.models) == {"m", "n"}


@pytest.mark.asyncio
async def test_a_day_built_before_the_model_rows_existed_is_rebuilt(monkeypatch):
    conn = FakeConn(
        {d: signature() for d in DAYS},
        {DAYS[0]: state(DAYS[0]), DAYS[1]: state(DAYS[1], shape=1), DAYS[2]: state(DAYS[2])},
    )

    _, built = await run_refresh(monkeypatch, conn)

    assert built == [DAYS[1]]  # same rates, same sessions, but no model rows: it is built again


@pytest.mark.asyncio
async def test_a_day_of_another_shape_is_not_confirmed():
    conn = FakeConn({d: signature() for d in DAYS[:2]}, {DAYS[0]: state(DAYS[0], shape=1), DAYS[1]: state(DAYS[1])})

    confirmed = await ur.confirm_recent(conn, version="v1", today=TODAY)

    assert confirmed == [DAYS[1]]


# ── a recompute of a session the signature cannot see (`touched`) ──


@pytest.mark.asyncio
async def test_a_day_the_refresher_touched_is_rebuilt_though_its_signature_is_unchanged(monkeypatch):
    # tool spans or line counts after the last hook event leave `session_dims`, and so the signature, as they were
    states = {d: state(d, built_ago=99999, sessions=2) for d in DAYS}
    states[DAYS[1]] |= {"touched": 3, "built_touched": 2}
    conn = FakeConn({d: signature(sessions=2) for d in DAYS}, states)

    rebuilt, _ = await run_refresh(monkeypatch, conn)

    assert rebuilt == [DAYS[1]]
    confirmed = [args[0] for sql, args in conn.executed if "SET checked_at" in sql]
    assert confirmed == [[DAYS[0], DAYS[2]]]  # the touched day is not confirmed as current


@pytest.mark.asyncio
async def test_a_touched_day_is_found_by_a_light_run_and_waits_for_its_hold(monkeypatch):
    states = {d: state(d, built_ago=99999) for d in DAYS}
    states[DAYS[2]] |= {"touched": 1, "built_touched": 0}  # older than the days a light run reads the signatures of
    conn = FakeConn({}, states)

    light, _ = await run_refresh(monkeypatch, conn, full_scan=False)
    assert light == [DAYS[2]]

    states[DAYS[2]] = state(DAYS[2], built_ago=60) | {"touched": 1, "built_touched": 0}
    held, _ = await run_refresh(monkeypatch, conn, full_scan=False, min_rebuild_s=120)
    assert held == []  # a past day is rebuilt at most every ten holds


@pytest.mark.asyncio
async def test_a_touched_recent_day_is_not_confirmed(monkeypatch):
    monkeypatch.setattr(ur, "datetime", FrozenDatetime)
    states = {DAYS[0]: state(DAYS[0], sessions=2) | {"touched": 5, "built_touched": 5}}
    states[DAYS[1]] = state(DAYS[1], sessions=2) | {"touched": 5, "built_touched": 4}
    conn = FakeConn({d: signature(sessions=2) for d in DAYS[:2]}, states)

    assert await ur.confirm_recent(conn, version="v1") == [DAYS[0]]


@pytest.mark.asyncio
async def test_a_build_records_the_touched_it_read_before_its_data_and_never_writes_touched():
    class BuildConn:
        def __init__(self):
            self.calls = []

        async def fetchval(self, sql, *args):
            self.calls.append(sql)
            return 7

        async def fetch(self, sql, *args):
            self.calls.append(sql)
            return []  # no class pair: the day has no session

        async def execute(self, sql, *args):
            self.calls.append((sql, args))

        async def executemany(self, sql, rows):
            pass

        def transaction(self):
            @asynccontextmanager
            async def tx():
                yield

            return tx()

    conn = BuildConn()
    reader = type("Reader", (), {"_s_cap": 1, "_rates_version": None})()

    await ur.build_day(reader, conn, DAYS[0], None, "v1", signature())

    assert conn.calls[0] == ur._SEEN_TOUCHED  # before any read of the day's data
    sql, args = conn.calls[-1]
    assert sql == ur._UPSERT_STATE and args[-1] == 7
    assert "built_touched = $11" in sql and "touched = $" not in sql.split("DO UPDATE SET")[1].replace(
        "built_touched", ""
    )


# ── /overview from the rollup ──


def overview_cohort(**kwargs):
    c = ur.Cohort()
    c.overview = ur.OverviewAgg(sessions=kwargs.pop("sessions", 3), **kwargs)
    return c


def test_overview_rows_add_across_days_with_sql_sum_rules_for_lines_and_models():
    a = ur.OverviewAgg(turns=2, spend_cost=Decimal("1.5"), unpriced={"x"})
    a.lines = {date(2026, 11, 1): [5, None, True]}
    a.models = {"m": [2, None]}
    b = ur.OverviewAgg(turns=3, spend_cost=Decimal("0.5"), unpriced={"y"})
    b.lines = {date(2026, 11, 1): [None, None, False], date(2026, 11, 2): [1, 1, False]}
    b.models = {"m": [1, Decimal(2)], "n": [1, None]}

    a.merge(b)

    assert (a.turns, a.spend_cost, a.unpriced) == (5, Decimal("2.0"), {"x", "y"})
    assert a.lines[date(2026, 11, 1)] == [5, None, True]  # a NULL sum stays NULL only while every part is
    assert a.lines[date(2026, 11, 2)] == [1, 1, False]
    assert a.models == {"m": [3, Decimal(2)], "n": [1, None]}


def test_the_overview_rows_have_the_shapes_the_live_statements_return():
    c = overview_cohort(
        sessions=4,
        turns=7,
        tool_calls=5,
        tool_successes=4,
        duration_ms=1000,
        files_changed=2,
        dead_sessions=1,
        spend_cost=Decimal("3.25"),
        cache_read_cost=Decimal("0.5"),
        unpriced={"raw-b", "raw-a"},
        input_tokens=10,
        api_calls=2,
    )
    served = ur.assemble_overview(c)

    spend = next(r for r in served.money_rows if r["part"] == "money")
    assert (spend["key"], spend["cost_usd"], spend["cache_read_cost_usd"]) == ("spend", 3.25, 0.5)
    assert spend["unpriced_models"] == ["raw-a", "raw-b"]
    assert next(r for r in served.money_rows if r["part"] == "dead")["n"] == 1
    assert served.fact_rows == [
        {"part": "facts", "turns": 7, "tool_calls": 5, "tool_calls_success": 4, "duration_ms": 1000, "files_changed": 2}
    ]
    assert served.kpi_rows[0]["total_sessions"] == 4 and served.kpi_rows[0]["total_api_calls"] == 2


def test_a_window_with_no_spend_has_no_spend_row_as_the_live_grouping():
    served = ur.assemble_overview(overview_cohort())

    assert [r["part"] for r in served.money_rows] == ["dead"]


def test_the_overview_lines_keep_unknown_apart_from_zero():
    c = overview_cohort()
    c.overview.lines = {date(2026, 11, 1): [5, 2, True], date(2026, 11, 2): [None, None, False]}
    served = ur.assemble_overview(c)

    days = {r["key"]: r for r in served.line_rows if r["part"] == "day"}
    assert days["2026-11-01"]["net_lines"] == 3 and days["2026-11-01"]["nonzero"]
    assert days["2026-11-02"]["net_lines"] is None  # `added - removed` with an unknown side is unknown, as in SQL
    total = next(r for r in served.line_rows if r["part"] == "total")
    assert (total["lines_added"], total["lines_removed"]) == (5, 2)


def test_a_window_without_lines_has_an_unknown_total_as_the_live_statement():
    total = next(r for r in ur.assemble_overview(overview_cohort()).line_rows if r["part"] == "total")

    assert (total["lines_added"], total["lines_removed"]) == (None, None)


def test_the_models_follow_the_live_order_sessions_then_spend_with_null_first_then_code_point():
    c = overview_cohort()
    c.overview.models = {
        "b": [5, Decimal(1)],
        "a": [5, Decimal(1)],
        "c": [5, None],
        "d": [5, Decimal(9)],
        "e": [9, Decimal(0)],
    }

    order = [r["key"] for r in ur.assemble_overview(c).model_rows]

    assert order == ["e", "c", "d", "a", "b"]  # DESC puts NULL first in PostgreSQL


# ── the Overview rows: build, write, read back (CR-001) and the one column list ──


class _Tx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class WriteConn:
    """Records what `write_day` sends."""

    def __init__(self):
        self.executed, self.many = [], {}

    def transaction(self, **kwargs):
        return _Tx()

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    async def executemany(self, sql, rows):
        self.many[sql] = list(rows)


def full_overview():
    """An aggregate whose every card total is a different number, so a swapped column shows."""
    o = ur.OverviewAgg()
    for i, name in enumerate(ur.OVERVIEW_SUMS, start=1):
        setattr(o, name, Decimal(f"{i}.5") if name in ur._OVERVIEW_NUMERIC else i)
    o.unpriced = {"raw-b", "raw-a"}
    o.lines = {date(2026, 11, 1): [5, 2, True], date(2026, 11, 2): [None, None, False]}
    o.models = {"m": [3, Decimal("1.5")], "n": [1, None]}
    return o


def one_class_cohort():
    c = ur.Cohort(users={"a@x": ur.UserAgg(session_count=3)})
    c.overview = full_overview()
    return c


SIGNATURE = {name: Decimal(0) for name in ur._SIGNATURE_FIELDS}


@pytest.mark.asyncio
async def test_write_day_stores_every_overview_total_in_the_column_of_its_name():
    conn = WriteConn()
    await ur.write_day(conn, date(2026, 11, 1), {("codemie", "profile"): one_class_cohort()}, SIGNATURE, "v1", 5)

    (row,) = conn.many[ur._INSERT_OVERVIEW]
    stored = dict(zip(ur._OVERVIEW_COLUMNS, row, strict=True))
    agg = full_overview()
    assert (stored["start_day"], stored["sub_class"], stored["origin_class"]) == (
        date(2026, 11, 1),
        "codemie",
        "profile",
    )
    for name in ur.OVERVIEW_SUMS:
        assert stored[name] == getattr(agg, name), name
    assert stored["unpriced"] == ["raw-a", "raw-b"]  # sorted: the stored array is deterministic


@pytest.mark.asyncio
async def test_write_day_stores_the_lines_by_line_day_and_the_models_with_their_spend():
    conn = WriteConn()
    await ur.write_day(conn, date(2026, 11, 1), {("codemie", "profile"): one_class_cohort()}, SIGNATURE, "v1", 5)

    assert conn.many[ur._INSERT_OVERVIEW_LINES] == [
        (date(2026, 11, 1), "codemie", "profile", date(2026, 11, 1), 5, 2, True),
        (date(2026, 11, 1), "codemie", "profile", date(2026, 11, 2), None, None, False),  # unknown stays unknown
    ]
    assert conn.many[ur._INSERT_OVERVIEW_MODEL] == [
        (date(2026, 11, 1), "codemie", "profile", "m", 3, Decimal("1.5")),
        (date(2026, 11, 1), "codemie", "profile", "n", 1, None),
    ]


@pytest.mark.asyncio
async def test_write_day_replaces_every_table_of_the_day_and_records_its_shape():
    conn = WriteConn()
    await ur.write_day(conn, date(2026, 11, 1), {("codemie", "profile"): one_class_cohort()}, SIGNATURE, "v1", 5)

    deleted = [sql for sql, _ in conn.executed if sql.startswith("DELETE")]
    assert {sql.split()[2] for sql in deleted} == set(ur._DAY_TABLES)
    upsert = next(args for sql, args in conn.executed if sql == ur._UPSERT_STATE)
    assert ur.SHAPE_OVERVIEW in upsert


def _migration_tables():
    """{table: [column, ...]} of the Overview tables as the migration creates them."""
    import importlib.util
    import re
    from pathlib import Path

    from codemie.repository.cli_analytics.postgres import migrations

    path = next((Path(migrations.ALEMBIC_DIR) / "versions").glob("a1c1a0000017_*.py"))
    spec = importlib.util.spec_from_file_location("a1c1a0000017", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tables = {}
    for statement in module.CREATE_STATEMENTS:
        match = re.match(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*)\)$", statement.strip(), re.S)
        if match:
            body = [line.strip() for line in match.group(2).splitlines() if line.strip()]
            tables[match.group(1)] = [line.split()[0] for line in body if line.split()[0] != "PRIMARY"]
    return tables


def _insert_columns(sql):
    import re

    table, columns = re.search(r"INSERT INTO (\w+)\s*\(([^)]*)\)", " ".join(sql.split())).groups()
    return table, [c.strip() for c in columns.split(",")]


def test_the_insert_statements_and_the_aggregate_follow_the_columns_of_the_migration():
    tables = _migration_tables()

    for sql in (ur._INSERT_OVERVIEW, ur._INSERT_OVERVIEW_LINES, ur._INSERT_OVERVIEW_MODEL):
        table, columns = _insert_columns(sql)
        # An explicit column list: its order is the statement's own (the write_day payload test pins it to the values),
        # its set is the table's.
        assert len(columns) == len(set(columns)) and set(columns) == set(tables[table]), table
    # the card totals: the aggregate's fields, the stored columns and the window read are one list
    assert set(tables["user_day_overview_rollup"]) == {
        "start_day",
        "sub_class",
        "origin_class",
        "unpriced",
        *ur.OVERVIEW_SUMS,
    }
    assert all(f"AS {name}" in ur._OVERVIEW_SQL for name in ur.OVERVIEW_SUMS)


class ReadConn:
    """Answers the window reads of `_window_cohort`: the Overview rows given, nothing for the others."""

    def __init__(self, sums=None, unpriced=(), lines=(), models=(), sessions=0):
        self.sums = sums if sums is not None else {name: None for name in ur.OVERVIEW_SUMS}
        self.unpriced, self.lines, self.models, self.sessions, self.args = unpriced, lines, models, sessions, []
        self.sql: list[str] = []

    async def fetchval(self, sql, *args):
        assert sql == ur._SESSIONS_SQL
        self.sql.append(sql)
        self.args.append(args)
        return self.sessions

    async def fetch(self, sql, *args):
        self.sql.append(sql)
        self.args.append(args)
        if sql == ur._OVERVIEW_UNPRICED_SQL:
            return [{"name": n} for n in self.unpriced]
        if sql == ur._OVERVIEW_LINES_SQL:
            return list(self.lines)
        if sql == ur._OVERVIEW_MODELS_SQL:
            return list(self.models)
        return []

    async def fetchrow(self, sql, *args):
        assert sql == ur._OVERVIEW_SQL
        self.sql.append(sql)
        return self.sums


@pytest.mark.asyncio
async def test_the_window_read_rebuilds_the_overview_aggregate_from_its_rows():
    sums = {name: i for i, name in enumerate(ur.OVERVIEW_SUMS, start=1)}
    sums["spend_cost"], sums["cache_read_cost"] = Decimal("8.25"), Decimal("1.5")
    sums["api_calls"] = None  # a SQL sum over no row
    conn = ReadConn(
        sums,
        unpriced=["raw-a"],
        lines=[
            {"line_day": date(2026, 11, 1), "added": 5, "removed": None, "nonzero": True},
            {"line_day": date(2026, 11, 2), "added": None, "removed": None, "nonzero": False},
        ],
        models=[{"model": "m", "sessions": 3, "spend": Decimal("1.5")}, {"model": "n", "sessions": 1, "spend": None}],
        sessions=42,
    )

    cohort = await ur._window_cohort(conn, date(2026, 11, 1), date(2026, 11, 2), ["codemie"], None, overview=True)

    o = cohort.overview
    assert o.sessions == 42 and cohort.users == {}  # the count only: no developer, money or top-model row is read
    assert ur._USERS_SQL not in conn.sql and ur._MONEY_WINDOW_SQL not in conn.sql and ur._TOP_SQL not in conn.sql
    assert (o.turns, o.duration_ms, o.input_tokens) == (1, 4, 9) and o.api_calls == 0  # NULL reads as nothing
    assert (o.spend_cost, o.cache_read_cost) == (Decimal("8.25"), Decimal("1.5"))
    assert o.unpriced == {"raw-a"}
    assert o.lines == {date(2026, 11, 1): [5, None, True], date(2026, 11, 2): [None, None, False]}
    assert o.models == {"m": [3, Decimal("1.5")], "n": [1, None]}
    assert (
        date(2026, 11, 1),
        date(2026, 11, 2),
        ["codemie"],
        None,
    ) in conn.args  # the window and the classes asked for


@pytest.mark.asyncio
async def test_a_window_without_overview_rows_reads_back_as_an_empty_aggregate():
    cohort = await ur._window_cohort(ReadConn(), date(2026, 11, 1), date(2026, 11, 2), None, None, overview=True)

    assert cohort.overview.merge is not None and cohort.overview.turns == 0 and cohort.overview.lines == {}
    assert cohort.overview.models == {} and cohort.overview.unpriced == set()


@pytest.mark.asyncio
async def test_the_overview_rows_are_not_read_unless_asked_for():
    conn = ReadConn()

    await ur._window_cohort(conn, date(2026, 11, 1), date(2026, 11, 2), None, None)

    assert not any("overview" in str(a) for a in conn.args)  # nothing of the three tables was fetched


class FakeStatements:
    """The reader of `gather_overview`: the live /overview statements, as rows of `part`."""

    def __init__(self):
        self.calls, self.rates_seen = [], None

    async def materialize_files(self, f):
        self.calls.append("materialize_files")

    async def get_cost_models(self, f):
        self.calls.append("get_cost_models")
        return ["claude-x"]

    async def get_overview_money(self, f, rates, exact=False):
        assert exact, "the stored money must be exact, not the float of normalise"
        self.rates_seen = rates
        return [
            {
                "part": "money",
                "key": "spend",
                "cost_usd": Decimal("3.5"),
                "cache_read_cost_usd": Decimal("0.5"),
                "unpriced_models": ["raw-a"],
            },
            {"part": "money", "key": "route", "cost_usd": Decimal("9")},  # an estimate bucket: not stored
            {"part": "dead", "n": 2},
        ]

    async def get_overview_facts(self, f):
        return [
            {
                "part": "facts",
                "turns": 7,
                "tool_calls": 5,
                "tool_calls_success": 4,
                "duration_ms": 900,
                "files_changed": 3,
            }
        ]

    async def get_overview_models(self, f):
        return [
            {"part": "model", "key": "m", "n": 3, "cost_usd": Decimal("1.5")},
            {"part": "model", "key": "n", "n": 1, "cost_usd": None},
        ]

    async def get_dashboard_kpis(self, f):
        return [
            {
                "total_sessions": 4,
                "total_input_tokens": 10,
                "total_output_tokens": 20,
                "total_cache_read_tokens": 30,
                "total_cache_creation_tokens": 40,
                "total_api_calls": 6,
            }
        ]

    async def get_overview_lines(self, f):
        return [
            {"part": "total", "lines_added": 9, "lines_removed": 3},
            {"part": "day", "key": "2026-11-01", "net_lines": 6, "lines_added": 9, "lines_removed": 3, "nonzero": True},
            {
                "part": "day",
                "key": "2026-11-02",
                "net_lines": None,
                "lines_added": None,
                "lines_removed": None,
                "nonzero": False,
            },
        ]


@pytest.mark.asyncio
async def test_gather_overview_maps_each_live_statement_to_the_aggregate_field_it_feeds():
    repo = FakeStatements()

    o = await ur.gather_overview(repo, window(), lambda m: m)

    assert (o.spend_cost, o.cache_read_cost, o.unpriced, o.dead_sessions) == (
        Decimal("3.5"),
        Decimal("0.5"),
        {"raw-a"},
        2,
    )
    assert (o.turns, o.tool_calls, o.tool_successes, o.duration_ms, o.files_changed) == (7, 5, 4, 900, 3)
    assert (o.input_tokens, o.output_tokens, o.cache_read_tokens, o.cache_creation_tokens, o.api_calls) == (
        10,
        20,
        30,
        40,
        6,
    )
    assert o.models == {"m": [3, Decimal("1.5")], "n": [1, None]}
    assert o.lines == {date(2026, 11, 1): [9, 3, True], date(2026, 11, 2): [None, None, False]}


# ── which shape a reader needs, which shape the job keeps, and the live gap sessions of the Overview ──


class ServeConn:
    """The connection of `serve_cohort`: the day states and the resumed sessions given, no other rollup row."""

    def __init__(self, states, resumed=()):
        self.states, self.resumed = states, list(resumed)

    def transaction(self, **kwargs):
        return _Tx()

    async def fetch(self, sql, *args):
        if "FROM user_rollup_day_state" in sql:
            return self.states
        if sql == ur._RESUMED_SQL:
            return [{"session_id": s} for s in self.resumed]
        return []

    async def fetchrow(self, sql, *args):
        return {name: None for name in ur.OVERVIEW_SUMS}

    async def fetchval(self, sql, *args):
        return 0


class _Held:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class ServeReader:
    """The reader of `serve_cohort`: `_engine.acquire()` hands out the connection, `bounded()` a scoped reader."""

    def __init__(self, conn):
        self.conn, self.bounded_count = conn, 0
        self._engine = self

    def acquire(self):
        return _Held(self.conn)

    @asynccontextmanager
    async def bounded(self):
        self.bounded_count += 1
        yield type("Scoped", (), {"materialize_sel": staticmethod(_noop)})()


async def _noop(*args, **kwargs):
    return None


def day_states(shape, version="v1"):
    first = MIDNIGHT.date()
    return [
        {"start_day": first + timedelta(days=n), "rates_version": version, "shape": shape, "checked_at": NOW}
        for n in range((NOW.date() - first).days + 1)
    ]


async def serve(conn, overview=False, models=False):
    return await ur.serve_cohort(
        ServeReader(conn), window(), lambda m: m, "v1", max_age_s=300, now=NOW, overview=overview, models=models
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shape", "overview", "served"),
    [(1, False, False), (2, False, True), (2, True, False), (3, False, True), (3, True, True), (4, True, True)],
    ids=["old-users", "cost-users", "cost-overview", "full-users", "full-overview", "newer-overview"],
)
async def test_a_reader_needs_only_the_shape_that_holds_what_it_reads(shape, overview, served):
    # /users and /cost read what a day of the cost shape holds, so they are not refused while the days are rebuilt for
    # the Overview rows; the Overview needs those rows; a day of a newer shape holds at least as much.
    result = await serve(ServeConn(day_states(shape)), overview=overview)

    assert (result is not None) is served


@pytest.mark.asyncio
async def test_the_overview_of_the_live_gap_sessions_is_added_to_the_rollup_rows(monkeypatch):
    seen = {}

    async def fake_cohort(repo, f, rate_of):
        seen["session_ids"] = f.session_ids
        return ur.Cohort(users={"live@x": ur.UserAgg(session_count=2)})

    async def fake_overview(repo, f, rate_of):
        return ur.OverviewAgg(turns=5, spend_cost=Decimal("1.5"), unpriced={"raw"})

    monkeypatch.setattr(ur, "gather_cohort", fake_cohort)
    monkeypatch.setattr(ur, "gather_overview", fake_overview)
    monkeypatch.setattr(ur, "_on_held", _noop)

    cohort = await serve(ServeConn(day_states(3), resumed=["s1", "s2"]), overview=True)

    assert seen["session_ids"] == ["s1", "s2"]  # only the sessions that started earlier and are active in the window
    assert cohort.users["live@x"].session_count == 2 and cohort.overview.sessions == 2  # counted with the rollup's
    assert (cohort.overview.turns, cohort.overview.spend_cost, cohort.overview.unpriced) == (5, Decimal("1.5"), {"raw"})


@pytest.mark.asyncio
async def test_the_overview_statements_do_not_run_for_the_live_gap_sessions_of_users_and_cost(monkeypatch):
    async def fake_cohort(repo, f, rate_of):
        return ur.Cohort(users={"live@x": ur.UserAgg(session_count=1)})

    async def fake_overview(repo, f, rate_of):
        raise AssertionError("the Overview statements ran for a request that does not read them")

    monkeypatch.setattr(ur, "gather_cohort", fake_cohort)
    monkeypatch.setattr(ur, "gather_overview", fake_overview)
    monkeypatch.setattr(ur, "_on_held", _noop)

    cohort = await serve(ServeConn(day_states(2), resumed=["s1"]))

    assert cohort.users["live@x"].session_count == 1 and cohort.overview.turns == 0


@pytest.mark.asyncio
async def test_a_day_of_a_newer_shape_is_neither_rebuilt_nor_left_unconfirmed(monkeypatch):
    conn = FakeConn({d: signature() for d in DAYS}, {d: state(d, shape=ur.ROLLUP_SHAPE + 1) for d in DAYS})
    assert sorted(await ur.confirm_recent(conn, version="v1", today=TODAY)) == sorted(DAYS)

    _, built = await run_refresh(monkeypatch, conn)

    assert built == []  # a newer pod's days are not built down to this pod's shape
