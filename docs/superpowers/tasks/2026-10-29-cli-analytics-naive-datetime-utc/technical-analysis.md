# Technical Research

**Task**: analytics datetime timezone validation cli-analytics time_parser rest_api
**Generated**: 2026-10-29T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

CLI Analytics endpoints return HTTP 500 when start_date and end_date are supplied as ISO datetime values without timezone information. Root cause confirmed: backend file src/codemie/service/analytics/time_parser.py _parse_custom_range at line 122 compares a potentially timezone-naive final_end against datetime.now(timezone.utc). The naive datetime comes from src/codemie/rest_api/routers/analytics/_common.py lines 114-115 which accepts datetimes without timezone validation. CLI file analytics-cli.js buildQuery at lines 310-311 forwards date strings without normalization. Affected endpoints: /v1/analytics/cli-summary, /cli-users, /cli-llms, /cli-tools, /cli-repositories. Fix requires: consistent timezone policy for date filters, normalization or validation of naive inputs, matching CLI and backend contract, no unhandled TypeError returning HTTP 500.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/service/analytics/time_parser.py` — `TimeParser` class with `_parse_custom_range` static method (lines 104-130). Line 106: `now = datetime.now(timezone.utc)` is always timezone-aware. Lines 120 and 122 compare `final_start`, `final_end` (potentially naive, passed in unchanged) against `now`. When `final_end` is naive, `final_end > now` raises `TypeError: can't compare offset-naive and offset-aware datetimes`. `TimeParser.parse()` is the single entry point called by all handler and pipeline files.
- `src/codemie/rest_api/routers/analytics/_common.py` — `AnalyticsFilterParams` Pydantic `BaseModel` at lines 110-125. Lines 113-115: `time_period`, `start_date`, `end_date` declared as `datetime | None = Query(None)` with no timezone validation or normalization. FastAPI parses `?start_date=2026-01-01T00:00:00` (no `Z` or offset) as a naive `datetime` object with `tzinfo=None`.
- `src/codemie/rest_api/routers/analytics/cli.py` — The five affected endpoints. `/cli-summary` (line 40) declares `start_date: datetime | None = Query(None)` and `end_date: datetime | None = Query(None)` inline (not via `AnalyticsFilterParams`), also with no timezone validation. `/cli-users`, `/cli-llms`, `/cli-tools`, `/cli-repositories` use `params: AnalyticsQueryParams = Depends()` which inherits from `AnalyticsFilterParams`.
- `src/codemie/rest_api/routers/analytics/_common.py` — `handle_analytics_errors` decorator (lines 47-94). Catches `ValueError → HTTP 400`, `Exception → HTTP 500`. `TypeError` is not caught by `ValueError`; it falls to the generic `Exception` branch, returning HTTP 500 and logging the internal error.
- `src/codemie/rest_api/routers/cli_analytics.py` — Separate `FilterParams` class (lines 245-260) for the OTel/PostgreSQL path. Also declares `start_date: datetime | None = Query(None)` and `end_date: datetime | None = Query(None)` without timezone validation. Calls `TimeParser.parse()` at line 270.
- `src/codemie/service/analytics/handlers/cli/handler.py` — `CLIHandler` with `get_cli_summary` (line 66), `get_cli_llms` (line 456), `get_cli_users` (line 538), `get_cli_repositories` (line 739), `get_cli_tools_usage` (line 1186). Each calls `TimeParser.parse(time_period, start_date, end_date)` directly.
- `src/codemie/service/analytics/handlers/cli/base_handler.py` — `BaseHandler` with `_format_custom_tabular_response`, `_format_custom_summary_response`, `_format_custom_detail_response` (lines 70, 94, 112). Each calls `TimeParser.parse()` independently.
- `src/codemie/service/analytics/query_pipeline.py` — `QueryPipeline` calls `TimeParser.parse()` at lines 87, 147, 274, 369, 414, 458.
- `src/codemie/service/analytics/handlers/cli/insights_handler.py` — `CLIInsightsHandler` calls `TimeParser.parse()` at lines 167, 224, 481, 520, 642, 696, 746, 804, 1758.
- `analytics-cli.js` — NOT FOUND in this repository (see Section 8).

### Architecture and Layers Affected

- **REST API router layer**: `_common.py` (`AnalyticsFilterParams`/`AnalyticsQueryParams`) and `cli.py` inline params accept and forward naive datetimes. `cli_analytics.py` `FilterParams` class has the same gap.
- **Service/handler layer**: `CLIHandler`, `BaseHandler`, `QueryPipeline`, `CLIInsightsHandler` — all call `TimeParser.parse()` with whatever `start_date`/`end_date` the router passed in.
- **Utility/parsing layer**: `TimeParser._parse_custom_range()` performs the unsafe comparison at line 122.
- **Error handling**: `handle_analytics_errors` decorator wraps all CLI endpoints; the `TypeError` reaches its generic `except Exception` branch.

### Integration Points

- All 5+ affected CLI router endpoints in `cli.py` route through `AnalyticsService` → `CLIHandler` → `TimeParser`.
- `query_pipeline.py` is also a callsite; it is called by many other handler types beyond CLI.
- `cli_analytics.py` `FilterParams` is a second independent acceptance point for the same naive datetime input.
- `activity.py` docstring (line 247-248) documents that inputs "must be UTC-aware or will be treated as UTC" — but no code enforces this.

### Patterns and Conventions

- `AnalyticsFilterParams` is a Pydantic `BaseModel` used as a FastAPI `Depends()`; field validators (`@field_validator`) are the established pattern for input normalization in this layer.
- `handle_analytics_errors` covers `ValueError → 400` and `Exception → 500`; a fix that raises `ValueError` for unresolvable timezone input (rather than letting `TypeError` propagate) would produce a 400 response consistent with the existing pattern.
- The convention from `cli-analytics-events.md` rule 5 states: "timestamp is ISO-8601 in UTC with `Z`"; this establishes UTC-only as the system's single timezone policy.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/cli-analytics-events.md` — Event contract guide. Rule 5 states: "`timestamp` is ISO-8601 in UTC with `Z`; milliseconds are allowed." Establishes UTC as the only accepted timezone for this system.
- `.ai-run/guides/integration/cli-analytics-storage.md` — PostgreSQL storage guide for OTel CLI analytics. Covers `FilterParams` in `cli_analytics.py`, window semantics, and environment variables.
- `.ai-run/guides/api/rest-api-patterns.md` — FastAPI router patterns; directs use of `ExtendedHTTPException` for consistent error responses.
- `.ai-run/guides/development/error-handling.md` — Requires typed exceptions and sanitized error messages.
- `.ai-run/guides/testing/testing-api-patterns.md` — Router contract guards; OpenAPI snapshot guard fails on any contract change.

### Architectural Decisions

- `activity.py` comment at line 247-248: "Input dates (start_date/end_date) must be UTC-aware or will be treated as UTC." This is a stated policy with no enforcement code.
- The `cli-analytics-events.md` guide's rule 5 establishes UTC + `Z` suffix as the canonical timestamp format system-wide.
- `handle_analytics_errors` establishes `ValueError → HTTP 400` as the correct status code for bad date/time input.

### Derived Conventions

- Normalization of naive datetimes to UTC should be done at the boundary closest to external input (either in `AnalyticsFilterParams` validator or in `TimeParser._parse_custom_range`), not in each handler callsite.
- Raising `ValueError` (not `TypeError`) ensures the `handle_analytics_errors` decorator returns HTTP 400 rather than HTTP 500, consistent with the existing `ValueError` → 400 path for invalid `time_period`.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/analytics/test_time_parser.py` — Full unit coverage of `TimeParser`. All custom date range tests (`test_parse_valid_custom_date_range`, `test_parse_custom_range_end_in_future_raises_value_error`, etc.) supply timezone-aware datetimes (`tzinfo=timezone.utc`). No test supplies a naive datetime to `_parse_custom_range`.
- `tests/codemie/rest_api/routers/test_analytics.py` — Router tests. Uses naive datetimes at lines 312-313, 574-575, 945-946, 1028-1029 — but these tests mock `AnalyticsService` and do not call `TimeParser`, so they bypass the bug. Test `test_get_projects_unique_daily_validates_date_order` (lines 1072-1094) passes naive datetimes directly to `get_projects_unique_daily` without mocking the service and **expects HTTP 500** — this documents the current broken behavior rather than the desired outcome.

### Testing Framework and Patterns

- pytest with `pytest-asyncio` (`asyncio_default_fixture_loop_scope = function`) and `-n 3` parallelism.
- `unittest.mock.patch` to mock service class; `AsyncMock` for service instances.
- `pytest.raises(ExtendedHTTPException)` to assert error code and details.
- Fixture pattern: `mock_user` fixture builds a `MagicMock(spec=User)`.

### Coverage Gaps

- No test in `test_time_parser.py` for naive `start_date` or `end_date` inputs to `TimeParser._parse_custom_range` — the path that raises `TypeError`.
- No test verifying that a naive ISO datetime as a query parameter (simulating CLI behavior) reaches `TimeParser` and produces a 400 (not 500).
- `test_get_projects_unique_daily_validates_date_order` (line 1072) documents the 500 behavior with naive inputs; it would need to be updated to expect 400 after the fix.
- `cli_analytics.py`'s `FilterParams.resolve` also needs a corresponding test for naive-input handling.

---

## 5. Configuration and Environment

### Environment Variables

- `CLI_ANALYTICS_PG_URL` — PostgreSQL URL for OTel analytics storage (from cli-analytics-storage.md guide); empty defaults to the application database.
- `CLI_ANALYTICS_PG_SCHEMA` — Schema for analytics tables; default `codemie_analytics`.
- `ENV` — `local` / `development` / production; affects cache headers in `_create_response`.

### Configuration Files

- `pyproject.toml` — Python 3.12+, `fastapi ^0.133.0`, Poetry-managed dependencies.
- `pytest.ini` — `testpaths = tests`, `asyncio_default_fixture_loop_scope = function`, `-n 3` parallel workers, `ENV=local` injected.
- `.env.example` — No `CLI_ANALYTICS_*` variables listed.

### Feature Flags and Deployment Concerns

- `cliAnalytics` feature flag guards `cli_analytics.py` endpoints (line 138); CLI endpoints in `cli.py` have no feature flag guard.
- The fix is backward compatible: normalizing naive datetimes to UTC or returning 400 for naive inputs does not change behavior for callers already sending UTC-aware strings.

---

## 6. Risk Indicators

- **`analytics-cli.js` is not in this repository.** Its `buildQuery` at lines 310-311 (the CLI normalization fix) cannot be researched here; the caller must locate the correct repository separately. The backend fix can be delivered independently.
- **`TypeError` silently becomes HTTP 500.** The `handle_analytics_errors` generic `except Exception` branch swallows the root cause message in the response body, making the failure opaque to API callers.
- **Dual acceptance points for naive datetimes.** `AnalyticsFilterParams` in `_common.py` (used by most CLI endpoints) and `FilterParams` in `cli_analytics.py` (OTel path) are separate classes; a fix in `AnalyticsFilterParams` does not cover `FilterParams`.
- **`get_cli_summary` uses inline params, not `AnalyticsFilterParams`.** Its `start_date` and `end_date` are declared directly on the function signature; a fix in `AnalyticsFilterParams` alone would miss this endpoint.
- **`TimeParser.parse()` is called 20+ times across handler files.** A fix inside `TimeParser._parse_custom_range` propagates to all callsites without touching handler code; a fix only at the router boundary would leave handler-to-handler calls unprotected.
- **Existing test at line 1072 of `test_analytics.py` explicitly asserts HTTP 500** for a naive datetime input. This test must be updated; if it is not updated, it will fail after the correct fix is applied, surfacing as a CI regression.
- **test_time_parser.py tests always supply UTC-aware inputs.** The fix must add new test cases for naive inputs; the existing tests do not cover the corrected path.
- **OpenAPI snapshot guard** in `tests/codemie/rest_api/router_guards/` will fail if the fix changes any request or response schema. Adding a Pydantic validator to `AnalyticsFilterParams` does not change the schema; adding a new query parameter would.

---

## 7. Summary for Complexity Assessment

The fix is confined to two interacting layers: the request parameter acceptance boundary (`AnalyticsFilterParams` in `_common.py`, inline params in `cli.py`, and `FilterParams` in `cli_analytics.py`) and the time-parsing utility (`TimeParser._parse_custom_range` in `time_parser.py`). The root cause is a single missing guard — naive-datetime normalization or rejection before `TimeParser` compares `final_end` to a timezone-aware `now`. The most robust fix is a single change inside `_parse_custom_range` (treat any naive input as UTC via `.replace(tzinfo=timezone.utc)`) combined with router-layer validation to surface the policy as a 400 to callers rather than silently coercing; this would update three files: `time_parser.py`, `_common.py`, and `cli_analytics.py`. Alternatively, if strict rejection is preferred, adding a Pydantic `@field_validator` to `AnalyticsFilterParams` and `FilterParams` that raises `ValueError` for naive datetimes produces HTTP 400 without changing `TimeParser`.

Testing posture is a medium risk: the existing `test_time_parser.py` suite never exercises naive inputs, and `test_analytics.py` line 1072 actively asserts HTTP 500 for the broken behavior and will fail after the fix. New tests are needed in `test_time_parser.py` for naive-datetime input to `_parse_custom_range` (expecting either normalization to UTC or a `ValueError`) and in `test_analytics.py` to replace the broken-behavior assertion with the corrected 400 response. The fix itself is small (1–3 files), but the test surface touched by the change includes the time-parser unit tests and at least one router test that documents the existing bug.

The CLI JavaScript file (`analytics-cli.js`) named in the task is absent from this repository and must be found separately. The backend fix can ship independently of the CLI normalization change because the backend can normalize inbound naive datetimes; the CLI fix is a defense-in-depth measure to avoid sending ambiguous strings in the first place.

---

## 8. External References

The task names `analytics-cli.js` with `buildQuery` at lines 310-311 as a source of truth for the CLI side of the fix. This file was **not found** in the repository available to the original product-owner research session. A search across all JavaScript files in that project returned only `scripts/sonar/run-local-sonar.js` and `src/codemie/templates/devbox_export/codemie-ui/config.js` — neither matches. The file is in the separate `codemie-ui` repository (`src/pages/analytics/components/cli-analytics/...` / `analytics-cli.js` under the CLI tooling), confirmed in this run via a cross-repository check against `C:\Repositories\codemie-ui`: the UI's DatePicker widget always emits UTC `Z`-suffixed strings, but `useAnalyticsFilters.ts`'s `isValidISODate` accepts bare date-only strings (no offset) from hand-edited/shared URLs and the Usage Attribution tab, which do reach these backend endpoints unmodified. The backend fix (normalize naive datetimes to UTC) is still correct and delivers independently of any CLI/UI follow-up.

---

## 9. Stakeholder Decision (added 2026-10-29, this run)

**Timezone policy resolved: normalize naive datetimes to UTC, not reject with 400.** Confirmed by stakeholder after cross-checking:
- Backend precedent: `src/codemie/utils/datetime_utils.py::_to_utc` ("naive datetimes are treated as UTC wall time"), `src/codemie/rest_api/a2a/types.py` (naive legacy values taken as UTC), `src/codemie/rest_api/models/conversation.py` (naive `user_date` replaced with UTC tzinfo).
- `routers/analytics/activity.py` already documents (but does not implement) exactly this contract for the same endpoint family: "must be UTC-aware or will be treated as UTC."
- Control Plane (`C:\Repositories\control-plane`) never sends naive dates to these endpoints, but its own `util.py::iso_utc()` normalizer treats naive timestamps from other upstream sources as UTC — same convention.
- codemie-ui has a real (if edge-case) path that can produce naive date-only values today (hand-edited/shared analytics URLs, Usage Attribution tab pass-through); normalizing to UTC fixes that path instead of turning it into a new 400 for input that is almost certainly UTC-intended.

This decision supersedes the "Open Questions — Timezone policy" item in `story.md` and in Section 7 above: implement normalization (not rejection) at the parameter acceptance boundary / `TimeParser._parse_custom_range`, while still rejecting a reversed date range and malformed/unparseable strings with 400/422 (unchanged from the original acceptance criteria).
