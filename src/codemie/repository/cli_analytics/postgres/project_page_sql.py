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

"""SQL builder of the paging statement of the CLI Analytics project breakdown.

The database decides which sessions match the search, groups them by project, orders the projects by spend and cuts the
page. Only the sessions of the page's projects go back to the service, which prices and assembles them. The
unattributed project (NULL name) is not ranked, paged or counted: its sessions come back apart, and only for the last
page, so the service appends its row after the named ones. The cost key and the kind flags read one build of the
token groups (`g`), the same groups `get_session_model_costs` returns, so the order and the priced rows cannot disagree.
"""

from __future__ import annotations

from codemie.repository.cli_analytics.postgres.cost_key_sql import (
    _RATE_COLUMNS,
    cost_key_from_groups_sql,
    kind_flags_from_groups_sql,
    model_token_groups,
)

_C = 'COLLATE "C"'
# Drill-down (`include_repositories=false`): only named projects are ranked, paged and counted.
_PROJECT_ORDER = f"p.cost_key DESC, p.project_name {_C}"
# A session matches when its project contains the (lower-cased) term. strpos, not ILIKE, so `%` and `_` in the term
# are literal without escaping.
_SEARCH_CONDITION = "strpos(lower(s.project_name), $search::text) > 0"
# Legacy (default): the unattributed project is ranked last and counted, and a repository name matches too.
_LEGACY_PROJECT_ORDER = f"p.project_name IS NULL, p.cost_key DESC, p.project_name {_C}"
_LEGACY_SEARCH_CONDITION = (
    "(strpos(lower(s.project_name), $search::text) > 0 OR strpos(lower(s.repository), $search::text) > 0)"
)


def spend_groups_cte() -> str:
    """The (session, model) token groups of the window, built once for the cost key and the flags."""
    return f"g AS MATERIALIZED (SELECT * FROM {model_token_groups(normalized=False)})"


def project_page_sql(sel_ctes: str, searched: bool, drilldown: bool = False) -> str:
    """One statement: the number of matching named projects, the page's project names in order, the ids of the
    page's matching sessions, the ids of the unattributed sessions (only on the last page and never when
    `searched`; else empty), the ids of every matching session when `searched` (else NULL: the whole
    window) and the kind flags of the window's spend groups. Needs `$limit`, `$offset`, the `rates_params` and, when
    `searched`, `$search`. Without `drilldown` (the default) the statement is the legacy one: the unattributed project
    is ranked last and counted, a repository name matches the search, and no unattributed ids come apart."""
    order = _PROJECT_ORDER if drilldown else _LEGACY_PROJECT_ORDER
    condition = _SEARCH_CONDITION if drilldown else _LEGACY_SEARCH_CONDITION
    named_only = " WHERE project_name IS NOT NULL" if drilldown else ""
    page_join = "p.project_name = m.project_name" if drilldown else "p.project_name IS NOT DISTINCT FROM m.project_name"
    used = ", ".join(f"fl.used_{c}" for c in _RATE_COLUMNS)
    where = f"\n            WHERE {condition}" if searched else ""
    unattributed_ids = (
        "ARRAY[]::text[]"
        if searched or not drilldown
        else f"""coalesce((SELECT array_agg(m.session_id ORDER BY m.session_id {_C}) FROM matched m
                         WHERE m.project_name IS NULL
                           AND $offset::bigint + $limit::bigint >= (SELECT count(*) FROM projects)),
                        ARRAY[]::text[])"""
    )
    matched_ids = (
        f"coalesce((SELECT array_agg(m.session_id ORDER BY m.session_id {_C}) FROM matched m), ARRAY[]::text[])"
        if searched
        else "NULL::text[]"
    )
    return f"""
        WITH {sel_ctes},
        {spend_groups_cte()},
        matched AS (
            SELECT s.session_id, nullif(s.project_name, '') AS project_name,
                   coalesce(k.cost_key, 0::numeric) AS cost_key
            FROM sel s
                 LEFT JOIN {cost_key_from_groups_sql()} ON k.session_id = s.session_id{where}
        ),
        projects AS (
            SELECT project_name, sum(cost_key) AS cost_key FROM matched{named_only}
            GROUP BY project_name
        ),
        page AS (
            SELECT p.project_name, p.cost_key, row_number() OVER (ORDER BY {order}) AS position
            FROM projects p
            ORDER BY {order}
            LIMIT $limit::bigint OFFSET $offset::bigint
        )
        SELECT (SELECT count(*) FROM projects)::bigint AS total,
               coalesce((SELECT array_agg(p.project_name ORDER BY p.position) FROM page p), ARRAY[]::text[])
                   AS project_names,
               coalesce((SELECT array_agg(m.session_id ORDER BY m.session_id {_C})
                         FROM matched m JOIN page p ON {page_join}),
                        ARRAY[]::text[]) AS session_ids,
               {unattributed_ids} AS unattributed_session_ids,
               {matched_ids} AS matched_session_ids,
               (SELECT coalesce(jsonb_agg(jsonb_build_array(fl.model_name, {used}) ORDER BY fl.model_name {_C}),
                                '[]'::jsonb) FROM {kind_flags_from_groups_sql()}) AS flags"""  # noqa: S608 - trusted fragments built here; values are bound parameters
