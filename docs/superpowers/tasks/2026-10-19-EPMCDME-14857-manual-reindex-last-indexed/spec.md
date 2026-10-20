# EPMCDME-14857: Manual reindex must update "Last indexed"

## Problem
The UI shows "Last indexed" from `last_reindex_triggered_at`. Scheduled reindexes stamp it at trigger time
via `IndexInfo.stamp_reindex_triggered_at` (`rest_api/models/index.py:1125`, targeted UPDATE, does not bump
`update_date`; first statement of every actor in `triggers/actors/datasource.py`). Manual reindexes never
stamp it, so the value stays outdated. The only manual assignment is in memory
(`datasource/base_datasource_processor.py:224`) and is lost because `complete_progress` persists via
`update_progress`, which writes only the fields it is given.

## Goals
1. Every manual reindex (full, incremental, resume) of every datasource type persists
   `last_reindex_triggered_at` at trigger time, without bumping `update_date`.
2. The health mixin (`agents/tools/datasource_health_mixin.py:27`, stale when
   `last_reindex_triggered_at > update_date`) keeps its meaning: stale/failed/reindexing while a run is
   in flight or after failure, not stale after success.
3. The misleading in-memory success-path assignment is removed.

## Design (what, not how)
Stamp once per manual entry point, following the actor pattern. Do not stamp inside `process()`,
`reprocess()`, `resume()` or `incremental_reindex()`: actors already stamp and then call them.

Entry points:
- `BaseDatasourceProcessor.schedule()` when a `func` (reprocess/resume/incremental_reindex) is passed.
  This covers the Atlassian, Google, Git FAQ, XWiki, SharePoint and Azure DevOps routers and the
  file-KB update (`use_cases/datasource/update_file_datasource_use_case.py:119`). A plain
  `schedule(background_tasks)` is index creation and is NOT stamped.
- The `process()` closure in `update_code_datasource_in_background`
  (`datasource/code/code_datasource_processor.py:206`) and in `SVNIndexService.reindex`
  (`datasource/svn/svn_index_service.py:55`), neither of which uses `schedule()`.
- `ProviderDatasourceReindexService.run`
  (`service/provider/datasource/provider_datasource_reindex_service.py:53`), which uses no processor.

Constraints:
- Ordering: stamp after `update_index()` and after the full `save()` in `_clear_error`, and after the
  provider update step. Both bump `update_date`; stamping earlier makes the row stale from the start.
- No clobbering: `set_queued`, `clear_queued` and `SharePointDatasourceProcessor._on_process_end`
  (`:330`) do a full `update()` from the in-memory `IndexInfo`, which would overwrite the stamp with an
  old value. The stamped value must therefore also be set on the in-memory `IndexInfo` the processor
  uses (for example by having the helper return the timestamp; `None` to `datetime` is backward
  compatible).
- Provider datasources: always stamp before the external call. If the reindex is accepted
  asynchronously ("Started"), existing behaviour stands (`start_progress` and the callback bump
  `update_date`). If it completes synchronously without errors, persist completion (for example
  `complete_progress`) so `update_date` moves past the stamp. Errors keep going through
  `_handle_error`. Because the same service runs from `triggers/bindings/webhook.py:562`,
  webhook-triggered provider reindexes are stamped too (consistent with other scheduled runs).
- Remove only the assignment at `base_datasource_processor.py:224`. Leave L244 and L260: on SharePoint
  they are persisted on failure by the later full update, so removing them would change failed-run
  behaviour (out of scope).

## Acceptance criteria
1. A manual reindex through each entry point above calls `IndexInfo.stamp_reindex_triggered_at` exactly
   once with the index id, and the value is returned by the API after reload.
2. Stamping does not change `update_date`.
3. A creation (not reindex) through `schedule()` does not stamp.
4. Health mixin: after stamp with a running or failed state the index reads REINDEXING/FAILED/STALE as
   for scheduled runs; after a successful run (including a synchronous provider reindex) it reads OK.
   Tests in `tests/codemie/agents/tools/kb/test_search_kb.py` and
   `tests/codemie/agents/tools/code/test_code_tools_health.py`.
5. `TestReindexActorStampsTriggeredAt` and other existing actor and processor tests still pass unchanged.
6. New tests for: `schedule()` with and without `func`; the code and SVN closures; the provider reindex
   service (none exists today), covering sync success, async start and error; and removal of L224.

## Non-goals
- Changing scheduler or actor logic (EPMCDME-10937 is separate); actors keep stamping as-is.
- Switching the value to completion time.
- Whether failed or guardrail-blocked runs should move "Last indexed"; L244/L260 and `set_error` untouched.
- UI changes, schema or migration changes, new API fields.
- Making `set_error` or `complete_progress` persist `last_reindex_triggered_at`.
- A new shared reindex abstraction beyond what the entry points above need.
