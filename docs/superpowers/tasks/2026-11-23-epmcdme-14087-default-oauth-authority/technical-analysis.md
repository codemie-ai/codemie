# Technical Research

**Task**: email oauth authority msal
**Generated**: 2026-11-23
**Research path**: codegraph

---

## 1. Original Context

EPMCDME-14087 (Bug, backend only) — "Outlook integration test fails due to malformed Microsoft login URL validation error". Slash command args were: "EPMCDME-14087. Do it in backend."

TICKET (verbatim essentials): After recent changes the Outlook integration test fails with: "Your given address (`//https://login.microsoftonline.com/b41b72d0-4e9f-42c6-8a69-f949f367c91d`) should consist of an https url with a minimum of one segment in a path, e.g. https://login.microsoftonline.com/{tenant} or https://{tenant_name}.ciamlogin.com/{tenant} or https://{tenant_name}.b2clogin.com/{tenant_name}.onmicrosoft.com". Acceptance criteria: (1) the Outlook integration test passes when a valid Microsoft tenant login URL is configured; (2) URL validation does not reject valid Microsoft login URLs because of an incorrectly added or preserved `//` before `https://`; (3) the test shows a clear validation error only when the user-provided URL is actually invalid; (4) existing Microsoft 365 integration flows are not regressed; (5) automated or manual regression coverage is added for the Microsoft login URL formats used by the Outlook integration. A later comment (12 Oct, automation account) says the OAuth email integration with an Outlook service account worked before (config: URL, Entra ID Tenant, Client ID, Secret) and now fails with an address that is only the tenant path (e.g. /<tenant-guid>).

FINDINGS FROM MANUAL REPRODUCTION (local stack, main ec82985817, MSAL 1.37.0; UI Integrations -> Create -> credential type "Email" -> auth type "OAuth via Microsoft Entra ID Application" -> Test, which POSTs /v1/settings/test/): the "Outlook integration" is the Email credential type with auth_type=oauth_azure. The literal `//https://...` prefix could NOT be reproduced from any payload the product or the test harness sends. Reproducible instead: Authority blank -> "(/<tenant>)"; Authority blank and Tenant not sent -> "(/None)"; Authority "https://login.microsoftonline.com/" (trailing slash) -> "(https://login.microsoftonline.com//<tenant>)"; Authority "https://login.microsoftonline.com" -> passes MSAL's address check. Suspected cause: commit 71bff46785 (EPMCDME-10928, 2026-07-27) changed the defaults of oauth_authority and oauth_scope in src/codemie_tools/notification/email/models.py from "https://login.microsoftonline.com" / "https://outlook.office365.com/.default" to get_tool_default(TOOL_NAME, ...) or "" (customer-configurable via customer_config tool_defaults), while EmailTool._get_oauth_token_azure in src/codemie_tools/notification/email/tools.py builds f"{oauth_authority}/{oauth_tenant_id}" and f scope [oauth_scope] with no fallback. The test harness commit of EPMCDME-13807 only adds oauth_authority="https://login.microsoftonline.com" and oauth_scope="https://outlook.office365.com/.default" to its credentials (test code, not production).

DECISION ALREADY MADE: Product Owner (Evgeniy Galinovskiy) approved on the ticket: "Go ahead with option a) Default the authority to https://login.microsoftonline.com" — default shown in the Authority field, overridable, no trailing slash. The user chose backend implementation only. Not yet decided (the clarity check will ask): also default oauth_scope; require tenant when auth_type=oauth_azure (today a missing tenant yields "/None"); strip whitespace/trailing slashes; treat a stored empty string as not set; whether to write regression tests.

PLEASE CHECK THESE STATEMENTS AGAINST CURRENT CODE (branch base is origin/main f97c9a6dc5, which is newer than ec82985817 — verify nothing changed in the email tool): items 1-7 (authority/scope construction; tool_defaults population and reach to saved integrations; stored value to EmailToolConfig path and blank/None handling; other consumers; required-field validation; existing tests; frontend default handling).

---

## 2. Codebase Findings

### Existing Implementations

Purpose A: obtain an MSAL token for the Email integration (auth_type=oauth_azure) from tenant + authority + scope.
- `src/codemie_tools/notification/email/tools.py:56-76` `EmailTool._get_oauth_token_azure` — `msal_authority = f"{self.config.oauth_authority}/{self.config.oauth_tenant_id}"` (:59), `msal_scope = [self.config.oauth_scope]` (:60); passes to `ConfidentialClientApplication` (:62). No fallback, no strip, no tenant check. Error path: result without `access_token` raises `ToolException("Failed to acquire OAuth access token: ...")`. Only caller: `_authenticate_smtp_server` (:113-118), used by `execute` (:171) and `_healthcheck` (:213); `_healthcheck` first calls `_determine_from_email(None)` (:210) which returns `oauth_from_email`.
- The MSAL address error is raised at `ConfidentialClientApplication(...)` construction; in `_healthcheck` it propagates to `CodeMieTool.healthcheck` (`src/codemie_tools/base/codemie_tool.py:222-227`) which returns `(False, humanize_error(e))`. In `execute` it is caught by the broad `except Exception` and returned as "Failed to send email: ...".

Purpose B: hold the config values and their defaults.
- `src/codemie_tools/notification/email/models.py:85-94` `EmailToolConfig.oauth_authority` / `oauth_scope` — `Optional[str]`, `default=get_tool_default(TOOL_NAME, "...") or ""`, evaluated once at class-definition (import) time. Description text still claims "optional, defaults to https://login.microsoftonline.com" / "https://outlook.office365.com/.default"; placeholders (json_schema_extra) hold the same literals. Test: no default assertions remain (see Section 4).
- `models.py:80-84` `oauth_tenant_id`, `:65-79` `oauth_from_email` / `oauth_client_id` / `oauth_client_secret` — all `Optional[str]`, default `None`, no validator. No `model_validator` exists in this model (a validator for OAUTH_AZURE fields was reviewed during EPMCDME-10928 in `docs/superpowers/tasks/2026-07-17-.../code-review-final.json` but is not in the current code).
- `models.py:41-45` `url` is the only `RequiredField` (`required_at_runtime`); `models.py:47-50` `auth_type` default from `tool_defaults.email.auth_type` or "basic".
- `src/codemie_tools/base/models.py:20-22` `get_tool_default = customer_config.get_tool_default`; `src/codemie/configs/customer_config.py:566-567` `get_tool_default(tool, field)` = `(self.tool_defaults.get(tool) or {}).get(field)`.

Purpose C: where `tool_defaults` comes from.
- `config/customer/customer-config.yaml:444-498` `tool_defaults.email`: only `smtp_url_placeholder` is active; `smtp_url`, `auth_type`, `oauth_authority`, `oauth_scope` are commented out. The commented example is `# oauth_authority: "https://login.microsoftonline.com/<tenant-id>"` (:497) — it includes a tenant segment, while the code appends the tenant itself (would yield `.../<tenant>/<tenant>`). Loaded at `customer_config.py:132` (`config_data.get("tool_defaults", {})`), field typed `Dict[str, Dict[str, Union[str, bool]]]` (:87). README.md:81-93 documents the section (restart required). Source is YAML only; no env var path (`tool_defaults` grep in src yields only customer_config.py).
- Net effect today with the shipped YAML: both defaults resolve to `""`.

Purpose D: stored values -> `EmailToolConfig`.
- Test button: `src/codemie/service/settings/settings_tester.py:64-79` `SettingsTester.__init__` merges request values over the stored setting's values (`_overwrite_credential_values` :210, masked values filtered :205), decrypts, `_normalize_credential_values` (:201) -> plain dict; `_test_email` (:164-165) `EmailTool(config=EmailToolConfig(**self.credential_values)).healthcheck()`. Keys absent from the dict take the Pydantic field default; keys present with `""` or `None` override it (`CredentialValues.value: Any = None`, `src/codemie/rest_api/models/settings.py:41-43`).
- Runtime: `src/codemie/service/settings/settings.py:962-1015` `SettingsService.get_config` -> `config_class(**cls._inject_oauth_config_values(setting))` (:1004; for non-OAuth-provider settings this is just `setting.normalize_values()`, :855-859), or `config_class(**tool_config.tool_creds)` (:983). Mapping `EmailToolConfig: CredentialTypes.EMAIL` at `settings.py:240`. Saved integrations therefore reach `EmailToolConfig` as `**dict(stored keys)`: a field-level Pydantic default applies to saved integrations that never stored the key, not to ones that stored `""`/`None`.
- `src/codemie/rest_api/models/settings.py:160-174` `EmailCredentials` (`oauth_authority`, `oauth_scope` `Optional[str] = None`); the only repository references to `EmailCredentials` are its own definition (no other consumer found).

Purpose E: show defaults/placeholders to users (frontend sibling `../codemie-ui`, note only).
- `codemie-ui/src/utils/settingsUIConfig.ts:472-531` email fields: `oauth_authority` (:520) and `oauth_scope` (:525) have only static `placeholder` text ("Optional, e.g. https://login.microsoftonline.com") and NO `defaultValue`; only `url` and `auth_type` use `dynDefault`.
- `codemie-ui/src/store/appInfo.ts:41` `emailtoolconfig: { credentialType: 'email', fields: ['url', 'auth_type'] }` — only these two fields' schema defaults/placeholders are read from `GET /v1/tools/configs` (`src/codemie/rest_api/routers/tool.py:48`). So a backend schema default for `oauth_authority` would not reach the form without a UI change; the field stays blank and the request omits it (per task findings).

Other touched code: `src/codemie_tools/notification/toolkit.py:28` `NotificationToolkitUI` registers `EmailTool`; `src/codemie_tools/notification/email/tools_vars.py:39` `config_class=EmailToolConfig`; `src/codemie/rest_api/models/prebuilt_assistants.py:348` uses `EMAIL_TOOL`.

### Decisions Already in the Code
- Defaults for tool config fields come from `customer_config.tool_defaults` at import time, via `get_tool_default(...) or ""` — `models.py:42,48,86,91`; convention from EPMCDME-10928 (`docs/superpowers/tasks/2026-07-17-epmcdme-10928-.../spec.md`). Alternative recorded in that plan: literal fallback ("https://login.microsoftonline.com", test `test_email_oauth_authority_default_value`). Reason for removing the literal: none found for the Email fields.
- Lazy validation convention: configs are constructible empty ("no validation at model level"; asserted in `tests/codemie_tools/notification/email/test_email_tools.py:213-241`), required fields flagged with `RequiredField` and checked only in `CodeMieTool._validate_config` (`codemie_tool.py:232-274`, called at :155 and :214, i.e. on tool run, NOT in `healthcheck`). Alternative: `model_validator` as in `src/codemie_tools/ms_exchange/models.py:36-49` (`_validate_selected_path`, per-auth-type required tuple; reviewed finding there: whitespace-only values pass `if not getattr(...)`).
- Authority convention: base URL without tenant; tool appends `/{tenant}` (`tools.py:59`, field description `models.py:87`). Contradicted by the YAML example (:497).

### Task Statements Checked Against the Code
1. "`_get_oauth_token_azure` builds authority only from f'{oauth_authority}/{oauth_tenant_id}' and scope from oauth_scope with no fallback" — holds (`tools.py:59-60`); unchanged on f97c9a6dc5.
2. "Defaults come from `get_tool_default(TOOL_NAME, ...) or ''`; `customer_config.get_tool_default` reads `tool_defaults`" — holds (`models.py:86,91`; `customer_config.py:566-567`). Populated only from `config/customer/customer-config.yaml` `tool_defaults` (loaded `customer_config.py:132`); email oauth keys commented out. A model-level default reaches saved integrations only when the stored credential has no `oauth_authority`/`oauth_scope` key (kwargs path above); a stored `""`/`None` bypasses it (Pydantic uses the passed value; no validator or coercion exists).
3. "UI omits blank fields; explicit '' or None can be passed" — partly unverified for the UI payload (frontend request not traced here); backend confirms `**dict` path with `CredentialValues.value: Any = None` allows `""`/`None` explicitly. Frontend confirms no `defaultValue` for these two fields (`settingsUIConfig.ts:520-529`).
4. "Who else constructs/consumes EmailToolConfig" — only `settings_tester.py:165`, `settings.py:240/1004/983`, `tools.py`, `tools_vars.py`, tests. No IaC export/import of the email credential found (search of `src/codemie/service/iac_export` for email returned nothing). MS Exchange (`src/codemie_tools/ms_exchange/models.py`, `graph_client.py:155`) has its own config with `oauth_tenant_id` but no authority/scope fields and uses azure-identity; SharePoint builds `https://login.microsoftonline.com/{tenant}/...` literals (`sharepoint/tools.py:134`, `datasource/loader/sharepoint_loader.py:326`, `routers/sharepoint_oauth.py:33`, `service/sharepoint_pkce_service.py:39`) independent of the email fields. `msal` / `ConfidentialClientApplication` is used only in `email/tools.py`.
5. "Required oauth_azure field validation" — none for `oauth_tenant_id`, `oauth_client_id`, `oauth_client_secret`, `oauth_from_email`: all `Optional[str] = None`, not `required_at_runtime`; only `url` is. A missing tenant yields "/None" in the authority; a missing from-email makes `_determine_from_email` return `None`; a missing client id/secret surfaces as an MSAL/`ToolException` error.
6. Branch base f97c9a6dc5 vs ec82985817 — email tool code is as quoted above (read from disk on this branch).
7. "Commit 71bff46785 changed the defaults" — consistent with the in-repo history: `docs/superpowers/tasks/2026-07-17-epmcdme-10928-.../technical-analysis.md:38-39` and `plans/2026-07-17-configurable-integration-url-defaults.md:868-876` list the prior literal defaults; commit hash itself not checked (no git access via tools).

### Consumers of Changed Contracts
Contract: `EmailToolConfig.oauth_authority` / `oauth_scope` default values (and, if changed, their validation).
- `tools.py:59-60` (reads both).
- `settings_tester.py:165` and `settings.py:983,1004` (construct from dict).
- `GET /v1/tools/configs` schema (`routers/tool.py:48`) exposes field `default`/`placeholder`; UI reads only `url`/`auth_type` for email (`appInfo.ts:41`).
- Tests that build configs: `tests/codemie_tools/notification/email/test_email_tools.py:229-238` (passes both explicitly), `tests/codemie/service/settings/test_settings_tester.py:181-216` (Basic auth only, healthcheck mocked).
- OpenAPI/schema baselines: `tests/codemie/rest_api/router_guards/baselines/openapi.json` has no email authority/scope entries (grep of `oauth_authority` in tests found none); `tests/codemie/service/tools/test_tool_discovery_service.py` lists no email fields.
- `config/customer/customer-config.yaml:493-498` and README.md:81-93 document the operator override (authority `.cn` example in `tests/codemie_tools/notification/email/test_models.py:23-31`).
None found for IaC export/import of Email credentials.

### Architecture and Layers Affected
- Tool config layer `codemie_tools/notification/email` (models, tools) — imports `codemie.configs.customer_config` through `codemie_tools/base/models.py`.
- Service layer `codemie/service/settings` (SettingsTester, SettingsService.get_config).
- Config layer `config/customer/customer-config.yaml` / `codemie/configs/customer_config.py`.
- REST model `codemie/rest_api/models/settings.py` (credential value carrier; `EmailCredentials` unused elsewhere).

### Integration Points
- MSAL (`msal` `ConfidentialClientApplication`) -> Microsoft Entra ID token endpoint; SMTP XOAUTH2 against e.g. smtp.office365.com:587.
- `POST /v1/settings/test/` -> `SettingsTester` -> `EmailTool.healthcheck`.

### Patterns and Conventions
- `TOOL_NAME` ClassVar + `get_tool_default(...) or ""` for overridable defaults; `RequiredField` for runtime-required; healthcheck returns `(bool, message)` with `humanize_error`.
- Guides: `.ai-run/guides/` has no email-specific guide; `integration/external-services.md`, `development/configuration-patterns.md`, `agents/agent-tools.md` are general.

---

## 3. Documentation Findings

### Guides and Architecture Docs
No email/Microsoft-auth guide found — conventions derived from code exploration. `README.md:79-93` documents `tool_defaults`.

### Architectural Decisions
`docs/superpowers/tasks/2026-07-17-epmcdme-10928-configurable-integration-url-defaults/{spec,plan,technical-analysis}.md` define "type B" fields (real runtime default) vs "type A" placeholder-only fields; the original plan classed `oauth_authority`/`oauth_scope` as type B with literal defaults. Its `code-review-final.json` records a whitespace-only validation finding for these two fields.

### Derived Conventions
Authority is a base URL with no tenant; the tool appends the tenant. Defaults are config-driven; frontend reads defaults only for fields listed in `TOOL_CONFIG_FIELD_MAP`.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie_tools/notification/email/test_models.py` — placeholder default for `url`; `tool_defaults` override for authority (.cn), scope (.cn partner), auth_type: these assert only `customer_config.get_tool_default(...)` after monkeypatching, NOT the model field (class-level defaults are fixed at import, so monkeypatching has no effect on `EmailToolConfig`); `EmailToolConfig().auth_type == BASIC`. There is no assertion of the oauth_authority/oauth_scope field defaults.
- `tests/codemie_tools/notification/email/test_email_tools.py` — Basic-auth healthcheck/execute cases with `smtplib.SMTP` patched; config construction test (:213-241) is the only OAuth-azure config test. No test calls `_get_oauth_token_azure` or patches `ConfidentialClientApplication`.
- `tests/codemie/service/settings/test_settings_tester.py:181-216` — `_test_email` with Basic credentials and mocked `EmailTool.healthcheck`; no oauth_azure case. (Exchange tester cases at :628 are a different credential type.)

### Testing Framework and Patterns
pytest (project policy `.ai-run/guides/testing/testing-patterns.md`); email tool tests use `unittest.TestCase` + `unittest.mock.patch`; model tests are plain pytest functions with `monkeypatch`; settings tester tests use `@patch.object` and `TestSettingRequest`/`CredentialValues`.

### Coverage Gaps
- `EmailTool._get_oauth_token_azure` (authority composition, scope list) — untested.
- oauth_azure path through `SettingsTester._test_email` — untested.
- `EmailToolConfig` default/blank/None handling of `oauth_authority`/`oauth_scope` — untested.
- Where a blank/default-authority regression test would fit by existing placement: `tests/codemie_tools/notification/email/test_email_tools.py` (tool, patch `codemie_tools.notification.email.tools.ConfidentialClientApplication`), `.../test_models.py` (config defaults), `tests/codemie/service/settings/test_settings_tester.py` (oauth_azure payload through the tester).

---

## 5. Configuration and Environment

### Environment Variables
None found for these fields (`tool_defaults` is YAML-only; no env var reads `oauth_authority`/`oauth_scope`).

### Configuration Files
- `config/customer/customer-config.yaml:493-498` `tool_defaults.email` (see Section 2, Purpose C); loaded at startup by `CustomerConfig._load_config` (`customer_config.py:103-132`); restart required for changes.

### Feature Flags and Deployment Concerns
None for email auth. Deployments that set `tool_defaults.email.oauth_authority` today get that value as default for rows lacking the key; custom `.cn`/partner authorities exist as documented override cases (`test_models.py:23-42`).

---

## 6. Risk Indicators

- Existing mechanism kept in place (Section 2, Purpose B/C): `tool_defaults` -> `get_tool_default(...) or ""` remains the default source for `oauth_authority`/`oauth_scope`; consumers: `models.py:85-94`, YAML :497-498, README.md:81-93, `test_models.py:23-42`.
- Speculative: a Pydantic field default alone does not cover stored `""`/`None` values (kwargs path `settings.py:1004`, `settings_tester.py:165`) — see Purpose D.
- Speculative: a literal fallback at both model and tool level changes behavior for deployments overriding via `tool_defaults` (.cn / partner authorities); precedence between the YAML override and a built-in default is not defined in code today.
- YAML example (`customer-config.yaml:497`) includes `<tenant-id>`, inconsistent with `tools.py:59`; an operator following it would produce a doubled tenant.
- Field descriptions/placeholders (`models.py:87,92`) still claim built-in defaults that no code applies.
- `oauth_tenant_id` has no validation: missing tenant yields "/None" (`tools.py:59`); `oauth_from_email` missing yields `None` sender; no whitespace/trailing-slash normalization (a trailing slash produces `//<tenant>`, matching the reproduced error shape).
- No test covers `_get_oauth_token_azure` or the oauth_azure tester path; the `//https://` literal in the ticket was not reproduced, so the acceptance wording and the reproduced symptoms (blank, trailing slash) are not the same string.
- UI cannot show a backend default for these fields (`appInfo.ts:41`, `settingsUIConfig.ts:520-529`); backend-only scope leaves the field blank in the form.
- `_validate_config` is not part of `healthcheck` (`codemie_tool.py:222-227`), so `RequiredField` markings would not affect the Test button.

---

## 7. Summary for Complexity Assessment

The change sits in the Email tool config layer (`codemie_tools/notification/email/models.py`, `tools.py`) with the Test button path (`settings_tester.py:165`) and runtime config path (`settings.py:962-1015`) as the only other consumers. The authority is composed in one place (`tools.py:59`) from a field whose default is now YAML-driven and empty by default; no other code composes an authority from these fields, and MS Exchange/SharePoint do not share them. File surface visible from research is small: 2 source files plus tests; the YAML example and README are adjacent documentation.

Technical novelty is low: the conventions (`get_tool_default`, `RequiredField`, `model_validator` in ms_exchange) already exist. The open facts are how saved integrations carry blank values (explicit `""`/`None` bypass a Pydantic default), and that `tool_defaults` overrides exist as an operator-facing mechanism.

Test posture: the oauth_azure token path and the tester's oauth_azure path have no coverage; the existing email test files are the natural homes. Risk factors: interplay between YAML overrides and a built-in default, absence of tenant validation, UI not reading defaults for these fields, and the unreproduced `//https://` literal.

---

## 8. External References

None named by the task.
