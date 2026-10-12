# Technical Research

**Task**: exchange credentials toolkit alembic-migration feature-flag
**Generated**: 2026-09-25
**Research path**: codegraph

---

## 1. Original Context

EPMCDME-15199: "Exchange toolkit: register EXCHANGE credential type, dual-path config, reversible migration and polling feature flag". The description and acceptance criteria below are verbatim from `ticket.local.txt` (lines 20-117). That file is the authoritative copy, and the tables and links it contains are not repeated here.

```text
h1. Slice 1 of 3 — Exchange platform enablement

Delivers the platform's ability to hold, validate and inject an Exchange credential. Contains *no Exchange protocol code and adds no new external dependencies* — the two transports that consume this config arrive in Slice 2.

h2. Scope
 * New members in the closed CredentialTypes and ToolSet enums in codemie_tools/base/models.py — CredentialTypes.MS_EXCHANGE, ToolSet.MS_EXCHANGE. Both must land in the same PR as the toolkit class, or ExchangeToolkitUI raises AttributeError at import and the toolkit silently fails to load.
 * One reversible Alembic migration on the codemie.credentialtypes PostgreSQL enum via op.sync_enum_values() — not raw ALTER TYPE ADD VALUE. new_values takes PostgreSQL enum label names (Python member .name, uppercase), built from current HEAD state.
 * ExchangeToolConfig with an auth_type discriminator and *no default*, validating only the fields of the selected path. Graph: tenant_id, client_id, client_secret, mailbox. EWS: ews_url, service_account_username, service_account_password, mailbox.
 * Native toolkit registration via DiscoverableToolkit autodiscovery; toolkit appears in the selector with no enterprise entitlement required.
 * Conditional auth-type field visibility in the integration configuration UI.
 * Credential injection and masking proven end-to-end with no transport attached.
 * Platform feature flag gating the scheduled-trigger configuration path, default *off*.
 * Documentation of what adding a native connector requires of the platform (REQ-3.1.9).

h2. Acceptance criteria
 * Given a platform instance with the toolkit installed, when the toolkit provider runs its autodiscovery scan, then the Exchange toolkit is discovered and ToolSet.MS_EXCHANGE resolves without an import-time error.
 * Given an assistant author opening the toolkit selector, when the list renders, then Exchange appears alongside Jira and ServiceNow with no enterprise entitlement required.
 * Given a configuration submitted with no auth_type, when it is validated, then it is rejected with a message naming auth_type as required — no transport is selected implicitly.
 * Given auth_type=oauth_azure with any of tenant_id, client_id or client_secret missing, when the configuration is validated, then it is rejected naming the specific missing field; and given auth_type=ntlm with any of ews_url, service_account_username or service_account_password missing, then likewise.
 * Given a valid configuration for either path, when an operator saves it and the platform later resolves it, then client_secret and service_account_password are stored encrypted, returned masked to the UI, and decrypted only into the in-memory config — appearing in no prompt, log, or model context.
 * Given an operator viewing the configuration form, when they select an auth_type, then only the fields belonging to that path are shown.
 * Given the Alembic migration applied forward, when it is rolled back, then the credentialtypes database enum is returned to exactly its pre-migration set of labels, MS_EXCHANGE credential rows are removed before the enum is shrunk, and no other toolkit or credential row is altered. (ToolSet.MS_EXCHANGE is a Python enum member reverted by code, not by the migration.)
 * Given the scheduled-trigger feature flag in its default state on a fresh install, when an assistant author configures the toolkit, then no polling cron path is offered; and when an operator enables the flag, then the scheduled-trigger configuration path becomes available without a redeploy.
 * Given an engineer reading the connector's documentation, when they set out to add a second native toolkit by this pattern, then the documentation states which platform changes that requires — closed-enum members, Alembic enum migration, and redeployment — so the REQ-3.1.9 constraint is discoverable rather than implicit.
 * Given the test suite, when it runs, then it covers toolkit discovery, enum registration, forward migration and rollback, and configuration validation for both paths using fixture-only credentials with no literal production values.

h2. Out of scope
 * All Exchange protocol code (Graph client, EWS client, mailbox operations) — Slice 2. The four production deps (azure-identity, msgraph-sdk-python, exchangelib, requests-ntlm) — Slice 2. Mutating operations — Slice 3. The polling mechanism itself and any relaxation of scheduler_settings_service._validate_minimum_hourly_frequency(). IMAP/POP3. OAUTH_AZURE_CERT and Autodiscover.
```

Durable constraints, coordination with EPMCDME-13773, dependencies and resolved open questions: see Section 8.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie_tools/base/models.py`:
  - `CredentialTypes` (line 68). `.value` is the UI label. `MS_TEAMS = "MSTeams"` is the last member, under `# Project settings`.
  - `ToolSet` (line 118) ends with `SHAREPOINT`. It has no MS_* member.
  - `RequiredField(default="")` sets `json_schema_extra.required_at_runtime`.
  - `CodeMieToolConfig`, `ToolKit`, `Tool.from_metadata`.
  - Blast radius: ToolSet has 56 callers and CredentialTypes has 74.
- `src/codemie_tools/notification/email/models.py` is the dual-auth precedent:
  - `EmailAuthType(str, Enum)` has `basic` and `oauth_azure`.
  - `auth_type` defaults through `get_tool_default(TOOL_NAME,"auth_type") or "basic"`.
  - Conditional fields are `Optional[str] = Field(default=None)`, and secrets are marked `json_schema_extra={"sensitive": True}`.
  - `credential_type` is frozen and excluded.
  - Tests: `tests/codemie_tools/notification/email/test_models.py`, `test_email_tools.py`.
- `src/codemie_tools/itsm/toolkit.py` is the minimal toolkit shape. `ITSMToolkitUI(ToolKit)` sets toolkit, tools and label. `ITSMToolkit(DiscoverableToolkit).get_definition()` returns the UI object.
- `src/codemie_tools/base/toolkit_provider.py` scans `codemie_tools` recursively for `DiscoverableToolkit` and `CodeMieToolConfig` subclasses.
  - Results are `lru_cache`d.
  - `_process_module_generic` catches only `ImportError`.
  - `get_available_tools_configs_info` exposes `json_schema_extra` keys (`sensitive`, `placeholder`, `required_at_runtime`) and `default`.
- `src/codemie/service/tools/tools_info_service.py`: `get_tools_info` merges the autodiscovered toolkits with standard, enterprise-plugin and provider toolkits. It applies only an `exclude_toolkits` filter, with no entitlement filter on native toolkits.
- `src/codemie/service/settings/settings.py` (SettingsService):
  - `LIST_OF_SENSITIVE_FIELDS` (lines 134-161) is key-based and global. It contains `client_secret`, `password`, `smtp_password` and `oauth_client_secret`. It does not contain `service_account_password`.
  - `__CREDENTIAL_CONFIG_TO_TYPE` (lines 228-258) maps config classes to types. `get_config` (line 960) raises `ValueError` for an unmapped class.
- `src/codemie/service/settings/base_settings.py` holds `_encrypt_fields`, `_decrypt_fields`, `hide_sensitive_fields` and `MASKED_VALUE`. Encryption comes from `EncryptionFactory` (Plain, Base64, AWS, Azure, GCP, Vault).
- `src/codemie/service/settings/settings_tester.py`: the `SettingsTester.handlers` dict is keyed by CredentialTypes. An unmapped type raises `SettingsTesterHandlerNotFound`.
- `src/codemie/service/settings/scheduler_settings_service.py:775` defines `_validate_minimum_hourly_frequency`, which enforces at least 3600 s between runs. It is out of scope.

### Architecture and Layers Affected
- **Tools library** (`codemie_tools`): enums, config model, toolkit class.
- **Service layer**: settings encryption and masking, the config-to-type map, the tester registry, tools info.
- **Data layer**: the `codemie.credentialtypes` PG enum on `codemie.settings.credential_type`.
- **Config layer**: customer-config feature components.
- **Docs**.
- **UI**: codemie-ui `CREDENTIAL_UI_MAPPING`, which is a separate repo.

### Integration Points
- Feature flags:
  - `customer_config.is_feature_enabled(key)` resolves the component `features:<key>` (`src/codemie/configs/customer_config.py:317`). A missing component means disabled.
  - Usage example: `ToolkitService._augment_toolkits_with_feature_flags` checks `webSearch` and `dynamicCodeInterpreter`.
- Runtime override:
  - `src/codemie/service/customer_config_declarations.py` holds the `DECLARATIONS` tuple (CHAT_DISCLAIMER, RELEASE_NOTES_RECENT_COUNT, WEB_SEARCH, SCHEDULERS). `features:schedulersView` is an existing scheduler-adjacent flag.
  - A component outside the registry is "served from YAML only". Adding a declaration needs no API or schema change.
  - Keys are built as `CUSTOMER_CONFIG__FEATURES__<UPPER_SNAKE>`.
  - Overrides are stored in the DynamicConfig table through `customer_config_service.save_setting`. They propagate within `CUSTOMER_CONFIG_CACHE_TTL_SECONDS`.
  - Routes: `PUT`/`DELETE /config/declarations/{component_id}`.
- `GET /v1/config` returns `resolve_components()`.

### Patterns and Conventions
- The credential-type migration precedent is `src/external/alembic/versions/8b2c1a4d5e6f_add_ms_teams_credential_type.py`.
  - Upgrade calls `op.sync_enum_values` with the full list of values.
  - Downgrade runs `DELETE FROM codemie.settings WHERE credential_type='MS_TEAMS'` and then syncs the list without that value.
  - It passes `affected_columns=[TableReference('codemie','settings','credential_type')]` and `enum_values_to_rename=[]`.
- The latest known enum state is in `fa14587c0de1_fold_oauth_credential_types.py` (2026-09-03). Its `_ENUM_VALUES_FOLDED` list has 35 labels, from JIRA through DIAL and MS_TEAMS. The three *_OAUTH values have been removed.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/agents/custom-tool-creation.md` is thin. It puts tools in `codemie_tools` and tests under `tests/codemie_tools/`.
- Guides also exist at `.ai-run/guides/development/configuration-patterns.md`, `.ai-run/guides/data/database-patterns.md` and `.ai-run/guides/development/security-patterns.md`. They were not read in depth.
- Connector-adjacent docs: `docs/workflows/08_integration_capabilities.md` and `docs/superpowers/specs/2026-08-17-epmcdme-14219-sharepoint-standalone-toolkit-design.md` (a standalone-toolkit precedent).
- No connector-authoring guide was found.

### Architectural Decisions
- The docstring in `customer_config_declarations.py` records that the runtime-override registry is opt-in per component.
- The fold migration's docstring records that the enum stores member names.

### Derived Conventions
- The enum label written to PG is the member `.name`.
- Each credential-type migration restates the full value list.
- Secrets are flagged twice: once by `sensitive: True` in the schema, which drives the UI, and once by the key in `LIST_OF_SENSITIVE_FIELDS`, which drives storage.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie_tools/base/test_toolkit_provider.py`: discovery.
- `tests/codemie_tools/notification/email/test_models.py`: Email config validation.
- `tests/codemie/service/settings/test_settings_service.py`, `test_folded_oauth_type.py`, `test_sharepoint_auth_switch.py`: settings and encryption.
- `tests/codemie/configs/test_customer_config.py`, `test_customer_config_override.py`, `test_component_resolution.py`; `tests/codemie/service/test_customer_config_declarations.py`, `test_customer_config_service.py`; `tests/codemie/rest_api/routers/test_customer_config_router.py`: feature flags and overrides.

### Testing Framework and Patterns
- pytest with `unittest.mock` (`patch`, `AsyncMock`) and pytest-asyncio. The policy is in `.ai-run/guides/testing/testing-patterns.md`.

### Coverage Gaps
- No Alembic up/down migration test harness was found. The only migration-named test is `tests/codemie/clients/test_enterprise_migrations.py`, which is unrelated.
- `tools_info_service.get_tools_info` has no covering tests.

---

## 5. Configuration and Environment

### Environment Variables
- `CUSTOMER_CONFIG_CACHE_TTL_SECONDS` sets the override staleness window.
- The mapping from a `FEATURE_*` env var to a `features:*` component was not confirmed in this research.

### Configuration Files
- The customer-config YAML component list is loaded by `CustomerConfig` in `src/codemie/configs/customer_config.py`. The exact YAML path was not captured.
- `tool_defaults` feeds `get_tool_default`.

### Feature Flags and Deployment Concerns
- Declared runtime-overridable flags: `features:webSearch`, `features:schedulersView`.
- `features:exchangePolling` does not exist today.

---

## 6. Risk Indicators

- The Alembic head is unverified. Migrations newer than `fa14587c0de1` may exist, and EPMCDME-7171 (AWS_MFA) may collide on down_revision. Run `poetry run alembic heads` before choosing a down_revision. The new_values list must be built from that head, not from `fa14587c0de1`.
- `toolkit_provider._process_module_generic` catches only `ImportError`. An `AttributeError` raised at import, such as a missing `ToolSet.MS_EXCHANGE`, is not caught there. The ticket's statement that the toolkit "silently fails" does not match this code; the error would surface elsewhere.
- Speculative: `service_account_password` is absent from `LIST_OF_SENSITIVE_FIELDS` (settings.py:134-161), so the encrypt-at-rest criterion likely depends on it being added. The list is global and key-based, so the addition affects every credential type that uses that key. `client_secret` is already covered.
- Speculative: `get_config` raises for classes that are not in `__CREDENTIAL_CONFIG_TO_TYPE`. MS_TEAMS is not in that map, so the precedent for injection is Email or ServiceNow, not MS_TEAMS.
- Speculative: `SettingsTester.handlers` has no Exchange handler. Testing an Exchange integration would raise `SettingsTesterHandlerNotFound` unless it is handled or left out.
- Speculative: making the flag "enable without redeploy" likely depends on a new `SettingDeclaration` in `DECLARATIONS`. Without one, the component is YAML-only. The path from the env var `FEATURE_EXCHANGE_POLLING` to the component is unconfirmed.
- The Email precedent's `get_tool_default` default must not be copied, because the ticket forbids it. `RequiredField("")` breaks enum coercion for `auth_type`.
- There is no migration test harness, so the forward and rollback acceptance criterion is a novel test pattern.
- ToolSet has 56 callers and CredentialTypes has 74, so any renumbering or relabelling has wide reach. `.value` "Microsoft Exchange" is persisted in assistant toolkit JSON and is effectively irreversible.
- The "what the scheduled-trigger path is" behind the flag was not located in the code during this research. Its only anchors are `scheduler_settings_service` and `features:schedulersView`.
- UI conditional fields live in the separate codemie-ui repo (`CREDENTIAL_UI_MAPPING`). EPMCDME-8933, the schema-driven UI work, is still Open.

---

## 7. Summary for Complexity Assessment

The change touches five backend layers: the `codemie_tools` library (the two enums, a new config model and a toolkit class), the settings service (sensitive-key list, config-to-type map, possibly the tester registry), the data layer (one `sync_enum_values` migration on `codemie.credentialtypes`), the customer-config feature registry, and docs. It also touches a separate UI repo. Every piece has a close precedent:

- ITSM toolkit for the toolkit shape
- EmailToolConfig for the dual auth config
- migration `8b2c1a4d5e6f` (MS_TEAMS) for the enum and delete-before-shrink downgrade
- `WEB_SEARCH`/`SCHEDULERS` `SettingDeclaration` for a runtime-overridable flag

No protocol code and no dependencies are involved, so technical novelty is low.

Test coverage is good around discovery, Email config, settings encryption and customer-config overrides. There is no harness for testing Alembic upgrade and downgrade, and `tools_info_service` is untested. The migration forward and rollback criterion is therefore the one new testing pattern.

Key risks:

- The unverified Alembic head, with the possible EPMCDME-7171 collision.
- The global, key-based sensitive-field list, which does not yet cover `service_account_password`.
- Irreversible label strings persisted in rows and assistant JSON.
- The unconfirmed mapping from `FEATURE_EXCHANGE_POLLING` to the component.
- The unlocated "scheduled-trigger configuration path" that the flag must gate.

---

## 8. External References

- `/Users/Oleksandr_Cherevach/work/codemie-dev/codemie/docs/superpowers/tasks/2026-09-25-epmcdme-15199/ticket.local.txt` resolved. It is Story EPMCDME-15199 (In Progress) under epic EPMCDME-13290, and it blocks EPMCDME-15200 and EPMCDME-13773. Key facts:
  - Field construction: "RequiredField for mailbox; Field(default=None, ...) for every path-conditional field with requirement enforced in @model_validator(mode="after"). Never bare Field(...)... auth_type uses Optional[] = Field(default=None), not RequiredField (whose default="" fails enum coercion)."
  - "Do *not* reintroduce a get_tool_default(...) or "ntlm" fallback."
  - Secrets are only `client_secret` and `service_account_password`. `mailbox`, `tenant_id`, `client_id`, `ews_url` and `service_account_username` are identifiers; mailbox appears in Graph paths, so it may appear in logs.
  - Resolved 2026-09-25:
    - Members are named MS_EXCHANGE with the label "Microsoft Exchange".
    - The flag is `features:exchangePolling` (env FEATURE_EXCHANGE_POLLING), default off and runtime-overridable.
    - There is a single ExchangeToolConfig, with the UI in codemie-ui `CREDENTIAL_UI_MAPPING` using shouldShow predicates and no auth_type UI default.
    - The slice releases together with Slice 2.
  - EPMCDME-13773 coordination: "We must not silently create a competing Graph credential type or duplicate configuration UX."
  - References:
    - EPMCDME-7171: colliding down_revision; run `poetry run alembic heads`.
    - EPMCDME-8933: Open, versus the claim that EPMCDME-10928 is live; verify before relying on the auto UI schema.
    - EPMCDME-9892: Email precedent.
    - EPMCDME-9514: gates Graph production rollout only.
