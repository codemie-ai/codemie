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

"""Transcript-to-OTel request pairing, inline slash-skill attribution and per-dispatch usage SQL.

The CTEs extend the reader's ``dispatch_fork`` and ``t`` (Skill spans) CTEs, bind ``$sid``, and expose
``paired_requests`` (every paired API request with the scope and agentId the plugin sent for it) and
``inline_usage`` for the final timeline projection.
"""

from codemie.repository.cli_analytics.postgres.rollups import (
    _MATCH,
    _MATCH_PARTIAL,
    _TIER_2_MATCH,
    _TIER_3_MATCH,
    _TIER_3_RANKING,
    _attr_ms,
    _candidates,
    _one_to_one,
    _resolved,
)
from codemie.repository.cli_analytics.vocabulary import SKILL_DISPATCH_EVENT, EventKind

_API_REQUEST = int(EventKind.API_REQUEST)
_C = 'COLLATE "C"'


# The OTel requests of a session paired with a transcript request (usage_requests), as `okey` (the row's identity)
# with the transcript request's `scope_kind`, `scope_name` and `agent_id`. The pairing is the rollups' own, run over
# the whole session: tier 1 (equal non-empty request ids, the n-th with the n-th), tier 2 (not both ids present, same
# raw model and totals, <= 5 s) and tier 3 (a partial positive-output snapshot, <= 30 s), the last two resolved one to
# one with the rollups' own walk (each transcript request takes its nearest free record). One `day` stands for the
# whole session, as no pair may be cut at midnight here.
_PAIRED_REQUESTS = f"""WITH RECURSIVE
            t AS MATERIALIZED (
                SELECT x.*, (x.ts AT TIME ZONE 'UTC')::date AS t_day,
                       row_number() OVER (PARTITION BY x.session_id ORDER BY x.ts, x.tkey) AS tid
                FROM (SELECT $sid::text AS session_id, date '2000-01-01' AS day,
                             '-infinity'::timestamptz AS lo, 'infinity'::timestamptz AS hi,
                             max(u.ts) AS ts, max(nullif(u.request_id, '')) AS request_id,
                             coalesce(u.model_raw, '') AS model_raw, max(u.input_tokens) AS input_tokens,
                             max(u.output_tokens) AS output_tokens, max(u.cache_read_tokens) AS cache_read_tokens,
                             max(u.cache_creation_5m_tokens) AS cache_5m, max(u.cache_creation_1h_tokens) AS cache_1h,
                             max(u.scope_kind) AS scope_kind, max(u.scope_name) AS scope_name,
                             max(u.agent_id) AS agent_id, max(u.agent_type) AS agent_type,
                             (max(nullif(u.stop_reason, '')) IS NULL AND NOT coalesce(bool_or(u.is_api_error), false))
                                 AS partial,
                             coalesce(nullif(u.request_id, ''), nullif(u.message_id, ''),
                                      u.tableoid::text || u.ctid::text) || '/' || coalesce(u.model_raw, '') AS tkey
                      FROM usage_requests u
                      WHERE u.session_id = $sid::text
                      GROUP BY coalesce(nullif(u.request_id, ''), nullif(u.message_id, ''),
                                        u.tableoid::text || u.ctid::text), coalesce(u.model_raw, '')) x
            ), o AS MATERIALIZED (
                SELECT $sid::text AS session_id, date '2000-01-01' AS day, '-infinity'::timestamptz AS lo,
                       'infinity'::timestamptz AS hi, l.tableoid::text || l.ctid::text AS okey, l.ts,
                       (l.ts AT TIME ZONE 'UTC')::date AS o_day, nullif(l.request_id, '') AS request_id, l.model,
                       l.query_source, nullif(l.attrs->>'agent.name', '') AS agent_name,
                       l.prompt_id, l.cost_usd, l.attrs,
                       l.input_tokens, l.output_tokens, l.cache_read_tokens, l.cache_creation_tokens,
                       row_number() OVER (ORDER BY l.ts, l.tableoid::text || l.ctid::text) AS oid
                FROM log_events l
                WHERE l.session_id = $sid::text AND l.event_kind = {_API_REQUEST}
            ), t1 AS (
                SELECT a.day, a.session_id, a.tid, b.oid
                FROM (SELECT day, session_id, request_id, tid,
                             row_number() OVER (PARTITION BY request_id ORDER BY tid) AS n
                      FROM t WHERE request_id IS NOT NULL) a
                JOIN (SELECT day, session_id, request_id, oid,
                             row_number() OVER (PARTITION BY request_id ORDER BY oid) AS n
                      FROM o WHERE request_id IS NOT NULL) b USING (day, session_id, request_id, n)
            ), {_candidates("c", _MATCH, _TIER_2_MATCH, "t1")},
            {_one_to_one("c", "")},
            p2 AS (
                SELECT day, session_id, tid, oid FROM t1
                UNION ALL
                {_resolved("")}
            ), {_candidates("c3", _MATCH_PARTIAL, _TIER_3_MATCH, "p2", _TIER_3_RANKING)},
            {_one_to_one("c3", "3", "d.totals_equal DESC, d.name_match DESC, d.gap, d.oid")},
            p AS (
                SELECT day, session_id, tid, oid FROM p2
                UNION ALL
                {_resolved("3")}
            )
            SELECT o.okey, o.ts, o.prompt_id, o.model, o.cost_usd, o.input_tokens, o.output_tokens, o.cache_read_tokens,
                   o.cache_creation_tokens, o.attrs, t.scope_kind, t.scope_name, coalesce(t.agent_id, '') AS agent_id
            FROM p
            JOIN t ON t.tid = p.tid
            JOIN o ON o.oid = p.oid"""


INLINE_SKILL_CTES = f"""paired_requests AS MATERIALIZED (
            {_PAIRED_REQUESTS}
        ), inline_attributed AS (
            -- the paired main-thread requests the plugin attributed to a skill
            SELECT okey, ts, prompt_id, model, cost_usd, input_tokens, output_tokens, cache_read_tokens,
                   cache_creation_tokens, attrs, scope_name AS skill_name
            FROM paired_requests
            WHERE scope_kind = 'skill' AND agent_id = ''
        ), inline_window AS (
            -- an inline slash dispatch's slice of its turn: to the next later start of the same skill in the same
            -- prompt, a dispatch or a Skill call, so repeated runs and a Skill call never share a request
            SELECT df.ts, df.skill_name, df.prompt_id,
                   least((SELECT min(d2.ts) FROM hook_events d2
                          WHERE d2.session_id = $sid::text AND d2.event_type = '{SKILL_DISPATCH_EVENT}'
                            AND d2.skill_name = df.skill_name AND d2.prompt_id = df.prompt_id AND d2.ts > df.ts),
                         (SELECT min(t.span_start) FROM t
                          WHERE t.skill_name = df.skill_name AND t.span_start > df.ts)) AS next_start
            FROM dispatch_fork df
            WHERE df.agent_id IS NULL AND coalesce(df.prompt_id, '') <> ''
        ), inline_matched AS (
            -- the main-thread API requests of an inline slash dispatch: its own prompt, from the dispatch to the next
            -- start of the same skill (another dispatch or a Skill call), that the plugin attributed to this skill
            -- (usage_requests scope_kind = skill) and the rollups' pairing matched (see inline_attributed). Read once
            -- here, so the sums and the per-model sums below never scan the session again per dispatch
            SELECT w.ts AS dispatch_ts, w.skill_name, w.prompt_id, x.ts, coalesce(x.model, '') AS model, x.cost_usd,
                   x.input_tokens, x.output_tokens, x.cache_read_tokens, x.cache_creation_tokens, x.attrs
            FROM inline_window w
            JOIN inline_attributed x ON x.skill_name = w.skill_name AND x.prompt_id = w.prompt_id AND x.ts >= w.ts
                                    AND (w.next_start IS NULL OR x.ts < w.next_start)
        ), inline_usage AS (
            SELECT g.dispatch_ts AS ts, g.skill_name, g.prompt_id, max(g.ts) AS last_ts,
                   sum(coalesce(g.cost_usd, 0)) AS cost_usd,
                   sum(coalesce(g.input_tokens, 0)) AS input_tokens,
                   sum(coalesce(g.output_tokens, 0)) AS output_tokens,
                   sum(coalesce(g.cache_read_tokens, 0)) AS cache_read_tokens,
                   sum(coalesce(g.cache_creation_tokens, 0)) AS cache_creation_tokens,
                   (SELECT jsonb_agg(m ORDER BY m.model_name {_C}) FROM (
                        SELECT r.model AS model_name,
                               sum(coalesce(r.input_tokens, 0)) AS input_tokens,
                               sum(coalesce(r.output_tokens, 0)) AS output_tokens,
                               sum(coalesce(r.cache_read_tokens, 0)) AS cache_read_tokens,
                               sum(coalesce(r.cache_creation_tokens, 0)) AS cache_creation_tokens,
                               sum({_attr_ms("r", "cache_creation_5m_tokens")}) AS cache_creation_5m_tokens,
                               sum({_attr_ms("r", "cache_creation_1h_tokens")}) AS cache_creation_1h_tokens
                        FROM inline_matched r
                        WHERE r.dispatch_ts = g.dispatch_ts AND r.skill_name = g.skill_name
                          AND r.prompt_id = g.prompt_id
                        GROUP BY 1) m) AS models
            FROM inline_matched g
            GROUP BY g.dispatch_ts, g.skill_name, g.prompt_id
        )"""
