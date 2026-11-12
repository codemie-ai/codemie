# Test data for the CLI analytics dashboard requests

`endpoint_data.py` fills a **scratch** Postgres with data for the requests in EPMCDME-17051 and EPMCDME-17052:
users, user-charts, cost, overview, repositories (paged and `include_branches=true`), efficiency.
Never point it at a shared or real database. `load` refuses without `--scratch-ok`.

## Default size

8,000 users, 300,000 sessions (200,000 in the last 30 days, 100,000 from 31 to 90 days ago), 400 repositories.
Edge cases are built in: unpriced model, personal billing, unknown duration, near-equal costs, sessions with no cost
row, zero-sum lines, sessions with no file changes. The full list is in the script docstring.

## Steps

1. Start a throwaway Postgres 17:

   ```
   docker run -d --name codemie-endpoint-data-scratch --shm-size=1g -p 127.0.0.1:55420:5432 \
     -e POSTGRES_PASSWORD=scratch -e POSTGRES_DB=scratch postgres:17 \
     -c max_wal_size=8GB -c checkpoint_timeout=15min -c shared_buffers=1GB -c maintenance_work_mem=512MB
   ```

2. Apply the migrations (the script never creates the schema). Build `AnalyticsPgSettings` with
   `dsn` and `migration_dsn` set to `postgresql://postgres:scratch@127.0.0.1:55420/scratch`,
   `schema="codemie_analytics"`, `iam_auth=False`, then call
   `codemie.repository.cli_analytics.postgres.migrations.run_migrations(settings)`.
   Wait until `pg_isready` accepts connections first.

3. Load, derive, check (from the repo root):

   ```
   export PG_URL=postgresql://postgres:scratch@127.0.0.1:55420/scratch
   poetry run python scripts/cli_analytics_bench/endpoint_data.py load --scratch-ok     # about the longest step
   poetry run python scripts/cli_analytics_bench/endpoint_data.py derive                # activity facts and sort keys
   poetry run python scripts/cli_analytics_bench/endpoint_data.py report                # row counts and coverage
   ```

   For EPMCDME-17051 also run `edge-cases --scratch-ok` once after `load` and `derive` (before `report`). It appends
   12 sessions (ids start with `edge17051-`; a rerun adds nothing) and derives their facts: sessions with only
   subagent-totals rows, developers named only by a fallback `cost_daily` or `lines_daily` email, and two unpriced
   models that differ only in case (`Edge-Unpriced-Model`, `edge-unpriced-model`). `report` counts each of them.

   For a quick trial add `--users 200 --recent-sessions 4000 --older-sessions 2000`.
   `--append` adds to a database that already has sessions.

4. Optional, to run the local app against it: set `CLI_ANALYTICS_PG_URL` for the app
   (it overrides `PG_URL` for CLI analytics only). With Docker compose use the host name
   `host.docker.internal` instead of `127.0.0.1`.

## Baseline and comparison for /users, /user-charts and /cost

`run_users_cost_bench.py` records the three outputs from the current handlers (`baseline`, once per commit to compare
against, before a handler changes) and checks a later run against them (`compare`, acceptance criteria 6-7). Its
output directory (`results/17051-baseline`) is not committed. See the docstring of the script for the commands.

## Expected row counts (default size)

session_dims 300,000 · cost_daily ~420k · session_usage ~9k · lines_daily ~210k · turns_daily ~327k ·
active_time_daily ~285k · tool_facts_daily ~257k · session_files_daily ~811k · invocations_hourly ~764k ·
session_skills ~132k.

## Known limits

- Only rollup tables are written, not the raw tables (spans, log_events, hook_events, metric_points). Sessions on the
  first or last day of a window may be admitted by stored facts alone, where production also checks raw records.
- `invocations_hourly` gets weekly partitions here. The app's maintenance job tries monthly ones and logs
  "would overlap partition" errors. They are harmless for reads, but expect them in the app log.
- Ids are derived from a stable hash, so a rerun with the same ids gives the same shape.

## Cleanup

`docker rm -f codemie-endpoint-data-scratch`
