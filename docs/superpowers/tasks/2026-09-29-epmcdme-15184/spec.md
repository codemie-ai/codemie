# EPMCDME-15184: Confluence loader retries transient connection errors

## Problem

`ConfluenceDatasourceLoader` (`src/codemie/datasource/loader/confluence_loader.py`) has retry only on `_search_content_by_cql` (l.64-74), and only for HTTP 502/503/504. A dropped connection (`requests.exceptions.ConnectionError` wrapping `urllib3 ProtocolError(RemoteDisconnected)`) is never retried. The ticket's premise that 502/503/504 are already retried holds only for the CQL search call.

The failing call is `get_all_restrictions_for_content`, made by upstream `langchain_community` 0.4.1 `ConfluenceLoader.is_public_page()` from `process_pages()`. It has no retry at all. It runs only when `include_restricted_content=False` (the CodeMie API default is True, which skips it) and the page status is `current`. The CQL search runs for every datasource.

On failure, `process()` raises and `index.set_error(str(ex))` stores text such as `('Connection aborted.', RemoteDisconnected(...))`. That text has no exception class name, and indexing aborts (`error=true`, `completed=false`).

Repro: `/mnt/c/Users/AleksandrBudanov/Projects/EPMCDME-15184/EPMCDME-15184-repro-wsl.md` and `EPMCDME-15184-repro-real-kb-epam.md`.

## Behavior

1. **Retryable predicate.** The predicate, `_is_transient_http_error` or a successor, returns true for:
   - `HTTPError` with status in `CONFLUENCE_CONFIG.retry_transient_status_codes` (502/503/504, unchanged);
   - `requests.exceptions.ConnectionError`, except `SSLError`. This includes `ConnectTimeout`, refused connections, DNS failures, and `RemoteDisconnected` wrapped in `ProtocolError`.

   It returns false for everything else: 401/403 and other `HTTPError`, `SSLError`, `ReadTimeout`/`Timeout` that is not also a ConnectionError, and non-requests exceptions.
2. **Call sites.**
   - The existing CQL search uses the widened predicate.
   - `is_public_page` is overridden in `ConfluenceDatasourceLoader` so that the `get_all_restrictions_for_content` call is retried with the same predicate.
   - No other call site changes.
3. **Policy.** The retry policy is unchanged: the same `STORAGE_CONFIG` attempt limit (`indexing_max_retries`, total attempts, so 2 means one retry) and the same exponential backoff, with no new config knobs.
4. **Continuation.** If a retried attempt succeeds, processing continues with the remaining pages. A successful retry adds no error state.
5. **Exhaustion.** Applies to both call sites. When the attempts are used up:
   - An ERROR log states the exception class, the attempt count and that retries were exhausted. This replaces the HTTP-status-only message.
   - The exception that propagates has a `str()` containing the exception class name and the retry-exhaustion context (attempts), with the original as `__cause__`. Because `base_datasource_processor.py` calls `index.set_error(str(ex))`, this puts the class name into the recorded error without touching the processor.
6. **Non-transient errors** are raised on the first attempt with no retry and no exhaustion wrapping.

## Acceptance criteria

1. 502/503/504 on the CQL search and on the restrictions call are retried, with no regression on the existing search tests.
2. A transient `requests.exceptions.ConnectionError` is retried on both call sites.
3. `RemoteDisconnected` wrapped in `ProtocolError` and surfaced as `ConnectionError` triggers the configured attempt limit and backoff.
4. After a successful retry, indexing continues and the remaining pages are processed.
5. On exhaustion, the log and the error text reaching `index.set_error` contain the exception class and the retry-exhaustion context.
6. Tests are added in `tests/codemie/datasource/loader/test_confluence_loader.py`, with `tenacity.nap.time.sleep` patched and `include_restricted_content=False`:
   - the restrictions call: ConnectionError once then success; ConnectionError every time gives exhaustion with the class name; HTTP 504 then success;
   - the CQL search: ConnectionError retried;
   - the existing 502/503/504 tests still pass;
   - continuation after a retry (the following pages are processed).
7. 401/403 and `SSLError` are not retried, on either call site. Each has a test.

## Non-goals

- Retry for the other unretried Confluence calls: comments, attachments, and `confluence.request` for pdf/image/doc/xls/svg. Also `fetch_remote_stats`. Follow-up ticket.
- Retrying `ReadTimeout`/`Timeout`.
- Retrying `SSLError`.
- New config keys or env vars, or changes to `indexing_max_retries` or the backoff values.
- Changes to `base_datasource_processor.py` or `IndexInfo.set_error`.
- Changing the CodeMie API default of `include_restricted_content`.
- Changes to the `langchain-community` pin or vendoring upstream.
- Retry helpers shared with the xwiki or sharepoint loaders.

## Risks and notes

- The retry decorator arguments are bound at import time, so tests must patch module-level values, as the existing tests do.
- Wrapping the exhausted exception changes its type for the exhaustion path only. Callers matching on `HTTPError` there would be affected. The plan must check for such callers, or keep the type and carry the context in the message.
- The `is_public_page` override must preserve upstream semantics: return the same boolean, and give up only on non-transient errors.
