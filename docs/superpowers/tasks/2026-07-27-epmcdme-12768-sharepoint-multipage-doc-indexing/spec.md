# Multi-chunk documents lose all but one chunk at retrieval

## Problem

Documents indexed through the SharePoint datasource are retrievable only one chunk
at a time. An agent asked for a table of contents, a section count, or a total page
count answers from the first page alone. The same files indexed through the File
datasource answer correctly from the full content.

The reported symptom names PDF. DOCX behaves the same way, and the defect is not
specific to either format or to SharePoint.

## Root cause

The parser is not at fault. Both datasources already share
`extract_documents_from_bytes`, and `PDFPlumberLoader` yields every page. Every page
reaches Elasticsearch. The loss happens at retrieval, through a three-link chain that
destroys chunk identity.

**1. The SharePoint processor discards chunk metadata.**
`sharepoint_datasource_processor._process_chunk` accepts a `chunk_metadata` argument
and ignores it, rebuilding metadata from a `source`/`title`/`type` whitelist. Because
`source` is the file's `webUrl`, every chunk of a file becomes metadata-identical.

**2. The base splitter numbers chunks per Document, not per file, and only sometimes.**
`base_datasource_processor._split_documents` assigns `chunk_num` inside a single
`Document`, and only when that Document splits into more than one chunk. A PDF page is
its own Document and usually fits in one chunk, so it is never numbered.

**3. Retrieval deduplicates on the degenerate identity.**
`rrf._filter_duplicates` keys on `f"{source}-{chunk_num}"`. With `source` shared and
`chunk_num` absent, the key collapses to one per file and a single chunk survives.

The File datasource is unaffected because it overrides `_split_documents` and numbers
chunks per file after grouping, and does not strip metadata.

This explains the format asymmetry. DOCX yields one Document and many chunks, so it is
broken by defect 1 alone. PDF yields many Documents of one chunk each, so it is broken
by defects 1 and 2 together.

The same metadata-rebuilding pattern exists in six further connectors — jira, xray,
confluence, azure_devops_wiki, azure_devops_work_item, code_summary — so any of their
items long enough to split is truncated the same way.

## Approach

Move the chunk-identity contract into the base class, where a subclass cannot opt out
of it, and make retrieval tolerant of documents indexed before the fix.

### Write path — `base_datasource_processor._split_documents`

Assign `chunk_num` per file, sequentially, always — including single-chunk documents —
and re-stamp it onto the `Document` returned by `_process_chunk`. A subclass may rebuild
its metadata however it likes and still cannot drop chunk identity. This is what repairs
all seven connectors through one change.

The counter is scoped to the indexing run, not to the batch. With
`sharepoint_loader.loader_batch_size: 20`, a 25-page PDF spans two batches; a per-batch
counter would restart at page 21 and reproduce the defect on exactly the large documents
the ticket is about. Sequencing is safe: `_split_documents` runs serially per batch
(`base_datasource_processor.py:756`) and parallelism begins below it, in
`_process_document`.

The counter initializes lazily, because tests call `_split_documents` directly without
going through `_load_and_process_documents`, and resets at the start of a run so numbering
does not accumulate across re-indexes.

A running counter is used rather than page metadata because loader metadata keys are not
uniform: `PDFPlumberLoader` emits `page`, the markitdown loaders emit `page_number`, and
XLSX — loaded with `split_by_page: True` — emits multiple Documents with neither. Any
page-derived identity would fix PDF and leave XLSX, and EML/ZIP attachments, broken.

### Read path — `rrf._filter_duplicates`

Fall back to the Elasticsearch `_id` when `chunk_num` is absent, so datasources indexed
before this fix return their full content without a re-index. The input dictionaries are
already keyed by `_id` (`rrf.py:52-53`), so iterating `.items()` provides it without a
signature change.

### Deliberately out of scope

`_filter_duplicates` is kept. After the write-path fix it is close to inert — genuine
duplicates share an `_id` and are already collapsed by the dictionary above it — but
removing it is a separate decision with a different blast radius.

Page-metadata keys are not unified. `chunk_num` is assigned in loader emission order,
which is reading order, and that is sufficient for the existing sort in `rrf.py:55-62`.

## Behavior change to disclose

`search_kb.format_document` (`search_kb.py:170-179`) appends `chunk_num` to the source
label shown to the agent. Now that the number is always present, single-chunk documents
in every datasource cite as `source-1` rather than `source`. This is display-only —
source routing compares `doc_paths` against the `source` field, not the label — but it
must be called out in the MR, or a reviewer will read it as a regression.

## Acceptance criteria

- A multi-page PDF indexed via SharePoint returns content from pages past page 1, matching
  File datasource behavior.
- A multi-chunk DOCX indexed via SharePoint returns content from beyond its first chunk.
- A file larger than one processing batch (>20 Documents) is numbered continuously; no two
  of its chunks share a `(source, chunk_num)` pair.
- A connector whose `_process_chunk` rebuilds metadata still produces chunks carrying
  `chunk_num`.
- Retrieval against an index written before this fix returns more than one chunk per file.
- Genuine duplicates are still collapsed by `_filter_duplicates`.
- File datasource behavior is unchanged.

## Testing

Each test is written failing first.

1. **Batch continuity (base).** Two consecutive `_split_documents` calls covering one file
   continue its numbering instead of restarting. This is the test that pins the
   cross-batch correctness the naive fix would miss.
2. **Contract enforcement (base).** A subclass whose `_process_chunk` discards its
   metadata argument still yields chunks carrying `chunk_num`.
3. **SharePoint end to end.** 25 page-Documents across two batches produce 25 distinct
   `(source, chunk_num)` pairs.
4. **Retrieval (rrf).** Chunks with distinct `chunk_num` all survive; chunks lacking
   `chunk_num` survive via the `_id` fallback; genuine duplicates are still removed.

No connector's `_process_chunk` changes, so the existing
`test_process_chunk_returns_document_with_correct_metadata`
(`tests/codemie/datasource/sharepoint/test_sharepoint_datasource_processor.py:506`) stays
valid: it exercises `_process_chunk` directly, and the chunk number is re-applied after that
method returns. `sharepoint_datasource_processor.py` needs no change at all — the fix lives
entirely in `base_datasource_processor.py` and `rrf.py`.

## Migration

No migration code. Elasticsearch mappings are dynamic and no Alembic revision is involved.

Indices written before this fix regain full retrieval immediately through the `_id`
fallback. Page ordering cannot be recovered for them, because neither `page` nor
`chunk_num` was stored. Restoring order requires a full re-index; an incremental one skips
unchanged files via `modified_since`. This belongs in the release notes.
