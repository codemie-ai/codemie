# EPMCDME-11766: MCP server test and tool fetch with unsaved ("new") environment variables

Size: M. Repos: `codemie` (this spec, in depth), `codemie-ui` (contract and dependency only). No SDK change.

## Problem

In the MCP toolkit wizard, choosing "new" env-var mode and clicking Test fails (InfoNgen is the reported case), while choosing an existing CodeMie Integration succeeds. The same applies to the Step-2 tool fetch.

Two causes:

1. **UI.** In "new" mode the typed values live only in `manualEnvVarValues` (`useEnvVarMode.ts:37`). They are persisted as a setting only on form submit (`submitHelpers.ts:55-66`). The Test button and Step-2 tool fetch send `settings: envVarHook.settings`, which is `undefined` in this mode (`MCPServerConfigStep.tsx:176`, `MCPToolkitForm/index.tsx:169-187`). The request carries no credentials at all.
2. **Backend.** `MCPToolkitService._resolve_credentials_with_priority` (`toolkit_service.py:1468-1511`) reads credentials only via `integration_alias` or `settings.id`. It never reads `settings.credential_values`. With `settings.id=None` it still calls `_resolve_credentials_by_id`, which runs the settings handler chain and can match an unrelated stored setting.

## Goal

`POST /assistants/mcp/test` and `POST /assistants/mcp_tools` use inline `settings.credential_values` as the MCP server environment when the request has no `settings.id`. The values are restricted to names declared by the catalog entry, are never logged, and are never persisted. Runtime, workflow and validation callers keep today's behaviour.

## Backend requirements (`codemie`)

- **R1 Opt-in.** Add an explicit keyword parameter `allow_inline_credentials: bool = False`. Thread it through `MCPToolkitService.get_mcp_server_tools` -> `_prepare_server_config` -> `_build_mcp_server_config` -> `_resolve_credentials_with_priority`. Only `MCPServerTester.test` (`mcp_tester.py`) and `MCPToolsInfoService.get_mcp_toolkit_info` (`mcp_tools_info_service.py:36`) pass `True`. Other callers (assistant runtime, `workflows/nodes/tool_node.py`, `workflows/config_resources_validation.py`, `workflows/validation/resources.py`) are unchanged.
- **R2 Resolution order is unchanged except for one new branch.** Alias still wins when present (Priority 1). In the `settings` branch:
  - `settings.id` present: resolve by id and ignore any inline values.
  - `settings.id` absent and the flag is True: use inline values.
  - `settings.id` absent and the flag is False: unchanged behaviour.
- **R3 Short-circuit.** The inline path must return without calling `_resolve_credentials_by_id` or any `SettingsService` method. This avoids the handler-chain fall-through and decryption of plaintext values.
- **R4 Allow-list.** The allow-list is the names in the catalog entry's `required_env_vars`, both required and optional. It applies when `use_custom_config` is false and `mcp_config_id` resolves via `MCPConfig.find_by_id` to an entry with a non-empty `required_env_vars`. Names outside the list are silently dropped.
- **R5 No allow-list means no inline values.** If `use_custom_config` is true, or there is no `mcp_config_id`, or the entry has no `required_env_vars`, inline values are ignored and the result is the current empty result. No error is raised.
- **R6 Merge.** Accepted values are merged into `actual_config.env` the same way resolved values are today (`env_vars.update(...)`, `toolkit_service.py:1570`). The request object is not mutated.
- **R7 No leakage.** Values are never logged. Log only counts, as the existing lines do (for example "Applied N environment variables"). Do not add values to exception text. Nothing is written through `SettingsService`, and the test path saves nothing.

## UI contract for the child `codemie-ui` story (depends on the backend story)

In "new" mode, the Test request and the Step-2 `mcp_tools` request attach:

`settings = { credential_type: "MCP", project_name: <project>, credential_values: [{key, value}, ...] }`

built from `manualEnvVarValues`, with no `id`. `SettingsBase` requires `project_name` and `credential_type`. `mcp_config_id` is already sent (`MCPToolkitForm/index.tsx:172,186`). Saving stays as today, on form submit (`handleNewEnvVarMode` -> `createOrUpdateMcpSetting`). The attached `settings` is for Test and tool fetch only. The existing mode ("existing" integration) is unchanged.

Landing order: backend first, then UI. Until the UI ships, the backend change is inert for the wizard.

## Acceptance criteria

1. Test with a catalog reference, no `settings.id`, and inline values for declared names succeeds against a server that needs them.
2. `mcp_tools` behaves the same as criterion 1.
3. Inline names not declared in `required_env_vars` are dropped and never reach the server env.
4. Inline values are ignored when the config is custom, has no `mcp_config_id`, or the catalog entry has no `required_env_vars`.
5. When `settings.id` is present, the stored setting is used and inline values are ignored.
6. Inline resolution never calls `SettingsService` and persists nothing. The handler chain is not entered when there is no id and inline values were supplied.
7. No log line or error message contains an inline value.
8. Runtime, workflow and validation callers produce identical results to before (the flag defaults to False).
9. No change to request or response models, so the openapi guard baseline is unchanged.

## Non-goals

- Error message wording and hint text in `MCPServerTester` and `check_mcp_server`.
- The restricted-mode (`mcpCustomServersDisabled`) gap on the test endpoint.
- Persisting inline values from the test endpoints, or changing how settings are saved on form submit.
- Any change to runtime, workflow or validation credential resolution.
- New or changed API fields, SDK changes (Python and Node), or MCP/test-harness changes.
- Handling of the `SettingsService.MASKED_VALUE` placeholder, an allow-list for `tool_creds`, or enforcing `required_env_vars` anywhere else.
- Overlaying inline values on a stored setting when an id is present.

## Testing notes (written only when tests are requested)

- Extend `tests/codemie/service/mcp/test_toolkit_service_three_state.py` with inline cases: allowed, unknown names dropped, custom config, id wins, flag False.
- Add `tests/codemie/service/mcp/test_mcp_tester.py` and `tests/codemie/service/tools/test_mcp_tools_info_service.py` assertions that the flag is passed.
- Add a caplog assertion for no values in logs.

## Open risks

- The UI story is required for the user-visible fix. The backend alone does not change the wizard's behaviour.
- Attaching plaintext values in `settings` means request-body logging by any middleware or proxy could capture them. This is existing behaviour for settings payloads and is not addressed here.
