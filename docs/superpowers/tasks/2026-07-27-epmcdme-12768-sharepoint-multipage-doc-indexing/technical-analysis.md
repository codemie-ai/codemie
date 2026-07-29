# Technical Research

**Task**: sharepoint datasource indexing document-parser pdf docx chunking
**Generated**: 2026-07-27
**Research path**: filesystem (codegraph MCP not available in this session)

---

## 1. Original Context

task_context (verbatim from Jira EPMCDME-12768, plus user-reported extension):

TITLE: PDF Documents Indexed via SharePoint Data Source Only Provide 1 Page to Agent, While File Data Source Works as Expected

DESCRIPTION:
There is a severe discrepancy when indexing PDF files via SharePoint Data Source versus File Data Source.

When connecting a SharePoint folder containing multiple PDF documents as a Data Source and attempting to analyze those files via a test agent, the agent is only able to access and retrieve the first page of each document. Attempting to request additional pages, the table of contents, or full-text analysis results in a failure, as only the first page is available.

However, when the exact same files are added using the File Data Source type, the agent can access and process all pages/content correctly. This appears to indicate a bug in the PDF indexing pipeline/synchronization specific to SharePoint Data Sources, resulting in incomplete document indexing and major loss of information for end users.

Preconditions:
- SharePoint integration enabled and accessible
- At least one SharePoint folder containing multi-page PDF documents
- Test Agent/assistant with access to both SharePoint Data Source and File Data Source containing identical PDFs

Steps to Reproduce:
1. Connect a SharePoint folder containing PDF files as a Data Source.
2. Allow indexing to complete.
3. Use an AI agent configured to access this Data Source.
4. Ask the agent to provide information that requires access to all pages (e.g., table of contents, section count, total page count).
5. Observe that agent can only provide information from page 1 of each PDF; additional pages are not accessible.
6. Repeat the process by adding the same files via a File Data Source and allow indexing to complete.
7. Use the same agent to perform the same queries on the File Data Source.
8. Observe that agent can access and provide information from the entire content of the PDFs.

Expected Result:
- PDF files indexed via SharePoint Data Source should be fully available to agents for multi-page content extraction, identical to File Data Source behavior.
- Agent is able to answer questions regarding the full PDF (total pages, all sections, table of contents, etc.).

Affected Areas:
- SharePoint Data Source indexing pipeline
- Document retrieval mechanisms for SharePoint-based sources
- PDF parsing logic within SharePoint connector

Acceptance Criteria:
- When connecting a SharePoint folder with PDF files and completing indexing, agents must be able to access and process the full contents (all pages) of the PDFs.
- Functionality matches the experience for File Data Source (no truncation, no loss of information beyond the 1st page).
- Regression test: Queries that request content from pages past page 1 must return answers from SharePoint-sourced PDFs.
- No negative impact on indexing performance or stability for existing File Data Sources.

Labels: AI/Run, AI-Generated, Backend. Priority: Major. Type: Bug.

USER-ADDED CONTEXT (IMPORTANT — expands the scope):
The user reports that the same truncation behavior has also been observed for DOCX documents indexed via SharePoint, not only PDF. The user has explicitly chosen the scope to be a GENERAL fix of the SharePoint indexing pipeline so that ALL multi-page/multi-chunk document formats (pdf, docx, and others) are fully indexed — NOT a pdf-only patch. Your research must therefore cover the full document-parsing/chunking path for SharePoint-sourced files across formats, not just PDF.

RESEARCH GOALS — be specific and exhaustive on these:
1. Find the SharePoint data source connector / indexer implementation in this repository (src/codemie/...), and the File data source connector for comparison.
2. Trace the exact document ingestion path for BOTH: how a file's bytes are fetched, which parser/loader is used (e.g. PyPDF, unstructured, pdfminer, python-docx, langchain document loaders), how the resulting Document objects are chunked/split, and how they end up in the vector store / Elasticsearch index.
3. Identify precisely WHERE the two paths diverge — the most likely defect is that the SharePoint path keeps only the first Document/page returned by the loader (e.g. `docs[0]`, `next(iter(...))`, `pages[0]`, taking only the first element, or a loader that returns a list but the code treats it as a single doc), or truncates content, or passes a page-limit/max-pages parameter.
4. Note how many parsing implementations exist and whether there is a shared/common file-parsing utility that both paths should use.
5. Document existing testing patterns for these connectors: where connector/indexer tests live, what fixtures/mocks are used for SharePoint (msgraph client mocks?), how parser tests assert on chunk counts.
6. Risk indicators: reindexing implications, backward compatibility of stored index documents, performance for large files, any chunk-size / max-content limits configured.

---

## 2. Codebase Findings

### Headline conclusion (read this first)

**Parsing is NOT where the paths diverge.** Both SharePoint and File datasources already funnel through the same shared parser, `extract_documents_from_bytes` in `src/codemie/datasource/loader/file_extraction_utils.py` (consolidated by a prior fix, commit `9a144ca32` / `18420d5e0`, EPMCDME-11342 — "Fix SharePoint PDF indexing by consolidating file extraction into shared utility"). `PDFPlumberLoader` emits one `Document` per page for all pages, and the SharePoint loader iterates and yields every one of them.

The defect is **downstream, in chunk-metadata handling plus retrieval-time deduplication**. All pages/chunks ARE written to Elasticsearch, but they are written with byte-identical metadata, and the retrieval layer deduplicates on that metadata — collapsing every document to a single surviving chunk before the agent ever sees it. No `docs[0]`, `next(iter(...))`, `[:1]`, `max_pages`, or content-truncation pattern exists in the datasource package (explicitly searched and ruled out).

### Root-cause chain (three defects that compose)

**Defect 1 — SharePoint `_process_chunk` discards the chunk metadata it is handed.**
`src/codemie/datasource/sharepoint/sharepoint_datasource_processor.py:279-286`

```python
return Document(
    page_content=chunk,
    metadata={
        "source": document.metadata.get("source", ""),
        "title": document.metadata.get("title", ""),
        "type": document.metadata.get("type", ""),
    },
)
```

The `chunk_metadata` parameter — which carries `chunk_num`, `page`, `total_pages`, `file_path` — is accepted and thrown away. Combined with `_transform_to_doc` (`src/codemie/datasource/loader/sharepoint_loader.py:1238-1266`) setting `metadata["source"]` to the item's `webUrl`, which is identical for every page of a file, **every chunk of a SharePoint document ends up with byte-identical metadata**. The File processor does not do this — it uses the base `_process_chunk` (`base_datasource_processor.py:626-627`), which preserves the full `chunk_metadata`.

**Defect 2 — retrieval-time dedup collapses identical-metadata chunks to one.**
`src/codemie/service/search_and_rerank/rrf.py:95-116`

```python
source = doc.metadata[self.source_field]
chunk  = doc.metadata.get(self.chunk_field, 0)   # chunk_field == 'chunk_num'
key = f"{source}-{chunk}"
if key not in seen_sources:
    filtered_results.append(doc)
```

Docstring: *"Each source should only have one document."* With `chunk_num` stripped, the key degenerates to `"<webUrl>-0"` for every chunk of the file, so **only the first survives**. Applied on both the fused and exact-match branches (`rrf.py:52-53`); `rrf.py:56-62` then sorts by `source, page, chunk_num`, and `page` is also stripped, so the surviving chunk is effectively ES/insertion order — normally page 1. This is the precise mechanism behind the reported symptom.

**Defect 3 — base chunk numbering is per-Document and conditional (the format-agnostic bug).**
`src/codemie/datasource/base_datasource_processor.py:608-611`

```python
for chunk_number, chunk in enumerate(split_chunks, start=1):
    chunk_metadata = document.metadata.copy()
    if len(split_chunks) > 1:
        chunk_metadata["chunk_num"] = chunk_number
```

Two independent problems for any loader that emits N `Document`s per source file (PDF pages, XLSX sheets, ZIP/MSG/EML members):
- numbering restarts at 1 for each page-document, so page 2 chunk 1 collides with page 1 chunk 1 on key `source-1`;
- when a page yields a single chunk (the normal case at `chunk_size: 2000`), **no `chunk_num` is set at all** → key `source-0` for every page.

`FileDatasourceProcessor` sidesteps this only because it overrides `_split_documents` (`src/codemie/datasource/file/file_datasource_processor.py:328-360`) and assigns `chunk_num` 1..N across ALL pages of a file (`_process_chunks`, `:178-182`). **Even after fixing Defect 1 alone, SharePoint PDFs would still collapse** — this is the line a "general fix" must address.

**Why PDF and DOCX both break, slightly differently:** `docx_loader.py` emits exactly ONE `Document` per DOCX, split into many chunks, so `len(split_chunks) > 1` and the base does set `chunk_num` — DOCX is broken by Defect 1 alone. PDF emits one `Document` per page, each usually a single chunk, so it is broken by Defect 1 **and** Defect 3. This asymmetry confirms the user's report that the fix must be general, not PDF-specific.

### Existing Implementations

- `src/codemie/datasource/base_datasource_processor.py` (1055 L) — abstract indexing pipeline: load → batch → split → guardrails → ES store. `_split_documents` at `:576-618`.
- `src/codemie/datasource/sharepoint/sharepoint_datasource_processor.py` (450 L) — SharePoint processor: index name (`:155-157`), splitter (`:299-304`), `_process_chunk` (`:279-286`), incremental cleanup (`:197-202`).
- `src/codemie/datasource/loader/sharepoint_loader.py` (1597 L) — Graph API connector: auth (`:324-344`), drive traversal (`:590-628`, `:991-1017`), download (`:1042-1074`), extraction call (`:1100`), `_process_file_item` (`:973-986`), `_transform_to_doc` (`:1238-1266`).
- `src/codemie/datasource/file/file_datasource_processor.py` — File processor; overrides `_split_documents` (`:362`) with per-file chunk numbering.
- `src/codemie/datasource/loader/file_loader.py` (161 L) — `FilesDatasourceLoader`, yields `List[Document]` per file.
- `src/codemie/datasource/loader/file_extraction_utils.py` (190 L) — **the shared parser**: `extract_documents_from_bytes` (`:118`), `LOADERS` dispatch table (`:51-69`), `DEFAULT_LOADER_KWARGS` (`:72-77`).
- `src/codemie/datasource/loader/binary/pdf_plumber_loader.py` — pdfplumber, `mode="page"` → one `Document` per page with `page`/`total_pages` metadata (`:105-121`).
- `src/codemie/datasource/loader/docx_loader.py` — markitdown + python-docx fallback → ONE `Document` per DOCX.
- `src/codemie/service/search_and_rerank/kb.py` — KB retrieval (kNN + text + LLM source routing); wires `chunk_field` at `:112` and `:149`.
- `src/codemie/service/search_and_rerank/rrf.py` — RRF fusion + `_filter_duplicates` (the collapse point).
- `src/codemie/agents/tools/kb/search_kb.py` — agent-facing `search_kb` tool, formats `source-chunk_num`.
- `config/datasources/datasources-config.yaml` — per-loader chunking: sharepoint `chunk_size: 2000 / overlap: 200` (`:140-142`), file `1500 / 100` (`:117-118`), code `2000 / 30`.

`src/codemie/datasource` subpackages: `loader/` (connectors), `loader/binary/` (pdf/msg/image), `loader/platform/`, `callback/`, `code/`, `file/`, `sharepoint/`, `jira/`, `xray/`, `svn/`, `google_doc/`, `azure_devops_wiki/`, `azure_devops_work_item/`, `platform/`, plus `confluence_datasource_processor.py`, `datasources_config.py`, `datasource_file_storage.py`, `datasource_concurrency_manager.py`, `exceptions.py`.

### Architecture and Layers Affected

`rest_api/routers/index.py` (HTTP) → `datasource/*_datasource_processor.py` (orchestration) → `datasource/loader/*` (connector) → `datasource/loader/file_extraction_utils.py` (parsing) → `langchain_text_splitters` (chunking) → `clients/elasticsearch.py` + `core/dependecies.get_elasticsearch` (persistence) → `service/search_and_rerank/*` (retrieval) → `agents/tools/kb/search_kb.py` (agent surface).

The fix spans **two layers that are usually treated separately**: the indexing/orchestration layer (metadata written) and the retrieval/rerank layer (metadata consumed for dedup). Any fix confined to one layer will not close the ticket.

### Ingestion path — SharePoint (traced)

1. `sharepoint_datasource_processor.py:169` `_init_loader()` → `SharePointLoader`
2. `sharepoint_loader.py:324-344` `_get_app_access_token()` — plain `requests.post` to `login.microsoftonline.com` (**no msgraph-sdk / Office365-REST client**)
3. `sharepoint_loader.py:590-628` `_get_all_drives()` → `:991-1017` `_load_documents_recursive()` (Graph `/children`, paginated)
4. `sharepoint_loader.py:1042-1074` `_download_and_extract_file()` — `requests.get(.../items/{id}/content)` → `file_bytes`
5. `sharepoint_loader.py:1100` → `extract_documents_from_bytes(...)` (shared parser)
6. `file_extraction_utils.py:143` loader chosen from `LOADERS`; `:169-173` `for document in loader.lazy_load(): documents.append(document)` — **full page list returned, no truncation here**
7. `sharepoint_loader.py:973-986` `_process_file_item` yields one dict PER page, all with the same `"id"`/`"url"`
8. `sharepoint_loader.py:1238-1266` `_transform_to_doc` → `metadata["source"] = item["url"]` (webUrl, identical per page)
9. `base_datasource_processor.py:671` batches of `SHAREPOINT_CONFIG.loader_batch_size` (20)
10. `base_datasource_processor.py:756` `_split_documents(docs)` → base impl `:604-618` with `RecursiveCharacterTextSplitter` (2000/200)
11. `sharepoint_datasource_processor.py:279-286` `_process_chunk` → **metadata wipe (Defect 1)**
12. `base_datasource_processor.py:1007` `store.add_documents(...)` → `ElasticsearchStore` from `core/dependecies.py:105`

### Ingestion path — File (traced, for contrast)

1. `file_datasource_processor.py:135` `_init_loader()` → `FilesDatasourceLoader`
2. `file_loader.py:109` `self.file_repo.read_file(...)` (local/S3 repo, not HTTP)
3. `file_loader.py:117` / `:145` → `extract_documents_from_bytes(...)` — **same shared parser**
4. `file_loader.py:110` yields a `List[Document]` (whole file) per iteration
5. `base_datasource_processor.py:671-673` batching (default 50)
6. `file_datasource_processor.py:362` `_split_documents` override → `_segregate_documents_input` (`:254`) → `_process_document_list` (`:224`) → `list_of_docs.extend(documents)` (`:252`)
7. `file_datasource_processor.py:328-360` `_split_list_with_documents` — `chunk_metadata = document.metadata.copy()` (`:350`), grouped by `file_path`
8. `file_datasource_processor.py:357-359` `if len(docs) > 1: self._process_chunks(docs)` → `:178-182` assigns `chunk_num` 1..N **across ALL pages of the file**
9. base `_process_chunk` (`base_datasource_processor.py:626-627`) — **keeps full `chunk_metadata`**
10. same ES store write path

### Additional collapse sites (same root cause, different surfaces)

- `src/codemie/datasource/jira_datasource_processor.py:144-147` and `xray_datasource_processor.py:137-140` — same metadata-wipe pattern (`metadata={"source": source, "key": key}`); multi-chunk Jira/Xray issues collapse identically.
- `src/codemie/datasource/loader/azure_devops_wiki_loader.py:126-157` and `azure_devops_work_item_loader.py:141-165` — rebuild metadata and drop `chunk_num` too.
- `src/codemie/workflows/utils/utils.py:308-310` — documents-tree/preview API dedups by `source+chunk_num`; for SharePoint that is `source+""` for all chunks, so the **UI also shows a single chunk** (last-wins dict comprehension).
- `src/codemie/agents/utils.py:238` + `:246` — identical collapse for the code-index file listing tool.
- `src/codemie/service/search_and_rerank/kb.py:87` + `:213` — `MAX_CHUNKS_FOR_SINGLE_DOCUMENT = 20`; any source with more than 20 chunks is excluded from LLM source routing, so the exact-match (full-document) retrieval branch never fires for large PDFs and they are only reachable through the deduped fused branch. **Aggravator, not root cause; affects both paths.**
- `src/codemie/datasource/loader/file_extraction_utils.py:180-181` — `except ValueError` wraps the whole `lazy_load()` iteration, so a mid-file failure silently returns only the pages accumulated so far, with no error surfaced. A real silent-truncation shape; see Risk Indicators for the tesseract angle.

### Patterns and Conventions

- Template Method: `BaseDatasourceProcessor.process()` is final; subclasses implement `_init_loader`/`_init_index` and override `_get_splitter`/`_process_chunk`/`_split_documents`. **The bug lives entirely in these override hooks** — the fix must decide whether to correct the SharePoint override or lift correct behavior into the base.
- ABC connector contract `BaseDatasourceLoader` (`fetch_remote_stats`, `lazy_load`, `get_load_stats`) plus langchain `BaseLoader`.
- Dispatch table (not factory) for parsers: `LOADERS` dict keyed by extension, `file_extraction_utils.py:51`.
- Dataclass config objects (`SharePointAuthConfig`, `SharePointProcessorConfig`).
- Callback observers (`DatasourceProcessorCallback` / `DatasourceMonitoringCallback`) for progress.
- YAML-driven per-datasource tuning via `datasources_config.py` → `config/datasources/datasources-config.yaml`.
- Optional process-pool offload for parsing (`file_processor_pool.py`, `ENABLE_FILE_MULTIPROCESSING`, default `False`).

### Integration Points

Cross-module dependency directions:
- `datasource/sharepoint/sharepoint_datasource_processor.py` → `datasource/loader/sharepoint_loader.py` → `datasource/loader/file_extraction_utils.py`
- `datasource/file/file_datasource_processor.py` → `datasource/loader/file_loader.py` → `datasource/loader/file_extraction_utils.py`
- `datasource/loader/git_loader.py` | `svn_loader.py` → `file_extraction_utils.py`
- `file_extraction_utils.py` → `binary/{pdf_plumber_loader,msg_loader,image_loader}.py`, `docx_loader.py`, `eml_loader.py`, `vsdx_loader.py`
- `eml_loader.py` | `binary/msg_loader.py` → `file_extraction_utils.py` (deferred import, cycle break)
- `datasource/*_datasource_processor.py` → `base_datasource_processor.py` → `core/dependecies.get_elasticsearch` → `clients/elasticsearch.py`
- `agents/tools/kb/search_kb.py` → `service/search_and_rerank/kb.py` → `service/search_and_rerank/rrf.py`
- `workflows/utils/utils.py` → `clients/elasticsearch.py` (direct ES read, bypasses rerank but repeats the same dedup key)

Blast radius of each candidate fix location:
| Fix site | Scope |
|---|---|
| `sharepoint_datasource_processor._process_chunk` (Defect 1) | SharePoint only — **necessary but not sufficient** |
| `base_datasource_processor._split_documents` (Defect 3) | Repo-wide: Git, SVN, Confluence, GoogleDoc, ADO, Jira, Xray |
| `rrf._filter_duplicates` key (Defect 2) | Repo-wide retrieval for every datasource type |

Connector exposure: File (immune, per-file `chunk_num`), Git and SVN (share the parser, use base `_process_chunk` → exposed to Defect 3 for PDFs in repos), MSG/EML (recursive attachment extraction through the same parser), Confluence (`_process_chunk:333` correctly passes `chunk_metadata` through → not affected by Defect 1), Jira/Xray/ADO (affected by the Defect 1 family). No S3 / Azure Blob / GDrive-file connectors exist.

### Third-party parsing dependencies

`pdfplumber` (page-by-page PDF), `langchain_markitdown` (`DocxLoader`, `XlsxLoader`, `HtmlLoader`, `EpubLoader`, `ZipLoader`, `PlainTextLoader` fallback), `python-docx` (DOCX fallback), `langchain_community` (`CSVLoader`, `UnstructuredPowerPointLoader`, `LLMImageBlobParser`, `TesseractBlobParser`), `pytesseract` (OCR fallback), `langchain_text_splitters` (`RecursiveCharacterTextSplitter.from_tiktoken_encoder`, `o200k_base`), `requests` (entire SharePoint Graph transport), `elasticsearch` / `langchain-elasticsearch`, `pathspec` (`files_filter`).

Two independent parsing stacks exist in the repo: the **indexing stack** (`datasource/loader/file_extraction_utils.py` + 7 loader modules, one dispatcher) and the **agent-tool stack** (`codemie_tools/file_analysis/{pdf,docx,xlsx,pptx,csv,email}/`), which is not used by indexing. Only the indexing stack is in scope.

---

## 3. Documentation Findings

### Guides and Architecture Docs

`.ai-run/guides/` exists (37 files). Binding for this fix:
- `.ai-run/guides/project.md` — Jira `EPMCDME`, GitLab remote `git@gitbud.epam.com:epm-cdme/codemie.git`, target branch `main`, MR via `glab`.
- `.ai-run/guides/quality-gates.md` — **gate contract**: `make ruff` → `make build` → `make license-check` → `make gitleaks` → `make test` → `make verify`.
- `.ai-run/guides/standards/git-workflow.md` — branch `EPMCDME-12345_short-description`, commit `EPMCDME-####: Description`, squash merge, no proactive git side effects.
- `.ai-run/guides/standards/code-quality.md` — Ruff config in `pyproject.toml`, `X | None` over `Optional[X]`, `from __future__ import annotations` in new modules, Apache-2.0 headers required (`make license-fix`).
- `.ai-run/guides/testing/testing-patterns.md` — tests mirror `src/` under `tests/codemie/<package path>/`; mock at provider boundaries; run narrowest scope; report the exact command.
- `.ai-run/guides/architecture/layered-architecture.md`, `architecture/project-structure.md`, `data/elasticsearch-integration.md`.

**No SharePoint-specific or indexing-pipeline guide exists** — the pipeline is undocumented in `.ai-run/guides/`.

`.claude/skills/` has 4 skills; relevant ones: `codemie-jira-assistant` (ticket adapter) and `taf-regression-advisor` (suggests TAF regression cases for backend changes). `.claude/agents` does not exist. `.claude/settings.json` runs `make ruff` as a Stop hook.

`docs/` contains no architecture or datasource/indexing documentation (only `workflows/`, webhook/JWKS config, and `superpowers/`).

### Architectural Decisions

No ADRs. Relevant recorded history instead:
- **A task dir for this exact ticket already exists**: `docs/superpowers/tasks/2026-07-27-epmcdme-12768-sharepoint-multipage-doc-indexing/` with only `.state.json` (`flow: sdlc-task`, `phase: main`, branch `EPMCDME-12768_sharepoint-multipage-doc-indexing`). No spec/plan yet.
- `docs/superpowers/tasks/2026-07-13-epmcdme-5383-zip-upload-datasource/technical-analysis.md:118` documents the shared loader dispatch (`LOADERS`, `extract_documents_from_bytes`) and explicitly notes SharePoint shares it.
- `docs/superpowers/plans/2026-07-14-datasource-search-coverage-gaps.md` + `docs/superpowers/specs/2026-07-14-datasource-search-coverage-gaps-design.md` — cover `SearchAndRerankKB`, `_knn_vector_search`, chunk retrieval. **Directly relevant to Defect 2.**
- `docs/superpowers/plans/EPMCDME-13171-scheduler-xray-sharepoint.md` — SharePoint scheduler/reindex actor wiring.

Git history (current branch `EPMCDME-12768_sharepoint-multipage-doc-indexing`, clean tree):
- `9a144ca32` / `18420d5e0` — EPMCDME-11342: Fix SharePoint PDF indexing by consolidating file extraction into shared utility — **the prior attempt at this exact class of bug**; it fixed parsing but not chunk metadata.
- `cc224afab` — EPMCDME-12735: Add DOCX conversion fallback and Visio support.
- `2a544c4dc` — EPMCDME-11259: Fix SharePoint indexing performance and `files_filter` path matching.
- `57e51c46b` — EPMCDME-11996: Fix SharePoint Processing Summary counts.
- `56158aa9a` — EPMCDME-12069: Offload file parsing to process pool; `af5e698d1` — EPMCDME-13075: BrokenProcessPool recovery.
- `7fa9b92f9`, `6832ff060` — PDF chunk-level search with **composite IDs** and summaries; preserve chunk summaries when updating metadata. **Precedent for chunk identity design.**
- `a90115d05` — EPMCDME-7188: override `lazy_load` for unlimited Confluence indexing (precedent for a loader yielding many docs).

`CHANGELOG.md` is stale (legacy `MDTUGPT-*` era) — nothing relevant.

### Derived Conventions

- `CLAUDE.md` is just `@AGENTS.md`; `AGENTS.md` is the real index — load the matching P0 guide before broad code search.
- Python 3.12+ typing, async/await for I/O, API → Service → Repository layering, Apache-2.0 headers on new files.
- Tests mirror `src/` under `tests/codemie/datasource/...`; mock the SharePoint/Graph boundary.
- **Two conflicting commit conventions**: `.ai-run/guides/standards/git-workflow.md` + README require `EPMCDME-12768: Description`; `CONTRIBUTING.md` documents Conventional Commits for the public GitHub fork flow. Recent history is 100% `EPMCDME-####:` — follow the guide.
- Zero `TODO`/`FIXME`/`HACK` markers exist anywhere in `src/codemie/datasource/` or in any `*sharepoint*`/`*parser*`/`*loader*` file (the single hit at `confluence_datasource_processor.py:234` is a prompt-injection keyword list, a false positive).

---

## 4. Testing Landscape

### Existing Coverage

`tests/` mirrors `src/`. Datasource tests at `tests/codemie/datasource/` with per-source subdirs (`sharepoint/`, `file/`, `jira/`, `code/`, `svn/`, `google_doc/`, `azure_devops_*`, `platform_tests/`, `callback/`) plus a flat `loader/` dir mirroring `src/codemie/datasource/loader/`.

SharePoint (three files, ~5.2k lines, all pure-mock):
- `tests/codemie/datasource/loader/test_sharepoint_loader.py` (2735 L) — the maintained/exhaustive suite: auth, Graph retries (401/429), pages, drives, folder scope, `files_filter`, counting, `lazy_load` fan-out. **Contains no test of `_process_file_item` / `_download_and_extract_file` / binary extraction at all.**
- `tests/codemie/datasource/sharepoint/test_sharepoint_loader.py` (1627 L) — older duplicate suite; the only place file extraction is exercised (`TestSharePointLoaderFileProcessingEdgeCases`, `:897-978`). `test_process_file_item_successful` (`:949`) mocks `_download_and_extract_file` to return a **single** `Document` and asserts `assert len(result) == 1` (`:971`).
- `tests/codemie/datasource/sharepoint/test_sharepoint_datasource_processor.py` (899 L) — `TestSharePointProcessorChunk` (`:503-537`) calls `_process_chunk` with one chunk and asserts metadata keys only. **`:506` is titled "Test `_process_chunk` preserves source/title/type and strips extra metadata" — an existing test actively locks in Defect 1 and will have to be changed.**

Other relevant: `tests/codemie/datasource/test_base_datasource_processor.py`, `file/test_file_datasource_processor.py` (400 L), `file/test_file_datasource_update_processor.py`, `loader/test_file_loader.py` (311 L, every assertion `assert len(documents) == 1` meaning one *file*, not one chunk), plus loader tests for git/svn/confluence/jira/csv/eml/msg/vsdx/image.

### Testing Framework and Patterns

pytest `^8.3.1` with `pytest-asyncio ^0.23.7`, `pytest-cov ^5.0.0`, `pytest-env ^1.1.3`, `pytest-mock ^3.14.0`, `pytest-httpx ^0.35.0`. Config in `pytest.ini` (`testpaths=tests`, `pythonpath=src`, `--import-mode=importlib`, `ENV=local`). No `[tool.pytest]` in pyproject.

Run: `make test` → `poetry run pytest tests/`; coverage `make coverage`; targeted `poetry run pytest tests/codemie/datasource/sharepoint -q`.

Mocking style: `unittest.mock.patch` on fully-qualified loader methods (e.g. `@patch("codemie.datasource.loader.sharepoint_loader.SharePointLoader._make_graph_request")`), `MagicMock` payloads shaped like Graph JSON. No `responses`/`respx`/`freezegun`/`testcontainers`; `pytest-httpx` is a dev dep but unused here.

Fixtures: root `tests/conftest.py` loads `tests/.env.test` (`ENABLE_FILE_MULTIPROCESSING=false`) and patches the Postgres engine. **No conftest exists under `tests/codemie/datasource/`** — SharePoint fixtures are defined inline per test file.

Assertion precedent for multi-page, at `tests/codemie/datasource/loader/test_pdf_plumber_loader.py:225` (`test_lazy_load_multiple_pages`, fully mocked `pdfplumber.open`): `assert len(documents) == 2`, `assert documents[0].metadata["page"] == 1`, `assert documents[1].metadata["total_pages"] == 2`. `test_docx_loader.py` builds real .docx via `python-docx` on the fly and asserts `len(docs) == 1` plus content substrings.

Binary sample docs in repo: `tests/codemie_tools/file_analysis/samples/sample.pdf`, `sample.pptx`, `sample.eml`, `sample.msg`; `tests/codemie_tools/file_analysis/docx/test.docx`; `tests/codemie_tools/file_analysis/pptx/test.pptx`; `tests/codemie/datasource/loader/fixtures/sample.vsdx` (the only fixture under the datasource tests).

### Coverage Gaps

- No test anywhere feeds a **multi-Document** return through `SharePointLoader._process_file_item`; the only test hardcodes a one-element list, so a regression to page-1-only passes green.
- No SharePoint test asserts chunk counts, or that N pages produce N distinct indexed documents, or that chunk identity is unique.
- No end-to-end test from `lazy_load` → splitter → `store.add_documents` for a binary file on either path; `_split_documents` is only tested with a mocked splitter (`tests/codemie/datasource/file/test_file_datasource_processor.py:141`).
- No real multi-page PDF fixture exists under `tests/codemie/datasource/`; pdfplumber pages are faked with `MagicMock`.
- DOCX has no multi-chunk test at all — all assertions are `len(docs) == 1`, so DOCX truncation is invisible to the suite.
- `_download_and_extract_file`, `_extract_documents_from_bytes`, `_extract_documents_from_bytes_multiprocess` (`sharepoint_loader.py:1042-1101`) are entirely untested; the multiprocess branch is additionally disabled by `ENABLE_FILE_MULTIPROCESSING=false` in `tests/.env.test`.
- **`rrf._filter_duplicates` has no test covering chunks that share a source** — the actual collapse point is unguarded.
- Duplicated/diverging SharePoint suites: new tests belong in `loader/test_sharepoint_loader.py` (maintained), but the file-extraction test classes currently live only in the older `sharepoint/test_sharepoint_loader.py`.

---

## 5. Configuration and Environment

### Environment Variables

| VAR | purpose | default |
|---|---|---|
| `ELASTIC_URL` | ES endpoint | `http://localhost:9200` |
| `ELASTIC_USERNAME` / `ELASTIC_PASSWORD` | ES auth | `""` |
| `ELASTIC_DATASOURCE_REPLICAS` | `number_of_replicas` on every datasource index | `1` |
| `ENABLE_FILE_MULTIPROCESSING` | routes SharePoint/File extraction to a subprocess pool (`configs/config.py:818`) | `False` |
| `FILE_DATASOURCE_MULTIPROCESSING_MAX_WORKERS` | pool size | `2` |
| `FILE_MULTIPROCESSING_MAX_EXECUTED_TASK_PER_WORKER` | worker recycle | `100` |
| `IMAGE_INDEXING_MAX_SIZE_BYTES` | max image size for indexing | 10 MB |
| `FILES_STORAGE_MAX_UPLOAD_SIZE` | File datasource upload cap (not applied to SharePoint) | 100 MB |
| `DATASOURCE_CONCURRENCY_LIMIT_ENABLED` / `MAX_CONCURRENT_DATASOURCE_INDEXING` / `DATASOURCE_QUEUE_TIMEOUT` | indexing concurrency gate | `False` / `5` / `3600s` |
| `STALE_INDEXING_WATCHDOG_ENABLED` | marks long-running jobs stale | `False` |
| `SHAREPOINT_PKCE_ENABLED` / `SHAREPOINT_OAUTH_CLIENT_ID` / `SHAREPOINT_OAUTH_SCOPES` | OAuth only, not indexing | `False` / `""` / `Sites.Read.All Files.Read.All offline_access User.Read` |
| `DATASOURCES_CONFIG_DIR` | path to the datasources YAML | `<repo>/config/datasources` |

**No env var overrides chunk size, page limits, or the SharePoint file-size cap** — those are YAML-only, and `max_file_size_mb` is additionally per-datasource on `IndexInfo.sharepoint`.

### Configuration Files

- `config/datasources/datasources-config.yaml` — single source of truth for per-loader chunking/batching plus global storage settings. SharePoint block `:137-145`, file `:116-118`, storage `:152-162`.
- `src/codemie/datasource/datasources_config.py` — pydantic models; exports `SHAREPOINT_CONFIG`, `FILE_CONFIG`, `STORAGE_CONFIG`. **`SharePointConfig` (`:107-115`) has no `enable_multiprocessing` / `processing_timeout` / `max_subprocesses` fields, unlike `FileConfig` (`:79-84`) and `CodeConfig` (`:48-50`).**
- `src/codemie/configs/config.py` — global pydantic-settings.
- `src/codemie/datasource/loader/file_extraction_utils.py` — shared `LOADERS` registry and `DEFAULT_LOADER_KWARGS`.

Numeric limits that could truncate (none is a page limit — **no page limit exists anywhere in the codebase**):
- `datasources-config.yaml:140-142` — SharePoint `chunk_size: 2000`, `chunk_overlap: 200`, `max_file_size_mb: 50` (files over the cap are silently skipped entirely at `sharepoint_loader.py:943-945`, not truncated).
- `datasources-config.yaml:138-139` — `loader_batch_size: 20`, `loader_timeout: 300` — the latter is the **HTTP download timeout** at `sharepoint_loader.py:1062`; a slow large PDF returns `[]` with only a `logger.error` (`:1072-1074`).
- `base_datasource_processor.py:803` — per-source future timeout 300s; on `TimeoutError` the **entire remaining chunk set for that source is dropped** and indexing still reports success. All pages of one PDF share one source key.
- `datasources-config.yaml:153` — `embeddings_max_docs_count: 20` (ES sub-batch, `base_datasource_processor.py:993`).
- `datasources-config.yaml:154` — `indexing_bulk_max_chunk_bytes: 104857600` (100 MB).
- `datasources-config.yaml:159` — `processed_documents_threshold: 1000`.
- `src/codemie/core/models.py:813-815` — retrieval `ElasticSearchKwargs`: `k=20`, `fetch_k=100` (shared with File datasource).

Elasticsearch: `src/codemie/core/dependecies.py:105-116` builds a langchain `ElasticsearchStore`; the only custom setting is `number_of_replicas`. **No explicit mapping, no `ignore_above` tuning, no analyzer** — the index is created by `store._store._create_index_if_not_exists()` (`base_datasource_processor.py:664`) with langchain defaults. Document `_id`s are langchain-generated UUIDs, so page chunks cannot overwrite each other by ID (ruling out ID collision as a cause — the data IS all in ES). Index name from `sharepoint_datasource_processor.py:155-157`. Incremental reindex deletes by `metadata.source.keyword` (`:197-202`), relying on the dynamic `.keyword` subfield with ES default `ignore_above: 256`.

### Feature Flags and Deployment Concerns

- `ENABLE_FILE_MULTIPROCESSING` routes SharePoint extraction through `file_process_pool` (`sharepoint_loader.py:1067-1070`, `_extract_documents_from_bytes_multiprocess` `:1076-1087`). SharePoint's multiprocess path calls `.result()` with **no timeout**, and has no `processing_timeout` config field.
- Parser selection is **not** flag-driven: `LOADERS` (`file_extraction_utils.py:51-69`) hardcodes PDF→`PDFPlumberLoader`, DOCX→`DocxLoader`, PPTX→`UnstructuredPowerPointLoader`.
- Image/OCR parser selection is implicit (`file_extraction_utils.py:82-98`): multimodal LLM available → `LLMImageBlobParser`, else → `TesseractBlobParser`.
- Per-datasource toggles: `include_pages`, `include_documents`, `include_lists`, `files_filter`, `max_file_size_mb`.
- Indexing runs on FastAPI `BackgroundTasks` (not celery/nats) via `datasource_concurrency_manager.run` (`base_datasource_processor.py:117-132`); scheduled reindex via APScheduler; SharePoint OAuth datasources skip scheduling (`sharepoint_datasource_processor.py:306-319`). Intra-job `ThreadPoolExecutor(max_workers=STORAGE_CONFIG.indexing_threads_count=20)` (`:769`).
- Dockerfile runtime stage (`:105-135`) installs `pandoc`, texlive, pango, subversion, etc. **but not `tesseract-ocr`** despite `pytesseract` being a dependency and `TesseractBlobParser` being the non-LLM fallback; also no `poppler-utils` or `libreoffice`. `deploy-templates/values.yaml` sets only env vars, so all environments run the same image — but multimodal-LLM availability differs per environment, and that is what selects the image parser.
- Resource ceiling: `deploy-templates/values.yaml:492-498` — `cpu: 2 / memory: 2048Mi`.
- **No Alembic migration needed** — datasource content lives in ES with dynamic mappings; Alembic covers Postgres only.

---

## 6. Risk Indicators

1. **Fix must span two layers.** The indexing layer writes metadata (`sharepoint_datasource_processor.py:279-286`, `base_datasource_processor.py:608-611`) and the retrieval layer consumes it for dedup (`rrf.py:95-116`). A change confined to either layer alone leaves the bug live. This is the single biggest scoping risk.
2. **An existing test locks in the defect.** `tests/codemie/datasource/sharepoint/test_sharepoint_datasource_processor.py:506` asserts `_process_chunk` "strips extra metadata". Fixing Defect 1 requires rewriting an assertion that currently encodes the bug as intended behavior — a reviewer may push back without the ticket context.
3. **Reindex is mandatory and incremental reindex will NOT do it.** Chunks already in ES lack `chunk_num`/`page`, so a retrieval-only change cannot recover them. Worse, `_cleanup_data_for_incremental_reindex` deletes by `metadata.source.keyword`, and `modified_since` (`sharepoint_datasource_processor.py:166-168`) skips unchanged files — an incremental run will not re-pull the PDFs. Users need a **full reprocess**. No migration path exists for existing indices.
4. **Stale-chunk leak on long URLs.** Incremental delete uses `metadata.source.keyword` with ES default `ignore_above: 256`. SharePoint `webUrl`s longer than 256 chars are not indexed as keyword, so stale chunks for those files are never deleted — a pre-existing correctness hazard that the reindex will surface.
5. **Repo-wide blast radius if fixed in the base.** Correcting `base_datasource_processor._split_documents` changes chunk numbering for Git, SVN, Confluence, GoogleDoc, ADO, Jira, and Xray simultaneously. Correcting the `rrf` dedup key changes retrieval for every datasource type. High regression surface, low existing test coverage on those paths.
6. **The same defect family exists in 4+ other connectors** (`jira_datasource_processor.py:144-147`, `xray_datasource_processor.py:137-140`, `azure_devops_wiki_loader.py:126-157`, `azure_devops_work_item_loader.py:141-165`). Scope creep pressure: they are genuinely broken the same way, but are not in this ticket.
7. **Two additional UI/tool surfaces repeat the same dedup key** and will need matching fixes or they will keep collapsing: `workflows/utils/utils.py:308-310` (documents-tree/preview) and `agents/utils.py:238-246` (code-index listing).
8. **`MAX_CHUNKS_FOR_SINGLE_DOCUMENT = 20`** (`kb.py:87`, `:213`) silently excludes any source with more than 20 chunks from LLM source routing. Large PDFs will still retrieve poorly after the metadata fix — this can make the fix look incomplete during verification.
9. **Missing `tesseract-ocr` in the Dockerfile** combined with `extract_images=True` in `DEFAULT_LOADER_KWARGS` and a swallowed `ValueError` at `file_extraction_utils.py:180-181` is a second, environment-dependent partial-extraction path: a failure on page 2's image parsing yields exactly page 1, silently. This shape matches the symptom too and may mask or compound the metadata bug in environments without a multimodal LLM configured. Adding packages requires pinned Debian versions per `.hadolint.yaml`.
10. **Silent whole-file loss paths** that will not show as failures: 300s future timeout drops the remaining chunk set for a source while reporting success (`base_datasource_processor.py:803`); 300s HTTP download timeout returns `[]` with a log line only (`sharepoint_loader.py:1062-1074`); files over `max_file_size_mb: 50` are skipped entirely (`:943-945`).
11. **SharePoint's multiprocess extraction calls `.result()` with no timeout** (`sharepoint_loader.py:1076-1087`), and `SharePointConfig` lacks the `processing_timeout` field its peers have — a hang risk that a full reindex of many large PDFs makes more likely.
12. **Zero test coverage on every function the fix touches**: `_download_and_extract_file`, `_extract_documents_from_bytes`, `_process_file_item` with multi-Document input, `rrf._filter_duplicates` with shared-source chunks. No real multi-page PDF fixture exists under `tests/codemie/datasource/`; no DOCX multi-chunk test exists.
13. **Duplicated SharePoint test suites** (`loader/test_sharepoint_loader.py` 2735 L vs `sharepoint/test_sharepoint_loader.py` 1627 L) with the file-extraction classes only in the older one — tests risk being added to the wrong file or duplicated.
14. **Memory/OOM during reindex.** Pod limit is 2048Mi; pdfplumber holds page objects and 20 indexing threads run concurrently. A full reindex of many multi-page PDFs — the exact remediation this fix requires — is the highest-memory operation in the system.
15. **A prior fix for this same symptom already shipped and did not resolve it** (EPMCDME-11342, `9a144ca32`). The obvious parsing-level explanation is already exhausted; a fix that stops at the parser will regress the ticket.
16. **No documentation exists for the indexing pipeline** in `.ai-run/guides/` or `docs/` — all conventions for this subsystem must be inferred from code.
17. **`chunk_size` differs between the two datasources** (SharePoint 2000/200, File 1500/100). Verification comparing SharePoint to File output will not produce identical chunk counts even after the fix; do not treat that difference as a failure.

---

## 7. Summary for Complexity Assessment

**Layers and change surface.** This bug is not in the parser, and it is not confined to SharePoint. A prior commit (EPMCDME-11342) already consolidated file extraction into a shared utility that both datasources use, and `PDFPlumberLoader` correctly yields every page. The defect is a three-part chain in chunk-identity metadata: SharePoint's `_process_chunk` override discards the `chunk_metadata` it is handed (`sharepoint_datasource_processor.py:279-286`); the base `_split_documents` assigns `chunk_num` per-Document and only when a document splits into more than one chunk (`base_datasource_processor.py:608-611`); and the retrieval layer deduplicates on `f"{source}-{chunk_num}"` with a docstring that says "each source should only have one document" (`rrf.py:95-116`). Because SharePoint sets `source` to the file's `webUrl` for every page, all chunks of a file share one dedup key and exactly one survives — normally page 1. The File datasource is immune purely because it overrides `_split_documents` with per-file numbering. This explains the PDF and DOCX asymmetry the user reported: DOCX (one Document, many chunks) breaks on the metadata wipe alone; PDF (many Documents, one chunk each) breaks on the wipe *and* the conditional numbering. Expect a change surface of roughly 3-6 source files across the orchestration and retrieval layers, plus 3-5 test files. A SharePoint-only fix is provably insufficient; the minimum correct fix touches shared code with repo-wide reach (Git, SVN, Confluence, GoogleDoc, ADO, Jira, Xray all flow through the same base and the same RRF filter), and two further surfaces (`workflows/utils/utils.py:308-310`, `agents/utils.py:238-246`) repeat the same dedup key independently.

**Technical novelty.** The fix follows established patterns rather than introducing new ones — the Template Method hooks, the composite-chunk-ID precedent from commits `7fa9b92f9`/`6832ff060`, and the File processor's per-file numbering all provide a working model to copy. There is no new dependency, no new abstraction, and no Alembic migration (datasource content lives in Elasticsearch with dynamic mappings). What raises difficulty above a routine bugfix is the coordination cost: the correct chunk-identity contract has to hold simultaneously across the write path and three separate read paths, and the design decision of *where* to fix it (SharePoint override vs base processor vs RRF key vs some combination) directly determines regression blast radius across seven connectors. That decision is genuinely non-obvious and carries real trade-offs, and an existing test at `test_sharepoint_datasource_processor.py:506` explicitly asserts the buggy behavior as intended, so the fix must also revise a test that encodes the defect as a requirement.

**Test posture and risk factors.** Coverage over the affected code is effectively zero in the precise places that matter. `_download_and_extract_file`, `_extract_documents_from_bytes`, and the multiprocess branch are entirely untested; the one test of `_process_file_item` hardcodes a single-element list and asserts `len(result) == 1`, so a page-1-only regression passes green; `rrf._filter_duplicates` has no test with chunks sharing a source; no SharePoint test asserts chunk counts; the DOCX loader tests all assert `len(docs) == 1`; and there is no real multi-page PDF fixture under `tests/codemie/datasource/` (pdfplumber pages are `MagicMock`s). New fixtures and likely a new conftest will be needed, and the SharePoint suite is duplicated across two files with the extraction tests in the older one. Beyond the code fix, three operational risks should weigh on scoring: existing SharePoint indices must be **fully** reprocessed because incremental reindex both deletes by a `source.keyword` field subject to ES's 256-char `ignore_above` and skips unchanged files via `modified_since`, so it will not re-pull the affected PDFs; that full reindex is the most memory-intensive operation in the system against a 2048Mi pod limit; and several silent whole-file loss paths (300s future timeout, 300s download timeout, 50 MB skip, missing `tesseract-ocr` with a swallowed `ValueError` mid-iteration) can each independently reproduce a "truncated document" symptom, meaning verification must distinguish the metadata fix from these confounders. The fact that a prior fix for this exact symptom already shipped and did not resolve it is the strongest signal that the shallow reading of this ticket is wrong and the shared-code fix is required.
