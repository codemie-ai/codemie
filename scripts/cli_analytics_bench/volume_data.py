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

"""Synthetic volume data for the Sessions list benchmarks (EPMCDME-16427). Writes to a SCRATCH database only.

    PG_URL=postgresql://.../scratch poetry run python scripts/cli_analytics_bench/volume_data.py load --sessions 300000
    PG_URL=... poetry run python scripts/cli_analytics_bench/volume_data.py skew --sessions 100000
    PG_URL=... poetry run python scripts/cli_analytics_bench/volume_data.py derive    # activity facts, then sort keys

`load` creates the partitions, then inserts `--sessions` sessions started uniformly over `--days` days: `session_dims`
rows with NO activity facts and NO stored columns (as right after the migrations), `cost_daily` rows (1 to 3 models, a
second day for resumed sessions, 3% without cost rows but with a subagent-totals-only `session_usage` row), skills of
about 30% of the sessions, hook-dispatch names of 5%, a slash command of 5%. `skew` adds sessions started 100 to 119
days ago whose ctx_per_call key and cost are the highest of all (the top keys lie outside the 7/30/90 day windows).
`derive` runs the repository's own `backfill_session_activity` and `backfill_session_sort_keys` and prints their
duration and WAL. The migrations are applied by the caller (`migrations.run_migrations`); this script never creates
the schema.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import date, datetime, timedelta, timezone

import asyncpg

from codemie.repository.cli_analytics.postgres.maintenance import Partition, month_start, week_start

MODELS = [
    ("us.anthropic.claude-sonnet-4-5-20250929-v1:0", 45),
    ("claude-opus-4-6-20260205", 15),
    ("claude-4-5-haiku", 15),
    ("gpt-5-2025-08-07", 10),
    ("gemini-3-flash", 10),
    ("claude-not-in-the-catalog", 5),
]
SKILLS = [
    "superpowers:brainstorming",
    "superpowers:writing-plans",
    "sdlc-factory:sdlc-standard",
    "stlc-factory:suite-health",
    "bmad-pm",
    "opsx:apply",
    "speckit.plan",
    "commit",
    "review",
    "simplify",
    "init",
    "loop",
]
SCHEMA = "codemie_analytics"
CHUNK = 50000


def _model_case(expr: str) -> str:
    total, parts = sum(w for _, w in MODELS), []
    acc = 0
    for name, weight in MODELS:
        acc += weight
        parts.append(f"WHEN {expr} < {acc / total:.4f} THEN '{name}'")
    return "CASE " + " ".join(parts) + f" ELSE '{MODELS[-1][0]}' END"


async def connect() -> asyncpg.Connection:
    return await asyncpg.connect(
        os.environ["PG_URL"], server_settings={"search_path": SCHEMA}, command_timeout=7200, timeout=30
    )


async def make_partitions(conn: asyncpg.Connection, first: date, last: date) -> int:
    n, month = 0, month_start(first)
    while month <= last:
        await conn.execute(Partition.monthly("cost_daily", month).create_sql())
        month, n = (month.replace(day=28) + timedelta(days=4)).replace(day=1), n + 1
    monday = week_start(first - timedelta(days=7))
    while monday <= last + timedelta(days=7):
        await conn.execute(Partition.weekly("invocations_hourly", monday).create_sql())
        monday, n = monday + timedelta(days=7), n + 1
    return n


_H = "('x' || substr(s.session_id, 1, 6))::bit(24)::int"  # a stable number per session (session ids are md5 hex)

_SESSIONS = """
INSERT INTO session_dims (session_id, started_at, last_event_at, repository, branch, jwt_email, owner_email,
    subscription_type, project_origin, project_name, first_prompt, title, commands)
SELECT md5($1 || g), ts, ts + make_interval(mins => (random() * 600)::int), 'repo' || (g % 200), 'main',
       'u' || (g % 2000) || '@x.com', 'u' || (g % 2000) || '@x.com',
       CASE WHEN g % 20 = 0 THEN 'personal' ELSE 'codemie' END,
       CASE WHEN g % 20 = 0 THEN 'personal' WHEN g % 7 = 0 THEN 'linked_folder' ELSE 'profile' END,
       'p' || (g % 10),
       CASE WHEN g % 5 = 0 THEN 'fix the failing test ' ELSE 'implement feature ' END || g,
       'session ' || g,
       CASE WHEN g % 20 = 3 THEN '["reload-plugins", "superpowers:executing-plans"]'::jsonb END
FROM (SELECT g,
             $2::timestamptz - make_interval(secs => ($4::float8 + random() * ($5::float8 - $4::float8)) * 86400)
               AS ts
      FROM generate_series($3::int, $3::int + $6::int - 1) g) t
RETURNING session_id"""

# 1 to 3 models per session, priced as the catalog; 3% of the sessions (h % 33 = 0) get no cost row.
_COST = f"""
INSERT INTO cost_daily (day, session_id, user_email, model_name, query_source, cost_usd, input_tokens, output_tokens,
    cache_read_tokens, cache_creation_tokens, api_call_count, cache_creation_5m_tokens, cache_creation_1h_tokens)
SELECT least((s.started_at AT TIME ZONE 'UTC')::date, current_date), s.session_id, s.jwt_email,
       {_model_case("(('x' || substr(md5(s.session_id || k.i), 1, 6))::bit(24)::int / 16777216.0)")},
       'main', 0, (random() * 2e5)::bigint, (random() * 5e4)::bigint,
       (random() * random() * 5e6 * $2::float8)::bigint, (random() * 1e5)::bigint,
       1 + (random() * 300 / $2::float8)::bigint,
       CASE WHEN {_H} % 3 = 0 THEN (random() * 5e4)::bigint END,
       CASE WHEN {_H} % 3 = 0 THEN (random() * 2e4)::bigint END
FROM session_dims s
CROSS JOIN LATERAL generate_series(1, 1 + ({_H} % 3 = 0)::int + ({_H} % 7 = 0)::int) k (i)
WHERE s.session_id = ANY ($1::text[]) AND {_H} % 33 <> 0
ON CONFLICT DO NOTHING"""

# 12% of the sessions are resumed: a row on a later day.
_RESUMED = f"""
INSERT INTO cost_daily (day, session_id, user_email, model_name, query_source, cost_usd, input_tokens, output_tokens,
    cache_read_tokens, cache_creation_tokens, api_call_count)
SELECT least((s.started_at AT TIME ZONE 'UTC')::date + 1 + {_H} % 3, current_date), s.session_id, s.jwt_email,
       'claude-4-5-haiku', 'main', 0, (random() * 1e5)::bigint, (random() * 2e4)::bigint, (random() * 1e6)::bigint,
       (random() * 5e4)::bigint, 1 + (random() * 100)::bigint
FROM session_dims s WHERE s.session_id = ANY ($1::text[]) AND {_H} % 33 <> 0 AND {_H} % 8 = 1
ON CONFLICT DO NOTHING"""

_SUBAGENT = f"""
INSERT INTO session_usage (session_id, day, model, speed, inference_geo, scope_kind, scope_name, agent_id,
    input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, attrs)
SELECT s.session_id, least((s.started_at AT TIME ZONE 'UTC')::date, current_date), 'claude-4-5-haiku', '', '', '', '',
       'a1', (random() * 1e5)::bigint, (random() * 1e4)::bigint, (random() * 1e6)::bigint, (random() * 1e4)::bigint,
       '{{"token_source": "subagent_usage"}}'::jsonb
FROM session_dims s WHERE s.session_id = ANY ($1::text[]) AND {_H} % 33 = 0
ON CONFLICT DO NOTHING"""

# About 30% of the sessions have 1 or 2 skills, 5% a hook-dispatch name.
_SKILL_ROWS = f"""
INSERT INTO session_skills (session_id, skill_name)
SELECT s.session_id, ($2::text[])[1 + (('x' || substr(md5(s.session_id || k.i), 1, 6))::bit(24)::int) % $3::int]
FROM session_dims s CROSS JOIN generate_series(1, 2) k (i)
WHERE s.session_id = ANY ($1::text[]) AND {_H} % 100 < 30 AND (k.i = 1 OR {_H} % 2 = 0)
ON CONFLICT DO NOTHING"""

_HOOKS = f"""
INSERT INTO invocations_hourly (hour, session_id, kind, name, calls, success)
SELECT date_trunc('hour', s.started_at) + interval '1 hour', s.session_id, 2,
       ($2::text[])[1 + (('x' || substr(md5(s.session_id), 1, 6))::bit(24)::int) % $3::int], 1, 1
FROM session_dims s WHERE s.session_id = ANY ($1::text[]) AND {_H} % 100 BETWEEN 30 AND 34
ON CONFLICT DO NOTHING"""


async def load(args: argparse.Namespace, skew: bool) -> dict:
    conn = await connect()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    started = time.perf_counter()
    await make_partitions(conn, (now - timedelta(days=args.days + 2)).date(), now.date())
    base = int(await conn.fetchval("SELECT count(*) FROM session_dims"))
    low, high = (100.0, 119.0) if skew else (0.0, float(args.days))
    tag = "skew" if skew else "u"
    for offset in range(0, args.sessions, CHUNK):
        count = min(CHUNK, args.sessions - offset)
        async with conn.transaction():
            ids = [r[0] for r in await conn.fetch(_SESSIONS, tag, now, base + offset + 1, low, high, count)]
            await conn.execute(_COST, ids, 1000.0 if skew else 1.0)
            await conn.execute(_RESUMED, ids)
            await conn.execute(_SUBAGENT, ids)
            await conn.execute(_SKILL_ROWS, ids, SKILLS, len(SKILLS))
            await conn.execute(_HOOKS, ids, SKILLS[:4], 4)
    await conn.execute("ANALYZE session_dims; ANALYZE cost_daily; ANALYZE session_usage; ANALYZE session_skills")
    out = {"sessions_added": args.sessions, "skew": skew, "seconds": round(time.perf_counter() - started, 1)}
    for table in ("session_dims", "cost_daily", "session_usage", "session_skills", "invocations_hourly"):
        out[table + "_rows"] = await conn.fetchval(f"SELECT count(*) FROM {table}")
    await conn.close()
    return out


async def sizes(conn: asyncpg.Connection) -> dict:
    row = await conn.fetchrow(
        "SELECT pg_table_size('session_dims') AS table_b, pg_indexes_size('session_dims') AS indexes_b, "
        "pg_total_relation_size('session_dims') AS total_b"
    )
    return {k.removesuffix("_b") + "_mb": round(v / 1e6, 1) for k, v in dict(row).items()}


async def derive(args: argparse.Namespace) -> dict:
    """The repository's own backfills, as the maintenance job runs them: activity facts, then the stored sort keys."""
    from codemie.repository.cli_analytics.postgres.session_activity import backfill_session_activity
    from codemie.repository.cli_analytics.postgres.session_sort_keys import backfill_session_sort_keys
    from codemie.service.analytics.cli_analytics_jobs import model_rates_by_name
    from codemie.service.analytics.handlers.coding_agent_pricing import catalog_rate_fingerprint
    from codemie.repository.cli_analytics.postgres.rates_version import RatesVersionProvider

    conn = await connect()
    out: dict = {"session_dims_before": await sizes(conn)}
    wal0 = int(await conn.fetchval("SELECT pg_current_wal_lsn() - '0/0'::pg_lsn"))
    started = time.perf_counter()
    out["activity_derived"] = await backfill_session_activity(conn, args.batch, 36000, 600)
    out["activity_seconds"] = round(time.perf_counter() - started, 1)
    out["activity_wal_mb"] = round(
        (int(await conn.fetchval("SELECT pg_current_wal_lsn() - '0/0'::pg_lsn")) - wal0) / 1e6, 1
    )
    out["session_dims_after_activity"] = await sizes(conn)
    version = RatesVersionProvider(catalog_rate_fingerprint).current()
    result = await backfill_session_sort_keys(conn, model_rates_by_name, version, args.batch)
    out["sort_keys"] = result._asdict()
    out["sort_keys"]["wal_mb"] = round((result.wal_bytes or 0) / 1e6, 1)
    out["session_dims_after_sort_keys_before_vacuum"] = await sizes(conn)
    await conn.execute("VACUUM (ANALYZE) session_dims")
    out["session_dims_after_vacuum"] = await sizes(conn)
    await conn.close()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["load", "skew", "derive"])
    parser.add_argument("--sessions", type=int, default=300000)
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--batch", type=int, default=1000)
    args = parser.parse_args()
    result = asyncio.run(derive(args) if args.mode == "derive" else load(args, skew=args.mode == "skew"))
    print(json.dumps(result, indent=2, default=str))  # noqa: T201 - the recorded artifact


if __name__ == "__main__":
    main()
