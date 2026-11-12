# Residual risks and accepted gaps (backend, reopen work)

Status: code is in the working tree, nothing committed. Evidence: `red-results.md` (RED table, after-task tables, EXPLAIN), `baseline.txt`.

## What changed (behavior)

| Area | Before | After |
|---|---|---|
| `session_dims.last_event_at` | max over the 13 dimension hooks (lifecycle hooks included) | max over the 11 real-work hooks (`ACTIVITY_HOOK_TYPES`, including the stop and the error that close a turn); `started_at` still the min over the 13 |
| Raw fallback range behind `ended_at` (`_RAW_RANGE`) | latest of any record | latest real-work record: no hook outside `ACTIVITY_HOOK_TYPES`, no log of kind OTHER, no built-in command (`/exit`, `/model`, `/quit`) and no command prompt with no model request on its span (`/cost`, `/status`), no span of kind OTHER and no interaction span without a model request or a typed prompt on its own span, only lines-of-code metrics; start side unchanged |
| Reader end probes (`_activity_range`, up to one day after the end; unbounded when no end is stored yet, as before) | any `log_events` / `spans` row | real-work rows only; start probes unchanged |
| Admission arms 1 and 2 (`_admitted_arms`) | `has_activity` / any activity, ever | activity on or after the window's first UTC day (`last_activity_day`, `_activity_from_window_start`) |

Merges (`LEAST` / `GREATEST`) never shrink a stored value, so none of this repairs a session that is already polluted.

## Accepted by the user (out of scope)

| # | Risk | Note |
|---|---|---|
| R1 | Session binding (class/origin) is unchanged | CLI <= 0.16.6 can still bind class/origin on an idle reopen |
| R2 | KPI tiles, cost, lines, users facts stay all-time | pinned by `test_reader.py` and live scenario 7 |
| R3 | Already polluted sessions are not repaired | stored `last_event_at`, `ended_at`, `started_at` keep their values; decision for the reporter (the four sessions in the ticket) |
| R4 | No cross-session dedup by message id | 16992 characterization tests pass: each request is counted once per session |

## New residual risks found in this work

| # | Risk | Detail / mitigation |
|---|---|---|
| R5 | `/exit` is recognized by `command_name = 'exit'` | repo convention only (`BUILTIN_COMMAND_NAMES`); no real client record was captured, and the CLI repository (codemie-code PR 797) holds no capture of it either. Any other command prompt with no model request on its span is also left out, so the name matters less. If the real record differs, a `/exit` after a reopen can again move the end of a session without a final summary (live scenario 10). The CLI team should confirm the attribute. |
| R6 | Admission is day-granular on the lower bound | closed in the review round for a session that started in the window: when its last activity day is the window's first day and the window does not start at midnight, a raw real-work record at or after the window start must confirm it. Still day-granular for a session that started outside the window (`_active_in_window`, pre-existing rule). |
| R7 | Admission still needs `started_at` in the window | `started_at` is the min over the 13 dimension hooks, so a reopen with no dimension hook on D1 moved it to D2; with this change the session is no longer listed for D2, but its displayed Start is unchanged (out of scope: no change to `started_at` semantics). |
| R8 | Probes reach only `end + 1 day` | beyond that the stored columns decide; the stored `ended_at` / `last_event_at` are now real-work only, so the 10-day case is covered by the stored values (live scenarios 1 to 4, 8 to 12); the within-a-day case is covered by the new live test (Task 4). |
| R9 | Retention clock | `session_purge._idle` reads `last_event_at`, `ended_at` and, for owner-bound rows only, `updated_at`; a reopen that does nothing no longer advances the first two, so a legacy (ownerless) session is purged on its last real activity, not on the reopen. Owner-bound sessions still advance with `updated_at`. Intended, but a visible change for legacy rows; pinned by a live purge test (`test_the_retention_clock_of_a_legacy_session_follows_its_last_real_work_not_a_later_lifecycle_hook`: real work 260 days ago and a `session.end` 150 days ago is purged at a 200-day retention; real work 150 days ago is kept). Why: the retention follows the last real work, as the end of the session does; a hook that a reopen sends must not keep stored data alive. Needs the product owner's confirmation. |
| R10 | Mixed semantics across rows | old rows hold the lifecycle-inclusive `last_event_at`, new rows the real-work one, until the rows age out. Users "last active" (`get_users_last_active`) follows the stored value. |
| R11 | Ids-only `sel` arm 1 lost the covering Index Only Scan | `last_activity_day` is not in `session_dims_active_shared_cov`; the arm uses `session_dims_active_started` with a heap filter (1,586 heap blocks vs 63 buffers, same time warm, 7 d window on 100k sessions). If it shows in production, add `last_activity_day` to the covering index in a migration (not done: no new index without proof). | **Accepted by the user at the code-review gate (2026-11-11)**: keep the code, correct the guide (done), re-measure at scale before release.
| R13 | The real-work filter has one definition | `postgres/real_work_sql.py`, used by the rollup fallback, the reader end probes and, since the review round, the admission raw check (`_RAW_ACTIVITY_SOURCES`), so `/exit` admits nothing. |
| R12 | Retention divergence note still applies | a session whose rollup rows all passed retention stays `has_activity` (documented divergence, unchanged). |

## Environment notes

- 19 unit failures are baseline on this Windows machine (17 `test_dirty.py` year 0001/9999 timestamps, 2 `test_settings.py`); the failed set is identical before and after.
- `make` is not installed in the Git Bash used here; the three `ruff` commands of the Makefile target were run directly, and the license check too (all clean).
- MR (when asked): the description must ask a reviewer to post `/sanity` (dependencies untouched, security-relevant admission code touched), and must not contain literal failure text.

## Review round 2 (decisions of 2026-11-11)

Fixed in the code: the raw admission check ignores `/exit` and startup records, the first-day admission of a started session is confirmed by a raw real-work record, and both branches of Users "last active" read the real-work hooks. Each has a live test that was red first.

| # | Item | Decision |
|---|---|---|
| R14 | A continuation's start can still take the parent's first copied line (summary `started_at` merged with `LEAST`) | Accepted. The corrected start comes from the new CLI; a live test pins the current behavior. Preview re-check after merge. |
| R15 | Sessions already polluted keep their stored values (merges never shrink) | Out of scope, same as R3. Follow-up: a one-off repair for the known sessions (for example 8ac53605), as a separate ticket. |
| R16 | Class/origin binding after a reopen by a CLI <= 0.16.6 (R1) | Out of scope. Follow-up ticket. The new CLI cannot trigger it. |
| R17 | No cross-session dedup of copied history (R4) | Out of scope: deduplicating means deleting or merging stored data. Follow-up ticket. |
| R18 | Session detail shows tool calls and agents that the aggregate tabs show as 0 | Accepted as a legacy-data inconsistency; data from the fixed CLI does not produce it. |
| R19 | A window longer than a day still lists a session whose last real work was on the window's first UTC day but before the window's start time, if it was later reopened idle | Accepted (auto-review thread on `_NO_RAW_CHECK_NEEDED`). Day rows hold only the day, and a raw check on long windows would drop sessions whose raw records are past retention and add a five-table probe per candidate to the default list. Windows of a day or less are exact. A guard test pins the day-granular rule. Closing it fully needs the exact last real-activity time stored on `session_dims` (migration, re-measure at scale): follow-up ticket. |
| R20 | `get_framework_sessions` (read by the usage attribution worker) counts a session by `started_at` in the window alone, with no activity check | Not fixed in this MR, same behaviour as before it. A reopen that became the session's `started_at` (R7: the first run left no dimension hook) is still counted for the reopen's period. Adding the activity gate would change the numbers the attribution worker consumes and has to stay consistent with the Elasticsearch side it is merged into, so it needs the owner of that worker: follow-up ticket. |
| R21 | A local command that expands into a prompt but whose model request is exported without the span id link is read as a command with no request; a command record that carries no span id cannot be linked to its interaction span | Accepted. The prompt record is then not real work, but its model request (`usage_requests`, the prompt hook and tool hooks) still is, so the session keeps its activity; only the end can stop at the request instead of the record. An interaction span counts only with a model request or a typed prompt on its own span (`real_work_sql._span_filter`), so a span stored before its records, or one whose command record has no span id, never moves the end. The turn rule of the rollup (`turns_daily`, `_REAL_TURN`) still needs the link to leave such a span out of the turn count, so a whole-day window of the reopen can still list the session in that case (the end stays); the live test pins the end only. |
| R22 | The evidence probe of an interaction span costs time when thousands of the newest spans have none | Measured on PostgreSQL 17 (`EXPLAIN (ANALYZE, BUFFERS)`, one session, the rollup fallback `_RAW_RANGE`): a session of 5,000 turns (24,000 spans, 28,750 logs) 2.3 ms, 396 buffers (the newest row decides at once); 2,000 local commands after 3,000 turns 1.1 s, 433k buffers; 6,000 consecutive local commands 1.9 s, 728k buffers. The filter before this round (EXISTS plus NOT EXISTS) took 1.5 s for the spans alone in the 6,000 case, so for the spans the evidence form is about a third cheaper. The cost is paid once per recompute of a dirty session (every 30 s at most) and only while the newest rows are all without evidence; there is no `(session_id, span_id)` index, the probe uses `log_events_session_ts` over one hour on each side. If it shows in production, add that index in a migration (not done: no new index without proof). |
| R11 | Loss of the covering index-only count | Accepted again: docs fixed, re-measure at scale (about 1.9M sessions) before or right after release; the migration is the last resort. |

## Rollout and old data (decision of 2026-11-11: documented, no action)

None of these is a defect of the fix. They come from data stored before the fix and from clients that have not updated yet. Nothing is repaired or backfilled by this change.

| # | Item | What stays as it is | When it stops |
|---|---|---|---|
| O1 | CLI <= 0.16.6 reopens | an old client still sends lifecycle records and a session summary on a prompt-less reopen, so the stored end (`ended_at`) of that session moves to the reopen time. The backend ignores lifecycle records for `last_event_at`, but cannot tell a summary written by an idle reopen from a real one. | as users update the CLI; no backend action |
| O2 | Sessions already polluted (for example 8ac53605 and three more) | stored duration, start and totals keep their old values (the merges never shrink: R3, R15) | only by a one-off repair UPDATE, a separate ticket (not done) |
| O3 | Copied history of continued sessions stored by CLI <= 0.16.6 | rows are stored in both sessions; no cross-session dedup (R4, R17) | only by a data repair, a separate ticket (not done) |
| O4 | Class and origin bound by an old CLI after an idle reopen | stored binding stays (R1, R16) | as users update; old rows are not rewritten |
| O5 | Old rows next to new rows | `last_event_at` of old rows includes lifecycle records, new rows do not (R10); "last active" follows the stored value | as old rows age out |
| O6 | Sub-day windows over old data | a window of a day or less that does not start at 00:00 UTC needs a raw real-work record, unless it starts before the raw retention (90 days): then the raw rows may be purged and the day rows decide, as for a longer window (so the first day of such an old window is day-granular, like R19) | no action |
| O7 | Multi-day sessions | the tiles show the session's full total in each window it appears in (R2) | accepted semantics |

Jira note: state these as known limits when closing EPMCDME-16993, so they are not read as unfinished work.
