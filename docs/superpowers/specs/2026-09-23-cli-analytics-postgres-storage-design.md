# CLI Analytics storage: a PostgreSQL adapter next to ClickHouse

Investigation, design and implementation plan · 2026-09-23 · Status: implemented under EPMCDME-15253 on
branch `EPMCDME-15253_cli-analytics-postgres-storage` (not merged); the deviations adopted while implementing
are listed in §6.11 and await the owner's sign-off

Scope: the OTel CLI Analytics pipeline (`/v1/analytics/cli-analytics/*`). The API contract, the
codemie-ui tab and the sdlc-analytics plugin stay unchanged. Everything in this document was
measured on a proof of concept (PoC) that ran the production handler code against both engines.
The PoC scripts were not kept once the implementation landed: §7 records their results, and the
implemented system is described, with its own measurements, in the [handoff](../handoffs/2026-09-24-cli-analytics-postgres-storage-handoff.md).

---

## 1. Decision

Put analytics storage behind two ports and ship two adapters, selected by one setting
(`CLI_ANALYTICS_STORAGE_BACKEND=clickhouse|postgres`, default `clickhouse`):

- **Read port** = the 27 fact queries that `LocalAnalyticsHandler` already calls. The handler, the
  router and the response models stay shared; each adapter answers the same queries with the same
  row shapes.
- **Write port** = "accept an OTLP body" and "accept a batch of hook events". The ClickHouse adapter
  keeps forwarding to the OTel Collector exactly as today. The PostgreSQL adapter decodes OTLP in
  the API and writes typed rows directly — there is no production-ready PostgreSQL exporter for
  the Collector (the contrib donation, issue #46501, is open with "Sponsor Needed").

The PostgreSQL adapter targets vanilla PostgreSQL 14+ with no extensions (validated on 17.11; the
features it uses need 14+), so it runs on Amazon RDS/Aurora, Azure Database for PostgreSQL Flexible
Server, Cloud SQL and AlloyDB. It stores typed, weekly-partitioned raw tables and keeps the same
rollup grain as the ClickHouse materialized views, refreshed by a background job instead of by
triggers.

What the PoC showed (details in §7):

| Question | Result |
| --- | --- |
| Same API output? | 381 endpoint scenarios through the unchanged handler and response models. After adding deterministic tie-breakers (the current code has none), 379/381 are byte-identical; the other 2 differ only in the order of two dispatch rows that share a timestamp. Without tie-breakers, every remaining difference traces to nondeterminism that already exists in the ClickHouse implementation. |
| Fast enough? | At ~480 users (×12 data, 28,524 sessions in 30 days) on a 2-vCPU Docker VM with `work_mem=32MB`, every endpoint answered in ≤ 202 ms for a 7-day window and ≤ 825 ms for a 30-day window (median of 3). Session detail: 25–34 ms. |
| Ingest latency? | p99 17–26 ms per request on one connection, against a 2 s client timeout in the plugin. 8 concurrent writers: 701 requests/s, 38,843 records/s, p99 32.7 ms. |
| Storage cost? | ~406 bytes per raw record in PostgreSQL (with indexes) vs ~23 in ClickHouse at ×12; with rollups and the idempotency ledger that is ~42 MB vs ~2 MB per active user at the PoC's volume (≈21×). A ClickHouse-style generic JSONB layout would cost ~1,080 bytes per record. |
| Exactly-once? | Replayed and "grown" batches (the plugin re-sends a growing batch after any failure) are stored once. ClickHouse double-counts them today. After concurrent ingest, incrementally maintained rollups equal a full recompute from raw (0 missing, 0 unexpected rows). |

What this costs us, stated plainly:

- Storage is about 20× larger than ClickHouse, and PostgreSQL cannot compress rows of this size at
  all (§5.2). At an assumed 500 users that is ~21 GB instead of ~1 GB (§7.5).
- Rollups become eventually consistent: a new event shows up in dashboards after the next refresh
  (default every 30 s), where ClickHouse materialized views are synchronous. The UI already caches
  responses for 5 minutes (`Cache-Control: private, max-age=300`, `cli_analytics.py:385`).
- Two implementations of 27 queries have to be kept in step. A shared contract test suite
  (the parity suite, §9 Phase 3) is the only thing that makes that safe.
- PostgreSQL gives the dashboards what they need; it does not give the ad-hoc, scan-everything
  analytics headroom ClickHouse has. Deployments that already run ClickHouse (e.g. for Langfuse)
  should keep using it.

---

## 2. How the pipeline works today

### 2.1 Data flow

```
Claude Code (OTLP http/protobuf, 60 s export)      plugin hooks (13 hook types, NDJSON)
        │                                                   │
        └──────────── local proxy (127.0.0.1:48318): spools per session, flushes every 20 s,
                      retries every 20 s forever on any non-2xx except 401/403
                                         │   cookie auth
                                         ▼
   CodeMie API  POST /v1/analytics/cli-analytics/{logs,metrics,traces}   → raw OTLP bytes forwarded
                POST /v1/analytics/cli-analytics/event-hooks              → NDJSON → OTLP/JSON logs
                                         │  _forward(): 3 attempts, 503 when collector is down
                                         ▼
   OTel Collector 0.105 (batch 1000/5 s, retry, file-backed queue)
                                         │  clickhouse exporter
                                         ▼
   ClickHouse codemie_analytics
     raw:      coding_agent_logs · coding_agent_traces · coding_agent_metrics_sum   (TTL 90 d)
     8 MVs →   hook_events · cost_daily · lines_daily · active_time_daily · turns_daily
               file_facts_daily · session_dims · session_identity                   (rollups 365 d)
     views:    v_session_dimensions · v_session_email
                                         │  ch_query() via clickhouse-connect
                                         ▼
   LocalAnalyticsRepository (27 queries) → LocalAnalyticsHandler (business logic, pricing)
                                         → 11 GET endpoints → codemie-ui "CLI Analytics" tab
```

### 2.2 Where ClickHouse is coupled into the code

The coupling is narrower than the feature size suggests — one client, one repository class, one
router module and the deployment files:

| Location | What is ClickHouse-specific |
| --- | --- |
| `src/codemie/clients/clickhouse.py:26-45` | `get_client()` / `ch_query()`; hard-coded database `codemie_analytics` |
| `src/codemie/repository/cli_analytics_repository.py` | all 27 queries: ClickHouse SQL, `{name:Type}` parameters, `argMax`, `uniqExact`, `groupUniqArray`, `Map[...]`, the two views |
| `src/codemie/rest_api/routers/cli_analytics.py:106` | `_repository = LocalAnalyticsRepository(ch_query)` — module-level wiring |
| `src/codemie/rest_api/routers/cli_analytics.py:142-268` | `_forward()` to the Collector, `_event_to_log_record()`, `_build_otlp_logs_payload()` |
| `src/codemie/rest_api/routers/cli_analytics.py:393-457` | 4 ingest endpoints (storage-agnostic parsing mixed with forwarding) |
| `src/codemie/configs/config.py:91-95, 202-203` | `CLICKHOUSE_*`, `ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT`, `ANALYTICS_INGEST_MAX_BODY_BYTES` |
| `config/clickhouse/schema.sql`, `schema.replicated.sql`, `config/otel/config.yaml`, `docker-compose.yml:187-236` | schema, MVs, collector, local stack |

`LocalAnalyticsHandler` (`src/codemie/service/analytics/handlers/cli_analytics_handler.py`) does not
know ClickHouse exists: it only calls repository methods and coerces values with `_i/_f/_s`. That
is the seam this design uses. The in-flight branch `EPMCDME-14834_cursor-ide-otlp-integration`
adds Cursor by widening `SpanName IN (...)` / `MetricName IN (...)` lists in both the MVs and the
repository; §6.7 shows how the PostgreSQL adapter absorbs that as data.

### 2.3 Contracts that must not move

From the plugin (read at `codemie-public-skills/ai-packages/sdlc-analytics`):

- Endpoints and bodies: `POST .../{logs,metrics,traces}` with `application/x-protobuf` (the proxy
  decompresses and **concatenates** OTLP exports, so one body can hold several sessions), and
  `.../event-hooks` with `application/x-ndjson`. Auth is a cookie. Only the status class is read.
- Delivery is at-least-once with no event id. After any non-2xx other than 401/403 the proxy keeps
  the spool file, **appends new lines** and re-sends the whole file every 20 s, forever
  (`proxy.mjs:525-599`). A body hash therefore cannot detect duplicates — records can.
- Latency matters: the proxy holds a per-session lock while it POSTs; the hook's `curl --max-time 2`
  that waits on that lock gives up after 2 s and then drops non-critical events for 30 minutes
  (`common.mjs:100-116`). Ingest must stay well under a second at p99.
- Hook payloads: every value is a string; `prompt_body` ≤ 200 chars, `tool_input`/`tool_output`
  ≤ 300 UTF-16 units (a cut can split an emoji into a lone surrogate); `error_message` is uncapped;
  an escaped `\u0000` survives sanitisation. PostgreSQL `text`/`jsonb` reject both NUL and lone
  surrogates.
- Claude Code settings written by the plugin: `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`, 60 s
  export intervals, `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1`, `OTEL_LOG_TOOL_DETAILS=1`, default
  (delta) metric temporality.

From the UI (read at `codemie-ui/src`): timestamps must be ISO with a `T`; `day` must be
`YYYY-MM-DD`; money and counts must be JSON numbers (a string cost crashes the page); `repository`
`null` and `""` render differently; the UI relies on backend ordering and truncates several lists
to the first 10–20 items; `/repositories` without paging must return every row with a
`pagination` object; the heatmap is Monday-first. None of this changes if the adapter returns the
same Python types the handler gets from ClickHouse today, which the PoC adapter does.

### 2.4 Defects found in the current implementation

The parity work surfaced these. They exist today with ClickHouse; the PostgreSQL adapter either
fixes them or must reproduce them deliberately.

| # | Defect | Effect today | Where |
| --- | --- | --- | --- |
| D1 | No tie-breakers on `ORDER BY count DESC` / `ORDER BY span_start`, and several fact queries have no `ORDER BY` at all while the handler consumes rows in order | Top-N lists, tool tables and agent labels ("agent · Explore #2") can reorder between requests | repository `:435`, `:719`, `:883`; handler `:785` |
| D2 | Sessions without plugin data get `start_time = "1970-01-01T00:00:00"` (ClickHouse `LEFT JOIN` default, `join_use_nulls=0`) | The "Unattributed" list sorts on a constant, so pages repeat or skip sessions; UI shows "Jan 01" | repository `:537-577`, `:611-635` |
| D3 | A repository's `project_name` is taken from whichever session row arrives first | 12 of 30 repositories in the PoC data span several projects; the label is arbitrary | handler `:413`, `:432-433` |
| D4 | `argMax(model, api_call_count)` reads unmerged `SummingMergeTree` parts | The "top model" can change after a background merge | repository `:228`, `:562`, `:640` |
| D5 | Project admins with no administered project get the filter value `"\x00__no_project_access__"` | Works on ClickHouse; PostgreSQL rejects NUL in a parameter, so every endpoint would return 500 for these users | router `:358` |
| D6 | Duplicate deliveries are stored twice | Retries after a timeout inflate cost, tokens and counts | Collector → ClickHouse path has no record identity |
| D7 | Bodies above `ANALYTICS_INGEST_MAX_BODY_BYTES` (5 MB) return 413; the proxy retries a growing body forever | After a long outage a session's spool can never drain | router `:397-440`, plugin proxy |
| D8 | Per-session fan-out: `/overview`, `/users`, `/repositories`, `/sessions` pull one row per session per fact query and merge in Python | Response time grows with sessions in the window on both engines (28.5K sessions in a 30-day window in the scale test) | handler `:159-561` |
| D9 | An unpaired surrogate in hook text becomes `\udXXX` in the OTLP/JSON the API builds | By my reading of json-iterator the Collector writes U+FFFD; Python's `json_format` rejects the same body. Not verified against a running Collector | router `:237-268` |
| D10 | `get_skill_names_by_session` sends every session id of the window as one HTTP form parameter | ClickHouse's default `http_max_field_value_size` is 131,072 bytes: measured, the query works with 3,242 ids and fails with 3,281. `/sessions` returns 500 whenever the selected window holds more than ~3,250 priced sessions. At the PoC's ~1.9 sessions per user per day that is ~240 users for a 7-day window and ~55 users for a 30-day window | repository `:483-507`, handler `:488-489` |
| D11 | ClickHouse applies an `ORDER BY` written after `UNION ALL` to the last `SELECT` only. The session timeline (API requests ∪ tool calls) and the dispatch list (agent and skill spans ∪ slash-command hook events) come back grouped by branch, not in time order. Found by comparing both engines' answers during implementation | The UI re-sorts both lists by time, but the handler numbers agent labels ("agent · Explore #2") in row order, so the numbers can run out of time order | repository `get_session_detail_events`, `get_session_detail_dispatches` |
| D12 | A dispatch whose parent span is not a matched interaction gets `real_duration_ms` of about −1.7·10¹² (the `LEFT JOIN` default timestamp 1970) | The dispatch timeline shows a negative duration; PostgreSQL reproduces it for parity. Not fixed | repository `get_session_detail_dispatches` |
| D13 | OTLP logs carrying `event_type` count as hook events with the client's own `user.email`, and a session's identity is `max(user_email)` | An authenticated client that knows a session id can change that session's attribution. Same on both engines; not fixed | `mv_session_identity`, `mv_session_dims`; PostgreSQL `session_dims` |

---

## 3. Requirements and targets

Functional:

- F1. `CLI_ANALYTICS_STORAGE_BACKEND` selects the engine at startup; no request-time branching in
  services or routers.
- F2. All 11 GET endpoints return the same JSON for the same stored data on either engine,
  modulo the decisions on D1–D4 recorded in this document.
- F3. The 4 ingest endpoints keep their paths, auth, content types, size limit and status classes.
- F4. The PostgreSQL deployment needs neither ClickHouse nor the OTel Collector.
- F5. A new harness (Cursor, Codex, Gemini CLI) is added by extending a vocabulary, not by
  rewriting queries.

Quality attributes, with the targets the PoC was measured against:

| Attribute | Target |
| --- | --- |
| Ingest latency | p99 < 500 ms per request at peak (plugin hard limit: 2 s) |
| Ingest throughput | ≥ 10× the estimated peak for 500 active users (§7.3) on one API pod |
| Dashboard latency | p95 < 1 s per endpoint for a 30-day window at ~500 users; session detail < 200 ms |
| Freshness | new events visible ≤ 60 s after ingest (refresh interval + run time) |
| Correctness | each record stored once under retries; rollups equal a full recompute from raw |
| Retention | raw 90 days, rollups 365 days (same as ClickHouse TTLs), configurable |
| Portability | PostgreSQL 14+ with no extensions; optional use of extensions only as accelerators |
| Isolation | analytics cannot exhaust the application's connection pool or hold long locks |
| Operability | schema via migrations, partition upkeep automated, backlog and lag observable |

The PoC measured medians of three runs on a laptop VM, not percentiles under load; the p95 targets
are verified by the Phase 4 load test (§9).

---

## 4. Adapter architecture: options considered

### A1 — Ports at the repository and ingest boundary (chosen)

The handler already consumes a set of named fact queries returning `list[dict]`. Promoting that
set to a `Protocol` costs nothing in the handler and leaves all business logic (pricing, dead
sessions, depth buckets, delivery framework) shared. Each adapter writes native SQL for its engine.

Why this beats the alternatives below: the two engines differ in more than dialect. ClickHouse
keeps rollups as `AggregateFunction` states merged at read time; PostgreSQL keeps plain rows
recomputed by a job. The queries read different tables, so any layer that tries to generate both
from one definition still needs per-engine table knowledge.

### A2 — One query definition compiled per dialect (SQLAlchemy Core, clickhouse-sqlalchemy)

Rejected. Every hard part of the current SQL is engine-specific: `argMax`, `argMinIfMerge`,
`uniqExactMerge`, `Map` access, `LEFT JOIN` default values. Each would need a custom `@compiles`
per dialect, and the table layouts still differ. It moves the problem into compiler hooks without
removing it.

### A3 — Transpile the ClickHouse SQL (SQLGlot)

Rejected for runtime; useful as a starting draft when porting. Porting by hand surfaced semantic differences a
transpiler cannot see: ClickHouse `LEFT JOIN` fills non-matching rows with `''`/`0`/`1970-01-01`
instead of `NULL`, `argMax` keeps the first maximum in scan order, `max()` on strings compares
bytes while PostgreSQL compares by collation, `{p:DateTime64(3)}` truncates parameters to
milliseconds.

### A4 — Semantic layer (Ibis, Cube, dbt metrics)

Rejected. Ibis can target both engines, but it still needs identical table layouts, adds a heavy
dependency for 27 queries, and makes per-engine tuning (the hourly rollups in §6.6) harder. Cube is
an extra service to operate, which is what PostgreSQL deployments are trying to avoid.

### Write-side options

| Option | Verdict |
| --- | --- |
| I1. Decode OTLP in the API and write to PostgreSQL (chosen) | No new component. `opentelemetry-proto`, `protobuf` and `asyncpg` are already dependencies (`pyproject.toml:55, 87, 96`). The plugin's spool provides durability while PostgreSQL is unavailable (the API returns 503, the proxy retries). |
| I2. Collector with a PostgreSQL exporter | Not in otelcol-contrib (donation #46501, Feb 2026, "Sponsor Needed"); third-party exporters exist but would mean building and supporting a custom Collector distribution, and their schemas are generic JSONB envelopes (≈1,080 B/record, §5.2). |
| I3. Collector → `otlphttp` → an internal CodeMie writer endpoint | Keeps the Collector's disk queue, but adds a hop and a component to deployments whose point is having fewer. Worth revisiting only if ingest must survive long PostgreSQL outages without relying on client spools. |
| I4. Queue (NATS is already a dependency) → worker → PostgreSQL | Decouples ingest latency from the database at the cost of another consumer to operate. Not needed at the measured load (§7.3). |

---

## 5. PostgreSQL storage: options considered

### 5.1 Layout options

| Option | Summary | Verdict |
| --- | --- | --- |
| P1. ClickHouse mirror | Collector-style envelope: attributes as JSONB maps, resource attributes on every row, promoted columns as generated columns | Measured ~1,084 B/row with two indexes. Rejected: 2.7× P2 for no query benefit. |
| **P2. Typed hybrid (chosen)** | Attributes the queries use become typed columns; the long tail stays in a residual JSONB column; session-constant attributes (`user.id`, `organization.id`, `terminal.type`, …) stored once per session; resource attributes deduplicated by hash | Measured ~405–411 B/row for logs/spans with the same two indexes. |
| P3. TimescaleDB hypertables + columnstore + continuous aggregates | Best compression and native rollups | Not a baseline: compression, continuous aggregates and retention policies are Timescale License (TSL) features. Amazon RDS and Cloud SQL do not offer TimescaleDB; Azure Flexible Server offers the Apache-2 edition only, which has none of those three. Keep as an optional accelerator for self-hosted clients. |
| P4. Columnar extensions (Citus columnar, Hydra, pg_duckdb, pg_mooncake) | Columnar compression inside PostgreSQL | Same availability problem on managed services. Not a baseline. |
| P5. Raw tables only, no rollups | Simplest | Measured too slow at scale: the five queries that still scanned raw data took 0.9–1.9 s each for a 30-day window at ~480 users (§7.4). |

### 5.2 Why storage cannot be tuned down on vanilla PostgreSQL

PostgreSQL compresses a value only when its row exceeds `TOAST_TUPLE_THRESHOLD` (≈2 KB, a
compile-time constant). Telemetry rows are 200–1,100 bytes. Reloading the same 300K log and
span records with `toast_tuple_target=128` and `lz4` on the JSONB columns did not change the size
(1,083 vs 1,084 B/row), because the TOAST pass never starts. On a managed service the only
effective lever is what goes into a row — which is why P2 moves attributes into typed columns and
stores session-constant attributes once. Filesystem compression (ZFS, btrfs) helps self-hosted
clients only.

Measured on identical records (bytes per row, heap + TOAST + the same two indexes):

| Layout | Logs | Spans |
| --- | --- | --- |
| P1 ClickHouse mirror, default | 1,084 | 1,086 |
| P1 + lz4 + `toast_tuple_target=128` | 1,083 | 1,086 |
| P2 typed hybrid | 411 | 403 |
| ClickHouse (same records, ZSTD) | 32 | 59 |

### 5.3 Rollup maintenance options

| Option | Verdict |
| --- | --- |
| R1. Upsert rollups inside the ingest transaction | Rejected. Exact `argMin`/distinct semantics need per-field bookkeeping; tool success needs a join with a span that may arrive in a later request; hot rows serialise concurrent requests of a session. |
| R2. Triggers | Rejected for the same reasons, plus logic hidden in the database. |
| **R3. Dirty-key recompute job (chosen)** | Ingest records `(day, session)` keys it touched; a job recomputes each affected rollup slice from raw with plain `GROUP BY`. Idempotent, absorbs late and out-of-order data, doubles as the backfill tool. Costs freshness (≤ refresh interval). |
| R4. Materialized views + `REFRESH CONCURRENTLY` | Rejected: full recompute on every refresh. |

### 5.4 Partitioning and retention

Native declarative range partitioning: weekly partitions for raw tables (13–14 live partitions at
90-day retention), monthly for daily rollups, daily for the 14-day idempotency ledger. Retention is
`DROP`/`DETACH ... CONCURRENTLY` of whole partitions — no `DELETE`, no vacuum debt. Partitions are
created ahead and dropped by an application job (APScheduler with a session advisory lock on the
analytics database, so it works when the analytics tables live in another database; §6.11); `pg_partman` + `pg_cron`
(available on RDS, Azure and Cloud SQL) is an optional alternative, not a requirement. Each table
keeps a `DEFAULT` partition as a safety net that should stay empty; the job alerts if it is not.

---

## 6. Target design

### 6.1 Module layout

```
src/codemie/repository/cli_analytics/
    ports.py          CliAnalyticsReader, CliTelemetryIngestor (Protocols); rows stay list[dict] (§6.11)
    filters.py        LocalAnalyticsFilter (moved) + explicit deny_all (fixes D5)
    vocabulary.py     harness vocabulary: span/event kinds, rollup metrics, hook dimension types
    factory.py        get_cli_analytics_storage() -> CliAnalyticsStorage(reader, ingestor, runtime)
    clickhouse/
        reader.py     today's SQL, plus tie-breakers (D1)
        ingestor.py   today's _forward / _event_to_log_record / _build_otlp_logs_payload
    postgres/
        engine.py     dedicated asyncpg pool for the analytics DSN; IAM tokens from PostgresClient's
                      helpers, minted for the DSN's own endpoint (settings.py builds the URLs, §6.11)
        otlp.py       OTLP protobuf/JSON -> typed rows (pure functions, no I/O)
        ingestor.py   sanitise, dedup ledger, batched INSERT ... unnest, dirty keys
        reader.py     the 27 queries
        rollups.py    refresher: claim + recompute SQL
        maintenance.py partitions ahead, retention drops, ledger and session_dims retention
src/codemie/service/analytics/cli_analytics_jobs.py   APScheduler registration + leader lock
src/external/alembic_cli_analytics/                   migrations for the analytics schema (PostgreSQL only)
```

`src/codemie/repository/cli_analytics_repository.py` was to keep re-exporting `LocalAnalyticsFilter`
and the ClickHouse reader under its old name for one release; it was removed instead (§6.11).

Interfaces (abridged; the full list mirrors the 27 current method signatures):

```python
class CliAnalyticsReader(Protocol):
    async def get_cost_kpis(self, f: LocalAnalyticsFilter) -> list[dict]: ...
    async def get_session_cost_facts(
        self, f: LocalAnalyticsFilter, search: str | None = None, is_unattributed: bool = False
    ) -> list[dict]: ...
    async def get_session_detail_dispatches(self, session_id: str) -> list[dict]: ...
    # ... 24 more, one per current repository method


class CliTelemetryIngestor(Protocol):
    async def ingest_otlp(self, signal: Literal["logs", "metrics", "traces"],
                          body: bytes, content_type: str) -> IngestResult: ...
    async def ingest_hook_events(self, events: list[dict], user_email: str,
                                 received_at_ns: int) -> IngestResult: ...


@dataclass(frozen=True)
class CliAnalyticsStorage:
    reader: CliAnalyticsReader
    ingestor: CliTelemetryIngestor
    runtime: CliAnalyticsRuntime | None   # None for ClickHouse; start, jobs and close for PostgreSQL
    raw_retention_days: int               # dashboard windows are clamped to it
```

The row contract is naive-UTC `datetime`, `date` for days, `int` for counts, `float` for money,
`list[str]` for arrays, `''`-or-`None` for missing strings exactly as today; the response models
enforce it, and comparing both engines' answers checks it (row `TypedDict`s were not added, §6.11). Driver values are normalised to that contract in one place in the PostgreSQL reader (asyncpg
returns timezone-aware datetimes and `Decimal` for `sum(bigint)`).

The router keeps request parsing (size limit, content type, NDJSON parsing, JWT email) and calls
the port; the module-level `_repository = LocalAnalyticsRepository(ch_query)` becomes a lazy
`CliAnalyticsStorageFactory.get()`, so a PostgreSQL deployment never creates a ClickHouse client.

### 6.2 Configuration

| Setting | Default | Meaning |
| --- | --- | --- |
| `CLI_ANALYTICS_STORAGE_BACKEND` | `clickhouse` | `clickhouse` or `postgres` |
| `CLI_ANALYTICS_PG_URL` | empty → main `PG_URL`/`POSTGRES_*` settings | analytics DSN; point at a separate instance or database for large installs |
| `CLI_ANALYTICS_PG_SCHEMA` | `codemie_analytics` | schema holding all analytics objects |
| `CLI_ANALYTICS_PG_POOL_SIZE` | 8 | dedicated pool, never the application pool |
| `CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS` | 30000 | mirrors `CLICKHOUSE_QUERY_TIMEOUT_SECONDS` |
| `CLI_ANALYTICS_PG_INGEST_STATEMENT_TIMEOUT_MS` | 10000 | each ingest transaction; a slower one answers 503 and the plugin retries |
| `CLI_ANALYTICS_PG_WORK_MEM` | `32MB` | set on the analytics pool's sessions; measured: 30-day `/overview` at ×12 went from 993 to 763 ms, `/users` from 783 to 471 ms |
| `CLI_ANALYTICS_PG_INGEST_ACQUIRE_TIMEOUT_MS` | 1000 | fail fast with 503 instead of queueing past the plugin's 2 s limit |
| `CLI_ANALYTICS_RAW_RETENTION_DAYS` | 90 | raw partitions dropped after this |
| `CLI_ANALYTICS_ROLLUP_RETENTION_DAYS` | 365 | rollup partitions dropped after this; must be at least the raw retention |
| `CLI_ANALYTICS_DEDUP_RETENTION_DAYS` | 14 | idempotency ledger window |
| `CLI_ANALYTICS_ROLLUP_REFRESH_SECONDS` | 30 | refresher interval |
| `CLI_ANALYTICS_PARTITION_PREMAKE_WEEKS` | 4 | partitions created ahead |

`CLICKHOUSE_*` and `ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT` keep their meaning for the ClickHouse
adapter. The router clamps windows to `CliAnalyticsStorage.raw_retention_days`, which each adapter
sets: `CLI_ANALYTICS_RAW_RETENTION_DAYS` on PostgreSQL, the schema's 90-day TTL on ClickHouse.

The analytics DSN must be a direct or session-pooled connection. Session settings travel as
startup parameters, the jobs hold session-level advisory locks, and asyncpg caches prepared
statements, so a transaction pooler (PgBouncer's default mode) breaks all three. A SQLAlchemy
`ssl=` parameter is rewritten as libpq's `sslmode=`. With IAM, a token is minted for the DSN's
own host, port and user.

Implementation note: analytics sessions also run with `jit=off`. A plan over every weekly
partition costs past `jit_above_cost`, and at ×3 JIT compilation of ~760 functions added about
500 ms to a 3 ms session-detail query; no endpoint was faster with JIT, and the rollup refresh
was 20% faster without it.

### 6.3 Write path (PostgreSQL)

Per request, one transaction:

1. Decode. `ExportLogsServiceRequest.FromString(body)` (protobuf concatenation merges the proxy's
   joined exports), or `json_format.Parse` for `application/json` with lone surrogates replaced by
   U+FFFD first. Hook NDJSON is parsed by the router as today.
2. Sanitise every string: strip NUL, replace lone surrogates with U+FFFD — before hashing and
   before encoding. Both cases occur in real plugin payloads (§2.3) and both crashed the PoC until
   handled.
3. Normalise with the same semantics as the Collector + materialized columns: attribute values
   rendered like pcommon `AsString()` (a double `5.0` is `"5"`), numbers parsed like
   `toUInt64OrZero`/`toFloat64OrZero` (unparseable originals stay in the residual JSONB),
   `session_id = coalesce(session.id, session_id)`, `prompt_id = coalesce(prompt_id, prompt.id)`,
   span `user_email` falls back to the resource attribute. OTel log records that carry `event_type`
   are hook events and go to `hook_events`, exactly like `mv_hook_events`. Values no real request
   comes near (tokens above 10^9, cost beyond ±10^6 USD, spans longer than 30 days, counter
   readings adding more than 10^9 to a rollup column) count as unparseable, so no client can make
   the dashboards' sums overflow `bigint` or `float8`; ClickHouse would store (and wrap) them.
4. Deduplicate per record: a 64-bit hash of the serialised record (spans: `trace_id + span_id`; hooks:
   the sorted-key JSON of the event, without the JWT email — the proxy re-sends a spool with
   whatever cookie it holds at that moment, so the sender is not part of the record's identity).
   `INSERT INTO ingest_dedup ... ON CONFLICT DO NOTHING RETURNING` decides which rows are new; only
   those are inserted.
5. Insert with one `INSERT ... SELECT FROM unnest($1::..[], ...)` per table (no temp tables).
6. Queue `(day, session)` keys in `rollup_dirty` with a bitmask of affected rollup families,
   `ON CONFLICT DO UPDATE` (the update takes the row lock the refresher protocol relies on). Span
   rows also mark the previous day when they start within an hour after midnight, so a tool call
   that crosses midnight is re-evaluated on both days.

Response: 200 with an empty OTLP `Export*ServiceResponse` body. Error mapping: undecodable body →
400; pool acquisition timeout or database error → 503 (the proxy retries). A record the database
rejects for its content (SQLSTATE class 22, or 54000) is found by splitting the request, dropped,
logged and entered in the ledger so a re-send skips it; the rest is stored and the answer is 200
with OTLP `partial_success` naming the rejected count. A request spends at most 64 failed
attempts on this; past them it answers 503, keeping what it stored and rejected, and the plugin's
re-send carries on from there (a 2xx would lose the rest: the plugin deletes its spool on any 2xx).
A storage that refuses its configuration (§6.11) answers 503 on every endpoint. The proxy retries every non-2xx answer
except 401/403 with its whole spool (D7-style loop), so answering 400 there would block that
client's telemetry for good; the plugin fix is tracked in §10.

### 6.4 Schema

Full DDL is in Appendix A. Summary:

| Table | Grain | Partitioning / retention | Main indexes |
| --- | --- | --- | --- |
| `log_events` | OTel log record (api_request, user_prompt, tool_result, skill_activated, …) | weekly / 90 d | `(session_id, ts)`, BRIN `(ts)`, partial `(session_id) INCLUDE (skill_name) WHERE event_kind=3`, partial `(ts) INCLUDE (session_id, command_name) WHERE event_kind=2` |
| `hook_events` | plugin hook event, every type | weekly / 90 d | `(session_id, ts)`, BRIN `(ts)` |
| `spans` | span | weekly / 90 d | `(session_id, ts)`, partial covering `(ts) INCLUDE (session_id, tool_name, tool_use_id, skill_name, subagent_type) WHERE span_kind=1`, partial `(tool_use_id) INCLUDE (success, ts) WHERE span_kind=2` |
| `metric_points` | sum/gauge data point | weekly / 90 d | `(session_id, ts)` |
| `otel_resources`, `session_attributes` | deduplicated resource / session-constant attributes | — | PK |
| `ingest_dedup` | record hash | daily / 14 d | PK `(day, h)` |
| `rollup_dirty` | `(day, session)` work queue | — | PK |
| `cost_daily`, `lines_daily`, `active_time_daily`, `turns_daily`, `tool_facts_daily` | same keys as the ClickHouse rollups | monthly / 365 d | PK + `(session_id)` |
| `session_files_daily` | `(day, session, file_path)` flags — replaces `uniqExact` states | monthly / 365 d | PK |
| `invocations_hourly` | `(hour, session, kind, name)` calls + successes for tools, skills, agents, slash commands | monthly / 365 d (a single table in the PoC) | PK + `(session_id)` |
| `session_dims` | one row per session: dimensions (earliest non-empty value) + identity (`jwt_email`, max developer name) | row delete after 365 d | PK |
| `session_skills` | `(session, skill)` from `skill_activated` | with `session_dims` | PK |

Canonical vocabularies are small integers set at ingest (`span_kind`: tool, tool_execution,
interaction, llm_request; `event_kind`: api_request, user_prompt, skill_activated, …). The
original name stays in `span_name` / `event_name`.

### 6.5 Rollup refresher

Runs every `CLI_ANALYTICS_ROLLUP_REFRESH_SECONDS` on one pod (leader lock), in batches of 5,000
keys, one transaction per batch:

1. Claim (the versioned claim, §6.11): `SELECT day, session_id, kinds, version FROM rollup_dirty
   WHERE marked_at <= clock_timestamp() ORDER BY marked_at LIMIT 5000`, an unlocked read, loaded
   into a temp table.
2. For each rollup family flagged in the bitmask: `DELETE` the claimed slices, `INSERT ... SELECT
   ... GROUP BY` from raw. `session_dims` and `session_skills` are recomputed per session.
3. Release: lock the claimed queue rows in `(day, session_id)` order, delete the ones whose version
   is still the claimed one, re-stamp the ones ingest marked again meanwhile; commit. On error
   everything rolls back and the keys stay queued.

A batch PostgreSQL rejects for its data is split in halves until the key at fault is alone, and
that key is dropped (logged). A batch that runs past its time limit has its keys flagged `alone`,
and whichever pod refreshes next takes them one at a time; a key that times out alone is retried
10, then 20 minutes later, and dropped after its third timeout in a row. The flags and counts live
in `rollup_dirty` (`alone`, `timeouts`), so every pod sees them and a restart keeps them. Lock waits
are bounded (`lock_timeout`), and the run is retried.

Why nothing is lost under concurrent ingest (READ COMMITTED): ingest marks a key with
`ON CONFLICT DO UPDATE ... SET version = version + 1`. The refresher deletes a key only while its
version is the one it claimed, under row locks taken in the order ingest takes them, so a key
marked again during a recompute stays queued and a later run recomputes it. Every recompute
statement takes a fresh snapshot, so rows committed after the claim are either included or re-queued. §7.3 checks
this by comparing incrementally maintained rollups with a full recompute after concurrent ingest.

The same job is the backfill and repair tool: mark a range of keys dirty and let it run.

### 6.6 Read path

Rules applied to every query (Appendix B has the full ClickHouse → PostgreSQL mapping):

- Day windows use `(ts AT TIME ZONE 'UTC')::date`; timestamp windows truncate the bounds to
  milliseconds, as `{p:DateTime64(3)}` does. Naive datetimes are treated as UTC explicitly —
  asyncpg would otherwise read them as local time.
- `argMax(model, api_call_count)` becomes `(array_agg(model ORDER BY api_call_count DESC, <key
  columns>))[1]`; `argMinIf(x, ts, x != '')` becomes `(array_agg(x ORDER BY ts, ingest_seq) FILTER
  (WHERE x <> ''))[1]` — the earliest value, ties to the first ingested, matching ClickHouse's
  first-seen behaviour.
- String `max()` uses `COLLATE "C"` (byte order, like ClickHouse).
- Where a ClickHouse `LEFT JOIN` default reaches the API (D2), the query either reproduces it or
  the defect is fixed in both engines first (decision in §10).

Performance techniques that made the difference at ~480 users (§7.4):

- **Hourly rollup interior + raw edges.** Tool success, tool usage and invocations must honour
  exact timestamps (ClickHouse filters raw spans with `BETWEEN start AND end`). The full hours
  inside the window come from `invocations_hourly`; only the partial hours at both edges are read
  from raw spans. Results stay exact; 1.75–1.95 s dropped to 32–84 ms.
- **`last_active` from `session_dims`.** `last_event_at` is already `max(ts)` over the same 13 hook
  types, so a raw lookup is needed only for sessions whose last event is after the window end.
  863 ms dropped to 33 ms. One assumption: the fallback name is the session's first
  `developer_name` rather than each event's; the plugin sets it once per machine, so the two agree.
- **`session_skills`** replaces a lookup of every session id across all partitions: 1.3 s →
  142 ms for 28.5K ids (and it no longer overflows a 64 MB `/dev/shm` in Docker, which the
  original query did).
- Distinct file counts use a two-level hash aggregate instead of `count(DISTINCT)`: 1.05 s →
  0.3 s.

### 6.7 Adding a harness

`vocabulary.py` maps harness names to canonical kinds:

```python
SPAN_KIND = {"claude_code.tool": TOOL, "claude_code.tool.execution": TOOL_EXEC,
             "claude_code.interaction": INTERACTION, "claude_code.llm_request": LLM_REQUEST}
LINES_METRICS = ("claude_code.lines_of_code.count",)
ACTIVE_TIME_METRICS = ("claude_code.active_time.total",)
```

Cursor support from branch `EPMCDME-14834` is three dictionary entries and two tuple entries. No
PostgreSQL query changes, because the queries filter on `span_kind`. Rows ingested before a name
was added keep kind `0` until a backfill re-derives them (one `UPDATE ... WHERE span_name IN
(...)` plus marking their keys dirty). The ClickHouse adapter keeps its `IN (...)` lists and
migration files as that branch does.

### 6.8 Operations

- **Topology.** Same PostgreSQL cluster in a separate schema is acceptable for small installs.
  Above roughly 200 active users, or when the analytics database grows past the main database,
  point `CLI_ANALYTICS_PG_URL` at a separate database or instance, so backups, vacuum and I/O do
  not compete with the application.
- **Pools.** A dedicated pool (default 8) for ingest + reads; reads get `statement_timeout`; ingest
  gets a short pool-acquire timeout so saturation turns into 503s the proxy retries, not into 2 s
  waits that make the plugin drop events.
- **Memory per query.** `work_mem=32MB` on the analytics pool; the default 4 MB makes the per-session
  aggregations spill (measured effect in §6.2).
- **Containers.** The PoC needed `shm_size` of at least 256 MB for PostgreSQL in Docker (the implementation does not, §6.11); the default 64 MB made
  a parallel hash join fail in the PoC ("could not resize shared memory segment").
- **Vacuum.** Raw tables are insert-only (index-only scans need the visibility map: PostgreSQL 13+
  `autovacuum_vacuum_insert_scale_factor` handles it). Rollup tables are rewritten slice by slice;
  give them `fillfactor=90` and a lower `autovacuum_vacuum_scale_factor`.
- **Metrics.** Ingest latency and rows per table, dedup hits, `rollup_dirty` size and oldest
  `marked_at` (alert at > 5 min), refresher run time, rows in `DEFAULT` partitions, table sizes.
  Implemented as log lines for now (§6.11): a stale-queue warning every 5 minutes while the oldest
  key waits longer than that, `dropped rollup key`, maintenance-step errors, rows in `DEFAULT`.
- **Backups.** Analytics data is re-creatable only from clients' spools, which are deleted after
  delivery; back it up like application data or accept its loss explicitly per client.

### 6.9 Security and privacy

Endpoints, auth and the admin gate stay in the shared router. The analytics role needs DDL only on
its schema (migrations) and DML on its tables, plus `TEMPORARY` on the database (PUBLIC has it by
default; the refresher stages its keys in a temporary table). A DBA may create the schema for it,
and then the role needs no `CREATE` on the database. Reads use parameters only (vocabulary constants are code,
not input). Resource attributes (which carry user emails) are purged once no raw row has used them
for the raw retention, as ClickHouse expires them with each row. The stored personal data is the same as in ClickHouse today: emails, developer
names, the first 200 characters of prompts, 300 characters of tool input/output, error messages.

### 6.10 Failure modes

| Failure | ClickHouse path today | PostgreSQL adapter |
| --- | --- | --- |
| Database down | Collector queues to disk; API returns 200 while the Collector is up | API returns 503; the proxy keeps the spool and retries every 20 s; nothing lost while the client keeps its spool |
| Collector down | API returns 503 after 3 attempts | not applicable |
| Duplicate delivery | stored twice (D6) | stored once (ledger, 14 days) |
| Slow database | Collector absorbs | ingest latency grows; acquire timeout converts it to 503 before 2 s |
| Refresher stopped | not applicable | raw data keeps arriving; dashboards go stale; backlog metric alerts; recovers by itself |
| Partition missing | not applicable | rows land in `DEFAULT`; maintenance job alerts and moves them |
| A record the database rejects | the Collector's exporter drops its batch; the API answered 200 | that record is dropped, logged and entered in the ledger; the rest is stored; 200 with `partial_success` (past 64 failed attempts: 503, and the re-send carries on) |
| A rollup key too slow to recompute | not applicable | its batch's keys are taken one at a time by whichever pod runs next; the key is retried 10, then 20 minutes later and dropped after 3 timeouts in a row |
| Analytics URL the pool cannot apply | not applicable | the storage refuses it at startup (logged, the application starts); analytics endpoints answer 503 |
| Maintenance step fails or times out | not applicable | the other steps, partitions and purges still run; the refresh job retries a missing `DEFAULT` partition within 30 s |

### 6.11 Deviations adopted during implementation

Each was decided while building and is recorded with its reason in the plan's decisions table
(`docs/superpowers/plans/2026-09-23-cli-analytics-postgres-storage.md`).

| Spec | Implemented | Why |
| --- | --- | --- |
| §6.8 metrics, Phase 4.3 dashboards and alerts | Log lines (see §6.8); metrics, dashboards and alert rules are a follow-up | They depend on a platform decision about the metrics backend; the log lines are alertable today |
| Phase 1.5 gzip `Content-Encoding` | Not decoded | The plugin never compresses, and the ClickHouse path never forwarded compressed bodies either |
| §6.3.4 `xxh64` | blake2b-64 | No new dependency; about 6·10⁻⁹ collision odds per day at 500 users |
| §6.1 row TypedDicts | Reader rows stay `list[dict]` | The response models enforce the row contract and comparing both engines' answers checks it; no type checker runs in the gate |
| §6.5 step 1 claim (`SKIP LOCKED`, `DELETE ... RETURNING`) | Versioned claim: an unlocked read, a recompute, then a sorted lock pass and `DELETE ... WHERE version = claimed` | Ingest waits only for the final delete, not for a whole recompute, within the plugin's 2 s limit; equally lossless |
| Phase 2.3 `LeaderLockContext` | Session advisory locks on the analytics database | Leader election then works when the analytics tables live in another database than the application's |
| Phase 1.2 reuse of `PostgresClient` URL building | The analytics URLs are built in `settings.py`: the configured URL is read once, strictly, as libpq reads it (connection parts given as parameters included), and the pool's (asyncpg) and the migrations' (SQLAlchemy/psycopg2) URLs are rebuilt from that one reading, fully percent-encoded, each value where both drivers decode it (a database name needing escapes and a socket's port go in the query). A test runs the drivers' own parsers on both URLs. `ssl=` becomes `sslmode=`; `hostaddr`/`service` and stricter security settings asyncpg cannot apply (`channel_binding=require`, `require_auth`, ...) refuse the storage, `options`/`load_balance_hosts`/`replication` are left out of both, other libpq parameters reach the migrations only; anything two clients could read differently (an `@` anywhere but before the host, an unescaped `/` or `?` in a user name or password, a `#`, a bad `%` escape, a control character, several hosts, a part set twice, no host, asyncpg's `database=`) is refused, never quoted; AWS IAM needs a network host and a user in the URL. The IAM token helpers are reused with the URL's host, port and user | asyncpg and libpq accept different URLs than SQLAlchemy's, and SQLAlchemy passes a URL's path and host on undecoded; an AWS token is bound to its endpoint |
| Phase 5.1 compose profile | None; `docker compose up -d codemie` already runs without ClickHouse and the Collector | A profile would stop ClickHouse and the Collector from starting by default for everyone |
| Phase 0.6 "existing tests pass unchanged" | Patch targets re-pointed to the ports; an autouse fixture pins the ClickHouse backend in the ingest tests | The router reaches ClickHouse only through the ports now, and a developer's `.env.local` can select another backend |
| §6.1 and Phase 0.1 compatibility module (`cli_analytics_repository.py` re-exporting the old names) | Removed; the ClickHouse queries live in `clickhouse/reader.py` | Nothing in the repository imported the old path; a branch that changes the old module must port its SQL to the new one anyway |
| Phase 5.1 `.env.example` section | None; the settings are documented in the operations guide | `.env.example` holds a fixed set of keys by design (`tests/codemie/configs/test_env_example.py`) |
| §6.8 `shm_size` of at least 256 MB | Not needed | The query that overflowed `/dev/shm` was replaced by `session_skills`; every suite and benchmark ran on Docker's default 64 MB |
| Phase 3.3 parity suite in the repository (and §8: in CI) | Kept out of the repository, with the other tests that need live ClickHouse or PostgreSQL servers; run before merging a storage change | They are test tooling for live databases, which the repository's test gate does not have; the committed unit tests need none |
| §6.3 error mapping (400 for rejected data) | Rejected records are isolated and dropped; 200 with `partial_success` | See §6.3: the proxy re-sends every non-2xx answer with its whole spool |

---

## 7. Proof of concept

### 7.1 Setup

- Docker Desktop VM: 2 vCPU, 3.8 GB RAM, shared with the developer stack (Elasticsearch held
  2.4 GB). PostgreSQL 17.11 (`pgvector/pgvector:pg17`, default settings: 128 MB shared_buffers,
  4 MB work_mem). ClickHouse 24.10 (`clickhouse/clickhouse-server:24`, the repo's image) capped at
  900 MiB with the production `config/clickhouse/schema.sql`. Numbers are therefore pessimistic
  for both engines; ratios between them are what carries over.
- Synthetic data from `gen.py`: real OTLP protobuf and hook NDJSON in the plugin's wire format
  (Claude Code attribute names and types, 13 hook types plus `agent.skill.dispatch`), with edge
  cases on purpose — sessions without plugin data, proxy sessions with an empty `user.email`,
  email only in resource attributes, sessions crossing midnight, sentinel first prompts, a split
  emoji, NUL bytes, unknown metrics.
- Parity dataset: 40 users, 31 days, 20,585 API requests, 1,166,212 records, 2,377 sessions.
  Scale dataset: the same ×12 with distinct sessions, tool-use ids and emails (~480 users,
  28,524 sessions in a 30-day window, ~14 M records).
- ClickHouse was loaded the way production loads it: the exporter's row mapping reproduced in
  Python, hook NDJSON converted by the router's own `_event_to_log_record` /
  `_build_otlp_logs_payload`. PostgreSQL was loaded through the PoC writer — the adapter under test.

The PoC scripts were not kept.

### 7.2 API parity

The parity suite runs the real `LocalAnalyticsHandler` over each repository and validates every payload
with the router's own response models before diffing the JSON (floats compared at 1e-9).
Scenarios: every endpoint × 5 windows (24 h, 7 d, 30 d, 60 d, a window with partial days at both
ends) × 6 filter sets (none, users, project, repository, users + project, the deny-all filter),
sessions with all three sort orders, pagination, search, framework and unattributed filters, and
64 session-detail pages including the edge cases.

| Run | Identical | Order-only | Different |
| --- | --- | --- | --- |
| As implemented today (no tie-breakers), v1 queries | 302 | 68 | 11 |
| Same, optimised v2 queries | 302 | 68 | 11 |
| Deterministic ordering on both engines, v1 | 380 | 1 | 0 |
| Deterministic ordering on both engines, v2 | 379 | 2 | 0 |

The 11 "different" results are D2 (205 unattributed sessions all sort on the same 1970 start time,
so page 1 of 50 is arbitrary) and D3 (repository project labels). The order-only results are D1.
Emulating the recommended tie-breakers identically on both engines leaves one or two session-detail
pages where an agent span and a slash-command dispatch share a timestamp; `ORDER BY span_start,
is_slash_command, subagent_type, skill_name` in both engines removes that.

Table-level totals were identical on both engines after loading: raw rows per signal, cost
($5,739.5475), input tokens (184,650,694), cache-read tokens (8,985,783,857), API calls (91,084),
sessions (2,377), lines, active time, turns (20,027), tool calls (99,436), distinct files changed /
written / edited (33,493 / 4,679 / 14,838), sessions with dimensions (2,172).

### 7.3 Ingest

Single connection, requests replayed in arrival order (1× dataset): 7,511 records/s; per request
p50 4.6–7.3 ms, p99 17.4–26.1 ms depending on signal. The rollup refresh for the whole dataset
(2,397 session-days) took 2.9 s.

Concurrent, on a separate dataset (4,202 requests, 232,976 records) with 8 writers while the
refresher ran every 15 s: 701 requests/s and 38,843 records/s; per request p50 10.0 ms, p95 21.5 ms,
p99 32.7 ms, max 97 ms. Checks after the run:

- Replaying 840 already-stored requests verbatim inserted nothing.
- Re-sending the 466 held-back requests as grown batches (earlier records of the same session plus
  new ones, protobuf bodies concatenated as the proxy does) stored exactly the expected rows.
- Incrementally maintained rollups for the dataset's sessions equal a single-statement recompute
  from raw: 0 missing and 0 unexpected rows in `cost_daily` (1,396 rows), `turns_daily`,
  `tool_facts_daily`, `lines_daily` and `session_dims`.

The first run of this test failed the grown-batch check with 5,377 extra hook rows. The hook dedup
key then included the JWT email, and the test re-sent lines under another user's identity. The
proxy re-sends a spool with whatever cookie it holds at that moment, so identity must be the event
content alone; the design uses that (§6.3).

Estimated production load, for scale: the proxy sends at most 4 POSTs per active session every
20 s. With 300 sessions active at once (60% of 500 users) that is 60 requests/s. The PoC's average
request carried 57 records, so ~3,400 records/s at that peak — under half of what one connection
sustained sequentially.

Backfill: the refresher rebuilt all rollups for 26,367 session-days (the ×11 replicas) in
91 s (~290 session-days/s, all rollup families) on one connection.

### 7.4 Query and endpoint latency

Per repository method, 1× dataset, 30-day window, median of 3 (ms):

| Method | ClickHouse | PostgreSQL |
| --- | --- | --- |
| rollup-backed methods (13 of them) | 4–18 | 2–21 |
| get_users_last_active | 20 | 61 |
| get_file_facts_by_session | 9 | 69 |
| get_tool_success_by_session | 49 | 99 |
| get_tool_usage | 42 | 166 |
| get_invocations | 82 | 111 |
| get_skill_names_by_session (2,377 ids) | 20 | 98 |
| session detail, 6 queries | 4–21 | 1–5 |

Same methods at ×12 (28,524 sessions in the window), PostgreSQL before and after the optimisations
in §6.6 (ms):

| Method | v1 | v2 |
| --- | --- | --- |
| get_users_last_active | 863 | 33 |
| get_file_facts_by_session | 1,053 | 297 |
| get_tool_success_by_session | 1,914 | 84 |
| get_tool_usage | 1,750 | 40 |
| get_invocations | 1,946 | 32 |
| get_skill_names_by_session (28,524 ids) | failed (`/dev/shm`) | 142 |
| get_session_cost_facts | 231 | 234 |
| get_model_breakdown / get_users | 143 / 122 | 142 / 119 |
| session detail, 6 queries | — | 1–18 |

Endpoint latency at ×12 — the full handler (all queries in parallel plus the Python merge),
median of 3 (ms):

| Endpoint | 7 d ClickHouse | 7 d PostgreSQL | 30 d ClickHouse | 30 d PostgreSQL | 30 d PostgreSQL, `work_mem=32MB` |
| --- | --- | --- | --- | --- | --- |
| overview | 237 | 201 | out of memory | 993 | 763 |
| cost | 22 | 41 | 54 | 199 | 152 |
| users (page of 50) | 257 | 131 | out of memory | 783 | 471 |
| repositories (all rows) | 185 | 121 | out of memory | 737 | 519 |
| tools | 490 | 51 | out of memory | 358 | 155 |
| activity | 13 | 14 | 50 | 36 | 31 |
| efficiency | 72 | 133 | 256 | 759 | 608 |
| sessions (page of 20) | fails (D10) | 209 | fails (D10) | 966 | 825 |
| overview, one user | 211 | 63 | out of memory | 113 | 87 |
| session detail | 75 | 39 | 121 | 55 | 34 |

With `work_mem=32MB` the 7-day numbers were 10–202 ms. Read the ClickHouse columns carefully:
ClickHouse was capped at 900 MiB in this VM, and "out of memory" means the query hit that cap.
Production ClickHouse has far more memory, so these rows say that PostgreSQL is not the bottleneck
at this scale; they do not say PostgreSQL is faster than ClickHouse. The `/sessions` failures are
not memory: they are D10 and happen on production ClickHouse too.

The remaining cost on the 30-day window is the handler's per-session fan-out (D8): `/overview`
merges eight per-session result sets of 28.5K rows in Python after its queries return.

### 7.5 Storage

| | ClickHouse | PostgreSQL |
| --- | --- | --- |
| 1× dataset (1.17 M records) | 40.2 MiB | 563 MB (incl. 98 MB idempotency ledger holding all 31 days) |
| Per raw record | ~36 B at 1×, ~23 B at ×12 | ~480 B at 1× (incl. ledger), ~406 B at ×12 (raw tables with indexes) |
| ×12 dataset | 308 MiB | 5,797 MB |

Projection for 90 days of raw data plus 365 days of rollups. The synthetic workload averages ~940
records per user per day (≈2 sessions × ≈490 records); this is an assumption, not a measurement of
production. Measure the real figure on the current ClickHouse before sizing:

```sql
SELECT round(
    ( (SELECT count() FROM codemie_analytics.coding_agent_logs        WHERE TimestampDate >= today() - 30)
    + (SELECT count() FROM codemie_analytics.coding_agent_traces      WHERE TimestampDate >= today() - 30)
    + (SELECT count() FROM codemie_analytics.coding_agent_metrics_sum WHERE TimeUnix >= today() - 30) )
    / greatest(1, (SELECT uniqExact(developer_name) FROM codemie_analytics.coding_agent_hook_events
                   WHERE TimestampDate >= today() - 30 AND developer_name != ''))
    / 30) AS records_per_user_day
```

Unit costs measured at ×12: PostgreSQL 406 B per raw record with indexes, 9.4 KB per session-day
of rollups, 88 B per record in the 14-day ledger; ClickHouse 22.8 B per raw record (including its
`hook_events` copy) and 131 B per session-day of rollups. Synthetic volume: 940 records and 1.93
session-days per user per day.

| Active users | PostgreSQL raw, 90 d | PostgreSQL rollups, 365 d | Idempotency ledger, 14 d | PostgreSQL total | ClickHouse total |
| --- | --- | --- | --- | --- | --- |
| 100 | 3.4 GB | 0.7 GB | 0.1 GB | 4.2 GB | 0.2 GB |
| 500 | 17.2 GB | 3.3 GB | 0.6 GB | 21.1 GB | 1.0 GB |
| 2,000 | 68.8 GB | 13.2 GB | 2.3 GB | 84.3 GB | 4.1 GB |

In absolute terms the PostgreSQL figures are small for a managed database; the operational weight
is in vacuum, backup and restore times growing with them, which is why §6.8 recommends a separate
database above ~200 users.

### 7.6 Defects the PoC caught in the PostgreSQL path

Each of these crashed or corrupted a PoC run before it was handled; each needs a test in the
implementation:

1. NUL in a filter parameter (D5) → `invalid byte sequence for encoding "UTF8": 0x00`.
2. Lone surrogate in hook text → `orjson` refuses to hash it; PostgreSQL refuses to store it.
3. NUL inside hook text → rejected by `text` and `jsonb`.
4. `sum(bigint)` returns `Decimal` and `timestamptz` returns aware datetimes → must be normalised
   to the handler's types, or `_iso()` output gains `+00:00`.
5. Naive datetime parameters → asyncpg converts them from local time.
6. `date_trunc('hour', ts)` follows the session time zone → use `date_trunc('hour', ts, 'UTC')`.
7. Parallel hash over 28.5K ids → exceeds Docker's default 64 MB `/dev/shm`.
8. `toast_tuple_target` + `lz4` → no effect below 2 KB rows (§5.2).

---

## 8. Risks and trade-offs

| Risk | Likelihood / impact | Mitigation |
| --- | --- | --- |
| The two query implementations drift | High over time / wrong numbers on one engine | Parity suite in CI (nightly, both engines in containers); every new metric lands in both adapters in one MR |
| Storage growth larger than planned | Medium / cost, slower vacuum and backups | Measured bytes per record + the sizing query; separate instance above ~200 users; retention settings; drop raw `llm_request` spans and raw metric points first if needed (they feed no endpoint) |
| Handler fan-out (D8) at thousands of sessions | Medium / slow `/users`, `/sessions` on both engines | Next step after parity: push per-session merging into SQL behind the same port |
| Refresher falls behind | Low / stale dashboards | Batch claims, backlog alert; a slow key is taken alone and deferred, so it cannot hold up the others (§6.5) |
| Semantic drift in edge cases (exec span outside the window, argMin ties, D2) | Low / off-by-one counts at window edges | Documented rules (§6.6); parity scenarios include partial-day windows |
| Analytics load hurting the application | Medium on shared clusters | Dedicated pool, timeouts, separate DSN option |
| Current ClickHouse defects surfacing during the rollout (D1–D4, D8, D10) | High / blamed on the new adapter | Fix D1, D3, D5, D10, D11 in Phase 0 before any PostgreSQL code ships, so both engines start from the same behaviour |

---

## 9. Implementation plan

Estimates are for one engineer who knows the codebase; tasks inside a phase can run in parallel.
Each phase ends green on `make ruff`, the unit tests, and — from Phase 3 — the parity suite.

### Phase 0 — Seams and determinism, no behaviour change except D1/D3/D5/D10/D11 (4–5 days)

1. Add `repository/cli_analytics/{ports,filters,factory,vocabulary}.py`; move
   `LocalAnalyticsFilter`; add `deny_all` and use it in `FilterParams.resolve` instead of the NUL
   sentinel (D5). Keep `cli_analytics_repository.py` as a re-export shim (removed in the end, §6.11).
2. Move the ClickHouse SQL into `clickhouse/reader.py` unchanged; add tie-breakers: tool usage and
   detail tools `ORDER BY call_count DESC, tool_name`, dispatches `ORDER BY span_start,
   is_slash_command, subagent_type, skill_name`, users/cost `..., developer_name`. Handler:
   `_top_invocations` sorts by `(-count, name)`; sessions sort gets `trace_id` as secondary key;
   repository `project_name` = most frequent project, then alphabetical (D3). The session timeline
   and dispatch queries become `SELECT * FROM (... UNION ALL ...) ORDER BY ...`, so their order
   applies to the whole union (D11).
3. Move `_forward`, `_event_to_log_record`, `_build_otlp_logs_payload` into
   `clickhouse/ingestor.py`; the router calls `storage.ingestor`.
4. `CLI_ANALYTICS_STORAGE_BACKEND` in `configs/config.py`; lazy factory wiring in the router.
5. Fix D10 in the ClickHouse reader: derive the session set inside the query (join the priced
   sessions of the window) instead of sending ids as a parameter. This is a live bug for
   ClickHouse deployments above ~3,250 sessions per window.
6. Tests: existing router/handler tests pass unchanged; new unit tests for tie-breakers,
   `deny_all`, and a D10 regression test with 5,000 ids.

Acceptance: ClickHouse deployments behave as before except the deterministic ordering; no import
of `clickhouse_connect` outside the ClickHouse adapter.

### Phase 1 — PostgreSQL schema and ingest (5–6 days)

1. `src/external/alembic_cli_analytics/` with its own version table in `codemie_analytics`, run at
   startup only for the PostgreSQL backend, under an advisory lock (pattern:
   `alembic_upgrade_enterprise_postgres`). DDL from Appendix A minus partitions.
2. `postgres/engine.py` and `postgres/settings.py`: dedicated asyncpg pool; URLs built per driver
   and IAM tokens minted for the analytics endpoint (§6.11); `statement_timeout`,
   `application_name=codemie-cli-analytics`.
3. `postgres/otlp.py`: pure decode/normalise/sanitise/hash functions. Golden tests from PoC
   fixtures: AsString rendering, numeric parsing, surrogate/NUL cases, concatenated bodies,
   hook-shaped OTel logs.
4. `postgres/ingestor.py`: transaction per request, ledger, `unnest` inserts, dirty keys, error
   mapping, OTLP response bodies.
5. Router: content types (`application/x-protobuf`, `application/json`); gzip `Content-Encoding`
   is not decoded, as the plugin never compresses (§6.11).

Acceptance: replay, grown-batch and concurrent-ingest tests pass (row counts equal "each record
once"); p99 < 100 ms per request on a developer machine.

### Phase 2 — Rollups and maintenance (3–4 days)

1. `postgres/rollups.py`: claim + recompute for all rollup families; batch size and interval from
   config; metrics.
2. `postgres/maintenance.py`: partitions ahead, raw/rollup/ledger drops, `session_dims` retention,
   `DEFAULT` partition check.
3. `service/analytics/cli_analytics_jobs.py`: register both jobs with APScheduler, each behind a
   session advisory lock on the analytics database (§6.11), only for the PostgreSQL backend.
4. Admin/CLI entry point to mark a date range dirty (backfill/repair).

Acceptance: incremental rollups equal a full recompute after concurrent ingest; retention drops
partitions on a fake clock.

### Phase 3 — PostgreSQL reader (5–6 days)

1. `postgres/reader.py`: the 27 methods with the v2 techniques from §6.6 and row normalisation.
2. Decide D2 (§10) and implement accordingly in both adapters.
3. The PoC's `parity.py` as a pytest suite: generator fixture, both engines via
   testcontainers or the compose file, all scenarios from §7.2.

Acceptance: parity suite green with zero "different" and zero "order-only" results.

### Phase 4 — Hardening and performance (3–4 days)

1. Load test at the ×12 volume: ingest p99, endpoint latency (§3 targets), refresher lag under
   ingest.
2. Failure tests: database down (503), pool saturation (fast 503), a record the database rejects
   (dropped, the rest stored, 200 with `partial_success`), missing partition (DEFAULT + alert).
3. Dashboards/alerts for the metrics in §6.8.

### Phase 5 — Packaging and docs (2–3 days)

1. No compose profile (§6.11): `docker compose up -d codemie` already runs without `clickhouse` and
   `otelcollector`; `.env.example` section; Helm values if the deployment repo carries them.
2. Guide `.ai-run/guides/integration/cli-analytics-storage.md`: choosing an engine, sizing,
   settings, backfill.
3. Optional: ClickHouse → PostgreSQL migration tool (export raw rows, replay through the writer).

Total: 22–28 engineer-days. Coordinate with `EPMCDME-14834` (Cursor): if it merges first, add its
names to `vocabulary.py` in Phase 1.

---

## 10. Decisions needed from the team

1. **D2 (1970 start time).** Fix it in both engines now (return `null`/`""` for sessions without
   plugin data, which the UI shows as "—") or reproduce it in PostgreSQL for byte parity and fix
   later. Recommendation: fix in Phase 0; the UI already handles empty values.
2. **Default topology** for PostgreSQL deployments: same cluster/separate schema, or a separate
   database from the start. Recommendation: separate database on the same cluster by default.
3. **Freshness.** Is ≤ 60 s acceptable for the dashboards? If not, the refresh interval can go to
   5–10 s at the cost of more frequent small transactions.
4. **Raw data kept for 90 days that no endpoint reads** (`llm_request` spans, raw metric points,
   residual attributes): keep for future features, or shorten their retention to save space.
5. **Plugin follow-ups** (separate repository): stop retrying 4xx forever and cap the resent
   batch size (D7); these affect both engines.

---

## Appendix A — PostgreSQL DDL (as validated in the PoC)

The PoC's schema, consolidated and re-validated on an empty database (11 partitioned tables, 169 indexes
after creating five weeks of partitions). It is kept for reference only: the implemented schema is the
Alembic history in `src/external/alembic_cli_analytics/versions/`, which supersedes it. Differences from the production target are called out in
§6.4 (`invocations_hourly` was a single table here; production partitions it monthly).

```sql
-- PostgreSQL storage for CodeMie CLI analytics — schema validated by the PoC.
-- Recommended layout "P2 typed-hybrid": typed raw tables partitioned by week, residual
-- attributes in JSONB, resource attributes de-duplicated, idempotent ingest via a
-- record-hash ledger, and rollups recomputed from raw for dirty (day, session) keys.
-- Vanilla PostgreSQL >= 14, no extensions required.

CREATE SCHEMA IF NOT EXISTS codemie_analytics;
SET search_path = codemie_analytics;

-- ── Canonical vocabularies (harness names map to these at ingest; see vocab.py) ──
-- span_kind : 0 other, 1 tool, 2 tool_execution, 3 interaction, 4 llm_request
-- event_kind: 0 other, 1 api_request, 2 user_prompt, 3 skill_activated, 4 tool_result,
--             5 tool_decision, 6 api_error

-- ── Reference data ────────────────────────────────────────────────────────────
CREATE TABLE otel_resources (
    resource_id  bigint PRIMARY KEY,           -- xxh64 of canonical resource attributes
    service_name text,
    attrs        jsonb NOT NULL,
    first_seen   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE session_attributes (               -- session-constant OTel attributes (user.id, org, terminal …)
    session_id   text PRIMARY KEY,
    attrs        jsonb NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- ── Raw signal tables (retention 90 d, weekly partitions) ────────────────────────
CREATE TABLE log_events (                        -- Path A: native OTel log events
    ts                    timestamptz NOT NULL,
    session_id            text,
    event_kind            smallint NOT NULL,
    event_name            text,
    prompt_id             text,
    user_email            text,
    model                 text,
    query_source          text,
    skill_name            text,
    command_name          text,
    trace_id              bytea,
    span_id               bytea,
    cost_usd              double precision,
    input_tokens          bigint,
    output_tokens         bigint,
    cache_read_tokens     bigint,
    cache_creation_tokens bigint,
    severity              smallint,
    resource_id           bigint,
    attrs                 jsonb
) PARTITION BY RANGE (ts);

CREATE SEQUENCE hook_events_seq;
CREATE TABLE hook_events (                       -- Path B: hook events (every event_type)
    ts                    timestamptz NOT NULL,
    ingest_seq            bigint NOT NULL DEFAULT nextval('hook_events_seq'),
    session_id            text,
    event_type            text NOT NULL,
    prompt_id             text,
    user_email            text,                  -- JWT email injected by /event-hooks
    developer_name        text,
    codemie_project_name  text,
    cwd                   text,
    git_branch            text,
    repo_remote           text,
    permission_mode       text,
    source                text,
    effort                text,
    tool_name             text,
    tool_use_id           text,
    tool_input            text,
    tool_output           text,
    error_message         text,
    error_type            text,
    reason                text,
    agent_id              text,
    agent_type            text,
    trigger               text,
    denial_reason         text,
    notification_type     text,
    prompt_body           text,
    skill_name            text,
    attrs                 jsonb
) PARTITION BY RANGE (ts);

CREATE TABLE spans (
    ts              timestamptz NOT NULL,        -- span start
    duration_ns     bigint NOT NULL,
    trace_id        bytea NOT NULL,
    span_id         bytea NOT NULL,
    parent_span_id  bytea,
    span_kind       smallint NOT NULL,
    span_name       text NOT NULL,
    session_id      text,
    user_email      text,
    tool_name       text,
    tool_use_id     text,
    file_path       text,
    subagent_type   text,
    skill_name      text,
    success         text,                        -- 'true'/'false' exactly as emitted
    status_code     smallint,
    resource_id     bigint,
    attrs           jsonb
) PARTITION BY RANGE (ts);

CREATE TABLE metric_points (
    ts            timestamptz NOT NULL,          -- time_unix_nano
    start_ts      timestamptz,
    metric_name   text NOT NULL,
    value         double precision NOT NULL,
    session_id    text,
    user_email    text,
    model         text,
    type          text,
    temporality   smallint,
    is_monotonic  boolean,
    resource_id   bigint,
    attrs         jsonb
) PARTITION BY RANGE (ts);

-- ── Idempotency ledger (retention 14 d) and rollup work queue ───────────────────
CREATE TABLE ingest_dedup (
    day  date   NOT NULL,
    h    bigint NOT NULL,
    PRIMARY KEY (day, h)
) PARTITION BY RANGE (day);

CREATE TABLE rollup_dirty (
    day        date        NOT NULL,
    session_id text        NOT NULL,
    kinds      int         NOT NULL,             -- bitmask 1 cost, 2 dims, 4 spans, 8 metrics
    marked_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (day, session_id)
);

-- ── Rollups (retention 365 d, monthly partitions) — same grain as the ClickHouse MVs ──
CREATE TABLE cost_daily (
    day                   date   NOT NULL,
    session_id            text   NOT NULL,
    user_email            text   NOT NULL,
    model_name            text   NOT NULL,
    query_source          text   NOT NULL,
    cost_usd              double precision NOT NULL,
    input_tokens          bigint NOT NULL,
    output_tokens         bigint NOT NULL,
    cache_read_tokens     bigint NOT NULL,
    cache_creation_tokens bigint NOT NULL,
    api_call_count        bigint NOT NULL,
    PRIMARY KEY (day, session_id, user_email, model_name, query_source)
) PARTITION BY RANGE (day);

CREATE TABLE lines_daily (
    day           date   NOT NULL,
    session_id    text   NOT NULL,
    user_email    text   NOT NULL,
    model_name    text   NOT NULL,
    lines_added   bigint NOT NULL,
    lines_removed bigint NOT NULL,
    PRIMARY KEY (day, session_id, user_email, model_name)
) PARTITION BY RANGE (day);

CREATE TABLE active_time_daily (
    day            date   NOT NULL,
    session_id     text   NOT NULL,
    user_email     text   NOT NULL,
    active_ms_user bigint NOT NULL,
    active_ms_cli  bigint NOT NULL,
    PRIMARY KEY (day, session_id, user_email)
) PARTITION BY RANGE (day);

CREATE TABLE turns_daily (
    day        date   NOT NULL,
    session_id text   NOT NULL,
    turns      bigint NOT NULL,
    PRIMARY KEY (day, session_id)
) PARTITION BY RANGE (day);

CREATE TABLE tool_facts_daily (
    day         date   NOT NULL,
    session_id  text   NOT NULL,
    tool_calls  bigint NOT NULL,
    agent_count bigint NOT NULL,
    skill_count bigint NOT NULL,
    PRIMARY KEY (day, session_id)
) PARTITION BY RANGE (day);

CREATE TABLE session_files_daily (               -- replaces uniqExact states: exact distinct files
    day        date    NOT NULL,
    session_id text    NOT NULL,
    file_path  text    NOT NULL,
    is_written boolean NOT NULL,
    is_edited  boolean NOT NULL,
    PRIMARY KEY (day, session_id, file_path)
) PARTITION BY RANGE (day);

CREATE TABLE session_dims (                      -- coding_agent_session_dims + session_identity
    session_id     text PRIMARY KEY,
    started_at     timestamptz,                  -- NULL when only non-dimension hook types exist
    last_event_at  timestamptz,
    repository     text,
    branch         text,
    repo_remote    text,
    project_name   text,
    developer_name text,
    first_prompt   text,
    jwt_email      text,                         -- max(), byte order (COLLATE "C")
    dev_name_max   text,
    updated_at     timestamptz NOT NULL DEFAULT now()
);

-- ── Indexes (declared on parents, inherited by partitions) ────────────────────────
CREATE INDEX log_events_session_ts   ON log_events (session_id, ts);
CREATE INDEX log_events_ts_brin      ON log_events USING brin (ts);
CREATE INDEX hook_events_session_ts  ON hook_events (session_id, ts);
CREATE INDEX hook_events_ts_brin     ON hook_events USING brin (ts);
CREATE INDEX spans_session_ts        ON spans (session_id, ts);
CREATE INDEX spans_exec_use_id       ON spans (tool_use_id) INCLUDE (success, ts)
                                       WHERE span_kind = 2;
CREATE INDEX metric_points_session_ts ON metric_points (session_id, ts);
CREATE INDEX cost_daily_session      ON cost_daily (session_id);
CREATE INDEX lines_daily_session     ON lines_daily (session_id);
CREATE INDEX active_time_session     ON active_time_daily (session_id);
CREATE INDEX turns_daily_session     ON turns_daily (session_id);
CREATE INDEX tool_facts_session      ON tool_facts_daily (session_id);
CREATE INDEX rollup_dirty_marked     ON rollup_dirty (marked_at);

-- ── Hourly invocation rollup and skill lookup (see §6.6) ─────────────────────────
-- One row per (hour, session, kind, name): kind 1 tool (success from the paired tool.execution span),
-- 2 skill (tool span skill_name), 3 agent (tool span subagent_type), 4 slash command (user_prompt).
-- Serves tool success / tool usage / invocations for the full hours inside a window; the partial
-- hours at both window edges are read from raw, so results stay exact.
CREATE TABLE IF NOT EXISTS invocations_hourly (
    hour       timestamptz NOT NULL,
    session_id text        NOT NULL,
    kind       smallint    NOT NULL,
    name       text        NOT NULL,
    calls      integer     NOT NULL,
    success    integer     NOT NULL,
    PRIMARY KEY (hour, session_id, kind, name)
);
CREATE INDEX IF NOT EXISTS invocations_hourly_session ON invocations_hourly (session_id);

CREATE INDEX IF NOT EXISTS log_events_skill_activated ON log_events (session_id) INCLUDE (skill_name)
    WHERE event_kind = 3;
CREATE INDEX IF NOT EXISTS log_events_commands ON log_events (ts) INCLUDE (session_id, command_name)
    WHERE event_kind = 2 AND command_name IS NOT NULL;
CREATE INDEX IF NOT EXISTS spans_tool_cover ON spans (ts)
    INCLUDE (session_id, tool_name, tool_use_id, skill_name, subagent_type) WHERE span_kind = 1;

-- Distinct activated skills per session (delivery-framework classification input).
CREATE TABLE IF NOT EXISTS session_skills (
    session_id text NOT NULL,
    skill_name text NOT NULL,
    PRIMARY KEY (session_id, skill_name)
);

-- ── Partition maintenance (in production: app scheduler job or pg_partman) ─────────
CREATE OR REPLACE FUNCTION ensure_partitions(p_from date, p_to date) RETURNS void
LANGUAGE plpgsql SET search_path = codemie_analytics, pg_temp AS $$
DECLARE
    t text; w date; m date;
BEGIN
    FOREACH t IN ARRAY ARRAY['log_events','hook_events','spans','metric_points'] LOOP
        w := date_trunc('week', p_from)::date;
        WHILE w < p_to LOOP
            EXECUTE format('CREATE TABLE IF NOT EXISTS %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
                           t || '_w' || to_char(w, 'IYYY"_"IW'), t,
                           w::text || ' 00:00:00+00', (w + 7)::text || ' 00:00:00+00');
            w := w + 7;
        END LOOP;
        EXECUTE format('CREATE TABLE IF NOT EXISTS %I PARTITION OF %I DEFAULT', t || '_default', t);
    END LOOP;
    w := p_from;
    WHILE w < p_to LOOP
        EXECUTE format('CREATE TABLE IF NOT EXISTS %I PARTITION OF ingest_dedup FOR VALUES FROM (%L) TO (%L)',
                       'ingest_dedup_d' || to_char(w, 'YYYYMMDD'), w, w + 1);
        w := w + 1;
    END LOOP;
    EXECUTE 'CREATE TABLE IF NOT EXISTS ingest_dedup_default PARTITION OF ingest_dedup DEFAULT';
    FOREACH t IN ARRAY ARRAY['cost_daily','lines_daily','active_time_daily','turns_daily',
                             'tool_facts_daily','session_files_daily'] LOOP
        m := date_trunc('month', p_from)::date;
        WHILE m < p_to LOOP
            EXECUTE format('CREATE TABLE IF NOT EXISTS %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
                           t || '_m' || to_char(m, 'YYYYMM'), t, m, (m + interval '1 month')::date);
            m := (m + interval '1 month')::date;
        END LOOP;
        EXECUTE format('CREATE TABLE IF NOT EXISTS %I PARTITION OF %I DEFAULT', t || '_default', t);
    END LOOP;
END $$;

-- The work queue is updated in place: leave room on each page for HOT updates.
ALTER TABLE rollup_dirty SET (fillfactor = 70);
```

## Appendix B — ClickHouse → PostgreSQL mapping

| ClickHouse | PostgreSQL | Note |
| --- | --- | --- |
| `uniqExact(x)` | `count(DISTINCT x)` | |
| `argMax(a, b)` | `(array_agg(a ORDER BY b DESC, <key cols>))[1]` | ClickHouse keeps the first maximum in scan order |
| `argMinIf(a, ts, a != '')` | `(array_agg(a ORDER BY ts, ingest_seq) FILTER (WHERE a <> ''))[1]` | ties → first ingested |
| `groupUniqArray(x)` | `array_agg(DISTINCT x)` | order not relied upon |
| `countIf(c)` / `sumIf(x, c)` | `count(*) FILTER (WHERE c)` / `sum(x) FILTER (WHERE c)` | |
| `any(x)`, `anyLast(x)` | `max(x)` | one value per group in every use |
| `uniqExactState` / `uniqExactMerge` | `session_files_daily` rows + `count(*)` over distinct paths | exact across days |
| `toDate(ts)` | `(ts AT TIME ZONE 'UTC')::date` | explicit UTC |
| `dateDiff('millisecond', a, b)` | `floor(extract(epoch FROM b)*1000) - floor(extract(epoch FROM a)*1000)` | boundary counting |
| `intDiv(a, b)` | `a / b` on bigint | truncation toward zero |
| `ilike(a, p)` | `a ILIKE p` | |
| `Map['k']` (missing → `''`) | typed column (missing → `NULL`) + `<> ''` predicates | `NULL <> ''` is not true, so filters match |
| `x IN {p:Array(String)}` | `x = ANY($n::text[])` | |
| `{p:DateTime64(3)}` | parameter truncated to milliseconds | |
| String `max()` | `max(x COLLATE "C")` | byte order |
| `LEFT JOIN` non-match → `''`/`0`/`1970-01-01` | `NULL` | reproduce only where the API shows it (D2) |
| `MATERIALIZED` column | column set at ingest | |
| Materialized view (synchronous) | refresher job (≤ interval) | |
| `SummingMergeTree` / `AggregatingMergeTree` | table with PK, slice recompute | |
| `TTL` | partition drop job | |

## Appendix C — Sources

- OTel Collector PostgreSQL exporter donation: https://github.com/open-telemetry/opentelemetry-collector-contrib/issues/46501
- Claude Code telemetry reference: https://code.claude.com/docs/en/monitoring-usage
- TimescaleDB editions (Apache-2 vs Community/TSL features): https://www.tigerdata.com/docs/about/latest/timescaledb-editions
- Azure Database for PostgreSQL extensions (TimescaleDB Apache-2 edition, pg_partman, pg_cron): https://learn.microsoft.com/en-us/azure/postgresql/extensions/concepts-extensions-considerations
- pg_partman and pg_cron on Amazon RDS: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL_Partitions.html, https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL_pg_cron.html
- Cloud SQL extensions (no TimescaleDB; pg_partman and pg_cron available): https://docs.cloud.google.com/sql/docs/postgres/extensions
- Amazon RDS for PostgreSQL extension versions (no TimescaleDB; pg_partman and pg_cron available): https://docs.aws.amazon.com/AmazonRDS/latest/PostgreSQLReleaseNotes/postgresql-extensions.html
