# Technical Research

**Task**: mcp integration settings credentials test
**Generated**: 2026-10-21
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-11766 InfoNgen MCP server integration fails on 'new' environment variable setup but succeeds with 'existing' CodeMie Integration selection
product owner notes:  notes-1-backend-codemie.md (lands first):
  - Root cause: credentials are resolved only from settings.id or integration_alias, never from inline values.
  - Change: use settings.credential_values directly when there is no id, on the test path.
  - Check: whether mcp_tools goes through the same resolution.
  - Guards: accept only env names from the catalog entry's required_env_vars, never log the values, never persist.
  - Out of scope: error wording, and the restricted-mode gap on the test endpoint.

feature_area: mcp integration settings credentials test

Repository: /home/volhamakarava/codemie-dev/codemie (backend). Read AGENTS.md and the relevant .ai-run/guides first (mcp-integration.md, security-patterns.md, rest-api-patterns.md). Write technical-analysis.md into run_dir and return only the short digest.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/rest_api/routers/assistant/generation.py:196-215` — `POST /assistants/mcp/test` (`check_mcp_server`); body `MCPServerCheckRequest{mcp_server: MCPServerDetails}`; delegates to `MCPServerTester(request, user).test()`. Any exception becomes a 422 `ExtendedHTTPException`.
- `src/codemie/service/mcp/mcp_tester.py` — `MCPServerTester.test()` calls `MCPToolkitService.get_mcp_server_tools(mcp_servers=[server], user_id=user.id, mcp_server_single_usage=True)`. It passes **no** `project_name` and **no** `tools_config`. Failure text and hints are built here (wording is out of scope).
- `src/codemie/rest_api/routers/assistant/toolkits.py:131-` — `POST /assistants/mcp_tools` (`get_mcp_tools`); body is a bare `MCPServerDetails`. It calls `MCPToolsInfoService.get_mcp_toolkit_info(mcp_server_config, user, project_name=None)` (`src/codemie/service/tools/mcp_tools_info_service.py:36`), which calls `MCPToolkitService.get_mcp_server_tools(..., user_id=user.id, project_name=...)`.
- `src/codemie/rest_api/models/assistant.py:173-` — `MCPServerDetails`: `config: MCPServerConfig`, `mcp_config_id`, `use_custom_config`, `settings: Optional[SettingsBase]`, `integration_alias`. `settings` is the "environment vars" slot (a comment in the model says it must be renamed).
- `src/codemie/service/mcp/toolkit_service.py` — the credential resolution that matters:
  - `_prepare_server_config` (~906) -> `_build_mcp_server_config` (~1514) -> `MCPAccessControlService.resolve_catalog_config` -> `_resolve_credentials_with_priority` (~1468).
  - `_resolve_credentials_with_priority`: (1) if `integration_alias` and not `ignore_integration_alias`, call `_resolve_credentials_by_alias`. (2) `elif mcp_server.settings:` call `_resolve_credentials_by_id(integration_id=settings.id, user_id=settings.user_id)`. Otherwise it returns `{}`. **`settings.credential_values` is never read on this path.** This confirms the PO root cause.
  - `_resolve_credentials_by_id` (~1411) calls `SettingsService.retrieve_setting(search_fields={USER_ID: user_id} or {}, setting_id=id)`, then `_extract_credentials_from_settings` (~1337), which already turns `credential_values` into `{key: value}`.
  - Resolved values are merged with `env_vars.update(resolved_env_vars)` into `actual_config.env`.
- `src/codemie/service/mcp/access_control.py:173` — `resolve_catalog_config`: with `use_custom_config=False` and a `mcp_config_id`, the config is replaced by the catalog entry's `config` (`MCPConfig.find_by_id`). With `use_custom_config=True` the inline `config` is used.
- `src/codemie/rest_api/models/mcp_config.py:81,197` — `MCPVariableDefinition{name, description, required}` and `MCPConfig.required_env_vars: list[MCPVariableDefinition]` (catalog entry). This is the allow-list source for the "only catalog env names" guard. `required_env_vars` is referenced nowhere else in `src` except the CRUD router and `mcp_config_service.py:295`, so nothing enforces it at runtime today.
- `src/codemie/service/settings/settings_handler.py` — `build_settings_handlers()` chain: assistant mapping -> `BySettingIDSettingsHandler` (only acts when `setting_id` is truthy) -> assistant -> Default (`default=True`) -> User -> GlobalUser -> Project.
- Other settings "test" code is unrelated: `src/codemie/service/settings/settings_tester.py` (`SettingsTester`) is the `/settings/test` credential tester. It does overlay inline `credential_values` onto a stored setting, but has no MCP handler. It is a possible pattern reference only.
- Other `get_mcp_server_tools` callers (they share the same resolution): `workflows/nodes/tool_node.py:135`, `workflows/config_resources_validation.py:181`, `workflows/validation/resources.py:376`, and the assistant runtime.

### Architecture and Layers Affected
- REST router (`generation.py`, `toolkits.py`) -> service (`mcp_tester.py`, `MCPToolsInfoService`) -> `MCPToolkitService` (credential resolution, server-config build) -> `SettingsService` / settings handlers -> MCP-Connect bridge client. No repository or migration layer is involved for the test path.

### Integration Points
- Runtime callers of `MCPToolkitService._build_mcp_server_config` also include the saved-assistant execution path, so any edit to `_resolve_credentials_with_priority` changes runtime behaviour, not only the test endpoints. The PO notes scope the change to the test path.
- `codemie-ui/src/store/assistants.ts:532` (`mcp_tools`) and `:931` (`testMCP`) post the form's `mcpServer` object as-is. The UI does not appear to strip the `settings` object. The exact wire shape of a "new env vars" `settings` (id absent, `credential_values` populated, `credential_type`/`project_name` present or not) is **not verified** here. `SettingsBase` requires `project_name` and `credential_type`, so the payload must already satisfy them or validation would fail before the service runs.
- `MCPToolkitService._apply_tool_config_to_mcp_server` (~1937) already has a precedent for inline creds: `tool_config.tool_creds` is a dict merged into `server_config.env`, with no allow-list.

### Patterns and Conventions
- Resolution helpers are `@classmethod`s on `MCPToolkitService` that return `dict[str, Any]` and fall back to `{}` on a miss (`_handle_credential_resolution_error` logs the error without values).
- Log-safe helpers already exist in the same file (`_sanitize_headers_for_log`, `_sanitize_server_config_for_log`, `_sanitize_exception_for_log`). Existing debug logs log counts, not values.
- Typed errors use `ExtendedHTTPException` at the router and `ValidationException` in services (`error-handling.md`).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/integration/mcp-integration.md` — covers only router registration and slow-start timeouts. It says nothing about credential resolution or the test endpoints.
- `.ai-run/guides/development/security-patterns.md` — "Keep credentials in configuration and never log sensitive values"; log sanitized operation context only.
- `.ai-run/guides/api/rest-api-patterns.md` and `.ai-run/guides/testing/testing-patterns.md` — not re-read in depth; the change adds no endpoint or response-model field.

### Architectural Decisions
- `toolkit_service.py` comments: a pinned integration (`settings` set by the author) is authoritative and per-user overrides never replace it. `_is_explicit_integration_slot` returns False whenever `settings is not None`.
- `MCPAccessControlService.strip_inline_config` is a documented no-op: "inline overrides are kept and win over the catalog at runtime".

### Derived Conventions
- No CHANGELOG or docs entry mentions InfoNgen. The `docs/` tree has no MCP credential design doc.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/service/mcp/test_mcp_tester.py` — `MCPServerTester` (empty tools, success, exceptions, hints). It mocks `get_mcp_server_tools`, so it does **not** exercise credential resolution.
- `tests/codemie/service/mcp/test_toolkit_service_three_state.py` — alias/settings/explicit-slot resolution (`test_default_resolves_integration_alias`, `test_explicit_integration_override_resolves_by_id`, etc.). This is the natural home for a "settings with no id and `credential_values`" case.
- `tests/codemie/service/tools/test_mcp_tools_info_service.py` and `tests/codemie/rest_api/routers/test_assistant_mcp_tools.py` — the `mcp_tools` path.
- `tests/codemie/rest_api/router_guards/baselines/openapi.json` mentions `mcp/test` (router guard baseline; a changed schema would need a baseline update).

### Testing Framework and Patterns
- pytest with `unittest.mock` patching of `SettingsService.retrieve_setting` and the catalog lookup. Tests are only to be written or run when requested (AGENTS.md).

### Coverage Gaps
- No test covers `settings` with `credential_values` and no `id`.
- No test covers the `required_env_vars` allow-list (it does not exist).
- No test asserts that credential values stay out of logs.

---

## 5. Configuration and Environment

### Environment Variables
- `MCP_SERVER_INIT_TIMEOUT`, `MCP_CONNECT_INIT_TIMEOUT`, `MCP_CLIENT_TIMEOUT` — timeouts only; not related to the credential issue.

### Configuration Files
- None relevant to credential resolution.

### Feature Flags and Deployment Concerns
- `customer_config.is_component_enabled("mcpCustomServersDisabled")` (restricted mode) is enforced in `validate_on_save`, `validate_on_restore` and `filter_for_runtime`, but the test endpoint does not call any of them. This is the gap the notes call out of scope.
- No migration, deployment or secrets changes are implicated.

---

## 6. Risk Indicators

- **Fall-through with `settings.id=None` (observed in code)**: today `_resolve_credentials_by_id(None, settings.user_id)` runs the whole handler chain with only `USER_ID` (or `{}`) as search fields. `BySettingIDSettingsHandler` is skipped, but the Default/User/GlobalUser/Project handlers can still match an unrelated stored setting. That could yield the wrong credentials, or an empty result, rather than a clean miss. Any fix should short-circuit on the inline values before calling this path. Not reproduced against a database.
- **Shared resolver blast radius**: `_resolve_credentials_with_priority` is used by runtime, workflows and validation, not only the test endpoints. The notes require the change to apply on the test path only. Gating options (a parameter or a different entry point) are a design decision for spec/plan.
- **mcp_tools check (answered)**: `mcp_tools` goes through the same `get_mcp_server_tools` -> `_prepare_server_config` -> `_resolve_credentials_with_priority` chain, so it has the same defect. The difference is `project_name=None` and no `tools_config`, as with the test endpoint.
- **Allow-list gaps**: `required_env_vars` exists only on catalog entries (`MCPConfig`). A custom config (`use_custom_config=True`) or an entry without `mcp_config_id` has no catalog list. The behaviour for those cases is undefined by the notes. The catalog entry would need an extra `MCPConfig.find_by_id` lookup, because `resolve_catalog_config` copies only `config`. `MCPVariableDefinition.required` is a per-variable flag, so whether optional names are accepted is ambiguous.
- **Secrets leakage**: values arrive as `CredentialValues.value: Any`, and the masked-value placeholder convention (`SettingsService.MASKED_VALUE`, used by `SettingsTester`) is not handled in the MCP path. The failure path in `check_mcp_server` interpolates `str(e)` into the 422 detail, and `MCPServerTester.test` into its message. Neither reviewed path was shown to echo env values, but the bridge or startup errors could. Do not add value logging.
- **Persist guard**: the test path does not save settings. Persisting would only happen if the fix called `SettingsService` create methods, which should be avoided. `retrieve_setting` also decrypts values via `_decrypt_credentials`, whereas inline values arrive in plaintext. Inline values should not be passed through decryption. `_decrypt_fields` behaviour on plaintext was not checked.
- Speculative: the UI may send the new env vars in a different shape (for example, `config.env` populated, or `settings` without `project_name`). If so, the defect location could differ from the notes. Confirm the actual payload from the failing request or the UI form before locking the fix.
- Speculative: SDK (`codemie-sdk`) models mirror `MCPServerDetails`. No API shape change is expected, so SDK and UI work is likely unnecessary.

---

## 7. Summary for Complexity Assessment

The change sits in a single backend layer: `MCPToolkitService._resolve_credentials_with_priority` and its helpers in `src/codemie/service/mcp/toolkit_service.py`. The notes' root cause is confirmed, because inline `settings.credential_values` are never read; only `settings.id` and `integration_alias` are. The two entry points, `POST /assistants/mcp/test` and `POST /assistants/mcp_tools`, share this resolution, so `mcp_tools` has the same defect. The file change surface is small (one or two source files plus tests). There are no schema, migration or config changes, and no API contract change.

Technical novelty is low (the helper that converts `credential_values` to a dict already exists), but there are real design decisions. These are gating the new behaviour to the test and `mcp_tools` paths without altering runtime and workflow resolution, handling `settings.id=None` (which currently falls through the handler chain), and sourcing the `required_env_vars` allow-list from the catalog entry, which today is not enforced anywhere. The `mcp_tools` endpoint is not named in the notes' change list, so the spec should say whether it is included.

Test coverage is mixed. Resolution logic has good unit tests (`test_toolkit_service_three_state.py`), but the tester tests mock out `get_mcp_server_tools`, and nothing covers inline credentials, the allow-list or log safety. The main risks are the shared-resolver blast radius, the unverified UI payload shape, the undefined allow-list behaviour for custom configs, and secret leakage through logs or error text.

---

## 8. External References

None named by the task. The product-owner notes file `notes-1-backend-codemie.md` was quoted inline in the task context and was not located as a separate file.
