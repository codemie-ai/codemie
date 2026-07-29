# Multi-chunk document retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every chunk of a multi-chunk document retrievable, instead of only one chunk per file surviving retrieval-time deduplication.

**Architecture:** The chunk-identity contract moves into `BaseDatasourceProcessor`, which numbers chunks per file across the whole indexing run and re-applies the number after `_process_chunk` returns — so a subclass that rebuilds its metadata cannot drop it. Retrieval additionally falls back to the Elasticsearch `_id` when `chunk_num` is absent, so indices written before this change return their full content without a re-index.

**Tech Stack:** Python 3.12, Poetry, pytest, LangChain `Document`, Elasticsearch.

## Global Constraints

- Commit subject format: `EPMCDME-12768: <Short description>` — ticket first, not Conventional Commits.
- Never mention the ticket id, EPAM, or internal URLs inside source or test files. The ticket belongs in the branch name and commit message only.
- Quality gates are Makefile targets: `make ruff` for lint, `make test` for the suite (`Makefile:27`, `Makefile:30`).
- Existing license headers stay at the top of every touched file.
- The chunk metadata key is `chunk_num`; the source metadata key is `BaseDatasourceProcessor.SOURCE` (`"source"`).

---

## Note on a corrected spec item

An earlier revision of the spec listed a fifth test, "Existing test correction", claiming
`test_process_chunk_returns_document_with_correct_metadata`
(`tests/codemie/datasource/sharepoint/test_sharepoint_datasource_processor.py:506`) asserts the
buggy behavior and must be updated. That was wrong: the test calls `_process_chunk` **directly**,
and this design changes `_process_chunk` in no connector — the chunk number is re-applied by
`_split_documents` after it returns. The test stays valid and untouched, and
`sharepoint_datasource_processor.py` needs no change at all.

The spec has been corrected accordingly. Every spec requirement is covered below.

## File Structure

- `src/codemie/datasource/base_datasource_processor.py` — owns chunk identity: run-scoped per-file
  numbering, always assigned, re-applied after `_process_chunk`. This is the single change that
  repairs all seven connectors.
- `src/codemie/service/search_and_rerank/rrf.py` — deduplication tolerates documents indexed before
  the fix.
- `tests/codemie/datasource/test_base_datasource_processor.py` — contract and batch-continuity tests.
- `tests/codemie/datasource/sharepoint/test_sharepoint_datasource_processor.py` — the ticket's own
  scenario, end to end through the real SharePoint processor.
- `tests/codemie/service/search_and_rerank/test_rrf.py` — retrieval-side tests.

No new files. No migration code: Elasticsearch mappings are dynamic and no Alembic revision is involved.

---

### Task 1: Base class owns chunk identity

**Files:**
- Modify: `src/codemie/datasource/base_datasource_processor.py:604-618` (`_split_documents`), plus a
  new counter helper pair and a reset call inside `_load_and_process_documents:662-670`
- Test: `tests/codemie/datasource/test_base_datasource_processor.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `BaseDatasourceProcessor._reset_chunk_numbering() -> None` and
  `BaseDatasourceProcessor._next_chunk_num(document_key: str) -> int`. Every chunk returned by
  `_split_documents` carries `metadata["chunk_num"]` as a 1-based `int`, sequential per file across
  the whole run.

**Test-first: yes — three tests asserting `chunk_num` on chunks produced by `_split_documents`; all fail with `KeyError: 'chunk_num'` because numbering is currently conditional and dropped by metadata-rebuilding subclasses.**

- [ ] **Step 1: Write the failing tests**

Append to `tests/codemie/datasource/test_base_datasource_processor.py`:

```python
class SourceKeyedProcessor(ConcreteDatasourceProcessor):
    """Uses the production metadata key so document grouping matches real connectors."""

    SOURCE = "source"


class MetadataStrippingProcessor(SourceKeyedProcessor):
    """Mirrors SharePoint, Jira and the other connectors that rebuild chunk metadata."""

    def _process_chunk(self, chunk: str, chunk_metadata, document: Document) -> Document:
        return Document(
            page_content=chunk,
            metadata={"source": document.metadata.get("source", "")},
        )


class TestChunkIdentity:
    """Chunk numbering is the base class's contract, not the subclass's."""

    def test_single_chunk_documents_are_numbered(self, mock_user, mock_index):
        processor = SourceKeyedProcessor("ds", mock_user, mock_index)
        page = Document(page_content="short page", metadata={"source": "https://host/file.pdf"})

        result = processor._split_documents([page])

        assert [doc.metadata["chunk_num"] for doc in result["https://host/file.pdf"]] == [1]

    def test_numbering_continues_across_batches(self, mock_user, mock_index):
        processor = SourceKeyedProcessor("ds", mock_user, mock_index)
        source = "https://host/file.pdf"
        first_page = Document(page_content="page one text", metadata={"source": source})
        second_page = Document(page_content="page two text", metadata={"source": source})

        first_batch = processor._split_documents([first_page])
        second_batch = processor._split_documents([second_page])

        assert [doc.metadata["chunk_num"] for doc in first_batch[source]] == [1]
        assert [doc.metadata["chunk_num"] for doc in second_batch[source]] == [2]

    def test_metadata_stripping_subclass_still_gets_chunk_num(self, mock_user, mock_index):
        processor = MetadataStrippingProcessor("ds", mock_user, mock_index)
        source = "https://host/file.docx"
        document = Document(page_content="body", metadata={"source": source, "title": "T"})

        result = processor._split_documents([document])

        assert result[source][0].metadata["chunk_num"] == 1

    def test_numbering_is_independent_per_file(self, mock_user, mock_index):
        processor = SourceKeyedProcessor("ds", mock_user, mock_index)
        first = Document(page_content="alpha", metadata={"source": "https://host/a.pdf"})
        second = Document(page_content="beta", metadata={"source": "https://host/b.pdf"})

        result = processor._split_documents([first, second])

        assert result["https://host/a.pdf"][0].metadata["chunk_num"] == 1
        assert result["https://host/b.pdf"][0].metadata["chunk_num"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/codemie/datasource/test_base_datasource_processor.py::TestChunkIdentity -v`
Expected: FAIL — `KeyError: 'chunk_num'` on every test. `test_single_chunk_documents_are_numbered` and
`test_numbering_continues_across_batches` fail because numbering is skipped when a document yields one
chunk; `test_metadata_stripping_subclass_still_gets_chunk_num` fails because the subclass discards it.

- [ ] **Step 3: Add the run-scoped counter helpers**

In `src/codemie/datasource/base_datasource_processor.py`, add these two methods to
`BaseDatasourceProcessor` directly above `_split_documents`:

```python
    def _reset_chunk_numbering(self) -> None:
        """Begin a fresh per-file chunk sequence for a new indexing run."""
        self._chunk_counters: dict[str, int] = defaultdict(int)

    def _next_chunk_num(self, document_key: str) -> int:
        """Return the next 1-based chunk number for ``document_key``.

        The counter spans the whole indexing run rather than a single batch. A file larger
        than the loader batch size is split across several ``_split_documents`` calls, and
        per-batch numbering would restart mid-file, giving two of its chunks the same
        identity and collapsing them at retrieval.
        """
        if not hasattr(self, "_chunk_counters"):
            self._reset_chunk_numbering()
        self._chunk_counters[document_key] += 1
        return self._chunk_counters[document_key]
```

- [ ] **Step 4: Assign and re-apply the chunk number in `_split_documents`**

Replace the loop body at `src/codemie/datasource/base_datasource_processor.py:604-615` with:

```python
        documents_dict: dict[str, list[Document]] = defaultdict(list)
        for document in docs:
            split_chunks = self._get_splitter(document).split_text(document.page_content)
            document_key = document.metadata.get("file_path", document.metadata.get(self.SOURCE))
            chunk_list = []
            for chunk in split_chunks:
                chunk_number = self._next_chunk_num(document_key)
                chunk_metadata = document.metadata.copy()
                chunk_metadata["chunk_num"] = chunk_number
                processed_chunk = self._process_chunk(chunk, chunk_metadata, document)
                # Subclasses rebuild metadata from their own whitelist, so the chunk number is
                # re-applied here: chunk identity is the base class's contract, not theirs.
                processed_chunk.metadata["chunk_num"] = chunk_number
                chunk_list.append(processed_chunk)
            documents_dict[document_key].extend(chunk_list)
```

Also update the docstring line at `:583` — it currently says a chunk gets "a unique identifier if the
document is split into multiple chunks". Replace that line with:

```
        3. Assigns metadata to each chunk, including a chunk number unique within its source file.
```

- [ ] **Step 5: Reset the counter at the start of each run**

In `_load_and_process_documents`, immediately after the `store._store._create_index_if_not_exists()`
call at `src/codemie/datasource/base_datasource_processor.py:664`, add:

```python
        self._reset_chunk_numbering()
```

This keeps numbering from accumulating across successive re-indexes on a reused processor instance.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `poetry run pytest tests/codemie/datasource/test_base_datasource_processor.py -v`
Expected: PASS — the four new tests plus every pre-existing test in the file.

- [ ] **Step 7: Run the datasource suite for regressions**

Run: `poetry run pytest tests/codemie/datasource/ -q`
Expected: PASS. This covers the File datasource, whose `_split_documents` override delegates its
single-document path to `super()._split_documents`, and the six other connectors that rebuild chunk
metadata.

- [ ] **Step 8: Lint**

Run: `make ruff`
Expected: exits successfully.

- [ ] **Step 9: Commit**

```bash
git add src/codemie/datasource/base_datasource_processor.py tests/codemie/datasource/test_base_datasource_processor.py
git commit -m "EPMCDME-12768: Number document chunks per file in the base processor"
```

---

### Task 2: Retrieval keeps every distinct chunk

**Files:**
- Modify: `src/codemie/service/search_and_rerank/rrf.py:95-116` (`_filter_duplicates`)
- Test: `tests/codemie/service/search_and_rerank/test_rrf.py`

**Interfaces:**
- Consumes: `metadata["chunk_num"]` as produced by Task 1.
- Produces: no signature change. `_filter_duplicates(results: dict) -> list` keeps reading the same
  dictionary, whose keys are Elasticsearch document ids.

**Test-first: yes — a test asserting that four chunks of one source with no `chunk_num` all survive; it fails returning 1 document, because the key collapses to `source-0`.**

- [ ] **Step 1: Write the failing tests**

Append to `tests/codemie/service/search_and_rerank/test_rrf.py`, inside `class TestRRF`:

```python
    def _rrf(self, search_results):
        return RRF(
            search_results=search_results,
            doc_paths=[],
            top_k=10,
            exact_match_field='exact_match_field',
            source_field='source',
            chunk_field='chunk_num',
        )

    def test_numbered_chunks_of_one_source_all_survive(self):
        search_results = [
            [
                Document(
                    page_content=f"page {n}",
                    metadata={'source': 'https://host/file.pdf', 'exact_match_field': 'p', 'chunk_num': n},
                ),
                0.5,
                uuid.uuid4(),
            ]
            for n in range(1, 5)
        ]

        results = self._rrf(search_results).execute()

        assert len(results) == 4
        assert sorted(doc.metadata['chunk_num'] for doc in results) == [1, 2, 3, 4]

    def test_legacy_chunks_without_chunk_num_survive_via_document_id(self):
        search_results = [
            [
                Document(
                    page_content=f"page {n}",
                    metadata={'source': 'https://host/file.pdf', 'exact_match_field': 'p'},
                ),
                0.5,
                uuid.uuid4(),
            ]
            for n in range(4)
        ]

        results = self._rrf(search_results).execute()

        assert len(results) == 4

    def test_genuine_duplicates_are_still_collapsed(self):
        duplicate = {'source': 'https://host/file.pdf', 'exact_match_field': 'p', 'chunk_num': 1}
        search_results = [
            [Document(page_content="same chunk", metadata=dict(duplicate)), 0.5, uuid.uuid4()],
            [Document(page_content="same chunk", metadata=dict(duplicate)), 0.4, uuid.uuid4()],
        ]

        results = self._rrf(search_results).execute()

        assert len(results) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/search_and_rerank/test_rrf.py -v`
Expected: `test_legacy_chunks_without_chunk_num_survive_via_document_id` FAILS with
`assert 1 == 4` — all four chunks share the key `https://host/file.pdf-0`. The other two new tests
pass already and are there to pin behavior that must not regress.

- [ ] **Step 3: Fall back to the document id**

Replace `_filter_duplicates` in `src/codemie/service/search_and_rerank/rrf.py:95-116` with:

```python
    def _filter_duplicates(self, results: dict) -> list:
        """
        Filter out duplicate documents.

        A chunk is identified by its source plus its chunk number. Documents indexed before
        chunk numbering was enforced carry no chunk number; for those the Elasticsearch
        document id is used instead, so their chunks stay distinct rather than collapsing
        into a single surviving entry.
        """
        seen_sources = set()
        filtered_results = []

        for doc_id, value in results.items():
            try:
                _, doc = value
            except ValueError:
                doc = value

            source = doc.metadata[self.source_field]
            chunk = doc.metadata.get(self.chunk_field)
            key = f"{source}-{chunk}" if chunk is not None else f"{source}-id:{doc_id}"

            if key not in seen_sources:
                filtered_results.append(doc)
                seen_sources.add(key)

        return filtered_results
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/search_and_rerank/ -v`
Expected: PASS, including the pre-existing `test_basic_rrf`.

- [ ] **Step 5: Lint**

Run: `make ruff`
Expected: exits successfully.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/search_and_rerank/rrf.py tests/codemie/service/search_and_rerank/test_rrf.py
git commit -m "EPMCDME-12768: Keep distinct chunks when deduplicating search results"
```

---

### Task 3: Regression test for the reported scenario

**Files:**
- Test: `tests/codemie/datasource/sharepoint/test_sharepoint_datasource_processor.py`

**Interfaces:**
- Consumes: `_next_chunk_num` / `_reset_chunk_numbering` from Task 1, through the real
  `SharePointDatasourceProcessor`.
- Produces: nothing consumed by later tasks.

This task adds no production code. It pins the ticket's own scenario — a multi-page PDF and a
multi-chunk DOCX arriving through the SharePoint processor, the PDF spanning more than one batch —
against the real connector rather than a test double, so a future connector-level regression is caught
where the bug was reported.

**Test-first: yes — both tests fail before Task 1 is applied; run them against the pre-Task-1 code path only if verifying the sequence, otherwise they pass immediately and serve as regression cover.**

- [ ] **Step 1: Write the tests**

Append to `tests/codemie/datasource/sharepoint/test_sharepoint_datasource_processor.py`:

```python
class TestSharePointMultiChunkRetrievability:
    """Every chunk of a SharePoint file must keep a distinct identity."""

    def test_pdf_pages_spanning_two_batches_get_unique_identities(self, sharepoint_processor):
        source = "https://tenant.sharepoint.com/sites/testsite/report.pdf"
        pages = [
            Document(
                page_content=f"Content of page {n}.",
                metadata={"source": source, "title": "report.pdf", "type": "document", "page": n},
            )
            for n in range(1, 26)
        ]

        first_batch = sharepoint_processor._split_documents(pages[:20])
        second_batch = sharepoint_processor._split_documents(pages[20:])

        chunks = first_batch[source] + second_batch[source]
        identities = [(doc.metadata["source"], doc.metadata["chunk_num"]) for doc in chunks]

        assert len(chunks) == 25
        assert len(set(identities)) == 25

    def test_docx_chunks_keep_their_numbers(self, sharepoint_processor):
        source = "https://tenant.sharepoint.com/sites/testsite/handbook.docx"
        document = Document(
            page_content="word " * 6000,
            metadata={"source": source, "title": "handbook.docx", "type": "document"},
        )

        result = sharepoint_processor._split_documents([document])

        chunks = result[source]
        assert len(chunks) > 1
        assert len({doc.metadata["chunk_num"] for doc in chunks}) == len(chunks)
```

- [ ] **Step 2: Run the tests**

Run: `poetry run pytest tests/codemie/datasource/sharepoint/test_sharepoint_datasource_processor.py -v`
Expected: PASS, including the pre-existing `TestSharePointProcessorChunk` tests, which are unaffected
because `_process_chunk` was not modified.

If `test_docx_chunks_keep_their_numbers` reports only one chunk, the splitter did not divide the body:
raise the repetition count in `page_content` until it exceeds `SHAREPOINT_CONFIG.chunk_size` (2000
tokens) and re-run. The assertion `len(chunks) > 1` guards the test's own premise.

- [ ] **Step 3: Run the full suite**

Run: `make test`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/codemie/datasource/sharepoint/test_sharepoint_datasource_processor.py
git commit -m "EPMCDME-12768: Cover multi-page SharePoint document retrieval"
```

---

## Self-review

**Spec coverage.** Write path (run-scoped per-file numbering, always assigned, re-applied after
`_process_chunk`) → Task 1. Read path (`_id` fallback) → Task 2. Acceptance criteria on PDF, DOCX,
cross-batch files, metadata-rebuilding subclasses, legacy indices, genuine duplicates, and unchanged
File datasource behavior → Tasks 1–3, plus the datasource-suite regression run in Task 1 Step 7. The
spec's fifth test item is dropped for the reason stated under "Correction to the spec". No migration
code, matching the spec.

**Behavior change.** The spec's disclosure — single-chunk documents now cite as `source-1` in
`search_kb.format_document` — needs no code and no test; it belongs in the MR description.

**Type consistency.** `_next_chunk_num(document_key: str) -> int` and `_reset_chunk_numbering() -> None`
are defined in Task 1 and referenced under those exact names in Tasks 2 and 3. `chunk_num` is an `int`
everywhere; `_filter_duplicates` distinguishes "absent" with `is not None`, so a legitimate `0` would
not be mistaken for missing.

**No placeholders.** Every step carries runnable code or an exact command.
