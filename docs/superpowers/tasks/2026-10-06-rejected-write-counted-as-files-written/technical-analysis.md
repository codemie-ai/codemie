# Technical Research

**Task**: cli-analytics postgres rollups session_files_daily dead-sessions wasted-cost tool-execution-success
**Generated**: 2026-10-06
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-15583: CLI Analytics counts a Write operation as a successful file write even when the user rejects the operation in the permission dialog and no file is actually created or modified. This causes the session's `files_changed`/`files_written` to be nonzero when it should be zero, which wrongly excludes the session from Dead Sessions / Wasted Cost classification (via `_dead_sessions()` in `src/codemie/service/analytics/handlers/cli_analytics_handler.py`, which classifies a session as dead based on `files_changed == 0`). Confirmed root cause: the `session_files_daily` Postgres rollup in `src/codemie/repository/cli_analytics/postgres/rollups.py` (inside the `RECOMPUTE` list, around line 1517) derives `is_written`/`is_edited` purely from the tool-call span (`span_kind = TOOL`) or the `agent.tool.start` hook event matching a Write/Edit tool name — i.e. the call being *made*, not it *succeeding*. A user-rejected (permission-denied) or failed Write/Edit still sets is_written/is_edited = true.

IMPORTANT — there is already an UNCOMMITTED candidate fix in the working tree on the current branch (`EPMCDME-15583_rejected-write-counted-as-files-written`) touching exactly this block of `rollups.py`. Read it via `git diff -- src/codemie/repository/cli_analytics/postgres/rollups.py` as part of your research. Summary of what it does: for the span branch, it adds a `LEFT JOIN LATERAL` to the matching execution span (`span_kind = _EXEC`, same `tool_use_id`, timestamp within [-1h, +1d]) and requires `e.success = 'true'` in the `bool_or` condition. For the hook-event fallback branch (sessions with no span data), it adds a `LEFT JOIN LATERAL` to the `agent.tool.end` hook event (same `tool_use_id`, same window) and requires that match (`e.ended`) in the `bool_or` condition, reasoning that `agent.tool.end` (unlike `agent.tool.denied`/`agent.tool.error`) only fires on a call that actually completed. It claims to mirror an existing success-join pattern already used by `invocations_hourly` in the same file.

Your job: evaluate this candidate diff rigorously, don't just trust it. Verify: (1) does `invocations_hourly` really use this exact join pattern, and does the candidate correctly mirror it (join keys, time window, NULL/false handling via `coalesce`)? (2) is `e.success`/`success` really a text column with values `'true'`/`'false'` (string), confirm the actual column type and existing usage elsewhere in this file or `reader.py`. (3) what are `_EXEC`, `_TOOL`, `_SPANS`, `_TOOL_END`/`_literal(_TOOL_END)`, `_HOOK_FILE_PATH`, `_NO_SPAN_OF_KIND`, `_IS_TOOL_START`, `_hook_key` — confirm their real definitions and that the diff uses them correctly in context. (4) Is the `interval '1 hour'`/`interval '1 day'` window reasonable/consistent with other joins in this file, or arbitrary? (5) Could this double-count or miss rows given `GROUP BY 1,2,3` plus the new LEFT JOIN LATERAL (fan-out risk — does the LATERAL return at most one row per outer row)? (6) Does the hook-fallback branch's reasoning actually hold — confirm hook event types/names (`agent.tool.start`, `agent.tool.end`, `agent.tool.denied`, `agent.tool.error` or similar) and that `agent.tool.end` truly only fires on completion, not on denial. (7) Is there missing handling for `WRITE_TOOLS`/`EDIT_TOOLS` definitions elsewhere unaffected by this diff?

Scope: this ticket covers ONLY the `session_files_daily` rollup's is_written/is_edited success-gating (and whatever `_dead_sessions()`/`files_changed` consumption needs to pick that fix up for free). A SEPARATE already-identified ticket, EPMCDME-15448 ("read-only file access counted as Files Changed", in `reader.py`'s file-fact reading, likely `get_file_facts_by_session` or similar), is explicitly OUT OF SCOPE — note where it lives but do not analyze or recommend changes for it. Also check `tests/codemie/repository/cli_analytics/support/hook_event_builders.py` (helper exists per repo search) and any existing test file(s) for `rollups.py`/`session_files_daily`/`_dead_sessions()` to establish the test-fixture pattern and framework (pytest + Postgres test harness, presumably) for adding coverage of: a successful Write/Edit, a user-rejected Write/Edit, and a failed Write/Edit.

Also note (for the caller, not to act on): other uncommitted/unrelated local-dev-environment changes exist on this branch (.env.example, config/customer/customer-config.yaml enabling the `features:cliAnalytics` flag, docker-compose.yml port remap) — these look like deliberate local setup to reproduce/test the ticket and are explicitly out of scope for research; confirmed seen via `git status`/`git diff --stat` (`.env.example` +13 lines staged, `config/customer/customer-config.yaml` +1/-2 staged, `docker-compose.yml` +1/-1 unstaged), not analyzed further. An untracked `.ai-run/sdlc-factory/doctor.json` is also present and unrelated.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/repository/cli_analytics/postgres/rollups.py` — the `RECOMPUTE` list builds all rollup tables; the `session_files_daily` entry (~line 1517) is the target of the fix. It has two UNION ALL branches: a span-based branch (`k.kinds & _SPANS <> 0 ... s.span_kind = _TOOL`) and a hook-event fallback branch for sessions lacking span data (`_NO_SPAN_OF_KIND`), gated on `_IS_TOOL_START`.
- `src/codemie/repository/cli_analytics/postgres/rollups.py:1557-1582` — `invocations_hourly`'s kind-1 (tool-call) branch, the pre-existing, production success-join pattern the candidate explicitly mirrors. It already computes a `success` column via the same two-branch (span LATERAL / hook LATERAL) technique.
- `src/codemie/repository/cli_analytics/postgres/otlp.py:182,745` — `SpanRow.success: str | None`, populated by popping a raw string `"success"` attribute off the span — confirms `success` is a nullable TEXT column, not boolean, and is compared via string literals `'true'`/`'false'` elsewhere (e.g. `rollups.py:1038`, pre-existing: `AND x.success = 'false'`).
- `src/codemie/repository/cli_analytics/vocabulary.py:74-75` — `WRITE_TOOLS: tuple[str, ...] = ("Write",)`; `EDIT_TOOLS: tuple[str, ...] = ("Edit", "MultiEdit", "NotebookEdit")`. Confirmed unchanged by the candidate diff (`git diff --stat` shows only `rollups.py` and the out-of-scope `docker-compose.yml` touched).
- `src/codemie/repository/cli_analytics/postgres/reader.py:360-392` — `get_file_facts_by_session()`. Its `per_file` CTE computes `bool_or(is_written) AS written, bool_or(is_edited) AS edited` per `(session_id, file_path)`; its `fl` CTE then computes **`files_changed = count(*)`** (count of distinct file_path rows, unconditional), separately from `files_written = count(*) FILTER (WHERE written)` and `files_edited = count(*) FILTER (WHERE edited)`.
- `src/codemie/service/analytics/handlers/cli_analytics_handler.py:275-288` — `_dead_sessions(cost_facts, files_by_session)`: for each cost-incurring session, classifies it dead if `files_by_session[sid]["files_changed"] == 0`. Two call sites: `get_overview()` (~line 238) and `get_cost()` (~line 880).

### Architecture and Layers Affected

- **Repository / Postgres rollup layer** (`src/codemie/repository/cli_analytics/postgres/rollups.py`) — this is the only layer the candidate diff touches. The `session_files_daily` RECOMPUTE statement's two UNION ALL branches (span-based and hook-fallback) are the exact change surface.
- **Repository / reader layer** (`src/codemie/repository/cli_analytics/postgres/reader.py`) — `get_file_facts_by_session()` consumes `session_files_daily` but is **not touched** by the candidate diff. See the Integration Points / Risk sections below for why this matters.
- **Service / handler layer** (`src/codemie/service/analytics/handlers/cli_analytics_handler.py`) — `_dead_sessions()` consumes `files_changed` (not `files_written`/`files_edited`) and is **not touched** by the candidate diff.
- **Scheduler / runtime layer** (`src/codemie/repository/cli_analytics/postgres/runtime.py`, `settings.py`, `cli_analytics_jobs.py`) — unaffected; just the delivery mechanism that periodically re-runs the RECOMPUTE SQL.
- No API/router layer changes; no DB migration/DDL changes (confirmed no `success` column DDL changes needed — the column already exists and is already used in `invocations_hourly`).

### Integration Points

- `session_files_daily` → `reader.py:get_file_facts_by_session()` → `files_by_session` dict → `_dead_sessions()` in `cli_analytics_handler.py` (both `get_overview()` and `get_cost()` call sites) and → the `code_changes` block in `get_cost()` (lines ~884-910), which uses `files_written`/`files_edited`.
- **Critical finding (discrepancy in the ticket's causal chain):** The candidate fix corrects `is_written`/`is_edited` on `session_files_daily` rows. Via the `per_file`/`fl` CTEs in `reader.py`, this correctly flows through to `files_written`/`files_edited` (consumed by the `code_changes` KPI block in `get_cost()`). **However, `files_changed` — the field `_dead_sessions()` actually keys on — is computed as an unconditional `count(*)` of distinct `file_path` rows in `session_files_daily`, independent of `is_written`/`is_edited`.** Rows in `session_files_daily` are created for *any* tool call with a non-empty `file_path` (the span branch's WHERE clause is `s.span_kind = _TOOL AND s.file_path <> ''` — no restriction to Write/Edit tool names at all; same shape for the hook fallback branch). This means a session whose only file-touching tool call was a **rejected or failed Write/Edit** still produces a `session_files_daily` row (file_path is still present on the attempted call) and therefore still increments `files_changed`, **regardless of whether the candidate's `is_written`/`is_edited` fix is applied.** Two research threads converged on this independently (code-structure thread's Risk #3, and the test/fixture thread's dedicated finding) — see Section 6 for the full implication.
- External boundary: the hook events (`agent.tool.start`/`agent.tool.end`/`agent.tool.denied`/`agent.tool.error`) are *emitted* by a CLI hooks plugin that lives **outside this repository**; this repo only ingests/consumes those event-type strings. The claim that `agent.tool.end` fires only on completed (not denied/errored) calls is well-supported by this repo's *internal* self-consistency (a dedicated `agent.tool.denied` vocabulary entry exists; `invocations_hourly`'s pre-existing, production logic already relies on the same assumption) but was **not independently verified against the actual emitting source**, which is not present in this checkout.

### Patterns and Conventions

- **Success-join idiom** (the pattern the candidate mirrors): for a span-based success check, `LEFT JOIN LATERAL (SELECT max(x.success) FROM spans x WHERE x.span_kind = _EXEC AND x.tool_use_id = <outer>.tool_use_id AND x.ts >= <outer>.ts - interval '1 hour' AND x.ts < <outer>.ts + interval '1 day') e ON <outer>.tool_use_id <> ''`, then test `e.success = 'true'`. For the hook fallback, `LEFT JOIN LATERAL (SELECT true AS ended FROM hook_events x WHERE x.session_id = k.session_id AND x.event_type = _literal(_TOOL_END) AND x.tool_use_id = <outer>.tool_use_id AND x.ts >= <outer>.ts - interval '1 hour' AND x.ts < <outer>.ts + interval '1 day' LIMIT 1) e ON <outer>.tool_use_id <> ''`, then test `e.ended`. This exact idiom is used verbatim, unchanged, by the pre-existing `invocations_hourly` statement (`rollups.py:1557-1582`) and the candidate transplants it faithfully — same join keys (span branch joins on `tool_use_id` only, deliberately not scoped to session; hook branch scopes to both `session_id` and `tool_use_id`), same `interval '1 hour'`/`interval '1 day'` window (reused verbatim, not invented), same NULL-handling idiom (`bool_or` ignores NULL inputs; outer `coalesce(bool_or(...), false)` retained unchanged).
- **Fan-out safety**: the span-branch LATERAL is a bare, unqualified aggregate (`max(x.success)`), which Postgres guarantees returns exactly one row; the hook-branch LATERAL has an explicit `LIMIT 1`. Both are confirmed single-row-per-outer-row, matching `invocations_hourly`'s identical construction — no double-counting risk via the `GROUP BY 1, 2, 3` that both branches retain unchanged.
- **Symbol definitions** (all confirmed correct in context):
  - `_TOOL, _EXEC, _INTERACTION = int(SpanKind.TOOL), int(SpanKind.TOOL_EXECUTION), int(SpanKind.INTERACTION)` (`rollups.py:154`)
  - `_SPANS = int(RollupFamily.SPAN_FACTS)` (`rollups.py:~143-153`) — dirty-key gating bit, unchanged by the diff
  - `_TOOL_END, _TOOL_ERROR = "agent.tool.end", "agent.tool.error"` (`rollups.py:859`); `_literal(text)` SQL-quotes a string literal (`rollups.py:131-132`)
  - `_HOOK_FILE_PATH = _json_text("h.attrs", "file_path")` (`rollups.py:1443`) — pulls `file_path` out of a hook event's JSON `attrs`, unchanged by the diff
  - `_NO_SPAN_OF_KIND` (`rollups.py:1434-1437`) — `NOT EXISTS` guard gating the hook-fallback branch to days/sessions with no span data at all, unchanged
  - `_IS_TOOL_START = f"h.event_type = {_literal(_TOOL_START)}"` where `_TOOL_START = "agent.tool.start"` (`rollups.py:1438-1439`), unchanged — the new `e.ended` condition layers on top of this, does not replace it
  - `_hook_key(column, limit) = f"{column} <> '' AND octet_length({column}) <= {limit}"` (`rollups.py:1446-1449`), unchanged
- **Hook/vocabulary data model**: `DIMENSION_HOOK_TYPES` (`vocabulary.py:79-93`) treats `agent.tool.start`, `agent.tool.end`, `agent.tool.error`, `agent.tool.denied` as four distinct, co-equal event types — the existence of a dedicated `agent.tool.denied` type is itself evidence that `agent.tool.end` is not expected to fire on denial. `dirty.py`'s `_SPAN_HOOK_TYPES` set notably does **not** include `agent.tool.denied` — a denied-only event (without its paired `agent.tool.start`) would not by itself mark `RollupFamily.SPAN_FACTS` dirty; real traffic always pairs them, but test fixtures must include both events to be realistic.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/cli-analytics-storage.md` — directly relevant. Documents the span/hook-fallback-pair derivation of `turns_daily`, `tool_facts_daily`, `invocations_hourly`, and `session_files_daily` ("a pair with spans uses spans") and documents `files_changed`/`files_written`/`files_edited` as "distinct paths of session_files_daily by flag". It does **not** mention gating on tool success/failure for file-touch rows — consistent with this being the previously-undocumented bug this fix addresses. Also documents `CLI_ANALYTICS_ROLLUP_RETENTION_DAYS`, the backfill/repair CLI (`python -m codemie.repository.cli_analytics.postgres.backfill ...`), and rollup-queue operational alerts.
- `.ai-run/guides/integration/cli-analytics-events.md` — documents the raw event schema contract (`files_changed`, `files_written`, `files_edited` fields) — contract-level, not rollup-logic documentation.
- `docs/superpowers/tasks/2026-09-23-epmcdme-15207-cli-analytics-wasted-cost-pct/plan.md` — a prior, related fix in the same CLI-Analytics-Efficiency/Dead-Sessions area (Epic EPMCDME-13580), which fixed the Wasted Cost % denominator. Notes a separate adjacent bug, EPMCDME-15214 (line-change metrics = 0), as untouched. No guide or plan doc exists specifically for `_dead_sessions()`'s classification logic itself.
- No `.ai-run/guides/` doc covers "Dead Sessions"/"Wasted Cost" classification logic in depth — `_dead_sessions()` itself is undocumented beyond its docstring.

### Architectural Decisions

- No formal ADRs found. The candidate diff itself carries an explanatory (non-marker) comment directly above the changed SQL in `rollups.py` (~lines 1517-1520) recording its own rationale: *"is_written/is_edited require a CONFIRMED successful call, never just the call itself... The span branch checks the matching execution span's success; the hook fallback checks for agent.tool.end (which, unlike agent.tool.denied/agent.tool.error, only fires on a completed call)."*
- Grepping `rollups.py` and `cli_analytics_handler.py` for `NOTE:`/`HACK:`/`TODO:`/`ADR:`/`DECISION:` markers returned zero hits.
- `CHANGELOG.md` exists but has no entries matching cli_analytics/dead-sessions/files_changed/EPMCDME-15583.

### Derived Conventions

- SQL-shape/control-flow test conventions for rollups live in `tests/codemie/repository/cli_analytics/postgres/test_rollups.py`, asserting on literal SQL text (not executed row content) via `str.startswith`/substring checks against a hand-rolled fake asyncpg connection.
- Shared test builders live under `tests/codemie/repository/cli_analytics/support/` (`hook_event_builders.py` as `hb`, `otlp_builders.py` as `b`), imported via `from tests.codemie.repository.cli_analytics.support import hook_event_builders as hb`.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/repository/cli_analytics/postgres/test_rollups.py` (1037 lines) — pure SQL-string/control-flow assertions against a fake `asyncpg` connection (`QueueConnection`); **no live Postgres, no row-content execution**. Contains `test_span_facts_statements_read_hooks_for_pairs_without_tool_or_interaction_spans`, which asserts the `session_files_daily` INSERT text contains expected fragments (`"h.attrs->'file_path'"`, `"h.tool_name = ANY("`) but never runs the SQL or inspects a resulting row's `is_written`/`is_edited` value. Three places in the test suite explicitly defer row-content proof to "Task 17" (`test_rollups.py:674,713`; `test_session_derived.py:17`) — **no such live-proving test exists anywhere in this checkout.**
- `tests/codemie/repository/cli_analytics/postgres/test_otlp.py` — owns local `_tool_span(**attrs)` / `_hook(**overrides)` builders for span/hook decode-level tests (e.g. `test_span_success_boolean_is_rendered_like_as_string`, confirming boolean `success` attrs are stringified to `"true"`/`"false"`).
- `tests/codemie/repository/cli_analytics/postgres/test_dirty.py` — owns a local `_span(name, ts, session)` helper; tests which hook/span types mark `RollupFamily.SPAN_FACTS` dirty.
- `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` — handler-mock pattern (`make_handler`, `make_repo_handler`, `BASE_COST_ROW`/`BASE_COST_FACT`). **Zero references anywhere to `_dead_sessions`, `dead_sessions`, `wasted_cost`, `files_written`, `files_edited`, `files_changed`, or `code_changes`** (confirmed by repo-wide grep).

### Testing Framework and Patterns

- pytest with `pytest.mark.asyncio`, run via `poetry run pytest tests/` (single Makefile `test:` target).
- **No testcontainers, no docker-compose-spun Postgres, no sqlite substitute anywhere** in `tests/codemie/repository/cli_analytics/`. All "DB" interaction is a hand-rolled fake connection class (`QueueConnection` in `test_rollups.py`, `RecordingConnection` in `test_ingestor.py`) that records SQL text/args rather than executing against a real server.
- `hook_event_builders.py` (`tests/codemie/repository/cli_analytics/support/`) builds six "sdlc-analytics plugin" events (`usage_request`, `subagent_usage`, `session_summary`, `session_env`, `skill_dispatch`, `git_snapshot`) plus generic helpers `minimal(type_, session_id, timestamp)`, `without(event, *keys)`, `as_otlp_log(...)`. **It does not build `agent.tool.start`/`agent.tool.end`/`agent.tool.denied`/`agent.tool.error`** — those are built ad hoc, locally, in `test_otlp.py` (`_hook(**overrides)`) and `test_dirty.py` (raw dicts / `hb.minimal(...)` used generically).
- Span builder: `tests/codemie/repository/cli_analytics/support/otlp_builders.py` (`span(name, trace_id=, span_id=, start=, end=, attrs=, parent_span_id=, status_code=)`, `traces_request(resource, spans, scope=)`). `success` is just another key inside `attrs` (string or stringified boolean), not a first-class kwarg. Span kind is derived server-side from span `name` via `vocabulary.SPAN_KIND` (`"claude_code.tool"` → `TOOL`=1, `"claude_code.tool.execution"` → `TOOL_EXECUTION`=2, `"claude_code.interaction"` → `INTERACTION`=3).

### Coverage Gaps

- No builder exists for realistic `agent.tool.start`/`agent.tool.end`/`agent.tool.denied`/`agent.tool.error` hook events with typed fields (`tool_name`, `tool_use_id`, `denial_reason`, etc.) — new tests must extend `hook_event_builders.py` or follow the ad-hoc local-dict pattern.
- No live-Postgres test harness exists at all; "Task 17" row-content proof is referenced three times but not implemented in this checkout. New tests proving actual `is_written`/`is_edited` row values **cannot run against a real DB** with current infra — they must either stay at the SQL-string-assertion level (like existing tests) or a new in-process/dockerized Postgres harness must be introduced, which is a larger infra decision beyond this ticket's natural scope.
- No existing test anywhere asserts `is_written=true` for a successful Write (or `false` for rejected/failed) — any new positive test is greenfield, with no template to copy.
- Zero coverage of `_dead_sessions()` at the handler layer.
- The existing `test_span_facts_statements_read_hooks_for_pairs_without_tool_or_interaction_spans` test is unaffected by the candidate diff either way (new LATERAL uses alias `x`, not `h`, so its `"JOIN hook_events h ON"` count assertion still holds) — meaning this diff currently ships with **zero regression protection** and zero positive-behavior proof.

---

## 5. Configuration and Environment

### Environment Variables

All configuration flows through `AnalyticsPgSettings` (`src/codemie/repository/cli_analytics/postgres/settings.py:336-379`), populated from `src/codemie/configs/config.py:200-223`; `rollups.py` itself contains no direct `os.environ`/`getenv` calls.

- `CLI_ANALYTICS_PG_URL` — analytics Postgres DSN (`config.py:200`)
- `CLI_ANALYTICS_PG_SCHEMA` — schema name, default `codemie_analytics` (`config.py:201`)
- `CLI_ANALYTICS_PG_POOL_SIZE` — connection pool size (`config.py:202`)
- `CLI_ANALYTICS_PG_STATEMENT_TIMEOUT_MS` — statement timeout (`config.py:203`)
- `CLI_ANALYTICS_RAW_RETENTION_DAYS` / `CLI_ANALYTICS_ROLLUP_RETENTION_DAYS` — retention windows (`config.py:211-212`)
- `CLI_ANALYTICS_ROLLUP_REFRESH_SECONDS` — rollup refresh interval, default 30s, consumed by `runtime.py:122` (`ScheduledJob("cli_analytics_rollup_refresh", ...)`)
- `CLI_ANALYTICS_ROLLUP_BATCH_SIZE`, `CLI_ANALYTICS_PARTITION_PREMAKE_WEEKS`, `CLI_ANALYTICS_MAINTENANCE_INTERVAL_MINUTES`, `CLI_ANALYTICS_SESSION_RETENTION_DAYS` — batch/partition/maintenance/purge tuning (`config.py:216-223`)

### Configuration Files

- `src/codemie/configs/config.py` — defines the `CLI_ANALYTICS_*` Pydantic settings fields.
- `src/codemie/repository/cli_analytics/postgres/settings.py` — resolves those into `AnalyticsPgSettings`, consumed by `runtime.py` to schedule `refresh_rollups` (which executes the `rollups.py` RECOMPUTE SQL this ticket touches).

### Feature Flags and Deployment Concerns

- `features.cliAnalytics` flag gates: (1) whether the background rollup scheduler starts at all — `customer_config.is_feature_enabled("cliAnalytics")` in `src/codemie/service/analytics/cli_analytics_jobs.py:89`; (2) whether the REST endpoints respond (404 when disabled) — `src/codemie/rest_api/routers/cli_analytics.py:138`. A nested sub-setting `get_feature_setting("cliAnalytics", "deliveryFramework", ...)` in `src/codemie/service/analytics/delivery_framework.py:45` is unrelated to this bug.
- The unrelated, out-of-scope local-dev changes on this branch (`.env.example`, `config/customer/customer-config.yaml` enabling `features:cliAnalytics`, `docker-compose.yml` port remap) are consistent with deliberate local setup to exercise this flag while reproducing the bug — confirmed present via `git status`/`git diff --stat`, not analyzed further per ticket scope.

---

## 6. Risk Indicators

- **Scope-closing discrepancy (highest priority):** `_dead_sessions()` classifies sessions using `files_changed` (an unconditional count of distinct `file_path` rows touched in `session_files_daily`), **not** `files_written`/`files_edited` (which the candidate fix correctly gates on success). Because `session_files_daily` rows are created for *any* tool call with a non-empty `file_path` — Read, rejected Write, failed Edit, anything — regardless of `is_written`/`is_edited`, **the candidate's rollups.py fix alone does not change `_dead_sessions()`'s classification output.** The ticket's stated effect ("wrongly excludes the session from Dead Sessions / Wasted Cost classification... via `_dead_sessions()`... based on `files_changed == 0`") will **not** be achieved by this diff alone. This needs explicit resolution with the ticket owner: either (a) the ticket's acceptance criteria should be narrowed to "fix `files_written`/`files_edited` only" (which is what the diff actually does, correctly), or (b) `_dead_sessions()`/`get_file_facts_by_session` must additionally be changed to key off `files_written + files_edited` instead of `files_changed` — a second code change, in a different file/layer, not present in the current uncommitted diff.
- **Zero test coverage for the new behavior.** The existing `test_rollups.py` SQL-shape assertions neither break nor validate the new success-gating logic; there is no positive test proving a rejected/failed Write actually yields `is_written=false`, and no test harness capable of executing the rollup SQL against real data exists in this checkout ("Task 17" row-content proof is referenced three times, implemented nowhere).
- **Hook-event emission semantics unverified against source of truth.** The assumption that `agent.tool.end` fires only on completed (not denied/errored) tool calls is internally self-consistent within this repo (dedicated `agent.tool.denied` vocabulary entry; pre-existing `invocations_hourly` logic relies on the same assumption) but the actual CLI-plugin emission code lives outside this repository and was not inspected. If that external plugin ever emits `agent.tool.end` as a generic "lifecycle finished" event (including on denial), the hook-fallback branch of this fix would silently regress for sessions with no span data.
- **No test builder for tool-lifecycle hook events.** `hook_event_builders.py` lacks `agent.tool.start`/`agent.tool.end`/`agent.tool.denied`/`agent.tool.error` builders; new tests must extend it or copy ad-hoc local patterns from `test_otlp.py`/`test_dirty.py`, adding minor friction/scope to the test-writing task.
- **`agent.tool.denied` absent from `_SPAN_HOOK_TYPES`** in `dirty.py` — a denied-only event without its paired `agent.tool.start` would not mark `RollupFamily.SPAN_FACTS` dirty; realistic test fixtures must include both events together.
- EPMCDME-15448 ("read-only file access counted as Files Changed") lives in `reader.py:360` (`get_file_facts_by_session`, `fl` CTE at lines 377-384) — confirmed location only, explicitly out of scope, not analyzed further. Note it is *adjacent to* (but distinct from) the `files_changed` discrepancy above: EPMCDME-15448 concerns Read-only tools being counted; the discrepancy above concerns rejected/failed Write/Edit tools being counted — both stem from the same `files_changed = count(*)` unconditional-count design in `reader.py`, which this ticket's diff does not touch.

---

## 7. Summary for Complexity Assessment

The verified code change surface is narrow and well-isolated: the candidate fix touches exactly one SQL statement block (`session_files_daily`'s two UNION ALL branches) in one file (`rollups.py`), reusing — faithfully and correctly, confirmed line-by-line — a success-join idiom already proven in production by the adjacent `invocations_hourly` statement in the same file. No new symbols, no new DDL/migrations, no new config, and no fan-out/double-counting risk were found; the join keys, time window (`interval '1 hour'`/`'1 day'`), NULL handling, and all referenced constants (`_EXEC`, `_TOOL`, `_TOOL_END`, `_HOOK_FILE_PATH`, `_NO_SPAN_OF_KIND`, `_IS_TOOL_START`, `_hook_key`) check out against their real definitions. In isolation, the SQL change itself is low-risk and low-novelty — it is a mechanical transplant of an existing pattern, not a new design.

However, two factors should raise the complexity/risk score materially. First, a scope-closing discrepancy was found: `_dead_sessions()` (the actual consumer named in the ticket's acceptance criteria) keys off `files_changed`, which is computed independently of `is_written`/`is_edited` and will remain nonzero for rejected/failed Write/Edit calls even after this fix — meaning the diff as currently scoped does not achieve the ticket's stated outcome without either a scope clarification or an additional change in `reader.py`/`cli_analytics_handler.py` (a different architectural layer: Service/Repository rather than pure Postgres-rollup SQL). This should be resolved before implementation proceeds, as it changes the file-change surface from "one file" to potentially "two files, two layers." Second, test coverage posture for this area is weak to nonexistent: there is no live-Postgres test harness in the repository at all (row-content proof is explicitly deferred to a never-implemented "Task 17"), no hook-event builder for tool-lifecycle events, and zero existing tests reference `_dead_sessions()` or `files_changed`/`files_written`/`files_edited`. Any serious test plan for "successful write / rejected write / failed write" scenarios is therefore greenfield work requiring either new SQL-string-assertion tests (consistent with existing conventions, lower effort) or a new DB-execution test infrastructure (higher effort, out of this ticket's natural scope). Combined with the unverifiable (from this repo alone) assumption about external hook-emission semantics, this task carries more hidden complexity than its small diff size suggests.
