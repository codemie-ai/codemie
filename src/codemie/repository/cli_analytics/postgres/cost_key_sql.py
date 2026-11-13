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

"""SQL builders of the per-session cost key and the priced-kind flags of the CLI Analytics Sessions list.

The token grouping (one row per session and raw model), the cost key that the `cost_usd` sort orders by (the SQL twin
of `CatalogPricer.price`) and the kind flags that decide which price columns the response shows all read the same
groups and the same kind-to-rate table, so they live together. The reader composes them into its statements; every
builder here needs the CTE `sel` (the selected sessions) of the statement it is placed in.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from codemie.repository.cli_analytics.ports import ModelRates
from codemie.repository.cli_analytics.postgres.kind_masks import KIND_BITS, KIND_TOKENS, RATE_COLUMNS
from codemie.repository.cli_analytics.scope import sql_personal_billed


def normalized_model_name(column: str) -> str:
    """First drop a trailing Bedrock version '-v1' with no colon that follows 'anthropic.claude-…' (a Bedrock id,
    case-insensitive), e.g. 'bedrock/eu.anthropic.claude-opus-4-6-v1' -> '…anthropic.claude-opus-4-6'; the colon form
    '-v1:0' keeps the date-stamp rule below. Then strip a LiteLLM route prefix ('bedrock/[<region>.]anthropic.',
    'vertex_ai/', 'anthropic/anthropic.', 'anthropic/', case-insensitive), then an optional leading LiteLLM Bedrock
    provider wrapper ('bedrock/'), a routing prefix ('converse/' or 'invoke/'), an optional region token plus
    'anthropic.' provider prefix (e.g. 'us.anthropic.', 'us-gov-west-1.anthropic.', 'anthropic.'), and a trailing
    Bedrock inference-profile version tag ('-v1:0'-style) when it immediately follows an
    8-digit date stamp, e.g. '-20251001-v1:0'. Last, Vertex's '@YYYYMMDD' becomes '-YYYYMMDD'
    (e.g. 'vertex_ai/claude-haiku-4-5@20251001' -> 'claude-haiku-4-5-20251001').
    LiteLLM's own Bedrock provider convention nests an API-path marker behind the
    'bedrock/' provider prefix (e.g. 'bedrock/converse/...'); not yet observed in this
    telemetry source, but a documented real pattern, so the wrapper is stripped defensively.
    The routing-prefix and provider-prefix strips are case-insensitive (a routing
    layer that emits a different case for these tokens must still normalize the same way).
    Never touches a date stamp on its own, a model's own version dash inside the id,
    or a trailing '-v\\d+:\\d+' shape that is not preceded by that date stamp.
    Dotted Claude versions are dashed last ('claude-sonnet-4.6' -> 'claude-sonnet-4-6', the form the catalog
    prices), case-insensitively, exactly as `normalize_model` does, so a dotted and a dashed spelling of one
    model are one group.
    """
    without_bedrock_version = f"regexp_replace({column}, '(anthropic\\.claude-[a-z0-9.-]*?)-v1$', '\\1', 'i')"
    without_route_prefix = (
        f"regexp_replace({without_bedrock_version}, "
        "'^(bedrock/([a-z0-9-]+\\.)?anthropic\\.|vertex_ai/|anthropic/(anthropic\\.)?)', '', 'i')"
    )
    without_routing_prefix = f"regexp_replace({without_route_prefix}, '^(bedrock/)?(converse|invoke)/', '', 'i')"
    without_prefix = f"regexp_replace({without_routing_prefix}, '^([a-z0-9-]+\\.)?anthropic\\.', '', 'i')"
    without_version_tag = f"regexp_replace({without_prefix}, '(\\d{{8}})-v\\d+:\\d+$', '\\1')"
    without_vertex_date = f"regexp_replace({without_version_tag}, '@(\\d{{8}})$', '-\\1')"
    return (
        f"CASE WHEN {without_vertex_date} ~* '^claude-' "
        f"THEN regexp_replace({without_vertex_date}, '(?<=\\d)\\.(?=\\d)', '-', 'g') ELSE {without_vertex_date} END"
    )


# Spend is what the company or a project pays: the cost of a Personal-paid session (also one stored under a work
# project) stays out of every aggregate money value, while the session itself remains in `sel` as activity.
def _spend(alias: str) -> str:
    return f"{alias}.session_id NOT IN (SELECT session_id FROM sel WHERE {sql_personal_billed('sel')})"


_SEL_SESSIONS = ("c.session_id IN (SELECT session_id FROM sel)", "u.session_id IN (SELECT session_id FROM sel)")


def _sessions_of(live: bool) -> tuple[str, str]:
    """The session conditions of the two token sources: `sel`, or with `live` only the CTE `live` (the sessions of a
    stored read whose values are computed by today's statement). `live` selects from `sel`, so it is the same scope;
    adding `sel` would make the planner hash the whole window and read every token row to find a handful."""
    if not live:
        return _SEL_SESSIONS
    return (
        "c.session_id IN (SELECT session_id FROM live)",
        "u.session_id IN (SELECT session_id FROM live)",
    )


def _token_rows(session_where: str, usage_where: str) -> str:
    """Token rows of the selected sessions: `cost_daily` plus the subagent totals-only rows of `session_usage`
    (attrs.token_source = 'subagent_usage'), which are priced at query time. The writer guarantees a totals row
    exists only when no request of that agent is kept, so nothing is counted twice. The 5m / 1h sums stay
    NULL when no row carries a split; only the four base counts are coalesced.

    Date, user, project, repository and branch filters need no copy here: both branches are scoped by
    `session_id IN (SELECT session_id FROM sel)`, and `sel` (see `_sessions_cte`) is where the window and
    every filter are decided."""
    return (
        "(SELECT t.session_id, t.model_name, t.input_tokens, t.output_tokens, t.cache_read_tokens,"
        " t.cache_creation_tokens, t.cache_creation_5m_tokens, t.cache_creation_1h_tokens FROM ("
        "SELECT c.session_id, c.model_name, c.input_tokens, c.output_tokens, c.cache_read_tokens,"
        " c.cache_creation_tokens, c.cache_creation_5m_tokens, c.cache_creation_1h_tokens"
        f" FROM cost_daily c WHERE {session_where}"
        " UNION ALL "
        "SELECT u.session_id, coalesce(u.model, ''), coalesce(u.input_tokens, 0), coalesce(u.output_tokens, 0),"
        " coalesce(u.cache_read_tokens, 0), coalesce(u.cache_creation_tokens, 0),"
        " u.cache_creation_5m_tokens, u.cache_creation_1h_tokens"
        f" FROM session_usage u WHERE u.attrs->>'token_source' = 'subagent_usage' AND {usage_where}"
        ") t) c"
    )


def model_token_groups(spend_only: bool = False, normalized: bool = True, live: bool = False) -> str:
    """THE token grouping: one row per (session, raw model) of the selected sessions, summed from `cost_daily` and
    the subagent totals-only rows of `session_usage` (see _token_rows), scoped by `sel`. Columns: session_id,
    model_name, normalized_model_name and the six token sums (the 5m / 1h sums stay NULL when no row has a split).
    `get_session_model_costs`, the cost key and the kind flags all read it, so they cannot disagree.
    `spend_only` leaves out Personal-paid sessions (see _spend). `normalized=False` leaves out the normalised name,
    which the cost key and the flags never read (three regexp passes per group on a million groups). `live` keeps only
    the sessions of the CTE `live` (a stored read)."""
    model_name = normalized_model_name("c.model_name")
    normalized_column = (
        f"\n               {model_name}                                 AS normalized_model_name," if normalized else ""
    )
    return f"""(SELECT c.session_id,
               c.model_name                                 AS model_name,{normalized_column}
               sum(c.input_tokens)::bigint                  AS input_tokens,
               sum(c.output_tokens)::bigint                 AS output_tokens,
               sum(c.cache_read_tokens)::bigint             AS cache_read_tokens,
               sum(c.cache_creation_tokens)::bigint         AS cache_creation_tokens,
               sum(c.cache_creation_5m_tokens)::bigint      AS cache_creation_5m_tokens,
               sum(c.cache_creation_1h_tokens)::bigint      AS cache_creation_1h_tokens
        FROM {_token_rows(*_sessions_of(live))}
        WHERE true{f" AND {_spend('c')}" if spend_only else ""}
        GROUP BY c.session_id, c.model_name) g"""


_RATE_COLUMNS = RATE_COLUMNS  # the name the reader and the page statements import


def rates_params(rates: Sequence[ModelRates]) -> dict[str, Any]:
    """Bind parameters of `session_cost_key_sql`: one array per rate column (None = missing), one entry per model."""
    unique = list({r.model: r for r in rates}.values())
    params: dict[str, Any] = {"rate_models": [r.model for r in unique]}
    for column in RATE_COLUMNS:
        params[f"rate_{column}"] = [None if getattr(r, column) is None else float(getattr(r, column)) for r in unique]
    return params


def _kind_term(tokens: str, column: str) -> str:
    return f"CASE WHEN {tokens} > 0 THEN {tokens}::float8 * coalesce(r.{column}, 0) ELSE 0 END"


def cost_terms() -> str:
    """The per-kind cost terms of one token group, summed; needs the alias `g` (token group) and `r` (rates)."""
    return " + ".join(_kind_term(tokens, column) for tokens, column in zip(KIND_TOKENS, RATE_COLUMNS, strict=True))


def cache_read_term() -> str:
    """The cache-read kind's term of `cost_terms()` alone."""
    return _kind_term(KIND_TOKENS[RATE_COLUMNS.index("cache_read")], "cache_read")


def unpriced_test() -> str:
    """Boolean: some kind with tokens > 0 has a NULL rate (a model without rates counts, all its rates are NULL)."""
    return (
        "("
        + " OR ".join(
            f"({tokens} > 0 AND r.{column} IS NULL)" for tokens, column in zip(KIND_TOKENS, RATE_COLUMNS, strict=True)
        )
        + ")"
    )


RATES_JOIN = (
    "LEFT JOIN unnest($rate_models::text[], "
    + ", ".join(f"$rate_{c}::float8[]" for c in RATE_COLUMNS)
    + ") AS r(model_name, "
    + ", ".join(RATE_COLUMNS)
    + ") ON r.model_name = g.model_name"
)
# The token groups of the window, computed once for the cost key and the kind flags of one statement.


def cost_groups_cte(live: bool = False) -> str:
    """`live`: only the groups of the sessions of the CTE `live` (the stored cost read prices the rest from
    `session_dims`)."""
    return f"g AS MATERIALIZED (SELECT * FROM {model_token_groups(normalized=False, live=live)})"


def cost_key_from_groups_sql() -> str:
    """`session_cost_key_sql` over the CTE `cost_groups_cte()`: `k(session_id, cost_key)`, same terms and rates."""
    return (
        f"(SELECT g.session_id, round(sum({cost_terms()})::numeric, 9) AS cost_key "
        f"FROM g {RATES_JOIN} GROUP BY g.session_id) k"
    )


def kind_flags_from_groups_sql() -> str:
    """`model_flags_sql` over the CTE `cost_groups_cte()`: per raw model, `bool_or(kind tokens > 0)` of its
    (session, model) groups (after the clamp), as `fl(model_name, used_...)`. The T2 semantics, in the same
    statement as the key, so the groups are built once."""
    flags = ", ".join(
        f"bool_or({tokens} > 0) AS used_{column}" for tokens, column in zip(KIND_TOKENS, RATE_COLUMNS, strict=True)
    )
    return f"(SELECT g.model_name, {flags} FROM g GROUP BY g.model_name) fl"


def session_cost_key_sql(spend_only: bool = False) -> str:
    """The SQL twin of `CatalogPricer.price` summed per session, as a derived table `k(session_id, cost_key)`:
    the kind-to-rate table of `KIND_TOKENS` / `RATE_COLUMNS`, a kind with `tokens <= 0` skipped, a missing rate
    (or a model without rates) counting as 0. `cost_key` is `round(cost, 9)` as numeric. Needs `sel` and the
    `rates_params` of the models; rates are bound as float8."""
    return (
        f"(SELECT g.session_id, round(sum({cost_terms()})::numeric, 9) AS cost_key "
        f"FROM {model_token_groups(spend_only)} {RATES_JOIN} GROUP BY g.session_id) k"
    )


def model_flags_sql(live: bool = False) -> str:
    """Per raw model, whether any (session, model) token group of the window used each priced kind
    (`bool_or(kind tokens > 0)`, the cache split clamped on the group's sums), as the derived table
    `fl(model_name, used_input, used_output, used_cache_read, used_cache_write, used_cache_write_1h)`. It reads the
    same `model_token_groups` as the cost key and `kind_flags_from_groups_sql`, so the flags and the key cannot
    disagree about a split (a clamp per raw row would). Used only where no page statement carries the flags (an
    empty page or window). The only other producer of the kind flags; needs `sel` (and `live` with `live`)."""
    flags = ", ".join(
        f"bool_or({tokens} > 0) AS used_{column}" for tokens, column in zip(KIND_TOKENS, RATE_COLUMNS, strict=True)
    )
    groups = model_token_groups(normalized=False, live=live)
    return f"(SELECT g.model_name, {flags} FROM {groups} GROUP BY g.model_name) fl"


def stored_kind_flags_sql(keyed: bool = False) -> str:
    """The kind flags of the sessions of `sel` outside the CTE `live`, from their stored `models_used` and
    `kind_masks` (one mask per model, the bits of `KIND_BITS`): the distinct `(models_used, kind_masks)` pairs are
    grouped first (a handful), then unnested per model, so the per-session token groups are never built. The mask is
    the `tokens > 0` test of the same groups, so the flags equal `model_flags_sql`. As `sfl(model_name, used_...)`.
    `keyed`: `sel` already carries `models_used` and `kind_masks` (from the covering index), so `session_dims` is not
    joined."""
    used = ", ".join(f"bool_or(m.mask & {KIND_BITS[c]} <> 0) AS used_{c}" for c in RATE_COLUMNS)
    source = "FROM sel s" if keyed else "FROM sel s JOIN session_dims sd ON sd.session_id = s.session_id"
    owner = "s" if keyed else "sd"
    return f"""(SELECT m.model_name, {used}
        FROM (SELECT {owner}.models_used, {owner}.kind_masks
              {source}
              WHERE {owner}.models_used IS NOT NULL AND s.session_id NOT IN (SELECT session_id FROM live)
              GROUP BY {owner}.models_used, {owner}.kind_masks) grp,
             unnest(grp.models_used, grp.kind_masks) AS m(model_name, mask)
        GROUP BY m.model_name) sfl"""


def merged_kind_flags_sql(live_flags: str, keyed: bool = False) -> str:
    """The kind flags of a stored read, as `fl(model_name, used_...)`: `live_flags` (today's flags of the sessions of
    `live`, a derived table of the same columns) or-ed per model with `stored_kind_flags_sql` (every other session)."""
    used = ", ".join(f"bool_or(u.used_{c}) AS used_{c}" for c in RATE_COLUMNS)
    return (
        f"(SELECT u.model_name, {used} FROM (SELECT * FROM {live_flags} "
        f"UNION ALL SELECT * FROM {stored_kind_flags_sql(keyed)}) u GROUP BY u.model_name) fl"
    )
