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

"""SQL builder of the per-project totals statement of the CLI Analytics projects drill-down
(`include_repositories=false`).

The caller restricts the filter to the sessions of the page (the page's named projects and, on the last page, the
unattributed one), so `sel` is the page. The database groups them by project and returns, per project, the counts of its
sessions, its catalog-priced spend, the number of its repositories and the ids of its sessions (the handler prices the
estimate buckets from them). No per-session row goes back to the service and no repository or branch row is built. The
reader supplies the fragments it owns (the session CTEs, the money query and the fact columns), so this module does not
import the reader.
"""

from __future__ import annotations

_C = 'COLLATE "C"'


def project_totals_sql(  # noqa: PLR0913 - keyword-only SQL fragments, each owned by the reader
    *,
    sel_ctes: str,
    priced_groups: str,
    money_from: str,
    fact_ctes: str,
    facts_from: str,
    fact_columns: str,
) -> str:
    """One statement, a row per project of `sel` (the unattributed project has `project_name` NULL, and comes last):
    `session_count`, `turns`, `files_changed`, `lines_added` / `lines_removed` (NULL while no session reports lines),
    `tool_calls`, `tool_calls_success`, the class lists, `cost_usd`, `repository_count` and `session_ids`.

    `repository_count` counts the project's distinct repositories, the sessions without one as one more: the number of
    (project, repository) groups the nested breakdown has. Needs the window and the rates parameters only.

    `fact_ctes` renders the per-session fact CTEs over `sel`, `facts_from` is the session-facts FROM clause over
    `sel s` and `fact_columns` the columns it selects (`session_id`, the facts, `sub`, `origin`)."""
    return f"""
        WITH {sel_ctes},
        {priced_groups},
        {fact_ctes},
        money AS (SELECT * FROM {money_from}),
        ps AS (
            SELECT nullif(s.project_name, '') AS project, coalesce(nullif(s.repository, ''), '') AS repository,
                   {fact_columns}
            FROM {facts_from}
        ),
        facts AS (
            SELECT project, count(*) AS session_count, sum(turns)::bigint AS turns,
                   sum(files_changed)::bigint AS files_changed, sum(lines_added)::bigint AS lines_added,
                   sum(lines_removed)::bigint AS lines_removed, sum(tool_calls)::bigint AS tool_calls,
                   sum(tool_calls_success)::bigint AS tool_calls_success,
                   array_agg(DISTINCT sub) AS subscription_types, array_agg(DISTINCT origin) AS project_origins,
                   count(DISTINCT repository)::bigint AS repository_count,
                   array_agg(session_id ORDER BY session_id {_C}) AS session_ids
            FROM ps GROUP BY project
        )
        SELECT a.project AS project_name, a.session_count, a.turns, a.files_changed, a.lines_added, a.lines_removed,
               a.tool_calls, a.tool_calls_success, a.subscription_types, a.project_origins,
               coalesce(m.cost_usd, 0) AS cost_usd, a.repository_count, a.session_ids
        FROM facts a
             LEFT JOIN money m ON m."grouping" = 'g_spend' AND m.project = coalesce(a.project, '')
        ORDER BY (a.project IS NULL), a.project {_C}"""  # noqa: S608 - fragments the reader built; no value is interpolated
