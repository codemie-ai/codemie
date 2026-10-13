# EPMCDME-13933: Marketplace assistants indexing exceeds the embedding context length

## Problem

In the AI/Run FAQ, the `search_kb_marketplace_assistants` tool fails every time:

> The datasource 'marketplace_assistants' last indexing attempt failed. Error: Error code: 400 — This model's maximum context length is 8192 tokens, however you requested 10854 tokens …

Root cause, verified in code:

1. **No size bound on the indexed document.** `AssistantLoader._build_page_content` (`src/codemie/datasource/loader/platform/assistant_loader.py`) builds one document per published assistant. The `Description` and the `Tools` list (every tool name) are unbounded. Only the system prompt is capped, at 1000 characters. The platform processor never runs the token splitter that every other indexer uses.
2. **One oversized assistant fails the whole sync.** `BasePlatformDatasourceProcessor._add_documents` (`src/codemie/datasource/platform/base_platform_processor.py`) embeds all documents in one `store.add_documents` call.
   - The embeddings API rejects the oversized input with a 400. The default model is `text-embedding-ada-002`, with an 8191-token input limit, and `check_embedding_ctx_length=False` disables LangChain's own check.
   - That one 400 fails the full sync, and `process()` saves the error on `IndexInfo.error`.
3. **The search tool refuses to run on a failed index.** While `IndexInfo.error` is set, `SearchKBTool.execute` raises a `ToolException` with the health notice before searching.

## Goal / Acceptance

- After deploy and a full marketplace reindex, the same FAQ query to `search_kb_marketplace_assistants` no longer returns the indexing / context-length error.
- No embedding input exceeds the model's context limit, whatever the assistant's size.
- The full description and the full tools list of every published assistant are searchable. Nothing is dropped because of length. Authors who write long descriptions do so on purpose.
- If one assistant fails to embed for any reason during a full sync, the `marketplace_assistants` index as a whole does not fail.
- The index never holds stale or duplicate content for an assistant: after a republish, update, unpublish, or the migration from one document per assistant.
- Search returns each assistant at most once.

## Decisions

- **Chunk, don't truncate.** An assistant becomes N chunks (N ≥ 1). A short assistant is still exactly one chunk.
- **System prompt keeps the existing 1000-character cap.** EPMCDME-9519 introduced it, and the user confirmed it stays. Indexing more of the prompt would expose it through FAQ answers.
- **Search returns one result per assistant**: the best-ranked chunk.

## Out of scope

- Changing `SearchKBTool` or the health-notice semantics.
- Limiting `Assistant.description` length at the API or UI level. Possible follow-up.
- Changing the publish/update error handling in `rest_api/routers/assistant.py`.
- Exact progress counters on update. `IndexInfo` progress is cosmetic for this hidden platform index.

## Design

### 1. Chunked documents, built with the base chunking pipeline

Splitting reuses the project's standard pipeline in `BaseDatasourceProcessor`: `_split_documents`, `_get_splitter`, `_process_chunk` and per-source `chunk_num` numbering. The loader keeps producing whole documents, as every other datasource loader does.

- **Loader contract.** `BasePlatformLoader` is unchanged from main.
  - `AssistantLoader._entity_to_document(entity) -> Document` returns one whole document per assistant.
  - `load_single_entity(entity_id) -> Optional[Document]` is also unchanged.
- **Whole document built by `AssistantLoader`:**
  - `page_content`: the body, with these parts:
    - `Description: <full description>`
    - `System Prompt: <system_prompt[:1000]>`
    - `Tools: <full comma-separated tool list>`
  - `metadata`: `id`, `name`, `source` (= name), `popularity_score`, plus `chunk_header`. The header is Name, Slug, `Page url with details`, Project, Categories, Toolkits, Created by and Usage, joined with `"\n\n"`.
- **Splitting** happens in `BasePlatformDatasourceProcessor`, before storing:
  - `_split_entity_documents(documents)` splits each entity separately. It calls `_reset_chunk_numbering()` and then the base `_split_documents([doc])`, so every assistant's chunks are numbered 1..N. The base convention is 1-based.
  - `_get_splitter` (override) returns `RecursiveCharacterTextSplitter.from_tiktoken_encoder(encoding_name="o200k_base", chunk_size=CHUNK_TOKENS, chunk_overlap=CHUNK_OVERLAP_TOKENS, disallowed_special={})`, with new class constants `CHUNK_TOKENS = 1500` and `CHUNK_OVERLAP_TOKENS = 100`.
  - `_process_chunk` (override) sets the chunk content to `chunk_header + "\n\n" + body_chunk`, and drops `chunk_header` from the chunk metadata.
  - Full sync and single-entity indexing both split before calling `_add_documents`.
- **Metadata per chunk:**
  - `id`: the assistant id
  - `name`
  - `source`: the name. Every chunk of one assistant shares it, so the KB routing source list is unchanged.
  - `popularity_score`
  - `chunk_num`: 1..N, assigned by the base class
- **ES document id:** `f"{assistant_id}_{chunk_num}"`, built by `_chunk_doc_id(doc)` in the processor. The base storage path uses random ids, so the platform processor keeps its own deterministic ids, which the stale cleanup needs.
- The first fix's truncation helpers and token constants are removed. Nothing is cut anymore.

### 2. Processor: chunk-aware writes, cleanup and resilience (`BasePlatformDatasourceProcessor`)

- **Grouping.** `_add_documents(documents, is_update=False, fail_fast=False)` groups the flat chunk list by `metadata.id`, keeping the original order. One group is one assistant.
- **Full sync** (default path):
  - Groups are packed into batches of up to `STORAGE_CONFIG.embeddings_max_docs_count` chunks. A group bigger than the limit forms its own batch.
  - If a batch raises, each group in it is retried alone. A group that still fails is skipped, and a warning is logged with `entity_id`, `entity_name`, `datasource_name` and the error. Its other chunks are not kept as partial content; the stale cleanup below does not run for it, so its previous version stays.
  - After a group is stored, progress moves once for that assistant: `move_progress(chunks_count=len(group), processed_file=name)`.
  - If nothing is stored, the existing `_validate_indexing_result` raises `NoChunksImportedException`, so the index is still marked FAILED.
- **Single entity** (`index_single_entity`, used by publish and update; `fail_fast=True` or `is_update=True`):
  - The assistant's chunks are stored in one call, and errors propagate to the caller.
  - Progress moves only when `is_update=False`.
- **Stale cleanup.** After any successful store of a group, all of that assistant's documents that are not among the ids just written are deleted:

  ```
  delete_by_query(index, query={"bool": {"filter": [{"terms": {"metadata.id.keyword": [<ids of stored groups>]}}],
                                       "must_not": [{"ids": {"values": [<new chunk doc ids>]}}]}},
                  conflicts="proceed")
  ```

  This removes the legacy one-document-per-assistant entries (ES id = assistant id), and the extra chunks left when a description shrinks. Writing first and deleting second means a failed write never leaves a gap: the old version stays searchable.
  - A cleanup failure is logged as a warning and does not fail indexing. Stale content is the lesser problem, and the next write retries the cleanup.
  - Verified mapping: `metadata.id` is `text` with a `.keyword` subfield.
- **Unpublish** (`remove_single_entity`):
  - `delete_by_query` with a `term` on `metadata.id.keyword` removes all of the assistant's chunks, including a legacy single document.
  - `decrease_progress(chunks_count=<deleted count>, processed_file=name)` runs only when the deleted count is greater than 0. When nothing was found, it logs a warning and does not raise.
  - Other ES errors still raise, as today.

### 3. Search: one result per assistant (`SearchAndRerankMarketplace.execute`)

- On the RRF-ordered results from `SearchAndRerankKB.execute`, keep only the first chunk per `metadata.id`, which is the best-ranked one. Results without an id are kept as they are. Then apply the existing popularity sort.
- A long assistant can therefore never take several of the `top_k=10` slots, and never gets several popularity boosts.
- The LLM source routing is unchanged, because all chunks share `metadata.source`. There is no extra LLM cost.

### 4. Operations

After deploy, an admin must trigger `POST /v1/admin/marketplace/reindex`, or restart the service with `PLATFORM_DATASOURCES_SYNC_ENABLED=true`. This does two things:
- clears the persisted FAILED state;
- migrates the legacy single documents to chunks, via the stale cleanup.

Record this step in the MR description.

## Testing

- **Loader** (`tests/codemie/datasource/loader/test_assistant_loader.py`):
  - The body holds the full description (including its last word), the full tools list and the system prompt capped at 1000 characters.
  - `chunk_header` holds the identifying fields.
  - The metadata keys are exactly `id`, `name`, `source`, `popularity_score`, `chunk_header`.
  - `load_single_entity` returns a Document for a published assistant, and `None` for an unpublished or missing one.
- **Processor splitting** (`tests/codemie/datasource/platform_tests/test_base_platform_processor.py`):
  - A short assistant gives 1 chunk, with `chunk_num` 1 and the header prepended.
  - A long description gives N > 1 chunks. Each chunk is ≤ about 2000 `o200k` tokens and starts with the header. Chunks are numbered 1..N per assistant, and numbering restarts for each assistant, even when two assistants share a name.
  - The last word and the last tool name are present.
  - Chunk metadata has no `chunk_header`.
- **Processor storage** (`tests/codemie/datasource/platform_tests/test_base_platform_processor.py`):
  - Chunk ids are `{id}_{n}`.
  - Grouped batching keeps an assistant's chunks together.
  - When a batch fails, the fallback runs per assistant; a failed assistant is skipped whole, and progress counts only stored assistants.
  - A stale cleanup query is issued with the stored ids and the new chunk ids.
  - A cleanup failure is logged and does not raise.
  - When all fail, nothing raises inside `_add_documents`.
  - Single entity: errors propagate on update and on publish, and progress moves on publish success.
  - Unpublish deletes by `metadata.id.keyword` and decreases progress by the deleted count. When 0 documents are deleted, there is no decrease and no raise.
- **Search** (`tests/codemie/service/search_and_rerank/`):
  - De-duplication keeps the first chunk per assistant id, then sorts by popularity.
- **Live check** on the local stack:
  - Reindex the 14 legacy single documents; they are replaced by chunks, with no duplicates.
  - A long-description assistant can be found by a phrase from the end of its description.
