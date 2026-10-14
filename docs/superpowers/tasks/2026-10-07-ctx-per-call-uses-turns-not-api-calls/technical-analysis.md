# Technical Research

**Task**: cli-analytics efficiency ctx-per-call api_call_count
**Generated**: 2026-10-07T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-15519 [CLI Analytics] "CTX/CALL" is computed per turn, not per API call

Preconditions
Environment: Local backend (http://localhost:8080)
API: 0.16.0 (/v1/info), backend repo codemie, branch EPMCDME-15253_cli-analytics-postgres-storage @ 1520ef5ac, analytics storage PostgreSQL
Component: CLI Analytics → Efficiency (Context Bloat KPIs, "Most bloated sessions" table) and the sessions list ctx_per_call sort

Description
Every "per call" context metric in CLI Analytics divides cache-read tokens by turns (user prompts). It should divide by api_call_count (model API calls). One turn usually triggers many API calls, especially with tool use, so the values are overstated.

Affected metrics:
- "CTX/CALL" column in the Efficiency "Most bloated sessions" table, and its sort order
- "Worst session CTX / call" KPI
- "Avg context / call" KPI
- ctx_per_call sorting in the sessions list

Steps to reproduce:
1. Run one prompt that triggers many tool calls, so one turn makes several API calls.
2. Open CLI Analytics → Efficiency and find the session in "Most bloated sessions".
3. Compare its CTX/CALL with the DB:
select sum(cache_read_tokens), sum(api_call_count) from codemie_analytics.cost_daily where session_id = '<id>';
select turns from codemie_analytics.turns_daily where session_id = '<id>';

Expected: CTX/CALL = cache_read_tokens / api_call_count.
Actual: CTX/CALL = cache_read_tokens / turns.

Relevant file hint: codemie/src/codemie/service/analytics/handlers/cli_analytics_handler.py

---

## 2. Codebase Findings

### Existing Implementations

All four turns-based ratios named by the ticket live in
`codemie/src/codemie/service/analytics/handlers/cli_analytics_handler.py` (class `LocalAnalyticsHandler`):

- `get_overview` — `"avg_context_per_call": (cache_read_tokens / total_turns) if total_turns else 0.0` (line 327)
- `get_sessions` — `"_ctx_per_call": (cache_read / turns) if turns else 0.0` (line 603), used only as the sort key for `sort_by="ctx_per_call"` (line 623) and popped from the row before it is returned (line 634) — the API response never exposes a per-call ratio field today
- `get_efficiency` — the "worst session" ratio: `ratio = (_i(row.get("cache_read_tokens")) / turns) if turns else 0.0` (line 895), and the KPI `"avg_context_per_call": (cache_read_tokens / total_turns) if total_turns else 0.0` (line 917)

All four read `turns` from `turns_by_session` (built from `get_turns_by_session`, backed by `turns_daily.turns`), not from `api_call_count`.

`get_session_cost_facts` and `get_cost_kpis` in
`codemie/src/codemie/repository/cli_analytics/postgres/reader.py` are the per-session and
aggregate queries over `cost_daily`. Neither currently selects `api_call_count`:
- `get_cost_kpis` (line 200) sums `cost_usd`, `input_tokens`, `output_tokens`, `cache_read_tokens`,
  `cache_creation_tokens` — no `api_call_count` sum.
- `get_session_cost_facts` (line 480) sums the same four token/cost columns per session — no
  `api_call_count` sum either.

Both already read from `_cost_rows(...)` (line 77), a subquery aliased `c` that selects `c.*` from
`cost_daily` plus a window column `grain_calls`; `cost_daily.api_call_count` is therefore already
present on every row these two queries scan, unused. Adding `sum(c.api_call_count)::bigint AS
api_call_count` (and `AS total_api_calls` for the KPI query) requires no new join.

The frontend has the identical bug independently: `codemie-ui/src/pages/analytics/components/cli-analytics/views/EfficiencyView.tsx`
renders the "Most bloated sessions" table's CTX/CALL column itself:
```
ctx_per_call: (row: SessionRow) => (
  <div ...>{row.turns ? formatTokens((row.cache_read_tokens ?? 0) / row.turns) : '—'}</div>
),
```
(lines 193-196). This table is fed by `useCliAnalyticsSessions(filters, repositories, { sort_by: 'ctx_per_call', per_page: 10 })`
(line 224-227), i.e. the same `/sessions` endpoint and `sort_by=ctx_per_call` the ticket names.
`SessionRow` (`codemie-ui/src/types/cliAnalytics.ts` lines 46-65) carries `turns` and
`cache_read_tokens` but no `api_call_count` field — the backend response has none to give it.

### Architecture and Layers Affected

- **Repository layer** (`codemie/src/codemie/repository/cli_analytics/postgres/reader.py`) — add
  `api_call_count` sums to `get_cost_kpis` and `get_session_cost_facts`. `ports.py`'s
  `CliAnalyticsReader` Protocol returns untyped `Rows` (`list[dict]`), so no signature change is
  needed there, only the SQL and the row contract documented in its docstring.
- **Service/handler layer** (`cli_analytics_handler.py`) — swap the four turns-based divisions for
  api_call_count-based ones in `get_overview`, `get_sessions`, `get_efficiency`.
- **API response models** (`codemie/src/codemie/rest_api/models/cli_analytics.py`) — `LocalAnalyticsSessionRow`
  (line 169) has `turns: int = 0` and `tool_call_count: int = 0` but nothing named `api_call_count`;
  exposing the new denominator to the frontend's own column renderer means adding a field here too.
- **Frontend** (`codemie-ui/src/pages/analytics/components/cli-analytics/views/EfficiencyView.tsx`,
  `codemie-ui/src/types/cliAnalytics.ts`) — the CTX/CALL cell formatter and the `SessionRow` type.
  `SessionsView.tsx` shows `turns` and `cache_read_tokens` as separate columns (no CTX/CALL column
  there), so it is not affected.

### Integration Points

- `cost_daily.api_call_count` is populated at rollup time in
  `codemie/src/codemie/repository/cli_analytics/postgres/rollups.py` (`_COST_DAILY`, line 639): one
  row's `api_call_count` is `count(*)` of the underlying model-API request rows for that
  (day, session, model, ...) grain — "service and subagent requests included" per the storage guide.
  This is exactly the "model API calls" semantics the ticket wants as the denominator.
- `.ai-run/guides/integration/cli-analytics-storage.md` (line 182) already documents `api_calls` as
  `sum(cost_daily.api_call_count)` for the `session_dims` fallback column (a different, unused-by-the-API
  table), confirming the column's intended meaning project-wide.
- The reader already both uses and relies on `api_call_count`'s correctness elsewhere:
  `get_users`, `get_session_cost_facts`, `get_session_detail_cost` all order the "top model" by
  `sum(c.api_call_count) OVER w AS grain_calls` (see `_cost_rows`, `_TOP_MODEL`) — a pre-existing,
  tested pattern for aggregating this column safely across the `cost_daily` grain split (speed,
  geo, scope, agent type).

### Patterns and Conventions

- Per-session facts from different fact tables are fetched in parallel via `asyncio.gather` and
  merged into dicts keyed by `session_id` in Python (`turns_by_session`, `success_by_session`, etc.) —
  the file's module docstring explains this is deliberate (see lines 22-26) rather than one wide SQL
  statement, with a documented threshold for revisiting it.
- Coercion helpers `_i`, `_f`, `_s` normalize `Decimal | None | int` reader output before arithmetic;
  any new `api_call_count` field read in the handler must go through `_i(...)` like the existing
  `turns`, `cache_read_tokens`, etc.
- `_float_sort_key` rounds to 9 decimals before sorting so float-sum reordering never flips a tie —
  the `ctx_per_call` sort key in `get_sessions` already follows this; a reworked ratio must keep doing so.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/cli-analytics-storage.md` — documents `cost_daily.api_call_count` and
  the `session_dims.api_calls` fallback (`sum(cost_daily.api_call_count)`), confirming the column's
  grain and meaning (line 182).
- `.ai-run/guides/integration/cli-analytics-events.md` — not read in depth; covers the OTel event
  contract that ultimately produces `cost_daily` rows, not the dashboard math.
- `docs/superpowers/specs/2026-09-23-cli-analytics-postgres-storage-design.md` — design notes
  mentioning `api_call_count`'s role in top-model selection (D4, the `argMax`/unmerged-parts risk),
  relevant background for why `_cost_rows`/`grain_calls` exists, not for this ticket's divide-by-turns bug.

### Architectural Decisions

No ADR specifically addresses the ctx-per-call formula. The module docstring in
`cli_analytics_handler.py` documents the per-session-merge-in-Python decision (D1/F1-style reasoning
inline) but says nothing about which denominator to use for "per call" metrics — this appears to be
a plain implementation bug, not a documented, overridden decision.

### Derived Conventions

- "Per call" is used elsewhere in the same module unambiguously as API calls: `get_cost` returns
  `avg_cost_per_session`, and tool-related metrics are explicitly named `tool_calls`/`tool_call_count`
  (from `invocations_hourly` kind 1), distinct from `turns`. The handler's own naming convention
  (`turns` = user prompts, `tool_calls` = tool invocations, nothing today named `api_calls`) supports
  reading the ticket's "api_call_count" as a third, currently-missing concept rather than a rename of
  an existing field.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` and
  `..._ordering.py` build a `MagicMock` repo via a `make_handler`/manual-mock pattern, stubbing
  `get_session_cost_facts`, `get_turns_by_session`, `get_tool_success_by_session`,
  `get_lines_by_session`, `get_model_breakdown`, `get_skill_names_by_session` as `AsyncMock`s, with a
  shared `BASE_COST_ROW` fixture (no `api_call_count` key today).
- Neither file has any test asserting `get_efficiency`'s `avg_context_per_call`,
  `worst_session_ctx_per_call`, or `get_sessions`' `ctx_per_call` sort order against known
  `cache_read_tokens`/`turns` values — a grep for `ctx_per_call`/`avg_context_per_call`/`get_efficiency`
  across `tests/` returns no hits outside the handler file itself. This metric is currently untested.
- `tests/codemie/repository/cli_analytics/postgres/test_reader.py` tests `get_session_cost_facts` and
  `get_cost_kpis` only for SQL shape (no `day BETWEEN`, correct `sel` join, no epoch defaults) and the
  `grain_calls`/top-model window pattern — not for which columns are selected, so adding
  `api_call_count` to the SELECT list won't collide with an existing assertion but also isn't guarded
  by one.

### Testing Framework and Patterns

pytest with `pytest.mark.asyncio`; reader tests use a `_SqlRecorder`/`_sql_of` helper that captures
the rendered SQL text and params without touching a real database (string-based assertions on the
query, e.g. `"JOIN sel" in sql`).

### Coverage Gaps

- No test currently pins the ctx-per-call formula to any denominator, so neither the bug nor its fix
  is directly tested — any fix needs new tests asserting the ratio against `api_call_count`, not just a
  code change.
- No test exists for `get_cost_kpis`/`get_session_cost_facts` selecting `api_call_count`, so a new
  reader test would need to assert the new column alias appears in the rendered SQL (string-based,
  per the existing pattern) and/or that the handler coerces it correctly from a mock row.
- Frontend has no test coverage located for the `ctx_per_call` cell formatter specifically; a search of
  `EfficiencyView.test.tsx` for the string was not performed in depth here (out of scope for this
  backend-first ticket) — flagged under Risk Indicators.

---

## 5. Configuration and Environment

### Environment Variables

None specific to this computation were found; CLI Analytics storage env vars (e.g. storage backend
selection) are documented in `.ai-run/guides/integration/cli-analytics-storage.md` but are unrelated
to the ctx-per-call formula.

### Configuration Files

No config file governs which column backs "ctx per call" — the divisor is hardcoded in the handler
(`turns_by_session.get(sid, 0)` / literal division) and in the frontend formatter.

### Feature Flags and Deployment Concerns

None found. The handler change is a pure in-process computation swap; the reader change is an
additive `SELECT` column, not a schema/migration change (`cost_daily.api_call_count` already exists
per `alembic_cli_analytics/versions/a1c1a0000001_cli_analytics_initial_schema.py` line 190).

---

## 6. Risk Indicators

- The fix spans two repos (`codemie` backend and `codemie-ui` frontend): the ticket's file hint and
  `feature_area` point only at the backend handler, but the "CTX/CALL" column in the Efficiency
  "Most bloated sessions" table is rendered by `EfficiencyView.tsx` from `row.turns`/`row.cache_read_tokens`
  directly — fixing only the backend sort order (`get_sessions`'s `_ctx_per_call`) would make the sort
  correct while the displayed numbers in that same table stay wrong, unless `api_call_count` is also
  added to `LocalAnalyticsSessionRow` and consumed by the frontend formatter. Speculative: scope
  should be confirmed with the ticket owner/spec before deciding whether the frontend change is in
  this ticket.
- Zero existing tests pin the current (buggy) or any future ctx-per-call formula, in either the
  handler or the reader — the fix carries no regression safety net today and needs new tests to prove
  correctness, not just a code diff.
- `turns == 0` and `api_call_count == 0` both need the same "no divide by zero → 0.0 (or None for the
  worst-session KPI)" guard the current code already has for `turns`; the ticket does not say whether
  a session that is API-call-free but turn-positive (or vice versa) should behave differently, but
  such sessions should be rare since every turn implies at least one API call.
- `BASE_COST_ROW` test fixtures (both handler test files) have no `api_call_count` key; every test
  that currently exercises `get_session_cost_facts`-derived rows will need that key added or will
  silently coerce to 0 via `_i(None)`, which could mask a wiring mistake (e.g. forgetting to add the
  column to the SQL) behind a passing test that only asserts shape, not value.

---

## 7. Summary for Complexity Assessment

This is a narrow, well-isolated bug fix in `codemie`'s CLI Analytics read path: `cost_daily.api_call_count`
already exists in the schema and is already selected (unused) via the `_cost_rows` subquery that both
`get_cost_kpis` and `get_session_cost_facts` already read from, so exposing it is one additive SELECT
column in each of two queries in `reader.py` — no migration, no new join. The handler-layer fix is four
divisions in `cli_analytics_handler.py` (`get_overview`, `get_sessions`, `get_efficiency` x2) that
currently divide by `turns_by_session[...]` and need to divide by the new `api_call_count` value
instead, following the file's existing `_i()`-coercion and zero-guard idioms.

The main complexity driver is scope, not mechanism: the frontend (`codemie-ui`) independently
reimplements the same turns-based ratio in `EfficiencyView.tsx`'s CTX/CALL cell renderer for the same
"Most bloated sessions" table the ticket names, reading `row.turns` directly rather than any backend-supplied
ratio. Fixing only the backend sort order leaves the displayed values in that table wrong unless the
new field is also threaded through `LocalAnalyticsSessionRow` → `SessionRow` (TypeScript) → the cell
formatter — a cross-repo change the ticket's file hint does not mention explicitly.

Test coverage for this exact metric is currently absent on both the handler and reader side, which
raises the practical risk (no regression net, so the fix's correctness rests entirely on new tests)
without raising the structural risk (no novel patterns, no new integrations, no schema change).

---

## 8. External References

None named by the task. The ticket does name a specific file as a starting point
(`codemie/src/codemie/service/analytics/handlers/cli_analytics_handler.py`), which was read in full
as part of Section 2 above, not treated as an external source.
