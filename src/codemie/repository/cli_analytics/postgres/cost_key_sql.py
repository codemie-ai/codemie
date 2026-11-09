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
from codemie.repository.cli_analytics.scope import sql_personal_billed


def normalized_model_name(column: str) -> str:
    """Strip an optional leading LiteLLM Bedrock provider wrapper ('bedrock/'), a routing
    prefix ('converse/' or 'invoke/'), an optional region token plus 'anthropic.' provider
    prefix (e.g. 'us.anthropic.', 'us-gov-west-1.anthropic.', 'anthropic.'), and a trailing
    Bedrock inference-profile version tag ('-v1:0'-style) when it immediately follows an
    8-digit date stamp, e.g. '-20251001-v1:0'.
    CR-002: LiteLLM's own Bedrock provider convention nests an API-path marker behind the
    'bedrock/' provider prefix (e.g. 'bedrock/converse/...'); not yet observed in this
    telemetry source, but a documented real pattern, so the wrapper is stripped defensively.
    The routing-prefix and provider-prefix strips are case-insensitive (CR-001: a routing
    layer that emits a different case for these tokens must still normalize the same way).
    Never touches a date stamp on its own, a model's own version dash inside the id,
    or a trailing '-v\\d+:\\d+' shape that is not preceded by that date stamp.
    """
    without_routing_prefix = f"regexp_replace({column}, '^(bedrock/)?(converse|invoke)/', '', 'i')"
    without_prefix = f"regexp_replace({without_routing_prefix}, '^([a-z0-9-]+\\.)?anthropic\\.', '', 'i')"
    return f"regexp_replace({without_prefix}, '(\\d{{8}})-v\\d+:\\d+$', '\\1')"


# Spend is what the company or a project pays: the cost of a Personal-paid session (also one stored under a work
# project) stays out of every aggregate money value, while the session itself remains in `sel` as activity.
def _spend(alias: str) -> str:
    return f"{alias}.session_id NOT IN (SELECT session_id FROM sel WHERE {sql_personal_billed('sel')})"


_SEL_SESSIONS = ("c.session_id IN (SELECT session_id FROM sel)", "u.session_id IN (SELECT session_id FROM sel)")


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


def model_token_groups(spend_only: bool = False, normalized: bool = True) -> str:
    """THE token grouping: one row per (session, raw model) of the selected sessions, summed from `cost_daily` and
    the subagent totals-only rows of `session_usage` (see _token_rows), scoped by `sel`. Columns: session_id,
    model_name, normalized_model_name and the six token sums (the 5m / 1h sums stay NULL when no row has a split).
    `get_session_model_costs`, the cost key and the kind flags all read it, so they cannot disagree.
    `spend_only` leaves out Personal-paid sessions (see _spend). `normalized=False` leaves out the normalised name,
    which the cost key and the flags never read (three regexp passes per group on a million groups)."""
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
        FROM {_token_rows(*_SEL_SESSIONS)}
        WHERE true{f" AND {_spend('c')}" if spend_only else ""}
        GROUP BY c.session_id, c.model_name) g"""


# The priced token kinds of a `model_token_groups` row `g`, in `kind_tokens` order (input, output, cache read,
# cache write = 5m + unsplit, cache write 1h), with the same clamp: the split never prices more than the write it
# splits. The one SQL spelling of the kinds; the cost key and the flags both use it.
_CACHE_1H = "least(coalesce(g.cache_creation_1h_tokens, 0), g.cache_creation_tokens)"
_CACHE_5M = f"least(coalesce(g.cache_creation_5m_tokens, 0), g.cache_creation_tokens - {_CACHE_1H})"
_KIND_TOKENS = (
    "g.input_tokens",
    "g.output_tokens",
    "g.cache_read_tokens",
    f"({_CACHE_5M} + (g.cache_creation_tokens - {_CACHE_1H} - {_CACHE_5M}))",
    _CACHE_1H,
)
_RATE_COLUMNS = ("input", "output", "cache_read", "cache_write", "cache_write_1h")


def rates_params(rates: Sequence[ModelRates]) -> dict[str, Any]:
    """Bind parameters of `session_cost_key_sql`: one array per rate column (None = missing), one entry per model."""
    unique = list({r.model: r for r in rates}.values())
    params: dict[str, Any] = {"rate_models": [r.model for r in unique]}
    for column in _RATE_COLUMNS:
        params[f"rate_{column}"] = [None if getattr(r, column) is None else float(getattr(r, column)) for r in unique]
    return params


def _cost_key_terms() -> str:
    return " + ".join(
        f"CASE WHEN {tokens} > 0 THEN {tokens}::float8 * coalesce(r.{column}, 0) ELSE 0 END"
        for tokens, column in zip(_KIND_TOKENS, _RATE_COLUMNS, strict=True)
    )


_RATES_JOIN = (
    "LEFT JOIN unnest($rate_models::text[], "
    + ", ".join(f"$rate_{c}::float8[]" for c in _RATE_COLUMNS)
    + ") AS r(model_name, "
    + ", ".join(_RATE_COLUMNS)
    + ") ON r.model_name = g.model_name"
)
# The token groups of the window, computed once for the cost key and the kind flags of one statement.


def cost_groups_cte() -> str:
    return f"g AS MATERIALIZED (SELECT * FROM {model_token_groups(normalized=False)})"


def cost_key_from_groups_sql() -> str:
    """`session_cost_key_sql` over the CTE `cost_groups_cte()`: `k(session_id, cost_key)`, same terms and rates."""
    return (
        f"(SELECT g.session_id, round(sum({_cost_key_terms()})::numeric, 9) AS cost_key "
        f"FROM g {_RATES_JOIN} GROUP BY g.session_id) k"
    )


def kind_flags_from_groups_sql() -> str:
    """`model_flags_sql` over the CTE `cost_groups_cte()`: per raw model, `bool_or(kind tokens > 0)` of its
    (session, model) groups (after the clamp), as `fl(model_name, used_...)`. The T2 semantics, in the same
    statement as the key, so the groups are built once."""
    flags = ", ".join(
        f"bool_or({tokens} > 0) AS used_{column}" for tokens, column in zip(_KIND_TOKENS, _RATE_COLUMNS, strict=True)
    )
    return f"(SELECT g.model_name, {flags} FROM g GROUP BY g.model_name) fl"


def session_cost_key_sql(spend_only: bool = False) -> str:
    """The SQL twin of `CatalogPricer.price` summed per session, as a derived table `k(session_id, cost_key)`:
    the kind-to-rate table of `_KIND_TOKENS` / `_RATE_COLUMNS`, a kind with `tokens <= 0` skipped, a missing rate
    (or a model without rates) counting as 0. `cost_key` is `round(cost, 9)` as numeric. Needs `sel` and the
    `rates_params` of the models; rates are bound as float8."""
    return (
        f"(SELECT g.session_id, round(sum({_cost_key_terms()})::numeric, 9) AS cost_key "
        f"FROM {model_token_groups(spend_only)} {_RATES_JOIN} GROUP BY g.session_id) k"
    )


def model_flags_sql() -> str:
    """Per raw model, whether any (session, model) token group of the window used each priced kind
    (`bool_or(kind tokens > 0)`, the cache split clamped on the group's sums), as the derived table
    `fl(model_name, used_input, used_output, used_cache_read, used_cache_write, used_cache_write_1h)`. It reads the
    same `model_token_groups` as the cost key and `kind_flags_from_groups_sql`, so the flags and the key cannot
    disagree about a split (a clamp per raw row would). Used only where no page statement carries the flags (an
    empty page or window). The only other producer of the kind flags; needs `sel`."""
    flags = ", ".join(
        f"bool_or({tokens} > 0) AS used_{column}" for tokens, column in zip(_KIND_TOKENS, _RATE_COLUMNS, strict=True)
    )
    return f"(SELECT g.model_name, {flags} FROM {model_token_groups(normalized=False)} GROUP BY g.model_name) fl"
