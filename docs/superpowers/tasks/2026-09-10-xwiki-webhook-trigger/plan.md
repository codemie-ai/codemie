# Plan — EPMCDME-14794: xWiki webhook trigger (backend)

## Goal

Allow xWiki datasources to be triggered by webhook. A webhook event runs a full
delete-and-rebuild reindex via the existing `reindex_xwiki` actor (already
`@with_datasource_job_lock`-guarded), consistent with Confluence. Backend only — no UI change.

## Requirements

- Backend `codemie` repo only.
- Consistent full-reprocess behaviour; no debouncing / incremental refresh; do not change the
  actor, cron path, or other datasource types.
- xWiki requires `setting_id` — must NOT go into `DATASOURCE_WITHOUT_SETTING_ID`.
- Out of scope (mention in MR, do not fix): Xray / SharePoint / Azure DevOps allowed by
  `validate_datasource` but not dispatched by `handle_datasource`.
- Commits: `EPMCDME-14794: ...`. Tests run with `LC_ALL=en_US.UTF-8`.

## Tasks

### Task 1 — Validator accepts xWiki webhook resource
Test-first: yes — (a) invert `tests/codemie/rest_api/test_xwiki_index_endpoints.py::test_xwiki_has_no_webhook_support`
to assert `"knowledge_base_xwiki" not in UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES` (rename to
`test_xwiki_supports_webhook`); (b) add a test that `validate_datasource_type_for_webhook` does NOT
raise for an xWiki datasource. Show RED.
Fix: in `settings_request_validator.py` remove `"knowledge_base_xwiki"` from
`UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES` and drop `xwiki` from the help message. Show GREEN.

### Task 2 — `validate_datasource` returns the xWiki datasource; missing setting_id raises
Test-first: yes — in `tests/codemie/triggers/bindings/test_cron.py` (validate_datasource section)
add: (a) `test_validate_datasource_xwiki_type_is_supported` — xWiki ds with `setting_id` is
returned; (b) `test_validate_datasource_xwiki_without_setting_id_raises` — xWiki ds with
`setting_id=None` raises `DatasourceNotValidated`. Show RED.
Fix: in `triggers/bindings/utils.py` add `FullDatasourceTypes.XWIKI` to the `validate_datasource`
allow-list. Do NOT touch `DATASOURCE_WITHOUT_SETTING_ID`. Show GREEN.

### Task 3 — `handle_datasource` schedules `reindex_xwiki` with the expected payload
Test-first: yes — in `tests/codemie/triggers/bindings/test_webhook.py` add a test that, for an
xWiki datasource, `handle_datasource` calls `background_tasks.add_task(reindex_xwiki, <payload>)`
where the payload is an `XWikiReindexTask` carrying `resource_id`, `project_name`, `resource_name`,
`index_info`, `xwiki_index_info`. Show RED.
Fix: in `triggers/bindings/webhook.py` import `reindex_xwiki` and `XWikiReindexTask`, and add an
`elif index_type == FullDatasourceTypes.XWIKI:` branch mirroring `cron.py:821-836`
(resource_id = webhook resource_id), dispatching via `background_tasks.add_task(reindex_xwiki, payload)`.
Show GREEN.

## Validation
- Run the four affected test files with `LC_ALL=en_US.UTF-8`.
- Run Sonar (quality gate that blocks the merge) or explicitly state it was skipped.
- Do not push or open an MR until the user says so.
