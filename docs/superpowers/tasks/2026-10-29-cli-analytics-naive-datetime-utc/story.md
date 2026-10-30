# [CLI Analytics] Fix HTTP 500 on Timezone-Naive Date Range Filters — Story

**Date**: 2026-10-29
**Status**: Approved
**Ticket**: [EPMCDME-16393](https://jiraeu.epam.com/browse/EPMCDME-16393)

---

## Context

- Five CLI Analytics endpoints (`/cli-summary`, `/cli-users`, `/cli-llms`, `/cli-tools`, `/cli-repositories`) accept `start_date` and `end_date` query parameters but do not validate timezone information before passing values to the time-parsing layer.
- Datetime inputs flow through two separate parameter classes — `AnalyticsFilterParams` (`_common.py`, used by most endpoints) and `FilterParams` (`cli_analytics.py`, OTel path) — plus inline function parameters on `get_cli_summary`; all three acceptance points lack timezone validation.
- `TimeParser._parse_custom_range` compares `final_end` to a timezone-aware `datetime.now(timezone.utc)`; when `final_end` is timezone-naive, Python raises an unhandled `TypeError` that the generic error handler converts to HTTP 500.
- An existing test explicitly asserts HTTP 500 for naive datetime input and documents the broken behavior; it must be updated as part of this fix.
- The CLI-side file responsible for forwarding date strings without normalization lives in a separate repository and is out of scope for this story.

---

## Complexity

**Size**: M (17/36) · **Recommended flow**: sdlc-standard

Fix spans four acceptance points and a shared utility called 20+ times; an open timezone-policy decision and an existing test that must be inverted add design and test risk beyond a simple one-file patch.

---

## Story

**As a** developer or analytics user consuming CodeMie CLI Analytics APIs, **I want** explicit date range filters to work correctly regardless of whether the datetime string includes timezone information, **so that** I can retrieve analytics data for any custom time window without hitting unexpected server errors.

---

## Background

CLI Analytics endpoints return HTTP 500 when `start_date` or `end_date` are supplied as timezone-naive ISO datetime strings (e.g. `2026-10-01T00:00:00`). The failure is a Python comparison error — timezone-naive vs timezone-aware datetime objects — that is swallowed by the generic error handler and surfaced as an opaque 500 with no actionable message. Timezone-aware strings (e.g. `2026-10-01T00:00:00+00:00`) work correctly. The inconsistency affects all five CLI Analytics endpoints in production (CodeMie CLI 0.16.2) and makes custom date range filtering unreliable for any caller that omits the UTC offset.

---

## Acceptance Criteria

- [ ] Given a request to any of the five CLI Analytics endpoints with timezone-aware datetime values, when the request is processed, then the response is HTTP 200 with the expected analytics payload.
- [ ] Given a request with timezone-naive datetime values (no UTC offset), when the request is processed, then the backend handles the input according to the documented timezone policy — either normalizing to UTC or returning a descriptive HTTP 400/422 — and HTTP 500 is never returned.
- [ ] Given a reversed date range (`start_date` later than `end_date`), when the request is processed, then the response is HTTP 400/422 with a meaningful validation error message.
- [ ] Given a request using a predefined time-period filter (e.g. `period=last_7_days`) without explicit dates, when the request is processed, then behavior is unchanged and the response is HTTP 200.
- [ ] Given a request with a malformed or unparseable datetime string, when the request is processed, then the response is HTTP 400/422 with a descriptive error message, not HTTP 500.

---

## Out of Scope

- Changes to `analytics-cli.js` or any CLI-side date normalization — that file lives in a separate repository and is tracked separately.
- Analytics endpoints that do not accept `start_date` / `end_date` parameters.
- Changes to predefined period filter behavior (e.g. `last_7_days`, `last_30_days`).
- UI changes.

---

## Open Questions

- **Timezone policy**: Should timezone-naive inputs be silently normalized to UTC, or strictly rejected with HTTP 400/422? The choice affects the API contract and downstream CLI behavior; stakeholder decision needed before implementation begins.
  **Resolved 2026-10-29**: Normalize naive datetimes to UTC (do not reject with 400), confirmed by stakeholder decision — consistent with existing codebase precedent (`datetime_utils._to_utc`, `a2a/types.py` naive-as-UTC handling) and the already-documented (but unimplemented) contract on `routers/analytics/activity.py`.
- **CLI follow-up**: Once the backend timezone policy is confirmed, a companion story for `analytics-cli.js` normalization should be filed against the correct repository (not `epm-cdme/codemie`).
