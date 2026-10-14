# EPMCDME-15583: Rejected/Failed Write-Edit Counted as Files Written Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A user-rejected or failed Write/Edit must not inflate `files_written`/`files_edited`/`files_changed`, and a session with no genuinely successful file modification must still be classified as a Dead Session with its cost counted as Wasted.

**Architecture:** Two independent SQL-generation fixes in the `codemie` backend's CLI-analytics Postgres layer: (1) `rollups.py`'s `session_files_daily` rollup already has an uncommitted, independently-verified-correct fix gating `is_written`/`is_edited` on actual execution success — this task locks it in with a test rather than re-deriving it; (2) `reader.py`'s `get_file_facts_by_session()` still computes `files_changed` without regard to those flags, which this plan fixes with a one-line `FILTER` clause, reusing an expression that already exists elsewhere in `rollups.py`. A third task adds direct unit coverage of `_dead_sessions()`'s classification behavior.

**Tech Stack:** Python, PostgreSQL (SQL built as f-strings, no ORM), pytest + pytest-asyncio. No live-Postgres test harness exists in this repo — every existing DB-layer test asserts against the generated SQL text via a fake connection/recorder, never executes it. All new tests follow that same SQL-string-assertion convention.

**Spec:** `docs/superpowers/tasks/2026-10-06-rejected-write-counted-as-files-written/spec.md`

## Global Constraints

- No live-Postgres test harness exists in this repo — all new tests assert against generated SQL text (string/substring assertions), never execute real queries. Do not introduce new DB-execution test infrastructure.
- Do not touch EPMCDME-15448 (Read-only tools also inflating `files_changed`) — same code area, separate ticket. The Task 2 fix incidentally also excludes Read-only rows as a side effect, but no code or test here should be framed around that ticket.
- Do not modify the unrelated local-dev-environment changes already present on this branch (`.env.example`, `config/customer/customer-config.yaml`, `docker-compose.yml`).
- Commit messages use `EPMCDME-15583: <description>` per `.ai-run/guides/standards/git-workflow.md`.
- Do not change `files_written`/`files_edited` in `reader.py`'s `fl` CTE — they already correctly use `count(*) FILTER (WHERE written)` / `(WHERE edited)`.

## Review Focus

- **Rejected Write/Edit** (permission denied: no execution span exists at all) — must not set `is_written`/`is_edited`. Covered by Task 1's assertion that both branches require an explicit success/completion signal (absence of one, not just a `'false'` value, must fail the gate).
- **Failed Write/Edit** (execution span exists with `success = 'false'`) — must not set `is_written`/`is_edited`. Covered by Task 1's assertion that the span branch requires `e.success = 'true'` literally (not merely "a success column exists").
- **A session where every file touch is rejected/failed** (`files_changed` ends up `0`) — must be classified as a Dead Session with its cost as Wasted. Covered by Task 3.
- **A session with at least one genuinely successful Write/Edit** (`files_changed >= 1`) — must NOT be classified dead, even if other calls in the same session were rejected/failed. Covered by Task 3.
- **`files_changed`'s new FILTER must not regress the already-correct `files_written`/`files_edited` columns in the same CTE** — Task 2's test asserts the exact `files_changed` SQL text without touching the other two, and running the full existing `test_file_facts_scope_both_branches_without_a_window` / `test_fact_methods_have_no_day_window_and_stay_scoped` tests (unmodified) after the fix catches any accidental widening of the change.

---

### Task 1: Lock in `session_files_daily`'s execution-success gating

**Files:**
- Modify (verify only, no code change expected): `src/codemie/repository/cli_analytics/postgres/rollups.py` (the already-uncommitted diff in the `session_files_daily` `_replace_daily(...)` call, ~line 1517)
- Test: `tests/codemie/repository/cli_analytics/postgres/test_rollups.py`

**Interfaces:**
- Consumes: `rollups.RECOMPUTE` (module-level list of SQL statement strings), `rollups._SPANS`, `rollups._EXEC` (both already-defined module constants), and the test file's existing local helpers `_target_table(statement: str) -> str` and `_gate_bits(statement: str) -> int` (both already defined in `test_rollups.py`).
- Produces: nothing new consumed by later tasks — this task only adds test coverage for existing (uncommitted) production code.

- [ ] **Step 1: Write the failing test**

Add to `tests/codemie/repository/cli_analytics/postgres/test_rollups.py` (near the existing `test_span_facts_statements_read_hooks_for_pairs_without_tool_or_interaction_spans` test):

```python
def test_session_files_daily_gates_is_written_is_edited_on_success() -> None:
    """is_written/is_edited require a CONFIRMED successful call, never just the call itself: a
    denied or failed Write/Edit must not count as a file touched (EPMCDME-15583). Row contents
    are proven live (Task 17); this locks the generated SQL's success-gating conditions."""
    inserts = {
        _target_table(s): s
        for s in RECOMPUTE
        if s.lstrip().startswith("INSERT INTO") and _gate_bits(s) & rollups._SPANS
    }
    files = inserts["session_files_daily"]
    # Span branch: only a matching execution span with success = 'true' counts, not a bare call.
    assert files.count("AND e.success = 'true'") == 2
    assert f"x.span_kind = {rollups._EXEC} AND x.tool_use_id = s.tool_use_id" in files
    # Hook-fallback branch: only agent.tool.end (never the bare agent.tool.start) counts.
    assert files.count("AND e.ended") == 2
    assert "x.event_type = 'agent.tool.end' AND x.tool_use_id = h.tool_use_id" in files
```

- [ ] **Step 2: Run the test against the code WITHOUT the candidate fix, to prove it is a real failing test**

The candidate fix is already present uncommitted in the working tree, so first stash only that file to see the test fail against the pre-fix baseline:

```bash
git stash push -- src/codemie/repository/cli_analytics/postgres/rollups.py
pytest tests/codemie/repository/cli_analytics/postgres/test_rollups.py::test_session_files_daily_gates_is_written_is_edited_on_success -v
```

Expected: FAIL (the pre-fix SQL has no `e.success = 'true'` / `e.ended` conditions in the `session_files_daily` INSERT).

- [ ] **Step 3: Restore the candidate fix**

```bash
git stash pop
```

- [ ] **Step 4: Run the test again to verify it passes**

Run: `pytest tests/codemie/repository/cli_analytics/postgres/test_rollups.py::test_session_files_daily_gates_is_written_is_edited_on_success -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/repository/cli_analytics/postgres/rollups.py tests/codemie/repository/cli_analytics/postgres/test_rollups.py
git commit -m "EPMCDME-15583: gate session_files_daily is_written/is_edited on execution success"
```

---

### Task 2: Fix `files_changed` to require a successful Write or Edit

**Files:**
- Modify: `src/codemie/repository/cli_analytics/postgres/reader.py:379` (the `fl` CTE inside `get_file_facts_by_session`)
- Test: `tests/codemie/repository/cli_analytics/postgres/test_reader.py`

**Interfaces:**
- Consumes: `PostgresCliAnalyticsReader.get_file_facts_by_session` (existing method, unchanged signature), the test file's existing `_sql_of(method: str) -> tuple[str, list]` async helper.
- Produces: nothing new consumed by later tasks.

- [ ] **Step 1: Write the failing test**

Add to `tests/codemie/repository/cli_analytics/postgres/test_reader.py` (near the existing `test_file_facts_scope_both_branches_without_a_window` test):

```python
@pytest.mark.asyncio
async def test_file_facts_changed_only_counts_written_or_edited_files():
    """files_changed must not count a file whose only touches were reads, or a rejected/failed
    Write/Edit — only a file with at least one successful Write or Edit counts (EPMCDME-15583)."""
    sql, _ = await _sql_of("get_file_facts_by_session")

    assert "count(*) FILTER (WHERE written OR edited) AS files_changed" in sql
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py::test_file_facts_changed_only_counts_written_or_edited_files -v`
Expected: FAIL (current SQL has `count(*) AS files_changed` with no `FILTER`)

- [ ] **Step 3: Apply the fix in `reader.py`**

In `get_file_facts_by_session`'s `fl` CTE, change:

```python
                   count(*)                       AS files_changed,
```

to:

```python
                   count(*) FILTER (WHERE written OR edited) AS files_changed,
```

(`written`/`edited` are the `per_file` CTE's existing `bool_or(is_written) AS written, bool_or(is_edited) AS edited` aliases, already in scope one CTE up — no other line in this CTE changes.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py::test_file_facts_changed_only_counts_written_or_edited_files -v`
Expected: PASS

- [ ] **Step 5: Run the full existing file-facts tests to confirm no regression**

Run: `pytest tests/codemie/repository/cli_analytics/postgres/test_reader.py -k file_facts -v`
Expected: all PASS (`test_fact_methods_have_no_day_window_and_stay_scoped[get_file_facts_by_session]` and `test_file_facts_scope_both_branches_without_a_window` unaffected)

- [ ] **Step 6: Commit**

```bash
git add src/codemie/repository/cli_analytics/postgres/reader.py tests/codemie/repository/cli_analytics/postgres/test_reader.py
git commit -m "EPMCDME-15583: files_changed only counts files with a successful write or edit"
```

---

### Task 3: Pin down Dead Session / Wasted Cost classification behavior

**Files:**
- Test: `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py`
- No production code change expected — `LocalAnalyticsHandler._dead_sessions()`'s own logic is already correct; it was simply uncovered by any test, and depends on Tasks 1-2 for its *inputs* (`files_changed`) to be correct.

**Interfaces:**
- Consumes: `LocalAnalyticsHandler._dead_sessions(cost_facts: list[dict], files_by_session: dict[str, dict]) -> tuple[int, float]` (existing static method, already imported module has `LocalAnalyticsHandler` imported in this test file).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the test (expected to pass immediately — this is a characterization test for already-correct logic that depends on Tasks 1-2's inputs)**

Add to `tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` (near the other small focused unit tests, e.g. after `test_agent_dispatch_uses_tool_execution_duration_ms`):

```python
def test_dead_sessions_counts_a_session_with_no_successful_file_change_as_dead():
    """A priced session whose only file touches were rejected/failed (files_changed == 0, once
    rollups.py/reader.py gate it on success) must be classified dead with its cost wasted
    (EPMCDME-15583)."""
    cost_facts = [{"session_id": "s1", "cost_usd": 2.0}]
    files_by_session = {"s1": {"files_changed": 0}}
    dead, wasted = LocalAnalyticsHandler._dead_sessions(cost_facts, files_by_session)
    assert dead == 1
    assert wasted == 2.0


def test_dead_sessions_excludes_a_session_with_a_successful_file_change():
    """A priced session with at least one genuinely successful Write/Edit (files_changed == 1)
    must not be classified dead, even though other calls in the session may have been rejected
    or failed."""
    cost_facts = [{"session_id": "s1", "cost_usd": 2.0}]
    files_by_session = {"s1": {"files_changed": 1}}
    dead, wasted = LocalAnalyticsHandler._dead_sessions(cost_facts, files_by_session)
    assert dead == 0
    assert wasted == 0.0
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `pytest tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py -k dead_sessions -v`
Expected: PASS (both). If either fails, `_dead_sessions()` itself has a bug unrelated to Tasks 1-2 — stop and investigate before proceeding; do not weaken the test.

- [ ] **Step 3: Commit**

```bash
git add tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py
git commit -m "EPMCDME-15583: add direct coverage of dead-session classification by files_changed"
```
