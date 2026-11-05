# Technical Research

**Task**: cli-analytics lines net_lines lines_daily
**Generated**: 2026-11-04
**Research path**: codegraph

---

## 1. Original Context

EPMCDME-16713, Bug, Critical:

[CLI Analytics] Lines+, Lines- and Net lines show 0 for Claude Code sessions with successful Write/Edit calls and changed files: missing line data is reported as a real zero.

Summary: For Claude Code sessions whose hook events arrived (tool results and changed files are counted), CLI Analytics shows Lines+ 0, Lines- 0, Net lines 0 even though the session has successful Write/Edit calls and changed files. Missing line data is reported as a real zero, so the API and UI cannot tell "no lines changed" from "lines unknown". Every aggregate that includes such sessions is understated (Overview Net lines, Net lines chart, Top Repositories, repository modal, Sessions table, Session Detail).

Proven root of the conflation (backend 2.63.0-RC.5): `lines_daily` is built only from the OTel lines metric, or from `agent.tool.end` hook events whose `attrs.lines_added` is a JSON number (src/codemie/repository/cli_analytics/postgres/rollups.py: RECOMPUTE lines_daily, _LINES_SESSION, _carries_lines()). With no lines_daily row, the reader (reader.py get_lines_by_session) returns nothing and the handler substitutes 0 (src/codemie/service/analytics/handlers/cli_analytics_handler.py: _build_repository_rows, get_sessions net_lines, get_session_detail, also get_users net_lines). Unknown and zero are indistinguishable.

Expected: When line counts are not available (no OTel lines metric and no agent.tool.end hook with a numeric lines_added) the API returns null instead of 0 for lines_added/lines_removed/net_lines. Real zeros stay 0. Sessions with OTel or hook line data report the same values as before.

Acceptance criteria (in scope for THIS run, backend only):
- A session whose line counts are not available returns null (not 0) for lines in /sessions, /sessions/{id} and /repositories.
- Regression: sessions with OTel or hook line data report the same values as before.
Out of scope (decided): UI change in ../codemie-ui ("-"/"Not reported" rendering); root cause of why sessions 8b7e8985/711c0464 lacked line data (needs prod DB); MIXED sessions (some agent.tool.end with lines_added, some without) keep today's sum-of-known behavior — design decision on a coverage indicator, to be raised on the ticket/MR, not implemented. Aggregates over several sessions (repository rows, users, overview, daily series) must stay correct: define in the research how an aggregate behaves when ALL its sessions are unknown vs some unknown (note as a risk/open decision, do not decide silently).

Local repro that already exists (reuse for before/after): C:\Users\Yauheni_Hil\repos\codemie-dev\epmcdme-16713-repro\seed_16713.py and check_16713.py; sessions 16713000-0000-4000-8000-000000000001 (hooks without lines_added: net_lines 0 today) and ...0002 (control with lines: 79). Local stack: WSL docker, backend container codemie-dev-codemie-1 bind-mounts src/.

feature_area: cli-analytics lines net_lines lines_daily

---

## 2. Codebase Findings

### Existing Implementations

Purpose A: report a session's line counts (lines_added, lines_removed, net_lines).
- Rollup writer `lines_daily`: `src/codemie/repository/cli_analytics/postgres/rollups.py:1764-1782` (inside `RECOMPUTE`, :1757). Two UNION ALL arms:
  - OTel arm: `metric_points` rows with `metric_name` in `LINES_METRICS`, only when `NOT _LINES_SESSION`.
  - Hook arm: `agent.tool.end` rows with `_carries_lines` and `_FIRST_TOOL_END`, only when `_LINES_SESSION`.
  - Both arms end in `HAVING sum(added) <> 0 OR sum(removed) <> 0`. The comment at :1763 says "Rows whose sums are all zero are not stored". So a session with a real zero (a numeric `lines_added: 0`, or an OTel metric summing to 0) also gets NO row. Today "no row" covers both "unknown" and "real zero".
  - `_LINES_SESSION` (:1737): session has any `agent.tool.end` with numeric `lines_added`. `_carries_lines` (:1730): `jsonb_typeof(attrs->'lines_added') = 'number'`. `_hook_lines` (:1747): non-number or over `_MAX_COUNTER` counts 0 (`coalesce(..., 0)`). A session with a numeric `lines_added` and a null/absent `lines_removed` counts removed as 0.
  - Recompute is triggered per (day, session) via `dirty.py:35` (`METRIC_FACTS = 8` covers lines_daily and active_time_daily); `ingestor.py:215` selects `DISTINCT day, session_id FROM lines_daily`.
- Reader (`src/codemie/repository/cli_analytics/postgres/reader.py`; port `ports.py:271-277`). All read `lines_daily` and are INNER by nature (no row, no output):
  - `get_lines_totals` :636, one row, `sum(...)::bigint`. A window with no rows returns one row of NULLs.
  - `get_lines_daily` :646, per day net.
  - `get_lines_by_user` :658, per developer net (joins `sel`).
  - `get_lines_by_session` :671, per session sums `GROUP BY session_id`. Sessions without a row are absent.
  - `get_session_detail_scalars` :1087-1093, scalar subselects `sum(lines_added)` / `sum(lines_removed)` from `lines_daily`; returns NULL when no row.
  - Also `reader.py:478`: `get_class_lists(by="day")` reads `lines_daily` for day class lists; `reader.py:138` `_LINES_EMAIL` uses `lines_daily` to resolve a session's developer.
- Handler (`src/codemie/service/analytics/handlers/cli_analytics_handler.py`). The helper `_i()` turns NULL/missing into 0 everywhere:
  - `_build_repository_rows` :336-378: `_new_repository_bucket` :233 seeds `lines_added/removed = 0`; :364-365 `+= _i(lines_by_session.get(sid, {}).get(...))`. `_finalize_repository_bucket` :263 sets `net_lines = added - removed`. `_group_branches_into_repositories` :294-333 sums `_REPO_SUM_FIELDS` (:282, includes lines) from the branch rows and recomputes `net_lines` (:328); branch rows are exposed through `_BRANCH_ROW_FIELDS` (:267).
  - `get_sessions` :888, reads `get_lines_by_session` :915 into `lines_by_session` :925 and passes it to `_session_row` (:793, net_lines at :830: `_i(added) - _i(removed)`). The session row carries only `net_lines`, not `lines_added/removed` (model `LocalAnalyticsSessionRow.net_lines: int = 0`, models:240).
  - `get_session_detail` :1054-1077: `lines_added = _i(scalars...)`, `net_lines = lines_added - lines_removed`. Detail model `LocalAnalyticsSessionDetail(LocalAnalyticsSessionRow)` at models:309 inherits the field.
  - `get_users` :625-683: `net_lines_by_user` from `get_lines_by_user` :642/:654, `.get(name, 0)` at :683. Model `LocalAnalyticsUserRow.net_lines: int = 0` (models:197).
  - `get_overview` :449-535: `get_lines_totals` :466, `get_lines_daily` :467. KPI `net_lines = _i(added) - _i(removed)` at :504-505. Daily buckets `_i(r.get("net_lines"))` at :535. Models: `LocalAnalyticsOverviewKPIs.net_lines: int` (:88), `LocalAnalyticsOverviewDailyBucket.net_lines: int` (:102).
  - `get_efficiency` :1234-1336: `get_lines_totals` :1250, `get_lines_by_session` :1251. `code_changes.net_lines` at :1336 (model `LocalAnalyticsCodeChangesKPIs.net_lines: int = 0`, models:406). `code_ids` (:1299-1304) adds sessions with non-zero lines to the "code change" class lists.
- API models (`src/codemie/rest_api/models/cli_analytics.py`): `lines_added: int`, `lines_removed: int`, `net_lines: int` (non-optional) on `LocalAnalyticsRepositoryBranchRow` (:124-132) and `LocalAnalyticsRepositoryRow` (:140-150). Router `src/codemie/rest_api/routers/cli_analytics.py` `get_sessions` :689, `get_users` :526, `get_session_detail` ~:720; responses validated through `_respond(..., Model)`.

Purpose B: tell "lines unknown" from "no lines changed". Nothing serves it. Searched `lines_daily`, `net_lines`, `lines_added` in src: no coverage flag, no nullable lines field, no per-session "has lines" signal. The only "unknown" signals in this module are other fields: `cost_available` (`get_sessions` :957) and `''` for unknown key strings in rollups.

Other touched code: `src/codemie/repository/cli_analytics/postgres/maintenance.py:46` (lists `lines_daily` as a rollup table; no logic change implied).

### Decisions Already in the Code
- One source of lines per session: hooks when any `agent.tool.end` has numeric `lines_added`, else OTel (rollups.py:1735-1737). Reason in comment: hook is exact per call, OTel counts a new file one line too many. This is the "lines available" definition the ticket uses.
- Zero-sum rows are not stored in `lines_daily` and `active_time_daily` (rollups.py:1763; HAVING clauses :1774, :1781). Reason: none found beyond the comment. Read against the grain: stored as "row present iff non-zero"; the task needs "row present iff known".
- Event contract: `lines_added`/`lines_removed` present on Edit/MultiEdit/Write, absent for other tools "absent, never 0" (`.ai-run/guides/integration/cli-analytics-events.md:439`). A session with `lines_added` null or string falls back to OTel (:442).
- Convention for missing numbers: handler `_i()` coerces to 0 for every numeric field; the reader returns NULL for empty `sum()`.
- Response models declare lines as non-null `int` (`net_lines: int`) or `int = 0`; the API has no nullable-int convention for these fields. Other nullable fields exist (`branch: str | None`, `repository: str | None`, `avg_cost_per_dead` may be None at handler :1325).
- Repository grouping: counts are summed in Python from per-session facts, `_pct` called once per group (EPMCDME-15585), handler :294-297.

### Task Statements Checked Against the Code
- "lines_daily is built only from OTel lines metric or agent.tool.end with numeric lines_added": holds (rollups.py:1764-1782).
- "With no lines_daily row, reader get_lines_by_session returns nothing and handler substitutes 0": holds (reader.py:671-680; handler :364-365, :830, :1054-1055/:1077, :683, :505, :1336).
- "Unknown and zero are indistinguishable": holds, and wider than stated: a real zero also writes no row (HAVING at :1774/:1781), so the reader cannot tell it from unknown either. "Real zeros stay 0" depends on how a zero session is told apart from an unknown one; today nothing stored does that.
- "Sessions with OTel or hook line data report the same values as before": holds as a current-behaviour claim; keeps `_LINES_SESSION` source selection in place (consumers: all of Purpose A).
- "MIXED sessions keep today's sum-of-known behavior": holds; hook arm filters `_carries_lines`, so events without lines are skipped (rollups.py:1778), and `_hook_lines` counts a non-numeric `lines_removed` as 0.
- Local repro numbers (session ...0002 net_lines 79): consistent with seed_16713.py calls (40+25+10+6+3 added = 84; removed 2+3 = 5; net 79). Session ...0001 has no `lines_added` on its `agent.tool.end` (seed `with_lines=False`), so no OTel metric either: no `lines_daily` row.
- "Aggregates must stay correct": no existing statement of how an aggregate treats unknown; see risks.

### Consumers of Changed Contracts
Contract: the lines fields of `get_lines_by_session`, `get_lines_totals`, `get_lines_daily`, `get_lines_by_user`, `get_session_detail_scalars`, and the API fields `lines_added`/`lines_removed`/`net_lines`.
- `get_lines_by_session`: handler get_sessions :915 (also `get_repositories` path via `_build_repository_rows`, the call at ~:731), `get_efficiency` :1251 (consumes only for `code_ids` truthiness, :1303).
- `get_lines_totals`: `get_overview` :466 (-> KPI `net_lines`), `get_efficiency` :1250 (-> `code_changes.net_lines`).
- `get_lines_daily`: `get_overview` :467 (-> `daily_buckets[].net_lines`, and `data_as_of` from the same rows at :547).
- `get_lines_by_user`: `get_users` :642 (-> user row `net_lines`).
- `get_session_detail_scalars`: `get_session_detail` :1054.
- Models `LocalAnalyticsOverviewKPIs`, `LocalAnalyticsOverviewDailyBucket`, `LocalAnalyticsRepositoryBranchRow`, `LocalAnalyticsRepositoryRow`, `LocalAnalyticsUserRow`, `LocalAnalyticsSessionRow` (and `LocalAnalyticsSessionDetail` by inheritance), `LocalAnalyticsCodeChangesKPIs`: lines typed `int`; `_respond` validates against them.
- Tests that assert these: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` (16 hits), `test_cli_handler.py` (55; mostly the ES `CLIHandler`, a different module), `test_cli_analytics_class_lists.py` (3), `test_cli_analytics_handler_ordering.py`, `test_cli_analytics_spend_scope.py`; repository: `tests/codemie/repository/cli_analytics/postgres/test_live_lines_daily.py` (23), `test_live_catalog_money.py`, `test_rollups.py`, `test_reader.py`, `test_live_owner_scope.py`, `test_dirty.py`; builders `tests/codemie/repository/cli_analytics/support/hook_event_builders.py`.
- UI consumer in `../codemie-ui` (out of scope, not inspected): it reads these fields as numbers today; a null in the response changes its input type.
- Not named by the task: `/users` (`net_lines` default `= 0`), `/efficiency` code_changes, and `get_class_lists(by="day")` reading `lines_daily` (reader.py:478).

### Architecture and Layers Affected
- Layers: REST models and router (`rest_api/models/cli_analytics.py`, `routers/cli_analytics.py`) -> service handler (`service/analytics/handlers/cli_analytics_handler.py`) -> repository port (`repository/cli_analytics/ports.py`) -> Postgres adapter (`postgres/reader.py`, `postgres/rollups.py`) with rollup tables created by Alembic (`src/external/alembic_cli_analytics/versions/a1c1a0000001_cli_analytics_initial_schema.py:194` `CREATE TABLE lines_daily`).
- Repository layer never imports the service layer (documented at cli_analytics_jobs.py:100).

### Integration Points
- Ingest: `ingestor.py` -> `hook_events` / `metric_points` raw tables -> dirty queue (`dirty.py`) -> `RollupRefresher.recompute` (rollups.py:2227) -> `lines_daily` -> reader -> handler -> router.
- Rollups refresh asynchronously by interval jobs (`cli_analytics_jobs.py`); the repro script polls up to 90 s for that.

### Patterns and Conventions
- Raw SQL strings composed in Python; reader queries all start `WITH {_sessions_cte(f)}` and scope via `self._scope(f)`.
- Reader returns `Rows` (list of dicts) normalised by `normalise()`; handler indexes by `session_id`.
- Port is a `Protocol` (`CliAnalyticsReader`); any new reader method needs the Protocol entry.
- Handler helpers `_s`, `_i`, `_pct`, `_first`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/cli-analytics-storage.md:209`: `lines_added`/`lines_removed` summary columns are sums of `lines_daily`; one source per session; hook path counts Edit, MultiEdit, Write only (NotebookEdit and Bash not counted).
- `.ai-run/guides/integration/cli-analytics-events.md:439-442`: `lines_added`/`lines_removed` contract (absent, never 0, when unknown); server builds `lines_daily` only from numeric `lines_added`.
- Neither guide says what the API returns when lines are unknown.

### Architectural Decisions
- No ADR found. Inline comments at rollups.py:1735-1737, :1763.

### Derived Conventions
- "Absent, never 0" for unknown lines at the event level (guide) is not carried through to the API; the API substitutes 0.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/repository/cli_analytics/postgres/test_live_lines_daily.py`: source selection (tool-end lines win over metric; metric-only; cursor metric; tool_use_id dedupe), live PostgreSQL.
- `test_rollups.py`, `test_reader.py`, `test_live_catalog_money.py` (`get_lines_by_user`, totals, daily), `test_live_owner_scope.py`, `test_dirty.py`.
- Handler tests with mocked repository under `tests/codemie/service/analytics/handlers/` (see Consumers).

### Testing Framework and Patterns
- pytest; `test_live_*` need a real PostgreSQL; handler tests mock the reader and feed row dicts. Event builders in `tests/codemie/repository/cli_analytics/support/hook_event_builders.py`.

### Coverage Gaps
- No test found asserting a session without any `lines_daily` row, nor a zero-sum session. Searched `net_lines`/`lines_daily` in the test dirs above; the 23 hits in `test_live_lines_daily.py` are source-selection cases.
- Router-level response validation for lines fields not located.

---

## 5. Configuration and Environment

### Environment Variables
- None specific to lines found. Local repro reads `SUPERADMIN_EMAIL`/`SUPERADMIN_PASSWORD` from `codemie/.env`.

### Configuration Files
- Feature flag `features:cliAnalytics` (`customer_config.is_feature_enabled("cliAnalytics")`, cli_analytics_jobs.py:89); project must have `cli_analytics_enabled=true` (repro).
- `config/customer/customer-config.yaml` has an uncommitted local modification (git status), not examined.

### Feature Flags and Deployment Concerns
- Rollups are materialised: a rollup SQL change takes effect only for keys recomputed after deploy. Existing `lines_daily` content is not rewritten unless days are marked dirty (`dirty.py`, backfill in rollups.py `backfill`). Whether a backfill path for `lines_daily` exists was not traced.
- Alembic migration directory `src/external/alembic_cli_analytics/versions/` governs `lines_daily` schema (one initial migration seen).

---

## 6. Risk Indicators

- Zero-row ambiguity (rollups.py:1764-1782): `lines_daily` omits zero-sum rows, so "no row" is also a real zero. The ticket's "real zeros stay 0" has no stored signal to rest on today. Speculative: a reader-side null-for-no-row change alone would turn real zeros into null.
- Aggregate behaviour is undefined in code and is an OPEN DECISION (not decided here). Cases, with current behaviour:
  - Repository row / branch row, ALL sessions unknown: today 0 (bucket seeded 0, handler :242, :364). Some unknown: sum of known.
  - `_group_branches_into_repositories` (:294-333) sums branch rows; a null branch value would break `repo[field] += row[field]` (:321).
  - `get_users` net_lines `.get(name, 0)` (:683); `get_overview` KPI (:505) and `daily_buckets` (:535) from totals NULLs; `get_efficiency` `code_changes.net_lines` (:1336) and `code_ids` (:1303).
  - Reader `get_lines_totals` returns NULL when no rows: the "all unknown" overview case is already visible at SQL level; "some unknown" is not distinguishable from "all known" without a per-session signal.
- Models: lines fields are non-null `int` on 7 response models; the OpenAPI schema and the `../codemie-ui` consumer change with any nullable field (out of scope but a compatibility risk).
- Scope of acceptance criteria covers /sessions, /sessions/{id}, /repositories; `/users`, `/overview`, `/efficiency` consume the same facts and are named in the ticket summary but not in the criteria.
- Session row exposes only `net_lines` (no `lines_added/removed`); the ticket text says lines_added/lines_removed/net_lines. Detail inherits the session-row model.
- MIXED session sum-of-known (rollups.py:1778, `_hook_lines` coalesce 0) is kept per the ticket: a partial sum is reported as a full value (understated, not null).
- Fallback quirk: a session with numeric `lines_added` and absent `lines_removed` reports removed 0.
- Rollup change vs history: existing rows are not rewritten by a reader change; a rollup change needs dirty-marking/backfill (not traced).
- Test gap for no-row and zero-sum sessions (Section 4).
- Live-test dependency: `test_live_*` need PostgreSQL; handler tests are mock-based and will not notice SQL changes.
- Index staleness: codegraph reported rollups.py, reader.py, handler and models as pending sync; the content above for those files came from source returned in this run and direct reads, but line numbers may shift if the working tree is edited.

---

## 7. Summary for Complexity Assessment

The change surface sits in one vertical slice: Postgres reader (5 lines queries in `reader.py`, plus the `ports.py` Protocol if a method is added), the service handler (`cli_analytics_handler.py`, about 8 sites that coerce missing to 0 through `_i()`), and seven response models that declare lines as non-null `int`. The rollup SQL (`rollups.py` lines_daily) is the data origin and is only touched if the zero-versus-unknown distinction has to be stored. No new dependencies, config or migrations were found as existing needs. The task is a semantics change to an existing, well-structured pipeline rather than a novel mechanism.

Test posture: live PostgreSQL tests cover lines_daily source selection; handler tests mock the reader. There is no test for a session without a `lines_daily` row or a zero-sum session, and no test of aggregate behaviour with unknown sessions. A local before/after repro exists (seed_16713.py, check_16713.py) with a bug session (...0001) and a control (...0002, net 79).

Key risks: (1) real zeros and unknown are both "no row" today because zero-sum rows are not stored, which bears directly on "real zeros stay 0"; (2) aggregate behaviour for all-unknown versus some-unknown (repository, branch, users, overview, daily, efficiency) is undefined in code and is an open decision; (3) null in a non-null API contract affects the OpenAPI schema and the UI consumer; (4) criteria name 3 endpoints while five others read the same facts.

---

## 8. External References

- `C:\Users\Yauheni_Hil\repos\codemie-dev\epmcdme-16713-repro\seed_16713.py`: resolved. Posts NDJSON to `/v1/analytics/cli-analytics/event-hooks` as `admin@codemie.ai`, project `epm-cdme-cp`, one session per request. `BUG_SID` ...0001 (repo `repro-16713-nolines`) sends 5 Write/Edit `agent.tool.start`/`agent.tool.end` pairs with no `lines_added`; `CTRL_SID` ...0002 (repo `repro-16713-control`) sends the same with `lines_added/removed` (40/0, 25/0, 10/0, 6/2, 3/3 = 84/5, net 79). Both send 2 `agent.usage.request` events (needed for `/repositories`). Comment in seed: contract is "int on Edit/MultiEdit/Write; absent (never 0) when unknown". Window 2026-11-03.
- `C:\Users\Yauheni_Hil\repos\codemie-dev\epmcdme-16713-repro\check_16713.py`: resolved. Logs in with `SUPERADMIN_EMAIL/PASSWORD` from `codemie/.env`, polls up to `--wait` 90 s, then prints `/sessions` rows (keys incl. `lines_added`, `lines_removed`, `net_lines`), `/sessions/{id}` (`net_lines`) and `/repositories` rows filtered on "16713", base `http://localhost:8080`, path prefix `/v1/analytics/cli-analytics`, window `start_date=2026-11-03T00:00:00Z&end_date=2026-11-04T00:00:00Z`.
- `../codemie-ui`: named as out of scope; not read.
