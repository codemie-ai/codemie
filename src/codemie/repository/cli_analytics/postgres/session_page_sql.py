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

from codemie.repository.cli_analytics.postgres.cost_key_sql import (
    _RATE_COLUMNS,
    kind_flags_from_groups_sql,
    merged_kind_flags_sql,
)
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


def cost_page_sql(ctes: str, stored: bool = False, keyed: bool = False) -> str:
    """The cost page: total, the page ids and the kind flags of the window, from one build of the token groups.
    `stored` (the CTEs carry `live` and the groups of `live` only): the flags of the other sessions come from their
    stored kind masks (`keyed`: `sel` carries them, `session_dims` is not joined)."""
    used = ", ".join(f"fl.used_{c}" for c in _RATE_COLUMNS)
    flags = merged_kind_flags_sql(kind_flags_from_groups_sql(), keyed) if stored else kind_flags_from_groups_sql()
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
                                '[]'::jsonb) FROM {flags}) AS flags"""


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


# The stored framework read: `lv` is the sessions of `filtered` whose stored names cannot be read (no stamp, or in S,
# `$stale`); their names are built set-wise like the temp table of the fallback, every other session reads
# `session_dims.classifier_names`. A session without a name has the empty set, like the fallback.
FRAMEWORK_STORED_NAMES = (
    "coalesce(CASE WHEN lv.session_id IS NULL THEN sd.classifier_names ELSE nm.names END, ARRAY[]::text[])"
)


def _framework_stored_from(ctes: str) -> str:
    return f"""
        WITH {ctes},
        lv AS (
            SELECT s.session_id, s.started_at FROM sel s JOIN session_dims sd ON sd.session_id = s.session_id
            WHERE sd.rates_version IS NULL OR s.session_id = ANY($stale::text[])
        )"""


def framework_stored_groups_sql(ctes: str) -> str:
    """The distinct classifier name sets of the filtered window with their session counts (the groups the rule is
    applied to)."""
    return f"""{_framework_stored_from(ctes)}
        SELECT g.names, count(*)::bigint AS n
        FROM (SELECT {FRAMEWORK_STORED_NAMES} AS names
              FROM filtered fw
                   JOIN session_dims sd ON sd.session_id = fw.session_id
                   LEFT JOIN lv ON lv.session_id = fw.session_id
                   LEFT JOIN {classifier_names_by_session_sql("lv")} ON nm.session_id = fw.session_id) g
        GROUP BY g.names"""


def framework_stored_page_sql(ctes: str) -> str:
    """The page of the sessions whose name set the rule accepted (`$matching`), in the order of the sort key."""
    return f"""{_framework_stored_from(ctes)}
        SELECT fw.session_id
        FROM filtered fw
             JOIN session_dims sd ON sd.session_id = fw.session_id
             LEFT JOIN lv ON lv.session_id = fw.session_id
             LEFT JOIN {classifier_names_by_session_sql("lv")} ON nm.session_id = fw.session_id
        WHERE to_jsonb({FRAMEWORK_STORED_NAMES}) IN (SELECT jsonb_array_elements($matching::jsonb))
        ORDER BY fw.sort_key DESC, fw.session_id {_C}
        LIMIT $limit::bigint OFFSET $offset::bigint"""


def ordered_page_sql(ctes: str) -> str:
    """A page of `filtered` by its key: the exact total and the page ids in order."""
    return f"""
        WITH {ctes},
        page AS (
            SELECT session_id, sort_key FROM filtered
            ORDER BY sort_key DESC, session_id {_C}
            LIMIT $limit::bigint OFFSET $offset::bigint
        )
        SELECT (SELECT count(*) FROM filtered)::bigint AS total,
               coalesce((SELECT array_agg(session_id ORDER BY sort_key DESC, session_id {_C}) FROM page),
                        ARRAY[]::text[]) AS session_ids"""


# ── the stored sort keys (EPMCDME-16427, part 2) ──

# The kill switch: one row; no row reads as off.
SORT_KEYS_ENABLED_SQL = "SELECT coalesce(bool_and(enabled), false) AS enabled FROM session_sort_keys_control"


# Today's ctx key (`reader._sort_clause`) over the stored totals of `session_dims sd`: cache reads (subagent totals
# included) per API call, rounded to 9 places, 0 without calls.
def stored_ctx_key(alias: str = "sd") -> str:
    """The stored ctx key over the columns of `alias`: `sd` (a join of `session_dims`) or `s` (the inputs `sel` carries,
    `_sessions_cte(with_keys=True)`)."""
    return (
        f"CASE WHEN coalesce({alias}.api_call_total, 0) = 0 THEN 0::numeric "
        f"ELSE round({alias}.cache_read_total::numeric / {alias}.api_call_total, 9) END"
    )


STORED_CTX_KEY = stored_ctx_key()


# Today's cost key over `session_dims sd`, valid only while the stamp is the reader's current rates version.
def stored_cost_key(alias: str = "sd") -> str:
    """The stored cost key over `alias`: `sd` (a join of `session_dims`) or `s` (`sel` carries it)."""
    return f"{alias}.cost_key"


STORED_COST_KEY = stored_cost_key()

# The stored columns `sel` carries out of the index-only scan of `session_dims_fw_win` (its INCLUDE list), by page.
CTX_KEY_COLUMNS = ("cache_read_total", "api_call_total")
COST_KEY_COLUMNS = ("cost_key", "models_used", "kind_masks")
FLAG_COLUMNS = ("models_used", "kind_masks")


def live_cte(only: str = "true") -> str:
    """The sessions of `sel` whose value is computed live: the ids of S (`$stale`) that `only` keeps. S is read in the
    SAME snapshot as the page (the reader runs both in one REPEATABLE READ transaction), so a session without a usable
    stamp is in S and no other is: this CTE does not look at `session_dims` again, which made every request scan the
    table and join it to the whole window even when S was empty. `only` is the request's own filter on `sel` (the
    search, the unattributed filter): a session it drops never reaches the page, so it is not computed."""
    return f"""
        live AS (
            SELECT s.session_id FROM sel s WHERE s.session_id = ANY($stale::text[]) AND ({only})
        )"""


LIVE_CTE = live_cte()


UNSTAMPED_CTX_SQL = "SELECT session_id FROM session_dims WHERE rates_version IS NULL LIMIT $s_limit::bigint"
# `<` and `>` instead of IS DISTINCT FROM: two index ranges of `session_dims_rates_version` that are empty when every
# session carries the current version, where IS DISTINCT FROM would read the whole table.
UNSTAMPED_COST_SQL = (
    "SELECT session_id FROM session_dims WHERE rates_version IS NULL OR rates_version < $rates_version::text "
    "OR rates_version > $rates_version::text LIMIT $s_limit::bigint"
)


def stored_cost_models_sql(sel: str) -> str:
    """The raw model names of the window for the cost sort's rate resolution: the stored `models_used` of every
    session of `sel` (which carries it) outside S, and the token rows of S (`$stale`), like the fallback statement.

    A session whose `models_used` is NULL in `sel` is read from its token rows too, in S or not: `sel` may be the
    request's temp table, built in an earlier snapshot than the one S is read in, and a session stamped between the
    two would otherwise be in neither arm, its models priced with no rates for that request."""
    return f"""
        WITH {sel},
        {LIVE_CTE},
        unread AS MATERIALIZED (
            SELECT session_id FROM live
            UNION SELECT s.session_id FROM sel s WHERE s.models_used IS NULL)
        SELECT m.model_name FROM (
            SELECT u.model_name
            FROM (SELECT s.models_used FROM sel s
                  WHERE s.models_used IS NOT NULL AND s.session_id NOT IN (SELECT session_id FROM live)
                  GROUP BY s.models_used) g, unnest(g.models_used) AS u(model_name)
            WHERE u.model_name IS NOT NULL
            UNION ALL SELECT c.model_name FROM cost_daily c
                 WHERE c.model_name IS NOT NULL AND session_id IN (SELECT session_id FROM unread)
            UNION ALL SELECT coalesce(u.model, '') FROM session_usage u
                 WHERE u.attrs->>'token_source' = 'subagent_usage' AND session_id IN (SELECT session_id FROM unread)
        ) m
        GROUP BY m.model_name ORDER BY m.model_name {_C}"""
