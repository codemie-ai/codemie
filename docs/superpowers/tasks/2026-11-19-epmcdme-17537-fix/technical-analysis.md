# Technical Research

**Task**: file datasource process pool multiprocessing maybe_pool_submit FileProcessPoolManager file_loader sharepoint_loader file_analysis xlsx pdf docx datasource concurrency slot indexing timeout
**Generated**: 2026-11-19 · **Updated**: 2026-11-19 (re-scoped story: initializer flag, one parsing timeout, no pool recreation)
**Research path**: codegraph, then manual verification and a stdlib reproduction in the re-scope

---

## 1. Original Context

File datasources (knowledge_base_file) hang forever at 0% on the ds-pool. Root cause confirmed on preview (pod codemie-api-ds-pool-55dcf658cc-5nzmx, 2026-11-18): the shared file-parsing ProcessPoolExecutor (src/codemie/datasource/loader/file_processor_pool.py, max_workers=2, max_tasks_per_child=100 → spawn start method) deadlocks. Each spawned worker imports file_processor_pool and builds its own module-level pool. Parsing inside the worker reaches maybe_pool_submit again (XlsbLoader → XlsxProcessor) and spawns a grandchild. When the worker reaches max_tasks_per_child it returns, and multiprocessing.util._exit_function joins all non-daemon children before threading._shutdown sends the nested pool's stop sentinel, so the worker never exits. The parent's _ExecutorManagerThread then blocks forever in p.join() (no timeout), no new work reaches any worker, replacements spawn up to max_workers and sit idle, and maybe_pool_submit's .result() (no timeout) blocks the indexing thread forever, never releasing the DatasourceConcurrencyManager slot and never setting an error. Reproduced locally with stdlib only. Full investigation notes in INVESTIGATION-file-datasource-indexing-hang.md at repo root.

**Approach after re-scope** (see `story.md`, D4 and D6–D10):
1. **Stop the nesting.** A module-level flag in `file_processor_pool.py`, set only by the pool's own worker initializer (a function that wraps `configure_pyroscope` and is passed as `initializer=`). When the flag is set, `maybe_pool_submit` calls the function directly. Workers may still build the inert module-level pool object at import; it starts no processes. No `multiprocessing.parent_process()` check.
2. **Bound the wait.** One timeout for every wait on the pool, from the existing `FileConfig.processing_timeout`, set to 1800 s in `datasources-config.yaml` (`-1` = no limit). On timeout: raise a dedicated parsing-timeout error (not an `OSError`/`TimeoutError` subclass), cancel the future if it has not started, and do not recreate the pool.
3. **Route SharePoint through `maybe_pool_submit`.**
4. **File loader:** a timed-out file counts as failed in the load stats. If nothing was indexed and at least one file timed out, the datasource error says parsing timed out.
5. **Startup log** shows the pool's real start method.

Out of scope: stuck-pool detection and recreation, a shorter tool-only limit, counting non-timeout parse errors as failed, the code-datasource subprocess mode, and the stale-indexing watchdog.

---

## 2. Codebase Findings

### Existing Implementations

**Purpose A — run file parsing off the indexing thread in a shared worker pool**
- `FileProcessPoolManager` — `src/codemie/datasource/loader/file_processor_pool.py:30-114`. Class-level singleton state (`_executor`, `_initialized`, `_max_workers`). `initialize()` (`:49`) calls `multiprocessing.set_start_method('fork', force=True)` (`:63`), maps `max_tasks_per_child < 0` to `None` (`:65-66`), builds `ProcessPoolExecutor(max_workers, max_tasks_per_child, initializer=configure_pyroscope, …)` with **no `mp_context`** (`:67-72`), registers `atexit.register(cls.shutdown)` on every call (`:76`), and logs `multiprocessing.get_start_method()` (global, not the executor's) (`:78-79`). `shutdown()` (`:98`) nulls `_executor` and clears `_initialized`.
- Module-level creation — `file_processor_pool.py:118-123`: `file_process_pool = FileProcessPoolManager()` and `initialize(...)` whenever `config.ENABLE_FILE_MULTIPROCESSING` is true. No process-identity check: runs in every process that imports the module, including spawned workers. Building the executor starts no processes. In CPython 3.12, `ProcessPoolExecutor.__init__` neither spawns workers nor starts the manager thread; both happen on the first `submit()` (verified in the project venv, 3.12.13).
- `_reinitialize_pool()` — `:128-135`: under `_reinit_lock`, `shutdown(wait=False)` then `initialize(force=True)`.
- `maybe_pool_submit(fn, *args, **kwargs)` — `:138-148`: inline call when the flag is off or pool not initialized (`:139-140`); otherwise `submit(...).result()` with **no timeout** (`:142`); on `BrokenProcessPool` reinit and resubmit once, again no timeout (`:143-146`); other exceptions re-raised unchanged. `**kwargs` are passed through to `fn`, so a timeout parameter must not collide with parser keyword arguments.
- Direct pool user bypassing `maybe_pool_submit` — `SharePointLoader._extract_documents_from_bytes_multiprocess` (`src/codemie/datasource/loader/sharepoint_loader.py:1076-1087`): `file_process_pool.get_executor().submit(extract_documents_from_bytes, …).result()`, no timeout, no `BrokenProcessPool` handling; raises `RuntimeError` if the pool is uninitialized. Its caller `_download_and_extract_file` (`:1067-1074`) catches every `Exception` and returns `[]`, and the caller of that counts an empty result as a failed file (`_total_files_failed`, `:987-989`).

**Purpose B — code that runs inside a pool worker and itself calls the pool (nesting)**
- Task sent to workers by both datasource paths: `extract_documents_from_bytes` (`src/codemie/datasource/loader/file_extraction_utils.py:122`), dispatching via `LOADERS` (`:53-71`).
- The only nested route: `XlsbLoader.load` (`src/codemie/datasource/loader/xlsb_loader.py:43-44`) → `XlsxProcessor.load` → `maybe_pool_submit(load_xlsx, …)` (`src/codemie_tools/file_analysis/xlsx/processor.py:80`). `XlsbLoader` wraps any exception as `UnreadableWorkbookError` (`xlsb_loader.py:45-46`).
- The same route is reached through email attachments: `EmlLoader` (`eml_loader.py:142`) and `OutlookMsgWithAttachmentsLoader` (`binary/msg_loader.py:150`) call `extract_documents_from_bytes` for each attachment, inside the worker, so an `.xlsb` attachment nests too. (Added in re-scope.)
- Other `maybe_pool_submit` callers — `docx/reader.py:135,190`, `pdf/processor.py:237`, `xlsx/tools.py:303`, `file_analysis_tool.py:160`. They run in the parent (agent tools, Azure DevOps loaders), so they only wait on the pool. The functions they submit (`codemie_tools/file_analysis/workers/*.py`: `convert_file_to_markdown`, `process_xlsx_to_markdown`, `extract_pdf_markdown`, `extract_docx_content`) never call the pool; `.xlsb` in `convert_file_to_markdown` calls `load_xlsx` directly (`markdown_workers.py:44-45`, `xlsx_workers.py:214`). (Verified in re-scope.) `DocxLoader` (`docx_loader.py:31`) wraps langchain_markitdown, not `DocxReader`.

**Purpose C — bound how long an indexing run waits and fail it visibly**
- `DatasourceConcurrencyManager.run` — `src/codemie/datasource/datasource_concurrency_manager.py:96-153`: slot released in `finally` (`:151-153`) only once `process_func` returns or raises; queue wait bounded by `DATASOURCE_QUEUE_TIMEOUT` (`config.py:225`, 3600 s) but execution is unbounded.
- `BaseDatasourceProcessor.process` — `src/codemie/datasource/base_datasource_processor.py:261-276`: any exception → `index.set_error(str(ex))`, callbacks, re-raise. `_validate_indexing_result` (`:556-581`) raises `NoChunksImportedException` when `current__chunks_state == 0`. `_persist_load_stats` (`:323-347`) reads `SKIPPED_DOCUMENTS_KEY` and `FAILED_DOCUMENTS_KEY` from `loader.get_load_stats()` and adds both to the total.
- `FilesDatasourceLoader._load_docs_parallel` — `src/codemie/datasource/loader/file_loader.py:112-137`: per file, `SkippedFileException` → skip counted; `UnreadableWorkbookError` → re-raised; **any other `Exception` → `logger.error` and the file is dropped without counting and without yielding**. `get_load_stats()` (`:165-166`) returns only the skipped count. The serial path `_lazy_load_documents` (`:139-163`) catches only `SkippedFileException`.
- Sibling bounded-subprocess mechanisms: code datasource `_run_in_subprocess` (`src/codemie/datasource/code/code_datasource_processor.py:138-160`) — `p.join(CODE_CONFIG.processing_timeout)` (`-1` = none, `datasources_config.py:49`), then `terminate()`; usage attribution `_run_in_process` (`src/codemie/service/usage_attribution/analysis.py:882-907`) — explicit `get_context("spawn")`, `daemon=True`, `asyncio.wait_for` timeout then `kill()`.
- `FileConfig.processing_timeout: int = -1`, `enable_multiprocessing`, `max_subprocesses` (`src/codemie/datasource/datasources_config.py:82-87`, exposed as `FILE_CONFIG`, `:191`) exist but only `chunk_size`/`chunk_overlap` are read (`file_datasource_processor.py:190`); `config/datasources/datasources-config.yaml:141-143` sets only those two.

### Decisions Already in the Code
- Start method: global `fork` is forced (`file_processor_pool.py:63`), but no `mp_context` is passed while `max_tasks_per_child` is set; CPython 3.12 (`pyproject.toml:3`) then uses `spawn`. Alternative: pass `mp_context`. Reason: none found.
- Pool created at import time (`:118-123`) rather than lazily or from app startup. Reason: none found.
- Recovery convention: reinit-and-retry-once only for `BrokenProcessPool` (`:143-146`); covered by `test_broken_pool_reinits_and_retries`.
- Per-file failure convention in the parallel loader: generic errors are logged and dropped, not fatal (`file_loader.py:136-137`), asserted by `test_error_per_file_is_logged_and_skipped` (`tests/codemie/datasource/loader/test_file_loader.py:250-271`). Kept unchanged by the re-scope (story AC12, NG7).
- Exceptions crossing the pool boundary implement `__reduce__` (`src/codemie/datasource/exceptions.py:112-115`). A new parsing-timeout exception raised in the parent does not cross the boundary, but following the same convention keeps it safe if it ever does.
- Timeout config convention: flat `config.py` settings with `_SECONDS` suffix and `Field(gt=0)` (e.g. `SPEND_LEDGER_POOL_TIMEOUT_SECONDS`, `config.py:861`), or `-1`/`0` meaning "no timeout" (`DATASOURCE_QUEUE_TIMEOUT`, `CODE_CONFIG.processing_timeout`). The re-scope uses the second form via `FILE_CONFIG.processing_timeout`.
- `FILE_MULTIPROCESSING_MAX_EXECUTED_TASK_PER_WORKER < 0` disables recycling (`:65-66`), which also makes the executor use the global `fork` method.

### Task Statements Checked Against the Code
- "max_workers=2, max_tasks_per_child=100" — holds (`config.py:1186-1187`).
- "→ spawn start method" — holds per CPython 3.12 `ProcessPoolExecutor` behaviour with `max_tasks_per_child` and no `mp_context`; log at `:78-79` reports the global method, so it reads `fork`.
- "Each spawned worker imports file_processor_pool and builds its own pool" — holds (`:118-123`, no process check). The pool object is inert until something submits to it (see Purpose A).
- "parsers running inside the worker call maybe_pool_submit again" — holds only for `.xlsb` via `XlsbLoader` (`xlsb_loader.py:43-44` → `xlsx/processor.py:80`), including `.xlsb` email attachments. The pdf/docx readers, `xlsx/tools.py` and `file_analysis_tool` run in the parent, and the worker functions they submit never call the pool.
- "maybe_pool_submit's .result() (no timeout) blocks the indexing thread forever, never releasing the slot" — holds (`:142`, `datasource_concurrency_manager.py:150-153`).
- "A stuck pool then fails the datasource with an error and frees its slot" — not true today for multi-file datasources: an exception from `maybe_pool_submit` is caught per file in `_load_docs_parallel` (`file_loader.py:136-137`); the datasource fails only if no chunks were imported (`base_datasource_processor.py:572-581`). In SharePoint any error yields `[]`. Addressed by story AC5/AC6 (timeouts counted as failed, specific error when nothing was indexed).
- "give .result() a timeout" — `sharepoint_loader.py:1087` waits on `.result()` outside `maybe_pool_submit`, so a change to `maybe_pool_submit` alone does not reach it. Addressed by story AC7 (SharePoint moves onto the helper).
- Kernel/CPython join ordering (`_exit_function` before `threading._shutdown`) — reproduced in the re-scope with a stdlib model of the module (module-level pool built at import, 2 workers, `max_tasks_per_child=3`, one nested call): without the initializer flag, task 4 hangs and the old worker stays alive waiting on its grandchild. With the flag, 7 tasks finish across 3 recycled workers, no worker has child processes, and a call from the main process still runs in a pool worker.
- (Re-scope) `concurrent.futures.TimeoutError` in Python 3.12 is the built-in `TimeoutError`, a subclass of `OSError` (verified in the project venv). `docx/reader.py:154` (`except IOError`) would re-wrap it as `DocumentReadError("Error reading document…")`; `email/tools.py:141` catches it explicitly. Hence a dedicated exception (story D9).

### Consumers of Changed Contracts
`maybe_pool_submit` (searched by name across `src/` and `tests/`):
- `file_loader.py:116` (file datasource, parent); `xlsx/processor.py:80` (also inside workers via `XlsbLoader`); `xlsx/tools.py:303`; `docx/reader.py:135,190`; `pdf/processor.py:237`; `file_analysis_tool.py:160` (any exception → `_fallback_decode_text_file`, `:165-168`, so a timeout must be re-raised to reach the user, story AC8). `docx/reader.py:209-211` wraps any exception as `DocumentReadError`.
- Indirect callers through the shared parsers, all in the parent, each catching `Exception` and skipping the file with a warning:
  - Git: `git_loader.py:456-480` `_process_binary_file`.
  - SVN: `svn_loader.py:208-227` `_process_binary_file`.
  - Azure DevOps wiki and work items: `azure_devops_wiki_loader.py:670-711`, `azure_devops_work_item_loader.py:500-541`, PDF/DOCX/XLSX attachment helpers.

  A timeout there skips that file or attachment (story AC9). (Added in re-scope.)
- Tests patching it by module path: `tests/codemie_tools/file_analysis/{test_file_analysis.py,test_excel_tool.py,pdf/conftest.py,pdf/test_pdf_processor*.py,xlsx/conftest.py,xlsx/test_xlsx_converter.py}`, `tests/codemie/datasource/loader/test_file_loader.py`.

`file_process_pool` / `FileProcessPoolManager`:
- `sharepoint_loader.py:44,1077`; imported but unused at `pdf/processor.py:23` and `xlsx/processor.py:23`.
- `tests/codemie/datasource/loader/test_file_processor_pool.py` mutates `_initialized`/`_executor`/`_instance` class attributes and patches `config`, `_reinitialize_pool`, `shutdown`, `initialize`; `TestReinitializePool` asserts exact `initialize(max_workers=2, max_tasks_per_child=100, force=True)` kwargs. The autouse fixture must also reset the new worker flag.

`FilesDatasourceLoader.get_load_stats()` — gains the failed-files count. Read by `BaseDatasourceProcessor._persist_load_stats` (`base_datasource_processor.py:326-330`).

### Architecture and Layers Affected
- Datasource loader layer: `file_processor_pool.py` (flag, initializer wrapper, timeout, log), `file_loader.py` (timeout accounting, timeout error when nothing indexed), `sharepoint_loader.py` (route through helper).
- Tool layer (`codemie_tools/file_analysis`): `file_analysis_tool.py` and the docx reader must let the parsing-timeout error through instead of turning it into fallback text or a read error.
- Datasource processing/orchestration: `base_datasource_processor.py` (error message when nothing was indexed and a timeout occurred).
- Configuration: `config/datasources/datasources-config.yaml` (`file_loader.processing_timeout: 1800`); `src/codemie/configs/config.py:1184-1187` unchanged.

### Integration Points
- Worker initializer `configure_pyroscope` (`src/codemie/configs/pyroscope_config.py:32`), tags `process_name` from `multiprocessing.current_process().name` (`:137`). The new initializer wraps it and keeps its arguments.
- Other processes importing the module, none of which may be treated as pool workers:
  - The code datasource fork from `_run_in_subprocess` (`code_datasource_processor.py:151-152`).
  - The usage-attribution spawn (`analysis.py:884`).
  - The child process `uvicorn --reload` runs locally (`Makefile:84`). Prod runs a single uvicorn process (`Dockerfile:67`).
- PDF loader in workers gets an LLM-backed `images_parser` (`file_extraction_utils.py:153-154`), so a worker task can include network LLM calls. That is why the timeout default is long.
- Stale-indexing watchdog (`triggers/bindings/cron.py:385-386, 498-545`): off by default (`STALE_INDEXING_WATCHDOG_ENABLED=False`, `config.py:443`). When on, it claims records with no update for `stale_indexing_threshold_seconds` (300 s). `knowledge_base_file` is in `UNSUPPORTED_RESUME_TYPES` (`triggers/actors/datasource.py:795-800`), so a file datasource is marked as interrupted rather than resumed. SharePoint is resumed (`:1113`). Unchanged (story NG9).

### Patterns and Conventions
- Module-level singletons configured from `config` at import (`file_processor_pool.py:118`, `datasource_concurrency_manager.py:173`).
- Tests reset class-level singleton state in an autouse fixture (`test_file_processor_pool.py:24-35`) and mock executors/futures with `MagicMock`.

---

## 3. Documentation Findings

### Guides and Architecture Docs
No guide in `.ai-run/guides/` covers the file process pool or `ENABLE_FILE_MULTIPROCESSING`. Relevant general guides: `development/configuration-patterns.md`, `development/error-handling.md`, `testing/testing-patterns.md`.

### Architectural Decisions
None recorded for the pool. Decisions for this change are in `story.md` (D1–D10). Investigation notes: `INVESTIGATION-file-datasource-indexing-hang.md` (repo root, untracked).

### Derived Conventions
See *Decisions Already in the Code*.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/datasource/loader/test_file_processor_pool.py` — inline paths, `BrokenProcessPool` reinit/retry, reinit/retry failures, non-pool exception passthrough, `_reinitialize_pool` call order.
- `tests/codemie/datasource/loader/test_file_loader.py:223-330` — `_load_docs_parallel` routing, per-file error drop, skip counting, `UnreadableWorkbookError` propagation.
- `tests/codemie/datasource/test_datasource_concurrency_manager.py` — slot handling.
- `tests/codemie/datasource/loader/test_xlsb_loader.py`, `test_xlsb_real_fixtures.py` — xlsb parsing.

### Testing Framework and Patterns
pytest, `unittest.mock` (`patch`, `MagicMock`); `tests/.env.test:3` sets `ENABLE_FILE_MULTIPROCESSING=false`, so no test runs a real pool.

### Coverage Gaps
- No test for `.result()` timeouts, behaviour inside a worker process, or module-level init in child processes.
- `SharePointLoader._extract_documents_from_bytes_multiprocess` has no test (no test references it).
- No real-process test of nested pools or `max_tasks_per_child` recycling. The re-scope reproduction (Task Statements) shows the shape of such a test: a small pool with a lowered task limit and a nested call, checked with a bounded wait.

---

## 5. Configuration and Environment

### Environment Variables
- `ENABLE_FILE_MULTIPROCESSING` (default `False`, `config.py:1185`; `true` on preview and prod ds-pool per investigation).
- `FILE_DATASOURCE_MULTIPROCESSING_MAX_WORKERS` (2), `FILE_MULTIPROCESSING_MAX_EXECUTED_TASK_PER_WORKER` (100) — `config.py:1186-1187`.
- `DATASOURCE_CONCURRENCY_LIMIT_ENABLED`, `MAX_CONCURRENT_DATASOURCE_INDEXING`, `DATASOURCE_QUEUE_TIMEOUT` — `config.py:223-225`.
- `STALE_INDEXING_WATCHDOG_ENABLED` (default `False`, `config.py:443`).
- `PYROSCOPE_ENABLED` etc. (worker initializer).
- No environment variable for the pool timeout. It comes from the datasource config file (below), and no new setting is added.

### Configuration Files
`config/datasources/datasources-config.yaml:141-143` (`file_loader`: chunk settings only today). The change adds `processing_timeout: 1800` there; `FileConfig.processing_timeout` keeps `-1` (no limit) as its code default.

### Feature Flags and Deployment Concerns
- `ENABLE_FILE_MULTIPROCESSING=false` removes the pool entirely (documented mitigation).
- Setting `file_loader.processing_timeout: -1` turns the new timeout off.
- No deployment manifest changes are needed: the flag is code, not configuration.

---

## 6. Risk Indicators

Each item ends with how the re-scoped story handles it.

- `_load_docs_parallel` swallows generic per-file errors (`file_loader.py:136-137`); a timeout there would drop the file silently unless it is the only one. → Timeouts are caught and counted as failed (AC5, AC6). Other errors stay as today (AC12, NG7).
- SharePoint waits on the pool directly (`sharepoint_loader.py:1077-1087`), outside `maybe_pool_submit`; it shares the same pool and the same nested route via `XlsbLoader`. → Moved onto the helper (AC7).
- `_reinitialize_pool` uses `shutdown(wait=False)`; a stuck worker, its grandchild and the blocked manager thread from the old executor would stay alive. → Not used for timeouts: the pool is not recreated on timeout (AC4). Stuck-pool recreation is NG5. The `BrokenProcessPool` path is unchanged.
- A timeout value must cover legitimate long tasks, including LLM image parsing inside workers and large workbooks, plus time queued behind other files; agent-tool callers share the same timeout. → 1800 s default (D4), one limit for all callers (D8).
- `.result(timeout)` does not stop a running task; a timed-out task keeps a worker busy until it finishes. → Accepted. A queued future is cancelled (AC4). If both workers run endless tasks, later files fail after the timeout instead of hanging (NG5).
- A process-identity check (`multiprocessing.parent_process()`) is also true in code-datasource forked children, usage-attribution spawn children and the `uvicorn --reload` child. → Not used. The flag is set only by the pool's initializer (D6, AC3).
- `concurrent.futures.TimeoutError` is an `OSError`, so `except IOError` handlers relabel it. → Dedicated exception (D9).
- `file_analysis_tool._process_single_file` turns any exception into fallback text (`:165-168`). → The timeout error must pass through (AC8).
- `atexit.register(cls.shutdown)` added on every `initialize`, including each reinit (`file_processor_pool.py:76`). → Unchanged; it does not run in pool workers (they leave through `os._exit`).
- Misleading `start_method=` log (`:78-79`). → Fixed (AC13).
- `TestReinitializePool` asserts exact `initialize` kwargs; the singleton-state fixture must cover any new class or module attribute, including the worker flag.
- No real-pool tests anywhere (`tests/.env.test:3`).

---

## 7. Summary for Complexity Assessment

The change sits in the datasource loader layer, centred on one 148-line module, `src/codemie/datasource/loader/file_processor_pool.py`. There it adds a worker flag set by the pool initializer, a bounded wait with a dedicated timeout error, and the start-method log fix. Around it:
- `file_loader.py` counts timeouts as failed.
- `base_datasource_processor.py` reports a timeout when nothing was indexed.
- `sharepoint_loader.py` moves onto the helper.
- `file_analysis_tool.py` and the docx reader stop hiding the timeout error.
- `datasources-config.yaml` sets the 1800 s default.

The only nested route is `XlsbLoader` → `XlsxProcessor.load` → `maybe_pool_submit`, reached from file and SharePoint datasources, `.xlsb` email attachments included. It matches the `.xlsb` event in the incident timeline. Tools, Git, SVN and Azure DevOps only wait on the pool, and their existing per-file error handling absorbs a timeout.

The pattern is well understood (stdlib `ProcessPoolExecutor`). The flag approach is confirmed by a stdlib reproduction. No pool recreation or process killing is in scope. Tests are mock-based (`test_file_processor_pool.py`, `test_file_loader.py`); new tests cover the in-worker inline path, the timeout path, timeout accounting, SharePoint routing and tool error passthrough. Key risks after the re-scope:
- Choosing the timeout default; settled at 1800 s.
- Keeping today's behaviour for non-timeout errors and multiprocessing off.
- Making sure no non-worker process is treated as a worker.

---

## 8. External References

- `INVESTIGATION-file-datasource-indexing-hang.md` (repo root) — resolved. Key facts:
  - CPython 3.12 `concurrent/futures/process.py` ~360-374: on `exit_pid`, `processes.pop(pid)`, then `p.join()` with no timeout, then `_adjust_process_count()` instead of `_idle_worker_semaphore.release()`.
  - `_spawn_process` ~791: workers are not daemon processes.
  - `multiprocessing/util.py` ~353-360: `_exit_function` joins `active_children()`.
  - `multiprocessing/process.py` `_bootstrap` ~314-335: `_exit_function` runs before `threading._shutdown()`.
  - Timeline: the grandchild was first spawned at 18:07:04 on an `.xlsb` file; the hang began at 18:12:12 (~100th task).
  - Stdlib repro: `max_tasks_per_child=3`, nested pool on task 2 → task 4 hangs.
  - Mitigation: `ENABLE_FILE_MULTIPROCESSING=false`.
  - Fix options listed there (in-worker inline call, no module-level pool in children, `.result()` timeout plus pool recreation, start-method log). The re-scope chose the initializer flag and the timeout without recreation.
