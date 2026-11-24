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

"""The per-user start-day rollup behind /users and /user-charts (EPMCDME-17051).

A session sits in exactly one rollup row, on the UTC day it started, with the values the live statements give for it
over its whole lifetime. Everything the Users page shows is a sum, a maximum or a set union over the sessions of the
window, so a window that is a run of whole start days is the combination of those days' rows. That equals the live
result for the sessions that started in the window; the few sessions that started before it and were active in it
(`resumed`) are what the live rule also admits, so they are read live and combined (`serve`).

The rows are not computed by a second implementation: `gather_cohort` runs the live reader's own statements over a
temp table holding one day's sessions, so a rolled-up value and a live value cannot disagree for the same sessions.

A row is also keyed by the session's subscription class and project-origin class (the values the
`subscription_type` and `project_origin` filters compare), so a request with either filter sums the rows of the classes
it asks for. A day is built one class pair at a time, each through the live statements under that pair's filter.

/cost reads the same rows plus `user_day_model_rollup` (per start day, class pair and normalised model: the spend money
of the model's sessions, from the live `G_MODEL` grouping), so its totals, its per-developer money and its per-model
money need neither the session selection nor a pricing pass of the window (`serve_cost`).

Who may read it: a request whose rows do not depend on who asks (`eligible`), whose window is whole days ending now,
whose days are all built under today's rates and recently. Anything else returns None and the caller takes the live
statements.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from codemie.repository.cli_analytics.filters import LocalAnalyticsFilter
from codemie.repository.cli_analytics.ports import (
    G_MODEL,
    G_PERSONAL,
    G_ROUTE,
    G_TOTAL,
    G_USER,
    G_USER_PERSONAL,
    G_USER_ROUTE,
    CostGroup,
    GroupedMoney,
    LiveMergeNeeded,
    ModelRates,
    RollupCost,
    RollupEfficiency,
    RollupOverview,
    RollupProjects,
    RollupRanking,
    RollupTools,
    RollupUsers,
    UsersMoneyPage,
)
from codemie.repository.cli_analytics.postgres import request_stats
from codemie.repository.cli_analytics.postgres.grouped_cost_sql import Grouping, grouped_cost_sql
from codemie.repository.cli_analytics.postgres.reader import (
    _C,
    _GRAIN_MODEL,
    PostgresCliAnalyticsReader,
    _ConnectionPair,
    _cost_rows,
    window_params,
)
from codemie.repository.cli_analytics.scope import ViewerScope, sql_project_origin, sql_subscription_type

logger = logging.getLogger(__name__)

# The scope the rollup is built under: a super admin's (every shared session, no personal one). The viewer's e-mail
# never enters the SQL of "exclude" mode, so one rollup serves every request of that scope.
ROLLUP_SCOPE = ViewerScope(viewer_email=None, unrestricted=True, admin_projects=frozenset(), mode="exclude")
# A window "up to now" ends within this many seconds of the clock; the rollup is only read for such windows because its
# latest-event column is the lifetime maximum, which is the live answer when nothing is later than the window end.
LIVE_END_SECONDS = 120
# More sessions than this that started earlier and are active in the window: the live statements answer instead.
# Reading n such sessions live costs about 0.3 s + n * 0.1 ms (measured at 600K sessions: 3,000 in 0.4 s, 10,000 in
# 1.1 s, 30,000 in 3.3 s), and a short window of a busy installation has thousands of them (a session lasts a few days,
# a tenth of them resume: 3,300 for a window of 7 days, 3,200 for 8, 3,300 for 14, 1,300 for 30 and 190 for 90 at 600K
# sessions), so 2,000 sent every window of a week or two to the live statements: Overview 5.0 s and 8.0 s, Cost 3.1 s
# and 5.3 s, Users 3.2 s and 5.3 s at 7 and 14 days, against 1.4 to 1.9 s with the rollup. 8,000 keeps them inside the
# 2 s target. A rollup read runs in the light lane, which assumes cheap reads, so every reader refuses the merge of such
# sessions there (`LiveMergeNeeded`) and the handler repeats the read in the heavy lane.
MAX_RESUMED = 8000
# The Repositories and Projects rankings read the same sessions under the same limit.
MAX_RESUMED_RANKING = MAX_RESUMED
# How old today's row may be before the reader stops trusting the job: a few of its runs, and at least this long.
MIN_FRESH_SECONDS = 180
# The job checks the last RECENT_DAYS days for changed sessions on every run and all days this often.
RECENT_DAYS = 2
FULL_SCAN_SECONDS = 600
# What a built day holds: 1 = the developer rows, 2 = also the model rows (what /users and /cost read), 3 = also the
# Overview rows, 4 = the money of the model and Overview rows covers every session, the Personal-paid ones too
# (before, only the spend's; EPMCDME-17260), 5 = also the ranking rows (the Repositories and Projects tabs read them).
# A shape only grows: a reader asks for the least shape that holds what it reads (`min_shape`) and the job rebuilds a
# day of a shape below the one it builds (`ROLLUP_SHAPE`), so a day of a higher shape (a newer pod's) is neither
# refused nor rebuilt down. The users, cost and Overview readers ask for shape 4: the money a day of a lower shape
# stores leaves the Personal-paid sessions out, so until the job has rebuilt a day the live statements answer for it.
# The Efficiency reader asks for shape 6 (the Efficiency rows). 7 = the ranking rows also hold each group's pricing
# (the raw names of its unpriced models and whether any of its usage was priced; EPMCDME-17152 reports it per row),
# which the ranking readers ask for: a day built before has no pricing to answer with, so the live statements answer
# for it until the job has rebuilt it. 8 = also the Tools & Models rows (`user_day_tools_rollup`,
# `user_day_tools_models`), which the Tools & Models reader asks for.
SHAPE_COST = 4
SHAPE_OVERVIEW = 4
SHAPE_RANKING = 7
SHAPE_EFFICIENCY = 6
SHAPE_TOOLS = 8
ROLLUP_SHAPE = max(SHAPE_RANKING, SHAPE_EFFICIENCY, SHAPE_TOOLS)  # the shape the job builds


# The planner's cost of a random page while the sessions that started before the window are read live. They are at
# most MAX_RESUMED of all the sessions, and under the default (4) every statement hash-joins them to a scan of every
# partition of the fact tables; index lookups per session took a tenth of the time (measured at 700K sessions).
SMALL_COHORT_RANDOM_PAGE_COST = "1.1"

_MONEY_KEYS = ["user", "funding_route", "personal_billed"]
_MONEY_GROUPINGS = (
    Grouping("ur_all", _MONEY_KEYS),
    Grouping("ur_cd", _MONEY_KEYS, cost_daily_only=True),
    Grouping("ur_model", ["model"]),
)
_CENT = Decimal("0.000000001")


# ── values ───────────────────────────────────────────────────────────────────────────────────────────────────────


def _top_key(top: tuple | None) -> tuple:
    """Order of the keys of a developer's top-model candidate: most calls first (NULLS LAST), then the earliest day and
    the key columns in code-point order, as `_TOP_MODEL` orders the cost rows of a window."""
    if top is None or top[0] is None:
        return (1,)
    return (0, -top[0], *top[1:])


@dataclass
class UserAgg:
    """What the Users page sums for one developer over a set of sessions."""

    session_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    turns: int = 0
    tool_calls: int = 0
    tool_successes: int = 0
    net_lines: int | None = None
    last_active: datetime | None = None
    subscription_types: set[str] = field(default_factory=set)
    project_origins: set[str] = field(default_factory=set)
    top: tuple | None = None  # (calls, day, session_id, user_email, model, query_source)

    def merge(self, other: UserAgg) -> None:
        for name in (
            "session_count",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_creation_tokens",
            "turns",
            "tool_calls",
            "tool_successes",
        ):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        if other.net_lines is not None:
            self.net_lines = (self.net_lines or 0) + other.net_lines
        if other.last_active is not None and (self.last_active is None or other.last_active > self.last_active):
            self.last_active = other.last_active
        self.subscription_types |= other.subscription_types
        self.project_origins |= other.project_origins
        if _top_key(other.top) < _top_key(self.top):
            self.top = other.top


@dataclass
class MoneyAgg:
    """The priced money of the sessions of one (developer, funding route, personal-billed): over every priced session
    (`all`) and over those whose token rows all come from `cost_daily` (`cd`)."""

    cost_all: Decimal = Decimal(0)
    sessions_all: int = 0
    unpriced_all: set[str] = field(default_factory=set)
    cost_cd: Decimal = Decimal(0)
    sessions_cd: int = 0
    unpriced_cd: set[str] = field(default_factory=set)

    def merge(self, other: MoneyAgg) -> None:
        self.cost_all += other.cost_all
        self.sessions_all += other.sessions_all
        self.unpriced_all |= other.unpriced_all
        self.cost_cd += other.cost_cd
        self.sessions_cd += other.sessions_cd
        self.unpriced_cd |= other.unpriced_cd


@dataclass
class ModelAgg:
    """The spend money of the sessions that used one normalised model: the priced cost, the sessions and the unpriced
    raw names (the live `G_MODEL` row)."""

    cost: Decimal = Decimal(0)
    sessions: int = 0
    unpriced: set[str] = field(default_factory=set)

    def merge(self, other: ModelAgg) -> None:
        self.cost += other.cost
        self.sessions += other.sessions
        self.unpriced |= other.unpriced


# The Overview's card totals: the field of `OverviewAgg`, the column of `user_day_overview_rollup` and the key of the
# window read are one name. The two money fields are numeric, the others bigint.
OVERVIEW_SUMS = (
    "turns",
    "tool_calls",
    "tool_successes",
    "duration_ms",
    "files_changed",
    "dead_sessions",
    "spend_cost",
    "cache_read_cost",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "api_calls",
)
_OVERVIEW_NUMERIC = ("spend_cost", "cache_read_cost")
_DEPTH_COLUMNS = ("depth_1", "depth_2_5", "depth_6_10", "depth_11_25", "depth_26_50", "depth_50_plus")


def _nadd(a: Any, b: Any) -> Any:
    """SQL `sum` over two groups: unknown only while both are."""
    return None if a is None and b is None else (a or 0) + (b or 0)


DEPTH_BUCKETS = ("1", "2-5", "6-10", "11-25", "26-50", "50+")


@dataclass
class EfficiencyAgg:
    """What the Efficiency tab holds beyond the Overview rows, over a set of sessions: the cost of the dead sessions,
    the sessions per depth bucket (`DEPTH_BUCKETS`), the files written and edited, the sessions that have a cost row
    and the worst session by cache-read tokens per API call (`(ratio, session id)`, None while no session has a call).
    See `gather_efficiency`."""

    dead_cost: Decimal = Decimal(0)
    depth: list[int] = field(default_factory=lambda: [0] * len(DEPTH_BUCKETS))
    files_written: int = 0
    files_edited: int = 0
    spend_sessions: int = 0
    worst: tuple[float, str] | None = None

    def merge(self, other: EfficiencyAgg) -> None:
        self.dead_cost += other.dead_cost
        self.depth = [a + b for a, b in zip(self.depth, other.depth, strict=True)]
        self.files_written += other.files_written
        self.files_edited += other.files_edited
        self.spend_sessions += other.spend_sessions
        if other.worst is not None and (self.worst is None or _worse(other.worst, self.worst)):
            self.worst = other.worst


def _worse(a: tuple[float, str], b: tuple[float, str]) -> bool:
    """Whether session `a` is the worse one: the larger ratio, ties to the lower id in code-point order (the live
    `ORDER BY ratio DESC, session_id COLLATE "C"`)."""
    return a[0] > b[0] or (a[0] == b[0] and a[1] < b[1])


@dataclass
class OverviewAgg:
    """What the Overview sums over a set of sessions: its cards, its net lines per line day and its sessions per model
    (the rows of the live `/overview` statements, see `gather_overview`)."""

    turns: int = 0
    tool_calls: int = 0
    tool_successes: int = 0
    duration_ms: int = 0
    files_changed: int = 0
    dead_sessions: int = 0
    spend_cost: Decimal = Decimal(0)
    cache_read_cost: Decimal = Decimal(0)
    unpriced: set[str] = field(default_factory=set)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    api_calls: int = 0
    sessions: int = (
        0  # the sessions of the window (every one, priced or not); not stored, read from the developers' rows
    )
    lines: dict[date, list] = field(default_factory=dict)  # line day -> [added | None, removed | None, nonzero]
    models: dict[str, list] = field(default_factory=dict)  # model -> [sessions, spend | None]
    # The cost of every session by subscription class: not stored in the Overview rows, read from the money rows
    # (`_OVERVIEW_CLASSES_SQL`) or the live statement.
    class_cost: dict[str, Decimal] = field(default_factory=dict)
    users: set[str] = field(default_factory=set)  # the developers of the stored days, read only when asked
    efficiency: EfficiencyAgg = field(default_factory=EfficiencyAgg)  # read and stored by the Efficiency rows only

    def merge(self, other: OverviewAgg) -> None:
        self.users |= other.users
        self.efficiency.merge(other.efficiency)
        for name in OVERVIEW_SUMS:
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.sessions += other.sessions
        self.unpriced |= other.unpriced
        for day, (added, removed, nonzero) in other.lines.items():
            mine = self.lines.setdefault(day, [None, None, False])
            mine[0], mine[1], mine[2] = _nadd(mine[0], added), _nadd(mine[1], removed), mine[2] or nonzero
        for model, (sessions, spend) in other.models.items():
            mine = self.models.setdefault(model, [0, None])
            mine[0], mine[1] = mine[0] + sessions, _nadd(mine[1], spend)
        for name, cost in other.class_cost.items():
            self.class_cost[name] = self.class_cost.get(name, Decimal(0)) + cost


# A group of the ranking: (project, repository, branch), an empty name for none.
GroupKey = tuple[str, str, str]
# A cell of the ranking's money: (kind, money key, project, repository). Kind `spend` is the whole selection's spend
# (empty key, project and repository); `route` is a funding route, `billed` is "true" (the personal-billed estimate).
MoneyKey = tuple[str, str, str, str]
SPEND_KEY: MoneyKey = ("spend", "", "", "")


@dataclass
class GroupAgg:
    """What the Repositories tab sums over the sessions of a (project, repository, branch) group."""

    n: int = 0
    turns: int = 0
    files_changed: int = 0
    lines_added: int | None = None  # None while no session of the group reports lines
    lines_removed: int | None = None
    tool_calls: int = 0
    tool_successes: int = 0
    spend: Decimal = Decimal(0)
    subs: set[str] = field(default_factory=set)
    origins: set[str] = field(default_factory=set)
    unpriced: set[str] = field(default_factory=set)  # raw names of the models without a price behind `spend`
    priced: bool = False  # some usage of the group had a used token kind with a rate (the evidence of `cost_unknown`)

    def merge(self, other: GroupAgg) -> None:
        self.n += other.n
        self.turns += other.turns
        self.files_changed += other.files_changed
        self.lines_added, self.lines_removed = (
            _nadd(self.lines_added, other.lines_added),
            _nadd(self.lines_removed, other.lines_removed),
        )
        self.tool_calls += other.tool_calls
        self.tool_successes += other.tool_successes
        self.spend += other.spend
        self.subs |= other.subs
        self.origins |= other.origins
        self.unpriced |= other.unpriced
        self.priced = self.priced or other.priced


@dataclass
class MoneyCell:
    cost: Decimal = Decimal(0)
    sessions: int = 0
    unpriced: set[str] = field(default_factory=set)

    def merge(self, other: MoneyCell) -> None:
        self.cost += other.cost
        self.sessions += other.sessions
        self.unpriced |= other.unpriced


@dataclass
class RankingAgg:
    groups: dict[GroupKey, GroupAgg] = field(default_factory=dict)
    money: dict[MoneyKey, MoneyCell] = field(default_factory=dict)

    def merge(self, other: RankingAgg) -> None:
        for key, group in other.groups.items():
            self.groups.setdefault(key, GroupAgg()).merge(group)
        for key, cell in other.money.items():
            self.money.setdefault(key, MoneyCell()).merge(cell)


@dataclass
class ToolsAgg:
    """The Tools & Models tab's counts of a set of sessions: `tools` (name to `[calls, successes]`), `invocations`
    (`(kind, name)` with kind skill, agent or command to the count) and `models` (normalised model to
    `[sessions, total tokens]`), every name, uncut: the tab's top lists are cut after a window's days are summed."""

    tools: dict[str, list[int]] = field(default_factory=dict)
    invocations: dict[tuple[str, str], int] = field(default_factory=dict)
    models: dict[str, list[int]] = field(default_factory=dict)

    def merge(self, other: ToolsAgg) -> None:
        for name, (calls, successes) in other.tools.items():
            cell = self.tools.setdefault(name, [0, 0])
            cell[0] += calls
            cell[1] += successes
        for key, count in other.invocations.items():
            self.invocations[key] = self.invocations.get(key, 0) + count
        for model, (sessions, tokens) in other.models.items():
            cell = self.models.setdefault(model, [0, 0])
            cell[0] += sessions
            cell[1] += tokens


@dataclass
class Cohort:
    users: dict[str, UserAgg] = field(default_factory=dict)
    money: dict[tuple[str, str, bool], MoneyAgg] = field(default_factory=dict)
    models: dict[str, ModelAgg] = field(default_factory=dict)
    overview: OverviewAgg = field(default_factory=OverviewAgg)
    ranking: RankingAgg = field(default_factory=RankingAgg)
    tools: ToolsAgg = field(default_factory=ToolsAgg)

    def merge(self, other: Cohort) -> None:
        for name, agg in other.users.items():
            self.users.setdefault(name, UserAgg()).merge(agg)
        for key, agg in other.money.items():
            self.money.setdefault(key, MoneyAgg()).merge(agg)
        for model, agg in other.models.items():
            self.models.setdefault(model, ModelAgg()).merge(agg)
        self.overview.merge(other.overview)
        self.ranking.merge(other.ranking)
        self.tools.merge(other.tools)


# ── the live statements over a cohort ────────────────────────────────────────────────────────────────────────────


def _top_sql(repo: PostgresCliAnalyticsReader, f: LocalAnalyticsFilter) -> str:
    order = (
        f"ORDER BY c.grain_calls DESC NULLS LAST, c.day, c.session_id {_C}, c.user_email {_C}, "
        f"{_GRAIN_MODEL} {_C}, c.query_source {_C}"
    )

    def first(expression: str) -> str:
        return f"(array_agg({expression} {order}))[1]"

    return f"""
        WITH {repo._sel_cte(f, with_developer=True)}
        SELECT s.developer AS developer_name,
               {first("c.grain_calls")} AS top_calls, {first("c.day")} AS top_day,
               {first("c.session_id")} AS top_session, {first("c.user_email")} AS top_email,
               {first(_GRAIN_MODEL)} AS top_model, {first("c.query_source")} AS top_qs
        FROM {_cost_rows("", joined=True)}
             RIGHT JOIN sel s ON s.session_id = c.session_id
        GROUP BY 1"""


def _last_sql(repo: PostgresCliAnalyticsReader, f: LocalAnalyticsFilter) -> str:
    return f"""
        WITH {repo._sel_cte(f, with_developer=True)}
        SELECT s.developer AS developer_name, max(d.last_event_at) AS last_active
        FROM sel s JOIN session_dims d ON d.session_id = s.session_id
        GROUP BY 1"""


def _money_sql(repo: PostgresCliAnalyticsReader, f: LocalAnalyticsFilter) -> Callable[[str], str]:
    return lambda priced: (
        f"WITH {repo._sel_cte(f, with_developer=True)}, {priced} "
        f"SELECT gc.*, gc.cost_usd::text AS cost_text FROM {grouped_cost_sql(_MONEY_GROUPINGS)}"
    )


async def _gather_users(repo: PostgresCliAnalyticsReader, f: LocalAnalyticsFilter, cohort: Cohort) -> None:
    for row in await repo.get_users(f):
        cohort.users[row["developer_name"]] = UserAgg(
            session_count=int(row["session_count"]),
            input_tokens=int(row["input_tokens"]),
            output_tokens=int(row["output_tokens"]),
            cache_read_tokens=int(row["cache_read_tokens"]),
            cache_creation_tokens=int(row["cache_creation_tokens"]),
            subscription_types=set(row["subscription_types"] or ()),
            project_origins=set(row["project_origins"] or ()),
        )


async def _gather_user_facts(repo: PostgresCliAnalyticsReader, f: LocalAnalyticsFilter, cohort: Cohort) -> None:
    """The top model, the session facts, the net lines and the last activity of the developers `_gather_users` found."""
    for row in await repo._q(_top_sql(repo, f), window_params(f)):
        if row["top_calls"] is not None:
            cohort.users[row["developer_name"]].top = (
                int(row["top_calls"]),
                row["top_day"],
                row["top_session"],
                row["top_email"],
                row["top_model"],
                row["top_qs"],
            )
    for row in await repo.get_developer_session_facts(f):
        agg = cohort.users[row["developer"]]
        agg.turns, agg.tool_calls, agg.tool_successes = (
            int(row["turns"]),
            int(row["tool_calls"]),
            int(row["tool_successes"]),
        )
    for row in await repo.get_lines_by_user(f):
        if row["net_lines"] is not None:
            cohort.users[row["developer_name"]].net_lines = int(row["net_lines"])
    for row in await repo._q(_last_sql(repo, f), window_params(f)):
        cohort.users[row["developer_name"]].last_active = row["last_active"]


def _add_money_row(cohort: Cohort, row: dict) -> None:
    cost, sessions, names = Decimal(row["cost_text"] or "0"), int(row["session_count"]), set(row["unpriced_models"])
    if row["grouping"] == "ur_model":
        cohort.models[row["model"]] = ModelAgg(cost, sessions, names)
        return
    key = (row["user"], row["funding_route"], bool(row["personal_billed"]))
    agg = cohort.money.setdefault(key, MoneyAgg())
    if row["grouping"] == "ur_all":
        agg.cost_all, agg.sessions_all, agg.unpriced_all = cost, sessions, names
    else:
        agg.cost_cd, agg.sessions_cd, agg.unpriced_cd = cost, sessions, names


async def gather_cohort(
    repo: PostgresCliAnalyticsReader, f: LocalAnalyticsFilter, rate_of: Callable[[str], ModelRates]
) -> Cohort:
    """Per developer and per (developer, route, billed) values of the sessions of `repo`'s temp `sel`, from the live
    reader's own statements. `repo` is bounded and has materialized `sel` for `f`."""
    cohort = Cohort()
    await _gather_users(repo, f, cohort)
    if not cohort.users:  # no session: the other statements have nothing to add
        return cohort
    await _gather_user_facts(repo, f, cohort)
    rates = [rate_of(model) for model in await repo.get_cost_models(f)]
    for row in await repo._money_rows(f, rates, _money_sql(repo, f)):
        _add_money_row(cohort, row)
    return cohort


def _dec(value: Any) -> Decimal:
    return Decimal(0) if value is None else Decimal(str(value))


def _overview_money(agg: OverviewAgg, rows: list[dict]) -> None:
    for r in rows:
        if r["part"] == "money" and r["key"] == "spend":
            agg.spend_cost, agg.cache_read_cost = _dec(r["cost_usd"]), _dec(r["cache_read_cost_usd"])
            agg.unpriced = set(r["unpriced_models"] or ())
        elif r["part"] == "money" and r["key"] == "class":
            agg.class_cost[r["subscription_type"]] = _dec(r["cost_usd"])
        elif r["part"] == "dead":
            agg.dead_sessions = int(r["n"] or 0)
            agg.efficiency.dead_cost = _dec(r["cost_usd"])


def _overview_facts(agg: OverviewAgg, rows: list[dict]) -> None:
    for r in rows:
        agg.turns, agg.tool_calls, agg.tool_successes = (
            int(r["turns"] or 0),
            int(r["tool_calls"] or 0),
            int(r["tool_calls_success"] or 0),
        )
        agg.duration_ms, agg.files_changed = int(r["duration_ms"] or 0), int(r["files_changed"] or 0)


def _overview_kpis(agg: OverviewAgg, rows: list[dict]) -> None:
    for r in rows:
        agg.input_tokens, agg.output_tokens = int(r["total_input_tokens"] or 0), int(r["total_output_tokens"] or 0)
        agg.cache_read_tokens = int(r["total_cache_read_tokens"] or 0)
        agg.cache_creation_tokens = int(r["total_cache_creation_tokens"] or 0)
        agg.api_calls = int(r["total_api_calls"] or 0)
        agg.efficiency.spend_sessions = int(r["spend_sessions"] or 0)


def _efficiency_facts(agg: OverviewAgg, rows: list[dict]) -> None:
    for r in rows:
        if r["part"] == "worst":
            agg.efficiency.worst = (float(r["ratio"]), str(r["session_id"]))
        elif r["part"] == "depth":
            agg.efficiency.depth[DEPTH_BUCKETS.index(str(r["key"]))] = int(r["n"] or 0)
        elif r["part"] == "code":
            agg.efficiency.files_written = int(r["files_written"] or 0)
            agg.efficiency.files_edited = int(r["files_edited"] or 0)


async def gather_overview(
    repo: PostgresCliAnalyticsReader,
    f: LocalAnalyticsFilter,
    rate_of: Callable[[str], ModelRates],
    efficiency: bool = False,
) -> OverviewAgg:
    """The Overview's values of the sessions of `repo`'s temp `sel`, from the live reader's own /overview statements
    (`get_overview_money`, `_facts`, `_models`, `get_dashboard_kpis`, `get_overview_lines`). `repo` is bounded and has
    materialized `sel` for `f`. `efficiency` also reads the Efficiency facts (`get_efficiency_facts`); the dead-session
    cost and the sessions with a cost row come with the rows above."""
    await repo.materialize_files(f)
    agg = OverviewAgg()
    rates = [rate_of(model) for model in await repo.get_cost_models(f)]
    _overview_money(agg, await repo.get_overview_money(f, rates, exact=True))
    _overview_facts(agg, await repo.get_overview_facts(f))
    for r in await repo.get_overview_models(f):
        agg.models[r["key"]] = [int(r["n"]), None if r["cost_usd"] is None else _dec(r["cost_usd"])]
    _overview_kpis(agg, await repo.get_dashboard_kpis(f))
    for r in await repo.get_overview_lines(f):
        if r["part"] == "day":
            agg.lines[date.fromisoformat(r["key"])] = [r["lines_added"], r["lines_removed"], bool(r["nonzero"])]
    if efficiency:
        _efficiency_facts(agg, await repo.get_efficiency_facts(f))
    return agg


def _name(value: Any) -> str:
    return "" if value is None else str(value)


def _ranking_group_row(agg: RankingAgg, r: dict) -> None:
    key = (_name(r["project_name"]), _name(r["repository"]), _name(r["branch"]))
    group = agg.groups.setdefault(key, GroupAgg())
    group.n, group.spend = int(r["n"]), _dec(r["cost_usd"])
    group.unpriced, group.priced = set(r["unpriced_models"] or ()), bool(r["cost_priced"])


def _ranking_bucket_row(agg: RankingAgg, r: dict) -> None:
    where = (_name(r["project_name"]), _name(r["repository"]))
    if r["key"] == "b_route":
        agg.money[("route", _name(r["funding_route"]), *where)] = _money_cell(r)
    elif r["personal_billed"]:
        agg.money[("billed", "true", *where)] = _money_cell(r)


def _ranking_row(agg: RankingAgg, r: dict) -> None:
    if r["part"] == "group":
        _ranking_group_row(agg, r)
    elif r["part"] == "total" and r["key"] == "spend":
        agg.money[SPEND_KEY] = _money_cell(r)
    elif r["part"] == "bucket":
        _ranking_bucket_row(agg, r)


def _ranking_facts_row(agg: RankingAgg, r: dict) -> None:
    group = agg.groups[(_name(r["project_name"]), _name(r["repository"]), _name(r["branch"]))]
    group.turns, group.files_changed = int(r["turns"] or 0), int(r["files_changed"] or 0)
    group.lines_added, group.lines_removed = r["lines_added"], r["lines_removed"]
    group.tool_calls, group.tool_successes = int(r["tool_calls"] or 0), int(r["tool_calls_success"] or 0)
    group.subs, group.origins = set(r["subscription_types"] or ()), set(r["project_origins"] or ())


async def gather_ranking(
    repo: PostgresCliAnalyticsReader, f: LocalAnalyticsFilter, rate_of: Callable[[str], ModelRates]
) -> RankingAgg:
    """The Repositories groups and their money of the sessions of `repo`'s temp `sel`, from the live reader's own
    statements with branches (`get_repository_ranking`, `get_repository_facts`). `repo` is bounded and has materialized
    `sel` for `f`."""
    agg = RankingAgg()
    rates = [rate_of(model) for model in await repo.get_cost_models(f)]
    for r in await repo.get_repository_ranking(f, True, None, rates, exact=True):
        _ranking_row(agg, r)
    for r in await repo.get_repository_facts(f, True, None):
        _ranking_facts_row(agg, r)
    return agg


async def gather_tools(repo: PostgresCliAnalyticsReader, f: LocalAnalyticsFilter) -> ToolsAgg:
    """The Tools & Models rows of the sessions of `repo`'s temp `sel`, from the live reader's own statements
    (`get_tool_usage` without its limit, `get_invocations`, `get_model_breakdown`). `repo` is bounded and has
    materialized `sel` for `f`."""
    agg = ToolsAgg()
    for r in await repo.get_tool_usage(f, exact=True):
        agg.tools[str(r["tool_name"])] = [int(r["call_count"] or 0), int(r["success_count"] or 0)]
    for r in await repo.get_invocations(f):
        agg.invocations[(str(r["kind"]), str(r["name"]))] = int(r["count"] or 0)
    for r in await repo.get_model_breakdown(f):
        agg.models[str(r["model_name"])] = [int(r["session_count"] or 0), int(r["total_tokens"] or 0)]
    return agg


def _money_cell(row: dict) -> MoneyCell:
    return MoneyCell(_dec(row["cost_usd"]), int(row["session_count"] or 0), set(row["unpriced_models"] or ()))


# ── building ─────────────────────────────────────────────────────────────────────────────────────────────────────

_SIGNATURE_SQL = """
SELECT (started_at AT TIME ZONE 'UTC')::date AS day,
       count(*) AS sessions,
       count(*) FILTER (WHERE has_activity IS TRUE) AS active,
       coalesce(sum(extract(epoch FROM last_event_at)), 0)::numeric AS last_sum,
       coalesce(sum(cost_key), 0)::numeric AS cost_sum,
       coalesce(sum(hashtext(coalesce(user_email, ''))::bigint), 0)::numeric AS ident_sum,
       coalesce(sum(hashtext(coalesce(subscription_type, '') || chr(31) || coalesce(project_origin, ''))::bigint),
                0)::numeric AS class_sum
FROM session_dims
WHERE started_at >= $1::timestamptz AND started_at < $2::timestamptz
GROUP BY 1"""
_SIGNATURE_FIELDS = ("sessions", "active", "last_sum", "cost_sum", "ident_sum", "class_sum")
# The class pairs of the sessions that started on a day: the values the two class filters compare.
_DAY_CLASSES_SQL = f"""
SELECT DISTINCT {sql_subscription_type("subscription_type")} AS sub_class,
       {sql_project_origin("project_origin")} AS origin_class
FROM session_dims
WHERE started_at >= $1::timestamptz AND started_at < $2::timestamptz
ORDER BY 1, 2"""
_NO_SESSIONS = {name: Decimal(0) for name in _SIGNATURE_FIELDS}


def _day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


async def day_signatures(conn: Any, first: date, last: date) -> dict[date, dict[str, Decimal]]:
    """Per start day in `first..last`, what changes when a session of that day does: the numbers of sessions and of
    those with activity, and sums of the latest event, the cost key and hashes of the developer's e-mail and of the
    stored subscription type and project origin."""
    rows = await conn.fetch(_SIGNATURE_SQL, _day_start(first), _day_start(last + timedelta(days=1)))
    return {r["day"]: {name: Decimal(r[name]) for name in _SIGNATURE_FIELDS} for r in rows}


_INSERT_USER = """INSERT INTO user_day_rollup (start_day, usr, sub_class, origin_class, session_count, input_tokens,
    output_tokens, cache_read_tokens, cache_creation_tokens, turns, tool_calls, tool_successes, net_lines, last_active,
    top_calls, top_day, top_session, top_email, top_model, top_qs)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20)"""
_INSERT_MONEY = """INSERT INTO user_day_money_rollup (start_day, usr, sub_class, origin_class, funding_route,
    personal_billed, cost_all, sessions_all, unpriced_all, cost_cd, sessions_cd, unpriced_cd)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)"""
_INSERT_MODEL = """INSERT INTO user_day_model_rollup (start_day, sub_class, origin_class, model, cost, sessions,
    unpriced) VALUES ($1, $2, $3, $4, $5, $6, $7)"""
_OVERVIEW_COLUMNS = ("start_day", "sub_class", "origin_class", *OVERVIEW_SUMS, "unpriced")
_INSERT_OVERVIEW = (
    f"INSERT INTO user_day_overview_rollup ({', '.join(_OVERVIEW_COLUMNS)}) "  # noqa: S608 - fixed names
    f"VALUES ({', '.join(f'${n}' for n in range(1, len(_OVERVIEW_COLUMNS) + 1))})"
)
_INSERT_OVERVIEW_LINES = """INSERT INTO user_day_overview_lines (start_day, sub_class, origin_class, line_day,
    lines_added, lines_removed, nonzero) VALUES ($1, $2, $3, $4, $5, $6, $7)"""
_INSERT_OVERVIEW_MODEL = """INSERT INTO user_day_overview_models (start_day, sub_class, origin_class, model, sessions,
    spend) VALUES ($1, $2, $3, $4, $5, $6)"""
_INSERT_EFFICIENCY = """INSERT INTO user_day_efficiency_rollup (start_day, sub_class, origin_class, dead_cost, depth_1,
    depth_2_5, depth_6_10, depth_11_25, depth_26_50, depth_50_plus, files_written, files_edited, spend_sessions,
    worst_session_id, worst_ratio) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)"""
_INSERT_RANKING = """INSERT INTO user_day_ranking_rollup (start_day, sub_class, origin_class, project, repository,
    branch, session_count, turns, files_changed, lines_added, lines_removed, tool_calls, tool_successes, spend,
    unpriced, priced) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)"""
_INSERT_RANKING_MONEY = """INSERT INTO user_day_ranking_money_rollup (start_day, sub_class, origin_class, kind,
    money_key, project, repository, cost, sessions, unpriced) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)"""
_INSERT_TOOLS = """INSERT INTO user_day_tools_rollup (start_day, sub_class, origin_class, kind, name, calls,
    successes) VALUES ($1, $2, $3, $4, $5, $6, $7)"""
_INSERT_TOOLS_MODEL = """INSERT INTO user_day_tools_models (start_day, sub_class, origin_class, model, sessions,
    total_tokens) VALUES ($1, $2, $3, $4, $5, $6)"""
_DAY_TABLES = (
    "user_day_rollup",
    "user_day_money_rollup",
    "user_day_model_rollup",
    "user_day_overview_rollup",
    "user_day_overview_lines",
    "user_day_overview_models",
    "user_day_ranking_rollup",
    "user_day_ranking_money_rollup",
    "user_day_efficiency_rollup",
    "user_day_tools_rollup",
    "user_day_tools_models",
)
# `built_touched` is the day's `touched` as the build read it before its data: `touched` itself is the refresher's and
# is never written here, so a recompute that lands during the build leaves the two apart and the day is built again.
_UPSERT_STATE = """INSERT INTO user_rollup_day_state (start_day, sessions, active, last_sum, cost_sum, ident_sum,
    class_sum, rates_version, shape, built_at, checked_at, build_ms, touched, built_touched)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, now(), now(), $10, $11, $11)
    ON CONFLICT (start_day) DO UPDATE SET sessions = $2, active = $3, last_sum = $4, cost_sum = $5, ident_sum = $6,
    class_sum = $7, rates_version = $8, shape = $9, built_at = now(), checked_at = now(), build_ms = $10,
    built_touched = $11"""
_SEEN_TOUCHED = "SELECT touched FROM user_rollup_day_state WHERE start_day = $1"


def touched_since_built(state: Mapping[str, Any]) -> bool:
    """Whether the refresher recomputed a session of the day after the day was built (`RollupRefresher` counts every
    recompute in `touched`). The signature cannot see such a change when it leaves `session_dims` as it was: tool
    spans, line counts or unpriced tokens that arrive after the session's last hook event."""
    return int(state.get("touched") or 0) != int(state.get("built_touched") or 0)


# A day's cohorts by (subscription class, project-origin class).
ClassCohorts = Mapping[tuple[str, str], Cohort]


async def write_day(
    conn: Any,
    day: date,
    cohorts: ClassCohorts,
    signature: Mapping[str, Decimal],
    version: str,
    build_ms: int,
    seen_touched: int = 0,
) -> None:
    """Replace a day's rows and its state in one transaction, so a reader sees the old day or the new one."""
    users, money, models, overview, overview_lines, overview_models = [], [], [], [], [], []
    ranking, ranking_money, efficiency, tools, tools_models = [], [], [], [], []
    for (sub, origin), cohort in sorted(cohorts.items()):
        tools += [
            (day, sub, origin, "tool", name, calls, ok) for name, (calls, ok) in sorted(cohort.tools.tools.items())
        ]
        tools += [(day, sub, origin, kind, name, n, 0) for (kind, name), n in sorted(cohort.tools.invocations.items())]
        tools_models += [
            (day, sub, origin, model, n, tokens) for model, (n, tokens) in sorted(cohort.tools.models.items())
        ]
        ranking += [
            (day, sub, origin, *key, g.n, g.turns, g.files_changed, g.lines_added, g.lines_removed)
            + (g.tool_calls, g.tool_successes, g.spend, sorted(g.unpriced), g.priced)
            for key, g in sorted(cohort.ranking.groups.items())
        ]
        ranking_money += [
            (day, sub, origin, *key, c.cost, c.sessions, sorted(c.unpriced))
            for key, c in sorted(cohort.ranking.money.items())
        ]
        o = cohort.overview
        overview.append((day, sub, origin, *(getattr(o, name) for name in OVERVIEW_SUMS), sorted(o.unpriced)))
        e = o.efficiency
        worst = e.worst or (None, None)
        efficiency.append(
            (day, sub, origin, e.dead_cost, *e.depth, e.files_written, e.files_edited, e.spend_sessions)
            + (worst[1], worst[0])
        )
        overview_lines += [(day, sub, origin, d, *values) for d, values in sorted(o.lines.items())]
        overview_models += [(day, sub, origin, m, *values) for m, values in sorted(o.models.items())]
        models += [
            (day, sub, origin, model, m.cost, m.sessions, sorted(m.unpriced))
            for model, m in sorted(cohort.models.items())
        ]
        for name, a in sorted(cohort.users.items()):
            top = a.top or (None,) * 6
            users.append(
                (day, name, sub, origin, a.session_count, a.input_tokens, a.output_tokens, a.cache_read_tokens)
                + (a.cache_creation_tokens, a.turns, a.tool_calls, a.tool_successes, a.net_lines)
                + (_aware(a.last_active),)
                + top
            )
        money += [
            (day, usr, sub, origin, route, billed, m.cost_all, m.sessions_all, sorted(m.unpriced_all))
            + (m.cost_cd, m.sessions_cd, sorted(m.unpriced_cd))
            for (usr, route, billed), m in sorted(cohort.money.items())
        ]
    async with conn.transaction():
        for table in _DAY_TABLES:
            await conn.execute(f"DELETE FROM {table} WHERE start_day = $1", day)  # noqa: S608 - fixed names
        await conn.executemany(_INSERT_USER, users)
        await conn.executemany(_INSERT_MONEY, money)
        await conn.executemany(_INSERT_MODEL, models)
        await conn.executemany(_INSERT_OVERVIEW, overview)
        await conn.executemany(_INSERT_OVERVIEW_LINES, overview_lines)
        await conn.executemany(_INSERT_OVERVIEW_MODEL, overview_models)
        await conn.executemany(_INSERT_RANKING, ranking)
        await conn.executemany(_INSERT_RANKING_MONEY, ranking_money)
        await conn.executemany(_INSERT_EFFICIENCY, efficiency)
        await conn.executemany(_INSERT_TOOLS, tools)
        await conn.executemany(_INSERT_TOOLS_MODEL, tools_models)
        await conn.execute(
            _UPSERT_STATE,
            day,
            int(signature["sessions"]),
            int(signature["active"]),
            signature["last_sum"],
            signature["cost_sum"],
            signature["ident_sum"],
            signature["class_sum"],
            version,
            ROLLUP_SHAPE,
            build_ms,
            seen_touched,
        )


def _aware(value: datetime | None) -> datetime | None:
    """The reader returns naive UTC datetimes; the column is timestamptz."""
    return value if value is None or value.tzinfo else value.replace(tzinfo=timezone.utc)


def _day_filter(day: date, sub_class: str, origin_class: str) -> LocalAnalyticsFilter:
    start = _day_start(day)
    return LocalAnalyticsFilter(
        scope=ROLLUP_SCOPE,
        start_dt=start,
        end_dt=start + timedelta(days=1, milliseconds=-1),
        subscription_types=[sub_class],
        project_origins=[origin_class],
    )


async def build_day(
    reader: PostgresCliAnalyticsReader,
    conn: Any,
    day: date,
    rate_of: Callable[[str], ModelRates],
    version: str,
    signature: Mapping[str, Decimal],
) -> int:
    """Rebuild one start day on `conn`; returns the milliseconds it took. `signature` was read before the data, so a
    change that lands during the build leaves the day looking stale and it is built again."""
    started = time.perf_counter()
    seen_touched = int(await conn.fetchval(_SEEN_TOUCHED, day) or 0)  # before the data, as the signature was
    scoped = PostgresCliAnalyticsReader(_ConnectionPair(None, [conn]), s_cap=reader._s_cap)
    scoped.set_rates(reader._rates_version)
    start = _day_start(day)
    cohorts: dict[tuple[str, str], Cohort] = {}
    for pair in await conn.fetch(_DAY_CLASSES_SQL, start, start + timedelta(days=1)):
        f = _day_filter(day, pair["sub_class"], pair["origin_class"])
        try:
            await scoped.materialize_sel(f, cohort="started")
            cohort = await gather_cohort(scoped, f, rate_of)
            if cohort.users:
                cohort.overview = await gather_overview(scoped, f, rate_of, efficiency=True)
                cohort.ranking = await gather_ranking(scoped, f, rate_of)
                cohort.tools = await gather_tools(scoped, f)
        finally:
            await scoped._drop_sel(conn)
        if cohort.users:  # a pair the scope leaves out (personal sessions) has no row
            cohorts[(pair["sub_class"], pair["origin_class"])] = cohort
    elapsed = int((time.perf_counter() - started) * 1000)
    await write_day(conn, day, cohorts, signature, version, elapsed, seen_touched)
    return elapsed


async def confirm_recent(conn: Any, *, version: str, today: date | None = None) -> list[date]:
    """Stamp `checked_at` on the recent start days whose sessions still match the state they were built from. It is the
    cheap half of `refresh`, run on its own so that a long rebuild of an old day never leaves today's row unconfirmed
    (the readers stop trusting a row the job has not confirmed lately). Returns the days it confirmed."""
    today = today or datetime.now(timezone.utc).date()
    first = today - timedelta(days=RECENT_DAYS - 1)
    signatures = await day_signatures(conn, first, today)
    states = {
        r["start_day"]: r for r in await conn.fetch("SELECT * FROM user_rollup_day_state WHERE start_day >= $1", first)
    }
    current = [
        day
        for day, state in states.items()
        if state["rates_version"] == version
        and state["shape"] >= ROLLUP_SHAPE
        and not touched_since_built(state)
        and all(Decimal(state[name]) == signatures.get(day, _NO_SESSIONS)[name] for name in _SIGNATURE_FIELDS)
    ]
    if current:
        await conn.execute(
            "UPDATE user_rollup_day_state SET checked_at = now() WHERE start_day = ANY($1::date[])", current
        )
    return current


def _classify_days(
    days: int,
    today: date,
    scan_first: date,
    signatures: dict,
    states: dict,
    version: str,
    now: datetime,
    min_rebuild_s: float,
) -> tuple[list[date], list[date], list[date]]:
    """The days with no usable state, the days whose sessions changed and are due, and the days confirmed current."""
    missing, changed, current = [], [], []
    for day in (today - timedelta(days=n) for n in range(days)):
        want = signatures.get(day, _NO_SESSIONS)
        state = states.get(day)
        if state is None or state["rates_version"] != version or state["shape"] < ROLLUP_SHAPE:
            missing.append(day)
        elif touched_since_built(state) or (
            day >= scan_first and any(Decimal(state[name]) != want[name] for name in _SIGNATURE_FIELDS)
        ):
            hold = min_rebuild_s if day == today else 10 * min_rebuild_s
            if (now - state["built_at"]).total_seconds() >= hold:
                changed.append(day)
        elif day >= scan_first:
            current.append(day)
    return missing, changed, current


async def refresh(
    reader: PostgresCliAnalyticsReader,
    conn: Any,
    *,
    days: int,
    run_seconds: float,
    rate_of: Callable[[str], ModelRates],
    version: str,
    today: date | None = None,
    min_rebuild_s: float = 0.0,
    full_scan: bool = True,
) -> list[date]:
    """One run of the job: rebuild, newest first and within `run_seconds`, the start days of the last `days` whose
    sessions changed, that were built under other rates, or that were never built; drop the days that fell out. Returns
    the days it rebuilt. Unless `full_scan`, only the last `RECENT_DAYS` days are checked for changed sessions (the scan
    of every day costs seconds; days never built, built under other rates or touched by the refresher since their build
    are always found, from the state table).

    A day whose sessions changed is rebuilt at most every `min_rebuild_s` seconds (ten times that for a past day,
    which only late events change): today changes on nearly every run, and a rebuild is seconds to minutes of work. A
    day never built, or built under other rates, is not held back. A day of a shape below `ROLLUP_SHAPE` is rebuilt
    like a day never built; one of a higher shape (a newer pod's) is kept."""
    now = datetime.now(timezone.utc)
    today = today or now.date()
    first = today - timedelta(days=days - 1)
    scan_first = first if full_scan else max(first, today - timedelta(days=RECENT_DAYS - 1))
    signatures = await day_signatures(conn, scan_first, today)
    states = {
        r["start_day"]: r for r in await conn.fetch("SELECT * FROM user_rollup_day_state WHERE start_day >= $1", first)
    }
    missing, changed, current = _classify_days(days, today, scan_first, signatures, states, version, now, min_rebuild_s)
    if current:
        # The readers trust a day for as long as the job keeps confirming it: a day nothing touches is never rebuilt, so
        # the time it was built says nothing about whether it is current.
        await conn.execute(
            "UPDATE user_rollup_day_state SET checked_at = now() WHERE start_day = ANY($1::date[])", current
        )
    # Today first when it is due: a window is served only while today's row is fresh, and a long backfill must not let
    # it go stale. Its hold keeps it to one rebuild per `min_rebuild_s`, so it cannot starve the history. Then the days
    # with no usable state (a window is served only when every one of its days exists), then the other changed days.
    stale = [d for d in changed if d == today] + missing + [d for d in changed if d != today]
    deadline = time.monotonic() + run_seconds
    rebuilt: list[date] = []
    for day in stale:
        if rebuilt and time.monotonic() > deadline:
            break
        # A light run read the signatures of the recent days only: an older day it builds (a backfill) gets its own
        # signature read now, not an empty one, or the next full scan would find it changed and build it a second time.
        signature = signatures if day >= scan_first else await day_signatures(conn, day, day)
        await build_day(reader, conn, day, rate_of, version, signature.get(day, _NO_SESSIONS))
        rebuilt.append(day)
    for table in (*_DAY_TABLES, "user_rollup_day_state"):
        await conn.execute(f"DELETE FROM {table} WHERE start_day < $1", first)  # noqa: S608 - fixed names
    if rebuilt:
        logger.info(f"cli_analytics: user rollup rebuilt {len(rebuilt)} day(s), {len(stale) - len(rebuilt)} left")
    return rebuilt


# ── serving ──────────────────────────────────────────────────────────────────────────────────────────────────────


def eligible(f: LocalAnalyticsFilter, now: datetime) -> bool:
    """Whether the rollup can answer `f`: the scope and the filters do not narrow the sessions beyond what it holds
    (every shared session, whoever asks, by subscription class and project-origin class), and the window is whole
    start days up to now."""
    scope = f.scope
    if not (isinstance(scope, ViewerScope) and scope.unrestricted and scope.mode == "exclude"):
        return False
    if f.deny_all or f.project_unattributed or f.session_ids is not None:
        return False
    if any((f.users, f.projects, f.repositories, f.branch, f.developers)):
        return False
    start = f.start_dt if f.start_dt.tzinfo else f.start_dt.replace(tzinfo=timezone.utc)
    end = f.end_dt if f.end_dt.tzinfo else f.end_dt.replace(tzinfo=timezone.utc)
    return start == _day_start(start.date()) and abs((now - end).total_seconds()) <= LIVE_END_SECONDS


def ranking_eligible(f: LocalAnalyticsFilter, now: datetime) -> bool:
    """Whether the ranking rows can answer `f`. A copy of `eligible` that evolves on its own: the Repositories and
    Projects tabs filter by more than the Users page does, and a search is never asked of the rollup (it narrows the
    sessions by name; the caller does not call it with one). Every shared session, whoever asks, by subscription
    class and project-origin class; a window of whole start days up to now."""
    scope = f.scope
    if not (isinstance(scope, ViewerScope) and scope.unrestricted and scope.mode == "exclude"):
        return False
    if f.deny_all or f.project_unattributed or f.session_ids is not None:
        return False
    if any((f.users, f.projects, f.repositories, f.branch, f.developers)):
        return False
    start = f.start_dt if f.start_dt.tzinfo else f.start_dt.replace(tzinfo=timezone.utc)
    end = f.end_dt if f.end_dt.tzinfo else f.end_dt.replace(tzinfo=timezone.utc)
    return start == _day_start(start.date()) and abs((now - end).total_seconds()) <= LIVE_END_SECONDS


def _window_days(f: LocalAnalyticsFilter) -> tuple[date, date]:
    start = f.start_dt if f.start_dt.tzinfo else f.start_dt.replace(tzinfo=timezone.utc)
    end = f.end_dt if f.end_dt.tzinfo else f.end_dt.replace(tzinfo=timezone.utc)
    return start.astimezone(timezone.utc).date(), end.astimezone(timezone.utc).date()


# $3 and $4: the subscription classes and the project-origin classes asked for; NULL keeps every class.
_CLASSES = (
    "AND ($3::text[] IS NULL OR sub_class = ANY($3::text[])) "
    "AND ($4::text[] IS NULL OR origin_class = ANY($4::text[]))"
)
_SESSIONS_SQL = f"""
SELECT coalesce(sum(session_count), 0)::bigint FROM user_day_rollup WHERE start_day BETWEEN $1 AND $2 {_CLASSES}"""
_USERS_SQL = f"""
SELECT usr, sum(session_count)::bigint AS session_count, sum(input_tokens)::bigint AS input_tokens,
       sum(output_tokens)::bigint AS output_tokens, sum(cache_read_tokens)::bigint AS cache_read_tokens,
       sum(cache_creation_tokens)::bigint AS cache_creation_tokens, sum(turns)::bigint AS turns,
       sum(tool_calls)::bigint AS tool_calls, sum(tool_successes)::bigint AS tool_successes,
       sum(net_lines)::bigint AS net_lines, max(last_active) AS last_active,
       array_agg(DISTINCT sub_class) AS subs, array_agg(DISTINCT origin_class) AS origins
FROM user_day_rollup WHERE start_day BETWEEN $1 AND $2 {_CLASSES} GROUP BY usr"""
_TOP_SQL = f"""
SELECT DISTINCT ON (usr) usr, top_calls, top_day, top_session, top_email, top_model, top_qs
FROM user_day_rollup WHERE start_day BETWEEN $1 AND $2 {_CLASSES} AND top_calls IS NOT NULL
ORDER BY usr, top_calls DESC, top_day, top_session {_C}, top_email {_C}, top_model {_C}, top_qs {_C}"""
_MONEY_WINDOW_SQL = f"""
SELECT usr, funding_route, personal_billed, sum(cost_all) AS cost_all, sum(sessions_all)::bigint AS sessions_all,
       sum(cost_cd) AS cost_cd, sum(sessions_cd)::bigint AS sessions_cd
FROM user_day_money_rollup WHERE start_day BETWEEN $1 AND $2 {_CLASSES} GROUP BY usr, funding_route, personal_billed"""
# The unpriced raw model names are rare: only the rows that have any are unnested.
_UNPRICED_SQL = f"""
SELECT usr, funding_route, personal_billed, which, array_agg(DISTINCT n) AS names FROM (
    SELECT usr, funding_route, personal_billed, 'all' AS which, unnest(unpriced_all) AS n
    FROM user_day_money_rollup WHERE start_day BETWEEN $1 AND $2 {_CLASSES} AND cardinality(unpriced_all) > 0
    UNION ALL
    SELECT usr, funding_route, personal_billed, 'cd', unnest(unpriced_cd)
    FROM user_day_money_rollup WHERE start_day BETWEEN $1 AND $2 {_CLASSES} AND cardinality(unpriced_cd) > 0) x
GROUP BY usr, funding_route, personal_billed, which"""
# The window's model rows (the spend money of /cost's "Cost by Model") and their few unpriced raw names.
_MODELS_SQL = f"""
SELECT model, sum(cost) AS cost, sum(sessions)::bigint AS sessions
FROM user_day_model_rollup WHERE start_day BETWEEN $1 AND $2 {_CLASSES} GROUP BY model"""
_MODEL_UNPRICED_SQL = f"""
SELECT model, array_agg(DISTINCT n) AS names FROM (
    SELECT model, unnest(unpriced) AS n
    FROM user_day_model_rollup WHERE start_day BETWEEN $1 AND $2 {_CLASSES} AND cardinality(unpriced) > 0) x
GROUP BY model"""
# The window's Overview rows: the card totals (one row; NULL sums while no day has a row), the spend's unpriced raw
# names, the lines per line day and the sessions per model.
_OVERVIEW_SQL = f"""
SELECT {", ".join(f"sum({n}) AS {n}" if n in _OVERVIEW_NUMERIC else f"sum({n})::bigint AS {n}" for n in OVERVIEW_SUMS)}
FROM user_day_overview_rollup WHERE start_day BETWEEN $1 AND $2 {_CLASSES}"""  # noqa: S608 - fixed names
# The cost of every session of the window by subscription class: the money rows hold it per (developer, route,
# billed, class), the Overview rows only the total.
_OVERVIEW_CLASSES_SQL = f"""
SELECT sub_class, sum(cost_all) AS cost
FROM user_day_money_rollup WHERE start_day BETWEEN $1 AND $2 {_CLASSES} GROUP BY sub_class"""
# The developers of the window's stored days: the Users page's `total_count` (`_USERS_SQL` groups by the same column).
_OVERVIEW_USERS_SQL = f"""
SELECT DISTINCT usr FROM user_day_rollup WHERE start_day BETWEEN $1 AND $2 {_CLASSES}"""
# The window's Efficiency rows: the sums, and the day's worst sessions' best (the largest ratio, ties to the lowest id).
_EFFICIENCY_SQL = f"""
SELECT coalesce(sum(dead_cost), 0) AS dead_cost,
       coalesce(sum(depth_1), 0)::bigint AS depth_1, coalesce(sum(depth_2_5), 0)::bigint AS depth_2_5,
       coalesce(sum(depth_6_10), 0)::bigint AS depth_6_10, coalesce(sum(depth_11_25), 0)::bigint AS depth_11_25,
       coalesce(sum(depth_26_50), 0)::bigint AS depth_26_50, coalesce(sum(depth_50_plus), 0)::bigint AS depth_50_plus,
       coalesce(sum(files_written), 0)::bigint AS files_written,
       coalesce(sum(files_edited), 0)::bigint AS files_edited,
       coalesce(sum(spend_sessions), 0)::bigint AS spend_sessions
FROM user_day_efficiency_rollup WHERE start_day BETWEEN $1 AND $2 {_CLASSES}"""  # noqa: S608 - fixed names
_EFFICIENCY_WORST_SQL = f"""
SELECT worst_session_id, worst_ratio FROM user_day_efficiency_rollup
WHERE start_day BETWEEN $1 AND $2 {_CLASSES} AND worst_session_id IS NOT NULL
ORDER BY worst_ratio DESC, worst_session_id COLLATE "C" LIMIT 1"""
_OVERVIEW_UNPRICED_SQL = f"""
SELECT DISTINCT unnest(unpriced) AS name
FROM user_day_overview_rollup WHERE start_day BETWEEN $1 AND $2 {_CLASSES} AND cardinality(unpriced) > 0"""
_OVERVIEW_LINES_SQL = f"""
SELECT line_day, sum(lines_added)::bigint AS added, sum(lines_removed)::bigint AS removed, bool_or(nonzero) AS nonzero
FROM user_day_overview_lines WHERE start_day BETWEEN $1 AND $2 {_CLASSES} GROUP BY line_day"""
_OVERVIEW_MODELS_SQL = f"""
SELECT model, sum(sessions)::bigint AS sessions, sum(spend) AS spend
FROM user_day_overview_models WHERE start_day BETWEEN $1 AND $2 {_CLASSES} GROUP BY model"""
# The window's Tools & Models rows: every name summed over the days, the top lists are cut by `assemble_tools`.
_TOOLS_SQL = f"""
SELECT kind, name, sum(calls)::bigint AS calls, sum(successes)::bigint AS successes
FROM user_day_tools_rollup WHERE start_day BETWEEN $1 AND $2 {_CLASSES} GROUP BY kind, name"""
_TOOLS_MODELS_SQL = f"""
SELECT model, sum(sessions)::bigint AS sessions, sum(total_tokens)::bigint AS total_tokens
FROM user_day_tools_models WHERE start_day BETWEEN $1 AND $2 {_CLASSES} GROUP BY model"""
# The window's ranking rows: the groups' sums and the classes they were seen in, the money cells and the few unpriced
# raw names.
_RANKING_SQL = f"""
SELECT project, repository, branch, sum(session_count)::bigint AS n, sum(turns)::bigint AS turns,
       sum(files_changed)::bigint AS files_changed, sum(lines_added)::bigint AS lines_added,
       sum(lines_removed)::bigint AS lines_removed, sum(tool_calls)::bigint AS tool_calls,
       sum(tool_successes)::bigint AS tool_successes, sum(spend) AS spend, bool_or(priced) AS priced,
       array_agg(DISTINCT sub_class) AS subs, array_agg(DISTINCT origin_class) AS origins
FROM user_day_ranking_rollup WHERE start_day BETWEEN $1 AND $2 {_CLASSES} GROUP BY project, repository, branch"""
_RANKING_GROUP_UNPRICED_SQL = f"""
SELECT project, repository, branch, array_agg(DISTINCT n) AS names FROM (
    SELECT project, repository, branch, unnest(unpriced) AS n
    FROM user_day_ranking_rollup WHERE start_day BETWEEN $1 AND $2 {_CLASSES} AND cardinality(unpriced) > 0) x
GROUP BY project, repository, branch"""  # noqa: S608 - _CLASSES is a constant of this module; the values are bound
_RANKING_MONEY_SQL = f"""
SELECT kind, money_key, project, repository, sum(cost) AS cost, sum(sessions)::bigint AS sessions
FROM user_day_ranking_money_rollup WHERE start_day BETWEEN $1 AND $2 {_CLASSES}
GROUP BY kind, money_key, project, repository"""
_RANKING_UNPRICED_SQL = f"""
SELECT kind, money_key, project, repository, array_agg(DISTINCT n) AS names FROM (
    SELECT kind, money_key, project, repository, unnest(unpriced) AS n
    FROM user_day_ranking_money_rollup WHERE start_day BETWEEN $1 AND $2 {_CLASSES} AND cardinality(unpriced) > 0) x
GROUP BY kind, money_key, project, repository"""  # noqa: S608 - _CLASSES is a constant of this module; the values are bound
# Two selects, one per partial index (`session_dims_resumed_last`, `session_dims_activity_pending`): one OR over the
# table is a scan of all of it. The first is written the way its index needs: `resumed` as the index states it
# (`resumed IS TRUE` is not matched to it and scans the table), and the start as an expression, so the planner filters
# the index's rows by it instead of intersecting them with the bitmap of every earlier start (72 ms against 0.7 to 5 s
# at 700K sessions).
_RESUMED_SQL = """
(SELECT session_id FROM session_dims
 WHERE resumed AND last_activity_day >= $2 AND started_at + interval '0' < $1::timestamptz)
UNION
(SELECT session_id FROM session_dims WHERE has_activity IS NULL AND started_at < $1::timestamptz)
LIMIT $3"""
# Non-resumed, known-activity sessions started in [start's UTC day, rollup_start) for the partial-first-day path (the
# live arm admits a single-day session that started earlier that day than `start`).
# _RESUMED_SQL (above) covers resumed and unknown-activity sessions started before rollup_start; this covers the rest
# (single-day sessions in the gap). The two sets are disjoint: resumed and has_activity IS NULL are excluded here.
_GAP_NONRESUMED_SQL = """
SELECT session_id FROM session_dims
WHERE started_at >= $1::timestamptz AND started_at < $2::timestamptz
  AND NOT resumed AND has_activity IS NOT NULL
LIMIT $3"""


def _naive_utc(value: datetime | None) -> datetime | None:
    return value if value is None or value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)


async def _overview_window(conn: Any, args: tuple, users: bool = False, efficiency: bool = False) -> Cohort:
    """The Overview reads its own rows and only the number of sessions from the developers' rows: not their totals,
    top models, money groups or unpriced names. `users` also reads the names of the developers (the count of them),
    `efficiency` the Efficiency rows."""
    cohort = Cohort()
    o = cohort.overview
    if users:
        o.users = {r["usr"] for r in await conn.fetch(_OVERVIEW_USERS_SQL, *args)}
    o.sessions = int(await conn.fetchval(_SESSIONS_SQL, *args))
    row = await conn.fetchrow(_OVERVIEW_SQL, *args)
    for name in OVERVIEW_SUMS:
        if row[name] is not None:
            setattr(o, name, row[name] if isinstance(row[name], Decimal) else int(row[name]))
    o.unpriced = {r["name"] for r in await conn.fetch(_OVERVIEW_UNPRICED_SQL, *args)}
    o.lines = {
        r["line_day"]: [r["added"], r["removed"], bool(r["nonzero"])]
        for r in await conn.fetch(_OVERVIEW_LINES_SQL, *args)
    }
    o.models = {r["model"]: [r["sessions"], r["spend"]] for r in await conn.fetch(_OVERVIEW_MODELS_SQL, *args)}
    o.class_cost = {r["sub_class"]: r["cost"] for r in await conn.fetch(_OVERVIEW_CLASSES_SQL, *args)}
    if efficiency:
        e, row = o.efficiency, await conn.fetchrow(_EFFICIENCY_SQL, *args)
        e.dead_cost, e.files_written, e.files_edited = _dec(row["dead_cost"]), row["files_written"], row["files_edited"]
        e.depth = [row[name] for name in _DEPTH_COLUMNS]
        e.spend_sessions = row["spend_sessions"]
        worst = await conn.fetchrow(_EFFICIENCY_WORST_SQL, *args)
        e.worst = None if worst is None else (worst["worst_ratio"], worst["worst_session_id"])
    return cohort


async def _ranking_window(conn: Any, args: tuple) -> Cohort:
    """The ranking rows of the window's days, combined; nothing else of the cohort is read."""
    cohort = Cohort()
    ranking = cohort.ranking
    for r in await conn.fetch(_RANKING_SQL, *args):
        ranking.groups[(r["project"], r["repository"], r["branch"])] = GroupAgg(
            n=r["n"],
            turns=r["turns"],
            files_changed=r["files_changed"],
            lines_added=r["lines_added"],
            lines_removed=r["lines_removed"],
            tool_calls=r["tool_calls"],
            tool_successes=r["tool_successes"],
            spend=r["spend"],
            subs=set(r["subs"]),
            origins=set(r["origins"]),
            priced=bool(r["priced"]),
        )
    for r in await conn.fetch(_RANKING_GROUP_UNPRICED_SQL, *args):
        ranking.groups[(r["project"], r["repository"], r["branch"])].unpriced.update(r["names"])
    for r in await conn.fetch(_RANKING_MONEY_SQL, *args):
        ranking.money[(r["kind"], r["money_key"], r["project"], r["repository"])] = MoneyCell(r["cost"], r["sessions"])
    for r in await conn.fetch(_RANKING_UNPRICED_SQL, *args):
        ranking.money[(r["kind"], r["money_key"], r["project"], r["repository"])].unpriced.update(r["names"])
    return cohort


async def _tools_window(conn: Any, args: tuple) -> Cohort:
    """The Tools & Models rows of the window's days, combined; nothing else of the cohort is read."""
    cohort = Cohort()
    tools = cohort.tools
    for r in await conn.fetch(_TOOLS_SQL, *args):
        if r["kind"] == "tool":
            tools.tools[r["name"]] = [int(r["calls"]), int(r["successes"])]
        else:
            tools.invocations[(r["kind"], r["name"])] = int(r["calls"])
    for r in await conn.fetch(_TOOLS_MODELS_SQL, *args):
        tools.models[r["model"]] = [int(r["sessions"]), int(r["total_tokens"])]
    return cohort


async def _window_cohort(  # noqa: PLR0913 - the arguments of one rollup read, passed on as they come from the callers
    conn: Any,
    d0: date,
    d1: date,
    subs: list[str] | None,
    origins: list[str] | None,
    *,
    models: bool = False,
    overview: bool = False,
    ranking: bool = False,
    users: bool = False,
    efficiency: bool = False,
    tools: bool = False,
) -> Cohort:
    """The window's start days combined, for the classes asked for (None: every class), in the caller's
    transaction. `models` also reads the per-model rows (/cost), `overview` the Overview rows (`users`: and the
    developers' names, for their count; `efficiency`: and the Efficiency rows)."""
    args = (d0, d1, subs, origins)
    if tools:
        return await _tools_window(conn, args)
    if ranking:
        return await _ranking_window(conn, args)
    if overview:
        return await _overview_window(conn, args, users, efficiency)
    cohort = Cohort()
    for r in await conn.fetch(_USERS_SQL, *args):
        cohort.users[r["usr"]] = UserAgg(
            session_count=r["session_count"],
            input_tokens=r["input_tokens"],
            output_tokens=r["output_tokens"],
            cache_read_tokens=r["cache_read_tokens"],
            cache_creation_tokens=r["cache_creation_tokens"],
            turns=r["turns"],
            tool_calls=r["tool_calls"],
            tool_successes=r["tool_successes"],
            net_lines=r["net_lines"],
            last_active=_naive_utc(r["last_active"]),
            subscription_types=set(r["subs"]),
            project_origins=set(r["origins"]),
        )
    for r in await conn.fetch(_TOP_SQL, *args):
        cohort.users[r["usr"]].top = (
            r["top_calls"],
            r["top_day"],
            r["top_session"],
            r["top_email"],
            r["top_model"],
            r["top_qs"],
        )
    for r in await conn.fetch(_MONEY_WINDOW_SQL, *args):
        cohort.money[(r["usr"], r["funding_route"], r["personal_billed"])] = MoneyAgg(
            cost_all=r["cost_all"], sessions_all=r["sessions_all"], cost_cd=r["cost_cd"], sessions_cd=r["sessions_cd"]
        )
    for r in await conn.fetch(_UNPRICED_SQL, *args):
        agg = cohort.money[(r["usr"], r["funding_route"], r["personal_billed"])]
        (agg.unpriced_all if r["which"] == "all" else agg.unpriced_cd).update(r["names"])
    if models:
        for r in await conn.fetch(_MODELS_SQL, *args):
            cohort.models[r["model"]] = ModelAgg(cost=r["cost"], sessions=r["sessions"])
        for r in await conn.fetch(_MODEL_UNPRICED_SQL, *args):
            cohort.models[r["model"]].unpriced.update(r["names"])
    return cohort


def _group(keys: dict, cost: Decimal, sessions: int, unpriced: set[str]) -> CostGroup:
    return CostGroup(
        keys=keys,
        cost_usd=float(cost),
        cache_read_cost_usd=0.0,
        session_count=sessions,
        unpriced_models=tuple(sorted(unpriced)),
        cost_incomplete=bool(unpriced),
    )


def _fold_cells(rows: Iterable[tuple[tuple, MoneyAgg]], use_cd: bool) -> dict[tuple, list]:
    """`[cost, sessions, unpriced names]` per key. The cells are summed in place: a new set per cell of 25,000 was
    most of this function's time."""
    out: dict[tuple, list] = {}
    for key, m in rows:
        cost, n, names = (
            (m.cost_cd, m.sessions_cd, m.unpriced_cd) if use_cd else (m.cost_all, m.sessions_all, m.unpriced_all)
        )
        cell = out.get(key)
        if cell is None:
            out[key] = [cost, n, set(names)]
        else:
            cell[0] += cost
            cell[1] += n
            if names:
                cell[2] |= names
    return out


def _money(cohort: Cohort) -> tuple[GroupedMoney, dict[str, Decimal]]:
    """The groupings of the Users page from the (developer, route, billed) cells, and each developer's spend (the
    ranking key): the same sums, scopes and `cost_daily`-only rules as the live statement's six arms."""
    groups: dict[str, list[CostGroup]] = {
        g: [] for g in (G_TOTAL, G_USER, G_ROUTE, G_PERSONAL, G_USER_ROUTE, G_USER_PERSONAL)
    }

    def fill(grouping: str, cells: Iterable[tuple[tuple, MoneyAgg]], names: tuple[str, ...]) -> dict[tuple, Decimal]:
        """The groups of one grouping with a session; the cost of each, by key."""
        costs: dict[tuple, Decimal] = {}
        for key, (cost, n, unpriced) in _fold_cells(cells, False).items():
            if n > 0:
                groups[grouping].append(_group(dict(zip(names, key, strict=True)), cost, n, unpriced))
                costs[key] = cost
        return costs

    # The Users page's groupings (`_USERS_GROUPINGS`) sum every priced session: unlike /cost's they are not limited to
    # the sessions whose token rows all come from `cost_daily`, so the `*_cd` columns are not read here.
    cells = list(cohort.money.items())
    total = _fold_cells((((), m) for _, m in cells), False).get(())
    if total and total[1] > 0:
        groups[G_TOTAL].append(_group({}, *total))
    by_user = {user: cost for (user,), cost in fill(G_USER, (((u,), m) for (u, _, _), m in cells), ("user",)).items()}
    fill(G_ROUTE, (((r,), m) for (_, r, _), m in cells), ("funding_route",))
    fill(G_PERSONAL, (((b,), m) for (_, _, b), m in cells), ("personal_billed",))
    fill(G_USER_ROUTE, (((u, r), m) for (u, r, _), m in cells), ("user", "funding_route"))
    fill(G_USER_PERSONAL, (((u, b), m) for (u, _, b), m in cells), ("user", "personal_billed"))
    return GroupedMoney(groups), by_user


def assemble_cost(cohort: Cohort) -> RollupCost:
    """The reader port's answer for /cost from a window's combined cohort: the spend total, per developer (over the
    sessions with a `cost_daily` row, the `cd` columns) and per model, the developers that list, and the session and
    token totals, with the rules of the live statements (`_COST_GROUPINGS`, `get_cost_kpis`, `get_cost_by_user`)."""
    spend = list(cohort.money.values())
    names: set[str] = set()
    for m in spend:
        names |= m.unpriced_all
    groups: dict[str, list[CostGroup]] = {G_TOTAL: [], G_USER: [], G_MODEL: []}
    sessions = sum(m.sessions_all for m in spend)
    if sessions > 0:
        groups[G_TOTAL].append(_group({}, sum((m.cost_all for m in spend), Decimal(0)), sessions, names))
    per_user: dict[str, list] = {}
    for (user, _, _), m in cohort.money.items():
        cell = per_user.setdefault(user, [Decimal(0), 0, set()])
        cell[0] += m.cost_cd
        cell[1] += m.sessions_cd
        cell[2] |= m.unpriced_cd
    for user, (cost, n, unpriced) in per_user.items():
        if n > 0:
            groups[G_USER].append(_group({"user": user}, cost, n, unpriced))
    for model, m in cohort.models.items():
        groups[G_MODEL].append(_group({"model": model}, m.cost, m.sessions, m.unpriced))
    listed: dict[str, int] = {}
    for (user, _, _), m in cohort.money.items():
        listed[user] = listed.get(user, 0) + m.sessions_cd
    users = cohort.users.values()
    return RollupCost(
        money=GroupedMoney(groups),
        developers=sorted(user for user, n in listed.items() if n > 0),
        total_sessions=sum(u.session_count for u in users),
        spend_sessions=sum(m.sessions_cd for m in spend),
        total_tokens=sum(
            u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_creation_tokens for u in users
        ),
    )


def assemble_overview(cohort: Cohort, with_users: bool = False) -> RollupOverview:
    """The reader port's answer for /overview from a window's combined cohort, in the row shapes of the live statements
    the handler reads, so both paths build the response with the same code: the session count is the sessions of
    the window (every one), the rest the Overview rows."""
    o = cohort.overview
    money_rows = [{"part": "dead", "n": o.dead_sessions}]
    if o.spend_cost or o.unpriced or o.cache_read_cost:
        money_rows.append(
            {
                "part": "money",
                "key": "spend",
                "cost_usd": float(o.spend_cost),
                "cache_read_cost_usd": float(o.cache_read_cost),
                "unpriced_models": sorted(o.unpriced),
            }
        )
    money_rows += [
        {"part": "money", "key": "class", "subscription_type": name, "cost_usd": float(cost)}
        for name, cost in sorted(o.class_cost.items())
    ]
    fact_rows = [
        {
            "part": "facts",
            "turns": o.turns,
            "tool_calls": o.tool_calls,
            "tool_calls_success": o.tool_successes,
            "duration_ms": o.duration_ms,
            "files_changed": o.files_changed,
        }
    ]

    def model_order(item: tuple[str, list]) -> tuple:
        name, (sessions, spend) = item
        # The live ORDER BY: sessions DESC, spend DESC (NULL first, as PostgreSQL sorts it descending), name in C order
        return (-sessions, spend is not None, -(spend or 0), name)

    model_rows = [{"part": "model", "key": name, "n": n} for name, (n, _) in sorted(o.models.items(), key=model_order)]
    kpi_rows = [
        {
            "total_sessions": o.sessions,
            "total_input_tokens": o.input_tokens,
            "total_output_tokens": o.output_tokens,
            "total_cache_read_tokens": o.cache_read_tokens,
            "total_cache_creation_tokens": o.cache_creation_tokens,
            "total_api_calls": o.api_calls,
        }
    ]
    added = removed = None
    line_rows = []
    for day, (day_added, day_removed, nonzero) in o.lines.items():
        added, removed = _nadd(added, day_added), _nadd(removed, day_removed)
        net = None if day_added is None or day_removed is None else day_added - day_removed
        line_rows.append({"part": "day", "key": day.isoformat(), "net_lines": net, "nonzero": nonzero})
    line_rows.append({"part": "total", "lines_added": added, "lines_removed": removed})
    # The developers of the stored days and those of the sessions read live: the Users page's `total_count`.
    total_users = len(o.users | set(cohort.users)) if with_users else None
    return RollupOverview(money_rows, fact_rows, model_rows, kpi_rows, line_rows, total_users)


def assemble(cohort: Cohort) -> RollupUsers:
    """The reader port's answer from a window's combined cohort: the ranking of every developer, the money and the rows
    the handler reads, in the shapes the live statements return."""
    money, spend = _money(cohort)
    rank = sorted(cohort.users, key=lambda name: (-spend.get(name, Decimal(0)).quantize(_CENT, ROUND_HALF_UP), name))
    users, facts, lines, last = [], [], [], []
    for name, a in cohort.users.items():
        users.append(
            {
                "developer_name": name,
                "session_count": a.session_count,
                "input_tokens": a.input_tokens,
                "output_tokens": a.output_tokens,
                "cache_read_tokens": a.cache_read_tokens,
                "cache_creation_tokens": a.cache_creation_tokens,
                "top_model": a.top[4] if a.top else None,
                "subscription_types": sorted(a.subscription_types),
                "project_origins": sorted(a.project_origins),
            }
        )
        facts.append(
            {"developer": name, "turns": a.turns, "tool_calls": a.tool_calls, "tool_successes": a.tool_successes}
        )
        if a.net_lines is not None:
            lines.append({"developer_name": name, "net_lines": a.net_lines})
        if a.last_active is not None:
            last.append({"developer_name": name, "last_active": a.last_active})
    return RollupUsers(UsersMoneyPage(rank, len(rank), money), users, facts, lines, last)


async def _on_held(scoped: PostgresCliAnalyticsReader, statement: str) -> None:
    """Run a session-level statement on the one connection a bounded reader holds."""
    async with scoped._engine.acquire() as conn:
        await conn.execute(statement)


def _split_first_day(f: LocalAnalyticsFilter) -> tuple[datetime, datetime, datetime, LocalAnalyticsFilter]:
    """If the start is not a midnight boundary, the rollup serves from the next UTC midnight and the sessions that
    started in the partial first day [start, rollup_start) are added live, so no data is excluded from the response.
    Returns the start, the midnight before it, the midnight the rollup serves from, and the filter for the rollup."""
    start = f.start_dt if f.start_dt.tzinfo else f.start_dt.replace(tzinfo=UTC)
    day0 = _day_start(start.date())
    rollup_start = day0 if start == day0 else _day_start(start.date() + timedelta(days=1))
    rollup_f = f if rollup_start == start else copy.copy(f)
    if rollup_f is not f:
        rollup_f.start_dt = rollup_start
    return start, day0, rollup_start, rollup_f


async def _days_refusal(
    conn: Any, d0: date, d1: date, version: str, min_shape: int, now: datetime, max_age_s: float
) -> str | None:
    """None when every day of the window exists under the current rates, holds what is read, and the job confirmed the
    newest day lately; else why not: `rates` (a day built under other rates), `missing_day` (a day missing or too thin)
    or `stale` (the job has not confirmed the newest day lately)."""
    states = await conn.fetch(
        "SELECT start_day, rates_version, shape, checked_at FROM user_rollup_day_state "
        "WHERE start_day BETWEEN $1 AND $2",
        d0,
        d1,
    )
    if any(s["rates_version"] != version for s in states):
        return "rates"
    if len(states) != (d1 - d0).days + 1 or any(s["shape"] < min_shape for s in states):
        return "missing_day"
    newest = max(s["checked_at"] for s in states if s["start_day"] == d1)
    return None if (now - newest).total_seconds() <= max_age_s else "stale"


async def _sessions_read_live(
    conn: Any,
    d0: date,
    day0: date,
    rollup_start: datetime,
    start: datetime,
    partial: bool,
    limit: int | None = None,
) -> list[str] | None:
    """The sessions the start-day rows cannot hold for the window: those that started before the rollup's first day
    and are active in it, and (a partial first day) the ones that started in the gap. None when there are more than
    `limit` (`MAX_RESUMED` unless given) to read live, so the window is answered by the live statements."""
    limit = MAX_RESUMED if limit is None else limit
    # Counted before the rollup tables are read: a window with too many is answered live anyway.
    # For a partial first day, $2 = start.date() (not d0): catches sessions active in [start, rollup_start).
    rstart = _day_start(d0)  # = rollup_start
    pre_rollup = [r["session_id"] for r in await conn.fetch(_RESUMED_SQL, rstart, start.date(), limit + 1)]
    if len(pre_rollup) > limit:
        return None
    # Non-resumed, known-activity sessions started in [day0, rollup_start): _RESUMED_SQL misses them because
    # they neither resumed nor have unknown activity. Disjoint from pre_rollup by construction.
    gap_nonresumed: list[str] = []
    if partial:
        gap_limit = limit - len(pre_rollup) + 1
        gap_nonresumed = [r["session_id"] for r in await conn.fetch(_GAP_NONRESUMED_SQL, day0, rollup_start, gap_limit)]
        if len(gap_nonresumed) > limit - len(pre_rollup):
            return None
    return [*pre_rollup, *gap_nonresumed]


async def _merge_live_sessions(  # noqa: PLR0913 - the rows one rollup read asks for, one flag each, as `serve_cohort`
    reader: PostgresCliAnalyticsReader,
    f: LocalAnalyticsFilter,
    rate_of: Callable[[str], ModelRates],
    window: Cohort,
    live_sessions: list[str],
    overview: bool,
    ranking: bool = False,
    efficiency: bool = False,
    tools: bool = False,
) -> None:
    # Use the original f (start_dt = exact_clamp) so activity from the original window start is counted;
    # cohort=None: all live_sessions started before rollup_start, so the rollup cannot double-count them.
    narrowed = f.restricted_to(live_sessions)
    async with reader.bounded() as scoped:
        await _on_held(scoped, f"SET random_page_cost = {SMALL_COHORT_RANDOM_PAGE_COST}")
        try:
            await scoped.materialize_sel(narrowed)
            if tools:
                window.merge(Cohort(tools=await gather_tools(scoped, narrowed)))
                return
            if ranking:
                window.merge(Cohort(ranking=await gather_ranking(scoped, narrowed, rate_of)))
                return
            live = await gather_cohort(scoped, narrowed, rate_of)
            if overview:
                live.overview = await gather_overview(scoped, narrowed, rate_of, efficiency=efficiency)
                live.overview.sessions = sum(u.session_count for u in live.users.values())
            window.merge(live)
        finally:
            # The pool's reset would undo it too; a held connection that is not the pool's (the job's) would not.
            await _on_held(scoped, "RESET random_page_cost")


def _min_shape(*, overview: bool, ranking: bool, efficiency: bool, tools: bool = False) -> int:
    """The lowest day shape a read of these rows accepts (a day of a lower shape is refused and rebuilt by the job)."""
    if tools:
        return SHAPE_TOOLS
    if efficiency:
        return SHAPE_EFFICIENCY
    if ranking:
        return SHAPE_RANKING
    if overview:
        return SHAPE_OVERVIEW
    return SHAPE_COST


def _window_eligible(rollup_f: LocalAnalyticsFilter, now: datetime, ranking: bool) -> bool:
    """Whether the scope, filters and window are what the rollup holds (the ranking rows have their own rules)."""
    return ranking_eligible(rollup_f, now) if ranking else eligible(rollup_f, now)


def _live_limit(ranking: bool) -> int | None:
    """How many resumed sessions a read may add live: None for the default, the ranking's own limit for its rows."""
    return MAX_RESUMED_RANKING if ranking else None


async def serve_cohort(  # noqa: PLR0913 - the rollup read's inputs and its keyword options, one call site per endpoint
    reader: PostgresCliAnalyticsReader,
    f: LocalAnalyticsFilter,
    rate_of: Callable[[str], ModelRates],
    version: str | None,
    *,
    max_age_s: float,
    now: datetime | None = None,
    models: bool = False,
    overview: bool = False,
    ranking: bool = False,
    users: bool = False,
    efficiency: bool = False,
    tools: bool = False,
) -> Cohort | None:
    """The window's combined cohort from the rollup plus the few sessions that started earlier and are active in the
    window (read live), or None when the rollup may not answer: another scope or filters, a window it is not made for,
    a day it lacks, built under other rates or of a shape below what is read, or a job that has not run lately. `models`
    also reads the per-model rows (/cost); `overview` the Overview rows, held by a day of shape `SHAPE_OVERVIEW`.
    /users and /cost ask for `SHAPE_COST`, which equals it since EPMCDME-17260, so every reader waits for the rebuild of
    a lower-shape day and the live statements answer meanwhile; `ranking` the ranking rows (shape `SHAPE_RANKING`),
    under `ranking_eligible`; `tools` the Tools & Models rows (shape `SHAPE_TOOLS`)."""
    now = now or datetime.now(UTC)
    start, day0, rollup_start, rollup_f = _split_first_day(f)
    if not version:
        request_stats.rollup_refused("rates")
        return None
    if not _window_eligible(rollup_f, now, ranking):
        request_stats.rollup_refused("eligibility")
        return None
    d0, d1 = _window_days(rollup_f)
    if d0 > d1:  # a window inside one UTC day (last_hour): no whole day to serve from the rollup
        request_stats.rollup_refused("eligibility")
        return None
    min_shape = _min_shape(overview=overview, ranking=ranking, efficiency=efficiency, tools=tools)
    async with reader._engine.acquire() as conn, conn.transaction(isolation="repeatable_read", readonly=True):
        if (refusal := await _days_refusal(conn, d0, d1, version, min_shape, now, max_age_s)) is not None:
            request_stats.rollup_refused(refusal)
            return None
        live_sessions = await _sessions_read_live(
            conn, d0, day0, rollup_start, start, rollup_f is not f, _live_limit(ranking)
        )
        if live_sessions is None:  # too many sessions to add live: the window is not answered from whole days
            request_stats.rollup_refused("too_many_live")
            return None
        if live_sessions and not request_stats.live_merge_allowed.get():
            # Refused before the day tables are read: the read repeats in the heavy lane and would pay for them twice.
            raise LiveMergeNeeded("the rollup merges resumed sessions live")
        window = await _window_cohort(
            conn,
            d0,
            d1,
            f.subscription_types or None,
            f.project_origins or None,
            models=models,
            overview=overview,
            ranking=ranking,
            users=users,
            efficiency=efficiency,
            tools=tools,
        )
    if live_sessions:
        await _merge_live_sessions(reader, f, rate_of, window, live_sessions, overview, ranking, efficiency, tools)
    return window


async def serve(
    reader: PostgresCliAnalyticsReader,
    f: LocalAnalyticsFilter,
    rate_of: Callable[[str], ModelRates],
    version: str | None,
    *,
    max_age_s: float,
    now: datetime | None = None,
) -> RollupUsers | None:
    """The Users page's data for `f` (see `serve_cohort`), or None when the rollup may not answer."""
    window = await serve_cohort(reader, f, rate_of, version, max_age_s=max_age_s, now=now)
    if window is None:
        return None
    # 25,000 money cells and 10,000 developers are folded and ranked off the event loop: ingest runs on it.
    return await asyncio.to_thread(assemble, window)


async def serve_cost(
    reader: PostgresCliAnalyticsReader,
    f: LocalAnalyticsFilter,
    rate_of: Callable[[str], ModelRates],
    version: str | None,
    *,
    max_age_s: float,
    now: datetime | None = None,
) -> RollupCost | None:
    """/cost's totals, per-developer money and per-model money for `f` (see `serve_cohort`), or None when the rollup
    may not answer."""
    window = await serve_cohort(reader, f, rate_of, version, max_age_s=max_age_s, now=now, models=True)
    if window is None:
        return None
    return await asyncio.to_thread(assemble_cost, window)


def assemble_efficiency(cohort: Cohort) -> RollupEfficiency:
    """The reader port's answer for /efficiency from a window's combined cohort, in the row shapes of the live
    statements the handler reads (`get_efficiency_facts`, the money of `get_efficiency_money` and
    `get_dashboard_kpis`), so both paths build the response with the same code. The worst session has no prompt,
    title, repository or branch yet: `serve_efficiency` reads them for that one session."""
    o, e = cohort.overview, cohort.overview.efficiency
    added = removed = None
    for day_added, day_removed, _ in o.lines.values():
        added, removed = _nadd(added, day_added), _nadd(removed, day_removed)
    fact_rows: list[dict] = []
    if e.worst is not None:
        fact_rows.append({"part": "worst", "ratio": e.worst[0], "session_id": e.worst[1]})
    fact_rows += [
        {"part": "depth", "key": bucket, "n": n} for bucket, n in zip(DEPTH_BUCKETS, e.depth, strict=True) if n > 0
    ]
    fact_rows.append({"part": "all", "n": o.sessions})
    fact_rows.append(
        {
            "part": "code",
            "files_changed": o.files_changed,
            "files_written": e.files_written,
            "files_edited": e.files_edited,
            "lines_added": added,
            "lines_removed": removed,
        }
    )
    money_rows: list[dict] = [{"part": "dead", "n": o.dead_sessions, "cost_usd": float(e.dead_cost)}]
    if o.spend_cost or o.unpriced or o.cache_read_cost:
        money_rows.append(
            {
                "part": "money",
                "key": "spend",
                "cost_usd": float(o.spend_cost),
                "cache_read_cost_usd": float(o.cache_read_cost),
                "unpriced_models": sorted(o.unpriced),
            }
        )
    kpi_rows = [
        {
            "total_sessions": o.sessions,
            "spend_sessions": e.spend_sessions,
            "total_cache_read_tokens": o.cache_read_tokens,
            "total_api_calls": o.api_calls,
        }
    ]
    return RollupEfficiency(fact_rows, money_rows, kpi_rows)


_WORST_SESSION_SQL = """
SELECT coalesce(session_title, '') AS session_title, coalesce(entrypoint, '') AS entrypoint,
       coalesce(first_prompt, '') AS prompt, repository, branch
FROM session_dims WHERE session_id = $1"""


async def serve_efficiency(
    reader: PostgresCliAnalyticsReader,
    f: LocalAnalyticsFilter,
    rate_of: Callable[[str], ModelRates],
    version: str | None,
    *,
    max_age_s: float,
    now: datetime | None = None,
) -> RollupEfficiency | None:
    """/efficiency's rows for `f` (see `serve_cohort`), or None when the rollup may not answer."""
    window = await serve_cohort(
        reader, f, rate_of, version, max_age_s=max_age_s, now=now, overview=True, efficiency=True
    )
    if window is None:
        return None
    served = await asyncio.to_thread(assemble_efficiency, window)
    worst = next((r for r in served.fact_rows if r["part"] == "worst"), None)
    if worst is not None:
        async with reader._engine.acquire() as conn:
            row = await conn.fetchrow(_WORST_SESSION_SQL, worst["session_id"])
        worst.update(
            session_title=row["session_title"] if row else "",
            entrypoint=row["entrypoint"] if row else "",  # `_served_prompt` hides a Desktop session's auto-title by it
            prompt=row["prompt"] if row else "",
            repository=row["repository"] if row else None,
            branch=row["branch"] if row else None,
        )
    return served


async def serve_overview(  # noqa: PLR0913 - the rollup read's inputs and its keyword options, one call site per endpoint
    reader: PostgresCliAnalyticsReader,
    f: LocalAnalyticsFilter,
    rate_of: Callable[[str], ModelRates],
    version: str | None,
    *,
    max_age_s: float,
    now: datetime | None = None,
    users: bool = False,
) -> RollupOverview | None:
    """/overview's cards and charts for `f` (see `serve_cohort`), or None when the rollup may not answer. `users` also
    counts the developers (`total_users`)."""
    window = await serve_cohort(reader, f, rate_of, version, max_age_s=max_age_s, now=now, overview=True, users=users)
    if window is None:
        return None
    return await asyncio.to_thread(assemble_overview, window, users)


# ── the Repositories and Projects tabs from the ranking rows ─────────────────────────────────────────────────────


def _opt(name: str) -> str | None:
    return name or None


def _most_frequent(counts: Mapping[str, int] | None) -> str:
    """The name with the most sessions, ties to the alphabetically first (code-point order); '' when there is none."""
    if not counts:
        return ""
    return min(counts, key=lambda name: (-counts[name], name))


def _fold_repositories(groups: Mapping[GroupKey, GroupAgg]) -> dict[GroupKey, GroupAgg]:
    """The groups of the Repositories tab without branches: one per repository, whose project is the repository's most
    frequent one (ties to the alphabetically first, code-point order), as `get_repository_ranking` names it."""
    folded: dict[str, GroupAgg] = {}
    counts: dict[str, dict[str, int]] = {}
    for (project, repository, _), group in groups.items():
        folded.setdefault(repository, GroupAgg()).merge(group)
        if project:
            by_project = counts.setdefault(repository, {})
            by_project[project] = by_project.get(project, 0) + group.n
    out: dict[GroupKey, GroupAgg] = {}
    for repository, group in folded.items():
        out[(_most_frequent(counts.get(repository)), repository, "")] = group
    return out


def _rank(groups: Mapping[GroupKey, GroupAgg]) -> list[tuple[GroupKey, GroupAgg]]:
    """The live order: sessions, then spend (both descending), then the repository and the project in code-point
    order (the branch last, which the live statement leaves to the planner)."""
    return sorted(groups.items(), key=lambda kv: (-kv[1].n, -kv[1].spend, kv[0][1], kv[0][0], kv[0][2]))


def _group_row(rank: int, key: GroupKey, group: GroupAgg, include_branches: bool) -> dict:
    return {
        "part": "group",
        "key": str(rank),
        "n": group.n,
        "project_name": _opt(key[0]),
        "repository": _opt(key[1]),
        "branch": _opt(key[2]) if include_branches else None,
        "cost_usd": float(group.spend),
        "unpriced_models": sorted(group.unpriced),
        "cost_incomplete": bool(group.unpriced),
        "cost_priced": group.priced,
    }


def _fact_row(key: GroupKey, group: GroupAgg, include_branches: bool) -> dict:
    return {
        "part": "group",
        "n": group.n,
        "turns": group.turns,
        "files_changed": group.files_changed,
        "lines_added": group.lines_added,
        "lines_removed": group.lines_removed,
        "tool_calls": group.tool_calls,
        "tool_calls_success": group.tool_successes,
        "subscription_types": sorted(group.subs),
        "project_origins": sorted(group.origins),
        "project_name": _opt(key[0]) if include_branches else None,
        "repository": _opt(key[1]),
        "branch": _opt(key[2]) if include_branches else None,
    }


def _money_rows(
    money: Mapping[MoneyKey, MoneyCell],
    part: str,
    names: tuple[str, ...],
    level: Callable[[str, str], tuple],
    keep: Callable[[str, str], bool] = lambda _project, _repository: True,
) -> list[dict]:
    """The funding-route and personal-billed money cells of the (project, repository) pairs `keep` admits, folded to
    `level` (the columns `names`), as the live statements' rows of `part` (`total` or `bucket`)."""
    cells: dict[tuple, MoneyCell] = {}
    for (kind, money_key, project, repository), cell in money.items():
        if kind != "spend" and keep(project, repository):
            cells.setdefault((kind, money_key, *level(project, repository)), MoneyCell()).merge(cell)
    rows = []
    for (kind, money_key, *rest), cell in sorted(cells.items()):
        row: dict[str, Any] = {
            "part": part,
            "key": ("b_" if part == "bucket" else "") + ("route" if kind == "route" else "billed"),
            **{name: _opt(value) for name, value in zip(names, rest, strict=True)},
            "cost_usd": float(cell.cost),
            "cache_read_cost_usd": 0.0,
            "session_count": cell.sessions,
            "unpriced_models": sorted(cell.unpriced),
            "cost_incomplete": bool(cell.unpriced),
        }
        row["funding_route" if kind == "route" else "personal_billed"] = money_key if kind == "route" else True
        rows.append(row)
    return rows


def _spend_row(money: Mapping[MoneyKey, MoneyCell]) -> list[dict]:
    cell = money.get(SPEND_KEY)
    if cell is None:
        return []
    return [
        {
            "part": "total",
            "key": "spend",
            "cost_usd": float(cell.cost),
            "cache_read_cost_usd": 0.0,
            "session_count": cell.sessions,
            "unpriced_models": sorted(cell.unpriced),
            "cost_incomplete": bool(cell.unpriced),
        }
    ]


_TOOL_ROWS = 20  # the tools the tab lists, as the live statement's LIMIT


def assemble_tools(cohort: Cohort) -> RollupTools:
    """The reader port's answer for /tools from a window's combined cohort, in the row shapes of the live statements
    (`get_tool_usage`, `get_model_breakdown`, `get_invocations`) so both paths build the response with the same code.
    The tools are ordered by calls, then by name in byte order (`COLLATE "C"`), and cut to the tab's 20 after the days
    are summed; the models (by name) and invocations are all given, the handler keeps and cuts them as for the live
    rows."""
    t = cohort.tools
    top = sorted(t.tools.items(), key=lambda item: (-item[1][0], item[0]))[:_TOOL_ROWS]
    return RollupTools(
        tool_rows=[{"tool_name": name, "call_count": calls, "success_count": ok} for name, (calls, ok) in top],
        model_rows=[
            {"model_name": model, "session_count": n, "total_tokens": tokens}
            for model, (n, tokens) in sorted(t.models.items())
        ],
        invocation_rows=[{"kind": kind, "name": name, "count": n} for (kind, name), n in t.invocations.items()],
    )


async def serve_tools(
    reader: PostgresCliAnalyticsReader,
    f: LocalAnalyticsFilter,
    rate_of: Callable[[str], ModelRates],
    version: str | None,
    *,
    max_age_s: float,
    now: datetime | None = None,
) -> RollupTools | None:
    """/tools' rows for `f` (see `serve_cohort`), or None when the rollup may not answer."""
    window = await serve_cohort(reader, f, rate_of, version, max_age_s=max_age_s, now=now, tools=True)
    if window is None:
        return None
    return await asyncio.to_thread(assemble_tools, window)


def assemble_ranking(
    agg: RankingAgg, include_branches: bool, page: int, per_page: int | None, unattributed_last: bool = False
) -> RollupRanking:
    """The reader port's answer for the Repositories tab from a window's combined ranking rows, in the row shapes of
    `get_repository_ranking` and `get_repository_facts`: the groups (a (project, repository, branch) group with
    branches, else a repository), their page, their count, the window's money and the estimates of the page's groups."""
    groups = agg.groups if include_branches else _fold_repositories(agg.groups)
    ranked = _rank(groups)
    first = 0
    if not include_branches and per_page is not None:
        first = page * per_page
    shown = ranked[first : first + per_page] if not include_branches and per_page is not None else ranked
    counted = len(ranked)
    ranks = None
    if unattributed_last and not include_branches and per_page is not None:
        # The group without a repository ranks after the named ones, is never paged or counted and rides along with
        # every page, for the handler to place (`get_repository_ranking(unattributed_last=True)`).
        named = [kv for kv in ranked if kv[0][1] != ""]
        unattributed = [kv for kv in ranked if kv[0][1] == ""]
        shown = named[first : first + per_page] + unattributed
        counted = len(named)
        ranks = [first + i + 1 for i in range(len(shown) - len(unattributed))] + [counted + 1] * len(unattributed)
    rows = [
        _group_row(ranks[i] if ranks else first + i + 1, key, group, include_branches)
        for i, (key, group) in enumerate(shown)
    ]
    rows.append({"part": "count", "n": counted})
    rows += _spend_row(agg.money)
    rows += _money_rows(agg.money, "total", (), lambda _project, _repository: ())
    if include_branches:
        rows += _money_rows(agg.money, "bucket", ("project_name", "repository"), lambda p, r: (p, r))
    else:
        page_repositories = {key[1] for key, _ in shown}
        rows += _money_rows(
            agg.money, "bucket", ("repository",), lambda _p, r: (r,), keep=lambda _p, r: r in page_repositories
        )
    return RollupRanking(rows, [_fact_row(key, group, include_branches) for key, group in shown])


def assemble_projects(agg: RankingAgg, page: int, per_page: int, unattributed_last: bool = False) -> RollupProjects:
    """The reader port's answer for the Projects tab from a window's combined ranking rows: the projects by spend (the
    unattributed one last, then by name), the page's, and the rows `get_projects` reads for the page's groups.
    `unattributed_last` (the totals-only page): the unattributed project is not ranked or counted, and it is the last
    name of the last page, outside `per_page`."""
    spend: dict[str, Decimal] = {}
    for (project, _, _), group in agg.groups.items():
        spend[project] = spend.get(project, Decimal(0)) + group.spend
    ordered = sorted(spend, key=lambda project: (project == "", -spend[project], project))
    if unattributed_last:
        named = [project for project in ordered if project != ""]
        names = named[page * per_page : (page + 1) * per_page]
        last_page = (page + 1) * per_page >= len(named) and (page == 0 or page * per_page < len(named))
        if "" in spend and last_page:
            names.append("")
        ordered = named
    else:
        names = ordered[page * per_page : (page + 1) * per_page]
    on_page = set(names)
    shown = _rank({key: group for key, group in agg.groups.items() if key[0] in on_page})
    unpriced = agg.money.get(SPEND_KEY)
    return RollupProjects(
        total=len(ordered),
        project_names=[_opt(name) for name in names],
        group_rows=[_group_row(i + 1, key, group, True) for i, (key, group) in enumerate(shown)],
        fact_rows=[_fact_row(key, group, True) for key, group in shown],
        repository_buckets=_money_rows(
            agg.money, "bucket", ("project_name", "repository"), lambda p, r: (p, r), keep=lambda p, _r: p in on_page
        ),
        project_buckets=[
            {**row, "project_name": row["project_name"] or ""}
            for row in _money_rows(
                agg.money, "bucket", ("project_name",), lambda p, _r: (p,), keep=lambda p, _r: p in on_page
            )
        ],
        window_buckets=_money_rows(agg.money, "bucket", (), lambda _p, _r: ()),
        unpriced_models=sorted(unpriced.unpriced) if unpriced else [],
    )


async def serve_repository_ranking(  # noqa: PLR0913 - the rollup read's inputs and its keyword options
    reader: PostgresCliAnalyticsReader,
    f: LocalAnalyticsFilter,
    include_branches: bool,
    rate_of: Callable[[str], ModelRates],
    version: str | None,
    *,
    page: int = 0,
    per_page: int | None = None,
    unattributed_last: bool = False,
    max_age_s: float,
    now: datetime | None = None,
) -> RollupRanking | None:
    """The Repositories tab's ranking for `f` (see `serve_cohort`), or None when the rollup may not answer."""
    window = await serve_cohort(reader, f, rate_of, version, max_age_s=max_age_s, now=now, ranking=True)
    if window is None:
        return None
    return await asyncio.to_thread(
        assemble_ranking, window.ranking, include_branches, page, per_page, unattributed_last
    )


async def serve_project_page(  # noqa: PLR0913 - the rollup read's inputs, the page and its keyword options
    reader: PostgresCliAnalyticsReader,
    f: LocalAnalyticsFilter,
    page: int,
    per_page: int,
    rate_of: Callable[[str], ModelRates],
    version: str | None,
    *,
    unattributed_last: bool = False,
    max_age_s: float,
    now: datetime | None = None,
) -> RollupProjects | None:
    """The Projects tab's page for `f` (see `serve_cohort`), or None when the rollup may not answer."""
    window = await serve_cohort(reader, f, rate_of, version, max_age_s=max_age_s, now=now, ranking=True)
    if window is None:
        return None
    return await asyncio.to_thread(assemble_projects, window.ranking, page, per_page, unattributed_last)
