# EPMCDME-15584: SessionEnd Duration Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the `SessionEnd` hook is cancelled on a normal `/exit`, CLI Analytics session duration must stop truncating to the last activity hook event and instead read the already-computed `session_dims.duration_ms` fallback (summary-or-raw-range), in both the session-list and session-detail duration paths.

**Architecture:** `session_dims` already carries two independent time-pair families: `started_at`/`last_event_at` (hook-driven, decide the dashboard window — untouched by this fix) and `activity_started_at`/`ended_at`/`duration_ms` (raw-record-driven, computed by `rollups.py` from `agent.session.summary` or, when no summary exists, a raw-range fallback over `hook_events`/`usage_requests`/`log_events`/`spans`/`metric_points`). The three duration-computation call sites — `reader.get_session_durations`, `reader.get_session_detail_meta`, and `cli_analytics_handler.get_sessions` — currently compute `last_event_at − started_at` instead of reading `duration_ms`. This plan makes all three prefer `session_dims.duration_ms`, falling back to the hook-pair diff only when `duration_ms` itself is `NULL` (e.g. rollup hasn't run yet), so a cancelled `SessionEnd` hook no longer truncates the displayed duration.

**Tech Stack:** Python 3.12, asyncpg (raw SQL), pytest + pytest-asyncio, FastAPI/Pydantic REST models (unchanged — this fix changes only the SQL/Python computing the values the existing `int`-typed `duration_ms` fields already carry).

**Spec:** `docs/superpowers/tasks/2026-10-14-epmcdme-15584-sessionend-duration-fallback/technical-analysis.md` (no separate spec.md — sdlc-light; this plan's Goal/Architecture section is the requirements block).

## Global Constraints

- Backend-only scope: AC1 (reliable `/exit` completion) and the "repeated normal exits" half of AC5 depend on the CLI hook repo (`codemie-code`), not present in this checkout — out of scope here, per `technical-analysis.md` §Risk Indicators.
- Do not touch `started_at`/`last_event_at` semantics or the window-membership logic in `_sessions_cte` (`d.started_at IS NOT NULL AND {_STARTED_IN_WINDOW}`) — only the duration computation changes.
- `LocalAnalyticsSessionRow.duration_ms` and `LocalAnalyticsSessionDetail.duration_ms` (REST models, `src/codemie/rest_api/models/cli_analytics.py:61,182`) are typed `int`, not `int | None` — do not widen them to nullable; `0` remains the correct last-resort sentinel when every source is genuinely missing, so the final fallback stays `0`, not `None`.
- No new third-party dependencies, no new migration — `session_dims.activity_started_at`/`ended_at`/`duration_ms` already exist (`a1c1a0000006_cli_analytics_schema_extension.py`).
- Follow the existing SQL text-shape test pattern (`_SqlRecorder` + substring/regex assertions on the rendered SQL) already used throughout `tests/codemie/repository/cli_analytics/postgres/test_reader.py` — do not add a live-Postgres test for this.
- Commit format: `EPMCDME-15584: <imperative summary>`.

## Review Focus

- A session whose rollup has not run yet (`session_dims.duration_ms IS NULL`) must still get a duration via the existing hook-pair fallback, not `0` or a crash — covered in Tasks 1, 2, 4.
- A session with zero activity at all (both `duration_ms` and the hook pair unavailable) must still return `0` for the `int`-typed field, not `None` or a SQL error — covered in Task 2 (existing `coalesce(..., 0)` outer fallback is kept, just reordered).
- The session-list (`get_session_durations`) and session-detail (`get_session_detail_meta`) duration values must agree for the same session once both read `duration_ms` — covered by Tasks 1 and 2 using the identical coalesce expression.
- The overview KPI "total duration" and "avg session duration" (both built from `get_session_durations` rows, `cli_analytics_handler.py:344,505`) must reflect the fallback automatically since they consume the reader's output — no separate code change needed, but Task 1's test must assert the SQL actually changed so this isn't accidentally skipped.
- `get_sessions` (list view, Python-side duration) must not regress when the repository mock omits `session_duration_ms` entirely (today's test fixtures do) — covered by Task 4's regression assertion against the existing `BASE_COST_ROW` fixture.

---

### Task 1: Reader — expose and use `session_dims.duration_ms` in `get_session_durations`

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/reader.py:176-192` (`_sessions_cte`), `src/codemie/repository/cli_analytics/postgres/reader.py:462-469` (`get_session_durations`)
- Test: `tests/codemie/repository/cli_analytics/postgres/test_reader.py`

**Interfaces:**
- Consumes: `_ms(column: str) -> str` (existing helper, `reader.py:131`) — epoch-ms SQL expression.
- Produces: `_sessions_cte` now also selects `d.duration_ms AS session_duration_ms` in the `sel` CTE, consumed by Task 3.

- [ ] **Step 1: Write the failing test**

Add to `tests/codemie/repository/cli_analytics/postgres/test_reader.py` (near `test_session_durations_have_no_overlap_check`, line ~284):

```python
@pytest.mark.asyncio
async def test_session_durations_prefer_duration_ms_fallback():
    """A session with a cancelled SessionEnd still gets its real duration from session_dims.duration_ms."""
    sql, _ = await _sql_of("get_session_durations")

    assert "coalesce(session_duration_ms" in sql


def test_sessions_cte_selects_the_duration_ms_fallback_column():
    cte = " ".join(PostgresCliAnalyticsReader._sessions_cte(_flt()).split())

    assert "d.duration_ms" in cte
    assert "AS session_duration_ms" in cte
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -k "duration_ms_fallback or selects_the_duration_ms_fallback_column" -v`
Expected: FAIL — `AssertionError: assert 'coalesce(session_duration_ms' in '...'` (string not found; `_sessions_cte` does not select `d.duration_ms` yet, `get_session_durations` does not coalesce).

- [ ] **Step 3: Write minimal implementation**

In `reader.py`, inside `_sessions_cte` (the `SELECT` list at line 179-188), add the new column after `d.last_event_at`:

```python
                       d.started_at,
                       d.last_event_at,
                       d.duration_ms AS session_duration_ms,
                       coalesce(d.first_prompt, '') AS first_prompt
```

Then change `get_session_durations` (line 462-469) from:

```python
    async def get_session_durations(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
        SELECT session_id, {_ms("last_event_at")} - {_ms("started_at")} AS duration_ms
        FROM sel""",
            window_params(f),
        )
```

to:

```python
    async def get_session_durations(self, f: LocalAnalyticsFilter) -> Rows:
        return await self._q(
            f"""
        WITH {self._sessions_cte(f)}
        SELECT session_id,
               coalesce(session_duration_ms, {_ms("last_event_at")} - {_ms("started_at")}) AS duration_ms
        FROM sel""",
            window_params(f),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -k "duration_ms_fallback or selects_the_duration_ms_fallback_column or test_session_durations_have_no_overlap_check" -v`
Expected: PASS, all three (the pre-existing `test_session_durations_have_no_overlap_check` must still pass — `"FROM sel"` is still present and `"last_event_at >="` is still absent).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/repository/cli_analytics/postgres/reader.py tests/codemie/repository/cli_analytics/postgres/test_reader.py
git commit -m "EPMCDME-15584: Read session_dims.duration_ms fallback in get_session_durations"
```

---

### Task 2: Reader — fix `get_session_detail_meta` to use the same fallback, keep `0` only as last resort

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/reader.py:545-572`
- Test: `tests/codemie/repository/cli_analytics/postgres/test_reader.py`

**Interfaces:**
- Consumes: `_ms`, `_EPOCH` (existing helpers in `reader.py`).
- Produces: `get_session_detail_meta` row `duration_ms` now reflects the same fallback as Task 1; unchanged row shape/keys otherwise.

- [ ] **Step 1: Write the failing test**

Add near `test_session_detail_meta_resolves_the_earliest_service_name` (line ~322):

```python
@pytest.mark.asyncio
async def test_session_detail_meta_prefers_duration_ms_fallback():
    """Session-detail duration must read session_dims.duration_ms before falling back to the hook-pair diff."""
    recorder = _SqlRecorder()
    reader = PostgresCliAnalyticsReader(recorder)

    await reader.get_session_detail_meta("s1")

    sql = " ".join(recorder.statements[0].split())
    assert "coalesce(d.duration_ms" in sql
    assert ", 0)" in sql  # the only-when-truly-missing final fallback stays
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -k prefers_duration_ms_fallback -v`
Expected: FAIL — current SQL has `coalesce({_ms("d.last_event_at")} - {_ms("d.started_at")}, 0)`, no `d.duration_ms` reference, so `"coalesce(d.duration_ms"` is not found.

- [ ] **Step 3: Write minimal implementation**

Change the `duration_ms` line in `get_session_detail_meta` (line 565) from:

```python
               coalesce({_ms("d.last_event_at")} - {_ms("d.started_at")}, 0)          AS duration_ms,
```

to:

```python
               coalesce(d.duration_ms, {_ms("d.last_event_at")} - {_ms("d.started_at")}, 0) AS duration_ms,
```

(`d.duration_ms` is read directly off the `session_dims` row already joined as `d` in this query — no new join needed.)

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -k "prefers_duration_ms_fallback or test_session_detail_meta_resolves_the_earliest_service_name or test_session_detail_meta_service_name_tiebreak_is_deterministic" -v`
Expected: PASS, all three.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/repository/cli_analytics/postgres/reader.py tests/codemie/repository/cli_analytics/postgres/test_reader.py
git commit -m "EPMCDME-15584: Prefer duration_ms fallback in get_session_detail_meta"
```

---

### Task 3: Reader — expose `session_duration_ms` through `get_session_cost_facts`

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/reader.py:482-519`
- Test: `tests/codemie/repository/cli_analytics/postgres/test_reader.py`

**Interfaces:**
- Consumes: `sel.session_duration_ms` (produced in Task 1's `_sessions_cte` change).
- Produces: `get_session_cost_facts` rows now carry a `session_duration_ms` key, consumed by Task 4.

- [ ] **Step 1: Write the failing test**

Add near the cost_daily method tests (after line ~357 `# ── cost_daily methods ──`):

```python
@pytest.mark.asyncio
async def test_session_cost_facts_expose_the_duration_ms_fallback():
    sql, _ = await _sql_of("get_session_cost_facts")

    assert "session_duration_ms" in sql
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -k expose_the_duration_ms_fallback -v`
Expected: FAIL — `get_session_cost_facts` does not currently select `session_duration_ms` anywhere.

- [ ] **Step 3: Write minimal implementation**

In `get_session_cost_facts` (line 500-514), add one line to the `SELECT` list, directly after `max(s.last_event_at) AS last_event_at`:

```python
               max(s.started_at)                            AS started_at,
               max(s.last_event_at)                         AS last_event_at,
               max(s.session_duration_ms)                   AS session_duration_ms,
               {_TOP_MODEL}                                 AS model_name,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -k expose_the_duration_ms_fallback -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/repository/cli_analytics/postgres/reader.py tests/codemie/repository/cli_analytics/postgres/test_reader.py
git commit -m "EPMCDME-15584: Expose session_duration_ms from get_session_cost_facts"
```

---

### Task 4: Handler — use `session_duration_ms` fallback in `get_sessions` (list view)

**Files:**
- Modify: `src/codemie/service/analytics/handlers/cli_analytics_handler.py:613-625`
- Test: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py`

**Interfaces:**
- Consumes: `row.get("session_duration_ms")` (produced in Task 3), `row.get("started_at")`, `row.get("last_event_at")` (existing).
- Produces: `sessions[i]["duration_ms"]` (unchanged key/type — `int`), now preferring the fallback value when present.

- [ ] **Step 1: Write the failing test**

Add near `test_get_sessions_start_time_carries_utc_offset_for_naive_started_at` (line ~432):

```python
@pytest.mark.asyncio
async def test_get_sessions_duration_prefers_the_session_duration_ms_fallback():
    """A cancelled SessionEnd truncates last_event_at close to started_at, but session_duration_ms (rollup fallback)
    still reflects the real session length — the list view must use it, not the truncated hook-pair diff."""
    row = {
        **BASE_COST_ROW,
        "last_event_at": datetime(2026, 1, 1, 0, 0, 5),  # hook pair would compute only 5s
        "session_duration_ms": 128000,  # real session length per rollups.py fallback
    }
    handler = make_handler(cost_facts=[row])
    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    session = data["sessions"][0]
    assert session["duration_ms"] == 128000


@pytest.mark.asyncio
async def test_get_sessions_duration_falls_back_to_hook_pair_without_session_duration_ms():
    """Regression: when the repository row has no session_duration_ms (rollup not yet run), the list view must
    still compute duration from started_at/last_event_at exactly as before."""
    handler = make_handler(cost_facts=[dict(BASE_COST_ROW)])
    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    session = data["sessions"][0]
    assert session["duration_ms"] == 60000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py -k "prefers_the_session_duration_ms_fallback or falls_back_to_hook_pair_without_session_duration_ms" -v`
Expected: first test FAILS (`assert 5000 == 128000`); second test PASSES already (confirms no regression before the change — leave it passing as a pin).

- [ ] **Step 3: Write minimal implementation**

In `cli_analytics_handler.py`, change lines 621-625 from:

```python
            started_at = row.get("started_at")
            last_event = row.get("last_event_at")
            duration_ms = 0
            if isinstance(started_at, datetime) and isinstance(last_event, datetime):
                duration_ms = max(0, int((last_event - started_at).total_seconds() * 1000))
```

to:

```python
            started_at = row.get("started_at")
            last_event = row.get("last_event_at")
            fallback_duration = row.get("session_duration_ms")
            if fallback_duration is not None:
                duration_ms = _i(fallback_duration)
            elif isinstance(started_at, datetime) and isinstance(last_event, datetime):
                duration_ms = max(0, int((last_event - started_at).total_seconds() * 1000))
            else:
                duration_ms = 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py -k "prefers_the_session_duration_ms_fallback or falls_back_to_hook_pair_without_session_duration_ms" -v`
Expected: PASS, both.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/analytics/handlers/cli_analytics_handler.py tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py
git commit -m "EPMCDME-15584: Use session_duration_ms fallback in get_sessions list view"
```

---

### Task 5: Full-suite regression check for the touched modules

**Files:**
- None modified — verification only.

**Interfaces:**
- None.

- [ ] **Step 1: Run the full reader and handler test files**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py tests/codemie/service/analytics/handlers/test_cli_handler.py -v`
Expected: PASS, all tests (confirms Tasks 1-4 did not regress overview KPIs, user avg-duration, session-detail dispatch tests, or any other consumer of these three methods).

- [ ] **Step 2: Commit (only if the run surfaced and required a fix)**

If all green, no commit needed — this task is a checkpoint, not a code change.

---

### Task 6 (2026-10-19): make a non-final summary provisional, not authoritative, for ended_at/duration_ms

**Commit:** not yet committed — pending user review of this worktree's diff.

MR !4532 review (note_2466282, Dmytro_Bondarenko5) found that `session_dims.duration_ms` itself — the
column Tasks 1-4 above made the reader prefer — was still wrong for exactly the case this MR exists to
fix: a cancelled `SessionEnd`. `rollups.py`'s `_COUNTER_JOINS` picks the latest `agent.session.summary`
with no regard for its `is_final` flag, and codemie-code >=0.15.9 sends a non-final summary on every
Stop (`is_final: false`), so a cancelled `SessionEnd` left `duration_ms` cut at the last Stop — the
truncation bug moved one layer down, from the reader (fixed by Tasks 1-4) into `rollups.py` (not
touched by this task until now).

**Files:** `src/codemie/repository/cli_analytics/postgres/rollups.py` — `_IS_FINAL_SUMMARY`,
`_PROVISIONAL_SUMMARY` (new), the `fb` lateral's `WHERE` (now also runs when the latest summary is
provisional, so `fb.last_ts` is available to compare), `_ENDED_AT`/`_ACTIVITY_STARTED_AT`/
`_DURATION_MS` (new, replacing the plain `_summary_or` calls those two `_COUNTER_VALUES` entries used
before: final summary wins outright, provisional summary is compared against `fb.last_ts` and the later
wins). `.ai-run/guides/integration/cli-analytics-storage.md` — documents the `is_final` behavior and
AC3's tolerance.

**History respected, not repeated:** no prior decision on this task or its siblings said `is_final`
could be ignored; this is new column logic the review surfaced, not a re-litigated one.

**Tests:** new live-Postgres file `test_live_session_duration_fallback.py` (3 tests — see `qa-report.md`'s
2026-10-19 addendum for the full RED/GREEN record and the final-summary/repeated-exit regression guards).

**Verification (2026-10-19):** see `qa-report.md`'s addendum of the same date — 3 live tests (RED
confirmed pre-fix, GREEN after), 244 SQL-shape tests, 50 sibling live/handler tests, all passed; `ruff
format`/`ruff check` clean.

## Test-first summary

| Task | Test-first | Failing test |
|---|---|---|
| 1 | yes | `test_session_durations_prefer_duration_ms_fallback` / `test_sessions_cte_selects_the_duration_ms_fallback_column` |
| 2 | yes | `test_session_detail_meta_prefers_duration_ms_fallback` |
| 3 | yes | `test_session_cost_facts_expose_the_duration_ms_fallback` |
| 4 | yes | `test_get_sessions_duration_prefers_the_session_duration_ms_fallback` |
| 5 | no (verification checkpoint, no new code) | n/a |
| 6 | yes | `test_live_a_cancelled_sessionend_with_a_non_final_summary_takes_the_later_of_summary_and_raw_activity` |
