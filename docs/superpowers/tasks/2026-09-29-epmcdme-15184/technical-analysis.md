# Technical Research

**Task**: confluence datasource loader retry connection error
**Generated**: 2026-09-29
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-15184 (Bug, Major): Confluence loader does not retry ConnectionError / RemoteDisconnected and aborts indexing. The Confluence datasource loader retries only HTTP 502/503/504. Transient connection-level failures (requests.exceptions.ConnectionError caused by http.client.RemoteDisconnected wrapped in urllib3.exceptions.ProtocolError) terminate the whole indexing process; error propagates through confluence_loader.py and base_datasource_processor.py, e.g. from get_all_restrictions_for_content. Retry logic should be extended to treat relevant connection-level exceptions as retryable while preserving 502/503/504 behavior. Expected: request retried per configured retry policy (existing retry limit + backoff); if later attempt succeeds indexing continues; if all attempts fail, indexing failure reported with clear diagnostic logging including exception type and retry exhaustion. Acceptance Criteria: 1) 502/503/504 retry without regression. 2) Transient requests.exceptions.ConnectionError (connection aborts / remote disconnects) retried. 3) RemoteDisconnected wrapped in ProtocolError surfaced as ConnectionError triggers configured retry policy. 4) When a retry succeeds the Confluence indexing job continues and processes remaining pages. 5) When all retries exhausted the job records an actionable error containing exception class and retry-exhaustion context. 6) Automated tests cover 502/503/504 retry, ConnectionError caused by RemoteDisconnected, continuation after a retry, and final failure after retry exhaustion. 7) Non-transient errors such as authentication/authorization failures are NOT retried.
Already-verified findings you may build on (verify, do not just trust): src/codemie/datasource/loader/confluence_loader.py has a tenacity @retry only on ConfluenceDatasourceLoader._search_content_by_cql (predicate _is_transient_http_error on HTTPError status in CONFLUENCE_CONFIG.retry_transient_status_codes; retry_error_callback _log_and_reraise_exhausted logs only HTTP status). The failing call get_all_restrictions_for_content is made by upstream langchain_community 0.4.1 ConfluenceLoader.is_public_page() from process_pages() (only when include_restricted_content is False and page status=="current") and has no retry. base_datasource_processor.py @retry(Exception) (~line 936) wraps only _process_document (chunk storage); outer indexing except Exception calls index.set_error(str(ex)) and re-raises. Existing tests: tests/codemie/datasource/loader/test_confluence_loader.py (TestSearchContentByCqlRetry, TestLazyLoadRetryIntegration); no tests cover restrictions call or ConnectionError. Reproduction guide: /mnt/c/Users/AleksandrBudanov/Projects/EPMCDME-15184/EPMCDME-15184-repro-wsl.md. Also investigate: other loaders in src/codemie/datasource/loader with similar retry helpers to reuse; how process_pages/process_page/other Confluence API calls (attachments, comments, labels) are invoked and whether they share the same exposure; where CONFLUENCE_CONFIG / STORAGE_CONFIG retry settings are defined and documented (docs/ , config/ , env).

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/datasource/loader/confluence_loader.py` (144 lines): `ConfluenceDatasourceLoader(ConfluenceLoader, BaseDatasourceLoader)`.
  - Module-level `_TRANSIENT_HTTP_STATUS_CODES` frozenset from `CONFLUENCE_CONFIG.retry_transient_status_codes` (captured at import).
  - `_is_transient_http_error(exc)`: True only for `requests.exceptions.HTTPError` with status in that set.
  - `_log_and_reraise_exhausted(retry_state)`: logs "Retries exhausted for <fn> after N attempts - last HTTP status: <code>" (status only, `unknown` for non-HTTP exceptions; no exception class) then `raise exc`.
  - Only `@retry` in the file is on `_search_content_by_cql` (lines 64-74): `stop_after_attempt(STORAGE_CONFIG.indexing_max_retries)`, `wait_exponential(multiplier, min, max)` from STORAGE_CONFIG, `retry_if_exception(_is_transient_http_error)`, `retry_error_callback=_log_and_reraise_exhausted`, `before_sleep_log(logger, WARNING)`.
  - `lazy_load()` (100-144) loops CQL search via `next_url`, calls upstream `self.process_pages(...)` with the loader's flags. `fetch_remote_stats` calls `self.confluence.cql(...)` with no retry.
- Upstream `langchain_community/document_loaders/confluence.py` (0.4.1, read from a container-layer venv copy; not in the repo): `is_public_page` (l.530) calls `self.confluence.get_all_restrictions_for_content(page["id"])` (l.536); `process_pages` (l.543) calls `is_public_page` per page when `not include_restricted_content` (l.557). Other unretried per-page network calls in `process_page`/helpers: `get_page_comments` (l.618, include_comments), `get_attachments_from_content` (l.666, include_attachments), `self.confluence.request(path=link, absolute=True)` in process_pdf/image/doc/xls/svg (l.721-862). Upstream also has `number_of_retries/min_retry_seconds/max_retry_seconds` attributes (tenacity use around get_page_by_id / page-list fetches at l.~436 only, not restrictions). All these calls share the same exposure as the restrictions call.
- `src/codemie/datasource/base_datasource_processor.py` ~l.934-960: `@retry(stop=indexing_max_retries, wait_exponential, retry_if_exception_type(Exception), reraise=True, before_sleep_log(ERROR, exc_info=True))` on `_process_document` only (chunk storage). Outer indexing `except Exception` calls `index.set_error(str(ex))` and re-raises (per task finding; line ~1047 references `max_retries` in error context).
- `src/codemie/datasource/confluence_datasource_processor.py`: `_initialize_confluence_loader` builds the loader; `_parse_confluence_docs` transforms docs.
- Other loaders' retry helpers (candidates to mirror, none shared/reusable as-is):
  - `xwiki_loader.py` ~l.233-251: hand-rolled bounded single retry on timeout/429/5xx, 4xx never retried, `_MAX_ATTEMPTS`.
  - `sharepoint_loader.py` `_make_graph_request` l.411-452: recursive manual retry using `SHAREPOINT_CONFIG.max_retries` (401 refresh, 429 Retry-After, generic exception).
  - The Confluence tenacity pattern is the only one using STORAGE_CONFIG backoff.

### Architecture and Layers Affected
Datasource loader layer (`ConfluenceDatasourceLoader`), datasource config layer (`datasources_config.py`, YAML), and the indexing processor layer (`base_datasource_processor.py`, error recording via `IndexInfo.set_error`).

### Integration Points
- `atlassian` Confluence client (`self.confluence`, via `codemie_tools` config) over `requests`/`urllib3`; exceptions surface as `requests.exceptions.ConnectionError(ProtocolError(RemoteDisconnected))`.
- Upstream `langchain_community` ConfluenceLoader (methods overridden here: `_search_content_by_cql`, `lazy_load`).

### Patterns and Conventions
- tenacity `@retry` with config-driven stop/wait, predicate via `retry_if_exception`, `before_sleep_log`, custom exhausted callback that re-raises.
- Retry params are bound at import time (decorator args evaluated at class definition), so config changes need restart.
- Predecessor task EPMCDME-14732 (`docs/superpowers/tasks/2026-09-09-EPMCDME-14732-confluence-retry-backoff/`) introduced the current retry; its spec/plan/decisions are the best design precedent.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `AGENTS.md` mandates guides under `.ai-run/guides/`; `.ai-run/guides/integration/confluence-integration.md` exists but grep found no mention of retry/transient/429/timeout in it.
- Other guides listed by AGENTS.md relevant here: `development/error-handling.md`, `development/logging-patterns.md`, `development/configuration-patterns.md`, `testing/testing-patterns.md` (not read in detail).

### Architectural Decisions
- Retry settings are documented only by inline comments in `config/datasources/datasources-config.yaml` (l.139 `retry_transient_status_codes: [502, 503, 504]`; l.189-192 `indexing_max_retries: 2` (total attempts, not retries; 2 = 1 retry), `indexing_error_retry_wait_min_seconds: 10`, `..._max_seconds: 120`, `..._multiplier: 2`). The l.189 comment notes loader_timeout (180s) is per attempt and stale_indexing_threshold_seconds is 300s.
- Model fields: `datasources_config.py` l.79 (`CONFLUENCE_CONFIG.retry_transient_status_codes`, default `[502,503,504]`), l.156-159 (STORAGE_CONFIG retry fields). No env var or `docs/` doc for them beyond task docs from 14732.
- CHANGELOG.md has no Confluence retry entry (grep returned nothing).

### Derived Conventions
Retry logic sits in the loader; the processor only records failure. Docstring/comment style notes config-capture-at-import.

---

## 4. Testing Landscape

### Existing Coverage
`tests/codemie/datasource/loader/test_confluence_loader.py` (34 passing per repro guide):
- `TestFetchRemoteStats`, `TestSearchContentByCql`, `TestLazyLoad`, `TestLazyLoadIntegration`.
- `TestSearchContentByCqlRetry` (l.426+): 504-then-success, exhaustion, non-retryable immediate failure, next_url variants, sleep/no-sleep, warning log, final error log, predicate test, wiring to STORAGE_CONFIG / CONFLUENCE_CONFIG.
- `TestLazyLoadRetryIntegration` (l.621+): loader fixture with patched `ConfluenceLoader.__init__`, `include_restricted_content=True`, mock client; tests recover-from-504, exhaustion mid-pagination, non-retryable mid-pagination.

### Testing Framework and Patterns
pytest + `unittest.mock`; `mock_confluence_client` MagicMock fixture; `patch("tenacity.nap.time.sleep")` to skip backoff; `requests.exceptions.HTTPError(response=MagicMock(status_code=N))` helper; `caplog`/logger patching for log assertions.

### Coverage Gaps
- No test of `get_all_restrictions_for_content` / `is_public_page` / `include_restricted_content=False` path (existing integration fixture forces it True).
- No test with `requests.exceptions.ConnectionError`, `ProtocolError` or `RemoteDisconnected`.
- No tests of comments/attachments/labels network calls under failure; no tests for `fetch_remote_stats` failure.
- Processor-level error recording for loader failure is not covered in this file.

---

## 5. Configuration and Environment

### Environment Variables
None found specific to Confluence retry; values come from `config/datasources/datasources-config.yaml` loaded into `CONFLUENCE_CONFIG` / `STORAGE_CONFIG` (`datasources_config.py`). (Repro uses `CONFLUENCE_PERSONAL_TOKEN` only for the manual script.)

### Configuration Files
- `config/datasources/datasources-config.yaml` (l.139, l.189-192), `src/codemie/datasource/datasources_config.py` (l.79, l.156-159; `indexing_max_retries`, wait min/max are required fields without defaults, multiplier defaults to 2).

### Feature Flags and Deployment Concerns
- No feature flag. `deploy-templates/` was searched and has no retry settings.
- Retry knobs are import-time bound.

---

## 6. Risk Indicators

- Ticket premise partly wrong: 502/503/504 are retried only on the CQL search call. Restriction/comments/attachments calls are not retried at all (repro scenario C), so fixing requires new retry wrapping around upstream-invoked calls, not merely a wider predicate.
- Speculative: wrapping `is_public_page` (override in `ConfluenceDatasourceLoader`) is the smallest seam for the failing call; comments/attachments/`request()` calls live inside upstream `process_page` and would need method overrides or wrapping `self.confluence` methods.
- `indexing_max_retries: 2` means a single retry; "configured retry policy" is thin and worst-case backoff is 120s per retry; loader_timeout 180s and stale threshold 300s interplay (yaml comment l.189).
- `_log_and_reraise_exhausted` logs only HTTP status; AC5 needs exception class in log and in the recorded error. `index.set_error(str(ex))` records only the message, so exception class must be in the raised/logged text.
- Predicate design: `ConnectionError` also covers DNS failure, refused connections, and `requests.exceptions.SSLError` (a ConnectionError subclass); `ConnectTimeout` is both ConnectionError and Timeout. AC7 requires 401/403 (HTTPError) stay non-retried. Need a decision on SSLError and timeouts.
- Config-captured-at-import means new config knobs (if any) need restart; tests must patch module-level values.
- Retried GETs are idempotent; the shared tenacity decorator may get reused across several methods (each gets separate retry budget, which multiplies worst-case wall time).
- Upstream package in venv is outside repo; behaviour of upstream methods depends on pinned `langchain-community==0.4.1`.
- Minimal docs: confluence-integration guide and CHANGELOG do not describe retries.

---

## 7. Summary for Complexity Assessment

The change is concentrated in one loader file (`confluence_loader.py`) plus optionally the config model/YAML and one test file. Layers touched: datasource loader (retry predicate, new retry-wrapped call sites, richer exhaustion logging) and possibly config. `base_datasource_processor.py` already records errors via `set_error(str(ex))` and re-raises, so it likely needs no change beyond ensuring the message carries the exception class. The tenacity pattern is already in the file (precedent from EPMCDME-14732), so technical novelty is low.

The main non-trivial aspect is the seam: the failing call is inside upstream `langchain_community` `is_public_page`, so an override of `is_public_page` (or a wrapped client method) is required, and sibling calls (comments, attachments, `request`) share the exposure, which is a scope decision. Retry-predicate boundaries (ConnectionError vs SSLError/timeouts, keeping 401/403 unretried) need care.

Test posture is good for the search retry (established fixtures, sleep patching) but the restrictions path and ConnectionError/ProtocolError chain have zero coverage and the integration fixture forces `include_restricted_content=True`, so new fixtures are required. A runtime-verified repro exists (real proxy and offline variants). Estimated as a small-to-medium bug fix.

---

## 8. External References

Path named by task: `/mnt/c/Users/AleksandrBudanov/Projects/EPMCDME-15184/EPMCDME-15184-repro-wsl.md` - resolved and read. Key facts:
- Runtime-verified on WSL2, Python 3.12 (project needs >=3.12,<3.14), `langchain-community==0.4.1`. Baseline `python -m pytest tests/codemie/datasource/loader/test_confluence_loader.py -q` = 34 passed. Run with `PYTHONPATH=src`.
- Root cause confirmed: only `_search_content_by_cql` has retry; restriction call unretried; reached only with `include_restricted_content=False` and page `status=="current"`.
- Exact error: `requests.exceptions.ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))`.
- Offline scenarios observed today: A ConnectionError once then success -> aborts (1 restriction call); B always -> aborts (1 call); C HTTP 504 once on restriction call -> aborts (1 call); D 504 once on CQL search -> retried (2 calls).
- Expected after fix: `once` -> success with `restr_seen=2`; `always` -> fails after `indexing_max_retries` (2) attempts with exception class and retry exhaustion in log.
- Repro uses a local fault-injecting proxy that closes the socket on `/restriction` requests, against real kb.epam.com (needs `CONFLUENCE_PERSONAL_TOKEN`); tests patch `tenacity.nap.time.sleep`.
