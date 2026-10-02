# CLI Analytics Event Contract

The six events CodeMie CLI (formerly the `sdlc-analytics` plugin) sends on top of OTel telemetry and the 13 activity hook events: their JSON, where each field is stored, and the rules a sender must keep. The storage, the recalculation and the operations are in [`cli-analytics-storage.md`](cli-analytics-storage.md).

The server accepts these events. A change to a field name, a type or a rule on this page is a change to the contract: agree it before a sender or the server implements it.

## Routes

| Route | Form |
|---|---|
| `POST /v1/analytics/cli-analytics/event-hooks` | NDJSON: one event per line. The sender's email comes from the token |
| `POST /v1/analytics/cli-analytics/logs` | An OTLP log record with an `event_type` attribute is a hook event: `event_type` holds the event's `type`, every other field is an attribute of the same name (`session.id` and `prompt.id` are read as well as `session_id` and `prompt_id`). The sender's email is the `user.email` attribute |

- Both routes store an event the same way; an event sent through both is stored once when it has an `event_id` (see Identity and re-sends).
- A request body above 5,242,880 bytes (`ANALYTICS_INGEST_MAX_BODY_BYTES`) is refused with 413. A sender that posts a whole spool in one request must split it: about 7,000 unsent `agent.usage.request` events reach the limit, and the same body is refused on every retry.

## Conventions

| # | Rule |
|---|---|
| 1 | One flat JSON object per line, keys in `snake_case`. The envelope is `type`, `timestamp`, `session_id`, `prompt_id` |
| 2 | A field that fills a column carries the column's name |
| 3 | Numbers, booleans, arrays and objects are JSON values of that type, not strings |
| 4 | A missing string is `""`; a missing number, boolean, array or object is `null` or is left out. Never `0`, never a default. Any subset of the fields is accepted: an event holding only `type`, `timestamp` and `session_id` is stored |
| 5 | `timestamp` is ISO-8601 in UTC with `Z`; milliseconds are allowed. Its source is fixed per event (see each event) and never the time of sending. On the OTLP route the event carries the same value in a `timestamp` attribute; without it the record time is used and the event can be stored a second time. Timestamps inside the payload use the same format |
| 6 | `event_id` is an opaque string: the server hashes it and never parses it. The examples use `<kind>:<session_id>:<discriminator>` |
| 7 | Text taken from a prompt is cut by the sender to 200 characters, text taken from a tool or a description to 300 |
| 8 | No field reuses a typed hook key with another meaning. The typed keys are `developer_name`, `codemie_project_name`, `cwd`, `git_branch`, `repo_remote`, `permission_mode`, `source`, `effort`, `tool_name`, `tool_use_id`, `tool_input`, `tool_output`, `error_message`, `error_type`, `reason`, `agent_id`, `agent_type`, `trigger`, `denial_reason`, `notification_type`, `prompt_body`, `skill_name`. A value under one of them is stored as text, so a nested value is never sent there |
| 9 | Command names (`commands`, `primary_command`, `command_name`) carry no leading slash |

## Where an event is stored

| Event | Stored in | Read by the recalculation into |
|---|---|---|
| `agent.usage.request` | `usage_requests` only | `session_usage`, `session_usage_hourly`, `cost_daily`, `subagent_invocations` |
| `agent.subagent.usage` | `hook_events` | `subagent_invocations`; `session_usage` when it is the only token source |
| `agent.session.summary` | `hook_events` | `session_dims` |
| `agent.session.env` | `hook_events` | `session_dims` |
| `agent.skill.dispatch` | `hook_events` | the skill counters and `session_dims.skills` |
| `agent.git.snapshot` | `hook_events` | `session_dims` |

- In `hook_events` a field named like a typed key (rule 8) goes to that column and is absent from `attrs`; every other field goes to `attrs` with its JSON type.
- None of the six moves a session's start or end: `session_dims.started_at` and `last_event_at` come from the 13 activity hook events only. A session that sent none of those is in no dashboard window.

## Common fields

Any event may carry them; all are optional.

| Field | Type | Goes to |
|---|---|---|
| `schema_version` | int, `2` | `session_dims.schema_version` |
| `event_id` | string | the identity of the event |
| `platform`, `entrypoint`, `client_version`, `codemie_cli_version` | string | `session_dims` columns of the same names |
| `developer_name`, `codemie_project_name`, `cwd` | string | typed columns of `hook_events` |
| `identity_source` | string | the rank of the sender's identity in `session_dims` |
| `story_id`, `story_source` | string | `session_dims.story_id`, `story_source` |
| `agent_id`, `agent_type` | string, `""` outside a subagent | typed columns |

The examples show `schema_version` and `event_id` and leave the other common fields out.

## `agent.usage.request`

One event per request to the model: one group of transcript lines with the same request id (else message id) and model. Every number is the maximum over the lines of the group, never their sum. It is sent once the group is closed: the first version stored is the final one.

```json
{
  "type": "agent.usage.request",
  "timestamp": "2026-09-29T10:59:41.512Z",
  "session_id": "3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11",
  "schema_version": 2,
  "event_id": "usage:3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11:req_011CTx9aB:claude-opus-5-5-20260101",
  "request_id": "req_011CTx9aB",
  "message_id": "msg_01H7Yq",
  "agent_id": "",
  "agent_type": "",
  "scope_kind": "main",
  "scope_name": "",
  "model_raw": "claude-opus-5-5-20260101",
  "model": "claude-opus-5-5",
  "speed": "standard",
  "inference_geo": "not_available",
  "service_tier": "standard",
  "input_tokens": 12,
  "cache_creation_5m_tokens": 0,
  "cache_creation_1h_tokens": 2500,
  "cache_read_tokens": 48000,
  "output_tokens": 295,
  "thinking_tokens": 289,
  "web_search_requests": 0,
  "web_fetch_requests": 0,
  "stop_reason": "tool_use",
  "is_api_error": false,
  "git_branch": "feature/ABC-123"
}
```

| Field | Type | Meaning | Column of `usage_requests` |
|---|---|---|---|
| `timestamp` | string | the last transcript line of the group: the one closest to the OTel record of the request | `ts` |
| `request_id` | string | the request id of the API; `""` when the transcript has none (requests through a proxy) | `request_id` |
| `message_id` | string | the message id; the group key when there is no request id | `message_id` |
| `agent_id`, `agent_type` | string | the subagent that made the request; `""` in the main thread | same names |
| `scope_kind` | string: `main`, `skill`, `agent` | where the request was made | `scope_kind` |
| `scope_name` | string | the skill name or the agent type; `""` for `main`, never `main` | `scope_name` |
| `model_raw` | string | the model as the API names it | `model_raw` |
| `model` | string | normalised, without speed or region suffixes | `model` |
| `speed` | string: `standard`, `fast` | sent explicitly; OTel says `normal` for `standard` | `speed` |
| `inference_geo`, `service_tier` | string | as the API reports them | same names |
| `input_tokens`, `output_tokens`, `cache_read_tokens` | int | token counts of the request | same names |
| `cache_creation_5m_tokens`, `cache_creation_1h_tokens` | int | cache writes by lifetime | same names |
| `thinking_tokens` | int | a part of `output_tokens`, never added to it | same name |
| `web_search_requests`, `web_fetch_requests` | int | server tool use | same names |
| `stop_reason` | string | the stop reason of the message | same name |
| `is_api_error` | bool | the line is an API error | same name |
| `git_branch` | string | the branch of the line | same name |
| any other key | | | `attrs` |

The discriminator of `event_id` is the request id, else the message id, with the raw model.

## `agent.subagent.usage`

One event at the end of a subagent.

```json
{
  "type": "agent.subagent.usage",
  "timestamp": "2026-09-29T11:04:12.000Z",
  "session_id": "3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11",
  "prompt_id": "p-7",
  "schema_version": 2,
  "event_id": "subagent:3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11:a41f09c2",
  "agent_id": "a41f09c2",
  "agent_type": "Explore",
  "description": "Trace the failing path",
  "tool_use_id": "toolu_abc123",
  "workflow_run": "",
  "spawn_depth": 1,
  "worktree": "",
  "started_at": "2026-09-29T11:01:02.250Z",
  "ended_at": "2026-09-29T11:04:12.000Z",
  "duration_ms": 189750,
  "model": "claude-haiku-4-5",
  "api_calls": 9,
  "tool_calls": 14,
  "tool_results": 14,
  "tool_errors": 1,
  "lines_added": 0,
  "lines_removed": 0,
  "files_changed": 0,
  "tools": {"Read": {"calls": 9, "errors": 0}, "Grep": {"calls": 5, "errors": 1}},
  "skills": {"sdlc-factory:tech-analysis-orchestrator": 1},
  "commands": [],
  "compactions": [],
  "usage": [
    {
      "model": "claude-haiku-4-5",
      "model_raw": "claude-haiku-4-5-20251001",
      "speed": "standard",
      "inference_geo": "not_available",
      "service_tier": "standard",
      "scope_kind": "agent",
      "scope_name": "Explore",
      "input_tokens": 900,
      "cache_creation_5m_tokens": 1200,
      "cache_creation_1h_tokens": 0,
      "cache_read_tokens": 31000,
      "output_tokens": 2100,
      "thinking_tokens": 0,
      "web_search_requests": 0,
      "web_fetch_requests": 0,
      "api_calls": 9
    }
  ]
}
```

| Field | Type | Meaning | Column of `subagent_invocations` |
|---|---|---|---|
| `timestamp` | string | the last line of the subagent transcript; equal to `ended_at` | — (`hook_events.ts`) |
| `agent_id` | string | the id of the subagent | `agent_id` |
| `agent_type` | string | its type | `agent_type` |
| `description` | string, at most 300 characters | the task it was given | `description` |
| `tool_use_id` | string | the tool call that started it | `tool_use_id` |
| `workflow_run` | string | the workflow run it belongs to | `workflow_run` |
| `spawn_depth` | int | how deep it was spawned | `spawn_depth` |
| `worktree` | string | its worktree path | `worktree` |
| `started_at`, `ended_at` | string | the first and the last line of its transcript | same names |
| `duration_ms` | int | `ended_at − started_at` | `duration_ms` |
| `model` | string | the model with the most requests | `model` |
| `api_calls`, `tool_calls`, `tool_results`, `tool_errors` | int | requests; tool calls; tool results; results that are errors | same names |
| `lines_added`, `lines_removed`, `files_changed` | int | edit diffs; distinct file paths | same names |
| `tools` | object `{tool: {"calls": int, "errors": int}}` | calls and errors per tool | `tools` |
| `skills` | object `{name: int}` | skill calls | `skills` |
| `commands` | array of strings | slash commands in order | `commands` |
| `compactions` | array, elements as in `agent.session.summary` | compactions | `compactions` |
| `usage` | array of objects | the requests summed by `(model, speed, inference_geo, service_tier, scope_kind, scope_name)` | `attrs` |

- A row of `usage` carries the field names of `agent.usage.request` plus `api_calls`, so one row maps onto one `session_usage` row.
- The totals in `usage` are never added to per-request rows. They become `session_usage` rows only when the session has no `agent.usage.request` of that agent and no OTel `api_request` at all; otherwise they stay in `subagent_invocations.attrs`.
- The totals are read from the agent's latest event whose `usage` is an array with at least one object. A later event without it replaces nothing.
- `attribution` is not sent; the recalculation sets it.

## `agent.session.summary`

Sent on every stop of the agent and at the end of the session. Each one is a separate event with its own `event_id`; the recalculation takes the latest by `timestamp`.

```json
{
  "type": "agent.session.summary",
  "timestamp": "2026-09-29T11:20:05.000Z",
  "session_id": "3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11",
  "schema_version": 2,
  "event_id": "summary:3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11:412",
  "is_final": false,
  "title": "Extend the analytics schema",
  "started_at": "2026-09-29T10:15:30.000Z",
  "ended_at": "2026-09-29T11:20:05.000Z",
  "duration_ms": 3875000,
  "turns": 7,
  "api_calls": 86,
  "tool_calls": 41,
  "tool_results": 41,
  "tool_errors": 2,
  "lines_added": 120,
  "lines_removed": 34,
  "files_changed": 5,
  "files_written": 1,
  "files_edited": 4,
  "primary_model": "claude-opus-5-5",
  "models": ["claude-opus-5-5", "claude-haiku-4-5"],
  "primary_command": "plan",
  "commands": ["plan", "commit"],
  "skills": {"sdlc-factory:sdlc-standard": 3},
  "agents": {"Explore": 2},
  "tools": {"Edit": {"calls": 12, "errors": 1}, "Bash": {"calls": 29, "errors": 1}},
  "compaction_count": 1,
  "compaction_pre_tokens": 182000,
  "compactions": [
    {"start": "2026-09-29T10:50:00.000Z", "end": "2026-09-29T10:50:04.200Z",
     "duration_ms": 4200, "trigger": "auto",
     "pre_tokens": 182000, "post_tokens": 41000, "dropped_tokens": 141000}
  ],
  "branch_dominant": "feature/ABC-123",
  "branch_counts": {"main": 1, "feature/ABC-123": 6},
  "git_branch": "feature/ABC-123",
  "client_version": "2.1.284",
  "client_versions": ["2.1.283", "2.1.284"]
}
```

| Field | Type | Meaning | Column of `session_dims` |
|---|---|---|---|
| `timestamp` | string | the last transcript line the summary covers | `summary_ts`; decides which summary is the latest |
| `is_final` | bool | `true` at the end of the session, `false` on a stop | `attrs` |
| `title` | string, at most 200 characters | the session title | `title` |
| `started_at` | string | the first transcript line | `activity_started_at`, not `started_at` |
| `ended_at` | string | the last transcript line | `ended_at` |
| `duration_ms` | int | `ended_at − started_at` | `duration_ms` |
| `turns` | int | user prompts | `turns` |
| `api_calls` | int | requests to the model | `api_calls` |
| `tool_calls`, `tool_results`, `tool_errors` | int | tool calls; tool results; results that are errors | same names |
| `lines_added`, `lines_removed` | int | edit diffs | same names |
| `files_changed`, `files_written`, `files_edited` | int | distinct files touched; written; edited | same names |
| `primary_model` | string | the model with the most requests | `primary_model` |
| `models` | array of strings, primary first | distinct normalised models | `models` |
| `primary_command` | string, no slash | the first command | `primary_command` |
| `commands` | array of strings, call order, duplicates kept, no slash | slash commands | `commands` |
| `skills` | object `{name: int}` | skill calls | `skills` |
| `agents` | object `{agent_type: int}` | subagent calls | `agents` |
| `tools` | object `{tool: {"calls": int, "errors": int}}` | calls and errors per tool | `tools` |
| `compaction_count` | int | compactions | `compaction_count` |
| `compaction_pre_tokens` | int | the sum of the tokens before each compaction | `compaction_pre_tokens` |
| `compactions` | array of `{start, end, duration_ms, trigger, pre_tokens, post_tokens, dropped_tokens}`; `duration_ms` in milliseconds | one element per compaction | `compactions` |
| `branch_dominant` | string | the branch with the most user lines; a tie goes to the later one | `branch_dominant`; the source of `feature_id` |
| `branch_counts` | object `{branch: int}` | user lines per branch | `attrs` |
| `git_branch` | string | the branch of the last line | — (`hook_events.git_branch`) |
| `client_version` | string | the latest client version | `client_version` |
| `client_versions` | array of strings | the versions seen, in order | `client_versions` |

- The summary covers the main thread only. Nothing of a subagent is added to a session counter.
- It carries no tokens, no active time and no cost: tokens travel in `agent.usage.request`, the other two exist in OTel only.
- The summary columns are written as a set, and only from a summary that is not older than the stored one. A session with a summary counts its slash commands from `commands` alone.

## `agent.session.env`

The environment of the session. The discriminator of `event_id` is a hash of the payload, so an unchanged environment sent again on the same day is one record.

```json
{
  "type": "agent.session.env",
  "timestamp": "2026-09-29T10:15:30.000Z",
  "session_id": "3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11",
  "schema_version": 2,
  "event_id": "env:3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11:9c1d2e3f4a5b6c7d",
  "platform": "claude-code",
  "entrypoint": "cli",
  "client_version": "2.1.284",
  "codemie_cli_version": "0.14.1",
  "provider": "codemie-proxy",
  "api_host": "codemie.example.com",
  "configured_model": "claude-opus-5-5",
  "effort": "high",
  "output_style": "default",
  "permission_mode": "default",
  "plugins": {"sdlc-factory": "1.4.2"},
  "mcp_servers": ["codegraph", "jira"],
  "os": "darwin",
  "arch": "arm64",
  "node_version": "22.11.0",
  "timezone": "Europe/Warsaw",
  "hostname_hash": "5f2b0c9d7a1e4b38"
}
```

| Field | Type | Column of `session_dims` |
|---|---|---|
| `timestamp` | string, the time of the hook | — |
| `platform`, `entrypoint`, `client_version`, `codemie_cli_version` | string | same names |
| `provider` | string: `anthropic`, `bedrock`, `vertex`, `litellm-proxy`, `codemie-proxy` | `provider` |
| `api_host` | string, host only | `api_host` |
| `configured_model`, `output_style` | string | same names |
| `effort`, `permission_mode` | string | typed columns of `hook_events`, then the same names |
| `plugins` | object `{name: version}` | `plugins` |
| `mcp_servers` | array of strings, names only | `mcp_servers` |
| `os`, `arch`, `node_version`, `timezone` | string | same names |
| `hostname_hash` | string; the server stores what it receives and does not define the algorithm | `hostname_hash` |

## `agent.skill.dispatch`

A skill started by a slash command of the user.

```json
{
  "type": "agent.skill.dispatch",
  "timestamp": "2026-09-29T10:16:02.000Z",
  "session_id": "3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11",
  "prompt_id": "p-1",
  "schema_version": 2,
  "event_id": "skill:3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11:p-1:sdlc-factory:sdlc-standard",
  "skill_name": "sdlc-factory:sdlc-standard",
  "command_name": "sdlc-factory:sdlc-standard",
  "command_source": "plugin"
}
```

| Field | Type | Goes to |
|---|---|---|
| `timestamp` | string, the time of the hook | `hook_events.ts` |
| `skill_name` | string; the event is counted only when it is non-empty | `hook_events.skill_name`, a typed column |
| `command_name` | string, no slash | `attrs` |
| `command_source` | string | `attrs` |

- One invocation of a skill gives one of two events, never both: this event when a slash command starts the skill, `agent.tool.start` with `skill_name` when the model starts it through its tool. The server adds the two counts on that ground.
- The arguments of the command are prompt text and are not sent.

## `agent.git.snapshot`

The state of the repository, sent at the end of the session.

```json
{
  "type": "agent.git.snapshot",
  "timestamp": "2026-09-29T11:25:00.000Z",
  "session_id": "3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11",
  "schema_version": 2,
  "event_id": "git:3f6c1e1a-5b0c-4d59-9f0e-2a1d6c0b7e11:b7e1c02",
  "git_branch": "feature/ABC-123",
  "repo_remote": "https://gitlab.example.com/org/repo.git",
  "git_email": "dev@example.com",
  "git_head_start": "9a4f3d1",
  "git_head_end": "b7e1c02",
  "git_commits": [
    {"sha": "b7e1c02", "time": "2026-09-29T11:10:44Z", "files": 3, "added": 120, "removed": 34}
  ]
}
```

| Field | Type | Goes to |
|---|---|---|
| `timestamp` | string, the time of the hook | `hook_events.ts` |
| `git_branch`, `repo_remote` | string | typed columns of `hook_events` |
| `git_email` | string | `session_dims.git_email` |
| `git_head_start`, `git_head_end` | string, abbreviated or full SHA | `session_dims` columns of the same names |
| `git_commits` | array of `{sha: string, time: string, files: int, added: int, removed: int}`; `files` is a count, not a list of paths | `session_dims.git_commits` |

## Identity and re-sends

| Event | Identity |
|---|---|
| with a non-empty `event_id`, either route | `(type, session_id, event_id)` |
| without `event_id` | the whole content, as for the 13 activity hook events |

- The first stored version of an identity stays. A re-send is dropped whatever its other fields, its route or the login it arrives under. A changed event must therefore carry a new `event_id`; this is why every summary has its own.
- Two sessions that share an `event_id` are both stored, as are two types.
- A repeat is recognised when it carries the same UTC day in `timestamp` and arrives within `CLI_ANALYTICS_DEDUP_RETENTION_DAYS` (14) days. This is why `timestamp` must be deterministic (rule 5): an event re-sent with a `timestamp` on the other side of midnight is stored twice.
- The identity is taken from the values as they are stored: cleaned, and the session id cut to 256 bytes.

## Values the server changes or refuses

| Input | Stored |
|---|---|
| A count of `agent.usage.request`: an integer or a digit string up to 10^9 | the integer |
| … above 10^9 | `0`, the original in `attrs` |
| … absent, `null` or `""` | NULL |
| … anything else (`"abc"`, `-1`, `12.5`, `true`, an array, an object) | NULL, the original in `attrs` |
| `is_api_error`: `true` / `false`, or the strings `"true"` / `"false"` in any case | the boolean |
| … anything else | NULL, the original in `attrs` |
| Text of a usage row, the sender's email included | NUL characters removed, broken Unicode repaired, cut to 256 bytes; `""` is stored as NULL |
| A count or a duration read from `attrs` by the recalculation (summary counters, `usage` rows, OTel `duration_ms`) | the integer part when it is a non-negative number within the column's range, with at most 30 digits after the point; otherwise NULL |
| A `timestamp` that cannot be parsed | the time the event was received |
| `started_at`, `ended_at` and the other times inside a payload that are not valid ISO-8601 UTC | NULL |
| A client time after today | ignored when idle sessions are purged, so it cannot keep a session past its retention |

A missing or refused value never fails the request and never drops the event.
