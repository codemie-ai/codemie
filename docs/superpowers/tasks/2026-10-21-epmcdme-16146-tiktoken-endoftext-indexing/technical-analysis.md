# Technical Research

**Task**: datasource indexing tokens tiktoken monitoring-callback
**Generated**: 2026-10-21
**Research path**: codegraph

---

## 1. Original Context

EPMCDME-16146 — Git code datasource indexing fails on a tiktoken end-of-text special token.
Summary: Git code datasource indexing fails when repository content contains the tiktoken end-of-text special-token string (ASCII '<' '|endoftext|' '>'). The datasource monitoring callback estimates embedding tokens by passing document content to `calculate_tokens`; `tiktoken.encode` raises ValueError (disallowed special token). Run fails in `datasource_monitoring_callback.on_split_documents` -> `calculate_tokens` (src/codemie/core/utils.py) -> `tiktoken.encode`; datasource_reindex_errors_total with error_class ValueError; ASGI exception logged. Repo https://github.com/codemie-ai/codemie, embeddings model amazon.titan-embed-text-v2:0.
Expected: content processed without token estimation aborting indexing; indexing completes.
Acceptance criteria:
1. Indexing a Git code datasource whose document content includes the tiktoken end-of-text special-token string does not fail during token estimation.
2. The affected repository can be indexed without this ValueError.
3. Token estimation remains functional for ordinary document content.
4. A regression test covers document content containing the end-of-text special-token string in the indexing path.
Affected: backend Git/code datasource indexing & reindexing; document-splitting monitoring callback and token estimation.
Known hints (verify, don't trust): other unsafe `encoding.encode(str(...))` call sites exist (conversation_service.py ~907, service/mcp/toolkit.py ~277, datasource/code/docs_generation_service.py ~54, codemie_tools/base/codemie_tool.py ~273/291); precedent for safe usage: service/script_tool_calls/token_count.py uses `disallowed_special=()`. Existing test file: tests/codemie/datasource/callback/test_datasource_monitoring_callback.py.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/core/utils.py:190` `calculate_tokens(text, llm_model=llm_service.default_llm_model)`: `get_encoding(llm_model)` then `len(encoding.encode(str(text)))`. No `disallowed_special` argument, so tiktoken's default (`"all"`) raises ValueError on special-token text. Confirmed root cause. 19 callers (agents/callbacks/monitoring_callback.py, datasource_monitoring_callback.py, workflows/memory_utils.py, build_embedding_llm_run in the same file, others).
- `src/codemie/datasource/callback/datasource_monitoring_callback.py:67` `on_split_documents`: only when `self.request_uuid` is set, `self._estimated_embedding_tokens += sum(calculate_tokens(str(doc.page_content)) for doc in docs)`. The estimate is used only in the last-resort fallback in `on_complete`/`on_error` (when no request_summary data exists).
- `src/codemie/datasource/file/file_datasource_processor.py:359` calls `callback.on_split_documents(docs)` for each callback; `src/codemie/datasource/base_datasource_processor.py` (`_split_documents` :601, `_setup_processing_context` :270, `_notify_callbacks_on_complete` :294) also notifies callbacks. `on_error` records `datasource_*_errors_total` with `error_class`.
- `src/codemie_tools/base/utils.py:175` `get_encoding(llm_model)`: `tiktoken.encoding_for_model`, falling back to `o200k_base`. Returns a raw tiktoken Encoding.
- Safe precedent: `src/codemie/service/script_tool_calls/token_count.py:32` `count_tokens` uses `encode(text, disallowed_special=())`. Its tests (`tests/codemie/service/script_tool_calls/test_token_count.py`) include `test_special_token_text_is_counted_not_rejected` (`count_tokens("<|endoftext|>") > 0`) and an equality test against `calculate_tokens` for ordinary text.
- The file-datasource splitter already passes `disallowed_special={}` to `RecursiveCharacterTextSplitter.from_tiktoken_encoder` (file_datasource_processor.py:199), so the splitter itself does not raise.

Other `encode(str(...))` sites (verified, all default-unsafe):
- `src/codemie/service/conversation_service.py:905-907` `ConversationService.calculate_tokens` (classmethod, gpt-4.1-mini encoding). Its blast radius: `history_compaction_service.py` (7 callers).
- `src/codemie/service/mcp/toolkit.py:276-277` `encoding.encode(str_output)` in the MCP tool `_limit_output_content`.
- `src/codemie/datasource/code/docs_generation_service.py:53-56` `_limit_output_content`: `encode(str(output))`, then `decode` of the truncated tokens.
- `src/codemie_tools/base/codemie_tool.py:270-274` `calculate_tokens_count` and `:289-292` `_limit_output_content` (both `encode(str(output))`; the latter also truncates with decode).

### Architecture and Layers Affected
- Datasource processing layer (`src/codemie/datasource/`): callbacks (`DatasourceProcessorCallback` -> `DatasourceMonitoringCallback`) and the base/file processors that invoke them.
- Core utilities (`src/codemie/core/utils.py`), which is shared by agents, workflows and conversation compaction.
- Shared tools library (`src/codemie_tools/base/`) for the other call sites.

### Integration Points
- `calculate_tokens` -> `codemie_tools.base.utils.get_encoding` -> tiktoken.
- `DatasourceMonitoringCallback` -> `request_summary_manager`, `llm_service.get_embeddings_model_cost`, `DatasourceMonitoringService` (metrics).
- Git code processing reaches the callback through `BaseDatasourceProcessor` (`_setup_processing_context` instantiates `DatasourceMonitoringCallback`).

### Patterns and Conventions
- Callbacks are stubs on `DatasourceProcessorCallback` that subclasses override. Callback exceptions in `on_split_documents` are not guarded in the code seen.
- Token counting uses `get_encoding(model)` with an `o200k_base` fallback. The precedent for special-token-safe counting is `disallowed_special=()`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
Guides exist under `.ai-run/guides/`. Relevant: `testing/testing-patterns.md`, `testing/testing-service-patterns.md`, `development/error-handling.md`, `standards/code-quality.md`. They were not read in detail; no guide covers token counting specifically.

### Architectural Decisions
None found for token-counting policy. Inline precedent only: token_count.py docstring says it counts "the way the platform counts the output of a direct tool call".

### Derived Conventions
Special-token text is treated as plain text for counting (`disallowed_special=()`) in the script-tool path and in the splitter (`disallowed_special={}`).

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/datasource/callback/test_datasource_monitoring_callback.py`: class `TestDatasourceMonitoringCallback`; tests such as `test_on_complete_with_tokens_usage`, `..._without_tokens_usage`, `test_on_error_with_tokens_usage`, `..._without_tokens_usage`. Uses fixtures `mock_index_info` (IndexInfo, index_type="code"), `mock_user`, `mock_tokens_usage`, `mock_request_uuid`, with `@patch` of `request_summary_manager` and `DatasourceMonitoringService` methods. Only the first 80 lines were read; no `on_split_documents` test was seen and I did not confirm one is absent.
- `tests/codemie/service/script_tool_calls/test_token_count.py`: covers `count_tokens`, including special-token text.
- `tests/codemie_tools/base/test_codemie_tool.py`: covers CodeMieTool (via get_encoding blast radius).
- `tests/codemie/service/conversation/test_history_compaction_service.py` indirectly covers `ConversationService.calculate_tokens`.
- `tests/codemie/service/test_request_summary_manager.py`, `tests/codemie/agents/callbacks/test_tokens_callback.py`.

### Testing Framework and Patterns
pytest; unittest.mock `patch`/`patch.object`; class-based tests plus plain functions with parametrize.

### Coverage Gaps
- No direct test of `calculate_tokens` in core/utils.py was found other than via test_token_count.py's equality check (ordinary text).
- No test found (not exhaustively verified) for `on_split_documents` with special-token content, `DocsGenerationService._limit_output_content`, or the MCP `_limit_output_content` with special-token text.

---

## 5. Configuration and Environment

### Environment Variables
No env var governs token counting. Adjacent: `config.MCP_TOOL_TOKENS_SIZE_LIMIT` (MCPTool.tokens_size_limit).

### Configuration Files
`FILE_CONFIG` / `JSON_CONFIG` (chunk_size, chunk_overlap) used by the file splitter. Datasource concurrency settings (`MAX_CONCURRENT_DATASOURCE_INDEXING`, `DATASOURCE_CONCURRENCY_LIMIT_ENABLED`, `DATASOURCE_QUEUE_TIMEOUT`) are unrelated.

### Feature Flags and Deployment Concerns
None identified. The embeddings model in the report (amazon.titan-embed-text-v2:0) is not a tiktoken-known model, so `get_encoding` falls back to `o200k_base` via the `get_encoding` exception handler; the token count is therefore an estimate only.

---

## 6. Risk Indicators

- `calculate_tokens` has 19 callers; any change in its encode semantics affects agent/workflow/compaction counting (blast radius across `monitoring_callback.py`, `memory_utils.py`, etc.). Counts for ordinary text would be unchanged by `disallowed_special=()`; only text containing special-token strings differs (previously raised).
- Four other sites with identical defect exist (conversation_service, mcp/toolkit, docs_generation_service, codemie_tool x2). docs_generation_service and codemie_tool also `decode` truncated tokens; this is the same encoding, so the behavior difference is limited to special-token text.
- docs_generation_service is part of Git code indexing (docs generation), so it is plausibly reachable in the same repository flow; not traced.
- `on_split_documents` has no exception guard; any future estimator error would again abort indexing (an estimate feeding only a fallback metric).
- Only the first 80 lines of the callback test file were read; the existence of an `on_split_documents` test was not confirmed.
- Speculative: whether to fix at `calculate_tokens` (central) vs only the callback, and whether to touch the other sites, is a scope decision for spec/plan.

---

## 7. Summary for Complexity Assessment

The defect is isolated to one line: `calculate_tokens` in `src/codemie/core/utils.py` calls `encoding.encode(str(text))` with tiktoken's default special-token check, and `DatasourceMonitoringCallback.on_split_documents` calls it for every split document whenever `request_uuid` is set. The layers involved are datasource callbacks and core utilities. A safe precedent (`disallowed_special=()` in `script_tool_calls/token_count.py`, with a passing special-token test) exists in the repo, so no technical novelty.

The change surface for the reported symptom is small (one function, one test file). The hinted related sites (conversation_service, mcp/toolkit, docs_generation_service, codemie_tool) are verified to have the same unguarded pattern; including them widens the surface to about 5 source files and their tests, all with the same trivial change.

Test posture: the callback has a pytest file with fixtures for IndexInfo/User/request_uuid, and token_count has a model special-token test to copy. The main risk is the 19-caller blast radius of `calculate_tokens`, mitigated by unchanged results for ordinary text.

---

## 8. External References

None named by the task.
