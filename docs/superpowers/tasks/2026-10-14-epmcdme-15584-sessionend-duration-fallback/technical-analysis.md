# Technical Research

**Task**: cli analytics session duration otel hook sessionend
**Generated**: 2026-10-14
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-15584 [CLI Analytics][Sessions] Session duration is understated when SessionEnd hook is intermittently cancelled on normal /exit.

Problem: On some normal Claude Code exits, the SessionEnd analytics hook is intermittently cancelled (terminal shows 'SessionEnd hook [codemie hook --agent claude-code-otlp] failed: Hook cancelled'). When this happens, no SessionEnd record is stored and the local spool has no endedAt. CLI Analytics then calculates session duration from the last earlier hook event, so displayed DURATION is shorter than actual wall-clock session duration. Cost, tokens, requests, turns and tools still arrive correctly — only duration is wrong. Frequency ~11.5% of normal exits in reproduction.

Expected: A normal /exit must always produce reliable session-completion timing. Either (1) SessionEnd is recorded on every normal exit, or (2) if SessionEnd is unavailable, duration is derived from another reliable final telemetry timestamp so DURATION reflects the actual session lifetime.

Acceptance criteria:
1. Normal /exit reliably records session completion.
2. Missing/cancelled SessionEnd does not truncate session duration.
3. Session duration matches actual session lifetime within documented tolerance.
4. Cost/tokens remain unaffected.
5. Automated coverage includes repeated normal exits and cancelled SessionEnd fallback behavior.

---

## 2. Codebase Findings

### Scope Boundary — Important

This checkout (`codemie-dev/codemie`) is the **backend ingestion/storage/analytics service only**. The actual CLI hook sender (`codemie hook --agent claude-code-otlp`, the Claude Code `SessionEnd` hook invocation, its OTLP exporter, and the local spool with `endedAt`) lives in a **separate repository** (`codemie-code`), which is not present in this checkout (`codemie-repos/` is empty; a repo-wide grep for the literal string `SessionEnd` across `src/`, `tests/`, `docs/`, `.ai-run/` returns zero matches). This repo can only implement:
- Server-side fallback/recalculation logic for session duration when the `agent.session.end` hook record never arrives.
- Server-side validation/tests on the aggregation and display of `duration_ms`/status once a corrected value is emitted.

Acceptance criterion 1 ("normal /exit reliably records session completion") and criterion 5's "repeated normal exits" scenario are primarily CLI-repo concerns (hook execution reliability) and are out of scope for implementation here, though criteria 2–4 and the fallback half of criterion 5 are addressable server-side.

### Root Cause (server-side)

Session duration (list view and session-detail view) is computed as `last_event_at − started_at`, where `last_event_at` only advances on 13 "activity" hook event types (`DIMENSION_HOOK_TYPES` in `vocabulary.py`, which includes `agent.session.end` = the SessionEnd hook mapping). When `agent.session.end` is never stored (hook cancelled client-side), `last_event_at` sticks at the previous hook event's timestamp, so duration is short — exactly the reported symptom.

A more robust pair of columns already exists in `session_dims` and is **not currently used** for the duration-display queries: `activity_started_at` / `ended_at` / `duration_ms`. These are populated either from `agent.session.summary` (sent "on every stop of the agent and at the end of the session" — i.e. not solely gated on the SessionEnd hook) or, when no summary exists, from a fallback raw-range query (`_RAW_RANGE`) that unions `hook_events`, `usage_requests`, `log_events`, `spans`, `metric_points` — a later/truer "last telemetry" timestamp than the hook-only `last_event_at`. This is the natural "another reliable final telemetry timestamp" fallback the ticket asks for; it is already computed server-side by the rollup engine but not read by the duration-displaying queries.

### Existing Implementations

- `src/codemie/repository/cli_analytics/postgres/reader.py`
  - `get_session_durations` (~line 462-469) — `duration_ms = _ms(last_event_at) - _ms(started_at)`, session-list duration, hook-only, truncates on cancelled SessionEnd. **Primary fix locus.**
  - `get_session_detail_meta` (~line 545-572) — `duration_ms = coalesce(_ms(last_event_at) - _ms(started_at), 0)`, session-detail duration, same issue; also defaults to `0` rather than `null` on missing data (contract-violating per the project's null-not-zero convention, see below).
  - `get_session_cost_facts` (~line 482-519) — returns raw `started_at`/`last_event_at` consumed by the handler.
  - `_sessions_cte` (~line 154-192) — reads `d.started_at`, `d.last_event_at` from `session_dims`; does **not** currently select `activity_started_at`/`ended_at`/`duration_ms`.
  - `get_session_detail_dispatches` (~line 626-760) — unrelated per-dispatch (subagent) duration fix from prior ticket EPMCDME-15512; same file/session, different column (`subagent_duration_ms`), not whole-session duration.
- `src/codemie/service/analytics/handlers/cli_analytics_handler.py`
  - `get_sessions` (~line 584-650, specifically 621-625) — `duration_ms = max(0, (last_event - started_at).total_seconds()*1000)`, computed in Python from the same hook-only timestamps. Second place the truncation happens, parallel to the reader-level computation.
  - `_build_dispatches` / `_duration_for_kind` / `_clamp_to_session` / `_bar_position` (~lines 75-106, 780-830) — null-safe duration handling and clamp-to-session-bounds precedent from EPMCDME-15512, directly transferable pattern.
- `src/codemie/repository/cli_analytics/postgres/rollups.py`
  - `_COUNTER_VALUES` (~line 1109-1114) — defines `activity_started_at`, `ended_at`, `duration_ms` for `session_dims` via `_summary_or(...)` (summary-or-fallback).
  - `_FALLBACK_DURATION = _ms_between("fb.last_ts", "fb.first_ts")` (~line 1067); `_RAW_RANGE` (~line 924-928) unions `hook_events, usage_requests, log_events, spans, metric_points` — the pre-existing reliable timestamp source.
  - `_SUMMARY_EVENT = "agent.session.summary"` (~line 671); `_SUMMARY_WINS` / `_FALLBACK_RUNS` / `_FALLBACK_DUE` (~line 865-869) — logic for when summary vs. raw fallback applies.
  - `_COUNTER_JOINS` (~line 947-963) — `sm` = latest `agent.session.summary` by `(ts, ingest_seq)`.
  - DIMENSIONS upsert (~line 1622-1637) — `started_at = LEAST(...)`, `last_event_at = GREATEST(...)` — the hook-only columns driving the bug; separate from the `SESSION`-family (bit 32) merge that writes `activity_started_at`/`ended_at`/`duration_ms`.
- `src/codemie/repository/cli_analytics/postgres/vocabulary.py`
  - `DIMENSION_HOOK_TYPES` (~line 79-93) — the 13 hook types (incl. `agent.session.end`, `agent.session.stop`) that move `started_at`/`last_event_at`. This is the "last earlier hook event" the ticket refers to.
- `src/codemie/repository/cli_analytics/postgres/otlp.py` — `_duration_ns` clamps >30-day spans to 0, a separate unrelated zero-ing bug noted in prior EPMCDME-15512 research; not the cause here but present in the same ingest layer.
- `src/external/alembic_cli_analytics/versions/a1c1a0000006_cli_analytics_schema_extension.py` — migration adding `ended_at`, `duration_ms`, `activity_started_at`, `summary_ts` columns to `session_dims` (~lines 57-58, 167, 179).
- `src/codemie/rest_api/models/cli_analytics.py` — session list/detail response models; nullability of `duration_ms` not yet verified against the EPMCDME-15512 precedent (worth checking if duration becomes nullable).
- `src/codemie/configs/config.py` (~lines 195-226, cross-field validator at ~988) — `CLI_ANALYTICS_*` / `ANALYTICS_*` env vars with defaults; `CLI_ANALYTICS_PG_INGEST_ACQUIRE_TIMEOUT_MS` (default 1000ms) is explicitly chosen to stay under "the plugin's 2s hook timeout" — this 2s client-side hook timeout is the likely proximate cause of the ~11.5% SessionEnd cancellation rate, though that timeout itself lives in the CLI repo.
- `src/codemie/repository/cli_analytics/postgres/session_derived.py` — fallback/merge logic for `session_dims` columns when no summary exists.

### Architecture and Layers Affected

- **Repository (raw SQL)**: `postgres/reader.py` — `get_session_durations`, `get_session_detail_meta`, `get_session_cost_facts`, `_sessions_cte`. Primary fix surface.
- **Recalculation / rollup engine**: `postgres/rollups.py` — already computes the more robust `activity_started_at`/`ended_at`/`duration_ms`; likely reused rather than rebuilt.
- **Service/handler**: `cli_analytics_handler.py::get_sessions` — second place doing the same hook-only duration math, in Python.
- **Vocabulary/taxonomy**: `vocabulary.py` — defines which hook types count as "activity" (drives `last_event_at`).
- **Ingest**: `postgres/otlp.py` and the hook-event ingest path — where `agent.session.end` / `agent.session.summary` land in `hook_events`.
- **REST**: session list/detail response models in `rest_api/models/cli_analytics.py`.
- **DB/storage**: `session_dims` columns, migration `a1c1a0000006_cli_analytics_schema_extension.py`.
- **Out of scope here (separate repo)**: CLI hook execution/timeout layer, OTLP exporter, local spool — these live in `codemie-code`.

### Integration Points

- `codemie.service.analytics.handlers.cli_analytics_handler` → `codemie.repository.cli_analytics.ports` (interface) → `codemie.repository.cli_analytics.postgres.reader` (implementation)
- `codemie.repository.cli_analytics.postgres.reader` → `codemie.repository.cli_analytics.postgres.vocabulary` (`DIMENSION_HOOK_TYPES`, `SpanKind`, `EventKind`)
- `codemie.repository.cli_analytics.postgres.rollups` → `vocabulary` (event name constants); populates `session_dims`, which `reader.py` reads
- `codemie.rest_api.routers.cli_analytics` → `cli_analytics_handler` (inferred from router/handler naming convention; not independently re-verified this pass)
- CLI (`codemie-code`, not in this checkout) → ingest routes (`POST /v1/analytics/cli-analytics/event-hooks`, `/logs`) → `hook_events`/`log_events`/`spans` tables → `rollups.py` recalculation → `session_dims` → `reader.py` → handler → REST response
- `asyncpg` (via `AnalyticsPgEngine`) — raw SQL execution for `PostgresCliAnalyticsReader`; no new third-party packages implicated.

### Patterns and Conventions

- Two deliberately separate time-pair families per `.ai-run/guides/integration/cli-analytics-storage.md`: hook-driven (`started_at`/`last_event_at`, decide dashboard window membership) vs. raw-record-driven (`activity_started_at`/`ended_at`, "give the length of the session"). **The bug is that the duration-display queries read the wrong pair.**
- `agent.session.summary` is sent redundantly "on every stop of the agent AND at the end of the session" with its own `is_final` flag and its own `duration_ms`/`ended_at` fields computed independently of the SessionEnd hook — this is the contract's existing reliable-fallback mechanism, already wired into `session_dims` via `summary_ts`/`_SUMMARY_WINS`.
- Missing-numbers-are-null-never-zero contract rule (per `cli-analytics-events.md` rule 4), and the `_duration_for_kind`/`_clamp_to_session` precedent from EPMCDME-15512 — any new/changed duration field should follow the same null-not-zero, clamp-to-session-bounds discipline rather than silently defaulting to `0` (as `get_session_detail_meta` currently does with `coalesce(..., 0)`).
- SQL text-shape tests (`test_reader.py`) and unit tests calling private static methods (`_build_dispatches`, `_duration_for_kind`) directly with hand-built row dicts are the established test pattern for this module; live Postgres integration tests (`test_reader_live_dispatches.py`-style) skip without `TEST_LOCAL_PG_URL`.
- Commit convention: `EPMCDME-<id>: <imperative summary>` with `Generated with AI` / `Co-Authored-By: codemie-ai <codemie.ai@gmail.com>` trailers.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/cli-analytics-events.md` — full CLI analytics event contract. States: "None of the six moves a session's start or end: `session_dims.started_at` and `last_event_at` come from the 13 activity hook events only. A session that sent none of those is in no dashboard window." Documents `agent.session.summary` (`is_final` bool, `started_at`/`ended_at`/`duration_ms` fields, sent "on every stop of the agent and at the end of the session").
- `.ai-run/guides/integration/cli-analytics-storage.md` — storage/recalculation guide. Key sections: "Two pairs of times" (`started_at`/`last_event_at` = hook times, decide dashboard windows; `activity_started_at`/`ended_at` = first/last transcript line, give the session length); `duration_ms` rule ("with a summary → summary; without one (fallback) → `ended_at − activity_started_at`"); `session_dims` merge-kinds table (fallback kind computed from raw rows, written only while `summary_ts` is NULL). **No dedicated SessionEnd-cancellation section exists — this ticket is meant to fill that gap.**
- No other `.ai-run/guides/integration/` file references SessionEnd, hook cancellation, or duration fallback specifically.

### Architectural Decisions

- Design doc: `docs/superpowers/specs/2026-09-23-cli-analytics-postgres-storage-design.md` (source of design decisions D1/D3/D5/D10/D11 governing the fixed API response-shape contract).
- Plan: `docs/superpowers/plans/2026-09-23-cli-analytics-postgres-storage.md`.
- Handoff: `docs/superpowers/handoffs/2026-09-24-cli-analytics-postgres-storage-handoff.md`.
- A task scaffold for this exact ticket already exists at `docs/superpowers/tasks/2026-10-14-epmcdme-15584-sessionend-duration-fallback/.state.json` (flow `sdlc-light`, branch `EPMCDME-15584_sessionend-duration-fallback`, phase `main`) but contained no `spec.md`/`plan.md`/`technical-analysis.md` prior to this file.
- Highly relevant prior work: `docs/superpowers/tasks/2026-10-06-fix-subagent-duration-timeline/` (EPMCDME-15512, "Timeline shows subagent duration in ms instead of real run time"). Different bug (per-subagent dispatch duration, not whole-session duration) but touches the exact same two files (`reader.py` dispatch query, `cli_analytics_handler.py::_build_dispatches`) and establishes the project's convention for this exact class of problem: missing/cancelled end timestamp → `duration_ms: null` (never `0`), clamp duration to session bounds, prefer the actual event-based end timestamp over a tool/launch-ack span. That plan also found that the ticket's named event ("SubagentHandback") did not exist as such — the real hook was `SubagentStop`. By analogy, this ticket should verify whether "SessionEnd" maps 1:1 to `agent.session.end`, or whether the Claude Code `Stop` hook (which drives `agent.session.summary`) is a more reliable substitute, against real telemetry before implementing.
- EPMCDME-15583 (sibling recent commit, "Exclude rejected and failed Write/Edit calls...") is in the same CLI analytics area but has no dedicated task folder found and is not cross-referenced in guides or docs.

### Derived Conventions

(See Patterns and Conventions in Section 2 — guides exist for this domain, so conventions are primarily documented rather than purely derived.)

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/analytics/handlers/test_cli_handler.py` — CLIHandler unit tests: summary cards, DAU/MAU, agents/llms/users/errors/top-performers/top-versions/proxy-endpoints/tools-usage aggregations; repositories 3-level nested aggregation with `session_duration` sub-agg (sum of `session_duration_ms`); CLIInsightsHandler tests including a `completed_sessions` filter on `codemie_cli_session_total` with status in [completed, failed, interrupted].
- `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` — legacy/alternate CLI analytics handler tests, same domain; check for overlap before adding new tests.
- `tests/codemie/repository/cli_analytics/postgres/test_reader.py` — Postgres-backed CLI analytics reader tests; likely covers raw session/duration row reads, the direct target for new fix-coverage tests.
- No test file anywhere references `SessionEnd`, hook execution, OTLP spool, or hook-cancellation fallback (confirmed via repo-wide grep).

### Testing Framework and Patterns

- pytest 8.3.1, pytest-asyncio 1.4.0 (`@pytest.mark.asyncio`), pytest-mock 3.14.0, pytest-httpx 0.35.0, pytest-cov, pytest-env, pytest-xdist.
- Constructor-injection fixture pattern (`mock_user`, `mock_repository` as `MagicMock(spec=...)`, composed into a `handler` fixture, overridden per test class).
- `AsyncMock(side_effect=[...])` / `AsyncMock(return_value=...)` to stub pipeline/repository calls — no real Elasticsearch/Postgres hit; tests split "query builder" assertions from "result parser" assertions.
- `@patch(..., new_callable=AsyncMock)` for cross-module collaborators (e.g. `UserIdentityResolver`).
- Hand-built literal JSON/dict fixtures mimicking real aggregation responses (no `factory_boy`-style factory library).
- Class-based grouping (`TestCLISummary`, `TestCLIAgents`, `TestCLIRepositories`, etc.) with docstring "Verify X" phrasing and Arrange/Act/Assert comments in longer tests.
- No domain-specific `conftest.py` found for this handlers test package; fixtures are local to each test file.

### Coverage Gaps

- No tests for SessionEnd hook invocation/cancellation (the hook code is not in this repo — must be addressed in the CLI repo).
- No tests for repeated normal `/exit` cycles (idempotency across multiple session-start+end pairs) — this is also primarily a CLI-repo concern.
- No tests for a cancelled/missing SessionEnd falling back to another duration source — nothing in `_parse_cli_repositories_result` or `_build_cli_insights_user_detail_aggregation` distinguishes a truncated duration from a real one; `status` (completed/failed/interrupted) is used for filtering `avg_duration_ms` but no test asserts correct behavior when `status=interrupted` or `session_duration_ms` is 0/null due to cancellation.
- No tests verifying tolerance/accuracy of recorded duration vs. actual lifetime (acceptance criterion 3) — inherently a CLI-side (hook timer) concern not fully testable from this backend repo alone, though the server-side fallback-column selection logic (reading `activity_started_at`/`ended_at` vs `started_at`/`last_event_at`) is testable here with constructed row fixtures.
- No tests confirming cost/tokens are unaffected when duration-computation logic changes (acceptance criterion 4) — existing `get_cli_summary`/token-total tests are not parameterized against duration-fallback scenarios; new tests should assert cost/token aggregations are unchanged when a session row has `status=interrupted` or a fallback-computed duration.
- `test_reader.py` is the natural home for new unit tests exercising `get_session_durations`/`get_session_detail_meta` with fixture rows that have `last_event_at` stuck early but `activity_started_at`/`ended_at`/`duration_ms` populated (simulating cancelled SessionEnd + successful fallback summary/raw-range computation).

---

## 5. Configuration and Environment

### Environment Variables

All declared in `src/codemie/configs/config.py`, all optional with code defaults, none listed in `.env.example`:

- `CLI_ANALYTICS_PG_URL` (default "") — separate analytics Postgres DB; empty = use app DB.
- `CLI_ANALYTICS_PG_SCHEMA` (default `codemie_analytics`)
- `CLI_ANALYTICS_PG_POOL_SIZE` (default 8)
- `CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS` (default 30000)
- `CLI_ANALYTICS_PG_WORK_MEM` (default "32MB")
- `CLI_ANALYTICS_PG_INGEST_ACQUIRE_TIMEOUT_MS` (default 1000) — chosen to stay under "the plugin's 2s hook timeout"; **directly relevant** — this 2s client-side hook timeout is the likely proximate trigger for SessionEnd cancellation, though the timeout itself is CLI-side.
- `CLI_ANALYTICS_PG_INGEST_STATEMENT_TIMEOUT_MS` (default 10000) — intentionally above the plugin's 2s wait so large spool re-sends aren't repeatedly rejected.
- `CLI_ANALYTICS_RAW_RETENTION_DAYS` (default 90)
- `CLI_ANALYTICS_ROLLUP_RETENTION_DAYS` (default 365, must be ≥ raw retention)
- `CLI_ANALYTICS_SESSION_RETENTION_DAYS` (default 0/off)
- `CLI_ANALYTICS_DEDUP_RETENTION_DAYS` (default 14) — event_id/content-hash dedup window; relevant if a fallback-timestamp fix causes retried/duplicate SessionEnd-equivalent events.
- `CLI_ANALYTICS_ROLLUP_REFRESH_SECONDS` (default 30)
- `CLI_ANALYTICS_ROLLUP_BATCH_SIZE` (default 5000)
- `CLI_ANALYTICS_PARTITION_PREMAKE_WEEKS` (default 4)
- `CLI_ANALYTICS_MAINTENANCE_INTERVAL_MINUTES` (default 60)
- `ANALYTICS_INGEST_MAX_BODY_BYTES` (default 5,242,880)
- `ANALYTICS_DEFAULT_PAGE_SIZE` (default 20) — unrelated dashboard paging.

### Configuration Files

- `src/codemie/configs/config.py` (~lines 195-226, validator ~988) — Pydantic Settings class for all `CLI_ANALYTICS_*`/`ANALYTICS_*` vars.
- `.env.example` — does not list any `CLI_ANALYTICS_*` vars; all rely on code defaults.

### Feature Flags and Deployment Concerns

- No feature flags found to enable/disable CLI analytics ingestion or tune hook timeout server-side — the "~2s hook timeout" is a CLI-side constant, not configurable from this repo.
- No Docker/CI/deploy-template references to CLI analytics or hook execution (`deploy-templates/`, `docker-compose.yml`, `docker/` all clean of `CLI_ANALYTICS` references) — this is purely an application-config concern; no infra changes needed for the server-side fix.
- The actual fix for "SessionEnd intermittently cancelled on normal /exit" (hook execution/timeout reliability) belongs in the CLI repo (`codemie-code`), outside this repository's scope.
- Secrets: none relevant beyond normal DB connection-string handling for `CLI_ANALYTICS_PG_URL`.

---

## 6. Risk Indicators

- **Cross-repository scope split**: The actual hook-cancellation root cause (CLI-side `codemie hook --agent claude-code-otlp` execution and the client's ~2s hook timeout) lives in a separate repository (`codemie-code`) not present in this checkout. A server-side-only fix in this repo can satisfy criteria 2–4 (duration not truncated, matches lifetime, cost/tokens unaffected) by reading the already-computed `activity_started_at`/`ended_at`/`duration_ms` fallback columns, but cannot by itself satisfy criterion 1 ("normal /exit reliably records session completion") or the "repeated normal exits" half of criterion 5 — those require a companion change in the CLI repo. This must be flagged for cross-repo coordination/separate ticket.
- **Unverified mapping assumption**: Whether `agent.session.summary` (which already provides an independent `ended_at`/`duration_ms`) is reliably sent even when the Claude Code `SessionEnd` hook is cancelled is not yet confirmed against real telemetry. If `agent.session.summary` is also gated on the same cancelled hook path, the server-side fallback alone is insufficient and a CLI-side change becomes mandatory, not optional. The prior EPMCDME-15512 ticket discovered a similar mapping assumption was wrong (ticket named "SubagentHandback" but the real hook was `SubagentStop`) — same verification discipline should be applied here before implementation.
- **Two parallel truncation sites**: The bug exists in two separate places doing equivalent hook-only duration math — `reader.py::get_session_durations`/`get_session_detail_meta` (SQL) and `cli_analytics_handler.py::get_sessions` (Python). A fix must cover both or risk inconsistent duration values between the list view and detail view.
- **Null-vs-zero contract violation already present**: `get_session_detail_meta` currently does `coalesce(duration_ms, 0)` rather than returning `null`, violating the project's documented "missing numbers are null, never zero" convention (established in the EPMCDME-15512 precedent). Any fix should correct this rather than propagate it.
- **No existing automated coverage** for SessionEnd cancellation, repeated-exit scenarios, or duration-fallback correctness anywhere in the test suite (confirmed via repo-wide grep) — acceptance criterion 5 requires net-new test design, not an extension of existing tests.
- **Downstream consumers not fully inventoried**: `rest_api/models/cli_analytics.py` response-model nullability for `duration_ms` was not verified this pass; if duration becomes nullable, downstream dashboard/REST contract changes may ripple further than the reader/handler layer.
- **Dedup/retry interaction**: `CLI_ANALYTICS_DEDUP_RETENTION_DAYS` exists specifically to handle retried spool sends; if the eventual CLI-side fix involves re-sending a SessionEnd-equivalent event, the dedup window and content-hash logic need validation against the new event shape.
- **Related but distinct ingest bug noted in passing**: `postgres/otlp.py::_duration_ns` clamps >30-day spans to 0 — unrelated to this ticket but in the same ingest layer; do not conflate during implementation.
- **Requirements clarity**: task_context is detailed and includes concrete acceptance criteria — no clarity risk on the requirements side.

---

## 7. Summary for Complexity Assessment

This task touches three architectural layers in the backend repo: the Postgres repository layer (`reader.py` — the two SQL duration-computation sites), the service/handler layer (`cli_analytics_handler.py::get_sessions` — a parallel Python duration computation), and test coverage (net-new unit tests in `test_reader.py` and the CLI handler test files, since zero existing coverage touches this scenario). The actual data needed for the fix — `activity_started_at`, `ended_at`, `duration_ms` on `session_dims` — already exists and is already populated by the rollup engine (`rollups.py`) via a documented summary-or-raw-range fallback; this is not a greenfield computation, it is a "read from the right column" fix. Estimated file-change surface is small-to-medium: 2 files for the core fix (reader.py, cli_analytics_handler.py), possibly 1 for a REST model nullability change, plus 1-2 test files, and no new third-party dependencies or migrations are required (the schema already has the needed columns from a prior migration).

Technical novelty is low: the project has an established, directly analogous precedent (EPMCDME-15512, same session, same two files, same duration-null-not-zero/clamp-to-bounds convention) solved three weeks prior for subagent-level duration. This ticket is the session-level counterpart of that exact pattern, which significantly de-risks implementation — the convention, test style, and even some of the fixture-building code can be reused almost directly.

The primary complexity driver is not the backend fix itself but the cross-repository scope ambiguity: acceptance criterion 1 and part of criterion 5 depend on CLI-side hook execution reliability in a repository not present in this checkout, and it is not yet verified whether the existing `agent.session.summary` fallback is itself immune to the same cancellation path. If verification shows the summary event is reliably independent of the cancelled SessionEnd hook, this becomes a low-complexity, well-precedented backend-only fix. If it is not independent, the ticket requires a companion CLI-repo change that is entirely outside this analysis's visibility, which should be flagged as a scope/coordination risk rather than scored as backend complexity. Test coverage posture for this exact scenario is currently zero, so criterion 5 compliance requires authoring new fixture-based unit tests from scratch, following the established "hand-built row dict + private static method" pattern already used in `test_reader.py` and the CLI handler test files.
