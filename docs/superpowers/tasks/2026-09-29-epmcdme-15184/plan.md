# EPMCDME-15184 Confluence transient ConnectionError retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Retry transient `requests.exceptions.ConnectionError` (not SSLError) and 502/503/504 on the CQL search and on the restrictions call, with actionable exhaustion errors.

**Architecture:** In `src/codemie/datasource/loader/confluence_loader.py`, widen the predicate, build one shared tenacity decorator (existing STORAGE_CONFIG stop/wait, `before_sleep_log`), apply it to `_search_content_by_cql` and to a new `is_public_page` override. Exhaustion callback logs the exception class and raises `ConfluenceRetryExhaustedError` (`from` the original) so `index.set_error(str(ex))` records the class name and attempts.

**Tech Stack:** Python 3.12, tenacity, requests, pytest.

**Spec:** `docs/superpowers/tasks/2026-09-29-epmcdme-15184/spec.md`

Commit per task using the repository's existing convention.

## Global Constraints

- No new config keys or env vars; no change to `indexing_max_retries` or backoff; no changes to `base_datasource_processor.py`.
- Not retried: 401/403 and other HTTPError, `SSLError`, `ReadTimeout`/`Timeout` not also ConnectionError.
- Out of scope: comments/attachments/`confluence.request`/`fetch_remote_stats` retries.
- Tests patch `tenacity.nap.time.sleep`; retry args bind at import, so patch module-level values as existing tests do.

## Review Focus

- `SSLError` (a ConnectionError subclass) must not retry (tested in Task 1 predicate test).
- `ConnectTimeout` is both ConnectionError and Timeout and should retry (Task 1).
- Non-transient error on first attempt is raised unwrapped, original type (Tasks 1, 2).
- `is_public_page` returns the same bool as upstream after a retry (Task 2).

Caller check (done at planning time): the only `except HTTPError` in `src/codemie/datasource` around Confluence is `confluence_datasource_processor.py:459` in `validate_creds_and_loader`, which calls the unretried `fetch_remote_stats`. The retried paths (`lazy_load` / `is_public_page`) have no HTTPError matcher, so wrapping on exhaustion is safe. Task 1 re-greps `except HTTPError|except .*ConnectionError` across `src/` to confirm.

---

### Task 1: Predicate, exhaustion error, CQL search

**Files:**
- Modify: `src/codemie/datasource/loader/confluence_loader.py:29-48,64-74`
- Test: `tests/codemie/datasource/loader/test_confluence_loader.py` (extend `TestSearchContentByCqlRetry`, l.426+)

**Interfaces:**
- Produces: `_is_transient_error(exc) -> bool` (keep `_is_transient_http_error` name as an alias if existing tests import it); `class ConfluenceRetryExhaustedError(Exception)`; `_confluence_retry` (a `tenacity.retry(...)` decorator instance reused by Task 2).

- [ ] **Step 1: Write failing tests.** Predicate parametrized: True for HTTPError 502/503/504, ConnectionError, ConnectTimeout, ConnectionError wrapping `ProtocolError(RemoteDisconnected)`; False for HTTPError 401/403, SSLError, ReadTimeout, ValueError. CQL search: ConnectionError once then success (2 calls); ConnectionError always -> raises `ConfluenceRetryExhaustedError` whose `str()` contains `ConnectionError` and the attempt count, `__cause__` is the original, ERROR log has class and "exhausted"; 401 and SSLError raise the original after 1 call. Update existing exhaustion tests that assert the raised type is HTTPError / log has HTTP status.
- [ ] **Step 2: Run** `python -m pytest tests/codemie/datasource/loader/test_confluence_loader.py -q`; expect new tests FAIL.
- [ ] **Step 3: Implement.** Widen the predicate at l.32-36 (`isinstance ConnectionError and not SSLError`, or the existing HTTPError status check). Rewrite `_log_and_reraise_exhausted` (l.39-48) to log `type(exc).__name__`, attempts, fn name (keep HTTP status when present) and `raise ConfluenceRetryExhaustedError(f"{type(exc).__name__} after {n} attempts (retries exhausted): {exc}") from exc`. Hoist the l.64-74 decorator args into `_confluence_retry` and apply it to `_search_content_by_cql`. Grep `src/` for other `except HTTPError` matchers on this path and confirm none (see caller check above).
- [ ] **Step 4: Run** the test file; expect all PASS (baseline 34 plus new).

### Task 2: Retry the restrictions call via `is_public_page`

**Files:**
- Modify: `src/codemie/datasource/loader/confluence_loader.py` (new method on `ConfluenceDatasourceLoader`)
- Test: `tests/codemie/datasource/loader/test_confluence_loader.py` (new `TestIsPublicPageRetry`; uses the `TestLazyLoadRetryIntegration` fixture at l.621+ but with `include_restricted_content=False`)

**Interfaces:**
- Consumes: `_confluence_retry` from Task 1.
- Produces: `ConfluenceDatasourceLoader.is_public_page(self, page: dict) -> bool`.

- [ ] **Step 1: Write failing tests** with `mock_confluence_client.get_all_restrictions_for_content` as a side-effect list: ConnectionError(RemoteDisconnected) once then valid restrictions -> same bool as upstream, 2 calls; HTTP 504 then success; ConnectionError always -> `ConfluenceRetryExhaustedError` with class name in `str()`, exactly `indexing_max_retries` calls; 403 and SSLError -> raised unwrapped after 1 call. Continuation: through `lazy_load` with 3 pages (`status: current`) where the second page's restrictions call fails once, assert all 3 pages are yielded.
- [ ] **Step 2: Run** the new class; expect FAIL (no retry today, 1 call).
- [ ] **Step 3: Implement.** Read upstream `langchain_community/document_loaders/confluence.py` `is_public_page` (~l.530-541, in the venv site-packages). Override it decorated with `@_confluence_retry`, keeping identical semantics (the `status != "current"` short-circuit and the boolean result) and calling `self.confluence.get_all_restrictions_for_content(page["id"])`. Preferred: move only the network call into a small decorated helper and delegate the rest to `super().is_public_page` if that keeps behavior identical; otherwise copy the upstream body.
- [ ] **Step 4: Run** `python -m pytest tests/codemie/datasource/loader/test_confluence_loader.py -q`; expect all PASS.
