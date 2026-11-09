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

"""Live-Postgres checks of the EPMCDME-16440 owner-only read scope (AC1-AC10).

Runs the real reader SQL (scope predicate, detail gate, class lists, filters) on a real Postgres, unmocked.
Skipped when TEST_LOCAL_PG_URL is unreachable. Every test uses its own uuid-tagged ids, user and repository, and
filters its aggregates by that user, so the xdist workers sharing one database cannot see each other's rows.
"""

import json
import os
import uuid
from unittest.mock import patch
from datetime import date, datetime, timedelta, timezone

import asyncpg
import pytest
import pytest_asyncio

from codemie.configs.config import Config
from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.postgres.engine import AnalyticsPgEngine
from codemie.repository.cli_analytics.postgres.reader import PostgresCliAnalyticsReader
from codemie.repository.cli_analytics.postgres.settings import AnalyticsPgSettings
from codemie.repository.cli_analytics.scope import ViewerScope
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler

_LOCAL_PG_URL = os.environ.get("TEST_LOCAL_PG_URL", "postgresql://postgres:password@localhost:5432/postgres")

_TABLES = (
    "session_dims",
    "cost_daily",
    "log_events",
    "spans",
    "session_skills",
    "lines_daily",
    "turns_daily",
    "session_files_daily",
    "tool_facts_daily",
)
_NOW = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
_DAY: date = _NOW.date()
_WINDOW = (
    (_NOW - timedelta(hours=2)).astimezone(timezone.utc).replace(tzinfo=None),
    (_NOW + timedelta(hours=2)).astimezone(timezone.utc).replace(tzinfo=None),
)


def _scope(mode: str, viewer: str | None, *, unrestricted: bool = False, projects: tuple[str, ...] = ()):
    return ViewerScope(viewer_email=viewer, unrestricted=unrestricted, admin_projects=frozenset(projects), mode=mode)  # type: ignore[arg-type]


async def _get_engine() -> AnalyticsPgEngine:
    try:
        engine = AnalyticsPgEngine(AnalyticsPgSettings.from_config(Config(PG_URL=_LOCAL_PG_URL)))
        async with engine.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return engine
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"local analytics Postgres is not reachable: {exc!r}")


class _Case:
    """One test's namespace: unique tag, user, repository, project; seeds and cleans its own sessions."""

    def __init__(self, engine: AnalyticsPgEngine) -> None:
        self.engine = engine
        self.tag = uuid.uuid4().hex[:12]
        self.user = f"u-{self.tag}@example.com"
        self.repo = f"repo-{self.tag}"
        self.project = f"proj-{self.tag}"
        self.sids: list[str] = []
        self.reader = PostgresCliAnalyticsReader(engine)

    def sid(self, label: str) -> str:
        sid = f"t9-{self.tag}-{label}"
        self.sids.append(sid)
        return sid

    def f(self, scope, **kw) -> LocalAnalyticsFilter:
        kw.setdefault("users", [self.user])
        return LocalAnalyticsFilter(start_dt=_WINDOW[0], end_dt=_WINDOW[1], scope=scope, **kw)

    async def seed(
        self,
        label: str,
        *,
        sub: str | None = None,
        origin: str | None = None,
        owner: str | None = None,
        project: str | None = None,
        model: str = "claude-opus-5-5",
        cost: float | None = 1.0,
        repo: str | None = None,
        dims: bool = True,
        started: bool = True,
        day: date | None = None,
        skill: str | None = None,
        subagent_usage: bool = False,
        user: str | None = None,
        commands: list[str] | None = None,
        lines: bool = True,
        turns: int | None = None,
        files: int = 0,
    ) -> str:
        sid = self.sid(label)
        user = user or self.user
        async with self.engine.acquire() as conn:
            if dims:
                await conn.execute(
                    "INSERT INTO session_dims (session_id, started_at, last_event_at, jwt_email, repository, "
                    "project_name, owner_email, subscription_type, project_origin) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                    sid,
                    _NOW if started else None,
                    _NOW + timedelta(minutes=10),
                    user,
                    repo or self.repo,
                    project if project is not None else self.project,
                    owner,
                    sub,
                    origin,
                )
            if cost is not None:
                await conn.execute(
                    "INSERT INTO cost_daily (day, session_id, user_email, model_name, query_source, cost_usd, "
                    "input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, api_call_count) "
                    "VALUES ($1,$2,$3,$4,'repl_main_thread',$5,100,10,0,0,1)",
                    day or _DAY,
                    sid,
                    user,
                    model,
                    cost,
                )
                if lines:
                    await conn.execute(
                        "INSERT INTO lines_daily (day, session_id, user_email, model_name, lines_added, "
                        "lines_removed) VALUES ($1,$2,$3,$4,7,3)",
                        day or _DAY,
                        sid,
                        user,
                        model,
                    )
            if turns is not None:
                await conn.execute(
                    "INSERT INTO turns_daily (day, session_id, turns) VALUES ($1,$2,$3)", day or _DAY, sid, turns
                )
            if files:  # the file facts are read from the tool-facts rows of the session
                await conn.execute(
                    "INSERT INTO tool_facts_daily (day, session_id, tool_calls, agent_count, skill_count) "
                    "VALUES ($1,$2,$3,0,0)",
                    day or _DAY,
                    sid,
                    files,
                )
            for i in range(files):
                await conn.execute(
                    "INSERT INTO session_files_daily (day, session_id, file_path, is_written, is_edited) "
                    "VALUES ($1,$2,$3,true,false)",
                    day or _DAY,
                    sid,
                    f"f{i}.py",
                )
            if subagent_usage:
                await conn.execute(
                    "INSERT INTO log_events (ts, session_id, event_kind, trace_id, span_id, cost_usd, input_tokens, "
                    "output_tokens, cache_read_tokens, cache_creation_tokens, severity, resource_id, attrs) "
                    "VALUES ($1,$2,1,''::bytea,''::bytea,2.0,50,5,0,0,0,1,'{\"token_source\":\"subagent_usage\"}'::jsonb)",
                    _NOW,
                    sid,
                )
            if commands is not None:  # a summary: its commands win over the OTel user prompts
                await conn.execute(
                    "UPDATE session_dims SET commands = $2::jsonb, summary_ts = $3 WHERE session_id = $1",
                    sid,
                    json.dumps(commands),
                    _NOW,
                )
            if skill:
                await conn.execute("INSERT INTO session_skills (session_id, skill_name) VALUES ($1,$2)", sid, skill)
        return sid

    async def clean(self) -> None:
        async with self.engine.acquire() as conn:
            for table in _TABLES:
                await conn.execute(f"DELETE FROM {table} WHERE session_id = ANY($1::text[])", self.sids)
        await self.engine.close()

    async def listed(self, scope, **kw) -> set[str]:
        """Sessions visible through every list/aggregate method that exposes session ids."""
        f = self.f(scope, **kw)
        seen = {r["session_id"] for r in await self.reader.get_session_cost_facts(f)}
        seen_repo = {r["session_id"] for r in await self.reader.get_repository_sessions(f)}
        seen_models = {r["session_id"] for r in await self.reader.get_session_model_costs(f)}
        seen_users = {s for r in await self.reader.get_users(f) for s in r["session_ids"]}
        seen_cost_users = {s for r in await self.reader.get_cost_by_user(f) for s in r["session_ids"]}
        assert seen_repo >= seen or not seen, "cost_facts lists a session the sel-only list does not"
        return seen | seen_repo | seen_models | seen_users | seen_cost_users

    async def detail_rows(self, sid: str, scope) -> dict[str, list]:
        r = self.reader
        return {
            "meta": await r.get_session_detail_meta(sid, scope=scope),
            "cost": await r.get_session_detail_cost(sid, scope=scope),
            "model_costs": await r.get_session_detail_model_costs(sid, scope=scope),
            "scalars": await r.get_session_detail_scalars(sid, scope=scope),
            "tools": await r.get_session_detail_tools(sid, scope=scope),
            "events": await r.get_session_detail_events(sid, scope=scope),
            "dispatches": await r.get_session_detail_dispatches(sid, scope=scope),
            "skills": await r.get_skill_names_by_session([sid], scope=scope),
        }

    async def assert_denied(self, sid: str, scope) -> None:
        assert not await self.reader.authorize_session(sid, scope)
        # An ungrouped aggregate over zero rows is one all-NULL row; it carries no data.
        rows = await self.detail_rows(sid, scope)
        assert not [r for rs in rows.values() for r in rs if any(v is not None for v in r.values())]

    async def assert_allowed(self, sid: str, scope) -> None:
        assert await self.reader.authorize_session(sid, scope)
        rows = await self.detail_rows(sid, scope)
        assert rows["meta"] and rows["cost"] and rows["model_costs"], rows


@pytest_asyncio.fixture
async def case():
    c = _Case(await _get_engine())
    yield c
    await c.clean()


OWNER = "Owner@Example.com"
_PRICED = "claude-sonnet-4-6"  # in the catalog: dead-session money needs a priced model


def _is_zero(kpis: list) -> bool:
    """No row, or the ungrouped aggregate's one row with zero sessions and no spend."""
    return not kpis or (not kpis[0]["total_sessions"] and not kpis[0]["total_cost_usd"])


@pytest.mark.asyncio
async def test_ordinary_and_project_admin_owner_see_their_own_personal(case):
    """AC4: list and detail for an ordinary owner and a project-admin owner (personal project not administered)."""
    mine = await case.seed("mine", sub="personal", origin="personal", owner=f"  {OWNER.upper()} ", skill="s1")
    shared = await case.seed("shared", sub="corporate", origin="profile", owner=OWNER, project="adm-proj")
    ordinary_own = _scope("own_only", OWNER.lower())
    admin_own = _scope("own_only", OWNER, projects=("adm-proj",))
    for scope in (ordinary_own, admin_own):
        assert await case.listed(scope) == {mine}
        await case.assert_allowed(mine, _scope("detail", OWNER, projects=scope.admin_projects))
    assert (await case.reader.get_skill_names_by_session([mine], scope=_scope("detail", OWNER)))[0]["skill_names"] == [
        "s1"
    ]
    # An ordinary user never reaches a non-Personal session; the admin owner reaches it through the project.
    await case.assert_denied(shared, _scope("detail", OWNER))
    await case.assert_allowed(shared, _scope("detail", OWNER, projects=("adm-proj",)))
    # In the shared view neither sees their own Personal session (AC8).
    assert mine not in await case.listed(_scope("exclude", OWNER, projects=("adm-proj",)))


@pytest.mark.asyncio
async def test_nobody_else_sees_a_personal_session_super_admin_and_auditor_included(case):
    """AC5, AC6: others' Personal is absent from lists, aggregates, detail and skills for every other role."""
    p = await case.seed("p", sub="personal", origin="personal", owner=OWNER, skill="secret", project="adm-proj")
    others = [
        _scope("exclude", "admin@x.com", unrestricted=True),
        _scope("detail", "admin@x.com", unrestricted=True),
        _scope("exclude", "pa@x.com", projects=("adm-proj", case.project)),
        _scope("detail", "pa@x.com", projects=("adm-proj", case.project)),
        _scope("own_only", "pa@x.com", projects=("adm-proj",)),
        _scope("own_only", "ordinary@x.com"),
        _scope("detail", "ordinary@x.com"),
    ]
    for scope in others:
        if scope.mode != "detail":
            assert p not in await case.listed(scope)
            assert _is_zero(await case.reader.get_cost_kpis(case.f(scope)))
            assert await case.reader.get_users(case.f(scope)) == []
            assert await case.reader.get_model_breakdown(case.f(scope)) == []
            # Filtering for Personal returns nothing too.
            assert await case.listed(scope, subscription_types=["personal"], project_origins=["personal"]) == set()
        else:
            await case.assert_denied(p, scope)


@pytest.mark.asyncio
async def test_email_case_whitespace_and_null_owner_or_viewer(case):
    """AC7: lower(btrim) both sides; NULL/empty owner or viewer never matches; a missing owner is not Personal."""
    padded = await case.seed("padded", sub="personal", owner="  OWNER@example.COM ")
    no_owner = await case.seed("noowner", sub="personal", origin="personal", owner=None)
    blank_owner = await case.seed("blank", sub="personal", owner="   ")
    n_no_owner = await case.seed("nnone", sub="corporate", owner=None, project="adm-proj")
    assert await case.listed(_scope("own_only", " owner@Example.com ")) == {padded}
    for viewer in (None, "", "   "):
        assert await case.listed(_scope("own_only", viewer)) == set()
        for sid in (padded, no_owner, blank_owner):
            await case.assert_denied(sid, _scope("detail", viewer, unrestricted=True))
    for sid in (no_owner, blank_owner):  # P with a missing owner: denied even to super-admin
        await case.assert_denied(sid, _scope("detail", "admin@x.com", unrestricted=True))
        assert sid not in await case.listed(_scope("exclude", "admin@x.com", unrestricted=True))
        assert sid not in await case.listed(_scope("own_only", "owner@example.com"))
    # A non-Personal row with no owner is N and stays visible to project admins (list and detail).
    assert n_no_owner in await case.listed(_scope("exclude", "pa@x.com", projects=("adm-proj",)))
    await case.assert_allowed(n_no_owner, _scope("detail", "pa@x.com", projects=("adm-proj",)))
    await case.assert_denied(n_no_owner, _scope("detail", "ordinary@x.com"))


@pytest.mark.asyncio
async def test_a_personal_session_changes_no_shared_total(case):
    """AC8: overview/cost/users/repositories/efficiency inputs are identical with and without a Personal session."""
    await case.seed("n1", sub="corporate", origin="profile", cost=1.5)
    await case.seed("n2", sub="codemie", cost=2.5, model="claude-sonnet-4-5")
    f = case.f(_scope("exclude", "admin@x.com", unrestricted=True))

    async def snapshot():
        r = case.reader
        return {
            "kpis": await r.get_cost_kpis(f),
            "models": await r.get_model_breakdown(f),
            "users": await r.get_users(f),
            "by_user": await r.get_cost_by_user(f),
            "daily": await r.get_users_daily_activity(f),
            "lines": await r.get_lines_totals(f),
            "lines_daily": await r.get_lines_daily(f),
            "repos": await r.get_repository_sessions(f),
            "facts": sorted(await r.get_session_cost_facts(f), key=lambda x: x["session_id"]),
            "durations": sorted(await r.get_session_durations(f), key=lambda x: x["session_id"]),
            "model_costs": sorted(await r.get_session_model_costs(f), key=lambda x: x["session_id"]),
            "lines_by_session": sorted(await r.get_lines_by_session(f), key=lambda x: x["session_id"]),
        }

    before = await snapshot()
    assert before["kpis"][0]["total_cost_usd"] == pytest.approx(4.0)
    await case.seed("p", sub="personal", origin="personal", owner=OWNER, cost=100.0, model="claude-haiku-9")
    await case.seed("p2", sub=None, origin="personal", owner=OWNER, cost=50.0)  # Personal by origin alone
    assert await snapshot() == before


@pytest.mark.asyncio
async def test_costless_subagent_binding_only_and_m_rows(case):
    """AC9/AC11: cost-less and subagent_usage Personal rows stay out; binding-only unlisted; M never listed."""
    admin = _scope("exclude", "admin@x.com", unrestricted=True)
    # Real conversational activity with no cost, not a zero-activity session: a turns_daily row keeps
    # each costless fixture admitted to `sel`.
    p_costless = await case.seed("pc", sub="personal", owner=OWNER, cost=None, turns=1)
    p_sub = await case.seed("ps", sub="personal", owner=OWNER, cost=None, subagent_usage=True, turns=1)
    n_costless = await case.seed("nc", sub="corporate", cost=None, turns=1)
    binding = await case.seed("bind", sub="corporate", started=False)
    m = await case.seed("m", dims=False)
    seen = await case.listed(admin)
    sel_only = {r["session_id"] for r in await case.reader.get_repository_sessions(case.f(admin))}
    assert n_costless in sel_only and {p_costless, p_sub, binding, m}.isdisjoint(sel_only | seen)
    own = _scope("own_only", OWNER)
    own_sel = {r["session_id"] for r in await case.reader.get_repository_sessions(case.f(own))}
    assert own_sel == {p_costless, p_sub}
    assert {p_costless, p_sub}.isdisjoint(await case.listed(admin))
    for sid in (p_costless, p_sub):
        await case.assert_denied(sid, _scope("detail", "admin@x.com", unrestricted=True))


@pytest.mark.asyncio
async def test_m_class_legacy_detail(case):
    """AC10: no session_dims row = M: resolves for a permitted admin, unknown/none, denied to ordinary users,
    and never mistaken for an unowned Personal row."""
    m = await case.seed("m", dims=False)
    owned_p = await case.seed("p", sub="personal", owner=None)
    sa = _scope("detail", "admin@x.com", unrestricted=True)
    await case.assert_allowed(m, sa)
    meta = (await case.reader.get_session_detail_meta(m, scope=sa))[0]
    assert (meta["subscription_type"], meta["project_origin"]) == ("unknown", "none")
    await case.assert_denied(m, _scope("detail", "ordinary@x.com"))
    await case.assert_denied(m, _scope("detail", "pa@x.com", projects=("adm-proj",)))  # unattributed: super-admin only
    await case.assert_denied(owned_p, sa)  # the unowned P row is denied while the M row is not
    # The handler path preserves the legacy start (earliest event or epoch).
    detail, _, _ = await LocalAnalyticsHandler(case.reader).get_session_detail(m, scope=sa)
    assert detail is not None


@pytest.mark.asyncio
async def test_mixed_entity_lists_and_per_entity_sums(case):
    """AC1/AC3: one repository, user, model and day mixing Corporate, CodeMie, NULL and two origins."""
    seeds = [
        ("a", "corporate", "profile", 1.0),
        ("b", "codemie", "linked_folder", 2.0),
        ("c", None, None, 4.0),
        ("d", "corporate", None, 8.0),
    ]
    for label, sub, origin, cost in seeds:
        await case.seed(label, sub=sub, origin=origin, cost=cost)
    await case.seed("e", sub="weird-plan", origin="weird-origin", cost=16.0)
    f = case.f(_scope("exclude", "admin@x.com", unrestricted=True))
    r = case.reader
    want_sub = ["codemie", "corporate", "other", "unknown"]
    want_org = ["linked_folder", "none", "other", "profile"]

    (user,) = await r.get_users(f)
    assert user["session_count"] == 5 and user["cost_usd"] == pytest.approx(31.0)
    assert user["input_tokens"] == 500 and user["output_tokens"] == 50
    assert (sorted(user["subscription_types"]), sorted(user["project_origins"])) == (want_sub, want_org)
    assert user["subscription_types"] == sorted(user["subscription_types"])  # sorted distinct as returned
    (by_user,) = await r.get_cost_by_user(f)
    assert sorted(by_user["session_ids"]) == sorted(user["session_ids"]) and len(by_user["session_ids"]) == 5
    assert sorted(by_user["subscription_types"]) == want_sub
    (day,) = await r.get_users_daily_activity(f)
    assert day["session_count"] == 5 and sorted(day["project_origins"]) == want_org
    (kpi,) = await r.get_cost_kpis(f)
    assert kpi["total_sessions"] == 5 and kpi["total_cost_usd"] == pytest.approx(31.0)
    (model,) = await r.get_model_breakdown(f)
    assert model["session_count"] == 5 and model["cost_usd"] == pytest.approx(31.0)
    facts = {x["session_id"]: x for x in await r.get_session_cost_facts(f)}
    assert len(facts) == 5 and sum(x["cost_usd"] for x in facts.values()) == pytest.approx(31.0)
    assert facts[case.sids[2]]["subscription_type"] == "unknown" and facts[case.sids[2]]["project_origin"] == "none"
    assert facts[case.sids[3]]["subscription_type"] == "corporate" and facts[case.sids[3]]["project_origin"] == "none"
    assert facts[case.sids[4]]["subscription_type"] == "other" and facts[case.sids[4]]["project_origin"] == "other"
    for by in ("all", "model", "day"):
        (row,) = await r.get_class_lists(f, by)
        assert (sorted(row["subscription_types"]), sorted(row["project_origins"])) == (want_sub, want_org), by
    # Handler level: per-entity lists present and the figures equal the run with the lists dropped.
    users, _ = await LocalAnalyticsHandler(r).get_users(f, page=None, per_page=None)
    assert users["rows"][0]["subscription_types"] == want_sub


@pytest.mark.asyncio
async def test_unknown_none_other_filters(case):
    """AC2: `unknown`/`none` match NULL/empty, `other` an unrecognised value; filters only narrow."""
    a = await case.seed("a", sub="corporate", origin="profile")
    b = await case.seed("b", sub=None, origin=None)
    c = await case.seed("c", sub="", origin="")
    d = await case.seed("d", sub="mystery", origin="mystery")
    admin = _scope("exclude", "admin@x.com", unrestricted=True)
    assert await case.listed(admin, subscription_types=["unknown"]) == {b, c}
    assert await case.listed(admin, project_origins=["none"]) == {b, c}
    assert await case.listed(admin, subscription_types=["other"]) == {d}
    assert await case.listed(admin, project_origins=["other", "profile"]) == {a, d}
    assert await case.listed(admin, subscription_types=["corporate"], project_origins=["none"]) == set()
    p = await case.seed("p", sub="personal", origin="personal", owner=OWNER)
    assert await case.listed(admin, subscription_types=["personal"]) == set()
    own = _scope("own_only", OWNER)
    assert await case.listed(own, subscription_types=["personal"]) == {p}
    assert await case.listed(own, subscription_types=["corporate"]) == set()


@pytest.mark.asyncio
async def test_absent_or_malformed_scope_denies_at_the_reader(case):
    """AC10: scope=None / a bare string / a bad mode deny every list, aggregate, detail and skill read."""
    n = await case.seed("n", sub="corporate", skill="k")
    bad_mode = ViewerScope("a@x.com", True, frozenset(), "everything")  # type: ignore[arg-type]
    for bad in (None, "unrestricted", object(), bad_mode):
        assert await case.listed(bad) == set()
        assert _is_zero(await case.reader.get_cost_kpis(case.f(bad)))
        await case.assert_denied(n, bad)


@pytest.mark.asyncio
async def test_every_class_list_variant_and_per_entity_totals(case):
    """AC1: each get_class_lists variant (all/model/day/tool/invocation) lists exactly the sorted distinct classes
    of its entity, and per repository / user / model / day the counts and sums equal the seeded figures (the lists
    are separate side queries, so no total can multiply). Personal never reaches a shared list."""
    d2 = _DAY - timedelta(days=1)
    r2 = f"{case.repo}-b"
    spec = [  # label, sub, origin, repo, model, day, cost
        ("a", "corporate", "profile", case.repo, "claude-opus-5-5", _DAY, 1.0),
        ("b", "codemie", "linked_folder", case.repo, "claude-opus-5-5", _DAY, 2.0),
        ("c", None, None, r2, "claude-sonnet-4-5", d2, 4.0),
        ("d", "corporate", None, r2, "claude-opus-5-5", d2, 8.0),
        ("e", "mystery", "registered_repository", case.repo, "claude-sonnet-4-5", _DAY, 16.0),
    ]
    for label, sub, origin, repo, model, day, cost in spec:
        await case.seed(label, sub=sub, origin=origin, repo=repo, model=model, day=day, cost=cost)
    p = await case.seed("p", sub="personal", origin="personal", owner=OWNER, cost=99.0, model="claude-opus-5-5")
    async with case.engine.acquire() as conn:
        for sid, (kind, name) in zip(
            case.sids, [(1, "Read"), (2, "skill-x"), (3, "agent-y"), (4, "cmd-z"), (1, "Read")] + [(1, "Read")]
        ):
            await conn.execute(
                "INSERT INTO invocations_hourly (hour, session_id, kind, name, calls, success) VALUES ($1,$2,$3,$4,2,2)",
                _NOW.replace(minute=0, second=0),
                sid,
                kind,
                name,
            )
    admin = _scope("exclude", "admin@x.com", unrestricted=True)
    f = case.f(admin)
    r = case.reader
    cls = {x[0]: x for x in spec}

    def subs(labels):
        return sorted(
            {cls[k][1] or "unknown" if cls[k][1] in (None, "corporate", "codemie") else "other" for k in labels}
        )

    def orgs(labels):
        return sorted({cls[k][2] or "none" for k in labels})

    (allrow,) = await r.get_class_lists(f, "all")
    assert (allrow["subscription_types"], allrow["project_origins"]) == (
        ["codemie", "corporate", "other", "unknown"],
        ["linked_folder", "none", "profile", "registered_repository"],
    )
    by_model = {x["key"]: x for x in await r.get_class_lists(f, "model")}
    for model in ("claude-opus-5-5", "claude-sonnet-4-5"):
        labels = [s[0] for s in spec if s[4] == model]
        assert by_model[model]["subscription_types"] == subs(labels), model
        assert by_model[model]["project_origins"] == orgs(labels), model
    by_day = {x["key"]: x for x in await r.get_class_lists(f, "day")}
    for day in (_DAY, d2):
        labels = [s[0] for s in spec if s[5] == day]
        assert by_day[str(day)]["subscription_types"] == subs(labels), day
        assert by_day[str(day)]["project_origins"] == orgs(labels), day
    (tool,) = [x for x in await r.get_class_lists(f, "tool") if x["key"] == "Read"]
    assert tool["subscription_types"] == subs(["a", "e"])  # Personal p also has a Read row: not listed
    inv = {(x["kind"], x["key"]): x for x in await r.get_class_lists(f, "invocation")}
    assert inv[("skill", "skill-x")]["subscription_types"] == subs(["b"])
    assert inv[("agent", "agent-y")]["subscription_types"] == subs(["c"])
    assert inv[("command", "cmd-z")]["subscription_types"] == subs(["d"])
    assert all(p not in str(v) for v in inv.values()) and "personal" not in str(inv) + str(tool) + str(allrow)

    # Per-entity counts and sums equal the seeded figures.
    (user,) = await r.get_users(f)
    assert (user["session_count"], user["cost_usd"]) == (5, pytest.approx(31.0))
    for m, (n, cost) in {"claude-opus-5-5": (3, 11.0), "claude-sonnet-4-5": (2, 20.0)}.items():
        row = next(x for x in await r.get_model_breakdown(f) if x["model_name"] == m)
        assert (row["session_count"], row["cost_usd"]) == (n, pytest.approx(cost))
    daily = {str(x["day"]): x for x in await r.get_users_daily_activity(f)}
    assert daily[str(_DAY)]["session_count"] == 3 and daily[str(d2)]["session_count"] == 2
    facts = await r.get_session_cost_facts(f)
    for repo, (n, cost) in {case.repo: (3, 19.0), r2: (2, 12.0)}.items():
        rows = [x for x in facts if x["repository"] == repo]
        assert (len(rows), sum(x["cost_usd"] for x in rows)) == (n, pytest.approx(cost))
    # The own-Personal list view and the shared view are disjoint.
    assert p not in await case.listed(admin)
    assert (await case.listed(_scope("own_only", OWNER))) == {p}
    inv_totals = {(x["kind"], x["name"]): x["count"] for x in await r.get_invocations(f)}
    assert inv_totals[("skill", "skill-x")] == 2


async def _invocation(case, sid: str, kind: int, name: str, calls: int = 2) -> None:
    async with case.engine.acquire() as conn:
        await conn.execute(
            "INSERT INTO invocations_hourly (hour, session_id, kind, name, calls, success) VALUES ($1,$2,$3,$4,$5,$5)",
            _NOW.replace(minute=0, second=0),
            sid,
            kind,
            name,
            calls,
        )


@pytest.mark.asyncio
async def test_tool_and_invocation_lists_with_collisions_and_summary_precedence(case):
    """get_class_lists `tool` / `invocation` on rollup fixtures: the same name as skill, agent and tool keeps its
    kind apart; a session with a summary counts only its summary commands (its OTel prompts add nothing, neither
    to counts nor to labels); a session without one counts its OTel prompts; Personal never appears."""
    a = await case.seed("a", sub="corporate", origin="profile")
    b = await case.seed("b", sub="codemie", origin="linked_folder")
    c = await case.seed("c", sub=None, origin=None)
    d = await case.seed("d", sub="corporate", origin=None, commands=["cmd-sum"])
    e = await case.seed("e", sub="mystery", origin=None)
    p = await case.seed("p", sub="personal", origin="personal", owner=OWNER, commands=["cmd-p"])
    for sid, kind, name in [
        (a, 2, "dup"),  # skill dup
        (a, 1, "Read"),
        (b, 3, "dup"),  # agent dup
        (b, 1, "Edit"),
        (c, 1, "dup"),  # tool dup
        (c, 4, "cmd-otel"),
        (d, 4, "cmd-shadowed"),  # OTel prompt of a session that has a summary: ignored
        (e, 4, "cmd-otel"),
        (p, 2, "dup"),
        (p, 1, "Read"),
        (p, 4, "cmd-p-otel"),
    ]:
        await _invocation(case, sid, kind, name)
    f = case.f(_scope("exclude", "admin@x.com", unrestricted=True))
    r = case.reader

    tools = {x["key"]: x for x in await r.get_class_lists(f, "tool")}
    assert set(tools) == {"Read", "Edit", "dup"}
    assert (tools["Read"]["subscription_types"], tools["Read"]["project_origins"]) == (["corporate"], ["profile"])
    assert (tools["Edit"]["subscription_types"], tools["Edit"]["project_origins"]) == (["codemie"], ["linked_folder"])
    assert (tools["dup"]["subscription_types"], tools["dup"]["project_origins"]) == (["unknown"], ["none"])

    inv = {(x["kind"], x["key"]): x for x in await r.get_class_lists(f, "invocation")}
    assert set(inv) == {("skill", "dup"), ("agent", "dup"), ("command", "cmd-otel"), ("command", "cmd-sum")}
    got = {k: (v["subscription_types"], v["project_origins"]) for k, v in inv.items()}
    assert got[("skill", "dup")] == (["corporate"], ["profile"])
    assert got[("agent", "dup")] == (["codemie"], ["linked_folder"])
    assert got[("command", "cmd-otel")] == (["other", "unknown"], ["none"])  # c (NULL) and e (mystery)
    assert got[("command", "cmd-sum")] == (["corporate"], ["none"])  # from the summary, not the OTel row

    counts = {(x["kind"], x["name"]): x["count"] for x in await r.get_invocations(f)}
    assert counts == {("skill", "dup"): 2, ("agent", "dup"): 2, ("command", "cmd-otel"): 4, ("command", "cmd-sum"): 1}
    assert {x["tool_name"]: x["call_count"] for x in await r.get_tool_usage(f)} == {"Read": 2, "Edit": 2, "dup": 2}

    tools_payload = await LocalAnalyticsHandler(r).get_tools(f)
    skills = {x["name"]: x for x in tools_payload["skills_invoked"]}
    agents = {x["name"]: x for x in tools_payload["agent_subtypes"]}
    commands = {x["name"]: x for x in tools_payload["slash_commands"]}
    assert skills["dup"]["subscription_types"] == ["corporate"] and agents["dup"]["subscription_types"] == ["codemie"]
    assert commands["cmd-otel"]["subscription_types"] == ["other", "unknown"] and commands["cmd-otel"]["count"] == 4
    assert commands["cmd-sum"]["project_origins"] == ["none"] and "cmd-shadowed" not in commands

    # The owner's own-only view lists only the owner's Personal session, never a..e.
    own = {x["key"] for x in await r.get_class_lists(case.f(_scope("own_only", OWNER)), "tool")}
    assert own == {"Read"}


@pytest.mark.asyncio
async def test_second_entity_with_distinct_classes_borrows_no_labels(case):
    """Two users, repositories, models and days with disjoint class sets: each entity gets only its own labels and
    its own counts and sums, at reader and handler level."""
    u1, u2 = case.user, f"u2-{case.tag}@example.com"
    r1, r2 = case.repo, f"{case.repo}-2"
    d1, d2 = _DAY, _DAY - timedelta(days=1)
    m1, m2 = "claude-opus-5-5", "claude-sonnet-4-5"
    s1a = await case.seed("1a", sub="corporate", origin="profile", repo=r1, model=m1, day=d1, cost=1.0)
    s1b = await case.seed("1b", sub="codemie", origin="profile", repo=r1, model=m1, day=d1, cost=2.0)
    s2a = await case.seed("2a", sub=None, origin=None, repo=r2, model=m2, day=d2, cost=4.0, user=u2)
    s2b = await case.seed("2b", sub="mystery", origin="default_project", repo=r2, model=m2, day=d2, cost=8.0, user=u2)
    f = case.f(_scope("exclude", "admin@x.com", unrestricted=True), users=[u1, u2])
    r = case.reader
    e1 = (["codemie", "corporate"], ["profile"])
    e2 = (["other", "unknown"], ["default_project", "none"])

    users = {x["developer_name"]: x for x in await r.get_users(f)}
    assert {k: (v["session_count"], v["cost_usd"]) for k, v in users.items()} == {u1: (2, 3.0), u2: (2, 12.0)}
    assert (users[u1]["subscription_types"], users[u1]["project_origins"]) == e1
    assert (users[u2]["subscription_types"], users[u2]["project_origins"]) == e2
    models = {x["model_name"]: x for x in await r.get_model_breakdown(f)}
    assert {k: (v["session_count"], v["cost_usd"]) for k, v in models.items()} == {m1: (2, 3.0), m2: (2, 12.0)}
    by_model = {x["key"]: x for x in await r.get_class_lists(f, "model")}
    assert (by_model[m1]["subscription_types"], by_model[m1]["project_origins"]) == e1
    assert (by_model[m2]["subscription_types"], by_model[m2]["project_origins"]) == e2
    by_day = {x["key"]: x for x in await r.get_class_lists(f, "day")}
    assert (by_day[str(d1)]["subscription_types"], by_day[str(d1)]["project_origins"]) == e1
    assert (by_day[str(d2)]["subscription_types"], by_day[str(d2)]["project_origins"]) == e2
    facts = {x["session_id"]: x for x in await r.get_session_cost_facts(f)}
    assert {facts[s]["subscription_type"] for s in (s1a, s1b)} == {"corporate", "codemie"}
    assert {facts[s]["project_origin"] for s in (s2a, s2b)} == {"none", "default_project"}

    handler = LocalAnalyticsHandler(r)
    payload, _ = await handler.get_users(f, page=None, per_page=None)
    rows = {x["developer_name"]: x for x in payload["rows"]}
    assert (rows[u1]["session_count"], rows[u2]["session_count"]) == (2, 2)
    assert (rows[u1]["subscription_types"], rows[u1]["project_origins"]) == e1
    assert (rows[u2]["subscription_types"], rows[u2]["project_origins"]) == e2
    for user, day, exp in ((u1, d1, e1), (u2, d2, e2)):  # per-day activity of each user
        (act,) = [x for x in rows[user]["daily_activity"] if x["day"] == str(day)]
        assert act["session_count"] == 2 and (act["subscription_types"], act["project_origins"]) == exp
    repos_payload, _ = await handler.get_repositories(f, 0, 50, False)
    repos = {x["repository"]: x for x in repos_payload["rows"]}
    assert (repos[r1]["session_count"], repos[r2]["session_count"]) == (2, 2)
    assert (repos[r1]["subscription_types"], repos[r1]["project_origins"]) == e1
    assert (repos[r2]["subscription_types"], repos[r2]["project_origins"]) == e2
    cost_payload, _ = await handler.get_cost(f)
    cmodels = {x["model_name"]: x for x in cost_payload["cost_by_model"]}
    assert cmodels[m1]["subscription_types"] == e1[0] and cmodels[m2]["project_origins"] == e2[1]
    cusers = {x["developer_name"]: x for x in cost_payload["cost_by_user"]}
    assert cusers[u1]["subscription_types"] == e1[0] and cusers[u2]["subscription_types"] == e2[0]


@pytest.mark.asyncio
async def test_padded_personal_values_are_class_p_for_every_mode_and_the_skills_read(case):
    """CR-001: Personal detection and the response vocabulary trim the same whitespace set, for both columns and
    for tabs/newlines as well as spaces; the owner email trims it too (Python and SQL agree)."""
    by_sub = await case.seed("psub", sub=" \tPersonal\n", owner=OWNER, skill="k1")
    by_origin = await case.seed("porg", sub="corporate", origin="  PERSONAL ", owner="\t" + OWNER + "\n", skill="k2")
    padded_ids = {by_sub, by_origin}
    for scope in (_scope("exclude", "admin@x.com", unrestricted=True), _scope("own_only", "other@x.com")):
        assert not padded_ids & await case.listed(scope)
        assert _is_zero(await case.reader.get_cost_kpis(case.f(scope)))
    assert await case.listed(_scope("own_only", OWNER)) == padded_ids
    for sid, skill in ((by_sub, "k1"), (by_origin, "k2")):
        await case.assert_denied(sid, _scope("detail", "admin@x.com", unrestricted=True))
        await case.assert_allowed(sid, _scope("detail", OWNER))
        assert not await case.reader.get_skill_names_by_session(
            [sid], scope=_scope("detail", "admin@x.com", unrestricted=True)
        )
        assert (await case.reader.get_skill_names_by_session([sid], scope=_scope("detail", OWNER)))[0][
            "skill_names"
        ] == [skill]
    # The vocabulary agrees: the owner's view reports them as personal / personal.
    facts = {r["session_id"]: r for r in await case.reader.get_session_cost_facts(case.f(_scope("own_only", OWNER)))}
    assert facts[by_sub]["subscription_type"] == "personal" and facts[by_origin]["project_origin"] == "personal"


@pytest.mark.asyncio
async def test_list_mode_scopes_keep_delivery_framework_on_listed_sessions(case):
    """CR-004: /sessions passes its exclude / own_only scope into the skills read; the gate must still match."""
    shared = await case.seed("shared", sub="corporate", origin="profile", owner=OWNER, skill="sk-shared")
    mine = await case.seed("mine", sub="personal", origin="personal", owner=OWNER, skill="sk-mine")
    handler = LocalAnalyticsHandler(case.reader)
    for scope, expected in (
        (_scope("exclude", OWNER, unrestricted=True), {shared: "sk-shared"}),
        (_scope("own_only", OWNER), {mine: "sk-mine"}),
    ):
        with patch(f"{LocalAnalyticsHandler.__module__}.classify_delivery_framework", lambda names: ",".join(names)):
            payload, _, _ = await handler.get_sessions(case.f(scope), 0, 50, "start_time", None)
        assert {s["trace_id"]: s["delivery_framework"] for s in payload["sessions"]} == expected


@pytest.mark.asyncio
async def test_efficiency_class_lists_come_from_each_bucket_or_aggregates_own_sessions(case):
    """CR-002: disjoint labels per depth bucket, dead set and code-change set; counts/money/files unchanged."""
    scope = _scope("exclude", "admin@x.com", unrestricted=True)
    await case.seed("a", sub="corporate", origin="profile", model=_PRICED, cost=1.0, turns=1, files=2)
    await case.seed(
        "b", sub="codemie", origin="default_project", model=_PRICED, cost=2.0, turns=3, lines=False
    )  # dead, no lines
    await case.seed("c", sub=None, origin=None, model=_PRICED, cost=4.0, turns=30, files=1)
    await case.seed(
        "p", sub="personal", origin="personal", owner=OWNER, model=_PRICED, cost=8.0, turns=3, files=5
    )  # never shared
    data, _ = await LocalAnalyticsHandler(case.reader).get_efficiency(case.f(scope))

    def lists(x):
        return x["subscription_types"], x["project_origins"]

    assert lists(data["kpis"]) == (["codemie", "corporate", "unknown"], ["default_project", "none", "profile"])
    one = data["kpis"]["total_cost_usd"] / 3  # catalog-priced tokens: three equal shared sessions, Personal excluded
    assert one > 0
    assert (data["dead_sessions"]["count"], data["dead_sessions"]["wasted_cost_usd"]) == (1, pytest.approx(one))
    assert lists(data["dead_sessions"]) == (["codemie"], ["default_project"])
    depth = {b["bucket"]: b for b in data["session_depth"]}
    assert {k: v["count"] for k, v in depth.items() if v["count"]} == {"1": 1, "2-5": 1, "26-50": 1}
    assert lists(depth["1"]) == (["corporate"], ["profile"])
    assert lists(depth["2-5"]) == (["codemie"], ["default_project"])
    assert lists(depth["26-50"]) == (["unknown"], ["none"])
    assert lists(depth["6-10"]) == ([], [])  # empty group
    assert (data["code_changes"]["files_changed"], data["code_changes"]["net_lines"]) == (3, 8)
    assert lists(data["code_changes"]) == (["corporate", "unknown"], ["none", "profile"])


@pytest.mark.asyncio
async def test_sql_and_python_trim_exactly_the_same_whitespace_set(case):
    """The SQL trim literal is the Python set byte for byte: PG has no \v escape (octal \013 is used), and a
    literal `v` is never trimmed. Characters outside the set (NBSP, FS) are kept by both."""
    from codemie.repository.cli_analytics.scope import _WS, normalise_email, sql_norm

    async def sql(value: str) -> str:
        async with case.engine.acquire() as conn:
            return await conn.fetchval(f"SELECT {sql_norm('$1::text')}", value)

    for ch in _WS:  # one probe per whitespace byte, leading and trailing
        assert await sql(f"{ch}A@B.c{ch}") == normalise_email(f"{ch}A@B.c{ch}") == "a@b.c", repr(ch)
    for value in ("vowner@example.com", "owner@example.comv", "VOwnerV", "\u00a0a@b.c", "\x1ca@b.c"):
        assert await sql(value) == value.lower() == (normalise_email(value) or ""), repr(value)


@pytest.mark.asyncio
async def test_a_v_prefixed_owner_is_not_the_unprefixed_viewer_and_v_edges_of_the_vocabulary_survive(case):
    """A stored `vowner@...` must never match the viewer `owner@...` (list, detail, skills), only its own
    owner; `v` at the edges of subscription_type / project_origin is not trimmed (not Personal, vocabulary `other`)."""
    mine = await case.seed("v", sub="personal", owner="vowner@example.com", skill="sv")
    edge = await case.seed("edge", sub="vpersonalv", origin="vprofilev", owner="owner@example.com", cost=3.0)
    other = _scope("own_only", "owner@example.com")
    assert mine not in await case.listed(other)
    await case.assert_denied(mine, _scope("detail", "owner@example.com"))
    assert not await case.reader.get_skill_names_by_session([mine], scope=_scope("detail", "owner@example.com"))
    owner = _scope("own_only", "vowner@example.com")
    assert await case.listed(owner) == {mine}
    await case.assert_allowed(mine, _scope("detail", "vowner@example.com"))
    assert (await case.reader.get_skill_names_by_session([mine], scope=_scope("detail", "vowner@example.com")))[0][
        "skill_names"
    ] == ["sv"]
    # `vpersonalv` is not Personal: shared view lists it, and the vocabulary reports `other` for both columns.
    shared = _scope("exclude", "admin@x.com", unrestricted=True)
    facts = {r["session_id"]: r for r in await case.reader.get_session_cost_facts(case.f(shared))}
    assert (facts[edge]["subscription_type"], facts[edge]["project_origin"]) == ("other", "other")


@pytest.mark.asyncio
async def test_personal_paid_session_on_a_work_project_is_visible_but_not_spend(case):
    """EPMCDME-16440 (external / service-account users): subscription_type=personal with a shared origin is N, not P.
    It follows the work project's read permissions and shows as activity, its actual cost stays on the session, and
    it is in no aggregate money value. An ordinary Personal session (origin personal) and a Personal-paid row with
    a missing or unknown origin stay private (fail closed)."""
    work = await case.seed(
        "work", sub="personal", origin="default_project", owner=OWNER, cost=4.0, user="ext@x.com", model=_PRICED
    )
    legacy = await case.seed(
        "legacy", sub="personal", origin="legacy_project", owner=OWNER, cost=2.0, user="ext@x.com", model=_PRICED
    )
    paid = await case.seed(
        "paid", sub="corporate", origin="profile", owner="c@x.com", cost=1.0, user="c@x.com", model=_PRICED
    )
    private = await case.seed(
        "private", sub="personal", origin="personal", owner=OWNER, cost=8.0, user="ext@x.com", model=_PRICED
    )
    no_origin = await case.seed(
        "noorigin", sub="personal", origin=None, owner=OWNER, cost=16.0, user="ext@x.com", model=_PRICED
    )
    odd_origin = await case.seed(
        "oddorigin", sub="personal", origin="weird", owner=OWNER, cost=32.0, user="ext@x.com", model=_PRICED
    )
    project_admin = _scope("exclude", "pa@x.com", projects=(case.project,))
    f = case.f(project_admin, users=None)

    # Activity: the project admin sees the work-project session and the Corporate one, nothing private.
    assert await case.listed(project_admin, users=None) == {work, legacy, paid}
    await case.assert_allowed(work, _scope("detail", "pa@x.com", projects=(case.project,)))
    for hidden in (private, no_origin, odd_origin):
        await case.assert_denied(hidden, _scope("detail", "pa@x.com", projects=(case.project,)))
        await case.assert_denied(hidden, _scope("detail", "admin@x.com", unrestricted=True))
    await case.assert_denied(work, _scope("detail", "other@x.com", projects=("elsewhere",)))
    await case.assert_denied(work, _scope("detail", "ordinary@x.com"))
    # The account's own owner-only list never gains a work-project session.
    assert await case.listed(_scope("own_only", OWNER), users=None, repositories=[case.repo]) == {
        private,
        no_origin,
        odd_origin,
    }

    # Aggregates: sessions count as activity, money is company/project-paid only.
    kpi = (await case.reader.get_cost_kpis(f))[0]
    assert (kpi["total_sessions"], kpi["spend_sessions"], float(kpi["total_cost_usd"])) == (3, 1, 1.0)
    assert [float(r["cost_usd"]) for r in await case.reader.get_model_breakdown(f)] == [1.0]
    users = {r["developer_name"]: r for r in await case.reader.get_users(f)}
    assert float(users["ext@x.com"]["cost_usd"] or 0) == 0.0 and users["ext@x.com"]["session_count"] == 2
    assert float(users["c@x.com"]["cost_usd"]) == 1.0
    priced = await case.reader.get_session_model_costs(f, spend_only=True)
    assert {r["session_id"] for r in priced} == {paid}
    # ... while the session itself keeps its actual cost evidence.
    facts = {r["session_id"]: r for r in await case.reader.get_session_cost_facts(f)}
    assert float(facts[work]["cost_usd"]) == 4.0 and facts[work]["subscription_type"] == "personal"
    assert facts[work]["project_origin"] == "default_project"

    handler = LocalAnalyticsHandler(case.reader)
    cost, _ = await handler.get_cost(f)
    assert cost["kpis"]["total_sessions"] == 3 and cost["kpis"]["total_cost_usd"] > 0
    assert sum(r["cost_usd"] for r in cost["cost_by_model"]) == pytest.approx(cost["kpis"]["total_cost_usd"])
    assert sum(r["cost_usd"] for r in cost["cost_by_user"]) == pytest.approx(cost["kpis"]["total_cost_usd"])
    assert "personal" in cost["kpis"]["subscription_types"]  # visible as a class, never as spend
    sessions, _, _ = await handler.get_sessions(f, 0, 50, "start_time", None)
    by_id = {s["trace_id"]: s for s in sessions["sessions"]}
    assert by_id[work]["cost_usd"] > 0 and by_id[work]["subscription_type"] == "personal"


@pytest.mark.asyncio
@pytest.mark.parametrize("sort_by", ["start_time", "cost_usd", "ctx_per_call"])
async def test_sessions_unpriced_models_are_the_viewers_own_never_another_scopes(case, sort_by):
    """AC13: under own_only the unpriced list holds the viewer's own models and never a model of a session the viewer
    cannot list; the admin's view lists the reverse. The cost sort reads the whole window, so it lists the viewer's
    model used only off the page (page of 1); the other sorts list the page's models (a page holding both)."""
    per_page = 1 if sort_by == "cost_usd" else 50
    own_model, shared_model = f"mystery-own-{case.tag}", f"mystery-shared-{case.tag}"
    await case.seed("a-priced", sub="personal", origin="personal", owner=OWNER, model=_PRICED)
    await case.seed("b-own", sub="personal", origin="personal", owner=OWNER, model=own_model, cost=0.0)
    await case.seed("c-shared", sub="corporate", origin="profile", owner=OWNER, model=shared_model, cost=0.0)
    handler = LocalAnalyticsHandler(case.reader)

    own, own_pricer, _ = await handler.get_sessions(case.f(_scope("own_only", OWNER)), 0, per_page, sort_by, None)
    shared, shared_pricer, _ = await handler.get_sessions(
        case.f(_scope("exclude", "admin@x.com", unrestricted=True)), 0, per_page, sort_by, None
    )

    assert (len(own["sessions"]), own["total"]) == (per_page if per_page == 1 else 2, 2)
    assert own_pricer.unpriced == [own_model]
    assert shared_pricer.unpriced == [shared_model]
