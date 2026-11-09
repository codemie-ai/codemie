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

"""SQL builders of the paging statements of the CLI Analytics Sessions list.

Each builder takes the CTE text of the reader's order builder (`_ordered_window`: the CTEs `sel` and
`filtered(session_id, sort_key)`) and returns one statement; the reader binds the parameters and runs it. The
statements of the start_time page, the cost page and the framework-filter page live here, next to `cost_key_sql`, so
the reader keeps the order builder and the connection handling only.
"""

from __future__ import annotations

from codemie.repository.cli_analytics.postgres.cost_key_sql import _RATE_COLUMNS, kind_flags_from_groups_sql
from codemie.repository.cli_analytics.postgres.session_derived import classifier_names_by_session_sql

_C = 'COLLATE "C"'

# The framework filter: the temp table of the window's classifier names and the test of a session's name set
# against the sets the customer's rule accepts (`$matching` is their JSON array).
FRAMEWORK_NAMES_TABLE = "cli_framework_names"
FRAMEWORK_ACCEPTED = "to_jsonb(n.names) IN (SELECT jsonb_array_elements($matching::jsonb))"


def searched_start_time_page_sql(ctes: str) -> str:
    """A searched or filtered start_time page in one pass: the rows are counted by a window function and cut by a
    top-N sort."""
    return f"""
        WITH {ctes}
        SELECT session_id, count(*) OVER ()::bigint AS total FROM filtered
        ORDER BY sort_key DESC, session_id {_C}
        LIMIT $limit::bigint OFFSET $offset::bigint"""


def window_count_sql(ctes: str) -> str:
    """The exact number of sessions of `filtered`."""
    return f"WITH {ctes} SELECT count(*)::bigint AS total FROM filtered"


def start_time_page_sql(ctes: str) -> str:
    """The start_time page of the index-ordered walk: only the ids of the page, in order. Valid for the unfiltered
    list (`filtered` is `sel`). The first `page_end` rows of the started arm (`sel_main`, an ordered index walk that
    stops there) and every row of the other arms (`sel_rest`, few) hold the page, which is then cut from them."""
    return f"""
        WITH {ctes},
        candidates AS (
            (SELECT session_id, started_at FROM sel_main
             ORDER BY started_at DESC, session_id {_C} LIMIT ($limit::bigint + $offset::bigint))
            UNION ALL
            SELECT session_id, started_at FROM sel_rest
        )
        SELECT coalesce(array_agg(p.session_id ORDER BY p.started_at DESC, p.session_id {_C}), ARRAY[]::text[])
               AS session_ids
        FROM (SELECT session_id, started_at FROM candidates
              ORDER BY started_at DESC, session_id {_C}
              LIMIT $limit::bigint OFFSET $offset::bigint) p"""


def start_time_count_sql(ctes: str) -> str:
    """The exact number of sessions of the unfiltered list: one count per arm, so the started arm is an index-only
    count and nothing is appended row by row."""
    return f"""
        WITH {ctes}
        SELECT ((SELECT count(*) FROM sel_main) + (SELECT count(*) FROM sel_rest))::bigint AS total"""


def cost_page_sql(ctes: str) -> str:
    """The cost page: total, the page ids and the kind flags of the window, from one build of the token groups."""
    used = ", ".join(f"fl.used_{c}" for c in _RATE_COLUMNS)
    return f"""
        WITH {ctes},
        page AS (
            SELECT session_id, sort_key FROM filtered
            ORDER BY sort_key DESC, session_id {_C}
            LIMIT $limit::bigint OFFSET $offset::bigint
        )
        SELECT (SELECT count(*) FROM filtered)::bigint AS total,
               coalesce((SELECT array_agg(session_id ORDER BY sort_key DESC, session_id {_C}) FROM page),
                        ARRAY[]::text[]) AS session_ids,
               (SELECT coalesce(jsonb_agg(jsonb_build_array(fl.model_name, {used}) ORDER BY fl.model_name {_C}),
                                '[]'::jsonb) FROM {kind_flags_from_groups_sql()}) AS flags"""


def framework_flags_select(with_groups: bool) -> str:
    """The flags column of the framework page: the kind flags of the groups of the cost key, else NULL."""
    if not with_groups:
        return "NULL::jsonb"
    flag_columns = ", ".join(f"fl.used_{c}" for c in _RATE_COLUMNS)
    return (
        f"(SELECT coalesce(jsonb_agg(jsonb_build_array(fl.model_name, {flag_columns}) ORDER BY fl.model_name {_C}),"
        f" '[]'::jsonb) FROM {kind_flags_from_groups_sql()})"
    )


def framework_names_sql(names_ctes: str) -> str:
    """The temp table of the classifier names of every session of the filtered window."""
    return f"""
        CREATE TEMP TABLE {FRAMEWORK_NAMES_TABLE} ON COMMIT DROP AS
        WITH {names_ctes}
        SELECT fw.session_id, coalesce(nm.names, ARRAY[]::text[]) AS names
        FROM filtered fw
             LEFT JOIN {classifier_names_by_session_sql("sel")} ON nm.session_id = fw.session_id"""


def framework_page_sql(ctes: str, flags_select: str) -> str:
    """The page of the sessions whose name set the rule accepted, with the exact total and the flags."""
    return f"""
        WITH {ctes}
        SELECT fw.session_id, count(*) OVER ()::bigint AS total, {flags_select} AS flags
        FROM filtered fw JOIN {FRAMEWORK_NAMES_TABLE} n ON n.session_id = fw.session_id
        WHERE {FRAMEWORK_ACCEPTED}
        ORDER BY fw.sort_key DESC, fw.session_id {_C}
        LIMIT $limit::bigint OFFSET $offset::bigint"""


def framework_count_sql() -> str:
    """The exact total of the accepted sessions, for a page past the last one."""
    return f"SELECT count(*)::bigint AS total FROM {FRAMEWORK_NAMES_TABLE} n WHERE {FRAMEWORK_ACCEPTED}"
