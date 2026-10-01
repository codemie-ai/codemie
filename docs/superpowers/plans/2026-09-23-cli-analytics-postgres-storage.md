# CLI Analytics PostgreSQL Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a PostgreSQL storage adapter for OTel CLI Analytics next to ClickHouse. One setting selects the engine, and the API, UI and plugin contracts stay unchanged.

**Architecture:** Two ports sit at the repository boundary: a reader with the 27 fact queries the handler already calls, and an ingestor that accepts OTLP bodies and hook events. The ClickHouse adapter is today's code moved behind the ports. The PostgreSQL adapter:
- decodes OTLP in the API;
- writes typed, partitioned raw tables with a per-record idempotency ledger;
- keeps the ClickHouse rollup grain through a background refresher that recomputes dirty `(day, session)` keys.

**Tech Stack:** Python 3.12, FastAPI, asyncpg 0.30 (raw pool), opentelemetry-proto/protobuf, orjson, Alembic (separate environment), APScheduler, PostgreSQL 14+ (validated on 17).

**Spec:** `docs/superpowers/specs/2026-09-23-cli-analytics-postgres-storage-design.md`. The PoC scripts mentioned below were not kept once the implementation landed; the spec (§7) keeps their results. This plan records how the work was planned and decided; the code as built is described in the handoff, `docs/superpowers/handoffs/2026-09-24-cli-analytics-postgres-storage-handoff.md`.

## Global Constraints

- `CLI_ANALYTICS_STORAGE_BACKEND=clickhouse|postgres`, default `clickhouse`. The ClickHouse deployment behaves as before, with deterministic ordering (D1/D3) as the only visible change.
- API paths, auth, content types, size limit (413), status classes and response JSON stay the same on both engines.
- A PostgreSQL deployment needs neither ClickHouse nor the OTel Collector. It must never create a ClickHouse client.
- PostgreSQL 14+, no extensions. SQL uses bind parameters only; the only strings built into SQL are code constants and validated identifiers.
- Analytics uses its own asyncpg pool (`CLI_ANALYTICS_PG_POOL_SIZE`), never the application pool.
- Ingest p99 must stay well under 2 s: the plugin's hook `curl --max-time 2` drops events for 30 minutes after a timeout.
- The row contract toward the handler:
  - naive-UTC `datetime` values;
  - `date` for days;
  - `int` for counts and `float` for money;
  - `list[str]` for arrays;
  - `''` or `None` for missing strings, exactly as ClickHouse returns them.
- No new third-party dependencies: `xxhash` is only transitive, so hashing uses `hashlib.blake2b(digest_size=8)`.
- Existing tests keep their assertions. Only import and patch paths change where code moved.
- Git: work on branch `cli-analytics-postgres-storage`, renamed `EPMCDME-15253_cli-analytics-postgres-storage` once ticket EPMCDME-15253 was created. No commits without an `EPMCDME-####` ticket (AGENTS.md).
- Tests: `tests/` mirrors `src/`. Async tests use `@pytest.mark.asyncio`. Tests that need a real database or ClickHouse are kept out of the repository (see "Final cleanup" below); the committed tests need neither.

## Decisions taken for spec §10 and deviations from the spec

| Item | Decision | Why |
| --- | --- | --- |
| §10.1 D2 (1970 start time) | PostgreSQL reproduces ClickHouse's `1970-01-01T00:00:00` for sessions without plugin data | The user requires an unchanged API. The handler's `trace_id` tie-breaker (D1) already makes paging deterministic. |
| §10.2 topology | Any DSN via `CLI_ANALYTICS_PG_URL`; empty falls back to the main `PG_URL`/`POSTGRES_*` settings. Local runs use database `codemie_analytics` on the same server. | Spec recommendation |
| §10.3 freshness | Refresh every 30 s | Spec default |
| §10.4 raw retention | Keep all raw data 90 days | Spec default, configurable |
| §10.5 plugin | Out of scope | Separate repository |
| Hash | blake2b-64 instead of xxh64 | No new dependency. The collision odds per day at 500 users are about 6·10⁻⁹. |
| Refresher claim | **Versioned claim**: read keys and their `version` without locks, recompute, then `DELETE ... WHERE version = claimed`. The spec's variant held `FOR UPDATE` locks for the whole recompute. | The spec's protocol makes ingest of an active session wait for the whole refresher transaction (seconds), against a 2 s client limit. With the versioned claim, ingest waits only for the final delete. It is equally lossless: see the Task 8 proof. |
| Ingest statements | One ordered ledger `INSERT ... RETURNING` for all records of a request, then per-table inserts, then one ordered dirty-key upsert computed in Python | Gives a consistent lock order across concurrent requests (no deadlocks with grown or replayed batches). The dirty-key logic becomes unit-testable. |
| Partitions | Created, moved and dropped by Python (`maintenance.py`) instead of the PoC's plpgsql function | Unit-testable. The schema name is configurable. |
| D10 | ClickHouse reader splits session ids into chunks under a byte budget | Keeps the port signature. Fixes `/sessions` above ~3,250 sessions. |
| D11 | ClickHouse session timeline and dispatch unions are wrapped in a subquery, so the `ORDER BY` covers the whole union | Found by comparing both engines' answers (Task 13): ClickHouse orders only the last `SELECT` of a `UNION ALL`. PostgreSQL already ordered the whole union. |
| Code review (CR-002) | Reader rows stay `list[dict]`; no per-method row TypedDicts | The row contract is enforced where it matters: the router's response models, and a comparison of both engines' answers through the handler. No type checker runs in the gate, so TypedDicts would be unenforced annotations. |
| Code review (CR-018) | Operational signals are log lines for now: the stale-queue warning from the 30 s refresh job (rate-limited), `dropped rollup key` and maintenance-step errors, DEFAULT-partition rows. Metrics, dashboards and alert rules (spec §6.8, Phase 4.3) are a follow-up. | They depend on a platform decision about the metrics backend; the log lines are alertable today. |
| Code review (CR-026) | The PostgreSQL SQL is checked against a real server by tests kept out of the repository; `make test` stays database-free. Every windowed reader method is checked against each filter kind. | The repository has no pipeline, and `make test` must run without Docker. |
| Code review (CR-008) | Dedup window: 14 days from the record's day for on-time data; a record delivered later lands in the ledger's DEFAULT partition and is kept 14 days from first delivery (`first_seen`, migration `a1c1a0000002`). | Keeping the whole ledger for raw retention would multiply its size by six; re-sends happen within hours of the first delivery. |
| Code review (CR-024) | `features:cliAnalytics` is read once at startup | It comes from YAML or the environment and is not a dynamic setting; a test fails if it is ever declared dynamic. |
| Code review (CR-001) | `docker-compose.yml`: `shm_size: 256mb` on `postgres`; no compose profile. Guide: `.ai-run/guides/integration/cli-analytics-storage.md`. | A profile would stop ClickHouse and the Collector from starting by default for everyone; `docker compose up -d codemie` already runs without them. |
| Code review 2 (CR-014, CR-001) | The storage states how far back its raw rows reach (`CliAnalyticsStorage.raw_retention_days`: ClickHouse 90, the schema TTL; PostgreSQL `CLI_ANALYTICS_RAW_RETENTION_DAYS`), and the router clamps windows to it. Config rejects a rollup retention shorter than the raw retention. | The router no longer branches on the backend (F1). A shorter rollup retention would read part of an allowed window as zero. |
| Code review 2 (CR-002, CR-003, CR-009) | The backfill command first re-derives the kind of rows stored as kind 0, then marks one day per statement; `--now` rebuilds only what was queued before it started. | Spec §6.7. Bounded statements. The command ends under live ingest. |
| Code review 2 (CR-004, CR-007, CR-008) | Ingest transactions get their own statement timeout (`CLI_ANALYTICS_PG_INGEST_STATEMENT_TIMEOUT_MS`, 10 s). Client strings are cut to byte caps (keys 256 B, paths 1 KiB). A failing rollup batch is split; a key that fails alone with a data error is dropped and logged (`dropped rollup key`). | A lock wait answers 503 within the plugin's retry budget; oversized values cannot fail ingest forever; one bad key cannot stop every rollup. |
| Code review 2 (CR-011, CR-012, CR-013) | `ssl=` in a URL becomes `sslmode=`. IAM tokens are minted for the analytics DSN's host, port and user: the `codemie.clients.postgres` token helpers take an optional endpoint, and their defaults are unchanged. Connections must be direct or session-pooled; this is documented, not checked at runtime. | asyncpg and libpq know only `sslmode`. AWS tokens are bound to an endpoint. The jobs rely on session advisory locks, startup parameters and prepared statements, and a client cannot reliably detect a transaction pooler. |
| Code review 2 (CR-015, CR-016) | Migrations run in a daemon thread. The `rollup_dirty_marked` index is added by migration `a1c1a0000003`. | Shutdown does not wait for a migration queued behind another pod's lock. Claims read the oldest keys without sorting the queue; ingest re-marks never touch `marked_at`, so they stay HOT. |
| Code review 3 (CR-003) | Every statement run under a raised `SET LOCAL statement_timeout` passes `timeout=` with the same 5 s margin (maintenance moves and purges, backfill reclassify, as the refresher already did); a client-side `TimeoutError` is isolated like a database error; the refresh job logs a failed maintenance retry and refreshes anyway. | Without `timeout=`, asyncpg applied the pool's 35 s limit, so the raised limits never took effect and one slow step aborted the whole run. |
| Code review 3 (CR-006, CR-007) | Plausibility ceilings at ingest and in the refresher: tokens 10^9 per request and kind, cost ±10^6 USD, span duration 30 days, counter readings 10^9 per column; beyond them a value counts as unparseable. Digit strings are measured before `int()`; any other `ValueError` in a decoder is a 400. | No client can make a dashboard sum overflow `bigint`/`float8` for the whole retention. Deliberate divergence from ClickHouse for absurd values only, which ClickHouse stores and wraps. |
| Code review 3 (CR-002) | A record the database rejects is isolated by splitting the request, dropped, logged and entered in the ledger; the rest is stored; 200 with OTLP `partial_success`. Class-23 errors (such as "no partition for the row") stay 503. | The proxy re-sends every non-2xx answer with its whole spool, so a 400 blocked that client's telemetry for good. |
| Code review 3 (CR-004) | `otel_resources.last_seen` (migration `a1c1a0000004`), refreshed by each process at most once a day; maintenance purges resources not seen within the raw retention. | Resource attributes carry user emails; on ClickHouse they expire with each row. |
| Code review 3 (CR-005, CR-008, CR-009) | The schema is looked up before `CREATE SCHEMA`. Refresher: `lock_timeout` 30 s; a timed-out batch is retried key by key until the run's deadline; a key timing out alone waits at the back of the queue and is dropped after 3 timeouts in a row. DSN: only connection parameters are kept, with a warning naming the dropped ones. | A role given only a schema could not migrate; transient slowness no longer drops keys, and a slow key is found in one pass; parameters asyncpg would send as server settings, or libpq would reject, no longer break connections. |
| Code review 3 (CR-001, CR-010) | The spec gains §6.11, listing the deviations adopted during implementation, and its §6.3/§6.10 describe the error mapping built. A default-gate test audits every windowed PostgreSQL query: each branch reading a fact table carries the session scope or is correlated to a scoped query. | The spec now matches the code; a branch losing its scope fails `make test`, not only the opt-in suite. |
| Code review 3, deferred | Unchanged on both engines: `/logs` hook records keep the payload's `user.email`. Fixed on both although pre-existing: an NDJSON line with an integer too long to convert is skipped instead of failing `/event-hooks` with a 500. | The first is pre-existing ClickHouse behaviour (spec F2); the second was a one-line change in the parser this change owns. |
| Code review 4 (CR-008, CR-009, CR-002) | A batch timing out leaves its keys to be taken one at a time by the next runs, the first of each run whatever the deadline; a key timing out alone is deferred 10 minutes times its timeouts (claims read only due keys) and dropped after 3 in a row, whatever its version. `refresh()` returns keys actually recomputed; `backfill --now` stops after two runs in a row without progress. | The review-3 design never progressed: a 5-minute timeout always arrives after the 2-minute run deadline. Backoff keeps passing load from running up strikes. |
| Code review 4 (CR-010, CR-011, CR-007) | The migrations' URL keeps every libpq parameter; the pool's keeps asyncpg's; a stricter security setting asyncpg cannot apply refuses the storage (its endpoints fail, the application starts); the drop warning names only well-formed parameter names. The TEMPORARY privilege is documented and the restricted-role test runs the whole adapter. | A single allowlist dropped `channel_binding`/`require_auth` from the migrations too, silently weakening them; a malformed URL could log a password fragment. |
| Code review 4 (CR-003, CR-004, CR-005, CR-006) | Rejected-record isolation spends at most 64 failed attempts per request, then rejects failing parts whole; the ledger comes first, so a re-send brings nothing else. Each expired-partition drop is isolated. Zero-padded digit strings parse like toUInt64OrZero. Counter readings are bounded before they are scaled. | Bounded cost per request; retention cannot stop at one partition; no 400 and no dropped rollup key from values a client controls. |
| Code review 4 (CR-001) | Spec body aligned with §6.11 (status line, §5.4, §6.1, §6.5, §8, Phases 1.5, 2.3, 4.2, 5.1). | The owner's sign-off on §6.11 is a decision for the team, not for the implementation. |
| Code review 5 (CR-004) | The refresher's timeout state lives in `rollup_dirty` (`alone`, `timeouts`; migration `a1c1a0000005`, partial index on `marked_at WHERE alone`): a timed-out batch's keys are flagged in one sorted-lock UPDATE and taken one at a time by whichever pod runs next; strikes survive restarts and reset when a recompute succeeds. | Per-pod memory let other pods claim a timed-out batch whole again, 5 minutes each time. |
| Code review 5 (CR-003) | Past 64 failed isolation attempts a request answers 503, keeping what it stored and rejected; only records isolated one by one enter the ledger. | Rejecting the rest whole lost valid records; answering 200 without the ledger loses them too, as the plugin deletes its spool on any 2xx. |
| Code review 5 (CR-005, CR-006, CR-007, deferred miscased names) | `CliAnalyticsStorageConfigError` (not a ValueError): startup logs it and carries on, and every analytics endpoint answers 503. URLs with `@` after `?` or an unreadable port are refused without quoting them; `hostaddr`/`service` are refused, `options`/`load_balance_hosts`/`replication` left out of both connections; miscased parameter names are refused. | A refused URL answered 400 or 500; both connections must reach the same server as the same role; a miscased `SSLMODE` silently fell back to `prefer`. |
| Code review 5, deferred | Unchanged on both engines: negative counter readings are summed (a sign check must be decided with ClickHouse); class-53 resource errors (disk full, `temp_file_limit`) make the refresher retry the same batch until the resource is back. | Pre-existing; parity and operational alerts (backlog warning) cover them for now. |
| Code review 6 (CR-001 and the deferred URL items) | A URL is refused (fixed message, nothing quoted) unless asyncpg, SQLAlchemy and urllib read the same user, password, host and port from it; also refused: a `#` anywhere, a connection part set both before `?` and as a parameter, a non-numeric port. Unknown parameters are counted in the warning, never named. A driver refusing its settings on connect becomes `CliAnalyticsStorageConfigError` (503). | A raw `@` in a password sent asyncpg and the migrations to another host than IAM's, and libpq's error then quoted the password's tail on every retry. |
| Code review 6 (CR-002) | Integration tests assert the strike reset of a key recomputed in time and the 20-minute second deferral; each was mutation-checked. | Both transitions are SQL only; no test failed when they regressed. |
| Code review 7 (CR-002 to CR-005, and the deferred database path) | The three-reader agreement check is replaced: the URL is read once, strictly, as libpq reads it, and each driver's URL is rebuilt from that reading, fully percent-encoded. `user`, `password`, `host`, `port` and `dbname` given as parameters are folded in where the URL leaves them out (CR-003 advised refusing them; the rebuilt URLs make both drivers read them alike, which a test checks with asyncpg's and SQLAlchemy/psycopg2's own parsers). Refused: several hosts, a host that is not a name or an IP address, a raw `?` before the user's `@`, a bad `%` escape, control characters, asyncpg's `database=`. IAM reads the same folded parts; IAM with a socket is refused. | Each client split the URL its own way: parameters, a percent-encoded socket host, `h1,h2` and query parts escaped the check, and SQLAlchemy passed a database name's escapes on (the migrations reached `code%20mie`). |
| Code review 7 (CR-001, and the deferred certificate files) | A connect failure that is an `OSError` (network, TLS, a certificate file) passes through the engine; the dashboards answer it 503, like ingest. A configuration error names the driver's error class only. | `SSLCertVerificationError` is also a `ValueError`: it was reported as a misconfiguration, and once let through, the dashboards answered it 400 with the TLS message. |
| Code review 8 (CR-001, CR-002, CR-005) | A port without a host is refused: asyncpg cannot pair a URL's port with its default hosts, and dials an authority's `:port` as TCP `""`, while libpq uses its socket. An `@` is accepted only once, before the host: anywhere else a password's raw `/` or `?` turns its fragments into a port, a database or parameters (CR-002 kept a raw `@` in `user=`/`password=` values; that still let `/x?user=` through, so `alice%40corp.com` must be escaped). `%00` and known names padded with spaces are refused. The agreement test asserts what each driver reads, not only that they agree. | Round 7's rebuild dropped such a port; the designator rule missed a raw `/` before the `?`; both drivers could agree on a wrong decoding. |
| Code review 8 (CR-003, CR-004) | Only AWS IAM needs a network host and a user in the URL; GCP and Azure keep sockets (a Cloud SQL Auth Proxy) and user-less URLs. | An AWS token is signed for one endpoint and user, POSTGRES_*'s when the URL names none; GCP and Azure tokens are signed for neither. |
| Code review 8, deferred | Unchanged: IAM does not force TLS on URL-configured connections (as PostgresClient with `PG_URL`); `PG*` environment variables fill parts a URL leaves out, which the reading and `iam_endpoint` ignore; dashboards answer 500 for connect failures that are not OSErrors (`TooManyConnectionsError`, a rejected token, a token that cannot be minted). | Pre-existing, in line with PostgresClient; ingest already answers 503 for them. |
| Code review 9 (CR-001, and a deferred `database=` item) | A connection without a host is refused (URL, `PG_URL` or `POSTGRES_HOST=""`); this subsumes round 8's port rule. `database=` in any case or padding is refused like the exact name. | Without a host, asyncpg tries four socket directories and then TCP localhost while libpq tries one socket directory; a dropped `Database=` sent both connections, and the migrations' schema, to the user's default database. |
| Code review 9, deferred | Unchanged: near-miss TLS names (`ssl_mode`, `sslmode+`) are dropped and counted, per the unknown-parameter policy (driver-specific parameters in a reused `PG_URL`); with AWS IAM, a URL without a port and a set `PGPORT` get a token signed for 5432 (logins fail). | Pre-existing, documented policy; the failure is loud. |
| Final cleanup (2026-09-24) | Removed the compatibility module `cli_analytics_repository.py` and its tests (its six older tests moved next to the modules they test); folded migrations `a1c1a0000002`-`a1c1a0000005` into the initial one (catalog compared: identical schema); removed `alembic.ini` and `script.py.mako` (migrations run only through `run_migrations()`); reverted the `.env.example`, `tests/.env.test` and `docker-compose.yml` changes. Then moved the tests that need live ClickHouse or PostgreSQL servers (engine parity, API end to end, database integration), their helpers and the benchmark out of the repository: they are local tooling. | The schema had never shipped; nothing read those files; `.env.example` holds a fixed key set by design; the `.env.test` pin changed no test result; every run used the default `/dev/shm`. |
| Code review 10 (approve), deferred | Unchanged, each pre-existing: a socket *file* as `host=` (`/cloudsql/.../.s.PGSQL.5432`) works for asyncpg but not libpq, so the migrations fail at startup; a near-miss `dbname` (`dbnme=`) is dropped per the unknown-parameter policy, leaving the user's default database; `PGHOSTADDR`/`PGSERVICE` in the environment reach only the migrations. | Loud failures or environment-level settings outside the URL; the reading covers the URL. |
| Code review 2, deferred | Unchanged on both engines: a branch-only `/sessions` filter does not scope fact queries, and client-asserted `user.email` and project drive session attribution. | Pre-existing in the ClickHouse path; spec F2 requires the same answers from both engines. |
| Pre-existing, both engines | Unchanged: OTLP logs carrying `event_type` count as hook events with the client's `user.email`; an unmatched dispatch keeps a negative `real_duration_ms` (ClickHouse's LEFT JOIN default). Diverges on purpose: a non-finite `cost_usd` counts as 0 in PostgreSQL. | Changing the first two in one engine would break the shared API; ClickHouse turns the third into NaN totals that cannot be sent as JSON. |
| D1 / D3 | SQL tie-breakers on top-N and ordered lists in both readers. Handler tie-breakers for the sessions, repositories, efficiency and invocations sorts. The repository label is the most frequent project, ties alphabetical. | Spec Phase 0 |
| D4 / D6 / D8 / D9 | D4 unchanged in ClickHouse. D6 fixed in PostgreSQL by the ledger. D8 and D9 unchanged. | Out of Phase 0 scope |
| OTLP/JSON | Accepted by the PostgreSQL ingestor; hex trace/span ids are converted. Gzip is not decoded (unchanged from today). | Spec Phase 1.5; the plugin never compresses |

## File structure

```
src/codemie/repository/cli_analytics/
    __init__.py
    filters.py            LocalAnalyticsFilter (moved) + deny_all (D5)
    ports.py              CliAnalyticsReader, CliTelemetryIngestor protocols; IngestResult; ingest errors
    vocabulary.py         canonical kinds, metric names, hook types/fields, sentinels
    hook_events.py        shared hook NDJSON helpers: safe_str, timestamp_to_ns
    factory.py            CliAnalyticsStorage + get_cli_analytics_storage() / reset / aclose
    clickhouse/
        __init__.py
        reader.py         LocalAnalyticsRepository (moved) + tie-breakers + deny_all + D10 chunking
        ingestor.py       ClickHouseTelemetryIngestor (_forward, event_to_log_record, build_otlp_logs_payload)
    postgres/
        __init__.py
        settings.py       AnalyticsPgSettings (from config) + DSN building
        engine.py         AnalyticsPgEngine: lazy asyncpg pool, server settings, IAM password, advisory lock helper
        otlp.py           pure decoding/normalisation/sanitising/hashing → typed row tuples
        ingestor.py       PostgresTelemetryIngestor: ledger, unnest inserts, dirty keys, error mapping
        reader.py         PostgresCliAnalyticsReader: the 27 queries (PoC v2 + edges)
        rollups.py        RollupRefresher: versioned claim + recompute SQL
        maintenance.py    PartitionMaintainer: plan/create/move/drop partitions, retention purges
        migrations.py     run_migrations(): Alembic upgrade for the analytics schema under an advisory lock
src/codemie/repository/cli_analytics_repository.py   compat shim (re-exports); removed in the final cleanup
src/codemie/service/analytics/cli_analytics_jobs.py  CliAnalyticsJobsScheduler (APScheduler)
src/external/alembic_cli_analytics/                  env.py, versions/ (one migration)
src/codemie/configs/config.py                        CLI_ANALYTICS_* settings (+ masked URL)
src/codemie/rest_api/routers/cli_analytics.py        lazy storage wiring; ingest via port; deny_all
src/codemie/rest_api/main.py                         startup/shutdown for the PostgreSQL backend
src/codemie/service/analytics/handlers/cli_analytics_handler.py  D1/D3 tie-breakers, reader typing
tests/codemie/repository/cli_analytics/...           unit tests
tests/codemie/repository/cli_analytics/support/      OTLP builders and port contract checks for the tests
```

---

### Task 0: Branch and baseline

- [x] `git checkout -b cli-analytics-postgres-storage`. The user's unrelated uncommitted changes stay in the working tree untouched.
- [x] Baseline: run `pytest -n 0 tests/codemie/repository/test_cli_analytics_repository.py tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py tests/codemie/rest_api/routers/test_cli_analytics_ingest.py tests/codemie/rest_api/models/test_cli_analytics_models.py tests/codemie/clients/test_clickhouse.py`. Expected: all pass. Record the count.

### Task 1: Package seam: filters (D5), vocabulary, hook helpers, ports

**Files:** create `filters.py`, `vocabulary.py`, `hook_events.py`, `ports.py` and `__init__.py`. Modify `cli_analytics_repository.py` into a shim.

**Interfaces (produced):**
- `LocalAnalyticsFilter(start_dt, end_dt, users=None, projects=None, repositories=None, branch=None, deny_all=False)`. `has_session_filter` is `True` when `deny_all` is set; `params()` never contains NUL.
- `vocabulary`: `SpanKind`/`EventKind` int constants, `SPAN_KIND: dict[str,int]`, `EVENT_KIND`, `LINES_METRICS`, `ACTIVE_TIME_METRICS`, `ROLLUP_METRICS`, `DIMENSION_HOOK_TYPES`, `HOOK_TEXT_FIELDS`, `HOOK_KNOWN_KEYS`, `SESSION_SCOPED_KEYS`, `SENTINEL_EXACT`, `SENTINEL_PREFIX_TRIMMED`, `SENTINEL_PREFIX_RAW`, `SKILL_DISPATCH_EVENT = "agent.skill.dispatch"`, `WRITE_TOOLS`, `EDIT_TOOLS`.
- `hook_events.safe_str(val) -> str` (same as the router's `_safe_str`) and `hook_events.timestamp_to_ns(event, now_ns) -> int` (same as `_ts_to_ns`).
- `ports.CliAnalyticsReader` (Protocol, 27 async methods with today's signatures).
- `ports.CliTelemetryIngestor` (Protocol): `ingest_otlp(signal, body, content_type) -> IngestResult` and `ingest_hook_events(events, user_email, received_at_ns) -> IngestResult`.
- `IngestResult(body: bytes, media_type: str | None, accepted: int)`.
- Errors: `IngestError` (base) → `InvalidTelemetryPayloadError` (400), `UnsupportedTelemetryContentTypeError` (415), `TelemetryStorageUnavailableError` (503), `TelemetryUpstreamConfigurationError` (502).

- [x] Tests first, in `tests/codemie/repository/cli_analytics/test_filters.py`:
  - `deny_all` makes `has_session_filter` true;
  - `params()` lower-cases users and keeps branch;
  - no NUL anywhere in params.
- [x] Tests first, in `test_hook_events.py`:
  - `safe_str` handles None, dict and list;
  - `timestamp_to_ns` handles `Z`, offsets, missing values and garbage (falls back to `now_ns`);
  - microsecond precision survives.
- [x] Implement. The shim `cli_analytics_repository.py` re-exports `LocalAnalyticsFilter`, `LocalAnalyticsRepository`, `QueryFn` and `depth_bucket`.
- [x] Run the new tests plus the baseline set. All must be green.

### Task 2: ClickHouse reader moved + D1 tie-breakers + deny_all + D10

**Files:** create `clickhouse/reader.py` (the class moves from the old module). Test file: `tests/codemie/repository/cli_analytics/clickhouse/test_reader.py`.

- [x] Tests first:
  - `deny_all` makes the CTE `WHERE 0` and the facts scoped to `sel`;
  - tie-breakers: model breakdown `…, model_name`; cost by user and users `…, developer_name`; tool usage and detail tools `call_count DESC, tool_name`; dispatches `span_start, is_slash_command, subagent_type, skill_name`; events `timestamp, event_type, tool_name`;
  - D10: 5,000 ids of 36 chars → several `_q` calls, each `session_ids` parameter under the byte budget, rows merged, no id lost or duplicated;
  - an empty list makes no call.
- [x] Implement. SQL text is otherwise unchanged.
- [x] Existing `test_cli_analytics_repository.py` stays green through the shim.

### Task 3: ClickHouse ingestor moved behind the port

**Files:** create `clickhouse/ingestor.py`. Modify the router. Update only the patch and import paths in `tests/codemie/rest_api/routers/test_cli_analytics_ingest.py`.

- [x] Tests first, in `tests/.../clickhouse/test_ingestor.py`:
  - `ingest_otlp("logs", …)` posts to `{endpoint}/v1/logs` with the content type;
  - retries 5xx `_MAX_ATTEMPTS` times, then raises `TelemetryStorageUnavailableError`;
  - collector 4xx → `TelemetryUpstreamConfigurationError`;
  - hook events → OTLP/JSON with `user.email`.
- [x] Router maps the port errors to the same `HTTPException` status and detail text as today: 503 "Analytics collector unavailable", 502 "Analytics collector configuration error".
- [x] The full existing ingest test file passes with updated patch targets only.

### Task 4: Config, factory and lazy router wiring

**Files:** modify `config.py` and the router. Create `factory.py`. Tests: `test_factory.py` and router tests for the PostgreSQL ingest path.

- [x] Settings, with validation of schema identifier and `work_mem` format; `CLI_ANALYTICS_PG_URL` is masked in `to_safe_dict`:
  - `CLI_ANALYTICS_STORAGE_BACKEND`, `CLI_ANALYTICS_PG_URL`, `CLI_ANALYTICS_PG_SCHEMA`, `CLI_ANALYTICS_PG_POOL_SIZE`;
  - `CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS`, `CLI_ANALYTICS_PG_WORK_MEM`, `CLI_ANALYTICS_PG_INGEST_ACQUIRE_TIMEOUT_MS`;
  - `CLI_ANALYTICS_RAW_RETENTION_DAYS`, `CLI_ANALYTICS_ROLLUP_RETENTION_DAYS`, `CLI_ANALYTICS_DEDUP_RETENTION_DAYS`;
  - `CLI_ANALYTICS_ROLLUP_REFRESH_SECONDS`, `CLI_ANALYTICS_ROLLUP_BATCH_SIZE`;
  - `CLI_ANALYTICS_PARTITION_PREMAKE_WEEKS`, `CLI_ANALYTICS_MAINTENANCE_INTERVAL_MINUTES`.
- [x] Factory tests:
  - `clickhouse` yields the ClickHouse reader and ingestor and no jobs;
  - `postgres` yields the PostgreSQL ones and never touches `codemie.clients.clickhouse.get_client`;
  - it is a singleton; `reset` exists for tests.
- [x] Router:
  - `_storage()` and `_handler()` are lazy;
  - `FilterParams.resolve` sets `deny_all=True` instead of the NUL sentinel;
  - session detail scoping reads through the port;
  - ingest endpoints call the port and map errors to 400/415/502/503.

### Task 5: PostgreSQL settings + engine

**Files:** `postgres/settings.py`, `postgres/engine.py`. Tests: `test_settings.py`, `test_engine.py`.

- [x] Tests first:
  - DSN from `CLI_ANALYTICS_PG_URL`: accepts `postgresql://`, `postgresql+asyncpg://` and `postgres://`; maps `sslmode`;
  - fallback to `PG_URL`, then `POSTGRES_*` with quoting;
  - IAM: no password in the DSN, and the password is a callable run in a thread;
  - server settings are `search_path=<schema>`, `application_name`, `work_mem`, `statement_timeout`;
  - the pool is created lazily once, concurrent first calls create one pool, and `close()` is idempotent;
  - the sync SQLAlchemy URL for Alembic is correct;
  - the advisory-lock helper returns `False` when the lock is not acquired and always unlocks.

### Task 6: OTLP decoding (pure)

**Files:** `postgres/otlp.py`; test support `tests/.../support/otlp_builders.py`. Test: `test_otlp.py`.

**Interfaces:** `decode_otlp(signal, body, content_type) -> DecodedBatch`, `decode_hook_events(events, user_email, received_at_ns) -> DecodedBatch`. `DecodedBatch` holds:
- `log_rows`, `hook_rows`, `span_rows` and `metric_rows`, lists of `(day, hash, *columns)` tuples;
- `resources: dict[int, (service_name, attrs_json)]`;
- `session_attrs: dict[str, dict]`.

- [x] Golden tests (from spec §6.3 and the §7.6 list):
  - `as_str` renders like pcommon `AsString`: `5.0→"5"`, `1e21`, bool, int, arrays and maps as JSON, bytes as base64;
  - `ch_uint`/`ch_float` parse like the ClickHouse `…OrZero` functions and keep unparseable originals in `attrs`;
  - `clean` strips NUL and replaces lone surrogates with U+FFFD in keys and values;
  - concatenated protobuf bodies merge into one batch;
  - an OTel log carrying `event_type` goes to `hook_rows`;
  - `session.id` is preferred over `session_id`, and `prompt.id` is the fallback for `prompt_id`;
  - span email falls back to the resource;
  - session-scoped keys are split out once per session;
  - resource ids are stable across attribute order;
  - hook identity excludes the JWT email;
  - span identity is `trace_id + span_id`;
  - metric sum and gauge are handled; histograms are skipped;
  - JSON bodies convert hex ids; invalid protobuf or JSON raises `InvalidTelemetryPayloadError`; an unsupported media type raises the 415 error;
  - an empty body gives an empty batch.

### Task 7: Schema migration + partition maintenance

**Files:**
- `src/external/alembic_cli_analytics/{env.py,versions/a1c1a0000001_cli_analytics_initial_schema.py}`;
- `postgres/migrations.py` and `postgres/maintenance.py`.

Tests: `test_maintenance.py` (unit), plus the same runs against a real PostgreSQL (not in the repository).

- [x] The DDL is spec Appendix A, with these changes:
  - `invocations_hourly` is partitioned monthly;
  - `rollup_dirty.version bigint`;
  - `session_skills` and `session_attributes` get `updated_at`;
  - rollup tables get `fillfactor=90`, `rollup_dirty` gets `fillfactor=70`;
  - no plpgsql function.
- [x] Unit tests, for pure planning functions:
  - week ranges start on Monday and names use `IYYY_IW`; month and day ranges;
  - the premake horizon;
  - expired partitions are chosen from existing names using the upper bound ≤ cutoff; default and unknown names are never dropped;
  - identifiers are quoted.
- [x] Integration tests:
  - migrate an empty schema twice (idempotent);
  - partitions exist for the retention window through premake;
  - rows in DEFAULT are moved when their partition is created;
  - an expired partition is dropped on a fake clock;
  - ledger and default purges work.

### Task 8: PostgreSQL ingestor

**Files:** `postgres/ingestor.py`. Tests: `test_ingestor.py` (unit, fake connection) and integration.

- [x] Unit tests:
  - one transaction per request;
  - statement order: ledger, then (for the records it accepted only) resources, session attributes, rows and dirty keys; each keyed statement is ordered;
  - only fresh `(day, h)` rows are inserted; duplicates inside one request are dropped;
  - dirty kinds: log kinds 1 to 3 give cost (1), hooks give dims (2), spans of kinds 1 to 3 give spans (4) plus the previous day when within the first hour after midnight, rollup metrics give metrics (8);
  - the acquire timeout gives `TelemetryStorageUnavailableError`, as do `PostgresConnectionError` and `OSError`;
  - a record the database rejects for its content (class 22, 54000) is isolated by halving, dropped and entered in the ledger; the rest is stored. Past 64 failed attempts the request answers 503 and the plugin's re-send carries on (Code review 3 to 5);
  - response body: empty protobuf for protobuf requests, `{}` for JSON; OTLP `partial_success` when records were rejected.
- [x] Integration tests:
  - a replayed request inserts 0 rows;
  - a grown batch (old records plus new ones, bodies concatenated) inserts only the new ones;
  - 8 concurrent writers produce no deadlock, and the row counts equal the distinct records.
- **Lossless proof for the versioned refresher claim:** every ingest transaction that inserts raw rows for key K also upserts K and bumps `version`, in the same transaction. The refresher deletes K only when `version` equals the value it read *before* recomputing. Take any raw row committed for K:
  - if it committed before a recompute statement's snapshot, the recompute includes it;
  - otherwise its transaction bumped `version` after the claim read, so the delete does not match and K is recomputed on the next run.

  If that transaction is still in flight when the delete runs, the delete waits on its row lock and then re-checks `version` (EvalPlanQual). No committed row can therefore be missed by the final state.

### Task 9: Rollup refresher

**Files:** `postgres/rollups.py`. Tests: unit (claim/delete statement protocol with a fake connection) and integration.

- [x] Integration tests:
  - after concurrent ingest plus periodic refresh, every rollup table equals a single-statement full recompute from raw (0 missing, 0 unexpected rows);
  - a key re-marked during a refresh stays queued;
  - backfill with `mark_dirty(from_day, to_day)` rebuilds the rollups.

### Task 10: PostgreSQL reader

**Files:** `postgres/reader.py`. Tests: unit (param building, `_norm`, `_trunc_ms`, `_hours`, deny_all SQL, named-to-positional conversion) and integration (hand-built dataset with exact expected values for all 27 methods, plus edge cases: window edges inside an hour, a session crossing midnight, a plugin-less session giving the 1970 start, ties).

### Task 11: Jobs and startup

**Files:** `service/analytics/cli_analytics_jobs.py` and `main.py`. Tests: `tests/codemie/service/analytics/test_cli_analytics_jobs.py`.

- [x] Tests:
  - with ClickHouse, nothing is scheduled;
  - with PostgreSQL, the refresher interval job (`max_instances=1`, `coalesce=True`) and the maintenance job are registered, and maintenance runs once at start;
  - each run takes the advisory lock and skips when it is not the leader;
  - exceptions are logged and do not propagate;
  - shutdown closes the scheduler and the pool.
- [x] Startup: a migration or partition failure is logged, not fatal; the maintenance job retries it.

### Task 12: Handler determinism (D1/D3)

**Files:** handler. Tests: extend `test_cli_analytics_handler.py` with new tests only.

- [x] Tests:
  - session sort ties are broken by `trace_id`;
  - repository ties by `(repository, branch)`;
  - the project label is the most frequent, then alphabetical;
  - efficiency worst-session ties go to the lowest `session_id`;
  - invocations tie on name.

### Task 13: Engine parity (both engines)

**Files:** none in the repository: a test that loads both engines (ClickHouse the way the Collector's exporter writes it) and compares their answers, kept out of it with the other tests that need live databases. Acceptance: 0 "different", 0 "order-only".

- [x] 12 users x 62 days loaded into both engines the production way; every windowed endpoint x 5 windows (24 h, 7 d, 30 d, 60 d, partial days at both ends) x 6 filter sets (300 answers), 17 session-list variants and 65 session-detail pages (incl. edge cases) answer identical JSON (floats to 1e-9).
- [x] The first run found D11 (ClickHouse orders only the last `SELECT` of a `UNION ALL`); fixed in the ClickHouse reader, test first.

### Task 14: End-to-end, performance, packaging

- [x] ~~`.env.example` section (commented)~~, reverted in the final cleanup. Local `.env.local` gets `CLI_ANALYTICS_*`, and the running backend is restarted.
- [x] E2E on PostgreSQL:
  - send telemetry to `http://localhost:8080/v1/analytics/cli-analytics/*`: synthetic OTLP and hooks, plus a real `claude -p` session with signal-specific OTLP endpoints;
  - verify the rows and rollups;
  - verify all 11 GET endpoints;
  - automated as an end-to-end test (router + configured storage over HTTP; totals equal what was sent), kept out of the repository.
- [ ] Open `http://localhost:5173/analytics?tab=cliAnalytics` and check every widget visually. The page needs a signed-in session. The UI's own requests (overview, repositories and drill-down, sessions and their sorts, session detail, tools, activity, efficiency, cost, frameworks) were served by the PostgreSQL backend with 200s, but the rendered page was not inspected.
- [x] E2E on ClickHouse (regression): done in-process rather than on the live backend. The end-to-end test runs the router in ClickHouse mode; the ingestor forwards over HTTP to a stand-in OTel Collector that writes what the Collector's exporter would, and reads go through the production ClickHouse client. Both backends answer every read identically.
- [x] Performance (production defaults, 8 writers, refresher every 30 s):
  - ingest through HTTP on the live backend: 113 requests/s, p99 ~320 ms with the full middleware stack; in-process at x3: 21.5k records/s, p99 51-62 ms, no errors;
  - endpoint latency at x3 (120 users, 8,221 sessions in 30 days) instead of x12, because the Docker VM disk had 6 GiB free: 30-day medians 15-285 ms, max 327 ms; linear growth from x1 projects ~0.8 s for `/overview` at x12, as the PoC measured;
  - refresher lag under ingest: queue never older than 31 s, drained after every group; backfill ~505 session-days/s;
  - found and fixed: PostgreSQL JIT added ~500 ms to session detail (compiling per-partition expressions); `jit=off` on analytics sessions (session detail 14 ms median).
  - Targets in spec §3.
- [x] Failure tests: database down gives 503; pool saturation gives a fast 503; a request stuck behind a lock gives 503, leaves nothing behind and its retry stores once; a record the database rejects is dropped while the rest of its request is stored (200 with partial success); a missing partition goes to DEFAULT and is then moved.
- [x] `make ruff`, `make license-check`, the full CLI-analytics test set, then the full `make test`.
- [x] A code review subagent on the local diff; fix the findings and re-verify. Ten rounds, with 30, 16, 10, 11, 7, 2, 5, 5, 1 and 0 findings (the tenth approved); each round's are fixed test-first and re-verified (decisions above).
