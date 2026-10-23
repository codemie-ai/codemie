# EPMCDME-16146: Git code datasource indexing must tolerate tiktoken special-token text

## Problem

Indexing a Git code datasource aborts with `ValueError` when repository content contains the tiktoken
end-of-text special-token string. `DatasourceMonitoringCallback.on_split_documents`
(`src/codemie/datasource/callback/datasource_monitoring_callback.py:67`) calls `calculate_tokens`
(`src/codemie/core/utils.py:190`) for every split document when `request_uuid` is set. That function calls
`encoding.encode(str(text))` with tiktoken's default `disallowed_special="all"`, which raises on the string.
The error surfaces as `datasource_reindex_errors_total{error_class="ValueError"}`. The estimate only feeds the
last-resort fallback in `on_complete` / `on_error`, so it should never be able to abort indexing.

A second, lower-likelihood site has the same defect. `DocsGenService._limit_output_content`
(`src/codemie/datasource/code/docs_generation_service.py:52-57`) encodes `str(output)`. Its input is folder
name + file list + the first 500 chars of LLM-generated summaries / sub-READMEs, not raw file contents. It runs
only when `index.docs_generation` is on, in `_on_process_end`. When over `TOKEN_LIMIT` it truncates the token
list and decodes it back.

## Approach

Treat special-token text as plain text when counting, as already done in
`src/codemie/service/script_tool_calls/token_count.py:32` and in the file splitter
(`file_datasource_processor.py:199`).

1. `calculate_tokens`: pass `disallowed_special=()` to `encode`. Counts for ordinary text are unchanged; only
   text that used to raise is now counted. This fixes the 19 callers centrally.
2. `DocsGenService._limit_output_content`: pass `disallowed_special=()` to `encode`. The truncate-then-decode
   step must keep working for text containing the marker: decoding the truncated tokens returns the marker as
   literal text and never raises, so the returned string is still a valid prefix of the input.
3. `DatasourceMonitoringCallback.on_split_documents`: wrap the token estimate in `try/except Exception`, log a
   warning (no document content in the log message), and leave `_estimated_embedding_tokens` unchanged for
   that call. A metrics-estimate failure must never abort indexing.

Ruled out: guarding only the callback (leaves 18 other callers and the docs-generation site raising);
changing `get_encoding` to return a wrapper (wider blast radius for no gain).

## Acceptance criteria

1. Indexing a Git code datasource whose document content contains the end-of-text special-token string does not
   fail during token estimation.
2. The affected repository (codemie-ai/codemie) indexes without this `ValueError`.
3. `calculate_tokens` returns the same counts as before for ordinary text.
4. `calculate_tokens` on text containing the marker returns a positive count and does not raise.
5. `DocsGenService._limit_output_content` does not raise on text containing the marker, and when over the limit
   returns a correctly truncated string.
6. If the token estimate in `on_split_documents` raises any exception, the callback logs a warning, keeps the
   previous estimate and does not propagate the error.
7. Regression tests cover: `calculate_tokens` with the marker; `on_split_documents` with a document containing
   the marker (estimate increases, no raise); `on_split_documents` when the estimator raises (no propagation,
   estimate unchanged); `_limit_output_content` with the marker, both under and over `TOKEN_LIMIT`.

## Non-goals

- Other unsafe `encode()` sites: `conversation_service.py` (`ConversationService.calculate_tokens`),
  `service/mcp/toolkit.py` (`_limit_output_content`), `codemie_tools/base/codemie_tool.py`
  (`calculate_tokens_count`, `_limit_output_content`). Mention them in the MR description as follow-up.
- No change to `get_encoding`, the model-to-encoding fallback (`o200k_base`), or the splitter.
- No change to how the estimate is used in `on_complete` / `on_error`, or to metrics names and labels.
- No change to `script_tool_calls/token_count.py`.
- No new configuration, feature flag, or env var.

## Testing

Extend `tests/codemie/datasource/callback/test_datasource_monitoring_callback.py` (fixtures `mock_index_info`,
`mock_request_uuid`) for the callback tests. Add `calculate_tokens` tests alongside core utils tests, modelled on
`test_special_token_text_is_counted_not_rejected` in `tests/codemie/service/script_tool_calls/test_token_count.py`.
Add a `_limit_output_content` test for `DocsGenService`.
