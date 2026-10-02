# CLI Analytics Timestamp UTC-Offset Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `_iso()` in the backend CLI Analytics handler always emit a timezone-aware ISO 8601 string, so every CLI Analytics timestamp field is unambiguous to the frontend regardless of which repository driver returned a naive `datetime`.

**Architecture:** Single-function fix in a shared serialization helper (`_iso`) used by all CLI Analytics timestamp fields; no call site changes, no frontend changes. Add unit tests pinning the helper's three input shapes (naive, aware, date) plus one endpoint-level test proving the fix reaches a real response field.

**Tech Stack:** Python, pytest (`pytest-asyncio`), Poetry — repository is `C:/Projects/codemie-dev/codemie` (a sibling of this task's `codemie-ui` working directory; all paths below are absolute for that reason).

**Spec:** `C:/Projects/codemie-dev/codemie-ui/docs/superpowers/tasks/2026-10-01-cli-analytics-sessions-timezone-filter/spec.md`

**Note on working directory:** This plan's task_dir (`codemie-ui/docs/...`) is not the repo the code lives in. Every file path below is absolute under `C:/Projects/codemie-dev/codemie/`. Every test-run command below is explicitly prefixed `cd C:/Projects/codemie-dev/codemie &&` — do not run them from `codemie-ui`, and do not substitute `npm`/`vitest` for `poetry`/`pytest`.

## Global Constraints

- `_iso()` keeps its existing signature (`_iso(value: Any) -> str`) and every existing call site (lines 259, 264, 352, 376, 551, 641, 677) untouched — fix the helper only.
- No new abstraction, wrapper type, or date utility — the fix is the four-line body change the spec shows.
- No frontend file (`codemie-ui/src/components/form/DatePicker/DatePicker.tsx`, `codemie-ui/src/pages/analytics/components/AnalyticsFilters.tsx`, `codemie-ui/src/utils/helpers.ts`) is modified.
- No change to which repository driver is active, and no attempt to fix the driver itself.
- No change to `TimeParser`/`FilterParams` (`codemie/src/codemie/rest_api/routers/cli_analytics.py`, `codemie/src/codemie/service/analytics/time_parser.py`).
- Commit per task using the repository's existing convention: `EPMCDME-15437: <description>`, committed from inside `C:/Projects/codemie-dev/codemie`.

## Review Focus

- A non-UTC aware `datetime` (e.g. `+02:00`) must pass through with its own offset, not get re-converted to UTC — Task 1, test 2.
- A plain `date` value (day-bucket call sites at lines 259/264/352) must stay unaffected by the datetime-only branch — Task 1, test 3.
- `None` (a value `_iso` can still receive at call sites that don't pre-guard it, e.g. `r.get("timestamp")`) must keep falling through to `_s(value)` → `""`, unchanged by this fix — Task 1, test 4.
- Microsecond precision must survive attaching `tzinfo=timezone.utc` to a naive value, not get truncated or rounded — Task 1, test 1 (asserts the full microsecond value).
- The fix must be provable at a real response field, not just at the helper in isolation — a mocked repository returning a naive `started_at` must produce a session JSON `start_time` with an explicit offset — Task 2.

---

### Task 1: Fix `_iso()` to attach UTC to naive datetimes, with unit tests

**Files:**
- Modify: `C:/Projects/codemie-dev/codemie/src/codemie/service/analytics/handlers/cli_analytics_handler.py:87-90`
- Modify: `C:/Projects/codemie-dev/codemie/tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py:16,20` (imports) and append new tests after line 307

**Interfaces:**
- Consumes: nothing new — `timezone` is already imported at `cli_analytics_handler.py:33`.
- Produces: `_iso(value: Any) -> str` with corrected behavior; Task 2 imports and relies on this same function via `get_sessions`.

Test-first: yes — `_iso()` on a naive `datetime` currently returns a string with no UTC offset; the new tests fail against today's implementation.

- [ ] **Step 1: Write the failing unit tests**

In `test_cli_analytics_handler.py`, change line 16 from `from datetime import datetime` to:
```python
from datetime import date, datetime, timedelta, timezone
```
Change line 20 to also import `_iso`:
```python
from codemie.service.analytics.handlers.cli_analytics_handler import LocalAnalyticsHandler, _iso
```
Append after line 307:
```python
def test_iso_attaches_utc_to_naive_datetime():
    """A naive datetime must serialize with an explicit UTC offset, same wall-clock value."""
    result = _iso(datetime(2026, 9, 30, 12, 28, 8, 937211))
    assert result == "2026-09-30T12:28:08.937211+00:00"


def test_iso_leaves_aware_datetime_unchanged():
    """An already-aware datetime (including a non-UTC offset) must not be re-converted."""
    aware = datetime(2026, 9, 30, 14, 28, 8, tzinfo=timezone(timedelta(hours=2)))
    result = _iso(aware)
    assert result == aware.isoformat() == "2026-09-30T14:28:08+02:00"


def test_iso_date_passthrough_unaffected():
    """A plain date value (day buckets) has no time-of-day — it must serialize unchanged."""
    assert _iso(date(2026, 9, 30)) == "2026-09-30"


def test_iso_none_still_returns_empty_string():
    """None must keep falling through to the non-datetime/date branch, unchanged by this fix."""
    assert _iso(None) == ""
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `cd C:/Projects/codemie-dev/codemie && poetry run pytest tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py -k test_iso_ -v`
Expected: `test_iso_attaches_utc_to_naive_datetime` FAILS (missing offset); the other three PASS already (confirms they pin pre-existing behavior, not just the fix).

- [ ] **Step 3: Implement the fix**

Replace `cli_analytics_handler.py:87-90`:
```python
def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return _s(value)
```
(`datetime` is checked before `date` because `datetime` is itself a `date` subclass; order matters.)

- [ ] **Step 4: Run the tests to verify they all pass**

Run: `cd C:/Projects/codemie-dev/codemie && poetry run pytest tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py -k test_iso_ -v`
Expected: all four PASS.

- [ ] **Step 5: Commit**

```bash
cd C:/Projects/codemie-dev/codemie
git add src/codemie/service/analytics/handlers/cli_analytics_handler.py tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py
git commit -m "EPMCDME-15437: Attach UTC to naive datetimes in cli_analytics_handler._iso"
```

---

### Task 2: Prove the fix reaches a real endpoint field (sessions list)

**Files:**
- Modify: `C:/Projects/codemie-dev/codemie/tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py` (append one test after the tests added in Task 1)

**Interfaces:**
- Consumes: `make_handler()`, `make_filter()`, `BASE_COST_ROW` (existing fixtures at lines 23-50 — `BASE_COST_ROW["started_at"]` is already a naive `datetime(2026, 1, 1, 0, 0, 0)`) and `LocalAnalyticsHandler.get_sessions`, which serializes `started_at` into `session["start_time"]` via `_iso` at `cli_analytics_handler.py:551`. Relies on Task 1's fixed `_iso`.
- Produces: nothing consumed by later tasks — this is the plan's last task.

Test-first: yes — against the pre-fix `_iso`, this test fails because `start_time` has no offset; it passes once Task 1 is committed, and stays in the suite as the endpoint-level regression pin required by the spec.

- [ ] **Step 1: Write the test**

```python
@pytest.mark.asyncio
async def test_get_sessions_start_time_carries_utc_offset_for_naive_started_at():
    """Sessions-list start_time must carry an explicit UTC offset even when the repository returns a naive started_at."""
    row = {**BASE_COST_ROW, "repository": "my-repo", "branch": "main"}
    handler = make_handler(cost_facts=[row])
    data, _, _ = await handler.get_sessions(make_filter(), page=0, per_page=20, sort_by="start_time", search=None)
    session = data["sessions"][0]
    assert session["start_time"] == "2026-01-01T00:00:00+00:00"
```

- [ ] **Step 2: Run it against the current (already-fixed) code to verify it passes**

Run: `cd C:/Projects/codemie-dev/codemie && poetry run pytest tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py -k test_get_sessions_start_time_carries_utc_offset -v`
Expected: PASS (Task 1's fix is already in place; this step proves the fix is visible at the real call site, not just at the helper).

- [ ] **Step 3: Run the full handler test file to confirm no regression**

Run: `cd C:/Projects/codemie-dev/codemie && poetry run pytest tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py -v`
Expected: all tests PASS, including the four from Task 1 and this one.

- [ ] **Step 4: Commit**

```bash
cd C:/Projects/codemie-dev/codemie
git add tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py
git commit -m "EPMCDME-15437: Add endpoint-level regression test for sessions start_time UTC offset"
```

---

## Negative-constraint pass

- "No frontend file is modified" — no task touches `codemie-ui/`; both tasks' Files sections are scoped entirely to `codemie/`.
- "No new abstraction, wrapper type, or date utility" — Task 1 Step 3 is the exact four-line body the spec shows; no new function, class, or module is introduced.
- "`_iso` keeps its existing signature and call sites untouched" — Task 1 modifies only the function body (lines 87-90); none of the seven call sites (259, 264, 352, 376, 551, 641, 677) are edited.
- "No change to which repository driver is active, and no attempt to fix the driver itself" — no task touches any repository/driver file (`postgres/otlp.py`, ClickHouse reader, etc.).
- "No change to `TimeParser`/`FilterParams`" — no task touches `cli_analytics.py` (router) or `time_parser.py`.
- No requirements source came in inline for this plan (a spec arrived), so no separate Acceptance-criteria section was added per the planner's own rule; the spec's existing Acceptance criteria section is the record of done.
