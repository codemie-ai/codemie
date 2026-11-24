# EPMCDME-9989 ADO health checks honour the selected integration (setting_id)

**Goal:** `health_check_azure_devops_wiki` and `health_check_azure_devops_work_item` use the integration the user selected, and report a descriptive error if it cannot be used, instead of silently checking another one.

Commit per task using the repository's existing convention.

## Acceptance criteria
- An explicit `setting_id` is the integration that gets checked.
- No fallback to another integration when an id is given.
- An unusable selected id returns `ErrorMessage(field_error="setting_id")`, not a generic 422.
- Requests without `setting_id` behave as before.
- Other health checks (Jira, Xray, Confluence, XWiki, Git) are unchanged.

## Assumptions and findings
- Root cause: both ADO handlers omit `setting_id` (`src/codemie/service/index/datasource_health_check_service.py:160-164`, `:259-263`). `get_azure_devops_creds` already accepts `setting_id` (`settings.py:1546`).
- The settings chain (`settings_handler.py:235-248`) does fall back. `BySettingIDSettingsHandler` (`:125-131`) passes to later handlers when the id is not found or its `credential_type` differs. Forwarding the id alone therefore does not satisfy "no fallback".
- `get_azure_devops_creds` never returns None, and its contract stays unchanged for other callers. The guard goes in the health service, using `search_settings_by_id` from `codemie.service.settings.settings_util`.
- No API or schema change, so the OpenAPI baseline is untouched. Frontend is out of scope.
- negative-constraints: "no fallback to first available" is honoured by Task 1's guard; "do not change `get_azure_devops_creds` contract" is honoured because no edit is made there.

### Task 1: Forward setting_id and guard the explicit id in both ADO handlers
- Modify `datasource_health_check_service.py:160-164` and `:259-263`. Pass `setting_id=request.setting_id` to `get_azure_devops_creds`.
- Add one private classmethod, `_unresolved_ado_setting_error(request) -> DatasourceHealthCheckResponse | None`, and call it first in both handlers.
  - It returns None when `request.setting_id` is falsy.
  - Otherwise it calls `search_settings_by_id(request.setting_id)`. If the result is None or its `credential_type != CredentialTypes.AZURE_DEVOPS`, it returns a response with an `ErrorMessage` modelled on the XWiki one (`:198-212`): message "Selected Azure DevOps integration was not found", `field_error="setting_id"`, and details/help text pointing to the Integrations tab.
  - Find the `CredentialTypes` import path via the existing import in `settings.py`.
- Test-first: yes — with `setting_id="s1"` and the get_creds mock patched, assert `get_azure_devops_creds.call_args.kwargs["setting_id"] == "s1"` for wiki and work item; this fails today because the kwarg is missing.

### Task 2: Tests in `tests/codemie/service/index/test_azure_devops_health_check.py`
- Model on `tests/codemie/service/index/test_xwiki_health_check.py` (`_SERVICE` patch prefix, `test_the_selected_integration_is_the_one_checked`). Patch `SettingsService.get_azure_devops_creds`, `search_settings_by_id` and the two processors in the service module.
- Cases, parametrised over wiki and work item:
  - The selected id is forwarded.
  - An unknown id returns `field_error == "setting_id"`, and neither creds lookup nor processor is called.
  - A setting of the wrong credential type returns the same error.
  - No `setting_id` skips the lookup and calls creds with `setting_id=None` (no regression).
- Test-first: yes — the unknown-id case fails before Task 1 because the processor is built from the fallback creds; write it first, run it red, then pass it with Task 1's code. If Task 1 is already merged, run the tests against the reverted handler to confirm they fail.
