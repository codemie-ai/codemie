# Normalize Naive Datetimes to UTC in CLI Analytics Date Filters — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Naive `start_date`/`end_date` query params on CLI Analytics endpoints are normalized to UTC instead of crashing with HTTP 500; reversed ranges and malformed strings still return 400/422.

**Architecture:** One shared `to_utc()` helper (promoted from the existing private `_to_utc` in `datetime_utils.py`) is applied at each of the five acceptance points named in the spec — the `TimeParser` utility backstop, the `AnalyticsFilterParams` Pydantic model, the OTel `FilterParams` class, and the two inline-param endpoints (`get_cli_summary`, `get_projects_unique_daily`) — plus one bug fix (raw `HTTPException` → `ExtendedHTTPException` in `get_projects_unique_daily`'s reversed-range check).

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest/pytest-asyncio.

## Global Constraints

- Naive datetimes are normalized to UTC (`tzinfo` attached), never rejected with 400 for merely lacking an offset.
- Reversed ranges (`start_date` after `end_date`) and malformed strings still return 400/422; HTTP 500 is never returned for any date input.
- `TimeParser._parse_period` / predefined `time_period` behavior is unchanged — out of scope.
- No query-parameter name/type changes; no OpenAPI schema changes (`tests/codemie/rest_api/router_guards/`).
- Do not edit `usage.py`, `usage_attribution.py`, `cli_insights.py`, `activity_events_router.py` — protected transitively via the `TimeParser` fix (Task 1).
- Do not broaden `handle_analytics_errors` to catch all `HTTPException` subclasses — only the one raw-`HTTPException` raise site in `activity.py` is touched.
- Commit per task using the repository's existing convention.

---

### Task 1: Promote `to_utc` helper and fix `TimeParser._parse_custom_range`

**Files:**
- Modify: `src/codemie/utils/datetime_utils.py:22-24` (rename `_to_utc` → public `to_utc`), `:48` (update the one internal call site)
- Modify: `src/codemie/service/analytics/time_parser.py:104-130`
- Test: `tests/codemie/rest_api/routers/test_routers_utils.py`, `tests/codemie/service/analytics/test_time_parser.py`

**Interfaces:**
- Produces: `to_utc(dt: datetime) -> datetime` in `codemie.utils.datetime_utils` — naive input gets `tzinfo=timezone.utc` attached; aware input passes through unchanged. Tasks 2-5 import this.

Test-first: yes — add `test_to_utc_normalizes_naive_and_preserves_aware` (new, direct unit test of the renamed public function) to `test_routers_utils.py`, and add `test_parse_custom_range_naive_start_and_end_normalized` to `TestTimeParser` in `test_time_parser.py`, asserting `TimeParser.parse(None, datetime(2026, 1, 1), datetime(2026, 1, 2))` (no `tzinfo`) returns UTC-aware `start`/`end` and raises neither `TypeError` nor `ValueError`.

- [ ] Write the two failing tests above (import `to_utc` from `codemie.utils.datetime_utils`; it does not exist under that name yet, so the first test fails on `ImportError`; the second fails with `TypeError: can't compare offset-naive and offset-aware datetimes`).
- [ ] Run `pytest tests/codemie/rest_api/routers/test_routers_utils.py tests/codemie/service/analytics/test_time_parser.py -k "to_utc or naive"` and confirm both fail as predicted.
- [ ] Rename `_to_utc` to `to_utc` in `datetime_utils.py` (keep the body identical: `return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)`), update its one internal caller in `get_timestamp_bounds` (line 48) to call `to_utc`.
- [ ] In `time_parser.py`, add `from codemie.utils.datetime_utils import to_utc` and at the top of `_parse_custom_range` (before line 109's `if start_date:`) normalize: `start_date = to_utc(start_date) if start_date else None` and `end_date = to_utc(end_date) if end_date else None`.
- [ ] Re-run the two tests from step 2 and confirm they pass; also run the full `test_time_parser.py` and `test_routers_utils.py` files to confirm no regression.
- [ ] Commit.

---

### Task 2: Normalize naive dates in `AnalyticsFilterParams`

**Files:**
- Modify: `src/codemie/rest_api/routers/analytics/_common.py:110-125`
- Test: new file `tests/codemie/rest_api/routers/analytics/test_common_filter_params.py` (create directory if absent)

**Interfaces:**
- Consumes: `to_utc` from Task 1 (`codemie.utils.datetime_utils`).
- Produces: `AnalyticsFilterParams(start_date=..., end_date=...)` always yields UTC-aware (or `None`) `start_date`/`end_date` — relied on by every `AnalyticsQueryParams`-based endpoint in `cli.py` and `activity.py` (`cli-agents`, `cli-llms`, `cli-users`, `cli-errors`, `cli-repositories`, `cli-top-performers`, `cli-top-versions`, `cli-top-proxy-endpoints`, `cli-tools`).

Test-first: yes — `test_analytics_filter_params_normalizes_naive_dates_to_utc`: instantiate `AnalyticsFilterParams(start_date=datetime(2026, 1, 1), end_date=datetime(2026, 1, 2))` (both naive) and assert `result.start_date.tzinfo == timezone.utc` and `result.end_date.tzinfo == timezone.utc`. Also `test_analytics_filter_params_rejects_malformed_date_string` confirming `AnalyticsQueryParams` already 422s on an unparseable string via FastAPI/Pydantic coercion (no new code for this case — confirming test only), using Pydantic's `TypeAdapter` or `pytest.raises(ValidationError)` directly against the model.

- [ ] Write both tests in the new test file; run them and confirm the first fails (`start_date.tzinfo is None`) and the second already passes (confirms existing behavior, no regression risk).
- [ ] In `_common.py`, add `from pydantic import field_validator` and `from codemie.utils.datetime_utils import to_utc` to the imports, then add to `AnalyticsFilterParams`:
  ```python
  @field_validator("start_date", "end_date")
  @classmethod
  def _normalize_to_utc(cls, value: datetime | None) -> datetime | None:
      return to_utc(value) if value is not None else value
  ```
- [ ] Re-run the new test file and confirm both tests pass.
- [ ] Commit.

---

### Task 3: Normalize naive dates in `cli_analytics.py`'s `FilterParams`

**Files:**
- Modify: `src/codemie/rest_api/routers/cli_analytics.py:255-270`
- Test: `tests/codemie/rest_api/routers/test_cli_analytics.py` (or existing test file covering this router — add alongside existing `FilterParams`/`resolve` tests)

**Interfaces:**
- Consumes: `to_utc` from Task 1.
- Produces: `FilterParams.start_date`/`.end_date` are always UTC-aware (or `None`) immediately after `__init__`, before `resolve()` calls `TimeParser.parse(...)`.

Test-first: yes — `test_filter_params_normalizes_naive_dates_to_utc`: construct `FilterParams(time_period=None, start_date=datetime(2026, 1, 1), end_date=datetime(2026, 1, 2), users=None, projects=None, repositories=None)` with naive args and assert `params.start_date.tzinfo == timezone.utc` and `params.end_date.tzinfo == timezone.utc` directly (no need to call `resolve()`, which hits storage).

- [ ] Write the test; run it and confirm it fails (`tzinfo is None`).
- [ ] In `cli_analytics.py`, add `from codemie.utils.datetime_utils import to_utc` to imports (near existing `datetime, timezone` import at line 48), then in `FilterParams.__init__` change lines 266-267 from `self.start_date = start_date` / `self.end_date = end_date` to `self.start_date = to_utc(start_date) if start_date else None` and `self.end_date = to_utc(end_date) if end_date else None`.
- [ ] Re-run the test and confirm it passes.
- [ ] Commit.

---

### Task 4: Normalize naive dates in `get_cli_summary`

**Files:**
- Modify: `src/codemie/rest_api/routers/analytics/cli.py:46-64`
- Test: `tests/codemie/rest_api/routers/test_analytics.py` (add alongside existing `cli.py` router tests, mocking `AnalyticsService` the way neighboring tests in that file do)

**Interfaces:**
- Consumes: `to_utc` from Task 1.

Test-first: yes — `test_get_cli_summary_normalizes_naive_dates`: call `get_cli_summary(user=mock_user, time_period=None, start_date=datetime(2026, 1, 1), end_date=datetime(2026, 1, 2), users=None, projects=None)` with `AnalyticsService` mocked (per existing pattern in this file), then assert the mock's `get_cli_summary` was called with UTC-aware `start_date`/`end_date` (e.g. inspect `call_args` and check `.tzinfo == timezone.utc` on the second and third positional args), and that the call returns 200 (no exception).

- [ ] Write the test; run it and confirm it fails (mock was called with naive datetimes, `tzinfo is None`).
- [ ] In `cli.py`, add `from codemie.utils.datetime_utils import to_utc` to imports, then at the top of `get_cli_summary`'s body (before line 56's `service = AnalyticsService(user)`), add `start_date = to_utc(start_date) if start_date else None` and `end_date = to_utc(end_date) if end_date else None`.
- [ ] Re-run the test and confirm it passes.
- [ ] Commit.

---

### Task 5: Fix `get_projects_unique_daily` — normalize dates and stop masking its 400 as a 500

**Files:**
- Modify: `src/codemie/rest_api/routers/analytics/activity.py:396-446`
- Test: `tests/codemie/rest_api/routers/test_analytics.py:1072-1094` (update existing test's assertion) and two new tests in the same file

**Interfaces:**
- Consumes: `to_utc` from Task 1, `ExtendedHTTPException` from `codemie.core.exceptions` (not yet imported in this file).

Test-first: yes — three changes to `test_analytics.py`:
1. `test_get_projects_unique_daily_validates_date_order` (lines 1072-1094): inputs stay unchanged (`start=datetime(2026,1,31)`, `end=datetime(2026,1,1)`, naive and genuinely reversed); change the assertion from `exc_info.value.code == status.HTTP_500_INTERNAL_SERVER_ERROR` to `exc_info.value.code == status.HTTP_400_BAD_REQUEST`, keeping the existing `"start_date" in ... or "end_date" in ...` detail check.
2. New `test_get_projects_unique_daily_normalizes_naive_non_reversed_dates`: naive, non-reversed `start_date`/`end_date` (e.g. `datetime(2026, 1, 1)` / `datetime(2026, 1, 2)`), `AnalyticsService` mocked per the pattern in `test_get_projects_unique_daily_handles_empty_result` (same file, ~line 1096), asserting a normal response (no exception raised) and that the mocked service received UTC-aware dates.
3. New `test_get_projects_unique_daily_malformed_date_string_returns_422`: confirming-only test that an unparseable date string on this endpoint's query params produces FastAPI's existing 422 (no new code for this assertion).

- [ ] Write all three test changes; run `pytest tests/codemie/rest_api/routers/test_analytics.py -k projects_unique_daily -v` and confirm test 1 fails (currently asserts 500, code will still be 500 before the fix — expect the updated assertion to fail against current code), test 2 fails with the current naive-datetime `TypeError`→500 path, and test 3 already passes.
- [ ] In `activity.py`, add `from codemie.core.exceptions import ExtendedHTTPException` and `from codemie.utils.datetime_utils import to_utc` to imports. At the top of `get_projects_unique_daily`'s body, before the existing reversed-range check (line 430), normalize: `start_date = to_utc(start_date) if start_date else None` and `end_date = to_utc(end_date) if end_date else None`.
- [ ] Replace lines 431-436's `if start_date and end_date and start_date > end_date: from fastapi import HTTPException; raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="start_date must be before or equal to end_date")` with:
  ```python
  if start_date and end_date and start_date > end_date:
      raise ExtendedHTTPException(
          code=status.HTTP_400_BAD_REQUEST,
          message="Invalid request parameters",
          details="start_date must be before or equal to end_date",
          help="Please check your start_date and end_date values.",
      )
  ```
- [ ] Re-run the three tests from step 1 and confirm all pass.
- [ ] Commit.

---

## Negative-constraints pass

- "never reject with 400 for merely lacking an offset" — Tasks 2-5 all normalize via `to_utc` rather than raising; no task adds a rejecting validator for the naive-but-otherwise-valid case. Confirmed by each task's 200/normalized test.
- "`_parse_period`/predefined `time_period` unchanged" — no task touches `TimeParser._parse_period` or the `PERIODS` dict; Task 1 only edits `_parse_custom_range`.
- "do not edit `usage.py`, `usage_attribution.py`, `cli_insights.py`, `activity_events_router.py`" — no task lists these files; they are protected transitively by Task 1's fix inside `TimeParser._parse_custom_range`, which all of them call into.
- "do not broaden `handle_analytics_errors`" — Task 5 changes only the raise site in `activity.py` (raw `HTTPException` → `ExtendedHTTPException`); the decorator itself (`_common.py:47-94`) is unmodified by every task.
- "no OpenAPI schema changes" — Task 2's `@field_validator` does not change `AnalyticsFilterParams`'s field types; Tasks 3-5 change function bodies, not signatures. No task adds, removes, or retypes a query parameter.
- "HTTP 500 must never be returned for any date input" — Task 1 removes the `TypeError` source; Task 5 removes the second masking path (raw `HTTPException` swallowed by the generic `except Exception`). No remaining task leaves a naive or reversed date path falling through to the generic 500 branch.
