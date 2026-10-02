# Spec: CLI Analytics timestamps always carry a UTC offset (EPMCDME-15437)

## Problem

CLI Analytics timestamp fields are sometimes rendered without a UTC offset,
making them ambiguous to the frontend's zone-aware formatter and to the
Start/End date filter. Root cause (confirmed by manual testing against a
live backend and direct source reading — see `technical-analysis.md`
Section 9, which supersedes that document's earlier frontend-root-cause
framing in Sections 2/6/7): the stored values are always true UTC instants,
but on read-back at least one repository driver path returns a naive
(tzinfo-less) `datetime`. The shared serializer in
`codemie/src/codemie/service/analytics/handlers/cli_analytics_handler.py:87-90`
(`_iso`) calls `.isoformat()` directly on whatever it is given, so a naive
input silently produces an offset-less string
(`"2026-09-30T12:28:08.937211"` instead of `"...+00:00"`).

This is a backend-only defect. The frontend's `parseDate`
(`codemie-ui/src/utils/helpers.ts`) already defers to an explicit offset
whenever one is present, and the DatePicker filter already performs correct
local→UTC conversion; both only misbehave today because the API sometimes
hands them an unlabeled string.

## Fix

Change `_iso` so a naive `datetime` is treated as UTC before serialization,
while an already-aware `datetime` and any `date` value pass through
unchanged (a `date` has no time-of-day or offset to attach):

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

`datetime` is checked before `date` because `datetime` is itself a `date`
subclass; order matters.

Because every CLI Analytics timestamp field is serialized through this one
helper, fixing it here covers all of them without touching call sites:

- `last_active` (Users tab) — `cli_analytics_handler.py:376`
- `start_time` (sessions list) — `cli_analytics_handler.py:551`
- `start_time` (session detail) — `cli_analytics_handler.py:677`
- `timestamp` (session timeline/dispatch events) — `cli_analytics_handler.py:641`

The day-bucket call sites (`:259`, `:264`, `:352`) pass `date` objects, not
`datetime`, and are unaffected by this change — confirming they need no
edit is part of the regression test, not a separate code change.

## Testing

Add regression tests to
`codemie/tests/codemie/service/analytics/handlers/test_cli_analytics_handler.py`,
following its existing pattern of direct unit tests against module-level
helpers (e.g. `test_skill_dispatch_uses_real_duration_ms`):

1. `_iso()` on a naive `datetime` returns a string ending in `+00:00` (or
   equivalent UTC offset notation) for the same wall-clock value.
2. `_iso()` on an already-aware `datetime` (including a non-UTC offset)
   returns its own correct `.isoformat()` unchanged — no double conversion.
3. `_iso()` on a `date` returns the plain date string, unaffected.
4. At least one endpoint-level test (sessions list, session detail, Users
   `last_active`, or session timeline — reusing whatever existing
   endpoint test fixture already exercises `_iso`-backed fields) asserts
   the returned JSON's timestamp field contains an explicit UTC offset
   when the underlying repository/fixture datetime is naive.

## Non-goals

- No change to `codemie-ui/src/components/form/DatePicker/DatePicker.tsx`.
- No change to `codemie-ui/src/pages/analytics/components/AnalyticsFilters.tsx`.
- No change to `codemie-ui/src/utils/helpers.ts` (`parseDate`, `formatDate`,
  `formatDateTime`).
- No change to which repository driver is active, and no attempt to fix
  the driver itself to return tz-aware datetimes — `_iso` is fixed to be
  correct regardless of what the driver returns, which is the smaller and
  more durable fix (it also protects against any other driver path, present
  or future, exhibiting the same naive-datetime behavior).
- No change to `TimeParser`/`FilterParams` (`codemie/src/codemie/rest_api/routers/cli_analytics.py`,
  `codemie/src/codemie/service/analytics/time_parser.py`) — these already
  require and correctly handle timezone-aware inputs; they are not part of
  the defect.
- No new abstraction, wrapper type, or date utility — `_iso` keeps its
  existing signature and call sites untouched.

## Acceptance criteria

1. `_iso()` always returns a timezone-aware ISO 8601 string (explicit UTC
   offset) regardless of whether the input `datetime` was naive or already
   aware.
2. All four affected fields (sessions-list `start_time`, session-detail
   `start_time`, Users `last_active`, session-timeline `timestamp`) carry
   the fix, verified via the shared helper plus at least one endpoint-level
   assertion.
3. No frontend file is modified.
4. New backend tests (listed under Testing) pass and demonstrate the
   before/after behavior on a naive input.
5. A session shown at a given UTC time in the table is reliably findable
   via the Start/End filter using the browser-local-equivalent time, in
   both dev and prod builds — this follows automatically once the API
   response is unambiguous; confirmed by test/verification rather than by
   any filter-side code change.
