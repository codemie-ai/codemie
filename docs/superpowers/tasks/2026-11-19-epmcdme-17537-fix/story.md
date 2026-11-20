# File parsing pool deadlock — Story

**Date**: 2026-11-19
**Status**: Approved
**Architecture review**: intent, 2026-11-19, 505e07c1ed32
**Ticket**: [EPMCDME-17537](https://jiraeu.epam.com/browse/EPMCDME-17537)

---

## Context

- File datasources, SharePoint datasources and assistants' file-analysis tools parse documents in one shared pool of worker processes (`FileProcessPoolManager`, `src/codemie/datasource/loader/file_processor_pool.py`). Other loaders reach the same pool through the shared spreadsheet, PDF and Word parsers: Git and SVN code datasources (`.xlsb` files) and the Azure DevOps wiki and work-item loaders (PDF, Word and Excel attachments).
- One route submits to the pool again from inside a pool worker: a file or SharePoint datasource parses an `.xlsb` workbook, directly or as an attachment in an `.msg`/`.eml` file, through `XlsbLoader` → `XlsxProcessor` → `maybe_pool_submit`. The worker then starts a worker of its own. After its fixed task limit, that worker can never shut down, the pool stops handing out work, and every later file waits forever. Only file and SharePoint datasources can cause this. Every other caller (tools, Git, SVN, Azure DevOps) submits from the main process, and the functions it sends never call the pool, so it only waits on a stuck pool.
- Waiting on the pool has no time limit. A stuck pool therefore leaves every later file datasource at 0% with no error, holds its indexing slot forever, and recovers only when the pod restarts. This happened on preview on 2026-11-18. Prod runs the same code and settings. Details are in `INVESTIGATION-file-datasource-indexing-hang.md`.
- The file datasource configuration already has a `processing_timeout` setting (`loaders.file_loader.processing_timeout`, default `-1`), but nothing reads it.
- Today a parse error other than an unreadable workbook, on one file of a multi-file file datasource, is logged and the file is dropped without being counted. This story leaves that unchanged (NG7).

---

## Complexity

**Size**: M (re-scoped from L 21/36; not re-scored) · **Recommended flow**: sdlc-standard

The change is concentrated in the shared pool helper. Every caller goes through `maybe_pool_submit` except SharePoint, which is moved onto it. The file loader gains failure counting for timeouts. Stuck-pool detection and recreation are out of scope. Rollback is cheap: turn off file multiprocessing, or set the timeout to `-1`.

---

## Story

**As a** CodeMie user who indexes documents (uploaded files or SharePoint) or chats with an assistant about a document, **I want** document parsing to always either finish or fail with a clear error **so that** my datasource never sits at 0% forever and one stuck pool doesn't block everyone's indexing until the pod restarts.

---

## Background

When the file-parsing pool deadlocks, every new file datasource on that pod hangs silently. Users see 0% with no error, indexing slots fill up, and nothing recovers until an operator restarts the pod. The autotests on preview trigger it within minutes, and prod is exposed too. The fix removes the cause (pool workers starting workers of their own) and adds a time limit on waiting for the pool, so any future hang becomes a visible failure instead of a permanent one. The main goal is that datasources are processed and never stay stuck forever. Behaviour that works today must not change.

---

## Acceptance Criteria

- [ ] AC1: Given file parsing runs in the pool, when parsing inside a pool worker calls the shared pool helper again (an `.xlsb` workbook in a file or SharePoint datasource, directly or attached to an `.msg`/`.eml` file), then that call runs directly in the same worker and no process is started from inside a pool worker.
- [ ] AC2: Given multiprocessing is on, when one worker parses more files than its per-worker task limit, including `.xlsb` files that used to start a nested worker, from file datasources and from SharePoint datasources, then every one of them reaches a completed status, later file and SharePoint datasources keep indexing, and the worker is replaced normally at its limit. (Testable with a lowered `FILE_MULTIPROCESSING_MAX_EXECUTED_TASK_PER_WORKER`.)
- [ ] AC3: Given a process that is not one of the pool's own workers (the main API process, the child process that `uvicorn --reload` runs, the code-datasource subprocess), when it calls the shared pool helper with multiprocessing on, then the call goes through the pool exactly as today.
- [ ] AC4: Given a wait on the pool exceeds the parsing timeout, when the timeout is reached, then the wait ends with a dedicated "file parsing timed out" error, work that has not started yet is cancelled, and the pool is not recreated.
- [ ] AC5 (negative): Given a multi-file file datasource where one file's parsing times out, when indexing runs, then the other files are indexed, the timed-out file counts as failed in the datasource's processing statistics, the reason is logged with the file name, the datasource completes, and its indexing slot is released.
- [ ] AC6 (negative): Given a file datasource where no file was indexed and at least one file's parsing timed out (including a single-file datasource), when indexing runs, then the datasource ends in an error status whose message says file parsing timed out (not the generic "no chunks imported"), and its indexing slot is released.
- [ ] AC7 (negative): Given a SharePoint datasource where one file's parsing times out, when indexing runs, then that file counts as failed in SharePoint's existing failed-files count, the other files are indexed, and the run finishes. SharePoint goes through the shared pool helper rather than calling the pool directly.
- [ ] AC8 (negative): Given an assistant uses a file-analysis tool on a spreadsheet, PDF or Word document, when parsing times out, then the tool call returns a clear "file parsing timed out" error instead of the tool's fallback text or a generic read error, and the conversation continues.
- [ ] AC9 (negative): Given a Git or SVN code datasource where parsing an `.xlsb` file times out, or an Azure DevOps wiki or work-item datasource where parsing an attachment times out, when indexing runs, then that file or attachment is skipped with a warning through the loader's existing error handling, the rest is indexed, and the datasource completes.
- [ ] AC10: Given multiprocessing for file parsing is turned off, when file datasources, SharePoint datasources, code datasources and file-analysis tools run, then their behaviour is the same as today, and no timeout applies, because parsing runs directly.
- [ ] AC11: Given the parsing timeout is set to `-1`, when files are parsed in the pool, then waits have no limit, as today.
- [ ] AC12: Given these behaviours exist today, when this change ships, then they stay the same: an unreadable or encrypted workbook still fails the file datasource with its current message; other parse errors in a multi-file file datasource are still logged and the file dropped; a broken pool is still recreated and the file retried once; an error raised by parsing inside a worker reaches the caller with the same type as today.
- [ ] AC13: Given the file-parsing pool starts, when its startup is logged, then the log shows the start method the pool actually uses (`spawn` while a per-worker task limit is set).

---

## Out of Scope

- NG1: Recovering datasources already stuck from past incidents. They are re-indexed or re-created manually. Deploying the fix restarts the stuck pod.
- NG2: Changing the pool's size, its per-worker task limit, or its start method defaults.
- NG3: Time limits for other background processing (code datasources, Azure DevOps loaders and other subprocess runners), apart from their waits on the file-parsing pool, which AC9 covers.
- NG4: Pod-level health checks that detect a stuck pool and restart the pod.
- NG5: Detecting a stuck pool and recreating it by stopping its workers (formerly AC8). With the cause removed, no known path wedges the pool. If an unknown one does, files fail after the timeout instead of hanging, and the pod still needs a restart. Follow-up story.
- NG6: A shorter, tool-only wait limit (formerly AC9/AC10 and D6). See D8. Follow-up story.
- NG7: Counting non-timeout parse failures (corrupt documents) as failed in file datasources (formerly AC5 and D7). Follow-up story. It also has to decide behaviour with multiprocessing off, where such errors are not caught per file today.
- NG8: The code-datasource subprocess mode (`enable_multiprocessing` for code, off by default), which calls the pool from a forked child process.
- NG9: The stale-indexing watchdog (`STALE_INDEXING_WATCHDOG_ENABLED`, off by default). When it is on, it marks a file datasource with no progress for `stale_indexing_threshold_seconds` (300 s) as interrupted, because resume is not supported for file datasources. That happens before a longer parsing timeout fires. Unchanged by this story.

---

## Decisions

- D1: Should SharePoint datasources be covered too? → Yes. The no-nesting fix and the timeout also apply to SharePoint, which uses the same pool. — user
- D2: What happens when one file in a multi-file datasource times out? → That file counts as failed, the rest are indexed and the datasource finishes. It shows an error only if nothing was indexed. — user
- D3: Should the timeout also cover assistants' file-analysis tools? → Yes. A timed-out parse fails the tool call with a clear error instead of hanging the chat. — user
- D4: Which setting holds the parsing timeout? → The existing, unread `loaders.file_loader.processing_timeout` (`FileConfig.processing_timeout`). Its value is set to 1800 s in `config/datasources/datasources-config.yaml`, so slow but healthy parses keep working: files on prod parse in 1–4 s, but PDFs with images call a multimodal LLM per image, and the wait includes time queued behind other files. `-1` keeps "no limit". No second setting is added. — proposed by PO: architecture review (finding 3); 1800 s default confirmed by user
- D5: Does the "process pool" startup log line need to show the real start method? → Yes. It currently reports the global start method (`fork`), not the pool's (`spawn`), which misled the investigation. — proposed by PO: technical-analysis.md, Decisions Already in the Code
- D6: How does code know it is running inside a pool worker? → A module-level flag in `file_processor_pool.py` that only the pool's own worker initializer sets. That function wraps the existing `configure_pyroscope` initializer and is passed as `initializer=`. It is code, not deployment configuration. `multiprocessing.parent_process()` is not used, because it is also true in the child process that `uvicorn --reload` runs, which would silently turn the pool off there. Checked with a stdlib reproduction (2 workers, task limit 3, one nested call): without the flag the 4th task hangs; with the flag 7 tasks finish across 3 recycled workers with no grandchild processes, and calls from the main process still use the pool. — user (this session)
- D7: Must workers avoid building a pool object when they import the module? → No. A worker may still construct the module-level pool when it imports the module, before the initializer runs. The object starts no processes, because every call inside a worker runs directly (D6). Avoiding it would need a separate import-time check, which is the fragile part D6 avoids. — proposed in re-scope
- D8: One timeout for all callers, or a shorter one for tools? → One timeout (D4) for every wait on the pool. The spreadsheet, PDF and Word parsers that tools call are the same code that Git and SVN code datasources call, so a tool-only limit set in those parsers would also cut Git and SVN parsing. A shorter tool limit is NG6. — proposed in re-scope, replaces the former two-limit decision
- D9: Which error does a timeout raise? → A dedicated parsing-timeout error that is not a subclass of `OSError` or `TimeoutError`. In Python 3.12, `concurrent.futures.TimeoutError` is the built-in `TimeoutError`, a subclass of `OSError`, so existing `except IOError` handlers (for example the docx reader) would turn it into "Error reading document". — proposed in re-scope
- D10: How broad is the fix? → It lives in the shared helper, so it covers every caller. Only file and SharePoint datasources can cause the deadlock (via `XlsbLoader`). Tools, Git, SVN and Azure DevOps only wait on a stuck pool and need no code change beyond what the helper gives them (AC8, AC9). — user (this session)
- D11: Which ticket ID do commits use? → The Jira ticket created from this story (see **Ticket**). — user

---

## Open Questions

- None
