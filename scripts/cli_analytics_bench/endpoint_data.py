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

"""Synthetic data for the dashboard requests of EPMCDME-17051 and EPMCDME-17052. Writes to a SCRATCH database only.

The requests it feeds (GET /v1/analytics/cli-analytics/...):

    EPMCDME-17051: users?page=0&per_page=50, user-charts, cost
    EPMCDME-17052: overview, repositories?page=0&per_page=10, repositories?include_branches=true, efficiency

    PG_URL=postgresql://.../scratch poetry run python scripts/cli_analytics_bench/endpoint_data.py load --scratch-ok
    PG_URL=... poetry run python scripts/cli_analytics_bench/endpoint_data.py derive    # activity facts, then sort keys
    PG_URL=... poetry run python scripts/cli_analytics_bench/endpoint_data.py edge-cases # EPMCDME-17051 edge sessions
    PG_URL=... poetry run python scripts/cli_analytics_bench/endpoint_data.py report    # row counts and coverage

`load` defaults: 8,000 users, 200,000 sessions started in the last 30 days and 100,000 started 31 to 90 days ago. The
migrations are applied by the caller (`migrations.run_migrations`); this script never creates the schema. It refuses a
database whose `session_dims` is not empty unless `--append` is given, and it needs `--scratch-ok`.

Per session (chosen by a stable hash of the session id, so a rerun with the same ids gives the same shape):

    session_dims         user (power-law: some heavy users, every user at least once), repository and branch,
                         project, funding route (api_based, subscription, NULL, an unknown value), 5% personal-billed
    cost_daily           1 to 3 models, 12% resumed on a later day, 5% on a model without catalog rates, 2% in a tie
                         group (equal or one-token-apart costs); 3% have no cost row but a subagent-totals row
    lines_daily          60% changed lines, 10% zero-sum rows ("lines known"), 30% no row (lines unknown, NULL)
    turns_daily          97%;  active_time_daily 95% (5% unknown duration);  tool_facts_daily 85%
    session_files_daily  60% of sessions, 1 to 8 files each (the rest are "no file changed")
    invocations_hourly   tools with successes (kind 1), skills (2), agents (3), slash commands (4)
    session_skills       30% of the sessions

Not written: the raw tables (spans, log_events, hook_events, metric_points). The requests read the rollups above, so
sessions on the first or last day of a window may be admitted by the stored facts alone, where production would also
consult raw records. Run `derive` after `load`: it fills the activity facts and sort keys with the repository's own
backfills, as the maintenance job does.

`edge-cases` appends (and derives) the few sessions the generator above does not build and EPMCDME-17051 needs: sessions
with only subagent-totals rows, developers named only by a fallback `cost_daily` or `lines_daily` email, and two
unpriced models that differ only in case. Their ids start with `edge17051-`; a rerun adds nothing. `report` counts them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

import asyncpg

from codemie.repository.cli_analytics.postgres.maintenance import Partition, month_start, week_start
from volume_data import MODELS, SKILLS, _model_case, connect, derive  # the same folder: this script's own helpers

CHUNK = 20000
MONTHLY_TABLES = (
    "cost_daily",
    "lines_daily",
    "active_time_daily",
    "turns_daily",
    "tool_facts_daily",
    "session_files_daily",
)
TOOLS = ["Read", "Edit", "Bash", "Grep", "Write", "Glob"]
AGENTS = ["Explore", "general-purpose", "Plan"]
COMMANDS = ["reload-plugins", "superpowers:executing-plans", "commit", "review"]
UNPRICED_MODEL = "claude-not-in-the-catalog"
LINES_MODEL = MODELS[0][0]
EDGE_UNPRICED = ("Edge-Unpriced-Model", "edge-unpriced-model")  # unpriced, differ only in case
EDGE_PREFIX = "edge17051-"

_H = "('x' || substr(s.session_id, 1, 6))::bit(24)::int"  # a stable number per session (session ids are md5 hex)

# $1 tag, $2 now, $3 first ordinal, $4 users, $5 recent sessions, $6 last ordinal, $7 recent days, $8 older span days,
# $9 repositories. Ordinals 1..$5 are the recent period, the rest the older one; the first $4 ordinals are one per user.
_SESSIONS = """
INSERT INTO session_dims (session_id, started_at, last_event_at, ended_at, repository, branch, branch_dominant,
    repo_remote, project_name, developer_name, dev_name_max, jwt_email, user_email, owner_email, subscription_type,
    project_origin, funding_route, first_prompt, title, session_title, commands)
SELECT md5($1 || t.n), t.ts, least(t.ts + make_interval(mins => (random() * 600)::int), $2::timestamptz),
       CASE WHEN t.n % 20 <> 7 THEN least(t.ts + make_interval(mins => (random() * 600)::int), $2::timestamptz) END,
       'repo-' || t.r,
       t.branch, t.branch,
       'git@example.com:team/repo-' || t.r || '.git', 'project-' || (t.r % 40),
       'Dev ' || t.u, 'Dev ' || t.u, 'user' || t.u || '@example.com', 'user' || t.u || '@example.com',
       'user' || t.u || '@example.com',
       CASE WHEN t.n % 20 = 0 THEN 'personal' ELSE 'codemie' END,
       CASE WHEN t.n % 20 = 0 THEN 'personal' WHEN t.n % 7 = 0 THEN 'linked_folder' ELSE 'profile' END,
       CASE WHEN t.n % 100 < 50 THEN 'api_based' WHEN t.n % 100 < 80 THEN 'subscription'
            WHEN t.n % 100 < 97 THEN NULL ELSE 'legacy-value' END,
       CASE WHEN t.n % 5 = 0 THEN 'fix the failing test ' ELSE 'implement feature ' END || t.n || ' '
           || repeat('lorem ', 40),
       'session ' || t.n, 'session ' || t.n,
       CASE WHEN t.n % 20 = 3 THEN '["reload-plugins", "superpowers:executing-plans"]'::jsonb END
FROM (SELECT n, u, ts, (u * 13 + n % (1 + u % 5)) % $9::int AS r,
             CASE WHEN n % 10 < 6 THEN 'main' WHEN n % 10 < 9 THEN 'feature/f' || (n % 25)
                  ELSE 'fix/b' || (n % 9) END AS branch
      FROM (SELECT n,
                   CASE WHEN n <= $4::int THEN n - 1 ELSE floor($4::int * power(random(), 2))::int END AS u,
                   $2::timestamptz - make_interval(
                       secs => (CASE WHEN n <= $5::int THEN random() * $7::float8
                                     ELSE $7::float8 + random() * $8::float8 END) * 86400) AS ts
            FROM generate_series($3::int, $6::int) n) t0) t
RETURNING session_id"""

# 1 to 3 models per session; the tie group (2%) is one model and equal or one-token-apart tokens, no cache.
_COST = f"""
INSERT INTO cost_daily (day, session_id, user_email, model_name, query_source, cost_usd, input_tokens, output_tokens,
    cache_read_tokens, cache_creation_tokens, api_call_count, cache_creation_5m_tokens, cache_creation_1h_tokens)
SELECT least((s.started_at AT TIME ZONE 'UTC')::date, current_date), s.session_id, s.user_email,
       CASE WHEN {_H} % 50 = 0 THEN 'claude-4-5-haiku'
            ELSE {_model_case("(('x' || substr(md5(s.session_id || k.i), 1, 6))::bit(24)::int / 16777216.0)")} END,
       'main', 0,
       CASE WHEN {_H} % 50 = 0 THEN 100000 + {_H} % 2 ELSE (random() * 2e5)::bigint END,
       CASE WHEN {_H} % 50 = 0 THEN 20000 ELSE (random() * 5e4)::bigint END,
       CASE WHEN {_H} % 50 = 0 THEN 0 ELSE (random() * random() * 5e6)::bigint END,
       CASE WHEN {_H} % 50 = 0 THEN 0 ELSE (random() * 1e5)::bigint END,
       1 + (random() * 300)::bigint,
       CASE WHEN {_H} % 3 = 0 AND {_H} % 50 <> 0 THEN (random() * 5e4)::bigint END,
       CASE WHEN {_H} % 3 = 0 AND {_H} % 50 <> 0 THEN (random() * 2e4)::bigint END
FROM session_dims s
CROSS JOIN LATERAL generate_series(1, CASE WHEN {_H} % 50 = 0 THEN 1
                                           ELSE 1 + ({_H} % 3 = 0)::int + ({_H} % 7 = 0)::int END) k (i)
WHERE s.session_id = ANY ($1::text[]) AND {_H} % 33 <> 0
ON CONFLICT DO NOTHING"""

_RESUMED = f"""
INSERT INTO cost_daily (day, session_id, user_email, model_name, query_source, cost_usd, input_tokens, output_tokens,
    cache_read_tokens, cache_creation_tokens, api_call_count)
SELECT least((s.started_at AT TIME ZONE 'UTC')::date + 1 + {_H} % 3, current_date), s.session_id, s.user_email,
       'claude-4-5-haiku', 'main', 0, (random() * 1e5)::bigint, (random() * 2e4)::bigint, (random() * 1e6)::bigint,
       (random() * 5e4)::bigint, 1 + (random() * 100)::bigint
FROM session_dims s WHERE s.session_id = ANY ($1::text[]) AND {_H} % 33 <> 0 AND {_H} % 8 = 1
ON CONFLICT DO NOTHING"""

_SUBAGENT_ONLY = f"""
INSERT INTO session_usage (session_id, day, model, speed, inference_geo, scope_kind, scope_name, agent_id,
    input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, attrs)
SELECT s.session_id, least((s.started_at AT TIME ZONE 'UTC')::date, current_date), 'claude-4-5-haiku', '', '', '', '',
       'a1', (random() * 1e5)::bigint, (random() * 1e4)::bigint, (random() * 1e6)::bigint, (random() * 1e4)::bigint,
       '{{"token_source": "subagent_usage"}}'::jsonb
FROM session_dims s WHERE s.session_id = ANY ($1::text[]) AND {_H} % 33 = 0
ON CONFLICT DO NOTHING"""

# 60% changed lines, 10% zero-sum rows ("lines known"), 30% no row at all (lines unknown).
_LINES = f"""
INSERT INTO lines_daily (day, session_id, user_email, model_name, lines_added, lines_removed)
SELECT least((s.started_at AT TIME ZONE 'UTC')::date, current_date), s.session_id, s.user_email, '{LINES_MODEL}',
       CASE WHEN {_H} % 10 < 6 THEN 1 + (random() * 800)::bigint ELSE 0 END,
       CASE WHEN {_H} % 10 < 6 THEN (random() * 300)::bigint ELSE 0 END
FROM session_dims s WHERE s.session_id = ANY ($1::text[]) AND {_H} % 10 < 7
ON CONFLICT DO NOTHING"""

_TURNS = f"""
INSERT INTO turns_daily (day, session_id, turns)
SELECT least((s.started_at AT TIME ZONE 'UTC')::date + d.plus, current_date), s.session_id, 1 + (random() * 80)::bigint
FROM session_dims s
CROSS JOIN LATERAL (SELECT 0 AS plus UNION ALL SELECT 1 + {_H} % 3 WHERE {_H} % 8 = 1) d
WHERE s.session_id = ANY ($1::text[]) AND {_H} % 33 <> 0
ON CONFLICT DO NOTHING"""

_ACTIVE = f"""
INSERT INTO active_time_daily (day, session_id, user_email, active_ms_user, active_ms_cli)
SELECT least((s.started_at AT TIME ZONE 'UTC')::date, current_date), s.session_id, s.user_email,
       (random() * 3.6e6)::bigint, (random() * 1.8e6)::bigint
FROM session_dims s WHERE s.session_id = ANY ($1::text[]) AND {_H} % 20 <> 1
ON CONFLICT DO NOTHING"""

_TOOL_FACTS = f"""
INSERT INTO tool_facts_daily (day, session_id, tool_calls, agent_count, skill_count)
SELECT least((s.started_at AT TIME ZONE 'UTC')::date, current_date), s.session_id, 1 + (random() * 400)::bigint,
       (random() * 5)::bigint, (random() * 4)::bigint
FROM session_dims s WHERE s.session_id = ANY ($1::text[]) AND {_H} % 7 <> 2
ON CONFLICT DO NOTHING"""

# 60% of the sessions changed 1 to 8 files; the other 40% changed none (cost without a file: "dead" sessions).
_FILES = f"""
INSERT INTO session_files_daily (day, session_id, file_path, is_written, is_edited)
SELECT least((s.started_at AT TIME ZONE 'UTC')::date, current_date), s.session_id,
       'src/pkg' || (k.i % 5) || '/mod' || ({_H} % 50) || '_' || k.i || '.py', k.i % 2 = 0, k.i % 2 = 1 OR k.i % 3 = 0
FROM session_dims s CROSS JOIN LATERAL generate_series(1, 1 + {_H} % 8) k (i)
WHERE s.session_id = ANY ($1::text[]) AND {_H} % 5 < 3
ON CONFLICT DO NOTHING"""

# Tools (kind 1, with successes) for every session, skills (2) for 30%, agents (3) for 20%, slash commands (4) for 5%.
_INVOCATIONS = f"""
INSERT INTO invocations_hourly (hour, session_id, kind, name, calls, success)
SELECT date_trunc('hour', s.started_at) + interval '1 hour', s.session_id, 1, ($2::text[])[k.i], c.calls,
       greatest(0, c.calls - (random() * c.calls * 0.1)::int)
FROM session_dims s
CROSS JOIN LATERAL generate_series(1, 1 + {_H} % 3) k (i)
CROSS JOIN LATERAL (SELECT 1 + (random() * 40)::int AS calls) c
WHERE s.session_id = ANY ($1::text[])
UNION ALL
SELECT date_trunc('hour', s.started_at) + interval '1 hour', s.session_id, 2, ($3::text[])[1 + {_H} % $4::int], 1, 1
FROM session_dims s WHERE s.session_id = ANY ($1::text[]) AND {_H} % 100 < 30
UNION ALL
SELECT date_trunc('hour', s.started_at) + interval '1 hour', s.session_id, 3, ($5::text[])[1 + {_H} % $6::int],
       1 + (random() * 3)::int, 1
FROM session_dims s WHERE s.session_id = ANY ($1::text[]) AND {_H} % 100 BETWEEN 30 AND 49
UNION ALL
SELECT date_trunc('hour', s.started_at) + interval '1 hour', s.session_id, 4, ($7::text[])[1 + {_H} % $8::int], 1, 1
FROM session_dims s WHERE s.session_id = ANY ($1::text[]) AND {_H} % 100 BETWEEN 50 AND 54
ON CONFLICT DO NOTHING"""

_SKILL_ROWS = f"""
INSERT INTO session_skills (session_id, skill_name)
SELECT s.session_id, ($2::text[])[1 + (('x' || substr(md5(s.session_id || k.i), 1, 6))::bit(24)::int) % $3::int]
FROM session_dims s CROSS JOIN generate_series(1, 2) k (i)
WHERE s.session_id = ANY ($1::text[]) AND {_H} % 100 < 30 AND (k.i = 1 OR {_H} % 2 = 0)
ON CONFLICT DO NOTHING"""

TABLES = (
    "session_dims",
    "cost_daily",
    "session_usage",
    "lines_daily",
    "turns_daily",
    "active_time_daily",
    "tool_facts_daily",
    "session_files_daily",
    "invocations_hourly",
    "session_skills",
)


def _target(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.hostname}:{parts.port or 5432}{parts.path}"  # never the user or the password


async def make_partitions(conn: asyncpg.Connection, first: date, last: date) -> int:
    n, month = 0, month_start(first)
    while month <= last:
        for table in MONTHLY_TABLES:
            await conn.execute(Partition.monthly(table, month).create_sql())
            n += 1
        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
    monday = week_start(first - timedelta(days=7))
    while monday <= last + timedelta(days=7):
        await conn.execute(Partition.weekly("invocations_hourly", monday).create_sql())
        monday, n = monday + timedelta(days=7), n + 1
    return n


async def load(args: argparse.Namespace) -> dict:
    if not args.scratch_ok:
        raise SystemExit("refusing to load: pass --scratch-ok to confirm PG_URL is a SCRATCH database")
    conn = await connect()
    existing = int(await conn.fetchval("SELECT count(*) FROM session_dims"))
    if existing and not args.append:
        raise SystemExit(f"refusing to load: session_dims already has {existing} rows (use --append to add more)")
    total = args.recent_sessions + args.older_sessions
    now = datetime.now(timezone.utc).replace(microsecond=0)
    started = time.perf_counter()
    first_day = (now - timedelta(days=args.recent_days + args.older_days + 3)).date()
    await make_partitions(conn, first_day, now.date())
    tag = f"ep{existing}-"
    for offset in range(0, total, CHUNK):
        first, last = offset + 1, min(offset + CHUNK, total)
        async with conn.transaction():
            ids = [
                r[0]
                for r in await conn.fetch(
                    _SESSIONS,
                    tag,
                    now,
                    first,
                    args.users,
                    args.recent_sessions,
                    last,
                    float(args.recent_days),
                    float(args.older_days),
                    args.repos,
                )
            ]
            for sql in (_COST, _RESUMED, _SUBAGENT_ONLY, _LINES, _TURNS, _ACTIVE, _TOOL_FACTS, _FILES):
                await conn.execute(sql, ids)
            await conn.execute(
                _INVOCATIONS, ids, TOOLS[:6], SKILLS, len(SKILLS), AGENTS, len(AGENTS), COMMANDS, len(COMMANDS)
            )
            await conn.execute(_SKILL_ROWS, ids, SKILLS, len(SKILLS))
    await conn.execute("; ".join(f"ANALYZE {table}" for table in TABLES))
    out = {
        "target": _target(os.environ["PG_URL"]),
        "sessions_added": total,
        "seconds": round(time.perf_counter() - started, 1),
    }
    for table in TABLES:
        out[table + "_rows"] = await conn.fetchval(f"SELECT count(*) FROM {table}")
    await conn.close()
    return out


# The edge sessions of EPMCDME-17051, five days old, each with turns, tool facts and active time so the requests admit
# them. $1 now. `kind`: sub = only a subagent-totals session_usage row (no cost_daily row); cost = no email in
# session_dims, the developer comes from the cost_daily email; lines = no email in session_dims and no cost_daily row,
# the developer comes from the changed-lines email; case = two unpriced models that differ only in case.
_EDGE_SESSIONS = f"""
INSERT INTO session_dims (session_id, started_at, last_event_at, ended_at, repository, branch, branch_dominant,
    repo_remote, project_name, developer_name, dev_name_max, jwt_email, user_email, owner_email, subscription_type,
    project_origin, funding_route, first_prompt, title, session_title)
SELECT '{EDGE_PREFIX}' || k.kind || '-' || n, $1::timestamptz - interval '5 days',
       $1::timestamptz - interval '5 days' + interval '30 minutes',
       $1::timestamptz - interval '5 days' + interval '30 minutes', 'repo-edge', 'main', 'main',
       'git@example.com:team/repo-edge.git', 'project-edge',
       CASE WHEN k.has_email THEN k.email END, CASE WHEN k.has_email THEN k.email END,
       CASE WHEN k.has_email THEN k.email END, CASE WHEN k.has_email THEN k.email END,
       CASE WHEN k.has_email THEN k.email END, 'codemie', 'profile', 'api_based',
       'edge case ' || k.kind, 'edge ' || k.kind, 'edge ' || k.kind
FROM (VALUES ('sub', true, 'edge-subagent@example.com'), ('cost', false, NULL), ('lines', false, NULL),
             ('case', true, 'edge-case@example.com')) k (kind, has_email, email)
CROSS JOIN generate_series(1, 3) n
ON CONFLICT DO NOTHING"""

_EDGE_ROWS = (
    # subagent-totals only
    f"""INSERT INTO session_usage (session_id, day, model, speed, inference_geo, scope_kind, scope_name, agent_id,
    input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, attrs)
SELECT s.session_id, (s.started_at AT TIME ZONE 'UTC')::date, 'claude-4-5-haiku', '', '', '', '', 'a1',
       120000 + 1000 * n, 9000, 700000, 12000, '{{"token_source": "subagent_usage"}}'::jsonb
FROM session_dims s, generate_series(0, 0) n WHERE s.session_id LIKE '{EDGE_PREFIX}sub-%'
ON CONFLICT DO NOTHING""",
    # developer from the cost_daily email only
    f"""INSERT INTO cost_daily (day, session_id, user_email, model_name, query_source, cost_usd, input_tokens,
    output_tokens, cache_read_tokens, cache_creation_tokens, api_call_count)
SELECT (s.started_at AT TIME ZONE 'UTC')::date, s.session_id, 'edge-fallback-cost@example.com', 'claude-4-5-haiku',
       'main', 0, 150000, 15000, 900000, 20000, 40
FROM session_dims s WHERE s.session_id LIKE '{EDGE_PREFIX}cost-%'
ON CONFLICT DO NOTHING""",
    # two unpriced models differing only in case
    f"""INSERT INTO cost_daily (day, session_id, user_email, model_name, query_source, cost_usd, input_tokens,
    output_tokens, cache_read_tokens, cache_creation_tokens, api_call_count)
SELECT (s.started_at AT TIME ZONE 'UTC')::date, s.session_id, 'edge-case@example.com', m.name, 'main', 0,
       50000, 5000, 100000, 3000, 10
FROM session_dims s CROSS JOIN unnest($1::text[]) m (name) WHERE s.session_id LIKE '{EDGE_PREFIX}case-%'
ON CONFLICT DO NOTHING""",
    # developer from the changed-lines email only; the cost-fallback sessions also report lines under their own email
    f"""INSERT INTO lines_daily (day, session_id, user_email, model_name, lines_added, lines_removed)
SELECT (s.started_at AT TIME ZONE 'UTC')::date, s.session_id,
       CASE WHEN s.session_id LIKE '{EDGE_PREFIX}lines-%' THEN 'edge-fallback-lines@example.com'
            ELSE 'edge-fallback-cost@example.com' END, '{LINES_MODEL}', 40, 10
FROM session_dims s WHERE s.session_id LIKE '{EDGE_PREFIX}lines-%' OR s.session_id LIKE '{EDGE_PREFIX}cost-%'
ON CONFLICT DO NOTHING""",
    f"""INSERT INTO turns_daily (day, session_id, turns)
SELECT (s.started_at AT TIME ZONE 'UTC')::date, s.session_id, 7 FROM session_dims s
WHERE s.session_id LIKE '{EDGE_PREFIX}%' ON CONFLICT DO NOTHING""",
    f"""INSERT INTO tool_facts_daily (day, session_id, tool_calls, agent_count, skill_count)
SELECT (s.started_at AT TIME ZONE 'UTC')::date, s.session_id, 25, 1, 1 FROM session_dims s
WHERE s.session_id LIKE '{EDGE_PREFIX}%' ON CONFLICT DO NOTHING""",
    f"""INSERT INTO active_time_daily (day, session_id, user_email, active_ms_user, active_ms_cli)
SELECT (s.started_at AT TIME ZONE 'UTC')::date, s.session_id, coalesce(s.user_email, ''), 600000, 300000
FROM session_dims s WHERE s.session_id LIKE '{EDGE_PREFIX}%' ON CONFLICT DO NOTHING""",
)


async def edge_cases(args: argparse.Namespace) -> dict:
    """Append the EPMCDME-17051 edge sessions (idempotent) and derive their activity facts and sort keys."""
    if not args.scratch_ok:
        raise SystemExit("refusing to write: pass --scratch-ok to confirm PG_URL is a SCRATCH database")
    conn = await connect()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    async with conn.transaction():
        await make_partitions(conn, (now - timedelta(days=7)).date(), now.date())
        await conn.execute(_EDGE_SESSIONS, now)
        for sql in _EDGE_ROWS:
            if "$1::text[]" in sql:
                await conn.execute(sql, list(EDGE_UNPRICED))
            else:
                await conn.execute(sql)
    added = await conn.fetchval("SELECT count(*) FROM session_dims WHERE session_id LIKE $1", EDGE_PREFIX + "%")
    await conn.close()
    derived = await derive(argparse.Namespace(batch=args.batch))
    return {"edge_sessions": added, "derive": derived}


_COVERAGE = {
    "users": "SELECT count(DISTINCT user_email) FROM session_dims",
    "sessions_last_30_days": "SELECT count(*) FROM session_dims WHERE started_at >= now() - interval '30 days'",
    "sessions_31_to_90_days": "SELECT count(*) FROM session_dims WHERE started_at < now() - interval '30 days'",
    "personal_billed": "SELECT count(*) FROM session_dims WHERE subscription_type = 'personal'",
    "funding_api_based": "SELECT count(*) FROM session_dims WHERE funding_route = 'api_based'",
    "funding_subscription": "SELECT count(*) FROM session_dims WHERE funding_route = 'subscription'",
    "funding_null_or_other": "SELECT count(*) FROM session_dims WHERE funding_route IS NULL OR funding_route NOT IN "
    "('api_based', 'subscription', 'unknown')",
    "repositories": "SELECT count(DISTINCT repository) FROM session_dims",
    "repository_branch_pairs": "SELECT count(*) FROM (SELECT DISTINCT repository, branch FROM session_dims) x",
    "sessions_without_cost_row": "SELECT count(*) FROM session_dims s WHERE NOT EXISTS "
    "(SELECT 1 FROM cost_daily c WHERE c.session_id = s.session_id)",
    "sessions_on_unpriced_model": "SELECT count(DISTINCT session_id) FROM cost_daily WHERE model_name = '"
    + UNPRICED_MODEL
    + "'",
    "sessions_without_active_time": "SELECT count(*) FROM session_dims s WHERE NOT EXISTS "
    "(SELECT 1 FROM active_time_daily a WHERE a.session_id = s.session_id)",
    "sessions_with_files": "SELECT count(DISTINCT session_id) FROM session_files_daily",
    "sessions_with_zero_sum_lines": "SELECT count(*) FROM (SELECT session_id FROM lines_daily GROUP BY session_id "
    "HAVING sum(lines_added + lines_removed) = 0) x",
    "sessions_with_activity_facts": "SELECT count(*) FROM session_dims WHERE has_activity IS NOT NULL",
    # EPMCDME-17051 edge cases (`edge-cases` adds the ones the generator does not build)
    "sessions_with_only_subagent_totals": "SELECT count(*) FROM session_dims s WHERE EXISTS (SELECT 1 FROM "
    "session_usage u WHERE u.session_id = s.session_id AND u.attrs->>'token_source' = 'subagent_usage') "
    "AND NOT EXISTS (SELECT 1 FROM cost_daily c WHERE c.session_id = s.session_id)",
    "sessions_developer_from_fallback_cost_email": "SELECT count(*) FROM session_dims s WHERE "
    "coalesce(s.user_email, '') = '' AND EXISTS (SELECT 1 FROM cost_daily c WHERE c.session_id = s.session_id "
    "AND c.user_email <> '')",
    "developers_named_only_by_fallback_cost_email": "SELECT count(DISTINCT c.user_email) FROM session_dims s "
    "JOIN cost_daily c ON c.session_id = s.session_id WHERE coalesce(s.user_email, '') = '' AND c.user_email <> '' "
    "AND NOT EXISTS (SELECT 1 FROM session_dims o WHERE o.user_email = c.user_email)",
    "sessions_developer_from_fallback_lines_email": "SELECT count(*) FROM session_dims s WHERE "
    "coalesce(s.user_email, '') = '' AND NOT EXISTS (SELECT 1 FROM cost_daily c WHERE c.session_id = s.session_id "
    "AND c.user_email <> '') AND EXISTS (SELECT 1 FROM lines_daily l WHERE l.session_id = s.session_id "
    "AND l.user_email <> '' AND (l.lines_added <> 0 OR l.lines_removed <> 0))",
    "unpriced_model_names_differing_only_in_case": "SELECT count(*) FROM (SELECT lower(model_name) FROM cost_daily "
    "WHERE lower(model_name) = lower('" + EDGE_UNPRICED[0] + "') GROUP BY 1 HAVING count(DISTINCT model_name) > 1) x",
    "sessions_on_the_two_case_variants": "SELECT count(DISTINCT session_id) FROM cost_daily WHERE model_name IN ('"
    + "', '".join(EDGE_UNPRICED)
    + "')",
    "sessions_in_tie_group": "SELECT count(DISTINCT session_id) FROM cost_daily WHERE output_tokens = 20000 "
    "AND input_tokens IN (100000, 100001) AND cache_read_tokens = 0",
}


async def report(_: argparse.Namespace) -> dict:
    conn = await connect()
    out: dict = {"target": _target(os.environ["PG_URL"]), "rows": {}, "coverage": {}}
    for table in TABLES:
        out["rows"][table] = await conn.fetchval(f"SELECT count(*) FROM {table}")
    for name, sql in _COVERAGE.items():
        out["coverage"][name] = await conn.fetchval(sql)
    await conn.close()
    out["requests"] = [
        "EPMCDME-17051: /v1/analytics/cli-analytics/users?page=0&per_page=50, /user-charts, /cost",
        "EPMCDME-17052: /v1/analytics/cli-analytics/overview, /repositories?page=0&per_page=10, "
        "/repositories?include_branches=true, /efficiency",
    ]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["load", "derive", "report", "edge-cases"])
    parser.add_argument("--users", type=int, default=8000)
    parser.add_argument("--recent-sessions", type=int, default=200000, help="started in the last --recent-days")
    parser.add_argument("--older-sessions", type=int, default=100000, help="started before that, up to --older-days")
    parser.add_argument("--recent-days", type=int, default=30)
    parser.add_argument("--older-days", type=int, default=60, help="how many days the older sessions are spread over")
    parser.add_argument("--repos", type=int, default=400)
    parser.add_argument("--batch", type=int, default=1000, help="derive: sessions per backfill batch")
    parser.add_argument(
        "--scratch-ok", action="store_true", help="load, edge-cases: confirm PG_URL is a scratch database"
    )
    parser.add_argument("--append", action="store_true", help="load: add to a database that already has sessions")
    args = parser.parse_args()
    run = {"load": load, "derive": derive, "report": report, "edge-cases": edge_cases}[args.mode]
    print(json.dumps(asyncio.run(run(args)), indent=2, default=str))  # noqa: T201 - the recorded artifact


if __name__ == "__main__":
    main()
