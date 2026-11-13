# EPMCDME-16457 Copilot CLI Session Analytics — Backend Plan

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax for tracking. This is the backend half of EPMCDME-16457. The CLI half lives in `codemie-code` at `docs/superpowers/plans/2026-10-28-epmcdme-16457-copilot-cli-session-analytics-plan.md`.

**Ticket:** [EPMCDME-16457](https://jiraeu.epam.com/browse/EPMCDME-16457). Baseline: EPMCDME-15301 (CLI Analytics metrics), EPMCDME-16301 (pricing).

**Goal:** Session Detail shows the same tool activity and the correct harness for hook-only sessions (GitHub Copilot CLI) as it does for Claude Code, cache-read pricing resolves Copilot's dotted Claude model names, and Claude Code results do not change, except that sessions with dimensions but no usage row are now counted and listed (D4). Hook-only requests are priced at the catalog from their tokens at read time (D1).

## Why backend work is needed

Claude Code sends hooks **and** native OTel telemetry (spans `claude_code.tool*`, logs `api_request`, resource `service.name = claude-code`). Copilot CLI sends **hooks only**. Most of the backend already falls back to hooks when a session has no spans, so Session List and Overview work for Copilot with no change. Two Session Detail paths read OTel only:

| Detail value | Today | Claude | Copilot (hook-only) |
|---|---|---|---|
| Per-tool breakdown | `reader.py#get_session_detail_tools`: TOOL / EXEC spans only | ✅ | ❌ empty |
| Timeline | `reader.py#get_session_detail_events`: `log_events` + spans only | ✅ | ❌ empty |
| Harness name | `reader.py#get_session_detail_meta`: `otel_resources.service_name` via log/span `resource_id` only | ✅ "Claude Code" | ❌ "Unknown" |

The ticket names both gaps under "Confirmed Gaps": "Backend detail queries do not fully reuse the hook-derived metrics" and "Backend reporting does not fall back to the stored Copilot platform value".

## Already working, no change needed (verified)

- **Session List / Overview tool counts:** `get_tool_success_by_session` / `get_tool_usage` read `invocations_hourly` kind 1. That rollup already has a hook fallback (`rollups.py` ~1775-1785): calls = `agent.tool.start`, success = an `agent.tool.end` with the same `tool_use_id`, used only on days with no span (`_NO_SPAN_OF_KIND`).
- **Tool errors, tool totals and `api_calls` from `agent.session.summary`:** `_summary_or(...)` (`rollups.py` ~1190-1216); the latest summary wins by `ts`, `ingest_seq` (`rollups.py` ~1044-1051).
- **API calls / tokens / cost:** `agent.usage.request` → `usage_requests` → `_BUILD_REQUESTS` → `cost_daily`. The CLI sends one row per API request (Claude contract), so no `request_count` column is needed. **AC3 contract, confirmed against codemie-code (`copilot-cli-otlp.request-usage.ts`, `buildUsageRows`):** one `usage_requests` row per request with a distinct id: `apiCallId` from `session.usage_record`, else `messageId` per `assistant.message`; a shutdown-total row is attached to the last message's request id (same id, so it is not double-counted). Only a segment with no records and no messages falls back to one row per model (`<session>:<model>:seg<k>`); covered by `test_many_distinct_request_ids_count_as_many_api_calls` (193 ids -> 193 calls in `cost_daily` and the timeline). If the CLI sends one row per model or reuses ids, implement B1.
- **Pricing / cost (decision D1, see below):** `cost_daily.cost_usd` is NULL/0 for hook-only rows, but no analytics money is read from it: the handler prices every `(session, model)` token sum and every timeline request at the platform catalog (`price_session_models`, `CatalogPricer`). A hook-only session is therefore priced from its tokens, and a model the catalog cannot price lands in `unpriced_models` (AC7). See D1 and B4.
- **Attribution (user, project, repo, branch):** typed hook columns + `session_dims`.
- **Session universe (changed, D4):** the session list, KPIs, user tables and Detail start from `session_dims` and left-join `cost_daily`, so a session with no usage row at all (killed before its first `agentStop`, or a harness that reports no tokens) is counted and listed with empty cost facts, provided it has real activity and is not a sub-agent id (`_IS_LISTED`, see D4). Sessions that have usage rows report exactly the same values as before.

## Decisions

| # | Decision | Status |
|---|---|---|
| D1 | **Hook-only requests are priced at the catalog from their tokens, not at $0.** The timeline query returns the tokens (input, output, cache read, cache write 5m/1h) and the handler prices each row with `CatalogPricer`, exactly as it prices the session total. A model without a catalog price is reported in `unpriced_models`. No rollup or backfill change. | Decided (review of !4659) |
| D2 | Cache-read pricing and `unpriced_models` resolve the model through `normalize_model`; it learns dotted Claude versions (B4). The CLI keeps sending the literal name in `model_raw`. | Decided |
| D3 | Harness comes from `session_dims.platform` (B3), not a new lookup on `hook_events`. | Decided |
| D4 | Sessions with a `session_dims` row and no `cost_daily` row are counted and listed for every agent (EPMCDME-16496: GitHub Copilot Chat sessions report no tokens). Claude sessions with usage rows are unchanged; covered by `test_aggregates_keep_claude_session_results_and_add_the_session_without_usage`. Such a session is listed only with real activity (prompt, turn, API call or tool call) and when it is not a sub-agent's own id (`session_dims.is_subagent`, set at rollup time from the parent's `agent.subagent.start`; the named session is re-queued when that event arrives); covered by `test_listing_gate_drops_start_only_and_sub_agent_sessions` and `test_a_late_parent_start_flags_the_already_rolled_up_child_as_a_sub_agent`. A sub-agent id with its own cost rows stays listed, and Session Detail by id is not gated (a direct link to a hidden session still opens). | Decided (review of !4659) |
| D5 | AC4 detail is owned by the backend: the timeline request carries `cache_creation_tokens` and `reasoning_tokens` (thinking tokens, part of output) next to the input, output and cache-read tokens. Context per call is derivable from the row (cache read + cache write + input). | Decided (review of !4659) |
| D6 | **AC1: the backend does not distinguish managed (`codemie-copilot`) and native (`copilot`) Copilot CLI sessions.** Both arrive with `platform: 'copilot-cli'` and the same shape; `entrypoint` is stored (`session_dims.entrypoint`, B3-adjacent rollup column) but nothing in Session Detail, harness naming (`vocabulary.py`'s `platform` → label map) or the `_IS_LISTED` gate reads it. The two-mode check for AC1 is left to the Preview E2E (AC10); grep `entrypoint` in `reader.py` / `vocabulary.py` before changing this. | Decided (review of !4659) |

---

## Wire contract the CLI sends (for reference)

All events go through the existing hooks ingest (`agent.*` types, `hook_events` table). Relevant fields:

| Event | Typed columns | `attrs` (residual) |
|---|---|---|
| `agent.tool.start` | `tool_name` (runtime name: `bash`, `view`, `edit`, `create`, …), `tool_use_id` (Copilot's real `toolCallId`), `tool_input` | `platform: 'copilot-cli'`, `entrypoint: 'managed' \| 'native'`, `client_version`, `file_path` (write tools) |
| `agent.tool.end` / `agent.tool.error` | `tool_name`, `tool_use_id` (same ID as its start), `tool_output` / `error_message` | same as above |
| `agent.usage.request` | → `usage_requests` (`request_id` = `apiCallId` (Tier A) or `messageId` (Tier B), `model` normalized, `model_raw` literal, tokens, `stop_reason`, `scope_kind` `main` / `agent`) | `platform`, `entrypoint`, `client_version` and the other common fields |
| `agent.session.summary` | | Claude field names: `api_calls`, `tool_calls`, `tool_errors`, `tools`, `turns`, `models`, `primary_model`, `started_at`, `ended_at`, `is_final`, … |

`platform` is not in `HOOK_KNOWN_KEYS` (`vocabulary.py`), so it is stored in `hook_events.attrs`. That is the same place `fork_attribution.is_claude_code()` reads it from.

---

## Task B2 — Session Detail: hook fallback for tools and timeline

Rule (same as the rollups): **use the hook source only when the session has no span of the relevant kind.** Claude sessions always have spans, so their results are unchanged.

### B2.1 Per-tool breakdown — `reader.py#get_session_detail_tools`

Reuse the **same source as the Session List** (`invocations_hourly` kind 1), so List and Detail can't diverge. That is what the ticket's "reuse the hook-derived metrics available to session lists" asks for.

- [x] Keep the existing span query as the first branch, unchanged.
- [x] Add a second branch, used only for UTC days with no TOOL, TOOL_EXECUTION or INTERACTION span (the rollup's rule, review CR-001/CR-002):
  ```sql
  SELECT ih.name AS tool_name, sum(ih.calls)::bigint AS call_count, sum(ih.success)::bigint AS success_count
  FROM invocations_hourly ih
  WHERE ih.kind = 1 AND ih.session_id = $sid::text AND ih.name <> ''
    AND NOT EXISTS (SELECT 1 FROM spans s
                    WHERE s.session_id = $sid::text AND s.span_kind IN ({_TOOL}, {_EXEC}, {_INTERACTION})
                      AND s.ts >= date_trunc('day', ih.hour, 'UTC')
                      AND s.ts < date_trunc('day', ih.hour, 'UTC') + interval '1 day')
  GROUP BY ih.name
  ```
  Combine the two branches with `UNION ALL`, keep the existing `ORDER BY call_count DESC, tool_name {_C} LIMIT 20`, and keep the row shape `(tool_name, call_count, success_count)`.
- [x] Why not query `hook_events` directly: `invocations_hourly` already applies the dedup rules (a start is counted only on a no-span day; one end per start; the Skill / Agent exclusions). Re-implementing them in the reader would let List and Detail drift apart.
- [x] Handler (`cli_analytics_handler.py#get_session_detail`): no change. `tool_calls_success` / `tool_success_rate` are already computed from these rows against the scalar `tool_call_count` (`tool_facts_daily`, which has the same hook fallback).
- [x] **Freshness:** `invocations_hourly` and `tool_facts_daily` are recompute rollups, so a just-finished hook-only session shows an empty tool breakdown (and `tool_call_count`) until the next recompute. Cadence: the `cli_analytics_rollup_refresh` job runs every `CLI_ANALYTICS_ROLLUP_REFRESH_SECONDS` (default 30 s), so the lag is about 30 s plus queue time. This is accepted, the same lag every hook-only session already has in Session List.

### B2.2 Timeline — `reader.py#get_session_detail_events`

- [x] **Tool rows:** add a branch from `hook_events`, used only for UTC days with no TOOL, TOOL_EXECUTION or INTERACTION span, and only for tool names up to `MAX_KEY_BYTES` (review CR-001..CR-003):
  ```sql
  SELECT h.ts, 'tool_call', '', 0, 0, 0, 0, NULL, NULL, NULL, h.tool_name
  FROM hook_events h
  WHERE h.session_id = $sid::text AND h.event_type = 'agent.tool.start' AND h.tool_name <> ''
    AND octet_length(h.tool_name) <= {MAX_KEY_BYTES}
    AND NOT EXISTS (SELECT 1 FROM spans s
                    WHERE s.session_id = h.session_id AND s.span_kind IN ({_TOOL}, {_EXEC}, {_INTERACTION})
                      AND s.ts >= date_trunc('day', h.ts, 'UTC')
                      AND s.ts < date_trunc('day', h.ts, 'UTC') + interval '1 day')
  ```
  One row per start, which matches the span branch (one TOOL span per call).
- [x] **API request rows:** add a branch from `usage_requests`, dropped for a request when an `api_request` log event lies in its UTC day widened by the rollup's pairing reach (`_GROUP_REACH`, 1 h), tested once per grouped request on its `max(ts)`, never per row (the `cost_daily` plugin-vs-OTel rule is per day, and the rollup pairs a transcript request with its OTel record across midnight, so a per-row day test would list a pair twice or cut a re-sent request in two):
  - Group rows by `coalesce(nullif(request_id,''), nullif(message_id,''))` + `coalesce(model_raw,'')`, taking `max()` of each token field and `max(ts)`. This is the same identity and merge rule as `_BUILD_REQUESTS` (`rollups.py` ~423-445). A request re-sent with its totals attached must appear once. The reader cannot reuse the build: `_cli_requests` is a transaction-scoped TEMP table, so the grouping is replicated (a row with neither ID is its own group, as there).
  - Columns: `ts`, `'api_request'`, `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens` (5m + 1h, as the rollup), the 5m / 1h split, `reasoning_tokens` (`thinking_tokens`), `''`.
  - No cost column: the handler prices the row at the catalog (decision D1), so the timeline and the session total agree and an unpriced model is reported.
  - API-error rows (`is_api_error`) are **not** excluded: `_BUILD_REQUESTS` only uses the flag for `partial`, so every group counts in `cost_daily.api_call_count`; the timeline matches it.
  - **Invariant, tested:** the number of timeline `api_request` rows equals `cost_daily.api_call_count` for the session, so List and Detail cannot diverge.
- [x] Keep the final `ORDER BY timestamp, event_type, tool_name, model_name`.

### B2.3 Out of scope (no change)

- `get_session_detail_dispatches`: built on Claude `Agent` / `Skill` spans and fork hooks; Copilot sub-agents are not part of this ticket.
- `get_session_detail_scalars`: already hook-aware through `tool_facts_daily`, `turns_daily` and `lines_daily`.

## Task B3 — Harness name fallback

### B3.1 Reader — `reader.py#get_session_detail_meta`

`session_dims.platform` already holds the latest hook `platform` of the session (`rollups.py:1277`: `coalesce(lv.v->>'platform', <platform from OTel names>)`, column listed in `_SESSION_DIMS_COLUMNS`), and the query already joins `session_dims` as `d`. No new lookup on `hook_events` is needed.

- [x] Change the last select column to `coalesce(nullif(o.service_name, ''), nullif(d.platform, '')) AS harness_service_name`. OTel stays first, so Claude is unchanged, and the column name stays the same, so the handler needs no change.
- [x] Freshness: `session_dims` is recomputed with the other rollups (same lag as B2.1). Until then the harness is `Unknown`, as today.

### B3.2 Vocabulary — `vocabulary.py`

- [x] Add `"copilot-cli": "GitHub Copilot CLI"` to `HARNESS_LABEL_OVERRIDES`. Without it, the generic fallback would render "Copilot Cli".
- [x] Also `"copilot-desktop": "GitHub Copilot Desktop"` (EPMCDME-16526, same mechanism, same branch).
- [x] `"claude-code"` already maps to "Claude Code", so a hook-only Claude session (OTel disabled) also stops showing "Unknown". This is a side improvement and consistent with the rule.

### B3.3 Other harness usages

- [x] `humanize_harness_name` / `harness_service_name` are used **only** by Session Detail (`cli_analytics_handler.py` ~913, `models/cli_analytics.py` ~280). Session List and aggregates have no harness column today, so no other query changes. Re-grep before merging.

## Task B4 — Model-name resolution for cache-read pricing (decision D2)

For a hook-only request `cost_daily.model_name` is the **`model_raw`** the client sent (`rollups.py` ~527-529: `coalesce(a.model, '')` as `model`, `coalesce(a.model_raw, '')` as `model_name`). The handler prices cache reads by `model_name` (`_price_cache_reads`, `_session_cache_read_cost`). Copilot reports dotted Claude versions (`claude-sonnet-4.6`, `claude-haiku-4.5`), which `lookup_cost_config` does not resolve, so cache-read cost would be 0 and the model flagged in `unpriced_models` (which lists only models with cache-read tokens above 0).

- [x] `coding_agent_pricing.normalize_model`: after the Bedrock-prefix strip, turn a dotted version in a `claude-*` name into dashes (`claude-sonnet-4.6` → `claude-sonnet-4-6`, `claude-haiku-4.5` → `claude-haiku-4-5`). Every other name, dashed Claude names included, passes through unchanged. Lookup-only: stored `model_name` values and Claude results are unchanged.
- [x] Tests in `tests/codemie/service/analytics/handlers/test_coding_agent_pricing.py` (extend): the dotted cases above, `anthropic.claude-sonnet-4.6`, an unchanged `gpt-5.3-codex`, and an already dashed Claude name. Names that still do not resolve (for example `gpt-5.4-mini`, if absent from the catalog) correctly appear in `unpriced_models`.
- [x] No ingest, rollup or schema change.

---

## Tests

Follow the existing patterns in `tests/codemie/repository/cli_analytics/`. Live Postgres tests (`test_reader_live_*`, `test_live_*`) seed rows and run the rollups.

| Test | Covers |
|---|---|
| `tests/codemie/repository/cli_analytics/test_vocabulary.py` (extend) | `humanize_harness_name('copilot-cli') == 'GitHub Copilot CLI'`; `'claude-code'` is unchanged; `None` / `''` → `'Unknown'` |
| `tests/codemie/repository/cli_analytics/postgres/test_reader_live_copilot_detail.py` (new) | Seed a **hook-only** session: 3 `agent.tool.start` (`view`, `bash`, `bash`, distinct `tool_use_id`s), 2 `agent.tool.end` + 1 `agent.tool.error` for the same IDs, `platform: 'copilot-cli'`, and 3 `agent.usage.request` rows (one re-sent with the same `request_id` and higher input). Run the rollups. Assert: **AC — Detail tools = `view` 1/1, `bash` 2 calls / 1 success**; equal to `get_tool_usage` for that session (List = Detail); the timeline has 3 `tool_call` + 3 `api_request` rows (the re-sent request appears once, with the max tokens) and **the `api_request` count equals `cost_daily.api_call_count`**; timeline `cost_usd` equals the catalog price of the same tokens and the unpriced model is reported (D1; unit-level in `test_cli_analytics_handler.py`, since the handler is not run live); the row carries `cache_creation_tokens` and `reasoning_tokens` (D5); `harness_service_name == 'copilot-cli'` (from `session_dims.platform`) |
| Same file — sub-agent and error rows | an `is_api_error` usage row is counted in the timeline as in `cost_daily` (the counts stay equal); a `scope_kind = 'agent'` row is still one request |
| Same file — Claude regression | Seed a Claude session with TOOL / EXEC spans **and** hook `agent.tool.*` events. Detail tools and timeline come from spans only, with no doubled rows; harness comes from `otel_resources` |
| Same file — mixed | A session with OTel resource `service.name = claude-code` and hook `platform = copilot-cli` → OTel wins |
| `test_reader.py` (extend, SQL-shape) | the new branches appear only behind `NOT EXISTS (... spans ...)` / `NOT EXISTS (... log_events ...)` guards; the meta query selects `d.platform` as the fallback |
| `tests/codemie/service/analytics/handlers/test_coding_agent_pricing.py` (extend) | Task B4 |

Run the repository's standard lint and test commands for `tests/codemie/repository/cli_analytics` and `tests/codemie/service/analytics`.

## Delivery

- One migration (`a1c1a0000007`): `session_dims.is_subagent` (backfilled from the raw start events still kept) and a partial index `hook_events (agent_id) WHERE event_type = 'agent.subagent.start'` for its rollup lookup, so the listing reads no raw `hook_events`. The flag is sticky (a purge of the raw event does not clear it). Reader-only SQL otherwise, one vocabulary entry and a lookup-only `normalize_model` change. Rows already ingested benefit as soon as the rollups recompute. (The timeline event gains two optional fields, `cache_creation_tokens` and `reasoning_tokens`; the OpenAPI baseline is updated.)
- Merge before or together with the CLI release (gate G4 in the CLI plan). Until then Copilot sessions keep today's behavior (empty Detail breakdown, "Unknown", unresolved cache-read pricing). Nothing breaks.

## Open points (decide during implementation)

1. **D1:** settled by the review of !4659: priced at the catalog, no $0 exception.
2. **Fallback granularity (settled):** the guard is per UTC day, like the rollup, so a session with spans on one day and hooks only on another keeps both. Covered by `test_hook_fallback_is_decided_per_utc_day`, `test_timeline_hook_branch_is_off_for_a_day_with_any_tool_kind_span` and `test_timeline_skips_an_over_long_hook_tool_name`. The `usage_requests` branch follows the rollup's pairing instead (day widened by 1 h, per request group): `test_timeline_usage_branch_is_decided_per_utc_day`, `test_timeline_lists_a_request_once_when_its_otel_record_is_across_midnight`.

## Contingency (not planned)

**B1 — `request_count` column.** Needed only if a future Copilot build stops writing both `session.usage_record` and one `assistant.message` per request. Spec:
- migration: nullable `usage_requests.request_count`;
- ingestor: `UsageRow` mapping;
- `_BUILD_REQUESTS`: `max(request_count)` per group and `api_call_count += coalesce(request_count, 1)`;
- backfill.
