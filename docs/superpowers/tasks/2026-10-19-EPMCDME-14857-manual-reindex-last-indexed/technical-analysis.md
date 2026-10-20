# Technical Research

**Task**: datasource reindex index_info last_reindex_triggered_at
**Generated**: 2026-10-19
**Research path**: codegraph

---

## 1. Original Context

EPMCDME-14857 — Manual datasource reindex does not update the "Last indexed" timestamp

Repo: codemie/ (backend only — UI needs no change)

## Problem
After a manual (full) reindex of any datasource, the "Last indexed" value in the datasource details stays outdated. Datasources reindexed by the cron scheduler are not affected. The UI renders "Last indexed" straight from the API field `last_reindex_triggered_at`. The bug is that this field is not persisted for manual reindexes.

## Root cause (already analysed, verify before changing)
1. Scheduled reindexes stamp the field at trigger time: every reindex actor in `src/codemie/triggers/actors/datasource.py` (lines ~84, 133, 185, 245, 323, 381, 441, 518, 587, 641, 728, 1129) calls `IndexInfo.stamp_reindex_triggered_at(index_id)` (`src/codemie/rest_api/models/index.py:1125`) — a targeted SQL UPDATE that does NOT bump `update_date`.
2. Manual reindexes from the REST routers (`src/codemie/rest_api/routers/index/` — `code_repositories.py`, `_repository_support.py`, `kb_atlassian.py`, `kb_google.py`, `kb_git_faq.py`, `kb_xwiki.py`, `kb_sharepoint.py`, `kb_azure_devops.py`, `providers.py`, plus any service they delegate to, e.g. `datasource/svn/svn_index_service.py`, `service/provider/datasource/provider_datasource_reindex_service.py`) never call `stamp_reindex_triggered_at`.
3. The success path in `src/codemie/datasource/base_datasource_processor.py:224` sets `self.index.last_reindex_triggered_at = datetime.now()` only in memory; the following `complete_progress()` (`index.py:646`) persists via `update_progress(ProgressUpdate(...))`, which writes only the fields passed in `ProgressUpdate` — `last_reindex_triggered_at` is not passed, so the value is lost. (Error paths at lines 244/260 do persist it because `set_error()` does a full `self.update()`.)

## Decisions (already made by the product owner — do not re-open)
1. Manual reindex must capture `last_reindex_triggered_at` the same way scheduled runs do — stamp at trigger time via `IndexInfo.stamp_reindex_triggered_at()`. Do not switch to completion time. Resolve the misleading in-memory-only assignment on the success path (base_datasource_processor.py:224) so the code is consistent with that decision.
2. Staleness check must keep working (IN SCOPE): `src/codemie/agents/tools/datasource_health_mixin.py:27` flags an index as stale when `last_reindex_triggered_at > update_date`. After the fix: while a manual reindex is running, or if it failed, the index is reported stale/unhealthy exactly as for scheduled runs; after a successful manual reindex, the index is NOT reported stale (`update_date` set on completion must be later than the trigger stamp). Cover both with tests (see `tests/codemie/agents/tools/kb/test_search_kb.py`, `tests/codemie/agents/tools/code/test_code_tools_health.py`).
3. OUT OF SCOPE: whether failed / guardrail-blocked runs should move "Last indexed". Keep current behaviour.
4. Verification applies to manual reindex of ANY datasource type (code/git, SVN, Confluence/Jira, Google, Git FAQ, XWiki, SharePoint, Azure DevOps, provider datasources), full and incremental where available.
5. EPMCDME-10937 (scheduler jobs not running) is a different issue — do not touch scheduler execution logic.

## Acceptance criteria
1. A manual reindex of any datasource type persists `last_reindex_triggered_at`, stamped at trigger time, matching the scheduled-run behaviour.
2. Refreshing / reopening the datasource returns the new value from the API (persisted, not in-memory).
3. Stamping does not bump `update_date`; after a successful reindex the datasource is not reported stale by the health mixin; during/after a failed reindex it is.
4. Scheduled reindex behaviour and existing tests (e.g. `TestReindexActorStampsTriggeredAt` in `tests/codemie/triggers/actors/test_actor_datasource.py`) are not regressed.
5. Unit tests cover the manual stamping for the router/service entry points and the staleness interaction.

## Hints
- Prefer one shared place for the manual stamp (a common helper/service every manual reindex goes through) over copy-pasting into each router, if such a place exists; otherwise follow the actor pattern.
- Mind the scheduler path: if the shared place is also used by actors, do not stamp twice in a way that changes semantics.

(Source file: `/home/aliaksei_chumakou/codemie-dev/EPMCDME-14857_prompt_backend.md`)

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/rest_api/models/index.py` — `IndexInfo` (SQLModel table). `stamp_reindex_triggered_at(cls, index_id)` (L1125): raw `sa_update(...).values(last_reindex_triggered_at=datetime.now())`, own Session, does not touch `update_date`. `_build_progress_update_kwargs` (L1165) **always** adds `update_date = datetime.now()` to every `update_progress` call, and writes `last_reindex_triggered_at` only if `ProgressUpdate.last_reindex_triggered_at` is set (field exists, L1190). `complete_progress` (L646) and `set_error` (L671) both go through `update_progress` and pass no `last_reindex_triggered_at`. `update_index` (L887) calls `self.update()` (full save, bumps `update_date`) and snapshots `last_reindex_date`.
- `src/codemie/datasource/base_datasource_processor.py` — `BaseDatasourceProcessor`. Public entry points all funnel into `process()` (L143): `reprocess()` (L383, `_cleanup_data` then `process`), `resume()` (L403), `incremental_reindex()` (L426), and `schedule(background_tasks, func=None)` (L118, calls `_init_index` if `self.index is None`, then queues `datasource_concurrency_manager.run(target, self.index)`). In-memory-only assignments `self.index.last_reindex_triggered_at = datetime.now()` at L224 (success), L244 (guardrail-blocked), L260 (generic error).
- `src/codemie/triggers/actors/datasource.py` — scheduled/webhook actors `reindex_code/svn/jira/confluence/google/git_faq/azure_devops_wiki/azure_devops_work_item/xray/sharepoint/xwiki` (all `@with_datasource_job_lock`) each call `IndexInfo.stamp_reindex_triggered_at(payload.index_info.id)` as first statement, then build processor and call `datasource_concurrency_manager.run(processor.reprocess | incremental_reindex, processor.index)`. `resume_stale_datasource` (L1116) stamps at L1129. Actors construct processors directly with `index=`/`index_info=`; they do NOT go through `schedule()`.
- Manual entry points (all start a processor method in a background task):
  - Code/git: `routers/index/code_repositories.py::update_index_application` (L313) -> `datasource/code/code_datasource_processor.py::update_code_datasource_in_background` (L206; inner `process()` loads IndexInfo, `create_processor`, then `resume()` or `reprocess()`; always `reprocess` regardless of `full_reindex` flag) -> `run_in_background` (L163).
  - SVN: `_repository_support.py::_handle_svn_reindex` (L189) -> `datasource/svn/svn_index_service.py::SVNIndexService.reindex` (L55; same inner-`process()` pattern) -> `run_in_background`.
  - Confluence/Jira/Xray: `routers/index/kb_atlassian.py` (e.g. `update_knowledge_base_confluence` L199) builds processor with `index=kb_index[0]`, then `datasource_processor.schedule(background_tasks, processor.resume | reprocess)`. `schedule()` has 22 callers across `kb_atlassian.py`, `kb_azure_devops.py`, `kb_files.py` and 4 more files (not individually opened: `kb_google/kb_git_faq/kb_xwiki/kb_sharepoint` presumably follow the same pattern; verify).
  - Provider: `routers/index/providers.py::reindex_provider_datasource_index` (L86) -> `background_tasks.add_task(ProviderDatasourceReindexService.run)` (`service/provider/datasource/provider_datasource_reindex_service.py`). This does NOT use `BaseDatasourceProcessor` at all: it calls `ProviderDatasourceAdapter.reindex` (HTTP tool invocation to external provider), `start_progress` if async; completion arrives via provider callback endpoint (`/v1/callbacks/index/{id}`, not inspected). Same service is also invoked from `triggers/bindings/webhook.py::handle_datasource`.
  - File KB: `datasource/file/file_datasource_update_processor.py` overrides `process()` and never sets `last_reindex_triggered_at` (not in the ticket's type list).
- `src/codemie/agents/tools/datasource_health_mixin.py::_classify_health`: order is `error -> FAILED`, `not completed -> REINDEXING`, `last_reindex_triggered_at > update_date -> STALE`, else `OK`. Used by `SearchKBTool`, code tools (`code/tools.py`, `read_files_tools.py`, `code_toolkit.py`).

### Architecture and Layers Affected
- REST routers (`rest_api/routers/index/*`) -> datasource service helpers (`datasource/code/code_datasource_processor.py`, `datasource/svn/svn_index_service.py`) -> processors (`datasource/base_datasource_processor.py` + per-type subclasses) -> model/persistence (`rest_api/models/index.py::IndexInfo`).
- Parallel path: trigger layer (`triggers/actors/datasource.py`, `bindings/cron.py`, `bindings/webhook.py`) -> processors.
- Parallel path: provider service layer (`service/provider/datasource/*`).
- Consumer: agent tools layer (`agents/tools/datasource_health_mixin.py`).

### Integration Points
- `datasource_concurrency_manager.run` (`datasource/datasource_concurrency_manager.py`) wraps every reindex in both paths; on queue timeout it calls `index_info.set_error`.
- `update_progress` bumps `update_date` on every progress write during a run, so during a run `update_date` is always later than a trigger-time stamp (health reads REINDEXING while `completed=False`, FAILED on `error`).
- `IndexInfo.filter_by_project_and_repo` re-reads the row inside the background tasks of code/SVN (fresh object).

### Patterns and Conventions
- Actor pattern: first line of each actor is `IndexInfo.stamp_reindex_triggered_at(payload.index_info.id)`, before any validation.
- Targeted SQL updates (`sa_update`) are the project convention for fields that must not clobber user edits or bump `update_date` (`try_claim_for_resume`, `stamp_reindex_triggered_at`, `update_progress`).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/` exists (47 files). Relevant: `data/database-patterns.md`, `data/repository-patterns.md`, `architecture/service-layer-patterns.md`, `testing/testing-patterns.md`, `testing/testing-service-patterns.md`, `testing/testing-api-patterns.md`, `quality-gates.md`. Not opened by this research.

### Architectural Decisions
- Inline docstrings: `stamp_reindex_triggered_at` states STALE detection relies on `last_reindex_triggered_at > update_date`; `update_progress` cites EPMCDME-10036 for the targeted-update pattern.

### Derived Conventions
- Background reindex runs in FastAPI `BackgroundTasks`; DB-visible state for queued/running is written by the processor/manager, not the router.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/triggers/actors/test_actor_datasource.py` — `TestReindexActorStampsTriggeredAt` (L802): patches `IndexInfo.stamp_reindex_triggered_at`, asserts `assert_called_once_with(index_id)` and `index_info.update.assert_not_called()` for code, jira, confluence, google, azure wiki/work item, resume.
- `tests/codemie/triggers/test_xwiki_reindex.py` — xwiki actor.
- `tests/codemie/datasource/test_base_datasource_processor.py` — base `process`.
- `tests/codemie/datasource/code/test_background_processing.py` — `run_in_background`, `update_code_datasource_in_background` (resume/reprocess).
- `tests/codemie/rest_api/routers/test_index_git_validation.py` (code and SVN reindex via `_handle_svn_reindex`), `test_index.py`, `test_index_git_faq.py`, `tests/codemie/rest_api/test_xwiki_index_endpoints.py`.
- `tests/codemie/rest_api/models/test_index_info.py`, `test_index_health_fields.py` — model behaviour.
- `tests/codemie/service/provider/datasource/test_provider_datasource_adapter.py` (+ creation/update/base service tests); no dedicated reindex-service test found in the glob.
- Health mixin: `tests/codemie/agents/tools/kb/test_search_kb.py`, `tests/codemie/agents/tools/code/test_code_tools_health.py`.
- Per-processor tests: jira, sharepoint, azure_devops wiki/work_item, google_doc under `tests/codemie/datasource/`.

### Testing Framework and Patterns
- pytest; `MagicMock(spec=IndexInfo)` and `patch.object(IndexInfo, "stamp_reindex_triggered_at")` are the established mock style.

### Coverage Gaps
- No test asserts that a manual (router/service) reindex stamps the field.
- No test of `complete_progress`/`process` persisting or not persisting `last_reindex_triggered_at`.
- No dedicated test for `ProviderDatasourceReindexService.run` found.

---

## 5. Configuration and Environment

### Environment Variables
- None specific to this feature found. Adjacent: `MAX_CONCURRENT_DATASOURCE_INDEXING`, `DATASOURCE_CONCURRENCY_LIMIT_ENABLED`, `DATASOURCE_QUEUE_TIMEOUT` (concurrency manager).

### Configuration Files
- No feature config. DB column `last_reindex_triggered_at` already exists on `IndexInfo` and `ProgressUpdate`.

### Feature Flags and Deployment Concerns
- None found.

---

## 6. Risk Indicators

- Task premise discrepancy (verified in source): `IndexInfo.set_error` (index.py:671) uses `update_progress(ProgressUpdate(error, text, ...))`, NOT a full `self.update()`. So the in-memory assignments at base_datasource_processor.py L244 and L260 are also lost today, not only L224. Decision 3 says keep failed/guardrail behaviour; the actual current persisted behaviour of those paths is "not persisted".
- Ordering hazard: `update_index()` (called by routers before queuing) does a full `update()` that bumps `update_date`. Any manual stamp must occur after that call, otherwise `last_reindex_triggered_at > update_date` is false from the start. Stamp inside `process`/`schedule`/background task is after; stamp in a router before `update_index` would be before.
- Double-stamp hazard: `BaseDatasourceProcessor.process()` is the common sink for both manual and actor paths (actors stamp first, then call `reprocess`/`incremental_reindex`). A stamp placed in `process()` would stamp twice on scheduled runs (benign for the value, but changes trigger time to a later instant and would break `assert_called_once` style expectations if actors' tests share the mock). `schedule()` is only used by manual routers (actors don't call it), so it is a natural manual-only place but misses code/SVN (which use `run_in_background`, not `schedule`).
- Manual entry points are not uniform: (a) `schedule()` (KB routers), (b) `run_in_background` inner closures (code, SVN), (c) provider service (no processor), (d) file KB update processor (own `process()`; outside ticket list). No single existing choke point covers all of a-c.
- Provider datasources: reindex may complete asynchronously via callback; stamp must be before the external call. `update_date` handling on callback not inspected.
- Staleness semantics: during a run `update_progress` keeps bumping `update_date`, so STALE in practice only appears when a run is triggered (stamped) but never starts/updates; REINDEXING/FAILED are reported through `completed`/`error`. Tests for "stale while running" at the mixin level need explicit field values rather than a real run.
- Timestamp resolution: stamp (`datetime.now()`) vs `complete_progress` `update_date` (`datetime.now()`) are both app-clock naive datetimes; success ordering holds but equal-microsecond ties are theoretically possible.
- Speculative: removing L224 (and arguably L244/L260) leaves no in-memory value; a stale in-memory `last_reindex_triggered_at` on a later `self.index.update()` full save could overwrite a newer DB stamp (full-object update path in `update_index`/`start_progress` -> `save`). Worth checking which full saves occur after a stamp.
- Speculative: kb_google/kb_git_faq/kb_xwiki/kb_sharepoint/kb_azure_devops routers were not individually opened; confirm they use `schedule()`.

---

## 7. Summary for Complexity Assessment

The bug sits across three layers: REST routers/service helpers (manual triggers), `BaseDatasourceProcessor` (the shared processing sink with a misleading in-memory assignment at L224/244/260), and `IndexInfo` (persistence helpers, already containing the targeted `stamp_reindex_triggered_at`). No new schema or API surface exists to add: the column, `ProgressUpdate` field and stamp helper are all present. The change surface is small in code but spread over several non-uniform manual entry points: `BaseDatasourceProcessor.schedule()` (KB routers), `update_code_datasource_in_background` and `SVNIndexService.reindex` (code/SVN closures), and `ProviderDatasourceReindexService.run` (no processor).

Technical novelty is low: the actor pattern (stamp first, no `update_date` bump) is established and tested. The subtle parts are placement (after `update_index`'s full save, before processing; avoiding double stamping on the actor path that also reaches `process()`) and the staleness interaction with `update_progress` always bumping `update_date`. The ticket's root-cause item 3 is partly wrong: `set_error` does not persist `last_reindex_triggered_at` either.

Test posture: strong existing coverage for actors, the base processor, code background processing, router validation and health mixin, with established `patch.object(IndexInfo, "stamp_reindex_triggered_at")` mock style. Gaps are exactly the new behaviour (manual stamping per entry point, provider reindex service, success/failure staleness through the mixin). Key risks: non-uniform entry points, ordering vs `update_index`, double stamping, provider async path.

---

## 8. External References

Resolved: `/home/aliaksei_chumakou/codemie-dev/EPMCDME-14857_prompt_backend.md` is the source of the task text reproduced in Section 1 (the caller supplied its content verbatim; the file itself was not re-opened). Facts verified against source: stamp helper at index.py:1125 (no `update_date` bump) - confirmed; actors stamp at trigger time - confirmed; L224 in-memory only - confirmed; claim that `set_error` does a full `update()` - NOT confirmed (it uses `update_progress`, index.py:671-676).
