# EPMCDME-13933 Marketplace KB Indexing: Chunking — Implementation Plan

> **For agentic workers:** sdlc-standard drives this plan inline, with TDD in the current session. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Index every published assistant in full. Long ones are split into token-bounded chunks, not truncated. One failing assistant never fails the `marketplace_assistants` index, and the index never holds stale or duplicate content.

**Architecture:**
- `AssistantLoader` turns one assistant into N chunks. Every chunk repeats the identifying header; the body (description, system prompt[:1000], tools) is split with the repo's tiktoken splitter.
- `BasePlatformDatasourceProcessor` stores chunks grouped by assistant, with per-assistant fallback. After each store it deletes that assistant's stale documents, and unpublish deletes all of an assistant's chunks.
- `SearchAndRerankMarketplace` keeps the best-ranked chunk per assistant.

**Tech Stack:** Python 3.12, LangChain (`Document`, `RecursiveCharacterTextSplitter`, `ElasticsearchStore`), Elasticsearch `delete_by_query`, tiktoken, pytest + unittest.mock.

**Spec:** `docs/superpowers/tasks/2026-10-06-epmcdme-13933-marketplace-kb-indexing-context-length/spec.md`

**Supersedes** the truncation design in commits `cab3f7c8c` and `579d7ec4b`. The truncation code and its tests are replaced. The resilience, `fail_fast` and log-key fixes are kept and adapted.

## Global Constraints

- Splitter: `RecursiveCharacterTextSplitter.from_tiktoken_encoder(encoding_name="o200k_base", chunk_size=1500, chunk_overlap=100, disallowed_special=())`.
- `BODY_CHUNK_TOKENS = 1500`, `BODY_CHUNK_OVERLAP_TOKENS = 100`.
- `ASSISTANT_PROMPT_MAX_LENGTH = 1000` (characters) stays.
- Chunk metadata keys: `id`, `name`, `source`, `popularity_score`, `chunk_num`.
- ES document id: `f"{metadata['id']}_{metadata['chunk_num']}"`.
- Stale cleanup and unpublish query on `metadata.id.keyword`. The mapping is verified as text with a keyword subfield.
- Batch size: `STORAGE_CONFIG.embeddings_max_docs_count` chunks.
- Logging `extra` must not use reserved LogRecord keys (`name`, `msg`, `args`, …). Use `entity_name`.
- Run tests with `poetry run pytest -o addopts="--import-mode=importlib" …`, because pytest-xdist is absent from the local env.

## Review Focus

1. **Republishing an assistant with a shorter description:** the extra old chunks must disappear. Task 2, `test_stale_cleanup_query_excludes_new_ids`.
2. **Legacy single documents** (ES id = assistant id) present at deploy: removed by the cleanup on the first write. Same test, because the must_not filter lists only the new chunk ids.
3. **Unpublishing an assistant with N chunks:** all N are removed, and progress drops by N. Task 3.
4. **One assistant with more chunks than the batch size:** stored as its own batch, not split across batches. Task 2, `test_large_group_forms_own_batch`.
5. **Empty description, or no tools:** still exactly one chunk with the header. Task 1, `test_short_assistant_single_chunk`.

---

### Task 1: Loader produces chunked documents

Test-first: yes. `test_long_description_split_into_chunks_without_loss` fails because the loader returns a single truncated document.

**Files:**
- Modify: `src/codemie/datasource/loader/platform/platform_loader.py` (abstract `_entity_to_documents`, `load_single_entity -> list`, `lazy_load` yields every chunk)
- Modify: `src/codemie/datasource/loader/platform/assistant_loader.py`:
  - remove the truncation constants, `_truncate_to_tokens` and `_get_token_encoding`
  - add `BODY_CHUNK_TOKENS`, `BODY_CHUNK_OVERLAP_TOKENS`, `_build_header`, `_build_body`, `_split_body`, `_entity_to_documents`
  - `_build_metadata` gains `chunk_num`
  - `load_single_entity` returns a list
- Test: `tests/codemie/datasource/loader/test_assistant_loader.py`. Replace the `TestAssistantLoaderTokenBudget` class with `TestAssistantLoaderChunking`, and update the existing `_entity_to_document` and `load_single_entity` tests to the list API.

**Interfaces:**
- Produces:
  - `AssistantLoader._entity_to_documents(assistant) -> list[Document]`
  - `AssistantLoader.load_single_entity(assistant_id) -> list[Document]`
  - chunk metadata `{"id", "name", "source", "popularity_score", "chunk_num"}`

- [ ] **Step 1: Write failing tests.** Same `_make_sanitized` helper as before. Build documents via `loader._sanitized_to_documents(sanitized)`, a thin helper that `_entity_to_documents` calls after `_sanitize_entity`.
  - `test_short_assistant_single_chunk`: 1 document; `chunk_num == 0`; contains `Name: Big Assistant`, `Description: Helps with docs`, `Tools: tool_name_0, tool_name_1`, `Categories: developer`, `Created by: System`. Also check that with `description=""` and no toolkits there is still 1 chunk.
  - `test_long_description_split_into_chunks_without_loss`:
    - The description is `" ".join(f"w{i}" for i in range(20000))`.
    - Assert more than 1 chunk, `chunk_num` values `0..N-1`, and every chunk ≤ 2000 o200k tokens.
    - Every chunk starts with the header (`Name: Big Assistant` and `Page url with details: /#/assistants/asst_big`).
    - `"w19999"` appears in some chunk, and `"w0 "` appears in chunk 0.
  - `test_long_tools_list_fully_present`: with 3000 tools, `"tool_name_2999"` appears in some chunk, and every chunk is ≤ 2000 tokens.
  - `test_system_prompt_capped`: a 5000-character prompt; the joined content contains exactly its first 1000 characters and not character 1001 onward.
  - `test_metadata_keys`: the keys of each chunk equal `{"id", "name", "source", "popularity_score", "chunk_num"}`.
  - Update the existing tests: `_entity_to_document` → `_entity_to_documents(...)[0]`. `load_single_entity` returns a list on success and `[]` instead of `None`.
- [ ] **Step 2: Run tests and confirm RED.** Run `poetry run pytest -o addopts="--import-mode=importlib" tests/codemie/datasource/loader/test_assistant_loader.py -q`.
- [ ] **Step 3: Implement.**

```python
    BODY_CHUNK_TOKENS = 1500
    BODY_CHUNK_OVERLAP_TOKENS = 100

    def _build_header(self, sanitized: AssistantDocument) -> str:
        parts = [f"Name: {sanitized.name}"]
        if sanitized.slug:
            parts.append(f"Slug: {sanitized.slug}")
        parts += [f"Page url with details: /#/assistants/{sanitized.id}", f"Project: {sanitized.project}"]
        if sanitized.categories:
            parts.append(f"Categories: {', '.join(sanitized.categories)}")
        toolkit_names, _ = self._toolkit_and_tool_names(sanitized)
        if toolkit_names:
            parts.append(f"Toolkits: {', '.join(toolkit_names)}")
        self._add_creator(parts, sanitized)
        self._add_usage_stats(parts, sanitized)
        return "\n\n".join(parts)

    def _build_body(self, sanitized: AssistantDocument) -> str:
        parts = [
            f"Description: {sanitized.description}",
            f"System Prompt: {sanitized.system_prompt[: self.ASSISTANT_PROMPT_MAX_LENGTH]}",
        ]
        _, tool_names = self._toolkit_and_tool_names(sanitized)
        if tool_names:
            parts.append(f"Tools: {', '.join(tool_names)}")
        return "\n\n".join(parts)

    def _split_body(self, body: str) -> list[str]:
        splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            encoding_name="o200k_base",
            chunk_size=self.BODY_CHUNK_TOKENS,
            chunk_overlap=self.BODY_CHUNK_OVERLAP_TOKENS,
            disallowed_special=(),
        )
        return splitter.split_text(body) or [body]

    def _sanitized_to_documents(self, sanitized: AssistantDocument) -> list[Document]:
        header = self._build_header(sanitized)
        chunks = self._split_body(self._build_body(sanitized))
        return [
            Document(page_content=f"{header}\n\n{chunk}", metadata=self._build_metadata(sanitized, chunk_num))
            for chunk_num, chunk in enumerate(chunks)
        ]
```

  - `_toolkit_and_tool_names` wraps the existing `_extract_toolkits_info` over `sanitized.toolkits`; it replaces `_add_toolkits`.
  - `_build_page_content` is removed. The existing tests that used it move to `_sanitized_to_documents`.
  - `lazy_load` in the base class becomes `for entity in self.entities: yield from self._entity_to_documents(entity)`.
  - Update the docstrings.
- [ ] **Step 4: Run the loader tests and confirm GREEN.**
- [ ] **Step 5: Commit.** `EPMCDME-13933: Chunk marketplace assistant documents instead of truncating`

---

### Task 2: Processor stores chunks per assistant, with stale cleanup

Test-first: yes. `test_stale_cleanup_query_excludes_new_ids` fails because no cleanup exists and ids equal the assistant id.

**Files:**
- Modify: `src/codemie/datasource/platform/base_platform_processor.py`:
  - `_add_documents` (grouping, batching, `fail_fast`)
  - `_store_batch_with_fallback`, which becomes group-based
  - new `_chunk_doc_id`, `_group_by_entity`, `_delete_stale_chunks`
  - `index_single_entity` uses the list API
- Test: `tests/codemie/datasource/platform_tests/test_base_platform_processor.py`. Adapt the existing tests: `_doc(entity_id, chunk_num=0)`, and the expected ids become `"a_0"`.

**Interfaces:**
- Consumes the Task 1 chunk metadata (`id`, `chunk_num`, `name`).
- Produces:
  - `_chunk_doc_id(doc) -> str`
  - `_delete_stale_chunks(entity_ids: list[str], keep_ids: list[str]) -> None`

- [ ] **Step 1: Write failing tests.**
  - `test_chunk_ids_include_chunk_num`: chunks `a#0` and `a#1` → `store.add_documents(..., ids=["a_0", "a_1"])`.
  - `test_stale_cleanup_query_excludes_new_ids`: storing `a#0`, `a#1` and `b#0` triggers `processor.client.delete_by_query` with a body whose filter is `{"terms": {"metadata.id.keyword": ["a", "b"]}}` and whose must_not is `{"ids": {"values": ["a_0", "a_1", "b_0"]}}`, plus `conflicts="proceed"`.
  - `test_large_group_forms_own_batch`: batch size 2; `a` has 3 chunks and `b` has 1. The `add_documents` calls are `[["a_0","a_1","a_2"], ["b_0"]]`.
  - `test_batch_failure_skips_whole_assistant`: `bad` has 2 chunks and the second raises. `a` and `c` are stored. Progress is called twice, with `processed_file` values `["name a", "name c"]`. One warning is logged, with `extra["entity_id"] == "bad"`. The cleanup runs only for the stored assistants.
  - `test_cleanup_failure_logged_not_raised`: `delete_by_query` raises, and `_add_documents` still returns with progress moved.
  - `test_progress_counts_chunks_per_assistant`: `move_progress(chunks_count=2, processed_file="name a")`.
  - Keep and adapt: all-fail with no raise; the real-logger test; update propagates; publish propagates; publish moves progress once on success.
  - `index_single_entity` with `load_single_entity` returning `[]` logs a warning and returns.
- [ ] **Step 2: Run and confirm RED.**
- [ ] **Step 3: Implement.** Group in order:

```python
@staticmethod
def _group_by_entity(documents):
    groups: dict[str, list[Document]] = {}
    for doc in documents:
        groups.setdefault(doc.metadata.get("id", str(uuid4())), []).append(doc)
    return list(groups.values())
```

Then:
- Pack groups into batches up to `embeddings_max_docs_count` chunks; a group bigger than that becomes its own batch.
- `_store_batch_with_fallback(store, groups) -> list[list[Document]]`: try the whole batch; on failure, retry per group; log `_log_skipped_document(group[0], entity_id, error)`.
- After each successful store: `_delete_stale_chunks([entity ids], [chunk ids])`, inside try/except with a logged warning.
- Progress: one `move_progress(chunks_count=len(group), processed_file=name)` per stored group.
- `fail_fast` / `is_update` path: store all chunks directly, then clean up; progress per group only when `not is_update`.
- `index_single_entity`: `documents = self.loader.load_single_entity(entity_id)`. If it is empty, warn and return; otherwise `_add_documents(documents, is_update=is_update, fail_fast=True)`.
- [ ] **Step 4: Run the platform tests and confirm GREEN.**
- [ ] **Step 5: Commit.** `EPMCDME-13933: Store marketplace chunks per assistant and remove stale chunks`

---

### Task 3: Unpublish removes all chunks

Test-first: yes. `test_remove_single_entity_deletes_all_chunks` fails because the code deletes by ES id.

**Files:**
- Modify: `base_platform_processor.py`, `remove_single_entity`
- Test: `tests/codemie/datasource/platform_tests/test_assistant_datasource_processor.py`. Update the existing two remove tests and add one.

- [ ] **Step 1: Write failing tests.**
  - `client.delete_by_query` returns `{"deleted": 3}`. Assert the body is `{"query": {"term": {"metadata.id.keyword": "asst_1"}}}` with `conflicts="proceed"` and `refresh=True`, and that `decrease_progress(chunks_count=3, processed_file="Name")` is called.
  - With `{"deleted": 0}`, there is no `decrease_progress`, a warning is logged, and nothing raises.
  - When an ES error is raised, it re-raises, as today.
- [ ] **Step 2: Confirm RED.**
- [ ] **Step 3: Implement** the `delete_by_query` and the deleted-count handling.
- [ ] **Step 4: Confirm GREEN.**
- [ ] **Step 5: Commit.** `EPMCDME-13933: Remove all marketplace chunks on unpublish`

---

### Task 4: Search returns one result per assistant

Test-first: yes. `test_execute_dedupes_chunks_per_assistant` fails because duplicates pass through.

**Files:**
- Modify: `src/codemie/service/search_and_rerank/marketplace.py`, `execute`
- Test: `tests/codemie/service/search_and_rerank/test_marketplace.py`

- [ ] **Step 1: Failing test.**
  - The base `execute` returns `[a#1 (pop 0.5), b#0 (pop 0.9), a#0 (pop 0.5), x (no id, pop 0.1)]`.
  - Expected: `[b#0, a#1, x]`. The first `a` chunk wins, and the result is sorted by popularity.
  - The existing tests use documents without an id; they must still pass.
- [ ] **Step 2: Confirm RED.**
- [ ] **Step 3: Implement.** Keep the first document per `metadata.get("id")`; documents with no id are always kept. Then apply the existing sort.
- [ ] **Step 4: Confirm GREEN.**
- [ ] **Step 5: Commit.** `EPMCDME-13933: Return one marketplace search result per assistant`

---

### Task 5: Lint, regression and live check

Test-first: no. This task is verification.

- [ ] `poetry run ruff format && poetry run ruff check --fix && poetry run ruff check`, and the license check.
- [ ] `poetry run pytest -o addopts="--import-mode=importlib" tests/codemie/datasource tests/codemie/service/platform tests/codemie/service/search_and_rerank tests/codemie/agents/tools/kb tests/codemie/rest_api/routers/test_assistant_marketplace.py -q`. The pre-existing environment failures in the git/svn loaders and multiprocessing are known.
- [ ] Rebuild the backend image, then run the live check on the local stack (VPN needed for embeddings):
  - reindex and confirm the 14 legacy documents become chunks, with no ES id equal to a bare assistant id;
  - publish a long assistant, search for a phrase from its tail, and get one result;
  - unpublish it and confirm it has 0 chunks.
- [ ] Commit any lint fixes.

## Ops note (MR description, not code)

After deploy, run `POST /v1/admin/marketplace/reindex`. It clears the FAILED state and migrates legacy single documents to chunks.

---

## Iteration 3: Reuse the base chunking pipeline (approved 2026-10-08)

Goal: same behaviour, but splitting reuses `BaseDatasourceProcessor._split_documents`, `_get_splitter` and `_process_chunk` together with the base 1-based `chunk_num`. The loader goes back to one whole document per assistant, and `BasePlatformLoader` is restored to its main contract.

### Task R1: Loader returns one whole document with `chunk_header`
Test-first: yes. `test_entity_to_document_body_and_header` fails because the loader still returns a list of chunks.
- Restore `platform_loader.py` from main (`_entity_to_document`, `load_single_entity -> Optional[Document]`).
- In `AssistantLoader`:
  - remove `_split_body`, `_sanitized_to_documents`, `BODY_CHUNK_*` and the splitter import;
  - `_entity_to_document` returns `Document(page_content=_build_body(...), metadata={id, name, source, popularity_score, chunk_header})`;
  - `load_single_entity` returns `None` when the assistant is missing or unpublished.
- Tests: the body holds the full description and tools and the capped prompt; the header metadata; the exact metadata keys; `load_single_entity` returns a Document or `None`.

### Task R2: Processor splits through the base pipeline
Test-first: yes. `test_split_long_entity_into_numbered_chunks_with_header` fails because `_split_entity_documents` does not exist.
- `BasePlatformDatasourceProcessor`:
  - add the constants `CHUNK_TOKENS = 1500` and `CHUNK_OVERLAP_TOKENS = 100`;
  - a `_get_splitter` classmethod override;
  - a `_process_chunk` override that prepends `chunk_header` and drops it from the metadata;
  - `_split_entity_documents(documents)` (per entity: `_reset_chunk_numbering()`, then `_split_documents([doc])`).
- `_process` and `index_single_entity` split the whole documents before calling `_add_documents`. `index_single_entity` uses the Optional Document API again.
- Tests:
  - 1 chunk for a short assistant, numbered 1 with the header;
  - a long assistant gives 1..N chunks, each ≤ 2000 tokens with the header, and the last word is present;
  - numbering restarts per entity when two assistants share a name;
  - chunk metadata has no `chunk_header`;
  - `index_single_entity` stores `new_1..N`.

### Task R3: Verification
- Run ruff, the license check and the targeted regression suites.
- Rebuild the image and re-run the live check (expected ids are now `<id>_1..N`) and the FAQ end-to-end check on the branch image.
- Then do one review round (code-review.final over the refactor diff) and QA.
