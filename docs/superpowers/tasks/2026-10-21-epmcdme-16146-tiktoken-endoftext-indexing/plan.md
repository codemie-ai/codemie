# Tiktoken End-of-Text Tolerance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Git code datasource indexing no longer aborts when content contains the tiktoken end-of-text marker.

**Architecture:** Treat special-token text as plain text via `disallowed_special=()` (precedent: `src/codemie/service/script_tool_calls/token_count.py:32`), and make the callback estimate non-fatal.

**Tech Stack:** Python, tiktoken, pytest.

**Spec:** `docs/superpowers/tasks/2026-10-21-epmcdme-16146-tiktoken-endoftext-indexing/spec.md`

Commit per task using the repository's existing convention.

## Global Constraints

- No change to `get_encoding`, the splitter, `script_tool_calls/token_count.py`, metrics names/labels, or config.
- Other unsafe `encode()` sites (conversation_service, mcp/toolkit, codemie_tool) are out of scope.
- Marker string in tests: `"<|endoftext|>"`.

## Review Focus

- Marker in the middle of ordinary text (not only alone) is counted.
- `_limit_output_content` truncation over `TOKEN_LIMIT` with the marker before the cut still returns a valid prefix.
- Warning log in the callback must not contain document content.

---

### Task 1: calculate_tokens accepts special-token text

**Files:**
- Modify: `src/codemie/core/utils.py:190` (`encode(str(text))` call)
- Test: `tests/codemie/core/test_calculate_tokens.py` (new; model on `test_special_token_text_is_counted_not_rejected` in `tests/codemie/service/script_tool_calls/test_token_count.py`)

Test-first: yes — `test_calculate_tokens_counts_endoftext_marker` fails with ValueError before the change.

- [ ] Write tests: `calculate_tokens("<|endoftext|>") > 0`; `calculate_tokens("a <|endoftext|> b") > calculate_tokens("a  b")`; ordinary text equals `len(get_encoding(default model).encode(text))`. Confirm the marker tests fail.
- [ ] Pass `disallowed_special=()` to `encode`; confirm tests pass.

### Task 2: DocsGenService._limit_output_content accepts marker

**Files:**
- Modify: `src/codemie/datasource/code/docs_generation_service.py:52-57`
- Test: `tests/codemie/datasource/code/test_docs_generation_service.py`

Test-first: yes — `test_limit_output_content_with_endoftext_marker` fails with ValueError before the change.

- [ ] Tests: marker text under `TOKEN_LIMIT` returns unchanged; over the limit (patch `TOKEN_LIMIT` to a small value, marker before the cut) returns a string that is a prefix of the input and does not raise.
- [ ] Pass `disallowed_special=()` to `encode`; the decode step stays as is.

### Task 3: on_split_documents never aborts indexing

**Files:**
- Modify: `src/codemie/datasource/callback/datasource_monitoring_callback.py:67` (`on_split_documents`)
- Test: `tests/codemie/datasource/callback/test_datasource_monitoring_callback.py` (fixtures `mock_index_info`, `mock_request_uuid`)

Test-first: yes — `test_on_split_documents_swallows_estimator_error` fails because the exception propagates.

- [ ] Tests: (a) doc containing the marker, `request_uuid` set: `_estimated_embedding_tokens` increases, no raise; (b) patch `codemie.datasource.callback.datasource_monitoring_callback.calculate_tokens` to raise `ValueError`: no propagation, estimate unchanged, warning logged (patch `logger`) and message lacks the document content.
- [ ] Wrap the estimate in `try/except Exception`; compute the sum first, then add, so a failure leaves the previous estimate. Log with `logger.warning` using the exception class name only, no content.

## Negative-constraint pass

Honored: no change to `get_encoding`/splitter/token_count.py/metrics/config (only Tasks 1-3 files touched); other unsafe sites untouched (note them as follow-up in the MR description).
