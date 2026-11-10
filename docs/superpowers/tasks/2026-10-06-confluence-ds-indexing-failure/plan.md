# Confluence Per-Page 404 Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix EPMCDME-15405 — a Confluence page that 404s during indexing (deleted/trashed content) must be skipped, not abort the whole `kb-data-source` run.

**Architecture:** Override `ConfluenceDatasourceLoader.process_pages` to wrap each page's `is_public_page`/`process_page` call in a plain `try/except`: skip-and-log on `requests.exceptions.HTTPError` with `status_code == 404`, re-raise everything else unchanged. Track the skip count on an instance counter and expose it via a new `get_load_stats()`, mirroring the `FAILED_DOCUMENTS_KEY` convention already used by `sharepoint_loader.py` / `xwiki_loader.py`.

**Tech Stack:** Python, `langchain_community.document_loaders.ConfluenceLoader` (0.4.1), `requests`, pytest/`unittest.mock`.

**Spec:** No approved spec — requirements arrived inline (ticket text reproduced in Acceptance Criteria below). Codebase research: `docs/superpowers/tasks/2026-10-06-confluence-ds-indexing-failure/technical-analysis.md`.

## Acceptance criteria

- [ ] A page that 404s during per-page processing (`is_public_page` or `process_page`) is skipped instead of aborting the datasource run.
- [ ] The other pages in the same CQL batch are still yielded as `Document`s.
- [ ] A warning is logged for each skipped page, including the page id and the CQL (`self.cql`).
- [ ] Skipped pages are tracked and exposed via `get_load_stats()` under `BaseDatasourceLoader.FAILED_DOCUMENTS_KEY` (Confluence currently has no `get_load_stats()`).
- [ ] A 403 (or any non-404 error) raised during the same per-page processing path is **not** swallowed — it still propagates and aborts the run.
- [ ] The existing CQL-search-level tenacity retry (`_search_content_by_cql`, `is_public_page`'s `@_confluence_retry`, transient 502/503/504/`ConnectionError`) is unchanged; the 404 skip uses no retry decorator.
- [ ] New unit tests in `tests/codemie/datasource/loader/test_confluence_loader.py` cover: skip-and-continue + warning content, `get_load_stats()` reporting, and 403 propagation.

## Global Constraints

- Touch only `src/codemie/datasource/loader/confluence_loader.py` and `tests/codemie/datasource/loader/test_confluence_loader.py`.
- Do not touch `confluence_datasource_processor.py`, `_search_content_by_cql`/`_is_transient_error`, `from_confluence_index_info`/`to_confluence_index_info`, or `datasource_health_check_service.py` — out of scope per the ticket.
- No whole-suite quality-gate, manual/browser-verification, code-review, or commit-formatting task — the calling flow owns those. Commit per task using the repository's existing convention.

## Review Focus

- A 404 on the *first* page of a batch must not stop the remaining pages in that same batch (loop continuation, not early return) — covered by Task 1.
- A 404 from `is_public_page`'s restrictions check vs. one from `process_page` itself — both call sites sit inside the same `try` in the override, so either origin is covered — covered by Task 1.
- Multiple 404s in one batch must accumulate the counter, not just flip a boolean — covered by Task 1/2 (counter incremented per page).
- A batch where every page 404s must still complete `lazy_load()` without raising, with `get_load_stats()` reporting all of them failed — covered by Task 1/2 together (no early abort, no success-count floor).
- A 403 immediately after a successful page must still abort the batch, not get caught by the same `except` as 404 — covered by Task 1.

---

### Task 1: Isolate per-page 404s in `process_pages`

**Test-first:** yes — `TestProcessPagesSkipsNotFound::test_404_page_is_skipped_others_yielded_and_logged` and `::test_403_during_page_processing_is_not_swallowed` (both written and run first; they fail against the current `ConfluenceDatasourceLoader`, which has no `process_pages` override and so cannot skip a 404 at page granularity).

**Files:**
- Modify: `src/codemie/datasource/loader/confluence_loader.py:19` (imports), `:77-166` (class body — add override + counter reset)
- Test: `tests/codemie/datasource/loader/test_confluence_loader.py` (new test class, appended at end of file)

**Interfaces:**
- Produces: `ConfluenceDatasourceLoader.process_pages(pages, include_restricted_content, include_attachments, include_comments, include_labels, content_format, ocr_languages=None, keep_markdown_format=False, keep_newlines=False) -> Iterator[Document]` — same signature as upstream `ConfluenceLoader.process_pages`, now overridden.
- Produces: `self._failed_pages: int` (class default `0`, reset at the top of `lazy_load()`) — consumed by Task 2's `get_load_stats()`.

- [ ] **Step 1: Write the failing tests** — append to `tests/codemie/datasource/loader/test_confluence_loader.py`:

```python
class TestProcessPagesSkipsNotFound:
    """Per-page 404 isolation (EPMCDME-15405): one gone page must not abort the whole run."""

    @staticmethod
    def _http_error(status_code: int) -> requests.exceptions.HTTPError:
        response = MagicMock()
        response.status_code = status_code
        return requests.exceptions.HTTPError(response=response)

    @pytest.fixture
    def loader(self, confluence_loader):
        confluence_loader.include_restricted_content = True  # bypass is_public_page in these tests
        return confluence_loader

    @staticmethod
    def _call(loader, pages, process_page_side_effect):
        from unittest.mock import patch as _patch

        with _patch.object(loader, "process_page", side_effect=process_page_side_effect):
            return list(
                loader.process_pages(
                    pages,
                    include_restricted_content=True,
                    include_attachments=False,
                    include_comments=False,
                    include_labels=False,
                    content_format=None,
                )
            )

    def test_404_page_is_skipped_others_yielded_and_logged(self, loader):
        from codemie.datasource.loader.confluence_loader import logger as confluence_logger
        from langchain_core.documents import Document

        def fake_process_page(page, *_, **__):
            if page["id"] == "2":
                raise self._http_error(404)
            return Document(page_content=page["id"])

        with patch.object(confluence_logger, "warning") as mock_warning:
            result = self._call(loader, [{"id": "1"}, {"id": "2"}, {"id": "3"}], fake_process_page)

        assert [d.page_content for d in result] == ["1", "3"]
        mock_warning.assert_called_once()
        logged = str(mock_warning.call_args)
        assert "2" in logged
        assert loader.cql in logged

    def test_403_during_page_processing_is_not_swallowed(self, loader):
        with pytest.raises(requests.exceptions.HTTPError) as exc_info:
            self._call(loader, [{"id": "1"}], self._http_error(403))

        assert exc_info.value.response.status_code == 403
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/codemie/datasource/loader/test_confluence_loader.py::TestProcessPagesSkipsNotFound -v` — expect both tests FAIL (no override exists yet; the plain `process_pages` currently either doesn't exist on the subclass or isn't being exercised this way, so the 404 propagates instead of being skipped).

- [ ] **Step 3: Implement.** In `confluence_loader.py`, add the import (after line 19):

```python
from langchain_community.document_loaders.confluence import ContentFormat
```

Add `_failed_pages: int = 0` as a class attribute on `ConfluenceDatasourceLoader`, insert `self._failed_pages = 0` as the first line of `lazy_load()`'s body (before the `expand = ...` assignment at line 127), and add the override (place it directly above `lazy_load`, i.e. before line 121):

```python
def process_pages(
    self,
    pages: List[dict],
    include_restricted_content: bool,
    include_attachments: bool,
    include_comments: bool,
    include_labels: bool,
    content_format: ContentFormat,
    ocr_languages: Optional[str] = None,
    keep_markdown_format: Optional[bool] = False,
    keep_newlines: bool = False,
) -> Iterator[Document]:
    """Isolate per-page 404s: the CQL search can list a page whose content API 404s
    moments later (deleted/trashed). Skip that page and keep going; 403 and anything
    else still propagates and aborts the run — this is not a transient error, so no
    retry decorator applies here.
    """
    for page in pages:
        try:
            if not include_restricted_content and not self.is_public_page(page):
                continue
            yield self.process_page(
                page,
                include_attachments,
                include_comments,
                include_labels,
                content_format,
                ocr_languages=ocr_languages,
                keep_markdown_format=keep_markdown_format,
                keep_newlines=keep_newlines,
            )
        except requests.exceptions.HTTPError as e:
            if getattr(e.response, "status_code", None) != 404:
                raise
            self._failed_pages += 1
            logger.warning(
                "Confluence loader: skipping page id=%s — content not found (404). cql=%s. error=%s",
                page.get("id"),
                self.cql,
                e,
            )
```

- [ ] **Step 4: Run to verify pass** — same command as Step 2 — expect both tests PASS.

- Commit per the repository's existing convention (see Global Constraints).

---

### Task 2: Implement `get_load_stats()`

**Test-first:** yes — `TestProcessPagesSkipsNotFound::test_404_page_recorded_in_get_load_stats` (written and run first; it fails because `get_load_stats()` currently returns `None`, inherited unimplemented from `BaseDatasourceLoader`).

**Files:**
- Modify: `src/codemie/datasource/loader/confluence_loader.py` (add method to `ConfluenceDatasourceLoader`, after `fetch_remote_stats`)
- Test: `tests/codemie/datasource/loader/test_confluence_loader.py` (new test method on `TestProcessPagesSkipsNotFound`)

**Interfaces:**
- Consumes: `self._failed_pages` (produced by Task 1).
- Produces: `ConfluenceDatasourceLoader.get_load_stats() -> dict[str, Any]`, returning `{BaseDatasourceLoader.FAILED_DOCUMENTS_KEY: <count>}` — consumed by `BaseDatasourceProcessor._persist_load_stats()` (existing, unmodified).

- [ ] **Step 1: Write the failing test** — append to `TestProcessPagesSkipsNotFound`:

```python
    def test_404_page_recorded_in_get_load_stats(self, loader):
        from langchain_core.documents import Document

        def fake_process_page(page, *_, **__):
            if page["id"] == "2":
                raise self._http_error(404)
            return Document(page_content=page["id"])

        self._call(loader, [{"id": "1"}, {"id": "2"}], fake_process_page)

        assert loader.get_load_stats() == {loader.FAILED_DOCUMENTS_KEY: 1}
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/codemie/datasource/loader/test_confluence_loader.py::TestProcessPagesSkipsNotFound::test_404_page_recorded_in_get_load_stats -v` — expect FAIL (`get_load_stats` currently returns `None`, inherited from `BaseDatasourceLoader`).

- [ ] **Step 3: Implement** — add to `ConfluenceDatasourceLoader`, after `fetch_remote_stats`:

```python
def get_load_stats(self) -> dict[str, Any]:
    return {self.FAILED_DOCUMENTS_KEY: self._failed_pages}
```

- [ ] **Step 4: Run to verify pass** — same command as Step 2 — expect PASS.

- Commit per the repository's existing convention (see Global Constraints).

---

## Self-Review

**Spec coverage:** every Acceptance Criteria line maps to Task 1 (skip+continue, warning w/ page id+cql, 403 propagation, retry path untouched) or Task 2 (`get_load_stats`/`FAILED_DOCUMENTS_KEY`). No gaps.

**Negative constraints:**
- "must not abort the run" / "skip and continue" → honored by the `continue`-via-exception-swallow in Task 1's override (loop keeps going); no task re-introduces an abort path.
- "do NOT swallow 403/permission errors" → Task 1's `test_403_during_page_processing_is_not_swallowed` pins this; the `except` re-raises anything whose `status_code != 404`.
- "must not go through the existing CQL-search retry path" → Task 1 adds a plain `try/except` with no `@_confluence_retry` decorator; `_search_content_by_cql` and `_is_transient_error` are not touched by either task.
- "do not touch the reindex config round-trip / health-check service / processor" → both tasks' file lists are limited to `confluence_loader.py` and its test file.
- No negative constraints were found unaddressed.

**Type/signature consistency:** `process_pages`' signature matches upstream exactly (required — `lazy_load()` calls it with these exact kwarg names); `get_load_stats()` return type (`dict[str, Any]`) matches `BaseDatasourceLoader.get_load_stats()`'s declared return type and the `BaseDatasourceProcessor._persist_load_stats()` caller's existing `.get(...)` usage.
