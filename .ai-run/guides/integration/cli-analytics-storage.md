# CLI Analytics Storage

OTel telemetry from coding agents, sent by CodeMie CLI (formerly the sdlc-analytics plugin): Claude Code OTLP plus hook events, is stored in PostgreSQL, the only storage engine, behind the ports of `codemie.repository.cli_analytics`. The dashboards are served under `/v1/analytics/cli-analytics/*`.

OTel stays a data source, decoded inside the API: the OTLP endpoints of the API ingest directly, and a background job refreshes the rollups. There is no Collector and no second database; the analytics tables live in the application's PostgreSQL by default (see PostgreSQL Settings). Local stack: `docker compose up -d codemie postgres`.

## Where Code Belongs

| Concern | Location |
|---|---|
| Ports (`CliAnalyticsReader`, `CliTelemetryIngestor`, `CliAnalyticsRuntime`) and ingest errors | `src/codemie/repository/cli_analytics/ports.py` |
| Storage construction (lazy, one instance) | `src/codemie/repository/cli_analytics/factory.py` |
| PostgreSQL adapter (decode, ingest, rollups, maintenance, reader, runtime, backfill) | `src/codemie/repository/cli_analytics/postgres/` |
| PostgreSQL schema migrations (own Alembic environment and version table; run only through `run_migrations()`, so there is no `alembic.ini`: add a migration as a new file in `versions/` with `revision` and `down_revision` set) | `src/external/alembic_cli_analytics/` |
| Harness vocabulary (span, event and metric names to canonical kinds) | `src/codemie/repository/cli_analytics/vocabulary.py` |
| Background jobs and their lifecycle | `src/codemie/service/analytics/cli_analytics_jobs.py` |

| Avoid | Prefer |
|---|---|
| Importing a PostgreSQL adapter module in the router or handler | `get_cli_analytics_storage().reader` / `.ingestor`; the handler takes a `CliAnalyticsReader` |
| Changing a response shape | The API contract is fixed (see design D1/D3/D5/D10/D11); a new reader query goes into the port and the PostgreSQL reader, with a test |
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
| `CLI_ANALYTICS_RAW_RETENTION_DAYS` | `90` | Raw rows; dashboard windows are clamped to it |
| `CLI_ANALYTICS_ROLLUP_RETENTION_DAYS` | `365` | Daily and hourly rollups and `session_skills`; startup refuses a value below the raw retention |
| `CLI_ANALYTICS_SESSION_RETENTION_DAYS` | `0` (off) | Whole sessions of `session_dims`, `session_usage`, `session_usage_hourly` and `subagent_invocations` (see Session tables). A value from 1 to the raw retention minus 1 is refused as a storage configuration error: it is logged at startup and the analytics endpoints answer 503, the application still starts. Not in `.env.example` |
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

The section 6.6 latency budget (825 ms per endpoint) is unverified for these whole-session scans: no 30-day, ~28.5K-session run was available locally.

## Schema Extension

One revision adds the tables and columns that the six new events of CodeMie CLI (formerly the sdlc-analytics plugin) feed. Only primary keys and `NOT NULL` on key columns are constrained; a missing value is NULL (`''` in a key column).

| Table | Objects |
|---|---|
| `usage_requests` (new raw table, `PARTITION BY RANGE (ts)`, weekly, no primary key) | ids, scope, model, speed, geo, tier, five token counts, web counts, `stop_reason`, `is_api_error`, `git_branch`, `attrs`; indexes `(session_id, ts)` and BRIN `ts`; `user_email`, `message_id`, `agent_type`, `thinking_tokens` |
| `session_dims` | identity, platform, version, provider, story, model/command, duration, counters and `commands`/`skills`/`agents`/`tools`/`models` columns; indexes `(user_email, started_at)` and `(story_id)`; title, team, environment, space, plugin, host, account, git heads and other session context columns |
| `cost_daily` | `speed`, `inference_geo`, `scope_kind`, `scope_name`, `agent_type` (`NOT NULL DEFAULT ''`) join the primary key, which is replaced; `cache_creation_5m_tokens`, `cache_creation_1h_tokens` |
| `session_usage` (new) | per session, day, model, speed, geo, scope and agent; cache-creation total, thinking, web counts, API duration, TTFT sum, `attrs` |
| `subagent_invocations` (new) | `(session_id, agent_id)` with type, description, tool use, workflow, worktree, depth, start, duration, attribution; model, call and line counters, `tools`/`skills`/`commands`/`compactions`, `ended_at`, `attrs` |
| `session_usage_hourly` (new) | the whole table |
| `log_events` | `request_id`, index `(session_id, request_id)` |

- The revision creates no partitions. `usage_requests` is in `RAW_TABLES` in `postgres/maintenance.py`, so `PartitionMaintainer` creates its partitions and the raw retention applies; the other partitioned-table lists derive from it.
- Uniqueness of usage rows comes from the `ingest_dedup` ledger, as for the other raw tables.
- Ingest writes only `usage_requests` and `log_events.request_id`; the refresher fills the other new tables and columns (see Rollup families 16 and 32 and Session tables).

### Ingest of the six events

The contract of the events for a sender (JSON of each, field by field, identity, values the server changes) is in [`cli-analytics-events.md`](cli-analytics-events.md).

The routing is decided by the event `type`, on `POST …/cli-analytics/event-hooks` and on an OTLP log record with an `event_type` attribute alike.

| Event | Stored in |
|---|---|
| `agent.usage.request` | `usage_requests` only, never `hook_events` |
| `agent.subagent.usage`, `agent.session.summary`, `agent.session.env`, `agent.skill.dispatch`, `agent.git.snapshot` | `hook_events`: the 22 typed columns as before, every other field to `attrs` with its JSON type |

- None of the six is a session-boundary type: `DIMENSION_HOOK_TYPES` still holds 13.
- Usage row `user_email`: the JWT sender on `/event-hooks`, the `user.email` attribute on OTLP.
- `log_events.request_id` is lifted from the OTel `request_id` attribute, like `model` and `query_source`.

Typed values of `usage_requests` (`log_events` keeps 0 for unparseable, because its sums feed the `NOT NULL` measures of `cost_daily`):

| Input | Stored |
|---|---|
| Integer or digit string up to 10^9 | the integer |
| Above 10^9 | `0`, the original in `attrs` |
| Absent, `null` or `""` | NULL |
| Unparseable (`"abc"`, `-1`, `12.5`, `true`, array, object) | NULL, the original in `attrs` |
| Boolean column: `true`/`false` or the strings in any case; anything else | the boolean; NULL with the original in `attrs` |

### Deduplication by `event_id`

- With a non-empty `event_id` the record hash is over (type, session key, event_id) on both paths, so a re-send is recognised whatever the sender's email and two sessions sharing an `event_id` are both kept. The first stored version stays: a changed event, a new summary for one, must carry a new `event_id`. Without one, the old content hash applies.
- The ledger key is `(day, h)` and is kept `CLI_ANALYTICS_DEDUP_RETENTION_DAYS` (14) days: a repeat is dropped when it carries the same UTC day and arrives inside that window.
- On OTLP, a record with an `event_id`, and any `agent.usage.request`, takes `ts` from its `timestamp` attribute (record time when absent or unparseable), as `/event-hooks` does.

### Rollup families 16 and 32

`RollupFamily` gains `USAGE_FACTS = 16` and `SESSION = 32` (`rollup_dirty.kinds` is an integer: no migration).

| Record | New bits |
|---|---|
| `usage_requests` row | `USAGE_FACTS`, plus `LOG_FACTS`; `SESSION` when the session id is non-empty; never `DIMENSIONS` |
| OTel `api_request` | `USAGE_FACTS` on the key of its `LOG_FACTS` |
| `hook_events` row of type `agent.subagent.usage` | `USAGE_FACTS` |
| Any log, hook, span or metric record with a session id | `SESSION` |

- Midnight rule (marking): a `usage_requests` row at most 1 hour after 00:00:00 UTC also marks the previous day, and at most 1 hour before the next midnight the next day, with bits 1, 16 and 32 (`USAGE_REQUEST_REACH` in `dirty.py`, one constant shared with the recompute). An OTel `api_request` with a non-empty `request_id` marks the neighbouring day within the same 1 hour with the bits of its own day (1, 16 and 32 when the session id is non-empty); without a `request_id` it does so only within 5 seconds. Both bounds are inclusive; nothing else uses the rule. An event dated on the first or the last day of the calendar (0001-01-01, 9999-12-31) marks its own day only.
- Request matching (recompute of day D): tier 1 pairs a transcript request with the OTel `api_request` of the same session and the same non-empty `request_id` (n-th with n-th in time order) when each record lies within 1 hour of the other one's UTC day, in both directions: on the same day anywhere, or across a midnight with both records within 1 hour of it. D therefore reads transcript requests whose latest line is in [D - 1 h, D + 1 day + 1 h) and OTel records in the same range. The matched row is written only on the transcript request's own day; the paired OTel record is not written on its own day as a service or OTel-only row, so a cost is never counted on two days. Tier 2 (fingerprint, no identifier) stays at 5 seconds: both records within the day widened by 5 s and at most 5 s apart. The lines of one transcript request are grouped across the same 1 hour reach, so a request straddling midnight is never counted on two days.
- Every family recomputes: bit 16 rebuilds `session_usage` and `session_usage_hourly`, bit 32 the new `session_dims` columns and `subagent_invocations`. The `SESSION` statements run for the session of every claimed key with bit 1, 4, 8 or 32, after the daily and hourly rollups and `DIMENSIONS`. A key holding only bit 32 for a session with no rollups runs without error.
- A day is recomputed only inside raw retention; older keys are dropped with a WARNING and their rollups are kept.

### Session tables

| Table | Key | Recomputed |
|---|---|---|
| `session_usage` | `session_id, day, model, speed, inference_geo, scope_kind, scope_name, agent_id` (`''` = unknown) | Per `(day, session)` by bit 16: delete the day's rows, insert from `usage_requests` and OTel `api_request` |
| `session_usage_hourly` | the same plus `hour` and `service_tier` | Same recompute, one row per hour of the request time; the hours of a day sum to its `session_usage` rows |
| `subagent_invocations` | `(session_id, agent_id)` | Bit 32, upserted; the latest `agent.subagent.usage` wins; `attribution` is `hook` or `heuristic` (only start/stop seen); a `duration_ms` computed from the two times is NULL when the end is before the start |
| `session_dims` (new columns) | `session_id` | Bit 32, merged into the stored row; `DIMENSIONS` (bit 2) keeps the columns of the initial schema |

- Subagent totals are read from the agent's latest `agent.subagent.usage` event that carries them (`usage` is an array with at least one object); a later event without them replaces nothing. They become `session_usage` rows only when the session has no `usage_requests` for that agent, no OTel `api_request` and no `session_usage` row of the session (any day, any `agent_id`) carrying `attrs.otel_requests`; otherwise they stay in `subagent_invocations.attrs`. They are never added to per-request rows and have no hourly rows. The last condition keeps the tokens from being counted twice once raw retention has purged the OTel records: a later recompute of the totals' day still sees the marker. The delete of stale totals rows uses the same predicate.
- `api_calls`, `web_search_requests` and `web_fetch_requests` of `session_usage` and `session_usage_hourly` are `int` columns. A sum that does not fit is stored as NULL, so the recompute of the key never fails on it.
- `attrs.otel_requests` on a `session_usage` row is the number of OTel `api_request` records that went into the row: matched rows, `service` rows and OTel-only rows. The key is present only when the number is above 0. It is not a `token_source` value: a matched row takes its tokens from the transcript and only its cost from OTel.
- `session_dims` merge kinds (a NULL never overwrites a value):

| Kind | Rule |
|---|---|
| summary | Counters, lists, `title`, `ended_at`, `client_versions` and similar come from the latest `agent.session.summary`, written only when its `ts` is not older than the stored `summary_ts` |
| fallback | The same columns for a session with no summary, computed from raw rows; written only while `summary_ts` is NULL |
| priority | (`user_email`, `identity_source`) and (`story_id`, `story_source`) are replaced only by a higher rank, or the same rank and a later timestamp |
| latest value | Columns from `agent.session.env`, `agent.git.snapshot`, common fields and `session_attributes`: the latest non-empty value |
| accumulating | `sources`, fallback `client_versions` and `attrs` are merged with the stored value; an `attrs` key is never removed |
| rollup-derived | `active_ms`, `source_cost_usd`, `delivery_framework`, `feature_id` are recomputed each time |

- When the stored `activity_started_at` is earlier than the earliest raw record now present (raw rows were purged), fallback columns sourced from raw rows keep their stored value.
- A `session_dims` row created by a backfill rebuild (`--now`) keeps `delivery_framework` NULL until the next recompute by the refresher: the command passes no classifier.

### Counter fallback

A `(day, session)` pair is a fallback pair when it has no span of kind `TOOL`, `TOOL_EXECUTION` or `INTERACTION`; the test is by kind, never by name. Its `turns_daily`, `tool_facts_daily`, `invocations_hourly` and `session_files_daily` rows are counted from `hook_events` (`agent.prompt.submit`, `agent.tool.start`/`end`/`error`, `agent.skill.dispatch`, `agent.subagent.start`); a pair with spans uses spans. Spans and hooks are never added. `success` is an `agent.tool.end` with the same `tool_use_id`, counted on the day of the start. The reader has no fallback query of its own: it reads these rollups for the sessions of the window.

### Slash commands (`get_invocations`, kind 4)

`slash_commands` of `/tools` has one source per session. Only the kind-4 branches of the PostgreSQL reader's `get_invocations` follow this rule.

| Session | Source |
|---|---|
| With a summary (`session_dims.summary_ts` is not NULL) | Every string element of `session_dims.commands`, each counting 1 (duplicates kept, names as stored, without a leading slash). Non-string and empty elements are skipped; an empty or NULL `commands` adds nothing. Its OTel `user_prompt` records (kind 4 of `invocations_hourly`) add nothing |
| Without a summary | `command_name` of its OTel `user_prompt` records, from `invocations_hourly`, as before |

The window is the shared one (see Window Semantics): a session whose `started_at` is in the window adds all its commands, whichever source they come from.

### Known limitations

| Case | Effect |
|---|---|
| A tool call starts before midnight and its `agent.tool.end` arrives after 01:00 in a later batch | `success` stays 0 on the start's day until that day is recomputed again; a full rebuild gives 1. The span path has the same gap. Hook and span marking are unchanged |
| Tier-1 request matching: a transcript request and its OTel `api_request` (same session, same `request_id`) are more than 1 hour apart around midnight, so one record is outside the 1 hour reach of the other's UTC day | The pair is not matched and a cost can be lost; it is never counted twice, because the matched OTel record is written only with its transcript request |
| A client version changes between batches and there is no summary | `client_versions` accumulates both incrementally and after a backfill over existing rows; a rebuild from an empty schema keeps only the latest |
| Hooks counted a tool call, then an `INTERACTION` span with no tool spans arrives | `tool_calls` keeps the hook value incrementally (a NULL never overwrites); a full rebuild gives NULL |
| A session known from hooks only | `get_session_detail_tools` shows no tools: it reads raw spans only |
| A summary, an identity or a story event dated in the future by a wrong client clock | It is the latest by its own time: later, correctly dated events of that session do not replace its values until real time passes that date. The recalculation never compares client times with the current time |

### Reading the session tables

For whoever builds a report over `session_dims`, `session_usage`, `session_usage_hourly` and `subagent_invocations`. The dashboard endpoints of this API do not read them yet.

**Where each `session_dims` column comes from.** A session either has a summary (`summary_ts` is not NULL) or is filled by the fallback; `attrs.counters_source` says which (`summary` or `fallback`). A fallback value taken from raw rows cannot be rebuilt once raw retention has purged them; the stored value is kept.

| Column | With a summary | Without one (fallback) | Fallback lives |
|---|---|---|---|
| `turns` | summary | `sum(turns_daily.turns)` | rollup |
| `api_calls` | summary | `sum(cost_daily.api_call_count)`: service and subagent requests included | rollup |
| `tool_calls` | summary | `sum(tool_facts_daily.tool_calls)` | rollup |
| `tool_errors`, errors in `tools` | summary | execution spans with `success = 'false'`, one per `tool_use_id`; without execution spans, `agent.tool.error` events | raw |
| `tool_results` | summary | OTel `tool_result` events; without them, `agent.tool.end` plus `agent.tool.error` | raw |
| `tools` (`{tool: {calls, errors}}`) | summary | calls from `invocations_hourly` kind 1, errors as above | rollup, raw |
| `skills` (`{name: calls}`) | summary | `invocations_hourly` kind 2, plus `agent.skill.dispatch` events of a skill with no tool span on a day that has spans | rollup, raw |
| `agents` (`{agent_type: calls}`) | summary | `invocations_hourly` kind 3 | rollup |
| `lines_added`, `lines_removed` | summary | sums of `lines_daily`: an OTel metric, not a transcript diff | rollup |
| `files_changed`, `files_written`, `files_edited` | summary | distinct paths of `session_files_daily` by flag | rollup |
| `models`, `primary_model` | summary | `cost_daily.model_name` by calls, the first is the primary; raw model names | rollup |
| `commands`, `primary_command`, `title` | summary | NULL: nothing else carries them | — |
| `branch_dominant` | summary | the most frequent non-empty `hook_events.git_branch` | raw |
| `compactions`, `compaction_count` | summary | OTel `compaction` events; without them, `agent.session.compact` events (start and trigger only) | raw |
| `compaction_pre_tokens` | summary | NULL: the OTel value is kept inside `compactions` only | — |
| `activity_started_at`, `ended_at` | the first and the last transcript line | the earliest and the latest raw record of the session | raw |
| `duration_ms` | summary | `ended_at − activity_started_at`: wall clock, idle time included | raw |
| `client_versions` | summary | the one known `client_version`, accumulated over recomputes | raw |

Columns that do not depend on a summary:

| Column | Source |
|---|---|
| `user_email`, `identity_source` | the candidate of the highest rank, then the latest: `jwt` (the sender of a hook event or a usage row), `git`, `codemie_cli`, `claude_account` (OTel `user.email`), `os` |
| `story_id`, `story_source` | the candidate of the highest rank, then the latest: `explicit`, `marker`, `branch`, `mention`. The server never derives a story from a branch name |
| `git_email`, `codemie_cli_email`, `os_user`, `git_head_start`, `git_head_end`, `git_commits` | the latest non-empty value in `agent.session.start` and `agent.git.snapshot` |
| `claude_account_email` | the latest OTel `user.email` |
| `platform` | the common field; else from the prefix of the span and metric names (`claude_code.`, `cursor.`) |
| `client_version`, `entrypoint` | the common field; else the OTel session attributes `app.version`, `app.entrypoint` |
| `provider`, `api_host`, `configured_model`, `output_style`, `os`, `arch`, `node_version`, `timezone`, `hostname_hash`, `plugins`, `mcp_servers` | the latest `agent.session.env` (or `agent.session.start`) |
| `effort`, `permission_mode` | the latest non-empty value of the typed hook columns |
| `team`, `environment`, `space_id`, `space_source`, `codemie_cli_version`, `schema_version` | the latest non-empty common field of any event of the session |
| `organization_id`, `account_uuid`, `account_id`, `otel_user_id`, `terminal_type` | the OTel session attributes |
| `ingest_source` | `plugin` when the session has hook events or usage rows, else `otel` |
| `sources` | which of `otel` and `plugin` the session has data from; set by the recalculation |
| `active_ms`, `source_cost_usd` | sums of `active_time_daily` and `cost_daily`: OTel only |
| `feature_id` | `branch_dominant` without its first path segment; NULL stays NULL |
| `delivery_framework` | the classifier over the skill names: the keys of `skills` with a summary, else `session_skills` together with the keys of `skills` |

**Notes for a reader.**

- Two pairs of times. `started_at` and `last_event_at` are hook times and decide the dashboard windows. `activity_started_at` and `ended_at` are the first and the last transcript line (the raw range in the fallback) and give the length of the session.
- `session_dims` counters describe the main thread. Tools, skills and commands of a subagent are in `subagent_invocations`.
- In `session_usage`, `agent_id = ''` is the main thread and `agent_id <> ''` a subagent. `scope_kind = 'service'` is a request Claude Code made outside the transcript (session title, compaction): it has tokens and cost and no counterpart in the counters. The rule is the missing transcript counterpart, never the value of `query_source`; values seen so far are `repl_main_thread`, `sdk`, `agent:builtin:<type>`, `generate_session_title` and `compact`.
- `model` is normalised, for a request known from OTel only by the plugin's steps, so a model has one name in every row; fast mode and the inference region are in `speed` and `inference_geo`, not in the model name. `cost_daily.model_name` keeps the raw OTel name.
- `source_cost_usd` is the cost OTel reported. A request known from the transcript only has NULL there (0 in `cost_daily`): price it from its tokens. A row built from OTel only has the cache-write total in `cache_creation_tokens` and NULL in the 5m / 1h columns.
- The hours of a day in `session_usage_hourly` sum to the request-derived rows of `session_usage`. Rows with `attrs.token_source = 'subagent_usage'` are subagent totals and have no hourly rows.
- `attrs.otel_requests` on a `session_usage` row tells how many OTel records went into it; `subagent_invocations.attribution` is `hook` when the row rests on a usage event or usage rows, `heuristic` when only start and stop were seen.
- `team` is the tag the client's configuration states, and `feature_id` is derived from the branch; neither is a roster team or a tracker epic.

## Sizing (Measured)

| Measurement | Result |
|---|---|
| Ingest through the full API (8 senders) | 113 requests/s, p99 ~320 ms |
| Ingest adapter at x1, final code (40 developers, 8 writers) | 30.0k records/s, p99 35.5 ms, no errors; 30-day endpoint medians 5-72 ms |
| Ingest adapter at x3 (120 developers, 8 writers) | 21.5k records/s, p99 51-62 ms, no errors |
| 30-day endpoints at x3 (8,221 sessions) | medians 15-285 ms, max 327 ms; `/overview` grows linearly, ~0.8 s projected at x12 (~500 developers) |
| Session detail | 14 ms median warm, ~140 ms on a new connection |
| Refresher with the session recalculation (identifier pairing and neighbour-day marking included; 561 sessions, 137,996 records, one session of 5,836 raw records; PostgreSQL 17) | Drain of 673 keys 1.08-1.14 s (~590-630 session-days/s); backfill median 578-613 session-days/s (backfill marks 865 keys for 673 distinct, marking 0.13-0.26 s); the long session alone 0.13 s; incremental maximum refresh 0.08-0.09 s. One x1 interval drains well within 30 s |
| Refresher at x3 (120 developers, 2,017 keys, 402,316 records) and x12 (480 developers, 8,065 keys, 1,591,756 records; PostgreSQL 17) | Drain in one refresh call: x3 3.2 s (631 session-days/s, queue age 16.6 s), x12 14.1 s (572 session-days/s, queue age 66 s), against ~610 at x1; backfill median 588 and 550 session-days/s; the long session alone 0.13 s at both; incremental maximum refresh 0.13 s and 0.35 s. Queue empty and no WARNING or ERROR after every drain. Throughput is flat, drain time grows linearly with the queue |
| Ingest latency with the session recalculation (neighbour-day marking included) | p99 unchanged: otel-logs 45.4 ms, usage-requests 46.2 ms (p50 32.4 and 33.1 ms; target 500 ms). PostgreSQL 14 (14.24) and 17 (17.11) both pass every live check |
| Storage | ~480 B per raw record including rollups and the idempotency ledger |


## Operations

| Task | How |
|---|---|
| Schema | Migrated at startup in the background (the API does not wait); a failed migration is retried by the next refresh job. A pod waits up to 2 minutes for another pod's migration, but never at shutdown |
| Jobs | Rollup refresh and maintenance run on one pod at a time (advisory locks); `max_instances=1`, late runs are not dropped |
| Backfill or repair rollups | `python -m codemie.repository.cli_analytics.postgres.backfill --from YYYY-MM-DD --to YYYY-MM-DD [--now]`: re-derives the kind of rows stored before their name was mapped in `vocabulary.py`, then queues the range for the refresh job (`--now` rebuilds it in the command). Each raw source queues the bits its records mark at ingest: `usage_requests` 1, 16, 32 (+ the neighbouring day within 1 h of midnight); `log_events` 1, 32 (+16 for `api_request`, + the neighbouring day within 1 h of midnight with a `request_id`, within 5 s without); `hook_events` 2, 32 (+4 for the six fallback hook types, also on the day of `ts - 1 h`; +16 for `agent.subagent.usage`); `spans` 4, 32 (tool, execution and interaction spans also 4 on the day of `ts - 1 h`); `metric_points` 8, 32; intersected with the requested mask. No day older than raw retention is queued, neighbouring days included. The reported count is per one-day statement, so a neighbour day queued by two statements counts twice. A row created by `--now` has `delivery_framework` NULL until the refresher recomputes it |
| Alert on | WARNING `rollup queue has N keys, the oldest waiting Ns` (repeats every 5 min while stale); WARNING `recomputing rollup key ... timed out (N of 3 in a row); it is retried in N minutes`; ERROR `dropped rollup key`; ERROR `maintenance step ... failed`, `cannot create partition`, `purging expired rows of ... failed`; WARNING `rows outside every planned partition`; WARNING `dropped a telemetry record PostgreSQL rejects`; WARNING `a request holds more records PostgreSQL rejects than one attempt isolates`; ERROR `cannot drop expired partition`; ERROR `the configured analytics storage cannot be used` (startup) |
| Retention | Whole partitions are dropped; DEFAULT partitions, `session_skills` (rollup retention), `session_attributes` and OTel resources (raw retention) are purged by date. `session_dims` is not on rollup retention: with `CLI_ANALYTICS_SESSION_RETENTION_DAYS` = 0 it is kept. Above 0 the maintenance job first deletes whole sessions whose `coalesce(greatest(last_event_at, ended_at), updated_at)` is before the midnight-UTC cutoff, in one statement: the delete from `session_dims` decides once which sessions are idle, and `session_usage`, `session_usage_hourly` and `subagent_invocations` lose exactly those sessions. A session another transaction changes meanwhile is tested again and kept whole when it is no longer idle; when that transaction holds rows the purge needs, PostgreSQL may end one of the two with a deadlock error, and the next run retries. Then rows of those three tables with no `session_dims` row are deleted by their own date (`day`, `hour`, `coalesce(ended_at, started_at)`; a subagent row with no date is kept). `last_event_at`, `ended_at` and a subagent's dates come from the client: a value after today (UTC, by the maintainer's clock) is ignored, so a wrong client clock cannot keep a row past its retention |
| Rejected data | A record PostgreSQL rejects is dropped, logged and entered in the ledger (a re-send skips it); the rest of its request is stored and the answer is 200 with OTLP `partial_success`. Past 64 failed attempts in one request the answer is 503 and the plugin's re-send carries on |
| Slow rollup keys | A batch past the 5-minute limit has its keys flagged `alone` in `rollup_dirty` and taken one at a time by any pod; a key timing out alone is retried after 10, then 20 minutes and dropped at the third timeout in a row |
| After a bulk load or backfill | Run `ANALYZE` on the analytics schema (or give autovacuum a few minutes): new partitions without planner statistics made a single-user overview 10x slower (192 vs 18 ms at x1) |
| After a rolling deployment that brings the schema extension | Run the backfill command above over the days of the rollout, once every pod runs the new code. The refresh job runs on one pod at a time; a pod still on the previous code that claims a key marked with bits 16 or 32 recomputes the families it knows and releases the key, so `session_usage`, `session_usage_hourly`, `subagent_invocations` and the new `session_dims` columns stay empty for it. The backfill queues those bits again |
| Downgrade of `a1c1a0000006` | Fails with a unique violation once `cost_daily` holds rows that differ only in `speed`, `inference_geo`, `scope_kind`, `scope_name` or `agent_type` (the recalculation writes them), because the downgrade restores the 5-column primary key `(day, session_id, user_email, model_name, query_source)`. `cost_daily` is a rollup derived from raw rows, so before downgrading run `TRUNCATE cost_daily` (it also loses the cache-creation 5m/1h split the downgrade drops), then downgrade and rebuild it with the backfill command above. Do not delete only some of the finer rows |
| Implausible values | Tokens above 10^9, cost beyond ±10^6 USD, spans over 30 days and counter readings adding more than 10^9 count as unparseable (0, the original kept in `attrs`), so no client can overflow the dashboards' sums. The `int` sums of the session tables are bounded at recompute (NULL when they do not fit) |

## Testing

| Suite | Command |
|---|---|
| Unit tests (default gate) | `make test` |

They need no database. SQL and schema behaviour were verified against PostgreSQL 14 and 17.

Overview for the team (architecture, flows, measurements, open items): handoff `docs/superpowers/handoffs/2026-09-24-cli-analytics-postgres-storage-handoff.md`.

Evidence: design `docs/superpowers/specs/2026-09-23-cli-analytics-postgres-storage-design.md`; plan `docs/superpowers/plans/2026-09-23-cli-analytics-postgres-storage.md`; factory `src/codemie/repository/cli_analytics/factory.py`; runtime `src/codemie/repository/cli_analytics/postgres/runtime.py`.
