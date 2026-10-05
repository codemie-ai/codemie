# Technical Research

**Task**: MCP server catalogue governance / mcpCustomServersDisabled enforcement
**Generated**: 2026-09-18
**Research path**: filesystem

---

## 1. Original Context

MCP Catalogue Governance (EPMCDME-15094): enforce mcpCustomServersDisabled toggle so the curated MCP catalogue is the only way to add MCP servers when enabled. Two consequences: (1) users cannot hand-write an MCP server definition, only select from catalogue; (2) users cannot replace the JSON config of a catalogue-added server (may still supply user-supplied values like credentials). Enforced on every write path: web UI, public API, SDK. Toggle already exists in customer-config.yaml but is not yet authoritative/enforced. No second toggle (precedent: EPMCDME-13493 confirm_custom_override was tried and fully reverted). Governance is platform-wide, gated by existing is_admin_or_maintainer check, no per-project layer. Sub-tasks: A1 platform toggle made runtime-overridable/authoritative (codemie); A2 enforcement on all seven persisting save paths (codemie); A3 governance setting + builder behavior in web UI (codemie-ui); A4 SDK parity (codemie-sdk). Out of scope: tool allowlisting, run-time enforcement on read/historical data, catalogue secret redaction, tool preview/connection test endpoints, deployment-template validation.

---

## 2. Codebase Findings

### Existing Implementations

Enforcement is **already substantially implemented**, not greenfield. The core service and most of the seven save-path call sites exist today:

- `codemie/src/codemie/service/mcp/access_control.py` — `MCPAccessControlService`, the single enforcement point:
  - `validate_on_save(mcp_servers)` — when `customer_config.is_component_enabled("mcpCustomServersDisabled")` is true, calls `_validate_restricted_mode()`, which rejects any server without `mcp_config_id` and rejects any field on a catalog-ref server outside `_CATALOG_REF_ALLOWED_FIELDS` (`name`, `description`, `enabled`, `mcp_config_id`, `use_custom_config`, `tools`, `tools_tokens_size_limit`, `settings`, `integration_alias`, `resolve_dynamic_values_in_arguments`). Fields such as `config`, `command`, `arguments`, `mcp_connect_url` are excluded from that set, so restricted mode already blocks inline JSON config on a catalog-ref server while still allowing `settings` (credentials/env vars) — this is consequence (2) from the ticket.
  - `_validate_catalog_entries()` — always checks catalog `mcp_config_id` refs exist, are `is_active`, and `is_public` (open mode and restricted mode both).
  - `sanitize_for_save()` — thin wrapper: validate then return unchanged (no stripping; inline overrides are preserved and win at runtime per its own docstring).
  - `strip_inline_config()` — now a documented no-op ("inline overrides are kept and win over the catalog at runtime"); `_strip_one()` likewise a no-op passthrough.
  - `filter_for_runtime()` — runtime read-path filter, drops non-catalog or catalog-unavailable servers silently in restricted mode. (Ticket marks runtime/historical-data enforcement out of scope, but this function already exists and is exercised by `MCPToolkitService`.)
  - `resolve_catalog_config()` — resolves inline vs. catalog config based on `use_custom_config`.
- `codemie/tests/unit/service/mcp/test_access_control.py` — full unit coverage of the above (open mode, restricted mode, each forbidden field, `filter_for_runtime`, `resolve_catalog_config`).

**Save-path call sites found today** (`MCPAccessControlService.sanitize_for_save` / `validate_on_save`):
1. `codemie/src/codemie/rest_api/routers/assistant.py:743` — `create_assistant`
2. `codemie/src/codemie/rest_api/routers/assistant.py:862` — `update_assistant`
3. `codemie/src/codemie/rest_api/routers/assistant.py:977` — `ask_virtual_assistant` (not persisted, still validated)
4. `codemie/src/codemie/rest_api/routers/workflow.py:396` — `create_workflow` (via `_collect_workflow_mcp_servers`, which walks both `workflow_config.assistants[].mcp_servers` and `workflow_config.tools[].mcp_server`)
5. `codemie/src/codemie/rest_api/routers/workflow.py:474` — `update_workflow`
6. `codemie/src/codemie/service/skill_service.py:506` — skill create
7. `codemie/src/codemie/service/skill_service.py:649` — skill update

Two more call sites exist outside the "seven" but touch the same guard:
- `codemie/src/external/deployment_scripts/preconfigured_assistants.py:148,224` — create/update preconfigured-assistant deployment script (added by a prior ticket, EPMCDME-8996, per `docs/superpowers/tasks/2026-07-08-preconfigured-assistant-mcp-improvements/spec.md`).

`_track_mcp_usage_on_create/_track_mcp_usage_changes` in `assistant.py` reference `mcp_config_id` for catalog usage-count accounting; `codemie/src/codemie/rest_api/routers/mcp_managed.py` and `codemie/src/codemie/rest_api/routers/mcp_config.py` hold the catalog CRUD/list endpoints themselves.

### Architecture and Layers Affected

- **Config layer** — `codemie/src/codemie/configs/customer_config.py` (`CustomerConfig.is_component_enabled`), `codemie/src/codemie/configs/component_resolution.py` (generic runtime-override snapshot: `resolve()`/`resolve_all()`/`publish_snapshot()`), `codemie/src/codemie/service/customer_config_declarations.py` (the registry that makes a YAML component runtime-editable), `codemie/src/codemie/service/customer_config_service.py` (`OverrideCache`, `save_setting`/`reset_setting`/`list_settings`, backed by `DynamicConfigService`).
- **REST API layer** — `codemie/src/codemie/rest_api/routers/customer_config.py` (`GET /v1/config`, `GET/PUT/DELETE /v1/config/declarations/{component_id}`, gated by `require_customer_config_write`), `assistant.py`, `workflow.py`, `mcp_config.py`, `mcp_managed.py`.
- **Service layer** — `MCPAccessControlService`, `skill_service.py`, `MCPToolkitService` (`codemie/src/codemie/service/mcp/toolkit_service.py`, calls `filter_for_runtime` and `resolve_catalog_config` at runtime).
- **Security layer** — `codemie/src/codemie/rest_api/security/authentication.py`: `require_customer_config_write` → `_deny_unless_admin_or_maintainer` → `request.state.user.is_admin_or_maintainer`. This is the exact gate the ticket names as already existing and to be reused (no per-project layer).
- **UI layer** — `codemie-ui/src/utils/mcpMode.ts` (`isMCPRestrictedMode`), `codemie-ui/src/constants/mcp.ts` (`MCP_CUSTOM_SERVERS_DISABLED_CONFIG_ID`), `codemie-ui/src/pages/assistants/components/AssistantForm/components/Toolkits/MCPToolkit/*` (builder), `codemie-ui/src/pages/settings/administration/CustomerConfigurationPage.tsx` + `SettingCard.tsx` (generic admin settings page driven entirely by the backend's declarations list).
- **SDK layer** — `codemie-sdk/sdk/codemie-python/src/codemie_sdk/models/assistant.py` (`MCPServerDetails`), `models/mcp_config.py`, `services/assistant.py`, `services/mcp_configs.py`. The SDK's assistant/workflow/skill write methods call the same `/v1/assistants`, presumably `/v1/workflows`, and skill REST endpoints (confirmed for assistants: `services/assistant.py:108,123` → `POST/PUT /v1/assistants[...]`), so backend enforcement already applies transitively to SDK callers.

### Integration Points

- `MCPAccessControlService` depends on `codemie.configs.customer_config.customer_config` (module-level singleton) and `MCPConfig.get_by_ids` / `MCPConfig.find_by_id` (catalog persistence, `codemie/src/codemie/rest_api/models/mcp_config.py`).
- `customer_config.is_component_enabled()` → `resolve_component()` → `component_resolution.resolve()`, which layers a DB-backed override (`_snapshot`, populated by `OverrideCache` from `DynamicConfigService.alist_by_key_prefix(KEY_PREFIX)`) on top of the YAML value, **only for component IDs present in `customer_config_declarations.DECLARATIONS`**. `mcpCustomServersDisabled` is **not** currently in `DECLARATIONS` (`codemie/src/codemie/service/customer_config_declarations.py:171`, which lists only `CHAT_DISCLAIMER`, `RELEASE_NOTES_RECENT_COUNT`, `WEB_SEARCH`, `SCHEDULERS`). Today, toggling `mcpCustomServersDisabled` requires editing `customer-config.yaml` and redeploying — this is the concrete gap the ticket's "not yet authoritative/enforced" and A1 "runtime-overridable" language points at.
- The admin settings page (`CustomerConfigurationPage.tsx`) and its `SettingCard`/`SchemaForm` are fully generic: they render whatever `GET /v1/config/declarations` returns, with no per-setting frontend code. Adding a `SettingDeclaration` for `mcpCustomServersDisabled` (a single `SWITCH` field, mirroring `WEB_SEARCH`) would make it appear in the admin UI without further UI work — the same pattern the existing declarations already follow.
- `codemie-ui/src/pages/assistants/components/AssistantForm/components/Toolkits/MCPToolkit/MCPToolkit.tsx` reads `appInfoStore.configs` for `MCP_CUSTOM_SERVERS_DISABLED_CONFIG_ID` directly (bypassing `mcpMode.ts`'s helper in this one file) and, when restricted: hides "Add custom" (`showCustomSetup = !isRestricted`), builds marketplace-selected servers without `config`/inline fields, and passes `isCatalogRef={isRestricted && !!selectedMcpServer?.mcp_config_id}` into `MCPToolkitForm` → `MCPServerConfigStep.tsx`, which uses `isCatalogRef` to set `customSetupEnabled={!isCatalogRef}` and hide the connect-URL field. This is the assistant-builder half of A3, already built.
- **Gap found**: `codemie-ui/src/pages/workflows/editor/configPanels/**` (`VirtualAssistantForm.tsx`, `AssistantTab.tsx`, `ToolForm.tsx`, `ToolSelector.tsx`, `ToolTab.tsx`) and `codemie-ui/src/pages/skills/**` (`SkillForm.tsx`, `SkillDetails.tsx`, `useSkillForm.tsx`, `skillValidation.ts`) all reference MCP server data but **none of them import `isMCPRestrictedMode`, `mcpMode`, or `MCP_CUSTOM_SERVERS_DISABLED_CONFIG_ID`** — confirmed by grep across both directories returning no matches. Only the assistant-form `MCPToolkit` component is restricted-mode aware; the workflow tool/assistant editor and skill builder have no client-side gating today even though the backend (`workflow.py:396,474`, `skill_service.py:506,649`) already validates and would reject a hand-written config from those builders with a `ValidationException`.

### Patterns and Conventions

- **Runtime-override pattern**: any YAML component becomes admin-editable at runtime by adding one `SettingDeclaration` to `customer_config_declarations.DECLARATIONS` — "no API, schema or frontend change is required" per that module's own docstring. `DynamicConfigService` (admin-gated via `_validate_admin`/`user.is_admin_or_maintainer`) is the generic key-value store underneath; `customer_config_service.py` maps `CUSTOMER_CONFIG__...` keys back to component overrides.
- **Validate-then-strip pattern**: routers call `MCPAccessControlService.validate_on_save(...)` (raises `ValidationException` → mapped to 400 by the router) before persisting; `workflow.py` additionally calls `_strip_workflow_mcp_servers()` (now a no-op passthrough per current code) between validation and persistence.
- **Allowed-fields frozenset pattern**: `_CATALOG_REF_ALLOWED_FIELDS` in `access_control.py` is the single source of truth for "what a catalog-ref server may still carry" — any new `MCPServerDetails` field must be triaged into or out of that set explicitly, or it is silently rejected in restricted mode by the current "anything not allowed and not None" check.
- **Generic declared-settings UI pattern**: `SettingCard.tsx` + `SchemaForm` render any declaration without bespoke UI, keyed off `FieldType.SWITCH/INPUT/TEXTAREA`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/mcp-integration.md` (codemie) — directs MCP config/auth logic to stay behind the existing MCP service/router boundaries; documents the two-tier timeout for slow-starting servers. Does not mention catalogue governance or `mcpCustomServersDisabled`.
- No `.ai-run/guides/` entry specifically covers customer-config runtime overrides or MCP catalogue restriction; that behavior is documented only in code (docstrings in `component_resolution.py`, `customer_config_declarations.py`, `customer_config_service.py`).

### Architectural Decisions

- `codemie/docs/superpowers/tasks/2026-07-08-preconfigured-assistant-mcp-improvements/spec.md` (ticket EPMCDME-8996) — prior work that wired `MCPAccessControlService.sanitize_for_save()` into the preconfigured-assistant deployment script, and documents the "fail-fast in restricted mode" decision: a restricted-mode deployment with an inline-config template raises `ValidationException` and startup fails; operators must fix the template. This is directly relevant precedent for how A2 should treat any remaining unwired paths.
- Precedent named in the ticket itself: EPMCDME-13493 `confirm_custom_override` was tried and fully reverted — confirmed by an empty repo-wide search for `confirm_custom_override`/`ConfirmCustomOverride` (no matches anywhere, including git history artifacts checked). No trace of a second toggle or override-confirmation mechanism exists in the current codebase.
- `component_resolution.py` docstring records the resolution precedence decision explicitly: "runtime-computed component wins outright, then the override for each field it carries, then YAML."

### Derived Conventions

- Boolean feature toggles in `customer-config.yaml` follow the `enabled: bool` + optional `name`/`description` shape (see `mcpCustomServersDisabled`'s own YAML block, `codemie/config/customer/customer-config.yaml:252-256`, which already carries a `name` and `description` suitable for reuse in a `SettingDeclaration` label/description).
- New `ValidationException` messages in `access_control.py` name the server and the offending field/id explicitly (e.g. `"Field '{field}' is not allowed when mcp_config_id is set in restricted mode (server '{server.name}')."`), a convention any new checks should keep.

---

## 4. Testing Landscape

### Existing Coverage

- `codemie/tests/unit/service/mcp/test_access_control.py` — thorough: open/restricted mode for `validate_on_save` (empty list, inline pass in open mode, catalog-ref valid/missing/inactive/non-public, disabled-server-still-validated), every forbidden field in restricted mode (`config`, `command`, `arguments`, `mcp_connect_url`), `strip_inline_config` no-op behavior, `filter_for_runtime` (open mode passthrough, restricted-mode drops for each unavailability reason, mixed list), `resolve_catalog_config` (no-id passthrough, missing entry, no config, resolves, inline-wins via `use_custom_config`, global-mode overwrite, conversion failure).
- `codemie/tests/external/deployment_scripts/test_preconfigured_assistants.py` — covers the deployment-script call sites per the EPMCDME-8996 spec (1/2/>2-MCP scenarios, restricted-mode fail-fast).
- `codemie/tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py`, `test_toolkit_service_three_state.py` — toolkit-service-level MCP tests (runtime resolution, not save-path governance).
- UI: `codemie-ui/src/utils/__tests__/mcpMode.test.ts` — full coverage of `isMCPRestrictedMode` (absent config, enabled true/false, unrelated entries ignored). `codemie-ui/src/pages/assistants/.../MCPToolkit/__tests__/*` — eleven test files covering `MCPToolkit`, `MCPServerConfigStep`, `MCPServerDetail`, `MCPServerListItem`, `MCPServerEnvVars`, `MCPConfigSection`, `MCPActionButtons`, `MCPEmptyState`, `formHelpers`, accessibility. No grep hit for `isCatalogRef` or restricted-mode assertions inside these test files was separately confirmed beyond source usage — component behavior is covered by `MCPToolkit.test.tsx`/`MCPServerConfigStep.test.tsx` presence, but exact restricted-mode assertions were not individually opened.
- SDK (`codemie-sdk`): no test-harness file under `codemie-sdk/test-harness/**` matches `mcpCustomServersDisabled`; the two grep hits found were for an unrelated ticket (EPMCDME-13903) and an unrelated intake file (EPMCDME-14705) that merely contain the string incidentally.

### Testing Framework and Patterns

- Backend: `pytest` with `unittest.mock.patch`/`MagicMock`, module-path patch targets (e.g. `_CC = "codemie.service.mcp.access_control.customer_config"`), small `_server()`/`_catalog_entry()` builder helpers local to the test file — a pattern any new restricted-mode test should reuse.
- UI: Vitest + React Testing Library, `vi.mock('@/store/appInfo', ...)` for store-dependent unit tests.

### Coverage Gaps

- **No test exists for `customer_config_declarations.py` behavior with an `mcpCustomServersDisabled` declaration** — because the declaration does not exist yet (A1 work item).
- **No UI test exists for workflow-editor or skill-builder MCP components respecting restricted mode** — because, per the grep findings above, those components have no restricted-mode logic to test yet.
- **No SDK test or model-level check exists for `mcpCustomServersDisabled`** — the SDK currently has no direct knowledge of the toggle; it relies entirely on server-side rejection.
- **No integration/API test found (grep-visible) that submits a hand-written MCP server through the public API in restricted mode and asserts a 400** — the unit tests patch `customer_config` directly rather than exercising a full request; whether a router-level (API) test exists for this scenario was not located under `codemie/tests/`.

---

## 5. Configuration and Environment

### Environment Variables

- No `MCP_CUSTOM_SERVERS_DISABLED`-style environment variable exists. Unlike `features:*` components (which get a `FEATURE_*` env override via `CustomerConfig._apply_feature_env_override`), `mcpCustomServersDisabled` does **not** use the `features:` prefix, so it does not receive an env-var override today, and is not one of the hardcoded `CONFIG_IDS` runtime-computed components (`enterpriseEdition`, `userManagement`, `idpProvider`, `mcpAuthOrigin`, `chatContextualNaming`, `budgetSoftLimitNotification`, `budgetSoftLimitEmail`, `gitlabOauth`, `jiraOauth`, `confluenceOauth`).
- `MCP_SERVER_INIT_TIMEOUT`, `MCP_CONNECT_INIT_TIMEOUT`, `MCP_CLIENT_TIMEOUT` (documented in `.ai-run/guides/integration/mcp-integration.md`) govern MCP connection timeouts — unrelated to governance but adjacent domain.

### Configuration Files

- `codemie/config/customer/customer-config.yaml:252-256` — the `mcpCustomServersDisabled` component block: `enabled: false` by default, with `name: "MCP Custom Servers Disabled"` and a description already worded consistently with the ticket's intent.
- `codemie/src/codemie/service/customer_config_declarations.py` — the registry A1 needs to extend; currently 4 declarations, none for MCP.
- `codemie-ui/src/constants/mcp.ts` — `MCP_CUSTOM_SERVERS_DISABLED_CONFIG_ID = 'mcpCustomServersDisabled'`, the UI-side constant already wired to the same component id string.

### Feature Flags and Deployment Concerns

- `mcpCustomServersDisabled` is read via `customer_config.is_component_enabled("mcpCustomServersDisabled")` — a plain string component id, not routed through `CONFIG_IDS`/`is_feature_enabled` (which is `features:`-prefixed only). Any A1 change should preserve this exact component-id string since both `access_control.py` and `codemie-ui/src/constants/mcp.ts` already hardcode it.
- `OverrideCache` (`customer_config_service.py`) has a bounded TTL (`config.CUSTOMER_CONFIG_CACHE_TTL_SECONDS`) and a per-pod invalidate-on-write + eventual-consistency-within-TTL model for other pods; this is the same cache path a runtime-overridable `mcpCustomServersDisabled` would inherit, including its documented behavior of serving the last-known-good snapshot if the DB is unreachable, and falling back to YAML if it has never loaded successfully.
- The write path (`PUT/DELETE /v1/config/declarations/{component_id}`) is gated by `require_customer_config_write` → `is_admin_or_maintainer` — exactly the gate the ticket specifies as reused, with no additional per-project check found anywhere in `security/authentication.py`, `security/permissions.py`, or `core/ability.py` for this component.

---

## 6. Risk Indicators

- **A1 is a small, well-precedented change but is easy to under-scope**: adding a `SettingDeclaration` to `customer_config_declarations.py` is mechanically simple (one entry, mirroring `WEB_SEARCH`), but the ticket's "made authoritative" language plus the existing `mcpCustomServersDisabled` component **not** being `features:`-prefixed means it is excluded from the env-var override path (`_apply_feature_env_override`) — Speculative: if the eventual design wants `FEATURE_*`-style override in addition to the DB-backed one, that is a rename/re-namespacing decision with blast radius across `access_control.py` and `codemie-ui/src/constants/mcp.ts`, both of which hardcode the string `"mcpCustomServersDisabled"`.
- **UI builder gap is real and specific**: workflow-editor (`ToolForm.tsx`, `ToolSelector.tsx`, `VirtualAssistantForm.tsx`, `AssistantTab.tsx`, `ToolTab.tsx`) and skill builder (`SkillForm.tsx`, `useSkillForm.tsx`, `SkillDetails.tsx`) have zero references to `isMCPRestrictedMode`/`mcpMode`/`MCP_CUSTOM_SERVERS_DISABLED_CONFIG_ID`, confirmed by grep returning no matches in either directory. Backend enforcement (`workflow.py:396,474`, `skill_service.py:506,649`) already rejects violations there, so this is a UX gap (silent-until-save-time 400s) rather than a security gap, but A3 explicitly calls out "builder behavior" and these two builders are currently unaddressed.
- **`_CATALOG_REF_ALLOWED_FIELDS` is a manually maintained frozenset**: any new field added to `MCPServerDetails` in the future needs a conscious decision to add it to this set or not; it is easy for a reviewer to miss that a new field silently becomes forbidden-in-restricted-mode by omission (or, worse, silently allowed as a bypass channel if added without review).
- **`strip_inline_config`/`_strip_one` being no-ops today is a deliberate but easy-to-misread state**: their docstrings say inline overrides "win over the catalog at runtime," which sounds permissive; the actual restriction against setting those fields at all happens earlier, in `_validate_restricted_mode`. A reviewer skimming only `strip_inline_config` could wrongly conclude restricted mode is unenforced.
- **No test-harness / SDK test coverage found for this governance flag** — A4 has no existing regression net; SDK relies entirely on the backend rejecting a hand-written server, which the SDK model layer does not prevent client-side (its `MCPServerDetails` model allows setting `config`, `command`, `arguments`, `mcp_connect_url` unconditionally).
- **Two additional call sites beyond the "seven"** (`preconfigured_assistants.py:148,224`) already call `sanitize_for_save`; A2 scoping should confirm whether the ticket's "seven persisting save paths" count includes or excludes these, and the virtual-assistant path (`ask_virtual_assistant`, not persisted), to avoid double-counting or missing one during implementation.
- **Runtime/historical-data enforcement is explicitly out of scope**, but `filter_for_runtime` already implements a version of it — Speculative: if A1/A2 tighten `validate_on_save`, care is needed not to inadvertently change `filter_for_runtime`'s already-shipped, differently-scoped behavior.

---

## 7. Summary for Complexity Assessment

This ticket lands on a codebase where the core governance mechanism — `MCPAccessControlService` in `codemie/src/codemie/service/mcp/access_control.py` — is already implemented and unit-tested, and is already wired into all seven-or-nine identified save paths across assistants (create/update/virtual), workflows (create/update), and skills (create/update), plus a deployment script. The primary backend gap (A1) is narrow and precedented: `mcpCustomServersDisabled` is not yet listed in `customer_config_declarations.DECLARATIONS`, so it cannot be toggled at runtime through the existing generic `DynamicConfigService`-backed override mechanism (`OverrideCache`, `component_resolution.py`, `PUT /v1/config/declarations/{component_id}`) that four other flags already use, gated by the same `require_customer_config_write` → `is_admin_or_maintainer` check the ticket names. Adding that declaration is a small, well-understood change with a working template to copy (`WEB_SEARCH`).

The UI side (A3) is more novel in scope than it first appears: the assistant-builder MCP component (`MCPToolkit.tsx` and its `MCPToolkitForm`) already implements restricted-mode builder behavior (hiding custom setup, disabling JSON config edits for catalog-ref servers via `isCatalogRef`), and the admin settings page is fully generic and declaration-driven, so exposing the new toggle there needs no bespoke frontend code. However, the workflow editor and skill builder have no restricted-mode awareness at all today — a confirmed gap via grep, not a review guess — so bringing them to parity with the assistant builder is real, undocumented-pattern work rather than a copy-paste extension.

SDK parity (A4) inherits backend enforcement transitively today because the SDK's write methods call the same REST endpoints, but the SDK model (`MCPServerDetails` in `codemie_sdk/models/assistant.py`) has no restricted-mode awareness and no test coverage for this flag exists anywhere under the SDK's test harness. Key risk factors for complexity scoring: the manually maintained `_CATALOG_REF_ALLOWED_FIELDS` frozenset as a single point of both correctness and silent-regression risk, the confirmed absence of any workflow/skill UI gating, the precedent of a fully reverted sibling ticket (EPMCDME-13493) constraining the toggle-count design choice, and ambiguity in exactly which nine call sites constitute the ticket's "seven persisting save paths."

---

## 8. External References

None named by the task. The task references `customer-config.yaml` as a location where the toggle "already exists," which was resolved during research to `codemie/config/customer/customer-config.yaml` (component `mcpCustomServersDisabled`, lines 252-256) — this is an in-repo file covered under Section 2/5 above, not an external source of truth, so it is reported there rather than here. The task also references ticket EPMCDME-13493 as precedent; no filesystem artifact of that ticket's reverted `confirm_custom_override` mechanism was found (confirmed via repo-wide grep with zero matches), so there is nothing further to cite from it beyond the absence itself, noted under Section 6.
