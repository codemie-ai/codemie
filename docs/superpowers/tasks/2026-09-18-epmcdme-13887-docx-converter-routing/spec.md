# Spec: Fix DOCX-vs-PPTX converter misrouting (EPMCDME-13887)

## Problem

A valid `.docx` uploaded to the CodeMie Assistant fails with:

```
FileConversionException: PptxConverter threw ValueError with message: file ... is not a
PowerPoint file, content type is
'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'
```

## Root cause

`convert_file_to_markdown` (`src/codemie_tools/file_analysis/workers/markdown_workers.py:58`)
calls `md.convert(binary_content)` with no `StreamInfo`. MarkItDown then falls back to
`magika`'s content-based type guess. Because DOCX and PPTX are both OOXML zip containers,
magika's guess can come back PPTX-like for an actual DOCX, and `_convert` tries that guess's
converter (`PptxConverter`) first. `PptxConverter.accepts()` passes on extension/mimetype
grounds (the guess says pptx), but the underlying `python-pptx` open then fails on the real
(docx) bytes, producing the exact error above.

`ext = os.path.splitext(file_name or "")[1].lower()` is already computed at line 43 but is
only used for the `.xlsb` special case — it is never passed into `md.convert()`.

Validated (against the real installed `markitdown` 0.1.2 + real `test.docx`/`test.pptx`
fixtures, by mocking `magika.Magika` to force a pptx-like guess on real docx bytes):
- Without `stream_info`: reproduces the exact production error text.
- With `stream_info=StreamInfo(filename=file_name, extension=ext)`: converts successfully.
- `DocxConverter`/`PptxConverter` have disjoint `ACCEPTED_FILE_EXTENSIONS` /
  `ACCEPTED_MIME_TYPE_PREFIXES`, and the extension-based guess is tried before any
  magika-derived guess, so this hint deterministically wins regardless of what magika guesses.
- Existing PPTX fixture still converts correctly with the same hint applied — no PPTX
  regression.

## Fix

In `markdown_workers.py`, pass `stream_info=StreamInfo(filename=file_name, extension=ext or
None)` into the `md.convert()` call. `StreamInfo` is already a `markitdown` export used
elsewhere in this package (`utf8_safe_plain_text_converter.py`). No manual mimetype resolution
is needed — MarkItDown backfills mimetype from extension internally.

**`ext or None`, not bare `ext`**: `os.path.splitext(file_name or "")[1]` returns `""` (not
`None`) when `file_name` has no extension. Confirmed by reading
`_get_stream_info_guesses`/`copy_and_update` in the installed `markitdown` source: the
magika-guess compatibility check does `base_guess.extension.lstrip(".") not in
result.prediction.output.extensions` — an empty string never appears in that list, so
`extension=""` is unconditionally judged *incompatible* with magika's guess, which then
returns *both* guesses (enhanced-guess-with-empty-extension first). Because
`DocxConverter.accepts()`/`PptxConverter.accepts()` check extension against
`ACCEPTED_FILE_EXTENSIONS`, an empty extension matches neither, so that first guess is
rejected and routing falls straight through to the raw magika guess anyway — silently
reintroducing today's bug for any extensionless filename. Passing `None` instead leaves
MarkItDown's existing extension-absent behavior untouched (mimetype/extension guessing from
content, as today) rather than actively breaking it.

## Scope decision: AC4 (error must name the correct document type)

Two exception-swallowing boundaries downstream of this worker were investigated as candidates
for a change:

- `markdown_cache_service.get_or_convert` — builds
  `f"[Conversion failed for {file_obj.name}: {type(e).__name__}]"`. This already includes the
  original filename (with its real extension) and only the exception *class* name, never the
  exception's message body — so it can never leak a "PptxConverter" mention. **No change
  needed.**
- `file_analysis_tool._fallback_decode_text_file` — appends
  `f". Original error: {str(original_exception)}"`, which *does* include the exception's full
  message text. Today that can surface "PptxConverter ... not a PowerPoint file" for a
  genuinely-docx upload. Once routing is fixed, a `.docx` that still fails will fail inside
  `DocxConverter` (not `PptxConverter`), so `str(original_exception)` will itself name the
  correct converter/type. **No change needed** — the fix at the root cause makes this
  boundary correct as a side effect, since MarkItDown's own exception text is generated from
  whichever converter actually ran.

**Conclusion: the diff is the one-line `stream_info` fix plus tests.** No changes to
`markdown_cache_service.py` or `file_analysis_tool.py` are required to satisfy AC4.

## Explicitly out of scope

- **Sticky cache**: `MarkdownCacheService.get_or_convert` permanently caches a failure
  placeholder until `invalidate()` is called. This is a real second-order risk (a one-time
  misclassification becomes sticky) but is not named in this ticket's acceptance criteria and
  is orthogonal to the routing bug. Not touched in the diff — but call it out explicitly in
  the Jira comment and the MR description as a known limitation: the original reporter's file
  may still show the old cached `"[Conversion failed for ...]"` placeholder after this fix
  ships, until that specific cache entry is invalidated or naturally expires. This is a
  handoff/communication task, not a code task — track it as a plan step.
- **Other `MarkItDown.convert()` call sites** with the same missing-`stream_info` gap
  (`azure_devops_work_item_loader.py`, `azure_devops_wiki_loader.py`) — lower risk since they
  are already routed by `content_type` upstream, and out of scope per explicit instruction to
  keep this diff scoped to the file_analysis conversion pipeline.

## Test plan (regression tests, TDD)

Extend `tests/codemie_tools/file_analysis/workers/test_markdown_workers.py` with a new test
class covering docx/pptx routing:

1. **Failing test first**: patch `markitdown._markitdown.magika.Magika` (class-level — the
   worker builds its own internal `MarkItDown()` instance) so `identify_stream` returns a
   pptx-like guess; run the real `test.docx` fixture through the real (unmocked)
   `DocxConverter`/`PptxConverter` pipeline via `convert_file_to_markdown`. Pre-fix: raises
   `FileConversionException` mentioning `PptxConverter`. Post-fix: converts successfully.
2. A second case asserts the pptx fixture (`tests/codemie_tools/file_analysis/pptx/test.pptx`)
   still converts successfully after the fix (no PPTX regression), independent of the magika
   mock.
3. Optionally, a plain case with no magika mocking confirms a normal docx conversion still
   works end-to-end (guards against any regression in the happy path).
4. A case with an extensionless filename (`ext == ""`) confirms `stream_info.extension` is
   passed as `None`, not `""` — guards directly against the empty-string-extension pitfall
   described above.

This is a real fixture + real converter pipeline test (per
`.ai-run/guides/testing/testing-patterns.md`'s "Seam Tests for Policy Helpers" guidance — the
test must observe `md.convert()`'s actual behavior, not `accepts()` in isolation), not a
crafted-fixture attempt — a crafted OOXML fixture that fools the installed magika model could
not be produced (tried and failed per Jira Comment 2's theory).

## Acceptance criteria mapping

| AC | Satisfied by |
|---|---|
| Valid `.docx` never routed to `PptxConverter` | `stream_info` hint — extension guess wins before any magika guess |
| DOCX processing succeeds for previously-working documents | Same fix; regression test 3 |
| Converter selection correctly distinguishes DOCX vs PPTX | Disjoint extension/mimetype sets on `stream_info`, validated in test 1 |
| Error names correct document type on failure | Free side effect of correct routing (see Scope decision above) |
| Regression tests cover both DOCX and PPTX routing | Tests 1 and 2 |
| Existing PPTX conversion unaffected | Test 2 |
