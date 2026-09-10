# Spec: Confluence Loader Retry/Backoff for Transient 504 Errors

**Ticket**: EPMCDME-14732
**Slug**: EPMCDME-14732-confluence-retry-backoff
**Complexity**: XS (6/36)

---

## Problem

`ConfluenceDatasourceLoader._search_content_by_cql` makes HTTP calls via the Atlassian client (`self.confluence.get`). A transient 504 Gateway Timeout propagates as `requests.exceptions.HTTPError` through `lazy_load` → `base_datasource_processor._load_and_process_documents`, aborting the entire indexing run on the first transient failure.

## Solution

Add a `@retry` decorator to `_search_content_by_cql` that retries only on transient gateway errors (HTTP 502, 503, 504) with exponential backoff, using the existing `STORAGE_CONFIG` tuning knobs. Non-retryable errors (4xx) fail immediately.

## Design

### Predicate

A module-level function gates retries on status code:

```python
def _is_transient_http_error(exc: BaseException) -> bool:
    return (
        isinstance(exc, requests.exceptions.HTTPError)
        and getattr(exc.response, "status_code", None) in {502, 503, 504}
    )
```

This uses `retry_if_exception` (predicate form, not `retry_if_exception_type`) so 4xx errors fail immediately without retrying.

### Decorator

```python
@retry(
    stop=stop_after_attempt(STORAGE_CONFIG.indexing_max_retries),
    wait=wait_exponential(
        multiplier=2,
        min=STORAGE_CONFIG.indexing_error_retry_wait_min_seconds,
        max=STORAGE_CONFIG.indexing_error_retry_wait_max_seconds,
    ),
    retry=retry_if_exception(_is_transient_http_error),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
def _search_content_by_cql(self, ...):
    ...
```

`reraise=True` ensures the original `HTTPError` propagates after retries are exhausted, preserving existing failure-handling in the processor.

### Configuration

Retry tuning comes from `STORAGE_CONFIG` (no new config fields):
- `indexing_max_retries` — total attempt count (default: 2)
- `indexing_error_retry_wait_min_seconds` — minimum backoff wait (default: 10s)
- `indexing_error_retry_wait_max_seconds` — maximum backoff wait (default: 120s)

### Pattern reference

Matches the `@retry` pattern in `base_datasource_processor.py` (lines 934–944), with the key difference that `retry_if_exception` (predicate) replaces `retry_if_exception_type(Exception)` to avoid retrying 4xx errors.

## Files Changed

| File | Change |
|---|---|
| `src/codemie/datasource/loader/confluence_loader.py` | Add imports, `_is_transient_http_error` predicate, `@retry` decorator on `_search_content_by_cql` |
| `tests/codemie/datasource/loader/test_confluence_loader.py` | Append `TestSearchContentByCqlRetry` class with 3 mock-based tests |

## Acceptance Criteria

1. `_search_content_by_cql` retries on HTTP 502, 503, 504 before propagating failure.
2. Retry uses exponential backoff with `STORAGE_CONFIG` knobs.
3. Non-retryable errors (4xx) fail immediately on the first attempt.
4. After retries are exhausted, the original `HTTPError` is reraised.
5. Successful retry after initial 504 allows indexing to continue.
6. Three mock-based tests cover: successful retry, exhausted retries, non-retryable error.
7. Existing tests remain green.

## Out of Scope

- `fetch_remote_stats` (`confluence.cql()` path) — separate ticket if needed.
- Retry tuning changes — existing YAML values are sufficient.
