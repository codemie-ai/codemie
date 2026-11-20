# File parsing pool deadlock (EPMCDME-17537) - Spec

Source of requirements: `story.md` (approved, AC1-AC13, D1-D11, NG1-NG9). Facts about current code: `technical-analysis.md`.

## Problem

The shared file-parsing `ProcessPoolExecutor` (`src/codemie/datasource/loader/file_processor_pool.py`) deadlocks when a worker reaches its task limit while holding a child pool of its own. The only nesting route is `.xlsb`: `XlsbLoader` -> `XlsxProcessor.load` -> `maybe_pool_submit` (`xlsb_loader.py:43-44`, `xlsx/processor.py:80`), also via `.msg`/`.eml` attachments. Waits on the pool have no timeout (`file_processor_pool.py:142`), so indexing threads and datasource slots hang forever with no error.

## Approach

Remove the cause, then bound every wait. No stuck-pool detection and no pool recreation on timeout.

1. **No nesting.** A module-level flag in `file_processor_pool.py`, set only by the pool's own worker initializer (a function that sets the flag first, then calls `configure_pyroscope` with its current arguments, so a Pyroscope failure cannot leave the flag unset), passed as `initializer=`. When set, `maybe_pool_submit` calls the function directly. Workers may still build the inert module-level pool at import (it starts no processes). No `multiprocessing.parent_process()` check, so the main process, the `uvicorn --reload` child, code-datasource forks and usage-attribution spawns keep using the pool. (story AC1, AC3, D6, D7)
2. **One bounded wait, expiry decided from the future.** `maybe_pool_submit` waits on `.result(timeout)` with `FILE_CONFIG.processing_timeout` (`datasources_config.py:82-87`), read at call time. `config/datasources/datasources-config.yaml` sets `file_loader.processing_timeout: 1800`; any value `<= 0` means no limit (stated next to the field). Expiry is not recognised by the exception type: in Python 3.12 `.result(timeout)` raises the built-in `TimeoutError` on expiry, but `.result()` also re-raises whatever the worker raised, and socket or asyncio timeouts are that same class. So on `TimeoutError` the helper looks at the future. If `fut.done()`, it returns `fut.result()`: the file finished between the expiry and the check, so the late result is returned, or the worker's own exception is re-raised unchanged, whatever its class (never the expiry's bare `TimeoutError`, an `OSError` that the docx reader's `except IOError` would turn into "Error reading document" while the finished result is lost). Only if `not fut.done()` does it cancel the future (effective only if not started) and raise `FileParsingTimeoutError`; the pool is not recreated. The limit is per attempt: the retry after `BrokenProcessPool` gets its own full wait, so one file can wait up to twice the limit. `FileParsingTimeoutError` (in `codemie/datasource/exceptions.py`, not a subclass of `OSError`/`TimeoutError`, with `__reduce__` like its neighbours) has a fixed message, `File parsing timed out after <N>s`, so every layer that shows `str(e)` shows the same text. (story AC4, AC11, AC12, D4, D8, D9)
3. **SharePoint** `_extract_documents_from_bytes_multiprocess` (`sharepoint_loader.py:1076-1087`) calls `maybe_pool_submit(extract_documents_from_bytes, ...)` instead of the executor directly. Its existing catch-all in `_download_and_extract_file` already counts the file as failed. (story AC7)
4. **File datasource accounting, in the loader.** In `_load_docs_parallel` (`file_loader.py:112-137`) `FileParsingTimeoutError` is caught before the generic handler: logged with the file name and counted as failed and as timed out. `get_load_stats()` reports it under the existing `FAILED_DOCUMENTS_KEY` (read by `_persist_load_stats`, `base_datasource_processor.py:323-347`); no new stats key. The loader also tracks whether any file of this run produced documents. After the loop, as the xwiki loader does (`xwiki_loader.py:123-130`), if at least one file timed out and no file produced documents, it raises a datasource-level error `File parsing timed out for <N> file(s); nothing was indexed`. The raise sits after the loop so the per-file handler cannot swallow it, and the existing error path sets the error status and releases the slot. The update processor's loader sees only the newly added files, so the same rule covers create and update; `_validate_indexing_result` and `_on_process_end` (`file_datasource_update_processor.py:347-348`, which sets the chunk count to the whole index) are not touched. Other per-file errors stay as today. (story AC5, AC6, AC12)
5. **Tools: the timeout is never relabelled.** `FileParsingTimeoutError` is re-raised unchanged (`except FileParsingTimeoutError: raise` before the generic handler) at every handler between the helper and the tool boundary: `file_analysis_tool._process_single_file` (`:165-168`), `docx/reader.py:157` (`read_with_markitdown`, pool call at `:135`) and `:209` (`read_from_bytes`), `docx/processor.py:88` and `:125`, `docx/tools.py:244`. A healthy Word file is therefore never reported as "Document might be corrupted". PDF passes it through already. The spreadsheet tool (`src/codemie_tools/file_analysis/xlsx/tools.py`) reaches the pool by two routes: `_process_excel_file` calls `maybe_pool_submit` itself (`:303`), and `_load_excel_file` calls `XlsxProcessor.load` (`:148`), which calls it at `xlsx/processor.py:80`. Handlers around them: `:313` (default read; returns `Failed to process Excel file: <str(e)>`), `:166` (sheet names; returns `Error getting sheet names: <str(e)>`), `:281` (stats; returns `{"error": "Failed to get Excel stats: <str(e)>"}`, rendered as `Error: ...` by `_format_stats_as_markdown`) and `:191` (`_get_sheet_by_index`, returns `Error getting sheet by index: <str(e)>`; nothing in `src` calls this method, so it is unreachable through `execute`). Each keeps returning a string that contains the error's fixed message; none is changed to re-raise. `:149` only logs and re-raises, so it does not hide the error. The `filter_values` and `sheet_index` routes (`_get_filtered_content`, `_get_sheet_by_index_content`) have no handler: `FileParsingTimeoutError` leaves the tool as the raised error, like the other tools. Git, SVN and Azure DevOps loaders need no change: their per-file `except Exception` skips the file. (story AC8, AC9, D3)
6. **Startup log** reports the executor's real start method. `initialize()` builds the context explicitly with the same result as today (`spawn` when `max_tasks_per_child` is set, else the default), passes it as `mp_context=` and logs `ctx.get_start_method()`, not `multiprocessing.get_start_method()` (`:78-79`). No default changes (NG2). (story AC13)

With `ENABLE_FILE_MULTIPROCESSING=false` the pool is not used, parsing runs inline and no timeout applies. (story AC10)

## Acceptance criteria

Those of the story: AC1-AC13 (story AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC8, AC9, AC10, AC11, AC12, AC13), with two wordings made precise here:

- AC6: "no file was indexed" means no file of this run produced documents. In the update flow it covers the new files only: when every new file times out, the update ends in ERROR with the timeout message; the old index is not deleted, so old content stays. (story AC6)
- AC8: for the tools that raise (file-analysis, PDF, Word on both paths), the error that reaches the caller is `FileParsingTimeoutError` with its fixed message, not fallback text, "Document might be corrupted" or a generic read error. For the spreadsheet tool, where its handlers return text (`:313`, `:166`, `:281`, `:191`), the result contains "File parsing timed out" (case-insensitive) instead of a bare "Failed to process Excel file"; on the `filter_values` and `sheet_index` routes, which have no handler, the error that reaches the caller is `FileParsingTimeoutError`. (story AC8)

Verification the plan must cover: mock-based unit tests in the style of `test_file_processor_pool.py` and `test_file_loader.py`:

- In-worker inline path with the flag set and reset by the autouse fixture.
- Timeout, cancel, no reinit; `<= 0` means no limit; the retry after `BrokenProcessPool` gets its own bounded wait.
- Late finish: the future completes between the expiry and the `fut.done()` check; the result is returned, nothing is cancelled and no `FileParsingTimeoutError` is raised. The same check with a worker exception on the done future re-raises that exception unchanged.
- A submitted function that raises `TimeoutError` reaches the caller unchanged and is not counted as a timeout (AC12).
- A timeout counts as failed; all-timed-out raises the datasource error, in the create flow and in the update flow (existing index, all new files time out).
- SharePoint routing and failed count.
- AC8 at each tool boundary: file-analysis, PDF, Word (both docx paths, through `docx/tools.py`) and the spreadsheet tool, each route through `execute`: default read (`:313`), `get_sheet_names` (`:166`) and `get_stats` (`:281`) assert the result text; `filter_values` and `sheet_index` assert the raised `FileParsingTimeoutError`; `_get_sheet_by_index` (`:191`) is tested by a direct call, as `execute` cannot reach it. Cases that fail the pool call at `xlsx/processor.py:80` and at `:303` are both covered.

AC2 real-process test (`tests/.env.test` sets `ENABLE_FILE_MULTIPROCESSING=false` and spawned workers inherit it, so a naive test passes without the fix): build a standalone spawn `ProcessPoolExecutor` with `max_workers=1`, a lowered `max_tasks_per_child` and the real initializer, not `FileProcessPoolManager.initialize`, which forces the global start method to `fork` in the pytest process. Set `ENABLE_FILE_MULTIPROCESSING=true` in the environment before the pool starts, so workers inherit it and the nested `maybe_pool_submit` really goes to a pool. Run a nested call past the task limit under a short bounded wait and assert it completes. Add a negative control: the same scenario with a plain initializer (no flag) does not complete within the same short bound, proving the test fails without the fix.

- Worker functions live in a small helper module, `tests/codemie/datasource/loader/pool_worker_helpers.py` (the area's test dir and its `*_helpers.py` naming; named without `test_` so pytest does not collect it). `pytest.ini` sets `--import-mode=importlib`, under which a function defined in a test module may not load in a spawned child. The test refers to the functions as `tests.codemie.datasource.loader.pool_worker_helpers.<name>`, the import style other tests use (`from tests.codemie...`); `tests/__init__.py` and `tests/codemie/__init__.py` only hold the licence header. The helper imports only the standard library and `file_processor_pool`, never `tests/conftest.py`. The plan confirms the child can import it with the repo's pytest command (spawn copies the parent's `sys.path`) before relying on it.
- Teardown order, for the negative control: the executor's manager thread stays blocked in `p.join()` on the stuck worker, and Python joins that thread at process exit, so the pytest worker (`-n 3`) would hang at the end of the run. A fixture or `finally` first kills the stuck workers' whole process tree with `psutil` (already a dependency, `pyproject.toml:69`): for each process in `executor._processes`, its children recursively, then the worker itself. Only then it calls `executor.shutdown(wait=False, cancel_futures=True)`. Killing only the worker leaves its nested pool's grandchild orphaned, and shutting down first would block on the stuck worker.

## Non-goals

- NG1: Recovering datasources already stuck from past incidents (story NG1).
- NG2: Changing pool size, per-worker task limit or start method defaults (story NG2).
- NG3: Time limits for other background processing, apart from waits on the file-parsing pool (story NG3).
- NG4: Pod-level health checks for a stuck pool (story NG4).
- NG5: Stuck-pool detection and recreation by stopping workers (story NG5).
- NG6: A shorter, tool-only wait limit (story NG6).
- NG7: Counting non-timeout parse failures as failed in file datasources (story NG7).
- NG8: The code-datasource subprocess mode (story NG8).
- NG9: The stale-indexing watchdog (story NG9).
- No new environment variable or second timeout setting, no wiring of the unread `enable_multiprocessing` and `max_subprocesses` fields, and no feature docs outside the task dir.
- No shared parse-error base class or raise-first convention across the tool handlers; only the sites listed in Approach 5 change.

## Decisions

- D1: SharePoint covered by the fix and the timeout over file datasources only — user
- D2: A timed-out file counts as failed and the datasource finishes, over failing the whole datasource; error only if nothing was indexed — user
- D3: File-analysis tools covered by the timeout over leaving them unbounded — user
- D4: Timeout held in the existing unread `FILE_CONFIG.processing_timeout`, set to 1800 s in the yaml, over a new setting — user, answers AR-3
- D5: Startup log shows the executor's real start method over the global one
- D6: Worker detection by a flag set in the pool's initializer over `multiprocessing.parent_process()` — user
- D7: Workers may build the inert module-level pool at import over adding an import-time process check
- D8: One timeout for every caller over a shorter tool-only limit
- D9: Dedicated `FileParsingTimeoutError` outside `OSError` over reusing `TimeoutError`, which `except IOError` handlers would relabel
- D10: Fix in the shared helper covering all callers over per-caller changes — user
- D11: Commits use the Jira ticket created from this story — user
- Expiry decided from the future (`not fut.done()`) over the exception type, so a worker's own `TimeoutError` is never reported as a pool timeout — user, answers AR-4
- All-timed-out datasource error raised in the loader at the end of `_load_docs_parallel`, as the xwiki loader does, over a timed-out count in the loader's stats with the processor choosing the message — user, answers AR-1
- An update where every new file times out ends in ERROR over completing with a warning; the old index stays — user, answers AR-1
- Fixed message `File parsing timed out after <N>s`, re-raised unchanged at the docx catch-alls, over leaving each wrapper to prefix its own text — user, answers AR-3
- Spreadsheet tool keeps returning a string that contains the timeout text over changing it to raise — user, answers AR-3
- Per-file bound with no abort after consecutive timeouts over a loop-level limit, so a wedged pool costs files x limit (spec call, per NG5; AR-2 kept)
- Retry limit per attempt over a total budget for the file (spec call)
- Timeout value read per call from `FILE_CONFIG` over capturing it at pool initialization, so the setting is not frozen at import (spec call)
- Any value `<= 0` means no limit, not only `-1`, over passing `0` to `.result()` which would time out at once (spec call)
- Initializer sets the flag before `configure_pyroscope` over after it (spec call)
- Explicit `mp_context` in `initialize()` with today's resulting method over reading a private executor attribute for the log (spec call)
- On expiry, a future that is already done returns its result or re-raises its own exception, over re-raising the expiry's `TimeoutError` or raising `FileParsingTimeoutError` for a file that finished — user
- Real-process test worker functions in a helper module under `tests/codemie/datasource/loader/` over defining them in the test module — user
- Real-process test teardown kills the process tree with `psutil` before shutting the executor down, over shutting down first or killing only the parent — user
- Every xlsx handler around a pool call keeps returning text and the two handler-less routes raise, over changing the handlers (spec call)

## Assumptions

- `FILE_CONFIG` can be imported into `file_processor_pool.py` without an import cycle: `datasources_config.py` imports only `os`, `yaml`, `pydantic` and `codemie.configs` (architecture review); the plan confirms it.

## Open risks

- A wedged pool (both workers stuck on tasks that never finish, no kill under NG5) makes a datasource of N files hold its slot for about N x limit (100 files: about 50 hours), and up to twice that per file with the retry. Accepted under NG5; whether a worker can hang without the nested pool (for example a stalled LLM image call in a PDF worker) is not established.
- The effect of an ERROR status on using the existing datasource after an all-timed-out update was not checked (search and assistants over the old index). The plan verifies it before the update-flow behaviour is built.
- `0` means no limit here but a zero-second limit for the code loader (`code_datasource_processor.py:149`); a mistyped `0` silently restores the hang.
- Whether the yaml reaches pods from mounted config or from the image is not known; if it is baked in, setting the timeout to `-1` is a rebuild.
- Whether the agent framework turns an exception escaping a tool into readable tool-error text is not checked; the plan verifies AC8 end to end.
- With the stale-indexing watchdog on (NG9), its 300 s threshold fires before the 1800 s timeout.
- The real-process test relies on the repo root being on the parent's `sys.path` so the spawned worker can import `tests.codemie...`; not run here, the plan confirms it with the repo's pytest command.
