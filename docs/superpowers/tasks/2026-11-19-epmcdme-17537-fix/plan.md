# File parsing pool deadlock (EPMCDME-17537) Implementation Plan

**Goal:** Stop pool workers from nesting into the pool, and bound every wait on it, per `spec.md` (AC1-AC13).

**Rules:** Commit per task using the repository's existing convention, ticket EPMCDME-17537 (the executor commits; the planner does not). No code comments, no docs outside the task dir. Tests are mock-based in the style of `tests/codemie/datasource/loader/test_file_processor_pool.py` and `test_file_loader.py`, except Task 2. Run only the narrowest scope with `poetry run pytest <file>` (guide `.ai-run/guides/testing/testing-patterns.md`; `pytest.ini` has `pythonpath = src`, `--import-mode=importlib`, `-n 3`). Whole-suite gates and browser checks belong to later stages.

**Checked while planning:** `datasources_config.py` imports only `os`, `yaml`, `pydantic`, `codemie.configs`; `codemie/datasource/__init__.py` and `exceptions.py` import nothing, so no cycle is expected (Task 1 confirms by import). ERROR-status effect: an update that ends in ERROR goes through the existing `except Exception` branch (`file_datasource_update_processor.py:281-289`), which sets `index.error` and does not delete the old ES index. While `error` is set, `search_kb.py:163` raises `ToolException` with the failure notice and the code tools do the same (`tools.py:72,94,148,199`), so assistants cannot query the old content until the next update clears the flag (`:113`). This is how every other failed update behaves today, and it is the user's decision (spec AR-1). It goes into the digest, not into a code change.

---

### Task 1: Exception, FILE_CONFIG import, worker flag and initializer (AC1, AC3)

**Files:** `src/codemie/datasource/exceptions.py`, `src/codemie/datasource/loader/file_processor_pool.py`, `tests/codemie/datasource/loader/test_file_processor_pool.py`
**Test-first: yes — with the flag set, `maybe_pool_submit` calls `fn` directly and never touches `get_executor`; the initializer sets the flag before `configure_pyroscope` runs and still sets it when `configure_pyroscope` raises; with the flag unset the pool path is unchanged.**

- [ ] Add `FileParsingTimeoutError(timeout_seconds)` after `UnreadableWorkbookError` (`exceptions.py:92-115`): subclass of `Exception` only, fixed message `File parsing timed out after <N>s`, `__reduce__` returning `(self.__class__, (self.timeout_seconds,))`.
- [ ] In `file_processor_pool.py` import `FILE_CONFIG` from `codemie.datasource.datasources_config`. First run `poetry run python -c "import codemie.datasource.loader.file_processor_pool, codemie_tools.file_analysis.xlsx.processor"`. If an import cycle shows up, import `FILE_CONFIG` lazily inside the wait helper (Task 3) and note it in the commit.
- [ ] Add module flag `_in_pool_worker = False` and module-level `_pool_worker_initializer(pyroscope_settings)` that sets the flag first, then calls `configure_pyroscope(pyroscope_settings)`. Pass it as `initializer=` in `initialize()` (`:67-72`), keeping the existing `initargs`. `maybe_pool_submit` (`:139`) runs inline when the flag is set. Nothing else reads `parent_process()`.
- [ ] Extend the autouse fixture (`test_file_processor_pool.py:24-35`) to reset the flag.

### Task 2: Real-process test for AC2 (risk: spawned child must import the helper)

**Files:** `tests/codemie/datasource/loader/pool_worker_helpers.py` (new), `tests/codemie/datasource/loader/test_file_processor_pool_real_process.py` (new)
**Test-first: yes — a spawn pool with `max_workers=1`, `max_tasks_per_child=2` and the real initializer completes 5 tasks that each make a nested `maybe_pool_submit` call within the bound; the control with a plain initializer does not.**

- [ ] Helper module (stdlib and `file_processor_pool` only, never `tests/conftest.py`): `square(x)` and `nested_square(x)`, where `nested_square` returns `maybe_pool_submit(square, x)`. The test refers to them as `tests.codemie.datasource.loader.pool_worker_helpers.<name>`.
- [ ] Test builds `ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"), max_tasks_per_child=2, initializer=_pool_worker_initializer, initargs=({},))`. It does not use `FileProcessPoolManager.initialize`, which forces the global start method to `fork`. `monkeypatch.setenv("ENABLE_FILE_MULTIPROCESSING", "true")` before the first submit, so workers inherit it. A bounded wait of about 30 s per result.
- [ ] Negative control: same scenario with a no-op initializer must raise `TimeoutError` within the same bound.
- [ ] Teardown (a fixture or `finally`) must not read `executor._processes` at that point: a worker that reports it is exiting at its task limit is popped from that dict by the manager thread before the thread joins it (`concurrent/futures/process.py:362-363`; `self.processes` is the same object as `executor._processes`, `:317`). In the negative control the dict then holds only the idle replacement worker, and the stuck worker and its grandchild would survive. So keep a `set` of worker PIDs and, after every submit, add the keys of `executor._processes` to it. At teardown, for every recorded PID kill its descendants deepest first with `psutil` (`Process(pid).children(recursive=True)`, reversed), then the PID itself, ignoring `psutil.NoSuchProcess`. Only then call `executor.shutdown(wait=False, cancel_futures=True)`. Do not kill "every new child of the test process": the spawn context also starts a shared `resource_tracker` process.
- [ ] Run `poetry run pytest tests/codemie/datasource/loader/test_file_processor_pool_real_process.py`. This confirms the `sys.path` assumption (spawn copies the parent's `sys.path`; other tests already use `from tests.codemie...`). If the child cannot import the helper, or the negative control does not hang, stop and report to the caller instead of weakening the test.

### Task 3: Bounded wait in `maybe_pool_submit` (AC4, AC11, AC12)

**Files:** `src/codemie/datasource/loader/file_processor_pool.py` (`:138-148`), `tests/codemie/datasource/loader/test_file_processor_pool.py`
**Test-first: yes — a future whose `result(timeout)` raises `TimeoutError` while `done()` is False raises `FileParsingTimeoutError`, calls `cancel()` and never calls `_reinitialize_pool`.**

- [ ] Add a private `_wait_for(future)`: read `FILE_CONFIG.processing_timeout` at call time; any value `<= 0` passes `None`. On `TimeoutError`: if `future.done()` return `future.result()` (late result, or the worker's own exception re-raised unchanged); otherwise `future.cancel()` and raise `FileParsingTimeoutError(timeout) from None`.
- [ ] Use it for both the first wait and the retry after `BrokenProcessPool` (each attempt gets its own wait). Keep the `BrokenProcessPool` reinit branch as is.
- [ ] Tests: `<= 0` passes `None`; late finish returns the result with no cancel; late finish with a worker exception re-raises it unchanged; a submitted function raising `TimeoutError` (future done) reaches the caller unchanged and is not a pool timeout; the retry's wait is bounded; existing `test_broken_pool_reinits_and_retries` stays green.

### Task 4: File loader accounting and all-timed-out error (AC5, AC6, create and update flows)

**Files:** `src/codemie/datasource/exceptions.py`, `src/codemie/datasource/loader/file_loader.py` (`:112-137`, `:165-166`), `tests/codemie/datasource/loader/test_file_loader.py`, `tests/codemie/datasource/file/test_file_datasource_update_processor.py`
**Test-first: yes — with three files where one raises `FileParsingTimeoutError`, the other two are yielded and `get_load_stats()` reports `failed_documents == 1`; with every file timing out, iterating raises `AllFilesTimedOutError` whose message is `File parsing timed out for 2 file(s); nothing was indexed`.**

- [ ] Add `AllFilesTimedOutError(timed_out_count)` with a `__reduce__` next to its neighbours in `exceptions.py`.
- [ ] In `_load_docs_parallel`, catch `FileParsingTimeoutError` before the generic handler: log with the file name, increment a timed-out count (it is also the failed count). Track whether any file produced documents. After the loop, raise `AllFilesTimedOutError` when timed-out > 0 and nothing produced documents. The raise sits outside the per-file `try`. `get_load_stats()` adds `FAILED_DOCUMENTS_KEY` on every run; no new key. Update existing stats assertions.
- [ ] Keep `test_error_per_file_is_logged_and_skipped` and the `UnreadableWorkbookError` propagation unchanged (AC12). The serial path is untouched.
- [ ] Update-flow test (existing index, every new file times out): the update processor ends through `set_error` with the timeout message, the ES index is not deleted, the slot is released, and `_validate_indexing_result` is not edited. Record the ERROR-status effect found above in the commit body.

### Task 5: SharePoint through the shared helper (AC7)

**Files:** `src/codemie/datasource/loader/sharepoint_loader.py` (`:1076-1087`, import at `:44`), `tests/codemie/datasource/loader/test_sharepoint_loader.py`
**Test-first: yes — `_extract_documents_from_bytes_multiprocess` calls `maybe_pool_submit(extract_documents_from_bytes, ...)` and not the executor; when it raises `FileParsingTimeoutError`, `_download_and_extract_file` returns `[]` and the failed-files count increases.**

- [ ] Replace the direct `get_executor().submit(...).result()` with `maybe_pool_submit(extract_documents_from_bytes, file_bytes, file_name, self.request_uuid, ",", True, datasource_id=self.datasource_id)`. Drop the unused `file_process_pool` import. The existing catch-all in `_download_and_extract_file` is unchanged.

### Task 6: Tools never relabel the timeout (AC8)

**Files:** `src/codemie_tools/file_analysis/file_analysis_tool.py` (`:165-168`), `docx/reader.py` (`:154-159`, `:209`), `docx/processor.py` (`:85-90`, `:122-127`), `docx/tools.py` (`:234-247`); tests in `tests/codemie_tools/file_analysis/test_file_analysis.py` and `docx/{test_reader,test_processor,test_docx_tool}.py`
**Test-first: yes — with `maybe_pool_submit` patched to raise `FileParsingTimeoutError`, `FileAnalysisTool._process_single_file` and the docx reader (`read_with_markitdown` and `read_from_bytes`), processor (both methods) and tool each raise `FileParsingTimeoutError` itself, not fallback text, `CorruptedDocumentError`, `DocumentReadError` or `DocxProcessingError`.**

- [ ] At each listed handler add `except FileParsingTimeoutError: raise` before the generic handler (and before `except IOError` at `reader.py:154`, which comes first). No other handlers change.

### Task 7: Spreadsheet and PDF tool boundaries (AC8)

**Files:** `tests/codemie_tools/file_analysis/test_excel_tool.py`, `tests/codemie_tools/file_analysis/pdf/test_pdf_processor_error_handling.py`
**Test-first: no — characterization tests; the xlsx handlers already embed `str(e)`, which is the fixed message, and PDF passes through. If a case fails, fix that handler in this task.**

- [ ] With the pool call patched to raise `FileParsingTimeoutError`: default read (`xlsx/tools.py:313`), `get_sheet_names` (`:166`) and `get_stats` (`:281`) return text containing "file parsing timed out" case-insensitively; `filter_values` and `sheet_index` routes raise `FileParsingTimeoutError`; `_get_sheet_by_index` (`:191`) is called directly. Cover both pool call sites, `xlsx/tools.py:303` and `xlsx/processor.py:80`. PDF raises it.

### Task 8: Config default and real start method in the log (AC11, AC13)

**Files:** `config/datasources/datasources-config.yaml` (`:141-143`), `src/codemie/datasource/datasources_config.py` (`:87`), `src/codemie/datasource/loader/file_processor_pool.py` (`:59-79`), `tests/codemie/datasource/loader/test_file_processor_pool.py`
**Test-first: yes — `FILE_CONFIG.processing_timeout == 1800` as loaded from the yaml; with `max_tasks_per_child=100`, `ProcessPoolExecutor` receives a `spawn` `mp_context` and the log line says `start_method=spawn`; with `-1`, the default context is used and the log reports its method.**

- [ ] Add `processing_timeout: 1800` under `file_loader`. Declare the field as `Field(default=-1, description="Seconds to wait for a file parse in the shared pool; values <= 0 mean no limit")` and add nothing else.
- [ ] In `initialize()` build `ctx = get_context("spawn")` when `max_tasks_per_child` is set, else `get_context()`; pass `mp_context=ctx` and log `ctx.get_start_method()`. Leave `set_start_method('fork', force=True)` as is (NG2).

---

**Constraint pass:** no whole-suite or browser task. No pool recreation on timeout (Task 3, NG5). No `parent_process()` (Task 1, D6). No new env var or setting (Task 8, D4). No stats key beyond the existing `FAILED_DOCUMENTS_KEY` (Task 4). Non-timeout parse errors still dropped silently (Task 4, NG7). No xlsx handler changed to raise (Task 7). No `_validate_indexing_result` edit (Task 4). No default pool size, task limit or start method changed (Task 8, NG2). Task 2 teardown kills only recorded worker PIDs and their descendants, never the shared `resource_tracker`.
