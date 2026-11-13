# CLI Analytics Cache-Control Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Stop browsers serving stale CLI Analytics KPIs for 5 minutes (EPMCDME-17191).

**Architecture:** One choke point, `_respond()` in `src/codemie/rest_api/routers/cli_analytics.py:506-515`. Non-local header changes from `private, max-age=300` to `private, no-cache`; ETag stays; local stays `no-store`.

**Tech Stack:** FastAPI, pytest (route functions called directly with mocked handler).

Commit per task using the repository's existing convention (ticket EPMCDME-17191).

## Global Constraints

- Backend only. Do NOT add `If-None-Match` / 304 handling.
- Do NOT touch `dashboard_cache.py` (30s cache), rollups, frontend, or `analytics/_common.py::_create_response`.

## Review Focus

- Old ETag sent in `If-None-Match` after data changes: must be 200 with new body, never 304.
- `/classification-policy` bypasses `_respond`: its headers must not carry `max-age>0`.
- Local mode must remain `no-store` with no ETag.

---

### Task 1: Change `_respond` Cache-Control

**Files:**
- Modify: `src/codemie/rest_api/routers/cli_analytics.py:513`
- Create: `tests/codemie/rest_api/routers/test_cli_analytics_cache_headers.py`

Test-first: yes — non-local `_respond` returns `Cache-Control: private, no-cache` (currently `private, max-age=300`, so the assertion fails).

- [ ] **Step 1:** Write tests calling `_respond(payload, model_class)` directly (reuse a response model and minimal payload as in `test_cli_analytics_dashboard_cache.py`), patching `router_module.config.is_local`:
  - non-local: `Cache-Control == "private, no-cache"`, `"max-age"` absent, `ETag` present (64 hex chars).
  - local: `Cache-Control == "no-store"`, no `ETag`.
  - data change: two payloads differing in data; build a request-like check with `If-None-Match` = first ETag; the second response is `status_code == 200`, body is the new body, ETag differs (`_respond` takes no request, so 304 is impossible; assert status 200 and body equality).
- [ ] **Step 2:** Run `pytest tests/codemie/rest_api/routers/test_cli_analytics_cache_headers.py -v`; expect the non-local test to FAIL.
- [ ] **Step 3:** In `_respond`, replace the string `"private, max-age=300"` with `"private, no-cache"`; keep the ETag line unchanged.
- [ ] **Step 4:** Re-run the file; expect PASS. Also run `pytest tests/codemie/rest_api/routers/test_cli_analytics_dashboard_cache.py`.

### Task 2: Route-level header regression across all GET routes

**Files:**
- Modify: `tests/codemie/rest_api/routers/test_cli_analytics_cache_headers.py`

Test-first: no — regression coverage that passes once Task 1 lands.

- [ ] **Step 1:** Add a parametrized test that calls each `_respond`-based GET route function directly with a mocked handler (pattern: `_handler`, `_call`, AsyncMock, `ViewerScope` fixtures from `test_cli_analytics_dashboard_cache.py`): overview, cost, users, user-charts, repositories, projects, tools, activity, efficiency, frameworks, sessions, session detail (route functions near `cli_analytics.py:601-995`). With `is_local` False assert the `Cache-Control` has no `max-age` greater than 0 and contains `no-cache`; with `is_local` True assert `no-store`.
- [ ] **Step 2:** Add a test for `classification_policy()` (`cli_analytics.py:183`): it returns a plain dict (no Response headers), so assert the result is a `dict` and that no `max-age` is set (it relies on FastAPI defaults; document this in the test). Do not change the route.
- [ ] **Step 3:** Run the test file and `make ruff`; expect PASS.

---

negative-constraints: honored — no 304/If-None-Match handling (Task 1 only asserts 200); dashboard cache, frontend and legacy `_create_response` untouched (Global Constraints; no task modifies them); `/classification-policy` left unchanged (Task 2).
