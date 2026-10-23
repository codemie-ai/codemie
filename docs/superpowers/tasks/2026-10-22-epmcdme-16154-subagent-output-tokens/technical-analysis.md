# Technical Research

**Task**: cli analytics otel subagent output_tokens
**Generated**: 2026-10-22
**Research path**: filesystem

---

## 1. Original Context

https://jiraeu.epam.com/browse/EPMCDME-16154 — part of the fix is in https://github.com/codemie-ai/codemie-code/pull/674 (CLI side; this repo is the CodeMie BACKEND, so find the backend part).

Ticket (Bug): "[CLI Analytics][Preview] Output tokens are undercounted for sessions using a subagent".
Description: CLI Analytics undercounts output_tokens for a Claude Code session that uses a subagent when the model is called directly through the Anthropic API and the subagent runs in the background (interactive plain `claude`, Claude Desktop without the CodeMie gateway). Session total can be smaller than its own subagent's output. Input, cache and cost are correct; only output tokens affected. Does not reproduce for `claude -p` (foreground subagent) or codemie-claude (CodeMie gateway).
Actual: e.g. session 42b807fc: preview session output 1,283 vs OTel api_request total 2,701; Explore subagent shows 1,496 (> session total).
Root cause (from comment): the session total takes subagent output from transcript-derived usage (agent.usage.request). With plain Claude Code, most Explore requests are written to the subagent transcript with partial usage (stop_reason = null, never updated: e.g. OTel 4,766 vs transcript 3), while OTel has final values. Session output = main + subagent partials + background calls. Explore row uses OTel and is correct. Claude Desktop OTel records carry no request_id, so pairing relies on the fingerprint match.
Suggested fix: when a transcript request is paired with its OTel record, take output_tokens from OTel (or the larger of the two); on the CLI side, do not send output tokens from transcript lines with stop_reason = null, or mark them as partial.
Expected: session total includes all output tokens of main session and subagents and equals the OTel api_request total regardless of launch mode.
Acceptance criteria:
 * For interactive plain claude and Claude Desktop (Anthropic API) with one or more subagents, session output_tokens equals the OTel api_request total.
 * The subagent ("agent · Explore") output equals the OTel total of the subagent's requests and is never greater than the session total.
 * claude -p and codemie-claude sessions stay correct.
Other docs (live in the CLI repo, not here): docs/ARCHITECTURE-OTLP-PLUGIN.md, a backend-plan.md handoff (event fields, rule for partial requests, acceptance checks) — NOT available locally.

---

## 2. Codebase Findings

### Existing Implementations
All in `src/codemie/repository/cli_analytics/postgres/` (guides: `.ai-run/guides/integration/cli-analytics-events.md`, `cli-analytics-storage.md`).
- Ingest of `agent.usage.request`: `otlp.py` ~L595-620 builds a `UsageRow`; `stop_reason` is stored as text in `usage_requests.stop_reason` (L610, L616) and never read anywhere else in `src/codemie` for analytics (grep: only otlp.py). Counts go through `usage_int` (invalid -> NULL). `ingestor.py` stores rows; `dirty.py` marks rollup keys (bits 1/16/32).
- Request pairing and the token source: `rollups.py` `_BUILD_REQUESTS` (L300-457), filling temp table `_cli_requests` (`_REQUESTS`, L242-250, column `req_kind` = matched | transcript | service | otel_only).
  - `tr` (L318-338): transcript groups per (session, request_id else message_id, model_raw); `max(u.output_tokens)` L324.
  - `o` (L346-362): OTel `api_request` rows from `log_events` (`event_kind = API_REQUEST`).
  - Tier 1 `t1` (L366-376): equal non-empty `request_id`, n-th with n-th, 1 h reach.
  - Tier 2 `c`/`d`/`q`/`walk` (L379-417): fingerprint, <=5 s apart, requires `b.model = a.model_raw` and EQUAL input, **output**, cache_read, cache_write (L386-390), only when at least one side lacks a request_id.
  - **'matched' branch, L426-431**: tokens come from `_TRANSCRIPT_COLUMNS` (L264-269, `a.output_tokens` at L267); from OTel `b` only `cost_usd`, `duration_ms`, `ttft_ms`, model_name, user_email, query_source. Guide states it: "a matched row takes its tokens from the transcript and only its cost from OTel".
  - 'transcript' branch L434-439 (unpaired, transcript tokens); unmatched OTel -> 'service' (plugin day) / 'otel_only' L445-457 with OTel tokens.
- Consumers of `_cli_requests` (all sum `r.output_tokens`): `session_usage` `_SESSION_USAGE` L566-607 (sum at L582), `session_usage_hourly` L613-634 (L625), `cost_daily` `_COST_DAILY` L647-665 (L658). Rebuilt per (day, session) by bit 16 (session_usage) / bit 1 (cost_daily).
- Dashboard reads: `reader.py` L233 (`get_cost_kpis`, `sum(output_tokens)` from `cost_daily`), L296 (`get_users`), L559, L628 — i.e. the session/preview totals come from `cost_daily`, which is fed by `_cli_requests`; so one change in `_BUILD_REQUESTS` fixes cost_daily, session_usage and hourly together.
- Subagent ("agent · Explore") row in the session detail: `reader.py` ~L740-815, `agent_usage` CTE sums OTel `log_events.output_tokens` (L776) per agentId via spans; `coalesce(au.output_tokens, pu.output_tokens)` L812. This is OTel-only, which is why it is correct (matches the ticket) and can exceed the transcript-based session total. No change implied by the ticket here.
- `subagent_invocations` (rollups.py L1298-1420): no token columns; `api_calls` from event or `ur.groups`. Subagent totals in `agent.subagent.usage.usage[]` are a `session_usage` source (`_TOTALS_ROWS`, L478-560) only when the session has no usage_requests for that agent and no OTel api_request.

### Architecture and Layers Affected
Repository/adapter layer only: PostgreSQL rollup SQL (`rollups.py`), ingest decode (`otlp.py`) if a partial marker is handled server-side. Alembic env `src/external/alembic_cli_analytics/` (schema in `versions/a1c1a0000006_cli_analytics_schema_extension.py`; `usage_requests.stop_reason` column already exists). Router/service/reader need no change per the findings above.

### Integration Points
`usage_requests` + `log_events` -> `_cli_requests` -> `cost_daily`, `session_usage`, `session_usage_hourly` -> `reader.py` KPIs. Backfill: `python -m codemie.repository.cli_analytics.postgres.backfill --from --to` re-queues bits for recompute (needed to correct already-stored sessions; days older than raw retention are not recomputed).

### Patterns and Conventions
SQL lives in module-level f-string constants; tests assert on SQL text. Guides require contract changes (events doc) to be agreed with the sender; the "never sum, max over lines" rule for usage groups is documented in the events guide.

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/integration/cli-analytics-events.md` (event fields; says `agent.usage.request` is sent once the group is closed, "the first stored version is the final one"; `stop_reason` documented only as "the stop reason of the message"; no partial-marker field). `cli-analytics-storage.md` (Request matching L126; Session tables; "Reading the session tables" note on matched rows taking tokens from the transcript). Neither mentions a partial-usage rule. CLI-side docs named in the ticket are not present locally.

### Architectural Decisions
Recorded: tokens "from one source only" per request (rollups.py comment L241); tier 2 fingerprint intentionally requires equal token counts; dedup keeps the first stored version of an `event_id`.

### Derived Conventions
Changes to matching/rollup SQL are accompanied by SQL-text unit tests and a doc update in the storage guide.

---

## 4. Testing Landscape

### Existing Coverage
`tests/codemie/repository/cli_analytics/postgres/test_rollups.py`: `test_request_matching_runs_before_every_token_table` (L550), `test_a_transcript_request_is_written_only_on_its_own_day` (~L1010, splits `_BUILD_REQUESTS` on `UNION ALL`: matched/transcript/unmatched), `test_the_fingerprint_compares_the_raw_model_and_the_token_counts` (L1028, asserts the equal-output-tokens predicate L1037), `test_session_usage_counts_otel_requests_including_matched_ones` (L958), `test_an_identifier_pair_is_looked_for_across_midnight...` (L134), `test_an_otel_request_without_a_transcript_pair_...` (L1052). Also `test_ingestor.py`, `test_otlp.py` (usage row decode), `test_migrations.py`.

### Testing Framework and Patterns
pytest; no-database unit tests that assert on generated SQL strings; guide says SQL behaviour was verified against PostgreSQL 14/17 separately (no DB-backed test found in this tree).

### Coverage Gaps
No test asserts which source supplies `output_tokens` for a matched row, nothing covers `stop_reason` in recompute, and no behavioural (data-in/data-out) test for the partial-transcript scenario.

---

## 5. Configuration and Environment

### Environment Variables
None specific (settings in `cli-analytics-storage.md`; ingest limits `ANALYTICS_INGEST_MAX_BODY_BYTES`, `CLI_ANALYTICS_DEDUP_RETENTION_DAYS`).

### Configuration Files
`src/codemie/configs/config.py` (analytics settings). No feature flags for this area.

### Feature Flags and Deployment Concerns
Per-project gate `applications.cli_analytics_enabled`. After deploy, stored rollups keep old values until recomputed (backfill command).

---

## 6. Risk Indicators

- Speculative: the smallest backend fix is in the 'matched' SELECT (rollups.py L426-431): replace `a.output_tokens` by `greatest(coalesce(a.output_tokens,0), coalesce(b.output_tokens,0))` (or prefer `b.`) via `_TRANSCRIPT_COLUMNS`, which is shared with the 'transcript' branch (no `b` there) so it needs a separate expression for matched.
- Tier 2 (Claude Desktop, no request_id) requires equal output_tokens (L388): a partial transcript (3) never equals OTel (4,766), so the pair is NOT matched; the transcript row and an OTel 'service' row are then both written (possible double counting of other tokens, not just output). Preferring OTel in matched rows alone will not fix Desktop; the fingerprint predicate would need to drop/relax output equality (or compare only when stop_reason is final). Unmeasured here.
- Relaxing the fingerprint raises false-pair risk; tier 2 ordering/ties logic (walk CTE) is intricate.
- `stop_reason` is stored but unused; a server-side "ignore partial" rule would depend on CLI sending it reliably (null vs ""), `usage_text` normalisation to be checked. The CLI-side contract (partial flag) is not available locally.
- Subagent-only-OTel sessions: `_TOTALS_ROWS` fallback unaffected.
- Historic data needs backfill; raw-retention limit (90 days).
- Docs (`cli-analytics-events.md`, `cli-analytics-storage.md` L141 and L263 statements) must be updated if the token source rule changes.

---

## 7. Summary for Complexity Assessment

The backend part lives in one SQL builder, `rollups.py::_BUILD_REQUESTS`, which pairs transcript `usage_requests` with OTel `api_request` rows (tier 1 by request_id, tier 2 by fingerprint) and, for a matched pair, takes all tokens from the transcript and only cost/duration from OTel. That `_cli_requests` table feeds `cost_daily` (dashboard/preview totals), `session_usage` and `session_usage_hourly`, so a single change there reaches all totals. The subagent row in the session detail already uses OTel directly (reader.py ~L776) and is correct.

The change surface is small (one SQL constant, maybe `otlp.py`, two guide pages, `test_rollups.py`), but technically delicate: for Claude Desktop the pairing is the fingerprint tier, which requires equal output_tokens and therefore cannot pair a partial transcript request with its OTel record, so "take OTel when paired" alone does not satisfy the Desktop acceptance criterion. No DB-backed tests exist; tests are SQL-text assertions.

Key risks are the fingerprint redesign (false pairs/double counting), dependency on an unseen CLI contract for partial markers (`stop_reason` null / new field), and the need for a backfill of stored days.

---

## 8. External References

The task names PR https://github.com/codemie-ai/codemie-code/pull/674 and the CLI docs `docs/ARCHITECTURE-OTLP-PLUGIN.md` and `backend-plan.md`. They are in the CLI repo, not available locally and not fetched (unresolved: no local path). Jira ticket URL not fetched; its text is in Section 1.
