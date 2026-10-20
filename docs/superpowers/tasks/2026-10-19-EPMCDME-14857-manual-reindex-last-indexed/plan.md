# Manual Reindex "Last indexed" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist `last_reindex_triggered_at` at trigger time for every manual reindex entry point, without bumping `update_date`.

**Architecture:** Follow the actor pattern: call `IndexInfo.stamp_reindex_triggered_at(index_id)` once per manual entry point (never inside `process()`/`reprocess()`/`resume()`/`incremental_reindex()`). The helper returns the timestamp so callers also set it on the in-memory `IndexInfo`, because later full `update()` calls (`set_queued`, `clear_queued`, SharePoint `_on_process_end`) would otherwise overwrite the stamp.

**Tech Stack:** Python, SQLModel/SQLAlchemy, FastAPI BackgroundTasks, pytest (`patch.object(IndexInfo, "stamp_reindex_triggered_at")`, `MagicMock(spec=IndexInfo)`).

**Spec:** `docs/superpowers/tasks/2026-10-19-EPMCDME-14857-manual-reindex-last-indexed/spec.md`

Commit per task using the repository's existing convention (see `.ai-run/guides/standards/git-workflow.md`).

## Global Constraints

- Stamp after `update_index()`, after the full `save()` in `_clear_error`, and after the provider update step (these bump `update_date`).
- Stamp does not change `update_date`; value is trigger time, not completion time.
- Do not touch actors, scheduler logic, `set_error`, `complete_progress`, or `base_datasource_processor.py` L244/L260.
- No schema/migration/UI/API changes.

## Review Focus

- Plain `schedule(background_tasks)` (index creation) must not stamp.
- Stamped value survives a later full `update()` from the in-memory `IndexInfo` (in-memory value set).
- Router ordering: `update_index()` runs before `schedule()` in each KB router; if not, stamp would precede the `update_date` bump.
- Provider sync success reads OK (not STALE); provider error and "Started" paths keep current behaviour.
- Existing `TestReindexActorStampsTriggeredAt` stays green (`assert_called_once_with(index_id)` still valid; return value ignored).

---

### Task 1: Stamp helper returns the timestamp; drop misleading success-path assignment

**Files:**
- Modify: `src/codemie/rest_api/models/index.py:1125-1135`
- Modify: `src/codemie/datasource/base_datasource_processor.py:224`
- Test: `tests/codemie/rest_api/models/test_index_info.py`, `tests/codemie/datasource/test_base_datasource_processor.py`

**Interfaces:**
- Produces: `IndexInfo.stamp_reindex_triggered_at(cls, index_id: str) -> datetime` (same value written to the DB).

- [ ] **Step 1: Write failing tests** — `test_stamp_reindex_triggered_at_returns_written_timestamp` (patch `Session`/engine per neighbouring tests; assert returned value equals the `last_reindex_triggered_at` bound in the statement and `update_date` is not in the statement values); `test_process_success_does_not_assign_last_reindex_triggered_at` (run `process()` with an index mock whose `last_reindex_triggered_at` is a sentinel; assert it is unchanged after success).
- [ ] **Step 2: Run** `pytest tests/codemie/rest_api/models/test_index_info.py tests/codemie/datasource/test_base_datasource_processor.py -k "stamp or last_reindex" -v` — expect FAIL.
- [ ] **Step 3: Implement** — in `stamp_reindex_triggered_at` compute `ts = datetime.now()` once, use it in `.values(...)`, `return ts`, update the `-> None` annotation and docstring. Delete only the L224 line (`self.index.last_reindex_triggered_at = datetime.now()`); keep the `datetime` import if L244/L260 still use it.
- [ ] **Step 4: Run** the same command plus `pytest tests/codemie/triggers/actors/test_actor_datasource.py -k StampsTriggeredAt -v` — expect PASS.
- [ ] **Step 5: Commit.**

Test-first: yes — failing tests assert the helper returns the stamped datetime and that `process()` success no longer assigns the field.

### Task 2: Stamp in `BaseDatasourceProcessor.schedule()` when `func` is passed

**Files:**
- Modify: `src/codemie/datasource/base_datasource_processor.py:118-133`
- Verify only (edit if order is wrong): `src/codemie/rest_api/routers/index/kb_atlassian.py`, `kb_google.py`, `kb_git_faq.py`, `kb_xwiki.py`, `kb_sharepoint.py`, `kb_azure_devops.py`, `src/codemie/use_cases/datasource/update_file_datasource_use_case.py:119`
- Test: `tests/codemie/datasource/test_base_datasource_processor.py`

**Interfaces:**
- Consumes: Task 1 `stamp_reindex_triggered_at(...) -> datetime`.

- [ ] **Step 1: Write failing tests** — `test_schedule_with_func_stamps_once`: processor with `index` set, `patch.object(IndexInfo, "stamp_reindex_triggered_at", return_value=ts)`; call `schedule(bg, func=processor.reprocess)`; assert `assert_called_once_with(index.id)`, `index.last_reindex_triggered_at == ts`, and `bg.add_task` still called with `(datasource_concurrency_manager.run, func, index)`. `test_schedule_without_func_does_not_stamp`: `schedule(bg)` on a processor with `index=None` (creation) -> stamp not called.
- [ ] **Step 2: Run** `pytest tests/codemie/datasource/test_base_datasource_processor.py -k schedule -v` — expect FAIL.
- [ ] **Step 3: Implement** — in `schedule()`, after the `_init_index()` block, `if func is not None: self.index.last_reindex_triggered_at = IndexInfo.stamp_reindex_triggered_at(self.index.id)`. Import `IndexInfo` as the module already does.
- [ ] **Step 4: Verify ordering** — in each file listed above confirm any `update_index()` / full save happens before `schedule(...)`; fix the call order only if it is not.
- [ ] **Step 5: Run** the schedule tests plus `pytest tests/codemie/rest_api/routers -k "index" -q` — expect PASS. Commit.

Test-first: yes — `schedule(bg, func=...)` must stamp once and set the in-memory value; `schedule(bg)` must not stamp.

### Task 3: Stamp in the code and SVN reindex closures

**Files:**
- Modify: `src/codemie/datasource/code/code_datasource_processor.py:218-234` (`process()` closure in `update_code_datasource_in_background`)
- Modify: `src/codemie/datasource/svn/svn_index_service.py:79-95` (`process()` closure in `SVNIndexService.reindex`)
- Test: `tests/codemie/datasource/code/test_background_processing.py`, new/existing SVN service test (`tests/codemie/datasource/svn/`), `tests/codemie/rest_api/routers/test_index_git_validation.py` if it already drives SVN reindex

- [ ] **Step 1: Write failing tests** — for each closure (capture the function passed to the patched `run_in_background` and call it): `test_*_reindex_stamps_trigger_time_once` asserts `stamp_reindex_triggered_at` called once with the loaded `index.id`, the stamp happens before `create_processor`/`resume`/`reprocess` (use a shared `Mock` manager for call order), and `index.last_reindex_triggered_at` equals the returned value passed into `create_processor(index=...)`. Cover both `resume_indexing=True/False`.
- [ ] **Step 2: Run** `pytest tests/codemie/datasource/code/test_background_processing.py tests/codemie/datasource/svn -k stamp -v` — expect FAIL.
- [ ] **Step 3: Implement** — in each closure, right after loading `index` from `filter_by_project_and_repo(...)[0]`, add `index.last_reindex_triggered_at = IndexInfo.stamp_reindex_triggered_at(index.id)` (SVN uses its local `_IndexInfo` alias).
- [ ] **Step 4: Run** the same command plus `pytest tests/codemie/rest_api/routers/test_index_git_validation.py -q` — expect PASS. Commit.

Test-first: yes — closure must stamp once, before processor work, and carry the value on the in-memory index.

### Task 4: Stamp in `ProviderDatasourceReindexService.run` and complete synchronous reindexes

**Files:**
- Modify: `src/codemie/service/provider/datasource/provider_datasource_reindex_service.py:53-88`
- Test (new): `tests/codemie/service/provider/datasource/test_provider_datasource_reindex_service.py`

**Interfaces:**
- Consumes: Task 1 `stamp_reindex_triggered_at(...) -> datetime`.

- [ ] **Step 1: Write failing tests** (patch `Provider.get_by_id`, `ProviderDatasourceSchemaService`, `ProviderDatasourceAdapter`, `ProviderDatasourceUpdateService`, `IndexInfo.stamp_reindex_triggered_at`; datasource is `MagicMock(spec=IndexInfo)`):
  - `test_run_stamps_after_clear_error_and_before_adapter_reindex` (call order via shared manager mock; stamp called once with `datasource.id`; `datasource.last_reindex_triggered_at == ts`).
  - `test_run_with_values_stamps_after_update_service` (update service runs first); `test_run_update_failure_does_not_stamp` (update returns falsy -> early return, no stamp).
  - `test_run_sync_success_completes_progress` (response status not "Started", no errors -> `datasource.complete_progress` called once).
  - `test_run_async_started_starts_progress_and_does_not_complete` (`start_progress` called, `complete_progress` not).
  - `test_run_response_errors_calls_handle_error_no_complete` and `test_run_adapter_exception_calls_handle_error` (still stamped, no `complete_progress`).
- [ ] **Step 2: Run** `pytest tests/codemie/service/provider/datasource/test_provider_datasource_reindex_service.py -v` — expect FAIL.
- [ ] **Step 3: Implement** — in `run()`, after the optional update-service block and inside/before the `try`, set `self.datasource.last_reindex_triggered_at = IndexInfo.stamp_reindex_triggered_at(self.datasource.id)` before building the adapter. After the existing `start_progress`/`errors` handling, add: when `response.status != "Started"` and no `response.errors`, call `self.datasource.complete_progress()`. Keep `_handle_error` paths unchanged.
- [ ] **Step 4: Run** the new file plus the other `tests/codemie/service/provider/datasource/` tests and `pytest tests/codemie/triggers -k webhook -q` — expect PASS. Commit.

Test-first: yes — new service tests assert stamp ordering, sync completion, async start, and error paths.

### Task 5: Health-mixin staleness interaction tests

**Files:**
- Test: `tests/codemie/agents/tools/kb/test_search_kb.py`, `tests/codemie/agents/tools/code/test_code_tools_health.py`
- No source change expected (`src/codemie/agents/tools/datasource_health_mixin.py:27` already encodes the rule).

- [ ] **Step 1: Write tests** in each file using explicit field values (existing style): stamped `last_reindex_triggered_at` with `completed=False` -> REINDEXING; with `error=True` -> FAILED; stamped later than `update_date`, `completed=True` -> STALE; `update_date` later than the stamp after success (`completed=True`, `error=False`) -> OK.
- [ ] **Step 2: Run** `pytest tests/codemie/agents/tools/kb/test_search_kb.py tests/codemie/agents/tools/code/test_code_tools_health.py -v` — expect PASS (characterisation of the unchanged contract; if any case fails, report it rather than changing the mixin).
- [ ] **Step 3: Commit.**

Test-first: no — these pin existing mixin behaviour for the new stamping flow; no production code changes.

---

negative-constraints: honoured — no actor/scheduler edits (Tasks 1-4 touch none); no stamp inside `process()`/`reprocess()`/`resume()`/`incremental_reindex()` (Tasks 2-3 stamp at the entry point only; Task 1 only deletes L224); L244/L260, `set_error`, `complete_progress` untouched (Task 1); creation via `schedule()` not stamped (Task 2); trigger time kept, not completion time (all tasks); no schema/UI/new abstraction (helper change is a return value only).
