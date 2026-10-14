# EPMCDME-15512 Fix Subagent Duration in Session Timeline — Implementation Plan

> **Execution status (synced 2026-10-06).** The step checkboxes below were not ticked during execution; this table
> is the record.
>
> | Task | Status | Evidence |
> |---|---|---|
> | 1. Replace the test that encodes the bug | done | `test_agent_dispatch_uses_subagent_run_duration_ms` |
> | 2. Reader SQL joins subagent run time by agentId | done, revised | `stopped` CTE on the typed `agent_id` column (not `subagent_invocations`); anchor revised to `agent.tool.start`, see "Post-completion changes" |
> | 3. Handler uses subagent run time, null when unknown | done | `_duration_for_kind`, `_clamp_to_session`; handler tests for null, clamp, start before/after the session |
> | 4. Live test for parallel vs sequential windows | done (2026-10-06) | live `test_parallel_subagents_have_overlapping_windows`, `test_sequential_subagents_do_not_overlap` (reader rows through the handler) and handler unit tests `test_parallel_agents_render_as_overlapping_bars`, `test_sequential_agents_render_as_separate_bars`; also checked on real data (124 sequential pairs do not overlap, min gap 19 s) |
> | 5. Full verification and manual UI check | done | `qa-report.md` |

## Clarification assumptions

No answers were available at Stage 2, so these choices were made and are to be confirmed by the reviewer:

1. **Subagent run time source (REVISED after checking the live DB).** `subagent_invocations.duration_ms` is NOT usable: in the local analytics DB, 103 of 127 rows have `duration_ms` NULL, `started_at` NULL, `tool_use_id` empty, `attribution = 'heuristic'`. The `agent.subagent.usage` hook is not being sent there at all. The run time is instead:
   - **start** (superseded on 2026-10-06, see "Post-completion changes" below: the start is now `agent.tool.start`) = `ts` of the Agent `agent.tool.end` hook event (the launch; `tool_response.agentId`). It matches the `agent.subagent.start` ts to within ~1–100 ms where a start event exists. Start events exist for only 24 of the 128 agents, so they are not used.
   - **end** = `max(ts)` of the `agent.subagent.stop` hook event whose `attrs->'raw'->>'agent_id'` equals that agentId. Verified: 123 stop events, all with `agent_id`; no agent has more than one stop.
   - **no end** = `null`. 5 of 128 agents have no stop event (session cut off or hook not received).
   - The "SubagentHandback" name in the ticket does not exist in either repo. The Claude Code hook is `SubagentStop`.
2. **Pairing key.** `tool_use_id` → `agent.tool.end.tool_response.agentId` (existing `launched` CTE) → `subagent_invocations.agent_id`.
3. **Missing end.** If no matching invocation exists, or its `duration_ms` is NULL (end before start, or session ended mid-dispatch), the agent row's `duration_ms` is `null`, not `0`. This follows the contract rule "missing numbers are null, never 0".
4. **Existing unit test.** `test_agent_dispatch_uses_tool_execution_duration_ms` encodes the bug and is replaced, not kept.
5. **UI.** `codemie-ui` is not changed in this plan. AC 3 (overlap rendering) is verified manually after the API is fixed. If the UI draws bars from `start_offset_ms` and `duration_ms`, no UI change is expected.

---

**Goal:** Agent rows in `dispatches[]` report the subagent's real run time, so the Timeline shows overlapping bars for parallel subagents and detail panel values match the API.

**Architecture:** The reader SQL gets a `stopped` CTE (latest `agent.subagent.stop` per agent_id from `hook_events`). It is joined by the existing `launched.agent_id`, and the duration is `stopped.ended_at − launched.launch_ts`. The handler uses that value for agent rows, the same way it already uses `real_duration_ms` for skills and commands. Cost and token attribution are not touched.

**Tech Stack:** Python 3.12, SQLModel/asyncpg raw SQL (`CliAnalyticsReader`), pytest, live Postgres tests (skipped without `TEST_LOCAL_PG_URL`).

**Spec:** `docs/superpowers/tasks/2026-10-06-fix-subagent-duration-timeline/technical-analysis.md` (research) and the EPMCDME-15512 ticket (expected result and acceptance criteria).

## Global Constraints

- Missing numbers are `null`, never `0` (cli-analytics-events contract).
- Parallel-dispatch cost and token attribution from commit `d09b06bb0` must not change: `agent_usage` / `parent_usage` CTEs stay as they are.
- Skill and command duration stays on `real_duration_ms` (existing test `test_skill_dispatch_uses_real_duration_ms` must still pass).
- Commit messages start with `EPMCDME-15512:`.

## Review Focus

1. Two parallel subagents produce overlapping `[start_offset_ms, start_offset_ms + duration_ms)` windows, not an even split. → Task 4.
2. Two sequential subagents produce non-overlapping windows. → Task 4.
3. Session cut off mid-subagent (no invocation end) gives `duration_ms: null`, not `0` and not a negative value. → Task 3.
4. `start_offset_ms + duration_ms` never exceeds the session duration for a subagent that ends before the session does. → Task 4.
5. A subagent whose `agentId` is missing from the `agent.tool.end` hook gives `null` duration, and the row still appears with its label. → Task 2.

---

### Task 1: Replace the test that encodes the bug

**Files:**
- Modify: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py:301-306`

**Interfaces:**
- Consumes: `LocalAnalyticsHandler._build_dispatches(rows, session_start, session_duration_ms, session_cost)`
- Produces: row key `subagent_duration_ms` (int | None) consumed by Task 3.

Test-first: yes — `test_agent_dispatch_uses_subagent_run_duration_ms` fails with `6 == 64500`.

- [ ] **Step 1: Write the failing test**

Replace the test at lines 301–306 with:

```python
def test_agent_dispatch_uses_subagent_run_duration_ms():
    """Agent duration_ms is the subagent's real run time, not the Agent tool-execution span."""
    rows = [_dispatch_row(subagent_type="Explore", duration_ms=6, offset_s=25) | {"subagent_duration_ms": 64500}]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    agent = next(d for d in result if d["kind"] == "agent")
    assert agent["duration_ms"] == 64500
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py::test_agent_dispatch_uses_subagent_run_duration_ms -v`
Expected: FAIL with `assert 6 == 64500`

- [ ] **Step 3: Commit the test change only after Task 3 passes**

No commit yet. Task 1 and Task 3 land together so the branch is never left with a red suite.

---

### Task 2: Reader SQL joins subagent run time by agentId

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/reader.py:626-715` (`get_session_detail_dispatches`)
- Test: `tests/codemie/repository/cli_analytics/postgres/test_reader.py` (SQL text-shape test)

**Interfaces:**
- Consumes: CTE `launched(tool_use_id, agent_id)` already in the query (lines 653–659).
- Produces: result column `subagent_duration_ms` (bigint | NULL) on each dispatch row.

Test-first: yes — `test_dispatch_query_takes_agent_run_duration_from_subagent_stop_events` fails because the query text has no `stopped` CTE.

- [ ] **Step 0: Verify the stored shape before writing SQL (already done once; re-check on a fresh DB)**

```bash
docker exec c3b61d0059d8 psql -U postgres -d postgres -c "select event_type, ts, attrs->'raw'->>'agent_id' from codemie_analytics.hook_events where event_type='agent.subagent.stop' limit 3"
```
Expected: the `agent_id` column is non-null. Do NOT use `subagent_invocations.duration_ms`.

- [ ] **Step 1: Write the failing test**

Add to `tests/codemie/repository/cli_analytics/postgres/test_reader.py`:

```python
def test_dispatch_query_takes_agent_run_duration_from_subagent_stop_events():
    sql = CliAnalyticsReader._DISPATCHES_SQL_FOR_TEST  # see Step 3: expose the built SQL text
    assert "'agent.subagent.stop'" in sql
    assert "subagent_duration_ms" in sql
```

If the query is an inline f-string with no accessor, extract it to a module-level constant `_DISPATCHES_SQL` in Step 3 and assert against that constant instead.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py::test_dispatch_query_takes_agent_run_duration_from_subagent_invocations -v`
Expected: FAIL (`subagent_invocations` not in query text)

- [ ] **Step 3: Write minimal implementation**

In `reader.py`, make two changes:

1. In the `launched` CTE, also select the launch time, and keep the latest row per tool call:

```sql
        ), launched AS (
            SELECT DISTINCT ON (tool_use_id) tool_use_id, ts AS launch_ts,
                   substring(attrs->>'tool_response' from 'agentId[^a-zA-Z0-9_-]+([a-zA-Z0-9_-]+)') AS agent_id
            FROM hook_events
            WHERE session_id = $sid::text AND event_type = 'agent.tool.end' AND tool_use_id <> ''
            ORDER BY tool_use_id, ts DESC
```

2. Add a CTE after `launched`, the latest subagent stop per agent:

```sql
        ), stopped AS (
            -- when each subagent stopped, from its own SubagentStop hook event
            SELECT attrs->'raw'->>'agent_id' AS agent_id, max(ts) AS ended_at
            FROM hook_events
            WHERE session_id = $sid::text AND event_type = 'agent.subagent.stop'
            GROUP BY attrs->'raw'->>'agent_id'
```

In the outer `SELECT`, add the column next to `duration_ms`. A stop before the launch, or no stop, gives NULL:

```sql
               CASE WHEN st.ended_at >= la.launch_ts
                    THEN (extract(epoch FROM st.ended_at - la.launch_ts) * 1000)::bigint END AS subagent_duration_ms,
```

and join `stopped` next to `agent_usage`:

```sql
        LEFT JOIN stopped st ON st.agent_id = la.agent_id
```

Note: `la.launch_ts` is NULL when there is no launch row; `NULL >= …` is NULL, so the CASE yields NULL. Keep the `ex` join and `duration_ms` column unchanged.

Keep the `ex` join and `duration_ms` column unchanged; the handler decides which one to show.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -v`
Expected: PASS (new test and all existing SQL-shape tests)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/repository/cli_analytics/postgres/reader.py tests/codemie/repository/cli_analytics/postgres/test_reader.py
git commit -m "EPMCDME-15512: read subagent run time from subagent_invocations in dispatch query" -m "Generated with AI" -m "Co-Authored-By: codemie-ai <codemie.ai@gmail.com>"
```

---

### Task 3: Handler uses subagent run time for agent rows, null when unknown

**Files:**
- Modify: `src/codemie/service/analytics/handlers/cli_analytics_handler.py:758-760`
- Test: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` (Task 1 test + two more below)

**Interfaces:**
- Consumes: row key `subagent_duration_ms` from Task 2.
- Produces: `dispatches[].duration_ms` is `int | None`; `None` for agents without a known end.

Test-first: yes — `test_agent_dispatch_without_subagent_end_has_null_duration` and the Task 1 test fail before the helper exists.

- [ ] **Step 1: Write the failing tests**

Add to the same test file, after Task 1's test:

```python
def test_agent_dispatch_without_subagent_end_has_null_duration():
    """A subagent with no known end (session cut off, or no usage event) reports null, not the tool span or 0."""
    rows = [_dispatch_row(subagent_type="Explore", duration_ms=6, offset_s=25) | {"subagent_duration_ms": None}]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    agent = next(d for d in result if d["kind"] == "agent")
    assert agent["duration_ms"] is None


def test_skill_dispatch_ignores_subagent_duration_ms():
    rows = [_dispatch_row(skill_name="codemie:codemie-analytics", real_duration_ms=152845) | {"subagent_duration_ms": 1}]
    result = LocalAnalyticsHandler._build_dispatches(rows, datetime(2026, 1, 1, 0, 0, 0), 160000, 1.0)
    skill = next(d for d in result if d["kind"] == "skill")
    assert skill["duration_ms"] == 152845
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py -k "subagent_run_duration or without_subagent_end or ignores_subagent" -v`
Expected: FAIL (`6 == 64500`, and `0 is None` / `6 is None`)

- [ ] **Step 3: Write minimal implementation**

Replace lines 758–760 in `_build_dispatches`:

```python
                    "duration_ms": _duration_for_kind(row, kind),
```

and add a module-level helper next to `_i` / `_f`:

```python
def _duration_for_kind(row: dict, kind: str) -> int | None:
    """Skills and commands use the interaction span; agents use the subagent's own run time (null if unknown)."""
    if kind in ("skill", "command"):
        return _i(row.get("real_duration_ms"))
    if kind == "agent":
        value = row.get("subagent_duration_ms")
        return None if value is None else _i(value)
    return _i(row.get("duration_ms"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py -v`
Expected: PASS (all handler tests, including `test_skill_dispatch_uses_real_duration_ms`)

- [ ] **Step 5: Check the response model accepts null**

Run: `grep -n "duration_ms" src/codemie/rest_api/models/cli_analytics.py`
If the dispatch model types `duration_ms` as `int` without `| None`, change it to `int | None = None` and add a serialization test in `tests/codemie/rest_api/models/test_cli_analytics_models.py`.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/analytics/handlers/cli_analytics_handler.py src/codemie/rest_api/models/cli_analytics.py tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py tests/codemie/rest_api/models/test_cli_analytics_models.py
git commit -m "EPMCDME-15512: report subagent run time for agent dispatches, null when unknown" -m "Generated with AI" -m "Co-Authored-By: codemie-ai <codemie.ai@gmail.com>"
```

---

### Task 4: Live Postgres test for parallel vs sequential windows

**Files:**
- Modify: `tests/codemie/repository/cli_analytics/postgres/test_reader_live_dispatches.py`

**Interfaces:**
- Consumes: `_seed_agent` builder, `_cleanup`, and the `subagent_invocations` insert (Task 2 Step 0 shows the table).
- Produces: none (test only).

Test-first: yes — the overlap and non-overlap live tests are written before any check of the result; they run only with `TEST_LOCAL_PG_URL` set.

- [ ] **Step 1: Write the failing test**

Add two tests using the existing `_seed_agent` builder. Seed `agent.tool.end` (with `tool_response.agentId`) and `agent.subagent.stop` hook events so the parallel agents have durations 64500 and 34200 and the sequential pair 20600; use the `_seed_agent` builder and the hook-event builders in `tests/codemie/repository/cli_analytics/support/hook_event_builders.py`. Assert:

```python
async def test_parallel_subagents_have_overlapping_windows(live_reader):
    dispatches = await live_reader.get_session_detail_dispatches(PARALLEL_SESSION)  # seeded session id
    agents = [d for d in build_dispatches(dispatches) if d["kind"] == "agent"]
    a, b = sorted(agents, key=lambda d: d["start_offset_ms"])
    assert a["duration_ms"] == 64500 and b["duration_ms"] == 34200
    assert b["start_offset_ms"] < a["start_offset_ms"] + a["duration_ms"]  # overlap


async def test_sequential_subagents_do_not_overlap(live_reader):
    dispatches = await live_reader.get_session_detail_dispatches(SEQUENTIAL_SESSION)
    agents = [d for d in build_dispatches(dispatches) if d["kind"] == "agent"]
    a, b = sorted(agents, key=lambda d: d["start_offset_ms"])
    assert b["start_offset_ms"] >= a["start_offset_ms"] + a["duration_ms"]
```

`PARALLEL_SESSION`, `SEQUENTIAL_SESSION`, `live_reader` and `build_dispatches` are the names used by the existing live test file; check them there before writing and reuse them. Do not invent new fixtures.

- [ ] **Step 2: Run test to verify it fails**

Run: `TEST_LOCAL_PG_URL=<url> pytest tests/codemie/repository/cli_analytics/postgres/test_reader_live_dispatches.py -k "overlapping or do_not_overlap" -v`
Expected: FAIL before Tasks 2–3 are in place; PASS after.

Without `TEST_LOCAL_PG_URL` the tests skip. Report that as skipped, not passed.

- [ ] **Step 3: Implementation**

None beyond Tasks 2–3. If the test fails after them, fix the SQL join or the handler, not the test.

- [ ] **Step 4: Run test to verify it passes**

Run: `TEST_LOCAL_PG_URL=<url> pytest tests/codemie/repository/cli_analytics/postgres/test_reader_live_dispatches.py -v`
Expected: PASS, including the two existing cost-attribution tests.

- [ ] **Step 5: Commit**

```bash
git add tests/codemie/repository/cli_analytics/postgres/test_reader_live_dispatches.py
git commit -m "EPMCDME-15512: cover parallel and sequential subagent windows in live dispatch test" -m "Generated with AI" -m "Co-Authored-By: codemie-ai <codemie.ai@gmail.com>"
```

---

### Task 5: Full verification and manual UI check

**Files:** none changed.

- [ ] **Step 1: Run the affected unit suites**

Run: `pytest tests/codemie/service/analytics tests/codemie/repository/cli_analytics tests/codemie/rest_api/models/test_cli_analytics_models.py -v`
Expected: PASS, live tests reported as skipped if no DB.

- [ ] **Step 2: Lint**

Run: `make ruff` (per `.ai-run/guides/quality-gates.md`).
Expected: no errors.

- [ ] **Step 3: Manual check against the reproduction sessions**

Re-query sessions `ad5d81b2-eba9-4288-a9fd-e0a524461060` and `9c4725f2-954e-4463-bc88-5a97608ece42` via the session detail endpoint. Expected: agent `duration_ms` ≈ 64500, 34200 (parallel) and ≈ 20600 (sequential), within ±1 s. Check AC 2 with `start_offset_ms + duration_ms <= session duration`.

- [ ] **Step 4: Manual UI check**

Open the Timeline in `codemie-ui` (`http://localhost:5173`). Expected: parallel subagents render overlapping bars; the detail panel shows the same duration as the API. If the UI computes its own duration, record that as a follow-up and do not change `codemie-ui` in this task.

---

## Summary of changed surfaces

| File | Change |
|---|---|
| `src/codemie/repository/cli_analytics/postgres/reader.py` | `launched.launch_ts`, new `stopped` CTE, `subagent_duration_ms` column |
| `src/codemie/service/analytics/handlers/cli_analytics_handler.py` | `_duration_for_kind` helper; agent rows use subagent run time |
| `src/codemie/rest_api/models/cli_analytics.py` | `duration_ms` may be `None` (only if currently typed `int`) |
| tests (3 files) | replace buggy assertion; add null, skill-unchanged, live overlap tests |

---

## Post-completion changes (sync, 2026-10-06)

Made on branch `EPMCDME-15515-15512_fix-dispatch-cost-and-timeline` after the API response for session
`52d226aa-da7e-4fa3-8903-fb9c81dc1ecf` was validated against the raw telemetry.

**Why.** The ticket defines the run time "from the `Agent` tool call until the subagent returned its result". A
background agent was measured from its async launch ack (`agent.tool.end`), which arrives 1.9–8.3 s after the call
starts in all 32 real background dispatches in the local analytics DB, so its duration fell short by more than the
AC1 tolerance (~1 s). In session `52d226aa` the API returned 54355 / 24795 ms against 56923 / 27242 ms by the
ticket's definition.

**Change.**

- [x] `launched.launch_ts` is now `coalesce(s.ts, e.ts)`: the `agent.tool.start` hook event for background and
      foreground agents alike, the launch ack only when the start is missing. The `async_launched` branch is gone.
      `agent.tool.start` precedes the Agent tool span by 150–300 ms, within tolerance; it exists for every real
      dispatch that has hook events.
- [x] The bar's `start_offset_ms` moves with it (same anchor as the duration).
- [x] Live test `test_subagent_run_time_runs_from_its_call_start_to_its_stop_event_and_is_null_without_one`: the
      seed now sends `agent.tool.start` for every agent and the background ack 3 s later, like real telemetry. Red
      before the change (27000, from the ack), green after (30000); it also asserts `dispatch_start` is the call start.
- [x] SQL-shape test `test_agent_run_time_runs_from_its_call_start_to_its_subagent_stop_event` asserts
      `coalesce(s.ts, e.ts) AS launch_ts` and no `async_launched`.

**Result for session `52d226aa`** (API): Explore #1 offset 7661, duration 57102 ms (ticket definition 56923);
Explore #2 offset 8597, duration 27502 ms (27242). Bars overlap; `offset + duration` is within the session (311547).
- [x] Task 4 closed: live tests `test_parallel_subagents_have_overlapping_windows` and
      `test_sequential_subagents_do_not_overlap` seed agents like real telemetry and check the bars the API returns
      (reader rows passed through `LocalAnalyticsHandler._build_dispatches`); handler unit tests
      `test_parallel_agents_render_as_overlapping_bars` and `test_sequential_agents_render_as_separate_bars` cover the
      same rule without a database, so it is also checked where live tests skip. Mutation check: with the original
      bug (tool-execution span as the duration) all four fail; with every agent ending at the session's last
      SubagentStop both live tests fail.
- [x] Sonar: the bar placement made `_build_dispatches` exceed the cognitive complexity limit (16 > 15); it moved to
      the helper `_bar_position` without a behaviour change (all handler placement tests unchanged and green).

Review record: `code-review-check.json` in this folder.
