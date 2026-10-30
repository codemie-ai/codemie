# Spec: Normalize naive datetimes to UTC in CLI Analytics date filters

**Ticket**: EPMCDME-16393
**Status**: Approved (policy stakeholder-confirmed 2026-10-29; see technical-analysis.md Section 9)

## Problem

`start_date`/`end_date` query parameters on CLI Analytics endpoints currently return HTTP 500
whenever the client omits a UTC offset. The crash originates in
`TimeParser._parse_custom_range` (`src/codemie/service/analytics/time_parser.py:104-130`), which
compares the caller-supplied `final_start`/`final_end` against an always-aware
`datetime.now(timezone.utc)`. A naive input raises `TypeError`, which
`handle_analytics_errors` (`src/codemie/rest_api/routers/analytics/_common.py:47-94`) has no
specific branch for, so it falls into the generic `except Exception` → HTTP 500 path.

A related, previously undiagnosed bug sits in `get_projects_unique_daily`
(`src/codemie/rest_api/routers/analytics/activity.py:396-436`): its own reversed-range check
raises a bare `HTTPException(400, ...)` instead of `ExtendedHTTPException`. That exception type
is not special-cased by `handle_analytics_errors`, so it also falls through to the generic
branch and returns 500 — independent of the naive/aware `TypeError` above, and the actual reason
`test_get_projects_unique_daily_validates_date_order` currently observes a 500.

## Policy (decided, not open)

Naive `start_date`/`end_date` are normalized to UTC wall time (`tzinfo=UTC` attached), never
rejected for merely lacking an offset. This matches existing precedent
(`datetime_utils.py::_to_utc`, `a2a/types.py`, `models/conversation.py`'s naive `user_date`
handling) and the already-written but unimplemented contract in `activity.py`'s docstring
("must be UTC-aware or will be treated as UTC").

Unconditional invariants:
- A reversed range (`start_date` after `end_date`) → HTTP 400/422 with a message naming the field.
- A malformed/unparseable datetime string → HTTP 400/422.
- HTTP 500 is never returned for any date input.
- Predefined `time_period` values (e.g. `last_7_days`) are untouched — `_parse_period` is not in scope.

## Fix locations

1. `AnalyticsFilterParams` (Pydantic `BaseModel`, `_common.py:110-125`) — add normalization so
   naive `start_date`/`end_date` gain `tzinfo=UTC` before any handler sees them. Covers
   `AnalyticsQueryParams` and therefore `cli-users`, `cli-llms`, `cli-tools`, `cli-repositories`,
   and other `Depends()`-based analytics endpoints.
2. `FilterParams` (plain class, not a Pydantic model, `cli_analytics.py:255-270`) — normalize
   imperatively in `__init__`, the OTel/PostgreSQL CLI analytics path.
3. `get_cli_summary`'s inline `start_date`/`end_date` params (`cli.py:47-54`) — not covered by
   `AnalyticsFilterParams` since the endpoint declares its own params; normalize at the top of
   the handler body before use.
4. `get_projects_unique_daily`'s inline params and its own reversed-range check
   (`activity.py:396-436`) — normalize the same way, and change its `raise HTTPException(...)`
   to `raise ExtendedHTTPException(code=400, ...)` so the existing generic-exception branch no
   longer recasts a correctly-detected 400 as a 500.
5. `TimeParser._parse_custom_range` (`time_parser.py:104-130`) — normalize any naive
   `start_date`/`end_date` it receives directly, as a shared-utility backstop. `TimeParser.parse()`
   has 20+ call sites, including routers outside this ticket's five named endpoints
   (`usage.py`, `usage_attribution.py`, `cli_insights.py`, `activity_events_router.py`, all of
   which declare their own inline naive-capable `datetime` params). Fixing the shared utility
   protects those transitively without editing them directly.

All five fixes use the same one-line pattern as the existing `_to_utc` precedent
(`dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)`); extracting a single shared
helper over duplicating the line five times is preferred but left to the implementation plan.

Malformed/unparseable strings already produce a 422 today via FastAPI/Pydantic's own `datetime`
type coercion on `Query(...)` fields — no new code is needed for that invariant, only a
confirming test.

## Testing

- `test_time_parser.py`: new cases feeding naive `start_date`, naive `end_date`, and both naive
  to `_parse_custom_range`, asserting no `TypeError` and UTC-normalized output.
- `test_analytics.py::test_get_projects_unique_daily_validates_date_order`: inputs are
  unchanged (`start_date=2026-01-31`, `end_date=2026-01-01`, both naive — a genuinely reversed
  range, not merely a naive range). Only the expectation changes: assert
  `ExtendedHTTPException` with `code == HTTP_400_BAD_REQUEST` instead of 500. The task note
  suggesting this test be inverted to expect "200/normalized" does not hold here — these inputs
  are reversed, and the reversed-range invariant above requires 400, never 200, for this case.
- New test (naive, non-reversed `start_date`/`end_date` on `get_projects_unique_daily`) asserting
  a normalized 200, since the corrected version of the existing test no longer exercises that path.
- New equivalent naive-input/200 tests for the other four fix locations (`cli-summary`,
  `cli-users` or another `AnalyticsFilterParams`-backed endpoint, the `cli_analytics.py` OTel
  path, and a malformed-string case returning 422).
- Confirm via `tests/codemie/rest_api/router_guards/` that no request/response schema changed
  (normalization changes behavior, not the field's declared type).

## Acceptance Criteria

- Naive `start_date`/`end_date` at all five fix locations are normalized to UTC and the request
  succeeds (200) when otherwise valid — never 500, never 400 for the mere absence of an offset.
- A reversed date range returns 400/422 with a message identifying `start_date`/`end_date`,
  at every endpoint reachable through `AnalyticsFilterParams`, `FilterParams`, `get_cli_summary`,
  and `get_projects_unique_daily`.
- A malformed/unparseable datetime string returns 400/422, confirmed by test.
- `time_period` predefined-range behavior is unchanged.
- `test_get_projects_unique_daily_validates_date_order` passes with inputs unchanged and an
  inverted expectation of HTTP 400 (not 500, not 200).
- A new test demonstrates the naive + non-reversed + 200 path that the corrected existing test
  no longer covers.
- No OpenAPI schema change.

## Non-Goals

- `analytics-cli.js` / codemie-ui client-side normalization — confirmed to live in a separate
  repository (`codemie-ui`); tracked separately, not part of this backend fix.
- Broadening `handle_analytics_errors` to catch all `HTTPException` subclasses globally — the
  fix is scoped to the single raw-`HTTPException` raise site in `activity.py`.
- Changing `TimeParser._parse_period` or any predefined `time_period` behavior.
- Changing the public API contract: query parameter names, types, or response shapes.
- Rejecting naive datetimes with 400 for the mere absence of a timezone offset — explicitly
  decided against in favor of UTC normalization.
- Editing `usage.py`, `usage_attribution.py`, `cli_insights.py`, or `activity_events_router.py`
  directly — these are protected transitively via the `TimeParser` fix (location 5 above).
