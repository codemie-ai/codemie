# EPMCDME-16993 / EPMCDME-16992 Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A prompt-less reopen of an old Claude Code session (and a continued session with no work of its own) no longer changes its CLI Analytics duration or window membership, proved by a live PostgreSQL regression test, with no change to behavior for ordinary sessions.

**Architecture:** Test-first. One live PostgreSQL test file reproduces both tickets through the real hook -> rollup -> reader -> handler path and is run RED before any production change. Fixes are the smallest edits that turn the failing assertions green: (1) `last_event_at` and the raw fallback end stop counting lifecycle hooks and housekeeping OTel records, using one shared "real activity" list; (2) reader end probes use the same list; (3) admission arms 1 and 2 require activity on or after the window's first UTC day, **only if the RED run shows they admit the session**. EPMCDME-16992 gets tests only.

**Tech Stack:** Python 3.12, asyncpg, PostgreSQL (analytics schema `codemie_analytics`), pytest 8 + pytest-asyncio, docker (throwaway PostgreSQL 17 and 14 for live tests).

**Spec:** no `spec.md` (sdlc-light). Requirements: `work-item.md` (Jira text, Expected results, all comments) and the scope decisions in the *Scope* section below. Codebase context: `technical-analysis.md`; subagent notes `research-*.md` (partly unverified; line numbers there are HEAD `bfdd2dcc1e` and the CLI handoff's numbers are stale).

## Scope (user-approved, 2026-11-11)

In: the 16993 Expected results (duration ends at last real activity; session reported only for the period it has activity in) and its required regression test; 16992 backend tests.
Out, recorded as accepted residual risks (see end): period-clipping KPI tiles/charts, cross-session dedup, **any session-binding change (deferred by the user)**, repair/backfill of already-polluted sessions, a general EPMCDME-16847 housekeeping filter, detail-vs-aggregate tool-call mismatch.

## STATUS AND NEXT STEPS (updated 2026-11-11 after Tasks 0 and 1; read this block first)

**Done:** Task 0 (`baseline.txt`) and Task 1 (`test_live_session_reopen.py`, RED run in `red-results.md`: 16 failed / 14 passed,
identical on PostgreSQL 14 and 17). No production code has been changed yet.

**Decisions taken from the RED results (they override the "conditional" wording in Tasks 3-5 and in the minimal set below):**
- **Task 5 (admission) is REQUIRED.** Scenario 2 reproduces the ticket: D1 reached the backend without a dimension hook, the reopen's
  `agent.session.start` becomes `started_at`, and arms 1 and 2 of `_admitted_arms` list the session on D2 in all six endpoints.
- **Task 3a (`last_event_at`) and Task 3b (raw fallback end, `_RAW_RANGE`) are REQUIRED.** Scenarios 1, 11, 12 (lifecycle hooks alone move
  the end, with or without a summary) and 8, 10, 12 (startup / `/exit` records move the end of a session without a final summary).
- **Task 4 (reader end probes, `get_users_last_active`) stays CONDITIONAL.** Nothing in the RED run exercises it. Add a failing test first
  (a reopen within one day of the session end, and the Users "last active" case); change the reader only if that test fails.
- Tasks 2, 6 and 7 are unchanged. Task 6 already passes; keep it as guard tests.

**Do now, in this order, one task at a time. After each task run the live file and the unit files it names, and record the result in
`red-results.md` (a short "after Task N" table: which of the 30 runs turned green). Stop and report if a run that was green turns red.**

1. **Task 2.** Shared `ACTIVITY_HOOK_TYPES` in `vocabulary.py`, `reader._ACTIVITY_HOOK_TYPES` becomes an alias. No behavior change.
2. **Task 3a.** In the DIMENSIONS upsert (`rollups.py` ~2069-2092) keep `started_at = min(...)` over `DIMENSION_HOOK_TYPES` and `LEAST`/`GREATEST`
   merges, and compute `last_event_at` as `max(ts)` over `ACTIVITY_HOOK_TYPES` only. Expected green: scenarios 1, 11 (and the `last_event_at` half of 12).
3. **Task 3b.** In `_RAW_RANGE` (end side only, ~1105-1109 and ~1285-1290) count only real activity: `ACTIVITY_HOOK_TYPES` hooks, all `usage_requests`,
   `log_events` with `event_kind <> OTHER` and **not** a built-in command (`/exit`, `/quit`, `/model`: `command_name` in `BUILTIN_COMMAND_NAMES`),
   `spans` with `span_kind <> OTHER`, lines metrics. Leave the start side, `fb.first_ts` and the `_ENDED_AT` provisional rule untouched.
   Expected green: scenarios 8, 10, 12. Verify the `command_name` column against a real `user_prompt` log row if one is available (red-results.md item 4 says it is
   only seeded); if the real column differs, say so in `red-results.md` instead of guessing.
4. **Task 5.** Bound arm 1 and arm 2 of `_admitted_arms` (and the `_SESSION_ADMITTED_IN_WINDOW` constant) by the period, exactly as written in Task 5.
   Required before and after: the `EXPLAIN (ANALYZE, BUFFERS)` check in Task 5 Step 4. If arm 1 loses `session_dims_active_started`, revert that part and
   record it as a residual risk instead of shipping a slower query. Guards that must stay green: Review Focus 4 and 5, scenario 5 (real work on the reopen is listed
   on both days), scenario 6 (presets), scenario 7 (multi-day tiles stay all-time). Expected green: scenario 2.
5. **Task 4 (conditional, see above)** only if its new failing test proves it.
6. **Task 7, reduced:** the two guide sentences (end of a session = last real activity; admission bounded), `make ruff`, then rerun the baseline set from
   Task 0 and the live file on PostgreSQL 17 and 14. The 19 failures in `baseline.txt` are pre-existing (Windows year 0001/9999 timestamps, `test_settings`);
   compare against that list and report any other difference. Update the unit tests that pin the changed SQL text, each with a one-line reason.
7. Stop and remove the docker containers `cli-live-pg14` and `cli-live-pg17`. **Do not commit or push** until the user asks.

**If time runs out:** the order above is also the priority order. 2 + 3a + 3b fix the durations; Task 5 fixes the listing on the reopen day (scenario 2).
Do not ship Task 3 or Task 5 with any previously green scenario turned red, and do not ship Task 5 without the `EXPLAIN` evidence.

**Do not do (decided, out of scope):** session binding / class and origin (R1), period-clipped tiles and charts (R2), repair or backfill of already polluted
sessions (R3), cross-session dedup, any change to `started_at` semantics or the displayed start. The CLI side is finished and needs nothing from the backend to be
safe (see `minimal-scope-evidence.md`).

---

## Minimal delivery set (read this first; added 2026-11-11 for a tight deadline)

The CLI fixes (branch `EPMCDME-16992_16993_otlp-continued-reopened-redo`, codemie-code) are safe to ship **without any
backend change**: they only send fewer events (an idle reopen sends nothing; a continuation sends only its own work),
and the backend already handles sessions with fewer or no activity events (`_has_any_activity` keeps a session with only
`session.start`/`env` hooks out of the lists, `reader.py:248`). Nothing the new CLI sends is a new event type, field or
shape. The backend work below is therefore **not a precondition for shipping the CLI**; it closes what the CLI cannot:
older CLIs (<= 0.16.6) still in the field, the ticket's required live regression test, and the Expected result for data
those older clients send. The smallest backend change that does that:

| Task | In the minimal set? | Why / limit |
|---|---|---|
| 0 Baseline | Yes (short: run only the cli_analytics unit files and the two live files named) | Needed to tell new failures from old ones. |
| 1 Live regression test | **Yes, required by the ticket.** Reduce to fixtures (i), (ii), (iii), (v) and the guard (vii). Drop (iv) `/exit` and (vi) presets if time is short. | The ticket names this test as a required deliverable. It is the proof that an old client's reopen no longer moves duration or window. |
| 2 Shared activity-hook list | Yes (tiny, no behavior change) | Prerequisite of 3 and 4. |
| 3 Rollup: `last_event_at` / raw end from real activity only | **Yes, this is the actual fix for duration.** | Root cause: `rollups.py:2073` takes `max(ts)` over `DIMENSION_HOOK_TYPES` (includes `agent.session.start/stop/end`, `agent.notification`). Riskiest edit (rollup SQL): durations of all sessions shrink by the lag to their end hook (R8). Keep `started_at`/`fb.first_ts` and the `_ENDED_AT` provisional rule untouched. |
| 4 Reader: end probes and Users "last active" | Yes, only the `_activity_range` end probes (`reader.py:549-575`). `get_users_last_active` only if the Users assertion in Task 1 fails. | Without it the reader re-adds housekeeping OTel rows to the end. |
| 5 Admission bounded by the period | **REQUIRED: RED scenario 2 shows it** (see STATUS block above). | Touches the five-arm, index-served admission shape (EPMCDME-16427): highest regression and performance risk, so it is gated by evidence plus the `EXPLAIN` check. |
| 6 16992 backend tests | Optional (tests only, no production change) | Characterization tests; a failure is a finding to report, not a task. |
| 7 Guides, full regression | Reduced: `make ruff`, the Task 0 test set on PostgreSQL 17 (14 optional), the two guide sentences about the end of a session. | Skip the long guide rewrite if time-boxed; record that in the MR. |

**Not in the minimal set (accepted residual risks R1-R8 at the end of this file):** class/origin rebinding (R1), period-clipped
tiles and charts (R2), repair of already polluted sessions (R3), the 16992 rows already stored by old clients (R4),
detail-vs-aggregate (R5), the same-day variant (R6).

**Rule if time runs out:** ship Tasks 0-4 together with Task 1's test. Do not ship Task 3 without Task 1's duration
tests green, and do not ship Task 5 without its `EXPLAIN` evidence. Shipping none of it leaves the backend exactly as
today: new-CLI data is correct, old-CLI data and the old polluted sessions stay wrong, and nothing breaks.

## Global Constraints

- Git: commit/push/MR **only when the user asks** (AGENTS.md). Commit format then: `EPMCDME-16993: <Description>`; no ticket IDs in code comments (user memory rule: comments state the reason in one line).
- Tests: only the tests this plan names; the ticket requires the live test. Run the narrowest scope; state the exact command used.
- `test_reader.py:648` (`test_cost_methods_have_no_day_window_and_are_bound_to_sel`) must keep passing unchanged: tiles stay all-time for admitted sessions.
- Keep the five-arm, index-served admission shape (EPMCDME-16427) and the documented retention divergence. No new index. No migration.
- Do not change `started_at` semantics or the displayed *start* (`_activity_range` start side, `fb.first_ts`).
- Keep the provisional-summary rule (`rollups.py` `_ENDED_AT`: a cancelled `SessionEnd` must not truncate duration) and the rule that an `api_request` may precede the first hook (EPMCDME-15506).
- Live-test command: `TEST_LOCAL_PG_URL=postgresql://postgres:password@127.0.0.1:<port>/postgres poetry run pytest -n0 <file>` (`-n0` required; they share one database and refresh the same rollup days).
- Quality gate before handoff: `make ruff` (`.ai-run/guides/quality-gates.md`).

## Review Focus

Failure modes the tickets imply that no single task's main test covers (each has its test in the owning task):
1. A non-Claude-Code session with activity but only lifecycle hooks (no summary) must still get a sane, non-negative duration ending at its last real record (Task 3).
2. An ordinary session that ends with Stop/SessionEnd seconds after its last tool: duration shrinks by seconds only, list and detail agree (Task 3).
3. A session whose first `api_request` precedes its first hook keeps its start (EPMCDME-15506) (Task 3).
4. A session starting inside a sub-day window with activity after the window end stays listed (sub-day precision, EPMCDME-16496) (Task 5).
5. A session started at 23:59 UTC whose activity is on the next UTC day is listed for the window of either day (Task 5).
6. A provisional-summary session with a cancelled `SessionEnd` keeps its full duration (Task 3).

## File Structure

- Create `tests/codemie/repository/cli_analytics/postgres/test_live_session_reopen.py`: live regression + 16992 tests (helpers copied minimally from the deleted `test_live_session_activity_window.py`).
- Modify `src/codemie/repository/cli_analytics/vocabulary.py`: export the one "real activity hook types" tuple (moved from `reader._ACTIVITY_HOOK_TYPES`).
- Modify `src/codemie/repository/cli_analytics/postgres/rollups.py`: `last_event_at` max (~2069-2092) and `_RAW_RANGE` last side (~1105-1109, 1285-1290).
- Modify `src/codemie/repository/cli_analytics/postgres/reader.py`: `_activity_range` probes (549-575), `get_users_last_active` (954-979), `_admitted_arms` arms 1-2 (270-301, conditional).
- Modify unit tests that pin the changed SQL text: `tests/codemie/repository/cli_analytics/postgres/test_reader.py`, `test_rollups.py`, `tests/codemie/repository/cli_analytics/test_vocabulary.py` (only the assertions the change makes false).
- Modify guides `.ai-run/guides/integration/cli-analytics-storage.md`, `cli-analytics-events.md`.
- Create `docs/superpowers/tasks/2026-11-11-epmcdme-16993-cli-analytics-reopen-redo/red-results.md` (what failed before any fix).

## Traceability

| Source | Requirement | Task |
|---|---|---|
| 16993 Expected 1 | duration ends at last real activity on D1 | 1, 3, 4 |
| 16993 Expected 1 | session reported only for D1 (not D2) | 1, 5 |
| 16993 Expected 1 | class / project attribution unchanged | out of scope: binding deferred (Residual R1) |
| 16993 Expected 2 / 16496 AC4 | old session with no new activity in window stays excluded | 1, 5 |
| 16993 comment 16077824 | required live test: sessions, overview, users, repositories, cost, activity; duration ends at last D1 activity | 1 |
| 16993 comment 16077824 | Last 7 Days / 24 Hours / Hour presets show no old session | 1 (fixture vi), 5 |
| 16993 comment 16079046 | startup OTel records (6 kinds) + `/exit` `user_prompt` move the end | 1 (iii, iv), 3, 4 |
| 16993 comment 16070695 | D2 tiles drop to $0 once admission is right; D1 view keeps $7.46 | 1 |
| 16993 comment 16070695 | `get_lines_daily` would draw D1 bars in a D2 window | Residual R2 (tiles/charts) |
| 16992 Expected 1 | continuation reports only its own work | 6 |
| 16992 Expected 2 / comment 16079089 AC2 | continuation Started is its own | 6 (CLI redo is the fix; Residual R4) |
| 16992 Expected 3 / AC3 | totals count each request once | 6 |
| 16992 description | detail 111 vs aggregate 0 | Residual R5 (by design) |

---

### Task 0: Baseline and live database

**Files:** none changed.

- [ ] **Step 1:** Start throwaway databases: `docker run -d --rm --name cli-live-pg17 -e POSTGRES_PASSWORD=password -p 127.0.0.1:55417:5432 postgres:17` and the same for 14 on port 55414. Wait for `pg_isready`.
- [ ] **Step 2:** Run the baseline and save the tail of each output to `docs/superpowers/tasks/2026-11-11-epmcdme-16993-cli-analytics-reopen-redo/baseline.txt`:
  `poetry run pytest -n0 tests/codemie/repository/cli_analytics tests/codemie/service/analytics tests/codemie/rest_api/routers/test_cli_analytics*.py -q`
  and, with `TEST_LOCAL_PG_URL` set, `tests/codemie/repository/cli_analytics/postgres/test_live_route_pricing.py` and `test_live_session_subagent_cost.py`.
  Expected: record exactly which tests pass, fail or are skipped before any change. Any pre-existing failure is listed as pre-existing, not fixed here.

Test-first: no (baseline only).

### Task 1: Live regression test, run RED

**Files:**
- Create: `tests/codemie/repository/cli_analytics/postgres/test_live_session_reopen.py`
- Create: `docs/superpowers/tasks/2026-11-11-epmcdme-16993-cli-analytics-reopen-redo/red-results.md`

**Interfaces:**
- Produces: `class _Reopen` (seed builder: `sid(name)`, `email`, `d1: datetime`, `d2: datetime`, `window(start, end) -> LocalAnalyticsFilter` filtered to `users=[email]`), `async def _seed(engine, sid, hooks=[(type, ts)], usage=[ts], logs=[(event_name, event_kind, ts)], spans=[(span_kind, ts)])` writing raw rows then running `RollupRefresher` under `live_rollup_lock`, and `async def _endpoints(handler, f) -> dict` returning `{"sessions": [session_ids], "overview_sessions": int, "users": int, "repositories": int, "cost_usd": float, "activity_total": int}` from `handler.get_sessions / get_overview / get_users / get_repositories / get_cost / get_activity`.
- Consumes: `LocalAnalyticsHandler`, `PostgresCliAnalyticsReader`, `RollupRefresher`, `live_rollup_lock`, `EXCLUDE_SCOPE`.

- [ ] **Step 1: Write the helpers** by copying `_engine`, `_cleanup`, `_naive` and the `_seed_via_hooks` pattern from `git show 8b08abb93a^:tests/codemie/repository/cli_analytics/postgres/test_live_session_activity_window.py`. Seed D1 activity through the real pipeline (hook rows and a `usage_requests` row, as `test_live_route_pricing.py` does) so `cost_daily`, `turns_daily` and the stored activity facts are produced by the rollup, not hand-written. Read the six handler method signatures first (`get_sessions(f, page, per_page, sort, order)`, `get_overview(f)`, `get_users(f, page, per_page)`, `get_repositories(f, page, ...)`, `get_cost(f)`, `get_activity(f)`).
- [ ] **Step 2: Write these tests** (D1 = six days ago 17:44-19:36 UTC with real activity; D2 = today 18:17 UTC; D2 window 18:00-18:30; D1 window = whole D1 day). Each parametrized over `["underived", "derived"]` stored facts as in the template:
  - `test_reopen_lifecycle_only_with_d1_dimension_hooks_is_not_reported_on_d2`: fixture (i): D1 `agent.session.start`, `agent.prompt.submit`, `agent.tool.start`, usage; D2 `agent.session.start`, `agent.session.env`, `agent.git.snapshot`, `agent.session.end`. Assert every `_endpoints(D2 window)` value is empty/0 and the session is listed with its cost for the D1 window.
  - `test_reopen_lifecycle_only_without_d1_dimension_hooks_is_not_reported_on_d2`: fixture (ii): D1 has usage/OTel `api_request` log and a TOOL span only (no dimension hook); same D2 hooks. Same assertions. (This is the only case where `started_at` can reach D2: `rollups.py:2087` is `LEAST`.)
  - `test_startup_otel_records_on_d2_do_not_list_or_extend_the_session`: fixture (iii): add `log_events` rows named `hook_registered`, `plugin_loaded`, `managed_settings_resolved`, `mcp_server_connection`, `hook_execution_start`, `hook_execution_complete` (kind OTHER) stamped 18:17:44-18:17:48 on D2. Assert not listed on D2 and, for the D1 window, `duration_ms == last D1 activity - first D1 start`.
  - `test_exit_user_prompt_record_on_d2_does_not_list_or_extend_the_session`: fixture (iv): one `user_prompt` log row (kind 2) for `/exit` on D2. Same assertions. Record in `red-results.md` how the row is distinguishable from a real prompt (inspect the `log_events` columns/`attrs`; `BUILTIN_COMMAND_NAMES` is `("model","exit","quit")`).
  - `test_reopen_that_does_real_work_is_listed_on_both_days_with_each_days_consumption`: fixture (v): D2 adds `agent.prompt.submit` + a `usage_requests` row. Assert listed for D1 and D2 windows, `duration_ms` spans first D1 start to last D2 activity.
  - `test_recent_presets_do_not_show_an_old_reopened_session`: fixture (vi): windows ending now of 7 days, 24 hours and 1 hour with the reopen inside; assert the Sessions list has no row for the session.
  - `test_a_genuinely_multi_day_session_keeps_all_time_tiles_in_each_window`: fixture (vii), guard: activity on D1 and D2; each window lists it and shows all-time cost (documents the accepted tile semantics).
- [ ] **Step 3: Run RED:** `TEST_LOCAL_PG_URL=postgresql://postgres:password@127.0.0.1:55417/postgres poetry run pytest -n0 tests/codemie/repository/cli_analytics/postgres/test_live_session_reopen.py -q`. Expected: the duration assertions fail (end moves to D2); admission assertions fail only for some fixtures.
- [ ] **Step 4: Write `red-results.md`:** a table of test x {sessions, overview, users, repositories, cost, activity, duration} -> PASS/FAIL, with the observed `session_dims.started_at/last_event_at/ended_at/activity_started_at` for each fixture. This table decides whether Task 5 runs.
- [ ] **Step 5:** Commit only when the user asks.

Test-first: yes - the seven tests above are the failing tests; fixtures (iii) and (iv) are expected to fail on duration today.

### Task 2: One shared list of real-activity hook types

**Files:**
- Modify: `src/codemie/repository/cli_analytics/vocabulary.py`
- Modify: `src/codemie/repository/cli_analytics/postgres/reader.py:200-209`
- Test: `tests/codemie/repository/cli_analytics/test_vocabulary.py`

**Interfaces:**
- Produces: `ACTIVITY_HOOK_TYPES: tuple[str, ...]` in `vocabulary.py` (same eight members as today's `reader._ACTIVITY_HOOK_TYPES`: `agent.prompt.submit`, `agent.tool.start/end/error/denied`, `agent.subagent.start`, `agent.subagent.usage`, `agent.skill.dispatch`); `reader._ACTIVITY_HOOK_TYPES` becomes an alias of it.

- [ ] **Step 1: Test:** in `test_vocabulary.py`, `test_activity_hook_types_exclude_every_lifecycle_hook` asserts `ACTIVITY_HOOK_TYPES` contains none of `agent.session.start/stop/end/compact/env/summary`, `agent.git.snapshot`, `agent.notification`, and equals the previous reader tuple.
- [ ] **Step 2:** Run it, expect FAIL (name missing). `poetry run pytest -n0 tests/codemie/repository/cli_analytics/test_vocabulary.py -q`
- [ ] **Step 3:** Add the tuple to `vocabulary.py`; import it in `reader.py`; keep `_ACTIVITY_HOOKS = _text_array(...)` behavior identical.
- [ ] **Step 4:** Run the vocabulary tests and `test_reader.py` -> PASS (no behavior change).

Test-first: yes - `test_activity_hook_types_exclude_every_lifecycle_hook` fails on the missing export.

### Task 3: Rollup - the stored end stops counting lifecycle and housekeeping records

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/rollups.py` (DIMENSIONS upsert ~2069-2092; `_RAW_RANGE` ~1105-1109 and its use at ~1285-1290)
- Test: `tests/codemie/repository/cli_analytics/postgres/test_rollups.py`, live: `test_live_session_reopen.py`

**Interfaces:**
- Consumes: `vocabulary.ACTIVITY_HOOK_TYPES`.
- Produces: `session_dims.last_event_at` = max `ts` of hooks in `ACTIVITY_HOOK_TYPES` (still merged with `GREATEST`); `fb.last_ts` = max `ts` over real activity only (hooks in `ACTIVITY_HOOK_TYPES`, all `usage_requests`, `log_events` with `event_kind <> OTHER` excluding the local-command prompt found in Task 1, `spans` with `span_kind <> OTHER`, lines `metric_points`). `started_at` and `fb.first_ts` are unchanged.

- [ ] **Step 1: Test (unit):** in `test_rollups.py`, `test_last_event_at_is_taken_from_activity_hooks_only` asserts the DIMENSIONS statement computes `max(h.ts) FILTER (WHERE h.event_type = ANY(<activity types>))` while `min(h.ts) FILTER (... <dimension types>)` is unchanged; `test_raw_range_end_counts_real_activity_only` asserts the end side of `_RAW_RANGE` carries the same filters as `reader._RAW_ACTIVITY_SOURCES` and the start side has none. Run: expect FAIL.
- [ ] **Step 2:** Implement: split the `last_event_at` aggregate to the activity filter; build the `_RAW_RANGE` end filters from one shared definition with the reader (move `_RAW_ACTIVITY_SOURCES` filters into a small helper both import, instead of a second copy). Keep the `_ENDED_AT` provisional/final logic untouched.
- [ ] **Step 3:** Check every consumer of `last_event_at` and `fb.last_ts` (`grep -rn "last_event_at\|last_ts" src/`): `session_files_daily` day filter (~1206-1207), `get_session_cost_facts` list field, `get_users_last_active`. Write the list into `red-results.md` with the intended behavior of each; fix any that relied on a lifecycle hook to be the end.
- [ ] **Step 4: Live tests** `test_non_claude_session_with_only_lifecycle_hooks_ends_at_its_last_usage_record` (Review Focus 1: no summary, hooks `session.start`/`session.end` only plus usage rows; assert `duration_ms >= 0` and end == last usage ts), `test_session_end_hook_seconds_after_the_last_tool_shortens_duration_by_those_seconds_only` (2), `test_an_api_request_before_the_first_hook_keeps_the_session_start` (3), `test_provisional_summary_with_cancelled_session_end_keeps_full_duration` (6). Run the live file: Task 1 duration tests turn GREEN.
- [ ] **Step 5:** Run unit baseline files again; update only assertions made false by the intended SQL change, each with a one-line reason in the diff. Commit only when the user asks.

Test-first: yes - the Task 1 duration assertions (fixtures i-iv) and the two unit tests above fail first.

### Task 4: Reader - end probes and Users "last active" use real activity

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/reader.py` (`_activity_range` 549-575; `get_users_last_active` 954-979)
- Test: `test_reader.py` (`test_activity_probes_are_bounded_by_a_day_around_the_hook_times` ~538, `test_users_last_active_has_no_overlap_where` ~547), live file.

**Interfaces:**
- Produces: `_activity_range(alias)` end-side probes of `log_events` / `spans` carry `event_kind <> OTHER` / `span_kind <> OTHER` (and the local-command exclusion); the start-side probes are unchanged. `get_users_last_active` fallback branch uses `ACTIVITY_HOOK_TYPES` instead of `_DIM_TYPES`.

- [ ] **Step 1: Test:** `test_end_probes_ignore_housekeeping_records_and_start_probes_do_not` asserts the "later" probe SQL has the kind filters and the "earlier" probe does not; `test_users_last_active_ignores_lifecycle_hooks` asserts the fallback `event_type = ANY(...)` is the activity list. Run: FAIL.
- [ ] **Step 2:** Implement (extend the `bound` strings in `probes()`; keep the +-1 day bounds so partition pruning is unchanged).
- [ ] **Step 3: Live test** `test_users_last_active_does_not_move_to_a_prompt_less_reopen` (fixture i, D2 window around D1 activity and the reopen): last active equals the last D1 activity. Run the live file.
- [ ] **Step 4:** If a stored (already polluted) row is used in fixture (i) with `last_event_at` set to D2 by hand, record in `red-results.md` that the reader does not repair it (Residual R3). Commit only when the user asks.

Test-first: yes - the two unit tests and the live users test fail first.

### Task 5: Admission bounded by the period (runs only if Task 1 shows it is needed)

**Gate: MET** (RED scenario 2 lists the session on D2 in all six endpoints). This task is required. Original gate text: run only if `red-results.md` shows fixture (ii) (or any other) listed or counted on D2 by arm 1 or arm 2. If every admission assertion already passes after Task 3-4, skip it and record "admission not reproducible: arm 1/2 need `started_at` on D2, which requires fixture (ii)" in `red-results.md`; do not change SQL.

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/reader.py:248-256` (`_has_any_activity`), `270-301` (`_admitted_arms`, arms 1 and 2), the `_SESSION_ADMITTED_IN_WINDOW` constant at 259
- Test: `test_reader.py:435-465` (`_ARM_STARTED`, `_ARM_UNDERIVED` fragments), live file.

**Interfaces:**
- Produces: arm 1 adds `AND {a}.last_activity_day >= {_START_DAY_UTC}`; arm 2 replaces the unbounded `_has_any_activity(a)` by an activity probe restricted to `day >= {_START_DAY_UTC}` (lower bound only, mirroring arm 1). `_SESSION_ADMITTED_IN_WINDOW` uses the same bounded form so the reference constant and the arms agree.

- [ ] **Step 1: Test (unit):** update `_ARM_STARTED` / `_ARM_UNDERIVED` fragments to the bounded form first; run -> FAIL.
- [ ] **Step 2: Live tests** `test_session_started_in_window_with_all_activity_before_the_window_is_not_listed` (fixture ii), `test_a_session_started_inside_a_sub_day_window_with_activity_after_the_window_end_stays_listed` (Review Focus 4), `test_a_session_started_at_2359_utc_with_next_day_activity_is_listed_for_both_days` (Review Focus 5). Run the first -> FAIL, the other two -> PASS (guards).
- [ ] **Step 3:** Implement the two arm edits. Same-day variant of fixture (ii) (activity and reopen on one UTC day) is **not** fixed by a day bound; record as Residual R6.
- [ ] **Step 4: Performance check:** run `EXPLAIN (ANALYZE, BUFFERS)` for the arm-1 and arm-2 SELECTs before/after on a seeded table (>= 50,000 `session_dims` rows, `derive_session_activity` run) using the bench folder from `docs/superpowers/tasks/2026-11-04-epmcdme-16427-cli-analytics-sessions-db-pagination/bench/` if it runs; arm 1 must still use `session_dims_active_started`. Save the plans in `red-results.md`. If arm 1 changes plan shape, revert and mark Residual.
- [ ] **Step 5:** Run unit + live files again. Commit only when the user asks.

Test-first: yes - fixture (ii) admission test fails first.

### Task 6: EPMCDME-16992 backend tests (no production change)

**Files:**
- Modify: `tests/codemie/repository/cli_analytics/postgres/test_live_session_reopen.py`

- [ ] **Step 1: Tests:**
  - `test_continuation_with_no_own_work_keeps_its_own_start_and_reports_nothing`: parent P with usage and a prompt from T0; continuation C with only `agent.session.start` at T0+13m, then OTHER startup records; no summary, no usage. Assert for C: `start_time == T0+13m` (list Date and detail `started_at`), cost 0, tokens 0, turns 0, tool calls 0; P unchanged.
  - `test_continuation_with_own_work_reports_only_its_own_requests`: C adds a prompt, a tool call and one usage request with its own ids; summary `started_at` equal to C's own first line. Assert C cost/tokens equal that one request; P's cost excludes it.
  - `test_each_request_is_counted_once_in_user_and_repository_totals`: P + C (as above) under one user and repo; assert the overview, users and repositories token and cost totals equal the sum of the distinct requests.
- [ ] **Step 2:** Run. Expected: PASS today. If any FAIL, stop and report to the user with the stored `session_dims` values; do not change production code without a new decision.
- [ ] **Step 3:** Commit only when the user asks.

Test-first: no - characterization tests guarding the CLI fix; they pass on current code by design (a failure is a finding, not a task).

### Task 7: Guides, full regression, handoff notes

**Files:**
- Modify: `.ai-run/guides/integration/cli-analytics-storage.md` (Window Semantics ~53-55, duration ~63 and ~331, Operations/backfill ~417-435), `cli-analytics-events.md` (~118 wording "13 activity hook events")
- Create: `docs/superpowers/tasks/2026-11-11-epmcdme-16993-cli-analytics-reopen-redo/residual-risks.md` (copy of the list below, for the Jira comment)

- [ ] **Step 1:** Update the guides: reported end = last real activity (list defined once in `vocabulary.ACTIVITY_HOOK_TYPES`); `started_at` still from the 13 dimension hooks; the "activity" wording conflict between the two guides removed; admission arms 1-2 bounded (if Task 5 ran); existing rows are not repaired (monotonic merges) and a backfill does not shrink them.
- [ ] **Step 2:** Run the full set from Task 0 Step 2 on PostgreSQL 17 and 14 (live) plus the unit set; compare with `baseline.txt`. Expected: no new failures; `test_reader.py:648` unchanged and passing.
- [ ] **Step 3:** `make ruff`. Expected: clean. If it fails with `poetry: No such file or directory`, follow `.ai-run/guides/development/setup-guide.md` (Claude Code Stop Hook section).
- [ ] **Step 4:** Stop and remove the docker containers. Report to the user the exact commands run and results. If an MR is requested later, its description must ask a reviewer to post `/sanity` (security-relevant: analytics ingest/binding code is not changed, but state this). Commit only when the user asks.

Test-first: no (documentation and verification).

## Residual risks and decisions (for the user and the Jira comment)

- **R1 - Classification / origin (16993 Expected 1, last clause):** binding unchanged by decision. Older CLIs (<= 0.16.6) that send evidence requests for an idle reopen can still bind an unbound legacy session as `unknown / legacy_project`; any request with a session id and at least one record binds (`session_binding.py:86-92`, `ingestor.py:505,567`). Needs its own ticket with the funding/consent flow reviewed.
- **R2 - Tiles and charts** stay all-time for any admitted session (guide: "needs its own task"); `get_lines_daily`, `get_users_daily_activity` can still draw D1 days in a D2 window for a genuinely multi-day session.
- **R3 - Existing polluted sessions** (`8ac53605`, `9d0bf1c0`, `d0fb4149`, `8ac27927`) keep their stored `last_event_at`/`ended_at`: merges are `GREATEST`/`LEAST`, a backfill does not shrink them. Repair needs a one-off UPDATE (decision for the reporter); verify on Preview with a **new** session.
- **R4 - 16992 Started:** the backend trusts the summary `attrs.started_at` and ratchets `started_at`/`activity_started_at` earlier. The Preview AC2 failure came from the reverted CLI PR 779; rows already stored by older CLIs keep a parent-dated start and copied requests (cost double count) until repaired.
- **R5 - Detail vs aggregate** tool calls/agents differ by design (`greatest(summary, observed)` vs observed only).
- **R6 - Same-UTC-day variant** of fixture (ii) (activity and reopen on one day, D1 without dimension hooks) is not excluded by a day-level bound; sub-day precision is a documented accepted design.
- **R7 - `/exit` `user_prompt`:** excluded from the end only if Task 1 finds a column that distinguishes it; otherwise it still extends the end (the up-to-date CLI no longer sends it).
- **R8 - Duration semantics** shrink for every session by the lag between its last real activity and its `session.end`/`stop` hook (seconds, per the ticket's Expected result); dashboards comparing durations across the deploy will step down slightly.
