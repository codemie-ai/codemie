# Technical Research

**Task**: azure devops wiki healthcheck integration (EPMCDME-9989)
**Generated**: 2026-10-19
**Research path**: filesystem

---

## 1. Original Context

Ticket EPMCDME-9989, Bug

Summary: Azure DevOps Wiki healthcheck endpoint returns 422 when integration/setting id is not passed; backend uses first available integration instead of UI-selected one.

Description: When triggering the healthcheck endpoint for Azure DevOps Wiki, the API returns a 422 error if no integrationId/settingId is sent. The backend takes the first available integration it finds, regardless of which integration id was explicitly chosen in the UI. The healthcheck may therefore reference the wrong Azure DevOps integration and report a false negative.

Preconditions: multiple Azure DevOps Wiki integrations exist for the user/project; integration id is selectable in the UI.
Steps: 1 configure multiple ADO Wiki integrations; 2 select a specific one in UI; 3 trigger healthcheck; 4 integration/setting id is not passed to backend; 5 healthcheck uses first available integration and may return 422.
Actual: 422, wrong integration may be used. Expected: healthcheck always uses the exact integration/setting id selected in the UI; 200 or a descriptive error referencing the user's explicit choice.
Affected: ADO Wiki integration healthcheck endpoint (backend); UI-to-backend integration handoff; integration config and diagnostics.

Acceptance Criteria:
- Healthcheck endpoint accepts and respects explicit integration id/setting id from UI.
- No fallback to "first available" integration if multiple exist.
- 422 or other errors only occur for problems with the specific, explicitly-selected integration id.
- Proper and consistent diagnostics visible to end users for the tested integration.
- No regression in other Azure DevOps Wiki or integration healthcheck features.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/rest_api/routers/index/queries.py:346` `POST /index/health` -> `IndexHealthCheckService.health_check_datasource`. Any exception is wrapped into `ExtendedHTTPException` 422 "Cannot validate provided datasource" (this is the source of the 422).
- `src/codemie/rest_api/models/index.py:1460` `DatasourceHealthCheckRequest` already has `setting_id: Optional[str]`, plus `wiki_query`, `wiki_name`, `wiql_query`. Response: `DatasourceHealthCheckResponse(implemented, documents_count, error: ErrorMessage{message,details,help,field_error})`.
- `src/codemie/service/index/datasource_health_check_service.py`:
  - `health_check_azure_devops_wiki` (line ~160): calls `SettingsService.get_azure_devops_creds(user_id, project_name)` with NO `setting_id`, then builds `AzureDevOpsWikiDatasourceProcessor(...)` and `_check_docs_health()`. Uses `hasattr(request, "wiki_query")` (always true on the model).
  - `health_check_azure_devops_work_item` (line ~259): same omission (sibling bug, not in ticket text).
  - Jira, Xray, Confluence, XWiki handlers pass `setting_id=request.setting_id`. XWiki is the reference fix: passes setting_id, returns `ErrorMessage(field_error="setting_id")` when creds resolve to None (comment explicitly documents the same bug class).
- `src/codemie/service/settings/settings.py:1546` `get_azure_devops_creds(..., setting_id=None)` already accepts `setting_id` and forwards it to `retrieve_setting(search_fields, assistant_id, setting_id)`. It never returns None: missing setting yields empty strings (base_url/token "") via `get_credential` defaults, so a bad/absent setting surfaces later as a loader error.
- `AzureDevOpsWikiLoader._validate_creds` (`src/codemie/datasource/loader/azure_devops_wiki_loader.py:154`) raises `MissingIntegrationException("AzureDevOps Wiki")` / `UnauthorizedException`; `_check_docs_health` in the processor re-raises, so they end up in the generic 422.
- Callers that do pass setting_id (reference): `src/codemie/rest_api/routers/index/kb_azure_devops.py` lines ~135, ~245 (`request.setting_id or kb_index.setting_id`). Other callers of `get_azure_devops_creds`: `triggers/actors/datasource.py:469,544`, `service/tools/toolkit_settings_service.py:154`.
- `retrieve_setting` uses `build_settings_handlers().handle(search_fields, assistant_id, setting_id)` (chain-of-handlers; not read in detail).

### Architecture and Layers Affected
Router (`queries.py`) -> service (`IndexHealthCheckService`) -> settings service (`SettingsService.get_azure_devops_creds`) -> datasource processor -> loader. Request model already carries the field.

### Integration Points
Azure DevOps SDK (`azure.devops.v7_1.wiki.WikiClient`) via loader; Settings table via settings handlers chain. UI is a separate repo (not in scope here); the backend request model already accepts `setting_id`, so whether the UI sends it is not verifiable from this repo.

### Patterns and Conventions
Per-type classmethod `health_check_<type>`; errors-as-response with `ErrorMessage.field_error` for known user errors (XWiki), generic exceptions to 422 in the router/service wrapper (`health_check_datasource` lines 66-115 map several exception types to responses; not fully read).

---

## 3. Documentation Findings

### Guides and Architecture Docs
`.ai-run/guides/` exists (api, data, integration, development, testing). Not read in depth; `integration/external-services.md` and `api/rest-api-patterns.md` are the relevant ones. No ADR specific to health checks found.

### Architectural Decisions
Inline comment in `health_check_xwiki` records the decision that setting_id must be honoured and None creds must yield a field error rather than HTTP 500.

### Derived Conventions
Follow the XWiki handler for setting_id propagation and descriptive `ErrorMessage`.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/index/test_xwiki_health_check.py`: includes `test_the_selected_integration_is_the_one_checked` asserting `get_creds.call_args.kwargs["setting_id"]` (good template); patches `f"{_SERVICE}.SettingsService.get_xwiki_creds"` and the processor.
- `tests/codemie/service/index/test_datasource_health_check_service.py`: Git health check only (`TestHealthCheckGit`); no Azure DevOps Wiki/work item cases found.
- `tests/codemie/rest_api/routers/test_index.py` and `tests/codemie/datasource/azure_devops_wiki/test_azure_devops_wiki_datasource_processor.py` exist; not checked for health coverage.

### Testing Framework and Patterns
pytest, `unittest.mock.patch` on module-level names in the service module.

### Coverage Gaps
No test for `health_check_azure_devops_wiki` or `health_check_azure_devops_work_item` forwarding setting_id.

---

## 5. Configuration and Environment

No env vars, flags or deployment config specific to this path found. Credentials come from stored Settings (type `CredentialTypes.AZURE_DEVOPS`). `tests/codemie/rest_api/router_guards/baselines/openapi.json` is an OpenAPI baseline that would need attention only if the request/response schema changes.

---

## 6. Risk Indicators

- The only mismatch located: both ADO health handlers omit `setting_id` when calling `get_azure_devops_creds`; the parameter already exists, so the code path for the fix is small.
- Speculative: "422 when id not passed" in the ticket may also come from a frontend/other path; the backend request model has `setting_id` Optional so a missing id does not cause pydantic 422 here. 422 observed is likely the generic wrapper (empty creds -> `MissingIntegrationException`/auth error). Reproduction not performed.
- `get_azure_devops_creds` never returns None, so "explicit id not found" is not distinguishable from "no integration" without a new check (Speculative: would need a guard comparable to XWiki's, possibly by testing for empty base_url/token or inspecting the setting).
- Speculative: `retrieve_setting` handler chain may fall back to another setting when the id does not match; not verified. Check `build_settings_handlers` before claiming "no fallback" is satisfied.
- Work item health check has the same omission; fixing only wiki leaves a sibling broken (scope decision).
- Generic 422 wrapper exposes `str(e)` to the user; diagnostics consistency is a design question.
- Frontend handoff is outside this repo.

---

## 7. Summary for Complexity Assessment

The change sits in one service module (`datasource_health_check_service.py`), with the request model and `SettingsService.get_azure_devops_creds` already supporting `setting_id`. The core defect is `health_check_azure_devops_wiki` (and the work-item sibling) not forwarding `request.setting_id`. A working in-repo template exists in the XWiki handler and its test.

Technical novelty is low. Test coverage for ADO health handlers is absent; an XWiki test provides a pattern. The main uncertainty is how the settings handler chain behaves with an unknown setting_id (possible fallback) and how to produce a descriptive error for an unresolved explicit id since the ADO creds getter never returns None.

Likely small band: 1-2 source files, 1 test file, no migrations or config.

---

## 8. External References

None named by the task
