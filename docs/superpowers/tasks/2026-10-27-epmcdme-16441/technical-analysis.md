# Technical Research

**Task**: cli-analytics rollups session_dims subagent (EPMCDME-16441 P3/P4 backend verification)
**Generated**: 2026-10-27
**Research path**: filesystem

---

## 1. Original Context

Verify against THIS repo (backend, src/codemie/, current branch) the claims of /Users/vponkratina/work/ai/codemie-code/docs/superpowers/tasks/2026-10-27-epmcdme-16441/handoff-backend.md for (P3) sessions with usage events but no agent.session.end and (P4) subagent usage cost. Report which claims hold, which are wrong, narrowest code changes, file:line facts and existing tests.

---

## 2. Codebase Findings

All paths under `src/codemie/repository/cli_analytics/` (`postgres/rollups.py` = R, `postgres/reader.py` = D).

### P3 facts
- `agent.session.end` is a DIMENSION hook type (`vocabulary.py:78-92`, list `DIMENSION_HOOK_TYPES`). Dimension types: session.start/stop/end/compact, prompt.submit, tool.*, turn.error, subagent.start/stop, notification. `agent.usage.request`, `agent.subagent.usage`, `agent.git.snapshot` are NOT dimension types.
- `session_dims.started_at/last_event_at/branch/repository/project/first_prompt` come from the DIMENSIONS upsert (R:1907-1925): `min/max(ts) FILTER (event_type = ANY(_DIM_TYPES))`; `_earliest()` (R:183-186) takes the earliest non-empty value over dimension types only. So `session.end` is only one of 13 types; any other dim event (session.start, Stop-derived session.stop, prompt.submit, tool.*) sets started_at. A session with ONLY usage requests and no dim event gets started_at NULL (min over empty FILTER).
- Reader excludes `started_at IS NULL` (D:288; storage guide line 53 "Sessions without started_at are excluded"). Claim holds, but trigger is "no dimension event at all", not "no session.end".
- `ended_at`/`duration_ms` (R:1185-1200, 1238-1240): summary final -> summary ended_at; no summary -> `fb.last_ts` (raw range last); provisional summary -> greatest(summary ended_at, fb.last_ts). Fallback for a lost SessionEnd already exists (EPMCDME-15584, comment R:1109-1112). Reader does not read `session_dims.duration_ms`; it reports end = greatest(last_event_at, ended_at, latest log/span row) and start = least(started_at, activity_started_at, ...) (D:210-225, storage guide line 291). So ended_at/duration for a no-end session is derived from last events already. Handoff ticket's "derive ended_at from the last event" is ALREADY implemented.
- Branch: `session_dims.branch` = earliest non-empty `git_branch` of dim-type events (R:1912); `branch_dominant` (R:1097-1099) = most frequent `hook_events.git_branch` over ANY event type. `agent.git.snapshot` (`_GIT_EVENT`, R:776, `_GIT_TEXT`, `git_commits` R:790,888) feeds git heads/commits columns and identity candidates, not `branch`. Handoff statement "branch sourced from hook_events.git_branch" holds; the git snapshot-only fields (commits, HEAD end) are lost without the event.
- Rollups needing session.end: grep of `session.end` in rollups.py/reader.py finds no reference; none required (claim 5 holds). Cost: `cost_daily` built from `_cli_requests` (R:739-762, `_LOG` kind), no session.end dependency. `session_dims.source_cost_usd` = sum(cost_daily) (R:1070).
- Ingest: no rejection by type seen in this grep (not traced through otlp.py fully; handoff itself says unrejected). Dirty marking: `dirty.py:19-26` RollupFamily; USAGE_FACTS=16 for usage hooks.

### P4 facts
- `_totals_are_a_source` at R:589-607 matches the handoff (four conditions: no usage_requests row for session+agent; no log_events API_REQUEST for the session any agent; no non-subagent_usage session_usage row for that agent; no session_usage row with attrs `otel_requests`). Hold.
- Totals row `source_cost_usd` NULL: `_TOTALS_ROWS` (R:~621-651) emits `NULL::float8` in the cost position; the handoff cites R:640 - not line-verified but content holds. Totals are the latest `agent.subagent.usage` event with totals per (session, agent) via `DISTINCT ON ... ORDER BY h.ts DESC, h.ingest_seq DESC` (R:~635-647) -> "latest wins per agent" holds. Test `test_subagent_totals_come_from_the_latest_event_that_carries_them` (tests test_rollups.py:1103).
- Totals reach `session_usage` only (R:~660+ `_SESSION_USAGE`; the `_REQUESTS` temp table is the source of `cost_daily`, R:242-262, and totals are not in it). `cost_daily` (R:739-762) is `_LOG`-family from requests; totals never enter it. Reader cost/KPI queries read `cost_daily` (D:340-380, 764); grep finds NO `session_usage` or `subagent_invocations` reference in reader.py. So subagent-only totals NEVER reach dashboard/KPI cost. Handoff claim (storage guide) holds, and this is stronger than the handoff's possible drop: totals have no cost even when they are a source (cost NULL, and not in cost_daily).
- Consequence: the handoff's "P4 drop when OTel api_request present and agent.usage.request absent" is real but narrower than described: cost of a subagent with only totals is absent from KPIs in ALL cases, independent of `_totals_are_a_source`. The `_totals_are_a_source` gate only decides token rows in session_usage.
- Dedup by event_id: not re-verified in otlp.py in this pass (handoff cites otlp.py:621-627; file exists, `git_branch` handling at 611-617). Marked unverified.

---

## 3. Documentation Findings

- `.ai-run/guides/integration/cli-analytics-storage.md` (windows line 53, summary/fallback table ~204-209, reader range notes 290-291) and `cli-analytics-events.md` cover this domain. Conventions: windows decided only by `session_dims.started_at`.
- Decision comment R:1109-1112 and R:1185-1200: EPMCDME-15584 MR !4532 (cancelled SessionEnd fallback duration).

## 4. Testing Landscape

- `tests/codemie/repository/cli_analytics/postgres/test_live_session_duration_fallback.py` (live DB): cancelled SessionEnd with non-final summary (line 158), repeated cancelled SessionEnds (345), cost facts carry fallback duration (456), cost identical with/without provisional fallback (486). Covers P3 duration/ended_at for lost end.
- `test_rollups.py`: `test_request_derived_rows_of_the_agent_disqualify_its_totals` (183), `test_otel_usage_rows_of_the_session_disqualify_subagent_totals` (1044), totals from latest event (1103), subagent_invocations (672, 1324). SQL-string assertions, no DB.
- `test_reader.py`, `test_reader_live_session_range.py` cover the window and range.
- Gaps: no test found with usage-only sessions (no dimension event) in the reader; no test asserting subagent totals' cost absence from cost_daily; no `session.end`-specific test (grep: none).

## 5. Configuration and Environment

`CLI_ANALYTICS_SESSION_RETENTION_DAYS`, `CLI_ANALYTICS_SESSION_PURGE_BATCH_SIZE` (storage guide line 340) concern purge, not this task. No feature flags found for this area.

---

## 6. Risk Indicators

- Speculative: pricing totals (tokens x price) inside rollups SQL to fill cost for totals-only subagents touches `_TOTALS_ROWS`, the model-price source and cost_daily semantics; larger than a gate tweak and risks double counting with requests.
- Speculative: changing `_totals_are_a_source` to ignore OTel api_request alone would reintroduce double counting where requests exist.
- Rollups.py is 2343 lines of SQL strings; edits are tested mostly by string asserts, so live-DB tests are needed.
- Dedup/ingest-acceptance claims (otlp.py) not independently traced here.
- Checkout differs from commit 48fa31b56 possibly; line numbers verified: 589-607 exact; cited 627-651/640 approximately matched.

---

## 7. Summary for Complexity Assessment

P3: Mostly holds as a non-issue. Rollups do not require `agent.session.end`; it is one of 13 dimension types. `started_at` needs ANY dimension event; `ended_at`/duration already fall back to the raw range (EPMCDME-15584), and the reader computes the reported range from last events. Real residual losses: sessions with zero dimension events (reader excludes them, D:288) and `agent.git.snapshot`-only fields (git commits/heads). Branch is not sourced from git.snapshot.

P4: `_totals_are_a_source` matches the handoff. But totals never reach `cost_daily` or KPI cost at all (reader has no session_usage/subagent_invocations reads; totals rows have NULL cost), so cost for totals-only subagents is missing regardless of the gate. Cost counted once via `agent.usage.request` rows in `cost_daily`.

Surface: reader.py/rollups.py only; testing coverage for adjacent behavior is good but the two gaps above are untested.

---

## 8. External References

- `/Users/vponkratina/work/ai/codemie-code/docs/superpowers/tasks/2026-10-27-epmcdme-16441/handoff-backend.md`: resolved, read fully. Siblings findings-F2/T2/T3.md not read (handoff summarises them; not needed for the repo checks).
