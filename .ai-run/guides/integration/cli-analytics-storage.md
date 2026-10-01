# CLI Analytics Storage

OTel telemetry from coding agents (the sdlc-analytics plugin: Claude Code OTLP plus hook events) is stored by one of two adapters behind the same ports. The dashboards (`/v1/analytics/cli-analytics/*`) answer identically on both.

## Engine Choice

| `CLI_ANALYTICS_STORAGE_BACKEND` | Write path | Needs | Choose it when |
|---|---|---|---|
| `clickhouse` (default) | The router forwards OTLP and hook events to the OTel Collector, whose exporter writes ClickHouse; materialized views build the rollups | `CLICKHOUSE_*`, `ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT`, the `otelcollector` and `clickhouse` services | ClickHouse is already run, or far more than ~500 developers |
| `postgres` | The API decodes OTLP and writes PostgreSQL directly; a background job refreshes the rollups | a PostgreSQL database (the application's by default) | Fewer moving parts: no ClickHouse, no Collector |

Local stack without ClickHouse: set `CLI_ANALYTICS_STORAGE_BACKEND=postgres` in `.env` and start `docker compose up -d codemie` (it does not depend on `otelcollector` or `clickhouse`).

## Where Code Belongs

| Concern | Location |
|---|---|
| Ports (`CliAnalyticsReader`, `CliTelemetryIngestor`, `CliAnalyticsRuntime`) and ingest errors | `src/codemie/repository/cli_analytics/ports.py` |
| Backend selection (lazy, one instance) | `src/codemie/repository/cli_analytics/factory.py` |
| ClickHouse adapter | `src/codemie/repository/cli_analytics/clickhouse/` |
| PostgreSQL adapter (decode, ingest, rollups, maintenance, reader, runtime, backfill) | `src/codemie/repository/cli_analytics/postgres/` |
| PostgreSQL schema migrations (own Alembic environment and version table; run only through `run_migrations()`, so there is no `alembic.ini`: add a migration as a new file in `versions/` with `revision` and `down_revision` set) | `src/external/alembic_cli_analytics/` |
| Harness vocabulary (span, event and metric names to canonical kinds) | `src/codemie/repository/cli_analytics/vocabulary.py` |
| Background jobs and their lifecycle | `src/codemie/service/analytics/cli_analytics_jobs.py` |

| Avoid | Prefer |
|---|---|
| Importing `codemie.clients.clickhouse` or a PostgreSQL adapter module in the router or handler | `get_cli_analytics_storage().reader` / `.ingestor`; the handler takes a `CliAnalyticsReader` |
| A new reader query in one adapter only | Add it to the port and to both readers in the same change, with a test for each: both engines must return the same answers |
| Changing a response to fix one engine | Fix both engines; the API contract is shared (see design D1/D3/D5/D10/D11) |
| `logger.exception("... %s", x)` | f-strings with `exc_info` logging: the project formatter drops %-args |

## PostgreSQL Settings

| Variable | Default | Purpose |
|---|---|---|
| `CLI_ANALYTICS_PG_URL` | empty (application database) | A separate analytics database, as a libpq or SQLAlchemy URL. It is read once, as libpq reads it, and each driver's URL is rebuilt from that reading, fully percent-encoded: the pool (asyncpg), the migrations (SQLAlchemy and psycopg2) and IAM use the same user, host, port and database. `user`, `password`, `host`, `port` and `dbname` may be given as parameters where the URL leaves them out; a socket directory works as `?host=/path` or percent-encoded before `?`. A host or a socket directory is required: without one, asyncpg and libpq try different default hosts. `ssl=` becomes `sslmode=`. The migrations keep every libpq parameter; the pool keeps the ones asyncpg reads and the others are named in a warning. Refused, with a message that quotes nothing from the URL: a stricter security setting asyncpg cannot apply (`channel_binding=require`, `require_auth`, `sslcrldir`, `sslcertmode=require`, `gssencmode=require`, `requirepeer`), `hostaddr`, `service`, asyncpg's `database=` (in any case), a parameter name miscased or padded with spaces, a parameter without `=` or set twice, a connection part set both before `?` and as a parameter, a `#`, a `%` that is not an escape (or `%00`), a control character (a trailing newline), an `@` anywhere but before the host (write `user=alice%40corp.com`), an unescaped `/` or `?` in the user name or password, several hosts, a host that is not a name or an IP address (an IPv6 zone included), a port that is not a number, no host, and AWS IAM without a network host and a user (an AWS token is signed for them; GCP and Azure tokens are not, so a Cloud SQL socket works). The error is logged at startup and the analytics endpoints answer 503, as they do when a driver refuses its settings on connect or the database cannot be reached (network, TLS, timeout). `options`, `load_balance_hosts` and `replication` are left out of both connections; parameters not known to libpq are dropped and only counted in the warning. With IAM configured, a URL without a password authenticates with a token minted for its own host, port and user |
| `CLI_ANALYTICS_PG_SCHEMA` | `codemie_analytics` | Schema of all analytics tables |
| `CLI_ANALYTICS_PG_POOL_SIZE` | `8` | Dedicated pool per pod (never the application pool); each background job uses one connection |
| `CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS` | `30000` | Dashboard statements; the rollup refresh and maintenance get 5 minutes |
| `CLI_ANALYTICS_PG_INGEST_STATEMENT_TIMEOUT_MS` | `10000` | Each ingest transaction; a slower one answers 503 and the plugin retries |
| `CLI_ANALYTICS_PG_WORK_MEM` | `32MB` | Per analytics session |
| `CLI_ANALYTICS_PG_INGEST_ACQUIRE_TIMEOUT_MS` | `1000` | Ingest answers 503 (the plugin retries) instead of waiting past its 2 s timeout |
| `CLI_ANALYTICS_RAW_RETENTION_DAYS` | `90` | Raw rows; dashboard windows are clamped to it (on ClickHouse, to the schema's 90-day TTL) |
| `CLI_ANALYTICS_ROLLUP_RETENTION_DAYS` | `365` | Daily and hourly rollups, session dimensions; startup refuses a value below the raw retention |
| `CLI_ANALYTICS_DEDUP_RETENTION_DAYS` | `14` | Re-sent records are dropped this long after the record's day, or after first delivery for late records |
| `CLI_ANALYTICS_ROLLUP_REFRESH_SECONDS` | `30` | Dashboard freshness |
| `CLI_ANALYTICS_ROLLUP_BATCH_SIZE` | `5000` | Keys recomputed per transaction |
| `CLI_ANALYTICS_PARTITION_PREMAKE_WEEKS` | `4` | Partitions created ahead |
| `CLI_ANALYTICS_MAINTENANCE_INTERVAL_MINUTES` | `60` | Partitions, retention, DEFAULT-partition checks |

Analytics sessions run with `jit=off`: plans over every weekly partition cost past `jit_above_cost`, and JIT compilation added ~500 ms to a 3 ms query.

The analytics role needs DDL on its schema, DML on its tables and `TEMPORARY` on the database (PUBLIC has it by default; the refresher stages its keys in a temporary table). A DBA may create the schema for it; the role then needs no `CREATE` on the database.

The connection must be direct or session-pooled (PgBouncer `pool_mode = session`), never transaction-pooled. The session settings above are sent as startup parameters, the background jobs hold session-level advisory locks across transactions, and asyncpg caches prepared statements per connection. This applies to the application database too when `CLI_ANALYTICS_PG_URL` is empty.

## Window Semantics

On the PostgreSQL reader a session is in a date window if and only if `session_dims.started_at` is in `[start_dt, end_dt]` (inclusive, bounds truncated to ms). The predicate lives once, in `_sessions_cte` (`sel`); every windowed fact query scopes or inner-joins to `sel` and carries no day, timestamp or hour predicate, so an in-window session contributes all its data whatever the fact's own day, and a session that started before the window contributes nothing. Sessions without `started_at` are excluded. Session-detail and skill-name lookups keyed by `session_id` are not windowed.

The ClickHouse reader follows the same contract: `v_session_dimensions.started_at` in `[start_dt, end_dt]` (inclusive, `DateTime64(3)` binds) is decided once in `_sessions_cte` (`sel`), and no fact query carries its own day or timestamp predicate. Tool and invocation queries scan the raw `coding_agent_traces`/`coding_agent_logs` for the windowed sessions (no hourly rollup), and `get_users_last_active` reports each windowed session's latest hook event at or before `end_dt`. Sessions without dimensions drop out. Whole-session raw scans on these tool/invocation queries are not benchmarked (raw TTL 90 d).

The section 6.6 latency budget (825 ms per endpoint) is unverified for these whole-session scans: no 30-day, ~28.5K-session run was available locally.

## Sizing (Measured)

| Measurement | Result |
|---|---|
| Ingest through the full API (8 senders) | 113 requests/s, p99 ~320 ms |
| Ingest adapter at x1, final code (40 developers, 8 writers) | 30.0k records/s, p99 35.5 ms, no errors; 30-day endpoint medians 5-72 ms |
| Ingest adapter at x3 (120 developers, 8 writers) | 21.5k records/s, p99 51-62 ms, no errors |
| 30-day endpoints at x3 (8,221 sessions) | medians 15-285 ms, max 327 ms; `/overview` grows linearly, ~0.8 s projected at x12 (~500 developers) |
| Session detail | 14 ms median warm, ~140 ms on a new connection |
| Rollup refresh | ~505 session-days/s |
| Storage | ~480 B per raw record including rollups and the idempotency ledger |


## Operations

| Task | How |
|---|---|
| Schema | Migrated at startup in the background (the API does not wait); a failed migration is retried by the next refresh job. A pod waits up to 2 minutes for another pod's migration, but never at shutdown |
| Jobs | Rollup refresh and maintenance run on one pod at a time (advisory locks); `max_instances=1`, late runs are not dropped |
| Backfill or repair rollups | `python -m codemie.repository.cli_analytics.postgres.backfill --from YYYY-MM-DD --to YYYY-MM-DD [--now]`: re-derives the kind of rows stored before their name was mapped in `vocabulary.py`, then queues the range for the refresh job (`--now` rebuilds it in the command) |
| Alert on | WARNING `rollup queue has N keys, the oldest waiting Ns` (repeats every 5 min while stale); WARNING `recomputing rollup key ... timed out (N of 3 in a row); it is retried in N minutes`; ERROR `dropped rollup key`; ERROR `maintenance step ... failed`, `cannot create partition`, `purging expired rows of ... failed`; WARNING `rows outside every planned partition`; WARNING `dropped a telemetry record PostgreSQL rejects`; WARNING `a request holds more records PostgreSQL rejects than one attempt isolates`; ERROR `cannot drop expired partition`; ERROR `the configured analytics storage cannot be used` (startup) |
| Retention | Whole partitions are dropped; DEFAULT partitions, per-session tables and OTel resources no raw row has used for the raw retention are purged by date |
| Rejected data | A record PostgreSQL rejects is dropped, logged and entered in the ledger (a re-send skips it); the rest of its request is stored and the answer is 200 with OTLP `partial_success`. Past 64 failed attempts in one request the answer is 503 and the plugin's re-send carries on |
| Slow rollup keys | A batch past the 5-minute limit has its keys flagged `alone` in `rollup_dirty` and taken one at a time by any pod; a key timing out alone is retried after 10, then 20 minutes and dropped at the third timeout in a row |
| After a bulk load or backfill | Run `ANALYZE` on the analytics schema (or give autovacuum a few minutes): new partitions without planner statistics made a single-user overview 10x slower (192 vs 18 ms at x1) |
| Implausible values | Tokens above 10^9, cost beyond ±10^6 USD, spans over 30 days and counter readings adding more than 10^9 count as unparseable (0, the original kept in `attrs`), so no client can overflow the dashboards' sums |

## Testing

| Suite | Command |
|---|---|
| Unit tests (default gate) | `make test` |

They need no database. SQL, schema and engine-to-engine behaviour were verified against real PostgreSQL and ClickHouse servers (handoff, section 9.4).

Overview for the team (architecture, flows, measurements, open items): handoff `docs/superpowers/handoffs/2026-09-24-cli-analytics-postgres-storage-handoff.md`.

Evidence: design `docs/superpowers/specs/2026-09-23-cli-analytics-postgres-storage-design.md`; plan `docs/superpowers/plans/2026-09-23-cli-analytics-postgres-storage.md`; factory `src/codemie/repository/cli_analytics/factory.py`; runtime `src/codemie/repository/cli_analytics/postgres/runtime.py`.
