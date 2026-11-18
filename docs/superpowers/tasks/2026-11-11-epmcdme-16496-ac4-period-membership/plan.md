# EPMCDME-16496 (AC4): Tighten CLI Analytics Period-Membership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tighten `_SESSION_ADMITTED_IN_WINDOW` in `postgres/reader.py` so a session is listed and
counted for a period only when it has genuine activity (prompt, API call, tool call) in that
period — not lifecycle hooks, zero-value metric points, or slash-command-synthetic prompts — and
close the `resumed`-session gap (EPMCDME-16993), including for sessions whose stored `has_activity`
was already derived from a zero-value `lines_daily` row before this fix.

**Architecture:** One predicate (`reader.py`), four narrowing edits to its raw/existence probes,
one arm restructuring for the `resumed` gap, plus one companion fix to the stored-fact derivation
(`session_activity.py`) that the live predicate alone cannot close for already-derived sessions —
all read by the single CTE `sel` every dashboard endpoint already shares, so no endpoint-by-endpoint
change is needed. Tests move from SQL-string-shape assertions to semantic (seeded-row) assertions
plus new live-Postgres integration coverage.

**Tech Stack:** Python, asyncpg, PostgreSQL, pytest + pytest-asyncio.

**Spec:** `docs/superpowers/tasks/2026-11-11-epmcdme-16496-ac4-period-membership/spec.md`

Commit per task using the repository's existing convention.

## Global Constraints

- Fact *values* (turns, cost, tool calls, lines per session) stay all-time sums once a session is
  admitted — no per-session fact query body changes.
- No DB migration or new stored column, unless Task 8's benchmark shows the new resumed arm
  regresses the index-only started page/count unacceptably — if so, stop and report, do not add a
  migration unilaterally. Task 2's one-time data correction (resetting `has_activity`/
  `last_activity_day` to NULL for a narrowly-identified population) is a plain `UPDATE` of existing
  columns, not a schema change, and is not what this constraint guards against.
- No per-tool special-casing — every change uses vocabulary (`EventKind`, `SpanKind`,
  `command_name`, `resumed`, `_ACTIVITY_TABLES`) already shared across harnesses.
- Do not change `resumed`'s own stored definition in `session_activity.py` (existence-based, value-
  blind over all five `_ACTIVITY_TABLES`) — Task 2 only narrows `has_activity`/`last_activity_day`;
  Task 4 only changes which arm reads `resumed`/its live equivalent, never what either means.
- Filing the follow-up ticket for the cross-tool hook-coverage gap (Task 3) and for the fact-value-
  clipping limitation is explicitly **not** a task in this plan — both are process actions (create a
  ticket, assign an owner) outside an implementation plan's scope, not code changes. This reasoning
  carries over unchanged from the prior round; it does not become a task here either.

## Review Focus

- A genuinely active, non-resumed session that started in the window keeps unconditional admission
  (no regression to today's sub-day-precise `_STARTED_IN_WINDOW` arm) — Task 4.
- A real, hookless `user_prompt` (blank `command_name`) must still admit on its own — the
  `log_events` narrowing must not drop it alongside the slash-command case — Task 3.
- A non-zero lines point/row still admits; only the zero-value case is excluded, **both** at the
  live-SQL layer and at the stored-fact layer — Task 1 (live) and Task 2 (stored, already-derived
  sessions).
- Raw retention: `_active_in_window`'s partial-day branch needs raw rows that may be purged for an
  old custom window within rollup retention but past raw retention — Task 4's live-resumed split
  must keep the `_has_any_activity` fallback for the non-live-resumed case, not switch the whole
  underived cohort to `_active_in_window`.
- Cross-endpoint agreement (Overview/Sessions/Users/Repositories/Cost/Activity totals) must hold
  after every narrowing, not just for the happy path — Task 7.
- The module must still import: `_SESSION_ADMITTED_IN_WINDOW` (reader.py:259) calls
  `_has_any_activity('d')` → `_activity_exists('d')` at **module-load time**, so every constant that
  function references must already be bound higher up in the file — Task 1, Task 4.

---

### Task 1: Narrow the live zero-value checks (`metric_points`, `lines_daily`) — module-import-safe

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/reader.py:180-218` (new constant,
  `_activity_exists`, `_RAW_ACTIVITY_SOURCES`)
- Test: `tests/codemie/repository/cli_analytics/postgres/test_reader.py`

**Interfaces:**
- Produces: a new module-level constant `_LINES_DAILY_NONZERO`, declared immediately above
  `_activity_exists` (before line 183), qualified to the EXISTS subquery's own alias `ax` — not a
  rename or move of the existing, differently-shaped `_LINES_NONZERO` at `reader.py:369` (that one
  stays exactly where it is; it is unqualified and used by unrelated callers, so reusing it by name
  from `_activity_exists` would require moving it above line 259, where `_has_any_activity('d')`
  already calls `_activity_exists('d')` at import time — defining a new, correctly-scoped constant
  is simpler and carries no risk of breaking `_LINES_NONZERO`'s existing callers).
- `_activity_exists` and `_RAW_ACTIVITY_SOURCES` keep their existing signatures; only their clause
  text changes.

Test-first: yes — `test_activity_exists_requires_a_nonzero_lines_daily_row` and the `metric_points`
value-filter assertion both fail against today's code (no value filter exists yet), and the full
`test_reader.py` file (not a `-k` subset) must collect and pass by Step 4 so no literal elsewhere in
the file is left describing stale SQL.

- [ ] **Step 1: Write the failing tests**
  - Extend `test_sessions_cte_times_a_partial_window_day_by_its_raw_records` (or add a sibling
    assertion for the `metric_points` case) to require `AND r.value <> 0` appended after the
    existing `metric_name = ANY(...)` clause.
  - Add `test_activity_exists_requires_a_nonzero_lines_daily_row`: call `_activity_exists("d")`
    directly and assert the `lines_daily` EXISTS clause contains the **parenthesized** form
    `AND (ax.lines_added <> 0 OR ax.lines_removed <> 0)`, and that the other four tables' clauses do
    not contain it. Parens matter: appended unparenthesized after an `AND`, the OR would bind looser
    than the preceding `ax.session_id = ...` AND and strip the session-id scoping from the EXISTS.
  - Update the test-local `_any_activity` helper (`test_reader.py:422-428`) to add the same
    parenthesized `lines_daily` condition, confirmed against the actual generated SQL (print/diff
    `PostgresCliAnalyticsReader._sessions_cte(_flt())`, not guessed whitespace) — folded into this
    task, not deferred to a later literal-sync task.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -k "metric_points or nonzero_lines_daily" -v`
Expected: FAIL (current SQL has no value filter)

- [ ] **Step 3: Implement**
  - Add `_LINES_DAILY_NONZERO = "ax.lines_added <> 0 OR ax.lines_removed <> 0"` directly above
    `_activity_exists`'s definition (before line 183) — bound in module globals well before line 259
    executes `_has_any_activity('d')` at import time.
  - `_activity_exists(alias, days=None, fenced=False)`: for the `lines_daily` table only, append
    `f" AND ({_LINES_DAILY_NONZERO})"` to that table's EXISTS clause, parenthesized. The other four
    tables keep plain existence.
  - `_RAW_ACTIVITY_SOURCES`'s `metric_points` entry: append `" AND r.value <> 0"` after the existing
    `metric_name = ANY(...)` clause (no OR inside, so no parens hazard here).

- [ ] **Step 4: Run the full file to verify no literal is left stale**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -v`
Expected: PASS (every test in the file, not just the `-k` subset — confirms the module still
imports cleanly and no other pinned literal silently went stale).

- [ ] **Step 5: Commit**

---

### Task 2: Make the stored `has_activity`/`last_activity_day` facts value-aware for `lines_daily`

**Why this task exists (closes a gap Task 1 cannot):** `session_activity.py`'s `DERIVE_SQL` sets
`has_activity` from row **existence** across all five `_ACTIVITY_TABLES`, including `lines_daily`,
with no value check — untouched by Task 1, which only edits the live-computation functions in
`reader.py`. Once a session is derived, Task 4's fast "started, not resumed" arm reads the stored
`{a}.has_activity` column directly, with no call to `_activity_exists`/`_active_in_window` at all —
so a session whose only ever row is a zero-value `lines_daily` row still has `has_activity = true`
and is still admitted, in every window, forever, regardless of Task 1's fix. This is a real gap: an
already-derived session with no real signal, ever, stays listed.

**Decision:** make `has_activity`/`last_activity_day` value-aware for `lines_daily`, while leaving
`resumed`'s own definition exactly as today (value-blind, over all five tables) — this satisfies
the spec's non-goal verbatim ("changing `resumed`'s own stored definition... not fixed here"),
because `resumed` answers a different question ("did this session continue past its start day,
however") that does not need to change for this fix. The alternative (reading a live, per-row
value-aware check for `lines_daily` specifically from inside the "started, not resumed" arm) was
rejected: it would add a per-row EXISTS probe back into the one arm whose entire reason to exist is
staying index-only — the predicate has a documented, measured history of catastrophic scan-cost
regressions at this exact arm (see Task 8), so reintroducing a probe there to avoid touching
`session_activity.py` is the worse trade.

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/session_activity.py:59-85` (`_DAYS`,
  `DERIVE_SQL`'s `facts` CTE)
- Test: `tests/codemie/repository/cli_analytics/postgres/test_session_activity.py`

**Interfaces:**
- Produces: `_DAYS` gains a third projected column, `real` — `true` for every table except
  `lines_daily`, where it is `(lines_added <> 0 OR lines_removed <> 0)`. `facts`'s `last_day`
  becomes `max(d.day) FILTER (WHERE d.real)`; `resumed`'s own expression (`bool_or(...) FILTER
  (WHERE d.day IS NOT NULL)`) is untouched — it still sees every row, `real` or not.
- `derive_session_activity`/`backfill_session_activity` signatures are unchanged; only the SQL text
  of `DERIVE_SQL` changes.

Test-first: yes — a new text-shape assertion on `DERIVE_SQL` fails against today's value-blind
`_DAYS`.

- [ ] **Step 1: Write the failing test**

Add `test_has_activity_ignores_zero_value_lines_daily_rows_but_resumed_stays_value_blind` to
`test_session_activity.py`: assert `DERIVE_SQL` contains a `lines_daily`-specific `real` projection
(`(lines_added <> 0 OR lines_removed <> 0) AS real`) and `max(d.day) FILTER (WHERE d.real)`, and
that the `resumed` expression's own `FILTER (WHERE d.day IS NOT NULL)` clause is present unchanged
and does **not** reference `d.real` — i.e., the test pins both halves of the decision above, not
just the fixed one.

- [ ] **Step 2: Run the test to verify it fails**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_session_activity.py -k has_activity_ignores -v`
Expected: FAIL

- [ ] **Step 3: Implement**
  - Change `_DAYS` to project a third column per table:
    `f"SELECT session_id, day, {'(lines_added <> 0 OR lines_removed <> 0)' if t == 'lines_daily' else 'true'} AS real FROM {t} WHERE session_id = ANY($1::text[])"`.
  - In `facts`, change `max(d.day) AS last_day` to `max(d.day) FILTER (WHERE d.real) AS last_day`.
    Leave the `resumed` expression exactly as is.
  - **One-time data correction (not a migration):** already-derived sessions whose stored
    `has_activity = true` came solely from a zero-value `lines_daily` row are now stale under the
    corrected rule, and the existing periodic re-derive (`maintenance.py:306-314`,
    `PartitionMaintainer._derive_session_activity` → `session_activity.backfill_session_activity`)
    only re-derives sessions whose `has_activity IS NULL` — it will not touch these on its own. Add
    a narrowly-scoped, one-time `UPDATE` (run as an ops/deploy step, not a schema migration — no new
    column, no DDL) that resets exactly the affected population back to NULL so the existing
    maintenance step picks them up on its normal cadence under the now-corrected `DERIVE_SQL`:
    ```sql
    UPDATE session_dims sd
    SET has_activity = NULL, last_activity_day = NULL
    WHERE sd.has_activity
      AND EXISTS (SELECT 1 FROM lines_daily l WHERE l.session_id = sd.session_id)
      AND NOT EXISTS (SELECT 1 FROM turns_daily t WHERE t.session_id = sd.session_id)
      AND NOT EXISTS (SELECT 1 FROM tool_facts_daily tf WHERE tf.session_id = sd.session_id)
      AND NOT EXISTS (SELECT 1 FROM cost_daily c WHERE c.session_id = sd.session_id)
      AND NOT EXISTS (SELECT 1 FROM session_usage u WHERE u.session_id = sd.session_id)
    ```
    Scoping to "has a `lines_daily` row and no row in any other activity table" bounds this to the
    only population that can possibly be wrong, instead of resetting the whole table (which would
    temporarily widen every arm back to the unbounded table-probe fallback for ~10^6 sessions — the
    exact scan-cost regression this predicate has hit before). Commit this statement as a short,
    named one-off script alongside the code change; do not run it inside `DERIVE_SQL` itself or on
    every maintenance pass.

- [ ] **Step 4: Run the test to verify it passes**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_session_activity.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 3: Narrow `log_events`/`spans` raw-activity clauses (command-triggered prompts, `INTERACTION` span)

**Files:**
- Modify: `reader.py:212-218` (`_RAW_ACTIVITY_SOURCES`)
- Test: `tests/codemie/repository/cli_analytics/postgres/test_reader.py`

**Interfaces:**
- Consumes: `_INTERACTION = int(SpanKind.INTERACTION)` (`reader.py:111`); `EventKind.USER_PROMPT`.
- Produces: no signature change; only the `log_events`/`spans` entries' filter strings change.

**Cross-tool scope note (write this down here, where the code actually changes, not only in the
spec's Non-goals):** dropping `INTERACTION` from the spans raw-activity source and relying on the
paired `log_events` row (same `span_id`) instead applies uniformly to every client that writes
through this schema, including Cursor — `vocabulary.py`'s `SPAN_KIND` maps Cursor's
`cursor.interaction` span naming to the same `INTERACTION` kind. Cursor's pairing of that span with
its own `user_prompt` log row (the mechanism this fix relies on to avoid losing coverage) is
**confirmed in research for Claude Code, unconfirmed in this repository for Cursor**. This is the
already-accepted cross-tool follow-up named in the spec's Non-goals, not a new gap introduced here;
it is written down at this task because this is the commit that would surface it if Cursor's pairing
ever turns out to differ.

Test-first: yes — new assertions on the `log_events`/`spans` clause text fail against today's
bare `<> OTHER` filters.

- [ ] **Step 1: Write the failing tests**
  - Extend the `test_sessions_cte_times_a_partial_window_day_by_its_raw_records` parametrization
    (or add new assertions) for `log_events`: the clause must keep `event_kind <>
    {int(EventKind.OTHER)}` AND add `AND (r.event_kind <> {int(EventKind.USER_PROMPT)} OR
    coalesce(btrim(r.command_name), '') = '')`.
  - For `spans`: the clause must become `span_kind <> {int(SpanKind.OTHER)} AND span_kind <>
    {_INTERACTION}` (dropping the bare `<> OTHER`-only form).
  - Update any other test-local literal in this file that reproduces the old `log_events`/`spans`
    clause text (e.g. inside `_any_activity`-adjacent helpers, if any duplicate it) in this same
    step, confirmed against the generated SQL — folded into this task, not deferred.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -k "log_events or spans" -v`
Expected: FAIL

- [ ] **Step 3: Implement**
  - `_RAW_ACTIVITY_SOURCES`'s `log_events` entry: keep `r.event_kind <> {int(EventKind.OTHER)}`,
    append `AND (r.event_kind <> {int(EventKind.USER_PROMPT)} OR coalesce(btrim(r.command_name), '') = '')`.
  - `_RAW_ACTIVITY_SOURCES`'s `spans` entry: change to
    `f" AND r.span_kind <> {int(SpanKind.OTHER)} AND r.span_kind <> {_INTERACTION}"`.

- [ ] **Step 4: Run the full file to verify no literal is left stale**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 4: Close the EPMCDME-16993 `resumed` gap — restructure `_admitted_arms` and give the dead-code single-predicate form an accurate restatement

**Files:**
- Modify: `reader.py:248-301` (add `_live_resumed`, `_SESSION_ADMITTED_IN_WINDOW`, `_admitted_arms`)
- Test: `tests/codemie/repository/cli_analytics/postgres/test_reader.py`

**Interfaces:**
- Produces: a new helper `_live_resumed(alias, fenced=False)`, defined right after
  `_has_any_activity` (before line 259, so it is also safely bound before any module-load-time use)
  — mirrors `session_activity.DERIVE_SQL`'s `resumed` definition ("a row exists on a UTC day other
  than the session's start day"), value-blind like the stored fact it stands in for (consistent with
  Task 2's decision to leave `resumed` itself untouched).
- `_admitted_arms` now returns a 6-tuple (one more arm than today, from splitting today's arm 1);
  callers (`_sessions_cte`, `_sel_ctes`) are unaffected since both already iterate `arms`
  generically.
- `_SESSION_ADMITTED_IN_WINDOW` is confirmed, by grep, to be **dead code at runtime** — never
  interpolated into executed SQL, only quoted by this module's own docstring and
  `_admitted_arms`'s docstring as "the rule" the arms implement equivalently. Rather than leave it a
  stale restatement (adding only `AND NOT d.resumed` to its first disjunct would make it diverge
  from what the live arms actually do for an underived, resumed session, since `d.resumed` is NULL
  for those and the disjunct would short-circuit to NULL, not the live-fallback the arms use), give
  it the same live-fallback treatment via `coalesce`, so it stays an accurate single-predicate
  restatement even though nothing executes it:
    ```python
    _SESSION_ADMITTED_IN_WINDOW = (
        f"((({_STARTED_IN_WINDOW}) AND {_has_any_activity('d')} "
        f"AND NOT coalesce(d.resumed, {_live_resumed('d')})) OR {_active_in_window('d')})"
    )
    ```
    This is a deliberate improvement over the spec's literal fix text (which only adds
    `AND NOT d.resumed`) because the spec itself flags that exact form as inaccurate for the
    underived case — this rewrite resolves that by example rather than leaving a note, and does not
    change any executed query (confirmed dead code).
- **Dependency on Task 2:** arm 1 ("started, not resumed") below still reads the stored
  `{a}.has_activity` column directly, with no live value check. Its correctness for a zero-lines-
  daily-only session therefore depends entirely on Task 2's corrected `DERIVE_SQL` (for newly
  derived sessions) and its one-time backfill (for already-derived ones) — not on anything in this
  task. Do not re-introduce a per-row probe into this arm to "double-check" `has_activity`; that
  defeats the reason the arm is index-only (see Task 2's rejected-alternative note).

Test-first: yes — the new arm-literal and single-predicate assertions below fail against today's
five-arm, `AND NOT d.resumed`-less code.

- [ ] **Step 1: Write the failing tests**
  - `test_sessions_cte_decides_the_window_by_session_start`: update the arm literals
    (`_ARM_STARTED` gains `AND NOT d.resumed`; add `_ARM_STARTED_RESUMED` for the new arm using
    `_active_in_window("d", fenced=True)`; `_ARM_UNDERIVED` changes from reading
    `d.has_activity IS NULL AND {_HAS_ANY_ACTIVITY}` to `d.has_activity IS NULL AND
    (({_LIVE_RESUMED} AND {_ACTIVE_IN_WINDOW_FENCED}) OR (NOT {_LIVE_RESUMED} AND
    {_HAS_ANY_ACTIVITY}))`) and assert all six arms are present in the generated SQL.
  - Add `test_live_resumed_mirrors_the_stored_resumed_definition`: call `_live_resumed("d")`
    directly and assert its clause is "a row in any `_ACTIVITY_TABLES` on a UTC day `<>` the
    session's own start day" for all five tables — the same shape `session_activity.DERIVE_SQL`
    uses for `resumed`, confirmed by comparing the two expressions' structure (the modules cannot
    import each other, so this is a structural parity assertion, the same convention
    `test_session_activity.py`'s existing `test_the_activity_tables_of_the_stored_facts_are_the_ones_of_the_window_rule`
    already uses for `_ACTIVITY_TABLES` itself).
  - Add `test_sessions_cte_single_predicate_restates_the_arms_for_an_underived_resumed_session`:
    assert `_SESSION_ADMITTED_IN_WINDOW` contains `NOT coalesce(d.resumed, {_live_resumed('d')})` as
    part of its first disjunct.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -k "admitted_arms or window_predicate or resumed" -v`
Expected: FAIL

- [ ] **Step 3: Implement**
  - Add `_live_resumed(alias, fenced=False)` after `_has_any_activity`, before line 259.
  - `_admitted_arms`: split today's arm 1 into "started, not resumed" (`{started} AND
    {a}.has_activity AND NOT {a}.resumed`) and a new "started, resumed" arm (`{started} AND
    {a}.has_activity AND {a}.resumed AND {_active_in_window(a, fenced=True)}`).
  - Rewrite today's arm 2 ("started, underived"): `{started} AND {a}.has_activity IS NULL AND
    (({_live_resumed(a, fenced=True)} AND {_active_in_window(a, fenced=True)}) OR (NOT
    {_live_resumed(a, fenced=True)} AND {_has_any_activity(a)}))`. Arms 3-5 (today's "outside" arms)
    stay structurally unchanged; they already call `_active_in_window`/`_has_any_activity`, which
    inherit Tasks 1 and 3's narrowing automatically.
  - `_SESSION_ADMITTED_IN_WINDOW`: as shown above.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 5: Unit test — UTC-midnight-crossing hour presets require an in-window raw record, not just a day row

**Files:**
- Test: `tests/codemie/repository/cli_analytics/postgres/test_reader.py`

**Interfaces:**
- Consumes: `PostgresCliAnalyticsReader._sessions_cte` (existing), `_active_in_window`'s
  partial-day branch (reader.py:232-245, unchanged by this task).

Test-first: yes — `test_hour_preset_crossing_utc_midnight_requires_a_raw_record_for_the_partial_day`
is new; it must pass against the already-correct AND-structure, and must fail loudly if Task 4
accidentally changed that structure.

- [ ] **Step 1: Write the test**

Add `test_hour_preset_crossing_utc_midnight_requires_a_raw_record_for_the_partial_day`: build a
filter whose `$start_dt`/`$end_dt` span `last_6_hours` across a UTC-midnight boundary (two partial
UTC days, no whole day), assert the generated `_sessions_cte` SQL's day-row branch for each partial
day is ANDed with `_raw_activity_in_window`'s raw-table EXISTS (not OR'd, and not satisfied by the
day row alone) — i.e., assert the literal structure `({window_days} AND {raw})`, not
`({window_days} OR {raw})`, appears for both partial days.

- [ ] **Step 2: Run the test**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py::test_hour_preset_crossing_utc_midnight_requires_a_raw_record_for_the_partial_day -v`
Expected: PASS, pinning existing, already-correct behavior. If it FAILS, `_active_in_window`'s
AND/OR structure regressed in Task 4 — fix Task 4's code, not this test.

- [ ] **Step 3: No production code change** — confirmed by Step 2's result.

- [ ] **Step 4: Run the full file once more to confirm no regressions**

Run: `poetry run pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 6: Live-Postgres integration tests — housekeeping-only, exit-time-only, and whole-day zero-lines-only sessions excluded

**Files:**
- Create: `tests/codemie/repository/cli_analytics/postgres/test_live_session_period_membership.py`

**Interfaces:**
- Consumes: `AnalyticsPgEngine`, `PostgresTelemetryIngestor` or direct raw-table INSERTs,
  `RollupRefresher(engine, batch_size=...)` + `.mark_dirty(first_day, last_day, conn)` +
  `.refresh()` (the `_refresh` helper pattern in `test_live_route_pricing.py:146-151`),
  `PostgresCliAnalyticsReader`, following that file's `TEST_LOCAL_PG_URL` skip pattern verbatim.
  Running through the real `RollupRefresher` is deliberate here: it exercises Task 2's corrected
  `DERIVE_SQL`, not just Task 1's live-SQL narrowing.

Test-first: yes — all three tests below fail against pre-Task-1/2/3 code (the housekeeping-only,
exit-time-only, and whole-day-zero-lines sessions would currently be admitted).

- [ ] **Step 1: Write the failing tests** (skip-when-unset; each test deletes only its own session
  rows)
  - `test_lifecycle_hooks_only_session_excluded_from_every_endpoint`: insert only
    `session.start`/`session.env`/`session.end`/`git.snapshot` hook rows for a session whose
    `started_at` is inside the queried window; after refresh, assert the session is absent from
    `get_session_page`, `get_cost_kpis` (both Overview and Cost use), `get_users`,
    `get_repository_sessions`, and does not appear in `get_users_daily_activity`'s/
    `get_class_lists`'s per-day totals for that window.
  - `test_exit_time_only_session_excluded_from_every_endpoint`: insert (a) a zero-value
    `claude_code.lines_of_code.count` metric point, (b) a `log_events` `user_prompt` row with
    non-blank `command_name = 'exit'`, and its paired `INTERACTION` span — no real prompt/API/tool
    row — for a session started inside an hour-preset window; assert the same absence across all six
    endpoint calls as above.
  - `test_whole_day_zero_lines_only_session_excluded_for_last_7_days_and_a_custom_whole_day_window`:
    seed a session with **only** a zero-value `claude_code.lines_of_code.count` metric point (no
    hooks, no other table rows at all), run it through `RollupRefresher` so it becomes a real,
    zero-value `lines_daily` row, then query it with (i) `last_7_days` and (ii) a custom window of
    whole UTC days containing that session's day — assert the session is absent from all six
    endpoints for both window types. This is the production scenario from the Jira repro (a
    zero-delta lines-of-code point admitting a session via the whole-day/day-row branch of
    `_active_in_window`/`_has_any_activity`, not the single-day/exit-time case Task 6's other two
    tests already cover) and is new coverage distinct from them.
  - For all three tests, also assert a genuinely active session (seeded with one real
    `agent.prompt.submit` hook, `log_events` `user_prompt` row with blank `command_name`, and
    matching `usage_requests` row) in the same window IS present on every endpoint, so the test
    proves exclusion, not an empty-window false negative.

- [ ] **Step 2: Run tests to verify they fail against the pre-fix behavior**

Run: `TEST_LOCAL_PG_URL=<url> poetry run pytest -n0 tests/codemie/repository/cli_analytics/postgres/test_live_session_period_membership.py -v`
Expected: FAIL before Tasks 1-3 are in place (if run only after Tasks 1-4 are already committed,
confirm the fail-first property by temporarily reverting one clause locally, not by assertion, and
note the result here).

- [ ] **Step 3: No further production code change** — this task proves Tasks 1-3's fix end-to-end;
  if a test fails here after Tasks 1-4 are merged, treat it as a regression in those tasks, not a
  gap to patch locally.

- [ ] **Step 4: Run tests to verify they pass**

Run: `TEST_LOCAL_PG_URL=<url> poetry run pytest -n0 tests/codemie/repository/cli_analytics/postgres/test_live_session_period_membership.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 7: Live-Postgres integration test — `resumed` session with housekeeping-only activity in the queried window, and cross-endpoint total agreement

**Files:**
- Modify: `tests/codemie/repository/cli_analytics/postgres/test_live_session_period_membership.py`
  (created in Task 6)

**Interfaces:**
- Consumes: same as Task 6.

Test-first: yes, **but only for the "started inside the window" half** — tracing today's unmodified
arm 1 (`_STARTED_IN_WINDOW AND has_activity`, with no `resumed` check at all) shows it admits a
resumed session unconditionally once `started_at` lands in the window, so that half genuinely fails
against pre-Task-4 code. The "started outside the window" half already goes through today's arm 4
(`outside AND resumed AND last_activity_day >= start AND _active_in_window(fenced)`), which already
requires a genuine day-row/raw record and is untouched by any lifecycle-hook write — it is already
correct against unmodified main and is kept here as a **non-regression guard**, not new coverage; do
not expect it to fail at Step 2. The cross-endpoint contract test is also a non-regression guard:
the totals already agree today (structural, by construction — every endpoint reads the same `sel`),
so this test protects that property against Tasks 1-4 breaking it, it does not newly pass because of
this ticket's fix.

- [ ] **Step 1: Write the tests**
  - `test_resumed_session_with_housekeeping_only_day_excluded_both_when_started_inside_and_outside_the_window`:
    two sessions, each with real activity (`agent.prompt.submit` + matching rows) on one UTC day and
    only lifecycle-hook rows on a second UTC day that falls inside the queried window — one session's
    `started_at` lies inside the window (the new, previously-failing case), the other's lies outside
    it (the regression-guard case). Assert both are excluded from all six endpoints for that window,
    and both are included when the window covers their real-activity day instead.
  - `test_cross_endpoint_session_totals_agree_for_the_same_filter`: for one filter (reuse the fixture
    sessions already seeded above, mixing genuinely-active, housekeeping-only, and exit-time-only
    sessions), assert `get_cost_kpis(...).total_sessions` == `get_session_page(...)` total ==
    `sum(u.session_count for u in get_users(...))` == `sum(r.session_count for r in
    get_repository_sessions(...))` == `get_cost_kpis(...)` (Cost view) `.total_sessions` ==
    `sum(a.session_count for a in get_users_daily_activity(...))` (adjust field names to the port's
    actual return shape, confirmed by reading `ports.py` before writing the assertion).

- [ ] **Step 2: Run tests, distinguishing the two halves**

Run: `TEST_LOCAL_PG_URL=<url> poetry run pytest -n0 tests/codemie/repository/cli_analytics/postgres/test_live_session_period_membership.py -v`
Expected (before Task 4 is in place): the "started inside the window" assertion FAILs; the "started
outside the window" assertion and the cross-endpoint contract test already PASS. If either of the
latter two unexpectedly fails against unmodified main, that is itself a finding to report, not a
pre-existing-fail to code around.

- [ ] **Step 3: No further production code change** — this task proves Task 4's fix and the
  structural cross-endpoint-agreement property named in the technical analysis; if the totals
  disagree, or the "started outside" case starts failing, after Task 4 is merged, that is a Task 4
  regression.

- [ ] **Step 4: Run tests to verify they all pass**

Run: `TEST_LOCAL_PG_URL=<url> poetry run pytest -n0 tests/codemie/repository/cli_analytics/postgres/test_live_session_period_membership.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

---

### Task 8: Benchmark the new resumed-arm cost and update the storage guide

**Files:**
- Modify: `.ai-run/guides/integration/cli-analytics-storage.md` ("Window Semantics" and "Scan cost
  of the admission rule" sections)

**Interfaces:**
- Consumes: `scripts/cli_analytics_bench/run_sessions_bench.py` (existing, unmodified).

Test-first: no — this task is a measurement and a documentation update, not new behavior; no test
pins prose.

- [ ] **Step 1:** Run `scripts/cli_analytics_bench/run_sessions_bench.py` (or an `EXPLAIN (ANALYZE,
  BUFFERS)` of the default-sorted session page/count statement) against a `resumed`-population
  sized at production scale, isolating the new "started, resumed" arm's cost from Task 4.
  - **If production-scale data cannot be obtained** (no production-like dataset reachable from the
    benchmark environment): do not skip this step silently. Either (a) scale the synthetic
    `resumed` population up by a documented multiplier (e.g. 100x the largest population measured in
    dev) and extrapolate the per-row cost using the same scaling method the guide's existing 2026-11-06
    entry used, recording the multiplier and the extrapolation method next to the numbers, or
    (b) defer the benchmark with a dated, explicit TODO in the guide and treat the Non-goals'
    migration fallback as provisionally adopted (i.e., assume the regression is real) until the real
    measurement lands — never merge with the benchmark simply absent and no note explaining why.

- [ ] **Step 2:** Record the measured (or extrapolated/deferred, per Step 1's fallback) numbers in
  `cli-analytics-storage.md`'s "Scan cost of the admission rule" section, dated, next to the
  existing 2026-11-06 entry.

- [ ] **Step 3:** If the measurement shows an unacceptable regression on the index-only started
  page/count, stop and report back rather than adding a migration — the Global Constraints section
  names the `session_dims_active_shared_cov` `INCLUDE`-list extension as the fallback, decided from
  this number, not assumed safe in advance.

- [ ] **Step 4:** Update "Window Semantics" to describe the corrected rule: the narrowed
  `metric_points`/`lines_daily` probes (Task 1), the value-aware stored `has_activity`/
  `last_activity_day` facts and the one-time backfill (Task 2), the narrowed `log_events`/`spans`
  probes (Task 3), and the six-arm `resumed`-aware admission (Task 4), replacing the outdated
  five-arm description.

- [ ] **Step 5: Commit**

---

## Self-Review

**1. Spec coverage:** Metric points live narrowing (Task 1) · module-import-safety + parens fix
(Task 1) · `lines_daily` live existence narrowing (Task 1) · stored `has_activity`/
`last_activity_day` value-awareness + backfill (Task 2) · `log_events` command_name / `spans`
`INTERACTION` drop + cross-tool note (Task 3) · `resumed` gap / six-arm restructuring +
dead-code-accurate single predicate (Task 4) · UTC-midnight hour-preset unit test (Task 5) ·
lifecycle-hooks-only, exit-time-only, and whole-day-zero-lines-only live tests (Task 6) ·
resumed-housekeeping-only live test (new + regression-guard halves) and cross-endpoint contract test
(Task 7) · benchmark (with no-data fallback) + doc update (Task 8). All eight Acceptance Criteria
and all required Testing-section coverage are each owned by exactly one task; the literal-test
maintenance that the prior round isolated into its own task is now folded into Tasks 1-3 themselves.

**2. Step scan:** Each implementation step names the exact clause/arm/column change and the exact
constant or helper to add (`_LINES_DAILY_NONZERO`, `_live_resumed`, the `real` column); no step says
"handle edge cases" without naming the case.

**3. Type consistency:** `_admitted_arms` stays a tuple of SQL-string SELECTs consumed by
`_sel_ctes`/`_sessions_cte` unchanged in Task 4 (6-tuple instead of 5-tuple, same element type) —
no caller signature changes required. `_activity_exists`'s new per-table condition and
`DERIVE_SQL`'s new `real` column are both additive; no existing caller's signature changes.

**4. Review Focus:** covered by Task 4 (non-resumed regression, module-import safety), Task 3
(hookless prompt, cross-tool note), Task 1 + Task 2 (nonzero still admits, at both the live and
stored layers), Task 4 (raw-retention fallback kept for non-live-resumed underived), Task 7
(cross-endpoint agreement).

**5. Proportion:** ~17 KB for an 8-task plan touching two files' admission predicate and its stored
cache, plus one new live-test file and a doc update, across a change with a documented, measured
history of scan-cost regressions and a prior gate rejection — the size tracks the number of distinct
blocking findings addressed (module-import hazard, missing parens, a derived-cache gap requiring a
companion file and a data-correction step, a dead-code accuracy fix, a missing test scenario, and a
mis-described regression-guard), not padding; fenced code blocks are limited to symbols that do not
exist yet (`_LINES_DAILY_NONZERO`, the `_DAYS`/`facts` change, the one-time `UPDATE`, `_live_resumed`
and `_SESSION_ADMITTED_IN_WINDOW`'s new form) and stay under 40% of the document.

## Negative-Constraint Pass

- "Fact values... should remain all-time sums... only membership changes" — no task in this plan
  edits a per-session fact query body (`get_turns_by_session`, `get_lines_by_session`, etc.); Tasks
  1-2-3-4 touch only `_RAW_ACTIVITY_SOURCES`/`_activity_exists`/`DERIVE_SQL`/`_admitted_arms`,
  upstream of `sel`.
- "No DB migration or new stored column" (unless the benchmark forces it) — Task 2's one-time
  `UPDATE` writes to existing columns, with no DDL and no new column; Task 8 explicitly stops and
  reports rather than adding a migration if the benchmark says otherwise; no task adds a migration
  file.
- "No per-tool special-casing" — Tasks 1-2-3-4 all narrow using vocabulary (`EventKind`, `SpanKind`,
  `command_name`, `resumed`, `_ACTIVITY_TABLES`) already shared across harnesses; no task branches
  on `platform` or a harness name. Task 3 documents, rather than works around, the one place this
  vocabulary's cross-tool coverage is unconfirmed (Cursor's `INTERACTION`/`user_prompt` pairing).
- "Changing `resumed`'s own stored definition... not fixed here" — Task 2 changes only
  `has_activity`/`last_activity_day`'s derivation (value-aware for `lines_daily`); the `resumed`
  expression inside `DERIVE_SQL` is explicitly left untouched, pinned by Task 2's own test. Task 4
  only changes which arm reads `resumed`/`_live_resumed` live vs. stored, never what either means.
- "Guaranteeing period-membership correctness for a CLI tool whose hook pipeline does not cover the
  kinds this fix narrows... Deferred to a tracked follow-up, not fixed here" — no task in this plan
  adds cross-tool hook-coverage verification; Task 3 documents the gap at the point of change but
  does not attempt to close it. This plan does not file the follow-up ticket either (that is a
  process action outside an implementation plan's scope, not a code task — see Global Constraints).
- negative-constraints: the spec's Non-goals section (fact-value clipping, migrations, per-tool
  casing, `resumed`'s own definition, cross-tool guarantee) is the source for every bullet above;
  none were found unaddressed.
