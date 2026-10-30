# EPMCDME-16441 backend: subagent cost, gate-discard visibility, cost-less sessions in the list

Branch EPMCDME-16441-backend-session-end-subagent-cost. Size L (22). Paths relative to `src/codemie/`; `R` = `repository/cli_analytics/postgres/rollups.py`, `D` = `.../reader.py`, `H` = `service/analytics/handlers/cli_analytics_handler.py`.

## Problem
1. A subagent that only has `agent.subagent.usage` totals (no `agent.usage.request` rows) has its tokens in `session_usage` (`token_source='subagent_usage'`, R:628-703) but no money anywhere: `cost_daily` is built from requests only (R:739-764) and the reader never reads `session_usage`.
2. The ingest project gate (ingestor.py:366 OTLP, :379 hooks; `service/analytics/cli_analytics_project_gate.py:52-81`) answers HTTP 200 on a discard and the CLI deletes its spool. Nothing is logged or counted, so "analytics not configured" cannot be told apart from a product defect.
3. `get_session_cost_facts` (D:673-675) is driven by `cost_daily`, so a session with dimension events but no cost rows is missing from the session list.

## Design

### 1. Subagent totals priced at query time (no rollup change)
- Displayed money is not read from stored `cost_usd`/`source_cost_usd`. It is priced in Python at query time: `get_session_model_costs` (D:364-384) sums `cost_daily` token columns per (session, raw model) with the normalised model name, and the handler passes them to `price_session_models` (`service/analytics/handlers/session_money.py:79`) with `CatalogPricer` (`coding_agent_pricing.py:62-107`, `lookup_cost_config`, no default-model fallback: an unknown model prices 0.0 and is logged as unpriced).
- Change: add the `session_usage` rows with `attrs->>'token_source' = 'subagent_usage'` to `get_session_model_costs` (D:364) and `get_session_detail_model_costs` (D:752), grouped by session_id and raw model (with the same normalised model name), as additional token sums UNIONed with the `cost_daily` sums. They flow through the existing pricer unchanged. No rollup change, no backfill (history corrects at once), no pricing callback, no worker catalog concern.
- Double counting is prevented on the write side: totals rows exist only while `_totals_are_a_source` holds, and the DELETE at R:666-670 removes them once the agent has request rows. The reader adds no guard. `_totals_are_a_source` (R:588-607) stays byte-identical.
- Scope: `_scope`/`_sessions_cte` (D:251, 304) restrict by `session_id IN (SELECT session_id FROM sel)`, and `session_usage` has `session_id`, so `_scope(f, "u.session_id")` applies as is. `session_usage` has no `user_email` or `query_source`, so no filter on those applies to it; sessions are windowed by `sel` (`started_at`), not by the row `day`. Plan-time check: the exact `session_usage` token column names (including the 5m/1h cache split) and that the UNION keeps the "5m/1h NULL when no split" rule.
- Time-bucket mismatch (accepted): `session_usage_hourly` never reads `session_usage`, and totals carry no time. Hourly or time-series views do not show totals tokens, while session money does.
- Accepted residuals, not scope: token totals in `get_cost_kpis` (D:334-339), `get_model_breakdown` and `get_users` still read `cost_daily`, so money includes subagent tokens while those token counts do not. Dispatch-level costs (log_events/spans, D:995-1117) do not see totals-only agents either.

### 2. Gate-discard visibility
- One structured log line per discard, emitted where the reason is known (ingestor.py:366 and :379): reason code, route (`otlp` or `hooks`), event count (`wire_count`), and a keyed hash (HMAC-SHA256, truncated) of the user id. The hash is a log field only, never a metric label. No email, no project name, no raw header or body values.
- No counter (amended after review: nothing would read it; the structured log line is the only signal). Reasons are `project_context_required`, `project_context_conflict`, `cli_analytics_disabled`. Not persisted, no migration. The HTTP response is unchanged.
- Amended (2026-10-28, owner decision, MR !4652 review): the discard line may also carry `session_count` and up to 3 `session_ids` taken from the request body. They are client-supplied text: sanitized, capped at 64 characters each, de-duplicated. Still no email, project name, raw user id or raw header value. The line is not rate limited. Contract and tests: AC1 of `docs/superpowers/tasks/2026-10-28-epmcdme-16441-be-review-fixes/spec.md`.
- The ingestor does not hold the user; the gate (`_ProjectIngestGate`) does. The plan picks the narrowest way for the gate to expose the hash.
- HMAC key: a NEW DEDICATED config setting. `MCP_AUTH_HMAC_SECRET` (config.py:533) is not reused: it also signs OAuth state and keys Redis encryption (`service/oauth/stores.py:37,41`, `oauth_security.py:82`). Empty key means no hash field is emitted, never an unkeyed hash. No existing pattern for hashing user ids in logs was found, so this one is new.

### 3. Cost-less sessions in the session list
- Relax the inner join in `get_session_cost_facts` (D:673-675) so a session in `sel` and `act` (it has dimension events) with no `cost_daily` rows is returned.
- Its cost shows as 0.0. NULL cost is not possible: `cost_usd: float` is required (`rest_api/models/cli_analytics.py:224`) and `_float_sort_key` (H:159) fails on None at H:259/302/505/595/764; the handler already defaults missing cost to 0.0 (H:758). No NULL-cost handling is introduced.
- REQUIRED (owner-approved): add an additive field `cost_available: bool` to the session-list row model (`rest_api/models/cli_analytics.py`, next to `cost_usd` at :224) and fill it in the handler (`get_sessions`, near H:758): True when the session has at least one token row (`cost_daily` or subagent totals) whose model has a catalog entry (a priced row); False when it has no token rows (a cost-less session from the relaxed join) or only rows of models missing from the catalog. A model with a catalog entry but no price for some token kind counts as priced, so `cost_usd` may be understated for such a session. Non-breaking: `cost_usd` stays a required float (0.0) and sorting is unchanged. Plan-time check (note only, not in scope): TS/UI clients ignore unknown fields.
- A log line states why cost rows are missing for the added sessions (count only, no user or project data).
- KPI cost totals do not change. The handler's priced-session universe (H:407-419) keeps turns, files, tool calls and cost aggregates over priced sessions only; a test pins this.

## Acceptance criteria
- AC-A: a totals-only agent (no request rows) adds its priced cost to the parent session's cost in the session list and the session detail, through the existing pricer.
- AC-B: an agent with request rows is not double counted; a later-arriving request row removes the totals row (existing DELETE) and session cost equals the request-only cost.
- AC-C: a totals model with no catalog price prices 0.0 and is logged as unpriced; no default-model price is used.
- AC-D: `_totals_are_a_source` and the rollup SQL are unchanged.
- AC-E: each gate discard reason logs one line (reason, route, event count, user hash when the key is set; also `session_count` and up to 3 sanitized `session_ids`, see the Design 2 amendment) (no counter, see amendment above); no email, project name or raw user id appears; an empty key emits no hash field; accepted requests log nothing.
- AC-F: a session with dimension events and no cost rows is in the session list with cost 0.0 and `cost_available` false; a priced session (at least one token row of a model with a catalog entry) has `cost_available` true, and a session whose only token rows are of models missing from the catalog has it false; KPI cost and priced aggregates are unchanged by the cost-less session's presence.
- AC-G: the live-DB tests for A, B, C and F pass, the existing cli_analytics suites pass, and the storage guide (`.ai-run/guides/integration/cli-analytics-storage.md`) documents the new cost path, the accepted residuals and the gate discard log line with the key rollout.

## Non-goals
- No CLI repo change, no SDK change, no prod measurement.
- No change to `_totals_are_a_source`, and no rollup or `cost_daily` change; no backfill.
- No fix for sessions with zero dimension events being dropped (`started_at IS NULL`, D:284-288); known residual.
- No change to `get_cost_kpis`, `get_model_breakdown`, `get_users` or dispatch-level cost.
- No persistence or migration for gate discards, and no change to the discard HTTP response.
- No NULL cost in the response model.

## Tests
Live-DB tests (pattern: `tests/codemie/repository/cli_analytics/postgres/test_live_session_duration_fallback.py`): totals-only agent appears in session and detail cost (A), agent with request rows not double counted (B), unknown model 0.0 plus unpriced warning (C), join relaxation with `cost_available` false vs true (F), KPI unchanged (F). Unit tests with caplog and counter for AC-E, including the empty-key case. SQL-string assertions only for AC-D.
