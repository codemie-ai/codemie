# Technical Analysis — EPMCDME-14794: xWiki webhook trigger

## Summary

Enable webhook triggering for xWiki datasources (backend only). Today the backend rejects an
xWiki webhook setting with 422 at save time and would refuse to dispatch it at invocation time.
Three production files and one test file change. A webhook event triggers a full
delete-and-rebuild reindex (`reindex_xwiki`, already guarded by `@with_datasource_job_lock`),
identical to Confluence / Azure DevOps Wiki / SharePoint / Google.

## Codebase Findings (verified against branch off origin/main 40ec3c96e)

### Touch point 1 — `src/codemie/service/settings/settings_request_validator.py`
- Line 75: `"knowledge_base_xwiki",  # user-visible name: "xwiki"` inside
  `UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES` (lines 68–78). **Remove this entry.**
- Line 81 (help message `UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES_HELP_MESSAGE`, lines 79–83):
  `"file, sharepoint, xray, azure devops wiki, azure devops work item, xwiki. "` — **drop `, xwiki`.**
- `validate_datasource_type_for_webhook` (lines 499–518) raises 422 when
  `datasource.index_type in UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES`. Removing the entry makes it accept xWiki.

### Touch point 2 — `src/codemie/triggers/bindings/utils.py`
- `validate_datasource()` (lines 59–86) allow-list at lines 66–75 does NOT include XWIKI. **Add
  `FullDatasourceTypes.XWIKI` to that list.**
- `DATASOURCE_WITHOUT_SETTING_ID` (lines 53–56) = `[GOOGLE, PROVIDER]`. **Do NOT add XWIKI** — the
  setting_id guard at lines 78–82 then correctly raises `DatasourceNotValidated` for an xWiki
  datasource missing `setting_id`.

### Touch point 3 — `src/codemie/triggers/bindings/webhook.py`
- `handle_datasource()` (lines 523–603) dispatches by `index_type`: PROVIDER, code, JIRA,
  CONFLUENCE, GOOGLE, else `NotImplementedDatasource`. **Add an XWIKI branch** before the `else`.
- Reference implementation: `cron.py:821–836` builds
  `XWikiReindexTask(project_name, resource_id=job_id, resource_name, user, index_info, xwiki_index_info=index_info.xwiki)`
  and schedules `reindex_xwiki`. For the webhook path, `resource_id = resource_id` (the webhook
  resource_id) and dispatch via `background_tasks.add_task(reindex_xwiki, payload)`.
- **New imports needed:** `reindex_xwiki` (add to the `codemie.triggers.actors.datasource` import
  at line 30) and `XWikiReindexTask` (add to the `codemie.triggers.trigger_models` import, lines 36–41).

### Touch point 4 — `tests/codemie/rest_api/test_xwiki_index_endpoints.py`
- Lines 53–54: `test_xwiki_has_no_webhook_support` asserts
  `"knowledge_base_xwiki" in UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES`. **Invert** to assert it is NOT
  in the set (rename to reflect new behaviour).

### Supporting symbols (all verified present)
- `FullDatasourceTypes.XWIKI = "knowledge_base_xwiki"` — `src/codemie/service/constants.py:28`
- `reindex_xwiki(payload: XWikiReindexTask)` with `@with_datasource_job_lock` —
  `src/codemie/triggers/actors/datasource.py:655–656` (full delete-and-rebuild via reprocess)
- `class XWikiReindexTask(ReindexTaskPayload)` — `src/codemie/triggers/trigger_models.py:74`;
  `xwiki_index_info: Optional[XWikiIndexInfo] = None` (deliberately optional). Base requires
  `project_name, resource_id, resource_name, user, index_info`.

### Test patterns to mirror
- `validate_datasource` unit test: `tests/codemie/triggers/bindings/test_cron.py:497–518`
  (patch `codemie.triggers.bindings.utils.IndexInfo.find_by_id`, set `is_code_index()=False`,
  `index_type`, `setting_id`).
- `handle_datasource` dispatch test: `tests/codemie/triggers/bindings/test_webhook.py` — patch
  `codemie.triggers.bindings.webhook.validate_datasource`, assert `background_tasks.add_task` called.
- Validator webhook test: `tests/codemie/service/settings/test_settings_request_validator.py` uses
  `_make_datasource(index_type)` helper and `validate_datasource_type_for_*`.

## UI note (for MR / AC)
The webhook form's resource selector (codemie-ui `useResourceOptions -> getDataSourceOptions ->
GET v1/index`, no type filter) already lists every datasource of the project, so xWiki is already
selectable. The 422 came solely from the backend on save. **No UI change required.**

## Risk Indicators
- Scope: 3 production files + 1 test file. Narrow, single datasource type.
- Consistency: `reindex_xwiki` reused unchanged; `@with_datasource_job_lock` skips (does not queue)
  a concurrent run — intended behaviour, no debouncing/incremental refresh added here.
- Out of scope (mention, do not fix): Xray / SharePoint / Azure DevOps are allowed by
  `validate_datasource` but NOT dispatched by `handle_datasource` — pre-existing gap, unrelated to this ticket.
