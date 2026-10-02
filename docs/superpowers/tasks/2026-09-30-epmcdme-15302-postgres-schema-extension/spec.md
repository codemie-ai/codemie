# EPMCDME-15302 — PostgreSQL schema extension for CLI Analytics

**Ticket:** EPMCDME-15302. **Builds on:** the PostgreSQL storage of CLI Analytics (EPMCDME-15253), schema `codemie_analytics`, revision `a1c1a0000001`.
**Package:** `src/codemie/repository/cli_analytics/`, `src/external/alembic_cli_analytics/versions/`.
**Operations guide:** `.ai-run/guides/integration/cli-analytics-storage.md`.

This document describes the change as it stands on the branch. It stands on its own.

## 1. Goal

1. Extend the schema so that it holds what the six new events of CodeMie CLI (formerly the `sdlc-analytics` plugin) carry: per-request usage, per-session and per-subagent facts.
2. Accept those events on both ingest routes, store each in its raw table and deduplicate them by `event_id`.
3. Recalculate the new session tables and columns from raw rows, by the same queue and refresher as the existing rollups.
4. Count slash commands from one source per session.
5. Remove ClickHouse and the OTel Collector. OTel stays a data source: the API decodes OTLP itself and writes PostgreSQL.

## 2. Migration — revision `a1c1a0000006`

- `down_revision = "a1c1a0000001"`.
- An `UPGRADE` list of SQL strings run by `op.execute`, names without a schema prefix, as in the initial revision.
- Only `CREATE TABLE`, `ALTER TABLE … ADD COLUMN` and `CREATE INDEX`, with one exception: the primary key of `cost_daily` is replaced.
- The objects the ticket names come first. The additions follow in a block of their own. Each addition can be removed with one `DROP COLUMN` or `DROP TABLE`; nothing in the additions renames, re-keys or replaces an object the ticket names.
- Constraints are restricted to: the primary keys below; `NOT NULL` on key columns and on `ts` / `session_id` of the raw table; `NOT NULL DEFAULT ''` on the new key columns of `cost_daily`. No enum types, `CHECK` constraints, foreign keys or unique indexes other than primary keys. A missing value is NULL; in a key column it is the empty string `''`.
- PostgreSQL 14+ only: no extensions, no `NULLS NOT DISTINCT`.
- The revision creates no partitions. `PartitionMaintainer` creates them from the registry in `maintenance.py`; `usage_requests` is added to `RAW_TABLES` there, which also gives it the raw retention.
- The downgrade removes what the upgrade created, the additions first, and restores the five-column key of `cost_daily`. It fails once `cost_daily` holds rows that differ only in the new key columns.

### 2.1 Objects of the ticket

**`usage_requests`** — a new raw table, `PARTITION BY RANGE (ts)`, weekly partitions, no primary key. Uniqueness comes from the `ingest_dedup` ledger, as for the other raw tables.

| Column | Type |
|---|---|
| `ts` | `timestamptz NOT NULL` |
| `session_id` | `text NOT NULL` |
| `request_id`, `agent_id`, `scope_kind`, `scope_name`, `model_raw`, `model`, `speed`, `inference_geo`, `service_tier`, `stop_reason`, `git_branch` | `text` |
| `input_tokens`, `cache_creation_5m_tokens`, `cache_creation_1h_tokens`, `cache_read_tokens`, `output_tokens` | `bigint` |
| `web_search_requests`, `web_fetch_requests` | `int` |
| `is_api_error` | `bool` |
| `attrs` | `jsonb` |

Indexes: `usage_requests_session_ts` on `(session_id, ts)`; `usage_requests_ts_brin` on `USING brin (ts)`.

**`session_dims`** — new columns, all nullable:

| Columns | Type |
|---|---|
| `user_email`, `identity_source`, `git_email`, `codemie_cli_email`, `claude_account_email`, `os_user` | `text` |
| `platform`, `ingest_source`, `client_version`, `codemie_cli_version`, `provider` | `text` |
| `branch_dominant`, `feature_id`, `story_id`, `story_source` | `text` |
| `primary_model`, `primary_command`, `delivery_framework` | `text` |
| `ended_at` | `timestamptz` |
| `duration_ms`, `active_ms`, `compaction_pre_tokens` | `bigint` |
| `turns`, `api_calls`, `tool_calls`, `tool_errors`, `lines_added`, `lines_removed`, `files_changed`, `files_written`, `files_edited`, `compaction_count` | `int` |
| `commands`, `skills`, `agents`, `tools`, `models` | `jsonb` |
| `source_cost_usd` | `double precision` |

Indexes: `session_dims_user_started` on `(user_email, started_at)`; `session_dims_story` on `(story_id)`.

**`cost_daily`** — new columns and a replaced key:

| Column | Type | In key |
|---|---|---|
| `speed`, `inference_geo`, `scope_kind`, `scope_name`, `agent_type` | `text NOT NULL DEFAULT ''` | yes |
| `cache_creation_5m_tokens`, `cache_creation_1h_tokens` | `bigint` (nullable) | no |

The key becomes `(day, session_id, user_email, model_name, query_source, speed, inference_geo, scope_kind, scope_name, agent_type)`. The existing measures do not change. The `DEFAULT ''` lets the revision apply to a database whose `cost_daily` already holds rows.

**`session_usage`** — a new table, not partitioned. `PRIMARY KEY (session_id, day, model, speed, inference_geo, scope_kind, scope_name, agent_id)`; `day` is a UTC `date`, the other seven are `text`.

| Column | Type |
|---|---|
| `input_tokens`, `cache_creation_5m_tokens`, `cache_creation_1h_tokens`, `cache_read_tokens`, `output_tokens` | `bigint` |
| `api_calls` | `int` |
| `source_cost_usd` | `double precision` |

Index: `session_usage_day` on `(day)`.

**`subagent_invocations`** — a new table, not partitioned. `PRIMARY KEY (session_id, agent_id)`.

| Column | Type |
|---|---|
| `agent_type`, `description`, `tool_use_id`, `workflow_run`, `worktree`, `attribution` | `text` |
| `spawn_depth` | `int` |
| `started_at` | `timestamptz` |
| `duration_ms` | `bigint` |

### 2.2 Additions

All columns of this group are nullable.

| Table | Added |
|---|---|
| `usage_requests` | `user_email text`, `message_id text`, `agent_type text`, `thinking_tokens bigint` |
| `session_usage` | `cache_creation_tokens bigint`, `thinking_tokens bigint`, `web_search_requests int`, `web_fetch_requests int`, `api_duration_ms bigint`, `ttft_ms_sum bigint`, `attrs jsonb` |
| `subagent_invocations` | `model text`, `api_calls int`, `tool_calls int`, `tool_errors int`, `tool_results int`, `lines_added int`, `lines_removed int`, `files_changed int`, `tools jsonb`, `skills jsonb`, `commands jsonb`, `compactions jsonb`, `ended_at timestamptz`, `attrs jsonb` |
| `log_events` | `request_id text`, index `log_events_session_request` on `(session_id, request_id)` |

`session_dims` also gains:

| Type | Columns |
|---|---|
| `text` | `title`, `team`, `environment`, `space_id`, `space_source`, `entrypoint`, `hostname_hash`, `api_host`, `configured_model`, `effort`, `output_style`, `os`, `arch`, `node_version`, `timezone`, `permission_mode`, `git_head_start`, `git_head_end`, `organization_id`, `account_uuid`, `account_id`, `otel_user_id`, `terminal_type` |
| `jsonb` | `sources`, `attrs`, `client_versions`, `compactions`, `plugins`, `mcp_servers`, `git_commits` |
| `int` | `tool_results`, `schema_version` |
| `timestamptz` | `activity_started_at`, `summary_ts` |

**`session_usage_hourly`** — a new table, not partitioned. `PRIMARY KEY (session_id, hour, model, speed, inference_geo, service_tier, scope_kind, scope_name, agent_id)`; `hour` is the start of the UTC hour.

| Column | Type |
|---|---|
| `input_tokens`, `cache_creation_tokens`, `cache_creation_5m_tokens`, `cache_creation_1h_tokens`, `cache_read_tokens`, `output_tokens`, `thinking_tokens`, `api_duration_ms`, `ttft_ms_sum` | `bigint` |
| `web_search_requests`, `web_fetch_requests`, `api_calls` | `int` |
| `source_cost_usd` | `double precision` |
| `attrs` | `jsonb` |

Index: `session_usage_hourly_hour` on `(hour)`.

## 3. Ingest

Ingest writes raw rows and dirty keys only. It never writes a rollup or a session table.

### 3.1 Routing of the six events

The six events arrive as NDJSON on `POST …/cli-analytics/event-hooks`, like the existing hook events, or as an OTLP log record with an `event_type` attribute. The routing is decided by the event type on both routes.

| Event | Stored in |
|---|---|
| `agent.usage.request` | `usage_requests` only, never `hook_events` |
| `agent.subagent.usage`, `agent.session.summary`, `agent.session.env`, `agent.skill.dispatch`, `agent.git.snapshot` | `hook_events` |

- In `hook_events` a field whose name is one of the 22 typed hook columns goes to that column; every other field goes to `attrs` and keeps its JSON type.
- None of the six types moves a session boundary: `DIMENSION_HOOK_TYPES` stays at its 13 types.
- The `usage_requests` row: `ts` is the event `timestamp` (on OTLP the `timestamp` attribute; the time received, or the record time, when it is absent or does not parse). `user_email` is the authenticated sender on `/event-hooks` and the `user.email` attribute on OTLP. The other typed columns take the field of the same name. Everything else goes to `attrs`, except an empty `prompt_id`, which is not stored on either route.
- Text goes through the existing helpers: NUL stripped, broken Unicode repaired, cut to 256 bytes, `''` stored as NULL.
- An event with any subset of the fields is stored. One holding only `type`, `session_id` and `timestamp` gives a row whose other columns are NULL.
- `log_events.request_id` is lifted from the OTel `request_id` attribute, like `model` and `query_source`.

### 3.2 Typed values of `usage_requests`

| Input | Stored |
|---|---|
| JSON integer or a string of ASCII digits, up to 10^9 | the integer |
| above 10^9 | `0`, with the original in `attrs` |
| key absent, `null` or `""` | NULL |
| anything else (`"abc"`, `-1`, `12.5`, `true`, an array, an object) | NULL, with the original in `attrs` |
| boolean column: JSON `true` / `false`, or those words as strings in any case | the boolean |
| boolean column, anything else | NULL, with the original in `attrs` |

`log_events` keeps its parsing, where an unparseable number is 0: its sums feed the `NOT NULL` measures of `cost_daily`.

### 3.3 Deduplication by `event_id`

| Case | Record hash |
|---|---|
| a non-empty `event_id`, on either route | hash of (type, session key, `event_id`) |
| no `event_id`, `/event-hooks` | the whole event content, as before |
| no `event_id`, OTLP | the serialized record, as before |

The same triple gives the same hash on both routes, so an event sent through both is stored once. Two sessions that share an `event_id` are both stored. The sender's email is never part of the hash. The ledger key is `(day, hash)` and is kept 14 days: a repeat is recognised when it carries the same UTC day and arrives within that time.

### 3.4 Families and marking

`RollupFamily` gains `USAGE_FACTS = 16` and `SESSION = 32`. `rollup_dirty.kinds` is an integer, so no migration is needed. Masks are plain integers.

| Stored record | Families | Days |
|---|---|---|
| OTel log event of kind `api_request`, `user_prompt`, `skill_activated` | `LOG_FACTS`, as before | own day |
| OTel `api_request` | also `USAGE_FACTS` | own day, plus the midnight rule |
| any OTel log event, span or metric point with a session id | `SESSION` | own day |
| `usage_requests` row | `LOG_FACTS`, `USAGE_FACTS`; `SESSION` when the session id is not empty; never `DIMENSIONS` | own day, plus the midnight rule |
| `hook_events` row with a session id | `DIMENSIONS` as before, `SESSION` | own day |
| … of type `agent.tool.start`, `agent.tool.end`, `agent.tool.error`, `agent.prompt.submit`, `agent.subagent.start`, `agent.skill.dispatch` | also `SPAN_FACTS` | own day and the day of `ts − 1 h`, as for spans |
| … of type `agent.subagent.usage` | also `USAGE_FACTS` | own day |

**Midnight rule.** A `usage_requests` row within 1 hour of midnight UTC also marks the neighbouring day, with the same bits. An OTel `api_request` does so within 1 hour when it carries a `request_id`, and within 5 seconds when it carries none. Both bounds are inclusive. The 1 hour is one constant, shared with the recalculation (§4.2). An event dated on the first or the last day of the calendar marks its own day only: there is no neighbouring day to mark.

### 3.5 Event contract

The full contract for a sender, with the JSON of each event, is in `.ai-run/guides/integration/cli-analytics-events.md`; this section is its summary. One flat JSON object per line, keys in `snake_case`. The envelope is `type`, `timestamp` (ISO-8601 UTC), `session_id`, `prompt_id`. Numbers, booleans, arrays and objects are sent as JSON values of that type. `event_id` is opaque to the server. Command names (`commands`, `primary_command`, `command_name`) carry no leading slash.

Common optional fields on any of the six: `schema_version` (int), `event_id`, `platform`, `entrypoint`, `client_version`, `codemie_cli_version`, `identity_source`, `story_id`, `story_source`, `developer_name`, `codemie_project_name`, `cwd`, `agent_id`, `agent_type`.

| Event | `timestamp` is | Fields |
|---|---|---|
| `agent.usage.request` | the last transcript line of the request | `request_id`, `message_id`, `scope_kind` (`main` / `skill` / `agent`), `scope_name`, `model_raw`, `model`, `speed`, `inference_geo`, `service_tier`, `stop_reason`, `git_branch`; `input_tokens`, `cache_creation_5m_tokens`, `cache_creation_1h_tokens`, `cache_read_tokens`, `output_tokens`, `thinking_tokens`, `web_search_requests`, `web_fetch_requests`; `is_api_error` |
| `agent.subagent.usage` | the end of the subagent | `description`, `tool_use_id`, `workflow_run`, `worktree`, `started_at`, `ended_at`, `model`; `spawn_depth`, `duration_ms`, `api_calls`, `tool_calls`, `tool_results`, `tool_errors`, `lines_added`, `lines_removed`, `files_changed`; `tools`, `skills`, `commands`, `compactions`; `usage` (array: the usage fields of a request plus `api_calls`) |
| `agent.session.summary` | the last transcript line it covers | `title`, `started_at`, `ended_at`, `primary_model`, `primary_command`, `branch_dominant`, `git_branch`, `client_version`; `is_final`; the counters (`duration_ms`, `turns`, `api_calls`, `tool_calls`, `tool_results`, `tool_errors`, `lines_added`, `lines_removed`, `files_changed`, `files_written`, `files_edited`, `compaction_count`, `compaction_pre_tokens`); `models`, `commands`, `client_versions`; `skills`, `agents`, `tools`, `branch_counts`; `compactions` |
| `agent.session.env` | the time of the hook | `platform`, `entrypoint`, `client_version`, `codemie_cli_version`, `provider`, `api_host`, `configured_model`, `effort`, `output_style`, `permission_mode`, `os`, `arch`, `node_version`, `timezone`, `hostname_hash`; `plugins`; `mcp_servers` |
| `agent.skill.dispatch` | the time of the hook | `skill_name`, `command_name` (no leading slash), `command_source` |
| `agent.git.snapshot` | the time of the hook | `git_branch`, `repo_remote`, `git_email`, `git_head_start`, `git_head_end`; `git_commits` |

One invocation of a skill gives one of two events, never both: `agent.skill.dispatch` when a slash command of the user starts the skill, `agent.tool.start` with `skill_name` when the model starts it through its tool. The counter fallback (§4.7) adds the two counts on that ground.

## 4. Recalculation

### 4.1 Families

| Bit | Family | Recomputes |
|---|---|---|
| 1 | `LOG_FACTS` | `cost_daily` with its ten-column key, plus what it recomputed before |
| 2 | `DIMENSIONS` | unchanged: the original columns of `session_dims` (`started_at`, `last_event_at`, …) |
| 4 | `SPAN_FACTS` | what it recomputed before, with the counter fallback (§4.7) |
| 8 | `METRIC_FACTS` | unchanged |
| 16 | `USAGE_FACTS` | `session_usage`, `session_usage_hourly` |
| 32 | `SESSION` | the new `session_dims` columns, `subagent_invocations` |

- A table rebuilt with DELETE + INSERT belongs to one family. `session_dims` is upserted by `DIMENSIONS` and `SESSION`, each writing only its own columns.
- The claim protocol of the refresher is unchanged, and there is no second job. Every statement of a recompute transaction runs under the refresh time budget, the Python-derived columns of `session_dims` included.
- Inside one recompute transaction the daily and hourly rollups come first, then `DIMENSIONS`, then `SESSION`. The `SESSION` statements run for the session of every claimed key with bit 1, 4, 8 or 32, because the session columns read those rollups.
- Every INSERT groups by exactly the key columns of its table, with `coalesce(x, '')` on key columns and `coalesce(x, 0)` on `NOT NULL` measures. A constraint violation would stall the queue, so none may occur on data. For the same reason a sum written to an `int` column of `session_usage` or `session_usage_hourly` (`api_calls`, `web_search_requests`, `web_fetch_requests`) is NULL when it does not fit the column.
- A day is recomputed only inside raw retention. Older keys are dropped with a WARNING and their stored rows are kept.

### 4.2 Requests: grouping and matching

**Transcript requests.** `usage_requests` rows are grouped by session, request id (else message id) and raw model. Each measure is the maximum within the group, never the sum; the time of the group is its latest `ts`. A row with neither id is a group of its own. A duplicate that got past the ledger therefore changes nothing. The lines of one request are grouped across midnight within 1 hour, the same reach as the marking.

**OTel requests.** `log_events` rows of kind `api_request`.

**Plugin data is decided per day.** The pair (day, session) has plugin data when the session has `usage_requests` rows in that day, extended by 5 seconds on both sides. This decides whether an unmatched OTel request is a `service` request and whether `cost_daily` of that day uses the plugin path.

**Matching, on a day with plugin data.**

| Tier | Applies when | Pair |
|---|---|---|
| 1, identifier | both records carry a non-empty `request_id` | same session and same `request_id`, within 1 hour across midnight in either direction |
| 2, fingerprint | at least one record has no `request_id` | same session, same model, equal input, output, cache-read and cache-creation tokens, at most 5 seconds apart; the nearest in time wins |

- Tier 1 runs first; tier 2 runs on what is left. An empty identifier never matches. A record is in at most one pair. Ties are broken by a stated order, never by physical row order.
- A matched request belongs to the day and hour of its transcript time, and is written on that day only.
- Tokens from the transcript and from OTel are never added together.

### 4.3 Token tables

| Request | Key columns | Tokens | Cost | Durations |
|---|---|---|---|---|
| matched | from `usage_requests` | `usage_requests` | OTel | OTel |
| transcript only | from `usage_requests` | `usage_requests` | `session_usage`: NULL; `cost_daily`: 0 | NULL |
| service: an unmatched OTel request on a day with plugin data | normalised OTel model, normalised OTel speed, `scope_kind = 'service'`, `scope_name` = `query_source` | OTel; the cache-creation total, 5m and 1h NULL | OTel | OTel |
| a day without plugin data | normalised OTel model, normalised OTel speed; with `agent.name`, `scope_kind = 'agent'` and `scope_name` = that name | OTel | OTel | OTel |
| subagent totals (§4.4) | from the `usage[]` row | the `usage[]` row | NULL | NULL |

- `speed` is `standard` or `fast`; OTel `normal` is stored as `standard`. `scope_name` is never `main`. A filler value is never invented.
- `model` has one value per model whichever source named it. A transcript request carries the plugin's normalised name. An OTel name is normalised by the same steps in the same order: lower case, trimmed, without a vendor prefix, a region prefix, a date suffix and a version suffix. `cost_daily.model_name` keeps the OTel name as it was sent.
- `thinking_tokens` is part of `output_tokens` and is never added to it.
- `session_usage` is recomputed per (day, session). `session_usage_hourly` is built from the same requests in the same recompute, one row per hour of the request time. Neither table is derived from the other; the hours of a day sum to its daily rows. Subagent totals have no hourly rows.
- `cost_daily` on a day without plugin data is computed as before: the five new key columns are `''` and the 5m / 1h split is NULL. On a day with plugin data a matched or service row takes `model_name`, `user_email` and `query_source` from OTel, a transcript-only row takes the raw model, the sender and `''`.
- `attrs.otel_requests` on a `session_usage` row is the number of OTel records that went into it: matched, `service` and OTel-only requests. The key is present only when the number is above 0.

### 4.4 Subagent tokens

`agent.usage.request` carries per-request rows with `agent_id`; `agent.subagent.usage` carries the subagent's totals in `usage[]`. They are never added together. The totals are read from the agent's latest event that carries them, that is, whose `usage` is an array with at least one object: a later event without them replaces nothing.

The totals become `session_usage` rows, marked `attrs.token_source = 'subagent_usage'`, only when all hold:

1. the session has no `usage_requests` rows of that agent, on any day;
2. the session has no OTel `api_request` record, on any day;
3. no `session_usage` row of that agent was built from its requests;
4. no `session_usage` row of the session carries `attrs.otel_requests`, on any day and for any agent.

Conditions 3 and 4 read stored rows: raw retention may have purged the requests while the rows built from them remain. In every other case the totals stay in `subagent_invocations.attrs`. A recompute that finds requests while totals rows exist on another day deletes those rows; it never inserts on another day. The insert and the delete share one predicate.

### 4.5 `session_dims`, family `SESSION`

A row exists for every session with a stored record, a session known from OTel only included. `started_at`, `last_event_at` and the other original columns belong to `DIMENSIONS` and come from the 13 dimension hook types only, so a session without such an event keeps `started_at` NULL. The summary describes the main thread; nothing of a subagent is added to a session counter.

In every rule a NULL never overwrites a stored value. The source of every column, with and without a summary, is listed in the operations guide, section "Reading the session tables".

| Kind | Columns | Rule |
|---|---|---|
| summary | the counters, `commands`, `skills`, `agents`, `tools`, `models`, `compactions`, `primary_model`, `primary_command`, `branch_dominant`, `title`, `ended_at`, `client_versions`, `activity_started_at` | from the latest `agent.session.summary`; written as a set, and only when its `ts` is not older than the stored `summary_ts` |
| fallback | the same columns, for a session with no summary | computed from raw rows and rollups; written only while `summary_ts` is NULL |
| priority | (`user_email`, `identity_source`), (`story_id`, `story_source`) | replaced only by a higher rank, or the same rank and a later timestamp. Identity ranks: `jwt`, `git`, `codemie_cli`, `claude_account`, `os` |
| latest value | columns from `agent.session.env`, `agent.git.snapshot`, the common fields, `session_attributes` | the latest non-empty value |
| accumulating | `sources`, fallback `client_versions`, `attrs` | merged with the stored value; an `attrs` key is never removed |
| rollup-derived | `active_ms`, `source_cost_usd`, `delivery_framework`, `feature_id` | recomputed each time |

- When the stored `activity_started_at` is earlier than the earliest raw record now present, raw rows were purged, and the fallback columns sourced from raw rows keep their stored value.
- Fallback sources: `turns`, `api_calls`, `tool_calls`, lines and files from the daily rollups; `tool_errors` from execution spans with `success = 'false'`, else `agent.tool.error` events, never calls minus successes; `tool_results` from OTel `tool_result` events, else `agent.tool.end` plus `agent.tool.error`; `agents` from `invocations_hourly`; `skills` from `invocations_hourly` plus the `agent.skill.dispatch` events of a skill that has no tool span, on a day that has spans (a day without spans already counts its dispatches in the rollup, §4.7); compactions from OTel `compaction` events, else `agent.session.compact`; `activity_started_at` and `ended_at` from the earliest and latest raw record. `primary_command`, `commands` and `title` have no fallback.
- `feature_id` is `branch_dominant` without its first path segment; NULL stays NULL.
- `delivery_framework` is the classifier over the session's skill names, NULL when there is none. With a summary the names are the keys of `skills`; without one, `session_skills` (the skills OTel reported) together with the keys of the `skills` fallback, which also counts the skills known from hooks. The classifier is handed to the refresher by the application; the command-line backfill passes none, so a row it creates keeps NULL until the next recompute.
- `sources` is set by the recalculation (`plugin`, `otel`), never by the client.
- Fields of the five events stored in `hook_events` that have a typed column are read from the column, not from `attrs`.

### 4.6 `subagent_invocations`, family `SESSION`

Key `(session_id, agent_id)`, upserted; a NULL never overwrites, `attrs` is merged key by key. The latest `agent.subagent.usage` wins.

| Column | Source, in order |
|---|---|
| `agent_type` | the usage event; `agent.subagent.start` / `stop`; `usage_requests.agent_type` |
| `started_at` | the usage event; the time of `agent.subagent.start`; the earliest `ts` of the agent's `usage_requests` |
| `ended_at` | the usage event; the time of `agent.subagent.stop`; never from `usage_requests` |
| `duration_ms` | the usage event; else `ended_at − started_at`, NULL when the end is before the start |
| `model`, `api_calls` | the usage event; else from the agent's `usage_requests` |
| the other counters and lists | the usage event |
| `attribution` | `hook` with a usage event or `usage_requests` of the agent; `heuristic` when only start / stop exist |
| `attrs` | the `usage[]` totals, always, and the fields without a column |

A span is never matched to a subagent. A session known from OTel only has no rows.

### 4.7 Counter fallback, family `SPAN_FACTS`

A (day, session) pair is a fallback pair when it has no span of kind `TOOL`, `TOOL_EXECUTION` or `INTERACTION`. The test is on the kind, never on the name. A pair has one source: spans when they exist, hooks otherwise; the two are never added.

| Rollup | Fallback source in `hook_events` |
|---|---|
| `turns_daily.turns` | count of `agent.prompt.submit` |
| `tool_facts_daily` | `tool_calls`: `agent.tool.start` with a tool name; `agent_count`: `agent.subagent.start`; `skill_count`: `agent.tool.start` and `agent.skill.dispatch` with a skill name that fits a key, the rows `invocations_hourly` kind 2 counts (two different invocations by the contract, §3.5) |
| `invocations_hourly` kind 1 | `agent.tool.start` by tool name; `success` is an `agent.tool.end` with the same `tool_use_id`, counted on the day of the start |
| `invocations_hourly` kinds 2, 3 | skills and agents as above, by name |
| `session_files_daily` | only when `agent.tool.start` carries `file_path` |

### 4.8 Retention and backfill

- New setting `CLI_ANALYTICS_SESSION_RETENTION_DAYS`, default `0` (never delete); otherwise not below the raw retention. A refused value is logged at startup and the analytics endpoints answer 503, as for the other settings. It is documented in the operations guide, not in `.env.example`.
- When above 0: a session is deleted whole from `session_usage`, `session_usage_hourly`, `subagent_invocations` and `session_dims` when `coalesce(greatest(last_event_at, ended_at), updated_at)` is older than the cutoff. One statement does it: the delete from `session_dims` decides once which sessions are idle, and the other three tables lose exactly those. A session that another transaction makes non-idle meanwhile is kept whole. A row of the first three tables with no `session_dims` row is deleted by its own date. `session_dims` is no longer purged by the rollup retention.
- `last_event_at`, `ended_at` and the dates of a subagent row come from the client. One that lies after today is ignored by the purge, so a wrong client clock cannot keep a row past its retention; a session with no believable client time is judged by `updated_at`.
- Backfill finds sessions whose only raw rows are in `usage_requests`, and each raw source yields the bits its records mark at ingest, neighbouring days included. No day older than the raw retention is queued for any family.

## 5. Reader and handler

- **Window.** A session is in a window if and only if `session_dims.started_at` is in it. The rule is decided once, in the session selection of the reader, and every fact query is scoped to that selection with no time condition of its own. A session without `started_at` is in no window.
- **Top model** (`/users`, `/sessions`, session detail). `cost_daily` now has several rows per original key, so the calls are summed back to the original grain `(day, session_id, user_email, model_name, query_source)` first, then the model of the largest row wins, ties in the existing order. On data without plugin events the answer is unchanged.
- **Slash commands** (`slash_commands` of `/tools`). One source per session. A session with a summary (`session_dims.summary_ts` is not NULL) counts the string elements of `session_dims.commands`, each as 1, duplicates kept, names as stored; non-string and empty elements are skipped, and its OTel `user_prompt` records add nothing. A session without a summary counts its OTel commands from `invocations_hourly`, as before. The rollups and the port are unchanged.
- **`user_email`.** Every response row that carries `developer_name` gains a field `user_email` with the same value. `developer_name` is kept.
- No other query or response changes.

## 6. ClickHouse removal

- Removed: the `repository/cli_analytics/clickhouse/` adapter and `clients/clickhouse.py`; `config/clickhouse/` and `config/otel/`; the `clickhouse` and `otelcollector` services and their volumes in `docker-compose.yml`; the settings `CLICKHOUSE_*`, `ANALYTICS_INGEST_OTLP_HTTP_ENDPOINT` and `CLI_ANALYTICS_STORAGE_BACKEND`; the dependency `clickhouse-connect`; the tests of the adapter and the client.
- The ports and the port contract checks stay. What existed only to choose between engines goes: the builder table of the factory and the error type only the removed adapter raised, with its 502 mapping in the router.
- `.env.example` loses the ClickHouse and Collector keys; the test of its fixed key set changes with it.
- Tests that pinned or selected the backend are rewritten for one engine.
- Comments and docstrings that described behaviour "as ClickHouse does" describe it on its own terms. No behaviour changes. The parsers `ch_uint` and `ch_float` keep their names: they belong to the PostgreSQL adapter of MR !4327, and renaming them is left to it.
- `poetry.lock`: the `clickhouse-connect` block was removed and the content hash recomputed without access to the private registry; `poetry check --lock` passes and no other package or version changed. `lz4`, which only `clickhouse-connect` needed, is still listed and is not installed.

## 7. Known limitations

| Case | Effect |
|---|---|
| A tool call starts before midnight and its end arrives after 01:00 in a later batch | `success` stays 0 on the day of the start until that day is recomputed again. The span path has the same gap |
| A transcript request and its OTel record with the same `request_id` are more than 1 hour apart around midnight | the pair is not matched and a cost can be lost; it is never counted twice |
| A client version changes between batches and there is no summary | `client_versions` holds both incrementally; a rebuild from an empty schema keeps only the latest |
| Hooks counted a tool call, then an `INTERACTION` span with no tool spans arrives | `tool_calls` keeps the hook value incrementally; a full rebuild gives NULL |
| A session known from hooks only | the session detail page shows no tools: that query reads raw spans only |
| A summary, an identity or a story event is dated in the future by a wrong client clock | it is the latest by its own time, so later, correctly dated events of that session do not replace its values until real time passes that date. Only that session is affected. The recalculation does not compare client times with the current time: its result must not depend on when it runs |
| A session with no dimension hook event: OTel only, or the new plugin events only | it has a `session_dims` row and rows in the session tables, but no `started_at`, so it is in no dashboard window |
| During a rolling deployment a pod still on the previous code claims a key marked with bit 16 or 32 | it recomputes the families it knows and releases the key, so the session tables stay empty for that key. After the rollout the backfill command is run over its days (operations guide) |
| Two transcript requests with equal token counts lie within 5 seconds of midnight on its two sides, one OTel record lies between them, and none carries a `request_id` | the fingerprint tier pairs the record with the earlier request on the first day and with the later one on the second, so its cost is counted on both days. The identifier tier is symmetric; the fingerprint tier looks only inside the day widened by 5 seconds |
| Downgrade of the revision once `cost_daily` holds rows that differ only in the five new key columns | the five-column key cannot be restored and the downgrade stops. The operations guide gives the procedure: empty `cost_daily`, downgrade, rebuild it with the backfill command |
| The purge of idle sessions meets a recompute of one of them | the session is kept whole; PostgreSQL may end one of the two transactions with a deadlock error, and the next run retries |

## 8. Verification

**Two tiers.** Unit tests run in `make test` without a database. Everything that depends on what SQL returns was checked against a live PostgreSQL, in a schema created and dropped by each check; these checks are not part of the repository. A unit test with a mocked connection is never the proof of a query result.

**Rules of the checks.** Expected values are literals worked out by hand. Incremental recalculation is compared with a full one. Events are synthetic and built field by field. One session is supplied as transcript only, as OTel only, and as both, with and without a shared `request_id`. After every check the `rollup_dirty` queue is empty and the refresher logged no WARNING or ERROR: an empty queue alone is not enough, because it also empties when a key is dropped.

| What | Result |
|---|---|
| Migration | applies from scratch, over a database at `a1c1a0000001` with `cost_daily` rows, and a second time with no change; keys and indexes as in §2 |
| Ingest | each of the six events stored where §3.1 says on both routes; one event through both routes stored once; the same `event_id` in two sessions stored twice |
| Recalculation | incremental equals full for both new families and the fallback, apart from the limitations of §7, each recorded with both values; token totals equal across the four ways of supplying one session |
| Subagent totals after raw retention | a recompute after the OTel records were purged inserts no totals row; the check fails when condition 4 of §4.4 is removed |
| Top model, slash commands | checked on sessions with and without plugin events; each check fails when the rule is put back to its earlier form |
| Bounds and purge | `int` sums past the column give NULL and the key is recomputed; a later subagent event without `usage[]` rows keeps the totals; a session made non-idle by another transaction during the purge keeps all its rows; a client time after today keeps no row; a client number with 20,000 digits after the point is NULL; a backfill queues the day before a tool span of the first hour; a subagent whose end is before its start gets no computed duration; events dated 0001-01-01 and 9999-12-31 are ingested. Each of them but the last fails when its rule is removed |
| Engine comparison | before the removal, both engines were fed the same bytes and compared through one handler over 388 scenarios (9 windowed endpoints × 5 windows × 6 filter sets, the session list, every session detail page). Main data set: 388 identical. Data set with the Cursor span and metric names: 388 identical. Two deliberate breaks of a PostgreSQL query were reported by the comparison |
| PostgreSQL versions | all live checks pass on 17 and on 14 |
| Refresher load | about 610 session-days per second at 40 developers, 631 at 120, 572 at 480; at 480 the whole queue drains in 14 s of the 30 s interval |
| Unit tests | the list of failing tests equals the list of MR !4327 |
| `git grep -i -E 'clickhouse\|otelcollector\|ANALYTICS_INGEST_OTLP'` | nothing outside `docs/superpowers/` and `CHANGELOG.md` |
| `docker compose config --services` | neither `clickhouse` nor `otelcollector` |

## 9. Acceptance criteria

1. The revision applies to a live PostgreSQL from scratch, over existing `cost_daily` rows and a second time; the keys and indexes are those of §2.
2. Each of the six events is stored where §3.1 says, on both routes; an event holding only `session_id`, `type` and `timestamp` is stored.
3. The same `event_id` sent twice, the second time with other fields or through the other route, is stored once; an event without `event_id` behaves as before.
4. `"input_tokens": "12"` is stored as 12 and `"abc"` as NULL with the original in `attrs`; `"is_api_error": "TRUE"` as true and `"yes"` as NULL with the original in `attrs`.
5. Dirty keys follow §3.4, the midnight rule included.
6. Incremental recalculation equals full recalculation for `USAGE_FACTS`, `SESSION` and the counter fallback, apart from the limitations of §7.
7. One session supplied as transcript only, OTel only and both gives equal token totals.
8. Subagent totals are never counted next to requests, also after raw retention purged the requests.
9. A session older than raw retention does not change on recalculation.
10. The responses of the existing endpoints on PostgreSQL equal those of ClickHouse on the same data, shown before the removal.
11. `slash_commands` follows the one-source rule of §5.
12. Nothing in the code mentions ClickHouse, the Collector or their settings; `docker compose up` starts without them.
13. The operations guide, `AGENTS.md` and `README.md` describe one engine.

## 10. Non-goals

- A read API over the long-term tables, and any change to the plugin that sends the events.
- Fixing the negative duration a skill dispatch can show on the session detail page; it is returned as before.
- Findings of MR !4327 outside this change.
- Enum types, `CHECK` constraints, foreign keys or extra unique indexes.

## 11. Accepted decisions

Each of these was weighed against the project's guide on untrusted input, which prefers to reject a value it cannot take as sent, and was decided the other way on purpose. They are decisions of the task, not open work.

| # | Decision | Why | Known cost |
|---|---|---|---|
| 1 | The identity of an event with an `event_id` is `(type, session, event_id)`: neither the sender nor the content is part of it (§3.3) | A re-send must be recognised whatever it carries. The proxy forwards a session's spool under whichever login is active, so the sender can differ between the first send and a repeat. Acceptance criterion 3 requires it | The first stored version stays. A changed event must carry a new `event_id`; every new summary does. A sender who knows a session id and an event id can store an event under them first, and the real one of that day is then dropped. The ledger key includes the UTC day, so the same event re-sent with a `timestamp` on the other side of midnight is stored twice |
| 2 | That identity is hashed over the values as they are stored: cleaned, and the session id cut to 256 bytes (§3.3) | Both routes must give one hash for one event, and the stored value is what the two routes have in common | Two different received values that clean to the same text are one event. It takes a NUL character, an unpaired surrogate or an id longer than 256 bytes |
| 3 | A `user.email` received over OTLP is ranked `jwt` in `session_dims` (§4.5) | It is the rule of the PostgreSQL adapter of MR !4327, which builds `jwt_email` from the same value. Ranking it lower needs the route stored per raw row, which that adapter does not do | On the OTLP route the value is what the client states. No reader uses `session_dims.user_email` yet. To be changed together with that adapter |
| 4 | A count of `agent.usage.request` above 10^9 is stored as 0, with the original in `attrs` (§3.2) | The same rule as for `log_events` in the PostgreSQL adapter of MR !4327: an implausible value must not reach a sum | A 0 in the column is told from a real zero only by the key in `attrs` |
| 5 | The text of a usage row is cleaned and cut to 256 bytes, the sender's email included; the received value is not kept (§3.1) | The rule of the PostgreSQL adapter of MR !4327 for every text that can reach a key or an index | Two ids that differ only after the 256th byte become one. No real id is near that length |
| 6 | The `skills` fallback of `session_dims` reads two sources (§4.5) | A skill started by a slash command has no tool span; on a day with spans the rollup does not count its dispatch event | None: one invocation gives one of the two events (§3.5) |
