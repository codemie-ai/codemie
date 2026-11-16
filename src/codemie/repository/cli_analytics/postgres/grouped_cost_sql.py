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

"""SQL builders of the grouped catalog cost of the CLI Analytics dashboards (EPMCDME-17051, shared with -17052).

Two levels, both placed in a statement that already defines `sel` built with `with_developer=True`, and bound with
`rates_params` like the Sessions cost sort:

- `priced_groups_cte()`: the CTE `priced_groups`, one row per (session, raw model) of `sel` with a token row, priced
  once (the cache split clamped at that grain, `cost` and `cache_read_cost` each `round(_, 9)`), with the session's
  keys. Every row counts toward the money, the Personal-paid sessions' too (EPMCDME-17260).
- `grouped_cost_sql(groupings)`: the derived table `gc`, every requested grouping summed from that one priced set.

The pricing terms are the builders of `cost_key_sql`, so the Sessions key and these figures cannot disagree. Contract:
`docs/stories/2026-11-09-cli-analytics-python-load/shared-cost-sql-contract.md`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, get_args

from codemie.repository.cli_analytics.postgres.cost_key_sql import (
    _SEL_SESSIONS,
    RATES_JOIN,
    cache_read_term,
    cost_terms,
    model_token_groups,
    unpriced_test,
)
from codemie.repository.cli_analytics.scope import FUNDING_ROUTES, sql_personal_billed, sql_subscription_type

GroupKey = Literal[
    "user", "project", "repository", "branch", "model", "funding_route", "personal_billed", "subscription_type"
]
_GROUP_KEYS: tuple[str, ...] = get_args(GroupKey)

# Per key: the `priced_groups` column it reads and the type of its NULL where a grouping does not use it. `user` is a
# reserved word in PostgreSQL, so the column is quoted wherever it is named.
_KEY_SOURCES: dict[str, tuple[str, str]] = {
    "user": ('"user"', "text"),
    "project": ("project", "text"),
    "repository": ("repository", "text"),
    "branch": ("branch", "text"),
    "model": ("normalized_model_name", "text"),
    "funding_route": ("funding_route", "text"),
    "personal_billed": ("personal_billed", "boolean"),
    "subscription_type": ("sub_class", "text"),
}
_KEY_COLUMNS = {key: '"user"' if key == "user" else key for key in _GROUP_KEYS}


@dataclass(frozen=True)
class Grouping:
    """One grouping of `gc`: its rows carry `name` in the column `grouping`."""

    name: str  # returned in column `grouping`
    keys: Sequence[GroupKey]  # [] = whole window
    cost_daily_only: bool = False  # WHERE has_cost_daily

    def __post_init__(self) -> None:
        keys = tuple(self.keys)
        unknown = [k for k in keys if k not in _GROUP_KEYS]
        if unknown:
            raise ValueError(f"grouping {self.name!r}: unknown key {unknown}, expected some of {_GROUP_KEYS}")
        if len(set(keys)) != len(keys):
            raise ValueError(f"grouping {self.name!r}: a key is repeated in {keys}")
        object.__setattr__(self, "keys", keys)


def _token_groups_joined_to_sel(scope: str = "sel") -> str:
    """`model_token_groups` with both token sources joined to `sel` instead of filtered by `session_id IN (SELECT
    session_id FROM sel)`. `sel` has one row per session, so the rows are the same; on 600K sessions the planner
    probes every partition's index once per session for the IN form (18 s) and hashes `sel` for the join (5 s).
    `model_token_groups` itself stays as it is (the Sessions key and the flags read it). `scope` is the CTE joined
    instead of `sel` (the stored form passes `live`, a subset of `sel`, so the rows are those of its sessions)."""
    text = model_token_groups(normalized=True)
    cost_daily = f"FROM cost_daily c WHERE {_SEL_SESSIONS[0]}"
    usage = f"FROM session_usage u WHERE u.attrs->>'token_source' = 'subagent_usage' AND {_SEL_SESSIONS[1]}"
    if cost_daily not in text or usage not in text:
        raise RuntimeError("model_token_groups changed: update the join form of the grouped cost token read")
    return text.replace(
        cost_daily, f"FROM cost_daily c JOIN {scope} x ON x.session_id = c.session_id WHERE true"
    ).replace(
        usage,
        f"FROM session_usage u JOIN {scope} x ON x.session_id = u.session_id "
        "WHERE u.attrs->>'token_source' = 'subagent_usage'",
    )


# The sessions of `sel` whose stored groups are not valid (`$stale`, read by `model_groups_mode` in the request's
# snapshot), joined and materialized: an `IN (subselect)` made 190 unstamped sessions slow a request 3x (16427).
_LIVE_CTE = """live AS MATERIALIZED (
        SELECT s.session_id FROM sel s JOIN unnest($stale::text[]) AS u(session_id) ON u.session_id = s.session_id)"""

# The token groups of the stored form: the stored rows of the sessions of `sel` that are not in `live`, then the live
# build of the sessions of `live`. Both arms carry the same columns, `g` is the alias the pricing expressions read.
_STORED_ARM = """SELECT m.session_id, m.model_name, m.normalized_model_name, m.input_tokens, m.output_tokens,
                       m.cache_read_tokens, m.cache_creation_tokens, m.cache_creation_5m_tokens,
                       m.cache_creation_1h_tokens, m.has_cost_daily
                FROM session_model_groups m
                JOIN sel x ON x.session_id = m.session_id
                LEFT JOIN live l ON l.session_id = m.session_id
                WHERE l.session_id IS NULL"""


def priced_groups_cte(stored: bool = False) -> str:
    """The CTE `priced_groups AS MATERIALIZED (...)`: session_id, model_name, normalized_model_name, "user"
    (the developer of `sel`), project, repository, branch, funding_route (NULL or outside `FUNDING_ROUTES`:
    'unknown'), personal_billed, sub_class, has_cost_daily, cost, cache_read_cost, unpriced.
    Needs `sel` with the developer column and the `rates_params` of the window's models.

    `stored=False` (the default, and the fallback of every stored read) builds every group live from `cost_daily` and
    `session_usage`. `stored=True` returns two CTEs, `live` and `priced_groups`, and needs the parameter `$stale` (the
    ids of the sessions whose rows in `session_model_groups` are not valid, see `stored_mode.model_groups_mode`):
    the groups of the other sessions are read from the table (tokens only, priced here with the same builders) and
    those of `$stale` (the ones in `sel`) are built live. The output columns are the same."""
    billed = sql_personal_billed("s")
    routes = ", ".join(f"'{route}'" for route in FUNDING_ROUTES)
    if stored:
        tokens = f"""(SELECT g.*,
                       EXISTS (SELECT 1 FROM cost_daily cd WHERE cd.session_id = g.session_id) AS has_cost_daily
                FROM {_token_groups_joined_to_sel("live")})"""
        source = f"""(({_STORED_ARM})
                UNION ALL
                {tokens}) g"""
        prefix, has_cost_daily = f"{_LIVE_CTE},\n        ", "g.has_cost_daily"
    else:
        source = _token_groups_joined_to_sel()
        prefix = ""
        has_cost_daily = "EXISTS (SELECT 1 FROM cost_daily cd WHERE cd.session_id = g.session_id)"
    return f"""{prefix}priced_groups AS MATERIALIZED (
        SELECT g.session_id,
               g.model_name,
               g.normalized_model_name,
               s.developer                                  AS "user",
               s.project_name                               AS project,
               s.repository,
               s.branch,
               CASE WHEN d.funding_route IN ({routes}) THEN d.funding_route ELSE 'unknown' END AS funding_route,
               {billed}                                     AS personal_billed,
               {sql_subscription_type("s.subscription_type")} AS sub_class,
               {has_cost_daily} AS has_cost_daily,
               round(({cost_terms()})::numeric, 9)          AS cost,
               round(({cache_read_term()})::numeric, 9)     AS cache_read_cost,
               {unpriced_test()}                            AS unpriced
        FROM {source}
        JOIN sel s ON s.session_id = g.session_id
        LEFT JOIN session_dims d ON d.session_id = g.session_id
        {RATES_JOIN})"""


_SESSION_KEYS = tuple(k for k in _GROUP_KEYS if k != "model")
_MODEL_COLUMN = "model"


def _key_column(key: str) -> str:
    return _MODEL_COLUMN if key == "model" else _KEY_COLUMNS[key]


def _session_value(key: str) -> str:
    """A session-level key of a `priced_groups` row, folded over the rows of one session (all rows agree)."""
    column = _KEY_SOURCES[key][0]
    return f"bool_and(p.{column})" if _KEY_SOURCES[key][1] == "boolean" else f"min(p.{column})"


def _cells(keys: Sequence[str], with_model: bool) -> str:
    """Pre-aggregation of the `priced_groups` rows into small cells, by the session-level `keys` the groupings use
    (plus `has_cost_daily`, which a `cost_daily_only` WHERE reads), and by the normalised model when `with_model`.

    A session has one value of every session-level key, so the cells of a coarser grouping hold disjoint sessions and
    `session_count` adds up exactly. That needs one row per session in a cell: the inner query folds the rows of a
    session (and, with a model, of a session and a normalised model: Foo-X and foo-x are one) to one row first, the
    only place a session is counted. The unpriced raw names ride on extra zero-money rows (`uname`), so the groupings
    only union them; they are the few rows with `p.unpriced`."""
    session_cols = "".join(f"{_session_value(k)} AS {_key_column(k)}, " for k in keys)
    model_in = "p.normalized_model_name AS model, " if with_model else ""
    blank = " AND p.normalized_model_name <> ''" if with_model else ""
    grain = "p.session_id, p.normalized_model_name" if with_model else "p.session_id"
    outer = [_MODEL_COLUMN] if with_model else []
    outer += [_key_column(k) for k in keys] + ["has_cost_daily"]
    cols = ", ".join(outer)
    unpriced_exprs = (
        ["p.normalized_model_name"] * with_model + [f"p.{_KEY_SOURCES[k][0]}" for k in keys] + ["p.has_cost_daily"]
    )
    unpriced_cols = ", ".join(f"{e} AS {c}" for e, c in zip(unpriced_exprs, outer, strict=True))
    unpriced_group = ", ".join(unpriced_exprs)
    return f"""SELECT {cols},
               sum(cost) AS cost, sum(cache_read_cost) AS cache_read_cost, count(*) AS n,
               bool_or(unpriced) AS unpriced, NULL::text AS uname
        FROM (SELECT {model_in}{session_cols}bool_and(p.has_cost_daily) AS has_cost_daily,
                     sum(p.cost) AS cost, sum(p.cache_read_cost) AS cache_read_cost, bool_or(p.unpriced) AS unpriced
              FROM priced_groups p
              WHERE true{blank}
              GROUP BY {grain}) s
        GROUP BY {cols}
        UNION ALL
        SELECT {unpriced_cols}, 0::numeric, 0::numeric, 0::bigint, true, p.model_name
        FROM priced_groups p
        WHERE p.unpriced{blank}
        GROUP BY {unpriced_group}, p.model_name"""


def _arm(grouping: Grouping, used: Sequence[str]) -> str:
    keys = [_key_column(k) for k in grouping.keys]
    columns = [
        f"c.{_key_column(k)} AS {_key_column(k)}"
        if k in grouping.keys
        else f"NULL::{_KEY_SOURCES[k][1]} AS {_key_column(k)}"
        for k in used
    ]
    where = ["c.has_cost_daily"] if grouping.cost_daily_only else []
    name = grouping.name.replace("'", "''")
    group_by = f" GROUP BY {', '.join(f'c.{k}' for k in keys)}" if keys else " HAVING count(*) > 0"
    source = "cells_model" if "model" in grouping.keys else "cells_session"
    return f"""SELECT '{name}'::text AS "grouping",{"".join(f" {c}," for c in columns)}
               sum(c.cost)                                  AS cost_usd,
               sum(c.cache_read_cost)                       AS cache_read_cost_usd,
               sum(c.n)::bigint                             AS session_count,
               coalesce(array_agg(DISTINCT c.uname COLLATE "C" ORDER BY c.uname COLLATE "C")
                        FILTER (WHERE c.uname IS NOT NULL), '{{}}')::text[] AS unpriced_models,
               coalesce(bool_or(c.unpriced), false)        AS cost_incomplete
        FROM {source} c
        WHERE {" AND ".join(where) or "true"}{group_by}"""


def grouped_cost_sql(groupings: Sequence[Grouping]) -> str:
    """The derived table `gc("grouping", <one column per key any grouping uses, NULL where unused>, cost_usd,
    cache_read_cost_usd, session_count, unpriced_models, cost_incomplete)`, one UNION ALL arm per grouping over the
    CTE `priced_groups` of the same statement (priced once, it is MATERIALIZED). The key columns follow `GroupKey`
    order; `user` is quoted. A grouping that matches no row has no row, `[]` included.

    The `priced_groups` rows (one per session and raw model, about 800K at 600K sessions) are aggregated once or twice
    into small cells (`cells_session` for the groupings without a model, `cells_model` for the ones with it) and
    every arm sums those, so no arm sorts the priced rows."""
    if not groupings:
        raise ValueError("grouped_cost_sql needs at least one grouping")
    names = [g.name for g in groupings]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ValueError(f"duplicate grouping names {duplicates}")
    used = [k for k in _GROUP_KEYS if any(k in g.keys for g in groupings)]
    arms = "\n        UNION ALL\n        ".join(f"({_arm(g, used)})" for g in groupings)
    ctes = []
    for name, with_model in (("cells_session", False), ("cells_model", True)):
        users = [g for g in groupings if ("model" in g.keys) == with_model]
        if users:
            keys = [k for k in _SESSION_KEYS if any(k in g.keys for g in users)]
            ctes.append(f"{name} AS MATERIALIZED ({_cells(keys, with_model)})")
    return f"""(
        WITH {", ".join(ctes)}
        {arms}
    ) gc"""
