# Technical Research

**Task**: cli_analytics harness service name session detail clickhouse postgres
**Generated**: 2026-10-02
**Research path**: filesystem

---

## 1. Original Context

Add a resolved 'harness_name' (display label of the coding client: Claude Code, Cursor, Codex, etc.) to the CLI Analytics Session Details response, for both the ClickHouse and PostgreSQL storage backends (selected via CLI_ANALYTICS_STORAGE_BACKEND). Source of truth: the ServiceName column already present on ClickHouse's raw coding_agent_logs/coding_agent_traces tables, and the equivalent service_name column on PostgreSQL's otel_resources table (joined via resource_id from log_events/spans). Event-hook-originated records carry ServiceName='codemie-agent-hooks' on ClickHouse, and PostgreSQL's hook_events table has no resource/service column at all — hooks cannot identify the harness. So for a given session_id, the harness must be resolved by finding, among that session's raw records, a service name that is NOT 'codemie-agent-hooks' (preferring the earliest such value — the same 'resolve one dimension per session, earliest-value wins' pattern already used for repository/branch/developer_name in both readers' session-dimension resolution). Decisions already made: (1) humanize the raw service_name into a display label generically (title-case, replace dashes/underscores with spaces), with a small explicit override map in vocabulary.py for cases needing a specific label (at minimum 'claude-code' -> 'Claude Code'); (2) when no non-hook service name is resolvable for a session at all, return 'Unknown'. This repo's own guide (.ai-run/guides/integration/cli-analytics-storage.md) documents the rule: a new reader query must be added to the port AND to both readers in the same change, with a test for each engine — never fix one engine only.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/repository/cli_analytics/ports.py` — `CliAnalyticsReader` Protocol (`@runtime_checkable`), one `async def` per query, no docstring return-shape contract beyond the module docstring: "Rows are plain dicts keyed by the column aliases each method documents in the ClickHouse adapter." `get_session_detail_meta(session_id: str) -> Rows` is declared at line 164; adding `harness_name` is a dict-key addition, not a signature change — `assert_implements_port` (below) checks method name/async-ness/parameter shape only, not dict keys.
- `src/codemie/repository/cli_analytics/clickhouse/reader.py:584-608` — `get_session_detail_meta`: selects from `coding_agent_cost_daily` (session existence + min day), `LEFT JOIN v_session_dimensions d`, `LEFT JOIN v_session_email e`. Returns `session_id, developer_name, repository, branch, project_name, prompt, started_at, duration_ms`. **No ServiceName/harness column today.** `v_session_dimensions` (built from `coding_agent_session_dims`, fed by `mv_session_dims` from `coding_agent_hook_events`) never carries ServiceName — hook events are extracted from `coding_agent_logs` but the hook materialized-view pipeline does not propagate it (confirmed by reading `config/clickhouse/schema.sql` end to end: `coding_agent_hook_events`, `coding_agent_session_dims`, `mv_session_dims`, `v_session_dimensions` column lists). A harness-name query must read the **raw** `coding_agent_logs`/`coding_agent_traces` tables directly for the session, mirroring how `get_session_detail_tools`/`get_session_detail_events` already scan raw tables by `session_id` outside `sel`.
- `src/codemie/repository/cli_analytics/postgres/reader.py:508-524` — `get_session_detail_meta`: `WITH c AS (SELECT DISTINCT session_id FROM cost_daily WHERE session_id = $sid)`, `LEFT JOIN session_dims d`. Returns the same keys as ClickHouse plus CH-parity epoch fallback for `started_at`. `session_dims` table (`src/external/alembic_cli_analytics/versions/a1c1a0000001_cli_analytics_initial_schema.py:253-266`) has no service/resource column. A harness query must join `log_events`/`spans` (both carry `resource_id bigint NOT NULL`, `ts timestamptz NOT NULL`, `session_id text NOT NULL`) to `otel_resources(resource_id, service_name, attrs, first_seen, last_seen)` by `resource_id`. `hook_events` (same file, ~line 110) has `ts, ingest_seq, session_id, event_type, prompt_id, user_email`, the hook text columns, `attrs` — **no `resource_id` column**, confirming hooks cannot carry a service name on this backend either.
- `src/codemie/repository/cli_analytics/vocabulary.py` — documented home for "harness vocabulary" (module docstring, and `.ai-run/guides/integration/cli-analytics-storage.md`'s "Where Code Belongs" table). Currently holds `SpanKind`/`EventKind` enums, `SPAN_KIND`/`EVENT_KIND` name maps, `LINES_METRICS`/`ACTIVE_TIME_METRICS`, hook field tuples. No existing service-name-to-label map; the natural, single place to add one plus a humanizing helper.
- `src/codemie/repository/cli_analytics/clickhouse/ingestor.py:110` — the ClickHouse hook-events ingest path hard-codes `"service.name": "codemie-agent-hooks"` as a `ResourceAttributes` value — this is the literal string the new query must exclude.
- `src/codemie/service/analytics/handlers/cli_analytics_handler.py:602-700` — `get_session_detail`: `asyncio.gather`s `get_session_detail_meta` plus six other per-session reader calls, builds a flat `detail` dict (line 671) assembled from `meta`, `cost`, `scalars`, etc.; `detail["delivery_framework"] = classify_delivery_framework(skill_names)` is set after the dict literal (line 699), i.e. the handler already has a pattern for deriving one more field onto `detail` beyond the literal and assigning it directly from a helper — `harness_name` can follow either pattern (dict-literal key sourced from `meta.get("harness_name")`, or a post-assignment call to a vocabulary helper).
- `src/codemie/rest_api/models/cli_analytics.py:167-190` (`LocalAnalyticsSessionRow`) and `:239-247` (`LocalAnalyticsSessionDetail(LocalAnalyticsSessionRow)`) — neither has a harness/client field today. `delivery_framework: str | None` on the base row is the closest existing analog (nullable, handler-derived, not reader-sourced raw column).

### Architecture and Layers Affected

- **Reader layer** (`ports.py` Protocol + both concrete readers): new/extended `get_session_detail_meta` query, same signature, new dict key.
- **Vocabulary layer** (`vocabulary.py`): new override map + humanizing helper (pure function, no I/O).
- **Service/handler layer** (`cli_analytics_handler.py::get_session_detail`): thread `harness_name` from `meta` into the assembled `detail` dict, applying the humanize/override function if the readers return the raw/humanized value rather than the final label (open design choice — see Section 6).
- **API model layer** (`rest_api/models/cli_analytics.py`): add `harness_name` field to `LocalAnalyticsSessionDetail` (not to `LocalAnalyticsSessionRow` — the task scopes this to Session *Details* only, not the session list).
- Not in scope per task: the frontend `codemie-ui` hardcode (separate repo).

### Integration Points

- ClickHouse: `coding_agent_logs`/`coding_agent_traces` raw tables (90-day TTL) are the only source; `v_session_dimensions`/`coding_agent_session_dims` do not carry ServiceName and cannot be extended without a schema/materialized-view change (none requested by the task — resolving straight from raw tables, as other session-detail queries already do, needs no schema change).
- PostgreSQL: `log_events`/`spans` join `otel_resources` on `resource_id`; `otel_resources` rows are purged once unused for the raw retention (per the storage guide's Operations table), so this join is only valid within the raw-retention window, same lifetime as the rest of session-detail's raw-table reads.
- No cross-module dependents found beyond the handler and the two readers; `rest_api/routers/cli_analytics.py` is a pass-through to the handler (per task's own confirmed layering) and needs no change beyond whatever the model gains.

### Patterns and Conventions

- **Earliest-value-wins dimension resolution**: ClickHouse encodes it as `AggregateFunction(argMinIf, String, DateTime64(9), UInt8)` columns in `coding_agent_session_dims`, consumed via `v_session_dimensions`; PostgreSQL encodes the equivalent at read time via `array_agg(... ORDER BY ...)[1]` (see `_TOP_MODEL` in `postgres/reader.py`) or job-side aggregation into `session_dims`. Since harness-name has no dimension-table home (hook events never carried ServiceName), the new query cannot reuse either machinery as-is; it needs its own earliest-non-hook-service lookup against raw tables/`log_events`+`spans`, structurally most similar to `get_session_detail_cost`/`get_session_detail_tools` (raw-table reads scoped only by `session_id`, no window, no `sel`).
- **Port-then-both-readers-then-test-per-engine** is an explicit, named rule in `.ai-run/guides/integration/cli-analytics-storage.md`'s "Where Code Belongs" table and is also enforced structurally: `assert_implements_port` (`tests/.../support/contracts.py`) fails if a reader's method signature diverges from the port, and `test_every_fact_a_windowed_query_reads_is_scoped_to_the_selected_sessions` parametrizes over every port method except `get_session_detail_*`/`get_skill_names_by_session` — i.e. session-detail methods are deliberately exempt from the windowing audit (they are per-session, unwindowed), but still enumerated via `port_methods(CliAnalyticsReader)`.
- Dict-shape testing, not real DB: both readers are tested by injecting a mock query function (ClickHouse: `AsyncMock` standing in for `query_fn`) or a `_SqlRecorder` (`PostgreSQL`, fakes `engine.acquire()/fetch()` and records the rendered SQL string) — never a live ClickHouse/PostgreSQL connection.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/cli-analytics-storage.md` — primary guide for this domain. Key rules used above: "A new reader query in one adapter only" → "Add it to the port and to both readers in the same change, with a test for each: both engines must return the same answers"; "Harness vocabulary (span, event and metric names to canonical kinds)" → `vocabulary.py`; Testing section states the suite needs no database (`make test`); session-detail lookups are explicitly called out as not windowed ("Session-detail and skill-name lookups keyed by session_id are not windowed").
- No guide file specifically documents the ClickHouse `ServiceName`/PostgreSQL `otel_resources.service_name` columns' semantics beyond what the schema/migration files show; this analysis derived them from `config/clickhouse/schema.sql` and the Alembic initial-schema file directly.

### Architectural Decisions

- Design decisions D1/D3/D5/D10/D11 referenced in the guide concern session universe, turns definition, deny_all empty-set behavior, and the session-id parameter-size budget — none of them govern harness-name resolution; this is a net-new dimension, not a documented decision being extended.
- `vocabulary.py`'s own docstring states the harness-vocabulary design intent plainly: "Supporting another harness (Cursor, Codex, Gemini CLI) is therefore a data change here" — i.e. the module is already designed for exactly this kind of addition (name → display/kind mapping), even though no service-name-to-label map exists in it yet.

### Derived Conventions

- Humanizing a raw name for display is not an existing pattern anywhere in `vocabulary.py` (which maps names to integer kinds, not to display strings), so there is no existing "humanize" helper to reuse; it would be new, small, pure code colocated with the override map per the task's own decision.
- `classify_delivery_framework` (`src/codemie/service/analytics/delivery_framework.py`, imported into the handler) is the closest existing precedent for a pure-Python classification helper fed by handler-level data and attached to the `detail` dict post-hoc — worth looking at for style/placement when the plan decides where humanization is called from.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/repository/cli_analytics/clickhouse/test_reader.py` — `test_session_detail_meta_resolves_a_plugin_less_session` is the only direct test of `get_session_detail_meta` today; it is a pure pass-through check (mock query returns a row, reader returns it unchanged) — the SQL body itself is not asserted. `test_reader_implements_the_reader_port` uses `assert_implements_port` against `CliAnalyticsReader`.
- `tests/codemie/repository/cli_analytics/postgres/test_reader.py` — no test specifically targets `get_session_detail_meta`; the file's relevant structural tests are `test_every_fact_a_windowed_query_reads_is_scoped_to_the_selected_sessions` (excludes `get_session_detail_*` by name) and the `_SqlRecorder`/`_sql_of` helpers used elsewhere for asserting rendered SQL and bound parameters.
- `tests/codemie/repository/cli_analytics/test_vocabulary.py` — plain dict/tuple equality assertions against the vocabulary module's constants; no behavioral/function tests since the module currently exports only mappings and tuples, not functions.
- `tests/codemie/repository/cli_analytics/support/contracts.py` — `port_methods()` and `assert_implements_port()`, used by both reader test files to keep adapters in signature parity with the port.

### Testing Framework and Patterns

- `pytest` with `pytest.mark.asyncio` for async reader/handler methods. ClickHouse reader tests mock the injected `query_fn` as `AsyncMock`; PostgreSQL reader tests fake the asyncpg-shaped `engine.acquire()`/`fetch()` via `_SqlRecorder`, and assert against the rendered SQL text (`" ".join(sql.split())` normalization) or captured bound parameters, never a live DB — matches the guide's "no database" claim exactly.

### Coverage Gaps

- No test asserts what `get_session_detail_meta`'s *query body* actually selects for either engine (both existing tests are trivial pass-throughs on mocked return values) — a new harness-name test will need to either extend the trivial pass-through style (assert the mocked row's `harness_name` key flows through) or, better, assert the SQL text excludes `'codemie-agent-hooks'`/`service_name`/`ServiceName` and orders by earliest timestamp, following the `_sql`/`_SqlRecorder` patterns used elsewhere in each file.
- No existing test exercises the "all raw records are hook-originated → Unknown" fallback path for either engine; this is new logic end to end (reader resolution + handler/model default) with zero existing coverage to extend.
- `vocabulary.py` has no function-level tests today (only constant equality checks); a humanize-with-override helper will be the module's first testable function, so its test will set the pattern rather than follow one.

---

## 5. Configuration and Environment

### Environment Variables

- `CLI_ANALYTICS_STORAGE_BACKEND` — selects `clickhouse` (default) or `postgres`; governs which reader this task's new query is exercised through at runtime (`src/codemie/repository/cli_analytics/factory.py`).
- No new environment variable is implied by anything found; `CLICKHOUSE_*` and `CLI_ANALYTICS_PG_*` settings already documented in the storage guide are unaffected (no new table, column, or migration is required by this task per the "no schema change" finding above).

### Configuration Files

- `config/clickhouse/schema.sql` — raw table and materialized-view definitions; confirmed current `ServiceName LowCardinality(String)` column on `coding_agent_logs` (line 52) and `coding_agent_traces` (line 132), unchanged by the recent fast-forward merge mentioned in the task's branch notes (no revert touched these column lists; the SpanName/cursor-span-routing revert the task mentions is confined to filter expressions, not column definitions, and did not appear in the sections read).
- `src/external/alembic_cli_analytics/versions/a1c1a0000001_cli_analytics_initial_schema.py` — single initial-schema migration (no later migrations exist yet); defines `otel_resources(resource_id, service_name, attrs, first_seen, last_seen)`, `log_events`/`spans` (`resource_id`, `ts`, `session_id` all present and NOT NULL on both), and `hook_events` (no `resource_id`/service column).

### Feature Flags and Deployment Concerns

None found specific to this task; no feature flag gates CLI Analytics session-detail fields, and no Dockerfile/CI reference to `harness_name`/`service_name` exists.

---

## 6. Risk Indicators

- Speculative: ClickHouse's `get_session_detail_meta` currently reads only `coding_agent_cost_daily` + `v_session_dimensions`/`v_session_email`; resolving `harness_name` will likely require an additional raw-table subquery (`coding_agent_logs` and/or `coding_agent_traces`, both have 90-day TTL — matches the rest of session detail's raw-table dependency, not a new retention risk, but worth flagging since it is one more raw scan per detail request).
- Speculative: on PostgreSQL, resolving "earliest non-hook service name" needs a join from `log_events`/`spans` through `resource_id` to `otel_resources`, ordered by `ts`/`LIMIT 1`, likely as an additional CTE in `get_session_detail_meta`'s existing `WITH c AS (...)` structure — a straightforward addition, but it is a genuinely new join shape not mirrored by any existing query in this file (closest analog is `get_session_detail_events`'s `log_events`/`spans` UNION, which doesn't join `otel_resources`).
- `coding_agent_logs`'s `ORDER BY` key is `(user_email, TimestampDate, event_name, event_type, session_id, Timestamp)` — `ServiceName` is not part of it, so filtering that table by `ServiceName != 'codemie-agent-hooks'` combined with `session_id = {session_id}` relies on the `idx_session_id` bloom filter index, not primary-key pruning; `coding_agent_traces`'s key does start with `ServiceName`, which is more favorable there.
- No test today pins down the exact row shape (`Rows = list[dict]`) semantics for `None` vs `''`/`'Unknown'` on a missing harness name — the port docstring states a general convention ("a missing string is '' or None exactly where ClickHouse returns one") but the task explicitly wants `'Unknown'` as a non-empty sentinel, which is a new convention for this one field, not an application of the existing one; worth making explicit in the spec rather than left to each reader's own judgment.
- `otel_resources` rows are purged once unused for the raw retention period (per the storage guide), so very old sessions near the retention boundary could lose their resource row before `log_events`/`spans` purge — low risk given both share the raw-retention lifetime, but asymmetric purge ordering is unverified from the code read here.
- Humanization-with-override function in `vocabulary.py` is net-new (no existing "name → display label" helper to model it on); the plan should decide once whether humanization happens in the reader (SQL `CASE`/engine-side), the vocabulary helper (Python, shared), or the handler — doing it in two of the three risks the exact "fix one engine only" trap the storage guide warns about.

---

## 7. Summary for Complexity Assessment

This task touches four layers in a small, well-trodden seam: the `CliAnalyticsReader` Protocol (`ports.py`, no signature change, just a new dict key on an existing method), both concrete readers' `get_session_detail_meta` (new raw-table/join logic, one query body each), `vocabulary.py` (a new override map plus a small humanizing helper — the module's first function, not just constants), and the handler/model pair that threads the new field into the API response (`cli_analytics_handler.py::get_session_detail`, `LocalAnalyticsSessionDetail`). No schema migration, new table, or materialized view is required on either engine — `ServiceName` already exists on ClickHouse's raw tables and `otel_resources.service_name` already exists and is already joinable from `log_events`/`spans` via `resource_id`, both confirmed by reading the current schema/migration files fresh (the task's branch notes about a recent fast-forward and reverted ClickHouse filter routing did not touch these column definitions).

The technical novelty is concentrated in the SQL: resolving "earliest non-hook-originated service name for this session" has no existing dimension-table shortcut on either engine (hook events never carried ServiceName, so the usual `v_session_dimensions`/`session_dims` argMin-style machinery doesn't apply), so each reader needs its own small raw-table query, structurally closest to the already-unwindowed `get_session_detail_cost`/`get_session_detail_tools` queries rather than the windowed `_sessions_cte` pattern. Test coverage for the touched method is currently trivial on both engines (pass-through only; no SQL-body assertions), and the vocabulary module has zero function-level tests to extend — so this change adds both the first meaningful SQL assertions for `get_session_detail_meta` and the first function tests for `vocabulary.py`, which is more test-authoring surface than code-authoring surface.

Key risk factors are: (1) the explicit two-readers-plus-port-plus-test-per-engine discipline this repo enforces structurally (`assert_implements_port`) and by guide convention, raising the cost of touching one engine without the other; (2) a genuinely new "Unknown" sentinel convention for a missing value, diverging from the port's documented `''`/`None` convention, that the spec should pin down explicitly rather than leave to per-reader judgment; (3) a design choice not yet made — where humanization/override lookup happens (SQL, vocabulary helper, or handler) — that determines whether the two engines can share logic or risk silently diverging.

---

## 8. External References

- `.ai-run/guides/integration/cli-analytics-storage.md` (resolved, read in full) — the task's named guide. Facts used: the "Where Code Belongs" table placing harness vocabulary in `vocabulary.py` and mandating port+both-readers+test-per-engine changes together; the Testing section's "They need no database" claim (`make test` is the gate); the note that "Session-detail and skill-name lookups keyed by session_id are not windowed."
- `.ai-run/guides/testing/testing-patterns.md` (resolved, read) — general pytest location/scope conventions ("mirror src/ under an existing root", "run the narrowest relevant scope"); no CLI-analytics-specific test command beyond what the storage guide already states.
- `.ai-run/guides/quality-gates.md` (resolved, read) — exact commands: `make ruff` (format/lint), `make test` (pytest gate), `make coverage` (pytest + HTML coverage, skip unless requested).
- No other external (outside-repo) path or URL was named by the task; the branch/commit notes in the task's own preamble point at in-repo files (`clickhouse/reader.py`, `config/clickhouse/schema.sql`), which were re-read fresh per the task's instruction rather than trusted from memory.
