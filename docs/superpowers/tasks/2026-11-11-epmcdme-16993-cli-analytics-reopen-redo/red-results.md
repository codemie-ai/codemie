# RED results: live PostgreSQL reopen tests, before any production change

Backend HEAD `bfdd2dcc1e`, working tree: only the new test file. Same result on PostgreSQL 17 and 14 (port 35417 / 35414; 55417 is in a
Windows reserved port range). Each test runs twice: stored activity facts derived and reset to NULL. Result is identical in both modes.

Command: `TEST_LOCAL_PG_URL=postgresql://postgres:password@127.0.0.1:35417/postgres poetry run pytest -n0 -q tests/codemie/repository/cli_analytics/postgres/test_live_session_reopen.py`
Result: **16 failed, 14 passed** (15 scenarios x 2 modes).

Setup: D1 = 10 days before D2. D1 real work 17:44-19:36 (prompt, tool, two model requests, `session.end` 20 s after the last request).
D2 = 30 minutes ago. "Reported on D2" = any of sessions, overview, users, repositories, cost, activity lists the session in the D2 whole-day
window or the 20-minute window around the reopen. "Duration" = list and detail duration, expected to end within 60 s of the last D1 request.

| # | Scenario (what D2 sends) | Reported on D2 | Duration ends at last D1 activity | Moves the end |
|---|---|---|---|---|
| 1 | lifecycle hooks; D1 has dimension hooks | no (PASS) | **FAIL** 847,503,000 ms (9.8 d) vs ~6.7 M | `last_event_at` = D2 `session.end` |
| 2 | lifecycle hooks; D1 has **no** dimension hook (OTel/usage only) | **FAIL: listed in all six endpoints, both windows** | **FAIL** 847,487,000 ms | `started_at` = D2 `session.start` (13:09:04), `activity_started_at` stays D1 (17:44:20) |
| 3 | hooks + 6 startup OTel records | no | FAIL | masked by the hooks (see 8, 9, 11) |
| 4 | hooks + `/exit` `user_prompt` | no | FAIL | masked by the hooks (see 10) |
| 5 | reopen does real work (prompt + model request) | listed on D2 and D1 (PASS) | spans D1 to D2 (PASS) | guard, passes |
| 6 | presets last 7 days / 24 hours / hour, hooks + startup records | none listed (PASS) | n/a | guard, passes |
| 7 | multi-day session, tiles in each window | PASS | n/a | all-time tiles pinned (3,030 tokens on both days) |
| 8 | startup records only, D1 **no summary** | no | **FAIL** | `fb.last_ts` (raw fallback, `_RAW_RANGE`) |
| 9 | startup records only, D1 **final summary** | no | PASS | protected: `ended_at` is the summary, fallback not computed |
| 10 | `/exit` `user_prompt` only, D1 no summary | no | **FAIL** | `fb.last_ts` |
| 11 | lifecycle hooks only, D1 final summary | no | **FAIL** | `last_event_at` only (fallback not computed) |
| 12 | lifecycle hooks only, D1 provisional summary | no | **FAIL** | `last_event_at` and `fb.last_ts` |

16992 characterization (no production change expected): continuation with no own work keeps its own Start, reports no tokens/turns/tool calls and is
not listed; continuation with own work reports only its request; each request counted once in overview, users and repositories. **All pass.**

## What this decides

1. **Admission (Task 5) is needed.** Scenario 2 reproduces the ticket: when D1 reached the backend without a dimension hook, the reopen's
   `agent.session.start` becomes `started_at`, and arm 1/2 of `_admitted_arms` list the session on D2 with its whole history. When D1 has
   dimension hooks (scenario 1) admission is already correct, as the code analysis predicted (`LEAST`, `rollups.py:2087`).
2. **Duration, `last_event_at` part (Task 3a) is needed.** Scenarios 1, 11, 12: lifecycle hooks alone move the end, whatever the summary state.
3. **Duration, raw fallback part (Task 3b) is needed.** Scenarios 8, 10, 12: a session without a final summary ends at the last startup or `/exit`
   record. A final summary protects the session from OTel records but not from lifecycle hooks (9 vs 11).
4. **`/exit` is distinguishable** by `command_name = 'exit'` on the kind-2 record (what the test seeds; the real column content is not captured in
   this repo's tests). The end filter must therefore exclude built-in commands (`BUILTIN_COMMAND_NAMES`), not only kind OTHER. Unverified against a
   real client record.
5. **Not covered:** reopen within one day of the end (the reader probes of `_activity_range`, Task 4) and the same-UTC-day variant (R6). Task 4 is
   still unproven; the probes reach only `end + 1 day`, so nothing here exercises them.

## Pre-existing failures (baseline, not caused by this work)
See `baseline.txt`: 19 unit failures (17 `test_dirty.py` year 0001/9999 timestamps raising `OSError: [Errno 22]` on Windows, 2 `test_settings.py`),
live files `test_live_route_pricing.py` and `test_live_session_subagent_cost.py`: 15 passed.

## After Tasks 2, 3a, 3b, 5 (live file, 15 scenarios x 2 modes = 34 runs; PG17 and PG14)

Per-task tables for Tasks 2, 3a and 3b were not recorded one by one at the time; what was observed: after Task 2 the
vocabulary unit test was green; after 3a + 3b the live file went from 16 failed to 2 failed (only the admission scenario
and its isolation test). Scenario 1 stays red after 3a alone (no summary in that scenario), it needs 3b (`fb.last_ts`).
The forecast in `plan.md` said 3a alone would fix it: that forecast was wrong.

State after Task 5: **34 passed on PG17 and 34 passed on PG14.** Nothing that was green turned red.

| # | Scenario | Before | After |
|---|---|---|---|
| 1, 11, 12 | lifecycle hooks move the end (`last_event_at`) | FAIL | PASS (3a, 3b) |
| 2 | D1 without dimension hook, D2 lifecycle hooks: admission + duration | FAIL | PASS (5, 3a, 3b) |
| 3, 4, 8, 10 | startup records / `/exit` move the end (`fb.last_ts`) | FAIL | PASS (3b) |
| 5, 6, 7, 9 | guards (reopen with real work, presets, multi-day tiles, final summary) | PASS | PASS |
| 16992 characterization (3), Review Focus guards (2) | all | PASS | PASS |

Unit set (`poetry run pytest -n0 tests/codemie/repository/cli_analytics tests/codemie/service/analytics tests/codemie/rest_api/routers/test_cli_analytics*.py -q`):
19 failed, 4303 passed, 48 skipped. The 19 failed are the same 19 as `baseline.txt` (sorted lists are identical). Passed +3 are the new
unit tests; skipped +34 are the new live runs without `TEST_LOCAL_PG_URL`.
Baseline live files (`test_live_route_pricing.py`, `test_live_session_subagent_cost.py`) on PG17: 15 passed.

## Task 5 EXPLAIN evidence (100,000 sessions in scratch db `bench`, PG17, windows 7 d and 1 d)

Before = the old SQL text (unbounded activity), after = the bounded text. Plans: `%TEMP%\bench\e2_before.txt`, `e2_after.txt`.

| Query | Before | After |
|---|---|---|
| arm 1 (7 d, 10,436 rows) | Bitmap Heap Scan, `session_dims_active_started`, 15.6 ms | same index, 14.2 ms; `last_activity_day` is a heap filter, no rows removed |
| arm 1 (1 d, 1,455 rows) | `session_dims_active_started`, 2.0 ms | same, 1.4 ms |
| arm 2 (7 d, 125 rows) | `session_dims_activity_pending`, 12.1 ms | same index, 4.8 ms |
| arm 2 (1 d, 21 rows) | `session_dims_activity_pending`, 0.67 ms | Index Scan on the same index, 0.24 ms |
| ids-only `sel`, arm 1 | **Index Only Scan `session_dims_active_shared_cov`, Heap Fetches 0, 63 buffers** | **Bitmap Heap Scan on `session_dims_active_started`, 1,586 heap blocks, 1,626 buffers** |
| ids-only `sel`, arms 2-5 | unchanged | unchanged (arm 2 now bounds the raw-activity probes with `day >= window start day`) |

End-to-end ids-only count with `jit=off`, 8 timed runs per variant, alternating (warm cache): 7 d before 71.6 ms / after 68.9 ms;
1 d before 38.7 ms / after 40.2 ms; row counts equal (10,556 and 1,473). The 224/307/545 ms seen in the raw EXPLAIN output are JIT
compilation, identical in both variants.

Verdict: arm 1 keeps `session_dims_active_started` in both forms, so the revert condition in the plan is not met and Task 5 is kept.
**Residual risk R11:** the ids-only form of arm 1 no longer uses the covering index, because `last_activity_day` is not part of
`session_dims_active_shared_cov`. In a warm cache the cost is not measurable; on a cold cache it reads about one heap page per 6-7 sessions
in the window instead of index pages only. Fix, if it ever shows in production: a migration that adds `last_activity_day` to the covering
index (not done: no new index without proof). The `ids_only` docstring in `reader.py` was corrected to say so.

## `/exit` command_name

The tests seed `/exit` as a kind-2 `user_prompt` record with `command_name = 'exit'`. That is the repo's own convention
(`BUILTIN_COMMAND_NAMES = ("model", "exit", "quit")`, `SENTINEL_EXACT`); no real client record was captured in this work, so the assumption
is not verified against a real CLI. If the real record has another `command_name` (or none), a `/exit` on the reopen day would again move the end of a
session that has no final summary (scenario 10). The CLI team should confirm the attribute.

## Task 4 (reader end probes), test first

New live test `test_a_reopen_less_than_a_day_after_the_end_does_not_extend_or_list_the_session` (reopen 2 h and 5 h after the last D1 activity,
lifecycle hooks + 6 startup records + `/exit`; checks list/detail duration, nothing listed on the reopen day, Users last active), 2 cases x 2 fact modes.

RED before the change (PG17): **4 failed**. Stored `ended_at` / `last_event_at` were correct (19:36 / 18:30:05), the reported duration was not:
13,980,000 ms (reopen 2 h later: ends at the `/exit` record) and 24,780,000 ms (5 h later) against ~6.72 M expected. Cause: the `later` probes of
`_activity_range` read any `log_events` / `spans` row up to one day after the stored end. So Task 4 was needed, and the 10-day scenarios did not show it.

Change: the end-side probes read real work only (same definition as `_RAW_RANGE`); the start-side probes are unchanged. Unit test
`test_the_latest_activity_probes_read_real_work_only_and_the_earliest_read_every_record` pins it.

After (PG17 and PG14): `test_live_session_reopen.py` + `test_live_route_pricing.py` + `test_live_session_subagent_cost.py` = **53 passed on each**
(38 reopen runs + 15). Unit set: 19 failed (identical to baseline), 4304 passed, 52 skipped.
Ruff format / check and the license check: clean (`make` itself is not installed here; the Makefile commands were run directly).
Guides updated: `cli-analytics-storage.md` (admission rule, stored facts, reported range, fallback table, provisional summary) and
`cli-analytics-events.md` (which hooks feed `started_at` and `last_event_at`; "activity" wording now "dimension" for the 13).

## Consumers of `last_event_at` and `fb.last_ts` (plan Task 3, step 3)

`grep -rnE "last_event_at|last_ts" src --include=*.py` (alembic excluded). `last_event_at` is now the last real-work hook (8 types);
`fb.last_ts` is now the last real-work raw record (activity hooks, usage, kind <> OTHER logs without built-in commands, kind <> OTHER spans, lines metrics).

| Consumer | Where | Before | After | Intended |
|---|---|---|---|---|
| Reported end, duration, heatmap, Sessions list/detail, session cost facts | `reader.py` `_activity_range` (558; used at 748 and 1630) | `greatest(last_event_at, ended_at, any later log/span)` | same shape; the later probes read real work only and start from the start when no end is stored | yes (the ticket) |
| Sessions list field `last_event_at` | `reader.py:726` | last of the 13 dimension hooks | last real-work hook | yes |
| Users "last active" | `reader.py` `get_users_last_active` (963-973) | last dimension hook (`last_event_at`, raw scan only when it is after the window end) | `last_event_at` is the last real-work hook; the raw scan after the window end still reads the 13 dimension hooks | yes for the first branch; second branch unchanged by design (documented as "dimension hook") |
| Stored `ended_at` of a session without a final summary | `rollups.py` 1299-1301 (`fb.last_ts`) | latest raw record of any kind | latest real-work record | yes |
| `session_files_daily` day window | `rollups.py:1220` (`greatest(cur.ended_at, fb.last_ts)`) | upper bound included startup/`/exit` days | upper bound is the last real-work day; file days come from tool hooks, which are real work, so no file day is cut | yes |
| Retention clock | `session_purge.py:59` `_idle` | `greatest(last_event_at, ended_at, updated_at if owner-bound)` then `updated_at` | same expression, fed by the new values; owner-bound rows still advance with `updated_at`, legacy rows stop advancing on a reopen that does nothing | accepted (R9) |
| Dims upsert merge | `rollups.py` 2083-2102 | `GREATEST` | `GREATEST` (never shrinks; NULL for a lifecycle-only session) | yes |

A session with no real-work hook now has `last_event_at = NULL`: `greatest()` and `coalesce()` in every consumer above ignore NULL; the reader's
later probe falls back to the start of the session (CR-003 of the review, live test `test_a_session_with_no_stored_end_is_read_within_a_day_of_its_start_only`).
