# Technical Research

**Task**: cli analytics cache-control
**Generated**: 2026-11-13
**Research path**: filesystem

---

## 1. Original Context

[CLI Analytics] Browser caches stale analytics responses for up to 5 minutes, causing outdated cost and session KPIs. (EPMCDME-17191, Bug)

Every CLI Analytics read endpoint responds with `Cache-Control: private, max-age=300`. The browser reuses a stored response for up to 5 minutes without contacting the server; after new sessions/costs are ingested a tab whose URL was already loaded keeps showing old figures. Backend data and API responses are consistent; stale values come from the browser HTTP cache.

Root cause (confirmed): `src/codemie/rest_api/routers/cli_analytics.py` `_respond()` (~lines 506-515) sets `Cache-Control: private, max-age=300` and an `ETag` for every non-local environment (introduced by commit fa1bfc246, EPMCDME-14100). Locally (`config.is_local`) it sends `no-store`. No conditional-request handling: a request with `If-None-Match` gets a full 200, never a 304. Affected routes: /overview, /cost, /users, /user-charts, /repositories, /projects, /tools, /activity, /efficiency, /frameworks, /sessions, /sessions/{trace_id}.

Recommended fix: Backend (smallest safe change): in `_respond()` replace `private, max-age=300` with `private, no-cache` and keep the ETag (`no-store` acceptable alternative for financial data). Do not add 304 handling unless the ETag is computed from the exact returned body. Frontend change (codemie-ui) is optional and OUT of this repo. Keep the 30s per-pod dashboard cache and EPMCDME-17051 rollups. Out of scope/related: legacy helper `analytics/_common.py::_create_response()` sends `public, max-age=300`.

Acceptance criteria:
1. CLI Analytics does not serve stale browser-cached responses after data changes on refresh.
2. Overview, Cost, Users, Repositories, Sessions return current data when revisited/refreshed.
3. Existing ETag/304 behaviour remains correct if retained; changed data must never produce an incorrect 304.
4. Backend dashboard caching and performance optimizations remain functional.
5. Automated regression tests: cached $0 followed by nonzero costs, repeated requests, conditional requests.
Backend regression tests: every CLI Analytics GET route returns agreed Cache-Control (no max-age > 0) outside local mode; If-None-Match with an old ETag after a data change returns 200 with new body, never 304.

---

## 2. Codebase Findings

### Existing Implementations

Purpose A: control how long a client may reuse a CLI Analytics read response.
- `src/codemie/rest_api/routers/cli_analytics.py:506-515` `_respond(payload, model_class)`: validates via the response model, `model_dump(by_alias=True)`, returns `JSONResponse`. `config.is_local` -> `Cache-Control: no-store`, no ETag. Otherwise `private, max-age=300` plus `ETag` = sha256 of `json.dumps(body, sort_keys=True)` (the exact returned body). No handling of `If-None-Match` anywhere in `src` for this router (grep for `If-None`/`etag` finds only unrelated tools and S3 code).
- Callers of `_respond` (all GET routes; one call each): `:630` overview, `:665` cost, `:717` and `:741` users / user-charts (via `asyncio.to_thread`), `:792` repositories, `:815` projects, `:834` and `:885` (two further routes, tools/activity range), `:907` efficiency, `:919` frameworks, `:953` sessions, `:995` session detail. GET routes declared at lines 183 (`/classification-policy`), 601, 633, 668, 720, 763, 795, 818, 862, 890, 910, 922, 956 (+ detail). `/classification-policy` (`:183`) does not go through `_respond` (not checked for its own headers).
- The body includes `metadata` built by `_metadata()` (`:496-503`): `timestamp` = `now_iso()` and `execution_time_ms`. Both vary per request, so the ETag differs on every response even for identical data. `/frameworks` has no metadata (`{"data": ...}` only).
- Ingest routes (POST `/logs`, `/metrics`, `/traces`, `/event-hooks`) do not use `_respond`.

Purpose B: backend reuse of computed answers (must remain, per task).
- `src/codemie/service/analytics/dashboard_cache.py`, used by `cli_analytics_handler.py` and `cli_analytics_jobs.py`; 30 s per pod, key = endpoint + viewer scope + filters + paging; `CLI_ANALYTICS_DASHBOARD_CACHE_TTL_S` (`.ai-run/guides/integration/cli-analytics-storage.md:41`). Covers `/users`, `/user-charts`, `/cost` only. Test: `tests/codemie/rest_api/routers/test_cli_analytics_dashboard_cache.py`.

Other touched code:
- `src/codemie/rest_api/routers/analytics/_common.py:119-129` `_create_response()` (legacy, non-CLI analytics): same shape, `public, max-age=300` + ETag (sha256 of `response_dict`); callers include `analytics/activity.py:71,98,123` and other modules in that package. Named by the task as out of scope/related.

### Decisions Already in the Code
- Local mode uses `no-store`, non-local uses cache + ETag: `cli_analytics.py:510-514`; reason: none found in code (commit fa1bfc246 / EPMCDME-14100 per task).
- ETag is derived from the full body (`json.dumps(body, sort_keys=True)`), not from data version. Convention: header decision sits in a single choke point (`_respond`), no per-route headers.
- The same `max-age=300` pattern is duplicated in the legacy `_create_response` (`public`).

### Task Statements Checked Against the Code
- "`_respond()` sets `private, max-age=300` + ETag for non-local; `no-store` locally": holds (`cli_analytics.py:510-514`).
- "No conditional-request handling": holds (no `If-None-Match` read in `src/codemie/rest_api/routers/cli_analytics.py` or `src/codemie`'s analytics routers).
- "Do not add 304 handling unless ETag computed from exact returned body": the ETag is computed from the returned body, but that body carries per-request `metadata.timestamp` / `execution_time_ms` (`:496-503`), so any stored ETag never equals a later one even without data change (facts only).
- "Keep the 30 s per-pod dashboard cache and rollups": keeps `dashboard_cache.py` in place (consumers: `cli_analytics_handler.py`, `cli_analytics_jobs.py`, router).
- "Legacy `_create_response` sends `public, max-age=300`": holds (`analytics/_common.py:127`).
- "Affected routes: ... 12": holds for `_respond` callers; `/classification-policy` is a further GET outside `_respond`.
- Frontend (codemie-ui) change: unverified, outside this repository.

### Consumers of Changed Contracts
- `_respond` header contract: consumed by all `_respond` callers above and the browser/UI (outside repo). No in-repo test asserts `Cache-Control` or `ETag` for CLI Analytics (grep of `tests/codemie/rest_api/routers/test_cli_analytics*.py`: no matches for Cache-Control/ETag/is_local).
- `tests/codemie/rest_api/routers/test_analytics.py` references Cache-Control/ETag/`_create_response` (legacy helper); would be affected only if that helper is changed.
- `config.is_local` is read from `config` module imported in the router.

### Architecture and Layers Affected
- API/router layer only: `src/codemie/rest_api/routers/cli_analytics.py` (`_respond`). Handler/service/repository untouched.

### Integration Points
- Browser HTTP cache (client, outside repo); `dashboard_cache` in the handler layer (server-side, independent of response headers).

### Patterns and Conventions
- Single response helper per router; Pydantic response model validates then dumps by alias; routes protected by `_admin_gate` dependency (e.g. `:601-606`).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/cli-analytics-storage.md` (dashboard cache setting at line 41; no mention of Cache-Control/ETag found), `.ai-run/guides/integration/cli-analytics-events.md`, `.ai-run/guides/testing/testing-api-patterns.md`, `.ai-run/guides/api/endpoint-conventions.md` (not read in depth).

### Architectural Decisions
- None found for HTTP caching headers.

### Derived Conventions
- Router tests live in `tests/codemie/rest_api/routers/test_cli_analytics_*.py`, one file per concern.

---

## 4. Testing Landscape

### Existing Coverage
- `test_cli_analytics_dashboard_cache.py`: calls the route functions directly with a mocked handler (`_handler`, `_call`, patch/AsyncMock, `ViewerScope` fixtures) and a `_COST` payload; covers server-side cache hits, scope isolation, TTL 0.
- Other `test_cli_analytics_*.py` files cover gates, timeouts, funding, ingest, overview flags.

### Testing Framework and Patterns
- pytest (+ pytest-asyncio style `async def` tests), `unittest.mock` patching of router module attributes (`router_module`), `from __future__ import annotations`.

### Coverage Gaps
- No test for `_respond` headers (Cache-Control, ETag), local vs non-local, or conditional requests, for CLI Analytics.

---

## 5. Configuration and Environment

### Environment Variables
- `CLI_ANALYTICS_DASHBOARD_CACHE_TTL_S` (default 30; 0 disables) - server-side cache only.

### Configuration Files
- `config.is_local` (config module) selects header branch in `_respond`.

### Feature Flags and Deployment Concerns
- `cliAnalytics` feature flag gates the endpoints (`_ensure_enabled`). No proxy/CDN config for these headers found in the repo.

---

## 6. Risk Indicators

- Speculative: the ETag changes per request because `metadata.timestamp`/`execution_time_ms` are in the hashed body (`cli_analytics.py:496-503, 514`); any conditional-request test asserting 304 on unchanged data would need that accounted for.
- Speculative: `/classification-policy` (`:183`) is a GET outside `_respond`; "every GET route" test would cover it too.
- Legacy `_create_response` (`analytics/_common.py:127`) keeps `public, max-age=300`; existing `test_analytics.py` references it.
- Server-side 30 s dashboard cache (`dashboard_cache.py`) remains a staleness source for `/users`, `/user-charts`, `/cost`; the task keeps it in place (see Task Statements).
- No existing header tests for the CLI Analytics router; tests call route functions directly rather than through HTTP, so header assertions on responses need either `_respond` unit calls or a TestClient.
- Frontend behaviour unverified (outside repo).

---

## 7. Summary for Complexity Assessment

The header behaviour sits in one function, `_respond()` in `src/codemie/rest_api/routers/cli_analytics.py` (lines 506-515), called by all 12 CLI Analytics read routes. The router layer is the only layer involved; handlers, repositories, and the 30 s per-pod `dashboard_cache` are not touched by the symptom. A legacy sibling helper in `analytics/_common.py` has the same pattern but is named as out of scope.

No novel patterns. The ETag already hashes the full returned body but includes per-request timing metadata; no code reads `If-None-Match`. Test posture: no existing tests of CLI Analytics response headers; adjacent router tests (dashboard cache) call route functions with a mocked handler and offer reusable fixtures. New tests would cover header assertions per route, local vs non-local, and conditional requests.

Key risks: the metadata-in-ETag fact, `/classification-policy` outside `_respond`, the legacy helper and its tests, and server-side cache staleness that the task keeps.

---

## 8. External References

None named by the task (commit fa1bfc246 and the frontend repo codemie-ui were mentioned but not as sources to read; the frontend is outside this repository).
