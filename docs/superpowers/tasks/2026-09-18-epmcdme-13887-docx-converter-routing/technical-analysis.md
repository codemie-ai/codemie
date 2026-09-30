# Technical Research

**Task**: file_analysis markitdown docx pptx converter stream_info
**Generated**: 2026-09-18
**Research path**: filesystem (Explore agents — codegraph MCP tool not available in this environment)

---

## 1. Original Context

Jira EPMCDME-13887 — Bug: a valid .docx uploaded to the CodeMie Assistant fails with
FileConversionException: "PptxConverter threw ValueError with message: file ... is not
a PowerPoint file, content type is
'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'". The
same workflow worked before for the user; nothing changed on their side, so the failure is
file-dependent, not a code regression.

Acceptance criteria (from the Jira ticket):
- Valid .docx files are detected as Word documents and are never routed to PptxConverter.
- DOCX processing succeeds for documents that previously worked.
- Converter selection correctly distinguishes DOCX vs PPTX OOXML content types.
- If conversion still fails, the user-facing error names the correct document type.
- Regression tests must cover both DOCX and PPTX routing.
- Existing PPTX conversion behavior must remain unaffected.

Jira Comment 2 root-cause theory: leftover MIP/AIP classification-label artifacts
(docMetadata/LabelInfo.xml at the zip root + an extra relationship of type
http://schemas.microsoft.com/office/2020/02/relationships/classificationlabels in
_rels/.rels, left behind by Word after a sensitivity label was applied then removed)
confuse MarkItDown's magika-based content-type guesser into predicting a
presentation-like type for genuinely-DOCX byte content.

Known findings already verified this session (please confirm/build on rather than
re-derive from scratch):
- Bug entry point: src/codemie_tools/file_analysis/workers/markdown_workers.py, function
  convert_file_to_markdown. It computes `ext = os.path.splitext(file_name or "")[1].lower()`
  but only uses `ext` for an `.xlsb` special case; the main call `result = md.convert(binary_content)`
  (line 58) passes a bare io.BytesIO with NO `stream_info`, so file_name/ext are never used to
  disambiguate DOCX vs PPTX. MarkItDown then guesses the type purely from bytes via magika.
- markitdown 0.1.2 internals (installed in the local poetry venv): `MarkItDown.convert` ->
  `convert_stream` -> `_get_stream_info_guesses` (builds an extension/mimetype-based
  "enhanced_guess" plus a magika content-based guess; if incompatible, both are returned,
  enhanced_guess first) -> `_convert` (tries each stream_info guess against each registered
  converter's `accepts()`, sorted by priority, first accept+successful-convert wins).
  DocxConverter/PptxConverter accepts() check `stream_info.extension` against disjoint
  ACCEPTED_FILE_EXTENSIONS (.docx vs .pptx) and disjoint ACCEPTED_MIME_TYPE_PREFIXES first,
  before any magika-derived guess is even reached (when the extension-based guess is present
  and tried first). So passing `stream_info=StreamInfo(filename=file_name, extension=ext)`
  into `md.convert()` fully and unambiguously fixes the routing regardless of magika's guess.
- Other MarkItDown call sites with the same missing-stream_info gap (survey done by a
  background research agent this session):
  - src/codemie/datasource/loader/azure_devops_work_item_loader.py:557-559 and
    src/codemie/datasource/loader/azure_devops_wiki_loader.py:727-729 (`_extract_msg_text`):
    `md.convert(BytesIO(content_bytes))`, no stream_info — lower risk in practice (caller
    already routes by content_type/extension before reaching this method) but same gap.
  - src/codemie_tools/file_analysis/email/tools.py:761: `md.convert(temp_path)` — SAFE,
    because it's a real file path with correct suffix; convert_local() auto-derives
    StreamInfo from the path.
  - src/codemie/service/conversation/message_exporter.py:89: MarkItDown() instantiated but
    .convert() never called — dead code, not a real call site.
- Callers of convert_file_to_markdown: src/codemie_tools/file_analysis/file_analysis_tool.py:161-165
  and src/codemie/service/file_service/markdown_cache_service.py:73 — both pass `file_obj.name`,
  which is confirmed to always be the real client-supplied original filename end-to-end (never a
  generated UUID), from FastAPI UploadFile.filename through to FileObject.name.
- Where FileConversionException becomes user-facing: markdown_cache_service.py:70-77
  (`get_or_convert`) catches broad Exception and sets
  `markdown = f"[Conversion failed for {file_obj.name}: {type(e).__name__}]"` (cached and
  returned as file content) — currently only echoes the exception class name, not the
  document type; may need attention for the "names the correct document type" AC.
  Separately, file_analysis_tool.py:161-168 silently swallows exceptions and falls back to
  _fallback_decode_text_file, never surfacing FileConversionException at all.
- Existing tests: tests/codemie_tools/file_analysis/workers/test_markdown_workers.py (xlsb
  short-circuit + Utf8SafePlainTextConverter tests; NO docx/pptx routing tests yet) and
  tests/codemie/service/file_service/test_markdown_cache_service.py:260-340 (tests the
  FileConversionException/UnsupportedFormatException fallback-string path).
- Existing fixtures: tests/codemie_tools/file_analysis/docx/test.docx,
  tests/codemie_tools/file_analysis/pptx/test.pptx,
  tests/codemie_tools/file_analysis/samples/sample.pptx (+ golden .json/.md files).
- Attempted to craft a real fixture that fools the installed magika model (injecting
  docMetadata/LabelInfo.xml + extra _rels/.rels relationship per Comment 2's theory) — did
  NOT reproduce misclassification; magika still correctly identified it as docx. So a
  mock-of-magika-boundary test (patching `markitdown._markitdown.magika.Magika` so the real
  routing engine sees a controlled, wrong content guess) is likely the practical regression
  test strategy, rather than depending on fooling the real ML model.

Please verify these findings against current source, note anything outdated, and add any
additional risk indicators or codebase conventions relevant to fixing this narrowly within
the file_analysis conversion pipeline (per user instruction: keep the diff minimal and
scoped there — the azure_devops loader sites are informational only, not required scope).

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie_tools/file_analysis/workers/markdown_workers.py` (63 lines, current content confirmed unchanged from ticket description) — `convert_file_to_markdown(file_bytes, file_name, llm_client=None, llm_model=None)`:
  - Line 43: `ext = os.path.splitext(file_name or "")[1].lower()` — extension already derived from filename, used only for the `.xlsb` short-circuit (lines 44-45, delegates to `process_xlsx_to_markdown`).
  - Lines 47-51: constructs `MarkItDown(enable_builtins=True, llm_client=llm_client)`.
  - Line 55: `md.register_converter(Utf8SafePlainTextConverter(), priority=9)`.
  - Line 56: `binary_content = io.BytesIO(file_bytes)`.
  - **Line 58: `result = md.convert(binary_content)` — the root-cause call site. No `stream_info=` kwarg passed, so `ext`/`file_name` never reach MarkItDown's routing logic.**
  - Lines 60-62: `except Exception as e: logger.error(f"MarkItDown conversion failed for {file_name}: {e}"); raise` — re-raises (does not swallow), f-string logging at `error` severity.
- Production call sites of `convert_file_to_markdown` (both pass only `(file_bytes, file_name)` — no signature change needed for the fix):
  - `src/codemie_tools/file_analysis/file_analysis_tool.py:155-163` (`_process_single_file`), via `maybe_pool_submit(convert_file_to_markdown, file_object.bytes_content(), file_object.name)` — runs through a process pool (per module docstring "MarkItDown worker for multiprocessing").
  - `src/codemie/service/file_service/markdown_cache_service.py:70-83` (`get_or_convert`), called inline (not pooled): `markdown = convert_file_to_markdown(raw_bytes, file_obj.name)`.
- `src/codemie_tools/file_analysis/file_analysis_tool.py` exception handling (lines 154-168, `_process_single_file`): `except FileNotFoundError` returns `"File not found: ..."`; broad `except Exception as e` calls `_fallback_decode_text_file(file_object, original_exception=e)` (lines 134-152) — for binary files (docx/pptx, `is_text_based()` is False) this returns `"File type not supported for direct decoding"` + optional `". Original error: {str(original_exception)}"`, i.e. it can leak the misleading "PptxConverter"-mentioning markitdown exception text verbatim into a user-facing string without naming the true document type.
- `src/codemie/service/file_service/markdown_cache_service.py` (`get_or_convert`, lines 53-96): already has a try/except guard (added by a prior related fix, see Section 3) — catches broad `Exception`, builds `f"[Conversion failed for {file_obj.name}: {type(e).__name__}]"` (line 75), logs via `logger.warning("MarkdownCache: conversion failed for %s/%s (%s, %d bytes): %s", ...)` (lines 76-83), then unconditionally caches this placeholder string via `repo.write_file(...)` (lines 85-94). **Second-order effect confirmed**: a one-time magika misclassification becomes permanently sticky — subsequent reads hit the cache "hit" path (lines 61-64) and keep returning the placeholder until `invalidate()` (lines 98-108) is called.

### Architecture and Layers Affected

- **Agent-Tool / File-processing worker layer**: `markdown_workers.py` (the actual fix target) — a pure function used both inline and via a multiprocessing pool, no framework coupling.
- **Service layer**: `MarkdownCacheService.get_or_convert` — orchestrates the worker call, cache read/write, and exception-to-string fallback; also a required touch point per AC #4 (user-facing error must name correct doc type).
- **Agent-Tool consumer**: `FileAnalysisTool._process_single_file` / `_fallback_decode_text_file` — the other exception-swallowing boundary; may need a touch to stop leaking misleading converter-name text, depending on how narrowly the fix is scoped.
- **External integration (third-party library)**: `markitdown` 0.1.2 (pinned `^0.1.2` in `pyproject.toml:120`, resolved `0.1.2` in `poetry.lock:5897-5898`) and its internal `magika` content-sniffing dependency — not modified, only correctly parameterized via `StreamInfo`.
- No DB/persistence layer changes needed; no API/router layer changes needed (this is purely an internal worker/service fix).

### Integration Points

- `markitdown.StreamInfo` (frozen dataclass: `mimetype`, `extension`, `charset`, `filename`, `local_path`, `url`) — already imported and used elsewhere in the same package (`utf8_safe_plain_text_converter.py:32,39-44,47`), confirming it's the correct, idiomatic type to construct for the fix; no new import path needs to be discovered.
- `markitdown.converters._docx_converter.DocxConverter.accepts()` — matches on `ACCEPTED_FILE_EXTENSIONS = [".docx"]` or `ACCEPTED_MIME_TYPE_PREFIXES = ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"]`, purely from `stream_info`, no byte-sniffing of its own.
- `markitdown.converters._pptx_converter.PptxConverter.accepts()` — mirrors the above with `.pptx` / `presentationml` — disjoint from Docx's accepted extensions/mimetypes, confirming a correct `stream_info` hint fully disambiguates.
- `markitdown._markitdown._get_stream_info_guesses()` (site-packages, ~lines 661-758): if `base_guess` (built from the caller's `stream_info`) has no extension/mimetype, magika's raw-byte guess is the *sole* signal (today's broken behavior). If the caller supplies an extension/mimetype that conflicts with magika's guess, **both** guesses are added to the candidate list, extension-guess first — so `_convert()`'s first-accept-wins loop (site-packages `_markitdown.py:529-619`) will hit `DocxConverter.accepts()` before ever reaching the magika-derived pptx guess. This is the exact, confirmed fix mechanism.
- `codemie_tools.base.file_object.MimeType` (`src/codemie_tools/base/file_object.py:26-44`) — already defines `DOCX_TYPE`, `PPTX_TYPE`, `XLSX_TYPE`, etc., plus `normalise_mime()` helper. Already used by `markitdown_xlsx_converter.py` for the same style of extension/mimetype-based `accepts()` logic. This is the idiomatic reuse point if the fix needs a mimetype constant rather than just an extension string — no new mapping table should be invented (a duplicate, narrower `_EXT_TO_MIME` dict already exists in `email/tools.py:68-82` and should NOT be copied again).
- MarkItDown itself internally calls `mimetypes.guess_type()` to backfill mimetype from extension — so supplying just `extension=ext` (already computed at line 43) into `StreamInfo` is sufficient; the fix does not need to also resolve a mimetype string manually.

### Patterns and Conventions

- Extension-driven short-circuit pattern already exists in the same function (`.xlsb` branch, lines 44-45) — the fix is a natural extension of an already-established idiom in this exact file, not a new pattern.
- `xlsx_workers.process_xlsx_to_markdown(file_bytes, sheet_names, visible_only, file_ext='.xlsx')` takes an explicit `file_ext` string parameter (not a `StreamInfo`-like object) — a sibling worker convention, but not directly reusable for the MarkItDown call itself since `StreamInfo` is the markitdown-native mechanism.
- `src/codemie_tools/file_analysis/xlsx/markitdown_xlsx_converter.py` — a custom `XlsxConverter(markitdown.converters.XlsxConverter)` with an `accepts()` override reading `stream_info.extension`/`normalise_mime(stream_info.mimetype)` — a good template for how `stream_info` fields are meant to be consumed, though this specific class appears to be dead code (defined but never registered/imported elsewhere in `src/`) — flag for awareness, out of scope to fix.
- Module docstring + Google-style `Args:`/`Returns:` docstrings, f-string logging, broad `except Exception as e: logger.error(...); raise` (re-raise, don't swallow) — the established style in `markdown_workers.py` to preserve.
- `markdown_cache_service.py` style differs: short one-to-three-line docstrings, `%s`-placeholder lazy logging (not f-strings), module-level `_UPPER_SNAKE` constants, and every I/O call wrapped in its own narrow try/except with a safe fallback — do not mix styles across files when touching both.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/development/error-handling.md` — read in full. Three relevant sections:
  - **Typed Exceptions**: prefer shared project exceptions (`ValidationException`, `ExtendedHTTPException`) over bare `raise Exception(...)` for user-facing errors. No file-conversion-specific typed exception exists or is prescribed.
  - **Sanitized Failures**: log internal detail, return a safe client-facing message; never swallow exceptions without logging context.
  - **Accurate Error Details** (directly load-bearing for AC #4): "An error message must describe the failure that actually happened. If one code path can fail for several distinct reasons, branch and produce a distinct message per reason." This is the concrete guide-level justification for why the fix must name the correct document type rather than emit a generic string.
- `.ai-run/guides/development/logging-patterns.md` — read in full. **Severity** section: match log severity to operational impact; expected/recoverable validation-style failures should use `warning`, not `error`. Note: `markdown_workers.py` currently uses `logger.error` for conversion failures (line 61) while `markdown_cache_service.py` already uses `logger.warning` for the same class of recoverable per-file failure — an existing inconsistency, not necessarily in scope to fix but worth noting if the error-handling code is touched.
- `.ai-run/guides/testing/testing-patterns.md` — read in full. **Seam Tests for Policy Helpers** section is directly relevant: "when a helper encapsulates a decision (e.g. which converter should handle this file), unit-testing the helper alone is insufficient — each callsite needs a test that observes what actually reaches the outer boundary... otherwise a duplicate guard at the callsite stays silent when the helper's logic is deleted." This means a regression test asserting `md.convert(...)` is actually invoked with the correct `stream_info` (not just that `DocxConverter.accepts()` works in isolation) is the required test shape.
- **No domain-specific guide exists** for markitdown/docx/pptx/file_analysis/magika — `grep -rli` across all of `.ai-run/guides/` for these terms returned zero matches. This is generic project-wide guidance only; domain conventions had to be derived from source/tests directly.
- **Prior related work found in `docs/`**: `docs/superpowers/tasks/2026-07-31-fix-markitdown-exception/` — a previously completed SDLC-Factory task for **EPMCDME-13779** ("UnsupportedFormatException on chat file attachments"), touching the exact same two files (`markdown_cache_service.py`, `markdown_workers.py`). Its `technical-analysis.md` already documented that `markitdown.UnsupportedFormatException`/`FileConversionException` are third-party (not project) classes, and its `plan.md` shows the try/except guard now present in `get_or_convert()` (lines 70-96) was added by that prior fix. This confirms the current `[Conversion failed for {filename}: {exception_type}]` fallback format is an intentional, already-shipped convention from a related but distinct prior bug — not something introduced by this investigation.

### Architectural Decisions

- The prior EPMCDME-13779 fix deliberately placed the try/except guard at the **caller** (`get_or_convert`), not inside `convert_file_to_markdown()`, because the worker function is designed to log-and-re-raise rather than swallow (confirmed still true in current source, lines 60-62). Any fix for EPMCDME-13887 should preserve this separation of concerns: `markdown_workers.py` fixes *routing correctness*; `markdown_cache_service.py`/`file_analysis_tool.py` own *user-facing error presentation*.
- No architectural decision record exists specifically permitting or prohibiting adding a `document_type`/context field to exceptions raised from this pipeline — this would be a new pattern if introduced.

### Derived Conventions

- Extension-based routing hints are already a trusted, established idiom in this exact function (the `.xlsb` branch) — extending that trust to a `StreamInfo(filename=file_name, extension=ext)` hint for the general path is consistent with existing local convention, not a novel technique.
- No TODO/HACK/NOTE/DECISION markers exist anywhere under `src/codemie_tools/file_analysis/` (confirmed via repo-wide grep) — this bug/gap was not previously flagged as known technical debt.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie_tools/file_analysis/workers/test_markdown_workers.py` (174 lines) — `TestXlsbPreconversion`, `TestUtf8SafePlainTextConverter`, `TestConvertFileToMarkdownCyrillic`. **No docx/pptx routing test exists.** `TestConvertFileToMarkdownCyrillic` exercises real (unmocked) MarkItDown/magika end-to-end with crafted "realistic" bytes — the same general approach already tried (and failed) against the real magika model for the label-artifact repro described in the ticket.
- `tests/codemie/service/file_service/test_markdown_cache_service.py` (391 lines) — six tests around lines 232-371 cover the `UnsupportedFormatException`/`FileConversionException` fallback path (`[Conversion failed for {filename}: {exception_type}]` format), partial-batch-failure continuation, and structured `logger.warning` call args. **None of these use a `.docx`/`.pptx` filename or assert that the message names the correct document type on a misrouting failure** — they only assert the raw exception class name is embedded.
- `tests/codemie_tools/file_analysis/test_file_analysis.py` — `test_fallback_mechanism` (parametrized over sample files) explicitly **excludes** `.docx`/`.pptx`/`.xlsx`/`.xls`/`.csv`/`.eml`/`.msg` from its filter, and `tests/codemie_tools/file_analysis/samples/` has **no `.docx` fixture at all** (only `docx/test.docx` elsewhere) — so this path structurally cannot and does not cover docx/pptx today.

### Testing Framework and Patterns

- pytest, `unittest.mock.patch`/`MagicMock` (no `pytest-mock`/`mocker` in `test_markdown_workers.py` specifically, though `mocker` is used in some other test files elsewhere in the repo).
- Established, documented mocking convention (explicit code comment in the test file): **patch the name where it's *used*, not where it's defined** — `patch("codemie_tools.file_analysis.workers.markdown_workers.MarkItDown")`, not `patch("markitdown.MarkItDown")` — otherwise the real class still runs.
- `mock_md_cls.return_value.convert.return_value = mock_result` (a `MagicMock` with `.text_content` set) is the established pattern for stubbing a successful conversion; `mock_md.assert_not_called()` / `mock_convert.assert_called_once_with(...)` for asserting on the seam.
- **No precedent anywhere in the repo mocks `magika.Magika` directly.** Confirmed correct patch target for a magika-boundary test would be `markitdown._markitdown.magika.Magika` (installed package: `import magika` at line ~15, `self._magika = magika.Magika()` inside `MarkItDown.__init__` at line ~113, `self._magika.identify_stream(file_stream)` called at line ~689 inside `_get_stream_info_guesses`). This confirms the ticket's own conclusion — the practical regression test path is patching this boundary to force a misleading guess, since real-fixture crafting did not fool the installed model.
- Fixtures directly usable: `tests/codemie_tools/file_analysis/docx/test.docx`, `tests/codemie_tools/file_analysis/pptx/test.pptx` (with golden `.json`/`.md` companions), `tests/codemie_tools/file_analysis/samples/sample.pptx`. No conftest.py exists in `workers/`, `docx/`, `pptx/`, or `samples/` — each test file loads fixture bytes manually.

### Coverage Gaps

- No test asserts `md.convert()` is called with a `stream_info` hint reflecting the file's extension (the seam the fix will introduce).
- No test forces a magika misclassification (via mocking) to prove the fix prevents misrouting despite a bad content-based guess — this is the core regression test the AC requires and has zero existing precedent to copy.
- No test asserts the user-facing error string (from either `markdown_cache_service.get_or_convert` or `file_analysis_tool._fallback_decode_text_file`) names the correct document type when conversion still fails — current fallback format only ever surfaces the exception *class* name, never the file type.
- No `.docx` sample exists in `samples/`, so any new `FileAnalysisTool`-level coverage (as opposed to worker-level) needs a new fixture or a dedicated non-parametrized test using `docx/test.docx`.

---

## 5. Configuration and Environment

### Environment Variables

None specific to this pipeline were found. No env vars gate markitdown/magika behavior, converter selection, or file-type routing in this codebase.

### Configuration Files

- `pyproject.toml:120` — `markitdown = {extras = ["all"], version = "^0.1.2"}` (pinned). `poetry.lock:5897-5898` resolves to exactly `0.1.2`, matching what's installed in the local poetry venv used for verification.
- No feature-flag or config-driven toggle exists for converter selection — routing behavior is entirely determined by code, not configuration.

### Feature Flags and Deployment Concerns

None identified. This is a pure library-usage/routing-logic fix with no deployment, secrets, or infrastructure surface.

---

## 6. Risk Indicators

- **Zero regression test coverage today** for docx/pptx routing correctness at the `convert_file_to_markdown` seam — the fix must add net-new tests with no directly copyable precedent for the magika-mocking strategy (first of its kind in this repo).
- **Sticky-cache second-order effect**: once a docx is misclassified and fails, `MarkdownCacheService.get_or_convert` permanently caches the `[Conversion failed...]` placeholder string until explicit `invalidate()` — meaning even after the routing fix ships, any file that already hit the cache with a bad result will keep returning the stale placeholder unless cache invalidation is considered as part of the fix or rollout.
- **Third-party exception classes carry no document-type field**: `markitdown.FileConversionException`/`UnsupportedFormatException` are defined entirely inside the installed `markitdown` package (not project code) and only carry `attempts` (which converter was tried) — never the caller's intended/true file type. Satisfying AC #4 ("names the correct document type") requires codemie's own code to construct the message using `file_name`/`ext` already in scope at the catch site, not by inspecting the exception object alone.
- **Two independent exception-swallowing boundaries** (`markdown_cache_service.get_or_convert` and `file_analysis_tool._fallback_decode_text_file`) both need to be considered for AC #4, since a user could hit either code path depending on how the file was uploaded/requested — fixing only one leaves the other still capable of leaking a "PptxConverter"-mentioning message for a genuinely-docx file.
- **Logging severity inconsistency**: `markdown_workers.py` logs conversion failures at `logger.error`, while `markdown_cache_service.py` (and the project's own logging-patterns guide) treat this class of recoverable per-file failure as `logger.warning`-worthy — not blocking, but a small inconsistency a reviewer may flag if the error path is touched.
- **No domain-specific `.ai-run/guides/` entry exists** for this pipeline (markitdown/docx/pptx/magika) — all conventions had to be derived from source and a prior related task under `docs/superpowers/tasks/2026-07-31-fix-markitdown-exception/`, increasing the chance of subtle convention drift if the fix doesn't cross-reference that prior work.
- **Real-fixture repro of Comment 2's theory failed**: crafting a docx with injected `docMetadata/LabelInfo.xml` + extra `_rels/.rels` relationship did not fool the installed magika model in this environment. This means the regression test cannot rely on a "real" misclassifying fixture and must mock the magika boundary — introducing a maintenance risk if `markitdown`/`magika` internals change their import path (`markitdown._markitdown.magika.Magika`) in a future upgrade.
- **Related but out-of-scope call sites** (`azure_devops_work_item_loader.py:557-559`, `azure_devops_wiki_loader.py:727-729`) share the identical missing-`stream_info` gap — explicitly excluded from this fix's scope per user instruction, but worth a follow-up ticket note since the same magika-misclassification risk theoretically applies there too (mitigated in practice by upstream content-type routing).
- **codegraph MCP tool was not available in this environment** — research relied entirely on the filesystem fallback (four parallel Explore agents). No indexing-gap risk beyond the normal limits of grep/glob-based search, but flagging per protocol.

---

## 7. Summary for Complexity Assessment

This fix is narrowly scoped to the Agent-Tool/file-processing worker layer and the Service layer immediately around it: the core change is a one-line-ish addition inside `convert_file_to_markdown` (`src/codemie_tools/file_analysis/workers/markdown_workers.py:58`) to pass `stream_info=StreamInfo(filename=file_name, extension=ext)` into `md.convert()`, using an extension value already computed two lines above and a `StreamInfo` type already imported elsewhere in the same package. No signature changes are needed at either production call site (`file_analysis_tool.py` and `markdown_cache_service.py`), since both already pass `file_name`/`file_obj.name`. Satisfying AC #4 (user-facing error names the correct document type) additionally touches two independent exception-handling boundaries — `markdown_cache_service.get_or_convert`'s fallback-string builder and `file_analysis_tool._fallback_decode_text_file`'s swallow-and-decode path — neither of which currently has access to a document-type field on the caught exception, so the message must be assembled from `file_name`/extension already in scope rather than read off the exception object. Estimated file-change surface is small: 1 core worker file, likely 1-2 exception-handling call sites, plus new test files — no DB, API, or config changes.

Technical novelty is low for the core fix (it follows an idiom already present in the same file for `.xlsb`, and mirrors `markitdown`'s own documented, intended usage pattern for `stream_info`), but there is real novelty in the **test strategy**: no precedent exists anywhere in this codebase for mocking the `magika.Magika` boundary, and an earlier attempt to craft a real fixture that reproduces the misclassification failed against the installed model. This means the regression test approach itself must be newly established, which is a case-by-case complexity driver above and beyond the trivial production code change.

Test coverage posture for this exact seam is currently **zero** — no docx/pptx routing test exists at the worker level, and the `FileAnalysisTool`-level sample-driven tests structurally exclude docx/pptx entirely (no fixture, explicit filter exclusion). The AC explicitly requires regression tests for both DOCX and PPTX routing, so this is not optional coverage to add but a hard requirement stacked on top of a novel mocking strategy. Key risk factors for complexity scoring: (1) the sticky-cache second-order effect, which may warrant its own consideration even though it's not explicitly named in the AC; (2) the need to touch two separate, differently-styled exception-swallowing sites to fully satisfy AC #4 without over-scoping into the excluded azure_devops call sites; and (3) the first-of-its-kind magika-mocking test pattern, which carries some risk of brittleness against future `markitdown` internal-path changes.
