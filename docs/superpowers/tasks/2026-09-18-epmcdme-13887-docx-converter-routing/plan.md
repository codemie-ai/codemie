# DOCX-vs-PPTX Converter Routing Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop MarkItDown from misrouting valid `.docx` uploads to `PptxConverter` by giving it an explicit `StreamInfo` extension hint, and add regression coverage for DOCX/PPTX routing.

**Architecture:** `convert_file_to_markdown` already computes `ext` from `file_name` (used today only for the `.xlsb` short-circuit). Pass that same `ext` into `md.convert()` via `stream_info=StreamInfo(filename=file_name, extension=ext or None)`. `DocxConverter`/`PptxConverter` have disjoint `ACCEPTED_FILE_EXTENSIONS`, and MarkItDown tries the extension-based guess before any magika content-based guess when the two conflict — so this hint deterministically wins regardless of what magika guesses from the raw OOXML zip bytes.

**Tech Stack:** Python 3.13, `markitdown` 0.1.2 (`StreamInfo`, `MarkItDown.convert`), pytest, `unittest.mock.patch`/`MagicMock`.

**Spec:** `docs/superpowers/tasks/2026-09-18-epmcdme-13887-docx-converter-routing/spec.md`

## Global Constraints

- Exactly one source file changes: `src/codemie_tools/file_analysis/workers/markdown_workers.py`. Do not touch `markdown_cache_service.py` or `file_analysis_tool.py` (spec's Scope Decision: AC4 is satisfied as a free side effect of the routing fix).
- Do not touch `azure_devops_work_item_loader.py` / `azure_devops_wiki_loader.py` — explicitly out of scope per the spec.
- Use `ext or None`, never bare `ext`, when building `StreamInfo.extension` — an empty string is judged incompatible with magika's guess and silently reintroduces the bug for extensionless filenames (see spec's "`ext or None`, not bare `ext`" section).
- Test file to extend: `tests/codemie_tools/file_analysis/workers/test_markdown_workers.py`. Follow its existing convention: patch `codemie_tools.file_analysis.workers.markdown_workers.MarkItDown` where used for construction-level mocks; for the magika-boundary test, patch `markitdown._markitdown.magika.Magika` (the class), since `convert_file_to_markdown` builds its own `MarkItDown()` instance whose `__init__` does `self._magika = magika.Magika()`.
- No changes to `pyproject.toml`/`poetry.lock` — `markitdown` and its `StreamInfo` export are already available.

---

### Task 1: Add DOCX/PPTX routing regression tests and the `stream_info` fix

**Files:**
- Modify: `src/codemie_tools/file_analysis/workers/markdown_workers.py:21` (import), `:58` (the `md.convert(...)` call)
- Test: `tests/codemie_tools/file_analysis/workers/test_markdown_workers.py` (append a new `TestDocxPptxRouting` class after the existing `TestConvertFileToMarkdownCyrillic` class, i.e. after line 174)

**Interfaces:**
- Consumes: `convert_file_to_markdown(file_bytes: bytes, file_name: str, llm_client=None, llm_model=None) -> str` (existing signature, unchanged) from `codemie_tools.file_analysis.workers.markdown_workers`.
- Consumes: real fixtures `tests/codemie_tools/file_analysis/docx/test.docx` and `tests/codemie_tools/file_analysis/pptx/test.pptx` (existing files, read as bytes).
- Produces: nothing new for later tasks — this is the terminal code change for this bug fix.

**Test-first: yes — a magika-mocked pptx-like guess forced onto real `test.docx` bytes must reproduce `FileConversionException` mentioning `PptxConverter` before the fix, and convert successfully after it.**

- [ ] **Step 1: Write the failing tests**

Add this class to `tests/codemie_tools/file_analysis/workers/test_markdown_workers.py`, after the `TestConvertFileToMarkdownCyrillic` class (end of file):

```python
class TestDocxPptxRouting:
    """Regression tests for EPMCDME-13887: DOCX must never be routed to PptxConverter.

    MarkItDown falls back to magika's content-based type guess when no stream_info hint is
    given. Because DOCX and PPTX are both OOXML zip containers, magika can misclassify a real
    DOCX as PPTX-like, and MarkItDown then tries PptxConverter first, which fails opening the
    real (docx) bytes with python-pptx. These tests force that exact magika misclassification
    by patching markitdown._markitdown.magika.Magika (class-level, since convert_file_to_markdown
    builds its own MarkItDown() instance whose __init__ does self._magika = magika.Magika()) and
    verify the real DocxConverter/PptxConverter pipeline routes correctly once a stream_info
    extension hint is supplied.
    """

    DOCX_PATH = "tests/codemie_tools/file_analysis/docx/test.docx"
    PPTX_PATH = "tests/codemie_tools/file_analysis/pptx/test.pptx"

    @staticmethod
    def _read(path):
        with open(path, "rb") as f:
            return f.read()

    @staticmethod
    def _make_pptx_like_magika_result():
        """A magika identify_stream() result shaped like a real PPTX-content prediction."""
        result = MagicMock()
        result.status = "ok"
        result.prediction.output.label = "pptx"
        result.prediction.output.is_text = False
        result.prediction.output.extensions = ["pptx"]
        result.prediction.output.mime_type = (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )
        return result

    def test_docx_misclassified_by_magika_still_converts_after_fix(self):
        """Core regression test: a real .docx must convert even when magika guesses pptx."""
        docx_bytes = self._read(self.DOCX_PATH)
        with patch("markitdown._markitdown.magika.Magika") as mock_magika_cls:
            mock_magika_cls.return_value.identify_stream.return_value = (
                self._make_pptx_like_magika_result()
            )
            result = convert_file_to_markdown(docx_bytes, "report.docx")
        assert result is not None
        assert len(result) > 0

    def test_pptx_conversion_unaffected_by_fix(self):
        """No PPTX regression: the real .pptx fixture must still convert, unmocked."""
        pptx_bytes = self._read(self.PPTX_PATH)
        result = convert_file_to_markdown(pptx_bytes, "slides.pptx")
        assert result is not None
        assert len(result) > 0

    def test_plain_docx_happy_path_still_works(self):
        """Guard against regressions in the normal (unmocked) docx conversion path."""
        docx_bytes = self._read(self.DOCX_PATH)
        result = convert_file_to_markdown(docx_bytes, "report.docx")
        assert result is not None
        assert len(result) > 0

    def test_extensionless_filename_passes_none_not_empty_string(self):
        """ext == '' must become stream_info.extension=None, not '' (see spec pitfall note)."""
        docx_bytes = self._read(self.DOCX_PATH)
        with patch("codemie_tools.file_analysis.workers.markdown_workers.MarkItDown") as mock_md_cls:
            mock_result = MagicMock()
            mock_result.text_content = "converted"
            mock_md = mock_md_cls.return_value
            mock_md.convert.return_value = mock_result
            convert_file_to_markdown(docx_bytes, "noext")
        _, kwargs = mock_md.convert.call_args
        assert kwargs["stream_info"].extension is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie_tools/file_analysis/workers/test_markdown_workers.py::TestDocxPptxRouting -v`

Expected:
- `test_docx_misclassified_by_magika_still_converts_after_fix` — **FAILS** with `markitdown._exceptions.FileConversionException` mentioning `PptxConverter threw ValueError ... is not a PowerPoint file` (reproduces the exact production bug).
- `test_pptx_conversion_unaffected_by_fix` — **PASSES already** (no fix needed for this one; it documents the no-regression baseline).
- `test_plain_docx_happy_path_still_works` — **PASSES already** (magika correctly classifies the unmocked real docx bytes on this installed model; documents the happy-path baseline before the fix, per technical-analysis.md's finding that the real fixture does not fool the installed magika).
- `test_extensionless_filename_passes_none_not_empty_string` — **FAILS**, because `mock_md.convert.call_args` has no `stream_info` kwarg at all today (`kwargs["stream_info"]` raises `KeyError`).

- [ ] **Step 3: Write minimal implementation**

In `src/codemie_tools/file_analysis/workers/markdown_workers.py`:

Change the import on line 21 from:

```python
from markitdown import MarkItDown
```

to:

```python
from markitdown import MarkItDown, StreamInfo
```

Change the conversion call on line 58 from:

```python
        result = md.convert(binary_content)
```

to:

```python
        result = md.convert(binary_content, stream_info=StreamInfo(filename=file_name, extension=ext or None))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie_tools/file_analysis/workers/test_markdown_workers.py -v`

Expected: all tests in the file **PASS**, including all four new `TestDocxPptxRouting` cases and every pre-existing test in `TestXlsbPreconversion`, `TestUtf8SafePlainTextConverter`, and `TestConvertFileToMarkdownCyrillic` (no regressions).

- [ ] **Step 5: Commit**

```bash
git add src/codemie_tools/file_analysis/workers/markdown_workers.py tests/codemie_tools/file_analysis/workers/test_markdown_workers.py
git commit -m "EPMCDME-13887: Fix DOCX-vs-PPTX converter misrouting via stream_info hint"
```

---

### Task 2: Handoff communication — sticky-cache limitation

This is not a code task. Per the spec's "Explicitly out of scope" section, `MarkdownCacheService.get_or_convert` permanently caches a `[Conversion failed for {filename}: {exception_type}]` placeholder until `invalidate()` is called. This fix fixes future conversions but does **not** clear any placeholder already cached for the original reporter's file before this fix shipped.

**Test-first: no — this is a documentation/communication step, not a code change.**

- [ ] **Step 1: Add a Jira comment on EPMCDME-13887**

State plainly: the routing fix is in the MR, but if the original reporter already hit the bug, their specific file's cached conversion result may still show the old `"[Conversion failed for ...]"` placeholder until that cache entry is invalidated or naturally expires — ask them to retry the upload (or request a cache invalidation) after the fix ships if they still see the stale error.

- [ ] **Step 2: Include the same note in the MR description**

Add a "Known limitation" section to the MR description with the same explanation, so a reviewer is aware this is a deliberate scope boundary (per spec), not an oversight.

- [ ] **Step 3: Add the `/sanity` reviewer request**

Per `AGENTS.md`'s Repository Rules ("Sanity Regression"): this repo has no pipeline config, so the MR description must ask a reviewer to post `/sanity` — include this request explicitly since the diff touches file-conversion routing logic.
