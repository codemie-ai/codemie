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

"""SQL builder of the paged branches statement of the CLI Analytics repository drill-down.

The database groups the selected sessions by branch, ranks the groups (session count, spend, name), cuts the page and
reads the facts of the page's sessions only. The NULL-branch group ranks after the named ones, is never paged or
counted, and rides along with every page for the handler to place. The reader supplies the fragments it owns (the
session CTEs, the money query and the fact columns), so this module does not import the reader.
"""

from __future__ import annotations

from collections.abc import Callable

_C = 'COLLATE "C"'


def branch_ranking_sql(  # noqa: PLR0913 - keyword-only SQL fragments, each owned by the reader
    *,
    sel_ctes: str,
    priced_groups: str,
    money_from: str,
    fact_ctes: Callable[[str], str],
    facts_from: str,
    fact_columns: str,
    part: Callable[..., str],
    total_part: str,
) -> str:
    """One statement of `part` rows: `group` (a branch with its facts and cost), `count` (named branches before
    paging) and `total`. Needs `$offset`, `$limit`, the window and the rates parameters.

    `fact_ctes(sessions)` renders the per-session fact CTEs over the named CTE, `facts_from` is the session-facts
    FROM clause over `selp s`, `fact_columns` the columns it selects and `total_part` the `total` select."""
    return f"""
        WITH {sel_ctes},
        {priced_groups},
        grp AS (SELECT nullif(branch, '') AS branch, count(*) AS session_count FROM sel GROUP BY 1),
        money AS (SELECT * FROM {money_from}),
        ranked AS (
            SELECT g.*, coalesce(m.cost_usd, 0) AS cost,
                   row_number() OVER (ORDER BY (g.branch IS NULL), g.session_count DESC, coalesce(m.cost_usd, 0) DESC,
                                      coalesce(g.branch, '') {_C}) AS rn
            FROM grp g LEFT JOIN money m ON m."grouping" = 'g_spend' AND m.branch = coalesce(g.branch, '')
        ),
        page AS (
            SELECT * FROM ranked
            WHERE branch IS NULL OR (rn > $offset::bigint AND rn <= $offset::bigint + $limit::bigint)
        ),
        selp AS (SELECT * FROM sel WHERE nullif(branch, '') IN (SELECT branch FROM page)
                 OR (nullif(branch, '') IS NULL AND EXISTS (SELECT 1 FROM page WHERE branch IS NULL))),
        {fact_ctes("selp")},
        ps AS (
            SELECT nullif(s.branch, '') AS branch, {fact_columns}
            FROM {facts_from}
        ),
        facts AS (
            SELECT branch, sum(turns) AS turns, sum(files_changed) AS files_changed, sum(lines_added) AS lines_added,
                   sum(lines_removed) AS lines_removed, sum(tool_calls) AS tool_calls,
                   sum(tool_calls_success) AS tool_calls_success, array_agg(DISTINCT sub) AS subscription_types,
                   array_agg(DISTINCT origin) AS project_origins
            FROM ps GROUP BY branch
        )
        SELECT {part(
            "group", key="p.rn::text", n="p.session_count", branch="p.branch", cost_usd="p.cost",
            turns="a.turns", files_changed="a.files_changed", lines_added="a.lines_added",
            lines_removed="a.lines_removed", tool_calls="a.tool_calls", tool_calls_success="a.tool_calls_success",
            subscription_types="a.subscription_types", project_origins="a.project_origins")}
        FROM page p LEFT JOIN facts a ON a.branch IS NOT DISTINCT FROM p.branch
        UNION ALL
        SELECT {part("count", n="count(*) FILTER (WHERE branch IS NOT NULL)")} FROM grp
        UNION ALL
        {total_part}"""  # noqa: S608 - trusted fragments from the reader; values are bound parameters
