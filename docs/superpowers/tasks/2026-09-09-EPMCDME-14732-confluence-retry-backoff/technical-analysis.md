# Technical Research

**Task**: confluence datasource loader retry backoff
**Generated**: 2026-09-09T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

The Confluence loader aborts the entire datasource indexing process when a transient 504 Gateway Timeout occurs during Confluence crawling. The loader should handle transient upstream errors (HTTP 504) by retrying the failed request with a safe retry/backoff strategy before marking the indexing process as failed. Key files: codemie/datasource/loader/confluence_loader.py (_search_content_by_cql, lazy_load), codemie/datasource/base_datasource_processor.py (_load_and_process_documents, _process, process). The fix should add retry/backoff to _search_content_by_cql and include mock-based tests for successful retry, exhausted retries, and non-retryable errors.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/datasource/loader/confluence_loader.py` — `ConfluenceDatasourceLoader` extends `ConfluenceLoader` (langchain-community) and `BaseDatasourceLoader`. `_search_content_by_cql` is the HTTP boundary (calls `self.confluence.get(next_url)` or `self.confluence.get(url, params=params)`). `lazy_load` iterates pages via `_search_content_by_cql`, following the `_links.next` pagination cursor. This is the only method that performs HTTP calls in the pagination loop and is the correct injection point.
- `src/codemie/datasource/base_datasource_processor.py` — Already imports `tenacity` and uses `@retry` with `STORAGE_CONFIG` knobs on `_process_document`. This is the canonical pattern to follow: `stop_after_attempt`, `wait_exponential`, `retry_if_exception`, `before_sleep_log`, `reraise=True`.
- `src/codemie/datasource/datasources_config.py` — `StorageConfig` dataclass owns `indexing_max_retries=2`, `indexing_error_retry_wait_min_seconds=10`, `indexing_error_retry_wait_max_seconds=120`. Loaded via `STORAGE_CONFIG` singleton.
- `config/datasources/datasources-config.yaml` — Runtime values for the three retry knobs. No new config fields needed.

### Architecture and Layers Affected
- **Datasource loader layer** (`confluence_loader.py`) — primary change site; `@retry` decorator on `_search_content_by_cql`.
- **Configuration layer** (`datasources_config.py`, `datasources-config.yaml`) — consumed read-only; no changes required.
- **Test layer** (`tests/codemie/datasource/loader/test_confluence_loader.py`) — new test class appended.
- No changes to API routes, service layer, repository layer, or Elasticsearch store.

### Integration Points
- `atlassian-python-api` Confluence client — raises `requests.exceptions.HTTPError` for non-2xx responses; `exc.response.status_code` carries the HTTP code.
- `tenacity` — already a declared dependency; `retry_if_exception` (predicate form) needed to discriminate retryable (502/503/504) from non-retryable (4xx) errors.
- `STORAGE_CONFIG` — singleton imported from `codemie.datasource.datasources_config`; provides retry tuning without new env vars.
- `base_datasource_processor._load_and_process_documents` — iterates `loader.lazy_load()`; unhandled exceptions propagate upward and abort indexing. Retry at `_search_content_by_cql` is the correct guard point.

### Patterns and Conventions
- Retry decorator: `@retry(stop=stop_after_attempt(...), wait=wait_exponential(multiplier=2, min=..., max=...), retry=retry_if_exception(<predicate>), reraise=True, before_sleep=before_sleep_log(logger, logging.WARNING))` — matches `base_datasource_processor.py` exactly.
- Predicate: a module-level `_is_transient_http_error(exc)` function checking `isinstance(exc, requests.exceptions.HTTPError) and exc.response.status_code in {502, 503, 504}`.
- Logging: use the existing `codemie.configs.logger` (already imported in the loader file).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/confluence-integration.md` — confirms `ConfluenceDatasourceLoader` is the correct component; no router involvement for loader-level errors.
- `.ai-run/guides/development/error-handling.md` — log + reraise pattern confirmed; distinct status code ranges must be handled distinctly (4xx = caller error, do not retry; 5xx gateway = retryable).
- `.ai-run/guides/testing/testing-patterns.md` — test file location mirrors `src/` under `tests/codemie/`; pytest + `unittest.mock`; test class per feature.

### Architectural Decisions
- Retry tuning lives exclusively in `StorageConfig` / `datasources-config.yaml`; inline magic numbers are not the pattern.
- `reraise=True` is required — after exhausting retries, the original exception must propagate so the indexing pipeline can log a structured failure.

### Derived Conventions
- `retry_if_exception` (predicate form) is used over `retry_if_exception_type` when the retry condition depends on instance state (i.e., response status code), not just exception class.
- `multiplier=2` in `wait_exponential` is consistent with the existing processor retry config.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/datasource/loader/test_confluence_loader.py` — existing suite covers `lazy_load` pagination and `fetch_remote_stats`; no coverage for error/retry paths.

### Testing Framework and Patterns
- pytest (declared in `pyproject.toml`); `unittest.mock.MagicMock` and `patch` for mocking.
- Per acceptance criteria: mock-based tests only; no live Confluence instance required.
- Tests must patch `tenacity`'s internal sleep (`tenacity.nap.time.sleep`) or set `wait=wait_none()` via `retry.statistics` override, because the `@retry` decorator is evaluated at class definition time and `STORAGE_CONFIG` values are baked in.

### Coverage Gaps
- Successful retry after initial 504 — not covered.
- Exhausted retries with graceful failure and logging — not covered.
- Non-retryable errors (400, 401) failing immediately — not covered.

---

## 5. Configuration and Environment

### Environment Variables
- No new env vars required. Retry knobs come from `datasources-config.yaml` → `STORAGE_CONFIG`.

### Configuration Files
- `config/datasources/datasources-config.yaml` — `indexing_max_retries: 2`, `indexing_error_retry_wait_min_seconds: 10`, `indexing_error_retry_wait_max_seconds: 120`. No changes needed.

### Feature Flags and Deployment Concerns
- No feature flags. Change is purely additive (decorator wrapping existing method).
- `stop_after_attempt(2)` means 2 total attempts (1 original + 1 retry) with current yaml value. If the gateway flap lasts longer than one retry, the failure will still propagate — acceptable given the configurable knob.

---

## 6. Risk Indicators

- **Decorator baked at class definition time**: `@retry(...)` arguments referencing `STORAGE_CONFIG` are evaluated once when the module loads. Tests patching `STORAGE_CONFIG` after import will not affect the decorator. Tests must patch `tenacity.nap.time.sleep` to skip waits, or use `retry.statistics` / `wait_none()` override.
- **`fetch_remote_stats` uses `confluence.cql()` not `confluence.get()`**: that code path is not covered by this change. If it also experiences 504 errors, a separate fix is required.
- **`stop_after_attempt(2)` is a low ceiling**: with a 10s min backoff, worst-case retry window is ~10–120s. If the Confluence gateway flap is longer, indexing will still fail. This is a known limitation of the existing config, not introduced by this change.
- **`reraise=True` is required**: without it, tenacity swallows the final exception and returns `None`, breaking the caller's exception handling and making failures silent.
- **502/503 added alongside 504**: aligns with the "transient gateway error" semantic; the ticket names 504 but 502/503 have identical semantics and are safer to handle consistently.

---

## 7. Summary for Complexity Assessment

The change is narrowly scoped to `confluence_loader.py`: add a module-level `_is_transient_http_error` predicate and a `@retry` decorator to `_search_content_by_cql`. No new dependencies, no new config fields, and no changes to the API, service, or database layers. The tenacity pattern and `STORAGE_CONFIG` retry knobs already exist in `base_datasource_processor.py`, making this a direct reuse — not a novel pattern introduction. File change surface is two files: the loader (source) and its test file.

Test coverage requires three new test cases: successful retry after 504, exhausted retries with graceful failure, and non-retryable 400/401 failing immediately. The main testing risk is that the `@retry` decorator is evaluated at class definition time, so tests must patch `tenacity.nap.time.sleep` rather than `STORAGE_CONFIG`. This is a known tenacity testing pattern and does not materially increase complexity.

Risk factors are low. The `fetch_remote_stats` method's `confluence.cql()` call is not covered by this change (out of scope per the ticket). The retry ceiling of 2 attempts is configurable via existing yaml knobs. No breaking changes to existing callers — `lazy_load` behavior is unchanged on success paths; on exhausted retry the same `HTTPError` propagates as before, just after the retry window.
